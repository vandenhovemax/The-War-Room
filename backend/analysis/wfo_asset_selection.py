"""Walk Forward Optimization — Asset Selection engine.

Runs the existing greedy optimizer on rolling IS windows,
evaluates selected assets on OOS windows, and compiles tracks.
"""
from __future__ import annotations

import itertools
import logging
import re
import time as _time

import numpy as np
import pandas as pd

from backend.analysis.correlation import get_correlation_matrix
from backend.analysis.daily_pnl import build_daily_pnl_matrix
from backend.analysis.diversification import diversify_portfolio, diversify_split_portfolio
from backend.analysis.optimizer import _asset_pf, optimize_portfolio
from backend.analysis.scoring import calculate_stability
from backend.analysis.boundary_clip import clip_is_trades
from backend.analysis.wfo_boundary import (
  assert_no_oos_leakage,
  assert_trades_within_boundary,
  build_is_daily_pnl,
  filter_trades_strict,
)
from backend.db.models import BacktestTrade

_BATCH_TAG_RE = re.compile(r"~B(\d+)_")
log = logging.getLogger(__name__)


def _build_batch_tagged_daily_pnl(
  trades: list[BacktestTrade],
  is_end: str,
  run_batch_map: dict[int, int],
) -> pd.DataFrame:
  """Build batch-tagged daily PnL matrix — same as Cross-Strategy.

  Groups trades by batch_id via run_batch_map, builds per-batch PnL,
  tags columns as SYMBOL~B{bid}_Side, then concatenates.
  This keeps same-symbol assets from different strategy batches separate.
  """
  from backend.api.routes.cross_strategy import _add_batch_tag

  # Group trades by batch_id
  batch_trades: dict[int, list[BacktestTrade]] = {}
  for t in trades:
    bid = run_batch_map.get(t.run_id)
    if bid is not None:
      batch_trades.setdefault(bid, []).append(t)
    else:
      # Unknown batch — group under pseudo-batch 0
      batch_trades.setdefault(0, []).append(t)

  matrices: list[pd.DataFrame] = []
  for bid, bt in batch_trades.items():
    m = build_daily_pnl_matrix(bt, include_side=True)
    if not m.empty:
      m.columns = [_add_batch_tag(c, bid) for c in m.columns]
      matrices.append(m)

  if not matrices:
    return pd.DataFrame()

  daily_pnl = pd.concat(matrices, axis=1).fillna(0)
  if not daily_pnl.empty:
    full_range = pd.date_range(
      start=daily_pnl.index.min(), end=daily_pnl.index.max(), freq="D",
    )
    daily_pnl = daily_pnl.reindex(full_range).fillna(0)

  assert_no_oos_leakage(daily_pnl, is_end, context="batch-tagged daily PnL")
  return daily_pnl


# CS-063: ``generate_wfo_windows`` and ``wfo_window_gen.generate_rolling_windows``
# were two near-identical implementations of the same primitive.
# Silent drift risk: a future fix in one would miss the other and
# optimiser WFO ↔ batch-selection WFO could emit different window
# grids for the same inputs. Canonicalise on the more-tested
# ``generate_rolling_windows`` (7 test files, 3 prod call sites) and
# expose this name as a thin passthrough so existing imports keep
# working.
from backend.analysis.wfo_window_gen import (
  generate_rolling_windows as _generate_rolling_windows_canonical,
)


def generate_wfo_windows(
  is_months: int,
  oos_months: int,
  last_oos_end: str,
  data_start: str,
) -> list[dict]:
  """Generate rolling WFO windows (passthrough to the canonical
  implementation in ``wfo_window_gen.generate_rolling_windows``).
  """
  return _generate_rolling_windows_canonical(
    is_months=is_months,
    oos_months=oos_months,
    last_oos_end=last_oos_end,
    data_start=data_start,
  )


def _filter_trades_by_dates(
  trades: list[BacktestTrade], start: str, end: str,
) -> list[BacktestTrade]:
  """Filter trades where entry_time falls within [start, end]."""
  s = pd.Timestamp(start)
  e = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
  return [t for t in trades if s <= pd.Timestamp(t.entry_time) <= e]


def _compute_metrics(trades: list[BacktestTrade]) -> dict:
  """Compute portfolio-level metrics from a list of trades.

  Groups by position_id so multi-exit positions (TP1+TP2) count as one
  trade, matching calculate_metrics() and _portfolio_metrics().
  Returns gross_profit and gross_loss directly so cumulative aggregation
  can sum them instead of trying to reconstruct from PF + net_profit.
  """
  if not trades:
    return {"net_profit": 0, "profit_factor": 0, "max_drawdown": 0,
            "win_rate": 0, "total_trades": 0, "stability": 0,
            "gross_profit": 0, "gross_loss": 0}
  # Group by position_id to merge partial fills into one P&L per position
  from collections import defaultdict
  pos_pnl: dict[str, float] = defaultdict(float)
  for t in trades:
    key = t.position_id if t.position_id else f"_solo_{t.id}"
    pos_pnl[key] += t.net_profit
  pnls = list(pos_pnl.values())
  net = sum(pnls)
  gp = sum(p for p in pnls if p > 0)
  gl = sum(abs(p) for p in pnls if p <= 0)  # <= 0: zero-PnL counts as loss
  pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)
  wins = sum(1 for p in pnls if p > 0)
  wr = round(wins / len(pnls), 4)
  # Equity curve for drawdown (use raw trade-level pnls for chronological order)
  raw_pnls = [t.net_profit for t in trades]
  equity = np.cumsum(raw_pnls)
  running_max = np.maximum.accumulate(equity)
  dd = float(np.min(equity - running_max))
  return {
    "net_profit": round(net, 2), "profit_factor": pf,
    "max_drawdown": round(dd, 2), "win_rate": wr,
    "total_trades": len(pnls), "stability": 0,
    "gross_profit": round(gp, 2), "gross_loss": round(gl, 2),
  }


def _compute_oos_benchmark(trades: list[BacktestTrade]) -> dict:
  """Compute H1/H2 benchmark robustness on OOS trades."""
  from backend.analysis.benchmark import compute_benchmark_robustness
  rows = []
  for t in trades:
    asset = f"{t.symbol}_{t.side or 'Long'}"
    rows.append({
      "Asset": asset,
      "Net Profit": t.net_profit,
      "Exit Time": pd.Timestamp(t.exit_time) if t.exit_time else pd.NaT,
    })
  if not rows:
    return {}
  df = pd.DataFrame(rows)
  stats, _ = compute_benchmark_robustness(df)
  return stats


def _run_manual_optimization(
  daily_pnl: pd.DataFrame,
  asset_batch_map: dict[str, int],
  strategy_map: dict[int, str],
  batch_ids: list[int],
  optimizer_config: dict,
  corr_method: str,
  n_candidates: int,
  seed_metrics: list[str],
  max_seeds: int,
  prioritize_stability: bool,
  all_trades: list | None = None,
  div_algo: str = "greedy",
) -> list[dict]:
  """3-layer manual optimization for a single WFO window.

  L1: Per-batch, split-optimize within each strategy group
  L2: Score variants using obj_layer2
  L3: Combine best variants across strategies using obj_layer3
  """
  obj_layer1 = optimizer_config.get("obj_layer1", "hybrid")
  obj_layer2 = optimizer_config.get("obj_layer2", "stability")
  obj_layer3 = optimizer_config.get("obj_layer3", "hybrid")
  candidates_per = optimizer_config.get("candidates_per_strategy", 3)
  n_final = optimizer_config.get("n_final_strats", 2)
  n_longs = optimizer_config.get("n_longs", 3)
  n_shorts = optimizer_config.get("n_shorts", 3)

  # TC constraints (position-count)
  tc_low = optimizer_config.get("tc_low_threshold", 0)
  tc_high = optimizer_config.get("tc_high_threshold", 0)
  tc_active = tc_low > 0 or tc_high > 0
  max_dd_pct = optimizer_config.get("max_allowed_drawdown_pct", 0.0)

  l2_groups = optimizer_config.get("l2_groups", [])
  l1_quality_gate = optimizer_config.get("l1_quality_gate", False)

  # Optimizer controls (must match WFO runtime extraction)
  seed_weights = optimizer_config.get("seed_weights", None)
  max_overlap = optimizer_config.get("max_overlap", None)
  expand_pool = optimizer_config.get("expand_greedy_pool", True)
  seed_prior_weight = optimizer_config.get("seed_prior_weight", 0.0)
  streamlit_logic = optimizer_config.get("streamlit_logic", False)
  min_pf = optimizer_config.get("min_pf", 0.5)

  # Phase 1+2: Per-batch optimization
  variant_pnls: dict[str, pd.Series] = {}
  variant_assets: dict[str, list[str]] = {}
  bid_variants: dict[int, list[str]] = {}  # batch_id -> variant names (for L2 groups)

  for bid in batch_ids:
    batch_cols = [c for c in daily_pnl.columns if asset_batch_map.get(c) == bid]
    if len(batch_cols) < 2:
      continue
    batch_pnl = daily_pnl[batch_cols]

    # Quality Gate mode: pass through but filter out clearly poor assets (PF < 0.5)
    if l1_quality_gate:
      gated = [c for c in batch_cols if _asset_pf(batch_pnl[c]) >= min_pf]
      if not gated:
        gated = batch_cols  # fallback: keep all if nothing passes
      sname = strategy_map.get(bid, f"Batch_{bid}")
      vn = f"{sname}_v1"
      variant_assets[vn] = gated
      variant_pnls[vn] = daily_pnl[gated].sum(axis=1)
      bid_variants[bid] = [vn]
      continue

    n_l_avail = sum(1 for c in batch_cols if c.endswith("_Long"))
    n_s_avail = sum(1 for c in batch_cols if c.endswith("_Short"))
    n_l = min(n_longs, max(n_l_avail, 1))
    n_s = min(n_shorts, max(n_s_avail, 1))

    corr = get_correlation_matrix(batch_pnl, corr_method)
    if tc_active and all_trades is not None:
      from backend.analysis.optimizer_tc import optimize_split_with_trade_constraints
      from backend.api.routes.analysis import _compute_trade_counts
      batch_trades = [t for t in all_trades if t.symbol in {c.rsplit("_", 1)[0] for c in batch_cols}]
      trade_counts = _compute_trade_counts(batch_trades, include_side=True)
      tc_dict = {
        "low_threshold": tc_low, "max_low": optimizer_config.get("tc_max_low", 99),
        "high_threshold": tc_high, "min_high": optimizer_config.get("tc_min_high", 0),
      }
      variants = diversify_split_portfolio(
        div_algo, batch_pnl, corr, n_longs=n_l, n_shorts=n_s,
        within_objective=obj_layer1, combined_objective=obj_layer2,
        n_portfolios=candidates_per,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        prioritize_stability=prioritize_stability,
        trade_counts=trade_counts, tc_longs=tc_dict, tc_shorts=tc_dict,
        max_dd_pct=max_dd_pct,
        seed_weights=seed_weights, max_overlap=max_overlap,
        expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
      )
    else:
      variants = diversify_split_portfolio(
        div_algo, batch_pnl, corr, n_longs=n_l, n_shorts=n_s,
        within_objective=obj_layer1, combined_objective=obj_layer2,
        n_portfolios=candidates_per,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        prioritize_stability=prioritize_stability,
        max_dd_pct=max_dd_pct,
        seed_weights=seed_weights, max_overlap=max_overlap,
        expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
      )
    sname = strategy_map.get(bid, f"Batch_{bid}")
    bid_vnames: list[str] = []
    for vi, v in enumerate(variants):
      vn = f"{sname}_v{vi + 1}"
      variant_assets[vn] = v["assets"]
      variant_pnls[vn] = batch_pnl[v["assets"]].sum(axis=1)
      bid_vnames.append(vn)
    bid_variants[bid] = bid_vnames

  if len(variant_pnls) < 2:
    return []

  l3_obj = obj_layer3
  if l3_obj == "min_drawdown":
    l3_obj = "min_max_drawdown"

  if l2_groups:
    # --- L2 grouped Phase 3 (same approach as cross_strategy + beam_optimizer) ---
    from backend.analysis.beam_optimizer import _score_combo

    grouped_bids = set(b for group in l2_groups for b in group)

    # L2: enumerate combos within each group, score with obj_layer2
    l2_super_variants: dict[str, list[str]] = {}
    l2_super_pnls: dict[str, pd.Series] = {}

    for gi, group_bids in enumerate(l2_groups):
      group_variant_lists = [bid_variants.get(bid, []) for bid in group_bids]
      if not all(group_variant_lists):
        continue
      combos = list(itertools.product(*group_variant_lists))
      scored: list[tuple[float, list[str]]] = []
      for combo in combos:
        merged = list(dict.fromkeys(
          a for vn in combo for a in variant_assets.get(vn, [])
        ))
        if not merged:
          continue
        score = _score_combo(daily_pnl, merged, obj_layer2, corr_method)
        scored.append((score, merged))
      scored.sort(key=lambda x: x[0])
      for si, (_, merged) in enumerate(scored[:candidates_per]):
        sv_name = f"Group{gi + 1}_v{si + 1}"
        l2_super_variants[sv_name] = merged
        l2_super_pnls[sv_name] = daily_pnl[merged].sum(axis=1)

    # Ungrouped batches pass through as solo variants
    for bid in set(batch_ids) - grouped_bids:
      for vn in bid_variants.get(bid, []):
        if vn in variant_pnls:
          l2_super_variants[vn] = variant_assets[vn]
          l2_super_pnls[vn] = variant_pnls[vn]

    if len(l2_super_pnls) < 2:
      return []

    # L3: global assembly across super-variants
    l3_n = min(n_final, len(l2_super_pnls))
    vdf = pd.DataFrame(l2_super_pnls).fillna(0)
    vcorr = get_correlation_matrix(vdf, corr_method)
    global_res = optimize_portfolio(
      daily_pnl=vdf, corr_matrix=vcorr,
      n_assets=l3_n, objective=l3_obj,
      n_portfolios=n_candidates,
      seed_metrics=seed_metrics, max_seeds=max_seeds,
      prioritize_stability=prioritize_stability,
      streamlit_logic=streamlit_logic,
      expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
      seed_weights=seed_weights,
    )

    # Expand super-variants to individual assets
    results: list[dict] = []
    for r in global_res:
      sel_variants = r["assets"]
      assets = list(dict.fromkeys(
        a for vn in sel_variants for a in l2_super_variants.get(vn, [])
      ))
      if not assets:
        continue
      asset_pnl = daily_pnl[assets].sum(axis=1)
      net = float(asset_pnl.sum())
      gp = float(asset_pnl[asset_pnl > 0].sum()) if not asset_pnl.empty else 0
      gl = float(abs(asset_pnl[asset_pnl < 0].sum())) if not asset_pnl.empty else 0
      equity = asset_pnl.cumsum()
      dd = float((equity - equity.cummax()).min()) if not equity.empty else 0
      stab = float(calculate_stability(asset_pnl)) if len(asset_pnl) > 1 else 0
      metrics = {
        "net_profit": round(net, 2),
        "profit_factor": round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0),
        "max_drawdown": round(dd, 2),
        "stability": round(stab, 4),
        "n_assets": len(assets),
      }
      results.append({"assets": assets, "metrics": metrics})
    return results

  # --- Original ungrouped Phase 3 ---
  n_final = min(n_final, len(variant_pnls))
  vdf = pd.DataFrame(variant_pnls).fillna(0)
  vcorr = get_correlation_matrix(vdf, corr_method)

  global_res = optimize_portfolio(
    daily_pnl=vdf, corr_matrix=vcorr,
    n_assets=n_final, objective=l3_obj,
    n_portfolios=n_candidates,
    seed_metrics=seed_metrics, max_seeds=max_seeds,
    prioritize_stability=prioritize_stability,
    streamlit_logic=streamlit_logic,
    expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
    seed_weights=seed_weights,
  )

  # Expand variants back to individual assets and compute metrics
  results = []
  for r in global_res:
    sel_variants = r["assets"]
    assets = list(dict.fromkeys(
      a for vn in sel_variants for a in variant_assets.get(vn, [])
    ))
    if not assets:
      continue
    asset_pnl = daily_pnl[assets].sum(axis=1) if assets else pd.Series()
    net = float(asset_pnl.sum())
    gp = float(asset_pnl[asset_pnl > 0].sum()) if not asset_pnl.empty else 0
    gl = float(abs(asset_pnl[asset_pnl < 0].sum())) if not asset_pnl.empty else 0
    equity = asset_pnl.cumsum()
    dd = float((equity - equity.cummax()).min()) if not equity.empty else 0
    stab = float(calculate_stability(asset_pnl)) if len(asset_pnl) > 1 else 0
    metrics = {
      "net_profit": round(net, 2),
      "profit_factor": round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0),
      "max_drawdown": round(dd, 2),
      "stability": round(stab, 4),
      "n_assets": len(assets),
    }
    results.append({"assets": assets, "metrics": metrics})

  return results


def run_wfo_asset_selection(
  all_trades: list[BacktestTrade],
  windows: list[dict],
  optimizer_config: dict,
  n_candidates: int = 3,
  corr_method: str = "standard",
  div_algo: str = "greedy",
  db: "Session | None" = None,
) -> dict:
  """Run optimizer on each IS window, evaluate OOS, compile tracks."""
  _t0 = _time.perf_counter()
  time_mode = optimizer_config.get("time_mode", "rolling")
  split_mode = optimizer_config.get("split_mode", False)
  min_trades = optimizer_config.get("min_trades", 5)
  min_pf = optimizer_config.get("min_pf", 0.5)
  min_wr = optimizer_config.get("min_wr", 0.0)
  objective = optimizer_config.get("objective", "hybrid")
  n_assets = optimizer_config.get("n_assets", 5)
  seed_metrics = optimizer_config.get("seed_metrics", ["sortino"])
  max_seeds = optimizer_config.get("max_seeds", 20)
  prioritize_stability = optimizer_config.get("prioritize_stability", False)
  log.info("═══ WFAS START ═══  windows=%d, split=%s, n_assets=%d, seeds=%d, obj=%s, div=%s",
           len(windows), split_mode, n_assets, max_seeds, objective, div_algo)
  log.info("  Quality: min_trades=%d, min_pf=%.2f, min_wr=%.1f",
           min_trades, min_pf, min_wr)
  log.info("  Benchmark=%s, Recency=%s, Stability=%s",
           optimizer_config.get("benchmark_enabled", False),
           optimizer_config.get("recency_enabled", False),
           prioritize_stability)

  # TC constraints
  tc_low = optimizer_config.get("tc_low_threshold", 0)
  tc_high = optimizer_config.get("tc_high_threshold", 0)
  tc_combined_active = tc_low > 0 or tc_high > 0
  tc_longs_cfg = optimizer_config.get("tc_longs")
  tc_shorts_cfg = optimizer_config.get("tc_shorts")
  tc_split_active = bool(tc_longs_cfg or tc_shorts_cfg)
  max_dd_pct = optimizer_config.get("max_allowed_drawdown_pct", 0.0)
  seed_weights = optimizer_config.get("seed_weights", None)
  max_overlap = optimizer_config.get("max_overlap", None)
  expand_pool = optimizer_config.get("expand_greedy_pool", True)
  seed_prior_weight = optimizer_config.get("seed_prior_weight", 0.0)

  # Benchmark config
  bm_enabled = optimizer_config.get("benchmark_enabled", False)
  bm_source = optimizer_config.get("benchmark_source", "")
  bm_split = optimizer_config.get("benchmark_split", "time")
  bm_threshold = optimizer_config.get("benchmark_threshold", 1.2)
  bm_elite = optimizer_config.get("benchmark_elite_target", 1.0)
  bm_exclude = optimizer_config.get("benchmark_exclude_fragile", True)

  # Event config
  ev_enabled = optimizer_config.get("event_mode", False)
  ev_n_trades = optimizer_config.get("n_trades", 20)
  ev_staleness = optimizer_config.get("staleness_days", 0)
  ev_per_batch = optimizer_config.get("per_batch", None)

  # Recency config
  rec_enabled = optimizer_config.get("recency_enabled", False)
  rec_mode = optimizer_config.get("recency_mode", "gradual")
  rec_batch = optimizer_config.get("recency_batch_size", 10)
  rec_boost = optimizer_config.get("recency_boost_factor", 3.0)

  # Manual mode config
  wfo_mode = optimizer_config.get("wfo_mode", "auto")
  asset_batch_map = optimizer_config.get("_asset_batch_map", {})
  strategy_map = optimizer_config.get("_strategy_map", {})
  manual_batch_ids = optimizer_config.get("_batch_ids", [])

  # Directional batches config
  l2_groups = optimizer_config.get("l2_groups", [])
  n_per_batch = optimizer_config.get("n_per_batch", 5)
  obj_l1 = optimizer_config.get("obj_l1", "hybrid")
  obj_l2 = optimizer_config.get("obj_l2", "uncorrelated")
  obj_l3 = optimizer_config.get("obj_l3", "max_sortino")
  k_l1 = optimizer_config.get("k_l1", 5)
  k_l2 = optimizer_config.get("k_l2", 5)
  k_l3 = optimizer_config.get("k_l3", 3)

  # Always use side-tagged columns (SYMBOL_Long / SYMBOL_Short) for consistency
  # with Cross-Strategy optimizer.  Each direction is a separate optimization target.
  needs_side = True

  # Session filter config
  sf_enabled = optimizer_config.get("session_filter_enabled", False)
  sf_mode = optimizer_config.get("session_filter_mode", "fixed")
  sf_exclude_days = optimizer_config.get("session_filter_exclude_days", [])
  sf_exclude_hours = optimizer_config.get("session_filter_exclude_hours", [])
  sf_auto_min_trades = optimizer_config.get("session_filter_auto_min_trades", 10)
  sf_auto_include_insuf = optimizer_config.get("session_filter_auto_include_insufficient", True)
  sf_grade_on_cropped = optimizer_config.get("session_filter_grade_on_cropped", False)
  sf_saved_mask_id = optimizer_config.get("session_mask_id")

  # Structural quality pre-filter config (mirrors optimizer)
  pf_enabled = optimizer_config.get("prefilter_enabled", False)
  pf_mode = optimizer_config.get("prefilter_mode", "trend")
  pf_hurst_min = optimizer_config.get("prefilter_hurst_min", 0.50)
  pf_ter_pct = optimizer_config.get("prefilter_ter_percentile", 25)
  pf_hurst_max = optimizer_config.get("prefilter_hurst_max", 0.50)
  pf_vr_max = optimizer_config.get("prefilter_vr_max", 1.0)
  pf_min_cr = optimizer_config.get("prefilter_min_crossing_rate", 0.05)
  bm_dsr_gate = optimizer_config.get("benchmark_dsr_gate", False)
  bm_consistency_blend = optimizer_config.get("benchmark_consistency_blend", 0.0)
  bm_fragility_blend = optimizer_config.get("benchmark_fragility_blend", 0.0)
  bm_wr_harmony_blend = optimizer_config.get("benchmark_wr_harmony_blend", 0.0)
  ac_post = optimizer_config.get("ac_postfilter", False)
  ac_post_type = optimizer_config.get("ac_postfilter_type", "trend")
  ac_lag = optimizer_config.get("ac_lag", 1)

  pool_per_batch = optimizer_config.get("pool_per_batch", 0)
  # run_batch_map: maps run_id → batch_id so we can build batch-tagged daily PnL
  # (keeps same-symbol assets from different strategy batches separate, like Cross-Strategy)
  run_batch_map: dict[int, int] = optimizer_config.get("_run_batch_map", {})
  has_multiple_batches = len(set(run_batch_map.values())) >= 2

  window_results: list[dict] = []

  for w in windows:
    _wt0 = _time.perf_counter()
    log.info("── Window %d ──  IS: %s → %s", w["window_num"], w["is_start"], w["is_end"])

    # --- IS boundary: filter trades strictly within IS period ---
    is_trades = filter_trades_strict(all_trades, w["is_start"], w["is_end"])
    assert_trades_within_boundary(is_trades, w["is_end"], context=f"window {w['window_num']} IS trades")

    # Boundary clipping: force-close straddling trades at IS end close price
    _clip_is = optimizer_config.get("clip_is", True)
    _clip_tf = optimizer_config.get("_timeframe")
    if _clip_is and db is not None and _clip_tf:
      is_trades = clip_is_trades(is_trades, w["is_end"], _clip_tf, db)
      log.info("  [1] IS trades: %d (from %d total), clip_is=True",
               len(is_trades), len(all_trades))
    else:
      log.info("  [1] IS trades: %d (from %d total)%s", len(is_trades), len(all_trades),
               "" if _clip_is else " (clip_is=False)")

    # --- Session Filter: apply time-of-day / day-of-week filtering ---
    # Keep unfiltered IS trades for the grade toggle (grade_on_cropped=False)
    _is_trades_unfiltered = is_trades  # reference (not copy) — only used if grade toggle OFF
    _window_mask_data: dict | None = None  # mask for this window (for OOS + results)
    _sf_auto_stats: dict | None = None     # auto mode stats (for results)

    if sf_enabled and is_trades:
      from backend.analysis.session_mask_filter import apply_session_mask, build_fixed_mask

      if sf_mode == "fixed" and (sf_exclude_days or sf_exclude_hours):
        _window_mask_data = build_fixed_mask(sf_exclude_days, sf_exclude_hours)
        is_trades = apply_session_mask(is_trades, _window_mask_data)
        log.info("  [1b] Session filter (fixed): %d → %d trades (excl days=%s, hours=%s)",
                 len(_is_trades_unfiltered), len(is_trades), sf_exclude_days, sf_exclude_hours)

      elif sf_mode == "auto":
        from backend.analysis.session_auto_mask import compute_auto_mask
        _sf_auto_stats = compute_auto_mask(is_trades, sf_auto_min_trades, sf_auto_include_insuf)
        _window_mask_data = _sf_auto_stats["mask_data"]
        is_trades = apply_session_mask(is_trades, _window_mask_data)
        log.info("  [1b] Session filter (auto): %d → %d trades (kept hours=%s, kept days=%s)",
                 len(_is_trades_unfiltered), len(is_trades),
                 _sf_auto_stats["kept_hours"], _sf_auto_stats["kept_days"])

      elif sf_mode == "saved_mask" and sf_saved_mask_id and db is not None:
        import json as _json
        from backend.db.models import SessionTimeMask
        mask_obj = db.query(SessionTimeMask).filter(SessionTimeMask.id == sf_saved_mask_id).first()
        if mask_obj:
          _window_mask_data = _json.loads(mask_obj.mask_data)
          is_trades = apply_session_mask(is_trades, _window_mask_data)
          log.info("  [1b] Session filter (saved mask '%s'): %d → %d trades",
                   mask_obj.name, len(_is_trades_unfiltered), len(is_trades))

    # Build daily PnL — batch-tagged when multiple batches (matches Cross-Strategy)
    if has_multiple_batches:
      daily_pnl = _build_batch_tagged_daily_pnl(is_trades, w["is_end"], run_batch_map)
    else:
      daily_pnl = build_is_daily_pnl(is_trades, w["is_end"], include_side=needs_side)
    _n_before_qf = daily_pnl.shape[1]
    _n_longs_raw = sum(1 for c in daily_pnl.columns if c.endswith("_Long"))
    _n_shorts_raw = sum(1 for c in daily_pnl.columns if c.endswith("_Short"))
    log.info("  [2] Daily PnL: %d rows × %d cols (%dL %dS), batch_tagged=%s",
             daily_pnl.shape[0], daily_pnl.shape[1], _n_longs_raw, _n_shorts_raw,
             has_multiple_batches)

    # Quality filters (min trades, min PF, min WR) — IS data only
    if min_trades > 0 or min_pf > 0 or min_wr > 0:
      from backend.api.routes.analysis import _filter_daily_pnl
      daily_pnl = _filter_daily_pnl(
        daily_pnl, is_trades, min_trades, min_pf, min_wr,
        include_side=needs_side,
        run_batch_map=run_batch_map if has_multiple_batches else None,
      )
      assert_no_oos_leakage(daily_pnl, w["is_end"], context="quality filters")
    log.info("  [3] After quality filters: %d cols (removed %d)",
             daily_pnl.shape[1], _n_before_qf - daily_pnl.shape[1])

    # Pre-filter: remove structurally unsuitable assets (mirrors optimizer)
    if pf_enabled and daily_pnl.shape[1] > 0:
      _n_before_pf = daily_pnl.shape[1]
      if pf_mode == "trend":
        from backend.analysis.seed_ranker import prefilter_for_trend
        survivors = prefilter_for_trend(daily_pnl, pf_hurst_min, pf_ter_pct)
      else:
        from backend.analysis.seed_ranker import prefilter_for_meanrev
        survivors = prefilter_for_meanrev(daily_pnl, pf_hurst_max, pf_vr_max, pf_min_cr)
      daily_pnl = daily_pnl[[c for c in daily_pnl.columns if c in survivors]]
      assert_no_oos_leakage(daily_pnl, w["is_end"], context="prefilter")
      log.info("  [3b] Pre-filter (%s): %d → %d cols", pf_mode, _n_before_pf, daily_pnl.shape[1])

    # Benchmark robustness — IS data only
    external_scores = None
    cur_seeds = list(seed_metrics)
    if bm_enabled:
      log.info("  [4] Computing benchmark robustness...")
      if has_multiple_batches:
        # Use cross-strategy benchmark (supports batch-tagged column names)
        from backend.api.routes.cross_strategy import _cross_prepare_benchmark
        daily_pnl, ext = _cross_prepare_benchmark(
          is_trades, daily_pnl, bm_source, bm_split,
          bm_threshold, bm_elite, bm_exclude,
          run_batch_map=run_batch_map,
          consistency_blend=bm_consistency_blend,
          fragility_blend=bm_fragility_blend,
          wr_harmony_blend=bm_wr_harmony_blend,
          dsr_gate=bm_dsr_gate,
        )
      else:
        from backend.api.routes.analysis import _prepare_benchmark
        daily_pnl, ext = _prepare_benchmark(
          is_trades, daily_pnl, bm_source, bm_split,
          bm_threshold, bm_elite, bm_exclude,
          consistency_blend=bm_consistency_blend,
          fragility_blend=bm_fragility_blend,
          wr_harmony_blend=bm_wr_harmony_blend,
          dsr_gate=bm_dsr_gate,
          daily_pnl_full=daily_pnl,
        )
      assert_no_oos_leakage(daily_pnl, w["is_end"], context="benchmark")
      external_scores = ext
      if "benchmark" not in cur_seeds:
        cur_seeds = [*cur_seeds, "benchmark"]
      log.info("  [4] Benchmark done → %d cols remain, %d scored",
               daily_pnl.shape[1], len(ext.get("benchmark", {})))
    else:
      log.info("  [4] Benchmark: SKIPPED")

    # Recency-weighted seed scoring — IS data only
    if rec_enabled and is_trades:
      log.info("  [5] Computing recency scores...")
      from backend.analysis.optimizer_seeds import compute_recency_seed_scores
      from backend.analysis.wfo_oos_eval import _trade_to_key
      trade_df = pd.DataFrame([
        {"Asset": _trade_to_key(t, needs_side, run_batch_map if has_multiple_batches else None),
         "Exit Time": t.exit_time, "Net Profit": t.net_profit}
        for t in is_trades if t.exit_time is not None and t.net_profit is not None
      ])
      if not trade_df.empty:
        if external_scores is None:
          external_scores = {}
        external_scores["recency"] = compute_recency_seed_scores(
          trade_df, mode=rec_mode, batch_size=rec_batch, boost_factor=rec_boost,
        )
        if "recency" not in cur_seeds:
          cur_seeds = [*cur_seeds, "recency"]
      log.info("  [5] Recency done → %d assets scored", len(external_scores.get("recency", {})))
    else:
      log.info("  [5] Recency: SKIPPED")

    # Event-based seed scoring — IS data only
    if ev_enabled and is_trades:
      log.info("  [5b] Computing event-based scores...")
      from backend.api.routes.cross_strategy import _compute_event_external_scores
      event_ext = _compute_event_external_scores(
        is_trades, ev_enabled, ev_n_trades, ev_staleness,
        ev_per_batch, list(cur_seeds), asset_batch_map,
        include_side=needs_side,
        date_from=w.get("is_start"),
        run_batch_map=run_batch_map if has_multiple_batches else None,
      )
      if event_ext:
        if external_scores is None:
          external_scores = {}
        external_scores = {**external_scores, **event_ext}
      log.info("  [5b] Event scoring done → %d metrics", len(event_ext))
    else:
      log.info("  [5b] Event scoring: SKIPPED")

    # Pool-per-batch: ensure each batch gets fair representation (mirrors Cross-Strategy)
    # Works with both batch-tagged columns (~B{id}) and asset_batch_map
    if (has_multiple_batches or asset_batch_map) and not daily_pnl.empty:
      import re

      from backend.analysis.optimizer_seeds import compute_seed_scores
      all_cols = daily_pnl.columns.tolist()
      pool_scores = compute_seed_scores(
        daily_pnl, all_cols, cur_seeds, external_scores,
        metric_weights=seed_weights,
      )
      # Match Cross-Strategy: in split mode, target = n_longs + n_shorts
      if split_mode:
        target = optimizer_config.get("n_longs", 3) + optimizer_config.get("n_shorts", 3)
      else:
        target = n_assets
      ppb = pool_per_batch if pool_per_batch > 0 else max(target * 2, max_seeds)

      batch_groups: dict[int, list[str]] = {}
      keep_pool: set[str] = set()
      for a in all_cols:
        # Try extracting batch_id from batch-tagged column name (SYMBOL~B{id}_Side)
        m = re.search(r"~B(\d+)_", a)
        if m:
          bid = int(m.group(1))
          batch_groups.setdefault(bid, []).append(a)
        elif asset_batch_map:
          # Fallback: use asset_batch_map for non-tagged columns
          bid_val = asset_batch_map.get(a)
          if bid_val is not None:
            batch_groups.setdefault(bid_val, []).append(a)
          else:
            keep_pool.add(a)
        else:
          keep_pool.add(a)  # No batch info → auto-include

      for _bid, group_assets in batch_groups.items():
        ranked = sorted(group_assets, key=lambda a: pool_scores.get(a, float("inf")))
        keep_pool.update(ranked[:ppb])

      pool_cols = [c for c in daily_pnl.columns if c in keep_pool]
      log.info("  [6] Pool-per-batch: ppb=%d, %d batches, %d/%d kept",
               ppb, len(batch_groups), len(pool_cols), len(all_cols))
      if len(pool_cols) >= 2:
        daily_pnl = daily_pnl[pool_cols]
        assert_no_oos_leakage(daily_pnl, w["is_end"], context="pool-per-batch")

    is_symbols = daily_pnl.shape[1] if not daily_pnl.empty else 0
    _n_longs_pool = sum(1 for c in daily_pnl.columns if c.endswith("_Long")) if not daily_pnl.empty else 0
    _n_shorts_pool = sum(1 for c in daily_pnl.columns if c.endswith("_Short")) if not daily_pnl.empty else 0

    # Count how many pool assets have PF >= 1.0 (optimizer hard gate)
    _pf_pass = 0
    if not daily_pnl.empty:
      for _col in daily_pnl.columns:
        if _asset_pf(daily_pnl[_col]) >= 1.0:
          _pf_pass += 1
    log.info("  [7] PRE-OPTIMIZER: pool=%d (%dL %dS), PF≥1.0=%d/%d, seeds=%d, rows=%d",
             is_symbols, _n_longs_pool, _n_shorts_pool, _pf_pass, is_symbols,
             max_seeds, daily_pnl.shape[0] if not daily_pnl.empty else 0)
    log.info("  [7] Seed metrics=%s, objective=%s, split=%s (%.1fs elapsed)",
             cur_seeds, objective, split_mode, _time.perf_counter() - _wt0)
    candidates_data: list[dict] = []

    # Batch-tagged columns (SYMBOL~B{id}_Side) carry their own batch id, but
    # the route's asset_batch_map is keyed SYMBOL_Side and, when batches share
    # symbols, holds only the first batch. Read the id from the tag (as the
    # pool-per-batch step does) so the directional and manual paths see every
    # batch's assets. Fixed 2026-10-08: both modes returned no candidates.
    window_abm = asset_batch_map
    if has_multiple_batches and not daily_pnl.empty:
      window_abm = dict(asset_batch_map)
      for _col in daily_pnl.columns:
        _m = _BATCH_TAG_RE.search(_col)
        if _m:
          window_abm[_col] = int(_m.group(1))

    raw: list[dict] = []
    _opt_t0 = _time.perf_counter()

    if is_symbols >= 2 and wfo_mode == "directional" and l2_groups:
      # Directional batches mode: beam search optimization
      from backend.analysis.beam_optimizer import run_beam_optimization
      # Position-count constraints for beam search
      beam_tc_config = None
      beam_tc_counts = None
      if tc_low > 0 or tc_high > 0:
        from backend.api.routes.analysis import _compute_trade_counts
        beam_tc_counts = _compute_trade_counts(is_trades, include_side=needs_side)
        beam_tc_config = {
          "low_threshold": tc_low, "max_low": optimizer_config.get("tc_max_low", 99),
          "high_threshold": tc_high, "min_high": optimizer_config.get("tc_min_high", 0),
        }
      raw = run_beam_optimization(
        daily_pnl, window_abm, l2_groups,
        n_per_batch, obj_l1, obj_l2, obj_l3,
        k_l1, k_l2, k_l3, corr_method,
        cur_seeds, max_seeds, prioritize_stability, external_scores,
        tc_config=beam_tc_config, trade_counts=beam_tc_counts,
        l1_quality_gate=optimizer_config.get("l1_quality_gate", False),
        n_per_group=optimizer_config.get("n_per_group", 15),
      )

    elif is_symbols >= 2 and wfo_mode == "manual" and len(manual_batch_ids) >= 2:
      # Manual mode: 3-layer optimization per window
      raw = _run_manual_optimization(
        daily_pnl, window_abm, strategy_map,
        manual_batch_ids, optimizer_config, corr_method,
        n_candidates, cur_seeds, max_seeds, prioritize_stability,
        all_trades=is_trades, div_algo=div_algo,
      )

    elif is_symbols >= 2:
      # Clamp requested assets to what's available in this window
      effective_n = min(n_assets, is_symbols)
      assert_no_oos_leakage(daily_pnl, w["is_end"], context="auto optimizer")
      _corr_t0 = _time.perf_counter()
      corr = get_correlation_matrix(daily_pnl, corr_method)
      log.info("  [8] Correlation matrix: %dx%d (%.2fs)",
               corr.shape[0], corr.shape[1], _time.perf_counter() - _corr_t0)

      if split_mode:
        # Clamp longs/shorts to available per side
        all_cols = daily_pnl.columns.tolist()
        n_long_avail = sum(1 for c in all_cols if c.endswith("_Long"))
        n_short_avail = sum(1 for c in all_cols if c.endswith("_Short"))
        eff_longs = min(optimizer_config.get("n_longs", 3), max(n_long_avail, 1))
        eff_shorts = min(optimizer_config.get("n_shorts", 3), max(n_short_avail, 1))
        log.info("  [9] Running SPLIT optimizer: %dL/%dS, within=%s, combined=%s",
                 eff_longs, eff_shorts,
                 optimizer_config.get("within_objective", "uncorrelated"),
                 optimizer_config.get("combined_objective", "stability"))

        if tc_split_active:
          from backend.api.routes.analysis import _compute_trade_counts
          tc_counts = _compute_trade_counts(is_trades, include_side=True)
          raw = diversify_split_portfolio(
            div_algo, daily_pnl, corr,
            n_longs=eff_longs,
            n_shorts=eff_shorts,
            within_objective=optimizer_config.get("within_objective", "uncorrelated"),
            combined_objective=optimizer_config.get("combined_objective", "stability"),
            n_portfolios=n_candidates,
            seed_metrics=cur_seeds, max_seeds=max_seeds,
            prioritize_stability=prioritize_stability,
            external_scores=external_scores,
            trade_counts=tc_counts,
            tc_longs=tc_longs_cfg, tc_shorts=tc_shorts_cfg,
            max_dd_pct=max_dd_pct,
            seed_weights=seed_weights, max_overlap=max_overlap,
            expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
          )
        else:
          raw = diversify_split_portfolio(
            div_algo, daily_pnl, corr,
            n_longs=eff_longs,
            n_shorts=eff_shorts,
            within_objective=optimizer_config.get("within_objective", "uncorrelated"),
            combined_objective=optimizer_config.get("combined_objective", "stability"),
            n_portfolios=n_candidates,
            seed_metrics=cur_seeds, max_seeds=max_seeds,
            prioritize_stability=prioritize_stability,
            external_scores=external_scores,
            max_dd_pct=max_dd_pct,
            seed_weights=seed_weights, max_overlap=max_overlap,
            expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
          )
      else:
        if tc_combined_active:
          from backend.api.routes.analysis import _compute_trade_counts
          tc_counts = _compute_trade_counts(is_trades)
          tc = {
            "low_threshold": tc_low, "max_low": optimizer_config.get("tc_max_low", 99),
            "high_threshold": tc_high, "min_high": optimizer_config.get("tc_min_high", 0),
          }
          raw = diversify_portfolio(
            div_algo, daily_pnl, corr, effective_n, objective,
            n_candidates, seed_metrics=cur_seeds, max_seeds=max_seeds,
            prioritize_stability=prioritize_stability,
            external_scores=external_scores,
            trade_counts=tc_counts, tc_constraints=tc,
            max_dd_pct=max_dd_pct,
            seed_weights=seed_weights, max_overlap=max_overlap,
            expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
          )
        else:
          raw = diversify_portfolio(
            div_algo, daily_pnl, corr, effective_n, objective,
            n_candidates, seed_metrics=cur_seeds, max_seeds=max_seeds,
            prioritize_stability=prioritize_stability,
            external_scores=external_scores,
            max_dd_pct=max_dd_pct,
            seed_weights=seed_weights, max_overlap=max_overlap,
            expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
          )

    log.info("  [9] Optimizer done → %d candidates (%.2fs optimizer, %.1fs window total)",
             len(raw), _time.perf_counter() - _opt_t0, _time.perf_counter() - _wt0)

    # ── Overfit Guard: walk-forward rerank (portfolio-level, gated, default OFF) ──
    # When enabled, splits IS into train/validation and re-ranks candidates by
    # validation-window stability. When disabled, this block is a no-op and the
    # run is byte-identical to today.
    _og_enabled = optimizer_config.get("overfit_guard_enabled", False)
    _of_wf_rerank = optimizer_config.get("of_wf_rerank_enabled", False)
    if (_og_enabled or _of_wf_rerank) and raw and not daily_pnl.empty and len(raw) > 1:
      try:
        from backend.analysis.overfit_guard import walk_forward_rerank
        _n_before = len(raw)
        _top_before = raw[0].get("assets", []) if raw else []
        raw = walk_forward_rerank(raw, daily_pnl, split_pct=0.70, metric="stability")
        _top_after = raw[0].get("assets", []) if raw else []
        log.info("  [9a] Overfit Guard WF rerank: %d candidates, top changed=%s",
                 _n_before, _top_before != _top_after)
      except Exception as _og_exc:
        log.warning("  [9a] Overfit Guard WF rerank failed (skipped): %s", _og_exc)

    # AC post-filter: flag mismatched assets per candidate (mirrors optimizer)
    if ac_post and raw:
      from backend.analysis.seed_ranker import postfilter_ac
      for c in raw:
        ac_results = postfilter_ac(daily_pnl, c.get("assets", []),
                                   strategy_type=ac_post_type, lag=ac_lag)
        c["ac_flags"] = [a for a, info in ac_results.items() if info["flagged"]]
        c["ac_scores"] = {a: info["ac"] for a, info in ac_results.items()}
      _flagged_total = sum(len(c.get("ac_flags", [])) for c in raw)
      log.info("  [9b] AC postfilter (%s, lag=%d): %d flagged across %d candidates",
               ac_post_type, ac_lag, _flagged_total, len(raw))

    # Evaluate candidates on OOS (shared for all modes)
    is_overlapping = time_mode == "overlapping" and w.get("oos_h1_start")

    # Use batch-tagged trade keys when multiple batches (matches Cross-Strategy)
    _rbm = run_batch_map if has_multiple_batches else None

    for rank, c in enumerate(raw[:n_candidates], 1):
      assets = c.get("assets", [])
      # Recompute IS metrics from trades (same methodology as OOS for fair comparison)
      from backend.analysis.wfo_oos_eval import _trade_to_key
      asset_set = set(assets)
      # Grade toggle: use filtered or unfiltered IS trades for IS metrics
      _is_source = is_trades if sf_grade_on_cropped or not _window_mask_data else _is_trades_unfiltered
      cand_is_trades = [t for t in _is_source if _trade_to_key(t, needs_side, _rbm) in asset_set]
      is_metrics = _compute_metrics(cand_is_trades)

      if is_overlapping:
        from backend.analysis.wfo_oos_eval import evaluate_oos_overlapping
        oos_result = evaluate_oos_overlapping(
          all_trades, w, assets, is_metrics, needs_side, _rbm,
          session_mask_data=_window_mask_data,
        )
      else:
        from backend.analysis.wfo_oos_eval import evaluate_oos_standard
        oos_result = evaluate_oos_standard(
          all_trades, w, assets, is_metrics, needs_side, _rbm,
          session_mask_data=_window_mask_data,
        )

      cand_data: dict = {
        "rank": rank, "assets": assets,
        "is_metrics": is_metrics,
        "oos_metrics": oos_result.get("oos_metrics"),
        "oos_wfe": oos_result.get("oos_wfe"),
        "oos_benchmark": oos_result.get("oos_benchmark"),
      }
      # Overlapping extras
      if is_overlapping:
        cand_data["oos_h1_metrics"] = oos_result.get("oos_h1_metrics")
        cand_data["oos_h2_metrics"] = oos_result.get("oos_h2_metrics")
        cand_data["wfe_h1"] = oos_result.get("wfe_h1")
        cand_data["degradation_ratio"] = oos_result.get("degradation_ratio")

      candidates_data.append(cand_data)

    # Selected count from top candidate (for display)
    selected_count = len(candidates_data[0]["assets"]) if candidates_data else 0

    win_entry: dict = {
      "window_num": w["window_num"],
      "is_start": w["is_start"], "is_end": w["is_end"],
      "oos_start": w.get("oos_start"), "oos_end": w.get("oos_end"),
      "is_trade_count": len({t.position_id or f"_solo_{t.id}" for t in is_trades}),
      "is_symbol_count": selected_count,
      "is_pool_size": is_symbols,
      "candidates": candidates_data,
    }
    # Session filter diagnostics (per-window)
    if _window_mask_data:
      sf_diag: dict = {
        "mode": sf_mode,
        "trades_before": len(_is_trades_unfiltered),
        "trades_after": len(is_trades),
        "grade_on_cropped": sf_grade_on_cropped,
      }
      if _sf_auto_stats:
        sf_diag["auto_kept_hours"] = _sf_auto_stats["kept_hours"]
        sf_diag["auto_excluded_hours"] = _sf_auto_stats["excluded_hours"]
        sf_diag["auto_kept_days"] = _sf_auto_stats["kept_days"]
        sf_diag["auto_excluded_days"] = _sf_auto_stats["excluded_days"]
      win_entry["session_filter"] = sf_diag
    # Pass through H1/H2 dates for overlapping
    if is_overlapping:
      win_entry["oos_h1_start"] = w.get("oos_h1_start")
      win_entry["oos_h1_end"] = w.get("oos_h1_end")
      win_entry["oos_h2_start"] = w.get("oos_h2_start")
      win_entry["oos_h2_end"] = w.get("oos_h2_end")

    window_results.append(win_entry)

  # Use new aggregation module
  from backend.analysis.wfo_aggregation import (
    aggregate_summary,
  )
  from backend.analysis.wfo_aggregation import (
    compile_tracks as _compile_tracks_new,
  )
  from backend.analysis.wfo_aggregation import (
    cumulative_metrics as _cumulative_new,
  )

  tracks = _compile_tracks_new(window_results, n_candidates, all_trades, time_mode)
  cum_is = _cumulative_new(window_results, "is")
  cum_oos = _cumulative_new(window_results, "oos")
  summary = aggregate_summary(window_results, time_mode)

  log.info("═══ WFAS DONE ═══  %d windows processed, %.1fs total",
           len(window_results), _time.perf_counter() - _t0)

  result: dict = {
    "windows": window_results,
    "tracks": tracks,
    "cumulative_is": cum_is,
    "cumulative_oos": cum_oos,
    "summary": summary,
  }

  # Overlapping mode: add H1/H2 cumulative metrics
  if time_mode == "overlapping":
    result["cumulative_oos_h1"] = _cumulative_new(window_results, "oos_h1")
    result["cumulative_oos_h2"] = _cumulative_new(window_results, "oos_h2")

  # Health check: flag degradation patterns
  from backend.analysis.wfo_health_check import wfo_health_check
  result["health_check"] = wfo_health_check(window_results)

  return result
