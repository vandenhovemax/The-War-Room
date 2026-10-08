"""CS-057: live-loop orchestrator tests currently monkey-patch the
very services they claim to cover — so a service-signature change or
a silent DB-write semantics change can pass every existing test.

Existing mock tests (``test_live_loop_catchup.py``) stay useful for
fast feedback. This file *adds* one thin integration test per
orchestrator that exercises the real service against an in-memory DB
with empty tables. If any service signature drifts from what the
orchestrator calls, this test breaks.

Strategy: redirect each service's ``SessionLocal`` to a test session
and call the catch-up orchestrator with an empty DB. On an empty DB
every service is a no-op that returns its contract dict — which is
exactly what we want to lock.
"""
from __future__ import annotations

import inspect

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base


# ── In-memory DB & SessionLocal redirect ─────────────────────────────

_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
  cur.execute("PRAGMA journal_mode=WAL")
  cur.execute("PRAGMA foreign_keys=ON")
  cur.close()


_TestSession = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


@pytest.fixture(scope="module", autouse=True)
def _schema():
  Base.metadata.create_all(bind=_ENGINE)
  yield
  Base.metadata.drop_all(bind=_ENGINE)


@pytest.fixture
def patched_services(monkeypatch):
  """Redirect every service's ``SessionLocal`` to the test DB.

  The services import ``SessionLocal`` at module load, so the rebind
  has to target the service module itself — not
  ``backend.db.database``.
  """
  import backend.db.database as _db_module
  import backend.services.batch_store_refresh as _batch_svc
  import backend.services.hybrid_portfolio_refresh as _hybrid_svc
  import backend.services.oos_boundary as _oos_svc
  import backend.services.parity_canary as _canary_svc
  import backend.services.step1_refresh_worker as _worker_svc

  for mod in (
    _db_module, _batch_svc, _hybrid_svc, _oos_svc, _canary_svc, _worker_svc,
  ):
    monkeypatch.setattr(mod, "SessionLocal", _TestSession, raising=False)

  yield


# ── Tests ────────────────────────────────────────────────────────────

def test_catch_up_runs_all_four_real_services_on_empty_db(patched_services):
  """Integration: call ``catch_up(include_canary=True)`` with all four
  real services pointed at an empty in-memory DB. Every layer must
  return its documented contract dict — a signature or DB-write
  change in any service would trip this.
  """
  from backend.api.routes.live_loop import catch_up

  resp = catch_up(include_canary=True)

  # Orchestrator contract: four layers, in order.
  assert resp["ok"] is True, (
    f"CS-057 REGRESSION: catch_up reported failure on an empty DB — "
    f"a service crashed where it should have returned an empty "
    f"report. Details: {resp}"
  )
  assert len(resp["layers"]) == 4
  assert [l["layer"] for l in resp["layers"]] == [
    "l1_batch_store_refresh",
    "l2_hybrid_portfolio_refresh",
    "l3_oos_boundary",
    "l4_parity_canary",
  ], (
    "CS-057 REGRESSION: catch_up no longer dispatches the four "
    "expected services in order. A rewire in backend.api.routes."
    "live_loop has broken the contract."
  )

  # L1 contract — batch_store_refresh.
  l1 = resp["layers"][0]
  assert l1["ok"] is True
  for key in ("started_at", "ended_at", "batches", "total_new_trades", "errors"):
    assert key in l1["result"], f"L1 missing contract key {key!r}"
  assert l1["result"]["total_new_trades"] == 0
  assert l1["result"]["errors"] == 0
  assert l1["result"]["batches"] == []

  # L2 contract — hybrid_portfolio_refresh.
  l2 = resp["layers"][1]
  assert l2["ok"] is True
  for key in ("started_at", "ended_at", "results", "total_hybrid", "ok", "failed"):
    assert key in l2["result"], f"L2 missing contract key {key!r}"
  assert l2["result"]["total_hybrid"] == 0

  # L3 contract — oos_boundary.check_and_enqueue.
  l3 = resp["layers"][2]
  assert l3["ok"] is True
  for key in ("scanned", "past_boundary", "enqueued", "skipped_pending", "results"):
    assert key in l3["result"], f"L3 missing contract key {key!r}"
  assert l3["result"]["scanned"] == 0

  # L4 contract — parity_canary.run_parity_canary.
  l4 = resp["layers"][3]
  assert l4["ok"] is True
  for key in ("run_date", "total_combos", "combos_with_diff",
              "errors", "report", "report_id"):
    assert key in l4["result"], f"L4 missing contract key {key!r}"
  assert l4["result"]["total_combos"] == 0


def test_catch_up_persisted_a_parity_canary_row(patched_services):
  """The canary service is supposed to write one ``ParityCanaryReport``
  row per run. An in-memory DB + real service confirms the write
  actually happens — a silent ``db.commit()`` drop would be
  invisible to mock tests.
  """
  from backend.api.routes.live_loop import catch_up
  from backend.db.models import ParityCanaryReport

  catch_up(include_canary=True)

  db = _TestSession()
  try:
    rows = db.query(ParityCanaryReport).all()
    assert len(rows) >= 1, (
      "CS-057 REGRESSION: run_parity_canary did not persist a "
      "ParityCanaryReport row. The monkey-patched mock test would "
      "have passed silently — the real DB write is missing."
    )
  finally:
    db.close()


def test_run_one_dispatches_to_real_service_by_alias(patched_services):
  """``run_one('l3')`` must call the real ``oos_boundary.check_and_
  enqueue`` and return its contract dict. This locks the alias →
  service mapping without a mock.
  """
  from backend.api.routes.live_loop import run_one

  resp = run_one("l3")
  assert isinstance(resp, dict)
  assert resp["ok"] is True
  assert resp["layer"] == "l3_oos_boundary"
  # Real service's contract keys:
  for key in ("scanned", "past_boundary", "enqueued", "skipped_pending"):
    assert key in resp["result"], f"run_one('l3') result missing {key!r}"


def test_service_signatures_match_orchestrator_calls():
  """Static check: each service the orchestrator calls must take the
  argument shape the orchestrator passes. A signature drift that
  ``raise TypeError`` at runtime would only surface if the service
  is actually invoked — but a mocked test never invokes it. So we
  lock the signature here too.
  """
  from backend.services import (
    batch_store_refresh, hybrid_portfolio_refresh,
    oos_boundary, parity_canary,
  )

  # L1, L2 are zero-arg entry points.
  for fn in (
    batch_store_refresh.refresh_all_active_batches,
    hybrid_portfolio_refresh.refresh_all_hybrid_portfolios,
  ):
    sig = inspect.signature(fn)
    required = [
      p for p in sig.parameters.values()
      if p.default is inspect.Parameter.empty
      and p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)
    ]
    assert not required, (
      f"CS-057 REGRESSION: {fn.__qualname__} now requires {required!r}. "
      "The catch_up orchestrator calls it with zero args and will "
      "raise TypeError at runtime — but mocks in the existing "
      "live-loop tests would silently absorb it."
    )

  # L3 entry point: keyword-only `now` with a default.
  sig_l3 = inspect.signature(oos_boundary.check_and_enqueue)
  assert "now" in sig_l3.parameters
  assert sig_l3.parameters["now"].default is None, (
    "CS-057 REGRESSION: oos_boundary.check_and_enqueue.now default "
    "changed — catch_up calls it with no args, expecting the default."
  )

  # L4 entry point: keyword-only args with defaults.
  sig_l4 = inspect.signature(parity_canary.run_parity_canary)
  for param_name in ("batch_ids", "max_combos", "db"):
    assert param_name in sig_l4.parameters, (
      f"CS-057 REGRESSION: run_parity_canary dropped {param_name!r} "
      "parameter. catch_up or run_one may pass it and crash."
    )
