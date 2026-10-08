"""Speed regression tests for portfolio optimization.

Ensures optimization completes within acceptable time bounds — a backstop
against catastrophic slowdowns (infinite loops, accidental O(n³)), not a
tight benchmark.

NOTE (2026-10 audit): thresholds were originally tuned for the
batch-vectorized fast path. That path was deliberately reverted in
85f60e97f — _batch_score_candidates/_batch_fast_stability produce subtly
different FP values that cascade through greedy search into worse
portfolios. The default fast_mode=False path is correct-but-slower, so
thresholds are sized to it (observed ~172s for hybrid+stability on a slow
dev box) with generous headroom. If the batch path is ever fixed and
re-enabled, these can be tightened again.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from backend.analysis.correlation import get_correlation_matrix
from backend.analysis.optimizer import optimize_portfolio


# ── Synthetic data generator ────────────────────────────────────────────

def _make_daily_pnl(n_assets: int = 80, n_days: int = 500) -> pd.DataFrame:
  """Generate synthetic daily PnL data.

  70% of assets have a positive bias (slight edge).
  """
  rng = np.random.default_rng(42)
  columns = [
    f"ASSET{i}_Long" if i % 2 == 0 else f"ASSET{i}_Short"
    for i in range(n_assets)
  ]
  data = rng.standard_normal((n_days, n_assets)) * 5
  for i in range(int(n_assets * 0.7)):
    data[:, i] += 2.0
  return pd.DataFrame(data, columns=columns)


@pytest.fixture(scope="module")
def daily_pnl():
  return _make_daily_pnl(80, 500)


@pytest.fixture(scope="module")
def corr_matrix(daily_pnl):
  return get_correlation_matrix(daily_pnl, "standard")


# ── Speed tests ─────────────────────────────────────────────────────────

class TestOptimizerSpeed:

  def test_correlation_matrix_speed(self, daily_pnl):
    """Correlation matrix for 80 assets should be fast."""
    start = time.perf_counter()
    corr = get_correlation_matrix(daily_pnl, "standard")
    elapsed = time.perf_counter() - start

    assert corr.shape == (80, 80)
    assert elapsed < 5, f"Correlation matrix took {elapsed:.1f}s, expected <5s"

  def test_optimize_hybrid_stability_speed(self, daily_pnl, corr_matrix):
    """Hybrid objective + stability on the correct (non-batch) path."""
    start = time.perf_counter()
    result = optimize_portfolio(
      daily_pnl, corr_matrix, n_assets=30, objective="hybrid",
      n_portfolios=3, max_seeds=35, prioritize_stability=True,
    )
    elapsed = time.perf_counter() - start

    assert len(result) >= 1
    assert elapsed < 300, (
      f"optimize_portfolio (hybrid+stability) took {elapsed:.1f}s, expected <300s"
    )

  def test_greedy_search_sortino_speed(self, daily_pnl, corr_matrix):
    """max_sortino greedy search should complete in <60s."""
    start = time.perf_counter()
    result = optimize_portfolio(
      daily_pnl, corr_matrix, n_assets=30, objective="max_sortino",
      n_portfolios=3, max_seeds=35,
    )
    elapsed = time.perf_counter() - start

    assert len(result) >= 1
    assert elapsed < 60, (
      f"optimize_portfolio (max_sortino) took {elapsed:.1f}s, expected <60s"
    )

  @pytest.mark.parametrize("objective", [
    "max_sortino", "max_calmar", "max_recovery", "min_ulcer",
    "max_profit_factor", "min_max_drawdown", "max_sharpe",
    "stability", "max_robust_sortino", "max_rapid_recovery",
  ])
  def test_objective_speed(self, daily_pnl, corr_matrix, objective):
    """Every scoring objective should complete on the correct path."""
    start = time.perf_counter()
    result = optimize_portfolio(
      daily_pnl, corr_matrix, n_assets=30, objective=objective,
      n_portfolios=3, max_seeds=20,
    )
    elapsed = time.perf_counter() - start

    assert len(result) >= 1
    assert elapsed < 120, f"{objective} took {elapsed:.1f}s, expected <120s"

  def test_beam_optimizer_speed(self, daily_pnl):
    """Beam optimizer should complete in <120s for 80 assets."""
    from backend.analysis.beam_optimizer import run_beam_optimization

    cols = daily_pnl.columns.tolist()
    asset_batch = {col: i % 3 for i, col in enumerate(cols)}

    start = time.perf_counter()
    result = run_beam_optimization(
      daily_pnl, asset_batch,
      l2_groups=[[0, 1, 2]],
      n_per_batch=15,
      obj_l1="hybrid", obj_l2="stability", obj_l3="hybrid",
      k_l1=5, k_l2=3, k_l3=3,
      corr_method="standard",
      seed_metrics=["sortino"],
      max_seeds=20,
      prioritize_stability=True,
      external_scores=None,
    )
    elapsed = time.perf_counter() - start

    assert elapsed < 120, (
      f"Beam optimizer took {elapsed:.1f}s, expected <120s"
    )

  def test_diversify_portfolio_speed(self, daily_pnl, corr_matrix):
    """diversify_portfolio on the correct (non-batch) path."""
    from backend.analysis.diversification import diversify_portfolio

    start = time.perf_counter()
    result = diversify_portfolio(
      "greedy", daily_pnl, corr_matrix, n_assets=30, objective="hybrid",
      n_portfolios=3, max_seeds=35, prioritize_stability=True,
    )
    elapsed = time.perf_counter() - start

    assert len(result) >= 1
    assert elapsed < 300, (
      f"diversify_portfolio took {elapsed:.1f}s, expected <300s"
    )
