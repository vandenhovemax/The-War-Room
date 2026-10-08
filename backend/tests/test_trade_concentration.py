"""Tests for backend.analysis.trade_concentration.

Anchored to the real Feb 2025 live-UI screenshot that motivated the filter:
  - 3 "winning" trades totalling $11,216 in a single month
  - 2 of them are the same ALGO short on Feb 1 at 11:00 AM, $5,028 each
    (different batches — B43, B44 — same bar)
  - 90% of the month came from two near-duplicate fills

The filter's job is to flag that kind of concentration before the config is
trusted in production.
"""
from __future__ import annotations

from datetime import datetime

from backend.analysis.trade_concentration import (
  DEFAULT_MAX_TRADE_PCT_OF_OOS,
  TradeRecord,
  compute_concentration,
  concentration_from_trades,
  dedupe_same_bar_cross_batch,
)


def _feb_2025_dataset() -> list[TradeRecord]:
  """Feb 2025 screenshot + a few synthetic OOS trades for context."""
  feb = [
    TradeRecord(5028.51, datetime(2025, 2, 1, 11, 0), "ALGO", "s"),
    TradeRecord(5028.51, datetime(2025, 2, 1, 11, 0), "ALGO", "s"),  # duplicate
    TradeRecord(1159.14, datetime(2025, 2, 1, 12, 30), "PHA", "s"),
  ]
  surrounding = [
    TradeRecord(200.0, datetime(2025, 3, 5, 10, 0), "BTC", "l"),
    TradeRecord(-150.0, datetime(2025, 3, 6, 11, 0), "ETH", "s"),
    TradeRecord(8500.0, datetime(2025, 4, 10, 10, 0), "SOL", "l"),
    TradeRecord(130.0, datetime(2025, 5, 15, 10, 0), "MATIC", "l"),
    TradeRecord(180.0, datetime(2025, 6, 2, 10, 0), "OP", "s"),
  ]
  return feb + surrounding


def test_feb_2025_case_fails_concentration_gate() -> None:
  """The motivating real-world case must fail the 25% rule."""
  result = compute_concentration(_feb_2025_dataset())
  assert result["passes_concentration_gate"] is False
  assert result["max_trade_pct_of_oos"] > DEFAULT_MAX_TRADE_PCT_OF_OOS
  # Expect violations to cover: max-trade, top-3, quarter, month
  assert len(result["violations"]) >= 2
  # Sanity check: largest |pnl| is the $8,500 SOL trade or an ALGO
  assert result["max_trade_pnl"] >= 5028.51
  assert result["n_trades"] == 8


def test_empty_trades_passes_gate_and_returns_zeros() -> None:
  result = compute_concentration([])
  assert result["passes_concentration_gate"] is True
  assert result["n_trades"] == 0
  assert result["max_trade_pnl"] == 0.0
  assert result["cum_oos_pnl"] == 0.0
  assert result["violations"] == []


def test_single_dominant_trade_fails() -> None:
  trades = [
    TradeRecord(10000.0, datetime(2025, 1, 5, 10, 0), "BIG", "l"),
    TradeRecord(100.0, datetime(2025, 2, 5, 10, 0), "SMALL", "l"),
  ]
  result = compute_concentration(trades)
  assert result["passes_concentration_gate"] is False
  # 10000 / 10100 ≈ 0.99
  assert abs(result["max_trade_pct_of_oos"] - (10000 / 10100)) < 1e-3


def test_distributed_clean_config_passes() -> None:
  """A properly diversified OOS with no trade > 25% of cum AND each month's
  net large vs any single trade in that month passes the full gate.

  Key design: each month has multiple winners of similar magnitude so
  max|trade| stays well under 80% of the month's signed net.
  """
  # 20 trades spread across 5 months, 4 similarly-sized winners per month.
  # Cum = 20 × $300 = $6,000; max trade $350, which is 5.8% of cum and
  # ≤35% of any month's net.
  trades: list[TradeRecord] = []
  for m in range(1, 6):
    # 4 winners per month at varied magnitudes that sum to ~$1,200
    for d, pnl in zip([3, 10, 17, 24], [250.0, 300.0, 300.0, 350.0]):
      trades.append(TradeRecord(pnl, datetime(2025, m, d, 11, 0), f"A{m}{d}", "l"))
  result = compute_concentration(trades)
  assert result["passes_concentration_gate"] is True, (
    f"expected pass, got violations: {result['violations']}"
  )
  assert result["max_trade_pct_of_oos"] <= DEFAULT_MAX_TRADE_PCT_OF_OOS


def test_relaxed_threshold_still_caught_by_top3() -> None:
  """Relaxing the single-trade gate shouldn't let an all-concentrated config
  slip past — top-3 rule is the backup."""
  result = compute_concentration(
    _feb_2025_dataset(),
    max_trade_pct_of_oos=0.95,  # artificially high single-trade threshold
  )
  # Top-3 is $8500 + $5028 + $5028 = $18,556 vs cum ≈ $20k → ~93%
  assert result["passes_concentration_gate"] is False


def test_dedupe_same_bar_cross_batch_collapses_duplicates() -> None:
  feb = [
    TradeRecord(5028.51, datetime(2025, 2, 1, 11, 0), "ALGO", "s"),
    TradeRecord(5028.51, datetime(2025, 2, 1, 11, 2), "ALGO", "s"),
    TradeRecord(1159.14, datetime(2025, 2, 1, 12, 30), "PHA", "s"),
  ]
  deduped = dedupe_same_bar_cross_batch(feb, bar_resolution_minutes=30)
  assert len(deduped) == 2
  algo = [r for r in deduped if r.symbol == "ALGO"][0]
  # Mean of two identical fills = same value
  assert abs(algo.pnl - 5028.51) < 1e-6
  # Non-duplicated PHA trade unchanged
  pha = [r for r in deduped if r.symbol == "PHA"][0]
  assert abs(pha.pnl - 1159.14) < 1e-6


def test_dedupe_different_bars_not_collapsed() -> None:
  """11:00 and 11:35 are different 30m bars — should NOT merge."""
  trades = [
    TradeRecord(100.0, datetime(2025, 3, 1, 11, 0), "BTC", "l"),
    TradeRecord(200.0, datetime(2025, 3, 1, 11, 35), "BTC", "l"),
  ]
  deduped = dedupe_same_bar_cross_batch(trades, bar_resolution_minutes=30)
  assert len(deduped) == 2


def test_concentration_from_trades_works_on_duck_objects() -> None:
  """Verifies the BacktestTrade-shaped shortcut. No sqlalchemy needed."""
  class FakeTrade:
    def __init__(self, net_profit, entry_time, symbol, side):
      self.net_profit = net_profit
      self.entry_time = entry_time
      self.symbol = symbol
      self.side = side
  trades = [
    FakeTrade(500.0, datetime(2025, 1, 1, 10), "A", "l"),
    FakeTrade(-100.0, datetime(2025, 2, 1, 10), "B", "s"),
    FakeTrade(2000.0, datetime(2025, 3, 1, 10), "C", "l"),
  ]
  result = concentration_from_trades(trades)
  assert result["n_trades"] == 3
  # 2000 / 2400 = 83% → fails gate
  assert result["passes_concentration_gate"] is False


def test_tuple_input_shapes() -> None:
  """Both 2-tuples and 4-tuples accepted."""
  t2 = [(100.0, datetime(2025, 1, 1, 10)), (50.0, datetime(2025, 2, 1, 10))]
  r2 = compute_concentration(t2)
  assert r2["n_trades"] == 2

  t4 = [
    (100.0, datetime(2025, 1, 1, 10), "A", "l"),
    (50.0, datetime(2025, 2, 1, 10), "B", "s"),
  ]
  r4 = compute_concentration(t4)
  assert r4["n_trades"] == 2


def test_per_month_and_per_quarter_reported() -> None:
  trades = [
    TradeRecord(500.0, datetime(2025, 1, 5, 10), "A", "l"),
    TradeRecord(100.0, datetime(2025, 1, 20, 10), "B", "l"),
    TradeRecord(2000.0, datetime(2025, 4, 3, 10), "C", "l"),
  ]
  result = compute_concentration(trades)
  # 2025-01 and 2025-04 → 2 months, 2 quarters (Q1, Q2)
  assert result["n_months"] == 2
  assert result["n_quarters"] == 2
  assert "2025-01" in result["per_month_max_pct"]
  assert "2025-Q1" in result["per_quarter_max_pct"]
