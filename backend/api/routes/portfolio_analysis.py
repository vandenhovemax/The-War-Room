"""Portfolio analysis routes — OOS analysis, comparison, asset swap, portfolio test."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.analysis.boundary_clip import clip_is_trades
from backend.analysis.correlation import get_correlation_matrix
from backend.analysis.daily_pnl import build_daily_pnl_matrix
from backend.analysis.full_metrics import calculate_full_metrics
from backend.analysis.portfolio_grading import compute_portfolio_grade, compute_return_to_dd_ratio, detect_strategy_type
from backend.analysis.tv_data_loader import trades_from_db
from backend.constants import COMMISSION_PCT
from backend.db.database import SessionLocal, get_db
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  BatchBacktestRun,
  LiveEvent,
  Portfolio,
  PortfolioAsset,
  Strategy,
)
from backend.services.phase2_recorder import BASKET_EVENT_TYPES
from backend.utils.logger import get_logger

# Legacy LiveEvent.event_type values written by pre-T-002 code paths.
# Production DBs still contain rows with these labels, so the default
# stitcher filter must include them for backward-compat.
_LEGACY_BASKET_EVENT_TYPES = ("hybrid_nightly_refresh", "basket_restore")

# Default event-type filter for the legacy stitcher. Combines the canonical
# T-002 set (single source of truth in `phase2_recorder.BASKET_EVENT_TYPES`)
# with pre-T-002 legacy labels for backward-compat. Callers can opt into a
# narrower set by passing `event_types=BASKET_EVENT_TYPES` directly.
DEFAULT_STITCHER_EVENT_TYPES = tuple(BASKET_EVENT_TYPES) + _LEGACY_BASKET_EVENT_TYPES

logger = get_logger(__name__)
router = APIRouter()

# In-memory progress for portfolio test tasks
_test_progress: dict[str, dict] = {}

# Cancellation tracking for portfolio tests
_cancelled_tests: set[str] = set()


def _is_test_cancelled(task_id: str) -> bool:
  """Check if a portfolio test has been requested to cancel."""
  return task_id in _cancelled_tests


# --- Helpers ---


def _calc_wfe_pf(is_pf: float, oos_pf: float) -> float:
  """PF-based Walk Forward Efficiency: (OOS_PF - 1) / (IS_PF - 1).

  Measures how much of the IS *edge* (the excess above breakeven PF=1)
  survived out-of-sample.  Returns 0 when IS has no edge (PF <= 1).
  """
  is_edge = is_pf - 1.0
  if is_edge <= 0:
    return 0.0
  return (oos_pf - 1.0) / is_edge


def _get_portfolio(portfolio_id: int, db: Session) -> Portfolio:
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(404, "Portfolio not found")
  return p


def _single_asset_name(a) -> str:
  """Build a single asset column name from a PortfolioAsset.

  Handles legacy data where symbol may already contain _Long/_Short suffix.
  """
  if a.trade_side in ("Long", "Short"):
    suffix = f"_{a.trade_side}"
    return a.symbol if a.symbol.endswith(suffix) else f"{a.symbol}{suffix}"
  return a.symbol


def _portfolio_asset_names(p: Portfolio, *, active_only: bool = True) -> list[str]:
  """Build asset column names like BTCUSDT_Long from portfolio assets.

  When trade_side is 'both' (or unknown), both _Long and _Short variants are
  included so trades_from_db output (which always has a side suffix) matches.
  active_only=True (default) excludes deactivated assets (e.g. after swap).
  """
  names = []
  for a in p.assets:
    if active_only and not a.is_active:
      continue
    if a.trade_side in ("Long", "Short"):
      names.append(_single_asset_name(a))
    else:
      names.append(f"{a.symbol}_Long")
      names.append(f"{a.symbol}_Short")
  return names


def _compute_cost_summary(df: pd.DataFrame, settings: dict) -> dict:
  """Compute total stress costs from the portfolio's stored cost settings.

  Returns a summary dict with total, average, breakdown by cost type, and rates.
  Returns empty dict if no costs are configured or df is empty.
  """
  slip = float(settings.get("extra_slippage_pct", 0))
  comm = float(settings.get("extra_commission_pct", 0))
  fund = float(settings.get("funding_daily_pct", 0))
  sprd = float(settings.get("spread_pct", 0))
  if not any([slip, comm, fund, sprd]) or df.empty:
    return {}
  n = len(df)
  notionals = (df["Entry Price"] * df["Quantity"]).abs()
  slip_cost = float((notionals * (slip / 100) * 2).sum()) if slip > 0 else 0.0
  comm_cost = float((notionals * (comm / 100) * 2).sum()) if comm > 0 else 0.0
  sprd_cost = float((notionals * (sprd / 200) * 2).sum()) if sprd > 0 else 0.0
  fund_cost = 0.0
  if fund > 0 and "Entry Time" in df.columns and "Exit Time" in df.columns:
    hold_days = (
      (pd.to_datetime(df["Exit Time"]) - pd.to_datetime(df["Entry Time"]))
      .dt.total_seconds() / 86400
    ).clip(lower=0)
    fund_cost = float((notionals * (fund / 100) * hold_days).sum())
  total = slip_cost + comm_cost + sprd_cost + fund_cost
  return {
    "total_stress_cost": round(total, 2),
    "avg_cost_per_trade": round(total / n, 2) if n > 0 else 0,
    "cost_breakdown": {
      "slippage": round(slip_cost, 2),
      "commission": round(comm_cost, 2),
      "spread": round(sprd_cost, 2),
      "funding": round(fund_cost, 2),
    },
    "rates": {
      "slippage_pct": slip, "commission_pct": comm,
      "spread_pct": sprd, "funding_daily_pct": fund,
    },
    "trade_count": n,
  }


def _get_portfolio_batch_context(p: Portfolio, db: Session) -> dict:
  """Extract leverage, strategy name, config name from the portfolio's batch."""
  settings = json.loads(p.source_strategies) if p.source_strategies else {}
  bid = settings.get("batch_id") or (settings.get("batch_ids", [None]) or [None])[0]
  if not bid:
    return {"leverage": 1, "strategy_name": "", "config_name": ""}

  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
  if not batch:
    return {"leverage": 1, "strategy_name": "", "config_name": ""}

  params = json.loads(batch.parameters) if batch.parameters else {}
  lev = params.get("leverage", 1)

  # batch.name is typically "StrategyName — ConfigName"
  strat_name = ""
  config_name = ""
  if batch.name and " — " in batch.name:
    parts = batch.name.split(" — ", 1)
    strat_name = parts[0].strip()
    config_name = parts[1].strip()
  elif batch.name:
    strat_name = batch.name

  return {"leverage": lev, "strategy_name": strat_name, "config_name": config_name}


def _resolve_oos_end_date(p: Portfolio, req_override: str | None = None) -> str | None:
  """Resolve the canonical OOS end date for a saved portfolio.

  Single source of truth for OOS-end resolution across portfolio_oos_date,
  portfolio_live_views, and any future endpoint (Tier 1.1 L04 audit fix).

  Order:
    1. Explicit request override (if provided)
    2. settings['oos_end_date']
    3. settings['data_end_date']
    4. portfolio.created_at (first 10 chars)
  Returns the resolved date as a YYYY-MM-DD string, or None if none apply.
  """
  if req_override:
    return str(req_override)[:10]
  try:
    settings = json.loads(p.source_strategies) if p.source_strategies else {}
  except (ValueError, TypeError):
    settings = {}
  raw = settings.get("oos_end_date") or settings.get("data_end_date")
  if not raw and p.created_at:
    raw = (
      p.created_at.strftime("%Y-%m-%d")
      if hasattr(p.created_at, "strftime")
      else str(p.created_at)[:10]
    )
  return str(raw)[:10] if raw else None


def _load_portfolio_trades(
  p: Portfolio, db: Session, *, date_filter: str | None = None,
  clip_is: bool = True,
) -> pd.DataFrame:
  """Load standardized trade DataFrame for a portfolio's assets.

  Supports both single-strat (batch_id: int) and cross-strat (batch_ids: list).
  Each asset's trades are loaded ONLY from its source batch (batch_id on
  PortfolioAsset) to prevent cross-batch leakage when multiple batches share
  the same symbols.
  Optional date_filter: "is" | "oos" | "live" to limit by portfolio date periods.
  clip_is: when True, force-close trades straddling optimization_end_date.

  When the portfolio's stored settings contain extra costs or a risk profile,
  they are applied to the trades before returning (same math as the optimizer).
  """
  settings = json.loads(p.source_strategies) if p.source_strategies else {}

  # ── WFAS replay path ──
  # A portfolio saved from walk-forward optimization has the exact rotation
  # stored in settings. We replay that rotation directly instead of going
  # through portfolio_assets + batch queries, because portfolio_assets only
  # holds the FINAL window's basket — historical winning batches are missed.
  # "live" queries fall through to the legacy path to read real post-save
  # trades (replay data is optimization-period only by construction).
  if date_filter != "live":
    from backend.analysis.portfolio_wfo_replay import (
      has_wfo_replay_metadata,
      load_wfas_forward_append,
      load_wfas_replay,
    )
    if has_wfo_replay_metadata(settings):
      df = load_wfas_replay(settings, db)
      # Append forward-period trades on the currently-active basket.
      # load_wfas_replay stops at the last committed OOS rotation; without
      # this append the months after that boundary show empty in the matrix
      # / modal even though the live basket has been trading the whole time.
      # "is" asks for in-sample only → skip (forward is post-IS by def).
      if date_filter != "is":
        fwd = load_wfas_forward_append(p, settings, db)
        if not fwd.empty:
          df = pd.concat([df, fwd], ignore_index=True) if not df.empty else fwd
      if df.empty:
        return df
      if date_filter and "Entry Time" in df.columns:
        df["Entry Time"] = pd.to_datetime(df["Entry Time"])
        if date_filter == "is":
          # WFAS replay trades are OOS by construction.
          return pd.DataFrame()
        # "oos" / "oos_full" keep every row.
      return df

  # Collect all batch IDs — single-strat or cross-strat
  batch_ids: list[int] = []
  if settings.get("batch_ids"):
    batch_ids = settings["batch_ids"]
  elif settings.get("batch_id"):
    batch_ids = [settings["batch_id"]]

  # Fallback: derive from active assets when settings is missing batch refs.
  # PortfolioAsset.batch_id is the authoritative per-asset batch reference.
  if not batch_ids:
    batch_ids = sorted({a.batch_id for a in p.assets if a.is_active and a.batch_id is not None})

  if not batch_ids:
    logger.warning("Portfolio %d (%s) has no batch references in settings or assets", p.id, p.name)
    return pd.DataFrame()

  # Group portfolio assets by their source batch_id to prevent cross-batch leakage
  # Only include active assets (deactivated ones, e.g. after swap, are excluded)
  from collections import defaultdict
  batch_asset_names: dict[int | None, list[str]] = defaultdict(list)
  for a in p.assets:
    if not a.is_active:
      continue
    if a.trade_side in ("Long", "Short"):
      batch_asset_names[a.batch_id].append(_single_asset_name(a))
    else:
      batch_asset_names[a.batch_id].append(f"{a.symbol}_Long")
      batch_asset_names[a.batch_id].append(f"{a.symbol}_Short")

  # Collect all BacktestTrade objects (keeping batch tracking for risk scaling)
  all_trades: list[BacktestTrade] = []
  run_batch_map: dict[int, int] = {}  # run_id → batch_id
  allowed_names: set[str] = set()
  for bid, names in batch_asset_names.items():
    if bid is not None:
      runs = (
        db.query(BacktestRun)
        .filter(BacktestRun.batch_id == bid, BacktestRun.status == "completed")
        .all()
      )
    else:
      runs = (
        db.query(BacktestRun)
        .filter(BacktestRun.batch_id.in_(batch_ids), BacktestRun.status == "completed")
        .all()
      )
    if not runs:
      continue
    run_ids = [r.id for r in runs]
    trades = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).all()
    if clip_is and p.optimization_end_date:
      tf = settings.get("timeframe", "D")
      boundary = p.optimization_end_date.strftime("%Y-%m-%d") if hasattr(p.optimization_end_date, "strftime") else str(p.optimization_end_date)[:10]
      trades = clip_is_trades(trades, boundary, tf, db)
    # Track batch_id for each run (needed for heterogeneous risk scaling)
    for r in runs:
      run_batch_map[r.id] = bid if bid is not None else (batch_ids[0] if batch_ids else 0)
    all_trades.extend(trades)
    allowed_names.update(names)

  if not all_trades:
    logger.warning("Portfolio %d: no trades found after batch-scoped loading", p.id)
    return pd.DataFrame()

  # Apply date_from filter if stored (optimizer's IS start boundary)
  _date_from = settings.get("date_from") or settings.get("is_start_date")
  if _date_from:
    try:
      from datetime import datetime as _dt
      from_dt = _dt.strptime(str(_date_from)[:10], "%Y-%m-%d")
      all_trades = [t for t in all_trades if t.entry_time and t.entry_time >= from_dt]
    except (ValueError, TypeError):
      pass
    if not all_trades:
      logger.warning("Portfolio %d: no trades after date_from filter %s", p.id, _date_from)
      return pd.DataFrame()

  # ── Pre-filter to portfolio assets BEFORE costs & scaling ──
  # The martingale scaler groups by symbol for streak tracking. Without this,
  # ALL trades from batch runs are scaled together and non-portfolio trades
  # (e.g. ANKRUSDT_Short from B161) contaminate streak counts for selected
  # portfolio assets (e.g. ANKRUSDT_Long from B157).
  _pa_batch: set[tuple[int, str]] = set()   # (batch_id, "SYMBOL_Side")
  _pa_legacy: set[str] = set()              # names from None-batch (any batch OK)
  for _bid, _names in batch_asset_names.items():
    for _n in _names:
      if _bid is not None:
        _pa_batch.add((_bid, _n))
      else:
        _pa_legacy.add(_n)
  _pre = len(all_trades)
  all_trades = [
    t for t in all_trades
    if (
      (run_batch_map.get(t.run_id, -1),
       f"{t.symbol}_{(t.side or 'Long').title()}") in _pa_batch
      or f"{t.symbol}_{(t.side or 'Long').title()}" in _pa_legacy
    )
  ]
  if _pre != len(all_trades):
    logger.debug("Portfolio %d: pre-filtered %d → %d trades (portfolio assets only)",
                 p.id, _pre, len(all_trades))
  if not all_trades:
    logger.warning("Portfolio %d: no trades match portfolio assets after pre-filter", p.id)
    return pd.DataFrame()

  # Session filter — the saved exec-filter settings must apply on every
  # trade read, not only the WFO stitch path (_stitch_time_based_walkforward
  # / _load_trades_for_segment). entry_time is naive NYT by convention, so
  # hour/weekday checks match the optimizer's exec-filter semantics.
  _sf_h, _sf_d = _resolve_session_filter(settings)
  if _sf_h or _sf_d:
    all_trades = [
      t for t in all_trades
      if t.entry_time is not None
      and (not _sf_h or t.entry_time.hour not in _sf_h)
      and (not _sf_d or t.entry_time.weekday() not in _sf_d)
    ]
    if not all_trades:
      logger.warning("Portfolio %d: no trades after session filter", p.id)
      return pd.DataFrame()

  # ORDER: scale FIRST, then costs (Tier 1.1 L5/L01 audit fix).
  # The optimizer path in cross_strategy.py applies scaling before stress-cost
  # deduction so streak tracking sees true gross PnL signs. When costs were
  # applied first, marginal winners could flip to losses and poison streak
  # tracking for martingale modes — producing three different OOS values for
  # the same saved portfolio across optimizer vs summary vs dashboard views.

  # Apply stored risk profile scaling (must run BEFORE cost stress)
  rp = settings.get("risk_profile", {})
  if rp and rp.get("override_enabled", True):
    from types import SimpleNamespace
    from backend.api.routes.analysis import _apply_risk_scaling
    # Resolve date_to reliably: stored setting → portfolio model fallback
    _date_to = settings.get("date_to")
    if not _date_to and hasattr(p, 'optimization_end_date') and p.optimization_end_date:
      _date_to = (
        p.optimization_end_date.strftime("%Y-%m-%d")
        if hasattr(p.optimization_end_date, "strftime")
        else str(p.optimization_end_date)[:10]
      )
    mock_req = SimpleNamespace(
      risk_profile_mode=rp.get("mode", "fixed"),
      risk_profile_value=float(rp.get("initial_risk_usd", 0)),
      risk_profile_initial_capital=float(rp.get("initial_capital", 10000)),
      risk_profile_multiplier=float(rp.get("multiplier", 2.0)),
      risk_profile_max_streak=int(rp.get("max_streak", 3)),
      risk_profile_kelly_n=int(rp.get("kelly_n", 2)),
      risk_profile_kelly_warmup=int(rp.get("kelly_warmup", 20)),
      risk_profile_kelly_scope=rp.get("kelly_scope", "portfolio"),
      risk_profile_kelly_lookback=int(rp.get("kelly_lookback", 0)),
      risk_profile_kelly_cap=float(rp.get("kelly_cap", 0.0)),
      risk_profile_martingale_compound=rp.get("martingale_compound", "off"),
      risk_profile_martingale_scope=rp.get("martingale_scope", "asset"),
      risk_profile_martingale_count_mode=rp.get("martingale_count_mode", "full_position"),
      risk_profile_martingale_oos_reset=rp.get("martingale_oos_reset", True),
      risk_profile_martingale_start_after=int(rp.get("martingale_start_after", 0)),
      risk_profile_anti_martingale_reset_after=int(rp.get("anti_martingale_reset_after", 0)),
      risk_profile_sizing_master=rp.get("sizing_master", "portfolio"),
      risk_profile_batch_configs=json.dumps(rp["batch_configs"]) if rp.get("batch_configs") else None,
      risk_profile_auto_asset_configs=rp.get("auto_asset_configs"),
      batch_ids=batch_ids,
      batch_id=batch_ids[0] if batch_ids else None,
      date_to=_date_to,
    )
    # Cross-strategy portfolios group trades by symbol_Side (include_side=True)
    # to match how the optimizer scaled them — otherwise L/S share one streak.
    _is_cross = getattr(p, 'is_cross_strat', False) or settings.get("is_cross_strat", False)
    # Use saved IS-end state if available (deterministic replay on reload)
    _saved_is_end = rp.get("is_end_state")
    # Only ask the scaler to collect per-batch end-state on the FIRST load
    # (when nothing is saved yet). Every subsequent page load reuses the saved
    # state via initial_is_end_state and skips the bookkeeping cost.
    _need_state = not _saved_is_end and p.id > 0
    _result = _apply_risk_scaling(
      all_trades, mock_req, db, include_side=_is_cross,
      run_batch_map=run_batch_map,
      return_is_end_state=_need_state,
      initial_is_end_state=_saved_is_end,
    )
    if _need_state and isinstance(_result, tuple):
      all_trades, _is_end_state = _result
      if _is_end_state:
        try:
          rp["is_end_state"] = _is_end_state
          settings["risk_profile"] = rp
          p.source_strategies = json.dumps(settings)
          db.commit()
          logger.info("Portfolio %d: saved IS-end streak state (%d groups)", p.id, len(_is_end_state))
        except Exception as _e:
          logger.warning("Portfolio %d: failed to save IS-end state: %s", p.id, _e)
          db.rollback()
    elif isinstance(_result, tuple):
      all_trades = _result[0]
    else:
      all_trades = _result

  # Apply stored extra costs (slippage, commission, funding, spread) AFTER
  # scaling — this matches cross_strategy.py's optimizer order and prevents
  # streak-tracking contamination from cost-induced sign flips (Tier 1.1 L01).
  _slip = float(settings.get("extra_slippage_pct", 0))
  _comm = float(settings.get("extra_commission_pct", 0))
  _fund = float(settings.get("funding_daily_pct", 0))
  _sprd = float(settings.get("spread_pct", 0))
  if _slip > 0 or _comm > 0 or _fund > 0 or _sprd > 0:
    from backend.api.routes.analysis import _apply_stress_slippage
    all_trades = _apply_stress_slippage(
      all_trades, _slip, db,
      extra_commission_pct=_comm,
      funding_daily_pct=_fund,
      spread_pct=_sprd,
    )

  # Convert to DataFrame — per-batch to prevent cross-batch symbol duplication.
  # When the same symbol+side exists in multiple batches, the optimizer treats
  # them as separate assets (BTCUSDT~B5_Long vs BTCUSDT~B7_Long). We must only
  # include each batch's trades for assets actually selected from that batch.
  has_batch_scoped = any(a.batch_id is not None for a in p.assets if a.is_active)
  if has_batch_scoped:
    batch_trades_grouped: dict[int, list] = defaultdict(list)
    for t in all_trades:
      bid = run_batch_map.get(t.run_id, -1)
      batch_trades_grouped[bid].append(t)
    # None-batch assets (legacy) allowed from any batch
    none_names = set(batch_asset_names.get(None, []))
    dfs = []
    for bid, trades_list in batch_trades_grouped.items():
      if not trades_list:
        continue
      batch_df = trades_from_db(trades_list)
      if batch_df.empty:
        continue
      names = set(batch_asset_names.get(bid, [])) | none_names
      if names:
        batch_df = batch_df[batch_df["Asset"].isin(names)]
      if not batch_df.empty:
        dfs.append(batch_df)
    df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
  else:
    # Legacy path: no batch_id on assets — use simple filtering
    df = trades_from_db(all_trades)
    if not df.empty:
      df = df[df["Asset"].isin(allowed_names)]
  if df.empty:
    return df

  # Apply date period filter if requested
  # Use Entry Time for period attribution — a trade belongs to the period
  # where it was opened, even if it exits in a later period.
  if date_filter and not df.empty and "Entry Time" in df.columns:
    df["Entry Time"] = pd.to_datetime(df["Entry Time"])
    opt_end = p.optimization_end_date
    created = p.created_at
    if date_filter == "is" and opt_end:
      df = df[df["Entry Time"].dt.date <= pd.Timestamp(opt_end).date()]
    elif date_filter == "oos" and opt_end and created:
      df = df[(df["Entry Time"].dt.date > pd.Timestamp(opt_end).date()) &
              (df["Entry Time"].dt.date <= pd.Timestamp(created).date())]
    elif date_filter == "oos_full" and opt_end:
      df = df[df["Entry Time"].dt.date > pd.Timestamp(opt_end).date()]
    elif date_filter == "live":
      # WFAS portfolios: forward-period starts after the last *committed*
      # OOS rotation window (not portfolio.created_at, which typically
      # equals "today" and would leave zero trades). Non-WFAS falls back
      # to created_at.
      from backend.analysis.portfolio_wfo_replay import get_wfas_forward_cutoff
      cutoff = get_wfas_forward_cutoff(settings)
      if cutoff is None and created:
        cutoff = pd.Timestamp(created).date()
      if cutoff is not None:
        df = df[df["Entry Time"].dt.date > cutoff]
  return df



def _quick_metrics(df: pd.DataFrame) -> dict:
  """Compute compact metrics dict matching ISvsLivePanel's 10 KPI keys.

  Delegates to the shared compute_portfolio_metrics_from_trades() which
  uses the SAME methodology as the optimizer's _portfolio_metrics():
  - Daily PnL matrix with zero-day gaps filled
  - Max DD from daily PnL equity curve (not trade-level)
  - Stability via calculate_stability() (blended score, keeps zero days)
  - Sortino/Calmar from daily PnL including zero-PnL days
  """
  if df.empty:
    return {}
  from backend.analysis.portfolio_metrics import compute_portfolio_metrics_from_trades
  result = compute_portfolio_metrics_from_trades(df)
  # Add backward-compatible 'trades' alias
  result["trades"] = result.get("total_trades", 0)
  return result


def _serialize(metrics: dict) -> dict:
  """Make metrics JSON-serializable."""
  import numpy as np
  result = {}
  for k, v in metrics.items():
    if isinstance(v, (pd.DataFrame, pd.Series)):
      continue
    elif isinstance(v, (pd.Timestamp, pd.Timedelta)):
      result[k] = str(v) if pd.notna(v) else None
    elif isinstance(v, np.datetime64):
      result[k] = str(v) if not pd.isna(v) else None
    elif isinstance(v, (float, np.floating)):
      result[k] = None if (pd.isna(v) or np.isinf(v)) else round(float(v), 4)
    elif isinstance(v, (int, np.integer)):
      result[k] = int(v)
    elif isinstance(v, np.bool_):
      result[k] = bool(v)
    else:
      result[k] = v
  return result


def _safe_calculate_full_metrics(df: pd.DataFrame, **kwargs) -> dict:
  if df.empty:
    return {}
  try:
    return _serialize(calculate_full_metrics(df, **kwargs))
  except Exception as e:
    logger.error(f"calculate_full_metrics failed: {e}")
    return {}


# ── Walk-Forward trade loading ────────────────────────────────────────
# Saved WFO and hybrid Phase 2 portfolios keep only the FINAL basket in
# PortfolioAsset. Loading every trade from that basket over the whole period
# (the default _load_portfolio_trades behavior) fabricates a "fixed basket"
# backtest that never actually happened. The helpers below reconstruct the
# REAL walk-forward: for each window / rotation period, include only the
# basket that was active during that exact date range, then stitch together.


def _normalize_asset_tag(tag: str) -> tuple[str, str, int | None]:
  """Parse an asset tag into (symbol, side, batch_id).

  Formats encountered across the WFO paths (canonical: SYMBOL~B{id}_Side):
    "BTCUSDT_Long"                → ("BTCUSDT", "Long", None)
    "BTCUSDT~B12_Long"            → ("BTCUSDT", "Long", 12)  (rolling/static, canonical)
    "BTCUSDT_Long~B12"            → ("BTCUSDT", "Long", 12)  (legacy trailing form)
    "B12_BTCUSDT_Long"            → ("BTCUSDT", "Long", 12)  (hybrid per-batch prefix)
  """
  import re as _re
  s = tag
  bid: int | None = None

  m_mid = _re.search(r"~B(-?\d+)(?=_Long$|_Short$|$)", s)
  if m_mid:
    bid = int(m_mid.group(1))
    s = s[: m_mid.start()] + s[m_mid.end():]

  if bid is None:
    m_prefix = _re.match(r"^B(\d+)_(.+)$", s)
    if m_prefix:
      bid = int(m_prefix.group(1))
      s = m_prefix.group(2)

  side = "both"
  if s.endswith("_Long"):
    side = "Long"
    sym = s[: -len("_Long")]
  elif s.endswith("_Short"):
    side = "Short"
    sym = s[: -len("_Short")]
  else:
    sym = s
  return sym, side, bid


def _load_trades_for_segment(
  basket_tags: list[str],
  date_from: str | None,
  date_to: str | None,
  batch_ids_fallback: list[int],
  db: Session,
  sf_exclude_hours: set[int] | None = None,
  sf_exclude_days: set[int] | None = None,
) -> pd.DataFrame:
  """Load trades for a specific (basket, date range) slice.

  - basket_tags: asset tags ("SYMBOL_Side" or "SYMBOL~B{n}_Side" or "B{n}_SYMBOL_Side")
  - date_from/to: inclusive ISO date strings; None means unbounded on that side
  - batch_ids_fallback: batches to search when the tag doesn't embed one
  - sf_exclude_hours / sf_exclude_days: session filter — applied on entry_time
    (matches the check used by event_wfo._sf_exclude_* → real WFO truth)
  """
  if not basket_tags:
    return pd.DataFrame()

  # Group wanted (batch_id, symbol_side) pairs — batch_id=None means "any batch in fallback"
  wanted_keyed: set[tuple[int, str]] = set()   # (batch_id, "SYM_Side")
  wanted_loose: set[str] = set()                # "SYM_Side" — any batch allowed
  all_bids: set[int] = set()
  for tag in basket_tags:
    sym, side, bid = _normalize_asset_tag(tag)
    # Only L/S are recognizable trades; "both" tags are unusual — split them
    if side not in ("Long", "Short"):
      for s in ("Long", "Short"):
        name = f"{sym}_{s}"
        if bid is not None:
          wanted_keyed.add((bid, name))
          all_bids.add(bid)
        else:
          wanted_loose.add(name)
      continue
    name = f"{sym}_{side}"
    if bid is not None:
      wanted_keyed.add((bid, name))
      all_bids.add(bid)
    else:
      wanted_loose.add(name)

  if wanted_loose:
    all_bids.update(batch_ids_fallback)

  if not all_bids:
    return pd.DataFrame()

  runs = (
    db.query(BacktestRun)
    .filter(BacktestRun.batch_id.in_(sorted(all_bids)), BacktestRun.status == "completed")
    .all()
  )
  if not runs:
    return pd.DataFrame()
  run_batch_map = {r.id: r.batch_id for r in runs}
  run_ids = list(run_batch_map.keys())

  q = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids))
  if date_from:
    try:
      from datetime import datetime as _dt
      _df = _dt.strptime(str(date_from)[:10], "%Y-%m-%d")
      q = q.filter(BacktestTrade.entry_time >= _df)
    except (ValueError, TypeError):
      pass
  if date_to:
    try:
      from datetime import datetime as _dt, timedelta as _td
      _dt_end = _dt.strptime(str(date_to)[:10], "%Y-%m-%d") + _td(days=1)
      q = q.filter(BacktestTrade.entry_time < _dt_end)
    except (ValueError, TypeError):
      pass
  trades = q.all()
  if not trades:
    return pd.DataFrame()

  # Filter by (batch, symbol_side): each trade must match its batch's allowed basket
  _sf_active = bool(sf_exclude_hours or sf_exclude_days)
  kept: list = []
  for t in trades:
    # Session filter — entry-time gating, same check event_wfo uses
    if _sf_active:
      et = t.entry_time
      if et is None:
        continue
      if sf_exclude_hours and et.hour in sf_exclude_hours:
        continue
      if sf_exclude_days and et.weekday() in sf_exclude_days:
        continue
    bid = run_batch_map.get(t.run_id)
    name = f"{t.symbol}_{(t.side or 'Long').title()}"
    if bid is not None and (bid, name) in wanted_keyed:
      kept.append(t)
    elif name in wanted_loose:
      kept.append(t)
  if not kept:
    return pd.DataFrame()

  df = trades_from_db(kept)
  return df


def _resolve_session_filter(settings: dict) -> tuple[set[int], set[int]]:
  """Return (exclude_hours, exclude_days) sets per the saved WFO settings.

  Returns empty sets when session filter is disabled — no-op in the loader.
  """
  if not settings.get("session_filter_enabled"):
    return set(), set()
  if settings.get("session_filter_mode") != "fixed":
    return set(), set()
  return (
    set(settings.get("session_filter_exclude_hours") or []),
    set(settings.get("session_filter_exclude_days") or []),
  )


def _stitch_time_based_walkforward(
  settings: dict, batch_ids_fallback: list[int], db: Session,
) -> tuple[pd.DataFrame, list[dict]]:
  """Stitch per-window OOS trades for time-based WFO.

  Uses wfo_per_window (assets per window) + wfo_windows_meta (dates per window).
  Returns (stitched_df, per_segment_breakdown[]).
  """
  per_window = settings.get("wfo_per_window") or []
  windows_meta = settings.get("wfo_windows_meta") or []
  if not per_window or not windows_meta:
    return pd.DataFrame(), []
  sf_h, sf_d = _resolve_session_filter(settings)
  meta_by_num = {w.get("window_num"): w for w in windows_meta}
  frames: list[pd.DataFrame] = []
  breakdown: list[dict] = []
  for pw in per_window:
    wn = pw.get("window_num")
    meta = meta_by_num.get(wn) or {}
    oos_start = meta.get("oos_start")
    oos_end = meta.get("oos_end")
    assets = pw.get("assets") or []
    if not oos_start or not oos_end or not assets:
      continue
    seg = _load_trades_for_segment(
      assets, oos_start, oos_end, batch_ids_fallback, db,
      sf_exclude_hours=sf_h, sf_exclude_days=sf_d,
    )
    # Use saved oos_pnl from the WFO engine as the authoritative realized PnL —
    # it already accounts for risk sizing (Kelly / % equity / basket divisor)
    # which isn't carried on raw BacktestTrade rows. Reconstructed trades give
    # us trade count, dates, and WR for the breakdown.
    saved_oos_pnl = pw.get("oos_pnl")
    saved_oos_wfe = pw.get("oos_wfe")
    recon_pnl = 0.0 if seg.empty or "Net Profit" not in seg.columns \
                else float(seg["Net Profit"].sum())
    seg_row = {
      "window_num": wn,
      "oos_start": oos_start,
      "oos_end": oos_end,
      "assets": assets,
      "expected_oos_pnl": saved_oos_pnl,
      "expected_oos_wfe": saved_oos_wfe,
      "trades": 0 if seg.empty else int(len(seg)),
      "realized_pnl": float(saved_oos_pnl) if saved_oos_pnl is not None else recon_pnl,
      "reconstructed_pnl": recon_pnl,
    }
    breakdown.append(seg_row)
    if not seg.empty:
      frames.append(seg)
  stitched = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
  return stitched, breakdown


def _stitch_event_based_walkforward(
  settings: dict, p: Portfolio, batch_ids_fallback: list[int], db: Session,
  event_types: tuple[str, ...] | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
  """Stitch event-based (hybrid Phase 2) walk-forward.

  - Research OOS segment: driven by source_strategies.phase2_selection_changes
    (basket rotation events during the research backtest).
  - Live segment (post-cutoff): driven by LiveEvent rows whose `event_type`
    is in `event_types`. Default = `DEFAULT_STITCHER_EVENT_TYPES`, which
    includes the canonical T-002 set (`hybrid_refresh`, `hybrid_rotation`,
    `phase2_evaluated_no_change`) PLUS pre-T-002 legacy labels
    (`hybrid_nightly_refresh`, `basket_restore`) for backward-compat with
    older DBs.

  Callers wanting only the canonical T-002 set should pass
  `event_types=BASKET_EVENT_TYPES`. T-003 Delta 1: parameterised so
  external callers can opt into narrower / wider sets without touching
  this function's internals.

  T-003 Delta 5: legacy stitcher is retired for hybrid portfolios with
  `step2_result.selected_records` (the unified `load_wfas_replay +
  load_wfas_forward_append` pair is the source of truth instead). This
  function still RUNS for legacy callers, but logs a WARNING when fired
  on a hybrid portfolio — the route audit in Delta 3 confirmed no detail
  route hits this path for hybrids post-T-003, so the WARNING marks a
  regression. Non-hybrid (pre-T-002) callers log INFO as the expected
  fallback.
  """
  if event_types is None:
    event_types = DEFAULT_STITCHER_EVENT_TYPES
  # T-003 Delta 5 observability guard. Hybrid portfolios MUST go through
  # `get_portfolio_detail_dataframe` / `load_wfas_replay + forward_append`.
  # If the legacy stitcher fires on a hybrid portfolio, Delta 9's regression
  # test catches it — but the WARNING here is the runtime signal too.
  from backend.analysis.portfolio_wfo_replay import get_effective_step2
  _is_hybrid = bool(get_effective_step2(settings).get("selected_records"))
  if _is_hybrid:
    logger.warning(
      "T-003 regression: _stitch_event_based_walkforward fired on hybrid "
      "portfolio id=%s name=%r — should route through load_wfas_replay + "
      "load_wfas_forward_append instead. Continuing with legacy stitch.",
      getattr(p, "id", None), getattr(p, "name", None),
    )
  else:
    logger.info(
      "_stitch_event_based_walkforward fallback fired on non-hybrid "
      "portfolio id=%s (no step2_result.selected_records — legitimate "
      "pre-T-002 path).",
      getattr(p, "id", None),
    )
  changes_research = settings.get("phase2_selection_changes") or []
  initial_basket = settings.get("phase2_initial_basket") or []
  cutoff = p.optimization_end_date
  cutoff_iso = (
    cutoff.strftime("%Y-%m-%d") if hasattr(cutoff, "strftime")
    else (str(cutoff)[:10] if cutoff else None)
  )

  # Derive initial basket if not stored: replay changes in reverse from final
  if not initial_basket and changes_research:
    final_assets = [a.symbol + "_" + a.trade_side for a in p.assets if a.is_active
                    and a.trade_side in ("Long", "Short")]
    reconstructed = set(final_assets)
    for c in sorted(changes_research, key=lambda x: x.get("date", ""), reverse=True):
      for a in (c.get("added") or []):
        reconstructed.discard(a)
      for a in (c.get("removed") or []):
        reconstructed.add(a)
    initial_basket = sorted(reconstructed)

  segments: list[tuple[list[str], str | None, str | None, str]] = []
  # Research OOS segments: walk changes in date order
  if changes_research:
    sorted_chg = sorted(changes_research, key=lambda x: x.get("date", ""))
    active = set(initial_basket)
    prev_date: str | None = None
    for chg in sorted_chg:
      chg_date = chg.get("date")
      if prev_date and active:
        segments.append((sorted(active), prev_date, chg_date, "research"))
      for a in (chg.get("added") or []):
        active.add(a)
      for a in (chg.get("removed") or []):
        active.discard(a)
      prev_date = chg_date
    # Tail segment: from last change to cutoff
    if prev_date and active:
      segments.append((sorted(active), prev_date, cutoff_iso, "research"))

  # Live segments: from cutoff forward, using LiveEvent rows.
  # Filter via `event_types` parameter (default includes T-002 canonical
  # set + pre-T-002 legacy labels — see docstring).
  live_events = (
    db.query(LiveEvent)
    .filter(LiveEvent.portfolio_id == p.id,
            LiveEvent.event_type.in_(list(event_types)))
    .order_by(LiveEvent.created_at.asc())
    .all()
  )
  live_active: set[str] = set()
  # Seed live basket from whatever was final in research
  if changes_research and initial_basket is not None:
    live_active = set(initial_basket)
    for c in sorted(changes_research, key=lambda x: x.get("date", "")):
      for a in (c.get("added") or []):
        live_active.add(a)
      for a in (c.get("removed") or []):
        live_active.discard(a)
  elif not live_active:
    live_active = {a.symbol + "_" + a.trade_side for a in p.assets
                   if a.is_active and a.trade_side in ("Long", "Short")}

  prev_live_date = cutoff_iso
  for ev in live_events:
    try:
      det = json.loads(ev.details or "{}")
    except Exception:
      det = {}
    ev_date = ev.created_at.strftime("%Y-%m-%d") if ev.created_at else None
    if prev_live_date and live_active and ev_date:
      segments.append((sorted(live_active), prev_live_date, ev_date, "live"))
    for a in (det.get("added") or []):
      live_active.add(a)
    for a in (det.get("removed") or []):
      live_active.discard(a)
    prev_live_date = ev_date
  # Tail live segment: last event → today
  if prev_live_date and live_active:
    from datetime import datetime as _dt
    today_iso = _dt.utcnow().strftime("%Y-%m-%d")
    segments.append((sorted(live_active), prev_live_date, today_iso, "live"))

  sf_h, sf_d = _resolve_session_filter(settings)
  frames: list[pd.DataFrame] = []
  breakdown: list[dict] = []
  for basket, d_from, d_to, phase in segments:
    seg = _load_trades_for_segment(
      basket, d_from, d_to, batch_ids_fallback, db,
      sf_exclude_hours=sf_h, sf_exclude_days=sf_d,
    )
    breakdown.append({
      "phase": phase,
      "date_from": d_from,
      "date_to": d_to,
      "basket": basket,
      "trades": 0 if seg.empty else int(len(seg)),
      "realized_pnl": 0.0 if seg.empty or "Net Profit" not in seg.columns
                       else float(seg["Net Profit"].sum()),
    })
    if not seg.empty:
      frames.append(seg)
  stitched = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
  return stitched, breakdown


def _load_wfo_walkforward_trades(
  p: Portfolio, db: Session,
) -> tuple[str, pd.DataFrame, list[dict]]:
  """Return (mode, stitched_df, per_segment_breakdown[]).

  mode ∈ {"time", "event", "none"}. "none" means the portfolio lacks the
  saved walk-forward data (legacy save) — caller should fall back to the
  existing fixed-basket view with a banner.
  """
  settings = json.loads(p.source_strategies) if p.source_strategies else {}
  batch_ids: list[int] = settings.get("batch_ids") or []
  if not batch_ids:
    batch_ids = sorted({a.batch_id for a in p.assets if a.is_active and a.batch_id is not None})

  if settings.get("wfo_per_window") and settings.get("wfo_windows_meta"):
    df, breakdown = _stitch_time_based_walkforward(settings, batch_ids, db)
    return "time", df, breakdown

  if settings.get("phase2_selection_changes") is not None or (
    p.portfolio_source == "wfas_phase2"
  ):
    df, breakdown = _stitch_event_based_walkforward(settings, p, batch_ids, db)
    return "event", df, breakdown

  return "none", pd.DataFrame(), []


# --- Endpoints ---

@router.get("/{portfolio_id}/full-metrics")
def portfolio_full_metrics(portfolio_id: int, db: Session = Depends(get_db)):
  """Full 50+ KPI panel with L/S breakdown, equity curve, and monthly matrix."""
  p = _get_portfolio(portfolio_id, db)
  date_filter = "is" if p.optimization_end_date else None
  df = _load_portfolio_trades(p, db, date_filter=date_filter)
  if df.empty:
    return {"metrics": {}, "long_metrics": {}, "short_metrics": {},
            "equity_curve": [], "monthly_matrix": {}}

  # Extract batch context (leverage, strategy, config) for margin calculations
  ctx = _get_portfolio_batch_context(p, db)

  # Full metrics on all trades
  full = _safe_calculate_full_metrics(df, **ctx)

  # Sync metrics_snapshot on the portfolio so the list page stays current.
  # Use _quick_metrics for consistency with ISvsLivePanel.
  snapshot = _quick_metrics(df)
  if snapshot:
    p.metrics_snapshot = json.dumps(snapshot)
    db.commit()

  # L/S breakdown
  long_df = df[df["Asset"].str.endswith("_Long")] if "Asset" in df.columns else pd.DataFrame()
  short_df = df[df["Asset"].str.endswith("_Short")] if "Asset" in df.columns else pd.DataFrame()
  long_m = _safe_calculate_full_metrics(long_df, **ctx)
  short_m = _safe_calculate_full_metrics(short_df, **ctx)

  # Equity curve points (cumulative PnL by exit date)
  eq_points: list[dict] = []
  if not df.empty and "Exit Time" in df.columns:
    sorted_df = df.sort_values("Exit Time")
    cum = sorted_df["Net Profit"].cumsum()
    # Sample at most 500 points for chart performance
    step = max(1, len(cum) // 500)
    for i in range(0, len(cum), step):
      eq_points.append({
        "date": str(sorted_df.iloc[i]["Exit Time"].date()),
        "equity": round(float(cum.iloc[i]), 2),
      })
    # Always include last point
    if len(cum) > 0 and (len(eq_points) == 0 or eq_points[-1]["equity"] != round(float(cum.iloc[-1]), 2)):
      eq_points.append({
        "date": str(sorted_df.iloc[-1]["Exit Time"].date()),
        "equity": round(float(cum.iloc[-1]), 2),
      })

  # Monthly matrix — enriched with trade count and win rate per cell.
  # Bug fix (2026-04-27): Use Entry Time bucketing for WFAS replay portfolios
  # (matches `portfolio_month_trades` and `portfolio_monthly_filtered`). Without
  # this, the matrix cell and the drill-down modal disagree for any trade that
  # enters in one month and exits in the next — the table grouped by exit time,
  # the modal grouped by entry time, so months with cross-boundary trades
  # showed totally different counts and PnL totals.
  from backend.analysis.portfolio_wfo_replay import has_wfo_replay_metadata
  _settings_for_bucket = json.loads(p.source_strategies) if p.source_strategies else {}
  bucket_col = "Entry Time" if has_wfo_replay_metadata(_settings_for_bucket) else "Exit Time"

  monthly: dict[str, dict[str, dict]] = {}
  if not df.empty and bucket_col in df.columns:
    df_m = df.copy()
    df_m[bucket_col] = pd.to_datetime(df_m[bucket_col])
    df_m["Year"] = df_m[bucket_col].dt.year
    df_m["Month"] = df_m[bucket_col].dt.month
    for (yr, mo), grp in df_m.groupby(["Year", "Month"]):
      yr_str = str(int(yr))
      if yr_str not in monthly:
        monthly[yr_str] = {}
      total = len(grp)
      wins = len(grp[grp["Net Profit"] > 0])
      monthly[yr_str][str(int(mo))] = {
        "pnl": round(float(grp["Net Profit"].sum()), 2),
        "trades": total,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
      }

  # Also include the full equity curve (IS+OOS) for the chart with red OOS segment
  full_eq_points: list[dict] = []
  if p.optimization_end_date:
    full_df = _load_portfolio_trades(p, db)
    if not full_df.empty and "Exit Time" in full_df.columns:
      full_df["Exit Time"] = pd.to_datetime(full_df["Exit Time"])
      sorted_full = full_df.sort_values("Exit Time")
      cum_full = sorted_full["Net Profit"].cumsum()
      step_f = max(1, len(cum_full) // 500)
      for i in range(0, len(cum_full), step_f):
        full_eq_points.append({
          "date": str(sorted_full.iloc[i]["Exit Time"].date()),
          "equity": round(float(cum_full.iloc[i]), 2),
        })
      if len(cum_full) > 0 and (len(full_eq_points) == 0 or full_eq_points[-1]["equity"] != round(float(cum_full.iloc[-1]), 2)):
        full_eq_points.append({
          "date": str(sorted_full.iloc[-1]["Exit Time"].date()),
          "equity": round(float(cum_full.iloc[-1]), 2),
        })

  opt_end_str = None
  if p.optimization_end_date:
    opt_end_str = p.optimization_end_date.isoformat() if hasattr(p.optimization_end_date, "isoformat") else str(p.optimization_end_date)

  return {
    "metrics": full,
    "long_metrics": long_m,
    "short_metrics": short_m,
    "equity_curve": eq_points,
    "full_equity_curve": full_eq_points if full_eq_points else eq_points,
    "monthly_matrix": monthly,
    "optimization_end_date": opt_end_str,
  }


@router.get("/{portfolio_id}/full-metrics-wfo")
def portfolio_full_metrics_wfo(
  portfolio_id: int,
  risk_adjust: int = 0,
  db: Session = Depends(get_db),
):
  """Full 50+ KPI panel using walk-forward stitched trades (OOS-only truth).

  Mirrors /full-metrics shape so the same frontend component can render it,
  but uses the real per-window / per-rotation OOS instead of the fixed-basket
  hypothetical. For non-WFO portfolios returns empty metrics.

  WFAS Phase 2 portfolios with persisted step2_result take the replay path —
  every KPI is computed from Phase 2's selected_records (entry-date bucketed,
  in-basket-at-entry already enforced by the optimizer).

  Query params:
    risk_adjust=1 — rescale DB PnLs (base: $100 risk × pnl_multiplier) to the
      portfolio's user-defined risk profile. Supported for fixed-$ and
      %-equity modes; martingale/Kelly return unchanged with a flag in the
      response so the UI can disable the toggle for those modes.
  """
  p = _get_portfolio(portfolio_id, db)

  # ── Phase 2 replay path ──
  # When a WFAS Phase 2 portfolio has step2_result stored, load trades via
  # the replay helper and use the pre-computed monthly matrix / equity curve
  # that already match the Phase 2 optimizer page. This skips the legacy
  # stitcher entirely so the portfolio page agrees with the optimizer.
  settings = json.loads(p.source_strategies) if p.source_strategies else {}
  risk_adjust_flag = bool(risk_adjust)
  risk_adjust_applied = False
  risk_adjust_supported = True
  # T-003 Delta 3: route through the unified helper. `get_portfolio_detail_dataframe`
  # returns the merged committed + forward DF for hybrid WFAS portfolios,
  # or None for non-hybrid (legacy stitcher fallback below).
  from backend.analysis.portfolio_wfo_replay import (
    get_effective_step2,
    get_portfolio_detail_dataframe,
    get_wfas_forward_cutoff,
    rescale_df_to_user_risk,
    resolve_risk_context,
  )
  # Anchored-walk fix (2026-06-10): read the MERGED save+refresh view, not
  # the raw save blob. The save blob's matrix/equity stop at the committed
  # boundary; nightly-walk records live in refresh_step2_result. The merged
  # view rebuilds the roll-ups from all records so post-anchor months show.
  step2 = get_effective_step2(settings) or (settings.get("step2_result") or {})
  unified_df = get_portfolio_detail_dataframe(p, settings, db)
  if unified_df is not None:
    df = unified_df
    # `has_forward` gates the use of the pre-computed `aggregate_monthly_matrix`
    # / `aggregate_equity_curve` (frozen at last commit). When forward-period
    # rows are present we MUST rebuild from the DF so post-cutoff months are
    # included. Derive from the DF itself — no extra DB hit.
    cutoff = get_wfas_forward_cutoff(settings)
    has_forward = False
    if cutoff is not None and not df.empty and "Entry Time" in df.columns:
      max_entry = pd.to_datetime(df["Entry Time"]).max()
      if pd.notna(max_entry):
        has_forward = max_entry.date() > cutoff
    # Risk-adjust rescale (Task B): resolve (risk_profile, batch_risk_amount)
    # — saved profile wins, else fall back to .env (what live orders use).
    # fixed/pct supported; martingale/kelly pass through unchanged.
    if risk_adjust_flag:
      _bids: list[int] = settings.get("batch_ids") or []
      if not _bids and settings.get("batch_id"):
        _bids = [settings["batch_id"]]
      rp, _base = resolve_risk_context(settings, _bids, db)
      df, risk_adjust_supported = rescale_df_to_user_risk(df, rp, _base)
      risk_adjust_applied = risk_adjust_supported
    if df.empty:
      return {"metrics": {}, "long_metrics": {}, "short_metrics": {},
              "equity_curve": [], "monthly_matrix": {}, "mode": "replay",
              "risk_adjust_applied": risk_adjust_applied,
              "risk_adjust_supported": risk_adjust_supported}

    ctx = _get_portfolio_batch_context(p, db)
    full = _safe_calculate_full_metrics(df, **ctx)
    long_df = df[df["Asset"].str.endswith("_Long")] if "Asset" in df.columns else pd.DataFrame()
    short_df = df[df["Asset"].str.endswith("_Short")] if "Asset" in df.columns else pd.DataFrame()
    long_m = _safe_calculate_full_metrics(long_df, **ctx)
    short_m = _safe_calculate_full_metrics(short_df, **ctx)

    # Prefer the pre-computed Phase 2 grid when present — already entry-date
    # bucketed by step2_service. If absent, if forward-period trades were
    # appended, OR if a risk-adjust rescale was applied, fall back to
    # building from the combined DF so the matrix reflects current units.
    monthly_matrix: dict = {}
    agg_matrix = step2.get("aggregate_monthly_matrix") or {}
    if agg_matrix.get("matrix") and not has_forward and not risk_adjust_applied:
      for yr, months in agg_matrix["matrix"].items():
        monthly_matrix[str(yr)] = {}
        for mo, pnl in months.items():
          # Count trades + wins for this bucket from selected_records.
          yr_i, mo_i = int(yr), int(mo)
          month_rows = df[
            (pd.to_datetime(df["Entry Time"]).dt.year == yr_i) &
            (pd.to_datetime(df["Entry Time"]).dt.month == mo_i)
          ] if "Entry Time" in df.columns else df.iloc[0:0]
          total = int(len(month_rows))
          wins = int((month_rows["Net Profit"] > 0).sum()) if total else 0
          monthly_matrix[str(yr)][str(mo_i)] = {
            "pnl": round(float(pnl), 2),
            "trades": total,
            "win_rate": round(wins / total * 100, 1) if total else 0.0,
          }
    elif "Entry Time" in df.columns:
      df_m = df.copy()
      df_m["Entry Time"] = pd.to_datetime(df_m["Entry Time"])
      df_m["Year"] = df_m["Entry Time"].dt.year
      df_m["Month"] = df_m["Entry Time"].dt.month
      for (yr, mo), grp in df_m.groupby(["Year", "Month"]):
        yr_str = str(int(yr))
        if yr_str not in monthly_matrix:
          monthly_matrix[yr_str] = {}
        total = len(grp)
        wins = int((grp["Net Profit"] > 0).sum())
        monthly_matrix[yr_str][str(int(mo))] = {
          "pnl": round(float(grp["Net Profit"].sum()), 2),
          "trades": total,
          "win_rate": round(wins / total * 100, 1) if total else 0.0,
        }

    # Equity curve: reuse the Phase 2 curve when stored; else build from
    # selected_records sorted by entry time. When forward-period trades
    # are appended or risk-adjust is active, rebuild from DF so the curve
    # extends past the last committed rotation and reflects current units.
    eq_points: list[dict] = []
    agg_eq = step2.get("aggregate_equity_curve") or []
    if agg_eq and not has_forward and not risk_adjust_applied:
      # Downsample to at most ~500 points so the frontend stays snappy.
      step_n = max(1, len(agg_eq) // 500)
      for i in range(0, len(agg_eq), step_n):
        pt = agg_eq[i]
        eq_points.append({
          "date": str(pt.get("date", ""))[:10],
          "equity": round(float(pt.get("equity", 0) or 0), 2),
        })
      if agg_eq and (not eq_points or eq_points[-1]["date"] != str(agg_eq[-1].get("date", ""))[:10]):
        last = agg_eq[-1]
        eq_points.append({
          "date": str(last.get("date", ""))[:10],
          "equity": round(float(last.get("equity", 0) or 0), 2),
        })
    elif "Entry Time" in df.columns:
      sorted_df = df.sort_values("Entry Time")
      cum = sorted_df["Net Profit"].cumsum()
      step_n = max(1, len(cum) // 500)
      for i in range(0, len(cum), step_n):
        et = sorted_df.iloc[i]["Entry Time"]
        eq_points.append({
          "date": str(pd.Timestamp(et).date()) if et is not None else "",
          "equity": round(float(cum.iloc[i]), 2),
        })
      if len(cum) > 0 and (not eq_points or eq_points[-1]["equity"] != round(float(cum.iloc[-1]), 2)):
        et = sorted_df.iloc[-1]["Entry Time"]
        eq_points.append({
          "date": str(pd.Timestamp(et).date()) if et is not None else "",
          "equity": round(float(cum.iloc[-1]), 2),
        })

    opt_end_str = (
      p.optimization_end_date.strftime("%Y-%m-%d")
      if getattr(p, "optimization_end_date", None) and hasattr(p.optimization_end_date, "strftime")
      else (str(p.optimization_end_date)[:10] if getattr(p, "optimization_end_date", None) else None)
    )

    return {
      "metrics": full,
      "long_metrics": long_m,
      "short_metrics": short_m,
      "equity_curve": eq_points,
      "full_equity_curve": eq_points,
      "monthly_matrix": monthly_matrix,
      "optimization_end_date": opt_end_str,
      "mode": "replay",
      "risk_adjust_applied": risk_adjust_applied,
      "risk_adjust_supported": risk_adjust_supported,
    }

  # ── Non-hybrid WFAS replay path ──
  # `get_portfolio_detail_dataframe` returns None for non-hybrid (T-003 hybrid-only
  # gate). But `load_wfas_replay` works for both: it dispatches to
  # `_load_replay_non_hybrid` for non-hybrid portfolios, applying pnl_multiplier
  # consistently. Use that directly so the WFO route's monthly grid matches
  # `/full-metrics` and `/month-trades` (which both already use this path).
  #
  # The previous fallback used `_load_wfo_walkforward_trades` →
  # `_stitch_time_based_walkforward` → `trades_from_db`, which loaded raw DB
  # PnL WITHOUT applying pnl_multiplier — but then concatenated forward rows
  # via `load_wfas_forward_append` which DID apply multiplier. Result: the
  # monthly grid showed 1× values for committed months and 4× values for
  # April (forward), so portfolio 353's March cell was $504 instead of $2,017
  # while April was correctly -$2,192. Inconsistent units in the same DF.
  from backend.analysis.portfolio_wfo_replay import (
    has_wfo_replay_metadata,
    load_wfas_forward_append,
    load_wfas_replay,
    rescale_df_to_user_risk,
    resolve_risk_context,
  )

  if has_wfo_replay_metadata(settings):
    df = load_wfas_replay(settings, db)
    fwd = load_wfas_forward_append(p, settings, db)
    if not fwd.empty:
      df = pd.concat([df, fwd], ignore_index=True) if not df.empty else fwd
    if df.empty:
      return {"metrics": {}, "long_metrics": {}, "short_metrics": {},
              "equity_curve": [], "monthly_matrix": {}, "mode": "replay"}
    mode = "replay"
  else:
    # ── Truly legacy fallback (event-based or no replay metadata) ──
    mode, df, _breakdown = _load_wfo_walkforward_trades(p, db)
    if mode == "none" or df.empty:
      return {"metrics": {}, "long_metrics": {}, "short_metrics": {},
              "equity_curve": [], "monthly_matrix": {}, "mode": mode}
    fwd = load_wfas_forward_append(p, settings, db)
    if not fwd.empty:
      df = pd.concat([df, fwd], ignore_index=True) if not df.empty else fwd

  # Risk-adjust rescale (Task B). Resolves (risk_profile, batch_risk_amount)
  # — saved profile wins, else .env fallback. fixed/pct supported.
  if risk_adjust_flag:
    _bids: list[int] = settings.get("batch_ids") or []
    if not _bids and settings.get("batch_id"):
      _bids = [settings["batch_id"]]
    rp, _base = resolve_risk_context(settings, _bids, db)
    df, risk_adjust_supported = rescale_df_to_user_risk(df, rp, _base)
    risk_adjust_applied = risk_adjust_supported

  ctx = _get_portfolio_batch_context(p, db)
  full = _safe_calculate_full_metrics(df, **ctx)

  long_df = df[df["Asset"].str.endswith("_Long")] if "Asset" in df.columns else pd.DataFrame()
  short_df = df[df["Asset"].str.endswith("_Short")] if "Asset" in df.columns else pd.DataFrame()
  long_m = _safe_calculate_full_metrics(long_df, **ctx)
  short_m = _safe_calculate_full_metrics(short_df, **ctx)

  # Note: df["Net Profit"] is already in final units (committed rows were
  # pre-scaled above, forward rows carry pnl_multiplier). No further `* scale`
  # needed here or in metrics.
  eq_points: list[dict] = []
  if not df.empty and "Exit Time" in df.columns:
    sorted_df = df.sort_values("Exit Time")
    cum = sorted_df["Net Profit"].cumsum()
    step = max(1, len(cum) // 500)
    for i in range(0, len(cum), step):
      eq_points.append({
        "date": str(sorted_df.iloc[i]["Exit Time"].date()),
        "equity": round(float(cum.iloc[i]), 2),
      })
    if len(cum) > 0 and (len(eq_points) == 0 or eq_points[-1]["equity"] != round(float(cum.iloc[-1]), 2)):
      eq_points.append({
        "date": str(sorted_df.iloc[-1]["Exit Time"].date()),
        "equity": round(float(cum.iloc[-1]), 2),
      })

  # Monthly matrix — WFO route always deals with WFAS replay portfolios, so
  # Entry Time bucketing is always correct here (matches the in-basket-at-entry
  # contract and the IS/OOS split in `portfolio_oos_date`).
  monthly: dict[str, dict[str, dict]] = {}
  if not df.empty and "Entry Time" in df.columns:
    df_m = df.copy()
    df_m["Entry Time"] = pd.to_datetime(df_m["Entry Time"])
    df_m["Year"] = df_m["Entry Time"].dt.year
    df_m["Month"] = df_m["Entry Time"].dt.month
    for (yr, mo), grp in df_m.groupby(["Year", "Month"]):
      yr_str = str(int(yr))
      if yr_str not in monthly:
        monthly[yr_str] = {}
      total = len(grp)
      wins = len(grp[grp["Net Profit"] > 0])
      monthly[yr_str][str(int(mo))] = {
        "pnl": round(float(grp["Net Profit"].sum()), 2),
        "trades": total,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
      }

  return {
    "metrics": full,
    "long_metrics": long_m,
    "short_metrics": short_m,
    "equity_curve": eq_points,
    "full_equity_curve": eq_points,
    "monthly_matrix": monthly,
    "mode": mode,
    "risk_adjust_applied": risk_adjust_applied,
    "risk_adjust_supported": risk_adjust_supported,
  }


@router.post("/{portfolio_id}/backfill-wfas-step2")
def portfolio_backfill_wfas_step2(
  portfolio_id: int,
  wfo_run_id: int,
  db: Session = Depends(get_db),
):
  """One-shot backfill for legacy WFAS Phase 2 portfolios saved before
  step2_result started being persisted on the portfolio.

  Reads step2_result from the given WFORun.optimizer_settings and merges it
  into the portfolio's source_strategies JSON so has_wfo_replay_metadata()
  returns True and every downstream view (monthly table, modal, full metrics)
  agrees with the optimizer Phase 2 page.

  Idempotent: calling twice is safe — the latest step2_result wins.
  """
  from backend.db.models import WFORun

  p = _get_portfolio(portfolio_id, db)

  r = db.query(WFORun).filter(WFORun.id == wfo_run_id).first()
  if not r:
    raise HTTPException(404, f"WFORun {wfo_run_id} not found")

  opt_settings = json.loads(r.optimizer_settings) if r.optimizer_settings else {}
  step2 = opt_settings.get("step2_result")
  if not isinstance(step2, dict) or not step2.get("selected_records"):
    raise HTTPException(
      400,
      f"WFORun {wfo_run_id} has no step2_result.selected_records to backfill",
    )

  settings = json.loads(p.source_strategies) if p.source_strategies else {}
  # Keep only the fields the portfolio replay path reads — no need to drag
  # per_window diagnostics etc. into source_strategies.
  settings["step2_result"] = {
    k: step2[k] for k in (
      "selected_records",
      "aggregate_monthly_matrix",
      "aggregate_equity_curve",
      "aggregate_breakdown",
      "cumulative_metrics",
    ) if step2.get(k) is not None
  }
  p.source_strategies = json.dumps(settings)
  db.commit()

  return {
    "ok": True,
    "portfolio_id": portfolio_id,
    "wfo_run_id": wfo_run_id,
    "records_copied": len(step2.get("selected_records") or []),
    "has_monthly_matrix": bool(step2.get("aggregate_monthly_matrix")),
  }


@router.get("/{portfolio_id}/live-metrics")
def portfolio_live_metrics(
  portfolio_id: int,
  mode: str = "real",
  db: Session = Depends(get_db),
):
  """Full metrics for LIVE trades since the portfolio was saved.

  mode ∈ {"real", "paper", "both"} — filters trades.is_paper.
  Returns the same shape as /full-metrics so the frontend can render the
  same rich KPI panel.
  """
  from backend.db.models import Trade
  p = _get_portfolio(portfolio_id, db)

  q = db.query(Trade).filter(
    Trade.portfolio_id == portfolio_id,
    Trade.is_test == False,  # noqa: E712
  )
  if mode == "real":
    q = q.filter(Trade.is_paper == False)  # noqa: E712
  elif mode == "paper":
    q = q.filter(Trade.is_paper == True)  # noqa: E712

  # Only trades that closed after portfolio creation count as "live for this save".
  save_dt = p.created_at
  if save_dt is not None:
    q = q.filter(Trade.closed_at >= save_dt)

  trades = q.all()
  if not trades:
    return {"metrics": {}, "long_metrics": {}, "short_metrics": {},
            "equity_curve": [], "monthly_matrix": {}, "trade_count": 0,
            "since": save_dt.isoformat() if save_dt else None, "mode": mode}

  rows = []
  for t in trades:
    asset = f"{t.symbol}_{t.side}"
    rows.append({
      "Asset": asset,
      "Net Profit": float(t.realized_pnl or 0.0),
      "Entry Time": t.opened_at,
      "Exit Time": t.closed_at,
      "Entry Price": float(t.entry_price or 0.0),
      "Exit Price": float(t.exit_price or 0.0),
    })
  df = pd.DataFrame(rows)
  df["Exit Time"] = pd.to_datetime(df["Exit Time"])
  df["Entry Time"] = pd.to_datetime(df["Entry Time"])

  ctx = _get_portfolio_batch_context(p, db)
  full = _safe_calculate_full_metrics(df, **ctx)

  long_df = df[df["Asset"].str.endswith("_Long")]
  short_df = df[df["Asset"].str.endswith("_Short")]
  long_m = _safe_calculate_full_metrics(long_df, **ctx)
  short_m = _safe_calculate_full_metrics(short_df, **ctx)

  eq_points: list[dict] = []
  sorted_df = df.sort_values("Exit Time")
  cum = sorted_df["Net Profit"].cumsum()
  step = max(1, len(cum) // 500)
  for i in range(0, len(cum), step):
    eq_points.append({
      "date": str(sorted_df.iloc[i]["Exit Time"].date()),
      "equity": round(float(cum.iloc[i]), 2),
    })
  if len(cum) > 0 and (len(eq_points) == 0 or eq_points[-1]["equity"] != round(float(cum.iloc[-1]), 2)):
    eq_points.append({
      "date": str(sorted_df.iloc[-1]["Exit Time"].date()),
      "equity": round(float(cum.iloc[-1]), 2),
    })

  monthly: dict[str, dict[str, dict]] = {}
  df_m = df.copy()
  df_m["Year"] = df_m["Exit Time"].dt.year
  df_m["Month"] = df_m["Exit Time"].dt.month
  for (yr, mo), grp in df_m.groupby(["Year", "Month"]):
    yr_str = str(int(yr))
    if yr_str not in monthly:
      monthly[yr_str] = {}
    total = len(grp)
    wins = len(grp[grp["Net Profit"] > 0])
    monthly[yr_str][str(int(mo))] = {
      "pnl": round(float(grp["Net Profit"].sum()), 2),
      "trades": total,
      "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
    }

  return {
    "metrics": full,
    "long_metrics": long_m,
    "short_metrics": short_m,
    "equity_curve": eq_points,
    "full_equity_curve": eq_points,
    "monthly_matrix": monthly,
    "trade_count": len(trades),
    "since": save_dt.isoformat() if save_dt else None,
    "mode": mode,
  }


@router.get("/{portfolio_id}/wfo-timeline")
def portfolio_wfo_timeline(portfolio_id: int, db: Session = Depends(get_db)):
  """Real walk-forward timeline for saved WFO / hybrid Phase 2 portfolios.

  Returns stitched metrics (per-window / per-rotation basket applied to its
  own date range) plus a per-segment breakdown and the "live" tail since the
  optimization cutoff. Falls back with mode='none' for legacy saved portfolios
  that don't carry walk-forward metadata — the frontend then shows a banner
  asking the user to re-save from the WFO panel.

  Response shape:
    {
      mode: "time" | "event" | "none",
      optimization_end_date: ISO string | null,
      metrics: {...},                     # aggregate KPIs over stitched OOS
      equity_curve: [{date, equity}],     # stitched equity
      segments: [ ... per-window / per-rotation breakdown ],
      has_walkforward: bool,              # false for legacy saves
    }
  """
  p = _get_portfolio(portfolio_id, db)
  mode, df, segments = _load_wfo_walkforward_trades(p, db)
  opt_end = p.optimization_end_date
  opt_end_str = (
    opt_end.strftime("%Y-%m-%d") if hasattr(opt_end, "strftime")
    else (str(opt_end)[:10] if opt_end else None)
  )

  has_walkforward = mode != "none"
  if df.empty:
    return {
      "mode": mode,
      "optimization_end_date": opt_end_str,
      "metrics": {},
      "equity_curve": [],
      "segments": segments,
      "has_walkforward": has_walkforward,
    }

  # Sort by entry time for a clean equity curve
  if "Entry Time" in df.columns:
    df = df.sort_values("Entry Time").reset_index(drop=True)

  metrics = _serialize(_quick_metrics(df))

  # Override aggregate $ values with the engine's own per-window OOS PnL sum
  # (authoritative — already includes risk sizing the raw trades don't carry).
  # WR / trade count stay from reconstruction since those are sizing-invariant.
  saved_pnl_sum = 0.0
  saved_pnl_count = 0
  for s in segments:
    p_saved = s.get("expected_oos_pnl")
    if p_saved is not None:
      saved_pnl_sum += float(p_saved)
      saved_pnl_count += 1
  if saved_pnl_count > 0:
    metrics["net_profit"] = saved_pnl_sum
    recon_pnl = float(df["Net Profit"].sum()) if "Net Profit" in df.columns else 0.0
    metrics["reconstructed_net_profit"] = recon_pnl
    # Scale max_drawdown by the same ratio so it stays comparable to net_profit.
    # This is a proxy — the real engine DD on sized PnL may differ in shape.
    if recon_pnl != 0 and metrics.get("max_drawdown") is not None:
      scale = saved_pnl_sum / recon_pnl
      try:
        metrics["max_drawdown"] = float(metrics["max_drawdown"]) * scale
      except (TypeError, ValueError):
        pass

  # Cumulative equity curve (reconstructed shape; scale if we have the saved sum)
  equity_curve: list[dict] = []
  if "Entry Time" in df.columns and "Net Profit" in df.columns:
    pnl_series = df["Net Profit"]
    recon_total = float(pnl_series.sum())
    if saved_pnl_count > 0 and recon_total != 0:
      scale = saved_pnl_sum / recon_total
      pnl_series = pnl_series * scale
    cum = pnl_series.cumsum()
    for dt_val, eq_val in zip(df["Entry Time"].tolist(), cum.tolist()):
      try:
        dt_iso = pd.Timestamp(dt_val).strftime("%Y-%m-%d")
      except Exception:
        dt_iso = str(dt_val)[:10]
      equity_curve.append({"date": dt_iso, "equity": float(eq_val)})

  return {
    "mode": mode,
    "optimization_end_date": opt_end_str,
    "metrics": metrics,
    "equity_curve": equity_curve,
    "segments": segments,
    "has_walkforward": has_walkforward,
  }


class OOSRequest(BaseModel):
  split_pct: float = 70.0  # IS percentage (0-100)
  clip_is: bool = True   # force-close IS trades at IS boundary close price
  clip_oos: bool = True  # force-close OOS trades at OOS end close price (OFF = exclude unfinished)


@router.post("/{portfolio_id}/oos")
def portfolio_oos(portfolio_id: int, req: OOSRequest, db: Session = Depends(get_db)):
  """OOS analysis: split portfolio trades into IS / OOS windows."""
  from backend.analysis.boundary_clip import clip_df_at_boundary
  p = _get_portfolio(portfolio_id, db)
  df = _load_portfolio_trades(p, db, clip_is=req.clip_is)
  if df.empty:
    return {"is_metrics": {}, "oos_metrics": {}, "wfe": 0, "cutoff": None}

  df["Entry Time"] = pd.to_datetime(df["Entry Time"])
  df["Exit Time"] = pd.to_datetime(df["Exit Time"])
  df = df.sort_values("Entry Time").copy()
  # Split at the configured percentage
  cutoff_idx = int(len(df) * req.split_pct / 100)
  cutoff_idx = max(1, min(cutoff_idx, len(df) - 1))
  cutoff_date = df.iloc[cutoff_idx]["Entry Time"]

  is_df = df[df["Entry Time"] <= cutoff_date]
  oos_df = df[df["Entry Time"] > cutoff_date]

  # OOS clipping at created_at boundary
  _oos_clip_details: list[dict] = []
  if p.created_at:
    oos_end_str = p.created_at.strftime("%Y-%m-%d") if hasattr(p.created_at, "strftime") else str(p.created_at)[:10]
    settings = json.loads(p.source_strategies) if p.source_strategies else {}
    tf = settings.get("timeframe", "D")
    # First: hard-remove trades that entered AFTER the OOS end date
    if not oos_df.empty:
      oos_end_ts = pd.Timestamp(oos_end_str)
      oos_df = oos_df[oos_df["Entry Time"].dt.date <= oos_end_ts.date()].copy()
    # Then: handle trades straddling the boundary
    if req.clip_oos and not oos_df.empty:
      oos_df, _oos_clip_details = clip_df_at_boundary(oos_df, oos_end_str, tf, db, return_details=True)
    elif not req.clip_oos and not oos_df.empty:
      oos_end_ts = pd.Timestamp(oos_end_str)
      oos_df = oos_df[oos_df["Exit Time"].dt.date <= oos_end_ts.date()]

  is_m = _quick_metrics(is_df)
  oos_m = _quick_metrics(oos_df)

  # Diagnostic: log when IS metrics are unexpectedly empty
  if not is_m and not is_df.empty:
    logger.warning(
      "Portfolio %d: _quick_metrics returned empty for IS despite %d IS trades",
      portfolio_id, len(is_df),
    )

  # WFE = annualized OOS / annualized IS (time-normalized — kept for backward compat)
  wfe = 0.0
  if not is_df.empty and not oos_df.empty:
    is_span = (is_df["Entry Time"].max() - is_df["Entry Time"].min()).total_seconds()
    oos_span = (oos_df["Entry Time"].max() - oos_df["Entry Time"].min()).total_seconds()
    is_pnl = float(is_df["Net Profit"].sum())
    oos_pnl = float(oos_df["Net Profit"].sum())
    if is_span > 0 and is_pnl != 0 and oos_span > 0:
      wfe = (oos_pnl / oos_span) / (is_pnl / is_span)

  # PF-based WFE
  _is_pf_s = float(is_m.get("profit_factor", 0) or 0)
  _oos_pf_s = float(oos_m.get("profit_factor", 0) or 0)
  wfe_pf = _calc_wfe_pf(_is_pf_s, _oos_pf_s)

  return {
    "is_metrics": is_m,
    "oos_metrics": oos_m,
    "wfe": round(wfe, 4),
    "wfe_pf": round(wfe_pf, 4),
    "cutoff": cutoff_date.isoformat() if pd.notna(cutoff_date) else None,
    "is_trades": len(is_df),
    "oos_trades": len(oos_df),
    "clipped_trades": _oos_clip_details,
  }


class OOSDateRequest(BaseModel):
  cutoff_date: str | None = None  # ISO date string; None = use optimization_end_date
  clip_is: bool = True   # force-close IS trades at IS boundary close price
  clip_oos: bool = True  # force-close OOS trades at OOS end close price (OFF = exclude unfinished)
  oos_end_date: str | None = None  # explicit OOS end date — overrides auto-detected value


@router.post("/{portfolio_id}/oos-date")
def portfolio_oos_date(portfolio_id: int, req: OOSDateRequest, db: Session = Depends(get_db)):
  """Date-based OOS analysis with full KPIs and per-asset WFE.

  Uses optimization_end_date from source_strategies if no cutoff_date
  is explicitly provided.
  """
  p = _get_portfolio(portfolio_id, db)

  # Determine cutoff date (check multiple sources)
  cutoff_str = req.cutoff_date
  if not cutoff_str:
    # 1. Check optimization_end_date on Portfolio model (most reliable)
    if p.optimization_end_date:
      cutoff_str = p.optimization_end_date.isoformat() if hasattr(p.optimization_end_date, "isoformat") else str(p.optimization_end_date)
    else:
      # 2. Fall back to source_strategies settings
      settings = json.loads(p.source_strategies) if p.source_strategies else {}
      cutoff_str = settings.get("date_to") or settings.get("end_date") or settings.get("optimization_end_date")

      # 3. Fall back to source batch configs (WARN: may differ from original boundary)
      if not cutoff_str or str(cutoff_str) in ("None", "all", ""):
        batch_ids = settings.get("batch_ids") or ([settings["batch_id"]] if settings.get("batch_id") else [])
        if batch_ids:
          batches = db.query(BatchBacktestRun).filter(BatchBacktestRun.id.in_(batch_ids)).all()
          for b in batches:
            cfg = json.loads(b.parameters) if b.parameters else {}
            dt = cfg.get("date_to") or cfg.get("optimization_end_date")
            if dt and str(dt) not in ("None", "all", ""):
              cutoff_str = dt
              logger.warning(
                "Portfolio %d: cutoff_date reconstructed from batch %d params (%s) — "
                "may differ from original optimization_end_date. Set optimization_end_date on the portfolio.",
                portfolio_id, b.id, cutoff_str,
              )
              break

      if not cutoff_str or str(cutoff_str) in ("None", "all", ""):
        return {
          "is_metrics": {}, "oos_metrics": {}, "wfe": 0, "cutoff": None,
          "is_trades": 0, "oos_trades": 0, "per_asset": [],
          "is_full_metrics": {}, "oos_full_metrics": {},
          "oos_equity_curve": [], "is_monthly_matrix": {}, "oos_monthly_matrix": {},
          "error": "No OOS cutoff date found. Set optimization_end_date on the portfolio.",
        }

  cutoff = pd.to_datetime(cutoff_str)
  from backend.analysis.boundary_clip import clip_df_at_boundary
  df = _load_portfolio_trades(p, db, clip_is=req.clip_is)
  if df.empty:
    return {
      "is_metrics": {}, "oos_metrics": {}, "wfe": 0,
      "cutoff": cutoff_str, "is_trades": 0, "oos_trades": 0,
      "per_asset": [], "is_full_metrics": {}, "oos_full_metrics": {},
      "oos_equity_curve": [], "is_monthly_matrix": {}, "oos_monthly_matrix": {},
    }

  df["Entry Time"] = pd.to_datetime(df["Entry Time"])
  df["Exit Time"] = pd.to_datetime(df["Exit Time"])
  is_df = df[df["Entry Time"].dt.date <= cutoff.date()].copy()
  oos_df = df[df["Entry Time"].dt.date > cutoff.date()].copy()

  # Cost summary for all trades (unfiltered)
  _cost_stg_oos = json.loads(p.source_strategies) if p.source_strategies else {}
  cost_summary_all = _compute_cost_summary(df, _cost_stg_oos)

  # OOS boundary clipping — uses _resolve_oos_end_date helper (Tier 1.1 L04)
  _oos_clip_details_2: list[dict] = []
  settings_oos = json.loads(p.source_strategies) if p.source_strategies else {}
  oos_end_str = _resolve_oos_end_date(p, req_override=req.oos_end_date)
  oos_end_raw = oos_end_str  # back-compat: downstream code checks oos_end_raw truthy
  if oos_end_str:
    tf = settings_oos.get("timeframe", "D")
    # First: hard-remove trades that entered AFTER the OOS end date
    if not oos_df.empty:
      oos_end_ts = pd.Timestamp(oos_end_str)
      oos_df = oos_df[oos_df["Entry Time"].dt.date <= oos_end_ts.date()].copy()
    # Then: handle trades straddling the boundary
    if req.clip_oos and not oos_df.empty:
      oos_df, _oos_clip_details_2 = clip_df_at_boundary(oos_df, oos_end_str, tf, db, return_details=True)
    elif not req.clip_oos and not oos_df.empty:
      oos_end_ts = pd.Timestamp(oos_end_str)
      oos_df = oos_df[oos_df["Exit Time"].dt.date <= oos_end_ts.date()]

  # Drop rows with missing Exit Time — keeps full_metrics and quick_metrics in sync
  is_df = is_df.dropna(subset=["Exit Time"]) if not is_df.empty else is_df
  oos_df = oos_df.dropna(subset=["Exit Time"]) if not oos_df.empty else oos_df

  # OOS cost summary (after filtering so it reflects the actual OOS window)
  cost_summary_oos = _compute_cost_summary(oos_df, _cost_stg_oos)

  # Quick metrics
  is_m = _quick_metrics(is_df)
  oos_m = _quick_metrics(oos_df)

  # Diagnostic: log when IS metrics are unexpectedly empty
  if not is_m and not is_df.empty:
    logger.warning(
      "Portfolio %d (full-oos): _quick_metrics returned empty for IS despite %d IS trades",
      portfolio_id, len(is_df),
    )

  # Full metrics (with leverage/context from batch)
  ctx = _get_portfolio_batch_context(p, db)
  is_full = _safe_calculate_full_metrics(is_df, **ctx)
  oos_full = _safe_calculate_full_metrics(oos_df, **ctx)

  # Inject stability R² from quick metrics into full metrics (not computed by calculate_full_metrics)
  if oos_m.get("stability") is not None and oos_full:
    oos_full["Stability (R²)"] = oos_m["stability"]
  if is_m.get("stability") is not None and is_full:
    is_full["Stability (R²)"] = is_m["stability"]

  # Portfolio-wide IS/OOS time spans (used for both aggregate and per-asset WFE)
  ptf_is_span = (is_df["Entry Time"].max() - is_df["Entry Time"].min()).total_seconds() if not is_df.empty else 0
  ptf_oos_span = (oos_df["Entry Time"].max() - oos_df["Entry Time"].min()).total_seconds() if not oos_df.empty else 0

  # Aggregate WFE (time-normalized — kept for backward compat)
  wfe = 0.0
  if not is_df.empty and not oos_df.empty:
    is_pnl = float(is_df["Net Profit"].sum())
    oos_pnl = float(oos_df["Net Profit"].sum())
    if ptf_is_span > 0 and is_pnl != 0 and ptf_oos_span > 0:
      wfe = (oos_pnl / ptf_oos_span) / (is_pnl / ptf_is_span)

  # Build asset → strategy/direction lookup from portfolio assets
  # Strategy name = actual name from Strategy table (via batch.strategy_id)
  asset_strategy_map: dict[str, str] = {}
  asset_direction_map: dict[str, str] = {}
  batch_strat_prefix: dict[int, str] = {}
  _ss = json.loads(p.source_strategies) if p.source_strategies else {}
  batch_ids_for_lookup = _ss.get("batch_ids") or ([_ss["batch_id"]] if _ss.get("batch_id") else [])
  if batch_ids_for_lookup:
    _batches = db.query(BatchBacktestRun).filter(
        BatchBacktestRun.id.in_(batch_ids_for_lookup)
    ).all()
    for _b in _batches:
      # Strategy name: from Strategy table (actual strategy, not batch name)
      _strat = db.query(Strategy).filter(Strategy.id == _b.strategy_id).first()
      batch_strat_prefix[_b.id] = _strat.name if _strat else "Unknown"
  for a in p.assets:
    asset_key = f"{a.symbol}_{a.trade_side}" if a.trade_side in ("Long", "Short") else a.symbol
    strat_name = batch_strat_prefix.get(a.batch_id, "") if a.batch_id else ""
    if not strat_name:
      raw = a.strategy_name or "Unknown"
      strat_name = raw.split(" — ", 1)[0] if " — " in raw else raw
    asset_strategy_map[asset_key] = strat_name
    asset_direction_map[asset_key] = a.trade_side or "Both"

  # Per-asset WFE
  per_asset = []
  if "Asset" in df.columns:
    for asset in sorted(df["Asset"].unique()):
      a_df = df[df["Asset"] == asset]
      a_is = a_df[a_df["Entry Time"].dt.date <= cutoff.date()]
      a_oos = a_df[a_df["Entry Time"].dt.date > cutoff.date()]

      is_pnl_a = float(a_is["Net Profit"].sum()) if not a_is.empty else 0
      oos_pnl_a = float(a_oos["Net Profit"].sum()) if not a_oos.empty else 0
      is_wins = a_is[a_is["Net Profit"] > 0] if not a_is.empty else pd.DataFrame()
      is_losses = a_is[a_is["Net Profit"] <= 0] if not a_is.empty else pd.DataFrame()
      oos_wins = a_oos[a_oos["Net Profit"] > 0] if not a_oos.empty else pd.DataFrame()
      oos_losses = a_oos[a_oos["Net Profit"] <= 0] if not a_oos.empty else pd.DataFrame()
      is_gp = float(is_wins["Net Profit"].sum()) if not is_wins.empty else 0
      is_gl = float(abs(is_losses["Net Profit"].sum())) if not is_losses.empty else 0
      oos_gp = float(oos_wins["Net Profit"].sum()) if not oos_wins.empty else 0
      oos_gl = float(abs(oos_losses["Net Profit"].sum())) if not oos_losses.empty else 0
      is_pf = round(min(is_gp / is_gl, 10.0), 2) if is_gl > 0 else (10.0 if is_gp > 0 else 0)
      oos_pf = round(min(oos_gp / oos_gl, 10.0), 2) if oos_gl > 0 else (10.0 if oos_gp > 0 else 0)
      oos_wr = round(len(oos_wins) / len(a_oos) * 100, 1) if len(a_oos) > 0 else 0

      # Per-asset WFE (time-normalized — kept for backward compat)
      a_wfe = 0.0
      if not a_is.empty and not a_oos.empty:
        if ptf_is_span > 0 and is_pnl_a != 0 and ptf_oos_span > 0:
          a_wfe = (oos_pnl_a / ptf_oos_span) / (is_pnl_a / ptf_is_span)
          a_wfe = max(-10.0, min(10.0, a_wfe))  # clamp to [-10, 10]

      # Per-asset PF-based WFE
      a_wfe_pf = _calc_wfe_pf(is_pf, oos_pf)

      per_asset.append({
        "asset": asset,
        "strategy_name": asset_strategy_map.get(asset, "Unknown"),
        "direction": asset_direction_map.get(asset, "Both"),
        "WFE": round(a_wfe, 4),
        "WFE_PF": round(a_wfe_pf, 4),
        "IS_PF": is_pf,
        "OOS_PF": oos_pf,
        "OOS_WR": oos_wr,
        "IS_PnL": round(is_pnl_a, 2),
        "OOS_PnL": round(oos_pnl_a, 2),
        "OOS_GP": round(oos_gp, 2),
        "OOS_GL": round(oos_gl, 2),
        "IS_Trades": len(a_is),
        "OOS_Trades": len(a_oos),
      })

  # Strategy + direction groups — aggregate OOS metrics by strategy and direction
  strategy_groups = []
  if per_asset:
    from collections import defaultdict
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for pa in per_asset:
      key = (pa["strategy_name"], pa["direction"])
      groups[key].append(pa)

    for (strat, direction), assets_in_group in sorted(groups.items()):
      g_oos_pnl = sum(a["OOS_PnL"] for a in assets_in_group)
      g_oos_trades = sum(a["OOS_Trades"] for a in assets_in_group)
      # Aggregate trade-level gross profit/loss across all assets in group
      g_oos_gp = sum(a["OOS_GP"] for a in assets_in_group)
      g_oos_gl = sum(a["OOS_GL"] for a in assets_in_group)
      g_oos_pf = round(min(g_oos_gp / g_oos_gl, 10.0), 2) if g_oos_gl > 0 else (10.0 if g_oos_gp > 0 else None)
      # Group WR = weighted average by trade count
      g_oos_wr_sum = sum(a["OOS_WR"] * a["OOS_Trades"] for a in assets_in_group)
      g_oos_wr = round(g_oos_wr_sum / g_oos_trades, 1) if g_oos_trades > 0 else 0

      strategy_groups.append({
        "strategy_name": strat,
        "direction": direction,
        "assets": [a["asset"] for a in assets_in_group],
        "group_metrics": {
          "net_profit": round(g_oos_pnl, 2),
          "gross_profit": round(g_oos_gp, 2),
          "gross_loss": round(g_oos_gl, 2),
          "profit_factor": g_oos_pf,
          "win_rate": g_oos_wr,
          "trades": g_oos_trades,
        },
      })

  # OOS equity curve
  oos_eq: list[dict] = []
  if not oos_df.empty:
    sorted_oos = oos_df.sort_values("Exit Time")
    cum = sorted_oos["Net Profit"].cumsum()
    step = max(1, len(cum) // 500)
    for i in range(0, len(cum), step):
      oos_eq.append({
        "date": str(sorted_oos.iloc[i]["Exit Time"].date()),
        "equity": round(float(cum.iloc[i]), 2),
      })
    if len(cum) > 0 and (len(oos_eq) == 0 or oos_eq[-1]["equity"] != round(float(cum.iloc[-1]), 2)):
      oos_eq.append({
        "date": str(sorted_oos.iloc[-1]["Exit Time"].date()),
        "equity": round(float(cum.iloc[-1]), 2),
      })

  # Build enriched monthly matrix (trade count + win rate) for both IS and OOS
  #
  # Bug fix: bucket WFAS replay portfolios by Entry Time so the matrix matches
  # the IS/OOS split (which is also Entry Time, lines 1762-1763) and the modal
  # drilldowns in `portfolio_month_trades` + `portfolio_monthly_filtered`
  # (which both bucket by Entry Time for WFAS replay). Without this, a trade
  # that entered in March and exited in April was placed in March by the IS/OOS
  # split but in April by the monthly matrix — same row in the table showed
  # different values than the drilldown popup. Legacy non-WFAS portfolios stay
  # on Exit Time bucketing for back-compat.
  from backend.analysis.portfolio_wfo_replay import has_wfo_replay_metadata
  _settings_for_bucket = json.loads(p.source_strategies) if p.source_strategies else {}
  _bucket_col = "Entry Time" if has_wfo_replay_metadata(_settings_for_bucket) else "Exit Time"

  def _build_monthly_matrix(src_df: pd.DataFrame) -> dict[str, dict[str, dict]]:
    result: dict[str, dict[str, dict]] = {}
    if src_df.empty:
      return result
    tmp = src_df.copy()
    tmp["Year"] = tmp[_bucket_col].dt.year
    tmp["Month"] = tmp[_bucket_col].dt.month
    for (yr, mo), grp in tmp.groupby(["Year", "Month"]):
      yr_str = str(int(yr))
      if yr_str not in result:
        result[yr_str] = {}
      total = len(grp)
      wins = len(grp[grp["Net Profit"] > 0])
      result[yr_str][str(int(mo))] = {
        "pnl": round(float(grp["Net Profit"].sum()), 2),
        "trades": total,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
      }
    return result

  is_monthly = _build_monthly_matrix(is_df)
  oos_monthly = _build_monthly_matrix(oos_df)

  # Robustness metrics for headline display
  oos_net = float(oos_m.get("net_profit", 0) or 0)
  oos_dd = float(oos_m.get("max_drawdown", 0) or 0)
  oos_stability = float(oos_m.get("stability", 0) or 0)
  oos_sortino = float(oos_m.get("sortino", 0) or 0)
  oos_pf_val = float(oos_m.get("profit_factor", 0) or 0)
  is_pf_val = float(is_m.get("profit_factor", 0) or 0)
  oos_wr = float(oos_m.get("win_rate", 0) or 0)
  return_to_dd = compute_return_to_dd_ratio(oos_net, oos_dd)

  # PF-based WFE: (OOS_PF - 1) / (IS_PF - 1) — measures edge retention
  wfe_pf = _calc_wfe_pf(is_pf_val, oos_pf_val)

  # Auto-detect strategy type from OOS trade profile
  oos_avg_win = float(oos_full.get("Avg Win", 0) or 0)
  oos_avg_loss = float(oos_full.get("Avg Loss", 0) or 0)
  strat_type = detect_strategy_type(
    win_rate=oos_wr,
    profit_factor=oos_pf_val,
    avg_win=oos_avg_win,
    avg_loss=oos_avg_loss,
  )

  grade, grade_score = compute_portfolio_grade(
    stability_r2=oos_stability,
    wfe=wfe_pf,
    return_to_dd=return_to_dd,
    sortino=oos_sortino,
    profit_factor=oos_pf_val,
    strategy_type=strat_type,
  )

  return {
    "is_metrics": is_m,
    "oos_metrics": oos_m,
    "wfe": round(wfe, 4),
    "wfe_pf": round(wfe_pf, 4),
    "cutoff": cutoff_str,
    "is_trades": len(is_df),
    "oos_trades": len(oos_df),
    "per_asset": per_asset,
    "strategy_groups": strategy_groups,
    "is_full_metrics": is_full,
    "oos_full_metrics": oos_full,
    "oos_equity_curve": oos_eq,
    "is_monthly_matrix": is_monthly,
    "oos_monthly_matrix": oos_monthly,
    "return_to_dd_ratio": return_to_dd,
    "portfolio_grade": grade,
    "portfolio_grade_score": round(grade_score, 4),
    "strategy_type": strat_type,
    "oos_end_date": oos_end_str if oos_end_raw else None,
    "clipped_trades": _oos_clip_details_2,
    "cost_summary": cost_summary_all,
    "oos_cost_summary": cost_summary_oos,
  }


@router.get("/{portfolio_id}/month-trades")
def portfolio_month_trades(
  portfolio_id: int,
  year: int,
  month: int,
  cutoff_date: str | None = None,
  period: str = "oos",
  db: Session = Depends(get_db),
):
  """Return individual trades for a specific month within a portfolio.

  Query params:
    year, month — the calendar month to filter by (exit_time based)
    cutoff_date — IS/OOS boundary date (YYYY-MM-DD)
    period — "oos" (default) or "is" to filter which side of the cutoff
  """
  p = _get_portfolio(portfolio_id, db)
  df = _load_portfolio_trades(p, db, clip_is=False)
  if df.empty:
    return {"trades": [], "year": year, "month": month, "period": period}

  # Phase 2 replay trades are entry-bucketed (in-basket-at-entry); filter
  # the modal by Entry Time so it agrees with the Monthly PnL table.
  # Legacy non-WFAS portfolios stay on Exit Time bucketing.
  from backend.analysis.portfolio_wfo_replay import has_wfo_replay_metadata
  _settings = json.loads(p.source_strategies) if p.source_strategies else {}
  bucket_col = "Entry Time" if has_wfo_replay_metadata(_settings) else "Exit Time"

  df[bucket_col] = pd.to_datetime(df[bucket_col])

  # Filter by calendar month (of the bucketing column)
  mask = (df[bucket_col].dt.year == year) & (df[bucket_col].dt.month == month)

  # Apply IS/OOS cutoff filter (against the same bucketing column)
  if cutoff_date:
    cutoff_ts = pd.Timestamp(cutoff_date)
    if period == "oos":
      mask = mask & (df[bucket_col] > cutoff_ts)
    elif period == "is":
      mask = mask & (df[bucket_col] <= cutoff_ts)

  filtered = df[mask].sort_values(bucket_col)

  # Build asset -> strategy_id / batch_id lookup from portfolio assets
  # so the chart popup can load the correct indicators for each trade.
  asset_strategy_map: dict[str, int] = {}
  asset_batch_map: dict[str, int] = {}
  batch_strategy_cache: dict[int, int] = {}
  for a in p.assets:
    if not a.is_active or not a.batch_id:
      continue
    if a.batch_id not in batch_strategy_cache:
      batch_row = db.query(BatchBacktestRun).filter(
        BatchBacktestRun.id == a.batch_id
      ).first()
      if batch_row and batch_row.strategy_id:
        batch_strategy_cache[a.batch_id] = int(batch_row.strategy_id)
    strat_id = batch_strategy_cache.get(a.batch_id)
    sides = (
      [a.trade_side] if a.trade_side in ("Long", "Short") else ["Long", "Short"]
    )
    for s in sides:
      key = f"{a.symbol}_{s}"
      if strat_id is not None:
        asset_strategy_map[key] = strat_id
      asset_batch_map[key] = a.batch_id

  trades_out = []
  for i, (_, row) in enumerate(filtered.iterrows(), 1):
    asset = str(row.get("Asset", ""))
    # Clean display name
    display = asset.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")
    side_raw = asset.rsplit("_", 1)[-1] if "_" in asset else ""
    side = side_raw if side_raw in ("Long", "Short") else ""

    trades_out.append({
      "num": i,
      "asset": asset,
      "asset_display": display,
      "side": side,
      "entry_time": str(row["Entry Time"]) if pd.notna(row.get("Entry Time")) else None,
      "exit_time": str(row["Exit Time"]) if pd.notna(row.get("Exit Time")) else None,
      "entry_price": round(float(row["Entry Price"]), 6) if pd.notna(row.get("Entry Price")) else None,
      "exit_price": round(float(row["Exit Price"]), 6) if pd.notna(row.get("Exit Price")) else None,
      "net_profit": round(float(row["Net Profit"]), 2) if pd.notna(row.get("Net Profit")) else 0,
      "commission": round(float(row["Commission"]), 4) if pd.notna(row.get("Commission")) else None,
      "strategy_id": asset_strategy_map.get(asset),
      "batch_id": (
        int(row["_batch_id"])
        if "_batch_id" in row and pd.notna(row.get("_batch_id"))
        else asset_batch_map.get(asset)
      ),
    })

  return {"trades": trades_out, "year": year, "month": month, "period": period}


class MonthlyFilteredRequest(BaseModel):
  cutoff_date: str | None = None
  batch_ids: list[int] | None = None  # filter to specific batches (None = all)
  direction: str = "all"  # "all", "long", "short"


@router.post("/{portfolio_id}/monthly-filtered")
def portfolio_monthly_filtered(
  portfolio_id: int, req: MonthlyFilteredRequest, db: Session = Depends(get_db),
):
  """Lightweight endpoint returning monthly PnL matrix filtered by batch and direction.

  Returns IS + OOS matrices plus available batch options for the selector.
  """
  p = _get_portfolio(portfolio_id, db)

  # Resolve cutoff date
  cutoff_str = req.cutoff_date
  if not cutoff_str and p.optimization_end_date:
    cutoff_str = (
      p.optimization_end_date.isoformat()
      if hasattr(p.optimization_end_date, "isoformat")
      else str(p.optimization_end_date)
    )
  if not cutoff_str:
    settings = json.loads(p.source_strategies) if p.source_strategies else {}
    cutoff_str = settings.get("date_to") or settings.get("optimization_end_date")

  # Load trades
  df = _load_portfolio_trades(p, db, clip_is=True)

  # Gather batch options (unique batch_ids from active assets)
  batch_ids_in_portfolio: set[int] = set()
  for a in p.assets:
    if a.is_active and a.batch_id:
      batch_ids_in_portfolio.add(a.batch_id)

  batch_options = []
  if batch_ids_in_portfolio:
    batches = db.query(BatchBacktestRun).filter(
      BatchBacktestRun.id.in_(list(batch_ids_in_portfolio))
    ).all()
    for b in batches:
      strat = db.query(Strategy).filter(Strategy.id == b.strategy_id).first()
      strat_name = strat.name if strat else "Unknown"
      label = b.name or f"{strat_name} #{b.id}"
      batch_options.append({"id": b.id, "name": label, "strategy": strat_name})
    batch_options.sort(key=lambda x: x["id"])

  if df.empty or not cutoff_str:
    return {
      "is_matrix": {}, "oos_matrix": {},
      "batches": batch_options,
    }

  df["Entry Time"] = pd.to_datetime(df["Entry Time"])
  df["Exit Time"] = pd.to_datetime(df["Exit Time"])

  # Filter by direction
  if req.direction in ("long", "short") and "Asset" in df.columns:
    suffix = "_Long" if req.direction == "long" else "_Short"
    df = df[df["Asset"].str.endswith(suffix)]

  # Filter by batch_ids — match via Asset name to PortfolioAsset mapping
  if req.batch_ids and len(req.batch_ids) > 0:
    # Build set of asset names belonging to selected batches
    allowed_assets: set[str] = set()
    for a in p.assets:
      if not a.is_active:
        continue
      if a.batch_id not in req.batch_ids:
        continue
      if a.trade_side in ("Long", "Short"):
        allowed_assets.add(f"{a.symbol}_{a.trade_side}")
      else:
        allowed_assets.add(f"{a.symbol}_Long")
        allowed_assets.add(f"{a.symbol}_Short")
    if allowed_assets:
      df = df[df["Asset"].isin(allowed_assets)]
    else:
      df = df.iloc[0:0]  # empty

  if df.empty:
    return {"is_matrix": {}, "oos_matrix": {}, "batches": batch_options}

  # Split IS / OOS
  cutoff = pd.to_datetime(cutoff_str)
  is_df = df[df["Entry Time"].dt.date <= cutoff.date()].copy()
  oos_df = df[df["Entry Time"].dt.date > cutoff.date()].copy()

  # Phase 2 replay trades are entry-bucketed by Phase 2's aggregate_monthly_matrix;
  # legacy portfolios bucket by Exit Time. Pick the bucket column once per request.
  from backend.analysis.portfolio_wfo_replay import has_wfo_replay_metadata
  _settings = json.loads(p.source_strategies) if p.source_strategies else {}
  bucket_col = "Entry Time" if has_wfo_replay_metadata(_settings) else "Exit Time"

  def _build_matrix(src: pd.DataFrame) -> dict:
    result: dict[str, dict[str, dict]] = {}
    if src.empty:
      return result
    tmp = src.copy()
    tmp["Year"] = tmp[bucket_col].dt.year
    tmp["Month"] = tmp[bucket_col].dt.month
    for (yr, mo), grp in tmp.groupby(["Year", "Month"]):
      yr_str = str(int(yr))
      if yr_str not in result:
        result[yr_str] = {}
      total = len(grp)
      wins = len(grp[grp["Net Profit"] > 0])
      result[yr_str][str(int(mo))] = {
        "pnl": round(float(grp["Net Profit"].sum()), 2),
        "trades": total,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
      }
    return result

  return {
    "is_matrix": _build_matrix(is_df),
    "oos_matrix": _build_matrix(oos_df),
    "batches": batch_options,
  }


class CompareRequest(BaseModel):
  portfolio_ids: list[int]


@router.post("/compare")
def portfolio_compare(req: CompareRequest, db: Session = Depends(get_db)):
  """Compare metrics across multiple saved portfolios, including OOS analysis."""
  if len(req.portfolio_ids) < 2:
    raise HTTPException(400, "Need at least 2 portfolios")

  results = []
  for pid in req.portfolio_ids:
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not p:
      continue
    df = _load_portfolio_trades(p, db)
    metrics = _quick_metrics(df) if not df.empty else {}
    ctx = _get_portfolio_batch_context(p, db)
    full = _safe_calculate_full_metrics(df, **ctx)

    entry: dict = {
      "id": p.id,
      "name": p.name,
      "asset_count": len(p.assets),
      "metrics": metrics,
      "full_metrics": full,
      "created_at": p.created_at.isoformat() if p.created_at else None,
      # OOS fields — filled below if cutoff found
      "oos_metrics": {},
      "oos_full_metrics": {},
      "wfe_pf": 0.0,
      "cutoff": None,
      "is_trades": 0,
      "oos_trades": 0,
      "oos_equity_curve": [],
      "portfolio_grade": None,
      "portfolio_grade_score": None,
      "return_to_dd_ratio": None,
    }

    # --- OOS analysis (reuses portfolio_oos_date logic) ---
    cutoff_str = None
    if p.optimization_end_date:
      cutoff_str = p.optimization_end_date.isoformat() if hasattr(p.optimization_end_date, "isoformat") else str(p.optimization_end_date)
    else:
      settings = json.loads(p.source_strategies) if p.source_strategies else {}
      cutoff_str = settings.get("date_to") or settings.get("end_date") or settings.get("optimization_end_date")
      if not cutoff_str or str(cutoff_str) in ("None", "all", ""):
        cutoff_str = None

    if cutoff_str and not df.empty:
      try:
        cutoff = pd.to_datetime(cutoff_str)
        df["Entry Time"] = pd.to_datetime(df["Entry Time"])
        df["Exit Time"] = pd.to_datetime(df["Exit Time"])
        is_df = df[df["Entry Time"].dt.date <= cutoff.date()].copy()
        oos_df = df[df["Entry Time"].dt.date > cutoff.date()].copy()
        is_df = is_df.dropna(subset=["Exit Time"]) if not is_df.empty else is_df
        oos_df = oos_df.dropna(subset=["Exit Time"]) if not oos_df.empty else oos_df

        is_m = _quick_metrics(is_df)
        oos_m = _quick_metrics(oos_df)
        oos_full_m = _safe_calculate_full_metrics(oos_df, **ctx)

        # Inject stability into full metrics
        if oos_m.get("stability") is not None and oos_full_m:
          oos_full_m["Stability (R²)"] = oos_m["stability"]

        # WFE (PF-based)
        is_pf_val = float(is_m.get("profit_factor", 0) or 0)
        oos_pf_val = float(oos_m.get("profit_factor", 0) or 0)
        wfe_pf = _calc_wfe_pf(is_pf_val, oos_pf_val)

        # Return-to-DD ratio
        oos_net = float(oos_m.get("net_profit", 0) or 0)
        oos_dd = float(oos_m.get("max_drawdown", 0) or 0)
        ret_dd = compute_return_to_dd_ratio(oos_net, oos_dd)

        # Strategy type + grade
        oos_wr = float(oos_m.get("win_rate", 0) or 0)
        oos_stability = float(oos_m.get("stability", 0) or 0)
        oos_sortino = float(oos_m.get("sortino", 0) or 0)
        oos_avg_win = float(oos_full_m.get("Avg Win", 0) or 0)
        oos_avg_loss = float(oos_full_m.get("Avg Loss", 0) or 0)
        strat_type = detect_strategy_type(
          win_rate=oos_wr, profit_factor=oos_pf_val,
          avg_win=oos_avg_win, avg_loss=oos_avg_loss,
        )
        grade, grade_score = compute_portfolio_grade(
          stability_r2=oos_stability, wfe=wfe_pf,
          return_to_dd=ret_dd, sortino=oos_sortino,
          profit_factor=oos_pf_val, strategy_type=strat_type,
        )

        # OOS equity curve (trade-based cumulative PnL)
        oos_eq: list[dict] = []
        if not oos_df.empty:
          sorted_oos = oos_df.sort_values("Exit Time")
          cum = sorted_oos["Net Profit"].cumsum()
          step = max(1, len(cum) // 500)
          for i in range(0, len(cum), step):
            oos_eq.append({
              "date": str(sorted_oos.iloc[i]["Exit Time"].date()),
              "equity": round(float(cum.iloc[i]), 2),
            })
          if len(cum) > 0 and (len(oos_eq) == 0 or oos_eq[-1]["equity"] != round(float(cum.iloc[-1]), 2)):
            oos_eq.append({
              "date": str(sorted_oos.iloc[-1]["Exit Time"].date()),
              "equity": round(float(cum.iloc[-1]), 2),
            })

        entry.update({
          "oos_metrics": oos_m,
          "oos_full_metrics": oos_full_m,
          "wfe_pf": round(wfe_pf, 4),
          "cutoff": cutoff_str,
          "is_trades": len(is_df),
          "oos_trades": len(oos_df),
          "oos_equity_curve": oos_eq,
          "portfolio_grade": grade,
          "portfolio_grade_score": round(grade_score, 4) if grade_score else None,
          "return_to_dd_ratio": round(ret_dd, 4) if ret_dd else None,
        })
      except Exception as e:
        logger.warning("Portfolio %d OOS compare failed: %s", pid, e)

    results.append(entry)
  return {"portfolios": results}


class SwapRequest(BaseModel):
  remove_asset: str  # e.g. "BTCUSDT_Long"
  min_trades: int = 0
  min_wr: float = 0.0
  min_pf: float = 0.0
  corr_method: str = "anti"  # "anti" | "smart" | "hybrid"
  is_only: bool = True  # Filter to IS period only for candidate scoring
  use_seed_ranking: bool = True  # Rank by portfolio's seed metrics first
  max_pool_size: int = 20  # Max candidates to keep after seed ranking


@router.post("/{portfolio_id}/swap-candidates")
def swap_candidates(
  portfolio_id: int, req: SwapRequest, db: Session = Depends(get_db),
):
  """Find top replacement candidates for an asset in a portfolio.

  Supports IS-only filtering, min trade/WR/PF thresholds, and
  multiple correlation scoring methods (anti, smart, hybrid).
  """
  p = _get_portfolio(portfolio_id, db)
  settings = json.loads(p.source_strategies) if p.source_strategies else {}

  # Support both single batch_id and multi batch_ids
  batch_ids: list[int] = []
  if settings.get("batch_ids"):
    batch_ids = settings["batch_ids"]
  elif settings.get("batch_id"):
    batch_ids = [settings["batch_id"]]
  if not batch_ids:
    raise HTTPException(400, "Portfolio has no batch reference — cannot find candidates")

  asset_names = _portfolio_asset_names(p)
  if req.remove_asset not in asset_names:
    raise HTTPException(400, f"{req.remove_asset} not in portfolio")

  # Load all trades from all batches — intentionally unscoped so we can
  # discover swap candidates from ANY batch asset.  This means existing
  # portfolio assets' PnL may include cross-batch contamination, but swap
  # scoring is approximate by design (candidates are ranked relatively).
  all_runs = []
  for bid in batch_ids:
    runs = (
      db.query(BacktestRun)
      .filter(BacktestRun.batch_id == bid, BacktestRun.status == "completed")
      .all()
    )
    all_runs.extend(runs)
  run_ids = [r.id for r in all_runs]
  trades = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).all()

  # Build asset → batch_name mapping for candidate labeling.
  # Uses each batch's direction_filter to map only the correct suffix
  # (e.g., a "short" batch only maps _Short entries, not _Long).
  # Direction-specific batches get priority over "both" batches.
  batch_name_map: dict[int, str] = {}
  batch_dir_filter: dict[int, str] = {}
  for bid in batch_ids:
    batch_obj = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
    batch_name_map[bid] = batch_obj.name if batch_obj and batch_obj.name else f"Batch #{bid}"
    batch_dir_filter[bid] = (batch_obj.direction_filter or "both") if batch_obj else "both"

  asset_batch_names: dict[str, str] = {}  # "BTCUSDT_Long" → "RSI Breakout — Conservative"
  asset_batch_ids: dict[str, int] = {}    # "BTCUSDT_Long" → 42
  # Process direction-specific batches first so they get priority
  dir_priority = {"long": 0, "short": 0, "both": 1, "long_plus_short": 1}
  for r in sorted(all_runs, key=lambda x: dir_priority.get(batch_dir_filter.get(x.batch_id, "both"), 1)):
    dir_f = batch_dir_filter.get(r.batch_id, "both")
    if dir_f == "long":
      suffixes = ("_Long",)
    elif dir_f == "short":
      suffixes = ("_Short",)
    else:
      suffixes = ("_Long", "_Short")
    for suffix in suffixes:
      key = f"{r.symbol}{suffix}"
      if key not in asset_batch_names and r.batch_id in batch_name_map:
        asset_batch_names[key] = batch_name_map[r.batch_id]
        asset_batch_ids[key] = r.batch_id

  # ALWAYS filter to IS period when portfolio has an OOS cutoff date
  # (prevents OOS data from influencing swap candidate scoring)
  opt_end_ts = None
  if p.optimization_end_date:
    opt_end_ts = pd.Timestamp(p.optimization_end_date)
    trades = [t for t in trades if t.entry_time and pd.Timestamp(t.entry_time).date() <= opt_end_ts.date()]

  daily_pnl = build_daily_pnl_matrix(trades, include_side=True)
  # No daily_pnl truncation — trades filtered by entry_time may exit after cutoff
  df = trades_from_db(trades)

  remaining = [a for a in asset_names if a != req.remove_asset]

  # Determine direction constraint from the portfolio's optimizer settings.
  # split_mode=True (predetermined N Long + M Short) → keep same direction.
  # split_mode=False or absent (combined pool) → any direction is fine.
  side_suffix = ""
  opt = settings.get("optimizer_settings") or settings.get("builder_settings") or settings
  is_split = opt.get("split_mode", False)
  if is_split:
    if req.remove_asset.endswith("_Long"):
      side_suffix = "_Long"
    elif req.remove_asset.endswith("_Short"):
      side_suffix = "_Short"

  # Find candidates: not already in portfolio, respecting direction constraint
  all_assets = daily_pnl.columns.tolist()
  pool = [
    a for a in all_assets
    if a not in asset_names and (not side_suffix or a.endswith(side_suffix))
  ]

  if not pool:
    return {"candidates": [], "message": "No replacement candidates available"}

  # Step 1: Quality filter — compute per-candidate metrics, apply min thresholds
  quality_pool: list[dict] = []
  for candidate in pool:
    cdf = df[df["Asset"] == candidate] if "Asset" in df.columns else pd.DataFrame()
    n_trades = len(cdf)
    if n_trades == 0:
      continue
    wins = cdf[cdf["Net Profit"] > 0]
    losses = cdf[cdf["Net Profit"] <= 0]
    gp = float(wins["Net Profit"].sum()) if not wins.empty else 0
    gl = float(abs(losses["Net Profit"].sum())) if not losses.empty else 0
    net = float(cdf["Net Profit"].sum())
    wr = round(len(wins) / n_trades * 100, 1)
    pf = round(min(gp / gl, 10.0), 2) if gl > 0 else 0.0
    if n_trades < req.min_trades or wr < req.min_wr or pf < req.min_pf:
      continue
    quality_pool.append({
      "asset": candidate, "net_profit": round(net, 2),
      "profit_factor": pf, "win_rate": wr, "trades": n_trades,
      "avg_trade": round(float(cdf["Net Profit"].mean()), 2),
    })

  if not quality_pool:
    return {"candidates": [], "message": "No candidates pass quality filters"}

  # Step 2: Seed ranking — narrow to top N using the portfolio's own seed metrics
  seed_ranked_pool = quality_pool
  seed_metrics_used: list[str] = []
  if req.use_seed_ranking and len(quality_pool) > req.max_pool_size:
    from backend.analysis.optimizer_seeds import compute_seed_scores
    # Extract seed_metrics from portfolio's saved optimizer settings
    src = settings.get("optimizer_settings") or settings.get("builder_settings") or settings
    seed_metrics_used = src.get("seed_metrics", ["sortino"])
    pool_names = [c["asset"] for c in quality_pool]
    seed_scores = compute_seed_scores(daily_pnl, pool_names, seed_metrics_used)
    # Sort by seed score (lower = better in compute_seed_scores)
    for c in quality_pool:
      c["_seed"] = seed_scores.get(c["asset"], float("inf"))
    quality_pool.sort(key=lambda x: x["_seed"])
    seed_ranked_pool = quality_pool[:req.max_pool_size]

  # Step 3: Correlation scoring — rank the seed pool by fit with remaining portfolio
  corr = get_correlation_matrix(daily_pnl, "standard")
  remaining_in_corr = [a for a in remaining if a in corr.columns]

  scored = []
  for i, candidate in enumerate(seed_ranked_pool):
    asset = candidate["asset"]
    if asset not in corr.columns:
      continue
    corrs = corr.loc[asset, remaining_in_corr] if remaining_in_corr else pd.Series(dtype=float)
    mean_corr = float(corrs.mean()) if len(corrs) > 0 else 0

    pf = candidate["profit_factor"]
    wr = candidate["win_rate"]
    net = candidate["net_profit"]
    if req.corr_method == "smart":
      norm_pf = min(pf / 3.0, 1.0)
      norm_wr = min(wr / 100.0, 1.0)
      score = mean_corr - 0.3 * norm_pf - 0.2 * norm_wr
    elif req.corr_method == "hybrid":
      profitability = 1.0 if net > 0 and pf >= 1.0 else 0.0
      score = mean_corr - 0.5 * profitability
    else:
      score = mean_corr

    scored.append({
      "asset": asset,
      "corr_score": round(mean_corr, 4),
      "score": round(score, 4),
      "seed_rank": i + 1,
      "net_profit": candidate["net_profit"],
      "profit_factor": pf,
      "win_rate": wr,
      "trades": candidate["trades"],
      "avg_trade": candidate["avg_trade"],
      "batch_name": asset_batch_names.get(asset, ""),
      "batch_id": asset_batch_ids.get(asset),
    })

  scored.sort(key=lambda x: x["score"])
  return {
    "candidates": scored[:10],
    "is_only": bool(p.optimization_end_date),
    "method": req.corr_method,
    "seed_metrics": seed_metrics_used,
    "pool_size": len(seed_ranked_pool),
  }


class SwapExecuteRequest(BaseModel):
  remove_asset: str  # e.g. "BTCUSDT_Long"
  add_asset: str  # e.g. "SOLUSDT_Long"
  reason: str = ""
  batch_id: int | None = None  # Source batch for the new asset (from candidate search)
  remove_batch_id: int | None = None  # Source batch of the asset being removed (disambiguates same-symbol duplicates)


@router.post("/{portfolio_id}/swap-execute")
def swap_execute(
  portfolio_id: int, req: SwapExecuteRequest, db: Session = Depends(get_db),
):
  """Execute an asset swap in-place on the portfolio.

  Deactivates the removed asset and adds the new one. Logs the swap
  in the portfolio's swap_history JSON array.
  """
  from datetime import datetime

  p = _get_portfolio(portfolio_id, db)
  asset_names = _portfolio_asset_names(p)
  if req.remove_asset not in asset_names:
    raise HTTPException(400, f"{req.remove_asset} not in portfolio")

  # Parse remove_asset into symbol + side
  if req.remove_asset.endswith("_Long"):
    old_symbol = req.remove_asset[:-5]
    old_side = "Long"
  elif req.remove_asset.endswith("_Short"):
    old_symbol = req.remove_asset[:-6]
    old_side = "Short"
  else:
    old_symbol = req.remove_asset
    old_side = "both"

  # Find and deactivate the old asset
  # When remove_batch_id is provided, match on batch_id too to disambiguate
  # same-symbol assets from different batches (e.g. two ACEUSDT_Short).
  old_asset = None
  for a in p.assets:
    sym_match = a.symbol == old_symbol or a.symbol == req.remove_asset
    side_match = a.trade_side == old_side
    if sym_match and side_match and a.is_active:
      if req.remove_batch_id is not None and a.batch_id != req.remove_batch_id:
        continue
      old_asset = a
      break
  if not old_asset:
    raise HTTPException(400, f"Active asset {req.remove_asset} not found in portfolio")

  old_asset.is_active = False

  # Parse add_asset into symbol + side
  if req.add_asset.endswith("_Long"):
    new_symbol = req.add_asset[:-5]
    new_side = "Long"
  elif req.add_asset.endswith("_Short"):
    new_symbol = req.add_asset[:-6]
    new_side = "Short"
  else:
    new_symbol = req.add_asset
    new_side = "both"

  # Determine correct batch, strategy, and config for the new asset.
  # If batch_id is provided (from candidate search), look up the real info.
  # Otherwise fall back to old asset's info (legacy behavior).
  new_batch_id = req.batch_id or old_asset.batch_id
  new_strategy_name = old_asset.strategy_name
  new_config_name = old_asset.config_name
  if req.batch_id:
    batch_obj = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == req.batch_id).first()
    if batch_obj:
      new_batch_id = batch_obj.id
      # Extract strategy name from the Strategy table
      if batch_obj.strategy_id:
        strat_obj = db.query(Strategy).filter(Strategy.id == batch_obj.strategy_id).first()
        if strat_obj:
          new_strategy_name = strat_obj.name
      # Extract config name from batch name format "StrategyName — ConfigName [— Long/Short]"
      # Also look up config_group which stores the clean config name
      if batch_obj.config_group:
        new_config_name = batch_obj.config_group
      elif batch_obj.name and " \u2014 " in batch_obj.name:
        parts = batch_obj.name.split(" \u2014 ")
        # parts[0] = strategy, parts[1] = config, parts[2+] = direction/merged
        new_config_name = parts[1] if len(parts) > 1 else batch_obj.name
      elif batch_obj.name and " - " in batch_obj.name:
        parts = batch_obj.name.split(" - ")
        new_config_name = parts[1] if len(parts) > 1 else batch_obj.name

  new_asset = PortfolioAsset(
    portfolio_id=p.id,
    symbol=new_symbol,
    trade_side=new_side,
    strategy_name=new_strategy_name,
    is_active=True,
    raw_name=new_symbol,
    batch_id=new_batch_id,
    config_name=new_config_name,
  )
  db.add(new_asset)

  # Append to swap_history
  history = json.loads(p.swap_history) if p.swap_history else []
  history.append({
    "date": datetime.now(timezone.utc).isoformat(),
    "removed": req.remove_asset,
    "added": req.add_asset,
    "reason": req.reason,
  })
  p.swap_history = json.dumps(history)

  db.commit()
  db.refresh(p)

  return {
    "status": "swapped",
    "removed": req.remove_asset,
    "added": req.add_asset,
    "swap_history": history,
    "active_assets": [
      f"{a.symbol}_{a.trade_side}" if a.trade_side in ("Long", "Short") else a.symbol
      for a in p.assets if a.is_active
    ],
  }


def _build_view(df_slice: pd.DataFrame, start_date: str | None, end_date: str | None,
                **metrics_ctx) -> dict:
  """Build a full view dict with metrics, equity curve, monthly matrix."""
  if df_slice.empty:
    return {"metrics": {}, "full_metrics": {}, "trade_count": 0,
            "start_date": start_date, "end_date": end_date,
            "equity_curve": [], "monthly_matrix": {}}

  quick = _quick_metrics(df_slice)
  full = _safe_calculate_full_metrics(df_slice, **metrics_ctx)

  # Inject Stability (R²) from quick_metrics into full_metrics
  # (calculate_full_metrics doesn't compute it, but _quick_metrics does)
  if "stability" in quick and full:
    full["Stability (R\u00b2)"] = quick["stability"]

  # Equity curve
  eq_points: list[dict] = []
  if "Exit Time" in df_slice.columns:
    sorted_df = df_slice.sort_values("Exit Time")
    cum = sorted_df["Net Profit"].cumsum()
    step = max(1, len(cum) // 500)
    for i in range(0, len(cum), step):
      eq_points.append({
        "date": str(sorted_df.iloc[i]["Exit Time"].date()),
        "equity": round(float(cum.iloc[i]), 2),
      })
    if len(cum) > 0 and (not eq_points or eq_points[-1]["equity"] != round(float(cum.iloc[-1]), 2)):
      eq_points.append({
        "date": str(sorted_df.iloc[-1]["Exit Time"].date()),
        "equity": round(float(cum.iloc[-1]), 2),
      })

  # Monthly matrix — enriched with trade count and win rate per cell
  monthly: dict[str, dict[str, dict]] = {}
  if "Exit Time" in df_slice.columns:
    df_m = df_slice.copy()
    df_m["Year"] = df_m["Exit Time"].dt.year
    df_m["Month"] = df_m["Exit Time"].dt.month
    for (yr, mo), grp in df_m.groupby(["Year", "Month"]):
      yr_str = str(int(yr))
      if yr_str not in monthly:
        monthly[yr_str] = {}
      total = len(grp)
      wins = len(grp[grp["Net Profit"] > 0])
      monthly[yr_str][str(int(mo))] = {
        "pnl": round(float(grp["Net Profit"].sum()), 2),
        "trades": total,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
      }

  return {
    "metrics": quick, "full_metrics": full,
    "trade_count": len(df_slice),
    "start_date": start_date, "end_date": end_date,
    "equity_curve": eq_points, "monthly_matrix": monthly,
  }


@router.post("/{portfolio_id}/live-views")
def portfolio_live_views(portfolio_id: int, db: Session = Depends(get_db)):
  """Live views: 3 periods — IS, OOS, Live + legacy since_saved/since_optimized."""
  p = _get_portfolio(portfolio_id, db)
  df = _load_portfolio_trades(p, db)
  if not df.empty and "Entry Time" in df.columns:
    df["Entry Time"] = pd.to_datetime(df["Entry Time"])

  # Compute cost summary for the full trade set
  _cost_stg = json.loads(p.source_strategies) if p.source_strategies else {}
  cost_summary = _compute_cost_summary(df, _cost_stg)

  opt_end = getattr(p, "optimization_end_date", None)
  is_start = getattr(p, "is_start_date", None)
  created = p.created_at
  ctx = _get_portfolio_batch_context(p, db)

  views: dict = {}

  # --- IS (In-Sample): is_start → optimization_end_date ---
  if opt_end and not df.empty:
    opt_ts = pd.Timestamp(opt_end)
    is_df = df[df["Entry Time"].dt.date <= opt_ts.date()]
    if is_start:
      is_ts = pd.Timestamp(is_start)
      is_df = is_df[is_df["Entry Time"].dt.date >= is_ts.date()]
    views["in_sample"] = _build_view(
      is_df,
      is_start.isoformat() if hasattr(is_start, "isoformat") and is_start else None,
      opt_end.isoformat() if hasattr(opt_end, "isoformat") else str(opt_end),
      **ctx,
    )
  else:
    views["in_sample"] = _build_view(pd.DataFrame(), None, None)

  # --- Resolve stored OOS end date via shared helper (Tier 1.1 L04) ---
  _settings = json.loads(p.source_strategies) if p.source_strategies else {}
  _oos_end_str = _resolve_oos_end_date(p)
  _clip_tf = _settings.get("timeframe", "D")

  # --- OOS (Out-of-Sample): optimization_end_date → oos_end_date ---
  _live_clip_details: list[dict] = []
  if opt_end and not df.empty:
    opt_ts = pd.Timestamp(opt_end)
    oos_df = df[df["Entry Time"].dt.date > opt_ts.date()].copy()
    # Clip OOS at stored oos_end_date (same as portfolio_oos_date reference impl)
    if _oos_end_str and not oos_df.empty:
      # First: hard-remove trades that entered AFTER the OOS end date
      _oos_end_ts = pd.Timestamp(_oos_end_str)
      oos_df = oos_df[oos_df["Entry Time"].dt.date <= _oos_end_ts.date()].copy()
      # Then: clip trades straddling the boundary
      if not oos_df.empty:
        from backend.analysis.boundary_clip import clip_df_at_boundary
        oos_df, _live_clip_details = clip_df_at_boundary(oos_df, _oos_end_str, _clip_tf, db, return_details=True)
    _oos_end_label = _oos_end_str or (created.isoformat() if hasattr(created, "isoformat") and created else None)
    views["oos"] = _build_view(
      oos_df,
      opt_end.isoformat() if hasattr(opt_end, "isoformat") else str(opt_end),
      _oos_end_label,
      **ctx,
    )
  else:
    views["oos"] = _build_view(pd.DataFrame(), None, None)

  # --- Full OOS: optimization_end_date → present (no OOS end clipping) ---
  if opt_end and not df.empty:
    opt_ts = pd.Timestamp(opt_end)
    oos_full_df = df[df["Entry Time"].dt.date > opt_ts.date()]
    views["oos_full"] = _build_view(
      oos_full_df,
      opt_end.isoformat() if hasattr(opt_end, "isoformat") else str(opt_end),
      None,
      **ctx,
    )
  else:
    views["oos_full"] = _build_view(pd.DataFrame(), None, None)

  # --- Live: created_at → present ---
  if created and not df.empty:
    created_ts = pd.Timestamp(created)
    live_df = df[df["Entry Time"].dt.date > created_ts.date()]
    views["live"] = _build_view(
      live_df,
      created.isoformat() if hasattr(created, "isoformat") else str(created),
      None,
      **ctx,
    )
  else:
    views["live"] = _build_view(pd.DataFrame(), None, None)

  # --- Legacy views for backward compat ---
  # Tier 1.1 L03: align `since_optimized` with the CLIPPED oos view so the
  # dashboard's "OOS" number matches the summary card's "OOS" number. The
  # unclipped "OOS + live" window is available explicitly via oos_full.
  views["since_saved"] = views["live"]
  views["since_optimized"] = views["oos"]
  views["clipped_trades"] = _live_clip_details
  views["cost_summary"] = cost_summary

  return views


# --- Portfolio Test ---

class TestPortfolioRequest(BaseModel):
  initial_capital: float = 10_000.0
  commission_pct: float = COMMISSION_PCT
  leverage: int = 1
  risk_mode: str = "pct"
  risk_value: float = 0.0
  slippage_pct: float = 0.0  # % of fill price (0 = no slippage)
  oos_only: bool = False  # only test on OOS data (after optimization_end_date)


def _run_test_task(
  portfolio_id: int, task_id: str,
  initial_capital: float, commission_pct: float,
  leverage: int, risk_mode: str, risk_value: float,
  slippage_pct: float = 0.0, oos_only: bool = False,
):
  """Background task: run portfolio test and store result in progress."""
  db = SessionLocal()
  try:
    from backend.services.portfolio_test import run_portfolio_test
    result = run_portfolio_test(
      portfolio_id, db,
      initial_capital=initial_capital,
      commission_pct=commission_pct,
      leverage=leverage,
      risk_mode=risk_mode,
      risk_value=risk_value,
      slippage_pct=slippage_pct,
      oos_only=oos_only,
      progress=_test_progress,
      task_id=task_id,
      cancel_check=lambda: _is_test_cancelled(task_id),
    )
    if _is_test_cancelled(task_id):
      _test_progress[task_id]["status"] = "cancelled"
      _cancelled_tests.discard(task_id)
    else:
      _test_progress[task_id]["status"] = "completed"
      _test_progress[task_id]["result"] = result
  except Exception as e:
    logger.error("Portfolio test %s failed: %s", task_id, e)
    if _is_test_cancelled(task_id):
      _test_progress[task_id]["status"] = "cancelled"
      _cancelled_tests.discard(task_id)
    else:
      _test_progress[task_id]["status"] = "error"
      _test_progress[task_id]["error"] = str(e)
  finally:
    db.close()


@router.post("/{portfolio_id}/test-portfolio")
def test_portfolio(
  portfolio_id: int, req: TestPortfolioRequest,
  background_tasks: BackgroundTasks,
  db: Session = Depends(get_db),
):
  """Launch a portfolio test in the background."""
  p = _get_portfolio(portfolio_id, db)
  total_symbols = sum(1 for a in p.assets if a.is_active)

  if not total_symbols:
    raise HTTPException(400, "Portfolio has no assets")

  # Pre-validate: check batch reference exists
  settings = json.loads(p.source_strategies) if p.source_strategies else {}
  batch_ids: list[int] = []
  if settings.get("batch_ids"):
    batch_ids = settings["batch_ids"]
  elif settings.get("batch_id"):
    batch_ids = [settings["batch_id"]]
  if not batch_ids:
    raise HTTPException(400, "Portfolio has no batch reference — cannot run test")

  # Pre-validate: check at least one batch exists in DB
  from backend.db.models import BatchBacktestRun
  batch_count = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.id.in_(batch_ids))
    .count()
  )
  if batch_count == 0:
    raise HTTPException(400, "Source batch backtest(s) no longer exist — cannot run test")

  task_id = str(uuid.uuid4())[:8]
  _test_progress[task_id] = {
    "status": "running",
    "total_symbols": total_symbols,
    "completed_symbols": 0,
    "current_group": "",
    "started_at": datetime.now(timezone.utc).isoformat(),
  }

  background_tasks.add_task(
    _run_test_task, portfolio_id, task_id,
    req.initial_capital, req.commission_pct,
    req.leverage, req.risk_mode, req.risk_value,
    req.slippage_pct, req.oos_only,
  )

  return {"task_id": task_id, "status": "running", "total_symbols": total_symbols}


@router.get("/test-progress/{task_id}")
def test_progress(task_id: str):
  """Poll portfolio test progress."""
  prog = _test_progress.get(task_id)
  if not prog:
    raise HTTPException(404, "Task not found")
  return prog


@router.post("/test-progress/{task_id}/cancel")
def cancel_test(task_id: str):
  """Cancel a running portfolio test."""
  prog = _test_progress.get(task_id)
  if not prog:
    raise HTTPException(404, "Task not found")
  if prog.get("status") != "running":
    return {"cancelled": False, "reason": f"Task is already {prog.get('status')}"}
  _cancelled_tests.add(task_id)
  prog["status"] = "cancelled"
  logger.info("Portfolio test %s cancelled by user", task_id)
  return {"cancelled": True, "task_id": task_id}


# --- Synergy Audit ---

class SynergyAuditRequest(BaseModel):
  friction_pct: float = 0.20
  use_oos_only: bool = True  # If True, split at optimization_end_date


@router.post("/{portfolio_id}/synergy-audit")
def synergy_audit(
  portfolio_id: int, req: SynergyAuditRequest, db: Session = Depends(get_db),
):
  """Strategy synergy audit: marginal contribution, diversification, conflicts, breakeven."""
  from backend.analysis.synergy_audit import (
    compute_breakeven_friction,
    compute_diversification_multiplier,
    compute_marginal_contribution,
    detect_conflicts,
  )

  p = _get_portfolio(portfolio_id, db)
  df = _load_portfolio_trades(p, db)
  if df.empty:
    return {
      "marginal": [], "diversification": {}, "conflicts": {},
      "breakeven": {}, "trade_count": 0, "oos_only": False,
    }

  df["Exit Time"] = pd.to_datetime(df["Exit Time"])
  df["Entry Time"] = pd.to_datetime(df["Entry Time"])

  # Optionally split to OOS only
  oos_only = False
  if req.use_oos_only:
    settings = json.loads(p.source_strategies) if p.source_strategies else {}
    cutoff_str = settings.get("date_to") or settings.get("end_date")
    if not cutoff_str or cutoff_str in ("None", "all"):
      cutoff_str = p.optimization_end_date if p.optimization_end_date else None
    if cutoff_str:
      cutoff = pd.to_datetime(cutoff_str)
      df["Entry Time"] = pd.to_datetime(df["Entry Time"])
      oos_df = df[df["Entry Time"].dt.date > cutoff.date()].copy()
      if not oos_df.empty:
        df = oos_df
        oos_only = True

  # Determine default strategy name from portfolio assets
  strat_names = set()
  for a in p.assets:
    if a.strategy_name and a.strategy_name != "Unknown":
      strat_names.add(a.strategy_name)
  default_strat = list(strat_names)[0] if len(strat_names) == 1 else None

  marginal = compute_marginal_contribution(df, req.friction_pct, default_strat)
  diversification = compute_diversification_multiplier(df, req.friction_pct, default_strat)
  conflicts = detect_conflicts(df, default_strat)
  breakeven = compute_breakeven_friction(df, req.friction_pct)

  return {
    "marginal": marginal,
    "diversification": diversification,
    "conflicts": conflicts,
    "breakeven": breakeven,
    "trade_count": len(df),
    "oos_only": oos_only,
  }


# --- Risk Profile Comparison ---

class RiskProfile(BaseModel):
  name: str                           # user label, e.g. "Fixed $500"
  mode: str                           # "fixed" | "pct" | "martingale" | "anti_martingale" | "kelly"
  risk_value: float = 0.0             # $ amount (fixed/mart) or % (pct)
  initial_capital: float = 10_000.0
  # Martingale-specific
  multiplier: float = 2.0
  max_streak: int = 3
  # Kelly-specific
  kelly_fraction: float = 1.0         # 1.0=full, 0.5=half, 0.25=quarter

class RiskComparisonRequest(BaseModel):
  profiles: list[RiskProfile]         # 1-6 profiles to compare
  oos_only: bool = False


@router.post("/{portfolio_id}/risk-comparison")
def risk_comparison(
  portfolio_id: int, req: RiskComparisonRequest,
  db: Session = Depends(get_db),
):
  """Compare multiple risk profiles on a portfolio's normalized trades.

  Uses instant math scaling — no engine re-run.
  """
  from backend.analysis.trade_scaler import (
    scale_fixed,
    scale_kelly,
    scale_martingale,
    scale_pct_equity,
  )

  if not req.profiles or len(req.profiles) > 6:
    raise HTTPException(400, "Provide 1-6 risk profiles")

  p = _get_portfolio(portfolio_id, db)
  df = _load_portfolio_trades(p, db, date_filter="oos" if req.oos_only else None)
  if df.empty:
    raise HTTPException(404, "No trades found for this portfolio")

  # Get batch risk amount from source batch parameters
  settings = json.loads(p.source_strategies) if p.source_strategies else {}
  batch_ids: list[int] = settings.get("batch_ids", [])
  if not batch_ids and settings.get("batch_id"):
    batch_ids = [settings["batch_id"]]

  batch_risk_amount = 0.0
  is_normalized = False
  if batch_ids:
    batch = db.query(BatchBacktestRun).filter(
      BatchBacktestRun.id == batch_ids[0]
    ).first()
    if batch and batch.parameters:
      params = json.loads(batch.parameters)
      batch_risk_amount = params.get("batch_risk_amount", 0) or params.get("risk_value", 0)
      is_normalized = params.get("normalized", False)

  if batch_risk_amount <= 0:
    # Fallback: use risk_value from params
    if batch and batch.parameters:
      params = json.loads(batch.parameters)
      batch_risk_amount = params.get("risk_value", 100) or 100

  # Run each profile
  results = []
  for profile in req.profiles:
    if profile.mode == "fixed":
      res = scale_fixed(
        df, batch_risk_amount, profile.risk_value, profile.initial_capital,
      )
    elif profile.mode == "pct":
      res = scale_pct_equity(
        df, batch_risk_amount, profile.initial_capital, profile.risk_value,
      )
    elif profile.mode in ("martingale", "anti_martingale"):
      mart_type = "martingale" if profile.mode == "martingale" else "anti"
      res = scale_martingale(
        df, batch_risk_amount, profile.risk_value, profile.initial_capital,
        mart_type=mart_type,
        multiplier=profile.multiplier,
        max_streak=profile.max_streak,
      )
    elif profile.mode == "kelly":
      res = scale_kelly(
        df, batch_risk_amount, profile.initial_capital, profile.kelly_fraction,
      )
    else:
      raise HTTPException(400, f"Unknown mode: {profile.mode}")

    results.append({
      "name": profile.name,
      "mode": profile.mode,
      "metrics": res["metrics"],
      "equity_curve": res["equity_curve"],
    })

  return {
    "profiles": results,
    "batch_risk_amount": batch_risk_amount,
    "is_normalized": is_normalized,
    "trades_used": len(df),
  }


# ---------------------------------------------------------------------------
# Risk profile endpoints (read / update per-portfolio risk profile)
# ---------------------------------------------------------------------------

class GroupSizingConfig(BaseModel):
  batch_ids: list[int]
  risk_usd: float
  max_margin: float | None = None

class RiskProfileUpdate(BaseModel):
  initial_risk_usd: float | None = None
  override_enabled: bool | None = None
  group_sizing: list[GroupSizingConfig] | None = None
  # Portfolio-level override for the global max_position_pct guardrail.
  # 0 = disable cap, positive = % of equity per single position, None = leave unchanged.
  max_position_pct: float | None = None


@router.get("/{portfolio_id}/risk-profile")
def get_risk_profile(
  portfolio_id: int,
  db: Session = Depends(get_db),
):
  """Return the saved risk profile for a portfolio + global guardrails."""
  from backend.config.settings import get_settings

  p = _get_portfolio(portfolio_id, db)
  settings_obj = json.loads(p.source_strategies) if p.source_strategies else {}
  profile = settings_obj.get("risk_profile", {})

  # Resolve batch_risk_amount from source batch
  batch_ids: list[int] = settings_obj.get("batch_ids", [])
  if not batch_ids and settings_obj.get("batch_id"):
    batch_ids = [settings_obj["batch_id"]]
  batch_risk_amount = 0.0
  if batch_ids:
    batch = db.query(BatchBacktestRun).filter(
      BatchBacktestRun.id == batch_ids[0],
    ).first()
    if batch and batch.parameters:
      params = json.loads(batch.parameters)
      batch_risk_amount = params.get("batch_risk_amount", 0) or params.get("risk_value", 0) or 100

  # Global guardrails from settings
  s = get_settings()
  guardrails = {
    "daily_loss_limit_pct": s.daily_loss_limit_pct,
    "max_daily_loss_usd": s.max_daily_loss_usd,
    "max_positions_long": s.max_positions_long,
    "max_positions_short": s.max_positions_short,
    "max_positions_total": s.max_positions_total,
    "max_position_pct": s.max_position_pct,
    "margin_breathing_room_pct": s.margin_breathing_room_pct,
  }

  # Per-group info: collect distinct (group_name, timeframe, batch_id) from portfolio assets
  asset_groups: list[dict] = []
  pa_list = db.query(PortfolioAsset).filter(PortfolioAsset.portfolio_id == portfolio_id).all()
  _seen_groups: dict[str, dict] = {}
  for pa in pa_list:
    gn = getattr(pa, "group_name", None) or ""
    tf = getattr(pa, "timeframe", None) or ""
    bid = pa.batch_id
    if not gn and not tf:
      continue
    key = f"{gn}|{tf}"
    if key not in _seen_groups:
      _seen_groups[key] = {"group_name": gn, "timeframe": tf, "batch_ids": set(), "asset_count": 0}
    _seen_groups[key]["batch_ids"].add(bid)
    _seen_groups[key]["asset_count"] += 1
  for g in _seen_groups.values():
    asset_groups.append({
      "group_name": g["group_name"],
      "timeframe": g["timeframe"],
      "batch_ids": sorted(b for b in g["batch_ids"] if b is not None),
      "asset_count": g["asset_count"],
    })

  return {
    "portfolio_id": portfolio_id,
    "risk_profile": profile,
    "batch_risk_amount": batch_risk_amount,
    "guardrails": guardrails,
    "asset_groups": asset_groups,
  }


@router.patch("/{portfolio_id}/risk-profile")
def update_risk_profile(
  portfolio_id: int,
  req: RiskProfileUpdate,
  db: Session = Depends(get_db),
):
  """Update ONLY initial_risk_usd and override_enabled on a portfolio's risk profile.

  Mode, multiplier, max_streak, kelly settings, and TP splits are locked
  from the optimizer and cannot be changed here.
  """
  p = _get_portfolio(portfolio_id, db)
  settings_obj = json.loads(p.source_strategies) if p.source_strategies else {}
  profile = settings_obj.get("risk_profile", {})

  if req.initial_risk_usd is not None:
    if req.initial_risk_usd <= 0:
      raise HTTPException(400, "initial_risk_usd must be positive")
    profile["initial_risk_usd"] = req.initial_risk_usd

  if req.override_enabled is not None:
    profile["override_enabled"] = req.override_enabled

  if req.max_position_pct is not None:
    if req.max_position_pct < 0:
      raise HTTPException(400, "max_position_pct must be >= 0 (0 disables the cap)")
    profile["max_position_pct"] = req.max_position_pct

  # Per-group sizing → converts to per_batch batch_configs for the live engine
  if req.group_sizing is not None:
    if len(req.group_sizing) > 0:
      batch_configs: dict[str, dict] = {}
      for gs in req.group_sizing:
        for bid in gs.batch_ids:
          cfg: dict = {"mode": "fixed", "value": gs.risk_usd}
          if gs.max_margin is not None:
            cfg["maxMargin"] = gs.max_margin
          batch_configs[str(bid)] = cfg
      profile["sizing_master"] = "per_batch"
      profile["batch_configs"] = batch_configs
    else:
      # Empty list = remove per-batch sizing
      profile.pop("sizing_master", None)
      profile.pop("batch_configs", None)

  settings_obj["risk_profile"] = profile
  p.source_strategies = json.dumps(settings_obj)
  db.commit()

  return {
    "portfolio_id": portfolio_id,
    "risk_profile": profile,
    "message": "Risk profile updated",
  }


# ---------------------------------------------------------------------------
# DD Status — drawdown days + depth (OOS + Live combined equity)
# ---------------------------------------------------------------------------

@router.get("/{portfolio_id}/dd-status")
def portfolio_dd_status(portfolio_id: int, db: Session = Depends(get_db)):
  """Compute drawdown status treating OOS → Live as one continuous equity curve.

  The peak can come from the OOS period; days-in-DD counts from that peak
  to today.  Also returns worst OOS max drawdown for comparison.
  """
  import numpy as np
  from backend.utils.clock import today as _today

  p = _get_portfolio(portfolio_id, db)

  # Only meaningful for active (paper/real) portfolios
  if p.portfolio_type not in ("paper", "real"):
    return {"in_drawdown": False, "dd_days": 0, "dd_depth_usd": 0.0,
            "dd_depth_pct": 0.0, "peak_date": None, "peak_equity": 0.0,
            "current_equity": 0.0, "oos_worst_dd_usd": 0.0, "oos_worst_dd_pct": 0.0}

  # --- Load all trades (full IS+OOS+Live) ---
  df = _load_portfolio_trades(p, db, clip_is=True)
  if df.empty:
    return {"in_drawdown": False, "dd_days": 0, "dd_depth_usd": 0.0,
            "dd_depth_pct": 0.0, "peak_date": None, "peak_equity": 0.0,
            "current_equity": 0.0, "oos_worst_dd_usd": 0.0, "oos_worst_dd_pct": 0.0}

  df["Exit Time"] = pd.to_datetime(df["Exit Time"])
  df["Entry Time"] = pd.to_datetime(df["Entry Time"])

  # Determine OOS cutoff
  cutoff = None
  if p.optimization_end_date:
    cutoff = pd.Timestamp(p.optimization_end_date)

  # Split into OOS-only for computing worst OOS MDD
  oos_df = pd.DataFrame()
  if cutoff is not None:
    oos_df = df[df["Entry Time"].dt.date > cutoff.date()].copy()

  # Build combined equity curve from OOS + Live only (skip IS)
  if cutoff is not None:
    combined_df = df[df["Entry Time"].dt.date > cutoff.date()].copy()
  else:
    # No OOS cutoff — use all trades
    combined_df = df.copy()

  if combined_df.empty:
    return {"in_drawdown": False, "dd_days": 0, "dd_depth_usd": 0.0,
            "dd_depth_pct": 0.0, "peak_date": None, "peak_equity": 0.0,
            "current_equity": 0.0, "oos_worst_dd_usd": 0.0, "oos_worst_dd_pct": 0.0}

  combined_df = combined_df.sort_values("Exit Time").copy()

  # Get initial capital
  settings = json.loads(p.source_strategies) if p.source_strategies else {}
  initial_capital = float(settings.get("risk_profile", {}).get("initial_capital", 10000))

  # Build daily equity
  combined_df["_date"] = combined_df["Exit Time"].dt.floor("D")
  daily_pnl = combined_df.groupby("_date")["Net Profit"].sum()

  # Fill missing days with 0
  if len(daily_pnl) > 1:
    full_range = pd.date_range(start=daily_pnl.index.min(), end=daily_pnl.index.max(), freq="D")
    daily_pnl = daily_pnl.reindex(full_range).fillna(0)

  equity = initial_capital + daily_pnl.cumsum()
  peak_series = equity.cummax()
  dd_series = equity - peak_series

  # Current state
  current_equity = float(equity.iloc[-1]) if len(equity) > 0 else initial_capital
  peak_equity = float(peak_series.iloc[-1]) if len(peak_series) > 0 else initial_capital
  peak_idx = equity.idxmax() if len(equity) > 0 else None
  peak_date_str = str(peak_idx.date()) if peak_idx is not None else None

  current_dd_usd = float(dd_series.iloc[-1]) if len(dd_series) > 0 else 0.0
  current_dd_pct = (current_dd_usd / peak_equity * 100) if peak_equity > 0 else 0.0
  in_drawdown = current_dd_usd < -0.01  # small tolerance

  # DD days: from peak date to today
  dd_days = 0
  if in_drawdown and peak_idx is not None:
    dd_days = (_today() - peak_idx.date()).days

  # --- Worst OOS max drawdown ---
  oos_worst_dd_usd = 0.0
  oos_worst_dd_pct = 0.0
  if not oos_df.empty:
    oos_sorted = oos_df.sort_values("Exit Time").copy()
    oos_sorted["_date"] = oos_sorted["Exit Time"].dt.floor("D")
    oos_daily = oos_sorted.groupby("_date")["Net Profit"].sum()
    if len(oos_daily) > 1:
      oos_full = pd.date_range(start=oos_daily.index.min(), end=oos_daily.index.max(), freq="D")
      oos_daily = oos_daily.reindex(oos_full).fillna(0)
    oos_equity = initial_capital + oos_daily.cumsum()
    oos_peak = oos_equity.cummax()
    oos_dd = oos_equity - oos_peak
    oos_worst_dd_usd = float(oos_dd.min()) if len(oos_dd) > 0 else 0.0
    oos_worst_dd_pct = float((oos_dd / oos_peak.replace(0, float("nan"))).min() * 100) if len(oos_dd) > 0 else 0.0

  return {
    "in_drawdown": in_drawdown,
    "dd_days": dd_days,
    "dd_depth_usd": round(current_dd_usd, 2),
    "dd_depth_pct": round(current_dd_pct, 2),
    "peak_date": peak_date_str,
    "peak_equity": round(peak_equity, 2),
    "current_equity": round(current_equity, 2),
    "oos_worst_dd_usd": round(oos_worst_dd_usd, 2),
    "oos_worst_dd_pct": round(oos_worst_dd_pct, 2),
  }


# ── Paper-trading overview ────────────────────────────────────────────
#
# Compact summary for every active paper-or-real portfolio so the user
# can see at-a-glance which portfolios are doing what without clicking
# into each one. Uses _load_portfolio_trades, so the trade counts and
# PnL respect the basket-at-t filter for hybrid portfolios — same view
# shown by the trades-table modal and the post-fix KPI tile.

@router.get("/paper-overview")
def paper_overview(
  sparkline_points: int = 30,
  db: Session = Depends(get_db),
):
  """Compact summary of every active paper/real portfolio.

  Returns one entry per portfolio with the realized PnL since the
  portfolio's `activated_at`, plus a small evenly-spaced sample of
  cumulative PnL points for a sparkline chart. No DB writes.

  Query params:
    sparkline_points — how many points to sample for the sparkline
                       (default 30). Capped at 200.
  """
  sparkline_points = max(2, min(200, int(sparkline_points)))

  portfolios = (
    db.query(Portfolio)
    .filter(
      Portfolio.is_active == True,  # noqa: E712
      Portfolio.is_deleted == False,  # noqa: E712
      Portfolio.portfolio_type.in_(("paper", "real")),
    )
    .order_by(Portfolio.id)
    .all()
  )

  out: list[dict] = []
  empty_bucket = {"trades": 0, "pnl": 0.0, "win_rate": 0.0}
  for p in portfolios:
    summary: dict = {
      "id": p.id,
      "name": p.name,
      "mode": p.mode,
      "portfolio_type": p.portfolio_type,
      "activated_at": p.activated_at.isoformat() if p.activated_at else None,
      "last_refreshed_at": p.last_refreshed_at.isoformat() if p.last_refreshed_at else None,
      "trades_count": 0,
      "net_pnl": 0.0,
      "win_rate": 0.0,
      "avg_trade_pnl": 0.0,
      "sortino": None,
      "profit_factor": None,
      "max_dd": None,
      "sparkline": [],   # [{t: iso, pnl: cumulative_$}]
      "needs_refresh": False,
      # Initialize month buckets up front so the API response shape is
      # stable even when needs_refresh is True (early return below would
      # otherwise omit these fields).
      "current_month": dict(empty_bucket),
      "prev_month": dict(empty_bucket),
      "current_month_label": None,
      "prev_month_label": None,
    }

    # Read live_metrics_snapshot — the source of truth that paper_refresh
    # writes after applying the basket-at-t filter for hybrid portfolios.
    # If empty, mark the row "needs_refresh" so the UI can prompt.
    raw = p.live_metrics_snapshot
    if not raw or raw.strip() in ("", "{}"):
      summary["needs_refresh"] = True
      out.append(summary)
      continue
    try:
      snap = json.loads(raw)
    except Exception:
      summary["needs_refresh"] = True
      out.append(summary)
      continue
    if not isinstance(snap, dict) or not snap:
      summary["needs_refresh"] = True
      out.append(summary)
      continue

    summary["trades_count"] = int(snap.get("Total Positions") or snap.get("Total Trades") or 0)
    summary["net_pnl"] = round(float(snap.get("Total Net Profit", 0) or 0), 2)
    summary["win_rate"] = round(float(snap.get("Win Rate", 0) or 0), 1)
    summary["avg_trade_pnl"] = round(float(snap.get("Avg Trade Profit", 0) or 0), 2)
    summary["sortino"] = snap.get("Sortino Ratio")
    summary["profit_factor"] = snap.get("Profit Factor")
    summary["max_dd"] = snap.get("Max Drawdown ($)")
    # Per-month breakdown — filled by paper_refresh from the same filtered
    # trade DataFrame (so basket-at-t respected). Shape:
    #   { trades: int, pnl: float, win_rate: float }
    summary["current_month"] = snap.get("current_month") or {"trades": 0, "pnl": 0.0, "win_rate": 0.0}
    summary["prev_month"] = snap.get("prev_month") or {"trades": 0, "pnl": 0.0, "win_rate": 0.0}
    summary["current_month_label"] = snap.get("current_month_label")
    summary["prev_month_label"] = snap.get("prev_month_label")

    # Equity curve was added to the snapshot by paper_refresh; the panel
    # downsamples further client-side if needed.
    eq = snap.get("equity_curve") or []
    if isinstance(eq, list) and eq:
      if len(eq) <= sparkline_points:
        summary["sparkline"] = eq
      else:
        # Downsample to requested point count
        n = len(eq)
        step = (n - 1) / (sparkline_points - 1)
        seen = set()
        idx_iter = [
          int(round(i * step))
          for i in range(sparkline_points)
        ]
        idx_iter = [i for i in idx_iter if not (i in seen or seen.add(i))]
        summary["sparkline"] = [eq[i] for i in idx_iter]

    out.append(summary)

  # ── ACTUAL engine fills (live + paper) — what the engine truly traded ──
  # Read the `trades` table (real Bybit fills + paper-engine simulated fills,
  # both populated by the live engine) for each portfolio. Distinct from the
  # `Total Net Profit` snapshot field above which is the STRATEGY REPLAY view
  # (what the strategy would have done if perfectly executed). Both views are
  # exposed so the user can compare:
  #   • Strategy ("snapshot") = "what the strategy intended" — answers
  #     "is the strategy setup good?"
  #   • Engine               = "what the engine actually executed" — answers
  #     "is the engine actually trading what the strategy says?"
  # Filter: `Trade.is_test = False` excludes manual test trades. Per-portfolio
  # closed_at >= activated_at is enforced in Python below since SQLAlchemy
  # group-by + per-row activated_at compare would need a CASE expression.
  from backend.db.models import Trade
  from sqlalchemy import func as sa_func

  pid_to_summary = {s["id"]: s for s in out}
  for s in out:
    s["engine"] = {
      "trades": 0, "pnl": 0.0, "win_rate": 0.0,
      "first_at": None, "last_at": None,
    }

  if pid_to_summary:
    pids = list(pid_to_summary.keys())
    # One row per (portfolio_id, is_paper) so the user can see real vs
    # simulated fills if they want. We aggregate them together below.
    rows = (
      db.query(
        Trade.portfolio_id,
        Trade.realized_pnl,
        Trade.closed_at,
      )
      .filter(
        Trade.portfolio_id.in_(pids),
        Trade.is_test == False,  # noqa: E712
      )
      .all()
    )
    # Group in Python — small dataset, simple per-portfolio activation filter
    by_pid: dict[int, list] = {}
    for pid, pnl, closed_at in rows:
      by_pid.setdefault(pid, []).append((float(pnl or 0.0), closed_at))

    for pid, fills in by_pid.items():
      s = pid_to_summary.get(pid)
      if s is None:
        continue
      activated_at = None
      if s["activated_at"]:
        from datetime import datetime as _dt
        try:
          activated_at = _dt.fromisoformat(s["activated_at"].replace("Z", "+00:00"))
        except Exception:
          activated_at = None
      # Apply per-portfolio activation cutoff
      if activated_at is not None:
        if activated_at.tzinfo is not None:
          # Normalize: trade.closed_at is naive UTC by convention
          activated_at = activated_at.replace(tzinfo=None)
        fills = [(p, c) for p, c in fills if c is not None and c >= activated_at]
      if not fills:
        continue
      n = len(fills)
      total_pnl = sum(p for p, _ in fills)
      wins = sum(1 for p, _ in fills if p > 0)
      closed_ats = sorted([c for _, c in fills if c is not None])
      s["engine"] = {
        "trades": n,
        "pnl": round(total_pnl, 2),
        "win_rate": round(wins / n * 100.0, 1) if n > 0 else 0.0,
        "first_at": closed_ats[0].isoformat() if closed_ats else None,
        "last_at": closed_ats[-1].isoformat() if closed_ats else None,
      }

  return {"portfolios": out, "sparkline_points": sparkline_points}
