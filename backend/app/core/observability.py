"""Makes a RUNNING deployment understandable from the outside."""
import contextvars
import datetime
import json
import logging
import sys
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "docminds_log_context", default=None
)


def new_context(**fields: Any) -> contextvars.Token:
    """Start a fresh context (one per request, or per Celery task)."""
    clean = {key: str(value) for key, value in fields.items() if value is not None}
    token = _context.set(clean)
    _sentry_bind(clean)
    return token


def reset_context(token: contextvars.Token) -> None:
    """Undo `new_context`."""
    _context.reset(token)


def bind(**fields: Any) -> None:
    """Add fields to the CURRENT context, so every later log line carries them."""
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
            "time": datetime.datetime.fromtimestamp(
                record.created, tz=datetime.timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_context.get() or {})
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """LOG_FORMAT=text: one human-readable line, with the same fields appended"""

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
    """A StreamHandler that writes to whatever `sys.stdout` is NOW."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, _value) -> None:
        pass


_OUR_HANDLER = "_docminds_handler"


def configure_logging() -> None:
    """Send every log record in the process through one formatter."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _OUR_HANDLER, False):
            root.removeHandler(handler)

    handler = _CurrentStdoutHandler()
    setattr(handler, _OUR_HANDLER, True)
    handler.setFormatter(JsonFormatter() if settings.LOG_FORMAT == "json" else TextFormatter())
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL)

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False

    if settings.LOG_LEVEL != "DEBUG":
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("sqlalchemy.engine.Engine").propagate = False


_sentry_enabled = False


def init_sentry(component: str) -> bool:
    """Switch Sentry on for this process. `component` is "api", "worker" or"""
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
            send_default_pii=False,
            integrations=integrations,
        )
        sentry_sdk.set_tag("component", component)
    except Exception as exc:
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
            sentry_sdk.set_user({"id": fields["user_id"]})
    except Exception:  # pragma: no cover - never let tagging break a request
        pass
