"""CS-004: mocked-HTTP contract suite for Bybit V5 trading surface.

This is the biggest test gap flagged in the audit: the entire live-trading
surface — symbol normalization, qty/lot rounding, price/tick rounding,
``reduceOnly`` plumbing, response validation, dust filtering — had zero
direct coverage. A single Bybit schema change or a one-char typo in the
rounding helper could silently swap qty on a live order.

This suite uses ``unittest.mock`` to patch the underlying ``BybitHTTP``
pybit session, so no HTTP traffic leaves the test. Each test asserts one
of the contract surfaces:

  1. ``.P`` suffix stripping at every ingress (CS-040 guard).
  2. Floor-to-step rounding for qty and price using Decimal (no float
     artifacts like ``0.3 // 0.1 == 2.0``).
  3. V5 request body shape — category=linear, symbol normalized,
     ``reduceOnly`` on SL/TP, SL/TP fields nested under stopLoss/takeProfit.
  4. ``BybitAPIError`` is raised on non-zero retCode and stays compatible
     with the legacy ``"110043" in str(e)`` substring probe.
  5. ``get_positions`` filters sub-lot dust so the engine never "closes"
     a position that is effectively flat.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.exchange.bybit_trading import (
  BybitAPIError,
  BybitTradingClient,
  _normalize_symbol,
)
from backend.exchange.order_manager import OrderManager


# ── Fixtures: table-driven instrument specs ──────────────────────────

_INSTRUMENTS = {
  "BTCUSDT": {"tick_size": 0.1, "lot_size": 0.001},
  "ETHUSDT": {"tick_size": 0.01, "lot_size": 0.01},
  "DOGEUSDT": {"tick_size": 0.00001, "lot_size": 1.0},
  "MATICUSDT": {"tick_size": 0.0001, "lot_size": 1.0},
  "XRPUSDT": {"tick_size": 0.0001, "lot_size": 1.0},
}


@pytest.fixture
def mock_client():
  """BybitTradingClient with the pybit HTTP session patched to a Mock.
  Return (client, mock_session) so tests can inspect call args.
  """
  with patch("backend.exchange.bybit_trading.BybitHTTP") as mock_http:
    mock_session = MagicMock()
    mock_http.return_value = mock_session
    client = BybitTradingClient(
      api_key="test_key", api_secret="test_secret", testnet=True,
    )
    yield client, mock_session


# ── 1. _normalize_symbol — `.P` stripping ────────────────────────────

def test_normalize_strips_p_suffix():
  assert _normalize_symbol("BTCUSDT.P") == "BTCUSDT"


def test_normalize_passthrough_for_bare_symbol():
  assert _normalize_symbol("BTCUSDT") == "BTCUSDT"


def test_normalize_passthrough_for_none():
  """Cancel-all-orders calls with symbol=None must NOT raise."""
  assert _normalize_symbol(None) is None


def test_normalize_only_strips_trailing_P_not_internal():
  """A hypothetical symbol with "P" in the middle must not be corrupted."""
  assert _normalize_symbol("PEPEUSDT") == "PEPEUSDT"


def test_place_order_normalizes_symbol_at_ingress(mock_client):
  """Even if the caller passes ``BTCUSDT.P`` (TradingView form), the V5
  body must be sent with the bare Bybit form.
  """
  client, mock_session = mock_client
  mock_session.place_order.return_value = {
    "retCode": 0, "retMsg": "OK",
    "result": {"orderId": "abc123", "orderLinkId": "link"},
  }
  client.place_order(
    symbol="BTCUSDT.P", side="Buy", order_type="Market", qty="0.001",
  )
  kwargs = mock_session.place_order.call_args.kwargs
  assert kwargs["symbol"] == "BTCUSDT", (
    "CS-040: .P suffix leaked through place_order to the wire"
  )


def test_cancel_order_normalizes_symbol_at_ingress(mock_client):
  client, mock_session = mock_client
  mock_session.cancel_order.return_value = {"retCode": 0, "result": {}}
  client.cancel_order(symbol="ETHUSDT.P", order_id="xyz")
  kwargs = mock_session.cancel_order.call_args.kwargs
  assert kwargs["symbol"] == "ETHUSDT"


def test_set_leverage_normalizes_symbol_at_ingress(mock_client):
  client, mock_session = mock_client
  mock_session.set_leverage.return_value = {"retCode": 0, "result": {}}
  client.set_leverage(symbol="DOGEUSDT.P", buy_leverage="10", sell_leverage="10")
  kwargs = mock_session.set_leverage.call_args.kwargs
  assert kwargs["symbol"] == "DOGEUSDT"


def test_get_positions_normalizes_symbol_at_ingress(mock_client):
  client, mock_session = mock_client
  mock_session.get_positions.return_value = {
    "retCode": 0, "result": {"list": []},
  }
  client.get_positions(symbol="BTCUSDT.P")
  kwargs = mock_session.get_positions.call_args.kwargs
  assert kwargs["symbol"] == "BTCUSDT"


# ── 2. _round_qty / _round_price — Decimal floor correctness ──────────

@pytest.mark.parametrize("qty,lot,expected", [
  # The Decimal fix: 0.3 // 0.1 would be 2.0 via float floor, dropping
  # a full lot. Must return "0.3".
  (0.3, 0.1, "0.3"),
  # Fine-grained lot at 0.001 (BTCUSDT) — 0.0015 floors to 0.001.
  (0.0015, 0.001, "0.001"),
  # Already-at-step value must not drop a lot.
  (0.002, 0.001, "0.002"),
  # Whole-lot assets (lot=1) round down to int and format as "3", not "3.0".
  (3.7, 1.0, "3"),
  (3.0, 1.0, "3"),
  # Sub-min qty floors to zero for lot=1.
  (0.4, 1.0, "0"),
  # Large qty with small lot — 1234.5678 at 0.01 → "1234.56" (floor).
  (1234.5678, 0.01, "1234.56"),
])
def test_round_qty_uses_decimal_floor(qty, lot, expected):
  assert OrderManager._round_qty(qty, lot) == expected


@pytest.mark.parametrize("price,tick,expected", [
  # Standard BTC tick at 0.1 — 43210.17 floors to 43210.1
  (43210.17, 0.1, "43210.1"),
  # ETH tick 0.01 — 2345.678 floors to 2345.67
  (2345.678, 0.01, "2345.67"),
  # Sub-cent tick 0.00001 (DOGE) — 0.123456 floors to 0.12345
  (0.123456, 0.00001, "0.12345"),
  # Already-at-tick value is unchanged.
  (100.10, 0.1, "100.1"),
  # Whole-tick pricing (tick=1) — 43210.99 floors to 43210.
  (43210.99, 1.0, "43210"),
])
def test_round_price_uses_decimal_floor(price, tick, expected):
  assert OrderManager._round_price(price, tick) == expected


def test_round_qty_never_returns_a_negative():
  """Even on pathological inputs, qty rounds toward zero — a negative
  qty would flip order side at Bybit.
  """
  # Near-zero positive must floor to the min positive or "0".
  assert OrderManager._round_qty(0.0, 0.001) == "0.000"


# ── 3. V5 request body shape ─────────────────────────────────────────

def test_place_order_includes_category_linear(mock_client):
  client, mock_session = mock_client
  mock_session.place_order.return_value = {
    "retCode": 0, "result": {"orderId": "x", "orderLinkId": "y"},
  }
  client.place_order(
    symbol="BTCUSDT", side="Buy", order_type="Market", qty="0.001",
  )
  kwargs = mock_session.place_order.call_args.kwargs
  assert kwargs["category"] == "linear", (
    "CS-004: every V5 call must include category=linear; without it "
    "Bybit rejects with 110017 (category required)"
  )


def test_place_order_passes_reduce_only_flag_through(mock_client):
  client, mock_session = mock_client
  mock_session.place_order.return_value = {
    "retCode": 0, "result": {"orderId": "x", "orderLinkId": "y"},
  }
  client.place_order(
    symbol="BTCUSDT", side="Sell", order_type="Market", qty="0.001",
    reduce_only=True,
  )
  kwargs = mock_session.place_order.call_args.kwargs
  assert kwargs["reduceOnly"] is True, (
    "CS-004: reduceOnly must round-trip as a proper bool. If this drops "
    "to False on SL/TP calls, an exit can open a new opposite position."
  )


def test_place_order_defaults_reduce_only_to_false(mock_client):
  """Entries must explicitly NOT have reduceOnly set — if it leaks to
  True, an entry order is rejected because there's no position to reduce.
  """
  client, mock_session = mock_client
  mock_session.place_order.return_value = {
    "retCode": 0, "result": {"orderId": "x", "orderLinkId": "y"},
  }
  client.place_order(
    symbol="BTCUSDT", side="Buy", order_type="Market", qty="0.001",
  )
  kwargs = mock_session.place_order.call_args.kwargs
  assert kwargs["reduceOnly"] is False


def test_place_order_attaches_sl_and_tp_fields(mock_client):
  client, mock_session = mock_client
  mock_session.place_order.return_value = {
    "retCode": 0, "result": {"orderId": "x", "orderLinkId": "y"},
  }
  client.place_order(
    symbol="BTCUSDT", side="Buy", order_type="Market", qty="0.001",
    sl_price="42000.0", tp_price="45000.0",
  )
  kwargs = mock_session.place_order.call_args.kwargs
  assert kwargs["stopLoss"] == "42000.0"
  assert kwargs["takeProfit"] == "45000.0"
  # triggerBy must be set when SL/TP are attached.
  assert kwargs["slTriggerBy"] == "LastPrice"
  assert kwargs["tpTriggerBy"] == "LastPrice"


def test_place_order_omits_sl_tp_when_not_provided(mock_client):
  """Without SL/TP, the V5 body must NOT carry empty stopLoss/takeProfit
  strings — Bybit treats them as literal "0" trigger prices.
  """
  client, mock_session = mock_client
  mock_session.place_order.return_value = {
    "retCode": 0, "result": {"orderId": "x", "orderLinkId": "y"},
  }
  client.place_order(
    symbol="BTCUSDT", side="Buy", order_type="Market", qty="0.001",
  )
  kwargs = mock_session.place_order.call_args.kwargs
  assert "stopLoss" not in kwargs
  assert "takeProfit" not in kwargs


def test_place_order_attaches_trigger_fields_for_conditional(mock_client):
  """Conditional (stop-entry) orders need triggerPrice + triggerDirection.
  Missing triggerDirection causes Bybit 10001 ("required params lost").
  """
  client, mock_session = mock_client
  mock_session.place_order.return_value = {
    "retCode": 0, "result": {"orderId": "x", "orderLinkId": "y"},
  }
  client.place_order(
    symbol="BTCUSDT", side="Sell", order_type="Market", qty="0.001",
    trigger_price="41000.0", trigger_direction=2,
  )
  kwargs = mock_session.place_order.call_args.kwargs
  assert kwargs["triggerPrice"] == "41000.0"
  assert kwargs["triggerDirection"] == 2
  assert kwargs["triggerBy"] == "LastPrice"


# ── 4. BybitAPIError — structured + legacy-substring compatible ──────

def test_bybit_api_error_is_valueerror():
  """Legacy handlers catch ``ValueError`` — the new error must still be
  catchable that way.
  """
  err = BybitAPIError(110043, "leverage not modified")
  assert isinstance(err, ValueError)


def test_bybit_api_error_exposes_ret_code_and_msg():
  err = BybitAPIError(110043, "leverage not modified")
  assert err.ret_code == 110043
  assert err.ret_msg == "leverage not modified"


def test_bybit_api_error_str_contains_code_for_legacy_substring_probes():
  """A lot of existing code does ``if "110043" in str(e)``. Breaking this
  would re-raise on already-set leverage and fail every live trade.
  """
  err = BybitAPIError(110043, "leverage not modified")
  assert "110043" in str(err)
  assert "leverage not modified" in str(err)


def test_check_response_passes_on_zero_ret_code():
  """retCode=0 means OK — must return without raising."""
  BybitTradingClient._check_response({"retCode": 0, "retMsg": "OK"})


def test_check_response_raises_on_nonzero_ret_code():
  """Any non-zero retCode raises with the exact code and message so the
  caller can dispatch on it.
  """
  with pytest.raises(BybitAPIError) as excinfo:
    BybitTradingClient._check_response({
      "retCode": 110025, "retMsg": "Position mode not modified",
    })
  assert excinfo.value.ret_code == 110025
  assert excinfo.value.ret_msg == "Position mode not modified"


def test_check_response_raises_on_missing_retcode():
  """A malformed response (no retCode) must still surface — default to
  -1 (sentinel) and raise, rather than silently treat it as success.
  """
  with pytest.raises(BybitAPIError):
    BybitTradingClient._check_response({"retMsg": "malformed"})


# ── 5. get_positions dust filter ──────────────────────────────────────

def test_get_positions_filters_sub_lot_dust(mock_client):
  """Bybit occasionally returns tiny non-zero sizes after partial close
  (e.g. 1e-9 dust). Without filtering, the engine tries to "close" a
  position that isn't there; this blocks the next entry on that symbol.
  """
  client, mock_session = mock_client
  mock_session.get_positions.return_value = {
    "retCode": 0, "result": {"list": [
      {"symbol": "BTCUSDT", "size": "0.000000001", "side": "Buy",
       "avgPrice": "42000", "markPrice": "42000",
       "unrealisedPnl": "0", "leverage": "10",
       "liqPrice": "0", "tradeMode": 1, "positionIdx": 0},
      {"symbol": "ETHUSDT", "size": "0.5", "side": "Buy",
       "avgPrice": "2500", "markPrice": "2500",
       "unrealisedPnl": "0", "leverage": "10",
       "liqPrice": "0", "tradeMode": 1, "positionIdx": 0},
    ]},
  }
  positions = client.get_positions()
  # Dust is dropped, real position kept.
  symbols = [p["symbol"] for p in positions]
  assert "BTCUSDT" not in symbols, (
    "CS-004: sub-lot dust (< 1e-8) must not appear in open-positions list"
  )
  assert "ETHUSDT" in symbols


def test_get_positions_keeps_real_small_positions_above_dust_floor(mock_client):
  """A 0.001 BTC position is legitimate (the Bybit min lot) — must NOT
  be filtered as dust.
  """
  client, mock_session = mock_client
  mock_session.get_positions.return_value = {
    "retCode": 0, "result": {"list": [
      {"symbol": "BTCUSDT", "size": "0.001", "side": "Buy",
       "avgPrice": "42000", "markPrice": "42000",
       "unrealisedPnl": "0", "leverage": "10",
       "liqPrice": "0", "tradeMode": 1, "positionIdx": 0},
    ]},
  }
  positions = client.get_positions()
  assert len(positions) == 1
  assert positions[0]["size"] == pytest.approx(0.001)


# ── 6. get_instrument_info returns parsed spec with floats ────────────

def test_get_instrument_info_parses_v5_response_and_caches(mock_client):
  client, mock_session = mock_client
  mock_session.get_instruments_info.return_value = {
    "retCode": 0, "result": {"list": [{
      "symbol": "BTCUSDT",
      "lotSizeFilter": {
        "qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "100",
      },
      "priceFilter": {"tickSize": "0.1"},
      "leverageFilter": {"maxLeverage": "100", "minLeverage": "1"},
      "fundingInterval": 480,
    }]},
  }
  info = client.get_instrument_info("BTCUSDT")
  assert info["tick_size"] == pytest.approx(0.1)
  assert info["lot_size"] == pytest.approx(0.001)
  assert info["min_qty"] == pytest.approx(0.001)
  assert info["max_qty"] == pytest.approx(100.0)
  assert info["max_leverage"] == pytest.approx(100.0)
  # Second call hits the cache (no extra HTTP call).
  _ = client.get_instrument_info("BTCUSDT")
  assert mock_session.get_instruments_info.call_count == 1


def test_get_instrument_info_raises_on_unknown_symbol(mock_client):
  client, mock_session = mock_client
  mock_session.get_instruments_info.return_value = {
    "retCode": 0, "result": {"list": []},
  }
  with pytest.raises(ValueError, match="Instrument not found"):
    client.get_instrument_info("BOGUSUSDT")
