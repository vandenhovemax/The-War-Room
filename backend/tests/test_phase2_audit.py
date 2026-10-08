"""T-002 Step 7 — phase-2 recorder audit self-check tests.

Coverage:
  A1. All-clean state → verdict PASS, zero defects.
  A2. dispatch_outcome=='pending' on a stale event → flagged dispatch_pending_stale.
  A3. dispatch_outcome=='pending' on a fresh event → NOT flagged (within threshold).
  A4. dispatch_outcome=='failed' → flagged dispatch_failed.
  A5. dispatch_outcome=='engine_offline' → benign, counted not flagged.
  A6. Cold-save events excluded from dispatch metrics + counted separately.
  A7. Active portfolio with zero phase-2 events past silence threshold → flagged active_portfolio_silent.
  A8. Active portfolio with events outside lookback (but >0 total) → NOT flagged.
  A9. Inactive / deleted / saved portfolios → NOT flagged regardless.
  A10. Stub-persistence streak > threshold → flagged stub_persistent.
  A11. Stub-streak interrupted by non-synth event → not flagged.
  A12. Lookback window respected (older events ignored).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, LiveEvent, ParityCanaryReport, Portfolio
from backend.services.phase2_audit import (
  PHASE2_AUDIT_KIND,
  run_phase2_audit,
  run_phase2_audit_and_persist,
)
from backend.services.phase2_recorder import (
  EVENT_TYPE_HYBRID_REFRESH,
  EVENT_TYPE_NO_CHANGE,
)


_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _set_pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
  cur.execute("PRAGMA journal_mode=WAL")
  cur.execute("PRAGMA foreign_keys=ON")
  cur.close()


_Session = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


@pytest.fixture(scope="module", autouse=True)
def _schema():
  Base.metadata.create_all(bind=_ENGINE)
  yield
  Base.metadata.drop_all(bind=_ENGINE)


@pytest.fixture()
def db():
  s = _Session()
  try:
    yield s
  finally:
    s.rollback()
    s.query(ParityCanaryReport).delete()
    s.query(LiveEvent).delete()
    s.query(Portfolio).delete()
    s.commit()
    s.close()


# ── Fixture helpers ────────────────────────────────────────────────────

NOW = datetime(2026, 4, 25, 14, 0, 0, tzinfo=timezone.utc)


def _portfolio(
  db, *, name: str = "P", is_active: bool = True, is_deleted: bool = False,
  portfolio_type: str = "paper", activated_at: datetime | None = None,
  created_at: datetime | None = None,
) -> Portfolio:
  p = Portfolio(
    name=name, portfolio_type=portfolio_type, mode="hybrid",
    is_active=is_active, is_deleted=is_deleted,
    activated_at=activated_at,
    created_at=created_at or (NOW - timedelta(days=10)),
    source_strategies="{}",
  )
  db.add(p)
  db.commit()
  return p


def _event(
  db, portfolio_id: int, *, event_type: str = EVENT_TYPE_HYBRID_REFRESH,
  outcome: str = "success", reconstructed: bool = False, trigger: str = "nightly_cron",
  synth_for_stub: bool = False, created_at: datetime | None = None,
) -> LiveEvent:
  details = {
    "decided_at_iso": (created_at or NOW).isoformat(),
    "trigger": trigger,
    "phase1_window_num": 8,
    "phase1_window_num_synth_for_stub": synth_for_stub,
    "prior_basket": [], "chosen_basket": [], "added": [], "removed": [],
    "basket_unchanged": False,
    "dispatch_outcome": outcome,
    "reconstructed": reconstructed,
  }
  row = LiveEvent(
    portfolio_id=portfolio_id, event_type=event_type, symbol="",
    details=json.dumps(details), created_at=created_at or NOW,
  )
  db.add(row)
  db.commit()
  return row


# ── A1. Clean state ────────────────────────────────────────────────────

def test_audit_clean_state_returns_pass(db):
  p = _portfolio(db, name="P-clean", activated_at=NOW - timedelta(days=5))
  _event(db, p.id, outcome="success", created_at=NOW - timedelta(hours=2))

  report = run_phase2_audit(db, now_utc=NOW)
  assert report["verdict"] == "PASS"
  assert report["defects"] == []
  assert report["events_scanned"] == 1
  assert report["engine_offline_count"] == 0


# ── A2. Stale pending → flagged ───────────────────────────────────────

def test_audit_flags_stale_pending_event(db):
  p = _portfolio(db, name="P-stale", activated_at=NOW - timedelta(days=5))
  e = _event(
    db, p.id, outcome="pending",
    created_at=NOW - timedelta(hours=2),  # well past 30-min threshold
  )

  report = run_phase2_audit(db, now_utc=NOW, pending_threshold_minutes=30)
  assert report["verdict"] == "DEFECTS_FOUND"
  cats = [d["category"] for d in report["defects"]]
  assert "dispatch_pending_stale" in cats
  defect = next(d for d in report["defects"] if d["category"] == "dispatch_pending_stale")
  assert defect["live_event_id"] == e.id
  assert defect["portfolio_id"] == p.id


# ── A3. Fresh pending → NOT flagged ───────────────────────────────────

def test_audit_does_not_flag_fresh_pending_event(db):
  p = _portfolio(db, name="P-fresh", activated_at=NOW - timedelta(days=5))
  _event(
    db, p.id, outcome="pending",
    created_at=NOW - timedelta(minutes=5),  # under 30-min threshold
  )

  report = run_phase2_audit(db, now_utc=NOW, pending_threshold_minutes=30)
  assert report["verdict"] == "PASS"
  assert report["defects"] == []


# ── A4. Failed → flagged ──────────────────────────────────────────────

def test_audit_flags_dispatch_failed(db):
  p = _portfolio(db, name="P-failed", activated_at=NOW - timedelta(days=5))
  e = _event(db, p.id, outcome="failed", created_at=NOW - timedelta(hours=1))

  report = run_phase2_audit(db, now_utc=NOW)
  cats = [d["category"] for d in report["defects"]]
  assert "dispatch_failed" in cats
  defect = next(d for d in report["defects"] if d["category"] == "dispatch_failed")
  assert defect["live_event_id"] == e.id


# ── A5. engine_offline → benign ───────────────────────────────────────

def test_audit_engine_offline_is_benign(db):
  p = _portfolio(db, name="P-offline", activated_at=NOW - timedelta(days=5))
  # Two engine_offline events on different UTC days (continuity check
  # only allows one nightly_cron event per day per portfolio).
  _event(db, p.id, outcome="engine_offline", created_at=NOW - timedelta(hours=1))
  _event(db, p.id, outcome="engine_offline", created_at=NOW - timedelta(hours=26))

  report = run_phase2_audit(db, now_utc=NOW)
  assert report["verdict"] == "PASS"
  assert report["engine_offline_count"] == 2
  assert report["defects"] == []


# ── A6. Cold-save events excluded from dispatch metrics ───────────────

def test_audit_cold_save_excluded_from_dispatch_metrics(db):
  p = _portfolio(db, name="P-cold", activated_at=NOW - timedelta(days=5))
  # A cold-save event with dispatch_outcome=success — must not count against
  # dispatch metrics; counted separately under cold_save_count.
  _event(
    db, p.id, event_type=EVENT_TYPE_NO_CHANGE, outcome="success",
    reconstructed=True, trigger="cold_save",
    created_at=NOW - timedelta(hours=24),
  )
  # Also add a real successful event so silence check passes.
  _event(db, p.id, outcome="success", created_at=NOW - timedelta(hours=1))

  report = run_phase2_audit(db, now_utc=NOW)
  assert report["verdict"] == "PASS"
  assert report["cold_save_count"] == 1
  assert report["events_scanned"] == 2


def test_audit_cold_save_pending_does_not_flag(db):
  """If a cold-save event somehow has pending (shouldn't, but defensive),
  it must NOT be flagged as dispatch_pending_stale."""
  p = _portfolio(db, name="P-cold-pending", activated_at=NOW - timedelta(days=5))
  _event(
    db, p.id, event_type=EVENT_TYPE_NO_CHANGE, outcome="pending",
    reconstructed=True, trigger="cold_save",
    created_at=NOW - timedelta(hours=2),
  )

  report = run_phase2_audit(db, now_utc=NOW, pending_threshold_minutes=30)
  assert report["verdict"] == "PASS"
  assert report["cold_save_count"] == 1
  assert all(d["category"] != "dispatch_pending_stale" for d in report["defects"])


# ── A7. Active portfolio silent past threshold → flagged ──────────────

def test_audit_flags_active_portfolio_with_no_events(db):
  p = _portfolio(
    db, name="P-silent", is_active=True, portfolio_type="paper",
    activated_at=NOW - timedelta(days=5),
  )
  # No events seeded.

  report = run_phase2_audit(db, now_utc=NOW, silence_days_threshold=2)
  cats = [d["category"] for d in report["defects"]]
  assert "active_portfolio_silent" in cats
  defect = next(d for d in report["defects"] if d["category"] == "active_portfolio_silent")
  assert defect["portfolio_id"] == p.id
  assert defect["live_event_id"] is None


def test_audit_does_not_flag_silent_non_hybrid_portfolio(db):
  # Non-hybrid portfolios never produce phase-2 events by design (L2 nightly
  # filters mode='hybrid' only). They must NOT be flagged active_portfolio_silent
  # — pre-fix this regressed when many paper non-hybrid portfolios were activated
  # 2026-04-25..29 and inflated combos_with_diff from ~8 to ~40.
  p = Portfolio(
    name="P-silent-non-hybrid", portfolio_type="paper", mode="non_hybrid",
    is_active=True, is_deleted=False,
    activated_at=NOW - timedelta(days=5),
    created_at=NOW - timedelta(days=10),
    source_strategies="{}",
  )
  db.add(p)
  db.commit()

  report = run_phase2_audit(db, now_utc=NOW, silence_days_threshold=2)
  silent_pids = [
    d["portfolio_id"] for d in report["defects"]
    if d["category"] == "active_portfolio_silent"
  ]
  assert p.id not in silent_pids, (
    "non-hybrid portfolio must not be flagged active_portfolio_silent — "
    "they don't emit phase-2 events by design"
  )


# ── A8. Active portfolio with events outside lookback → not flagged ───

def test_audit_does_not_flag_active_with_old_events(db):
  p = _portfolio(
    db, name="P-old-event", is_active=True, portfolio_type="paper",
    activated_at=NOW - timedelta(days=30),
  )
  # Event outside the 7-day lookback but exists in the table.
  _event(db, p.id, outcome="success", created_at=NOW - timedelta(days=14))

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  cats = [d["category"] for d in report["defects"]]
  assert "active_portfolio_silent" not in cats, (
    "portfolio with events outside lookback must not be flagged silent"
  )


# ── A9. Inactive / deleted / saved → not flagged ──────────────────────

def test_audit_does_not_flag_inactive_portfolio(db):
  _portfolio(
    db, name="P-inactive", is_active=False, portfolio_type="paper",
    activated_at=NOW - timedelta(days=5),
  )
  report = run_phase2_audit(db, now_utc=NOW)
  assert report["verdict"] == "PASS"


def test_audit_does_not_flag_deleted_portfolio(db):
  _portfolio(
    db, name="P-deleted", is_active=True, is_deleted=True,
    portfolio_type="paper", activated_at=NOW - timedelta(days=5),
  )
  report = run_phase2_audit(db, now_utc=NOW)
  assert report["verdict"] == "PASS"


def test_audit_does_not_flag_saved_portfolio(db):
  _portfolio(
    db, name="P-saved", is_active=True, portfolio_type="saved",
    activated_at=NOW - timedelta(days=5),
  )
  report = run_phase2_audit(db, now_utc=NOW)
  assert report["verdict"] == "PASS"


# ── A10. Stub-persistence → flagged ───────────────────────────────────

def test_audit_flags_stub_persistence_streak(db):
  p = _portfolio(db, name="P-stub", activated_at=NOW - timedelta(days=10))
  for i in range(5):
    _event(
      db, p.id, outcome="success", synth_for_stub=True,
      created_at=NOW - timedelta(hours=24 * (5 - i)),
    )

  report = run_phase2_audit(db, now_utc=NOW, stub_persistence_threshold=3)
  cats = [d["category"] for d in report["defects"]]
  assert "stub_persistent" in cats
  defect = next(d for d in report["defects"] if d["category"] == "stub_persistent")
  assert defect["portfolio_id"] == p.id
  assert len(defect["evidence"]["streak_event_ids"]) == 5


def test_audit_stub_streak_interrupted_does_not_flag(db):
  p = _portfolio(db, name="P-stub-int", activated_at=NOW - timedelta(days=10))
  # 4 synth events, then a non-synth, then 1 synth — streak resets.
  base = NOW - timedelta(days=6)
  for i in range(4):
    _event(
      db, p.id, outcome="success", synth_for_stub=True,
      created_at=base + timedelta(hours=i),
    )
  _event(
    db, p.id, outcome="success", synth_for_stub=False,
    created_at=base + timedelta(hours=10),
  )
  _event(
    db, p.id, outcome="success", synth_for_stub=True,
    created_at=base + timedelta(hours=12),
  )

  report = run_phase2_audit(db, now_utc=NOW, stub_persistence_threshold=3)
  # The first 4-streak DID exceed threshold of 3 — gets flagged at the
  # interruption point.
  cats = [d["category"] for d in report["defects"]]
  assert "stub_persistent" in cats
  defect = next(d for d in report["defects"] if d["category"] == "stub_persistent")
  assert len(defect["evidence"]["streak_event_ids"]) == 4


# ── A12. Lookback window respected ────────────────────────────────────

def test_audit_lookback_window_excludes_old_events(db):
  p = _portfolio(db, name="P-lookback", activated_at=NOW - timedelta(days=30))
  # Event 14 days old — outside default 7-day lookback.
  _event(db, p.id, outcome="failed", created_at=NOW - timedelta(days=14))
  # Event 1 day old — inside lookback.
  _event(db, p.id, outcome="success", created_at=NOW - timedelta(days=1))

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  assert report["events_scanned"] == 1, "old event must be excluded"
  cats = [d["category"] for d in report["defects"]]
  assert "dispatch_failed" not in cats, "old failed event must not surface"


# ── A11. (renumbered) — all defect categories report shape sanity ─────

def test_audit_report_shape_keys(db):
  p = _portfolio(db, name="P-shape", activated_at=NOW - timedelta(days=5))
  _event(db, p.id, outcome="success", created_at=NOW - timedelta(hours=1))
  report = run_phase2_audit(db, now_utc=NOW)

  for k in (
    "generated_at_iso", "lookback_days", "events_scanned",
    "engine_offline_count", "cold_save_count", "defects", "verdict",
  ):
    assert k in report, f"missing report key {k!r}"
  assert isinstance(report["defects"], list)


# ============================================================
# T-002 v2 Item 1 — run_phase2_audit_and_persist tests
# ============================================================

def _all_reports(db):
  return db.query(ParityCanaryReport).order_by(ParityCanaryReport.id.asc()).all()


def test_persist_writes_paritycanaryreport_on_clean_run(db):
  """A clean audit still writes one ParityCanaryReport row so operators
  can confirm 'audit ran today'. combos_with_diff=0 marks it benign."""
  p = _portfolio(db, name="P-persist-clean", activated_at=NOW - timedelta(days=5))
  _event(db, p.id, outcome="success", created_at=NOW - timedelta(hours=1))

  result = run_phase2_audit_and_persist(db, now_utc=NOW)

  assert result["verdict"] == "PASS"
  assert result["report_id"] is not None
  reports = _all_reports(db)
  assert len(reports) == 1
  row = reports[0]
  assert row.combos_with_diff == 0
  assert row.total_combos == 1
  payload = json.loads(row.report_json)
  assert payload["kind"] == PHASE2_AUDIT_KIND
  assert payload["verdict"] == "PASS"
  assert payload["defects"] == []
  # No LiveEvent emitted on a clean run.
  liveevents = db.query(LiveEvent).filter(
    LiveEvent.event_type == "parity_canary",
  ).all()
  assert liveevents == []


def test_persist_writes_paritycanaryreport_and_liveevent_on_defects(db):
  """Defect path: ParityCanaryReport row carries kind + defect specifics
  in report_json, AND a LiveEvent of type='parity_canary' is emitted so
  the Activity feed surfaces the audit verdict to operators."""
  p = _portfolio(db, name="P-persist-bad", activated_at=NOW - timedelta(days=5))
  e = _event(
    db, p.id, outcome="failed",
    created_at=NOW - timedelta(hours=1),
  )

  result = run_phase2_audit_and_persist(db, now_utc=NOW)

  assert result["verdict"] == "DEFECTS_FOUND"
  reports = _all_reports(db)
  assert len(reports) == 1
  row = reports[0]
  assert row.combos_with_diff >= 1
  payload = json.loads(row.report_json)
  assert payload["kind"] == PHASE2_AUDIT_KIND
  assert any(
    d["category"] == "dispatch_failed" and d["live_event_id"] == e.id
    for d in payload["defects"]
  )
  # Activity-feed LiveEvent emitted.
  ae = db.query(LiveEvent).filter(
    LiveEvent.event_type == "parity_canary",
  ).all()
  assert len(ae) == 1
  ae_details = json.loads(ae[0].details)
  assert ae_details["kind"] == PHASE2_AUDIT_KIND
  assert ae_details["report_id"] == row.id
  assert ae_details["defects_count"] == row.combos_with_diff


def test_persist_run_date_is_now_utc_date(db):
  """run_date stamped on the ParityCanaryReport row should match the
  caller-supplied now_utc's date in YYYY-MM-DD form (deterministic
  for the daily-rollup audit UI)."""
  p = _portfolio(db, name="P-date", activated_at=NOW - timedelta(days=5))
  _event(db, p.id, outcome="success", created_at=NOW - timedelta(hours=1))

  result = run_phase2_audit_and_persist(db, now_utc=NOW)
  assert result["report_id"] is not None
  rows = _all_reports(db)
  assert rows[-1].run_date == NOW.date().isoformat()


def test_persist_does_not_raise_on_internal_failure(db, monkeypatch):
  """Persistence is best-effort. If the ParityCanaryReport write blows
  up, the audit dict is still returned with report_id=None and no
  exception leaks to the caller."""
  p = _portfolio(db, name="P-fail-write", activated_at=NOW - timedelta(days=5))
  _event(db, p.id, outcome="success", created_at=NOW - timedelta(hours=1))

  # Force the inner JSON dump to raise so the SAVEPOINT block fails.
  import backend.services.phase2_audit as audit_mod

  original_dumps = audit_mod.json.dumps
  def _boom(*args, **kwargs):
    raise RuntimeError("simulated json failure")

  monkeypatch.setattr(audit_mod.json, "dumps", _boom)
  try:
    result = run_phase2_audit_and_persist(db, now_utc=NOW)
  finally:
    monkeypatch.setattr(audit_mod.json, "dumps", original_dumps)

  assert result["verdict"] == "PASS"
  assert result["report_id"] is None


def test_persist_opens_its_own_session_when_db_is_none(monkeypatch):
  """Mirrors the APScheduler call pattern (no db arg). Verify the helper
  opens a SessionLocal() and closes it cleanly."""
  s = _Session()
  s.query(LiveEvent).delete()
  s.query(ParityCanaryReport).delete()
  s.query(Portfolio).delete()
  s.commit()
  p = Portfolio(
    name="P-own-session", portfolio_type="paper", mode="hybrid",
    is_active=True, is_deleted=False,
    activated_at=NOW - timedelta(days=5),
    source_strategies="{}",
  )
  s.add(p)
  s.commit()
  s.close()

  closed_count = {"n": 0}

  class _SessionTrack:
    def __init__(self, *a, **kw):
      self._inner = _Session(*a, **kw)
    def __getattr__(self, name):
      return getattr(self._inner, name)
    def close(self):
      closed_count["n"] += 1
      return self._inner.close()

  import backend.services.phase2_audit as audit_mod
  monkeypatch.setattr(audit_mod, "_open_session", lambda: _SessionTrack())

  result = run_phase2_audit_and_persist(db=None, now_utc=NOW)

  assert result["verdict"] in ("PASS", "DEFECTS_FOUND")
  assert closed_count["n"] == 1, "helper must close the session it opened"

  s2 = _Session()
  try:
    rows = s2.query(ParityCanaryReport).all()
    assert len(rows) == 1
    assert json.loads(rows[0].report_json)["kind"] == PHASE2_AUDIT_KIND
  finally:
    s2.query(LiveEvent).delete()
    s2.query(ParityCanaryReport).delete()
    s2.query(Portfolio).delete()
    s2.commit()
    s2.close()


def test_scheduler_registers_phase2_audit_job():
  """L2b nightly tick is registered at 03:30 NYT. Asserts both that the
  job exists and that the registered callable is the persistent variant
  (not the pure-return variant)."""
  from backend.api.app import build_live_loop_scheduler
  from backend.services import phase2_audit as audit_mod

  scheduler = build_live_loop_scheduler()
  job = scheduler.get_job("l2b_phase2_recorder_audit")
  assert job is not None, "phase-2 audit job must be registered"
  assert job.func is audit_mod.run_phase2_audit_and_persist
  # Scheduler is constructed-not-started; nothing to shut down. The
  # in-process job stores GC cleanly when the local goes out of scope.


# ============================================================
# B. Per-day continuity + basket-vs-`selected_records` cross-check
#    (T-002 v2 Item 2)
# ============================================================

def _hybrid_portfolio(db, *, name: str, activated_days_ago: int,
                     selected_records: list[dict] | None = None,
                     source_strategies: dict | None = None):
  """Active hybrid paper portfolio with optional selected_records snapshot
  embedded in source_strategies.refresh_step2_result."""
  src = source_strategies or {}
  if selected_records is not None:
    rsr = src.setdefault("refresh_step2_result", {})
    rsr["selected_records"] = selected_records
  p = Portfolio(
    name=name, portfolio_type="paper", mode="hybrid",
    is_active=True, is_deleted=False,
    activated_at=NOW - timedelta(days=activated_days_ago),
    created_at=NOW - timedelta(days=activated_days_ago + 1),
    source_strategies=json.dumps(src),
  )
  db.add(p)
  db.commit()
  return p


def _nightly_event(db, portfolio_id, *, day_offset: int,
                   chosen_basket: list[str] | None = None,
                   oos_start_iso: str | None = None,
                   oos_end_iso: str | None = None,
                   trigger: str = "nightly_cron",
                   reconstructed: bool = False):
  """Phase-2 nightly_cron event with custom day_offset (negative = past)
  and optional chosen_basket / OOS window for the cross-check."""
  ts = NOW + timedelta(days=day_offset)
  details = {
    "decided_at_iso": ts.isoformat(),
    "trigger": trigger,
    "phase1_window_num": 8,
    "phase1_window_num_synth_for_stub": False,
    "prior_basket": [], "chosen_basket": chosen_basket or [],
    "added": [], "removed": [],
    "basket_unchanged": False,
    "dispatch_outcome": "success",
    "reconstructed": reconstructed,
    "phase1_window_oos_start_iso": oos_start_iso,
    "phase1_window_oos_end_iso": oos_end_iso,
  }
  row = LiveEvent(
    portfolio_id=portfolio_id, event_type=EVENT_TYPE_HYBRID_REFRESH,
    symbol="", details=json.dumps(details), created_at=ts,
  )
  db.add(row)
  db.commit()
  return row


# ── B1. Per-day continuity — missing day flagged ─────────────────────

def test_continuity_flags_missing_day_between_events(db):
  """Portfolio activated 5 days ago, has events on day -3 and day -1,
  but day -2 is missing. Continuity check flags day -2 as missing."""
  p = _hybrid_portfolio(db, name="P-gap", activated_days_ago=5)
  _nightly_event(db, p.id, day_offset=-3)
  _nightly_event(db, p.id, day_offset=-1)

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  cats = [d["category"] for d in report["defects"]]
  missing = [d for d in report["defects"] if d["category"] == "continuity_missing_day"]
  assert "continuity_missing_day" in cats
  # Day -2 is the gap; days -3 and -1 are covered.
  missing_days = {d["evidence"]["day_iso"] for d in missing}
  assert (NOW - timedelta(days=2)).date().isoformat() in missing_days


# ── B2. Per-day continuity — newly-active portfolio NOT flagged ──────

def test_continuity_does_not_flag_before_first_event(db):
  """A portfolio that was activated 5 days ago but only has its first
  event today should NOT be flagged for missing the previous 4 days.
  active_portfolio_silent covers the never-reported case; continuity
  only spans first-observed-event → today."""
  p = _hybrid_portfolio(db, name="P-fresh-report", activated_days_ago=5)
  _nightly_event(db, p.id, day_offset=0)  # first event is today

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  cats = [d["category"] for d in report["defects"]]
  assert "continuity_missing_day" not in cats


# ── B3. Per-day continuity — duplicate cron flagged ──────────────────

def test_continuity_flags_double_cron_fire_on_same_day(db):
  """Two nightly_cron events on the same UTC day for the same portfolio
  surface as continuity_double_day (APScheduler coalesce misconfig
  or duplicated trigger)."""
  p = _hybrid_portfolio(db, name="P-dup", activated_days_ago=5)
  _nightly_event(db, p.id, day_offset=-1)  # first cron of day -1
  # Second event also on day -1, 1 hour later — same UTC day.
  ts2 = NOW - timedelta(days=1, hours=-1)
  details2 = {
    "decided_at_iso": ts2.isoformat(), "trigger": "nightly_cron",
    "chosen_basket": [], "dispatch_outcome": "success",
  }
  db.add(LiveEvent(
    portfolio_id=p.id, event_type=EVENT_TYPE_HYBRID_REFRESH,
    symbol="", details=json.dumps(details2), created_at=ts2,
  ))
  db.commit()

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  cats = [d["category"] for d in report["defects"]]
  assert "continuity_double_day" in cats
  doubles = [d for d in report["defects"] if d["category"] == "continuity_double_day"]
  assert doubles[0]["evidence"]["day_iso"] == (NOW - timedelta(days=1)).date().isoformat()


# ── B4. Per-day continuity — force_refresh does NOT count as cron ────

def test_continuity_force_refresh_does_not_substitute_for_cron(db):
  """A force_refresh event on a day with no nightly_cron event still
  surfaces continuity_missing_day for that day. Cron is the ground
  truth for the daily refresh contract."""
  p = _hybrid_portfolio(db, name="P-force", activated_days_ago=5)
  _nightly_event(db, p.id, day_offset=-2)  # cron on day -2
  _nightly_event(db, p.id, day_offset=-1, trigger="manual_l2")  # force_refresh on day -1
  _nightly_event(db, p.id, day_offset=0)  # cron today

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  missing = [d for d in report["defects"] if d["category"] == "continuity_missing_day"]
  missing_days = {d["evidence"]["day_iso"] for d in missing}
  # Day -1 has only manual_l2 → still flagged as missing nightly_cron.
  assert (NOW - timedelta(days=1)).date().isoformat() in missing_days


# ── B5. Per-day continuity — non-hybrid portfolio not checked ────────

def test_continuity_skips_non_hybrid_portfolio(db):
  """Non-hybrid portfolios don't produce phase-2 events by design.
  Continuity check must skip them outright."""
  p = Portfolio(
    name="P-non-hybrid", portfolio_type="paper", mode="non_hybrid",
    is_active=True, is_deleted=False,
    activated_at=NOW - timedelta(days=5),
    source_strategies="{}",
  )
  db.add(p)
  db.commit()

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  cats = [d["category"] for d in report["defects"]]
  assert "continuity_missing_day" not in cats


# ── B6. Per-day continuity — inactive portfolio not checked ──────────

def test_continuity_skips_deactivated_days(db):
  """A portfolio that deactivated mid-lookback should not be flagged
  for missing events on days AFTER its deactivation."""
  p = _hybrid_portfolio(db, name="P-deactivated", activated_days_ago=10)
  p.deactivated_at = NOW - timedelta(days=2)
  db.commit()
  _nightly_event(db, p.id, day_offset=-5)
  _nightly_event(db, p.id, day_offset=-4)
  _nightly_event(db, p.id, day_offset=-3)
  # No events on days -1, 0 — but portfolio was deactivated on day -2.

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  missing = [d for d in report["defects"] if d["category"] == "continuity_missing_day"]
  missing_days = {d["evidence"]["day_iso"] for d in missing}
  # Days -1, 0 must NOT appear (portfolio was deactivated).
  assert (NOW - timedelta(days=1)).date().isoformat() not in missing_days
  assert NOW.date().isoformat() not in missing_days


# ── B7. Cross-check — basket missing traded symbol flagged ───────────

def test_cross_check_flags_basket_missing_traded_symbol(db):
  """selected_records contains BTCUSDT trades inside the OOS window,
  but the recorded chosen_basket only has ETHUSDT. Mismatch → defect."""
  oos_start = (NOW - timedelta(days=7)).isoformat()
  oos_end = NOW.isoformat()
  records = [
    {"dt": (NOW - timedelta(days=1)).isoformat(), "symbol": "BTCUSDT",
     "side": "Long", "batch_id": 1, "pnl": 5.0},
    {"dt": (NOW - timedelta(days=2)).isoformat(), "symbol": "ETHUSDT",
     "side": "Long", "batch_id": 1, "pnl": 3.0},
  ]
  p = _hybrid_portfolio(
    db, name="P-mismatch", activated_days_ago=5,
    selected_records=records,
  )
  _nightly_event(
    db, p.id, day_offset=0,
    chosen_basket=["B1_ETHUSDT_Long"],  # missing BTCUSDT
    oos_start_iso=oos_start, oos_end_iso=oos_end,
  )

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  mism = [d for d in report["defects"] if d["category"] == "basket_vs_records_mismatch"]
  assert len(mism) == 1
  assert "B1_BTCUSDT_Long" in mism[0]["evidence"]["missing_in_basket"]


# ── B8. Cross-check — exact match passes ─────────────────────────────

def test_cross_check_passes_when_basket_covers_all_records(db):
  """When chosen_basket contains every traded symbol in the OOS window,
  no basket_vs_records_mismatch defect surfaces."""
  oos_start = (NOW - timedelta(days=7)).isoformat()
  oos_end = NOW.isoformat()
  records = [
    {"dt": (NOW - timedelta(days=1)).isoformat(), "symbol": "BTCUSDT",
     "side": "Long", "batch_id": 1, "pnl": 5.0},
    {"dt": (NOW - timedelta(days=2)).isoformat(), "symbol": "ETHUSDT",
     "side": "Long", "batch_id": 1, "pnl": 3.0},
  ]
  p = _hybrid_portfolio(
    db, name="P-match", activated_days_ago=5,
    selected_records=records,
  )
  _nightly_event(
    db, p.id, day_offset=0,
    chosen_basket=["B1_BTCUSDT_Long", "B1_ETHUSDT_Long"],
    oos_start_iso=oos_start, oos_end_iso=oos_end,
  )

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  cats = [d["category"] for d in report["defects"]]
  assert "basket_vs_records_mismatch" not in cats


# ── B9. Cross-check — out-of-window records ignored ──────────────────

def test_cross_check_ignores_records_outside_oos_window(db):
  """selected_records dt outside the OOS window are not part of the
  cross-check slice — they belong to a previous decision period."""
  oos_start = (NOW - timedelta(days=2)).isoformat()
  oos_end = NOW.isoformat()
  records = [
    # Outside window (older than oos_start) — must be ignored.
    {"dt": (NOW - timedelta(days=10)).isoformat(), "symbol": "OLDUSDT",
     "side": "Long", "batch_id": 1, "pnl": 1.0},
    # Inside window.
    {"dt": (NOW - timedelta(days=1)).isoformat(), "symbol": "BTCUSDT",
     "side": "Long", "batch_id": 1, "pnl": 5.0},
  ]
  p = _hybrid_portfolio(
    db, name="P-window", activated_days_ago=5,
    selected_records=records,
  )
  _nightly_event(
    db, p.id, day_offset=0,
    chosen_basket=["B1_BTCUSDT_Long"],  # OLDUSDT not included — but it's out-of-window
    oos_start_iso=oos_start, oos_end_iso=oos_end,
  )

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  cats = [d["category"] for d in report["defects"]]
  assert "basket_vs_records_mismatch" not in cats


# ── B10. Cross-check — cold-save events skipped ──────────────────────

def test_cross_check_skips_cold_save_events(db):
  """Cold-save events are reconstructed (lossy) snapshots; their
  chosen_basket is intentionally derived from selected_records, so
  the cross-check has no diagnostic value and would be circular."""
  oos_start = (NOW - timedelta(days=7)).isoformat()
  oos_end = NOW.isoformat()
  records = [
    {"dt": (NOW - timedelta(days=1)).isoformat(), "symbol": "BTCUSDT",
     "side": "Long", "batch_id": 1, "pnl": 5.0},
  ]
  p = _hybrid_portfolio(
    db, name="P-cold-only", activated_days_ago=5,
    selected_records=records,
  )
  # Cold-save event with empty basket — should NOT trigger cross-check defect.
  ts = NOW
  details = {
    "decided_at_iso": ts.isoformat(),
    "trigger": "cold_save",
    "chosen_basket": [],
    "phase1_window_oos_start_iso": oos_start,
    "phase1_window_oos_end_iso": oos_end,
    "reconstructed": True,
    "dispatch_outcome": "success",
  }
  db.add(LiveEvent(
    portfolio_id=p.id, event_type=EVENT_TYPE_NO_CHANGE, symbol="",
    details=json.dumps(details), created_at=ts,
  ))
  db.commit()

  report = run_phase2_audit(db, now_utc=NOW, lookback_days=7)
  cats = [d["category"] for d in report["defects"]]
  assert "basket_vs_records_mismatch" not in cats

