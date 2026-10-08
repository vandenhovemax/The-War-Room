"""Bybit V5 API contract tests (mocked SDK).

Pins the exact request parameters BybitTradingClient sends to the pybit
HTTP client, plus response parsing. Prevents silent regressions in:
  - category="linear" on every call
  - reduceOnly boolean (not string) on exit orders
  - settleCoin="USDT" on portfolio-wide ops
  - Conditional fields (stopLoss / takeProfit / triggerPrice) only set when present
  - _check_response raises on non-zero retCode
  - get_positions parsing (size / avgPrice / markPrice / sides)
  - Instrument info parsing (tick_size, lot_size, min/max qty)
  - set_leverage swallows 110043 (already set)
  - switch_to_isolated swallows 110026 / 100028
  - StopOrder filter is queried alongside Order filter in get_open_orders

The pybit SDK is mocked by patching the client's ``_session`` attribute
with a ``MagicMock`` — we don't hit the real network.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_client():
  """Build a BybitTradingClient with the pybit HTTP SDK patched out."""
  with patch("backend.exchange.bybit_trading.BybitHTTP") as mock_http:
    mock_http.return_value = MagicMock()
    from backend.exchange.bybit_trading import BybitTradingClient
    client = BybitTradingClient(
      api_key="k", api_secret="s", testnet=True,
    )
  # Replace the session with a fresh mock for clean call assertions
  client._session = MagicMock()
  return client


def _ok(result: dict | list | None = None) -> dict:
  """Shape a successful Bybit V5 envelope."""
  return {"retCode": 0, "retMsg": "OK", "result": result or {}}


# --- _check_response ----------------------------------------------------

def test_check_response_accepts_retcode_zero():
  from backend.exchange.bybit_trading import BybitTradingClient
  # Static method — no need to patch. No exception means it's accepted.
  BybitTradingClient._check_response({"retCode": 0, "retMsg": "OK"})


def test_check_response_raises_on_nonzero():
  from backend.exchange.bybit_trading import BybitTradingClient
  with pytest.raises(ValueError) as exc:
    BybitTradingClient._check_response({"retCode": 110043, "retMsg": "leverage not modified"})
  assert "110043" in str(exc.value)
  assert "leverage not modified" in str(exc.value)


def test_check_response_raises_on_missing_retcode():
  from backend.exchange.bybit_trading import BybitTradingClient
  with pytest.raises(ValueError):
    BybitTradingClient._check_response({})  # default -1


# --- place_order contract ----------------------------------------------

def test_place_order_sends_linear_category_and_reduce_only_bool():
  c = _make_client()
  c._session.place_order.return_value = _ok({
    "orderId": "abc", "orderLinkId": "lnk",
  })

  c.place_order(
    symbol="BTCUSDT", side="Buy", order_type="Market",
    qty="0.01", reduce_only=True,
  )

  args = c._session.place_order.call_args.kwargs
  assert args["category"] == "linear"
  assert args["symbol"] == "BTCUSDT"
  assert args["side"] == "Buy"
  assert args["orderType"] == "Market"
  assert args["qty"] == "0.01"
  # CRITICAL: must be Python bool — Bybit V5 rejects "true"/"false" strings here
  assert args["reduceOnly"] is True
  assert args["timeInForce"] == "GTC"


def test_place_order_omits_sl_tp_when_not_set():
  c = _make_client()
  c._session.place_order.return_value = _ok({"orderId": "x"})

  c.place_order(
    symbol="BTCUSDT", side="Buy", order_type="Market", qty="0.01",
  )

  args = c._session.place_order.call_args.kwargs
  assert "stopLoss" not in args
  assert "takeProfit" not in args
  assert "triggerPrice" not in args


def test_place_order_sets_sl_tp_when_provided():
  c = _make_client()
  c._session.place_order.return_value = _ok({"orderId": "x"})

  c.place_order(
    symbol="BTCUSDT", side="Buy", order_type="Market", qty="0.01",
    sl_price="48000", tp_price="52000",
  )

  args = c._session.place_order.call_args.kwargs
  assert args["stopLoss"] == "48000"
  assert args["takeProfit"] == "52000"
  assert args["slTriggerBy"] == "LastPrice"
  assert args["tpTriggerBy"] == "LastPrice"


def test_place_order_conditional_sets_trigger_direction():
  c = _make_client()
  c._session.place_order.return_value = _ok({"orderId": "x"})

  c.place_order(
    symbol="BTCUSDT", side="Sell", order_type="Market", qty="0.01",
    trigger_price="50000", trigger_direction=2,
  )

  args = c._session.place_order.call_args.kwargs
  assert args["triggerPrice"] == "50000"
  assert args["triggerDirection"] == 2
  assert args["triggerBy"] == "LastPrice"


def test_place_order_limit_sends_price():
  c = _make_client()
  c._session.place_order.return_value = _ok({"orderId": "x"})

  c.place_order(
    symbol="BTCUSDT", side="Buy", order_type="Limit",
    qty="0.01", price="50000",
  )

  assert c._session.place_order.call_args.kwargs["price"] == "50000"


def test_place_order_raises_on_nonzero_retcode():
  c = _make_client()
  c._session.place_order.return_value = {
    "retCode": 110007,
    "retMsg": "Insufficient available balance",
    "result": {},
  }

  with pytest.raises(ValueError) as exc:
    c.place_order(
      symbol="BTCUSDT", side="Buy", order_type="Market", qty="0.01",
    )
  assert "110007" in str(exc.value)


# --- cancel_order / cancel_all -----------------------------------------

def test_cancel_order_by_order_id():
  c = _make_client()
  c._session.cancel_order.return_value = _ok({"orderId": "abc"})

  c.cancel_order(symbol="BTCUSDT", order_id="abc")

  args = c._session.cancel_order.call_args.kwargs
  assert args["category"] == "linear"
  assert args["symbol"] == "BTCUSDT"
  assert args["orderId"] == "abc"
  assert "orderLinkId" not in args


def test_cancel_order_requires_id_or_link():
  c = _make_client()
  with pytest.raises(ValueError):
    c.cancel_order(symbol="BTCUSDT")


def test_cancel_all_orders_uses_usdt_settle_coin():
  c = _make_client()
  c._session.cancel_all_orders.return_value = _ok({"list": []})

  c.cancel_all_orders()

  args = c._session.cancel_all_orders.call_args.kwargs
  assert args["category"] == "linear"
  assert args["settleCoin"] == "USDT"
  assert "symbol" not in args


def test_cancel_all_orders_with_symbol_filter():
  c = _make_client()
  c._session.cancel_all_orders.return_value = _ok({"list": []})

  c.cancel_all_orders(symbol="ETHUSDT")

  args = c._session.cancel_all_orders.call_args.kwargs
  assert args["symbol"] == "ETHUSDT"


# --- get_open_orders: normal + stop combined ---------------------------

def test_get_open_orders_queries_both_order_and_stoporder():
  c = _make_client()
  c._session.get_open_orders.side_effect = [
    _ok({"list": [{"orderId": "normal-1"}]}),
    _ok({"list": [{"orderId": "stop-1"}]}),
  ]

  out = c.get_open_orders()

  assert c._session.get_open_orders.call_count == 2
  # First call: plain orders (no orderFilter)
  first = c._session.get_open_orders.call_args_list[0].kwargs
  assert "orderFilter" not in first
  # Second call: StopOrder filter
  second = c._session.get_open_orders.call_args_list[1].kwargs
  assert second["orderFilter"] == "StopOrder"
  # Both must settle in USDT
  assert first["settleCoin"] == "USDT"
  assert second["settleCoin"] == "USDT"
  # Combined result
  assert len(out) == 2
  assert {o["orderId"] for o in out} == {"normal-1", "stop-1"}


# --- get_positions parsing ---------------------------------------------

def test_get_positions_filters_zero_size():
  """size==0 positions must be dropped (flat positions are returned by Bybit)."""
  c = _make_client()
  c._session.get_positions.return_value = _ok({
    "list": [
      {"symbol": "BTCUSDT", "side": "Buy", "size": "0.01",
       "avgPrice": "50000", "markPrice": "50100",
       "unrealisedPnl": "1.0", "leverage": "5",
       "liqPrice": "45000", "tradeMode": "1", "positionIdx": 0},
      {"symbol": "ETHUSDT", "side": "", "size": "0",
       "avgPrice": "0", "markPrice": "0",
       "unrealisedPnl": "0", "leverage": "1",
       "tradeMode": "0", "positionIdx": 0},
    ],
  })

  out = c.get_positions()

  assert len(out) == 1
  assert out[0]["symbol"] == "BTCUSDT"
  assert out[0]["size"] == 0.01
  assert out[0]["entry_price"] == 50000.0
  assert out[0]["mark_price"] == 50100.0
  assert out[0]["unrealized_pnl"] == 1.0
  assert out[0]["liq_price"] == 45000.0
  assert out[0]["margin_mode"] == "isolated"


def test_get_positions_cross_margin_parsed():
  c = _make_client()
  c._session.get_positions.return_value = _ok({
    "list": [{
      "symbol": "BTCUSDT", "side": "Sell", "size": "0.02",
      "avgPrice": "50000", "markPrice": "49900",
      "unrealisedPnl": "2.0", "leverage": "10",
      "liqPrice": "", "tradeMode": "0", "positionIdx": 0,
    }],
  })

  out = c.get_positions()

  assert out[0]["margin_mode"] == "cross"
  assert out[0]["liq_price"] is None  # empty string handled


# --- Instrument info parsing -------------------------------------------

def test_get_instrument_info_parses_filters():
  c = _make_client()
  c._session.get_instruments_info.return_value = _ok({
    "list": [{
      "symbol": "BTCUSDT",
      "lotSizeFilter": {
        "qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "500",
      },
      "priceFilter": {"tickSize": "0.10"},
      "leverageFilter": {"maxLeverage": "100", "minLeverage": "1"},
      "fundingInterval": 480,
    }],
  })

  info = c.get_instrument_info("BTCUSDT")

  assert info["tick_size"] == 0.10
  assert info["lot_size"] == 0.001
  assert info["min_qty"] == 0.001
  assert info["max_qty"] == 500.0
  assert info["max_leverage"] == 100.0
  assert info["funding_interval"] == 480


def test_get_instrument_info_caches_result():
  c = _make_client()
  c._session.get_instruments_info.return_value = _ok({
    "list": [{
      "symbol": "BTCUSDT",
      "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "500"},
      "priceFilter": {"tickSize": "0.10"},
      "leverageFilter": {"maxLeverage": "100", "minLeverage": "1"},
    }],
  })

  _ = c.get_instrument_info("BTCUSDT")
  _ = c.get_instrument_info("BTCUSDT")

  # Second call must hit the cache, not the API
  assert c._session.get_instruments_info.call_count == 1


def test_get_instrument_info_raises_on_empty_list():
  c = _make_client()
  c._session.get_instruments_info.return_value = _ok({"list": []})

  with pytest.raises(ValueError) as exc:
    c.get_instrument_info("FAKEUSDT")
  assert "Instrument not found" in str(exc.value)


def test_clear_instrument_cache_forces_refetch():
  c = _make_client()
  c._session.get_instruments_info.return_value = _ok({
    "list": [{
      "symbol": "BTCUSDT",
      "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "500"},
      "priceFilter": {"tickSize": "0.10"},
      "leverageFilter": {"maxLeverage": "100", "minLeverage": "1"},
    }],
  })

  _ = c.get_instrument_info("BTCUSDT")
  c.clear_instrument_cache()
  _ = c.get_instrument_info("BTCUSDT")

  assert c._session.get_instruments_info.call_count == 2


# --- Leverage / margin idempotency -------------------------------------

def test_set_leverage_swallows_110043_already_modified():
  """110043 = leverage already at target → treat as success, not raise."""
  c = _make_client()
  c._session.set_leverage.side_effect = Exception(
    "Bybit API error [110043]: leverage not modified"
  )

  out = c.set_leverage("BTCUSDT", "10", "10")

  assert out == {}


def test_set_leverage_raises_on_unrelated_error():
  c = _make_client()
  c._session.set_leverage.side_effect = Exception(
    "Bybit API error [10001]: some other error"
  )

  with pytest.raises(Exception) as exc:
    c.set_leverage("BTCUSDT", "10", "10")
  assert "10001" in str(exc.value)


def test_switch_to_isolated_swallows_already_isolated():
  """110026 = already isolated, 100028 = UTA (no isolated) — both silent."""
  c = _make_client()
  c._session.switch_margin_mode.side_effect = Exception(
    "Bybit API error [110026]: already isolated"
  )
  assert c.switch_to_isolated("BTCUSDT", "10", "10") == {}

  c._session.switch_margin_mode.side_effect = Exception(
    "Bybit API error [100028]: UTA account"
  )
  assert c.switch_to_isolated("BTCUSDT", "10", "10") == {}


# --- order_link_id format ---------------------------------------------

def test_generate_order_link_id_is_bounded():
  """Bybit max is 36 chars."""
  from backend.exchange.bybit_trading import BybitTradingClient
  for _ in range(10):
    link = BybitTradingClient.generate_order_link_id()
    assert 1 <= len(link) <= 36


# --- Wallet balance parsing -------------------------------------------

def test_wallet_balance_handles_empty_string_values():
  """Bybit sometimes returns '' for empty balances — must coerce to 0.0."""
  c = _make_client()
  c._session.get_wallet_balance.return_value = _ok({
    "list": [{
      "totalEquity": "1000.5",
      "totalWalletBalance": "",
      "totalAvailableBalance": "800.0",
      "coin": [
        {"coin": "USDT", "equity": "1000.5", "availableToWithdraw": "800.0", "unrealisedPnl": ""},
      ],
    }],
  })

  out = c.get_wallet_balance()

  assert out["total_equity"] == 1000.5
  assert out["wallet_balance"] == 0.0  # empty string → 0
  assert out["available"] == 800.0
  assert out["coins"][0]["unrealized_pnl"] == 0.0


def test_wallet_balance_empty_accounts_returns_zeros():
  c = _make_client()
  c._session.get_wallet_balance.return_value = _ok({"list": []})

  out = c.get_wallet_balance()

  assert out == {"total_equity": 0, "available": 0, "coins": []}
