"""Ship 2 Test 6 — OOS-boundary Step-1 rotation + proposal flow.

What we guard (plan §L3 / validation row 6 — "Clock past OOS end →
proposal fires, no auto-rotate"):

  1. `oos_boundary.check_and_enqueue(now=<past OOS>)`:
     * Enqueues a RebalanceProposal row (status='pending') for a portfolio
       that is past its oos_end.
     * Does NOT mutate Portfolio, PortfolioAsset, or KellyAllocation.
     * Skips portfolios still inside their OOS window.
     * Skips portfolios that already have a pending proposal
       (report counts them under `skipped_pending`).

  2. `rebalance.approve_proposal`:
     * Forks a NEW Portfolio row with the proposed basket.
     * Archives the old portfolio (is_active=False, deactivated_at set) —
       does NOT delete it.
     * Sets the proposal's status='approved', resulting_portfolio_id.
     * Writes a LiveEvent(event_type='rebalance_approved').

  3. `rebalance.reject_proposal`:
     * Flips status to 'rejected'.
     * Leaves Portfolio / PortfolioAsset untouched.
     * Writes a LiveEvent(event_type='rebalance_rejected').

Strategy for the test:
  The Step-1 core (run_batch_selection) is too heavy to invoke here and
  is independently covered elsewhere. We monkey-patch:
    * step1_refresh_worker.run_batch_selection → returns a minimal result
      dict with a controlled summary.portfolio (the new basket).
    * step1_refresh_worker.load_unified_trades → returns empty trades
      (we never exercise the Step-1 math in this test).
    * oos_boundary.SessionLocal → the test's in-memory Session.

  Approve/reject are called directly against the route functions with
  the test DB session (they accept `db: Session = Depends(get_db)` which
  we pass positionally).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
  Base,
  KellyAllocation,
  LiveEvent,
  Portfolio,
  PortfolioAsset,
  RebalanceProposal,
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
    # Clean slate per test — these tests create portfolios with
    # overlapping attributes and assert on global counts, so the shared
    # module-scoped engine needs reset between tests.
    s.query(LiveEvent).delete()
    s.query(RebalanceProposal).delete()
    s.query(KellyAllocation).delete()
    s.query(PortfolioAsset).delete()
    s.query(Portfolio).delete()
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


# ── Seed helpers ──────────────────────────────────────────────────

def _make_portfolio(
  db,
  *,
  name: str,
  oos_end: datetime,
  basket: list[tuple[str, str, int]],  # [(symbol, side, batch_id), ...]
  oos_length_days: int = 30,
  mode: str = "non_hybrid",
  portfolio_type: str = "paper",
  is_active: bool = True,
) -> int:
  """Create a portfolio whose optimization_end_date is set so that
  (optimization_end_date + oos_length_days) == oos_end. Caller picks
  oos_end explicitly; we reverse-engineer optimization_end_date.
  """
  p = Portfolio(
    name=name,
    portfolio_type=portfolio_type,
    mode=mode,
    is_active=is_active,
    is_deleted=False,
    optimization_end_date=oos_end - timedelta(days=oos_length_days),
    source_strategies=json.dumps({
      "strategy_id": 1,
      "timeframe": "30",
      "oos_length_days": oos_length_days,
      "bs_config": {},
    }),
  )
  db.add(p)
  db.flush()
  for sym, side, bid in basket:
    db.add(PortfolioAsset(
      portfolio_id=p.id, symbol=sym, trade_side=side,
      strategy_name="TestStrat", raw_name=f"{sym}_{side}",
      batch_id=bid, is_active=True,
      config_name="default", timeframe="30",
    ))
  db.commit()
  return p.id


def _patch_worker_step1(monkeypatch, new_basket: list[str]):
  """Wire up step1_refresh_worker so run_batch_selection returns a
  controlled basket and load_unified_trades returns empty trades.
  """
  import backend.services.step1_refresh_worker as worker

  def _fake_run(trades, windows, config, rbm, batch_info=None, db=None):
    return {
      "summary": {"portfolio": list(new_basket)},
      "config_used": {},
    }
  monkeypatch.setattr(worker, "run_batch_selection", _fake_run)
  monkeypatch.setattr(
    worker, "load_unified_trades",
    lambda db, batch_ids=None, cutoff=None, portfolio_id=None: ([], {}, {}),
  )


# ── Tests ─────────────────────────────────────────────────────────

def test_not_past_oos_end_skipped(db, monkeypatch):
  """Portfolio whose OOS window hasn't ended yet → no proposal written."""
  future_oos = datetime(2026, 1, 1)
  pid = _make_portfolio(
    db, name="inside_oos", oos_end=future_oos,
    basket=[("BTCUSDT", "long", 1), ("ETHUSDT", "long", 1)],
  )

  import backend.services.oos_boundary as ob
  monkeypatch.setattr(ob, "SessionLocal", _Session)
  _patch_worker_step1(monkeypatch, ["BTCUSDT_Long"])

  # 'now' is BEFORE oos_end, so portfolio is not past boundary.
  report = ob.check_and_enqueue(now=datetime(2025, 12, 1))

  assert report["scanned"] == 1
  assert report["past_boundary"] == 0
  assert report["enqueued"] == 0
  assert report["skipped_pending"] == 0

  proposals = db.query(RebalanceProposal).filter(
    RebalanceProposal.portfolio_id == pid
  ).all()
  assert proposals == []


def test_past_oos_end_enqueues_proposal_without_mutating_portfolio(db, monkeypatch):
  """Clock past oos_end → proposal written. Portfolio + basket untouched."""
  oos_end = datetime(2025, 6, 1)
  basket = [
    ("BTCUSDT", "long", 1),
    ("ETHUSDT", "long", 1),
    ("BTCUSDT", "short", 2),
  ]
  pid = _make_portfolio(db, name="past_oos", oos_end=oos_end, basket=basket)

  # Snapshot portfolio + asset state BEFORE the boundary scan.
  before = db.query(Portfolio).filter(Portfolio.id == pid).first()
  before_is_active = before.is_active
  before_opt_end = before.optimization_end_date
  before_assets = [
    (a.id, a.symbol, a.trade_side, a.batch_id, a.is_active)
    for a in db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == pid)
    .order_by(PortfolioAsset.id)
    .all()
  ]

  # Step-1 re-run returns a new basket: drop ETHUSDT_Long, add SOLUSDT_Long.
  new_basket = ["BTCUSDT_Long", "SOLUSDT_Long", "BTCUSDT_Short"]

  import backend.services.oos_boundary as ob
  monkeypatch.setattr(ob, "SessionLocal", _Session)
  _patch_worker_step1(monkeypatch, new_basket)

  report = ob.check_and_enqueue(now=datetime(2025, 7, 15))

  assert report["scanned"] == 1
  assert report["past_boundary"] == 1
  assert report["enqueued"] == 1
  assert report["skipped_pending"] == 0
  assert report["results"][0]["reason"] == "proposed"
  assert report["results"][0]["portfolio_id"] == pid

  # Proposal persisted with correct shape.
  proposals = db.query(RebalanceProposal).filter(
    RebalanceProposal.portfolio_id == pid
  ).all()
  assert len(proposals) == 1
  prop = proposals[0]
  assert prop.status == "pending"
  assert prop.trigger_type == "oos_boundary"
  assert prop.reviewed_at is None
  assert prop.reviewed_by is None
  assert prop.resulting_portfolio_id is None
  # Current basket (sorted) reflects the 3 seeded assets.
  current = json.loads(prop.current_basket_json)
  assert sorted(current) == sorted([
    "BTCUSDT_Long", "ETHUSDT_Long", "BTCUSDT_Short",
  ])
  # Proposed basket matches what Step-1 returned.
  assert sorted(json.loads(prop.proposed_basket_json)) == sorted(new_basket)
  diff = json.loads(prop.diff_json)
  assert diff["added"] == ["SOLUSDT_Long"]
  assert diff["removed"] == ["ETHUSDT_Long"]
  assert sorted(diff["unchanged"]) == ["BTCUSDT_Long", "BTCUSDT_Short"]

  # Portfolio + assets MUST NOT have been mutated (Safety Contract).
  after = db.query(Portfolio).filter(Portfolio.id == pid).first()
  assert after.is_active == before_is_active
  assert after.optimization_end_date == before_opt_end
  assert after.deactivated_at is None

  after_assets = [
    (a.id, a.symbol, a.trade_side, a.batch_id, a.is_active)
    for a in db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == pid)
    .order_by(PortfolioAsset.id)
    .all()
  ]
  assert after_assets == before_assets, (
    "PortfolioAsset rows changed during boundary scan — auto-rotation "
    "leaked. This is a Safety Contract violation."
  )

  # No KellyAllocation writes either.
  assert db.query(KellyAllocation).count() == 0


def test_already_pending_is_skipped(db, monkeypatch):
  """Running check_and_enqueue twice → second call finds pending and skips.

  This guards against proposal flooding if the nightly scheduler fires and
  the user hasn't reviewed the previous one yet.
  """
  oos_end = datetime(2025, 6, 1)
  pid = _make_portfolio(
    db, name="dup_guard", oos_end=oos_end,
    basket=[("BTCUSDT", "long", 1)],
  )

  import backend.services.oos_boundary as ob
  monkeypatch.setattr(ob, "SessionLocal", _Session)
  _patch_worker_step1(monkeypatch, ["BTCUSDT_Long", "ETHUSDT_Long"])

  # First call: enqueues.
  r1 = ob.check_and_enqueue(now=datetime(2025, 7, 15))
  assert r1["enqueued"] == 1
  assert r1["skipped_pending"] == 0

  # Second call: already a pending proposal → skipped.
  r2 = ob.check_and_enqueue(now=datetime(2025, 7, 16))
  assert r2["enqueued"] == 0
  assert r2["skipped_pending"] == 1
  assert r2["results"][0]["reason"] == "already_pending"

  # Still exactly one proposal on disk.
  n = db.query(RebalanceProposal).filter(
    RebalanceProposal.portfolio_id == pid
  ).count()
  assert n == 1


def test_approve_forks_new_portfolio_and_archives_old(db, monkeypatch):
  """Approve → new Portfolio row, old archived (is_active=False)."""
  oos_end = datetime(2025, 6, 1)
  old_basket = [
    ("BTCUSDT", "long", 1),
    ("ETHUSDT", "long", 1),
    ("ADAUSDT", "long", 2),
  ]
  old_pid = _make_portfolio(
    db, name="to_rotate", oos_end=oos_end, basket=old_basket,
    portfolio_type="paper", mode="non_hybrid",
  )

  import backend.services.oos_boundary as ob
  monkeypatch.setattr(ob, "SessionLocal", _Session)
  # New basket: keep BTC long + ETH long, drop ADA long, add SOL long.
  new_basket = ["BTCUSDT_Long", "ETHUSDT_Long", "SOLUSDT_Long"]
  _patch_worker_step1(monkeypatch, new_basket)

  ob.check_and_enqueue(now=datetime(2025, 7, 15))

  prop = db.query(RebalanceProposal).filter(
    RebalanceProposal.portfolio_id == old_pid
  ).first()
  assert prop is not None
  assert prop.status == "pending"

  # Call the approve route function directly with the test DB session.
  from backend.api.routes.rebalance import ApproveBody, approve_proposal
  resp = approve_proposal(
    proposal_id=prop.id, body=ApproveBody(reviewed_by="max"), db=db,
  )

  assert resp["ok"] is True
  new_pid = resp["new_portfolio_id"]
  assert new_pid != old_pid

  # Proposal finalized.
  db.refresh(prop)
  assert prop.status == "approved"
  assert prop.reviewed_by == "max"
  assert prop.reviewed_at is not None
  assert prop.resulting_portfolio_id == new_pid

  # OLD portfolio archived, not deleted.
  old = db.query(Portfolio).filter(Portfolio.id == old_pid).first()
  assert old is not None, "old portfolio must be kept for audit"
  assert old.is_active is False
  assert old.is_deleted is False
  assert old.deactivated_at is not None

  # Old PortfolioAsset rows still exist (audit trail).
  old_assets = db.query(PortfolioAsset).filter(
    PortfolioAsset.portfolio_id == old_pid
  ).all()
  assert len(old_assets) == len(old_basket)

  # NEW portfolio has the proposed basket.
  new = db.query(Portfolio).filter(Portfolio.id == new_pid).first()
  assert new is not None
  assert new.is_active is True
  assert new.source_portfolio_id == old_pid
  assert new.mode == old.mode
  assert new.portfolio_type == old.portfolio_type
  # Fresh OOS clock: optimization_end_date == activated_at (both ~now).
  assert new.activated_at is not None
  assert new.optimization_end_date is not None

  new_assets = db.query(PortfolioAsset).filter(
    PortfolioAsset.portfolio_id == new_pid
  ).all()
  new_keys = sorted(f"{a.symbol}_{a.trade_side.capitalize()}" for a in new_assets)
  assert new_keys == sorted(new_basket)

  # Carried-forward assets (BTCUSDT_Long, ETHUSDT_Long) keep their old
  # batch_id; truly-new SOLUSDT_Long has batch_id=None (the user resolves
  # it later or in a follow-up proposal).
  by_key = {f"{a.symbol}_{a.trade_side.capitalize()}": a for a in new_assets}
  assert by_key["BTCUSDT_Long"].batch_id == 1
  assert by_key["ETHUSDT_Long"].batch_id == 1
  assert by_key["SOLUSDT_Long"].batch_id is None

  # LiveEvent(rebalance_approved) emitted.
  ev = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == old_pid,
    LiveEvent.event_type == "rebalance_approved",
  ).all()
  assert len(ev) == 1
  details = json.loads(ev[0].details)
  assert details["proposal_id"] == prop.id
  assert details["old_portfolio_id"] == old_pid
  assert details["new_portfolio_id"] == new_pid
  assert details["added"] == ["SOLUSDT_Long"]
  assert details["removed"] == ["ADAUSDT_Long"]


def test_reject_sets_status_without_side_effects(db, monkeypatch):
  """Reject → status='rejected', no Portfolio or basket mutations."""
  oos_end = datetime(2025, 6, 1)
  pid = _make_portfolio(
    db, name="to_reject", oos_end=oos_end,
    basket=[("BTCUSDT", "long", 1), ("ETHUSDT", "long", 1)],
  )

  import backend.services.oos_boundary as ob
  monkeypatch.setattr(ob, "SessionLocal", _Session)
  _patch_worker_step1(monkeypatch, ["BTCUSDT_Long"])
  ob.check_and_enqueue(now=datetime(2025, 7, 15))

  prop = db.query(RebalanceProposal).filter(
    RebalanceProposal.portfolio_id == pid
  ).first()
  assert prop is not None

  # Snapshot portfolio BEFORE reject.
  p_before = db.query(Portfolio).filter(Portfolio.id == pid).first()
  before_active = p_before.is_active
  before_deactivated = p_before.deactivated_at

  from backend.api.routes.rebalance import RejectBody, reject_proposal
  resp = reject_proposal(
    proposal_id=prop.id,
    body=RejectBody(reason="too aggressive", reviewed_by="max"),
    db=db,
  )
  assert resp["ok"] is True

  db.refresh(prop)
  assert prop.status == "rejected"
  assert prop.reviewed_by == "max"
  assert prop.reviewed_at is not None
  assert prop.resulting_portfolio_id is None

  # Portfolio unchanged — this is the central no-auto-rotate guarantee.
  p_after = db.query(Portfolio).filter(Portfolio.id == pid).first()
  assert p_after.is_active == before_active
  assert p_after.deactivated_at == before_deactivated

  # No new Portfolio row created.
  assert db.query(Portfolio).count() == 1

  # LiveEvent(rebalance_rejected) emitted.
  ev = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == pid,
    LiveEvent.event_type == "rebalance_rejected",
  ).all()
  assert len(ev) == 1
  details = json.loads(ev[0].details)
  assert details["proposal_id"] == prop.id
  assert details["reason"] == "too aggressive"
  assert details["reviewed_by"] == "max"


def test_mixed_portfolios_only_past_boundary_enqueued(db, monkeypatch):
  """Two portfolios — one past oos_end, one inside. Only the past one fires."""
  pid_past = _make_portfolio(
    db, name="past_one", oos_end=datetime(2025, 6, 1),
    basket=[("BTCUSDT", "long", 1)],
  )
  pid_inside = _make_portfolio(
    db, name="inside_one", oos_end=datetime(2026, 6, 1),
    basket=[("ETHUSDT", "long", 1)],
  )

  import backend.services.oos_boundary as ob
  monkeypatch.setattr(ob, "SessionLocal", _Session)
  _patch_worker_step1(monkeypatch, ["BTCUSDT_Long", "SOLUSDT_Long"])

  report = ob.check_and_enqueue(now=datetime(2025, 7, 15))

  assert report["scanned"] == 2
  assert report["past_boundary"] == 1
  assert report["enqueued"] == 1
  assert report["skipped_pending"] == 0

  # Proposal only for the past-boundary portfolio.
  past_proposals = db.query(RebalanceProposal).filter(
    RebalanceProposal.portfolio_id == pid_past
  ).all()
  inside_proposals = db.query(RebalanceProposal).filter(
    RebalanceProposal.portfolio_id == pid_inside
  ).all()
  assert len(past_proposals) == 1
  assert len(inside_proposals) == 0


def test_approve_non_pending_returns_409(db, monkeypatch):
  """Approve on an already-approved/rejected proposal → HTTP 409."""
  from fastapi import HTTPException

  oos_end = datetime(2025, 6, 1)
  pid = _make_portfolio(
    db, name="double_approve", oos_end=oos_end,
    basket=[("BTCUSDT", "long", 1)],
  )

  import backend.services.oos_boundary as ob
  monkeypatch.setattr(ob, "SessionLocal", _Session)
  _patch_worker_step1(monkeypatch, ["BTCUSDT_Long"])
  ob.check_and_enqueue(now=datetime(2025, 7, 15))

  prop = db.query(RebalanceProposal).filter(
    RebalanceProposal.portfolio_id == pid
  ).first()
  # Manually flip to rejected to simulate prior review.
  prop.status = "rejected"
  db.commit()

  from backend.api.routes.rebalance import ApproveBody, approve_proposal
  with pytest.raises(HTTPException) as exc:
    approve_proposal(
      proposal_id=prop.id, body=ApproveBody(reviewed_by="max"), db=db,
    )
  assert exc.value.status_code == 409
  assert "not_pending" in str(exc.value.detail)
