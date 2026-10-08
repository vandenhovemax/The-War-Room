"""Targeted tests for the event-based WFAS dedup/rebuild helper.

Verifies every invariant for backend/analysis/wfo_event_dedup.py:
  1. Duplicated (asset, dt) records are dropped exactly once.
  2. Equity curve is rebuilt from deduped records, not summed per-window.
  3. asset_frequency.selected matches exact per-asset count in deduped records.
  4. Ancillary counters scale proportionally.
  5. No-op when there are no duplicates.
  6. Runs whether overlap_flag is True or False.
"""
from __future__ import annotations

from backend.analysis.wfo_event_dedup import EventAggregateState, dedup_and_rebuild


def _rec(asset: str, dt: str, pnl: float) -> dict:
  return {"asset": asset, "dt": dt, "pnl": pnl, "symbol": asset.split("_")[1] if "_" in asset else asset, "side": "Long"}


def test_overlap_drops_duplicates_and_rebuilds_curve():
  """The canonical bug: same trade in two overlapping windows → curve double-counts loss."""
  # Window 1 pnls: -50, +100 (running: -50, 50)
  # Window 2 pnls (overlap): +100 (duplicate!), -30 (new). Naive append → curve: -50, 50, 150, 120
  # Deduped should be: -50, +100, -30 → curve: -50, 50, 20
  state = EventAggregateState()
  state.selected_records = [
    _rec("B1_BTC_Long", "2024-01-01T10:00:00", -50.0),
    _rec("B1_BTC_Long", "2024-01-02T10:00:00", 100.0),
    _rec("B1_BTC_Long", "2024-01-02T10:00:00", 100.0),  # dup
    _rec("B1_BTC_Long", "2024-01-03T10:00:00", -30.0),
  ]
  state.selected_pnls = [-50.0, 100.0, 100.0, -30.0]
  state.equity_curve = [-50.0, 50.0, 150.0, 120.0]  # pre-dedup, inflated
  state.equity_dates = ["2024-01-01", "2024-01-02", "2024-01-02", "2024-01-03"]
  state.pnl_deltas = [-50.0, 100.0, 100.0, -30.0]
  state.asset_freq = {"B1_BTC_Long": {"selected": 4, "eligible": 8}}
  state.total_steps = 10
  state.rebalance_count = 4
  state.all_trade_count = 20
  state.warmup_trades = 6
  state.running_equity = 120.0

  dropped = dedup_and_rebuild(state, overlap_flag=True)

  assert dropped == 1, f"expected 1 drop, got {dropped}"
  assert state.total_trades == 3
  assert state.selected_pnls == [-50.0, 100.0, -30.0]
  assert state.equity_curve == [-50.0, 50.0, 20.0], f"curve not rebuilt: {state.equity_curve}"
  assert state.running_equity == 20.0
  assert state.asset_freq["B1_BTC_Long"]["selected"] == 3
  # eligible scaled by 3/4 = 0.75 → 8 * 0.75 = 6
  assert state.asset_freq["B1_BTC_Long"]["eligible"] == 6
  # Ancillary scaled by 3/4
  assert state.total_steps == 8  # round(10 * 0.75) = 8
  assert state.rebalance_count == 3  # round(4 * 0.75) = 3
  assert state.all_trade_count == 15  # round(20 * 0.75) = 15
  assert state.warmup_trades == 4  # round(6 * 0.75) = round(4.5) = 4 (banker's rounding)
  # Invariant: curve sum == pnls sum
  assert round(sum(state.pnl_deltas), 2) == round(sum(state.selected_pnls), 2)


def test_no_duplicates_is_noop():
  """Rolling mode (no overlap): helper should leave everything intact."""
  state = EventAggregateState()
  state.selected_records = [
    _rec("B1_BTC_Long", "2024-01-01T10:00:00", -50.0),
    _rec("B1_ETH_Long", "2024-01-02T10:00:00", 100.0),
  ]
  state.selected_pnls = [-50.0, 100.0]
  state.equity_curve = [-50.0, 50.0]
  state.equity_dates = ["2024-01-01", "2024-01-02"]
  state.pnl_deltas = [-50.0, 100.0]
  state.asset_freq = {
    "B1_BTC_Long": {"selected": 1, "eligible": 3},
    "B1_ETH_Long": {"selected": 1, "eligible": 2},
  }
  state.total_steps = 5
  state.rebalance_count = 2
  state.all_trade_count = 10
  state.warmup_trades = 3

  dropped = dedup_and_rebuild(state, overlap_flag=False)

  assert dropped == 0
  assert state.total_trades == 2
  assert state.equity_curve == [-50.0, 50.0]  # unchanged
  assert state.total_steps == 5  # unchanged (scaling skipped when dropped=0)
  assert state.asset_freq["B1_BTC_Long"]["selected"] == 1
  assert state.asset_freq["B1_BTC_Long"]["eligible"] == 3  # unchanged
  assert state.asset_freq["B1_ETH_Long"]["eligible"] == 2


def test_multi_asset_dedup_rebuilds_asset_frequency_exactly():
  """With multiple assets, selected count must be exact per asset after dedup."""
  state = EventAggregateState()
  state.selected_records = [
    _rec("B1_BTC_Long", "2024-01-01T10:00:00", 10.0),
    _rec("B1_BTC_Long", "2024-01-01T10:00:00", 10.0),  # dup
    _rec("B1_ETH_Long", "2024-01-01T11:00:00", -5.0),
    _rec("B1_ETH_Long", "2024-01-02T11:00:00", 20.0),
    _rec("B1_ETH_Long", "2024-01-02T11:00:00", 20.0),  # dup
    _rec("B1_SOL_Long", "2024-01-03T12:00:00", 7.0),
  ]
  state.selected_pnls = [10.0, 10.0, -5.0, 20.0, 20.0, 7.0]
  state.equity_curve = [10.0, 20.0, 15.0, 35.0, 55.0, 62.0]
  state.equity_dates = ["2024-01-01"] * 2 + ["2024-01-01"] + ["2024-01-02"] * 2 + ["2024-01-03"]
  state.pnl_deltas = [10.0, 10.0, -5.0, 20.0, 20.0, 7.0]
  state.asset_freq = {
    "B1_BTC_Long": {"selected": 2, "eligible": 4},
    "B1_ETH_Long": {"selected": 3, "eligible": 6},
    "B1_SOL_Long": {"selected": 1, "eligible": 2},
    "B1_DOGE_Long": {"selected": 0, "eligible": 4},  # eligible but never picked
  }
  state.total_steps = 12

  dropped = dedup_and_rebuild(state, overlap_flag=True)

  assert dropped == 2
  assert state.total_trades == 4
  # Exact per-asset selected counts
  assert state.asset_freq["B1_BTC_Long"]["selected"] == 1
  assert state.asset_freq["B1_ETH_Long"]["selected"] == 2
  assert state.asset_freq["B1_SOL_Long"]["selected"] == 1
  # Eligible scaled by 4/6 ≈ 0.667
  # BTC: 4*0.667 = 2.67 → 3
  assert state.asset_freq["B1_BTC_Long"]["eligible"] == 3
  # ETH: 6*0.667 = 4 → but clamped to selected (2) if needed — 4 is fine
  assert state.asset_freq["B1_ETH_Long"]["eligible"] == 4
  # Never-selected DOGE still present
  assert "B1_DOGE_Long" in state.asset_freq
  # Equity curve rebuilt in time order
  assert state.equity_curve[-1] == round(10.0 - 5.0 + 20.0 + 7.0, 2)
  assert round(sum(state.pnl_deltas), 2) == round(sum(state.selected_pnls), 2)


def test_empty_state_is_safe():
  state = EventAggregateState()
  dropped = dedup_and_rebuild(state, overlap_flag=False)
  assert dropped == 0
  assert state.total_trades == 0


def test_invariant_length_match():
  """After dedup, len(pnls) must equal len(records) — assertion enforces it."""
  state = EventAggregateState()
  state.selected_records = [_rec("B1_BTC_Long", "2024-01-01T10:00:00", 5.0)]
  state.selected_pnls = [5.0]
  dedup_and_rebuild(state, overlap_flag=False)
  assert len(state.selected_pnls) == len(state.selected_records)


if __name__ == "__main__":
  test_overlap_drops_duplicates_and_rebuilds_curve()
  print("PASS: test_overlap_drops_duplicates_and_rebuilds_curve")
  test_no_duplicates_is_noop()
  print("PASS: test_no_duplicates_is_noop")
  test_multi_asset_dedup_rebuilds_asset_frequency_exactly()
  print("PASS: test_multi_asset_dedup_rebuilds_asset_frequency_exactly")
  test_empty_state_is_safe()
  print("PASS: test_empty_state_is_safe")
  test_invariant_length_match()
  print("PASS: test_invariant_length_match")
  print("\nAll sandbox dedup tests passed.")
