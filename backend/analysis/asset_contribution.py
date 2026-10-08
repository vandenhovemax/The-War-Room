"""Per-asset leave-one-out marginal contribution analysis.

Computes how much each asset helps or hurts the portfolio by
temporarily removing it and measuring the impact on key metrics.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from backend.analysis.scoring import calculate_stability
from backend.analysis.shared import _asset_pf  # noqa: F401


def _portfolio_sortino(pnl: pd.Series) -> float:
  """Sortino ratio (positive = better)."""
  if pnl.empty or len(pnl) < 2:
    return 0.0
  mean_ret = pnl.mean()
  downside = pnl[pnl < 0]
  if downside.empty:
    return abs(mean_ret) * 100 if mean_ret > 0 else 0.0
  dd = float(np.sqrt(np.mean(downside.values ** 2)))
  return float(mean_ret / dd) if dd > 0 else 0.0


def _max_drawdown(pnl: pd.Series) -> float:
  """Max drawdown (negative number)."""
  if pnl.empty:
    return 0.0
  cum = pnl.cumsum()
  peak = np.maximum.accumulate(cum.values)
  return float((cum.values - peak).min())


def compute_asset_marginal_contribution(
  portfolio_assets: list[str],
  daily_pnl: pd.DataFrame,
  score_fn: Callable | None = None,
) -> list[dict]:
  """Per-asset leave-one-out contribution analysis.

  For each asset in portfolio_assets:
    1. Compute portfolio metrics WITH the asset
    2. Compute portfolio metrics WITHOUT the asset
    3. Delta = with - without (positive delta = asset helps)

  Returns list of dicts sorted by contribution_score descending.
  """
  if not portfolio_assets:
    return []

  valid = [a for a in portfolio_assets if a in daily_pnl.columns]
  if not valid:
    return []

  # Full portfolio metrics
  full_pnl = daily_pnl[valid].sum(axis=1)
  full_pf = _asset_pf(full_pnl)
  full_stab = float(calculate_stability(full_pnl))
  full_sortino = _portfolio_sortino(full_pnl)
  full_net = float(full_pnl.sum())
  full_dd = _max_drawdown(full_pnl)

  results = []
  for asset in valid:
    remaining = [a for a in valid if a != asset]
    if not remaining:
      # Single asset portfolio — removing it zeroes everything
      results.append({
        "asset": asset,
        "pf_delta": full_pf,
        "stability_delta": full_stab,
        "sortino_delta": full_sortino,
        "net_profit_delta": full_net,
        "dd_delta": -full_dd,  # removing improves DD to 0
        "contribution_score": full_net,  # sole contributor
        "verdict": "helpful",
      })
      continue

    # Metrics WITHOUT this asset
    sans_pnl = daily_pnl[remaining].sum(axis=1)
    sans_pf = _asset_pf(sans_pnl)
    sans_stab = float(calculate_stability(sans_pnl))
    sans_sortino = _portfolio_sortino(sans_pnl)
    sans_net = float(sans_pnl.sum())
    sans_dd = _max_drawdown(sans_pnl)

    # Deltas: positive = removing this asset HURTS (asset is helpful)
    pf_delta = full_pf - sans_pf
    stab_delta = full_stab - sans_stab
    sortino_delta = full_sortino - sans_sortino
    net_delta = full_net - sans_net
    dd_delta = full_dd - sans_dd  # negative if removing improves DD

    # Composite contribution score (weighted)
    # Normalize each delta by scale, then average
    # Higher = more helpful
    contribution = (
      0.30 * (pf_delta / max(full_pf, 0.01))
      + 0.25 * stab_delta
      + 0.25 * (sortino_delta / max(abs(full_sortino), 0.01))
      + 0.20 * (net_delta / max(abs(full_net), 0.01))
    )

    if contribution > 0.02:
      verdict = "helpful"
    elif contribution < -0.02:
      verdict = "harmful"
    else:
      verdict = "neutral"

    results.append({
      "asset": asset,
      "pf_delta": round(pf_delta, 4),
      "stability_delta": round(stab_delta, 4),
      "sortino_delta": round(sortino_delta, 4),
      "net_profit_delta": round(net_delta, 2),
      "dd_delta": round(dd_delta, 2),
      "contribution_score": round(contribution, 4),
      "verdict": verdict,
    })

  # Sort by contribution_score descending (most helpful first)
  results.sort(key=lambda x: x["contribution_score"], reverse=True)
  return results
