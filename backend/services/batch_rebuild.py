"""Bulk rebuild of stale batches — in-place, preserves every important column.

Why this exists:
  The nightly L1 pipeline (`batch_store_refresh`) extends every run forward
  from its saved `strategy_state`. Batches created before the strategy_state
  column existed have `run.strategy_state IS NULL`, so L1 skips them
  forever. This service is the one-off fix: re-run each stale run cold to
  capture state, then L1 takes over.

Design (safety contract):
  * IN-PLACE — we keep the same `run.id` and `run.batch_id` so every
    `PortfolioAsset.batch_id` binding stays valid. No new batches created.
  * ADDITIVE columns untouched: `id`, `batch_id`, `strategy_id`, `symbol`,
    `timeframe`, `start_date`, `parameters`, `created_at`. Only the live-state
    columns are written: `end_date`, `metrics`, `strategy_state`, `status`,
    `error_message`.
  * Trades replaced atomically per run: delete old rows for that run_id,
    insert fresh rows. Other runs' trades are never touched.
  * Never raises from rebuild_all_stale — per-run failures are logged and
    counted in the progress report.

Usage:
  * `find_stale_batches(db)` — returns [{batch_id, name, total_runs,
    stale_runs}, ...] for the UI.
  * `rebuild_all_stale()` — long-running; safe to run in a thread. The
    endpoint layer starts it via BackgroundTasks.
  * `get_progress()` — read-only snapshot for the UI progress bar.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from backend.constants import COMMISSION_PCT
from backend.db.database import SessionLocal
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  BatchBacktestRun,
  Strategy,
)
from backend.utils.candle_loader import load_candles_df, to_nyt

log = logging.getLogger(__name__)


# ── Progress singleton ──────────────────────────────────────────
# One rebuild job at a time. Simple in-memory dict + lock.

_progress_lock = threading.Lock()
_progress: dict = {
  "status": "idle",          # idle | running | completed | error
  "phase": "",               # "" | "downloading" | "loading_candles" | "rebuilding"
  "total_runs": 0,
  "completed_runs": 0,
  "failed_runs": 0,
  "current": "",             # "BTCUSDT (batch #12)"
  "started_at": None,
  "ended_at": None,
  "errors": [],              # [{run_id, symbol, reason}]
  "l0_summary": None,        # set if download_candles_first=True
  "candles_cached": 0,       # how many (symbol, tf) dfs were preloaded
}


def get_progress() -> dict:
  """Read-only snapshot of the current rebuild job (safe to call any time)."""
  with _progress_lock:
    return dict(_progress)


def _progress_set(**fields):
  with _progress_lock:
    _progress.update(fields)


def _progress_bump(**deltas):
  with _progress_lock:
    for k, v in deltas.items():
      _progress[k] = _progress.get(k, 0) + v


# ── Discovery ───────────────────────────────────────────────────

def find_stale_batches(db) -> list[dict]:
  """Return a summary of batches containing any run with NULL strategy_state.

  Only considers active batches (not deleted/archived/compacted). The shape
  matches what the UI wants to display:
    [{"batch_id": 12, "name": "Batch #12", "total_runs": 40, "stale_runs": 40}, ...]
  """
  active_batches = (
    db.query(BatchBacktestRun)
    .filter(
      BatchBacktestRun.is_deleted == False,  # noqa: E712
      BatchBacktestRun.is_archived == False,  # noqa: E712
      BatchBacktestRun.is_compacted == False,  # noqa: E712
    )
    .all()
  )

  out: list[dict] = []
  for b in active_batches:
    runs = (
      db.query(BacktestRun)
      .filter(
        BacktestRun.batch_id == b.id,
        BacktestRun.is_deleted == False,  # noqa: E712
      )
      .all()
    )
    if not runs:
      continue
    stale = [r for r in runs if not r.strategy_state]
    if not stale:
      continue
    out.append({
      "batch_id": b.id,
      "name": b.name or f"Batch #{b.id}",
      "total_runs": len(runs),
      "stale_runs": len(stale),
    })
  return out


# ── Single-run rebuild (cold) ───────────────────────────────────

_MISSING = object()


def _load_pair_cached(
  symbol: str, tf: str, cache: dict | None,
):
  """Return the DataFrame for (symbol, tf), using `cache` when provided.

  Cache misses go to `load_candles_df` via a fresh DB session (thread-safe).
  Misses are memoized as a sentinel so we never repeat a failed load.
  """
  key = (symbol, tf)
  if cache is not None and key in cache:
    hit = cache[key]
    if hit is _MISSING:
      return None
    return hit

  from backend.db.database import SessionLocal
  s = SessionLocal()
  try:
    df = to_nyt(load_candles_df(s, symbol, tf))
  except Exception:
    df = None
  finally:
    s.close()

  if cache is not None:
    cache[key] = df if df is not None else _MISSING
  return df


def rebuild_run(
  db,
  run: BacktestRun,
  candle_cache: dict | None = None,
) -> dict:
  """Cold-rebuild one run in place. Preserves run_id, batch_id, and all
  identity columns. Replaces trades + captures strategy_state.

  `candle_cache` (optional) maps `(symbol, tf) -> DataFrame` and is shared
  across runs so the same (symbol, tf) is only loaded once per job.

  Returns {ok, new_trades, reason?} — never raises.
  """
  from backend.backtester.engine import BacktestConfig, BacktestEngine
  from backend.backtester.trade_storage import store_trades
  from backend.utils.safe_exec import safe_exec

  batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == run.batch_id).first()
  if batch is None or batch.is_deleted or batch.is_archived:
    return {"ok": False, "reason": "batch_inactive"}

  strategy = db.query(Strategy).filter(Strategy.id == run.strategy_id).first()
  if strategy is None or not strategy.python_source:
    return {"ok": False, "reason": "no_strategy_source"}

  # Parse batch + run params (same precedence as L1's replay_run_forward).
  try:
    params = json.loads(batch.parameters or "{}")
  except Exception:
    params = {}
  try:
    run_params = json.loads(run.parameters or "{}")
  except Exception:
    run_params = {}
  inputs = run_params.get("inputs") or params.get("strategy_inputs") or {}
  direction_filter = run_params.get("direction_filter") or batch.direction_filter or "both"
  try:
    extra_timeframes = json.loads(batch.extra_timeframes or "[]")
  except Exception:
    extra_timeframes = []

  # Load candles (cache-aware — shared across runs in the same job)
  df = _load_pair_cached(run.symbol, run.timeframe, candle_cache)
  if df is None:
    return {"ok": False, "reason": "candle_load_failed"}
  if df.empty:
    return {"ok": False, "reason": "no_candles"}

  extra_tf_dfs = {}
  for tf in (extra_timeframes or []):
    edf = _load_pair_cached(run.symbol, tf, candle_cache)
    if edf is None:
      log.warning("batch_rebuild: missing extra-tf %s for %s", tf, run.symbol)
      continue
    extra_tf_dfs[tf] = edf

  strategy_cls = safe_exec(strategy.python_source, label=f"rebuild_{run.symbol}")
  if not strategy_cls:
    return {"ok": False, "reason": "strategy_compile_failed"}

  # Mirror L1's config shape exactly so metrics are comparable.
  config = BacktestConfig(
    initial_capital=params.get("initial_capital", 10000),
    commission_pct=params.get("commission_pct", COMMISSION_PCT),
    slippage_mode=params.get("slippage_mode", "pct"),
    slippage_value=params.get("slippage_value", 0.0),
    leverage=params.get("leverage", 1),
    direction_filter=direction_filter,
    max_pyramiding=params.get("max_pyramiding", 1),
    warmup_bars=params.get("warmup_bars", 0),
    risk_pct=params.get("risk_pct", 0.0),
    risk_mode=params.get("risk_mode", "pct"),
    risk_value=params.get("risk_value", 0.0),
    strategy_risk_mode=params.get("strategy_risk_mode", "fixed"),
    strategy_risk_value=params.get("strategy_risk_value", 10.0),
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
  try:
    result = engine.run(
      strategy_cls(), df,
      symbol=run.symbol, timeframe=run.timeframe,
      inputs=inputs, extra_timeframes=extra_tf_dfs,
    )
  except Exception as e:
    log.warning("batch_rebuild: engine failed run=%d %s: %s", run.id, run.symbol, e)
    return {"ok": False, "reason": f"engine_failed: {e}"}

  # Replace trades in-place (SAME run_id — portfolio bindings unaffected).
  db.query(BacktestTrade).filter(BacktestTrade.run_id == run.id).delete(
    synchronize_session=False,
  )
  new_trades = result.trades or []
  if new_trades:
    store_trades(db, run.id, run.symbol, new_trades, config.initial_capital)

  # Update live-state columns. Everything else untouched.
  from backend.utils.sanitize import sanitize_for_json
  run.end_date = df.index[-1].to_pydatetime().replace(tzinfo=None)
  try:
    run.metrics = json.dumps(sanitize_for_json(result.metrics))
  except Exception as _exc:
    log.debug("swallowed: %s", _exc)
  try:
    run.strategy_state = json.dumps(
      getattr(result, "strategy_state", {}) or {}, default=str
    )
  except Exception as e:
    log.warning("batch_rebuild: state serialize failed run=%d: %s", run.id, e)
  run.status = "completed"
  run.error_message = ""

  db.commit()
  return {"ok": True, "new_trades": len(new_trades)}


# ── Bulk orchestration ──────────────────────────────────────────

def _collect_unique_pairs(db, batch_ids: list[int]) -> set[tuple[str, str]]:
  """Gather every (symbol, tf) pair referenced by the given batches.

  Covers each run's primary timeframe AND the batch's extra_timeframes.
  Used to build a one-shot preload set so the candle DB is touched once
  per unique pair, not once per run.
  """
  pairs: set[tuple[str, str]] = set()
  if not batch_ids:
    return pairs

  runs = (
    db.query(BacktestRun.batch_id, BacktestRun.symbol, BacktestRun.timeframe)
    .filter(
      BacktestRun.batch_id.in_(batch_ids),
      BacktestRun.is_deleted == False,  # noqa: E712
    )
    .all()
  )
  batch_extras: dict[int, list[str]] = {}
  for b in db.query(BatchBacktestRun).filter(BatchBacktestRun.id.in_(batch_ids)).all():
    try:
      batch_extras[b.id] = json.loads(b.extra_timeframes or "[]") or []
    except Exception:
      batch_extras[b.id] = []

  for bid, sym, tf in runs:
    if sym and tf:
      pairs.add((sym, tf))
    for etf in batch_extras.get(bid, []) or []:
      if etf:
        pairs.add((sym, etf))
  return pairs


def _preload_candles(pairs: set[tuple[str, str]]) -> dict:
  """Parallel-load every (symbol, tf) pair with 8 worker threads.

  Each worker opens its own DB session (SQLAlchemy sessions are not
  thread-safe). Failed loads are memoized as `_MISSING` so they aren't
  retried once per run later.
  """
  from concurrent.futures import ThreadPoolExecutor

  cache: dict = {}
  if not pairs:
    return cache
  plist = sorted(pairs)

  def _one(pair):
    sym, tf = pair
    from backend.db.database import SessionLocal
    s = SessionLocal()
    try:
      df = to_nyt(load_candles_df(s, sym, tf))
      return pair, df
    except Exception:
      return pair, None
    finally:
      s.close()

  workers = min(8, len(plist))
  with ThreadPoolExecutor(max_workers=workers) as pool:
    for pair, df in pool.map(_one, plist):
      cache[pair] = df if df is not None else _MISSING

  return cache


def rebuild_all_stale(download_candles_first: bool = False) -> dict:
  """Rebuild every stale run across every active batch. Thread-safe.

  Phases (reported in `_progress.phase`):
    1. "downloading"      — optional L0 candle catch-up (append-only upsert)
    2. "loading_candles"  — parallel-preload every unique (symbol, tf)
    3. "rebuilding"       — cold-rebuild each stale run using the cache

  Never raises. Returns the final progress snapshot.
  """
  with _progress_lock:
    if _progress["status"] == "running":
      return dict(_progress)
    _progress.update({
      "status": "running",
      "phase": "",
      "total_runs": 0,
      "completed_runs": 0,
      "failed_runs": 0,
      "current": "",
      "started_at": datetime.now(timezone.utc).isoformat(),
      "ended_at": None,
      "errors": [],
      "l0_summary": None,
      "candles_cached": 0,
    })

  db = SessionLocal()
  try:
    # --- Phase 1: download candles (optional) ---
    if download_candles_first:
      _progress_set(phase="downloading", current="Downloading new candles from Bybit…")
      try:
        from backend.services.candle_catchup import catchup_candles
        summary = catchup_candles()
        _progress_set(l0_summary=summary)
      except Exception as e:
        log.warning("batch_rebuild: L0 catchup failed: %s", e)
        _progress_set(l0_summary={"error": f"{type(e).__name__}: {e}"})

    # --- Phase 2: find stale runs + preload candles ---
    stale_batches = find_stale_batches(db)
    total = sum(b["stale_runs"] for b in stale_batches)
    _progress_set(total_runs=total)

    if total == 0:
      _progress_set(
        status="completed",
        phase="",
        ended_at=datetime.now(timezone.utc).isoformat(),
      )
      return get_progress()

    batch_ids = [b["batch_id"] for b in stale_batches]
    pairs = _collect_unique_pairs(db, batch_ids)
    _progress_set(
      phase="loading_candles",
      current=f"Preloading {len(pairs)} unique (symbol, TF) pairs…",
    )
    candle_cache = _preload_candles(pairs)
    cached_ok = sum(1 for v in candle_cache.values() if v is not _MISSING)
    _progress_set(candles_cached=cached_ok)

    log.info(
      "batch_rebuild: starting — %d stale batches, %d stale runs, "
      "%d unique (sym, tf) pairs (%d cached)",
      len(stale_batches), total, len(pairs), cached_ok,
    )

    # --- Phase 3: rebuild each stale run using the shared cache ---
    _progress_set(phase="rebuilding", current="")
    for info in stale_batches:
      bid = info["batch_id"]
      runs = (
        db.query(BacktestRun)
        .filter(
          BacktestRun.batch_id == bid,
          BacktestRun.is_deleted == False,  # noqa: E712
        )
        .all()
      )
      for run in runs:
        if run.strategy_state:
          continue  # already captured — skip
        _progress_set(current=f"{run.symbol} (batch #{bid})")
        try:
          res = rebuild_run(db, run, candle_cache=candle_cache)
          if res.get("ok"):
            _progress_bump(completed_runs=1)
          else:
            _progress_bump(failed_runs=1)
            with _progress_lock:
              _progress["errors"].append({
                "run_id": run.id,
                "symbol": run.symbol,
                "batch_id": bid,
                "reason": res.get("reason") or "unknown",
              })
        except Exception as e:
          db.rollback()
          _progress_bump(failed_runs=1)
          with _progress_lock:
            _progress["errors"].append({
              "run_id": run.id,
              "symbol": run.symbol,
              "batch_id": bid,
              "reason": f"exc_{type(e).__name__}: {e}",
            })
          log.warning("batch_rebuild: run %d threw: %s", run.id, e)

    _progress_set(
      status="completed",
      phase="",
      current="",
      ended_at=datetime.now(timezone.utc).isoformat(),
    )
    snap = get_progress()
    log.info(
      "batch_rebuild: done — completed=%d failed=%d",
      snap["completed_runs"], snap["failed_runs"],
    )
    return snap
  except Exception as e:
    log.exception("batch_rebuild: orchestrator crashed")
    _progress_set(
      status="error",
      current=f"orchestrator crash: {e}",
      ended_at=datetime.now(timezone.utc).isoformat(),
    )
    return get_progress()
  finally:
    db.close()
