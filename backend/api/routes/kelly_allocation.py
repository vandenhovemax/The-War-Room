"""Kelly portfolio allocation API — compute, view, and configure multi-variate
Kelly weights for a portfolio's strategies/assets.

Endpoints:
  GET  /{portfolio_id}           — current weights + config
  POST /{portfolio_id}/compute   — trigger manual recompute
  PUT  /{portfolio_id}/config    — update config (schedule, thresholds)
  GET  /{portfolio_id}/history   — past allocation snapshots
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.analysis.daily_pnl import build_daily_pnl_matrix
from backend.analysis.kelly_allocator import (
  KellyAllocConfig,
  compute_kelly_allocation,
)
from backend.db.database import get_db
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  KellyAllocation,
  Portfolio,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


# ── Pydantic models ─────────────────────────────────────────────────

class KellyConfigUpdate(BaseModel):
  mode: str = "per_strategy"
  half_kelly_multiplier: float = 0.5
  sr_threshold: float = 0.5
  max_cap_pct: float = 0.40
  corr_warning_threshold: float = 0.7
  regime_dampening_factor: float = 0.5
  min_trades_per_strategy: int = 100
  is_lookback_days: int = 0
  schedule_type: str = "monthly"
  schedule_trade_count: int = 100


# ── GET current allocation ──────────────────────────────────────────

@router.get("/{portfolio_id}")
def get_kelly_allocation(portfolio_id: int, db: Session = Depends(get_db)):
  """Return current Kelly allocation weights + config for this portfolio."""
  p = db.get(Portfolio, portfolio_id)
  if not p:
    return {"error": "Portfolio not found"}

  current = (
    db.query(KellyAllocation)
    .filter(
      KellyAllocation.portfolio_id == portfolio_id,
      KellyAllocation.is_current == True,
    )
    .first()
  )

  if not current:
    return {
      "portfolio_id": portfolio_id,
      "enabled": False,
      "weights": {},
      "config": asdict(KellyAllocConfig()),
      "result": {},
      "computed_at": None,
      "trigger": None,
    }

  return {
    "portfolio_id": portfolio_id,
    "enabled": True,
    "weights": json.loads(current.weights_json or "{}"),
    "config": json.loads(current.config_json or "{}"),
    "result": json.loads(current.result_json or "{}"),
    "computed_at": current.computed_at.isoformat() if current.computed_at else None,
    "trigger": current.trigger,
  }


# ── POST compute (manual trigger) ──────────────────────────────────

@router.post("/{portfolio_id}/compute")
def compute_kelly(portfolio_id: int, db: Session = Depends(get_db)):
  """Load IS trades for the portfolio, compute Kelly weights, store result."""
  p = db.get(Portfolio, portfolio_id)
  if not p:
    return {"error": "Portfolio not found"}

  settings = json.loads(p.source_strategies or "{}")
  config_json = settings.get("kelly_allocation_config", {})

  # Build config from stored settings
  config = KellyAllocConfig(
    mode=config_json.get("mode", "per_strategy"),
    half_kelly_multiplier=config_json.get("half_kelly_multiplier", 0.5),
    sr_threshold=config_json.get("sr_threshold", 0.5),
    max_cap_pct=config_json.get("max_cap_pct", 0.40),
    corr_warning_threshold=config_json.get("corr_warning_threshold", 0.7),
    regime_dampening_factor=config_json.get("regime_dampening_factor", 0.5),
    min_trades_per_strategy=config_json.get("min_trades_per_strategy", 100),
    is_lookback_days=config_json.get("is_lookback_days", 0),
    schedule_type=config_json.get("schedule_type", "monthly"),
    schedule_trade_count=config_json.get("schedule_trade_count", 100),
  )

  # Load IS trades for the portfolio
  daily_pnl, oos_sharpe = _load_portfolio_is_data(db, p, settings, config)

  if daily_pnl.empty:
    return {"error": "No IS trade data available for Kelly allocation"}

  # Compute Kelly allocation
  result = compute_kelly_allocation(daily_pnl, oos_sharpe, config)

  # Store result (mark old as not current)
  db.query(KellyAllocation).filter(
    KellyAllocation.portfolio_id == portfolio_id,
    KellyAllocation.is_current == True,
  ).update({"is_current": False})

  alloc = KellyAllocation(
    portfolio_id=portfolio_id,
    weights_json=json.dumps(result.weights),
    result_json=json.dumps(result.to_dict()),
    config_json=json.dumps(asdict(config)),
    computed_at=datetime.now(timezone.utc),
    trigger="manual",
    is_current=True,
  )
  db.add(alloc)
  db.commit()

  logger.info(
    "Kelly allocation computed for portfolio %d: %d strategies, HHI=%.3f, regime=%s",
    portfolio_id, len(result.weights), result.herfindahl_index, result.regime_state,
  )

  return {
    "portfolio_id": portfolio_id,
    "weights": result.weights,
    "result": result.to_dict(),
    "config": asdict(config),
    "computed_at": alloc.computed_at.isoformat(),
  }


# ── PUT config ──────────────────────────────────────────────────────

@router.put("/{portfolio_id}/config")
def update_kelly_config(
  portfolio_id: int,
  req: KellyConfigUpdate,
  db: Session = Depends(get_db),
):
  """Update Kelly allocation config stored in portfolio source_strategies."""
  p = db.get(Portfolio, portfolio_id)
  if not p:
    return {"error": "Portfolio not found"}

  settings = json.loads(p.source_strategies or "{}")
  settings["kelly_allocation_config"] = req.model_dump()
  p.source_strategies = json.dumps(settings)
  db.commit()

  return {"ok": True, "config": req.model_dump()}


# ── GET history ─────────────────────────────────────────────────────

@router.get("/{portfolio_id}/history")
def get_kelly_history(portfolio_id: int, db: Session = Depends(get_db)):
  """Return all past Kelly allocation snapshots for this portfolio."""
  rows = (
    db.query(KellyAllocation)
    .filter(KellyAllocation.portfolio_id == portfolio_id)
    .order_by(KellyAllocation.computed_at.desc())
    .limit(50)
    .all()
  )

  return [
    {
      "id": r.id,
      "weights": json.loads(r.weights_json or "{}"),
      "computed_at": r.computed_at.isoformat() if r.computed_at else None,
      "trigger": r.trigger,
      "is_current": r.is_current,
      "hhi": json.loads(r.result_json or "{}").get("herfindahl_index"),
      "regime": json.loads(r.result_json or "{}").get("regime_state"),
    }
    for r in rows
  ]


# ── Internal helpers ────────────────────────────────────────────────

def _load_portfolio_is_data(
  db: Session,
  portfolio: Portfolio,
  settings: dict,
  config: KellyAllocConfig,
) -> tuple[pd.DataFrame, dict[str, float]]:
  """Load IS trades for the portfolio and build daily PnL matrix.

  Returns (daily_pnl_matrix, oos_sharpe_dict).
  """
  # Collect batch_ids and assets from portfolio
  batch_ids = settings.get("batch_ids", [])
  if not batch_ids and settings.get("batch_id"):
    batch_ids = [settings["batch_id"]]

  if not batch_ids:
    return pd.DataFrame(), {}

  # Build asset name mapping per batch
  batch_asset_names: dict[int | None, list[str]] = defaultdict(list)
  for a in portfolio.assets:
    if not a.is_active:
      continue
    if a.trade_side in ("Long", "Short"):
      batch_asset_names[a.batch_id].append(f"{a.symbol}_{a.trade_side}")
    else:
      batch_asset_names[a.batch_id].append(f"{a.symbol}_Long")
      batch_asset_names[a.batch_id].append(f"{a.symbol}_Short")

  # CS-010 (intentionally NOT migrated — verified 2026-04-21):
  # This function intentionally bypasses ``load_trades_lightweight`` and
  # loads raw ``BacktestTrade`` rows per-leg. Kelly builds a daily PnL
  # correlation matrix (see ``build_daily_pnl_matrix``) keyed on each
  # leg's ``exit_time.floor("D")``. If TP1 exits Tuesday and a trailing
  # stop exits Friday, the raw per-leg view attributes each day's PnL to
  # the day it actually landed — which is what Kelly needs for its daily
  # correlation/variance estimate. The canonical loader aggregates legs
  # into one position with ``exit_time = max(leg_exits)``, collapsing
  # Tuesday's PnL into Friday — a silent shift of the correlation matrix
  # and therefore the live Kelly weights. Additionally the loader drops
  # ``entry_time is None`` trades (CS-035) and applies ``pnl_multiplier``
  # scaling, both of which would further move weights.
  # Keep this path raw. If a future audit wants to re-examine, make sure
  # every partial-exit / None-entry / multiplier edge case gets a parity
  # test first — and the switch should be opt-in per portfolio, not a
  # silent global change.
  all_trades: list[BacktestTrade] = []
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

    # Clip to IS boundary (before optimization_end_date)
    if portfolio.optimization_end_date:
      boundary = portfolio.optimization_end_date
      trades = [t for t in trades if t.exit_time and t.exit_time < boundary]

    all_trades.extend(trades)
    allowed_names.update(names)

  if not all_trades:
    return pd.DataFrame(), {}

  # Filter to portfolio assets only
  all_trades = [
    t for t in all_trades
    if f"{t.symbol}_{(t.side or 'Long').title()}" in allowed_names
  ]

  if not all_trades:
    return pd.DataFrame(), {}

  # Build daily PnL matrix (columns = SYMBOL_Side)
  daily_pnl = build_daily_pnl_matrix(all_trades, include_side=True)

  # Compute OOS Sharpe per asset (trades AFTER optimization_end_date)
  oos_sharpe: dict[str, float] = {}
  if portfolio.optimization_end_date:
    oos_trades: list[BacktestTrade] = []
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
      boundary = portfolio.optimization_end_date
      trades = [t for t in trades if t.exit_time and t.exit_time >= boundary]
      oos_trades.extend(trades)

    # Filter to portfolio assets
    oos_trades = [
      t for t in oos_trades
      if f"{t.symbol}_{(t.side or 'Long').title()}" in allowed_names
    ]

    # Compute Sharpe per asset from OOS trades
    if oos_trades:
      oos_pnl = build_daily_pnl_matrix(oos_trades, include_side=True)
      for col in oos_pnl.columns:
        series = oos_pnl[col]
        mean_r = series.mean()
        std_r = series.std()
        if std_r > 0:
          oos_sharpe[col] = float(mean_r / std_r * np.sqrt(365))
        else:
          oos_sharpe[col] = 0.0

  return daily_pnl, oos_sharpe
