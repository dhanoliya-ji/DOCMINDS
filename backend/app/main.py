"""
main.py
=======
WHAT THIS FILE IS
-----------------
The front door of the entire backend. This is the file the web server runs:

    uvicorn app.main:app --reload
                ^^^^^^^^ ^^^
                module   the `app` object created below

Everything a browser or the frontend sends arrives here first, and this file
decides which piece of code handles it.

WHAT IT ACTUALLY DOES, IN ORDER
-------------------------------
  1. Switches every log line to structured JSON, and Sentry on if configured
     (core/observability.py).
  2. Creates the FastAPI application object.
  3. Adds middleware: request ids + access log, security headers, the upload
     size ceiling, and CORS so the Next.js frontend is allowed to call us.
  4. Registers every group of endpoints ("routers") under a URL prefix.
  5. Defines the status endpoints of its own: /health (liveness) and
     /health/ready (readiness, core/health.py).

WHAT IS A ROUTER?
-----------------
Rather than writing all fifty endpoints in one enormous file, related endpoints
are grouped into their own module -- everything about documents in
`api/v1/documents.py`, everything about chat in `api/v1/chat.py`, and so on.
Each module exposes a `router`, and this file mounts them all onto the app with
a URL prefix. That is why `chat.py` can define its path as just `/sessions`
while the real, public URL ends up being `/api/v1/chat/sessions`.

THE REQUEST LIFECYCLE
---------------------
    Browser
      -> CORS middleware        (is this website allowed to call us?)
      -> request context        (request id, access log -- core/middleware.py)
      -> security headers       (nosniff, frame DENY, HSTS...)
      -> upload size ceiling    (413 before a huge body is read)
      -> router matching        (which function handles this URL?)
      -> dependencies           (open a DB session, decode the JWT, load user)
      -> your endpoint function (the actual work)
      -> Pydantic response model(filter and serialise the output)
      -> Browser
"""

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
from app.api.v1.documents import router as documents_router
from app.api.v1.integrations import router as integrations_router
from app.api.v1.org import router as org_router
from app.api.v1.projects import router as projects_router
from app.api.v1.search import router as search_router
from app.api.v1.sso import router as sso_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.users import router as users_router
from app.api.v1.webhooks import router as webhooks_router
from app.core import health, observability
from app.core.config import settings
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.core.upload_limit import UploadSizeLimitMiddleware
from app.services.usage import QuotaExceeded

# ----------------------------------------------------------------------
# LOGGING AND ERROR REPORTING -- before anything else logs a line
# ----------------------------------------------------------------------
# Every log record in the process becomes one JSON object carrying the
# request id, user and organization (core/observability.py). Sentry is
# switched on only when SENTRY_DSN is set; otherwise this is a no-op.
observability.configure_logging()
observability.init_sentry("api")

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# STARTUP: recover documents a previous run left behind
# ----------------------------------------------------------------------
async def _recover_on_startup() -> None:
    """
    Re-queue documents orphaned by the last shutdown (see
    `recover_stuck_documents` in tasks/ingestion.py).

    This matters most in INGESTION_MODE=inline, where the jobs run inside this
    very process: a redeploy in the middle of a job would otherwise leave that
    document on "processing" for ever. It never raises -- a database that is
    not reachable yet must not stop the API from starting.
    """
    from app.tasks.ingestion import recover_stuck_documents

    try:
        await recover_stuck_documents()
    except Exception as exc:
        logger.warning("Document recovery skipped at startup", extra={"error": repr(exc)})


# Strong reference to the startup job, so it is not garbage-collected while
# it runs (asyncio itself only keeps a weak one).
_startup_jobs: set = set()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Code that runs once when the server starts (before `yield`) and once
    when it stops (after it).

    The recovery is started in the BACKGROUND rather than awaited: the API
    begins answering requests -- and health checks -- immediately, instead of
    waiting on a database query that may take a while to time out.
    """
    if settings.INGESTION_RECOVER_ON_STARTUP:
        job = asyncio.create_task(_recover_on_startup())
        _startup_jobs.add(job)
        job.add_done_callback(_startup_jobs.discard)
    # Cloud-import auto-sync, in-process, for INGESTION_MODE=inline only
    # (Celery beat sends it otherwise). A no-op unless CONNECTOR_SYNC_ENABLED.
    from app.services.connectors import scheduler as connector_scheduler

    connector_scheduler.start()
    yield
    await connector_scheduler.stop()


# ----------------------------------------------------------------------
# CREATE THE APPLICATION
# ----------------------------------------------------------------------
app = FastAPI(
    lifespan=lifespan,
    title=settings.PROJECT_NAME,
    description=(
        "A multi-tenant RAG platform. Upload documents in 19 formats, and ask "
        "questions answered from their contents with page-level citations."
    ),
    version="1.0.0",
    # FastAPI generates an OpenAPI specification -- a machine-readable
    # description of every endpoint, derived automatically from the type hints
    # and Pydantic schemas. It powers the two documentation pages below and can
    # generate a typed client for the frontend.
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    # Two free, interactive documentation UIs:
    #   /docs  -> Swagger UI. You can call any endpoint straight from the page.
    #   /redoc -> ReDoc. Cleaner for reading, no "try it" buttons.
    docs_url="/docs",
    redoc_url="/redoc",
)

# ----------------------------------------------------------------------
# CORS -- Cross-Origin Resource Sharing
# ----------------------------------------------------------------------
# THE PROBLEM THIS SOLVES:
# Browsers enforce the "same-origin policy". A page loaded from
# http://localhost:3000 is NOT allowed to make requests to
# http://localhost:8000, because a different port counts as a different origin.
# Without the middleware below, every frontend request would fail with an
# opaque CORS error in the console -- and this is the single most common thing
# to trip up people wiring a React frontend to a Python API for the first time.
#
# `allow_origins` is the explicit list of sites we trust. In development that is
# localhost:3000; in production the deployed frontend URL is appended via the
# FRONTEND_ORIGIN environment variable. See `cors_origins` in core/config.py.
#
# Note this is a browser-enforced rule, not a server firewall: tools such as
# curl and Postman ignore CORS entirely. Real access control is done by the JWT
# authentication on each endpoint, not here.
# ----------------------------------------------------------------------
# UPLOAD SIZE CEILING
# ----------------------------------------------------------------------
# Refuses an over-sized upload with 413 before its body is parsed and spooled
# to disk (see core/upload_limit.py). Added BEFORE the CORS middleware on
# purpose: Starlette wraps each new middleware around the previous ones, so
# CORS ends up outermost and adds its headers to this 413 too -- without them
# the browser would hide the real error behind a CORS failure.
app.add_middleware(UploadSizeLimitMiddleware)

# ----------------------------------------------------------------------
# SECURITY HEADERS AND REQUEST CONTEXT  (core/middleware.py)
# ----------------------------------------------------------------------
# Same ordering rule as above: each add_middleware wraps the ones before it.
# The security headers wrap the upload ceiling, so its 413 carries them too.
# The request context wraps both, so its request id and access-log line
# cover EVERYTHING inside -- including a 413 or a crash -- and CORS (added
# last) is still the outermost layer.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Permit cookies and Authorization headers to be sent along with requests.
    allow_credentials=True,
    # Allow every HTTP verb (GET, POST, DELETE, ...).
    allow_methods=["*"],
    # Allow every request header, which we need for `Authorization: Bearer ...`.
    allow_headers=["*"],
    # Response headers the BROWSER may read from a cross-origin reply. By
    # default JavaScript sees only a handful of "safe" ones; Retry-After is not
    # among them, so without this the frontend could not tell a rate-limited
    # user how long to wait, and X-Request-ID could not be quoted in a report.
    expose_headers=["Retry-After", "X-Request-ID"],
)

# ----------------------------------------------------------------------
# QUOTAS -> HTTP
# ----------------------------------------------------------------------
# services/usage.py raises QuotaExceeded from deep inside an endpoint (chat,
# upload) without knowing about HTTP. One handler turns it into the response:
# 429 + Retry-After for the monthly token quota, 402 for documents/storage.
@app.exception_handler(QuotaExceeded)
async def quota_exceeded_handler(_request: Request, exc: QuotaExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.message}, headers=exc.headers
    )


# ----------------------------------------------------------------------
# REGISTER THE ROUTERS
# ----------------------------------------------------------------------
# `prefix` prepends a path to every route in that router.
# `tags` groups the endpoints into labelled sections on the /docs page.

# --- Identity and tenancy ---
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
# Members, invites, API keys and the audit log. Admin-only, every endpoint.
app.include_router(
    org_router, prefix=f"{settings.API_V1_STR}/org", tags=["Organization admin"]
)

# Outgoing webhooks (admin-only; every route 404s unless WEBHOOKS_ENABLED).
app.include_router(
    webhooks_router, prefix=f"{settings.API_V1_STR}/webhooks", tags=["Webhooks"]
)

# Cloud imports, billing and the admin dashboard. Each 404s unless switched
# on (CONNECTORS_ENABLED, BILLING_ENABLED, ADMIN_DASHBOARD_ENABLED).
app.include_router(
    integrations_router, prefix=f"{settings.API_V1_STR}/integrations", tags=["Cloud imports"]
)
app.include_router(
    billing_router, prefix=f"{settings.API_V1_STR}/billing", tags=["Billing"]
)
app.include_router(
    analytics_router, prefix=f"{settings.API_V1_STR}/analytics", tags=["Admin dashboard"]
)

# --- Document ingestion ---
app.include_router(
    documents_router, prefix=f"{settings.API_V1_STR}/documents", tags=["Documents"]
)
app.include_router(
    tasks_router, prefix=f"{settings.API_V1_STR}/tasks", tags=["Tasks"]
)

# --- The RAG features: semantic search and grounded chat ---
app.include_router(
    search_router, prefix=f"{settings.API_V1_STR}/search", tags=["Semantic Search"]
)
app.include_router(
    chat_router, prefix=f"{settings.API_V1_STR}/chat", tags=["RAG Chat"]
)


# ----------------------------------------------------------------------
# STATUS ENDPOINTS
# ----------------------------------------------------------------------


@app.get("/", tags=["Health"])
async def root():
    """
    A friendly landing response at the base URL.

    Useful after deployment: opening the service URL confirms the API is alive
    and points you straight at the interactive documentation.
    """
    return {
        "name": settings.PROJECT_NAME,
        "version": "1.0.0",
        "docs": "/docs",
        "health": f"{settings.API_V1_STR}/health",
    }


@app.get(f"{settings.API_V1_STR}/health", tags=["Health"])
async def health_check():
    """
    Confirm the API is running, and report how the AI layer is configured.

    Hosting platforms poll an endpoint like this to decide whether the service
    is healthy. Reporting the AI configuration here is a deliberate debugging
    aid: after deploying, one request tells you whether the server actually
    picked up your GROQ_API_KEY, which is by far the most common thing to get
    wrong in a cloud environment.
    """
    return {
        "status": "healthy",
        "project_name": settings.PROJECT_NAME,
        "environment": settings.ENV,
        "debug_mode": settings.DEBUG,
        # Report only WHETHER a key is present -- never the key itself.
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
    """
    READINESS: can this instance serve real traffic right now?

    Checks the database, Redis and (with INGESTION_MODE=celery) that a Celery
    worker answers a ping -- concurrently, each with its own timeout -- and
    reports each component's status. Answers 503 when a HARD dependency is
    down, so a load balancer or deploy can hold traffic back.

    /health above deliberately checks nothing: see core/health.py for why
    liveness and readiness must be separate.
    """
    ready, body = await health.readiness()
    return JSONResponse(status_code=200 if ready else 503, content=body)
