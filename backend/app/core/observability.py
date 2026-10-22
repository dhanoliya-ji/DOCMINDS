"""
observability.py
================
WHAT THIS FILE DOES
-------------------
Makes a RUNNING deployment understandable from the outside. Three pieces:

    configure_logging()   every log line becomes ONE JSON object
    new_context / bind    which request, user and organization a line belongs to
    init_sentry()         optional error reporting, off unless SENTRY_DSN is set

WHY JSON LOGS INSTEAD OF print()
--------------------------------
This codebase used to report what it was doing with `print()`:

    [Ingestion] 1/5 Fetching, scanning and extracting text (type: pdf)...

Perfectly readable in a terminal, and nearly useless in production. With fifty
users at once, the lines of fifty requests interleave, and nothing on a line
says WHICH request, user or organization it belongs to. A log platform
(Render's log stream, Datadog, Loki, CloudWatch) cannot filter it either,
except by regular expression.

The same event as structured logging:

    {"time": "2026-10-22T09:15:02.113+00:00", "level": "INFO",
     "logger": "app.tasks.ingestion", "message": "Fetching, scanning and
     extracting text", "request_id": "3f9c0a...", "org_id": "...",
     "document_id": "...", "file_type": "pdf"}

Every field is queryable: "all ERROR lines for org X", "everything request
3f9c0a did", "how long does stage 1 take for PDFs".

HOW A LINE KNOWS WHICH REQUEST IT BELONGS TO
-------------------------------------------
A `ContextVar` -- Python's "global variable, but private to each request".
asyncio runs many requests on one thread, so an ordinary global would be
overwritten by whichever request ran last. A ContextVar keeps one value per
asyncio task, follows a request into `await`ed calls, and is copied into
`asyncio.to_thread` threads and background tasks too.

    middleware.py  creates the context: {"request_id": "3f9c..."}
    deps.py        adds {"user_id": ..., "org_id": ...} once the caller is known
    JsonFormatter  copies whatever is in the context onto every line

The context holds a DICT, created once per request, and `bind` updates that
dict in place. That detail matters: a value SET on a ContextVar inside a
child task is invisible to its parent, but a change to a dict both of them
hold is seen by both. That is how the access-log line, written by the
middleware AFTER the endpoint ran, still knows the user the endpoint found.

ADDING FIELDS TO ONE LINE
-------------------------
Standard logging's `extra=` -- every key becomes a JSON field:

    logger.info("Document processed", extra={"document_id": doc.id, "chunks": 42})
"""

import contextvars
import datetime
import json
import logging
import sys
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# THE PER-REQUEST CONTEXT
# ----------------------------------------------------------------------
_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "docminds_log_context", default=None
)


def new_context(**fields: Any) -> contextvars.Token:
    """
    Start a fresh context (one per request, or per Celery task).

    Returns a token; pass it to `reset_context` when the unit of work ends,
    so the next request on this task does not inherit these fields.
    """
    clean = {key: str(value) for key, value in fields.items() if value is not None}
    token = _context.set(clean)
    _sentry_bind(clean)
    return token


def reset_context(token: contextvars.Token) -> None:
    """Undo `new_context`."""
    _context.reset(token)


def bind(**fields: Any) -> None:
    """
    Add fields to the CURRENT context, so every later log line carries them.

    Called by api/deps.py once the caller is authenticated:
    `bind(user_id=..., org_id=...)`. Outside any request (a script, a test)
    it simply starts a context of its own.
    """
    clean = {key: str(value) for key, value in fields.items() if value is not None}
    context = _context.get()
    if context is None:
        _context.set(clean)
    else:
        context.update(clean)
    _sentry_bind(clean)


def current_context() -> Dict[str, Any]:
    """A copy of the fields bound so far (empty outside a request)."""
    return dict(_context.get() or {})


def current_request_id() -> Optional[str]:
    """The id of the request being served, if any. Stored on usage events."""
    return (_context.get() or {}).get("request_id")


# ----------------------------------------------------------------------
# FORMATTERS
# ----------------------------------------------------------------------
# The attributes every LogRecord has anyway. Anything ELSE on a record came
# from `extra=` and is copied into the JSON as a field of its own.
_STANDARD_ATTRIBUTES = set(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", (), None))
) | {"message", "asctime"}


def _extra_fields(record: logging.LogRecord) -> Dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_ATTRIBUTES and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    """One JSON object per line: the format log platforms index natively."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            # UTC with milliseconds, ISO 8601 -- sorts correctly as text and
            # every log tool parses it without configuration.
            "time": datetime.datetime.fromtimestamp(
                record.created, tz=datetime.timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Request id, user, organization, Celery task...
        payload.update(_context.get() or {})
        # ...then this one line's own fields, which win on a clash.
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        # `default=str` turns UUIDs, datetimes and Decimals into text instead
        # of crashing the logger -- a log call must never raise.
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """
    LOG_FORMAT=text: one human-readable line, with the same fields appended
    as key=value pairs. Nicer in a local terminal; worse for a log platform.
    """

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        line = f"{timestamp} {record.levelname:<7} {record.name}: {record.getMessage()}"
        fields = {**(_context.get() or {}), **_extra_fields(record)}
        if fields:
            line += "  " + " ".join(f"{key}={value}" for key, value in fields.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class _CurrentStdoutHandler(logging.StreamHandler):
    """
    A StreamHandler that writes to whatever `sys.stdout` is NOW.

    A normal StreamHandler remembers the stream it was created with. Test
    runners (pytest) and some process managers swap `sys.stdout` out and
    later close the stand-in; a handler still holding the old one then fails
    with "I/O operation on closed file". Looking it up on every write avoids
    that entirely.
    """

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, _value) -> None:
        # StreamHandler.__init__ assigns self.stream; there is nothing to keep.
        pass


# Marks the handler THIS module installed, so calling configure_logging()
# twice replaces it instead of adding a second one (which would print every
# line twice), and never removes a handler someone else added.
_OUR_HANDLER = "_docminds_handler"


def configure_logging() -> None:
    """
    Send every log record in the process through one formatter.

    Called once by main.py when the API starts, and by the Celery worker
    through its `setup_logging` signal (core/celery_app.py). Safe to call
    again: it replaces its own handler rather than stacking another.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _OUR_HANDLER, False):
            root.removeHandler(handler)

    handler = _CurrentStdoutHandler()
    setattr(handler, _OUR_HANDLER, True)
    handler.setFormatter(JsonFormatter() if settings.LOG_FORMAT == "json" else TextFormatter())
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL)

    # Uvicorn configures its own loggers with their own handlers and format
    # before it imports this app. Route them through ours instead, so its
    # startup and error lines are JSON too.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    # Uvicorn's own access log is replaced by the richer one in
    # core/middleware.py (request id, user, organization, duration), so it
    # is silenced rather than printing every request twice.
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False

    # httpx logs every outgoing HTTP call (to Groq, Gemini) at INFO. Useful
    # when debugging, noise otherwise.
    if settings.LOG_LEVEL != "DEBUG":
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)

    # With ENV=development, SQLAlchemy's `echo=True` prints SQL through a
    # handler of its own. Letting those records ALSO reach ours would print
    # every statement twice.
    logging.getLogger("sqlalchemy.engine.Engine").propagate = False


# ----------------------------------------------------------------------
# SENTRY (optional)
# ----------------------------------------------------------------------
# Sentry collects unhandled exceptions, with the stack trace, the request and
# the tags below, and groups repeats -- so "this crashed 40 times since the
# deploy" is one alert, not 40 log lines nobody reads.
#
# OFF unless SENTRY_DSN is set. `sentry_sdk` is imported only then, so a
# deployment without it never loads the package, and one where it is
# missing logs a warning and carries on rather than refusing to start.
_sentry_enabled = False


def init_sentry(component: str) -> bool:
    """
    Switch Sentry on for this process. `component` is "api", "worker" or
    "beat", attached to every event as a tag.

    Returns True if Sentry is now active. Never raises: a monitoring tool
    that can take the application down is worse than no monitoring.
    """
    global _sentry_enabled
    if _sentry_enabled:
        return True
    if not settings.SENTRY_DSN:
        return False

    try:
        import sentry_sdk
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; error reporting is off. "
            "Run: pip install -r requirements.txt"
        )
        return False

    integrations = []
    try:
        if component == "api":
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.starlette import StarletteIntegration

            integrations += [StarletteIntegration(), FastApiIntegration()]
        else:
            from sentry_sdk.integrations.celery import CeleryIntegration

            integrations.append(CeleryIntegration())
    except Exception as exc:  # an integration that cannot load is not fatal
        logger.warning("A Sentry integration could not be loaded", extra={"error": repr(exc)})

    try:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT or settings.ENV,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            # Never attach request bodies, cookies or IP addresses by
            # default: the bodies here are questions about confidential
            # documents.
            send_default_pii=False,
            integrations=integrations,
        )
        sentry_sdk.set_tag("component", component)
    except Exception as exc:
        # A malformed DSN, mostly. Say so, and run without Sentry.
        logger.error("Sentry could not be started; continuing without it", extra={"error": repr(exc)})
        return False

    _sentry_enabled = True
    logger.info("Sentry error reporting is on", extra={"component": component})
    return True


def sentry_enabled() -> bool:
    return _sentry_enabled


def _sentry_bind(fields: Dict[str, str]) -> None:
    """Copy the request id, user and organization onto Sentry's events too."""
    if not _sentry_enabled or not fields:
        return
    try:
        import sentry_sdk

        for key in ("request_id", "org_id", "task_id"):
            if key in fields:
                sentry_sdk.set_tag(key, fields[key])
        if "user_id" in fields:
            # Only the id: no email, no IP (send_default_pii is off).
            sentry_sdk.set_user({"id": fields["user_id"]})
    except Exception:  # pragma: no cover - never let tagging break a request
        pass
