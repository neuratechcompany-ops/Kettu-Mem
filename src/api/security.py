"""
Security — API key auth, rate limiting, input validation/sanitization.

Middleware for FastAPI:
  - APIKeyAuth: validates X-API-Key header
  - RateLimiter: sliding window rate limiting per client IP
  - RequestValidation: Pydantic models for all inputs
  - InputSanitizer: strips dangerous patterns from inputs

Usage:
  from api.security import add_security_middleware
  add_security_middleware(app)
"""

import re
import time
from collections import defaultdict
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings

# ── Pydantic request models ─────────────────────────────


class SessionStartRequest(BaseModel):
    session_id: str = Field(default="", max_length=128)
    project_id: str = Field(default="default", max_length=64)

    @field_validator("session_id")
    @classmethod
    def sanitize_id(cls, v):
        return re.sub(r"[^\w\-]", "", v)[:128]


class SessionEndRequest(BaseModel):
    reason: str = Field(default="manual", max_length=64)
    extract_facts: bool = True


class TurnBeforeRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4096)
    strategy: str = Field(default="normal", max_length=32)
    system_prompt: Optional[str] = Field(default=None, max_length=16384)
    tools: list[dict] = Field(default_factory=list)
    token_budget: Optional[int] = Field(default=None, ge=100, le=256000)

    @field_validator("query")
    @classmethod
    def sanitize_query(cls, v):
        return InputSanitizer.sanitize(v)


class TurnAfterRequest(BaseModel):
    events: list[dict] = Field(..., min_length=1)
    extract_facts: bool = True


class CompressRequest(BaseModel):
    end_step: Optional[int] = None


class Mem0AddRequest(BaseModel):
    type: str = Field(default="fact", max_length=32)
    content: str = Field(..., min_length=1, max_length=8192)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    entities: list[str] = Field(default_factory=list)


class CognitiveStartRequest(BaseModel):
    goal: str = Field(default="", max_length=4096)
    plan: list[str] = Field(default_factory=list)
    space: str = Field(default="project", max_length=32)


class CognitiveContextRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4096)
    token_budget: int = Field(default=32000, ge=100, le=256000)


class CognitiveStepRequest(BaseModel):
    response: str = Field(default="", max_length=16384)
    tool_calls: list[dict] = Field(default_factory=list)
    tool_outputs: list[dict] = Field(default_factory=list)
    user_input: str = Field(default="", max_length=4096)


class CognitiveReflectRequest(BaseModel):
    response: str = Field(default="", max_length=16384)
    tool_calls: list[dict] = Field(default_factory=list)
    tool_outputs: list[dict] = Field(default_factory=list)


class CognitiveSpaceRequest(BaseModel):
    space: str = Field(default="project", max_length=32)


# ── Input Sanitizer ─────────────────────────────────────


class InputSanitizer:
    """Strip dangerous patterns from user inputs."""

    # Patterns to remove/replace
    _DANGEROUS_PATTERNS = [
        # SQL injection fragments
        (r"(?i)(\b)(?:SELECT|INSERT|UPDATE|DELETE|DROP|UNION|ALTER)\b", "…"),
        # Script tags
        (r"<script[^>]*>.*?</script>", ""),
        # Null bytes
        (r"\x00", ""),
        # Control characters (except newline, tab)
        (r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", ""),
    ]

    @classmethod
    def sanitize(cls, text: str) -> str:
        """Sanitize input text."""
        if not text or not isinstance(text, str):
            return text
        for pattern, replacement in cls._DANGEROUS_PATTERNS:
            text = re.sub(pattern, replacement, text, flags=re.DOTALL)
        return text[:65536]  # max 64KB


# ── API Key Auth ────────────────────────────────────────


def _get_api_key() -> Optional[str]:
    """Resolve API key: HERMES_MEMORY_API_KEY > KETTU_MEM_API_KEY > settings."""
    import logging
    import os

    dev_logger = logging.getLogger("kettu-mem.security")

    key = os.getenv("HERMES_MEMORY_API_KEY") or settings.api_key
    if not key:
        dev_logger.warning(
            "SECURITY: No API key configured (HERMES_MEMORY_API_KEY not set). "
            "Server running in DEV MODE — all endpoints are public. "
            "Set HERMES_MEMORY_API_KEY for production."
        )
    return key


class APIKeyAuth:
    """API Key authentication middleware."""

    def __init__(self, api_key: str = None):
        self._enabled = True
        self.api_key = api_key or _get_api_key()
        if not self.api_key:
            self.api_key = None  # будет прочитан при первом запросе


# ── Rate Limiter ────────────────────────────────────────


class RateLimiter:
    """
    Sliding window rate limiter per client IP.

    Configurable via settings:
      rate_limit_requests (default 100)
      rate_limit_window (default 60 seconds)
    """

    def __init__(self):
        self.max_requests = settings.rate_limit_requests
        self.window = settings.rate_limit_window
        self._clients: dict[str, list[float]] = defaultdict(list)
        self._blocked: dict[str, float] = {}  # ip → unblock_time

    def _cleanup(self, now: float):
        """Periodic cleanup of old entries."""
        # Remove expired blocks
        for ip in list(self._blocked.keys()):
            if now > self._blocked[ip]:
                del self._blocked[ip]

        # Trim client windows periodically
        if len(self._clients) > 10000:
            # Keep only last 1000
            keys = sorted(
                self._clients.keys(), key=lambda k: self._clients[k][-1] if self._clients[k] else 0
            )
            for key in keys[:-1000]:
                del self._clients[key]

    def is_allowed(self, client_ip: str) -> tuple[bool, str]:
        """
        Check if request is allowed under rate limit.

        Returns (allowed, reason).
        """
        now = time.time()
        self._cleanup(now)

        # Check block
        if client_ip in self._blocked and now < self._blocked[client_ip]:
            remaining = int(self._blocked[client_ip] - now)
            return False, f"rate_limited:retry_in_{remaining}s"

        # Sliding window
        window = self._clients[client_ip]
        # Remove old entries
        cutoff = now - self.window
        self._clients[client_ip] = [t for t in window if t > cutoff]

        if len(self._clients[client_ip]) >= self.max_requests:
            # Block for window duration
            self._blocked[client_ip] = now + self.window
            return False, "rate_limited:too_many_requests"

        self._clients[client_ip].append(now)
        return True, "ok"

    def get_client_ip(self, request: Request) -> str:
        """Extract client IP from request, respecting proxies."""
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP", "")
        if real_ip:
            return real_ip
        if request.client:
            return request.client.host
        return "unknown"


# ── Security Middleware (combined) ──────────────────────

# Public endpoints (no API key required)
PUBLIC_PATHS = {"/health", "/ready", "/live", "/health/deep", "/metrics", "/status"}


class SecurityMiddleware(BaseHTTPMiddleware):
    """Combined security middleware: API key auth + rate limiting + input validation."""

    def __init__(self, app):
        super().__init__(app)
        self.rate_limiter = RateLimiter()
        self.api_key_auth = APIKeyAuth()

    async def dispatch(self, request: Request, call_next):
        # API key auth (unless public endpoint)
        # Read API key at request time (not import time)
        api_key = _get_api_key()
        if api_key and request.url.path not in PUBLIC_PATHS:
            provided_key = request.headers.get("X-API-Key", "")
            if not provided_key or provided_key != api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid API key"},
                )

        # Rate limit
        client_ip = self.rate_limiter.get_client_ip(request)
        allowed, reason = self.rate_limiter.is_allowed(client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited", "detail": reason},
                headers={"Retry-After": str(settings.rate_limit_window)},
            )

        # Process
        response = await call_next(request)

        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"

        return response


def add_security_middleware(app):
    """Add all security middleware to a FastAPI app."""
    app.add_middleware(SecurityMiddleware)
