"""Paper Trading Client — simulates BybitTradingClient without real exchange.

Drop-in replacement that maintains a virtual wallet and in-memory order book.
Uses Bybit public API (no auth) for instrument info only.
All method signatures match BybitTradingClient exactly.
"""
from __future__ import annotations

import threading
import uuid

import httpx

from backend.utils.logger import get_logger

logger = get_logger(__name__)

TESTNET_URL = "https://api-testnet.bybit.com"
MAINNET_URL = "https://api.bybit.com"

# Bybit USDT perps taker fee
TAKER_FEE_RATE = 0.00055


class PaperTradingClient:
  """Simulated Bybit trading client for paper trading.

  Maintains:
  - Virtual wallet with configurable starting balance
  - In-memory order book (pending limit/stop orders)
  - Instrument info cache (from Bybit public API)
  - Realized PnL tracking
  """

  def __init__(self, initial_balance: float = 10_000.0, testnet: bool = True):
    self._initial_balance = initial_balance
    self.testnet = testnet
    self._base_url = TESTNET_URL if testnet else MAINNET_URL

    # Virtual wallet
    self._realized_pnl: float = 0.0

    # Order book: order_link_id -> order dict
    self._open_orders: dict[str, dict] = {}
    self._order_counter: int = 0
    self._lock = threading.Lock()

    # Last known prices per symbol (for mark-to-market)
    self._last_prices: dict[str, float] = {}

    # Open position margin tracking: position_db_id -> notional value
    self._position_margins: dict[int, float] = {}

    # Instrument info cache: symbol -> parsed info
    self._instruments: dict[str, dict] = {}

    # Fill simulator reference (set after construction)
    self._fill_simulator = None

    # HTTP client for public API
    self._http = httpx.Client(timeout=10.0)

    logger.info(
      "Paper trading client initialized: balance=$%.2f testnet=%s",
      initial_balance, testnet,
    )

  def set_fill_simulator(self, simulator) -> None:
    """Wire the fill simulator for immediate market order fills."""
    self._fill_simulator = simulator

  # ── Static Methods ─────────────────────────────────────────────

  @staticmethod
  def generate_order_link_id() -> str:
    """Generate a unique client order ID (max 36 chars)."""
    return str(uuid.uuid4())[:36]

  # ── Instrument Info (Public API) ───────────────────────────────

  def get_instrument_info(self, symbol: str) -> dict:
    """Get instrument specs from Bybit public API (cached)."""
    if symbol in self._instruments:
      return self._instruments[symbol]

    try:
      resp = self._http.get(
        f"{self._base_url}/v5/market/instruments-info",
        params={"category": "linear", "symbol": symbol},
      )
      data = resp.json()

      if data.get("retCode") != 0:
        raise ValueError(f"Bybit API error: {data.get('retMsg', 'Unknown')}")

      items = data.get("result", {}).get("list", [])
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

    except httpx.HTTPError as e:
      logger.error("Failed to fetch instrument info for %s: %s", symbol, e)
      raise ValueError(f"Cannot fetch instrument info for {symbol}: {e}")

  def clear_instrument_cache(self) -> None:
    """Clear cached instrument info."""
    self._instruments.clear()

  # ── Order Management ───────────────────────────────────────────

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
    """Place a simulated order.

    trigger_direction is accepted to match BybitTradingClient.place_order
    (Bybit V5 requires it on stop orders). The paper fill simulator infers
    direction from side + last_price comparison, so the value is stored
    for parity with real-Bybit order rows but not used by the sim.
    """
    if not order_link_id:
      order_link_id = self.generate_order_link_id()

    with self._lock:
      self._order_counter += 1
      order_id = f"paper_{self._order_counter}"

    order = {
      "orderId": order_id,
      "orderLinkId": order_link_id,
      "symbol": symbol,
      "side": side,
      "orderType": order_type,
      "qty": qty,
      "price": price,
      "triggerPrice": trigger_price,
      "triggerBy": trigger_by,
      "triggerDirection": trigger_direction,
      "timeInForce": time_in_force,
      "reduceOnly": reduce_only,
      "orderStatus": "New",
      "cumExecQty": "0",
      "avgPrice": "0",
    }

    is_market_no_trigger = order_type == "Market" and not trigger_price

    if is_market_no_trigger:
      # Market orders fill immediately — let the simulator handle it
      with self._lock:
        self._open_orders[order_link_id] = order
      logger.info(
        "Paper market order queued for immediate fill: %s %s %s qty=%s",
        symbol, side, order_type, qty,
      )
      # Trigger immediate fill via simulator
      if self._fill_simulator:
        last = self._last_prices.get(symbol, 0)
        if last > 0:
          self._fill_simulator.try_fill_order(order_link_id, last)
    else:
      # Limit or stop orders — store for simulator to check
      with self._lock:
        self._open_orders[order_link_id] = order
      logger.info(
        "Paper order stored: %s %s %s qty=%s price=%s trigger=%s",
        symbol, side, order_type, qty, price, trigger_price,
      )

    return {"orderId": order_id, "orderLinkId": order_link_id}

  def cancel_order(
    self, symbol: str,
    order_id: str | None = None,
    order_link_id: str | None = None,
  ) -> dict:
    """Cancel a pending paper order."""
    with self._lock:
      if order_link_id and order_link_id in self._open_orders:
        del self._open_orders[order_link_id]
        logger.info("Paper order cancelled: linkId=%s", order_link_id)
        return {}

      if order_id:
        to_remove = None
        for lid, o in self._open_orders.items():
          if o.get("orderId") == order_id:
            to_remove = lid
            break
        if to_remove:
          del self._open_orders[to_remove]
          logger.info("Paper order cancelled: orderId=%s", order_id)
          return {}

    return {}

  def cancel_all_orders(self, symbol: str | None = None) -> dict:
    """Cancel all pending paper orders, optionally filtered by symbol."""
    with self._lock:
      if symbol:
        to_remove = [
          lid for lid, o in self._open_orders.items()
          if o["symbol"] == symbol
        ]
      else:
        to_remove = list(self._open_orders.keys())

      for lid in to_remove:
        del self._open_orders[lid]

    logger.info("Paper orders cancelled: %d orders (symbol=%s)", len(to_remove), symbol or "ALL")
    return {}

  def amend_order(
    self, symbol: str,
    order_id: str | None = None,
    order_link_id: str | None = None,
    qty: str | None = None,
    price: str | None = None,
    trigger_price: str | None = None,
  ) -> dict:
    """Amend a pending paper order (qty, price, or trigger price)."""
    with self._lock:
      order = self._open_orders.get(order_link_id)
      if not order and order_id:
        for o in self._open_orders.values():
          if o.get("orderId") == order_id:
            order = o
            break

      if order:
        if qty:
          order["qty"] = qty
        if price:
          order["price"] = price
        if trigger_price:
          order["triggerPrice"] = trigger_price

    return {}

  def get_open_orders(self, symbol: str | None = None) -> list[dict]:
    """Get all pending paper orders."""
    with self._lock:
      orders = list(self._open_orders.values())

    if symbol:
      orders = [o for o in orders if o["symbol"] == symbol]
    return orders

  # ── Position Management (no-ops in paper mode) ─────────────────

  def get_positions(self, symbol: str | None = None) -> list[dict]:
    """Return empty — positions tracked in DB, not exchange."""
    return []

  def set_leverage(self, symbol: str, buy_leverage: str, sell_leverage: str) -> dict:
    """No-op — leverage is conceptual in paper mode."""
    return {}

  def switch_to_isolated(self, symbol: str, buy_leverage: str, sell_leverage: str) -> dict:
    """No-op — margin mode is conceptual in paper mode."""
    return {}

  # ── Account ────────────────────────────────────────────────────

  def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
    """Return virtual wallet balance with margin deducted from available."""
    equity = self._initial_balance + self._realized_pnl
    margin_in_use = sum(self._position_margins.values())
    available = max(0.0, equity - margin_in_use)
    return {
      "total_equity": equity,
      "wallet_balance": equity,
      "available": available,
      "coins": [{
        "coin": "USDT",
        "equity": equity,
        "available": available,
        "unrealized_pnl": 0,
      }],
    }

  def test_connection(self) -> bool:
    """Always succeeds — no exchange connection needed."""
    return True

  # ── Execution History ──────────────────────────────────────────

  def get_executions(self, symbol: str | None = None, limit: int = 100) -> list[dict]:
    """Return empty — executions tracked via callbacks."""
    return []

  def get_funding_rate_history(self, symbol: str, limit: int = 50) -> list[dict]:
    """Return empty — no funding in paper mode."""
    return []

  # ── Paper-Specific Methods ─────────────────────────────────────

  def get_pending_orders(self) -> list[dict]:
    """Get all unfilled orders for the fill simulator to iterate."""
    with self._lock:
      return list(self._open_orders.values())

  def fill_order(self, order_link_id: str, fill_price: float) -> dict | None:
    """Mark an order as filled and return execution event data.

    Returns None if order not found (already filled/cancelled).
    """
    with self._lock:
      order = self._open_orders.pop(order_link_id, None)

    if not order:
      return None

    qty = float(order["qty"])
    fee = fill_price * qty * TAKER_FEE_RATE

    logger.info(
      "Paper fill: %s %s %s qty=%.6f @ %.4f fee=%.4f",
      order["symbol"], order["side"], order["orderType"],
      qty, fill_price, fee,
    )

    return {
      "orderLinkId": order_link_id,
      "orderId": order["orderId"],
      "symbol": order["symbol"],
      "side": order["side"],
      "execQty": str(qty),
      "execPrice": str(fill_price),
      "execFee": str(fee),
      "cumExecQty": str(qty),
      "avgPrice": str(fill_price),
      "isMaker": "false",
    }

  def record_last_price(self, symbol: str, price: float) -> None:
    """Track last known price per symbol."""
    self._last_prices[symbol] = price

  def add_realized_pnl(self, pnl: float) -> None:
    """Add to realized PnL (called by fill simulator or externally)."""
    self._realized_pnl += pnl

  def reserve_margin(self, position_db_id: int, notional: float) -> None:
    """Reserve margin for an open position (entry_price * qty).

    Called by PositionManager after a fill to track margin in use.
    """
    with self._lock:
      self._position_margins[position_db_id] = notional

  def release_margin(self, position_db_id: int) -> None:
    """Release margin when a position closes.

    Called by PositionManager/OrderManager on position close.
    """
    with self._lock:
      self._position_margins.pop(position_db_id, None)
