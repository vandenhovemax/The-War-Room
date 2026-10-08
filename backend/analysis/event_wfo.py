"""Event-Based Walk Forward Asset Selection engine.

Trade-by-trade simulation: walks chronologically through every closed trade,
evaluates each asset on its last N trades, selects a Top_K portfolio, and
records whether the current trade's asset was in that portfolio. The composite
equity curve proves whether the selection logic is predictive.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import numpy as np
import pandas as pd

from backend.analysis.batch_selection_scoring import score_metric
from backend.analysis.daily_pnl import _asset_key
from backend.analysis.scoring import ulcer_index
from backend.db.models import BacktestTrade

# Metrics where lower = better (for ranking: sort ascending)
_LOWER_IS_BETTER = {"max_dd", "ulcer"}


def _compute_event_metrics(pnl_list: list[float], days_span: float = 0, include_components: bool = False) -> dict:
  """Compute metrics from a short trade PnL list (5-200 values).

  All numpy, no DataFrames. Returns dict with metric values where
  higher = better for most metrics (except max_dd and ulcer).

  days_span: calendar days covered by trades (for Calmar annualization).
             0 = unknown, falls back to raw recovery.
  """
  arr = np.array(pnl_list, dtype=np.float64)
  n = len(arr)
  if n == 0:
    return {
      "net_profit": 0, "pf": 0, "wr": 0, "max_dd": 0,
      "sortino": 0, "recovery": 0, "ulcer": 0, "calmar": 0,
      "total_trades": 0,
    }

  net = float(arr.sum())
  gp = float(arr[arr > 0].sum()) if np.any(arr > 0) else 0.0
  gl = float(np.abs(arr[arr < 0]).sum()) if np.any(arr < 0) else 0.0
  pf = min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)
  wins = int(np.sum(arr > 0))
  wr = wins / n * 100

  # Equity curve for drawdown-based metrics
  equity = np.cumsum(arr)
  peak = np.maximum.accumulate(equity)
  dd = equity - peak
  max_dd = float(np.abs(dd.min())) if dd.min() < 0 else 0.0

  # Sortino: mean / downside_dev where downside_dev = sqrt(mean(d²))
  mean_pnl = float(arr.mean())
  downside = arr[arr < 0]
  downside_dev = float(np.sqrt(np.mean(downside ** 2))) if len(downside) > 0 else 0.0
  if downside_dev > 0:
    sortino = mean_pnl / downside_dev
  else:
    sortino = abs(mean_pnl) if mean_pnl > 0 else 0.0

  # Recovery: net_profit / max_dd
  recovery = net / max_dd if max_dd > 0 else (abs(net) if net > 0 else 0.0)

  # Calmar: annualized_return / max_dd (differentiated from raw recovery)
  if max_dd > 0 and days_span > 0:
    ann_return = net * (365.25 / days_span)
    calmar = ann_return / max_dd
  elif max_dd > 0:
    calmar = recovery  # fallback when no time info
  else:
    calmar = abs(net) if net > 0 else 0.0

  # Ulcer index: RMS of drawdowns on one reference scale (see ulcer_index)
  ulcer = ulcer_index(equity, peak)

  result = {
    "net_profit": round(net, 2), "pf": round(pf, 4),
    "wr": round(wr, 1), "max_dd": round(max_dd, 2),
    "sortino": round(sortino, 4), "recovery": round(recovery, 4),
    "ulcer": round(ulcer, 4), "calmar": round(calmar, 4),
    "total_trades": n,
  }

  # Always include a placeholder components field (will be populated by callers if needed)
  if include_components:
    result["components"] = None  # Will be populated by ranking logic

  return result


def _window_aligned_risk_metrics(window_pnls: list[float]) -> dict:
  """Compute sortino / calmar / recovery / ulcer from per-window PnL aggregates.

  Phase 1 (`wfo_aggregation.py`) computes these ratios on per-OOS-window PnL
  aggregates. Phase 2's `_compute_event_metrics(agg_selected_pnls)` operates
  on per-trade PnL, which has much higher noise → artificially low ratios
  and non-comparable numbers between Phase 1 and Phase 2 for the same run.

  This helper recomputes the four risk-based metrics on the same per-window
  series Phase 1 uses so the two values measure the same thing.

  Formulas match `wfo_aggregation.py` conventions exactly:
    sortino  = mean_pnl / sqrt(sum(neg²) / n_neg)            on window pnls
    recovery = total_pnl / max_window_equity_drawdown
    calmar   = recovery (unannualised, matches `_compute_event_metrics` default)
    ulcer    = sqrt(mean(drawdown_pct²)) on window equity curve
  """
  import math as _math

  n = len(window_pnls)
  if n < 2:
    return {}

  mean_pnl = sum(window_pnls) / n
  total = sum(window_pnls)

  neg = [p for p in window_pnls if p < 0]
  if neg:
    dd_dev = _math.sqrt(sum(p * p for p in neg) / len(neg))
    sortino = (mean_pnl / dd_dev) if dd_dev > 0 else 0.0
  else:
    sortino = abs(mean_pnl) if mean_pnl > 0 else 0.0

  equity: list[float] = []
  cum = 0.0
  for p in window_pnls:
    cum += p
    equity.append(cum)
  peaks: list[float] = []
  peak = 0.0
  for e in equity:
    if e > peak:
      peak = e
    peaks.append(peak)
  dd_series = [e - pk for e, pk in zip(equity, peaks)]
  max_dd_win = abs(min(dd_series)) if dd_series and min(dd_series) < 0 else 0.0

  if max_dd_win > 0:
    recovery = total / max_dd_win
  else:
    recovery = abs(total) if total > 0 else 0.0
  calmar = recovery

  # Shared reference scale. The old "0 when peak <= 0" rule gave a curve that
  # never made money a perfect ulcer of 0 (the best score, lower = better).
  ulcer = ulcer_index(np.array(equity), np.array(peaks))

  return {
    "sortino": round(sortino, 4),
    "recovery": round(recovery, 4),
    "calmar": round(calmar, 4),
    "ulcer": round(ulcer, 4),
  }


def _rank_assets(
  asset_metrics: dict[str, dict], objective: str,
) -> list[str]:
  """Rank assets by metric (higher = better, except max_dd/ulcer)."""
  reverse = objective not in _LOWER_IS_BETTER
  return sorted(
    asset_metrics.keys(),
    key=lambda a: asset_metrics[a].get(objective, 0),
    reverse=reverse,
  )


_DEFAULT_COMPOSITE_WEIGHTS = {
  "sortino": 0.40, "pf": 0.25, "recovery": 0.20, "wr": 0.15,
}


def _normalize_for_composite(name: str, value: float) -> float:
  """Normalize a single metric to a comparable scale for composite blending.

  Each metric has its own typical range. We normalize to roughly [0, 1]
  so weights are meaningful across different metric combinations.
  """
  if name == "sortino":
    return min(max(value, 0), 5.0) / 5.0
  elif name == "pf":
    return min(max(value, 0), 5.0) / 5.0
  elif name == "recovery":
    return min(max(value, 0), 10.0) / 10.0
  elif name == "wr":
    return value / 100.0
  elif name == "calmar":
    return min(max(value, 0), 10.0) / 10.0
  elif name == "net_profit":
    return max(value, 0)  # raw — no universal normalization
  elif name == "max_dd":
    # Lower is better; invert via diminishing function (0 DD → 1.0)
    return 1.0 / (1.0 + max(value, 0) / 100.0)
  elif name == "ulcer":
    # Lower is better; 0 → 1.0, 50+ → ~0
    return max(1.0 - max(value, 0) / 50.0, 0.0)
  else:
    return max(value, 0)


def _composite_score(m: dict, weights: dict) -> tuple[float, dict]:
  """Blend N normalized metrics into a single composite score.

  Supports any metric from _compute_event_metrics(). Each metric is
  normalized to a comparable scale before applying its weight.

  Returns (score, components_dict) where components_dict contains:
    "raw": {metric_name: raw_value, ...}
    "normalized": {metric_name: normalized_value, ...}
    "weighted": {metric_name: contribution, ...}
    "final_score": float
  """
  components = {"raw": {}, "normalized": {}, "weighted": {}}
  score = 0.0
  for metric_name, weight in weights.items():
    raw = m.get(metric_name, 0)
    normalized = _normalize_for_composite(metric_name, raw)
    weighted = weight * normalized
    components["raw"][metric_name] = raw
    components["normalized"][metric_name] = normalized
    components["weighted"][metric_name] = weighted
    score += weighted
  components["final_score"] = score
  return score, components


def _rank_by_composite(
  asset_metrics: dict[str, dict], weights: dict,
) -> tuple[list[str], dict[str, dict]]:
  """Rank assets by composite score (higher = better).

  Returns (ranked_asset_list, asset_components_dict) where:
    asset_components_dict maps asset_key → components dict (for decomposition)
  """
  asset_scores: dict[str, float] = {}
  asset_components: dict[str, dict] = {}
  for a in asset_metrics.keys():
    score, components = _composite_score(asset_metrics[a], weights)
    asset_scores[a] = score
    asset_components[a] = components
  ranked = sorted(asset_scores.keys(), key=lambda a: asset_scores[a], reverse=True)
  return ranked, asset_components


def _days_span(times: list[datetime], n: int) -> float:
  """Calendar days covered by the last *n* timestamps. 0 if insufficient data."""
  t = times[-n:] if len(times) >= n else times
  if len(t) < 2:
    return 0
  return max((t[-1] - t[0]).total_seconds() / 86400, 0)


# ── Category Filter (additive, off by default) ────────────────────────────────
# Each asset can belong to one or more categories (CoinGecko-tagged, kept in
# the symbol_categories table). The Category Filter computes a rolling composite
# score per category from the trades of all currently-buffered assets in that
# category, tertile-buckets the categories, and applies a multiplier (or hard
# gate) to each candidate asset's score before final ranking. Multi-category
# assets get the **max** multiplier across their cats (best-case promotion).
#
# Leakage guard: asset_buffers are populated only by CLOSE events that fired
# strictly before the current OPEN-time decision (enforced by the existing
# event loop CLOSE-before-OPEN ordering). No timestamp re-check needed here.

_CATEGORY_INFLUENCE_MULTIPLIERS = {
  # influence_level: (top_tertile_mult, mid_tertile_mult, bottom_tertile_mult)
  "none": (1.0, 1.0, 1.0),
  "low":  (1.10, 1.0, 0.90),
  "mid":  (1.25, 1.0, 0.75),
  "high": (1.50, 1.0, 0.50),
  # "all" is a hard gate handled separately
}


# Side suffixes produced by _asset_key (via .title()): "Long", "Short".
# Also handle abbreviated forms "L", "S" for forward-compat.
_SIDE_SUFFIXES = {"L", "S", "LONG", "SHORT"}


def _symbol_from_scoring_key(key: str) -> str:
  """Extract the bare SYMBOL from an event_wfo scoring key.

  Scoring keys are formatted as either ``SYMBOL_Side`` or ``B{bid}_SYMBOL_Side``
  (per-batch mode). Strips an optional ``B<digits>_`` prefix and an optional
  trailing side suffix (``_Long``, ``_Short``, ``_L``, ``_S``).
  """
  parts = key.split("_")
  if parts and parts[0].startswith("B") and parts[0][1:].isdigit():
    parts = parts[1:]
  if len(parts) >= 2 and parts[-1].upper() in _SIDE_SUFFIXES:
    parts = parts[:-1]
  return "_".join(parts)


def _score_category_buffer(
  pnls: list[float],
  times: list[datetime],
  score_metric_name: str,
  composite_weights: dict,
) -> float:
  metric_name = str(score_metric_name or "composite").lower()
  span = _days_span(times, len(pnls))
  metrics = _compute_event_metrics(pnls, span)

  if metric_name == "composite":
    score, _ = _composite_score(metrics, composite_weights)
    return score

  try:
    score = float(score_metric(pd.Series(pnls, dtype=float), metric_name))
  except Exception:
    score = -999.0

  if score == -999.0:
    score, _ = _composite_score(metrics, composite_weights)
  return score


def _compute_category_scores(
  asset_buffers: dict[str, list[float]],
  asset_trade_times: dict[str, list[datetime]],
  categories_map: dict[str, list[str]],
  lookback: int,
  score_metric_name: str,
  composite_weights: dict,
) -> dict[str, float]:
  """Build a {category: composite_score} dict from current buffers.

  For each category, pools the **last ``lookback`` trades** from every buffered
  asset whose symbol is tagged with that category, then runs the same
  ``_composite_score`` blend used for assets. Categories with zero trades are
  omitted.
  """
  if not categories_map or lookback <= 0:
    return {}
  cat_pnls: dict[str, list[float]] = defaultdict(list)
  cat_times: dict[str, list[datetime]] = defaultdict(list)
  for asset_key, buf in asset_buffers.items():
    if not buf:
      continue
    sym = _symbol_from_scoring_key(asset_key).upper()
    cats = categories_map.get(sym) or []
    if not cats:
      continue
    tail_pnls = buf[-lookback:]
    tail_times = (asset_trade_times.get(asset_key) or [])[-lookback:]
    for c in cats:
      cat_pnls[c].extend(tail_pnls)
      cat_times[c].extend(tail_times)
  scores: dict[str, float] = {}
  for c, pnls in cat_pnls.items():
    if not pnls:
      continue
    score = _score_category_buffer(
      pnls, cat_times.get(c) or [], score_metric_name, composite_weights,
    )
    scores[c] = score
  return scores


def _compute_category_scores_streak_event(
  asset_buffers: dict[str, list[float]],
  asset_trade_times: dict[str, list[datetime]],
  categories_map: dict[str, list[str]],
  lookback: int,
  streak_required: int,
  fire_duration_trades: int,
  rebalance_ts: datetime | None = None,
) -> dict[str, float]:
  """Event-driven category scorer — returns 1.0 for "on fire" categories, 0.0 otherwise.

  A category is "on fire" if any sliding window of ``streak_required`` consecutive
  pooled category trades (drawn from the most recent ``streak_required +
  fire_duration_trades`` trades) were all profitable. This captures the signal
  "a winning streak completed in this category within the trailing decay window"
  and lets the on-fire state auto-expire ``fire_duration_trades`` after the
  streak ended.

  Trades from all assets tagged with the category are pooled by exit-time order.
  When ``lookback`` is large (e.g. 100k), effectively uses whole history.
  Validated via session 2026-04-17-B Trial T06: +$199/q avg PnL, -$392/q DD,
  +0.31 PF vs no-filter baseline on `s33_E4b_v3_ddweight`.
  """
  if not categories_map or lookback <= 0 or streak_required <= 0:
    return {}
  # Build (exit_time, pnl) pairs per category then sort chronologically.
  # Without the sort, the pool zigzags in dict-iteration order across assets,
  # so "last N trades" is approximate. All trades are still past (CLOSE events
  # only per the event-loop sort at line 822), but the streak window is precise
  # only after chronological merge.
  cat_pairs: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
  for asset_key, buf in asset_buffers.items():
    if not buf:
      continue
    sym = _symbol_from_scoring_key(asset_key).upper()
    cats = categories_map.get(sym) or []
    if not cats:
      continue
    times = asset_trade_times.get(asset_key) or []
    tail_pnls = buf[-lookback:] if lookback > 0 else buf
    tail_times = times[-lookback:] if lookback > 0 else times
    pairs = list(zip(tail_times, tail_pnls))
    for c in cats:
      cat_pairs[c].extend(pairs)
  scores: dict[str, float] = {}
  window_span = streak_required + max(0, fire_duration_trades)
  for c, pairs in cat_pairs.items():
    pairs.sort(key=lambda p: p[0])
    if rebalance_ts is not None:
      pairs = [p for p in pairs if p[0] < rebalance_ts]
    if len(pairs) < streak_required:
      scores[c] = 0.0
      continue
    pnls = [p[1] for p in pairs]
    recent = pnls[-window_span:] if window_span > 0 else pnls
    on_fire = False
    for i in range(len(recent) - streak_required + 1):
      window = recent[i : i + streak_required]
      if all(x > 0 for x in window):
        on_fire = True
        break
    scores[c] = 1.0 if on_fire else 0.0
  return scores


def _category_tier_lookup_event(cat_scores: dict[str, float]) -> dict[str, str]:
  """Map event-driven category scores (0.0/1.0) to 'on_fire' / 'cold' labels.

  Companion to :func:`_compute_category_scores_streak_event`. A score of 1.0
  (or any value >= 0.5) means the category is currently on fire; else cold.
  """
  if not cat_scores:
    return {}
  return {c: ("on_fire" if s >= 0.5 else "cold") for c, s in cat_scores.items()}


def _category_tier_lookup(cat_scores: dict[str, float]) -> dict[str, str]:
  """Tertile-bucket categories by score → {category: 'top'|'mid'|'bottom'}.

  With < 3 categories, every category falls into 'top' (no meaningful tiers).
  """
  if not cat_scores:
    return {}
  if len(cat_scores) < 3:
    return {c: "top" for c in cat_scores}
  ordered = sorted(cat_scores.items(), key=lambda kv: kv[1], reverse=True)
  n = len(ordered)
  cut1 = max(1, n // 3)
  cut2 = max(cut1 + 1, (2 * n) // 3)
  tiers: dict[str, str] = {}
  for i, (c, _) in enumerate(ordered):
    if i < cut1:
      tiers[c] = "top"
    elif i < cut2:
      tiers[c] = "mid"
    else:
      tiers[c] = "bottom"
  return tiers


def _apply_category_filter(
  ranked_list: list[tuple[str, float, dict]],
  categories_map: dict[str, list[str]],
  cat_tiers: dict[str, str],
  influence: str,
  max_per_cat: int,
  fire_multiplier: float = 1.30,
) -> list[tuple[str, float, dict]]:
  """Re-rank ``ranked_list`` by applying a per-category multiplier (or gate).

  - influence='none': passthrough (returns ranked_list unchanged).
  - influence in {'low','mid','high'}: multiply each asset's score by the
    **max** multiplier across its categories (untagged assets stay at 1.0).
  - influence='all': hard gate — drop assets whose category is not in the
    top tertile (untagged assets are dropped).
  - influence='event': event-driven on-fire boost. Assets in an "on_fire"
    category get their score multiplied by ``fire_multiplier`` (default 1.30);
    "cold" categories and untagged assets keep their score unchanged (1.00x).
  - max_per_cat > 0: after re-rank, cap how many simultaneously-selected assets
    can share the same primary (first) category by skipping extras.
  """
  if not ranked_list or influence == "none":
    return ranked_list
  hard_gate = influence == "all"
  event_mode = influence == "event"
  mults = _CATEGORY_INFLUENCE_MULTIPLIERS.get(influence, (1.0, 1.0, 1.0))

  rescored: list[tuple[str, float, dict]] = []
  for asset, score, components in ranked_list:
    sym = _symbol_from_scoring_key(asset).upper()
    cats = categories_map.get(sym) or []
    if hard_gate:
      # Keep only assets that have at least one category in the top tertile
      if not cats or not any(cat_tiers.get(c) == "top" for c in cats):
        continue
      rescored.append((asset, score, components))
      continue
    if event_mode:
      # Event-driven: best-mult across cats; on_fire → fire_multiplier, else 1.0
      if not cats:
        rescored.append((asset, score, components))
        continue
      best_mult = 1.0
      for c in cats:
        label = cat_tiers.get(c, "cold")
        m = fire_multiplier if label == "on_fire" else 1.0
        if m > best_mult:
          best_mult = m
      rescored.append((asset, score * best_mult, components))
      continue
    if not cats:
      rescored.append((asset, score, components))
      continue
    # Soft multiplier — pick the most generous tier across this asset's cats
    best_mult = 0.0
    for c in cats:
      tier = cat_tiers.get(c, "mid")
      m = {"top": mults[0], "mid": mults[1], "bottom": mults[2]}[tier]
      if m > best_mult:
        best_mult = m
    rescored.append((asset, score * best_mult, components))

  # Re-sort by adjusted score (descending)
  rescored.sort(key=lambda t: t[1], reverse=True)

  if max_per_cat <= 0:
    return rescored

  # Enforce max_per_cat using each asset's PRIMARY (first) category
  cat_counts: dict[str, int] = defaultdict(int)
  capped: list[tuple[str, float, dict]] = []
  for asset, score, components in rescored:
    sym = _symbol_from_scoring_key(asset).upper()
    cats = categories_map.get(sym) or []
    primary = cats[0] if cats else None
    if primary is None:
      capped.append((asset, score, components))
      continue
    if cat_counts[primary] >= max_per_cat:
      continue
    cat_counts[primary] += 1
    capped.append((asset, score, components))
  return capped


def _rank_assets_for_decision(
  asset_buffers: dict,
  asset_trade_times: dict,
  eligible_assets: set,
  n_trades: int,
  scoring_method: str,
  objective: str,
  composite_weights: dict,
  min_pf: float,
  min_wr: float,
  staleness_days: int,
  current_time,
  asset_last_time: dict,
  sample_count_floor: int = 0,
) -> list[tuple[str, float, dict]]:
  """Rank eligible assets by their event metrics computed on the last n_trades.

  Returns a list of (asset_key, score, components_dict) sorted descending by score.
  Assets failing sample floor / PF / WR / staleness gates are excluded.
  components_dict contains per-metric raw values AND normalized contributions
  (for score decomposition).

  Args:
    asset_buffers: dict[asset_key] → list[pnl_values]
    asset_trade_times: dict[asset_key] → list[datetime]
    eligible_assets: set of asset keys to rank (filters asset_buffers)
    n_trades: lookback window size
    scoring_method: "single" or "composite"
    objective: metric name for single-method ("sortino", "pf", etc)
    composite_weights: dict for composite method
    min_pf, min_wr: gates (>= min values required)
    staleness_days: max days since last trade (0=disabled)
    current_time: datetime for staleness check
    asset_last_time: dict[asset_key] → last trade datetime
    sample_count_floor: if > 0, exclude assets with < this many trades
  """
  metrics_dict: dict[str, dict] = {}
  components_dict: dict[str, dict] = {}
  excluded_by_floor = 0

  for asset in eligible_assets:
    buf = asset_buffers.get(asset, [])
    if len(buf) < n_trades:
      continue
    # Sample floor gate
    if sample_count_floor > 0 and len(buf) < sample_count_floor:
      excluded_by_floor += 1
      continue
    # Staleness gate
    if staleness_days > 0:
      last = asset_last_time.get(asset)
      if last and (current_time - last).days > staleness_days:
        continue
    # Compute metrics
    metrics = _compute_event_metrics(buf[-n_trades:], _days_span(asset_trade_times.get(asset, []), n_trades))
    # Quality gates
    if min_pf > 0 and metrics["pf"] < min_pf:
      continue
    if min_wr > 0 and metrics["wr"] < min_wr:
      continue
    metrics_dict[asset] = metrics
    if sample_count_floor > 0:
      components_dict[asset] = {"floor_check": len(buf)}

  # Log excluded by floor per decision point (for diagnostics)
  if sample_count_floor > 0 and excluded_by_floor > 0:
    import logging
    log = logging.getLogger(__name__)
    log.debug("_rank_assets_for_decision: %d assets excluded by sample_count_floor=%d",
              excluded_by_floor, sample_count_floor)

  # Rank by scoring method
  if not metrics_dict:
    return []

  if scoring_method == "composite":
    ranked, asset_comps = _rank_by_composite(metrics_dict, composite_weights)
    components_dict.update(asset_comps)
  else:
    # Single-method: still populate components with trivial decomposition
    ranked = _rank_assets(metrics_dict, objective)
    for asset in ranked:
      m = metrics_dict[asset]
      raw_val = m.get(objective, 0)
      norm_val = _normalize_for_composite(objective, raw_val)
      components_dict[asset] = {
        "raw": {objective: raw_val},
        "normalized": {objective: norm_val},
        "weighted": {objective: norm_val},  # single method: weight=1.0
        "final_score": norm_val,
      }

  # Build result tuples (asset, score, components)
  result = []
  for asset in ranked:
    if scoring_method == "composite":
      score = components_dict[asset].get("final_score", 0.0)
    else:
      score = components_dict[asset].get("final_score", 0.0)
    result.append((asset, score, components_dict[asset]))

  return result


def _try_spot_replace(
  check_assets: set[str],
  asset_buffers: dict[str, list[float]],
  asset_last_time: dict[str, datetime],
  asset_trade_times: dict[str, list[datetime]],
  current_portfolio: set[str],
  trade_time: datetime,
  n_trades: int,
  staleness_days: int,
  min_pf: float,
  min_wr: float,
  objective: str,
  min_hold: int,
  asset_locks: dict[str, int],
  scoring_method: str = "single",
  composite_weights: dict | None = None,
) -> int:
  """Try to replace checked portfolio assets with better non-portfolio contenders.

  Mutates current_portfolio and asset_locks in place.
  Returns the number of swaps performed.

  Ranking honours `scoring_method`: under "composite", both current and
  candidate assets are scored via `_composite_score(composite_weights)` and
  higher wins. Under "single" (default), the legacy `objective` metric is used.
  """
  comp_w = composite_weights or {}
  use_composite = scoring_method == "composite"

  # Build eligible non-portfolio pool
  eligible: dict[str, dict] = {}
  for a, buf in asset_buffers.items():
    if a in current_portfolio or len(buf) < n_trades:
      continue
    if staleness_days > 0:
      last = asset_last_time.get(a)
      if last and (trade_time - last).days > staleness_days:
        continue
    m = _compute_event_metrics(buf[-n_trades:], _days_span(asset_trade_times.get(a, []), n_trades))
    if min_pf > 0 and m["pf"] < min_pf:
      continue
    if min_wr > 0 and m["wr"] < min_wr:
      continue
    eligible[a] = m

  if not eligible:
    return 0

  swaps = 0
  for chk in sorted(check_assets):
    if chk not in current_portfolio:
      continue
    if min_hold > 0 and asset_locks.get(chk, 0) > 0:
      continue
    if len(asset_buffers.get(chk, [])) < n_trades:
      continue
    cur_m = _compute_event_metrics(asset_buffers[chk][-n_trades:], _days_span(asset_trade_times.get(chk, []), n_trades))
    # Find best available replacement (not already in portfolio)
    avail = {a: m for a, m in eligible.items() if a not in current_portfolio}
    if not avail:
      break
    if use_composite:
      ranked, _ = _rank_by_composite(avail, comp_w)
      best_a = ranked[0]
      cur_score, _ = _composite_score(cur_m, comp_w)
      best_score, _ = _composite_score(avail[best_a], comp_w)
      should_swap = best_score > cur_score  # composite: higher always wins
    else:
      best_a = _rank_assets(avail, objective)[0]
      cur_score = cur_m.get(objective, 0)
      best_score = avail[best_a].get(objective, 0)
      should_swap = (best_score < cur_score) if objective in _LOWER_IS_BETTER \
                    else (best_score > cur_score)
    if should_swap:
      current_portfolio.discard(chk)
      current_portfolio.add(best_a)
      if min_hold > 0:
        asset_locks[best_a] = min_hold
      swaps += 1
  return swaps


def run_event_wfo(
  all_trades: list[BacktestTrade], config: dict,
  seed_state: dict | None = None,
) -> dict:
  """Run event-based Walk Forward Asset Selection.

  seed_state (T-002e'): optional end-of-run snapshot produced by a previous
    call's `final_state_snapshot`. When provided, the per-asset buffers +
    locks + rescue buffers are pre-populated so the next run continues from
    the prior state instead of rebuilding from scratch. When None (the
    default), behavior is byte-identical to the pre-seed signature — the
    non-hybrid optimizer code path never touches this kwarg.

    Also read from `config["_seed_state"]` when the kwarg is absent. The
    fallback exists so callers that thread `config` through many layers
    (step2_service → hybrid_portfolio_refresh) don't have to add a kwarg
    in every hop to opt in to continuation.

  Walks chronologically through every trade. At each step, evaluates
  all assets on their last N trades, selects Top_K portfolio, and
  checks if the current trade's asset was selected.

  Optional walk-forward controls:
    date_from: Trades before this date populate buffers (warmup) but don't
               contribute to the equity curve or selection tracking.
    warmup_trades: Min global trades before walk-forward starts. Works with
                   date_from: date sets the window, warmup_trades sets the
                   minimum within that window.
    rebalance_mode: How portfolio is updated after warmup:
      "global"      — full re-rank every N global trades (default)
      "portfolio"   — full re-rank every N portfolio-member trades
      "spot_single" — when a portfolio asset trades, re-evaluate only
                      that asset vs the best non-portfolio contender
      "spot_batch"  — accumulate portfolio-asset trades, periodically
                      re-evaluate all accumulated assets vs contenders
    rebalance_every: Interval for "global" mode (default 1 = every trade).
    portfolio_rebalance_every: Interval for "portfolio" and "spot_batch"
                               modes (default 5).
    min_hold: Once an asset enters the portfolio, it stays for at least this
              many of its own trades before eligible for removal (default 0).
  """
  n_trades = config.get("n_trades", 10)
  n_assets = config.get("n_assets", 20)
  min_pf = config.get("min_pf", 1.0)
  min_wr = config.get("min_wr", 30.0)
  staleness_days = config.get("staleness_days", 180)
  objective = config.get("objective", "sortino")

  # Walk-forward enhancements
  date_from_str = config.get("date_from", "") or ""
  rebalance_every = max(1, config.get("rebalance_every", 1))
  min_hold = max(0, config.get("min_hold", 0))
  warmup_trades_cfg = max(0, config.get("warmup_trades", 0))
  rebalance_mode = config.get("rebalance_mode", "global")
  portfolio_rebalance_every = max(1, config.get("portfolio_rebalance_every", 5))

  # Scoring method: "single" (rank by one metric) or "composite" (weighted blend)
  scoring_method = config.get("scoring_method", "single")
  comp_weights = config.get("composite_weights", None) or _DEFAULT_COMPOSITE_WEIGHTS

  # Per-asset day-of-week profitability execution filter
  exec_day_profit = config.get("exec_day_profit", False)

  # Feature 2: Sample-count floor (exclude assets with < this many trades)
  sample_count_floor = max(0, config.get("hybrid_sample_count_floor", 0))

  # Feature 5: Rescue system parameters
  rescue_enabled = config.get("hybrid_rescue_enabled", False)
  rescue_quota_pct = max(0.0, min(1.0, config.get("hybrid_rescue_quota_pct", 0.10)))
  rescue_min_trades = max(0, config.get("hybrid_rescue_min_trades", 30))
  rescue_min_score = max(0.0, config.get("hybrid_rescue_min_score", 0.0))
  rescue_confidence_gap = max(0.0, config.get("hybrid_rescue_confidence_gap", 0.15))
  rescue_trades = config.get("rescue_trades", []) or []

  # Category Filter (additive — passthrough when disabled)
  category_filter_enabled = bool(config.get("category_filter_enabled", False))
  category_influence = str(config.get("category_influence", "none") or "none").lower()
  if category_influence not in ("none", "low", "mid", "high", "all", "event"):
    category_influence = "none"
  category_lookback_trades = max(0, int(config.get("category_lookback_trades", 20) or 0))
  category_max_per_cat = max(0, int(config.get("category_max_per_cat", 0) or 0))
  category_score_metric = str(config.get("category_score_metric", "composite") or "composite").lower()
  categories_map: dict[str, list[str]] = config.get("_categories_map", {}) or {}
  # Event-driven category mode (session 2026-04-17-B — T06 winning variant)
  # When category_mode=='streak_event', the filter uses _compute_category_scores_streak_event
  # + _category_tier_lookup_event + influence='event' branch of _apply_category_filter.
  # When category_mode=='tertile' (default), retains the pre-2026-04-17 behavior.
  category_mode = str(config.get("category_mode", "tertile") or "tertile").lower()
  category_streak_required = max(1, int(config.get("category_streak_required", 5) or 5))
  category_fire_multiplier = float(config.get("category_fire_multiplier", 1.30) or 1.30)
  category_fire_duration_trades = max(0, int(config.get("category_fire_duration_trades", 10) or 10))
  # Auto-route influence to "event" when event mode is selected but influence
  # was left at a legacy value (so preset authors can set category_mode alone).
  if category_mode == "streak_event" and category_influence not in ("event", "none"):
    category_influence = "event"
  # If the master switch is off OR no categories are loaded, force passthrough
  if not category_filter_enabled or not categories_map or category_influence == "none":
    category_filter_enabled = False
    category_influence = "none"

  # Multi-batch / directional settings
  asset_batch_map = config.get("_asset_batch_map", {})
  # Per-(batch, asset) scoring: when a run_batch_map {run_id: batch_id} is
  # provided, the per-asset scoring key becomes "{batch_id}_{SYMBOL}_{Side}"
  # so the same symbol from different batches has independent lookback
  # buffers (different strategies = different scoring histories).
  _run_batch_map: dict = config.get("_run_batch_map", {}) or {}
  _per_batch_keys = bool(_run_batch_map)

  def _scoring_key(_t) -> str:
    base = _asset_key(_t, include_side=True)
    if _per_batch_keys:
      _bid = _run_batch_map.get(getattr(_t, "run_id", None))
      if _bid is not None:
        return f"B{_bid}_{base}"
    return base

  def _key_to_batch_id(key: str) -> int:
    """Extract batch_id from a scoring key.

    In per-batch mode the key is encoded as ``f"B{bid}_{SYMBOL}_{Side}"`` by
    ``_scoring_key``. This helper recovers the numeric batch id so the L2/L3
    directional path can group correctly. Falls back to the legacy
    ``asset_batch_map`` lookup in non-per-batch mode.
    """
    if _per_batch_keys and key.startswith("B"):
      _under = key.find("_")
      if _under > 1:
        try:
          return int(key[1:_under])
        except ValueError:
          pass
    return asset_batch_map.get(key, 0)

  l2_groups = config.get("l2_groups", [])
  n_per_group = config.get("n_per_group", 15)
  obj_l2 = config.get("obj_l2", "sortino")
  obj_l3 = config.get("obj_l3", "sortino")
  directional = config.get("directional_batches", False) and bool(l2_groups)

  # ── Session Filter — execution-only gating (Golden Rule: never filter scoring data) ──
  # In event mode, the session filter must NOT remove trades from the pool.
  # Scoring always sees ALL trades. The filter only gates PnL counting.
  sf_enabled = config.get("session_filter_enabled", False)
  _sf_exclude_days: set[int] = set()
  _sf_exclude_hours: set[int] = set()
  if sf_enabled and all_trades:
    sf_mode = config.get("session_filter_mode", "fixed")
    if sf_mode == "fixed":
      _sf_exclude_days = set(config.get("session_filter_exclude_days", []))
      _sf_exclude_hours = set(config.get("session_filter_exclude_hours", []))
    elif sf_mode == "auto":
      from backend.analysis.session_auto_mask import compute_auto_mask
      _auto = compute_auto_mask(
        all_trades,
        config.get("session_filter_auto_min_trades", 10),
        config.get("session_filter_auto_include_insufficient", True),
      )
      # Convert auto mask to exclude sets for execution gating
      mask = _auto.get("mask_data", {})
      _sf_exclude_days = {d for d in range(7) if not mask.get("days", {}).get(str(d), True)}
      _sf_exclude_hours = {h for h in range(24) if not mask.get("hours", {}).get(str(h), True)}
  _sf_active = bool(_sf_exclude_days or _sf_exclude_hours)

  # ── Build two-event stream (OPEN at entry_time, CLOSE at exit_time) ──
  # Look-ahead prevention: portfolio decisions fire at OPEN (entry_time) and
  # can ONLY see trades already CLOSED by then. Buffers are updated at CLOSE.
  # Events at the same timestamp are ordered CLOSE-before-OPEN so a closing
  # trade's PnL is visible to a co-timestamped decision.
  def _to_dt(v):
    if v is None:
      return None
    if isinstance(v, str):
      try:
        return datetime.fromisoformat(v)
      except ValueError:
        return None
    return v

  events: list[tuple[datetime, int, str, object]] = []
  _valid_trades = 0
  for _t in all_trades:
    _et = _to_dt(_t.entry_time)
    _xt = _to_dt(_t.exit_time)
    if _et is None or _xt is None or _xt < _et:
      continue
    events.append((_et, 1, "OPEN", _t))   # OPEN after CLOSE at same ts
    events.append((_xt, 0, "CLOSE", _t))  # CLOSE first
    _valid_trades += 1

  if not events:
    return {"mode": "event", "error": "No trades with entry+exit times"}

  events.sort(key=lambda e: (e[0], e[1]))
  # Placeholder so return dict still exposes a "sorted_trades" size
  sorted_trades = [ev[3] for ev in events if ev[2] == "OPEN"]

  # Parse warmup cutoff
  warmup_cutoff: datetime | None = None
  if date_from_str:
    try:
      warmup_cutoff = datetime.fromisoformat(date_from_str)
    except ValueError:
      pass  # Invalid date string, ignore

  # Per-asset trade PnL buffers
  asset_buffers: dict[str, list[float]] = defaultdict(list)
  # Per-asset trade timestamps (for Calmar days_span)
  asset_trade_times: dict[str, list[datetime]] = defaultdict(list)
  # Per-asset last trade time (for staleness)
  asset_last_time: dict[str, datetime] = {}
  # Per-asset per-weekday cumulative PnL (for exec_day_profit filter)
  asset_day_pnl: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))

  # Results accumulators
  equity_curve: list[float] = []
  equity_dates: list[str] = []
  cum_pnl = 0.0
  selected_count = 0
  selected_pnls: list[float] = []
  selected_records: list[dict] = []  # per-selected-trade: {dt, pnl, asset, symbol, side, timeframe, batch_id, components, rescued}
  asset_selected: dict[str, int] = defaultdict(int)
  asset_eligible: dict[str, int] = defaultdict(int)
  portfolio_sizes: list[int] = []
  prev_portfolio: set[str] = set()
  selection_changes: list[dict] = []

  # Feature 5: Rescue system tracking
  rescue_pool_sizes: list[int] = []
  rescue_decisions_total = 0
  rescue_decisions_triggered = 0
  rescue_admissions_total = 0
  rescue_by_asset: dict[str, int] = defaultdict(int)

  # Feature 5: Rescue asset buffers (separate from main asset_buffers)
  rescue_asset_buffers: dict[str, list[float]] = defaultdict(list)
  rescue_asset_trade_times: dict[str, list[datetime]] = defaultdict(list)
  rescue_asset_last_time: dict[str, datetime] = {}

  # T-002e': optional state seeding. When seed_state is None (default), this
  # block is a no-op and the function behaves byte-identically to the prior
  # signature. When provided, we pre-fill the per-asset buffers/locks/rescue
  # buffers from a prior run's `final_state_snapshot`. Datetime values are
  # accepted either as ISO strings (JSON round-trip) or as datetime objects.
  #
  # T-001 Stage 8: also seed `current_portfolio` and `asset_locks` so the
  # live refresh inherits the saved basket. Without these, saved-basket
  # assets would be silently ranked-out at the first rebalance event even
  # if they had min_hold trades remaining. The optimizer's normal
  # per-window walks pass no seed and behave as before.
  if seed_state is None:
    seed_state = config.get("_seed_state")

  # Walk-forward state (declared here so seed loader can populate them)
  current_portfolio: set[str] = set()
  portfolio_components: dict[str, dict] = {}  # hoisted so end-of-run readout is safe when no rebalance occurs
  asset_locks: dict[str, int] = {}

  if seed_state:
    def _parse_dt(v):
      return datetime.fromisoformat(v) if isinstance(v, str) else v
    for _k, _v in (seed_state.get("asset_buffers") or {}).items():
      asset_buffers[_k] = list(_v)
    for _k, _v in (seed_state.get("asset_trade_times") or {}).items():
      asset_trade_times[_k] = [_parse_dt(_t) for _t in _v]
    for _k, _v in (seed_state.get("asset_last_time") or {}).items():
      asset_last_time[_k] = _parse_dt(_v)
    for _k, _v in (seed_state.get("rescue_asset_buffers") or {}).items():
      rescue_asset_buffers[_k] = list(_v)
    for _k, _v in (seed_state.get("rescue_asset_trade_times") or {}).items():
      rescue_asset_trade_times[_k] = [_parse_dt(_t) for _t in _v]
    for _k, _v in (seed_state.get("rescue_asset_last_time") or {}).items():
      rescue_asset_last_time[_k] = _parse_dt(_v)
    # Saved-basket continuity: incumbents start in the portfolio and
    # carry their min_hold protection. asset_locks may be stale if the
    # prior walk's data overlaps with this walk; passing `True` for
    # `_reset_locks_to_min_hold` (set by the live refresh) treats every
    # incumbent as freshly-added.
    for _k in (seed_state.get("current_portfolio") or []):
      current_portfolio.add(_k)
    if seed_state.get("_reset_locks_to_min_hold"):
      _seed_min_hold = max(0, int(config.get("min_hold", 0)))
      if _seed_min_hold > 0:
        for _k in current_portfolio:
          asset_locks[_k] = _seed_min_hold
    else:
      for _k, _v in (seed_state.get("asset_locks") or {}).items():
        try:
          asset_locks[_k] = int(_v)
        except (ValueError, TypeError):
          pass
  post_warmup_counter = 0
  rebalance_count = 0
  warmup_trade_count = 0
  portfolio_trade_counter = 0
  spot_traded_since_check: set[str] = set()

  # ── Build rescue event stream (if enabled) ──
  # Only CLOSE events matter for rescue: they update the rescue buffers.
  # Rescue OPEN events do NOT fire decisions (rescue candidates never trade
  # on their own; they only enter the slate via admission at main-path
  # rebalance points).
  rescue_events: list[tuple[datetime, object]] = []
  if rescue_enabled and rescue_trades:
    for _t in rescue_trades:
      _xt = _to_dt(_t.exit_time)
      if _xt is None:
        continue
      rescue_events.append((_xt, _t))
    if rescue_events:
      rescue_events.sort(key=lambda e: e[0])
  # Persistent index into rescue_events — advances as we consume events,
  # never restarts. Fixes a bug where the rescue buffer was re-populated
  # from scratch on every rebalance, causing duplicate trades to accumulate
  # (buffer size grew as O(n_rebalances × n_rescue_trades) instead of O(n_rescue_trades)).
  rescue_event_idx = 0

  # ── Main event loop ──
  # Two event types per trade:
  #   CLOSE — updates the per-asset buffer (+ day PnL) with the realized result.
  #   OPEN  — decision point: rebalance (if due), check membership, record pnl.
  # Same timestamp: CLOSE fires before OPEN so co-timestamped decisions can see
  # the just-closed data. Nothing fires on future data; the Golden Rule holds.
  for ev_time, _prio, kind, trade in events:
    asset = _scoring_key(trade)
    trade_pnl = trade.net_profit or 0.0

    if kind == "CLOSE":
      # Realized trade: update scoring buffers.
      asset_buffers[asset].append(trade_pnl)
      asset_trade_times[asset].append(ev_time)
      asset_last_time[asset] = ev_time
      # Day-of-week PnL tracking keyed by ENTRY weekday (same field as filter).
      if exec_day_profit:
        _ent = _to_dt(trade.entry_time)
        if _ent is not None:
          asset_day_pnl[asset][_ent.weekday()] += trade_pnl

      # Spot rebalance triggers fire when a portfolio asset CLOSES — that's
      # when we have a new data point to justify considering a swap.
      if asset in current_portfolio:
        if rebalance_mode == "spot_single":
          swaps = _try_spot_replace(
            {asset}, asset_buffers, asset_last_time, asset_trade_times,
            current_portfolio, ev_time, n_trades, staleness_days,
            min_pf, min_wr, objective, min_hold, asset_locks,
            scoring_method=scoring_method, composite_weights=comp_weights,
          )
          rebalance_count += swaps
        elif rebalance_mode == "spot_batch":
          spot_traded_since_check.add(asset)
          portfolio_trade_counter += 1
          if portfolio_trade_counter % portfolio_rebalance_every == 0:
            swaps = _try_spot_replace(
              spot_traded_since_check, asset_buffers, asset_last_time,
              asset_trade_times, current_portfolio, ev_time, n_trades,
              staleness_days, min_pf, min_wr, objective, min_hold,
              asset_locks,
              scoring_method=scoring_method, composite_weights=comp_weights,
            )
            rebalance_count += swaps
            spot_traded_since_check = set()

      # A trade's own lock counts down when that trade closes (it "used" one slot).
      if min_hold > 0 and asset in asset_locks and asset_locks[asset] > 0:
        asset_locks[asset] -= 1
      continue

    # ── OPEN event ── decision point at entry_time ──
    trade_time = ev_time  # entry_time

    trade_weekday = trade_time.weekday() if exec_day_profit and hasattr(trade_time, "weekday") else None

    # === WARMUP PHASE: date-based (entry time vs cutoff) ===
    # Trades that open before the cutoff don't get scored or recorded. Their
    # CLOSE events still populate buffers (even if the close happens after the
    # cutoff — that's fine: the buffer reflects history, which is what
    # post-cutoff decisions need).
    if warmup_cutoff and trade_time < warmup_cutoff:
      warmup_trade_count += 1
      continue

    # === WARMUP PHASE: trade-count ===
    if warmup_trades_cfg > 0 and warmup_trade_count < warmup_trades_cfg:
      warmup_trade_count += 1
      continue

    post_warmup_counter += 1

    # Determine rebalance trigger based on mode
    if rebalance_mode == "global":
      is_rebalance = (rebalance_every == 1) or \
                     (post_warmup_counter == 1) or \
                     (post_warmup_counter % rebalance_every == 0)
    elif rebalance_mode == "portfolio":
      is_portfolio_trade = asset in current_portfolio
      if is_portfolio_trade:
        portfolio_trade_counter += 1
      is_rebalance = (post_warmup_counter == 1) or \
                     (is_portfolio_trade and
                      portfolio_trade_counter % portfolio_rebalance_every == 0)
    else:
      # Spot modes: full rebalance only on first step
      is_rebalance = (post_warmup_counter == 1)

    # --- Portfolio evaluation (only on rebalance steps) ---
    if is_rebalance:
      rebalance_count += 1
      portfolio: set[str] = set()
      portfolio_components: dict[str, dict] = {}  # Feature 3: track components for selected assets

      # Category Filter: compute fresh per-category tiers for this rebalance.
      # Empty when filter is disabled — _apply_category_filter is a no-op then.
      # Dispatches on category_mode: "tertile" (legacy) | "streak_event" (T06).
      cat_tiers: dict[str, str] = {}
      if category_filter_enabled:
        if category_mode == "streak_event":
          cat_scores = _compute_category_scores_streak_event(
            asset_buffers,
            asset_trade_times,
            categories_map,
            category_lookback_trades,
            category_streak_required,
            category_fire_duration_trades,
            rebalance_ts=ev_time,
          )
          cat_tiers = _category_tier_lookup_event(cat_scores)
        else:
          cat_scores = _compute_category_scores(
            asset_buffers,
            asset_trade_times,
            categories_map,
            category_lookback_trades,
            category_score_metric,
            comp_weights,
          )
          cat_tiers = _category_tier_lookup(cat_scores)

      if not directional:
        # Single-batch Auto mode: use new ranking helper
        ranked_list = _rank_assets_for_decision(
          asset_buffers=asset_buffers,
          asset_trade_times=asset_trade_times,
          eligible_assets=set(asset_buffers.keys()),
          n_trades=n_trades,
          scoring_method=scoring_method,
          objective=objective,
          composite_weights=comp_weights,
          min_pf=min_pf,
          min_wr=min_wr,
          staleness_days=staleness_days,
          current_time=trade_time,
          asset_last_time=asset_last_time,
          sample_count_floor=sample_count_floor,
        )
        # Apply Category Filter (no-op when disabled)
        if category_filter_enabled:
          ranked_list = _apply_category_filter(
            ranked_list, categories_map, cat_tiers,
            category_influence, category_max_per_cat,
            fire_multiplier=category_fire_multiplier,
          )
        # ranked_list is [(asset, score, components), ...]
        # FIX (2026-04-19): use local loop-variable names so the outer `asset`
        # (set at line 905 from _scoring_key(trade)) doesn't get shadowed by
        # Python's leaky for-loop scoping. Before this fix, after the rebalance
        # block `asset` pointed at ranked_list[n_assets-1] instead of the current
        # trade's key, so the `if asset in current_portfolio` gate at line 1258
        # was always True on rebalance steps and records stored the wrong asset.
        for _rl_asset, _rl_score, _rl_components in ranked_list:
          asset_eligible[_rl_asset] += 1

        # Take top n_assets
        for _rl_i, (_rl_asset, _rl_score, _rl_components) in enumerate(ranked_list[:n_assets]):
          portfolio.add(_rl_asset)
          portfolio_components[_rl_asset] = _rl_components

      else:
        # Multi-batch Directional mode: L1 gate → L2 group → L3 final
        # Build L1 survivors using the new helper
        l1_survivors: dict[int, list[tuple[str, float, dict]]] = defaultdict(list)

        l1_ranked = _rank_assets_for_decision(
          asset_buffers=asset_buffers,
          asset_trade_times=asset_trade_times,
          eligible_assets=set(asset_buffers.keys()),
          n_trades=n_trades,
          scoring_method="single",  # L1 always uses single metric for grouping
          objective=objective,
          composite_weights=comp_weights,
          min_pf=min_pf,
          min_wr=min_wr,
          staleness_days=staleness_days,
          current_time=trade_time,
          asset_last_time=asset_last_time,
          sample_count_floor=sample_count_floor,
        )
        # Apply Category Filter to L1 survivors (no-op when disabled).
        # Cap is intentionally NOT enforced here — directional mode applies its
        # own per-group cap downstream via n_per_group / L2-L3 logic.
        if category_filter_enabled:
          l1_ranked = _apply_category_filter(
            l1_ranked, categories_map, cat_tiers,
            category_influence, max_per_cat=0,
            fire_multiplier=category_fire_multiplier,
          )

        # Group L1 survivors by batch
        # FIX (2026-04-19): use local loop-variable names so `asset` (from
        # line 905) doesn't leak. See the comment above the non-directional
        # block for the full rationale.
        for _l1_asset, _l1_score, _l1_components in l1_ranked:
          asset_eligible[_l1_asset] += 1
          bid = _key_to_batch_id(_l1_asset)
          l1_survivors[bid].append((_l1_asset, _l1_score, _l1_components))

        # L2: group selection
        l2_pool: list[tuple[str, dict]] = []
        grouped_bids: set[int] = set()
        for group_bids in l2_groups:
          grouped_bids.update(group_bids)
          group_assets = []
          for bid in group_bids:
            group_assets.extend(l1_survivors.get(bid, []))
          if not group_assets:
            continue
          # Re-rank within group using L2 objective
          # FIX (2026-04-19): _gm_asset instead of `asset` (no leak — see above).
          group_metrics = {a: {} for a, _, _ in group_assets}
          for _gm_asset in group_metrics.keys():
            if len(asset_buffers[_gm_asset]) >= n_trades:
              group_metrics[_gm_asset] = _compute_event_metrics(
                asset_buffers[_gm_asset][-n_trades:],
                _days_span(asset_trade_times.get(_gm_asset, []), n_trades)
              )
          if scoring_method == "composite":
            ranked, _ = _rank_by_composite(group_metrics, comp_weights)
          else:
            ranked = _rank_assets(group_metrics, obj_l2)
          for a in ranked[:n_per_group]:
            # Find components from l1_survivors
            comps = next((c for x, _, c in group_assets if x == a), {})
            l2_pool.append((a, comps))

        for bid, assets in l1_survivors.items():
          if bid not in grouped_bids:
            for a, s, comps in assets:
              l2_pool.append((a, comps))

        # L3: final selection
        # FIX (2026-04-19): _l3_asset instead of `asset` (no leak — see above).
        if l2_pool:
          l3_metrics = {}
          for _l3_asset, _l3_comps in l2_pool:
            if len(asset_buffers[_l3_asset]) >= n_trades:
              l3_metrics[_l3_asset] = _compute_event_metrics(
                asset_buffers[_l3_asset][-n_trades:],
                _days_span(asset_trade_times.get(_l3_asset, []), n_trades)
              )
          if scoring_method == "composite":
            ranked, l3_comps = _rank_by_composite(l3_metrics, comp_weights)
            portfolio_components = {a: l3_comps.get(a, {}) for a in ranked[:n_assets]}
          else:
            ranked = _rank_assets(l3_metrics, obj_l3)
            portfolio_components = {a: (next((c for x, c in l2_pool if x == a), {})) for a in ranked[:n_assets]}
          portfolio = set(ranked[:n_assets])

      # Feature 5 & 6: Rescue system — try to admit rejected assets if confidence gap is high
      if rescue_enabled and rescue_events:
        rescue_decisions_total += 1
        # Advance the rescue event index up to (but not past) the current
        # decision time. Uses a persistent index so each rescue event is
        # processed exactly once across all rebalances.
        while rescue_event_idx < len(rescue_events):
          r_ev_time, r_trade = rescue_events[rescue_event_idx]
          if r_ev_time > trade_time:
            break
          r_asset = _scoring_key(r_trade)
          rescue_asset_buffers[r_asset].append(r_trade.net_profit or 0.0)
          rescue_asset_trade_times[r_asset].append(r_ev_time)
          rescue_asset_last_time[r_asset] = r_ev_time
          rescue_event_idx += 1

        # Rank rescue candidates using the SAME scoring window (n_trades) as
        # the main path, so scores are directly comparable for the
        # confidence-gap check. The rescue_min_trades value is applied as a
        # sample-count floor (eligibility gate), NOT as the scoring window.
        rescue_ranked = _rank_assets_for_decision(
          asset_buffers=rescue_asset_buffers,
          asset_trade_times=rescue_asset_trade_times,
          eligible_assets=set(rescue_asset_buffers.keys()),
          n_trades=n_trades,
          scoring_method=scoring_method,
          objective=objective,
          composite_weights=comp_weights,
          min_pf=min_pf,
          min_wr=min_wr,
          staleness_days=staleness_days,
          current_time=trade_time,
          asset_last_time=rescue_asset_last_time,
          sample_count_floor=rescue_min_trades,
        )

        # Apply absolute score floor
        rescue_ranked = [
          (a, s, c) for a, s, c in rescue_ranked
          if s >= rescue_min_score
        ]

        rescue_pool_sizes.append(len(rescue_ranked))

        if rescue_ranked:
          # Get worst accepted asset score
          if portfolio:
            worst_portfolio_score = min(
              portfolio_components.get(a, {}).get("final_score", 0.0)
              for a in portfolio
            ) if portfolio_components else 0.0
          else:
            worst_portfolio_score = 0.0

          # Admit rescue candidates if gap threshold is met
          # Cap at rescue_quota_pct of n_assets
          max_rescues = max(1, int(np.ceil(rescue_quota_pct * n_assets)))
          rescues_admitted = 0

          for rescue_asset, rescue_score, rescue_components in rescue_ranked:
            if rescue_asset in portfolio:
              continue  # Already in portfolio
            if rescues_admitted >= max_rescues:
              break

            # Check confidence gap: best rescue vs worst accepted
            gap = rescue_score - worst_portfolio_score
            if gap >= rescue_confidence_gap:
              # Admit this rescue: replace worst portfolio member
              if portfolio:
                worst_asset = min(
                  portfolio,
                  key=lambda a: portfolio_components.get(a, {}).get("final_score", 0.0)
                )
                portfolio.discard(worst_asset)
              portfolio.add(rescue_asset)
              portfolio_components[rescue_asset] = rescue_components
              rescues_admitted += 1
              rescue_admissions_total += 1
              rescue_by_asset[rescue_asset] += 1
              # Update worst score for next candidate
              if portfolio:
                worst_portfolio_score = min(
                  portfolio_components.get(a, {}).get("final_score", 0.0)
                  for a in portfolio
                )

          if rescues_admitted > 0:
            rescue_decisions_triggered += 1

      # Selection lock
      if min_hold > 0:
        for a in current_portfolio - portfolio:
          if asset_locks.get(a, 0) > 0:
            portfolio.add(a)
        for a in portfolio - current_portfolio:
          asset_locks[a] = min_hold

      current_portfolio = portfolio

    # Track portfolio changes
    if current_portfolio != prev_portfolio and post_warmup_counter > 1:
      added = current_portfolio - prev_portfolio
      removed = prev_portfolio - current_portfolio
      if added or removed:
        selection_changes.append({
          "step": post_warmup_counter,
          "date": trade_time.strftime("%Y-%m-%d") if hasattr(trade_time, "strftime") else str(trade_time)[:10],
          "added": sorted(added),
          "removed": sorted(removed),
        })
    prev_portfolio = set(current_portfolio)
    portfolio_sizes.append(len(current_portfolio))

    # Execution filters (clocked on entry_time — live trading reality)
    exec_allowed = True
    if exec_day_profit and trade_weekday is not None:
      exec_allowed = asset_day_pnl[asset][trade_weekday] > 0
    if exec_allowed and _sf_active:
      if _sf_exclude_days and trade_time.weekday() in _sf_exclude_days:
        exec_allowed = False
      elif _sf_exclude_hours and trade_time.hour in _sf_exclude_hours:
        exec_allowed = False

    if asset in current_portfolio and exec_allowed:
      cum_pnl += trade_pnl
      selected_count += 1
      asset_selected[asset] += 1
      selected_pnls.append(trade_pnl)
      _trade_run_id = getattr(trade, "run_id", None)
      # Feature 3: include components decomposition in record
      # Feature 5: check if asset was rescued (exists in rescue_by_asset)
      is_rescued = asset in rescue_by_asset if rescue_enabled else False
      record = {
        "dt": trade_time.isoformat() if hasattr(trade_time, "isoformat") else str(trade_time),
        "pnl": float(trade_pnl),
        "asset": asset,
        "symbol": getattr(trade, "symbol", "") or "",
        "side": getattr(trade, "side", "") or "",
        "timeframe": getattr(trade, "timeframe", "") or "",
        "run_id": _trade_run_id,
        "batch_id": _run_batch_map.get(_trade_run_id, 0) if _per_batch_keys else 0,
        "components": portfolio_components.get(asset),  # Feature 3
        "rescued": is_rescued,  # Feature 5
      }
      selected_records.append(record)

    equity_curve.append(round(cum_pnl, 2))
    dt_str = trade_time.strftime("%Y-%m-%d") if hasattr(trade_time, "strftime") else str(trade_time)[:10]
    equity_dates.append(dt_str)

  # Summary metrics from selected trades only (tracked during main loop)
  summary_metrics = _compute_event_metrics(selected_pnls) if selected_pnls else {}

  # Build asset frequency table
  asset_frequency: dict[str, dict] = {}
  all_assets = set(asset_selected.keys()) | set(asset_eligible.keys())
  for a in sorted(all_assets):
    sel = asset_selected.get(a, 0)
    elig = asset_eligible.get(a, 0)
    asset_frequency[a] = {
      "selected": sel,
      "eligible": elig,
      "pct": round(sel / elig * 100, 1) if elig > 0 else 0,
    }

  avg_portfolio = round(sum(portfolio_sizes) / len(portfolio_sizes), 1) if portfolio_sizes else 0

  # Add portfolio-level stats to summary
  summary_metrics["avg_portfolio_size"] = avg_portfolio
  summary_metrics["total_steps"] = post_warmup_counter
  summary_metrics["total_trades"] = selected_count

  # Feature 5: Add rescue system stats to summary
  summary_metrics["rescue_enabled"] = rescue_enabled
  summary_metrics["rescue_decisions_total"] = rescue_decisions_total
  summary_metrics["rescue_decisions_triggered"] = rescue_decisions_triggered
  summary_metrics["rescue_admissions_total"] = rescue_admissions_total
  summary_metrics["rescue_pool_size_avg"] = round(
    sum(rescue_pool_sizes) / len(rescue_pool_sizes), 1
  ) if rescue_pool_sizes else 0
  summary_metrics["rescue_by_asset"] = dict(rescue_by_asset)

  # End-of-stream basket + per-asset final composite scores.
  # Consumed by the live-loop hybrid refresh service (backend/services/hybrid_portfolio_refresh.py)
  # to project Step-2 selection into executor weights without re-running scoring.
  current_portfolio_at_end = sorted(current_portfolio) if current_portfolio else []
  asset_scores_at_end = {
    a: float(portfolio_components.get(a, {}).get("final_score", 0.0))
    for a in current_portfolio_at_end
  }

  # T-002e': end-of-run snapshot for continuation. JSON-safe (datetime
  # values serialized as ISO strings). Pass this dict back into run_event_wfo
  # via the `seed_state` kwarg to resume a prior run without rebuilding
  # per-asset buffers from scratch. Included unconditionally so callers can
  # opt in without a config flag; non-hybrid callers that never consume it
  # incur only the cost of building the dict (~ms for typical basket sizes).
  final_state_snapshot = {
    "asset_buffers": {k: list(v) for k, v in asset_buffers.items()},
    "asset_trade_times": {
      k: [t.isoformat() if isinstance(t, datetime) else t for t in v]
      for k, v in asset_trade_times.items()
    },
    "asset_last_time": {
      k: (v.isoformat() if isinstance(v, datetime) else v)
      for k, v in asset_last_time.items()
    },
    "rescue_asset_buffers": {k: list(v) for k, v in rescue_asset_buffers.items()},
    "rescue_asset_trade_times": {
      k: [t.isoformat() if isinstance(t, datetime) else t for t in v]
      for k, v in rescue_asset_trade_times.items()
    },
    "rescue_asset_last_time": {
      k: (v.isoformat() if isinstance(v, datetime) else v)
      for k, v in rescue_asset_last_time.items()
    },
    # T-001 Stage 8: incumbent basket + lock counts for live refresh
    # continuity. The live refresh seeds these so saved-basket assets
    # carry their min_hold protection across refresh boundaries.
    "current_portfolio": sorted(current_portfolio),
    "asset_locks": {k: int(v) for k, v in asset_locks.items()},
  }

  return {
    "mode": "event",
    "equity_curve": equity_curve,
    "equity_dates": equity_dates,
    "all_trade_count": len(sorted_trades),
    "selected_trade_count": selected_count,
    "warmup_trades": warmup_trade_count,
    "rebalance_count": rebalance_count,
    "summary": summary_metrics,
    "asset_frequency": asset_frequency,
    "selection_changes": selection_changes[:200],
    "selected_pnls": selected_pnls,
    "selected_records": selected_records,
    "current_portfolio_at_end": current_portfolio_at_end,
    "asset_scores_at_end": asset_scores_at_end,
    "final_state_snapshot": final_state_snapshot,
  }
