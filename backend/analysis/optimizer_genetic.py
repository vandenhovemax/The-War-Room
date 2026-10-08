"""Genetic Algorithm portfolio construction.

Evolves a population of portfolios over multiple generations using
selection, crossover, and mutation. Explores the search space far
more thoroughly than greedy or tournament approaches.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.scoring import SCORING_FUNCTIONS, calculate_stability
from backend.analysis.shared import _asset_pf  # noqa: F401
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def _score_portfolio(
  daily_pnl: pd.DataFrame,
  assets: list[str],
  score_fn,
) -> float:
  """Score a portfolio (lower = better)."""
  valid = [a for a in assets if a in daily_pnl.columns]
  if not valid:
    return float("inf")
  combined = daily_pnl[valid].sum(axis=1)
  return float(score_fn(combined))


def genetic_select(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_assets: int,
  seed_scores: dict[str, float],
  objective: str = "stability",
  n_portfolios: int = 3,
  n_generations: int = 50,
  population_size: int = 100,
  mutation_rate: float = 0.10,
  crossover_rate: float = 0.70,
  elite_pct: float = 0.10,
) -> list[list[str]]:
  """Genetic algorithm portfolio selection.

  1. Initialize random population of portfolios
  2. For each generation:
     a. Score all portfolios with the objective
     b. Keep elite % unchanged (elitism)
     c. Select parents via tournament selection
     d. Crossover: combine assets from two parents
     e. Mutation: randomly swap assets
  3. Return top n_portfolios diverse results.
  """
  # Filter to profitable assets
  all_assets = [a for a in daily_pnl.columns if _asset_pf(daily_pnl[a]) >= 1.0]
  if len(all_assets) <= n_assets:
    return [all_assets]

  score_fn = SCORING_FUNCTIONS.get(objective, SCORING_FUNCTIONS.get("stability"))
  rng = np.random.default_rng(42)
  n_elite = max(1, int(population_size * elite_pct))

  # Seed-ranked assets for biased initialization
  ranked = sorted(all_assets, key=lambda a: seed_scores.get(a, float("inf")))

  logger.info(
    "  genetic: %d assets, %d gen, pop=%d, mut=%.0f%%, cross=%.0f%%, elite=%d",
    len(all_assets), n_generations, population_size,
    mutation_rate * 100, crossover_rate * 100, n_elite,
  )

  # ── Initialize population ──
  # Half seeded (biased toward top-ranked), half random
  population: list[list[str]] = []
  n_seeded = population_size // 2
  for i in range(n_seeded):
    # Start from top assets, add some randomness
    top_pool = ranked[: min(n_assets * 3, len(ranked))]
    chosen = list(rng.choice(top_pool, size=min(n_assets, len(top_pool)), replace=False))
    # Fill remaining from full pool if needed
    while len(chosen) < n_assets:
      remaining = [a for a in all_assets if a not in chosen]
      if not remaining:
        break
      chosen.append(rng.choice(remaining))
    population.append(sorted(chosen))

  for _ in range(population_size - n_seeded):
    chosen = list(rng.choice(all_assets, size=min(n_assets, len(all_assets)), replace=False))
    population.append(sorted(chosen))

  # ── Evolution loop ──
  for gen in range(n_generations):
    # Score all portfolios
    scored = []
    for portfolio in population:
      s = _score_portfolio(daily_pnl, portfolio, score_fn)
      scored.append((s, portfolio))
    scored.sort(key=lambda x: x[0])

    # Elitism: keep top performers unchanged
    new_population = [p for _, p in scored[:n_elite]]

    # Fill rest of population
    while len(new_population) < population_size:
      if rng.random() < crossover_rate:
        # Crossover: pick two parents, combine their genes
        p1 = _tournament_parent(scored, rng)
        p2 = _tournament_parent(scored, rng)
        child = _crossover(p1, p2, n_assets, all_assets, rng)
      else:
        # Clone a parent
        child = list(_tournament_parent(scored, rng))

      # Mutation: randomly swap assets
      if rng.random() < mutation_rate:
        child = _mutate(child, all_assets, rng)

      new_population.append(sorted(child))

    population = new_population

  # ── Final scoring and diversity selection ──
  final_scored = []
  for portfolio in population:
    s = _score_portfolio(daily_pnl, portfolio, score_fn)
    final_scored.append((s, portfolio))
  final_scored.sort(key=lambda x: x[0])

  # Diversity filter: accept portfolios differing by >= 2 assets
  accepted: list[list[str]] = []
  for _, portfolio in final_scored:
    asset_set = set(portfolio)
    if all(len(asset_set.symmetric_difference(set(a))) >= 2 for a in accepted):
      accepted.append(portfolio)
    if len(accepted) >= n_portfolios:
      break

  if not accepted:
    accepted.append(final_scored[0][1])

  return accepted


def _tournament_parent(
  scored: list[tuple[float, list[str]]],
  rng: np.random.Generator,
  k: int = 5,
) -> list[str]:
  """Select a parent via tournament selection (pick best of k random)."""
  k = min(k, len(scored))
  indices = rng.choice(len(scored), size=k, replace=False)
  best_idx = min(indices, key=lambda i: scored[i][0])
  return scored[best_idx][1]


def _crossover(
  p1: list[str],
  p2: list[str],
  n_assets: int,
  all_assets: list[str],
  rng: np.random.Generator,
) -> list[str]:
  """Uniform crossover: each gene comes from either parent."""
  combined = list(dict.fromkeys(p1 + p2))  # union, deduplicated
  if len(combined) <= n_assets:
    return combined[:n_assets]

  # Each asset: 50% chance from p1, 50% from p2, prioritize shared
  shared = set(p1) & set(p2)
  child = list(shared)

  # Fill from remaining parent assets randomly
  remaining = [a for a in combined if a not in shared]
  rng.shuffle(remaining)
  child.extend(remaining)

  return child[:n_assets]


def _mutate(
  portfolio: list[str],
  all_assets: list[str],
  rng: np.random.Generator,
) -> list[str]:
  """Swap 1-2 random assets with ones not in the portfolio."""
  portfolio = list(portfolio)
  available = [a for a in all_assets if a not in portfolio]
  if not available:
    return portfolio

  n_swaps = min(rng.integers(1, 3), len(portfolio), len(available))
  swap_out = list(rng.choice(len(portfolio), size=n_swaps, replace=False))
  swap_in = list(rng.choice(available, size=n_swaps, replace=False))

  for i, idx in enumerate(swap_out):
    portfolio[idx] = swap_in[i]

  return portfolio


# ── Split L/S version ──

def genetic_select_split(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_longs: int,
  n_shorts: int,
  seed_scores: dict[str, float],
  objective: str = "stability",
  n_portfolios: int = 3,
  n_generations: int = 50,
  population_size: int = 100,
  mutation_rate: float = 0.10,
  crossover_rate: float = 0.70,
  elite_pct: float = 0.10,
) -> list[dict]:
  """Genetic algorithm for split L/S mode."""
  long_cols = [c for c in daily_pnl.columns if c.endswith("_Long")]
  short_cols = [c for c in daily_pnl.columns if c.endswith("_Short")]

  l_corr = corr_matrix.loc[
    [c for c in long_cols if c in corr_matrix.columns],
    [c for c in long_cols if c in corr_matrix.columns],
  ] if long_cols else corr_matrix
  s_corr = corr_matrix.loc[
    [c for c in short_cols if c in corr_matrix.columns],
    [c for c in short_cols if c in corr_matrix.columns],
  ] if short_cols else corr_matrix

  long_results = genetic_select(
    daily_pnl[long_cols] if long_cols else daily_pnl,
    l_corr, n_longs, seed_scores, objective, n_portfolios,
    n_generations, population_size, mutation_rate, crossover_rate, elite_pct,
  )
  short_results = genetic_select(
    daily_pnl[short_cols] if short_cols else daily_pnl,
    s_corr, n_shorts, seed_scores, objective, n_portfolios,
    n_generations, population_size, mutation_rate, crossover_rate, elite_pct,
  )

  results = []
  for i in range(min(len(long_results), len(short_results), n_portfolios)):
    sl = long_results[i] if i < len(long_results) else long_results[0]
    ss = short_results[i] if i < len(short_results) else short_results[0]
    combined = sorted(sl) + sorted(ss)
    valid = [c for c in combined if c in daily_pnl.columns]
    if valid:
      cpnl = daily_pnl[valid].sum(axis=1)
      stab = float(calculate_stability(cpnl))
      cum = cpnl.cumsum()
      peak = np.maximum.accumulate(cum.values)
      max_dd = float((cum.values - peak).min())
      gp = float(cpnl[cpnl > 0].sum())
      gl = abs(float(cpnl[cpnl < 0].sum()))
      pf = min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)
      results.append({
        "longs": sorted(sl), "shorts": sorted(ss),
        "assets": sorted(combined), "score": 1 - stab,
        "stability_r2": stab,
        "metrics": {
          "net_profit": float(cum.iloc[-1]) if not cum.empty else 0,
          "profit_factor": float(pf),
          "max_drawdown": float(max_dd),
          "stability": stab,
          "n_assets": len(combined),
        },
      })

  return results if results else []
