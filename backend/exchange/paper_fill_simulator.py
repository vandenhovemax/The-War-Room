"""Paper Fill Simulator — fills paper orders based on live ticker prices.

Monitors real-time price updates from Bybit public WebSocket and fills
pending paper orders when price conditions are met. Fires callbacks in
the exact same format as Bybit private WebSocket streams so OrderManager
processes them identically to real fills.
"""
from __future__ import annotations

from collections.abc import Callable

from backend.utils.logger import get_logger

logger = get_logger(__name__)


class PaperFillSimulator:
  """Simulates order fills using live market prices.

  Fill conditions mirror the backtester engine logic:
  - Market (no trigger): fills immediately at last_price
  - Limit Buy: fills when last_price <= limit_price
  - Limit Sell: fills when last_price >= limit_price
  - Stop Buy (SL for short): fills when last_price >= trigger_price
  - Stop Sell (SL for long): fills when last_price <= trigger_price
  """

  def __init__(
    self,
    paper_client,
    on_order: Callable[[dict], None] | None = None,
    on_execution: Callable[[dict], None] | None = None,
    slippage_pct: float = 0.01,
  ):
    self._client = paper_client
    self._on_order = on_order
    self._on_execution = on_execution
    self._slippage_pct = slippage_pct / 100  # Convert from % to decimal

  def on_price_update(self, symbol: str, last_price: float) -> None:
    """Called on every ticker update from public WebSocket.

    Checks all pending orders for this symbol and fills
    any whose conditions are met.
    """
    self._client.record_last_price(symbol, last_price)

    pending = self._client.get_pending_orders()
    for order in pending:
      if order["symbol"] != symbol:
        continue

      fill_price = self._check_fill_condition(order, last_price)
      if fill_price is not None:
        fill_price = self._apply_slippage(fill_price, order["side"])
        self._execute_fill(order["orderLinkId"], fill_price)

  def try_fill_order(self, order_link_id: str, last_price: float) -> None:
    """Try to fill a specific order immediately (for market orders)."""
    pending = self._client.get_pending_orders()
    for order in pending:
      if order["orderLinkId"] != order_link_id:
        continue

      fill_price = self._check_fill_condition(order, last_price)
      if fill_price is not None:
        fill_price = self._apply_slippage(fill_price, order["side"])
        self._execute_fill(order_link_id, fill_price)
      break

  def _check_fill_condition(self, order: dict, last_price: float) -> float | None:
    """Check if an order's fill condition is met.

    Returns the fill price if condition is met, None otherwise.
    """
    order_type = order.get("orderType", "")
    side = order.get("side", "")
    trigger_price_str = order.get("triggerPrice")
    limit_price_str = order.get("price")

    # Market order with no trigger — fill immediately
    if order_type == "Market" and not trigger_price_str:
      return last_price

    # Market order with trigger (stop order — used for SL)
    if order_type == "Market" and trigger_price_str:
      trigger = float(trigger_price_str)
      if side == "Buy" and last_price >= trigger:
        return last_price  # SL for short: buy when price rises to trigger
      if side == "Sell" and last_price <= trigger:
        return last_price  # SL for long: sell when price falls to trigger
      return None

    # Limit order
    if order_type == "Limit" and limit_price_str:
      limit = float(limit_price_str)
      if side == "Buy" and last_price <= limit:
        return limit  # Fill at limit price (better than market)
      if side == "Sell" and last_price >= limit:
        return limit
      return None

    return None

  def _apply_slippage(self, price: float, side: str) -> float:
    """Apply simulated slippage to fill price.

    Slippage always moves the price against the trader:
    - Buy: price goes up (pay more)
    - Sell: price goes down (receive less)
    """
    if self._slippage_pct <= 0:
      return price

    if side == "Buy":
      return price * (1 + self._slippage_pct)
    else:
      return price * (1 - self._slippage_pct)

  def _execute_fill(self, order_link_id: str, fill_price: float) -> None:
    """Execute a paper fill and fire callbacks."""
    exec_data = self._client.fill_order(order_link_id, fill_price)
    if not exec_data:
      return  # Order already filled or cancelled

    # Fire order update callback (same format as Bybit WS)
    if self._on_order:
      order_update = {
        "orderLinkId": exec_data["orderLinkId"],
        "orderId": exec_data["orderId"],
        "symbol": exec_data["symbol"],
        "side": exec_data["side"],
        "orderStatus": "Filled",
        "cumExecQty": exec_data["cumExecQty"],
        "avgPrice": exec_data["avgPrice"],
      }
      try:
        self._on_order(order_update)
      except Exception as e:
        logger.error("Paper on_order callback error: %s", e)

    # Fire execution callback (same format as Bybit WS)
    if self._on_execution:
      try:
        self._on_execution(exec_data)
      except Exception as e:
        logger.error("Paper on_execution callback error: %s", e)
