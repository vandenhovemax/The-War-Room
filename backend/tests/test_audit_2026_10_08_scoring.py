"""Regression tests for the 2026-10-08 scoring audit.

Each class pins one bug that was found by reading the objective/metric code
against its own docstrings and against its sibling implementations:

  * MR score's variance-ratio term subsampled every 5th day instead of summing
    5-day blocks, so it was ~0.2 for every series (a constant +0.2 bonus).
  * Ulcer Index divided each drawdown by the running peak, falling back to $1
    when the peak was <= 0. A curve whose first trade lost was scored in
    "$ x 100" instead of % — ~100x worse than the same $ drawdown after a win.
    The optimizer's copy was already fixed (B17); the batch-selection, event
    WFO and full-metrics copies were not.
  * Profit factor with no losing day returned -gross_profit (dollars) on the
    slow path and the -10 cap on the fast path.
  * GPR scored an all-zero (never traded) series as the best possible value.
  * The fast (vectorised) and slow optimizer scoring paths disagreed on
    rapid_recovery (no drawdown) and tail_ratio (fewer than 10 days).
  * Backtest expectancy counted breakeven trades in the loss rate, so it did
    not equal the average trade.
  * Full-metrics Sortino annualised a per-TRADE ratio with sqrt(365).

Reference values are computed independently inside each test, not by calling
the code under test.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backend.analysis import batch_selection_scoring as bss
from backend.analysis.event_wfo import _compute_event_metrics, _window_aligned_risk_metrics
from backend.analysis.full_metrics import calculate_full_metrics
from backend.analysis.scoring import (
  SCORING_FUNCTIONS,
  _batch_score_candidates,
  portfolio_profit_factor,
  portfolio_rapid_recovery,
  portfolio_tail_ratio,
  portfolio_ulcer,
)
from backend.backtester.metrics import calculate_metrics
from backend.backtester.models import OrderFill


def _series(values: list[float]) -> pd.Series:
  idx = pd.date_range("2024-01-01", periods=len(values), freq="D")
  return pd.Series(values, index=idx, dtype=float)


def _reference_ulcer(equity: np.ndarray, peak: np.ndarray) -> float:
  """RMS drawdown in % of ONE reference scale for the whole curve."""
  ref = max(float(np.abs(peak).max()), float(np.abs(equity).max()), 1.0)
  dd_pct = (equity - peak) / ref * 100
  return float(np.sqrt(np.mean(dd_pct ** 2)))


# First trade loses $20, then the curve grinds up.
_LOSS_FIRST = [0.0, -20.0] + [0.0] * 5 + [2.0] * 30
# First trade wins $100, then the same $20 loss, then the same grind.
_WIN_FIRST = [0.0, 100.0, -20.0] + [0.0] * 5 + [2.0] * 30


class TestMrScoreVarianceRatio:
  """VR(5) = Var(5-day block sums) / (5 * Var(daily)) — Lo & MacKinlay."""

  def test_sign_persistent_series_gets_no_vr_bonus(self):
    # Runs of ten +1 then ten -1: 5-day blocks are +5 / -5, so VR = 5 (trending).
    pnl = _series(([1.0] * 10 + [-1.0] * 10) * 6)
    active = pnl.values
    blocks = active[: len(active) // 5 * 5].reshape(-1, 5).sum(axis=1)
    vr = np.var(blocks, ddof=1) / (5 * np.var(active, ddof=1))
    assert vr > 1.0
    vr_bonus = max(0.0, 1.0 - vr)  # == 0 for a trending series

    equity = pnl.cumsum().values
    window = max(20, len(equity) // 10)
    rolling = pd.Series(equity).rolling(window, min_periods=1).mean().values
    signs = np.sign(equity - rolling)
    cr = float(np.sum(np.abs(np.diff(signs)) > 0)) / (len(equity) - 1)
    ac = float(np.corrcoef(active[:-1], active[1:])[0, 1])
    expected = float(np.clip(0.40 * cr + 0.35 * max(0.0, -ac) + 0.25 * vr_bonus, 0, 1))

    assert bss.metric_mr_score(pnl) == pytest.approx(expected, abs=1e-9)

  def test_vr_term_tracks_autocorrelation(self):
    """Mean-reverting AR(-0.6) must score above trending AR(+0.6) on the VR term."""
    rng = np.random.default_rng(11)
    eps = rng.normal(0, 1, 600)
    trend, revert = np.zeros(600), np.zeros(600)
    for i in range(1, 600):
      trend[i] = 0.6 * trend[i - 1] + eps[i]
      revert[i] = -0.6 * revert[i - 1] + eps[i]
    from backend.analysis.seed_ranker import variance_ratio
    assert variance_ratio(trend, 5) > 1.5
    assert variance_ratio(revert, 5) < 0.7


class TestUlcerReferenceScale:
  """Same $ drawdown must not be scored ~100x worse because it came first."""

  def test_batch_selection_ulcer_matches_optimizer_ulcer(self):
    for values in (_LOSS_FIRST, _WIN_FIRST):
      pnl = _series(values)
      assert bss.metric_ulcer(pnl) == pytest.approx(-portfolio_ulcer(pnl), rel=1e-12)

  def test_batch_selection_ulcer_is_bounded(self):
    rng = np.random.default_rng(3)
    for _ in range(50):
      pnl = _series(list(rng.normal(0, 10, 60)))
      assert -bss.metric_ulcer(pnl) <= 200.0 + 1e-9

  def test_loss_first_no_longer_explodes(self):
    loss_first = -bss.metric_ulcer(_series(_LOSS_FIRST))
    win_first = -bss.metric_ulcer(_series(_WIN_FIRST))
    assert loss_first < 10 * win_first  # was ~100x before the fix

  def test_event_metrics_ulcer_uses_reference_scale(self):
    # First trade loses, second loses more while the running peak is still
    # negative: the old per-peak division scored this -$10 dip as -1000%.
    pnls = [-20.0, -10.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    expected = _reference_ulcer(equity, peak)
    assert _compute_event_metrics(pnls)["ulcer"] == pytest.approx(round(expected, 4), abs=1e-4)

  def test_window_aligned_ulcer_not_perfect_for_losing_curve(self):
    """A curve that never makes money used to get ulcer 0 (the best score)."""
    m = _window_aligned_risk_metrics([-10.0, -10.0, -10.0, -10.0])
    assert m["ulcer"] > 0

  def test_full_metrics_ulcer_uses_reference_scale(self):
    pnls = [-20.0, -10.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0]
    df = _trades_df(pnls)
    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    expected = _reference_ulcer(equity, peak)
    assert calculate_full_metrics(df)["Ulcer Index"] == pytest.approx(expected, rel=1e-9)


class TestFastSlowParity:
  """The vectorised path (Settings Recommender) must rank like the slow path."""

  def test_profit_factor_no_loss_is_capped(self):
    no_loss = _series([0, 0, 120.0, 0, 80.0, 0, 0, 200.0])
    assert portfolio_profit_factor(no_loss) == -10.0

  def test_gpr_all_zero_is_not_best(self):
    assert SCORING_FUNCTIONS["max_gpr"](_series([0.0] * 20)) == float("inf")
    assert SCORING_FUNCTIONS["max_gpr"](_series([0.0, 5.0, 0.0, 3.0])) == -10.0

  def test_rapid_recovery_no_drawdown(self):
    mono = _series([10.0] * 20)
    fast = _batch_score_candidates(np.zeros(20), mono.values[:, None], "max_rapid_recovery")[0]
    assert fast == portfolio_rapid_recovery(mono)

  def test_tail_ratio_short_series(self):
    short = _series([5.0, -2, 3, -1, 4, 6])
    fast = _batch_score_candidates(np.zeros(6), short.values[:, None], "max_tail_ratio")[0]
    assert fast == portfolio_tail_ratio(short)

  @pytest.mark.parametrize("objective", sorted(SCORING_FUNCTIONS))
  def test_every_objective_agrees_on_edge_cases(self, objective):
    rng = np.random.default_rng(5)
    T = 40
    cols = np.column_stack([
      rng.normal(0, 10, T),                          # ordinary
      np.where(rng.random(T) < 0.2, 25.0, 0.0),      # sparse, never loses
      np.zeros(T),                                   # never traded
      np.full(T, 3.0),                               # monotonic, no drawdown
      np.where(rng.random(T) < 0.2, -15.0, 0.0),     # sparse, only loses
    ])
    combined = np.zeros(T)
    fast = _batch_score_candidates(combined, cols, objective)
    fn = SCORING_FUNCTIONS[objective]
    for k in range(cols.shape[1]):
      slow = fn(pd.Series(combined + cols[:, k]))
      if math.isinf(slow):
        assert math.isinf(fast[k]) and np.sign(fast[k]) == np.sign(slow), (objective, k, fast[k], slow)
      else:
        assert fast[k] == pytest.approx(slow, rel=1e-9, abs=1e-9), (objective, k, fast[k], slow)


class TestBacktestExpectancy:
  def test_expectancy_equals_average_trade_with_breakevens(self):
    t0 = pd.Timestamp("2024-01-01")
    pnls = [10.0, 10.0, -10.0, 0.0, 0.0]  # two wins, one loss, two exact breakevens
    fills = []
    for i, p in enumerate(pnls):
      pid = f"p{i}"
      fills.append(OrderFill(bar_index=2 * i, timestamp=t0, side="long", action="entry",
                             price=100.0, qty=1.0, position_id=pid))
      fills.append(OrderFill(bar_index=2 * i + 1, timestamp=t0, side="long", action="exit",
                             price=100.0 + p, qty=1.0, pnl=p, position_id=pid))
    equity = list(10_000 + np.cumsum([0.0] + pnls))
    m = calculate_metrics(fills, equity, 10_000.0, total_bars=10)
    assert m["avg_trade"] == pytest.approx(2.0)
    assert m["expectancy"] == pytest.approx(m["avg_trade"])


def _trades_df(pnls: list[float], per_day: int = 1) -> pd.DataFrame:
  rows = []
  for i, p in enumerate(pnls):
    day = pd.Timestamp("2024-01-01") + pd.Timedelta(days=i // per_day)
    rows.append({
      "Entry Time": day + pd.Timedelta(hours=1),
      "Exit Time": day + pd.Timedelta(hours=2 + i % per_day),
      "Net Profit": p, "Asset": "BTCUSDT", "Side": "Long",
    })
  return pd.DataFrame(rows)


class TestFullMetricsSortinoIsDaily:
  def test_sortino_is_computed_on_calendar_daily_pnl(self):
    # Trades on days 0,1,2,5,6,9 — days 3,4,7,8 have no trade and count as 0.
    days = [0, 1, 2, 5, 6, 9]
    pnls = [30.0, -10.0, 25.0, -5.0, 40.0, -20.0]
    rows = [{
      "Entry Time": pd.Timestamp("2024-01-01") + pd.Timedelta(days=d, hours=1),
      "Exit Time": pd.Timestamp("2024-01-01") + pd.Timedelta(days=d, hours=2),
      "Net Profit": p, "Asset": "BTCUSDT", "Side": "Long",
    } for d, p in zip(days, pnls)]
    daily = pd.Series(0.0, index=range(10))
    for d, p in zip(days, pnls):
      daily[d] += p
    neg = daily[daily < 0]
    expected = daily.mean() / math.sqrt((neg ** 2).mean()) * math.sqrt(365)
    assert calculate_full_metrics(pd.DataFrame(rows))["Sortino Ratio"] == pytest.approx(expected, rel=1e-9)

  def test_splitting_a_trade_within_a_day_does_not_change_sortino(self):
    one_per_day = _trades_df([20.0, -10.0, 30.0, -6.0, 12.0, 8.0])
    # Same daily totals, split unevenly into two trades per day.
    two_per_day = _trades_df([17.0, 3.0, -1.0, -9.0, 29.0, 1.0, -5.5, -0.5, 2.0, 10.0, 7.0, 1.0],
                             per_day=2)
    a = calculate_full_metrics(one_per_day)["Sortino Ratio"]
    b = calculate_full_metrics(two_per_day)["Sortino Ratio"]
    assert a == pytest.approx(b, rel=1e-9)


class TestGreedyDiversityFallback:
  """When too few diverse portfolios exist, the re-run drops the LAST-ADDED asset."""

  def test_rerun_excludes_last_added_not_alphabetically_last(self):
    from backend.analysis.optimizer import _greedy_search
    T = 30
    # Z is the best single asset (the seed); A is its best partner.
    pnl = pd.DataFrame({
      "A": np.full(T, 8.0), "B": np.full(T, 5.0), "C": np.full(T, 3.0), "Z": np.full(T, 10.0),
    }, index=pd.date_range("2024-01-01", periods=T, freq="D"))
    def total_profit(s):  # lower = better
      return -float(np.sum(s))

    result = _greedy_search(pnl, n_assets=2, score_fn=total_profit, n_seeds=1, n_portfolios=2)

    assert result[0] == ["A", "Z"]           # seed Z, then A added
    assert len(result) == 2
    # A (last added) is excluded from the re-run, so the core asset Z survives.
    # The old code excluded "Z" (alphabetically last of ["A", "Z"]).
    assert "Z" in result[1] and "A" not in result[1]


class TestUndefinedRatiosAreScaleFree:
  """O3: a ratio with a zero denominator scores -RATIO_CAP, not raw dollars."""

  RATIO_OBJECTIVES = [
    "max_sortino", "max_calmar", "max_recovery", "max_gain_pain",
    "max_profit_factor", "max_payoff", "max_cde", "max_est_efficiency",
    "max_rapid_recovery", "max_robust_sortino",
  ]

  @pytest.mark.parametrize("objective", RATIO_OBJECTIVES)
  def test_dollar_size_does_not_change_the_score(self, objective):
    from backend.analysis.scoring import RATIO_CAP
    fn = SCORING_FUNCTIONS[objective]
    rng = np.random.default_rng(9)
    gains = rng.uniform(1.0, 3.0, 40)       # never loses, never draws down
    small, big = _series(list(gains * 0.01)), _series(list(gains * 500.0))
    assert fn(small) == fn(big) == -RATIO_CAP

  def test_sharpe_with_zero_variance(self):
    from backend.analysis.scoring import RATIO_CAP, portfolio_sharpe
    assert portfolio_sharpe(_series([0.03] * 30)) == portfolio_sharpe(_series([700.0] * 30)) == -RATIO_CAP

  def test_tail_ratio_without_left_tail(self):
    from backend.analysis.scoring import RATIO_CAP
    assert portfolio_tail_ratio(_series([0.0] * 5 + [4.0, 6.0, 9.0] * 4)) == -RATIO_CAP
