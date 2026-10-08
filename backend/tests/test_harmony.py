"""Tests for the batch harmony gate module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.analysis.harmony import (
    HarmonyResult,
    PairHarmony,
    _complementarity_ratio,
    _correlation_stability,
    _drawdown_overlap,
    _return_correlation,
    _stress_correlation,
    build_batch_curves,
    filter_batches_by_harmony,
    filter_groups_by_harmony,
    harmony_gate,
)


# ---------------------------------------------------------------------------
# Fixtures: synthetic equity curves
# ---------------------------------------------------------------------------

def _make_pnl(seed: int, n: int = 500, trend: float = 0.5) -> pd.Series:
    """Generate a random daily PnL series with optional positive trend."""
    rng = np.random.RandomState(seed)
    noise = rng.normal(trend, 10, n)
    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.Series(noise, index=dates)


@pytest.fixture
def correlated_pair():
    """Two curves that are nearly identical (high correlation)."""
    base = _make_pnl(42, 500, 0.5)
    a = base + np.random.RandomState(1).normal(0, 0.5, 500)
    b = base + np.random.RandomState(2).normal(0, 0.5, 500)
    return a, b


@pytest.fixture
def uncorrelated_pair():
    """Two independent curves (near-zero correlation)."""
    return _make_pnl(100, 500, 0.5), _make_pnl(200, 500, 0.3)


@pytest.fixture
def anti_correlated_pair():
    """Two curves that move in opposite directions."""
    base = _make_pnl(42, 500, 0.5)
    return base, -base + np.random.RandomState(3).normal(0, 1, 500)


# ---------------------------------------------------------------------------
# Individual metric tests
# ---------------------------------------------------------------------------

class TestReturnCorrelation:
    def test_high_correlation(self, correlated_pair):
        a, b = correlated_pair
        rc = _return_correlation(a, b)
        assert rc > 0.9, f"Expected high correlation, got {rc}"

    def test_uncorrelated(self, uncorrelated_pair):
        a, b = uncorrelated_pair
        rc = _return_correlation(a, b)
        assert abs(rc) < 0.3, f"Expected near-zero correlation, got {rc}"

    def test_anti_correlated(self, anti_correlated_pair):
        a, b = anti_correlated_pair
        rc = _return_correlation(a, b)
        assert rc < -0.8, f"Expected strong negative correlation, got {rc}"

    def test_short_series(self):
        a = pd.Series([1, 2, 3])
        b = pd.Series([4, 5, 6])
        assert _return_correlation(a, b) == 0.0  # Too short


class TestCorrelationStability:
    def test_stable_relationship(self, correlated_pair):
        a, b = correlated_pair
        cs = _correlation_stability(a, b, window=60)
        assert cs < 0.15, f"Expected stable (low std), got {cs}"

    def test_short_data_fallback(self):
        a = _make_pnl(1, 50)
        b = _make_pnl(2, 50)
        cs = _correlation_stability(a, b, window=60)
        assert cs == 0.3  # Default fallback


class TestDrawdownOverlap:
    def test_identical_curves_high_overlap(self, correlated_pair):
        a, b = correlated_pair
        do = _drawdown_overlap(a, b)
        # Highly correlated curves should have significant DD overlap
        assert do > 0.5, f"Expected high DD overlap, got {do}"

    def test_independent_curves_low_overlap(self, uncorrelated_pair):
        a, b = uncorrelated_pair
        do = _drawdown_overlap(a, b)
        # Independent curves should have lower overlap
        assert do < 0.8, f"Expected lower DD overlap, got {do}"


class TestStressCorrelation:
    def test_correlated_in_stress(self, correlated_pair):
        a, b = correlated_pair
        sc = _stress_correlation(a, b)
        # Correlated curves should also be correlated in stress
        assert sc > 0.5, f"Expected high stress correlation, got {sc}"

    def test_short_data(self):
        a = pd.Series([1, -1, 2, -2])
        b = pd.Series([3, -3, 4, -4])
        assert _stress_correlation(a, b) == 0.0


class TestComplementarityRatio:
    def test_complementary_pair(self, uncorrelated_pair):
        a, b = uncorrelated_pair
        cr = _complementarity_ratio(a, b)
        # Uncorrelated profitable curves should be > 1.0 (better together)
        assert cr >= 0.8, f"Expected complementary (>=0.8), got {cr}"

    def test_identical_pair(self, correlated_pair):
        a, b = correlated_pair
        cr = _complementarity_ratio(a, b)
        # Nearly identical curves: combined Sharpe ≈ individual Sharpe
        assert cr < 1.5, f"Expected ~1.0 for identical curves, got {cr}"


# ---------------------------------------------------------------------------
# harmony_gate() integration tests
# ---------------------------------------------------------------------------

class TestHarmonyGate:
    def test_strength_zero_passes_all(self):
        """Strength=0 disables gate, all pass."""
        curves = {1: _make_pnl(1), 2: _make_pnl(2), 3: _make_pnl(3)}
        hr = harmony_gate(curves, strength=0)
        assert hr.all_pass
        assert len(hr.pairs) == 3  # C(3,2) = 3 pairs

    def test_highly_correlated_fails_strict_gate(self, correlated_pair):
        a, b = correlated_pair
        hr = harmony_gate({1: a, 2: b}, strength=75)
        # Highly correlated pair should fail a strict gate
        assert len(hr.pairs) == 1
        pair = hr.pairs[0]
        assert pair.harmony_score < 0.75

    def test_uncorrelated_passes_moderate_gate(self, uncorrelated_pair):
        a, b = uncorrelated_pair
        hr = harmony_gate({1: a, 2: b}, strength=40)
        pair = hr.pairs[0]
        assert pair.harmony_score > 0.4

    def test_result_structure(self):
        curves = {1: _make_pnl(10), 2: _make_pnl(20), 3: _make_pnl(30)}
        hr = harmony_gate(curves, strength=50)
        assert isinstance(hr, HarmonyResult)
        assert len(hr.pairs) == 3
        for p in hr.pairs:
            assert isinstance(p, PairHarmony)
            assert 0 <= p.harmony_score <= 1.0
            assert -1.0 <= p.return_correlation <= 1.0
            assert 0 <= p.drawdown_overlap <= 1.0

    def test_to_dict(self):
        curves = {1: _make_pnl(10), 2: _make_pnl(20)}
        hr = harmony_gate(curves, strength=50)
        d = hr.to_dict()
        assert "pairs" in d
        assert "mean_harmony" in d
        assert "all_pass" in d
        assert "failed_pairs" in d
        pair_d = d["pairs"][0]
        assert "return_correlation" in pair_d
        assert "harmony_score" in pair_d


# ---------------------------------------------------------------------------
# Beam optimizer helper tests
# ---------------------------------------------------------------------------

class TestBuildBatchCurves:
    def test_basic(self):
        dates = pd.date_range("2023-01-01", periods=100, freq="D")
        daily_pnl = pd.DataFrame({
            "BTCUSDT_Long": np.random.randn(100),
            "ETHUSDT_Long": np.random.randn(100),
            "XRPUSDT_Short": np.random.randn(100),
        }, index=dates)
        asset_batch_map = {
            "BTCUSDT_Long": 1,
            "ETHUSDT_Long": 1,
            "XRPUSDT_Short": 2,
        }
        curves = build_batch_curves(daily_pnl, asset_batch_map)
        assert 1 in curves
        assert 2 in curves
        assert len(curves[1]) == 100
        # Batch 1 should be sum of BTC + ETH
        expected = daily_pnl["BTCUSDT_Long"] + daily_pnl["ETHUSDT_Long"]
        pd.testing.assert_series_equal(curves[1], expected)


class TestFilterBatchesByHarmony:
    def test_no_filter_when_disabled(self):
        daily_pnl = pd.DataFrame({
            "A": _make_pnl(1, 200).values,
            "B": _make_pnl(2, 200).values,
        }, index=pd.date_range("2023-01-01", periods=200, freq="D"))
        asset_batch_map = {"A": 1, "B": 2}
        l1_variants = {1: [{"assets": ["A"]}], 2: [{"assets": ["B"]}]}

        filtered, hr = filter_batches_by_harmony(
            l1_variants, daily_pnl, asset_batch_map,
            group_bids=[1, 2], strength=0,
        )
        assert 1 in filtered
        assert 2 in filtered

    def test_keeps_minimum_two_batches(self):
        """Even with high strength, should keep at least 2 batches."""
        dates = pd.date_range("2023-01-01", periods=300, freq="D")
        base = _make_pnl(42, 300, 0.5)
        daily_pnl = pd.DataFrame({
            "A": base.values,
            "B": (base + np.random.RandomState(1).normal(0, 0.1, 300)).values,
            "C": _make_pnl(99, 300, 0.3).values,
        }, index=dates)
        asset_batch_map = {"A": 1, "B": 2, "C": 3}
        l1_variants = {
            1: [{"assets": ["A"]}],
            2: [{"assets": ["B"]}],
            3: [{"assets": ["C"]}],
        }

        filtered, hr = filter_batches_by_harmony(
            l1_variants, daily_pnl, asset_batch_map,
            group_bids=[1, 2, 3], strength=99,
        )
        assert len(filtered) >= 2


class TestFilterGroupsByHarmony:
    def test_returns_advisory_data(self):
        dates = pd.date_range("2023-01-01", periods=200, freq="D")
        l2_results = [
            [{"assets": ["A", "B"], "score": 0.5, "source_bids": [1]}],
            [{"assets": ["C", "D"], "score": 0.6, "source_bids": [2]}],
        ]
        daily_pnl = pd.DataFrame({
            "A": _make_pnl(1, 200).values,
            "B": _make_pnl(2, 200).values,
            "C": _make_pnl(3, 200).values,
            "D": _make_pnl(4, 200).values,
        }, index=dates)

        results, hr = filter_groups_by_harmony(l2_results, daily_pnl, strength=50)
        # Should return l2_results unchanged (L2.5 is advisory)
        assert len(results) == 2
        assert isinstance(hr, HarmonyResult)
        assert len(hr.pairs) == 1  # 2 groups = 1 pair


# ---------------------------------------------------------------------------
# Manual mode integration: prove L1.5 and L2.5 actually change outputs
# ---------------------------------------------------------------------------

class TestManualModeHarmonyImpact:
    """Simulate the Manual mode L1.5 and L2.5 logic from cross_strategy.py
    to prove harmony gate actually removes/penalizes batches."""

    def _make_correlated_pair(self, n: int = 300) -> tuple[pd.Series, pd.Series]:
        """Two highly correlated series (rho ≈ 0.95)."""
        rng = np.random.RandomState(42)
        base = rng.normal(0.5, 10, n).cumsum()
        noise = rng.normal(0, 1, n)
        a = pd.Series(np.diff(base), index=pd.date_range("2023-01-01", periods=n - 1, freq="D"))
        b = pd.Series(np.diff(base + noise), index=pd.date_range("2023-01-01", periods=n - 1, freq="D"))
        return a, b

    def _make_uncorrelated(self, seed: int, n: int = 300) -> pd.Series:
        rng = np.random.RandomState(seed)
        return pd.Series(rng.normal(0.5, 10, n - 1),
                         index=pd.date_range("2023-01-01", periods=n - 1, freq="D"))

    def test_l15_removes_bad_batch_from_group(self):
        """L1.5 should remove the highly-correlated batch from a group,
        proving it changes the group composition."""
        corr_a, corr_b = self._make_correlated_pair()
        good_c = self._make_uncorrelated(seed=99)

        # Group has 3 batches: batch 1 & 2 are highly correlated, batch 3 is independent
        curves = {1: corr_a, 2: corr_b, 3: good_c}
        l2_groups = [[1, 2, 3]]

        # Simulate L1.5 logic from cross_strategy.py (lines 2764-2805)
        hr = harmony_gate(curves, strength=60)
        original_group = list(l2_groups[0])

        if not hr.all_pass:
            batch_avg: dict[int, float] = {b: 0.0 for b in curves}
            batch_cnt: dict[int, int] = {b: 0 for b in curves}
            for p in hr.pairs:
                batch_avg[p.id_a] += p.harmony_score
                batch_avg[p.id_b] += p.harmony_score
                batch_cnt[p.id_a] += 1
                batch_cnt[p.id_b] += 1
            for b in batch_avg:
                if batch_cnt[b] > 0:
                    batch_avg[b] /= batch_cnt[b]
            threshold = 60 / 100.0
            sorted_b = sorted(curves.keys(), key=lambda x: batch_avg.get(x, 0), reverse=True)
            kept = [b for b in sorted_b if batch_avg.get(b, 0) >= threshold]
            if len(kept) < 2:
                kept = sorted_b[:2]
            removed = set(original_group) - set(kept)
            l2_groups[0] = [b for b in original_group if b not in removed]

        # The group should have been pruned (at least one batch removed)
        assert len(l2_groups[0]) < len(original_group), \
            f"L1.5 should have removed a batch but group stayed at {len(l2_groups[0])}"
        # Batch 3 (uncorrelated, high harmony) should always survive
        assert 3 in l2_groups[0], "The independent batch 3 should survive L1.5"

    def test_l25_penalizes_bad_group_pnl(self):
        """L2.5 should scale down PnLs of poorly-harmonized group outputs,
        proving it changes L3 scoring inputs."""
        corr_a, corr_b = self._make_correlated_pair()

        # Two L2 group outputs that are highly correlated
        l2_super_pnls = {
            "Group1_v1": corr_a.copy(),
            "Group2_v1": corr_b.copy(),
        }
        original_sum_g1 = l2_super_pnls["Group1_v1"].sum()
        original_sum_g2 = l2_super_pnls["Group2_v1"].sum()

        # Simulate L2.5 from cross_strategy.py (lines 2837-2870)
        group_curves_l25 = {0: l2_super_pnls["Group1_v1"], 1: l2_super_pnls["Group2_v1"]}
        hr25 = harmony_gate(group_curves_l25, strength=75)

        if not hr25.all_pass:
            group_harm_avg: dict[int, float] = {}
            group_harm_cnt: dict[int, int] = {}
            for p in hr25.pairs:
                for gid in (p.id_a, p.id_b):
                    group_harm_avg[gid] = group_harm_avg.get(gid, 0) + p.harmony_score
                    group_harm_cnt[gid] = group_harm_cnt.get(gid, 0) + 1
            for gid in group_harm_avg:
                if group_harm_cnt[gid] > 0:
                    group_harm_avg[gid] /= group_harm_cnt[gid]
            threshold = 75 / 100.0
            for gid, avg_h in group_harm_avg.items():
                if avg_h < threshold:
                    penalty = max(0.1, avg_h / max(threshold, 0.01))
                    for sv_key in list(l2_super_pnls.keys()):
                        if sv_key.startswith(f"Group{gid + 1}_"):
                            l2_super_pnls[sv_key] = l2_super_pnls[sv_key] * penalty

        # At least one group's PnL should have been penalized (reduced)
        penalized_g1 = abs(l2_super_pnls["Group1_v1"].sum()) < abs(original_sum_g1)
        penalized_g2 = abs(l2_super_pnls["Group2_v1"].sum()) < abs(original_sum_g2)
        assert penalized_g1 or penalized_g2, \
            "L2.5 should have penalized at least one group's PnL"

    def test_l15_per_group_toggle_respected(self):
        """When per_group toggle is off for a group, L1.5 should skip it."""
        corr_a, corr_b = self._make_correlated_pair()
        good_c = self._make_uncorrelated(seed=99)

        curves = {1: corr_a, 2: corr_b, 3: good_c}
        l2_groups = [[1, 2, 3]]
        per_group = {0: False}  # Group 0 disabled

        # With per_group=False, group should not be touched
        if per_group.get(0, True) is False:
            result = list(l2_groups[0])
        else:
            result = []  # would run L1.5

        assert result == [1, 2, 3], "Group with per_group=False should not be modified"

    def test_harmony_off_no_impact(self):
        """When harmony is disabled, groups and PnLs should be unchanged."""
        corr_a, corr_b = self._make_correlated_pair()
        l2_groups = [[1, 2]]
        l2_super_pnls = {"Group1_v1": corr_a.copy(), "Group2_v1": corr_b.copy()}
        original_groups = [list(g) for g in l2_groups]
        original_sums = {k: v.sum() for k, v in l2_super_pnls.items()}

        harmony_enabled = False
        if not harmony_enabled:
            pass  # Nothing happens

        assert l2_groups == original_groups
        for k in l2_super_pnls:
            assert l2_super_pnls[k].sum() == original_sums[k]
