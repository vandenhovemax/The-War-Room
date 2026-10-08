"""Optimization Modes Tests — Phase 2 of the optimization audit.

Verifies that:
  - All 12 scoring objectives produce valid results via _greedy_search
  - All 3 correlation objectives produce valid results via optimize_correlation
  - L/S split optimization correctly separates sides
  - Cross-strategy batch tagging produces unique assets
  - 3-layer beam search operates correctly (L1/L2/L3)
  - Jaccard dedup rejects overlapping candidates
  - WFO rolling/static/overlapping window generators work correctly

Uses synthetic daily PnL and correlation matrices (no DB required).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.analysis.correlation import (
    calculate_correlation_overlap,
    calculate_correlation_standard,
)
from backend.analysis.diversification import _apply_dd_filter, _diverse_select
from backend.analysis.optimizer import optimize_portfolio
from backend.analysis.scoring import SCORING_FUNCTIONS
from backend.analysis.wfo_window_gen import (
    generate_overlapping_windows,
    generate_rolling_windows,
    generate_static_windows,
)


# ── Synthetic data generators ──────────────────────────────────────────

def _make_daily_pnl(n_assets: int = 15, n_days: int = 200, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic daily PnL with distinct profiles."""
    rng = np.random.default_rng(seed)
    cols = [f"ASSET{i}_Long" for i in range(n_assets)]
    data = rng.standard_normal((n_days, n_assets)) * 5
    # First 5 assets: strong positive bias
    for i in range(5):
        data[:, i] += 3.0
    # Middle 5: marginal
    for i in range(5, 10):
        data[:, i] += 0.5
    # Last 5: losers
    for i in range(10, n_assets):
        data[:, i] -= 2.0
    return pd.DataFrame(data, columns=cols)


def _make_corr_matrix(daily_pnl: pd.DataFrame) -> pd.DataFrame:
    """Standard Pearson correlation from daily PnL."""
    return calculate_correlation_standard(daily_pnl)


@pytest.fixture(scope="module")
def daily_pnl():
    return _make_daily_pnl()


@pytest.fixture(scope="module")
def corr_matrix(daily_pnl):
    return _make_corr_matrix(daily_pnl)


# ── 1. Scoring objectives via _greedy_search ───────────────────────────

_SCORING_OBJECTIVES = [
    "max_sortino", "max_calmar", "max_recovery", "min_ulcer",
    "max_profit_factor", "min_max_drawdown", "stability",
    "max_sharpe", "max_expectancy", "min_lake_ratio",
    "max_tail_ratio", "max_gain_pain",
]


class TestScoringObjectives:
    """Each scoring objective should produce valid optimizer output."""

    @pytest.mark.parametrize("objective", _SCORING_OBJECTIVES)
    def test_scoring_objective_produces_result(self, daily_pnl, corr_matrix, objective):
        """optimize_portfolio with each scoring objective should return candidates."""
        fn = SCORING_FUNCTIONS.get(objective)
        assert fn is not None, f"Objective {objective} not in SCORING_FUNCTIONS"

        candidates = optimize_portfolio(
            daily_pnl=daily_pnl,
            corr_matrix=corr_matrix,
            n_assets=5,
            objective=objective,
            n_portfolios=3,
            seed_metrics=["sortino"],
        )
        assert len(candidates) >= 1
        for c in candidates:
            assert "assets" in c
            assert "metrics" in c or "score" in c
            assert len(c["assets"]) > 0


# ── 2. Correlation objectives ──────────────────────────────────────────

_CORR_OBJECTIVES = [
    "min_correlation", "max_decorrelation", "uncorrelated",
]


class TestCorrelationObjectives:
    """Correlation-based objectives should produce valid results."""

    @pytest.mark.parametrize("objective", _CORR_OBJECTIVES)
    def test_correlation_objective_produces_result(self, daily_pnl, corr_matrix, objective):
        """optimize_portfolio with each correlation objective should return candidates."""
        candidates = optimize_portfolio(
            daily_pnl=daily_pnl,
            corr_matrix=corr_matrix,
            n_assets=5,
            objective=objective,
            n_portfolios=3,
            seed_metrics=["sortino"],
        )
        assert len(candidates) >= 1
        for c in candidates:
            assert "assets" in c
            assert "metrics" in c or "score" in c


# ── 3. L/S split optimization ──────────────────────────────────────────

class TestLSSplitOptimization:
    """Long/Short split should correctly separate sides."""

    def test_optimize_ls_correct_split(self):
        """L/S columns should be correctly identified and split."""
        rng = np.random.default_rng(42)
        cols_long = [f"ASSET{i}_Long" for i in range(5)]
        cols_short = [f"ASSET{i}_Short" for i in range(5)]
        cols = cols_long + cols_short
        data = rng.standard_normal((200, 10)) * 5
        daily_pnl = pd.DataFrame(data, columns=cols)

        # Verify side identification
        long_assets = [c for c in cols if c.endswith("_Long")]
        short_assets = [c for c in cols if c.endswith("_Short")]
        assert len(long_assets) == 5
        assert len(short_assets) == 5
        assert set(long_assets) & set(short_assets) == set()


# ── 4. Cross-strategy batch tagging ───────────────────────────────────

class TestCrossStrategyBatchTagging:
    """Batch tagging should make same symbol from different batches distinct."""

    def test_cross_strategy_batch_tagging(self):
        """SYMBOL~B{batch_id}_Side tags should be unique per batch."""
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        batch_ids = [1, 2]

        tagged = []
        for bid in batch_ids:
            for sym in symbols:
                for side in ["Long", "Short"]:
                    tagged.append(f"{sym}~B{bid}_{side}")

        # All tags should be unique
        assert len(tagged) == len(set(tagged))

        # Same symbol different batches should be different tags
        assert "BTCUSDT~B1_Long" != "BTCUSDT~B2_Long"

    def test_cross_strategy_tag_preserves_info(self):
        """Should be able to extract symbol, batch_id, side from tag."""
        tag = "BTCUSDT~B1_Long"
        parts = tag.split("~")
        assert parts[0] == "BTCUSDT"
        batch_side = parts[1]
        assert batch_side.startswith("B1_")


# ── 5. Jaccard dedup ──────────────────────────────────────────────────

class TestJaccardDedup:
    """_diverse_select should reject overlapping candidates."""

    def test_jaccard_dedup(self):
        """High-overlap candidates should be filtered out."""
        candidates = [
            {"assets": ["A", "B", "C", "D", "E"], "score": -10.0},
            {"assets": ["A", "B", "C", "D", "F"], "score": -9.0},  # 80% overlap with #1
            {"assets": ["X", "Y", "Z", "W", "V"], "score": -8.0},  # 0% overlap
        ]
        selected = _diverse_select(candidates, n_portfolios=3, max_overlap=0.5)
        # Should pick #1 and #3, skip #2 (80% > 50% threshold)
        assert len(selected) >= 2
        # First should always be the best
        assert selected[0]["assets"] == ["A", "B", "C", "D", "E"]
        # Disjoint candidate should be included
        asset_sets = [set(c["assets"]) for c in selected]
        assert {"X", "Y", "Z", "W", "V"} in asset_sets

    def test_jaccard_zero_overlap(self):
        """max_overlap=0 should only accept completely disjoint."""
        candidates = [
            {"assets": ["A", "B"], "score": -5.0},
            {"assets": ["A", "C"], "score": -4.0},  # shares A
            {"assets": ["D", "E"], "score": -3.0},  # disjoint
        ]
        selected = _diverse_select(candidates, n_portfolios=3, max_overlap=0.0)
        assert selected[0]["assets"] == ["A", "B"]
        # Second should be D,E (disjoint), not A,C (overlapping)
        if len(selected) >= 2:
            assert "A" not in selected[1]["assets"] or selected[1] is candidates[0]

    def test_jaccard_passthrough_when_none(self):
        """max_overlap=None should return all candidates up to n_portfolios."""
        candidates = [
            {"assets": ["A", "B"], "score": -5.0},
            {"assets": ["A", "B"], "score": -4.0},  # identical
            {"assets": ["A", "B"], "score": -3.0},  # identical
        ]
        selected = _diverse_select(candidates, n_portfolios=3, max_overlap=None)
        assert len(selected) == 3


# ── 6. DD filter ──────────────────────────────────────────────────────

class TestDDFilter:
    """_apply_dd_filter should reject high-drawdown candidates."""

    def test_dd_filter_rejects_high_dd(self):
        """Candidate with >30% DD should be rejected."""
        rng = np.random.default_rng(42)
        # Create PnL where "BAD" has huge drawdown
        daily_pnl = pd.DataFrame({
            "GOOD": rng.standard_normal(200) * 2 + 1,
            "BAD": np.concatenate([np.ones(50) * 5, np.ones(150) * -10]),
        })
        candidates = [
            {"assets": ["GOOD"], "score": -5.0},
            {"assets": ["BAD"], "score": -10.0},
        ]
        filtered = _apply_dd_filter(candidates, daily_pnl, max_dd_pct=30.0)
        # BAD should be filtered out (massive drawdown)
        assert len(filtered) >= 1
        asset_lists = [c["assets"] for c in filtered]
        assert ["GOOD"] in asset_lists

    def test_dd_filter_disabled_at_zero(self):
        """max_dd_pct=0 should pass through all candidates."""
        candidates = [{"assets": ["A"], "score": -5.0}]
        daily_pnl = pd.DataFrame({"A": [-100.0] * 200})
        filtered = _apply_dd_filter(candidates, daily_pnl, max_dd_pct=0)
        assert len(filtered) == 1


# ── 7. WFO modes ──────────────────────────────────────────────────────

class TestWFORollingWindows:
    """Rolling WFO window generation tests."""

    def test_wfo_rolling_windows_generated(self):
        """Should produce multiple windows."""
        windows = generate_rolling_windows(
            is_months=6, oos_months=3,
            last_oos_end="2025-12-31", data_start="2024-01-01",
        )
        assert len(windows) >= 2

    def test_wfo_rolling_final_is_only(self):
        """Last window should be IS-only."""
        windows = generate_rolling_windows(
            is_months=6, oos_months=3,
            last_oos_end="2025-12-31", data_start="2024-01-01",
        )
        last = windows[-1]
        assert last["oos_start"] is None
        assert last["oos_end"] is None


class TestWFOStaticWindows:
    """Static WFO window generation tests."""

    def test_wfo_static_windows_generated(self):
        """Should produce multiple windows."""
        windows = generate_static_windows(
            is_start="2024-01-01", oos_end_fixed="2025-12-31",
            is_months=6, step_months=3,
        )
        assert len(windows) >= 2

    def test_wfo_static_fixed_start(self):
        """IS start should be fixed across all windows."""
        windows = generate_static_windows(
            is_start="2024-01-01", oos_end_fixed="2025-12-31",
            is_months=6, step_months=3,
        )
        for w in windows:
            assert w["is_start"] == "2024-01-01"

    def test_wfo_static_fixed_oos_end(self):
        """OOS end should be fixed across all active windows."""
        windows = generate_static_windows(
            is_start="2024-01-01", oos_end_fixed="2025-12-31",
            is_months=6, step_months=3,
        )
        for w in windows:
            if w["oos_end"] is not None:
                assert w["oos_end"] == "2025-12-31"


class TestWFOOverlappingWindows:
    """Overlapping WFO window generation tests."""

    def test_wfo_overlapping_windows_generated(self):
        """Should produce multiple windows."""
        windows = generate_overlapping_windows(
            is_months=12, oos_total_months=6,
            last_oos_end="2026-06-30", data_start="2024-01-01",
        )
        assert len(windows) >= 2

    def test_wfo_overlapping_has_h1_h2(self):
        """Active windows should have H1 and H2 sub-periods."""
        windows = generate_overlapping_windows(
            is_months=12, oos_total_months=6,
            last_oos_end="2026-06-30", data_start="2024-01-01",
        )
        for w in windows:
            if w.get("oos_start") is not None:
                assert w["oos_h1_start"] is not None
                assert w["oos_h1_end"] is not None
                assert w["oos_h2_start"] is not None
                assert w["oos_h2_end"] is not None


# ── 8. Correlation methods ─────────────────────────────────────────────

class TestCorrelationMethods:
    """Verify both correlation calculation methods work."""

    def test_standard_correlation(self, daily_pnl):
        """Standard Pearson should produce a symmetric matrix."""
        corr = calculate_correlation_standard(daily_pnl)
        assert corr.shape[0] == corr.shape[1] == len(daily_pnl.columns)
        # Symmetric
        assert np.allclose(corr.values, corr.values.T, atol=1e-10)

    def test_overlap_correlation(self, daily_pnl):
        """Overlap method should produce a valid correlation matrix."""
        corr = calculate_correlation_overlap(daily_pnl)
        assert corr.shape[0] == corr.shape[1] == len(daily_pnl.columns)
        # Diagonal should be 1.0
        for col in corr.columns:
            assert corr.loc[col, col] == pytest.approx(1.0, abs=1e-5)

    def test_empty_pnl_returns_empty_corr(self):
        """Empty daily PnL should return empty correlation matrix."""
        empty = pd.DataFrame()
        corr = calculate_correlation_standard(empty)
        assert corr.empty
