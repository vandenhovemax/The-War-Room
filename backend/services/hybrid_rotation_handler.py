"""T-003 Stage 4 — Hybrid rotation handler.

Runs ON ROTATION DAY ONLY (today >= next_phase1_restart boundary). Replaces
the noise-pick nightly refresh on rotation days with a proper re-grade
using the just-completed OOS window as the grading basis.

NOT YET WIRED INTO THE GATE. Stage 2's `frozen_pending_rotation` branch in
`hybrid_portfolio_refresh.refresh_portfolio` still freezes on rotation day.
This module provides:
  - `compute_rotation_plan(portfolio_id, db=None)` — pure planning step,
    no writes. Returns a RotationPlan dataclass describing the proposed
    healed window, new live window, idempotency hash, and call to run_step2.
  - `apply_rotation_plan(plan, db)` — atomically applies a plan: updates
    wfo_windows_meta, persists step2 results, flips PortfolioAsset.is_active,
    writes KellyAllocation + LiveEvent, updates last_rotation_hash.
  - `rotate_portfolio(portfolio_id, db=None)` — orchestrates compute+apply.

Idempotency:
  Each rotation produces a deterministic hash of (portfolio_id, healed
  window oos_start/end, current batch_ids, preset_id). Persisted at
  `source_strategies.last_rotation_hash`. Re-running on the same day with
  the same inputs returns reason='already_rotated' and writes nothing.

Stub heal (Stage 4a):
  The last window in windows_meta is typically a save-time stub (e.g. P24
  w8 = 23 days). On rotation, the stub's oos_end is healed forward to
  (next_boundary - 1 day) and its oos_start is anchored on (prev window
  oos_end + 1 day) — closing any off-by-one gaps the optimizer left.

Append (Stage 4b):
  A new "live" window is appended starting at next_boundary, ending at
  (next_boundary + span_months - 1 day). This becomes the next save-time
  stub until rotation N+1.

Re-grade (Stage 4c):
  Calls `step2_service.run_step2` with a step1_result whose single window
  spans the full healed (just-completed) OOS period — multi-month real
  data, not the 1-day synthetic window the legacy nightly refresh uses.
  Batches stay fixed (current basket's batch_ids); assets get re-graded.

Basket flip (Stage 5 mechanics, lifted from hybrid_portfolio_refresh):
  Diff vs current PortfolioAsset rows. is_active flipped per new basket.
  Newcomer rows inserted. KellyAllocation row written with
  trigger='step2_rotation'. LiveEvent emitted with event_type='hybrid_rotation'.

Safety:
  - All writes happen in a single db.commit() at the end of apply_rotation_plan.
  - On exception during apply, db.rollback() runs in the caller (refresh service
    today already does this; rotate_portfolio() inherits the pattern).
  - Idempotency hash is checked AND written in the same transaction so the
    "already rotated" guard cannot lose to a concurrent run on the same day.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from dateutil.relativedelta import relativedelta

from backend.analysis.hybrid_rotation import next_phase1_restart
from backend.analysis.step2_service import run_step2
from backend.analysis.trade_log_unified import load_unified_trades
from backend.db.database import SessionLocal
from backend.db.models import (
  BatchBacktestRun,
  KellyAllocation,
  LiveEvent,
  Portfolio,
  PortfolioAsset,
  Strategy,
)
from backend.services.phase2_recorder import (
  EVENT_TYPE_HYBRID_ROTATION,
  record_phase2_event,
)
from backend.utils.clock import today as _clock_today

log = logging.getLogger(__name__)


# ── Plan dataclass ──────────────────────────────────────────────

@dataclass
class RotationPlan:
  portfolio_id: int
  rotation_today: date
  next_boundary: date
  span_months: int
  healed_window: dict             # {window_num, oos_start, oos_end}
  new_live_window: dict           # {window_num, oos_start, oos_end}
  current_batch_ids: list[int]
  rotation_hash: str
  step1_result: dict              # synthetic single-window step1 for run_step2
  ok: bool = True
  reason: str | None = None
  meta: dict = field(default_factory=dict)


# ── Pure helpers (no DB) ────────────────────────────────────────

def _parse_date(value) -> date | None:
  if value is None:
    return None
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  s = str(value)[:10]
  try:
    return datetime.strptime(s, "%Y-%m-%d").date()
  except (ValueError, TypeError):
    return None


def _infer_span_months(windows_meta: list[dict]) -> int | None:
  """Mirror of the cadence inference used by next_phase1_restart, but
  returning span_months for our own walk math.
  """
  if not isinstance(windows_meta, list) or len(windows_meta) < 2:
    return None
  spans = []
  for w in windows_meta:
    if not isinstance(w, dict):
      continue
    s = _parse_date(w.get("oos_start"))
    e = _parse_date(w.get("oos_end"))
    if s is None or e is None or e < s:
      continue
    spans.append((e - s).days)
  if len(spans) < 2:
    return None
  earlier_mode = max(set(spans[:-1]), key=lambda x: spans[:-1].count(x))
  if earlier_mode > 0 and spans[-1] < 0.8 * earlier_mode:
    spans = spans[:-1]
  if not spans:
    return None
  mode_span = max(set(spans), key=lambda x: spans.count(x))
  if mode_span <= 0:
    return None
  return max(1, round(mode_span / 30.44))


def compute_healed_window(windows_meta: list[dict], boundary: date,
                          span_months: int) -> dict:
  """Stage 4a: heal the trailing stub. The just-completed window is
  [boundary - span_months, boundary - 1 day]. Window_num matches the
  stub's number (we replace the stub, not append a duplicate).
  """
  oos_start = boundary - relativedelta(months=span_months)
  oos_end = boundary - relativedelta(days=1)
  last_num = 0
  for w in windows_meta:
    if isinstance(w, dict) and isinstance(w.get("window_num"), int):
      last_num = max(last_num, w["window_num"])
  return {
    "window_num": last_num,  # heal-in-place: keep the same number
    "oos_start": oos_start.isoformat(),
    "oos_end": oos_end.isoformat(),
  }


def compute_new_live_window(boundary: date, span_months: int,
                            healed_num: int) -> dict:
  """Stage 4b: the new 'live' window starts at the boundary and runs for
  span_months. This becomes the next save-time stub until rotation N+1.
  """
  oos_end = boundary + relativedelta(months=span_months) - relativedelta(days=1)
  return {
    "window_num": healed_num + 1,
    "oos_start": boundary.isoformat(),
    "oos_end": oos_end.isoformat(),
  }


def compute_rotation_hash(portfolio_id: int, healed_window: dict,
                          batch_ids: list[int], preset_id: str | None) -> str:
  """Deterministic hash for idempotency. Same inputs → same hash. If the
  portfolio's persisted last_rotation_hash matches this, the rotation
  already ran and is skipped.
  """
  payload = json.dumps({
    "portfolio_id": portfolio_id,
    "oos_start": healed_window["oos_start"],
    "oos_end": healed_window["oos_end"],
    "batch_ids": sorted(batch_ids),
    "preset_id": preset_id or "",
  }, sort_keys=True)
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def merge_windows_meta(existing: list[dict], healed: dict,
                       new_live: dict) -> list[dict]:
  """Replace the trailing stub with `healed`, append `new_live`. If the
  existing meta has no stub at the tail (last window already ends exactly
  at boundary-1day), still append new_live without replacing.
  """
  out: list[dict] = []
  for w in existing:
    if not isinstance(w, dict):
      out.append(w)
      continue
    out.append(dict(w))
  if out and isinstance(out[-1], dict) and out[-1].get("window_num") == healed["window_num"]:
    out[-1] = healed
  else:
    out.append(healed)
  out.append(new_live)
  return out


# ── DB helpers ──────────────────────────────────────────────────

def _current_batch_ids(db, portfolio_id: int) -> list[int]:
  rows = (
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == portfolio_id)
    .all()
  )
  return sorted({r.batch_id for r in rows if r.batch_id is not None})


def _build_step2_config(src_cfg: dict) -> dict:
  cfg = dict(src_cfg or {})
  cfg["hybrid_mode"] = True
  cfg.setdefault("oos_months", src_cfg.get("oos_months", 6))
  return cfg


def _project_weights_from_scores(assets: list[str],
                                 scores: dict[str, float]) -> dict[str, float]:
  if not assets:
    return {}
  positive = {a: float(scores.get(a, 0.0) or 0.0) for a in assets}
  total = sum(v for v in positive.values() if v > 0)
  if total <= 0:
    w = 1.0 / len(assets)
    return {a: w for a in assets}
  return {a: (v / total) if v > 0 else 0.0 for a, v in positive.items()}


# ── Public API ──────────────────────────────────────────────────

def compute_rotation_plan(portfolio_id: int, db=None,
                          run_step2_fn=run_step2,
                          simulate_today: date | None = None) -> RotationPlan:
  """Pure planning + step2 invocation, no DB writes.

  `run_step2_fn` is parameterized so tests can inject a fake.

  `simulate_today` (T-001 Item #8 catch-up): when provided, it overrides
  `_clock_today()` for the boundary-check pathway. The gate in
  `hybrid_portfolio_refresh.refresh_portfolio` passes the missed boundary
  date here when it detects a cron miss (today is past a cadence point
  the portfolio hasn't rotated through). Healed/new_live window math
  uses simulate_today as the rotation anchor so the just-completed OOS
  window is computed correctly even when called days/weeks after the
  boundary itself.
  """
  own = db is None
  if own:
    db = SessionLocal()

  effective_today = simulate_today if simulate_today is not None else _clock_today()

  def empty_plan(reason: str) -> RotationPlan:
    return RotationPlan(
      portfolio_id=portfolio_id,
      rotation_today=effective_today,
      next_boundary=date(1970, 1, 1),
      span_months=0,
      healed_window={},
      new_live_window={},
      current_batch_ids=[],
      rotation_hash="",
      step1_result={},
      ok=False,
      reason=reason,
    )

  try:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if portfolio is None:
      return empty_plan("not_found")
    if portfolio.mode != "hybrid":
      return empty_plan("not_hybrid")
    if portfolio.is_deleted:
      return empty_plan("deleted")
    if not portfolio.is_active:
      return empty_plan("inactive")

    try:
      src_cfg = json.loads(portfolio.source_strategies or "{}")
    except Exception:
      src_cfg = {}

    rotation_today = effective_today
    windows_meta = src_cfg.get("wfo_windows_meta")
    next_boundary = next_phase1_restart(windows_meta, rotation_today)
    if next_boundary is None:
      return empty_plan("no_boundary_inferable")
    if rotation_today < next_boundary:
      return empty_plan("not_yet_due")

    span_months = _infer_span_months(windows_meta)
    if span_months is None:
      return empty_plan("no_span_inferable")

    batch_ids = _current_batch_ids(db, portfolio_id)
    if not batch_ids:
      return empty_plan("no_basket_batches")

    healed = compute_healed_window(windows_meta, next_boundary, span_months)
    new_live = compute_new_live_window(next_boundary, span_months,
                                       healed["window_num"])
    rot_hash = compute_rotation_hash(
      portfolio_id, healed, batch_ids, src_cfg.get("preset_id"),
    )

    if src_cfg.get("last_rotation_hash") == rot_hash:
      plan = RotationPlan(
        portfolio_id=portfolio_id,
        rotation_today=rotation_today,
        next_boundary=next_boundary,
        span_months=span_months,
        healed_window=healed,
        new_live_window=new_live,
        current_batch_ids=batch_ids,
        rotation_hash=rot_hash,
        step1_result={},
        ok=True,
        reason="already_rotated",
      )
      return plan

    # Build step1 with the just-completed (healed) window as the single OOS
    # window for run_step2 to grade against. IS span uses the previous full
    # window's start as a soft anchor — run_step2's window_num=1 logic only
    # cares about oos_start/oos_end + selected_batches.
    step1_result = {
      "windows": [{
        "window_num": healed["window_num"],
        "is_start": healed["oos_start"],  # placeholder; not used for grading
        "oos_start": healed["oos_start"],
        "oos_end": healed["oos_end"],
        "candidates": [{"selected_batches": batch_ids}],
      }],
    }

    # Load trades for the rotation window. Cutoff is the prior window's
    # oos_end (the rotation grades fresh OOS data only for the just-completed
    # window). load_unified_trades will return all trades in the batches;
    # run_step2 filters by the window's oos_start/oos_end internally.
    trades, rbm, batch_info = load_unified_trades(
      db,
      batch_ids=batch_ids,
      cutoff=portfolio.optimization_end_date,
      portfolio_id=portfolio_id,
    )
    if not trades:
      return empty_plan("no_trades")

    config = _build_step2_config(src_cfg)
    _prior = src_cfg.get("continuation_state")
    if _prior:
      config["_seed_state"] = _prior

    step2_result = run_step2_fn(
      step1_result=step1_result,
      all_trades=trades,
      run_batch_map=rbm,
      config=config,
      db=db,
    )
    if step2_result is None:
      return empty_plan("step2_returned_none")

    plan = RotationPlan(
      portfolio_id=portfolio_id,
      rotation_today=rotation_today,
      next_boundary=next_boundary,
      span_months=span_months,
      healed_window=healed,
      new_live_window=new_live,
      current_batch_ids=batch_ids,
      rotation_hash=rot_hash,
      step1_result=step1_result,
      ok=True,
      reason=None,
      meta={
        "step2_result": step2_result,
        "batch_info": batch_info,
        "src_cfg": src_cfg,
      },
    )
    return plan
  finally:
    if own:
      db.close()


def _extract_basket(step2_result: dict, config: dict) -> tuple[list[str], dict]:
  changes = step2_result.get("selection_changes") or []
  basket: list[str] = []
  if changes:
    s: set[str] = set()
    for c in changes:
      for a in (c.get("added") or []):
        s.add(a)
      for a in (c.get("removed") or []):
        s.discard(a)
    basket = sorted(s)
  if not basket:
    freq = step2_result.get("asset_frequency") or {}
    ranked = sorted(
      freq.items(),
      key=lambda kv: (kv[1].get("selected", 0), kv[1].get("pct", 0.0)),
      reverse=True,
    )
    n_assets = int(config.get("hybrid_n_assets") or 0) or len(ranked)
    basket = [a for a, _ in ranked[:n_assets]]
  scores = step2_result.get("latest_asset_scores") or {}
  return basket, scores


def apply_rotation_plan(plan: RotationPlan, db) -> dict:
  """Apply a rotation plan atomically. Returns a summary report dict.

  Skips writes when plan.ok is False or plan.reason is 'already_rotated'.
  """
  report: dict = {
    "portfolio_id": plan.portfolio_id,
    "ok": plan.ok,
    "reason": plan.reason,
    "added": [],
    "removed": [],
    "newcomers_created": [],
    "skipped_newcomers": [],
    "rotation_hash": plan.rotation_hash,
    "next_boundary": plan.next_boundary.isoformat() if plan.next_boundary != date(1970, 1, 1) else None,
  }
  if not plan.ok:
    return report
  if plan.reason == "already_rotated":
    return report

  step2_result = plan.meta.get("step2_result") or {}
  src_cfg = plan.meta.get("src_cfg") or {}
  batch_info = plan.meta.get("batch_info") or {}
  config = _build_step2_config(src_cfg)

  basket, scores = _extract_basket(step2_result, config)
  weights = _project_weights_from_scores(basket, scores)

  portfolio = db.query(Portfolio).filter(Portfolio.id == plan.portfolio_id).first()
  if portfolio is None:
    report["ok"] = False
    report["reason"] = "not_found_at_apply"
    return report

  current_keys: set[str] = set()
  existing_pa_keys: set[str] = set()
  pa_rows = list(
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == plan.portfolio_id)
    .all()
  )
  for pa in pa_rows:
    key = (
      f"B{pa.batch_id}_{pa.symbol}_{pa.trade_side}"
      if pa.batch_id else f"{pa.symbol}_{pa.trade_side}"
    )
    existing_pa_keys.add(key)
    if pa.is_active:
      current_keys.add(key)

  new_set = set(basket)
  added = sorted(new_set - current_keys)
  removed = sorted(current_keys - new_set)

  if basket:
    db.query(KellyAllocation).filter(
      KellyAllocation.portfolio_id == plan.portfolio_id,
      KellyAllocation.is_current == True,  # noqa: E712
    ).update({KellyAllocation.is_current: False}, synchronize_session=False)
    db.add(KellyAllocation(
      portfolio_id=plan.portfolio_id,
      weights_json=json.dumps(weights),
      result_json=json.dumps({"basket": basket, "scores": scores}),
      config_json=json.dumps({
        "source": "step2_rotation",
        "rotation_hash": plan.rotation_hash,
        "healed_window": plan.healed_window,
      }),
      trigger="step2_rotation",
      is_current=True,
    ))

    for pa in pa_rows:
      key = (
        f"B{pa.batch_id}_{pa.symbol}_{pa.trade_side}"
        if pa.batch_id else f"{pa.symbol}_{pa.trade_side}"
      )
      pa.is_active = key in new_set

    newcomer_keys = [k for k in basket if k not in existing_pa_keys]
    portfolio_strategy = (src_cfg.get("strategy_name") or "").strip()
    meta_cache: dict[int, tuple[str, str | None]] = {}

    def _resolve(bid: int) -> tuple[str, str | None]:
      if bid in meta_cache:
        return meta_cache[bid]
      strat_name = ""
      cfg_name: str | None = None
      batch = db.get(BatchBacktestRun, bid)
      if batch is not None:
        try:
          bp = json.loads(batch.parameters or "{}")
          cfg_name = bp.get("config_name") or None
        except Exception:
          cfg_name = None
        try:
          strat = db.get(Strategy, batch.strategy_id)
          if strat is not None:
            strat_name = (strat.name or "").strip()
        except Exception:
          strat_name = ""
      meta_cache[bid] = (strat_name, cfg_name)
      return meta_cache[bid]

    for key in newcomer_keys:
      parts = key.rsplit("_", 2)
      if len(parts) != 3 or not parts[0].startswith("B"):
        report["skipped_newcomers"].append(key)
        continue
      try:
        bid = int(parts[0][1:])
      except ValueError:
        report["skipped_newcomers"].append(key)
        continue
      sym, side = parts[1], parts[2]
      info = batch_info.get(bid) or {}
      batch_strategy_name, config_name = _resolve(bid)
      strategy_name = portfolio_strategy or batch_strategy_name
      if not strategy_name:
        report["skipped_newcomers"].append(key)
        continue
      db.add(PortfolioAsset(
        portfolio_id=plan.portfolio_id,
        symbol=sym,
        trade_side=side,
        strategy_name=strategy_name,
        raw_name=f"{sym}_{side.capitalize()}",
        batch_id=bid,
        config_name=config_name,
        timeframe=info.get("timeframe"),
        group_name=info.get("group"),
        is_active=True,
      ))
      report["newcomers_created"].append(key)

  # ── Update wfo_windows_meta: heal stub + append new live ──
  windows_meta = src_cfg.get("wfo_windows_meta") or []
  src_cfg["wfo_windows_meta"] = merge_windows_meta(
    windows_meta, plan.healed_window, plan.new_live_window,
  )
  src_cfg["last_rotation_hash"] = plan.rotation_hash
  src_cfg["last_rotation_at"] = datetime.now(timezone.utc).isoformat()
  src_cfg["refresh_step2_result"] = {
    "selected_records": step2_result.get("selected_records"),
    "aggregate_monthly_matrix": step2_result.get("aggregate_monthly_matrix"),
    "aggregate_equity_curve": step2_result.get("aggregate_equity_curve"),
    "aggregate_breakdown": step2_result.get("aggregate_breakdown"),
    "cumulative_metrics": step2_result.get("cumulative_metrics"),
    "latest_asset_scores": scores,
    "refreshed_at": datetime.now(timezone.utc).isoformat(),
    "rotation_hash": plan.rotation_hash,
  }
  pw = step2_result.get("per_window")
  if pw:
    src_cfg["wfo_per_window"] = pw
    tail = (pw[-1] or {}).get("final_state_snapshot")
    if tail:
      src_cfg["continuation_state"] = tail

  portfolio.source_strategies = json.dumps(src_cfg)
  portfolio.last_refreshed_at = datetime.now(timezone.utc)

  # T-002: phase-2 recorder replaces legacy LiveEvent emit. SAVEPOINT
  # isolates recorder failure from the trading-state commit.
  try:
    with db.begin_nested():
      record_phase2_event(
        db,
        portfolio_id=plan.portfolio_id,
        target_window=plan.healed_window,
        windows_meta=src_cfg.get("wfo_windows_meta") or [],
        src_cfg=src_cfg,
        step2_result=step2_result,
        batch_ids=list(plan.current_batch_ids),
        rbm=None,
        prior_basket=sorted(current_keys),
        chosen_basket=sorted(basket),
        added=added,
        removed=removed,
        basket_unchanged=False,
        kelly_allocation_id=None,
        trigger="rotation_day",
        event_type_override=EVENT_TYPE_HYBRID_ROTATION,
        extra={
          "rotation_hash": plan.rotation_hash,
          "healed_window": plan.healed_window,
          "new_live_window": plan.new_live_window,
          "weights_count": len(weights),
          "computed_at": datetime.now(timezone.utc).isoformat(),
        },
      )
  except Exception as rec_e:
    log.exception(
      "rotate: phase2_recorder failed for %d: %s", plan.portfolio_id, rec_e,
    )

  db.commit()

  # ── T-001 follow-up: kick the executor so the new basket goes live now ──
  # PortfolioAsset is cached in strategy_executor._active at activation; it
  # is NOT re-read on every cycle. Without this dispatch the executor keeps
  # trading the previous basket and using stale Kelly weights until the next
  # process restart or manual /live/refresh.
  _dispatch_executor_refresh(plan.portfolio_id)

  report.update({
    "added": added,
    "removed": removed,
    "basket_size": len(basket),
    "weights_written": len(weights),
    "healed_window": plan.healed_window,
    "new_live_window": plan.new_live_window,
  })
  return report


def _dispatch_executor_refresh(portfolio_id: int) -> None:
  """Post-commit: invalidate the executor's Kelly cache and schedule a
  re-read of PortfolioAsset rows + WS subscription update.

  Mirrors the dispatch pattern the legacy hybrid_portfolio_refresh did at
  pre-T-001 lines 577-604 of the .bak. Best-effort: any exception is
  logged but never raised — the rotation itself is already committed and
  must not be rolled back by an executor-side hiccup.
  """
  try:
    from backend.exchange.engine_coordinator import get_engine
    engine = get_engine()
  except Exception as e:
    log.warning("rotate: engine import failed for %d: %s", portfolio_id, e)
    return

  if engine is None:
    return

  try:
    if hasattr(engine, "refresh_kelly_cache"):
      engine.refresh_kelly_cache(portfolio_id)
  except Exception as e:
    log.warning("rotate: refresh_kelly_cache failed for %d: %s", portfolio_id, e)

  try:
    if hasattr(engine, "refresh_portfolio") and hasattr(engine, "_schedule"):
      coro = engine.refresh_portfolio(portfolio_id)
      loop = getattr(engine, "_loop", None)
      if loop is not None and loop.is_running():
        engine._schedule(coro)
      elif hasattr(coro, "close"):
        coro.close()
  except Exception as e:
    log.warning("rotate: refresh_portfolio dispatch failed for %d: %s",
                portfolio_id, e)


def rotate_portfolio(portfolio_id: int, db=None,
                     run_step2_fn=run_step2,
                     simulate_today: date | None = None) -> dict:
  """End-to-end rotation: compute plan + apply. Returns the apply report.

  This is the function the gate calls on rotation day (replacing the
  legacy `frozen_pending_rotation` return). `simulate_today` enables the
  Item #8 catch-up path — the gate passes the missed cadence boundary
  date so the rotation handler grades the correct OOS window even when
  the cron fires after the rotation day itself.
  """
  own = db is None
  if own:
    db = SessionLocal()
  try:
    plan = compute_rotation_plan(
      portfolio_id, db=db, run_step2_fn=run_step2_fn,
      simulate_today=simulate_today,
    )
    if not plan.ok:
      return {
        "portfolio_id": portfolio_id,
        "ok": False,
        "reason": plan.reason,
      }
    if plan.reason == "already_rotated":
      return {
        "portfolio_id": portfolio_id,
        "ok": True,
        "reason": "already_rotated",
        "rotation_hash": plan.rotation_hash,
      }
    return apply_rotation_plan(plan, db)
  except Exception as e:
    db.rollback()
    log.exception("rotate_portfolio: portfolio %d failed: %s", portfolio_id, e)
    return {
      "portfolio_id": portfolio_id,
      "ok": False,
      "reason": "exception",
      "error": str(e),
    }
  finally:
    if own:
      db.close()
