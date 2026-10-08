"""Regression guard for T-001: WFAS save routes must set Portfolio.mode
from settings['hybrid_mode'] at write time.

Prior bug: /save-track and /save-phase2-track constructed Portfolio(...)
without a `mode=` kwarg, so the SQLAlchemy column default ('non_hybrid')
won regardless of whether the user saved in hybrid mode. Result: saved
portfolios silently bypassed refresh_all_hybrid_portfolios, their
baskets froze, and Monthly PnL went empty post-cutoff.

These tests hit both routes end-to-end with an in-memory DB (no cleanup
needed — the DB dies with the process).
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.app import create_app
from backend.db.database import get_db
from backend.db.models import Base, Portfolio


_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
  cur.execute("PRAGMA foreign_keys=ON")
  cur.close()


_Session = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


def _override_get_db():
  db = _Session()
  try:
    yield db
  finally:
    db.close()


def _client() -> TestClient:
  Base.metadata.create_all(_ENGINE)
  app = create_app()
  app.dependency_overrides[get_db] = _override_get_db
  return TestClient(app)


def _save_track(client: TestClient, name: str, hybrid_mode) -> dict:
  wfo_settings: dict = {}
  if hybrid_mode is not None:
    wfo_settings["hybrid_mode"] = hybrid_mode
  payload = {
    "name": name,
    "batch_ids": [],
    "track_rank": 1,
    "assets": [],
    "wfo_settings": wfo_settings,
    "metrics_snapshot": {"net_profit": 0.0, "total_trades": 0},
  }
  resp = client.post("/api/wfo/save-track", json=payload)
  assert resp.status_code == 200, resp.text
  return resp.json()


def _save_phase2(client: TestClient, name: str, hybrid_mode) -> dict:
  preset_config: dict = {"hybrid_rebalance_every": 5}
  if hybrid_mode is not None:
    preset_config["hybrid_mode"] = hybrid_mode
  payload = {
    "name": name,
    "batch_ids": [],
    "assets": [],
    "preset_config": preset_config,
    "metrics_snapshot": {"net_profit": 0.0, "total_trades": 0},
    "winning_batch_ids": [],
  }
  resp = client.post("/api/wfo/save-phase2-track", json=payload)
  assert resp.status_code == 200, resp.text
  return resp.json()


def _mode_for(pid: int) -> str:
  db = _Session()
  try:
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    assert p is not None, f"Portfolio {pid} not found"
    return p.mode
  finally:
    db.close()


class TestSaveTrackModeWiring:

  def test_hybrid_mode_true_saves_as_hybrid(self):
    client = _client()
    r = _save_track(client, "T001_st_hybrid_true", hybrid_mode=True)
    assert _mode_for(r["id"]) == "hybrid"

  def test_hybrid_mode_false_saves_as_non_hybrid(self):
    client = _client()
    r = _save_track(client, "T001_st_hybrid_false", hybrid_mode=False)
    assert _mode_for(r["id"]) == "non_hybrid"

  def test_hybrid_mode_missing_saves_as_non_hybrid(self):
    client = _client()
    r = _save_track(client, "T001_st_hybrid_missing", hybrid_mode=None)
    assert _mode_for(r["id"]) == "non_hybrid"


class TestSavePhase2ModeWiring:

  def test_hybrid_mode_true_saves_as_hybrid(self):
    client = _client()
    r = _save_phase2(client, "T001_p2_hybrid_true", hybrid_mode=True)
    assert _mode_for(r["id"]) == "hybrid"

  def test_hybrid_mode_false_saves_as_non_hybrid(self):
    client = _client()
    r = _save_phase2(client, "T001_p2_hybrid_false", hybrid_mode=False)
    assert _mode_for(r["id"]) == "non_hybrid"

  def test_hybrid_mode_missing_defaults_to_hybrid(self):
    # Phase 2 output is inherently the hybrid rotation signal. If the
    # preset_config omits hybrid_mode, default to hybrid rather than
    # silently dropping the portfolio into the non_hybrid freeze path.
    client = _client()
    r = _save_phase2(client, "T001_p2_hybrid_missing", hybrid_mode=None)
    assert _mode_for(r["id"]) == "hybrid"
