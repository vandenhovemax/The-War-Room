"""T-L003 — Live Loop scheduler regression tests.

Context:
  Overnight Apr 23→24 2026, AsyncIOScheduler silently dropped L1 and L2
  nightly cron fires on Windows. The FastAPI event loop was idle-looped,
  APScheduler's asyncio variant never woke to fire the callbacks. Portfolios
  23/24 therefore never got their nightly refresh — observable as stale
  `last_refreshed_at` and a missing April PnL row.

  Fix: swap to BackgroundScheduler (thread-pool based), decoupled from the
  event loop. These tests pin:
    1. Job registration — L1/L2/L3/L4 are registered with the right cron
       triggers.
    2. Scheduler actually FIRES a near-future job within a bounded window.

  The second test is the direct regression test for the silent-drop bug:
  if BackgroundScheduler ever stops firing on our stack, this test catches it.
"""
from __future__ import annotations

import time
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from datetime import datetime, timedelta, timezone


# ── Job registration ─────────────────────────────────────────────

def test_scheduler_registers_all_three_jobs():
  """All six Live Loop jobs (L1, L2, L2b, L3, L4, L5) must be registered
  with the correct IDs and NYT-timezoned cron triggers.

  Asserting on IDs + cron fields (not just count) so a future "who renamed
  l2_hybrid_portfolio_refresh?" never passes silently.
  """
  from backend.api.app import build_live_loop_scheduler

  scheduler = build_live_loop_scheduler()
  try:
    jobs = {j.id: j for j in scheduler.get_jobs()}

    assert set(jobs.keys()) == {
      "l1_batch_store_refresh",
      "l2_hybrid_portfolio_refresh",
      "l2b_phase2_recorder_audit",
      "l3_oos_boundary",
      "l4_parity_canary",
      "l5_weekly_paper_refresh",
    }, f"unexpected job set: {list(jobs.keys())}"

    # Cron trigger verification — cron fields live on the trigger object.
    def _field(trigger, name):
      for f in trigger.fields:
        if f.name == name:
          return str(f)
      return None

    l1 = jobs["l1_batch_store_refresh"].trigger
    assert _field(l1, "hour") == "1"
    assert _field(l1, "minute") == "0"
    assert str(l1.timezone) == "America/New_York"

    l2 = jobs["l2_hybrid_portfolio_refresh"].trigger
    assert _field(l2, "hour") == "3"
    assert _field(l2, "minute") == "0"
    assert str(l2.timezone) == "America/New_York"

    l3 = jobs["l3_oos_boundary"].trigger
    assert _field(l3, "hour") == "4"
    assert _field(l3, "minute") == "0"
    assert str(l3.timezone) == "America/New_York"

    l4 = jobs["l4_parity_canary"].trigger
    assert _field(l4, "day") == "1"
    assert _field(l4, "hour") == "5"
    assert _field(l4, "minute") == "0"
    assert str(l4.timezone) == "America/New_York"

    # Misfire grace — prevents a missed fire from being silently dropped
    # when APScheduler wakes up late. The three daily jobs get 1h grace,
    # the monthly parity canary gets 12h (can tolerate later recovery).
    assert jobs["l1_batch_store_refresh"].misfire_grace_time == 3600
    assert jobs["l2_hybrid_portfolio_refresh"].misfire_grace_time == 3600
    assert jobs["l3_oos_boundary"].misfire_grace_time == 3600
    assert jobs["l4_parity_canary"].misfire_grace_time == 3600 * 12

    # coalesce=True prevents backlog stampede if the scheduler sleeps
    # through multiple fire windows. max_instances=1 prevents overlap.
    for jid, job in jobs.items():
      assert job.coalesce is True, f"{jid} must coalesce"
      assert job.max_instances == 1, f"{jid} must have max_instances=1"
  finally:
    # Scheduler is created stopped, but shutdown defensively in case a
    # future refactor flips the default.
    if scheduler.running:
      scheduler.shutdown(wait=False)


# ── The reliability regression test ──────────────────────────────

def test_scheduler_fires_job_on_near_future_cron():
  """The regression test for the Apr 23→24 silent-drop bug.

  Register a one-shot job 2 seconds in the future on a fresh
  BackgroundScheduler instance, start the scheduler, and assert the callback
  actually runs within 30 seconds. If BackgroundScheduler ever regresses to
  the AsyncIOScheduler silent-drop behavior on our stack (Python 3.13,
  Windows, APScheduler current version), this test fails and we catch it
  before it eats a nightly refresh.

  Uses `DateTrigger` rather than `CronTrigger` because we need sub-minute
  precision for the test to be fast. Both trigger types share the same
  scheduler-firing mechanism — if one fires, so does the other.
  """
  fired = threading.Event()

  def _cb():
    fired.set()

  scheduler = BackgroundScheduler(timezone="America/New_York")
  try:
    run_at = datetime.now(timezone.utc) + timedelta(seconds=2)
    scheduler.add_job(
      _cb,
      DateTrigger(run_date=run_at),
      id="regression_fire_test",
      misfire_grace_time=30,
    )
    scheduler.start()

    # Wait up to 30s for the callback to land. 2s fire delay + 28s slack
    # covers slow CI machines without being absurdly generous.
    assert fired.wait(timeout=30.0), (
      "BackgroundScheduler failed to fire a near-future job within 30s. "
      "This is the exact failure mode that caused the Apr 23→24 silent drop "
      "of L1/L2. Investigate scheduler thread-pool state, installed "
      "APScheduler version, and whether this process has a working "
      "threading.Event primitive before trusting the nightly cron."
    )

    # Belt-and-suspenders: after firing, the one-shot job should have been
    # removed from the job store (DateTrigger is not recurring).
    jobs = scheduler.get_jobs()
    assert not any(j.id == "regression_fire_test" for j in jobs), (
      "one-shot job still in scheduler after firing — job cleanup broken"
    )
  finally:
    if scheduler.running:
      scheduler.shutdown(wait=False)


def test_scheduler_is_background_not_asyncio():
  """Pin the scheduler class — the whole point of T-L003 is that we use
  BackgroundScheduler, not AsyncIOScheduler. A future refactor that swaps
  back to AsyncIO would silently re-introduce the overnight silent-drop bug.
  """
  from backend.api.app import build_live_loop_scheduler

  scheduler = build_live_loop_scheduler()
  try:
    cls_name = type(scheduler).__name__
    assert cls_name == "BackgroundScheduler", (
      f"Live Loop scheduler must be BackgroundScheduler (thread-pool based) "
      f"to survive event-loop idle-drops on Windows. Got {cls_name!r}. "
      f"T-L003 regression — do NOT revert to AsyncIOScheduler."
    )
  finally:
    if scheduler.running:
      scheduler.shutdown(wait=False)
