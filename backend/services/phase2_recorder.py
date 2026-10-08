"""T-002 — Faithful live phase-2 decision recorder.

Records one `LiveEvent` row per nightly phase-2 evaluation (basket-changed
via `event_type='hybrid_refresh'`, basket-unchanged via the new
`event_type='phase2_evaluated_no_change'`). The Pydantic schema below
locks the `details` shape at write time; legacy `LiveEvent.hybrid_refresh`
rows from before T-002 still deserialise via `Optional` fields and
`extra="allow"`.

Failure isolation contract:
  * `record_phase2_event(...)` calls `db.add(...)` and `db.flush()`.
  * It NEVER calls `db.commit()`.
  * The caller wraps it in `with db.begin_nested():` so a recorder
    failure rolls back only the recorder writes — trading state still
    commits on the outer transaction.
  * `update_dispatch_outcome(...)` is a separate short-lived txn; runs
    AFTER the outer commit. Its failure cannot undo trading state.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.db.models import LiveEvent

log = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────

EVENT_TYPE_HYBRID_REFRESH = "hybrid_refresh"
EVENT_TYPE_HYBRID_ROTATION = "hybrid_rotation"
EVENT_TYPE_NO_CHANGE = "phase2_evaluated_no_change"

DISPATCH_PENDING = "pending"
DISPATCH_SUCCESS = "success"
DISPATCH_FAILED = "failed"
DISPATCH_ENGINE_OFFLINE = "engine_offline"

VALID_DISPATCH_OUTCOMES = frozenset({
  DISPATCH_PENDING, DISPATCH_SUCCESS, DISPATCH_FAILED, DISPATCH_ENGINE_OFFLINE,
})

VALID_TRIGGERS = frozenset({
  "nightly_cron", "on_save", "manual_l2", "rotation_day", "cold_save",
})

RECORDER_VERSION = 1


# ── Pydantic schemas ───────────────────────────────────────────────

class SessionFilterSnapshot(BaseModel):
  """Frozen-at-decision-time copy of the session_filter_* config.

  Live phase-2 reads these from `Portfolio.source_strategies` JSON.
  Snapshot freezes them onto the event row so historical events do not
  drift if the user later edits the portfolio's filter settings.
  """
  enabled: bool = False
  mode: Optional[str] = None
  exclude_hours: list[int] = Field(default_factory=list)
  exclude_days: list[str] = Field(default_factory=list)


class Phase2EventDetails(BaseModel):
  """Common shape for every phase-2 recorder event_type.

  Backwards-compatible: every field is Optional or has a default.
  Pre-T-002 `LiveEvent.hybrid_refresh` rows lack the new fields and
  deserialise fine via `extra='allow'`.

  IMPORTANT: never reorder/rename a field without bumping
  RECORDER_VERSION and updating the cold-save migration script. The
  hash inputs are read from these fields and must stay stable.
  """
  model_config = ConfigDict(extra="allow")

  # Identity
  decided_at_iso: Optional[str] = None
  trigger: Optional[str] = None
  recorder_version: int = RECORDER_VERSION

  # Phase-1 window
  phase1_window_num: Optional[int] = None
  phase1_window_num_synth_for_stub: bool = False
  phase1_window_oos_start_iso: Optional[str] = None
  phase1_window_oos_end_iso: Optional[str] = None

  # Basket diff
  prior_basket: list[str] = Field(default_factory=list)
  chosen_basket: list[str] = Field(default_factory=list)
  added: list[str] = Field(default_factory=list)
  removed: list[str] = Field(default_factory=list)
  basket_unchanged: bool = False

  # Lock counter — `lock_size` is the configured X (`hybrid_min_hold`).
  # `lock_progress` is per-asset residual at decision time, sourced from
  # `step2_result.per_window[-1].final_state_snapshot.asset_locks`.
  lock_size: Optional[int] = None
  lock_progress: dict[str, int] = Field(default_factory=dict)

  # Frozen policy snapshot
  session_filter_snapshot: SessionFilterSnapshot = Field(
    default_factory=SessionFilterSnapshot,
  )

  # Auditability
  decision_inputs_hash: Optional[str] = None
  latest_asset_scores: dict[str, float] = Field(default_factory=dict)
  kelly_allocation_id: Optional[int] = None

  # Provenance
  reconstructed: bool = False
  reconstructed_at_iso: Optional[str] = None

  # Dispatch verification (Bug #7) — updated AFTER the outer commit by
  # `update_dispatch_outcome(...)`.
  dispatch_outcome: str = DISPATCH_PENDING

  recorder_error: Optional[str] = None


# ── Hash spec (ATLAS ask #2) ───────────────────────────────────────

def fingerprint_run_batch_map(rbm: dict[int, int] | None) -> str:
  """Stable 64-bit fingerprint of the run→batch mapping used in the
  decision. Two refreshes with the same trade-input set produce the same
  fingerprint, regardless of dict ordering.
  """
  if not rbm:
    return "0" * 16
  pairs = sorted([(int(rid), int(bid)) for rid, bid in rbm.items()])
  blob = json.dumps(pairs, separators=(",", ":")).encode("utf-8")
  return hashlib.sha256(blob).hexdigest()[:16]


def compute_decision_inputs_hash(
  *,
  window_oos_start: str,
  window_oos_end: str,
  batch_ids: list[int],
  session_filter_snapshot: SessionFilterSnapshot | dict,
  hybrid_min_hold: int,
  hybrid_n_assets: int,
  hybrid_n_trades: int,
  hybrid_objective: Optional[str],
  hybrid_scoring_method: Optional[str],
  hybrid_composite_weights: Optional[dict],
  run_batch_map_fingerprint: str,
) -> str:
  """sha256 hex over canonical-JSON of the frozen decision inputs.

  Locked spec (ATLAS ask #2). Never reorder/rename keys here without
  bumping `RECORDER_VERSION` and updating
  `tests/test_phase2_recorder.py::test_hash_stability_known_fixture`.
  """
  if isinstance(session_filter_snapshot, SessionFilterSnapshot):
    sf = session_filter_snapshot.model_dump()
  else:
    sf = dict(session_filter_snapshot or {})
  payload = {
    "window_oos_start": str(window_oos_start),
    "window_oos_end": str(window_oos_end),
    "batch_ids": sorted(int(b) for b in (batch_ids or [])),
    "session_filter_snapshot": sf,
    "hybrid_min_hold": int(hybrid_min_hold or 0),
    "hybrid_n_assets": int(hybrid_n_assets or 0),
    "hybrid_n_trades": int(hybrid_n_trades or 0),
    "hybrid_objective": hybrid_objective,
    "hybrid_scoring_method": hybrid_scoring_method,
    "hybrid_composite_weights": hybrid_composite_weights,
    "run_batch_map_fingerprint": run_batch_map_fingerprint,
  }
  canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Helpers ────────────────────────────────────────────────────────

def _extract_lock_progress(step2_result: dict | None) -> dict[str, int]:
  """Pull per-asset residual lock counts from the live phase-2 output.

  `run_event_wfo` writes them to `final_state_snapshot.asset_locks`
  (event_wfo.py:1523). Live refresh runs exactly one window, so the
  last per_window entry is the live trailing window.
  """
  if not isinstance(step2_result, dict):
    return {}
  pw = step2_result.get("per_window") or []
  if not pw:
    return {}
  snap = (pw[-1] or {}).get("final_state_snapshot") or {}
  locks = snap.get("asset_locks") or {}
  out: dict[str, int] = {}
  for k, v in locks.items():
    try:
      out[str(k)] = int(v)
    except (ValueError, TypeError):
      continue
  return out


def _detect_synth_for_stub(target_window: dict, windows_meta: list[dict]) -> bool:
  """Bug #12 — detect Path B in `_build_live_trailing_window`.

  Path A keeps saved `oos_start`. Path B synthesises a fresh
  `oos_start = prior_window.oos_end + 1 day`. Path C also keeps saved.
  So Path B ⇔ target.oos_start does NOT match any saved window's
  oos_start.
  """
  target_start = str(target_window.get("oos_start") or "")[:10]
  if not target_start:
    return False
  for w in windows_meta or []:
    if not isinstance(w, dict):
      continue
    saved_start = str(w.get("oos_start") or "")[:10]
    if saved_start and saved_start == target_start:
      return False
  return True


def _build_session_filter_snapshot(src_cfg: dict) -> SessionFilterSnapshot:
  return SessionFilterSnapshot(
    enabled=bool(src_cfg.get("session_filter_enabled", False)),
    mode=src_cfg.get("session_filter_mode"),
    exclude_hours=[int(h) for h in (src_cfg.get("session_filter_exclude_hours") or []) if isinstance(h, (int, str)) and str(h).isdigit()],
    exclude_days=[str(d) for d in (src_cfg.get("session_filter_exclude_days") or [])],
  )


# ── Main entry point ───────────────────────────────────────────────

def record_phase2_event(
  db,
  *,
  portfolio_id: int,
  target_window: dict,
  windows_meta: list[dict],
  src_cfg: dict,
  step2_result: dict | None,
  batch_ids: list[int],
  rbm: dict[int, int] | None,
  prior_basket: list[str],
  chosen_basket: list[str],
  added: list[str],
  removed: list[str],
  basket_unchanged: bool,
  kelly_allocation_id: Optional[int] = None,
  trigger: str = "nightly_cron",
  reconstructed: bool = False,
  reconstructed_at_iso: Optional[str] = None,
  decided_at: Optional[datetime] = None,
  event_type_override: Optional[str] = None,
  extra: Optional[dict[str, Any]] = None,
) -> LiveEvent:
  """Write one phase-2 decision event row to `live_events`.

  Caller responsibilities:
    * Wrap call site in `with db.begin_nested():` so a recorder
      exception rolls back only the recorder writes (ATLAS ask #4).
    * Do NOT rely on this function to commit. It only `db.add(...)`
      and `db.flush()`. The outer transaction commits.
    * On exception, log and continue — trading state must still write.

  Returns the just-flushed (uncommitted) `LiveEvent` row. The caller may
  read `row.id` to schedule a `update_dispatch_outcome(...)` after the
  outer commit.
  """
  decided_at_actual = decided_at or datetime.now(timezone.utc)

  if event_type_override is not None:
    event_type = event_type_override
  else:
    event_type = EVENT_TYPE_NO_CHANGE if basket_unchanged else EVENT_TYPE_HYBRID_REFRESH

  if trigger not in VALID_TRIGGERS:
    log.warning(
      "phase2_recorder: unknown trigger %r — coercing to 'nightly_cron'", trigger,
    )
    trigger = "nightly_cron"

  sf_snap = _build_session_filter_snapshot(src_cfg)
  rbm_fp = fingerprint_run_batch_map(rbm)
  decision_hash = compute_decision_inputs_hash(
    window_oos_start=str(target_window.get("oos_start") or ""),
    window_oos_end=str(target_window.get("oos_end") or ""),
    batch_ids=list(batch_ids or []),
    session_filter_snapshot=sf_snap,
    hybrid_min_hold=int(src_cfg.get("hybrid_min_hold", 0) or 0),
    hybrid_n_assets=int(src_cfg.get("hybrid_n_assets", 0) or 0),
    hybrid_n_trades=int(src_cfg.get("hybrid_n_trades", 0) or 0),
    hybrid_objective=src_cfg.get("hybrid_objective"),
    hybrid_scoring_method=src_cfg.get("hybrid_scoring_method"),
    hybrid_composite_weights=src_cfg.get("hybrid_composite_weights"),
    run_batch_map_fingerprint=rbm_fp,
  )

  scores_raw = (step2_result or {}).get("latest_asset_scores") or {}
  scores: dict[str, float] = {}
  for k, v in scores_raw.items():
    try:
      scores[str(k)] = float(v)
    except (ValueError, TypeError):
      continue

  details_model = Phase2EventDetails(
    decided_at_iso=decided_at_actual.isoformat(),
    trigger=trigger,
    recorder_version=RECORDER_VERSION,
    phase1_window_num=int(target_window.get("window_num") or 0),
    phase1_window_num_synth_for_stub=_detect_synth_for_stub(target_window, windows_meta),
    phase1_window_oos_start_iso=str(target_window.get("oos_start") or ""),
    phase1_window_oos_end_iso=str(target_window.get("oos_end") or ""),
    prior_basket=sorted(str(x) for x in (prior_basket or [])),
    chosen_basket=sorted(str(x) for x in (chosen_basket or [])),
    added=sorted(str(x) for x in (added or [])),
    removed=sorted(str(x) for x in (removed or [])),
    basket_unchanged=bool(basket_unchanged),
    lock_size=int(src_cfg.get("hybrid_min_hold", 0) or 0),
    lock_progress=_extract_lock_progress(step2_result),
    session_filter_snapshot=sf_snap,
    decision_inputs_hash=decision_hash,
    latest_asset_scores=scores,
    kelly_allocation_id=kelly_allocation_id,
    reconstructed=bool(reconstructed),
    reconstructed_at_iso=reconstructed_at_iso,
    dispatch_outcome=DISPATCH_PENDING,
    recorder_error=None,
  )

  details_json = details_model.model_dump_json()
  if extra:
    try:
      d = json.loads(details_json)
    except (ValueError, TypeError):
      d = {}
    for k, v in extra.items():
      d[str(k)] = v
    details_json = json.dumps(d, default=str)

  row = LiveEvent(
    portfolio_id=portfolio_id,
    event_type=event_type,
    symbol="",
    details=details_json,
    created_at=decided_at_actual,
  )
  db.add(row)
  db.flush()  # populate row.id for the caller's downstream dispatch update
  return row


# ── Dispatch outcome update (post-commit, separate txn) ────────────

def update_dispatch_outcome(db, live_event_id: int, outcome: str) -> bool:
  """Update an already-committed event row's `dispatch_outcome` field.

  Runs as its own transaction. Caller wraps in try/except — failure
  here cannot undo the trading-state commit that already happened.

  Returns True on successful update; False if row not found or outcome
  invalid.
  """
  if outcome not in VALID_DISPATCH_OUTCOMES:
    log.warning("phase2_recorder: invalid dispatch_outcome %r — ignored", outcome)
    return False
  row = db.query(LiveEvent).filter(LiveEvent.id == live_event_id).first()
  if row is None:
    return False
  try:
    details: dict[str, Any] = json.loads(row.details or "{}")
  except (ValueError, TypeError):
    details = {}
  details["dispatch_outcome"] = outcome
  row.details = json.dumps(details)
  db.commit()
  return True


# ── Dispatch verification (Bug #7 mitigation) ──────────────────────

def _default_engine_provider() -> Any:
  """Resolve the live engine via the canonical import path.

  Pulled out so tests can inject a stub via the `engine_provider` kwarg
  on `verify_dispatch_outcome` without monkey-patching imports.
  """
  from backend.exchange.engine_coordinator import get_engine
  return get_engine()


def verify_dispatch_outcome(
  *,
  db,
  live_event_id: int,
  portfolio_id: int,
  timeout_s: float = 2.0,
  poll_interval_s: float = 0.1,
  engine_provider: Optional[Callable[[], Any]] = None,
) -> str:
  """Confirm the live engine picked up the post-refresh cache invalidation.

  Bug #7 — `_dispatch_executor_refresh(...)` is fire-and-forget. If the
  engine is offline or the cache reload silently failed, the recorder
  row would otherwise stay `dispatch_outcome="pending"` forever and the
  user would never know the trade router is running stale weights.

  Strategy:
    * Resolve the engine. If the import raises or returns None →
      `engine_offline`.
    * Poll `engine._kelly_alloc_cache[portfolio_id]` up to `timeout_s`,
      sleeping `poll_interval_s` between checks. A populated dict with
      a non-empty `weights` entry counts as `success`.
    * If the deadline elapses without a populated entry → `failed`.

  Always updates the LiveEvent row via `update_dispatch_outcome(...)`
  regardless of verdict, so the audit log reflects reality.

  Returns the verdict string ('success' | 'failed' | 'engine_offline').
  Never raises — engine probing failures are logged and treated as
  `failed` so the caller (refresh_portfolio) can move on.
  """
  provider = engine_provider or _default_engine_provider
  try:
    engine = provider()
  except Exception as e:
    log.warning(
      "verify_dispatch_outcome: engine resolve failed for pid=%d: %s",
      portfolio_id, e,
    )
    update_dispatch_outcome(db, live_event_id, DISPATCH_ENGINE_OFFLINE)
    return DISPATCH_ENGINE_OFFLINE

  if engine is None:
    update_dispatch_outcome(db, live_event_id, DISPATCH_ENGINE_OFFLINE)
    return DISPATCH_ENGINE_OFFLINE

  deadline = time.monotonic() + max(0.0, float(timeout_s))
  interval = max(0.01, float(poll_interval_s))
  while True:
    try:
      cache = getattr(engine, "_kelly_alloc_cache", None)
      if isinstance(cache, dict):
        entry = cache.get(portfolio_id)
        if isinstance(entry, dict) and entry.get("weights"):
          update_dispatch_outcome(db, live_event_id, DISPATCH_SUCCESS)
          return DISPATCH_SUCCESS
    except Exception as e:
      log.warning(
        "verify_dispatch_outcome: cache probe raised for pid=%d: %s",
        portfolio_id, e,
      )
      # treat probe failure as a transient miss; loop until deadline
    if time.monotonic() >= deadline:
      break
    time.sleep(interval)

  update_dispatch_outcome(db, live_event_id, DISPATCH_FAILED)
  return DISPATCH_FAILED


# ── As-of-time basket resolver (Step 4 — fixes the UI shift bug) ───

# All phase-2 events that change the basket. `phase2_evaluated_no_change`
# is included for completeness — it carries the basket-at-time-T fact
# even though `chosen_basket == prior_basket`.
#
# T-003 Delta 2: promoted from private `_BASKET_EVENT_TYPES` to public
# `BASKET_EVENT_TYPES`. Single source of truth for every basket-event
# reader (forward-period replay, legacy stitcher, audit cross-check, etc).
# Future event-type additions land HERE only.
BASKET_EVENT_TYPES = (
  EVENT_TYPE_HYBRID_REFRESH,
  EVENT_TYPE_HYBRID_ROTATION,
  EVENT_TYPE_NO_CHANGE,
)

# Back-compat alias — any pre-T-003 importer still works.
_BASKET_EVENT_TYPES = BASKET_EVENT_TYPES


def _utc_to_nyt_naive(dt: datetime) -> datetime:
  """Convert a UTC tz-aware datetime → NYT tz-naive.

  Trade `entry_time` is stored NYT-naive (CLAUDE.md convention).
  LiveEvent.created_at is written by the recorder as UTC tz-aware
  (`datetime.now(timezone.utc)`). The forward-period replay compares
  the two — they must be in the same convention before comparison.
  """
  try:
    from zoneinfo import ZoneInfo
    nyt = ZoneInfo("America/New_York")
  except Exception:  # pragma: no cover — zoneinfo missing on weird runtimes
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
  if dt.tzinfo is None:
    # Treat naive as UTC (recorder always writes UTC; defensive default)
    dt = dt.replace(tzinfo=timezone.utc)
  return dt.astimezone(nyt).replace(tzinfo=None)


def iter_basket_segments(db, portfolio_id: int) -> list[tuple[datetime, set[str]]]:
  """Walk all phase-2 events for `portfolio_id` and emit a chronological
  list of `(effective_from_nyt_naive, basket_set)` segments.

  Segment[i] is the basket active from `segment[i].effective_from`
  (inclusive) until `segment[i+1].effective_from` (exclusive). The first
  segment uses the chronologically-first event's `prior_basket` and
  starts at `datetime.min` so trades before any event still resolve.

  `effective_from` is in NYT-naive convention to match
  `BacktestTrade.entry_time`. Caller passes the trade's `entry_time`
  directly to `resolve_basket_at_t(segments, entry_time)`.

  Returns [] when the portfolio has no recorder events yet (caller
  should fall back to the legacy `is_active` read).
  """
  rows = (
    db.query(LiveEvent)
    .filter(
      LiveEvent.portfolio_id == portfolio_id,
      LiveEvent.event_type.in_(BASKET_EVENT_TYPES),
    )
    .order_by(LiveEvent.created_at.asc())
    .all()
  )
  segments: list[tuple[datetime, set[str]]] = []
  for idx, r in enumerate(rows):
    try:
      d = json.loads(r.details or "{}")
    except (ValueError, TypeError):
      continue
    chosen = set(str(x) for x in (d.get("chosen_basket") or []))
    if idx == 0:
      prior = set(str(x) for x in (d.get("prior_basket") or []))
      # Sentinel start so trades dated before any event still resolve.
      segments.append((datetime.min, prior))
    ts = r.created_at
    if ts is None:
      continue
    segments.append((_utc_to_nyt_naive(ts), chosen))
  return segments


def resolve_basket_at_t(
  segments: list[tuple[datetime, set[str]]],
  t: datetime,
) -> set[str]:
  """Return the basket active at NYT-naive time `t`.

  `segments` must be the output of `iter_basket_segments(...)` — already
  sorted and in NYT-naive convention. `t` is compared as NYT-naive; if
  caller passes a tz-aware datetime, tz info is dropped without
  conversion (caller's responsibility to align).

  Returns an empty set when `segments` is empty.
  """
  if not segments:
    return set()
  if t.tzinfo is not None:
    t = t.replace(tzinfo=None)
  active = segments[0][1]
  for ts, basket in segments:
    if ts <= t:
      active = basket
    else:
      break
  return active
