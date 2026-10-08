"""Ship 1 Test 4 — Non-hybrid portfolio freeze invariant.

Purpose:
  Enforce the Safety Contract from the Live Loop plan:
      Non-hybrid portfolios are IMMUTABLE within their OOS window.
      No nightly service is allowed to write to PortfolioAsset or
      KellyAllocation for a non_hybrid portfolio.

  The only legal writes for non_hybrid are:
    1. A human-approved RebalanceProposal at the OOS boundary (Ship 2).
    2. Never the nightly hybrid refresh.

Guarded code path:
  backend/services/hybrid_portfolio_refresh.py
    - _hybrid_portfolios()  — must filter out mode='non_hybrid'
    - refresh_portfolio()   — must early-return 'not_hybrid' when called
                              directly against a non_hybrid id.

Test strategy:
  1. Seed an in-memory SQLite with one non_hybrid portfolio + basket.
  2. Snapshot PortfolioAsset rows + KellyAllocation count + LiveEvent
     count BEFORE running the nightly service.
  3. Run refresh_all_hybrid_portfolios() pointed at the in-memory DB.
  4. Snapshot AFTER — every field we snapshotted must be byte-identical.
  5. Also assert that calling refresh_portfolio(pid) directly on a
     non_hybrid portfolio returns reason='not_hybrid' without mutating.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
  Base,
  KellyAllocation,
  LiveEvent,
  Portfolio,
  PortfolioAsset,
)

# ── In-memory SQLite fixture ──────────────────────────────────────

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


@pytest.fixture(scope="module", autouse=True)
def _setup_schema():
  Base.metadata.create_all(bind=_ENGINE)
  yield
  Base.metadata.drop_all(bind=_ENGINE)


@pytest.fixture()
def db():
  s = _Session()
  try:
    yield s
  finally:
    s.rollback()
    s.close()


# ── Helpers ────────────────────────────────────────────────────────

def _seed_non_hybrid(db) -> int:
  """Create one non_hybrid portfolio with 3 basket assets."""
  p = Portfolio(
    name="freeze_test_non_hybrid",
    portfolio_type="paper",
    mode="non_hybrid",
    is_active=True,
    optimization_end_date=datetime(2024, 6, 1),
    source_strategies=json.dumps({
      "strategy_id": 1,
      "timeframe": "30",
      "hybrid_mode": False,  # matches the mode column
    }),
  )
  db.add(p)
  db.flush()
  for sym, side, bid in [
    ("BTCUSDT", "long", 1),
    ("ETHUSDT", "long", 1),
    ("SOLUSDT", "short", 2),
  ]:
    db.add(PortfolioAsset(
      portfolio_id=p.id, symbol=sym, trade_side=side,
      batch_id=bid, is_active=True,
    ))
  db.commit()
  return p.id


def _snapshot_state(db, portfolio_id: int) -> dict:
  assets = (
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == portfolio_id)
    .order_by(PortfolioAsset.id)
    .all()
  )
  return {
    "asset_tuples": [
      (a.symbol, a.trade_side, a.batch_id, a.is_active) for a in assets
    ],
    "kelly_count": db.query(KellyAllocation).filter(
      KellyAllocation.portfolio_id == portfolio_id,
    ).count(),
    "event_count": db.query(LiveEvent).filter(
      LiveEvent.portfolio_id == portfolio_id,
    ).count(),
  }


# ── Tests ──────────────────────────────────────────────────────────

def test_refresh_all_skips_non_hybrid(db, monkeypatch):
  """refresh_all_hybrid_portfolios() must not touch non_hybrid rows."""
  pid = _seed_non_hybrid(db)
  before = _snapshot_state(db, pid)

  # Route the service's SessionLocal() call into our in-memory engine.
  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  report = svc.refresh_all_hybrid_portfolios()

  # The report should simply show 0 hybrid portfolios processed.
  assert report["total_hybrid"] == 0
  assert report["ok"] == 0
  assert report["failed"] == 0
  assert report["results"] == []

  after = _snapshot_state(db, pid)
  assert before == after, (
    "non_hybrid portfolio was mutated by the nightly service — "
    "Safety Contract violation."
  )


def test_refresh_portfolio_direct_call_rejects_non_hybrid(db, monkeypatch):
  """Even a direct call to refresh_portfolio(pid) must no-op on non_hybrid."""
  pid = _seed_non_hybrid(db)
  before = _snapshot_state(db, pid)

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  result = svc.refresh_portfolio(pid)

  assert result["ok"] is False
  assert result["reason"] == "not_hybrid"
  assert result["portfolio_id"] == pid

  after = _snapshot_state(db, pid)
  assert before == after, (
    "refresh_portfolio() mutated a non_hybrid portfolio despite "
    "returning 'not_hybrid' — that's even worse than violating the "
    "safety contract."
  )


def test_hybrid_portfolios_query_filters_by_mode(db):
  """_hybrid_portfolios() must return only mode='hybrid' rows."""
  # seed one non_hybrid
  pid_nh = _seed_non_hybrid(db)

  # seed one hybrid
  ph = Portfolio(
    name="freeze_test_hybrid_sibling",
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    optimization_end_date=datetime(2024, 6, 1),
    source_strategies=json.dumps({"strategy_id": 1, "timeframe": "30"}),
  )
  db.add(ph)
  db.commit()

  from backend.services.hybrid_portfolio_refresh import _hybrid_portfolios
  rows = _hybrid_portfolios(db)
  ids = {p.id for p in rows}
  assert ph.id in ids, "hybrid portfolio missing from query"
  assert pid_nh not in ids, (
    "non_hybrid portfolio leaked into _hybrid_portfolios() — "
    "Safety Contract violation at the query boundary."
  )


def test_mode_column_default_is_non_hybrid(db):
  """Portfolios created without an explicit mode must default to non_hybrid.

  This is the single most important default: any legacy or hand-crafted
  portfolio row automatically gets the frozen behavior.
  """
  p = Portfolio(
    name="freeze_test_default_mode",
    portfolio_type="saved",
    source_strategies=json.dumps({"strategy_id": 1, "timeframe": "30"}),
  )
  db.add(p)
  db.commit()
  db.refresh(p)
  assert p.mode == "non_hybrid", (
    f"Portfolio.mode defaulted to {p.mode!r} — must be 'non_hybrid' "
    "to preserve the freeze invariant for legacy rows."
  )
