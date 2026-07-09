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


class Test422ValidationErrors:
    """Test that all Pydantic-protected POST endpoints return 422 on invalid input."""

    @pytest.fixture
    def client(self, temp_dir):
        from fastapi.testclient import TestClient
        import api.server as server_module

        server_module._data_dir = temp_dir
        server_module._mm = None
        server_module._cr = None

        with TestClient(server_module.app, raise_server_exceptions=False) as tc:
            yield tc

        if server_module._mm:
            try:
                server_module._mm.close()
            except Exception:
                pass
            server_module._mm = None
        server_module._cr = None
        server_module._data_dir = ""

    def test_session_end_422_wrong_type(self, client):
        """SessionEndRequest: reason must be str, extract_facts must be bool."""
        resp = client.post("/session/end", json={"reason": 42, "extract_facts": "not_a_bool"})
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

    def test_compress_422_invalid_end_step(self, client):
        """CompressRequest: end_step must be int or None, not a string."""
        resp = client.post("/compress", json={"end_step": "not_a_number"})
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

    def test_cognitive_start_422_plan_not_list(self, client):
        """CognitiveStartRequest: plan must be a list of strings."""
        resp = client.post("/cognitive/start", json={
            "goal": "test",
            "plan": "not_a_list",
            "space": "project"
        })
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

    def test_cognitive_context_422_token_budget_string(self, client):
        """CognitiveContextRequest: token_budget must be int."""
        resp = client.post("/cognitive/context", json={
            "query": "test",
            "token_budget": "not_an_int"
        })
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

    def test_cognitive_step_422_tool_calls_not_list(self, client):
        """CognitiveStepRequest: tool_calls must be a list of dicts."""
        resp = client.post("/cognitive/step", json={
            "response": "some response",
            "tool_calls": "not_a_list",
            "tool_outputs": [],
            "user_input": ""
        })
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

    def test_cognitive_reflect_422_tool_outputs_not_list(self, client):
        """CognitiveReflectRequest: tool_outputs must be a list of dicts."""
        resp = client.post("/cognitive/reflect", json={
            "response": "some reflection",
            "tool_calls": [],
            "tool_outputs": "not_a_list"
        })
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

    def test_cognitive_space_422_space_not_str(self, client):
        """CognitiveSpaceRequest: space must be a string."""
        resp = client.post("/cognitive/space", json={"space": 12345})
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


class TestHTTPAuthWithKey:
    """HTTP-level auth tests with API key enabled.

    Tests: 401 on missing/wrong key, 200 on valid key, public endpoints,
    and 422 on invalid payloads through the full middleware stack.
    """

    @pytest.fixture
    def client(self, monkeypatch, temp_dir):
        """Create TestClient with API key auth enabled."""
        monkeypatch.setenv("HERMES_MEMORY_API_KEY", "test-secret-key")

        # Reload security and server modules to pick up the env var
        import importlib
        import api.security
        import api.server
        importlib.reload(api.security)
        importlib.reload(api.server)

        server = api.server
        server._data_dir = temp_dir
        server._mm = None
        server._cr = None

        from fastapi.testclient import TestClient
        with TestClient(server.app, raise_server_exceptions=False) as tc:
            yield tc

        if server._mm:
            try:
                server._mm.close()
            except Exception:
                pass
            server._mm = None
        server._cr = None
        server._data_dir = ""

    def test_no_api_key_on_protected(self, client):
        """POST /session/start without X-API-Key → 401."""
        resp = client.post("/session/start", json={"session_id": "test"})
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"

    def test_wrong_api_key(self, client):
        """POST /session/start with wrong key → 401."""
        resp = client.post(
            "/session/start",
            json={"session_id": "test"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"

    def test_valid_api_key(self, client):
        """POST /session/start with valid key → 200."""
        resp = client.post(
            "/session/start",
            json={"session_id": "test"},
            headers={"X-API-Key": "test-secret-key"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_invalid_payload(self, client):
        """POST with broken JSON → 422."""
        resp = client.post(
            "/session/start",
            content=b"not valid json",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": "test-secret-key",
            },
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    def test_health_public(self, client):
        """GET /health without key → 200 (public endpoint)."""
        resp = client.get("/health")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_metrics_public(self, client):
        """GET /metrics without key → 200 (public endpoint)."""
        resp = client.get("/metrics")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_session_end_422(self, client):
        """POST /session/end with invalid payload → 422."""
        resp = client.post(
            "/session/end",
            json={"reason": 42, "extract_facts": "not_bool"},
            headers={"X-API-Key": "test-secret-key"},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    def test_cognitive_422(self, client):
        """POST /cognitive/start with invalid payload → 422."""
        resp = client.post(
            "/cognitive/start",
            json={"goal": "test", "plan": "not_a_list", "space": "project"},
            headers={"X-API-Key": "test-secret-key"},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
