"""L4.1 — Monthly parity canary (Ship 3, audit-only).

Purpose:
  On the 1st of every month at 02:00 NYT, pick each active `BacktestRun`
  inside every active, non-archived, non-compacted `BatchBacktestRun`
  and re-run it from scratch — no state injection, no position injection
  — using the saved strategy python_source + parameters. Compare the
  trades produced by the fresh run against the `BacktestTrade` rows
  already on disk (which have been extended nightly by L1.1). Any
  divergence means the incremental store has drifted from ground truth
  and needs human investigation.

Design safety (RULE 9 — Non-Coder Safety Mode):
  * PURE AUDIT. This service NEVER writes a BacktestTrade, never
    mutates a run, never touches a Portfolio / PortfolioAsset /
    KellyAllocation row. The only writes it performs are:
        - INSERT into `parity_canary_reports` (one row per canary run).
        - INSERT into `live_events` with event_type='parity_canary' when
          divergence is detected, so the Activity feed surfaces it.
  * Never raises — every per-run failure is caught and counted under
    `errors`. One broken symbol cannot kill the monthly audit.

Interface:
  run_parity_canary()                      — monthly entry point.
  compare_run_parity(db, run)              — per-run comparator; monkey-
                                             patched by tests.

Divergence semantics (per run):
  * Identity key for a trade = (entry_time, side, round(net_profit, 2)).
  * stored_only  = trades in BacktestTrade but missing from fresh run.
  * fresh_only   = trades from fresh run missing from BacktestTrade.
  * has_diff     = True iff |stored_only| + |fresh_only| > 0 OR
                   |sum(fresh.net_profit) - sum(stored.net_profit)| > 0.01.

CS-010 exemption (intentionally NOT migrated to load_trades_lightweight):
  This module IS the parity baseline. Its job is to verify that what lives
  in the ``BacktestTrade`` table matches what the engine emits on a cold
  re-run. Reading through ``load_trades_lightweight`` would:
    * aggregate multi-leg trades via ``max(leg_exits)`` → collapses legs
      the engine emits separately, forcing a false divergence on every
      partial-exit run.
    * apply the group's ``pnl_multiplier`` scaling to stored trades while
      the fresh engine output is raw — the diff would scream on every
      scaled batch even when stored and fresh agree bit-for-bit.
    * drop ``entry_time is None`` rows (CS-035) silently, hiding the exact
      integrity defect the canary is meant to catch.
  Any future attempt to route the canary through the loader must first
  freeze a per-leg, unscaled parity path in the loader itself, validated
  by a dedicated test against a known-divergent fixture. Until then,
  keep this path on raw ``BacktestTrade`` queries.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from backend.constants import COMMISSION_PCT
from backend.db.database import SessionLocal
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  BatchBacktestRun,
  LiveEvent,
  ParityCanaryReport,
)

log = logging.getLogger(__name__)


# ── Trade identity + diff helpers ───────────────────────────────

def _trade_key(entry_time, side: str, net_profit: float) -> tuple:
  """Canonical identity for diff matching — robust to float jitter."""
  ts = entry_time.isoformat() if entry_time is not None else ""
  return (ts, (side or "").lower(), round(float(net_profit or 0.0), 2))


def _diff_trades(stored: list[dict], fresh: list[dict]) -> dict:
  """Return the diff summary dict used in the canary report."""
  stored_keys = {_trade_key(t["entry_time"], t["side"], t["net_profit"]) for t in stored}
  fresh_keys = {_trade_key(t["entry_time"], t["side"], t["net_profit"]) for t in fresh}

  stored_only = sorted(stored_keys - fresh_keys)
  fresh_only = sorted(fresh_keys - stored_keys)

  stored_pnl = sum(float(t["net_profit"] or 0.0) for t in stored)
  fresh_pnl = sum(float(t["net_profit"] or 0.0) for t in fresh)
  pnl_delta = round(fresh_pnl - stored_pnl, 4)

  has_diff = bool(stored_only) or bool(fresh_only) or abs(pnl_delta) > 0.01

  return {
    "stored_count": len(stored),
    "fresh_count": len(fresh),
    "stored_only_count": len(stored_only),
    "fresh_only_count": len(fresh_only),
    "pnl_delta": pnl_delta,
    "has_diff": has_diff,
    # Cap the per-run divergence list so one bad run can't bloat the report.
    "stored_only_sample": [list(k) for k in stored_only[:20]],
    "fresh_only_sample": [list(k) for k in fresh_only[:20]],
  }


# ── Per-run comparator ──────────────────────────────────────────

def _stored_trades(db, run_id: int) -> list[dict]:
  rows = (
    db.query(BacktestTrade)
    .filter(BacktestTrade.run_id == run_id)
    .all()
  )
  return [
    {
      "entry_time": r.entry_time,
      "exit_time": r.exit_time,
      "side": r.side,
      "net_profit": r.net_profit,
    }
    for r in rows
  ]


def compare_run_parity(db, run: BacktestRun) -> dict:
  """Re-run `run` from scratch and diff against stored trades.

  Returns:
    {
      ok: bool,
      reason: str | None,       # set when ok=False
      has_diff: bool,
      diff: dict,               # only when ok=True
    }

  Tests monkey-patch this function to avoid spinning up the engine +
  candle loader. The real implementation below performs the full cold
  run using the same code path that produced the original trades, minus
  any state/position injection (this is 'from scratch' by definition).
  """
  from backend.backtester.engine import BacktestConfig, BacktestEngine
  from backend.utils.candle_loader import load_candles_df, to_nyt
  from backend.utils.safe_exec import safe_exec

  if run is None or run.is_deleted:
    return {"ok": False, "reason": "run_inactive", "has_diff": False, "diff": {}}

  batch = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.id == run.batch_id)
    .first()
  )
  if batch is None or batch.is_deleted or batch.is_archived or batch.is_compacted:
    return {"ok": False, "reason": "batch_inactive", "has_diff": False, "diff": {}}

  # Deferred import to keep the module import cheap for tests that never
  # touch compare_run_parity directly.
  from backend.db.models import Strategy
  strategy = db.query(Strategy).filter(Strategy.id == run.strategy_id).first()
  if strategy is None or not strategy.python_source:
    return {"ok": False, "reason": "no_strategy_source", "has_diff": False, "diff": {}}

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

  try:
    df_full = to_nyt(load_candles_df(db, run.symbol, run.timeframe))
  except Exception as e:
    return {"ok": False, "reason": f"candle_load_failed: {e}", "has_diff": False, "diff": {}}
  if df_full.empty:
    return {"ok": False, "reason": "no_candles", "has_diff": False, "diff": {}}

  # Slice to the run's original window — we audit against the historical
  # trade set, so we must replay the same bar range. end_date has likely
  # moved forward via L1.1; use the stored BacktestTrade tail as the
  # honest end-boundary by taking run.end_date as authoritative.
  if run.end_date is not None:
    df_slice = df_full[df_full.index <= df_full.index.asof(run.end_date)]
    if df_slice.empty:
      df_slice = df_full
  else:
    df_slice = df_full

  strategy_cls = safe_exec(strategy.python_source, label="parity_canary")
  if not strategy_cls:
    return {"ok": False, "reason": "strategy_compile_failed", "has_diff": False, "diff": {}}

  # Extra-TF data for multi-TF strategies.
  extra_tf_dfs: dict = {}
  for tf in (extra_timeframes or []):
    try:
      extra_tf_dfs[tf] = to_nyt(load_candles_df(db, run.symbol, tf))
    except Exception as _exc:
      log.debug("swallowed: %s", _exc)

  config = BacktestConfig(
    initial_capital=params.get("initial_capital", 10000),
    commission_pct=params.get("commission_pct", COMMISSION_PCT),
    slippage_mode=params.get("slippage_mode", "pct"),
    slippage_value=params.get("slippage_value", 0.0),
    leverage=params.get("leverage", 1),
    direction_filter=direction_filter,
    max_pyramiding=params.get("max_pyramiding", 1),
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
    # Cold run — no inject_strategy_state, no inject_positions. This is
    # the 'from scratch' leg of the parity check.
    result = engine.run(
      strategy_cls(), df_slice,
      symbol=run.symbol, timeframe=run.timeframe,
      inputs=inputs, extra_timeframes=extra_tf_dfs,
    )
  except Exception as e:
    log.warning("parity_canary: cold replay failed run=%d %s: %s",
                run.id, run.symbol, e)
    return {"ok": False, "reason": f"engine_failed: {e}", "has_diff": False, "diff": {}}

  fresh = [
    {
      "entry_time": getattr(t, "entry_time", None),
      "exit_time": getattr(t, "exit_time", None),
      "side": getattr(t, "side", ""),
      "net_profit": getattr(t, "net_profit", 0.0) or 0.0,
    }
    for t in (result.trades or [])
  ]
  stored = _stored_trades(db, run.id)
  diff = _diff_trades(stored, fresh)
  return {"ok": True, "reason": None, "has_diff": diff["has_diff"], "diff": diff}


# ── Orchestration ───────────────────────────────────────────────

def _active_runs(db, batch_ids: list[int] | None = None) -> list[BacktestRun]:
  q = (
    db.query(BacktestRun)
    .join(BatchBacktestRun, BacktestRun.batch_id == BatchBacktestRun.id)
    .filter(
      BacktestRun.is_deleted == False,  # noqa: E712
      BatchBacktestRun.is_deleted == False,  # noqa: E712
      BatchBacktestRun.is_archived == False,  # noqa: E712
      BatchBacktestRun.is_compacted == False,  # noqa: E712
    )
  )
  if batch_ids:
    q = q.filter(BacktestRun.batch_id.in_(batch_ids))
  return q.all()


def run_parity_canary(
  *,
  batch_ids: list[int] | None = None,
  max_combos: int | None = None,
  db=None,
) -> dict:
  """Monthly entry point. Iterate every eligible run, compare, persist
  a ParityCanaryReport row. Returns the report payload.

  Args:
    batch_ids:   Restrict the audit to these batch IDs (default: all active).
    max_combos:  Optional cap on combos to inspect (useful for ad-hoc runs).
    db:          Optional session. When None, opens SessionLocal() and
                 closes it at the end.

  Returns: {
    run_date, total_combos, combos_with_diff, errors,
    report: {
      runs: [{run_id, batch_id, symbol, ok, has_diff, reason, diff?}, ...],
      portfolio_ids_with_diff: [int, ...],
    },
    report_id: int,
  }

  Never raises — per-run failures are caught and counted.
  """
  own_session = db is None
  if own_session:
    db = SessionLocal()

  started = datetime.now(timezone.utc)
  run_date = started.strftime("%Y-%m-%d")
  report_payload: dict = {
    "started_at": started.isoformat(),
    "ended_at": None,
    "runs": [],
    "portfolio_ids_with_diff": [],
  }

  try:
    runs = _active_runs(db, batch_ids=batch_ids)
    if max_combos is not None:
      runs = runs[:max_combos]

    total = 0
    with_diff = 0
    errors = 0

    for run in runs:
      total += 1
      try:
        res = compare_run_parity(db, run)
      except Exception as e:
        errors += 1
        log.warning("parity_canary: run %d threw: %s", run.id, e)
        report_payload["runs"].append({
          "run_id": run.id, "batch_id": run.batch_id, "symbol": run.symbol,
          "ok": False, "has_diff": False,
          "reason": f"exc_{type(e).__name__}",
        })
        continue

      entry = {
        "run_id": run.id,
        "batch_id": run.batch_id,
        "symbol": run.symbol,
        "ok": bool(res.get("ok")),
        "has_diff": bool(res.get("has_diff")),
        "reason": res.get("reason"),
      }
      if res.get("ok") and res.get("has_diff"):
        with_diff += 1
        entry["diff"] = res.get("diff") or {}
      elif not res.get("ok"):
        errors += 1
      report_payload["runs"].append(entry)

    report_payload["ended_at"] = datetime.now(timezone.utc).isoformat()
    report_payload["errors"] = errors

    # Persist the audit row.
    row = ParityCanaryReport(
      run_date=run_date,
      portfolio_id=None,  # cross-portfolio audit; batches are the axis here
      total_combos=total,
      combos_with_diff=with_diff,
      report_json=json.dumps(report_payload, default=str),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Activity-feed surface — ONE LiveEvent if divergences were found.
    # Human investigates from here.
    if with_diff > 0:
      db.add(LiveEvent(
        portfolio_id=None,
        event_type="parity_canary",
        details=json.dumps({
          "report_id": row.id,
          "run_date": run_date,
          "total_combos": total,
          "combos_with_diff": with_diff,
          "errors": errors,
        }),
      ))
      db.commit()

    log.info(
      "parity_canary: date=%s total=%d with_diff=%d errors=%d report_id=%d",
      run_date, total, with_diff, errors, row.id,
    )
    return {
      "run_date": run_date,
      "total_combos": total,
      "combos_with_diff": with_diff,
      "errors": errors,
      "report": report_payload,
      "report_id": row.id,
    }
  finally:
    if own_session:
      db.close()
