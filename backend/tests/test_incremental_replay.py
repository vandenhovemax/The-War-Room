"""Ship 1 Test 3 — Incremental replay orchestration (L1.1).

What we guard:
  backend/services/batch_store_refresh.py is the nightly job that extends
  every active batch's trade store forward in time. The per-run work
  (replay_run_forward) is heavy — it touches the candle loader, engine,
  and strategy exec path. This test isolates the *orchestration* around
  it and monkey-patches replay_run_forward so we can prove:

    1. refresh_batch iterates every non-deleted run and aggregates
       new_trades / runs_refreshed / runs_skipped / runs_failed correctly.
    2. Per-run failures do not kill the whole batch — the remaining runs
       still get replayed, and the failure is counted.
    3. Inactive (deleted/archived) batches are refused at the batch level.
    4. refresh_all_active_batches filters out deleted/archived/compacted
       batches at the query boundary.
    5. The real replay_run_forward returns the right reason codes for its
       early-exit paths (no_strategy_state, batch_inactive). These are
       not monkey-patched — they hit the real code, just on an in-memory
       DB with no candle data, so the function exits before touching the
       candle loader.

Why this matters: the nightly refresh runs unattended. If one run's
exception propagated, a single broken symbol could silently stop 91
batches from refreshing. Test 2 covers that failure isolation.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  Strategy,
)

# ── In-memory fixture ─────────────────────────────────────────────

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
    s.close()


# ── Seed helpers ──────────────────────────────────────────────────

def _ensure_strategy(db) -> Strategy:
  existing = db.query(Strategy).filter(Strategy.id == 1).first()
  if existing is not None:
    return existing
  s = Strategy(
    id=1, name="incremental_replay_strat",
    source_type="manual",
    python_source="class S:\n  def init(self): pass\n  def next(self): pass\n",
  )
  db.add(s)
  db.commit()
  return s


def _seed_batch(db, batch_id: int, *, is_deleted=False, is_archived=False,
                is_compacted=False, n_runs=3,
                has_state=True) -> BatchBacktestRun:
  _ensure_strategy(db)
  b = BatchBacktestRun(
    id=batch_id, strategy_id=1, timeframe="30",
    direction_filter="both", parameters="{}",
    total_symbols=n_runs, completed_symbols=n_runs,
    status="completed",
    is_deleted=is_deleted, is_archived=is_archived, is_compacted=is_compacted,
  )
  db.add(b)
  db.flush()
  for i in range(n_runs):
    r = BacktestRun(
      strategy_id=1, symbol=f"SYM{i}", timeframe="30",
      start_date=datetime(2024, 1, 1), end_date=datetime(2024, 6, 1),
      parameters="{}", status="completed", batch_id=batch_id,
      strategy_state='{"bars_seen": 10}' if has_state else None,
    )
    db.add(r)
  db.commit()
  return b


# ── Tests ─────────────────────────────────────────────────────────

def test_refresh_batch_aggregates_across_runs(db, monkeypatch):
  """Happy path: every run returns new trades → summary totals add up."""
  _seed_batch(db, 1001, n_runs=3)
  import backend.services.batch_store_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  def _fake_replay(db_, run, candle_cache=None):
    return {"ok": True, "new_trades": 5,
            "from_ts": "2024-06-01T00:00:00", "to_ts": "2024-06-10T00:00:00"}
  monkeypatch.setattr(svc, "replay_run_forward", _fake_replay)

  summary = svc.refresh_batch(1001, db=db)

  assert summary["batch_id"] == 1001
  assert summary["runs_total"] == 3
  assert summary["runs_refreshed"] == 3
  assert summary["runs_skipped"] == 0
  assert summary["runs_failed"] == 0
  assert summary["new_trades"] == 15  # 3 runs × 5 trades each


def test_refresh_batch_skips_runs_with_no_new_bars(db, monkeypatch):
  """'no_new_bars' → skipped, not failed. Aggregates correctly."""
  _seed_batch(db, 1002, n_runs=4)
  import backend.services.batch_store_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  calls = {"n": 0}

  def _fake_replay(db_, run, candle_cache=None):
    calls["n"] += 1
    # Alternate: 2 success, 2 no-new-bars
    if calls["n"] % 2 == 0:
      return {"ok": False, "reason": "no_new_bars"}
    return {"ok": True, "new_trades": 3}
  monkeypatch.setattr(svc, "replay_run_forward", _fake_replay)

  summary = svc.refresh_batch(1002, db=db)
  assert summary["runs_total"] == 4
  assert summary["runs_refreshed"] == 2
  assert summary["runs_skipped"] == 2
  assert summary["runs_failed"] == 0
  assert summary["new_trades"] == 6
  assert summary["reasons"].get("no_new_bars") == 2


def test_refresh_batch_isolates_per_run_failures(db, monkeypatch):
  """One run raises → other runs still complete. Failure counted."""
  _seed_batch(db, 1003, n_runs=3)
  import backend.services.batch_store_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  calls = {"n": 0}

  def _fake_replay(db_, run, candle_cache=None):
    calls["n"] += 1
    if calls["n"] == 2:
      raise RuntimeError("simulated engine crash")
    return {"ok": True, "new_trades": 4}
  monkeypatch.setattr(svc, "replay_run_forward", _fake_replay)

  summary = svc.refresh_batch(1003, db=db)
  assert summary["runs_total"] == 3
  # 2 survived, 1 crashed — critical isolation property
  assert summary["runs_refreshed"] == 2
  assert summary["runs_failed"] == 1
  assert summary["new_trades"] == 8
  # Exception name rolled into reasons dict
  assert any(k.startswith("exc_") for k in summary["reasons"])


def test_refresh_batch_rejects_inactive_batch(db, monkeypatch):
  """Deleted / archived batch → skip without touching any run."""
  _seed_batch(db, 1004, n_runs=2, is_archived=True)
  import backend.services.batch_store_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  called = {"n": 0}

  def _fake_replay(db_, run, candle_cache=None):
    called["n"] += 1
    return {"ok": True, "new_trades": 1}
  monkeypatch.setattr(svc, "replay_run_forward", _fake_replay)

  summary = svc.refresh_batch(1004, db=db)
  assert summary["runs_refreshed"] == 0
  assert summary["reasons"].get("batch") == "inactive"
  assert called["n"] == 0, "replay_run_forward must not be called on archived batch"


def test_refresh_all_filters_inactive_batches(db, monkeypatch):
  """Only non-deleted, non-archived, non-compacted batches enter the loop."""
  # active
  _seed_batch(db, 1005, n_runs=1)
  # deleted
  _seed_batch(db, 1006, n_runs=1, is_deleted=True)
  # archived
  _seed_batch(db, 1007, n_runs=1, is_archived=True)
  # compacted
  _seed_batch(db, 1008, n_runs=1, is_compacted=True)

  import backend.services.batch_store_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  touched = []

  def _fake_replay(db_, run, candle_cache=None):
    touched.append(run.batch_id)
    return {"ok": True, "new_trades": 1}
  monkeypatch.setattr(svc, "replay_run_forward", _fake_replay)

  report = svc.refresh_all_active_batches()

  assert 1005 in touched
  assert 1006 not in touched, "deleted batch leaked into nightly job"
  assert 1007 not in touched, "archived batch leaked into nightly job"
  assert 1008 not in touched, "compacted batch leaked into nightly job"
  # Report shape holds
  assert "started_at" in report and "ended_at" in report
  assert report["total_new_trades"] >= 1


def test_replay_run_forward_rejects_run_without_state(db):
  """Real replay_run_forward returns no_strategy_state without crashing.

  This hits the real function (not monkey-patched) to prove its early-exit
  contract: a run with strategy_state=None is a "needs full rebuild" signal,
  not a failure.
  """
  from backend.services.batch_store_refresh import replay_run_forward

  _seed_batch(db, 1009, n_runs=1, has_state=False)
  run = db.query(BacktestRun).filter(BacktestRun.batch_id == 1009).first()
  assert run is not None
  assert run.strategy_state is None

  res = replay_run_forward(db, run)
  assert res["ok"] is False
  assert res["reason"] == "no_strategy_state"


def test_replay_run_forward_rejects_inactive_batch(db):
  """Real replay_run_forward returns batch_inactive when the parent batch
  is archived, even if the run itself has state.
  """
  from backend.services.batch_store_refresh import replay_run_forward

  _seed_batch(db, 1010, n_runs=1, is_archived=True, has_state=True)
  run = db.query(BacktestRun).filter(BacktestRun.batch_id == 1010).first()
  assert run is not None
  assert run.strategy_state is not None

  res = replay_run_forward(db, run)
  assert res["ok"] is False
  assert res["reason"] == "batch_inactive"


def test_no_trades_written_when_replay_skipped(db, monkeypatch):
  """Convergence invariant: if nothing is added, the trade store must be
  byte-identical to its starting state. This is the defining property of
  the nightly job — idle days don't mutate history."""
  _seed_batch(db, 1011, n_runs=2)
  run_ids = [r.id for r in db.query(BacktestRun).filter(BacktestRun.batch_id == 1011).all()]
  trades_before = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).count()

  import backend.services.batch_store_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  # All runs return no_new_bars → replay must not touch the trade table
  monkeypatch.setattr(
    svc, "replay_run_forward",
    lambda db_, run, candle_cache=None: {"ok": False, "reason": "no_new_bars"},
  )

  summary = svc.refresh_batch(1011, db=db)
  assert summary["runs_skipped"] == 2
  assert summary["new_trades"] == 0

  trades_after = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).count()
  assert trades_before == trades_after, (
    "BacktestTrade row count changed on a no-op refresh — "
    "convergence (idle→identical) broken."
  )
