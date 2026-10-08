"""PCA (Principal Component Analysis) asset selection.

Decomposes asset returns into principal components (independent risk factors),
clusters assets by their factor loadings, and selects the best representative
from each cluster to build a maximally diversified portfolio.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage

from backend.analysis.optimizer_seeds import compute_seed_scores
from backend.analysis.scoring import calculate_stability
from backend.analysis.shared import _asset_pf  # noqa: F401


def _pca_decompose(daily_pnl: pd.DataFrame, variance_threshold: float = 0.90):
  """Eigendecomposition of covariance matrix.

  Returns (loadings, eigenvalues, n_components_kept).
  """
  cov = daily_pnl.cov().values
  cov = np.nan_to_num(cov, nan=0.0)
  eigenvalues, eigenvectors = np.linalg.eigh(cov)

  idx = np.argsort(eigenvalues)[::-1]
  eigenvalues = np.maximum(eigenvalues[idx], 0)
  eigenvectors = eigenvectors[:, idx]

  total_var = eigenvalues.sum()
  if total_var == 0:
    return eigenvectors, eigenvalues, len(eigenvalues)

  cumulative = np.cumsum(eigenvalues) / total_var
  n_comp = max(2, int(np.searchsorted(cumulative, variance_threshold) + 1))
  n_comp = min(n_comp, len(eigenvalues))

  loadings = eigenvectors[:, :n_comp] * np.sqrt(eigenvalues[:n_comp])
  return loadings, eigenvalues, n_comp


def _cluster_by_loadings(loadings: np.ndarray, n_clusters: int) -> np.ndarray:
  """Hierarchical clustering on PCA loadings. Returns 1-indexed labels."""
  if loadings.shape[0] <= n_clusters:
    return np.arange(1, loadings.shape[0] + 1)

  norms = np.linalg.norm(loadings, axis=1, keepdims=True)
  norms = np.where(norms == 0, 1, norms)
  normed = loadings / norms

  link = linkage(normed, method="ward")
  return fcluster(link, t=n_clusters, criterion="maxclust")


def pca_select_assets(
  daily_pnl: pd.DataFrame,
  n_assets: int,
  n_portfolios: int = 3,
  seed_metrics: list[str] | None = None,
  max_seeds: int = 20,
  external_scores: dict | None = None,
) -> list[list[str]]:
  """PCA-based asset selection. Returns list[list[str]]."""
  assets = daily_pnl.columns.tolist()

  # Hard gate: exclude PF < 1.0, then seed-rank the rest
  assets = [a for a in assets if _asset_pf(daily_pnl[a]) >= 1.0]
  if len(assets) > max_seeds:
    _metrics = seed_metrics or ["sortino"]
    scores = compute_seed_scores(daily_pnl, assets, _metrics, external_scores)
    assets = sorted(assets, key=lambda a: scores.get(a, float("inf")))[:max_seeds]
  daily_pnl = daily_pnl[assets] if assets else daily_pnl

  if n_assets >= len(assets):
    return [assets]
  if len(assets) < 2:
    return [assets]

  _metrics = seed_metrics or ["sortino"]
  scores = compute_seed_scores(daily_pnl, assets, _metrics)

  # Expanded configurations for more diverse candidates
  configs: list[dict] = [
    {"variance_threshold": 0.90, "pick_rank": 0},  # Candidate 1: 90% var, best per cluster
    {"variance_threshold": 0.85, "explicit_clusters": max(2, n_assets // 2), "pick_rank": 1},  # 2nd best per cluster
    {"variance_threshold": 0.95, "pick_rank": 0},  # Candidate 3: 95% var, best per cluster
    {"variance_threshold": 0.80, "explicit_clusters": max(2, n_assets // 3), "pick_rank": 0},
  ]

  results: list[list[str]] = []
  seen: set[tuple[str, ...]] = set()
  rng = np.random.default_rng(42)

  for ci, cfg in enumerate(configs[:max(n_portfolios, 3)]):
    if len(results) >= n_portfolios:
      break
    try:
      # Pool subsampling for candidates 2+
      use_pnl = daily_pnl
      use_assets = assets
      if ci > 0 and len(assets) > n_assets + 5:
        drop_n = max(1, int(len(assets) * 0.10))
        keep_idx = sorted(rng.choice(len(assets), size=len(assets) - drop_n, replace=False))
        use_assets = [assets[i] for i in keep_idx]
        use_pnl = daily_pnl[use_assets]

      loadings, _, n_comp = _pca_decompose(use_pnl, cfg.get("variance_threshold", 0.90))

      if "explicit_clusters" in cfg:
        n_clusters = min(cfg["explicit_clusters"], len(use_assets))
      else:
        n_clusters = min(n_assets, max(2, n_comp))

      labels = _cluster_by_loadings(loadings, n_clusters)
      pick_rank = cfg.get("pick_rank", 0)

      selected: list[str] = []
      for cid in sorted(set(labels)):
        members = [use_assets[i] for i in range(len(use_assets)) if labels[i] == cid]
        members.sort(key=lambda a: scores.get(a, float("inf")))
        # Pick nth-best from cluster (0 = best, 1 = second-best, etc.)
        pick_idx = min(pick_rank, len(members) - 1)
        selected.append(members[pick_idx])

      if len(selected) < n_assets:
        remaining = [a for a in use_assets if a not in selected]
        remaining.sort(key=lambda a: scores.get(a, float("inf")))
        selected.extend(remaining[: n_assets - len(selected)])

      selected = selected[:n_assets]
    except Exception:
      selected = sorted(assets, key=lambda a: scores.get(a, float("inf")))[:n_assets]

    key = tuple(sorted(selected))
    if key not in seen:
      seen.add(key)
      results.append(selected)

  return results if results else [assets[:n_assets]]


def pca_select_split(
  daily_pnl: pd.DataFrame,
  n_longs: int,
  n_shorts: int,
  n_portfolios: int = 3,
  seed_metrics: list[str] | None = None,
  max_seeds: int = 20,
  external_scores: dict | None = None,
) -> list[dict]:
  """PCA for split L/S. Returns list[dict] matching optimize_split_portfolio."""
  all_assets = daily_pnl.columns.tolist()
  long_assets = [a for a in all_assets if a.endswith("_Long")]
  short_assets = [a for a in all_assets if a.endswith("_Short")]

  # Hard gate: exclude PF < 1.0, then seed-rank the rest per direction
  long_assets = [a for a in long_assets if a in daily_pnl.columns and _asset_pf(daily_pnl[a]) >= 1.0]
  short_assets = [a for a in short_assets if a in daily_pnl.columns and _asset_pf(daily_pnl[a]) >= 1.0]
  _metrics = seed_metrics or ["sortino"]
  if len(long_assets) > max_seeds:
    l_scores = compute_seed_scores(daily_pnl, long_assets, _metrics, external_scores)
    long_assets = sorted(long_assets, key=lambda a: l_scores.get(a, float("inf")))[:max_seeds]
  if len(short_assets) > max_seeds:
    s_scores = compute_seed_scores(daily_pnl, short_assets, _metrics, external_scores)
    short_assets = sorted(short_assets, key=lambda a: s_scores.get(a, float("inf")))[:max_seeds]
  # Rebuild daily_pnl for filtered pool
  filtered = long_assets + short_assets
  daily_pnl = daily_pnl[[c for c in filtered if c in daily_pnl.columns]]

  if len(long_assets) < n_longs or len(short_assets) < n_shorts:
    return []

  _metrics = seed_metrics or ["sortino"]
  thresholds = [0.85, 0.90, 0.95][:n_portfolios]
  results: list[dict] = []
  seen: set[tuple[str, ...]] = set()

  for thresh in thresholds:
    try:
      lcols = [c for c in daily_pnl.columns if c in long_assets]
      scols = [c for c in daily_pnl.columns if c in short_assets]

      if len(lcols) <= n_longs:
        sel_l = lcols
      else:
        lp = pca_select_assets(daily_pnl[lcols], n_longs, 1, _metrics)
        sel_l = lp[0] if lp else lcols[:n_longs]

      if len(scols) <= n_shorts:
        sel_s = scols
      else:
        sp = pca_select_assets(daily_pnl[scols], n_shorts, 1, _metrics)
        sel_s = sp[0] if sp else scols[:n_shorts]
    except Exception:
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
