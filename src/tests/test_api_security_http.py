"""HTTP security integration tests via TestClient."""
import os

import pytest
from fastapi.testclient import TestClient

API_KEY = "kettu-mem-secure-key-2026"


@pytest.fixture(autouse=True)
def setup_env():
    os.environ["HERMES_MEMORY_API_KEY"] = "kettu-mem-secure-key-2026"
    yield
    os.environ.pop("HERMES_MEMORY_API_KEY", None)


@pytest.fixture
def client():
    from api.server import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestSecurityHTTP:
    """Real HTTP security tests via TestClient."""

    # Auth: no key
    def test_no_key_returns_401(self, client):
        resp = client.get("/mem0/stats")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    # Auth: wrong key
    def test_wrong_key_returns_401(self, client):
        resp = client.get("/mem0/stats",
                           headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"

    # Auth: valid key
    def test_valid_key_returns_200(self, client):
        resp = client.get("/mem0/stats",
                          headers={"X-API-Key": API_KEY})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # Invalid payload → 422
    def test_invalid_payload_returns_422(self, client):
        resp = client.post("/turn/before",
                           json={"bad": "data"},
                           headers={"X-API-Key": API_KEY})
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    # Public endpoints — no key needed
    def test_health_public(self, client):
        assert client.get("/health").status_code == 200

    def test_ready_public(self, client):
        assert client.get("/ready").status_code == 200

    def test_live_public(self, client):
        assert client.get("/live").status_code == 200

    def test_metrics_public(self, client):
        assert client.get("/metrics").status_code == 200
