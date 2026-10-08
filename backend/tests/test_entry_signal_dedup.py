"""Entry signal de-duplication guards (double-fill prevention).

Before flipping martingale on real money, we need proof that a bursty or
retried entry signal cannot cause two orders to land on the exchange.
The coordinator has two guards that work together:

  1. _open_position_keys — "there is already a Position row for this
     (portfolio, symbol, side)"; blocks any signal at all.
  2. _in_flight_entries  — "we've already submitted an entry for this
     (portfolio, symbol, side) in the last 120s"; blocks duplicates
     while the order is pending fill.

These tests pin both guards so a refactor cannot remove them silently.

A third, exchange-level guard is a UUID4 `orderLinkId` sent on every
order — Bybit rejects duplicates within a 24h window. That one is a
bybit_trading.py invariant and is covered by the place_order tests.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, Portfolio
from backend.exchange.strategy_executor import StrategySignal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_factory():
  eng = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
  )
  Base.metadata.create_all(eng)
  return sessionmaker(bind=eng)


def _make_portfolio(db) -> int:
  profile = {
    "override_enabled": True,
    "mode": "fixed",
    "initial_risk_usd": 100.0,
  }
  p = Portfolio(
    name="dedup_test",
    portfolio_type="real",
    source_strategies=json.dumps({"risk_profile": profile}),
  )
  db.add(p)
  db.commit()
  db.refresh(p)
  return int(p.id)


# ---------------------------------------------------------------------------
# Test doubles (same shape as the e2e test — these NEVER hit the exchange)
# ---------------------------------------------------------------------------

class _CapturingPositionManager:
  def __init__(self):
    self.calc_calls: list[dict] = []
    self.prepare_calls: list[tuple[str, int]] = []

  def calculate_position_size(self, **kwargs) -> dict:
    self.calc_calls.append(kwargs)
    sl_distance = abs(kwargs["entry_price"] - kwargs["sl_price"])
    qty = kwargs["risk_amount"] / sl_distance
    return {
      "qty": qty,
      "notional": qty * kwargs["entry_price"],
      "leverage": 1,
      "risk_amount": kwargs["risk_amount"],
      "sl_distance": sl_distance,
      "capped": False, "desired_qty": qty, "max_leverage": 100.0,
      "margin_budget": 10_000.0, "total_slots": 10,
      "wallet_balance": 10_000.0, "available_balance": 10_000.0,
    }

  def prepare_symbol(self, symbol: str, leverage: int) -> None:
    self.prepare_calls.append((symbol, leverage))


class _CapturingOrderManager:
  def __init__(self):
    self.submit_calls: list[dict] = []

  def submit_entry_order(self, **kwargs) -> "_Dummy":
    self.submit_calls.append(kwargs)
    return _Dummy()


class _Dummy:
  def close(self) -> None: pass
  def __await__(self):
    if False:
      yield
    return None


def _make_coordinator(db_factory, pm, om):
  from backend.exchange.engine_coordinator import EngineCoordinator
  settings = SimpleNamespace(
    paper_trading=False,
    risk_per_trade_usd=100.0, risk_per_trade_pct=1.0, risk_mode="fixed",
    max_positions_total=20, max_position_notional=0,
    skip_capped_entries=False,
    entry_order_type="Market", entry_limit_offset_pct=0.0,
  )
  coord = EngineCoordinator.__new__(EngineCoordinator)
  coord._db_factory = db_factory
  coord._settings = settings
  coord._position_manager = pm
  coord._order_manager = om
  coord._safety_monitor = None
  coord._candle_aggregator = None
  coord._strategy_executor = None
  coord._open_position_keys = set()
  coord._in_flight_entries = {}
  coord._kelly_alloc_cache = {}
  coord._loop = None
  coord._schedule = lambda coro: (coro.close() if coro is not None and hasattr(coro, "close") else None)
  coord._log_event = lambda *a, **k: None
  coord._get_position_margin_cap = lambda *a, **k: None
  coord._get_portfolio_max_position_pct = lambda *a, **k: None
  return coord


def _signal(pid: int, symbol: str = "BTCUSDT") -> StrategySignal:
  return StrategySignal(
    portfolio_id=pid, symbol=symbol, action="entry", direction="long",
    entry_id="LONG1", qty=1.0, sl_price=99.0, tp_price=0.0,
    trail_points=0.0, trail_offset=0.0, limit_price=100.0, comment="test",
  )


# ---------------------------------------------------------------------------
# 1. In-flight dedup — duplicate signal within 120s is blocked
# ---------------------------------------------------------------------------

def test_duplicate_signal_within_window_is_blocked(db_factory):
  """Second signal same (portfolio, symbol, side) within 120s: no 2nd order."""
  db = db_factory()
  try:
    pid = _make_portfolio(db)
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  coord._handle_entry_signal(_signal(pid))
  coord._handle_entry_signal(_signal(pid))  # duplicate — should be dropped

  assert len(om.submit_calls) == 1, (
    f"Duplicate signal was NOT deduped — {len(om.submit_calls)} orders sent. "
    f"The in-flight cache guard regressed."
  )


# ---------------------------------------------------------------------------
# 2. In-flight dedup — different symbol is NOT blocked
# ---------------------------------------------------------------------------

def test_different_symbol_is_not_blocked(db_factory):
  db = db_factory()
  try:
    pid = _make_portfolio(db)
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  coord._handle_entry_signal(_signal(pid, symbol="BTCUSDT"))
  coord._handle_entry_signal(_signal(pid, symbol="ETHUSDT"))

  assert len(om.submit_calls) == 2, (
    "Dedup is too aggressive — it blocked a different symbol."
  )


# ---------------------------------------------------------------------------
# 3. In-flight dedup — different side is NOT blocked
# ---------------------------------------------------------------------------

def test_different_side_is_not_blocked(db_factory):
  db = db_factory()
  try:
    pid = _make_portfolio(db)
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  long_sig = _signal(pid, symbol="BTCUSDT")
  short_sig = StrategySignal(
    portfolio_id=pid, symbol="BTCUSDT", action="entry", direction="short",
    entry_id="SHORT1", qty=1.0, sl_price=101.0, tp_price=0.0,
    trail_points=0.0, trail_offset=0.0, limit_price=100.0, comment="test",
  )
  coord._handle_entry_signal(long_sig)
  coord._handle_entry_signal(short_sig)

  assert len(om.submit_calls) == 2


# ---------------------------------------------------------------------------
# 4. In-flight dedup — stale entry (> 120s old) is purged
# ---------------------------------------------------------------------------

def test_stale_in_flight_entry_is_purged(db_factory, monkeypatch):
  """After 120s the flight cache entry is cleared; a new signal goes through.

  This is the KNOWN GAP: if Bybit accepted the first order but we never saw
  the ACK, a fresh signal > 120s later can place a second order before the
  reconciliation loop catches the phantom. See Notion bug 'entry dedup
  window shorter than reconciliation cadence'. The test pins current
  behavior so any intended tightening is a visible code change.
  """
  db = db_factory()
  try:
    pid = _make_portfolio(db)
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  # Plant a "ghost" in-flight entry 200s ago (beyond 120s TTL)
  flight_key = f"{pid}_BTCUSDT_Buy"
  coord._in_flight_entries[flight_key] = time.time() - 200.0

  coord._handle_entry_signal(_signal(pid))

  assert len(om.submit_calls) == 1, (
    "Stale in-flight entry should have been purged, allowing the fresh "
    "signal through. This test pins the TTL sweep in _handle_entry_signal."
  )
  # And the stale key was cleared (then replaced with a fresh timestamp)
  assert coord._in_flight_entries[flight_key] > time.time() - 5


# ---------------------------------------------------------------------------
# 5. Open-position-key dedup — once a position exists, signal is blocked
# ---------------------------------------------------------------------------

def test_existing_position_key_blocks_entry(db_factory):
  """Even if the flight cache is empty (process restarted), an existing
  position on the same (portfolio, symbol, side) blocks re-entry."""
  db = db_factory()
  try:
    pid = _make_portfolio(db)
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  # Simulate the state after fill: position key registered
  coord._open_position_keys.add(f"{pid}_BTCUSDT_Buy")

  coord._handle_entry_signal(_signal(pid))

  assert len(om.submit_calls) == 0, (
    "An entry signal landed an order even though an open position already "
    "exists for this portfolio+symbol+side — the double-fill guard broke."
  )


# ---------------------------------------------------------------------------
# 6. Invalid SL direction is rejected before any order is built
# ---------------------------------------------------------------------------

def test_invalid_sl_direction_blocks_entry_buy(db_factory):
  """Buy with SL >= entry price must be rejected (Bug 37 guard)."""
  db = db_factory()
  try:
    pid = _make_portfolio(db)
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  # Build a signal with SL on the WRONG side of entry (invalid)
  bad = StrategySignal(
    portfolio_id=pid, symbol="BTCUSDT", action="entry", direction="long",
    entry_id="LONG1", qty=1.0, sl_price=101.0,  # SL > entry (invalid for Buy)
    tp_price=0.0, trail_points=0.0, trail_offset=0.0,
    limit_price=100.0, comment="test",
  )
  coord._handle_entry_signal(bad)

  assert len(om.submit_calls) == 0, (
    "Entry with SL on wrong side of entry should have been blocked."
  )


def test_missing_sl_blocks_entry(db_factory):
  """sl_price <= 0 must block entry — live trading without SL is prohibited."""
  db = db_factory()
  try:
    pid = _make_portfolio(db)
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  no_sl = StrategySignal(
    portfolio_id=pid, symbol="BTCUSDT", action="entry", direction="long",
    entry_id="LONG1", qty=1.0, sl_price=0.0, tp_price=0.0,
    trail_points=0.0, trail_offset=0.0, limit_price=100.0, comment="test",
  )
  coord._handle_entry_signal(no_sl)

  assert len(om.submit_calls) == 0
