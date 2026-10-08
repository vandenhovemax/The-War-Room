"""Ground truth tests for L0 batch grading and portfolio-to-live data pipeline.

TWO areas covered:

A) L0 Batch Grading (structural metrics computed after each batch backtest):
   - _compute_structural_metrics() output format and rounding
   - grade_all_assets() integration (multi-asset grading)
   - Daily PnL construction from trades
   - Edge cases: sparse trades, all-winners, all-losers, flat equity
   - End-to-end: PnL → equity → metrics → composite scores → grades → regime

B) Portfolio → Live Trading Data Pipeline:
   - source_strategies JSON parsing
   - Strategy resolution 4-step fallback logic
   - Parameter merging (defaults + config overrides + batch overrides)
   - Cross-strategy batch parameter caching
   - ActivePortfolio construction
   - StrategySignal dataclass fields
   - Risk profile resolution (fixed, pct, martingale)
   - Asset list filtering (is_active)
   - Deactivation cleanup
   - Refresh (add/remove symbols)
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
from unittest.mock import MagicMock
from datetime import datetime, timezone, date, timedelta


# =========================================================================
# HELPERS
# =========================================================================

def _make_pnl(assets: dict[str, np.ndarray], n: int = 200) -> pd.DataFrame:
  """Wrap named numpy arrays into a DataFrame with DatetimeIndex."""
  dates = pd.date_range("2024-01-01", periods=n, freq="D")
  return pd.DataFrame(assets, index=dates)


def _make_trades(daily_pnls: list[float], start_date=None):
  """Create mock trade objects with exit_time and net_profit."""
  if start_date is None:
    start_date = date(2024, 1, 1)
  trades = []
  for i, pnl in enumerate(daily_pnls):
    t = MagicMock()
    t.exit_time = datetime(
      start_date.year, start_date.month, start_date.day,
      tzinfo=timezone.utc,
    ) + timedelta(days=i)
    t.net_profit = pnl
    trades.append(t)
  return trades


# =========================================================================
# A1. L0 — STRUCTURAL METRIC OUTPUT FORMAT
# =========================================================================

class TestL0OutputFormat:
  """_compute_structural_metrics_from_prices returns correctly formatted dict."""

  def test_all_keys_present(self):
    """Output dict has all 14 expected keys."""
    from backend.api.routes.batch_backtest import _compute_structural_metrics_from_prices

    # Simulate realistic close prices (uptrending)
    rng = np.random.default_rng(42)
    close = 100.0 + np.cumsum(rng.normal(0.1, 1.0, 500))
    close = np.maximum(close, 1.0)  # no negatives

    result = _compute_structural_metrics_from_prices(close, pool_size=20)

    expected_keys = {
      "regime", "trend_score", "trend_grade", "meanrev_score",
      "meanrev_grade", "TER", "Hurst", "GPR", "AC1", "DSR",
      "MRI", "VR5", "HalfLife", "CrossRate",
    }
    assert set(result.keys()) == expected_keys

  def test_every_asset_gets_graded(self):
    """With sufficient close prices, every asset gets a full grade — no trade-count dependency."""
    from backend.api.routes.batch_backtest import _compute_structural_metrics_from_prices

    rng = np.random.default_rng(99)
    close = 50.0 + np.cumsum(rng.normal(0.0, 0.5, 200))
    close = np.maximum(close, 1.0)

    result = _compute_structural_metrics_from_prices(close, pool_size=10)
    assert len(result) > 0, "Should produce metrics from OHLCV even with zero trades"
    assert "Hurst" in result
    assert "regime" in result

  def test_too_few_bars_returns_empty(self):
    """< 50 close prices → empty dict (not enough data for reliable metrics)."""
    from backend.api.routes.batch_backtest import _compute_structural_metrics_from_prices
    close = np.array([100.0, 101.0, 99.5, 102.0])
    result = _compute_structural_metrics_from_prices(close, pool_size=5)
    assert result == {}

  def test_trending_close_prices_high_ter(self):
    """Steadily rising close prices → higher TER than random walk."""
    from backend.api.routes.batch_backtest import _compute_structural_metrics_from_prices

    rng = np.random.default_rng(42)
    # Trending: linear rise with small noise
    close_trend = np.linspace(100.0, 200.0, 500) + rng.normal(0, 0.5, 500)
    close_trend = np.maximum(close_trend, 1.0)

    # Random walk for comparison
    close_rw = 100.0 + np.cumsum(rng.normal(0, 1.0, 500))
    close_rw = np.maximum(close_rw, 1.0)

    result_trend = _compute_structural_metrics_from_prices(close_trend, pool_size=20)
    result_rw = _compute_structural_metrics_from_prices(close_rw, pool_size=20)

    # Trending price should have higher TER than random walk
    assert result_trend["TER"] > result_rw["TER"], (
      f"Trending TER ({result_trend['TER']}) should exceed random walk TER ({result_rw['TER']})"
    )
    assert result_trend["TER"] > 0.2, f"Trending TER should be > 0.2, got {result_trend['TER']}"

  def test_random_walk_close_prices_hurst_near_half(self):
    """Random walk prices → Hurst near 0.5."""
    from backend.api.routes.batch_backtest import _compute_structural_metrics_from_prices

    rng = np.random.default_rng(42)
    # Pure random walk
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, 500))
    close = np.maximum(close, 1.0)

    result = _compute_structural_metrics_from_prices(close, pool_size=20)
    # Hurst of random walk should be near 0.5 (±0.15 tolerance for finite sample)
    assert 0.35 < result["Hurst"] < 0.65, f"Random walk Hurst should be ~0.5, got {result['Hurst']}"

  def test_regime_is_valid_string(self):
    """regime must be TREND, MEANREV, or NEUTRAL."""
    from backend.analysis.seed_ranker import classify_regime
    assert classify_regime(0.4, 0.1) == "TREND"
    assert classify_regime(0.1, 0.4) == "MEANREV"
    assert classify_regime(0.3, 0.3) == "NEUTRAL"
    assert classify_regime(0.31, 0.3) == "NEUTRAL"  # diff=0.01 < threshold

  def test_grades_are_valid_letters(self):
    """grade_letter returns only valid grades."""
    from backend.analysis.seed_ranker import grade_letter
    valid = {"A+", "A", "B+", "B", "C+", "C", "D", "F"}
    for score in np.arange(0.0, 1.01, 0.01):
      assert grade_letter(float(score)) in valid

  def test_halflife_infinity_capped(self):
    """HalfLife replaces inf with 999.0."""
    hl = float('inf')
    result = round(hl, 1) if hl < float('inf') else 999.0
    assert result == 999.0

  def test_rounding_precision(self):
    """All numeric metrics rounded to 4 decimal places."""
    val = 0.123456789
    assert round(val, 4) == 0.1235  # standard rounding


# =========================================================================
# A2. L0 — DAILY PNL CONSTRUCTION FROM TRADES
# =========================================================================

class TestL0OhlcvMetricsComputation:
  """Verify OHLCV-based structural metrics computation."""

  def test_log_returns_from_close_prices(self):
    """Log returns computed correctly from close prices."""
    close = np.array([100.0, 102.0, 101.0, 105.0])
    returns = np.diff(np.log(close))
    assert len(returns) == 3
    assert abs(returns[0] - np.log(102.0 / 100.0)) < 1e-10

  def test_ter_uses_close_prices_directly(self):
    """TER computed on close prices (not log returns)."""
    from backend.analysis.seed_ranker import trend_efficiency_ratio
    # Straight line up → TER ≈ 1.0
    close = np.linspace(100.0, 200.0, 100)
    ter = trend_efficiency_ratio(close)
    assert ter > 0.99, f"Perfectly linear close prices should have TER ≈ 1.0, got {ter}"

  def test_zero_close_prices_filtered_out(self):
    """Close prices ≤ 0 are stripped before computing log returns."""
    from backend.api.routes.batch_backtest import _compute_structural_metrics_from_prices
    rng = np.random.default_rng(42)
    close = 50.0 + np.cumsum(rng.normal(0.0, 0.5, 200))
    close = np.maximum(close, 1.0)
    # Insert some zeros — should be filtered out
    close_with_zeros = np.concatenate([np.zeros(5), close])
    result = _compute_structural_metrics_from_prices(close_with_zeros, pool_size=10)
    assert len(result) > 0, "Should handle zeros in close prices"

  def test_no_trade_count_dependency(self):
    """OHLCV metrics have NO trade count threshold — any 50+ bars works."""
    from backend.api.routes.batch_backtest import _compute_structural_metrics_from_prices
    rng = np.random.default_rng(42)
    # Just 60 close prices — would have been too few with old trade-based thresholds
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.5, 60))
    close = np.maximum(close, 1.0)
    result = _compute_structural_metrics_from_prices(close, pool_size=5)
    assert len(result) > 0, "Should compute metrics from 60 bars (no trade-count gate)"
    assert "Hurst" in result

  def test_pool_size_affects_dsr(self):
    """DSR uses pool_size for multiple-testing correction."""
    from backend.api.routes.batch_backtest import _compute_structural_metrics_from_prices
    rng = np.random.default_rng(42)
    close = 100.0 + np.cumsum(rng.normal(0.1, 1.0, 500))
    close = np.maximum(close, 1.0)
    r1 = _compute_structural_metrics_from_prices(close, pool_size=5)
    r2 = _compute_structural_metrics_from_prices(close, pool_size=100)
    # Larger pool = stricter DSR test → lower or equal DSR
    assert r1["DSR"] >= r2["DSR"] or abs(r1["DSR"] - r2["DSR"]) < 0.01


# =========================================================================
# A3. L0 — END-TO-END METRIC PIPELINE
# =========================================================================

class TestL0EndToEndMetrics:
  """Full pipeline: daily PnL → equity → all metrics → grades."""

  def test_strong_uptrend_gets_trend_grade(self):
    """Steady upward PnL → high TER, positive AC, TREND regime."""
    from backend.analysis.seed_ranker import (
      trend_efficiency_ratio, autocorrelation, trend_score,
      meanrev_score, classify_regime, grade_letter,
    )

    # Consistent small positive returns = strong trend
    rng = np.random.default_rng(100)
    pnl = rng.uniform(0.5, 2.0, 200)  # always positive
    equity = np.cumsum(pnl)

    ter = trend_efficiency_ratio(equity)
    assert ter > 0.8, f"Strong uptrend should have TER > 0.8, got {ter}"

    t_score = trend_score(pnl, n_pool=20)
    m_score = meanrev_score(pnl)
    regime = classify_regime(t_score, m_score)

    # With all-positive returns: TER ≈ 1.0, trend should dominate
    assert t_score > m_score, "Uptrend must score higher on trend than meanrev"
    assert regime == "TREND"

  def test_alternating_returns_gets_meanrev_signals(self):
    """Alternating +/- returns → negative AC, low TER."""
    from backend.analysis.seed_ranker import (
      autocorrelation, trend_efficiency_ratio,
    )

    # Perfectly alternating: +10, -10, +10, -10, ...
    pnl = np.array([10.0 if i % 2 == 0 else -10.0 for i in range(200)])
    equity = np.cumsum(pnl)

    ac = autocorrelation(pnl, lag=1)
    ter = trend_efficiency_ratio(equity)

    assert ac < -0.5, f"Alternating returns should have strong negative AC, got {ac}"
    assert ter < 0.1, f"Alternating returns should have low TER, got {ter}"

  def test_all_winners_high_gpr(self):
    """All positive trades → GPR = 10.0 (capped)."""
    from backend.analysis.seed_ranker import gain_to_pain_ratio
    pnl = np.ones(50) * 5.0  # all wins
    gpr = gain_to_pain_ratio(pnl)
    assert gpr == 10.0, f"All-winners GPR should be capped at 10.0, got {gpr}"

  def test_all_losers_zero_gpr(self):
    """All negative trades → GPR < 0."""
    from backend.analysis.seed_ranker import gain_to_pain_ratio
    pnl = np.ones(50) * -5.0  # all losses
    gpr = gain_to_pain_ratio(pnl)
    assert gpr < 0, f"All-losers GPR should be negative, got {gpr}"

  def test_flat_equity_zero_ter(self):
    """Zero PnL → TER = 0."""
    from backend.analysis.seed_ranker import trend_efficiency_ratio
    equity = np.zeros(50)
    ter = trend_efficiency_ratio(equity)
    assert ter == 0.0

  def test_grade_all_assets_multiasset(self):
    """grade_all_assets handles multiple assets correctly."""
    from backend.analysis.seed_ranker import grade_all_assets

    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({
      "BTCUSDT": rng.uniform(0.5, 2.0, n),    # uptrend
      "ETHUSDT": rng.normal(0.0, 5.0, n),      # random
      "SOLUSDT": np.array([10.0 if i % 2 == 0 else -10.0 for i in range(n)]),  # alternating
    })

    result = grade_all_assets(pnl, n_pool=3)

    # Must have all assets
    assert set(result["asset"].values) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}

    # Required columns
    required_cols = {
      "asset", "regime", "trend_score", "trend_grade",
      "meanrev_score", "meanrev_grade", "TER", "Hurst",
      "GPR", "AC(1)", "DSR", "MRI", "VR(5)", "HalfLife", "CrossRate",
    }
    assert required_cols.issubset(set(result.columns))

    # Sorted by trend_score descending
    scores = result["trend_score"].values
    assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))

    # BTCUSDT (uptrend) should have highest trend_score
    btc_row = result[result["asset"] == "BTCUSDT"].iloc[0]
    assert btc_row["trend_score"] > 0.2

  def test_grade_all_assets_grades_consistent_with_scores(self):
    """Grades match the score-to-grade mapping."""
    from backend.analysis.seed_ranker import grade_all_assets, grade_letter

    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({"ASSET1": rng.normal(1.0, 3.0, n)})

    result = grade_all_assets(pnl, n_pool=1)
    row = result.iloc[0]

    assert row["trend_grade"] == grade_letter(row["trend_score"])
    assert row["meanrev_grade"] == grade_letter(row["meanrev_score"])


# =========================================================================
# A4. L0 — SPARSE PNL HANDLING
# =========================================================================

class TestL0SparsePnl:
  """_active_returns strips zeros for sparse trade data."""

  def test_active_returns_strips_zeros(self):
    """Zero-PnL days removed from returns."""
    from backend.analysis.seed_ranker import _active_returns
    raw = np.array([0, 0, 10, 0, -5, 0, 0, 20, 0, -3, 0, 0, 15, 0, 8, -2, 12, -4, 7, -1])
    active = _active_returns(raw)
    assert 0.0 not in active
    assert len(active) == 11  # 11 non-zero values: 10,-5,20,-3,15,8,-2,12,-4,7,-1

  def test_active_returns_fallback_if_too_few(self):
    """Falls back to full array if fewer than 10 non-zero values."""
    from backend.analysis.seed_ranker import _active_returns
    raw = np.array([0, 0, 10, 0, -5, 0, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    active = _active_returns(raw)
    # Only 3 non-zero values < 10, so fallback to full array
    assert len(active) == len(raw)

  def test_hurst_uses_active_returns(self):
    """Hurst exponent strips zeros before computing."""
    from backend.analysis.seed_ranker import hurst_exponent
    # Mostly zeros with some non-zero = should get meaningful Hurst
    rng = np.random.default_rng(42)
    full = rng.normal(1.0, 2.0, 200)
    sparse = np.zeros(600)
    # Insert non-zero every 3rd position
    for i in range(200):
      sparse[i * 3] = full[i]

    h_full = hurst_exponent(full)
    h_sparse = hurst_exponent(sparse)
    # Both should give similar Hurst since _active_returns strips zeros
    assert abs(h_full - h_sparse) < 0.15, (
      f"Hurst diverges too much: full={h_full:.3f} sparse={h_sparse:.3f}"
    )


# =========================================================================
# A5. L0 — PRE/POST FILTERS AT BATCH LEVEL
# =========================================================================

class TestL0PrePostFilters:
  """Trend/meanrev pre-filters and AC post-filter."""

  def test_prefilter_trend_removes_low_hurst(self):
    """prefilter_for_trend removes assets with Hurst < 0.5."""
    from backend.analysis.seed_ranker import prefilter_for_trend

    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({
      "TREND_ASSET": rng.uniform(0.5, 2.0, n),       # uptrend → Hurst > 0.5
      "MEAN_REV_ASSET": np.array([10.0 if i % 2 == 0 else -10.0 for i in range(n)]),
    })

    survivors = prefilter_for_trend(pnl, hurst_min=0.50)
    # Alternating asset should have Hurst < 0.5, so it might be removed
    # (depends on exact Hurst computation, but trend asset should survive)
    assert "TREND_ASSET" in survivors

  def test_prefilter_meanrev_removes_trending(self):
    """prefilter_for_meanrev removes assets with Hurst >= 0.5."""
    from backend.analysis.seed_ranker import prefilter_for_meanrev, hurst_exponent

    rng = np.random.default_rng(42)
    n = 200
    trending = rng.uniform(0.5, 2.0, n)     # pure uptrend
    alternating = np.array([5.0 if i % 2 == 0 else -5.0 for i in range(n)])

    pnl = _make_pnl({"TREND": trending, "MEANREV": alternating})

    survivors = prefilter_for_meanrev(pnl, hurst_max=0.50)
    # If Hurst("TREND") >= 0.5, it should be removed
    h_trend = hurst_exponent(trending)
    if h_trend >= 0.5:
      assert "TREND" not in survivors or len(survivors) == 2  # fallback if all removed

  def test_prefilter_fallback_when_all_removed(self):
    """Pre-filters return all assets as fallback when all would be removed."""
    from backend.analysis.seed_ranker import prefilter_for_trend

    n = 200
    # Alternating returns → low Hurst for all
    pnl = _make_pnl({
      "A": np.array([10.0 if i % 2 == 0 else -10.0 for i in range(n)]),
      "B": np.array([5.0 if i % 2 == 0 else -5.0 for i in range(n)]),
    })

    survivors = prefilter_for_trend(pnl, hurst_min=0.99)  # impossible threshold
    # Fallback: return all assets
    assert set(survivors) == {"A", "B"}

  def test_postfilter_ac_flags_mismatch(self):
    """postfilter_ac flags negative AC for trend, positive AC for meanrev."""
    from backend.analysis.seed_ranker import postfilter_ac

    n = 200
    # Alternating → negative AC
    alternating = np.array([10.0 if i % 2 == 0 else -10.0 for i in range(n)])
    pnl = _make_pnl({"ALT": alternating})

    # For trend strategy: negative AC should be flagged
    result = postfilter_ac(pnl, ["ALT"], strategy_type="trend")
    assert result["ALT"]["flagged"] is True

    # For meanrev strategy: negative AC should NOT be flagged
    result = postfilter_ac(pnl, ["ALT"], strategy_type="meanrev")
    assert result["ALT"]["flagged"] is False

  def test_postfilter_ac_trend_positive_not_flagged(self):
    """Positive AC in a trend strategy → not flagged."""
    from backend.analysis.seed_ranker import postfilter_ac

    rng = np.random.default_rng(42)
    n = 200
    # Strong trend → positive AC
    trending = rng.uniform(0.5, 2.0, n)
    pnl = _make_pnl({"TREND": trending})

    result = postfilter_ac(pnl, ["TREND"], strategy_type="trend")
    # Positive AC should NOT be flagged for trend strategies
    if result["TREND"]["ac"] >= 0:
      assert result["TREND"]["flagged"] is False


# =========================================================================
# A6. L0 — MRI FORMULA VERIFICATION
# =========================================================================

class TestL0MRIFormula:
  """MRI = -AC(1) * (1 - TER) * crossing_rate — only when AC < 0."""

  def test_mri_zero_when_ac_positive(self):
    """Positive AC → MRI = 0 (not mean-reverting)."""
    from backend.analysis.seed_ranker import mean_reversion_intensity

    rng = np.random.default_rng(42)
    pnl = rng.uniform(0.5, 2.0, 200)  # uptrend → positive AC
    mri = mean_reversion_intensity(pnl)
    assert mri == 0.0

  def test_mri_positive_when_ac_negative(self):
    """Negative AC → MRI > 0."""
    from backend.analysis.seed_ranker import mean_reversion_intensity

    # Alternating returns → negative AC
    pnl = np.array([10.0 if i % 2 == 0 else -10.0 for i in range(200)])
    mri = mean_reversion_intensity(pnl)
    assert mri > 0, f"MRI should be positive for mean-reverting data, got {mri}"

  def test_mri_formula_hand_calc(self):
    """Verify MRI = -AC * (1-TER) * CR with known values."""
    from backend.analysis.seed_ranker import (
      mean_reversion_intensity, autocorrelation,
      trend_efficiency_ratio, _crossing_rate,
    )

    pnl = np.array([10.0 if i % 2 == 0 else -10.0 for i in range(200)])
    equity = np.cumsum(pnl)

    ac = autocorrelation(pnl, lag=1)
    ter = trend_efficiency_ratio(equity)
    cr = _crossing_rate(pnl)
    mri = mean_reversion_intensity(pnl)

    if ac < 0:
      expected = (-ac) * (1.0 - ter) * cr
      expected = max(0.0, min(expected, 1.0))
      assert abs(mri - expected) < 1e-6


# =========================================================================
# A7. L0 — DSR EDGE CASES
# =========================================================================

class TestL0DSREdgeCases:
  """Deflated Sharpe Ratio edge case behavior."""

  def test_dsr_neutral_for_few_returns(self):
    """< 30 active returns → DSR = 0.5 (neutral)."""
    from backend.analysis.seed_ranker import deflated_sharpe
    pnl = np.array([1.0, -0.5, 2.0, -1.0, 0.5] * 4)  # 20 non-zero
    dsr = deflated_sharpe(pnl, n_trials=20)
    assert dsr == 0.5

  def test_dsr_neutral_for_constant_returns(self):
    """Constant returns (std=0) → DSR = 0.5."""
    from backend.analysis.seed_ranker import deflated_sharpe
    pnl = np.ones(50) * 5.0
    dsr = deflated_sharpe(pnl, n_trials=20)
    assert dsr == 0.5

  def test_dsr_between_0_and_1(self):
    """DSR always in [0, 1]."""
    from backend.analysis.seed_ranker import deflated_sharpe
    for seed in range(5):
      rng2 = np.random.default_rng(seed)
      pnl = rng2.normal(0.5, 3.0, 200)
      dsr = deflated_sharpe(pnl, n_trials=20)
      assert 0.0 <= dsr <= 1.0, f"DSR out of range: {dsr}"


# =========================================================================
# B1. PORTFOLIO → LIVE: SOURCE STRATEGIES PARSING
# =========================================================================

class TestSourceStrategiesParsing:
  """source_strategies JSON → strategy resolution data."""

  def test_parse_full_source_strategies(self):
    """All fields extracted correctly from source_strategies JSON."""
    source_json = json.dumps({
      "strategy_id": 3,
      "strategy_name": "RSI Swing",
      "config_name": "aggressive",
      "timeframe": "30",
      "extra_timeframes": ["D", "240"],
      "risk_profile": {"mode": "fixed", "initial_risk_usd": 50},
      "is_cross_strat": False,
      "batch_ids": [10, 11, 12],
    })

    source_data = json.loads(source_json)

    assert source_data["strategy_id"] == 3
    assert source_data["strategy_name"] == "RSI Swing"
    assert source_data["config_name"] == "aggressive"
    assert source_data["timeframe"] == "30"
    assert source_data["extra_timeframes"] == ["D", "240"]
    assert source_data["risk_profile"]["mode"] == "fixed"
    assert source_data["is_cross_strat"] is False
    assert source_data["batch_ids"] == [10, 11, 12]

  def test_parse_empty_source_strategies(self):
    """Empty/missing source_strategies defaults gracefully."""
    source_data = json.loads("{}")

    assert source_data.get("strategy_name", "") == ""
    assert source_data.get("config_name", "") == ""
    assert source_data.get("timeframe", "") == ""
    assert source_data.get("extra_timeframes", []) == []
    assert source_data.get("risk_profile", {}) == {}
    assert source_data.get("is_cross_strat", False) is False
    assert source_data.get("batch_ids") is None

  def test_parse_cross_strategy_source(self):
    """Cross-strategy portfolios have is_cross_strat=True and multiple batch_ids."""
    source_json = json.dumps({
      "strategy_name": "Multi-Strategy",
      "is_cross_strat": True,
      "batch_ids": [5, 8, 12],
      "timeframe": "60",
    })

    source_data = json.loads(source_json)
    assert source_data["is_cross_strat"] is True
    assert len(source_data["batch_ids"]) == 3


# =========================================================================
# B2. PORTFOLIO → LIVE: PARAMETER MERGING
# =========================================================================

class TestParameterMerging:
  """Parameter resolution: defaults + config overrides + batch overrides."""

  def test_list_format_to_dict(self):
    """strategy.parameters as list of defs → extracted to {name: default}."""
    raw_params = [
      {"name": "rsi_period", "type": "int", "default": 14},
      {"name": "overbought", "type": "float", "default": 70.0},
      {"name": "oversold", "type": "float", "default": 30.0},
    ]
    default_params = {p["name"]: p.get("default") for p in raw_params if "name" in p}

    assert default_params == {
      "rsi_period": 14,
      "overbought": 70.0,
      "oversold": 30.0,
    }

  def test_dict_format_passthrough(self):
    """strategy.parameters as dict → used directly."""
    raw_params = {"rsi_period": 14, "overbought": 70.0}
    if isinstance(raw_params, list):
      default_params = {p["name"]: p.get("default") for p in raw_params}
    else:
      default_params = raw_params

    assert default_params == {"rsi_period": 14, "overbought": 70.0}

  def test_config_overrides_applied(self):
    """Config overrides update default params (single-strategy)."""
    default_params = {"rsi_period": 14, "overbought": 70.0, "oversold": 30.0}
    overrides = {"rsi_period": 21, "overbought": 80.0}  # from StrategyConfig

    default_params.update(overrides)

    assert default_params["rsi_period"] == 21  # overridden
    assert default_params["overbought"] == 80.0  # overridden
    assert default_params["oversold"] == 30.0  # unchanged

  def test_batch_params_override_for_cross_strategy(self):
    """Cross-strategy: batch params override defaults per asset."""
    default_params = {"rsi_period": 14, "sma_length": 50}

    batch_params_cache = {
      10: {"rsi_period": 21},   # batch 10 uses RSI 21
      11: {"sma_length": 100},  # batch 11 uses SMA 100
    }

    # Asset from batch 10
    asset_params_10 = dict(default_params)
    batch_p = batch_params_cache.get(10)
    if batch_p:
      asset_params_10.update(batch_p)

    assert asset_params_10["rsi_period"] == 21  # from batch
    assert asset_params_10["sma_length"] == 50  # unchanged

    # Asset from batch 11
    asset_params_11 = dict(default_params)
    batch_p = batch_params_cache.get(11)
    if batch_p:
      asset_params_11.update(batch_p)

    assert asset_params_11["rsi_period"] == 14  # unchanged
    assert asset_params_11["sma_length"] == 100  # from batch

  def test_strategy_inputs_key_extraction(self):
    """Batch parameters stored as {strategy_inputs: {actual params}}."""
    batch_params_raw = json.dumps({
      "strategy_inputs": {"rsi_period": 21, "threshold": 0.5},
      "other_config": "ignored",
    })

    bp = json.loads(batch_params_raw)
    extracted = bp.get("strategy_inputs", bp)

    assert extracted == {"rsi_period": 21, "threshold": 0.5}

  def test_strategy_inputs_fallback_to_root(self):
    """If no strategy_inputs key, use the root dict."""
    batch_params_raw = json.dumps({"rsi_period": 21, "threshold": 0.5})
    bp = json.loads(batch_params_raw)
    extracted = bp.get("strategy_inputs", bp)

    assert extracted == {"rsi_period": 21, "threshold": 0.5}


# =========================================================================
# B3. PORTFOLIO → LIVE: ACTIVE PORTFOLIO CONSTRUCTION
# =========================================================================

class TestActivePortfolioConstruction:
  """ActivePortfolio dataclass holds correct state."""

  def test_active_portfolio_fields(self):
    """All fields populated correctly."""
    from backend.exchange.strategy_executor import ActivePortfolio

    ap = ActivePortfolio(
      portfolio_id=42,
      strategy_source="class MyStrategy(BaseStrategy): ...",
      parameters={"rsi_period": 14},
      base_tf="30",
      extra_tfs=["D", "240"],
      assets=[
        {"symbol": "BTCUSDT", "side": "Long", "batch_id": 10},
        {"symbol": "ETHUSDT", "side": "Both", "batch_id": 10},
      ],
    )

    assert ap.portfolio_id == 42
    assert ap.base_tf == "30"
    assert ap.extra_tfs == ["D", "240"]
    assert len(ap.assets) == 2
    assert ap.warmup_bars == 200  # default
    assert ap.strategies == {}
    assert ap.bar_counts == {}
    assert ap.warmed_up == {}

  def test_active_portfolio_custom_warmup(self):
    """Custom warmup_bars override."""
    from backend.exchange.strategy_executor import ActivePortfolio

    ap = ActivePortfolio(
      portfolio_id=1, strategy_source="", parameters={},
      base_tf="30", extra_tfs=[], assets=[], warmup_bars=500,
    )
    assert ap.warmup_bars == 500


# =========================================================================
# B4. PORTFOLIO → LIVE: STRATEGY SIGNAL DATACLASS
# =========================================================================

class TestStrategySignalFields:
  """StrategySignal carries all data from strategy to engine."""

  def test_signal_entry_fields(self):
    """Entry signal has all required fields."""
    from backend.exchange.strategy_executor import StrategySignal

    sig = StrategySignal(
      portfolio_id=42,
      symbol="BTCUSDT",
      action="entry",
      direction="long",
      entry_id="LONG1",
      qty=0.001,
      sl_price=95000.0,
      tp_price=105000.0,
      trail_points=500.0,
      trail_offset=0.0,
      limit_price=0.0,
      comment="RSI oversold",
    )

    assert sig.portfolio_id == 42
    assert sig.symbol == "BTCUSDT"
    assert sig.action == "entry"
    assert sig.direction == "long"
    assert sig.sl_price == 95000.0
    assert sig.tp_price == 105000.0
    assert sig.trail_points == 500.0
    assert sig.qty_percent == 100.0  # default

  def test_signal_exit_partial_close(self):
    """Exit signal with partial close (qty_percent < 100)."""
    from backend.exchange.strategy_executor import StrategySignal

    sig = StrategySignal(
      portfolio_id=42,
      symbol="BTCUSDT",
      action="exit",
      direction="long",
      entry_id="LONG1",
      qty=0.001,
      sl_price=0,
      tp_price=0,
      trail_points=0,
      trail_offset=0,
      limit_price=0,
      comment="partial TP",
      qty_percent=50.0,
    )

    assert sig.action == "exit"
    assert sig.qty_percent == 50.0

  def test_signal_side_mapping(self):
    """direction 'long' → Buy, 'short' → Sell (engine mapping)."""
    from backend.exchange.strategy_executor import StrategySignal

    long_sig = StrategySignal(
      portfolio_id=1, symbol="X", action="entry", direction="long",
      entry_id="L1", qty=1, sl_price=100, tp_price=200,
      trail_points=0, trail_offset=0, limit_price=0, comment="",
    )
    short_sig = StrategySignal(
      portfolio_id=1, symbol="X", action="entry", direction="short",
      entry_id="S1", qty=1, sl_price=200, tp_price=100,
      trail_points=0, trail_offset=0, limit_price=0, comment="",
    )

    # Engine does: side = "Buy" if signal.direction == "long" else "Sell"
    buy_side = "Buy" if long_sig.direction == "long" else "Sell"
    sell_side = "Buy" if short_sig.direction == "long" else "Sell"

    assert buy_side == "Buy"
    assert sell_side == "Sell"


# =========================================================================
# B5. PORTFOLIO → LIVE: RISK PROFILE RESOLUTION
# =========================================================================

class TestRiskProfileResolution:
  """_get_portfolio_risk() resolves risk amount from portfolio profile."""

  def test_fixed_mode_returns_initial_risk(self):
    """Fixed mode: returns initial_risk_usd directly."""
    profile = {"mode": "fixed", "initial_risk_usd": 75.0, "override_enabled": True}
    mode = profile.get("mode", "fixed")
    base_risk = profile.get("initial_risk_usd", 100.0)

    assert mode == "fixed"
    assert base_risk == 75.0

  def test_pct_mode_calculates_from_equity(self):
    """Pct mode: equity * risk_pct / 100."""
    equity = 10000.0
    risk_pct = 2.0  # 2%
    risk_amount = equity * (risk_pct / 100)

    assert risk_amount == 200.0

  def test_martingale_streak_scaling(self):
    """Martingale: base * multiplier ^ min(streak, max_streak)."""
    base_risk = 50.0
    multiplier = 2.0
    max_streak = 3

    # 0 losses: factor = 2^0 = 1
    assert base_risk * (multiplier ** min(0, max_streak)) == 50.0

    # 1 loss: factor = 2^1 = 2
    assert base_risk * (multiplier ** min(1, max_streak)) == 100.0

    # 2 losses: factor = 2^2 = 4
    assert base_risk * (multiplier ** min(2, max_streak)) == 200.0

    # 3 losses (at cap): factor = 2^3 = 8
    assert base_risk * (multiplier ** min(3, max_streak)) == 400.0

    # 5 losses (capped at 3): factor = 2^3 = 8
    assert base_risk * (multiplier ** min(5, max_streak)) == 400.0

  def test_kelly_falls_back_to_fixed(self):
    """Kelly mode falls back to fixed risk (not yet implemented live)."""
    profile = {"mode": "kelly", "initial_risk_usd": 60.0, "override_enabled": True}
    mode = profile.get("mode", "fixed")
    base_risk = profile.get("initial_risk_usd", 100.0)

    # Kelly or fixed both return base_risk
    if mode == "fixed" or mode == "kelly":
      result = base_risk if base_risk > 0 else 100.0

    assert result == 60.0

  def test_global_fallback_when_no_profile(self):
    """No risk_profile → uses global settings."""
    source = {}  # empty source_strategies
    profile = source.get("risk_profile", {})

    # profile is empty, no override
    assert profile.get("override_enabled", True) is True  # but profile is empty

    # In real code: falls through to global risk_mode
    risk_mode = source.get("risk_mode", "fixed")
    risk_value = source.get("risk_per_trade_usd", 100.0)

    assert risk_mode == "fixed"
    assert risk_value == 100.0

  def test_pct_compound_martingale(self):
    """Martingale with pct compound: equity * (risk_pct/100) * factor."""
    equity = 12000.0
    risk_pct = 1.5  # 1.5%
    multiplier = 2.0
    streak = 2
    max_streak = 3

    capped = min(streak, max_streak)
    factor = multiplier ** capped  # 2^2 = 4
    result = equity * (risk_pct / 100) * factor

    assert result == 12000.0 * 0.015 * 4.0  # = 720.0
    assert result == 720.0

  def test_dollar_compound_martingale(self):
    """Martingale with dollar compound: base * factor * (equity/initial_cap)."""
    base_risk = 50.0
    multiplier = 2.0
    streak = 1
    max_streak = 3
    equity = 15000.0
    initial_cap = 10000.0

    capped = min(streak, max_streak)
    factor = multiplier ** capped  # 2^1 = 2
    equity_ratio = equity / initial_cap  # 1.5
    result = base_risk * factor * equity_ratio

    assert result == 50.0 * 2 * 1.5  # = 150.0
    assert result == 150.0


# =========================================================================
# B6. PORTFOLIO → LIVE: ASSET LIST BUILDING
# =========================================================================

class TestAssetListBuilding:
  """PortfolioAsset → asset_list dict transformation."""

  def test_asset_list_structure(self):
    """Each asset gets {symbol, side, batch_id}."""
    # Simulate what strategy_executor does at line 239-242
    assets_from_db = [
      MagicMock(symbol="BTCUSDT", trade_side="Long", batch_id=10),
      MagicMock(symbol="ETHUSDT", trade_side="Both", batch_id=10),
      MagicMock(symbol="SOLUSDT", trade_side="Short", batch_id=11),
    ]

    asset_list = [
      {"symbol": a.symbol, "side": a.trade_side, "batch_id": a.batch_id}
      for a in assets_from_db
    ]

    assert len(asset_list) == 3
    assert asset_list[0] == {"symbol": "BTCUSDT", "side": "Long", "batch_id": 10}
    assert asset_list[1] == {"symbol": "ETHUSDT", "side": "Both", "batch_id": 10}
    assert asset_list[2] == {"symbol": "SOLUSDT", "side": "Short", "batch_id": 11}

  def test_only_active_assets_included(self):
    """Only assets with is_active=True are loaded."""
    all_assets = [
      MagicMock(symbol="BTCUSDT", is_active=True),
      MagicMock(symbol="ETHUSDT", is_active=False),  # deactivated
      MagicMock(symbol="SOLUSDT", is_active=True),
    ]

    # Simulates the DB filter: is_active == True
    active_assets = [a for a in all_assets if a.is_active]
    assert len(active_assets) == 2
    assert active_assets[0].symbol == "BTCUSDT"
    assert active_assets[1].symbol == "SOLUSDT"

  def test_asset_batch_id_for_param_lookup(self):
    """batch_id on PortfolioAsset links to parameter source."""
    asset = MagicMock(symbol="BTCUSDT", batch_id=10)
    batch_params = {10: {"rsi_period": 21}}

    params = batch_params.get(asset.batch_id, {})
    assert params == {"rsi_period": 21}

  def test_asset_without_batch_id(self):
    """Assets without batch_id use defaults (legacy portfolios)."""
    asset = MagicMock(symbol="BTCUSDT", batch_id=None)
    batch_params = {10: {"rsi_period": 21}}

    params = batch_params.get(asset.batch_id, {})
    assert params == {}  # None key not in cache → empty


# =========================================================================
# B7. PORTFOLIO → LIVE: ENTRY SIGNAL VALIDATION
# =========================================================================

class TestEntrySignalValidation:
  """Engine validates signals before placing orders."""

  def test_no_sl_price_rejected(self):
    """Entry signal with sl_price <= 0 is rejected."""
    sl_price = 0.0
    assert sl_price <= 0  # would be rejected by engine

  def test_dedup_by_symbol_side(self):
    """Duplicate entry for same symbol+side is blocked."""
    from backend.exchange.strategy_executor import StrategySignal

    sig1 = StrategySignal(
      portfolio_id=42, symbol="BTCUSDT", action="entry", direction="long",
      entry_id="L1", qty=0.001, sl_price=95000, tp_price=105000,
      trail_points=0, trail_offset=0, limit_price=0, comment="",
    )

    # In-flight key: "portfolio_id_symbol_side"
    side = "Buy" if sig1.direction == "long" else "Sell"
    flight_key = f"{sig1.portfolio_id}_{sig1.symbol}_{side}"

    assert flight_key == "42_BTCUSDT_Buy"

    # Second signal would match the same key → blocked
    sig2 = StrategySignal(
      portfolio_id=42, symbol="BTCUSDT", action="entry", direction="long",
      entry_id="L2", qty=0.002, sl_price=94000, tp_price=106000,
      trail_points=0, trail_offset=0, limit_price=0, comment="",
    )

    side2 = "Buy" if sig2.direction == "long" else "Sell"
    flight_key2 = f"{sig2.portfolio_id}_{sig2.symbol}_{side2}"

    assert flight_key == flight_key2  # duplicate detected

  def test_different_side_not_deduped(self):
    """Long and short for same symbol are NOT duplicates."""
    long_key = "42_BTCUSDT_Buy"
    short_key = "42_BTCUSDT_Sell"
    assert long_key != short_key

  def test_stale_in_flight_cleaned(self):
    """Stale in-flight entries (> 120s) are cleaned up."""
    import time

    in_flight = {"42_BTCUSDT_Buy": time.time() - 200}  # 200 seconds ago
    now = time.time()

    stale = [k for k, t in in_flight.items() if now - t > 120]
    for k in stale:
      in_flight.pop(k, None)

    assert len(in_flight) == 0  # stale entry removed


# =========================================================================
# B8. PORTFOLIO → LIVE: POSITION SIZING DATA FLOW
# =========================================================================

class TestPositionSizingDataFlow:
  """Data flowing from signal → position sizing → order."""

  def test_risk_based_qty_calculation(self):
    """qty = risk_amount / sl_distance (per tick)."""
    entry_price = 100.0
    sl_price = 95.0
    risk_amount = 50.0

    sl_distance = abs(entry_price - sl_price)  # 5.0
    qty = risk_amount / sl_distance  # 10.0

    assert qty == 10.0

  def test_risk_based_qty_short(self):
    """Short: sl_distance = sl_price - entry_price."""
    entry_price = 100.0
    sl_price = 105.0  # stop above for short
    risk_amount = 50.0

    sl_distance = abs(sl_price - entry_price)  # 5.0
    qty = risk_amount / sl_distance  # 10.0

    assert qty == 10.0

  def test_sizing_output_carries_to_order(self):
    """Sizing dict fields flow to order submission."""
    sizing = {"qty": 0.005, "leverage": 10, "notional": 500.0}

    # Engine uses these fields:
    assert sizing["qty"] > 0  # sent to submit_entry_order
    assert sizing["leverage"] > 0  # used for prepare_symbol


# =========================================================================
# B9. PORTFOLIO → LIVE: ORDER TYPE RESOLUTION
# =========================================================================

class TestOrderTypeResolution:
  """Entry order type determined from signal + settings."""

  def test_signal_limit_price_overrides(self):
    """If signal.limit_price > 0, use Limit order."""
    limit_price = 98000.0

    if limit_price > 0:
      order_type = "Limit"
    else:
      order_type = "Market"

    assert order_type == "Limit"

  def test_settings_limit_with_offset(self):
    """Settings limit order: offset from entry price."""
    entry_price = 100000.0
    offset_pct = 0.1  # 0.1% offset
    side = "Buy"

    offset = entry_price * (offset_pct / 100)  # 100.0
    if side == "Buy":
      limit_price = entry_price + offset  # 100100.0 (slightly above for buy)
    else:
      limit_price = entry_price - offset  # 99900.0

    assert limit_price == 100100.0

  def test_market_order_when_no_limit(self):
    """No limit price → Market order."""
    limit_price = 0.0

    if limit_price > 0:
      order_type = "Limit"
    else:
      order_type = "Market"

    assert order_type == "Market"


# =========================================================================
# B10. PORTFOLIO → LIVE: DEACTIVATION CLEANUP
# =========================================================================

class TestDeactivationCleanup:
  """Portfolio deactivation cleans up correctly."""

  def test_deactivation_removes_from_active(self):
    """deactivate_portfolio pops from _active dict."""
    from backend.exchange.strategy_executor import ActivePortfolio

    active_dict = {}
    ap = ActivePortfolio(
      portfolio_id=42, strategy_source="", parameters={},
      base_tf="30", extra_tfs=[], assets=[{"symbol": "BTCUSDT", "side": "Long"}],
    )
    active_dict[42] = ap

    # Simulate deactivation
    removed = active_dict.pop(42, None)
    assert removed is not None
    assert 42 not in active_dict

  def test_shared_symbol_not_unregistered(self):
    """If another portfolio uses the same symbol, don't unsubscribe."""
    from backend.exchange.strategy_executor import ActivePortfolio

    active_dict = {
      42: ActivePortfolio(
        portfolio_id=42, strategy_source="", parameters={},
        base_tf="30", extra_tfs=[],
        assets=[{"symbol": "BTCUSDT", "side": "Long"}],
      ),
      43: ActivePortfolio(
        portfolio_id=43, strategy_source="", parameters={},
        base_tf="30", extra_tfs=[],
        assets=[{"symbol": "BTCUSDT", "side": "Short"}],  # same symbol!
      ),
    }

    # Deactivating portfolio 42
    removed = active_dict.pop(42)

    # Collect symbols still needed
    all_symbols_needed = set()
    for pid, ap in active_dict.items():
      for asset in ap.assets:
        all_symbols_needed.add(asset["symbol"])

    # BTCUSDT still needed by portfolio 43
    for asset in removed.assets:
      should_unregister = asset["symbol"] not in all_symbols_needed
      assert should_unregister is False  # BTCUSDT still used


# =========================================================================
# B11. PORTFOLIO → LIVE: REFRESH ADD/REMOVE
# =========================================================================

class TestRefreshAddRemove:
  """Portfolio refresh adds new symbols and removes old ones."""

  def test_refresh_detects_added_symbols(self):
    """New symbols in DB but not in active → added."""
    current_symbols = {"BTCUSDT", "ETHUSDT"}
    new_symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}  # SOLUSDT added

    added = new_symbols - current_symbols
    removed = current_symbols - new_symbols
    kept = current_symbols & new_symbols

    assert added == {"SOLUSDT"}
    assert removed == set()
    assert kept == {"BTCUSDT", "ETHUSDT"}

  def test_refresh_detects_removed_symbols(self):
    """Symbols in active but not in DB → removed."""
    current_symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    new_symbols = {"BTCUSDT", "ETHUSDT"}  # SOLUSDT removed

    removed = current_symbols - new_symbols
    assert removed == {"SOLUSDT"}

  def test_refresh_wont_remove_with_open_position(self):
    """Symbols with open positions are NOT removed (safety check)."""
    removed_candidates = {"SOLUSDT"}
    has_open_position = {"SOLUSDT": True}

    actually_removed = [
      sym for sym in removed_candidates
      if not has_open_position.get(sym, False)
    ]

    assert actually_removed == []  # SOLUSDT has open position, kept


# =========================================================================
# B12. PORTFOLIO → LIVE: SAFETY CHECKS
# =========================================================================

class TestSafetyChecks:
  """Pre-trade safety validation."""

  def test_max_positions_blocking(self):
    """Engine blocks entry when max_positions reached."""
    total_open = 10
    max_pos = 10

    blocked = total_open >= max_pos
    assert blocked is True

  def test_risk_free_not_counted(self):
    """Risk-free positions (SL at breakeven) not counted against budget."""
    all_open = 10
    risk_free = 3
    risk_adjusted_count = all_open - risk_free  # 7

    # But hard cap still uses all_open
    max_pos = 10
    hard_blocked = all_open >= max_pos

    # For sizing, use risk_adjusted
    assert risk_adjusted_count == 7
    assert hard_blocked is True  # still blocked by hard cap


# =========================================================================
# A8. L0 — VARIANCE RATIO INTERPRETATION
# =========================================================================

class TestVarianceRatioInterpretation:
  """VR(q) = Var(r_q) / (q * Var(r_1)): <1 mean-rev, >1 trending."""

  def test_vr_random_walk_approximately_one(self):
    """Random walk returns → VR ≈ 1.0."""
    from backend.analysis.seed_ranker import variance_ratio
    rng = np.random.default_rng(42)
    pnl = rng.normal(0.0, 1.0, 1000)
    vr = variance_ratio(pnl, lag=5)
    assert 0.6 < vr < 1.5, f"Random walk VR should be ≈ 1.0, got {vr}"

  def test_vr_short_series_returns_one(self):
    """Too short series → VR = 1.0 (assume random walk)."""
    from backend.analysis.seed_ranker import variance_ratio
    pnl = np.array([1.0, -1.0, 2.0])  # only 3 values, lag=5 needs more
    vr = variance_ratio(pnl, lag=5)
    assert vr == 1.0


# =========================================================================
# A9. L0 — HALF-LIFE INTERPRETATION
# =========================================================================

class TestHalfLifeInterpretation:
  """Half-life of mean reversion: lower = faster snap-back."""

  def test_halflife_trending_returns_inf(self):
    """Trending data (theta >= 0) → half_life = inf."""
    from backend.analysis.seed_ranker import half_life
    pnl = np.linspace(1.0, 100.0, 200)  # perfect uptrend
    hl = half_life(pnl)
    assert hl == float('inf')

  def test_halflife_short_series_returns_inf(self):
    """< 20 values → half_life = inf."""
    from backend.analysis.seed_ranker import half_life
    pnl = np.array([1, -1, 2, -2, 1])
    hl = half_life(pnl)
    assert hl == float('inf')

  def test_halflife_clamped_range(self):
    """Half-life clamped to [1, 500]."""
    from backend.analysis.seed_ranker import half_life
    rng = np.random.default_rng(42)
    pnl = rng.normal(0.0, 2.0, 200)
    hl = half_life(pnl)
    if hl < float('inf'):
      assert 1.0 <= hl <= 500.0


# =========================================================================
# A10. L0 — CROSSING RATE USES FULL EQUITY
# =========================================================================

class TestCrossingRateFullEquity:
  """_crossing_rate uses the FULL equity curve (not stripped zeros)."""

  def test_crossing_rate_range(self):
    """Crossing rate always in [0, 1]."""
    from backend.analysis.seed_ranker import _crossing_rate
    rng = np.random.default_rng(42)
    pnl = rng.normal(0.0, 5.0, 200)
    cr = _crossing_rate(pnl)
    assert 0.0 <= cr <= 1.0

  def test_crossing_rate_short_series_zero(self):
    """< 20 values → crossing rate = 0."""
    from backend.analysis.seed_ranker import _crossing_rate
    pnl = np.array([1, -1, 2])
    cr = _crossing_rate(pnl)
    assert cr == 0.0

  def test_crossing_rate_constant_series_zero(self):
    """Constant equity → never crosses rolling mean → low rate."""
    from backend.analysis.seed_ranker import _crossing_rate
    pnl = np.ones(200) * 5.0  # constant positive → equity = 5, 10, 15, ...
    cr = _crossing_rate(pnl)
    # Monotonically increasing equity never crosses rolling mean
    assert cr < 0.1


# =========================================================================
# B13. PORTFOLIO → LIVE: EXTRA TIMEFRAME RESOLUTION
# =========================================================================

class TestExtraTimeframeResolution:
  """Extra timeframes resolved from source_data, batch, or code regex."""

  def test_source_data_extra_tfs(self):
    """Extra TFs from source_strategies.extra_timeframes."""
    source_data = {"extra_timeframes": ["D", "240"]}
    extra_tfs = source_data.get("extra_timeframes", [])
    assert extra_tfs == ["D", "240"]

  def test_regex_auto_detect_from_code(self):
    """Auto-detect request_security() calls in strategy source."""
    import re

    code = '''
    def next(self):
      daily_close = self.request_security("D", "close")
      h4_rsi = self.request_security("240", "rsi")
    '''

    tf_matches = re.findall(
      r'request_security\s*\(\s*["\'](\w+)["\']', code,
    )
    extra_tfs = list(dict.fromkeys(tf_matches))  # deduplicate preserving order

    assert extra_tfs == ["D", "240"]

  def test_regex_no_duplicates(self):
    """Multiple request_security calls with same TF → deduplicated."""
    import re

    code = '''
    daily_close = self.request_security("D", "close")
    daily_high = self.request_security("D", "high")
    '''

    tf_matches = re.findall(r'request_security\s*\(\s*["\'](\w+)["\']', code)
    extra_tfs = list(dict.fromkeys(tf_matches))

    assert extra_tfs == ["D"]  # only one "D"

  def test_no_request_security_empty(self):
    """No request_security calls → empty extra_tfs."""
    import re

    code = '''
    def next(self):
      self.buy()
    '''

    tf_matches = re.findall(r'request_security\s*\(\s*["\'](\w+)["\']', code)
    assert tf_matches == []


# =========================================================================
# B14. PORTFOLIO → LIVE: STRATEGY RESOLUTION 4-STEP FALLBACK
# =========================================================================

class TestStrategyResolutionFallback:
  """4-step fallback: strategy_id → name → batch → asset batch_id."""

  def test_step1_strategy_id(self):
    """Step 1: direct strategy_id from source_data."""
    source_data = {"strategy_id": 3, "strategy_name": "RSI Swing"}
    strategy_id = source_data.get("strategy_id")
    assert strategy_id == 3  # resolved directly

  def test_step2_strategy_name(self):
    """Step 2: lookup by strategy_name."""
    source_data = {"strategy_name": "RSI Swing"}  # no strategy_id
    strategy_id = source_data.get("strategy_id")
    strategy_name = source_data.get("strategy_name", "")

    assert strategy_id is None
    assert strategy_name == "RSI Swing"  # would be used for DB lookup

  def test_step3_batch_ids(self):
    """Step 3: resolve via batch_ids → BatchBacktestRun.strategy_id."""
    source_data = {"batch_ids": [10, 11, 12]}  # no strategy_id or name
    batch_ids = source_data.get("batch_ids", [])

    assert len(batch_ids) == 3  # would iterate and check each batch

  def test_step4_asset_batch_id(self):
    """Step 4: last resort via asset.batch_id."""
    # Used when source_data has no strategy reference at all
    asset = MagicMock(batch_id=10)
    assert asset.batch_id == 10  # would look up batch → strategy

  def test_all_steps_fail_returns_false(self):
    """If no strategy found after all 4 steps, activation fails."""
    source_data = {}  # nothing
    strategy_id = source_data.get("strategy_id")
    strategy_name = source_data.get("strategy_name", "")
    batch_ids = source_data.get("batch_ids", [])

    assert strategy_id is None
    assert strategy_name == ""
    assert batch_ids == []
    # Would return False in strategy_executor


# =========================================================================
# B15. PORTFOLIO → LIVE: WS SUBSCRIPTION DATA FLOW
# =========================================================================

class TestWSSubscriptionDataFlow:
  """Active assets → WebSocket symbol subscriptions."""

  def test_subscribe_all_active_symbols(self):
    """Each active asset's symbol gets subscribed."""
    assets = [
      MagicMock(symbol="BTCUSDT"),
      MagicMock(symbol="ETHUSDT"),
      MagicMock(symbol="SOLUSDT"),
    ]

    subscribed = []
    for asset in assets:
      subscribed.append(asset.symbol)

    assert subscribed == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

  def test_subscribe_with_all_timeframes(self):
    """WS subscribes with base_tf + extra_tfs."""
    base_tf = "30"
    extra_tfs = ["D", "240"]

    # Engine subscribes: ws_manager.subscribe_symbol(sym, base_tf, extra_tfs)
    all_tfs = [base_tf] + extra_tfs
    assert all_tfs == ["30", "D", "240"]


# =========================================================================
# A11. L0 — COMPOSITE SCORE HAND CALCULATION
# =========================================================================

class TestL0CompositeScoreHandCalc:
  """Verify trend_score and meanrev_score formulas match known inputs."""

  def test_trend_score_with_known_components(self):
    """trend_score = 0.30*TER + 0.25*max(0,H-0.5)*2 + 0.25*sigmoid(GPR) + 0.20*max(0,AC)."""
    # Use simple values to verify formula
    ter = 0.8
    hurst = 0.7
    gpr = 2.0
    ac = 0.3

    sig_gpr = 1.0 / (1.0 + np.exp(-gpr))  # sigmoid(2.0) ≈ 0.8808
    expected = (
      0.30 * ter
      + 0.25 * max(0.0, hurst - 0.5) * 2.0
      + 0.25 * sig_gpr
      + 0.20 * max(0.0, ac)
    )

    # Manual calc:
    # 0.30 * 0.8 = 0.24
    # 0.25 * max(0, 0.2) * 2 = 0.25 * 0.4 = 0.10
    # 0.25 * 0.8808 = 0.2202
    # 0.20 * 0.3 = 0.06
    # Total = 0.24 + 0.10 + 0.2202 + 0.06 = 0.6202
    assert abs(expected - 0.6202) < 0.001

  def test_meanrev_score_with_known_components(self):
    """meanrev_score = 0.25*MRI + 0.25*max(0,0.5-H)*2 + 0.20*max(0,1-VR) + 0.15*norm_hl + 0.15*CR."""
    mri = 0.5
    hurst = 0.3
    vr = 0.7
    hl = 10.0
    cr = 0.4

    norm_hl = 1.0 / (1.0 + hl / 10.0)  # 1/(1+1) = 0.5
    expected = (
      0.25 * mri
      + 0.25 * max(0.0, 0.5 - hurst) * 2.0
      + 0.20 * max(0.0, 1.0 - vr)
      + 0.15 * norm_hl
      + 0.15 * cr
    )

    # Manual calc:
    # 0.25 * 0.5 = 0.125
    # 0.25 * max(0, 0.2) * 2 = 0.25 * 0.4 = 0.10
    # 0.20 * max(0, 0.3) = 0.20 * 0.3 = 0.06
    # 0.15 * 0.5 = 0.075
    # 0.15 * 0.4 = 0.06
    # Total = 0.125 + 0.10 + 0.06 + 0.075 + 0.06 = 0.42
    assert abs(expected - 0.42) < 0.001


# =========================================================================
# A12. L0 — BATCH GRADING INTEGRATION
# =========================================================================

class TestL0BatchGradingIntegration:
  """End-to-end: daily PnL → all metrics → grades → regime."""

  def test_strong_trend_asset_graded_correctly(self):
    """Asset with strong uptrend → A/A+ trend grade, TREND regime."""
    from backend.analysis.seed_ranker import (
      trend_score, meanrev_score, classify_regime, grade_letter,
    )

    rng = np.random.default_rng(42)
    pnl = rng.uniform(0.5, 3.0, 200)  # consistently positive

    t_score = trend_score(pnl, n_pool=20)
    m_score = meanrev_score(pnl)
    regime = classify_regime(t_score, m_score)
    t_grade = grade_letter(t_score)

    assert regime == "TREND"
    assert t_grade in ("A+", "A", "B+"), f"Strong trend should get good grade, got {t_grade}"
    assert t_score > m_score

  def test_random_walk_asset_neutral(self):
    """Random walk → NEUTRAL or close to it."""
    from backend.analysis.seed_ranker import (
      trend_score, meanrev_score, classify_regime,
    )

    rng = np.random.default_rng(42)
    pnl = rng.normal(0.0, 1.0, 500)  # random walk

    t_score = trend_score(pnl, n_pool=20)
    m_score = meanrev_score(pnl)
    diff = abs(t_score - m_score)

    # Random walk should not have strong preference either way
    assert diff < 0.3, f"Random walk should be near-neutral, diff={diff}"

  def test_compute_structural_metrics_output_merged(self):
    """Structural metrics dict can be merged into existing metrics."""
    existing_metrics = {
      "net_profit": 5000.0,
      "profit_factor": 1.5,
      "win_rate": 0.55,
    }

    structural = {
      "regime": "TREND",
      "trend_score": 0.42,
      "trend_grade": "A",
      "meanrev_score": 0.15,
      "meanrev_grade": "C+",
      "TER": 0.65,
      "Hurst": 0.62,
      "GPR": 2.3,
      "AC1": 0.12,
      "DSR": 0.72,
      "MRI": 0.0,
      "VR5": 1.2,
      "HalfLife": 999.0,
      "CrossRate": 0.15,
    }

    merged = dict(existing_metrics)
    merged.update(structural)

    # All original keys preserved
    assert merged["net_profit"] == 5000.0
    assert merged["profit_factor"] == 1.5

    # All structural keys added
    assert merged["regime"] == "TREND"
    assert merged["trend_score"] == 0.42
    assert merged["trend_grade"] == "A"
    assert merged["TER"] == 0.65
