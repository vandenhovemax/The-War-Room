"""Tests that batch scoring functions match individual scoring functions exactly."""
import numpy as np
import pandas as pd
import pytest

from backend.analysis.scoring import (
  _batch_fast_stability,
  _batch_score_candidates,
  _fast_stability,
  SCORING_FUNCTIONS,
)


@pytest.fixture
def sample_data():
  rng = np.random.default_rng(42)
  T = 500
  K = 20
  combined = rng.standard_normal(T) * 10  # daily PnL in dollars
  candidates = rng.standard_normal((T, K)) * 10
  return combined, candidates


class TestBatchFastStability:
  def test_matches_individual(self, sample_data):
    combined, candidates = sample_data
    K = candidates.shape[1]
    batch_result = _batch_fast_stability(combined, candidates)
    assert batch_result.shape == (K,)

    for k in range(K):
      individual = _fast_stability(combined + candidates[:, k])
      assert abs(batch_result[k] - individual) < 1e-10, (
        f"Mismatch at candidate {k}: batch={batch_result[k]}, individual={individual}"
      )

  def test_empty_input(self):
    # T < 2 should return zeros
    result = _batch_fast_stability(np.array([1.0]), np.array([[2.0]]))
    assert result.shape == (1,)
    assert result[0] == 0.0

  def test_single_candidate(self, sample_data):
    combined, candidates = sample_data
    single = candidates[:, :1]  # (T, 1)
    batch_result = _batch_fast_stability(combined, single)
    individual = _fast_stability(combined + single[:, 0])
    assert abs(batch_result[0] - individual) < 1e-10

  def test_all_positive_pnl(self):
    """Stability should be high for consistently positive PnL."""
    T = 200
    combined = np.ones(T) * 5.0
    candidates = np.ones((T, 3)) * 2.0
    result = _batch_fast_stability(combined, candidates)
    assert result.shape == (3,)
    # All-positive linear equity should have high stability
    assert all(r > 0.5 for r in result)

  def test_zero_combined(self):
    """Zero combined + candidate should match individual."""
    T = 100
    combined = np.zeros(T)
    rng = np.random.default_rng(99)
    candidates = rng.standard_normal((T, 5)) * 10
    batch_result = _batch_fast_stability(combined, candidates)
    for k in range(5):
      individual = _fast_stability(candidates[:, k])
      assert abs(batch_result[k] - individual) < 1e-10

  def test_short_series(self):
    """T=5, K=3 — verify stability computed correctly for very short series."""
    rng = np.random.default_rng(77)
    combined = rng.standard_normal(5) * 10
    candidates = rng.standard_normal((5, 3)) * 10
    batch_result = _batch_fast_stability(combined, candidates)
    assert batch_result.shape == (3,)
    for k in range(3):
      individual = _fast_stability(combined + candidates[:, k])
      assert abs(batch_result[k] - individual) < 1e-10

  def test_all_negative_pnl(self):
    """All negative PnL should produce low stability."""
    T = 200
    combined = np.ones(T) * -5.0
    candidates = np.ones((T, 3)) * -2.0
    result = _batch_fast_stability(combined, candidates)
    assert result.shape == (3,)
    for k in range(3):
      individual = _fast_stability(combined + candidates[:, k])
      assert abs(result[k] - individual) < 1e-10

  def test_zero_variance(self):
    """Constant combined+candidate should not crash."""
    T = 100
    combined = np.ones(T) * 50.0
    candidates = np.ones((T, 2)) * 10.0
    result = _batch_fast_stability(combined, candidates)
    assert result.shape == (2,)
    # Should not crash; values should be finite
    assert all(np.isfinite(result))


class TestBatchScoreCandidates:
  TESTABLE_OBJECTIVES = [
    "max_sortino", "max_calmar", "max_recovery", "min_ulcer",
    "max_profit_factor", "min_max_drawdown", "max_sharpe",
    "max_expectancy", "min_lake_ratio", "max_tail_ratio",
    "max_gain_pain", "max_payoff", "max_cde", "max_robust_sortino",
    "max_est_efficiency", "max_rapid_recovery", "stability",
  ]

  @pytest.mark.parametrize("objective", TESTABLE_OBJECTIVES)
  def test_matches_individual(self, sample_data, objective):
    combined, candidates = sample_data
    K = candidates.shape[1]

    batch_result = _batch_score_candidates(combined, candidates, objective)
    assert batch_result.shape == (K,)

    fn = SCORING_FUNCTIONS[objective]
    for k in range(K):
      individual = fn(pd.Series(combined + candidates[:, k]))
      # Allow small numerical tolerance
      if np.isfinite(individual) and np.isfinite(batch_result[k]):
        assert abs(batch_result[k] - individual) < 1e-6, (
          f"Mismatch for {objective} at candidate {k}: "
          f"batch={batch_result[k]}, individual={individual}"
        )
      else:
        # Both should be inf or both finite
        assert np.isinf(batch_result[k]) == np.isinf(individual), (
          f"Inf mismatch for {objective} at candidate {k}"
        )

  def test_structural_objectives_fallback(self, sample_data):
    """Structural objectives fall back to per-candidate loop — just verify they don't crash."""
    combined, candidates = sample_data
    for obj in ["max_gpr", "max_ter", "max_keep_score"]:
      result = _batch_score_candidates(combined, candidates[:, :3], obj)
      assert result.shape == (3,)
      assert all(np.isfinite(result))

  def test_short_series(self):
    """T < 2 should return inf for all candidates."""
    combined = np.array([5.0])
    candidates = np.array([[1.0, 2.0, 3.0]])
    result = _batch_score_candidates(combined, candidates, "max_sortino")
    assert result.shape == (3,)
    assert all(np.isinf(result))

  def test_single_candidate_matches(self, sample_data):
    """Single candidate batch should match individual call."""
    combined, candidates = sample_data
    single = candidates[:, :1]

    for obj in ["max_sortino", "max_sharpe", "max_calmar"]:
      batch_result = _batch_score_candidates(combined, single, obj)
      fn = SCORING_FUNCTIONS[obj]
      individual = fn(pd.Series(combined + single[:, 0]))
      if np.isfinite(individual):
        assert abs(batch_result[0] - individual) < 1e-6

  def test_all_winning_trades(self):
    """All positive PnL should produce finite scores for key objectives."""
    T = 200
    combined = np.ones(T) * 5.0
    candidates = np.ones((T, 3)) * 2.0
    for obj in ["max_sortino", "max_calmar", "max_profit_factor"]:
      result = _batch_score_candidates(combined, candidates, obj)
      assert result.shape == (3,)
      assert all(np.isfinite(result)), f"{obj} returned non-finite for all-positive PnL"

  def test_all_losing_trades(self):
    """All negative PnL — most objectives should return inf."""
    T = 200
    combined = np.ones(T) * -5.0
    candidates = np.ones((T, 3)) * -2.0
    for obj in ["max_sortino", "max_calmar", "max_recovery"]:
      result = _batch_score_candidates(combined, candidates, obj)
      assert result.shape == (3,)
      # All-negative equity: no gains, so most ratios are inf or degenerate
      fn = SCORING_FUNCTIONS[obj]
      for k in range(3):
        individual = fn(pd.Series(combined + candidates[:, k]))
        if np.isinf(individual):
          assert np.isinf(result[k])

  def test_empty_series(self):
    """T=0 should return inf without crashing."""
    combined = np.array([])
    candidates = np.empty((0, 3))
    result = _batch_score_candidates(combined, candidates, "max_sortino")
    assert result.shape == (3,)
    assert all(np.isinf(result))
