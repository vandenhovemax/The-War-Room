"""Bybit WebSocket Manager — real-time data feeds using pybit.

Manages two connections:
- Public: kline (per-TF candles for bar close detection) + tickers (live prices for TSL)
- Private: order updates, executions, position changes, wallet updates

Subscribes to the ACTUAL timeframe kline (e.g. kline.5, kline.30, kline.240)
instead of aggregating 1-minute bars. This ensures bar close fires at exactly
the right time with complete OHLCV from Bybit — no alignment bugs after reconnect.

Auto-reconnects via health monitor thread (checks every 15s).
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

from pybit.unified_trading import WebSocket as BybitWebSocket

from backend.config.settings import get_settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)

HEALTH_CHECK_INTERVAL = 15  # seconds between connection checks
RECONNECT_DELAY = 3  # seconds to wait before reconnecting


class BybitWSManager:
  """Manages Bybit WebSocket connections for live data.

  Public streams:
  - kline.{TF}.{SYMBOL}: Per-TF candles (e.g. kline.5, kline.30, kline.D)
  - tickers.{SYMBOL}: Live prices for TrailingStopTracker

  Private streams:
  - order: Order status updates
  - execution: Fill notifications with fees
  - position: Margin/PnL changes
  - wallet: Balance updates

  IMPORTANT: Private WS callbacks (on_order, on_execution, etc.) run on
  pybit's internal thread. If callbacks do heavy work (DB ops), they block
  pybit's read loop and cause ping timeouts → disconnects. To prevent this,
  callbacks are offloaded to the main asyncio event loop via call_soon_threadsafe.
  """

  def __init__(
    self,
    api_key: str = "",
    api_secret: str = "",
    testnet: bool = True,
    on_kline: Callable[[str, dict], None] | None = None,
    on_ticker: Callable[[str, float], None] | None = None,
    on_order: Callable[[dict], None] | None = None,
    on_execution: Callable[[dict], None] | None = None,
    on_position: Callable[[dict], None] | None = None,
    on_wallet: Callable[[dict], None] | None = None,
  ):
    self._api_key = api_key
    self._api_secret = api_secret
    self._testnet = testnet
    self._settings = get_settings()

    # Callbacks
    self._on_kline = on_kline
    self._on_ticker = on_ticker
    self._on_order = on_order
    self._on_execution = on_execution
    self._on_position = on_position
    self._on_wallet = on_wallet

    # WebSocket instances
    self._public_ws: BybitWebSocket | None = None
    self._private_ws: BybitWebSocket | None = None

    # Track subscriptions for reconnect
    # symbol → set of interval strings (e.g. {"5", "D"})
    self._kline_subs: dict[str, set[str]] = {}
    self._ticker_symbols: set[str] = set()
    self._private_connected: bool = False

    # Health monitor
    self._health_thread: threading.Thread | None = None
    self._last_public_msg: float = 0.0
    self._last_private_msg: float = 0.0
    self._reconnect_count: int = 0

    # Main event loop reference (set in start() for thread-safe callback offloading)
    self._loop: asyncio.AbstractEventLoop | None = None

    # State
    self._running = False

  # ── Connection ────────────────────────────────────────────────────

  def start(self) -> None:
    """Start both public and private WebSocket connections.

    Hybrid testnet mode: when testnet=True and testnet_use_mainnet_prices=True,
    public feeds (candles, tickers) connect to mainnet for real prices while
    private feeds (orders, fills) connect to testnet for execution testing.
    """
    if self._running:
      return

    # Capture the main event loop for thread-safe callback offloading
    try:
      self._loop = asyncio.get_running_loop()
    except RuntimeError:
      self._loop = None
      logger.warning("No running event loop — WS callbacks will run on pybit thread")

    self._running = True
    self._connect_all()
    self._start_health_monitor()

  def _connect_all(self) -> None:
    """Create and connect both public and private WebSocket instances."""
    # Hybrid mode: use mainnet public feeds when trading on testnet
    use_hybrid = self._testnet and self._settings.testnet_use_mainnet_prices
    public_testnet = False if use_hybrid else self._testnet

    if use_hybrid:
      logger.info(
        "Starting Bybit WebSocket — HYBRID MODE: public=mainnet (real prices), private=testnet"
      )
    else:
      logger.info("Starting Bybit WebSocket connections (testnet=%s)", self._testnet)

    # Public WebSocket — mainnet for real prices in hybrid mode
    try:
      self._public_ws = BybitWebSocket(
        testnet=public_testnet,
        channel_type="linear",
        ping_interval=20,
        ping_timeout=15,
      )
      self._last_public_msg = time.monotonic()
      logger.info(
        "Public WebSocket connected (%s)",
        "mainnet — real prices" if use_hybrid else ("testnet" if self._testnet else "mainnet"),
      )
    except Exception as e:
      logger.error("Failed to connect public WebSocket: %s", e)

    # Private WebSocket — always matches testnet/mainnet setting (for order execution)
    if self._api_key and self._api_secret:
      try:
        self._private_ws = BybitWebSocket(
          testnet=self._testnet,
          channel_type="private",
          api_key=self._api_key,
          api_secret=self._api_secret,
          ping_interval=20,
          ping_timeout=15,
        )
        self._private_connected = True
        self._last_private_msg = time.monotonic()
        self._subscribe_private_streams()
        logger.info("Private WebSocket connected (%s)", "testnet" if self._testnet else "mainnet")
      except Exception as e:
        logger.error("Failed to connect private WebSocket: %s", e)
    else:
      logger.warning("No API keys — private WebSocket not connected")

  def stop(self) -> None:
    """Disconnect all WebSocket connections."""
    self._running = False

    if self._public_ws:
      try:
        self._public_ws.exit()
      except Exception as _exc:
        logger.debug("swallowed: %s", _exc)
      self._public_ws = None

    if self._private_ws:
      try:
        self._private_ws.exit()
      except Exception as _exc:
        logger.debug("swallowed: %s", _exc)
      self._private_ws = None

    self._private_connected = False
    self._kline_subs.clear()
    self._ticker_symbols.clear()
    logger.info("WebSocket connections closed")

  def is_connected(self) -> dict:
    """Check connection status."""
    return {
      "public": self._public_ws is not None,
      "private": self._private_connected,
      "kline_symbols": len(self._kline_subs),
      "kline_streams": sum(len(v) for v in self._kline_subs.values()),
      "ticker_symbols": len(self._ticker_symbols),
      "reconnect_count": self._reconnect_count,
    }

  # ── Health Monitor ──────────────────────────────────────────────────

  def _start_health_monitor(self) -> None:
    """Start background thread that checks WS health and reconnects if dead."""
    self._health_thread = threading.Thread(
      target=self._health_loop,
      name="ws-health-monitor",
      daemon=True,
    )
    self._health_thread.start()
    logger.info("WebSocket health monitor started (check every %ds)", HEALTH_CHECK_INTERVAL)

  def _health_loop(self) -> None:
    """Periodically check WebSocket connections and reconnect if needed."""
    while self._running:
      time.sleep(HEALTH_CHECK_INTERVAL)
      if not self._running:
        break

      try:
        self._check_and_reconnect()
      except Exception as e:
        logger.error("Health monitor error: %s", e)

  def _is_ws_alive(self, ws: BybitWebSocket | None) -> bool:
    """Check if a pybit WebSocket instance is still connected.

    Tries multiple detection methods since pybit's internals vary by version:
    1. Check websocket-client sock.connected
    2. Check if the WS run thread is still alive
    3. Fallback: assume alive if object exists (let message timeout catch it)
    """
    if ws is None:
      return False
    try:
      # Method 1: Check sock.connected on the underlying WebSocketApp
      inner = getattr(ws, "ws", None)
      if inner is not None:
        sock = getattr(inner, "sock", None)
        if sock is not None and hasattr(sock, "connected"):
          return sock.connected
        # sock is None means disconnected
        if sock is None:
          # But only if WebSocketApp has been started (has a 'sock' attr at all)
          if hasattr(inner, "sock"):
            return False

      # Method 2: Check if pybit's internal thread is still alive
      wst = getattr(ws, "wst", None) or getattr(ws, "_wst", None)
      if wst is not None and hasattr(wst, "is_alive"):
        return wst.is_alive()

      # Fallback: if the object exists, assume alive
      return True
    except AttributeError:
      # CS-046: narrowed from bare ``except Exception``. The only expected
      # failure mode is pybit renaming or removing the internal attributes
      # we probe (``wst``, ``sock``, ``is_alive``) — that maps to
      # AttributeError. Any other exception (e.g. a runtime bug) should
      # now surface so the reconnect logic can react appropriately.
      return False

  def _check_and_reconnect(self) -> None:
    """Check both connections and reconnect any that died."""
    now = time.monotonic()

    # For public WS: check socket state AND message recency
    # If we received a message in the last 60s, it's alive regardless of socket check
    public_alive = self._is_ws_alive(self._public_ws)
    if not public_alive and self._public_ws is not None:
      # Double-check: if we got a message recently, the socket check may be wrong
      if (now - self._last_public_msg) < 60:
        public_alive = True

    # For private WS: only check socket state (messages can be infrequent)
    private_alive = True
    if self._private_connected:
      private_alive = self._is_ws_alive(self._private_ws)
      # Private can go long periods without messages, so use longer timeout
      if not private_alive and self._private_ws is not None:
        if (now - self._last_private_msg) < 120:
          private_alive = True

    if public_alive and private_alive:
      return

    # Something died — reconnect only the dead connection(s)
    dead_parts = []
    if not public_alive:
      dead_parts.append("public")
    if not private_alive:
      dead_parts.append("private")

    self._reconnect_count += 1
    logger.warning(
      "WebSocket connection lost (%s) — reconnecting (attempt #%d)...",
      " + ".join(dead_parts), self._reconnect_count,
    )

    time.sleep(RECONNECT_DELAY)

    # Hybrid mode check
    use_hybrid = self._testnet and self._settings.testnet_use_mainnet_prices
    public_testnet = False if use_hybrid else self._testnet

    # Reconnect only public if it died
    if not public_alive:
      if self._public_ws:
        try:
          self._public_ws.exit()
        except Exception as _exc:
          logger.debug("swallowed: %s", _exc)
        self._public_ws = None

      saved_kline = {sym: set(intervals) for sym, intervals in self._kline_subs.items()}
      saved_ticker = set(self._ticker_symbols)
      self._kline_subs.clear()
      self._ticker_symbols.clear()

      try:
        self._public_ws = BybitWebSocket(
          testnet=public_testnet,
          channel_type="linear",
          ping_interval=20,
          ping_timeout=15,
        )
        self._last_public_msg = time.monotonic()
        logger.info("Public WebSocket reconnected")
      except Exception as e:
        logger.error("Failed to reconnect public WebSocket: %s", e)

      # Re-subscribe to saved streams with original intervals
      for symbol, intervals in saved_kline.items():
        for interval in intervals:
          self.subscribe_kline(symbol, interval)
      for symbol in saved_ticker:
        self.subscribe_ticker(symbol)

    # Reconnect only private if it died
    if not private_alive:
      if self._private_ws:
        try:
          self._private_ws.exit()
        except Exception as _exc:
          logger.debug("swallowed: %s", _exc)
        self._private_ws = None
        self._private_connected = False

      if self._api_key and self._api_secret:
        try:
          self._private_ws = BybitWebSocket(
            testnet=self._testnet,
            channel_type="private",
            api_key=self._api_key,
            api_secret=self._api_secret,
            ping_interval=20,
            ping_timeout=15,
          )
          self._private_connected = True
          self._last_private_msg = time.monotonic()
          self._subscribe_private_streams()
          logger.info("Private WebSocket reconnected")
        except Exception as e:
          logger.error("Failed to reconnect private WebSocket: %s", e)

    logger.info(
      "WebSocket reconnection complete — %d kline symbols (%d streams) + %d ticker streams active",
      len(self._kline_subs),
      sum(len(v) for v in self._kline_subs.values()),
      len(self._ticker_symbols),
    )

  # ── Public Subscriptions ──────────────────────────────────────────

  @staticmethod
  def _pybit_interval(interval: str):
    """Convert canonical interval string to pybit format.

    Numeric intervals (1,3,5,15,30,60,120,240,360,720) → int
    Letter intervals (D,W,M) → str
    """
    if interval.isdigit():
      return int(interval)
    return interval  # "D", "W", "M" stay as strings

  def subscribe_kline(self, symbol: str, interval: str = "1") -> None:
    """Subscribe to a kline stream for a specific timeframe.

    Args:
      symbol: e.g. "DOGEUSDT"
      interval: Bybit interval — "5", "30", "60", "240", "D", etc.
    """
    if not self._public_ws:
      return

    existing = self._kline_subs.get(symbol, set())
    if interval in existing:
      return  # Already subscribed

    try:
      self._public_ws.kline_stream(
        interval=self._pybit_interval(interval),
        symbol=symbol,
        callback=self._handle_kline,
      )
      self._kline_subs.setdefault(symbol, set()).add(interval)
      logger.info("Subscribed to kline.%s.%s", interval, symbol)
    except Exception as e:
      logger.error("Failed to subscribe kline.%s for %s: %s", interval, symbol, e)

  def subscribe_ticker(self, symbol: str) -> None:
    """Subscribe to ticker (live price) for a symbol."""
    if not self._public_ws or symbol in self._ticker_symbols:
      return

    try:
      self._public_ws.ticker_stream(
        symbol=symbol,
        callback=self._handle_ticker,
      )
      self._ticker_symbols.add(symbol)
      logger.info("Subscribed to tickers.%s", symbol)
    except Exception as e:
      logger.error("Failed to subscribe ticker for %s: %s", symbol, e)

  def subscribe_symbol(
    self, symbol: str,
    base_tf: str = "5",
    extra_tfs: list[str] | None = None,
  ) -> None:
    """Subscribe to kline streams for all needed TFs + ticker.

    Args:
      symbol: e.g. "DOGEUSDT"
      base_tf: Primary timeframe ("5", "30", "240", "D", etc.)
      extra_tfs: Additional timeframes (e.g. ["D"] for daily)
    """
    # Subscribe to the base TF kline (for bar close detection)
    self.subscribe_kline(symbol, base_tf)
    # Subscribe to extra TFs (for multi-TF strategies)
    for tf in (extra_tfs or []):
      self.subscribe_kline(symbol, tf)
    # Subscribe to ticker (for TSL live price tracking)
    self.subscribe_ticker(symbol)

  def unsubscribe_symbol(self, symbol: str) -> None:
    """Unsubscribe from all streams for a symbol."""
    self._kline_subs.pop(symbol, None)
    self._ticker_symbols.discard(symbol)
    # pybit doesn't support unsubscribe — tracked for reconnect filtering

  # ── Private Subscriptions ─────────────────────────────────────────

  def _subscribe_private_streams(self) -> None:
    """Subscribe to all private streams (order, execution, position, wallet)."""
    if not self._private_ws:
      return

    try:
      self._private_ws.order_stream(callback=self._handle_order)
      logger.info("Subscribed to private order stream")
    except Exception as e:
      logger.error("Failed to subscribe order stream: %s", e)

    try:
      self._private_ws.execution_stream(callback=self._handle_execution)
      logger.info("Subscribed to private execution stream")
    except Exception as e:
      logger.error("Failed to subscribe execution stream: %s", e)

    try:
      self._private_ws.position_stream(callback=self._handle_position)
      logger.info("Subscribed to private position stream")
    except Exception as e:
      logger.error("Failed to subscribe position stream: %s", e)

    try:
      self._private_ws.wallet_stream(callback=self._handle_wallet)
      logger.info("Subscribed to private wallet stream")
    except Exception as e:
      logger.error("Failed to subscribe wallet stream: %s", e)

  def _offload(self, fn: Callable, *args) -> None:
    """Run a callback on the main event loop thread instead of pybit's WS thread.

    This prevents DB operations in callbacks from blocking pybit's read loop,
    which would cause ping timeouts and connection drops.
    """
    if self._loop and self._loop.is_running():
      self._loop.call_soon_threadsafe(fn, *args)
    else:
      # Fallback: run directly (may block WS thread — better than dropping)
      fn(*args)

  # ── Message Handlers ──────────────────────────────────────────────

  def _handle_kline(self, message: dict) -> None:
    """Process kline WebSocket message."""
    try:
      self._last_public_msg = time.monotonic()
      topic = message.get("topic", "")
      data_list = message.get("data", [])
      if not data_list:
        return

      # Extract symbol from topic: "kline.1.BTCUSDT"
      parts = topic.split(".")
      if len(parts) >= 3:
        symbol = parts[2]
      else:
        return

      for kline in data_list:
        if self._on_kline:
          self._offload(self._on_kline, symbol, kline)

    except Exception as e:
      logger.error("Error handling kline message: %s", e)

  def _handle_ticker(self, message: dict) -> None:
    """Process ticker WebSocket message."""
    try:
      self._last_public_msg = time.monotonic()
      topic = message.get("topic", "")
      data = message.get("data", {})
      if not data:
        return

      # Extract symbol from topic: "tickers.BTCUSDT"
      parts = topic.split(".")
      if len(parts) >= 2:
        symbol = parts[1]
      else:
        return

      last_price = float(data.get("lastPrice", 0))
      if last_price > 0 and self._on_ticker:
        self._offload(self._on_ticker, symbol, last_price)

    except Exception as e:
      logger.error("Error handling ticker message: %s", e)

  def _handle_order(self, message: dict) -> None:
    """Process order update from private stream.

    Offloads callback to main event loop — DB operations must NOT block
    pybit's WS thread (causes ping timeouts → disconnects).
    """
    try:
      self._last_private_msg = time.monotonic()
      data_list = message.get("data", [])
      for order_data in data_list:
        if self._on_order:
          self._offload(self._on_order, order_data)
    except Exception as e:
      logger.error("Error handling order message: %s", e)

  def _handle_execution(self, message: dict) -> None:
    """Process execution (fill) from private stream.

    Offloaded to main event loop to avoid blocking pybit's WS thread.
    """
    try:
      self._last_private_msg = time.monotonic()
      data_list = message.get("data", [])
      for exec_data in data_list:
        if self._on_execution:
          self._offload(self._on_execution, exec_data)
    except Exception as e:
      logger.error("Error handling execution message: %s", e)

  def _handle_position(self, message: dict) -> None:
    """Process position update from private stream."""
    try:
      self._last_private_msg = time.monotonic()
      data_list = message.get("data", [])
      for pos_data in data_list:
        if self._on_position:
          self._offload(self._on_position, pos_data)
    except Exception as e:
      logger.error("Error handling position message: %s", e)

  def _handle_wallet(self, message: dict) -> None:
    """Process wallet balance update from private stream."""
    try:
      self._last_private_msg = time.monotonic()
      data_list = message.get("data", [])
      for wallet_data in data_list:
        if self._on_wallet:
          self._offload(self._on_wallet, wallet_data)
    except Exception as e:
      logger.error("Error handling wallet message: %s", e)
