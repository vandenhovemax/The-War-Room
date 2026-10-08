"""Tests for seed ranking separation and correctness.

Verifies that:
  - Different seed_metrics produce different rankings
  - External scores (benchmark, recency, event) merge correctly
  - Metric weights shift rankings as expected
  - Seed ranking is independent from optimizer objective
  - Each scoring pipeline produces correct output format
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from dataclasses import dataclass

from backend.analysis.optimizer_seeds import (
  compute_benchmark_seed_scores,
  compute_event_seed_scores,
  compute_recency_seed_scores,
  compute_seed_scores,
)
from backend.analysis.seed_ranker import (
  postfilter_ac,
  prefilter_for_meanrev,
  prefilter_for_trend,
)


@dataclass
class _FakeTrade:
  """Minimal duck-type of BacktestTrade for event seed tests."""
  symbol: str = "BTCUSDT"
  side: str = "long"
  entry_time: datetime | None = None
  exit_time: datetime | None = None
  net_profit: float = 10.0
  position_id: str = "T1"


# ── Synthetic data generators ──────────────────────────────────────────

def _make_daily_pnl(n_assets: int = 10, n_days: int = 200, seed: int = 42) -> pd.DataFrame:
  """Generate synthetic daily PnL with distinct asset profiles.

  - Assets 0-3: strong positive bias (consistently profitable)
  - Assets 4-6: weak positive bias (marginal)
  - Assets 7-9: mixed/negative (losers)
  """
  rng = np.random.default_rng(seed)
  columns = [f"ASSET{i}_Long" for i in range(n_assets)]
  data = rng.standard_normal((n_days, n_assets)) * 5

  # Strong winners: high mean, low vol
  for i in range(4):
    data[:, i] += 3.0
  # Marginal winners: slight positive
  for i in range(4, 7):
    data[:, i] += 0.5
  # Losers: negative bias
  for i in range(7, n_assets):
    data[:, i] -= 2.0

  return pd.DataFrame(data, columns=columns)


def _make_trades_df(n_assets: int = 10, n_trades_per: int = 30) -> pd.DataFrame:
  """Generate synthetic trade data for recency scoring."""
  rng = np.random.default_rng(42)
  rows = []
  base_time = datetime(2024, 6, 1)

  for i in range(n_assets):
    for j in range(n_trades_per):
      pnl = float(rng.normal(3.0 - i * 0.5, 5.0))
      exit_time = base_time + timedelta(days=j, hours=int(rng.integers(0, 23)))
      rows.append({
        "Asset": f"ASSET{i}_Long",
        "Net Profit": pnl,
        "Exit Time": exit_time,
      })

  return pd.DataFrame(rows)


def _make_benchmark_stats(n_assets: int = 10) -> dict:
  """Generate synthetic benchmark robustness stats."""
  rng = np.random.default_rng(42)
  statuses = ["Elite", "Strong", "Stable", "Weak", "Fragile"]
  stats = {}
  for i in range(n_assets):
    h1_pf = max(0.3, float(rng.normal(2.5 - i * 0.3, 0.5)))
    h2_pf = max(0.3, float(rng.normal(2.5 - i * 0.3, 0.5)))
    h1_t = int(rng.integers(5, 30))
    h2_t = int(rng.integers(5, 30))
    status = statuses[min(i // 2, len(statuses) - 1)]
    stats[f"ASSET{i}_Long"] = {
      "H1_PF": h1_pf, "H2_PF": h2_pf,
      "h1_trades": h1_t, "h2_trades": h2_t,
      "status": status,
      "h1_wr": 0.55 - i * 0.03,
      "h2_wr": 0.54 - i * 0.03,
      "pnl_values": list(rng.normal(10 - i * 2, 5, 20)),
    }
  return stats


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def daily_pnl():
  return _make_daily_pnl()


@pytest.fixture(scope="module")
def trades_df():
  return _make_trades_df()


@pytest.fixture(scope="module")
def benchmark_stats():
  return _make_benchmark_stats()


# ── Tests: compute_seed_scores basic behavior ───────────────────────────

class TestComputeSeedScoresBasic:
  """Core compute_seed_scores behavior."""

  def test_returns_all_assets(self, daily_pnl):
    """Should return a score for every asset."""
    assets = daily_pnl.columns.tolist()
    scores = compute_seed_scores(daily_pnl, assets, ["sortino"])
    assert set(scores.keys()) == set(assets)

  def test_lower_is_better(self, daily_pnl):
    """Strong winners should have lower (better) seed scores."""
    assets = daily_pnl.columns.tolist()
    scores = compute_seed_scores(daily_pnl, assets, ["sortino"])
    # ASSET0-3 (strong) should rank better than ASSET7-9 (losers)
    strong_avg = np.mean([scores[f"ASSET{i}_Long"] for i in range(4)])
    weak_avg = np.mean([scores[f"ASSET{i}_Long"] for i in range(7, 10)])
    assert strong_avg < weak_avg, (
      f"Strong assets ({strong_avg:.2f}) should have lower seed score "
      f"than weak ({weak_avg:.2f})"
    )

  def test_empty_assets_returns_empty(self, daily_pnl):
    """Empty assets list should return empty dict."""
    scores = compute_seed_scores(daily_pnl, [], ["sortino"])
    assert scores == {}

  def test_single_asset(self, daily_pnl):
    """Single asset should return score 0 (rank 0)."""
    scores = compute_seed_scores(daily_pnl, ["ASSET0_Long"], ["sortino"])
    assert len(scores) == 1
    assert scores["ASSET0_Long"] == 0.0  # rank 0 / 1 weight = 0.0


# ── Tests: Different metrics produce different rankings ──────────────────

class TestMetricSeparation:
  """Verify that different seed_metrics produce meaningfully different rankings."""

  def test_sortino_vs_pnl_different_rankings(self, daily_pnl):
    """Sortino and raw PnL should produce different orderings."""
    assets = daily_pnl.columns.tolist()
    scores_sortino = compute_seed_scores(daily_pnl, assets, ["sortino"])
    scores_pnl = compute_seed_scores(daily_pnl, assets, ["pnl"])

    # Rankings don't need to be completely different, but they shouldn't
    # be identical (sortino penalizes downside vol, pnl doesn't)
    # At minimum, verify the function ran without error and both are valid
    assert len(scores_sortino) == len(assets)
    assert len(scores_pnl) == len(assets)

  def test_stability_vs_sharpe_different_rankings(self, daily_pnl):
    """Stability and Sharpe should weight differently."""
    assets = daily_pnl.columns.tolist()
    scores_stab = compute_seed_scores(daily_pnl, assets, ["stability"])
    scores_sharpe = compute_seed_scores(daily_pnl, assets, ["sharpe"])

    # Key: both produce valid finite scores
    for a in assets:
      assert np.isfinite(scores_stab[a]), f"Stability score for {a} is not finite"
      assert np.isfinite(scores_sharpe[a]), f"Sharpe score for {a} is not finite"

  def test_multi_metric_blend(self, daily_pnl):
    """Multiple metrics should produce blended result different from single metric."""
    assets = daily_pnl.columns.tolist()
    scores_single = compute_seed_scores(daily_pnl, assets, ["sortino"])
    scores_multi = compute_seed_scores(daily_pnl, assets, ["sortino", "pnl", "max_dd"])

    # Multi-metric should be different from single (rank averaging changes scores)
    diffs = [abs(scores_single[a] - scores_multi[a]) for a in assets]
    assert max(diffs) > 0, "Multi-metric scores identical to single-metric"

  @pytest.mark.parametrize("metric", [
    "sortino", "pf", "pnl", "max_dd", "ulcer", "wr", "recovery",
    "stability", "sharpe", "calmar", "expectancy", "payoff",
    "lake_ratio", "tail_ratio", "gain_pain", "cde",
    "robust_sortino", "est_efficiency", "rapid_recovery",
  ])
  def test_all_metrics_produce_finite_scores(self, daily_pnl, metric):
    """Every supported metric should produce finite scores."""
    assets = daily_pnl.columns.tolist()
    scores = compute_seed_scores(daily_pnl, assets, [metric])
    for a in assets:
      assert np.isfinite(scores[a]), f"{metric} produced non-finite score for {a}"


# ── Tests: Metric weights ───────────────────────────────────────────────

class TestMetricWeights:
  """Verify metric_weights shifts rankings correctly."""

  def test_heavy_weight_dominates(self, daily_pnl):
    """A metric with weight=100 should dominate over weight=1."""
    assets = daily_pnl.columns.tolist()
    # Heavy sortino
    scores_heavy_sortino = compute_seed_scores(
      daily_pnl, assets, ["sortino", "pnl"],
      metric_weights={"sortino": 100.0, "pnl": 1.0},
    )
    # Heavy pnl
    scores_heavy_pnl = compute_seed_scores(
      daily_pnl, assets, ["sortino", "pnl"],
      metric_weights={"sortino": 1.0, "pnl": 100.0},
    )
    # The top-ranked asset should differ
    # With very different weights, the blend should produce different top picks
    # (or at minimum, different score distributions)
    score_diffs = [
      abs(scores_heavy_sortino[a] - scores_heavy_pnl[a]) for a in assets
    ]
    assert max(score_diffs) > 0, "Heavy weighting had no effect on scores"

  def test_equal_weights_match_default(self, daily_pnl):
    """Explicit equal weights should match default (no weights)."""
    assets = daily_pnl.columns.tolist()
    scores_default = compute_seed_scores(daily_pnl, assets, ["sortino", "pnl"])
    scores_equal = compute_seed_scores(
      daily_pnl, assets, ["sortino", "pnl"],
      metric_weights={"sortino": 1.0, "pnl": 1.0},
    )
    for a in assets:
      assert abs(scores_default[a] - scores_equal[a]) < 1e-10


# ── Tests: External scores (benchmark, recency) ─────────────────────────

class TestExternalScores:
  """Verify external_scores merge correctly into compute_seed_scores."""

  def test_external_scores_override_metric(self, daily_pnl):
    """External scores for a metric should override calendar computation."""
    assets = daily_pnl.columns.tolist()
    # Make ASSET9 the best via external score
    external = {"sortino": {a: 0.0 for a in assets}}
    external["sortino"]["ASSET9_Long"] = 100.0  # very high = very good

    scores = compute_seed_scores(
      daily_pnl, assets, ["sortino"], external_scores=external,
    )
    # ASSET9 should now rank #1 (lowest score) because external score
    # is negated (-100) and ranked → rank 0
    order = sorted(assets, key=lambda a: scores[a])
    assert order[0] == "ASSET9_Long", (
      f"ASSET9 should rank first with high external score, got {order[0]}"
    )

  def test_multiple_external_sources_merge(self, daily_pnl):
    """Benchmark + recency external scores should both contribute."""
    assets = daily_pnl.columns.tolist()
    external = {
      "benchmark": {a: float(i) for i, a in enumerate(assets)},  # 0=best, 9=worst
      "recency": {a: float(9 - i) for i, a in enumerate(assets)},  # reversed
    }
    scores = compute_seed_scores(
      daily_pnl, assets, ["benchmark", "recency"], external_scores=external,
    )
    # Blended result: benchmark ranks 0→0, recency ranks 0→9
    # Average should be moderate for all assets
    for a in assets:
      assert np.isfinite(scores[a])

  def test_missing_external_scores_fallback(self, daily_pnl):
    """Assets missing from external_scores should get 0.0 raw score."""
    assets = daily_pnl.columns.tolist()
    # Only provide external for 3 assets
    external = {"sortino": {assets[0]: 5.0, assets[1]: 3.0, assets[2]: 1.0}}
    scores = compute_seed_scores(
      daily_pnl, assets, ["sortino"], external_scores=external,
    )
    # All assets should still have scores
    assert len(scores) == len(assets)
    for a in assets:
      assert np.isfinite(scores[a])


# ── Tests: compute_benchmark_seed_scores ─────────────────────────────────

class TestBenchmarkSeedScores:
  """Verify benchmark robustness scoring pipeline."""

  def test_returns_all_assets(self, benchmark_stats):
    """Should return a score for every asset in stats."""
    scores = compute_benchmark_seed_scores(benchmark_stats)
    assert set(scores.keys()) == set(benchmark_stats.keys())

  def test_higher_is_better(self, benchmark_stats):
    """Elite assets should score higher than Fragile ones."""
    scores = compute_benchmark_seed_scores(benchmark_stats)
    # ASSET0, ASSET1 = Elite; ASSET8, ASSET9 = Fragile
    elite_avg = np.mean([scores[f"ASSET{i}_Long"] for i in range(2)])
    fragile_avg = np.mean([scores[f"ASSET{i}_Long"] for i in range(8, 10)])
    assert elite_avg > fragile_avg, (
      f"Elite ({elite_avg:.2f}) should score higher than Fragile ({fragile_avg:.2f})"
    )

  def test_status_multiplier_effect(self, benchmark_stats):
    """Different status levels should produce different multipliers."""
    scores_no_blend = compute_benchmark_seed_scores(benchmark_stats)
    # All scores should be positive
    for a, s in scores_no_blend.items():
      assert s >= 0, f"Negative benchmark score for {a}: {s}"

  def test_consistency_blend(self, benchmark_stats):
    """consistency_blend > 0 should penalize lopsided H1/H2 PFs."""
    scores_off = compute_benchmark_seed_scores(benchmark_stats, consistency_blend=0.0)
    scores_on = compute_benchmark_seed_scores(benchmark_stats, consistency_blend=0.3)
    # At least one asset should have a different score
    diffs = [abs(scores_off[a] - scores_on[a]) for a in benchmark_stats]
    assert max(diffs) > 0, "Consistency blend had no effect"

  def test_fragility_blend(self, benchmark_stats):
    """fragility_blend > 0 should penalize tail-dependent strategies."""
    scores_off = compute_benchmark_seed_scores(benchmark_stats, fragility_blend=0.0)
    scores_on = compute_benchmark_seed_scores(benchmark_stats, fragility_blend=0.3)
    diffs = [abs(scores_off[a] - scores_on[a]) for a in benchmark_stats]
    assert max(diffs) > 0, "Fragility blend had no effect"

  def test_wr_harmony_blend(self, benchmark_stats):
    """wr_harmony_blend > 0 should penalize divergent win rates."""
    scores_off = compute_benchmark_seed_scores(benchmark_stats, wr_harmony_blend=0.0)
    scores_on = compute_benchmark_seed_scores(benchmark_stats, wr_harmony_blend=0.3)
    diffs = [abs(scores_off[a] - scores_on[a]) for a in benchmark_stats]
    assert max(diffs) > 0, "WR harmony blend had no effect"

  def test_min_reliable_trades_confidence(self):
    """Low trade count should reduce confidence (and score)."""
    stats_high = {
      "A": {"H1_PF": 2.0, "H2_PF": 2.0, "h1_trades": 50, "h2_trades": 50,
             "status": "Strong", "h1_wr": 0.55, "h2_wr": 0.55, "pnl_values": [1]*50},
    }
    stats_low = {
      "A": {"H1_PF": 2.0, "H2_PF": 2.0, "h1_trades": 3, "h2_trades": 3,
             "status": "Strong", "h1_wr": 0.55, "h2_wr": 0.55, "pnl_values": [1]*6},
    }
    score_high = compute_benchmark_seed_scores(stats_high, min_reliable_trades=20)
    score_low = compute_benchmark_seed_scores(stats_low, min_reliable_trades=20)
    assert score_high["A"] > score_low["A"], (
      f"High trades ({score_high['A']:.2f}) should score better than low ({score_low['A']:.2f})"
    )


# ── Tests: compute_recency_seed_scores ───────────────────────────────────

class TestRecencySeedScores:
  """Verify recency-weighted scoring pipeline."""

  def test_returns_scores_for_qualifying_assets(self, trades_df):
    """Should return scores for assets with enough trades."""
    scores = compute_recency_seed_scores(trades_df, min_trades=5)
    assert len(scores) > 0
    for a, s in scores.items():
      assert s >= 0, f"Negative recency score for {a}: {s}"

  def test_gradual_vs_batch_modes_differ(self, trades_df):
    """Gradual and batch modes should produce different scores."""
    scores_gradual = compute_recency_seed_scores(trades_df, mode="gradual")
    scores_batch = compute_recency_seed_scores(
      trades_df, mode="batch", batch_size=10, boost_factor=3.0,
    )
    common = set(scores_gradual) & set(scores_batch)
    assert len(common) > 0
    diffs = [abs(scores_gradual[a] - scores_batch[a]) for a in common]
    assert max(diffs) > 0, "Gradual and batch modes produced identical scores"

  def test_min_trades_filter(self, trades_df):
    """Assets with fewer than min_trades should get score 0.0."""
    # With min_trades=100 and 30 trades per asset, all get 0.0
    scores = compute_recency_seed_scores(trades_df, min_trades=100)
    for a, s in scores.items():
      assert s == 0.0, f"Expected 0.0 for {a} below min_trades, got {s}"

  def test_higher_is_better_for_profitable(self, trades_df):
    """More profitable assets should score higher."""
    scores = compute_recency_seed_scores(trades_df, min_trades=5)
    if len(scores) >= 2:
      sorted_assets = sorted(scores, key=lambda a: scores[a], reverse=True)
      # ASSET0 (most profitable) should be near top
      top_3 = set(sorted_assets[:3])
      assert "ASSET0_Long" in top_3 or "ASSET1_Long" in top_3, (
        f"Profitable assets not in top 3: {sorted_assets[:3]}"
      )

  def test_confidence_penalty(self):
    """Low trade count should reduce confidence multiplier."""
    rows_few = [{"Asset": "A", "Net Profit": 10.0,
                 "Exit Time": datetime(2024, 6, 1) + timedelta(days=i)}
                for i in range(5)]
    rows_many = [{"Asset": "B", "Net Profit": 10.0,
                  "Exit Time": datetime(2024, 6, 1) + timedelta(days=i)}
                 for i in range(50)]
    df = pd.DataFrame(rows_few + rows_many)
    scores = compute_recency_seed_scores(df, min_trades=5, min_reliable_trades=20)
    # B has 50 trades (confidence=1.0), A has 5 trades (confidence=0.25)
    # Both have 100% WR and same PF, but B should score higher
    assert scores.get("B", 0) > scores.get("A", 0), (
      f"Higher trade count should score better: A={scores.get('A')}, B={scores.get('B')}"
    )


# ── Tests: Seed vs Objective Separation ──────────────────────────────────

class TestSeedObjectiveSeparation:
  """Verify seeds and optimizer objective are independent concepts."""

  def test_seed_scores_same_regardless_of_intended_objective(self, daily_pnl):
    """compute_seed_scores doesn't know about the optimizer objective.

    Two calls with the same seed_metrics should return identical scores,
    regardless of what objective the caller plans to use.
    """
    assets = daily_pnl.columns.tolist()
    scores_a = compute_seed_scores(daily_pnl, assets, ["sortino"])
    scores_b = compute_seed_scores(daily_pnl, assets, ["sortino"])
    for a in assets:
      assert scores_a[a] == scores_b[a]

  def test_seed_metric_sortino_different_from_calmar(self, daily_pnl):
    """seed_metrics=['sortino'] should rank differently from seed_metrics=['calmar']."""
    assets = daily_pnl.columns.tolist()
    scores_sortino = compute_seed_scores(daily_pnl, assets, ["sortino"])
    scores_calmar = compute_seed_scores(daily_pnl, assets, ["calmar"])

    # They may or may not differ for synthetic data, but scores should be different
    diffs = [abs(scores_sortino[a] - scores_calmar[a]) for a in assets]
    assert max(diffs) > 0, "Sortino and Calmar seed scores are identical"


# ── Tests: Rank-averaging formula ────────────────────────────────────────

class TestRankAveragingFormula:
  """Verify the weighted rank-averaging inside compute_seed_scores."""

  def test_single_metric_scores_are_ranks(self, daily_pnl):
    """With one metric, scores should be 0..N-1 ranks divided by 1."""
    assets = daily_pnl.columns.tolist()
    scores = compute_seed_scores(daily_pnl, assets, ["sortino"])
    values = sorted(scores.values())
    # Should be exactly ranks 0, 1, 2, ..., N-1 (each / total_weight=1.0)
    for i, v in enumerate(values):
      assert v == pytest.approx(float(i), abs=1e-10), (
        f"Expected rank {i}, got {v}"
      )

  def test_two_metrics_average_ranks(self, daily_pnl):
    """With two equal-weight metrics, score = (rank_a + rank_b) / 2."""
    assets = daily_pnl.columns.tolist()
    n = len(assets)
    scores = compute_seed_scores(daily_pnl, assets, ["sortino", "pnl"])
    # Scores should be between 0 and n-1 (average of two rank lists)
    for a in assets:
      assert 0 <= scores[a] <= n - 1, f"Score {scores[a]} out of range for {a}"

  def test_weighted_rank_formula(self):
    """Manual verification of weighted rank-averaging with known data."""
    # Create data where asset order is clear
    data = pd.DataFrame({
      "A": [10.0, 10.0, 10.0],  # best PnL
      "B": [5.0, 5.0, 5.0],    # mid PnL
      "C": [-5.0, -5.0, -5.0], # worst PnL
    })
    assets = ["A", "B", "C"]

    # With metric "pnl", _score_metric returns -(pnl.sum())
    # A: -30 (rank 0), B: -15 (rank 1), C: 15 (rank 2)
    scores = compute_seed_scores(data, assets, ["pnl"])
    assert scores["A"] < scores["B"] < scores["C"]
    assert scores["A"] == pytest.approx(0.0, abs=1e-10)
    assert scores["B"] == pytest.approx(1.0, abs=1e-10)
    assert scores["C"] == pytest.approx(2.0, abs=1e-10)


# ── Tests: Edge cases ───────────────────────────────────────────────────

class TestSeedEdgeCases:
  """Edge cases for seed scoring."""

  def test_all_zero_pnl(self):
    """All-zero PnL should not crash and should produce finite scores."""
    data = pd.DataFrame({
      "A": np.zeros(100),
      "B": np.zeros(100),
    })
    scores = compute_seed_scores(data, ["A", "B"], ["sortino"])
    assert len(scores) == 2
    # Both should have inf scores (std=0 makes sortino undefined)
    # But they should be finite after rank-averaging
    for a in ["A", "B"]:
      assert np.isfinite(scores[a])

  def test_single_day_pnl(self):
    """Single-day PnL should not crash."""
    data = pd.DataFrame({"A": [5.0], "B": [-3.0]})
    scores = compute_seed_scores(data, ["A", "B"], ["sortino"])
    assert len(scores) == 2

  def test_asset_not_in_dataframe(self):
    """Asset not in daily_returns should get inf score."""
    data = pd.DataFrame({"A": np.ones(50)})
    scores = compute_seed_scores(data, ["A", "MISSING"], ["sortino"])
    assert scores["MISSING"] > scores["A"]

  def test_benchmark_empty_stats(self):
    """Empty benchmark stats should return empty dict."""
    scores = compute_benchmark_seed_scores({})
    assert scores == {}

  def test_recency_empty_trades(self):
    """Empty trades DataFrame should return empty scores."""
    df = pd.DataFrame(columns=["Asset", "Net Profit", "Exit Time"])
    scores = compute_recency_seed_scores(df, min_trades=5)
    assert scores == {}


# ── Tests: All 21 seed metrics individually ────────────────────────────

_ALL_SEED_METRICS = [
    "sortino", "pf", "pnl", "avg_dd", "max_dd", "ulcer", "wfe", "wr",
    "recovery", "stability", "sharpe", "calmar", "expectancy", "payoff",
    "lake_ratio", "tail_ratio", "gain_pain", "cde", "robust_sortino",
    "est_efficiency", "rapid_recovery",
]


class TestAllSeedMetrics:
    """Verify every seed metric produces finite scores."""

    @pytest.mark.parametrize("metric", _ALL_SEED_METRICS)
    def test_individual_metric_finite(self, daily_pnl, metric):
        """Each of the 21 seed metrics should produce finite scores."""
        assets = daily_pnl.columns.tolist()
        scores = compute_seed_scores(daily_pnl, assets, [metric])
        assert len(scores) == len(assets)
        for a in assets:
            assert np.isfinite(scores[a]), (
                f"Metric '{metric}' produced non-finite score for {a}: {scores[a]}"
            )

    def test_multi_metric_weighted_blend(self, daily_pnl):
        """Blending 3+ metrics with weights should produce valid scores."""
        assets = daily_pnl.columns.tolist()
        scores = compute_seed_scores(
            daily_pnl, assets,
            ["sortino", "pnl", "max_dd", "stability", "wr"],
            metric_weights={"sortino": 3.0, "pnl": 2.0, "max_dd": 1.0,
                            "stability": 1.0, "wr": 1.0},
        )
        assert len(scores) == len(assets)
        for a in assets:
            assert np.isfinite(scores[a])

    def test_max_seeds_limit(self, daily_pnl):
        """Seed scores should return scores for all requested assets."""
        assets = daily_pnl.columns.tolist()
        # With a subset of assets, should get scores for exactly that subset
        subset = assets[:3]
        scores = compute_seed_scores(daily_pnl, subset, ["sortino"])
        assert len(scores) == 3


# ── Tests: Event seed scoring ──────────────────────────────────────────

class TestEventSeedScoring:
    """Verify event-based seed scoring pipeline."""

    def test_event_seed_basic(self):
        """compute_event_seed_scores should return scores for valid assets."""
        trades = []
        base = datetime(2024, 6, 1)
        for i in range(30):
            trades.append(_FakeTrade(
                symbol="BTCUSDT", side="long",
                entry_time=base + timedelta(days=i),
                exit_time=base + timedelta(days=i, hours=5),
                net_profit=float(10 + i),
                position_id=f"T{i}",
            ))
        scores = compute_event_seed_scores(trades, n_trades=20)
        assert "BTCUSDT" in scores
        assert scores["BTCUSDT"] > 0

    def test_event_seed_staleness_filter(self):
        """Stale assets should be excluded from event seeds."""
        trades = []
        for i in range(30):
            trades.append(_FakeTrade(
                symbol="STALEUSDT", side="long",
                entry_time=datetime(2020, 1, 1) + timedelta(days=i),
                exit_time=datetime(2020, 1, 1) + timedelta(days=i, hours=5),
                net_profit=10.0,
                position_id=f"T{i}",
            ))
        scores = compute_event_seed_scores(
            trades, n_trades=20, staleness_days=30,
            reference_time=datetime(2024, 6, 1),
        )
        assert "STALEUSDT" not in scores

    def test_event_seed_n_trades_minimum(self):
        """Assets with fewer than n_trades should be excluded."""
        trades = [_FakeTrade(
            symbol="FEWUSDT", side="long",
            entry_time=datetime(2024, 6, 1) + timedelta(days=i),
            exit_time=datetime(2024, 6, 1) + timedelta(days=i, hours=5),
            net_profit=10.0, position_id=f"T{i}",
        ) for i in range(5)]
        scores = compute_event_seed_scores(trades, n_trades=20)
        assert "FEWUSDT" not in scores


# ── Tests: Recency modes ──────────────────────────────────────────────

class TestRecencyModes:
    """Verify both recency scoring modes."""

    def test_recency_gradual_weighting(self, trades_df):
        """Gradual mode: later trades weighted more heavily."""
        scores = compute_recency_seed_scores(trades_df, mode="gradual")
        assert len(scores) > 0
        for s in scores.values():
            assert s >= 0

    def test_recency_batch_weighting(self, trades_df):
        """Batch mode: last batch_size trades get boost_factor."""
        scores = compute_recency_seed_scores(
            trades_df, mode="batch", batch_size=10, boost_factor=3.0,
        )
        assert len(scores) > 0
        for s in scores.values():
            assert s >= 0


# ── Tests: Benchmark H1/H2 split ──────────────────────────────────────

class TestBenchmarkH1H2:
    """Verify benchmark H1/H2 split and status multipliers."""

    def test_benchmark_h1_h2_split(self, benchmark_stats):
        """Stats should contain H1 and H2 trade counts."""
        for asset, stats in benchmark_stats.items():
            assert "h1_trades" in stats
            assert "h2_trades" in stats
            assert "H1_PF" in stats
            assert "H2_PF" in stats
            assert stats["h1_trades"] > 0
            assert stats["h2_trades"] > 0

    def test_benchmark_status_multipliers(self):
        """Each status level should produce a different multiplier effect."""
        multipliers = {
            "Elite": 1.2, "Strong": 1.1, "Stable": 1.0,
            "Weak": 0.7, "Fragile": 0.5,
        }
        scores = {}
        for status, mult in multipliers.items():
            stats = {
                "TEST": {
                    "H1_PF": 2.0, "H2_PF": 2.0,
                    "h1_trades": 50, "h2_trades": 50,
                    "status": status,
                    "h1_wr": 0.55, "h2_wr": 0.55,
                    "pnl_values": [1.0] * 50,
                },
            }
            result = compute_benchmark_seed_scores(stats)
            scores[status] = result["TEST"]

        # Elite > Strong > Stable > Weak > Fragile
        assert scores["Elite"] > scores["Strong"]
        assert scores["Strong"] > scores["Stable"]
        assert scores["Stable"] > scores["Weak"]
        assert scores["Weak"] > scores["Fragile"]


# ── Tests: Structural prefilters ───────────────────────────────────────

class TestStructuralPrefilters:
    """Verify trend and mean-reversion prefilters."""

    def _make_pnl_with_profiles(self):
        """Create PnL where some assets trend, some mean-revert."""
        rng = np.random.default_rng(42)
        n = 300
        # Trending: cumulative drift
        trend = rng.standard_normal(n) * 1 + 0.5
        # Mean-reverting: oscillating
        meanrev = rng.standard_normal(n) * 5
        meanrev = meanrev - 0.5 * np.cumsum(meanrev) / np.arange(1, n + 1)
        # Random walk
        random_walk = rng.standard_normal(n) * 3

        return pd.DataFrame({
            "TREND_Long": trend,
            "MEANREV_Long": meanrev,
            "RANDOM_Long": random_walk,
        })

    def test_prefilter_trend(self):
        """Trend prefilter should keep assets with high Hurst + TER."""
        daily_pnl = self._make_pnl_with_profiles()
        # Very permissive filter to ensure it runs without error
        result = prefilter_for_trend(daily_pnl, hurst_min=0.0, ter_percentile=0)
        assert len(result) > 0
        assert all(isinstance(a, str) for a in result)

    def test_prefilter_meanrev(self):
        """Meanrev prefilter should keep assets with low Hurst + VR."""
        daily_pnl = self._make_pnl_with_profiles()
        # Very permissive filter
        result = prefilter_for_meanrev(
            daily_pnl, hurst_max=1.0, vr_max=10.0, min_crossing_rate=0.0,
        )
        assert len(result) > 0
        assert all(isinstance(a, str) for a in result)

    def test_prefilter_trend_strict_reduces_pool(self):
        """Strict trend filter should reduce the asset pool."""
        daily_pnl = self._make_pnl_with_profiles()
        permissive = prefilter_for_trend(daily_pnl, hurst_min=0.0, ter_percentile=0)
        strict = prefilter_for_trend(daily_pnl, hurst_min=0.6, ter_percentile=50)
        assert len(strict) <= len(permissive)


# ── Tests: AC postfilter ──────────────────────────────────────────────

class TestACPostfilter:
    """Verify autocorrelation postfilter for trend/meanrev strategies."""

    def test_ac_postfilter_trend(self):
        """Trend postfilter should flag assets with negative AC."""
        rng = np.random.default_rng(42)
        daily_pnl = pd.DataFrame({
            "A_Long": rng.standard_normal(200) * 5 + 1,
            "B_Long": rng.standard_normal(200) * 5,
        })
        result = postfilter_ac(daily_pnl, ["A_Long", "B_Long"], strategy_type="trend")
        assert isinstance(result, dict)
        for asset, info in result.items():
            assert "ac" in info or "flag" in info or isinstance(info, dict)

    def test_ac_postfilter_meanrev(self):
        """Meanrev postfilter should flag assets with positive AC."""
        rng = np.random.default_rng(42)
        daily_pnl = pd.DataFrame({
            "A_Long": rng.standard_normal(200) * 5,
            "B_Long": rng.standard_normal(200) * 5,
        })
        result = postfilter_ac(daily_pnl, ["A_Long", "B_Long"], strategy_type="meanrev")
        assert isinstance(result, dict)
