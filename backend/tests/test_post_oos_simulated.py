"""Tests for /api/portfolios/{id}/post-oos-simulated-metrics — the
"Non-Realizable" column in the OOS-vs-LIVE panel.

STATUS (2026-10 audit): SPEC TESTS FOR AN UNBUILT FEATURE. The route was
never implemented — no handler exists in backend/api, no git history
contains it, and the frontend never calls it. These tests pin the
intended short-circuit contract and are skipped until the endpoint is
built. Remove the skip mark (not the tests) when it lands.

Scope:
  * Portfolio with no optimization_end_date      → empty payload
  * Portfolio whose oos_end is still in the future → empty payload
  * Portfolio past oos_end but no active assets  → empty payload
  * Portfolio past oos_end with assets whose batches are missing
    → empty payload (graceful fallback, never raises)

We do not exercise the engine re-run here; that path is covered
indirectly by parity_canary tests. These tests pin the route's
short-circuit logic so a future refactor can't silently start returning
non-empty data for portfolios that shouldn't have any.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.app import create_app
import backend.db.database as _db_module
from backend.db.database import get_db
from backend.db.models import Base, Portfolio, PortfolioAsset


pytestmark = pytest.mark.skip(
    reason="spec test: /post-oos-simulated-metrics endpoint was never "
           "implemented (no route in backend/api, no frontend caller). "
           "Unskip when the endpoint is built."
)

_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _set_pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
  cur.execute("PRAGMA journal_mode=WAL")
  cur.execute("PRAGMA foreign_keys=ON")
  cur.close()


_Session = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


def _override_get_db():
  db = _Session()
  try:
    yield db
  finally:
    db.close()


@pytest.fixture(scope="module", autouse=True)
def _schema():
  Base.metadata.create_all(bind=_ENGINE)
  # Route the app's SessionLocal to this in-memory engine so any code path
  # that opens its own session (e.g. deferred imports) stays on the test DB.
  _prev = _db_module.SessionLocal
  _db_module.SessionLocal = _Session
  yield
  _db_module.SessionLocal = _prev
  Base.metadata.drop_all(bind=_ENGINE)


@pytest.fixture()
def client():
  app = create_app()
  app.dependency_overrides[get_db] = _override_get_db
  with TestClient(app) as c:
    yield c
  app.dependency_overrides.clear()


@pytest.fixture()
def db():
  s = _Session()
  try:
    s.query(PortfolioAsset).delete()
    s.query(Portfolio).delete()
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


def _make_portfolio(
  db, *,
  opt_end: datetime | None,
  oos_length_days: int = 30,
  assets: list[tuple[str, str, int | None]] | None = None,
) -> int:
  p = Portfolio(
    name="test",
    portfolio_type="paper",
    mode="non_hybrid",
    is_active=True,
    is_deleted=False,
    optimization_end_date=opt_end,
    source_strategies=json.dumps({"oos_length_days": oos_length_days}),
  )
  db.add(p)
  db.flush()
  for sym, side, bid in (assets or []):
    db.add(PortfolioAsset(
      portfolio_id=p.id, symbol=sym, trade_side=side,
      batch_id=bid, is_active=True,
      config_name="default", timeframe="30",
    ))
  db.commit()
  return p.id


def test_no_optimization_end_date_returns_empty(client, db):
  pid = _make_portfolio(db, opt_end=None)
  r = client.get(f"/api/portfolios/{pid}/post-oos-simulated-metrics")
  assert r.status_code == 200
  payload = r.json()
  assert payload["metrics"] == {}
  assert payload["trade_count"] == 0
  assert payload["since"] is None


def test_future_oos_end_returns_empty(client, db):
  # opt_end tomorrow → oos_end ~31 days in the future → nothing to simulate.
  tomorrow = datetime.utcnow() + timedelta(days=1)
  pid = _make_portfolio(db, opt_end=tomorrow)
  r = client.get(f"/api/portfolios/{pid}/post-oos-simulated-metrics")
  assert r.status_code == 200
  payload = r.json()
  assert payload["metrics"] == {}
  assert payload["trade_count"] == 0


def test_past_oos_end_no_assets_returns_empty(client, db):
  # opt_end 120 days ago → oos_end 90 days ago → post-OOS window exists,
  # but no active assets → graceful empty.
  past = datetime.utcnow() - timedelta(days=120)
  pid = _make_portfolio(db, opt_end=past, assets=[])
  r = client.get(f"/api/portfolios/{pid}/post-oos-simulated-metrics")
  assert r.status_code == 200
  payload = r.json()
  assert payload["metrics"] == {}
  assert payload["trade_count"] == 0
  assert payload["since"] is not None
  assert payload["until"] is not None


def test_past_oos_end_missing_batch_returns_empty(client, db):
  """Asset references a batch_id that doesn't exist → per-asset loop
  swallows and returns empty; route returns 200, not 500."""
  past = datetime.utcnow() - timedelta(days=120)
  pid = _make_portfolio(
    db, opt_end=past,
    assets=[("BTCUSDT", "Long", 99999)],  # no such batch
  )
  r = client.get(f"/api/portfolios/{pid}/post-oos-simulated-metrics")
  assert r.status_code == 200
  payload = r.json()
  # No trades produced because the batch doesn't exist.
  assert payload["trade_count"] == 0
  assert payload["metrics"] == {}


def test_unknown_portfolio_id_returns_404(client):
  r = client.get("/api/portfolios/999999/post-oos-simulated-metrics")
  assert r.status_code == 404
