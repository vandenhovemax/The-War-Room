"""API key middleware (backend/api/auth.py).

2026-10-08: with a key configured, a missing or wrong key came back as 500,
not 401 — an HTTPException raised inside a BaseHTTPMiddleware bypasses
FastAPI's handlers. The shipped .env.example also set a placeholder key, so
copying it broke every UI call. These tests pin the intended behaviour.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.api.auth as auth
from backend.api.app import create_app

KEY = "s3cret-test-key"
# A path with no route: past the middleware it is a 404, so the status alone
# says whether auth let the request through (no database needed).
PROBE = "/api/__auth_probe__"


@pytest.fixture
def client_with_key(monkeypatch):
  monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(secret_key=KEY))
  return TestClient(create_app())


def test_missing_key_is_401_not_500(client_with_key):
  r = client_with_key.get(PROBE)
  assert r.status_code == 401
  assert "API key" in r.json()["detail"]


def test_wrong_key_is_401(client_with_key):
  assert client_with_key.get(PROBE, headers={"X-API-Key": "nope"}).status_code == 401


def test_right_key_passes_either_header(client_with_key):
  assert client_with_key.get(PROBE, headers={"X-API-Key": KEY}).status_code == 404
  assert client_with_key.get(PROBE, headers={"Authorization": f"Bearer {KEY}"}).status_code == 404


def test_exempt_paths_need_no_key(client_with_key):
  assert client_with_key.get("/openapi.json").status_code == 200


def test_no_key_configured_means_open(monkeypatch):
  monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(secret_key=""))
  assert TestClient(create_app()).get(PROBE).status_code == 404


def test_env_example_ships_with_auth_off():
  from pathlib import Path
  example = Path(__file__).resolve().parents[1] / "config" / ".env.example"
  lines = [ln.strip() for ln in example.read_text().splitlines()]
  assert "SECRET_KEY=" in lines
