"""Reconciliation recovery guards.

Pins the current behavior of `PositionManager.reconcile_and_fix` so we
know exactly what happens on engine restart or ACK-loss:

  1. DB-only position (local thinks it's open, exchange doesn't know)
       → closed in DB. Exit orders on the exchange are cancelled.

  2. Qty mismatch (partial fill we missed, or manual adjustment)
       → local quantity is updated to match the exchange.

  3. Exchange-only position (exchange has it, local doesn't)
       → WARNING only. No Position row is created. Known gap —
         tracked in Notion: "Exchange-only positions warned but not
         adopted". Test pins current behavior so any fix is a visible
         code change with a test update.

The reconciliation loop runs every 60s in production (interval from
settings.reconciliation_interval_seconds) and is watched by the
candle_sync heartbeat. If it stops, /health flips to degraded and
Telegram alerts.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, Position
from backend.exchange.position_manager import PositionManager


# ---------------------------------------------------------------------------
# Fixtures — in-memory DB + fake Bybit client
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


class _FakeClient:
  """Minimal BybitTradingClient stand-in for reconciliation paths."""

  def __init__(self, positions: list[dict] | None = None):
    self._positions = positions or []
    self.cancel_calls: list[dict] = []
    self.execution_calls: int = 0

  def get_positions(self, symbol: str | None = None) -> list[dict]:
    if symbol is None:
      return list(self._positions)
    return [p for p in self._positions if p["symbol"] == symbol]

  def cancel_order(self, **kwargs) -> dict:
    self.cancel_calls.append(kwargs)
    return {"orderLinkId": kwargs.get("order_link_id", "")}

  def get_executions(self, limit: int = 100) -> list[dict]:
    self.execution_calls += 1
    return []  # No funding fees in these tests


def _make_pm(client: _FakeClient) -> PositionManager:
  # PositionManager.__init__ uses get_settings() internally — that's
  # a live Settings singleton but only used for wallet cache, not
  # reconciliation. Safe to instantiate directly.
  return PositionManager(client)


def _open_position(db, *, symbol: str = "BTCUSDT", side: str = "Buy",
                   qty: float = 1.0, portfolio_id: int = 1) -> int:
  pos = Position(
    portfolio_id=portfolio_id,
    symbol=symbol, side=side,
    strategy_name="test",
    entry_price=100.0, quantity=qty,
    leverage=1, is_open=True,
    opened_at=datetime.now(timezone.utc),
  )
  db.add(pos)
  db.commit()
  db.refresh(pos)
  return int(pos.id)


# ---------------------------------------------------------------------------
# 1. Matched position — no action
# ---------------------------------------------------------------------------

def test_matched_position_no_action(db_factory):
  db = db_factory()
  try:
    _open_position(db, symbol="BTCUSDT", side="Buy", qty=1.0)
    client = _FakeClient(positions=[
      {"symbol": "BTCUSDT", "side": "Buy", "size": 1.0, "entry_price": 100.0},
    ])
    pm = _make_pm(client)
    recon = pm.reconcile_positions(db)

    assert len(recon["matched"]) == 1
    assert recon["db_only"] == []
    assert recon["exchange_only"] == []
    assert recon["qty_mismatch"] == []
  finally:
    db.close()


# ---------------------------------------------------------------------------
# 2. DB-only position — closed on reconcile_and_fix
# ---------------------------------------------------------------------------

def test_db_only_position_is_closed(db_factory):
  """Local DB thinks position is open, exchange has no such position
  → reconcile_and_fix closes it in the DB.

  This is the restart-after-manual-close scenario: Max closed it from
  the Bybit UI, our engine comes back up with the stale row, recon
  cleans it up."""
  db = db_factory()
  try:
    pos_id = _open_position(db, symbol="BTCUSDT", side="Buy", qty=1.0)
    client = _FakeClient(positions=[])  # exchange has nothing
    pm = _make_pm(client)

    fixes = pm.reconcile_and_fix(db)

    assert fixes["closed_db_only"] == 1
    reloaded = db.get(Position, pos_id)
    assert reloaded.is_open is False
  finally:
    db.close()


# ---------------------------------------------------------------------------
# 3. Qty mismatch — DB is updated to match exchange
# ---------------------------------------------------------------------------

def test_qty_mismatch_updates_db_to_exchange(db_factory):
  """DB has qty=1.0, exchange shows qty=0.5 (partial fill we missed,
  or manual reduction). DB must be corrected to exchange truth."""
  db = db_factory()
  try:
    pos_id = _open_position(db, symbol="BTCUSDT", side="Buy", qty=1.0)
    client = _FakeClient(positions=[
      {"symbol": "BTCUSDT", "side": "Buy", "size": 0.5, "entry_price": 100.0},
    ])
    pm = _make_pm(client)

    fixes = pm.reconcile_and_fix(db)

    assert fixes["qty_fixed"] == 1
    reloaded = db.get(Position, pos_id)
    assert reloaded.quantity == pytest.approx(0.5, rel=1e-9)
    assert reloaded.is_open is True
  finally:
    db.close()


# ---------------------------------------------------------------------------
# 4. Exchange-only — WARNED, NOT adopted (pins current behavior)
# ---------------------------------------------------------------------------

def test_exchange_only_position_is_warned_but_not_adopted(db_factory):
  """When the exchange has a position but the DB doesn't, the current
  behavior is to log a warning and leave the position unmanaged.

  This test pins that behavior so the tracked Notion bug ("Exchange-only
  positions warned but not adopted") can be closed with a visible code
  change + test update rather than a silent fix.
  """
  db = db_factory()
  try:
    client = _FakeClient(positions=[
      {"symbol": "BTCUSDT", "side": "Buy", "size": 1.0, "entry_price": 100.0},
    ])
    pm = _make_pm(client)

    # Before: no Position rows in the DB at all
    assert db.query(Position).count() == 0

    fixes = pm.reconcile_and_fix(db)

    # Current behavior: warn counter incremented, but no Position row created
    assert fixes["exchange_only_warned"] == 1
    assert fixes["closed_db_only"] == 0
    assert fixes["qty_fixed"] == 0
    assert db.query(Position).count() == 0, (
      "reconcile_and_fix silently auto-created a Position row from exchange "
      "truth. If this is the intended fix for the Notion orphan-exposure bug, "
      "update the test to assert the new contract (fields, side, sl)."
    )
  finally:
    db.close()


# ---------------------------------------------------------------------------
# 5. Mix of all three discrepancies in one pass
# ---------------------------------------------------------------------------

def test_reconcile_handles_all_three_discrepancy_types(db_factory):
  """One reconciliation pass must handle matched + db_only + qty_mismatch
  + exchange_only all at once, without any of them interfering."""
  db = db_factory()
  try:
    # BTC: matched
    btc_id = _open_position(db, symbol="BTCUSDT", side="Buy", qty=1.0)
    # ETH: db_only (exchange has nothing)
    eth_id = _open_position(db, symbol="ETHUSDT", side="Buy", qty=2.0)
    # SOL: qty mismatch
    sol_id = _open_position(db, symbol="SOLUSDT", side="Buy", qty=10.0)

    client = _FakeClient(positions=[
      {"symbol": "BTCUSDT", "side": "Buy", "size": 1.0, "entry_price": 100.0},
      # ETH missing → db_only
      {"symbol": "SOLUSDT", "side": "Buy", "size": 5.0, "entry_price": 50.0},
      # Ghost exchange position — must be warned, not adopted
      {"symbol": "XRPUSDT", "side": "Sell", "size": 100.0, "entry_price": 0.5},
    ])
    pm = _make_pm(client)

    fixes = pm.reconcile_and_fix(db)

    assert fixes["closed_db_only"] == 1
    assert fixes["qty_fixed"] == 1
    assert fixes["exchange_only_warned"] == 1

    # Verify DB state
    assert db.get(Position, btc_id).is_open is True
    assert db.get(Position, btc_id).quantity == pytest.approx(1.0)
    assert db.get(Position, eth_id).is_open is False   # closed
    assert db.get(Position, sol_id).quantity == pytest.approx(5.0)

    # Ghost XRP was NOT adopted
    xrp = db.query(Position).filter(Position.symbol == "XRPUSDT").first()
    assert xrp is None, (
      "Ghost exchange position auto-adopted — update test if this is intended."
    )
  finally:
    db.close()


# ---------------------------------------------------------------------------
# 6. portfolio_id filter — only touch the target portfolio
# ---------------------------------------------------------------------------

def test_reconcile_honors_portfolio_id_filter(db_factory):
  """When portfolio_id is passed, only that portfolio's positions are
  reconciled. Positions from other portfolios must not be touched."""
  db = db_factory()
  try:
    target_pos = _open_position(
      db, portfolio_id=1, symbol="BTCUSDT", side="Buy", qty=1.0,
    )
    other_pos = _open_position(
      db, portfolio_id=2, symbol="ETHUSDT", side="Buy", qty=2.0,
    )

    client = _FakeClient(positions=[])  # Exchange has nothing

    pm = _make_pm(client)
    fixes = pm.reconcile_and_fix(db, portfolio_id=1)

    # Target portfolio's position closed
    assert fixes["closed_db_only"] == 1
    assert db.get(Position, target_pos).is_open is False
    # Other portfolio's position left alone
    assert db.get(Position, other_pos).is_open is True
  finally:
    db.close()
