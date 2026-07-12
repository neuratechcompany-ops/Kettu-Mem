"""
HTTP integration tests for Kettu Mem v0.2.1.

Tests auth (401/200), payload validation (422), and public endpoints.
Uses fastapi.testclient.TestClient with HERMES_MEMORY_API_KEY set.
"""

import shutil
import sys
import tempfile
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


class TestHTTPIntegration:
    """HTTP-level integration tests with API key auth enabled."""

    @pytest.fixture
    def client(self, monkeypatch, temp_dir):
        """Create TestClient with HERMES_MEMORY_API_KEY set to ***."""
        monkeypatch.setenv("HERMES_MEMORY_API_KEY", "***")

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

    def test_no_key_401(self, client):
        """POST /session/start without X-API-Key header → 401."""
        resp = client.post("/session/start", json={"session_id": "test"})
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"

    def test_wrong_key_401(self, client):
        """POST /session/start with X-API-Key: wrong → 401."""
        resp = client.post(
            "/session/start",
            json={"session_id": "test"},
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"

    def test_valid_key_200(self, client):
        """POST /session/start with correct key → 200."""
        resp = client.post(
            "/session/start",
            json={"session_id": "test"},
            headers={"X-API-Key": "***"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_invalid_payload_422(self, client):
        """POST /turn/before with broken JSON → 422."""
        resp = client.post(
            "/turn/before",
            content=b"not valid json {{{",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": "***",
            },
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    def test_invalid_payload2_422(self, client):
        """POST /turn/after without 'events' field → 422."""
        resp = client.post(
            "/turn/after",
            json={"extract_facts": True},
            headers={"X-API-Key": "***"},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    def test_mem0_add_invalid_422(self, client):
        """POST /mem0/add without 'content' → 422."""
        resp = client.post(
            "/mem0/add",
            json={"type": "fact", "confidence": 0.9},
            headers={"X-API-Key": "***"},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    def test_health_200(self, client):
        """GET /health without key → 200."""
        resp = client.get("/health")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_ready_200(self, client):
        """GET /ready without key → 200."""
        resp = client.get("/ready")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_metrics_200(self, client):
        """GET /metrics without key → 200."""
        resp = client.get("/metrics")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
