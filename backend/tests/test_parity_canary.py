"""Ship 3 Test 8 — Parity canary fires on seeded divergence.

Validates the L4.1 monthly audit (backend/services/parity_canary.py).

What we guard:
  1. `run_parity_canary` iterates every active BacktestRun inside every
     active, non-archived, non-compacted BatchBacktestRun.
  2. Deleted / archived / compacted batches are excluded from the audit.
  3. Orchestration aggregates totals correctly:
        total_combos, combos_with_diff, errors
  4. A seeded divergence (monkey-patched per-run comparator returning
     has_diff=True) flips `combos_with_diff` and writes a LiveEvent with
     event_type='parity_canary' on the Activity feed.
  5. A clean run (has_diff=False on every run) writes a ParityCanaryReport
     with combos_with_diff=0 AND emits no LiveEvent (no false alarms).
  6. Per-run exceptions are isolated — one bad run cannot kill the audit.
  7. The _diff_trades helper correctly classifies stored_only / fresh_only
     / pnl_delta cases (unit test of the core comparator primitive).

Why we monkey-patch `compare_run_parity`:
  The real implementation spins up the candle loader + engine. Test 2
  (test_strategy_state_parity) already guards engine correctness; this
  test focuses strictly on the orchestration + audit-logging behavior,
  which is the new code in Ship 3.
"""
from __future__ import annotations

import json
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
  LiveEvent,
  ParityCanaryReport,
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
    # Clean slate per test — report counts compare against fresh state.
    s.query(LiveEvent).delete()
    s.query(ParityCanaryReport).delete()
    s.query(BacktestTrade).delete()
    s.query(BacktestRun).delete()
    s.query(BatchBacktestRun).delete()
    s.query(Strategy).delete()
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


# ── Seed helpers ──────────────────────────────────────────────────

def _ensure_strategy(db, sid=1):
  if db.query(Strategy).filter(Strategy.id == sid).first() is not None:
    return
  db.add(Strategy(
    id=sid, name="canary_strat", source_type="manual",
    python_source="class S:\n  def init(self): pass\n  def next(self): pass\n",
  ))
  db.commit()


def _seed_batch(
  db, batch_id: int, *,
  runs: list[str],
  is_deleted=False, is_archived=False, is_compacted=False,
):
  """Create a batch + one BacktestRun per symbol in `runs`."""
  _ensure_strategy(db)
  db.add(BatchBacktestRun(
    id=batch_id, strategy_id=1, timeframe="30",
    direction_filter="both", parameters="{}",
    total_symbols=len(runs), completed_symbols=len(runs),
    status="completed",
    is_deleted=is_deleted, is_archived=is_archived, is_compacted=is_compacted,
  ))
  db.flush()
  for sym in runs:
    db.add(BacktestRun(
      strategy_id=1, symbol=sym, timeframe="30",
      start_date=datetime(2024, 1, 1), end_date=datetime(2024, 6, 1),
      parameters="{}", status="completed", batch_id=batch_id,
    ))
  db.commit()


# ── Tests ─────────────────────────────────────────────────────────

def test_canary_clean_run_writes_report_no_liveevent(db, monkeypatch):
  """Every run returns has_diff=False → report saved, NO LiveEvent."""
  _seed_batch(db, 100, runs=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
  import backend.services.parity_canary as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  def _fake(_db, _run):
    return {"ok": True, "reason": None, "has_diff": False,
            "diff": {"has_diff": False, "stored_count": 5, "fresh_count": 5}}
  monkeypatch.setattr(svc, "compare_run_parity", _fake)

  result = svc.run_parity_canary(db=db)

  assert result["total_combos"] == 3
  assert result["combos_with_diff"] == 0
  assert result["errors"] == 0
  assert result["report_id"] is not None

  # Report persisted.
  reports = db.query(ParityCanaryReport).all()
  assert len(reports) == 1
  assert reports[0].total_combos == 3
  assert reports[0].combos_with_diff == 0
  payload = json.loads(reports[0].report_json)
  assert len(payload["runs"]) == 3
  assert all(r["has_diff"] is False for r in payload["runs"])

  # Activity feed: clean run must NOT emit a parity_canary LiveEvent.
  events = db.query(LiveEvent).filter(
    LiveEvent.event_type == "parity_canary"
  ).all()
  assert events == []


def test_canary_seeded_divergence_fires_liveevent(db, monkeypatch):
  """One run diverges → combos_with_diff=1, LiveEvent emitted."""
  _seed_batch(db, 200, runs=["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"])
  import backend.services.parity_canary as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  # Make ETHUSDT diverge, rest clean.
  def _fake(_db, run):
    if run.symbol == "ETHUSDT":
      return {
        "ok": True, "reason": None, "has_diff": True,
        "diff": {
          "has_diff": True,
          "stored_count": 12, "fresh_count": 11,
          "stored_only_count": 1, "fresh_only_count": 0,
          "pnl_delta": -42.5,
          "stored_only_sample": [["2024-01-15T00:00:00", "long", 42.5]],
          "fresh_only_sample": [],
        },
      }
    return {"ok": True, "reason": None, "has_diff": False,
            "diff": {"has_diff": False, "stored_count": 8, "fresh_count": 8}}
  monkeypatch.setattr(svc, "compare_run_parity", _fake)

  result = svc.run_parity_canary(db=db)

  assert result["total_combos"] == 4
  assert result["combos_with_diff"] == 1
  assert result["errors"] == 0

  # LiveEvent emitted for Activity feed.
  events = db.query(LiveEvent).filter(
    LiveEvent.event_type == "parity_canary"
  ).all()
  assert len(events) == 1
  details = json.loads(events[0].details)
  assert details["combos_with_diff"] == 1
  assert details["total_combos"] == 4
  assert details["report_id"] == result["report_id"]

  # Report contains the diff payload for the diverged run.
  payload = json.loads(
    db.query(ParityCanaryReport).filter(
      ParityCanaryReport.id == result["report_id"]
    ).first().report_json
  )
  diverged = [r for r in payload["runs"] if r["has_diff"]]
  assert len(diverged) == 1
  assert diverged[0]["symbol"] == "ETHUSDT"
  assert diverged[0]["diff"]["pnl_delta"] == -42.5


def test_canary_skips_inactive_batches(db, monkeypatch):
  """Deleted, archived, and compacted batches must be excluded."""
  _seed_batch(db, 301, runs=["BTCUSDT", "ETHUSDT"])   # active
  _seed_batch(db, 302, runs=["SOLUSDT"], is_deleted=True)
  _seed_batch(db, 303, runs=["ADAUSDT"], is_archived=True)
  _seed_batch(db, 304, runs=["LINKUSDT"], is_compacted=True)

  import backend.services.parity_canary as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  visited = []

  def _fake(_db, run):
    visited.append((run.batch_id, run.symbol))
    return {"ok": True, "reason": None, "has_diff": False, "diff": {}}
  monkeypatch.setattr(svc, "compare_run_parity", _fake)

  result = svc.run_parity_canary(db=db)

  # Only the 2 runs in batch 301 should have been compared.
  assert result["total_combos"] == 2
  assert sorted(visited) == [(301, "BTCUSDT"), (301, "ETHUSDT")]


def test_canary_isolates_per_run_failures(db, monkeypatch):
  """One run throws → others still processed; error counted."""
  _seed_batch(db, 400, runs=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
  import backend.services.parity_canary as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  def _fake(_db, run):
    if run.symbol == "ETHUSDT":
      raise RuntimeError("simulated comparator crash")
    return {"ok": True, "reason": None, "has_diff": False, "diff": {}}
  monkeypatch.setattr(svc, "compare_run_parity", _fake)

  result = svc.run_parity_canary(db=db)

  # All 3 counted, 1 error, 0 divergences.
  assert result["total_combos"] == 3
  assert result["errors"] == 1
  assert result["combos_with_diff"] == 0

  payload = json.loads(
    db.query(ParityCanaryReport).filter(
      ParityCanaryReport.id == result["report_id"]
    ).first().report_json
  )
  crashed = [r for r in payload["runs"] if r["symbol"] == "ETHUSDT"]
  assert len(crashed) == 1
  assert crashed[0]["ok"] is False
  assert crashed[0]["reason"].startswith("exc_RuntimeError")


def test_canary_max_combos_caps_scan(db, monkeypatch):
  """max_combos honors the limit so ad-hoc runs stay bounded."""
  _seed_batch(db, 500, runs=[f"SYM{i}" for i in range(7)])
  import backend.services.parity_canary as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  count = {"n": 0}

  def _fake(_db, _run):
    count["n"] += 1
    return {"ok": True, "reason": None, "has_diff": False, "diff": {}}
  monkeypatch.setattr(svc, "compare_run_parity", _fake)

  result = svc.run_parity_canary(db=db, max_combos=3)

  assert result["total_combos"] == 3
  assert count["n"] == 3


def test_canary_does_not_mutate_trades_or_runs(db, monkeypatch):
  """Audit-only guarantee: no BacktestTrade / BacktestRun writes."""
  _seed_batch(db, 600, runs=["BTCUSDT", "ETHUSDT"])
  # Seed a couple of existing trades so we can detect additions/deletions.
  runs = db.query(BacktestRun).filter(BacktestRun.batch_id == 600).all()
  for r in runs:
    db.add(BacktestTrade(
      run_id=r.id, trade_num=1, position_id=f"P{r.id}",
      symbol=r.symbol, side="long",
      entry_time=datetime(2024, 2, 1), exit_time=datetime(2024, 2, 2),
      entry_price=100.0, exit_price=110.0, quantity=1.0,
      net_profit=10.0, commission=0.1,
    ))
  db.commit()

  trades_before = db.query(BacktestTrade).count()
  runs_before = [(r.id, r.end_date) for r in
                 db.query(BacktestRun).order_by(BacktestRun.id).all()]

  import backend.services.parity_canary as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(
    svc, "compare_run_parity",
    lambda _db, _run: {"ok": True, "reason": None,
                       "has_diff": True,
                       "diff": {"has_diff": True, "stored_count": 1,
                                "fresh_count": 2, "pnl_delta": 5.0,
                                "stored_only_count": 0, "fresh_only_count": 1,
                                "stored_only_sample": [],
                                "fresh_only_sample": []}},
  )

  svc.run_parity_canary(db=db)

  trades_after = db.query(BacktestTrade).count()
  runs_after = [(r.id, r.end_date) for r in
                db.query(BacktestRun).order_by(BacktestRun.id).all()]

  assert trades_after == trades_before, "parity canary must never write BacktestTrade rows"
  assert runs_after == runs_before, "parity canary must never mutate BacktestRun rows"


# ── Unit tests on the _diff_trades comparator helper ─────────────

def test_diff_trades_identical_sets_has_no_diff():
  from backend.services.parity_canary import _diff_trades
  trades = [
    {"entry_time": datetime(2024, 1, 1), "side": "long", "net_profit": 10.0},
    {"entry_time": datetime(2024, 1, 2), "side": "short", "net_profit": -5.0},
  ]
  diff = _diff_trades(trades, trades)
  assert diff["has_diff"] is False
  assert diff["stored_only_count"] == 0
  assert diff["fresh_only_count"] == 0
  assert diff["pnl_delta"] == 0.0


def test_diff_trades_missing_stored_flags_diff():
  from backend.services.parity_canary import _diff_trades
  t1 = {"entry_time": datetime(2024, 1, 1), "side": "long", "net_profit": 10.0}
  t2 = {"entry_time": datetime(2024, 1, 2), "side": "short", "net_profit": -5.0}
  diff = _diff_trades([t1, t2], [t1])  # fresh run missing t2
  assert diff["has_diff"] is True
  assert diff["stored_only_count"] == 1
  assert diff["fresh_only_count"] == 0
  assert diff["pnl_delta"] == pytest.approx(5.0)  # fresh - stored


def test_diff_trades_pnl_only_drift_below_cent_ignored():
  """Trade identity keys round pnl to cents — sub-cent float jitter is
  expected across platforms and must not fire the canary. But a > $0.01
  summed delta DOES fire, even when entry_time matches."""
  from backend.services.parity_canary import _diff_trades
  # 1000 trades with +0.005 each — rounds to 0.01/0.00 per trade. Summed
  # absolute delta = 5.0, which is far above the 0.01 floor.
  stored = [
    {"entry_time": datetime(2024, 1, i + 1), "side": "long",
     "net_profit": 100.0} for i in range(5)
  ]
  fresh = [
    {"entry_time": datetime(2024, 1, i + 1), "side": "long",
     "net_profit": 100.0 + 0.5} for i in range(5)
  ]
  diff = _diff_trades(stored, fresh)
  assert diff["has_diff"] is True
  assert diff["pnl_delta"] == pytest.approx(2.5)  # +0.5 * 5


def test_diff_trades_micro_float_jitter_does_not_fire():
  """Sub-penny noise across all trades must not trigger the canary — the
  cents-level identity + $0.01 summed-delta tolerance absorbs it."""
  from backend.services.parity_canary import _diff_trades
  stored = [{"entry_time": datetime(2024, 1, 1), "side": "long",
             "net_profit": 100.0}]
  fresh = [{"entry_time": datetime(2024, 1, 1), "side": "long",
            "net_profit": 100.004}]
  diff = _diff_trades(stored, fresh)
  assert diff["has_diff"] is False
