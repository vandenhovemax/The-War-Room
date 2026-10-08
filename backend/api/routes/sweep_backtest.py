"""Sweep backtest routes — run parameter sweeps across multiple strategy configs."""
import copy
import itertools
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.rate_limit import rate_limit
from backend.constants import COMMISSION_PCT
from backend.db.database import get_db
from backend.db.models import BatchBacktestRun, Strategy, StrategyConfig, SweepRun
from backend.utils.logger import get_logger
from backend.utils.sanitize import sanitize_for_json

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SweepParam(BaseModel):
  name: str
  min: float
  max: float
  step: float


class SweepRequest(BaseModel):
  strategy_id: int
  symbols: list[str]
  timeframe: str
  direction_filter: str = "both"
  sweep_params: list[SweepParam]
  sweep_mode: str = "grid"           # "grid" | "one_at_a_time"
  max_combos: int = 0                # 0 = no limit
  # Standard batch settings (mirror BatchBacktestRequest):
  initial_capital: float = 10000.0
  commission_pct: float = COMMISSION_PCT
  slippage_mode: str = "pct"
  slippage_value: float = 0.0
  leverage: int = 1
  min_avg_daily_volume: float = 0.0
  max_avg_daily_volume: float = 0.0
  min_positions: int = 0
  config_id: int | None = None
  strategy_inputs: dict | None = None  # base values for non-swept params
  max_pyramiding: int = 1
  warmup_bars: int = 0
  extra_timeframes: list[str] | None = None
  risk_pct: float = 0.0
  risk_mode: str = "pct"
  risk_value: float = 0.0
  martingale_enabled: bool = False
  martingale_type: str = "anti"
  martingale_multiplier: float = 2.0
  martingale_max_streak: int = 3
  vol_filter_enabled: bool = False
  vol_filter_atr_ceiling: float = 0.0
  vol_filter_atr_length: int = 14
  mode: str = "individual"
  notes: str = ""
  normalized: bool = True
  include_costs: bool = False
  funding_rate_daily_pct: float = 0.0
  spread_pct: float = 0.0
  prefilter_enabled: bool = False
  prefilter_mode: str = "trend"
  prefilter_hurst_min: float = 0.50
  prefilter_ter_percentile: int = 25
  prefilter_hurst_max: float = 0.50
  prefilter_vr_max: float = 1.0
  prefilter_min_crossing_rate: float = 0.05
  min_data_date: str | None = None
  process_orders_on_close: bool = False
  enforce_bybit_limits: bool = False
  expected_live_size: float = 0.0
  max_margin_per_trade: float = 0.0
  margin_filter_tolerance: float = 10.0
  bybit_reject_pct: float = 0.0


# ---------------------------------------------------------------------------
# Sweep-level Bybit filter cache — after the first combo discovers which
# symbols are filtered (size/margin/reject), subsequent combos skip them
# automatically, saving ~1/3 of compute when many symbols fail Bybit limits.
# ---------------------------------------------------------------------------
_sweep_bybit_cache: dict[int, set[str]] = {}


def _learn_filtered_symbols(sweep_id: int, batch_id: int):
  """After a batch completes, extract Bybit-filtered symbols into the sweep cache."""
  from backend.db.database import SessionLocal
  db = SessionLocal()
  try:
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    if not batch or not batch.aggregate_metrics:
      return
    agg = json.loads(batch.aggregate_metrics)
    filtered: set[str] = set()
    for key in ("size_filtered_symbols", "margin_filtered_symbols", "reject_filtered_symbols"):
      filtered.update(agg.get(key, []))
    if filtered:
      prev = len(_sweep_bybit_cache.get(sweep_id, set()))
      _sweep_bybit_cache.setdefault(sweep_id, set()).update(filtered)
      new_total = len(_sweep_bybit_cache[sweep_id])
      if new_total > prev:
        logger.info(
          "Sweep %d: learned %d Bybit-filtered symbols from batch %d (total skip list: %d)",
          sweep_id, new_total - prev, batch_id, new_total,
        )
  finally:
    db.close()


def _make_sweep_runner(sweep_id, batch_id, common_args, common_kwargs, direction_filter):
  """Create a sweep-aware batch runner that skips previously Bybit-filtered symbols.

  IMPORTANT: deep-copy common_kwargs so each runner gets its own isolated
  inputs dict. Without this, all runners can end up sharing the same dict
  reference due to Python's late-binding / fork semantics, causing every
  combo after the first to run with the last combo's parameters.
  """
  from backend.api.routes.batch_backtest import _batch_progress, _run_batch

  # Snapshot the kwargs NOW so the closure is immune to later mutations
  frozen_kwargs = copy.deepcopy(common_kwargs)
  frozen_args = (common_args[0], list(common_args[1]), common_args[2])

  def runner():
    py_src, symbols, tf = frozen_args
    # Log the actual strategy inputs for this combo
    swept_inputs = frozen_kwargs.get("inputs", {})
    logger.info(
      "Sweep %d batch %d: starting with inputs=%s",
      sweep_id, batch_id,
      {k: v for k, v in swept_inputs.items() if not k.startswith("_")},
    )
    skip = _sweep_bybit_cache.get(sweep_id, set())
    if skip:
      original_count = len(symbols)
      symbols_filtered = [s for s in symbols if s not in skip]
      skipped = original_count - len(symbols_filtered)
      if skipped > 0:
        logger.info(
          "Sweep %d batch %d: skipping %d Bybit-filtered symbols (%d → %d)",
          sweep_id, batch_id, skipped, original_count, len(symbols_filtered),
        )
        # Update progress tracking to reflect reduced symbol count
        prog = _batch_progress.get(batch_id)
        if prog:
          prog["total_symbols"] = len(symbols_filtered)
          for sym in skip:
            prog["symbol_statuses"].pop(sym, None)
        # Update DB record
        from backend.db.database import SessionLocal
        db2 = SessionLocal()
        try:
          b = db2.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
          if b:
            b.total_symbols = len(symbols_filtered)
            db2.commit()
        finally:
          db2.close()
      _run_batch(batch_id, py_src, symbols_filtered, tf,
                 direction_filter=direction_filter, **frozen_kwargs)
    else:
      _run_batch(batch_id, py_src, symbols, tf,
                 direction_filter=direction_filter, **frozen_kwargs)

    # After completion, learn which symbols were filtered
    _learn_filtered_symbols(sweep_id, batch_id)

  return runner


# ---------------------------------------------------------------------------
# Combo generation
# ---------------------------------------------------------------------------

def _generate_combos(
  sweep_params: list[SweepParam],
  base_inputs: dict,
  mode: str,
  param_types: dict[str, str] | None = None,
) -> list[dict]:
  """Generate parameter combinations from sweep ranges.

  Grid: cartesian product of all param ranges.
  One-at-a-time: vary one param while keeping others at base value.
  param_types: optional {name: "int"|"float"} — casts values to correct type.
  """
  # Determine which params should be int or bool
  int_params: set[str] = set()
  bool_params: set[str] = set()
  for p in sweep_params:
    declared = (param_types or {}).get(p.name)
    if declared == "bool":
      bool_params.add(p.name)
    elif declared == "int":
      int_params.add(p.name)
    elif declared == "float":
      pass  # explicitly float — keep as float
    # Auto-detect when no type info: if min, max, and step are all whole numbers → int
    elif declared is None and (p.min == int(p.min) and p.max == int(p.max) and p.step == int(p.step)):
      int_params.add(p.name)

  def _cast(name: str, val: float):
    """Cast value to correct type: bool, int, or float."""
    if name in bool_params:
      return bool(round(val))
    if name in int_params:
      return int(round(val))
    return round(val, 6)

  if mode == "one_at_a_time":
    combos: list[dict] = []
    for param in sweep_params:
      val = param.min
      while val <= param.max + 1e-9:
        combo = {**base_inputs, param.name: _cast(param.name, val)}
        combos.append(combo)
        val += param.step
    return combos

  # Grid mode
  ranges: list[list[tuple[str, float | int]]] = []
  for p in sweep_params:
    vals: list[float | int] = []
    v = p.min
    while v <= p.max + 1e-9:
      vals.append(_cast(p.name, v))
      v += p.step
    ranges.append([(p.name, v) for v in vals])

  return [{**base_inputs, **dict(c)} for c in itertools.product(*ranges)]


def _combo_label(combo_values: dict, swept_names: list[str]) -> str:
  """Build a human-readable label like 'Retrace=20, MinBars=3'."""
  parts = []
  for name in swept_names:
    if name in combo_values:
      v = combo_values[name]
      # Format nicely: drop trailing zeros for floats
      if isinstance(v, float) and v == int(v):
        parts.append(f"{name}={int(v)}")
      else:
        parts.append(f"{name}={v}")
  return ", ".join(parts)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/sweep/run")
def start_sweep(
  request: Request,
  req: SweepRequest,
  db: Session = Depends(get_db),
):
  """Start a parameter sweep — generates combos and queues batch backtests."""
  rate_limit(request, max_requests=5, window_seconds=60)

  strategy = db.query(Strategy).filter(Strategy.id == req.strategy_id).first()
  if not strategy:
    raise HTTPException(status_code=404, detail="Strategy not found")
  if not strategy.python_source:
    raise HTTPException(status_code=400, detail="Strategy has no Python code")
  if not req.symbols:
    raise HTTPException(status_code=400, detail="No symbols provided")
  if not req.sweep_params:
    raise HTTPException(status_code=400, detail="No sweep parameters provided")

  # Resolve base inputs
  base_inputs: dict = {}
  if req.config_id:
    config = db.query(StrategyConfig).filter(
      StrategyConfig.id == req.config_id,
      StrategyConfig.strategy_id == req.strategy_id,
    ).first()
    if not config:
      raise HTTPException(status_code=404, detail="Config not found")
    base_inputs = json.loads(config.parameter_values) if config.parameter_values else {}
  elif req.strategy_inputs:
    base_inputs = dict(req.strategy_inputs)

  # Merge strategy defaults so non-swept params have values
  # Also build a type map so sweep values get cast correctly (int vs float)
  strat_defaults = {}
  param_types: dict[str, str] = {}
  if strategy.parameters:
    for p in json.loads(strategy.parameters):
      strat_defaults[p["name"]] = p["default"]
      if p.get("type") in ("int", "float", "bool"):
        param_types[p["name"]] = p["type"]
  base_inputs = {**strat_defaults, **base_inputs}

  # Generate combos — pass param_types so int params stay as int (not float)
  combos = _generate_combos(req.sweep_params, base_inputs, req.sweep_mode, param_types)
  # Log swept values for each combo (diagnostic)
  for ci, cv in enumerate(combos):
    swept_vals = {p.name: cv.get(p.name) for p in req.sweep_params}
    logger.info("Sweep combo %d/%d: %s", ci, len(combos), swept_vals)
  if not combos:
    raise HTTPException(status_code=400, detail="No parameter combinations generated. Check ranges/steps.")
  if req.max_combos > 0 and len(combos) > req.max_combos:
    raise HTTPException(
      status_code=400,
      detail=f"Too many combinations ({len(combos)}). "
             f"Maximum is {req.max_combos}. Reduce ranges or increase step sizes.",
    )

  swept_names = [p.name for p in req.sweep_params]

  # Create sweep run
  sweep = SweepRun(
    strategy_id=req.strategy_id,
    name=f"{strategy.name} — Sweep ({', '.join(swept_names)})",
    sweep_config=json.dumps({
      "params": [p.model_dump() for p in req.sweep_params],
      "mode": req.sweep_mode,
      "base_values": base_inputs,
    }),
    batch_config=json.dumps({
      "symbols": req.symbols,
      "timeframe": req.timeframe,
      "direction_filter": req.direction_filter,
      "initial_capital": req.initial_capital,
      "commission_pct": req.commission_pct,
      "leverage": req.leverage,
      "normalized": req.normalized,
      "include_costs": req.include_costs,
      "mode": req.mode,
    }),
    total_combos=len(combos),
    completed_combos=0,
    status="running",
  )
  db.add(sweep)
  db.commit()
  db.refresh(sweep)
  sweep_id = sweep.id

  logger.info(
    "Sweep %d started: %d combos (%s mode), %d symbols, direction=%s",
    sweep_id, len(combos), req.sweep_mode, len(req.symbols), req.direction_filter,
  )

  # Import batch pipeline functions
  from backend.api.routes.batch_backtest import (
    _batch_progress,
    _enqueue_batch,
  )
  from backend.api.routes.backtest import _strip_backtester_params

  all_batch_ids: list[int] = []

  for combo_idx, combo_inputs in enumerate(combos):
    label = _combo_label(combo_inputs, swept_names)

    # Build the strategy_inputs for this combo (strip backtester params)
    effective_inputs = _strip_backtester_params(combo_inputs)

    # Extract strategy risk reference
    strat_risk_mode = "fixed"
    strat_risk_value = 0.0
    rm = combo_inputs.get("Risk Mode", "")
    if "%" in str(rm) or "equity" in str(rm).lower() or "pct" in str(rm).lower():
      strat_risk_mode = "pct"
      strat_risk_value = float(combo_inputs.get("Risk % of Equity", 0) or 0)
    else:
      strat_risk_value = float(combo_inputs.get("Risk Amount ($)", 0) or 0)

    # Extract pyramiding
    max_pyramiding = req.max_pyramiding
    mct = combo_inputs.get("Max Concurrent Trades")
    if mct is not None:
      max_pyramiding = int(mct)

    # Auto-detect extra timeframes
    extra_tfs = set(req.extra_timeframes or [])
    _TF_MAP = {"1h": "60", "4h": "240", "12h": "720", "1D": "D", "3D": "3D"}
    for param_key in ("RSI Timeframe", "MA Filter Timeframe"):
      tf_label = combo_inputs.get(param_key)
      if tf_label and tf_label != "Current" and tf_label in _TF_MAP:
        extra_tfs.add(_TF_MAP[tf_label])
    resolved_extra_tfs = list(extra_tfs) if extra_tfs else None

    # Normalized mode overrides
    eff_risk_mode = req.risk_mode
    eff_risk_value = req.risk_value
    eff_mart = req.martingale_enabled
    eff_risk_pct = req.risk_pct
    if req.normalized:
      eff_risk_mode = "fixed"
      if eff_risk_value <= 0:
        eff_risk_value = 100.0
      eff_mart = False
      eff_risk_pct = 0.0

    # Enforce include_costs toggle
    eff_commission = req.commission_pct if req.include_costs else 0.0
    eff_slippage = req.slippage_value if req.include_costs else 0.0
    eff_funding = req.funding_rate_daily_pct if req.include_costs else 0.0
    eff_spread = req.spread_pct if req.include_costs else 0.0

    # Build parameters JSON for the batch (includes sweep_combo for identification)
    sweep_combo_values = {name: combo_inputs.get(name) for name in swept_names}
    params_json = json.dumps({
      "initial_capital": req.initial_capital,
      "commission_pct": req.commission_pct,
      "leverage": req.leverage,
      "max_pyramiding": max_pyramiding,
      "strategy_inputs": effective_inputs,
      "mode": req.mode,
      "slippage_mode": req.slippage_mode,
      "slippage_value": req.slippage_value,
      "warmup_bars": req.warmup_bars,
      "min_avg_daily_volume": req.min_avg_daily_volume,
      "max_avg_daily_volume": req.max_avg_daily_volume,
      "min_positions": req.min_positions,
      "risk_mode": eff_risk_mode,
      "risk_value": eff_risk_value,
      "martingale_enabled": eff_mart,
      "martingale_type": req.martingale_type,
      "martingale_multiplier": req.martingale_multiplier,
      "martingale_max_streak": req.martingale_max_streak,
      "vol_filter_enabled": req.vol_filter_enabled,
      "vol_filter_atr_ceiling": req.vol_filter_atr_ceiling,
      "vol_filter_atr_length": req.vol_filter_atr_length,
      "normalized": req.normalized,
      "include_costs": req.include_costs,
      "funding_rate_daily_pct": req.funding_rate_daily_pct,
      "spread_pct": req.spread_pct,
      "process_orders_on_close": req.process_orders_on_close,
      "enforce_bybit_limits": req.enforce_bybit_limits,
      "expected_live_size": req.expected_live_size,
      "max_margin_per_trade": req.max_margin_per_trade,
      "margin_filter_tolerance": req.margin_filter_tolerance,
      "bybit_reject_pct": req.bybit_reject_pct,
      "sweep_combo": sweep_combo_values,
      "sweep_combo_index": combo_idx,
    })

    # Common args for _run_batch
    _common_args = (strategy.python_source, list(req.symbols), req.timeframe)
    _common_kwargs = dict(
      initial_capital=req.initial_capital,
      commission_pct=eff_commission,
      leverage=req.leverage,
      inputs=effective_inputs,
      max_pyramiding=max_pyramiding,
      warmup_bars=req.warmup_bars,
      extra_timeframes=resolved_extra_tfs,
      risk_pct=eff_risk_pct,
      mode=req.mode,
      slippage_mode=req.slippage_mode,
      slippage_value=eff_slippage,
      risk_mode=eff_risk_mode,
      risk_value=eff_risk_value,
      martingale_enabled=eff_mart,
      martingale_type=req.martingale_type,
      martingale_multiplier=req.martingale_multiplier,
      martingale_max_streak=req.martingale_max_streak,
      vol_filter_enabled=req.vol_filter_enabled,
      vol_filter_atr_ceiling=req.vol_filter_atr_ceiling,
      vol_filter_atr_length=req.vol_filter_atr_length,
      strategy_risk_mode=strat_risk_mode,
      strategy_risk_value=strat_risk_value,
      min_positions=req.min_positions,
      funding_rate_daily_pct=eff_funding,
      spread_pct=eff_spread,
      process_orders_on_close=req.process_orders_on_close,
      enforce_bybit_limits=req.enforce_bybit_limits,
      expected_live_size=req.expected_live_size,
      max_margin_per_trade=req.max_margin_per_trade,
      margin_filter_tolerance=req.margin_filter_tolerance,
      bybit_reject_pct=req.bybit_reject_pct,
    )

    # Handle long_plus_short — create two batches per combo
    if req.direction_filter == "long_plus_short":
      for dir_label, dir_filter in [("Long", "long"), ("Short", "short")]:
        batch_name = f"{strategy.name} — Sweep #{combo_idx + 1} ({label}) — {dir_label}"
        b = BatchBacktestRun(
          strategy_id=req.strategy_id,
          timeframe=req.timeframe,
          direction_filter=dir_filter,
          name=batch_name,
          source_name=batch_name,
          config_group=f"Sweep #{sweep_id}",
          notes=req.notes,
          parameters=params_json,
          extra_timeframes=json.dumps(sorted(extra_tfs)) if extra_tfs else "[]",
          total_symbols=len(req.symbols),
          completed_symbols=0,
          status="queued",
          sweep_id=sweep_id,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        all_batch_ids.append(b.id)

        _batch_progress[b.id] = {
          "status": "queued",
          "total_symbols": len(req.symbols),
          "completed_symbols": 0,
          "current_symbol": "",
          "symbol_statuses": {s: "pending" for s in req.symbols},
          "started_at": datetime.now(timezone.utc).isoformat(),
        }
        _enqueue_batch(
          b.id,
          _make_sweep_runner(sweep_id, b.id, _common_args, _common_kwargs, dir_filter),
        )
    else:
      # Single direction
      batch_name = f"{strategy.name} — Sweep #{combo_idx + 1} ({label})"
      b = BatchBacktestRun(
        strategy_id=req.strategy_id,
        timeframe=req.timeframe,
        direction_filter=req.direction_filter,
        name=batch_name,
        source_name=batch_name,
        config_group=f"Sweep #{sweep_id}",
        notes=req.notes,
        parameters=params_json,
        extra_timeframes=json.dumps(sorted(extra_tfs)) if extra_tfs else "[]",
        total_symbols=len(req.symbols),
        completed_symbols=0,
        status="queued",
        sweep_id=sweep_id,
      )
      db.add(b)
      db.commit()
      db.refresh(b)
      all_batch_ids.append(b.id)

      _batch_progress[b.id] = {
        "status": "queued",
        "total_symbols": len(req.symbols),
        "completed_symbols": 0,
        "current_symbol": "",
        "symbol_statuses": {s: "pending" for s in req.symbols},
        "started_at": datetime.now(timezone.utc).isoformat(),
      }
      _enqueue_batch(
        b.id,
        _make_sweep_runner(sweep_id, b.id, _common_args, _common_kwargs, req.direction_filter),
      )

  logger.info("Sweep %d: queued %d batches", sweep_id, len(all_batch_ids))

  return {
    "sweep_id": sweep_id,
    "total_combos": len(combos),
    "batch_ids": all_batch_ids,
    "status": "running",
  }


@router.get("/sweep/progress/{sweep_id}")
def sweep_progress(sweep_id: int, db: Session = Depends(get_db)):
  """Poll sweep progress — aggregates from child batch statuses."""
  sweep = db.query(SweepRun).filter(SweepRun.id == sweep_id).first()
  if not sweep:
    return {"status": "unknown"}

  child_batches = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.sweep_id == sweep_id)
    .order_by(BatchBacktestRun.id)
    .all()
  )

  completed = sum(1 for b in child_batches if b.status == "completed")
  errored = sum(1 for b in child_batches if b.status == "error")
  cancelled = sum(1 for b in child_batches if b.status == "cancelled")
  total = len(child_batches)

  # Determine overall status
  if sweep.status == "interrupted":
    # Already recovered after a restart — keep that status
    status = "interrupted"
  elif cancelled > 0 and completed + errored + cancelled >= total:
    status = "cancelled"
  elif completed + errored >= total and total > 0:
    status = "completed"
    # Update sweep row
    if sweep.status != "completed":
      sweep.status = "completed"
      sweep.completed_combos = sweep.total_combos
      db.commit()
  else:
    # Still "running" in DB — but check if server restarted (no in-memory progress)
    from backend.api.routes.batch_backtest import _batch_progress as bp
    running_batches = [b for b in child_batches if b.status in ("running", "queued")]
    has_memory = any(b.id in bp for b in running_batches)
    if running_batches and not has_memory:
      # Server restarted — recover now
      for b in running_batches:
        b.status = "cancelled"
      sweep.status = "interrupted"
      sweep.completed_combos = completed
      db.commit()
      status = "interrupted"
      logger.info("Auto-recovered stale sweep %d during progress poll", sweep_id)
    else:
      status = "running"

  # Per-combo progress
  from backend.api.routes.batch_backtest import _batch_progress
  combos = []
  for b in child_batches:
    # Try in-memory progress first, fall back to DB
    prog = _batch_progress.get(b.id)
    params = json.loads(b.parameters) if b.parameters else {}
    combos.append({
      "batch_id": b.id,
      "direction": b.direction_filter,
      "status": prog["status"] if prog else b.status,
      "combo_label": b.name or "",
      "combo_index": params.get("sweep_combo_index", 0),
      "completed_symbols": prog["completed_symbols"] if prog else b.completed_symbols,
      "total_symbols": b.total_symbols,
      "current_symbol": prog.get("current_symbol", "") if prog else "",
    })

  return {
    "sweep_id": sweep_id,
    "total_combos": sweep.total_combos,
    "completed": completed,
    "total_batches": total,
    "status": status,
    "combos": combos,
  }


@router.get("/sweep/runs")
def list_sweeps(db: Session = Depends(get_db)):
  """List all saved sweep runs."""
  sweeps = (
    db.query(SweepRun)
    .filter(SweepRun.is_deleted != True)
    .order_by(SweepRun.created_at.desc())
    .limit(100)
    .all()
  )
  return [_sweep_to_dict(s) for s in sweeps]


@router.get("/sweep/runs/{sweep_id}")
def get_sweep_detail(sweep_id: int, db: Session = Depends(get_db)):
  """Get sweep detail with comparison data for all combos."""
  sweep = db.query(SweepRun).filter(SweepRun.id == sweep_id).first()
  if not sweep:
    raise HTTPException(status_code=404, detail="Sweep not found")

  child_batches = (
    db.query(BatchBacktestRun)
    .filter(
      BatchBacktestRun.sweep_id == sweep_id,
      BatchBacktestRun.is_deleted != True,
    )
    .order_by(BatchBacktestRun.id)
    .all()
  )

  # Group batches by combo_index
  combo_map: dict[int, list[dict]] = {}
  combo_values_map: dict[int, dict] = {}

  for b in child_batches:
    params = json.loads(b.parameters) if b.parameters else {}
    combo_idx = params.get("sweep_combo_index", 0)
    sweep_combo = params.get("sweep_combo", {})
    combo_values_map[combo_idx] = sweep_combo

    agg = {}
    batch_grade = None
    batch_grade_score = None
    if b.aggregate_metrics:
      try:
        agg = sanitize_for_json(json.loads(b.aggregate_metrics))
        batch_grade = agg.get("batch_grade")
        batch_grade_score = agg.get("batch_grade_score")
      except Exception as _exc:
        logger.debug("swallowed: %s", _exc)

    batch_info = {
      "batch_id": b.id,
      "direction": b.direction_filter,
      "status": b.status,
      "aggregate_metrics": agg,
      "batch_grade": batch_grade,
      "batch_grade_score": batch_grade_score,
      "name": b.name,
      "total_symbols": b.total_symbols,
    }
    combo_map.setdefault(combo_idx, []).append(batch_info)

  # Build sorted combo list
  combos = []
  for idx in sorted(combo_map.keys()):
    combos.append({
      "combo_index": idx,
      "combo_values": combo_values_map.get(idx, {}),
      "batches": combo_map[idx],
    })

  return {
    "sweep": _sweep_to_dict(sweep),
    "combos": combos,
  }


@router.post("/sweep/runs/{sweep_id}/cancel")
def cancel_sweep(sweep_id: int, db: Session = Depends(get_db)):
  """Cancel all running/queued batches in a sweep."""
  sweep = db.query(SweepRun).filter(SweepRun.id == sweep_id).first()
  if not sweep:
    raise HTTPException(status_code=404, detail="Sweep not found")

  child_batches = (
    db.query(BatchBacktestRun)
    .filter(
      BatchBacktestRun.sweep_id == sweep_id,
      BatchBacktestRun.status.in_(["running", "queued", "downloading_extra_tfs"]),
    )
    .all()
  )

  from backend.api.routes.batch_backtest import (
    _batch_progress,
    _batch_queue,
    _cancelled_batches,
    _queue_lock,
    _sync_progress_to_db,
  )

  cancelled_ids = []
  for b in child_batches:
    _cancelled_batches.add(b.id)
    prog = _batch_progress.get(b.id)
    if prog:
      prog["status"] = "cancelled"
    b.status = "cancelled"
    cancelled_ids.append(b.id)

  # Remove from queue
  with _queue_lock:
    cancel_set = set(cancelled_ids)
    items = [item for item in _batch_queue if item.batch_id not in cancel_set]
    _batch_queue.clear()
    _batch_queue.extend(items)

  # Count how many combos completed — use "interrupted" if some data exists
  completed_count = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.sweep_id == sweep_id, BatchBacktestRun.status == "completed")
    .count()
  )
  sweep.status = "interrupted" if completed_count > 0 else "cancelled"
  sweep.completed_combos = completed_count
  db.commit()

  for bid in cancelled_ids:
    _sync_progress_to_db(bid)

  logger.info("Sweep %d stopped: %d batches cancelled, %d completed (status=%s)",
              sweep_id, len(cancelled_ids), completed_count, sweep.status)
  return {"cancelled": True, "sweep_id": sweep_id, "batches_cancelled": len(cancelled_ids),
          "completed_count": completed_count, "status": sweep.status}


@router.post("/sweep/runs/{sweep_id}/resume")
def resume_sweep(
  request: Request,
  sweep_id: int,
  db: Session = Depends(get_db),
):
  """Resume an interrupted/cancelled sweep — re-queues only the missing combos."""
  rate_limit(request, max_requests=5, window_seconds=60)

  sweep = db.query(SweepRun).filter(SweepRun.id == sweep_id).first()
  if not sweep:
    raise HTTPException(status_code=404, detail="Sweep not found")
  if sweep.status not in ("interrupted", "cancelled"):
    raise HTTPException(status_code=400, detail=f"Cannot resume sweep with status '{sweep.status}'")

  strategy = db.query(Strategy).filter(Strategy.id == sweep.strategy_id).first()
  if not strategy or not strategy.python_source:
    raise HTTPException(status_code=400, detail="Strategy missing or has no code")

  # Parse stored configs
  sweep_config = json.loads(sweep.sweep_config) if sweep.sweep_config else {}
  batch_config = json.loads(sweep.batch_config) if sweep.batch_config else {}
  sweep_params_raw = sweep_config.get("params", [])
  sweep_mode = sweep_config.get("mode", "grid")
  base_values = sweep_config.get("base_values", {})

  if not sweep_params_raw:
    raise HTTPException(status_code=400, detail="Sweep config missing parameter definitions")

  # Rebuild SweepParam objects
  sweep_param_objs = [SweepParam(**p) for p in sweep_params_raw]
  swept_names = [p.name for p in sweep_param_objs]

  # Build param type map from strategy definitions
  param_types: dict[str, str] = {}
  if strategy.parameters:
    for p in json.loads(strategy.parameters):
      if p.get("type") in ("int", "float", "bool"):
        param_types[p["name"]] = p["type"]

  # Regenerate full combo list (same order as original)
  combos = _generate_combos(sweep_param_objs, base_values, sweep_mode, param_types)
  if not combos:
    raise HTTPException(status_code=400, detail="Could not regenerate combos from stored config")

  # Find which combo indices already have completed batches
  completed_indices: set[int] = set()
  child_batches = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.sweep_id == sweep_id)
    .all()
  )
  for b in child_batches:
    if b.status == "completed":
      params = json.loads(b.parameters) if b.parameters else {}
      completed_indices.add(params.get("sweep_combo_index", -1))
      # Pre-populate Bybit filter cache from completed batches
      if b.aggregate_metrics:
        agg = json.loads(b.aggregate_metrics)
        for key in ("size_filtered_symbols", "margin_filtered_symbols", "reject_filtered_symbols"):
          syms = agg.get(key, [])
          if syms:
            _sweep_bybit_cache.setdefault(sweep_id, set()).update(syms)

  if _sweep_bybit_cache.get(sweep_id):
    logger.info(
      "Sweep %d resume: pre-loaded %d Bybit-filtered symbols from completed batches",
      sweep_id, len(_sweep_bybit_cache[sweep_id]),
    )

  # Delete cancelled/error child batches (they have no data)
  from backend.db.models import BacktestRun
  deleted_count = 0
  for b in child_batches:
    if b.status in ("cancelled", "error", "queued"):
      # Delete any child runs too (shouldn't exist but be safe)
      db.query(BacktestRun).filter(BacktestRun.batch_id == b.id).delete()
      db.delete(b)
      deleted_count += 1
  db.commit()

  # Get batch-level settings from a completed batch (has everything)
  ref_params: dict = {}
  for b in child_batches:
    if b.status == "completed" and b.parameters:
      ref_params = json.loads(b.parameters)
      break

  if not ref_params and completed_indices:
    raise HTTPException(status_code=500, detail="Completed batches found but no parameters to reference")

  # Determine missing combos
  missing = [(idx, c) for idx, c in enumerate(combos) if idx not in completed_indices]
  if not missing:
    sweep.status = "completed"
    sweep.completed_combos = sweep.total_combos
    db.commit()
    return {"sweep_id": sweep_id, "status": "completed", "message": "All combos already completed", "resumed": 0}

  # Import batch pipeline
  from backend.api.routes.batch_backtest import (
    _batch_progress,
    _enqueue_batch,
  )
  from backend.api.routes.backtest import _strip_backtester_params

  # Resolve settings — prefer ref_params (from completed batch), fall back to batch_config
  symbols = batch_config.get("symbols", [])
  timeframe = batch_config.get("timeframe", "30")
  direction_filter = batch_config.get("direction_filter", "both")
  initial_capital = ref_params.get("initial_capital", batch_config.get("initial_capital", 10000.0))
  commission_pct = ref_params.get("commission_pct", 0.0)
  leverage = ref_params.get("leverage", batch_config.get("leverage", 1))
  mode = ref_params.get("mode", batch_config.get("mode", "individual"))
  include_costs = ref_params.get("include_costs", False)

  all_batch_ids: list[int] = []

  for combo_idx, combo_inputs in missing:
    label = _combo_label(combo_inputs, swept_names)
    effective_inputs = _strip_backtester_params(combo_inputs)

    # Risk
    strat_risk_mode = "fixed"
    strat_risk_value = 0.0
    rm = combo_inputs.get("Risk Mode", "")
    if "%" in str(rm) or "equity" in str(rm).lower() or "pct" in str(rm).lower():
      strat_risk_mode = "pct"
      strat_risk_value = float(combo_inputs.get("Risk % of Equity", 0) or 0)
    else:
      strat_risk_value = float(combo_inputs.get("Risk Amount ($)", 0) or 0)

    max_pyramiding = int(combo_inputs.get("Max Concurrent Trades", ref_params.get("max_pyramiding", 1)))

    # Extra TFs
    extra_tfs: set[str] = set()
    _TF_MAP = {"1h": "60", "4h": "240", "12h": "720", "1D": "D", "3D": "3D"}
    for param_key in ("RSI Timeframe", "MA Filter Timeframe"):
      tf_label = combo_inputs.get(param_key)
      if tf_label and tf_label != "Current" and tf_label in _TF_MAP:
        extra_tfs.add(_TF_MAP[tf_label])
    resolved_extra_tfs = list(extra_tfs) if extra_tfs else None

    # Use same settings as completed batches
    eff_risk_mode = ref_params.get("risk_mode", "fixed")
    eff_risk_value = ref_params.get("risk_value", 100.0)
    eff_mart = ref_params.get("martingale_enabled", False)
    eff_risk_pct = 0.0
    eff_commission = commission_pct if include_costs else 0.0
    eff_slippage = ref_params.get("slippage_value", 0.0) if include_costs else 0.0
    eff_funding = ref_params.get("funding_rate_daily_pct", 0.0) if include_costs else 0.0
    eff_spread = ref_params.get("spread_pct", 0.0) if include_costs else 0.0

    sweep_combo_values = {name: combo_inputs.get(name) for name in swept_names}
    params_json = json.dumps({
      **{k: v for k, v in ref_params.items() if k not in ("sweep_combo", "sweep_combo_index", "strategy_inputs")},
      "strategy_inputs": effective_inputs,
      "sweep_combo": sweep_combo_values,
      "sweep_combo_index": combo_idx,
    })

    _common_args = (strategy.python_source, list(symbols), timeframe)
    _common_kwargs = dict(
      initial_capital=initial_capital,
      commission_pct=eff_commission,
      leverage=leverage,
      inputs=effective_inputs,
      max_pyramiding=max_pyramiding,
      warmup_bars=ref_params.get("warmup_bars", 0),
      extra_timeframes=resolved_extra_tfs,
      risk_pct=eff_risk_pct,
      mode=mode,
      slippage_mode=ref_params.get("slippage_mode", "pct"),
      slippage_value=eff_slippage,
      risk_mode=eff_risk_mode,
      risk_value=eff_risk_value,
      martingale_enabled=eff_mart,
      martingale_type=ref_params.get("martingale_type", "anti"),
      martingale_multiplier=ref_params.get("martingale_multiplier", 2.0),
      martingale_max_streak=ref_params.get("martingale_max_streak", 3),
      vol_filter_enabled=ref_params.get("vol_filter_enabled", False),
      vol_filter_atr_ceiling=ref_params.get("vol_filter_atr_ceiling", 0.0),
      vol_filter_atr_length=ref_params.get("vol_filter_atr_length", 14),
      strategy_risk_mode=strat_risk_mode,
      strategy_risk_value=strat_risk_value,
      min_positions=ref_params.get("min_positions", 0),
      funding_rate_daily_pct=eff_funding,
      spread_pct=eff_spread,
      process_orders_on_close=ref_params.get("process_orders_on_close", False),
      enforce_bybit_limits=ref_params.get("enforce_bybit_limits", False),
      expected_live_size=ref_params.get("expected_live_size", 0.0),
      max_margin_per_trade=ref_params.get("max_margin_per_trade", 0.0),
      margin_filter_tolerance=ref_params.get("margin_filter_tolerance", 10.0),
      bybit_reject_pct=ref_params.get("bybit_reject_pct", 0.0),
    )

    if direction_filter == "long_plus_short":
      for dir_label, dir_val in [("Long", "long"), ("Short", "short")]:
        batch_name = f"{strategy.name} — Sweep #{combo_idx + 1} ({label}) — {dir_label}"
        b = BatchBacktestRun(
          strategy_id=sweep.strategy_id,
          timeframe=timeframe,
          direction_filter=dir_val,
          name=batch_name,
          source_name=batch_name,
          config_group=f"Sweep #{sweep_id}",
          parameters=params_json,
          extra_timeframes=json.dumps(sorted(extra_tfs)) if extra_tfs else "[]",
          total_symbols=len(symbols),
          completed_symbols=0,
          status="queued",
          sweep_id=sweep_id,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        all_batch_ids.append(b.id)

        _batch_progress[b.id] = {
          "status": "queued",
          "total_symbols": len(symbols),
          "completed_symbols": 0,
          "current_symbol": "",
          "symbol_statuses": {s: "pending" for s in symbols},
          "started_at": datetime.now(timezone.utc).isoformat(),
        }
        _enqueue_batch(
          b.id,
          _make_sweep_runner(sweep_id, b.id, _common_args, _common_kwargs, dir_val),
        )
    else:
      batch_name = f"{strategy.name} — Sweep #{combo_idx + 1} ({label})"
      b = BatchBacktestRun(
        strategy_id=sweep.strategy_id,
        timeframe=timeframe,
        direction_filter=direction_filter,
        name=batch_name,
        source_name=batch_name,
        config_group=f"Sweep #{sweep_id}",
        parameters=params_json,
        extra_timeframes=json.dumps(sorted(extra_tfs)) if extra_tfs else "[]",
        total_symbols=len(symbols),
        completed_symbols=0,
        status="queued",
        sweep_id=sweep_id,
      )
      db.add(b)
      db.commit()
      db.refresh(b)
      all_batch_ids.append(b.id)

      _batch_progress[b.id] = {
        "status": "queued",
        "total_symbols": len(symbols),
        "completed_symbols": 0,
        "current_symbol": "",
        "symbol_statuses": {s: "pending" for s in symbols},
        "started_at": datetime.now(timezone.utc).isoformat(),
      }
      _enqueue_batch(
        b.id,
        _make_sweep_runner(sweep_id, b.id, _common_args, _common_kwargs, direction_filter),
      )

  # Update sweep status
  sweep.status = "running"
  db.commit()

  logger.info(
    "Sweep %d resumed: %d combos already done, %d to go (%d batches queued), %d stale batches cleaned",
    sweep_id, len(completed_indices), len(missing), len(all_batch_ids), deleted_count,
  )

  return {
    "sweep_id": sweep_id,
    "already_completed": len(completed_indices),
    "remaining": len(missing),
    "batch_ids": all_batch_ids,
    "status": "running",
  }


@router.delete("/sweep/runs/{sweep_id}")
def delete_sweep(sweep_id: int, db: Session = Depends(get_db)):
  """Soft-delete a sweep and all its child batches."""
  sweep = db.query(SweepRun).filter(SweepRun.id == sweep_id).first()
  if not sweep:
    raise HTTPException(status_code=404, detail="Sweep not found")

  from backend.api.routes.batch_backtest import _find_portfolios_using_batch
  from backend.db.models import BacktestRun

  now = datetime.now(timezone.utc)

  # Check if any child batch is used by a portfolio
  child_batches = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.sweep_id == sweep_id)
    .all()
  )
  blocked = []
  for b in child_batches:
    refs = _find_portfolios_using_batch(db, b.id)
    if refs:
      names = ", ".join(p["name"] for p in refs)
      blocked.append(f"Batch #{b.id} used by: {names}")

  if blocked:
    raise HTTPException(
      status_code=409,
      detail=f"Cannot delete sweep — some batches are used by portfolios: {'; '.join(blocked)}",
    )

  # Soft-delete sweep + children
  sweep.is_deleted = True
  sweep.deleted_at = now
  total_children = 0
  for b in child_batches:
    b.is_deleted = True
    b.deleted_at = now
    total_children += 1
    # Also soft-delete child backtest runs
    child_runs = db.query(BacktestRun).filter(BacktestRun.batch_id == b.id).all()
    for run in child_runs:
      run.is_deleted = True
      run.deleted_at = now

  db.commit()
  _sweep_bybit_cache.pop(sweep_id, None)
  logger.info("Sweep %d deleted: %d child batches", sweep_id, total_children)
  return {"deleted": True, "sweep_id": sweep_id, "batches_deleted": total_children}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sweep_to_dict(s: SweepRun) -> dict:
  sweep_config = {}
  if s.sweep_config:
    try:
      sweep_config = json.loads(s.sweep_config)
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)
  batch_config = {}
  if s.batch_config:
    try:
      batch_config = json.loads(s.batch_config)
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)
  return {
    "id": s.id,
    "strategy_id": s.strategy_id,
    "name": s.name,
    "sweep_config": sweep_config,
    "batch_config": batch_config,
    "total_combos": s.total_combos,
    "completed_combos": s.completed_combos,
    "status": s.status,
    "created_at": (s.created_at.isoformat() + "Z") if s.created_at else None,
  }
