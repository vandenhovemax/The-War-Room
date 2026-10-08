"""Live Loop catch-up test (Ship 3).

What this guards:
  The user may shut the backend down for days or weeks. When it comes
  back up, the scheduler will have missed its nightly jobs. The manual
  'catch-up' endpoint (POST /api/live-loop/catch-up) is the rescue path:
  it runs L1 → L2 → L3 synchronously so every batch is extended, every
  hybrid basket is refreshed, and every past-boundary portfolio gets a
  pending proposal in a single call.

  This test proves the orchestration layer in `backend.api.routes.live_loop`:
    1. `catch_up()` invokes L1, L2, L3 in order.
    2. Each layer's success/failure is captured in `result` or `error`.
    3. A thrown layer does NOT prevent later layers from running.
    4. `include_canary=True` adds L4 as the fourth layer.
    5. `run_one(layer)` dispatches to the right service by name (with
       aliases: 'l1'/'batch', 'l2'/'hybrid', 'l3'/'boundary'/'oos',
       'l4'/'canary'/'parity').
    6. `get_status()` returns the live counters the UI renders.

Strategy:
  Monkey-patch the four layer entry points on the `live_loop` module so
  the test never exercises the heavy backtest/engine paths. We already
  cover those individually in test_incremental_replay, test_hybrid_update,
  test_step1_rotation, test_parity_canary.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
  Base,
  BatchBacktestRun,
  ParityCanaryReport,
  Portfolio,
  RebalanceProposal,
  Strategy,
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
    s.query(ParityCanaryReport).delete()
    s.query(RebalanceProposal).delete()
    s.query(BatchBacktestRun).delete()
    s.query(Portfolio).delete()
    s.query(Strategy).delete()
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


# ── Tests ─────────────────────────────────────────────────────────

def test_catch_up_runs_three_layers_in_order(monkeypatch):
  """Happy path: L1, L2, L3 all succeed."""
  calls: list[str] = []

  from backend.services import (
    batch_store_refresh,
    hybrid_portfolio_refresh,
    oos_boundary,
  )
  monkeypatch.setattr(
    batch_store_refresh, "refresh_all_active_batches",
    lambda: (calls.append("l1"), {"total_new_trades": 42})[1],
  )
  monkeypatch.setattr(
    hybrid_portfolio_refresh, "refresh_all_hybrid_portfolios",
    lambda: (calls.append("l2"), {"refreshed": 3})[1],
  )
  monkeypatch.setattr(
    oos_boundary, "check_and_enqueue",
    lambda: (calls.append("l3"), {"enqueued": 1})[1],
  )

  from backend.api.routes.live_loop import catch_up
  resp = catch_up(include_canary=False)

  assert resp["ok"] is True
  assert calls == ["l1", "l2", "l3"]
  assert [l["layer"] for l in resp["layers"]] == [
    "l1_batch_store_refresh",
    "l2_hybrid_portfolio_refresh",
    "l3_oos_boundary",
  ]
  assert all(l["ok"] for l in resp["layers"])
  assert resp["layers"][0]["result"]["total_new_trades"] == 42
  assert resp["layers"][1]["result"]["refreshed"] == 3
  assert resp["layers"][2]["result"]["enqueued"] == 1


def test_catch_up_continues_when_one_layer_raises(monkeypatch):
  """L2 throws → L3 still runs; aggregate ok=False but L1+L3 show ok=True."""
  from backend.services import (
    batch_store_refresh,
    hybrid_portfolio_refresh,
    oos_boundary,
  )
  monkeypatch.setattr(
    batch_store_refresh, "refresh_all_active_batches",
    lambda: {"ok": True},
  )

  def _boom():
    raise RuntimeError("simulated L2 crash")
  monkeypatch.setattr(
    hybrid_portfolio_refresh, "refresh_all_hybrid_portfolios", _boom,
  )

  ran_l3 = []
  monkeypatch.setattr(
    oos_boundary, "check_and_enqueue",
    lambda: (ran_l3.append(True), {"enqueued": 0})[1],
  )

  from backend.api.routes.live_loop import catch_up
  resp = catch_up(include_canary=False)

  assert resp["ok"] is False
  assert ran_l3 == [True], "L3 must still run after L2 crashes"
  assert resp["layers"][0]["ok"] is True
  assert resp["layers"][1]["ok"] is False
  assert "simulated L2 crash" in resp["layers"][1]["error"]
  assert resp["layers"][2]["ok"] is True


def test_catch_up_include_canary_runs_l4(monkeypatch):
  """include_canary=True adds L4 as the 4th layer."""
  from backend.services import (
    batch_store_refresh,
    hybrid_portfolio_refresh,
    oos_boundary,
    parity_canary,
  )
  monkeypatch.setattr(batch_store_refresh, "refresh_all_active_batches", lambda: {})
  monkeypatch.setattr(hybrid_portfolio_refresh, "refresh_all_hybrid_portfolios", lambda: {})
  monkeypatch.setattr(oos_boundary, "check_and_enqueue", lambda: {})
  canary_ran = []
  monkeypatch.setattr(
    parity_canary, "run_parity_canary",
    lambda: (canary_ran.append(True), {"total_combos": 10, "combos_with_diff": 0})[1],
  )

  from backend.api.routes.live_loop import catch_up
  resp = catch_up(include_canary=True)

  assert len(resp["layers"]) == 4
  assert resp["layers"][3]["layer"] == "l4_parity_canary"
  assert canary_ran == [True]


def test_run_one_dispatches_to_right_layer(monkeypatch):
  """run_one('l2') invokes only L2."""
  calls: list[str] = []
  from backend.services import (
    batch_store_refresh,
    hybrid_portfolio_refresh,
    oos_boundary,
    parity_canary,
  )
  monkeypatch.setattr(batch_store_refresh, "refresh_all_active_batches",
                      lambda: calls.append("l1"))
  monkeypatch.setattr(hybrid_portfolio_refresh, "refresh_all_hybrid_portfolios",
                      lambda: calls.append("l2"))
  monkeypatch.setattr(oos_boundary, "check_and_enqueue",
                      lambda: calls.append("l3"))
  monkeypatch.setattr(parity_canary, "run_parity_canary",
                      lambda: calls.append("l4"))

  from backend.api.routes.live_loop import run_one
  run_one("l2")
  assert calls == ["l2"]

  run_one("hybrid")
  assert calls == ["l2", "l2"]

  run_one("l4")
  assert calls == ["l2", "l2", "l4"]

  run_one("canary")
  assert calls == ["l2", "l2", "l4", "l4"]


def test_run_one_rejects_unknown_layer():
  from backend.api.routes.live_loop import run_one
  with pytest.raises(HTTPException) as exc:
    run_one("l99")
  assert exc.value.status_code == 400
  assert "unknown layer" in str(exc.value.detail)


def test_status_reports_counts(db, monkeypatch):
  """get_status reflects the state of the DB."""
  # Seed: 2 active batches, 1 archived batch, 1 hybrid portfolio, 1 live portfolio,
  # 1 pending proposal, and a latest canary row.
  strat = Strategy(id=1, name="S", source_type="manual")
  db.add(strat)
  db.flush()
  db.add(BatchBacktestRun(id=1, strategy_id=1, timeframe="30", direction_filter="both",
                          parameters="{}", total_symbols=1, completed_symbols=1,
                          status="completed"))
  db.add(BatchBacktestRun(id=2, strategy_id=1, timeframe="30", direction_filter="both",
                          parameters="{}", total_symbols=1, completed_symbols=1,
                          status="completed"))
  db.add(BatchBacktestRun(id=3, strategy_id=1, timeframe="30", direction_filter="both",
                          parameters="{}", total_symbols=1, completed_symbols=1,
                          status="completed", is_archived=True))
  db.flush()
  hp = Portfolio(name="hp", portfolio_type="paper", mode="hybrid",
                 is_active=True, is_deleted=False)
  lp = Portfolio(name="lp", portfolio_type="real", mode="non_hybrid",
                 is_active=True, is_deleted=False)
  db.add(hp)
  db.add(lp)
  db.flush()
  db.add(RebalanceProposal(
    portfolio_id=lp.id, trigger_type="oos_boundary",
    status="pending", current_basket_json="[]",
    proposed_basket_json="[]", diff_json="{}",
  ))
  db.add(ParityCanaryReport(
    run_date="2026-04-01",
    total_combos=55, combos_with_diff=0, report_json="{}",
  ))
  db.commit()

  from backend.api.routes.live_loop import get_status
  status = get_status(db=db)

  assert status["active_batches"] == 2  # archived excluded
  assert status["active_live_portfolios"] == 2  # both are live
  assert status["hybrid_portfolios"] == 1
  assert status["pending_proposals"] == 1
  assert status["last_canary"] is not None
  assert status["last_canary"]["total_combos"] == 55
  assert status["last_canary"]["combos_with_diff"] == 0
  assert "l1_batch_store_refresh" in status["schedule"]
  assert "l4_parity_canary" in status["schedule"]
