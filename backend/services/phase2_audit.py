"""T-002 Step 7 — Phase-2 recorder audit self-check.

Scans recent `LiveEvent` rows produced by the phase-2 recorder and
returns a structured defect report. PURE AUDIT — never writes,
never raises. Mirrors the design of `parity_canary.py`.

Run as a scheduled tick (post-nightly) or on demand from a route.

Defect categories
-----------------
1. **dispatch_pending_stale** — `dispatch_outcome=='pending'` on a real
   refresh event older than `pending_threshold_minutes`. Either the
   verify_dispatch_outcome poll never ran, or the engine never picked
   up the cache invalidation. Real defect; needs investigation.

2. **dispatch_failed** — `dispatch_outcome=='failed'` on any event in
   the lookback window. The engine was reachable, but the kelly cache
   never populated within the verify timeout.

3. **active_portfolio_silent** — portfolio is `is_active=True`, not
   deleted, paper/real type, activated more than `silence_days_threshold`
   days ago, but has ZERO phase-2 LiveEvent rows. Indicates the nightly
   tick never reached this portfolio (Stage 8 broken, cold-save skipped,
   or scheduler disabled).

4. **stub_persistent** — `phase1_window_num_synth_for_stub=True` on
   the same portfolio for more than `stub_persistence_threshold` events
   in a row. The optimizer's stub logic is leaking through to live
   decisions; `_build_live_trailing_window` may need a fix.

5. **continuity_missing_day** — for an active hybrid portfolio, the
   nightly cron should fire exactly once per UTC day. Zero `nightly_cron`
   events on a fully-active day = the cron never reached the portfolio
   that day. (T-002 v2 Item 2.)

6. **continuity_double_day** — two or more `nightly_cron` events on the
   same UTC day for the same portfolio. Duplicate cron fires (or APScheduler
   `coalesce=False` misconfig) leak the same decision twice. (T-002 v2 Item 2.)

7. **basket_vs_records_mismatch** — for the latest event in the lookback
   per portfolio, symbols present in `selected_records` (sliced by the
   event's OOS window) that are NOT in `chosen_basket`. Indicates the
   recorder dropped traded symbols from the basket snapshot — the audit
   trail no longer faithfully describes what was traded. (T-002 v2 Item 2.)

Benign states (NOT flagged)
---------------------------
- `engine_offline` — operator intentionally disconnected the engine.
  Reported under `engine_offline_count` for visibility but not as a defect.
- `pending` on a `phase2_evaluated_no_change` cold-save event — by
  design `cold_save` events skip the dispatch path (they ship with
  `dispatch_outcome='success'`).
- Cold-save events (`reconstructed=True && trigger=='cold_save'`) are
  excluded from dispatch-health metrics — they're synthetic anchors,
  not real dispatches.

Returns
-------
A dict with shape:
  {
    "generated_at_iso": str,
    "lookback_days": int,
    "events_scanned": int,
    "engine_offline_count": int,
    "cold_save_count": int,
    "defects": [
      {"category": str, "portfolio_id": int|None, "live_event_id": int|None,
       "detail": str, "evidence": dict},
      ...
    ],
    "verdict": "PASS" | "DEFECTS_FOUND",
  }
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.db.models import LiveEvent, ParityCanaryReport, Portfolio
from backend.services.phase2_recorder import (
  DISPATCH_ENGINE_OFFLINE,
  DISPATCH_FAILED,
  DISPATCH_PENDING,
  DISPATCH_SUCCESS,
  EVENT_TYPE_HYBRID_REFRESH,
  EVENT_TYPE_HYBRID_ROTATION,
  EVENT_TYPE_NO_CHANGE,
)


PHASE2_AUDIT_KIND = "phase2_recorder_continuity"


log = logging.getLogger(__name__)


_PHASE2_EVENT_TYPES = (
  EVENT_TYPE_HYBRID_REFRESH,
  EVENT_TYPE_HYBRID_ROTATION,
  EVENT_TYPE_NO_CHANGE,
)

# T-002 v2 Item 2: nightly cron fires once per UTC day. Continuity check
# counts only events stamped with this trigger — manual force_refresh ticks
# on the same day are legitimate and don't substitute for a missing cron fire.
_NIGHTLY_TRIGGER = "nightly_cron"


def _safe_loads(s: str | None) -> dict:
  if not s:
    return {}
  try:
    out = json.loads(s)
  except (ValueError, TypeError):
    return {}
  return out if isinstance(out, dict) else {}


def _is_cold_save(details: dict) -> bool:
  """Cold-save baseline events: synthetic, exclude from dispatch metrics."""
  return bool(details.get("reconstructed")) and details.get("trigger") == "cold_save"


def run_phase2_audit(
  db: Session,
  *,
  lookback_days: int = 7,
  pending_threshold_minutes: int = 30,
  silence_days_threshold: int = 2,
  stub_persistence_threshold: int = 3,
  now_utc: datetime | None = None,
) -> dict[str, Any]:
  """Scan recent recorder events and produce a defect report.

  All thresholds are caller-overridable. `now_utc` is injectable for
  deterministic tests.
  """
  now = now_utc or datetime.now(timezone.utc)
  since = now - timedelta(days=max(0, int(lookback_days)))

  rows = (
    db.query(LiveEvent)
    .filter(
      LiveEvent.event_type.in_(_PHASE2_EVENT_TYPES),
      LiveEvent.created_at >= since,
    )
    .order_by(LiveEvent.created_at.asc())
    .all()
  )

  defects: list[dict[str, Any]] = []
  engine_offline = 0
  cold_save_count = 0
  events_scanned = len(rows)

  pending_threshold = timedelta(minutes=max(0, int(pending_threshold_minutes)))

  # Track stub-persistence per portfolio.
  per_pid_stub_streak: dict[int, int] = defaultdict(int)
  per_pid_stub_evidence: dict[int, list[int]] = defaultdict(list)
  per_pid_seen: set[int] = set()

  for r in rows:
    details = _safe_loads(r.details)
    pid = r.portfolio_id
    outcome = details.get("dispatch_outcome", DISPATCH_PENDING)
    cold = _is_cold_save(details)

    if pid is not None:
      per_pid_seen.add(pid)

    if cold:
      cold_save_count += 1
      # Cold-save events are synthetic; skip dispatch defect checks.
      continue

    if outcome == DISPATCH_ENGINE_OFFLINE:
      engine_offline += 1
      continue

    # Dispatch-pending stale check.
    if outcome == DISPATCH_PENDING:
      # `phase2_evaluated_no_change` events have nothing to dispatch
      # (basket didn't change). The no-change branch in
      # hybrid_portfolio_refresh stamps them DISPATCH_SUCCESS at write
      # time; this guard catches legacy pre-fix rows that still carry
      # the legacy 'pending' sentinel and prevents false positives.
      if r.event_type == EVENT_TYPE_NO_CHANGE:
        continue
      ts = r.created_at
      if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
      age = (now - ts) if ts is not None else timedelta(0)
      if age > pending_threshold:
        defects.append({
          "category": "dispatch_pending_stale",
          "portfolio_id": pid,
          "live_event_id": r.id,
          "detail": (
            f"event {r.id} for portfolio {pid} stuck in 'pending' for "
            f"{int(age.total_seconds() / 60)} min (threshold "
            f"{pending_threshold_minutes} min)"
          ),
          "evidence": {
            "event_type": r.event_type,
            "created_at_iso": ts.isoformat() if ts else None,
            "trigger": details.get("trigger"),
          },
        })
      continue

    if outcome == DISPATCH_FAILED:
      defects.append({
        "category": "dispatch_failed",
        "portfolio_id": pid,
        "live_event_id": r.id,
        "detail": (
          f"event {r.id} for portfolio {pid} reported dispatch_outcome=failed "
          f"— engine reachable but kelly cache never populated within timeout"
        ),
        "evidence": {
          "event_type": r.event_type,
          "trigger": details.get("trigger"),
          "phase1_window_num": details.get("phase1_window_num"),
        },
      })
      continue

    # outcome == DISPATCH_SUCCESS — no defect on dispatch axis.
    _ = DISPATCH_SUCCESS  # docs the success branch

    # Stub-persistence tracking. Only count consecutive synth_for_stub
    # ticks per portfolio. Reset the streak when a non-synth event lands.
    if pid is not None:
      if details.get("phase1_window_num_synth_for_stub"):
        per_pid_stub_streak[pid] += 1
        per_pid_stub_evidence[pid].append(r.id)
      else:
        if per_pid_stub_streak[pid] > stub_persistence_threshold:
          defects.append({
            "category": "stub_persistent",
            "portfolio_id": pid,
            "live_event_id": per_pid_stub_evidence[pid][-1],
            "detail": (
              f"portfolio {pid} carried phase1_window_num_synth_for_stub=True "
              f"on {per_pid_stub_streak[pid]} consecutive events "
              f"(threshold {stub_persistence_threshold})"
            ),
            "evidence": {"streak_event_ids": list(per_pid_stub_evidence[pid])},
          })
        per_pid_stub_streak[pid] = 0
        per_pid_stub_evidence[pid] = []

  # Flush any portfolios still mid-streak at end of window.
  for pid, streak in per_pid_stub_streak.items():
    if streak > stub_persistence_threshold:
      defects.append({
        "category": "stub_persistent",
        "portfolio_id": pid,
        "live_event_id": per_pid_stub_evidence[pid][-1],
        "detail": (
          f"portfolio {pid} carried phase1_window_num_synth_for_stub=True "
          f"on {streak} consecutive events through end of audit window "
          f"(threshold {stub_persistence_threshold})"
        ),
        "evidence": {"streak_event_ids": list(per_pid_stub_evidence[pid])},
      })

  # Active-portfolio silence check.
  # Restricted to mode='hybrid' — non-hybrid portfolios never emit phase-2
  # events by design (L2 nightly filters them out in
  # hybrid_portfolio_refresh._hybrid_portfolios), so flagging them silent
  # is a false positive. Mirrors the hybrid-only scope the continuity check
  # below already enforces.
  silence_threshold = timedelta(days=max(0, int(silence_days_threshold)))
  active_portfolios = (
    db.query(Portfolio)
    .filter(
      Portfolio.mode == "hybrid",
      Portfolio.is_active == True,  # noqa: E712
      Portfolio.is_deleted == False,  # noqa: E712
      Portfolio.portfolio_type.in_(("paper", "real")),
    )
    .all()
  )
  for p in active_portfolios:
    if p.id in per_pid_seen:
      continue
    activated_at = p.activated_at
    if activated_at is None:
      # Fall back to portfolio.created_at as a defensive proxy.
      activated_at = p.created_at
    if activated_at is None:
      continue
    if activated_at.tzinfo is None:
      activated_at = activated_at.replace(tzinfo=timezone.utc)
    age = now - activated_at
    if age <= silence_threshold:
      continue
    # Also confirm there are NO phase-2 events at all (not just in window).
    has_any_event = (
      db.query(LiveEvent.id)
      .filter(
        LiveEvent.portfolio_id == p.id,
        LiveEvent.event_type.in_(_PHASE2_EVENT_TYPES),
      )
      .first()
    )
    if has_any_event is not None:
      continue  # has events outside lookback — recorder is reaching it
    defects.append({
      "category": "active_portfolio_silent",
      "portfolio_id": p.id,
      "live_event_id": None,
      "detail": (
        f"portfolio {p.id} ({p.name!r}) is active for "
        f"{age.days} days but has no phase-2 LiveEvent rows — "
        f"nightly tick never reached it. Cold-save it or check Stage 8."
      ),
      "evidence": {
        "portfolio_type": p.portfolio_type,
        "activated_at_iso": activated_at.isoformat(),
        "silence_days_threshold": silence_days_threshold,
      },
    })

  # T-002 v2 Item 2: per-day continuity + basket-vs-records cross-check.
  # Runs on hybrid portfolios only (non-hybrid portfolios don't produce
  # phase-2 events at all by design).
  defects.extend(_run_continuity_checks(
    db, now=now, lookback_days=lookback_days, rows=rows,
  ))

  return {
    "generated_at_iso": now.isoformat(),
    "lookback_days": lookback_days,
    "events_scanned": events_scanned,
    "engine_offline_count": engine_offline,
    "cold_save_count": cold_save_count,
    "defects": defects,
    "verdict": "DEFECTS_FOUND" if defects else "PASS",
  }


# ── T-002 v2 Item 2: continuity + basket-vs-records cross-check ──

def _iso_after(a_iso: str | None, b_iso: str | None) -> bool:
  """True if a_iso > b_iso (string compare on ISO-8601 — safe because
  ISO-8601 sorts lexically). False if either side is missing."""
  if not a_iso or not b_iso:
    return False
  return str(a_iso) > str(b_iso)


def _record_key(rec: dict) -> str | None:
  """Build the basket-key form (`B{batch_id}_{symbol}_{side}`) used by
  `chosen_basket` from a `selected_records` row. Returns None if the
  record is missing the symbol field — those rows can't be cross-checked."""
  sym = rec.get("symbol") or rec.get("asset")
  if not sym:
    return None
  side = rec.get("side") or rec.get("trade_side") or ""
  bid = rec.get("batch_id")
  if bid is not None:
    return f"B{bid}_{sym}_{side}"
  return f"{sym}_{side}"


def _run_continuity_checks(
  db: Session,
  *,
  now: datetime,
  lookback_days: int,
  rows: list[LiveEvent],
) -> list[dict[str, Any]]:
  """Per-day continuity + basket-vs-`selected_records` cross-check.

  Per-day continuity (PLAN section 8 (1)): for each fully-active UTC day
  in the lookback window, expect exactly one `nightly_cron` phase-2
  event. Zero rows = `continuity_missing_day` defect; >1 rows =
  `continuity_double_day` defect.

  Cross-check (PLAN section 8 (3)): for the latest event per portfolio
  in the lookback, slice the portfolio's current `selected_records` to
  the event's OOS window. Symbols present in the slice but missing
  from the event's `chosen_basket` = `basket_vs_records_mismatch`
  defect — recorder dropped traded symbols from the basket snapshot.

  Active-day rule (ATLAS spec): a day D is "active" only if the
  portfolio was `is_active=True` at 12:00 UTC on D — a midday anchor
  that's well after the 03:00 NYT cron fires. Partial-day activations
  are conservatively skipped.

  Cross-check is best-effort. `selected_records` is overwritten on each
  refresh, so older events lose their snapshot — only the latest event
  per portfolio gets cross-checked. Older events still get the per-day
  continuity check (which doesn't depend on a fresh snapshot).
  """
  defects: list[dict[str, Any]] = []

  hybrid_portfolios = (
    db.query(Portfolio)
    .filter(
      Portfolio.mode == "hybrid",
      Portfolio.is_deleted == False,  # noqa: E712
      Portfolio.portfolio_type.in_(("paper", "real")),
    )
    .all()
  )

  rows_by_pid: dict[int, list[LiveEvent]] = defaultdict(list)
  for r in rows:
    if r.portfolio_id is not None:
      rows_by_pid[r.portfolio_id].append(r)

  end_day = now.date()
  start_day = (now - timedelta(days=max(0, int(lookback_days)))).date()
  days: list = []
  d = start_day
  while d <= end_day:
    days.append(d)
    d += timedelta(days=1)

  for p in hybrid_portfolios:
    activated_at = p.activated_at or p.created_at
    if activated_at is None:
      continue
    if activated_at.tzinfo is None:
      activated_at = activated_at.replace(tzinfo=timezone.utc)
    deactivated_at = p.deactivated_at
    if deactivated_at is not None and deactivated_at.tzinfo is None:
      deactivated_at = deactivated_at.replace(tzinfo=timezone.utc)

    # Group this portfolio's nightly_cron events by UTC day. Cold-save
    # events are excluded (synthetic) and force_refresh / on_save events
    # don't substitute for missing cron fires.
    nightly_by_day: dict = defaultdict(list)
    pid_rows = rows_by_pid.get(p.id, [])
    for r in pid_rows:
      details = _safe_loads(r.details)
      if _is_cold_save(details):
        continue
      if details.get("trigger") != _NIGHTLY_TRIGGER:
        continue
      ts = r.created_at
      if ts is None:
        continue
      if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
      nightly_by_day[ts.date()].append(r)

    # Continuity is only meaningful once the portfolio has started
    # reporting. Span = [first_event_day, today]. A portfolio that has
    # NEVER reported in the lookback is caught by active_portfolio_silent
    # instead. This avoids false positives on freshly-activated portfolios
    # whose first nightly_cron tick simply hasn't landed yet.
    if not nightly_by_day:
      continue
    first_event_day = min(nightly_by_day.keys())

    # Per-day continuity — span first observed event day → today.
    for D in days:
      if D < first_event_day:
        continue
      anchor = datetime(D.year, D.month, D.day, 12, 0, 0, tzinfo=timezone.utc)
      if activated_at > anchor:
        continue  # not active at midday of D
      if deactivated_at is not None and deactivated_at < anchor:
        continue  # already deactivated by midday of D
      # Cron fires at 03:00 NYT = 07:00-08:00 UTC. Skip days where now
      # hasn't yet reached 09:00 UTC of D — the cron may not have fired.
      cron_window_end = datetime(D.year, D.month, D.day, 9, 0, 0, tzinfo=timezone.utc)
      if now < cron_window_end:
        continue

      events = nightly_by_day.get(D, [])
      if len(events) == 0:
        defects.append({
          "category": "continuity_missing_day",
          "portfolio_id": p.id,
          "live_event_id": None,
          "detail": (
            f"portfolio {p.id} ({p.name!r}) was active on {D.isoformat()} (UTC) "
            f"but no nightly_cron phase-2 event was recorded — cron likely "
            f"skipped this portfolio that day."
          ),
          "evidence": {
            "day_iso": D.isoformat(),
            "activated_at_iso": activated_at.isoformat(),
            "deactivated_at_iso": (
              deactivated_at.isoformat() if deactivated_at else None
            ),
          },
        })
      elif len(events) > 1:
        defects.append({
          "category": "continuity_double_day",
          "portfolio_id": p.id,
          "live_event_id": events[0].id,
          "detail": (
            f"portfolio {p.id} ({p.name!r}) recorded {len(events)} nightly_cron "
            f"phase-2 events on {D.isoformat()} (UTC) — expected exactly 1. "
            f"APScheduler coalesce misconfig or a duplicated trigger."
          ),
          "evidence": {
            "day_iso": D.isoformat(),
            "event_ids": [e.id for e in events],
            "event_types": [e.event_type for e in events],
          },
        })

    # Basket-vs-`selected_records` cross-check on latest non-cold-save
    # event in the lookback for this portfolio.
    latest: LiveEvent | None = None
    latest_details: dict[str, Any] = {}
    for r in sorted(pid_rows, key=lambda r: r.created_at, reverse=True):
      d_ = _safe_loads(r.details)
      if _is_cold_save(d_):
        continue
      latest = r
      latest_details = d_
      break
    if latest is None:
      continue

    chosen_basket = set(latest_details.get("chosen_basket") or [])
    if not chosen_basket:
      continue  # nothing meaningful to cross-check

    try:
      src = json.loads(p.source_strategies or "{}")
    except (ValueError, TypeError):
      src = {}

    # `refresh_step2_result` (latest refresh) takes precedence over the
    # original `step2_result`. Fall back to step2_result if the portfolio
    # has never been refreshed.
    sel_records: list = []
    rsr = src.get("refresh_step2_result") or {}
    if isinstance(rsr, dict) and rsr.get("selected_records"):
      sel_records = list(rsr["selected_records"])
    if not sel_records:
      step2 = src.get("step2_result") or {}
      if isinstance(step2, dict) and step2.get("selected_records"):
        sel_records = list(step2["selected_records"])
    if not sel_records:
      continue  # no records to cross-check against

    oos_start_iso = latest_details.get("phase1_window_oos_start_iso")
    oos_end_iso = latest_details.get("phase1_window_oos_end_iso")

    sliced: list[dict] = []
    for rec in sel_records:
      if not isinstance(rec, dict):
        continue
      rec_dt = rec.get("dt")
      if oos_start_iso and rec_dt and str(rec_dt) < str(oos_start_iso):
        continue
      if _iso_after(str(rec_dt) if rec_dt else None, oos_end_iso):
        continue
      sliced.append(rec)
    if not sliced:
      continue  # window doesn't overlap any records — can't cross-check

    record_keys: set[str] = set()
    for rec in sliced:
      key = _record_key(rec)
      if key is not None:
        record_keys.add(key)
    if not record_keys:
      continue

    orphans = record_keys - chosen_basket
    if orphans:
      defects.append({
        "category": "basket_vs_records_mismatch",
        "portfolio_id": p.id,
        "live_event_id": latest.id,
        "detail": (
          f"portfolio {p.id} ({p.name!r}) latest phase-2 event {latest.id} "
          f"chosen_basket missing {len(orphans)} symbol(s) that appear in "
          f"selected_records (window {oos_start_iso}..{oos_end_iso}): "
          f"{sorted(orphans)[:5]}"
        ),
        "evidence": {
          "missing_in_basket": sorted(orphans)[:20],
          "basket_size": len(chosen_basket),
          "records_in_window": len(record_keys),
          "oos_start_iso": oos_start_iso,
          "oos_end_iso": oos_end_iso,
        },
      })

  return defects


# ── Persistent writer (T-002 v2 Item 1) ───────────────────────────

def run_phase2_audit_and_persist(
  db: Session | None = None,
  *,
  lookback_days: int = 7,
  pending_threshold_minutes: int = 30,
  silence_days_threshold: int = 2,
  stub_persistence_threshold: int = 3,
  now_utc: datetime | None = None,
) -> dict[str, Any]:
  """Run the phase-2 recorder audit AND persist the result as one
  `ParityCanaryReport` row. Wired into the nightly APScheduler tick
  (post-Y-B refresh) so defects surface in the existing audit UI
  without an operator running the audit by hand.

  Contract:
    * One `ParityCanaryReport` row written per call (pass or defects-found).
      `report_json["kind"] = 'phase2_recorder_continuity'` is the discriminator
      that lets the audit UI separate phase-2 rows from the legacy
      cross-portfolio canary rows.
    * `total_combos` = events scanned. `combos_with_diff` = defect count.
    * On `DEFECTS_FOUND`, also emit ONE `LiveEvent` with
      `event_type='parity_canary'` so the Activity feed shows it
      (mirrors `parity_canary.run_parity_canary`).
    * Audit-only — never raises. A persistence failure rolls the row back
      and logs a warning; the function still returns the audit dict.

  Parameter `db` is optional — when omitted, the function opens a fresh
  `SessionLocal()` (matches the APScheduler call pattern of every other
  nightly job in this app). Tests pass an explicit Session for isolation.
  """
  own_session = db is None
  session: Session = db if db is not None else _open_session()
  try:
    audit_result = run_phase2_audit(
      session,
      lookback_days=lookback_days,
      pending_threshold_minutes=pending_threshold_minutes,
      silence_days_threshold=silence_days_threshold,
      stub_persistence_threshold=stub_persistence_threshold,
      now_utc=now_utc,
    )

    payload = {
      "kind": PHASE2_AUDIT_KIND,
      "verdict": audit_result["verdict"],
      "events_scanned": audit_result["events_scanned"],
      "engine_offline_count": audit_result["engine_offline_count"],
      "cold_save_count": audit_result["cold_save_count"],
      "defects": audit_result["defects"],
      "lookback_days": audit_result["lookback_days"],
      "thresholds": {
        "pending_threshold_minutes": pending_threshold_minutes,
        "silence_days_threshold": silence_days_threshold,
        "stub_persistence_threshold": stub_persistence_threshold,
      },
      "generated_at_iso": audit_result["generated_at_iso"],
    }

    run_dt = (now_utc or datetime.now(timezone.utc)).date().isoformat()
    try:
      with session.begin_nested():
        row = ParityCanaryReport(
          run_date=run_dt,
          portfolio_id=None,
          total_combos=audit_result["events_scanned"],
          combos_with_diff=len(audit_result["defects"]),
          report_json=json.dumps(payload, default=str),
        )
        session.add(row)
        session.flush()
        report_id = row.id
        if audit_result["defects"]:
          session.add(LiveEvent(
            portfolio_id=None,
            event_type="parity_canary",
            symbol="",
            details=json.dumps({
              "kind": PHASE2_AUDIT_KIND,
              "report_id": report_id,
              "run_date": run_dt,
              "verdict": audit_result["verdict"],
              "events_scanned": audit_result["events_scanned"],
              "defects_count": len(audit_result["defects"]),
            }),
          ))
      session.commit()
      audit_result["report_id"] = report_id
    except Exception as e:
      log.warning(
        "run_phase2_audit_and_persist: persistence failed "
        "(audit result still returned): %s", e,
      )
      try:
        session.rollback()
      except Exception:
        pass
      audit_result["report_id"] = None

    return audit_result
  finally:
    if own_session:
      try:
        session.close()
      except Exception:
        pass


def _open_session() -> Session:
  """Late-bound import so tests can monkeypatch `SessionLocal`."""
  from backend.db.database import SessionLocal
  return SessionLocal()
