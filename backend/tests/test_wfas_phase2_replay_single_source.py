"""Regression: WFAS Phase-2 replay is the single source of truth.

Guards the fix that every portfolio view (monthly grid, modal, full metrics)
reads from `step2_result.selected_records` + `aggregate_monthly_matrix`
instead of the legacy batch-query stitchers.

What we prove:
  1. `/full-metrics-wfo` returns `mode="replay"` when step2_result is present.
  2. The returned `monthly_matrix` equals the stored `aggregate_monthly_matrix`
     (same months, same PnL) — no re-bucketing, no scaling drift.
  3. `/month-trades` picks the same entries the matrix attributes to that
     month (entry-date bucketed) when replay metadata is present.
  4. `has_wfo_replay_metadata()` flips to True once selected_records exist.
  5. The backfill endpoint merges step2_result from a WFORun into the portfolio.
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
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  Portfolio,
  PortfolioAsset,
  Strategy,
  WFORun,
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


def _override_get_db():
  db = _Session()
  try:
    yield db
  finally:
    db.close()


@pytest.fixture(scope="module", autouse=True)
def _schema():
  Base.metadata.create_all(bind=_ENGINE)
  # Swap the module-level engine so services that call SessionLocal hit our in-memory DB.
  _db_module.engine = _ENGINE
  _db_module.SessionLocal = _Session
  yield
  Base.metadata.drop_all(bind=_ENGINE)


@pytest.fixture()
def client():
  app = create_app()
  app.dependency_overrides[get_db] = _override_get_db
  with TestClient(app) as c:
    yield c
  app.dependency_overrides.clear()


# ── Seed ─────────────────────────────────────────────────────────

# Three canonical trades: one in March (losing), one in March (winning),
# one in April (losing). Phase-2 attributes each trade to its ENTRY month.
# The aggregate_monthly_matrix bakes that in — the test asserts the
# endpoint returns the exact same shape.
_T1 = datetime(2026, 3, 10, 14, 0)   # March entry → exits same month
_T2 = datetime(2026, 3, 25, 9, 0)    # March entry → exits in April
_T3 = datetime(2026, 4, 5, 10, 0)    # April entry

_MARCH_PNL = -200.0 + 500.0   # = 300 net, 2 trades, 50% WR
_APRIL_PNL = -80.0            # 1 trade, 0% WR


def _seed(db):
  if db.query(Strategy).filter(Strategy.id == 1).first() is not None:
    return
  db.add(Strategy(id=1, name="S", source_type="manual"))
  db.add(BatchBacktestRun(
    id=1, strategy_id=1, timeframe="30",
    direction_filter="long_plus_short", status="completed",
    total_symbols=1, completed_symbols=1,
    parameters=json.dumps({"initial_capital": 10000}),
  ))
  db.flush()
  db.add(BacktestRun(
    id=101, strategy_id=1, symbol="BTCUSDT", timeframe="30",
    start_date=_T1 - timedelta(days=5),
    end_date=_T3 + timedelta(days=10),
    status="completed", batch_id=1,
  ))
  db.flush()
  db.add_all([
    BacktestTrade(
      run_id=101, trade_num=1, position_id="P1",
      symbol="BTCUSDT", side="long",
      entry_time=_T1, exit_time=_T1 + timedelta(hours=3),
      entry_price=1000.0, exit_price=990.0, quantity=0.1,
      net_profit=-200.0, net_profit_pct=-2.0, commission=0.1,
    ),
    # Entered March 25, exits April 2 — proves entry-bucketing vs exit-bucketing.
    BacktestTrade(
      run_id=101, trade_num=2, position_id="P2",
      symbol="BTCUSDT", side="long",
      entry_time=_T2, exit_time=datetime(2026, 4, 2, 10, 0),
      entry_price=1000.0, exit_price=1050.0, quantity=0.1,
      net_profit=500.0, net_profit_pct=5.0, commission=0.1,
    ),
    BacktestTrade(
      run_id=101, trade_num=3, position_id="P3",
      symbol="BTCUSDT", side="long",
      entry_time=_T3, exit_time=_T3 + timedelta(hours=5),
      entry_price=1000.0, exit_price=992.0, quantity=0.1,
      net_profit=-80.0, net_profit_pct=-0.8, commission=0.1,
    ),
  ])
  db.commit()


def _selected_records() -> list[dict]:
  """Shape matches step2_service output: run_id, dt, symbol, side, pnl, batch_id."""
  return [
    {"run_id": 101, "dt": _T1.isoformat(), "symbol": "BTCUSDT",
     "side": "long", "pnl": -200.0, "batch_id": 1},
    {"run_id": 101, "dt": _T2.isoformat(), "symbol": "BTCUSDT",
     "side": "long", "pnl": 500.0, "batch_id": 1},
    {"run_id": 101, "dt": _T3.isoformat(), "symbol": "BTCUSDT",
     "side": "long", "pnl": -80.0, "batch_id": 1},
  ]


def _aggregate_monthly_matrix() -> dict:
  """Entry-bucketed grid. March = +300, April = -80.

  Shape matches backend/analysis/step2_service.py:382 — numeric-month-as-string
  keys, plain float PnL values, float row/col/grand totals.
  """
  return {
    "years": ["2026"],
    "matrix": {"2026": {"3": _MARCH_PNL, "4": _APRIL_PNL}},
    "row_totals": {"2026": _MARCH_PNL + _APRIL_PNL},
    "col_totals": {"3": _MARCH_PNL, "4": _APRIL_PNL},
    "grand_total": _MARCH_PNL + _APRIL_PNL,
  }


def _make_phase2_portfolio(db, include_step2: bool) -> int:
  p = Portfolio(
    name="wfas_test",
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    portfolio_source="wfo",
    optimization_end_date=datetime(2026, 5, 1),
    source_strategies=json.dumps({
      "strategy_id": 1, "timeframe": "30",
      "hybrid_mode": True, "oos_months": 2,
      **({"step2_result": {
        "selected_records": _selected_records(),
        "aggregate_monthly_matrix": _aggregate_monthly_matrix(),
      }} if include_step2 else {}),
    }),
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="long",
    batch_id=1, is_active=True,
  ))
  db.commit()
  return p.id


# ── Tests ────────────────────────────────────────────────────────

def test_has_wfo_replay_metadata_flips_on_selected_records():
  from backend.analysis.portfolio_wfo_replay import has_wfo_replay_metadata
  assert has_wfo_replay_metadata({}) is False
  assert has_wfo_replay_metadata({"step2_result": {}}) is False
  assert has_wfo_replay_metadata(
    {"step2_result": {"selected_records": _selected_records()}}
  ) is True


def test_full_metrics_wfo_returns_replay_mode_and_stored_matrix(client):
  """/full-metrics-wfo reads from stored matrix when step2_result present."""
  db = _Session()
  try:
    _seed(db)
    pid = _make_phase2_portfolio(db, include_step2=True)
  finally:
    db.close()

  r = client.get(f"/api/portfolios/{pid}/full-metrics-wfo")
  assert r.status_code == 200, r.text
  body = r.json()

  assert body.get("mode") == "replay", f"expected replay mode, got {body.get('mode')}"
  assert body.get("optimization_end_date"), "optimization_end_date must propagate"

  # Route enriches the stored matrix with trade/win counts from the replay df.
  mm = body.get("monthly_matrix") or {}
  # The route returns monthly_matrix as a flat {year: {month_num_str: {pnl,trades,win_rate}}}
  cells = mm.get("2026") or {}
  mar = cells.get("3") or {}
  apr = cells.get("4") or {}
  assert mar.get("pnl") == pytest.approx(_MARCH_PNL)
  assert apr.get("pnl") == pytest.approx(_APRIL_PNL)
  assert mar.get("trades") == 2, f"March should have 2 entries, got {mar}"
  assert apr.get("trades") == 1, f"April should have 1 entry, got {apr}"


def test_month_trades_uses_entry_time_when_replay_present(client):
  """The T2 trade enters March 25, exits April 2. It MUST show in March."""
  db = _Session()
  try:
    _seed(db)
    pid = _make_phase2_portfolio(db, include_step2=True)
  finally:
    db.close()

  r = client.get(f"/api/portfolios/{pid}/month-trades?year=2026&month=3")
  assert r.status_code == 200, r.text
  body = r.json()
  trades = body.get("trades") or []
  # March has 2 entries (T1 and T2) even though T2's exit is in April.
  assert len(trades) == 2, f"expected 2 March entries, got {len(trades)}: {trades}"


def test_backfill_endpoint_merges_step2_from_wfo_run(client):
  """POST /portfolios/{id}/backfill-wfas-step2 copies artifacts into the portfolio."""
  db = _Session()
  try:
    _seed(db)
    pid = _make_phase2_portfolio(db, include_step2=False)
    # Stash step2_result on a WFORun so the backfill endpoint can read it.
    wfo = WFORun(
      batch_id=1,
      batch_ids=json.dumps([1]),
      is_months=6,
      oos_months=2,
      last_oos_end="2026-05-01",
      optimizer_settings=json.dumps({
        "step2_result": {
          "selected_records": _selected_records(),
          "aggregate_monthly_matrix": _aggregate_monthly_matrix(),
        },
      }),
      status="completed",
    )
    db.add(wfo)
    db.commit()
    wfo_run_id = wfo.id
  finally:
    db.close()

  r = client.post(
    f"/api/portfolios/{pid}/backfill-wfas-step2",
    params={"wfo_run_id": wfo_run_id},
  )
  assert r.status_code == 200, r.text
  body = r.json()
  assert body.get("ok") is True
  assert body.get("records_copied") == 3
  assert body.get("has_monthly_matrix") is True

  # Reload the portfolio and verify step2_result is now present.
  db2 = _Session()
  try:
    p = db2.query(Portfolio).filter(Portfolio.id == pid).first()
    settings = json.loads(p.source_strategies)
    step2 = settings.get("step2_result") or {}
    assert len(step2.get("selected_records") or []) == 3
    assert step2.get("aggregate_monthly_matrix")
  finally:
    db2.close()

  # After backfill, /full-metrics-wfo should now return replay mode.
  r2 = client.get(f"/api/portfolios/{pid}/full-metrics-wfo")
  assert r2.status_code == 200
  assert r2.json().get("mode") == "replay"
