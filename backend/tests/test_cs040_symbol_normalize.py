"""CS-040 regression: strip TradingView's ``.P`` perpetual suffix at the
Bybit trading-client boundary.

If ``BTCUSDT.P`` leaks through (alert, saved config, typo), Bybit's V5
API rejects it with ``110001 Invalid symbol`` and the live order never
places. Normalize once at the client entry point so every downstream
call (place_order, cancel, get_positions, set_leverage, etc.) sees the
bare symbol.

These tests pin the helper contract and sample a few ingress sites.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from backend.exchange.bybit_trading import BybitTradingClient, _normalize_symbol


# ── Helper contract ──────────────────────────────────────────────────

def test_strips_P_suffix():
  assert _normalize_symbol("BTCUSDT.P") == "BTCUSDT"


def test_passes_through_bare_symbol():
  assert _normalize_symbol("BTCUSDT") == "BTCUSDT"


def test_passes_through_none():
  """Optional-symbol endpoints (cancel_all_orders) accept None."""
  assert _normalize_symbol(None) is None


def test_does_not_strip_lowercase_or_mid_string():
  # Only trailing exact ".P" is stripped.
  assert _normalize_symbol("BTCUSDT.p") == "BTCUSDT.p"
  assert _normalize_symbol("AP.USDT") == "AP.USDT"


def test_warns_on_strip(caplog):
  caplog.set_level(logging.WARNING, logger="backend.exchange.bybit_trading")
  _normalize_symbol("ETHUSDT.P")
  assert any("stripped .P suffix" in r.message for r in caplog.records)
  assert any("ETHUSDT" in r.message for r in caplog.records)


def test_no_warning_on_bare_symbol(caplog):
  caplog.set_level(logging.WARNING, logger="backend.exchange.bybit_trading")
  _normalize_symbol("BTCUSDT")
  assert not any("stripped" in r.message for r in caplog.records)


# ── Ingress wiring: a few representative methods ─────────────────────

def _make_client() -> BybitTradingClient:
  """Construct a client without hitting the network."""
  with patch("backend.exchange.bybit_trading.BybitHTTP") as mock_http:
    mock_http.return_value = MagicMock()
    return BybitTradingClient(api_key="k", api_secret="s", testnet=True)


def test_place_order_strips_P():
  client = _make_client()
  mock_resp = {"retCode": 0, "retMsg": "OK", "result": {"orderId": "x", "orderLinkId": "y"}}
  client._session.place_order = MagicMock(return_value=mock_resp)
  client.place_order(
    symbol="BTCUSDT.P", side="Buy", order_type="Market", qty="0.01",
  )
  kwargs = client._session.place_order.call_args.kwargs
  assert kwargs["symbol"] == "BTCUSDT"


def test_cancel_order_strips_P():
  client = _make_client()
  client._session.cancel_order = MagicMock(
    return_value={"retCode": 0, "retMsg": "OK", "result": {}},
  )
  client.cancel_order(symbol="ETHUSDT.P", order_id="abc")
  kwargs = client._session.cancel_order.call_args.kwargs
  assert kwargs["symbol"] == "ETHUSDT"


def test_set_leverage_strips_P():
  client = _make_client()
  client._session.set_leverage = MagicMock(return_value={"result": {}})
  client.set_leverage("SOLUSDT.P", "10", "10")
  kwargs = client._session.set_leverage.call_args.kwargs
  assert kwargs["symbol"] == "SOLUSDT"


def test_get_positions_strips_P():
  client = _make_client()
  client._session.get_positions = MagicMock(
    return_value={"retCode": 0, "retMsg": "OK", "result": {"list": []}},
  )
  client.get_positions(symbol="XRPUSDT.P")
  kwargs = client._session.get_positions.call_args.kwargs
  assert kwargs["symbol"] == "XRPUSDT"


def test_cancel_all_orders_none_symbol_still_works():
  """None passthrough — 'cancel everything' semantics preserved."""
  client = _make_client()
  client._session.cancel_all_orders = MagicMock(
    return_value={"retCode": 0, "retMsg": "OK", "result": {}},
  )
  client.cancel_all_orders(symbol=None)
  kwargs = client._session.cancel_all_orders.call_args.kwargs
  assert "symbol" not in kwargs
