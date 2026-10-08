"""Tests for the Settings Recommender module.

Covers pool profiling, quick-mode heuristic recommendations,
thorough-mode cross-validation, edge cases, and result serialization.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.analysis.settings_recommender import (
    PoolProfile,
    Recommendation,
    RecommenderResult,
    profile_pool,
    recommend_quick,
    recommend_settings,
    recommend_thorough,
    result_to_dict,
    _build_settings_grid,
    _compute_asset_metrics,
    _run_single_cv_fold,
)


# ═══════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def pool_30():
    """30 assets (15L + 15S) across 3 batches, 200 days of daily PnL.

    Includes 2 suspicious outliers with extreme metrics.
    """
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=200)
    cols = []
    batch_map = {}

    # Batch 138: 5 Longs + 5 Shorts (normal)
    for i in range(5):
        tag = f"ASSET{i}~B138_Long"
        cols.append(tag)
        batch_map[tag] = 138
    for i in range(5):
        tag = f"ASSET{i}~B138_Short"
        cols.append(tag)
        batch_map[tag] = 138

    # Batch 141: 5 Longs + 5 Shorts (normal)
    for i in range(5):
        tag = f"ASSET{i}~B141_Long"
        cols.append(tag)
        batch_map[tag] = 141
    for i in range(5):
        tag = f"ASSET{i}~B141_Short"
        cols.append(tag)
        batch_map[tag] = 141

    # Batch 142: 3 Longs + 5 Shorts + 2 outliers
    for i in range(3):
        tag = f"ASSET{i}~B142_Long"
        cols.append(tag)
        batch_map[tag] = 142
    for i in range(5):
        tag = f"ASSET{i}~B142_Short"
        cols.append(tag)
        batch_map[tag] = 142

    # Outliers
    outlier1 = "OUTLIER1~B142_Short"
    outlier2 = "OUTLIER2~B142_Long"
    cols.extend([outlier1, outlier2])
    batch_map[outlier1] = 142
    batch_map[outlier2] = 142

    # Build PnL data
    data = np.random.randn(200, len(cols)) * 50 + 10

    # Make outliers look great in train, terrible in validation
    idx1 = cols.index(outlier1)
    idx2 = cols.index(outlier2)
    data[:140, idx1] = np.random.randn(140) * 20 + 100  # great first 70%
    data[140:, idx1] = np.random.randn(60) * 30 - 50     # terrible last 30%
    data[:140, idx2] = np.random.randn(140) * 15 + 80
    data[140:, idx2] = np.random.randn(60) * 25 - 40

    daily_pnl = pd.DataFrame(data, index=dates, columns=cols)

    # Create mock trades (simple: one trade per non-zero PnL day per asset)
    class MockTrade:
        def __init__(self, key, pnl):
            self._tagged_key = key
            self.net_profit = pnl
            self.exit_time = None
            self.position_id = None
            self.id = 0

    trades = []
    for col in cols:
        non_zero = daily_pnl[col][daily_pnl[col] != 0]
        # Simulate sparse trades (every 10th non-zero day)
        for idx, val in non_zero.iloc[::10].items():
            trades.append(MockTrade(col, float(val)))

    return daily_pnl, trades, batch_map


@pytest.fixture
def corr_matrix_30(pool_30):
    """Pre-computed correlation matrix for pool_30."""
    daily_pnl, _, _ = pool_30
    return daily_pnl.corr()


# ═══════════════════════════════════════════════════════════════════════
#  Test Class 1 — Pool Profiling
# ═══════════════════════════════════════════════════════════════════════

class TestPoolProfile:
    def test_basic_counts(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        prof, am = profile_pool(daily_pnl, trades, batch_map, corr_matrix_30)

        assert prof.n_assets == len(daily_pnl.columns)
        assert prof.n_batches == 3
        assert prof.n_longs > 0
        assert prof.n_shorts > 0
        assert prof.n_longs + prof.n_shorts == prof.n_assets

    def test_trade_count_stats(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        prof, am = profile_pool(daily_pnl, trades, batch_map, corr_matrix_30)

        assert prof.trade_count_median > 0
        assert prof.trade_count_min >= 1
        assert prof.trade_count_max >= prof.trade_count_min
        assert prof.trade_count_p25 <= prof.trade_count_p75

    def test_metric_distributions(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        prof, am = profile_pool(daily_pnl, trades, batch_map, corr_matrix_30)

        # Should have stats for all guard metrics
        assert "pf" in prof.metric_stats
        assert "sortino" in prof.metric_stats
        assert "max_dd" in prof.metric_stats

        # Each metric should have mean, std, skew, kurtosis
        pf_stats = prof.metric_stats["pf"]
        assert "mean" in pf_stats
        assert "std" in pf_stats
        assert "skew" in pf_stats
        assert "kurtosis" in pf_stats
        assert "p10" in pf_stats
        assert "p90" in pf_stats
        assert "outlier_pct" in pf_stats

    def test_correlation_structure(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        prof, am = profile_pool(daily_pnl, trades, batch_map, corr_matrix_30)

        assert 0.0 <= prof.corr_mean_abs <= 1.0
        assert 0.0 <= prof.corr_median_abs <= 1.0
        assert 0.0 <= prof.corr_p90_abs <= 1.0
        assert 0.0 <= prof.corr_density_above_50 <= 100.0

    def test_batch_balance(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        prof, am = profile_pool(daily_pnl, trades, batch_map, corr_matrix_30)

        assert len(prof.batch_sizes) == 3
        assert 0 < prof.batch_balance_ratio <= 1.0

    def test_quality_spread(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        prof, am = profile_pool(daily_pnl, trades, batch_map, corr_matrix_30)

        assert prof.pf_spread >= 0
        # Gini should be between 0 and 1
        assert -0.1 <= prof.quality_gini <= 1.0

    def test_asset_metrics_computed(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        prof, am = profile_pool(daily_pnl, trades, batch_map, corr_matrix_30)

        assert len(am) == len(daily_pnl.columns)
        for tag, metrics in am.items():
            assert "pf" in metrics
            assert "sortino" in metrics
            assert "n_trades" in metrics
            assert "batch_id" in metrics


# ═══════════════════════════════════════════════════════════════════════
#  Test Class 2 — Quick Mode Recommendations
# ═══════════════════════════════════════════════════════════════════════

class TestQuickMode:
    def test_produces_recommendations(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        prof, am = profile_pool(daily_pnl, trades, batch_map, corr_matrix_30)
        recs = recommend_quick(prof, am, split_mode=True)

        assert len(recs) > 0
        # Should have at least algorithm, objective, and guard recommendations
        categories = set(r.category for r in recs)
        assert "algorithm" in categories
        assert "objective" in categories

    def test_recommendation_structure(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        prof, am = profile_pool(daily_pnl, trades, batch_map, corr_matrix_30)
        recs = recommend_quick(prof, am)

        for rec in recs:
            assert isinstance(rec, Recommendation)
            assert rec.setting != ""
            assert 0 <= rec.confidence <= 1.0
            assert rec.reason != ""
            assert rec.category in ("algorithm", "objective", "seed",
                                     "filter", "overfitting_guard")

    def test_low_trade_count_triggers_guard(self):
        """Pool with very low trade counts should trigger overfitting guard."""
        prof = PoolProfile(
            n_assets=30, n_batches=3, n_longs=15, n_shorts=15,
            trade_count_median=8.0, trade_count_mean=10.0,
            trade_count_min=3, trade_count_max=20,
            pct_below_10_trades=60.0, pct_below_20_trades=90.0,
            corr_mean_abs=0.15, corr_density_above_50=5.0,
            corr_density_above_30=15.0,
            pf_spread=3.0, quality_gini=0.25,
            batch_balance_ratio=0.8, n_suspicious=3,
            suspicious_tags=["OUT1", "OUT2", "OUT3"],
            metric_stats={"pf": {"skew": 2.0, "p90": 5.5}},
        )
        recs = recommend_quick(prof, {})

        guard_recs = [r for r in recs if r.setting == "overfit_guard_enabled"]
        assert len(guard_recs) == 1
        assert guard_recs[0].value is True
        assert guard_recs[0].confidence >= 0.8

    def test_high_trade_count_relaxes_guard(self):
        """Pool with adequate trade counts may not need aggressive guard."""
        prof = PoolProfile(
            n_assets=30, n_batches=3, n_longs=15, n_shorts=15,
            trade_count_median=50.0, trade_count_mean=55.0,
            trade_count_min=30, trade_count_max=100,
            pct_below_10_trades=0.0, pct_below_20_trades=0.0,
            corr_mean_abs=0.10, corr_density_above_50=2.0,
            corr_density_above_30=10.0,
            pf_spread=2.0, quality_gini=0.15,
            batch_balance_ratio=0.9, n_suspicious=0,
            metric_stats={"pf": {"skew": 0.5, "p90": 2.5}},
        )
        recs = recommend_quick(prof, {})

        guard_recs = [r for r in recs if r.setting == "overfit_guard_enabled"]
        assert len(guard_recs) == 1
        # With no suspicious and high trade counts, guard is optional
        assert guard_recs[0].value is False

    def test_stability_recommended_for_low_trades(self):
        """Low trade counts should recommend stability objective."""
        prof = PoolProfile(
            n_assets=30, n_batches=3, n_longs=15, n_shorts=15,
            trade_count_median=10.0,
            corr_mean_abs=0.15, corr_density_above_50=5.0,
            corr_density_above_30=15.0,
            quality_gini=0.2, n_suspicious=0,
            metric_stats={"pf": {"skew": 1.0, "p90": 3.0}},
        )
        recs = recommend_quick(prof, {})

        obj_recs = [r for r in recs if r.setting == "objective"]
        assert len(obj_recs) == 1
        assert obj_recs[0].value == "stability"

    def test_bayesian_k_scales_with_trades(self):
        """Very low trades → higher k, moderate → lower k."""
        # Very low
        prof_low = PoolProfile(
            n_assets=30, trade_count_median=8.0, n_suspicious=1,
            metric_stats={"pf": {"skew": 1.0, "p90": 3.0}},
            suspicious_tags=["X"],
        )
        recs_low = recommend_quick(prof_low, {})
        k_recs_low = [r for r in recs_low if r.setting == "of_bayes_k"]

        # Moderate
        prof_mod = PoolProfile(
            n_assets=30, trade_count_median=18.0, n_suspicious=1,
            metric_stats={"pf": {"skew": 1.0, "p90": 3.0}},
            suspicious_tags=["X"],
        )
        recs_mod = recommend_quick(prof_mod, {})
        k_recs_mod = [r for r in recs_mod if r.setting == "of_bayes_k"]

        if k_recs_low and k_recs_mod:
            assert k_recs_low[0].value >= k_recs_mod[0].value


# ═══════════════════════════════════════════════════════════════════════
#  Test Class 3 — Settings Grid
# ═══════════════════════════════════════════════════════════════════════

class TestSettingsGrid:
    def test_default_grid_size(self):
        grid = _build_settings_grid()
        # 4 algos × 5 obj × 3 spw × 4 guards × 4pf × 4tr × 4wr = 15360
        # (independent filter axes produce more combos; capped by max_combos at runtime)
        assert len(grid) > 100
        assert len(grid) <= 20000

    def test_custom_algos_reduces_grid(self):
        grid = _build_settings_grid(algos=["greedy", "hrp"])
        grid_all = _build_settings_grid()
        assert len(grid) < len(grid_all)

    def test_no_sweep_reduces_grid(self):
        grid = _build_settings_grid(
            sweep_guards=False, sweep_seed_prior=False, sweep_filters=False,
        )
        # Minimal: 4 algos × 5 obj × 1 spw × 1 guard × 1 filter = 20
        assert len(grid) <= 50

    def test_grid_structure(self):
        grid = _build_settings_grid(algos=["greedy"], objectives=["stability"])
        assert len(grid) > 0
        for entry in grid:
            assert "div_algo" in entry
            assert "objective" in entry
            assert "seed_prior_weight" in entry
            assert "guard_preset" in entry


# ═══════════════════════════════════════════════════════════════════════
#  Test Class 4 — Single CV Fold
# ═══════════════════════════════════════════════════════════════════════

class TestSingleCVFold:
    def test_basic_fold(self, pool_30):
        daily_pnl, trades, batch_map = pool_30
        settings = {
            "div_algo": "greedy",
            "objective": "stability",
            "seed_prior_weight": 0.1,
        }
        result = _run_single_cv_fold(
            settings, daily_pnl, trades, batch_map,
            train_idx=140, n_assets=10, n_longs=5, n_shorts=5,
            split_mode=True, seed_metrics=["sortino"],
        )

        # Should have val_stability and val_pf
        assert "val_stability" in result
        assert "val_pf" in result
        # No error
        assert "error" not in result or result.get("n_assets_selected", 0) > 0

    def test_fold_with_guard(self, pool_30):
        daily_pnl, trades, batch_map = pool_30
        settings = {
            "div_algo": "greedy",
            "objective": "stability",
            "seed_prior_weight": 0.1,
            "overfit_guard_enabled": True,
            "of_log_enabled": True,
            "of_log_caps": {"pf": 4.0, "sortino": 3.0, "payoff": 3.0,
                            "wr": 70.0, "sharpe": 2.5, "calmar": 3.0},
            "of_z_score_enabled": True,
            "of_z_threshold": 2.0,
            "of_z_penalty_strength": 0.15,
        }
        result = _run_single_cv_fold(
            settings, daily_pnl, trades, batch_map,
            train_idx=140, n_assets=10, n_longs=5, n_shorts=5,
            split_mode=True, seed_metrics=["sortino"],
        )
        assert "val_stability" in result

    def test_insufficient_data(self, pool_30):
        daily_pnl, trades, batch_map = pool_30
        settings = {"div_algo": "greedy", "objective": "stability"}
        # Very small train_idx → insufficient data
        result = _run_single_cv_fold(
            settings, daily_pnl, trades, batch_map,
            train_idx=3, n_assets=5, n_longs=3, n_shorts=3,
            split_mode=True, seed_metrics=["sortino"],
        )
        assert result.get("error") == "insufficient_data"

    def test_filters_can_eliminate_all(self, pool_30):
        daily_pnl, trades, batch_map = pool_30
        settings = {
            "div_algo": "greedy",
            "objective": "stability",
            "min_pf": 1e9,  # impossibly high
        }
        result = _run_single_cv_fold(
            settings, daily_pnl, trades, batch_map,
            train_idx=140, n_assets=5, n_longs=3, n_shorts=3,
            split_mode=True, seed_metrics=["sortino"],
        )
        assert result.get("error") == "all_filtered"


# ═══════════════════════════════════════════════════════════════════════
#  Test Class 5 — Thorough Mode (small grid)
# ═══════════════════════════════════════════════════════════════════════

class TestThoroughMode:
    def test_small_sweep(self, pool_30):
        """Run with minimal grid to test the full pipeline."""
        daily_pnl, trades, batch_map = pool_30
        cv_results, top_presets = recommend_thorough(
            daily_pnl, trades, batch_map,
            n_assets=8, n_longs=4, n_shorts=4,
            split_mode=True,
            algos=["greedy"],
            objectives=["stability"],
            sweep_guards=False,
            sweep_seed_prior=False,
            sweep_filters=False,
            n_splits=2,
            split_pcts=[0.65, 0.75],
        )

        assert len(cv_results) > 0
        assert len(top_presets) > 0
        assert len(top_presets) <= 5

        # Results should be sorted by composite score
        for i in range(len(top_presets) - 1):
            assert top_presets[i]["composite_score"] >= top_presets[i + 1]["composite_score"]

    def test_thorough_result_structure(self, pool_30):
        daily_pnl, trades, batch_map = pool_30
        cv_results, top_presets = recommend_thorough(
            daily_pnl, trades, batch_map,
            n_assets=6, n_longs=3, n_shorts=3,
            split_mode=True,
            algos=["greedy"],
            objectives=["stability"],
            sweep_guards=False,
            sweep_seed_prior=False,
            sweep_filters=False,
        )

        if cv_results:
            r = cv_results[0]
            assert "settings" in r
            assert "avg_val_stability" in r
            assert "avg_val_pf" in r
            assert "avg_overfit_ratio" in r
            assert "composite_score" in r

    def test_max_combos_cap(self, pool_30):
        daily_pnl, trades, batch_map = pool_30
        # With max_combos=5, only 5 combos tested
        cv_results, _ = recommend_thorough(
            daily_pnl, trades, batch_map,
            n_assets=6, n_longs=3, n_shorts=3,
            split_mode=True,
            algos=["greedy", "quality_first"],
            objectives=["stability", "hybrid"],
            sweep_guards=False,
            sweep_seed_prior=False,
            sweep_filters=False,
            max_combos=5,
        )
        assert len(cv_results) <= 5


# ═══════════════════════════════════════════════════════════════════════
#  Test Class 6 — Full Pipeline (recommend_settings)
# ═══════════════════════════════════════════════════════════════════════

class TestRecommendSettings:
    def test_quick_mode(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        result = recommend_settings(
            daily_pnl, trades, batch_map, corr_matrix_30,
            mode="quick", split_mode=True,
        )

        assert isinstance(result, RecommenderResult)
        assert result.mode == "quick"
        assert result.elapsed_seconds >= 0
        assert result.profile.n_assets > 0
        assert len(result.recommendations) > 0
        # Quick mode should not have CV results
        assert len(result.cv_results) == 0

    def test_thorough_mode(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        result = recommend_settings(
            daily_pnl, trades, batch_map, corr_matrix_30,
            mode="thorough", split_mode=True,
            n_assets=6, n_longs=3, n_shorts=3,
            algos=["greedy"],
            objectives=["stability"],
            sweep_guards=False,
            sweep_seed_prior=False,
            sweep_filters=False,
        )

        assert isinstance(result, RecommenderResult)
        assert result.mode == "thorough"
        assert len(result.recommendations) > 0  # Quick always runs
        assert len(result.top_presets) > 0  # Thorough adds CV

    def test_no_corr_provided(self, pool_30):
        """Should auto-compute correlation if not provided."""
        daily_pnl, trades, batch_map = pool_30
        result = recommend_settings(
            daily_pnl, trades, batch_map,
            corr_matrix=None, mode="quick",
        )
        assert result.profile.corr_mean_abs >= 0


# ═══════════════════════════════════════════════════════════════════════
#  Test Class 7 — Serialization
# ═══════════════════════════════════════════════════════════════════════

class TestSerialization:
    def test_result_to_dict(self, pool_30, corr_matrix_30):
        daily_pnl, trades, batch_map = pool_30
        result = recommend_settings(
            daily_pnl, trades, batch_map, corr_matrix_30,
            mode="quick",
        )
        d = result_to_dict(result)

        assert isinstance(d, dict)
        assert d["mode"] == "quick"
        assert "profile" in d
        assert "recommendations" in d
        assert isinstance(d["recommendations"], list)
        assert isinstance(d["profile"]["n_assets"], int)

    def test_dict_json_safe(self, pool_30, corr_matrix_30):
        """Ensure the dict can be serialized to JSON."""
        import json
        daily_pnl, trades, batch_map = pool_30
        result = recommend_settings(
            daily_pnl, trades, batch_map, corr_matrix_30,
            mode="quick",
        )
        d = result_to_dict(result)
        # Should not raise
        json_str = json.dumps(d)
        assert len(json_str) > 100


# ═══════════════════════════════════════════════════════════════════════
#  Test Class 8 — Edge Cases
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_single_batch(self):
        """Pool with only 1 batch."""
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=100)
        cols = [f"A{i}~B1_Long" for i in range(5)]
        data = np.random.randn(100, 5) * 30 + 10
        daily_pnl = pd.DataFrame(data, index=dates, columns=cols)
        batch_map = {c: 1 for c in cols}

        class MockTrade:
            def __init__(self, key):
                self._tagged_key = key

        trades = [MockTrade(c) for c in cols for _ in range(10)]
        prof, am = profile_pool(daily_pnl, trades, batch_map)

        assert prof.n_batches == 1
        assert prof.n_assets == 5

    def test_two_assets_minimum(self):
        """Minimal pool with 2 assets."""
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=50)
        cols = ["A~B1_Long", "B~B1_Short"]
        data = np.random.randn(50, 2) * 20 + 5
        daily_pnl = pd.DataFrame(data, index=dates, columns=cols)
        batch_map = {"A~B1_Long": 1, "B~B1_Short": 1}

        class MockTrade:
            def __init__(self, key):
                self._tagged_key = key

        trades = [MockTrade(c) for c in cols for _ in range(5)]
        result = recommend_settings(
            daily_pnl, trades, batch_map, mode="quick",
        )
        assert result.profile.n_assets == 2
        assert len(result.recommendations) > 0

    def test_all_same_pf(self):
        """All assets have identical PF — no outliers."""
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=100)
        cols = [f"A{i}~B1_Long" for i in range(10)]
        # Same positive drift for all
        data = np.random.randn(100, 10) * 10 + 5
        daily_pnl = pd.DataFrame(data, index=dates, columns=cols)
        batch_map = {c: 1 for c in cols}

        class MockTrade:
            def __init__(self, key):
                self._tagged_key = key

        trades = [MockTrade(c) for c in cols for _ in range(15)]
        prof, am = profile_pool(daily_pnl, trades, batch_map)

        assert prof.n_suspicious == 0

    def test_combined_mode(self, pool_30, corr_matrix_30):
        """Test with split_mode=False."""
        daily_pnl, trades, batch_map = pool_30
        result = recommend_settings(
            daily_pnl, trades, batch_map, corr_matrix_30,
            mode="quick", split_mode=False,
        )
        assert result.profile.n_assets > 0
        assert len(result.recommendations) > 0

    def test_empty_pool(self):
        """Empty DataFrame should be handled gracefully."""
        daily_pnl = pd.DataFrame()
        result = recommend_settings(daily_pnl, [], {}, mode="quick")
        assert result.profile.n_assets == 0
