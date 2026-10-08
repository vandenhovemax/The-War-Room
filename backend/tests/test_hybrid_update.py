"""Ship 1 Test 5 — Hybrid portfolio nightly refresh updates basket + weights.

Purpose:
  Prove that `refresh_portfolio(pid)` on a mode='hybrid' portfolio:
    1. Writes a new KellyAllocation row with trigger='step2_nightly'
       and is_current=True.
    2. Flips all previous is_current rows for that portfolio to False.
    3. Flips PortfolioAsset.is_active so the executor only scans the
       newly-selected basket on the next cycle.
    4. Emits a LiveEvent(event_type='hybrid_nightly_refresh') for the
       Activity feed.
    5. Returns ok=True with added/removed/basket_size populated.

Scope (Ship 1):
  We monkey-patch `run_step2` so the test is unit-level: we are proving
  `hybrid_portfolio_refresh` glues Step-2 output to DB writes correctly,
  NOT re-testing Step-2's internal selection logic (that is what
  `test_step2_extraction_parity.py` guards).

Safety cross-check:
  A second non_hybrid portfolio in the same DB MUST remain untouched
  after the hybrid refresh runs (Safety Contract).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  KellyAllocation,
  LiveEvent,
  Portfolio,
  PortfolioAsset,
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

def _seed_basic_universe(db):
  """One strategy, two batches, four (symbol, side) runs each with 6 trades.

  Idempotent — skips when the universe already exists (module-scoped engine
  is shared across tests, so the first test to run seeds it).
  """
  if db.query(Strategy).filter(Strategy.id == 1).first() is not None:
    return
  strat = Strategy(id=1, name="TestStrat", source_type="manual")
  db.add(strat)
  db.flush()

  for bid in (1, 2):
    b = BatchBacktestRun(
      id=bid,
      strategy_id=1,
      timeframe="30",
      direction_filter="long_plus_short",
      status="completed",
      total_symbols=2,
      completed_symbols=2,
      parameters=json.dumps({"initial_capital": 10000}),
    )
    db.add(b)
  db.flush()

  start = datetime(2024, 1, 1)
  run_id = 0
  for bid in (1, 2):
    for sym in ("BTCUSDT", "ETHUSDT"):
      for side in ("long", "short"):
        run_id += 1
        r = BacktestRun(
          id=run_id, strategy_id=1, symbol=sym, timeframe="30",
          start_date=start, end_date=start + timedelta(days=180),
          status="completed", batch_id=bid,
        )
        db.add(r)
        db.flush()
        # 6 trades, mostly profitable so Step-2 has something to pick
        for i in range(6):
          entry = start + timedelta(days=10 * (i + 1))
          pnl = 100.0 if i % 2 == 0 else -30.0
          db.add(BacktestTrade(
            run_id=run_id, trade_num=i + 1, position_id=f"P{run_id}_{i}",
            symbol=sym, side=side,
            entry_time=entry, exit_time=entry + timedelta(hours=4),
            entry_price=1000.0, exit_price=1010.0, quantity=0.1,
            net_profit=pnl, net_profit_pct=pnl / 10.0,
            commission=0.1,
          ))
  db.commit()


def _make_hybrid_portfolio(db, name="h_test") -> int:
  # Contiguous rolling chain (W2.oos_start == W1.oos_end + 1 day) so the
  # anchored walk resolves its boundary: anchor = 2024-07-01.
  windows_meta = [
    {"window_num": 1, "is_start": "2024-01-01", "is_end": "2024-02-29",
     "oos_start": "2024-03-01", "oos_end": "2024-04-30"},
    {"window_num": 2, "is_start": "2024-03-01", "is_end": "2024-04-30",
     "oos_start": "2024-05-01", "oos_end": "2024-06-30"},
  ]
  p = Portfolio(
    name=name,
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    optimization_end_date=datetime(2024, 6, 1),
    source_strategies=json.dumps({
      "strategy_id": 1, "timeframe": "30",
      "hybrid_mode": True, "oos_months": 2,
      "hybrid_n_trades": 3, "hybrid_n_assets": 2,
      "hybrid_min_pf": 1.0, "hybrid_min_wr": 0.3,
      "hybrid_min_hold": 0, "hybrid_rebalance_every": 5,
      "hybrid_rebalance_mode": "global",
      "hybrid_scoring_method": "composite",
      "hybrid_objective": "sortino",
      "wfo_windows_meta": windows_meta,
    }),
  )
  db.add(p)
  db.flush()
  for sym, side, bid in [
    ("BTCUSDT", "long", 1),
    ("ETHUSDT", "long", 1),
    ("BTCUSDT", "short", 2),
    ("ETHUSDT", "short", 2),
  ]:
    db.add(PortfolioAsset(
      portfolio_id=p.id, symbol=sym, trade_side=side,
      batch_id=bid, is_active=True,
    ))
  db.commit()
  return p.id


def _make_non_hybrid_sibling(db) -> int:
  p = Portfolio(
    name="nh_sibling",
    portfolio_type="paper",
    mode="non_hybrid",
    is_active=True,
    optimization_end_date=datetime(2024, 6, 1),
    source_strategies=json.dumps({
      "strategy_id": 1, "timeframe": "30", "hybrid_mode": False,
    }),
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="long",
    batch_id=1, is_active=True,
  ))
  db.commit()
  return p.id


# ── Fake Step-2 output (matches contract from test_step2_extraction_parity) ──

def _fake_step2_result(selected_basket: list[str]) -> dict:
  """Minimal Step-2 output: just the fields refresh_portfolio actually reads."""
  return {
    "mode": "event",
    "walk_forward": True,
    "selection_changes": [
      {
        "window": 1,
        "added": selected_basket,
        "removed": [],
      },
    ],
    "asset_frequency": {
      a: {"selected": 1, "eligible": 1, "pct": 100.0}
      for a in selected_basket
    },
    "per_window": [{"window_num": 1}],
    "winning_batches": [1, 2],
    "summary": {"total_trades": 0, "net_profit": 0.0},
    "step2_config": {},
    "config_used": {},
  }


# ── Tests ─────────────────────────────────────────────────────────

def test_hybrid_refresh_writes_kelly_and_event(db, monkeypatch):
  """Happy path — run refresh, check every write."""
  _seed_basic_universe(db)
  pid = _make_hybrid_portfolio(db)

  # Step-2 selects 2 of 4 basket assets — this forces is_active flips.
  new_basket = [
    "B1_BTCUSDT_long",   # kept active
    "B2_ETHUSDT_short",  # kept active
  ]
  # Dropped: B1_ETHUSDT_long, B2_BTCUSDT_short

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(svc, "run_step2", lambda **kw: _fake_step2_result(new_basket))

  result = svc.refresh_portfolio(pid)

  # ── Return-shape contract ──
  assert result["ok"] is True, result
  assert result["basket_size"] == 2
  assert result["weights_written"] == 2
  # The new basket is a strict subset of the previously-active basket:
  # "added" = new − current (empty, nothing brand-new entered).
  # "removed" = current − new (the two assets Step-2 dropped).
  assert set(result["added"]) == set()
  assert set(result["removed"]) == {"B1_ETHUSDT_long", "B2_BTCUSDT_short"}

  # ── KellyAllocation ──
  rows = (
    db.query(KellyAllocation)
    .filter(KellyAllocation.portfolio_id == pid)
    .order_by(KellyAllocation.id)
    .all()
  )
  assert len(rows) == 1, "exactly one KellyAllocation row should exist"
  row = rows[0]
  assert row.is_current is True
  assert row.trigger == "step2_refresh"
  weights = json.loads(row.weights_json)
  assert set(weights.keys()) == set(new_basket)
  # Uniform 1/N fallback (Decision Point #1): weights sum to ~1.0
  assert abs(sum(weights.values()) - 1.0) < 1e-9
  for w in weights.values():
    assert w == pytest.approx(0.5)

  # ── LiveEvent emitted ──
  events = (
    db.query(LiveEvent)
    .filter(
      LiveEvent.portfolio_id == pid,
      LiveEvent.event_type == "hybrid_refresh",
    )
    .all()
  )
  assert len(events) == 1
  details = json.loads(events[0].details)
  assert details["weights_count"] == 2
  assert "added" in details and "removed" in details

  # ── PortfolioAsset.is_active reflects new basket ──
  assets = (
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == pid)
    .all()
  )
  for pa in assets:
    key = f"B{pa.batch_id}_{pa.symbol}_{pa.trade_side}"
    expected_active = key in set(new_basket)
    assert pa.is_active is expected_active, (
      f"PortfolioAsset({key}) is_active={pa.is_active}, "
      f"expected {expected_active}"
    )


def test_hybrid_refresh_marks_prior_kelly_not_current(db, monkeypatch):
  """Running refresh twice should leave exactly one is_current=True row."""
  _seed_basic_universe(db)
  pid = _make_hybrid_portfolio(db)

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  monkeypatch.setattr(
    svc, "run_step2",
    lambda **kw: _fake_step2_result(["B1_BTCUSDT_long", "B1_ETHUSDT_long"]),
  )
  assert svc.refresh_portfolio(pid)["ok"] is True

  monkeypatch.setattr(
    svc, "run_step2",
    lambda **kw: _fake_step2_result(["B2_BTCUSDT_short", "B2_ETHUSDT_short"]),
  )
  assert svc.refresh_portfolio(pid)["ok"] is True

  rows = (
    db.query(KellyAllocation)
    .filter(KellyAllocation.portfolio_id == pid)
    .all()
  )
  assert len(rows) == 2, "two refreshes should leave two rows"
  current = [r for r in rows if r.is_current is True]
  assert len(current) == 1, "only the latest row should be is_current=True"
  # The current row's weights must reflect the second refresh
  weights = json.loads(current[0].weights_json)
  assert set(weights.keys()) == {"B2_BTCUSDT_short", "B2_ETHUSDT_short"}


def test_hybrid_refresh_does_not_touch_non_hybrid_sibling(db, monkeypatch):
  """Running refresh_all on a mixed DB leaves non_hybrid rows byte-identical.

  Cross-check against Test 4 — belt-and-suspenders against the Safety
  Contract.
  """
  _seed_basic_universe(db)
  hpid = _make_hybrid_portfolio(db)
  nhpid = _make_non_hybrid_sibling(db)

  # Snapshot non_hybrid state
  nh_assets_before = [
    (a.symbol, a.trade_side, a.batch_id, a.is_active)
    for a in db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == nhpid)
    .order_by(PortfolioAsset.id)
    .all()
  ]
  nh_kelly_before = db.query(KellyAllocation).filter(
    KellyAllocation.portfolio_id == nhpid,
  ).count()
  nh_events_before = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == nhpid,
  ).count()

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(
    svc, "run_step2",
    lambda **kw: _fake_step2_result(["B1_BTCUSDT_long"]),
  )

  report = svc.refresh_all_hybrid_portfolios()
  # Other tests in this module share the same in-memory DB, so prior-test
  # hybrids may also be processed here. The Safety Contract is what we're
  # proving: the non_hybrid sibling MUST NOT appear in results, and the
  # specific hybrid we created here MUST have been refreshed.
  assert any(r.get("ok") and r.get("portfolio_id") == hpid
             for r in report["results"]), (
    "the hybrid we created was not refreshed"
  )
  assert all(r.get("portfolio_id") != nhpid for r in report["results"]), (
    "non_hybrid sibling appeared in hybrid refresh results — "
    "Safety Contract violation at the query boundary."
  )

  nh_assets_after = [
    (a.symbol, a.trade_side, a.batch_id, a.is_active)
    for a in db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == nhpid)
    .order_by(PortfolioAsset.id)
    .all()
  ]
  nh_kelly_after = db.query(KellyAllocation).filter(
    KellyAllocation.portfolio_id == nhpid,
  ).count()
  nh_events_after = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == nhpid,
  ).count()

  assert nh_assets_before == nh_assets_after, "non_hybrid assets mutated"
  assert nh_kelly_before == nh_kelly_after, "non_hybrid Kelly mutated"
  assert nh_events_before == nh_events_after, "non_hybrid event feed mutated"


def test_hybrid_refresh_empty_basket_returns_reason(db, monkeypatch):
  """When Step-2 returns no basket, refresh must refuse to flip anything.

  Anchored-walk contract: empty basket → ok=False with
  reason='step2_picked_empty', no KellyAllocation written, live basket
  preserved (defensive — see module docstring Safety section).
  """
  _seed_basic_universe(db)
  pid = _make_hybrid_portfolio(db)

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(svc, "run_step2", lambda **kw: _fake_step2_result([]))

  result = svc.refresh_portfolio(pid)
  assert result["ok"] is False
  assert result["reason"] == "step2_picked_empty"
  # No KellyAllocation row written
  assert db.query(KellyAllocation).filter(
    KellyAllocation.portfolio_id == pid,
  ).count() == 0
  # Live basket untouched — every asset stays active
  assets = db.query(PortfolioAsset).filter(
    PortfolioAsset.portfolio_id == pid,
  ).all()
  assert all(a.is_active for a in assets)


def test_hybrid_refresh_dispatches_executor_refresh(db, monkeypatch):
  """Regression — nightly refresh must schedule engine.refresh_portfolio(pid)
  onto the engine's event loop so the live executor rebuilds active.assets
  and WS subscriptions. Without this, the executor keeps trading yesterday's
  basket until /live/refresh is hit manually.
  """
  import sys
  import types as _types

  _seed_basic_universe(db)
  pid = _make_hybrid_portfolio(db)

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(
    svc, "run_step2",
    lambda **kw: _fake_step2_result(["B1_BTCUSDT_long", "B2_ETHUSDT_short"]),
  )

  class _FakeLoop:
    def is_running(self): return True

  class _FakeEngine:
    _loop = _FakeLoop()
    def __init__(self):
      self.kelly_calls = []
      self.schedule_calls = []
      self.refresh_pid = []
    def refresh_kelly_cache(self, p):
      self.kelly_calls.append(p); return True
    async def refresh_portfolio(self, p):
      self.refresh_pid.append(p)
      return {"added": [], "removed": [], "kept": []}
    def _schedule(self, coro):
      self.schedule_calls.append(coro)

  fake = _FakeEngine()
  fake_mod = _types.ModuleType("backend.exchange.engine_coordinator")
  fake_mod.get_engine = lambda: fake
  monkeypatch.setitem(sys.modules, "backend.exchange.engine_coordinator", fake_mod)

  result = svc.refresh_portfolio(pid)
  assert result["ok"] is True, result

  # Kelly cache still kicked (unchanged behaviour)
  assert fake.kelly_calls == [pid]
  # THE FIX: refresh_portfolio(pid) was scheduled onto the engine loop
  assert len(fake.schedule_calls) == 1
  coro = fake.schedule_calls[0]
  import asyncio
  assert asyncio.iscoroutine(coro)
  # Awaiting proves the coroutine IS engine.refresh_portfolio(pid)
  loop = asyncio.new_event_loop()
  try:
    outcome = loop.run_until_complete(coro)
  finally:
    loop.close()
  assert outcome == {"added": [], "removed": [], "kept": []}
  assert fake.refresh_pid == [pid]


def test_hybrid_refresh_skips_dispatch_when_engine_loop_down(db, monkeypatch):
  """If the engine's event loop isn't running (e.g. engine mid-restart),
  the refresh must skip the dispatch cleanly — no coroutine leak, no crash.
  """
  import sys
  import types as _types

  _seed_basic_universe(db)
  pid = _make_hybrid_portfolio(db)

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(
    svc, "run_step2",
    lambda **kw: _fake_step2_result(["B1_BTCUSDT_long", "B2_ETHUSDT_short"]),
  )

  class _DownLoop:
    def is_running(self): return False

  class _EngineLoopDown:
    _loop = _DownLoop()
    def refresh_kelly_cache(self, p): return True
    async def refresh_portfolio(self, p):
      return {"added": [], "removed": [], "kept": []}
    def _schedule(self, coro):
      raise AssertionError("must not dispatch when loop is down")

  fake_mod = _types.ModuleType("backend.exchange.engine_coordinator")
  fake_mod.get_engine = lambda: _EngineLoopDown()
  monkeypatch.setitem(sys.modules, "backend.exchange.engine_coordinator", fake_mod)

  result = svc.refresh_portfolio(pid)
  assert result["ok"] is True
