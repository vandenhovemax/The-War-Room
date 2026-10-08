"""CS-054: the audit called out a cluster of analysis modules that
had production call sites but no direct unit tests. Coverage gaps
matter here because these modules feed every optimization report:

  * ``monte_carlo.py``       — risk-cone drives the UI's "Best/Worst
                               Case" readouts users trade on.
  * ``daily_pnl.py``         — upstream of every correlation matrix
                               (standard and trade_overlap).
  * ``boundary_clip.py``     — silent mis-clip = IS/OOS contamination.
  * ``correlation.py``       — portfolio diversification dispatcher.
  * ``benchmark.py``         — H1/H2 robustness scoring.

This suite exercises the public entry points with small synthetic
fixtures so a regression in any of them fails in under a second
instead of surfacing inside a 12h WFAS run.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from backend.analysis.benchmark import compute_benchmark_robustness
from backend.analysis.boundary_clip import (
  ClippedTrade,
  clip_trades_at_boundary,
)
from backend.analysis.correlation import (
  calculate_correlation_overlap,
  calculate_correlation_standard,
  get_correlation_matrix,
)
from backend.analysis.daily_pnl import build_daily_pnl_matrix
from backend.analysis.monte_carlo import monte_carlo_simulation


# ── Fixtures ──────────────────────────────────────────────────────────

@dataclass
class _FakeTrade:
  """Duck-type of BacktestTrade used across these modules."""
  id: int = 0
  run_id: int = 0
  trade_num: int = 0
  symbol: str = "BTCUSDT"
  side: str = "long"
  entry_time: Any = None
  exit_time: Any = None
  entry_price: float = 100.0
  exit_price: float = 110.0
  quantity: float = 1.0
  net_profit: float = 10.0
  net_profit_pct: float = 10.0
  commission: float = 0.0
  position_id: str = "T1"


# ── monte_carlo_simulation ───────────────────────────────────────────

def test_monte_carlo_returns_none_on_empty_trades():
  """Empty input is a degenerate case the UI hits when no trades match
  a filter — the function must return None, not crash.
  """
  assert monte_carlo_simulation([]) is None


def test_monte_carlo_stats_shape_and_cone_ordering():
  """With a fixed seed the simulation is deterministic enough to lock
  two invariants: the stats dict carries every key the UI reads, and
  the cone percentiles are monotonically ordered at every step.
  """
  np.random.seed(0)
  pnl = [10.0, -5.0, 12.0, -8.0, 7.0, -3.0, 15.0, -4.0, 6.0, -2.0]
  result = monte_carlo_simulation(
    pnl, n_simulations=200, initial_capital=10000.0,
  )
  assert result is not None
  stats = result["stats"]
  # Every UI field must be populated.
  for key in (
    "median_return", "worst_case_return", "best_case_return",
    "prob_profit", "prob_ruin", "median_max_dd", "median_max_dd_pct",
    "median_sharpe", "original_return", "original_max_dd",
    "original_max_dd_pct", "n_trades", "n_simulations",
  ):
    assert key in stats, f"CS-054: MC stats missing key {key!r}"

  # Cone percentiles must be ordered 5 <= 25 <= 50 <= 75 <= 95 at every
  # step of the simulation — otherwise the UI's fan chart is nonsense.
  cone = result["cone"]
  for step in range(len(pnl)):
    p5 = cone[5][step]
    p25 = cone[25][step]
    p50 = cone[50][step]
    p75 = cone[75][step]
    p95 = cone[95][step]
    assert p5 <= p25 <= p50 <= p75 <= p95, (
      f"CS-054 REGRESSION: MC cone percentiles not monotone at "
      f"step {step}: {p5=}, {p25=}, {p50=}, {p75=}, {p95=}"
    )


def test_monte_carlo_original_equity_matches_cumsum():
  """`original_equity` must equal cumsum of the input PnLs — not a
  re-simulated path. If this drifts, the "actual run" line on the fan
  chart stops matching the trade log.
  """
  pnl = [5.0, -2.0, 3.0, 7.0, -1.0]
  result = monte_carlo_simulation(pnl, n_simulations=50)
  assert result is not None
  expected = np.cumsum(pnl).tolist()
  assert result["original_equity"] == expected


# ── build_daily_pnl_matrix ───────────────────────────────────────────

def test_daily_pnl_matrix_empty_input_returns_empty_df():
  df = build_daily_pnl_matrix([])
  assert isinstance(df, pd.DataFrame)
  assert df.empty


def test_daily_pnl_matrix_zero_fills_full_date_range():
  """Correlation of daily PnL breaks if non-trading days are missing
  rather than zero-filled — zero-fill is the published contract.
  """
  base = datetime(2024, 1, 1)
  trades = [
    _FakeTrade(symbol="BTCUSDT", exit_time=base, net_profit=10.0),
    # Skip Jan 2 — must be zero-filled, not absent.
    _FakeTrade(symbol="BTCUSDT", exit_time=base + timedelta(days=2), net_profit=-4.0),
  ]
  m = build_daily_pnl_matrix(trades)
  assert len(m) == 3, (
    "CS-054 REGRESSION: daily PnL matrix dropped non-trading days. "
    "Correlation will be computed only over trade days — inflated."
  )
  assert float(m.iloc[1, 0]) == 0.0, "gap day must be zero-filled"


def test_daily_pnl_matrix_include_side_splits_long_short():
  """When callers need split L/S exposure, columns must be
  SYMBOL_Long / SYMBOL_Short — the existing optimizer_split code
  reads the suffix directly.
  """
  base = datetime(2024, 1, 1)
  trades = [
    _FakeTrade(symbol="BTCUSDT", side="Long", exit_time=base, net_profit=5.0),
    _FakeTrade(symbol="BTCUSDT", side="Short", exit_time=base, net_profit=-2.0),
  ]
  m = build_daily_pnl_matrix(trades, include_side=True)
  assert "BTCUSDT_Long" in m.columns
  assert "BTCUSDT_Short" in m.columns
  assert float(m.loc[m.index[0], "BTCUSDT_Long"]) == 5.0
  assert float(m.loc[m.index[0], "BTCUSDT_Short"]) == -2.0


# ── clip_trades_at_boundary ──────────────────────────────────────────

def test_clip_straddling_trade_is_rewritten_to_boundary_close():
  """A trade entered before the boundary and exited after must be
  re-priced to the boundary close and marked `boundary_clip`.
  """
  boundary_date = "2024-06-30"
  prices = {"BTCUSDT": 105.0}
  boundary_times = {
    "BTCUSDT": datetime(2024, 6, 30, 23, 45, 0),
  }
  t = _FakeTrade(
    symbol="BTCUSDT", side="long",
    entry_time=datetime(2024, 6, 25),
    exit_time=datetime(2024, 7, 5),
    entry_price=100.0, exit_price=120.0, quantity=2.0,
    net_profit=40.0, commission=1.0,
  )
  out = clip_trades_at_boundary([t], boundary_date, prices, boundary_times)
  assert len(out) == 1
  clipped = out[0]
  assert isinstance(clipped, ClippedTrade), (
    "CS-054 REGRESSION: straddling trade must be replaced by "
    "ClippedTrade, not the original BacktestTrade."
  )
  assert clipped.exit_reason == "boundary_clip"
  assert clipped.exit_price == 105.0
  # PnL must reflect boundary close, not the original 120.0 exit:
  # (105 - 100) * 2 = 10, minus scaled commission.
  assert clipped.net_profit < 40.0
  assert clipped.exit_time == boundary_times["BTCUSDT"]


def test_clip_non_straddling_trade_is_passed_through_unchanged():
  """A trade fully inside IS (or fully inside OOS) must not be
  rewritten — otherwise the clip path silently alters non-boundary
  trades.
  """
  t = _FakeTrade(
    symbol="BTCUSDT", side="long",
    entry_time=datetime(2024, 6, 25),
    exit_time=datetime(2024, 6, 28),
    entry_price=100.0, exit_price=108.0, quantity=1.0,
    net_profit=8.0,
  )
  out = clip_trades_at_boundary([t], "2024-06-30", {"BTCUSDT": 105.0})
  assert out[0] is t, (
    "CS-054 REGRESSION: non-straddling trade was replaced by a "
    "ClippedTrade. Boundary clip must be a no-op for these."
  )


def test_clip_short_trade_pnl_sign_is_correct():
  """Short PnL = (entry - exit) * qty. The clip path must respect
  the side — a sign flip would silently invert the P&L of every
  boundary-clipped short.
  """
  t = _FakeTrade(
    symbol="BTCUSDT", side="short",
    entry_time=datetime(2024, 6, 25),
    exit_time=datetime(2024, 7, 5),
    entry_price=100.0, exit_price=90.0, quantity=2.0,
    net_profit=20.0, commission=0.0,
  )
  # Boundary close 95 → short gains (100-95)*2 = 10
  out = clip_trades_at_boundary([t], "2024-06-30", {"BTCUSDT": 95.0})
  clipped = out[0]
  assert clipped.net_profit == pytest.approx(10.0), (
    "CS-054 REGRESSION: short-side boundary clip PnL has the wrong "
    "sign. The formula is (entry - boundary_close) * qty."
  )


# ── correlation (standard + overlap) ─────────────────────────────────

def test_correlation_standard_identity_diagonal():
  """A daily matrix correlated with itself returns a square matrix
  with 1.0 on the diagonal and bounded elsewhere.
  """
  idx = pd.date_range("2024-01-01", periods=30, freq="D")
  np.random.seed(1)
  df = pd.DataFrame({
    "A": np.random.randn(30),
    "B": np.random.randn(30),
  }, index=idx)
  corr = calculate_correlation_standard(df)
  assert corr.shape == (2, 2)
  assert corr.loc["A", "A"] == pytest.approx(1.0)
  assert corr.loc["B", "B"] == pytest.approx(1.0)


def test_correlation_overlap_returns_zero_below_10_shared_days():
  """The overlap method defaults pairs with <10 shared non-zero days
  to 0 — users rely on this to reject thin-data pairs.
  """
  idx = pd.date_range("2024-01-01", periods=30, freq="D")
  a = np.zeros(30)
  b = np.zeros(30)
  # Only 5 days where BOTH are non-zero.
  for i in range(5):
    a[i] = 1.0
    b[i] = 1.0
  df = pd.DataFrame({"A": a, "B": b}, index=idx)
  corr = calculate_correlation_overlap(df)
  assert corr.loc["A", "B"] == 0.0, (
    "CS-054 REGRESSION: correlation_overlap returned a non-zero "
    "value with fewer than 10 shared non-zero days. Low-overlap "
    "pairs must default to 0 to avoid spurious diversification."
  )


def test_correlation_dispatch_auto_downgrades_huge_pools():
  """`trade_overlap` on >500 assets must auto-downgrade to `standard`
  so the UI cannot wedge on huge pools.
  """
  idx = pd.date_range("2024-01-01", periods=5, freq="D")
  # 501 columns of zero data triggers the downgrade branch.
  data = {f"S{i}": np.zeros(5) for i in range(501)}
  df = pd.DataFrame(data, index=idx)
  # Should not raise — downgrade returns the standard matrix.
  corr = get_correlation_matrix(df, method="trade_overlap")
  assert corr.shape == (501, 501)


# ── benchmark robustness ─────────────────────────────────────────────

def test_benchmark_robustness_stable_vs_weak_classification():
  """Stable assets (balanced H1/H2 PF) get Elite/Strong/Stable;
  unstable (one-sided losing half) gets Weak. Lock the sign of the
  classification — a flip here misleads users into trading weak
  assets.
  """
  # Build a stable asset: PnLs evenly positive across the whole period.
  stable_rows = [
    {
      "Asset": "STABLE", "Side": "Long",
      "Entry Time": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
      "Exit Time": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
      "Net Profit": 10.0 if i % 2 == 0 else -2.0,
    }
    for i in range(40)
  ]
  # Build a weak asset: all H1 wins, all H2 losses — PF crashes in H2.
  weak_rows = []
  for i in range(20):
    weak_rows.append({
      "Asset": "WEAK", "Side": "Long",
      "Entry Time": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
      "Exit Time": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
      "Net Profit": 10.0,
    })
  for i in range(20, 40):
    weak_rows.append({
      "Asset": "WEAK", "Side": "Long",
      "Entry Time": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
      "Exit Time": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
      "Net Profit": -10.0,
    })

  df = pd.DataFrame(stable_rows + weak_rows)
  stats, source_pf = compute_benchmark_robustness(df, validation_threshold=1.2)

  assert stats["WEAK"]["status"] == "Weak", (
    "CS-054 REGRESSION: asset with all-winning H1 and all-losing H2 "
    "was not classified Weak. The H1/H2 split check is broken."
  )
  assert bool(stats["STABLE"]["is_stable"]) is True, (
    "CS-054 REGRESSION: evenly-profitable asset failed the stability "
    "gate — H1/H2 PF thresholds are misbehaving."
  )
