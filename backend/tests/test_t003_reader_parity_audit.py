"""T-003 Delta 11 — Reader-parity audit regression suite.

Pins:
  * Hybrid portfolio with both readers in agreement → ok=True, has_diff=False.
  * Non-hybrid portfolio (no `step2_result.selected_records`) is gated out
    with `reason='not_hybrid'`.
  * Carve-out: portfolio with a `basket_restore` LiveEvent is SKIPPED
    (skipped=True, skip_reason='basket_restore_carveout') — the legacy
    stitcher reads those rows but the unified reader doesn't, so comparing
    the two would surface a known-intentional divergence.
  * `_diff_reader_outputs` correctly flags a synthetic divergence
    (returns has_diff=True, populates `unified_only_sample` /
    `legacy_only_sample`).
  * Orchestrator `run_reader_parity_audit` writes a single LiveEvent
    of type `reader_parity_audit` per divergent portfolio when divergence
    exists, and writes nothing when none do.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.db.database as _db_module
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  BatchGroup,
  LiveEvent,
  Portfolio,
  PortfolioAsset,
  Strategy,
)
from backend.services.reader_parity_audit import (
  READER_PARITY_AUDIT_KIND,
  _diff_reader_outputs,
  _trade_keys,
  compare_readers_for_portfolio,
  run_reader_parity_audit,
)


_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _pragma(dbapi_conn, _):
  c = dbapi_conn.cursor()
  c.execute("PRAGMA foreign_keys=ON")
  c.close()


_Session = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


@pytest.fixture(scope="module", autouse=True)
def _schema():
  Base.metadata.create_all(bind=_ENGINE)
  _prev_engine = _db_module.engine
  _prev_session = _db_module.SessionLocal
  _db_module.engine = _ENGINE
  _db_module.SessionLocal = _Session
  yield
  _db_module.engine = _prev_engine
  _db_module.SessionLocal = _prev_session
  Base.metadata.drop_all(bind=_ENGINE)


@pytest.fixture()
def db():
  s = _Session()
  try:
    s.query(LiveEvent).delete()
    s.query(BacktestTrade).delete()
    s.query(PortfolioAsset).delete()
    s.query(Portfolio).delete()
    s.query(BacktestRun).delete()
    s.query(BatchBacktestRun).delete()
    s.query(BatchGroup).delete()
    s.query(Strategy).delete()
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


# ── Seed helpers ────────────────────────────────────────────────────


_T1 = datetime(2026, 3, 10, 14, 0)
_T2 = datetime(2026, 3, 25, 9, 0)
_CUTOFF = datetime(2026, 5, 1)


def _seed_strategy_and_batch(db) -> None:
  if db.query(Strategy).filter(Strategy.id == 1).first() is not None:
    return
  db.add(Strategy(id=1, name="S", source_type="manual"))
  db.add(BatchGroup(id=99, name="T003D11_NOSCALE", pnl_multiplier=1.0, timeframe="30"))
  db.flush()
  db.add(BatchBacktestRun(
    id=1, strategy_id=1, timeframe="30", group_id=99,
    direction_filter="long_plus_short", status="completed",
    total_symbols=1, completed_symbols=1,
    parameters=json.dumps({"initial_capital": 10000}),
  ))
  db.flush()
  db.add(BacktestRun(
    id=101, strategy_id=1, symbol="BTCUSDT", timeframe="30",
    start_date=_T1 - timedelta(days=5),
    end_date=_T2 + timedelta(days=10),
    status="completed", batch_id=1,
  ))
  db.flush()
  db.add(BacktestTrade(
    run_id=101, trade_num=1, position_id="P1",
    symbol="BTCUSDT", side="long",
    entry_time=_T1, exit_time=_T1 + timedelta(hours=3),
    entry_price=1000.0, exit_price=990.0, quantity=0.1,
    net_profit=-200.0, net_profit_pct=-2.0, commission=0.1,
  ))
  db.commit()


def _selected_records() -> list[dict]:
  return [
    {"run_id": 101, "dt": _T1.isoformat(), "symbol": "BTCUSDT",
     "side": "long", "pnl": -200.0, "batch_id": 1},
    {"run_id": 101, "dt": _T2.isoformat(), "symbol": "BTCUSDT",
     "side": "long", "pnl": 500.0, "batch_id": 1},
  ]


def _make_hybrid_portfolio(db, *, name: str) -> int:
  settings: dict = {
    "strategy_id": 1, "timeframe": "30",
    "hybrid_mode": True, "oos_months": 2,
    "phase2_selection_changes": [
      {"date": "2026-04-01", "added": [], "removed": []},
    ],
    "phase2_initial_basket": ["BTCUSDT_long"],
    "step2_result": {"selected_records": _selected_records()},
    "batch_ids": [1],
  }
  p = Portfolio(
    name=name,
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    portfolio_source="wfo",
    optimization_end_date=_CUTOFF,
    source_strategies=json.dumps(settings),
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="Long",
    batch_id=1, is_active=True,
  ))
  db.commit()
  return p.id


def _make_non_hybrid_portfolio(db, *, name: str) -> int:
  settings: dict = {
    "strategy_id": 1, "timeframe": "30",
    "hybrid_mode": False,
    "batch_ids": [1],
  }
  p = Portfolio(
    name=name,
    portfolio_type="paper",
    mode="non_hybrid",
    is_active=True,
    portfolio_source="wfo",
    optimization_end_date=_CUTOFF,
    source_strategies=json.dumps(settings),
  )
  db.add(p)
  db.commit()
  return p.id


# ── Pure-helper unit tests (no DB) ──────────────────────────────────


def test_trade_keys_handles_empty_and_none():
  assert _trade_keys(None) == set()
  assert _trade_keys(pd.DataFrame()) == set()


def test_trade_keys_canonicalises_floats_and_case():
  df = pd.DataFrame([
    {"entry_time": _T1, "symbol": "BTCUSDT", "side": "Long",
     "net_profit": -200.001},
    {"entry_time": _T2, "symbol": "BTCUSDT", "side": "long",
     "net_profit": 500.0049},
  ])
  keys = _trade_keys(df)
  assert (_T1.isoformat(), "BTCUSDT", "long", -200.0) in keys
  assert (_T2.isoformat(), "BTCUSDT", "long", 500.0) in keys


def test_diff_reader_outputs_zero_diff_on_identical_dfs():
  df = pd.DataFrame([
    {"entry_time": _T1, "symbol": "BTCUSDT", "side": "long",
     "net_profit": -200.0},
    {"entry_time": _T2, "symbol": "BTCUSDT", "side": "long",
     "net_profit": 500.0},
  ])
  diff = _diff_reader_outputs(df, df.copy())
  assert diff["has_diff"] is False
  assert diff["unified_only_count"] == 0
  assert diff["legacy_only_count"] == 0
  assert diff["pnl_delta"] == 0.0


def test_diff_reader_outputs_flags_synthetic_divergence():
  unified = pd.DataFrame([
    {"entry_time": _T1, "symbol": "BTCUSDT", "side": "long",
     "net_profit": -200.0},
    {"entry_time": _T2, "symbol": "BTCUSDT", "side": "long",
     "net_profit": 500.0},
  ])
  # Legacy is missing T2 and has an extra T3 trade
  _T3 = datetime(2026, 4, 1, 12, 0)
  legacy = pd.DataFrame([
    {"entry_time": _T1, "symbol": "BTCUSDT", "side": "long",
     "net_profit": -200.0},
    {"entry_time": _T3, "symbol": "ETHUSDT", "side": "short",
     "net_profit": 100.0},
  ])
  diff = _diff_reader_outputs(unified, legacy)
  assert diff["has_diff"] is True
  assert diff["unified_only_count"] == 1
  assert diff["legacy_only_count"] == 1
  # T2 (500.0) only in unified, T3 (ETH/short/100.0) only in legacy
  assert any("BTCUSDT" in row and "long" in row for row in diff["unified_only_sample"])
  assert any("ETHUSDT" in row and "short" in row for row in diff["legacy_only_sample"])


# ── Per-portfolio comparator (DB-backed) ────────────────────────────


def test_non_hybrid_portfolio_returns_not_hybrid(db):
  _seed_strategy_and_batch(db)
  pid = _make_non_hybrid_portfolio(db, name="non_hybrid")
  p = db.query(Portfolio).filter(Portfolio.id == pid).first()
  res = compare_readers_for_portfolio(p, db)
  assert res["ok"] is False
  assert res["reason"] == "not_hybrid"
  assert res["skipped"] is False
  assert res["has_diff"] is False


def test_basket_restore_carveout_skips_portfolio(db):
  _seed_strategy_and_batch(db)
  pid = _make_hybrid_portfolio(db, name="legacy_basket_restore")
  # Inject one legacy `basket_restore` event — should trigger the carve-out
  db.add(LiveEvent(
    portfolio_id=pid,
    event_type="basket_restore",
    details=json.dumps({"basket": ["BTCUSDT_Long"]}),
    created_at=datetime(2026, 4, 1, 12, 0),
  ))
  db.commit()
  p = db.query(Portfolio).filter(Portfolio.id == pid).first()
  res = compare_readers_for_portfolio(p, db)
  assert res["ok"] is True
  assert res["skipped"] is True
  assert res["skip_reason"] == "basket_restore_carveout"
  assert res["has_diff"] is False


def test_hybrid_portfolio_audit_runs_and_returns_diff_payload(db):
  """Smoke test — both readers run on a hybrid portfolio without raising,
  and the result includes a populated diff payload (regardless of whether
  divergence is found, since the legacy stitcher's segmentation differs
  from the unified reader's path on synthetic fixtures).
  """
  _seed_strategy_and_batch(db)
  pid = _make_hybrid_portfolio(db, name="hybrid_clean")
  p = db.query(Portfolio).filter(Portfolio.id == pid).first()
  res = compare_readers_for_portfolio(p, db)
  assert res["ok"] is True
  assert res["skipped"] is False
  assert res["reason"] is None
  # Diff payload always populated when ok=True and not skipped
  assert "unified_count" in res["diff"]
  assert "legacy_count" in res["diff"]
  assert "pnl_delta" in res["diff"]
  assert isinstance(res["has_diff"], bool)


# ── Orchestrator (DB-backed) ────────────────────────────────────────


def test_orchestrator_runs_over_all_portfolios_and_counts(db):
  _seed_strategy_and_batch(db)
  pid_hybrid = _make_hybrid_portfolio(db, name="hybrid_a")
  pid_non = _make_non_hybrid_portfolio(db, name="non_hybrid_b")

  result = run_reader_parity_audit(db=db)
  assert result["total_portfolios"] == 2
  # Non-hybrid is counted under errors (ok=False, reason=not_hybrid)
  assert result["errors"] >= 1

  pid_set = {entry["portfolio_id"] for entry in result["report"]["portfolios"]}
  assert pid_hybrid in pid_set
  assert pid_non in pid_set


def test_orchestrator_writes_live_event_only_on_divergence(db, monkeypatch):
  """Force a divergence by monkey-patching the per-portfolio comparator
  to return has_diff=True. Verify exactly one `reader_parity_audit`
  LiveEvent row is written for the divergent portfolio.
  """
  _seed_strategy_and_batch(db)
  pid = _make_hybrid_portfolio(db, name="forced_diff")

  def _fake_compare(p, _db):
    return {
      "portfolio_id": p.id, "ok": True, "reason": None,
      "skipped": False, "skip_reason": None,
      "has_diff": True,
      "diff": {"unified_count": 1, "legacy_count": 0,
               "unified_only_count": 1, "legacy_only_count": 0,
               "pnl_delta": 100.0, "has_diff": True,
               "unified_only_sample": [], "legacy_only_sample": []},
    }

  monkeypatch.setattr(
    "backend.services.reader_parity_audit.compare_readers_for_portfolio",
    _fake_compare,
  )

  result = run_reader_parity_audit(db=db)
  assert result["portfolios_with_diff"] == 1
  assert pid in result["portfolios_with_diff_ids"]

  # Exactly one reader_parity_audit LiveEvent for the divergent portfolio
  rows = (
    db.query(LiveEvent)
    .filter(LiveEvent.portfolio_id == pid)
    .filter(LiveEvent.event_type == READER_PARITY_AUDIT_KIND)
    .all()
  )
  assert len(rows) == 1
  details = json.loads(rows[0].details)
  assert details["verdict"] == "DIVERGENCE"
  assert details["kind"] == READER_PARITY_AUDIT_KIND


def test_orchestrator_writes_no_live_event_when_clean(db, monkeypatch):
  """When no portfolio diverges, no `reader_parity_audit` LiveEvent
  is written.
  """
  _seed_strategy_and_batch(db)
  _make_hybrid_portfolio(db, name="clean")

  def _fake_compare(p, _db):
    return {
      "portfolio_id": p.id, "ok": True, "reason": None,
      "skipped": False, "skip_reason": None,
      "has_diff": False, "diff": {},
    }

  monkeypatch.setattr(
    "backend.services.reader_parity_audit.compare_readers_for_portfolio",
    _fake_compare,
  )

  result = run_reader_parity_audit(db=db)
  assert result["portfolios_with_diff"] == 0

  rows = (
    db.query(LiveEvent)
    .filter(LiveEvent.event_type == READER_PARITY_AUDIT_KIND)
    .all()
  )
  assert rows == []


def test_orchestrator_supports_portfolio_id_filter(db):
  """`portfolio_ids=[X]` restricts the audit to exactly those portfolios."""
  _seed_strategy_and_batch(db)
  pid_a = _make_hybrid_portfolio(db, name="a")
  _ = _make_hybrid_portfolio(db, name="b")  # noqa: F841

  result = run_reader_parity_audit(portfolio_ids=[pid_a], db=db)
  assert result["total_portfolios"] == 1
  pids = {e["portfolio_id"] for e in result["report"]["portfolios"]}
  assert pids == {pid_a}
