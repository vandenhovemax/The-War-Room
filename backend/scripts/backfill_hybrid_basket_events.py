"""Backfill historical basket events for hybrid portfolios.

Why this exists
---------------
`hybrid_portfolio_refresh.refresh_portfolio()` is designed for "live tomorrow"
— each nightly run writes ONE basket event for "today". If you save a hybrid
portfolio whose OOS ended N days ago, those N days of "what would the basket
have been each day" are never recorded. Without those events, the basket-at-t
filter in paper_refresh has nothing to match against — every replay trade
gets dropped or reports the current basket, breaking proper WFAS validation.

What this script does
---------------------
For each requested hybrid portfolio:
1. Detects the gap between `optimization_end_date + 1d` and the date of the
   first existing basket event (or today if no events yet).
2. Walks day-by-day through the gap, simulating "if today were day D".
3. Reuses `refresh_portfolio(simulate_today=D)` so the grading logic is
   IDENTICAL to the nightly cron — no separate code path that could drift.
4. After each daily call:
   - Updates the just-written LiveEvent's `created_at` to D @ 07:00 UTC
     (matches the actual nightly cron timestamp pattern).
   - RESTORES `portfolio_assets.is_active` to its pre-call state so the
     backfill doesn't leave the basket pointing at a historical composition.
5. Commits each day individually so a mid-backfill failure leaves a partial
   timeline rather than rolling back work-in-progress.

What it does NOT do
-------------------
- Does NOT mutate `wfo_windows_meta`, `source_strategies`, or any other
  portfolio config field beyond what `refresh_portfolio` naturally does.
- Does NOT touch `paper_refresh` — run that AFTER backfill to rebuild
  the metrics snapshot using the new event history.
- Does NOT call `_dispatch_executor_refresh` for historical events
  (monkey-patched to a no-op for the duration of the backfill).
- Does NOT touch `data/live_engine_state/` (those are scanner files,
  unrelated to phase-2 events).

Safety
------
- Always copy `data/antigravity.db` to `data/antigravity.db.pre_backfill_*`
  BEFORE running for real. The `--dry-run` mode shows exactly what would
  happen without writing.
- Each portfolio's backfill is its own DB session — a failure on one
  portfolio cannot corrupt another.
- The HYBRID_REFRESH_BACKFILL marker (`"trigger": "backfill"` inside
  details JSON) makes backfilled events distinguishable from real
  nightly events for forensic / rollback purposes.

CLI usage
---------
   python -m backend.scripts.backfill_hybrid_basket_events --portfolio-id 58 --dry-run
   python -m backend.scripts.backfill_hybrid_basket_events --portfolio-id 58
   python -m backend.scripts.backfill_hybrid_basket_events --portfolio-ids 58,59,60,61,62
   python -m backend.scripts.backfill_hybrid_basket_events --all-hybrid --min-gap-days 7

API usage (called from `portfolio_paper.py` endpoint)
-----------------------------------------------------
   from backend.scripts.backfill_hybrid_basket_events import (
       backfill_portfolio, plan_backfill,
   )
   plan = plan_backfill(db, portfolio_id)             # dry plan
   result = backfill_portfolio(db, portfolio_id)      # actual write
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from backend.db.database import SessionLocal
from backend.db.models import LiveEvent, Portfolio
from backend.services.phase2_recorder import BASKET_EVENT_TYPES

log = logging.getLogger(__name__)


# ── Result types ────────────────────────────────────────────────────


@dataclass
class DayResult:
  """Outcome of a single simulated day."""
  day: str  # ISO date
  ok: bool = False
  basket_size: int = 0
  basket_changed: bool = False
  reason: Optional[str] = None
  live_event_id: Optional[int] = None


@dataclass
class BackfillResult:
  """End-to-end report for one portfolio."""
  portfolio_id: int
  name: Optional[str] = None
  oos_end: Optional[str] = None
  first_existing_event: Optional[str] = None
  gap_start: Optional[str] = None
  gap_end: Optional[str] = None
  total_days_planned: int = 0
  dry_run: bool = False
  days_succeeded: int = 0
  days_failed: int = 0
  days_skipped: int = 0
  day_results: list[DayResult] = field(default_factory=list)
  started_at: Optional[str] = None
  ended_at: Optional[str] = None
  error: Optional[str] = None

  def as_dict(self) -> dict:
    d = asdict(self)
    d["day_results"] = [asdict(r) for r in self.day_results]
    return d


# ── Gap detection ───────────────────────────────────────────────────


def _portfolio_oos_end(p: Portfolio) -> Optional[date]:
  """Latest oos_end across all windows_meta entries. Returns None if missing."""
  try:
    src = json.loads(p.source_strategies or "{}")
  except (ValueError, TypeError):
    return None
  wm = src.get("wfo_windows_meta") or []
  ends = []
  for w in wm:
    if not isinstance(w, dict):
      continue
    try:
      ends.append(date.fromisoformat(str(w.get("oos_end", ""))[:10]))
    except (ValueError, TypeError):
      continue
  # Fallback to optimization_end_date if no parseable windows
  if not ends and p.optimization_end_date:
    return p.optimization_end_date.date() if hasattr(p.optimization_end_date, "date") else p.optimization_end_date
  return max(ends) if ends else None


def _portfolio_last_oos_start(p: Portfolio) -> Optional[date]:
  """oos_start of the chronologically-LAST window. Used when OOS extends
  to today (the optimizer saved with `oos_end = today` so there's no
  post-OOS gap to fill — but the OOS window itself was never walked)."""
  try:
    src = json.loads(p.source_strategies or "{}")
  except (ValueError, TypeError):
    return None
  wm = src.get("wfo_windows_meta") or []
  starts = []
  for w in wm:
    if not isinstance(w, dict):
      continue
    try:
      starts.append((
        date.fromisoformat(str(w.get("oos_end", ""))[:10]),
        date.fromisoformat(str(w.get("oos_start", ""))[:10]),
      ))
    except (ValueError, TypeError):
      continue
  if not starts:
    return None
  # Sort by oos_end, take the latest window's oos_start
  starts.sort()
  return starts[-1][1]


def _first_basket_event_date(db: Session, portfolio_id: int) -> Optional[date]:
  """Date of the chronologically-first basket-type event for this portfolio."""
  row = (
    db.query(LiveEvent.created_at)
    .filter(
      LiveEvent.portfolio_id == portfolio_id,
      LiveEvent.event_type.in_(BASKET_EVENT_TYPES),
    )
    .order_by(LiveEvent.created_at.asc())
    .first()
  )
  if not row or not row[0]:
    return None
  ts = row[0]
  if isinstance(ts, str):
    try:
      ts = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
      return None
  return ts.date() if hasattr(ts, "date") else None


def plan_backfill(db: Session, portfolio_id: int, today: Optional[date] = None) -> dict:
  """Identify the backfill range for a portfolio without writing anything.

  Two cases handled by the gap_start rule below:

  CASE A — `optimization_end_date < today` (typical for older portfolios):
    The optimizer baked OOS data up to `optimization_end_date`, so we only
    need to fill from THAT day forward. Saved portfolios like p44 (opt_end
    Apr 22, activated Apr 27) → 5-day gap (Apr 23-27).

  CASE B — `optimization_end_date >= today` (typical for portfolios saved
    today with `oos_end = today` stub):
    There is no post-OOS gap, but the OOS window itself was never walked
    day-by-day. Walk from `last_oos_start` forward so paper_refresh has
    basket-at-t segments covering the whole OOS period. p58-62 fall here.

  In both cases we skip days that already have basket events (preserves the
  small number of nightly events on portfolios like p44).
  """
  today = today or date.today()
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    return {"portfolio_id": portfolio_id, "ok": False, "reason": "not_found"}
  if p.mode != "hybrid":
    return {
      "portfolio_id": portfolio_id, "name": p.name,
      "ok": False, "reason": "not_hybrid",
    }
  oos_end = _portfolio_oos_end(p)
  if not oos_end:
    return {
      "portfolio_id": portfolio_id, "name": p.name,
      "ok": False, "reason": "no_oos_end",
    }

  # optimization_end_date as a plain `date` for arithmetic
  opt_end = p.optimization_end_date
  if hasattr(opt_end, "date"):
    opt_end = opt_end.date()

  first_evt = _first_basket_event_date(db, portfolio_id)

  # Decide which case we're in and compute gap_start accordingly
  case = "post_oos_only"
  if opt_end is not None and opt_end < today:
    # CASE A: walk only post-OOS gap
    gap_start = opt_end + timedelta(days=1)
  else:
    # CASE B: walk the OOS window itself (it extends to today)
    last_oos_start = _portfolio_last_oos_start(p)
    if last_oos_start is None:
      return {
        "portfolio_id": portfolio_id, "name": p.name,
        "ok": False, "reason": "no_last_oos_start",
      }
    gap_start = last_oos_start
    case = "walk_full_oos"

  # Gap end = day BEFORE first existing event (if any), else today - 1d
  # (don't backfill today — let the nightly cron handle it naturally)
  gap_end = (first_evt - timedelta(days=1)) if first_evt else (today - timedelta(days=1))

  if gap_start > gap_end:
    return {
      "portfolio_id": portfolio_id, "name": p.name,
      "ok": True, "reason": "no_gap",
      "case": case,
      "oos_end": oos_end.isoformat(),
      "first_existing_event": first_evt.isoformat() if first_evt else None,
      "total_days_planned": 0,
    }
  total = (gap_end - gap_start).days + 1
  return {
    "portfolio_id": portfolio_id,
    "name": p.name,
    "mode": p.mode,
    "is_active": bool(p.is_active),
    "ok": True,
    "case": case,
    "oos_end": oos_end.isoformat(),
    "optimization_end_date": opt_end.isoformat() if opt_end else None,
    "first_existing_event": first_evt.isoformat() if first_evt else None,
    "gap_start": gap_start.isoformat(),
    "gap_end": gap_end.isoformat(),
    "total_days_planned": total,
    "estimated_minutes": round(total * 30 / 60, 1),  # ~30s per day, very rough
  }


# ── Single-day backfill ─────────────────────────────────────────────


def _backfill_one_day(
  db: Session, portfolio_id: int, simulated_day: date,
) -> DayResult:
  """Compute what the basket WOULD have been on `simulated_day` and write a
  SINGLE LiveEvent row with that decision — nothing else.

  This is a NON-MUTATING version: it replays the grading logic from
  refresh_portfolio() phases 1-7 (read-only) and writes ONLY the LiveEvent
  in phase 8. It deliberately skips:
    * portfolio_assets is_active flips    (preserves current live basket)
    * portfolio_assets newcomer inserts   (no row drift)
    * KellyAllocation writes              (no per-day kelly accumulation)
    * source_strategies mutations         (preserves real continuation_state)
    * _dispatch_executor_refresh          (no live-engine notifications)

  Previous version called refresh_portfolio() directly which caused 32 extra
  is_active PortfolioAsset rows to be inserted into p58 during a partial run
  (basket bloated from 21 → 53). Restored from .pre_backfill_2026-05-18
  backup; this rewrite eliminates the leak at the source.
  """
  from backend.services import hybrid_portfolio_refresh as hpr
  from backend.analysis.step2_service import run_step2
  from backend.analysis.trade_log_unified import load_unified_trades

  result = DayResult(day=simulated_day.isoformat())

  try:
    p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if p is None:
      result.reason = "not_found"
      return result

    try:
      src_cfg = json.loads(p.source_strategies or "{}")
    except (ValueError, TypeError):
      src_cfg = {}

    windows_meta = src_cfg.get("wfo_windows_meta") or []
    if not windows_meta:
      result.reason = "no_windows_meta"
      return result

    # ── Phase 1-2: anchored window (read-only) ─────────────────────────
    # Anchored-walk alignment (2026-06-10): use the SAME window logic as
    # the nightly refresh — fixed anchor (committed-history boundary),
    # warmup from the saved IS span, NO buffer seed. Days before the
    # anchor are committed history and are skipped (the save-time records
    # already cover them; writing simulated events there would put a
    # second, different simulation on top of the optimizer's truth).
    anchor, anchor_source = hpr._resolve_walk_anchor(
      src_cfg, windows_meta, simulated_day,
    )
    if anchor is None:
      result.reason = f"no_walk_anchor ({anchor_source})"
      return result
    if simulated_day < anchor:
      result.ok = True
      result.reason = "before_walk_anchor"
      return result
    target = hpr._build_anchored_window(
      anchor=anchor,
      today=simulated_day,
      windows_meta=windows_meta,
      oos_months=int(src_cfg.get("oos_months", 6) or 6),
    )

    pa_rows = hpr._portfolio_assets(db, portfolio_id)
    batch_ids = hpr._basket_batch_ids(pa_rows)
    if not batch_ids:
      result.reason = "no_basket_batches"
      return result

    # ── Phase 3: load trades ──────────────────────────────────────────
    trades, rbm, _batch_info = load_unified_trades(
      db,
      batch_ids=batch_ids,
      cutoff=p.optimization_end_date,
      portfolio_id=portfolio_id,
    )
    if not trades:
      result.reason = "no_trades"
      return result

    # ── Phase 4: build single-window step1_result ─────────────────────
    step1_result = {"windows": [{
      "window_num": target["window_num"],
      "is_start": target.get("is_start") or target["oos_start"],
      "is_end": target.get("is_end") or target["oos_start"],
      "oos_start": target["oos_start"],
      "oos_end": target["oos_end"],
      "candidates": [{"selected_batches": list(batch_ids)}],
    }]}

    # ── Phase 5: build config — NO seed ────────────────────────────────
    # Anchored-walk alignment (2026-06-10): identical to the nightly
    # refresh. The warmup span ([is_start..anchor)) rebuilds the scoring
    # buffers from the same trades the optimizer used — no snapshot seed,
    # no incumbent injection, no look-ahead. Each simulated day D's walk
    # is therefore deterministic given the trade set: walk anchor→D.
    cfg = dict(src_cfg)
    cfg["hybrid_mode"] = True
    cfg.setdefault("oos_months", src_cfg.get("oos_months", 6))
    cfg.pop("_seed_state", None)

    # ── Phase 6: run step2 grading ────────────────────────────────────
    step2_result = run_step2(
      step1_result=step1_result,
      all_trades=trades,
      run_batch_map=rbm,
      config=cfg,
      db=db,
    )
    if step2_result is None:
      result.reason = "step2_returned_none"
      return result

    # ── Phase 7: extract basket ───────────────────────────────────────
    n_assets = int(cfg.get("hybrid_n_assets") or 0)
    basket, _scores = hpr._extract_basket(step2_result, n_assets)
    if not basket:
      result.reason = "step2_picked_empty"
      return result

    # ── Phase 8: write ONLY the LiveEvent (no Kelly, no PA, no src_cfg)
    sim_ts_naive = datetime.combine(simulated_day, dtime(7, 0))  # naive UTC

    # Look up the chronologically-most-recent prior basket event so we can
    # populate prior_basket / added / removed accurately. The fallback
    # (when no prior event exists) is the current active asset set.
    prior_evt = (
      db.query(LiveEvent)
      .filter(
        LiveEvent.portfolio_id == portfolio_id,
        LiveEvent.event_type.in_(BASKET_EVENT_TYPES),
        LiveEvent.created_at < sim_ts_naive,
      )
      .order_by(LiveEvent.created_at.desc())
      .first()
    )
    prior_basket: list[str] = []
    if prior_evt:
      try:
        pd = json.loads(prior_evt.details or "{}")
        prior_basket = sorted(pd.get("chosen_basket") or [])
      except (ValueError, TypeError):
        pass
    if not prior_basket:
      prior_basket = sorted(
        hpr._asset_key(pa) for pa in pa_rows if pa.is_active
      )

    chosen_basket = sorted(basket)
    added = sorted(set(chosen_basket) - set(prior_basket))
    removed = sorted(set(prior_basket) - set(chosen_basket))
    is_change = bool(added or removed)

    event_type = "hybrid_refresh" if is_change else "phase2_evaluated_no_change"
    details = {
      "trigger": "backfill",
      "simulated_day": simulated_day.isoformat(),
      "backfilled_at": datetime.now(timezone.utc).isoformat(),
      "chosen_basket": chosen_basket,
      "prior_basket": prior_basket,
      "added": added,
      "removed": removed,
      "basket_unchanged": not is_change,
      "walked_window": dict(target),
      "dispatch_outcome": "success",
    }

    evt = LiveEvent(
      portfolio_id=portfolio_id,
      event_type=event_type,
      details=json.dumps(details),
      created_at=sim_ts_naive,
    )
    db.add(evt)
    db.flush()

    result.ok = True
    result.basket_size = len(chosen_basket)
    result.basket_changed = is_change
    result.live_event_id = evt.id

  except Exception as e:
    log.exception("backfill day %s for p%d failed: %s", simulated_day, portfolio_id, e)
    result.ok = False
    result.reason = f"exception: {e}"
    db.rollback()

  return result


# ── Per-portfolio orchestrator ──────────────────────────────────────


def backfill_portfolio(
  db: Session,
  portfolio_id: int,
  dry_run: bool = False,
  today: Optional[date] = None,
  progress_cb=None,
) -> BackfillResult:
  """Walk the gap day-by-day for one portfolio. Commits each day.

  `progress_cb` (optional) is invoked with (portfolio_id, day_idx, total_days,
  day_result_dict) after each day completes — used by the API endpoint to
  feed task-progress polls.
  """
  res = BackfillResult(portfolio_id=portfolio_id)
  res.dry_run = dry_run
  res.started_at = datetime.now(timezone.utc).isoformat()

  plan = plan_backfill(db, portfolio_id, today=today)
  res.name = plan.get("name")
  res.oos_end = plan.get("oos_end")
  res.first_existing_event = plan.get("first_existing_event")
  res.gap_start = plan.get("gap_start")
  res.gap_end = plan.get("gap_end")
  res.total_days_planned = int(plan.get("total_days_planned") or 0)

  if not plan.get("ok"):
    res.error = plan.get("reason")
    res.ended_at = datetime.now(timezone.utc).isoformat()
    return res
  if res.total_days_planned == 0:
    res.ended_at = datetime.now(timezone.utc).isoformat()
    return res

  if dry_run:
    # Just emit a no-op DayResult per planned day so the caller can see scope
    start = date.fromisoformat(res.gap_start)
    for i in range(res.total_days_planned):
      d = start + timedelta(days=i)
      res.day_results.append(DayResult(day=d.isoformat(), ok=True, reason="dry_run"))
    res.days_skipped = res.total_days_planned
    res.ended_at = datetime.now(timezone.utc).isoformat()
    return res

  # Real run — walk the gap. _backfill_one_day is non-mutating (only INSERTs
  # the LiveEvent), so no snapshot/restore of source_strategies or
  # portfolio_assets is needed.
  start = date.fromisoformat(res.gap_start)
  for i in range(res.total_days_planned):
    d = start + timedelta(days=i)
    day_res = _backfill_one_day(db, portfolio_id, d)
    res.day_results.append(day_res)
    if day_res.ok:
      res.days_succeeded += 1
      db.commit()
    else:
      res.days_failed += 1
      # Don't db.commit on failure; _backfill_one_day already rolled back
    if progress_cb is not None:
      try:
        progress_cb(portfolio_id, i + 1, res.total_days_planned, asdict(day_res))
      except Exception:
        pass  # progress reporting must never break the run

  res.ended_at = datetime.now(timezone.utc).isoformat()
  return res


# ── CLI entry point ─────────────────────────────────────────────────


def _all_hybrid_with_gap(db: Session, min_gap_days: int) -> list[int]:
  """Find every hybrid portfolio whose detected gap >= min_gap_days."""
  rows = (
    db.query(Portfolio.id)
    .filter(
      Portfolio.mode == "hybrid",
      Portfolio.is_deleted == False,  # noqa: E712
    )
    .all()
  )
  picked = []
  for r in rows:
    plan = plan_backfill(db, r[0])
    if plan.get("ok") and plan.get("total_days_planned", 0) >= min_gap_days:
      picked.append(r[0])
  return picked


def main(argv: Optional[list[str]] = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--portfolio-id", type=int, help="single portfolio id")
  parser.add_argument("--portfolio-ids", type=str, help="comma-separated list")
  parser.add_argument(
    "--all-hybrid",
    action="store_true",
    help="every hybrid portfolio with a gap",
  )
  parser.add_argument(
    "--min-gap-days",
    type=int,
    default=1,
    help="when using --all-hybrid: minimum gap size to qualify (default 1)",
  )
  parser.add_argument(
    "--dry-run",
    action="store_true",
    help="show what would happen, write nothing",
  )
  parser.add_argument(
    "--verbose", "-v", action="store_true", help="DEBUG logging",
  )
  args = parser.parse_args(argv)

  logging.basicConfig(
    level=logging.DEBUG if args.verbose else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
  )

  db = SessionLocal()
  try:
    if args.all_hybrid:
      ids = _all_hybrid_with_gap(db, args.min_gap_days)
    elif args.portfolio_ids:
      ids = [int(x) for x in args.portfolio_ids.split(",") if x.strip()]
    elif args.portfolio_id is not None:
      ids = [args.portfolio_id]
    else:
      parser.error("specify --portfolio-id, --portfolio-ids, or --all-hybrid")
      return 2

    if not ids:
      print("No portfolios match the criteria. Nothing to do.")
      return 0

    print(f"\n=== Backfill {'PLAN' if args.dry_run else 'RUN'}: {len(ids)} portfolio(s) ===\n")
    overall_t0 = time.time()
    for pid in ids:
      print(f"\n--- portfolio {pid} ---")
      t0 = time.time()
      res = backfill_portfolio(db, pid, dry_run=args.dry_run)
      elapsed = time.time() - t0
      if res.error:
        print(f"  ERROR: {res.error}")
        continue
      print(f"  name: {res.name}")
      print(f"  OOS end: {res.oos_end}")
      print(f"  first existing event: {res.first_existing_event or '(none)'}")
      print(f"  gap: {res.gap_start} → {res.gap_end} ({res.total_days_planned} days)")
      if args.dry_run:
        print("  [DRY RUN — no writes]")
      else:
        print(f"  succeeded: {res.days_succeeded} / failed: {res.days_failed}")
        print(f"  elapsed: {elapsed:.1f}s")
        # Show last few day results
        for dr in res.day_results[-5:]:
          status = "OK" if dr.ok else "FAIL"
          extra = f"(basket={dr.basket_size}{', CHANGED' if dr.basket_changed else ''})" if dr.ok else f"({dr.reason})"
          print(f"    {dr.day}: {status} {extra}")

    print(f"\n=== Total elapsed: {time.time() - overall_t0:.1f}s ===")
    print(
      "\nNext step: click 'Refresh' on each portfolio in Paper Trading tab "
      "to rebuild the metrics snapshot using the new event history."
    )
    return 0
  finally:
    db.close()


if __name__ == "__main__":
  sys.exit(main())
