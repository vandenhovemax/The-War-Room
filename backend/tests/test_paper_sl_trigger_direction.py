"""Regression: PaperTradingClient.place_order must accept trigger_direction.

Bug found 2026-05-23 (introduced ~2026-05-21): order_manager.place_sl_order
passes trigger_direction=1|2 on every SL placement (Bybit V5 requires it),
but PaperTradingClient.place_order didn't accept the kwarg. Every paper
SL raised TypeError → 3 retries → safety net market-closed the position
immediately (≈2× slippage loss, 0m duration). 7 of 8 paper positions
opened between 2026-05-21 and 2026-05-23 died this way; the only survivor
was on real Bybit (portfolio 49) which uses BybitTradingClient.

Locks two invariants:
  1. PaperTradingClient.place_order accepts trigger_direction without error.
  2. The kwarg is stored on the order dict for parity with Bybit rows.
"""
from __future__ import annotations

import inspect

from backend.exchange.paper_trading import PaperTradingClient


def test_place_order_accepts_trigger_direction_kwarg():
  """Signature-level lock: the parameter must exist."""
  sig = inspect.signature(PaperTradingClient.place_order)
  assert "trigger_direction" in sig.parameters, (
    "REGRESSION: PaperTradingClient.place_order has no trigger_direction "
    "param. order_manager.place_sl_order passes it on every SL — without "
    "this kwarg, every paper SL raises TypeError → safety net force-closes "
    "the position in <2s. 7/8 paper positions died this way between "
    "2026-05-21 and 2026-05-23. Real Bybit is unaffected (its client "
    "accepts the kwarg)."
  )


def test_paper_sl_placement_does_not_raise():
  """Behavioural: mimic order_manager.place_sl_order's exact call shape."""
  client = PaperTradingClient(initial_balance=10_000.0, testnet=True)
  # Seed cache so get_instrument_info is never called (no network in test)
  client._instruments["MINAUSDT"] = {
    "symbol": "MINAUSDT",
    "tick_size": 0.00001,
    "lot_size": 0.1,
    "min_qty": 0.1,
    "max_qty": 1_000_000.0,
    "max_leverage": 100.0,
    "min_leverage": 1.0,
    "funding_interval": 480,
  }

  # Exact kwargs that order_manager.place_sl_order uses (see line 316-325).
  result = client.place_order(
    symbol="MINAUSDT",
    side="Buy",  # SL for a short position
    order_type="Market",
    qty="11261.2",
    trigger_price="0.05947",
    trigger_by="LastPrice",
    reduce_only=True,
    order_link_id="test-sl-link-id",
    trigger_direction=1,  # 1 = trigger when price rises (short SL)
  )

  assert result.get("orderLinkId") == "test-sl-link-id"
  assert result.get("orderId", "").startswith("paper_")

  # The order should be in the open book (stop orders aren't auto-filled
  # because last_price isn't above trigger).
  stored = client._open_orders.get("test-sl-link-id")
  assert stored is not None, "SL order was not stored in open book"
  assert stored["triggerDirection"] == 1, (
    "trigger_direction was accepted but not stored on the order dict"
  )


def test_paper_entry_still_works_without_trigger_direction():
  """The kwarg is optional — entries (Market, no trigger) must still work."""
  client = PaperTradingClient(initial_balance=10_000.0, testnet=True)
  client._instruments["MINAUSDT"] = {
    "symbol": "MINAUSDT", "tick_size": 0.00001, "lot_size": 0.1,
    "min_qty": 0.1, "max_qty": 1_000_000.0,
    "max_leverage": 100.0, "min_leverage": 1.0, "funding_interval": 480,
  }
  result = client.place_order(
    symbol="MINAUSDT",
    side="Sell",
    order_type="Market",
    qty="4504.5",
    order_link_id="test-entry-link-id",
  )
  assert result.get("orderLinkId") == "test-entry-link-id"
