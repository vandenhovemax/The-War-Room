"""L1.1 — Offline batch store refresh (nightly, additive).

Purpose:
  Extend every active batch's trade store forward in time. For each
  `BacktestRun` inside every `BatchBacktestRun`, load the candles that
  arrived since `run.end_date`, resume the strategy from the saved
  `run.strategy_state`, and APPEND the newly generated trades to the
  existing run (same run_id). This keeps saved/live portfolios pointing
  at the same batch_ids while their historical trade pools stay fresh.

Design safety (RULE 9):
  * Additive-only. We do NOT rewrite any existing BacktestTrade row; we
    only INSERT new rows with `run_id = existing_run.id`.
  * We never touch a run that has no `strategy_state` captured — that
    signals an older batch that needs a one-off full rebuild. The user
    triggers those manually from the UI.
  * We operate run-by-run inside per-run try/except so one broken
    symbol cannot kill the whole nightly job.
  * Non-deleted, non-archived batches only.

Interface:
  refresh_all_active_batches()        — nightly entry point (0 args,
                                        runs against the default DB
                                        session factory).
  refresh_batch(batch_id)             — single batch (ad-hoc manual).
  replay_run_forward(db, run)         — single run helper; callable
                                        from tests.

Returns a small report dict per batch for the Activity feed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pandas as pd

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


# ── Run-level replay ────────────────────────────────────────────

def _cache_get_candles(candle_cache, db, symbol, timeframe):
  """Cache-aware wrapper around to_nyt(load_candles_df(...)).

  candle_cache is an optional dict keyed by (symbol, timeframe). When the
  nightly job calls refresh_all_active_batches, it supplies a single dict
  that spans every run in every batch, so each (symbol, timeframe) is read
  from disk + tz-converted once instead of ~40 times.

  Returns a SHARED DataFrame — callers must not mutate it.
  """
  if candle_cache is None:
    return to_nyt(load_candles_df(db, symbol, timeframe))
  key = (symbol, timeframe)
  cached = candle_cache.get(key)
  if cached is None:
    cached = to_nyt(load_candles_df(db, symbol, timeframe))
    candle_cache[key] = cached
  return cached


def replay_run_forward(db, run: BacktestRun, candle_cache=None) -> dict:
  """Replay one BacktestRun from its saved state up to the latest candle.

  Returns a dict: {ok, new_trades, from_ts, to_ts, reason}
  Never raises — logs and returns {ok: False, reason: ...} instead.

  candle_cache: optional dict[(symbol, timeframe) -> DataFrame] shared across
    a full nightly run so the same candle series isn't re-loaded per-run.
  """
  from backend.backtester.engine import BacktestConfig, BacktestEngine
  from backend.backtester.trade_storage import store_trades
  from backend.utils.safe_exec import safe_exec

  if not run.strategy_state:
    return {"ok": False, "reason": "no_strategy_state"}

  batch = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.id == run.batch_id)
    .first()
  )
  if batch is None or batch.is_deleted or batch.is_archived:
    return {"ok": False, "reason": "batch_inactive"}

  strategy = db.query(Strategy).filter(Strategy.id == run.strategy_id).first()
  if strategy is None or not strategy.python_source:
    return {"ok": False, "reason": "no_strategy_source"}

  # Parse saved params + strategy inputs
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

  # Load full candle history and find where we left off
  try:
    df_full = _cache_get_candles(candle_cache, db, run.symbol, run.timeframe)
  except Exception as e:
    return {"ok": False, "reason": f"candle_load_failed: {e}"}
  if df_full.empty:
    return {"ok": False, "reason": "no_candles"}

  # Candle index is tz-naive NYT (to_nyt strips tz). The cutoff must match.
  cutoff = run.end_date
  if cutoff is None:
    return {"ok": False, "reason": "no_end_date"}
  boundary_ts = pd.Timestamp(cutoff)
  if boundary_ts.tzinfo is not None:
    boundary_ts = boundary_ts.tz_convert("America/New_York").tz_localize(None)

  boundary_idx = df_full.index.searchsorted(boundary_ts, side="right") - 1
  if boundary_idx < 0 or boundary_idx >= len(df_full) - 1:
    return {"ok": True, "new_trades": 0, "reason": "no_new_bars"}

  strategy_cls = safe_exec(strategy.python_source, label="batch_store_refresh")
  if not strategy_cls:
    return {"ok": False, "reason": "strategy_compile_failed"}

  # Warmup bars: re-compute indicators so they match the saved state's view
  temp = strategy_cls()
  temp._df = df_full
  temp._inputs = inputs
  temp._tf_data = {}
  try:
    temp.init()
  except Exception as _exc:
    log.debug("swallowed: %s", _exc)
  base_warmup = max(200, (temp._warmup_bars or 50) * 4)

  warmup_start = max(0, boundary_idx + 1 - base_warmup)
  # .copy() detaches this slice from the shared cached DataFrame. Without the
  # copy, any engine/strategy column-mutation would leak into the cache and
  # corrupt subsequent runs on the same (symbol, timeframe).
  df_slice = df_full.iloc[warmup_start:].copy()
  actual_warmup = boundary_idx + 1 - warmup_start

  # Extra TF data — engine.run calls .copy() on each entry internally when
  # building strategy._tf_data (engine.py ~line 164), so the cached DataFrame
  # itself is not at risk from those paths.
  extra_tf_dfs: dict[str, pd.DataFrame] = {}
  for tf in (extra_timeframes or []):
    try:
      extra_tf_dfs[tf] = _cache_get_candles(candle_cache, db, run.symbol, tf)
    except Exception as _extra_err:
      log.warning(
        "batch_store_refresh: no extra-tf %s data for %s (%s)",
        tf, run.symbol, _extra_err,
      )

  # Parse saved strategy_state
  try:
    saved_state = json.loads(run.strategy_state)
  except Exception as e:
    return {"ok": False, "reason": f"state_parse_failed: {e}"}

  inject_positions = saved_state.pop("_open_positions", None)

  # Build engine config from saved params (same as _run_single_symbol_refresh)
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
      strategy_cls(), df_slice,
      symbol=run.symbol, timeframe=run.timeframe,
      inputs=inputs, extra_timeframes=extra_tf_dfs,
      inject_positions=inject_positions,
      inject_strategy_state=saved_state,
    )
  except Exception as e:
    log.warning("batch_store_refresh: replay failed run=%d %s: %s",
                run.id, run.symbol, e)
    return {"ok": False, "reason": f"engine_failed: {e}"}

  new_trades = result.trades or []

  # D1 observability: phantom-dupe forensic — if the engine emitted any
  # exit fills with entry_time BEFORE the boundary, that means warmup
  # state didn't restore cleanly and the engine re-played already-
  # committed bars. The store_trades guard will drop those re-emissions,
  # but log here so we can diagnose the upstream cause.
  pre_boundary = sum(
    1 for t in new_trades
    if getattr(t, "action", None) == "exit"
       and getattr(t, "entry_time", None) is not None
       and t.entry_time < boundary_ts
  )
  if pre_boundary:
    log.warning(
      "replay_run_forward run=%d %s emitted %d exit fills with entry_time "
      "BEFORE boundary_ts (%s) — engine warmup likely re-emitting committed bars",
      run.id, run.symbol, pre_boundary, boundary_ts.isoformat(),
    )

  # Drop the prior forced "backtest_end" exit rows for this run before
  # appending new trades. Those rows are the fictional last-bar closes of
  # positions that were actually still open — the same positions we just
  # re-injected via inject_positions. Without this delete, if the re-injected
  # position closes during the replay a second exit row is appended and the
  # position's P&L gets double-counted. Run-scoped delete only; other
  # backtest_trades rows are untouched.
  db.query(BacktestTrade).filter(
    BacktestTrade.run_id == run.id,
    BacktestTrade.exit_reason == "backtest_end",
  ).delete(synchronize_session=False)

  if new_trades:
    store_trades(db, run.id, run.symbol, new_trades, config.initial_capital)

  run.end_date = df_slice.index[-1].to_pydatetime().replace(tzinfo=None)
  try:
    run.strategy_state = json.dumps(
      getattr(result, "strategy_state", {}) or {}, default=str
    )
  except Exception as _exc:
    log.debug("swallowed: %s", _exc)

  db.commit()

  return {
    "ok": True,
    "new_trades": len(new_trades),
    "from_ts": boundary_ts.isoformat(),
    "to_ts": run.end_date.isoformat() if run.end_date else None,
  }


# ── Batch-level orchestration ───────────────────────────────────

def refresh_batch(batch_id: int, db=None, candle_cache=None) -> dict:
  """Refresh every eligible run inside one batch. Returns summary dict.

  candle_cache: optional dict — when supplied, (symbol, timeframe) candle
    loads are reused across runs. Pass the same dict when iterating multiple
    batches to amortize the read cost across the whole nightly job.
  """
  own_session = db is None
  if own_session:
    db = SessionLocal()

  summary = {
    "batch_id": batch_id,
    "runs_total": 0,
    "runs_refreshed": 0,
    "runs_skipped": 0,
    "runs_failed": 0,
    "new_trades": 0,
    "reasons": {},
  }

  try:
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    if batch is None or batch.is_deleted or batch.is_archived:
      summary["reasons"]["batch"] = "inactive"
      return summary

    runs = (
      db.query(BacktestRun)
      .filter(BacktestRun.batch_id == batch_id, BacktestRun.is_deleted == False)  # noqa: E712
      .all()
    )
    summary["runs_total"] = len(runs)

    for run in runs:
      try:
        res = replay_run_forward(db, run, candle_cache=candle_cache)
        if not res.get("ok"):
          if res.get("reason") == "no_new_bars":
            summary["runs_skipped"] += 1
          else:
            summary["runs_failed"] += 1
          r = res.get("reason") or "unknown"
          summary["reasons"][r] = summary["reasons"].get(r, 0) + 1
          continue
        summary["runs_refreshed"] += 1
        summary["new_trades"] += int(res.get("new_trades") or 0)
      except Exception as e:
        db.rollback()
        summary["runs_failed"] += 1
        summary["reasons"][f"exc_{type(e).__name__}"] = (
          summary["reasons"].get(f"exc_{type(e).__name__}", 0) + 1
        )
        log.warning("batch_store_refresh: run %d threw: %s", run.id, e)

    log.info(
      "batch_store_refresh: batch %d — refreshed=%d skipped=%d failed=%d new_trades=%d",
      batch_id, summary["runs_refreshed"], summary["runs_skipped"],
      summary["runs_failed"], summary["new_trades"],
    )
    return summary
  finally:
    if own_session:
      db.close()


def refresh_all_active_batches() -> dict:
  """Nightly entry point. Iterates every active batch. Scheduled at 01:00 NYT
  (see backend/api/app.py lifespan).

  Report shape:
    {started_at, ended_at, batches: [summary, ...], total_new_trades, errors}
  """
  started = datetime.now(timezone.utc)
  report: dict = {
    "started_at": started.isoformat(),
    "ended_at": None,
    "batches": [],
    "total_new_trades": 0,
    "errors": 0,
  }

  db = SessionLocal()
  try:
    bids = [
      b.id for b in db.query(BatchBacktestRun)
      .filter(
        BatchBacktestRun.is_deleted == False,  # noqa: E712
        BatchBacktestRun.is_archived == False,  # noqa: E712
        BatchBacktestRun.is_compacted == False,  # noqa: E712
      )
      .all()
    ]
  finally:
    db.close()

  log.info("batch_store_refresh: nightly run — %d active batches", len(bids))

  # Shared candle cache for the whole nightly pass — amortizes the ~800
  # (symbol, timeframe) reads across all 32k+ runs. Holds for the duration
  # of the nightly job, then goes out of scope and is freed.
  candle_cache: dict = {}

  for bid in bids:
    try:
      s = refresh_batch(bid, candle_cache=candle_cache)
      report["batches"].append(s)
      report["total_new_trades"] += int(s.get("new_trades") or 0)
    except Exception as e:
      report["errors"] += 1
      log.error("batch_store_refresh: batch %d crashed: %s", bid, e)

  report["ended_at"] = datetime.now(timezone.utc).isoformat()
  log.info(
    "batch_store_refresh: nightly complete — %d batches, %d new trades, %d errors",
    len(bids), report["total_new_trades"], report["errors"],
  )
  return report
