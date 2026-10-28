"""The front door of the entire backend."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.billing import router as billing_router
from app.api.v1.chat import router as chat_router
from app.api.v1.compare import router as compare_router
from app.api.v1.documents import router as documents_router
from app.api.v1.integrations import router as integrations_router
from app.api.v1.org import router as org_router
from app.api.v1.projects import router as projects_router
from app.api.v1.prompts import router as prompts_router
from app.api.v1.search import router as search_router
from app.api.v1.shares import public_router as public_shares_router
from app.api.v1.shares import router as shares_router
from app.api.v1.sso import router as sso_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.users import router as users_router
from app.api.v1.webhooks import router as webhooks_router
from app.core import health, observability
from app.core.config import settings
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.core.upload_limit import UploadSizeLimitMiddleware
from app.services.usage import QuotaExceeded

observability.configure_logging()
observability.init_sentry("api")

logger = logging.getLogger(__name__)


async def _recover_on_startup() -> None:
    """Re-queue documents orphaned by the last shutdown (see"""
    from app.tasks.ingestion import recover_stuck_documents

    try:
        await recover_stuck_documents()
    except Exception as exc:
        logger.warning("Document recovery skipped at startup", extra={"error": repr(exc)})
    if settings.CONNECTORS_ENABLED:
        from app.services.connectors.service import resume_imports

        try:
            await resume_imports()
        except Exception as exc:
            logger.warning("Import recovery skipped at startup", extra={"error": repr(exc)})


_startup_jobs: set = set()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Code that runs once when the server starts (before `yield`) and once"""
    if settings.INGESTION_RECOVER_ON_STARTUP:
        job = asyncio.create_task(_recover_on_startup())
        _startup_jobs.add(job)
        job.add_done_callback(_startup_jobs.discard)
    from app.services.connectors import scheduler as connector_scheduler

    connector_scheduler.start()
    sweeper = None
    if settings.INGESTION_MODE == "inline":
        from app.tasks.ingestion import inline_sweeper

        sweeper = asyncio.create_task(inline_sweeper())
    yield
    await connector_scheduler.stop()
    if sweeper is not None:
        sweeper.cancel()
        await asyncio.gather(sweeper, return_exceptions=True)
    from app.tasks.connectors import inline_import_queue
    from app.tasks.ingestion import inline_queue

    await inline_import_queue.stop()
    await inline_queue.stop()


app = FastAPI(
    lifespan=lifespan,
    title=settings.PROJECT_NAME,
    description=(
        "A multi-tenant RAG platform. Upload documents in 19 formats, and ask "
        "questions answered from their contents with page-level citations."
    ),
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(UploadSizeLimitMiddleware)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "X-Request-ID"],
)

@app.exception_handler(QuotaExceeded)
async def quota_exceeded_handler(_request: Request, exc: QuotaExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.message}, headers=exc.headers
    )


app.include_router(
    auth_router, prefix=f"{settings.API_V1_STR}/auth", tags=["Authentication"]
)
app.include_router(
    sso_router, prefix=f"{settings.API_V1_STR}/auth/sso", tags=["Single sign-on"]
)
app.include_router(
    users_router, prefix=f"{settings.API_V1_STR}/users", tags=["Users"]
)
app.include_router(
    projects_router, prefix=f"{settings.API_V1_STR}/projects", tags=["Projects"]
)
app.include_router(
    org_router, prefix=f"{settings.API_V1_STR}/org", tags=["Organization admin"]
)

app.include_router(
    webhooks_router, prefix=f"{settings.API_V1_STR}/webhooks", tags=["Webhooks"]
)

app.include_router(
    integrations_router, prefix=f"{settings.API_V1_STR}/integrations", tags=["Cloud imports"]
)
app.include_router(
    billing_router, prefix=f"{settings.API_V1_STR}/billing", tags=["Billing"]
)
app.include_router(
    analytics_router, prefix=f"{settings.API_V1_STR}/analytics", tags=["Admin dashboard"]
)

app.include_router(
    documents_router, prefix=f"{settings.API_V1_STR}/documents", tags=["Documents"]
)
app.include_router(
    tasks_router, prefix=f"{settings.API_V1_STR}/tasks", tags=["Tasks"]
)

app.include_router(
    search_router, prefix=f"{settings.API_V1_STR}/search", tags=["Semantic Search"]
)
app.include_router(
    chat_router, prefix=f"{settings.API_V1_STR}/chat", tags=["RAG Chat"]
)

app.include_router(
    compare_router, prefix=f"{settings.API_V1_STR}/compare", tags=["Document comparison"]
)
app.include_router(
    prompts_router, prefix=f"{settings.API_V1_STR}/prompts", tags=["Prompt library"]
)
app.include_router(
    shares_router, prefix=f"{settings.API_V1_STR}/shares", tags=["Share links"]
)
app.include_router(
    public_shares_router, prefix=f"{settings.API_V1_STR}/public", tags=["Public"]
)


@app.get("/", tags=["Health"])
async def root():
    """A friendly landing response at the base URL."""
    return {
        "name": settings.PROJECT_NAME,
        "version": "1.0.0",
        "docs": "/docs",
        "health": f"{settings.API_V1_STR}/health",
    }


@app.get(f"{settings.API_V1_STR}/health", tags=["Health"])
async def health_check():
    """Confirm the API is running, and report how the AI layer is configured."""
    return {
        "status": "healthy",
        "project_name": settings.PROJECT_NAME,
        "environment": settings.ENV,
        "debug_mode": settings.DEBUG,
        "llm": {
            "provider": settings.LLM_PROVIDER,
            "model": settings.GROQ_MODEL,
            "api_key_configured": bool(settings.GROQ_API_KEY),
        },
        "embeddings": {
            "provider": settings.EMBEDDING_PROVIDER,
            "dimension": settings.EMBEDDING_DIMENSION,
        },
        "retrieval": {
            "top_k": settings.RETRIEVAL_TOP_K,
            "min_score": settings.RETRIEVAL_MIN_SCORE,
        },
    }


@app.get(f"{settings.API_V1_STR}/health/ready", tags=["Health"])
async def readiness_check():
    """READINESS: can this instance serve real traffic right now?"""
    ready, body = await health.readiness()
    return JSONResponse(status_code=200 if ready else 503, content=body)
