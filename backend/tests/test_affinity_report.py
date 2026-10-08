"""Tests for the Batch Affinity Report engine."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.analysis.affinity_report import (
    AffinityReport,
    PairMetrics,
    _m_return_correlation,
    _m_correlation_stability,
    _m_drawdown_overlap,
    _m_stress_divergence,
    _m_complementarity,
    _m_recovery_offset,
    _m_shape_diversity,
    compute_pair_metrics,
    compute_all_pairs,
    classify_roles,
    find_clusters,
    validate_oos,
    generate_affinity_report,
)


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_pnl(seed: int, n: int = 500, trend: float = 0.5) -> pd.Series:
    rng = np.random.RandomState(seed)
    return pd.Series(
        rng.normal(trend, 10, n),
        index=pd.date_range("2023-01-01", periods=n, freq="D"),
    )

def _make_correlated(n: int = 500) -> tuple[pd.Series, pd.Series]:
    rng = np.random.RandomState(42)
    base = rng.normal(0.5, 10, n)
    noise = rng.normal(0, 1, n)
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.Series(base, index=idx), pd.Series(base + noise, index=idx)

def _make_anticorrelated(n: int = 500) -> tuple[pd.Series, pd.Series]:
    rng = np.random.RandomState(42)
    base = rng.normal(0.5, 10, n)
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.Series(base, index=idx), pd.Series(-base + rng.normal(0, 1, n), index=idx)


# ── Individual metric tests ─────────────────────────────────────────────

class TestReturnCorrelation:
    def test_correlated_low_score(self):
        a, b = _make_correlated()
        score = _m_return_correlation(a, b)
        assert score < 0.1  # highly correlated → low score

    def test_uncorrelated_mid_score(self):
        a = _make_pnl(1)
        b = _make_pnl(99)
        score = _m_return_correlation(a, b)
        assert 0.3 < score < 0.7

    def test_anticorrelated_high_score(self):
        a, b = _make_anticorrelated()
        score = _m_return_correlation(a, b)
        assert score > 0.9


class TestCorrelationStability:
    def test_stable_high_score(self):
        a, b = _make_correlated()
        score = _m_correlation_stability(a, b)
        assert score > 0.5  # stable correlation

    def test_short_series(self):
        a = pd.Series([1, 2, 3])
        b = pd.Series([4, 5, 6])
        score = _m_correlation_stability(a, b)
        assert score == 0.5  # default for short series


class TestDrawdownOverlap:
    def test_correlated_high_overlap(self):
        a, b = _make_correlated()
        score = _m_drawdown_overlap(a, b)
        # Correlated series overlap in drawdowns → low score
        assert score < 0.6

    def test_independent_less_overlap(self):
        a = _make_pnl(1)
        b = _make_pnl(99)
        score = _m_drawdown_overlap(a, b)
        assert score > 0.3


class TestStressDivergence:
    def test_anticorrelated_high_score(self):
        a, b = _make_anticorrelated()
        score = _m_stress_divergence(a, b)
        assert score > 0.6

    def test_correlated_low_score(self):
        a, b = _make_correlated()
        score = _m_stress_divergence(a, b)
        assert score < 0.4


class TestComplementarity:
    def test_anticorrelated_high(self):
        a, b = _make_anticorrelated()
        score = _m_complementarity(a, b)
        assert score > 0.7  # opposite signs most of the time

    def test_correlated_low(self):
        a, b = _make_correlated()
        score = _m_complementarity(a, b)
        assert score < 0.4


class TestRecoveryOffset:
    def test_correlated_low_offset(self):
        a, b = _make_correlated()
        score = _m_recovery_offset(a, b)
        assert score < 0.3  # synchronized DDs

    def test_anticorrelated_high_offset(self):
        a, b = _make_anticorrelated()
        score = _m_recovery_offset(a, b)
        assert score > 0.7


class TestShapeDiversity:
    def test_identical_low_diversity(self):
        a, b = _make_correlated()
        score = _m_shape_diversity(a, b)
        assert score < 0.3

    def test_different_profiles(self):
        rng = np.random.RandomState(42)
        n = 500
        idx = pd.date_range("2023-01-01", periods=n, freq="D")
        # Steady grinder vs spiky
        a = pd.Series(rng.normal(0.5, 2, n), index=idx)
        b = pd.Series(rng.normal(0.0, 20, n), index=idx)
        score = _m_shape_diversity(a, b)
        assert score > 0.2


# ── Composite tests ─────────────────────────────────────────────────────

class TestComputePairMetrics:
    def test_returns_pair_metrics(self):
        a = _make_pnl(1)
        b = _make_pnl(2)
        pm = compute_pair_metrics(a, b, 1, 2)
        assert isinstance(pm, PairMetrics)
        assert 0 <= pm.composite_score <= 1.0
        assert pm.id_a == 1
        assert pm.id_b == 2

    def test_to_dict(self):
        a = _make_pnl(1)
        b = _make_pnl(2)
        pm = compute_pair_metrics(a, b, 1, 2)
        d = pm.to_dict()
        assert "return_correlation" in d
        assert "composite_score" in d


class TestComputeAllPairs:
    def test_correct_count(self):
        curves = {1: _make_pnl(1), 2: _make_pnl(2), 3: _make_pnl(3)}
        pairs = compute_all_pairs(curves)
        assert len(pairs) == 3  # C(3,2) = 3

    def test_five_batches(self):
        curves = {i: _make_pnl(i * 10) for i in range(5)}
        pairs = compute_all_pairs(curves)
        assert len(pairs) == 10  # C(5,2) = 10


# ── Role classification tests ──────────────────────────────────────────

class TestClassifyRoles:
    def test_identifies_anchor(self):
        curves = {1: _make_pnl(1), 2: _make_pnl(2), 3: _make_pnl(3)}
        pairs = compute_all_pairs(curves)
        roles = classify_roles(pairs, [1, 2, 3])
        anchors = [r for r in roles if r.role == "anchor"]
        assert len(anchors) == 1

    def test_identifies_redundant(self):
        a, b = _make_correlated()
        c = _make_pnl(99)
        curves = {1: a, 2: b, 3: c}
        pairs = compute_all_pairs(curves)
        roles = classify_roles(pairs, [1, 2, 3])
        redundant = [r for r in roles if r.role == "redundant"]
        # Highly correlated 1 & 2 should produce at least one redundant
        assert len(redundant) >= 1


# ── Clustering tests ────────────────────────────────────────────────────

class TestFindClusters:
    def test_returns_clusters(self):
        curves = {1: _make_pnl(1), 2: _make_pnl(2), 3: _make_pnl(3)}
        pairs = compute_all_pairs(curves)
        clusters = find_clusters(pairs, [1, 2, 3])
        assert len(clusters) >= 1
        all_batches = [b for c in clusters for b in c.batch_ids]
        assert set(all_batches) == {1, 2, 3}


# ── OOS validation tests ───────────────────────────────────────────────

class TestOOSValidation:
    def test_validates_pairs(self):
        is_curves = {1: _make_pnl(1), 2: _make_pnl(2)}
        oos_curves = {1: _make_pnl(11), 2: _make_pnl(22)}
        is_pairs = compute_all_pairs(is_curves)
        oos_pairs = compute_all_pairs(oos_curves)
        validations, reliability = validate_oos(is_pairs, oos_pairs)
        assert len(validations) == 7  # 1 pair × 7 metrics
        assert len(reliability) == 7
        for v in validations:
            assert v.status in ("certified", "degraded", "improved", "spurious")


# ── Full report tests ───────────────────────────────────────────────────

class TestGenerateAffinityReport:
    def test_basic_report(self):
        curves = {i: _make_pnl(i * 10) for i in range(1, 6)}
        report = generate_affinity_report(curves)
        assert isinstance(report, AffinityReport)
        assert len(report.pairs) == 10  # C(5,2)
        assert len(report.roles) == 5
        assert len(report.clusters) >= 1
        assert report.recommendation
        assert "anchor" in report.recommendation

    def test_with_oos(self):
        is_curves = {1: _make_pnl(1), 2: _make_pnl(2), 3: _make_pnl(3)}
        oos_curves = {1: _make_pnl(11), 2: _make_pnl(22), 3: _make_pnl(33)}
        report = generate_affinity_report(is_curves, oos_curves)
        assert report.oos_validations is not None
        assert report.metric_reliability is not None
        assert len(report.oos_validations) == 21  # 3 pairs × 7 metrics

    def test_to_dict(self):
        curves = {1: _make_pnl(1), 2: _make_pnl(2)}
        report = generate_affinity_report(curves)
        d = report.to_dict()
        assert "pairs" in d
        assert "roles" in d
        assert "clusters" in d
        assert "metric_leaderboard" in d
        assert "recommendation" in d

    def test_two_batches_minimum(self):
        curves = {1: _make_pnl(1)}
        report = generate_affinity_report(curves)
        assert "error" in report.recommendation
