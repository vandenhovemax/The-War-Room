"""Portfolio routes — list, create, detail, update, delete, hide/unhide, builder."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.constants import COMMISSION_PCT
from backend.db.database import get_db
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  BatchBacktestRun,
  DailyPnL,
  JournalTrade,
  LiveEvent,
  LiveSignal,
  LiveStrategyState,
  Order,
  Portfolio,
  PortfolioAsset,
  Position,
  Strategy,
  Trade,
)
from backend.utils.logger import get_logger
from backend.utils.sanitize import sanitize_for_json

logger = get_logger(__name__)
router = APIRouter()


class PortfolioCreate(BaseModel):
  name: str
  batch_id: int | None = None
  batch_ids: list[int] | None = None  # multi-batch (manual builder)
  assets: list[dict]  # [{symbol, side, strategy_name?}]
  optimizer_settings: dict | None = None
  optimization_timing: dict | None = None
  metrics_snapshot: dict | None = None
  is_start_date: str | None = None
  optimization_end_date: str | None = None  # explicit OOS start
  portfolio_source: str = "optimizer"  # optimizer | cross_strategy | manual | wfo
  # Live-loop update semantics. Hybrid = basket + weights refreshed nightly
  # by Step-2; non_hybrid = basket frozen until OOS boundary. Default keeps
  # backward compat with UIs that don't send this field.
  mode: str | None = None  # "hybrid" | "non_hybrid" | None (auto-detect)


class PortfolioUpdate(BaseModel):
  name: str | None = None
  is_active: bool | None = None
  description: str | None = None


def _utc_iso(dt) -> str | None:
  """Serialize a UTC datetime with explicit +00:00 offset.

  SQLite strips timezone info, so naive datetimes from the DB need the
  UTC marker re-added so JavaScript interprets them correctly.
  """
  if dt is None:
    return None
  s = dt.isoformat()
  if "+" not in s and "Z" not in s:
    s += "+00:00"
  return s


def _portfolio_to_dict(p: Portfolio) -> dict:
  return {
    "id": p.id,
    "name": p.name,
    "is_active": p.is_active,
    "is_cross_strat": p.is_cross_strat,
    "metrics_snapshot": json.loads(p.metrics_snapshot) if p.metrics_snapshot else {},
    "source_strategies": json.loads(p.source_strategies) if p.source_strategies else {},
    "created_at": _utc_iso(p.created_at),
    "asset_count": sum(1 for a in p.assets if a.is_active) if p.assets else 0,
    "portfolio_type": getattr(p, "portfolio_type", "saved") or "saved",
    "source_portfolio_id": getattr(p, "source_portfolio_id", None),
    "activated_at": _utc_iso(getattr(p, "activated_at", None)),
    "deactivated_at": _utc_iso(getattr(p, "deactivated_at", None)),
    "live_metrics_snapshot": json.loads(p.live_metrics_snapshot) if getattr(p, "live_metrics_snapshot", None) and p.live_metrics_snapshot else {},
    "last_refreshed_at": _utc_iso(getattr(p, "last_refreshed_at", None)),
    "is_hidden": getattr(p, "is_hidden", False) or False,
    "is_deleted": getattr(p, "is_deleted", False) or False,
    "deleted_at": _utc_iso(getattr(p, "deleted_at", None)),
    "optimization_end_date": p.optimization_end_date.isoformat() if getattr(p, "optimization_end_date", None) else None,
    "is_start_date": p.is_start_date.isoformat() if getattr(p, "is_start_date", None) else None,
    "portfolio_source": getattr(p, "portfolio_source", "optimizer") or "optimizer",
    "description": getattr(p, "description", None) or "",
    "swap_history": json.loads(p.swap_history) if getattr(p, "swap_history", None) and p.swap_history else [],
    "mode": getattr(p, "mode", "non_hybrid") or "non_hybrid",
  }


@router.get("")
def list_portfolios(
  type: str | None = None,
  is_hidden: bool = Query(default=False),
  is_deleted: bool = Query(default=False),
  db: Session = Depends(get_db),
):
  q = db.query(Portfolio).filter(
    Portfolio.is_hidden == is_hidden,
    Portfolio.is_deleted == is_deleted,
  )
  if type:
    q = q.filter(Portfolio.portfolio_type == type)
  portfolios = q.order_by(Portfolio.created_at.desc()).all()
  return [_portfolio_to_dict(p) for p in portfolios]


@router.get("/count")
def portfolio_count(db: Session = Depends(get_db)):
  return {"count": db.query(Portfolio).count()}


def _extract_tp_split(portfolio: Portfolio, settings: dict, db: Session) -> None:
  """Extract TP1/TP2 quantity split from raw backtest trades and store in source_strategies."""
  from collections import defaultdict
  from statistics import median

  batch_ids: list[int] = settings.get("batch_ids", [])
  if not batch_ids:
    return

  # Load raw trades from source batches
  runs = (
    db.query(BacktestRun)
    .filter(BacktestRun.batch_id.in_(batch_ids), BacktestRun.status == "completed")
    .all()
  )
  if not runs:
    return

  run_ids = [r.id for r in runs]
  trades = (
    db.query(BacktestTrade)
    .filter(BacktestTrade.run_id.in_(run_ids))
    .filter(BacktestTrade.position_id.isnot(None))
    .all()
  )
  if not trades:
    return

  # Group by (run_id, position_id) — each position may have 1+ exit fills
  pos_groups: dict[tuple, list] = defaultdict(list)
  for t in trades:
    pos_groups[(t.run_id, t.position_id)].append(t)

  # Find positions with multi-exit (TP1 + TP2 or similar)
  tp1_pcts: list[float] = []
  for _key, fills in pos_groups.items():
    if len(fills) < 2:
      continue
    # Check if any fill has TP1/TP2 in exit_reason
    has_tp1 = any("TP1" in (f.exit_reason or "") for f in fills)
    has_tp2 = any("TP2" in (f.exit_reason or "") for f in fills)
    if not (has_tp1 and has_tp2):
      continue
    total_qty = sum(abs(f.quantity) for f in fills)
    if total_qty <= 0:
      continue
    tp1_qty = sum(abs(f.quantity) for f in fills if "TP1" in (f.exit_reason or ""))
    tp1_pcts.append(round(tp1_qty / total_qty * 100, 1))

  # Store in source_strategies.risk_profile
  source = json.loads(portfolio.source_strategies or "{}")
  profile = source.get("risk_profile", {})

  if tp1_pcts:
    med_tp1 = round(median(tp1_pcts), 1)
    profile["tp1_pct"] = med_tp1
    profile["tp2_pct"] = round(100 - med_tp1, 1)
  else:
    profile["tp1_pct"] = 100.0
    profile["tp2_pct"] = 0.0

  source["risk_profile"] = profile
  portfolio.source_strategies = json.dumps(source)
  db.commit()


@router.post("")
def create_portfolio(req: PortfolioCreate, db: Session = Depends(get_db)):
  """Save a portfolio from optimizer results."""
  logger.info("create_portfolio: name=%s, assets=%d, source=%s, batch_ids=%s",
              req.name, len(req.assets) if req.assets else 0,
              req.portfolio_source, req.batch_ids)
  if not req.name:
    raise HTTPException(status_code=400, detail="Name is required")
  if not req.assets:
    raise HTTPException(status_code=400, detail="At least one asset is required")

  try:
    # Merge batch_id(s) into settings so OOS analysis can find the source data
    settings = dict(req.optimizer_settings or {})
    if req.batch_ids:
      settings["batch_ids"] = req.batch_ids
    if req.batch_id:
      settings["batch_id"] = req.batch_id
      if "batch_ids" not in settings:
        settings["batch_ids"] = [req.batch_id]

    # Extract optimization end date — explicit param takes priority
    opt_end = req.optimization_end_date or settings.get("date_to") or settings.get("optimization_end_date")

    # Fallback: look at source batch configs for date_to
    if not opt_end:
      all_bids = settings.get("batch_ids") or ([settings["batch_id"]] if settings.get("batch_id") else [])
      if all_bids:
        batches = db.query(BatchBacktestRun).filter(BatchBacktestRun.id.in_(all_bids)).all()
        for b in batches:
          cfg = json.loads(b.parameters) if b.parameters else {}
          dt = cfg.get("date_to") or cfg.get("optimization_end_date")
          if dt:
            opt_end = dt
            break

    # Parse dates safely
    opt_end_dt = None
    if opt_end:
      try:
        opt_end_dt = datetime.fromisoformat(str(opt_end))
      except (ValueError, TypeError):
        logger.warning("Invalid optimization_end_date: %r — ignoring", opt_end)

    is_start_dt = None
    if req.is_start_date:
      try:
        is_start_dt = datetime.fromisoformat(str(req.is_start_date))
      except (ValueError, TypeError):
        logger.warning("Invalid is_start_date: %r — ignoring", req.is_start_date)
    # Fallback: auto-derive from optimizer_settings.date_from if not set
    if is_start_dt is None and req.optimizer_settings:
      date_from = req.optimizer_settings.get("date_from")
      if date_from:
        try:
          is_start_dt = datetime.fromisoformat(str(date_from))
        except (ValueError, TypeError):
          pass

    # Sanitize metrics_snapshot — handles numpy types, NaN, Inf, nested structures
    metrics = req.metrics_snapshot or {}
    safe_metrics = sanitize_for_json(metrics)

    # Sanitize settings — convert any non-serializable values
    safe_settings = json.loads(json.dumps(settings, default=str))

    # Preserve optimization timing if present
    if req.optimization_timing:
      safe_settings["optimization_timing"] = req.optimization_timing

    # Extract risk profile fields from optimizer_settings into a structured sub-object
    _rp_keys = [
      "risk_profile_mode", "risk_profile_value", "risk_profile_initial_capital",
      "risk_profile_multiplier", "risk_profile_max_streak",
      "risk_profile_martingale_compound", "risk_profile_martingale_scope",
      "risk_profile_sizing_master", "risk_profile_batch_configs",
      "risk_profile_kelly_n", "risk_profile_kelly_warmup",
      "risk_profile_kelly_scope", "risk_profile_kelly_lookback", "risk_profile_kelly_cap",
      "risk_profile_martingale_count_mode", "risk_profile_martingale_oos_reset",
      "risk_profile_auto_asset_configs",
      "risk_profile_martingale_start_after", "risk_profile_anti_martingale_reset_after",
      "risk_profile_apply_group_multiplier",
    ]
    has_risk_profile = any(safe_settings.get(k) for k in _rp_keys)
    if has_risk_profile:
      # Use .get() (not .pop()) — flat keys must stay for frontend display,
      # nested object is for the live trading engine
      rp = {
        "mode": safe_settings.get("risk_profile_mode", "fixed"),
        "initial_risk_usd": float(safe_settings.get("risk_profile_value", 0)),
        "initial_capital": float(safe_settings.get("risk_profile_initial_capital", 10000)),
        "multiplier": float(safe_settings.get("risk_profile_multiplier", 2.0)),
        "max_streak": int(safe_settings.get("risk_profile_max_streak", 3)),
        "martingale_compound": safe_settings.get("risk_profile_martingale_compound", "off"),
        "martingale_scope": safe_settings.get("risk_profile_martingale_scope", "asset"),
        "martingale_count_mode": safe_settings.get("risk_profile_martingale_count_mode", "full_position"),
        "kelly_n": int(safe_settings.get("risk_profile_kelly_n", 2)),
        "kelly_warmup": int(safe_settings.get("risk_profile_kelly_warmup", 20)),
        "kelly_scope": safe_settings.get("risk_profile_kelly_scope", "portfolio"),
        "kelly_lookback": int(safe_settings.get("risk_profile_kelly_lookback", 0)),
        "kelly_cap": float(safe_settings.get("risk_profile_kelly_cap", 0.0)),
        "sizing_master": safe_settings.get("risk_profile_sizing_master", "portfolio"),
        "martingale_oos_reset": safe_settings.get("risk_profile_martingale_oos_reset", True),
        "martingale_start_after": int(safe_settings.get("risk_profile_martingale_start_after", 0)),
        "anti_martingale_reset_after": int(safe_settings.get("risk_profile_anti_martingale_reset_after", 0)),
        "apply_group_multiplier": bool(safe_settings.get("risk_profile_apply_group_multiplier", False)),
        "override_enabled": True,
      }
      # Auto-risk per-asset configs: store raw JSON for dashboard reload
      raw_auto = safe_settings.get("risk_profile_auto_asset_configs")
      if raw_auto:
        rp["auto_asset_configs"] = raw_auto if isinstance(raw_auto, str) else json.dumps(raw_auto)
      # Per-batch configs: store parsed JSON in risk_profile for live engine
      raw_bc = safe_settings.get("risk_profile_batch_configs")
      if raw_bc and rp["sizing_master"] == "per_batch":
        try:
          rp["batch_configs"] = json.loads(raw_bc) if isinstance(raw_bc, str) else raw_bc
        except Exception as _exc:
          logger.debug("swallowed: %s", _exc)  # skip if malformed
      safe_settings["risk_profile"] = rp

    # Enrich cross-strategy portfolios with batch metadata (TF, strategy, extra TFs)
    if safe_settings.get("batch_ids"):
      batch_ids = safe_settings["batch_ids"]
      batches = db.query(BatchBacktestRun).filter(BatchBacktestRun.id.in_(batch_ids)).all()
      if batches:
        # Base timeframe: use first batch's (all batches in a cross-strat share the same TF)
        if not safe_settings.get("timeframe"):
          base_tfs = set(b.timeframe for b in batches if b.timeframe)
          if base_tfs:
            safe_settings["timeframe"] = batches[0].timeframe
            if len(base_tfs) > 1:
              logger.warning("Cross-strat batches have mixed TFs: %s — using %s",
                             base_tfs, batches[0].timeframe)

        # Extra timeframes: union across all batches
        all_extra = set()
        for b in batches:
          for tf in json.loads(b.extra_timeframes or "[]"):
            all_extra.add(tf)
        if all_extra:
          safe_settings["extra_timeframes"] = sorted(all_extra)

        # Strategy reference: from first batch's strategy_id
        if not safe_settings.get("strategy_name"):
          strategy_row = db.get(Strategy, batches[0].strategy_id) if batches[0].strategy_id else None
          if strategy_row:
            safe_settings["strategy_name"] = strategy_row.name
            safe_settings["strategy_id"] = strategy_row.id

    # Auto-set oos_end_date if not provided — use max batch data end date
    if not safe_settings.get("oos_end_date"):
      _all_bids = safe_settings.get("batch_ids") or ([safe_settings["batch_id"]] if safe_settings.get("batch_id") else [])
      if _all_bids:
        _batches_for_oos = db.query(BatchBacktestRun).filter(BatchBacktestRun.id.in_(_all_bids)).all()
        _end_dates = []
        for b in _batches_for_oos:
          _cfg = json.loads(b.parameters) if b.parameters else {}
          _de = _cfg.get("data_end_date") or _cfg.get("date_to")
          if _de:
            _end_dates.append(str(_de)[:10])
        if _end_dates:
          safe_settings["oos_end_date"] = max(_end_dates)
          safe_settings["oos_end_date_source"] = "auto_batch_end"
    else:
      safe_settings["oos_end_date_source"] = "manual"

    # Asset fingerprint for duplicate detection across optimizer + saved runs
    _asset_tags = sorted(
      f"{a.get('symbol','')}_{a.get('side','both')}_B{a.get('batch_id','')}"
      for a in req.assets
    )
    safe_settings["asset_fingerprint"] = "|".join(_asset_tags)

    # Resolve live-loop mode. Explicit request wins; otherwise derive from
    # the optimizer_settings hybrid_mode flag so this is a no-op for callers
    # that already describe the portfolio as hybrid in settings.
    _resolved_mode = req.mode
    if _resolved_mode not in ("hybrid", "non_hybrid"):
      _resolved_mode = "hybrid" if safe_settings.get("hybrid_mode") else "non_hybrid"

    portfolio = Portfolio(
      name=req.name,
      metrics_snapshot=json.dumps(safe_metrics),
      source_strategies=json.dumps(safe_settings),
      optimization_end_date=opt_end_dt,
      is_start_date=is_start_dt,
      portfolio_source=req.portfolio_source,
      is_cross_strat=(req.portfolio_source == "cross_strategy"),
      mode=_resolved_mode,
    )
    db.add(portfolio)
    db.flush()

    # Append DB ID to name so portfolios are easy to reference
    portfolio.name = f"{portfolio.name} [#{portfolio.id}]"

    for asset in req.assets:
      db.add(PortfolioAsset(
        portfolio_id=portfolio.id,
        symbol=asset.get("symbol", ""),
        trade_side=asset.get("side", "both"),
        strategy_name=asset.get("strategy_name", ""),
        raw_name=asset.get("symbol", ""),
        batch_id=asset.get("batch_id"),
        config_name=asset.get("config_name"),
        risk_mode=asset.get("risk_mode"),
      ))

    db.commit()
    db.refresh(portfolio)

    # Recalculate metrics_snapshot from trade-level data so the card
    # shows accurate IS expectancy metrics (PF, WR, positions, net profit).
    # Uses _quick_metrics() which matches the optimizer's _portfolio_metrics()
    # methodology (losses <= $0, daily PnL equity for DD/stability).
    # IMPORTANT: when optimization_end_date exists, use IS-only trades
    # (entry_time <= cutoff) to avoid mixing IS and OOS data.
    try:
      import pandas as pd

      from backend.api.routes.portfolio_analysis import _load_portfolio_trades, _quick_metrics
      df = _load_portfolio_trades(portfolio, db)
      if not df.empty:
        # Filter to IS-only when an OOS cutoff date is set
        if opt_end_dt and "Entry Time" in df.columns:
          df["Entry Time"] = pd.to_datetime(df["Entry Time"])
          df = df[df["Entry Time"].dt.date <= opt_end_dt.date()]
        if not df.empty:
          recalc = _quick_metrics(df)
          # Preserve optimizer-calculated stability & n_assets
          recalc["stability"] = recalc.get("stability", 0) or safe_metrics.get("stability", 0)
          recalc["n_assets"] = safe_metrics.get("n_assets", len(req.assets))
          portfolio.metrics_snapshot = json.dumps(sanitize_for_json(recalc))
          db.commit()
          db.refresh(portfolio)
    except Exception as e:
      logger.warning("Post-save snapshot recalculation failed: %s", e)
      # If portfolio has OOS cutoff, clear snapshot rather than keep potentially combined data
      if opt_end_dt:
        portfolio.metrics_snapshot = "{}"
        db.commit()
        logger.warning("Cleared metrics_snapshot for portfolio %d to prevent IS/OOS contamination", portfolio.id)

    # --- Extract TP1/TP2 split from raw trade data ---
    try:
      _extract_tp_split(portfolio, safe_settings, db)
    except Exception as e:
      logger.warning("TP split extraction failed: %s — skipping", e)

    # --- Seed initial KellyAllocation for hybrid portfolios ---
    # Graded category weights from the optimizer must size live trades from
    # trade 1 — not wait for the first nightly hybrid refresh. Run Step-2
    # now on the saved basket so the KellyAllocation row carries the same
    # per-asset composite scores the optimization produced.
    if _resolved_mode == "hybrid":
      try:
        from backend.services.hybrid_portfolio_refresh import refresh_portfolio
        seed_report = refresh_portfolio(portfolio.id, db=db, allow_inactive=True)
        if seed_report.get("ok"):
          logger.info(
            "create_portfolio: seeded initial KellyAllocation for hybrid portfolio %d "
            "(basket=%d, weights=%d)",
            portfolio.id, seed_report.get("basket_size", 0),
            seed_report.get("weights_written", 0),
          )
        else:
          logger.warning(
            "create_portfolio: initial weight seeding skipped for portfolio %d: %s",
            portfolio.id, seed_report.get("reason"),
          )
      except Exception as e:
        # Never block portfolio save on seeding — the nightly refresh will
        # still populate weights. Log and move on.
        logger.warning("create_portfolio: seed_initial_weights failed: %s", e)

    return _portfolio_to_dict(portfolio)

  except HTTPException:
    raise
  except Exception as exc:
    logger.exception("create_portfolio failed")
    db.rollback()
    raise HTTPException(status_code=500, detail=f"Save failed: {exc}") from exc


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")

  detail = _portfolio_to_dict(p)
  detail["assets"] = [
    {
      "id": a.id,
      "symbol": a.symbol,
      "side": a.trade_side,
      "strategy_name": a.strategy_name,
      "is_active": a.is_active,
      "batch_id": a.batch_id,
      "config_name": a.config_name,
      "risk_mode": a.risk_mode,
    }
    for a in p.assets
  ]

  # Look up source batch names (e.g. "RSI Breakout — Conservative")
  settings = json.loads(p.source_strategies) if p.source_strategies else {}
  bid_list: list[int] = []
  if settings.get("batch_ids"):
    bid_list = settings["batch_ids"]
  elif settings.get("batch_id"):
    bid_list = [settings["batch_id"]]
  if bid_list:
    batches = (
      db.query(BatchBacktestRun)
      .filter(BatchBacktestRun.id.in_(bid_list))
      .all()
    )
    detail["batch_names"] = [b.name or f"Batch #{b.id}" for b in batches]
    detail["batch_id_map"] = {b.id: b.name or f"Batch #{b.id}" for b in batches}

    # Include first batch's backtest settings for Portfolio Test form defaults
    first_params = json.loads(batches[0].parameters) if batches[0].parameters else {}
    detail["batch_settings"] = {
      "initial_capital": first_params.get("initial_capital", 10000),
      "commission_pct": first_params.get("commission_pct", COMMISSION_PCT),
      "leverage": first_params.get("leverage", 1),
      "risk_mode": first_params.get("risk_mode", "pct"),
      "risk_value": first_params.get("risk_value", 0),
      "slippage_value": first_params.get("slippage_value", 0),
    }

    # Per-batch risk values — only included when batches have different risk amounts
    per_batch_risk: dict[int, dict] = {}
    for b in batches:
      bp = json.loads(b.parameters) if b.parameters else {}
      per_batch_risk[b.id] = {
        "name": b.name or f"Batch #{b.id}",
        "risk_value": bp.get("risk_value", 0),
        "risk_mode": bp.get("risk_mode", "pct"),
      }
    unique_risks = set(v["risk_value"] for v in per_batch_risk.values())
    if len(unique_risks) > 1:
      detail["batch_risk_values"] = per_batch_risk
  else:
    detail["batch_names"] = []
    detail["batch_id_map"] = {}
    detail["batch_settings"] = {}

  return detail


@router.put("/{portfolio_id}")
def update_portfolio(
  portfolio_id: int, req: PortfolioUpdate, db: Session = Depends(get_db),
):
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")

  if req.name is not None:
    p.name = req.name
  if req.is_active is not None:
    p.is_active = req.is_active
  if req.description is not None:
    p.description = req.description

  db.commit()
  db.refresh(p)
  return _portfolio_to_dict(p)


@router.delete("/{portfolio_id}")
async def delete_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
  """Soft-delete a portfolio (moves to Deleted tab, recoverable)."""
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")

  # Block deletion of active/live portfolios
  if p.is_active:
    raise HTTPException(
      status_code=400,
      detail="Cannot delete an active portfolio. Stop trading/monitoring first.",
    )
  try:
    from backend.exchange.engine_coordinator import get_engine
    engine = get_engine()
    status = engine.get_status()
    if portfolio_id in (status.get("active_portfolios") or []):
      raise HTTPException(
        status_code=400,
        detail="Cannot delete a portfolio that is live trading. Stop trading first.",
      )
  except HTTPException:
    raise
  except Exception as _exc:
    logger.debug("swallowed: %s", _exc)  # Engine might not be running

  p.is_deleted = True
  p.deleted_at = datetime.now(timezone.utc)
  db.commit()
  return {"ok": True}


@router.patch("/{portfolio_id}/restore")
def restore_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
  """Restore a soft-deleted portfolio back to Saved."""
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")
  p.is_deleted = False
  p.deleted_at = None
  p.portfolio_type = "saved"
  db.commit()
  return {"ok": True}


@router.delete("/{portfolio_id}/permanent")
def permanently_delete_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
  """Permanently delete a portfolio (irreversible). Only works on soft-deleted portfolios."""
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")
  if not p.is_deleted:
    raise HTTPException(
      status_code=400,
      detail="Portfolio must be in Deleted tab first. Delete it normally, then permanently delete.",
    )

  # Delete child rows that belong exclusively to this portfolio
  db.query(PortfolioAsset).filter(
    PortfolioAsset.portfolio_id == portfolio_id,
  ).delete(synchronize_session=False)

  # Delete rows from tables with non-nullable FK to portfolios
  db.query(LiveStrategyState).filter(
    LiveStrategyState.portfolio_id == portfolio_id,
  ).delete(synchronize_session=False)
  db.query(LiveSignal).filter(
    LiveSignal.portfolio_id == portfolio_id,
  ).delete(synchronize_session=False)

  # Null out FK references in shared tables so cascade isn't needed
  for model in (Order, Position, Trade, DailyPnL, LiveEvent, JournalTrade):
    db.query(model).filter(model.portfolio_id == portfolio_id).update(
      {"portfolio_id": None}, synchronize_session=False,
    )

  db.delete(p)
  db.commit()
  return {"ok": True}


@router.patch("/{portfolio_id}/hide")
def hide_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")
  p.is_hidden = True
  db.commit()
  return {"ok": True}


@router.patch("/{portfolio_id}/unhide")
def unhide_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")
  p.is_hidden = False
  # Restore to saved type when unhiding
  p.portfolio_type = "saved"
  db.commit()
  return {"ok": True}


@router.patch("/{portfolio_id}/archive")
def archive_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
  """Archive a portfolio — preserves all data (trades, KPIs, assets) but moves it
  to the Archived tab. Portfolio can be restored later."""
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")

  # Block archiving of active/live portfolios
  if p.is_active:
    raise HTTPException(
      status_code=400,
      detail="Cannot archive an active portfolio. Stop trading/monitoring first.",
    )

  p.portfolio_type = "archived"
  p.deactivated_at = p.deactivated_at or datetime.now(timezone.utc)
  db.commit()
  return {"ok": True}


@router.patch("/{portfolio_id}/unarchive")
def unarchive_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
  """Restore an archived portfolio back to Saved."""
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")
  if p.portfolio_type != "archived":
    raise HTTPException(status_code=400, detail="Portfolio is not archived")
  p.portfolio_type = "saved"
  db.commit()
  return {"ok": True}


# --- Portfolio Builder endpoints ---

@router.get("/builder/batches")
def builder_list_batches(db: Session = Depends(get_db)):
  """List available batches for the manual portfolio builder."""
  batches = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.status == "completed")
    .filter(BatchBacktestRun.is_deleted != True)
    .order_by(BatchBacktestRun.created_at.desc())
    .all()
  )
  result = []
  for b in batches:
    strat = db.query(Strategy).filter(Strategy.id == b.strategy_id).first()
    strat_name = strat.name if strat else f"Strategy #{b.strategy_id}"
    result.append({
      "batch_id": b.id,
      "name": b.name or f"Batch #{b.id}",
      "strategy_name": strat_name,
      "strategy_id": b.strategy_id,
      "direction_filter": b.direction_filter or "both",
      "total_symbols": b.total_symbols,
      "completed_symbols": b.completed_symbols,
      "created_at": b.created_at.isoformat() if b.created_at else None,
    })
  return {"batches": result}


@router.get("/builder/batch-assets")
def builder_batch_assets(
  batch_id: int = Query(...),
  direction: str = Query(default="both"),
  db: Session = Depends(get_db),
):
  """List assets from a batch with per-asset IS metrics for the builder."""
  from backend.analysis.tv_data_loader import trades_from_db

  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if not batch:
    raise HTTPException(404, "Batch not found")

  runs = (
    db.query(BacktestRun)
    .filter(BacktestRun.batch_id == batch_id, BacktestRun.status == "completed")
    .all()
  )
  if not runs:
    return {"assets": [], "batch_name": batch.name}

  run_ids = [r.id for r in runs]
  trades = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).all()
  df = trades_from_db(trades)
  if df.empty:
    return {"assets": [], "batch_name": batch.name}

  # Filter by direction
  if direction == "Long":
    df = df[df["Asset"].str.endswith("_Long")]
  elif direction == "Short":
    df = df[df["Asset"].str.endswith("_Short")]

  # Compute per-asset metrics
  assets = []
  for asset_name in sorted(df["Asset"].unique()):
    adf = df[df["Asset"] == asset_name]
    wins = adf[adf["Net Profit"] > 0]
    losses = adf[adf["Net Profit"] <= 0]
    gp = float(wins["Net Profit"].sum()) if not wins.empty else 0
    gl = float(abs(losses["Net Profit"].sum())) if not losses.empty else 0
    net = float(adf["Net Profit"].sum())
    # Parse symbol and side from asset name
    if asset_name.endswith("_Long"):
      symbol = asset_name.replace("_Long", "")
      side = "Long"
    elif asset_name.endswith("_Short"):
      symbol = asset_name.replace("_Short", "")
      side = "Short"
    else:
      symbol = asset_name
      side = "Both"

    assets.append({
      "asset_name": asset_name,
      "symbol": symbol,
      "side": side,
      "trades": len(adf),
      "net_profit": round(net, 2),
      "profit_factor": round(min(gp / gl, 10.0), 2) if gl > 0 else None,
      "win_rate": round(len(wins) / len(adf) * 100, 1) if len(adf) > 0 else 0,
      "avg_trade": round(float(adf["Net Profit"].mean()), 2) if len(adf) > 0 else 0,
    })

  # Sort by net profit descending
  assets.sort(key=lambda x: x["net_profit"], reverse=True)

  # Get strategy name
  strat = db.query(Strategy).filter(Strategy.id == batch.strategy_id).first()
  strat_name = strat.name if strat else ""

  return {
    "assets": assets,
    "batch_name": batch.name or f"Batch #{batch.id}",
    "strategy_name": strat_name,
  }


@router.get("/{portfolio_id}/data-freshness")
def get_data_freshness(portfolio_id: int, db: Session = Depends(get_db)):
  """Check candle data freshness for each asset in a portfolio.

  Returns per-asset: latest candle timestamp, how many candles behind,
  and whether the data is fresh (within 3 candle periods of now).
  Also returns all_fresh flag for portfolio-level indicator.
  """
  from backend.data.data_manager import DataManager

  portfolio = db.get(Portfolio, portfolio_id)
  if not portfolio:
    raise HTTPException(404, "Portfolio not found")

  source = json.loads(portfolio.source_strategies or "{}")
  base_tf = source.get("timeframe", "")

  # Batch fallback for timeframe (cross-strat portfolios, pre-enrichment portfolios)
  if not base_tf and source.get("batch_ids"):
    from backend.db.models import BatchBacktestRun
    for bid in source["batch_ids"]:
      batch = db.get(BatchBacktestRun, bid)
      if batch and batch.timeframe:
        base_tf = batch.timeframe
        break
  if not base_tf:
    base_tf = "30"

  # DataManager._canonical_tf() normalizes any format on query,
  # so we just pass base_tf directly. Interval calculation uses
  # canonical (Bybit) format keys.
  from backend.utils.tf_resolver import canonical_tf
  canon_tf = canonical_tf(base_tf)

  # Candle interval in seconds (canonical Bybit format keys)
  tf_seconds = {
    "1": 60, "3": 180, "5": 300, "15": 900, "30": 1800,
    "60": 3600, "120": 7200, "240": 14400, "360": 21600, "720": 43200,
    "D": 86400, "W": 604800,
  }
  interval = tf_seconds.get(canon_tf, 1800)

  assets = db.query(PortfolioAsset).filter(
    PortfolioAsset.portfolio_id == portfolio_id,
    PortfolioAsset.is_active == True,
  ).all()

  manager = DataManager()
  now = datetime.now(timezone.utc)
  results = []
  all_fresh = True

  for asset in assets:
    latest = manager.get_latest_timestamp(asset.symbol, canon_tf)
    if latest:
      if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
      age_seconds = (now - latest).total_seconds()
      candles_behind = age_seconds / interval
      is_fresh = candles_behind < 3
    else:
      candles_behind = -1
      is_fresh = False

    if not is_fresh:
      all_fresh = False

    results.append({
      "symbol": asset.symbol,
      "timeframe": canon_tf,
      "latest_candle": latest.isoformat() if latest else None,
      "candles_behind": round(candles_behind, 1),
      "is_fresh": is_fresh,
    })

  return {
    "portfolio_id": portfolio_id,
    "all_fresh": all_fresh,
    "timeframe": canon_tf,
    "assets": results,
  }


# ── T-002 Step 8 — Phase-2 events feed for the live UI ────────────────

_PHASE2_EVENT_TYPES = (
  "hybrid_refresh",
  "hybrid_rotation",
  "phase2_evaluated_no_change",
)


@router.get("/{portfolio_id}/phase2-events")
def list_phase2_events(
  portfolio_id: int,
  limit: int = Query(default=50, ge=1, le=200),
  offset: int = Query(default=0, ge=0),
  event_type: str | None = Query(default=None),
  db: Session = Depends(get_db),
):
  """List recent phase-2 LiveEvent rows for a portfolio.

  Returns rows produced by `phase2_recorder.record_phase2_event`,
  newest first. Each row exposes a parsed-but-thin `details` view so
  the UI can render basket diffs, dispatch outcomes, and reconstruction
  flags without re-parsing the JSON blob.

  Query params:
    * `limit` — page size (1-200, default 50)
    * `offset` — pagination offset
    * `event_type` — optional filter; one of
      `hybrid_refresh`, `hybrid_rotation`, `phase2_evaluated_no_change`
  """
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")

  if event_type is not None and event_type not in _PHASE2_EVENT_TYPES:
    raise HTTPException(
      status_code=400,
      detail=f"event_type must be one of {list(_PHASE2_EVENT_TYPES)}",
    )

  q = db.query(LiveEvent).filter(LiveEvent.portfolio_id == portfolio_id)
  if event_type is not None:
    q = q.filter(LiveEvent.event_type == event_type)
  else:
    q = q.filter(LiveEvent.event_type.in_(_PHASE2_EVENT_TYPES))

  total = q.count()
  rows = (
    q.order_by(LiveEvent.created_at.desc(), LiveEvent.id.desc())
    .offset(offset)
    .limit(limit)
    .all()
  )

  events: list[dict] = []
  for r in rows:
    try:
      details = json.loads(r.details) if r.details else {}
    except (ValueError, TypeError):
      details = {}
    if not isinstance(details, dict):
      details = {}
    events.append({
      "id": r.id,
      "portfolio_id": r.portfolio_id,
      "event_type": r.event_type,
      "symbol": r.symbol or "",
      "created_at": _utc_iso(r.created_at),
      "details": {
        "decided_at_iso": details.get("decided_at_iso"),
        "trigger": details.get("trigger"),
        "recorder_version": details.get("recorder_version"),
        "phase1_window_num": details.get("phase1_window_num"),
        "phase1_window_num_synth_for_stub": bool(
          details.get("phase1_window_num_synth_for_stub", False)
        ),
        "phase1_window_oos_start_iso": details.get("phase1_window_oos_start_iso"),
        "phase1_window_oos_end_iso": details.get("phase1_window_oos_end_iso"),
        "prior_basket": details.get("prior_basket") or [],
        "chosen_basket": details.get("chosen_basket") or [],
        "added": details.get("added") or [],
        "removed": details.get("removed") or [],
        "basket_unchanged": bool(details.get("basket_unchanged", False)),
        "dispatch_outcome": details.get("dispatch_outcome"),
        "dispatch_verified_at_iso": details.get("dispatch_verified_at_iso"),
        "reconstructed": bool(details.get("reconstructed", False)),
        "decision_inputs_hash": details.get("decision_inputs_hash"),
      },
    })

  return {
    "portfolio_id": portfolio_id,
    "limit": limit,
    "offset": offset,
    "total": total,
    "event_type_filter": event_type,
    "events": events,
  }


# ── T-003 Delta 7: Unified portfolio history feed ─────────────────────
#
# Single chronological merge of two stores:
#   1. Portfolio.swap_history JSON  (manual swaps + WFAS rebuilds)
#   2. LiveEvent rows where event_type ∈ BASKET_EVENT_TYPES (nightly hybrid)
#
# Per-row type discriminator: manual_swap | phase2_refresh | phase2_rotation
# | phase2_no_change. Frontend (Delta 8) renders one timeline keyed by type.

_HISTORY_TYPES = (
  "manual_swap",
  "phase2_refresh",
  "phase2_rotation",
  "phase2_no_change",
)

_LIVE_EVENT_TO_HISTORY_TYPE = {
  "hybrid_refresh": "phase2_refresh",
  "hybrid_rotation": "phase2_rotation",
  "phase2_evaluated_no_change": "phase2_no_change",
}


def _utc_iso_str(value) -> str | None:
  """Coerce a datetime / ISO string to a UTC-suffixed ISO-8601 string.

  swap_history rows store ISO strings written via `datetime.now(utc).isoformat()`,
  so they already carry an offset. LiveEvent.created_at is a naive datetime
  in the DB (SQLite drops tz) so we re-attach +00:00.
  """
  if value is None:
    return None
  if isinstance(value, datetime):
    return _utc_iso(value)
  s = str(value).strip()
  if not s:
    return None
  if "+" not in s and "Z" not in s and "T" in s:
    return s + "+00:00"
  return s


def _swap_history_to_entry(raw: dict) -> dict | None:
  """Map one Portfolio.swap_history row → unified history entry.

  Two known shapes (both legacy, both written by existing routes):
    a) manual swap (portfolio_analysis.py:swap_execute)
       {date, removed, added, reason}
    b) WFAS rebuild (wfo.py:save_phase2_rebuild)
       {date, type: "phase2_refresh", added, removed, unchanged,
        new_count, old_count}
  """
  if not isinstance(raw, dict):
    return None
  ts = _utc_iso_str(raw.get("date"))
  raw_type = (raw.get("type") or "").strip().lower()

  if raw_type == "phase2_refresh":
    added = raw.get("added") or []
    removed = raw.get("removed") or []
    summary_parts = []
    if removed:
      summary_parts.append(f"Removed {', '.join(map(str, removed))}")
    if added:
      summary_parts.append(f"added {', '.join(map(str, added))}")
    summary = "; ".join(summary_parts) if summary_parts else "WFAS rebuild — no basket change"
    return {
      "type": "phase2_refresh",
      "timestamp_iso": ts,
      "summary": summary,
      "actor": "user",
      "details": {
        "added": list(added),
        "removed": list(removed),
        "unchanged": list(raw.get("unchanged") or []),
        "new_count": raw.get("new_count"),
        "old_count": raw.get("old_count"),
      },
      "reconstructed": False,
      "source_store": "swap_history",
    }

  # Default: legacy manual swap
  removed_one = raw.get("removed")
  added_one = raw.get("added")
  reason = raw.get("reason") or ""
  if removed_one and added_one:
    summary = f"Swapped {removed_one} → {added_one}"
  elif removed_one:
    summary = f"Removed {removed_one}"
  elif added_one:
    summary = f"Added {added_one}"
  else:
    summary = "Manual swap"
  if reason:
    summary = f"{summary} ({reason})"
  return {
    "type": "manual_swap",
    "timestamp_iso": ts,
    "summary": summary,
    "actor": "user",
    "details": {
      "removed": removed_one,
      "added": added_one,
      "reason": reason,
    },
    "reconstructed": False,
    "source_store": "swap_history",
  }


def _live_event_to_entry(row: LiveEvent) -> dict | None:
  """Map one LiveEvent row → unified history entry.

  Only basket-affecting event_types are mapped (per BASKET_EVENT_TYPES).
  """
  hist_type = _LIVE_EVENT_TO_HISTORY_TYPE.get(row.event_type)
  if hist_type is None:
    return None

  try:
    details_raw = json.loads(row.details) if row.details else {}
  except (ValueError, TypeError):
    details_raw = {}
  if not isinstance(details_raw, dict):
    details_raw = {}

  added = details_raw.get("added") or []
  removed = details_raw.get("removed") or []
  basket_unchanged = bool(details_raw.get("basket_unchanged", False))
  trigger = details_raw.get("trigger") or "nightly_cron"

  # Build summary per type
  if hist_type == "phase2_no_change":
    summary = "Nightly evaluation — no basket change"
  elif hist_type == "phase2_rotation":
    if removed and added:
      summary = f"Rotation: removed {', '.join(map(str, removed))}, added {', '.join(map(str, added))}"
    else:
      summary = "Rotation"
  else:  # phase2_refresh
    if basket_unchanged:
      summary = "Nightly refresh — basket unchanged"
    elif removed and added:
      summary = f"Refresh: removed {', '.join(map(str, removed))}, added {', '.join(map(str, added))}"
    elif added:
      summary = f"Refresh: added {', '.join(map(str, added))}"
    elif removed:
      summary = f"Refresh: removed {', '.join(map(str, removed))}"
    else:
      summary = "Phase-2 refresh"

  # Actor: prefer trigger field, fall back per type
  if trigger == "rotation_day":
    actor = "rotation"
  elif trigger == "cold_save":
    actor = "cold_save"
  else:
    actor = trigger or "nightly_cron"

  ts = _utc_iso_str(details_raw.get("decided_at_iso") or row.created_at)

  return {
    "type": hist_type,
    "timestamp_iso": ts,
    "summary": summary,
    "actor": actor,
    "details": {
      "prior_basket": details_raw.get("prior_basket") or [],
      "chosen_basket": details_raw.get("chosen_basket") or [],
      "added": list(added),
      "removed": list(removed),
      "basket_unchanged": basket_unchanged,
      "trigger": trigger,
      "dispatch_outcome": details_raw.get("dispatch_outcome"),
      "phase1_window_num": details_raw.get("phase1_window_num"),
      "phase1_window_oos_start_iso": details_raw.get("phase1_window_oos_start_iso"),
      "phase1_window_oos_end_iso": details_raw.get("phase1_window_oos_end_iso"),
      "decision_inputs_hash": details_raw.get("decision_inputs_hash"),
      "live_event_id": row.id,
      "live_event_type": row.event_type,
    },
    "reconstructed": bool(details_raw.get("reconstructed", False)),
    "source_store": "live_event",
  }


@router.get("/{portfolio_id}/history")
def get_portfolio_history(
  portfolio_id: int,
  limit: int = Query(default=100, ge=1, le=500),
  offset: int = Query(default=0, ge=0),
  type: str | None = Query(default=None),
  db: Session = Depends(get_db),
):
  """T-003 Delta 7 — Unified chronological history feed for a portfolio.

  Merges manual swap entries from `Portfolio.swap_history` with phase-2
  basket events from `LiveEvent` (filtered to `BASKET_EVENT_TYPES`).
  Each row carries a type discriminator so the frontend can render a
  single timeline:

      manual_swap | phase2_refresh | phase2_rotation | phase2_no_change

  Newest entries first. Pagination is applied AFTER the merge so
  ordering is stable across types.
  """
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(status_code=404, detail="Portfolio not found")

  if type is not None and type not in _HISTORY_TYPES:
    raise HTTPException(
      status_code=400,
      detail=f"type must be one of {list(_HISTORY_TYPES)}",
    )

  # 1. Pull swap_history rows
  try:
    swap_rows = json.loads(p.swap_history) if p.swap_history else []
  except (ValueError, TypeError):
    swap_rows = []
  if not isinstance(swap_rows, list):
    swap_rows = []

  swap_entries: list[dict] = []
  for raw in swap_rows:
    entry = _swap_history_to_entry(raw)
    if entry is not None:
      swap_entries.append(entry)

  # 2. Pull LiveEvent rows for the canonical basket event types
  live_rows = (
    db.query(LiveEvent)
    .filter(LiveEvent.portfolio_id == portfolio_id)
    .filter(LiveEvent.event_type.in_(_PHASE2_EVENT_TYPES))
    .order_by(LiveEvent.created_at.desc(), LiveEvent.id.desc())
    .all()
  )
  live_entries: list[dict] = []
  for row in live_rows:
    entry = _live_event_to_entry(row)
    if entry is not None:
      live_entries.append(entry)

  # 2b. VISION post-ship dedup (E1): the WFAS Refresh button (wfo.py
  # save_phase2_rebuild) writes BOTH a swap_history row AND a
  # phase2_selection_changes payload that cold-save Pass A later
  # reconstructs into a LiveEvent. That twin would surface in `/history`
  # twice — once as source_store="swap_history" (actor=user), once as
  # source_store="live_event" (reconstructed=true, actor=cold_save).
  # Drop the reconstructed live_event twin when a same-day swap-side
  # phase2_refresh covers the same added/removed set.
  swap_refresh_keys: set[tuple[str, frozenset, frozenset]] = set()
  for e in swap_entries:
    if e["type"] != "phase2_refresh":
      continue
    ts = e.get("timestamp_iso") or ""
    date_key = ts[:10]
    if not date_key:
      continue
    added_key = frozenset(map(str, e["details"].get("added") or []))
    removed_key = frozenset(map(str, e["details"].get("removed") or []))
    swap_refresh_keys.add((date_key, added_key, removed_key))

  if swap_refresh_keys:
    deduped_live: list[dict] = []
    for e in live_entries:
      if e.get("reconstructed") and e["type"] == "phase2_refresh":
        ts = e.get("timestamp_iso") or ""
        date_key = ts[:10]
        added_key = frozenset(map(str, e["details"].get("added") or []))
        removed_key = frozenset(map(str, e["details"].get("removed") or []))
        if (date_key, added_key, removed_key) in swap_refresh_keys:
          continue
      deduped_live.append(e)
    live_entries = deduped_live

  # 3. Merge + sort newest-first. None timestamps sink to the bottom.
  merged = swap_entries + live_entries
  if type is not None:
    merged = [e for e in merged if e["type"] == type]
  merged.sort(
    key=lambda e: e.get("timestamp_iso") or "",
    reverse=True,
  )

  total = len(merged)
  page = merged[offset : offset + limit]

  return {
    "portfolio_id": portfolio_id,
    "limit": limit,
    "offset": offset,
    "total": total,
    "type_filter": type,
    "entries": page,
  }
