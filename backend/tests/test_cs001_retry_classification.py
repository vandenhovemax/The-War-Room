"""CS-001: Bybit error classification must gate the ``place_sl_order``
retry loop. Permanent errors (insufficient balance, invalid qty) raise on
attempt 1 so the caller's emergency-close path fires immediately instead
of burning ~0.75s on hopeless retries. Transient errors (rate limit,
system busy) still retry up to 3 times.

The bug surface: ``place_sl_order`` previously caught bare ``Exception``
and retried everything 3x. With a position already open on the exchange
but its SL rejected for "insufficient balance", three blind retries delay
the emergency-close by ~0.75s — during which the market can move
adversely. Classifying retryable vs permanent shortens the exposure.

Tests pin:
1. ``is_retryable_bybit_error`` returns True for 10002, 10006, 10016,
   10018, 130035 and False for unlisted codes (110007, 110017, 110045).
2. ``is_retryable_bybit_error`` returns True for non-``BybitAPIError``
   exceptions (network / timeout surfaces as plain Exception from pybit).
3. ``place_sl_order`` raises on attempt 1 when the API returns a
   permanent error — call count on ``place_order`` == 1.
4. ``place_sl_order`` retries 3x on transient errors then raises.
5. ``place_sl_order`` retries 3x on network-class (non-Bybit) exceptions.
6. ``place_sl_order`` breaks out early on success — no extra retries
   after the first successful call.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, Portfolio, Position
from backend.exchange.bybit_trading import (
  BybitAPIError,
  _RETRYABLE_RET_CODES,
  is_retryable_bybit_error,
)
from backend.exchange.order_manager import OrderManager


# ── 1. is_retryable_bybit_error classification ───────────────────────

@pytest.mark.parametrize("code", [10002, 10006, 10016, 10018, 130035])
def test_retryable_codes_are_retryable(code):
  err = BybitAPIError(code, "transient")
  assert is_retryable_bybit_error(err) is True
  assert code in _RETRYABLE_RET_CODES


@pytest.mark.parametrize("code", [110007, 110017, 110045, 110001, 110025])
def test_permanent_codes_are_not_retryable(code):
  """110007 insufficient balance, 110017 invalid qty, 110045 position
  exceeded, 110001 invalid symbol, 110025 position idx mismatch — all
  raise on attempt 1.
  """
  err = BybitAPIError(code, "permanent")
  assert is_retryable_bybit_error(err) is False
  assert code not in _RETRYABLE_RET_CODES


def test_non_bybit_exception_is_retryable_by_default():
  """Network resets, timeouts, pybit internal exceptions — surface as
  plain Exception subclasses, not BybitAPIError. Treat as transient.
  """
  assert is_retryable_bybit_error(ConnectionError("network reset")) is True
  assert is_retryable_bybit_error(TimeoutError("read timed out")) is True
  assert is_retryable_bybit_error(RuntimeError("pybit internal")) is True


def test_retryable_set_is_frozen():
  """Guard against accidental runtime mutation — the retryable set must
  be a frozenset so `.add()` / `.remove()` can't silently reclassify
  errors at runtime.
  """
  with pytest.raises(AttributeError):
    _RETRYABLE_RET_CODES.add(110007)  # type: ignore[attr-defined]


# ── 2. place_sl_order retry-loop integration ─────────────────────────

@pytest.fixture
def db():
  engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
  )
  Base.metadata.create_all(engine)
  Session = sessionmaker(bind=engine)
  session = Session()
  yield session
  session.close()
  engine.dispose()


@pytest.fixture
def order_manager_with_position(db):
  """OrderManager wired to a MagicMock BybitTradingClient and a real
  in-memory DB session with a Portfolio + open Position.

  Returns (om, mock_client, position_id) so tests can configure
  ``mock_client.place_order.side_effect`` and assert call_count.
  """
  portfolio = Portfolio(id=1, name="test", portfolio_type="real", is_active=True)
  db.add(portfolio)
  db.commit()

  pos = Position(
    id=1, portfolio_id=1, symbol="BTCUSDT", side="Buy",
    entry_price=50000.0, quantity=0.01, is_open=True,
    exchange_symbol="BTCUSDT",
  )
  db.add(pos)
  db.commit()

  mock_client = MagicMock()
  mock_client.get_instrument_info.return_value = {
    "lot_size": 0.001, "tick_size": 0.1,
  }

  def _db_factory():
    return db

  om = OrderManager(
    trading_client=mock_client,
    db_factory=_db_factory,
  )
  return om, mock_client, pos.id


def test_permanent_error_returns_none_on_first_attempt(order_manager_with_position):
  """110007 insufficient balance — permanent. One call, then the outer
  ``except`` in ``place_sl_order`` converts to ``return None`` so the
  caller (``_on_entry_filled``) fires the emergency close immediately.
  Burning two more retries would delay that close by ~0.75s.
  """
  om, mock_client, pos_id = order_manager_with_position
  mock_client.place_order.side_effect = BybitAPIError(110007, "insufficient balance")

  result = om.place_sl_order(pos_id, "BTCUSDT", "Buy", 0.01, 48000.0)

  assert result is None, "permanent error must signal failure to caller"
  assert mock_client.place_order.call_count == 1, (
    f"CS-001: permanent error must NOT retry — got "
    f"{mock_client.place_order.call_count} calls, expected 1. "
    "Retrying wastes ~0.75s before the emergency close fires."
  )


def test_transient_error_retries_three_times(order_manager_with_position):
  """10006 rate-limit — transient. Three attempts, then give up (None)."""
  om, mock_client, pos_id = order_manager_with_position
  mock_client.place_order.side_effect = BybitAPIError(10006, "too many visits")

  result = om.place_sl_order(pos_id, "BTCUSDT", "Buy", 0.01, 48000.0)

  assert result is None
  assert mock_client.place_order.call_count == 3, (
    f"CS-001: transient error must retry 3x — got "
    f"{mock_client.place_order.call_count} calls, expected 3."
  )


def test_network_error_retries_three_times(order_manager_with_position):
  """ConnectionError from pybit — non-Bybit, treated as transient."""
  om, mock_client, pos_id = order_manager_with_position
  mock_client.place_order.side_effect = ConnectionError("network reset")

  result = om.place_sl_order(pos_id, "BTCUSDT", "Buy", 0.01, 48000.0)

  assert result is None
  assert mock_client.place_order.call_count == 3


def test_success_on_first_attempt_no_extra_calls(order_manager_with_position):
  """Happy path — one call, success, no retries."""
  om, mock_client, pos_id = order_manager_with_position
  mock_client.place_order.return_value = {"orderId": "abc123"}

  link_id = om.place_sl_order(pos_id, "BTCUSDT", "Buy", 0.01, 48000.0)

  assert mock_client.place_order.call_count == 1
  assert link_id is not None


def test_transient_then_success_stops_retrying(order_manager_with_position):
  """Rate-limit on attempt 1, success on attempt 2 — don't call a third time."""
  om, mock_client, pos_id = order_manager_with_position
  mock_client.place_order.side_effect = [
    BybitAPIError(10006, "too many visits"),
    {"orderId": "abc123"},
  ]

  link_id = om.place_sl_order(pos_id, "BTCUSDT", "Buy", 0.01, 48000.0)

  assert mock_client.place_order.call_count == 2
  assert link_id is not None


def test_mixed_permanent_after_transient_still_stops_on_permanent(order_manager_with_position):
  """Rate-limit on attempt 1 (retry), insufficient balance on attempt 2
  (permanent — must NOT attempt 3). This catches a regression where a
  later permanent error still gets the full retry budget.
  """
  om, mock_client, pos_id = order_manager_with_position
  mock_client.place_order.side_effect = [
    BybitAPIError(10006, "too many visits"),
    BybitAPIError(110007, "insufficient balance"),
  ]

  result = om.place_sl_order(pos_id, "BTCUSDT", "Buy", 0.01, 48000.0)

  assert result is None
  assert mock_client.place_order.call_count == 2, (
    "CS-001: on a transient-then-permanent sequence, stop at the "
    "permanent error (no attempt 3). Got "
    f"{mock_client.place_order.call_count} calls."
  )
