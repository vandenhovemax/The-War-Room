"""Tests for the heartbeat helper + /health component staleness reporting.

Guards the watchdog loop that Telegram uses to detect stuck background
processes (candle_sync, engine reconciliation). Without these tests a
refactor could silently break the "backend is dying at 3am" alert.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.utils import heartbeats
from backend.api.routes import dashboard as dashboard_route


# ---------------------------------------------------------------------------
# Fixtures — isolate the heartbeat dir so tests don't touch production files
# ---------------------------------------------------------------------------

@pytest.fixture
def hb_dir(tmp_path, monkeypatch):
  d = tmp_path / "heartbeats"
  monkeypatch.setattr(heartbeats, "HEARTBEAT_DIR", d)
  return d


# ---------------------------------------------------------------------------
# Heartbeat helper
# ---------------------------------------------------------------------------

def test_beat_creates_file_with_parseable_timestamp(hb_dir):
  heartbeats.beat("alpha")
  f = hb_dir / "alpha.hb"
  assert f.exists()
  raw = f.read_text(encoding="utf-8").strip()
  ts = datetime.fromisoformat(raw)
  # Written as UTC-aware
  assert ts.tzinfo is not None


def test_read_returns_none_for_missing(hb_dir):
  assert heartbeats.read("never_beaten") is None


def test_read_roundtrip(hb_dir):
  heartbeats.beat("bravo")
  ts = heartbeats.read("bravo")
  assert ts is not None
  # Fresh beat should be within 5s of now
  delta = (datetime.now(timezone.utc) - ts).total_seconds()
  assert 0 <= delta < 5


def test_age_seconds_none_for_missing(hb_dir):
  assert heartbeats.age_seconds("nope") is None


def test_age_seconds_small_for_fresh(hb_dir):
  heartbeats.beat("charlie")
  age = heartbeats.age_seconds("charlie")
  assert age is not None
  assert 0 <= age < 5


def test_is_stale_true_when_missing(hb_dir):
  assert heartbeats.is_stale("ghost", 60) is True


def test_is_stale_false_when_fresh(hb_dir):
  heartbeats.beat("delta")
  assert heartbeats.is_stale("delta", 60) is False


def test_is_stale_true_when_old(hb_dir):
  # Write a timestamp 10 minutes in the past
  hb_dir.mkdir(parents=True, exist_ok=True)
  old = datetime.now(timezone.utc) - timedelta(minutes=10)
  (hb_dir / "echo.hb").write_text(old.isoformat(), encoding="utf-8")
  assert heartbeats.is_stale("echo", 60) is True
  assert heartbeats.is_stale("echo", 3600) is False


def test_beat_overwrites_not_appends(hb_dir):
  heartbeats.beat("foxtrot")
  time.sleep(0.01)
  heartbeats.beat("foxtrot")
  raw = (hb_dir / "foxtrot.hb").read_text(encoding="utf-8").strip()
  # Exactly one line
  assert "\n" not in raw
  # Still parses as a single timestamp
  datetime.fromisoformat(raw)


def test_empty_name_raises(hb_dir):
  with pytest.raises(ValueError):
    heartbeats.read("")
  with pytest.raises(ValueError):
    heartbeats.read("/")  # all chars stripped → empty → rejected


def test_path_traversal_is_sanitized_not_allowed(hb_dir):
  # Dots and slashes are stripped, so "../../etc/passwd" becomes a safe
  # "etcpasswd" file inside HEARTBEAT_DIR — no traversal escape possible.
  heartbeats.beat("../../../etc/passwd")
  # The escaped characters should not have produced a file outside hb_dir
  assert not (hb_dir.parent / "etc" / "passwd.hb").exists()
  # But the sanitized version should exist inside hb_dir
  assert (hb_dir / "etcpasswd.hb").exists()


def test_beat_never_raises_on_write_failure(hb_dir, monkeypatch):
  # Force HEARTBEAT_DIR to a path we can't create (null char is invalid on all OS)
  bad = Path("\0invalid\0")
  monkeypatch.setattr(heartbeats, "HEARTBEAT_DIR", bad)
  # Must not raise — heartbeats are best-effort, they must never crash the caller
  heartbeats.beat("golf")


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def client(hb_dir):
  """FastAPI TestClient with only the dashboard router mounted.

  We don't need the full app — just the /health route — which keeps the
  test fast and independent of other routes' database requirements.
  """
  app = FastAPI()
  app.include_router(dashboard_route.router)
  return TestClient(app)


def test_health_backwards_compat_fields(client, hb_dir):
  r = client.get("/health")
  assert r.status_code == 200
  body = r.json()
  # Old callers (like the telegram bot's "error" check) must still see these
  assert body["status"] == "ok"
  assert body["service"] == "warroom"


def test_health_ok_when_no_beats(client, hb_dir):
  # Never-beaten components are "unknown" — must NOT trip degraded
  # (otherwise a fresh install would look broken)
  r = client.get("/health")
  body = r.json()
  assert body["status"] == "ok"
  for name, info in body["components"].items():
    assert info["status"] == "unknown"
    assert info["age_seconds"] is None


def test_health_ok_when_all_fresh(client, hb_dir):
  heartbeats.beat("candle_sync")
  heartbeats.beat("engine_reconciliation")
  r = client.get("/health")
  body = r.json()
  assert body["status"] == "ok"
  assert body["components"]["candle_sync"]["status"] == "ok"
  assert body["components"]["engine_reconciliation"]["status"] == "ok"


def test_health_degraded_when_one_stale(client, hb_dir):
  # candle_sync fresh, engine ancient
  heartbeats.beat("candle_sync")
  hb_dir.mkdir(parents=True, exist_ok=True)
  old = datetime.now(timezone.utc) - timedelta(hours=1)
  (hb_dir / "engine_reconciliation.hb").write_text(old.isoformat(), encoding="utf-8")

  r = client.get("/health")
  body = r.json()
  assert body["status"] == "degraded"
  assert body["components"]["candle_sync"]["status"] == "ok"
  assert body["components"]["engine_reconciliation"]["status"] == "stale"
  assert body["components"]["engine_reconciliation"]["age_seconds"] > 60


def test_health_components_carry_max_age(client, hb_dir):
  r = client.get("/health")
  body = r.json()
  # Max-age values are the contract the telegram message uses — keep them visible
  assert body["components"]["candle_sync"]["max_age_seconds"] == 35 * 60
  assert body["components"]["engine_reconciliation"]["max_age_seconds"] == 60


def test_health_unknown_does_not_trigger_degraded(client, hb_dir):
  # Only candle_sync has beaten; engine_reconciliation is unknown.
  # This must stay "ok" — we don't want degraded alerts at startup
  # before the live engine has been started for the first time.
  heartbeats.beat("candle_sync")
  r = client.get("/health")
  body = r.json()
  assert body["status"] == "ok"
  assert body["components"]["engine_reconciliation"]["status"] == "unknown"
