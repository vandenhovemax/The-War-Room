"""Bybit V5 trading client — full order management via pybit.

Wraps the official pybit SDK for order placement, position management,
and account configuration. Extends the read-only BybitPrivateClient
with trading capabilities.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

from pybit.unified_trading import HTTP as BybitHTTP

from backend.utils.logger import get_logger

logger = get_logger(__name__)

TESTNET_URL = "https://api-testnet.bybit.com"
MAINNET_URL = "https://api.bybit.com"


def _normalize_symbol(symbol: str | None) -> str | None:
  """CS-040: strip the TradingView ``.P`` perpetual suffix at Bybit ingress.

  TradingView's crypto feed tags perpetual futures with a ``.P`` suffix
  (``BTCUSDT.P``), but Bybit's V5 API expects the bare contract symbol
  (``BTCUSDT``). If the suffix leaks through — via a hand-configured
  alert, a copy-pasted symbol, or legacy saved state — Bybit returns
  ``110001 Invalid symbol`` instead of executing. Normalize once at the
  trading-client boundary so every downstream call is guaranteed to use
  the Bybit form.

  Pass-through for ``None`` (optional-symbol endpoints like "cancel all").
  """
  if symbol is None:
    return None
  if symbol.endswith(".P"):
    cleaned = symbol[:-2]
    logger.warning(
      "CS-040: stripped .P suffix from symbol %r -> %r (TradingView form "
      "leaked to Bybit client)", symbol, cleaned,
    )
    return cleaned
  return symbol


class BybitAPIError(ValueError):
  """Structured Bybit API error carrying retCode and retMsg.

  Subclasses ``ValueError`` so existing ``except ValueError`` handlers and
  legacy ``"110043" in str(e)`` substring checks continue to work. New
  code can use ``isinstance(e, BybitAPIError)`` and ``e.ret_code`` to
  dispatch on exchange error codes without string parsing.

  Reference codes seen in this file:
    110043 = leverage not modified (set_leverage idempotent)
    110026 = already isolated margin
    100028 = UTA account (no isolated mode)
  """
  __slots__ = ("ret_code", "ret_msg")

  def __init__(self, ret_code: int, ret_msg: str):
    self.ret_code = ret_code
    self.ret_msg = ret_msg
    super().__init__(f"Bybit API error [{ret_code}]: {ret_msg}")


# CS-001: retryable Bybit V5 retCodes. Everything else is permanent and
# must raise on the first attempt so the caller's emergency-close path
# fires immediately instead of wasting ~0.75s on hopeless retries.
#   10002 = server busy / system under maintenance
#   10006 = too many visits (rate limit)
#   10016 = service downgrade (short outage)
#   10018 = request timeout
#   130035 = internal retry hint
# Curated from Bybit V5 docs; unlisted codes (110007 insufficient balance,
# 110017 invalid qty, 110045 position size exceeded, etc.) are permanent.
_RETRYABLE_RET_CODES: frozenset[int] = frozenset({10002, 10006, 10016, 10018, 130035})


def is_retryable_bybit_error(exc: BaseException) -> bool:
  """Return True if ``exc`` is a transient Bybit error worth retrying.

  Typed ``BybitAPIError`` with an unrecognised (non-retryable) ``ret_code``
  is treated as permanent. Any non-Bybit exception (network reset, pybit
  timeout) is retryable by default — these surface as plain ``Exception``
  subclasses from pybit and are always transient.
  """
  if isinstance(exc, BybitAPIError):
    return exc.ret_code in _RETRYABLE_RET_CODES
  return True


class BybitTradingClient:
  """Full Bybit V5 trading client using pybit SDK.

  Handles order placement, cancellation, amendment, position/leverage
  management, and account queries. All methods raise on API errors.

  Hybrid testnet mode: when testnet=True and use_mainnet_prices=True,
  instrument info queries go to mainnet (real tick sizes, lot sizes,
  max leverage) while order/position operations go to testnet.
  """

  def __init__(
    self, api_key: str, api_secret: str, testnet: bool = True,
    use_mainnet_prices: bool = False,
  ):
    self.api_key = api_key
    self.api_secret = api_secret
    self.testnet = testnet
    self._session = BybitHTTP(
      api_key=api_key,
      api_secret=api_secret,
      testnet=testnet,
      recv_window=60000,  # 60s tolerance for system clock drift
    )
    # Separate mainnet session for public queries in hybrid mode
    self._use_mainnet_prices = use_mainnet_prices and testnet
    self._public_session: BybitHTTP | None = None
    if self._use_mainnet_prices:
      self._public_session = BybitHTTP(testnet=False)
      logger.info("Hybrid mode: instrument info will query mainnet")
    # Instrument info cache: symbol -> {tick_size, lot_size, min_qty, max_leverage, ...}
    self._instruments: dict[str, dict] = {}

  @staticmethod
  def generate_order_link_id() -> str:
    """Generate a unique client order ID (max 36 chars for Bybit)."""
    return str(uuid.uuid4())[:36]

  # ── Instrument Info ──────────────────────────────────────────────

  def get_instrument_info(self, symbol: str) -> dict:
    """Get instrument specs (cached). Returns tick_size, lot_size, min_qty, max_leverage.

    In hybrid mode, queries mainnet for accurate instrument specs.
    """
    symbol = _normalize_symbol(symbol)  # CS-040
    if symbol in self._instruments:
      return self._instruments[symbol]
    # Use mainnet session for instrument info in hybrid mode
    session = self._public_session if self._public_session else self._session
    resp = session.get_instruments_info(category="linear", symbol=symbol)
    self._check_response(resp)
    items = resp.get("result", {}).get("list", [])
    if not items:
      raise ValueError(f"Instrument not found: {symbol}")
    info = items[0]
    lot_filter = info.get("lotSizeFilter", {})
    price_filter = info.get("priceFilter", {})
    lev_filter = info.get("leverageFilter", {})
    parsed = {
      "symbol": symbol,
      "tick_size": float(price_filter.get("tickSize", "0.01")),
      "lot_size": float(lot_filter.get("qtyStep", "0.001")),
      "min_qty": float(lot_filter.get("minOrderQty", "0.001")),
      "max_qty": float(lot_filter.get("maxOrderQty", "1000")),
      "max_leverage": float(lev_filter.get("maxLeverage", "100")),
      "min_leverage": float(lev_filter.get("minLeverage", "1")),
      "funding_interval": int(info.get("fundingInterval", 480)),
    }
    self._instruments[symbol] = parsed
    return parsed

  def clear_instrument_cache(self) -> None:
    """Clear cached instrument info (call on reconnect)."""
    self._instruments.clear()

  # ── Order Management ─────────────────────────────────────────────

  def place_order(
    self,
    symbol: str,
    side: str,
    order_type: str,
    qty: str,
    price: str | None = None,
    time_in_force: str = "GTC",
    reduce_only: bool = False,
    order_link_id: str | None = None,
    sl_price: str | None = None,
    tp_price: str | None = None,
    trigger_price: str | None = None,
    trigger_by: str = "LastPrice",
    trigger_direction: int | None = None,
  ) -> dict:
    """Place an order on Bybit.

    Args:
      symbol: e.g. "BTCUSDT"
      side: "Buy" or "Sell"
      order_type: "Market" or "Limit"
      qty: order quantity as string
      price: limit price (required for Limit orders)
      time_in_force: "GTC", "IOC", "FOK", "PostOnly"
      reduce_only: True for exit orders
      order_link_id: client-side UUID for tracking
      sl_price: stop loss trigger price (exchange-managed SL)
      tp_price: take profit trigger price (exchange-managed TP)
      trigger_price: for conditional/stop orders
      trigger_by: "LastPrice", "MarkPrice", "IndexPrice"
      trigger_direction: 1 = trigger when price rises, 2 = trigger when price falls
                         Required for conditional orders with triggerPrice.

    Returns: {"orderId": "...", "orderLinkId": "..."}
    """
    symbol = _normalize_symbol(symbol)  # CS-040
    params: dict = {
      "category": "linear",
      "symbol": symbol,
      "side": side,
      "orderType": order_type,
      "qty": qty,
      "timeInForce": time_in_force,
      "reduceOnly": reduce_only,
    }
    if price is not None:
      params["price"] = price
    if order_link_id:
      params["orderLinkId"] = order_link_id
    if sl_price:
      params["stopLoss"] = sl_price
      params["slTriggerBy"] = trigger_by
    if tp_price:
      params["takeProfit"] = tp_price
      params["tpTriggerBy"] = trigger_by
    if trigger_price:
      params["triggerPrice"] = trigger_price
      params["triggerBy"] = trigger_by
      if trigger_direction is not None:
        params["triggerDirection"] = trigger_direction

    logger.info("Placing order: %s %s %s qty=%s price=%s", symbol, side, order_type, qty, price)
    resp = self._session.place_order(**params)
    self._check_response(resp)
    result = resp.get("result", {})
    logger.info("Order placed: orderId=%s linkId=%s", result.get("orderId"), result.get("orderLinkId"))
    return result

  def cancel_order(
    self, symbol: str,
    order_id: str | None = None,
    order_link_id: str | None = None,
  ) -> dict:
    """Cancel an open order by orderId or orderLinkId."""
    symbol = _normalize_symbol(symbol)  # CS-040
    params: dict = {"category": "linear", "symbol": symbol}
    if order_id:
      params["orderId"] = order_id
    elif order_link_id:
      params["orderLinkId"] = order_link_id
    else:
      raise ValueError("Must provide order_id or order_link_id")

    logger.info("Cancelling order: symbol=%s id=%s link=%s", symbol, order_id, order_link_id)
    resp = self._session.cancel_order(**params)
    self._check_response(resp)
    return resp.get("result", {})

  def cancel_all_orders(self, symbol: str | None = None) -> dict:
    """Cancel all open orders, optionally filtered by symbol."""
    symbol = _normalize_symbol(symbol)  # CS-040
    params: dict = {"category": "linear", "settleCoin": "USDT"}
    if symbol:
      params["symbol"] = symbol
    logger.warning("Cancelling ALL orders: symbol=%s", symbol or "ALL")
    resp = self._session.cancel_all_orders(**params)
    self._check_response(resp)
    return resp.get("result", {})

  def amend_order(
    self, symbol: str,
    order_id: str | None = None,
    order_link_id: str | None = None,
    qty: str | None = None,
    price: str | None = None,
    trigger_price: str | None = None,
  ) -> dict:
    """Amend an existing order (change qty, price, or trigger price).

    For conditional orders (SL/TP), use trigger_price to update the
    trigger level without cancel+replace.
    """
    symbol = _normalize_symbol(symbol)  # CS-040
    params: dict = {"category": "linear", "symbol": symbol}
    if order_id:
      params["orderId"] = order_id
    elif order_link_id:
      params["orderLinkId"] = order_link_id
    if qty:
      params["qty"] = qty
    if price:
      params["price"] = price
    if trigger_price:
      params["triggerPrice"] = trigger_price

    logger.info("Amending order: symbol=%s qty=%s price=%s trigger=%s", symbol, qty, price, trigger_price)
    resp = self._session.amend_order(**params)
    self._check_response(resp)
    return resp.get("result", {})

  def get_open_orders(self, symbol: str | None = None) -> list[dict]:
    """Get all open/active orders (normal + conditional/stop).

    Bybit separates orders by type: default orderFilter="Order" only
    returns normal limit/market orders. Our SL orders use triggerPrice,
    making them conditional "StopOrder" type. We fetch both and combine.
    """
    symbol = _normalize_symbol(symbol)  # CS-040
    params: dict = {"category": "linear", "settleCoin": "USDT"}
    if symbol:
      params["symbol"] = symbol

    # Normal orders (TP limits, entry limits, etc.)
    resp_normal = self._session.get_open_orders(**params)
    self._check_response(resp_normal)
    normal = resp_normal.get("result", {}).get("list", [])

    # Conditional/stop orders (SL orders placed with triggerPrice)
    stop_params = {**params, "orderFilter": "StopOrder"}
    resp_stop = self._session.get_open_orders(**stop_params)
    self._check_response(resp_stop)
    stop = resp_stop.get("result", {}).get("list", [])

    return normal + stop

  def get_order_history(self, symbol: str | None = None, limit: int = 50) -> list[dict]:
    """Get closed/cancelled order history."""
    symbol = _normalize_symbol(symbol)  # CS-040
    params: dict = {"category": "linear", "limit": limit}
    if symbol:
      params["symbol"] = symbol
    resp = self._session.get_order_history(**params)
    self._check_response(resp)
    return resp.get("result", {}).get("list", [])

  # ── Position Management ──────────────────────────────────────────

  def set_leverage(self, symbol: str, buy_leverage: str, sell_leverage: str) -> dict:
    """Set leverage for a symbol (buy and sell separately)."""
    symbol = _normalize_symbol(symbol)  # CS-040
    logger.info("Setting leverage: %s buy=%s sell=%s", symbol, buy_leverage, sell_leverage)
    try:
      resp = self._session.set_leverage(
        category="linear", symbol=symbol,
        buyLeverage=buy_leverage, sellLeverage=sell_leverage,
      )
      return resp.get("result", {})
    except Exception as e:
      # 110043 = leverage not modified (already set) — treat as success
      if "110043" in str(e):
        return {}
      raise

  def switch_to_isolated(self, symbol: str, buy_leverage: str, sell_leverage: str) -> dict:
    """Switch symbol to isolated margin mode."""
    symbol = _normalize_symbol(symbol)  # CS-040
    logger.info("Switching to isolated margin: %s", symbol)
    try:
      resp = self._session.switch_margin_mode(
        category="linear", symbol=symbol,
        tradeMode=1,  # 1 = isolated
        buyLeverage=buy_leverage, sellLeverage=sell_leverage,
      )
      return resp.get("result", {})
    except Exception as e:
      # 110026 = already isolated, 100028 = UTA account (no isolated mode)
      if "110026" in str(e) or "100028" in str(e):
        return {}
      raise

  def get_position_mode(self, symbol: str = "BTCUSDT") -> str:
    """Return 'one_way', 'hedge', or 'unknown' based on the raw position
    rows Bybit returns for a probe symbol.

    The engine assumes one-way mode for sizing, reconciliation, and the
    "one position per (portfolio, symbol, side)" invariant. Hedge mode
    would break martingale's pyramiding behavior (new Buy adds to
    existing long) by creating a separate position row instead.

    Detection: in hedge mode Bybit returns TWO entries per symbol
    (positionIdx=1 for long-side, positionIdx=2 for short-side) even
    when both are flat. In one-way mode, ONE entry (positionIdx=0).
    BTCUSDT is a safe probe — it's always listed.
    """
    try:
      resp = self._session.get_positions(
        category="linear", symbol=_normalize_symbol(symbol),
      )
      self._check_response(resp)
    except Exception as e:
      logger.warning("get_position_mode probe failed for %s: %s", symbol, e)
      return "unknown"
    entries = resp.get("result", {}).get("list", []) or []
    if not entries:
      return "unknown"
    indices = {int(p.get("positionIdx", 0)) for p in entries}
    if indices == {0}:
      return "one_way"
    if indices & {1, 2}:
      return "hedge"
    return "unknown"

  def get_positions(self, symbol: str | None = None) -> list[dict]:
    """Get open positions (USDT perpetuals)."""
    symbol = _normalize_symbol(symbol)  # CS-040
    params: dict = {"category": "linear", "settleCoin": "USDT"}
    if symbol:
      params["symbol"] = symbol
    resp = self._session.get_positions(**params)
    self._check_response(resp)
    positions = []
    for p in resp.get("result", {}).get("list", []):
      size = float(p.get("size", 0))
      # Bybit occasionally returns tiny non-zero sizes for flat positions
      # (e.g. 1e-9 dust from prior partial fills). Using abs<epsilon
      # instead of ``== 0`` keeps dust out of the open-position list
      # so the engine doesn't try to "close" a position that isn't there.
      # Smallest real lot on Bybit linear perps is 0.001 — the 1e-8
      # ceiling sits safely below anything that could be a real trade.
      if abs(size) < 1e-8:
        continue
      positions.append({
        "symbol": p.get("symbol", ""),
        "side": p.get("side", ""),
        "size": size,
        "entry_price": float(p.get("avgPrice", 0)),
        "mark_price": float(p.get("markPrice", 0)),
        "unrealized_pnl": float(p.get("unrealisedPnl", 0)),
        "leverage": p.get("leverage", "1"),
        "liq_price": float(p.get("liqPrice", 0)) if p.get("liqPrice") else None,
        "margin_mode": "isolated" if p.get("tradeMode") == "1" else "cross",
        "position_idx": int(p.get("positionIdx", 0)),
      })
    return positions

  # ── Account ──────────────────────────────────────────────────────

  def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
    """Get wallet balance. Returns {total_equity, available, coins}."""
    resp = self._session.get_wallet_balance(accountType=account_type)
    self._check_response(resp)
    accounts = resp.get("result", {}).get("list", [])
    if not accounts:
      return {"total_equity": 0, "available": 0, "coins": []}
    acct = accounts[0]
    coins = []
    def _safe_float(v, default=0.0):
      """Bybit sometimes returns '' instead of 0 for empty balances."""
      try:
        return float(v) if v != "" else default
      except (ValueError, TypeError):
        return default

    for c in acct.get("coin", []):
      coins.append({
        "coin": c.get("coin", ""),
        "equity": _safe_float(c.get("equity", 0)),
        "available": _safe_float(c.get("availableToWithdraw", 0)),
        "unrealized_pnl": _safe_float(c.get("unrealisedPnl", 0)),
      })
    return {
      "total_equity": _safe_float(acct.get("totalEquity", 0)),
      "wallet_balance": _safe_float(acct.get("totalWalletBalance", 0)),
      "available": _safe_float(acct.get("totalAvailableBalance", 0)),
      "coins": coins,
    }

  def test_connection(self) -> bool:
    """Test if API keys are valid."""
    try:
      self._session.get_account_info()
      return True
    except Exception as e:
      logger.warning("Bybit connection test failed: %s", e)
      return False

  # ── Execution History ────────────────────────────────────────────

  def get_executions(self, symbol: str | None = None, limit: int = 100) -> list[dict]:
    """Get recent trade executions (fills)."""
    params: dict = {"category": "linear", "limit": limit}
    if symbol:
      params["symbol"] = symbol
    resp = self._session.get_executions(**params)
    self._check_response(resp)
    executions = []
    for t in resp.get("result", {}).get("list", []):
      executions.append({
        "symbol": t.get("symbol", ""),
        "side": t.get("side", ""),
        "qty": float(t.get("execQty", 0)),
        "price": float(t.get("execPrice", 0)),
        "fee": float(t.get("execFee", 0)),
        "exec_time": t.get("execTime", ""),
        "exec_type": t.get("execType", ""),
        "order_id": t.get("orderId", ""),
        "order_link_id": t.get("orderLinkId", ""),
        "is_maker": t.get("isMaker", "") == "true",
      })
    return executions

  # ── Funding Rate ─────────────────────────────────────────────────

  def get_funding_rate_history(self, symbol: str, limit: int = 50) -> list[dict]:
    """Get funding rate history for a symbol."""
    resp = self._session.get_funding_rate_history(
      category="linear", symbol=symbol, limit=limit,
    )
    self._check_response(resp)
    return [
      {
        "symbol": r.get("symbol", ""),
        "funding_rate": float(r.get("fundingRate", 0)),
        "funding_rate_timestamp": r.get("fundingRateTimestamp", ""),
      }
      for r in resp.get("result", {}).get("list", [])
    ]

  # ── Internal ─────────────────────────────────────────────────────

  @staticmethod
  def _check_response(resp: dict) -> None:
    """Raise BybitAPIError on non-zero retCode.

    Exception carries ``ret_code`` and ``ret_msg`` attributes; its string
    form matches the prior ``ValueError`` format so legacy callers doing
    ``"110043" in str(e)`` still work.
    """
    ret_code = resp.get("retCode", -1)
    if ret_code != 0:
      raise BybitAPIError(ret_code, resp.get("retMsg", "Unknown"))
