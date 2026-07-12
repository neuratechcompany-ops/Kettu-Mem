"""
Logging — structlog-based structured logging.

Features:
- JSON-formatted logs (production) or console (dev)
- Request ID tracking (auto-generated per request)
- Session ID in all log entries
- Latency breakdown per endpoint
- Configurable log level via settings

Usage:
  from utils.logging import setup_logging, get_logger
  setup_logging()
  logger = get_logger(__name__)
  logger.info("event_recorded", event_id="abc", latency_ms=12.3)
"""
import sys
import time
import uuid
from contextvars import ContextVar

import structlog

from config import settings

# Context variables for request-scoped state
_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_session_id_var: ContextVar[str] = ContextVar("session_id", default="-")
_latency_start_var: ContextVar[float] = ContextVar("latency_start", default=0.0)


def get_request_id() -> str:
    """Get current request ID."""
    return _request_id_var.get()


def set_request_id(rid: str = None) -> str:
    """Set request ID for current context."""
    rid = rid or uuid.uuid4().hex[:12]
    _request_id_var.set(rid)
    return rid


def get_session_id() -> str:
    """Get current session ID."""
    return _session_id_var.get()


def set_session_id(sid: str):
    """Set session ID for current context."""
    _session_id_var.set(sid)


def start_latency_timer():
    """Start measuring endpoint latency."""
    _latency_start_var.set(time.time())


def get_latency_ms() -> float:
    """Get elapsed latency in milliseconds."""
    start = _latency_start_var.get()
    if start == 0:
        return 0
    return round((time.time() - start) * 1000, 1)


# ── Structlog configuration ─────────────────────────────

def setup_logging(log_level: str = None):
    """
    Configure structlog for Kettu Mem.

    Args:
        log_level: Override log level (defaults to settings.log_level)
    """
    level = (log_level or settings.log_level).upper()
    fmt = settings.log_format

    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # Shared processors
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.filter_by_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to use structlog
    import logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level, logging.INFO),
    )


def get_logger(name: str = None) -> structlog.BoundLogger:
    """Get a structlog logger with Kettu Mem context."""
    logger = structlog.get_logger(name or "kettu_mem")
    return logger.bind(
        component=name or "kettu_mem",
        request_id=_request_id_var,
        session_id=_session_id_var,
    )


# ── Middleware for request logging ──────────────────────

class LoggingMiddleware:
    """
    ASGI middleware that injects request_id, session_id, and latency.

    Usage (in FastAPI):
        app.add_middleware(LoggingMiddleware)
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = uuid.uuid4().hex[:12]
        set_request_id(rid)
        start_latency_timer()

        logger = get_logger("api")
        path = scope.get("path", "?")
        method = scope.get("method", "?")
        status_code = 0

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            latency = get_latency_ms()
            logger.info(
                "request_complete",
                method=method, path=path, status=status_code,
                latency_ms=latency,
            )
        except Exception as e:
            logger.error("request_failed", error=str(e), path=path)
            raise


def add_logging_middleware(app):
    """Add logging middleware to a FastAPI app."""
    app.add_middleware(LoggingMiddleware)
