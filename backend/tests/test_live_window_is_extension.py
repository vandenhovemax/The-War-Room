"""Pins the Live Window IS-extension fix (April 22).

The live window appended at the end of `run_batch_selection` used to reuse
the last historical window's IS unchanged — asset selection for live
trading ignored the most recent OOS period (data we now have). This test
asserts that the live window:

  * shifts IS forward by one OOS period (rolling walk-forward)
  * ends IS at the last historical OOS end (folds last OOS into IS)
  * sets OOS to [last_oos_end + 1, today]
  * computes real OOS metrics when `today > last_oos_end`, zero otherwise
  * is flagged with `is_live=True` on both the window and every candidate

Without the fix the saved portfolio would track assets picked from stale
IS data and its forward-walking OOS view would collapse to zero days.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.analysis.batch_selection import (
  BatchSelectionConfig,
  run_batch_selection,
)
from backend.analysis.wfo_window_gen import generate_rolling_windows


def _fake_trade(tid: int, run_id: int, symbol: str, side: str,
                entry_dt: datetime, pnl: float):
  """Duck-typed BacktestTrade — only the fields run_batch_selection reads."""
  return SimpleNamespace(
    id=tid, run_id=run_id, trade_num=tid, position_id=f"P{tid}",
    symbol=symbol, side=side,
    entry_time=entry_dt,
    exit_time=entry_dt + timedelta(hours=2),
    entry_price=100.0, exit_price=100.0 + pnl,
    quantity=1.0, net_profit=pnl, net_profit_pct=pnl / 100.0,
    commission=0.0, mae=0.0, mfe=0.0,
  )


def _seed_trades(
  *, n_symbols: int = 3, start: date, end: date, spacing_days: int = 7,
) -> tuple[list, dict[int, int]]:
  """Make a dense, uniform trade stream per symbol across [start, end]."""
  trades = []
  run_batch_map: dict[int, int] = {}
  symbols = ["AAAUSDT", "BBBUSDT", "CCCUSDT"][:n_symbols]
  tid = 0
  for run_id, sym in enumerate(symbols, start=1):
    run_batch_map[run_id] = 100 + run_id   # distinct batch per symbol
    cur = start
    # Uniform $10 wins, $5 losses — positive PF for every asset
    win_toggle = True
    while cur <= end:
      tid += 1
      pnl = 10.0 if win_toggle else -5.0
      trades.append(_fake_trade(
        tid=tid, run_id=run_id, symbol=sym, side="Long",
        entry_dt=datetime(cur.year, cur.month, cur.day, 10, 0),
        pnl=pnl,
      ))
      win_toggle = not win_toggle
      cur += timedelta(days=spacing_days)
  return trades, run_batch_map


def _make_config(include_live: bool) -> BatchSelectionConfig:
  return BatchSelectionConfig(
    max_batches=2,
    assets_per_batch=2,
    n_candidates=1,
    min_calibrated=1,
    batch_rank_metric="net_pnl",
    cal_rank_metric="net_pnl",
    asset_rank_metric="net_pnl",
    min_is_days=5,
    min_pf=0.1,
    min_asset_days=1,
    asset_wr_gate=0.0,
    q_starts=1,
    include_live_window=include_live,
  )


@pytest.fixture
def seeded_trades():
  trades, rbm = _seed_trades(
    start=date(2023, 1, 1), end=date(2024, 9, 30), spacing_days=3,
  )
  return trades, rbm


def _hist_windows():
  # IS=12m, OOS=3m, 1 historical window ending 2024-08-31
  windows = generate_rolling_windows(
    is_months=12, oos_months=3,
    last_oos_end="2024-08-31", data_start="2023-01-01",
  )
  return [w for w in windows if w.get("oos_start")]


def test_live_window_shifts_is_forward_by_one_oos(seeded_trades):
  """With today>last_oos_end, live IS must roll forward one OOS period."""
  trades, rbm = seeded_trades
  windows = _hist_windows()
  cfg = _make_config(include_live=True)
  # Sanity: pick up the real last historical OOS end from generator output
  last_hist_win = [w for w in windows if w.get("oos_start")][-1]
  last_oos_end = last_hist_win["oos_end"]            # e.g. "2024-06-30"
  last_is_start = last_hist_win["is_start"]          # e.g. "2023-04-01"
  last_oos_start = last_hist_win["oos_start"]
  oos_days = (
    datetime.strptime(last_oos_end, "%Y-%m-%d")
    - datetime.strptime(last_oos_start, "%Y-%m-%d")
  ).days + 1
  expected_is_start = (
    datetime.strptime(last_is_start, "%Y-%m-%d")
    + timedelta(days=oos_days)
  ).date().isoformat()
  expected_oos_start = (
    datetime.strptime(last_oos_end, "%Y-%m-%d") + timedelta(days=1)
  ).date().isoformat()
  today_iso = "2024-10-15"

  with patch(
    "backend.analysis.batch_selection._clock_today",
    return_value=date(2024, 10, 15),
  ):
    result = run_batch_selection(trades, windows, cfg, rbm)

  live_windows = [w for w in result["windows"] if w.get("is_live")]
  assert len(live_windows) == 1, "expected exactly one live window"
  lw = live_windows[0]

  # IS must END at last historical OOS end (last OOS folded into IS)
  assert lw["is_end"] == last_oos_end, (
    f"Expected live IS to end at last historical OOS end ({last_oos_end}), "
    f"got {lw['is_end']}"
  )

  # IS must be SHIFTED forward by one OOS period (rolling walk-forward)
  assert lw["is_start"] == expected_is_start, (
    f"Expected live IS start to roll forward {oos_days} days from last IS "
    f"start {last_is_start} → {expected_is_start}, got {lw['is_start']}"
  )

  # OOS = day after last_oos_end through today
  assert lw["oos_start"] == expected_oos_start
  assert lw["oos_end"] == today_iso

  # Live flag on every candidate
  for cand in lw.get("candidates", []):
    assert cand.get("is_live") is True


def test_live_window_zeros_oos_when_today_equals_last_oos_end(seeded_trades):
  """When today == last_oos_end, OOS has zero days → metrics zeroed out."""
  trades, rbm = seeded_trades
  windows = _hist_windows()
  cfg = _make_config(include_live=True)
  # Use the REAL last_oos_end from the generator (not the target)
  last_oos_end_iso = windows[-1]["oos_end"]
  last_oos_end_date = datetime.strptime(last_oos_end_iso, "%Y-%m-%d").date()

  with patch(
    "backend.analysis.batch_selection._clock_today",
    return_value=last_oos_end_date,
  ):
    result = run_batch_selection(trades, windows, cfg, rbm)

  live = [w for w in result["windows"] if w.get("is_live")][0]
  for cand in live.get("candidates", []):
    # No forward-walking data yet — OOS metrics must be zeroed
    assert cand.get("oos_metrics") == {}
    assert cand.get("oos_equity_curve") == []
    assert cand.get("oos_wfe") is None


def test_live_window_keeps_real_oos_metrics_when_forward_data_exists(seeded_trades):
  """When today>last_oos_end and trades exist in [oos_start, today],
  OOS metrics must be REAL — not zeroed out. This is the honest
  forward-walking evaluation the user sees on the saved portfolio."""
  trades, rbm = seeded_trades
  windows = _hist_windows()
  cfg = _make_config(include_live=True)
  # Today must be strictly after the last generated OOS end
  last_oos_end_iso = windows[-1]["oos_end"]
  today = datetime.strptime(last_oos_end_iso, "%Y-%m-%d").date() + timedelta(days=30)

  with patch(
    "backend.analysis.batch_selection._clock_today",
    return_value=today,
  ):
    result = run_batch_selection(trades, windows, cfg, rbm)

  live = [w for w in result["windows"] if w.get("is_live")][0]
  cands = live.get("candidates", [])
  assert cands, "live window must have at least one candidate"
  # At least one candidate should have non-empty OOS metrics
  has_real_oos = any(
    (c.get("oos_metrics") or {}).get("total_trades", 0) > 0
    for c in cands
  )
  assert has_real_oos, (
    "Expected real OOS metrics on live window when forward-walking "
    "data exists — found all zeros (bug: OOS was forcibly cleared)"
  )


def test_no_live_window_when_disabled(seeded_trades):
  trades, rbm = seeded_trades
  windows = _hist_windows()
  cfg = _make_config(include_live=False)

  result = run_batch_selection(trades, windows, cfg, rbm)
  live_windows = [w for w in result["windows"] if w.get("is_live")]
  assert live_windows == [], "live window should not appear when disabled"
