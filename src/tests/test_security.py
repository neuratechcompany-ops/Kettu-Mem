"""
Security tests for Kettu Mem v0.2.1.

Tests: API key auth, Pydantic validation, rate limiting, public endpoints.
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _clear_module_state():
    """Reset global state between tests."""
    import api.server as server_module
    server_module._mm = None
    server_module._cr = None
    server_module._data_dir = ""
    yield
    server_module._mm = None
    server_module._cr = None
    server_module._data_dir = ""


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestAPIKeyAuthDirect:
    """Test APIKeyAuth class directly (no HTTP server needed)."""

    def test_enabled_with_key(self):
        """APIKeyAuth is enabled when key is provided."""
        from api.security import APIKeyAuth
        auth = APIKeyAuth("my-secret-key")
        assert auth._enabled is True
        assert auth.api_key == "my-secret-key"

    def test_disabled_without_key(self, monkeypatch):
        """APIKeyAuth is disabled when no key is set anywhere."""
        monkeypatch.delenv("HERMES_MEMORY_API_KEY", raising=False)
        monkeypatch.delenv("KETTU_MEM_API_KEY", raising=False)
        from config import settings
        old = settings.api_key
        settings.api_key = None
        try:
            from api.security import APIKeyAuth
            auth = APIKeyAuth()
            assert auth._enabled is False
        finally:
            settings.api_key = old

    def test_env_var_priority(self, monkeypatch):
        """HERMES_MEMORY_API_KEY takes priority over settings."""
        from api.security import _get_api_key
        monkeypatch.setenv("HERMES_MEMORY_API_KEY", "hermes-secret")
        from config import settings
        old = settings.api_key
        settings.api_key = "settings-secret"
        try:
            key = _get_api_key()
            assert key == "hermes-secret"
        finally:
            settings.api_key = old


class TestSecurityMiddlewareApp:
    """Test security middleware attached to the real app."""

    def test_security_middleware_present(self):
        """SecurityMiddleware is in app.user_middleware."""
        from api.server import app
        names = [m.cls.__name__ for m in app.user_middleware]
        assert "SecurityMiddleware" in names, f"Not found in: {names}"

    def test_cors_and_security_both_present(self):
        """Both CORS and Security middleware are registered."""
        from api.server import app
        names = [m.cls.__name__ for m in app.user_middleware]
        assert "CORSMiddleware" in names
        assert "SecurityMiddleware" in names

    def test_cors_outermost_security_inner(self):
        """CORS is outermost, Security runs between CORS and app."""
        from api.server import app
        names = [m.cls.__name__ for m in app.user_middleware]
        # user_middleware order is the order they were added.
        # Starlette wraps them LIFO: first added = outermost.
        # So execution order (outermost→innermost) is reversed list.
        # We need CORS (outermost) → Security → Logging → Metrics (innermost).
        # In user_middleware: [first_added, ..., last_added]
        # Execution: last_added → ... → first_added
        # If user_middleware = [CORS, Security, Logging, Metrics],
        # execution = Metrics → Logging → Security → CORS. That's wrong.
        # We want execution: CORS → Security → Logging → Metrics.
        # So user_middleware should be [Metrics, Logging, Security, CORS].
        # That means Metrics was added first (innermost), CORS last (outermost).
        cors_pos = names.index("CORSMiddleware")
        sec_pos = names.index("SecurityMiddleware")
        # CORS should be outermost (last in execution = first or last in list?)
        # Actually: Starlette middleware stack: last added goes on top (outermost).
        # So in user_middleware, first element was added first (innermost).
        # For CORS to be outermost, it should be last in user_middleware.
        assert cors_pos > sec_pos, \
            f"CORS should be outermost (after Security in list): CORS={cors_pos}, Security={sec_pos}, list={names}"


class TestHealthPublicEndpoints:
    """Public endpoints should work without any key."""

    def test_health_no_key(self):
        from api.server import health
        import asyncio
        result = asyncio.new_event_loop().run_until_complete(health())
        assert result["status"] == "ok"


class TestRateLimitingDirect:
    """Test rate limiter logic directly."""

    def test_rate_limiter_blocks_after_limit(self):
        from api.security import RateLimiter
        rl = RateLimiter()
        rl.max_requests = 3
        rl.window = 60

        for _ in range(3):
            allowed, reason = rl.is_allowed("10.0.0.99")
            assert allowed, f"Expected allowed, got {reason}"

        allowed, reason = rl.is_allowed("10.0.0.99")
        assert not allowed
        assert "rate_limited" in reason

    def test_different_ips_not_blocked(self):
        from api.security import RateLimiter
        rl = RateLimiter()
        rl.max_requests = 3
        rl.window = 60

        for _ in range(3):
            rl.is_allowed("10.0.0.1")

        allowed, _ = rl.is_allowed("10.0.0.2")
        assert allowed

    def test_get_client_ip_forwarded(self):
        from api.security import RateLimiter
        from unittest.mock import MagicMock
        rl = RateLimiter()
        mock_req = MagicMock()
        mock_req.headers = {"X-Forwarded-For": "10.0.0.1, 10.0.0.2"}
        mock_req.client = MagicMock()
        mock_req.client.host = "127.0.0.1"
        assert rl.get_client_ip(mock_req) == "10.0.0.1"


class TestPydanticValidation:
    """Test Pydantic models directly (no HTTP)."""

    def test_session_start_valid(self):
        from api.security import SessionStartRequest
        r = SessionStartRequest(session_id="test", project_id="p1")
        assert r.session_id == "test"
        assert r.project_id == "p1"

    def test_session_start_defaults(self):
        from api.security import SessionStartRequest
        r = SessionStartRequest()
        assert r.session_id == ""
        assert r.project_id == "default"

    def test_turn_before_valid(self):
        from api.security import TurnBeforeRequest
        r = TurnBeforeRequest(query="hello", strategy="normal", token_budget=32000)
        assert r.query == "hello"
        assert r.token_budget == 32000

    def test_turn_before_invalid_budget(self):
        from api.security import TurnBeforeRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            TurnBeforeRequest(token_budget=-5)

    def test_turn_after_valid(self):
        from api.security import TurnAfterRequest
        r = TurnAfterRequest(events=[
            {"role": "user", "type": "message", "content": "test"}
        ])
        assert len(r.events) == 1

    def test_mem0_add_valid(self):
        from api.security import Mem0AddRequest
        r = Mem0AddRequest(type="fact", content="Test fact", confidence=0.8)
        assert r.confidence == 0.8

    def test_mem0_add_invalid_confidence(self):
        from api.security import Mem0AddRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            Mem0AddRequest(confidence=2.0)


class TestInputSanitizer:
    """Test input sanitization."""

    def test_sql_injection_removed(self):
        from api.security import InputSanitizer
        result = InputSanitizer.sanitize("SELECT * FROM users WHERE 1=1")
        assert "SELECT" not in result

    def test_script_tags_removed(self):
        from api.security import InputSanitizer
        result = InputSanitizer.sanitize("<script>alert('xss')</script>")
        assert "<script>" not in result

    def test_null_bytes_removed(self):
        from api.security import InputSanitizer
        result = InputSanitizer.sanitize("hello\x00world")
        assert "\x00" not in result

    def test_normal_text_passes(self):
        from api.security import InputSanitizer
        result = InputSanitizer.sanitize("Normal text")
        assert result == "Normal text"

    def test_none_passes(self):
        from api.security import InputSanitizer
        assert InputSanitizer.sanitize(None) is None
