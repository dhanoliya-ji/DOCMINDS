"""
celery_app.py
=============
WHAT THIS FILE DOES
-------------------
Configures Celery -- the system that runs slow work OUTSIDE the web request.

THE PROBLEM IT SOLVES
---------------------
Processing a 200-page scanned PDF takes minutes: rasterise every page, OCR it,
chunk it, embed it. If that ran inside the upload request, the user's browser
would sit on a spinner and eventually time out.

So instead:

    1. The upload endpoint saves the file and writes a Document row with
       status "pending".
    2. It calls `process_document_task.delay(document_id)`, which does NOT run
       the function -- it drops a small message onto Redis and returns instantly.
    3. The API responds to the browser in milliseconds.
    4. A separate WORKER process, possibly on another machine entirely, picks
       that message up and does the real work.
    5. The frontend polls the document's status until it reads "completed".

THE THREE PIECES
----------------
    BROKER   Redis. The queue that holds pending jobs.
    WORKER   A separate process you start yourself:
                 celery -A app.core.celery_app.celery_app worker --loglevel=info
             (add --pool=solo on Windows -- Windows has no fork())
    BACKEND  Where results and statuses are stored. Also Redis here.

Broker and backend are different roles: the broker carries work TO the worker,
the backend carries answers BACK. They happen to use the same Redis server,
which is normal for a project this size.
"""

from celery import Celery

from app.core.config import settings

# ----------------------------------------------------------------------
# CREATE THE CELERY APPLICATION
# ----------------------------------------------------------------------
celery_app = Celery(
    # A label for this app, shown in logs and monitoring tools.
    "document_intelligence_tasks",
    # Where jobs are QUEUED.
    broker=settings.REDIS_URL,
    # Where results and statuses are STORED, so /tasks/status/{id} can report
    # whether a job succeeded.
    backend=settings.REDIS_URL,
)

# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------
celery_app.conf.update(
    # Report a task as STARTED the moment a worker picks it up. Without this,
    # a task sits in PENDING right up until it finishes, so the UI cannot tell
    # "queued behind other work" apart from "actively being processed".
    task_track_started=True,

    # ---- Serialisation ----
    # JSON rather than Python's `pickle`. This is a security decision, not a
    # preference: pickle can execute arbitrary code when it deserialises, so
    # anyone able to write to the queue could run code on your worker. JSON
    # can only ever describe data.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # ---- Time ----
    # Always work in UTC internally. Servers, developers and users sit in
    # different time zones; storing anything else guarantees confusion, and
    # breaks twice a year when clocks change.
    timezone="UTC",
    enable_utc=True,

    # ---- Housekeeping ----
    # Discard stored results after 1 day (86400 seconds). Without an expiry,
    # every task result accumulates in Redis forever and slowly consumes all
    # its memory.
    result_expires=86400,

    # ---- Durability ----
    # Fetch ONE message at a time. By default a worker grabs several in
    # advance; combined with acks_late (set on the ingestion task) a crashed
    # worker would then strand every pre-fetched document until Redis's
    # visibility timeout passed. With one at a time, only the job actually
    # running is affected.
    worker_prefetch_multiplier=1,

    # With acks_late, a message stays in Redis UNACKNOWLEDGED while its task
    # runs -- and while a retry waits out its countdown on the worker. If it
    # is still unacknowledged after the "visibility timeout", Redis decides
    # the worker died and hands the message to another one. That must never
    # happen to a job that is merely slow, so the window is kept longer than
    # the hard time limit plus the longest retry wait. One hour, Celery's own
    # default, is the floor. (Even if it did happen, the claim in
    # tasks/ingestion.py would make the second copy skip -- this just avoids
    # the wasted delivery.)
    broker_transport_options={
        "visibility_timeout": max(
            3600,
            settings.INGESTION_TIME_LIMIT_SECONDS
            + settings.INGESTION_RETRY_BACKOFF_MAX_SECONDS
            + 300,
        ),
    },

    # ---- Periodic jobs (Celery BEAT) ----
    # Beat is a tiny scheduler process that sends a task on a timer, like
    # cron. Here it sends the sweep that finds documents orphaned by a dead
    # worker and puts them back on the queue (tasks/ingestion.py). Start it
    # with `celery ... beat`, or `worker -B` when there is a single worker.
    beat_schedule={
        "recover-stuck-documents": {
            "task": "app.tasks.ingestion.recover_stuck_documents_task",
            "schedule": settings.INGESTION_RECOVERY_INTERVAL_MINUTES * 60.0,
        },
    },

    # Explicitly import the tasks package on worker start-up. A task the worker
    # has never imported is not registered, and any job for it fails with
    # "Received unregistered task" -- a genuinely confusing error, because the
    # code plainly exists.
    imports=("app.tasks",),
)

# A second safety net: scan the `app` package for anything decorated with
# `@celery_app.task`. Between this and `imports` above, a newly added task is
# picked up without further configuration.
celery_app.autodiscover_tasks(["app"])


# ----------------------------------------------------------------------
# LOGGING, ERROR REPORTING AND PER-TASK CONTEXT  (core/observability.py)
# ----------------------------------------------------------------------
# Celery normally installs its OWN log handlers and format when a worker
# starts. Connecting to `setup_logging` tells it "logging is handled": the
# worker's lines then come out as the same JSON objects as the API's, which
# one log query can search together.
#
# The task signals give every line a task runs a context of its own -- the
# task id and name -- the worker-side counterpart of the request id the API
# middleware assigns.
from celery import signals  # noqa: E402

from app.core import observability  # noqa: E402

_task_context_tokens: dict = {}


@signals.setup_logging.connect
def _configure_worker_logging(**_kwargs) -> None:
    observability.configure_logging()


@signals.worker_process_init.connect
@signals.worker_init.connect
def _start_worker_sentry(**_kwargs) -> None:
    # Off unless SENTRY_DSN is set; never raises.
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
