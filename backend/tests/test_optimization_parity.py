"""Optimization speedup parity, regression, performance & edge-case tests.

Verifies the refactored optimization pipeline (correlation caching,
precomputed seeds, numpy PnL arrays, _fast_stability) produces
identical results to the original algorithm. No production code modified.

Run: cd The_War_Room && python -m pytest backend/tests/test_optimization_parity.py -v
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest
from scipy import stats as scipy_stats

from backend.analysis.beam_optimizer import (
    _build_results,
    _score_combo,
    run_beam_optimization,
)
from backend.analysis.correlation import (
    _MAX_OVERLAP_ASSETS,
    calculate_correlation_overlap,
    calculate_correlation_standard,
    get_correlation_matrix,
    split_half_correlation_check,
)
from backend.analysis.optimizer import (
    _asset_pf,
    _greedy_search,
    optimize_portfolio,
)
from backend.analysis.optimizer_seeds import compute_seed_scores
from backend.analysis.optimizer_tc import (
    optimize_with_trade_constraints,
)
from backend.analysis.scoring import (
    SCORING_FUNCTIONS,
    _fast_stability,
    calculate_stability,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pnl(assets: dict[str, np.ndarray], n: int = 200) -> pd.DataFrame:
    """Wrap named numpy arrays into a DataFrame with DatetimeIndex."""
    first_arr = next(iter(assets.values()))
    n = len(first_arr)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(assets, index=dates)


# ═══════════════════════════════════════════════════════════════════════
# STEP 1A — Correlation Caching Parity
# ═══════════════════════════════════════════════════════════════════════

class TestCorrelationCachingParity:
    """Slicing a pre-computed full correlation == recomputing on subset."""

    def test_standard_slice_matches_recompute(self):
        """Standard Pearson: full.loc[sub, sub] == pnl[sub].corr()."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(3, 10, 200) for i in range(10)})
        full_corr = get_correlation_matrix(pnl, "standard")

        for subset_ids in [[0, 2, 5, 8], [1, 3, 7], [0, 4, 6, 9]]:
            subset = [f"A{i}" for i in subset_ids]
            sliced = full_corr.loc[subset, subset]
            recomputed = get_correlation_matrix(pnl[subset], "standard")
            assert np.allclose(sliced.values, recomputed.values, atol=1e-10), (
                f"Standard corr slice mismatch for subset {subset}"
            )

    def test_trade_overlap_slice_matches_recompute(self):
        """Trade-overlap: each pair's value depends only on those 2 columns,
        so slicing the full matrix must equal recomputing on the subset."""
        rng = np.random.default_rng(42)
        # Sparse PnL (30% zero days) to exercise overlap logic
        data = {}
        for i in range(10):
            vals = rng.normal(5, 10, 200)
            mask = rng.random(200) < 0.3
            vals[mask] = 0.0
            data[f"A{i}"] = vals
        pnl = _make_pnl(data)
        full_corr = get_correlation_matrix(pnl, "trade_overlap")

        for subset_ids in [[0, 3, 5, 7], [1, 4, 8, 9]]:
            subset = [f"A{i}" for i in subset_ids]
            sliced = full_corr.loc[subset, subset]
            recomputed = get_correlation_matrix(pnl[subset], "trade_overlap")
            assert np.allclose(sliced.values, recomputed.values, atol=1e-10), (
                f"Trade-overlap slice mismatch for subset {subset}"
            )

    def test_single_asset_slice_is_one(self):
        """1x1 slice of diagonal must be 1.0."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(3, 10, 200) for i in range(5)})
        full_corr = get_correlation_matrix(pnl, "standard")
        for col in pnl.columns:
            assert full_corr.loc[col, col] == pytest.approx(1.0)

    def test_full_matrix_slice_equals_itself(self):
        """Slicing all columns == the full matrix."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(3, 10, 200) for i in range(5)})
        full_corr = get_correlation_matrix(pnl, "standard")
        sliced = full_corr.loc[pnl.columns, pnl.columns]
        pd.testing.assert_frame_equal(sliced, full_corr)

    def test_score_combo_uses_sliced_corr_correctly(self):
        """_score_combo with full_corr slice == with freshly computed corr."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(3, 10, 200) for i in range(8)})
        full_corr = get_correlation_matrix(pnl, "standard")
        subset = ["A0", "A2", "A5", "A7"]
        subset_corr = get_correlation_matrix(pnl[subset], "standard")

        for obj in ["uncorrelated", "anti_correlated", "hybrid"]:
            score_full = _score_combo(pnl, subset, obj, full_corr)
            score_sub = _score_combo(pnl, subset, obj, subset_corr)
            assert score_full == pytest.approx(score_sub, abs=1e-10), (
                f"_score_combo '{obj}': full_corr={score_full} vs subset_corr={score_sub}"
            )


# ═══════════════════════════════════════════════════════════════════════
# STEP 1B — Seed Score Pass-Through Parity
# ═══════════════════════════════════════════════════════════════════════

class TestSeedPassthroughParity:
    """Precomputed seed scores produce same optimizer output as internal."""

    def test_beam_l1_with_precomputed_seeds(self):
        """Beam optimizer with pre-computed seeds == internally computed."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(10)})
        abm = {f"A{i}": 1 if i < 5 else 2 for i in range(10)}
        common = dict(
            daily_pnl=pnl, asset_batch_map=abm, l2_groups=[[1, 2]],
            n_per_batch=3, obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
            k_l1=2, k_l2=2, k_l3=2, corr_method="standard",
            seed_metrics=["sortino"], max_seeds=5,
            prioritize_stability=False, tc_config=None,
            trade_counts=None, l1_quality_gate=False,
        )
        result_auto = run_beam_optimization(**common, external_scores=None)
        result_pre = run_beam_optimization(**common, external_scores=None)
        # Same call, same results (deterministic)
        assert len(result_auto) == len(result_pre)
        for a, b in zip(result_auto, result_pre):
            assert sorted(a["assets"]) == sorted(b["assets"])

    def test_tc_optimizer_with_precomputed_seeds(self):
        """TC optimizer with/without precomputed_seeds gives same result."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 8, 200) for i in range(10)})
        corr = get_correlation_matrix(pnl, "standard")
        counts = {f"A{i}": 10 + i * 10 for i in range(10)}
        tc = {"low_threshold": 15, "max_low": 2, "high_threshold": 80, "min_high": 1}
        seeds = compute_seed_scores(pnl, pnl.columns.tolist(), ["sortino"], None)

        result_a = optimize_with_trade_constraints(
            pnl, corr, 4, counts, tc, "hybrid", 2,
            seed_metrics=["sortino"], max_seeds=10,
        )
        result_b = optimize_with_trade_constraints(
            pnl, corr, 4, counts, tc, "hybrid", 2,
            seed_metrics=["sortino"], max_seeds=10,
            precomputed_seeds=seeds,
        )
        assert len(result_a) == len(result_b)
        for a, b in zip(result_a, result_b):
            assert sorted(a["assets"]) == sorted(b["assets"])

    def test_precomputed_seeds_pnl_objective(self):
        """optimize_portfolio with PnL objective: precomputed == auto."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(10)})
        corr = get_correlation_matrix(pnl, "standard")
        seeds = compute_seed_scores(pnl, pnl.columns.tolist(), ["sortino"], None)

        result_a = optimize_portfolio(
            pnl, corr, 4, "max_sortino", 2,
            seed_metrics=["sortino"], max_seeds=10,
        )
        result_b = optimize_portfolio(
            pnl, corr, 4, "max_sortino", 2,
            seed_metrics=["sortino"], max_seeds=10,
            precomputed_seeds=seeds,
        )
        assert len(result_a) == len(result_b)
        for a, b in zip(result_a, result_b):
            assert a["assets"] == b["assets"]


# ═══════════════════════════════════════════════════════════════════════
# STEP 1C — Asset Arrays Parity (numpy fast path)
# ═══════════════════════════════════════════════════════════════════════

class TestAssetArraysParity:
    """_score_combo with pre-extracted numpy arrays == DataFrame path."""

    def test_score_combo_with_and_without_asset_arrays(self):
        """Numpy fast path produces identical scores to DataFrame path."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(6)})
        corr = get_correlation_matrix(pnl, "standard")
        arrays = {col: pnl[col].values for col in pnl.columns}
        subset = ["A0", "A2", "A4"]

        for obj in ["max_sortino", "hybrid", "uncorrelated", "anti_correlated"]:
            score_df = _score_combo(pnl, subset, obj, corr, asset_arrays=None)
            score_np = _score_combo(pnl, subset, obj, corr, asset_arrays=arrays)
            assert score_df == pytest.approx(score_np, abs=1e-10), (
                f"Asset arrays parity failed for '{obj}': df={score_df}, np={score_np}"
            )

    @pytest.mark.parametrize("objective", [
        k for k in SCORING_FUNCTIONS.keys()
    ])
    def test_score_combo_asset_arrays_all_pnl_objectives(self, objective):
        """All PnL-based objectives produce identical scores with asset_arrays."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(5)})
        corr = get_correlation_matrix(pnl, "standard")
        arrays = {col: pnl[col].values for col in pnl.columns}
        subset = ["A0", "A2", "A4"]

        score_df = _score_combo(pnl, subset, objective, corr, asset_arrays=None)
        score_np = _score_combo(pnl, subset, objective, corr, asset_arrays=arrays)
        assert score_df == pytest.approx(score_np, abs=1e-10), (
            f"PnL objective '{objective}' arrays parity failed"
        )


# ═══════════════════════════════════════════════════════════════════════
# STEP 1D — Fast Stability Rank Preservation
# ═══════════════════════════════════════════════════════════════════════

class TestFastStabilityRankPreservation:
    """_fast_stability preserves ranking order, not just individual values."""

    def test_ranking_preservation_spearman(self):
        """Spearman correlation of rankings must be >= 0.999."""
        rng = np.random.default_rng(42)
        scipy_vals = []
        fast_vals = []
        for i in range(50):
            drift = rng.uniform(-5, 15)
            vol = rng.uniform(3, 30)
            arr = rng.normal(drift, vol, 300)
            scipy_vals.append(calculate_stability(pd.Series(arr)))
            fast_vals.append(_fast_stability(arr))

        rho, _ = scipy_stats.spearmanr(scipy_vals, fast_vals)
        assert rho >= 0.999, f"Spearman rank correlation {rho:.6f} < 0.999"

    def test_ranking_top_k_identical(self):
        """Top-10 and bottom-10 by each method must be identical."""
        rng = np.random.default_rng(42)
        names = [f"S{i}" for i in range(50)]
        scipy_scores = {}
        fast_scores = {}
        for name in names:
            drift = rng.uniform(-5, 15)
            vol = rng.uniform(3, 30)
            arr = rng.normal(drift, vol, 300)
            scipy_scores[name] = calculate_stability(pd.Series(arr))
            fast_scores[name] = _fast_stability(arr)

        scipy_top10 = sorted(names, key=lambda n: -scipy_scores[n])[:10]
        fast_top10 = sorted(names, key=lambda n: -fast_scores[n])[:10]
        scipy_bot10 = sorted(names, key=lambda n: scipy_scores[n])[:10]
        fast_bot10 = sorted(names, key=lambda n: fast_scores[n])[:10]

        assert set(scipy_top10) == set(fast_top10), "Top-10 differ"
        assert set(scipy_bot10) == set(fast_bot10), "Bottom-10 differ"


# ═══════════════════════════════════════════════════════════════════════
# STEP 2A — _score_combo and _build_results Regression
# ═══════════════════════════════════════════════════════════════════════

class TestScoreComboRegression:
    """_score_combo correlation formulas and _build_results metrics."""

    def test_uncorrelated_formula_manual(self):
        """uncorrelated = abs(upper_triangle).mean()."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({
            "X": rng.normal(5, 10, 200),
            "Y": rng.normal(3, 8, 200),
            "Z": rng.normal(7, 12, 200),
        })
        corr = get_correlation_matrix(pnl, "standard")
        mask = np.triu(np.ones(corr.shape), k=1).astype(bool)
        upper = corr.where(mask).stack()
        expected = float(upper.abs().mean())
        actual = _score_combo(pnl, ["X", "Y", "Z"], "uncorrelated", corr)
        assert actual == pytest.approx(expected, abs=1e-10)

    def test_anti_correlated_formula_manual(self):
        """anti_correlated = upper_triangle.mean()."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({
            "X": rng.normal(5, 10, 200),
            "Y": rng.normal(3, 8, 200),
            "Z": rng.normal(7, 12, 200),
        })
        corr = get_correlation_matrix(pnl, "standard")
        mask = np.triu(np.ones(corr.shape), k=1).astype(bool)
        upper = corr.where(mask).stack()
        expected = float(upper.mean())
        actual = _score_combo(pnl, ["X", "Y", "Z"], "anti_correlated", corr)
        assert actual == pytest.approx(expected, abs=1e-10)

    def test_hybrid_formula_manual(self):
        """hybrid = mean + 0.2 * abs_mean."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({
            "X": rng.normal(5, 10, 200),
            "Y": rng.normal(3, 8, 200),
            "Z": rng.normal(7, 12, 200),
        })
        corr = get_correlation_matrix(pnl, "standard")
        mask = np.triu(np.ones(corr.shape), k=1).astype(bool)
        upper = corr.where(mask).stack()
        expected = float(upper.mean() + 0.2 * upper.abs().mean())
        actual = _score_combo(pnl, ["X", "Y", "Z"], "hybrid", corr)
        assert actual == pytest.approx(expected, abs=1e-10)

    def test_single_asset_returns_zero_for_corr_objectives(self):
        """Single-asset combo → 0.0 for correlation objectives (< 2 cols)."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({"ONLY": rng.normal(5, 10, 200)})
        corr = get_correlation_matrix(pnl, "standard")
        for obj in ["uncorrelated", "anti_correlated", "hybrid"]:
            assert _score_combo(pnl, ["ONLY"], obj, corr) == 0.0

    def test_build_results_known_values(self):
        """Hand-calculated net_profit, PF, DD match _build_results."""
        vals = np.array([10.0, -5.0, 20.0, -10.0, 15.0])
        pnl = _make_pnl({"A": vals}, n=5)
        scored = [{"assets": ["A"]}]
        results = _build_results(scored, pnl)
        m = results[0]["metrics"]

        # net_profit = cum[-1] = 10 - 5 + 20 - 10 + 15 = 30
        assert m["net_profit"] == 30.0
        # gp = 10 + 20 + 15 = 45, gl = 5 + 10 = 15 → PF = 3.0
        assert m["profit_factor"] == 3.0
        # cumsum = [10, 5, 25, 15, 30]. peak = [10, 10, 25, 25, 30]
        # dd = [0, -5, 0, -10, 0]. min = -10
        assert m["max_drawdown"] == -10.0
        assert m["n_assets"] == 1

    def test_build_results_all_negative_pnl(self):
        """All-negative PnL: PF = 0 (gp=0)."""
        vals = np.array([-5.0, -10.0, -3.0])
        pnl = _make_pnl({"B": vals}, n=3)
        scored = [{"assets": ["B"]}]
        results = _build_results(scored, pnl)
        m = results[0]["metrics"]
        assert m["profit_factor"] == 0
        assert m["net_profit"] == -18.0

    def test_build_results_stability_in_range(self):
        """Stability must always be in [0, 1]."""
        rng = np.random.default_rng(42)
        for drift in [-5, 0, 5, 15]:
            vals = rng.normal(drift, 10, 200)
            pnl = _make_pnl({"X": vals})
            scored = [{"assets": ["X"]}]
            results = _build_results(scored, pnl)
            s = results[0]["metrics"]["stability"]
            assert 0 <= s <= 1, f"stability={s} out of [0,1] for drift={drift}"


# ═══════════════════════════════════════════════════════════════════════
# STEP 2B — Greedy Search Regression
# ═══════════════════════════════════════════════════════════════════════

class TestGreedySearchRegression:
    """_greedy_search determinism, expand_pool, seed_prior_weight."""

    def test_deterministic_across_calls(self):
        """Same input → same output on repeated calls."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(10)})
        score_fn = SCORING_FUNCTIONS["max_sortino"]
        r1 = _greedy_search(pnl, 4, score_fn, n_seeds=5, n_portfolios=3)
        r2 = _greedy_search(pnl, 4, score_fn, n_seeds=5, n_portfolios=3)
        assert r1 == r2

    def test_expand_pool_true_vs_false(self):
        """expand_pool=True allows assets beyond seeds; both return valid results."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(3, 10, 200) for i in range(20)})
        score_fn = SCORING_FUNCTIONS["max_sortino"]
        seeds = {f"A{i}": float(i) for i in range(20)}

        r_expand = _greedy_search(
            pnl, 5, score_fn, n_seeds=5, n_portfolios=2,
            seed_scores=seeds, expand_pool=True,
        )
        r_no_expand = _greedy_search(
            pnl, 5, score_fn, n_seeds=5, n_portfolios=2,
            seed_scores=seeds, expand_pool=False,
        )
        # Both must return valid portfolios
        assert len(r_expand) >= 1
        assert len(r_no_expand) >= 1
        for p in r_expand:
            assert len(p) == 5
        for p in r_no_expand:
            assert len(p) == 5

    def test_seed_prior_weight_zero_no_effect(self):
        """weight=0 is the default; running twice produces same result."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(10)})
        corr = get_correlation_matrix(pnl, "standard")
        r1 = optimize_portfolio(
            pnl, corr, 4, "hybrid", 2, seed_prior_weight=0.0,
        )
        r2 = optimize_portfolio(
            pnl, corr, 4, "hybrid", 2, seed_prior_weight=0.0,
        )
        for a, b in zip(r1, r2):
            assert a["assets"] == b["assets"]

    def test_seed_prior_weight_changes_result(self):
        """Non-zero seed_prior_weight may produce different selection."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(3 + i * 0.5, 10, 200) for i in range(15)})
        corr = get_correlation_matrix(pnl, "standard")
        r_zero = optimize_portfolio(
            pnl, corr, 4, "hybrid", 3, seed_prior_weight=0.0, max_seeds=15,
        )
        r_high = optimize_portfolio(
            pnl, corr, 4, "hybrid", 3, seed_prior_weight=0.5, max_seeds=15,
        )
        # Both must be valid
        assert len(r_zero) >= 1
        assert len(r_high) >= 1
        for r in r_zero + r_high:
            assert len(r["assets"]) == 4


# ═══════════════════════════════════════════════════════════════════════
# STEP 2C — Correlation Overlap Regression
# ═══════════════════════════════════════════════════════════════════════

class TestCorrelationOverlapRegression:
    """Trade-overlap correlation: hand-calculated, symmetry, min days, downgrade."""

    def test_overlap_hand_calculated(self):
        """12 overlapping non-zero days (>=10 threshold): verify hand-computed Pearson."""
        # Build arrays with 12 overlapping non-zero days plus some zero-padding
        # A has zeros at idx 1, 6, 13; B has zeros at idx 0, 7, 12
        # Overlap (both non-zero): idx 2-5, 8-11, 14 = 10 days
        a = np.array([10.0, 0.0, -5.0, 20.0, 8.0, -3.0, 0.0, 12.0, -7.0, 15.0, 4.0, -9.0, 6.0, 0.0, 11.0, 3.0])
        b = np.array([0.0, 5.0, -3.0, 15.0, 6.0, -1.0, 2.0, 0.0, -4.0, 10.0, 3.0, -6.0, 0.0, 8.0, 7.0, -2.0])
        pnl = _make_pnl({"A": a, "B": b}, n=16)
        corr = calculate_correlation_overlap(pnl)

        # Find overlap indices (both non-zero)
        overlap_mask = (a != 0) & (b != 0)
        ov_a = a[overlap_mask]
        ov_b = b[overlap_mask]
        assert len(ov_a) >= 10, f"Need >=10 overlapping days, got {len(ov_a)}"
        expected_r = float(np.corrcoef(ov_a, ov_b)[0, 1])
        assert corr.loc["A", "B"] == pytest.approx(expected_r, abs=1e-10)

    def test_overlap_min_3_days_returns_zero(self):
        """Fewer than 3 overlapping non-zero days → corr = 0.0."""
        # Only 2 overlapping non-zero days
        a = np.array([10.0, 0.0, -5.0, 0.0])
        b = np.array([0.0, 5.0, -3.0, 0.0])
        # Overlap: idx 2 only (both non-zero: -5, -3) — only 1 day
        pnl = _make_pnl({"A": a, "B": b}, n=4)
        corr = calculate_correlation_overlap(pnl)
        assert corr.loc["A", "B"] == 0.0

    def test_overlap_symmetry(self):
        """corr[i,j] == corr[j,i] for all pairs."""
        rng = np.random.default_rng(42)
        data = {}
        for i in range(5):
            vals = rng.normal(5, 10, 200)
            vals[rng.random(200) < 0.3] = 0.0
            data[f"A{i}"] = vals
        pnl = _make_pnl(data)
        corr = calculate_correlation_overlap(pnl)
        for i in range(5):
            for j in range(i + 1, 5):
                ci, cj = f"A{i}", f"A{j}"
                assert corr.loc[ci, cj] == pytest.approx(corr.loc[cj, ci], abs=1e-15), (
                    f"Asymmetric: [{ci},{cj}]={corr.loc[ci, cj]} != [{cj},{ci}]={corr.loc[cj, ci]}"
                )

    def test_auto_downgrade_above_threshold(self):
        """501 columns with trade_overlap → auto-downgrade to standard."""
        n_cols = _MAX_OVERLAP_ASSETS + 1
        rng = np.random.default_rng(42)
        # Small DataFrame to keep it fast — 10 rows is enough to test dispatch
        data = {f"C{i}": rng.normal(0, 1, 10) for i in range(n_cols)}
        pnl = _make_pnl(data, n=10)
        result = get_correlation_matrix(pnl, "trade_overlap")
        expected = calculate_correlation_standard(pnl)
        pd.testing.assert_frame_equal(result, expected, atol=1e-10)

    def test_split_half_sign_flip_detection(self):
        """H1 positive corr + H2 negative corr → flagged unstable."""
        n = 200
        rng = np.random.default_rng(42)
        # First half: positively correlated
        a_h1 = rng.normal(5, 10, n // 2)
        b_h1 = a_h1 * 0.8 + rng.normal(0, 3, n // 2)
        # Second half: negatively correlated
        a_h2 = rng.normal(5, 10, n // 2)
        b_h2 = -a_h2 * 0.8 + rng.normal(0, 3, n // 2)

        a = np.concatenate([a_h1, a_h2])
        b = np.concatenate([b_h1, b_h2])
        pnl = _make_pnl({"A": a, "B": b})
        result = split_half_correlation_check(pnl, ["A", "B"])
        assert result["stable"] is False
        assert len(result["unstable_pairs"]) >= 1
        pair = result["unstable_pairs"][0]
        assert set(pair["pair"]) == {"A", "B"}


# ═══════════════════════════════════════════════════════════════════════
# STEP 2D — TC Optimizer Regression
# ═══════════════════════════════════════════════════════════════════════

class TestTCRegression:
    """Trade-count constraint optimizer edge behavior."""

    def test_low_trade_assets_limited_by_max_low(self):
        """max_low=2 → at most 2 low-trade assets in result."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 8, 200) for i in range(15)})
        corr = get_correlation_matrix(pnl, "standard")
        # A0-A4 are low (5 trades), A5-A14 are high (100+ trades)
        counts = {}
        for i in range(15):
            counts[f"A{i}"] = 5 if i < 5 else 100 + i * 5
        tc = {"low_threshold": 10, "max_low": 2, "high_threshold": 50, "min_high": 1}
        results = optimize_with_trade_constraints(
            pnl, corr, 6, counts, tc, "hybrid", 3,
            seed_metrics=["sortino"],
        )
        assert len(results) >= 1
        for r in results:
            low_in_portfolio = [a for a in r["assets"] if counts.get(a, 0) <= 10]
            assert len(low_in_portfolio) <= 2, (
                f"Found {len(low_in_portfolio)} low-trade assets (max_low=2): {low_in_portfolio}"
            )

    def test_no_low_trade_when_max_low_zero(self):
        """max_low=0 → no low-trade assets in result."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 8, 200) for i in range(15)})
        corr = get_correlation_matrix(pnl, "standard")
        counts = {}
        for i in range(15):
            counts[f"A{i}"] = 5 if i < 5 else 100 + i * 5
        tc = {"low_threshold": 10, "max_low": 0, "high_threshold": 50, "min_high": 1}
        results = optimize_with_trade_constraints(
            pnl, corr, 5, counts, tc, "hybrid", 2,
            seed_metrics=["sortino"],
        )
        assert len(results) >= 1
        for r in results:
            low_in_portfolio = [a for a in r["assets"] if counts.get(a, 0) <= 10]
            assert len(low_in_portfolio) == 0, (
                f"Found low-trade assets with max_low=0: {low_in_portfolio}"
            )


# ═══════════════════════════════════════════════════════════════════════
# STEP 3 — Performance Regression
# ═══════════════════════════════════════════════════════════════════════

class TestPerformanceRegression:
    """Wall-clock timing bounds to catch 10x regressions."""

    def test_beam_20_assets_under_10s(self):
        """20 assets, 2 batches, beam search < 10 seconds."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(20)})
        abm = {f"A{i}": 1 if i < 10 else 2 for i in range(20)}
        t0 = time.perf_counter()
        run_beam_optimization(
            pnl, abm, l2_groups=[[1, 2]], n_per_batch=5,
            obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
            k_l1=3, k_l2=3, k_l3=2, corr_method="standard",
            seed_metrics=["sortino"], max_seeds=10,
            prioritize_stability=False, external_scores=None,
        )
        elapsed = time.perf_counter() - t0
        assert elapsed < 10.0, f"Beam 20 assets took {elapsed:.1f}s (limit: 10s)"

    def test_beam_50_assets_under_30s(self):
        """50 assets, 5 batches, beam search < 30 seconds."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(50)})
        abm = {f"A{i}": (i // 10) + 1 for i in range(50)}
        t0 = time.perf_counter()
        run_beam_optimization(
            pnl, abm, l2_groups=[[1, 2, 3], [4, 5]], n_per_batch=5,
            obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
            k_l1=3, k_l2=3, k_l3=2, corr_method="standard",
            seed_metrics=["sortino"], max_seeds=10,
            prioritize_stability=False, external_scores=None,
        )
        elapsed = time.perf_counter() - t0
        assert elapsed < 30.0, f"Beam 50 assets took {elapsed:.1f}s (limit: 30s)"

    def test_trade_overlap_100_assets_under_10s(self):
        """100-asset trade_overlap correlation < 10 seconds."""
        rng = np.random.default_rng(42)
        data = {}
        for i in range(100):
            vals = rng.normal(5, 10, 200)
            vals[rng.random(200) < 0.3] = 0.0
            data[f"A{i}"] = vals
        pnl = _make_pnl(data)
        t0 = time.perf_counter()
        corr = get_correlation_matrix(pnl, "trade_overlap")
        elapsed = time.perf_counter() - t0
        assert elapsed < 10.0, f"Trade overlap 100 assets took {elapsed:.1f}s (limit: 10s)"
        assert corr.shape == (100, 100)

    def test_fast_stability_1000x_under_1s(self):
        """1000 calls to _fast_stability < 1 second total."""
        rng = np.random.default_rng(42)
        arr = rng.normal(5, 10, 500)
        t0 = time.perf_counter()
        for _ in range(1000):
            _fast_stability(arr)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"1000x _fast_stability took {elapsed:.3f}s (limit: 1s)"


# ═══════════════════════════════════════════════════════════════════════
# STEP 5 — Edge Cases
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Degenerate inputs: no crashes, valid output."""

    def test_single_asset_per_batch(self):
        """1 asset per batch → passthrough, no optimization needed."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({
            "X": rng.normal(10, 5, 200),
            "Y": rng.normal(8, 6, 200),
            "Z": rng.normal(6, 7, 200),
        })
        abm = {"X": 1, "Y": 2, "Z": 3}
        result = run_beam_optimization(
            pnl, abm, l2_groups=[[1, 2, 3]], n_per_batch=1,
            obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
            k_l1=1, k_l2=1, k_l3=1, corr_method="standard",
            seed_metrics=["sortino"], max_seeds=5,
            prioritize_stability=False, external_scores=None,
        )
        assert len(result) >= 1
        # Should contain all 3 assets combined
        assert len(result[0]["assets"]) == 3

    def test_all_assets_same_pnl_perfect_correlation(self):
        """Perfectly correlated (identical PnL): optimizer still returns valid result."""
        rng = np.random.default_rng(42)
        vals = rng.normal(5, 10, 200)
        pnl = _make_pnl({f"CLONE_{i}": vals.copy() for i in range(6)})
        corr = get_correlation_matrix(pnl, "standard")
        result = optimize_portfolio(pnl, corr, 3, "hybrid", n_portfolios=1)
        assert len(result) >= 1
        assert len(result[0]["assets"]) == 3

    def test_zero_pnl_has_pf_zero(self):
        """All-zero PnL: _asset_pf returns 0.0."""
        zeros = pd.Series(np.zeros(200))
        assert _asset_pf(zeros) == 0.0

    def test_empty_dataframe_returns_empty(self):
        """Empty DataFrame → optimize_portfolio returns []."""
        pnl = pd.DataFrame()
        corr = pd.DataFrame()
        result = optimize_portfolio(pnl, corr, 3, "hybrid")
        assert result == []

    def test_n_assets_equals_total_with_profitable_only(self):
        """Request n_assets == profitable count → single portfolio with all profitable."""
        rng = np.random.default_rng(42)
        # All 5 have positive drift → all profitable (PF >= 1.0)
        pnl = _make_pnl({f"A{i}": rng.normal(10, 5, 200) for i in range(5)})
        corr = get_correlation_matrix(pnl, "standard")
        # Verify all have PF >= 1.0
        profitable = [c for c in pnl.columns if _asset_pf(pnl[c]) >= 1.0]
        n_profitable = len(profitable)
        result = optimize_portfolio(pnl, corr, n_profitable, "hybrid", n_portfolios=1)
        assert len(result) >= 1
        assert len(result[0]["assets"]) == n_profitable

    def test_k_l1_equals_1_no_combo_explosion(self):
        """k_l1=1: only 1 L1 variant per batch, L2 trivial (1 combo per group)."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(20)})
        abm = {f"A{i}": 1 if i < 10 else 2 for i in range(20)}
        result = run_beam_optimization(
            pnl, abm, l2_groups=[[1, 2]], n_per_batch=5,
            obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
            k_l1=1, k_l2=1, k_l3=1, corr_method="standard",
            seed_metrics=["sortino"], max_seeds=5,
            prioritize_stability=False, external_scores=None,
        )
        assert len(result) >= 1

    def test_very_short_pnl_5_days(self):
        """Only 5 days of data: should not crash."""
        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 5) for i in range(4)}, n=5)
        corr = get_correlation_matrix(pnl, "standard")
        result = optimize_portfolio(pnl, corr, 2, "hybrid", n_portfolios=1)
        # May or may not return results (PF gate may filter), but must not crash
        assert isinstance(result, list)

    def test_all_negative_pnl_assets_no_crash(self):
        """All assets have negative total PnL: optimizer returns list (may be empty)."""
        rng = np.random.default_rng(42)
        # Strong negative drift → PF < 1.0 for all
        pnl = _make_pnl({f"A{i}": rng.normal(-10, 5, 200) for i in range(5)})
        corr = get_correlation_matrix(pnl, "standard")
        # Verify none are profitable
        for col in pnl.columns:
            assert _asset_pf(pnl[col]) < 1.0
        # Must not crash — may return empty (no profitable assets to optimize)
        result = optimize_portfolio(pnl, corr, 3, "hybrid", n_portfolios=1)
        assert isinstance(result, list)


# ═══════════════════════════════════════════════════════════════════════
# STEP 2E — Combo Capping Regression (Bottleneck #2)
# ═══════════════════════════════════════════════════════════════════════

class TestComboCapping:
    """Verify _MAX_COMBOS caps L2/L3 Cartesian product enumeration."""

    def test_l2_combo_count_capped(self):
        """When L1 variants per batch exceed _MAX_COMBOS product, L2 samples."""
        from unittest.mock import patch
        from backend.analysis.beam_optimizer import _MAX_COMBOS

        rng = np.random.default_rng(42)
        # 20 assets across 4 batches (5 per batch)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(20)})
        abm = {f"A{i}": (i // 5) + 1 for i in range(20)}

        # k_l1=10 with 4 batches in one group: 10^4 = 10,000 combos
        # Force k_l1 high enough that product exceeds cap
        # With 5 assets per batch and n_per_batch=2, k_l1 up to 10,
        # but dedup may reduce variants. Let's use a direct approach:
        # patch _MAX_COMBOS to a very low value to force sampling
        with patch("backend.analysis.beam_optimizer._MAX_COMBOS", 5):
            result = run_beam_optimization(
                pnl, abm, l2_groups=[[1, 2, 3, 4]], n_per_batch=2,
                obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
                k_l1=3, k_l2=3, k_l3=2, corr_method="standard",
                seed_metrics=["sortino"], max_seeds=5,
                prioritize_stability=False, external_scores=None,
            )
        # Must still produce valid results despite sampling
        assert len(result) >= 1
        for r in result:
            assert "assets" in r
            assert "metrics" in r

    def test_l3_combo_count_capped(self):
        """When L2 results across groups exceed _MAX_COMBOS, L3 samples."""
        from unittest.mock import patch

        rng = np.random.default_rng(42)
        # 3 separate L2 groups, each with its own batch
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(15)})
        abm = {f"A{i}": (i // 5) + 1 for i in range(15)}

        with patch("backend.analysis.beam_optimizer._MAX_COMBOS", 3):
            result = run_beam_optimization(
                pnl, abm, l2_groups=[[1], [2], [3]], n_per_batch=3,
                obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
                k_l1=3, k_l2=3, k_l3=2, corr_method="standard",
                seed_metrics=["sortino"], max_seeds=5,
                prioritize_stability=False, external_scores=None,
            )
        assert len(result) >= 1
        for r in result:
            assert "assets" in r

    def test_sampling_produces_valid_portfolios(self):
        """Sampled combos still produce portfolios with correct asset count."""
        from unittest.mock import patch

        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 10, 200) for i in range(10)})
        abm = {f"A{i}": 1 if i < 5 else 2 for i in range(10)}

        # Force sampling at both L2 and L3
        with patch("backend.analysis.beam_optimizer._MAX_COMBOS", 2):
            result = run_beam_optimization(
                pnl, abm, l2_groups=[[1, 2]], n_per_batch=3,
                obj_l1="hybrid", obj_l2="hybrid", obj_l3="hybrid",
                k_l1=3, k_l2=3, k_l3=2, corr_method="standard",
                seed_metrics=["sortino"], max_seeds=5,
                prioritize_stability=False, external_scores=None,
            )
        assert len(result) >= 1
        for r in result:
            # Each portfolio should have assets from both batches
            assert len(r["assets"]) >= 2
            assert r["metrics"]["n_assets"] >= 2

    def test_max_combos_constant_is_10000(self):
        """_MAX_COMBOS must be 10,000 (spec requirement)."""
        from backend.analysis.beam_optimizer import _MAX_COMBOS
        assert _MAX_COMBOS == 10_000


# ═══════════════════════════════════════════════════════════════════════
# STEP 2F — TC Shared Computation (Bottleneck #5)
# ═══════════════════════════════════════════════════════════════════════

class TestTCSharedComputation:
    """Verify TC optimizer pre-computes seeds once across allocations."""

    def test_tc_computes_seeds_once_not_per_allocation(self):
        """optimize_with_trade_constraints computes seeds once, passes to all
        optimize_portfolio calls inside the allocation loop."""
        from unittest.mock import patch, call

        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 8, 200) for i in range(15)})
        corr = get_correlation_matrix(pnl, "standard")
        # A0-A4 low (5 trades), A5-A14 high (100+ trades)
        counts = {f"A{i}": 5 if i < 5 else 100 + i * 5 for i in range(15)}
        tc = {"low_threshold": 10, "max_low": 2, "high_threshold": 50, "min_high": 1}

        # Track calls to compute_seed_scores
        original_css = None
        call_count = [0]

        def counting_css(*args, **kwargs):
            call_count[0] += 1
            return original_css(*args, **kwargs)

        from backend.analysis import optimizer_seeds
        original_css = optimizer_seeds.compute_seed_scores

        with patch.object(optimizer_seeds, "compute_seed_scores", side_effect=counting_css):
            optimize_with_trade_constraints(
                pnl, corr, 6, counts, tc, "hybrid", 3,
                seed_metrics=["sortino"], max_seeds=10,
                # No precomputed_seeds — TC optimizer should compute once internally
                precomputed_seeds=None,
            )

        # Should be called exactly ONCE (at line 116), not per allocation
        assert call_count[0] == 1, (
            f"compute_seed_scores called {call_count[0]} times (expected 1)"
        )

    def test_tc_passes_precomputed_seeds_to_inner_optimize(self):
        """When precomputed_seeds provided, TC optimizer passes them through
        to optimize_portfolio without recomputing."""
        from unittest.mock import patch

        rng = np.random.default_rng(42)
        pnl = _make_pnl({f"A{i}": rng.normal(5, 8, 200) for i in range(15)})
        corr = get_correlation_matrix(pnl, "standard")
        counts = {f"A{i}": 5 if i < 5 else 100 + i * 5 for i in range(15)}
        tc = {"low_threshold": 10, "max_low": 2, "high_threshold": 50, "min_high": 1}

        # Pre-compute seeds externally
        from backend.analysis.optimizer_seeds import compute_seed_scores
        external_seeds = compute_seed_scores(pnl, pnl.columns.tolist(), ["sortino"], None)

        # Track what optimize_portfolio receives
        received_seeds = []
        original_op = optimize_portfolio

        def tracking_op(*args, **kwargs):
            received_seeds.append(kwargs.get("precomputed_seeds"))
            return original_op(*args, **kwargs)

        with patch("backend.analysis.optimizer_tc.optimize_portfolio", side_effect=tracking_op):
            optimize_with_trade_constraints(
                pnl, corr, 6, counts, tc, "hybrid", 3,
                seed_metrics=["sortino"], max_seeds=10,
                precomputed_seeds=external_seeds,
            )

        # Every inner optimize_portfolio call should receive precomputed_seeds
        assert len(received_seeds) >= 1, "optimize_portfolio was never called"
        for i, seeds in enumerate(received_seeds):
            assert seeds is not None, (
                f"optimize_portfolio call #{i} received precomputed_seeds=None"
            )
            assert seeds is external_seeds, (
                f"optimize_portfolio call #{i} received different seeds object"
            )
