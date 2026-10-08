"""Batch backtest routes — run one strategy across multiple symbols."""
import hashlib
import json
import threading
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import bindparam, func as sa_func, text as sa_text
from sqlalchemy.orm import Session

from backend.api.rate_limit import rate_limit
from backend.backtester.trade_storage import store_trades
from backend.constants import COMMISSION_PCT
from backend.db.database import get_db
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  BatchBacktestRun,
  OHLCVData,
  Strategy,
  StrategyConfig,
)
from backend.utils.logger import get_logger
from backend.utils.sanitize import sanitize_for_json

logger = get_logger(__name__)
router = APIRouter()

# In-memory batch progress
_batch_progress: dict[int, dict] = {}

# Cancellation tracking
_cancelled_batches: set[int] = set()

# Request dedup — hash-based protection against duplicate batch submissions.
# Maps request_hash → {"batch_ids": [...], "ts": monotonic_time}
_recent_request_hashes: dict[str, dict] = {}
_DEDUP_HASH_TTL = 30  # seconds — how long to remember a request hash
_dedup_lock = threading.Lock()  # prevents race condition when two identical requests arrive simultaneously

# Per-symbol candle cache — avoids redundant DB loads across queued batches.
# Each (symbol, timeframe) pair is cached individually with its own TTL, so
# batches with overlapping-but-not-identical symbol lists still benefit.
import time as _time
_CACHE_TTL_SECONDS = 3600  # 1 hour per symbol (refreshed on each access)

# (symbol, timeframe) → {"df": DataFrame, "ts": monotonic_time}
_candle_cache_primary: dict[tuple[str, str], dict] = {}
# (symbol, timeframe, extra_tf) → {"df": DataFrame, "ts": monotonic_time}
_candle_cache_extra: dict[tuple[str, str, str], dict] = {}
_candle_cache_lock = threading.Lock()

# Bybit instrument specs cache — avoids redundant API calls across queued batches.
_instrument_specs_cache: dict[str, dict] | None = None
_instrument_specs_ts: float = 0.0
_INSTRUMENT_SPECS_TTL = 1800  # 30 minutes


# ---------------------------------------------------------------------------
# Sequential batch queue — processes one batch at a time so they don't
# compete for CPU / memory / SQLite writes.
# ---------------------------------------------------------------------------

@dataclass
class _QueueItem:
  batch_id: int
  run_fn: Callable[[], None]


_batch_queue: deque[_QueueItem] = deque()
_queue_lock = threading.Lock()
_queue_event = threading.Event()
_worker_started = False


def _batch_worker():
  """Background worker that processes batches one at a time."""
  logger.info("Batch queue worker started")
  while True:
    _queue_event.wait()
    _queue_event.clear()

    while True:
      with _queue_lock:
        if not _batch_queue:
          break
        item = _batch_queue.popleft()

      batch_id = item.batch_id

      # Skip if cancelled while queued
      if _is_cancelled(batch_id):
        prog = _batch_progress.get(batch_id)
        if prog:
          prog["status"] = "cancelled"
        _sync_progress_to_db(batch_id)
        _cancelled_batches.discard(batch_id)
        logger.info("Batch %d was cancelled while queued — skipping", batch_id)
        continue

      # Transition queued → running
      prog = _batch_progress.get(batch_id)
      if prog:
        prog["status"] = "running"
      _sync_progress_to_db(batch_id)

      from backend.db.database import SessionLocal
      db_sess = SessionLocal()
      try:
        batch = db_sess.query(BatchBacktestRun).filter(
          BatchBacktestRun.id == batch_id
        ).first()
        if batch:
          batch.status = "running"
          db_sess.commit()
      finally:
        db_sess.close()

      logger.info("Batch %d starting (queue has %d remaining)",
                   batch_id, len(_batch_queue))

      try:
        item.run_fn()
      except Exception as e:
        logger.error("Batch %d worker error: %s\n%s", batch_id, e, traceback.format_exc())
        # Mark as error so the frontend stops polling and the queue advances
        prog = _batch_progress.get(batch_id)
        if prog:
          prog["status"] = "error"
          prog["error"] = str(e)
        _sync_progress_to_db(batch_id)
        from backend.db.database import SessionLocal
        err_sess = SessionLocal()
        try:
          err_batch = err_sess.query(BatchBacktestRun).filter(
            BatchBacktestRun.id == batch_id
          ).first()
          if err_batch:
            err_batch.status = "error"
            err_sess.commit()
        finally:
          err_sess.close()

      logger.info("Batch %d finished", batch_id)

    # Queue empty — keep cache alive for a few minutes so the next
    # batch submission (same symbols/TF, different config) can reuse it.
    # The cache will be lazily evicted when a new batch loads different data.


def _ensure_worker_started():
  """Start the worker thread if not already running (lazy, thread-safe)."""
  global _worker_started
  if _worker_started:
    return
  with _queue_lock:
    if _worker_started:
      return
    t = threading.Thread(target=_batch_worker, daemon=True, name="batch-queue-worker")
    t.start()
    _worker_started = True
    logger.info("Batch queue worker thread launched")


def _enqueue_batch(batch_id: int, run_fn: Callable[[], None]):
  """Add a batch to the queue and wake the worker."""
  _ensure_worker_started()
  with _queue_lock:
    _batch_queue.append(_QueueItem(batch_id=batch_id, run_fn=run_fn))
  _queue_event.set()
  logger.info("Batch %d enqueued (queue depth: %d)", batch_id, len(_batch_queue))


def _is_cancelled(batch_id: int) -> bool:
  """Check if a batch has been requested to cancel."""
  return batch_id in _cancelled_batches


def _safe_progress(batch_id: int) -> dict:
  """Return the progress dict for batch_id, or a throwaway dict if batch was deleted.

  Background tasks may still be running when a user deletes a batch.
  This prevents KeyError crashes by returning a disposable dict whose
  mutations are silently discarded.
  """
  prog = _batch_progress.get(batch_id)
  if prog is not None:
    return prog
  return {"status": "deleted", "symbol_statuses": {}, "completed_symbols": 0, "current_symbol": ""}


def _sync_progress_to_db(batch_id: int) -> None:
  """Persist current in-memory progress to batch_backtest_runs.progress_detail.

  Called at key checkpoints (status changes, symbol completions) so that
  progress survives a backend restart.
  """
  prog = _batch_progress.get(batch_id)
  if not prog:
    return
  from backend.db.database import SessionLocal
  db = SessionLocal()
  try:
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    if batch:
      batch.progress_detail = json.dumps(prog)
      db.commit()
  except Exception as e:
    logger.debug("Failed to sync progress for batch %d: %s", batch_id, e)
  finally:
    db.close()


class BatchBacktestRequest(BaseModel):
  strategy_id: int
  symbols: list[str]
  timeframe: str
  direction_filter: str = "both"
  initial_capital: float = 10000.0
  commission_pct: float = COMMISSION_PCT
  slippage_mode: str = "pct"     # "pct" | "pips" | "risk_pct"
  slippage_value: float = 0.0    # 0 = use default
  leverage: int = 1
  min_avg_daily_volume: float = 0.0
  max_avg_daily_volume: float = 0.0  # upper ADV cap; 0 = no limit
  min_positions: int = 0  # post-backtest filter: skip symbols with fewer trades
  config_id: int | None = None
  strategy_inputs: dict | None = None
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
  mode: str = "individual"  # "individual" or "portfolio"
  name: str | None = None    # optional batch name (auto-generated if absent)
  notes: str = ""            # user's personal note
  normalized: bool = True    # normalized mode: force fixed $ risk, no martingale
  include_costs: bool = False  # OFF by default — pure strategy validation
  funding_rate_daily_pct: float = 0.0  # daily funding rate for perps (0 = disabled)
  spread_pct: float = 0.0             # bid-ask spread % (0 = disabled)
  # L0 Pre-filter: skip assets that don't pass structural quality checks
  prefilter_enabled: bool = False
  prefilter_mode: str = "trend"  # "trend" or "meanrev"
  prefilter_hurst_min: float = 0.50   # trend mode: min Hurst
  prefilter_ter_percentile: int = 25  # trend mode: TER percentile cutoff
  prefilter_hurst_max: float = 0.50   # meanrev mode: max Hurst
  prefilter_vr_max: float = 1.0       # meanrev mode: max VR
  prefilter_min_crossing_rate: float = 0.05  # meanrev mode: min crossing rate
  min_data_date: str | None = None  # skip symbols whose earliest candle is after this date (YYYY-MM-DD)
  process_orders_on_close: bool = False  # True = fill entries at bar's close
  enforce_bybit_limits: bool = False     # True = fetch & enforce min/max qty + lot_size
  expected_live_size: float = 0.0       # $ risk per trade in live — post-filter: exclude assets where Bybit min_qty fails at this size
  max_margin_per_trade: float = 0.0     # $ cap — drop asset if margin per trade exceeds this (0 = disabled)
  margin_filter_tolerance: float = 10.0  # % of entries allowed to exceed margin cap before dropping
  bybit_reject_pct: float = 0.0          # % of trades rejected (below min qty) before dropping symbol (0 = disabled)


@router.post("/batch/run")
def start_batch(
  request: Request,
  req: BatchBacktestRequest,
  db: Session = Depends(get_db),
):
  """Start a batch backtest across multiple symbols."""
  rate_limit(request, max_requests=10, window_seconds=60)
  strategy = db.query(Strategy).filter(Strategy.id == req.strategy_id).first()
  if not strategy:
    raise HTTPException(status_code=404, detail="Strategy not found")
  if not strategy.python_source:
    raise HTTPException(status_code=400, detail="Strategy has no Python code")
  if not req.symbols:
    raise HTTPException(status_code=400, detail="No symbols provided")
  if req.direction_filter not in ("long", "short", "both", "long_plus_short"):
    raise HTTPException(status_code=400, detail="direction_filter must be long/short/both/long_plus_short")

  # Normalized mode: force fixed $ risk, disable martingale/compounding
  if req.normalized:
    req.risk_mode = "fixed"
    if req.risk_value <= 0:
      req.risk_value = 100.0
    req.martingale_enabled = False
    req.risk_pct = 0.0
    logger.info("Normalized mode: fixed $%.0f risk, martingale disabled", req.risk_value)

  # Resolve strategy inputs
  inputs: dict = {}
  if req.config_id:
    config = db.query(StrategyConfig).filter(
      StrategyConfig.id == req.config_id,
      StrategyConfig.strategy_id == req.strategy_id,
    ).first()
    if not config:
      raise HTTPException(status_code=404, detail="Config not found")
    inputs = json.loads(config.parameter_values) if config.parameter_values else {}
  elif req.strategy_inputs:
    inputs = req.strategy_inputs

  # Extract strategy's risk reference from effective inputs (defaults + config
  # overrides) BEFORE stripping. This lets the engine know what risk the
  # strategy will actually use, so it can override correctly.
  strat_defaults = {}
  if strategy.parameters:
    for p in json.loads(strategy.parameters):
      strat_defaults[p["name"]] = p["default"]
  # Merge defaults with config/UI overrides (same as backtest.py)
  effective_inputs = {**strat_defaults, **inputs}
  strat_risk_mode = "fixed"
  strat_risk_value = 0.0
  rm = effective_inputs.get("Risk Mode", "")
  if "%" in str(rm) or "equity" in str(rm).lower() or "pct" in str(rm).lower():
    strat_risk_mode = "pct"
    strat_risk_value = float(effective_inputs.get("Risk % of Equity", 0) or 0)
  else:
    strat_risk_value = float(effective_inputs.get("Risk Amount ($)", 0) or 0)

  # Strip backtester-level params from direct UI inputs so the strategy
  # uses its baked-in defaults. DON'T strip when using a daughter config —
  # config values are intentional overrides that should pass through.
  if not req.config_id:
    from backend.api.routes.backtest import _strip_backtester_params
    inputs = _strip_backtester_params(inputs)

  # Extract pyramiding from strategy inputs if present, else use UI value
  max_pyramiding = req.max_pyramiding
  mct = inputs.get("Max Concurrent Trades")
  if mct is not None:
    max_pyramiding = int(mct)

  # Filter symbols by ADV range (min and/or max) if specified
  # ADV is a daily liquidity metric — query ALL available timeframes (not just
  # the backtest TF) so that symbols with data in any TF get a proper ADV value.
  symbols = list(req.symbols)
  skipped_adv: list[str] = []
  if req.min_avg_daily_volume > 0 or req.max_avg_daily_volume > 0:
    from backend.db.database import ohlcv_router
    adv_map: dict[str, float] = {}
    # Prefer the backtest TF first, then fall back to all others
    tf_list = [req.timeframe] + [t for t in ohlcv_router.list_timeframes() if t != req.timeframe]
    for tf in tf_list:
      try:
        _adv_db = ohlcv_router.get_session(tf)
      except Exception:
        continue
      try:
        # Only query symbols we don't have ADV for yet
        missing = [s for s in symbols if s not in adv_map]
        if not missing:
          break
        adv_rows = (
          _adv_db.query(
            OHLCVData.symbol,
            sa_func.sum(OHLCVData.close * OHLCVData.volume).label("turnover"),
            sa_func.min(OHLCVData.timestamp).label("earliest"),
            sa_func.max(OHLCVData.timestamp).label("latest"),
          )
          .filter(OHLCVData.symbol.in_(missing), OHLCVData.timeframe == tf)
          .group_by(OHLCVData.symbol)
          .all()
        )
        for row in adv_rows:
          if row.turnover and row.earliest and row.latest:
            days = max((row.latest - row.earliest).days + 1, 1)
            val = float(row.turnover) / days
            # Keep best ADV per symbol across timeframes
            if val > adv_map.get(row.symbol, 0):
              adv_map[row.symbol] = val
      finally:
        _adv_db.close()
    kept: list[str] = []
    for s in symbols:
      adv = adv_map.get(s, 0)
      above_min = adv >= req.min_avg_daily_volume if req.min_avg_daily_volume > 0 else True
      below_max = adv <= req.max_avg_daily_volume if req.max_avg_daily_volume > 0 else True
      if above_min and below_max:
        kept.append(s)
      else:
        skipped_adv.append(s)
        reason = "below minimum" if not above_min else "above maximum"
        logger.info("Skipping %s — ADV $%.0f %s", s, adv, reason)
    symbols = kept
    if not symbols:
      raise HTTPException(
        status_code=400,
        detail=f"All {len(req.symbols)} symbols filtered out by ADV range "
               f"(min=${req.min_avg_daily_volume:,.0f}, max=${req.max_avg_daily_volume:,.0f})",
      )

  # Filter symbols by earliest data date — skip symbols listed after the cutoff
  skipped_data_date: list[str] = []
  if req.min_data_date and symbols:
    from datetime import datetime as _dt
    cutoff = _dt.strptime(req.min_data_date, "%Y-%m-%d")
    from backend.db.database import ohlcv_router
    _earliest_db = ohlcv_router.get_session(req.timeframe)
    try:
      earliest_rows = (
        _earliest_db.query(
          OHLCVData.symbol,
          sa_func.min(OHLCVData.timestamp).label("earliest"),
        )
        .filter(OHLCVData.symbol.in_(symbols), OHLCVData.timeframe == req.timeframe)
        .group_by(OHLCVData.symbol)
        .all()
      )
    finally:
      _earliest_db.close()
    earliest_map: dict[str, datetime] = {}
    for row in earliest_rows:
      if row.earliest:
        earliest_map[row.symbol] = row.earliest
    kept_date: list[str] = []
    for s in symbols:
      earliest = earliest_map.get(s)
      if earliest is None:
        skipped_data_date.append(s)
        logger.info("Skipping %s — no candle data found", s)
      elif earliest > cutoff:
        skipped_data_date.append(s)
        logger.info("Skipping %s — earliest data %s is after cutoff %s", s, earliest.strftime("%Y-%m-%d"), req.min_data_date)
      else:
        kept_date.append(s)
    symbols = kept_date
    if not symbols:
      raise HTTPException(
        status_code=400,
        detail=f"All symbols filtered out by min data date ({req.min_data_date}). "
               f"No symbol has data starting before that date.",
      )

  # L0 Pre-filter: skip assets that don't pass structural quality checks
  prefiltered: list[str] = []
  if req.prefilter_enabled and symbols:
    import numpy as np
    logger.info("L0 pre-filter enabled (mode=%s), scanning %d symbols", req.prefilter_mode, len(symbols))

    # Compute TER values for percentile calculation (trend mode)
    # Single batched query across all symbols to avoid N round-trips (B23).
    from backend.db.database import ohlcv_router
    _pf_db = ohlcv_router.get_session(req.timeframe)
    all_metrics: list[tuple[str, dict]] = []
    try:
      stmt = sa_text(
        "SELECT symbol, close FROM ohlcv_data "
        "WHERE symbol IN :syms AND timeframe = :tf "
        "ORDER BY symbol, timestamp"
      ).bindparams(bindparam("syms", expanding=True))
      rows = _pf_db.execute(stmt, {"syms": list(symbols), "tf": req.timeframe}).fetchall()
      by_sym: dict[str, list[float]] = {}
      for sym_val, close_val in rows:
        by_sym.setdefault(sym_val, []).append(close_val)
      for sym in symbols:
        closes = by_sym.get(sym, [])
        if len(closes) < 50:
          prefiltered.append(sym)
          continue
        close_prices = np.array(closes, dtype=float)
        metrics = _compute_structural_metrics_from_prices(close_prices)
        if not metrics:
          prefiltered.append(sym)
          continue
        all_metrics.append((sym, metrics))
    finally:
      _pf_db.close()

    # Compute TER percentile threshold for trend mode
    ter_threshold = 0.0
    if req.prefilter_mode == "trend" and all_metrics:
      ter_values = sorted(m["TER"] for _, m in all_metrics if m.get("TER", 0) > 0)
      if ter_values:
        idx = min(int(len(ter_values) * req.prefilter_ter_percentile / 100), len(ter_values) - 1)
        ter_threshold = ter_values[idx]

    kept_symbols: list[str] = []
    for sym, m in all_metrics:
      passes = True
      if req.prefilter_mode == "trend":
        if m.get("Hurst", 0) < req.prefilter_hurst_min:
          passes = False
        elif m.get("TER", 0) < ter_threshold:
          passes = False
      else:  # meanrev
        if m.get("Hurst", 0) > req.prefilter_hurst_max:
          passes = False
        elif m.get("VR5", 0) >= req.prefilter_vr_max:
          passes = False
        elif m.get("CrossRate", 0) < req.prefilter_min_crossing_rate:
          passes = False

      if passes:
        kept_symbols.append(sym)
      else:
        prefiltered.append(sym)
        logger.info("Pre-filtered out %s", sym)

    symbols = kept_symbols
    logger.info("L0 pre-filter: %d passed, %d filtered out", len(symbols), len(prefiltered))
    if not symbols:
      raise HTTPException(
        status_code=400,
        detail=f"L0 pre-filter ({req.prefilter_mode} mode) removed all {len(req.symbols)} symbols. "
               f"Try relaxing thresholds.",
      )

  # Auto-detect extra timeframes from strategy inputs (same as single backtest)
  extra_tfs = set(req.extra_timeframes or [])
  _TF_MAP = {"1h": "60", "4h": "240", "12h": "720", "1D": "D", "3D": "3D"}
  for param_key in ("RSI Timeframe", "MA Filter Timeframe"):
    tf_label = inputs.get(param_key)
    if tf_label and tf_label != "Current" and tf_label in _TF_MAP:
      extra_tfs.add(_TF_MAP[tf_label])
  resolved_extra_tfs = list(extra_tfs) if extra_tfs else None

  # Auto-generate batch name from strategy + config
  batch_name = req.name
  config_group_name = "Default"
  if not batch_name:
    batch_name = strategy.name
    if req.config_id:
      cfg = db.query(StrategyConfig).filter(StrategyConfig.id == req.config_id).first()
      if cfg:
        batch_name += f" — {cfg.name}"
        config_group_name = cfg.name
  else:
    # User provided a custom name — try to extract config from it
    sep = batch_name.find(" — ")
    if sep > 0:
      config_group_name = batch_name[sep + 3:]

  # Build shared parameters JSON
  params_json = json.dumps({
    "initial_capital": req.initial_capital,
    "commission_pct": req.commission_pct,
    "leverage": req.leverage,
    "max_pyramiding": max_pyramiding,
    "config_id": req.config_id,
    "strategy_inputs": inputs,
    "mode": req.mode,
    "slippage_mode": req.slippage_mode,
    "slippage_value": req.slippage_value,
    "warmup_bars": req.warmup_bars,
    "min_avg_daily_volume": req.min_avg_daily_volume,
    "max_avg_daily_volume": req.max_avg_daily_volume,
    "min_positions": req.min_positions,
    "risk_mode": req.risk_mode,
    "risk_value": req.risk_value,
    "martingale_enabled": req.martingale_enabled,
    "martingale_type": req.martingale_type,
    "martingale_multiplier": req.martingale_multiplier,
    "martingale_max_streak": req.martingale_max_streak,
    "vol_filter_enabled": req.vol_filter_enabled,
    "vol_filter_atr_ceiling": req.vol_filter_atr_ceiling,
    "vol_filter_atr_length": req.vol_filter_atr_length,
    "normalized": req.normalized,
    "batch_risk_amount": req.risk_value if req.normalized else 0,
    "include_costs": req.include_costs,
    "funding_rate_daily_pct": req.funding_rate_daily_pct,
    "spread_pct": req.spread_pct,
    "prefilter_enabled": req.prefilter_enabled,
    "prefilter_mode": req.prefilter_mode,
    "prefilter_count": len(prefiltered),
    "process_orders_on_close": req.process_orders_on_close,
    "enforce_bybit_limits": req.enforce_bybit_limits,
    "expected_live_size": req.expected_live_size,
    "max_margin_per_trade": req.max_margin_per_trade,
    "margin_filter_tolerance": req.margin_filter_tolerance,
    "bybit_reject_pct": req.bybit_reject_pct,
  })

  # Enforce include_costs toggle — zero out all costs when OFF
  if not req.include_costs:
    req.commission_pct = 0.0
    req.slippage_value = 0.0
    req.funding_rate_daily_pct = 0.0
    req.spread_pct = 0.0

  # Shared args for _run_batch background tasks
  _common_args = (
    strategy.python_source, symbols, req.timeframe,
  )
  _common_kwargs = dict(
    initial_capital=req.initial_capital,
    commission_pct=req.commission_pct, leverage=req.leverage,
    inputs=inputs, max_pyramiding=max_pyramiding,
    warmup_bars=req.warmup_bars, extra_timeframes=resolved_extra_tfs,
    risk_pct=req.risk_pct, mode=req.mode,
    slippage_mode=req.slippage_mode, slippage_value=req.slippage_value,
    risk_mode=req.risk_mode, risk_value=req.risk_value,
    martingale_enabled=req.martingale_enabled, martingale_type=req.martingale_type,
    martingale_multiplier=req.martingale_multiplier, martingale_max_streak=req.martingale_max_streak,
    vol_filter_enabled=req.vol_filter_enabled, vol_filter_atr_ceiling=req.vol_filter_atr_ceiling,
    vol_filter_atr_length=req.vol_filter_atr_length,
    strategy_risk_mode=strat_risk_mode, strategy_risk_value=strat_risk_value,
    min_positions=req.min_positions,
    funding_rate_daily_pct=req.funding_rate_daily_pct,
    spread_pct=req.spread_pct,
    process_orders_on_close=req.process_orders_on_close,
    enforce_bybit_limits=req.enforce_bybit_limits,
    expected_live_size=req.expected_live_size,
    max_margin_per_trade=req.max_margin_per_trade,
    margin_filter_tolerance=req.margin_filter_tolerance,
    bybit_reject_pct=req.bybit_reject_pct,
  )

  # --- Dedup guard (hash-based): reject if an identical request was just made ---
  # Lock prevents race condition where two identical requests arrive simultaneously,
  # both check the hash cache before either has reserved it, and both create batches.
  _sym_hash = hashlib.md5(",".join(sorted(symbols)).encode()).hexdigest()[:12]
  _req_fingerprint = f"{req.strategy_id}|{_sym_hash}|{req.timeframe}|{req.direction_filter}|{req.config_id}|{params_json}"
  _req_hash = hashlib.md5(_req_fingerprint.encode()).hexdigest()
  _now_mono = _time.monotonic()
  with _dedup_lock:
    # Purge stale entries
    _stale = [k for k, v in _recent_request_hashes.items() if _now_mono - v["ts"] > _DEDUP_HASH_TTL]
    for k in _stale:
      del _recent_request_hashes[k]
    _cached = _recent_request_hashes.get(_req_hash)
    if _cached:
      if _cached.get("batch_ids"):
        logger.info("Hash dedup guard: returning cached batch_ids %s (hash=%s)", _cached["batch_ids"], _req_hash[:8])
        return {
          "batch_id": _cached["batch_ids"][0],
          "batch_ids": _cached["batch_ids"],
          "status": "queued",
          "total_symbols": len(symbols),
          "skipped_low_adv": len(skipped_adv),
          "skipped_data_date": len(skipped_data_date),
          "prefiltered": len(prefiltered),
        }
      else:
        # Hash reserved but batch_ids not yet populated — another request is
        # creating this batch right now. Wait briefly for it to finish.
        _wait_for_dedup = True
    else:
      _wait_for_dedup = False
    if not _wait_for_dedup:
      # Reserve hash immediately — second request will see this and wait
      _recent_request_hashes[_req_hash] = {"batch_ids": None, "ts": _now_mono}
  # If another request is already creating this batch, wait for its IDs
  if _wait_for_dedup:
    for _ in range(20):  # up to 10 seconds
      _time.sleep(0.5)
      _entry = _recent_request_hashes.get(_req_hash)
      if _entry and _entry.get("batch_ids"):
        logger.info("Hash dedup guard (waited): returning batch_ids %s (hash=%s)", _entry["batch_ids"], _req_hash[:8])
        return {
          "batch_id": _entry["batch_ids"][0],
          "batch_ids": _entry["batch_ids"],
          "status": "queued",
          "total_symbols": len(symbols),
          "skipped_low_adv": len(skipped_adv),
          "skipped_data_date": len(skipped_data_date),
          "prefiltered": len(prefiltered),
        }
    logger.warning("Hash dedup guard: timed out waiting for pending batch (hash=%s) — allowing through", _req_hash[:8])

  # --- Dedup guard (DB-based): reject if an identical batch exists in last 10 seconds ---
  _dedup_cutoff = datetime.now(timezone.utc) - timedelta(seconds=10)
  if req.direction_filter == "long_plus_short":
    # For long_plus_short, the stored names have "— Long" / "— Short" suffixes
    _dedup_existing = (
      db.query(BatchBacktestRun)
      .filter(
        BatchBacktestRun.strategy_id == req.strategy_id,
        BatchBacktestRun.timeframe == req.timeframe,
        BatchBacktestRun.name.in_([f"{batch_name} — Long", f"{batch_name} — Short"]),
        BatchBacktestRun.is_deleted != True,
        BatchBacktestRun.created_at >= _dedup_cutoff,
      )
      .first()
    )
    if _dedup_existing:
      # Find both Long and Short batch IDs
      _dedup_pair = (
        db.query(BatchBacktestRun)
        .filter(
          BatchBacktestRun.strategy_id == req.strategy_id,
          BatchBacktestRun.timeframe == req.timeframe,
          BatchBacktestRun.name.in_([f"{batch_name} — Long", f"{batch_name} — Short"]),
          BatchBacktestRun.is_deleted != True,
          BatchBacktestRun.created_at >= _dedup_cutoff,
        )
        .order_by(BatchBacktestRun.id)
        .all()
      )
      _dedup_ids = [b.id for b in _dedup_pair]
      logger.info("Dedup guard (L+S): returning existing batches %s instead of creating duplicates", _dedup_ids)
      return {
        "batch_id": _dedup_ids[0],
        "batch_ids": _dedup_ids,
        "status": _dedup_pair[0].status or "queued",
        "total_symbols": _dedup_pair[0].total_symbols or len(symbols),
        "skipped_low_adv": len(skipped_adv),
        "skipped_data_date": len(skipped_data_date),
        "prefiltered": len(prefiltered),
      }
  else:
    _dedup_existing = (
      db.query(BatchBacktestRun)
      .filter(
        BatchBacktestRun.strategy_id == req.strategy_id,
        BatchBacktestRun.timeframe == req.timeframe,
        BatchBacktestRun.name == batch_name,
        BatchBacktestRun.is_deleted != True,
        BatchBacktestRun.created_at >= _dedup_cutoff,
      )
      .first()
    )
    if _dedup_existing:
      logger.info("Dedup guard: returning existing batch %d instead of creating duplicate", _dedup_existing.id)
      return {
        "batch_id": _dedup_existing.id,
        "batch_ids": [_dedup_existing.id],
        "status": _dedup_existing.status or "queued",
        "total_symbols": _dedup_existing.total_symbols or len(symbols),
        "skipped_low_adv": len(skipped_adv),
        "skipped_data_date": len(skipped_data_date),
        "prefiltered": len(prefiltered),
      }

  # Long+Short → create TWO separate batches (one long, one short)
  if req.direction_filter == "long_plus_short":
    batch_ids = []
    for dir_label, dir_filter in [("Long", "long"), ("Short", "short")]:
      full_name = f"{batch_name} — {dir_label}"
      b = BatchBacktestRun(
        strategy_id=req.strategy_id, timeframe=req.timeframe,
        direction_filter=dir_filter,
        name=full_name,
        source_name=full_name,
        config_group=config_group_name, notes=req.notes,
        parameters=params_json,
        extra_timeframes=json.dumps(sorted(extra_tfs)) if extra_tfs else "[]",
        total_symbols=len(symbols), completed_symbols=0, status="queued",
      )
      db.add(b)
      db.commit()
      db.refresh(b)
      batch_ids.append(b.id)

      _batch_progress[b.id] = {
        "status": "queued", "total_symbols": len(symbols),
        "completed_symbols": 0, "current_symbol": "",
        "symbol_statuses": {s: "pending" for s in symbols},
        "started_at": datetime.now(timezone.utc).isoformat(),
      }
      # Capture loop vars for lambda closure
      _bid = b.id
      _df = dir_filter
      _enqueue_batch(
        _bid,
        lambda bid=_bid, df=_df: _run_batch(bid, *_common_args, direction_filter=df, **_common_kwargs),
      )

    # Cache hash so duplicate requests are caught instantly
    _recent_request_hashes[_req_hash] = {"batch_ids": batch_ids, "ts": _time.monotonic()}
    return {
      "batch_id": batch_ids[0], "batch_ids": batch_ids,
      "status": "queued", "total_symbols": len(symbols),
      "skipped_low_adv": len(skipped_adv),
      "skipped_data_date": len(skipped_data_date),
      "prefiltered": len(prefiltered),
    }

  # Single direction — create one batch
  batch = BatchBacktestRun(
    strategy_id=req.strategy_id,
    timeframe=req.timeframe,
    direction_filter=req.direction_filter,
    name=batch_name,
    source_name=batch_name,
    config_group=config_group_name,
    notes=req.notes,
    parameters=params_json,
    extra_timeframes=json.dumps(sorted(extra_tfs)) if extra_tfs else "[]",
    total_symbols=len(symbols),
    completed_symbols=0,
    status="queued",
  )
  db.add(batch)
  db.commit()
  db.refresh(batch)

  _batch_progress[batch.id] = {
    "status": "queued",
    "total_symbols": len(symbols),
    "completed_symbols": 0,
    "current_symbol": "",
    "symbol_statuses": {s: "pending" for s in symbols},
    "started_at": datetime.now(timezone.utc).isoformat(),
  }

  _bid = batch.id
  _df = req.direction_filter
  _enqueue_batch(
    _bid,
    lambda bid=_bid, df=_df: _run_batch(bid, *_common_args, direction_filter=df, **_common_kwargs),
  )

  # Cache hash so duplicate requests are caught instantly
  _recent_request_hashes[_req_hash] = {"batch_ids": [batch.id], "ts": _time.monotonic()}
  return {
    "batch_id": batch.id, "status": "queued",
    "total_symbols": len(symbols),
    "skipped_low_adv": len(skipped_adv),
    "skipped_data_date": len(skipped_data_date),
    "prefiltered": len(prefiltered),
  }


@router.get("/batch/progress/{batch_id}")
def batch_progress(batch_id: int, db: Session = Depends(get_db)):
  # Fast path: in-memory progress (available while backend is running)
  prog = _batch_progress.get(batch_id)
  if prog is not None:
    # Add queue position for queued batches
    if prog.get("status") == "queued":
      with _queue_lock:
        pos = next(
          (i + 1 for i, item in enumerate(_batch_queue) if item.batch_id == batch_id),
          0,
        )
      return {**prog, "queue_position": pos}
    return prog
  # Slow path: fall back to DB (survives restart)
  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if batch and batch.progress_detail:
    try:
      saved = json.loads(batch.progress_detail)
      if saved and saved.get("status") != "deleted":
        return saved
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)
  # If batch is completed/error in DB but no progress_detail, synthesize
  if batch:
    return {"status": batch.status, "completed_symbols": batch.completed_symbols,
            "total_symbols": batch.total_symbols, "current_symbol": ""}
  return {"status": "unknown"}


@router.get("/batch/queue")
def batch_queue_status():
  """Return current queue depth and batch IDs in order."""
  with _queue_lock:
    items = [{"batch_id": item.batch_id, "position": i + 1}
             for i, item in enumerate(_batch_queue)]
  return {"queue_depth": len(items), "items": items}


@router.get("/batch/runs")
def list_batch_runs(db: Session = Depends(get_db), include_archived: bool = False):
  query = db.query(BatchBacktestRun).filter(
    BatchBacktestRun.is_deleted != True,
    BatchBacktestRun.sweep_id == None,  # noqa: E711  — exclude sweep child batches
  )
  if not include_archived:
    query = query.filter(
      (BatchBacktestRun.is_archived == False) | (BatchBacktestRun.is_archived == None)  # noqa: E712
    )
  batches = query.order_by(BatchBacktestRun.created_at.desc()).limit(1000).all()
  # Compute max data end date per batch in a single query
  batch_ids = [b.id for b in batches]
  date_map: dict[int, str | None] = {}
  if batch_ids:
    rows = (
      db.query(BacktestRun.batch_id, sa_func.max(BacktestRun.end_date))
      .filter(BacktestRun.batch_id.in_(batch_ids))
      .group_by(BacktestRun.batch_id)
      .all()
    )
    date_map = {bid: d for bid, d in rows}
  return [_batch_to_dict(b, data_end_date=date_map.get(b.id)) for b in batches]


@router.get("/batch/runs/{batch_id}")
def get_batch_detail(batch_id: int, db: Session = Depends(get_db), date_to: str | None = None):
  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if not batch:
    raise HTTPException(status_code=404, detail="Batch not found")
  # Get child runs (exclude filtered symbols — too few trades)
  child_runs = (
    db.query(BacktestRun)
    .filter(BacktestRun.batch_id == batch_id, BacktestRun.status != "filtered")
    .order_by(BacktestRun.symbol)
    .all()
  )

  # If date_to is set, load trades and recompute performance metrics per symbol
  perf_override: dict[str, dict] = {}
  if date_to:
    from datetime import datetime as dt
    cutoff = dt.fromisoformat(date_to)
    run_ids = [r.id for r in child_runs]
    if run_ids:
      trades = (
        db.query(BacktestTrade)
        .filter(BacktestTrade.run_id.in_(run_ids), BacktestTrade.entry_time <= cutoff)
        .all()
      )
      # Group trades by symbol
      from collections import defaultdict
      by_sym: dict[str, list] = defaultdict(list)
      for t in trades:
        by_sym[t.symbol].append(t)
      for sym, sym_trades in by_sym.items():
        total = len(sym_trades)
        wins = [t for t in sym_trades if t.net_profit > 0]
        gross_profit = sum(t.net_profit for t in wins)
        gross_loss = abs(sum(t.net_profit for t in sym_trades if t.net_profit <= 0))
        # Compute avg consecutive bull/bear/all streaks
        sorted_trades = sorted(sym_trades, key=lambda t: t.entry_time)
        bull_streaks: list[int] = []
        bear_streaks: list[int] = []
        cur_streak = 0
        cur_side: str | None = None
        for t in sorted_trades:
          side = "bull" if t.net_profit > 0 else "bear"
          if side == cur_side:
            cur_streak += 1
          else:
            if cur_side == "bull" and cur_streak > 0:
              bull_streaks.append(cur_streak)
            elif cur_side == "bear" and cur_streak > 0:
              bear_streaks.append(cur_streak)
            cur_streak = 1
            cur_side = side
        if cur_side == "bull" and cur_streak > 0:
          bull_streaks.append(cur_streak)
        elif cur_side == "bear" and cur_streak > 0:
          bear_streaks.append(cur_streak)
        avg_bull = round(sum(bull_streaks) / len(bull_streaks), 2) if bull_streaks else 0
        avg_bear = round(sum(bear_streaks) / len(bear_streaks), 2) if bear_streaks else 0
        all_streaks = bull_streaks + bear_streaks
        avg_all = round(sum(all_streaks) / len(all_streaks), 2) if all_streaks else 0
        perf_override[sym] = {
          "total_trades": total,
          "net_profit": round(sum(t.net_profit for t in sym_trades), 2),
          "win_rate": round(len(wins) / total * 100, 2) if total > 0 else 0,
          "profit_factor": round(min(gross_profit / gross_loss, 10.0), 2) if gross_loss > 0 else (10.0 if gross_profit > 0 else 0),
          "avg_consec_bull": avg_bull,
          "avg_consec_bear": avg_bear,
          "avg_consec_all": avg_all,
        }

  per_symbol = []
  for r in child_runs:
    metrics = sanitize_for_json(json.loads(r.metrics)) if r.metrics else {}
    # When OOS cutoff active, override performance metrics with recomputed values
    if date_to:
      empty = {"total_trades": 0, "net_profit": 0, "win_rate": 0, "profit_factor": 0, "avg_consec_bull": 0, "avg_consec_bear": 0, "avg_consec_all": 0}
      override = perf_override.get(r.symbol, empty)
      for k, v in override.items():
        metrics[k] = v
    per_symbol.append({
      "symbol": r.symbol,
      "run_id": r.id,
      "status": r.status,
      "metrics": metrics,
    })
  # Compute data_end_date from child runs
  max_end = max((r.end_date for r in child_runs if r.end_date), default=None)
  return {
    "batch": _batch_to_dict(batch, data_end_date=max_end),
    "aggregate_metrics": sanitize_for_json(json.loads(batch.aggregate_metrics)) if batch.aggregate_metrics else {},
    "per_symbol": per_symbol,
  }


def _find_portfolios_using_batch(db: Session, batch_id: int) -> list[dict]:
  """Find all portfolios that reference this batch_id.

  Checks two places:
    1. PortfolioAsset.batch_id — per-asset link to source batch
    2. Portfolio.source_strategies JSON — batch_ids / batch_id fields
  """
  from backend.db.models import Portfolio, PortfolioAsset

  # Check 1: PortfolioAsset.batch_id
  asset_refs = (
    db.query(PortfolioAsset.portfolio_id)
    .join(Portfolio)
    .filter(
      PortfolioAsset.batch_id == batch_id,
      Portfolio.is_hidden != True,
    )
    .distinct()
    .all()
  )
  portfolio_ids = {r[0] for r in asset_refs}

  # Check 2: Portfolio.source_strategies JSON contains batch_id
  all_portfolios = db.query(Portfolio).filter(Portfolio.is_hidden != True).all()
  for p in all_portfolios:
    if p.id in portfolio_ids:
      continue
    settings = json.loads(p.source_strategies or "{}")
    batch_ids = settings.get("batch_ids", [])
    if not batch_ids and settings.get("batch_id"):
      batch_ids = [settings["batch_id"]]
    if batch_id in batch_ids:
      portfolio_ids.add(p.id)

  if not portfolio_ids:
    return []
  portfolios = db.query(Portfolio).filter(Portfolio.id.in_(portfolio_ids)).all()
  return [{"id": p.id, "name": p.name} for p in portfolios]


@router.post("/batch/runs/{batch_id}/cancel")
def cancel_batch_run(batch_id: int, db: Session = Depends(get_db)):
  """Cancel a running batch backtest."""
  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if not batch:
    raise HTTPException(status_code=404, detail="Batch not found")
  if batch.status not in ("running", "downloading_extra_tfs", "queued"):
    return {"cancelled": False, "reason": f"Batch is already {batch.status}"}
  _cancelled_batches.add(batch_id)
  # Remove from queue if still queued
  with _queue_lock:
    items = [item for item in _batch_queue if item.batch_id != batch_id]
    _batch_queue.clear()
    _batch_queue.extend(items)
  prog = _batch_progress.get(batch_id)
  if prog:
    prog["status"] = "cancelled"
  batch.status = "cancelled"
  db.commit()
  _sync_progress_to_db(batch_id)
  logger.info("Batch %d cancelled by user", batch_id)
  return {"cancelled": True, "id": batch_id}


@router.post("/batch/cancel-all")
def cancel_all_batches(db: Session = Depends(get_db)):
  """Cancel ALL running batch backtests at once."""
  running = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.status.in_(["running", "downloading_extra_tfs", "queued"]))
    .all()
  )
  # Clear the entire queue
  with _queue_lock:
    _batch_queue.clear()
  cancelled_ids = []
  for batch in running:
    _cancelled_batches.add(batch.id)
    prog = _batch_progress.get(batch.id)
    if prog:
      prog["status"] = "cancelled"
    batch.status = "cancelled"
    cancelled_ids.append(batch.id)
  db.commit()
  for bid in cancelled_ids:
    _sync_progress_to_db(bid)
  logger.info("Cancelled ALL running/queued batches: %s", cancelled_ids)
  return {"cancelled": True, "ids": cancelled_ids, "count": len(cancelled_ids)}


@router.delete("/batch/runs/{batch_id}")
def delete_batch_run(batch_id: int, db: Session = Depends(get_db)):
  """Soft-delete a batch run and all its child runs."""
  from datetime import datetime
  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if not batch:
    raise HTTPException(status_code=404, detail="Batch not found")

  # Block deletion if any portfolio references this batch
  refs = _find_portfolios_using_batch(db, batch_id)
  if refs:
    names = ", ".join(p["name"] for p in refs)
    raise HTTPException(
      status_code=409,
      detail=f"Cannot delete batch #{batch_id} — used by portfolio(s): {names}",
    )

  now = datetime.now(timezone.utc)
  batch.is_deleted = True
  batch.deleted_at = now
  # Soft-delete child runs too
  child_runs = db.query(BacktestRun).filter(BacktestRun.batch_id == batch_id).all()
  for run in child_runs:
    run.is_deleted = True
    run.deleted_at = now
  db.commit()
  _batch_progress.pop(batch_id, None)
  return {"deleted": True, "id": batch_id, "child_runs_deleted": len(child_runs)}


@router.delete("/batch/runs")
def delete_all_batch_runs(db: Session = Depends(get_db)):
  """Soft-delete ALL batch runs and their child runs.

  Batches referenced by portfolios are skipped (not deleted).
  """
  from datetime import datetime
  now = datetime.now(timezone.utc)
  batches = db.query(BatchBacktestRun).filter(BatchBacktestRun.is_deleted != True).all()
  total_deleted = 0
  total_children = 0
  skipped = []
  for batch in batches:
    refs = _find_portfolios_using_batch(db, batch.id)
    if refs:
      names = ", ".join(p["name"] for p in refs)
      skipped.append({"batch_id": batch.id, "name": batch.name, "portfolios": names})
      continue
    batch.is_deleted = True
    batch.deleted_at = now
    children = db.query(BacktestRun).filter(BacktestRun.batch_id == batch.id).all()
    total_children += len(children)
    for run in children:
      run.is_deleted = True
      run.deleted_at = now
    total_deleted += 1
  db.commit()
  _batch_progress.clear()
  return {
    "deleted": True,
    "batches_deleted": total_deleted,
    "child_runs_deleted": total_children,
    "batches_skipped": skipped,
  }


@router.post("/batch/runs/cleanup-duplicates")
def cleanup_duplicate_batches(db: Session = Depends(get_db)):
  """Find and soft-delete duplicate batches.

  Duplicates = same name, strategy_id, timeframe, direction_filter, and
  created_at within 60 seconds of each other.  Keeps the newest (highest id)
  of each group and soft-deletes the rest.
  """
  batches = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.is_deleted != True)
    .order_by(BatchBacktestRun.id.desc())
    .all()
  )
  # Group by signature (name + strategy + tf + direction + minute)
  seen: dict[str, int] = {}  # sig → kept batch id
  to_delete: list[BatchBacktestRun] = []
  for b in batches:
    ts = int(b.created_at.timestamp() // 60) if b.created_at else 0
    sig = f"{b.name}|{b.strategy_id}|{b.timeframe}|{b.direction_filter}|{ts}"
    if sig not in seen:
      seen[sig] = b.id  # keep newest (ordered desc by id)
    else:
      # Check it's not used by a portfolio
      refs = _find_portfolios_using_batch(db, b.id)
      if not refs:
        to_delete.append(b)

  now = datetime.now(timezone.utc)
  child_count = 0
  for b in to_delete:
    b.is_deleted = True
    b.deleted_at = now
    children = db.query(BacktestRun).filter(BacktestRun.batch_id == b.id).all()
    child_count += len(children)
    for run in children:
      run.is_deleted = True
      run.deleted_at = now
    _batch_progress.pop(b.id, None)
  db.commit()
  logger.info("Cleanup: removed %d duplicate batches (%d child runs)", len(to_delete), child_count)
  return {
    "duplicates_removed": len(to_delete),
    "child_runs_removed": child_count,
  }


class BatchUpdateRequest(BaseModel):
  name: str | None = None
  notes: str | None = None
  personal_grade: str | None = None


@router.patch("/batch/runs/{batch_id}/notes")
def update_batch_notes(
  batch_id: int, req: BatchUpdateRequest, db: Session = Depends(get_db),
):
  """Update batch name, notes, and/or personal grade."""
  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if not batch:
    raise HTTPException(status_code=404, detail="Batch not found")
  if req.name is not None:
    batch.name = req.name
  if req.notes is not None:
    batch.notes = req.notes
  if req.personal_grade is not None:
    # Allow clearing with empty string
    batch.personal_grade = req.personal_grade if req.personal_grade else None
  db.commit()
  return _batch_to_dict(batch)


@router.patch("/batch/runs/{batch_id}/archive")
def toggle_batch_archive(batch_id: int, db: Session = Depends(get_db)):
  """Toggle archived status on a batch."""
  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if not batch:
    raise HTTPException(status_code=404, detail="Batch not found")
  batch.is_archived = not batch.is_archived
  db.commit()
  return {"id": batch.id, "is_archived": batch.is_archived}


@router.post("/batch/runs/{batch_id}/compact")
def compact_batch(batch_id: int, db: Session = Depends(get_db)):
  """Compact a batch: archive it and delete all child runs + trades to save space.

  Keeps the batch record with aggregate KPIs (grade, PF, WR) and parameters
  so it can still be re-run via the Refresh button.
  """
  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if not batch:
    raise HTTPException(status_code=404, detail="Batch not found")
  if batch.is_compacted:
    return {"id": batch.id, "is_compacted": True, "runs_deleted": 0, "trades_deleted": 0}

  # Block if any portfolio references this batch
  refs = _find_portfolios_using_batch(db, batch_id)
  if refs:
    names = ", ".join(p["name"] for p in refs)
    raise HTTPException(
      status_code=409,
      detail=f"Cannot compact batch #{batch_id} — used by portfolio(s): {names}",
    )

  # Count what we're about to delete
  child_runs = db.query(BacktestRun).filter(BacktestRun.batch_id == batch_id).all()
  run_ids = [r.id for r in child_runs]
  trades_deleted = 0
  if run_ids:
    trades_deleted = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).delete(synchronize_session=False)
  runs_deleted = db.query(BacktestRun).filter(BacktestRun.batch_id == batch_id).delete(synchronize_session=False)

  batch.is_archived = True
  batch.is_compacted = True
  db.commit()
  logger.info("Batch %d compacted: %d runs, %d trades deleted", batch_id, runs_deleted, trades_deleted)
  return {"id": batch.id, "is_compacted": True, "runs_deleted": runs_deleted, "trades_deleted": trades_deleted}


@router.post("/batch/runs/{batch_id}/repair-structural")
def repair_structural_metrics(batch_id: int, db: Session = Depends(get_db)):
  """Re-compute structural metrics for ALL symbols in a completed batch.

  Uses raw OHLCV close prices — every asset with candle data gets graded,
  regardless of trade count.
  """
  from backend.utils.candle_loader import load_candles_df

  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if not batch:
    raise HTTPException(status_code=404, detail="Batch not found")

  child_runs = (
    db.query(BacktestRun)
    .filter(BacktestRun.batch_id == batch_id)
    .all()
  )

  repaired = 0
  skipped = 0
  for run in child_runs:
    try:
      df = load_candles_df(db, run.symbol, batch.timeframe)
      close_prices = df["close"].values
      structural = _compute_structural_metrics_from_prices(
        close_prices, pool_size=len(child_runs),
      )
      all_new = {}
      if structural:
        all_new.update(structural)
      # Also compute PnL-based structural metrics
      try:
        pnl_metrics = _compute_structural_metrics_from_pnl(
          db, run.id, pool_size=len(child_runs),
        )
        if pnl_metrics:
          all_new.update(pnl_metrics)
      except Exception as _exc:
        logger.debug("swallowed: %s", _exc)  # PnL metrics are optional (may have < 3 trades)
      if all_new:
        merged = json.loads(run.metrics) if run.metrics else {}
        merged.update(all_new)
        run.metrics = json.dumps(sanitize_for_json(merged))
        db.commit()
        repaired += 1
      else:
        skipped += 1
    except Exception as e:
      logger.warning("Repair failed for %s: %s", run.symbol, e)
      skipped += 1

  return {
    "batch_id": batch_id,
    "repaired": repaired,
    "skipped": skipped,
    "total": len(child_runs),
  }


@router.post("/batch/repair-all-structural")
def repair_all_structural_metrics(db: Session = Depends(get_db)):
  """Re-compute structural metrics for ALL completed batches.

  Uses raw OHLCV close prices — every asset with candle data gets graded.
  Always recomputes (overwrites old trade-PnL-based metrics with correct
  OHLCV-based ones).
  """
  from backend.utils.candle_loader import load_candles_df

  batches = (
    db.query(BatchBacktestRun)
    .filter(
      BatchBacktestRun.status == "completed",
      BatchBacktestRun.is_deleted != True,
    )
    .all()
  )

  total_repaired = 0
  total_skipped = 0
  batch_count = 0

  for batch in batches:
    child_runs = (
      db.query(BacktestRun)
      .filter(BacktestRun.batch_id == batch.id)
      .all()
    )

    if not child_runs:
      continue

    batch_count += 1
    for run in child_runs:
      try:
        df = load_candles_df(db, run.symbol, batch.timeframe)
        close_prices = df["close"].values
        structural = _compute_structural_metrics_from_prices(
          close_prices, pool_size=len(child_runs),
        )
        all_new = {}
        if structural:
          all_new.update(structural)
        # Also compute PnL-based structural metrics
        try:
          pnl_metrics = _compute_structural_metrics_from_pnl(
            db, run.id, pool_size=len(child_runs),
          )
          if pnl_metrics:
            all_new.update(pnl_metrics)
        except Exception as _exc:
          logger.debug("swallowed: %s", _exc)
        if all_new:
          merged = json.loads(run.metrics) if run.metrics else {}
          merged.update(all_new)
          run.metrics = json.dumps(sanitize_for_json(merged))
          db.commit()
          total_repaired += 1
        else:
          total_skipped += 1
      except Exception as e:
        logger.warning("Repair failed for %s in batch %d: %s", run.symbol, batch.id, e)
        total_skipped += 1

  return {
    "batches_processed": batch_count,
    "total_repaired": total_repaired,
    "total_skipped": total_skipped,
  }


class MergeBatchesRequest(BaseModel):
  batch_ids: list[int]
  name: str | None = None


@router.post("/batch/merge")
def merge_batches(req: MergeBatchesRequest, db: Session = Depends(get_db)):
  """Merge 2+ completed batches into a single combined batch."""
  if len(req.batch_ids) < 2:
    raise HTTPException(status_code=400, detail="Need at least 2 batches to merge")

  # Load and validate all source batches
  batches = []
  for bid in req.batch_ids:
    b = db.query(BatchBacktestRun).filter(
      BatchBacktestRun.id == bid, BatchBacktestRun.is_deleted != True,
    ).first()
    if not b:
      raise HTTPException(status_code=404, detail=f"Batch {bid} not found")
    if b.status != "completed":
      raise HTTPException(status_code=400, detail=f"Batch {bid} is not completed (status: {b.status})")
    batches.append(b)

  # Validate same strategy
  strategy_ids = set(b.strategy_id for b in batches)
  if len(strategy_ids) > 1:
    raise HTTPException(status_code=400, detail="All batches must be from the same strategy")

  # Gather child BacktestRun records from all source batches
  all_child_runs: list[BacktestRun] = []
  for b in batches:
    runs = (
      db.query(BacktestRun)
      .filter(BacktestRun.batch_id == b.id, BacktestRun.status != "filtered")
      .all()
    )
    all_child_runs.extend(runs)

  # Group by symbol — merge trades from overlapping symbols
  from collections import defaultdict
  symbol_runs: dict[str, list[BacktestRun]] = defaultdict(list)
  for r in all_child_runs:
    symbol_runs[r.symbol].append(r)

  # Auto-generate name
  batch_directions = [b.direction_filter or "?" for b in batches]
  if req.name:
    merged_name = req.name
  else:
    base_name = batches[0].name or "Batch"
    # Strip direction suffix if present
    for suffix in [" — Long", " — Short"]:
      if base_name.endswith(suffix):
        base_name = base_name[: -len(suffix)]
    merged_name = f"{base_name} — Merged ({'+'.join(batch_directions)})"

  config_group = batches[0].config_group or "Default"
  params = batches[0].parameters or "{}"

  # Create merged batch record
  # Collect extra TFs from all source batches
  all_extra = set()
  for b in batches:
    for tf in json.loads(b.extra_timeframes or "[]"):
      all_extra.add(tf)

  merged_batch = BatchBacktestRun(
    strategy_id=batches[0].strategy_id,
    timeframe=batches[0].timeframe,
    direction_filter="merged",
    name=merged_name,
    source_name=merged_name,
    config_group=config_group,
    parameters=params,
    extra_timeframes=json.dumps(sorted(all_extra)) if all_extra else "[]",
    source_batch_ids=json.dumps(req.batch_ids),
    total_symbols=len(symbol_runs),
    completed_symbols=len(symbol_runs),
    status="completed",
  )
  db.add(merged_batch)
  db.commit()
  db.refresh(merged_batch)

  # Create child BacktestRun records for each symbol in the merged batch
  from backend.db.models import BacktestTrade
  all_metrics: list[dict] = []

  for symbol, runs in symbol_runs.items():
    if len(runs) == 1:
      # Single source — copy metrics and link trades
      src = runs[0]
      merged_metrics = json.loads(src.metrics) if src.metrics else {}
      merged_run = BacktestRun(
        strategy_id=merged_batch.strategy_id,
        batch_id=merged_batch.id,
        symbol=symbol, timeframe=merged_batch.timeframe,
        start_date=src.start_date, end_date=src.end_date,
        parameters=json.dumps({"direction_filter": "merged", "source_run_ids": [src.id]}),
        metrics=json.dumps(sanitize_for_json(merged_metrics)),
        status="completed",
      )
      db.add(merged_run)
      db.flush()

      # Copy trades from source run
      src_trades = db.query(BacktestTrade).filter(BacktestTrade.run_id == src.id).all()
      for t in src_trades:
        new_trade = BacktestTrade(
          run_id=merged_run.id, trade_num=t.trade_num,
          symbol=t.symbol, side=t.side,
          entry_time=t.entry_time, exit_time=t.exit_time,
          entry_price=t.entry_price, exit_price=t.exit_price,
          quantity=t.quantity, net_profit=t.net_profit,
          net_profit_pct=t.net_profit_pct, commission=t.commission,
          mae=t.mae, mfe=t.mfe,
          sl_price=t.sl_price, tp_price=t.tp_price,
          exit_reason=t.exit_reason, position_id=t.position_id,
        )
        db.add(new_trade)

      all_metrics.append({"symbol": symbol, **merged_metrics})
    else:
      # Multiple sources — merge trades and recalculate metrics
      all_trades_for_symbol = []
      start_dates = []
      end_dates = []
      for src in runs:
        src_trades = db.query(BacktestTrade).filter(BacktestTrade.run_id == src.id).all()
        all_trades_for_symbol.extend(src_trades)
        if src.start_date:
          start_dates.append(src.start_date)
        if src.end_date:
          end_dates.append(src.end_date)

      # Sort trades chronologically
      all_trades_for_symbol.sort(key=lambda t: t.entry_time or datetime.min)

      # Combine metrics from source runs (simple aggregation)
      combined_metrics: dict = {}
      for src in runs:
        src_m = json.loads(src.metrics) if src.metrics else {}
        if not combined_metrics:
          combined_metrics = dict(src_m)
        else:
          # Sum additive metrics, recalculate ratios
          combined_metrics["total_trades"] = combined_metrics.get("total_trades", 0) + src_m.get("total_trades", 0)
          combined_metrics["net_profit"] = combined_metrics.get("net_profit", 0) + src_m.get("net_profit", 0)
          combined_metrics["gross_profit"] = combined_metrics.get("gross_profit", 0) + src_m.get("gross_profit", 0)
          combined_metrics["gross_loss"] = combined_metrics.get("gross_loss", 0) + src_m.get("gross_loss", 0)
          combined_metrics["winning_trades"] = combined_metrics.get("winning_trades", 0) + src_m.get("winning_trades", 0)
          combined_metrics["losing_trades"] = combined_metrics.get("losing_trades", 0) + src_m.get("losing_trades", 0)
          combined_metrics["total_commission"] = combined_metrics.get("total_commission", 0) + src_m.get("total_commission", 0)

      # Recalculate derived metrics
      total = combined_metrics.get("total_trades", 0)
      wins = combined_metrics.get("winning_trades", 0)
      gp = combined_metrics.get("gross_profit", 0)
      gl = abs(combined_metrics.get("gross_loss", 0))
      if total > 0:
        combined_metrics["win_rate"] = round(wins / total * 100, 2)
      if gl > 0:
        combined_metrics["profit_factor"] = round(min(gp / gl, 10.0), 4)

      merged_run = BacktestRun(
        strategy_id=merged_batch.strategy_id,
        batch_id=merged_batch.id,
        symbol=symbol, timeframe=merged_batch.timeframe,
        start_date=min(start_dates) if start_dates else None,
        end_date=max(end_dates) if end_dates else None,
        parameters=json.dumps({"direction_filter": "merged", "source_run_ids": [r.id for r in runs]}),
        metrics=json.dumps(sanitize_for_json(combined_metrics)),
        status="completed",
      )
      db.add(merged_run)
      db.flush()

      # Copy all trades to merged run (renumber trade_num)
      for idx, t in enumerate(all_trades_for_symbol, 1):
        new_trade = BacktestTrade(
          run_id=merged_run.id, trade_num=idx,
          symbol=t.symbol, side=t.side,
          entry_time=t.entry_time, exit_time=t.exit_time,
          entry_price=t.entry_price, exit_price=t.exit_price,
          quantity=t.quantity, net_profit=t.net_profit,
          net_profit_pct=t.net_profit_pct, commission=t.commission,
          mae=t.mae, mfe=t.mfe,
          sl_price=t.sl_price, tp_price=t.tp_price,
          exit_reason=t.exit_reason, position_id=t.position_id,
        )
        db.add(new_trade)

      all_metrics.append({"symbol": symbol, **combined_metrics})

  db.commit()

  # Calculate and store aggregate metrics
  agg = _aggregate_metrics(all_metrics)
  merged_batch.aggregate_metrics = json.dumps(sanitize_for_json(agg))
  db.commit()

  return {
    "batch_id": merged_batch.id,
    "name": merged_batch.name,
    "source_batch_ids": req.batch_ids,
    "total_symbols": len(symbol_runs),
    "status": "completed",
  }


def _batch_to_dict(b: BatchBacktestRun, *, data_end_date: str | None = None) -> dict:
  params = {}
  if b.parameters:
    try:
      params = json.loads(b.parameters)
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)
  # Extract batch grade + key metrics from aggregate_metrics if available
  batch_grade = None
  batch_grade_score = None
  avg_profit_factor = None
  avg_win_rate = None
  symbols_with_trades = None
  total_net_profit = None
  total_trades_all = None
  avg_max_drawdown_pct = None
  profitable_symbols = None
  best_symbol = None
  worst_symbol = None
  total_symbols_tested = None
  if b.aggregate_metrics:
    try:
      agg = json.loads(b.aggregate_metrics)
      batch_grade = agg.get("batch_grade")
      batch_grade_score = agg.get("batch_grade_score")
      avg_profit_factor = agg.get("avg_profit_factor")
      avg_win_rate = agg.get("avg_win_rate")
      symbols_with_trades = agg.get("symbols_with_trades")
      total_net_profit = agg.get("total_net_profit")
      total_trades_all = agg.get("total_trades_all")
      avg_max_drawdown_pct = agg.get("avg_max_drawdown_pct")
      profitable_symbols = agg.get("profitable_symbols")
      best_symbol = agg.get("best_symbol")
      worst_symbol = agg.get("worst_symbol")
      total_symbols_tested = agg.get("total_symbols_tested")
      # Force D- when total net profit is negative
      if total_net_profit is not None and total_net_profit < 0 and batch_grade:
        batch_grade = "D-"
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)
  return {
    "id": b.id,
    "strategy_id": b.strategy_id,
    "timeframe": b.timeframe,
    "direction_filter": b.direction_filter,
    "total_symbols": b.total_symbols,
    "completed_symbols": b.completed_symbols,
    "status": b.status,
    "created_at": (b.created_at.isoformat() + "Z") if b.created_at else None,
    "name": b.name,
    "notes": b.notes or "",
    "config_group": b.config_group,
    "source_batch_ids": json.loads(b.source_batch_ids) if b.source_batch_ids else None,
    "refreshed_from_id": b.refreshed_from_id,
    "source_name": b.source_name or b.name,
    "group_id": b.group_id,
    "extra_timeframes": json.loads(b.extra_timeframes or "[]"),
    "parameters": params,
    "data_end_date": str(data_end_date)[:10] if data_end_date else None,
    "batch_grade": batch_grade,
    "batch_grade_score": batch_grade_score,
    "avg_profit_factor": avg_profit_factor,
    "avg_win_rate": avg_win_rate,
    "symbols_with_trades": symbols_with_trades,
    "total_net_profit": total_net_profit,
    "is_archived": bool(b.is_archived),
    "is_compacted": bool(b.is_compacted),
    "personal_grade": b.personal_grade or None,
    "total_trades_all": total_trades_all,
    "avg_max_drawdown_pct": avg_max_drawdown_pct,
    "profitable_symbols": profitable_symbols,
    "best_symbol": best_symbol,
    "worst_symbol": worst_symbol,
    "total_symbols_tested": total_symbols_tested,
  }


def _load_candles_df(db: Session, symbol: str, timeframe: str) -> "pd.DataFrame":
  """Load candles from DB — delegates to shared candle_loader."""
  from backend.utils.candle_loader import load_candles_df
  return load_candles_df(db, symbol, timeframe)


def _to_nyt(df: "pd.DataFrame") -> "pd.DataFrame":
  """Convert UTC → NYT — delegates to shared candle_loader."""
  from backend.utils.candle_loader import to_nyt
  return to_nyt(df)


def _download_missing_extra_tfs(symbols: list[str], extra_timeframes: list[str], batch_id: int):
  """Auto-download extra TF data that's missing from DB before batch runs.

  For derived timeframes (e.g. 3D), builds from source data instead of Bybit.
  """
  import asyncio

  from backend.data.bybit_downloader import BybitDownloader
  from backend.data.data_manager import DataManager, is_derived_tf
  from backend.data.derived_candles import rebuild_3d_candles

  manager = DataManager()
  missing: list[tuple[str, str]] = []
  derived_missing: list[tuple[str, str]] = []
  for tf in extra_timeframes:
    for symbol in symbols:
      if not manager.get_latest_timestamp(symbol, tf):
        if is_derived_tf(tf):
          derived_missing.append((symbol, tf))
        else:
          missing.append((symbol, tf))

  if not missing and not derived_missing:
    logger.info("All extra TF data present for batch %d", batch_id)
    return

  total_missing = len(missing) + len(derived_missing)
  logger.info(
    "Auto-downloading %d missing extra TF datasets for batch %d (%d derived)",
    total_missing, batch_id, len(derived_missing),
  )
  _safe_progress(batch_id)["status"] = "downloading_extra_tfs"

  # Download non-derived TFs from Bybit
  if missing:
    async def _do_downloads():
      downloader = BybitDownloader()
      for i, (symbol, tf) in enumerate(missing):
        try:
          latest = manager.get_latest_timestamp(symbol, tf)
          # Naive datetimes from DB are UTC — force tzinfo to avoid local-time shift
          if latest and latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
          since_ms = int(latest.timestamp() * 1000) + 1 if latest else None
          candles = await downloader.fetch_all_klines(symbol, tf, since_ms=since_ms)
          if candles:
            manager.upsert_candles(symbol, tf, candles)
          logger.info("Downloaded %s/%s (%d/%d)", symbol, tf, i + 1, len(missing))
        except Exception as e:
          logger.warning("Failed to download %s/%s: %s", symbol, tf, e)
        await asyncio.sleep(0.3)

    # CS-021: asyncio.run handles cleanup on exception and raises
    # RuntimeError cleanly if called from a running loop instead of
    # manually juggling new_event_loop / run_until_complete / close.
    asyncio.run(_do_downloads())

  # Build derived TFs from source data (e.g. 3D from 1D)
  for symbol, tf in derived_missing:
    try:
      if tf == "3D":
        # Ensure 1D data exists — download if missing
        if not manager.get_latest_timestamp(symbol, "D"):
          async def _dl_daily(sym: str):
            dl = BybitDownloader()
            candles = await dl.fetch_all_klines(sym, "D")
            if candles:
              manager.upsert_candles(sym, "D", candles)
          # CS-021: prefer asyncio.run over manual loop lifecycle
          asyncio.run(_dl_daily(symbol))
        rebuild_3d_candles(symbol)
        logger.info("Built 3D for %s", symbol)
    except Exception as e:
      logger.warning("Failed to build derived %s/%s: %s", symbol, tf, e)

  _safe_progress(batch_id)["status"] = "running"
  logger.info("Finished downloading extra TF data for batch %d", batch_id)


def _run_batch(
  batch_id: int, python_source: str, symbols: list[str],
  timeframe: str, direction_filter: str, initial_capital: float,
  commission_pct: float, leverage: int, inputs: dict,
  max_pyramiding: int = 1, warmup_bars: int = 0,
  extra_timeframes: list[str] | None = None,
  risk_pct: float = 0.0, mode: str = "individual",
  slippage_mode: str = "pct", slippage_value: float = 0.0,
  risk_mode: str = "pct", risk_value: float = 0.0,
  martingale_enabled: bool = False, martingale_type: str = "anti",
  martingale_multiplier: float = 2.0, martingale_max_streak: int = 3,
  vol_filter_enabled: bool = False, vol_filter_atr_ceiling: float = 0.0,
  vol_filter_atr_length: int = 14,
  strategy_risk_mode: str = "fixed", strategy_risk_value: float = 0.0,
  min_positions: int = 0,
  funding_rate_daily_pct: float = 0.0, spread_pct: float = 0.0,
  process_orders_on_close: bool = False,
  enforce_bybit_limits: bool = False,
  expected_live_size: float = 0.0,
  max_margin_per_trade: float = 0.0,
  margin_filter_tolerance: float = 10.0,
  bybit_reject_pct: float = 0.0,
):
  """Run backtest — individual (per-symbol) or portfolio (shared equity)."""
  from backend.db.database import SessionLocal

  # Fetch Bybit instrument specs (cached between batches to avoid redundant API calls)
  global _instrument_specs_cache, _instrument_specs_ts
  instrument_specs: dict[str, dict] = {}
  if enforce_bybit_limits or expected_live_size > 0:
    _now = _time.monotonic()
    if _instrument_specs_cache is not None and (_now - _instrument_specs_ts) < _INSTRUMENT_SPECS_TTL:
      instrument_specs = _instrument_specs_cache
      logger.info("Using cached Bybit instrument specs (%d symbols, %.0fs old)",
                  len(instrument_specs), _now - _instrument_specs_ts)
    else:
      try:
        import asyncio
        from backend.data.bybit_downloader import BybitDownloader
        async def _fetch():
          dl = BybitDownloader()
          try:
            return await dl.get_all_instrument_specs()
          finally:
            await dl.close()
        instrument_specs = asyncio.run(_fetch())
        _instrument_specs_cache = instrument_specs
        _instrument_specs_ts = _time.monotonic()
        logger.info("Fetched Bybit instrument specs for %d symbols (cached for %ds)",
                    len(instrument_specs), _INSTRUMENT_SPECS_TTL)
      except Exception as exc:
        logger.warning("Failed to fetch Bybit instrument specs: %s", exc)
        # Fall back to stale cache if available
        if _instrument_specs_cache is not None:
          instrument_specs = _instrument_specs_cache
          logger.info("Using stale cached specs as fallback (%d symbols)", len(instrument_specs))

  # Auto-download missing extra TF data before running
  if extra_timeframes:
    try:
      _download_missing_extra_tfs(symbols, extra_timeframes, batch_id)
    except Exception as e:
      logger.error("Extra TF download failed for batch %d: %s", batch_id, e)

  db = SessionLocal()

  try:
    if mode == "portfolio":
      _run_portfolio_batch(
        db, batch_id, python_source, symbols, timeframe,
        direction_filter, initial_capital, commission_pct, leverage,
        inputs, max_pyramiding, warmup_bars, extra_timeframes, risk_pct,
        slippage_mode, slippage_value,
        risk_mode, risk_value,
        martingale_enabled, martingale_type, martingale_multiplier, martingale_max_streak,
        vol_filter_enabled, vol_filter_atr_ceiling, vol_filter_atr_length,
        strategy_risk_mode, strategy_risk_value,
        min_positions,
        funding_rate_daily_pct, spread_pct,
        process_orders_on_close,
        instrument_specs=instrument_specs,
        expected_live_size=expected_live_size,
      )
    else:
      _run_individual_batch(
        db, batch_id, python_source, symbols, timeframe,
        direction_filter, initial_capital, commission_pct, leverage,
        inputs, max_pyramiding, warmup_bars, extra_timeframes, risk_pct,
        slippage_mode, slippage_value,
        risk_mode, risk_value,
        martingale_enabled, martingale_type, martingale_multiplier, martingale_max_streak,
        vol_filter_enabled, vol_filter_atr_ceiling, vol_filter_atr_length,
        strategy_risk_mode, strategy_risk_value,
        min_positions,
        funding_rate_daily_pct, spread_pct,
        process_orders_on_close,
        instrument_specs=instrument_specs,
        expected_live_size=expected_live_size,
        max_margin_per_trade=max_margin_per_trade,
        margin_filter_tolerance=margin_filter_tolerance,
        bybit_reject_pct=bybit_reject_pct,
      )
  except Exception as e:
    logger.error("Batch %d failed: %s", batch_id, e)
    logger.error(traceback.format_exc())
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    if batch:
      batch.status = "error"
      batch.error_message = str(e)
      db.commit()
    _safe_progress(batch_id)["status"] = "error"
    _sync_progress_to_db(batch_id)
  finally:
    db.close()


def _mp_backtest_worker(args: dict) -> dict:
  """Run backtest + structural metrics for one symbol in a thread.

  No DB access — returns all results for the main thread to persist.
  """
  from backend.backtester.engine import BacktestConfig, BacktestEngine
  from backend.utils.safe_exec import safe_exec
  from backend.utils.sanitize import sanitize_for_json

  symbol = args["symbol"]

  try:
    strategy_cls = safe_exec(args["python_source"], label=f"batch_{symbol}")
    if not strategy_cls:
      return {"symbol": symbol, "error": "No TranspiledStrategy",
              "metrics": {}, "trades": [],
              "start_date": None, "end_date": None}

    # Auto-detect process_orders_on_close from transpiled strategy
    proc_on_close = args.get("process_orders_on_close", False)
    if not proc_on_close and getattr(strategy_cls, 'process_orders_on_close', False) is True:
      proc_on_close = True

    df = args["df"]
    extra_tf_dfs = args.get("extra_tf_dfs") or {}

    config = BacktestConfig(
      initial_capital=args["initial_capital"],
      commission_pct=args["commission_pct"],
      slippage_mode=args["slippage_mode"],
      slippage_value=args["slippage_value"],
      leverage=args["leverage"],
      direction_filter=args["direction_filter"],
      max_pyramiding=args["max_pyramiding"],
      warmup_bars=args["warmup_bars"],
      risk_pct=args["risk_pct"],
      risk_mode=args["risk_mode"],
      risk_value=args["risk_value"],
      strategy_risk_mode=args["strategy_risk_mode"],
      strategy_risk_value=args["strategy_risk_value"],
      martingale_enabled=args["martingale_enabled"],
      martingale_type=args["martingale_type"],
      martingale_multiplier=args["martingale_multiplier"],
      martingale_max_streak=args["martingale_max_streak"],
      vol_filter_enabled=args["vol_filter_enabled"],
      vol_filter_atr_ceiling=args["vol_filter_atr_ceiling"],
      vol_filter_atr_length=args["vol_filter_atr_length"],
      funding_rate_daily_pct=args["funding_rate_daily_pct"],
      spread_pct=args["spread_pct"],
      process_orders_on_close=proc_on_close,
      bybit_lot_size=args.get("bybit_lot_size", 0.0),
      bybit_min_qty=args.get("bybit_min_qty", 0.0),
      bybit_max_qty=args.get("bybit_max_qty", 0.0),
    )

    engine = BacktestEngine(config)
    worker_inputs = dict(args.get("inputs") or {})
    result = engine.run(
      strategy_cls(), df, symbol=symbol, timeframe=args["timeframe"],
      inputs=worker_inputs, extra_timeframes=extra_tf_dfs,
    )

    metrics = dict(result.metrics)
    pool_size = args.get("pool_size", 20)

    # OHLCV structural metrics (measures asset behavior)
    try:
      close_prices = df["close"].values
      ohlcv_metrics = _compute_structural_metrics_from_prices(
        close_prices, pool_size=pool_size,
      )
      if ohlcv_metrics:
        metrics.update(ohlcv_metrics)
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)

    # PnL structural metrics (measures strategy behavior on this asset)
    try:
      pnl_metrics = _compute_structural_metrics_from_pnl(
        None, 0, pool_size=pool_size,
        trade_fills=result.trades,
      )
      if pnl_metrics:
        metrics.update(pnl_metrics)
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)

    return {
      "symbol": symbol,
      "metrics": sanitize_for_json(metrics),
      "trades": result.trades,
      "start_date": df.index[0],
      "end_date": df.index[-1],
      "error": None,
    }
  except Exception as e:
    return {
      "symbol": symbol,
      "metrics": {"total_trades": 0, "net_profit": 0},
      "trades": [],
      "start_date": None,
      "end_date": None,
      "error": str(e),
    }


def _store_batch_result(
  db: Session, batch_id: int, strategy_id: int, result: dict,
  direction_filter: str, inputs: dict, initial_capital: float,
  timeframe: str,
) -> None:
  """Write one symbol's backtest result to DB (main thread only).

  Called after _mp_backtest_worker returns — keeps all DB writes sequential
  in the main thread to avoid SQLite contention.
  """
  run = BacktestRun(
    strategy_id=strategy_id,
    batch_id=batch_id,
    symbol=result["symbol"],
    timeframe=timeframe,
    start_date=result["start_date"],
    end_date=result["end_date"],
    parameters=json.dumps({"direction_filter": direction_filter, "inputs": inputs}),
    metrics=json.dumps(result["metrics"]),
    status="completed",
  )
  db.add(run)
  db.flush()
  store_trades(db, run.id, result["symbol"], result["trades"], initial_capital)
  db.commit()


def _run_individual_batch(
  db: Session, batch_id: int, python_source: str, symbols: list[str],
  timeframe: str, direction_filter: str, initial_capital: float,
  commission_pct: float, leverage: int, inputs: dict,
  max_pyramiding: int = 1, warmup_bars: int = 0,
  extra_timeframes: list[str] | None = None, risk_pct: float = 0.0,
  slippage_mode: str = "pct", slippage_value: float = 0.0,
  risk_mode: str = "pct", risk_value: float = 0.0,
  martingale_enabled: bool = False, martingale_type: str = "anti",
  martingale_multiplier: float = 2.0, martingale_max_streak: int = 3,
  vol_filter_enabled: bool = False, vol_filter_atr_ceiling: float = 0.0,
  vol_filter_atr_length: int = 14,
  strategy_risk_mode: str = "fixed", strategy_risk_value: float = 0.0,
  min_positions: int = 0,
  funding_rate_daily_pct: float = 0.0, spread_pct: float = 0.0,
  process_orders_on_close: bool = False,
  instrument_specs: dict[str, dict] | None = None,
  expected_live_size: float = 0.0,
  max_margin_per_trade: float = 0.0,
  margin_filter_tolerance: float = 10.0,
  bybit_reject_pct: float = 0.0,
):
  """Run backtest for each symbol independently."""
  from concurrent.futures import ThreadPoolExecutor
  all_metrics: list[dict] = []
  reject_filtered: list[str] = []  # assets dropped by bybit reject % filter
  size_filtered: list[str] = []  # assets dropped by expected_live_size filter
  margin_filtered: list[str] = []  # assets dropped by margin efficiency filter

  # --- Phase 1: Pre-load candle data (per-symbol cache, only loads what's missing) ---
  _now_mono = _time.monotonic()
  preloaded: dict[str, "pd.DataFrame"] = {}
  preloaded_extra: dict[str, dict[str, "pd.DataFrame"]] = {}
  symbols_to_load: list[str] = []

  # Check which symbols are already cached (and still fresh)
  with _candle_cache_lock:
    # Purge expired entries across ALL cached symbols (lazy housekeeping)
    _expired_primary = [k for k, v in _candle_cache_primary.items()
                        if _now_mono - v["ts"] > _CACHE_TTL_SECONDS]
    for k in _expired_primary:
      del _candle_cache_primary[k]
    _expired_extra = [k for k, v in _candle_cache_extra.items()
                      if _now_mono - v["ts"] > _CACHE_TTL_SECONDS]
    for k in _expired_extra:
      del _candle_cache_extra[k]

    for sym in symbols:
      _pk = (sym, timeframe)
      _cached = _candle_cache_primary.get(_pk)
      if _cached:
        # Cache hit — refresh TTL and use it
        _cached["ts"] = _now_mono
        preloaded[sym] = _cached["df"]
        # Also grab any cached extra TFs for this symbol
        if extra_timeframes:
          _extras = {}
          for etf in extra_timeframes:
            _ek = (sym, timeframe, etf)
            _ecached = _candle_cache_extra.get(_ek)
            if _ecached:
              _ecached["ts"] = _now_mono
              _extras[etf] = _ecached["df"]
          if _extras:
            preloaded_extra[sym] = _extras
          # If some extra TFs are missing, need to load this symbol
          if extra_timeframes and len(_extras) < len(extra_timeframes):
            symbols_to_load.append(sym)
      else:
        symbols_to_load.append(sym)

  _cache_hits = len(symbols) - len(symbols_to_load)
  if symbols_to_load:
    logger.info("Batch %d: %d/%d symbols cached, loading %d from DB...",
                batch_id, _cache_hits, len(symbols), len(symbols_to_load))
    _safe_progress(batch_id)["phase"] = "loading"
    _safe_progress(batch_id)["current_symbol"] = "Loading candle data..."

    def _load_one(sym: str):
      """Load primary + extra TF candles for one symbol (thread-safe: own DB session)."""
      from backend.db.database import SessionLocal
      thread_db = SessionLocal()
      try:
        primary = _to_nyt(_load_candles_df(thread_db, sym, timeframe))
        extras = {}
        if extra_timeframes:
          for tf in extra_timeframes:
            try:
              extras[tf] = _to_nyt(_load_candles_df(thread_db, sym, tf))
            except ValueError:
              pass
        return sym, primary, extras
      except ValueError:
        return sym, None, {}
      finally:
        thread_db.close()

    load_workers = min(8, len(symbols_to_load))
    with ThreadPoolExecutor(max_workers=load_workers) as pool:
      for sym, df, extras in pool.map(_load_one, symbols_to_load):
        if _is_cancelled(batch_id):
          break
        if df is not None:
          preloaded[sym] = df
          if extras:
            preloaded_extra[sym] = extras
          # Store each symbol individually in cache
          with _candle_cache_lock:
            _candle_cache_primary[(sym, timeframe)] = {"df": df, "ts": _time.monotonic()}
            for etf, edf in extras.items():
              _candle_cache_extra[(sym, timeframe, etf)] = {"df": edf, "ts": _time.monotonic()}
        else:
          _safe_progress(batch_id)["symbol_statuses"][sym] = "error"
    logger.info("Batch %d: candle data ready — %d from cache, %d freshly loaded",
                batch_id, _cache_hits, len(symbols_to_load))
  else:
    logger.info("Batch %d: all %d symbols served from cache", batch_id, len(symbols))

  _safe_progress(batch_id)["phase"] = "backtesting"

  # --- Phase 2: Run backtests with ThreadPoolExecutor (shared memory, no pickle overhead) ---
  from concurrent.futures import ThreadPoolExecutor, as_completed

  strategy_id = db.query(BatchBacktestRun).filter(
    BatchBacktestRun.id == batch_id
  ).first().strategy_id

  # Snapshot inputs at batch start — deep-copy to prevent any aliasing across
  # sequential batches that share the same in-memory queue thread.
  import copy as _copy
  batch_inputs = _copy.deepcopy(inputs)
  logger.info(
    "Batch %d: frozen inputs hash=%s sample=%s",
    batch_id,
    hash(frozenset(batch_inputs.items())) if batch_inputs else 0,
    {k: v for k, v in list(batch_inputs.items())[:5]},
  )

  # Build work items — skip symbols with no data
  work_items = []
  for sym in symbols:
    if sym not in preloaded:
      _safe_progress(batch_id)["symbol_statuses"][sym] = "error"
      all_metrics.append({"symbol": sym, "total_trades": 0, "net_profit": 0, "error": "No data"})
      continue
    _safe_progress(batch_id)["symbol_statuses"][sym] = "queued"
    work_items.append({
      "python_source": python_source, "symbol": sym,
      "df": preloaded[sym],
      "extra_tf_dfs": preloaded_extra.get(sym),
      "timeframe": timeframe, "direction_filter": direction_filter,
      "inputs": batch_inputs,
      "initial_capital": initial_capital, "commission_pct": commission_pct,
      "leverage": leverage, "max_pyramiding": max_pyramiding,
      "warmup_bars": warmup_bars, "risk_pct": risk_pct,
      "slippage_mode": slippage_mode, "slippage_value": slippage_value,
      "risk_mode": risk_mode, "risk_value": risk_value,
      "strategy_risk_mode": strategy_risk_mode, "strategy_risk_value": strategy_risk_value,
      "martingale_enabled": martingale_enabled, "martingale_type": martingale_type,
      "martingale_multiplier": martingale_multiplier, "martingale_max_streak": martingale_max_streak,
      "vol_filter_enabled": vol_filter_enabled, "vol_filter_atr_ceiling": vol_filter_atr_ceiling,
      "vol_filter_atr_length": vol_filter_atr_length,
      "funding_rate_daily_pct": funding_rate_daily_pct, "spread_pct": spread_pct,
      "process_orders_on_close": process_orders_on_close,
      "pool_size": len(symbols),
      "bybit_lot_size": (instrument_specs or {}).get(sym, {}).get("lot_size", 0.0),
      "bybit_min_qty": (instrument_specs or {}).get(sym, {}).get("min_qty", 0.0),
      "bybit_max_qty": (instrument_specs or {}).get(sym, {}).get("max_qty", 0.0),
    })

  num_workers = min(12, len(work_items)) if work_items else 1
  logger.info("Batch %d: running %d symbols with %d parallel threads",
              batch_id, len(work_items), num_workers)

  completed_count = len(all_metrics)  # pre-skipped symbols already counted
  pool = ThreadPoolExecutor(max_workers=num_workers)
  try:
    futures = {pool.submit(_mp_backtest_worker, item): item["symbol"]
               for item in work_items}
    try:
      for future in as_completed(futures, timeout=3600):  # 60-min safety cap
        sym = futures[future]
        try:
          result = future.result(timeout=300)  # 5-min cap per symbol
        except Exception as e:
          logger.error("Batch %d process error for %s: %s", batch_id, sym, e)
          result = {"symbol": sym, "metrics": {"total_trades": 0, "net_profit": 0},
                    "trades": [], "error": str(e)}

        # Expected-live-size post-filter: scale trade qtys to live risk,
        # drop asset if too many entries fall below Bybit min_qty.
        if (not result.get("error") and expected_live_size > 0
            and risk_value > 0 and instrument_specs):
          _sym_spec = instrument_specs.get(result["symbol"], {})
          _min_qty = _sym_spec.get("min_qty", 0)
          if _min_qty > 0 and result.get("trades"):
            _scale = expected_live_size / risk_value
            _entries = [t for t in result["trades"]
                        if getattr(t, "action", "") == "entry"]
            if _entries:
              _n_fail = sum(1 for t in _entries
                            if abs(t.qty) * _scale < _min_qty)
              if _n_fail > 0:
                logger.info(
                  "Live-size filter: %s dropped — %d/%d entries below "
                  "min_qty (%.6f) at $%.0f live risk (scale=%.2fx)",
                  result["symbol"], _n_fail, len(_entries),
                  _min_qty, expected_live_size, _scale,
                )
                size_filtered.append(result["symbol"])
                _safe_progress(batch_id)["symbol_statuses"][result["symbol"]] = "size_filtered"
                all_metrics.append({
                  "symbol": result["symbol"],
                  "total_trades": 0, "net_profit": 0,
                  "size_filtered": True,
                })
                completed_count += 1
                _safe_progress(batch_id)["completed_symbols"] = completed_count
                _safe_progress(batch_id)["current_symbol"] = result["symbol"]
                _sync_progress_to_db(batch_id)
                continue

        # Margin-efficiency post-filter: drop asset if too many entries
        # require more margin than the user's cap.
        if (not result.get("error") and max_margin_per_trade > 0
            and expected_live_size > 0 and risk_value > 0 and instrument_specs):
          _sym_spec = instrument_specs.get(result["symbol"], {})
          _bybit_max_lev = _sym_spec.get("max_leverage", 100)
          # Use the asset's maximum allowed leverage for margin calculation —
          # in live trading the user can set per-asset leverage to the max.
          _eff_lev = _bybit_max_lev
          if _eff_lev > 0:
            _scale = expected_live_size / risk_value
            _entries = [t for t in result["trades"]
                        if getattr(t, "action", "") == "entry"]
            if _entries:
              _n_over = 0
              for t in _entries:
                _notional = abs(t.qty) * _scale * t.price
                _margin = _notional / _eff_lev
                if _margin > max_margin_per_trade:
                  _n_over += 1
              _pct_over = (_n_over / len(_entries)) * 100
              if margin_filter_tolerance > 0 and _pct_over > margin_filter_tolerance:
                logger.info(
                  "Margin filter: %s dropped — %d/%d entries (%.0f%%) exceed "
                  "$%.0f margin cap (eff_lev=%.1f, bybit_max=%.0f)",
                  result["symbol"], _n_over, len(_entries), _pct_over,
                  max_margin_per_trade, _eff_lev, _bybit_max_lev,
                )
                margin_filtered.append(result["symbol"])
                _safe_progress(batch_id)["symbol_statuses"][result["symbol"]] = "margin_filtered"
                all_metrics.append({
                  "symbol": result["symbol"],
                  "total_trades": 0, "net_profit": 0,
                  "margin_filtered": True,
                })
                completed_count += 1
                _safe_progress(batch_id)["completed_symbols"] = completed_count
                _safe_progress(batch_id)["current_symbol"] = result["symbol"]
                _sync_progress_to_db(batch_id)
                continue

        # Write result to DB in main thread (no SQLite contention)
        if not result.get("error"):
          _store_batch_result(
            db, batch_id, strategy_id, result,
            direction_filter, batch_inputs, initial_capital, timeframe,
          )
          _safe_progress(batch_id)["symbol_statuses"][result["symbol"]] = "completed"
        else:
          _safe_progress(batch_id)["symbol_statuses"][result["symbol"]] = "error"
          logger.error("Batch %d symbol %s failed: %s",
                       batch_id, result["symbol"], result.get("error"))

        all_metrics.append({"symbol": result["symbol"], **result.get("metrics", {})})
        completed_count += 1
        _safe_progress(batch_id)["completed_symbols"] = completed_count
        _safe_progress(batch_id)["current_symbol"] = result["symbol"]

        # Update batch record with progress
        try:
          batch_rec = db.query(BatchBacktestRun).filter(
            BatchBacktestRun.id == batch_id
          ).first()
          if batch_rec:
            batch_rec.completed_symbols = completed_count
            db.commit()
        except Exception as _exc:
          logger.debug("swallowed: %s", _exc)
        _sync_progress_to_db(batch_id)

        if _is_cancelled(batch_id):
          break
    except TimeoutError:
      logger.error("Batch %d: ThreadPoolExecutor timed out after 60 min — aborting", batch_id)
  finally:
    pool.shutdown(wait=False, cancel_futures=True)

  # If cancelled, finalize and exit early
  if _is_cancelled(batch_id):
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    if batch and batch.status != "cancelled":
      batch.status = "cancelled"
      db.commit()
    _safe_progress(batch_id)["status"] = "cancelled"
    _sync_progress_to_db(batch_id)
    _cancelled_batches.discard(batch_id)
    logger.info("Batch %d cancelled — %d/%d symbols completed", batch_id, len(all_metrics), len(symbols))
    return

  # Post-backtest filter: exclude symbols with too few trades
  filtered_metrics = all_metrics
  skipped_low_trades = 0
  if min_positions > 0:
    filtered_metrics = []
    for m in all_metrics:
      if m.get("total_trades", 0) < min_positions:
        skipped_low_trades += 1
        logger.info("Filtering %s — %d trades below minimum %d",
                     m.get("symbol", "?"), m.get("total_trades", 0), min_positions)
        # Mark the BacktestRun as filtered so it's excluded from results
        run = (
          db.query(BacktestRun)
          .filter(BacktestRun.batch_id == batch_id, BacktestRun.symbol == m.get("symbol"))
          .first()
        )
        if run:
          run.status = "filtered"
          db.commit()
      else:
        filtered_metrics.append(m)

  # Post-backtest filter: exclude symbols where too many trades were rejected by Bybit min qty
  if bybit_reject_pct > 0:
    next_metrics = []
    for m in filtered_metrics:
      rejected = m.get("rejected_below_min_qty", 0)
      total = m.get("total_trades", 0)
      attempted = total + rejected
      if attempted > 0:
        pct_rejected = (rejected / attempted) * 100
        if pct_rejected >= bybit_reject_pct:
          sym = m.get("symbol", "?")
          reject_filtered.append(sym)
          logger.info("Bybit reject filter: %s dropped — %d/%d trades (%.1f%%) rejected (threshold %.1f%%)",
                      sym, rejected, attempted, pct_rejected, bybit_reject_pct)
          run = (
            db.query(BacktestRun)
            .filter(BacktestRun.batch_id == batch_id, BacktestRun.symbol == sym)
            .first()
          )
          if run:
            run.status = "filtered"
            db.commit()
          _safe_progress(batch_id)["symbol_statuses"][sym] = "reject_filtered"
          continue
      next_metrics.append(m)
    filtered_metrics = next_metrics

  agg = _aggregate_metrics(filtered_metrics)
  if skipped_low_trades > 0:
    agg["skipped_low_trades"] = skipped_low_trades
  if size_filtered:
    agg["size_filtered_count"] = len(size_filtered)
    agg["size_filtered_symbols"] = size_filtered
  if margin_filtered:
    agg["margin_filtered_count"] = len(margin_filtered)
    agg["margin_filtered_symbols"] = margin_filtered
  if reject_filtered:
    agg["reject_filtered_count"] = len(reject_filtered)
    agg["reject_filtered_symbols"] = reject_filtered
  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if batch:
    batch.status = "completed"
    batch.aggregate_metrics = json.dumps(sanitize_for_json(agg))
    db.commit()

  _safe_progress(batch_id)["status"] = "completed"
  _safe_progress(batch_id)["size_filtered_count"] = len(size_filtered)
  _safe_progress(batch_id)["margin_filtered_count"] = len(margin_filtered)
  _safe_progress(batch_id)["reject_filtered_count"] = len(reject_filtered)
  _sync_progress_to_db(batch_id)
  logger.info("Batch %d completed: %d symbols (%d filtered by min_positions, %d by live-size, %d by margin, %d by reject-pct)",
              batch_id, len(symbols), skipped_low_trades, len(size_filtered), len(margin_filtered), len(reject_filtered))


def _run_portfolio_batch(
  db: Session, batch_id: int, python_source: str, symbols: list[str],
  timeframe: str, direction_filter: str, initial_capital: float,
  commission_pct: float, leverage: int, inputs: dict,
  max_pyramiding: int = 1, warmup_bars: int = 0,
  extra_timeframes: list[str] | None = None, risk_pct: float = 0.0,
  slippage_mode: str = "pct", slippage_value: float = 0.0,
  risk_mode: str = "pct", risk_value: float = 0.0,
  martingale_enabled: bool = False, martingale_type: str = "anti",
  martingale_multiplier: float = 2.0, martingale_max_streak: int = 3,
  vol_filter_enabled: bool = False, vol_filter_atr_ceiling: float = 0.0,
  vol_filter_atr_length: int = 14,
  strategy_risk_mode: str = "fixed", strategy_risk_value: float = 0.0,
  min_positions: int = 0,
  funding_rate_daily_pct: float = 0.0, spread_pct: float = 0.0,
  process_orders_on_close: bool = False,
  instrument_specs: dict[str, dict] | None = None,
  expected_live_size: float = 0.0,
):
  """Run portfolio backtest — shared equity across all symbols."""
  from backend.backtester.engine import BacktestConfig
  from backend.backtester.portfolio_engine import run_portfolio_backtest
  from backend.backtester.trade_storage import store_trades

  # Load all symbol DataFrames
  symbol_dfs: dict[str, "pd.DataFrame"] = {}
  for i, symbol in enumerate(symbols):
    if _is_cancelled(batch_id):
      logger.info("Batch %d (portfolio) cancelled during data loading", batch_id)
      break

    _safe_progress(batch_id)["current_symbol"] = symbol
    _safe_progress(batch_id)["symbol_statuses"][symbol] = "loading"
    try:
      symbol_dfs[symbol] = _to_nyt(_load_candles_df(db, symbol, timeframe))
    except ValueError:
      logger.warning("No data for %s %s — skipping", symbol, timeframe)
      _safe_progress(batch_id)["symbol_statuses"][symbol] = "error"

  # Load extra timeframe data for each symbol
  extra_tf_dfs: dict[str, dict[str, "pd.DataFrame"]] = {}
  if extra_timeframes:
    for symbol in symbol_dfs:
      sym_tfs: dict[str, "pd.DataFrame"] = {}
      for tf in extra_timeframes:
        try:
          sym_tfs[tf] = _to_nyt(_load_candles_df(db, symbol, tf))
        except ValueError:
          logger.warning("No %s data for %s — skipping", tf, symbol)
      if sym_tfs:
        extra_tf_dfs[symbol] = sym_tfs
    logger.info("Loaded extra TFs %s for %d symbols", extra_timeframes, len(extra_tf_dfs))

  # If cancelled during data loading, exit early
  if _is_cancelled(batch_id):
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    if batch and batch.status != "cancelled":
      batch.status = "cancelled"
      db.commit()
    _safe_progress(batch_id)["status"] = "cancelled"
    _sync_progress_to_db(batch_id)
    _cancelled_batches.discard(batch_id)
    return

  # Mark all loaded symbols as running
  for symbol in symbol_dfs:
    _safe_progress(batch_id)["symbol_statuses"][symbol] = "running"

  # Each batch is already single-direction (long_plus_short is split at start_batch)
  _ps = dict(
    risk_mode=risk_mode, risk_value=risk_value,
    strategy_risk_mode=strategy_risk_mode, strategy_risk_value=strategy_risk_value,
    martingale_enabled=martingale_enabled, martingale_type=martingale_type,
    martingale_multiplier=martingale_multiplier, martingale_max_streak=martingale_max_streak,
    vol_filter_enabled=vol_filter_enabled, vol_filter_atr_ceiling=vol_filter_atr_ceiling,
    vol_filter_atr_length=vol_filter_atr_length,
    process_orders_on_close=process_orders_on_close,
  )

  config = BacktestConfig(
    initial_capital=initial_capital, commission_pct=commission_pct,
    slippage_mode=slippage_mode, slippage_value=slippage_value,
    leverage=leverage, direction_filter=direction_filter,
    max_pyramiding=max_pyramiding, warmup_bars=warmup_bars,
    risk_pct=risk_pct,
    funding_rate_daily_pct=funding_rate_daily_pct, spread_pct=spread_pct,
    **_ps,
  )

  result = run_portfolio_backtest(
    python_source, symbol_dfs, config, inputs=inputs,
    extra_tf_dfs=extra_tf_dfs if extra_tf_dfs else None,
    timeframe=timeframe,
  )

  # Store per-symbol results as child BacktestRun records
  strategy_id = db.query(BatchBacktestRun).filter(
    BatchBacktestRun.id == batch_id
  ).first().strategy_id

  all_metrics: list[dict] = []
  for i, (symbol, metrics) in enumerate(result.per_symbol_metrics.items()):
    df = symbol_dfs[symbol]
    run = BacktestRun(
      strategy_id=strategy_id, batch_id=batch_id,
      symbol=symbol, timeframe=timeframe,
      start_date=df.index[0], end_date=df.index[-1],
      parameters=json.dumps({"direction_filter": direction_filter, "inputs": inputs, "mode": "portfolio"}),
      metrics=json.dumps(sanitize_for_json(metrics)),
      status="completed",
    )
    db.add(run)
    db.flush()
    trades = result.per_symbol_trades.get(symbol, [])
    store_trades(db, run.id, symbol, trades, initial_capital)
    db.commit()

    # Compute structural metrics (OHLCV + PnL)
    all_structural = {}
    try:
      close_prices = symbol_dfs[symbol]["close"].values
      ohlcv_metrics = _compute_structural_metrics_from_prices(
        close_prices, pool_size=len(result.per_symbol_metrics),
      )
      if ohlcv_metrics:
        metrics.update(ohlcv_metrics)
        all_structural.update(ohlcv_metrics)
    except Exception as e:
      logger.warning("OHLCV structural metrics failed for %s (portfolio): %s", symbol, e)
    try:
      pnl_metrics = _compute_structural_metrics_from_pnl(
        db, run.id, pool_size=len(result.per_symbol_metrics),
      )
      if pnl_metrics:
        metrics.update(pnl_metrics)
        all_structural.update(pnl_metrics)
    except Exception as e:
      logger.warning("PnL structural metrics failed for %s (portfolio): %s", symbol, e)
    if all_structural:
      merged = json.loads(run.metrics) if run.metrics else {}
      merged.update(all_structural)
      run.metrics = json.dumps(sanitize_for_json(merged))
      db.commit()

    all_metrics.append({"symbol": symbol, **metrics})
    _safe_progress(batch_id)["symbol_statuses"][symbol] = "completed"
    _safe_progress(batch_id)["completed_symbols"] = i + 1

  # Post-backtest filter: exclude symbols with too few trades
  filtered_metrics = all_metrics
  skipped_low_trades = 0
  if min_positions > 0:
    filtered_metrics = []
    for m in all_metrics:
      if m.get("total_trades", 0) < min_positions:
        skipped_low_trades += 1
        logger.info("Filtering %s — %d trades below minimum %d",
                     m.get("symbol", "?"), m.get("total_trades", 0), min_positions)
        run = (
          db.query(BacktestRun)
          .filter(BacktestRun.batch_id == batch_id, BacktestRun.symbol == m.get("symbol"))
          .first()
        )
        if run:
          run.status = "filtered"
          db.commit()
      else:
        filtered_metrics.append(m)

  # Store aggregate with combined portfolio metrics
  agg = _aggregate_metrics(filtered_metrics)
  if skipped_low_trades > 0:
    agg["skipped_low_trades"] = skipped_low_trades
  agg["portfolio_net_profit"] = result.combined_metrics.get("net_profit", 0)
  agg["portfolio_max_drawdown_pct"] = result.combined_metrics.get("max_drawdown_pct", 0)
  agg["portfolio_sharpe"] = result.combined_metrics.get("sharpe_ratio", 0)
  agg["portfolio_win_rate"] = result.combined_metrics.get("win_rate", 0)
  agg["mode"] = "portfolio"

  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
  if batch:
    batch.status = "completed"
    batch.completed_symbols = len(symbol_dfs)
    batch.aggregate_metrics = json.dumps(sanitize_for_json(agg))
    db.commit()

  _safe_progress(batch_id)["status"] = "completed"
  _sync_progress_to_db(batch_id)
  logger.info("Portfolio batch %d completed: %d symbols (%d filtered by min_positions)",
              batch_id, len(symbol_dfs), skipped_low_trades)


def _compute_structural_metrics_from_prices(
  close_prices: "np.ndarray",
  pool_size: int = 20,
) -> dict:
  """Compute structural quality metrics from raw OHLCV close prices.

  Uses log returns of the asset's close prices — measures the ASSET's
  inherent trending/mean-reverting behavior, independent of any strategy.
  Every asset with candle data gets graded (no trade-count thresholds).

  All sub-metrics are computed ONCE and composite scores (trend_score,
  meanrev_score) are inlined to avoid redundant hurst/TER/AC/crossing_rate
  recomputation (previously called 3x each via wrapper functions).
  """
  import numpy as np
  from backend.analysis.seed_ranker import (
      trend_efficiency_ratio, hurst_exponent, gain_to_pain_ratio,
      autocorrelation, deflated_sharpe,
      variance_ratio, half_life, _crossing_rate,
      grade_letter, classify_regime,
  )

  if len(close_prices) < 50:
    return {}

  # Log returns of close prices
  close = close_prices.astype(float)
  close = close[close > 0]  # drop any zero/negative prices
  if len(close) < 50:
    return {}
  returns = np.diff(np.log(close))

  # Compute each sub-metric ONCE
  ter = trend_efficiency_ratio(close)  # TER on raw prices (stored metric)
  h = hurst_exponent(returns)
  gpr = gain_to_pain_ratio(returns)
  ac1 = autocorrelation(returns, lag=1)
  dsr = deflated_sharpe(returns, n_trials=pool_size)
  vr = variance_ratio(returns, lag=5)
  hl = half_life(returns)
  cr = _crossing_rate(returns)

  # TER on cumulative log-returns (used by trend_score and MRI formulas)
  equity = np.cumsum(returns)
  ter_equity = trend_efficiency_ratio(equity)

  # Inline MRI = -AC(1) * (1 - TER_equity) * crossing_rate (if AC < 0)
  if len(returns) >= 20 and ac1 < 0:
    mri = float(np.clip((-ac1) * (1.0 - ter_equity) * cr, 0.0, 1.0))
  else:
    mri = 0.0

  # Inline trend_score formula (uses TER on equity, not close)
  sig_gpr = 1.0 / (1.0 + np.exp(-gpr))
  t_score = float(np.clip(
    0.30 * ter_equity
    + 0.25 * max(0.0, (h - 0.5)) * 2.0
    + 0.25 * sig_gpr
    + 0.20 * max(0.0, ac1),
    0.0, 1.0,
  ))

  # Inline meanrev_score formula
  norm_hl = 1.0 / (1.0 + hl / 10.0) if hl < float('inf') else 0.0
  m_score = float(np.clip(
    0.25 * mri
    + 0.25 * max(0.0, (0.5 - h)) * 2.0
    + 0.20 * max(0.0, 1.0 - vr)
    + 0.15 * norm_hl
    + 0.15 * cr,
    0.0, 1.0,
  ))

  regime = classify_regime(t_score, m_score)

  return {
    "regime": regime,
    "trend_score": round(t_score, 4),
    "trend_grade": grade_letter(t_score),
    "meanrev_score": round(m_score, 4),
    "meanrev_grade": grade_letter(m_score),
    "TER": round(ter, 4),
    "Hurst": round(h, 4),
    "GPR": round(gpr, 4),
    "AC1": round(ac1, 4),
    "DSR": round(dsr, 4),
    "MRI": round(mri, 4),
    "VR5": round(vr, 4),
    "HalfLife": round(hl, 1) if hl < float('inf') else 999.0,
    "CrossRate": round(cr, 4),
  }


def _compute_structural_metrics_from_pnl(
  db: "Session",
  run_id: int,
  pool_size: int = 20,
  trade_fills: "list | None" = None,
) -> dict:
  """Compute structural quality metrics from strategy trade PnL.

  Measures how the STRATEGY's returns behave on this asset — complements
  the OHLCV-based metrics which measure the asset's inherent behavior.
  Requires at least 3 trades (Fix 4: lowered from 5).
  All keys are prefixed with 'pnl_' to avoid collision with OHLCV metrics.

  If trade_fills is provided, uses in-memory OrderFill list directly
  (avoids re-querying trades from DB that were just committed).
  """
  import numpy as np
  import pandas as pd
  from backend.analysis.seed_ranker import (
      trend_efficiency_ratio, hurst_exponent, gain_to_pain_ratio,
      autocorrelation, deflated_sharpe,
      variance_ratio, half_life, _crossing_rate,
      grade_letter, classify_regime,
  )

  # Build trade data from in-memory fills or DB query
  if trade_fills is not None:
    # Use in-memory OrderFill list — skip DB query
    exit_fills = [f for f in trade_fills if f.action == "exit"]
    if len(exit_fills) < 3:
      return {}
    trade_data = [(f.timestamp, f.pnl) for f in exit_fills]
  else:
    # Fallback: query from DB (used by portfolio mode and batch refresh)
    trades = (
      db.query(BacktestTrade)
      .filter(BacktestTrade.run_id == run_id)
      .order_by(BacktestTrade.exit_time)
      .all()
    )
    if len(trades) < 3:
      return {}
    trade_data = [(t.exit_time, t.net_profit) for t in trades]

  # Build daily PnL series from trade exit dates
  df = pd.DataFrame(trade_data, columns=["date", "pnl"])
  df["date"] = pd.to_datetime(df["date"]).dt.date
  daily_pnl = df.groupby("date")["pnl"].sum()
  if len(daily_pnl) < 10:
    return {}

  pnl = daily_pnl.values.astype(float)
  equity = np.cumsum(pnl)

  # Compute each sub-metric ONCE (no redundant calls via wrapper functions)
  ter = trend_efficiency_ratio(equity)
  h = hurst_exponent(pnl)
  gpr = gain_to_pain_ratio(pnl)
  ac1 = autocorrelation(pnl, lag=1)
  dsr = deflated_sharpe(pnl, n_trials=pool_size)
  vr = variance_ratio(pnl, lag=5)
  hl = half_life(pnl)
  cr = _crossing_rate(pnl)

  # Inline MRI = -AC(1) * (1 - TER) * crossing_rate (if AC < 0)
  if len(pnl) >= 20 and ac1 < 0:
    mri = float(np.clip((-ac1) * (1.0 - ter) * cr, 0.0, 1.0))
  else:
    mri = 0.0

  # Inline trend_score formula (TER on equity = same as stored ter)
  sig_gpr = 1.0 / (1.0 + np.exp(-gpr))
  t_score = float(np.clip(
    0.30 * ter
    + 0.25 * max(0.0, (h - 0.5)) * 2.0
    + 0.25 * sig_gpr
    + 0.20 * max(0.0, ac1),
    0.0, 1.0,
  ))

  # Inline meanrev_score formula
  norm_hl = 1.0 / (1.0 + hl / 10.0) if hl < float('inf') else 0.0
  m_score = float(np.clip(
    0.25 * mri
    + 0.25 * max(0.0, (0.5 - h)) * 2.0
    + 0.20 * max(0.0, 1.0 - vr)
    + 0.15 * norm_hl
    + 0.15 * cr,
    0.0, 1.0,
  ))

  regime = classify_regime(t_score, m_score)

  return {
    "pnl_regime": regime,
    "pnl_trend_score": round(t_score, 4),
    "pnl_trend_grade": grade_letter(t_score),
    "pnl_meanrev_score": round(m_score, 4),
    "pnl_meanrev_grade": grade_letter(m_score),
    "pnl_TER": round(ter, 4),
    "pnl_Hurst": round(h, 4),
    "pnl_GPR": round(gpr, 4),
    "pnl_AC1": round(ac1, 4),
    "pnl_DSR": round(dsr, 4),
    "pnl_MRI": round(mri, 4),
    "pnl_VR5": round(vr, 4),
    "pnl_HalfLife": round(hl, 1) if hl < float('inf') else 999.0,
    "pnl_CrossRate": round(cr, 4),
  }


def _run_single_symbol(
  db: Session, batch_id: int, python_source: str, symbol: str,
  timeframe: str, direction_filter: str, initial_capital: float,
  commission_pct: float, leverage: int, inputs: dict,
  max_pyramiding: int = 1, warmup_bars: int = 0,
  extra_timeframes: list[str] | None = None,
  risk_pct: float = 0.0,
  slippage_mode: str = "pct", slippage_value: float = 0.0,
  risk_mode: str = "pct", risk_value: float = 0.0,
  martingale_enabled: bool = False, martingale_type: str = "anti",
  martingale_multiplier: float = 2.0, martingale_max_streak: int = 3,
  vol_filter_enabled: bool = False, vol_filter_atr_ceiling: float = 0.0,
  vol_filter_atr_length: int = 14,
  strategy_risk_mode: str = "fixed", strategy_risk_value: float = 0.0,
  funding_rate_daily_pct: float = 0.0, spread_pct: float = 0.0,
  process_orders_on_close: bool = False,
  _preloaded_df: "pd.DataFrame | None" = None,
  _preloaded_extra: "dict[str, pd.DataFrame] | None" = None,
) -> tuple[dict, list]:
  """Run backtest for one symbol, store results, return (metrics, trades)."""
  from backend.backtester.engine import BacktestConfig, BacktestEngine

  # Use pre-loaded data if available, otherwise load from DB
  if _preloaded_df is not None:
    df = _preloaded_df
  else:
    df = _to_nyt(_load_candles_df(db, symbol, timeframe))

  # Load extra timeframe data (also convert to NYT)
  if _preloaded_extra is not None:
    extra_tf_dfs = _preloaded_extra
  else:
    extra_tf_dfs = {}
    if extra_timeframes:
      for tf in extra_timeframes:
        try:
          extra_tf_dfs[tf] = _to_nyt(_load_candles_df(db, symbol, tf))
        except ValueError:
          logger.warning("No %s data for %s — skipping", tf, symbol)

  # Position sizing kwargs shared across configs
  _ps = dict(
    risk_mode=risk_mode, risk_value=risk_value,
    strategy_risk_mode=strategy_risk_mode, strategy_risk_value=strategy_risk_value,
    martingale_enabled=martingale_enabled, martingale_type=martingale_type,
    martingale_multiplier=martingale_multiplier, martingale_max_streak=martingale_max_streak,
    vol_filter_enabled=vol_filter_enabled, vol_filter_atr_ceiling=vol_filter_atr_ceiling,
    vol_filter_atr_length=vol_filter_atr_length,
    process_orders_on_close=process_orders_on_close,
  )

  # Each batch is already single-direction (long_plus_short is split at start_batch)
  from backend.utils.safe_exec import safe_exec
  strategy_cls = safe_exec(python_source, label=f"batch_{symbol}")
  if not strategy_cls:
    raise ValueError("Code does not define TranspiledStrategy")

  config = BacktestConfig(
    initial_capital=initial_capital, commission_pct=commission_pct,
    slippage_mode=slippage_mode, slippage_value=slippage_value,
    leverage=leverage, direction_filter=direction_filter,
    max_pyramiding=max_pyramiding, warmup_bars=warmup_bars,
    risk_pct=risk_pct,
    funding_rate_daily_pct=funding_rate_daily_pct, spread_pct=spread_pct,
    **_ps,
  )
  engine = BacktestEngine(config)
  result = engine.run(
    strategy_cls(), df, symbol=symbol, timeframe=timeframe,
    inputs=inputs, extra_timeframes=extra_tf_dfs,
  )

  # Create child BacktestRun record
  run = BacktestRun(
    strategy_id=db.query(BatchBacktestRun).filter(
      BatchBacktestRun.id == batch_id
    ).first().strategy_id,
    batch_id=batch_id,
    symbol=symbol, timeframe=timeframe,
    start_date=df.index[0], end_date=df.index[-1],
    parameters=json.dumps({"direction_filter": direction_filter, "inputs": inputs}),
    metrics=json.dumps(sanitize_for_json(result.metrics)),
    status="completed",
  )
  db.add(run)
  db.flush()

  store_trades(db, run.id, symbol, result.trades, initial_capital)
  db.commit()
  return result.metrics, result.trades


@router.post("/batch/runs/{batch_id}/refresh")
def refresh_batch(
  request: Request,
  batch_id: int,
  db: Session = Depends(get_db),
):
  """Refresh a completed batch with latest data — downloads new candles,
  continues backtests from saved strategy state, creates a new batch."""
  rate_limit(request, max_requests=10, window_seconds=60)
  original = db.query(BatchBacktestRun).filter(
    BatchBacktestRun.id == batch_id, BatchBacktestRun.is_deleted != True,
  ).first()
  if not original:
    raise HTTPException(status_code=404, detail="Batch not found")
  if original.status != "completed":
    raise HTTPException(status_code=400, detail=f"Batch is not completed (status: {original.status})")

  # Load strategy
  strategy = db.query(Strategy).filter(Strategy.id == original.strategy_id).first()
  if not strategy or not strategy.python_source:
    raise HTTPException(status_code=400, detail="Strategy not found or has no Python code")

  # Get child runs (completed, non-filtered)
  child_runs = (
    db.query(BacktestRun)
    .filter(BacktestRun.batch_id == batch_id, BacktestRun.status == "completed")
    .all()
  )
  if not child_runs:
    raise HTTPException(status_code=400, detail="No completed runs in this batch")

  # Extract symbols and per-symbol data
  symbols = []
  old_end_dates: dict[str, str] = {}
  for run in child_runs:
    symbols.append(run.symbol)
    old_end_dates[run.symbol] = run.end_date.isoformat() if run.end_date else ""

  # Find ongoing positions per symbol (force-closed at batch end)
  ongoing_positions: dict[str, list[dict]] = {}
  for run in child_runs:
    ongoing = (
      db.query(BacktestTrade)
      .filter(BacktestTrade.run_id == run.id, BacktestTrade.exit_reason == "backtest_end")
      .all()
    )
    if ongoing:
      ongoing_positions[run.symbol] = [{
        "position_id": t.position_id or f"pos_{t.id}",
        "side": t.side,
        "entry_price": t.entry_price,
        "qty": t.quantity,
        "entry_time": t.entry_time.isoformat() if t.entry_time else None,
        "sl_price": t.sl_price,
        "tp_price": t.tp_price,
      } for t in ongoing]

  # Parse original params
  params = json.loads(original.parameters) if original.parameters else {}
  inputs = params.get("strategy_inputs", {})
  extra_timeframes = params.get("extra_timeframes")

  # Auto-detect extra timeframes from inputs (same as start_batch)
  extra_tfs = set(extra_timeframes or [])
  _TF_MAP = {"1h": "60", "4h": "240", "12h": "720", "1D": "D", "3D": "3D"}
  for param_key in ("RSI Timeframe", "MA Filter Timeframe"):
    tf_label = inputs.get(param_key)
    if tf_label and tf_label != "Current" and tf_label in _TF_MAP:
      extra_tfs.add(_TF_MAP[tf_label])
  resolved_extra_tfs = list(extra_tfs) if extra_tfs else None

  # Create new batch record
  today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
  new_name = f"{original.name or f'Batch #{original.id}'} (\u2192 {today_str})"

  new_batch = BatchBacktestRun(
    strategy_id=original.strategy_id,
    timeframe=original.timeframe,
    direction_filter=original.direction_filter,
    name=new_name,
    source_name=original.source_name or original.name,
    config_group=original.config_group,
    notes=original.notes or "",
    parameters=original.parameters,
    extra_timeframes=json.dumps(sorted(extra_tfs)) if extra_tfs else (original.extra_timeframes or "[]"),
    refreshed_from_id=batch_id,
    total_symbols=len(symbols),
    completed_symbols=0,
    status="queued",
  )
  db.add(new_batch)
  db.commit()
  db.refresh(new_batch)

  _batch_progress[new_batch.id] = {
    "status": "queued",
    "total_symbols": len(symbols),
    "completed_symbols": 0,
    "current_symbol": "",
    "symbol_statuses": {s: "pending" for s in symbols},
    "started_at": datetime.now(timezone.utc).isoformat(),
  }

  # Extract engine config values from params
  strat_risk_mode = "fixed"
  strat_risk_value = 0.0
  if strategy.parameters:
    strat_defaults = {}
    for p in json.loads(strategy.parameters):
      strat_defaults[p["name"]] = p["default"]
    effective_inputs = {**strat_defaults, **inputs}
    rm = effective_inputs.get("Risk Mode", "")
    if "%" in str(rm) or "equity" in str(rm).lower() or "pct" in str(rm).lower():
      strat_risk_mode = "pct"
      strat_risk_value = float(effective_inputs.get("Risk % of Equity", 0) or 0)
    else:
      strat_risk_value = float(effective_inputs.get("Risk Amount ($)", 0) or 0)

  _nb_id = new_batch.id
  _enqueue_batch(
    _nb_id,
    lambda nb_id=_nb_id: _run_refresh_batch(
      nb_id, batch_id, strategy.python_source, symbols,
      original.timeframe, original.direction_filter,
      old_end_dates, ongoing_positions,
      params, resolved_extra_tfs,
      strat_risk_mode, strat_risk_value,
    ),
  )

  return {
    "batch_id": new_batch.id,
    "status": "queued",
    "total_symbols": len(symbols),
    "refreshed_from_id": batch_id,
  }


def _download_refresh_data(symbols: list[str], timeframe: str,
                            extra_timeframes: list[str] | None, batch_id: int):
  """Download latest candles for all symbols/timeframes (incremental)."""
  import asyncio

  from backend.data.bybit_downloader import BybitDownloader
  from backend.data.data_manager import DataManager

  manager = DataManager()
  all_tfs = [timeframe] + (extra_timeframes or [])

  async def _do_downloads():
    downloader = BybitDownloader()
    total = len(all_tfs) * len(symbols)
    done = 0
    for tf in all_tfs:
      for symbol in symbols:
        try:
          latest = manager.get_latest_timestamp(symbol, tf)
          # Naive datetimes from DB are UTC — force tzinfo to avoid local-time shift
          if latest and latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
          since_ms = int(latest.timestamp() * 1000) + 1 if latest else None
          candles = await downloader.fetch_all_klines(symbol, tf, since_ms=since_ms)
          if candles:
            manager.upsert_candles(symbol, tf, candles)
          done += 1
          logger.info("Refresh download %s/%s (%d/%d)", symbol, tf, done, total)
        except Exception as e:
          logger.warning("Failed to download %s/%s: %s", symbol, tf, e)
          done += 1
        await asyncio.sleep(0.2)

  # CS-021: prefer asyncio.run over manual loop lifecycle
  asyncio.run(_do_downloads())
  logger.info("Refresh data download complete for batch %d", batch_id)


def _run_refresh_batch(
  batch_id: int, _original_batch_id: int, python_source: str,
  symbols: list[str], timeframe: str, direction_filter: str,
  old_end_dates: dict[str, str], ongoing_positions: dict[str, list[dict]],
  params: dict,
  extra_timeframes: list[str] | None,
  strategy_risk_mode: str, strategy_risk_value: float,
):
  """Background task: download new data + run continuation backtests."""
  from backend.db.database import SessionLocal
  db = SessionLocal()

  try:
    # Phase 1: Download latest data
    _safe_progress(batch_id)["status"] = "downloading"
    _download_refresh_data(symbols, timeframe, extra_timeframes, batch_id)
    _safe_progress(batch_id)["status"] = "running"

    # Phase 2: Run continuation backtests
    all_metrics: list[dict] = []
    for i, symbol in enumerate(symbols):
      if _is_cancelled(batch_id):
        logger.info("Refresh batch %d cancelled at symbol %d/%d", batch_id, i, len(symbols))
        break

      _safe_progress(batch_id)["current_symbol"] = symbol
      _safe_progress(batch_id)["symbol_statuses"][symbol] = "running"

      try:
        result_metrics = _run_single_symbol_refresh(
          db, batch_id, python_source, symbol, timeframe,
          direction_filter, params,
          old_end_date=old_end_dates.get(symbol, ""),
          ongoing=ongoing_positions.get(symbol, []),
          extra_timeframes=extra_timeframes,
          strategy_risk_mode=strategy_risk_mode,
          strategy_risk_value=strategy_risk_value,
        )
        all_metrics.append({"symbol": symbol, **result_metrics})
        _safe_progress(batch_id)["symbol_statuses"][symbol] = "completed"
      except Exception as e:
        logger.error("Refresh batch %d symbol %s failed: %s", batch_id, symbol, e)
        logger.error(traceback.format_exc())
        _safe_progress(batch_id)["symbol_statuses"][symbol] = "error"
        all_metrics.append({"symbol": symbol, "total_trades": 0, "net_profit": 0, "error": str(e)})

      _safe_progress(batch_id)["completed_symbols"] = i + 1
      batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
      if batch:
        batch.completed_symbols = i + 1
        db.commit()
      _sync_progress_to_db(batch_id)

    # If cancelled, finalize and exit early
    if _is_cancelled(batch_id):
      batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
      if batch and batch.status != "cancelled":
        batch.status = "cancelled"
        db.commit()
      _safe_progress(batch_id)["status"] = "cancelled"
      _sync_progress_to_db(batch_id)
      _cancelled_batches.discard(batch_id)
      return

    # Post-filter by min_positions if original had it
    min_positions = params.get("min_positions", 0)
    filtered_metrics = all_metrics
    skipped_low_trades = 0
    if min_positions > 0:
      filtered_metrics = []
      for m in all_metrics:
        if m.get("total_trades", 0) < min_positions:
          skipped_low_trades += 1
          run = (
            db.query(BacktestRun)
            .filter(BacktestRun.batch_id == batch_id, BacktestRun.symbol == m.get("symbol"))
            .first()
          )
          if run:
            run.status = "filtered"
            db.commit()
        else:
          filtered_metrics.append(m)

    agg = _aggregate_metrics(filtered_metrics)
    if skipped_low_trades > 0:
      agg["skipped_low_trades"] = skipped_low_trades
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    if batch:
      batch.status = "completed"
      batch.aggregate_metrics = json.dumps(sanitize_for_json(agg))
      db.commit()

    _safe_progress(batch_id)["status"] = "completed"
    _sync_progress_to_db(batch_id)
    logger.info("Refresh batch %d completed: %d symbols", batch_id, len(symbols))

  except Exception as e:
    logger.error("Refresh batch %d failed: %s", batch_id, e)
    logger.error(traceback.format_exc())
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    if batch:
      batch.status = "error"
      batch.error_message = str(e)
      db.commit()
    _safe_progress(batch_id)["status"] = "error"
    _sync_progress_to_db(batch_id)
  finally:
    db.close()


def _run_single_symbol_refresh(
  db: Session, batch_id: int, python_source: str, symbol: str,
  timeframe: str, direction_filter: str, params: dict,
  old_end_date: str, ongoing: list[dict],
  extra_timeframes: list[str] | None,
  strategy_risk_mode: str, strategy_risk_value: float,
) -> dict:
  """Run continuation backtest for one symbol from old_end_date forward."""
  import pandas as pd

  from backend.backtester.engine import BacktestConfig, BacktestEngine

  # Load ALL primary TF data from DB (includes new candles)
  df = _to_nyt(_load_candles_df(db, symbol, timeframe))

  # Find boundary: the bar at or just before old_end_date
  if not old_end_date:
    raise ValueError(f"No end date for {symbol}")
  boundary_ts = pd.Timestamp(old_end_date)
  if boundary_ts.tzinfo is None:
    boundary_ts = boundary_ts.tz_localize("America/New_York")
  boundary_idx = df.index.searchsorted(boundary_ts, side="right") - 1
  if boundary_idx < 0 or boundary_idx >= len(df) - 1:
    return {"total_trades": 0, "net_profit": 0, "note": "No new data available"}

  # Compute warmup for indicator convergence
  from backend.utils.safe_exec import safe_exec
  strategy_cls = safe_exec(python_source, label="batch_refresh")
  if not strategy_cls:
    raise ValueError("Code does not define TranspiledStrategy")

  # Peek at _warmup_bars from a temporary instance
  inputs = params.get("strategy_inputs", {})
  temp = strategy_cls()
  temp._df = df
  temp._inputs = inputs
  temp._tf_data = {}
  try:
    temp.init()
  except Exception as _exc:
    logger.debug("swallowed: %s", _exc)
  base_warmup = max(200, (temp._warmup_bars or 50) * 4)

  # Slice: base_warmup bars before boundary to end of data
  warmup_start = max(0, boundary_idx + 1 - base_warmup)
  df_slice = df.iloc[warmup_start:]
  actual_warmup = boundary_idx + 1 - warmup_start

  # Load extra TF data — FULL history (precomputed in init())
  extra_tf_dfs: dict[str, pd.DataFrame] = {}
  if extra_timeframes:
    for tf in extra_timeframes:
      try:
        extra_tf_dfs[tf] = _to_nyt(_load_candles_df(db, symbol, tf))
      except ValueError:
        logger.warning("Refresh: no %s data for %s", tf, symbol)

  # Build engine config from saved params
  config = BacktestConfig(
    initial_capital=params.get("initial_capital", 10000),
    commission_pct=params.get("commission_pct", COMMISSION_PCT),
    slippage_mode=params.get("slippage_mode", "pct"),
    slippage_value=params.get("slippage_value", 0.0),
    leverage=params.get("leverage", 1),
    direction_filter=direction_filter,
    max_pyramiding=params.get("max_pyramiding", 1),
    warmup_bars=actual_warmup,
    risk_pct=params.get("risk_pct", 0.0),
    risk_mode=params.get("risk_mode", "pct"),
    risk_value=params.get("risk_value", 0.0),
    strategy_risk_mode=strategy_risk_mode,
    strategy_risk_value=strategy_risk_value,
    martingale_enabled=params.get("martingale_enabled", False),
    martingale_type=params.get("martingale_type", "anti"),
    martingale_multiplier=params.get("martingale_multiplier", 2.0),
    martingale_max_streak=params.get("martingale_max_streak", 3),
    vol_filter_enabled=params.get("vol_filter_enabled", False),
    vol_filter_atr_ceiling=params.get("vol_filter_atr_ceiling", 0.0),
    vol_filter_atr_length=params.get("vol_filter_atr_length", 14),
    funding_rate_daily_pct=params.get("funding_rate_daily_pct", 0.0),
    spread_pct=params.get("spread_pct", 0.0),
    process_orders_on_close=params.get("process_orders_on_close", False),
  )
  engine = BacktestEngine(config)
  result = engine.run(
    strategy_cls(), df_slice, symbol=symbol, timeframe=timeframe,
    inputs=inputs, extra_timeframes=extra_tf_dfs,
    inject_positions=ongoing if ongoing else None,
  )

  # Store results — dates reflect the NEW period only
  first_trading_idx = actual_warmup if actual_warmup < len(df_slice) else len(df_slice) - 1
  first_trading_ts = df_slice.index[first_trading_idx]

  run = BacktestRun(
    strategy_id=db.query(BatchBacktestRun).filter(
      BatchBacktestRun.id == batch_id
    ).first().strategy_id,
    batch_id=batch_id,
    symbol=symbol, timeframe=timeframe,
    start_date=first_trading_ts, end_date=df_slice.index[-1],
    parameters=json.dumps({"direction_filter": direction_filter, "inputs": inputs, "refresh": True}),
    metrics=json.dumps(sanitize_for_json(result.metrics)),
    status="completed",
  )
  db.add(run)
  db.flush()

  store_trades(db, run.id, symbol, result.trades, params.get("initial_capital", 10000))
  db.commit()
  return result.metrics


def _aggregate_metrics(all_metrics: list[dict]) -> dict:
  """Compute portfolio-level stats from per-symbol metrics."""
  valid = [m for m in all_metrics if m.get("total_trades", 0) > 0]
  if not valid:
    return {"total_symbols_tested": len(all_metrics), "profitable_symbols": 0}

  profits = [m.get("net_profit", 0) for m in valid]
  agg = {
    "total_symbols_tested": len(all_metrics),
    "symbols_with_trades": len(valid),
    "profitable_symbols": sum(1 for p in profits if p > 0),
    "total_net_profit": round(sum(profits), 2),
    "avg_net_profit": round(sum(profits) / len(valid), 2),
    "avg_win_rate": round(sum(m.get("win_rate", 0) for m in valid) / len(valid), 2),
    "avg_profit_factor": round(
      sum(pf for m in valid if (pf := m.get("profit_factor", 0)) < 10)
      / max(1, sum(1 for m in valid if m.get("profit_factor", 0) < 10)),
      2,
    ),
    "total_trades_all": sum(m.get("total_trades", 0) for m in valid),
    "avg_max_drawdown_pct": round(sum(m.get("max_drawdown_pct", 0) for m in valid) / len(valid), 2),
    "best_symbol": max(valid, key=lambda m: m.get("net_profit", 0)).get("symbol", ""),
    "worst_symbol": min(valid, key=lambda m: m.get("net_profit", 0)).get("symbol", ""),
    "max_simultaneous_trades": max((m.get("max_simultaneous_trades", 0) for m in valid), default=0),
  }

  # Compute batch grade
  grade, score = _compute_batch_grade(valid, agg)
  agg["batch_grade"] = grade
  agg["batch_grade_score"] = round(score, 3)

  return agg


def _compute_batch_grade(
  valid_metrics: list[dict], agg: dict,
) -> tuple[str, float]:
  """Grade a batch backtest run on how promising the strategy looks across
  the whole basket. Softer than portfolio grading — a PF of 1.4 on the
  basket is excellent, PF below 0.9 is F.

  Scoring components (0-1 each):
    1. Overall avg PF          (30%)
    2. Win rate                (15%)
    3. Total trade count       (15%)
    4. Profitable symbol ratio (20%) — many winners > few big winners
    5. Distribution quality    (20%) — median PF vs mean PF

  Profitability floor: PF >= 1.0 → minimum D-, PF >= 1.3 → minimum D
  """
  import statistics

  avg_pf = agg.get("avg_profit_factor", 0) or 0
  avg_wr = agg.get("avg_win_rate", 0) or 0
  total_trades = agg.get("total_trades_all", 0) or 0
  n_symbols = agg.get("symbols_with_trades", 0) or 0
  n_profitable = agg.get("profitable_symbols", 0) or 0

  # 1. Overall PF score (30%)
  if avg_pf >= 1.5:    pf_score = 1.0
  elif avg_pf >= 1.3:  pf_score = 0.85
  elif avg_pf >= 1.15: pf_score = 0.7
  elif avg_pf >= 1.0:  pf_score = 0.5
  elif avg_pf >= 0.9:  pf_score = 0.3
  else:                pf_score = 0.1

  # 2. Win rate score (15%)
  if avg_wr >= 55:     wr_score = 1.0
  elif avg_wr >= 50:   wr_score = 0.8
  elif avg_wr >= 45:   wr_score = 0.6
  elif avg_wr >= 40:   wr_score = 0.4
  else:                wr_score = 0.2

  # 3. Trade count score (15%)
  if total_trades >= 1000:   tc_score = 1.0
  elif total_trades >= 500:  tc_score = 0.8
  elif total_trades >= 200:  tc_score = 0.6
  elif total_trades >= 100:  tc_score = 0.4
  else:                      tc_score = 0.2

  # 4. Profitable symbol ratio (20%)
  pct_profitable = (n_profitable / n_symbols * 100) if n_symbols > 0 else 0
  if pct_profitable >= 70:   pr_score = 1.0
  elif pct_profitable >= 55: pr_score = 0.8
  elif pct_profitable >= 40: pr_score = 0.6
  elif pct_profitable >= 25: pr_score = 0.4
  else:                      pr_score = 0.2

  # 5. Distribution quality (20%) — median PF across symbols
  # If median is high, many symbols are good (not just one outlier)
  per_sym_pfs = [
    min(m.get("profit_factor", 0), 10.0)
    for m in valid_metrics if m.get("profit_factor") is not None
  ]
  if per_sym_pfs:
    median_pf = statistics.median(per_sym_pfs)
  else:
    median_pf = avg_pf

  if median_pf >= 1.2:    dq_score = 1.0
  elif median_pf >= 1.05: dq_score = 0.75
  elif median_pf >= 0.95: dq_score = 0.5
  elif median_pf >= 0.85: dq_score = 0.35
  else:                   dq_score = 0.15

  # Weighted score
  score = (
    pf_score * 0.30
    + wr_score * 0.15
    + tc_score * 0.15
    + pr_score * 0.20
    + dq_score * 0.20
  )

  # Grade thresholds (softer than portfolio grading)
  if score >= 0.85:    grade = "A+"
  elif score >= 0.75:  grade = "A"
  elif score >= 0.65:  grade = "B+"
  elif score >= 0.55:  grade = "B"
  elif score >= 0.45:  grade = "C+"
  elif score >= 0.35:  grade = "C"
  elif score >= 0.25:  grade = "D"
  elif score >= 0.15:  grade = "D-"
  else:                grade = "F"

  # Profitability floor — a profitable strategy can't get F
  if avg_pf >= 1.3 and grade == "F":
    grade = "D"
  elif avg_pf >= 1.0 and grade in ("F",):
    grade = "D-"

  return grade, score
