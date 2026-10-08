"""Batch Selection — 3-step hierarchical WFO engine.

Step 1: IS Screening — rank batches by batch_rank_metric
Step 2: Calibration Gate — confirm against PREVIOUS window's OOS
Step 3: Asset Picking — pick top assets per batch by asset_rank_metric

Uses entry-date slicing: daily PnL built once using ENTRY date,
then sliced by date range. This ensures trades are attributed to the
period where the decision was made (entry), not when they resolved (exit).
Separate code path from the correlation-based optimizer — does NOT use
filter_trades_strict().
"""
from __future__ import annotations

import hashlib
import logging
import pickle
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from backend.utils.clock import today as _clock_today
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from backend.analysis.batch_selection_scoring import METRIC_NAMES, score_metric
from backend.db.models import BacktestTrade

log = logging.getLogger(__name__)


# ── Lightweight Trade Loader ─────────────────────────────────
# Loads only the 6 columns research needs as namedtuples.
# PnL is auto-scaled by bybit_limit / 100 (backtest base).
# ~3-4x less memory than full ORM loading.

from collections import namedtuple

LightTrade = namedtuple("LightTrade", [
  "run_id", "symbol", "side", "net_profit", "entry_time", "exit_time", "timeframe",
])


def load_trades_lightweight(
  db,
  batch_ids: list[int] | None = None,
  *,
  group_names: list[str] | None = None,
  scale_pnl: bool = True,
) -> tuple[list[LightTrade], dict[int, int], dict[int, dict]]:
  """Load trades as lightweight namedtuples — 6 columns only.

  Args:
    db:            SQLAlchemy session
    batch_ids:     Specific batch IDs to load (None = all active)
    group_names:   Load only batches in these groups, e.g. ["A", "B"].
                   Overrides batch_ids if provided.
    scale_pnl:     Multiply net_profit by the group's pnl_multiplier
                   (e.g. 2.5 for Group A M30). Set False if backtests
                   already ran at production sizing.

  Returns:
    trades:       list[LightTrade] with .run_id, .symbol, .side,
                  .net_profit (scaled if scale_pnl), .entry_time,
                  .exit_time, .timeframe
    run_batch_map: dict[run_id → batch_id]
    batch_info:    dict[batch_id → {timeframe, bybit_limit,
                  max_margin_per_trade, pnl_multiplier, name,
                  group, slippage_pct, fee_pct}]

  PnL scaling:
    When scale_pnl=True (default), net_profit is multiplied by the
    group's pnl_multiplier. This is a direct ratio — e.g. 2.5 means
    "production trades are 2.5x bigger than backtest trades."
    Set to 1.0 (or scale_pnl=False) if backtests already used
    production sizing with real fees/slippage baked in.
    WR / sortino / sharpe / PF are unaffected (scale-invariant).

  Usage:
    from backend.analysis.batch_selection import load_trades_lightweight

    # Load all active batches
    trades, rbm, batch_info = load_trades_lightweight(db)

    # Load only Group A (M30)
    trades, rbm, batch_info = load_trades_lightweight(db, group_names=["A"])

    # Load Group A + B
    trades, rbm, batch_info = load_trades_lightweight(db, group_names=["A", "B"])

    # trades are duck-type compatible with BacktestTrade —
    # pass directly to precompute_pnl_structures, dedup_batches, etc.
  """
  from sqlalchemy import text as sa_text, inspect as sa_inspect
  from backend.db.models import BacktestRun, BatchBacktestRun

  # ── Step 0: Load group settings (if groups table has the new columns) ──
  group_settings: dict[int, dict] = {}  # group_id → settings
  # CS-034: previously a bare ``except Exception`` here swallowed *every*
  # failure — a transient DB lock or a real schema corruption both
  # silently downgraded to "pre-migration" semantics (no pnl_multiplier,
  # wrong $ amounts). Narrowed to the two conditions that legitimately
  # mean "this DB predates the migration": NoSuchTableError (table not
  # created yet) and OperationalError (SQLite "no such table").
  # Everything else re-raises so a real bug surfaces instead of returning
  # numerically wrong results as if successful.
  from sqlalchemy.exc import NoSuchTableError, OperationalError
  try:
    inspector = sa_inspect(db.bind)
    group_cols = {c["name"] for c in inspector.get_columns("batch_groups")}
    has_group_params = "bybit_limit" in group_cols
  except (NoSuchTableError, OperationalError) as e:
    log.info("batch_groups table missing (%s) — pre-migration DB", type(e).__name__)
    group_cols = set()
    has_group_params = False

  if has_group_params:
    from backend.db.models import BatchGroup
    # Check if pnl_multiplier column exists (may still be backtest_base on old DBs)
    has_multiplier = "pnl_multiplier" in group_cols
    q_cols = [
      BatchGroup.id, BatchGroup.name, BatchGroup.timeframe,
      BatchGroup.bybit_limit, BatchGroup.max_margin_per_trade,
      BatchGroup.min_adv, BatchGroup.slippage_pct, BatchGroup.fee_pct,
    ]
    if has_multiplier:
      q_cols.append(BatchGroup.pnl_multiplier)
    groups = db.query(*q_cols).all()
    for row in groups:
      gid, gname, gtf, gbl, gmmpt, gadv, gslip, gfee = row[:8]
      g_mult = row[8] if has_multiplier and len(row) > 8 else 1.0
      group_settings[gid] = {
        "group_name": gname, "timeframe": gtf, "bybit_limit": gbl,
        "max_margin_per_trade": gmmpt, "min_adv": gadv,
        "slippage_pct": gslip or 0.0, "fee_pct": gfee or 0.0,
        "pnl_multiplier": g_mult if g_mult else 1.0,
      }
    # If filtering by group_names, resolve to batch_ids
    if group_names is not None:
      target_gids = [gid for gid, gs in group_settings.items()
                     if gs["group_name"] in group_names]
      if not target_gids:
        log.warning("load_trades_lightweight: no groups found matching %s", group_names)
        return [], {}, {}
      group_bids = db.query(BatchBacktestRun.id).filter(
        BatchBacktestRun.group_id.in_(target_gids),
        BatchBacktestRun.status == "completed",
        BatchBacktestRun.is_deleted == False,
      ).all()
      batch_ids = [r[0] for r in group_bids]
      log.info("Group filter %s → %d batches (group_ids=%s)",
               group_names, len(batch_ids), target_gids)

  # ── Step 1: Load batch metadata ──
  # Check if per-batch columns exist (migration may not have run yet)
  try:
    inspector = sa_inspect(db.bind)
    existing_cols = {c["name"] for c in inspector.get_columns("batch_backtest_runs")}
  except (NoSuchTableError, OperationalError) as e:
    # CS-034: narrowed from bare except — only swallow the actual
    # pre-migration cases (table missing / DB not yet migrated).
    # A genuine driver or programming error should propagate.
    log.info("batch_backtest_runs columns probe failed (%s) — treating as pre-migration",
             type(e).__name__)
    existing_cols = set()
  has_bybit = "bybit_limit" in existing_cols
  has_mmpt = "max_margin_per_trade" in existing_cols
  has_gid = "group_id" in existing_cols

  # Build column list dynamically
  cols = [BatchBacktestRun.id, BatchBacktestRun.timeframe, BatchBacktestRun.name]
  if has_bybit:
    cols.append(BatchBacktestRun.bybit_limit)
  if has_mmpt:
    cols.append(BatchBacktestRun.max_margin_per_trade)
  if has_gid:
    cols.append(BatchBacktestRun.group_id)

  q = db.query(*cols)
  if batch_ids is None:
    batches = q.filter(
      BatchBacktestRun.status == "completed",
      BatchBacktestRun.is_deleted == False,
    ).all()
  else:
    batches = q.filter(
      BatchBacktestRun.id.in_(batch_ids),
    ).all()

  batch_info: dict[int, dict] = {}
  for row in batches:
    bid = row[0]
    tf = row[1]
    bname = row[2]  # name
    # Per-batch columns (fallback)
    b_bl = getattr(row, "bybit_limit", None) if has_bybit else None
    b_mmpt = getattr(row, "max_margin_per_trade", None) if has_mmpt else None
    b_gid = getattr(row, "group_id", None) if has_gid else None

    # Group settings take priority over per-batch columns
    gs = group_settings.get(b_gid, {}) if b_gid else {}

    limit = gs.get("bybit_limit") or b_bl
    mmpt = gs.get("max_margin_per_trade") or b_mmpt
    multiplier = gs.get("pnl_multiplier", 1.0)
    scale = multiplier if scale_pnl else 1.0

    batch_info[bid] = {
      "timeframe": tf,
      "bybit_limit": limit,
      "max_margin_per_trade": mmpt,
      "pnl_multiplier": multiplier,
      "scale_factor": scale,
      "name": bname,
      "group": gs.get("group_name"),
      "group_id": b_gid,
      "slippage_pct": gs.get("slippage_pct", 0.0),
      "fee_pct": gs.get("fee_pct", 0.0),
    }

  valid_bids = sorted(batch_info.keys())
  if not valid_bids:
    log.warning("load_trades_lightweight: no valid batches found")
    return [], {}, {}

  # ── Step 2: Build run_id → batch_id map ──
  run_batch_map: dict[int, int] = {}
  chunk = 200
  for i in range(0, len(valid_bids), chunk):
    chunk_bids = valid_bids[i:i + chunk]
    rows = db.query(
      BacktestRun.id, BacktestRun.batch_id,
    ).filter(
      BacktestRun.batch_id.in_(chunk_bids),
      BacktestRun.status == "completed",
    ).all()
    for rid, bid in rows:
      run_batch_map[rid] = bid

  if not run_batch_map:
    log.warning("load_trades_lightweight: no runs found for %d batches", len(valid_bids))
    return [], {}, {}

  # ── Step 3: Load only the 6 needed columns ──
  # Build scale lookup: run_id → scale_factor and timeframe
  run_scale = {}
  run_tf = {}
  for rid, bid in run_batch_map.items():
    info = batch_info.get(bid)
    if info:
      run_scale[rid] = info["scale_factor"]
      run_tf[rid] = info["timeframe"]

  all_run_ids = list(run_batch_map.keys())
  trades: list[LightTrade] = []
  chunk = 500

  from backend.db.models import BacktestTrade as BT

  # Aggregate partial-exit legs (TP1 + TP2/TSL) into single positions.
  #
  # Schema reality (verified 2026-04-19 via trade_storage.py:54-58):
  #   The backtest engine emits ONE row per exit leg. Each leg's stored
  #   `net_profit` is the PARTIAL-quantity PnL for that leg (entry commission
  #   is shared proportionally by `share = fill.qty / total_eq`). Summing
  #   every leg of a position therefore reconstructs the full-position PnL.

  positions: dict[tuple, list] = {}
  for i in range(0, len(all_run_ids), chunk):
    chunk_ids = all_run_ids[i:i + chunk]
    rows = db.query(
      BT.run_id, BT.position_id, BT.symbol, BT.side, BT.net_profit,
      BT.entry_time, BT.exit_time, BT.exit_reason,
    ).filter(
      BT.run_id.in_(chunk_ids),
    ).all()
    for run_id, position_id, symbol, side, net_profit, entry_time, exit_time, exit_reason in rows:
      key = (run_id, position_id) if position_id else \
            (run_id, f"_noid__{symbol}__{side}__{entry_time}__{exit_time}")
      positions.setdefault(key, []).append({
        "symbol": symbol, "side": side,
        "net_profit": float(net_profit or 0.0),
        "entry_time": entry_time, "exit_time": exit_time,
        "exit_reason": exit_reason or "",
      })

  for (run_id, _pid), legs in positions.items():
    scale = run_scale.get(run_id, 1.0)
    tf = run_tf.get(run_id, "30")
    symbol = legs[0]["symbol"]; side = legs[0]["side"]

    if len(legs) == 1:
      pnl = legs[0]["net_profit"]
      entry_t = legs[0]["entry_time"]
      exit_t = legs[0]["exit_time"]
    else:
      # Multi-leg partial fills: SUM the leg PnLs (matches UI).
      pnl = sum(l["net_profit"] for l in legs)
      entry_t = min((l["entry_time"] for l in legs if l["entry_time"]), default=None)
      exit_t = max((l["exit_time"] for l in legs if l["exit_time"]), default=None)

    # CS-035: skip trades with no entry_time. Every downstream consumer
    # (WFAS windowing, session filters, active-day counts, equity curves)
    # keys off entry_time; a None entry silently shifts into whichever
    # bucket pandas/Python sort puts it in, corrupting window boundaries.
    # A leg with no timestamp is a storage bug — drop it at the loader
    # boundary so every downstream path sees well-formed trades.
    if entry_t is None:
      log.debug("CS-035: skipping trade with entry_time=None (run_id=%s sym=%s side=%s)",
                run_id, symbol, side)
      continue

    trades.append(LightTrade(
      run_id=run_id,
      symbol=symbol,
      side=side,
      net_profit=pnl * scale,
      entry_time=entry_t,
      exit_time=exit_t,
      timeframe=tf,
    ))

  log.info(
    "load_trades_lightweight: %d trades from %d batches (%d runs), "
    "scale_pnl=%s",
    len(trades), len(valid_bids), len(run_batch_map), scale_pnl,
  )

  # Log per-TF summary
  tf_counts: dict[str, int] = defaultdict(int)
  for t in trades:
    tf_counts[t.timeframe] += 1
  for tf, cnt in sorted(tf_counts.items()):
    info_sample = next(
      (v for v in batch_info.values() if v["timeframe"] == tf), {}
    )
    log.info(
      "  TF=%s: %d trades, bybit_limit=$%.0f, scale=%.2fx",
      tf, cnt, info_sample.get("bybit_limit", 0), info_sample.get("scale_factor", 1),
    )

  return trades, run_batch_map, batch_info


@dataclass
class BatchSelectionConfig:
  """All user-configurable batch selection parameters."""
  max_batches: int = 4
  assets_per_batch: int = 5
  n_candidates: int = 20
  min_calibrated: int = 3
  min_pf: float = 1.0
  min_is_days: int = 30
  min_asset_days: int = 5
  asset_must_profit: bool = False
  cal_must_profit: bool = True
  batch_rank_metric: str = "recovery"
  cal_rank_metric: str = "ulcer"
  asset_rank_metric: str = "net_pnl"
  max_tpm: int = 40
  asset_wr_gate: float = 0.20  # Min IS win rate for asset selection (0 = disabled)
  # ── Advanced features (from sweep research) ──
  # Recency Momentum: weight recent IS PnL more heavily in Step 1.
  # 0 = off (uniform). 2.0 = newest day weighted 2x oldest.
  recency_weight: float = 0.0
  # Adaptive mb: auto-determine max_batches by quality-gap analysis.
  # When enabled, max_batches becomes the upper bound.
  adaptive_mb: bool = False
  adaptive_mb_min: int = 2
  adaptive_mb_max: int = 7
  adaptive_mb_gap_pct: int = 75  # percentile threshold for gap detection
  # Ensemble Calibration Gate: require top-50% in K of 5 risk metrics
  # instead of single cal_rank_metric. 0 = single metric (default).
  ensemble_cal_k: int = 0
  # Regime-Conditional: multiply IS score by cosine similarity of
  # batch regime profile vs aggregate IS regime. 0 = off, 1.0 = full weight.
  regime_weight: float = 0.0
  # ELO Tournament: replace IS ranking with round-robin ELO ratings.
  # 0 = off, >0 = number of subsample iterations per matchup.
  elo_iterations: int = 0
  # Exec filter config — applied to OOS evaluation ONLY (not IS/CAL).
  # When set, run_batch_selection uses all trades for IS/CAL scoring but
  # builds a separate exec-filtered asset PnL for OOS evaluation.
  exec_exclude_hours: set[int] | None = None  # NYT hours to exclude from exec eval
  exec_exclude_days: set[int] | None = None   # weekdays to exclude (0=Mon..6=Sun)
  # ── Research-validated features (from co-work sweeps) ──
  # L2 Groups: force diversity by selecting batches from both long and short
  # pools independently. Mode determines how slots are allocated.
  l2_groups: bool = False
  # L2 Mode: how to split mb slots between Long and Short when l2_groups=True.
  #   "fixed"        — ceil(mb/2) from each side (legacy 50/50 behavior)
  #   "proportional"  — allocate slots proportional to each side's representation
  #                     in the IS-ranked candidate pool (min 1 per side)
  #   "threshold"     — give (mb-1) slots to whichever side has the higher
  #                     average IS score in the candidate pool, 1 to the other
  #   "min1"          — guarantee 1 from each side, fill remaining slots from
  #                     overall ranking regardless of side
  l2_mode: str = "fixed"
  # Progressive Pruning: pre-filter batch pool by historical quality.
  # Two-pass: first pass counts appearances/avg rank, second uses pruned pool.
  # prune_long/prune_short = how many worst batches to remove from each side.
  pruning_enabled: bool = False
  prune_long: int = 0    # Remove N worst LONG batches (e.g. 4 in Pr4R8)
  prune_short: int = 0   # Remove N worst SHORT batches (e.g. 8 in Pr4R8)
  # CS-005: Progressive Pruning uses ALL windows (including future IS periods)
  # to decide which batches to drop. Any OOS metric produced with pruning is
  # contaminated by look-ahead and is RESEARCH ONLY — never tradeable. The
  # caller must set this to True alongside pruning_enabled=True to acknowledge
  # that; otherwise run_batch_selection raises ValueError before producing
  # any numbers. Off by default so stale configs fail loud instead of silently
  # leaking contaminated numbers to a live portfolio.
  research_only_acknowledged: bool = False
  # Past OOS Confirmation: in Step 3, require assets to be profitable in
  # the PREVIOUS OOS window. Filters out assets that don't confirm.
  past_oos_confirm: bool = False
  # Q-Aligned Averaging: run the full pipeline from 4 quarter-aligned
  # starts (Jan/Apr/Jul/Oct) and average results. 1 = single run (default),
  # 4 = full Q-averaging (matches research methodology).
  q_starts: int = 1  # 1 or 4
  # Live Window: after all historical windows, add a forward-looking window
  # that uses the latest IS period to select assets for the current (live) period.
  # OOS = today onwards, so no OOS metrics — just asset selection for trading.
  include_live_window: bool = True
  # ── Overfit Guard: penalize suspiciously extreme assets in Step 3 ──
  # Applies guard mechanisms from overfit_guard.py as multipliers on asset
  # scores before ranking. Catches "too good to be true" in-sample assets.
  # Research: +5-9pp WR lift across both A+B and full pools.
  overfit_guard_enabled: bool = False
  # Guard mode selects which mechanism(s) to apply:
  #   "log"       — Log Diminish: compress extreme metrics (best single guard, +5.8pp)
  #   "zscore"    — Z-Score Penalty: penalize >2σ within-batch outliers
  #   "bayes"     — Bayesian Shrinkage: shrink toward batch mean
  #   "log_zscore"— Log + Z-Score combined
  #   "full"      — All 5 guards (Log + Z-Score + Bayes + Sigmoid + NR)
  overfit_guard_mode: str = "log"
  # Log Diminish caps (only for log/log_zscore/full modes)
  og_log_cap_pf: float = 4.0
  og_log_cap_sortino: float = 3.0
  og_log_cap_payoff: float = 3.0
  og_log_cap_sharpe: float = 2.5
  og_log_cap_calmar: float = 3.0
  og_log_cap_wr: float = 70.0
  # Z-Score params (only for zscore/log_zscore/full modes)
  og_z_threshold: float = 2.0
  og_z_penalty_strength: float = 0.15
  # Bayesian shrinkage (only for bayes/full modes)
  og_bayes_k: float = 5.0
  # ── Rescue System (Feature 5-6) ──
  hybrid_sample_count_floor: int = 0        # Feature 2: exclude assets with < N trades
  hybrid_rescue_enabled: bool = False       # Feature 5: enable rescue system
  hybrid_rescue_quota_pct: float = 0.10     # Feature 5: max % of slate that can be rescued (0.0–1.0)
  hybrid_rescue_min_trades: int = 30        # Feature 5: per-asset minimum for rescue eligibility
  hybrid_rescue_min_score: float = 0.0      # Feature 5: absolute score floor for rescue
  hybrid_rescue_confidence_gap: float = 0.15  # Feature 6: min gap between best rescue and worst accepted

  # ── Category Filter (portable from hybrid path) ──────────────────
  # Mirrors event_wfo's per-decision-point category scoring. When enabled,
  # symbols within the selected batches are re-ranked per window using the
  # same composite-weighted category scores the hybrid path computes.
  # Refresh cadence is per window (every OOS rollover) by default.
  category_filter_enabled: bool = False
  category_influence: str = "none"            # none|low|mid|high|all|event
  category_lookback_trades: int = 50          # per-symbol trailing window
  category_score_metric: str = "composite"    # pnl|pf|sortino|composite
  category_max_per_cat: int = 0               # 0 = uncapped
  # Category refresh cadence. "per_window" recomputes at each OOS rollover
  # (cleanest for live trading). "fixed" computes once from the first IS window.
  category_refresh_mode: str = "per_window"
  # Optional per-run composite weight overrides (same semantics as hybrid path).
  # If None, use a fixed default matching the hybrid JSON convention.
  category_composite_weights: dict | None = None
  # ── Category Mode (session 2026-04-17-B — on-fire event variant) ──
  # "tertile"      : legacy behavior — composite-score ranking + top/mid/bottom
  #                  tertile multipliers keyed by category_influence.
  # "streak_event" : event-driven — a category is "on fire" when the last
  #                  ``category_streak_required`` consecutive pooled trades
  #                  within the trailing (streak + fire_duration) window were
  #                  all profitable. On-fire categories get their assets'
  #                  scores multiplied by ``category_fire_multiplier`` during
  #                  hybrid rebalance selection. Cold categories passthrough.
  #                  Validated T06 (2026-04-17-B): +$199/q, -$392/q DD, +0.31 PF
  #                  on s33_E4b_v3_ddweight vs no-filter baseline.
  category_mode: str = "tertile"
  category_streak_required: int = 5           # consecutive winners to trigger
  category_fire_multiplier: float = 1.30      # boost applied while on fire
  category_fire_duration_trades: int = 10     # trailing window after streak

  def __post_init__(self) -> None:
    """CS-067: loud-fail on enum typos and out-of-range numerics.

    A silent default in ``if mode == "log": ... elif ...`` chains is
    exactly the non-coder-safety concern the project rules flag —
    a user cannot visually detect that a saved preset with a typo'd
    ``overfit_guard_mode`` silently runs in the default branch. Raise
    at config construction so the misconfiguration surfaces instead
    of producing a misleading but plausible result.

    Paired with CS-031's martingale-scope validation — same shape.
    """
    # Sourced from the scoring dispatcher so a new metric added to
    # batch_selection_scoring._METRICS is automatically accepted here.
    _allowed_batch_rank = set(METRIC_NAMES)
    _allowed_cal_rank = _allowed_batch_rank
    _allowed_asset_rank = _allowed_batch_rank
    _allowed_og_mode = {"log", "zscore", "bayes", "log_zscore", "full"}
    _allowed_cat_influence = {"none", "low", "mid", "high", "all", "event"}
    _allowed_cat_refresh = {"per_window", "fixed"}
    _allowed_cat_mode = {"tertile", "streak_event"}
    _allowed_cat_score = {"pnl", "pf", "sortino", "composite"}
    _allowed_l2_mode = {"fixed", "proportional", "threshold", "min1"}

    def _check(value: str, allowed: set[str], field_name: str) -> None:
      if value not in allowed:
        raise ValueError(
          f"BatchSelectionConfig.{field_name}={value!r} is not in the "
          f"allowed set {sorted(allowed)!r}. This usually means a typo "
          f"in a saved preset — fix it at the call site."
        )

    _check(self.batch_rank_metric, _allowed_batch_rank, "batch_rank_metric")
    _check(self.cal_rank_metric, _allowed_cal_rank, "cal_rank_metric")
    _check(self.asset_rank_metric, _allowed_asset_rank, "asset_rank_metric")
    _check(self.overfit_guard_mode, _allowed_og_mode, "overfit_guard_mode")
    _check(self.category_influence, _allowed_cat_influence, "category_influence")
    _check(self.category_refresh_mode, _allowed_cat_refresh, "category_refresh_mode")
    _check(self.category_mode, _allowed_cat_mode, "category_mode")
    _check(self.category_score_metric, _allowed_cat_score, "category_score_metric")
    _check(self.l2_mode, _allowed_l2_mode, "l2_mode")

    # Numeric range guards for the fields where a negative or zero
    # value silently degrades without failing.
    if self.assets_per_batch < 1:
      raise ValueError(
        f"BatchSelectionConfig.assets_per_batch={self.assets_per_batch} "
        f"must be >= 1."
      )
    if self.max_batches < 1:
      raise ValueError(
        f"BatchSelectionConfig.max_batches={self.max_batches} must be >= 1."
      )
    if self.recency_weight < 0:
      raise ValueError(
        f"BatchSelectionConfig.recency_weight={self.recency_weight} "
        f"must be >= 0."
      )
    if not (0.0 <= self.hybrid_rescue_quota_pct <= 1.0):
      raise ValueError(
        f"BatchSelectionConfig.hybrid_rescue_quota_pct="
        f"{self.hybrid_rescue_quota_pct} must be in [0.0, 1.0]."
      )


# ── Dedup ─────────────────────────────────────────────────────

def dedup_batches(
  trades: list[BacktestTrade],
  run_batch_map: dict[int, int],
) -> tuple[list[BacktestTrade], dict[int, int], list[int]]:
  """Remove duplicate batches by content-addressable fingerprint.

  Fingerprint is a truncated SHA-256 over the sorted trade tuple set —
  (symbol, side, entry_time, exit_time, round(net_profit, 4)) — which makes
  unrelated batches with coincidentally identical trade count and rounded
  total PnL no longer collide. Keeps the lowest batch_id per fingerprint.

  Returns (filtered_trades, filtered_map, deduped_batch_ids).
  """
  import hashlib

  # Group trades by batch_id
  batch_trades: dict[int, list[BacktestTrade]] = defaultdict(list)
  for t in trades:
    bid = run_batch_map.get(t.run_id)
    if bid is not None:
      batch_trades[bid].append(t)

  # Build fingerprints
  fingerprints: dict[str, int] = {}
  for bid in sorted(batch_trades.keys()):
    bt = batch_trades[bid]
    tuples = sorted(
      (
        getattr(t, "symbol", "") or "",
        getattr(t, "side", "") or "",
        str(getattr(t, "entry_time", "") or ""),
        str(getattr(t, "exit_time", "") or ""),
        round(float(t.net_profit or 0.0), 4),
      )
      for t in bt
    )
    # CS-052: hash tuple-by-tuple so memory stays O(1) regardless of batch size.
    # The old `sha256(repr(tuples))` materialised the entire list's repr in RAM,
    # which is a ~100MB spike per 3M-trade batch.
    h_obj = hashlib.sha256()
    for tup in tuples:
      h_obj.update(repr(tup).encode("utf-8"))
      h_obj.update(b"\x00")
    h = h_obj.hexdigest()[:16]
    fp = f"{len(bt)}_{h}"
    if fp not in fingerprints:
      fingerprints[fp] = bid

  keep = set(fingerprints.values())
  removed = set(batch_trades.keys()) - keep
  if removed:
    log.info("Dedup: removed %d duplicate batches: %s", len(removed), sorted(removed))

  # Filter trades and map
  keep_runs = {rid for rid, bid in run_batch_map.items() if bid in keep}
  filtered_trades = [t for t in trades if t.run_id in keep_runs]
  filtered_map = {rid: bid for rid, bid in run_batch_map.items() if bid in keep}

  return filtered_trades, filtered_map, sorted(keep)


# ── Batch side detection ──────────────────────────────────────

def detect_batch_sides(
  trades: list[BacktestTrade],
  run_batch_map: dict[int, int],
) -> dict[int, str]:
  """Determine the dominant side ('Long' or 'Short') of each batch.

  Counts trades per side per batch. The side with more trades wins.
  Returns batch_id → 'Long' or 'Short'.
  """
  side_counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
  for t in trades:
    bid = run_batch_map.get(t.run_id)
    if bid is not None:
      s = (t.side or "Long").title()
      side_counts[bid][s] += 1

  result: dict[int, str] = {}
  for bid, counts in side_counts.items():
    long_n = counts.get("Long", 0)
    short_n = counts.get("Short", 0)
    result[bid] = "Short" if short_n > long_n else "Long"
  return result


# ── Build daily PnL structures ────────────────────────────────

def build_batch_daily_pnl(
  trades: list[BacktestTrade],
  run_batch_map: dict[int, int],
) -> dict[int, pd.Series]:
  """Per-batch daily PnL indexed by ENTRY date.

  Uses entry date so trades are attributed to the period where the
  decision was made, preventing IS/OOS boundary contamination.
  Reindexed to full calendar date range, missing days filled with 0.
  """
  batch_rows: dict[int, list[tuple[date, float]]] = defaultdict(list)
  for t in trades:
    bid = run_batch_map.get(t.run_id)
    if bid is None or not t.entry_time:
      continue
    d = pd.Timestamp(t.entry_time).floor("D")
    batch_rows[bid].append((d, t.net_profit))

  result: dict[int, pd.Series] = {}
  for bid, rows in batch_rows.items():
    df = pd.DataFrame(rows, columns=["date", "pnl"])
    daily = df.groupby("date")["pnl"].sum()
    if daily.empty:
      continue
    idx = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    result[bid] = daily.reindex(idx, fill_value=0.0)

  return result


def build_asset_daily_pnl(
  trades: list[BacktestTrade],
  run_batch_map: dict[int, int],
) -> tuple[
  dict[str, pd.Series],
  dict[str, int],
  dict[int, list[str]],
  dict[str, list[pd.Timestamp]],
  dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]],
]:
  """Per-asset daily PnL indexed by ENTRY date.

  Uses entry date so trades are attributed to the period where the
  decision was made, preventing IS/OOS boundary contamination.
  Asset key format: "{batch_id}_{symbol}_{side}"
  Skips assets with fewer than 3 trades.

  Returns:
    asset_daily: key → daily PnL Series
    asset_to_batch: key → batch_id
    batch_to_assets: batch_id → [keys]
    asset_trade_dates: key → sorted list of entry dates
    asset_trade_intervals: key → sorted list of (entry_time, exit_time)
  """
  asset_rows: dict[str, list[tuple[pd.Timestamp, float]]] = defaultdict(list)
  asset_trade_count: dict[str, int] = defaultdict(int)
  asset_intervals_raw: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = defaultdict(list)

  for t in trades:
    bid = run_batch_map.get(t.run_id)
    if bid is None or not t.entry_time:
      continue
    side = (t.side or "Long").title()
    key = f"{bid}_{t.symbol}_{side}"
    d = pd.Timestamp(t.entry_time).floor("D")
    asset_rows[key].append((d, t.net_profit))
    asset_trade_count[key] += 1
    # Track entry/exit intervals for concurrent trade calculation
    if t.exit_time:
      asset_intervals_raw[key].append(
        (pd.Timestamp(t.entry_time), pd.Timestamp(t.exit_time))
      )

  asset_daily: dict[str, pd.Series] = {}
  asset_to_batch: dict[str, int] = {}
  batch_to_assets: dict[int, list[str]] = defaultdict(list)
  asset_trade_dates: dict[str, list[pd.Timestamp]] = {}
  asset_trade_intervals: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}

  for key, rows in asset_rows.items():
    if asset_trade_count[key] < 3:
      continue
    bid = int(key.split("_")[0])
    df = pd.DataFrame(rows, columns=["date", "pnl"])
    daily = df.groupby("date")["pnl"].sum()
    if daily.empty:
      continue
    idx = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    asset_daily[key] = daily.reindex(idx, fill_value=0.0)
    asset_to_batch[key] = bid
    batch_to_assets[bid].append(key)
    # Sorted list of actual trade entry dates (for TPM estimation)
    asset_trade_dates[key] = sorted(df["date"].tolist())
    # Sorted intervals for concurrent trade calculation
    if key in asset_intervals_raw:
      asset_trade_intervals[key] = sorted(asset_intervals_raw[key], key=lambda x: x[0])

  return asset_daily, asset_to_batch, dict(batch_to_assets), asset_trade_dates, asset_trade_intervals


def build_asset_wr_data(
  trades: list[BacktestTrade],
  run_batch_map: dict[int, int],
) -> dict[str, pd.DataFrame]:
  """Per-asset daily win/total counts indexed by ENTRY date.

  Returns dict mapping asset key → DataFrame with columns ["wins", "total"]
  and date index. Used by _compute_asset_wr_in_range when raw trades are not
  available (precomputed mode).
  """
  rows: dict[str, dict[pd.Timestamp, list[int, int]]] = defaultdict(
    lambda: defaultdict(lambda: [0, 0])
  )
  for t in trades:
    bid = run_batch_map.get(t.run_id)
    if bid is None or not t.entry_time:
      continue
    side = (t.side or "Long").title()
    key = f"{bid}_{t.symbol}_{side}"
    d = pd.Timestamp(t.entry_time).floor("D")
    rows[key][d][1] += 1  # total
    if (t.net_profit or 0) > 0:
      rows[key][d][0] += 1  # wins

  result: dict[str, pd.DataFrame] = {}
  for key, date_map in rows.items():
    dates = sorted(date_map.keys())
    wins = [date_map[d][0] for d in dates]
    totals = [date_map[d][1] for d in dates]
    result[key] = pd.DataFrame(
      {"wins": wins, "total": totals}, index=pd.DatetimeIndex(dates)
    )
  return result


def build_straddle_table(
  trades: list[BacktestTrade],
  run_batch_map: dict[int, int],
) -> dict[str, np.ndarray]:
  """Trades that stay open past midnight, as parallel arrays.

  Daily PnL is keyed by ENTRY date, so a trade entered on a window's last
  in-sample day carries its full result — known only after it exits — into
  the selection. Only trades whose exit day is after their entry day can be
  open at a day-end decision cutoff, so this table is all
  `_known_at_decision()` needs to remove them (O1, 2026-10-08).

  Keys: bid, key, entry_day, exit_day (datetime64[D]), pnl, win (0/1).
  """
  bids: list[int] = []
  keys: list[str] = []
  entry_days: list = []
  exit_days: list = []
  pnls: list[float] = []
  wins: list[int] = []
  for t in trades:
    bid = run_batch_map.get(t.run_id)
    if bid is None or not t.entry_time or not t.exit_time:
      continue
    ed = t.entry_time.date() if isinstance(t.entry_time, datetime) else pd.Timestamp(t.entry_time).date()
    xd = t.exit_time.date() if isinstance(t.exit_time, datetime) else pd.Timestamp(t.exit_time).date()
    if xd <= ed:
      continue
    side = (t.side or "Long").title()
    bids.append(bid)
    keys.append(f"{bid}_{t.symbol}_{side}")
    entry_days.append(ed)
    exit_days.append(xd)
    pnl = float(t.net_profit or 0.0)
    pnls.append(pnl)
    wins.append(1 if pnl > 0 else 0)
  return {
    "bid": np.array(bids, dtype=np.int64),
    "key": np.array(keys, dtype=object),
    "entry_day": np.array(entry_days, dtype="datetime64[D]"),
    "exit_day": np.array(exit_days, dtype="datetime64[D]"),
    "pnl": np.array(pnls, dtype=float),
    "win": np.array(wins, dtype=np.int64),
  }


def _known_at_decision(
  batch_daily: dict[int, pd.Series],
  asset_daily: dict[str, pd.Series],
  asset_wr_data: dict[str, pd.DataFrame] | None,
  straddle_table: dict[str, np.ndarray] | None,
  decision_day: pd.Timestamp,
) -> tuple[dict[int, pd.Series], dict[str, pd.Series], dict[str, pd.DataFrame] | None]:
  """What the selection could actually see at the end of `decision_day`.

  Removes every trade entered on or before `decision_day` that was still
  open at its end (exit day after it) from the entry-date series. Only the
  affected series are copied; the inputs are never modified. A day whose
  only trades were removed becomes exactly 0 so active-day counts drop too.
  """
  if not straddle_table or len(straddle_table["pnl"]) == 0:
    return batch_daily, asset_daily, asset_wr_data
  cut = np.datetime64(pd.Timestamp(decision_day).date(), "D")
  mask = (straddle_table["entry_day"] <= cut) & (straddle_table["exit_day"] > cut)
  if not mask.any():
    return batch_daily, asset_daily, asset_wr_data

  open_trades = pd.DataFrame({
    "bid": straddle_table["bid"][mask],
    "key": straddle_table["key"][mask],
    "day": pd.to_datetime(straddle_table["entry_day"][mask]),
    "pnl": straddle_table["pnl"][mask],
    "win": straddle_table["win"][mask],
  })

  def _remove(series_by_id: dict, sums: pd.Series) -> dict:
    out = dict(series_by_id)
    for (sid, day), amount in sums.items():
      s = out.get(sid)
      if s is None or day not in s.index:
        continue
      if s is series_by_id.get(sid):
        s = s.copy()
        out[sid] = s
      v = float(s.at[day]) - float(amount)
      s.at[day] = 0.0 if abs(v) < 1e-9 else v
    return out

  known_batch = _remove(batch_daily, open_trades.groupby(["bid", "day"])["pnl"].sum())
  known_asset = _remove(asset_daily, open_trades.groupby(["key", "day"])["pnl"].sum())

  known_wr = asset_wr_data
  if asset_wr_data:
    known_wr = dict(asset_wr_data)
    counts = open_trades.groupby(["key", "day"]).agg(n=("pnl", "size"), w=("win", "sum"))
    for (key, day), row in counts.iterrows():
      df = known_wr.get(key)
      if df is None or day not in df.index:
        continue
      if df is asset_wr_data.get(key):
        df = df.copy()
        known_wr[key] = df
      df.at[day, "total"] = int(df.at[day, "total"]) - int(row["n"])
      df.at[day, "wins"] = int(df.at[day, "wins"]) - int(row["w"])

  return known_batch, known_asset, known_wr


def is_excluded_by_exec_filter(
  entry_time,
  exclude_hours: set[int] | None,
  exclude_days: set[int] | None,
) -> bool:
  """CS-014: single source of truth for exec-filter predicate.

  Callers that operate on different shapes (BacktestTrade, LightTrade,
  raw SQL tuples) all share this decision so the filter can't drift
  between sites. A return of ``True`` means the trade should be
  EXCLUDED from exec-eligible metrics.

  Rules (all must be false for the trade to survive):
    - entry_time is None → exclude (can't classify a trade with no time)
    - entry_time.hour ∈ exclude_hours → exclude
    - entry_time.weekday() ∈ exclude_days → exclude
  """
  if entry_time is None:
    return True
  if exclude_hours and entry_time.hour in exclude_hours:
    return True
  if exclude_days and entry_time.weekday() in exclude_days:
    return True
  return False


def _filter_trades_exec(
  trades: list[BacktestTrade],
  exclude_hours: set[int] | None,
  exclude_days: set[int] | None,
) -> list[BacktestTrade]:
  """Filter trades by entry-hour/day for exec evaluation."""
  if not exclude_hours and not exclude_days:
    return trades
  return [
    t for t in trades
    if not is_excluded_by_exec_filter(t.entry_time, exclude_hours, exclude_days)
  ]


def _compute_asset_wr_in_range(
  trades: list[BacktestTrade],
  run_batch_map: dict[int, int],
  key: str,
  start: str,
  end: str,
  asset_wr_data: dict[str, pd.DataFrame],
) -> tuple[int, float]:
  """Compute trade count and win rate for a specific asset key in [start, end].

  Returns (trade_count, win_rate). Uses ENTRY date for range filtering
  (consistent with entry-date slicing used by build_*_daily_pnl).

  `asset_wr_data` is required (precomputed per-day wins/totals from
  `build_asset_wr_data`). The raw-trade fallback was removed (B15) because
  iterating ~2-3M trades per asset × window was catastrophically slow on
  recommender paths; all production callers already precompute.
  `trades` and `run_batch_map` are kept for signature stability.
  """
  s = pd.Timestamp(start)
  e = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
  df = asset_wr_data.get(key)
  if df is None:
    return 0, 0.0
  mask = (df.index >= s) & (df.index <= e)
  sl = df.loc[mask]
  total = int(sl["total"].sum())
  wins = int(sl["wins"].sum())
  wr = wins / total if total > 0 else 0.0
  return total, wr


# ── Internal helpers ──────────────────────────────────────────

def _slice_pnl(pnl: pd.Series, start: str, end: str) -> pd.Series:
  """Slice a daily PnL series to [start, end] inclusive."""
  s = pd.Timestamp(start)
  e = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
  return pnl.loc[(pnl.index >= s) & (pnl.index <= e)]


def _nonzero_days(pnl: pd.Series) -> int:
  """Count non-zero PnL days in a series."""
  return int((pnl != 0).sum())


def _compute_pf(pnl: pd.Series) -> float:
  """Simple profit factor from daily PnL."""
  gains = float(pnl[pnl > 0].sum())
  losses = float(pnl[pnl < 0].sum())
  if losses == 0:
    return 10.0 if gains > 0 else 0.0
  return min(gains / abs(losses), 10.0)


def _count_trades_in_range(
  dates: list[pd.Timestamp], start: str, end: str,
) -> int:
  """Count trades with entry dates in [start, end]."""
  s = pd.Timestamp(start)
  e = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
  return sum(1 for d in dates if s <= d <= e)


def _is_months_count(is_start: str, is_end: str) -> float:
  """Approximate number of months between two dates."""
  s = pd.Timestamp(is_start)
  e = pd.Timestamp(is_end)
  return max((e - s).days / 30.44, 1.0)


def _rich_metrics_from_daily(combined_pnl: pd.Series, total_trades: int) -> dict:
  """Compute a full suite of portfolio metrics from a daily PnL series.

  Returns a dict with all KPIs needed for both IS and OOS reporting:
    net_profit, profit_factor, max_drawdown, win_rate, total_trades,
    gross_profit, gross_loss, avg_trade, stability, sortino, sharpe,
    calmar, recovery_factor, return_dd_ratio, longest_dd_days,
    expectancy, avg_win, avg_loss, payoff_ratio, n_wins, n_losses,
    total_days, active_days
  """
  if combined_pnl.empty:
    return {
      "net_profit": 0, "profit_factor": 0, "max_drawdown": 0,
      "win_rate": 0, "total_trades": 0, "stability": 0,
      "gross_profit": 0, "gross_loss": 0, "avg_trade": 0,
      "sortino": 0, "sharpe": 0, "calmar": 0,
      "recovery_factor": 0, "return_dd_ratio": 0,
      "longest_dd_days": 0, "expectancy": 0,
      "avg_win": 0, "avg_loss": 0, "payoff_ratio": 0,
      "n_wins": 0, "n_losses": 0,
      "total_days": 0, "active_days": 0,
    }

  net = float(combined_pnl.sum())
  gp = float(combined_pnl[combined_pnl > 0].sum())
  gl = float(combined_pnl[combined_pnl < 0].sum())
  pf = min(gp / abs(gl), 10.0) if gl != 0 else (10.0 if gp > 0 else 0.0)

  equity = combined_pnl.cumsum()
  drawdown = equity - equity.cummax()
  dd = float(drawdown.min()) if len(equity) > 0 else 0.0

  active = combined_pnl[combined_pnl != 0]
  wins = int((active > 0).sum())
  losses = int((active < 0).sum())
  wr = wins / len(active) if len(active) > 0 else 0.0
  avg_trade = net / total_trades if total_trades > 0 else 0.0

  # Win/loss averages and payoff
  win_vals = active[active > 0]
  loss_vals = active[active < 0]
  avg_win = float(win_vals.mean()) if len(win_vals) > 0 else 0.0
  avg_loss = float(loss_vals.mean()) if len(loss_vals) > 0 else 0.0
  payoff = abs(avg_win / avg_loss) if avg_loss != 0 else (10.0 if avg_win > 0 else 0.0)
  expectancy = (wr * avg_win) + ((1 - wr) * avg_loss) if len(active) > 0 else 0.0

  # Risk-adjusted ratios — computed on daily returns
  mean_daily = float(combined_pnl.mean())
  std_daily = float(combined_pnl.std()) if len(combined_pnl) > 1 else 0.0
  downside = combined_pnl[combined_pnl < 0]
  downside_std = float(downside.std()) if len(downside) > 1 else 0.0

  # Crypto trades 24/7/365 — annualise on 365, matching backend.analysis.shared.daily_ann_factor.
  ann_factor = np.sqrt(365)
  sharpe = (mean_daily / std_daily * ann_factor) if std_daily > 0 else 0.0
  sortino = (mean_daily / downside_std * ann_factor) if downside_std > 0 else 0.0

  # Calmar: annualized return / max drawdown
  ann_return = mean_daily * 365
  calmar = (ann_return / abs(dd)) if dd != 0 else 0.0

  # Recovery Factor: net profit / max drawdown
  recovery_factor = (net / abs(dd)) if dd != 0 else 0.0
  return_dd_ratio = recovery_factor  # alias

  # Longest drawdown duration (days)
  longest_dd_days = 0
  if len(drawdown) > 0:
    in_dd = drawdown < 0
    if in_dd.any():
      # Run-length encoding of drawdown periods
      changes = in_dd.astype(int).diff().fillna(0)
      dd_groups = changes.ne(0).cumsum()
      dd_runs = in_dd.groupby(dd_groups).sum()
      longest_dd_days = int(dd_runs.max()) if len(dd_runs) > 0 else 0

  # Stability R² — how linear is the equity curve
  stability = 0.0
  if len(equity) >= 3:
    try:
      x = np.arange(len(equity))
      ss_res = float(np.sum((equity.values - np.polyval(np.polyfit(x, equity.values, 1), x)) ** 2))
      ss_tot = float(np.sum((equity.values - equity.values.mean()) ** 2))
      stability = max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    except (np.linalg.LinAlgError, ValueError, FloatingPointError) as e:
      # CS-046: narrowed from bare ``except Exception``. Real failure modes
      # here are numerical: LinAlgError on singular matrices, ValueError on
      # mismatched shape, FloatingPointError on overflow. A logic bug
      # (AttributeError, TypeError) must not be swallowed as "stability=0".
      log.debug("stability R^2 computation failed (%s: %s) — using 0.0",
                type(e).__name__, e)
      stability = 0.0

  # Max consecutive wins/losses from active (non-zero) daily PnL
  max_consec_wins = 0
  max_consec_losses = 0
  if len(active) > 0:
    streak = 0
    for v in active.values:
      if v > 0:
        streak = streak + 1 if streak > 0 else 1
        max_consec_wins = max(max_consec_wins, streak)
      elif v < 0:
        streak = streak - 1 if streak < 0 else -1
        max_consec_losses = max(max_consec_losses, abs(streak))
      else:
        streak = 0

  return {
    "net_profit": round(net, 2),
    "profit_factor": round(pf, 4),
    "max_drawdown": round(dd, 2),
    "win_rate": round(wr, 4),
    "total_trades": total_trades,
    "stability": round(stability, 4),
    "gross_profit": round(gp, 2),
    "gross_loss": round(abs(gl), 2),
    "avg_trade": round(avg_trade, 2),
    "sortino": round(sortino, 4),
    "sharpe": round(sharpe, 4),
    "calmar": round(calmar, 4),
    "recovery_factor": round(recovery_factor, 4),
    "return_dd_ratio": round(return_dd_ratio, 4),
    "longest_dd_days": longest_dd_days,
    "expectancy": round(expectancy, 2),
    "avg_win": round(avg_win, 2),
    "avg_loss": round(avg_loss, 2),
    "payoff_ratio": round(payoff, 4),
    "n_wins": wins,
    "n_losses": losses,
    "max_consec_wins": max_consec_wins,
    "max_consec_losses": max_consec_losses,
    "total_days": len(combined_pnl),
    "active_days": len(active),
  }


def _combine_asset_pnl(
  selected_assets: list[str],
  asset_daily: dict[str, pd.Series],
  asset_trade_dates: dict[str, list[pd.Timestamp]],
  start: str,
  end: str,
) -> tuple[pd.Series, int]:
  """Combine daily PnL across selected assets for a given period.

  Returns (combined_daily_pnl, total_trade_count).
  """
  combined = pd.Series(dtype=float)
  total_trades = 0
  for key in selected_assets:
    pnl = asset_daily.get(key)
    if pnl is None:
      continue
    sl = _slice_pnl(pnl, start, end)
    if combined.empty:
      combined = sl.copy()
    else:
      combined = combined.add(sl, fill_value=0.0)
    total_trades += _count_trades_in_range(
      asset_trade_dates.get(key, []), start, end,
    )
  return combined, total_trades


def _build_equity_curve(combined_pnl: pd.Series) -> list[dict]:
  """Build equity curve data from daily PnL series.

  Returns list of {date, equity, drawdown_pct} dicts for charting.
  """
  if combined_pnl.empty:
    return []
  equity = combined_pnl.cumsum()
  peak = equity.cummax()
  dd_pct = ((equity - peak) / peak.where(peak > 0, 1.0) * 100).fillna(0.0)
  return [
    {
      "date": str(d.date()) if hasattr(d, "date") else str(d),
      "equity": round(float(v), 2),
      "drawdown_pct": round(float(dd_pct.loc[d]), 2),
    }
    for d, v in equity.items()
  ]


def _build_monthly_matrix(combined_pnl: pd.Series) -> dict:
  """Build monthly PnL matrix from daily PnL series.

  Returns {years, matrix: {year: {month: pnl}}, row_totals, col_totals, grand_total}.
  """
  if combined_pnl.empty:
    return {"years": [], "matrix": {}, "row_totals": {}, "col_totals": {}, "grand_total": 0}

  monthly = combined_pnl.groupby([combined_pnl.index.year, combined_pnl.index.month]).sum()
  years = sorted(set(combined_pnl.index.year))
  matrix: dict[str, dict[str, float]] = {}
  row_totals: dict[str, float] = {}
  col_totals: dict[str, float] = {str(m): 0.0 for m in range(1, 13)}

  for y in years:
    y_str = str(y)
    matrix[y_str] = {}
    row_total = 0.0
    for m in range(1, 13):
      val = float(monthly.get((y, m), 0.0))
      matrix[y_str][str(m)] = round(val, 2)
      row_total += val
      col_totals[str(m)] += val
    row_totals[y_str] = round(row_total, 2)

  for m in col_totals:
    col_totals[m] = round(col_totals[m], 2)

  return {
    "years": [str(y) for y in years],
    "matrix": matrix,
    "row_totals": row_totals,
    "col_totals": col_totals,
    "grand_total": round(sum(row_totals.values()), 2),
  }


def _compute_asset_breakdown(
  selected_assets: list[str],
  asset_daily: dict[str, pd.Series],
  asset_trade_dates: dict[str, list[pd.Timestamp]],
  batch_info: dict[int, dict],
  is_start: str,
  is_end: str,
  oos_start: str | None,
  oos_end: str | None,
) -> list[dict]:
  """Compute per-asset IS + OOS metrics for breakdown table.

  Returns list of dicts with symbol, side, timeframe, group, and
  IS/OOS WR, PF, PnL, trade counts — one per selected asset.
  """
  result: list[dict] = []
  for key in selected_assets:
    parts = key.split("_", 2)
    bid = int(parts[0]) if parts[0].isdigit() else 0
    symbol = parts[1] if len(parts) > 1 else key
    side = parts[2] if len(parts) > 2 else "Long"

    daily = asset_daily.get(key)
    if daily is None or daily.empty:
      continue

    binfo = batch_info.get(bid, {})
    tf = binfo.get("timeframe", "?")
    group = binfo.get("group") or binfo.get("name", f"B{bid}")

    # IS metrics
    is_daily = _slice_pnl(daily, is_start, is_end)
    is_trades = _count_trades_in_range(asset_trade_dates.get(key, []), is_start, is_end)
    is_m = _quick_asset_metrics(is_daily, is_trades)

    # OOS metrics (if available)
    oos_m: dict = {}
    oos_trades = 0
    if oos_start and oos_end:
      oos_daily = _slice_pnl(daily, oos_start, oos_end)
      oos_trades = _count_trades_in_range(asset_trade_dates.get(key, []), oos_start, oos_end)
      oos_m = _quick_asset_metrics(oos_daily, oos_trades)

    result.append({
      "key": key,
      "batch_id": bid,
      "symbol": symbol,
      "side": side,
      "timeframe": tf,
      "group": group,
      "is_wr": is_m.get("win_rate", 0),
      "is_pf": is_m.get("profit_factor", 0),
      "is_pnl": is_m.get("net_profit", 0),
      "is_trades": is_trades,
      "oos_wr": oos_m.get("win_rate", 0),
      "oos_pf": oos_m.get("profit_factor", 0),
      "oos_pnl": oos_m.get("net_profit", 0),
      "oos_trades": oos_trades,
    })
  return result


def _compute_concurrent_trades(
  selected_assets: list[str],
  asset_intervals: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]],
  start: str,
  end: str,
) -> dict:
  """Compute max and avg concurrent open trades for selected assets in a date range.

  Uses entry/exit timestamp intervals. Returns {max_concurrent, avg_concurrent}.
  """
  ts_start = pd.Timestamp(start)
  ts_end = pd.Timestamp(end)

  # Collect all entry/exit events within the date range
  events: list[tuple[pd.Timestamp, int]] = []
  for key in selected_assets:
    for entry, exit_t in asset_intervals.get(key, []):
      if entry >= ts_start and entry < ts_end:
        events.append((entry, 1))    # +1 on entry
        events.append((exit_t, -1))  # -1 on exit

  if not events:
    return {"max_concurrent": 0, "avg_concurrent": 0.0}

  # Sort by time; entries before exits at same timestamp (captures peak overlap)
  events.sort(key=lambda e: (e[0], -e[1]))

  current = 0
  peak = 0
  # For weighted average: track time spent at each concurrency level
  weighted_sum = 0.0
  prev_time = events[0][0]
  for ts, delta in events:
    if ts > prev_time:
      dt = (ts - prev_time).total_seconds()
      weighted_sum += current * dt
    current += delta
    peak = max(peak, current)
    prev_time = ts

  total_seconds = (events[-1][0] - events[0][0]).total_seconds()
  avg = round(weighted_sum / total_seconds, 1) if total_seconds > 0 else 0.0

  return {"max_concurrent": peak, "avg_concurrent": avg}


def _quick_asset_metrics(daily: pd.Series, trade_count: int) -> dict:
  """Lightweight metrics for a single asset (WR, PF, PnL). Skips heavy KPIs."""
  if daily.empty or trade_count == 0:
    return {"win_rate": 0, "profit_factor": 0, "net_profit": 0}
  net = float(daily.sum())
  gp = float(daily[daily > 0].sum())
  gl = float(daily[daily < 0].sum())
  pf = min(gp / abs(gl), 10.0) if gl != 0 else (10.0 if gp > 0 else 0.0)
  active = daily[daily != 0]
  wins = int((active > 0).sum())
  wr = wins / len(active) if len(active) > 0 else 0.0
  return {"win_rate": round(wr, 4), "profit_factor": round(pf, 2), "net_profit": round(net, 2)}


def _compute_full_analysis(
  selected_assets: list[str],
  asset_daily: dict[str, pd.Series],
  asset_trade_dates: dict[str, list[pd.Timestamp]],
  start: str,
  end: str,
) -> tuple[dict, list[dict], dict]:
  """Compute metrics + equity curve + monthly matrix for a period.

  Returns (metrics_dict, equity_curve, monthly_matrix).
  """
  combined, total_trades = _combine_asset_pnl(
    selected_assets, asset_daily, asset_trade_dates, start, end,
  )
  metrics = _rich_metrics_from_daily(combined, total_trades)
  equity_curve = _build_equity_curve(combined)
  monthly_matrix = _build_monthly_matrix(combined)
  return metrics, equity_curve, monthly_matrix


def _compute_oos_metrics(
  selected_assets: list[str],
  asset_daily: dict[str, pd.Series],
  asset_trade_dates: dict[str, list[pd.Timestamp]],
  oos_start: str,
  oos_end: str,
) -> dict:
  """Compute OOS evaluation metrics for selected assets.

  NEVER used for selection decisions — reporting only.
  Returns full suite of KPIs (Sortino, Sharpe, Calmar, Recovery, etc.).
  """
  combined, total_trades = _combine_asset_pnl(
    selected_assets, asset_daily, asset_trade_dates, oos_start, oos_end,
  )
  return _rich_metrics_from_daily(combined, total_trades)


def _compute_is_metrics(
  selected_assets: list[str],
  asset_daily: dict[str, pd.Series],
  asset_trade_dates: dict[str, list[pd.Timestamp]],
  is_start: str,
  is_end: str,
) -> dict:
  """Compute IS metrics for the selected portfolio (for track display).

  Returns full suite of KPIs (same as OOS for consistent comparison).
  """
  combined, total_trades = _combine_asset_pnl(
    selected_assets, asset_daily, asset_trade_dates, is_start, is_end,
  )
  return _rich_metrics_from_daily(combined, total_trades)


# ── Regime-Conditional helpers ─────────────────────────────────

def _regime_features(pnl: pd.Series) -> np.ndarray:
  """Compute 3-element regime vector: [volatility, trend, autocorrelation]."""
  if len(pnl) < 5:
    return np.zeros(3)
  vals = pnl.values.astype(float)
  vol = float(np.std(vals)) if len(vals) > 1 else 0.0
  # Trend: slope of linear regression on equity curve
  eq = np.cumsum(vals)
  x = np.arange(len(eq), dtype=float)
  if len(eq) > 1 and np.std(eq) > 0:
    trend = float(np.polyfit(x, eq, 1)[0])
  else:
    trend = 0.0
  # Lag-1 autocorrelation
  if len(vals) > 2:
    ac = float(np.corrcoef(vals[:-1], vals[1:])[0, 1])
    if np.isnan(ac):
      ac = 0.0
  else:
    ac = 0.0
  return np.array([vol, trend, ac])


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
  """Cosine similarity between two vectors."""
  na = np.linalg.norm(a)
  nb = np.linalg.norm(b)
  if na < 1e-12 or nb < 1e-12:
    return 0.0
  return float(np.dot(a, b) / (na * nb))


# ── ELO Tournament helpers ────────────────────────────────────

def _elo_tournament(
  batch_pnl: dict[int, pd.Series],
  is_start: str,
  is_end: str,
  iterations: int = 5,
) -> dict[int, float]:
  """Run round-robin ELO tournament on IS PnL series.

  Each pair compares cosine similarity of cumulative PnL
  to an ideal (linear) equity curve on a random 70% subsample.
  """
  bids = list(batch_pnl.keys())
  if len(bids) < 2:
    return {b: 1500.0 for b in bids}

  # Prepare sliced PnL
  sliced: dict[int, np.ndarray] = {}
  for bid in bids:
    pnl = batch_pnl.get(bid)
    if pnl is None:
      continue
    sl = _slice_pnl(pnl, is_start, is_end)
    if len(sl) >= 10:
      sliced[bid] = sl.values.astype(float)
  bids = list(sliced.keys())
  if len(bids) < 2:
    return {b: 1500.0 for b in bids}

  elo: dict[int, float] = {b: 1500.0 for b in bids}
  rng = np.random.RandomState(42)
  k_factor = 32.0

  for _ in range(iterations):
    for i, a in enumerate(bids):
      for b in bids[i + 1:]:
        va = sliced[a]
        vb = sliced[b]
        n = min(len(va), len(vb))
        mask = rng.random(n) < 0.7
        if mask.sum() < 5:
          continue
        sa = np.cumsum(va[:n][mask])
        sb = np.cumsum(vb[:n][mask])
        ideal = np.linspace(0, max(np.median([sa[-1], sb[-1]]), 1.0), len(sa))
        # Cosine similarity to ideal
        cos_a = _cosine_sim(sa, ideal)
        cos_b = _cosine_sim(sb, ideal)
        # ELO update
        ea = 1.0 / (1.0 + 10.0 ** ((elo[b] - elo[a]) / 400.0))
        eb = 1.0 - ea
        if cos_a > cos_b:
          elo[a] += k_factor * (1 - ea)
          elo[b] += k_factor * (0 - eb)
        else:
          elo[a] += k_factor * (0 - ea)
          elo[b] += k_factor * (1 - eb)

  return elo


# ── Main engine ───────────────────────────────────────────────

def _progressive_prune(
  batch_daily: dict[int, pd.Series],
  batch_sides: dict[int, str],
  windows: list[dict],
  config: BatchSelectionConfig,
) -> set[int]:
  """Progressive Pruning: pre-filter batch pool by historical quality.

  Pass 1: For each window, score all batches on IS and track which appear in
  top-N candidates and their rank positions.
  Pass 2: Remove the worst prune_long LONGs and prune_short SHORTs by
  appearance count (tiebreak by average rank, higher=worse).

  Returns the set of batch_ids to KEEP (after removal).

  !!! LOOK-AHEAD WARNING (Tier 1.2 audit B01) !!!

  Static pruning uses all windows including future IS periods. Any OOS
  window's input pool has been filtered using information from later IS
  windows — this is textbook look-ahead bias. A backward-only rolling
  prune (per-window exclusion using only IS ≤ current IS_start) is the
  only leak-free variant; that rewrite requires a sandbox review.

  Callers MUST surface `pruning_look_ahead_warning=True` on any summary
  produced with pruning_enabled=True. OOS numbers under this mode are
  research-only and NOT safe for live trading.
  """
  # Count appearances in top-N and track avg rank per batch
  appearance_count: dict[int, int] = defaultdict(int)
  rank_sum: dict[int, float] = defaultdict(float)

  for window in windows:
    is_start = window["is_start"]
    is_end = window["is_end"]

    scored: list[tuple[int, float]] = []
    for bid, pnl in batch_daily.items():
      sl = _slice_pnl(pnl, is_start, is_end)
      if _nonzero_days(sl) < config.min_is_days:
        continue
      net = float(sl.sum())
      if net <= 0:
        continue
      sc = score_metric(sl, config.batch_rank_metric)
      if sc == -999:
        continue
      scored.append((bid, sc))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_n = scored[:config.n_candidates]

    for rank_idx, (bid, _) in enumerate(top_n):
      appearance_count[bid] += 1
      rank_sum[bid] += rank_idx + 1  # 1-indexed rank

  # Compute avg rank (lower = better)
  avg_rank: dict[int, float] = {}
  for bid in appearance_count:
    avg_rank[bid] = rank_sum[bid] / appearance_count[bid]

  # Split by side
  long_bids = [bid for bid in batch_daily if batch_sides.get(bid) == "Long"]
  short_bids = [bid for bid in batch_daily if batch_sides.get(bid) == "Short"]

  # Sort by quality: fewer appearances = worse, higher avg rank = worse
  def quality_key(bid):
    return (appearance_count.get(bid, 0), -avg_rank.get(bid, 999))

  long_bids.sort(key=quality_key)
  short_bids.sort(key=quality_key)

  # Remove worst N from each side
  remove = set()
  if config.prune_long > 0:
    remove.update(long_bids[:config.prune_long])
  if config.prune_short > 0:
    remove.update(short_bids[:config.prune_short])

  keep = set(batch_daily.keys()) - remove
  log.info(
    "Progressive pruning: removed %d LONG + %d SHORT = %d total, keeping %d",
    min(config.prune_long, len(long_bids)),
    min(config.prune_short, len(short_bids)),
    len(remove), len(keep),
  )
  return keep


# ═══════════════════════════════════════════════════════════════════
#  Overfit Guard — multiplier computation for Step 3
# ═══════════════════════════════════════════════════════════════════

def _compute_overfit_guard_multipliers(
  selected_batches: list[int],
  batch_to_assets: dict[int, list[str]],
  asset_daily: dict[str, pd.Series],
  is_start: str,
  is_end: str,
  config: BatchSelectionConfig,
) -> dict[str, float]:
  """Compute per-asset multipliers for the overfit guard.

  Applies guard mechanisms from overfit_guard.py as multipliers [0.3, 1.2]
  on asset stability scores. Catches suspiciously extreme IS metrics.

  The guard computes per-asset metrics (pf, sortino, wr, etc.), applies
  transforms (log compression, z-score penalty, etc.), and converts
  post-transform rank divergence into score multipliers.
  """
  from backend.analysis.overfit_guard import (
    apply_log_diminish, apply_z_score_penalty,
    apply_bayesian_shrinkage, apply_sigmoid_cap,
    apply_normal_range_bonus, GUARD_METRICS,
  )

  # Build per-asset metrics for all assets in selected batches
  all_keys: list[str] = []
  asset_to_batch: dict[str, int] = {}
  for bid in selected_batches:
    for k in batch_to_assets.get(bid, []):
      all_keys.append(k)
      asset_to_batch[k] = bid

  asset_metrics: dict[str, dict] = {}
  for key in all_keys:
    pnl = asset_daily.get(key)
    if pnl is None:
      continue
    sl = _slice_pnl(pnl, is_start, is_end)
    nz = sl[sl != 0]
    if len(nz) < 5:
      continue
    pos = nz[nz > 0]
    neg = nz[nz < 0]
    gw = float(pos.sum()) if len(pos) > 0 else 0.0
    gl = abs(float(neg.sum())) if len(neg) > 0 else 0.001
    cum = sl.cumsum()
    dd = cum - cum.cummax()
    max_dd = abs(float(dd.min())) if len(dd) > 0 else 0.0
    nt = len(nz)
    wr = len(pos) / nt * 100 if nt > 0 else 0.0
    aw = float(pos.mean()) if len(pos) > 0 else 0.0
    al = abs(float(neg.mean())) if len(neg) > 0 else 0.001
    pf = min(gw / max(gl, 0.001), 10.0)
    std = float(nz.std()) if len(nz) > 1 else 1.0
    dds = float(neg.std()) if len(neg) > 1 else 1.0
    if dds <= 0:
      dds = 1.0
    if std <= 0:
      std = 1.0
    asset_metrics[key] = {
      "pf": pf,
      "sortino": float(nz.mean() / dds),
      "win_rate": wr,
      "max_dd": max_dd,
      "net_profit": float(sl.sum()),
      # Cap payoff at 10.0 (same convention as pf on L1403). Without a cap,
      # a zero-loss asset produces `aw / 0.001` = 1000×avg_win, which the
      # overfit-guard z-penalty then flags as the biggest outlier — but the
      # outlier is driven by the 0.001 floor, not real overfitting.
      "payoff": min(aw / max(al, 0.001), 10.0),
      "sharpe": float(nz.mean() / std),
      "calmar": float(cum.iloc[-1] / max(max_dd, 0.001)) if len(cum) > 0 else 0.0,
      "n_trades": nt,
      "batch_id": asset_to_batch.get(key, 0),
    }

  if not asset_metrics:
    return {}

  # Apply guard transforms based on mode.
  # CS-018: asset_metrics is a dict[str, dict[str, float|int]] with no
  # nested containers, so a two-level dict comprehension is equivalent to
  # deepcopy and roughly 30-50x faster on 500+ asset pools.
  am = {k: dict(v) for k, v in asset_metrics.items()}
  tags = list(am.keys())
  multipliers: dict[str, float] = {t: 1.0 for t in tags}
  mode = config.overfit_guard_mode

  use_log = mode in ("log", "log_zscore", "full")
  use_zscore = mode in ("zscore", "log_zscore", "full")
  use_bayes = mode in ("bayes", "full")
  use_sigmoid = mode == "full"
  use_nr = mode == "full"

  if use_log:
    caps = {
      "pf": config.og_log_cap_pf,
      "sortino": config.og_log_cap_sortino,
      "payoff": config.og_log_cap_payoff,
      "sharpe": config.og_log_cap_sharpe,
      "calmar": config.og_log_cap_calmar,
      "wr": config.og_log_cap_wr,
    }
    am = apply_log_diminish(am, caps)

  if use_zscore:
    zm = apply_z_score_penalty(
      am,
      z_threshold=config.og_z_threshold,
      penalty_strength=config.og_z_penalty_strength,
    )
    for t in tags:
      multipliers[t] *= zm.get(t, 1.0)

  if use_bayes:
    am = apply_bayesian_shrinkage(am, k=config.og_bayes_k)

  if use_sigmoid:
    sig_caps = {
      "pf": config.og_log_cap_pf,
      "sortino": config.og_log_cap_sortino,
      "payoff": config.og_log_cap_payoff,
      "sharpe": config.og_log_cap_sharpe,
      "calmar": config.og_log_cap_calmar,
      "wr": config.og_log_cap_wr,
    }
    am = apply_sigmoid_cap(am, sig_caps)

  if use_nr:
    nm = apply_normal_range_bonus(am)
    for t in tags:
      multipliers[t] *= nm.get(t, 1.0)

  # Rank-divergence penalty from transforms (log/bayes/sigmoid)
  if use_log or use_bayes or use_sigmoid:
    for metric in GUARD_METRICS:
      orig_vals = [(t, asset_metrics[t].get(metric, 0)) for t in tags if t in asset_metrics]
      trans_vals = [(t, am[t].get(metric, 0)) for t in tags if t in am]
      if len(orig_vals) < 3:
        continue
      orig_vals.sort(key=lambda x: x[1], reverse=True)
      trans_vals.sort(key=lambda x: x[1], reverse=True)
      orig_rank = {t: r for r, (t, _) in enumerate(orig_vals)}
      trans_rank = {t: r for r, (t, _) in enumerate(trans_vals)}
      for t in tags:
        if t in orig_rank and t in trans_rank:
          rank_drop = trans_rank[t] - orig_rank[t]
          if rank_drop > 2:
            multipliers[t] *= max(0.5, 1.0 - 0.02 * rank_drop)
          elif rank_drop < -2:
            multipliers[t] *= min(1.15, 1.0 + 0.01 * abs(rank_drop))

  # Clamp final multipliers
  for t in tags:
    multipliers[t] = max(0.3, min(1.2, multipliers[t]))

  return multipliers


def _run_single_batch_selection(
  trades: list[BacktestTrade],
  rbm: dict[int, int],
  batch_ids: list[int],
  batch_daily: dict[int, pd.Series],
  asset_daily: dict[str, pd.Series],
  asset_to_batch: dict[str, int],
  batch_to_assets: dict[int, list[str]],
  asset_trade_dates: dict[str, list[pd.Timestamp]],
  asset_trade_intervals: dict = None,
  exec_batch_daily: dict[int, pd.Series] = None,
  exec_asset_daily: dict[str, pd.Series] = None,
  exec_asset_trade_dates: dict[str, list[pd.Timestamp]] = None,
  exec_asset_trade_intervals: dict = None,
  has_exec_filter: bool = False,
  batch_sides: dict[int, str] = None,
  asset_wr_data: dict[str, pd.DataFrame] | None = None,
  windows: list[dict] = None,
  config: BatchSelectionConfig = None,
  progress_callback: callable | None = None,
  batch_info: dict[int, dict] | None = None,
  db=None,  # Optional SQLAlchemy Session — used ONLY when config.category_filter_enabled=True
  straddle_table: dict[str, np.ndarray] | None = None,
) -> list[dict]:
  """Run the 3-step pipeline for a SINGLE set of windows. Returns window_results list.

  Steps 1-3 (selection) read decision-time views of the daily series: trades
  still open when the window's picks are made are removed (see
  `_known_at_decision`). Reporting (IS/OOS metrics) uses the full series.
  """

  window_results: list[dict] = []
  total_windows = len([w for w in windows if w.get("oos_start") and w.get("oos_end")])

  for i, window in enumerate(windows):
    is_start = window["is_start"]
    is_end = window["is_end"]
    oos_start = window.get("oos_start")
    oos_end = window.get("oos_end")

    if not oos_start or not oos_end:
      continue

    # ── Progress reporting ──
    if progress_callback:
      progress_callback(
        step=i + 1, total=total_windows,
        label=f"Window {i + 1}/{total_windows} — OOS {oos_start} to {oos_end}",
      )

    log.info(
      "Window %d: IS [%s, %s], OOS [%s, %s]",
      window["window_num"], is_start, is_end, oos_start, oos_end,
    )

    # Selection happens at the end of the day before OOS starts. Steps 1-3
    # only see trades that had closed by then (O1, 2026-10-08).
    decision_day = pd.Timestamp(oos_start) - pd.Timedelta(days=1)
    sel_batch_daily, sel_asset_daily, sel_wr_data = _known_at_decision(
      batch_daily, asset_daily, asset_wr_data, straddle_table, decision_day,
    )

    # ════════════ STEP 1: IS SCREENING ════════════
    is_ranked: list[tuple[int, float]] = []

    for bid in batch_ids:
      pnl = sel_batch_daily.get(bid)
      if pnl is None:
        continue
      sl = _slice_pnl(pnl, is_start, is_end)
      if _nonzero_days(sl) < config.min_is_days:
        continue
      net = float(sl.sum())
      if net <= 0:
        continue
      pf = _compute_pf(sl)
      if pf < config.min_pf:
        continue

      # Recency Momentum
      if config.recency_weight > 0 and len(sl) > 1:
        idx_pos = np.arange(len(sl), dtype=float) / max(len(sl) - 1, 1)
        weights = 1.0 + (config.recency_weight - 1.0) * idx_pos
        weighted_pnl = pd.Series(sl.values * weights, index=sl.index)
        sc = score_metric(weighted_pnl, config.batch_rank_metric)
      else:
        sc = score_metric(sl, config.batch_rank_metric)

      if sc == -999:
        continue
      is_ranked.append((bid, sc))

    # Regime-Conditional
    if config.regime_weight > 0 and len(is_ranked) >= 2:
      agg_pnl = pd.Series(dtype=float)
      for bid, _ in is_ranked:
        pnl = sel_batch_daily.get(bid)
        if pnl is not None:
          sl = _slice_pnl(pnl, is_start, is_end)
          agg_pnl = agg_pnl.add(sl, fill_value=0.0) if not agg_pnl.empty else sl.copy()
      agg_regime = _regime_features(agg_pnl)

      adjusted = []
      for bid, sc in is_ranked:
        pnl = sel_batch_daily.get(bid)
        if pnl is None:
          adjusted.append((bid, sc))
          continue
        sl = _slice_pnl(pnl, is_start, is_end)
        batch_regime = _regime_features(sl)
        sim = _cosine_sim(agg_regime, batch_regime)
        multiplier = 1.0 + config.regime_weight * max(sim, 0.0)
        adjusted.append((bid, sc * multiplier))
      is_ranked = adjusted

    # ELO Tournament
    if config.elo_iterations > 0:
      elo_ratings = _elo_tournament(sel_batch_daily, is_start, is_end, config.elo_iterations)
      passing_bids = {bid for bid, _ in is_ranked}
      is_ranked = [
        (bid, elo_ratings.get(bid, 1500.0))
        for bid in passing_bids if bid in elo_ratings
      ]

    is_ranked.sort(key=lambda x: x[1], reverse=True)
    candidates = is_ranked[:config.n_candidates]

    log.info(
      "  Step 1: %d batches passed IS screening, top %d candidates",
      len(is_ranked), len(candidates),
    )

    # ════════════ STEP 2: CALIBRATION GATE ════════════
    effective_mb = config.max_batches

    if config.adaptive_mb and len(candidates) >= 2:
      vals = [sc for _, sc in candidates]
      mb_min = config.adaptive_mb_min
      mb_max = min(config.adaptive_mb_max, len(candidates))
      if mb_max > mb_min:
        gaps = []
        v_range = vals[0] - vals[min(mb_max, len(vals) - 1)] if len(vals) > mb_max else vals[0] - vals[-1]
        for pos in range(mb_min - 1, min(mb_max, len(vals) - 1)):
          norm_gap = (vals[pos] - vals[pos + 1]) / v_range if v_range > 0 else 0
          gaps.append((pos + 1, norm_gap))
        if gaps:
          best_cut, best_gap = max(gaps, key=lambda x: x[1])
          threshold = float(np.percentile([g[1] for g in gaps], config.adaptive_mb_gap_pct)) if len(gaps) > 1 else 0
          effective_mb = best_cut if best_gap >= threshold else mb_max
        else:
          effective_mb = mb_min
      else:
        effective_mb = mb_min

    if i == 0 or len(windows) < 2:
      # First window: cold start — use IS ranking
      if config.l2_groups:
        selected_batches = _l2_select(candidates, batch_sides, effective_mb, mode=config.l2_mode)
      else:
        selected_batches = [bid for bid, _ in candidates[:effective_mb]]
      cal_results = {"mode": "cold_start", "passed": len(selected_batches)}
    else:
      prev_window = windows[i - 1]
      prev_oos_start = prev_window.get("oos_start", "")
      prev_oos_end = prev_window.get("oos_end", "")

      # Ensemble CAL Gate
      if config.ensemble_cal_k >= 2:
        ensemble_metrics = ("ulcer", "recovery", "sortino", "pf", "tail_ratio")
        ens_data: list[dict] = []

        for bid, is_score in candidates:
          pnl = sel_batch_daily.get(bid)
          if pnl is None:
            continue
          sl = _slice_pnl(pnl, prev_oos_start, prev_oos_end)
          net = float(sl.sum())
          if config.cal_must_profit and net <= 0:
            continue
          row = {"bid": bid, "is_score": is_score}
          valid = True
          for m in ensemble_metrics:
            sc = score_metric(sl, m)
            if sc == -999:
              valid = False
              break
            row[m] = sc
          if valid:
            ens_data.append(row)

        if ens_data:
          edf = pd.DataFrame(ens_data)
          n_ens = len(edf)
          top_half = max(n_ens // 2, 1)
          passes = pd.DataFrame(index=edf.index)
          for m in ensemble_metrics:
            passes[m] = edf[m].rank(ascending=False) <= top_half
          edf["cal_passes"] = passes.sum(axis=1)

          qualified = edf[edf["cal_passes"] >= config.ensemble_cal_k]
          if len(qualified) == 0:
            qualified = edf[edf["cal_passes"] >= max(config.ensemble_cal_k - 1, 1)]
          if len(qualified) == 0:
            qualified = edf

          qualified = qualified.nlargest(effective_mb, "is_score")
          cal_pool = list(zip(qualified["bid"].tolist(), qualified["is_score"].tolist()))
          if config.l2_groups:
            selected_batches = _l2_select(cal_pool, batch_sides, effective_mb, mode=config.l2_mode)
          else:
            selected_batches = qualified["bid"].tolist()
          cal_results = {"mode": "ensemble_cal", "k": config.ensemble_cal_k, "pool_size": n_ens, "passed": len(selected_batches)}
        else:
          if config.l2_groups:
            selected_batches = _l2_select(candidates, batch_sides, effective_mb, mode=config.l2_mode)
          else:
            selected_batches = [bid for bid, _ in candidates[:effective_mb]]
          cal_results = {"mode": "ensemble_cal_fallback", "passed": len(selected_batches)}

      else:
        # Standard single-metric CAL
        cal_ranked: list[tuple[int, float]] = []
        cal_rejected: list[int] = []

        for bid, is_score in candidates:
          pnl = sel_batch_daily.get(bid)
          if pnl is None:
            cal_rejected.append(bid)
            continue
          sl = _slice_pnl(pnl, prev_oos_start, prev_oos_end)
          net = float(sl.sum())
          if config.cal_must_profit and net <= 0:
            cal_rejected.append(bid)
            continue
          cal_sc = score_metric(sl, config.cal_rank_metric)
          if cal_sc == -999:
            cal_rejected.append(bid)
            continue
          cal_ranked.append((bid, cal_sc))

        cal_ranked.sort(key=lambda x: x[1], reverse=True)

        # L2 Groups: select from both sides independently
        if config.l2_groups:
          selected_batches = _l2_select(cal_ranked, batch_sides, effective_mb, mode=config.l2_mode)
        else:
          selected_batches = [bid for bid, _ in cal_ranked[:effective_mb]]

        # Fallback: fill to effective_mb from IS-ranked if < min_calibrated
        if len(selected_batches) < config.min_calibrated:
          selected_set = set(selected_batches)
          for bid, _ in candidates:
            if bid not in selected_set:
              selected_batches.append(bid)
              selected_set.add(bid)
              if len(selected_batches) >= effective_mb:
                break

        cal_results = {
          "mode": "calibrated",
          "passed": len(cal_ranked),
          "rejected": len(cal_rejected),
          "l2_groups": config.l2_groups,
        }

    log.info("  Step 2: selected %d batches %s (effective_mb=%d, l2=%s, l2_mode=%s)",
             len(selected_batches), selected_batches, effective_mb,
             config.l2_groups, config.l2_mode if config.l2_groups else "n/a")

    # ════════════ STEP 3: ASSET PICKING ════════════
    selected_assets: list[str] = []
    estimated_tpm = 0.0
    is_mo = _is_months_count(is_start, is_end)
    asset_details: list[dict] = []

    # Past OOS Confirmation: get previous OOS profitable assets
    prev_oos_profitable: set[str] | None = None
    if config.past_oos_confirm and i > 0:
      prev_w = windows[i - 1]
      p_oos_start = prev_w.get("oos_start", "")
      p_oos_end = prev_w.get("oos_end", "")
      if p_oos_start and p_oos_end:
        prev_oos_profitable = set()
        for key, pnl in sel_asset_daily.items():
          sl = _slice_pnl(pnl, p_oos_start, p_oos_end)
          if float(sl.sum()) > 0:
            prev_oos_profitable.add(key)
        log.info("    Past OOS confirm: %d assets profitable in prev OOS", len(prev_oos_profitable))

    # ── Overfit Guard: compute multipliers ONCE for all selected batches ──
    og_multipliers: dict[str, float] = {}
    if config.overfit_guard_enabled:
      og_multipliers = _compute_overfit_guard_multipliers(
        selected_batches, batch_to_assets, sel_asset_daily,
        is_start, is_end, config,
      )
      log.info("  Step 3 OG: guard=%s, %d assets with multipliers",
               config.overfit_guard_mode, len(og_multipliers))

    for bid in selected_batches:
      b_assets = batch_to_assets.get(bid, [])
      asset_scored: list[tuple[str, float]] = []

      for key in b_assets:
        pnl = sel_asset_daily.get(key)
        if pnl is None:
          continue
        sl = _slice_pnl(pnl, is_start, is_end)
        if _nonzero_days(sl) < config.min_asset_days:
          continue
        net = float(sl.sum())
        if config.asset_must_profit and net <= 0:
          continue
        # Past OOS confirmation gate
        if prev_oos_profitable is not None and key not in prev_oos_profitable:
          continue
        # Asset WR gate
        if config.asset_wr_gate > 0:
          n_trades, wr = _compute_asset_wr_in_range(
            trades, rbm, key, is_start, is_end,
            asset_wr_data=sel_wr_data,
          )
          if n_trades < 5 or wr < config.asset_wr_gate:
            continue
        sc = score_metric(sl, config.asset_rank_metric)
        if sc == -999:
          continue
        # Apply overfit guard multiplier
        if config.overfit_guard_enabled and key in og_multipliers:
          sc *= og_multipliers[key]
        asset_scored.append((key, sc))

      asset_scored.sort(key=lambda x: x[1], reverse=True)

      picked = 0
      for key, sc in asset_scored:
        if picked >= config.assets_per_batch:
          break
        trade_count = _count_trades_in_range(
          asset_trade_dates.get(key, []), is_start, is_end,
        )
        tpm_est = trade_count / is_mo
        if estimated_tpm + tpm_est > config.max_tpm and len(selected_assets) > 0:
          break

        selected_assets.append(key)
        estimated_tpm += tpm_est
        picked += 1
        parts = key.split("_", 2)
        asset_details.append({
          "key": key,
          "batch_id": bid,
          "symbol": parts[1] if len(parts) > 1 else key,
          "side": parts[2] if len(parts) > 2 else "Long",
          "is_score": round(sc, 4),
          "tpm_est": round(tpm_est, 1),
        })

    log.info(
      "  Step 3: picked %d assets (est TPM: %.1f, past_oos=%s)",
      len(selected_assets), estimated_tpm, config.past_oos_confirm,
    )

    # ════════════ STEP 3b: CATEGORY FILTER (optional, portable from hybrid) ═
    category_promotion_audit: dict | None = None
    if (
      config.category_filter_enabled
      and str(config.category_influence).lower() != "none"
      and len(selected_assets) > 0
    ):
      try:
        # Lazy import to avoid widening import surface for non-users.
        from backend.analysis.event_wfo import (
          _compute_category_scores as _cat_compute_scores,
          _compute_category_scores_streak_event as _cat_compute_scores_streak,
          _category_tier_lookup as _cat_tier_lookup,
          _category_tier_lookup_event as _cat_tier_lookup_event,
          _apply_category_filter as _cat_apply_filter,
          _symbol_from_scoring_key as _cat_symbol_from_key,
        )
        from backend.services.category_service import (
          get_categories_for_symbols as _cat_get_categories,
        )

        # Build per-asset IS buffers (trade PnLs + timestamps within IS window).
        _asset_buffers: dict[str, list[float]] = defaultdict(list)
        _asset_trade_times: dict[str, list[datetime]] = defaultdict(list)
        _sel_keys = set(selected_assets)
        _is_start_ts = pd.Timestamp(is_start)
        _is_end_ts = pd.Timestamp(is_end)
        _decision_end_ts = decision_day + pd.Timedelta(days=1)
        _sel_batch_set = set(selected_batches)
        for t in trades:
          bid = rbm.get(t.run_id)
          if bid not in _sel_batch_set:
            continue
          if t.entry_time is None:
            continue
          et = t.entry_time if isinstance(t.entry_time, pd.Timestamp) else pd.Timestamp(t.entry_time)
          if et < _is_start_ts or et >= _is_end_ts:
            continue
          # Still open at the decision → its result was not known yet (O1)
          if t.exit_time is not None and pd.Timestamp(t.exit_time) >= _decision_end_ts:
            continue
          _bi_row = (batch_info or {}).get(bid, {})
          _tf = _bi_row.get("timeframe", "?")
          _side = (t.side or "Long").capitalize()
          _key = f"{_tf}_{t.symbol.upper()}_{_side}"
          if _key not in _sel_keys:
            # Fall back to any selected key matching symbol+side (ignore tf variants).
            _matches = [
              k for k in _sel_keys
              if k.endswith(f"_{t.symbol.upper()}_{_side}")
            ]
            if not _matches:
              continue
            _key = _matches[0]
          _asset_buffers[_key].append(float(t.net_profit))
          _asset_trade_times[_key].append(et)

        _syms = sorted({_cat_symbol_from_key(k).upper() for k in _sel_keys})
        _categories_map = _cat_get_categories(_syms, db) if db is not None else {}

        # Default composite weights — mirror the hybrid JSON convention.
        _composite_weights = config.category_composite_weights or {
          "sortino": 0.20, "pf": 0.45, "recovery": 0.20, "wr": 0.15,
        }

        _mode = str(getattr(config, "category_mode", "tertile") or "tertile").lower()
        if _mode == "streak_event":
          _cat_scores = _cat_compute_scores_streak(
            _asset_buffers,
            _asset_trade_times,
            _categories_map,
            lookback=int(config.category_lookback_trades or 0),
            streak_required=int(getattr(config, "category_streak_required", 5) or 5),
            fire_duration_trades=int(getattr(config, "category_fire_duration_trades", 10) or 10),
          )
          _cat_tiers = _cat_tier_lookup_event(_cat_scores)
          _influence = "event"
        else:
          _cat_scores = _cat_compute_scores(
            _asset_buffers,
            _asset_trade_times,
            _categories_map,
            lookback=int(config.category_lookback_trades or 0),
            score_metric_name=str(config.category_score_metric or "composite"),
            composite_weights=_composite_weights,
          )
          _cat_tiers = _cat_tier_lookup(_cat_scores)
          _influence = str(config.category_influence).lower()

        _is_score_by_key = {ad["key"]: float(ad.get("is_score") or 0.0) for ad in asset_details}
        _ranked_for_filter: list[tuple[str, float, dict]] = [
          (_k, _is_score_by_key.get(_k, 0.0), {}) for _k in selected_assets
        ]
        _filtered = _cat_apply_filter(
          _ranked_for_filter,
          _categories_map,
          _cat_tiers,
          influence=_influence,
          max_per_cat=int(config.category_max_per_cat or 0),
          fire_multiplier=float(getattr(config, "category_fire_multiplier", 1.30) or 1.30),
        )
        _survivors = [k for k, _, _ in _filtered]
        _survivor_set = set(_survivors)

        if len(_survivor_set) < len(selected_assets):
          _dropped = [k for k in selected_assets if k not in _survivor_set]
          selected_assets = _survivors
          asset_details = [ad for ad in asset_details if ad["key"] in _survivor_set]
          estimated_tpm = sum(
            _count_trades_in_range(
              asset_trade_dates.get(k, []), is_start, is_end,
            ) / is_mo
            for k in selected_assets
          )
          log.info(
            "  Step 3b: category filter (%s, max_per_cat=%d) kept %d/%d assets (tpm→%.1f)",
            config.category_influence,
            int(config.category_max_per_cat or 0),
            len(selected_assets),
            len(selected_assets) + len(_dropped),
            estimated_tpm,
          )
        else:
          _dropped = []
          log.info(
            "  Step 3b: category filter (%s) applied, 0 assets dropped (re-ranked only)",
            config.category_influence,
          )

        category_promotion_audit = {
          "influence": str(config.category_influence).lower(),
          "lookback_trades": int(config.category_lookback_trades or 0),
          "score_metric": str(config.category_score_metric or "composite"),
          "max_per_cat": int(config.category_max_per_cat or 0),
          "refresh_mode": str(config.category_refresh_mode or "per_window"),
          "cat_scores": {k: round(v, 6) for k, v in _cat_scores.items()},
          "cat_tiers": dict(_cat_tiers),
          "n_survivors": len(selected_assets),
          "n_dropped": len(_dropped),
          "dropped_keys": _dropped,
        }
      except (KeyError, ValueError, TypeError, AttributeError, RuntimeError) as _cat_err:
        # Narrowed from bare `Exception`: only soft data/metadata issues are
        # swallowed so the category filter falls back to a transparent
        # passthrough. Real programming errors (ImportError, NameError,
        # AssertionError) now propagate so they surface loudly instead of
        # being hidden behind a WARNING + audit entry.
        log.warning("  Step 3b: category filter errored — passthrough. %s", _cat_err)
        category_promotion_audit = {"error": str(_cat_err), "passthrough": True}

    # ════════════ STEP 4: OOS EVALUATION (reporting only) ════════════
    oos_metrics, oos_equity_curve, oos_monthly_matrix = _compute_full_analysis(
      selected_assets, asset_daily, asset_trade_dates, oos_start, oos_end,
    )
    is_metrics, is_equity_curve, is_monthly_matrix = _compute_full_analysis(
      selected_assets, asset_daily, asset_trade_dates, is_start, is_end,
    )

    exec_oos_metrics = None
    exec_oos_equity_curve = None
    exec_oos_monthly_matrix = None
    exec_is_metrics = None
    if has_exec_filter:
      exec_oos_metrics, exec_oos_equity_curve, exec_oos_monthly_matrix = _compute_full_analysis(
        selected_assets, exec_asset_daily, exec_asset_trade_dates,
        oos_start, oos_end,
      )
      # Compute exec-filtered IS metrics so WFE compares apples to apples
      exec_is_metrics, _, _ = _compute_full_analysis(
        selected_assets, exec_asset_daily, exec_asset_trade_dates,
        is_start, is_end,
      )

    report_oos = exec_oos_metrics if exec_oos_metrics else oos_metrics
    # For WFE: use exec-filtered IS when exec filter is active (fair comparison)
    wfe_is = exec_is_metrics if exec_is_metrics else is_metrics
    is_pnl = wfe_is.get("net_profit", 0)
    oos_pnl = report_oos.get("net_profit", 0)
    is_days = max((pd.Timestamp(is_end) - pd.Timestamp(is_start)).days, 1)
    oos_days = max((pd.Timestamp(oos_end) - pd.Timestamp(oos_start)).days, 1)
    wfe = round((oos_pnl / oos_days) / (is_pnl / is_days), 4) if is_pnl != 0 else None

    display_assets = [ad["symbol"] + "~B" + str(ad["batch_id"]) + "_" + ad["side"]
                      for ad in asset_details]

    is_trade_count = sum(
      _count_trades_in_range(asset_trade_dates.get(k, []), is_start, is_end)
      for k in selected_assets
    )

    # Enrich asset_details with timeframe + group from batch_info
    _bi = batch_info or {}
    for ad in asset_details:
      bi = _bi.get(ad["batch_id"], {})
      ad["timeframe"] = bi.get("timeframe", "?")
      ad["group"] = bi.get("group") or bi.get("name", f"B{ad['batch_id']}")

    # Per-asset breakdown (IS + OOS metrics per asset)
    use_daily = exec_asset_daily if has_exec_filter else asset_daily
    use_tdates = exec_asset_trade_dates if has_exec_filter else asset_trade_dates
    asset_breakdown = _compute_asset_breakdown(
      selected_assets, use_daily, use_tdates, _bi,
      is_start, is_end, oos_start, oos_end,
    )

    # Concurrent trades (OOS period, using exec-filtered intervals if applicable)
    use_intervals = (exec_asset_trade_intervals or {}) if has_exec_filter else (asset_trade_intervals or {})
    oos_concurrent = _compute_concurrent_trades(
      selected_assets, use_intervals, oos_start, oos_end,
    )

    # Count unique POSITIONS (not exits) by deduplicating entry_times
    # Within one asset key, same entry_time = same position (multi-exit)
    oos_s = pd.Timestamp(oos_start)
    oos_e = pd.Timestamp(oos_end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    oos_position_count = 0
    for key in selected_assets:
      intervals = use_intervals.get(key, [])
      unique_entries = {et for et, _ in intervals if oos_s <= et <= oos_e}
      oos_position_count += len(unique_entries)

    # Patch total_trades in the reported OOS metrics to show position count
    report_oos["total_positions"] = oos_position_count
    if exec_oos_metrics:
      exec_oos_metrics["total_positions"] = oos_position_count
    oos_metrics["total_positions"] = oos_position_count

    candidate_data = {
      "rank": 1,
      "assets": display_assets,
      "is_metrics": is_metrics,
      "is_equity_curve": is_equity_curve,
      "is_monthly_matrix": is_monthly_matrix,
      "oos_metrics": report_oos,
      "oos_full_metrics": oos_metrics,
      "oos_equity_curve": exec_oos_equity_curve if exec_oos_equity_curve else oos_equity_curve,
      "oos_monthly_matrix": exec_oos_monthly_matrix if exec_oos_monthly_matrix else oos_monthly_matrix,
      "oos_wfe": wfe,
      "selected_batches": selected_batches,
      "calibration_results": cal_results,
      "asset_details": asset_details,
      "asset_breakdown": asset_breakdown,
      "estimated_tpm": round(estimated_tpm, 1),
      "n_batches": len(selected_batches),
      "n_assets": len(selected_assets),
      "oos_max_concurrent": oos_concurrent["max_concurrent"],
      "oos_avg_concurrent": oos_concurrent["avg_concurrent"],
    }
    if exec_oos_metrics:
      candidate_data["oos_exec_metrics"] = exec_oos_metrics
      exec_pnl = exec_oos_metrics.get("net_profit", 0)
      full_pnl = oos_metrics.get("net_profit", 0)
      candidate_data["ef_ratio"] = round(exec_pnl / full_pnl * 100, 1) if full_pnl != 0 else 0

    if category_promotion_audit is not None:
      candidate_data["category_promotion"] = category_promotion_audit

    window_results.append({
      "window_num": window["window_num"],
      "is_start": is_start,
      "is_end": is_end,
      "oos_start": oos_start,
      "oos_end": oos_end,
      "is_trade_count": is_trade_count,
      "is_symbol_count": len(selected_assets),
      "is_pool_size": len(batch_ids),
      "candidates": [candidate_data],
    })

  return window_results


def _l2_select(
  ranked: list[tuple[int, float]],
  batch_sides: dict[int, str],
  mb: int,
  mode: str = "fixed",
) -> list[int]:
  """L2 Groups: select batches from both Long and Short pools.

  Supports 4 allocation modes:
    "fixed"        — ceil(mb/2) per side (legacy 50/50 behavior)
    "proportional" — slots proportional to each side's count in candidates (min 1)
    "threshold"    — (mb-1) to the stronger side (by avg IS score), 1 to weaker
    "min1"         — 1 guaranteed per side, rest filled from overall ranking

  Preserves ranking order within each side. Falls back to fill from
  the other side if one side has too few candidates.
  """
  import math

  longs = [(bid, sc) for bid, sc in ranked if batch_sides.get(bid) == "Long"]
  shorts = [(bid, sc) for bid, sc in ranked if batch_sides.get(bid) == "Short"]

  if mode == "fixed":
    # Legacy: ceil(mb/2) from each side
    per_side = math.ceil(mb / 2)
    selected = []
    selected.extend([bid for bid, _ in longs[:per_side]])
    selected.extend([bid for bid, _ in shorts[:per_side]])
    selected = selected[:mb]

  elif mode == "proportional":
    # Allocate slots proportional to each side's representation in the
    # candidate pool, with a minimum of 1 per side (if that side exists).
    n_long = len(longs)
    n_short = len(shorts)
    total = n_long + n_short
    if total == 0:
      return []
    if n_long == 0:
      long_slots, short_slots = 0, mb
    elif n_short == 0:
      long_slots, short_slots = mb, 0
    else:
      # Proportional allocation with min-1 floor
      long_slots = max(1, round(mb * n_long / total))
      short_slots = max(1, mb - long_slots)
      # If both floors push total > mb, reduce the larger one
      if long_slots + short_slots > mb:
        if long_slots >= short_slots:
          long_slots = mb - short_slots
        else:
          short_slots = mb - long_slots

    selected = []
    selected.extend([bid for bid, _ in longs[:long_slots]])
    selected.extend([bid for bid, _ in shorts[:short_slots]])

  elif mode == "threshold":
    # Give (mb-1) slots to the side with higher average IS score, 1 to the other.
    if not longs and not shorts:
      return []
    if not longs:
      return [bid for bid, _ in shorts[:mb]]
    if not shorts:
      return [bid for bid, _ in longs[:mb]]

    import numpy as np
    long_avg = float(np.mean([sc for _, sc in longs]))
    short_avg = float(np.mean([sc for _, sc in shorts]))

    if long_avg >= short_avg:
      long_slots = mb - 1
      short_slots = 1
    else:
      long_slots = 1
      short_slots = mb - 1

    selected = []
    selected.extend([bid for bid, _ in longs[:long_slots]])
    selected.extend([bid for bid, _ in shorts[:short_slots]])

  elif mode == "min1":
    # Guarantee 1 from each side (if exists), fill rest from overall ranking.
    selected = []
    selected_set = set()
    if longs:
      selected.append(longs[0][0])
      selected_set.add(longs[0][0])
    if shorts:
      selected.append(shorts[0][0])
      selected_set.add(shorts[0][0])
    # Fill remaining from overall ranked list (best IS scores first)
    for bid, _ in ranked:
      if len(selected) >= mb:
        break
      if bid not in selected_set:
        selected.append(bid)
        selected_set.add(bid)
    return selected[:mb]

  else:
    raise ValueError(f"Unknown l2_mode: {mode!r}")

  # Fill if one side had too few candidates
  if len(selected) < mb:
    remaining = [bid for bid, _ in ranked if bid not in set(selected)]
    for bid in remaining:
      selected.append(bid)
      if len(selected) >= mb:
        break

  return selected


def _compute_pool_fingerprint(
  all_trades: list[BacktestTrade],
  run_batch_map: dict[int, int],
) -> str:
  """Compute a deterministic fingerprint for a trade pool.

  The fingerprint captures: deduped batch count, batch IDs, total trade count,
  and total PnL (rounded). If ANY trade is added, removed, or modified in the
  pool, the fingerprint changes — invalidating any cached precomputed structures.
  """
  # Group by batch_id
  batch_trades: dict[int, list[float]] = defaultdict(list)
  for t in all_trades:
    bid = run_batch_map.get(t.run_id)
    if bid is not None:
      batch_trades[bid].append(t.net_profit)

  # Build per-batch fingerprints (same as dedup_batches), then hash the full set
  parts = []
  for bid in sorted(batch_trades.keys()):
    pnls = batch_trades[bid]
    fp = f"{bid}:{len(pnls)}:{round(sum(pnls), 0)}"
    parts.append(fp)

  raw = "|".join(parts)
  return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _compute_exec_hash(
  exec_exclude_hours: set[int] | None,
  exec_exclude_days: set[int] | None,
) -> str:
  """Compute a hash for the exec filter configuration."""
  h_str = sorted(exec_exclude_hours) if exec_exclude_hours else []
  d_str = sorted(exec_exclude_days) if exec_exclude_days else []
  raw = f"h={h_str}|d={d_str}"
  return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _cache_path(
  cache_dir: str | Path,
  pool_fp: str,
  exec_hash: str,
) -> Path:
  """Build the cache file path.

  `v2` (2026-10-08): caches now carry `straddle_table` for the decision-time
  selection fix; older `precomputed_*.pkl` files are simply not read again.
  """
  return Path(cache_dir) / f"precomputed_v2_{pool_fp}_{exec_hash}.pkl"


def precompute_pnl_structures(
  all_trades: list[BacktestTrade],
  run_batch_map: dict[int, int],
  exec_exclude_hours: set[int] | None = None,
  exec_exclude_days: set[int] | None = None,
  cache_dir: str | Path | None = None,
) -> dict:
  """Pre-compute all daily PnL structures from trades.

  This is the expensive step (~5 min for 230K trades). By calling this once
  and passing the result to run_batch_selection(precomputed=...), you can
  run many configs without rebuilding these structures each time.

  If exec_exclude_hours/exec_exclude_days are provided, exec-filtered
  structures are also computed. Otherwise exec structures = full structures.

  If cache_dir is provided, the precomputed structures are saved to disk
  after building, keyed by a pool fingerprint + exec filter hash. On
  subsequent calls with the same pool and exec filter, the cached version
  is loaded instead of rebuilding (~seconds vs ~5 minutes). The cache is
  automatically invalidated if the pool fingerprint changes (any trade
  added, removed, or modified).

  Returns a dict with keys:
    trades, rbm, batch_ids, batch_daily, asset_daily, asset_to_batch,
    batch_to_assets, asset_trade_dates, batch_sides, asset_wr_data,
    exec_batch_daily, exec_asset_daily, exec_asset_trade_dates,
    has_exec_filter, _pool_fingerprint, _exec_hash
  """
  # ── Cache check ──
  pool_fp = _compute_pool_fingerprint(all_trades, run_batch_map) if cache_dir else ""
  exec_hash = _compute_exec_hash(exec_exclude_hours, exec_exclude_days) if cache_dir else ""

  if cache_dir:
    cdir = Path(cache_dir)
    cdir.mkdir(parents=True, exist_ok=True)
    cpath = _cache_path(cdir, pool_fp, exec_hash)

    if cpath.exists():
      try:
        t0 = time.monotonic()
        with open(cpath, "rb") as f:
          cached = pickle.load(f)
        # Validate fingerprint matches (guards against hash collisions or stale files)
        if (
          cached.get("_pool_fingerprint") == pool_fp
          and cached.get("_exec_hash") == exec_hash
        ):
          elapsed = time.monotonic() - t0
          log.info(
            "Precompute cache HIT: loaded %d batches, %d assets in %.1fs from %s",
            len(cached["batch_ids"]), len(cached["asset_daily"]), elapsed, cpath.name,
          )
          return cached
        else:
          log.warning("Precompute cache STALE (fingerprint mismatch), rebuilding")
      except (OSError, pickle.UnpicklingError, EOFError, AttributeError, KeyError) as e:
        # CS-046: narrowed from bare ``except Exception``. Real failure modes:
        # OSError (file locked/missing), UnpicklingError/EOFError (truncated
        # cache), AttributeError/KeyError (pickle written by old schema).
        # A typo or programming bug should now surface at the offending line.
        log.warning("Precompute cache load failed (%s: %s), rebuilding",
                    type(e).__name__, e)

  # ── Build from scratch ──
  t0 = time.monotonic()
  trades, rbm, batch_ids = dedup_batches(all_trades, run_batch_map)
  log.info("Precompute: %d deduped batches, %d trades", len(batch_ids), len(trades))

  batch_daily = build_batch_daily_pnl(trades, rbm)
  asset_daily, asset_to_batch, batch_to_assets, asset_trade_dates, asset_trade_intervals = (
    build_asset_daily_pnl(trades, rbm)
  )
  batch_sides = detect_batch_sides(trades, rbm)

  # Asset WR data: per-asset per-day win/total counts for asset_wr_gate
  asset_wr_data = build_asset_wr_data(trades, rbm)
  log.info("Asset WR data: %d assets", len(asset_wr_data))

  # Trades open past midnight — for decision-time selection views (O1)
  straddle_table = build_straddle_table(trades, rbm)

  has_exec_filter = bool(exec_exclude_hours or exec_exclude_days)
  if has_exec_filter:
    exec_trades = _filter_trades_exec(trades, exec_exclude_hours, exec_exclude_days)
    exec_batch_daily = build_batch_daily_pnl(exec_trades, rbm)
    exec_asset_daily, _, _, exec_asset_trade_dates, exec_asset_trade_intervals = (
      build_asset_daily_pnl(exec_trades, rbm)
    )
    log.info(
      "Exec filter: %d→%d trades, %d exec batch series, %d exec asset series",
      len(trades), len(exec_trades), len(exec_batch_daily), len(exec_asset_daily),
    )
  else:
    exec_batch_daily = batch_daily
    exec_asset_daily = asset_daily
    exec_asset_trade_dates = asset_trade_dates
    exec_asset_trade_intervals = asset_trade_intervals

  result = {
    "trades": trades,
    "rbm": rbm,
    "batch_ids": batch_ids,
    "batch_daily": batch_daily,
    "asset_daily": asset_daily,
    "asset_to_batch": asset_to_batch,
    "batch_to_assets": batch_to_assets,
    "asset_trade_dates": asset_trade_dates,
    "asset_trade_intervals": asset_trade_intervals,
    "batch_sides": batch_sides,
    "asset_wr_data": asset_wr_data,
    "straddle_table": straddle_table,
    "exec_batch_daily": exec_batch_daily,
    "exec_asset_daily": exec_asset_daily,
    "exec_asset_trade_dates": exec_asset_trade_dates,
    "exec_asset_trade_intervals": exec_asset_trade_intervals,
    "has_exec_filter": has_exec_filter,
    "_pool_fingerprint": pool_fp,
    "_exec_hash": exec_hash,
  }

  elapsed = time.monotonic() - t0
  log.info("Precompute: built in %.1fs", elapsed)

  # ── Save to cache ──
  if cache_dir:
    cpath = _cache_path(Path(cache_dir), pool_fp, exec_hash)
    try:
      # Save without raw trades (they're large and not needed for precomputed mode)
      cache_data = {k: v for k, v in result.items() if k not in ("trades",)}
      cache_data["trades"] = []  # Placeholder — precomputed mode passes [] anyway
      t1 = time.monotonic()
      with open(cpath, "wb") as f:
        pickle.dump(cache_data, f, protocol=pickle.HIGHEST_PROTOCOL)
      save_elapsed = time.monotonic() - t1
      size_mb = cpath.stat().st_size / (1024 * 1024)
      log.info(
        "Precompute cache SAVED: %.1f MB in %.1fs → %s",
        size_mb, save_elapsed, cpath.name,
      )
    except (OSError, pickle.PicklingError) as e:
      # CS-046: narrowed from bare ``except Exception``. The save path only
      # fails on filesystem issues (permissions, disk full) or unpicklable
      # objects. Anything else is a bug worth raising.
      log.warning("Precompute cache save failed (%s: %s), continuing without cache",
                  type(e).__name__, e)

  return result


def run_batch_selection(
  all_trades: list[BacktestTrade],
  windows: list[dict],
  config: BatchSelectionConfig,
  run_batch_map: dict[int, int],
  precomputed: dict | None = None,
  progress_callback: callable | None = None,
  batch_info: dict[int, dict] | None = None,
  db=None,  # Optional Session — required ONLY when config.category_filter_enabled=True
) -> dict:
  """Run the 3-step batch selection WFO.

  Supports Q-aligned averaging (q_starts=4), L2 groups, progressive pruning,
  and past OOS confirmation.

  Args:
    precomputed: Optional dict from precompute_pnl_structures(). When provided,
      skips the expensive trade aggregation step. The exec filter in `config`
      is IGNORED when precomputed is set — use the exec filter args in
      precompute_pnl_structures() instead.

  Returns dict compatible with the existing WFO result format:
    windows: list of window result dicts
    tracks: compiled per-rank tracks
    cumulative_is / cumulative_oos: aggregated metrics
    summary: mode-appropriate summary
  """
  # ── Progress: precomputing stage ──
  if progress_callback:
    progress_callback(step=0, total=0, label="Preparing trade data...")

  if precomputed is not None:
    # Use pre-computed structures — skip all expensive trade aggregation
    trades = precomputed["trades"]
    rbm = precomputed["rbm"]
    batch_ids = precomputed["batch_ids"]
    batch_daily = precomputed["batch_daily"]
    asset_daily = precomputed["asset_daily"]
    asset_to_batch = precomputed["asset_to_batch"]
    batch_to_assets = precomputed["batch_to_assets"]
    asset_trade_dates = precomputed["asset_trade_dates"]
    asset_trade_intervals = precomputed.get("asset_trade_intervals", {})
    batch_sides = precomputed["batch_sides"]
    asset_wr_data = precomputed.get("asset_wr_data")
    exec_batch_daily = precomputed["exec_batch_daily"]
    exec_asset_daily = precomputed["exec_asset_daily"]
    exec_asset_trade_dates = precomputed["exec_asset_trade_dates"]
    exec_asset_trade_intervals = precomputed.get("exec_asset_trade_intervals", {})
    has_exec_filter = precomputed["has_exec_filter"]
    straddle_table = precomputed.get("straddle_table")
    if straddle_table is None and trades:
      straddle_table = build_straddle_table(trades, rbm)
    if straddle_table is None:
      log.warning(
        "Batch selection (precomputed): no straddle_table and no trades — "
        "selection CANNOT exclude trades still open at each decision (O1). "
        "Rebuild with precompute_pnl_structures().",
      )
    log.info(
      "Batch selection (precomputed): %d batches, %d trades, exec_filter=%s, wr_data=%s",
      len(batch_ids), len(trades), has_exec_filter,
      len(asset_wr_data) if asset_wr_data else "none",
    )
  else:
    # ── Prerequisite: dedup + build data structures ──
    trades, rbm, batch_ids = dedup_batches(all_trades, run_batch_map)
    log.info("Batch selection: %d deduped batches, %d trades", len(batch_ids), len(trades))

    # IS/CAL structures: built from ALL trades (no exec filter)
    batch_daily = build_batch_daily_pnl(trades, rbm)
    asset_daily, asset_to_batch, batch_to_assets, asset_trade_dates, asset_trade_intervals = (
      build_asset_daily_pnl(trades, rbm)
    )

    # Detect batch sides (needed for L2 groups and pruning)
    batch_sides = detect_batch_sides(trades, rbm)

    # Asset WR data: per-asset per-day win/total counts for asset_wr_gate
    asset_wr_data = build_asset_wr_data(trades, rbm)

    # Trades open past midnight — for decision-time selection views (O1)
    straddle_table = build_straddle_table(trades, rbm)

    # Exec-filtered structures: for OOS evaluation only
    has_exec_filter = bool(config.exec_exclude_hours or config.exec_exclude_days)
    if has_exec_filter:
      exec_trades = _filter_trades_exec(
        trades, config.exec_exclude_hours, config.exec_exclude_days,
      )
      exec_batch_daily = build_batch_daily_pnl(exec_trades, rbm)
      exec_asset_daily, _, _, exec_asset_trade_dates, exec_asset_trade_intervals = (
        build_asset_daily_pnl(exec_trades, rbm)
      )
      log.info(
        "Exec filter: %d→%d trades, %d exec batch series, %d exec asset series",
        len(trades), len(exec_trades), len(exec_batch_daily), len(exec_asset_daily),
      )
    else:
      exec_batch_daily = batch_daily
      exec_asset_daily = asset_daily
      exec_asset_trade_dates = asset_trade_dates
      exec_asset_trade_intervals = asset_trade_intervals

  log.info(
    "Built PnL: %d batch series, %d asset series (%d Long, %d Short)",
    len(batch_daily), len(asset_daily),
    sum(1 for s in batch_sides.values() if s == "Long"),
    sum(1 for s in batch_sides.values() if s == "Short"),
  )

  # ── Progressive Pruning: pre-filter batch pool ──
  active_batch_ids = batch_ids
  pruning_look_ahead = False
  if config.pruning_enabled and (config.prune_long > 0 or config.prune_short > 0):
    # CS-005: hard gate — refuse to run pruning unless the caller has
    # explicitly acknowledged that the output is research-only and MUST NOT
    # be used to activate a live portfolio. Fails loud here instead of
    # silently producing contaminated numbers that someone else serialises
    # past the honour-system warning.
    if not config.research_only_acknowledged:
      raise ValueError(
        "CS-005: Progressive Pruning is look-ahead-contaminated and may only "
        "be used for research. Set research_only_acknowledged=True on the "
        "BatchSelectionConfig to confirm the output will not feed a live "
        "portfolio. See AUDIT_MASTER.md CS-005 and "
        "batch_selection._progressive_prune docstring."
      )
    log.warning(
      "Progressive Pruning ENABLED (RESEARCH ONLY) — static pruning uses ALL "
      "windows including future IS periods. OOS metrics are research-only and "
      "unsafe for live trading (Tier 1.2 audit B01 / CS-005)."
    )
    keep = _progressive_prune(batch_daily, batch_sides, windows, config)
    active_batch_ids = [bid for bid in batch_ids if bid in keep]
    log.info("After pruning: %d → %d batches", len(batch_ids), len(active_batch_ids))
    pruning_look_ahead = True

  # ── Q-Aligned Averaging ──
  from backend.analysis.wfo_window_gen import generate_rolling_windows

  q_start_months = [1, 4, 7, 10] if config.q_starts >= 4 else [None]
  all_q_window_results: list[list[dict]] = []

  for q_month in q_start_months:
    if q_month is not None:
      # Generate windows from a Q-aligned start
      # Find the earliest date in the data and align to the given quarter month
      all_dates = []
      for pnl in batch_daily.values():
        if not pnl.empty:
          all_dates.append(pnl.index.min())
      if not all_dates:
        continue
      earliest = min(all_dates)
      # Align: find the first occurrence of q_month on or after earliest
      q_year = earliest.year
      q_start_date = date(q_year, q_month, 1)
      if pd.Timestamp(q_start_date) < earliest:
        # Move to next year if the Q-start is before data start
        q_start_date = date(q_year + 1, q_month, 1)

      # Determine last_oos_end from the original windows
      # Bug fix: windows[-1] may be an IS-only window (oos_end=None) from
      # generate_rolling_windows(). Find the last window that has an OOS period.
      orig_last_oos = None
      for w_rev in reversed(windows):
        if w_rev.get("oos_end"):
          orig_last_oos = w_rev["oos_end"]
          break
      if not orig_last_oos:
        continue
      oos_months = (
        (pd.Timestamp(windows[0]["oos_end"]) - pd.Timestamp(windows[0]["oos_start"])).days
        if windows and windows[0].get("oos_start")
        else 90
      )
      # Approximate oos_months from the first window
      oos_mo = round(oos_months / 30) or 3
      is_mo_count = round(
        (pd.Timestamp(windows[0]["is_end"]) - pd.Timestamp(windows[0]["is_start"])).days / 30
      ) if windows else 12

      q_windows = generate_rolling_windows(
        is_mo_count, oos_mo, orig_last_oos, q_start_date.isoformat(),
      )
      q_windows = [w for w in q_windows if w.get("oos_start") is not None]
      if not q_windows:
        continue
      log.info("Q-start %d: %d windows from %s", q_month, len(q_windows), q_start_date)
    else:
      q_windows = windows

    # Run the single-pass pipeline
    wr = _run_single_batch_selection(
      trades=trades, rbm=rbm, batch_ids=active_batch_ids,
      batch_daily=batch_daily,
      asset_daily=asset_daily, asset_to_batch=asset_to_batch,
      batch_to_assets=batch_to_assets, asset_trade_dates=asset_trade_dates,
      asset_trade_intervals=asset_trade_intervals,
      exec_batch_daily=exec_batch_daily, exec_asset_daily=exec_asset_daily,
      exec_asset_trade_dates=exec_asset_trade_dates,
      exec_asset_trade_intervals=exec_asset_trade_intervals,
      has_exec_filter=has_exec_filter, batch_sides=batch_sides,
      asset_wr_data=asset_wr_data, windows=q_windows, config=config,
      progress_callback=progress_callback, batch_info=batch_info, db=db,
      straddle_table=straddle_table,
    )
    all_q_window_results.append(wr)

  # Merge all Q-start results into a single window_results list
  if len(all_q_window_results) == 1:
    window_results = all_q_window_results[0]
  else:
    # Combine all windows from all Q-starts, renumber sequentially
    window_results = []
    win_num = 1
    for q_idx, wr_list in enumerate(all_q_window_results):
      for w in wr_list:
        w_copy = dict(w)
        w_copy["window_num"] = win_num
        w_copy["q_start_idx"] = q_idx
        window_results.append(w_copy)
        win_num += 1

  # ── Compile tracks and cumulative metrics ──
  from backend.analysis.wfo_aggregation import (
    aggregate_summary,
    compile_tracks,
    cumulative_metrics,
  )

  tracks = compile_tracks(window_results, 1, trades, mode="rolling")
  cum_is = cumulative_metrics(window_results, "is")
  cum_oos = cumulative_metrics(window_results, "oos")
  summary = aggregate_summary(window_results, "rolling")

  # Add batch-selection-specific summary stats
  oos_windows = [w for w in window_results if w.get("oos_start")]
  # For Q-averaged mode, exclude EACH Q-start's first (cold-start) window
  if config.q_starts >= 4:
    # Group by Q-start index and exclude window_num=1 from each group
    q_first_wins = set()
    seen_q = set()
    for w in oos_windows:
      q_idx = w.get("q_start_idx", 0)
      if q_idx not in seen_q:
        seen_q.add(q_idx)
        q_first_wins.add(w["window_num"])
    cal_windows = [w for w in oos_windows if w["window_num"] not in q_first_wins]
  else:
    cal_windows = oos_windows[1:]  # Exclude cold-start window 1

  if cal_windows:
    # Primary OOS WR (exec if filtered, else full)
    cal_wins = sum(
      1 for w in cal_windows
      if w["candidates"] and w["candidates"][0].get("oos_metrics", {}).get("net_profit", 0) > 0
    )
    summary["calibrated_wr"] = f"{cal_wins}/{len(cal_windows)}"
    summary["calibrated_wr_pct"] = round(cal_wins / len(cal_windows) * 100, 1)
    summary["calibrated_pnl"] = round(sum(
      w["candidates"][0].get("oos_metrics", {}).get("net_profit", 0)
      for w in cal_windows if w["candidates"]
    ), 2)

    # Full OOS WR (always unfiltered)
    full_wins = sum(
      1 for w in cal_windows
      if w["candidates"] and w["candidates"][0].get("oos_full_metrics", {}).get("net_profit", 0) > 0
    )
    summary["full_wr"] = f"{full_wins}/{len(cal_windows)}"
    summary["full_wr_pct"] = round(full_wins / len(cal_windows) * 100, 1)
    summary["full_pnl"] = round(sum(
      w["candidates"][0].get("oos_full_metrics", {}).get("net_profit", 0)
      for w in cal_windows if w["candidates"]
    ), 2)

    # Exec OOS WR (only when filter is active)
    if has_exec_filter:
      exec_wins = sum(
        1 for w in cal_windows
        if w["candidates"] and w["candidates"][0].get("oos_exec_metrics", {}).get("net_profit", 0) > 0
      )
      summary["exec_wr"] = f"{exec_wins}/{len(cal_windows)}"
      summary["exec_wr_pct"] = round(exec_wins / len(cal_windows) * 100, 1)
      summary["exec_pnl"] = round(sum(
        w["candidates"][0].get("oos_exec_metrics", {}).get("net_profit", 0)
        for w in cal_windows if w["candidates"]
      ), 2)
      # Delta: exec_wr - full_wr
      summary["delta_pp"] = round(summary["exec_wr_pct"] - summary["full_wr_pct"], 1)
      # e/f ratio
      if summary["full_pnl"] != 0:
        summary["ef_ratio_pct"] = round(summary["exec_pnl"] / summary["full_pnl"] * 100, 1)

  # Concurrent trade stats across OOS windows
  if oos_windows:
    max_concurrents = []
    avg_concurrents = []
    for w in oos_windows:
      c0 = w["candidates"][0] if w.get("candidates") else {}
      mc = c0.get("oos_max_concurrent", 0)
      ac = c0.get("oos_avg_concurrent", 0)
      if mc > 0:
        max_concurrents.append(mc)
        avg_concurrents.append(ac)
    if max_concurrents:
      summary["oos_max_concurrent"] = max(max_concurrents)
      summary["oos_avg_max_concurrent"] = round(sum(max_concurrents) / len(max_concurrents), 1)
      summary["oos_avg_concurrent"] = round(sum(avg_concurrents) / len(avg_concurrents), 1)

  summary["total_batches_pool"] = len(batch_ids)
  summary["active_batches_pool"] = len(active_batch_ids)
  summary["mode"] = "batch_selection"
  summary["has_exec_filter"] = has_exec_filter
  summary["q_starts"] = config.q_starts
  summary["l2_groups"] = config.l2_groups
  summary["l2_mode"] = config.l2_mode if config.l2_groups else "off"
  summary["pruning_enabled"] = config.pruning_enabled
  summary["pruning_look_ahead_warning"] = pruning_look_ahead
  # CS-005: top-level research_only flag so no downstream code can miss it.
  # pruning_look_ahead_warning is specific to Progressive Pruning; research_only
  # is the load-bearing "do not activate in live" marker and must propagate
  # to every serialisation layer and the frontend result card.
  summary["research_only"] = pruning_look_ahead
  summary["past_oos_confirm"] = config.past_oos_confirm

  # ── Expected (de-overlapped) OOS metrics for Q-starts > 1 ──
  # When q_starts=4, each OOS period is evaluated ~4 times from different
  # IS starting points. Absolute metrics (PnL, trades, DD) are ~4× inflated.
  # Rate metrics (WR, Sharpe, Sortino, PF) are valid as-is.
  # expected_oos gives the realistic single-pass trading expectation.
  q = max(config.q_starts, 1)
  expected_oos = dict(cum_oos)  # shallow copy
  if q > 1:
    expected_oos["net_profit"] = round(cum_oos.get("net_profit", 0) / q, 2)
    expected_oos["total_trades"] = round(cum_oos.get("total_trades", 0) / q)
    expected_oos["cumulative_max_dd"] = round(cum_oos.get("cumulative_max_dd", 0) / q, 2)
    expected_oos["max_drawdown"] = round(cum_oos.get("max_drawdown", 0) / q, 2)
    expected_oos["best_window_pnl"] = cum_oos.get("best_window_pnl", 0)  # keep as-is (single window)
    expected_oos["worst_window_pnl"] = cum_oos.get("worst_window_pnl", 0)
    expected_oos["total_windows_evaluated"] = round(cum_oos.get("total_windows_evaluated", 0) / q)
    expected_oos["avg_window_pnl"] = cum_oos.get("avg_window_pnl", 0)  # already per-window avg
    expected_oos["avg_trade"] = cum_oos.get("avg_trade", 0)  # already per-trade avg
    # Calibrated PnL also needs adjustment
    if "calibrated_pnl" in summary:
      summary["expected_calibrated_pnl"] = round(summary["calibrated_pnl"] / q, 2)
    if "full_pnl" in summary:
      summary["expected_full_pnl"] = round(summary["full_pnl"] / q, 2)
    if "exec_pnl" in summary:
      summary["expected_exec_pnl"] = round(summary["exec_pnl"] / q, 2)
  summary["q_overlap_divisor"] = q

  # ── Live Window: forward-looking asset selection for trading NOW ──
  if config.include_live_window and window_results:
    last_hist = window_results[-1]
    live_oos_end = _clock_today().isoformat()

    # Rolling walk-forward: the live window folds the last historical OOS
    # into IS (we now have that data), so IS shifts forward by one OOS
    # span and live OOS = [last_oos_end + 1, today].
    last_oos_start = last_hist.get("oos_start")
    last_oos_end = last_hist.get("oos_end")
    if last_oos_start and last_oos_end:
      oos_days = (
        date.fromisoformat(last_oos_end) - date.fromisoformat(last_oos_start)
      ).days + 1
      live_is_start = (
        date.fromisoformat(last_hist["is_start"]) + timedelta(days=oos_days)
      ).isoformat()
      live_is_end = last_oos_end
      live_oos_start = (
        date.fromisoformat(last_oos_end) + timedelta(days=1)
      ).isoformat()
    else:
      # No OOS on the last window — keep IS unchanged, live span from is_end.
      live_is_start = last_hist["is_start"]
      live_is_end = last_hist["is_end"]
      live_oos_start = last_hist.get("oos_end", live_is_end)

    if progress_callback:
      progress_callback(
        step=0, total=0,
        label=f"Live window — selecting assets for {live_oos_start} onwards",
      )

    # Run Steps 1-3 on the live IS period (same logic as _run_single_batch_selection)
    live_window = {
      "window_num": max(w["window_num"] for w in window_results) + 1,
      "is_start": live_is_start,
      "is_end": live_is_end,
      "oos_start": live_oos_start,
      "oos_end": live_oos_end,
    }
    live_results = _run_single_batch_selection(
      trades=trades, rbm=rbm, batch_ids=active_batch_ids,
      batch_daily=batch_daily,
      asset_daily=asset_daily, asset_to_batch=asset_to_batch,
      batch_to_assets=batch_to_assets, asset_trade_dates=asset_trade_dates,
      asset_trade_intervals=asset_trade_intervals,
      exec_batch_daily=exec_batch_daily, exec_asset_daily=exec_asset_daily,
      exec_asset_trade_dates=exec_asset_trade_dates,
      exec_asset_trade_intervals=exec_asset_trade_intervals,
      has_exec_filter=has_exec_filter, batch_sides=batch_sides,
      asset_wr_data=asset_wr_data, windows=[live_window], config=config,
      batch_info=batch_info, db=db, straddle_table=straddle_table,
    )
    # Mark as live. Zero OOS metrics only when the live OOS span is empty
    # (oos_start > oos_end → no forward-walking days exist yet); otherwise
    # keep the real metrics — the user sees honest forward evaluation.
    no_oos_days = live_oos_start > live_oos_end
    for lw in live_results:
      lw["is_live"] = True
      for cand in lw.get("candidates", []):
        cand["is_live"] = True
        if no_oos_days:
          cand["oos_metrics"] = {}
          cand["oos_full_metrics"] = {}
          cand["oos_equity_curve"] = []
          cand["oos_monthly_matrix"] = {"years": [], "matrix": {}, "row_totals": {}, "col_totals": {}, "grand_total": 0}
          cand["oos_wfe"] = None
    window_results.extend(live_results)
    log.info("Live window added: IS [%s, %s] → Live from %s", live_is_start, live_is_end, live_oos_start)

  # ── Aggregate OOS equity curve & monthly matrix ──────────────
  # Stitch per-window OOS equity curves chronologically so equity
  # continues from where the previous window ended.
  agg_oos_equity: list[dict] = []
  agg_oos_monthly: dict = {"years": [], "matrix": {}, "row_totals": {}, "col_totals": {}, "grand_total": 0}
  historical_windows = [w for w in window_results if not w.get("is_live")]
  oos_daily_all: list[tuple[str, float]] = []  # (date, daily_pnl)

  for w in historical_windows:
    cands = w.get("candidates", [])
    if not cands:
      continue
    ec = cands[0].get("oos_equity_curve", [])
    if not ec:
      continue
    # Extract daily PnL from equity diffs
    for i, pt in enumerate(ec):
      if i == 0:
        daily_pnl = pt["equity"]
      else:
        daily_pnl = pt["equity"] - ec[i - 1]["equity"]
      oos_daily_all.append((pt["date"], daily_pnl))

  if oos_daily_all:
    # Sort by date, build cumulative equity
    oos_daily_all.sort(key=lambda x: x[0])
    cumulative = 0.0
    peak = 0.0
    for date_str, pnl in oos_daily_all:
      cumulative += pnl
      cumulative_r = round(cumulative, 2)
      peak = max(peak, cumulative_r)
      dd_pct = round(((cumulative_r - peak) / peak * 100) if peak > 0 else 0.0, 2)
      # Clamp to reasonable range — huge negatives happen when peak is tiny
      dd_pct = max(dd_pct, -100.0)
      agg_oos_equity.append({"date": date_str, "equity": cumulative_r, "drawdown_pct": dd_pct})

    # Build aggregate monthly matrix from daily PnL
    daily_index = pd.DatetimeIndex([d for d, _ in oos_daily_all])
    daily_vals = pd.Series([v for _, v in oos_daily_all], index=daily_index)
    agg_oos_monthly = _build_monthly_matrix(daily_vals)

  # ── Aggregate OOS asset breakdown across all windows ──────────
  agg_oos_breakdown: list[dict] = []
  _bd_accum: dict[str, dict] = {}  # key → {oos_pnl, oos_trades, oos_wr_sum, ...}
  for w in historical_windows:
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
      if k not in _bd_accum:
        _bd_accum[k] = {
          "key": k, "batch_id": item["batch_id"],
          "symbol": item["symbol"], "side": item["side"],
          "timeframe": item.get("timeframe", ""), "group": item.get("group", ""),
          "oos_pnl": 0.0, "oos_trades": 0, "oos_wr_weighted": 0.0,
          "oos_gross_profit": 0.0, "oos_gross_loss": 0.0,
          "is_pnl": 0.0, "is_trades": 0, "is_wr_weighted": 0.0,
          "is_gross_profit": 0.0, "is_gross_loss": 0.0,
        }
      acc = _bd_accum[k]
      acc["oos_pnl"] += item.get("oos_pnl", 0)
      acc["oos_trades"] += item.get("oos_trades", 0)
      acc["oos_wr_weighted"] += item.get("oos_wr", 0) * item.get("oos_trades", 0)
      acc["oos_gross_profit"] += max(item.get("oos_pnl", 0), 0)
      acc["oos_gross_loss"] += min(item.get("oos_pnl", 0), 0)
      acc["is_pnl"] += item.get("is_pnl", 0)
      acc["is_trades"] += item.get("is_trades", 0)
      acc["is_wr_weighted"] += item.get("is_wr", 0) * item.get("is_trades", 0)
      acc["is_gross_profit"] += max(item.get("is_pnl", 0), 0)
      acc["is_gross_loss"] += min(item.get("is_pnl", 0), 0)

  for acc in _bd_accum.values():
    oos_t = acc["oos_trades"]
    is_t = acc["is_trades"]
    oos_gl = abs(acc["oos_gross_loss"])
    is_gl = abs(acc["is_gross_loss"])
    agg_oos_breakdown.append({
      "key": acc["key"], "batch_id": acc["batch_id"],
      "symbol": acc["symbol"], "side": acc["side"],
      "timeframe": acc["timeframe"], "group": acc["group"],
      "oos_wr": acc["oos_wr_weighted"] / oos_t if oos_t else 0,
      "oos_pf": min(acc["oos_gross_profit"] / oos_gl, 10) if oos_gl else (10 if acc["oos_gross_profit"] > 0 else 0),
      "oos_pnl": round(acc["oos_pnl"], 2),
      "oos_trades": oos_t,
      "is_wr": acc["is_wr_weighted"] / is_t if is_t else 0,
      "is_pf": min(acc["is_gross_profit"] / is_gl, 10) if is_gl else (10 if acc["is_gross_profit"] > 0 else 0),
      "is_pnl": round(acc["is_pnl"], 2),
      "is_trades": is_t,
    })
  agg_oos_breakdown.sort(key=lambda x: x["oos_pnl"], reverse=True)

  return {
    "windows": window_results,
    "tracks": tracks,
    "cumulative_is": cum_is,
    "cumulative_oos": cum_oos,
    "expected_oos": expected_oos,
    "summary": summary,
    "aggregate_oos_equity_curve": agg_oos_equity,
    "aggregate_oos_monthly_matrix": agg_oos_monthly,
    "aggregate_oos_breakdown": agg_oos_breakdown,
  }
