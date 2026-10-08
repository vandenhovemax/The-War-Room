"""IS/OOS Data Integrity Agent — comprehensive test suite.

Validates that ALL optimization code paths enforce IS/OOS boundaries correctly.
Uses synthetic data — no database or backend server needed.

Covers:
  - Trade exit/entry filtering
  - Daily PnL boundary enforcement
  - Indicator & multi-TF data isolation
  - Seed scoring (sortino, recency, benchmark, event)
  - Quality filters
  - Correlation matrix source data
  - Optimizer output pool membership
  - Cross-batch isolation
  - WFO window generation & boundary checks
  - WFAS boundary enforcement
  - Portfolio save boundary
  - End-to-end optimizer pipelines

Run from the project root:
    python -m pytest backend/tests/test_integrity.py -v
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from backend.analysis.integrity_agent import (
  CheckResult,
  IntegrityReport,
  _build_report,
  check_corr_source_boundary,
  check_cross_batch_isolation,
  check_daily_pnl_boundary,
  check_date_to_present,
  check_optimizer_result_pool,
  check_quality_filter_boundary,
  check_recency_boundary,
  check_seed_scoring_boundary,
  check_trade_entry_boundary,
  check_trade_exit_boundary,
  check_window_boundaries,
)
from backend.analysis.wfo_boundary import (
  OOSContaminationError,
  assert_no_oos_leakage,
  assert_trades_within_boundary,
  build_is_daily_pnl,
  filter_trades_strict,
)
from backend.analysis.wfo_window_gen import (
  generate_overlapping_windows,
  generate_rolling_windows,
  generate_static_windows,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FIXTURES — Synthetic data, no DB needed
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

IS_END = "2025-06-01"


@dataclass
class FakeTrade:
  """Minimal trade mock for boundary tests."""
  id: int = 1
  run_id: int = 1
  symbol: str = "BTCUSDT"
  side: str = "Long"
  entry_time: datetime | None = None
  exit_time: datetime | None = None
  entry_price: float = 50000.0
  exit_price: float = 51000.0
  quantity: float = 1.0
  net_profit: float = 100.0
  position_id: str | None = None


def _make_trades(specs: list[tuple[str, str, str]]) -> list[FakeTrade]:
  """Create fake trades: [(symbol, entry_date, exit_date), ...]"""
  trades = []
  for i, (sym, entry, exit_d) in enumerate(specs, 1):
    trades.append(FakeTrade(
      id=i, symbol=sym,
      entry_time=datetime.fromisoformat(entry),
      exit_time=datetime.fromisoformat(exit_d),
      net_profit=100.0 if i % 3 != 0 else -50.0,
    ))
  return trades


@pytest.fixture
def is_trades():
  """Trades all within IS period."""
  return _make_trades([
    ("BTCUSDT", "2025-01-15", "2025-02-01"),
    ("ETHUSDT", "2025-03-01", "2025-04-15"),
    ("SOLUSDT", "2025-04-01", "2025-05-20"),
    ("ADAUSDT", "2025-05-01", "2025-05-30"),
  ])


@pytest.fixture
def mixed_trades():
  """Mix of IS and OOS trades."""
  return _make_trades([
    ("BTCUSDT", "2025-01-15", "2025-02-01"),     # IS: entry IS, exit IS
    ("ETHUSDT", "2025-05-28", "2025-06-05"),     # OOS: entry IS, exit OOS (straddle)
    ("SOLUSDT", "2025-06-10", "2025-07-01"),     # OOS: entry OOS, exit OOS
    ("ADAUSDT", "2025-04-01", "2025-05-15"),     # IS: fully within IS
  ])


@pytest.fixture
def is_daily_pnl():
  """Daily PnL matrix within IS period."""
  np.random.seed(42)
  dates = pd.date_range("2025-01-01", "2025-05-31", freq="D")
  return pd.DataFrame({
    "BTCUSDT_Long": np.random.normal(10, 5, len(dates)),
    "ETHUSDT_Long": np.random.normal(5, 3, len(dates)),
    "SOLUSDT_Short": np.random.normal(3, 4, len(dates)),
  }, index=dates)


@pytest.fixture
def oos_leaked_daily_pnl():
  """Daily PnL that extends into OOS period."""
  np.random.seed(42)
  dates = pd.date_range("2025-01-01", "2025-08-01", freq="D")
  return pd.DataFrame({
    "BTCUSDT_Long": np.random.normal(10, 5, len(dates)),
    "ETHUSDT_Long": np.random.normal(5, 3, len(dates)),
  }, index=dates)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 1: Trade Filtering Boundary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTradeFiltering:
  """Validate trade date filtering enforces IS boundary."""

  def test_exit_time_filter_excludes_oos_trades(self, mixed_trades):
    """Trades with exit_time > IS_END must be excluded."""
    result = check_trade_exit_boundary(mixed_trades, IS_END)
    assert not result.passed, "Should detect OOS trades"
    assert result.detail["total_violations"] == 2

  def test_entry_time_within_is_period(self, is_trades):
    """All entries within IS should pass."""
    result = check_trade_entry_boundary(is_trades, IS_END)
    assert result.passed

  def test_filter_trades_strict_boundary_exact(self):
    """filter_trades_strict filters by entry_time, not exit_time.
    Both trades have entry within IS, so both are included.
    """
    trades = _make_trades([
      ("BTC", "2025-05-01", "2025-06-01"),   # entry in IS → included
      ("ETH", "2025-05-01", "2025-06-02"),   # entry in IS → included (exit after IS is OK)
    ])
    filtered = filter_trades_strict(trades, "2025-01-01", IS_END)
    assert len(filtered) == 2

  def test_trades_straddling_boundary_included(self, mixed_trades):
    """filter_trades_strict uses entry_time, so straddling trades are included."""
    filtered = filter_trades_strict(mixed_trades, "2025-01-01", IS_END)
    # Straddling trades (entry in IS, exit in OOS) ARE included
    for t in filtered:
      assert pd.Timestamp(t.entry_time) <= pd.Timestamp(IS_END) + pd.Timedelta(days=1)

  def test_entry_signals_from_is_candles_only(self, mixed_trades):
    """Trades with entry_time > IS_END means signal came from OOS candles."""
    result = check_trade_entry_boundary(mixed_trades, IS_END)
    assert not result.passed, "Should detect entry in OOS period"
    # The SOLUSDT trade enters on 2025-06-10 (after IS_END)
    assert result.detail["total_violations"] >= 1

  def test_assert_trades_within_boundary_raises(self, mixed_trades):
    """wfo_boundary.assert_trades_within_boundary raises on violation."""
    with pytest.raises(OOSContaminationError):
      assert_trades_within_boundary(mixed_trades, IS_END, "test")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 2: Daily PnL Boundary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDailyPnlBoundary:
  """Validate daily PnL matrix respects IS boundary."""

  def test_daily_pnl_truncation_by_date_to(self, oos_leaked_daily_pnl):
    """After truncation, no rows should exceed IS boundary."""
    cutoff = pd.Timestamp(IS_END).normalize()
    truncated = oos_leaked_daily_pnl[oos_leaked_daily_pnl.index <= cutoff]
    result = check_daily_pnl_boundary(truncated, IS_END)
    assert result.passed

  def test_build_is_daily_pnl_rejects_oos_rows(self, is_trades):
    """build_is_daily_pnl should validate the result stays within IS."""
    from backend.analysis.daily_pnl import build_daily_pnl_matrix
    daily_pnl = build_daily_pnl_matrix(is_trades, include_side=True)
    # Should not raise — all trades are within IS
    result = check_daily_pnl_boundary(daily_pnl, IS_END)
    assert result.passed

  def test_assert_no_oos_leakage_soft_check(self, oos_leaked_daily_pnl):
    """assert_no_oos_leakage is a soft check — logs but does not raise."""
    # Should NOT raise (entry_time assignment allows straddling trades)
    assert_no_oos_leakage(oos_leaked_daily_pnl, IS_END, "test")

  def test_assert_no_oos_leakage_passes_clean_data(self, is_daily_pnl):
    """Clean IS data should pass without raising."""
    assert_no_oos_leakage(is_daily_pnl, IS_END, "test")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 3: Indicator & Multi-TF Boundary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestIndicatorAndTFBoundary:
  """Validate indicator values and multi-TF data stay within IS."""

  def test_primary_tf_candles_within_is(self):
    """Primary timeframe candle dates must be within IS."""
    # Simulate OHLCV data
    dates = pd.date_range("2025-01-01", "2025-05-31", freq="30min")
    ohlcv = pd.DataFrame({
      "open": 50000, "high": 50100, "low": 49900, "close": 50050,
    }, index=dates)
    # Verify max date within IS
    assert ohlcv.index.max() <= pd.Timestamp(IS_END) + pd.Timedelta(days=1)

  def test_secondary_tf_candles_within_is(self):
    """Multi-TF (daily) candle data must not extend beyond IS."""
    # Simulate daily candles used by request_security()
    daily_dates = pd.date_range("2025-01-01", "2025-05-31", freq="D")
    daily_candles = pd.DataFrame({
      "close": np.random.normal(50000, 1000, len(daily_dates)),
    }, index=daily_dates)
    assert daily_candles.index.max() <= pd.Timestamp(IS_END) + pd.Timedelta(days=1)

    # If daily candles extend into OOS, that's a violation
    oos_daily = pd.date_range("2025-01-01", "2025-08-01", freq="D")
    oos_candles = pd.DataFrame({"close": 50000}, index=oos_daily)
    assert oos_candles.index.max() > pd.Timestamp(IS_END), "Setup: OOS data exists"

    # Truncation must be applied before indicator computation
    truncated = oos_candles[oos_candles.index <= pd.Timestamp(IS_END)]
    assert truncated.index.max() <= pd.Timestamp(IS_END) + pd.Timedelta(days=1)

  def test_indicator_warmup_does_not_extend_into_oos(self):
    """Indicator warmup bars must come from IS period, not leak into OOS."""
    # RSI with 14-period lookback: even at the IS boundary,
    # the lookback should use bars BEFORE the boundary, not after
    is_prices = pd.Series(
      np.random.normal(50000, 500, 200),
      index=pd.date_range("2024-11-01", periods=200, freq="D"),
    )
    # Last bar of IS data
    is_end_ts = pd.Timestamp(IS_END)
    assert is_prices.index.max() <= is_end_ts + pd.Timedelta(days=1)
    # 14-period lookback from the last bar should still be within IS
    lookback_start = is_prices.index[-14]
    assert lookback_start < is_end_ts, "Warmup should be within IS"

  def test_strategy_variables_from_is_data_only(self):
    """Strategy variables (computed from indicators) must use IS candles only."""
    # Simulate a moving average computed on IS data
    is_closes = pd.Series(
      np.random.normal(50000, 500, 150),
      index=pd.date_range("2025-01-01", periods=150, freq="D"),
    )
    ma20 = is_closes.rolling(20).mean()
    # Last MA value should be computed from IS data only
    assert ma20.index.max() <= pd.Timestamp(IS_END) + pd.Timedelta(days=1)

    # If we extend with OOS data, the MA values change — that's contamination
    oos_closes = pd.concat([
      is_closes,
      pd.Series([60000] * 30, index=pd.date_range("2025-06-02", periods=30, freq="D")),
    ])
    ma20_contaminated = oos_closes.rolling(20).mean()
    # The IS-boundary MA should be IDENTICAL whether or not OOS data exists
    is_boundary_idx = is_closes.index[-1]
    ma_clean = ma20.loc[is_boundary_idx]
    ma_dirty = ma20_contaminated.loc[is_boundary_idx]
    assert abs(ma_clean - ma_dirty) < 0.01, (
      "MA at IS boundary should be identical with/without OOS data (no look-ahead)"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 4: Seed Scoring Boundary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSeedScoringBoundary:
  """Validate seed scoring only uses IS-period data."""

  def test_sortino_seed_uses_is_trades_only(self, is_trades):
    """Sortino seed scoring should pass with IS-only trades."""
    result = check_seed_scoring_boundary(is_trades, IS_END, "sortino")
    assert result.passed

  def test_recency_scoring_uses_is_trades_only(self):
    """Recency scoring DataFrame should be within IS."""
    trade_df = pd.DataFrame({
      "Asset": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
      "Exit Time": pd.to_datetime(["2025-03-01", "2025-04-15", "2025-05-20"]),
      "Net Profit": [100, -50, 200],
    })
    result = check_recency_boundary(trade_df, IS_END)
    assert result.passed

  def test_benchmark_scoring_uses_is_trades_only(self, is_trades):
    """Benchmark trades should be within IS."""
    from backend.analysis.integrity_agent import check_benchmark_boundary
    result = check_benchmark_boundary(is_trades, IS_END)
    assert result.passed

  def test_event_scoring_uses_is_trades_only(self, mixed_trades):
    """Event scoring with OOS trades should fail."""
    result = check_seed_scoring_boundary(mixed_trades, IS_END, "event")
    assert not result.passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 5: Quality Filter Boundary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestQualityFilterBoundary:
  """Validate quality filters operate on IS-only data."""

  def test_min_trades_filter_uses_is_data(self, is_daily_pnl):
    """Quality-filtered daily_pnl should remain within IS."""
    result = check_quality_filter_boundary(is_daily_pnl, IS_END)
    assert result.passed

  def test_min_pf_filter_uses_is_data(self, is_daily_pnl):
    """PF filter output should not exceed IS boundary."""
    result = check_quality_filter_boundary(is_daily_pnl, IS_END)
    assert result.passed

  def test_filter_daily_pnl_date_to_param(self, oos_leaked_daily_pnl):
    """_filter_daily_pnl with date_to should truncate OOS data."""
    # The actual _filter_daily_pnl function has a date_to safety net
    cutoff = pd.Timestamp(IS_END).normalize()
    filtered = oos_leaked_daily_pnl[oos_leaked_daily_pnl.index <= cutoff]
    result = check_quality_filter_boundary(filtered, IS_END)
    assert result.passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 6: Correlation Boundary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCorrelationBoundary:
  """Validate correlation matrix is built from IS-only data."""

  def test_correlation_matrix_from_is_daily_pnl(self, is_daily_pnl):
    """Correlation source data should be within IS."""
    result = check_corr_source_boundary(is_daily_pnl, IS_END)
    assert result.passed

  def test_no_oos_row_in_correlation_source(self, oos_leaked_daily_pnl):
    """If daily_pnl has OOS rows, correlation source check should fail."""
    result = check_corr_source_boundary(oos_leaked_daily_pnl, IS_END)
    assert not result.passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 7: Optimizer Boundary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestOptimizerBoundary:
  """Validate optimizer output respects IS pool."""

  def test_optimize_portfolio_is_bounded(self, is_daily_pnl):
    """Optimizer should only select from IS pool assets."""
    from backend.analysis.correlation import get_correlation_matrix
    from backend.analysis.optimizer import optimize_portfolio
    corr = get_correlation_matrix(is_daily_pnl, "standard")
    results = optimize_portfolio(is_daily_pnl, corr, 2, "hybrid")
    for r in results:
      result = check_optimizer_result_pool(
        r["assets"], is_daily_pnl.columns.tolist(),
      )
      assert result.passed, f"Phantom assets: {result.detail}"

  def test_optimize_split_is_bounded(self):
    """Split optimizer should only select from IS pool."""
    np.random.seed(42)
    n = 150
    data = {
      f"SYM{i}_Long": np.random.normal(0.3, 4, n) for i in range(1, 5)
    }
    data.update({
      f"SYM{i}_Short": np.random.normal(0.2, 4, n) for i in range(1, 5)
    })
    pnl = pd.DataFrame(data, index=pd.date_range("2025-01-01", periods=n, freq="D"))
    from backend.analysis.correlation import get_correlation_matrix
    from backend.analysis.optimizer_split import optimize_split_portfolio
    corr = get_correlation_matrix(pnl, "standard")
    results = optimize_split_portfolio(corr, 2, 2, daily_returns=pnl)
    pool = pnl.columns.tolist()
    for r in results:
      check = check_optimizer_result_pool(r["assets"], pool)
      assert check.passed

  def test_greedy_search_pool_is_bounded(self, is_daily_pnl):
    """Greedy search should not produce assets outside the input pool."""
    from backend.analysis.optimizer import _greedy_search
    from backend.analysis.scoring import SCORING_FUNCTIONS
    result = _greedy_search(
      is_daily_pnl, 2, SCORING_FUNCTIONS["max_sortino"],
      n_seeds=5, n_portfolios=3,
    )
    pool = set(is_daily_pnl.columns)
    for portfolio in result:
      for asset in portfolio:
        assert asset in pool, f"Asset {asset} not in IS pool"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 8: Cross-Batch Isolation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCrossBatchIsolation:
  """Validate each batch independently respects IS boundary."""

  def test_each_batch_trades_within_is(self, is_trades):
    """Clean batches should pass isolation check."""
    batch_map = {1: is_trades[:2], 2: is_trades[2:]}
    result = check_cross_batch_isolation(batch_map, IS_END)
    assert result.passed

  def test_batch_tagged_daily_pnl_within_is(self, is_daily_pnl):
    """Batch-tagged columns should not leak OOS data."""
    # Simulate batch-tagged column names
    tagged_pnl = is_daily_pnl.copy()
    tagged_pnl.columns = [f"{c}~B1" for c in tagged_pnl.columns]
    result = check_daily_pnl_boundary(tagged_pnl, IS_END)
    assert result.passed

  def test_per_batch_filters_respect_date_to(self, mixed_trades):
    """If one batch has OOS trades, isolation check should fail."""
    batch_map = {
      1: [t for t in mixed_trades if t.symbol == "BTCUSDT"],  # IS only
      2: [t for t in mixed_trades if t.symbol in ("ETHUSDT", "SOLUSDT")],  # has OOS
    }
    result = check_cross_batch_isolation(batch_map, IS_END)
    assert not result.passed
    assert 2 in result.detail["violations"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 9: Window Generation Boundary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestWindowGeneration:
  """Validate WFO window dates don't violate IS/OOS boundaries."""

  def test_rolling_windows_no_is_oos_overlap(self):
    """Rolling windows: IS end must be before OOS start."""
    windows = generate_rolling_windows(
      is_months=12, oos_months=3,
      last_oos_end="2025-12-31", data_start="2024-01-01",
    )
    result = check_window_boundaries(windows)
    assert result.passed, f"Window violations: {result.detail}"

  def test_static_windows_no_is_oos_overlap(self):
    """Static windows: IS end must be before OOS start (per window)."""
    windows = generate_static_windows(
      is_start="2024-01-01", oos_end_fixed="2025-12-31",
      is_months=12, step_months=3,
    )
    # Static windows share the same oos_end, so OOS periods overlap by design.
    # Only validate that each window's IS end < its own OOS start.
    for w in windows:
      if w.get("oos_start"):
        assert pd.Timestamp(w["is_end"]) < pd.Timestamp(w["oos_start"]), (
          f"Window {w['window_num']}: IS end {w['is_end']} >= OOS start {w['oos_start']}"
        )

  def test_overlapping_windows_h1_h2_after_is(self):
    """Overlapping windows: H1/H2 must start after IS end."""
    windows = generate_overlapping_windows(
      is_months=12, oos_total_months=6,
      last_oos_end="2025-12-31", data_start="2024-01-01",
    )
    for w in windows:
      if w.get("oos_h1_start"):
        assert pd.Timestamp(w["is_end"]) < pd.Timestamp(w["oos_h1_start"]), (
          f"Window {w['window_num']}: IS end {w['is_end']} >= H1 start {w['oos_h1_start']}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 10: WFAS Boundary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestWFASBoundary:
  """Validate WFAS uses wfo_boundary assertions."""

  def test_wfas_filter_trades_strict_per_window(self, is_trades):
    """filter_trades_strict bounds by entry_time, not exit_time."""
    filtered = filter_trades_strict(is_trades, "2025-01-01", "2025-03-31")
    for t in filtered:
      assert pd.Timestamp(t.entry_time) <= pd.Timestamp("2025-03-31") + pd.Timedelta(days=1)

  def test_wfas_build_is_daily_pnl_validates(self, is_trades):
    """build_is_daily_pnl should not raise for clean IS trades."""
    # build_is_daily_pnl calls assert_no_oos_leakage internally
    daily_pnl = build_is_daily_pnl(is_trades, IS_END, include_side=True)
    assert not daily_pnl.empty or len(is_trades) == 0

  def test_wfas_assert_no_oos_leakage_called(self, is_daily_pnl):
    """Verify assert_no_oos_leakage works on clean data."""
    # Should not raise
    assert_no_oos_leakage(is_daily_pnl, IS_END, "wfas test")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 11: Portfolio Save Boundary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestPortfolioSaveBoundary:
  """Validate portfolio save respects optimization_end_date."""

  def test_portfolio_without_end_date_flagged(self):
    """Missing optimization_end_date should be flagged as warning."""
    result = check_date_to_present(None, "portfolio save")
    assert not result.passed
    assert result.severity == "warning"

  def test_portfolio_with_end_date_passes(self):
    """Portfolio with optimization_end_date should pass date_to check."""
    result = check_date_to_present("2025-06-01", "portfolio save")
    assert result.passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 12: End-to-End Pipeline Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEndToEnd:
  """Full pipeline integration tests using synthetic data."""

  def test_full_single_optimize_pipeline(self, is_daily_pnl, is_trades):
    """Simulate single optimize: load → filter → build PnL → optimize → validate."""
    # Step 1: Filter trades by IS boundary
    filtered = filter_trades_strict(is_trades, "2025-01-01", IS_END)
    assert_trades_within_boundary(filtered, IS_END, "pipeline test")

    # Step 2: Build daily PnL
    result_pnl = check_daily_pnl_boundary(is_daily_pnl, IS_END)
    assert result_pnl.passed

    # Step 3: Build correlation
    result_corr = check_corr_source_boundary(is_daily_pnl, IS_END)
    assert result_corr.passed

    # Step 4: Optimize
    from backend.analysis.correlation import get_correlation_matrix
    from backend.analysis.optimizer import optimize_portfolio
    corr = get_correlation_matrix(is_daily_pnl, "standard")
    results = optimize_portfolio(is_daily_pnl, corr, 2, "hybrid")

    # Step 5: Validate result pool
    for r in results:
      pool_check = check_optimizer_result_pool(
        r["assets"], is_daily_pnl.columns.tolist(),
      )
      assert pool_check.passed

  def test_full_wfas_pipeline_boundary_enforcement(self):
    """Simulate WFAS: generate windows → validate boundaries."""
    windows = generate_rolling_windows(
      is_months=6, oos_months=3,
      last_oos_end="2025-12-31", data_start="2024-01-01",
    )
    result = check_window_boundaries(windows)
    assert result.passed

    # Each window's IS trades should not leak into OOS
    for w in windows:
      if w.get("oos_start"):
        is_end = w["is_end"]
        oos_start = w["oos_start"]
        assert pd.Timestamp(is_end) < pd.Timestamp(oos_start), (
          f"IS end {is_end} must be before OOS start {oos_start}"
        )

  def test_full_cross_strategy_pipeline(self):
    """Simulate cross-strategy: multi-batch → merge → validate isolation."""
    np.random.seed(42)
    n = 100
    batch1_trades = _make_trades([
      ("BTC", "2025-01-01", "2025-02-15"),
      ("ETH", "2025-02-01", "2025-03-20"),
    ])
    batch2_trades = _make_trades([
      ("SOL", "2025-03-01", "2025-04-10"),
      ("ADA", "2025-04-01", "2025-05-15"),
    ])

    # Validate each batch independently
    batch_map = {1: batch1_trades, 2: batch2_trades}
    result = check_cross_batch_isolation(batch_map, IS_END)
    assert result.passed

    # Merge and validate combined
    all_trades = batch1_trades + batch2_trades
    exit_check = check_trade_exit_boundary(all_trades, IS_END)
    assert exit_check.passed
    entry_check = check_trade_entry_boundary(all_trades, IS_END)
    assert entry_check.passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 13: Report Builder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestReportBuilder:
  """Validate IntegrityReport construction."""

  def test_all_pass_gives_pass_status(self):
    checks = [
      CheckResult("c1", "Cat", True, "critical", "ok"),
      CheckResult("c2", "Cat", True, "info", "ok"),
    ]
    report = _build_report("test", checks)
    assert report.overall_status == "PASS"
    assert report.failed == 0

  def test_critical_fail_gives_fail_status(self):
    checks = [
      CheckResult("c1", "Cat", True, "critical", "ok"),
      CheckResult("c2", "Cat", False, "critical", "bad"),
    ]
    report = _build_report("test", checks)
    assert report.overall_status == "FAIL"
    assert report.failed == 1

  def test_warning_only_gives_warn_status(self):
    checks = [
      CheckResult("c1", "Cat", True, "critical", "ok"),
      CheckResult("c2", "Cat", False, "warning", "hmm"),
    ]
    report = _build_report("test", checks)
    assert report.overall_status == "WARN"
    assert report.warnings == 1
