"""Order Manager — full order lifecycle for live trading.

Handles: placement, TTL timeouts, partial fills, ghost order prevention,
exit order management (SL/TP), and reconciliation with exchange state.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.config.settings import get_settings
from backend.constants import COMMISSION_PCT
from backend.db.models import (
  JournalTrade,
  LiveEvent,
  Order,
  Portfolio,
  PortfolioAsset,
  Position,
  Strategy,
  StrategyConfig,
  Trade,
)
from backend.exchange.bybit_trading import BybitTradingClient, is_retryable_bybit_error
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class OrderManager:
  """Manages the complete order lifecycle for live trading.

  Responsibilities:
  - Place entry orders (limit with TTL or market)
  - Place exit orders (SL as market stop, TP as limit)
  - Track partial fills and adjust exit quantities
  - Cancel unfilled entries after TTL timeout
  - Reconcile local DB state vs exchange open orders
  - Clean up ghost/orphan orders
  """

  def __init__(
    self,
    trading_client: BybitTradingClient,
    db_factory: Callable[[], Session],
    on_position_opened: Callable[[int], None] | None = None,
    on_position_closed: Callable[[int], None] | None = None,
  ):
    self._client = trading_client
    self._db_factory = db_factory
    self._settings = get_settings()
    self._on_position_opened = on_position_opened
    self._on_position_closed = on_position_closed
    # Active TTL timers: order_link_id → asyncio.Task
    self._ttl_timers: dict[str, asyncio.Task] = {}
    # Track pending entries awaiting fill: order_link_id → db Order.id
    self._pending_entries: dict[str, int] = {}
    # Carry trail_distance from entry submission to fill handler (in-memory only)
    self._trail_distances: dict[str, float] = {}
    # Carry batch_id from entry submission to fill handler (for per-batch martingale)
    self._batch_ids: dict[str, int] = {}  # order_link_id → batch_id
    # Thread-safe coroutine scheduler (set by EngineCoordinator)
    self._scheduler: Callable | None = None
    # CS-039: idempotency guard — on_order_update can fire twice for the
    # same orderLinkId (WS private stream AND REST poll for market orders).
    # The previous DB-only check (prev_status != "filled") is TOCTOU-unsafe
    # under concurrent callers. Claim the link-id under a lock before
    # routing to _on_entry_filled / _on_exit_filled so the second call
    # short-circuits. Same guard protects on_execution so commission
    # doesn't accumulate twice for the same fill.
    self._fill_processed_lock = threading.Lock()
    self._fill_processed_links: set[str] = set()
    self._execution_processed_keys: set[tuple[str, str]] = set()

  def set_scheduler(self, scheduler_fn: Callable) -> None:
    """Set a thread-safe coroutine scheduler (from EngineCoordinator._schedule)."""
    self._scheduler = scheduler_fn

  def _schedule_coro(self, coro) -> None:
    """Schedule a coroutine safely from any thread context.

    Tries asyncio.create_task first (works if on event loop thread),
    falls back to the injected scheduler (works from WS threads).
    """
    try:
      asyncio.create_task(coro)
    except RuntimeError:
      if self._scheduler:
        self._scheduler(coro)
      else:
        logger.error("Cannot schedule coroutine — no event loop and no scheduler set")

  async def _call_exchange(self, fn):
    """Run a blocking exchange REST call in a thread pool executor.

    Prevents blocking the asyncio event loop while waiting for Bybit
    REST responses (100-500ms per call). H1 audit fix.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn)

  # ── Entry Orders ──────────────────────────────────────────────────

  async def submit_entry_order(
    self,
    portfolio_id: int,
    symbol: str,
    side: str,
    qty: float,
    order_type: str = "Limit",
    limit_price: float | None = None,
    sl_price: float | None = None,
    tp_price: float | None = None,
    trail_distance: float | None = None,
    ttl_seconds: int | None = None,
    batch_id: int | None = None,
  ) -> int | None:
    """Place an entry order and start TTL timer.

    Args:
      portfolio_id: Which portfolio this trade belongs to
      symbol: e.g. "BTCUSDT"
      side: "Buy" or "Sell"
      qty: Position size (already calculated by PositionManager)
      order_type: "Limit" or "Market"
      limit_price: Required for Limit orders
      sl_price: Stop loss price (stored, placed after fill)
      tp_price: Take profit price (stored, placed after fill)
      trail_distance: Trailing stop distance (handled by TSL tracker)
      ttl_seconds: Override default entry TTL

    Returns: DB Order.id or None on failure
    """
    if ttl_seconds is None:
      ttl_seconds = self._settings.entry_order_ttl_seconds

    order_link_id = BybitTradingClient.generate_order_link_id()
    if batch_id is not None:
      self._batch_ids[order_link_id] = batch_id
    info = await self._call_exchange(lambda: self._client.get_instrument_info(symbol))
    qty_str = self._round_qty(qty, info["lot_size"])
    price_str = None
    if limit_price is not None:
      price_str = self._round_price(limit_price, info["tick_size"])

    # Record order in DB before sending to exchange
    db = self._db_factory()
    try:
      db_order = Order(
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=qty,
        price=limit_price,
        sl_price=sl_price,
        tp_price=tp_price,
        status="submitting",
        order_link_id=order_link_id,
        portfolio_id=portfolio_id,
        role="entry",
        intended_qty=qty,
        filled_qty=0.0,
        time_in_force="GTC" if order_type == "Limit" else "IOC",
        ttl_seconds=ttl_seconds,
      )
      db.add(db_order)
      db.commit()
      db.refresh(db_order)
      order_db_id = db_order.id

      self._log_event(db, portfolio_id, "order_submitting", symbol, {
        "order_link_id": order_link_id, "side": side, "qty": qty_str,
        "price": price_str, "type": order_type, "ttl": ttl_seconds,
      })

      # Place on exchange (non-blocking — runs in thread pool)
      result = await self._call_exchange(lambda: self._client.place_order(
        symbol=symbol,
        side=side,
        order_type=order_type,
        qty=qty_str,
        price=price_str,
        time_in_force="GTC" if order_type == "Limit" else "IOC",
        order_link_id=order_link_id,
      ))

      exchange_order_id = result.get("orderId", "")
      db_order.exchange_order_id = exchange_order_id
      db_order.status = "open"
      db.commit()

      self._log_event(db, portfolio_id, "order_placed", symbol, {
        "order_link_id": order_link_id, "exchange_order_id": exchange_order_id,
        "side": side, "qty": qty_str, "price": price_str,
      })

      # Track trail_distance for TSL registration after fill
      if trail_distance and trail_distance > 0:
        self._trail_distances[order_link_id] = trail_distance

      # Track and start TTL timer for limit orders
      self._pending_entries[order_link_id] = order_db_id
      if order_type == "Limit" and ttl_seconds > 0:
        task = asyncio.create_task(
          self._entry_ttl_timer(order_link_id, symbol, ttl_seconds)
        )
        self._ttl_timers[order_link_id] = task

      logger.info(
        "Entry order placed: %s %s %s qty=%s price=%s ttl=%ds linkId=%s",
        symbol, side, order_type, qty_str, price_str, ttl_seconds, order_link_id,
      )

      # For Market orders: poll fill via REST (don't rely on private WS)
      if order_type == "Market":
        await asyncio.sleep(1)  # Give exchange time to settle
        try:
          history = await self._call_exchange(lambda: self._client.get_order_history(symbol, limit=5))
          for h in history:
            if h.get("orderLinkId") == order_link_id and h.get("orderStatus") == "Filled":
              logger.info("Market order fill confirmed via REST poll: %s", order_link_id)
              # CS-002: on_order_update → _on_entry_filled → place_sl_order
              # contains a sync retry loop with time.sleep (up to 0.75s).
              # Directly awaiting these sync calls blocks the event loop
              # for the full retry budget, stalling WS handlers and TSL
              # amendments for every other position. Push to an executor
              # thread — place_sl_order's own comment assumes that context.
              def _process_fill(fill=h):
                self.on_order_update(fill)
                self.on_execution(fill)
              await self._call_exchange(_process_fill)
              break
          else:
            logger.warning("Market order fill not yet confirmed via REST: %s", order_link_id)
        except Exception as poll_err:
          logger.error("REST fill poll failed: %s", poll_err)

      return order_db_id

    except Exception as e:
      logger.error("Failed to place entry order: %s", e)
      # Mark as failed in DB
      if 'db_order' in dir() and db_order.id:
        db_order.status = "failed"
        db_order.cancel_reason = str(e)[:200]
        db.commit()
        self._log_event(db, portfolio_id, "order_failed", symbol, {
          "order_link_id": order_link_id, "error": str(e)[:200],
        })
      return None
    finally:
      db.close()

  # ── Exit Orders ───────────────────────────────────────────────────

  def place_sl_order(
    self,
    position_db_id: int,
    symbol: str,
    side: str,
    qty: float,
    sl_price: float,
  ) -> str | None:
    """Place a stop-loss order (always market trigger).

    The SL side is opposite to the position side.
    Returns order_link_id on success.
    """
    close_side = "Sell" if side == "Buy" else "Buy"
    order_link_id = BybitTradingClient.generate_order_link_id()
    info = self._client.get_instrument_info(symbol)
    qty_str = self._round_qty(qty, info["lot_size"])
    sl_str = self._round_price(sl_price, info["tick_size"])

    db = self._db_factory()
    try:
      # Record SL order in DB
      db_order = Order(
        symbol=symbol,
        side=close_side,
        order_type="Market",
        quantity=qty,
        sl_price=sl_price,
        status="submitting",
        order_link_id=order_link_id,
        portfolio_id=self._get_position_portfolio(db, position_db_id),
        position_id_fk=position_db_id,
        role="sl",
        intended_qty=qty,
        reduce_only=True,
      )
      db.add(db_order)
      db.commit()

      # Place conditional stop order on exchange — with retry.
      # Runs on a thread-pool worker (not the event loop), so time.sleep
      # is safe. Exponential backoff (0.25 → 0.5s) caps worst-case wait
      # at 0.75s before the final raise; DB session is released in the
      # outer `finally` so a slow retry cannot hold a SQLite lock after
      # this method returns. CS-001: permanent Bybit errors (insufficient
      # balance, invalid qty) raise immediately so the caller's emergency
      # close fires without burning the full retry budget first.
      import time as _time
      result = None
      last_err = None
      backoffs = (0.25, 0.5)  # delay before attempts 2 and 3
      for attempt in range(3):
        try:
          # triggerDirection: 2 = trigger when price falls (long SL), 1 = trigger when price rises (short SL)
          trig_dir = 2 if side == "Buy" else 1
          result = self._client.place_order(
            symbol=symbol,
            side=close_side,
            order_type="Market",
            qty=qty_str,
            trigger_price=sl_str,
            trigger_by="LastPrice",
            reduce_only=True,
            order_link_id=order_link_id,
            trigger_direction=trig_dir,
          )
          break  # Success
        except Exception as api_err:
          last_err = api_err
          if not is_retryable_bybit_error(api_err):
            logger.error(
              "SL API permanent error for %s (not retrying): %s",
              symbol, api_err,
            )
            raise
          logger.warning("SL API attempt %d/3 failed for %s: %s", attempt + 1, symbol, api_err)
          if attempt < 2:
            _time.sleep(backoffs[attempt])
      if result is None:
        raise last_err or Exception("SL placement failed after 3 retries")

      db_order.exchange_order_id = result.get("orderId", "")
      db_order.status = "open"
      db.commit()

      # Update position with SL link
      pos = db.get(Position, position_db_id)
      if pos:
        pos.sl_order_link_id = order_link_id
        db.commit()

      self._log_event(
        db, db_order.portfolio_id, "sl_placed", symbol,
        {"order_link_id": order_link_id, "sl_price": sl_price, "qty": qty_str},
      )

      logger.info("SL placed: %s %s @ %s qty=%s", symbol, close_side, sl_str, qty_str)
      return order_link_id

    except Exception as e:
      logger.error("Failed to place SL order: %s", e)
      return None
    finally:
      db.close()

  def modify_sl_order(self, position_db_id: int, new_sl_price: float) -> bool:
    """Modify an existing SL order's trigger price on the exchange.

    Called when the strategy moves SL (e.g., to breakeven after TP1).
    Tries amend first; falls back to cancel + replace if amend fails.
    Returns True on success.
    """
    db = self._db_factory()
    try:
      pos = db.get(Position, position_db_id)
      if not pos or not pos.is_open or not pos.sl_order_link_id:
        logger.warning("modify_sl: no open position or SL link for id=%d", position_db_id)
        return False

      # Find the live SL order
      sl_order = db.query(Order).filter(
        Order.order_link_id == pos.sl_order_link_id,
        Order.role == "sl",
        Order.status == "open",
      ).first()

      if not sl_order:
        logger.warning("modify_sl: no open SL order found for link=%s", pos.sl_order_link_id)
        return False

      info = self._client.get_instrument_info(pos.symbol)
      new_sl_str = self._round_price(new_sl_price, info["tick_size"])
      old_sl = pos.sl_price

      # Try amend first (avoids cancel+replace race condition)
      try:
        self._client.amend_order(
          symbol=pos.symbol,
          order_link_id=pos.sl_order_link_id,
          trigger_price=new_sl_str,
        )
        logger.info("SL amended: %s %s -> %s (was %s)", pos.symbol, pos.sl_order_link_id, new_sl_str, old_sl)
      except Exception as amend_err:
        # Fallback: cancel old SL and place new one
        logger.warning("SL amend failed (%s), falling back to cancel+replace", amend_err)
        try:
          self._client.cancel_order(symbol=pos.symbol, order_link_id=pos.sl_order_link_id)
        except Exception as cancel_err:
          logger.error("Failed to cancel old SL: %s", cancel_err)
          return False

        sl_order.status = "cancelled"
        sl_order.cancel_reason = "sl_modified"
        db.commit()

        # Place replacement SL (synchronous, returns new link_id)
        new_link = self.place_sl_order(
          position_db_id, pos.symbol, pos.side, pos.quantity, new_sl_price,
        )
        if not new_link:
          logger.error("Failed to place replacement SL for %s", pos.symbol)
          return False
        return True

      # Update DB records after successful amend
      sl_order.sl_price = new_sl_price
      pos.sl_price = new_sl_price
      db.commit()

      self._log_event(
        db, pos.portfolio_id, "sl_modified", pos.symbol,
        {"old_sl": old_sl, "new_sl": new_sl_price, "order_link_id": pos.sl_order_link_id},
      )
      return True

    except Exception as e:
      logger.error("Failed to modify SL order: %s", e)
      return False
    finally:
      db.close()

  async def place_tp_order(
    self,
    position_db_id: int,
    symbol: str,
    side: str,
    qty: float,
    tp_price: float,
    ttl_seconds: int | None = None,
    qty_percent: float = 100.0,
  ) -> str | None:
    """Place a take-profit limit order with TTL.

    If the TP limit isn't filled within TTL, it gets cancelled and
    replaced with a market close. Returns order_link_id on success.

    qty_percent: Percentage of position to close (default 100 = full close).
    Values < 100 create a partial exit — position stays open with reduced qty.
    """
    # Apply qty_percent to get actual exit qty
    if qty_percent < 100.0:
      qty = qty * (qty_percent / 100.0)
    if ttl_seconds is None:
      ttl_seconds = self._settings.tp_order_ttl_seconds

    close_side = "Sell" if side == "Buy" else "Buy"
    order_link_id = BybitTradingClient.generate_order_link_id()
    info = await self._call_exchange(lambda: self._client.get_instrument_info(symbol))
    qty_str = self._round_qty(qty, info["lot_size"])
    tp_str = self._round_price(tp_price, info["tick_size"])

    db = self._db_factory()
    try:
      tp_role = "tp1" if qty_percent < 100.0 else "tp"
      db_order = Order(
        symbol=symbol,
        side=close_side,
        order_type="Limit",
        quantity=qty,
        price=tp_price,
        status="submitting",
        order_link_id=order_link_id,
        portfolio_id=self._get_position_portfolio(db, position_db_id),
        position_id_fk=position_db_id,
        role=tp_role,
        intended_qty=qty,
        reduce_only=True,
        time_in_force="GTC",
        ttl_seconds=ttl_seconds,
      )
      db.add(db_order)
      db.commit()

      result = await self._call_exchange(lambda: self._client.place_order(
        symbol=symbol,
        side=close_side,
        order_type="Limit",
        qty=qty_str,
        price=tp_str,
        time_in_force="GTC",
        reduce_only=True,
        order_link_id=order_link_id,
      ))

      db_order.exchange_order_id = result.get("orderId", "")
      db_order.status = "open"
      db.commit()

      # Update position with TP link
      pos = db.get(Position, position_db_id)
      if pos:
        pos.tp_order_link_id = order_link_id
        db.commit()

      # Start TP TTL timer
      if ttl_seconds > 0:
        task = asyncio.create_task(
          self._tp_ttl_timer(order_link_id, symbol, position_db_id, qty, ttl_seconds)
        )
        self._ttl_timers[order_link_id] = task

      self._log_event(
        db, db_order.portfolio_id, "tp_placed", symbol,
        {"order_link_id": order_link_id, "tp_price": tp_price, "qty": qty_str, "ttl": ttl_seconds},
      )

      logger.info("TP placed: %s %s @ %s qty=%s ttl=%ds", symbol, close_side, tp_str, qty_str, ttl_seconds)
      return order_link_id

    except Exception as e:
      logger.error("Failed to place TP order: %s", e)
      return None
    finally:
      db.close()

  def place_market_close(
    self,
    position_db_id: int,
    symbol: str,
    side: str,
    qty: float,
    reason: str = "manual",
  ) -> str | None:
    """Place a market order to close a position immediately.

    Args:
      reason: "manual", "tp_timeout", "circuit_breaker", "deactivation"
    """
    close_side = "Sell" if side == "Buy" else "Buy"
    order_link_id = BybitTradingClient.generate_order_link_id()
    info = self._client.get_instrument_info(symbol)
    qty_str = self._round_qty(qty, info["lot_size"])

    db = self._db_factory()
    try:
      db_order = Order(
        symbol=symbol,
        side=close_side,
        order_type="Market",
        quantity=qty,
        status="submitting",
        order_link_id=order_link_id,
        portfolio_id=self._get_position_portfolio(db, position_db_id),
        position_id_fk=position_db_id,
        role="close",
        intended_qty=qty,
        reduce_only=True,
        time_in_force="IOC",
      )
      db.add(db_order)
      db.commit()

      result = self._client.place_order(
        symbol=symbol,
        side=close_side,
        order_type="Market",
        qty=qty_str,
        reduce_only=True,
        order_link_id=order_link_id,
      )

      db_order.exchange_order_id = result.get("orderId", "")
      db_order.status = "open"  # Wait for execution callback to confirm fill
      db.commit()

      self._log_event(
        db, db_order.portfolio_id, "market_close", symbol,
        {"order_link_id": order_link_id, "qty": qty_str, "reason": reason},
      )

      logger.info("Market close: %s %s qty=%s reason=%s", symbol, close_side, qty_str, reason)
      return order_link_id

    except Exception as e:
      err_str = str(e)
      logger.error("Failed to place market close: %s", e)

      # ErrCode 110017: "current position is zero" — already closed on Bybit.
      # Close the position in DB so the UI doesn't show a ghost position.
      if "110017" in err_str:
        logger.info("Position already closed on Bybit (110017) — closing in DB: %s pos=%s", symbol, position_db_id)
        try:
          pos = db.get(Position, position_db_id)
          if pos and pos.is_open:
            pos.is_open = False
            pos.closed_at = datetime.now(timezone.utc)
            db_order.status = "cancelled"
            db.commit()
            # Cancel any remaining exit orders on exchange
            self._cancel_other_exits(db, position_db_id, order_link_id)
            logger.info("Ghost position cleaned: %s pos=%s", symbol, position_db_id)
            # Notify coordinator to clean dedup cache (Bug 42)
            if self._on_position_closed:
              try:
                self._on_position_closed(pos.id)
              except Exception as cb_err:
                logger.error("on_position_closed callback error (ghost): %s", cb_err)
        except Exception as cleanup_err:
          logger.error("Failed to clean ghost position: %s", cleanup_err)
        return order_link_id  # Return non-None so caller knows it was handled

      return None
    finally:
      db.close()

  # ── WebSocket Callbacks ───────────────────────────────────────────

  def on_order_update(self, data: dict) -> None:
    """Handle order status update from WebSocket private stream.

    Called by BybitWSManager when an order status changes.
    """
    order_link_id = data.get("orderLinkId", "")
    status = data.get("orderStatus", "")
    symbol = data.get("symbol", "")
    filled_qty = float(data.get("cumExecQty", 0))
    avg_price = float(data.get("avgPrice", 0)) if data.get("avgPrice") else None

    if not order_link_id:
      return

    db = self._db_factory()
    try:
      db_order = db.query(Order).filter(
        Order.order_link_id == order_link_id
      ).first()
      if not db_order:
        logger.warning("Order update for unknown linkId: %s", order_link_id)
        return

      prev_status = db_order.status

      # Map Bybit statuses to our internal statuses
      if status in ("New", "Untriggered"):
        db_order.status = "open"
      elif status == "PartiallyFilled":
        db_order.status = "partial"
        db_order.filled_qty = filled_qty
        db_order.avg_fill_price = avg_price
      elif status == "Filled":
        db_order.status = "filled"
        db_order.filled_qty = filled_qty
        db_order.avg_fill_price = avg_price
        self._cancel_ttl_timer(order_link_id)
      elif status in ("Cancelled", "Rejected", "Deactivated"):
        db_order.status = "cancelled"
        db_order.cancel_reason = data.get("rejectReason", status)
        self._cancel_ttl_timer(order_link_id)

      db.commit()

      # Handle fill completion — only process the FIRST fill notification
      # (both WS and REST poll can fire for the same order). CS-039:
      # TOCTOU-safe via an idempotency lock on orderLinkId; the prior
      # DB check alone could let two threads past the "prev_status !=
      # filled" gate and double-insert the position.
      should_process_fill = False
      if status == "Filled" and prev_status != "filled":
        with self._fill_processed_lock:
          if order_link_id not in self._fill_processed_links:
            self._fill_processed_links.add(order_link_id)
            should_process_fill = True

      if should_process_fill and db_order.role == "entry":
        self._on_entry_filled(db, db_order)
      elif should_process_fill and db_order.role in ("sl", "tp", "tp1", "close"):
        self._on_exit_filled(db, db_order)
      elif status in ("Cancelled", "Rejected") and db_order.role == "sl":
        # SL was cancelled externally — this is dangerous, log a warning
        logger.warning("SL order cancelled externally! linkId=%s symbol=%s", order_link_id, symbol)
        self._log_event(
          db, db_order.portfolio_id, "sl_cancelled_external", symbol,
          {"order_link_id": order_link_id, "reason": data.get("rejectReason", "")},
        )

    except Exception as e:
      logger.error("Error handling order update: %s", e)
    finally:
      db.close()

  def on_execution(self, data: dict) -> None:
    """Handle execution (fill) update from WebSocket or REST order history.

    Records commission fees per execution.
    Accepts both WS field names (execFee) and REST field names (cumExecFee).
    """
    order_link_id = data.get("orderLinkId", "")
    # Bug 43: REST order history uses cumExecFee, WS uses execFee
    exec_fee = float(data.get("execFee", 0)) or float(data.get("cumExecFee", 0))
    exec_qty = float(data.get("execQty", 0)) or float(data.get("cumExecQty", 0))
    exec_price = float(data.get("execPrice", 0)) or float(data.get("avgPrice", 0))

    if not order_link_id:
      return

    # CS-039: dedup per-execution. WS sends one event per fill with a
    # unique execId; REST order_history returns aggregated cumExecFee
    # with no execId. Key on execId when present, else on orderLinkId
    # alone (the REST path is the aggregated view — one shot per
    # orderLinkId). Without this, commission accumulates twice when
    # WS and REST both report the same market fill.
    exec_id = data.get("execId", "") or ""
    dedup_key = (order_link_id, exec_id)
    with self._fill_processed_lock:
      if dedup_key in self._execution_processed_keys:
        return
      self._execution_processed_keys.add(dedup_key)

    db = self._db_factory()
    try:
      db_order = db.query(Order).filter(
        Order.order_link_id == order_link_id
      ).first()
      if not db_order:
        return

      # Accumulate commission
      db_order.commission = (db_order.commission or 0) + abs(exec_fee)
      db_order.filled_qty = float(data.get("cumExecQty", db_order.filled_qty or 0))
      if data.get("avgPrice"):
        db_order.avg_fill_price = float(data["avgPrice"])
      db.commit()

      # Update position commission tracking
      if db_order.position_id_fk:
        pos = db.get(Position, db_order.position_id_fk)
        if pos:
          if db_order.role == "entry":
            pos.entry_commission = (pos.entry_commission or 0) + abs(exec_fee)
          else:
            pos.exit_commission = (pos.exit_commission or 0) + abs(exec_fee)
          db.commit()

      self._log_event(
        db, db_order.portfolio_id, "execution", db_order.symbol,
        {
          "order_link_id": order_link_id, "role": db_order.role,
          "exec_qty": exec_qty, "exec_price": exec_price,
          "fee": exec_fee, "cum_qty": db_order.filled_qty,
        },
      )

    except Exception as e:
      logger.error("Error handling execution: %s", e)
    finally:
      db.close()

  # ── TTL Timers ────────────────────────────────────────────────────

  async def _entry_ttl_timer(self, order_link_id: str, symbol: str, ttl: int) -> None:
    """Cancel unfilled entry after TTL expires. Keep partial fills."""
    await asyncio.sleep(ttl)

    db = self._db_factory()
    try:
      db_order = db.query(Order).filter(
        Order.order_link_id == order_link_id
      ).first()
      if not db_order or db_order.status not in ("open", "partial"):
        return  # Already filled or cancelled

      filled = db_order.filled_qty or 0
      logger.info(
        "Entry TTL expired: %s linkId=%s filled=%.4f/%.4f",
        symbol, order_link_id, filled, db_order.intended_qty,
      )

      # Cancel remaining on exchange (non-blocking)
      try:
        await self._call_exchange(lambda: self._client.cancel_order(symbol=symbol, order_link_id=order_link_id))
      except Exception as e:
        logger.warning("Failed to cancel timed-out entry (may already be filled): %s", e)

      db_order.status = "cancelled"
      db_order.cancel_reason = "timeout"
      db_order.expired_at = datetime.now(timezone.utc)
      db.commit()

      self._log_event(
        db, db_order.portfolio_id, "entry_timeout", symbol,
        {"order_link_id": order_link_id, "filled": filled, "intended": db_order.intended_qty},
      )

      # If partially filled, create position with actual qty and place exits
      if filled > 0:
        self._create_position_from_partial(db, db_order, filled)

    except Exception as e:
      logger.error("Error in entry TTL timer: %s", e)
    finally:
      db.close()
      self._ttl_timers.pop(order_link_id, None)
      self._pending_entries.pop(order_link_id, None)

  async def _tp_ttl_timer(
    self, order_link_id: str, symbol: str, position_db_id: int,
    qty: float, ttl: int,
  ) -> None:
    """Cancel TP limit and replace with market close after TTL."""
    await asyncio.sleep(ttl)

    db = self._db_factory()
    try:
      db_order = db.query(Order).filter(
        Order.order_link_id == order_link_id
      ).first()
      if not db_order or db_order.status != "open":
        return

      logger.info("TP TTL expired: %s linkId=%s — switching to market close", symbol, order_link_id)

      # Cancel the limit TP (non-blocking)
      try:
        await self._call_exchange(lambda: self._client.cancel_order(symbol=symbol, order_link_id=order_link_id))
      except Exception as e:
        logger.warning("Failed to cancel timed-out TP: %s", e)

      db_order.status = "cancelled"
      db_order.cancel_reason = "tp_timeout"
      db_order.expired_at = datetime.now(timezone.utc)
      db.commit()

      # Get position to determine side
      pos = db.get(Position, position_db_id)
      if pos and pos.is_open:
        self.place_market_close(position_db_id, symbol, pos.side, qty, reason="tp_timeout")

      self._log_event(
        db, db_order.portfolio_id, "tp_timeout", symbol,
        {"order_link_id": order_link_id, "replaced_with": "market_close"},
      )

    except Exception as e:
      logger.error("Error in TP TTL timer: %s", e)
    finally:
      db.close()
      self._ttl_timers.pop(order_link_id, None)

  def _cancel_ttl_timer(self, order_link_id: str) -> None:
    """Cancel an active TTL timer (order was filled or manually cancelled)."""
    task = self._ttl_timers.pop(order_link_id, None)
    if task and not task.done():
      task.cancel()

  # ── Fill Handlers ─────────────────────────────────────────────────

  def _on_entry_filled(self, db: Session, db_order: Order) -> None:
    """Handle a fully filled entry order — create Position, place exits."""
    # Guard: don't create duplicate positions for the same entry order
    existing = db.query(Position).filter(
      Position.entry_order_link_id == db_order.order_link_id,
    ).first()
    if existing:
      logger.info("Position already created for entry %s — skipping duplicate", db_order.order_link_id)
      return

    filled_qty = db_order.filled_qty or db_order.intended_qty
    avg_price = db_order.avg_fill_price or db_order.price or 0

    # Create Position record
    pos = Position(
      portfolio_id=db_order.portfolio_id,
      symbol=db_order.symbol,
      side=db_order.side,
      entry_price=avg_price,
      quantity=filled_qty,
      sl_price=db_order.sl_price,
      tp_price=db_order.tp_price,
      is_open=True,
      exchange_symbol=db_order.symbol,
      margin_mode=self._settings.default_margin_mode,
      entry_order_link_id=db_order.order_link_id,
      entry_commission=db_order.commission or 0,
      batch_id=self._batch_ids.pop(db_order.order_link_id, None),
    )
    db.add(pos)
    db.commit()
    db.refresh(pos)

    # Link order to position
    db_order.position_id_fk = pos.id

    # Reserve margin in paper trading wallet (prevents over-leveraging)
    from backend.exchange.paper_trading import PaperTradingClient
    if isinstance(self._client, PaperTradingClient):
      notional = avg_price * filled_qty
      self._client.reserve_margin(pos.id, notional)

    # Set TSL fields on Position if trail_distance was provided with entry
    trail_dist = self._trail_distances.pop(db_order.order_link_id, 0)
    if trail_dist > 0:
      pos.trailing_stop_distance = trail_dist
      pos.trailing_extreme = avg_price
      logger.info("TSL set on position: pos=%d %s trail=%.4f", pos.id, db_order.symbol, trail_dist)

    # Resolve strategy_name from portfolio for trade/journal attribution
    try:
      portfolio = db.get(Portfolio, db_order.portfolio_id)
      if portfolio and portfolio.source_strategies:
        source = json.loads(portfolio.source_strategies)
        pos.strategy_name = source.get("strategy_name", "")
    except Exception as e:
      logger.warning("Failed to resolve strategy_name for pos %d: %s", pos.id, e)

    db.commit()

    self._log_event(
      db, db_order.portfolio_id, "position_opened", db_order.symbol,
      {
        "position_id": pos.id, "side": db_order.side,
        "entry_price": avg_price, "qty": filled_qty,
        "sl": db_order.sl_price, "tp": db_order.tp_price,
        "trail_distance": trail_dist if trail_dist > 0 else None,
      },
    )

    # Place exit orders for actual filled quantity
    if db_order.sl_price and db_order.sl_price > 0:
      sl_link = self.place_sl_order(pos.id, db_order.symbol, db_order.side, filled_qty, db_order.sl_price)
      if not sl_link:
        logger.critical(
          "FAILED to place SL for %s pos=%d — CLOSING POSITION for safety!",
          db_order.symbol, pos.id,
        )
        self._log_event(db, db_order.portfolio_id, "sl_placement_failed", db_order.symbol, {
          "position_id": pos.id, "sl_price": db_order.sl_price,
        })
        # Emergency close — never leave a position unprotected
        self.place_market_close(pos.id, db_order.symbol, db_order.side, filled_qty, reason="sl_failed")
        return  # Skip TP placement — position is being closed
    else:
      logger.critical(
        "Entry filled WITHOUT SL price! symbol=%s pos=%d — CLOSING for safety!",
        db_order.symbol, pos.id,
      )
      # Emergency close — no SL price means no protection
      self.place_market_close(pos.id, db_order.symbol, db_order.side, filled_qty, reason="no_sl_price")
      return  # Skip TP placement

    if db_order.tp_price:
      # Check portfolio risk profile for TP1/TP2 split
      tp1_pct = 100.0
      try:
        portfolio_obj = db.get(Portfolio, db_order.portfolio_id)
        if portfolio_obj and portfolio_obj.source_strategies:
          src = json.loads(portfolio_obj.source_strategies)
          rp = src.get("risk_profile", {})
          tp1_pct = rp.get("tp1_pct", 100.0)
      except Exception as e:
        logger.warning("Failed to resolve TP1 split for portfolio %d: %s", db_order.portfolio_id, e)

      if tp1_pct < 100.0 and tp1_pct > 0:
        # TP1: partial exit at TP price with tp1_pct of qty
        self._schedule_coro(
          self.place_tp_order(
            pos.id, db_order.symbol, db_order.side, filled_qty,
            db_order.tp_price, qty_percent=tp1_pct,
          )
        )
      else:
        # Single TP: full exit
        self._schedule_coro(
          self.place_tp_order(pos.id, db_order.symbol, db_order.side, filled_qty, db_order.tp_price)
        )

    # Notify coordinator
    if self._on_position_opened:
      try:
        self._on_position_opened(pos.id)
      except Exception as e:
        logger.error("on_position_opened callback error: %s", e)

    self._pending_entries.pop(db_order.order_link_id, None)

  def _on_exit_filled(self, db: Session, db_order: Order) -> None:
    """Handle a filled exit order — close Position, record Trade."""
    if not db_order.position_id_fk:
      return

    pos = db.get(Position, db_order.position_id_fk)
    if not pos:
      return

    # Guard: prevent double-close if another exit already closed this position
    if not pos.is_open:
      logger.warning(
        "Exit filled for already-closed position: %s linkId=%s role=%s — ignoring duplicate",
        pos.symbol, db_order.order_link_id, db_order.role,
      )
      return

    exit_price = db_order.avg_fill_price or db_order.price or 0
    exit_commission = db_order.commission or 0
    filled_qty = db_order.filled_qty or db_order.quantity or pos.quantity

    # Partial exit (TP1): reduce position, keep it open, move SL to BE
    is_partial = db_order.role == "tp1" and filled_qty < pos.quantity
    if is_partial:
      # Calculate PnL for the partial close
      if pos.side == "Buy":
        pnl = (exit_price - pos.entry_price) * filled_qty
      else:
        pnl = (pos.entry_price - exit_price) * filled_qty
      # Commission proportional to partial qty
      entry_comm_partial = (pos.entry_commission or 0) * (filled_qty / pos.quantity)
      total_commission = entry_comm_partial + exit_commission
      net_pnl = pnl - total_commission

      # Record partial trade
      trade = Trade(
        portfolio_id=pos.portfolio_id,
        symbol=pos.symbol,
        side="Long" if pos.side == "Buy" else "Short",
        strategy_name=pos.strategy_name,
        entry_price=pos.entry_price,
        exit_price=exit_price,
        quantity=filled_qty,
        realized_pnl=net_pnl,
        commission=total_commission,
        opened_at=pos.opened_at,
        closed_at=datetime.now(timezone.utc),
        close_reason="tp1",
        entry_commission=entry_comm_partial,
        exit_commission=exit_commission,
        funding_fee=0,
        batch_id=pos.batch_id,
        is_paper=bool(self._settings.paper_trading),
      )
      db.add(trade)

      # Reduce position qty — keep open
      pos.quantity -= filled_qty
      pos.risk_removed = True

      # Reduce reserved margin proportionally in paper trading wallet
      from backend.exchange.paper_trading import PaperTradingClient
      if isinstance(self._client, PaperTradingClient):
        remaining_notional = pos.entry_price * pos.quantity
        self._client.reserve_margin(pos.id, remaining_notional)

      db.commit()

      # Move SL to breakeven
      if pos.sl_order_link_id:
        logger.info("TP1 partial filled — amending SL to BE (entry=%.4f) for %s", pos.entry_price, pos.symbol)
        try:
          self.modify_sl_order(pos.id, pos.entry_price)
        except Exception as e:
          logger.error("Failed to amend SL to BE after TP1 fill: %s", e)

      self._log_event(
        db, pos.portfolio_id, "tp1_partial_fill", pos.symbol,
        {"filled_qty": filled_qty, "remaining_qty": pos.quantity, "pnl": round(net_pnl, 4)},
      )
      logger.info("TP1 partial close: %s qty=%.4f remaining=%.4f pnl=$%.2f",
                   pos.symbol, filled_qty, pos.quantity, net_pnl)
      return

    # Full exit (TP, TP2, SL, TSL, close) — close position entirely.
    # NOTE: No SL-to-BE amend here — the position is closing fully.
    # _cancel_other_exits() below cancels the SL directly, avoiding a
    # wasteful API round-trip and the brief SL race window.

    # Calculate realized PnL for full close
    close_qty = pos.quantity
    if pos.side == "Buy":
      pnl = (exit_price - pos.entry_price) * close_qty
    else:
      pnl = (pos.entry_price - exit_price) * close_qty

    total_commission = (pos.entry_commission or 0) + exit_commission
    net_pnl = pnl - total_commission - (pos.funding_accumulated or 0)

    # Close position
    pos.is_open = False
    pos.closed_at = datetime.now(timezone.utc)
    pos.exit_commission = exit_commission

    # Release margin in paper trading wallet
    from backend.exchange.paper_trading import PaperTradingClient
    if isinstance(self._client, PaperTradingClient):
      self._client.release_margin(pos.id)

    # Record trade
    trade = Trade(
      portfolio_id=pos.portfolio_id,
      symbol=pos.symbol,
      side="Long" if pos.side == "Buy" else "Short",
      strategy_name=pos.strategy_name,
      entry_price=pos.entry_price,
      exit_price=exit_price,
      quantity=close_qty,
      realized_pnl=net_pnl,
      commission=total_commission,
      opened_at=pos.opened_at,
      closed_at=pos.closed_at,
      close_reason=db_order.role,  # "sl", "tp", "tp1", "close", "tsl"
      entry_commission=pos.entry_commission or 0,
      exit_commission=exit_commission,
      funding_fee=pos.funding_accumulated or 0,
      batch_id=pos.batch_id,
      is_paper=bool(self._settings.paper_trading),
    )
    db.add(trade)
    db.commit()

    # Cancel remaining exit orders for this position
    self._cancel_other_exits(db, pos.id, db_order.order_link_id)

    self._log_event(
      db, pos.portfolio_id, "position_closed", pos.symbol,
      {
        "position_id": pos.id, "exit_price": exit_price,
        "pnl": net_pnl, "reason": db_order.role,
        "entry_commission": pos.entry_commission,
        "exit_commission": exit_commission,
        "funding": pos.funding_accumulated,
      },
    )

    logger.info(
      "Position closed: %s %s PnL=%.2f reason=%s",
      pos.symbol, pos.side, net_pnl, db_order.role,
    )

    # Auto-journal for real portfolios
    _auto_journal_trade(db, trade, pos)

    # Notify coordinator
    if self._on_position_closed:
      try:
        self._on_position_closed(pos.id)
      except Exception as e:
        logger.error("on_position_closed callback error: %s", e)

  def _create_position_from_partial(self, db: Session, db_order: Order, filled_qty: float) -> None:
    """Create a position from a partially filled entry and place exits."""
    # Guard: don't create duplicate positions for the same entry order
    existing = db.query(Position).filter(
      Position.entry_order_link_id == db_order.order_link_id,
    ).first()
    if existing:
      logger.info("Position already created for partial entry %s — skipping", db_order.order_link_id)
      return

    avg_price = db_order.avg_fill_price or db_order.price or 0

    pos = Position(
      portfolio_id=db_order.portfolio_id,
      symbol=db_order.symbol,
      side=db_order.side,
      entry_price=avg_price,
      quantity=filled_qty,
      sl_price=db_order.sl_price,
      tp_price=db_order.tp_price,
      is_open=True,
      exchange_symbol=db_order.symbol,
      margin_mode=self._settings.default_margin_mode,
      entry_order_link_id=db_order.order_link_id,
      entry_commission=db_order.commission or 0,
      batch_id=self._batch_ids.pop(db_order.order_link_id, None),
    )
    db.add(pos)
    db.commit()
    db.refresh(pos)

    db_order.position_id_fk = pos.id

    # Reserve margin in paper trading wallet (prevents over-leveraging)
    from backend.exchange.paper_trading import PaperTradingClient
    if isinstance(self._client, PaperTradingClient):
      notional = avg_price * filled_qty
      self._client.reserve_margin(pos.id, notional)

    # Set TSL fields on Position if trail_distance was provided with entry
    trail_dist = self._trail_distances.pop(db_order.order_link_id, 0)
    if trail_dist > 0:
      pos.trailing_stop_distance = trail_dist
      pos.trailing_extreme = avg_price
      logger.info("TSL set on partial position: pos=%d %s trail=%.4f", pos.id, db_order.symbol, trail_dist)

    # Resolve strategy_name from portfolio for trade/journal attribution
    try:
      portfolio = db.get(Portfolio, db_order.portfolio_id)
      if portfolio and portfolio.source_strategies:
        source = json.loads(portfolio.source_strategies)
        pos.strategy_name = source.get("strategy_name", "")
    except Exception as e:
      logger.warning("Failed to resolve strategy_name for partial pos %d: %s", pos.id, e)

    db.commit()

    self._log_event(
      db, db_order.portfolio_id, "position_opened_partial", db_order.symbol,
      {
        "position_id": pos.id, "filled_qty": filled_qty,
        "intended_qty": db_order.intended_qty, "entry_price": avg_price,
        "trail_distance": trail_dist if trail_dist > 0 else None,
      },
    )

    # Place exits for ACTUAL filled qty (not intended)
    if db_order.sl_price and db_order.sl_price > 0:
      sl_link = self.place_sl_order(pos.id, db_order.symbol, db_order.side, filled_qty, db_order.sl_price)
      if not sl_link:
        logger.critical(
          "FAILED to place SL for partial fill %s pos=%d — CLOSING POSITION for safety!",
          db_order.symbol, pos.id,
        )
        self._log_event(db, db_order.portfolio_id, "sl_placement_failed", db_order.symbol, {
          "position_id": pos.id, "sl_price": db_order.sl_price, "partial": True,
        })
        # Emergency close — never leave a position unprotected
        self.place_market_close(pos.id, db_order.symbol, db_order.side, filled_qty, reason="sl_failed_partial")
        return
    elif not db_order.sl_price or db_order.sl_price <= 0:
      logger.critical(
        "Partial entry filled WITHOUT SL price! symbol=%s pos=%d — CLOSING for safety!",
        db_order.symbol, pos.id,
      )
      self.place_market_close(pos.id, db_order.symbol, db_order.side, filled_qty, reason="no_sl_price_partial")
      return
    if db_order.tp_price:
      self._schedule_coro(
        self.place_tp_order(pos.id, db_order.symbol, db_order.side, filled_qty, db_order.tp_price)
      )

    if self._on_position_opened:
      try:
        self._on_position_opened(pos.id)
      except Exception as e:
        logger.error("on_position_opened callback error: %s", e)

  # ── Reconciliation ────────────────────────────────────────────────

  def reconcile_orders(self) -> dict:
    """Compare local DB orders vs exchange open orders.

    Returns: {matched: int, orphan_exchange: list, orphan_local: list}
    """
    exchange_orders = self._client.get_open_orders()
    exchange_by_link = {o.get("orderLinkId", ""): o for o in exchange_orders if o.get("orderLinkId")}

    db = self._db_factory()
    try:
      local_open = db.query(Order).filter(
        Order.status.in_(["open", "partial", "submitting"])
      ).all()
      local_by_link = {o.order_link_id: o for o in local_open if o.order_link_id}

      matched = 0
      orphan_exchange = []
      orphan_local = []

      # Check exchange orders against local
      for link_id, ex_order in exchange_by_link.items():
        if link_id in local_by_link:
          matched += 1
        else:
          orphan_exchange.append({
            "order_link_id": link_id,
            "symbol": ex_order.get("symbol", ""),
            "side": ex_order.get("side", ""),
            "status": ex_order.get("orderStatus", ""),
          })

      # Check local orders against exchange
      for link_id, local_order in local_by_link.items():
        if link_id not in exchange_by_link:
          orphan_local.append({
            "order_link_id": link_id,
            "symbol": local_order.symbol,
            "role": local_order.role,
            "db_id": local_order.id,
          })

      result = {
        "matched": matched,
        "orphan_exchange": orphan_exchange,
        "orphan_local": orphan_local,
      }

      if orphan_exchange or orphan_local:
        logger.warning("Order reconciliation discrepancies: %s", result)
        self._log_event(db, None, "reconciliation", "", result)

      return result

    finally:
      db.close()

  def cancel_orphan_orders(self) -> int:
    """Cancel exchange orders that have no matching local DB record."""
    recon = self.reconcile_orders()
    cancelled = 0

    for orphan in recon["orphan_exchange"]:
      try:
        self._client.cancel_order(
          symbol=orphan["symbol"],
          order_link_id=orphan["order_link_id"],
        )
        cancelled += 1
        logger.info("Cancelled orphan order: %s %s", orphan["symbol"], orphan["order_link_id"])
      except Exception as e:
        logger.warning("Failed to cancel orphan order: %s", e)

    # Check local orphans — they might have been FILLED (not just cancelled).
    # An orphan_local order is "in our DB as open but not on exchange open list".
    # This happens when: (a) the order filled, or (b) it was cancelled externally.
    # We MUST check order history to distinguish fills from cancellations,
    # because fills need to trigger _on_exit_filled() (SL-to-BE, trade recording).
    db = self._db_factory()
    try:
      for orphan in recon["orphan_local"]:
        db_order = db.get(Order, orphan["db_id"])
        if not db_order or db_order.status not in ("open", "partial", "submitting"):
          continue

        # Check order history to see if this order was filled
        filled_via_history = False
        try:
          history = self._client.get_order_history(db_order.symbol, limit=20)
          for h in history:
            if h.get("orderLinkId") == db_order.order_link_id and h.get("orderStatus") == "Filled":
              logger.info(
                "Orphan order was actually FILLED: %s %s linkId=%s — processing fill",
                db_order.symbol, db_order.role, db_order.order_link_id,
              )
              self.on_order_update(h)
              self.on_execution(h)
              filled_via_history = True
              break
        except Exception as hist_err:
          logger.warning("Failed to check order history for orphan %s: %s", db_order.order_link_id, hist_err)

        if not filled_via_history:
          db_order.status = "cancelled"
          db_order.cancel_reason = "reconciliation_orphan"

      db.commit()
    finally:
      db.close()

    return cancelled

  # ── Cancel & Shutdown ─────────────────────────────────────────────

  def cancel_entry_order(self, order_link_id: str) -> bool:
    """Manually cancel a pending entry order."""
    db = self._db_factory()
    try:
      db_order = db.query(Order).filter(
        Order.order_link_id == order_link_id
      ).first()
      if not db_order:
        return False

      try:
        self._client.cancel_order(symbol=db_order.symbol, order_link_id=order_link_id)
      except Exception as e:
        logger.warning("Failed to cancel entry: %s", e)

      db_order.status = "cancelled"
      db_order.cancel_reason = "manual"
      self._cancel_ttl_timer(order_link_id)
      db.commit()
      return True
    finally:
      db.close()

  def shutdown(self) -> None:
    """Graceful shutdown: cancel pending entries, KEEP exit orders alive.

    Exit orders (SL/TP) remain on the exchange to protect open positions
    even when the engine is offline.
    """
    logger.warning("OrderManager shutting down — cancelling pending entries")

    # Cancel all TTL timers
    for link_id, task in list(self._ttl_timers.items()):
      if not task.done():
        task.cancel()
    self._ttl_timers.clear()

    # Cancel pending entry orders on exchange
    db = self._db_factory()
    try:
      pending = db.query(Order).filter(
        Order.role == "entry",
        Order.status.in_(["open", "partial", "submitting"]),
      ).all()

      for order in pending:
        try:
          self._client.cancel_order(
            symbol=order.symbol, order_link_id=order.order_link_id,
          )
          order.status = "cancelled"
          order.cancel_reason = "shutdown"
          logger.info("Cancelled entry on shutdown: %s %s", order.symbol, order.order_link_id)
        except Exception as e:
          logger.warning("Failed to cancel entry on shutdown: %s", e)

      db.commit()

      # Log how many exit orders remain active
      active_exits = db.query(Order).filter(
        Order.role.in_(["sl", "tp", "tp1"]),
        Order.status == "open",
      ).count()
      logger.info("Shutdown complete: %d exit orders left active on exchange", active_exits)

    finally:
      db.close()
      self._pending_entries.clear()

  # ── Helpers ───────────────────────────────────────────────────────

  def _cancel_other_exits(self, db: Session, position_id: int, except_link_id: str) -> None:
    """Cancel all exit orders for a position except the one that just filled.

    Includes ``close`` so an in-flight ``market_close`` (role='close') is
    also cancelled when an SL/TP fires — otherwise a concurrent SL/TP and
    market_close could both fire and the position would be double-closed.
    """
    other_exits = db.query(Order).filter(
      Order.position_id_fk == position_id,
      Order.role.in_(["sl", "tp", "tp1", "tsl", "close"]),
      Order.status == "open",
      Order.order_link_id != except_link_id,
    ).all()

    for order in other_exits:
      try:
        self._client.cancel_order(symbol=order.symbol, order_link_id=order.order_link_id)
        order.status = "cancelled"
        order.cancel_reason = "position_closed"
        logger.info("Cancelled exit order: %s %s role=%s", order.symbol, order.order_link_id, order.role)
      except Exception as e:
        logger.warning("Failed to cancel exit: %s", e)

    db.commit()

  @staticmethod
  def _get_position_portfolio(db: Session, position_id: int) -> int | None:
    """Get portfolio_id from a Position record."""
    pos = db.get(Position, position_id)
    return pos.portfolio_id if pos else None

  @staticmethod
  def _round_qty(qty: float, lot_size: float) -> str:
    """Round qty DOWN to exchange lot precision.

    Uses Decimal to avoid float floor artifacts — e.g. ``0.3 // 0.1``
    returns 2.0 in float, dropping a full lot. ``Decimal(str(x))`` uses
    Python's shortest repr so 0.1/0.3/0.001 round-trip exactly.
    """
    from decimal import Decimal
    q = Decimal(str(qty))
    step = Decimal(str(lot_size))
    rounded = (q // step) * step
    if lot_size >= 1:
      return str(int(rounded))
    decimals = len(str(lot_size).rstrip("0").split(".")[-1])
    return f"{rounded:.{decimals}f}"

  @staticmethod
  def _round_price(price: float, tick_size: float) -> str:
    """Round price DOWN to exchange tick precision.

    Same Decimal fix as ``_round_qty`` — avoids float floor artifacts
    that can shift SL/TP/trigger prices by one tick and cause Bybit
    rejection or unintended fills.
    """
    from decimal import Decimal
    p = Decimal(str(price))
    step = Decimal(str(tick_size))
    rounded = (p // step) * step
    if tick_size >= 1:
      return str(int(rounded))
    decimals = len(str(tick_size).rstrip("0").split(".")[-1])
    return f"{rounded:.{decimals}f}"

  @staticmethod
  def _log_event(
    db: Session, portfolio_id: int | None, event_type: str,
    symbol: str, details: dict,
  ) -> None:
    """Write an audit event to the live_events table."""
    try:
      evt = LiveEvent(
        portfolio_id=portfolio_id,
        event_type=event_type,
        symbol=symbol,
        details=json.dumps(details, default=str),
      )
      db.add(evt)
      db.commit()
    except Exception as e:
      logger.warning("Failed to log event %s: %s", event_type, e)
      db.rollback()


def _auto_journal_trade(db: Session, trade: Trade, pos: Position) -> None:
  """Auto-create a journal entry when a REAL live trade closes.

  Only runs for real portfolios (not paper). Failure here must never
  block the trade flow — everything is wrapped in try/except.
  """
  try:
    # Only auto-journal real portfolios
    if not pos.portfolio_id:
      return
    portfolio = db.query(Portfolio).filter(Portfolio.id == pos.portfolio_id).first()
    if not portfolio or portfolio.portfolio_type != "real":
      return

    # Dedup: skip if already journaled
    existing = (
      db.query(JournalTrade)
      .filter(JournalTrade.live_trade_id == trade.id)
      .first()
    )
    if existing:
      return

    # Resolve batch_id and config_name from PortfolioAsset
    batch_id = None
    config_name = None
    asset = (
      db.query(PortfolioAsset)
      .filter(
        PortfolioAsset.portfolio_id == pos.portfolio_id,
        PortfolioAsset.symbol == pos.symbol,
      )
      .first()
    )
    if asset:
      batch_id = asset.batch_id
      config_name = asset.config_name

    # Resolve strategy_id from strategy_name
    strategy_id = None
    config_id = None
    if pos.strategy_name:
      strat = (
        db.query(Strategy)
        .filter(Strategy.name == pos.strategy_name)
        .first()
      )
      if strat:
        strategy_id = strat.id
        # Resolve config_id from config_name
        if config_name:
          cfg = (
            db.query(StrategyConfig)
            .filter(
              StrategyConfig.strategy_id == strat.id,
              StrategyConfig.name == config_name,
            )
            .first()
          )
          if cfg:
            config_id = cfg.id

    # Compute P&L percentage
    net_profit_pct = None
    if trade.entry_price and trade.entry_price > 0:
      net_profit_pct = trade.realized_pnl / (trade.entry_price * trade.quantity) * 100

    journal = JournalTrade(
      symbol=trade.symbol,
      side=trade.side,
      status="closed",
      source="live",
      strategy_id=strategy_id,
      config_id=config_id,
      portfolio_id=trade.portfolio_id,
      batch_id=batch_id,
      config_name=config_name,
      live_trade_id=trade.id,
      entry_price=trade.entry_price,
      quantity=trade.quantity,
      sl_price=pos.sl_price,
      tp_price=pos.tp_price,
      entry_time=trade.opened_at,
      exit_price=trade.exit_price,
      exit_time=trade.closed_at,
      exit_reason=trade.close_reason,
      outcome="Win" if trade.realized_pnl > 0 else "Loss",
      net_profit=trade.realized_pnl,
      net_profit_pct=round(net_profit_pct, 2) if net_profit_pct is not None else None,
      actual_commission=trade.commission,
      commission_rate=COMMISSION_PCT,
      expected_commission=trade.commission,
    )
    db.add(journal)
    db.commit()
    logger.info(
      "Auto-journaled live trade: %s %s PnL=%.2f",
      trade.symbol, trade.side, trade.realized_pnl,
    )
  except Exception as e:
    logger.error("Auto-journal failed (non-fatal): %s", e)
    try:
      db.rollback()
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)
