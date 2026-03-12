"""
Smoke test for the VaultBubbles FastAPI application.

Uses FastAPI's TestClient (backed by httpx) to verify the app starts and
the /health liveness probe responds correctly. No database required.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
