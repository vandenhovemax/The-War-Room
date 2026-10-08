"""T-003 Delta 10 regression: the four remaining mandatory tests.

Per ATLAS Section E, T-003 ships with five mandatory regression tests:
  1. test_old_basket_trade_remains_visible_after_rotation     ← here
  2. test_new_basket_trade_appears_only_after_active          ← here
  3. test_no_change_event_preserves_basket                    ← here
  4. test_month_trades_and_monthly_matrix_agree               ← here
  5. test_legacy_stitcher_fallback_only_for_pre_t002_portfolios — Delta 9

What this suite pins:

  The unified read pair (`load_wfas_replay` + `load_wfas_forward_append`,
  routed through `get_portfolio_detail_dataframe`) is the single source
  of truth for hybrid WFAS detail views. This means:

  - Trades from the OLD basket that entered BEFORE a rotation must
    remain visible after the rotation lands (forward path uses
    as-of-time basket, not current basket).
  - Trades in NEW basket symbols that entered BEFORE the basket added
    them must NOT be visible (would be a leak of out-of-basket trades).
  - `phase2_evaluated_no_change` events must preserve the prior basket
    (no symbols added/removed by mistake).
  - `/month-trades` for any (year, month) must return trades whose
    Net Profit sums to the value `/full-metrics-wfo` reports for that
    cell of `monthly_matrix`. Two routes, one ground truth.

Fixture shape:

  - Cutoff (optimization_end_date) = 2026-03-31
  - Committed period (in `selected_records`):
      * BTC entered 2026-02-15 (PnL +300)
      * ETH entered 2026-03-15 (PnL -150)
  - Forward period (raw `BacktestTrade` rows, filtered through
    as-of-time basket via `LiveEvent` segments):
      * ETH 2026-04-02 → before rotation, ETH in basket → INCLUDED (+100)
      * SOL 2026-04-02 → before rotation, SOL not in basket → EXCLUDED
      * ETH 2026-04-10 → after rotation, ETH out of basket → EXCLUDED
      * SOL 2026-04-10 → after rotation, SOL in basket → INCLUDED (+200)
      * BTC 2026-04-25 → after no-change, BTC still in basket → INCLUDED (+50)
  - LiveEvents:
      * 2026-04-05 12:00 UTC (= 08:00 NYT) — hybrid_rotation,
        prior=[BTC,ETH], chosen=[BTC,SOL], removed=[ETH], added=[SOL]
      * 2026-04-20 12:00 UTC (= 08:00 NYT) — phase2_evaluated_no_change,
        prior=[BTC,SOL], chosen=[BTC,SOL]
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.db.database as _db_module
from backend.api.app import create_app
from backend.db.database import get_db
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
  WFORun,  # noqa: F401 — imported so create_all picks up the table
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
  _db_module.engine = _ENGINE
  _db_module.SessionLocal = _Session
  yield
  Base.metadata.drop_all(bind=_ENGINE)


def _override_get_db():
  db = _Session()
  try:
    yield db
  finally:
    db.close()


@pytest.fixture()
def client():
  app = create_app()
  app.dependency_overrides[get_db] = _override_get_db
  with TestClient(app) as c:
    yield c
  app.dependency_overrides.clear()


# ── Fixture data ────────────────────────────────────────────────────

# Cutoff and key timestamps
_CUTOFF_DATE = datetime(2026, 3, 31)

# Committed-period entry times (in selected_records)
_T_BTC_FEB = datetime(2026, 2, 15, 14, 0)
_T_ETH_MAR = datetime(2026, 3, 15, 9, 0)

# Forward-period entry times
_T_ETH_APR2 = datetime(2026, 4, 2, 9, 0)    # before rotation → ETH in basket
_T_ETH_APR10 = datetime(2026, 4, 10, 9, 0)  # after rotation → ETH out
_T_SOL_APR2 = datetime(2026, 4, 2, 9, 0)    # before rotation → SOL not yet
_T_SOL_APR10 = datetime(2026, 4, 10, 9, 0)  # after rotation → SOL in
_T_BTC_APR25 = datetime(2026, 4, 25, 9, 0)  # after no-change → BTC still in

# LiveEvent timestamps (UTC tz-aware so recorder convention matches)
_EVT_ROTATION = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)   # = 08:00 EDT NYT
_EVT_NO_CHANGE = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)  # = 08:00 EDT NYT

# PnL values
_PNL_BTC_FEB = 300.0
_PNL_ETH_MAR = -150.0
_PNL_ETH_APR2 = 100.0
_PNL_ETH_APR10 = 80.0   # this trade is filtered out by basket gate
_PNL_SOL_APR2 = 70.0    # this trade is filtered out by basket gate
_PNL_SOL_APR10 = 200.0
_PNL_BTC_APR25 = 50.0

# Run IDs per (symbol, side) — load_wfas_replay groups by (run_id, dt, symbol, side)
_RUN_BTC = 101
_RUN_ETH = 102
_RUN_SOL = 103


def _basket_key(batch_id: int, symbol: str, side: str = "Long") -> str:
  """Format used by the recorder: `B{bid}_{SYMBOL}_{Side}`."""
  return f"B{batch_id}_{symbol}_{side}"


def _selected_records() -> list[dict]:
  """Committed-period records — entry-bucketed, basket-already-applied."""
  return [
    {"run_id": _RUN_BTC, "dt": _T_BTC_FEB.isoformat(), "symbol": "BTCUSDT",
     "side": "long", "pnl": _PNL_BTC_FEB, "batch_id": 1},
    {"run_id": _RUN_ETH, "dt": _T_ETH_MAR.isoformat(), "symbol": "ETHUSDT",
     "side": "long", "pnl": _PNL_ETH_MAR, "batch_id": 1},
  ]


def _aggregate_monthly_matrix() -> dict:
  """Pre-computed by step2_service in production; here we hand-build it.

  Only covers the committed period (Feb + Mar 2026). The route augments
  with forward-period months (Apr 2026) when has_forward is true.
  """
  return {
    "years": ["2026"],
    "matrix": {"2026": {"2": _PNL_BTC_FEB, "3": _PNL_ETH_MAR}},
    "row_totals": {"2026": _PNL_BTC_FEB + _PNL_ETH_MAR},
    "col_totals": {"2": _PNL_BTC_FEB, "3": _PNL_ETH_MAR},
    "grand_total": _PNL_BTC_FEB + _PNL_ETH_MAR,
  }


def _seed_full_fixture(db) -> int:
  """Seed all rows; return portfolio id. Idempotent if called once per session."""
  if db.query(Portfolio).filter(Portfolio.name == "t003_d10_fixture").first():
    return db.query(Portfolio).filter(Portfolio.name == "t003_d10_fixture").first().id

  # Strategy + batch + runs (one run per symbol so groupby in replay works).
  # Pre-create a BatchGroup with pnl_multiplier=1.0 and pin batch.group_id to
  # it. Otherwise `init_db()` (run by FastAPI lifespan when the test client
  # boots) inserts the production "Group A" with pnl_multiplier=2.0 and
  # auto-assigns every M30 batch to it, doubling forward-period PnLs and
  # breaking the parity assertion below. See seed at backend/db/database.py:489.
  if not db.query(Strategy).filter(Strategy.id == 1).first():
    db.add(Strategy(id=1, name="S", source_type="manual"))
    db.add(BatchGroup(id=99, name="T003_NOSCALE", pnl_multiplier=1.0, timeframe="30"))
    db.flush()
    db.add(BatchBacktestRun(
      id=1, strategy_id=1, timeframe="30", group_id=99,
      direction_filter="long_plus_short", status="completed",
      total_symbols=3, completed_symbols=3,
      parameters=json.dumps({"initial_capital": 10000}),
    ))
    db.flush()
    db.add_all([
      BacktestRun(id=_RUN_BTC, strategy_id=1, symbol="BTCUSDT", timeframe="30",
                  start_date=_T_BTC_FEB - timedelta(days=10),
                  end_date=_T_BTC_APR25 + timedelta(days=10),
                  status="completed", batch_id=1),
      BacktestRun(id=_RUN_ETH, strategy_id=1, symbol="ETHUSDT", timeframe="30",
                  start_date=_T_ETH_MAR - timedelta(days=10),
                  end_date=_T_ETH_APR10 + timedelta(days=10),
                  status="completed", batch_id=1),
      BacktestRun(id=_RUN_SOL, strategy_id=1, symbol="SOLUSDT", timeframe="30",
                  start_date=_T_SOL_APR2 - timedelta(days=60),
                  end_date=_T_SOL_APR10 + timedelta(days=10),
                  status="completed", batch_id=1),
    ])
    db.flush()

  # Trades
  trades = [
    # Committed (in selected_records)
    BacktestTrade(run_id=_RUN_BTC, trade_num=1, position_id="BTC1",
                  symbol="BTCUSDT", side="long",
                  entry_time=_T_BTC_FEB, exit_time=_T_BTC_FEB + timedelta(hours=4),
                  entry_price=50000.0, exit_price=51500.0, quantity=0.01,
                  net_profit=_PNL_BTC_FEB, net_profit_pct=3.0, commission=0.5),
    BacktestTrade(run_id=_RUN_ETH, trade_num=1, position_id="ETHM",
                  symbol="ETHUSDT", side="long",
                  entry_time=_T_ETH_MAR, exit_time=_T_ETH_MAR + timedelta(hours=2),
                  entry_price=3000.0, exit_price=2950.0, quantity=0.5,
                  net_profit=_PNL_ETH_MAR, net_profit_pct=-1.7, commission=0.3),
    # Forward (raw — basket gating applied at read time)
    BacktestTrade(run_id=_RUN_ETH, trade_num=2, position_id="ETH2",
                  symbol="ETHUSDT", side="long",
                  entry_time=_T_ETH_APR2, exit_time=_T_ETH_APR2 + timedelta(hours=3),
                  entry_price=3100.0, exit_price=3170.0, quantity=0.5,
                  net_profit=_PNL_ETH_APR2, net_profit_pct=2.3, commission=0.3),
    BacktestTrade(run_id=_RUN_ETH, trade_num=3, position_id="ETH3",
                  symbol="ETHUSDT", side="long",
                  entry_time=_T_ETH_APR10, exit_time=_T_ETH_APR10 + timedelta(hours=2),
                  entry_price=3200.0, exit_price=3260.0, quantity=0.5,
                  net_profit=_PNL_ETH_APR10, net_profit_pct=1.9, commission=0.3),
    BacktestTrade(run_id=_RUN_SOL, trade_num=1, position_id="SOL1",
                  symbol="SOLUSDT", side="long",
                  entry_time=_T_SOL_APR2, exit_time=_T_SOL_APR2 + timedelta(hours=4),
                  entry_price=120.0, exit_price=125.0, quantity=10.0,
                  net_profit=_PNL_SOL_APR2, net_profit_pct=4.2, commission=0.4),
    BacktestTrade(run_id=_RUN_SOL, trade_num=2, position_id="SOL2",
                  symbol="SOLUSDT", side="long",
                  entry_time=_T_SOL_APR10, exit_time=_T_SOL_APR10 + timedelta(hours=5),
                  entry_price=125.0, exit_price=145.0, quantity=10.0,
                  net_profit=_PNL_SOL_APR10, net_profit_pct=16.0, commission=0.4),
    BacktestTrade(run_id=_RUN_BTC, trade_num=2, position_id="BTC2",
                  symbol="BTCUSDT", side="long",
                  entry_time=_T_BTC_APR25, exit_time=_T_BTC_APR25 + timedelta(hours=3),
                  entry_price=52000.0, exit_price=52250.0, quantity=0.01,
                  net_profit=_PNL_BTC_APR25, net_profit_pct=0.5, commission=0.5),
  ]
  db.add_all(trades)
  db.flush()

  # Portfolio (hybrid + step2 metadata + cutoff)
  settings = {
    "strategy_id": 1, "timeframe": "30",
    "hybrid_mode": True, "oos_months": 2,
    "step2_result": {
      "selected_records": _selected_records(),
      "aggregate_monthly_matrix": _aggregate_monthly_matrix(),
    },
  }
  p = Portfolio(
    name="t003_d10_fixture",
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    portfolio_source="wfo",
    optimization_end_date=_CUTOFF_DATE,
    source_strategies=json.dumps(settings),
  )
  db.add(p)
  db.flush()
  # PortfolioAsset rows: post-rotation basket = BTC + SOL.
  # ETH is set is_active=False (rotated out) — load_wfas_forward_append's
  # `batch_ids` set still picks up trades because batch universe is fixed.
  db.add_all([
    PortfolioAsset(portfolio_id=p.id, symbol="BTCUSDT", trade_side="Long",
                   batch_id=1, is_active=True),
    PortfolioAsset(portfolio_id=p.id, symbol="SOLUSDT", trade_side="Long",
                   batch_id=1, is_active=True),
    PortfolioAsset(portfolio_id=p.id, symbol="ETHUSDT", trade_side="Long",
                   batch_id=1, is_active=False),
  ])

  # LiveEvents: rotation then no-change. `details` carries the prior+chosen
  # baskets that iter_basket_segments reads. created_at is UTC tz-aware so
  # _utc_to_nyt_naive converts correctly to NYT for entry_time comparison.
  initial_basket = [_basket_key(1, "BTCUSDT"), _basket_key(1, "ETHUSDT")]
  rotated_basket = [_basket_key(1, "BTCUSDT"), _basket_key(1, "SOLUSDT")]
  db.add(LiveEvent(
    portfolio_id=p.id,
    event_type="hybrid_rotation",
    symbol="",
    details=json.dumps({
      "prior_basket": sorted(initial_basket),
      "chosen_basket": sorted(rotated_basket),
      "added": [_basket_key(1, "SOLUSDT")],
      "removed": [_basket_key(1, "ETHUSDT")],
      "basket_unchanged": False,
    }),
    created_at=_EVT_ROTATION,
  ))
  db.add(LiveEvent(
    portfolio_id=p.id,
    event_type="phase2_evaluated_no_change",
    symbol="",
    details=json.dumps({
      "prior_basket": sorted(rotated_basket),
      "chosen_basket": sorted(rotated_basket),  # unchanged
      "added": [],
      "removed": [],
      "basket_unchanged": True,
    }),
    created_at=_EVT_NO_CHANGE,
  ))
  db.commit()
  return p.id


def _load_unified_df(pid: int):
  """Helper: re-fetch portfolio and call the routing helper directly.

  Returns the DataFrame the helper produces (committed + forward), which
  is exactly what `/full-metrics-wfo`, `/month-trades`, etc. read.
  """
  from backend.analysis.portfolio_wfo_replay import get_portfolio_detail_dataframe
  db = _Session()
  try:
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    settings = json.loads(p.source_strategies)
    return get_portfolio_detail_dataframe(p, settings, db)
  finally:
    db.close()


def _df_has_trade(df, *, symbol: str, side: str, entry_time: datetime) -> bool:
  """Asset column is `{SYMBOL}_{Side}`, Entry Time is a Timestamp."""
  if df is None or df.empty:
    return False
  asset = f"{symbol}_{side}"
  import pandas as pd
  mask = (
    (df["Asset"] == asset)
    & (pd.to_datetime(df["Entry Time"]) == pd.Timestamp(entry_time))
  )
  return bool(mask.any())


# ── Tests ───────────────────────────────────────────────────────────


def test_old_basket_trade_remains_visible_after_rotation():
  """A trade entered while the OLD basket was active stays visible after
  the rotation lands.

  The bug T-002 fixed: before T-002 the forward-period view filtered
  through `PortfolioAsset.is_active`, so when the rotation flipped
  ETHUSDT to inactive, the April-2 ETH trade disappeared retroactively.
  T-002 fixed this by walking `LiveEvent` segments and assigning each
  trade to its as-of-time basket. T-003 promotes that fix into the
  unified `load_wfas_forward_append` path and routes every detail view
  through it.

  Scenario: ETHUSDT trade entered 2026-04-02; rotation to remove ETH
  fires 2026-04-05. After the rotation, the April-2 ETH trade must
  still appear in the helper's DataFrame.
  """
  db = _Session()
  try:
    pid = _seed_full_fixture(db)
  finally:
    db.close()

  df = _load_unified_df(pid)
  assert df is not None and not df.empty, (
    "helper returned None/empty for hybrid portfolio with step2_result"
  )

  # The April-2 ETH trade MUST be in the DF (entered before rotation,
  # while ETH was still in basket).
  assert _df_has_trade(df, symbol="ETHUSDT", side="Long",
                       entry_time=_T_ETH_APR2), (
    f"April-2 ETH trade missing from DF — old basket member should stay "
    f"visible after rotation. DF rows:\n{df[['Asset', 'Entry Time']].to_string()}"
  )

  # And the April-10 ETH trade must NOT be in the DF (entered after
  # rotation; ETH was no longer in basket — sanity check that the
  # as-of-time gate actually IS gating, not just letting everything through).
  assert not _df_has_trade(df, symbol="ETHUSDT", side="Long",
                           entry_time=_T_ETH_APR10), (
    f"April-10 ETH trade present in DF — should have been filtered out "
    f"by post-rotation basket gate. DF rows:\n{df[['Asset', 'Entry Time']].to_string()}"
  )


def test_new_basket_trade_appears_only_after_active():
  """A trade in a symbol that joins the basket via rotation must NOT
  appear for entries BEFORE the rotation. Symmetric to the old-basket
  test: the as-of-time gate filters in BOTH directions.

  Scenario: SOLUSDT joins the basket at 2026-04-05. The April-2 SOL
  trade must NOT appear (basket didn't include SOL yet); the April-10
  SOL trade MUST appear (post-rotation, SOL in basket).

  Without this, a leak would let pre-active-window trades from a newly-
  added symbol pollute the forward view — incorrect for compliance
  (we never traded SOL on April 2; showing one would be a phantom).
  """
  db = _Session()
  try:
    pid = _seed_full_fixture(db)
  finally:
    db.close()

  df = _load_unified_df(pid)
  assert df is not None and not df.empty

  # Pre-active SOL trade → MUST be absent
  assert not _df_has_trade(df, symbol="SOLUSDT", side="Long",
                           entry_time=_T_SOL_APR2), (
    f"April-2 SOL trade present in DF — SOL wasn't in basket until "
    f"April 5 rotation. Leaking pre-active trades is a phantom-trade bug. "
    f"DF rows:\n{df[['Asset', 'Entry Time']].to_string()}"
  )

  # Post-active SOL trade → MUST be present
  assert _df_has_trade(df, symbol="SOLUSDT", side="Long",
                       entry_time=_T_SOL_APR10), (
    f"April-10 SOL trade missing from DF — SOL joined basket April 5, "
    f"this entry is after that. DF rows:\n{df[['Asset', 'Entry Time']].to_string()}"
  )


def test_no_change_event_preserves_basket():
  """A `phase2_evaluated_no_change` event must NOT alter the active basket.

  T-002 v3 introduced this event type so the recorder always writes a
  daily heartbeat — even when phase 2 produces no changes. The basket
  walker (`iter_basket_segments`) reads the `chosen_basket` field from
  these events and emits a new segment. If the no-change handler ever
  writes a wrong/empty `chosen_basket`, the segment after a no-change
  event would zero out the basket and every subsequent forward trade
  would be filtered out — the symptom would be "live UI goes blank
  after a routine nightly refresh".

  Scenario: April-25 BTCUSDT trade enters AFTER the April-20 no-change
  event. BTC was in the basket before April-20 and the no-change event
  preserves it. So the trade must appear in the DF.
  """
  db = _Session()
  try:
    pid = _seed_full_fixture(db)
  finally:
    db.close()

  df = _load_unified_df(pid)
  assert df is not None and not df.empty

  assert _df_has_trade(df, symbol="BTCUSDT", side="Long",
                       entry_time=_T_BTC_APR25), (
    f"April-25 BTC trade missing from DF — the April-20 no-change event "
    f"should have preserved BTC in the basket. If it's missing, the "
    f"no-change segment is dropping the basket. DF rows:\n"
    f"{df[['Asset', 'Entry Time']].to_string()}"
  )

  # Also pin the segment shape directly: the no-change event's chosen_basket
  # equals the prior segment's chosen_basket. This guards against a future
  # recorder bug that writes an empty `chosen_basket` for no-change events.
  from backend.services.phase2_recorder import iter_basket_segments
  db = _Session()
  try:
    segments = iter_basket_segments(db, pid)
  finally:
    db.close()

  # 3 segments expected: sentinel(prior_basket of first event), rotation, no_change
  assert len(segments) == 3, (
    f"expected 3 segments (sentinel + rotation + no_change), got {len(segments)}: "
    f"{segments}"
  )
  # Last two segments (rotation @ April 5, no_change @ April 20) must
  # have IDENTICAL basket contents — the no-change event preserves.
  rotated_basket = segments[1][1]
  preserved_basket = segments[2][1]
  assert rotated_basket == preserved_basket, (
    f"no-change segment basket {preserved_basket} differs from prior "
    f"rotated basket {rotated_basket} — no-change must preserve."
  )
  assert "B1_BTCUSDT_Long" in preserved_basket, (
    f"BTC missing from preserved basket: {preserved_basket}"
  )
  assert "B1_SOLUSDT_Long" in preserved_basket, (
    f"SOL missing from preserved basket: {preserved_basket}"
  )


def test_month_trades_and_monthly_matrix_agree(client):
  """`/month-trades` and `/full-metrics-wfo` must agree on every month.

  Both routes ultimately read the same DataFrame (via
  `get_portfolio_detail_dataframe` for hybrid). The monthly matrix in
  `/full-metrics-wfo` reports per-cell PnL; `/month-trades` returns
  the individual trades. Their sums MUST agree — a divergence means
  one route is bypassing the unified pair (the original T-003 bug).

  This test exercises the full route stack (FastAPI client) so it
  catches both the helper output AND the wiring layer (route handler
  reads the right column, applies the right filter, returns the right
  shape).
  """
  db = _Session()
  try:
    pid = _seed_full_fixture(db)
  finally:
    db.close()

  # Pull the monthly matrix from /full-metrics-wfo
  r = client.get(f"/api/portfolios/{pid}/full-metrics-wfo")
  assert r.status_code == 200, r.text
  body = r.json()
  assert body.get("mode") == "replay", (
    f"expected replay mode, got {body.get('mode')} — helper not routing"
  )
  matrix = body.get("monthly_matrix") or {}

  # Walk every (year, month) cell and verify /month-trades agrees.
  # Months in our fixture: 2026-02 (BTC committed), 2026-03 (ETH committed),
  # 2026-04 (forward — BTC + SOL only, ETH+SOL excluded by basket gate).
  expected_cells = {
    ("2026", "2"): _PNL_BTC_FEB,
    ("2026", "3"): _PNL_ETH_MAR,
    ("2026", "4"): _PNL_ETH_APR2 + _PNL_SOL_APR10 + _PNL_BTC_APR25,
  }
  for (yr, mo), expected_pnl in expected_cells.items():
    cell = matrix.get(yr, {}).get(mo, {})
    assert cell, (
      f"matrix missing cell {yr}-{mo}: matrix={matrix}"
    )
    actual_pnl = float(cell.get("pnl") or 0.0)
    assert actual_pnl == pytest.approx(expected_pnl, abs=0.01), (
      f"matrix {yr}-{mo} pnl={actual_pnl} != expected {expected_pnl}"
    )

    # Now /month-trades for the same cell — sum of net_profits must match
    r2 = client.get(
      f"/api/portfolios/{pid}/month-trades?year={yr}&month={mo}"
    )
    assert r2.status_code == 200, r2.text
    trades = r2.json().get("trades") or []
    sum_net = sum(float(t.get("net_profit") or 0.0) for t in trades)
    assert sum_net == pytest.approx(expected_pnl, abs=0.01), (
      f"month-trades {yr}-{mo} sum {sum_net} != matrix cell {actual_pnl} "
      f"(expected {expected_pnl}). Trades:\n{trades}"
    )
    # Trade count must also match what the matrix reports.
    cell_trades = int(cell.get("trades") or 0)
    assert len(trades) == cell_trades, (
      f"month-trades {yr}-{mo} returned {len(trades)} trades, "
      f"matrix cell reports {cell_trades}"
    )

  # Negative case: April should have exactly 3 trades (ETH-Apr2, SOL-Apr10,
  # BTC-Apr25). The two basket-gated trades (ETH-Apr10, SOL-Apr2) must NOT
  # leak in. This is the inverse of the matrix-pnl check — same data, but
  # failure mode is "extra rows" instead of "wrong sum".
  r_apr = client.get(f"/api/portfolios/{pid}/month-trades?year=2026&month=4")
  april_trades = r_apr.json().get("trades") or []
  april_assets = sorted(t.get("asset") for t in april_trades)
  assert april_assets == ["BTCUSDT_Long", "ETHUSDT_Long", "SOLUSDT_Long"], (
    f"April trades returned wrong assets: {april_assets}. Expected exactly "
    f"BTC + ETH + SOL (one each, basket-gated). If you see two ETH or two "
    f"SOL rows, the basket gate is leaking."
  )
