"""CS-006: ``pnl_multiplier`` must scale every trade's ``net_profit``
exactly once, regardless of which group the batch belongs to.

The ``pnl_multiplier`` on ``batch_groups`` is a force-multiplier for
research-sized backtests: if a group's backtests ran at 1 BTC notional
but production trades run at 2.5 BTC, setting the multiplier to 2.5
scales the raw ``net_profit`` so every downstream surface
(WFAS, Kelly, parity canary, cross-strategy, dashboard) sees
production-scale $.

The bug surface: because WR/PF/Sharpe are scale-invariant, a regression
that double-scales (or forgets to scale) is **invisible** on every
metric except absolute $. Live Kelly sizing reads these $ directly —
a miscaling here biases f* and walks straight into production.

Tests pinned:

1. ``scale_pnl=True`` on a group with ``multiplier=2.5`` → every trade's
   ``net_profit`` is exactly 2.5x the stored DB value.
2. ``scale_pnl=False`` → unscaled, always 1:1 with DB.
3. Two groups with different multipliers co-exist → each batch's trades
   are scaled by its own group, not leaked across groups.
4. ``batch_info[bid]["scale_factor"]`` mirrors the applied scale so
   consumers can reason about what was done.
5. A group with ``pnl_multiplier=NULL`` degrades to 1.0 (never raises,
   never silently zeros out PnL).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.analysis.batch_selection import load_trades_lightweight
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  BatchGroup,
  Strategy,
)


# ── Fixture: fresh DB per test so group edits don't leak ─────────────

@pytest.fixture
def db():
  engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
  )
  Base.metadata.create_all(engine)
  Session = sessionmaker(bind=engine)
  session = Session()
  yield session
  session.close()


def _seed_batch(
  session, *, batch_id: int, group_id: int, symbol: str = "BTCUSDT",
  pnls: list[float],
):
  """Seed one batch with one run containing ``len(pnls)`` trades.
  Returns the list of raw net_profit values written to the DB.
  """
  session.add(BatchBacktestRun(
    id=batch_id, strategy_id=1, timeframe="30m",
    direction_filter="long_plus_short", status="completed",
    total_symbols=1, completed_symbols=1, is_deleted=False,
    group_id=group_id,
    parameters=json.dumps({"initial_capital": 10000}),
  ))
  session.flush()

  run_id = batch_id * 100
  session.add(BacktestRun(
    id=run_id, strategy_id=1, symbol=symbol, timeframe="30m",
    start_date=datetime(2024, 1, 1),
    end_date=datetime(2024, 1, 1) + timedelta(days=30),
    status="completed", batch_id=batch_id,
  ))
  session.flush()

  start = datetime(2024, 1, 1)
  for i, pnl in enumerate(pnls):
    session.add(BacktestTrade(
      run_id=run_id, trade_num=i + 1, symbol=symbol, side="long",
      net_profit=pnl, position_id=f"T{run_id}_{i}",
      entry_time=start + timedelta(days=i),
      exit_time=start + timedelta(days=i, hours=2),
      entry_price=50000.0, exit_price=50000.0 + pnl, quantity=1.0,
    ))
  session.commit()
  return pnls


def _setup_strategy(session):
  session.add(Strategy(id=1, name="TestStrat"))
  session.flush()


# ── 1. scale_pnl=True applies multiplier exactly once ────────────────

@pytest.mark.parametrize("multiplier", [0.5, 1.0, 2.0, 2.5])
def test_scale_pnl_true_applies_multiplier_exactly_once(db, multiplier):
  _setup_strategy(db)
  db.add(BatchGroup(id=1, name="G1", pnl_multiplier=multiplier,
                    timeframe="30"))
  db.flush()
  raw_pnls = _seed_batch(db, batch_id=1, group_id=1,
                          pnls=[100.0, -50.0, 75.0])

  trades, rbm, batch_info = load_trades_lightweight(db, scale_pnl=True)

  # One trade per input PnL. net_profit is scaled exactly once.
  got = sorted(t.net_profit for t in trades)
  expected = sorted(p * multiplier for p in raw_pnls)
  assert got == pytest.approx(expected, abs=1e-9), (
    f"CS-006: multiplier={multiplier} — expected PnL {expected}, got {got}. "
    f"Double-scaling or no-scaling would both trip this assertion."
  )
  # batch_info reports what was applied.
  assert batch_info[1]["pnl_multiplier"] == pytest.approx(multiplier)
  assert batch_info[1]["scale_factor"] == pytest.approx(multiplier)


# ── 2. scale_pnl=False never scales, regardless of multiplier ────────

@pytest.mark.parametrize("multiplier", [0.5, 1.0, 2.5])
def test_scale_pnl_false_always_returns_raw_values(db, multiplier):
  _setup_strategy(db)
  db.add(BatchGroup(id=1, name="G1", pnl_multiplier=multiplier,
                    timeframe="30"))
  db.flush()
  raw_pnls = _seed_batch(db, batch_id=1, group_id=1,
                          pnls=[200.0, -100.0])

  trades, _, batch_info = load_trades_lightweight(db, scale_pnl=False)

  got = sorted(t.net_profit for t in trades)
  assert got == pytest.approx(sorted(raw_pnls), abs=1e-9)
  # scale_factor reflects the flag — even when group's multiplier != 1.
  assert batch_info[1]["scale_factor"] == pytest.approx(1.0)
  # pnl_multiplier still reports the group's configured value — the flag
  # only controls *application*, not disclosure.
  assert batch_info[1]["pnl_multiplier"] == pytest.approx(multiplier)


# ── 3. Two groups with different multipliers don't cross-contaminate ─

def test_per_batch_scaling_stays_in_its_group(db):
  _setup_strategy(db)
  db.add(BatchGroup(id=1, name="A", pnl_multiplier=2.0, timeframe="30"))
  db.add(BatchGroup(id=2, name="B", pnl_multiplier=0.5, timeframe="30"))
  db.flush()
  _seed_batch(db, batch_id=1, group_id=1, symbol="BTCUSDT",
               pnls=[100.0])
  _seed_batch(db, batch_id=2, group_id=2, symbol="ETHUSDT",
               pnls=[100.0])

  trades, rbm, batch_info = load_trades_lightweight(db, scale_pnl=True)

  by_symbol = {t.symbol: t.net_profit for t in trades}
  assert by_symbol["BTCUSDT"] == pytest.approx(200.0, abs=1e-9), (
    "CS-006: group A (×2.0) should scale 100 → 200"
  )
  assert by_symbol["ETHUSDT"] == pytest.approx(50.0, abs=1e-9), (
    "CS-006: group B (×0.5) should scale 100 → 50 — a cross-group leak "
    "would produce 200 or some other wrong value"
  )
  assert batch_info[1]["scale_factor"] == pytest.approx(2.0)
  assert batch_info[2]["scale_factor"] == pytest.approx(0.5)


# ── 4. group_names filter preserves scaling ──────────────────────────

def test_group_names_filter_scales_only_included(db):
  _setup_strategy(db)
  db.add(BatchGroup(id=1, name="A", pnl_multiplier=2.0, timeframe="30"))
  db.add(BatchGroup(id=2, name="B", pnl_multiplier=0.5, timeframe="30"))
  db.flush()
  _seed_batch(db, batch_id=1, group_id=1, symbol="BTCUSDT",
               pnls=[100.0, -50.0])
  _seed_batch(db, batch_id=2, group_id=2, symbol="ETHUSDT",
               pnls=[100.0])

  trades_a, _, info_a = load_trades_lightweight(
    db, group_names=["A"], scale_pnl=True,
  )
  # Only A's trades, scaled by A's multiplier.
  assert len(trades_a) == 2
  assert all(t.symbol == "BTCUSDT" for t in trades_a)
  got = sorted(t.net_profit for t in trades_a)
  assert got == pytest.approx([-100.0, 200.0], abs=1e-9)
  # Only batch 1 in batch_info.
  assert set(info_a.keys()) == {1}


# ── 5. NULL multiplier degrades to 1.0, never zero/NaN ───────────────

def test_null_multiplier_falls_back_to_identity(db):
  """If the column was added without a default on an old row, SQLAlchemy
  can yield a Python ``None``. The loader must treat None as 1.0, NOT
  multiply by zero (which would wipe every trade silently).
  """
  _setup_strategy(db)
  grp = BatchGroup(id=1, name="G1", timeframe="30")
  grp.pnl_multiplier = None  # explicitly NULL in DB
  db.add(grp)
  db.flush()
  _seed_batch(db, batch_id=1, group_id=1, pnls=[100.0, -50.0, 75.0])

  trades, _, batch_info = load_trades_lightweight(db, scale_pnl=True)

  got = sorted(t.net_profit for t in trades)
  assert got == pytest.approx([-50.0, 75.0, 100.0], abs=1e-9), (
    "CS-006: NULL multiplier must degrade to 1.0 (identity), not zero "
    "out every PnL"
  )
  assert batch_info[1]["pnl_multiplier"] == pytest.approx(1.0)
  assert batch_info[1]["scale_factor"] == pytest.approx(1.0)


# ── 6. WR / trade count are scale-invariant by construction ──────────

def test_winrate_unchanged_across_multipliers(db):
  """Scaling multiplies PnL; it must NOT change the sign of any trade,
  so win rate computed as fraction-of-positive stays identical.
  This is why the original bug went undetected — it's the single
  most-watched metric and the only one that's invariant to this class
  of bug.
  """
  _setup_strategy(db)
  db.add(BatchGroup(id=1, name="G1", pnl_multiplier=2.5, timeframe="30"))
  db.flush()
  raw = [100.0, -50.0, 75.0, -30.0, 200.0]
  _seed_batch(db, batch_id=1, group_id=1, pnls=raw)

  trades_scaled, _, _ = load_trades_lightweight(db, scale_pnl=True)
  trades_unscaled, _, _ = load_trades_lightweight(db, scale_pnl=False)

  wr_scaled = sum(1 for t in trades_scaled if t.net_profit > 0) / len(trades_scaled)
  wr_unscaled = sum(1 for t in trades_unscaled if t.net_profit > 0) / len(trades_unscaled)
  assert wr_scaled == wr_unscaled, (
    "CS-006: win rate must be scale-invariant — if this fails, scaling "
    "somehow flipped a sign, which means the multiplier went negative."
  )
