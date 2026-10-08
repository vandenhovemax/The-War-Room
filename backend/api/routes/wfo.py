"""Walk Forward Optimization routes — asset selection WFO."""
from __future__ import annotations

import json
import logging
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.rate_limit import rate_limit

log = logging.getLogger(__name__)

# ── In-memory WFO progress & result store ────────────────────
# Keyed by run_id (UUID string). Cleaned up after result is fetched.
_wfo_progress: dict[str, dict] = {}   # run_id → {step, total, label}
_wfo_results: dict[str, dict] = {}    # run_id → final result or {"error": msg}
_wfo_lock = threading.Lock()

from backend.analysis.batch_selection import (
  BatchSelectionConfig,
  is_excluded_by_exec_filter,
  run_batch_selection,
)
from backend.analysis.wfo_asset_selection import run_wfo_asset_selection
from backend.analysis.wfo_event_dedup import EventAggregateState, dedup_and_rebuild
from backend.analysis.wfo_window_gen import (
  generate_overlapping_windows,
  generate_rolling_windows,
  generate_static_windows,
)
from backend.db.database import get_db
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  BatchBacktestRun,
  BatchGroup,
  Portfolio,
  PortfolioAsset,
  Strategy,
  WFORun,
  WFOWindow,
)
from backend.utils.sanitize import sanitize_for_json as _sanitize

router = APIRouter()


class WFOAssetSelectionRequest(BaseModel):
  batch_ids: list[int] = []  # multi-batch support
  portfolio_ids: list[int] = []  # saved portfolios as virtual batches
  is_months: int = 24
  oos_months: int = 6
  last_oos_end: str
  # Optimizer settings (full parity with OptimizerPanel)
  n_assets: int = 5
  objective: str = "hybrid"
  corr_method: str = "standard"
  div_algo: str = "greedy"
  n_candidates: int = 3
  min_trades: int = 0
  min_pf: float = 0.0
  min_wr: float = 0.0
  extra_slippage_pct: float = 0.0
  extra_commission_pct: float = 0.0
  funding_daily_pct: float = 0.0
  spread_pct: float = 0.0
  seed_metrics: list[str] = ["sortino"]
  max_seeds: int = 20
  prioritize_stability: bool = False
  date_from: str | None = None
  # Split mode
  split_mode: bool = False
  n_longs: int = 3
  n_shorts: int = 3
  within_objective: str = "uncorrelated"
  combined_objective: str = "stability"
  # Trade-count constraints — combined mode
  tc_low_threshold: int = 0
  tc_max_low: int = 99
  tc_high_threshold: int = 0
  tc_min_high: int = 0
  # Trade-count constraints — split mode
  tc_longs: dict | None = None
  tc_shorts: dict | None = None
  # Manual mode (3-layer optimization, requires 2+ batches)
  wfo_mode: str = "auto"  # "auto" | "manual"
  obj_layer1: str = "hybrid"
  obj_layer2: str = "stability"
  obj_layer3: str = "hybrid"
  candidates_per_strategy: int = 3
  n_final_strats: int = 2
  # Recency-weighted seed scoring
  recency_enabled: bool = False
  recency_mode: str = "gradual"
  recency_batch_size: int = 10
  recency_boost_factor: float = 3.0
  # Benchmark robustness
  benchmark_enabled: bool = False
  benchmark_source: str = ""
  benchmark_split: str = "time"
  benchmark_threshold: float = 1.2
  benchmark_elite_target: float = 1.0
  benchmark_exclude_fragile: bool = True
  # Directional batches mode (beam search)
  directional_batches: bool = False
  l2_groups: list[list[int]] = []
  n_per_batch: int = 5
  obj_l1: str = "hybrid"
  obj_l2: str = "uncorrelated"
  obj_l3: str = "max_sortino"
  k_l1: int = 5
  k_l2: int = 5
  k_l3: int = 3
  # Event-based mode (trade-by-trade simulation)
  event_mode: bool = False
  n_trades: int = 10
  staleness_days: int = 180
  n_per_group: int = 15
  rebalance_every: int = 1   # Global Pulse: re-evaluate every N trades (1 = every trade)
  min_hold: int = 0           # Selection Lock: min own-trades before removal eligible
  warmup_trades: int = 0     # Min global trades before walk-forward starts (works with date_from)
  rebalance_mode: str = "global"  # "global" | "portfolio" | "spot_single" | "spot_batch"
  portfolio_rebalance_every: int = 5  # Portfolio/Spot Batch: re-evaluate every N portfolio trades
  # L1 quality gate (passes ALL survivors instead of Top N)
  l1_quality_gate: bool = False
  # Category filter — only include assets matching selected categories
  category_filter: list[str] = []
  # WFAS overhaul: time mode
  time_mode: str = "rolling"  # "rolling" | "static" | "overlapping"
  # Static mode: step size and minimum OOS period
  static_step_months: int = 3
  min_oos_months: int = 3
  # Overlapping mode: full OOS period (split into H1/H2)
  oos_total_months: int = 6
  # Pool per batch: top K assets kept per batch after seed ranking (0 = auto)
  pool_per_batch: int = 0
  # Diversity: max Jaccard overlap between candidate portfolios
  max_overlap: float | None = None
  # Structural quality pre-filter (mirrors optimizer)
  prefilter_enabled: bool = False
  prefilter_mode: str = "trend"
  prefilter_hurst_min: float = 0.50
  prefilter_ter_percentile: int = 25
  prefilter_hurst_max: float = 0.50
  prefilter_vr_max: float = 1.0
  prefilter_min_crossing_rate: float = 0.05
  # DSR gate (via benchmark)
  benchmark_dsr_gate: bool = False
  # AC postfilter
  ac_postfilter: bool = False
  ac_postfilter_type: str = "trend"
  ac_lag: int = 1
  # Max drawdown hard limit — reject portfolios exceeding this DD%
  max_allowed_drawdown_pct: float = 0.0  # 0 = disabled, e.g. 30.0 = reject >30% DD
  # Risk profile — applied to ALL trades per-asset before optimization
  risk_profile_mode: str = "fixed"            # "fixed"|"pct"|"martingale"|"anti_martingale"|"kelly"
  risk_profile_value: float = 0.0             # $ (fixed/mart) or % (pct). 0 = use batch risk as-is
  risk_profile_initial_capital: float = 10_000.0
  risk_profile_multiplier: float = 2.0        # martingale only
  risk_profile_max_streak: int = 3            # martingale only
  risk_profile_martingale_compound: str = "off"  # "off"|"dollar"|"pct"
  risk_profile_kelly_n: float = 2.0            # kelly N divisor (supports float for custom fractions)
  risk_profile_kelly_warmup: int = 20         # trades before Kelly reaches full confidence
  # Boundary clipping — force-close IS trades at IS boundary close price
  clip_is: bool = True
  # ── Batch Selection mode (4th WFO mode) ──
  # When wfo_mode="batch_selection", these fields are used instead of
  # optimizer settings. Prefix bs_ avoids collision with existing fields.
  bs_max_batches: int = 4
  bs_assets_per_batch: int = 5              # Research sweet spot (was 3)
  bs_n_candidates: int = 20
  bs_min_calibrated: int = 3
  bs_min_pf: float = 1.0
  bs_min_is_days: int = 30
  bs_min_asset_days: int = 5               # Match research (was 20)
  bs_asset_must_profit: bool = False        # True is catastrophic — Phase 7 finding (was True)
  bs_cal_must_profit: bool = True
  bs_batch_rank_metric: str = "recovery"   # Best IS metric — Phase 3/5 finding (was net_pnl)
  bs_cal_rank_metric: str = "ulcer"        # Best CAL metric — Phase 4/5 finding (was tail_ratio)
  bs_asset_rank_metric: str = "net_pnl"    # Best asset metric at mb=3 — Phase 10 (was lake_ratio)
  bs_max_tpm: int = 40
  bs_asset_wr_gate: float = 0.20           # Min win rate in IS for assets — research used 0.20
  # ── Batch Selection: advanced features ──
  bs_recency_weight: float = 0.0         # Recency momentum (0=off, 2.0=recent 2x)
  bs_adaptive_mb: bool = False           # Auto-size max_batches by quality gap
  bs_adaptive_mb_min: int = 2
  bs_adaptive_mb_max: int = 7
  bs_adaptive_mb_gap_pct: int = 75       # Gap percentile threshold
  bs_ensemble_cal_k: int = 0            # Ensemble CAL gate (0=single metric, 2+=K-of-5)
  bs_regime_weight: float = 0.0         # Regime-conditional (0=off, 1.0=full weight)
  bs_elo_iterations: int = 0            # ELO tournament (0=off, 5=standard)
  # ── Batch Selection: research-validated features (co-work sweeps) ──
  bs_l2_groups: bool = False            # L2 Groups: long/short split selection (+20pp)
  bs_pruning_enabled: bool = False      # Progressive pruning: pre-filter batch pool
  bs_prune_long: int = 0               # Remove N worst LONG batches (e.g. 4)
  bs_prune_short: int = 0              # Remove N worst SHORT batches (e.g. 8)
  bs_past_oos_confirm: bool = False     # Require assets profitable in previous OOS
  bs_q_starts: int = 1                 # Q-aligned averaging: 1=single start, 4=average 4 Q-starts
  bs_include_live_window: bool = True   # Add live/current window for forward-looking asset selection
  # ── Overfit Guard (TOP-LEVEL — works across all wfo_modes) ──
  # When enabled, applies overfit protection appropriate for the active mode:
  #   - batch_selection: 5-approach asset-level guard (via bs_* below)
  #   - auto/manual/directional: walk_forward_rerank (portfolio validation split)
  # Defaults to OFF — when disabled the run is byte-identical to today.
  # Old presets that set bs_overfit_guard_enabled keep working unchanged.
  overfit_guard_enabled: bool = False      # Master switch (any mode)
  overfit_guard_mode: str = "log"          # "log"|"zscore"|"bayes"|"log_zscore"|"full" — used in BS mode
  of_wf_rerank_enabled: bool = False       # Force WF rerank even when guard mode is asset-level
  # ── Overfit Guard: penalize suspiciously extreme assets in Step 3 (BS-mode-specific) ──
  bs_overfit_guard_enabled: bool = False   # Enable overfit guard on asset selection
  bs_overfit_guard_mode: str = "log"       # "log"|"zscore"|"bayes"|"log_zscore"|"full"
  bs_og_log_cap_pf: float = 4.0           # Log diminish caps
  bs_og_log_cap_sortino: float = 3.0
  bs_og_log_cap_payoff: float = 3.0
  bs_og_log_cap_sharpe: float = 2.5
  bs_og_log_cap_calmar: float = 3.0
  bs_og_log_cap_wr: float = 70.0
  bs_og_z_threshold: float = 2.0          # Z-score params
  bs_og_z_penalty_strength: float = 0.15
  bs_og_bayes_k: float = 5.0              # Bayesian shrinkage k
  # ── 2-Step Hybrid: batch selection (Step 1) → event-based (Step 2) ──
  hybrid_mode: bool = False            # When True + wfo_mode="batch_selection": chain Step 1→Step 2
  hybrid_n_trades: int = 10            # Step 2: lookback window (last N trades per asset)
  hybrid_n_assets: int = 3             # Step 2: portfolio size (top K assets)
  hybrid_staleness_days: int = 180     # Step 2: max days since last trade
  hybrid_min_pf: float = 1.0           # Step 2: min profit factor for eligibility
  hybrid_min_wr: float = 30.0          # Step 2: min win rate % for eligibility
  hybrid_rebalance_every: int = 10     # Step 2: re-evaluate every N trades
  hybrid_rebalance_mode: str = "global"  # Step 2: "global" | "portfolio" | "spot_single" | "spot_batch"
  hybrid_warmup_trades: int = 0        # Step 2: min trades before scoring starts
  hybrid_min_hold: int = 0             # Step 2: selection lock (min own-trades)
  hybrid_scoring_method: str = "composite"  # Step 2: "single" | "composite"
  hybrid_objective: str = "sortino"    # Step 2: single-metric objective
  hybrid_composite_weights: dict | None = None  # Step 2: composite weights
  hybrid_exec_day_profit: bool = True  # Step 2: per-asset day profitability filter (default on)
  # ── Rescue System (Feature 5-6) ──
  hybrid_sample_count_floor: int = 0        # Feature 2: exclude assets with < N trades
  hybrid_rescue_enabled: bool = False       # Feature 5: enable rescue system
  hybrid_rescue_quota_pct: float = 0.10     # Feature 5: max % of slate that can be rescued (0.0–1.0)
  hybrid_rescue_min_trades: int = 30        # Feature 5: per-asset minimum for rescue eligibility
  hybrid_rescue_min_score: float = 0.0      # Feature 5: absolute score floor for rescue
  hybrid_rescue_confidence_gap: float = 0.15  # Feature 6: min gap between best rescue and worst accepted
  # ── Category Filter (additive, off by default) ──
  category_filter_enabled: bool = False     # master switch
  category_influence: str = "none"          # none|low|mid|high|all|event
  category_lookback_trades: int = 20        # rolling window for per-category composite (15-30 typical)
  category_max_per_cat: int = 0             # max simultaneous portfolio assets per category (0 = uncapped)
  category_score_metric: str = "composite"  # reuses main composite weights
  category_refresh_mode: str = "per_window" # per_window | fixed — cadence for recomputing category tiers (non-hybrid only)
  # ── Category Filter — on-fire-streak event mode (2026-04-17-B; off by default) ──
  # When category_mode=="streak_event": pool trades per category, fire when N
  # consecutive winners seen in last (streak+duration) trades, apply
  # fire_multiplier to scores for `fire_duration_trades` trades after.
  # Legacy "tertile" behavior is preserved when category_mode is omitted.
  category_mode: str = "tertile"                # "tertile" (legacy) | "streak_event"
  category_streak_required: int = 5             # consecutive winners to trigger "on fire"
  category_fire_multiplier: float = 1.30        # score boost while on fire
  category_fire_duration_trades: int = 10       # trailing window after streak
  # ── Event mode: scoring method & execution filters ──
  scoring_method: str = "single"  # "single" (rank by objective) | "composite" (weighted blend)
  composite_weights: dict | None = None  # e.g. {"sortino": 0.4, "pf": 0.25, "recovery": 0.2, "wr": 0.15}
  exec_day_profit: bool = False  # per-asset day-of-week profitability execution filter
  # ── PnL Scaling (position sizing) ──
  # Controls how trade PnL is scaled before WFAS runs.
  # "group": auto-apply the batch group's pnl_multiplier (e.g. Group A = 2×)
  # "custom": apply a single user-specified multiplier to ALL trades
  # "per_batch": apply individual multipliers per batch (from pnl_scaling_per_batch map)
  # "none": no scaling (raw backtest PnL)
  pnl_scaling_mode: str = "group"       # "group" | "custom" | "per_batch" | "none"
  pnl_scaling_custom: float = 1.0       # Only used when mode="custom"
  pnl_scaling_per_batch: dict[str, float] = {}  # batch_id (str) → multiplier, used when mode="per_batch"
  # ── Session Filter — inline trade-time filtering ──
  session_filter_enabled: bool = False
  session_filter_mode: str = "fixed"  # "fixed" | "auto" | "saved_mask"
  # Fixed mode
  session_filter_exclude_days: list[int] = []   # 0=Mon..6=Sun
  session_filter_exclude_hours: list[int] = []  # 0-23
  # Auto mode
  session_filter_auto_min_trades: int = 10
  session_filter_auto_include_insufficient: bool = True
  # Grading toggle: False = grade on full data, True = grade on cropped
  session_filter_grade_on_cropped: bool = False
  # Track which research preset was applied (displayed in saved runs list)
  preset_id: str | None = None
  # CS-071: freeze the final-window cutoff date so replaying a preset on
  # a different calendar day produces the same windows. When None (the
  # default), wfo_window_gen falls back to ``backend.utils.clock.today``.
  # Accept ISO-format date strings (YYYY-MM-DD).
  replay_as_of: str | None = None


class WFOSaveTrackRequest(BaseModel):
  name: str
  batch_ids: list[int] = []
  track_rank: int
  assets: list[dict]
  wfo_settings: dict
  metrics_snapshot: dict
  optimization_end_date: str | None = None
  # Per-window rotation history for walk-forward display. Each entry:
  # {window_num, assets: [asset_tag], oos_pnl, oos_wfe, ...}. When present,
  # /portfolios/{id}/wfo-timeline stitches real per-window OOS trades.
  per_window: list[dict] | None = None
  # Window date metadata: [{window_num, is_start, is_end, oos_start, oos_end,
  # oos_h1_start?, oos_h1_end?, oos_h2_start?, oos_h2_end?}]. Locked at save time.
  windows_meta: list[dict] | None = None


class WFOSavePhase2Request(BaseModel):
  """Save Phase 2 (hybrid event WFO) results as a tradeable portfolio."""
  name: str
  batch_ids: list[int] = []
  # The final active assets from the last selection change
  assets: list[dict]  # [{symbol, side, batch_id, timeframe, group_name, asset_key}]
  # Full preset/run config so it can be re-run to refresh
  preset_config: dict
  # Phase 2 aggregate metrics
  metrics_snapshot: dict
  # Winning batch IDs from Phase 1 (the batches Phase 2 operates on)
  winning_batch_ids: list[int] = []
  # OOS boundary for optimization_end_date
  optimization_end_date: str | None = None
  # Preset ID for traceability
  preset_id: str | None = None
  # Event-based rotation history for walk-forward display. Each entry:
  # {step, date, added: [asset_tag], removed: [asset_tag]}. When present,
  # /portfolios/{id}/wfo-timeline stitches OOS trades by active basket.
  selection_changes: list[dict] | None = None
  # Optional: the basket active BEFORE the first selection_change (the
  # warm-up basket). Derived from assets+changes if omitted.
  initial_basket: list[str] | None = None
  # Phase 2 truth for portfolio replay — one row per selected trade:
  # {run_id, dt, symbol, side, pnl, batch_id, asset, timeframe, ...}.
  # Stored under source_strategies.step2_result so has_wfo_replay_metadata()
  # fires and every portfolio view (monthly table, modal, full metrics)
  # reads the same data the optimizer produced.
  selected_records: list[dict] | None = None
  # Pre-computed entry-date-bucketed monthly grid from step2_service.
  # {years, matrix, row_totals, col_totals, grand_total}.
  aggregate_monthly_matrix: dict | None = None
  # Optional: the equity curve and breakdown from Phase 2 so /full-metrics-wfo
  # can reuse them directly without re-computing from records.
  aggregate_equity_curve: list[dict] | None = None
  aggregate_breakdown: list[dict] | None = None
  cumulative_metrics: dict | None = None
  # Window date metadata (same shape as WFOSaveTrackRequest.windows_meta):
  # [{window_num, is_start, is_end, oos_start, oos_end, ...}]. Used to
  # auto-populate optimization_end_date from the last window's oos_end
  # when the caller omits optimization_end_date on a hybrid save.
  windows_meta: list[dict] | None = None
  # Per-window asset basket + OOS metrics from the Phase-2 event WFO run.
  # Mirrors step2_result.per_window. Persisted alongside windows_meta so
  # downstream views (wfo-timeline, monthly grid) can reconstruct the
  # basket evolution per window.
  wfo_per_window: list[dict] | None = None
  # T-002e': end-of-stream per-asset buffers + locks from run_event_wfo's
  # `final_state_snapshot`. When present, the next hybrid refresh can seed
  # run_event_wfo via `config["_seed_state"]` instead of rebuilding state
  # from scratch. Typically omitted on first save (buffers are re-derived
  # from scratch on the first refresh); written by the refresh service on
  # every subsequent refresh.
  continuation_state: dict | None = None


def _build_batch_info(batch_ids: list[int], db: Session) -> dict[int, dict]:
  """Build batch_info lookup: batch_id → {timeframe, group, name, pnl_multiplier}."""
  rows = (
    db.query(
      BatchBacktestRun.id,
      BatchBacktestRun.group_id,
      BatchGroup.name.label("group_name"),
      BatchGroup.timeframe,
      BatchGroup.pnl_multiplier,
    )
    .outerjoin(BatchGroup, BatchBacktestRun.group_id == BatchGroup.id)
    .filter(BatchBacktestRun.id.in_(batch_ids))
    .all()
  )
  info: dict[int, dict] = {}
  for bid, gid, gname, tf, mult in rows:
    info[bid] = {
      "timeframe": tf or "?",
      "group": gname or f"B{bid}",
      "group_id": gid,
      "name": gname or f"Batch {bid}",
      "pnl_multiplier": mult or 1.0,
    }
  return info


def _load_multi_batch_trades(
  batch_ids: list[int], db: Session,
  date_from: str | None = None,
  *,
  pnl_scaling_mode: str = "group",
  pnl_scaling_custom: float = 1.0,
  pnl_scaling_per_batch: dict[str, float] | None = None,
) -> tuple[list[BacktestTrade], dict[int, int], dict[int, float]]:
  """Load trades from multiple batches with optional date_from filter.

  Returns (trades, run_batch_map, batch_multipliers) where:
    - run_batch_map maps run_id → batch_id
    - batch_multipliers maps batch_id → effective multiplier applied

  PnL scaling modes:
    "group":     apply each batch's group pnl_multiplier (e.g. Group A = 2×)
    "custom":    apply a single user-specified multiplier to ALL trades
    "per_batch": apply individual multipliers per batch (from pnl_scaling_per_batch)
    "none":      no scaling — raw backtest PnL ($100 position size)
  """
  # ── Look up group pnl_multiplier per batch ──
  batch_group_mult: dict[int, float] = {}
  batch_rows = (
    db.query(BatchBacktestRun.id, BatchBacktestRun.group_id)
    .filter(BatchBacktestRun.id.in_(batch_ids))
    .all()
  )
  group_ids = {r.group_id for r in batch_rows if r.group_id}
  group_mult: dict[int, float] = {}
  if group_ids:
    groups = db.query(BatchGroup.id, BatchGroup.pnl_multiplier).filter(
      BatchGroup.id.in_(group_ids)
    ).all()
    group_mult = {g.id: (g.pnl_multiplier or 1.0) for g in groups}

  for r in batch_rows:
    batch_group_mult[r.id] = group_mult.get(r.group_id, 1.0) if r.group_id else 1.0

  # ── Resolve effective multiplier per batch based on mode ──
  per_batch_map = {int(k): v for k, v in (pnl_scaling_per_batch or {}).items()}
  batch_multipliers: dict[int, float] = {}
  for bid in batch_ids:
    if pnl_scaling_mode == "none":
      batch_multipliers[bid] = 1.0
    elif pnl_scaling_mode == "custom":
      batch_multipliers[bid] = pnl_scaling_custom
    elif pnl_scaling_mode == "per_batch":
      # Use user-supplied value; fall back to group multiplier if not specified
      batch_multipliers[bid] = per_batch_map.get(bid, batch_group_mult.get(bid, 1.0))
    else:  # "group" (default)
      batch_multipliers[bid] = batch_group_mult.get(bid, 1.0)

  # ── Load trades ──
  all_trades: list[BacktestTrade] = []
  run_batch_map: dict[int, int] = {}
  for bid in batch_ids:
    runs = (
      db.query(BacktestRun)
      .filter(BacktestRun.batch_id == bid, BacktestRun.status == "completed")
      .all()
    )
    run_ids = [r.id for r in runs]
    for r in runs:
      run_batch_map[r.id] = bid
    if run_ids:
      query = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids))
      if date_from:
        query = query.filter(BacktestTrade.entry_time >= date_from)
      all_trades.extend(query.all())

  # ── SAFETY: Convert ORM objects to plain SimpleNamespace snapshots ──
  # This completely eliminates SQLAlchemy instrumentation.  Without this,
  # modifying net_profit on ORM objects (or shallow copies of them) can:
  #   1. Auto-flush scaled values back to the DB via db.commit(),
  #      permanently corrupting the raw $100-base PnL.
  #   2. Raise ObjectDereferencedError when the original ORM object is
  #      garbage-collected but a copy's instrumented attribute still
  #      references its state.
  # SimpleNamespace has zero ORM ties — safe to mutate, copy, and GC.
  import types as _types
  _SNAP_ATTRS = (
    "id", "run_id", "trade_num", "symbol", "side",
    "net_profit", "net_profit_pct", "commission",
    "entry_time", "exit_time", "entry_price", "exit_price",
    "quantity", "timeframe", "position_id",
    "mae", "mfe", "sl_price", "tp_price", "exit_reason",
  )
  safe_trades = []
  for t in all_trades:
    snap = _types.SimpleNamespace(**{a: getattr(t, a, None) for a in _SNAP_ATTRS})
    safe_trades.append(snap)
    try:
      db.expunge(t)          # detach original so it can never be flushed
    except Exception as _exc:
      log.debug("swallowed: %s", _exc)
  all_trades = safe_trades

  # ── Apply pnl_multiplier scaling (on plain snapshots — no ORM ties) ──
  scaled_count = 0
  for t in all_trades:
    bid = run_batch_map.get(t.run_id)
    mult = batch_multipliers.get(bid, 1.0) if bid else 1.0
    if mult != 1.0:
      t.net_profit = (t.net_profit or 0.0) * mult
      scaled_count += 1

  # Log scaling info
  active_mults = {bid: m for bid, m in batch_multipliers.items() if m != 1.0}
  if active_mults:
    log.info(
      "PnL scaling (mode=%s): %d/%d trades scaled, multipliers=%s",
      pnl_scaling_mode, scaled_count, len(all_trades), active_mults,
    )
  else:
    log.info("PnL scaling (mode=%s): no scaling applied (all multipliers=1.0)", pnl_scaling_mode)

  # ── Aggregate multi-leg positions (TP1 + TP2/TSL) ──
  # The backtest engine stores one row per exit leg.  Without aggregation,
  # downstream scoring (batch_selection, event_wfo) double-counts every
  # multi-leg position (~1.75x trade inflation, corrupted PF / WR / PnL).
  # load_trades_lightweight() already does this; we must match here.
  from collections import defaultdict as _defaultdict
  _positions: dict[str, list] = _defaultdict(list)
  _pre_agg = len(all_trades)
  for t in all_trades:
    if t.position_id is not None:
      _pk = f"{t.run_id}_{t.position_id}"
    else:
      _entry_iso = t.entry_time.isoformat() if t.entry_time else ""
      _pk = f"{t.run_id}_noid__{t.symbol}__{t.side}__{_entry_iso}__{t.exit_time}"
    _positions[_pk].append(t)

  aggregated: list = []
  for _pk, legs in _positions.items():
    if len(legs) == 1:
      aggregated.append(legs[0])
    else:
      # Multi-leg: sum PnL, use earliest entry / latest exit, keep first leg's other attrs
      first = legs[0]
      first.net_profit = sum((l.net_profit or 0.0) for l in legs)
      first.entry_time = min((l.entry_time for l in legs if l.entry_time), default=first.entry_time)
      first.exit_time = max((l.exit_time for l in legs if l.exit_time), default=first.exit_time)
      # Use latest exit's exit_price/exit_reason (the final close)
      last_by_exit = max(legs, key=lambda l: l.exit_time if l.exit_time else first.exit_time)
      first.exit_price = last_by_exit.exit_price
      first.exit_reason = last_by_exit.exit_reason
      # Sum commissions and net_profit_pct across legs
      first.commission = sum((l.commission or 0.0) for l in legs)
      first.net_profit_pct = sum((l.net_profit_pct or 0.0) for l in legs)
      aggregated.append(first)

  all_trades = aggregated
  _post_agg = len(all_trades)
  if _pre_agg != _post_agg:
    log.info(
      "Position aggregation: %d raw legs → %d positions (%d multi-leg merged)",
      _pre_agg, _post_agg, _pre_agg - _post_agg,
    )

  return all_trades, run_batch_map, batch_multipliers


@router.post("/asset-selection")
def run_wfo(request: Request, req: WFOAssetSelectionRequest, db: Session = Depends(get_db)):
  """Run Walk Forward Optimization for asset selection."""
  rate_limit(request, max_requests=10, window_seconds=60, key="wfo")
  if not req.batch_ids and not req.portfolio_ids:
    raise HTTPException(400, "At least one batch or portfolio required")

  try:
    # Load all trades from all batches
    # Event mode: load ALL trades (no date_from filter) — event_wfo.py handles warmup
    # Time mode: filter by date_from at DB level
    _df = None if req.event_mode else req.date_from
    if req.batch_ids:
      all_trades, run_batch_map, _batch_mults = _load_multi_batch_trades(
        req.batch_ids, db, _df,
        pnl_scaling_mode=req.pnl_scaling_mode,
        pnl_scaling_custom=req.pnl_scaling_custom,
        pnl_scaling_per_batch=req.pnl_scaling_per_batch if req.pnl_scaling_per_batch else None,
      )
    else:
      all_trades, run_batch_map, _batch_mults = [], {}, {}

    # Merge in portfolio trades (saved portfolios as virtual batches)
    p_asset_batch: dict[str, int] = {}
    p_strat_map: dict[int, str] = {}
    if req.portfolio_ids:
      from backend.api.routes.cross_strategy import _load_portfolio_trades
      pt, p_asset_batch, p_strat_map, _, p_run_batch = _load_portfolio_trades(
        req.portfolio_ids, db, _df,
        date_to=None, tagged=False, clip_boundary=False,
      )
      all_trades.extend(pt)
      run_batch_map.update(p_run_batch)

    if not all_trades:
      raise HTTPException(400, "No trades found in selected sources")

    # Category filter — pre-filter trades by symbol category
    if req.category_filter:
      from backend.services.category_service import get_all_categories
      cat_map = get_all_categories(db)
      allowed_symbols = {
        sym for sym, cats in cat_map.items()
        if any(c in req.category_filter for c in cats)
      }
      all_trades = [t for t in all_trades if t.symbol in allowed_symbols]
      if not all_trades:
        raise HTTPException(400, "No trades match selected categories")

    # Apply extra cost stress test (slippage, commission, funding, spread)
    if req.extra_slippage_pct > 0 or req.extra_commission_pct > 0 or req.funding_daily_pct > 0 or req.spread_pct > 0:
      from backend.api.routes.analysis import _apply_stress_slippage
      all_trades = _apply_stress_slippage(all_trades, req.extra_slippage_pct, db,
                                           extra_commission_pct=req.extra_commission_pct,
                                           funding_daily_pct=req.funding_daily_pct,
                                           spread_pct=req.spread_pct)

    # Apply risk profile scaling (must be after slippage, before optimizer)
    _risk_diag: dict | None = None
    if req.risk_profile_mode != "fixed" or req.risk_profile_value > 0:
      # Diagnostic: capture PnL before scaling
      _pre_pnl = sum(t.net_profit or 0.0 for t in all_trades)
      _sample_before = [(t.symbol, round(t.net_profit or 0, 4)) for t in all_trades[:5]]
      log.info("═══ RISK PROFILE SCALING ═══")
      log.info("  mode=%s  value=%s  initial_capital=%s  multiplier=%s  max_streak=%s  kelly_n=%s",
               req.risk_profile_mode, req.risk_profile_value,
               req.risk_profile_initial_capital, req.risk_profile_multiplier,
               req.risk_profile_max_streak, req.risk_profile_kelly_n)
      log.info("  total_trades=%d  pre_scaling_total_pnl=%.2f", len(all_trades), _pre_pnl)

      from backend.api.routes.analysis import _apply_risk_scaling
      all_trades = _apply_risk_scaling(all_trades, req, db, include_side=req.split_mode, run_batch_map=run_batch_map)

      # Diagnostic: capture PnL after scaling
      _post_pnl = sum(t.net_profit or 0.0 for t in all_trades)
      _sample_after = [(t.symbol, round(t.net_profit or 0, 4)) for t in all_trades[:5]]
      _pnl_changed = abs(_post_pnl - _pre_pnl) > 0.01
      log.info("  post_scaling_total_pnl=%.2f  delta=%.2f  changed=%s",
               _post_pnl, _post_pnl - _pre_pnl, _pnl_changed)
      if not _pnl_changed:
        log.warning("  ⚠️ SCALING HAD NO EFFECT — total PnL unchanged!")
      log.info("═══ END RISK PROFILE ═══")

      _risk_diag = {
        "applied": True,
        "mode": req.risk_profile_mode,
        "value": req.risk_profile_value,
        "initial_capital": req.risk_profile_initial_capital,
        "pre_total_pnl": round(_pre_pnl, 2),
        "post_total_pnl": round(_post_pnl, 2),
        "pnl_change_pct": round((_post_pnl / _pre_pnl - 1) * 100, 1) if _pre_pnl != 0 else 0,
        "scaling_worked": _pnl_changed,
        "sample_before": _sample_before[:3],
        "sample_after": _sample_after[:3],
        "note": (
          "Fixed $ mode applies uniform scaling — portfolio SELECTION unchanged "
          "(PF, WR, Sortino are ratios). Only absolute values (net profit, max DD) change."
          if req.risk_profile_mode == "fixed"
          else None
        ),
      }
    else:
      log.info("═══ RISK PROFILE: SKIPPED (mode=%s, value=%s) ═══",
               req.risk_profile_mode, req.risk_profile_value)

    # Event-based mode: trade-by-trade simulation (early return)
    if req.event_mode:
      from backend.analysis.event_wfo import run_event_wfo
      config = req.model_dump(exclude={"batch_ids", "portfolio_ids", "is_months", "oos_months", "last_oos_end"})
      total_sources = len(req.batch_ids) + len(req.portfolio_ids)
      if req.directional_batches and total_sources >= 2:
        from backend.api.routes.cross_strategy import _build_asset_batch_map, _strategy_map
        abm = _build_asset_batch_map(req.batch_ids, db) if req.batch_ids else {}
        sm = _strategy_map(req.batch_ids, db) if req.batch_ids else {}
        if req.portfolio_ids:
          abm.update(p_asset_batch)
          sm.update(p_strat_map)
        config["_asset_batch_map"] = abm
        config["_strategy_map"] = sm
      result = run_event_wfo(all_trades, config)
      if _risk_diag:
        result["risk_profile_diagnostic"] = _risk_diag
      return _sanitize(result)

    # Determine data start from earliest trade (or explicit override)
    dates = [t.entry_time for t in all_trades if t.entry_time]
    data_start = req.date_from if req.date_from else min(dates).strftime("%Y-%m-%d")

    # CS-071: freeze the final IS-only window's cutoff when the caller
    # passed replay_as_of. Otherwise fall back to clock.today() inside
    # the generator. This preserves the audit's "same inputs → same
    # outputs" invariant across calendar days for saved presets.
    from datetime import date as _date
    _replay_cutoff: _date | None = None
    if req.replay_as_of:
      try:
        _replay_cutoff = _date.fromisoformat(req.replay_as_of)
      except ValueError as e:
        raise HTTPException(400, f"Invalid replay_as_of date: {e}") from e

    # Generate windows based on time_mode
    if req.time_mode == "static":
      windows = generate_static_windows(
        data_start, req.last_oos_end, req.is_months,
        step_months=req.static_step_months, min_oos_months=req.min_oos_months,
        today=_replay_cutoff,
      )
    elif req.time_mode == "overlapping":
      windows = generate_overlapping_windows(
        req.is_months, req.oos_total_months, req.last_oos_end, data_start,
        today=_replay_cutoff,
      )
    else:
      windows = generate_rolling_windows(
        req.is_months, req.oos_months, req.last_oos_end, data_start,
        today=_replay_cutoff,
      )
    if not windows:
      raise HTTPException(400, "No valid WFO windows could be generated")

    # ── Batch Selection mode: separate code path ──
    if req.wfo_mode == "batch_selection":
      # Filter out trailing IS-only windows (spec has no trailing window)
      windows = [w for w in windows if w.get("oos_start") is not None]
      if not windows:
        raise HTTPException(400, "No valid WFO windows with OOS period")

      # CS-070: ``session_filter_grade_on_cropped`` is wired only on the
      # ``wfo_asset_selection`` path. On the ``batch_selection`` branch
      # the flag is accepted by the request schema and persisted into
      # saved-run settings, but ``BatchSelectionConfig`` does not carry
      # it and ``run_batch_selection`` never reads it. Flipping the
      # toggle produces identical numbers on this path — a silent
      # no-op that a user could read as "this knob does nothing" and
      # conclude the effect is noise. Emit a clear backend warning so
      # the divergence surfaces in the logs.
      if req.session_filter_grade_on_cropped:
        log.warning(
          "session_filter_grade_on_cropped=True is IGNORED on "
          "wfo_mode='batch_selection' (the flag is only honoured on "
          "the wfo_asset_selection branch). IS/CAL always grade on "
          "full data on this path; only OOS is exec-filtered."
        )

      # Session filter: pass to batch_selection engine as exec filter config.
      # IS/CAL use ALL trades; exec filter applies to OOS evaluation ONLY.
      # This matches the research methodology: score on full data, evaluate on exec-filtered data.
      _sf_diag = None
      exec_exclude_hours: set[int] | None = None
      exec_exclude_days: set[int] | None = None
      if req.session_filter_enabled:
        _pre_count = len(all_trades)
        exclude_hours = set(req.session_filter_exclude_hours)
        exclude_days = set(req.session_filter_exclude_days)
        if exclude_hours or exclude_days:
          exec_exclude_hours = exclude_hours if exclude_hours else None
          exec_exclude_days = exclude_days if exclude_days else None
          # Count exec-eligible trades for diagnostic
          # CS-014: share predicate with canonical _filter_trades_exec
          exec_count = sum(
            1 for t in all_trades
            if not is_excluded_by_exec_filter(t.entry_time, exclude_hours, exclude_days)
          )
          _sf_diag = {
            "enabled": True,
            "mode": req.session_filter_mode,
            "exclude_hours": list(exclude_hours),
            "exclude_days": list(exclude_days),
            "trades_total": _pre_count,
            "trades_exec_eligible": exec_count,
            "pct_exec": round(exec_count / max(_pre_count, 1) * 100, 1),
            "note": "IS/CAL use ALL trades; exec filter applied to OOS evaluation only",
          }
          log.info(
            "Session filter (batch_selection): %d total, %d exec-eligible (%.1f%%), excl hours=%s, excl days=%s",
            _pre_count, exec_count, _sf_diag["pct_exec"],
            list(exclude_hours), list(exclude_days),
          )

      bs_config = BatchSelectionConfig(
        max_batches=req.bs_max_batches,
        assets_per_batch=req.bs_assets_per_batch,
        n_candidates=req.bs_n_candidates,
        min_calibrated=req.bs_min_calibrated,
        min_pf=req.bs_min_pf,
        min_is_days=req.bs_min_is_days,
        min_asset_days=req.bs_min_asset_days,
        asset_must_profit=req.bs_asset_must_profit,
        cal_must_profit=req.bs_cal_must_profit,
        batch_rank_metric=req.bs_batch_rank_metric,
        cal_rank_metric=req.bs_cal_rank_metric,
        asset_rank_metric=req.bs_asset_rank_metric,
        max_tpm=req.bs_max_tpm,
        asset_wr_gate=req.bs_asset_wr_gate,
        # Advanced features
        recency_weight=req.bs_recency_weight,
        adaptive_mb=req.bs_adaptive_mb,
        adaptive_mb_min=req.bs_adaptive_mb_min,
        adaptive_mb_max=req.bs_adaptive_mb_max,
        adaptive_mb_gap_pct=req.bs_adaptive_mb_gap_pct,
        ensemble_cal_k=req.bs_ensemble_cal_k,
        regime_weight=req.bs_regime_weight,
        elo_iterations=req.bs_elo_iterations,
        # Research-validated features (co-work sweeps)
        l2_groups=req.bs_l2_groups,
        pruning_enabled=req.bs_pruning_enabled,
        prune_long=req.bs_prune_long,
        prune_short=req.bs_prune_short,
        past_oos_confirm=req.bs_past_oos_confirm,
        q_starts=req.bs_q_starts,
        include_live_window=req.bs_include_live_window,
        # Overfit Guard — OR new top-level switch with legacy bs_* field.
        # Old presets with only bs_overfit_guard_enabled keep working;
        # new UI checkbox writes to overfit_guard_enabled.
        overfit_guard_enabled=(req.overfit_guard_enabled or req.bs_overfit_guard_enabled),
        overfit_guard_mode=(
          req.bs_overfit_guard_mode if req.bs_overfit_guard_enabled
          else req.overfit_guard_mode
        ),
        og_log_cap_pf=req.bs_og_log_cap_pf,
        og_log_cap_sortino=req.bs_og_log_cap_sortino,
        og_log_cap_payoff=req.bs_og_log_cap_payoff,
        og_log_cap_sharpe=req.bs_og_log_cap_sharpe,
        og_log_cap_calmar=req.bs_og_log_cap_calmar,
        og_log_cap_wr=req.bs_og_log_cap_wr,
        og_z_threshold=req.bs_og_z_threshold,
        og_z_penalty_strength=req.bs_og_z_penalty_strength,
        og_bayes_k=req.bs_og_bayes_k,
        # Exec filter: applied to OOS evaluation only (not IS/CAL)
        exec_exclude_hours=exec_exclude_hours,
        exec_exclude_days=exec_exclude_days,
        # Category filter (portable from hybrid path — active on non-hybrid too)
        category_filter_enabled=req.category_filter_enabled,
        category_influence=req.category_influence,
        category_lookback_trades=req.category_lookback_trades,
        category_score_metric=req.category_score_metric,
        category_max_per_cat=req.category_max_per_cat,
        category_refresh_mode=req.category_refresh_mode,
        category_composite_weights=req.composite_weights,
        # On-fire-streak event mode (2026-04-17-B; defaults preserve legacy)
        category_mode=req.category_mode,
        category_streak_required=req.category_streak_required,
        category_fire_multiplier=req.category_fire_multiplier,
        category_fire_duration_trades=req.category_fire_duration_trades,
      )
      # ── Build diagnostics (needed for both sync and async paths) ──
      oos_windows_list = [w for w in windows if w.get("oos_start")]
      _pnl_diag = {
        "mode": req.pnl_scaling_mode,
        "custom_value": req.pnl_scaling_custom if req.pnl_scaling_mode == "custom" else None,
        "batch_multipliers": {str(k): v for k, v in _batch_mults.items()},
        "any_scaling": any(v != 1.0 for v in _batch_mults.values()),
      }
      _win_diag = {
        "data_start": data_start,
        "last_oos_end": req.last_oos_end,
        "date_from_param": req.date_from,
        "total_windows": len(windows),
        "oos_windows": len(oos_windows_list),
        "first_window": {
          "is_start": windows[0]["is_start"],
          "is_end": windows[0]["is_end"],
          "oos_start": windows[0].get("oos_start"),
          "oos_end": windows[0].get("oos_end"),
        } if windows else None,
        "last_oos_window": {
          "is_start": oos_windows_list[-1]["is_start"],
          "is_end": oos_windows_list[-1]["is_end"],
          "oos_start": oos_windows_list[-1].get("oos_start"),
          "oos_end": oos_windows_list[-1].get("oos_end"),
        } if oos_windows_list else None,
        "total_trades_loaded": len(all_trades),
        "batch_ids_used": sorted(req.batch_ids),
        "earliest_trade": min(dates).strftime("%Y-%m-%d %H:%M") if dates else None,
        "latest_trade": max(dates).strftime("%Y-%m-%d %H:%M") if dates else None,
      }
      log.info(
        "Window diagnostic: data_start=%s, last_oos_end=%s, %d OOS windows, %d trades, batches=%s",
        data_start, req.last_oos_end, len(oos_windows_list), len(all_trades),
        sorted(req.batch_ids),
      )

      # ── Full config audit log (diagnose research-vs-production discrepancies) ──
      _step1_cfg = {
        "bs_max_batches": req.bs_max_batches,
        "bs_assets_per_batch": req.bs_assets_per_batch,
        "bs_n_candidates": req.bs_n_candidates,
        "bs_batch_rank_metric": req.bs_batch_rank_metric,
        "bs_cal_rank_metric": req.bs_cal_rank_metric,
        "bs_asset_rank_metric": req.bs_asset_rank_metric,
        "bs_cal_must_profit": req.bs_cal_must_profit,
        "bs_asset_wr_gate": req.bs_asset_wr_gate,
        "bs_recency_weight": req.bs_recency_weight,
        "bs_max_tpm": req.bs_max_tpm,
        "bs_q_starts": req.bs_q_starts,
        "bs_overfit_guard_enabled": req.bs_overfit_guard_enabled,
        "bs_overfit_guard_mode": req.bs_overfit_guard_mode,
        "bs_include_live_window": req.bs_include_live_window,
        "pnl_scaling_mode": req.pnl_scaling_mode,
        "category_filter_enabled": req.category_filter_enabled,
        "category_influence": req.category_influence,
        "category_lookback_trades": req.category_lookback_trades,
        "category_max_per_cat": req.category_max_per_cat,
        "category_score_metric": req.category_score_metric,
        "category_refresh_mode": req.category_refresh_mode,
        "category_mode": req.category_mode,
        "category_streak_required": req.category_streak_required,
        "category_fire_multiplier": req.category_fire_multiplier,
        "category_fire_duration_trades": req.category_fire_duration_trades,
      }
      _step2_cfg = {
        "hybrid_mode": req.hybrid_mode,
        "hybrid_n_trades": req.hybrid_n_trades,
        "hybrid_n_assets": req.hybrid_n_assets,
        "hybrid_min_hold": req.hybrid_min_hold,
        "hybrid_rebalance_every": req.hybrid_rebalance_every,
        "hybrid_rebalance_mode": req.hybrid_rebalance_mode,
        "hybrid_scoring_method": req.hybrid_scoring_method,
        "hybrid_objective": req.hybrid_objective,
        "hybrid_composite_weights": req.hybrid_composite_weights,
        "hybrid_exec_day_profit": req.hybrid_exec_day_profit,
        "hybrid_staleness_days": req.hybrid_staleness_days,
        "hybrid_min_pf": req.hybrid_min_pf,
        "hybrid_min_wr": req.hybrid_min_wr,
        "hybrid_warmup_trades": req.hybrid_warmup_trades,
        "hybrid_sample_count_floor": req.hybrid_sample_count_floor,
        "hybrid_rescue_enabled": req.hybrid_rescue_enabled,
      } if req.hybrid_mode else {"hybrid_mode": False}
      _sf_cfg = {
        "session_filter_enabled": req.session_filter_enabled,
        "exclude_days": req.session_filter_exclude_days,
        "exclude_hours_count": len(req.session_filter_exclude_hours),
      } if req.session_filter_enabled else {"session_filter_enabled": False}
      log.info(
        "═══ WFAS CONFIG AUDIT ═══\n"
        "  preset_id=%s\n"
        "  Step 1: %s\n"
        "  Step 2: %s\n"
        "  Session: %s\n"
        "═══ END CONFIG AUDIT ═══",
        getattr(req, "preset_id", None),
        _step1_cfg, _step2_cfg, _sf_cfg,
      )

      # Build batch_info for per-asset breakdown (timeframe, group name)
      _batch_info = _build_batch_info(req.batch_ids, db)

      # ── Hybrid Step 2 walk-forward helper (used by both sync + async paths) ──
      # As of 2026-04-19 the body lives in backend.analysis.step2_service.run_step2
      # so the nightly live-loop refresh can call it without owning an HTTP
      # request. The closure below is a thin shim that forwards the route's
      # captured vars (req / all_trades / run_batch_map / db).
      def _run_hybrid_step2(result):
        from backend.analysis.step2_service import (
          run_step2 as _svc_run_step2,
          build_config_from_request as _svc_build_cfg,
        )
        return _svc_run_step2(
          step1_result=result,
          all_trades=all_trades,
          run_batch_map=run_batch_map,
          config=_svc_build_cfg(req),
          db=db,
        )

      # ── Legacy inline body kept for reference during parity validation ──
      # Once the parity test is green this block can be deleted. Until then
      # leaving it as a commented fallback so a bisect is possible if the
      # live service path ever misbehaves.
      def _run_hybrid_step2_LEGACY(result):
        """Per-OOS-window Step 2 (event_wfo composite) on each window's Step 1
        winners. Returns step2_result dict or None if hybrid_mode disabled."""
        if not req.hybrid_mode:
          return None
        from datetime import datetime as _dt, timedelta as _td
        from backend.analysis.event_wfo import run_event_wfo, _compute_event_metrics, _window_aligned_risk_metrics

        oos_windows = [w for w in result["windows"] if w.get("oos_start")]
        if not oos_windows:
          return None

        # Bug #1 fix: detect overlapping OOS ranges (static / overlapping modes).
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
        if req.category_filter_enabled:
          try:
            from backend.services.category_service import get_all_categories
            _categories_map_for_event_wfo = {
              k.upper(): v for k, v in get_all_categories(db).items() if v
            }
          except Exception as _cat_err:
            log.warning("Category Filter: failed to load symbol_categories (%s) — running passthrough", _cat_err)

        base_step2_config = {
          "n_trades": req.hybrid_n_trades,
          "n_assets": req.hybrid_n_assets,
          "min_pf": req.hybrid_min_pf,
          "min_wr": req.hybrid_min_wr,
          "staleness_days": req.hybrid_staleness_days,
          "rebalance_every": req.hybrid_rebalance_every,
          "rebalance_mode": req.hybrid_rebalance_mode,
          "warmup_trades": req.hybrid_warmup_trades,
          "min_hold": req.hybrid_min_hold,
          "scoring_method": req.hybrid_scoring_method,
          "objective": req.hybrid_objective,
          "composite_weights": req.hybrid_composite_weights,
          "exec_day_profit": req.hybrid_exec_day_profit,
          "session_filter_enabled": req.session_filter_enabled,
          "session_filter_mode": req.session_filter_mode,
          "session_filter_exclude_hours": req.session_filter_exclude_hours,
          "session_filter_exclude_days": req.session_filter_exclude_days,
          # Feature 2: sample floor
          "hybrid_sample_count_floor": req.hybrid_sample_count_floor,
          # Feature 5-6: rescue system
          "hybrid_rescue_enabled": req.hybrid_rescue_enabled,
          "hybrid_rescue_quota_pct": req.hybrid_rescue_quota_pct,
          "hybrid_rescue_min_trades": req.hybrid_rescue_min_trades,
          "hybrid_rescue_min_score": req.hybrid_rescue_min_score,
          "hybrid_rescue_confidence_gap": req.hybrid_rescue_confidence_gap,
          # Category Filter
          "category_filter_enabled": req.category_filter_enabled,
          "category_influence": req.category_influence,
          "category_lookback_trades": req.category_lookback_trades,
          "category_max_per_cat": req.category_max_per_cat,
          "category_score_metric": req.category_score_metric,
          # On-fire-streak event mode (2026-04-17-B; defaults preserve legacy)
          "category_mode": req.category_mode,
          "category_streak_required": req.category_streak_required,
          "category_fire_multiplier": req.category_fire_multiplier,
          "category_fire_duration_trades": req.category_fire_duration_trades,
          "_categories_map": _categories_map_for_event_wfo,
        }

        per_window_step2: list[dict] = []
        agg_equity_curve: list[float] = []
        agg_equity_dates: list[str] = []
        agg_pnl_deltas: list[float] = []
        agg_selected_pnls: list[float] = []  # real executed trade PnLs across all OOS windows
        agg_selected_records: list[dict] = []  # per-trade records for aggregate curve/monthly/breakdown
        agg_selection_changes: list[dict] = []  # portfolio rebalance events across all OOS windows
        agg_total_steps = 0  # total post-warmup steps across all windows
        agg_total_trades = 0
        agg_all_trade_count = 0
        agg_warmup_trades = 0
        agg_rebalance_count = 0
        agg_asset_freq: dict[str, dict] = {}  # {asset: {selected, eligible}}
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
            is_dt = _dt.fromisoformat(w["is_start"])
            oe_dt = _dt.fromisoformat(w["oos_end"]) + _td(days=1) - _td(seconds=1)
          except Exception:
            continue
          # Bug #2 fix: gate window membership by ENTRY time (symmetric with
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
        # This is the single source of truth for every downstream aggregate.
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

        # Aggregate metrics from REAL selected trade PnLs (not equity deltas which include
        # zero-ticks on non-executed steps, contaminating WR/PF/Sortino).
        agg_summary = _compute_event_metrics(agg_selected_pnls) if agg_selected_pnls else {}
        agg_summary["avg_per_window"] = round(agg_summary.get("net_profit", 0) / n_done, 2) if n_done else 0
        # Bug #5 fix: explicit per-quarter number regardless of oos_months.
        _oos_mo = max(1, int(getattr(req, "oos_months", 3) or 3))
        agg_summary["avg_per_quarter"] = round(
          agg_summary.get("net_profit", 0) / n_done * (3.0 / _oos_mo), 2
        ) if n_done else 0
        # Bug #4 fix: single source of truth = len(agg_selected_pnls).
        agg_summary["total_trades"] = len(agg_selected_pnls)
        agg_summary["total_steps"] = agg_total_steps
        # Bug #6 fix: conservative cross-window MDD. The default `max_dd` is
        # computed on the concatenated realized PnL series (treats inter-window
        # gaps as continuous). `max_window_mdd` is the worst single-window MDD,
        # which is a safer upper bound when window boundaries reset scoring.
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
        # Phase 1 uses, so both surfaces measure the same thing for the same
        # run. Default _compute_event_metrics uses per-trade PnL which produces
        # artificially low ratios (see _window_aligned_risk_metrics docstring).
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
        # Recompute pct on aggregate asset_frequency
        for a in agg_asset_freq:
          elig = agg_asset_freq[a]["eligible"]
          sel = agg_asset_freq[a]["selected"]
          agg_asset_freq[a]["pct"] = round(sel / elig * 100, 1) if elig > 0 else 0
        # ── Build Phase 2 aggregates from the actual selected trades ──
        # These mirror Phase 1's aggregate_oos_* fields so the frontend can
        # render the same equity curve / monthly matrix / asset breakdown views.
        from collections import defaultdict as _dd
        agg_p2_equity: list[dict] = []
        agg_p2_monthly: dict = {"years": [], "matrix": {}, "row_totals": {}, "col_totals": {}, "grand_total": 0}
        agg_p2_breakdown: list[dict] = []
        agg_p2_cumulative: dict = {}
        if agg_selected_records:
          recs = sorted(agg_selected_records, key=lambda r: r.get("dt", ""))
          # Equity curve (per-trade, cumulative)
          cum = 0.0
          peak = 0.0
          daily_pnl: dict[str, float] = _dd(float)
          for r in recs:
            p = float(r.get("pnl", 0) or 0)
            cum += p
            peak = max(peak, cum)
            dd_pct = (cum - peak) / peak * 100 if peak > 0 else 0.0
            date_str = str(r.get("dt", ""))[:10]
            agg_p2_equity.append({"date": date_str, "equity": round(cum, 2), "drawdown_pct": round(dd_pct, 2)})
            daily_pnl[date_str] += p

          # Monthly matrix from daily pnl
          month_pnl: dict[str, dict[str, float]] = _dd(lambda: _dd(float))
          for ds, p in daily_pnl.items():
            if len(ds) >= 7:
              y = ds[:4]; m = str(int(ds[5:7]))
              month_pnl[y][m] += p
          years = sorted(month_pnl.keys())
          matrix = {y: {m: round(v, 2) for m, v in month_pnl[y].items()} for y in years}
          row_totals = {y: round(sum(month_pnl[y].values()), 2) for y in years}
          col_totals: dict[str, float] = _dd(float)
          for y in years:
            for m, v in month_pnl[y].items():
              col_totals[m] += v
          agg_p2_monthly = {
            "years": years, "matrix": matrix,
            "row_totals": row_totals,
            "col_totals": {m: round(v, 2) for m, v in col_totals.items()},
            "grand_total": round(sum(row_totals.values()), 2),
          }

          # Asset breakdown (per-asset OOS only — Phase 2 has no IS concept)
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

          # Cumulative metrics dict in the SAME shape as Phase 1 cumulative_oos
          # so the frontend IS vs OOS comparison table can consume it.
          _sm = agg_summary
          _gp = sum(p for p in agg_selected_pnls if p > 0)
          _gl = abs(sum(p for p in agg_selected_pnls if p < 0))
          _wins = sum(1 for p in agg_selected_pnls if p > 0)
          _n = len(agg_selected_pnls)
          agg_p2_cumulative = {
            "net_profit": _sm.get("net_profit", 0),
            "profit_factor": _sm.get("pf", 0),
            "win_rate": round(_wins / _n, 4) if _n else 0,  # 0..1 ratio (matches Phase 1 format)
            "total_trades": _n,
            "total_positions": _n,
            "max_drawdown": _sm.get("max_dd", 0),
            "cumulative_max_dd": _sm.get("max_dd", 0),  # cross-window MDD from concatenated PnL
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
            "payoff_ratio": round((_gp / _wins) / (_gl / (_n - _wins)), 4) if _wins > 0 and (_n - _wins) > 0 and _gl > 0 else 0,
          }

        if agg_selected_records:
          _rec_batch_ids = {int(r["batch_id"]) for r in agg_selected_records if r.get("batch_id")}
          if _rec_batch_ids:
            _batch_meta_rows = (
              db.query(
                BatchBacktestRun.id,
                BatchBacktestRun.name,
                BatchGroup.name.label("group_name"),
                BatchGroup.pnl_multiplier,
              )
              .outerjoin(BatchGroup, BatchBacktestRun.group_id == BatchGroup.id)
              .filter(BatchBacktestRun.id.in_(list(_rec_batch_ids)))
              .all()
            )
            _batch_meta = {
              row.id: {
                "batch_name": row.name or "",
                "batch_group": row.group_name or "",
                "pnl_multiplier": float(row.pnl_multiplier) if row.pnl_multiplier is not None else 1.0,
              }
              for row in _batch_meta_rows
            }
            for _r in agg_selected_records:
              _bid = _r.get("batch_id")
              if _bid and _bid in _batch_meta:
                _r["batch_name"] = _batch_meta[_bid]["batch_name"]
                _r["batch_group"] = _batch_meta[_bid]["batch_group"]
                _r["pnl_multiplier"] = _batch_meta[_bid]["pnl_multiplier"]

        return {
          "mode": "event", "walk_forward": True,
          "windows_processed": n_done, "windows_total": len(oos_windows),
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
          # Phase 2 aggregates — mirror Phase 1 aggregate_oos_* shape
          "aggregate_equity_curve": agg_p2_equity,
          "aggregate_monthly_matrix": agg_p2_monthly,
          "aggregate_breakdown": agg_p2_breakdown,
          "cumulative_metrics": agg_p2_cumulative,
          # Per-trade records so the month modal can filter to Phase 2 only
          "selected_records": agg_selected_records,
          # Config digest: exactly what Step 2 used (diagnose preset-vs-actual mismatches)
          "config_used": {
            "n_trades": req.hybrid_n_trades,
            "n_assets": req.hybrid_n_assets,
            "min_hold": req.hybrid_min_hold,
            "rebalance_every": req.hybrid_rebalance_every,
            "rebalance_mode": req.hybrid_rebalance_mode,
            "scoring_method": req.hybrid_scoring_method,
            "objective": req.hybrid_objective,
            "composite_weights": req.hybrid_composite_weights,
            "exec_day_profit": req.hybrid_exec_day_profit,
            "staleness_days": req.hybrid_staleness_days,
            "min_pf": req.hybrid_min_pf,
            "min_wr": req.hybrid_min_wr,
            "warmup_trades": req.hybrid_warmup_trades,
            "session_filter_exclude_days": req.session_filter_exclude_days,
            "session_filter_exclude_hours_count": len(req.session_filter_exclude_hours),
            "preset_id": getattr(req, "preset_id", None),
          },
        }

      # ── Check if async mode requested (frontend polls progress) ──
      _async_mode = request.query_params.get("async") == "1"

      if _async_mode:
        # Generate a unique run_id, spawn background thread, return immediately
        run_id = str(uuid.uuid4())
        with _wfo_lock:
          _wfo_progress[run_id] = {"step": 0, "total": 0, "label": "Loading trades..."}

        def _progress_cb(step: int, total: int, label: str):
          with _wfo_lock:
            _wfo_progress[run_id] = {"step": step, "total": total, "label": label}

        # Capture request data needed for DB persist (thread-safe copies)
        _req_dump = req.model_dump()
        _req_batch_ids = list(req.batch_ids)
        _req_portfolio_ids = list(req.portfolio_ids)

        def _run_bs_background():
          try:
            # Create thread-local DB session for category filter lookups (SQLAlchemy sessions are not thread-safe).
            from backend.db.database import SessionLocal as _SessionLocal
            _bg_db = _SessionLocal() if bs_config.category_filter_enabled else None
            try:
              result = run_batch_selection(
                all_trades, windows, bs_config, run_batch_map,
                progress_callback=_progress_cb, batch_info=_batch_info, db=_bg_db,
              )
            finally:
              if _bg_db is not None:
                _bg_db.close()
            result["pnl_scaling_diagnostic"] = _pnl_diag
            if _sf_diag:
              result["session_filter_diagnostic"] = _sf_diag
            result["window_diagnostic"] = _win_diag
            if _risk_diag:
              result["risk_profile_diagnostic"] = _risk_diag

            # Hybrid Step 2 walk-forward (background path)
            try:
              _bg_step2 = _run_hybrid_step2(result)
              if _bg_step2 is not None:
                result["step2"] = _bg_step2
            except Exception as _hyb_err:
              log.warning("Async WFO: hybrid Step 2 failed: %s", _hyb_err)

            # Persist to DB using a new session (can't share across threads)
            try:
              _bs_settings = {
                "wfo_mode": "batch_selection",
                **{k: v for k, v in _req_dump.items() if k.startswith("bs_")},
                "session_filter_enabled": _req_dump.get("session_filter_enabled"),
                "session_filter_mode": _req_dump.get("session_filter_mode"),
                "session_filter_exclude_hours": _req_dump.get("session_filter_exclude_hours"),
                "session_filter_exclude_days": _req_dump.get("session_filter_exclude_days"),
                "session_filter_grade_on_cropped": _req_dump.get("session_filter_grade_on_cropped"),
                "hybrid_mode": _req_dump.get("hybrid_mode"),
                **{k: v for k, v in _req_dump.items() if k.startswith("hybrid_")},
                # Persist the data-start and warmup controls so a replay uses
                # the same window-generation inputs. Without these, replay
                # falls back to min(trade.entry_time) and produces a different
                # data_start, different windows, and different asset picks.
                "date_from": _req_dump.get("date_from"),
                "warmup_trades": _req_dump.get("warmup_trades"),
                "replay_as_of": _req_dump.get("replay_as_of"),
                **({"preset_id": _req_dump["preset_id"]} if _req_dump.get("preset_id") else {}),
              }
              # Persist Phase 2 (hybrid Step 2) result inside optimizer_settings JSON
              # so the saved-runs view can show TRUE Phase 2 numbers (not Phase 1).
              # Stored under key "step2_result" — additive, no DB schema change.
              if result.get("step2") is not None:
                _bs_settings["step2_result"] = result["step2"]
              from backend.db.database import SessionLocal
              bg_db = SessionLocal()
              try:
                wfo_run = WFORun(
                  batch_id=_req_batch_ids[0] if _req_batch_ids else 0,
                  batch_ids=json.dumps(_req_batch_ids),
                  is_months=_req_dump["is_months"],
                  oos_months=_req_dump["oos_months"],
                  last_oos_end=_req_dump["last_oos_end"],
                  n_candidates=_req_dump["bs_n_candidates"],
                  optimizer_settings=json.dumps(_sanitize(_bs_settings)),
                  tracks=json.dumps(_sanitize(result["tracks"])),
                  cumulative_is=json.dumps(_sanitize(result["cumulative_is"])),
                  cumulative_oos=json.dumps(_sanitize(result["cumulative_oos"])),
                  time_mode="rolling",
                  event_mode=False,
                  portfolio_ids=json.dumps(_req_portfolio_ids),
                )
                bg_db.add(wfo_run)
                bg_db.flush()
                for w in result["windows"]:
                  bg_db.add(WFOWindow(
                    wfo_run_id=wfo_run.id,
                    window_num=w["window_num"],
                    is_start=w["is_start"],
                    is_end=w["is_end"],
                    oos_start=w.get("oos_start"),
                    oos_end=w.get("oos_end"),
                    candidates=json.dumps(_sanitize(w["candidates"])),
                    oos_results=json.dumps(_sanitize([c.get("oos_metrics") for c in w["candidates"]])),
                    is_trade_count=w["is_trade_count"],
                    is_symbol_count=w.get("is_symbol_count", 0),
                    is_pool_size=w.get("is_pool_size", 0),
                  ))
                bg_db.commit()
                result["wfo_run_id"] = wfo_run.id
              finally:
                bg_db.close()
            except Exception as db_err:
              log.warning("Async WFO: DB persist failed: %s", db_err)

            with _wfo_lock:
              _wfo_results[run_id] = _sanitize(result)
          except Exception as exc:
            log.error("Async WFO failed: %s", exc, exc_info=True)
            with _wfo_lock:
              _wfo_results[run_id] = {"error": str(exc)}

        thread = threading.Thread(target=_run_bs_background, daemon=True)
        thread.start()
        return {"run_id": run_id, "async": True}

      # ── Synchronous path (original behavior) ──
      result = run_batch_selection(all_trades, windows, bs_config, run_batch_map, batch_info=_batch_info, db=db)

      # Attach diagnostics
      result["pnl_scaling_diagnostic"] = _pnl_diag
      if _sf_diag:
        result["session_filter_diagnostic"] = _sf_diag
      result["window_diagnostic"] = _win_diag

      # ── 2-Step Hybrid: walk-forward Step 2 via shared helper ──
      step2_result = _run_hybrid_step2(result)

      # Persist to DB (same schema as optimizer WFO)
      bs_settings = {
        "wfo_mode": "batch_selection",
        **{k: v for k, v in req.model_dump().items() if k.startswith("bs_")},
        # Include session filter settings for reproducibility
        "session_filter_enabled": req.session_filter_enabled,
        "session_filter_mode": req.session_filter_mode,
        "session_filter_exclude_hours": req.session_filter_exclude_hours,
        "session_filter_exclude_days": req.session_filter_exclude_days,
        "session_filter_grade_on_cropped": req.session_filter_grade_on_cropped,
        # Include hybrid settings for reproducibility
        "hybrid_mode": req.hybrid_mode,
        **{k: v for k, v in req.model_dump().items() if k.startswith("hybrid_")},
        # Persist the data-start and warmup controls so a replay uses
        # the same window-generation inputs. Without these, replay
        # falls back to min(trade.entry_time) and produces a different
        # data_start, different windows, and different asset picks.
        "date_from": req.date_from,
        "warmup_trades": req.warmup_trades,
        "replay_as_of": req.replay_as_of,
        **({"preset_id": req.preset_id} if req.preset_id else {}),
      }
      # Persist Phase 2 result inside optimizer_settings JSON (additive, no schema change)
      if step2_result is not None:
        bs_settings["step2_result"] = step2_result
      try:
        wfo_run = WFORun(
          batch_id=req.batch_ids[0] if req.batch_ids else 0,
          batch_ids=json.dumps(req.batch_ids),
          is_months=req.is_months,
          oos_months=req.oos_months,
          last_oos_end=req.last_oos_end,
          n_candidates=req.bs_n_candidates,
          optimizer_settings=json.dumps(_sanitize(bs_settings)),
          tracks=json.dumps(_sanitize(result["tracks"])),
          cumulative_is=json.dumps(_sanitize(result["cumulative_is"])),
          cumulative_oos=json.dumps(_sanitize(result["cumulative_oos"])),
          time_mode="rolling",
          event_mode=False,
          portfolio_ids=json.dumps(list(req.portfolio_ids)),
        )
        db.add(wfo_run)
        db.flush()

        for w in result["windows"]:
          db.add(WFOWindow(
            wfo_run_id=wfo_run.id,
            window_num=w["window_num"],
            is_start=w["is_start"],
            is_end=w["is_end"],
            oos_start=w.get("oos_start"),
            oos_end=w.get("oos_end"),
            candidates=json.dumps(_sanitize(w["candidates"])),
            oos_results=json.dumps(_sanitize([c.get("oos_metrics") for c in w["candidates"]])),
            is_trade_count=w["is_trade_count"],
            is_symbol_count=w.get("is_symbol_count", 0),
            is_pool_size=w.get("is_pool_size", 0),
          ))
        db.commit()

        result["wfo_run_id"] = wfo_run.id
      except Exception as _save_err:
        log.warning("Could not persist WFO run to DB (read-only?): %s", _save_err)
        try:
          db.rollback()
        except Exception as _exc:
          log.debug("swallowed: %s", _exc)
      if _risk_diag:
        result["risk_profile_diagnostic"] = _risk_diag
      # Attach hybrid Step 2 result
      if step2_result is not None:
        result["step2"] = step2_result
      return _sanitize(result)

    # Build full optimizer config
    # NOTE: ``date_from`` is intentionally KEPT in the persisted config so
    # that a replay reconstructs the same window-generation inputs. Without
    # it, a replay falls back to min(trade.entry_time) and produces a
    # different data_start, windows, and asset picks.
    config = req.model_dump(exclude={
      "batch_ids", "portfolio_ids", "is_months", "oos_months",
      "last_oos_end", "static_step_months", "min_oos_months",
      "oos_total_months",
    })
    config["time_mode"] = req.time_mode

    # Total source count (batches + portfolios)
    total_sources = len(req.batch_ids) + len(req.portfolio_ids)

    # Pass run_batch_map so WFAS can build batch-tagged daily PnL
    # (same as Cross-Strategy: SYMBOL~B{bid}_Side keeps same-symbol
    # assets from different batches separate)
    if run_batch_map:
      config["_run_batch_map"] = run_batch_map

    # Always build asset→batch mapping for pool-per-batch diversity (multi-source)
    if total_sources >= 2:
      from backend.api.routes.cross_strategy import _build_asset_batch_map
      abm = _build_asset_batch_map(req.batch_ids, db) if req.batch_ids else {}
      if req.portfolio_ids:
        abm.update(p_asset_batch)
      config["_asset_batch_map"] = abm

    # Manual/Directional mode also need strategy map and batch IDs
    if (req.wfo_mode == "manual" or req.directional_batches) and total_sources >= 2:
      from backend.api.routes.cross_strategy import _strategy_map
      sm = _strategy_map(req.batch_ids, db) if req.batch_ids else {}
      if req.portfolio_ids:
        sm.update(p_strat_map)
      config["_strategy_map"] = sm
      config["_batch_ids"] = list(req.batch_ids) + [-pid for pid in req.portfolio_ids]

    # Directional batches: override wfo_mode to trigger beam search
    if req.directional_batches and req.l2_groups:
      config["wfo_mode"] = "directional"

    # Boundary clipping: resolve timeframe for OHLCV lookup
    _clip_db = None
    if req.clip_is:
      if req.batch_ids:
        batch_obj = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == req.batch_ids[0]).first()
        config["_timeframe"] = batch_obj.timeframe if batch_obj else "D"
      else:
        config["_timeframe"] = "D"
      _clip_db = db

    result = run_wfo_asset_selection(
      all_trades, windows, config, req.n_candidates, req.corr_method, req.div_algo,
      db=_clip_db,
    )

    # Persist to DB (wrapped in try/except for read-only DB environments)
    try:
      wfo_run = WFORun(
        batch_id=req.batch_ids[0] if req.batch_ids else 0,
        batch_ids=json.dumps(req.batch_ids),
        is_months=req.is_months,
        oos_months=req.oos_months,
        last_oos_end=req.last_oos_end,
        n_candidates=req.n_candidates,
        optimizer_settings=json.dumps(_sanitize(config)),
        tracks=json.dumps(_sanitize(result["tracks"])),
        cumulative_is=json.dumps(_sanitize(result["cumulative_is"])),
        cumulative_oos=json.dumps(_sanitize(result["cumulative_oos"])),
        time_mode=req.time_mode,
        event_mode=False,
        portfolio_ids=json.dumps(list(req.portfolio_ids)),
        cumulative_oos_h1=json.dumps(_sanitize(result.get("cumulative_oos_h1", {}))) if result.get("cumulative_oos_h1") else None,
        cumulative_oos_h2=json.dumps(_sanitize(result.get("cumulative_oos_h2", {}))) if result.get("cumulative_oos_h2") else None,
        avg_degradation=result.get("summary", {}).get("avg_degradation"),
      )
      db.add(wfo_run)
      db.flush()

      for w in result["windows"]:
        db.add(WFOWindow(
          wfo_run_id=wfo_run.id,
          window_num=w["window_num"],
          is_start=w["is_start"],
          is_end=w["is_end"],
          oos_start=w.get("oos_start"),
          oos_end=w.get("oos_end"),
          candidates=json.dumps(_sanitize(w["candidates"])),
          oos_results=json.dumps(_sanitize([c.get("oos_metrics") for c in w["candidates"]])),
          is_trade_count=w["is_trade_count"],
          is_symbol_count=w["is_symbol_count"],
          is_pool_size=w.get("is_pool_size", 0),
          # Overlapping H1/H2 fields
          oos_h1_start=w.get("oos_h1_start"),
          oos_h1_end=w.get("oos_h1_end"),
          oos_h2_start=w.get("oos_h2_start"),
          oos_h2_end=w.get("oos_h2_end"),
          oos_h1_results=json.dumps(_sanitize([c.get("oos_h1_metrics") for c in w["candidates"]])) if w.get("oos_h1_start") else None,
          oos_h2_results=json.dumps(_sanitize([c.get("oos_h2_metrics") for c in w["candidates"]])) if w.get("oos_h2_start") else None,
          degradation_ratio=w["candidates"][0].get("degradation_ratio") if w["candidates"] else None,
        ))
      db.commit()

      result["wfo_run_id"] = wfo_run.id
    except Exception as _save_err:
      log.warning("Could not persist WFO run to DB (read-only?): %s", _save_err)
      try:
        db.rollback()
      except Exception as _exc:
        log.debug("swallowed: %s", _exc)
    if _risk_diag:
      result["risk_profile_diagnostic"] = _risk_diag
    return _sanitize(result)

  except HTTPException:
    raise
  except Exception as exc:
    from backend.api.routes.optimization_errors import wrap_optimization_error
    raise wrap_optimization_error("WFO Asset Selection", exc) from exc


# ---------------------------------------------------------------------------
# ORM → dict helpers (used by list/get/rerun endpoints)
# ---------------------------------------------------------------------------

def _run_to_dict(r: WFORun) -> dict:
  """Convert a WFORun ORM instance to a JSON-safe dict (basic fields only).

  Does NOT include windows, tracks, or cumulative metrics — callers add those.
  """
  # Parse cumulative OOS for list-level display (PnL, PF, WR, MDD)
  cum_oos_raw = {}
  try:
    if r.cumulative_oos:
      cum_oos_raw = json.loads(r.cumulative_oos)
  except Exception as _exc:
    log.debug("swallowed: %s", _exc)

  return {
    "id": r.id,
    "batch_id": r.batch_id,
    "batch_ids": json.loads(r.batch_ids) if r.batch_ids else [],
    "is_months": r.is_months,
    "oos_months": r.oos_months,
    "last_oos_end": r.last_oos_end,
    "n_candidates": r.n_candidates,
    "optimizer_settings": json.loads(r.optimizer_settings) if r.optimizer_settings else {},
    "status": r.status,
    "created_at": r.created_at.isoformat() if r.created_at else None,
    "time_mode": getattr(r, "time_mode", "rolling"),
    "event_mode": getattr(r, "event_mode", False),
    "portfolio_ids": json.loads(r.portfolio_ids) if r.portfolio_ids else [],
    "avg_degradation": getattr(r, "avg_degradation", None),
    "parent_run_id": getattr(r, "parent_run_id", None),
    "window_count": len(r.windows) if r.windows else 0,
    "cumulative_oos": {
      "net_profit": cum_oos_raw.get("net_profit"),
      "profit_factor": cum_oos_raw.get("profit_factor"),
      "win_rate": cum_oos_raw.get("win_rate"),
      "max_drawdown": cum_oos_raw.get("max_drawdown"),
      "total_trades": cum_oos_raw.get("total_trades"),
      "sortino": cum_oos_raw.get("sortino"),
      "avg_window_pnl": cum_oos_raw.get("avg_window_pnl"),
      "total_windows_evaluated": cum_oos_raw.get("total_windows_evaluated"),
    } if cum_oos_raw else None,
  }


def _backfill_position_count(
  candidates: list[dict], oos_start: str | None, oos_end: str | None,
  opt_settings: dict | None, db,
) -> None:
  """Compute total_positions for old runs that only have total_trades.

  Queries actual BacktestTrade rows and counts unique positions
  (grouped by position_id or entry_time per run).
  """
  if not candidates or not oos_start or not oos_end:
    return
  c = candidates[0]
  oos_m = c.get("oos_metrics") or c.get("oos_exec_metrics")
  if not oos_m or "total_positions" in oos_m:
    return  # already has position count

  # Get selected batches + assets for this window
  sel_batches = c.get("selected_batches", [])
  if not sel_batches:
    return
  asset_details = c.get("asset_details", [])

  # Build exec filter
  sf_enabled = (opt_settings or {}).get("session_filter_enabled", False)
  ex_hours = set((opt_settings or {}).get("session_filter_exclude_hours", []))
  ex_days = set((opt_settings or {}).get("session_filter_exclude_days", []))
  has_ef = sf_enabled and bool(ex_hours or ex_days)

  # Get run_ids for selected batches
  run_rows = (
    db.query(BacktestRun.id, BacktestRun.batch_id)
    .filter(BacktestRun.batch_id.in_(sel_batches), BacktestRun.status == "completed")
    .all()
  )
  run_batch_map = {rid: bid for rid, bid in run_rows}
  if not run_batch_map:
    return

  # Query trades in OOS range by entry_time
  all_run_ids = list(run_batch_map.keys())
  from sqlalchemy import func
  trades = (
    db.query(
      BacktestTrade.run_id,
      BacktestTrade.symbol,
      BacktestTrade.side,
      BacktestTrade.entry_time,
      BacktestTrade.position_id,
    )
    .filter(
      BacktestTrade.run_id.in_(all_run_ids),
      BacktestTrade.entry_time >= oos_start,
      BacktestTrade.entry_time <= oos_end + " 23:59:59",
    )
    .all()
  )

  # Filter by exec filter + selected assets, count unique positions
  asset_set: set[str] | None = None
  if asset_details:
    asset_set = set()
    for ad in asset_details:
      bid = ad.get("batch_id")
      sym = ad.get("symbol", "")
      side = ad.get("side", "Long")
      if bid:
        asset_set.add(f"{bid}_{sym}_{side}".lower())

  position_keys: set[str] = set()
  for run_id, symbol, side, entry_time, position_id in trades:
    if not entry_time:
      continue
    bid = run_batch_map.get(run_id)
    if bid is None:
      continue

    # Exec filter — CS-014: share predicate with canonical filter
    if has_ef and is_excluded_by_exec_filter(entry_time, ex_hours, ex_days):
      continue

    # Asset filter
    if asset_set:
      ak = f"{bid}_{symbol}_{side}".lower()
      if ak not in asset_set:
        continue

    # Position key
    if position_id:
      pk = f"{run_id}_{position_id}"
    else:
      pk = f"{bid}_{symbol}_{side}_{entry_time.isoformat()}"
    position_keys.add(pk)

  pos_count = len(position_keys)

  # Patch all metric dicts
  for mk in ("oos_metrics", "oos_exec_metrics", "oos_full_metrics"):
    m = c.get(mk)
    if m and "total_trades" in m:
      m["total_positions"] = pos_count


def _window_to_dict(w: WFOWindow, opt_settings: dict | None = None, db=None) -> dict:
  """Convert a WFOWindow ORM instance to a JSON-safe dict with parsed JSON fields."""
  candidates = json.loads(w.candidates) if w.candidates else []

  # Backfill total_positions for old runs missing it
  if db and candidates:
    _backfill_position_count(candidates, w.oos_start, w.oos_end, opt_settings, db)

  d = {
    "id": w.id,
    "wfo_run_id": w.wfo_run_id,
    "window_num": w.window_num,
    "is_start": w.is_start,
    "is_end": w.is_end,
    "oos_start": w.oos_start,
    "oos_end": w.oos_end,
    "candidates": candidates,
    "oos_results": json.loads(w.oos_results) if w.oos_results else [],
    "is_trade_count": w.is_trade_count or 0,
    "is_symbol_count": w.is_symbol_count or 0,
    "is_pool_size": w.is_pool_size or 0,
  }
  # Overlapping mode fields
  if getattr(w, "oos_h1_start", None):
    d["oos_h1_start"] = w.oos_h1_start
    d["oos_h1_end"] = w.oos_h1_end
  if getattr(w, "oos_h2_start", None):
    d["oos_h2_start"] = w.oos_h2_start
    d["oos_h2_end"] = w.oos_h2_end
  if getattr(w, "oos_h1_results", None):
    d["oos_h1_results"] = json.loads(w.oos_h1_results)
  if getattr(w, "oos_h2_results", None):
    d["oos_h2_results"] = json.loads(w.oos_h2_results)
  if getattr(w, "degradation_ratio", None) is not None:
    d["degradation_ratio"] = w.degradation_ratio
  return d


@router.get("/runs")
def list_wfo_runs(db: Session = Depends(get_db)):
  """List all saved WFO runs (excludes soft-deleted)."""
  runs = db.query(WFORun).filter(WFORun.is_deleted != True).order_by(WFORun.created_at.desc()).all()
  return [_run_to_dict(r) for r in runs]


@router.delete("/runs/{run_id}")
def delete_wfo_run(run_id: int, db: Session = Depends(get_db)):
  """Soft-delete a WFO run."""
  from datetime import datetime, timezone
  r = db.query(WFORun).filter(WFORun.id == run_id).first()
  if not r:
    raise HTTPException(404, "WFO run not found")
  r.is_deleted = True
  r.deleted_at = datetime.now(timezone.utc)
  db.commit()
  return {"deleted": True, "id": run_id}


def _rebuild_summary(windows: list[dict], settings: dict) -> dict:
  """Reconstruct a summary dict from saved window data.

  Used when loading saved runs (which don't persist the summary object).
  Mirrors the summary logic in batch_selection.py → run_batch_selection().
  """
  from backend.analysis.wfo_aggregation import aggregate_summary

  time_mode = settings.get("time_mode", "rolling")
  summary = aggregate_summary(windows, time_mode)

  is_bs = settings.get("wfo_mode") == "batch_selection"
  if not is_bs:
    return summary

  # ── Batch-selection-specific stats ──
  oos_windows = [w for w in windows if w.get("oos_start")]
  q_starts = settings.get("bs_q_starts", 1)

  # Exclude cold-start windows (first per Q-start, or global first)
  if q_starts >= 4:
    q_first_wins: set[int] = set()
    seen_q: set[int] = set()
    for w in oos_windows:
      q_idx = w.get("q_start_idx", 0)
      if q_idx not in seen_q:
        seen_q.add(q_idx)
        q_first_wins.add(w["window_num"])
    cal_windows = [w for w in oos_windows if w["window_num"] not in q_first_wins]
  else:
    cal_windows = oos_windows[1:]  # Exclude cold-start window 1

  if cal_windows:
    def _cand_metric(w: dict, key: str) -> float:
      cs = w.get("candidates", [])
      return cs[0].get("oos_metrics", {}).get(key, 0) if cs else 0

    cal_wins = sum(1 for w in cal_windows if _cand_metric(w, "net_profit") > 0)
    summary["calibrated_wr"] = f"{cal_wins}/{len(cal_windows)}"
    summary["calibrated_wr_pct"] = round(cal_wins / len(cal_windows) * 100, 1)
    summary["calibrated_pnl"] = round(sum(_cand_metric(w, "net_profit") for w in cal_windows), 2)

    # Full OOS WR (unfiltered)
    def _full_metric(w: dict, key: str) -> float:
      cs = w.get("candidates", [])
      return cs[0].get("oos_full_metrics", {}).get(key, 0) if cs else 0

    full_wins = sum(1 for w in cal_windows if _full_metric(w, "net_profit") > 0)
    summary["full_wr"] = f"{full_wins}/{len(cal_windows)}"
    summary["full_wr_pct"] = round(full_wins / len(cal_windows) * 100, 1)
    summary["full_pnl"] = round(sum(_full_metric(w, "net_profit") for w in cal_windows), 2)

    # Exec OOS WR (if filter was active)
    has_exec = bool(settings.get("session_filter_enabled") and settings.get("session_filter_exclude_hours"))
    if has_exec:
      def _exec_metric(w: dict, key: str) -> float:
        cs = w.get("candidates", [])
        return cs[0].get("oos_exec_metrics", {}).get(key, 0) if cs else 0

      exec_wins = sum(1 for w in cal_windows if _exec_metric(w, "net_profit") > 0)
      summary["exec_wr"] = f"{exec_wins}/{len(cal_windows)}"
      summary["exec_wr_pct"] = round(exec_wins / len(cal_windows) * 100, 1)
      summary["exec_pnl"] = round(sum(_exec_metric(w, "net_profit") for w in cal_windows), 2)
      summary["delta_pp"] = round(summary["exec_wr_pct"] - summary["full_wr_pct"], 1)
      if summary["full_pnl"] != 0:
        summary["ef_ratio_pct"] = round(summary["exec_pnl"] / summary["full_pnl"] * 100, 1)

  summary["mode"] = "batch_selection"
  summary["has_exec_filter"] = bool(settings.get("session_filter_enabled") and settings.get("session_filter_exclude_hours"))
  summary["q_starts"] = q_starts
  return summary


def _rebuild_aggregate_oos(windows: list[dict]) -> dict:
  """Reconstruct aggregate OOS equity curve, monthly matrix, and asset breakdown
  from saved window data.  Mirrors batch_selection.py end-of-run aggregation."""
  historical = [w for w in windows if not w.get("is_live") and w.get("oos_start")]

  # ── Equity curve: stitch per-window OOS equity curves ──
  daily_all: list[tuple[str, float]] = []
  for w in historical:
    cands = w.get("candidates", [])
    if not cands:
      continue
    ec = cands[0].get("oos_equity_curve", [])
    for i, pt in enumerate(ec):
      daily_pnl = pt["equity"] if i == 0 else pt["equity"] - ec[i - 1]["equity"]
      daily_all.append((pt["date"], daily_pnl))

  equity_curve: list[dict] = []
  if daily_all:
    daily_all.sort(key=lambda x: x[0])
    cumulative = 0.0
    peak = 0.0
    for date_str, pnl in daily_all:
      cumulative += pnl
      peak = max(peak, cumulative)
      dd_pct = (cumulative - peak) / peak * 100 if peak > 0 else 0
      equity_curve.append({"date": date_str, "equity": round(cumulative, 2), "drawdown_pct": round(dd_pct, 2)})

  # ── Monthly matrix ──
  monthly_matrix: dict = {"years": [], "matrix": {}, "row_totals": {}, "col_totals": {}, "grand_total": 0}
  if daily_all:
    from collections import defaultdict
    month_pnl: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for date_str, pnl in daily_all:
      y = date_str[:4]
      m = str(int(date_str[5:7]))
      month_pnl[y][m] += pnl
    years = sorted(month_pnl.keys())
    matrix = {y: dict(month_pnl[y]) for y in years}
    row_totals = {y: round(sum(month_pnl[y].values()), 2) for y in years}
    col_totals: dict[str, float] = defaultdict(float)
    for y in years:
      for m, v in month_pnl[y].items():
        col_totals[m] += v
    col_totals_rounded = {m: round(v, 2) for m, v in col_totals.items()}
    # Round matrix values
    for y in matrix:
      matrix[y] = {m: round(v, 2) for m, v in matrix[y].items()}
    monthly_matrix = {
      "years": years, "matrix": matrix,
      "row_totals": row_totals, "col_totals": col_totals_rounded,
      "grand_total": round(sum(row_totals.values()), 2),
    }

  # ── Asset breakdown: aggregate per unique asset key ──
  bd_accum: dict[str, dict] = {}
  for w in historical:
    cands = w.get("candidates", [])
    if not cands:
      continue
    bd = cands[0].get("asset_breakdown")
    if not bd:
      continue
    for item in bd:
      if item.get("oos_trades", 0) == 0:
        continue
      k = item["key"]
      if k not in bd_accum:
        bd_accum[k] = {
          "key": k, "batch_id": item["batch_id"],
          "symbol": item["symbol"], "side": item["side"],
          "timeframe": item.get("timeframe", ""), "group": item.get("group", ""),
          "oos_pnl": 0.0, "oos_trades": 0, "oos_wr_w": 0.0,
          "oos_gp": 0.0, "oos_gl": 0.0,
          "is_pnl": 0.0, "is_trades": 0, "is_wr_w": 0.0,
          "is_gp": 0.0, "is_gl": 0.0,
        }
      a = bd_accum[k]
      a["oos_pnl"] += item.get("oos_pnl", 0)
      a["oos_trades"] += item.get("oos_trades", 0)
      a["oos_wr_w"] += item.get("oos_wr", 0) * item.get("oos_trades", 0)
      a["oos_gp"] += max(item.get("oos_pnl", 0), 0)
      a["oos_gl"] += min(item.get("oos_pnl", 0), 0)
      a["is_pnl"] += item.get("is_pnl", 0)
      a["is_trades"] += item.get("is_trades", 0)
      a["is_wr_w"] += item.get("is_wr", 0) * item.get("is_trades", 0)
      a["is_gp"] += max(item.get("is_pnl", 0), 0)
      a["is_gl"] += min(item.get("is_pnl", 0), 0)

  breakdown: list[dict] = []
  for a in bd_accum.values():
    oos_t = a["oos_trades"]
    is_t = a["is_trades"]
    oos_gl = abs(a["oos_gl"])
    is_gl = abs(a["is_gl"])
    breakdown.append({
      "key": a["key"], "batch_id": a["batch_id"],
      "symbol": a["symbol"], "side": a["side"],
      "timeframe": a["timeframe"], "group": a["group"],
      "oos_wr": a["oos_wr_w"] / oos_t if oos_t else 0,
      "oos_pf": min(a["oos_gp"] / oos_gl, 10) if oos_gl else (10 if a["oos_gp"] > 0 else 0),
      "oos_pnl": round(a["oos_pnl"], 2), "oos_trades": oos_t,
      "is_wr": a["is_wr_w"] / is_t if is_t else 0,
      "is_pf": min(a["is_gp"] / is_gl, 10) if is_gl else (10 if a["is_gp"] > 0 else 0),
      "is_pnl": round(a["is_pnl"], 2), "is_trades": is_t,
    })
  breakdown.sort(key=lambda x: x["oos_pnl"], reverse=True)

  return {"equity_curve": equity_curve, "monthly_matrix": monthly_matrix, "breakdown": breakdown}


@router.get("/runs/{run_id}")
def get_wfo_run(run_id: int, db: Session = Depends(get_db)):
  """Load a saved WFO run with all windows and tracks."""
  r = db.query(WFORun).filter(WFORun.id == run_id).first()
  if not r:
    raise HTTPException(404, "WFO run not found")
  d = _run_to_dict(r)
  opt_settings = json.loads(r.optimizer_settings) if r.optimizer_settings else {}
  d["windows"] = [_window_to_dict(w, opt_settings=opt_settings, db=db) for w in r.windows]
  d["tracks"] = json.loads(r.tracks) if r.tracks else []
  d["cumulative_is"] = json.loads(r.cumulative_is) if r.cumulative_is else {}
  d["cumulative_oos"] = json.loads(r.cumulative_oos) if r.cumulative_oos else {}
  if getattr(r, "cumulative_oos_h1", None):
    d["cumulative_oos_h1"] = json.loads(r.cumulative_oos_h1)
  if getattr(r, "cumulative_oos_h2", None):
    d["cumulative_oos_h2"] = json.loads(r.cumulative_oos_h2)

  # Backfill total_positions in cumulative_oos if missing
  cum_oos = d["cumulative_oos"]
  if cum_oos and "total_positions" not in cum_oos:
    total_pos = sum(
      (w.get("candidates", [{}])[0].get("oos_metrics") or
       w.get("candidates", [{}])[0].get("oos_exec_metrics") or {}).get("total_positions", 0)
      for w in d["windows"] if w.get("candidates")
    )
    cum_oos["total_positions"] = total_pos

  # Reconstruct summary (not persisted to DB)
  d["summary"] = _rebuild_summary(d["windows"], opt_settings)

  # Set wfo_run_id so frontend can use it for month-trades modal etc.
  d["wfo_run_id"] = r.id

  # Attach Phase 2 (hybrid Step 2) result if this was a hybrid run.
  # Stored inside optimizer_settings.step2_result by the runner; the frontend
  # expects it at result.step2 to render the Step 2 Event-Based panel.
  _step2 = opt_settings.get("step2_result") if isinstance(opt_settings, dict) else None
  if _step2:
    # Backfill batch metadata on saved runs whose selected_records predate the
    # enrichment added at live-response time. Adds batch_name/batch_group/
    # pnl_multiplier so the Phase 2 month modal shows the same Group+Batch
    # columns as the regular (non-hybrid) view.
    _recs = _step2.get("selected_records") or []
    if _recs:
      _needs_enrich = any(
        r.get("batch_id") and "batch_group" not in r for r in _recs
      )
      if _needs_enrich:
        _rec_batch_ids = {int(r["batch_id"]) for r in _recs if r.get("batch_id")}
        if _rec_batch_ids:
          _rows = (
            db.query(
              BatchBacktestRun.id,
              BatchBacktestRun.name,
              BatchGroup.name.label("group_name"),
              BatchGroup.pnl_multiplier,
            )
            .outerjoin(BatchGroup, BatchBacktestRun.group_id == BatchGroup.id)
            .filter(BatchBacktestRun.id.in_(list(_rec_batch_ids)))
            .all()
          )
          _meta = {
            row.id: {
              "batch_name": row.name or "",
              "batch_group": row.group_name or "",
              "pnl_multiplier": float(row.pnl_multiplier) if row.pnl_multiplier is not None else 1.0,
            }
            for row in _rows
          }
          for _r in _recs:
            _bid = _r.get("batch_id")
            if _bid and _bid in _meta:
              _r["batch_name"] = _meta[_bid]["batch_name"]
              _r["batch_group"] = _meta[_bid]["batch_group"]
              _r["pnl_multiplier"] = _meta[_bid]["pnl_multiplier"]
    d["step2"] = _step2

  # Reconstruct aggregate OOS analysis from saved window data
  agg = _rebuild_aggregate_oos(d["windows"])
  d["aggregate_oos_equity_curve"] = agg["equity_curve"]
  d["aggregate_oos_monthly_matrix"] = agg["monthly_matrix"]
  d["aggregate_oos_breakdown"] = agg["breakdown"]

  return d


@router.get("/runs/{run_id}/window/{window_num}")
def get_wfo_window(run_id: int, window_num: int, db: Session = Depends(get_db)):
  """Full detail for one WFO window."""
  w = (
    db.query(WFOWindow)
    .filter(WFOWindow.wfo_run_id == run_id, WFOWindow.window_num == window_num)
    .first()
  )
  if not w:
    raise HTTPException(404, "Window not found")
  r = db.query(WFORun).filter(WFORun.id == run_id).first()
  opt_settings = json.loads(r.optimizer_settings) if r and r.optimizer_settings else {}
  return _window_to_dict(w, opt_settings=opt_settings, db=db)


@router.post("/save-track")
def save_wfo_track(req: WFOSaveTrackRequest, db: Session = Depends(get_db)):
  """Save a WFO track as a portfolio."""
  from datetime import datetime

  settings = dict(req.wfo_settings)
  settings["batch_ids"] = req.batch_ids
  settings["source"] = "wfo_asset_selection"
  settings["track_rank"] = req.track_rank

  opt_end = None
  if req.optimization_end_date:
    opt_end = datetime.fromisoformat(req.optimization_end_date)
  elif settings.get("hybrid_mode") and req.windows_meta:
    _tail_oos = (req.windows_meta[-1] or {}).get("oos_end")
    if _tail_oos:
      try:
        opt_end = datetime.fromisoformat(_tail_oos)
      except (TypeError, ValueError):
        opt_end = None

  # ── Live Trading metadata: resolve strategy + timeframe from winning batches ──
  # StrategyExecutor.activate_portfolio() needs: strategy_id, strategy_name,
  # timeframe (stored in source_strategies). Without these, activation falls
  # back to brittle chains. Mirrors /save-phase2-track.
  strategy_id = None
  strategy_name = ""
  base_timeframe = ""
  extra_timeframes: list[str] = []

  if req.batch_ids:
    for bid in req.batch_ids:
      batch = db.get(BatchBacktestRun, bid)
      if batch and batch.strategy_id:
        strategy = db.get(Strategy, batch.strategy_id)
        if strategy:
          strategy_id = strategy.id
          strategy_name = strategy.name
          break
    for bid in req.batch_ids:
      batch = db.get(BatchBacktestRun, bid)
      if batch and batch.group_id:
        group = db.get(BatchGroup, batch.group_id)
        if group and group.timeframe:
          base_timeframe = group.timeframe
          break
    if strategy_id:
      strategy = db.get(Strategy, strategy_id)
      if strategy and strategy.python_source:
        import re as _re
        tf_matches = _re.findall(r'request_security\s*\(\s*["\'](\w+)["\']', strategy.python_source)
        if tf_matches:
          extra_timeframes = list(dict.fromkeys(tf_matches))

  # Assets span multiple batches when WFO picks from >1 batch → per-asset
  # batch parameter lookup is required (is_cross_strat=True). This is the
  # whole point of WFAS: different assets can use different optimized configs.
  asset_batch_ids = {a.get("batch_id") for a in req.assets if a.get("batch_id")}
  is_cross_strat = len(asset_batch_ids) > 1

  settings["strategy_id"] = strategy_id
  settings["strategy_name"] = strategy_name
  settings["timeframe"] = base_timeframe
  settings["extra_timeframes"] = extra_timeframes
  settings["is_cross_strat"] = is_cross_strat

  # Persist walk-forward rotation so the Saved Portfolio view can reconstruct
  # the real per-window OOS (not the final basket applied to all history).
  if req.per_window:
    settings["wfo_per_window"] = req.per_window
  if req.windows_meta:
    settings["wfo_windows_meta"] = req.windows_meta

  portfolio = Portfolio(
    name=req.name,
    metrics_snapshot=json.dumps(_sanitize(req.metrics_snapshot)),
    source_strategies=json.dumps(settings),
    optimization_end_date=opt_end,
    portfolio_source="wfo",
    is_cross_strat=is_cross_strat,
    mode=("hybrid" if settings.get("hybrid_mode") else "non_hybrid"),
  )
  db.add(portfolio)
  db.flush()

  # Build batch→(timeframe, group) lookup for fallback enrichment
  _batch_info_map = _build_batch_info(req.batch_ids, db) if req.batch_ids else {}

  for asset in req.assets:
    bid = asset.get("batch_id")
    tf = asset.get("timeframe")
    gn = asset.get("group_name")
    # Fallback: derive from batch_info if frontend didn't provide
    if bid and (not tf or not gn) and bid in _batch_info_map:
      bi = _batch_info_map[bid]
      tf = tf or bi.get("timeframe")
      gn = gn or bi.get("group_name")
    db.add(PortfolioAsset(
      portfolio_id=portfolio.id,
      symbol=asset.get("symbol", ""),
      trade_side=asset.get("side", "both"),
      strategy_name=asset.get("strategy_name", ""),
      raw_name=asset.get("symbol", ""),
      batch_id=bid,
      config_name=asset.get("config_name"),
      timeframe=tf,
      group_name=gn,
    ))
  db.commit()
  db.refresh(portfolio)

  # Recalculate metrics_snapshot from trade-level data for accurate
  # expectancy metrics (PF, WR, positions, net profit).
  # Uses _quick_metrics() which matches the optimizer's _portfolio_metrics()
  # methodology (losses <= $0, daily PnL equity for DD/stability).
  try:
    import logging as _log

    from backend.api.routes.portfolio_analysis import _load_portfolio_trades, _quick_metrics
    df = _load_portfolio_trades(portfolio, db, date_filter="is")
    if not df.empty:
      orig = req.metrics_snapshot or {}
      recalc = _quick_metrics(df)
      # Preserve optimizer-calculated stability & n_assets
      recalc["stability"] = recalc.get("stability", 0) or orig.get("stability", 0)
      recalc["n_assets"] = orig.get("n_assets", len(req.assets))
      portfolio.metrics_snapshot = json.dumps(_sanitize(recalc))
      db.commit()
      db.refresh(portfolio)
  except Exception as e:
    _log.getLogger(__name__).warning("WFO post-save snapshot recalculation failed: %s", e)
    # If portfolio has OOS cutoff, clear snapshot rather than keep potentially combined data
    if opt_end:
      portfolio.metrics_snapshot = "{}"
      db.commit()
      _log.getLogger(__name__).warning("Cleared metrics_snapshot for WFO portfolio %d to prevent IS/OOS contamination", portfolio.id)

  # Stage 8 / Y-B: kick an immediate refresh on hybrid saves so the
  # newest OOS window gets graded and the cursor (last_oos_end_walked)
  # is set. From then on the nightly 03:00 NYT cron carries the basket
  # forward automatically. Best-effort: a refresh failure does NOT roll
  # back the save — the portfolio is still saved correctly and tonight's
  # cron will pick it up.
  if portfolio.mode == "hybrid":
    try:
      from backend.services.hybrid_portfolio_refresh import refresh_portfolio as _yb_refresh
      _refresh_log = _log.getLogger(__name__)
      _refresh_log.info("WFO save-track: kicking immediate Y-B refresh for hybrid portfolio %d", portfolio.id)
      _r = _yb_refresh(portfolio.id, db=db, allow_inactive=False)
      _refresh_log.info(
        "WFO save-track: immediate refresh result for %d — ok=%s reason=%s added=%s removed=%s basket=%s",
        portfolio.id, _r.get("ok"), _r.get("reason"),
        _r.get("added"), _r.get("removed"), _r.get("basket_size"),
      )
    except Exception as _e:
      _log.getLogger(__name__).warning(
        "WFO save-track: immediate refresh failed for portfolio %d: %s "
        "(save still committed; nightly cron will pick it up)",
        portfolio.id, _e,
      )

  return {
    "id": portfolio.id, "name": portfolio.name,
    "created_at": portfolio.created_at.isoformat() if portfolio.created_at else None,
  }


@router.post("/save-phase2-track")
def save_wfas_phase2_track(req: WFOSavePhase2Request, db: Session = Depends(get_db)):
  """Save Phase 2 (hybrid event WFO) results as a tradeable portfolio.

  Unlike save-track (which saves Phase 1 batch picks), this saves the
  actual asset rotation output from Phase 2 — the tradeable signal.

  Critical for Live Trading:
  - Resolves strategy_id, strategy_name, timeframe from winning batches
  - Stores these in source_strategies so StrategyExecutor can activate
  - Each PortfolioAsset gets batch_id for per-asset parameter lookup
  - Full preset config stored for reproducibility + refresh
  """
  from datetime import datetime

  opt_end = None
  if req.optimization_end_date:
    opt_end = datetime.fromisoformat(req.optimization_end_date)
  elif (req.preset_config or {}).get("hybrid_mode", True) and req.windows_meta:
    _tail_oos = (req.windows_meta[-1] or {}).get("oos_end")
    if _tail_oos:
      try:
        opt_end = datetime.fromisoformat(_tail_oos)
      except (TypeError, ValueError):
        opt_end = None

  # ── Resolve strategy metadata from winning batches (required for Live Trading) ──
  # StrategyExecutor.activate_portfolio() needs: strategy_id, strategy_name, timeframe
  # These come from the batches that Phase 1 selected (winning_batch_ids).
  strategy_id = None
  strategy_name = ""
  base_timeframe = ""
  extra_timeframes: list[str] = []

  resolve_batch_ids = req.winning_batch_ids or req.batch_ids
  if resolve_batch_ids:
    for bid in resolve_batch_ids:
      batch = db.get(BatchBacktestRun, bid)
      if batch and batch.strategy_id:
        strategy = db.get(Strategy, batch.strategy_id)
        if strategy:
          strategy_id = strategy.id
          strategy_name = strategy.name
          log.info("Phase 2 save: resolved strategy '%s' (id=%d) from batch %d",
                   strategy_name, strategy_id, bid)
          break

    # Resolve timeframe from batch group
    for bid in resolve_batch_ids:
      batch = db.get(BatchBacktestRun, bid)
      if batch and batch.group_id:
        group = db.get(BatchGroup, batch.group_id)
        if group and group.timeframe:
          base_timeframe = group.timeframe
          log.info("Phase 2 save: resolved timeframe '%s' from batch group %d",
                   base_timeframe, batch.group_id)
          break

    # Resolve extra timeframes from strategy source (auto-detect request_security calls)
    if strategy_id:
      strategy = db.get(Strategy, strategy_id)
      if strategy and strategy.python_source:
        import re as _re
        tf_matches = _re.findall(r'request_security\s*\(\s*["\'](\w+)["\']', strategy.python_source)
        if tf_matches:
          extra_timeframes = list(dict.fromkeys(tf_matches))
          log.info("Phase 2 save: auto-detected extra TFs from strategy source: %s", extra_timeframes)

  if not strategy_id:
    log.warning("Phase 2 save: could not resolve strategy from batches %s — "
                "Live Trading activation will use batch_id fallback", resolve_batch_ids)

  # Build batch→(timeframe, group, config) lookup for asset enrichment
  all_batch_ids = list(set(req.batch_ids + req.winning_batch_ids))
  _batch_info_map = _build_batch_info(all_batch_ids, db) if all_batch_ids else {}

  # Also build batch→config_name lookup from strategy_configs
  _batch_config_map: dict[int, str] = {}
  if strategy_id:
    for bid in all_batch_ids:
      batch = db.get(BatchBacktestRun, bid)
      if batch:
        bp = json.loads(batch.parameters or "{}")
        cfg_name = bp.get("config_name", "")
        if cfg_name:
          _batch_config_map[bid] = cfg_name

  # Store full config for reproducibility + Live Trading activation
  source_info = {
    "source": "wfas_phase2",
    "preset_id": req.preset_id,
    "preset_config": req.preset_config,
    # Live Trading fields (StrategyExecutor reads these)
    "strategy_id": strategy_id,
    "strategy_name": strategy_name,
    "timeframe": base_timeframe,
    "extra_timeframes": extra_timeframes,
    "batch_ids": req.batch_ids,
    "winning_batch_ids": req.winning_batch_ids,
    # Cross-strategy flag: Phase 2 assets may come from different batches
    # with different parameter configs — StrategyExecutor uses batch_id
    # per asset for parameter lookup (same as cross_strategy portfolios)
    "is_cross_strat": True,
  }

  # Promote execution-filter + risk-profile fields to the TOP LEVEL of
  # source_info so StrategyExecutor (session_filter) and EngineCoordinator
  # (risk_profile) can read them without reaching into preset_config.
  # Reads at: strategy_executor.py:262-264, engine_coordinator.py:1668/1825.
  _pc = req.preset_config or {}

  # Config-fidelity fix (2026-06-10): promote EVERY grading key the
  # nightly hybrid refresh consumes to the top level. run_step2 +
  # hybrid_portfolio_refresh read these from source_strategies' top
  # level (cfg = dict(src_cfg)); leaving them only inside preset_config
  # made the live walk silently grade with run_step2's built-in defaults
  # (n_trades=10, n_assets=5, objective=net_profit, ...) instead of the
  # optimization's actual config. /save-track already stores them
  # top-level — this brings /save-phase2-track to parity.
  from backend.analysis.step2_service import _CONFIG_KEYS as _STEP2_CONFIG_KEYS
  for _ck in _STEP2_CONFIG_KEYS:
    if _ck in _pc and _ck not in source_info:
      source_info[_ck] = _pc[_ck]
  _sf_enabled = bool(_pc.get("session_filter_enabled", False))
  _sf_excl_h = _pc.get("session_filter_exclude_hours") or []
  _sf_excl_d = _pc.get("session_filter_exclude_days") or []
  if _sf_enabled or _sf_excl_h or _sf_excl_d:
    source_info["session_filter_enabled"] = _sf_enabled
    source_info["session_filter_exclude_hours"] = list(_sf_excl_h)
    source_info["session_filter_exclude_days"] = list(_sf_excl_d)

  _rp_mode = _pc.get("risk_profile_mode", "fixed")
  _rp_value = float(_pc.get("risk_profile_value", 0.0) or 0.0)
  if _rp_mode != "fixed" or _rp_value > 0:
    source_info["risk_profile"] = {
      "override_enabled": True,
      "mode": _rp_mode,
      "initial_risk_usd": _rp_value,
      "initial_capital": float(_pc.get("risk_profile_initial_capital", 10_000.0) or 10_000.0),
      "multiplier": float(_pc.get("risk_profile_multiplier", 2.0) or 2.0),
      "max_streak": int(_pc.get("risk_profile_max_streak", 3) or 3),
      "martingale_compound": _pc.get("risk_profile_martingale_compound", "off"),
      "kelly_n": float(_pc.get("risk_profile_kelly_n", 2.0) or 2.0),
      "kelly_warmup": int(_pc.get("risk_profile_kelly_warmup", 20) or 20),
      "sizing_master": "portfolio",
    }

  # Persist event-based rotation history so /wfo-timeline can reconstruct
  # the real per-period OOS (basket active between each change).
  if req.selection_changes:
    source_info["phase2_selection_changes"] = req.selection_changes
  if req.initial_basket:
    source_info["phase2_initial_basket"] = list(req.initial_basket)

  # Phase 2 truth: persist selected_records + aggregate_monthly_matrix so
  # has_wfo_replay_metadata() fires and every portfolio view reads the same
  # data as the optimizer Phase 2 page. Without this, /full-metrics-wfo,
  # /month-trades, /monthly-filtered, /assets, /dd-status all fall back to
  # legacy batch-query stitching which produces different numbers.
  if req.selected_records:
    step2_blob: dict = {"selected_records": req.selected_records}
    if req.aggregate_monthly_matrix:
      step2_blob["aggregate_monthly_matrix"] = req.aggregate_monthly_matrix
    if req.aggregate_equity_curve:
      step2_blob["aggregate_equity_curve"] = req.aggregate_equity_curve
    if req.aggregate_breakdown:
      step2_blob["aggregate_breakdown"] = req.aggregate_breakdown
    if req.cumulative_metrics:
      step2_blob["cumulative_metrics"] = req.cumulative_metrics
    if req.wfo_per_window:
      for w in reversed(req.wfo_per_window):
        if not isinstance(w, dict):
          continue
        latest_asset_scores = w.get("asset_scores_at_end")
        if latest_asset_scores:
          step2_blob["latest_asset_scores"] = latest_asset_scores
          break
    source_info["step2_result"] = step2_blob

  # T-002c: persist window metadata + per-window baskets so refresh and
  # downstream views (wfo-timeline, monthly grid) can reconstruct the
  # per-window basket evolution without re-running Phase 2. Mirrors the
  # /save-track path at L2287-2289.
  if req.windows_meta:
    source_info["wfo_windows_meta"] = req.windows_meta
  if req.wfo_per_window:
    source_info["wfo_per_window"] = req.wfo_per_window
  # T-002e': if the caller ships a final_state_snapshot alongside the save,
  # persist it as continuation_state so the first refresh resumes from
  # there. Typically absent on first save — refresh writes it on its own.
  continuation_state = req.continuation_state
  if continuation_state is None and req.wfo_per_window:
    for w in reversed(req.wfo_per_window):
      if not isinstance(w, dict):
        continue
      continuation_state = w.get("final_state_snapshot")
      if continuation_state:
        break
  if continuation_state:
    source_info["continuation_state"] = continuation_state

  portfolio = Portfolio(
    name=req.name,
    metrics_snapshot=json.dumps(_sanitize(req.metrics_snapshot)),
    source_strategies=json.dumps(source_info),
    optimization_end_date=opt_end,
    portfolio_source="wfas_phase2",
    is_cross_strat=True,  # enables per-asset batch parameter lookup
    mode=("hybrid" if (req.preset_config or {}).get("hybrid_mode", True) else "non_hybrid"),
    description=f"Phase 2 live signal — preset: {req.preset_id or 'custom'}. "
                f"Strategy: {strategy_name or 'unknown'} ({base_timeframe or '?'}m). "
                f"Assets rotate via event-based scoring (rebalance every "
                f"{req.preset_config.get('hybrid_rebalance_every', '?')} trades). "
                f"Re-run preset to refresh asset picks.",
  )
  db.add(portfolio)
  db.flush()

  for asset in req.assets:
    bid = asset.get("batch_id")
    tf = asset.get("timeframe") or base_timeframe
    gn = asset.get("group_name")
    cfg_name = _batch_config_map.get(bid) if bid else None
    # Fallback: derive from batch_info if frontend didn't provide
    if bid and (not tf or not gn) and bid in _batch_info_map:
      bi = _batch_info_map[bid]
      tf = tf or bi.get("timeframe") or base_timeframe
      gn = gn or bi.get("group_name")
    db.add(PortfolioAsset(
      portfolio_id=portfolio.id,
      symbol=asset.get("symbol", ""),
      trade_side=asset.get("side", "both"),
      strategy_name=strategy_name,
      raw_name=asset.get("asset_key", asset.get("symbol", "")),
      batch_id=bid,
      config_name=cfg_name,
      timeframe=tf or base_timeframe,
      group_name=gn,
      is_active=True,
    ))
  db.commit()
  db.refresh(portfolio)

  log.info(
    "Phase 2 portfolio saved: id=%d name=%s preset=%s strategy=%s(%s) "
    "assets=%d winning_batches=%s",
    portfolio.id, req.name, req.preset_id, strategy_name, base_timeframe,
    len(req.assets), req.winning_batch_ids,
  )

  # Stage 8 / Y-B: kick an immediate refresh on hybrid saves (mirror of
  # the same hook in /save-track). Sets the cursor so nightly cron can
  # carry the basket forward. Best-effort.
  if portfolio.mode == "hybrid":
    try:
      from backend.services.hybrid_portfolio_refresh import refresh_portfolio as _yb_refresh
      log.info("WFAS save-phase2: kicking immediate Y-B refresh for hybrid portfolio %d", portfolio.id)
      _r = _yb_refresh(portfolio.id, db=db, allow_inactive=False)
      log.info(
        "WFAS save-phase2: immediate refresh result for %d — ok=%s reason=%s added=%s removed=%s basket=%s",
        portfolio.id, _r.get("ok"), _r.get("reason"),
        _r.get("added"), _r.get("removed"), _r.get("basket_size"),
      )
    except Exception as _e:
      log.warning(
        "WFAS save-phase2: immediate refresh failed for portfolio %d: %s "
        "(save still committed; nightly cron will pick it up)",
        portfolio.id, _e,
      )

  return {
    "id": portfolio.id, "name": portfolio.name,
    "asset_count": len(req.assets),
    "strategy_name": strategy_name,
    "timeframe": base_timeframe,
    "created_at": portfolio.created_at.isoformat() if portfolio.created_at else None,
  }


class WFORefreshPhase2Request(BaseModel):
  """Refresh a Phase 2 portfolio's assets after re-running the preset."""
  portfolio_id: int
  # New active assets from the latest Phase 2 run
  assets: list[dict]  # [{symbol, side, batch_id, timeframe, group_name, asset_key}]
  # Updated metrics
  metrics_snapshot: dict
  # Updated winning batches
  winning_batch_ids: list[int] = []
  # New OOS boundary
  optimization_end_date: str | None = None
  # Phase 2 truth from the refreshed run — same contract as WFOSavePhase2Request.
  # When present, replaces source_strategies.step2_result so all portfolio
  # views reflect the re-run's new selected_records and monthly grid.
  selected_records: list[dict] | None = None
  aggregate_monthly_matrix: dict | None = None
  aggregate_equity_curve: list[dict] | None = None
  aggregate_breakdown: list[dict] | None = None
  cumulative_metrics: dict | None = None
  selection_changes: list[dict] | None = None
  initial_basket: list[str] | None = None


@router.post("/refresh-phase2-track")
def refresh_wfas_phase2_track(req: WFORefreshPhase2Request, db: Session = Depends(get_db)):
  """Refresh a Phase 2 portfolio's assets after re-running the WFAS preset.

  Deactivates all current assets and adds the new Phase 2 picks.
  Logs the rotation in swap_history for audit trail.
  Used when Phase 2 asset rotation produces different picks after a re-run.
  """
  from datetime import datetime, timezone

  portfolio = db.get(Portfolio, req.portfolio_id)
  if not portfolio:
    raise HTTPException(404, "Portfolio not found")
  if portfolio.portfolio_source != "wfas_phase2":
    raise HTTPException(400, "Only Phase 2 portfolios can be refreshed this way")

  # ── Snapshot current assets for swap_history ──
  current_assets = db.query(PortfolioAsset).filter(
    PortfolioAsset.portfolio_id == portfolio.id,
    PortfolioAsset.is_active == True,
  ).all()
  old_asset_keys = {a.raw_name or f"{a.symbol}_{a.trade_side}" for a in current_assets}

  # ── Deactivate all current assets ──
  for a in current_assets:
    a.is_active = False

  # ── Resolve strategy metadata from source_strategies ──
  source_data = json.loads(portfolio.source_strategies or "{}")
  strategy_name = source_data.get("strategy_name", "")
  base_timeframe = source_data.get("timeframe", "")

  # Build batch info for enrichment
  all_batch_ids = list(set(
    source_data.get("batch_ids", []) + req.winning_batch_ids
  ))
  _batch_info_map = _build_batch_info(all_batch_ids, db) if all_batch_ids else {}

  # Build batch→config_name lookup
  _batch_config_map: dict[int, str] = {}
  strategy_id = source_data.get("strategy_id")
  if strategy_id:
    for bid in all_batch_ids:
      batch = db.get(BatchBacktestRun, bid)
      if batch:
        bp = json.loads(batch.parameters or "{}")
        cfg_name = bp.get("config_name", "")
        if cfg_name:
          _batch_config_map[bid] = cfg_name

  # ── Add new assets ──
  new_asset_keys = set()
  for asset in req.assets:
    bid = asset.get("batch_id")
    tf = asset.get("timeframe") or base_timeframe
    gn = asset.get("group_name")
    cfg_name = _batch_config_map.get(bid) if bid else None
    if bid and (not tf or not gn) and bid in _batch_info_map:
      bi = _batch_info_map[bid]
      tf = tf or bi.get("timeframe") or base_timeframe
      gn = gn or bi.get("group_name")
    asset_key = asset.get("asset_key", f"{asset.get('symbol', '')}_{asset.get('side', 'both')}")
    new_asset_keys.add(asset_key)
    db.add(PortfolioAsset(
      portfolio_id=portfolio.id,
      symbol=asset.get("symbol", ""),
      trade_side=asset.get("side", "both"),
      strategy_name=strategy_name,
      raw_name=asset_key,
      batch_id=bid,
      config_name=cfg_name,
      timeframe=tf or base_timeframe,
      group_name=gn,
      is_active=True,
    ))

  # ── Log rotation in swap_history ──
  added = new_asset_keys - old_asset_keys
  removed = old_asset_keys - new_asset_keys
  unchanged = new_asset_keys & old_asset_keys
  swap_history = json.loads(portfolio.swap_history or "[]")
  swap_history.append({
    "date": datetime.now(timezone.utc).isoformat(),
    "type": "phase2_refresh",
    "added": sorted(added),
    "removed": sorted(removed),
    "unchanged": sorted(unchanged),
    "new_count": len(req.assets),
    "old_count": len(current_assets),
  })
  portfolio.swap_history = json.dumps(swap_history)

  # ── Update metrics + winning batches ──
  portfolio.metrics_snapshot = json.dumps(_sanitize(req.metrics_snapshot))
  if req.optimization_end_date:
    portfolio.optimization_end_date = datetime.fromisoformat(req.optimization_end_date)
  source_data["winning_batch_ids"] = req.winning_batch_ids

  # Refresh Phase 2 truth — overwrite step2_result so all portfolio views
  # reflect the re-run's new selected_records and monthly grid.
  if req.selected_records:
    step2_blob: dict = {"selected_records": req.selected_records}
    if req.aggregate_monthly_matrix:
      step2_blob["aggregate_monthly_matrix"] = req.aggregate_monthly_matrix
    if req.aggregate_equity_curve:
      step2_blob["aggregate_equity_curve"] = req.aggregate_equity_curve
    if req.aggregate_breakdown:
      step2_blob["aggregate_breakdown"] = req.aggregate_breakdown
    if req.cumulative_metrics:
      step2_blob["cumulative_metrics"] = req.cumulative_metrics
    source_data["step2_result"] = step2_blob
  if req.selection_changes is not None:
    source_data["phase2_selection_changes"] = req.selection_changes
  if req.initial_basket is not None:
    source_data["phase2_initial_basket"] = list(req.initial_basket)

  portfolio.source_strategies = json.dumps(source_data)
  portfolio.last_refreshed_at = datetime.now(timezone.utc)

  db.commit()
  db.refresh(portfolio)

  log.info(
    "Phase 2 portfolio refreshed: id=%d added=%s removed=%s unchanged=%d",
    portfolio.id, sorted(added), sorted(removed), len(unchanged),
  )

  return {
    "id": portfolio.id,
    "asset_count": len(req.assets),
    "added": sorted(added),
    "removed": sorted(removed),
    "unchanged_count": len(unchanged),
  }


@router.post("/runs/{run_id}/rerun")
def rerun_wfo_window(request: Request, run_id: int, db: Session = Depends(get_db)):
  """Re-run: append ONE new window to an existing WFO run.

  Uses the same config/environment as the original run, but with
  extended data. The new IS starts where the last OOS ended.
  """
  rate_limit(request, max_requests=10, window_seconds=60, key="wfo")
  from datetime import date as _date

  from dateutil.relativedelta import relativedelta

  r = db.query(WFORun).filter(WFORun.id == run_id).first()
  if not r:
    raise HTTPException(404, "WFO run not found")

  # Reconstruct config from saved optimizer_settings
  config = json.loads(r.optimizer_settings) if r.optimizer_settings else {}
  batch_ids = json.loads(r.batch_ids) if r.batch_ids else []
  time_mode = getattr(r, "time_mode", "rolling") or "rolling"

  if not batch_ids:
    raise HTTPException(400, "No batch_ids in saved run")

  # Find last window with OOS
  last_w = None
  for w in sorted(r.windows, key=lambda x: x.window_num, reverse=True):
    if w.oos_end:
      last_w = w
      break
  if not last_w:
    raise HTTPException(400, "No OOS window to extend from")

  # Generate new window dates
  new_is_start = _date.fromisoformat(last_w.is_start) + relativedelta(months=r.oos_months)
  new_is_end = new_is_start + relativedelta(months=r.is_months) - relativedelta(days=1)
  new_oos_start = new_is_end + relativedelta(days=1)
  new_oos_end = new_oos_start + relativedelta(months=r.oos_months) - relativedelta(days=1)
  new_win_num = max(w.window_num for w in r.windows) + 1

  new_window = {
    "window_num": new_win_num,
    "is_start": new_is_start.isoformat(),
    "is_end": new_is_end.isoformat(),
    "oos_start": new_oos_start.isoformat(),
    "oos_end": new_oos_end.isoformat(),
  }

  # For overlapping mode, add H1/H2 dates
  if time_mode == "overlapping":
    oos_total = config.get("oos_total_months", r.oos_months)
    half = max(oos_total // 2, 1)
    h1_start = new_oos_start
    h1_end = h1_start + relativedelta(months=half) - relativedelta(days=1)
    h2_start = h1_end + relativedelta(days=1)
    h2_end = h2_start + relativedelta(months=half) - relativedelta(days=1)
    new_window.update({
      "oos_end": h2_end.isoformat(),
      "oos_h1_start": h1_start.isoformat(),
      "oos_h1_end": h1_end.isoformat(),
      "oos_h2_start": h2_start.isoformat(),
      "oos_h2_end": h2_end.isoformat(),
    })

  try:
    # Load trades
    # Use saved scaling config, defaulting to "group" for backward compat
    _saved_scaling = config.get("pnl_scaling_mode", "group")
    _saved_custom = config.get("pnl_scaling_custom", 1.0)
    _saved_per_batch = config.get("pnl_scaling_per_batch")
    all_trades, run_batch_map, _ = _load_multi_batch_trades(
      batch_ids, db,
      pnl_scaling_mode=_saved_scaling,
      pnl_scaling_custom=_saved_custom,
      pnl_scaling_per_batch=_saved_per_batch,
    )
    if not all_trades:
      raise HTTPException(400, "No trades found in saved batch_ids")

    # Boundary clipping: resolve timeframe for rerun
    _clip_db = None
    if config.get("clip_is", True):
      if not config.get("_timeframe"):
        batch_obj = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_ids[0]).first()
        config["_timeframe"] = batch_obj.timeframe if batch_obj else "D"
      _clip_db = db

    # Run optimizer on the single new window
    result = run_wfo_asset_selection(
      all_trades, [new_window], config, r.n_candidates,
      config.get("corr_method", "standard"), config.get("div_algo", "greedy"),
      db=_clip_db,
    )

    # Append new window to DB
    w_data = result["windows"][0] if result["windows"] else {}
    db.add(WFOWindow(
      wfo_run_id=r.id,
      window_num=new_win_num,
      is_start=new_window["is_start"],
      is_end=new_window["is_end"],
      oos_start=new_window.get("oos_start"),
      oos_end=new_window.get("oos_end"),
      candidates=json.dumps(_sanitize(w_data.get("candidates", []))),
      oos_results=json.dumps(_sanitize([c.get("oos_metrics") for c in w_data.get("candidates", [])])),
      is_trade_count=w_data.get("is_trade_count", 0),
      is_symbol_count=w_data.get("is_symbol_count", 0),
      is_pool_size=w_data.get("is_pool_size", 0),
      oos_h1_start=new_window.get("oos_h1_start"),
      oos_h1_end=new_window.get("oos_h1_end"),
      oos_h2_start=new_window.get("oos_h2_start"),
      oos_h2_end=new_window.get("oos_h2_end"),
    ))

    # Recompute cumulative metrics from all windows
    from backend.analysis.wfo_aggregation import aggregate_summary, compile_tracks, cumulative_metrics
    _opt = json.loads(r.optimizer_settings) if r.optimizer_settings else {}
    all_window_data = [_window_to_dict(w, opt_settings=_opt, db=db) for w in r.windows] + [w_data]
    # Each window dict needs a candidates list with is_metrics/oos_metrics
    r.cumulative_is = json.dumps(_sanitize(cumulative_metrics(all_window_data, "is")))
    r.cumulative_oos = json.dumps(_sanitize(cumulative_metrics(all_window_data, "oos")))
    r.tracks = json.dumps(_sanitize(compile_tracks(all_window_data, r.n_candidates, all_trades, time_mode)))
    if time_mode == "overlapping":
      r.cumulative_oos_h1 = json.dumps(_sanitize(cumulative_metrics(all_window_data, "oos_h1")))
      r.cumulative_oos_h2 = json.dumps(_sanitize(cumulative_metrics(all_window_data, "oos_h2")))

    db.commit()

    return {
      "success": True,
      "window_num": new_win_num,
      "is_start": new_window["is_start"],
      "is_end": new_window["is_end"],
      "oos_start": new_window.get("oos_start"),
      "oos_end": new_window.get("oos_end"),
      "candidates": w_data.get("candidates", []),
      "is_trade_count": w_data.get("is_trade_count", 0),
      "is_symbol_count": w_data.get("is_symbol_count", 0),
      "is_pool_size": w_data.get("is_pool_size", 0),
    }

  except HTTPException:
    raise
  except Exception as exc:
    from backend.api.routes.optimization_errors import wrap_optimization_error
    raise wrap_optimization_error("WFO Window Rerun", exc) from exc


@router.post("/runs/{run_id}/replay-exact")
def replay_wfo_exact(request: Request, run_id: int, db: Session = Depends(get_db)):
  """Replay Exact: re-run a saved WFAS run with the **exact** same config.

  Unlike `/rerun` (which appends one new window) or the "Reapply" button
  (which loads settings into the form — lossy, requires manual batch
  re-selection), this endpoint bypasses the form entirely:

    1. Loads the saved wfo_runs row (batch_ids, is/oos months, time_mode,
       event_mode, portfolio_ids, last_oos_end, optimizer_settings).
    2. Reconstructs a WFOAssetSelectionRequest from those stored fields.
    3. Calls the same run_wfo handler the form uses.

  Guarantees:
    - Saved batch_ids are used verbatim. Adding new batches to the DB
      afterwards cannot change the result.
    - Every field from optimizer_settings that still exists on the Pydantic
      model is restored; unknown/stale keys are silently dropped.
    - Internal runtime keys (_run_batch_map, _timeframe, step2_result,
      progress) are stripped before replay — they are stateful leftovers,
      not config.
    - Result is saved as a NEW wfo_runs row with parent_run_id = original.
  """
  rate_limit(request, max_requests=10, window_seconds=60, key="wfo")

  r = db.query(WFORun).filter(WFORun.id == run_id).first()
  if not r:
    raise HTTPException(404, "WFO run not found")

  # ── Reconstruct the original request payload ──
  try:
    saved_opt = json.loads(r.optimizer_settings) if r.optimizer_settings else {}
  except (ValueError, TypeError):
    saved_opt = {}
  try:
    saved_batch_ids = json.loads(r.batch_ids) if r.batch_ids else []
  except (ValueError, TypeError):
    saved_batch_ids = []
  try:
    saved_portfolio_ids = json.loads(r.portfolio_ids) if r.portfolio_ids else []
  except (ValueError, TypeError):
    saved_portfolio_ids = []

  if not saved_batch_ids and not saved_portfolio_ids:
    raise HTTPException(400, "Saved run has no batch_ids or portfolio_ids")

  # Strip internal/runtime keys — these are not config and would poison replay.
  _INTERNAL_KEYS = {
    "_run_batch_map", "_timeframe", "step2_result", "progress",
    "_internal", "_cache",
  }
  clean_opt = {k: v for k, v in saved_opt.items() if k not in _INTERNAL_KEYS}

  # Filter to fields that the Pydantic model actually accepts. Drops any
  # stale keys from old runs whose field was renamed/removed.
  _allowed_fields = set(WFOAssetSelectionRequest.model_fields.keys())
  filtered_opt = {k: v for k, v in clean_opt.items() if k in _allowed_fields}
  _dropped = sorted(set(clean_opt.keys()) - set(filtered_opt.keys()))
  if _dropped:
    log.info("replay_wfo_exact: dropped %d stale optimizer_settings keys: %s",
             len(_dropped), _dropped)

  # Top-level columns take precedence over optimizer_settings for the
  # fields that live in dedicated columns.
  payload: dict = dict(filtered_opt)
  payload["batch_ids"] = list(saved_batch_ids)
  payload["portfolio_ids"] = list(saved_portfolio_ids)
  payload["is_months"] = int(r.is_months)
  payload["oos_months"] = int(r.oos_months)
  payload["last_oos_end"] = str(r.last_oos_end or "")
  payload["time_mode"] = getattr(r, "time_mode", "rolling") or "rolling"
  payload["event_mode"] = bool(getattr(r, "event_mode", False))

  # Build the Pydantic request. Missing fields use model defaults (same as
  # the original run — those defaults were in effect when it was saved).
  try:
    req = WFOAssetSelectionRequest(**payload)
  except Exception as exc:
    raise HTTPException(
      400,
      f"Failed to reconstruct request from saved run: {exc}",
    ) from exc

  log.info(
    "replay_wfo_exact: parent=%d batches=%d is=%d oos=%d tm=%s em=%s preset_id=%s",
    run_id, len(payload["batch_ids"]), payload["is_months"], payload["oos_months"],
    payload["time_mode"], payload["event_mode"], filtered_opt.get("preset_id"),
  )

  # Delegate to the main WFAS handler — same code path as the form Run button.
  # This produces a NEW saved wfo_runs row as a side effect.
  try:
    result = run_wfo(request, req, db)
  except HTTPException:
    raise
  except Exception as exc:
    from backend.api.routes.optimization_errors import wrap_optimization_error
    raise wrap_optimization_error("WFO Replay Exact", exc) from exc

  # Tag the new run with parent_run_id so lineage is visible in saved runs.
  try:
    new_run_id = (result or {}).get("wfo_run_id") if isinstance(result, dict) else None
    if new_run_id:
      new_r = db.query(WFORun).filter(WFORun.id == new_run_id).first()
      if new_r and getattr(new_r, "parent_run_id", None) is None:
        new_r.parent_run_id = run_id
        db.commit()
  except Exception as _tag_err:
    log.warning("replay_wfo_exact: failed to tag parent_run_id: %s", _tag_err)

  return result


# ── Async WFO Progress Endpoints ──────────────────────────────


@router.get("/progress/{run_id}")
def get_wfo_progress(run_id: str):
  """Poll current progress of a running WFO computation."""
  with _wfo_lock:
    prog = _wfo_progress.get(run_id)
    done = run_id in _wfo_results
  if prog is None and not done:
    raise HTTPException(404, "Unknown WFO run ID")
  return {
    "run_id": run_id,
    "step": prog["step"] if prog else 0,
    "total": prog["total"] if prog else 0,
    "label": prog["label"] if prog else "",
    "done": done,
  }


@router.get("/result/{run_id}")
def get_wfo_result(run_id: str):
  """Fetch the completed result of an async WFO run. Cleans up after fetch."""
  with _wfo_lock:
    result = _wfo_results.pop(run_id, None)
    _wfo_progress.pop(run_id, None)
  if result is None:
    raise HTTPException(404, "Result not ready or unknown run ID")
  if "error" in result:
    raise HTTPException(500, result["error"])
  return result


# ── WFAS Month-Trades Drilldown ─────────────────────────────────


@router.get("/runs/{run_id}/month-trades")
def wfo_month_trades(
  run_id: int,
  year: int,
  month: int,
  window_num: int | None = None,
  db: Session = Depends(get_db),
):
  """Return individual trades for a WFAS month cell.

  If window_num is provided, returns trades only for that window's OOS period.
  If omitted, returns all OOS trades across all windows for the given month.

  Trades are matched by: batch_id ∈ selected_batches AND entry_time within
  the window's OOS date range AND exit_time in the target year/month.
  """
  import pandas as pd

  r = db.query(WFORun).filter(WFORun.id == run_id).first()
  if not r:
    raise HTTPException(404, "WFO run not found")

  # Extract exec filter from optimizer_settings (to match OOS metrics)
  opt_settings = json.loads(r.optimizer_settings) if r.optimizer_settings else {}
  exec_filter_enabled = opt_settings.get("session_filter_enabled", False)
  exec_exclude_hours: set[int] = set(opt_settings.get("session_filter_exclude_hours", []))
  exec_exclude_days: set[int] = set(opt_settings.get("session_filter_exclude_days", []))
  has_exec_filter = exec_filter_enabled and bool(exec_exclude_hours or exec_exclude_days)

  windows = r.windows
  if window_num is not None:
    windows = [w for w in windows if w.window_num == window_num]
    if not windows:
      raise HTTPException(404, "Window not found")

  # Collect (batch_ids, oos_start, oos_end, selected_assets) per window
  window_specs: list[dict] = []
  for w in windows:
    if not w.oos_start or not w.oos_end:
      continue
    cands = json.loads(w.candidates) if w.candidates else []
    if not cands:
      continue
    c = cands[0]
    sel_batches = c.get("selected_batches", [])
    if not sel_batches:
      # Fallback: use run-level batch_ids
      sel_batches = json.loads(r.batch_ids) if r.batch_ids else []
    # Get selected asset symbols+sides for filtering
    asset_details = c.get("asset_details", [])
    window_specs.append({
      "oos_start": w.oos_start,
      "oos_end": w.oos_end,
      "batch_ids": sel_batches,
      "asset_details": asset_details,
    })

  if not window_specs:
    return {"trades": [], "year": year, "month": month, "window_num": window_num}

  # Build set of all batch IDs we need
  all_batch_ids: set[int] = set()
  for ws in window_specs:
    all_batch_ids.update(ws["batch_ids"])

  # Get run_ids for these batches
  run_rows = (
    db.query(BacktestRun.id, BacktestRun.batch_id)
    .filter(
      BacktestRun.batch_id.in_(list(all_batch_ids)),
      BacktestRun.status == "completed",
    )
    .all()
  )
  run_batch_map: dict[int, int] = {rid: bid for rid, bid in run_rows}
  if not run_batch_map:
    return {"trades": [], "year": year, "month": month, "window_num": window_num}

  # Build PnL multiplier + group name lookup per batch_id (single join)
  from backend.db.models import BatchBacktestRun, BatchGroup
  from collections import defaultdict

  batch_rows = (
    db.query(
      BatchBacktestRun.id,
      BatchBacktestRun.name,
      BatchBacktestRun.config_group,
      BatchBacktestRun.strategy_id,
      BatchBacktestRun.timeframe,
      BatchGroup.name,
      BatchGroup.pnl_multiplier,
    )
    .outerjoin(BatchGroup, BatchBacktestRun.group_id == BatchGroup.id)
    .filter(BatchBacktestRun.id.in_(list(all_batch_ids)))
    .all()
  )

  batch_scale: dict[int, float] = {}
  batch_group_name: dict[int, str] = {}
  batch_names: dict[int, str] = {}
  batch_strategy_id: dict[int, int] = {}
  batch_timeframe: dict[int, str] = {}
  for bid, bname, cfg_group, strat_id, tf, grp_name, grp_mult in batch_rows:
    batch_scale[bid] = grp_mult if grp_mult else 1.0
    batch_group_name[bid] = grp_name or cfg_group or ""
    batch_names[bid] = bname or f"Batch {bid}"
    if strat_id is not None:
      batch_strategy_id[bid] = int(strat_id)
    if tf:
      batch_timeframe[bid] = str(tf)

  # Query trades for the target month across all relevant run_ids
  all_run_ids = list(run_batch_map.keys())

  # Build month boundaries — use entry_time to match the monthly matrix
  # (matrix groups daily PnL by entry date, not exit date)
  from calendar import monthrange
  month_start = f"{year}-{month:02d}-01"
  last_day = monthrange(year, month)[1]
  month_end = f"{year}-{month:02d}-{last_day:02d} 23:59:59"

  # Query in chunks to avoid huge IN clauses
  trades_raw = []
  chunk_size = 500
  for i in range(0, len(all_run_ids), chunk_size):
    chunk = all_run_ids[i:i + chunk_size]
    rows = (
      db.query(BacktestTrade)
      .filter(
        BacktestTrade.run_id.in_(chunk),
        BacktestTrade.entry_time >= month_start,
        BacktestTrade.entry_time <= month_end,
      )
      .order_by(BacktestTrade.entry_time)
      .all()
    )
    trades_raw.extend(rows)

  # Filter trades: must be within OOS range of at least one matching window
  # and belong to a selected batch for that window.
  # Group into positions using position_id (or fallback key).
  positions_map: dict[str, dict] = {}  # pos_key → position dict
  total_trades = 0

  for t in trades_raw:
    batch_id = run_batch_map.get(t.run_id)
    if batch_id is None:
      continue

    # Apply exec filter — CS-014: share predicate with canonical filter.
    # NOTE: the `and t.entry_time` guard preserves pre-CS-014 semantics —
    # a trade with no entry_time was NOT dropped by the filter here (it
    # was handled downstream by the window-range check). Keeping that.
    if (
      has_exec_filter
      and t.entry_time
      and is_excluded_by_exec_filter(t.entry_time, exec_exclude_hours, exec_exclude_days)
    ):
      continue

    # Check if this trade falls within any window's OOS range + batch
    matched = False
    for ws in window_specs:
      if batch_id not in ws["batch_ids"]:
        continue
      entry_str = t.entry_time.isoformat() if t.entry_time else ""
      if entry_str < ws["oos_start"] or entry_str > ws["oos_end"]:
        continue
      if ws["asset_details"]:
        asset_key = f"{t.symbol}_{t.side}".lower() if t.side else t.symbol.lower()
        asset_symbols = {
          (f"{ad['symbol']}_{ad['side']}" if ad.get("side") and ad["side"] != "both"
          else ad["symbol"]).lower()
          for ad in ws["asset_details"]
          if ad.get("batch_id") == batch_id or not ad.get("batch_id")
        }
        if asset_key not in asset_symbols:
          continue
      matched = True
      break

    if not matched:
      continue

    total_trades += 1

    # Position key: use position_id if available, else (batch, symbol, side, entry_time)
    if t.position_id:
      pos_key = f"{t.run_id}_{t.position_id}"
    else:
      entry_iso = t.entry_time.isoformat() if t.entry_time else ""
      pos_key = f"{batch_id}_{t.symbol}_{t.side}_{entry_iso}"

    scale = batch_scale.get(batch_id, 1.0)
    raw_pnl = float(t.net_profit) if t.net_profit else 0
    scaled_pnl = raw_pnl * scale

    exit_dict = {
      "exit_time": t.exit_time.isoformat() if t.exit_time else None,
      "exit_price": round(float(t.exit_price), 6) if t.exit_price else None,
      "net_profit": round(scaled_pnl, 2),
      "quantity": round(float(t.quantity), 6) if t.quantity else None,
      "reason": t.exit_reason or "",
      "commission": round(float(t.commission), 4) if t.commission else None,
      "sl_price": round(float(t.sl_price), 6) if t.sl_price else None,
      "tp_price": round(float(t.tp_price), 6) if t.tp_price else None,
    }

    if pos_key not in positions_map:
      asset = f"{t.symbol}_{t.side}" if t.side else t.symbol
      display = asset.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")
      positions_map[pos_key] = {
        "asset": asset,
        "asset_display": display,
        "side": t.side or "",
        "entry_time": t.entry_time.isoformat() if t.entry_time else None,
        "entry_price": round(float(t.entry_price), 6) if t.entry_price else None,
        "batch_id": batch_id,
        "batch_name": batch_names.get(batch_id, f"Batch {batch_id}"),
        "batch_group": batch_group_name.get(batch_id, ""),
        "pnl_multiplier": scale,
        "strategy_id": batch_strategy_id.get(batch_id),
        "timeframe": batch_timeframe.get(batch_id),
        "run_id": int(t.run_id) if t.run_id else None,
        "net_profit": 0.0,
        "exits": [],
      }

    positions_map[pos_key]["exits"].append(exit_dict)
    positions_map[pos_key]["net_profit"] = round(
      positions_map[pos_key]["net_profit"] + scaled_pnl, 2
    )

  # Number positions and sort by entry_time
  positions_out = sorted(positions_map.values(), key=lambda p: p["entry_time"] or "")
  for i, p in enumerate(positions_out, 1):
    p["num"] = i

  return {
    "positions": positions_out,
    "position_count": len(positions_out),
    "trade_count": total_trades,
    "year": year,
    "month": month,
    "window_num": window_num,
  }
