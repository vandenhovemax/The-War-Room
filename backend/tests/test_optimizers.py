"""Comprehensive optimizer verification suite.

Tests all 3 optimizer paths (flat, split L/S, beam search), all 9 objectives,
all 9 seed metrics, trade-count constraints, and regression tests for 5 fixed bugs.

Run from the project root:
    python -m pytest backend/tests/test_optimizers.py -v

No database or backend server needed — uses synthetic data only.

Bug Reference:
    Bug #1 (CRITICAL): optimize_split_with_trade_constraints was gutted — missing allocation enumeration
    Bug #2 (CRITICAL): _greedy_search lost scores during dedup, no quality sorting
    Bug #3 (CRITICAL): Double cumsum in WFO asset selection stability calculation
    Bug #4 (MEDIUM):   Seed metrics not defaulting to ['sortino'] in optimize_portfolio
    Bug A  (MINOR):    PF=0 for all-win portfolios (gl=0) instead of 999.0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.analysis.beam_optimizer import (
    _score_combo,
    run_beam_optimization,
)
from backend.analysis.correlation import get_correlation_matrix
from backend.analysis.optimizer import (
    _greedy_search,
    optimize_correlation,
    optimize_portfolio,
)
from backend.analysis.optimizer_seeds import compute_seed_scores
from backend.analysis.optimizer_split import (
    _combined_score,
    _within_score,
    optimize_split_portfolio,
)
from backend.analysis.optimizer_tc import (
    _classify_assets,
    _enumerate_allocations,
    _score_low_trade_asset,
    optimize_split_with_trade_constraints,
    optimize_with_trade_constraints,
)
from backend.analysis.scoring import (
    SCORING_FUNCTIONS,
    calculate_stability,
    portfolio_calmar,
    portfolio_min_drawdown,
    portfolio_profit_factor,
    portfolio_recovery,
    portfolio_sortino,
    portfolio_ulcer,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FIXTURES — Synthetic data, no DB needed
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture
def daily_pnl_10():
    """10 assets, 200 days. ASSET1=most profitable, ASSET10=most losing."""
    np.random.seed(42)
    n_days = 200
    assets = [f"ASSET{i}" for i in range(1, 11)]
    data = {}
    for i, a in enumerate(assets):
        drift = 0.5 - (i * 0.1)
        data[a] = np.random.normal(drift, 5, n_days)
    return pd.DataFrame(
        data,
        index=pd.date_range("2024-01-01", periods=n_days, freq="D"),
    )


@pytest.fixture
def daily_pnl_split():
    """12 assets: 6 _Long + 6 _Short, 200 days."""
    np.random.seed(42)
    n_days = 200
    data = {}
    for i in range(1, 7):
        data[f"SYM{i}_Long"] = np.random.normal(0.3, 4, n_days)
        data[f"SYM{i}_Short"] = np.random.normal(0.2, 4, n_days)
    return pd.DataFrame(
        data,
        index=pd.date_range("2024-01-01", periods=n_days, freq="D"),
    )


@pytest.fixture
def daily_pnl_allwin():
    """5 assets where ALL daily PnL is positive. Tests PF edge case."""
    np.random.seed(99)
    n_days = 100
    data = {}
    for i in range(1, 6):
        data[f"WIN{i}"] = np.abs(np.random.normal(2, 1, n_days))
    return pd.DataFrame(
        data,
        index=pd.date_range("2024-01-01", periods=n_days, freq="D"),
    )


@pytest.fixture
def daily_pnl_split_large():
    """20 _Long + 20 _Short assets for TC constraint testing."""
    np.random.seed(123)
    n_days = 200
    data = {}
    for i in range(1, 21):
        data[f"SYM{i}_Long"] = np.random.normal(0.2, 3, n_days)
        data[f"SYM{i}_Short"] = np.random.normal(0.15, 3, n_days)
    return pd.DataFrame(
        data,
        index=pd.date_range("2024-01-01", periods=n_days, freq="D"),
    )


@pytest.fixture
def trade_counts_varied():
    """SYM1-5=low (5-10 trades), SYM6-15=mid (30-60), SYM16-20=high (100+)."""
    np.random.seed(77)
    counts = {}
    for i in range(1, 21):
        for side in ("_Long", "_Short"):
            if i <= 5:
                counts[f"SYM{i}{side}"] = np.random.randint(5, 11)
            elif i <= 15:
                counts[f"SYM{i}{side}"] = np.random.randint(30, 61)
            else:
                counts[f"SYM{i}{side}"] = np.random.randint(100, 200)
    return counts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 1: Scoring Functions (foundation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestScoringFunctions:
    """All scoring functions must return lower = better."""

    def test_sortino_profitable_beats_losing(self, daily_pnl_10):
        good = portfolio_sortino(daily_pnl_10["ASSET1"])
        bad = portfolio_sortino(daily_pnl_10["ASSET9"])
        assert good < bad, "Higher Sortino should give LOWER score"

    def test_calmar_profitable_beats_losing(self, daily_pnl_10):
        good = portfolio_calmar(daily_pnl_10["ASSET1"])
        bad = portfolio_calmar(daily_pnl_10["ASSET9"])
        assert good < bad

    def test_recovery_profitable_beats_losing(self, daily_pnl_10):
        good = portfolio_recovery(daily_pnl_10["ASSET1"])
        bad = portfolio_recovery(daily_pnl_10["ASSET9"])
        assert good < bad

    def test_profit_factor_profitable_beats_losing(self, daily_pnl_10):
        good = portfolio_profit_factor(daily_pnl_10["ASSET1"])
        bad = portfolio_profit_factor(daily_pnl_10["ASSET9"])
        assert good < bad

    def test_ulcer_smooth_beats_choppy(self):
        smooth = pd.Series([1, 1, 1, 1, 1, 1] * 30)
        choppy = pd.Series([5, -4, 5, -4, 5, -4] * 30)
        assert portfolio_ulcer(smooth) < portfolio_ulcer(choppy)

    def test_min_drawdown_small_beats_large(self):
        small_dd = pd.Series([1, 1, -0.5, 1, 1] * 40)
        large_dd = pd.Series([1, 1, -5, 1, 1] * 40)
        assert portfolio_min_drawdown(small_dd) < portfolio_min_drawdown(large_dd)

    def test_empty_series_does_not_crash(self):
        """Empty series should return a finite sentinel or inf, never crash."""
        empty = pd.Series([], dtype=float)
        for name, fn in SCORING_FUNCTIONS.items():
            val = fn(empty)
            assert isinstance(val, (int, float)), f"{name} should return numeric for empty, got {type(val)}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 2: calculate_stability (Bug #3 regression)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestStability:
    """Verify stability takes RAW PnL, not pre-cumsum'd equity."""

    def test_stability_range_0_to_1(self, daily_pnl_10):
        for col in daily_pnl_10.columns:
            s = calculate_stability(daily_pnl_10[col])
            assert 0 <= s <= 1, f"Stability out of range for {col}: {s}"

    def test_stability_straight_line_near_1(self):
        """Constant positive PnL = perfect equity line = stability near 1.0."""
        pnl = pd.Series([1.0] * 200)
        assert calculate_stability(pnl) > 0.9

    def test_stability_random_walk_lower(self):
        """Random noise with zero drift = low stability."""
        np.random.seed(42)
        pnl = pd.Series(np.random.normal(0, 5, 200))
        assert calculate_stability(pnl) < 0.5

    def test_no_double_cumsum_regression(self):
        """Bug #3: passing cumsum'd data (equity) vs raw PnL should give
        meaningfully different results — if they're the same, the bug is back."""
        np.random.seed(42)
        raw_pnl = pd.Series(np.random.normal(0.5, 3, 200))
        equity = raw_pnl.cumsum()
        stab_correct = calculate_stability(raw_pnl)
        stab_wrong = calculate_stability(equity)
        assert abs(stab_correct - stab_wrong) > 0.05, (
            f"Raw PnL stability ({stab_correct:.3f}) vs equity stability "
            f"({stab_wrong:.3f}) should differ — possible double-cumsum bug"
        )

    def test_stability_internally_cumsums(self):
        """Verify the function builds its own equity curve internally."""
        pnl = pd.Series([1, -1, 2, -1, 1, 0.5] * 30)
        s = calculate_stability(pnl)
        assert s > 0.2, "Trending-up PnL should have decent stability"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 3: Seed Scores (Bug #4 regression)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSeedScores:
    """Verify seed ranking across all 9 metrics."""

    ALL_METRICS = [
        "sortino", "pf", "pnl", "avg_dd", "max_dd",
        "ulcer", "wfe", "wr", "recovery",
    ]

    @pytest.mark.parametrize("metric", ALL_METRICS)
    def test_each_metric_individually(self, daily_pnl_10, metric):
        assets = daily_pnl_10.columns.tolist()
        scores = compute_seed_scores(daily_pnl_10, assets, [metric])
        assert len(scores) == 10
        assert all(isinstance(v, float) for v in scores.values())
        best = min(scores, key=scores.get)
        worst = max(scores, key=scores.get)
        assert scores[best] < scores[worst]

    def test_multi_metric_rank_average(self, daily_pnl_10):
        assets = daily_pnl_10.columns.tolist()
        scores = compute_seed_scores(daily_pnl_10, assets, ["sortino", "pf", "pnl"])
        assert len(scores) == 10
        vals = list(scores.values())
        assert min(vals) >= 0
        assert max(vals) <= 9

    def test_external_scores_injected(self, daily_pnl_10):
        assets = daily_pnl_10.columns.tolist()
        external = {"benchmark": {a: float(10 - i) for i, a in enumerate(assets)}}
        scores = compute_seed_scores(
            daily_pnl_10, assets, ["sortino", "benchmark"], external,
        )
        assert len(scores) == 10

    def test_empty_metrics_falls_back_to_sortino(self, daily_pnl_10):
        """Bug #4: even with no metrics, should still produce valid scores."""
        assets = daily_pnl_10.columns.tolist()
        scores = compute_seed_scores(daily_pnl_10, assets, [])
        assert len(scores) == 10


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 4: Flat Optimizer — optimize_portfolio (Bugs #2, #4, A)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestOptimizePortfolio:
    """Test all objectives through the master dispatcher."""

    CORR_OBJECTIVES = ["hybrid", "uncorrelated", "anti_correlated"]
    PNL_OBJECTIVES = [
        "max_sortino", "max_calmar", "max_recovery",
        "min_ulcer", "max_profit_factor", "min_max_drawdown",
    ]

    @pytest.mark.parametrize("objective", CORR_OBJECTIVES + PNL_OBJECTIVES)
    def test_each_objective_returns_results(self, daily_pnl_10, objective):
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        results = optimize_portfolio(
            daily_pnl_10, corr, n_assets=3, objective=objective,
            n_portfolios=3, seed_metrics=["sortino"], max_seeds=10,
        )
        assert len(results) >= 1
        assert all("assets" in r for r in results)
        assert all("metrics" in r for r in results)
        assert all(len(r["assets"]) == 3 for r in results)

    @pytest.mark.parametrize("objective", PNL_OBJECTIVES)
    def test_pnl_objectives_sorted_by_quality(self, daily_pnl_10, objective):
        """Bug #2 regression: results MUST be sorted (best first)."""
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        results = optimize_portfolio(
            daily_pnl_10, corr, n_assets=3, objective=objective,
            n_portfolios=5, seed_metrics=["sortino"], max_seeds=10,
        )
        if len(results) < 2:
            pytest.skip("Not enough unique portfolios")
        score_fn = SCORING_FUNCTIONS[objective]
        scores = [score_fn(daily_pnl_10[r["assets"]].sum(axis=1)) for r in results]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1] + 1e-6, (
                f"Not sorted at {i}: {scores[i]:.4f} > {scores[i+1]:.4f}"
            )

    def test_seed_metrics_none_defaults_to_sortino(self, daily_pnl_10):
        """Bug #4 regression: seed_metrics=None should still work."""
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        results = optimize_portfolio(
            daily_pnl_10, corr, n_assets=3, objective="hybrid",
            n_portfolios=3, seed_metrics=None,
        )
        assert len(results) >= 1

    def test_prioritize_stability_flag(self, daily_pnl_10):
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        r_normal = optimize_portfolio(
            daily_pnl_10, corr, 3, "hybrid", prioritize_stability=False,
        )
        r_stable = optimize_portfolio(
            daily_pnl_10, corr, 3, "hybrid", prioritize_stability=True,
        )
        assert len(r_normal) >= 1
        assert len(r_stable) >= 1

    def test_n_assets_equals_pool_returns_all_profitable(self, daily_pnl_10):
        """When n_assets == pool size, returns all *profitable* assets (PF >= 1.0 gate)."""
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        results = optimize_portfolio(daily_pnl_10, corr, n_assets=10, objective="hybrid")
        assert len(results) >= 1
        # Quality gate removes assets with PF < 1.0, so result may be < 10
        n_profitable = sum(1 for c in daily_pnl_10.columns
                           if daily_pnl_10[c][daily_pnl_10[c] > 0].sum()
                           >= abs(daily_pnl_10[c][daily_pnl_10[c] < 0].sum()))
        assert len(results[0]["assets"]) == n_profitable

    def test_metrics_keys_present(self, daily_pnl_10):
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        results = optimize_portfolio(daily_pnl_10, corr, 3, "hybrid")
        m = results[0]["metrics"]
        for key in ("net_profit", "profit_factor", "max_drawdown", "stability", "n_assets"):
            assert key in m, f"Missing metric key: {key}"

    def test_pf_allwin_not_zero(self, daily_pnl_allwin):
        """Bug A regression: all-win portfolio PF should be 999, not 0."""
        corr = get_correlation_matrix(daily_pnl_allwin, "standard")
        results = optimize_portfolio(daily_pnl_allwin, corr, 3, "hybrid")
        pf = results[0]["metrics"]["profit_factor"]
        assert pf > 0, f"All-win PF should not be 0, got {pf}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 5: _greedy_search (Bug #2 core regression)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGreedySearch:
    """Verify scores are stored, deduped, and sorted by quality."""

    def test_returns_list_of_asset_lists(self, daily_pnl_10):
        result = _greedy_search(
            daily_pnl_10, 3, SCORING_FUNCTIONS["max_sortino"],
            n_seeds=5, n_portfolios=3,
        )
        assert isinstance(result, list)
        assert all(isinstance(p, list) for p in result)
        assert all(len(p) == 3 for p in result)

    def test_no_duplicates(self, daily_pnl_10):
        result = _greedy_search(
            daily_pnl_10, 3, SCORING_FUNCTIONS["max_sortino"],
            n_seeds=10, n_portfolios=10,
        )
        keys = [tuple(sorted(p)) for p in result]
        assert len(keys) == len(set(keys)), "Duplicates found"

    def test_quality_ordering(self, daily_pnl_10):
        """Bug #2: first portfolio must have best (lowest) score."""
        score_fn = SCORING_FUNCTIONS["max_sortino"]
        result = _greedy_search(
            daily_pnl_10, 3, score_fn, n_seeds=10, n_portfolios=5,
        )
        if len(result) < 2:
            pytest.skip("Not enough unique portfolios")
        scores = [score_fn(daily_pnl_10[p].sum(axis=1)) for p in result]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1] + 1e-6, (
                f"Not quality-sorted at {i}: {scores[i]:.4f} > {scores[i+1]:.4f}"
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 6: Correlation-Based Optimizer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestOptimizeCorrelation:
    """Test all 3 correlation modes × 2 correlation methods."""

    @pytest.mark.parametrize("mode", ["uncorrelated", "anti_correlated", "hybrid"])
    def test_each_mode(self, daily_pnl_10, mode):
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        result = optimize_correlation(
            corr, 3, mode, n_portfolios=3, daily_pnl=daily_pnl_10,
        )
        assert len(result) >= 1
        assert all(len(p) == 3 for p in result)

    @pytest.mark.parametrize("mode", ["uncorrelated", "anti_correlated", "hybrid"])
    def test_no_duplicates(self, daily_pnl_10, mode):
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        result = optimize_correlation(corr, 3, mode, n_portfolios=5, daily_pnl=daily_pnl_10)
        keys = [tuple(sorted(p)) for p in result]
        assert len(keys) == len(set(keys))

    def test_with_seed_scores(self, daily_pnl_10):
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        seeds = compute_seed_scores(daily_pnl_10, daily_pnl_10.columns.tolist(), ["sortino"])
        result = optimize_correlation(
            corr, 3, "hybrid", seed_scores=seeds, daily_pnl=daily_pnl_10,
        )
        assert len(result) >= 1

    @pytest.mark.parametrize("method", ["standard", "trade_overlap"])
    def test_both_correlation_methods(self, daily_pnl_10, method):
        corr = get_correlation_matrix(daily_pnl_10, method)
        result = optimize_correlation(corr, 3, "hybrid", daily_pnl=daily_pnl_10)
        assert len(result) >= 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 7: Split L/S Optimizer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSplitOptimizer:
    """Test dual-objective split optimizer across all objective combos."""

    WITHIN = ["uncorrelated", "anti_correlated", "hybrid"]
    COMBINED = [
        "stability", "anti_correlated", "min_drawdown",
        "max_sortino", "max_calmar", "max_recovery",
        "min_ulcer", "max_profit_factor",
    ]

    @pytest.mark.parametrize("within", WITHIN)
    def test_each_within_objective(self, daily_pnl_split, within):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        results = optimize_split_portfolio(
            corr, n_longs=2, n_shorts=2,
            within_objective=within, combined_objective="stability",
            daily_returns=daily_pnl_split, n_portfolios=3,
        )
        assert len(results) >= 1
        for r in results:
            assert "longs" in r and "shorts" in r
            assert len(r["longs"]) == 2
            assert len(r["shorts"]) == 2

    @pytest.mark.parametrize("combined", COMBINED)
    def test_each_combined_objective(self, daily_pnl_split, combined):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        results = optimize_split_portfolio(
            corr, n_longs=2, n_shorts=2,
            within_objective="uncorrelated", combined_objective=combined,
            daily_returns=daily_pnl_split, n_portfolios=3,
        )
        assert len(results) >= 1

    def test_longs_have_long_suffix(self, daily_pnl_split):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        results = optimize_split_portfolio(corr, 3, 3, daily_returns=daily_pnl_split)
        for r in results:
            assert all(a.endswith("_Long") for a in r["longs"])
            assert all(a.endswith("_Short") for a in r["shorts"])

    def test_dedup_and_sorted_by_score(self, daily_pnl_split):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        results = optimize_split_portfolio(
            corr, 2, 2, daily_returns=daily_pnl_split, n_portfolios=5,
        )
        keys = [tuple(sorted(r["assets"])) for r in results]
        assert len(keys) == len(set(keys))
        scores = [r["score"] for r in results]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1] + 1e-6

    def test_seed_metrics_none_works(self, daily_pnl_split):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        results = optimize_split_portfolio(
            corr, 2, 2, daily_returns=daily_pnl_split, seed_metrics=None,
        )
        assert len(results) >= 1

    def test_metrics_present(self, daily_pnl_split):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        results = optimize_split_portfolio(corr, 2, 2, daily_returns=daily_pnl_split)
        assert "metrics" in results[0]
        assert "net_profit" in results[0]["metrics"]
        assert "stability" in results[0]["metrics"]

    def test_pf_allwin_split(self, daily_pnl_split):
        """Bug A: all-win PF should not be 0."""
        positive = daily_pnl_split.abs() + 0.01
        corr = get_correlation_matrix(positive, "standard")
        results = optimize_split_portfolio(corr, 2, 2, daily_returns=positive)
        if results:
            pf = results[0]["metrics"]["profit_factor"]
            assert pf > 0, f"All-win split PF={pf}, should not be 0"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 8: Split Scoring Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSplitScoringHelpers:
    """Verify _within_score and _combined_score."""

    @pytest.mark.parametrize("obj", ["uncorrelated", "anti_correlated", "hybrid"])
    def test_within_score_modes(self, daily_pnl_split, obj):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        score = _within_score(corr, "SYM1_Long", ["SYM2_Long", "SYM3_Long"], obj)
        assert isinstance(score, float)

    def test_within_score_empty_group_returns_zero(self, daily_pnl_split):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        assert _within_score(corr, "SYM1_Long", [], "uncorrelated") == 0.0

    @pytest.mark.parametrize("obj", [
        "stability", "anti_correlated", "min_drawdown",
        "max_sortino", "max_calmar",
    ])
    def test_combined_score_objectives(self, daily_pnl_split, obj):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        all_assets = daily_pnl_split.columns.tolist()
        long_assets = [a for a in all_assets if a.endswith("_Long")]
        score = _combined_score(daily_pnl_split, corr, all_assets[:4], long_assets, obj)
        assert isinstance(score, float)

    def test_combined_score_stability_range(self, daily_pnl_split):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        all_assets = daily_pnl_split.columns.tolist()
        long_assets = [a for a in all_assets if a.endswith("_Long")]
        score = _combined_score(daily_pnl_split, corr, all_assets[:4], long_assets, "stability")
        assert 0 <= score <= 1, f"Stability score {score} out of 0-1 range"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 9: Trade-Count Constraints — Combined
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTCCombined:
    """Pool-partitioned optimizer for flat (non-split) portfolios."""

    def test_classify_assets(self, trade_counts_varied):
        assets = [k for k in trade_counts_varied if k.endswith("_Long")]
        low, mid, high = _classify_assets(assets, trade_counts_varied, 15, 80)
        assert all(trade_counts_varied[a] <= 15 for a in low)
        assert all(trade_counts_varied[a] >= 80 for a in high)
        assert len(low) + len(mid) + len(high) == len(assets)

    def test_enumerate_allocations(self):
        allocs = _enumerate_allocations(5, max_low=2, min_high=1, n_pool_low=3, n_pool_high=5)
        assert len(allocs) > 0
        for n_low, n_high_min, n_flex in allocs:
            assert n_low + n_high_min + n_flex == 5
            assert n_low <= 2
            assert n_high_min >= 1

    def test_enumerate_no_constraints(self):
        allocs = _enumerate_allocations(5, max_low=999, min_high=0, n_pool_low=10, n_pool_high=10)
        assert len(allocs) > 0

    def test_tc_optimizer_returns_results(self, daily_pnl_10):
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        tc = {"low_threshold": 15, "max_low": 2, "high_threshold": 80, "min_high": 1}
        counts = {a: (i * 10 + 5) for i, a in enumerate(daily_pnl_10.columns)}
        results = optimize_with_trade_constraints(
            daily_pnl_10, corr, 4, counts, tc, "hybrid", 3,
            seed_metrics=["sortino"],
        )
        assert len(results) >= 1
        assert all(len(r["assets"]) == 4 for r in results)

    def test_tc_no_constraints_falls_through(self, daily_pnl_10):
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        tc = {"low_threshold": 0, "max_low": 999, "high_threshold": 0, "min_high": 0}
        counts = {a: 50 for a in daily_pnl_10.columns}
        results = optimize_with_trade_constraints(
            daily_pnl_10, corr, 3, counts, tc, "hybrid", 3,
        )
        assert len(results) >= 1

    @pytest.mark.parametrize("objective", [
        "hybrid", "uncorrelated", "max_sortino", "max_calmar", "min_ulcer",
    ])
    def test_tc_all_objectives(self, daily_pnl_10, objective):
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        tc = {"low_threshold": 15, "max_low": 1, "high_threshold": 80, "min_high": 1}
        counts = {a: (i * 12 + 5) for i, a in enumerate(daily_pnl_10.columns)}
        results = optimize_with_trade_constraints(
            daily_pnl_10, corr, 3, counts, tc, objective, 3,
            seed_metrics=["sortino"],
        )
        assert len(results) >= 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 10: Trade-Count Constraints — Split L/S (Bug #1 regression)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTCSplit:
    """Bug #1 regression: full allocation enumeration loop must work."""

    def test_split_tc_returns_results(self, daily_pnl_split_large, trade_counts_varied):
        corr = get_correlation_matrix(daily_pnl_split_large, "standard")
        tc = {"low_threshold": 15, "max_low": 2, "high_threshold": 80, "min_high": 1}
        results = optimize_split_with_trade_constraints(
            daily_pnl_split_large, corr,
            n_longs=3, n_shorts=3,
            trade_counts=trade_counts_varied,
            tc_longs=tc, tc_shorts=tc,
            within_objective="uncorrelated", combined_objective="stability",
            n_portfolios=3, seed_metrics=["sortino"],
        )
        assert len(results) >= 1
        for r in results:
            assert "longs" in r and "shorts" in r
            assert "assets" in r and "metrics" in r
            assert all(a.endswith("_Long") for a in r["longs"])
            assert all(a.endswith("_Short") for a in r["shorts"])

    def test_split_tc_no_constraints_fallback(self, daily_pnl_split):
        corr = get_correlation_matrix(daily_pnl_split, "standard")
        counts = {a: 50 for a in daily_pnl_split.columns}
        results = optimize_split_with_trade_constraints(
            daily_pnl_split, corr,
            n_longs=2, n_shorts=2,
            trade_counts=counts, tc_longs=None, tc_shorts=None,
        )
        assert len(results) >= 1

    def test_split_tc_dedup_and_sorted(self, daily_pnl_split_large, trade_counts_varied):
        corr = get_correlation_matrix(daily_pnl_split_large, "standard")
        tc = {"low_threshold": 15, "max_low": 2, "high_threshold": 80, "min_high": 0}
        results = optimize_split_with_trade_constraints(
            daily_pnl_split_large, corr,
            n_longs=3, n_shorts=3,
            trade_counts=trade_counts_varied,
            tc_longs=tc, tc_shorts=tc, n_portfolios=5,
        )
        if len(results) >= 2:
            keys = [tuple(sorted(r["assets"])) for r in results]
            assert len(keys) == len(set(keys)), "Duplicates found"
            scores = [r["score"] for r in results]
            for i in range(len(scores) - 1):
                assert scores[i] <= scores[i + 1] + 1e-6

    def test_split_tc_portfolio_sizes_correct(self, daily_pnl_split_large, trade_counts_varied):
        corr = get_correlation_matrix(daily_pnl_split_large, "standard")
        tc = {"low_threshold": 15, "max_low": 2, "high_threshold": 80, "min_high": 0}
        results = optimize_split_with_trade_constraints(
            daily_pnl_split_large, corr,
            n_longs=4, n_shorts=4,
            trade_counts=trade_counts_varied,
            tc_longs=tc, tc_shorts=tc, n_portfolios=3,
        )
        if results:
            assert len(results[0]["assets"]) == 8  # 4L + 4S

    @pytest.mark.parametrize("within,combined", [
        ("uncorrelated", "stability"),
        ("anti_correlated", "max_sortino"),
        ("hybrid", "min_drawdown"),
        ("uncorrelated", "max_calmar"),
    ])
    def test_split_tc_objective_combos(
        self, daily_pnl_split_large, trade_counts_varied, within, combined,
    ):
        corr = get_correlation_matrix(daily_pnl_split_large, "standard")
        tc = {"low_threshold": 15, "max_low": 1, "high_threshold": 80, "min_high": 0}
        results = optimize_split_with_trade_constraints(
            daily_pnl_split_large, corr,
            n_longs=3, n_shorts=3,
            trade_counts=trade_counts_varied,
            tc_longs=tc, tc_shorts=tc,
            within_objective=within, combined_objective=combined,
            n_portfolios=3,
        )
        assert len(results) >= 1

    def test_split_tc_one_side_no_constraints(self, daily_pnl_split_large, trade_counts_varied):
        """Constraints on longs only, shorts unconstrained."""
        corr = get_correlation_matrix(daily_pnl_split_large, "standard")
        tc_l = {"low_threshold": 15, "max_low": 1, "high_threshold": 80, "min_high": 1}
        results = optimize_split_with_trade_constraints(
            daily_pnl_split_large, corr,
            n_longs=3, n_shorts=3,
            trade_counts=trade_counts_varied,
            tc_longs=tc_l, tc_shorts=None,
            n_portfolios=3,
        )
        assert len(results) >= 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 11: Beam Search Optimizer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestBeamOptimizer:
    """Test 3-layer directional beam search."""

    @pytest.fixture
    def beam_data(self):
        """3 batches × 5 assets each, 200 days."""
        np.random.seed(42)
        n_days = 200
        data = {}
        batch_map = {}
        for bid in [1, 2, 3]:
            for i in range(5):
                name = f"B{bid}_A{i}_Long"
                data[name] = np.random.normal(0.2, 3, n_days)
                batch_map[name] = bid
        pnl = pd.DataFrame(
            data, index=pd.date_range("2024-01-01", periods=n_days, freq="D"),
        )
        return pnl, batch_map

    def test_beam_basic(self, beam_data):
        pnl, batch_map = beam_data
        results = run_beam_optimization(
            pnl, batch_map, l2_groups=[[1, 2], [3]],
            n_per_batch=3, obj_l1="hybrid", obj_l2="uncorrelated",
            obj_l3="max_sortino", k_l1=3, k_l2=3, k_l3=2,
            corr_method="standard", seed_metrics=["sortino"],
            max_seeds=10, prioritize_stability=False, external_scores=None,
        )
        assert len(results) >= 1
        assert all("assets" in r for r in results)
        assert all("metrics" in r for r in results)

    @pytest.mark.parametrize("obj_l3", [
        "hybrid", "uncorrelated", "max_sortino", "max_calmar", "min_ulcer",
    ])
    def test_beam_all_l3_objectives(self, beam_data, obj_l3):
        pnl, batch_map = beam_data
        results = run_beam_optimization(
            pnl, batch_map, l2_groups=[[1, 2]],
            n_per_batch=3, obj_l1="hybrid", obj_l2="uncorrelated",
            obj_l3=obj_l3, k_l1=3, k_l2=3, k_l3=2,
            corr_method="standard", seed_metrics=["sortino"],
            max_seeds=10, prioritize_stability=False, external_scores=None,
        )
        assert len(results) >= 1

    def test_beam_with_tc(self, beam_data):
        pnl, batch_map = beam_data
        np.random.seed(42)
        tc = {"low_threshold": 5, "max_low": 1, "high_threshold": 80, "min_high": 0}
        counts = {a: np.random.randint(10, 100) for a in pnl.columns}
        results = run_beam_optimization(
            pnl, batch_map, l2_groups=[[1, 2]],
            n_per_batch=3, obj_l1="hybrid", obj_l2="uncorrelated",
            obj_l3="max_sortino", k_l1=3, k_l2=3, k_l3=2,
            corr_method="standard", seed_metrics=["sortino"],
            max_seeds=10, prioritize_stability=False, external_scores=None,
            tc_config=tc, trade_counts=counts,
        )
        assert len(results) >= 1

    def test_beam_pf_allwin(self, beam_data):
        """Bug A: beam _build_results must handle all-win PF correctly."""
        pnl, batch_map = beam_data
        pnl_pos = pnl.abs() + 0.01
        results = run_beam_optimization(
            pnl_pos, batch_map, l2_groups=[[1, 2]],
            n_per_batch=3, obj_l1="hybrid", obj_l2="uncorrelated",
            obj_l3="max_sortino", k_l1=3, k_l2=3, k_l3=2,
            corr_method="standard", seed_metrics=["sortino"],
            max_seeds=10, prioritize_stability=False, external_scores=None,
        )
        if results:
            pf = results[0]["metrics"]["profit_factor"]
            assert pf > 0, f"Beam all-win PF={pf}, should not be 0"

    @pytest.mark.parametrize("obj", [
        "hybrid", "uncorrelated", "anti_correlated",
        "max_sortino", "max_calmar", "min_ulcer",
    ])
    def test_score_combo_all_objectives(self, daily_pnl_10, obj):
        assets = daily_pnl_10.columns.tolist()[:5]
        full_corr = get_correlation_matrix(daily_pnl_10, "standard")
        score = _score_combo(daily_pnl_10, assets, obj, full_corr)
        assert isinstance(score, float)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 12: Edge Cases
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEdgeCases:
    """Boundary conditions and degenerate inputs."""

    def test_single_asset(self, daily_pnl_10):
        corr = get_correlation_matrix(daily_pnl_10, "standard")
        results = optimize_portfolio(daily_pnl_10, corr, 1, "hybrid")
        assert len(results) >= 1
        assert len(results[0]["assets"]) == 1

    def test_two_assets_only(self):
        np.random.seed(42)
        pnl = pd.DataFrame({
            "A": np.random.normal(1, 3, 100),
            "B": np.random.normal(0.5, 3, 100),
        }, index=pd.date_range("2024-01-01", periods=100, freq="D"))
        corr = get_correlation_matrix(pnl, "standard")
        results = optimize_portfolio(pnl, corr, 2, "hybrid")
        assert len(results) == 1
        assert sorted(results[0]["assets"]) == ["A", "B"]

    def test_split_minimum_viable(self):
        """1 long + 1 short = minimum split portfolio."""
        np.random.seed(42)
        pnl = pd.DataFrame({
            "A_Long": np.random.normal(0.5, 3, 100),
            "B_Short": np.random.normal(0.3, 3, 100),
        }, index=pd.date_range("2024-01-01", periods=100, freq="D"))
        corr = get_correlation_matrix(pnl, "standard")
        results = optimize_split_portfolio(corr, 1, 1, daily_returns=pnl)
        assert len(results) >= 1

    def test_split_not_enough_shorts_returns_empty(self):
        """Request 3 shorts but only 1 available."""
        np.random.seed(42)
        pnl = pd.DataFrame({
            "A_Long": np.random.normal(0.5, 3, 100),
            "B_Long": np.random.normal(0.3, 3, 100),
            "C_Long": np.random.normal(0.2, 3, 100),
            "D_Short": np.random.normal(0.1, 3, 100),
        }, index=pd.date_range("2024-01-01", periods=100, freq="D"))
        corr = get_correlation_matrix(pnl, "standard")
        results = optimize_split_portfolio(corr, 2, 3, daily_returns=pnl)
        assert results == []

    def test_all_identical_pnl(self):
        """All assets identical — should not crash."""
        pnl = pd.DataFrame(
            {f"A{i}": [1, -1, 2, -1] * 50 for i in range(5)},
            index=pd.date_range("2024-01-01", periods=200, freq="D"),
        )
        corr = get_correlation_matrix(pnl, "standard")
        results = optimize_portfolio(pnl, corr, 3, "hybrid")
        assert len(results) >= 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GROUP 13: WFO Window Generation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestWFOWindows:
    """Test WFO window generation and date logic."""

    def test_generate_windows(self):
        from backend.analysis.wfo_asset_selection import generate_wfo_windows
        windows = generate_wfo_windows(
            is_months=12, oos_months=3,
            last_oos_end="2025-12-31", data_start="2024-01-01",
        )
        assert len(windows) >= 1
        for w in windows:
            assert w["is_start"] is not None
            assert w["is_end"] is not None
        # Last window should have no OOS (it's the current/final IS)
        assert windows[-1]["oos_start"] is None

    def test_window_dates_non_overlapping(self):
        from backend.analysis.wfo_asset_selection import generate_wfo_windows
        windows = generate_wfo_windows(
            is_months=6, oos_months=3,
            last_oos_end="2025-12-31", data_start="2024-01-01",
        )
        # OOS periods should not overlap with each other
        oos_windows = [w for w in windows if w["oos_start"]]
        for i in range(len(oos_windows) - 1):
            assert oos_windows[i]["oos_end"] < oos_windows[i + 1]["oos_start"], (
                f"OOS window {i} overlaps with {i+1}"
            )
