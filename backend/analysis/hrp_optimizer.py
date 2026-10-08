"""Hierarchical Risk Parity (HRP) asset selection.

Uses hierarchical clustering on the correlation matrix to find naturally
diversified groups, then assigns weights via inverse-variance recursive
bisection. Assets are ranked by HRP weight and the top N are selected.
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

from backend.analysis.scoring import calculate_stability
from backend.analysis.shared import _asset_pf  # noqa: F401

log = logging.getLogger(__name__)


def _corr_to_distance(corr_matrix: pd.DataFrame) -> np.ndarray:
  """Convert correlation to distance: d = sqrt(0.5 * (1 - corr))."""
  corr_clipped = corr_matrix.clip(-1, 1).values
  np.fill_diagonal(corr_clipped, 1.0)
  dist = np.sqrt(0.5 * (1 - corr_clipped))
  np.fill_diagonal(dist, 0.0)
  return dist


def _cluster_variance(cov: pd.DataFrame, assets: list[str]) -> float:
  """Inverse-variance portfolio variance for a cluster."""
  sub_cov = cov.loc[assets, assets]
  diag = np.diag(sub_cov).copy()
  diag[diag == 0] = 1e-10  # Clamp zero-variance to epsilon
  ivp = 1.0 / diag
  ivp_sum = ivp.sum()
  if ivp_sum == 0:
    return 1.0
  w = ivp / ivp_sum
  return float(w @ sub_cov.values @ w)


def _recursive_bisection(
  cov: pd.DataFrame, sorted_order: list[str],
) -> pd.Series:
  """HRP recursive bisection: allocate by inverse cluster variance."""
  weights = pd.Series(1.0, index=sorted_order)
  clusters = [sorted_order]

  while clusters:
    next_clusters = []
    for cluster in clusters:
      if len(cluster) <= 1:
        continue
      mid = len(cluster) // 2
      left, right = cluster[:mid], cluster[mid:]
      left_var = _cluster_variance(cov, left)
      right_var = _cluster_variance(cov, right)
      total = left_var + right_var
      alpha = 0.5 if total == 0 else 1 - left_var / total
      weights[left] *= alpha
      weights[right] *= 1 - alpha
      if len(left) > 1:
        next_clusters.append(left)
      if len(right) > 1:
        next_clusters.append(right)
    clusters = next_clusters

  return weights / weights.sum()


def _run_hrp(
  corr_matrix: pd.DataFrame,
  daily_pnl: pd.DataFrame,
  linkage_method: str = "single",
) -> pd.Series:
  """Run full HRP pipeline, return asset weights."""
  dist = _corr_to_distance(corr_matrix)
  condensed = squareform(dist, checks=False)
  condensed = np.nan_to_num(condensed, nan=0.0, posinf=1.0, neginf=0.0)
  link = linkage(condensed, method=linkage_method)
  order = leaves_list(link).tolist()
  sorted_assets = [corr_matrix.columns[i] for i in order]
  cov = daily_pnl[corr_matrix.columns].cov()
  return _recursive_bisection(cov, sorted_assets)


def hrp_select_assets(
  corr_matrix: pd.DataFrame,
  daily_pnl: pd.DataFrame,
  n_assets: int,
  n_portfolios: int = 3,
  seed_metrics: list[str] | None = None,
  max_seeds: int = 20,
  external_scores: dict | None = None,
) -> list[list[str]]:
  """HRP-based asset selection. Returns list[list[str]]."""
  assets = corr_matrix.columns.tolist()

  # Hard gate: exclude assets with PF < 1.0, then seed-rank the rest
  if daily_pnl is not None and not daily_pnl.empty:
    assets = [a for a in assets if a in daily_pnl.columns and _asset_pf(daily_pnl[a]) >= 1.0]
    if len(assets) > max_seeds:
      from backend.analysis.optimizer_seeds import compute_seed_scores
      _metrics = seed_metrics or ["sortino"]
      scores = compute_seed_scores(daily_pnl, assets, _metrics, external_scores)
      assets = sorted(assets, key=lambda a: scores.get(a, float("inf")))[:max_seeds]
    corr_matrix = corr_matrix.loc[
      [a for a in assets if a in corr_matrix.columns],
      [a for a in assets if a in corr_matrix.columns],
    ]

  if n_assets >= len(assets):
    return [assets]
  if len(assets) < 2:
    return [assets]

  # Expanded linkage methods for more diverse candidates
  all_methods = ["ward", "complete", "average", "single", "centroid", "median"]
  methods = all_methods[:max(n_portfolios, 3)]
  results: list[list[str]] = []
  seen: set[tuple[str, ...]] = set()
  rng = np.random.default_rng(42)

  prev_top_assets: list[str] = []  # for weight-exclusion re-runs

  for ci, method in enumerate(methods):
    if len(results) >= n_portfolios:
      break
    try:
      # Pool subsampling for candidates 2+: randomly drop 10%
      use_corr = corr_matrix
      use_assets = assets
      if ci > 0 and len(assets) > n_assets + 5:
        drop_n = max(1, int(len(assets) * 0.10))
        keep_idx = sorted(rng.choice(len(assets), size=len(assets) - drop_n, replace=False))
        use_assets = [assets[i] for i in keep_idx]
        use_corr = corr_matrix.loc[
          [a for a in use_assets if a in corr_matrix.columns],
          [a for a in use_assets if a in corr_matrix.columns],
        ]

      # Weight-exclusion: exclude top 3 from previous candidate
      if ci > 0 and prev_top_assets:
        exclude = set(prev_top_assets[:3])
        use_assets = [a for a in use_assets if a not in exclude]
        use_corr = use_corr.loc[
          [a for a in use_assets if a in use_corr.columns],
          [a for a in use_assets if a in use_corr.columns],
        ]

      if len(use_assets) < n_assets or len(use_corr) < 2:
        continue

      # Use only methods supported by scipy for the distance metric
      safe_method = method if method in ("single", "complete", "average", "ward") else "average"
      weights = _run_hrp(use_corr, daily_pnl, safe_method)
      top_n = weights.nlargest(n_assets).index.tolist()
      prev_top_assets = top_n  # store for next iteration's exclusion
    except Exception as exc:
      log.warning("HRP failed (%s method): %s — falling back to unranked", method, exc)
      top_n = assets[:n_assets]
    key = tuple(sorted(top_n))
    if key not in seen:
      seen.add(key)
      results.append(top_n)

  return results if results else [assets[:n_assets]]


def hrp_select_split(
  corr_matrix: pd.DataFrame,
  daily_pnl: pd.DataFrame,
  n_longs: int,
  n_shorts: int,
  n_portfolios: int = 3,
  seed_metrics: list[str] | None = None,
  max_seeds: int = 20,
  external_scores: dict | None = None,
) -> list[dict]:
  """HRP for split L/S. Returns list[dict] matching optimize_split_portfolio."""
  all_assets = corr_matrix.columns.tolist()
  long_assets = [a for a in all_assets if a.endswith("_Long")]
  short_assets = [a for a in all_assets if a.endswith("_Short")]

  # Hard gate: exclude PF < 1.0, then seed-rank the rest per direction
  if daily_pnl is not None and not daily_pnl.empty:
    long_assets = [a for a in long_assets if a in daily_pnl.columns and _asset_pf(daily_pnl[a]) >= 1.0]
    short_assets = [a for a in short_assets if a in daily_pnl.columns and _asset_pf(daily_pnl[a]) >= 1.0]
    from backend.analysis.optimizer_seeds import compute_seed_scores
    _metrics = seed_metrics or ["sortino"]
    if len(long_assets) > max_seeds:
      l_scores = compute_seed_scores(daily_pnl, long_assets, _metrics, external_scores)
      long_assets = sorted(long_assets, key=lambda a: l_scores.get(a, float("inf")))[:max_seeds]
    if len(short_assets) > max_seeds:
      s_scores = compute_seed_scores(daily_pnl, short_assets, _metrics, external_scores)
      short_assets = sorted(short_assets, key=lambda a: s_scores.get(a, float("inf")))[:max_seeds]
    # Rebuild corr_matrix for filtered pool
    filtered = long_assets + short_assets
    corr_matrix = corr_matrix.loc[
      [a for a in filtered if a in corr_matrix.columns],
      [a for a in filtered if a in corr_matrix.columns],
    ]

  if len(long_assets) < n_longs or len(short_assets) < n_shorts:
    return []

  all_methods = ["ward", "complete", "average", "single", "centroid", "median"]
  methods = all_methods[:max(n_portfolios, 3)]
  results: list[dict] = []
  seen: set[tuple[str, ...]] = set()

  for method in methods:
    if len(results) >= n_portfolios:
      break
    safe_method = method if method in ("single", "complete", "average", "ward") else "average"
    try:
      if len(long_assets) <= n_longs:
        sel_l = long_assets
      else:
        lcols = [a for a in corr_matrix.columns if a in long_assets]
        w_l = _run_hrp(corr_matrix.loc[lcols, lcols], daily_pnl, safe_method)
        sel_l = w_l.nlargest(n_longs).index.tolist()
      if len(short_assets) <= n_shorts:
        sel_s = short_assets
      else:
        scols = [a for a in corr_matrix.columns if a in short_assets]
        w_s = _run_hrp(corr_matrix.loc[scols, scols], daily_pnl, safe_method)
        sel_s = w_s.nlargest(n_shorts).index.tolist()
    except Exception as exc:
      log.warning("HRP split failed (%s method): %s — falling back to unranked", method, exc)
      sel_l = long_assets[:n_longs]
      sel_s = short_assets[:n_shorts]

    combined = sorted(sel_l) + sorted(sel_s)
    key = tuple(sorted(combined))
    if key in seen:
      continue
    seen.add(key)

    r2, score = 0.0, 0.0
    metrics: dict = {}
    valid = [c for c in combined if c in daily_pnl.columns]
    if valid:
      cpnl = daily_pnl[valid].sum(axis=1)
      r2 = calculate_stability(cpnl)
      score = 1 - r2
      cum = cpnl.cumsum()
      peak = np.maximum.accumulate(cum.values)
      max_dd = float((cum.values - peak).min())
      gp = float(cpnl[cpnl > 0].sum())
      gl = abs(float(cpnl[cpnl < 0].sum()))
      pf = min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)
      metrics = {
        "net_profit": float(cum.iloc[-1]) if not cum.empty else 0,
        "profit_factor": float(pf),
        "max_drawdown": float(max_dd),
        "stability": float(r2),
        "n_assets": len(combined),
      }

    results.append({
      "longs": sorted(sel_l), "shorts": sorted(sel_s),
      "assets": sorted(combined), "score": score, "stability_r2": r2,
      "metrics": metrics,
    })

  results.sort(key=lambda x: x["score"])
  return results[:n_portfolios]
