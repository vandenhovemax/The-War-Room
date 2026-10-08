"""Analysis routes — correlation, optimization, Monte Carlo, comparison."""
import json
import threading
import uuid
from datetime import datetime, timedelta

from backend.utils.logger import get_logger

logger = get_logger(__name__)

import numpy as np
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.analysis import (
  build_daily_pnl_matrix,
  calculate_stability,
  get_correlation_matrix,
  monte_carlo_simulation,
)
from backend.analysis.benchmark import compute_benchmark_robustness
from backend.analysis.boundary_clip import clip_is_trades
from backend.analysis.correlation import correlation_stats
from backend.analysis.diversification import diversify_portfolio, diversify_split_portfolio
from backend.analysis.optimizer_seeds import compute_benchmark_seed_scores
from backend.db.database import get_db
from backend.db.models import BacktestRun, BacktestTrade, BatchBacktestRun
from backend.services.category_service import get_all_categories

router = APIRouter()


# ---------------------------------------------------------------------------
# Async optimization — runs in background thread, frontend polls for result
# ---------------------------------------------------------------------------

_optimize_progress: dict[str, dict] = {}  # task_id → {status, result, error}
_optimize_lock = threading.Lock()


def _update_progress(task_id: str, step: int, total_steps: int, label: str, timing: dict | None = None):
  """Update optimization progress for frontend polling."""
  with _optimize_lock:
    prog = _optimize_progress.get(task_id)
    if prog is None:
      return
    prog["step"] = step
    prog["total_steps"] = total_steps
    prog["step_label"] = label
    if timing:
      prog.setdefault("timing", {}).update(timing)


def _run_optimize_task(task_id: str, req, db_factory, inner_fn, label: str):
  """Execute an optimization in a background thread and store the result."""
  db = db_factory()
  try:
    result = inner_fn(req, db, task_id=task_id)
    with _optimize_lock:
      _optimize_progress[task_id] = {"status": "completed", "result": result, "error": None}
    logger.info("%s task %s completed", label, task_id)
  except HTTPException as e:
    with _optimize_lock:
      _optimize_progress[task_id] = {
        "status": "error", "result": None, "error": e.detail,
        "http_status": e.status_code,
      }
    logger.warning("%s task %s failed: %s", label, task_id, e.detail)
  except Exception as e:
    with _optimize_lock:
      _optimize_progress[task_id] = {"status": "error", "result": None, "error": str(e)}
    logger.error("%s task %s error: %s", label, task_id, e)
  finally:
    db.close()


@router.get("/optimize/progress/{task_id}")
def optimize_progress(task_id: str):
  """Poll optimization task progress.

  When a terminal task (completed/error) is fetched, its result is marked
  for cleanup after 60s to prevent unbounded memory growth.
  """
  with _optimize_lock:
    prog = _optimize_progress.get(task_id)
  if prog is None:
    return {"status": "unknown"}
  # Schedule cleanup for terminal tasks (result already fetched by frontend)
  if prog.get("status") in ("completed", "error") and not prog.get("_cleanup_scheduled"):
    prog["_cleanup_scheduled"] = True
    def _cleanup():
      import time; time.sleep(60)
      with _optimize_lock:
        _optimize_progress.pop(task_id, None)
      logger.debug("Cleaned up progress for task %s", task_id)
    t = threading.Thread(target=_cleanup, daemon=True)
    t.start()
  return prog


@router.post("/optimize/{task_id}/cancel")
def cancel_optimize_task(task_id: str):
  """Cancel a running optimization/recommender task."""
  with _optimize_lock:
    prog = _optimize_progress.get(task_id)
  if prog is None:
    raise HTTPException(404, "Task not found")
  if prog.get("status") != "running":
    return {"cancelled": False, "reason": f"Task is already {prog.get('status')}"}
  with _optimize_lock:
    prog["status"] = "cancelled"
  logger.info("Optimization task %s cancelled by user", task_id)
  return {"cancelled": True, "task_id": task_id}


@router.post("/optimize/cache/clear")
def clear_optimization_cache():
  """Clear the L0 optimization cache and return prior stats."""
  from backend.analysis.optimization_cache import clear_cache, cache_stats
  stats = cache_stats()
  clear_cache()
  return {"cleared": True, "was": stats}


@router.get("/optimize/cache/stats")
def optimization_cache_stats():
  """Return L0 optimization cache statistics."""
  from backend.analysis.optimization_cache import cache_stats
  return cache_stats()


# --- Helpers ---

def _load_batch_trades(
  batch_id: int, db: Session,
  date_to: str | None = None,
  date_from: str | None = None,
  clip_boundary: bool = False,
) -> list:
  """Load all trades belonging to a batch backtest.

  date_to: OOS cutoff — only trades with entry_time <= date_to (In-Sample).
  date_from: Start date — only trades with entry_time >= date_from.
  clip_boundary: when True, force-close trades straddling date_to at boundary close price.
  """
  runs = (
    db.query(BacktestRun)
    .filter(BacktestRun.batch_id == batch_id, BacktestRun.status == "completed")
    .all()
  )
  if not runs:
    raise HTTPException(status_code=404, detail="No completed runs in batch")
  run_ids = [r.id for r in runs]
  query = db.query(BacktestTrade).filter(
    BacktestTrade.run_id.in_(run_ids),
    BacktestTrade.exit_reason != "backtest_end",
  )
  if date_to:
    # Use < next_day to include all times on cutoff date (fixes SQLite string comparison)
    cutoff_dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
    query = query.filter(BacktestTrade.entry_time < cutoff_dt)
  if date_from:
    from_dt = datetime.strptime(date_from, "%Y-%m-%d")
    query = query.filter(BacktestTrade.entry_time >= from_dt)
  trades = query.all()
  if clip_boundary and date_to:
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    tf = batch.timeframe if batch else "D"
    trades = clip_is_trades(trades, date_to, tf, db)
  return trades


def _apply_session_mask(trades: list, mask_id: int, db: Session, n_free_params: int = 5) -> list:
  """Apply a saved session time mask to filter trades by hour×day.

  Raises HTTPException if the mask is not found or if the adaptive
  significance gate fails (position-based, three-layer check).
  """
  import json as _json
  from backend.analysis.session_mask_filter import apply_session_mask, validate_mask_significance
  from backend.db.models import SessionTimeMask

  mask = db.query(SessionTimeMask).filter(SessionTimeMask.id == mask_id).first()
  if not mask:
    raise HTTPException(400, f"Session time mask #{mask_id} not found")

  mask_data = _json.loads(mask.mask_data)
  filtered = apply_session_mask(trades, mask_data)

  validation = validate_mask_significance(trades, filtered, n_free_params=n_free_params)
  if not validation["ok"]:
    reason = validation.get("gate_fail_reason", "Significance gate failed.")
    raise HTTPException(
      400,
      f"Session mask '{mask.name}': {reason} Widen the mask or remove it."
    )

  logger.info(
    "Session mask '%s' applied: %d → %d positions (%s%% retained, %d assets dropped)",
    mask.name, validation["positions_before"], validation["positions_after"],
    validation["pct_retained"], len(validation["dropped_assets"]),
  )
  return filtered


# --- Request Schemas ---

class CorrelationRequest(BaseModel):
  batch_id: int
  corr_method: str = "standard"
  min_trades: int = 0
  min_pf: float = 0.0
  min_wr: float = 0.0
  date_to: str | None = None


class OptimizeRequest(BaseModel):
  batch_id: int
  n_assets: int = 5
  objective: str = "hybrid"
  corr_method: str = "standard"
  div_algo: str = "greedy"
  n_portfolios: int = 3
  date_to: str | None = None  # OOS cutoff — only use trades before this date
  date_from: str | None = None  # Start date — only use trades after this date
  # Pre-optimization filters — disqualify assets that don't meet thresholds
  min_trades: int = 0
  min_pf: float = 0.0
  min_wr: float = 0.0
  # Bybit size gate — drop assets whose trades exceed Bybit qty limits at target risk
  bybit_size_gate: bool = False
  bybit_size_gate_max_reject_pct: float = 0.0  # 0 = binary (any rejection = fail)
  # Post-hoc trading costs — deducted from trade PnL before optimization
  extra_slippage_pct: float = 0.0
  extra_commission_pct: float = 0.0
  funding_daily_pct: float = 0.0
  spread_pct: float = 0.0
  # Seed ranking
  seed_metrics: list[str] = ["sortino"]
  max_seeds: int = 20
  seed_weights: dict[str, float] | None = None  # per-metric weights {metric: weight}
  # Stability prioritization
  prioritize_stability: bool = False
  # Event-based L1 scoring
  event_mode: bool = False        # score assets by last N trades instead of calendar PnL
  n_trades: int = 20              # lookback window (last N closed trades per asset)
  staleness_days: int = 180       # exclude assets with no trade in the last N days
  # Trade-count constraints
  tc_low_threshold: int = 0
  tc_max_low: int = 99
  tc_high_threshold: int = 0
  tc_min_high: int = 0
  # Recency-weighted seed scoring
  recency_enabled: bool = False
  recency_mode: str = "gradual"    # "gradual" or "batch"
  recency_batch_size: int = 10
  recency_boost_factor: float = 3.0
  # Benchmark robustness
  benchmark_enabled: bool = False
  benchmark_source: str = ""
  benchmark_split: str = "time"  # "time" or "trades"
  benchmark_threshold: float = 1.2
  benchmark_elite_target: float = 1.0
  benchmark_exclude_fragile: bool = True
  # Benchmark seed score enhancements (0.0 = off, 0.0-0.5 range)
  benchmark_consistency_blend: float = 0.0
  benchmark_fragility_blend: float = 0.0
  benchmark_wr_harmony_blend: float = 0.0
  # Category filter — only include assets matching selected categories
  category_filter: list[str] = []
  # Portfolio diversity: max Jaccard overlap between candidates (None = off)
  max_overlap: float | None = None
  # Risk profile — applied to ALL trades per-asset before optimization
  risk_profile_mode: str = "fixed"            # "fixed"|"pct"|"martingale"|"anti_martingale"|"inv_martingale"|"inv_anti_martingale"|"kelly"
  risk_profile_value: float = 0.0             # $ (fixed/mart) or % (pct). 0 = use batch risk as-is
  risk_profile_initial_capital: float = 10_000.0
  risk_profile_multiplier: float = 2.0        # martingale only
  risk_profile_max_streak: int = 3            # martingale only
  risk_profile_martingale_compound: str = "off"  # "off"|"dollar"|"pct" — compound equity with martingale
  risk_profile_martingale_scope: Literal["asset", "portfolio", "asset_batch", "batch"] = "asset"
  risk_profile_kelly_n: float = 2.0               # kelly N divisor (1=full, 2=half, 4=quarter)
  risk_profile_kelly_warmup: int = 20         # trades before Kelly reaches full confidence
  risk_profile_martingale_count_mode: Literal["full_position", "each_exit", "confirmed_tsl"] = "full_position"
  risk_profile_martingale_oos_reset: bool = True
  risk_profile_martingale_start_after: int = 0
  risk_profile_anti_martingale_reset_after: int = 0
  # Per-batch sizing — "portfolio" = one shared config, "per_batch" = each batch independent
  risk_profile_sizing_master: str = "portfolio"
  risk_profile_batch_configs: str | None = None  # JSON: {"batch_id": {mode, value, ...}}
  # Auto-risk: per-asset configs from auto_risk_profile analysis
  # JSON: {"BTCUSDT": {"mode": "anti_martingale", "value": 50, "valueFixed": 100, ...}, ...}
  risk_profile_auto_asset_configs: str | None = None
  # Boundary clipping — force-close IS trades at IS boundary close price
  clip_is: bool = True
  # Stage 1: Pre-filter (structural quality)
  prefilter_enabled: bool = False
  prefilter_mode: str = "trend"  # "trend" or "meanrev"
  prefilter_hurst_min: float = 0.50
  prefilter_ter_percentile: int = 25
  prefilter_hurst_max: float = 0.50
  prefilter_vr_max: float = 1.0
  prefilter_min_crossing_rate: float = 0.05
  # Stage 3: DSR benchmark gate
  benchmark_dsr_gate: bool = False
  # Stage 4: AC post-filter
  ac_postfilter: bool = False
  ac_postfilter_type: str = "trend"  # "trend" or "meanrev"
  ac_lag: int = 1
  # Max drawdown hard limit — reject portfolios exceeding this DD% of peak equity
  max_allowed_drawdown_pct: float = 0.0  # 0 = disabled, e.g. 30.0 = reject >30% DD
  # Avg Range seed config — market structure consecutive range thresholds
  avg_range_bull_threshold: float = 0.0
  avg_range_bear_threshold: float = 0.0
  avg_range_all_threshold: float = 0.0
  avg_range_filter_mode: bool = False
  # Asset Quality seed — composite OHLCV grade + structural thresholds
  asset_quality_mode: str = "trend"         # "trend" or "meanrev"
  asset_quality_grade_mode: str = "absolute" # "absolute" or "autozoom"
  asset_quality_min_grade: str = ""
  asset_quality_min_trades: int = 0
  asset_quality_filter: bool = False
  asset_quality_hurst_min: float = 0.50     # trend mode threshold
  asset_quality_ter_percentile: int = 25     # trend mode threshold
  asset_quality_hurst_max: float = 0.50     # meanrev mode threshold
  asset_quality_vr_max: float = 1.0         # meanrev mode threshold
  asset_quality_min_cr: float = 0.05        # meanrev mode threshold
  # Strategy Quality seed — PnL-based grade
  strat_quality_mode: str = "trend"
  strat_quality_min_grade: str = ""
  strat_quality_filter: bool = False
  # IS-WFE seed — real walk-forward efficiency using IS:OOS split
  is_wfe_is_pct: int = 60           # IS portion as % (60 means 60:40 split)
  is_wfe_min_trades: int = 0        # minimum trades in IS window for validity
  is_wfe_filter: bool = False       # True = remove assets with WFE below threshold
  is_wfe_min_wfe: float = 0.0       # minimum WFE to pass filter
  # Session Time Mask — pre-filter trades by hour×day before optimization
  session_mask_id: int | None = None  # FK to session_time_masks, None = no mask
  # ── Session Filter — inline trade-time filtering ──
  session_filter_enabled: bool = False
  session_filter_mode: str = "fixed"  # "fixed" | "auto" | "saved_mask"
  session_filter_exclude_days: list[int] = []   # 0=Mon..6=Sun
  session_filter_exclude_hours: list[int] = []  # 0-23
  session_filter_auto_min_trades: int = 10
  session_filter_auto_include_insufficient: bool = True
  session_filter_grade_on_cropped: bool = False


class MonteCarloRequest(BaseModel):
  batch_id: int | None = None
  run_id: int | None = None
  portfolio_id: int | None = None
  n_simulations: int = 1000
  ruin_threshold_pct: float = 50.0
  date_to: str | None = None  # OOS cutoff — only use trades before this date
  date_from: str | None = None  # OOS start — only use trades after this date
  min_trades: int = 0  # Exclude assets with fewer than N trades
  exclude_top_n: int = 0
  pf_min: float | None = None
  pf_max: float | None = None
  wr_min: float | None = None


class OptimizeLSRequest(BaseModel):
  batch_id: int
  n_longs: int = 3
  n_shorts: int = 3
  within_objective: str = "uncorrelated"
  combined_objective: str = "stability"
  corr_method: str = "standard"
  div_algo: str = "greedy"
  n_portfolios: int = 3
  date_to: str | None = None  # OOS cutoff — only use trades before this date
  date_from: str | None = None  # Start date — only use trades after this date
  min_trades: int = 0
  min_pf: float = 0.0
  min_wr: float = 0.0
  # Bybit size gate — drop assets whose trades exceed Bybit qty limits at target risk
  bybit_size_gate: bool = False
  bybit_size_gate_max_reject_pct: float = 0.0  # 0 = binary (any rejection = fail)
  # Post-hoc trading costs
  extra_slippage_pct: float = 0.0
  extra_commission_pct: float = 0.0
  funding_daily_pct: float = 0.0
  spread_pct: float = 0.0
  # Seed ranking
  seed_metrics: list[str] = ["sortino"]
  max_seeds: int = 20
  seed_weights: dict[str, float] | None = None  # per-metric weights {metric: weight}
  # Stability prioritization
  prioritize_stability: bool = False
  # Event-based L1 scoring
  event_mode: bool = False
  n_trades: int = 20
  staleness_days: int = 180
  # Trade-count constraints per direction
  tc_longs: dict | None = None  # {low_threshold, max_low, high_threshold, min_high}
  tc_shorts: dict | None = None
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
  # Benchmark seed score enhancements (0.0 = off, 0.0-0.5 range)
  benchmark_consistency_blend: float = 0.0
  benchmark_fragility_blend: float = 0.0
  benchmark_wr_harmony_blend: float = 0.0
  # Category filter — only include assets matching selected categories
  category_filter: list[str] = []
  # Portfolio diversity: max Jaccard overlap between candidates (None = off)
  max_overlap: float | None = None
  # Risk profile — applied to ALL trades per-asset before optimization
  risk_profile_mode: str = "fixed"
  risk_profile_value: float = 0.0
  risk_profile_initial_capital: float = 10_000.0
  risk_profile_multiplier: float = 2.0
  risk_profile_max_streak: int = 3
  risk_profile_martingale_compound: str = "off"
  risk_profile_martingale_scope: Literal["asset", "portfolio", "asset_batch", "batch"] = "asset"
  risk_profile_kelly_n: float = 2.0
  risk_profile_kelly_warmup: int = 20
  risk_profile_martingale_count_mode: Literal["full_position", "each_exit", "confirmed_tsl"] = "full_position"
  risk_profile_martingale_oos_reset: bool = True
  risk_profile_martingale_start_after: int = 0
  risk_profile_anti_martingale_reset_after: int = 0
  risk_profile_sizing_master: str = "portfolio"
  risk_profile_batch_configs: str | None = None
  risk_profile_auto_asset_configs: str | None = None
  # Boundary clipping — force-close IS trades at IS boundary close price
  clip_is: bool = True
  # Stage 1: Pre-filter (structural quality)
  prefilter_enabled: bool = False
  prefilter_mode: str = "trend"
  prefilter_hurst_min: float = 0.50
  prefilter_ter_percentile: int = 25
  prefilter_hurst_max: float = 0.50
  prefilter_vr_max: float = 1.0
  prefilter_min_crossing_rate: float = 0.05
  # Stage 3: DSR benchmark gate
  benchmark_dsr_gate: bool = False
  # Stage 4: AC post-filter
  ac_postfilter: bool = False
  ac_postfilter_type: str = "trend"
  ac_lag: int = 1
  # Max drawdown hard limit — reject portfolios exceeding this DD%
  max_allowed_drawdown_pct: float = 0.0  # 0 = disabled, e.g. 30.0 = reject >30% DD
  # Avg Range seed config — market structure consecutive range thresholds
  avg_range_bull_threshold: float = 0.0
  avg_range_bear_threshold: float = 0.0
  avg_range_all_threshold: float = 0.0
  avg_range_filter_mode: bool = False
  # Asset Quality seed — composite OHLCV grade + structural thresholds
  asset_quality_mode: str = "trend"
  asset_quality_grade_mode: str = "absolute"
  asset_quality_min_grade: str = ""
  asset_quality_min_trades: int = 0
  asset_quality_filter: bool = False
  asset_quality_hurst_min: float = 0.50
  asset_quality_ter_percentile: int = 25
  asset_quality_hurst_max: float = 0.50
  asset_quality_vr_max: float = 1.0
  asset_quality_min_cr: float = 0.05
  # Strategy Quality seed — PnL-based grade
  strat_quality_mode: str = "trend"
  strat_quality_min_grade: str = ""
  strat_quality_filter: bool = False
  # Session Time Mask — pre-filter trades by hour×day before optimization
  session_mask_id: int | None = None  # FK to session_time_masks, None = no mask
  # ── Session Filter — inline trade-time filtering ──
  session_filter_enabled: bool = False
  session_filter_mode: str = "fixed"  # "fixed" | "auto" | "saved_mask"
  session_filter_exclude_days: list[int] = []   # 0=Mon..6=Sun
  session_filter_exclude_hours: list[int] = []  # 0-23
  session_filter_auto_min_trades: int = 10
  session_filter_auto_include_insufficient: bool = True
  session_filter_grade_on_cropped: bool = False


class CompareRequest(BaseModel):
  batch_ids: list[int]


# --- Helpers ---

def _portfolio_metrics(
  daily_pnl, assets: list[str], trade_df: pd.DataFrame | None = None,
) -> dict:
  """Compute standard portfolio metrics for a set of assets.

  Expectancy metrics (PF, Win Rate, Avg Trade, Net Profit) are calculated
  at trade-level resolution when trade_df is available, merging multi-exit
  fills by position_id into positions.
  Time-series metrics (drawdown, stability, sortino, calmar, recovery) use
  daily PnL which is the standard approach in portfolio theory.

  trade_df: DataFrame with [Asset, Net Profit, position_id].
  """
  cols = [c for c in assets if c in daily_pnl.columns]
  if not cols:
    return {"net_profit": 0, "profit_factor": 0, "max_drawdown": 0, "stability": 0, "n_assets": 0}
  combined = daily_pnl[cols].sum(axis=1)
  equity = combined.cumsum()
  dd = float((equity - equity.cummax()).min())
  stab = float(calculate_stability(combined))

  # --- Expectancy metrics from trade-level data (position-merged) ---
  net = 0.0
  pf = 0.0
  total_trades = 0
  win_rate = 0.0
  avg_trade = 0.0
  if trade_df is not None and not trade_df.empty:
    ptf = trade_df[trade_df["Asset"].isin(cols)]
    if not ptf.empty and "position_id" in ptf.columns:
      pos_pnl = ptf.groupby(["Asset", "position_id"])["Net Profit"].sum()
      total_trades = len(pos_pnl)
      net = float(pos_pnl.sum())
      gp = float(pos_pnl[pos_pnl > 0].sum())
      gl = abs(float(pos_pnl[pos_pnl <= 0].sum()))
      pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)
      if total_trades > 0:
        wins = int((pos_pnl > 0).sum())
        win_rate = round(wins / total_trades * 100, 1)
        avg_trade = round(net / total_trades, 2)
    elif not ptf.empty:
      total_trades = len(ptf)
      net = float(ptf["Net Profit"].sum())
      gp = float(ptf.loc[ptf["Net Profit"] > 0, "Net Profit"].sum())
      gl = abs(float(ptf.loc[ptf["Net Profit"] <= 0, "Net Profit"].sum()))
      pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)
      if total_trades > 0:
        wins = int((ptf["Net Profit"] > 0).sum())
        win_rate = round(wins / total_trades * 100, 1)
        avg_trade = round(net / total_trades, 2)
  if total_trades == 0:
    net = float(combined.sum())
    gp = float(combined[combined > 0].sum())
    gl = float(abs(combined[combined < 0].sum()))
    pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)

  # --- Time-series metrics from daily PnL (standard approach) ---
  neg = combined[combined < 0]
  # Downside deviation = sqrt(mean(d^2))
  downside_dev = float(np.sqrt(np.mean(neg.values ** 2))) if len(neg) > 0 else 0.0
  daily_mean = float(combined.mean()) if len(combined) > 0 else 0.0
  # Annualize using 365 for crypto
  sortino = round(daily_mean / downside_dev * (365 ** 0.5), 2) if downside_dev > 0 else 0.0

  # Calmar = annualized return / |max DD|
  annual_ret = daily_mean * 365
  calmar = round(annual_ret / abs(dd), 2) if dd < 0 else 0.0

  # Recovery factor = net profit / |max DD|
  recovery = round(net / abs(dd), 2) if dd < 0 else 0.0

  return {
    "net_profit": round(net, 2), "profit_factor": pf,
    "max_drawdown": round(dd, 2), "stability": round(stab, 4), "n_assets": len(cols),
    "win_rate": win_rate, "total_trades": total_trades, "avg_trade": avg_trade,
    "sortino": sortino, "calmar_ratio": calmar, "recovery_factor": recovery,
  }


def _filter_daily_pnl(
  daily_pnl: pd.DataFrame, trades: list[BacktestTrade],
  min_trades: int = 0, min_pf: float = 0.0, min_wr: float = 0.0,
  max_pf: float = 0.0, cap_pf: bool = False,
  include_side: bool = False,
  run_batch_map: dict[int, int] | None = None,
  date_to: str | None = None,
) -> pd.DataFrame:
  """Remove columns (assets) that fail filter thresholds.

  Counts unique positions (grouped by position_id), not exit fills.
  A trade with TP1+TP2 exits shares one position_id → counts as 1 trade.

  include_side: when True, group trades by SYMBOL_Long/SYMBOL_Short
  to match suffixed column names in daily_pnl.
  run_batch_map: when provided (cross-strategy), tag asset keys with
  batch ID to match batch-tagged column names (SYMBOL~B{id}_Side).
  date_to: OOS cutoff safety net — drops trades/daily_pnl rows beyond this date.
  max_pf: when > 0, reject assets with PF above this (unless cap_pf is True).
  cap_pf: when True + max_pf > 0, keep those assets (capping happens at portfolio gate stage).
  """
  # Safety net: ensure no trade with entry beyond cutoff date leaks in
  # Do NOT truncate daily_pnl — IS trades may exit (realize PnL) after cutoff
  if date_to:
    cutoff = pd.Timestamp(date_to)
    _before = len(trades)
    trades = [t for t in trades if t.entry_time and pd.Timestamp(t.entry_time).date() <= cutoff.date()]
    logger.info("  _filter_daily_pnl: date_to=%s cutoff removed %d/%d trades", date_to, _before - len(trades), _before)

  # max_pf with cap_pf=True means "keep asset, cap later" — no filtering here
  _max_pf_filter = max_pf if (max_pf > 0 and not cap_pf) else 0.0
  if min_trades <= 0 and min_pf <= 0 and min_wr <= 0 and _max_pf_filter <= 0:
    return daily_pnl
  from collections import defaultdict

  from backend.analysis.daily_pnl import _asset_key
  # Group fills into positions by (asset_key, position_id) → total P&L
  pos_pnl: dict[str, dict[str, float]] = defaultdict(dict)
  for t in trades:
    pid = t.position_id if t.position_id else f"_solo_{t.id}"
    sym = _asset_key(t, include_side)
    # Add batch tag for cross-strategy (matches tagged column names)
    if run_batch_map and t.run_id in run_batch_map:
      bid = run_batch_map[t.run_id]
      parts = sym.rsplit("_", 1)
      if len(parts) == 2 and parts[1] in ("Long", "Short"):
        sym = f"{parts[0]}~B{bid}_{parts[1]}"
      else:
        sym = f"{sym}~B{bid}"
    if pid not in pos_pnl[sym]:
      pos_pnl[sym][pid] = 0.0
    pos_pnl[sym][pid] += (t.net_profit or 0.0)
  qualified = []
  _rej_no_trades = 0
  _rej_min_trades = 0
  _rej_min_pf = 0
  _rej_max_pf = 0
  _rej_min_wr = 0
  for sym in daily_pnl.columns:
    positions = pos_pnl.get(sym, {})
    count = len(positions)
    if count == 0:
      _rej_no_trades += 1
      continue
    if min_trades > 0 and count < min_trades:
      _rej_min_trades += 1
      continue
    wins = sum(1 for pnl in positions.values() if pnl > 0)
    gross_profit = sum(pnl for pnl in positions.values() if pnl > 0)
    gross_loss = sum(abs(pnl) for pnl in positions.values() if pnl <= 0)
    pf = min(gross_profit / gross_loss, 10.0) if gross_loss > 0 else 10.0
    if min_pf > 0 and pf < min_pf:
      _rej_min_pf += 1
      continue
    if _max_pf_filter > 0 and pf > _max_pf_filter:
      _rej_max_pf += 1
      continue
    wr = (wins / count * 100) if count > 0 else 0
    if min_wr > 0 and wr < min_wr:
      _rej_min_wr += 1
      continue
    qualified.append(sym)
  logger.info("  _filter_daily_pnl: %d/%d qualified | rejected: no_trades=%d, min_trades(%d)=%d, min_pf(%.1f)=%d, max_pf(%.1f)=%d, min_wr(%.0f%%)=%d",
              len(qualified), daily_pnl.shape[1],
              _rej_no_trades, min_trades, _rej_min_trades, min_pf, _rej_min_pf, _max_pf_filter, _rej_max_pf, min_wr, _rej_min_wr)
  return daily_pnl[qualified]


def _filter_bybit_size_gate(
  daily_pnl: pd.DataFrame,
  trades: list[BacktestTrade],
  max_reject_pct: float = 0.0,
  include_side: bool = False,
  run_batch_map: dict[int, int] | None = None,
) -> pd.DataFrame:
  """Drop assets whose scaled trade quantities violate Bybit instrument limits.

  By the time this runs, trades have already been through _apply_risk_scaling()
  so t.quantity reflects the real position size at the target risk level
  (including martingale/Kelly multipliers).

  max_reject_pct: 0 = binary mode (any single rejection -> drop asset).
                  >0 = rate mode (drop if rejected% > threshold).
  """
  from backend.api.routes.assets import _instrument_specs_cache, _instrument_specs_ts
  from backend.analysis.daily_pnl import _asset_key
  import time as _time

  # Get or refresh Bybit specs
  specs = _instrument_specs_cache
  if not specs or (_time.time() - _instrument_specs_ts) > 3600:
    from backend.api.routes.assets import get_instrument_specs
    result = get_instrument_specs()
    specs = result.get("specs", {})
  if not specs:
    logger.warning("  _filter_bybit_size_gate: no instrument specs available, skipping")
    return daily_pnl

  # Group trades by asset key (same keys as daily_pnl columns)
  from collections import defaultdict
  asset_trades: dict[str, list] = defaultdict(list)
  for t in trades:
    if not t.quantity or not t.symbol:
      continue
    sym = _asset_key(t, include_side)
    if run_batch_map and t.run_id in run_batch_map:
      bid = run_batch_map[t.run_id]
      parts = sym.rsplit("_", 1)
      if len(parts) == 2 and parts[1] in ("Long", "Short"):
        sym = f"{parts[0]}~B{bid}_{parts[1]}"
      else:
        sym = f"{sym}~B{bid}"
    asset_trades[sym].append(t)

  qualified = []
  _rej_exceed = 0
  for col in daily_pnl.columns:
    col_trades = asset_trades.get(col, [])
    if not col_trades:
      qualified.append(col)  # no trade data -> keep (can't check)
      continue

    # Extract base symbol for spec lookup (strip _Long/_Short and ~B{id} tags)
    base = col.split("~")[0]
    for sfx in ("_Long", "_Short"):
      if base.endswith(sfx):
        base = base[:-len(sfx)]
        break
    spec = specs.get(base)
    if not spec:
      qualified.append(col)  # no spec for symbol -> keep
      continue

    max_qty = spec.get("max_qty", 1e18)
    min_qty = spec.get("min_qty", 0)
    n_total = len(col_trades)
    n_rejected = 0
    for t in col_trades:
      qty = abs(float(t.quantity))
      if qty > max_qty or qty < min_qty:
        n_rejected += 1

    if n_rejected == 0:
      qualified.append(col)
      continue

    reject_rate = n_rejected / n_total * 100 if n_total > 0 else 0
    if max_reject_pct <= 0:
      # Binary mode: any rejection -> drop
      _rej_exceed += 1
      continue
    elif reject_rate > max_reject_pct:
      _rej_exceed += 1
      continue
    else:
      qualified.append(col)

  dropped = daily_pnl.shape[1] - len(qualified)
  if dropped > 0:
    logger.info("  _filter_bybit_size_gate: dropped %d/%d assets (mode=%s, threshold=%.1f%%)",
                dropped, daily_pnl.shape[1],
                "binary" if max_reject_pct <= 0 else f"rate>{max_reject_pct}%",
                max_reject_pct)
  return daily_pnl[qualified]


def _filter_by_category(
  daily_pnl: pd.DataFrame, category_filter: list[str], db: Session,
) -> pd.DataFrame:
  """Remove columns whose base symbol doesn't match any selected category.

  Empty category_filter means no filtering (all assets pass).
  Column names can be 'BTCUSDT', 'BTCUSDT_Long', or 'BTCUSDT_Short'.
  """
  if not category_filter:
    return daily_pnl
  cat_map = get_all_categories(db)
  keep = []
  for col in daily_pnl.columns:
    base = col.rsplit("_", 1)[0] if col.endswith(("_Long", "_Short")) else col
    cats = cat_map.get(base, [])
    if any(c in category_filter for c in cats):
      keep.append(col)
  return daily_pnl[keep] if keep else daily_pnl[[]].head(0)


def _compute_trade_counts(
  trades: list[BacktestTrade], include_side: bool = False,
) -> dict[str, int]:
  """Count unique positions per asset (not individual exit fills).

  Multi-exit trades (e.g. TP1 + TP2) share a position_id → count as 1.

  include_side: when True, keys become SYMBOL_Long/SYMBOL_Short.
  """
  from collections import defaultdict

  from backend.analysis.daily_pnl import _asset_key
  seen: dict[str, set] = defaultdict(set)
  for t in trades:
    key = t.position_id if t.position_id else f"_solo_{t.id}"
    sym = _asset_key(t, include_side)
    seen[sym].add(key)
  return {sym: len(pids) for sym, pids in seen.items()}


def _compute_pool_stats(
  daily_pnl: pd.DataFrame, trades: list[BacktestTrade],
  include_side: bool = False,
  run_batch_map: dict[int, int] | None = None,
) -> dict:
  """Compute PF distribution stats for the optimizer pool (after filtering).

  include_side: when True, keys become SYMBOL_Long/SYMBOL_Short to match
  side-suffixed column names in daily_pnl.
  run_batch_map: when provided (cross-strategy), tags asset keys with
  batch ID to match batch-tagged column names (SYMBOL~B{id}_Side).
  """
  from collections import defaultdict
  from backend.analysis.daily_pnl import _asset_key
  stats: dict[str, dict] = defaultdict(lambda: {"gp": 0.0, "gl": 0.0})
  for t in trades:
    sym = _asset_key(t, include_side)
    # Add batch tag for cross-strategy (matches tagged column names)
    if run_batch_map and t.run_id in run_batch_map:
      bid = run_batch_map[t.run_id]
      parts = sym.rsplit("_", 1)
      if len(parts) == 2 and parts[1] in ("Long", "Short"):
        sym = f"{parts[0]}~B{bid}_{parts[1]}"
      else:
        sym = f"{sym}~B{bid}"
    if sym in daily_pnl.columns:
      s = stats[sym]
      if (t.net_profit or 0) > 0:
        s["gp"] += t.net_profit
      else:
        s["gl"] += abs(t.net_profit or 0)
  pf_values = []
  for sym in daily_pnl.columns:
    s = stats.get(sym)
    if s:
      pf = min(s["gp"] / s["gl"], 10.0) if s["gl"] > 0 else 0.0
      pf_values.append(round(pf, 3))
    else:
      pf_values.append(0.0)
  pf_values.sort()
  n = len(pf_values)
  return {
    "total": n,
    "median_pf": round(pf_values[n // 2], 3) if n > 0 else 0,
    "mean_pf": round(sum(pf_values) / n, 3) if n > 0 else 0,
    "pf_above_1_5": sum(1 for v in pf_values if v >= 1.5),
    "pf_above_2_0": sum(1 for v in pf_values if v >= 2.0),
    "pf_values": pf_values,
  }


def _apply_avg_range_scoring(
  batch_id: int,
  daily_pnl: pd.DataFrame,
  external_scores: dict | None,
  bull_threshold: float,
  bear_threshold: float,
  all_threshold: float,
  filter_mode: bool,
  include_side: bool = False,
  date_to: str | None = None,
  db: Session | None = None,
) -> tuple[pd.DataFrame, dict | None]:
  """Score/filter assets by avg consecutive same-sided range metrics.

  When date_to is set: recomputes from IS-only OHLCV close prices to avoid
  OOS data leakage. Uses close-to-close direction streaks.
  When date_to is NOT set: reads stored avg_consec_* from BacktestRun.metrics.
  If filter_mode: removes assets below ALL nonzero thresholds from daily_pnl.
  Always adds external_scores["avg_range"] for seed ranking.

  Returns (daily_pnl, external_scores) — possibly filtered.
  """
  import json as _json

  # Map daily_pnl column names to base symbols
  def _base_symbol(col: str) -> str:
    return col.replace("_Long", "").replace("_Short", "") if include_side else col

  sym_range: dict[str, dict[str, float]] = {}

  if date_to and db is not None:
    # IS-only path: recompute from OHLCV close prices before cutoff
    from itertools import groupby as _groupby
    from backend.db.database import ohlcv_router
    from backend.utils.tf_resolver import canonical_tf as _canonical_tf
    from sqlalchemy import text

    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    tf = _canonical_tf(batch.timeframe) if batch else "D"
    cutoff_iso = (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)).isoformat()

    unique_syms = {_base_symbol(c) for c in daily_pnl.columns}
    ohlcv_engine = ohlcv_router.get_engine(tf)
    with ohlcv_engine.connect() as conn:
      for sym in unique_syms:
        rows = conn.execute(text(
          "SELECT close FROM ohlcv_data "
          "WHERE symbol = :sym AND timeframe = :tf AND timestamp < :cutoff "
          "ORDER BY timestamp"
        ), {"sym": sym, "tf": tf, "cutoff": cutoff_iso}).fetchall()
        if len(rows) < 10:
          sym_range[sym] = {"bull": 0.0, "bear": 0.0, "all": 0.0}
          continue
        closes = [r[0] for r in rows]
        directions = [1 if closes[i] > closes[i - 1] else -1
                      for i in range(1, len(closes))]
        bull_runs: list[int] = []
        bear_runs: list[int] = []
        for d, g in _groupby(directions):
          run_len = sum(1 for _ in g)
          if d == 1:
            bull_runs.append(run_len)
          else:
            bear_runs.append(run_len)
        avg_bull = float(np.mean(bull_runs)) if bull_runs else 0.0
        avg_bear = float(np.mean(bear_runs)) if bear_runs else 0.0
        if avg_bull > 0 and avg_bear > 0:
          avg_all = (avg_bull + avg_bear) / 2.0
        else:
          avg_all = max(avg_bull, avg_bear)
        sym_range[sym] = {"bull": avg_bull, "bear": avg_bear, "all": avg_all}
    logger.info("Avg Range: IS-only recompute for %d symbols (cutoff=%s)", len(unique_syms), date_to)
  else:
    # Full-range path: read stored metrics (original behavior)
    from backend.db.database import SessionLocal
    from backend.db.models import BacktestRun as _BR

    with SessionLocal() as _db:
      runs = _db.query(_BR.symbol, _BR.metrics).filter(
        _BR.batch_id == batch_id,
        _BR.status == "completed",
      ).all()

    for symbol, metrics_json in runs:
      try:
        m = _json.loads(metrics_json) if isinstance(metrics_json, str) else (metrics_json or {})
      except (ValueError, TypeError):
        continue
      sym_range[symbol] = {
        "bull": float(m.get("avg_consec_bull", 0)),
        "bear": float(m.get("avg_consec_bear", 0)),
        "all": float(m.get("avg_consec_all", 0)),
      }

  # Filter mode: remove assets below thresholds
  if filter_mode:
    keep = []
    for col in daily_pnl.columns:
      base = _base_symbol(col)
      r = sym_range.get(base, {"bull": 0, "bear": 0, "all": 0})
      ok = True
      if bull_threshold > 0 and r["bull"] < bull_threshold:
        ok = False
      if bear_threshold > 0 and r["bear"] < bear_threshold:
        ok = False
      if all_threshold > 0 and r["all"] < all_threshold:
        ok = False
      if ok:
        keep.append(col)
    daily_pnl = daily_pnl[keep]

  # Build seed scores: higher = better candidate
  avg_range_scores: dict[str, float] = {}
  for col in daily_pnl.columns:
    base = _base_symbol(col)
    r = sym_range.get(base, {"bull": 0, "bear": 0, "all": 0})
    score = 0.0
    if bull_threshold > 0:
      score += max(0, r["bull"] - bull_threshold)
    else:
      score += r["bull"]
    if bear_threshold > 0:
      score += max(0, r["bear"] - bear_threshold)
    else:
      score += r["bear"]
    if all_threshold > 0:
      score += max(0, r["all"] - all_threshold)
    else:
      score += r["all"]
    avg_range_scores[col] = score

  if external_scores is None:
    external_scores = {}
  external_scores["avg_range"] = avg_range_scores

  return daily_pnl, external_scores


def _apply_quality_scoring(
  batch_id: int,
  daily_pnl: pd.DataFrame,
  external_scores: dict | None,
  req: "OptimizeRequest | OptimizeLSRequest",
  include_side: bool = False,
  trades: list | None = None,
  db: Session | None = None,
) -> tuple[pd.DataFrame, dict | None]:
  """Score/filter assets using composite Asset Quality and Strategy Quality seeds.

  Asset Quality: OHLCV grade + structural thresholds (Hurst/TER or Hurst/VR/CR).
  Strategy Quality: PnL-based grade.

  When req.date_to is set and db is provided: recomputes ALL metrics from IS-only
  data (OHLCV before cutoff, trades before cutoff) to avoid OOS data leakage.
  When req.date_to is NOT set: reads stored metrics from BacktestRun (original behavior).
  """
  import json as _json
  from backend.analysis.seed_ranker import grade_letter, grade_letter_zoomed, GRADE_ORDER

  sym_metrics: dict[str, dict] = {}

  if req.date_to and db is not None:
    # IS-only path: recompute Asset Quality from OHLCV before cutoff
    from backend.api.routes.batch_backtest import _compute_structural_metrics_from_prices
    from backend.db.database import ohlcv_router
    from backend.utils.tf_resolver import canonical_tf as _canonical_tf
    from sqlalchemy import text as _text
    from collections import Counter

    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    tf = _canonical_tf(batch.timeframe) if batch else "D"
    cutoff_iso = (datetime.strptime(req.date_to, "%Y-%m-%d") + timedelta(days=1)).isoformat()
    pool_size = max(len(daily_pnl.columns), 2)

    # Count IS trades per symbol (replaces stored total_trades)
    is_trade_counts: dict[str, int] = Counter()
    if trades:
      for t in trades:
        is_trade_counts[t.symbol] = is_trade_counts.get(t.symbol, 0) + 1

    def _base_sym(col: str) -> str:
      return col.replace("_Long", "").replace("_Short", "") if include_side else col

    unique_syms = {_base_sym(c) for c in daily_pnl.columns}

    ohlcv_engine = ohlcv_router.get_engine(tf)
    with ohlcv_engine.connect() as conn:
      for sym in unique_syms:
        rows = conn.execute(_text(
          "SELECT close FROM ohlcv_data "
          "WHERE symbol = :sym AND timeframe = :tf AND timestamp < :cutoff "
          "ORDER BY timestamp"
        ), {"sym": sym, "tf": tf, "cutoff": cutoff_iso}).fetchall()
        if len(rows) < 50:
          sym_metrics[sym] = {"total_trades": is_trade_counts.get(sym, 0)}
          continue
        close = np.array([r[0] for r in rows], dtype=float)
        m = _compute_structural_metrics_from_prices(close, pool_size=pool_size)
        if m:
          m["total_trades"] = is_trade_counts.get(sym, 0)
          sym_metrics[sym] = m
        else:
          sym_metrics[sym] = {"total_trades": is_trade_counts.get(sym, 0)}
    logger.info("Asset Quality: IS-only recompute for %d symbols (cutoff=%s)", len(unique_syms), req.date_to)

    # IS-only path: recompute Strategy Quality from IS-only trades
    if trades:
      from collections import defaultdict
      from backend.analysis.seed_ranker import (
        trend_efficiency_ratio, hurst_exponent, gain_to_pain_ratio,
        autocorrelation, variance_ratio, half_life, _crossing_rate,
      )

      sym_trades: dict[str, list] = defaultdict(list)
      for t in trades:
        sym_trades[t.symbol].append(t)

      for sym, st in sym_trades.items():
        exits = [(t.exit_time, t.net_profit) for t in st
                 if t.exit_time and t.net_profit is not None]
        if len(exits) < 3:
          continue
        tdf = pd.DataFrame(exits, columns=["date", "pnl"])
        tdf["date"] = pd.to_datetime(tdf["date"]).dt.date
        dpnl = tdf.groupby("date")["pnl"].sum().values.astype(float)
        if len(dpnl) < 10:
          continue
        # Same formulas as _compute_structural_metrics_from_pnl
        equity = np.cumsum(dpnl)
        ter = trend_efficiency_ratio(equity)
        h = hurst_exponent(dpnl)
        gpr = gain_to_pain_ratio(dpnl)
        ac1 = autocorrelation(dpnl, lag=1)
        sig_gpr = 1.0 / (1.0 + np.exp(-gpr))
        t_score = float(np.clip(
          0.30 * ter + 0.25 * max(0.0, (h - 0.5)) * 2.0
          + 0.25 * sig_gpr + 0.20 * max(0.0, ac1), 0.0, 1.0,
        ))
        # meanrev score
        vr = variance_ratio(dpnl, lag=5)
        hl = half_life(dpnl)
        cr = _crossing_rate(dpnl)
        if len(dpnl) >= 20 and ac1 < 0:
          mri = float(np.clip((-ac1) * (1.0 - ter) * cr, 0.0, 1.0))
        else:
          mri = 0.0
        norm_hl = 1.0 / (1.0 + hl / 10.0) if hl < float('inf') else 0.0
        m_score = float(np.clip(
          0.25 * mri + 0.25 * max(0.0, (0.5 - h)) * 2.0
          + 0.20 * max(0.0, 1.0 - vr) + 0.15 * norm_hl + 0.15 * cr, 0.0, 1.0,
        ))
        if sym in sym_metrics:
          sym_metrics[sym]["pnl_trend_score"] = round(t_score, 4)
          sym_metrics[sym]["pnl_meanrev_score"] = round(m_score, 4)
        else:
          sym_metrics[sym] = {"pnl_trend_score": round(t_score, 4), "pnl_meanrev_score": round(m_score, 4)}
      logger.info("Strategy Quality: IS-only recompute for %d symbols (cutoff=%s)", len(sym_trades), req.date_to)
  else:
    # Full-range path: read stored metrics (original behavior)
    from backend.db.database import SessionLocal as _SL

    with _SL() as _db:
      runs = _db.query(BacktestRun.symbol, BacktestRun.metrics).filter(
        BacktestRun.batch_id == batch_id,
        BacktestRun.status == "completed",
      ).all()

    for symbol, metrics_json in runs:
      try:
        m = _json.loads(metrics_json) if isinstance(metrics_json, str) else (metrics_json or {})
      except (ValueError, TypeError):
        continue
      sym_metrics[symbol] = {
        "trend_score": float(m.get("trend_score", 0)),
        "meanrev_score": float(m.get("meanrev_score", 0)),
        "pnl_trend_score": float(m.get("pnl_trend_score", 0)),
        "pnl_meanrev_score": float(m.get("pnl_meanrev_score", 0)),
        "total_trades": int(m.get("total_trades", 0)),
        "Hurst": float(m.get("Hurst", 0)),
        "TER": float(m.get("TER", 0)),
        "VR5": float(m.get("VR5", 0)),
        "CrossRate": float(m.get("CrossRate", 0)),
      }

  def _base(col: str) -> str:
    return col.replace("_Long", "").replace("_Short", "") if include_side else col

  if external_scores is None:
    external_scores = {}

  # --- Asset Quality seed ---
  if "asset_quality" in req.seed_metrics:
    mode = req.asset_quality_mode  # "trend" or "meanrev"
    score_key = "trend_score" if mode == "trend" else "meanrev_score"

    # Min trades filter
    if req.asset_quality_min_trades > 0:
      keep = [c for c in daily_pnl.columns
              if sym_metrics.get(_base(c), {}).get("total_trades", 0) >= req.asset_quality_min_trades]
      daily_pnl = daily_pnl[keep]

    if req.asset_quality_filter:
      keep = []
      for col in daily_pnl.columns:
        sm = sym_metrics.get(_base(col), {})
        ok = True
        # Structural thresholds
        if mode == "trend":
          if sm.get("Hurst", 0) < req.asset_quality_hurst_min:
            ok = False
        else:
          if sm.get("Hurst", 0) > req.asset_quality_hurst_max:
            ok = False
          if sm.get("VR5", 0) >= req.asset_quality_vr_max:
            ok = False
          if sm.get("CrossRate", 0) < req.asset_quality_min_cr:
            ok = False
        # Grade threshold
        if ok and req.asset_quality_min_grade:
          raw = sm.get(score_key, 0.0)
          all_scores = [sym_metrics.get(_base(c), {}).get(score_key, 0.0) for c in daily_pnl.columns]
          g = grade_letter_zoomed(raw, all_scores) if req.asset_quality_grade_mode == "autozoom" else grade_letter(raw)
          if GRADE_ORDER.get(g, 0) < GRADE_ORDER.get(req.asset_quality_min_grade, 0):
            ok = False
        if ok:
          keep.append(col)
      daily_pnl = daily_pnl[keep]

    # TER percentile filter (trend mode only, when filter ON)
    if req.asset_quality_filter and mode == "trend" and daily_pnl.shape[1] > 0:
      import numpy as np
      ter_vals = [sym_metrics.get(_base(c), {}).get("TER", 0.0) for c in daily_pnl.columns]
      if ter_vals:
        cutoff = float(np.percentile(ter_vals, req.asset_quality_ter_percentile))
        keep = [c for c in daily_pnl.columns if sym_metrics.get(_base(c), {}).get("TER", 0.0) >= cutoff]
        daily_pnl = daily_pnl[keep]

    # Register score for ranking
    seed_scores: dict[str, float] = {}
    for col in daily_pnl.columns:
      seed_scores[col] = sym_metrics.get(_base(col), {}).get(score_key, 0.0)
    external_scores["asset_quality"] = seed_scores

  # --- Strategy Quality seed ---
  if "strat_quality" in req.seed_metrics:
    mode = req.strat_quality_mode
    score_key = "pnl_trend_score" if mode == "trend" else "pnl_meanrev_score"

    if req.strat_quality_filter and req.strat_quality_min_grade:
      keep = []
      all_scores = [sym_metrics.get(_base(c), {}).get(score_key, 0.0) for c in daily_pnl.columns]
      for col in daily_pnl.columns:
        raw = sym_metrics.get(_base(col), {}).get(score_key, 0.0)
        # PnL grades always use absolute (no zoom)
        g = grade_letter(raw)
        if GRADE_ORDER.get(g, 0) >= GRADE_ORDER.get(req.strat_quality_min_grade, 0):
          keep.append(col)
      daily_pnl = daily_pnl[keep]

    seed_scores_s: dict[str, float] = {}
    for col in daily_pnl.columns:
      seed_scores_s[col] = sym_metrics.get(_base(col), {}).get(score_key, 0.0)
    external_scores["strat_quality"] = seed_scores_s

  return daily_pnl, external_scores


def _prepare_benchmark(
  trades: list[BacktestTrade], daily_pnl: pd.DataFrame,
  source: str, split_method: str, threshold: float,
  elite_target: float, exclude_fragile: bool,
  consistency_blend: float = 0.0,
  fragility_blend: float = 0.0,
  wr_harmony_blend: float = 0.0,
  dsr_gate: bool = False,
  daily_pnl_full: "pd.DataFrame | None" = None,
  include_side: bool = False,
) -> tuple[pd.DataFrame, dict]:
  """Compute benchmark robustness, optionally filter fragile assets, return seed scores."""
  from backend.analysis.daily_pnl import _asset_key
  valid_symbols = set(daily_pnl.columns)
  relevant = [t for t in trades if _asset_key(t, include_side) in valid_symbols]
  rows = [{"Asset": _asset_key(t, include_side), "Exit Time": t.exit_time, "Net Profit": t.net_profit} for t in relevant]
  if not rows:
    return daily_pnl, {}
  df = pd.DataFrame(rows)
  stats, _ = compute_benchmark_robustness(
    df, source_asset=source, split_method=split_method,
    validation_threshold=threshold, elite_target=elite_target,
  )
  if exclude_fragile:
    keep = {a for a, s in stats.items() if s.get("status") not in ("Weak", "Fragile")}
    daily_pnl = daily_pnl[[c for c in daily_pnl.columns if c in keep]]
  bm_scores = compute_benchmark_seed_scores(
    stats,
    consistency_blend=consistency_blend,
    fragility_blend=fragility_blend,
    wr_harmony_blend=wr_harmony_blend,
    dsr_gate=dsr_gate,
    daily_pnl=daily_pnl_full if daily_pnl_full is not None else daily_pnl,
  )
  return daily_pnl, {"benchmark": bm_scores}


def _get_batch_risk_amount(batch_id: int, db: Session) -> float:
  """Read the batch_risk_amount from the batch parameters JSON.

  Non-normalized batches store batch_risk_amount=0 — fall through to
  risk_value so the scaler can still compute a valid scaling factor.
  """
  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if not batch or not batch.parameters:
    return 100.0  # default for pre-normalization batches
  try:
    params = json.loads(batch.parameters) if isinstance(batch.parameters, str) else batch.parameters
    amount = float(params.get("batch_risk_amount", 0))
    if amount > 0:
      return amount
    # Non-normalized batches: fall through to risk_value
    return float(params.get("risk_value", 100.0))
  except (json.JSONDecodeError, TypeError, ValueError):
    return 100.0


def _apply_risk_scaling(
  trades: list[BacktestTrade], req, db: Session,
  include_side: bool = False,
  run_batch_map: dict[int, int] | None = None,
  return_is_end_state: bool = False,
  initial_is_end_state: dict | None = None,
) -> list[BacktestTrade] | tuple:
  """Apply risk profile scaling to all trades per-asset before optimization.

  Modifies net_profit in-place after detaching from session.
  If mode is "fixed" and value is 0, no scaling occurs (uses batch risk as-is).

  When run_batch_map is provided and batches have different risk amounts,
  trades are grouped by source batch and scaled independently.

  If return_is_end_state=True, returns (trades, is_end_state_dict) where
  is_end_state_dict contains per-group IS-ending streak values.
  If initial_is_end_state is provided, those values seed the OOS segment.
  """
  _log = logger  # reuse module-level logger

  # Ensure trades are detached from DB session
  for t in trades:
    if hasattr(t, '_sa_instance_state'):
      try:
        db.expunge(t)
      except Exception as _exc:
        logger.debug("swallowed: %s", _exc)  # already detached (e.g. by stress slippage)

  from backend.analysis.trade_scaler import scale_backtest_trades

  # Build run_id → batch_id map for batch-aware martingale scopes
  # Only rebuild if the caller didn't provide one (e.g. portfolio_analysis
  # passes a pre-built map; cross_strategy uses synthetic negative IDs).
  if run_batch_map is None:
    from backend.db.models import BacktestRun
    batch_ids_list = getattr(req, 'batch_ids', []) or []
    single_bid = getattr(req, 'batch_id', None)
    all_bids = batch_ids_list if batch_ids_list else ([single_bid] if single_bid else [])
    if all_bids:
      runs = db.query(BacktestRun.id, BacktestRun.batch_id).filter(
        BacktestRun.batch_id.in_(all_bids),
      ).all()
      run_batch_map = {r.id: r.batch_id for r in runs} if runs else None

  # ── Per-batch sizing master ──
  sizing_master = getattr(req, 'risk_profile_sizing_master', 'portfolio')
  raw_batch_configs = getattr(req, 'risk_profile_batch_configs', None)
  parsed_batch_configs: dict[int, dict] | None = None
  if sizing_master == "per_batch" and raw_batch_configs:
    import json as _json
    try:
      parsed_batch_configs = {int(k): v for k, v in _json.loads(raw_batch_configs).items()}
    except Exception:
      _log.warning("  _apply_risk_scaling: failed to parse batch_configs, falling back to portfolio mode")
      sizing_master = "portfolio"

  if sizing_master == "per_batch" and parsed_batch_configs and run_batch_map:
    _log.debug("  _apply_risk_scaling: PER-BATCH mode with %d batch configs", len(parsed_batch_configs))

    # Group trades by batch
    from collections import defaultdict
    batch_groups: dict[int, list] = defaultdict(list)
    ungrouped: list = []
    for t in trades:
      rid = getattr(t, 'run_id', None)
      bid = run_batch_map.get(rid) if rid is not None else None
      if bid is not None:
        batch_groups[bid].append(t)
      else:
        ungrouped.append(t)

    result: list = []
    # Tier 1.1 L02: collect per-batch is_end_state so per-batch mode matches
    # the portfolio-mode round-trip. Keys use the batch-scoped form (e.g.
    # "{batch_id}_{symbol}_{side}") emitted by scale_backtest_trades under
    # martingale_scope="asset_batch".
    merged_is_end_state: dict[str, dict] = {}
    for bid, group in batch_groups.items():
      cfg = parsed_batch_configs.get(bid, {})
      b_mode = cfg.get("mode", "fixed")
      b_value = float(cfg.get("value", 0))

      # Skip scaling if fixed with no override
      if b_mode == "fixed" and b_value <= 0:
        result.extend(group)
        continue

      # Martingale modes locked to "asset_batch" in per-batch master
      b_is_mart = b_mode in ("martingale", "anti_martingale", "inv_martingale", "inv_anti_martingale")
      b_scope = "asset_batch" if b_is_mart else "asset"

      b_kwargs = dict(
        mode=b_mode,
        risk_value=b_value,
        initial_capital=float(cfg.get("initialCapital", 10000)),
        multiplier=float(cfg.get("multiplier", 2.0)),
        max_streak=int(cfg.get("maxStreak", 3)),
        kelly_n=float(cfg.get("kellyN", 2.0)),
        kelly_warmup=int(cfg.get("kellyWarmup", 20)),
        kelly_scope=cfg.get("kellyScope", "portfolio"),
        kelly_lookback=int(cfg.get("kellyLookback", 0)),
        kelly_cap=float(cfg.get("kellyCap", 0.0)),
        include_side=include_side,
        oos_date=getattr(req, 'date_to', None),
        martingale_compound=cfg.get("martingaleCompound", "off"),
        martingale_scope=b_scope,
        run_batch_map=run_batch_map,
        martingale_count_mode=cfg.get("martingaleCountMode", "full_position"),
        martingale_oos_reset=cfg.get("martingaleOosReset", True),
        martingale_start_after=int(cfg.get("martingaleStartAfter", 0)),
        anti_martingale_reset_after=int(cfg.get("antiMartingaleResetAfter", 0)),
        # Thread reload state so initial_is_end_state round-trips in per-batch mode too
        initial_is_end_state=initial_is_end_state,
      )
      batch_risk = _get_batch_risk_amount(bid, db)
      if batch_risk > 0:
        if return_is_end_state:
          scaled, b_state = scale_backtest_trades(
            group, batch_risk, return_is_end_state=True, **b_kwargs,
          )
          result.extend(scaled)
          if b_state:
            merged_is_end_state.update(b_state)
        else:
          result.extend(scale_backtest_trades(group, batch_risk, **b_kwargs))
      else:
        result.extend(group)

    # Ungrouped trades: no scaling (no config)
    result.extend(ungrouped)
    if return_is_end_state:
      return result, merged_is_end_state
    return result

  # ── Auto-risk per-asset mode ──
  raw_auto_configs = getattr(req, 'risk_profile_auto_asset_configs', None)
  if raw_auto_configs:
    import json as _json_auto
    try:
      auto_asset_cfgs: dict[str, dict] = _json_auto.loads(raw_auto_configs)
    except Exception:
      _log.warning("  _apply_risk_scaling: failed to parse auto_asset_configs, falling back to portfolio mode")
      auto_asset_cfgs = {}

    if auto_asset_cfgs:
      _log.debug("  _apply_risk_scaling: AUTO-RISK PER-ASSET mode for %d entries", len(auto_asset_cfgs))

      # Detect if composite keys are used (multi-batch: "SYMBOL__batchID")
      has_composite_keys = any("__batch" in k for k in auto_asset_cfgs)

      # Group trades by (symbol, batch_id) if composite keys, else by symbol only
      from collections import defaultdict as _dd
      trade_groups: dict[str, list] = _dd(list)
      for t in trades:
        sym = getattr(t, 'symbol', '') or ''
        if has_composite_keys and run_batch_map:
          rid = getattr(t, 'run_id', None)
          bid = run_batch_map.get(rid, 0) if rid is not None else 0
          group_key = f"{sym}__batch{bid}"
        else:
          group_key = sym
        trade_groups[group_key].append(t)

      # Resolve batch risks for ratio-based scaling
      batch_id_single = getattr(req, 'batch_id', None)
      batch_ids_list = getattr(req, 'batch_ids', []) or []
      all_bids_auto = batch_ids_list if batch_ids_list else ([batch_id_single] if batch_id_single else [])
      batch_risks_auto = {bid: _get_batch_risk_amount(bid, db) for bid in all_bids_auto} if all_bids_auto else {}
      default_batch_risk = batch_risks_auto.get(all_bids_auto[0], 0) if all_bids_auto else 0

      # Global defaults from request (for the dominant mode fallback)
      global_initial_capital = getattr(req, 'risk_profile_initial_capital', 10000.0)
      global_multiplier = getattr(req, 'risk_profile_multiplier', 2.0)
      global_compound = getattr(req, 'risk_profile_martingale_compound', 'off')
      global_scope = getattr(req, 'risk_profile_martingale_scope', 'asset')
      global_count_mode = getattr(req, 'risk_profile_martingale_count_mode', 'full_position')
      global_oos_reset = getattr(req, 'risk_profile_martingale_oos_reset', True)

      result_auto: list = []
      for group_key, group_trades in trade_groups.items():
        # Look up: try composite key first, then plain symbol
        acfg = auto_asset_cfgs.get(group_key, None)
        if acfg is None and "__batch" in group_key:
          plain_sym = group_key.split("__batch")[0]
          acfg = auto_asset_cfgs.get(plain_sym, None)
        if acfg is None:
          # Not in auto-config — use the dominant mode from request
          acfg = {
            "mode": req.risk_profile_mode,
            "value": req.risk_profile_value,
            "max_streak": getattr(req, 'risk_profile_max_streak', 3),
            "start_after": getattr(req, 'risk_profile_martingale_start_after', 0),
            "reset_after": getattr(req, 'risk_profile_anti_martingale_reset_after', 0),
          }

        a_mode = acfg.get("mode", "fixed")
        # Pick the right base risk: mart modes use valueMartingale, fixed uses valueFixed
        a_is_mart = a_mode in ("martingale", "anti_martingale", "inv_martingale", "inv_anti_martingale")
        a_value = float(acfg.get("valueMartingale" if a_is_mart else "valueFixed", acfg.get("value", 0)))

        if a_mode == "fixed" and a_value <= 0:
          result_auto.extend(group_trades)
          continue

        a_kwargs = dict(
          mode=a_mode,
          risk_value=a_value,
          initial_capital=float(acfg.get("initialCapital", global_initial_capital)),
          multiplier=float(acfg.get("multiplier", global_multiplier)),
          max_streak=int(acfg.get("max_streak", acfg.get("maxStreak", 3))),
          include_side=include_side,
          oos_date=getattr(req, 'date_to', None),
          martingale_compound=acfg.get("martingaleCompound", global_compound),
          martingale_scope=global_scope,
          run_batch_map=run_batch_map,
          martingale_count_mode=acfg.get("martingaleCountMode", global_count_mode),
          martingale_oos_reset=acfg.get("martingaleOosReset", global_oos_reset),
          martingale_start_after=int(acfg.get("start_after", acfg.get("martingaleStartAfter", 0))),
          anti_martingale_reset_after=int(acfg.get("reset_after", acfg.get("antiMartingaleResetAfter", 0))),
        )

        # Determine batch risk for this group's trades
        if run_batch_map and batch_risks_auto:
          trade_rids = {getattr(t, 'run_id', None) for t in group_trades}
          trade_bids = {run_batch_map.get(rid) for rid in trade_rids if rid is not None}
          trade_bids.discard(None)
          if trade_bids:
            batch_risk = batch_risks_auto.get(next(iter(trade_bids)), default_batch_risk)
          else:
            batch_risk = default_batch_risk
        else:
          batch_risk = default_batch_risk

        _log.debug("  _apply_risk_scaling: AUTO key=%s mode=%s value=%.2f batch_risk=%.2f",
                   group_key, a_mode, a_value, batch_risk)

        if batch_risk > 0:
          result_auto.extend(scale_backtest_trades(group_trades, batch_risk, **a_kwargs))
        else:
          result_auto.extend(group_trades)

      if return_is_end_state:
        return result_auto, {}
      return result_auto

  # ── Portfolio mode (single shared config) ──
  mode = req.risk_profile_mode
  value = req.risk_profile_value
  _log.debug("  _apply_risk_scaling: mode=%s value=%s include_side=%s", mode, value, include_side)

  # No scaling needed if fixed with no value override
  if mode == "fixed" and value <= 0:
    _log.debug("  _apply_risk_scaling: SKIPPED (fixed mode, value<=0)")
    if return_is_end_state:
      return trades, {}
    return trades

  # Common scaling kwargs
  scale_kwargs = dict(
    mode=mode,
    risk_value=value,
    initial_capital=req.risk_profile_initial_capital,
    multiplier=req.risk_profile_multiplier,
    max_streak=req.risk_profile_max_streak,
    kelly_n=req.risk_profile_kelly_n,
    kelly_warmup=req.risk_profile_kelly_warmup,
    kelly_scope=getattr(req, 'risk_profile_kelly_scope', 'portfolio'),
    kelly_lookback=getattr(req, 'risk_profile_kelly_lookback', 0),
    kelly_cap=getattr(req, 'risk_profile_kelly_cap', 0.0),
    include_side=include_side,
    oos_date=getattr(req, 'date_to', None),
    martingale_compound=getattr(req, 'risk_profile_martingale_compound', 'off'),
    martingale_scope=getattr(req, 'risk_profile_martingale_scope', 'asset'),
    run_batch_map=run_batch_map,
    martingale_count_mode=getattr(req, 'risk_profile_martingale_count_mode', 'full_position'),
    martingale_oos_reset=getattr(req, 'risk_profile_martingale_oos_reset', True),
    martingale_start_after=getattr(req, 'risk_profile_martingale_start_after', 0),
    anti_martingale_reset_after=getattr(req, 'risk_profile_anti_martingale_reset_after', 0),
    return_is_end_state=return_is_end_state,
    initial_is_end_state=initial_is_end_state,
  )

  # Resolve batch_id: single-batch has req.batch_id, cross-strategy has req.batch_ids
  batch_id = getattr(req, 'batch_id', None)
  if batch_id is not None:
    # Single-batch: scale all trades with one risk amount
    batch_risk = _get_batch_risk_amount(batch_id, db)
    if batch_risk <= 0:
      if return_is_end_state:
        return trades, {}
      return trades
    return scale_backtest_trades(trades, batch_risk, **scale_kwargs)

  batch_ids = getattr(req, 'batch_ids', [])
  if not batch_ids:
    _log.debug("  _apply_risk_scaling: no batch_ids found — returning unscaled")
    if return_is_end_state:
      return trades, {}
    return trades

  # Multi-batch: check if batches have heterogeneous risk amounts
  batch_risks = {bid: _get_batch_risk_amount(bid, db) for bid in batch_ids}
  _log.debug("  _apply_risk_scaling: batch_risks=%s", batch_risks)

  if len(set(batch_risks.values())) <= 1 or not run_batch_map:
    # All batches same risk (or no batch map): scale uniformly
    batch_risk = batch_risks[batch_ids[0]]
    _log.debug("  _apply_risk_scaling: uniform scaling, batch_risk=%.2f, target=%s mode=%s",
              batch_risk, value, mode)
    if batch_risk <= 0:
      _log.warning("  _apply_risk_scaling: batch_risk<=0, returning unscaled!")
      if return_is_end_state:
        return trades, {}
      return trades
    return scale_backtest_trades(trades, batch_risk, **scale_kwargs)

  # Heterogeneous risks: scale each batch group independently
  from collections import defaultdict
  batch_groups: dict[int, list] = defaultdict(list)
  ungrouped: list = []
  for t in trades:
    rid = getattr(t, 'run_id', None)
    bid = run_batch_map.get(rid) if rid is not None else None
    if bid is not None:
      batch_groups[bid].append(t)
    else:
      ungrouped.append(t)

  result: list = []
  merged_is_end_state: dict = {}
  for bid, group in batch_groups.items():
    br = batch_risks.get(bid, batch_risks[batch_ids[0]])
    if br > 0:
      scaled = scale_backtest_trades(group, br, **scale_kwargs)
      if return_is_end_state and isinstance(scaled, tuple):
        result.extend(scaled[0])
        merged_is_end_state.update(scaled[1])
      else:
        result.extend(scaled)
    else:
      result.extend(group)

  # Ungrouped trades (e.g. portfolio synthetics): use first batch risk
  if ungrouped:
    br = batch_risks[batch_ids[0]]
    if br > 0:
      scaled = scale_backtest_trades(ungrouped, br, **scale_kwargs)
      if return_is_end_state and isinstance(scaled, tuple):
        result.extend(scaled[0])
        merged_is_end_state.update(scaled[1])
      else:
        result.extend(scaled)
    else:
      result.extend(ungrouped)

  if return_is_end_state:
    return result, merged_is_end_state
  return result


def _apply_stress_slippage(
  trades: list[BacktestTrade], extra_pct: float, db: Session,
  extra_commission_pct: float = 0.0, funding_daily_pct: float = 0.0,
  spread_pct: float = 0.0,
  per_batch_costs: dict[int, dict] | None = None,
  run_batch_map: dict[int, int] | None = None,
) -> list[BacktestTrade]:
  """Apply post-hoc trading costs to trade PnL for stress testing.

  Costs deducted from net_profit per trade:
  - extra_pct (slippage): notional * pct/100 * 2 (round-trip)
  - extra_commission_pct: notional * pct/100 * 2 (round-trip)
  - spread_pct: notional * pct/100 (full spread = half per side * 2)
  - funding_daily_pct: notional * daily_rate/100 * hold_days

  When per_batch_costs is provided (keyed by batch_id), each trade uses
  its batch's slippage instead of the global extra_pct. Other costs
  (commission, funding, spread) still use the global values.

  Returns NEW trade-like objects with adjusted net_profit. Never mutates
  the original trade objects (which may be shared via the optimization cache).
  """
  has_per_batch = bool(per_batch_costs and run_batch_map)
  total_rt_pct = extra_pct + extra_commission_pct + (spread_pct / 2.0)
  has_costs = total_rt_pct > 0 or funding_daily_pct > 0 or has_per_batch
  if not has_costs:
    return trades
  from copy import copy
  result = []
  for t in trades:
    # Shallow copy so we don't mutate the caller's trade objects.
    # Trades from _load_multi_batch_trades are now plain SimpleNamespace,
    # so copy() is safe (no ORM instrumentation to cause GC errors).
    tc = copy(t)
    # Belt-and-suspenders: if somehow an ORM object slips through, detach it
    try:
      db.expunge(tc)
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)
    notional = abs(tc.entry_price * tc.quantity)

    # Determine slippage for this trade: per-batch override or global
    trade_slip = extra_pct
    if has_per_batch and run_batch_map and per_batch_costs:
      batch_id = run_batch_map.get(tc.run_id, -1) if tc.run_id else -1
      batch_cost = per_batch_costs.get(batch_id)
      if batch_cost:
        trade_slip = float(batch_cost.get("slippage", extra_pct))

    trade_rt_pct = trade_slip + extra_commission_pct + (spread_pct / 2.0)
    # Round-trip costs: slippage + commission + spread (each applied per side)
    if trade_rt_pct > 0:
      tc.net_profit -= notional * (trade_rt_pct / 100) * 2
    # Funding cost: notional * daily_rate * hold_days
    if funding_daily_pct > 0 and tc.entry_time and tc.exit_time:
      hold_secs = (tc.exit_time - tc.entry_time).total_seconds()
      hold_days = max(hold_secs / 86400, 0)
      tc.net_profit -= notional * (funding_daily_pct / 100) * hold_days
    # Keep net_profit_pct consistent with the adjusted net_profit
    if notional > 0:
      tc.net_profit_pct = (tc.net_profit / notional) * 100
    result.append(tc)
  return result


def _enrich_candidates(
  candidates: list[dict], daily_pnl: pd.DataFrame,
  trades: list[BacktestTrade], corr_matrix: pd.DataFrame,
) -> list[dict]:
  """Add comprehensive metrics + equity curve to each candidate."""
  for c in candidates:
    cols = [a for a in c["assets"] if a in daily_pnl.columns]
    if not cols:
      continue
    combined = daily_pnl[cols].sum(axis=1)
    equity = combined.cumsum()
    net = float(combined.sum())
    dd = float((equity - equity.cummax()).min())
    neg = combined[combined < 0]
    ds = float(neg.std()) if len(neg) > 1 else 1.0
    sortino = float((combined.mean() / ds) * (365 ** 0.5)) if ds > 0 else 0.0
    n_days = max(len(combined), 1)
    # Match trades to candidate assets (side-aware for L/S columns)
    from backend.analysis.daily_pnl import _asset_key
    has_side = any(col.endswith(("_Long", "_Short")) for col in cols)
    if has_side:
      asset_keys = set(cols)
      asset_trades = [t for t in trades if _asset_key(t, True) in asset_keys]
    else:
      asset_trades = [t for t in trades if t.symbol in set(cols)]
    # Group by position_id to count positions, not individual fills
    from collections import defaultdict
    _pos_pnl: dict[str, float] = defaultdict(float)
    for t in asset_trades:
      pid = t.position_id if t.position_id else f"_solo_{t.id}"
      _pos_pnl[pid] += (t.net_profit or 0.0)
    n_trades = len(_pos_pnl)
    wins = sum(1 for pnl in _pos_pnl.values() if pnl > 0)
    avg_corr = 0.0
    if len(cols) > 1:
      sub = corr_matrix.loc[cols, cols]
      mask = ~np.eye(len(cols), dtype=bool)
      avg_corr = float(sub.values[mask].mean())
    c["metrics"].update({
      "win_rate": round((wins / n_trades * 100) if n_trades > 0 else 0, 1),
      "total_trades": n_trades,
      "sortino": round(sortino, 2),
      "avg_corr": round(avg_corr, 3),
      "recovery_factor": round(net / abs(dd) if dd != 0 else 0, 2),
      "avg_trades_per_month": round(n_trades / max(1, n_days / 30), 1),
      "calmar": round((net / n_days * 365) / abs(dd) if dd != 0 else 0, 2),
    })
    eq = equity.values.tolist()
    if len(eq) > 200:
      eq = eq[::max(1, len(eq) // 200)]
    c["equity_curve"] = [round(v, 2) for v in eq]
  return candidates


# --- Endpoints ---

@router.post("/correlation")
def correlation(req: CorrelationRequest, db: Session = Depends(get_db)):
  """Compute correlation matrix for a batch backtest."""
  trades = _load_batch_trades(req.batch_id, db, date_to=req.date_to)
  daily_pnl = build_daily_pnl_matrix(trades)
  daily_pnl = _filter_daily_pnl(daily_pnl, trades, req.min_trades, req.min_pf, req.min_wr,
                                  date_to=req.date_to)

  if daily_pnl.shape[1] < 2:
    raise HTTPException(status_code=400, detail="Need at least 2 symbols for correlation")

  corr = get_correlation_matrix(daily_pnl, req.corr_method)
  stats = correlation_stats(corr)

  return {
    "symbols": corr.columns.tolist(),
    "matrix": corr.values.tolist(),
    "stats": stats,
  }


@router.post("/optimize")
def optimize(req: OptimizeRequest, background_tasks: BackgroundTasks):
  """Run portfolio optimizer in background — returns task_id immediately."""
  from backend.db.database import SessionLocal
  task_id = uuid.uuid4().hex[:12]
  with _optimize_lock:
    _optimize_progress[task_id] = {"status": "running", "result": None, "error": None}
  background_tasks.add_task(
    _run_optimize_task, task_id, req, SessionLocal, _optimize_inner, "Optimize",
  )
  return {"task_id": task_id, "status": "running"}


def _optimize_inner(req: OptimizeRequest, db: Session, task_id: str = ""):
  _update_progress(task_id, 0, 8, "Loading trades...")
  trades = _load_batch_trades(req.batch_id, db, date_to=req.date_to, date_from=req.date_from,
                              clip_boundary=req.clip_is)
  # Session Time Mask pre-filter — remove trades outside active hour×day cells
  if req.session_mask_id and not req.session_filter_enabled:
    # Legacy saved mask path (only if inline filter is not active)
    trades = _apply_session_mask(trades, req.session_mask_id, db, n_free_params=req.n_assets)
  # Inline Session Filter — fixed/auto/saved_mask mode
  if req.session_filter_enabled and trades:
    from backend.analysis.session_mask_filter import apply_session_mask as _sf_apply, build_fixed_mask
    _sf_mask_data = None
    if req.session_filter_mode == "fixed" and (req.session_filter_exclude_days or req.session_filter_exclude_hours):
      _sf_mask_data = build_fixed_mask(req.session_filter_exclude_days, req.session_filter_exclude_hours)
    elif req.session_filter_mode == "auto":
      from backend.analysis.session_auto_mask import compute_auto_mask
      _auto = compute_auto_mask(trades, req.session_filter_auto_min_trades, req.session_filter_auto_include_insufficient)
      _sf_mask_data = _auto["mask_data"]
      logger.info("Session filter (auto): kept hours=%s, kept days=%s", _auto["kept_hours"], _auto["kept_days"])
    elif req.session_filter_mode == "saved_mask" and req.session_mask_id:
      trades = _apply_session_mask(trades, req.session_mask_id, db, n_free_params=req.n_assets)
      _sf_mask_data = None  # already applied by _apply_session_mask
    if _sf_mask_data:
      _before = len(trades)
      trades = _sf_apply(trades, _sf_mask_data)
      logger.info("Session filter (%s): %d → %d trades", req.session_filter_mode, _before, len(trades))
  _update_progress(task_id, 1, 8, f"Trades loaded ({len(trades)} trades)")
  trades = _apply_risk_scaling(trades, req, db, include_side=False)
  trades = _apply_stress_slippage(trades, req.extra_slippage_pct, db,
                                   extra_commission_pct=req.extra_commission_pct,
                                   funding_daily_pct=req.funding_daily_pct,
                                   spread_pct=req.spread_pct)
  _update_progress(task_id, 2, 8, "Quality filters applied")
  daily_pnl = build_daily_pnl_matrix(trades)
  # No daily_pnl truncation — trades filtered by entry_time may exit after cutoff,
  # and their PnL (realized at exit) still belongs to IS.
  daily_pnl = _filter_daily_pnl(daily_pnl, trades, req.min_trades, req.min_pf, req.min_wr,
                                  date_to=req.date_to)
  if req.bybit_size_gate:
    daily_pnl = _filter_bybit_size_gate(
      daily_pnl, trades, req.bybit_size_gate_max_reject_pct,
    )
  daily_pnl = _filter_by_category(daily_pnl, req.category_filter, db)

  # Pre-filter: remove structurally unsuitable assets
  if req.prefilter_enabled and daily_pnl.shape[1] > 0:
    if req.prefilter_mode == "trend":
      from backend.analysis.seed_ranker import prefilter_for_trend
      survivors = prefilter_for_trend(daily_pnl, req.prefilter_hurst_min, req.prefilter_ter_percentile)
    else:
      from backend.analysis.seed_ranker import prefilter_for_meanrev
      survivors = prefilter_for_meanrev(daily_pnl, req.prefilter_hurst_max, req.prefilter_vr_max, req.prefilter_min_crossing_rate)
    daily_pnl = daily_pnl[[c for c in daily_pnl.columns if c in survivors]]

    # Pool-too-small check after pre-filter
    if daily_pnl.shape[1] < 2:
      raise HTTPException(
        status_code=400,
        detail=f"Pre-filter ({req.prefilter_mode} mode) reduced pool to "
               f"{daily_pnl.shape[1]} asset(s). Need at least 2. "
               f"Try relaxing Hurst or TER thresholds."
      )

  # Benchmark robustness: filter fragile assets + compute seed scores
  _update_progress(task_id, 3, 8, "Benchmark & seed scoring...")
  external_scores = None
  if req.benchmark_enabled:
    daily_pnl, external_scores = _prepare_benchmark(
      trades, daily_pnl, req.benchmark_source, req.benchmark_split,
      req.benchmark_threshold, req.benchmark_elite_target, req.benchmark_exclude_fragile,
      consistency_blend=req.benchmark_consistency_blend,
      fragility_blend=req.benchmark_fragility_blend,
      wr_harmony_blend=req.benchmark_wr_harmony_blend,
      dsr_gate=req.benchmark_dsr_gate,
      daily_pnl_full=daily_pnl,
    )
    if "benchmark" not in req.seed_metrics:
      req.seed_metrics = [*req.seed_metrics, "benchmark"]

  # Event-based L1 scoring: override seed ranking with last-N-trades metrics
  if req.event_mode:
    from backend.analysis.optimizer_seeds import compute_event_seed_scores
    metric_key = req.seed_metrics[0] if req.seed_metrics else "sortino"
    event_scores = compute_event_seed_scores(
      trades, n_trades=req.n_trades,
      objective=metric_key,
      staleness_days=req.staleness_days,
      include_side=False,
      date_from=req.date_from,
      date_to=req.date_to,  # OOS safety net — exclude trades exiting after IS cutoff
    )
    if external_scores is None:
      external_scores = {}
    external_scores[metric_key] = event_scores

  # Recency-weighted seed scoring: WR × PF weighted by trade age
  if req.recency_enabled:
    from backend.analysis.optimizer_seeds import compute_recency_seed_scores
    trade_df = pd.DataFrame([
      {"Asset": t.symbol, "Exit Time": t.exit_time, "Net Profit": t.net_profit}
      for t in trades if t.exit_time is not None and t.net_profit is not None
    ])
    if not trade_df.empty:
      if external_scores is None:
        external_scores = {}
      external_scores["recency"] = compute_recency_seed_scores(
        trade_df, mode=req.recency_mode,
        batch_size=req.recency_batch_size, boost_factor=req.recency_boost_factor,
      )
      if "recency" not in req.seed_metrics:
        req.seed_metrics = [*req.seed_metrics, "recency"]

  # Avg Range seed scoring / pre-filter (market structure consecutive ranges)
  if "avg_range" in req.seed_metrics:
    daily_pnl, external_scores = _apply_avg_range_scoring(
      req.batch_id, daily_pnl, external_scores,
      req.avg_range_bull_threshold, req.avg_range_bear_threshold,
      req.avg_range_all_threshold, req.avg_range_filter_mode,
      date_to=req.date_to, db=db,
    )

  # Quality seed scoring (Asset Quality + Strategy Quality)
  if "asset_quality" in req.seed_metrics or "strat_quality" in req.seed_metrics:
    daily_pnl, external_scores = _apply_quality_scoring(
      req.batch_id, daily_pnl, external_scores, req,
      trades=trades, db=db,
    )

  # IS-WFE seed scoring: real walk-forward efficiency via IS:OOS split
  if "is_wfe" in req.seed_metrics:
    from backend.analysis.optimizer_seeds import compute_is_wfe_seed_scores

    # Count trades per asset in the IS window for min_is_trades check
    trades_by_asset: dict[str, int] | None = None
    if req.is_wfe_min_trades > 0 and len(daily_pnl) > 0:
      is_len = int(len(daily_pnl) * req.is_wfe_is_pct / 100)
      is_cutoff_date = daily_pnl.index[is_len - 1] if is_len > 0 else daily_pnl.index[0]
      trades_by_asset = {}
      for t in trades:
        if t.exit_time is None or t.symbol is None:
          continue
        exit_ts = pd.Timestamp(str(t.exit_time))
        if exit_ts <= is_cutoff_date:
          trades_by_asset[t.symbol] = trades_by_asset.get(t.symbol, 0) + 1

    wfe_scores = compute_is_wfe_seed_scores(
      daily_pnl, is_pct=req.is_wfe_is_pct,
      min_is_trades=req.is_wfe_min_trades,
      trades_by_asset=trades_by_asset,
    )
    if wfe_scores:
      if external_scores is None:
        external_scores = {}
      external_scores["is_wfe"] = wfe_scores

    # Filter mode: remove assets below min WFE
    if req.is_wfe_filter and req.is_wfe_min_wfe > 0:
      scored_assets = set(wfe_scores.keys()) if wfe_scores else set()
      keep = [
        a for a in daily_pnl.columns
        if a in scored_assets and wfe_scores.get(a, 0) >= req.is_wfe_min_wfe
      ]
      if keep:
        daily_pnl = daily_pnl[keep]

  if daily_pnl.shape[1] < 2:
    raise HTTPException(status_code=400, detail="Need at least 2 symbols after filtering")
  if req.n_assets > daily_pnl.shape[1]:
    raise HTTPException(
      status_code=400,
      detail=f"n_assets ({req.n_assets}) > available symbols ({daily_pnl.shape[1]})",
    )

  _update_progress(task_id, 4, 8, f"Seed scores computed ({daily_pnl.shape[1]} assets in pool)")
  corr = get_correlation_matrix(daily_pnl, req.corr_method)
  _update_progress(task_id, 5, 8, "Correlation matrix built")
  tc_active = req.tc_low_threshold > 0 or req.tc_high_threshold > 0
  tc_constraints = {
    "low_threshold": req.tc_low_threshold, "max_low": req.tc_max_low,
    "high_threshold": req.tc_high_threshold, "min_high": req.tc_min_high,
  } if tc_active else None
  trade_counts = _compute_trade_counts(trades) if tc_active else None

  _update_progress(task_id, 6, 8, "Running optimizer...")
  candidates = diversify_portfolio(
    div_algo=req.div_algo,
    daily_pnl=daily_pnl, corr_matrix=corr,
    n_assets=req.n_assets, objective=req.objective,
    n_portfolios=req.n_portfolios,
    seed_metrics=req.seed_metrics, max_seeds=req.max_seeds,
    prioritize_stability=req.prioritize_stability,
    external_scores=external_scores,
    trade_counts=trade_counts, tc_constraints=tc_constraints,
    max_overlap=req.max_overlap,
    max_dd_pct=req.max_allowed_drawdown_pct,
    seed_weights=req.seed_weights,
  )

  _update_progress(task_id, 7, 8, "Enriching candidates...")
  candidates = _enrich_candidates(candidates, daily_pnl, trades, corr)

  # AC post-filter: flag mismatched assets per candidate
  if req.ac_postfilter:
    from backend.analysis.seed_ranker import postfilter_ac
    for c in candidates:
      ac_results = postfilter_ac(daily_pnl, c["assets"], strategy_type=req.ac_postfilter_type, lag=req.ac_lag)
      c["ac_flags"] = [a for a, info in ac_results.items() if info["flagged"]]
      c["ac_scores"] = {a: info["ac"] for a, info in ac_results.items()}

  # Split-half correlation stability check per candidate
  from backend.analysis.correlation import split_half_correlation_check
  for c in candidates:
    c["corr_stability"] = split_half_correlation_check(daily_pnl, c["assets"])

  pool_stats = _compute_pool_stats(daily_pnl, trades)

  _update_progress(task_id, 8, 8, "Complete")
  return {"candidates": candidates, "total_symbols": daily_pnl.shape[1], "pool_stats": pool_stats}


@router.post("/optimize-ls")
def optimize_ls(req: OptimizeLSRequest, background_tasks: BackgroundTasks):
  """Run split L/S optimizer in background — returns task_id immediately."""
  from backend.db.database import SessionLocal
  task_id = uuid.uuid4().hex[:12]
  with _optimize_lock:
    _optimize_progress[task_id] = {"status": "running", "result": None, "error": None}
  background_tasks.add_task(
    _run_optimize_task, task_id, req, SessionLocal, _optimize_ls_inner, "Optimize-LS",
  )
  return {"task_id": task_id, "status": "running"}


def _optimize_ls_inner(req: OptimizeLSRequest, db: Session, task_id: str = ""):
  _update_progress(task_id, 0, 8, "Loading trades...")
  trades = _load_batch_trades(req.batch_id, db, date_to=req.date_to, date_from=req.date_from,
                              clip_boundary=req.clip_is)
  # Session Time Mask pre-filter — remove trades outside active hour×day cells
  if req.session_mask_id and not req.session_filter_enabled:
    # Legacy saved mask path (only if inline filter is not active)
    trades = _apply_session_mask(trades, req.session_mask_id, db, n_free_params=req.n_longs + req.n_shorts)
  # Inline Session Filter — fixed/auto/saved_mask mode
  if req.session_filter_enabled and trades:
    from backend.analysis.session_mask_filter import apply_session_mask as _sf_apply, build_fixed_mask
    _sf_mask_data = None
    if req.session_filter_mode == "fixed" and (req.session_filter_exclude_days or req.session_filter_exclude_hours):
      _sf_mask_data = build_fixed_mask(req.session_filter_exclude_days, req.session_filter_exclude_hours)
    elif req.session_filter_mode == "auto":
      from backend.analysis.session_auto_mask import compute_auto_mask
      _auto = compute_auto_mask(trades, req.session_filter_auto_min_trades, req.session_filter_auto_include_insufficient)
      _sf_mask_data = _auto["mask_data"]
      logger.info("Session filter LS (auto): kept hours=%s, kept days=%s", _auto["kept_hours"], _auto["kept_days"])
    elif req.session_filter_mode == "saved_mask" and req.session_mask_id:
      trades = _apply_session_mask(trades, req.session_mask_id, db, n_free_params=req.n_longs + req.n_shorts)
      _sf_mask_data = None  # already applied by _apply_session_mask
    if _sf_mask_data:
      _before = len(trades)
      trades = _sf_apply(trades, _sf_mask_data)
      logger.info("Session filter LS (%s): %d → %d trades", req.session_filter_mode, _before, len(trades))
  _update_progress(task_id, 1, 8, f"Trades loaded ({len(trades)} trades)")
  trades = _apply_risk_scaling(trades, req, db, include_side=True)
  trades = _apply_stress_slippage(trades, req.extra_slippage_pct, db,
                                   extra_commission_pct=req.extra_commission_pct,
                                   funding_daily_pct=req.funding_daily_pct,
                                   spread_pct=req.spread_pct)
  _update_progress(task_id, 2, 8, "Quality filters applied")
  daily_pnl = build_daily_pnl_matrix(trades, include_side=True)
  # No daily_pnl truncation — trades filtered by entry_time may exit after cutoff
  daily_pnl = _filter_daily_pnl(daily_pnl, trades, req.min_trades, req.min_pf, req.min_wr,
                                  include_side=True, date_to=req.date_to)
  if req.bybit_size_gate:
    daily_pnl = _filter_bybit_size_gate(
      daily_pnl, trades, req.bybit_size_gate_max_reject_pct,
      include_side=True,
    )
  daily_pnl = _filter_by_category(daily_pnl, req.category_filter, db)

  # Pre-filter: remove structurally unsuitable assets
  if req.prefilter_enabled and daily_pnl.shape[1] > 0:
    if req.prefilter_mode == "trend":
      from backend.analysis.seed_ranker import prefilter_for_trend
      survivors = prefilter_for_trend(daily_pnl, req.prefilter_hurst_min, req.prefilter_ter_percentile)
    else:
      from backend.analysis.seed_ranker import prefilter_for_meanrev
      survivors = prefilter_for_meanrev(daily_pnl, req.prefilter_hurst_max, req.prefilter_vr_max, req.prefilter_min_crossing_rate)
    daily_pnl = daily_pnl[[c for c in daily_pnl.columns if c in survivors]]

    # Pool-too-small check after pre-filter
    if daily_pnl.shape[1] < 2:
      raise HTTPException(
        status_code=400,
        detail=f"Pre-filter ({req.prefilter_mode} mode) reduced pool to "
               f"{daily_pnl.shape[1]} asset(s). Need at least 2. "
               f"Try relaxing Hurst or TER thresholds."
      )

  # Benchmark robustness: filter fragile assets + compute seed scores
  _update_progress(task_id, 3, 8, "Benchmark & seed scoring...")
  external_scores = None
  if req.benchmark_enabled:
    daily_pnl, external_scores = _prepare_benchmark(
      trades, daily_pnl, req.benchmark_source, req.benchmark_split,
      req.benchmark_threshold, req.benchmark_elite_target, req.benchmark_exclude_fragile,
      consistency_blend=req.benchmark_consistency_blend,
      fragility_blend=req.benchmark_fragility_blend,
      wr_harmony_blend=req.benchmark_wr_harmony_blend,
      dsr_gate=req.benchmark_dsr_gate,
      daily_pnl_full=daily_pnl,
      include_side=True,
    )
    if "benchmark" not in req.seed_metrics:
      req.seed_metrics = [*req.seed_metrics, "benchmark"]

  # Event-based L1 scoring (L/S split — include_side=True)
  if req.event_mode:
    from backend.analysis.optimizer_seeds import compute_event_seed_scores
    metric_key = req.seed_metrics[0] if req.seed_metrics else "sortino"
    event_scores = compute_event_seed_scores(
      trades, n_trades=req.n_trades,
      objective=metric_key,
      staleness_days=req.staleness_days,
      include_side=True,
      date_from=req.date_from,
      date_to=req.date_to,  # OOS safety net — exclude trades exiting after IS cutoff
    )
    if external_scores is None:
      external_scores = {}
    external_scores[metric_key] = event_scores

  # Recency-weighted seed scoring (L/S split — use SYMBOL_Side keys)
  if req.recency_enabled:
    from backend.analysis.optimizer_seeds import compute_recency_seed_scores
    trade_df = pd.DataFrame([
      {"Asset": f"{t.symbol}_{(t.side or 'Long').title()}", "Exit Time": t.exit_time, "Net Profit": t.net_profit}
      for t in trades if t.exit_time is not None and t.net_profit is not None
    ])
    if not trade_df.empty:
      if external_scores is None:
        external_scores = {}
      external_scores["recency"] = compute_recency_seed_scores(
        trade_df, mode=req.recency_mode,
        batch_size=req.recency_batch_size, boost_factor=req.recency_boost_factor,
      )
      if "recency" not in req.seed_metrics:
        req.seed_metrics = [*req.seed_metrics, "recency"]

  # Avg Range seed scoring / pre-filter (market structure consecutive ranges)
  if "avg_range" in req.seed_metrics:
    daily_pnl, external_scores = _apply_avg_range_scoring(
      req.batch_id, daily_pnl, external_scores,
      req.avg_range_bull_threshold, req.avg_range_bear_threshold,
      req.avg_range_all_threshold, req.avg_range_filter_mode,
      include_side=True,
      date_to=req.date_to, db=db,
    )

  # Quality seed scoring (Asset Quality + Strategy Quality, L/S split)
  if "asset_quality" in req.seed_metrics or "strat_quality" in req.seed_metrics:
    daily_pnl, external_scores = _apply_quality_scoring(
      req.batch_id, daily_pnl, external_scores, req, include_side=True,
      trades=trades, db=db,
    )

  # IS-WFE seed scoring (L/S split)
  if "is_wfe" in req.seed_metrics:
    from backend.analysis.optimizer_seeds import compute_is_wfe_seed_scores

    trades_by_asset_ls: dict[str, int] | None = None
    if req.is_wfe_min_trades > 0 and len(daily_pnl) > 0:
      is_len = int(len(daily_pnl) * req.is_wfe_is_pct / 100)
      is_cutoff_date = daily_pnl.index[is_len - 1] if is_len > 0 else daily_pnl.index[0]
      trades_by_asset_ls = {}
      for t in trades:
        if t.exit_time is None or t.symbol is None:
          continue
        exit_ts = pd.Timestamp(str(t.exit_time))
        if exit_ts <= is_cutoff_date:
          # L/S uses SYMBOL_Long / SYMBOL_Short keys
          side = "Long" if (t.net_profit or 0) >= 0 else "Short"
          key = f"{t.symbol}_{side}"
          trades_by_asset_ls[key] = trades_by_asset_ls.get(key, 0) + 1

    wfe_scores_ls = compute_is_wfe_seed_scores(
      daily_pnl, is_pct=req.is_wfe_is_pct,
      min_is_trades=req.is_wfe_min_trades,
      trades_by_asset=trades_by_asset_ls,
    )
    if wfe_scores_ls:
      if external_scores is None:
        external_scores = {}
      external_scores["is_wfe"] = wfe_scores_ls

    if req.is_wfe_filter and req.is_wfe_min_wfe > 0:
      scored = set(wfe_scores_ls.keys()) if wfe_scores_ls else set()
      keep = [a for a in daily_pnl.columns if a in scored and wfe_scores_ls.get(a, 0) >= req.is_wfe_min_wfe]
      if keep:
        daily_pnl = daily_pnl[keep]

  if daily_pnl.shape[1] < 2:
    raise HTTPException(status_code=400, detail="Need at least 2 symbols after filtering")

  n_longs_avail = sum(1 for c in daily_pnl.columns if c.endswith("_Long"))
  n_shorts_avail = sum(1 for c in daily_pnl.columns if c.endswith("_Short"))
  if req.n_longs > n_longs_avail:
    raise HTTPException(
      status_code=400,
      detail=f"n_longs ({req.n_longs}) > available long assets ({n_longs_avail})",
    )
  if req.n_shorts > n_shorts_avail:
    raise HTTPException(
      status_code=400,
      detail=f"n_shorts ({req.n_shorts}) > available short assets ({n_shorts_avail})",
    )

  _update_progress(task_id, 4, 8, f"Seed scores computed ({n_longs_avail}L / {n_shorts_avail}S in pool)")
  corr = get_correlation_matrix(daily_pnl, req.corr_method)
  _update_progress(task_id, 5, 8, "Correlation matrix built")
  tc_active = bool(req.tc_longs or req.tc_shorts)
  trade_counts = _compute_trade_counts(trades, include_side=True) if tc_active else None

  _update_progress(task_id, 6, 8, "Running L/S optimizer...")
  raw = diversify_split_portfolio(
    div_algo=req.div_algo,
    daily_pnl=daily_pnl, corr_matrix=corr,
    n_longs=req.n_longs, n_shorts=req.n_shorts,
    within_objective=req.within_objective,
    combined_objective=req.combined_objective,
    n_portfolios=req.n_portfolios,
    seed_metrics=req.seed_metrics, max_seeds=req.max_seeds,
    prioritize_stability=req.prioritize_stability,
    external_scores=external_scores,
    trade_counts=trade_counts,
    tc_longs=req.tc_longs if tc_active else None,
    tc_shorts=req.tc_shorts if tc_active else None,
    max_overlap=req.max_overlap,
    max_dd_pct=req.max_allowed_drawdown_pct,
    seed_weights=req.seed_weights,
  )

  _update_progress(task_id, 7, 8, "Enriching candidates...")
  # Build trade-level DataFrame for accurate metrics
  # Include position_id so _portfolio_metrics can merge multi-exit fills
  _tdf = pd.DataFrame([
    {"Asset": f"{t.symbol}_{(t.side or 'Long').title()}", "Net Profit": t.net_profit,
     "position_id": t.position_id or f"_solo_{t.id}"}
    for t in trades if t.net_profit is not None
  ])

  # Enrich each candidate with standard portfolio metrics
  candidates = []
  for c in raw:
    metrics = _portfolio_metrics(daily_pnl, c["assets"], _tdf)
    candidates.append({
      "longs": c.get("longs", []),
      "shorts": c.get("shorts", []),
      "assets": c["assets"],
      "metrics": metrics,
      "score": round(float(c.get("score", 0)), 4),
    })

  candidates = _enrich_candidates(candidates, daily_pnl, trades, corr)

  # AC post-filter: flag mismatched assets per candidate
  if req.ac_postfilter:
    from backend.analysis.seed_ranker import postfilter_ac
    for c in candidates:
      ac_results = postfilter_ac(daily_pnl, c["assets"], strategy_type=req.ac_postfilter_type, lag=req.ac_lag)
      c["ac_flags"] = [a for a, info in ac_results.items() if info["flagged"]]
      c["ac_scores"] = {a: info["ac"] for a, info in ac_results.items()}

  # Split-half correlation stability check per candidate
  from backend.analysis.correlation import split_half_correlation_check
  for c in candidates:
    c["corr_stability"] = split_half_correlation_check(daily_pnl, c["assets"])

  pool_stats = _compute_pool_stats(daily_pnl, trades, include_side=True)

  _update_progress(task_id, 8, 8, "Complete")
  return {
    "candidates": candidates,
    "total_symbols": daily_pnl.shape[1],
    "n_longs_available": n_longs_avail,
    "n_shorts_available": n_shorts_avail,
    "pool_stats": pool_stats,
  }


@router.post("/monte-carlo")
def monte_carlo(req: MonteCarloRequest, db: Session = Depends(get_db)):
  """Run Monte Carlo simulation on batch, individual run, or portfolio trades."""
  initial_capital = 10000.0
  if req.portfolio_id:
    from backend.api.routes.portfolio_asset_routes import _load_trades
    from backend.db.models import Portfolio
    p = db.query(Portfolio).filter(Portfolio.id == req.portfolio_id).first()
    if not p:
      raise HTTPException(status_code=404, detail="Portfolio not found")
    df = _load_trades(p, db)
    if df.empty:
      raise HTTPException(status_code=400, detail="No trades in portfolio")
    # Build trade-like objects from DataFrame rows. CS-069: include
    # entry_time so clip_is_trades below can detect IS-straddling
    # positions (it compares t.entry_time < cutoff < t.exit_time).
    class _FakeTrade:
      def __init__(self, symbol: str, net_profit: float, entry_time=None,
                   exit_time=None, position_id=None):
        self.symbol = symbol
        self.net_profit = net_profit
        self.entry_time = entry_time
        self.exit_time = exit_time
        self.position_id = position_id
    # CS-069: parity with batch + run branches. The previous portfolio
    # cutoff ``Entry Time <= pd.Timestamp(date_to)`` treated date_to as
    # a midnight instant, dropping every trade entered later that same
    # day; the run branch uses ``< date_to + 1 day`` (inclusive of
    # whole-day), and the batch branch delegates to _load_batch_trades
    # with clip_boundary. A user comparing Monte Carlo stats across
    # the three saw distributions diverge because of the endpoint,
    # not the trades. Align on ``< date_to + 1 day`` and apply
    # clip_is_trades so IS-straddling positions clip to the boundary.
    if req.date_from and "Entry Time" in df.columns:
      df = df[df["Entry Time"] >= pd.Timestamp(req.date_from)]
    if req.date_to and "Entry Time" in df.columns:
      cutoff = pd.Timestamp(req.date_to) + pd.Timedelta(days=1)
      df = df[df["Entry Time"] < cutoff]
    trades = [_FakeTrade(
      row.get("Asset", ""),
      row.get("Net Profit", 0),
      entry_time=row.get("Entry Time"),
      exit_time=row.get("Exit Time"),
      position_id=row.get("position_id"),
    ) for _, row in df.iterrows()]
    # Get initial capital from portfolio source strategies
    ss = json.loads(p.source_strategies) if p.source_strategies else {}
    bid = (ss.get("batch_ids") or [ss.get("batch_id")])[0] if ss.get("batch_ids") or ss.get("batch_id") else None
    if bid:
      batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
      if batch and batch.parameters:
        initial_capital = json.loads(batch.parameters).get("initial_capital", 10000.0)
    # CS-069: clip IS-straddling trades for parity with batch/run branches
    if req.date_to and bid:
      batch_tf = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
      tf = batch_tf.timeframe if batch_tf and batch_tf.timeframe else "D"
      trades = clip_is_trades(trades, req.date_to, tf, db)
  elif req.batch_id:
    # clip_boundary=True: force-close IS-straddling trades so Monte Carlo
    # tail-risk stats reflect IS-only outcomes (prevents OOS leakage when
    # date_to is supplied as the IS cutoff).
    trades = _load_batch_trades(
      req.batch_id, db,
      date_to=req.date_to, date_from=req.date_from,
      clip_boundary=bool(req.date_to),
    )
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == req.batch_id).first()
    if batch and batch.parameters:
      initial_capital = json.loads(batch.parameters).get("initial_capital", 10000.0)
  elif req.run_id:
    query = (
      db.query(BacktestTrade)
      .filter(BacktestTrade.run_id == req.run_id)
    )
    if req.date_from:
      from_dt = datetime.strptime(req.date_from, "%Y-%m-%d")
      query = query.filter(BacktestTrade.entry_time >= from_dt)
    if req.date_to:
      cutoff_dt = datetime.strptime(req.date_to, "%Y-%m-%d") + timedelta(days=1)
      query = query.filter(BacktestTrade.entry_time < cutoff_dt)
    trades = query.order_by(BacktestTrade.entry_time).all()
    if not trades:
      raise HTTPException(status_code=404, detail="No trades for this run")
    run = db.query(BacktestRun).filter(BacktestRun.id == req.run_id).first()
    if run and run.parameters:
      initial_capital = json.loads(run.parameters).get("initial_capital", 10000.0)
    # Force-close IS-straddling trades — parity with batch path above.
    if req.date_to and run is not None:
      batch = db.query(BatchBacktestRun).filter(
        BatchBacktestRun.id == run.batch_id,
      ).first() if run.batch_id else None
      tf = batch.timeframe if batch else "D"
      trades = clip_is_trades(trades, req.date_to, tf, db)
  else:
    raise HTTPException(status_code=400, detail="Provide batch_id, run_id, or portfolio_id")

  # --- Position-level aggregation (matches optimizer counting) ---
  # Group fills into positions by (symbol, position_id) for accurate trade counts
  from collections import defaultdict
  _pos_pnl: dict[str, dict[str, float]] = defaultdict(dict)
  for _ti, t in enumerate(trades):
    pid = getattr(t, "position_id", None) or f"_solo_{getattr(t, 'id', _ti)}"
    sym = t.symbol
    if pid not in _pos_pnl[sym]:
      _pos_pnl[sym][pid] = 0.0
    _pos_pnl[sym][pid] += (t.net_profit or 0.0)

  # Pre-compute per-asset position stats
  _asset_stats: dict[str, dict] = {}
  for sym, positions in _pos_pnl.items():
    n = len(positions)
    wins = sum(1 for pnl in positions.values() if pnl > 0)
    gp = sum(pnl for pnl in positions.values() if pnl > 0)
    gl = sum(abs(pnl) for pnl in positions.values() if pnl <= 0)
    _asset_stats[sym] = {"n": n, "wins": wins, "gp": gp, "gl": gl}

  # Filter assets with fewer than min_trades positions
  if req.min_trades > 0:
    trades = [t for t in trades if _asset_stats.get(t.symbol, {}).get("n", 0) >= req.min_trades]

  # Exclude top N assets by net profit
  if req.exclude_top_n > 0:
    asset_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
      asset_pnl[t.symbol] += (t.net_profit or 0)
    ranked = sorted(asset_pnl.keys(), key=lambda s: asset_pnl[s], reverse=True)
    excluded = set(ranked[:req.exclude_top_n])
    trades = [t for t in trades if t.symbol not in excluded]

  # Filter by PF range (position-level PF)
  if req.pf_min is not None or req.pf_max is not None:
    keep = set()
    for sym, s in _asset_stats.items():
      pf = min(s["gp"] / s["gl"], 10.0) if s["gl"] > 0 else (10.0 if s["gp"] > 0 else 0.0)
      if req.pf_min is not None and pf < req.pf_min:
        continue
      if req.pf_max is not None and pf > req.pf_max:
        continue
      keep.add(sym)
    trades = [t for t in trades if t.symbol in keep]

  # Filter by Win Rate minimum (position-level WR)
  if req.wr_min is not None:
    wr_keep = set()
    for sym, s in _asset_stats.items():
      wr = (s["wins"] / s["n"] * 100) if s["n"] > 0 else 0
      if wr >= req.wr_min:
        wr_keep.add(sym)
    trades = [t for t in trades if t.symbol in wr_keep]

  pnl_values = [t.net_profit for t in trades]

  # Compute actual trade frequency for Sharpe annualization
  trades_per_year = 365.0  # crypto default
  exit_times = sorted(t.exit_time for t in trades if t.exit_time)
  if len(exit_times) >= 2:
    span_days = (exit_times[-1] - exit_times[0]).total_seconds() / 86400
    if span_days > 0:
      trades_per_year = len(exit_times) / span_days * 365.25

  result = monte_carlo_simulation(
    pnl_values, req.n_simulations,
    initial_capital=initial_capital,
    ruin_threshold_pct=req.ruin_threshold_pct,
    trades_per_year=trades_per_year,
  )

  if result is None:
    raise HTTPException(status_code=400, detail="No trades to simulate")

  return result


class RecalcRequest(BaseModel):
  batch_id: int
  assets: list[str]
  date_to: str | None = None
  date_from: str | None = None


@router.post("/recalculate-candidate")
def recalculate_candidate(req: RecalcRequest, db: Session = Depends(get_db)):
  """Recalculate metrics for a modified asset list (after removal)."""
  trades = _load_batch_trades(req.batch_id, db, date_to=req.date_to, date_from=req.date_from)
  daily_pnl = build_daily_pnl_matrix(trades)
  corr = get_correlation_matrix(daily_pnl, "standard")
  _tdf = pd.DataFrame([
    {"Asset": f"{t.symbol}_{(t.side or 'Long').title()}", "Net Profit": t.net_profit,
     "position_id": t.position_id or f"_solo_{t.id}"}
    for t in trades if t.net_profit is not None
  ])
  candidate = {"assets": req.assets, "metrics": _portfolio_metrics(daily_pnl, req.assets, _tdf)}
  _enrich_candidates([candidate], daily_pnl, trades, corr)
  return candidate


@router.post("/compare")
def compare(req: CompareRequest, db: Session = Depends(get_db)):
  """Compare metrics across multiple batch backtests."""
  if len(req.batch_ids) < 2:
    raise HTTPException(status_code=400, detail="Need at least 2 batch runs to compare")

  results = []
  for bid in req.batch_ids:
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
    if not batch:
      continue

    agg = json.loads(batch.aggregate_metrics) if batch.aggregate_metrics else {}
    strategy = batch.strategy

    results.append({
      "batch_id": bid,
      "strategy_name": strategy.name if strategy else "Unknown",
      "timeframe": batch.timeframe,
      "direction_filter": batch.direction_filter,
      "created_at": batch.created_at.isoformat() if batch.created_at else None,
      "metrics": agg,
    })

  # --- Category comparison across batches ---
  categories = get_all_categories(db)
  cat_comparison = _build_category_comparison(req.batch_ids, categories, db)

  # --- Per-symbol comparison across batches ---
  sym_comparison = _build_symbol_comparison(cat_comparison)

  return {"strategies": results, "category_comparison": cat_comparison, "symbol_comparison": sym_comparison}


def _build_category_comparison(
  batch_ids: list[int],
  categories: dict[str, list[str]],
  db: Session,
) -> dict:
  """Build per-category stats for each batch, enabling cross-batch category comparison."""
  all_cats: set[str] = set()
  batch_cat_data: list[dict] = []

  for bid in batch_ids:
    # Load per-batch trades via lightweight path (6 cols, auto-scaled PnL).
    # Avoids the 3M-row ORM load warned against in CLAUDE.md (X04).
    from backend.analysis.batch_selection import load_trades_lightweight
    trades, _rbm, _binfo = load_trades_lightweight(db, batch_ids=[bid])
    if not trades:
      batch_cat_data.append({"batch_id": bid, "categories": {}})
      continue

    # Group trades by asset (symbol_side format)
    from collections import defaultdict
    asset_trades: dict[str, list] = defaultdict(list)
    for t in trades:
      side = (t.side or "Long").title()
      asset_key = f"{t.symbol}_{side}"
      asset_trades[asset_key].append(t)

    # Build per-symbol summary
    sym_summaries: list[dict] = []
    for asset_key, atrades in asset_trades.items():
      wins = [t for t in atrades if (t.net_profit or 0) > 0]
      losses = [t for t in atrades if (t.net_profit or 0) <= 0]
      gp = sum(t.net_profit for t in wins) if wins else 0.0
      gl = abs(sum(t.net_profit for t in losses)) if losses else 0.0
      total = len(atrades)
      sym_summaries.append({
        "asset": asset_key,
        "trades": total,
        "net_profit": round(sum(t.net_profit or 0 for t in atrades), 2),
        "profit_factor": round(min(gp / gl, 10.0), 2) if gl > 0 else (10.0 if gp > 0 else None),
        "win_rate": round(len(wins) / total * 100, 1) if total > 0 else 0.0,
      })

    # Aggregate by category
    cat_stats: dict[str, dict] = {}
    for sym in sym_summaries:
      base = sym["asset"].replace("_Long", "").replace("_Short", "")
      cats = categories.get(base, [])
      if not cats:
        continue
      for cat in cats:
        all_cats.add(cat)
        if cat not in cat_stats:
          cat_stats[cat] = {"assets": 0, "net_profit": 0.0, "total_wins": 0, "total_trades": 0, "gross_profit": 0.0, "gross_loss": 0.0}
        cs = cat_stats[cat]
        cs["assets"] += 1
        cs["net_profit"] += sym["net_profit"]
        cs["total_trades"] += sym["trades"]
        cs["total_wins"] += round(sym["trades"] * (sym["win_rate"] / 100))
        if sym["net_profit"] > 0:
          cs["gross_profit"] += sym["net_profit"]
        else:
          cs["gross_loss"] += abs(sym["net_profit"])

    # Finalize per-category metrics for this batch
    finalized: dict[str, dict] = {}
    for cat, cs in cat_stats.items():
      pf = None
      if cs["gross_loss"] > 0:
        pf = round(min(cs["gross_profit"] / cs["gross_loss"], 10.0), 2)
      elif cs["gross_profit"] > 0:
        pf = 10.0
      finalized[cat] = {
        "assets": cs["assets"],
        "net_profit": round(cs["net_profit"], 2),
        "profit_factor": pf,
        "win_rate": round(cs["total_wins"] / cs["total_trades"] * 100, 1) if cs["total_trades"] > 0 else 0.0,
        "trades": cs["total_trades"],
      }

    batch_cat_data.append({"batch_id": bid, "categories": finalized, "symbol_summaries": sym_summaries})

  return {
    "all_categories": sorted(all_cats),
    "batches": batch_cat_data,
  }


def _build_symbol_comparison(cat_data: dict) -> dict:
  """Reshape per-batch symbol_summaries into a cross-batch lookup for the comparison panel."""
  all_symbols: set[str] = set()
  batch_sym_data: list[dict] = []

  for bd in cat_data.get("batches", []):
    sym_map: dict[str, dict] = {}
    for s in bd.get("symbol_summaries", []):
      sym_map[s["asset"]] = s
      all_symbols.add(s["asset"])
    batch_sym_data.append({
      "batch_id": bd["batch_id"],
      "symbols": sym_map,
    })

  return {
    "all_symbols": sorted(all_symbols),
    "batches": batch_sym_data,
  }


# ---------------------------------------------------------------------------
# Streak Study — per-strategy streak pattern analysis (IS trades only)
# ---------------------------------------------------------------------------

class StreakStudyRequest(BaseModel):
  batch_ids: list[int]
  date_from: str | None = None
  date_to: str | None = None  # OOS boundary — only IS trades (entry <= date_to) are used


@router.post("/streak-study")
def streak_study(req: StreakStudyRequest, db: Session = Depends(get_db)):
  """Analyse win/loss streak patterns for selected batches using IS trades only.

  Returns transition probabilities, streak distributions, autocorrelation,
  chi-squared significance, recommendation, and a simulated comparison of
  Fixed vs Martingale vs Anti-Martingale.
  """
  from backend.analysis.streak_study import analyze

  if not req.batch_ids:
    raise HTTPException(400, "At least one batch_id is required")

  if not req.date_to:
    logger.warning("streak-study called without date_to — using ALL trades (IS + OOS)")

  # Load trades via lightweight loader (X04 — avoids 3M-row ORM bulk pull).
  from backend.analysis.batch_selection import load_trades_lightweight
  all_trades, _rbm, _binfo = load_trades_lightweight(db, batch_ids=list(req.batch_ids))

  if not all_trades:
    raise HTTPException(404, "No trades found for selected batches")

  # Filter to IS trades only: entry_time <= date_to (OOS boundary)
  if req.date_to:
    all_trades = [t for t in all_trades if t.entry_time and str(t.entry_time)[:10] <= req.date_to]

  # Optional date_from filter
  if req.date_from:
    all_trades = [t for t in all_trades if t.entry_time and str(t.entry_time)[:10] >= req.date_from]

  if not all_trades:
    raise HTTPException(404, "No IS trades found within the specified date range")

  result = analyze(all_trades)
  return result


# ── Auto Risk Profile ─────────────────────────────────────────────────

class AutoRiskProfileRequest(BaseModel):
  batch_ids: list[int]
  date_to: str | None = None      # IS boundary — only IS trades (entry ≤ date_to) are used
  date_from: str | None = None
  base_risk: float = 100.0        # fixed $ risk for assets that get "fixed" mode
  base_risk_mart: float = 50.0    # martingale base $ risk for assets that get mart/anti-mart
  multiplier: float = 2.0         # user's chosen multiplier
  initial_capital: float = 10_000.0
  aggression: str = "conservative" # "conservative" or "aggressive"
  min_confidence: float = 0.0     # below this confidence % → force fixed (0 = disabled)
  strategy_hint: str = "auto"     # "trend" | "meanrev" | "auto" — biases thresholds


@router.post("/auto-risk-profile")
def auto_risk_profile(req: AutoRiskProfileRequest, db: Session = Depends(get_db)):
  """Analyze IS trade streaks per asset per batch and recommend risk profiles.

  Returns per-asset-per-batch recommendations (mode, max_streak, start_after, reset_after)
  plus an aggregate config for the dominant mode across all assets.
  The same symbol in different batches (different strategies) gets independent analysis.
  """
  from backend.analysis.auto_risk_profile import (
    analyze_batch_trades,
    build_auto_risk_config,
  )

  if not req.batch_ids:
    raise HTTPException(400, "At least one batch_id is required")

  if not req.date_to:
    raise HTTPException(400, "date_to (IS boundary) is required — auto risk only analyzes IS trades to avoid data leakage from OOS")

  # Load trades via lightweight loader (X04 — avoids 3M-row ORM bulk pull).
  from backend.analysis.batch_selection import load_trades_lightweight
  all_trades, run_batch_map, _binfo = load_trades_lightweight(
    db, batch_ids=list(req.batch_ids),
  )

  # Batch → strategy name (needed for recommendation labels)
  batch_strategy_names: dict[int, str] = {}
  for bid in req.batch_ids:
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
    if batch:
      from backend.db.models import Strategy
      strat = db.query(Strategy).filter(Strategy.id == batch.strategy_id).first()
      batch_strategy_names[bid] = strat.name if strat else f"Batch #{bid}"
    else:
      batch_strategy_names[bid] = f"Batch #{bid}"

  if not all_trades:
    raise HTTPException(404, "No trades found for selected batches")

  # Filter to IS only — strictly entry_time <= date_to, OOS is never touched
  all_trades = [t for t in all_trades if t.entry_time and str(t.entry_time)[:10] <= req.date_to]
  if req.date_from:
    all_trades = [t for t in all_trades if t.entry_time and str(t.entry_time)[:10] >= req.date_from]

  if not all_trades:
    raise HTTPException(404, "No IS trades found within the specified date range")

  recommendations = analyze_batch_trades(
    all_trades,
    date_to=req.date_to,
    multiplier=req.multiplier,
    aggression=req.aggression,
    min_confidence=req.min_confidence,
    strategy_hint=req.strategy_hint,
    run_batch_map=run_batch_map,
    batch_strategy_names=batch_strategy_names,
  )

  result = build_auto_risk_config(
    recommendations,
    base_risk_fixed=req.base_risk,
    base_risk_mart=req.base_risk_mart,
    multiplier=req.multiplier,
    initial_capital=req.initial_capital,
    aggression=req.aggression,
  )
  return result
