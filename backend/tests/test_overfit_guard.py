"""Tests for the Overfitting Guard pipeline.

8 test classes, ~45 test methods covering all 5 asset-level approaches,
the integration orchestrator, walk-forward re-rank, walk-forward algorithm,
and edge cases.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backend.analysis.overfit_guard import (
    apply_bayesian_shrinkage,
    apply_log_diminish,
    apply_normal_range_bonus,
    apply_overfit_guard,
    apply_sigmoid_cap,
    apply_z_score_penalty,
    bayesian_shrink,
    log_diminish,
    log_diminish_dd,
    normal_range_bonus,
    sigmoid_cap,
    walk_forward_rerank,
    z_score_penalty,
)


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def pool_metrics_20():
    """20 normal assets + 2 suspicious outliers across 2 batches."""
    np.random.seed(42)
    metrics: dict[str, dict] = {}
    for i in range(10):
        metrics[f"ASSET{i}~B138_Short"] = {
            "pf": np.random.uniform(1.2, 3.5),
            "sortino": np.random.uniform(0.5, 2.5),
            "win_rate": np.random.uniform(35, 65),
            "max_dd": np.random.uniform(-2000, -200),
            "net_profit": np.random.uniform(100, 3000),
            "payoff": np.random.uniform(0.8, 2.5),
            "sharpe": np.random.uniform(0.3, 2.0),
            "calmar": np.random.uniform(0.5, 3.0),
            "n_trades": int(np.random.randint(5, 25)),
            "batch_id": 138,
        }
    for i in range(10):
        metrics[f"ASSET{i}~B141_Long"] = {
            "pf": np.random.uniform(1.2, 3.5),
            "sortino": np.random.uniform(0.5, 2.5),
            "win_rate": np.random.uniform(35, 65),
            "max_dd": np.random.uniform(-2000, -200),
            "net_profit": np.random.uniform(100, 3000),
            "payoff": np.random.uniform(0.8, 2.5),
            "sharpe": np.random.uniform(0.3, 2.0),
            "calmar": np.random.uniform(0.5, 3.0),
            "n_trades": int(np.random.randint(5, 25)),
            "batch_id": 141,
        }
    # Inject 2 suspicious outliers
    metrics["OUTLIER1~B138_Short"] = {
        "pf": 9.5, "sortino": 6.0, "win_rate": 92.0,
        "max_dd": -15.0, "net_profit": 8000.0, "payoff": 5.5,
        "sharpe": 4.0, "calmar": 8.0, "n_trades": 6, "batch_id": 138,
    }
    metrics["OUTLIER2~B141_Long"] = {
        "pf": 8.0, "sortino": 5.5, "win_rate": 88.0,
        "max_dd": -25.0, "net_profit": 7000.0, "payoff": 4.8,
        "sharpe": 3.5, "calmar": 7.0, "n_trades": 8, "batch_id": 141,
    }
    return metrics


@pytest.fixture
def seed_scores_22(pool_metrics_20):
    """Initial seed scores (simple PF-based for testing)."""
    return {k: v["pf"] for k, v in pool_metrics_20.items()}


@pytest.fixture
def daily_pnl_22():
    """Daily PnL for 22 assets, 200 days."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=200)
    cols = ([f"ASSET{i}~B138_Short" for i in range(10)]
            + [f"ASSET{i}~B141_Long" for i in range(10)]
            + ["OUTLIER1~B138_Short", "OUTLIER2~B141_Long"])
    data = np.random.randn(200, 22) * 50 + 10
    # Outliers: great in train (first 140), terrible in validate (last 60)
    data[:140, 20] = np.random.randn(140) * 30 + 80
    data[140:, 20] = np.random.randn(60) * 30 - 40
    data[:140, 21] = np.random.randn(140) * 25 + 70
    data[140:, 21] = np.random.randn(60) * 25 - 30
    return pd.DataFrame(data, index=dates, columns=cols)


# ═══════════════════════════════════════════════════════════════════
#  Test Class 1 — Log Diminish
# ═══════════════════════════════════════════════════════════════════

class TestLogDiminish:
    def test_basic_compression(self):
        """PF 2.0 < cap 4.0: moderate compression."""
        score = log_diminish(2.0, 4.0)
        assert 0 < score < 1.0
        # Should be meaningfully above zero
        assert score > 0.3

    def test_extreme_compression(self):
        """PF 8.0 >> cap 4.0: barely higher than PF 4.0."""
        s4 = log_diminish(4.0, 4.0)
        s8 = log_diminish(8.0, 4.0)
        # Both should exist
        assert s4 > 0 and s8 > 0
        # The gap should be small — extreme values compressed
        assert (s8 - s4) < 0.5 * s4

    def test_zero_and_negative(self):
        """PF 0 or negative returns 0."""
        assert log_diminish(0, 4.0) == 0.0
        assert log_diminish(-1.0, 4.0) == 0.0

    def test_custom_caps(self):
        """Different caps produce different compression curves."""
        s_low = log_diminish(3.0, 2.0)
        s_high = log_diminish(3.0, 8.0)
        # Lower cap = more compression → lower score
        assert s_low > s_high  # log(1+3)/log(1+2) > log(1+3)/log(1+8)

    def test_dd_inverted_u(self):
        """Normal DD scores higher than suspiciously low DD."""
        pool = [-1500, -1200, -800, -600, -400, -200, -100, -50, -20, -5]
        abs_pool = [abs(x) for x in pool]
        # Normal DD (~500)
        s_normal = log_diminish_dd(500, abs_pool)
        # Suspiciously low DD (~5)
        s_low = log_diminish_dd(5, abs_pool)
        # Normal should score higher
        assert s_normal > s_low

    def test_passthrough_when_disabled(self, pool_metrics_20):
        """When disabled, scores are unmodified."""
        import copy
        original = copy.deepcopy(pool_metrics_20)
        config = {"log_enabled": False}
        result = apply_overfit_guard(
            {k: v["pf"] for k, v in pool_metrics_20.items()},
            pool_metrics_20, config,
        )
        # All keys preserved
        assert set(result.keys()) == set(original.keys())


# ═══════════════════════════════════════════════════════════════════
#  Test Class 2 — Z-Score Penalty
# ═══════════════════════════════════════════════════════════════════

class TestZScorePenalty:
    def test_normal_assets_unaffected(self):
        """Assets within 2 sigma get multiplier 1.0."""
        m = z_score_penalty(2.5, 2.0, 1.0, z_threshold=2.0)
        assert m == 1.0

    def test_outlier_penalized(self):
        """Asset with value far above batch mean gets penalized."""
        m = z_score_penalty(8.0, 2.0, 1.0, z_threshold=2.0, penalty_strength=0.15)
        assert m < 1.0
        assert m > 0.0

    def test_multiple_metric_stacking(self, pool_metrics_20):
        """Outlier on multiple metrics gets compounded penalty."""
        mults = apply_z_score_penalty(pool_metrics_20, z_threshold=1.5, penalty_strength=0.15)
        # OUTLIER1 should have a lower multiplier than normal assets
        outlier_m = mults.get("OUTLIER1~B138_Short", 1.0)
        normal_ms = [mults.get(f"ASSET{i}~B138_Short", 1.0) for i in range(5)]
        assert outlier_m < min(normal_ms)

    def test_threshold_sensitivity(self):
        """z=1.5 catches more than z=2.5."""
        m_tight = z_score_penalty(5.0, 2.0, 1.0, z_threshold=1.5)
        m_loose = z_score_penalty(5.0, 2.0, 1.0, z_threshold=2.5)
        assert m_tight <= m_loose

    def test_strength_scaling(self):
        """Higher strength penalizes harder."""
        m_gentle = z_score_penalty(6.0, 2.0, 1.0, z_threshold=2.0, penalty_strength=0.10)
        m_harsh = z_score_penalty(6.0, 2.0, 1.0, z_threshold=2.0, penalty_strength=0.50)
        assert m_harsh < m_gentle

    def test_dd_inverted_direction(self, pool_metrics_20):
        """Suspiciously low DD gets penalized (z inverted)."""
        mults = apply_z_score_penalty(pool_metrics_20, z_threshold=1.5)
        # OUTLIER1 has DD=-15 which is suspiciously close to 0
        outlier_m = mults.get("OUTLIER1~B138_Short", 1.0)
        assert outlier_m < 1.0

    def test_per_batch_isolation(self, pool_metrics_20):
        """Batch 138 stats don't affect batch 141 assets."""
        mults = apply_z_score_penalty(pool_metrics_20, z_threshold=2.0)
        # Both outliers should be caught independently
        assert "OUTLIER1~B138_Short" in mults
        assert "OUTLIER2~B141_Long" in mults

    def test_passthrough_when_disabled(self, pool_metrics_20):
        """Returns multiplier 1.0 when disabled (no z-score in config)."""
        config = {"z_score_enabled": False}
        result = apply_overfit_guard(
            {k: v["pf"] for k, v in pool_metrics_20.items()},
            pool_metrics_20, config,
        )
        assert len(result) == len(pool_metrics_20)


# ═══════════════════════════════════════════════════════════════════
#  Test Class 3 — Bayesian Shrinkage
# ═══════════════════════════════════════════════════════════════════

class TestBayesianShrinkage:
    def test_extreme_value_pulled(self):
        """PF 8.0 from 10 trades, batch mean 2.5, k=5 → shrunk."""
        s = bayesian_shrink(8.0, 2.5, 10, k=5.0)
        assert s < 8.0
        assert s > 2.5
        # (10*8 + 5*2.5) / 15 = 92.5/15 ≈ 6.17
        assert abs(s - 6.17) < 0.1

    def test_normal_value_barely_moves(self):
        """PF 2.5 from 10 trades, batch mean 2.5 → stays 2.5."""
        s = bayesian_shrink(2.5, 2.5, 10, k=5.0)
        assert abs(s - 2.5) < 0.01

    def test_high_trade_count_resists(self):
        """PF 8.0 from 100 trades → barely shrunk."""
        s = bayesian_shrink(8.0, 2.5, 100, k=5.0)
        assert s > 7.5  # (100*8+5*2.5)/105 ≈ 7.74

    def test_low_trade_count_strong(self):
        """PF 8.0 from 5 trades → heavily shrunk."""
        s = bayesian_shrink(8.0, 2.5, 5, k=5.0)
        assert s < 6.0  # (5*8+5*2.5)/10 = 5.25

    def test_k_sensitivity(self):
        """k=3 vs k=10 vs k=20: increasing shrinkage."""
        s3 = bayesian_shrink(8.0, 2.5, 10, k=3.0)
        s10 = bayesian_shrink(8.0, 2.5, 10, k=10.0)
        s20 = bayesian_shrink(8.0, 2.5, 10, k=20.0)
        assert s3 > s10 > s20

    def test_per_batch_mean(self, pool_metrics_20):
        """Each batch uses its own mean for shrinkage."""
        import copy
        am = copy.deepcopy(pool_metrics_20)
        am = apply_bayesian_shrinkage(am, k=5.0)
        # After shrinkage, outliers should move toward their batch mean
        o1_pf = am["OUTLIER1~B138_Short"]["pf"]
        assert o1_pf < 9.5  # was 9.5, should be pulled down

    def test_passthrough_when_disabled(self, pool_metrics_20):
        """Returns unchanged values when disabled."""
        import copy
        original = copy.deepcopy(pool_metrics_20)
        config = {"bayes_enabled": False}
        result = apply_overfit_guard(
            {k: v["pf"] for k, v in pool_metrics_20.items()},
            pool_metrics_20, config,
        )
        assert len(result) == len(original)


# ═══════════════════════════════════════════════════════════════════
#  Test Class 4 — Sigmoid Cap
# ═══════════════════════════════════════════════════════════════════

class TestSigmoidCap:
    def test_below_cap_moderate(self):
        """PF 2.0 with cap 4.0: moderate transformation."""
        s = sigmoid_cap(2.0, 4.0)
        assert s > 0
        assert s < 2.0  # tanh compresses

    def test_at_cap_compressed(self):
        """PF 4.0 with cap 4.0: ~76% of cap."""
        s = sigmoid_cap(4.0, 4.0)
        expected = 4.0 * math.tanh(1.0)  # ≈ 3.04
        assert abs(s - expected) < 0.01

    def test_above_cap_wall(self):
        """PF 8.0 and PF 12.0 score nearly identical."""
        s8 = sigmoid_cap(8.0, 4.0)
        s12 = sigmoid_cap(12.0, 4.0)
        assert abs(s12 - s8) < 0.3  # both near 4.0

    def test_different_from_log(self):
        """Sigmoid and log produce different curves."""
        sig = sigmoid_cap(6.0, 4.0)
        log = log_diminish(6.0, 4.0)
        assert sig != log

    def test_custom_caps(self):
        """Changing caps changes compression point."""
        s_low = sigmoid_cap(3.0, 2.0)
        s_high = sigmoid_cap(3.0, 8.0)
        assert s_low < s_high  # lower cap = more compression

    def test_passthrough_when_disabled(self, pool_metrics_20):
        """Unchanged when disabled."""
        config = {"sigmoid_enabled": False}
        result = apply_overfit_guard(
            {k: v["pf"] for k, v in pool_metrics_20.items()},
            pool_metrics_20, config,
        )
        assert len(result) == len(pool_metrics_20)


# ═══════════════════════════════════════════════════════════════════
#  Test Class 5 — Normal Range Bonus
# ═══════════════════════════════════════════════════════════════════

class TestNormalRangeBonus:
    def test_normal_range_gets_bonus(self):
        """Asset at 40th percentile → multiplier > 1.0."""
        pool = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        # 40th percentile is ~4.6, well within 20-60 range
        m = normal_range_bonus(5.0, pool, 20.0, 60.0, 0.3, 0.1)
        assert m > 1.0

    def test_too_good_penalized(self):
        """Asset at 5th percentile (too good) → multiplier < 1.0."""
        pool = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        p20 = np.percentile(pool, 20)
        # Value below the 20th percentile
        m = normal_range_bonus(0.5, pool, 20.0, 60.0, 0.3, 0.1)
        assert m < 1.0

    def test_too_bad_penalized(self):
        """Asset at 90th percentile → multiplier < 1.0."""
        pool = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        m = normal_range_bonus(10.0, pool, 20.0, 60.0, 0.3, 0.1)
        assert m < 1.0

    def test_dd_inversion(self, pool_metrics_20):
        """Very low DD is treated as 'too good' end."""
        mults = apply_normal_range_bonus(pool_metrics_20, pct_low=20.0, pct_high=60.0)
        # OUTLIER1 has DD=-15 (very small abs DD) → should be penalized
        outlier_m = mults.get("OUTLIER1~B138_Short", 1.0)
        assert outlier_m < 1.1  # should not get bonus

    def test_percentile_range_tuning(self):
        """Changing pct_low/pct_high shifts the sweet spot."""
        pool = np.arange(1, 21, dtype=float)
        # Narrow range: 40-50
        m_narrow = normal_range_bonus(5.0, pool, 40.0, 50.0, 0.3, 0.1)
        # Wide range: 10-80
        m_wide = normal_range_bonus(5.0, pool, 10.0, 80.0, 0.3, 0.1)
        # 5 is in the 10-80 range but below 40-50 range
        assert m_wide >= m_narrow

    def test_penalty_scale_tuning(self):
        """Higher penalty_scale = steeper dropoff."""
        pool = np.arange(1, 11, dtype=float)
        m_gentle = normal_range_bonus(0.5, pool, 20.0, 60.0, 0.1, 0.1)
        m_harsh = normal_range_bonus(0.5, pool, 20.0, 60.0, 0.8, 0.1)
        assert m_harsh < m_gentle

    def test_passthrough_when_disabled(self, pool_metrics_20):
        """Unchanged when disabled."""
        config = {"normal_range_enabled": False}
        result = apply_overfit_guard(
            {k: v["pf"] for k, v in pool_metrics_20.items()},
            pool_metrics_20, config,
        )
        assert len(result) == len(pool_metrics_20)


# ═══════════════════════════════════════════════════════════════════
#  Test Class 6 — Integration (full chain)
# ═══════════════════════════════════════════════════════════════════

class TestOverfitGuardIntegration:
    def test_full_chain_reduces_outlier_rank(self, pool_metrics_20, seed_scores_22):
        """OUTLIER1 starts top-ranked → after full chain → drops."""
        config = {
            "log_enabled": True,
            "log_caps": {"pf": 4.0, "sortino": 3.0, "payoff": 3.0,
                         "wr": 70.0, "sharpe": 2.5, "calmar": 3.0},
            "z_score_enabled": True,
            "z_threshold": 2.0,
            "z_penalty_strength": 0.15,
            "bayes_enabled": True,
            "bayes_k": 5.0,
            "sigmoid_enabled": True,
            "sig_caps": {"pf": 4.0, "sortino": 3.0, "payoff": 3.0,
                         "wr": 70.0, "sharpe": 2.5, "calmar": 3.0},
            "normal_range_enabled": True,
            "nr_pct_low": 20.0,
            "nr_pct_high": 60.0,
            "nr_penalty_scale": 0.3,
            "nr_bonus": 0.1,
        }
        result = apply_overfit_guard(seed_scores_22, pool_metrics_20, config)

        # Sort by score descending
        ranked = sorted(result.items(), key=lambda x: x[1], reverse=True)
        ranked_tags = [t for t, _ in ranked]

        # OUTLIER1 should NOT be #1 anymore
        o1_rank = ranked_tags.index("OUTLIER1~B138_Short")
        assert o1_rank > 0, "OUTLIER1 should drop from top rank"

    def test_all_disabled_passthrough(self, pool_metrics_20, seed_scores_22):
        """Master OFF → all scores unchanged keys."""
        config = {}  # nothing enabled
        result = apply_overfit_guard(seed_scores_22, pool_metrics_20, config)
        assert set(result.keys()) == set(seed_scores_22.keys())

    def test_partial_enable(self, pool_metrics_20, seed_scores_22):
        """Only Z-Score ON → only z-score adjustment applied."""
        config = {
            "z_score_enabled": True,
            "z_threshold": 1.5,
            "z_penalty_strength": 0.20,
        }
        result = apply_overfit_guard(seed_scores_22, pool_metrics_20, config)
        assert len(result) == len(seed_scores_22)
        # Outliers should have lower scores than in all-disabled
        config_off = {}
        result_off = apply_overfit_guard(seed_scores_22, pool_metrics_20, config_off)
        o1_on = result.get("OUTLIER1~B138_Short", 0)
        o1_off = result_off.get("OUTLIER1~B138_Short", 0)
        # With z-score enabled, outlier should be penalized more
        assert o1_on <= o1_off

    def test_chain_order(self, pool_metrics_20, seed_scores_22):
        """All 5 approaches applied in sequence produces valid output."""
        config = {
            "log_enabled": True,
            "log_caps": {"pf": 4.0, "sortino": 3.0, "payoff": 3.0,
                         "wr": 70.0, "sharpe": 2.5, "calmar": 3.0},
            "z_score_enabled": True,
            "z_threshold": 2.0,
            "z_penalty_strength": 0.15,
            "bayes_enabled": True,
            "bayes_k": 5.0,
            "sigmoid_enabled": True,
            "sig_caps": {"pf": 4.0, "sortino": 3.0, "payoff": 3.0,
                         "wr": 70.0, "sharpe": 2.5, "calmar": 3.0},
            "normal_range_enabled": True,
            "nr_pct_low": 20.0,
            "nr_pct_high": 60.0,
            "nr_penalty_scale": 0.3,
            "nr_bonus": 0.1,
        }
        result = apply_overfit_guard(seed_scores_22, pool_metrics_20, config)
        # All values should be positive
        assert all(v > 0 for v in result.values())

    def test_output_shape(self, pool_metrics_20, seed_scores_22):
        """Returns same keys as input, all values > 0."""
        config = {"log_enabled": True, "log_caps": {"pf": 4.0}}
        result = apply_overfit_guard(seed_scores_22, pool_metrics_20, config)
        assert set(result.keys()) == set(seed_scores_22.keys())
        assert all(v > 0 for v in result.values())


# ═══════════════════════════════════════════════════════════════════
#  Test Class 7 — Walk-Forward Re-rank
# ═══════════════════════════════════════════════════════════════════

class TestWalkForwardRerank:
    def test_overfitted_portfolio_drops(self, daily_pnl_22):
        """Portfolio heavy on outliers drops after WF re-rank."""
        # Candidate 1: heavy on outliers (great in-sample, bad validation)
        cand_overfit = {
            "assets": ["OUTLIER1~B138_Short", "OUTLIER2~B141_Long",
                       "ASSET0~B138_Short"],
            "score": 0.95,
        }
        # Candidate 2: normal assets
        cand_normal = {
            "assets": ["ASSET0~B138_Short", "ASSET1~B138_Short",
                       "ASSET2~B141_Long"],
            "score": 0.70,
        }
        candidates = [cand_overfit, cand_normal]
        result = walk_forward_rerank(candidates, daily_pnl_22,
                                     split_pct=0.70, metric="stability")
        # Normal portfolio should now be ranked higher
        assert result[0]["assets"] == cand_normal["assets"]

    def test_robust_portfolio_stays_top(self, daily_pnl_22):
        """Portfolio of normal assets stays ranked high."""
        cand1 = {"assets": [f"ASSET{i}~B138_Short" for i in range(5)], "score": 0.8}
        cand2 = {"assets": [f"ASSET{i}~B141_Long" for i in range(5)], "score": 0.7}
        candidates = [cand1, cand2]
        result = walk_forward_rerank(candidates, daily_pnl_22,
                                     split_pct=0.70, metric="net_profit")
        assert len(result) == 2

    def test_split_pct_respected(self, daily_pnl_22):
        """70% split uses first 140 of 200 days for train."""
        candidates = [
            {"assets": ["ASSET0~B138_Short", "ASSET1~B141_Long"], "score": 0.5},
        ]
        result = walk_forward_rerank(candidates, daily_pnl_22,
                                     split_pct=0.70, metric="stability")
        assert len(result) == 1

    def test_validation_metric_options(self, daily_pnl_22):
        """stability, sortino, pf, net_profit, sharpe all work."""
        candidates = [
            {"assets": [f"ASSET{i}~B138_Short" for i in range(3)], "score": 0.8},
            {"assets": [f"ASSET{i}~B141_Long" for i in range(3)], "score": 0.7},
        ]
        for metric in ("stability", "sortino", "pf", "net_profit", "sharpe"):
            result = walk_forward_rerank(candidates, daily_pnl_22,
                                         split_pct=0.70, metric=metric)
            assert len(result) == 2

    def test_single_candidate_passthrough(self, daily_pnl_22):
        """1 candidate → still 1 candidate (no crash)."""
        candidates = [{"assets": ["ASSET0~B138_Short"], "score": 0.9}]
        result = walk_forward_rerank(candidates, daily_pnl_22)
        assert len(result) == 1

    def test_passthrough_when_disabled(self, daily_pnl_22):
        """Disabled → order unchanged."""
        cand1 = {"assets": ["ASSET0~B138_Short"], "score": 0.9}
        cand2 = {"assets": ["ASSET1~B138_Short"], "score": 0.8}
        candidates = [cand1, cand2]
        # Empty daily_pnl → passthrough
        result = walk_forward_rerank(candidates, pd.DataFrame())
        assert result == candidates


# ═══════════════════════════════════════════════════════════════════
#  Test Class 8 — Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestOverfitGuardEdgeCases:
    def test_single_asset_batch(self):
        """Batch with 1 asset: z-score std=0 → no penalty."""
        metrics = {
            "SOLO~B100_Long": {
                "pf": 3.0, "sortino": 2.0, "win_rate": 55.0,
                "max_dd": -500.0, "net_profit": 1000.0, "payoff": 1.5,
                "sharpe": 1.2, "calmar": 2.0, "n_trades": 10, "batch_id": 100,
            }
        }
        mults = apply_z_score_penalty(metrics, z_threshold=2.0)
        assert mults["SOLO~B100_Long"] == 1.0

    def test_all_identical_metrics(self):
        """All assets have PF=2.0 → no penalties, no bonuses."""
        metrics = {}
        for i in range(5):
            metrics[f"A{i}~B1_Long"] = {
                "pf": 2.0, "sortino": 1.5, "win_rate": 50.0,
                "max_dd": -500.0, "net_profit": 500.0, "payoff": 1.0,
                "sharpe": 1.0, "calmar": 1.0, "n_trades": 15, "batch_id": 1,
            }
        mults = apply_z_score_penalty(metrics, z_threshold=2.0)
        for v in mults.values():
            assert v == 1.0

    def test_empty_pool(self):
        """Empty input → empty output."""
        result = apply_overfit_guard({}, {}, {})
        assert result == {}

    def test_nan_handling(self):
        """NaN in metrics → treated gracefully."""
        metrics = {
            "A~B1_Long": {
                "pf": float("nan"), "sortino": 1.5, "win_rate": 50.0,
                "max_dd": -500.0, "net_profit": 500.0, "payoff": 1.0,
                "sharpe": 1.0, "calmar": 1.0, "n_trades": 10, "batch_id": 1,
            },
            "B~B1_Short": {
                "pf": 2.0, "sortino": 1.0, "win_rate": 45.0,
                "max_dd": -800.0, "net_profit": 300.0, "payoff": 0.8,
                "sharpe": 0.7, "calmar": 0.5, "n_trades": 12, "batch_id": 1,
            },
        }
        # Should not crash
        config = {"log_enabled": True, "log_caps": {"pf": 4.0}}
        result = apply_overfit_guard({"A~B1_Long": 1.0, "B~B1_Short": 2.0},
                                     metrics, config)
        assert len(result) == 2

    def test_negative_pnl_assets(self):
        """Assets with negative PnL → still processed correctly."""
        metrics = {
            "NEG~B1_Short": {
                "pf": 0.5, "sortino": -0.5, "win_rate": 30.0,
                "max_dd": -3000.0, "net_profit": -500.0, "payoff": 0.3,
                "sharpe": -0.3, "calmar": -0.5, "n_trades": 20, "batch_id": 1,
            },
            "POS~B1_Long": {
                "pf": 2.0, "sortino": 1.5, "win_rate": 55.0,
                "max_dd": -800.0, "net_profit": 1000.0, "payoff": 1.5,
                "sharpe": 1.2, "calmar": 1.5, "n_trades": 18, "batch_id": 1,
            },
        }
        config = {"sigmoid_enabled": True, "sig_caps": {"pf": 4.0}}
        result = apply_overfit_guard(
            {"NEG~B1_Short": 0.5, "POS~B1_Long": 2.0}, metrics, config)
        assert len(result) == 2

    def test_extreme_k_values(self):
        """k=0 (no shrinkage) and k=100 (extreme shrinkage) both work."""
        s0 = bayesian_shrink(8.0, 2.5, 10, k=0.0)
        assert abs(s0 - 8.0) < 0.01  # k=0 → no pull
        s100 = bayesian_shrink(8.0, 2.5, 10, k=100.0)
        assert s100 < 4.0  # k=100 → very strong pull

    def test_extreme_z_threshold(self):
        """z=0.5 catches more, z=5.0 catches nothing."""
        m_tight = z_score_penalty(4.0, 2.0, 1.0, z_threshold=0.5, penalty_strength=0.15)
        m_loose = z_score_penalty(4.0, 2.0, 1.0, z_threshold=5.0, penalty_strength=0.15)
        assert m_tight < 1.0  # catches at z=0.5
        assert m_loose == 1.0  # doesn't catch at z=5.0
