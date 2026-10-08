"""A3 — Recovery script for portfolios damaged by the pre-A3 overwrite bug.

Tests cover:
  R1. Dry-run does not mutate the portfolio.
  R2. Live run restores save-time step2_result from wfo_runs.optimizer_settings.
  R3. Live run does NOT touch refresh_step2_result (additive-only contract).
  R4. Idempotent — running twice produces the same state.
  R5. --run-ids override resolves source WFORun when name doesn't match "Run <N>".
  R6. Empty selected_records in source → RecoveryError (fail-loud, don't silently wipe).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, Portfolio, WFORun


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
def _schema():
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
    # Tests commit — wipe rows between tests so each starts clean.
    s.query(Portfolio).delete()
    s.query(WFORun).delete()
    s.commit()
    s.close()


# ── Fixtures ─────────────────────────────────────────────────────────

def _full_step2_blob() -> dict:
  """Realistic save-time step2_result — multi-year matrix + 3 records."""
  return {
    "selected_records": [
      {"run_id": 1, "dt": "2022-06-01", "symbol": "BTCUSDT", "side": "long",  "scaled_pnl": 10.0},
      {"run_id": 1, "dt": "2023-03-15", "symbol": "ETHUSDT", "side": "long",  "scaled_pnl": 20.0},
      {"run_id": 1, "dt": "2024-01-10", "symbol": "BTCUSDT", "side": "short", "scaled_pnl": -5.0},
    ],
    "aggregate_monthly_matrix": {"matrix": {
      "2022": {"6": 10.0},
      "2023": {"3": 20.0},
      "2024": {"1": -5.0},
    }},
    "aggregate_equity_curve": [{"dt": "2022-06-01", "equity": 10000}],
    "aggregate_breakdown": {"by_symbol": {"BTCUSDT": 5.0, "ETHUSDT": 20.0}},
    "cumulative_metrics": {"net_profit": 25.0, "trades": 3},
  }


def _damaged_step2_blob() -> dict:
  """Single-record, single-month output — the post-bug state on P23/P24."""
  return {
    "selected_records": [
      {"run_id": 99, "dt": "2026-04-05", "symbol": "LUMIA", "side": "long", "scaled_pnl": 50.0},
    ],
    "aggregate_monthly_matrix": {"matrix": {"2026": {"4": 50.0}}},
  }


def _seed_wfo_run(db, run_id: int, step2: dict | None) -> None:
  opt_settings = {"step2_result": step2} if step2 is not None else {}
  db.add(WFORun(
    id=run_id, batch_id=1, is_months=12, oos_months=2,
    last_oos_end="2024-05-01", status="completed",
    optimizer_settings=json.dumps(opt_settings),
  ))
  db.commit()


def _seed_damaged_portfolio(db, name: str, refresh_present: bool = False) -> int:
  src = {
    "strategy_id": 1,
    "timeframe": "30",
    "hybrid_mode": True,
    "step2_result": _damaged_step2_blob(),
  }
  if refresh_present:
    src["refresh_step2_result"] = {
      "selected_records": [
        {"run_id": 2, "dt": "2026-04-20", "symbol": "YGG", "side": "short", "scaled_pnl": 30.0},
      ],
      "aggregate_monthly_matrix": {"matrix": {"2026": {"4": 80.0}}},
      "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
  p = Portfolio(
    name=name,
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    source_strategies=json.dumps(src),
  )
  db.add(p)
  db.commit()
  return p.id


# ── R1. Dry-run is a no-op ────────────────────────────────────────────

def test_dry_run_does_not_mutate_portfolio(db):
  from backend.scripts.recover_portfolio_step2 import recover_one

  _seed_wfo_run(db, run_id=366, step2=_full_step2_blob())
  pid = _seed_damaged_portfolio(db, name="Run 366")

  before = db.query(Portfolio).get(pid).source_strategies

  report = recover_one(db, pid, run_override=None, dry_run=True)

  db.expire_all()
  after = db.query(Portfolio).get(pid).source_strategies
  assert after == before, "dry-run must not mutate source_strategies"
  assert report["committed"] is False
  assert report["dry_run"] is True
  assert report["current_records"] == 1
  assert report["restored_records"] == 3
  assert report["current_years"] == ["2026"]
  assert report["restored_years"] == ["2022", "2023", "2024"]
  assert report["source_wfo_run_id"] == 366


# ── R2. Live run restores save-time blob ──────────────────────────────

def test_live_run_restores_step2_result(db):
  from backend.scripts.recover_portfolio_step2 import recover_one

  _seed_wfo_run(db, run_id=366, step2=_full_step2_blob())
  pid = _seed_damaged_portfolio(db, name="Run 366")

  report = recover_one(db, pid, run_override=None, dry_run=False)
  assert report["committed"] is True

  db.expire_all()
  src = json.loads(db.query(Portfolio).get(pid).source_strategies)
  restored = src["step2_result"]

  assert len(restored["selected_records"]) == 3
  assert set(restored["aggregate_monthly_matrix"]["matrix"].keys()) == {"2022", "2023", "2024"}
  assert restored["cumulative_metrics"] == {"net_profit": 25.0, "trades": 3}
  assert restored["aggregate_breakdown"] == {"by_symbol": {"BTCUSDT": 5.0, "ETHUSDT": 20.0}}


# ── R3. Refresh field is untouched (additive-only) ────────────────────

def test_live_run_does_not_touch_refresh_step2_result(db):
  from backend.scripts.recover_portfolio_step2 import recover_one

  _seed_wfo_run(db, run_id=366, step2=_full_step2_blob())
  pid = _seed_damaged_portfolio(db, name="Run 366", refresh_present=True)

  before_src = json.loads(db.query(Portfolio).get(pid).source_strategies)
  refresh_before = before_src["refresh_step2_result"]

  recover_one(db, pid, run_override=None, dry_run=False)

  db.expire_all()
  after_src = json.loads(db.query(Portfolio).get(pid).source_strategies)
  assert after_src["refresh_step2_result"] == refresh_before, (
    "recovery must NOT touch refresh_step2_result"
  )


# ── R4. Idempotent — two runs produce identical state ────────────────

def test_recovery_is_idempotent(db):
  from backend.scripts.recover_portfolio_step2 import recover_one

  _seed_wfo_run(db, run_id=366, step2=_full_step2_blob())
  pid = _seed_damaged_portfolio(db, name="Run 366")

  recover_one(db, pid, run_override=None, dry_run=False)
  db.expire_all()
  first = json.loads(db.query(Portfolio).get(pid).source_strategies)

  recover_one(db, pid, run_override=None, dry_run=False)
  db.expire_all()
  second = json.loads(db.query(Portfolio).get(pid).source_strategies)

  assert first == second, "second recovery must produce identical state"


# ── R5. --run-ids override ────────────────────────────────────────────

def test_run_id_override_resolves_when_name_does_not_match(db):
  from backend.scripts.recover_portfolio_step2 import recover_one, RecoveryError

  _seed_wfo_run(db, run_id=366, step2=_full_step2_blob())
  pid = _seed_damaged_portfolio(db, name="My Renamed Portfolio")

  # Without override: fails loud — never silently guess.
  with pytest.raises(RecoveryError, match="does not match 'Run <N>'"):
    recover_one(db, pid, run_override=None, dry_run=True)

  # With override: resolves correctly.
  report = recover_one(db, pid, run_override=366, dry_run=False)
  assert report["source_wfo_run_id"] == 366
  assert report["committed"] is True

  db.expire_all()
  src = json.loads(db.query(Portfolio).get(pid).source_strategies)
  assert len(src["step2_result"]["selected_records"]) == 3


# ── R6. Empty source → fail loud ─────────────────────────────────────

def test_empty_selected_records_in_source_raises(db):
  from backend.scripts.recover_portfolio_step2 import recover_one, RecoveryError

  empty_step2 = {
    "selected_records": [],
    "aggregate_monthly_matrix": {"matrix": {}},
  }
  _seed_wfo_run(db, run_id=500, step2=empty_step2)
  pid = _seed_damaged_portfolio(db, name="Run 500")

  with pytest.raises(RecoveryError, match="selected_records is empty"):
    recover_one(db, pid, run_override=None, dry_run=False)

  # Portfolio untouched on failure.
  db.expire_all()
  src = json.loads(db.query(Portfolio).get(pid).source_strategies)
  assert len(src["step2_result"]["selected_records"]) == 1, "damaged state must persist on failure"


def test_missing_step2_in_wfo_run_raises(db):
  from backend.scripts.recover_portfolio_step2 import recover_one, RecoveryError

  _seed_wfo_run(db, run_id=501, step2=None)
  pid = _seed_damaged_portfolio(db, name="Run 501")

  with pytest.raises(RecoveryError, match="is not a dict"):
    recover_one(db, pid, run_override=None, dry_run=True)


def test_nonexistent_portfolio_raises(db):
  from backend.scripts.recover_portfolio_step2 import recover_one, RecoveryError

  with pytest.raises(RecoveryError, match="Portfolio 99999 not found"):
    recover_one(db, 99999, run_override=None, dry_run=True)


def test_nonexistent_wfo_run_raises(db):
  from backend.scripts.recover_portfolio_step2 import recover_one, RecoveryError

  pid = _seed_damaged_portfolio(db, name="Run 9999")
  with pytest.raises(RecoveryError, match="WFORun 9999 .* not found"):
    recover_one(db, pid, run_override=None, dry_run=True)
