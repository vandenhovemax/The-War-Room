"""End-to-end parity: martingale streak → order qty sent to exchange.

Closes the last gap in the martingale safety net:

  test_martingale_parity.py         — formula shape (static)
  test_martingale_runtime_parity.py — $risk out of _get_portfolio_risk
  test_martingale_e2e_order_qty.py  — qty reaching the exchange client  ← THIS

Path exercised:

    seeded loss trades
      → _get_portfolio_risk(..)          (runtime parity test covers this)
      → position_manager.calculate_position_size(risk_amount=..)
      → order_manager.submit_entry_order(qty=..)        ← asserted here

We stub position_manager + order_manager with capturing test doubles so
there is no network / no real exchange client. Everything else runs for
real: DB-backed streak count, the real coordinator's _handle_entry_signal
wiring, the real multiplication from $risk → lot-rounded qty.

If the chain from streak → order qty ever regresses (wrong field name,
wrong mode dispatch, risk_amount swallowed by an intermediate layer,
etc.), the qty arriving at the order_manager will diverge from
base_risk × factor / sl_distance and the test will fail loudly.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, Portfolio, Trade
from backend.exchange.strategy_executor import StrategySignal


# ---------------------------------------------------------------------------
# DB + portfolio/trade seeding (mirrors test_martingale_runtime_parity.py)
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


def _make_portfolio(
  db, name: str, *, mode: str, base_risk: float, multiplier: float,
  max_streak: int, scope: str = "asset",
) -> int:
  profile = {
    "override_enabled": True,
    "mode": mode,
    "initial_risk_usd": base_risk,
    "multiplier": multiplier,
    "max_streak": max_streak,
    "martingale_compound": "off",
    "martingale_scope": scope,
  }
  p = Portfolio(
    name=name,
    portfolio_type="real",
    source_strategies=json.dumps({"risk_profile": profile}),
  )
  db.add(p)
  db.commit()
  db.refresh(p)
  return int(p.id)


def _add_loss(db, pid: int, *, symbol: str = "BTCUSDT", hours_ago: int = 1) -> None:
  now = datetime.now(timezone.utc)
  db.add(Trade(
    portfolio_id=pid,
    symbol=symbol,
    side="Long",
    strategy_name="test",
    entry_price=100.0,
    exit_price=95.0,
    quantity=0.1,
    realized_pnl=-5.0,
    commission=0.0,
    opened_at=now - timedelta(hours=hours_ago, minutes=30),
    closed_at=now - timedelta(hours=hours_ago),
    close_reason="sl",
    is_paper=False,
  ))


# ---------------------------------------------------------------------------
# Capturing test doubles — record kwargs, return deterministic sizing
# ---------------------------------------------------------------------------

class _CapturingPositionManager:
  """Stand-in for PositionManager that records every calculate_position_size
  call and returns a deterministic sizing dict computed from risk_amount.

  No rounding, no min-qty clamp — we want the qty to equal the exact
  formula output so the test asserts the bridge math, not lot rules
  (those are covered elsewhere).
  """

  def __init__(self):
    self.calc_calls: list[dict] = []
    self.prepare_calls: list[tuple[str, int]] = []

  def calculate_position_size(
    self, *, symbol: str, side: str, entry_price: float, sl_price: float,
    risk_amount: float, current_open_count: int = 0,
    max_margin_override: float | None = None,
    max_position_pct_override: float | None = None,
  ) -> dict:
    self.calc_calls.append({
      "symbol": symbol, "side": side,
      "entry_price": entry_price, "sl_price": sl_price,
      "risk_amount": risk_amount,
    })
    sl_distance = abs(entry_price - sl_price)
    qty = risk_amount / sl_distance
    return {
      "qty": qty,
      "notional": qty * entry_price,
      "leverage": 1,
      "risk_amount": risk_amount,
      "sl_distance": sl_distance,
      "capped": False,
      "desired_qty": qty,
      "max_leverage": 100.0,
      "margin_budget": 10_000.0,
      "total_slots": 10,
      "wallet_balance": 10_000.0,
      "available_balance": 10_000.0,
    }

  def prepare_symbol(self, symbol: str, leverage: int) -> None:
    self.prepare_calls.append((symbol, leverage))


class _CapturingOrderManager:
  """Stand-in for OrderManager that records submit_entry_order kwargs."""

  def __init__(self):
    self.submit_calls: list[dict] = []

  def submit_entry_order(self, **kwargs) -> "_DummyAwaitable":
    self.submit_calls.append(kwargs)
    return _DummyAwaitable()


class _DummyAwaitable:
  """Dummy coroutine-like: closed immediately by our _schedule override."""

  def close(self) -> None:
    pass

  def __await__(self):
    if False:
      yield
    return None


# ---------------------------------------------------------------------------
# Coordinator stub — minimal shape to drive _handle_entry_signal
# ---------------------------------------------------------------------------

def _make_coordinator(db_factory, pm, om):
  from backend.exchange.engine_coordinator import EngineCoordinator
  settings = SimpleNamespace(
    paper_trading=False,
    risk_per_trade_usd=100.0,
    risk_per_trade_pct=1.0,
    risk_mode="fixed",
    max_positions_total=20,
    max_position_notional=0,
    skip_capped_entries=False,
    entry_order_type="Market",
    entry_limit_offset_pct=0.0,
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

  # _schedule normally dispatches to asyncio; here we just close the dummy
  # awaitable so no "coroutine was never awaited" warning appears.
  def _noop_schedule(coro):
    if coro is not None and hasattr(coro, "close"):
      coro.close()
  coord._schedule = _noop_schedule

  # _log_event writes to DB; stub it.
  coord._log_event = lambda *a, **k: None

  # Budget helpers — return None so defaults are used.
  coord._get_position_margin_cap = lambda *a, **k: None
  coord._get_portfolio_max_position_pct = lambda *a, **k: None

  return coord


def _make_signal(
  portfolio_id: int, symbol: str = "BTCUSDT", *,
  entry_price: float = 100.0, sl_price: float = 99.0,
) -> StrategySignal:
  return StrategySignal(
    portfolio_id=portfolio_id,
    symbol=symbol,
    action="entry",
    direction="long",
    entry_id="LONG1",
    qty=1.0,
    sl_price=sl_price,
    tp_price=0.0,
    trail_points=0.0,
    trail_offset=0.0,
    limit_price=entry_price,  # >0 so _handle_entry_signal uses this as entry
    comment="test",
  )


# ---------------------------------------------------------------------------
# 1. Baseline (no streak) — qty matches base / sl_distance
# ---------------------------------------------------------------------------

def test_zero_streak_sends_base_qty(db_factory):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "e2e_base", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    db.commit()
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  coord._handle_entry_signal(_make_signal(
    pid, entry_price=100.0, sl_price=99.0,
  ))

  assert len(pm.calc_calls) == 1
  assert pm.calc_calls[0]["risk_amount"] == pytest.approx(100.0)
  assert len(om.submit_calls) == 1
  # qty = 100 / |100-99| = 100
  assert om.submit_calls[0]["qty"] == pytest.approx(100.0)
  assert om.submit_calls[0]["portfolio_id"] == pid
  assert om.submit_calls[0]["symbol"] == "BTCUSDT"
  assert om.submit_calls[0]["side"] == "Buy"


# ---------------------------------------------------------------------------
# 2. Loss streak scales the qty sent to the exchange
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_losses,expected_factor", [
  (1, 2.0),
  (2, 4.0),
  (3, 8.0),   # at cap
  (5, 8.0),   # past cap — qty must NOT exceed max_streak growth
])
def test_loss_streak_scales_order_qty(db_factory, n_losses, expected_factor):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "e2e_mg", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    for i in range(n_losses):
      _add_loss(db, pid, hours_ago=i + 1)
    db.commit()
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  coord._handle_entry_signal(_make_signal(
    pid, entry_price=100.0, sl_price=99.0,
  ))

  assert len(pm.calc_calls) == 1, "calculate_position_size not called"
  assert len(om.submit_calls) == 1, "submit_entry_order not called"

  expected_risk = 100.0 * expected_factor
  expected_qty = expected_risk / 1.0  # sl_distance = 1

  assert pm.calc_calls[0]["risk_amount"] == pytest.approx(expected_risk, rel=1e-9), (
    f"wrong $risk passed to position sizer: got {pm.calc_calls[0]['risk_amount']}, "
    f"expected {expected_risk}"
  )
  assert om.submit_calls[0]["qty"] == pytest.approx(expected_qty, rel=1e-9), (
    f"wrong qty sent to order manager after {n_losses} losses: "
    f"got {om.submit_calls[0]['qty']}, expected {expected_qty} "
    f"(= $100 × {expected_factor} / sl=1)"
  )


# ---------------------------------------------------------------------------
# 3. SL distance other than 1 — guards against hard-coded risk=qty assumption
# ---------------------------------------------------------------------------

def test_sl_distance_divides_risk_correctly(db_factory):
  """With $400 risk (2 losses × mult 2) and sl_distance=$5, qty = 80."""
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "e2e_sl", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    for i in range(2):
      _add_loss(db, pid, hours_ago=i + 1)
    db.commit()
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  coord._handle_entry_signal(_make_signal(
    pid, entry_price=100.0, sl_price=95.0,
  ))

  # $400 risk / $5 sl = 80 units
  assert om.submit_calls[0]["qty"] == pytest.approx(80.0, rel=1e-9)


# ---------------------------------------------------------------------------
# 4. Paper losses must NOT inflate the LIVE order qty
# ---------------------------------------------------------------------------

def test_paper_losses_do_not_inflate_live_order_qty(db_factory):
  """Regression guard: if the is_paper filter regresses, the live exchange
  would receive an 8×-size order on a fresh live account with 3 paper losses."""
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "e2e_paper_live", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    now = datetime.now(timezone.utc)
    # 3 PAPER losses — live coordinator must ignore these
    for i in range(3):
      db.add(Trade(
        portfolio_id=pid,
        symbol="BTCUSDT",
        side="Long",
        strategy_name="test",
        entry_price=100.0, exit_price=95.0,
        quantity=0.1, realized_pnl=-5.0, commission=0.0,
        opened_at=now - timedelta(hours=i + 1, minutes=30),
        closed_at=now - timedelta(hours=i + 1),
        close_reason="sl", is_paper=True,
      ))
    db.commit()
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)  # live by default

  coord._handle_entry_signal(_make_signal(
    pid, entry_price=100.0, sl_price=99.0,
  ))

  # Live coordinator sees ZERO live losses → base risk → base qty (100)
  assert om.submit_calls[0]["qty"] == pytest.approx(100.0, rel=1e-9), (
    f"Paper losses leaked into LIVE order qty! got {om.submit_calls[0]['qty']}, "
    f"expected 100 (base). Paper/live isolation regressed."
  )


# ---------------------------------------------------------------------------
# 5. Asset-scope streak — BTC losses don't scale an ETH order
# ---------------------------------------------------------------------------

def test_asset_scope_isolates_order_qty_per_symbol(db_factory):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "e2e_scope", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3, scope="asset",
    )
    # 3 BTC losses
    for i in range(3):
      _add_loss(db, pid, symbol="BTCUSDT", hours_ago=i + 1)
    db.commit()
  finally:
    db.close()

  pm, om = _CapturingPositionManager(), _CapturingOrderManager()
  coord = _make_coordinator(db_factory, pm, om)

  # Fire a signal on ETH — a different symbol, so streak should not apply
  coord._handle_entry_signal(_make_signal(
    pid, symbol="ETHUSDT", entry_price=100.0, sl_price=99.0,
  ))

  # ETH has zero losses on its own asset scope → base qty = 100
  assert om.submit_calls[0]["qty"] == pytest.approx(100.0, rel=1e-9), (
    f"BTC losses leaked into ETH order qty under asset-scope! "
    f"got {om.submit_calls[0]['qty']}, expected 100."
  )
