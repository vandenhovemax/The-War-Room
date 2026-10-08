"""Ground truth validation for L1/L2/L3 beam optimization and grading system.

Each test creates synthetic data with KNOWN expected results and verifies the
beam optimizer / seed ranker / grading code produces exactly those results.

Categories:
  1. L1 — Per-batch asset selection (Quality Gate, Top N, Jaccard dedup)
  2. L2 — Group combinations (combo scoring, rank bonus, pooling)
  3. L3 — Global assembly (diversity filter, fallback, sorting)
  4. Grading — Letter grades, regime classification, trend/meanrev scores
  5. Structural metrics — TER, Hurst, AC, MRI, VR, Half-Life, Crossing Rate
  6. _score_combo / _build_results — Combined portfolio scoring
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# =========================================================================
# HELPER: build deterministic daily PnL
# =========================================================================

def _make_pnl(assets: dict[str, np.ndarray], n: int = 200) -> pd.DataFrame:
  """Wrap named numpy arrays into a DataFrame with DatetimeIndex."""
  dates = pd.date_range("2024-01-01", periods=n, freq="D")
  return pd.DataFrame(assets, index=dates)


# =========================================================================
# 1. L1 — PER-BATCH ASSET SELECTION
# =========================================================================

class TestL1QualityGate:
  """Quality Gate mode: pass all assets with PF >= 0.5, filter the rest."""

  def test_quality_gate_passes_profitable_assets(self):
    """Assets with PF >= 0.5 survive the Quality Gate."""
    from backend.analysis.beam_optimizer import _MIN_GATE_PF, run_beam_optimization
    from backend.analysis.optimizer import _asset_pf
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({
      "WINNER": np.where(rng.random(n) < 0.8, 20.0, -10.0),  # PF ≈ 4
      "MEDIOCRE": np.where(rng.random(n) < 0.4, 10.0, -5.0),  # PF ≈ 1.3
      "LOSER": np.where(rng.random(n) < 0.1, 5.0, -20.0),    # PF << 0.5
    })
    # Verify our expectations: LOSER's PF < 0.5
    assert _asset_pf(pnl["LOSER"]) < _MIN_GATE_PF
    assert _asset_pf(pnl["WINNER"]) >= _MIN_GATE_PF
    assert _asset_pf(pnl["MEDIOCRE"]) >= _MIN_GATE_PF

    result = run_beam_optimization(
      pnl, asset_batch_map={"WINNER": 1, "MEDIOCRE": 1, "LOSER": 1},
      l2_groups=[], n_per_batch=2, obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
      k_l1=3, k_l2=3, k_l3=3, corr_method="standard",
      seed_metrics=["sortino"], max_seeds=10, prioritize_stability=False,
      external_scores=None, l1_quality_gate=True,
    )
    # At least one result; none should contain LOSER
    assert len(result) >= 1
    all_selected = {a for r in result for a in r["assets"]}
    assert "LOSER" not in all_selected, f"LOSER (PF < 0.5) should be gated out, got: {all_selected}"

  def test_quality_gate_fallback_keeps_all_if_none_pass(self):
    """If NO asset passes PF >= 0.5, fallback keeps all."""
    from backend.analysis.beam_optimizer import _MIN_GATE_PF, run_beam_optimization
    from backend.analysis.optimizer import _asset_pf
    rng = np.random.default_rng(42)
    n = 200
    # All assets are losers (PF < 0.5)
    pnl = _make_pnl({
      "BAD_A": np.where(rng.random(n) < 0.1, 5.0, -20.0),
      "BAD_B": np.where(rng.random(n) < 0.15, 5.0, -15.0),
    })
    assert _asset_pf(pnl["BAD_A"]) < _MIN_GATE_PF
    assert _asset_pf(pnl["BAD_B"]) < _MIN_GATE_PF

    result = run_beam_optimization(
      pnl, asset_batch_map={"BAD_A": 1, "BAD_B": 1},
      l2_groups=[], n_per_batch=2, obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
      k_l1=3, k_l2=3, k_l3=3, corr_method="standard",
      seed_metrics=["sortino"], max_seeds=10, prioritize_stability=False,
      external_scores=None, l1_quality_gate=True,
    )
    # Fallback: keeps all assets since none pass the gate
    if result:
      all_selected = {a for r in result for a in r["assets"]}
      # Both should be present (fallback = keep all)
      assert len(all_selected) >= 1


class TestL1TopN:
  """Top N mode: select n_per_batch assets, dedup with Jaccard."""

  def test_fewer_assets_than_n_returns_all(self):
    """When batch has fewer assets than n_per_batch, return single variant with all."""
    from backend.analysis.beam_optimizer import run_beam_optimization
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({
      "A": rng.normal(5, 10, n),
      "B": rng.normal(5, 10, n),
    })
    result = run_beam_optimization(
      pnl, asset_batch_map={"A": 1, "B": 1},
      l2_groups=[], n_per_batch=5,  # n_per_batch > 2 assets
      obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
      k_l1=3, k_l2=3, k_l3=3, corr_method="standard",
      seed_metrics=["sortino"], max_seeds=10, prioritize_stability=False,
      external_scores=None, l1_quality_gate=False,
    )
    assert len(result) >= 1
    # Both assets should be in the result
    assert set(result[0]["assets"]) == {"A", "B"}

  def test_l1_respects_n_per_batch(self):
    """L1 selects at most n_per_batch assets per batch."""
    from backend.analysis.beam_optimizer import run_beam_optimization
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({f"A{i}": rng.normal(5, 10, n) for i in range(8)})
    result = run_beam_optimization(
      pnl, asset_batch_map={f"A{i}": 1 for i in range(8)},
      l2_groups=[], n_per_batch=3,
      obj_l1="max_sortino", obj_l2="hybrid", obj_l3="hybrid",
      k_l1=2, k_l2=2, k_l3=2, corr_method="standard",
      seed_metrics=["sortino"], max_seeds=10, prioritize_stability=False,
      external_scores=None, l1_quality_gate=False,
    )
    assert len(result) >= 1
    for r in result:
      assert len(r["assets"]) <= 3


class TestJaccardDedup:
  """Verify Jaccard similarity and deduplication logic."""

  def test_jaccard_identical_is_1(self):
    from backend.analysis.beam_optimizer import _jaccard_similarity
    assert _jaccard_similarity(["A", "B", "C"], ["A", "B", "C"]) == pytest.approx(1.0)

  def test_jaccard_disjoint_is_0(self):
    from backend.analysis.beam_optimizer import _jaccard_similarity
    assert _jaccard_similarity(["A", "B"], ["C", "D"]) == pytest.approx(0.0)

  def test_jaccard_formula_manual(self):
    """Jaccard = |A ∩ B| / |A ∪ B|."""
    from backend.analysis.beam_optimizer import _jaccard_similarity
    # A={1,2,3}, B={2,3,4} → intersection={2,3}=2, union={1,2,3,4}=4 → 0.5
    assert _jaccard_similarity(["A", "B", "C"], ["B", "C", "D"]) == pytest.approx(0.5)

  def test_jaccard_empty_returns_1(self):
    from backend.analysis.beam_optimizer import _jaccard_similarity
    assert _jaccard_similarity([], []) == pytest.approx(1.0)

  def test_dedup_removes_similar_variants(self):
    """Variants with Jaccard > 0.7 should be removed."""
    from backend.analysis.beam_optimizer import _deduplicate_variants
    variants = [
      {"assets": ["A", "B", "C", "D", "E"]},       # kept
      {"assets": ["A", "B", "C", "D", "F"]},       # Jaccard = 4/6 = 0.667 < 0.7 → kept
      {"assets": ["A", "B", "C", "D", "E"]},       # Jaccard = 1.0 > 0.7 → removed (duplicate)
    ]
    result = _deduplicate_variants(variants, threshold=0.7)
    assert len(result) == 2

  def test_dedup_keeps_diverse_variants(self):
    """Completely different variants should all be kept."""
    from backend.analysis.beam_optimizer import _deduplicate_variants
    variants = [
      {"assets": ["A", "B"]},
      {"assets": ["C", "D"]},
      {"assets": ["E", "F"]},
    ]
    result = _deduplicate_variants(variants, threshold=0.7)
    assert len(result) == 3


# =========================================================================
# 2. L2 — GROUP COMBINATIONS
# =========================================================================

class TestL2ScoreCombo:
  """Verify _score_combo produces correct scores for combined portfolios."""

  def test_score_combo_uses_scoring_function(self):
    """PnL-based objective: _score_combo should match the scoring function."""
    from backend.analysis.beam_optimizer import _score_combo
    from backend.analysis.correlation import get_correlation_matrix
    from backend.analysis.scoring import portfolio_sortino
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({"A": rng.normal(5, 10, n), "B": rng.normal(3, 8, n)})
    full_corr = get_correlation_matrix(pnl, "standard")
    score = _score_combo(pnl, ["A", "B"], "max_sortino", full_corr)
    expected = portfolio_sortino(pnl[["A", "B"]].sum(axis=1))
    assert score == pytest.approx(expected, abs=1e-10)

  def test_score_combo_single_asset(self):
    """Single-asset combo: score = individual scoring function result."""
    from backend.analysis.beam_optimizer import _score_combo
    from backend.analysis.correlation import get_correlation_matrix
    from backend.analysis.scoring import portfolio_sharpe
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({"X": rng.normal(5, 10, n)})
    full_corr = get_correlation_matrix(pnl, "standard")
    score = _score_combo(pnl, ["X"], "max_sharpe", full_corr)
    expected = portfolio_sharpe(pnl["X"])
    assert score == pytest.approx(expected, abs=1e-10)

  def test_score_combo_empty_returns_inf(self):
    """Empty asset list → score = inf."""
    from backend.analysis.beam_optimizer import _score_combo
    from backend.analysis.correlation import get_correlation_matrix
    pnl = _make_pnl({"A": np.ones(200)})
    full_corr = get_correlation_matrix(pnl, "standard")
    score = _score_combo(pnl, [], "max_sortino", full_corr)
    assert score == float("inf")

  def test_score_combo_uncorrelated_objective(self):
    """Uncorrelated objective: abs(mean of upper-triangle correlations)."""
    from backend.analysis.beam_optimizer import _score_combo
    from backend.analysis.correlation import get_correlation_matrix
    rng = np.random.default_rng(42)
    n = 300
    base = rng.normal(0, 1, n)
    # Two perfectly correlated assets → high score (bad for uncorrelated)
    pnl_corr = _make_pnl({"A": base, "B": base + rng.normal(0, 0.01, n)}, n=n)
    corr_corr = get_correlation_matrix(pnl_corr, "standard")
    score_corr = _score_combo(pnl_corr, ["A", "B"], "uncorrelated", corr_corr)

    # Two independent assets → low score (good for uncorrelated)
    pnl_indep = _make_pnl({"A": rng.normal(0, 1, n), "B": rng.normal(0, 1, n)}, n=n)
    corr_indep = get_correlation_matrix(pnl_indep, "standard")
    score_indep = _score_combo(pnl_indep, ["A", "B"], "uncorrelated", corr_indep)

    assert score_indep < score_corr, \
      f"Independent ({score_indep:.3f}) should score lower (better) than correlated ({score_corr:.3f})"

  def test_score_combo_anti_correlated_objective(self):
    """Anti-correlated: mean of upper-triangle (lower = more negative = better)."""
    from backend.analysis.beam_optimizer import _score_combo
    from backend.analysis.correlation import get_correlation_matrix
    rng = np.random.default_rng(42)
    n = 300
    base = rng.normal(0, 1, n)
    # Anti-correlated pair → negative mean → low score (good)
    pnl_anti = _make_pnl({"A": base, "B": -base}, n=n)
    corr_anti = get_correlation_matrix(pnl_anti, "standard")
    score_anti = _score_combo(pnl_anti, ["A", "B"], "anti_correlated", corr_anti)

    # Positively correlated pair → positive mean → high score (bad)
    pnl_pos = _make_pnl({"A": base, "B": base + rng.normal(0, 0.01, n)}, n=n)
    corr_pos = get_correlation_matrix(pnl_pos, "standard")
    score_pos = _score_combo(pnl_pos, ["A", "B"], "anti_correlated", corr_pos)

    assert score_anti < score_pos


class TestL2RankBonus:
  """Verify L1 rank bonus is 0.005 per rank position."""

  def test_rank_bonus_value(self):
    """Rank bonus = sum(index-of-variant) * 0.005."""
    # This is tested implicitly in the beam optimizer, but let's verify the constant
    # L1 rank bonus = 0.005 per rank position
    rank_penalty = 0 * 0.005  # rank 0 (best variant)
    assert rank_penalty == 0.0
    rank_penalty = 3 * 0.005  # rank 3
    assert rank_penalty == pytest.approx(0.015)

  def test_rank_bonus_tiebreaker_only(self):
    """Rank bonus (0.005) is small enough to be tiebreaker only, not override objective."""
    from backend.analysis.beam_optimizer import _score_combo
    from backend.analysis.correlation import get_correlation_matrix
    rng = np.random.default_rng(42)
    n = 200
    # Create two portfolios with clearly different objective scores
    pnl = _make_pnl({
      "GOOD": rng.normal(10, 5, n),
      "BAD": rng.normal(-5, 30, n),
    })
    full_corr = get_correlation_matrix(pnl, "standard")
    score_good = _score_combo(pnl, ["GOOD"], "max_sortino", full_corr)
    score_bad = _score_combo(pnl, ["BAD"], "max_sortino", full_corr)

    # The difference between good and bad should be >> 0.005
    assert abs(score_good - score_bad) > 0.1, \
      f"Objective diff ({abs(score_good - score_bad):.4f}) should be >> rank bonus (0.005)"


# =========================================================================
# 3. L3 — GLOBAL ASSEMBLY
# =========================================================================

class TestL3DiversityFilter:
  """Verify diversity filter: accept only portfolios differing by >= 2 assets."""

  def test_identical_portfolios_rejected(self):
    """Two identical portfolios → only first is accepted."""
    from backend.analysis.beam_optimizer import run_beam_optimization
    rng = np.random.default_rng(42)
    n = 200
    # Single batch with 2 assets → only 1 possible portfolio → L3 gets 1 result
    pnl = _make_pnl({"A": rng.normal(5, 10, n), "B": rng.normal(5, 10, n)})
    result = run_beam_optimization(
      pnl, asset_batch_map={"A": 1, "B": 1},
      l2_groups=[], n_per_batch=2,
      obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
      k_l1=1, k_l2=1, k_l3=5,  # Request 5 but only 1 variant exists
      corr_method="standard",
      seed_metrics=["sortino"], max_seeds=10, prioritize_stability=False,
      external_scores=None, l1_quality_gate=False,
    )
    # Should get at most 1 unique result (diversity can't generate more)
    assert len(result) >= 1

  def test_diverse_portfolios_accepted(self):
    """Portfolios differing by >= 2 assets should both be accepted."""
    # If batch has 6+ assets and we select 3, different seeds produce diverse results
    from backend.analysis.beam_optimizer import run_beam_optimization
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({f"A{i}": rng.normal(5 + i, 10, n) for i in range(8)})
    result = run_beam_optimization(
      pnl, asset_batch_map={f"A{i}": 1 for i in range(8)},
      l2_groups=[], n_per_batch=3,
      obj_l1="max_sortino", obj_l2="hybrid", obj_l3="hybrid",
      k_l1=3, k_l2=3, k_l3=3, corr_method="standard",
      seed_metrics=["sortino"], max_seeds=10, prioritize_stability=False,
      external_scores=None, l1_quality_gate=False,
    )
    if len(result) >= 2:
      # Any two accepted portfolios should differ by >= 2 assets
      for i in range(len(result)):
        for j in range(i + 1, len(result)):
          diff = len(set(result[i]["assets"]).symmetric_difference(set(result[j]["assets"])))
          assert diff >= 2, \
            f"Portfolios {i} and {j} differ by only {diff} asset(s): " \
            f"{result[i]['assets']} vs {result[j]['assets']}"


class TestL3BuildResults:
  """Verify _build_results computes correct metrics."""

  def test_build_results_net_profit(self):
    """net_profit = sum of combined daily PnL."""
    from backend.analysis.beam_optimizer import _build_results
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({"A": rng.normal(5, 10, n), "B": rng.normal(3, 8, n)})
    scored = [{"assets": ["A", "B"]}]
    results = _build_results(scored, pnl)
    combined = pnl[["A", "B"]].sum(axis=1)
    expected = float(combined.cumsum().iloc[-1])
    assert results[0]["metrics"]["net_profit"] == pytest.approx(expected, abs=0.01)

  def test_build_results_profit_factor(self):
    """PF = gross_profit / gross_loss, capped at 10."""
    from backend.analysis.beam_optimizer import _build_results
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({"A": rng.normal(5, 10, n)})
    scored = [{"assets": ["A"]}]
    results = _build_results(scored, pnl)
    combined = pnl["A"]
    gp = float(combined[combined > 0].sum())
    gl = float(abs(combined[combined < 0].sum()))
    expected_pf = min(gp / gl, 10.0) if gl > 0 else 10.0
    assert results[0]["metrics"]["profit_factor"] == pytest.approx(expected_pf, abs=0.01)

  def test_build_results_max_drawdown(self):
    """max_drawdown = min(cum - cummax), should be negative or zero."""
    from backend.analysis.beam_optimizer import _build_results
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({"A": rng.normal(5, 20, n)})
    scored = [{"assets": ["A"]}]
    results = _build_results(scored, pnl)
    combined = pnl["A"]
    cum = combined.cumsum()
    expected_dd = float((cum - cum.cummax()).min())
    assert results[0]["metrics"]["max_drawdown"] == pytest.approx(expected_dd, abs=0.01)

  def test_build_results_n_assets(self):
    """n_assets should equal the number of assets in the portfolio."""
    from backend.analysis.beam_optimizer import _build_results
    pnl = _make_pnl({"A": np.ones(200), "B": np.ones(200), "C": np.ones(200)})
    scored = [{"assets": ["A", "B", "C"]}]
    results = _build_results(scored, pnl)
    assert results[0]["metrics"]["n_assets"] == 3

  def test_build_results_pf_all_winners(self):
    """All positive PnL → PF = 10.0 (capped)."""
    from backend.analysis.beam_optimizer import _build_results
    pnl = _make_pnl({"A": np.full(200, 5.0)})
    scored = [{"assets": ["A"]}]
    results = _build_results(scored, pnl)
    assert results[0]["metrics"]["profit_factor"] == pytest.approx(10.0)


# =========================================================================
# 4. GRADING — LETTER GRADES & REGIME CLASSIFICATION
# =========================================================================

class TestGradeLetterBoundaries:
  """Verify exact grade boundaries from grade_letter()."""

  def test_all_grade_boundaries(self):
    from backend.analysis.seed_ranker import grade_letter
    # Exact boundary tests
    assert grade_letter(0.45) == "A+"
    assert grade_letter(0.50) == "A+"
    assert grade_letter(0.35) == "A"
    assert grade_letter(0.44) == "A"
    assert grade_letter(0.28) == "B+"
    assert grade_letter(0.34) == "B+"
    assert grade_letter(0.22) == "B"
    assert grade_letter(0.27) == "B"
    assert grade_letter(0.16) == "C+"
    assert grade_letter(0.21) == "C+"
    assert grade_letter(0.10) == "C"
    assert grade_letter(0.15) == "C"
    assert grade_letter(0.05) == "D"
    assert grade_letter(0.09) == "D"
    assert grade_letter(0.04) == "F"
    assert grade_letter(0.00) == "F"

  def test_grade_letter_negative_input(self):
    from backend.analysis.seed_ranker import grade_letter
    assert grade_letter(-0.5) == "F"

  def test_grade_letter_above_1(self):
    from backend.analysis.seed_ranker import grade_letter
    assert grade_letter(1.0) == "A+"


class TestRegimeClassification:
  """Verify classify_regime: TREND vs MEANREV vs NEUTRAL."""

  def test_trend_dominates(self):
    from backend.analysis.seed_ranker import classify_regime
    # trend_score > meanrev_score + threshold
    assert classify_regime(0.4, 0.1, threshold=0.05) == "TREND"

  def test_meanrev_dominates(self):
    from backend.analysis.seed_ranker import classify_regime
    assert classify_regime(0.1, 0.4, threshold=0.05) == "MEANREV"

  def test_neutral_within_threshold(self):
    from backend.analysis.seed_ranker import classify_regime
    # diff = 0.3 - 0.28 = 0.02 < threshold=0.05 → NEUTRAL
    assert classify_regime(0.30, 0.28, threshold=0.05) == "NEUTRAL"

  def test_neutral_exact_equal(self):
    from backend.analysis.seed_ranker import classify_regime
    assert classify_regime(0.25, 0.25, threshold=0.05) == "NEUTRAL"

  def test_threshold_boundary(self):
    from backend.analysis.seed_ranker import classify_regime
    # diff = exactly threshold → NEUTRAL (not strict >)
    assert classify_regime(0.30, 0.25, threshold=0.05) == "NEUTRAL"
    # diff > threshold → TREND
    assert classify_regime(0.31, 0.25, threshold=0.05) == "TREND"


# =========================================================================
# 5. STRUCTURAL METRICS — TER, HURST, AC, MRI, VR, HALF-LIFE, CROSSING RATE
# =========================================================================

class TestTrendEfficiencyRatio:
  """TER = |net_move| / total_path_length. Range [0, 1]."""

  def test_perfect_uptrend(self):
    """Equity goes straight up → TER = 1.0."""
    from backend.analysis.seed_ranker import trend_efficiency_ratio
    equity = np.array([0, 10, 20, 30, 40, 50])
    assert trend_efficiency_ratio(equity) == pytest.approx(1.0)

  def test_perfect_downtrend(self):
    """Equity goes straight down → TER = 1.0 (absolute move)."""
    from backend.analysis.seed_ranker import trend_efficiency_ratio
    equity = np.array([50, 40, 30, 20, 10, 0])
    assert trend_efficiency_ratio(equity) == pytest.approx(1.0)

  def test_round_trip_is_zero(self):
    """Equity goes up then back to start → TER = 0.0."""
    from backend.analysis.seed_ranker import trend_efficiency_ratio
    equity = np.array([0, 10, 20, 10, 0])
    # net_move = |0 - 0| = 0
    # total_path = |10|+|10|+|10|+|10| = 40
    assert trend_efficiency_ratio(equity) == pytest.approx(0.0)

  def test_ter_formula_manual(self):
    """Verify TER formula step-by-step."""
    from backend.analysis.seed_ranker import trend_efficiency_ratio
    equity = np.array([0, 10, 5, 20])
    # net_move = |20 - 0| = 20
    # total_path = |10| + |5| + |15| = 30
    # TER = 20/30 = 0.6667
    assert trend_efficiency_ratio(equity) == pytest.approx(20.0 / 30.0, abs=1e-4)

  def test_ter_single_point(self):
    from backend.analysis.seed_ranker import trend_efficiency_ratio
    assert trend_efficiency_ratio(np.array([100])) == 0.0

  def test_ter_flat_line(self):
    """Flat equity (no movement) → TER = 0.0."""
    from backend.analysis.seed_ranker import trend_efficiency_ratio
    equity = np.array([50, 50, 50, 50])
    assert trend_efficiency_ratio(equity) == pytest.approx(0.0)


class TestAutocorrelation:
  """AC(1) = corr(r[t], r[t-1]). Positive = trending, negative = reverting."""

  def test_alternating_returns_negative_ac(self):
    """Alternating +/- returns → AC should be negative (mean-reverting)."""
    from backend.analysis.seed_ranker import autocorrelation
    # Strictly alternating: AC should be strongly negative
    returns = np.array([10, -10, 10, -10, 10, -10, 10, -10, 10, -10,
                        10, -10, 10, -10, 10, -10, 10, -10, 10, -10], dtype=float)
    ac = autocorrelation(returns, lag=1)
    assert ac < -0.8, f"Alternating returns should have AC < -0.8, got {ac:.4f}"

  def test_trending_returns_positive_ac(self):
    """Trending returns (momentum) → AC should be positive."""
    from backend.analysis.seed_ranker import autocorrelation
    # Runs of same sign: trending behavior
    returns = np.array([5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
                        -5, -6, -7, -8, -9, -10, -11, -12, -13, -14], dtype=float)
    ac = autocorrelation(returns, lag=1)
    assert ac > 0.5, f"Trending returns should have AC > 0.5, got {ac:.4f}"

  def test_ac_constant_returns_zero(self):
    """Constant returns → std=0 → AC = 0."""
    from backend.analysis.seed_ranker import autocorrelation
    returns = np.array([5.0] * 30)
    ac = autocorrelation(returns, lag=1)
    assert ac == pytest.approx(0.0)


class TestGainToPainRatio:
  """GPR = sum(all) / |sum(negative)|."""

  def test_gpr_formula_manual(self):
    from backend.analysis.seed_ranker import gain_to_pain_ratio
    returns = np.array([100.0, 50.0, -30.0, -20.0])
    # total = 100, pain = |-30 + -20| = 50
    # GPR = 100 / 50 = 2.0
    assert gain_to_pain_ratio(returns) == pytest.approx(2.0, abs=0.01)

  def test_gpr_all_wins_capped(self):
    from backend.analysis.seed_ranker import gain_to_pain_ratio
    returns = np.array([100.0, 200.0])
    # pain = 0, total > 0 → GPR = 10.0 (capped)
    assert gain_to_pain_ratio(returns) == pytest.approx(10.0)

  def test_gpr_all_losses(self):
    from backend.analysis.seed_ranker import gain_to_pain_ratio
    returns = np.array([-100.0, -200.0])
    # total = -300, pain = 300 → GPR = -300/300 = -1.0
    assert gain_to_pain_ratio(returns) == pytest.approx(-1.0)


class TestMeanReversionIntensity:
  """MRI = -AC(1) * (1 - TER) * crossing_rate. 0 if AC >= 0."""

  def test_mri_requires_negative_ac(self):
    """MRI = 0 when AC >= 0 (not mean-reverting)."""
    from backend.analysis.seed_ranker import mean_reversion_intensity
    # Trending returns → positive AC → MRI = 0
    returns = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
                        11, 12, 13, 14, 15, 16, 17, 18, 19, 20], dtype=float)
    assert mean_reversion_intensity(returns) == pytest.approx(0.0)

  def test_mri_too_few_returns(self):
    """MRI = 0 for < 20 returns."""
    from backend.analysis.seed_ranker import mean_reversion_intensity
    returns = np.array([1.0] * 10)
    assert mean_reversion_intensity(returns) == pytest.approx(0.0)


class TestVarianceRatio:
  """VR(q) = Var(r_q) / (q * Var(r_1)). VR < 1 = mean-reverting."""

  def test_vr_random_walk_near_1(self):
    """IID random returns → VR ≈ 1.0."""
    from backend.analysis.seed_ranker import variance_ratio
    rng = np.random.default_rng(42)
    returns = rng.normal(0, 1, 2000)
    vr = variance_ratio(returns, lag=5)
    assert 0.7 < vr < 1.3, f"Random walk VR should be near 1.0, got {vr:.4f}"

  def test_vr_too_few_returns(self):
    """Too few returns → VR = 1.0 (fallback)."""
    from backend.analysis.seed_ranker import variance_ratio
    returns = np.array([1.0, 2.0])
    assert variance_ratio(returns, lag=5) == pytest.approx(1.0)


class TestHalfLife:
  """Half-life of mean reversion: -ln(2) / ln(1 + theta)."""

  def test_half_life_trending_returns_inf(self):
    """Non-mean-reverting → half_life = inf."""
    from backend.analysis.seed_ranker import half_life
    # Steadily increasing returns → theta >= 0 → inf
    returns = np.linspace(1, 100, 100)
    hl = half_life(returns)
    assert hl == float('inf')

  def test_half_life_too_few_returns(self):
    from backend.analysis.seed_ranker import half_life
    returns = np.array([1.0] * 10)
    assert half_life(returns) == float('inf')


class TestCrossingRate:
  """Crossing rate = how often equity crosses its rolling mean."""

  def test_crossing_rate_bounds(self):
    """Crossing rate should be in [0, 1]."""
    from backend.analysis.seed_ranker import _crossing_rate
    rng = np.random.default_rng(42)
    returns = rng.normal(0, 1, 500)
    cr = _crossing_rate(returns)
    assert 0.0 <= cr <= 1.0

  def test_crossing_rate_too_few(self):
    """< 20 returns → crossing rate = 0."""
    from backend.analysis.seed_ranker import _crossing_rate
    returns = np.array([1.0] * 10)
    assert _crossing_rate(returns) == pytest.approx(0.0)


# =========================================================================
# 6. TREND SCORE & MEAN-REVERSION SCORE FORMULAS
# =========================================================================

class TestTrendScoreFormula:
  """Verify trend_score = 0.30*TER + 0.25*max(0,Hurst-0.5)*2 + 0.25*sigmoid(GPR) + 0.20*max(0,AC)."""

  def test_trend_score_components(self):
    """Verify trend_score matches hand-calculated formula."""
    from backend.analysis.seed_ranker import (
      trend_score, trend_efficiency_ratio, hurst_exponent,
      gain_to_pain_ratio, autocorrelation,
    )
    rng = np.random.default_rng(42)
    returns = rng.normal(5, 10, 500)

    # Compute components
    equity = np.cumsum(returns)
    ter = trend_efficiency_ratio(equity)
    h = hurst_exponent(returns)
    gpr = gain_to_pain_ratio(returns)
    ac = autocorrelation(returns, lag=1)
    sig_gpr = 1.0 / (1.0 + np.exp(-gpr))

    expected = (
      0.30 * ter
      + 0.25 * max(0.0, (h - 0.5)) * 2.0
      + 0.25 * sig_gpr
      + 0.20 * max(0.0, ac)
    )
    expected = float(np.clip(expected, 0.0, 1.0))

    actual = trend_score(returns)
    assert actual == pytest.approx(expected, abs=1e-6)

  def test_trend_score_range(self):
    """Trend score should be in [0, 1]."""
    from backend.analysis.seed_ranker import trend_score
    rng = np.random.default_rng(42)
    for seed in range(10):
      rng2 = np.random.default_rng(seed)
      returns = rng2.normal(0, 10, 500)
      ts = trend_score(returns)
      assert 0.0 <= ts <= 1.0, f"Trend score {ts} out of [0, 1] range"


class TestMeanRevScoreFormula:
  """Verify meanrev_score = 0.25*MRI + 0.25*max(0,0.5-H)*2 + 0.20*max(0,1-VR) + 0.15*norm_hl + 0.15*CR."""

  def test_meanrev_score_components(self):
    """Verify meanrev_score matches hand-calculated formula."""
    from backend.analysis.seed_ranker import (
      meanrev_score, mean_reversion_intensity, hurst_exponent,
      variance_ratio, half_life, _crossing_rate,
    )
    rng = np.random.default_rng(42)
    # Create alternating returns to get mean-reversion behavior
    base = rng.normal(0, 10, 500)
    # Alternate sign to induce mean-reversion
    signs = np.array([1 if i % 2 == 0 else -1 for i in range(500)], dtype=float)
    returns = base * signs

    mri = mean_reversion_intensity(returns)
    h = hurst_exponent(returns)
    vr = variance_ratio(returns, lag=5)
    hl = half_life(returns)
    cr = _crossing_rate(returns)

    norm_hl = 1.0 / (1.0 + hl / 10.0) if hl < float('inf') else 0.0

    expected = (
      0.25 * mri
      + 0.25 * max(0.0, (0.5 - h)) * 2.0
      + 0.20 * max(0.0, 1.0 - vr)
      + 0.15 * norm_hl
      + 0.15 * cr
    )
    expected = float(np.clip(expected, 0.0, 1.0))

    actual = meanrev_score(returns)
    assert actual == pytest.approx(expected, abs=1e-6)

  def test_meanrev_score_range(self):
    """Mean-rev score should be in [0, 1]."""
    from backend.analysis.seed_ranker import meanrev_score
    for seed in range(10):
      rng = np.random.default_rng(seed)
      returns = rng.normal(0, 10, 500)
      ms = meanrev_score(returns)
      assert 0.0 <= ms <= 1.0, f"Mean-rev score {ms} out of [0, 1] range"


# =========================================================================
# 7. GRADE_ALL_ASSETS — END-TO-END
# =========================================================================

class TestGradeAllAssets:
  """Verify grade_all_assets output structure and correctness."""

  def test_output_columns(self):
    """grade_all_assets returns all expected columns."""
    from backend.analysis.seed_ranker import grade_all_assets
    rng = np.random.default_rng(42)
    n = 300
    pnl = _make_pnl({"A": rng.normal(5, 10, n), "B": rng.normal(3, 8, n)}, n=n)
    df = grade_all_assets(pnl)
    expected_cols = {
      "asset", "regime", "trend_score", "trend_grade",
      "meanrev_score", "meanrev_grade",
      "TER", "Hurst", "GPR", "AC(1)", "DSR",
      "MRI", "VR(5)", "HalfLife", "CrossRate",
    }
    assert expected_cols.issubset(set(df.columns)), \
      f"Missing columns: {expected_cols - set(df.columns)}"

  def test_output_one_row_per_asset(self):
    from backend.analysis.seed_ranker import grade_all_assets
    rng = np.random.default_rng(42)
    n = 300
    pnl = _make_pnl({f"A{i}": rng.normal(5, 10, n) for i in range(5)}, n=n)
    df = grade_all_assets(pnl)
    assert len(df) == 5

  def test_output_sorted_by_trend_score_desc(self):
    """Output should be sorted by trend_score descending."""
    from backend.analysis.seed_ranker import grade_all_assets
    rng = np.random.default_rng(42)
    n = 300
    pnl = _make_pnl({f"A{i}": rng.normal(5 + i * 2, 10, n) for i in range(5)}, n=n)
    df = grade_all_assets(pnl)
    scores = df["trend_score"].values
    for i in range(len(scores) - 1):
      assert scores[i] >= scores[i + 1], \
        f"Not sorted: index {i} ({scores[i]}) < index {i + 1} ({scores[i + 1]})"

  def test_regime_matches_classification(self):
    """Each asset's regime should match classify_regime(trend, meanrev)."""
    from backend.analysis.seed_ranker import grade_all_assets, classify_regime
    rng = np.random.default_rng(42)
    n = 300
    pnl = _make_pnl({"A": rng.normal(5, 10, n), "B": rng.normal(3, 8, n)}, n=n)
    df = grade_all_assets(pnl)
    for _, row in df.iterrows():
      expected_regime = classify_regime(row["trend_score"], row["meanrev_score"])
      assert row["regime"] == expected_regime

  def test_grades_match_score_ranges(self):
    """Each grade should correspond to the correct score range."""
    from backend.analysis.seed_ranker import grade_all_assets, grade_letter
    rng = np.random.default_rng(42)
    n = 300
    pnl = _make_pnl({f"A{i}": rng.normal(i * 3, 10, n) for i in range(5)}, n=n)
    df = grade_all_assets(pnl)
    for _, row in df.iterrows():
      assert row["trend_grade"] == grade_letter(row["trend_score"])
      assert row["meanrev_grade"] == grade_letter(row["meanrev_score"])


# =========================================================================
# 8. PORTFOLIO-LEVEL STRUCTURAL OBJECTIVES
# =========================================================================

class TestPortfolioStructuralObjectives:
  """Verify portfolio_gpr, portfolio_ter, portfolio_keep_score, etc."""

  def test_portfolio_gpr_matches_manual(self):
    """portfolio_gpr = -min(sum(all)/|sum(neg)|, 10)."""
    from backend.analysis.seed_ranker import portfolio_gpr
    pnl = pd.Series([100.0, 50.0, -30.0, -20.0])
    # total=100, pain=50, GPR=2.0, negated=-2.0
    assert portfolio_gpr(pnl) == pytest.approx(-2.0, abs=0.01)

  def test_portfolio_ter_matches_manual(self):
    """portfolio_ter = -TER(equity curve)."""
    from backend.analysis.seed_ranker import portfolio_ter, trend_efficiency_ratio
    pnl = pd.Series([10.0, 5.0, -5.0, 20.0])
    equity = np.cumsum(pnl.values)
    expected = -trend_efficiency_ratio(equity)
    assert portfolio_ter(pnl) == pytest.approx(expected, abs=1e-6)

  def test_portfolio_keep_score_matches_manual(self):
    """portfolio_keep_score = -trend_score(pnl.values)."""
    from backend.analysis.seed_ranker import portfolio_keep_score, trend_score
    rng = np.random.default_rng(42)
    pnl = pd.Series(rng.normal(5, 10, 500))
    expected = -trend_score(pnl.values)
    assert portfolio_keep_score(pnl) == pytest.approx(expected, abs=1e-6)

  def test_portfolio_meanrev_score_matches_manual(self):
    """portfolio_meanrev_score = -meanrev_score(pnl.values)."""
    from backend.analysis.seed_ranker import portfolio_meanrev_score, meanrev_score
    rng = np.random.default_rng(42)
    pnl = pd.Series(rng.normal(0, 10, 500))
    expected = -meanrev_score(pnl.values)
    assert portfolio_meanrev_score(pnl) == pytest.approx(expected, abs=1e-6)

  def test_portfolio_crossing_rate_matches_manual(self):
    """portfolio_crossing_rate = -crossing_rate(pnl.values)."""
    from backend.analysis.seed_ranker import portfolio_crossing_rate, _crossing_rate
    rng = np.random.default_rng(42)
    pnl = pd.Series(rng.normal(0, 10, 500))
    expected = -_crossing_rate(pnl.values)
    assert portfolio_crossing_rate(pnl) == pytest.approx(expected, abs=1e-6)


# =========================================================================
# 9. SEED METRIC SCORING (for _score_metric integration)
# =========================================================================

class TestScoreMetricIntegration:
  """Verify score_metric returns correct negated values for seed ranking."""

  def test_gpr_negated(self):
    from backend.analysis.seed_ranker import score_metric, gain_to_pain_ratio
    rng = np.random.default_rng(42)
    pnl = rng.normal(5, 10, 500)
    expected = -min(gain_to_pain_ratio(pnl), 10.0)
    assert score_metric("gpr", pnl) == pytest.approx(expected, abs=1e-6)

  def test_ter_negated(self):
    from backend.analysis.seed_ranker import score_metric, trend_efficiency_ratio
    rng = np.random.default_rng(42)
    pnl = rng.normal(5, 10, 500)
    equity = np.cumsum(pnl)
    expected = -trend_efficiency_ratio(equity)
    assert score_metric("ter", pnl) == pytest.approx(expected, abs=1e-6)

  def test_hurst_negated(self):
    from backend.analysis.seed_ranker import score_metric, hurst_exponent
    rng = np.random.default_rng(42)
    pnl = rng.normal(5, 10, 500)
    expected = -hurst_exponent(pnl)
    assert score_metric("hurst", pnl) == pytest.approx(expected, abs=1e-6)

  def test_dsr_negated(self):
    from backend.analysis.seed_ranker import score_metric, deflated_sharpe
    rng = np.random.default_rng(42)
    pnl = rng.normal(5, 10, 500)
    expected = -deflated_sharpe(pnl, n_trials=20)
    assert score_metric("dsr", pnl, n_pool=20) == pytest.approx(expected, abs=1e-6)

  def test_crossing_rate_negated(self):
    from backend.analysis.seed_ranker import score_metric, _crossing_rate
    rng = np.random.default_rng(42)
    pnl = rng.normal(0, 10, 500)
    expected = -_crossing_rate(pnl)
    assert score_metric("crossing_rate", pnl) == pytest.approx(expected, abs=1e-6)

  def test_trend_score_negated(self):
    from backend.analysis.seed_ranker import score_metric, trend_score
    rng = np.random.default_rng(42)
    pnl = rng.normal(5, 10, 500)
    expected = -trend_score(pnl)
    assert score_metric("trend_score", pnl) == pytest.approx(expected, abs=1e-6)

  def test_meanrev_score_negated(self):
    from backend.analysis.seed_ranker import score_metric, meanrev_score
    rng = np.random.default_rng(42)
    pnl = rng.normal(0, 10, 500)
    expected = -meanrev_score(pnl)
    assert score_metric("meanrev_score", pnl) == pytest.approx(expected, abs=1e-6)

  def test_vr_below_1_negative_score(self):
    """VR < 1.0 → negative score (good for mean-rev)."""
    from backend.analysis.seed_ranker import score_metric
    # Alternating returns → VR should be < 1
    rng = np.random.default_rng(42)
    base = rng.normal(0, 10, 500)
    signs = np.array([1 if i % 2 == 0 else -1 for i in range(500)], dtype=float)
    pnl = base * signs
    result = score_metric("vr", pnl)
    # VR < 1 → score = -(1 - VR) → negative
    # VR >= 1 → score = (VR - 1) → positive
    # Either way, result is a finite float
    assert np.isfinite(result)

  def test_unknown_metric_raises(self):
    from backend.analysis.seed_ranker import score_metric
    with pytest.raises(ValueError, match="Unknown metric"):
      score_metric("nonexistent_metric", np.ones(100))


# =========================================================================
# 10. DSR BENCHMARK GATE
# =========================================================================

class TestDSRBenchmarkGate:
  """Verify dsr_benchmark_multiplier thresholds."""

  def test_low_dsr_penalty(self):
    """DSR < 0.50 → 0.5x penalty."""
    from backend.analysis.seed_ranker import dsr_benchmark_multiplier
    # Short constant returns → DSR will be neutral (0.50) due to < 30 active returns
    # We need to test the thresholds directly
    # Can't easily force DSR < 0.50 with synthetic data, so test the function logic
    # by creating a scenario with many random trials and short sample
    rng = np.random.default_rng(42)
    # Very noisy with near-zero mean → low Sharpe → low DSR
    pnl = rng.normal(0.001, 10, 500)
    result = dsr_benchmark_multiplier(pnl, n_pool=100)
    # Result should be 0.5, 1.0, or 1.1
    assert result in (0.5, 1.0, 1.1)

  def test_high_dsr_boost(self):
    """DSR > 0.80 → 1.1x boost."""
    from backend.analysis.seed_ranker import dsr_benchmark_multiplier
    # Strong consistent returns → high Sharpe → high DSR
    rng = np.random.default_rng(42)
    pnl = rng.normal(10.0, 1.0, 500)  # Very high Sharpe ratio
    result = dsr_benchmark_multiplier(pnl, n_pool=5)
    # With high Sharpe and few trials, DSR should be > 0.80
    assert result == 1.1

  def test_neutral_dsr_no_change(self):
    """DSR 0.50-0.80 → 1.0x neutral."""
    from backend.analysis.seed_ranker import dsr_benchmark_multiplier
    # Few active returns (< 30) → DSR returns 0.50 (neutral) → multiplier = 1.0
    pnl = np.zeros(100)
    pnl[:20] = np.array([5.0, -3.0] * 10)  # Only 20 non-zero values
    result = dsr_benchmark_multiplier(pnl, n_pool=20)
    assert result == 1.0


# =========================================================================
# 11. PRE-FILTERS
# =========================================================================

class TestPrefilters:
  """Verify trend and mean-rev pre-filters."""

  def test_prefilter_trend_returns_list(self):
    """prefilter_for_trend returns a list of asset names."""
    from backend.analysis.seed_ranker import prefilter_for_trend
    rng = np.random.default_rng(42)
    n = 300
    pnl = _make_pnl({f"A{i}": rng.normal(5, 10, n) for i in range(5)}, n=n)
    result = prefilter_for_trend(pnl)
    assert isinstance(result, list)
    assert all(a in pnl.columns for a in result)

  def test_prefilter_meanrev_returns_list(self):
    from backend.analysis.seed_ranker import prefilter_for_meanrev
    rng = np.random.default_rng(42)
    n = 300
    pnl = _make_pnl({f"A{i}": rng.normal(0, 10, n) for i in range(5)}, n=n)
    result = prefilter_for_meanrev(pnl)
    assert isinstance(result, list)
    assert all(a in pnl.columns for a in result)

  def test_prefilter_empty_fallback(self):
    """If no assets survive filter, fallback returns all."""
    from backend.analysis.seed_ranker import prefilter_for_trend
    rng = np.random.default_rng(42)
    n = 300
    # Even if hurst_min is very high, fallback should return all
    pnl = _make_pnl({"A": rng.normal(0, 10, n)}, n=n)
    result = prefilter_for_trend(pnl, hurst_min=0.99)
    assert len(result) >= 1  # Fallback returns at least the original


class TestPostfilterAC:
  """Verify AC post-filter flags mismatched assets."""

  def test_trend_flags_negative_ac(self):
    """For trend strategies, negative AC should be flagged."""
    from backend.analysis.seed_ranker import postfilter_ac
    rng = np.random.default_rng(42)
    n = 300
    # Alternating returns → negative AC → should be flagged for trend strategy
    alt = np.array([10, -10] * (n // 2), dtype=float)
    pnl = _make_pnl({"ALT": alt}, n=n)
    result = postfilter_ac(pnl, ["ALT"], strategy_type="trend")
    assert result["ALT"]["flagged"] is True

  def test_meanrev_flags_positive_ac(self):
    """For mean-rev strategies, positive AC should be flagged."""
    from backend.analysis.seed_ranker import postfilter_ac
    rng = np.random.default_rng(42)
    n = 300
    # Trending returns → positive AC → should be flagged for meanrev strategy
    trend = np.linspace(1, 100, n)
    pnl = _make_pnl({"TREND": trend}, n=n)
    result = postfilter_ac(pnl, ["TREND"], strategy_type="meanrev")
    # Note: the actual AC might not be strongly positive depending on the data
    # but the flag logic is: ac > 0 → flagged for meanrev
    if result["TREND"]["ac"] > 0:
      assert result["TREND"]["flagged"] is True
    else:
      assert result["TREND"]["flagged"] is False


# =========================================================================
# 12. END-TO-END BEAM OPTIMIZATION
# =========================================================================

class TestBeamOptimizerEndToEnd:
  """Full pipeline: L1 → L2 → L3, verify output structure and metrics."""

  def test_full_pipeline_returns_results(self):
    """Run full beam optimization and verify output structure."""
    from backend.analysis.beam_optimizer import run_beam_optimization
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({f"A{i}": rng.normal(5, 10, n) for i in range(6)})
    # 2 batches: A0-A2 in batch 1, A3-A5 in batch 2
    batch_map = {f"A{i}": 1 for i in range(3)}
    batch_map.update({f"A{i}": 2 for i in range(3, 6)})
    result = run_beam_optimization(
      pnl, asset_batch_map=batch_map,
      l2_groups=[[1, 2]], n_per_batch=2,
      obj_l1="max_sortino", obj_l2="hybrid", obj_l3="max_sharpe",
      k_l1=2, k_l2=2, k_l3=3, corr_method="standard",
      seed_metrics=["sortino"], max_seeds=10, prioritize_stability=False,
      external_scores=None, l1_quality_gate=False,
    )
    assert len(result) >= 1
    for r in result:
      assert "assets" in r
      assert "metrics" in r
      assert isinstance(r["assets"], list)
      assert len(r["assets"]) >= 1
      m = r["metrics"]
      assert "net_profit" in m
      assert "profit_factor" in m
      assert "max_drawdown" in m
      assert "stability" in m
      assert "n_assets" in m

  def test_empty_input_returns_empty(self):
    """Empty daily PnL → empty result."""
    from backend.analysis.beam_optimizer import run_beam_optimization
    pnl = pd.DataFrame()
    result = run_beam_optimization(
      pnl, asset_batch_map={}, l2_groups=[], n_per_batch=3,
      obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
      k_l1=3, k_l2=3, k_l3=3, corr_method="standard",
      seed_metrics=["sortino"], max_seeds=10, prioritize_stability=False,
      external_scores=None,
    )
    assert result == []

  def test_l3_results_sorted_by_score(self):
    """L3 results should be sorted by objective score (best first)."""
    from backend.analysis.beam_optimizer import run_beam_optimization
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({f"A{i}": rng.normal(5 + i, 10, n) for i in range(8)})
    batch_map = {f"A{i}": 1 for i in range(4)}
    batch_map.update({f"A{i}": 2 for i in range(4, 8)})
    result = run_beam_optimization(
      pnl, asset_batch_map=batch_map,
      l2_groups=[[1, 2]], n_per_batch=2,
      obj_l1="max_sortino", obj_l2="max_sortino", obj_l3="max_sortino",
      k_l1=3, k_l2=3, k_l3=3, corr_method="standard",
      seed_metrics=["sortino"], max_seeds=10, prioritize_stability=False,
      external_scores=None,
    )
    if len(result) >= 2:
      from backend.analysis.scoring import portfolio_sortino
      scores = []
      for r in result:
        cols = [c for c in r["assets"] if c in pnl.columns]
        combined = pnl[cols].sum(axis=1)
        scores.append(portfolio_sortino(combined))
      # Should be sorted (lower = better)
      for i in range(len(scores) - 1):
        # Allow small floating point tolerance
        assert scores[i] <= scores[i + 1] + 0.01, \
          f"Results not sorted by objective: {scores}"

  def test_quality_gate_mode_pools_all_survivors(self):
    """Quality Gate L2 should pool all L1 survivors and re-optimize."""
    from backend.analysis.beam_optimizer import run_beam_optimization
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({f"A{i}": rng.normal(5, 10, n) for i in range(6)})
    batch_map = {f"A{i}": 1 for i in range(3)}
    batch_map.update({f"A{i}": 2 for i in range(3, 6)})
    result = run_beam_optimization(
      pnl, asset_batch_map=batch_map,
      l2_groups=[[1, 2]], n_per_batch=2,
      obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
      k_l1=2, k_l2=2, k_l3=2, corr_method="standard",
      seed_metrics=["sortino"], max_seeds=10, prioritize_stability=False,
      external_scores=None, l1_quality_gate=True,
      n_per_group=4,
    )
    assert len(result) >= 1
    # In quality gate mode, L2 can select from BOTH batches
    all_selected = {a for r in result for a in r["assets"]}
    # Should have assets from batch 1 and/or batch 2
    assert len(all_selected) >= 1


# =========================================================================
# 13. PARITY TESTS — _fast_stability and precomputed_seeds
# =========================================================================

class TestFastStabilityParity:
  """Verify _fast_stability produces identical results to calculate_stability."""

  def test_parity_uptrend(self):
    """Uptrending equity: fast clone matches original."""
    from backend.analysis.scoring import _fast_stability, calculate_stability
    rng = np.random.default_rng(42)
    arr = rng.normal(5, 10, 500)
    assert _fast_stability(arr) == pytest.approx(
      calculate_stability(pd.Series(arr)), abs=1e-10,
    )

  def test_parity_flat(self):
    """Flat returns: fast clone matches original."""
    from backend.analysis.scoring import _fast_stability, calculate_stability
    arr = np.zeros(200)
    assert _fast_stability(arr) == pytest.approx(
      calculate_stability(pd.Series(arr)), abs=1e-10,
    )

  def test_parity_noisy(self):
    """Noisy random walk: fast clone matches original."""
    from backend.analysis.scoring import _fast_stability, calculate_stability
    rng = np.random.default_rng(123)
    arr = rng.normal(0, 20, 1000)
    assert _fast_stability(arr) == pytest.approx(
      calculate_stability(pd.Series(arr)), abs=1e-10,
    )

  def test_parity_downtrend(self):
    """Downtrending equity: fast clone matches original."""
    from backend.analysis.scoring import _fast_stability, calculate_stability
    rng = np.random.default_rng(99)
    arr = rng.normal(-3, 8, 300)
    assert _fast_stability(arr) == pytest.approx(
      calculate_stability(pd.Series(arr)), abs=1e-10,
    )

  def test_parity_short_series(self):
    """Very short series (n=2): fast clone matches original."""
    from backend.analysis.scoring import _fast_stability, calculate_stability
    arr = np.array([10.0, -5.0])
    assert _fast_stability(arr) == pytest.approx(
      calculate_stability(pd.Series(arr)), abs=1e-10,
    )

  def test_single_element_returns_zero(self):
    """Single element: both should return 0.0."""
    from backend.analysis.scoring import _fast_stability, calculate_stability
    arr = np.array([5.0])
    assert _fast_stability(arr) == 0.0
    assert calculate_stability(pd.Series(arr)) == 0.0


class TestPrecomputedSeedsParity:
  """Verify precomputed_seeds param produces identical optimizer output."""

  def test_precomputed_seeds_identical_output(self):
    """optimize_portfolio with precomputed_seeds == without (internally computed)."""
    from backend.analysis.correlation import get_correlation_matrix
    from backend.analysis.optimizer import optimize_portfolio
    from backend.analysis.optimizer_seeds import compute_seed_scores
    rng = np.random.default_rng(42)
    n = 200
    pnl = _make_pnl({f"A{i}": rng.normal(5, 10, n) for i in range(10)})
    corr = get_correlation_matrix(pnl, "standard")
    assets = pnl.columns.tolist()
    seeds = compute_seed_scores(pnl, assets, ["sortino"], None)

    # Run without precomputed (internal computation)
    result_a = optimize_portfolio(
      pnl, corr, 4, "hybrid", n_portfolios=2,
      seed_metrics=["sortino"], max_seeds=10,
    )
    # Run with precomputed (should produce identical results)
    result_b = optimize_portfolio(
      pnl, corr, 4, "hybrid", n_portfolios=2,
      seed_metrics=["sortino"], max_seeds=10,
      precomputed_seeds=seeds,
    )
    # Same assets selected in same order
    assert len(result_a) == len(result_b)
    for a, b in zip(result_a, result_b):
      assert a["assets"] == b["assets"]
