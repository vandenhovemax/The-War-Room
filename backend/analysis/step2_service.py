"""Step-2 Service — module-level callable for the hybrid walk-forward pass.

Lifted verbatim from `backend.api.routes.wfo.py:_run_hybrid_step2` so the
live-loop nightly refresh can invoke Step 2 without owning an HTTP request.
Behavior-neutral: the route now delegates to this function.

Parity guardrail: `backend/tests/test_step2_extraction_parity.py` compares
this function's output against a saved snapshot of the old closure's output
for an identical input.

Inputs (all explicit — no closure captures):
  step1_result: dict   — the Phase-1 `run_batch_selection` result with
                         `windows: [{is_start, oos_start, oos_end,
                         candidates, window_num, ...}]`.
  all_trades:   list   — LightTrade-compatible rows (or BacktestTrade) that
                         cover the IS+OOS span of every window.
  run_batch_map: dict  — {run_id -> batch_id} so each trade can be mapped
                         to its source batch.
  config:       dict   — see KEYS list below. Matches the fields that used
                         to be pulled off the Pydantic request. Missing
                         keys fall back to the same defaults the closure
                         used (documented at each read site).
  db:           Session (optional) — only used when
                         config['category_filter_enabled'] is True, for
                         `get_all_categories(db)`. Pass None when the
                         category filter is off.

Returns:
  step2 result dict OR None when config['hybrid_mode'] is False.
  Shape matches the old closure's return exactly (see the 3 large fields
  at the bottom of the function for the full contract).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from backend.analysis.event_wfo import _compute_event_metrics, _window_aligned_risk_metrics, run_event_wfo
from backend.analysis.wfo_event_dedup import EventAggregateState, dedup_and_rebuild

log = logging.getLogger(__name__)


# Keys read from the `config` dict. Defaults match the old closure's reads.
_CONFIG_KEYS = (
  "hybrid_mode",
  "hybrid_n_trades", "hybrid_n_assets", "hybrid_min_pf", "hybrid_min_wr",
  "hybrid_staleness_days", "hybrid_rebalance_every", "hybrid_rebalance_mode",
  "hybrid_warmup_trades", "hybrid_min_hold", "hybrid_scoring_method",
  "hybrid_objective", "hybrid_composite_weights", "hybrid_exec_day_profit",
  "hybrid_sample_count_floor",
  "hybrid_rescue_enabled", "hybrid_rescue_quota_pct",
  "hybrid_rescue_min_trades", "hybrid_rescue_min_score",
  "hybrid_rescue_confidence_gap",
  "session_filter_enabled", "session_filter_mode",
  "session_filter_exclude_hours", "session_filter_exclude_days",
  "category_filter_enabled", "category_influence", "category_lookback_trades",
  "category_max_per_cat", "category_score_metric",
  "category_mode", "category_streak_required", "category_fire_multiplier",
  "category_fire_duration_trades",
  "oos_months",
  "preset_id",
)


def run_step2(
  step1_result: dict,
  all_trades: list,
  run_batch_map: dict[int, int],
  config: dict,
  db: Any = None,
) -> dict | None:
  """Per-OOS-window Step 2 (event_wfo composite) on each window's Step 1
  winners. Returns step2_result dict or None if hybrid_mode disabled.
  """
  if not config.get("hybrid_mode"):
    return None

  oos_windows = [w for w in step1_result.get("windows", []) if w.get("oos_start")]
  if not oos_windows:
    return None

  # Bug #1 guard: detect overlapping OOS ranges (static / overlapping modes).
  # Rolling mode is safe by construction (step == oos_months).
  _ovl = False
  _sorted = sorted(oos_windows, key=lambda x: (x.get("oos_start") or ""))
  for _i in range(1, len(_sorted)):
    if (_sorted[_i].get("oos_start") or "") <= (_sorted[_i-1].get("oos_end") or ""):
      _ovl = True
      break
  if _ovl:
    log.warning(
      "Hybrid Step 2: overlapping OOS windows detected (static/overlapping mode). "
      "Per-trade dedup will be applied to aggregate results."
    )

  # Load category map once for the whole hybrid run (cheap, ~40 rows).
  # Empty dict when feature is disabled to keep the engine in passthrough.
  _categories_map_for_event_wfo: dict[str, list[str]] = {}
  if config.get("category_filter_enabled") and db is not None:
    try:
      from backend.services.category_service import get_all_categories
      _categories_map_for_event_wfo = {
        k.upper(): v for k, v in get_all_categories(db).items() if v
      }
    except Exception as _cat_err:
      log.warning(
        "Category Filter: failed to load symbol_categories (%s) — running passthrough",
        _cat_err,
      )

  base_step2_config = {
    "n_trades": config.get("hybrid_n_trades", 10),
    "n_assets": config.get("hybrid_n_assets", 5),
    "min_pf": config.get("hybrid_min_pf", 1.0),
    "min_wr": config.get("hybrid_min_wr", 0.5),
    "staleness_days": config.get("hybrid_staleness_days", 30),
    "rebalance_every": config.get("hybrid_rebalance_every", 5),
    "rebalance_mode": config.get("hybrid_rebalance_mode", "fixed"),
    "warmup_trades": config.get("hybrid_warmup_trades", 0),
    "min_hold": config.get("hybrid_min_hold", 0),
    "scoring_method": config.get("hybrid_scoring_method", "composite"),
    "objective": config.get("hybrid_objective", "net_profit"),
    "composite_weights": config.get("hybrid_composite_weights", {}),
    "exec_day_profit": config.get("hybrid_exec_day_profit", False),
    "session_filter_enabled": config.get("session_filter_enabled", False),
    "session_filter_mode": config.get("session_filter_mode", "exclude"),
    "session_filter_exclude_hours": config.get("session_filter_exclude_hours", []),
    "session_filter_exclude_days": config.get("session_filter_exclude_days", []),
    # Feature 2: sample floor
    "hybrid_sample_count_floor": config.get("hybrid_sample_count_floor", 0),
    # Feature 5-6: rescue system
    "hybrid_rescue_enabled": config.get("hybrid_rescue_enabled", False),
    "hybrid_rescue_quota_pct": config.get("hybrid_rescue_quota_pct", 0.0),
    "hybrid_rescue_min_trades": config.get("hybrid_rescue_min_trades", 30),
    "hybrid_rescue_min_score": config.get("hybrid_rescue_min_score", 0.0),
    "hybrid_rescue_confidence_gap": config.get("hybrid_rescue_confidence_gap", 0.0),
    # Category Filter
    "category_filter_enabled": config.get("category_filter_enabled", False),
    "category_influence": config.get("category_influence", 0.0),
    "category_lookback_trades": config.get("category_lookback_trades", 20),
    "category_max_per_cat": config.get("category_max_per_cat", 0),
    "category_score_metric": config.get("category_score_metric", "wr"),
    # On-fire-streak event mode (2026-04-17-B; defaults preserve legacy)
    "category_mode": config.get("category_mode", "soft_weight"),
    "category_streak_required": config.get("category_streak_required", 3),
    "category_fire_multiplier": config.get("category_fire_multiplier", 1.0),
    "category_fire_duration_trades": config.get("category_fire_duration_trades", 10),
    "_categories_map": _categories_map_for_event_wfo,
  }

  per_window_step2: list[dict] = []
  agg_equity_curve: list[float] = []
  agg_equity_dates: list[str] = []
  agg_pnl_deltas: list[float] = []
  agg_selected_pnls: list[float] = []
  agg_selected_records: list[dict] = []
  agg_selection_changes: list[dict] = []
  agg_total_steps = 0
  agg_total_trades = 0
  agg_all_trade_count = 0
  agg_warmup_trades = 0
  agg_rebalance_count = 0
  agg_asset_freq: dict[str, dict] = {}
  running_equity = 0.0
  all_winning_batches: set = set()

  for w in oos_windows:
    cands = w.get("candidates", [])
    w_winners = cands[0].get("selected_batches", []) if cands else []
    if not w_winners:
      per_window_step2.append({
        "window_num": w.get("window_num"), "oos_start": w.get("oos_start"),
        "oos_end": w.get("oos_end"), "winning_batches": [],
        "summary": {}, "skipped": "no_winners",
      })
      continue
    all_winning_batches.update(w_winners)
    wset = set(w_winners)
    try:
      is_dt = datetime.fromisoformat(w["is_start"])
      oe_dt = datetime.fromisoformat(w["oos_end"]) + timedelta(days=1) - timedelta(seconds=1)
    except Exception:
      continue
    # Bug #2 guard: gate window membership by ENTRY time (symmetric with
    # Phase 1). Trades whose entry falls inside [is_start .. oos_end] are
    # included even if they exit after oe_dt — prevents silent drop of
    # trailing trades in the final OOS window.
    sub_trades = [
      t for t in all_trades
      if run_batch_map.get(t.run_id) in wset
      and t.entry_time is not None
      and t.exit_time is not None
      and is_dt <= t.entry_time <= oe_dt
    ]
    if not sub_trades:
      per_window_step2.append({
        "window_num": w.get("window_num"), "oos_start": w.get("oos_start"),
        "oos_end": w.get("oos_end"), "winning_batches": w_winners,
        "summary": {}, "skipped": "no_trades",
      })
      continue
    cfg = dict(base_step2_config)
    cfg["date_from"] = w.get("oos_start", "")
    # Per-(batch, asset) scoring: each batch's version of a symbol gets
    # its own independent lookback buffer. Without this map, event_wfo
    # pools all batches under one SYMBOL_Side key (old behaviour).
    cfg["_run_batch_map"] = run_batch_map

    # Feature 5: Build rescue_trades from Phase-1-rejected batches
    if base_step2_config.get("hybrid_rescue_enabled", False):
      all_batch_ids = set(run_batch_map.values())
      rejected_batch_ids = all_batch_ids - wset
      rescue_trades = [
        t for t in all_trades
        if run_batch_map.get(t.run_id) in rejected_batch_ids
        and t.entry_time is not None
        and t.exit_time is not None
        and is_dt <= t.entry_time <= oe_dt
      ]
      cfg["rescue_trades"] = rescue_trades

    try:
      w_step2 = run_event_wfo(sub_trades, cfg)
    except Exception as _s2err:
      log.warning("Hybrid Step 2 window %s failed: %s", w.get("window_num"), _s2err)
      continue
    sm = w_step2.get("summary", {}) or {}
    w_trades = int(sm.get("total_trades", 0) or 0)
    agg_total_trades += w_trades
    agg_total_steps += int(sm.get("total_steps", 0) or 0)
    agg_selected_pnls.extend([float(p) for p in (w_step2.get("selected_pnls") or [])])
    agg_selected_records.extend(w_step2.get("selected_records") or [])
    for _chg in (w_step2.get("selection_changes") or []):
      agg_selection_changes.append({
        "date": _chg.get("date", ""),
        "added": list(_chg.get("added") or []),
        "removed": list(_chg.get("removed") or []),
        "window_num": w.get("window_num"),
      })
    agg_all_trade_count += int(w_step2.get("all_trade_count", 0) or 0)
    agg_warmup_trades += int(w_step2.get("warmup_trades", 0) or 0)
    agg_rebalance_count += int(w_step2.get("rebalance_count", 0) or 0)
    for a, fd in (w_step2.get("asset_frequency") or {}).items():
      slot = agg_asset_freq.setdefault(a, {"selected": 0, "eligible": 0})
      slot["selected"] += int(fd.get("selected", 0) or 0)
      slot["eligible"] += int(fd.get("eligible", 0) or 0)
    w_eq = w_step2.get("equity_curve", []) or []
    w_dates = w_step2.get("equity_dates", []) or []
    prev = 0.0
    for ev, dv in zip(w_eq, w_dates):
      ev_f = float(ev)
      agg_pnl_deltas.append(round(ev_f - prev, 6))
      prev = ev_f
      agg_equity_curve.append(round(running_equity + ev_f, 2))
      agg_equity_dates.append(dv)
    if w_eq:
      running_equity += float(w_eq[-1])
    per_window_step2.append({
      "window_num": w.get("window_num"), "oos_start": w.get("oos_start"),
      "oos_end": w.get("oos_end"), "winning_batches": w_winners,
      "summary": sm,
      # End-of-stream graded per-asset composite scores from this window
      # (consumed by hybrid_portfolio_refresh to build weighted KellyAllocation).
      "asset_scores_at_end": dict(w_step2.get("asset_scores_at_end") or {}),
      "current_portfolio_at_end": list(w_step2.get("current_portfolio_at_end") or []),
      # T-002e': end-of-window per-asset state (buffers, locks, rescue
      # buffers) so the next hybrid refresh can seed run_event_wfo via
      # `config["_seed_state"]` instead of rebuilding from scratch. Only the
      # last window's snapshot is used for continuation; earlier-window
      # snapshots are harmless filler that ride the same payload.
      "final_state_snapshot": w_step2.get("final_state_snapshot"),
    })

  n_done = sum(1 for x in per_window_step2 if not x.get("skipped") and x.get("summary"))

  # Bug #1 fix (HARDENED 2026-04-08): dedup per-trade records by
  # (asset, dt) so the same executed trade isn't counted twice across
  # overlapping windows. Delegated to `dedup_and_rebuild()` which:
  #   • Runs UNCONDITIONALLY (safety net, not gated on _ovl)
  #   • Rebuilds equity curve from deduped records (Issue A)
  #   • Rebuilds asset_frequency: exact `selected`, scaled `eligible` (Issue B)
  #   • Scales ancillary counters (steps/rebalances/all_trade/warmup) (Issue C)
  #   • Asserts pnls/records length invariant + logs curve/pnls mismatch
  if agg_selected_records:
    _dedup_state = EventAggregateState(
      selected_records=agg_selected_records,
      selected_pnls=agg_selected_pnls,
      equity_curve=agg_equity_curve,
      equity_dates=agg_equity_dates,
      pnl_deltas=agg_pnl_deltas,
      asset_freq=agg_asset_freq,
      total_steps=agg_total_steps,
      total_trades=agg_total_trades,
      rebalance_count=agg_rebalance_count,
      all_trade_count=agg_all_trade_count,
      warmup_trades=agg_warmup_trades,
      running_equity=running_equity,
    )
    dedup_and_rebuild(_dedup_state, overlap_flag=_ovl)
    agg_selected_records = _dedup_state.selected_records
    agg_selected_pnls = _dedup_state.selected_pnls
    agg_equity_curve = _dedup_state.equity_curve
    agg_equity_dates = _dedup_state.equity_dates
    agg_pnl_deltas = _dedup_state.pnl_deltas
    agg_asset_freq = _dedup_state.asset_freq
    agg_total_steps = _dedup_state.total_steps
    agg_total_trades = _dedup_state.total_trades
    agg_rebalance_count = _dedup_state.rebalance_count
    agg_all_trade_count = _dedup_state.all_trade_count
    agg_warmup_trades = _dedup_state.warmup_trades
    running_equity = _dedup_state.running_equity

  # Aggregate metrics from REAL selected trade PnLs.
  agg_summary = _compute_event_metrics(agg_selected_pnls) if agg_selected_pnls else {}
  agg_summary["avg_per_window"] = round(agg_summary.get("net_profit", 0) / n_done, 2) if n_done else 0
  _oos_mo = max(1, int(config.get("oos_months", 3) or 3))
  agg_summary["avg_per_quarter"] = round(
    agg_summary.get("net_profit", 0) / n_done * (3.0 / _oos_mo), 2
  ) if n_done else 0
  agg_summary["total_trades"] = len(agg_selected_pnls)
  agg_summary["total_steps"] = agg_total_steps
  _per_win_mdds = [
    float(x.get("summary", {}).get("max_dd", 0) or 0)
    for x in per_window_step2
    if not x.get("skipped") and x.get("summary")
  ]
  agg_summary["max_window_mdd"] = round(max(_per_win_mdds), 2) if _per_win_mdds else 0
  agg_summary["avg_portfolio_size"] = round(
    sum(float(x.get("summary", {}).get("avg_portfolio_size", 0) or 0) for x in per_window_step2 if not x.get("skipped")) / n_done, 1
  ) if n_done else 0
  # Align sortino/calmar/recovery/ulcer to the SAME per-window PnL series
  # Phase 1 uses, so both surfaces measure the same thing for the same run.
  # Default _compute_event_metrics uses per-trade PnL which produces artificially
  # low ratios (see _window_aligned_risk_metrics docstring).
  _win_pnls = [
    float(x.get("summary", {}).get("net_profit", 0) or 0)
    for x in per_window_step2
    if not x.get("skipped") and x.get("summary")
  ]
  _win_aligned = _window_aligned_risk_metrics(_win_pnls)
  if _win_aligned:
    agg_summary.update(_win_aligned)
  log.info(
    "Hybrid Step 2 walk-forward: %d/%d windows, %d trades, net=$%.0f mdd=$%.0f pf=%.2f wr=%.1f",
    n_done, len(oos_windows), agg_total_trades,
    agg_summary.get("net_profit", 0), agg_summary.get("max_dd", 0),
    agg_summary.get("pf", 0), agg_summary.get("wr", 0),
  )
  for a in agg_asset_freq:
    elig = agg_asset_freq[a]["eligible"]
    sel = agg_asset_freq[a]["selected"]
    agg_asset_freq[a]["pct"] = round(sel / elig * 100, 1) if elig > 0 else 0

  # ── Phase 2 aggregates (mirror Phase 1 aggregate_oos_* shape) ──
  agg_p2_equity: list[dict] = []
  agg_p2_monthly: dict = {"years": [], "matrix": {}, "row_totals": {}, "col_totals": {}, "grand_total": 0}
  agg_p2_breakdown: list[dict] = []
  agg_p2_cumulative: dict = {}
  if agg_selected_records:
    recs = sorted(agg_selected_records, key=lambda r: r.get("dt", ""))
    cum = 0.0
    peak = 0.0
    daily_pnl: dict[str, float] = defaultdict(float)
    for r in recs:
      p = float(r.get("pnl", 0) or 0)
      cum += p
      peak = max(peak, cum)
      dd_pct = (cum - peak) / peak * 100 if peak > 0 else 0.0
      date_str = str(r.get("dt", ""))[:10]
      agg_p2_equity.append({"date": date_str, "equity": round(cum, 2), "drawdown_pct": round(dd_pct, 2)})
      daily_pnl[date_str] += p

    month_pnl: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for ds, p in daily_pnl.items():
      if len(ds) >= 7:
        y = ds[:4]; m = str(int(ds[5:7]))
        month_pnl[y][m] += p
    years = sorted(month_pnl.keys())
    matrix = {y: {m: round(v, 2) for m, v in month_pnl[y].items()} for y in years}
    row_totals = {y: round(sum(month_pnl[y].values()), 2) for y in years}
    col_totals: dict[str, float] = defaultdict(float)
    for y in years:
      for m, v in month_pnl[y].items():
        col_totals[m] += v
    agg_p2_monthly = {
      "years": years, "matrix": matrix,
      "row_totals": row_totals,
      "col_totals": {m: round(v, 2) for m, v in col_totals.items()},
      "grand_total": round(sum(row_totals.values()), 2),
    }

    bd: dict[str, dict] = {}
    for r in recs:
      k = r.get("asset") or ""
      p = float(r.get("pnl", 0) or 0)
      slot = bd.setdefault(k, {
        "key": k, "batch_id": 0,
        "symbol": r.get("symbol", ""),
        "side": r.get("side", ""),
        "timeframe": r.get("timeframe", ""),
        "group": "",
        "oos_pnl": 0.0, "oos_trades": 0, "oos_wins": 0,
        "oos_gp": 0.0, "oos_gl": 0.0,
        "is_pnl": 0.0, "is_trades": 0, "is_wr": 0, "is_pf": 0,
      })
      slot["oos_pnl"] += p
      slot["oos_trades"] += 1
      if p > 0:
        slot["oos_wins"] += 1
        slot["oos_gp"] += p
      else:
        slot["oos_gl"] += abs(p)
    for a in bd.values():
      t = a["oos_trades"]
      a["oos_wr"] = (a["oos_wins"] / t) if t else 0
      a["oos_pf"] = min(a["oos_gp"] / a["oos_gl"], 10) if a["oos_gl"] > 0 else (10 if a["oos_gp"] > 0 else 0)
      a["oos_pnl"] = round(a["oos_pnl"], 2)
    agg_p2_breakdown = sorted(bd.values(), key=lambda x: x["oos_pnl"], reverse=True)

    _sm = agg_summary
    _gp = sum(p for p in agg_selected_pnls if p > 0)
    _gl = abs(sum(p for p in agg_selected_pnls if p < 0))
    _wins = sum(1 for p in agg_selected_pnls if p > 0)
    _n = len(agg_selected_pnls)
    agg_p2_cumulative = {
      "net_profit": _sm.get("net_profit", 0),
      "profit_factor": _sm.get("pf", 0),
      "win_rate": round(_wins / _n, 4) if _n else 0,
      "total_trades": _n,
      "total_positions": _n,
      "max_drawdown": _sm.get("max_dd", 0),
      "cumulative_max_dd": _sm.get("max_dd", 0),
      "avg_trade": round((_sm.get("net_profit", 0) / _n), 2) if _n else 0,
      "sortino": _sm.get("sortino", 0),
      "sharpe": 0,
      "recovery_factor": _sm.get("recovery", 0),
      "calmar": _sm.get("calmar", 0),
      "gross_profit": round(_gp, 2),
      "gross_loss": round(_gl, 2),
      "n_wins": _wins,
      "n_losses": _n - _wins,
      "avg_win": round(_gp / _wins, 2) if _wins > 0 else 0,
      "avg_loss": round(_gl / (_n - _wins), 2) if (_n - _wins) > 0 else 0,
      "payoff_ratio": round((_gp / _wins) / (_gl / (_n - _wins)), 4)
      if _wins > 0 and (_n - _wins) > 0 and _gl > 0 else 0,
    }

  # Pick the latest window that actually produced scores. Later windows
  # carry the freshest grading of each asset's composite — that's what the
  # live-loop hybrid refresh turns into KellyAllocation weights.
  latest_asset_scores: dict[str, float] = {}
  latest_portfolio_at_end: list[str] = []
  for w_rec in reversed(per_window_step2):
    if w_rec.get("skipped"):
      continue
    sc = w_rec.get("asset_scores_at_end") or {}
    if sc:
      latest_asset_scores = dict(sc)
      latest_portfolio_at_end = list(w_rec.get("current_portfolio_at_end") or [])
      break

  return {
    "mode": "event", "walk_forward": True,
    "windows_processed": n_done, "windows_total": len(oos_windows),
    "latest_asset_scores": latest_asset_scores,
    "latest_portfolio_at_end": latest_portfolio_at_end,
    "equity_curve": agg_equity_curve, "equity_dates": agg_equity_dates,
    "all_trade_count": agg_all_trade_count,
    "selected_trade_count": agg_total_trades,
    "warmup_trades": agg_warmup_trades,
    "rebalance_count": agg_rebalance_count,
    "asset_frequency": agg_asset_freq,
    "selection_changes": sorted(agg_selection_changes, key=lambda c: c.get("date", "")),
    "summary": agg_summary, "per_window": per_window_step2,
    "winning_batches": sorted(all_winning_batches),
    "step2_config": base_step2_config,
    "aggregate_equity_curve": agg_p2_equity,
    "aggregate_monthly_matrix": agg_p2_monthly,
    "aggregate_breakdown": agg_p2_breakdown,
    "cumulative_metrics": agg_p2_cumulative,
    "selected_records": agg_selected_records,
    "config_used": {
      "n_trades": config.get("hybrid_n_trades"),
      "n_assets": config.get("hybrid_n_assets"),
      "min_hold": config.get("hybrid_min_hold"),
      "rebalance_every": config.get("hybrid_rebalance_every"),
      "rebalance_mode": config.get("hybrid_rebalance_mode"),
      "scoring_method": config.get("hybrid_scoring_method"),
      "objective": config.get("hybrid_objective"),
      "composite_weights": config.get("hybrid_composite_weights"),
      "exec_day_profit": config.get("hybrid_exec_day_profit"),
      "staleness_days": config.get("hybrid_staleness_days"),
      "min_pf": config.get("hybrid_min_pf"),
      "min_wr": config.get("hybrid_min_wr"),
      "warmup_trades": config.get("hybrid_warmup_trades"),
      "session_filter_exclude_days": config.get("session_filter_exclude_days"),
      "session_filter_exclude_hours_count": len(config.get("session_filter_exclude_hours") or []),
      "preset_id": config.get("preset_id"),
    },
  }


def build_config_from_request(req) -> dict:
  """Helper: project a Pydantic WFO request onto the flat `config` dict
  that `run_step2` expects. Used by the route so behavior is identical
  to the old inline closure.
  """
  return {k: getattr(req, k, None) for k in _CONFIG_KEYS}
