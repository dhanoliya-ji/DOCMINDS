"""Configures Celery -- the system that runs slow work OUTSIDE the web request."""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "document_intelligence_tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_track_started=True,

    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    timezone="UTC",
    enable_utc=True,

    result_expires=86400,

    worker_prefetch_multiplier=1,

    broker_transport_options={
        "visibility_timeout": max(
            3600,
            settings.INGESTION_TIME_LIMIT_SECONDS
            + settings.INGESTION_RETRY_BACKOFF_MAX_SECONDS
            + 300,
        ),
    },

    beat_schedule={
        "recover-stuck-documents": {
            "task": "app.tasks.ingestion.recover_stuck_documents_task",
            "schedule": settings.INGESTION_RECOVERY_INTERVAL_MINUTES * 60.0,
        },
        "sweep-webhooks": {
            "task": "app.tasks.webhooks.sweep_webhooks_task",
            "schedule": 5 * 60.0,
        },
        "sync-connectors": {
            "task": "app.tasks.connectors.sync_connectors_task",
            "schedule": settings.CONNECTOR_SYNC_INTERVAL_MINUTES * 60.0,
        },
    },

    imports=("app.tasks",),
)

celery_app.autodiscover_tasks(["app"])


from celery import signals  # noqa: E402

from app.core import observability  # noqa: E402

_task_context_tokens: dict = {}


@signals.setup_logging.connect
def _configure_worker_logging(**_kwargs) -> None:
    observability.configure_logging()


@signals.worker_process_init.connect
@signals.worker_init.connect
def _start_worker_sentry(**_kwargs) -> None:
    observability.init_sentry("worker")


@signals.beat_init.connect
def _start_beat_sentry(**_kwargs) -> None:
    observability.init_sentry("beat")


@signals.task_prerun.connect
def _bind_task_context(task_id=None, task=None, **_kwargs) -> None:
    _task_context_tokens[task_id] = observability.new_context(
        task_id=task_id, task=getattr(task, "name", None)
    )


@signals.task_postrun.connect
def _clear_task_context(task_id=None, **_kwargs) -> None:
    token = _task_context_tokens.pop(task_id, None)
    if token is not None:
        try:
            observability.reset_context(token)
        except ValueError:  # pragma: no cover - token from another context
            pass
