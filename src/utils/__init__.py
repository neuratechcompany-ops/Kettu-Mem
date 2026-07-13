"""
Utilities layer for Kettu Mem.

Exports:
  setup_logging — configure structlog
  get_logger — get structured logger
  LoggingMiddleware — ASGI logging middleware
"""

from utils.logging import (
    LoggingMiddleware,
    get_latency_ms,
    get_logger,
    get_request_id,
    get_session_id,
    set_request_id,
    set_session_id,
    setup_logging,
    start_latency_timer,
)

__all__ = [
    "setup_logging",
    "get_logger",
    "LoggingMiddleware",
    "get_request_id",
    "set_request_id",
    "get_session_id",
    "set_session_id",
    "get_latency_ms",
    "start_latency_timer",
]
