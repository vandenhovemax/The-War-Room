"""T-002 — phase2_recorder unit tests.

Guards (per PLAN section 13 step 1 + ATLAS asks #1/#2/#4):

  1. Pydantic schema accepts basket-changed, basket-unchanged, and
     reconstructed paths. Optional fields default sensibly.
  2. compute_decision_inputs_hash is stable for a known input fixture
     (ATLAS ask #2) — the audit self-check must be falsifiable.
  3. record_phase2_event populates `lock_progress` from
     `step2_result.per_window[-1].final_state_snapshot.asset_locks`
     (ATLAS ask #1).
  4. record_phase2_event flags `phase1_window_num_synth_for_stub=true`
     when the target window's `oos_start` is not present in
     `windows_meta` (Bug #12).
  5. SAVEPOINT failure isolation (ATLAS ask #4): a recorder failure
     mid-write rolls back the recorder row but the surrounding
     trading-state writes still commit on the outer transaction.
  6. update_dispatch_outcome updates an already-committed row.

NOT covered here (other test files in this PR):
  * Hook integration into refresh_portfolio — Step 2 / test_phase2_recorder_integration.py
  * Audit self-check — Step 7 / test_phase2_recorder_audit.py
  * Cold-save idempotency — Step 6 / test_phase2_cold_save.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, LiveEvent, Portfolio
from backend.services.phase2_recorder import (
  DISPATCH_ENGINE_OFFLINE,
  DISPATCH_FAILED,
  DISPATCH_PENDING,
  DISPATCH_SUCCESS,
  EVENT_TYPE_HYBRID_REFRESH,
  EVENT_TYPE_NO_CHANGE,
  Phase2EventDetails,
  RECORDER_VERSION,
  SessionFilterSnapshot,
  compute_decision_inputs_hash,
  fingerprint_run_batch_map,
  record_phase2_event,
  update_dispatch_outcome,
  verify_dispatch_outcome,
)


# ── In-memory fixture (matches test_parity_canary.py) ──────────────

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
    s.query(LiveEvent).delete()
    s.query(Portfolio).delete()
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


# ── Test helpers ───────────────────────────────────────────────────

def _seed_portfolio(db, portfolio_id: int = 100) -> Portfolio:
  p = Portfolio(
    id=portfolio_id,
    name="t002_test_pf",
    mode="hybrid",
    is_active=True,
    is_deleted=False,
    source_strategies="{}",
  )
  db.add(p)
  db.commit()
  return p


def _saved_windows() -> list[dict]:
  return [
    {"window_num": 1, "oos_start": "2025-01-01", "oos_end": "2025-06-30",
     "is_start": "2024-07-01", "is_end": "2024-12-31"},
    {"window_num": 2, "oos_start": "2025-07-01", "oos_end": "2025-12-31",
     "is_start": "2025-01-01", "is_end": "2025-06-30"},
  ]


def _step2_result_with_locks(locks: dict[str, int]) -> dict:
  return {
    "latest_asset_scores": {"B1_BTCUSDT_long": 1.5, "B1_ETHUSDT_long": 1.2},
    "latest_portfolio_at_end": ["B1_BTCUSDT_long", "B1_ETHUSDT_long"],
    "selected_records": [],
    "per_window": [
      {
        "window_num": 2,
        "final_state_snapshot": {
          "asset_locks": locks,
        },
      },
    ],
  }


def _src_cfg() -> dict:
  return {
    "hybrid_min_hold": 5,
    "hybrid_n_assets": 2,
    "hybrid_n_trades": 50,
    "hybrid_objective": "sortino",
    "hybrid_scoring_method": "composite",
    "hybrid_composite_weights": {"sharpe": 0.5, "pf": 0.5},
    "session_filter_enabled": True,
    "session_filter_mode": "exclude",
    "session_filter_exclude_hours": [0, 1, 2, 3, 4, 5, 6, 7],
    "session_filter_exclude_days": ["Mon"],
  }


# ── 1. Pydantic schema ─────────────────────────────────────────────

def test_schema_accepts_basket_changed_payload():
  m = Phase2EventDetails(
    decided_at_iso="2026-04-25T03:00:00+00:00",
    trigger="nightly_cron",
    phase1_window_num=2,
    phase1_window_oos_start_iso="2025-07-01",
    phase1_window_oos_end_iso="2026-04-25",
    prior_basket=["A", "B"],
    chosen_basket=["B", "C"],
    added=["C"],
    removed=["A"],
    basket_unchanged=False,
    lock_size=5,
    lock_progress={"B": 3, "C": 5},
  )
  assert m.basket_unchanged is False
  assert m.added == ["C"]
  assert m.recorder_version == RECORDER_VERSION


def test_schema_accepts_basket_unchanged_payload():
  m = Phase2EventDetails(
    basket_unchanged=True,
    chosen_basket=["A", "B"],
    prior_basket=["A", "B"],
    added=[],
    removed=[],
  )
  assert m.basket_unchanged is True
  assert m.added == []
  assert m.removed == []


def test_schema_accepts_reconstructed_payload():
  m = Phase2EventDetails(
    reconstructed=True,
    reconstructed_at_iso="2026-04-25T18:00:00+00:00",
    chosen_basket=["A"],
    decision_inputs_hash=None,  # null on cold-save where unknown
    latest_asset_scores={},
  )
  assert m.reconstructed is True
  assert m.decision_inputs_hash is None


def test_schema_legacy_row_via_extra_allow():
  """Pre-T-002 LiveEvent.hybrid_refresh rows have legacy keys we
  preserve via extra='allow'. They must round-trip without loss."""
  legacy = {
    "added": ["X"],
    "removed": [],
    "basket_size": 7,
    "weights_count": 7,
    "walked_window": {"window_num": 3, "oos_start": "2025-01-01", "oos_end": "2025-06-30"},
    "used_seed": True,
    "computed_at": "2025-07-01T00:00:00+00:00",
  }
  m = Phase2EventDetails.model_validate(legacy)
  dumped = m.model_dump(exclude_unset=False)
  # extra fields preserved
  assert dumped.get("basket_size") == 7
  assert dumped.get("walked_window")["window_num"] == 3


# ── 2. Hash stability (ATLAS ask #2) ──────────────────────────────

def test_hash_stability_known_fixture():
  """Lock-in test: known inputs → known hash. If this changes, the
  cold-save script and audit self-check will silently disagree about
  decision identity. Bump RECORDER_VERSION + this fixture together."""
  sf = SessionFilterSnapshot(
    enabled=True, mode="exclude",
    exclude_hours=[0, 1, 2], exclude_days=["Mon"],
  )
  h = compute_decision_inputs_hash(
    window_oos_start="2025-07-01",
    window_oos_end="2026-04-25",
    batch_ids=[42, 7, 19],
    session_filter_snapshot=sf,
    hybrid_min_hold=5,
    hybrid_n_assets=2,
    hybrid_n_trades=50,
    hybrid_objective="sortino",
    hybrid_scoring_method="composite",
    hybrid_composite_weights={"sharpe": 0.5, "pf": 0.5},
    run_batch_map_fingerprint="abcd1234abcd1234",
  )
  assert isinstance(h, str)
  assert len(h) == 64  # sha256 hex
  # Lock the actual digest. If this changes, version-bump.
  assert h == "36316a21faff74b98250bc7e4ccb94bde67114368732cd6183b133c2233e664d"


def test_hash_changes_when_any_input_changes():
  base_kwargs = dict(
    window_oos_start="2025-07-01",
    window_oos_end="2026-04-25",
    batch_ids=[1, 2, 3],
    session_filter_snapshot={},
    hybrid_min_hold=5,
    hybrid_n_assets=2,
    hybrid_n_trades=50,
    hybrid_objective="sortino",
    hybrid_scoring_method="composite",
    hybrid_composite_weights=None,
    run_batch_map_fingerprint="fp1",
  )
  h0 = compute_decision_inputs_hash(**base_kwargs)
  h1 = compute_decision_inputs_hash(**{**base_kwargs, "hybrid_min_hold": 6})
  h2 = compute_decision_inputs_hash(**{**base_kwargs, "batch_ids": [1, 2, 3, 4]})
  h3 = compute_decision_inputs_hash(**{**base_kwargs, "run_batch_map_fingerprint": "fp2"})
  assert len({h0, h1, h2, h3}) == 4


def test_hash_is_order_independent_in_batch_ids():
  base_kwargs = dict(
    window_oos_start="2025-07-01",
    window_oos_end="2026-04-25",
    session_filter_snapshot={},
    hybrid_min_hold=5,
    hybrid_n_assets=2,
    hybrid_n_trades=50,
    hybrid_objective="sortino",
    hybrid_scoring_method="composite",
    hybrid_composite_weights=None,
    run_batch_map_fingerprint="fp1",
  )
  h_a = compute_decision_inputs_hash(batch_ids=[3, 1, 2], **base_kwargs)
  h_b = compute_decision_inputs_hash(batch_ids=[1, 2, 3], **base_kwargs)
  assert h_a == h_b


def test_run_batch_map_fingerprint_stable_under_dict_reorder():
  fp_a = fingerprint_run_batch_map({101: 1, 200: 2, 50: 3})
  fp_b = fingerprint_run_batch_map({50: 3, 101: 1, 200: 2})
  assert fp_a == fp_b
  fp_empty = fingerprint_run_batch_map(None)
  assert fp_empty == "0" * 16


# ── 3. record_phase2_event happy path + lock_progress (ATLAS ask #1)

def test_record_event_basket_changed_writes_full_row(db):
  _seed_portfolio(db)
  with db.begin_nested():
    row = record_phase2_event(
      db,
      portfolio_id=100,
      target_window={"window_num": 2, "oos_start": "2025-07-01", "oos_end": "2026-04-25"},
      windows_meta=_saved_windows(),
      src_cfg=_src_cfg(),
      step2_result=_step2_result_with_locks({"B1_BTCUSDT_long": 4, "B1_ETHUSDT_long": 5}),
      batch_ids=[1, 2, 3],
      rbm={101: 1, 200: 2},
      prior_basket=["B1_BTCUSDT_long"],
      chosen_basket=["B1_BTCUSDT_long", "B1_ETHUSDT_long"],
      added=["B1_ETHUSDT_long"],
      removed=[],
      basket_unchanged=False,
      kelly_allocation_id=42,
      trigger="nightly_cron",
    )
    assert row.id is not None
  db.commit()

  fetched = db.query(LiveEvent).filter(LiveEvent.portfolio_id == 100).one()
  assert fetched.event_type == EVENT_TYPE_HYBRID_REFRESH
  details = json.loads(fetched.details)
  assert details["basket_unchanged"] is False
  assert details["chosen_basket"] == ["B1_BTCUSDT_long", "B1_ETHUSDT_long"]
  assert details["lock_size"] == 5
  # ATLAS ask #1 — lock_progress populated from per_window[-1].final_state_snapshot
  assert details["lock_progress"] == {"B1_BTCUSDT_long": 4, "B1_ETHUSDT_long": 5}
  assert details["dispatch_outcome"] == DISPATCH_PENDING
  assert details["recorder_version"] == RECORDER_VERSION
  assert details["session_filter_snapshot"]["enabled"] is True
  assert details["session_filter_snapshot"]["exclude_days"] == ["Mon"]
  assert details["kelly_allocation_id"] == 42
  assert details["reconstructed"] is False
  # Hash stable for the (window, cfg, batches, fp) inputs
  assert isinstance(details["decision_inputs_hash"], str)
  assert len(details["decision_inputs_hash"]) == 64


def test_record_event_basket_unchanged_uses_no_change_event_type(db):
  _seed_portfolio(db)
  with db.begin_nested():
    record_phase2_event(
      db,
      portfolio_id=100,
      target_window={"window_num": 2, "oos_start": "2025-07-01", "oos_end": "2026-04-25"},
      windows_meta=_saved_windows(),
      src_cfg=_src_cfg(),
      step2_result=_step2_result_with_locks({}),
      batch_ids=[1],
      rbm={101: 1},
      prior_basket=["X"],
      chosen_basket=["X"],
      added=[],
      removed=[],
      basket_unchanged=True,
    )
  db.commit()
  rows = db.query(LiveEvent).filter(LiveEvent.portfolio_id == 100).all()
  assert len(rows) == 1
  assert rows[0].event_type == EVENT_TYPE_NO_CHANGE


# ── 4. Synth-for-stub detection (Bug #12) ──────────────────────────

def test_synth_for_stub_flag_set_when_target_oos_start_not_in_meta(db):
  _seed_portfolio(db)
  # target.oos_start is 2026-01-01 — NOT in saved windows {2025-01-01, 2025-07-01}
  target = {"window_num": 2, "oos_start": "2026-01-01", "oos_end": "2026-04-25"}
  with db.begin_nested():
    record_phase2_event(
      db,
      portfolio_id=100,
      target_window=target,
      windows_meta=_saved_windows(),
      src_cfg=_src_cfg(),
      step2_result=_step2_result_with_locks({}),
      batch_ids=[1],
      rbm={},
      prior_basket=[],
      chosen_basket=["A"],
      added=["A"],
      removed=[],
      basket_unchanged=False,
    )
  db.commit()
  details = json.loads(db.query(LiveEvent).first().details)
  assert details["phase1_window_num_synth_for_stub"] is True


def test_synth_for_stub_flag_clear_when_target_matches_saved(db):
  _seed_portfolio(db)
  # target.oos_start matches saved W2 — Path A or C
  target = {"window_num": 2, "oos_start": "2025-07-01", "oos_end": "2026-04-25"}
  with db.begin_nested():
    record_phase2_event(
      db,
      portfolio_id=100,
      target_window=target,
      windows_meta=_saved_windows(),
      src_cfg=_src_cfg(),
      step2_result=_step2_result_with_locks({}),
      batch_ids=[1],
      rbm={},
      prior_basket=[],
      chosen_basket=["A"],
      added=["A"],
      removed=[],
      basket_unchanged=False,
    )
  db.commit()
  details = json.loads(db.query(LiveEvent).first().details)
  assert details["phase1_window_num_synth_for_stub"] is False


# ── 5. SAVEPOINT failure isolation (ATLAS ask #4) ──────────────────

def test_savepoint_isolates_recorder_failure_from_outer_commit(db, monkeypatch):
  """The non-negotiable invariant: a recorder failure mid-write must
  NOT leak the recorder row into the outer commit.

  Pattern: outer txn writes a Portfolio.last_refreshed_at update
  (proxy for trading state). Recorder runs inside a SAVEPOINT and
  raises mid-write. Outer commits. Trading state must be present, no
  LiveEvent row must exist."""
  _seed_portfolio(db, portfolio_id=200)
  p = db.query(Portfolio).filter(Portfolio.id == 200).first()
  ts = datetime(2026, 4, 25, 3, 0, 0, tzinfo=timezone.utc)
  p.last_refreshed_at = ts  # outer trading-state write

  # Recorder raises after db.add by patching model_dump_json
  def _raise(*a, **kw):
    raise RuntimeError("simulated mid-write failure")

  monkeypatch.setattr(
    "backend.services.phase2_recorder.Phase2EventDetails.model_dump_json",
    _raise,
  )

  recorder_event = None
  try:
    with db.begin_nested():
      recorder_event = record_phase2_event(
        db,
        portfolio_id=200,
        target_window={"window_num": 1, "oos_start": "2025-01-01", "oos_end": "2025-06-30"},
        windows_meta=_saved_windows(),
        src_cfg=_src_cfg(),
        step2_result=_step2_result_with_locks({}),
        batch_ids=[1],
        rbm={},
        prior_basket=[],
        chosen_basket=["A"],
        added=["A"],
        removed=[],
        basket_unchanged=False,
      )
  except RuntimeError:
    recorder_event = None  # caller's exception path

  db.commit()  # outer commit — trading state must persist

  assert recorder_event is None
  refreshed = db.query(Portfolio).filter(Portfolio.id == 200).first()
  assert refreshed.last_refreshed_at.replace(tzinfo=None) == ts.replace(tzinfo=None)  # trading state survived
  assert db.query(LiveEvent).count() == 0  # recorder row rolled back


def test_savepoint_clean_path_does_not_block_outer_commit(db):
  """Sanity: the SAVEPOINT pattern doesn't break the happy path."""
  _seed_portfolio(db, portfolio_id=300)
  p = db.query(Portfolio).filter(Portfolio.id == 300).first()
  ts = datetime(2026, 4, 25, 3, 0, 0, tzinfo=timezone.utc)
  p.last_refreshed_at = ts

  with db.begin_nested():
    record_phase2_event(
      db,
      portfolio_id=300,
      target_window={"window_num": 1, "oos_start": "2025-01-01", "oos_end": "2025-06-30"},
      windows_meta=_saved_windows(),
      src_cfg=_src_cfg(),
      step2_result=_step2_result_with_locks({}),
      batch_ids=[1],
      rbm={},
      prior_basket=[],
      chosen_basket=["A"],
      added=["A"],
      removed=[],
      basket_unchanged=False,
    )
  db.commit()

  refreshed = db.query(Portfolio).filter(Portfolio.id == 300).first()
  assert refreshed.last_refreshed_at.replace(tzinfo=None) == ts.replace(tzinfo=None)
  assert db.query(LiveEvent).count() == 1


# ── 6. update_dispatch_outcome ────────────────────────────────────

def test_update_dispatch_outcome_updates_just_committed_row(db):
  _seed_portfolio(db, portfolio_id=400)
  with db.begin_nested():
    row = record_phase2_event(
      db,
      portfolio_id=400,
      target_window={"window_num": 1, "oos_start": "2025-01-01", "oos_end": "2025-06-30"},
      windows_meta=_saved_windows(),
      src_cfg=_src_cfg(),
      step2_result=_step2_result_with_locks({}),
      batch_ids=[1],
      rbm={},
      prior_basket=[],
      chosen_basket=["A"],
      added=["A"],
      removed=[],
      basket_unchanged=False,
    )
    event_id = row.id
  db.commit()

  ok = update_dispatch_outcome(db, event_id, DISPATCH_SUCCESS)
  assert ok is True
  fetched = db.query(LiveEvent).filter(LiveEvent.id == event_id).first()
  assert json.loads(fetched.details)["dispatch_outcome"] == DISPATCH_SUCCESS


def test_update_dispatch_outcome_rejects_invalid_outcome(db):
  _seed_portfolio(db, portfolio_id=500)
  with db.begin_nested():
    row = record_phase2_event(
      db,
      portfolio_id=500,
      target_window={"window_num": 1, "oos_start": "2025-01-01", "oos_end": "2025-06-30"},
      windows_meta=_saved_windows(),
      src_cfg=_src_cfg(),
      step2_result=_step2_result_with_locks({}),
      batch_ids=[1],
      rbm={},
      prior_basket=[],
      chosen_basket=["A"],
      added=["A"],
      removed=[],
      basket_unchanged=False,
    )
    event_id = row.id
  db.commit()
  ok = update_dispatch_outcome(db, event_id, "bogus_value")
  assert ok is False
  fetched = db.query(LiveEvent).filter(LiveEvent.id == event_id).first()
  assert json.loads(fetched.details)["dispatch_outcome"] == DISPATCH_PENDING


def test_update_dispatch_outcome_returns_false_for_missing_id(db):
  ok = update_dispatch_outcome(db, 99999, DISPATCH_FAILED)
  assert ok is False


# ── 7. extra dict + event_type_override (rotation forward-compat) ──

def test_extra_dict_merges_into_details_after_pydantic_dump(db):
  """The `extra` kwarg is the escape hatch the rotation handler uses to
  attach rotation_hash / healed_window / new_live_window without
  bumping the locked Pydantic schema. Extras must round-trip through
  the JSON column intact, alongside the locked fields."""
  _seed_portfolio(db, portfolio_id=600)
  extras = {
    "rotation_hash": "abc123",
    "healed_window": {"window_num": 7, "oos_start": "2026-01-01"},
    "new_live_window": {"window_num": 8, "oos_start": "2026-04-25"},
    "weights_count": 4,
  }
  with db.begin_nested():
    record_phase2_event(
      db,
      portfolio_id=600,
      target_window={"window_num": 7, "oos_start": "2026-01-01", "oos_end": "2026-04-24"},
      windows_meta=_saved_windows(),
      src_cfg=_src_cfg(),
      step2_result=_step2_result_with_locks({}),
      batch_ids=[1],
      rbm={},
      prior_basket=["A"],
      chosen_basket=["A", "B"],
      added=["B"],
      removed=[],
      basket_unchanged=False,
      event_type_override="hybrid_rotation",
      trigger="rotation_day",
      extra=extras,
    )
  db.commit()
  row = db.query(LiveEvent).filter(LiveEvent.portfolio_id == 600).one()
  assert row.event_type == "hybrid_rotation"
  details = json.loads(row.details)
  # locked fields still present
  assert details["basket_unchanged"] is False
  assert details["recorder_version"] == RECORDER_VERSION
  # extras merged in
  assert details["rotation_hash"] == "abc123"
  assert details["healed_window"]["window_num"] == 7
  assert details["new_live_window"]["window_num"] == 8
  assert details["weights_count"] == 4


def test_extra_none_preserves_default_payload(db):
  """When extra is None / not passed the JSON shape is unchanged."""
  _seed_portfolio(db, portfolio_id=700)
  with db.begin_nested():
    record_phase2_event(
      db,
      portfolio_id=700,
      target_window={"window_num": 1, "oos_start": "2025-01-01", "oos_end": "2025-06-30"},
      windows_meta=_saved_windows(),
      src_cfg=_src_cfg(),
      step2_result=_step2_result_with_locks({}),
      batch_ids=[1],
      rbm={},
      prior_basket=[],
      chosen_basket=["A"],
      added=["A"],
      removed=[],
      basket_unchanged=False,
    )
  db.commit()
  details = json.loads(db.query(LiveEvent).filter(LiveEvent.portfolio_id == 700).one().details)
  assert "rotation_hash" not in details
  assert details["recorder_version"] == RECORDER_VERSION


# ── 8. verify_dispatch_outcome (Bug #7) ────────────────────────────

class _StubEngine:
  """Minimal engine surface mirroring engine_coordinator: only
  `_kelly_alloc_cache` is probed by `verify_dispatch_outcome`. Tests
  mutate the dict directly to simulate cache fill / miss."""
  def __init__(self):
    self._kelly_alloc_cache: dict[int, dict] = {}


def _seed_event_for_verify(db, portfolio_id: int) -> int:
  """Create a portfolio + recorder row + commit it. Return live_event_id.

  We need a committed row because `verify_dispatch_outcome ->
  update_dispatch_outcome` issues its own commit; without a committed
  baseline the row write wouldn't be visible to the lookup query."""
  _seed_portfolio(db, portfolio_id=portfolio_id)
  with db.begin_nested():
    row = record_phase2_event(
      db,
      portfolio_id=portfolio_id,
      target_window={"window_num": 1, "oos_start": "2025-01-01", "oos_end": "2025-06-30"},
      windows_meta=_saved_windows(),
      src_cfg=_src_cfg(),
      step2_result=_step2_result_with_locks({}),
      batch_ids=[1],
      rbm={},
      prior_basket=[],
      chosen_basket=["A"],
      added=["A"],
      removed=[],
      basket_unchanged=False,
    )
    event_id = row.id
  db.commit()
  return event_id


def test_verify_dispatch_outcome_engine_offline_when_provider_returns_none(db):
  """If the engine is None (process not running), the verdict must be
  `engine_offline` — distinguishable from a real dispatch failure so
  the audit log doesn't flag a benign offline state as a bug."""
  event_id = _seed_event_for_verify(db, portfolio_id=801)
  verdict = verify_dispatch_outcome(
    db=db,
    live_event_id=event_id,
    portfolio_id=801,
    timeout_s=0.05,
    poll_interval_s=0.01,
    engine_provider=lambda: None,
  )
  assert verdict == DISPATCH_ENGINE_OFFLINE
  details = json.loads(db.query(LiveEvent).filter(LiveEvent.id == event_id).one().details)
  assert details["dispatch_outcome"] == DISPATCH_ENGINE_OFFLINE


def test_verify_dispatch_outcome_engine_offline_when_provider_raises(db):
  """Engine import path can raise (module not loaded yet). Treat the
  same as `engine_offline` rather than letting the exception crash the
  refresh job."""
  event_id = _seed_event_for_verify(db, portfolio_id=802)

  def _raises():
    raise ImportError("engine_coordinator not loaded")

  verdict = verify_dispatch_outcome(
    db=db,
    live_event_id=event_id,
    portfolio_id=802,
    timeout_s=0.05,
    poll_interval_s=0.01,
    engine_provider=_raises,
  )
  assert verdict == DISPATCH_ENGINE_OFFLINE
  details = json.loads(db.query(LiveEvent).filter(LiveEvent.id == event_id).one().details)
  assert details["dispatch_outcome"] == DISPATCH_ENGINE_OFFLINE


def test_verify_dispatch_outcome_success_when_cache_populated_immediately(db):
  """Happy path: by the time we probe, the engine has already populated
  `_kelly_alloc_cache[pid]` with a non-empty `weights` dict. Verdict is
  `success` on the first poll iteration — no sleep needed."""
  event_id = _seed_event_for_verify(db, portfolio_id=803)
  engine = _StubEngine()
  engine._kelly_alloc_cache[803] = {
    "weights": {"BTCUSDT_long": 0.6, "ETHUSDT_long": 0.4},
    "n_assets": 2,
    "computed_at": "2026-04-25T03:00:00+00:00",
  }
  verdict = verify_dispatch_outcome(
    db=db,
    live_event_id=event_id,
    portfolio_id=803,
    timeout_s=0.5,
    poll_interval_s=0.01,
    engine_provider=lambda: engine,
  )
  assert verdict == DISPATCH_SUCCESS
  details = json.loads(db.query(LiveEvent).filter(LiveEvent.id == event_id).one().details)
  assert details["dispatch_outcome"] == DISPATCH_SUCCESS


def test_verify_dispatch_outcome_failed_when_cache_empty_through_timeout(db):
  """Engine is up but `_kelly_alloc_cache[pid]` never appears. After the
  timeout elapses the verdict is `failed`, distinguishing real silent
  failure from `engine_offline`."""
  event_id = _seed_event_for_verify(db, portfolio_id=804)
  engine = _StubEngine()  # cache stays empty
  verdict = verify_dispatch_outcome(
    db=db,
    live_event_id=event_id,
    portfolio_id=804,
    timeout_s=0.08,
    poll_interval_s=0.02,
    engine_provider=lambda: engine,
  )
  assert verdict == DISPATCH_FAILED
  details = json.loads(db.query(LiveEvent).filter(LiveEvent.id == event_id).one().details)
  assert details["dispatch_outcome"] == DISPATCH_FAILED


def test_verify_dispatch_outcome_success_when_cache_populated_after_poll(db):
  """The engine populates the cache mid-loop on a later poll iteration.
  The helper must keep polling until the deadline rather than giving
  up after the first miss — otherwise nightly dispatches that finish
  in 50-100ms would all be flagged failed."""
  import threading
  event_id = _seed_event_for_verify(db, portfolio_id=805)
  engine = _StubEngine()

  def _populate_late():
    engine._kelly_alloc_cache[805] = {
      "weights": {"BTCUSDT_long": 1.0},
      "n_assets": 1,
      "computed_at": "2026-04-25T03:00:00+00:00",
    }

  # Fire the populate ~80ms after we start polling — well inside our
  # 1.0s timeout but past the first ~3 poll iterations at 20ms each.
  timer = threading.Timer(0.08, _populate_late)
  timer.start()
  try:
    verdict = verify_dispatch_outcome(
      db=db,
      live_event_id=event_id,
      portfolio_id=805,
      timeout_s=1.0,
      poll_interval_s=0.02,
      engine_provider=lambda: engine,
    )
  finally:
    timer.cancel()
  assert verdict == DISPATCH_SUCCESS
  details = json.loads(db.query(LiveEvent).filter(LiveEvent.id == event_id).one().details)
  assert details["dispatch_outcome"] == DISPATCH_SUCCESS


def test_verify_dispatch_outcome_failed_when_cache_entry_has_empty_weights(db):
  """Edge case: cache entry exists but `weights` is empty/falsy. That's
  not a successful dispatch — the engine wrote a stub but the real
  refresh hasn't landed. Verdict must still be `failed` once the
  timeout elapses."""
  event_id = _seed_event_for_verify(db, portfolio_id=806)
  engine = _StubEngine()
  engine._kelly_alloc_cache[806] = {
    "weights": {},  # falsy on purpose
    "n_assets": 0,
    "computed_at": "2026-04-25T03:00:00+00:00",
  }
  verdict = verify_dispatch_outcome(
    db=db,
    live_event_id=event_id,
    portfolio_id=806,
    timeout_s=0.05,
    poll_interval_s=0.01,
    engine_provider=lambda: engine,
  )
  assert verdict == DISPATCH_FAILED
  details = json.loads(db.query(LiveEvent).filter(LiveEvent.id == event_id).one().details)
  assert details["dispatch_outcome"] == DISPATCH_FAILED
