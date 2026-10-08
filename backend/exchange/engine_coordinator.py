"""Engine Coordinator — top-level orchestrator for live trading.

Wires together all components:
- BybitTradingClient (REST orders)
- BybitWSManager (WebSocket feeds)
- CandleAggregator (bar assembly)
- StrategyExecutor (signal generation)
- OrderManager (order lifecycle)
- PositionManager (sizing + leverage)
- TrailingStopTracker (TSL via live prices)
- SafetyMonitor (circuit breakers)

Manages the complete lifecycle: start → activate portfolios → trade → shutdown.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.config.settings import get_settings
from backend.db.models import LiveEvent, Order, Portfolio, Position, Trade
from backend.exchange.bybit_trading import BybitTradingClient
from backend.exchange.candle_aggregator import CandleAggregator
from backend.exchange.order_manager import OrderManager
from backend.exchange.position_manager import PositionManager
from backend.exchange.safety_monitor import SafetyMonitor
from backend.exchange.strategy_executor import StrategyExecutor, StrategySignal
from backend.exchange.tsl_tracker import TrailingStopTracker
from backend.exchange.ws_feeds import BybitWSManager
from backend.utils import heartbeats
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# CS-031: whitelist of valid martingale streak-grouping scopes.
# Anything outside this set is a bug (typo, stale migration, hand-edited JSON)
# and must be caught at activation — silently falling through to "asset" means
# the user sees the wrong risk grouping with no warning.
_VALID_MARTINGALE_SCOPES: frozenset[str] = frozenset({
  "asset", "portfolio", "asset_batch", "batch",
})


class EngineCoordinator:
  """Orchestrates all live trading components.

  Lifecycle:
    1. start() — init all components, connect WebSockets
    2. activate_portfolio(id) — begin trading a portfolio
    3. Runs autonomously: strategies generate signals → orders placed
    4. deactivate_portfolio(id) — stop trading
    5. shutdown() — graceful stop, keep exit orders alive
  """

  def __init__(self, db_factory: Callable[[], Session]):
    self._db_factory = db_factory
    self._settings = get_settings()
    self._running = False
    self._reconciliation_task: asyncio.Task | None = None
    self._fill_poll_task: asyncio.Task | None = None
    self._wallet_refresh_task: asyncio.Task | None = None
    self._kelly_recompute_task: asyncio.Task | None = None
    self._loop: asyncio.AbstractEventLoop | None = None  # stored for thread-safe scheduling

    # Components (initialized in start())
    self._trading_client: BybitTradingClient | None = None
    self._ws_manager: BybitWSManager | None = None
    self._candle_aggregator: CandleAggregator | None = None
    self._strategy_executor: StrategyExecutor | None = None
    self._order_manager: OrderManager | None = None
    self._position_manager: PositionManager | None = None
    self._tsl_tracker: TrailingStopTracker | None = None
    self._safety_monitor: SafetyMonitor | None = None
    self._paper_simulator = None  # PaperFillSimulator (paper mode only)

    # Broadcast callback (set by app.py to push updates to frontend)
    self._broadcast: Callable | None = None

    # In-memory guard: prevents scheduling duplicate entries before DB record exists
    # Key: f"{portfolio_id}_{symbol}_{side}" → timestamp
    self._in_flight_entries: dict[str, float] = {}

    # In-memory cache of open position keys — avoids DB session per dedup check
    # Populated on activate_portfolio, updated on position open/close callbacks
    # Key: f"{portfolio_id}_{symbol}_{side}"
    self._open_position_keys: set[str] = set()

    # CS-042: the StrategyExecutor reads this via the callback wired in
    # _initialize_components. Keep the conversion "long"/"short" → "Buy"/"Sell"
    # in one place rather than scattered across executor + coordinator.
    def _is_position_key_open(portfolio_id: int, symbol: str, direction: str) -> bool:
      side = "Buy" if direction == "long" else "Sell"
      return f"{portfolio_id}_{symbol}_{side}" in self._open_position_keys

    self._is_position_key_open = _is_position_key_open

    # Kelly allocation weight cache — loaded from DB, refreshed on recompute
    # Key: portfolio_id → {"weights": {...}, "n_assets": int, "computed_at": str}
    self._kelly_alloc_cache: dict[int, dict] = {}

    # SL price cache — avoids redundant API calls when 40 symbols fire bar close together
    # Use None (not {}) so the cache check works even when there are no SL orders
    self._sl_cache: dict[str, float] | None = None
    self._sl_cache_time: float = 0.0

  def _schedule(self, coro) -> None:
    """Schedule a coroutine on the main event loop from any thread."""
    if self._loop and self._loop.is_running():
      asyncio.run_coroutine_threadsafe(coro, self._loop)
    else:
      logger.warning("No event loop — cannot schedule coroutine")

  # ── Lifecycle ─────────────────────────────────────────────────────

  async def start(self) -> bool:
    """Initialize all components and connect to Bybit."""
    if self._running:
      logger.warning("Engine already running")
      return True

    if not self._settings.live_engine_enabled:
      logger.warning("Live engine is disabled (live_engine_enabled=False)")
      return False

    logger.info("Starting live trading engine...")

    # Clear in-memory caches from any previous run — they'll be rebuilt
    # from DB during activate_portfolio().  Prevents stale entries from
    # blocking new trades after a stop→start cycle.  (Bug 35)
    self._open_position_keys.clear()
    self._in_flight_entries.clear()

    try:
      # 1. Trading client (REST) — paper or live
      if self._settings.paper_trading:
        from backend.exchange.paper_trading import PaperTradingClient
        self._trading_client = PaperTradingClient(
          initial_balance=self._settings.paper_initial_balance,
          testnet=self._settings.bybit_testnet,
        )
        logger.info("Paper trading mode — using simulated exchange (balance=$%.2f)",
                     self._settings.paper_initial_balance)
      else:
        self._trading_client = BybitTradingClient(
          api_key=self._settings.bybit_api_key,
          api_secret=self._settings.bybit_api_secret,
          testnet=self._settings.bybit_testnet,
          use_mainnet_prices=self._settings.testnet_use_mainnet_prices,
        )
        # Test connection (live mode only)
        if not self._trading_client.test_connection():
          logger.error("Failed to connect to Bybit — check API keys")
          return False

        # Verify Bybit account is in one-way mode. The engine assumes
        # one-way everywhere (sizing, reconciliation, martingale pyramiding,
        # the single-position-per-symbol-side DB invariant). Hedge mode
        # would silently create two positions per symbol and break those
        # invariants. Refuse to start rather than trade in the wrong mode.
        mode = self._trading_client.get_position_mode()
        if mode == "hedge":
          logger.critical(
            "Bybit account is in HEDGE mode. The engine requires one-way mode "
            "— hedge would break martingale pyramiding and reconciliation. "
            "Switch to one-way in the Bybit UI (Preferences → Position Mode) "
            "and restart. Refusing to start.",
          )
          return False
        if mode == "unknown":
          logger.warning(
            "Could not verify Bybit position mode (probe returned 'unknown'). "
            "Continuing start, but confirm one-way mode is set on the account.",
          )
        else:
          logger.info("Bybit position mode verified: one_way")

      # 2. Position manager
      self._position_manager = PositionManager(self._trading_client)

      # 3. TSL tracker
      self._tsl_tracker = TrailingStopTracker(
        on_trigger=self._on_tsl_trigger,
      )

      # 3b. Safety monitor (circuit breakers, daily loss limits)
      self._safety_monitor = SafetyMonitor(
        get_equity_fn=self._get_total_equity,
        db_factory=self._db_factory,
      )

      # 4. Order manager
      self._order_manager = OrderManager(
        trading_client=self._trading_client,
        db_factory=self._db_factory,
        on_position_opened=self._on_position_opened,
        on_position_closed=self._on_position_closed,
      )
      self._order_manager.set_scheduler(self._schedule)

      # 5. Paper fill simulator (paper mode only)
      if self._settings.paper_trading:
        from backend.exchange.paper_fill_simulator import PaperFillSimulator
        self._paper_simulator = PaperFillSimulator(
          paper_client=self._trading_client,
          on_order=self._order_manager.on_order_update,
          on_execution=self._order_manager.on_execution,
          slippage_pct=self._settings.paper_slippage_pct,
        )
        self._trading_client.set_fill_simulator(self._paper_simulator)

      # 6. Candle aggregator
      self._candle_aggregator = CandleAggregator(
        on_bar_close=self._on_bar_close,
        on_bar_persist=self._persist_bar_to_db,
      )

      # 7. Strategy executor
      # CS-042: supply a cache-lookup callback so the per-signal dedup
      # inside the executor can short-circuit on the in-memory cache
      # instead of opening a fresh DB session per candidate entry.
      self._strategy_executor = StrategyExecutor(
        candle_aggregator=self._candle_aggregator,
        db_factory=self._db_factory,
        on_signal=self._on_strategy_signal,
        position_key_check=self._is_position_key_open,
      )

      # 8. WebSocket manager — public always, private only in live mode
      if self._settings.paper_trading:
        self._ws_manager = BybitWSManager(
          api_key="", api_secret="",  # No auth for paper
          testnet=self._settings.bybit_testnet,
          on_kline=self._on_ws_kline,
          on_ticker=self._on_paper_ticker,  # Route through simulator
          # No private callbacks — simulator handles fills
        )
      else:
        self._ws_manager = BybitWSManager(
          api_key=self._settings.bybit_api_key,
          api_secret=self._settings.bybit_api_secret,
          testnet=self._settings.bybit_testnet,
          on_kline=self._on_ws_kline,
          on_ticker=self._on_ws_ticker,
          on_order=self._order_manager.on_order_update,
          on_execution=self._order_manager.on_execution,
          on_position=self._on_ws_position,
          on_wallet=self._on_ws_wallet,
        )
      self._ws_manager.start()

      # 9. Start background loops (live mode only)
      if not self._settings.paper_trading:
        self._reconciliation_task = asyncio.create_task(self._reconciliation_loop())
        self._fill_poll_task = asyncio.create_task(self._fill_poll_loop())
        self._wallet_refresh_task = asyncio.create_task(self._wallet_refresh_loop())
        self._kelly_recompute_task = asyncio.create_task(self._kelly_recompute_loop())

      self._running = True
      self._loop = asyncio.get_running_loop()
      mode = "PAPER" if self._settings.paper_trading else "LIVE"
      logger.info("Live trading engine started successfully (%s mode)", mode)

      # Log event
      self._log_event(None, "engine_started", "", {
        "testnet": self._settings.bybit_testnet,
        "paper": self._settings.paper_trading,
      })

      # Auto-resume: reactivate portfolios that were live trading before shutdown
      await self._auto_resume_portfolios()

      await self._broadcast_status()
      return True

    except Exception as e:
      logger.error("Failed to start engine: %s", e)
      # Tear down whatever was partially initialized so the next start()
      # call runs against a clean slate. Without this, a failure after
      # ws_manager.start() (line 211) would leave an orphan WebSocket
      # and any already-scheduled background tasks still running.
      self._teardown_partial_start()
      return False

  def _teardown_partial_start(self) -> None:
    """Release partially-initialized components when start() fails mid-way.

    Mirrors the safe subset of ``shutdown()`` — only touches components
    that were actually assigned, swallows exceptions so one failed
    cleanup step cannot mask another.

    CS-003: reset ``_running=False`` and ``_loop=None`` so a retry
    genuinely re-runs ``start()`` from scratch. Prior to this fix the
    flag leaked True past teardown because it was set at line 229
    before ``_auto_resume_portfolios`` could raise — the next call
    to ``start()`` then returned True at the ``if self._running``
    short-circuit without ever rebuilding the torn-down components.
    """
    # Cancel any background tasks that were created before the failure
    for task_attr in (
      "_reconciliation_task", "_fill_poll_task",
      "_wallet_refresh_task", "_kelly_recompute_task",
    ):
      task = getattr(self, task_attr, None)
      if task and not task.done():
        try:
          task.cancel()
        except Exception as te:
          logger.warning("Teardown: error cancelling %s: %s", task_attr, te)
      setattr(self, task_attr, None)

    # Stop WebSocket if it was started
    if self._ws_manager is not None:
      try:
        self._ws_manager.stop()
      except Exception as we:
        logger.warning("Teardown: error stopping ws_manager: %s", we)
      self._ws_manager = None

    # Drop other component refs so a retry starts from a clean slate
    self._order_manager = None
    self._strategy_executor = None
    self._candle_aggregator = None
    self._paper_simulator = None
    self._safety_monitor = None
    self._tsl_tracker = None
    self._position_manager = None
    self._trading_client = None

    # CS-003: clear the running flag so the next start() call isn't
    # short-circuited by the "already running" guard in start().
    self._running = False
    self._loop = None

  async def _auto_resume_portfolios(self) -> None:
    """Re-activate portfolios that were live trading before shutdown.

    Queries DB for portfolio_type in ('real', 'paper') and is_active=True,
    then re-activates them through the normal activation flow.
    """
    db = self._db_factory()
    try:
      live_portfolios = db.query(Portfolio).filter(
        Portfolio.portfolio_type.in_(["real", "paper"]),
        Portfolio.is_active == True,
      ).all()

      if not live_portfolios:
        logger.info("No live portfolios to auto-resume")
        return

      logger.info("Auto-resuming %d portfolio(s) (real + paper)...", len(live_portfolios))
      # CS-003: count resume outcomes so the summary is visible to the
      # operator. Per-portfolio errors are intentionally caught so one
      # bad config doesn't block the others — but the aggregate count
      # must surface via event + log so a silent partial-resume isn't
      # invisible on the dashboard.
      resumed_ok = 0
      resumed_fail: list[tuple[int, str, str]] = []  # (id, name, reason)
      for p in live_portfolios:
        try:
          success = await self.activate_portfolio(p.id)
          if success:
            logger.info("  Resumed portfolio %d: %s", p.id, p.name)
            resumed_ok += 1
          else:
            logger.error("  Failed to resume portfolio %d: %s", p.id, p.name)
            resumed_fail.append((p.id, p.name, "activate_portfolio returned False"))
        except Exception as e:
          logger.error("  Error resuming portfolio %d: %s", p.id, e)
          resumed_fail.append((p.id, p.name, repr(e)))

      if resumed_fail:
        logger.error(
          "Auto-resume: %d/%d portfolio(s) FAILED to resume: %s",
          len(resumed_fail), len(live_portfolios),
          [(pid, pname) for pid, pname, _ in resumed_fail],
        )
        self._log_event(None, "auto_resume_partial", "", {
          "total": len(live_portfolios),
          "ok": resumed_ok,
          "failed": [
            {"portfolio_id": pid, "name": pname, "reason": reason}
            for pid, pname, reason in resumed_fail
          ],
        })
      else:
        logger.info("Auto-resume: all %d portfolio(s) resumed OK", resumed_ok)

      # Restore TSL tracking for open positions with trailing stops
      # (runs in thread pool — _restore_tsl_trackers does blocking REST calls)
      loop = asyncio.get_running_loop()
      await loop.run_in_executor(None, self._restore_tsl_trackers)

    finally:
      db.close()

  def _restore_tsl_trackers(self) -> None:
    """Restore TSL tracking for open positions after restart.

    Loads saved trailing_extreme from DB, re-registers in the in-memory
    TrailingStopTracker, catches up with current market price, and amends
    the exchange SL if the TSL level moved during downtime.
    """
    if not self._tsl_tracker:
      return

    db = self._db_factory()
    try:
      positions = db.query(Position).filter(
        Position.is_open == True,
        Position.trailing_stop_distance > 0,
      ).all()

      if not positions:
        logger.info("No TSL positions to restore")
        return

      logger.info("Restoring TSL tracking for %d position(s)...", len(positions))

      # Fetch current market prices from exchange (one API call)
      exchange_prices: dict[str, float] = {}
      if self._trading_client:
        try:
          ex_pos_list = self._trading_client.get_positions()
          for ep in ex_pos_list:
            sym = ep.get("symbol", "")
            mp = ep.get("mark_price", 0)
            if sym and mp > 0:
              exchange_prices[sym] = mp
        except Exception as e:
          logger.warning("Could not fetch exchange prices for TSL restore: %s", e)

      for pos in positions:
        saved_extreme = pos.trailing_extreme
        if saved_extreme is None or saved_extreme <= 0:
          saved_extreme = pos.entry_price

        # Re-register with saved extreme (trail_offset=0: already activated pre-restart)
        self._tsl_tracker.register(
          position_db_id=pos.id,
          portfolio_id=pos.portfolio_id,
          symbol=pos.symbol,
          side=pos.side,
          entry_price=pos.entry_price,
          trail_points=pos.trailing_stop_distance,
          trail_offset=0,
          initial_extreme=saved_extreme,
        )

        logger.info(
          "  TSL restored: pos=%d %s %s extreme=%.4f trail=%.4f",
          pos.id, pos.symbol, pos.side, saved_extreme, pos.trailing_stop_distance,
        )

        # Catch up with current market price (may have moved during downtime)
        market_price = exchange_prices.get(pos.symbol, 0)
        if market_price > 0:
          # Feed current price to tracker to update extreme naturally
          self._tsl_tracker.on_price_update(pos.symbol, market_price)

          # Also update DB extreme if market moved further
          if pos.side == "Buy" and market_price > saved_extreme:
            pos.trailing_extreme = market_price
            logger.info(
              "  TSL extreme caught up: pos=%d %s saved=%.4f -> market=%.4f",
              pos.id, pos.symbol, saved_extreme, market_price,
            )
          elif pos.side == "Sell" and market_price < saved_extreme:
            pos.trailing_extreme = market_price
            logger.info(
              "  TSL extreme caught up: pos=%d %s saved=%.4f -> market=%.4f",
              pos.id, pos.symbol, saved_extreme, market_price,
            )

        # Amend exchange SL if the correct stop has moved
        current_extreme = pos.trailing_extreme or saved_extreme
        if pos.side == "Buy":
          correct_stop = current_extreme - pos.trailing_stop_distance
        else:
          correct_stop = current_extreme + pos.trailing_stop_distance

        if correct_stop > 0 and pos.sl_price and self._order_manager:
          should_amend = (
            (pos.side == "Buy" and correct_stop > pos.sl_price)
            or (pos.side == "Sell" and correct_stop < pos.sl_price)
          )
          if should_amend:
            logger.info(
              "  TSL restore amend: pos=%d %s SL %.4f -> %.4f",
              pos.id, pos.symbol, pos.sl_price, correct_stop,
            )
            sl_ok = self._order_manager.modify_sl_order(pos.id, correct_stop)
            if sl_ok:
              pos.sl_price = correct_stop

      db.commit()
      logger.info(
        "TSL restoration complete: %d position(s) tracked",
        self._tsl_tracker.get_tracked_count(),
      )

    except Exception as e:
      logger.error("TSL restoration failed: %s", e)
    finally:
      db.close()

  async def shutdown(self) -> None:
    """Graceful shutdown — cancel entries, keep exits alive."""
    if not self._running:
      return

    logger.info("Shutting down live trading engine...")
    self._running = False

    # Stop background loops
    if self._reconciliation_task and not self._reconciliation_task.done():
      self._reconciliation_task.cancel()
    if self._fill_poll_task and not self._fill_poll_task.done():
      self._fill_poll_task.cancel()
    if self._wallet_refresh_task and not self._wallet_refresh_task.done():
      self._wallet_refresh_task.cancel()
    if self._kelly_recompute_task and not self._kelly_recompute_task.done():
      self._kelly_recompute_task.cancel()

    # Deactivate all portfolios (without closing positions)
    if self._strategy_executor:
      for pid in list(self._strategy_executor.get_active_portfolios()):
        self._strategy_executor.deactivate_portfolio(pid)

    # Shutdown order manager (cancels entries, keeps exits)
    if self._order_manager:
      self._order_manager.shutdown()

    # Disconnect WebSockets
    if self._ws_manager:
      self._ws_manager.stop()

    self._log_event(None, "engine_stopped", "", {})
    logger.info("Live trading engine shut down")
    await self._broadcast_status()

  # ── Portfolio Management ──────────────────────────────────────────

  async def activate_portfolio(self, portfolio_id: int) -> bool:
    """Activate a portfolio for live trading."""
    if not self._running:
      logger.error("Engine not running — cannot activate portfolio")
      return False

    if not self._strategy_executor:
      return False

    # Mode-mismatch guard — refuse to activate a portfolio in a mode that
    # conflicts with its promoted type. Prevents paper trades from polluting
    # live martingale streak (and vice versa). "saved" portfolios pass through;
    # they get their type set on first activation below.
    db = self._db_factory()
    try:
      p = db.get(Portfolio, portfolio_id)
      if p and p.portfolio_type in ("paper", "real"):
        engine_is_paper = bool(self._settings.paper_trading)
        portfolio_is_paper = (p.portfolio_type == "paper")
        if engine_is_paper != portfolio_is_paper:
          logger.error(
            "ACTIVATION REFUSED: portfolio %d (%s) is type=%r but engine is "
            "paper_trading=%s. Will NOT activate to prevent martingale state "
            "crossover. Either flip PAPER_TRADING in .env to match the "
            "portfolio, or clone the portfolio for the other mode.",
            portfolio_id, p.name or "?", p.portfolio_type, engine_is_paper,
          )
          return False
    finally:
      db.close()

    success = self._strategy_executor.activate_portfolio(portfolio_id)
    if not success:
      return False

    # Get resolved timeframes from the strategy executor
    ap = self._strategy_executor._active.get(portfolio_id)
    base_tf = ap.base_tf if ap else "30"
    extra_tfs = ap.extra_tfs if ap else []

    # Subscribe to WebSocket feeds for all portfolio symbols
    db = self._db_factory()
    try:
      from backend.db.models import PortfolioAsset
      assets = db.query(PortfolioAsset).filter(
        PortfolioAsset.portfolio_id == portfolio_id,
        PortfolioAsset.is_active == True,
      ).all()

      for asset in assets:
        if self._ws_manager:
          self._ws_manager.subscribe_symbol(
            asset.symbol, base_tf=base_tf, extra_tfs=extra_tfs,
          )

      # Populate open position cache for dedup (avoids DB query per signal)
      open_positions = db.query(Position).filter(
        Position.portfolio_id == portfolio_id,
        Position.is_open == True,
      ).all()
      for pos in open_positions:
        self._open_position_keys.add(f"{pos.portfolio_id}_{pos.symbol}_{pos.side}")

      # Update portfolio status in DB — preserve the portfolio's own type
      portfolio = db.get(Portfolio, portfolio_id)
      if portfolio:
        # Respect the portfolio's promoted type (paper/real).
        # Only set type if it's still "saved" (first activation via Live page).
        if portfolio.portfolio_type not in ("paper", "real"):
          portfolio.portfolio_type = "real" if not self._settings.paper_trading else "paper"
        # Only set activated_at on the FIRST activation. Auto-resume after
        # a backend restart calls activate_portfolio for every is_active
        # portfolio — if we reset the timestamp here, every restart wipes
        # the original activation date, which downstream consumers
        # (paper_refresh.run_refresh, KPI displays) treat as the start of
        # the "since-activation" trade window. Preserving it keeps trade
        # history visible across restarts.
        if portfolio.activated_at is None:
          portfolio.activated_at = datetime.now(timezone.utc)
        portfolio.is_active = True
        db.commit()

      self._log_event(portfolio_id, "portfolio_activated", "", {
        "symbols": [a.symbol for a in assets],
        "paper": self._settings.paper_trading,
      })

      # T-002i: seed _kelly_alloc_cache from persisted latest_asset_scores
      # if no current KellyAllocation row exists for a hybrid portfolio.
      # Bridges the gap between /save-phase2-track (writes scores but no
      # KellyAllocation row) and the first 03:00 NYT refresh (writes both).
      # Non-hybrid portfolios are skipped.
      if (portfolio and portfolio.mode == "hybrid"
          and portfolio_id not in self._kelly_alloc_cache):
        try:
          from backend.db.models import KellyAllocation
          has_row = (
            db.query(KellyAllocation)
            .filter(
              KellyAllocation.portfolio_id == portfolio_id,
              KellyAllocation.is_current == True,  # noqa: E712
            )
            .first()
          )
          if not has_row:
            src = json.loads(portfolio.source_strategies or "{}")
            scores = (src.get("step2_result") or {}).get("latest_asset_scores") or {}
            if scores:
              from backend.services.hybrid_portfolio_refresh import (
                _project_weights_from_scores,
              )
              basket = [
                (f"B{a.batch_id}_{a.symbol}_{a.trade_side}"
                 if a.batch_id else f"{a.symbol}_{a.trade_side}")
                for a in assets
              ]
              weights = _project_weights_from_scores(basket, scores)
              if weights:
                self._kelly_alloc_cache[portfolio_id] = {
                  "weights": weights,
                  "n_assets": len(weights),
                  "computed_at": datetime.now(timezone.utc).isoformat(),
                }
                logger.info(
                  "activate_portfolio: seeded kelly cache for hybrid "
                  "portfolio %d from persisted scores (%d weights)",
                  portfolio_id, len(weights),
                )
        except Exception as e:
          logger.warning(
            "activate_portfolio: kelly cache seed failed for %d: %s",
            portfolio_id, e,
          )

    finally:
      db.close()

    # Register with scanner exclusion list (prevent duplicate signals)
    from backend.scanner.scanner_core import register_live_portfolio
    register_live_portfolio(portfolio_id)

    logger.info("Portfolio %d activated for live trading", portfolio_id)
    await self._broadcast_status()
    return True

  async def refresh_portfolio(self, portfolio_id: int) -> dict:
    """Refresh portfolio asset list without killing open positions.

    Re-reads active assets from DB, subscribes new symbols to WS,
    unsubscribes removed symbols (if no open positions).
    """
    if not self._strategy_executor:
      return {"error": "Strategy executor not initialized"}

    result = self._strategy_executor.refresh_portfolio(portfolio_id)

    if "error" not in result:
      # Get TFs from active portfolio for WS subscription
      ap = self._strategy_executor._active.get(portfolio_id)
      base_tf = ap.base_tf if ap else "30"
      extra_tfs = ap.extra_tfs if ap else []

      # Subscribe WS for newly added symbols
      for sym in result.get("added", []):
        if self._ws_manager:
          self._ws_manager.subscribe_symbol(
            sym, base_tf=base_tf, extra_tfs=extra_tfs,
          )

      # Unsubscribe WS for removed symbols
      for sym in result.get("removed", []):
        if self._ws_manager:
          self._ws_manager.unsubscribe_symbol(sym)

      self._log_event(portfolio_id, "portfolio_refreshed", "", result)
      await self._broadcast_status()

    return result

  async def deactivate_portfolio(
    self,
    portfolio_id: int,
    close_positions: bool = False,
  ) -> bool:
    """Deactivate a portfolio.

    Args:
      close_positions: If True, close all open positions for this portfolio.
                       If False, positions remain open with their exit orders.
    """
    if not self._strategy_executor:
      return False

    self._strategy_executor.deactivate_portfolio(portfolio_id)

    # Clear open position cache entries for this portfolio
    to_remove = {k for k in self._open_position_keys if k.startswith(f"{portfolio_id}_")}
    self._open_position_keys -= to_remove

    # Unregister from scanner exclusion list
    from backend.scanner.scanner_core import unregister_live_portfolio
    unregister_live_portfolio(portfolio_id)

    if close_positions:
      await self._close_all_portfolio_positions(portfolio_id)

    # Update portfolio status
    db = self._db_factory()
    try:
      portfolio = db.get(Portfolio, portfolio_id)
      if portfolio:
        # Preserve portfolio_type (paper/real) — don't reset to "saved"
        portfolio.deactivated_at = datetime.now(timezone.utc)
        portfolio.is_active = False
        db.commit()

      self._log_event(portfolio_id, "portfolio_deactivated", "", {
        "close_positions": close_positions,
      })
    finally:
      db.close()

    logger.info("Portfolio %d deactivated (close_positions=%s)", portfolio_id, close_positions)
    await self._broadcast_status()
    return True

  async def close_position(self, position_id: int) -> bool:
    """Manually close a single position."""
    if not self._order_manager:
      return False

    db = self._db_factory()
    try:
      pos = db.get(Position, position_id)
      if not pos or not pos.is_open:
        return False

      # Run blocking REST call in thread pool (H1 audit fix)
      loop = asyncio.get_running_loop()
      om = self._order_manager
      link_id = await loop.run_in_executor(
        None,
        lambda: om.place_market_close(
          position_id, pos.symbol, pos.side, pos.quantity, reason="manual",
        ),
      )

      # Remove from TSL tracking
      if self._tsl_tracker:
        self._tsl_tracker.unregister(position_id)

      self._log_event(pos.portfolio_id, "manual_close", pos.symbol, {
        "position_id": position_id,
      })

      return link_id is not None
    finally:
      db.close()

  async def close_all_positions(self) -> int:
    """Emergency: close ALL open positions across all portfolios."""
    db = self._db_factory()
    try:
      open_positions = db.query(Position).filter(Position.is_open == True).all()
      closed = 0
      loop = asyncio.get_running_loop()
      for pos in open_positions:
        if self._order_manager:
          om = self._order_manager
          # Run blocking REST call in thread pool (H1 audit fix)
          link_id = await loop.run_in_executor(
            None,
            lambda p=pos: om.place_market_close(
              p.id, p.symbol, p.side, p.quantity, reason="emergency_close",
            ),
          )
          if link_id:
            closed += 1

      self._log_event(None, "emergency_close_all", "", {"positions_closed": closed})
      logger.warning("Emergency close: %d positions closed", closed)
      return closed
    finally:
      db.close()

  async def emergency_stop(self) -> None:
    """Kill switch — close all positions and shut down engine."""
    logger.warning("EMERGENCY STOP activated")
    await self.close_all_positions()
    await self.shutdown()
    self._log_event(None, "emergency_stop", "", {})

  # ── Status ────────────────────────────────────────────────────────

  def get_status(self) -> dict:
    """Get full engine status for frontend."""
    ws_status = self._ws_manager.is_connected() if self._ws_manager else {}
    tsl_status = self._tsl_tracker.get_status() if self._tsl_tracker else []
    active_portfolios = (
      self._strategy_executor.get_active_portfolios()
      if self._strategy_executor else []
    )

    equity = 0
    available = 0
    wallet_bal = 0
    if self._position_manager and self._running:
      try:
        equity = self._position_manager.get_exchange_equity()
        available = self._position_manager.get_available_balance()
        wallet_bal = self._position_manager.get_wallet_balance()
      except Exception as e:
        logger.debug("Failed to fetch wallet balances for status: %s", e)

    reserve = self._settings.reserve_account_usd
    total_equity = reserve + wallet_bal

    # Count risk-free positions (SL at BE or better)
    risk_free_count = 0
    try:
      db = self._db_factory()
      try:
        risk_free_count = db.query(Position).filter(
          Position.is_open == True,
          Position.risk_removed == True,
        ).count()
      finally:
        db.close()
    except Exception as e:
      logger.debug("Failed to count risk-free positions for status: %s", e)

    safety = self._safety_monitor.get_status() if self._safety_monitor else {}

    return {
      "running": self._running,
      "enabled": self._settings.live_engine_enabled,
      "testnet": self._settings.bybit_testnet,
      "testnet_use_mainnet_prices": self._settings.testnet_use_mainnet_prices,
      "paper_trading": self._settings.paper_trading,
      "websocket": ws_status,
      "active_portfolios": active_portfolios,
      "tsl_tracked": len(tsl_status),
      "equity": equity,
      "wallet_balance": wallet_bal,
      "available_balance": available,
      "reserve_account": reserve,
      "total_equity": total_equity,
      "breathing_room_pct": self._settings.margin_breathing_room_pct,
      "risk_free_positions": risk_free_count,
      "safety": safety,
    }

  # ── WebSocket Callbacks ───────────────────────────────────────────

  def _on_ws_kline(self, symbol: str, kline_data: dict) -> None:
    """Forward kline data to CandleAggregator."""
    if self._candle_aggregator:
      self._candle_aggregator.on_kline_update(symbol, kline_data)

  def _on_ws_ticker(self, symbol: str, last_price: float) -> None:
    """Forward ticker price to TSL tracker."""
    if self._tsl_tracker:
      self._tsl_tracker.on_price_update(symbol, last_price)

  def _on_paper_ticker(self, symbol: str, last_price: float) -> None:
    """Route ticker to both TSL tracker and paper fill simulator."""
    if self._tsl_tracker:
      self._tsl_tracker.on_price_update(symbol, last_price)
    if self._paper_simulator:
      self._paper_simulator.on_price_update(symbol, last_price)

  def _on_ws_position(self, data: dict) -> None:
    """Handle position update from exchange (for unrealized PnL broadcast)."""
    if self._broadcast:
      self._schedule(self._broadcast({
        "type": "position_update",
        "data": data,
      }))

  def _on_ws_wallet(self, data: dict) -> None:
    """Handle wallet update from exchange (for equity broadcast)."""
    if self._broadcast:
      self._schedule(self._broadcast({
        "type": "equity_update",
        "data": data,
      }))

  # ── Component Callbacks ───────────────────────────────────────────

  def _on_bar_close(self, symbol: str, timeframe: str, bar_data: dict) -> None:
    """Forward completed bar to StrategyExecutor + update position extremes."""
    if self._strategy_executor:
      self._strategy_executor.on_bar_close(symbol, timeframe, bar_data)

    # C3: Update extreme prices for open positions on this symbol
    self._update_position_extremes(symbol, bar_data)

    # Fetch exchange SL prices ONCE per bar close (1 API call for all positions)
    exchange_sl_map = self._sync_exchange_sl_prices()

    # C2: Amend exchange SL for positions with active TSL
    self._amend_tsl_on_bar(symbol, exchange_sl_map)

  def _update_position_extremes(self, symbol: str, bar_data: dict) -> None:
    """C3: Update extreme high/low for open positions from bar data."""
    bar_high = bar_data.get("high", 0)
    bar_low = bar_data.get("low", 0)
    if bar_high <= 0 or bar_low <= 0:
      return

    db = self._db_factory()
    try:
      positions = db.query(Position).filter(
        Position.symbol == symbol,
        Position.is_open == True,
      ).all()
      changed = False
      for pos in positions:
        if pos.side == "Buy":
          if pos.trailing_extreme is None or bar_high > pos.trailing_extreme:
            pos.trailing_extreme = bar_high
            changed = True
            # Sync to in-memory tracker so both layers stay consistent
            if pos.trailing_stop_distance and pos.trailing_stop_distance > 0 and self._tsl_tracker:
              self._tsl_tracker.update_extreme(pos.id, bar_high)
        else:  # Sell
          if pos.trailing_extreme is None or bar_low < pos.trailing_extreme:
            pos.trailing_extreme = bar_low
            changed = True
            if pos.trailing_stop_distance and pos.trailing_stop_distance > 0 and self._tsl_tracker:
              self._tsl_tracker.update_extreme(pos.id, bar_low)
      if changed:
        db.commit()
    except Exception as e:
      logger.error("Failed to update position extremes for %s: %s", symbol, e)
    finally:
      db.close()

  def _sync_exchange_sl_prices(self) -> dict[str, float]:
    """Fetch ALL open orders from exchange once per bar-close cycle.

    Returns {order_link_id: trigger_price} for SL sync.
    One API call covers all symbols — no per-position queries needed.

    Uses a 5-second cache so that when 40 symbols fire bar close together
    at the same 30m mark, only 1 HTTP call is made instead of 40.
    """
    if not self._trading_client:
      return {}

    # Return cached result if fresh (all 40 symbols share the same data)
    now = time.monotonic()
    if self._sl_cache is not None and (now - self._sl_cache_time) < 5.0:
      return self._sl_cache

    try:
      exchange_orders = self._trading_client.get_open_orders()
      result = {}
      for eo in exchange_orders:
        link = eo.get("orderLinkId", "")
        if not link:
          continue
        raw = eo.get("triggerPrice", 0)
        try:
          tp = float(raw) if raw != "" else 0.0
        except (ValueError, TypeError):
          tp = 0.0
        if tp > 0:
          result[link] = tp
      self._sl_cache = result
      self._sl_cache_time = now
      return result
    except Exception as e:
      logger.error("Failed to fetch exchange orders for SL sync: %s", e)
      return {}

  def _amend_tsl_on_bar(self, symbol: str, exchange_sl_map: dict[str, float]) -> None:
    """C2: Amend exchange SL to reflect TSL trail on each bar close.

    For positions with trailing_stop_distance > 0, calculates the new stop
    from the extreme price and amends the exchange SL if it moved.
    Syncs DB to exchange SL first to respect manual overrides.
    """
    if not self._order_manager:
      return

    db = self._db_factory()
    try:
      positions = db.query(Position).filter(
        Position.symbol == symbol,
        Position.is_open == True,
        Position.trailing_stop_distance > 0,
      ).all()

      for pos in positions:
        # Sync DB to exchange SL — catches manual overrides on Bybit
        if pos.sl_order_link_id and pos.sl_order_link_id in exchange_sl_map:
          exchange_sl = exchange_sl_map[pos.sl_order_link_id]
          if abs(exchange_sl - (pos.sl_price or 0)) > 0.0001:
            logger.warning(
              "SL synced from exchange: %s pos=%d DB=%.4f -> exchange=%.4f",
              pos.symbol, pos.id, pos.sl_price or 0, exchange_sl,
            )
            pos.sl_price = exchange_sl
            db.commit()

        extreme = pos.trailing_extreme
        trail = pos.trailing_stop_distance
        if not extreme or not trail or trail <= 0:
          continue

        # Calculate new stop level from extreme
        if pos.side == "Buy":
          new_stop = extreme - trail
        else:
          new_stop = extreme + trail

        if new_stop <= 0:
          continue

        # Only amend if stop actually moved in the favorable direction
        # (uses synced exchange value, so manual overrides are respected)
        if pos.sl_price:
          if pos.side == "Buy" and new_stop <= pos.sl_price:
            continue  # TSL only moves up for longs
          if pos.side == "Sell" and new_stop >= pos.sl_price:
            continue  # TSL only moves down for shorts

        logger.info(
          "TSL amend: %s %s extreme=%.4f trail=%.4f -> new_stop=%.4f (was %.4f)",
          pos.symbol, pos.side, extreme, trail, new_stop, pos.sl_price or 0,
        )
        sl_ok = self._order_manager.modify_sl_order(pos.id, new_stop)
        if sl_ok:
          pos.sl_price = new_stop
          db.commit()
        else:
          logger.error("TSL amend FAILED for %s pos=%d", pos.symbol, pos.id)
    except Exception as e:
      logger.error("TSL amend error for %s: %s", symbol, e)
    finally:
      db.close()

  def _persist_bar_to_db(self, symbol: str, timeframe: str, bar_data: dict) -> None:
    """Persist a completed bar to OHLCVData so DB stays current.

    Runs in a background thread so DB writes don't block signal processing.
    At bar close, 40 symbols fire near-simultaneously — the signal/order
    pipeline must not wait for 40 sequential DB commits.
    """
    def _write():
      try:
        from backend.data.data_manager import DataManager
        dm = DataManager()
        dm.upsert_candles(symbol, timeframe, [bar_data])
      except Exception as e:
        logger.error("Background bar persist failed for %s/%s: %s", symbol, timeframe, e)

    threading.Thread(target=_write, name=f"persist-{symbol}", daemon=True).start()

  def _on_strategy_signal(self, signal: StrategySignal) -> None:
    """Handle a trading signal from StrategyExecutor."""
    if not self._order_manager or not self._position_manager:
      return

    logger.info(
      "Signal: %s %s %s %s entry_id=%s",
      signal.symbol, signal.action, signal.direction, signal.comment, signal.entry_id,
    )

    if signal.action == "entry":
      self._handle_entry_signal(signal)
    elif signal.action == "exit":
      self._handle_exit_signal(signal)
    elif signal.action == "register_exit":
      self._handle_register_exit(signal)

  def _handle_entry_signal(self, signal: StrategySignal) -> None:
    """Process an entry signal — calculate size, prepare symbol, place order."""
    side = "Buy" if signal.direction == "long" else "Sell"

    if signal.sl_price <= 0:
      logger.warning("Entry signal without SL price — skipping: %s", signal.symbol)
      # Record a DB event so this isn't a silent failure. Discovered 2026-05-18:
      # all 30 portfolios produced 0 fills for 70 days because every entry signal
      # arrived here with sl_price=0 (strategy registers SL on bar N+1, not N).
      # This event makes the same bug detectable in minutes instead of months.
      self._log_event(signal.portfolio_id, "entry_blocked_no_sl", signal.symbol, {
        "entry_id": signal.entry_id,
        "direction": signal.direction,
        "reason": "no SL price on entry signal",
      })
      return

    # Dedup: skip if there's already an open position (in-memory cache, O(1))
    pos_key = f"{signal.portfolio_id}_{signal.symbol}_{side}"
    if pos_key in self._open_position_keys:
      logger.info("Skipping duplicate — open position exists for %s %s (cached)", signal.symbol, side)
      return

    # In-memory dedup: block if entry already in flight (hasn't hit DB yet)
    flight_key = f"{signal.portfolio_id}_{signal.symbol}_{side}"
    now = time.time()
    # Clean stale entries (> 120 seconds — safety net)
    stale = [k for k, t in self._in_flight_entries.items() if now - t > 120]
    for k in stale:
      self._in_flight_entries.pop(k, None)
    if flight_key in self._in_flight_entries:
      logger.info(
        "Skipping entry — already in flight: %s (%.1fs ago)",
        flight_key, now - self._in_flight_entries[flight_key],
      )
      return
    self._in_flight_entries[flight_key] = now

    # Get latest price for sizing — use portfolio's actual base TF
    base_tf = "30"  # fallback
    if self._strategy_executor:
      ap = self._strategy_executor._active.get(signal.portfolio_id)
      if ap:
        base_tf = ap.base_tf
    latest_bar = (
      self._candle_aggregator.get_latest_bar(signal.symbol, base_tf)
      if self._candle_aggregator else None
    )
    entry_price = signal.limit_price if signal.limit_price > 0 else (
      latest_bar["close"] if latest_bar else 0
    )
    if entry_price <= 0:
      logger.warning("Cannot determine entry price for %s", signal.symbol)
      self._in_flight_entries.pop(flight_key, None)
      return

    # Validate SL is on correct side of entry price (Bug 37 safety net)
    if side == "Buy" and signal.sl_price >= entry_price:
      logger.warning(
        "SL (%.5f) >= entry (%.5f) for Buy — invalid SL direction, skipping entry: %s",
        signal.sl_price, entry_price, signal.symbol,
      )
      self._in_flight_entries.pop(flight_key, None)
      return
    if side == "Sell" and signal.sl_price <= entry_price:
      logger.warning(
        "SL (%.5f) <= entry (%.5f) for Sell — invalid SL direction, skipping entry: %s",
        signal.sl_price, entry_price, signal.symbol,
      )
      self._in_flight_entries.pop(flight_key, None)
      return

    # Safety monitor pre-trade checks (circuit breaker, daily loss, position limits)
    # NOTE: notional=0 here because the strategy qty is meaningless for live sizing.
    # The position sizer (calculate_position_size) determines the real notional.
    # Post-sizing notional check happens AFTER sizing below.
    if self._safety_monitor:
      db_check = self._db_factory()
      try:
        allowed, reason = self._safety_monitor.check_pre_trade(
          db_check, signal.symbol, side, 0, signal.portfolio_id,
        )
        if not allowed:
          logger.warning("Safety check blocked entry for %s: %s", signal.symbol, reason)
          self._log_event(signal.portfolio_id, "entry_blocked", signal.symbol, {
            "reason": reason,
          })
          self._in_flight_entries.pop(flight_key, None)
          return
      finally:
        db_check.close()

    try:
      # Count positions: hard cap uses ALL open, sizing uses risk-adjusted
      db = self._db_factory()
      try:
        total_open = db.query(Position).filter(
          Position.is_open == True,
        ).count()
        # Include in-flight entries (submitted but not yet filled/created in DB)
        in_flight_count = len(self._in_flight_entries)
        total_with_inflight = total_open + in_flight_count
        # Hard cap: total positions including risk-free AND in-flight
        max_pos = self._settings.max_positions_total
        if total_with_inflight >= max_pos:
          logger.warning(
            "Max positions reached (%d+%d in-flight = %d/%d) — skipping entry for %s",
            total_open, in_flight_count, total_with_inflight, max_pos, signal.symbol,
          )
          self._in_flight_entries.pop(flight_key, None)
          return
        # Risk-adjusted count for sizing (risk-free don't count against budget)
        open_count = db.query(Position).filter(
          Position.is_open == True,
          Position.risk_removed == False,
        ).count()
      finally:
        db.close()

      # Calculate position size (auto-leverage based on available balance)
      margin_cap = self._get_position_margin_cap(signal.portfolio_id, batch_id=signal.batch_id)
      max_pos_pct = self._get_portfolio_max_position_pct(signal.portfolio_id)
      sizing = self._position_manager.calculate_position_size(
        symbol=signal.symbol,
        side=side,
        entry_price=entry_price,
        sl_price=signal.sl_price,
        risk_amount=self._get_portfolio_risk(signal.portfolio_id, signal.symbol, batch_id=signal.batch_id),
        current_open_count=open_count,
        max_margin_override=margin_cap,
        max_position_pct_override=max_pos_pct,
      )

      # If position was capped below desired risk-based size, either skip
      # or accept reduced size depending on the skip_capped_entries setting.
      if sizing["capped"]:
        if self._settings.skip_capped_entries:
          logger.warning(
            "SKIP entry %s %s — position capped: desired_qty=%.6f → actual_qty=%.6f "
            "(margin_budget=$%.2f, lev=%dx). Entry requires more margin than available.",
            signal.symbol, side,
            sizing["desired_qty"], sizing["qty"],
            sizing["margin_budget"], sizing["leverage"],
          )
          self._log_event(signal.portfolio_id, "entry_skipped_capped", signal.symbol, {
            "desired_qty": sizing["desired_qty"],
            "actual_qty": sizing["qty"],
            "margin_budget": sizing["margin_budget"],
            "leverage": sizing["leverage"],
            "reason": "Position capped below desired risk-based size",
          })
          self._in_flight_entries.pop(flight_key, None)
          return
        else:
          logger.info(
            "Entry %s %s capped but accepted (skip_capped_entries=off): "
            "desired_qty=%.6f → actual_qty=%.6f",
            signal.symbol, side,
            sizing["desired_qty"], sizing["qty"],
          )

      # Post-sizing notional sanity check (uses REAL notional from position sizer)
      if self._safety_monitor and self._settings.max_position_notional > 0:
        real_notional = sizing["notional"]
        if real_notional > self._settings.max_position_notional * 1.05:
          # 5% tolerance for rounding — position sizer already caps, this is a guardrail
          logger.warning(
            "Post-sizing notional $%.2f exceeds max $%.2f for %s — blocking entry",
            real_notional, self._settings.max_position_notional, signal.symbol,
          )
          self._in_flight_entries.pop(flight_key, None)
          return

      # Prepare symbol (set isolated margin + leverage)
      self._position_manager.prepare_symbol(signal.symbol, sizing["leverage"])

      # Determine entry order type from settings (strategy limit_price overrides)
      if signal.limit_price > 0:
        order_type = "Limit"
        limit_price = signal.limit_price
      elif self._settings.entry_order_type == "Limit":
        order_type = "Limit"
        # Auto-calculate limit price with offset from last price
        offset = entry_price * (self._settings.entry_limit_offset_pct / 100)
        limit_price = entry_price + offset if side == "Buy" else entry_price - offset
      else:
        order_type = "Market"
        limit_price = None

      self._schedule(
        self._order_manager.submit_entry_order(
          portfolio_id=signal.portfolio_id,
          symbol=signal.symbol,
          side=side,
          qty=sizing["qty"],
          order_type=order_type,
          limit_price=limit_price,
          sl_price=signal.sl_price,
          tp_price=signal.tp_price if signal.tp_price > 0 else None,
          trail_distance=signal.trail_points if signal.trail_points > 0 else None,
          batch_id=signal.batch_id,
        )
      )

    except Exception as e:
      logger.error("Failed to process entry signal: %s", e)
      self._in_flight_entries.pop(flight_key, None)

  def _handle_exit_signal(self, signal: StrategySignal) -> None:
    """Process an exit signal — find position and close it."""
    db = self._db_factory()
    try:
      side = "Buy" if signal.direction == "long" else "Sell"
      pos = db.query(Position).filter(
        Position.portfolio_id == signal.portfolio_id,
        Position.symbol == signal.symbol,
        Position.side == side,
        Position.is_open == True,
      ).first()

      if pos and self._order_manager:
        self._order_manager.place_market_close(
          pos.id, pos.symbol, pos.side, pos.quantity, reason="signal",
        )
    finally:
      db.close()

  def _handle_register_exit(self, signal: StrategySignal) -> None:
    """Register SL/TP/TSL from strategy exit orders.

    Handles three cases:
    1. TSL registration (trail_points > 0)
    2. SL modification (sl_price changed — e.g., moved to breakeven after TP1)
    3. Risk removal detection (SL at or past entry = risk-free position)
    """
    # 1. TSL tracking via TrailingStopTracker
    if signal.trail_points > 0 and self._tsl_tracker:
      db = self._db_factory()
      try:
        pos = db.query(Position).filter(
          Position.portfolio_id == signal.portfolio_id,
          Position.symbol == signal.symbol,
          Position.is_open == True,
        ).first()
        if pos:
          self._tsl_tracker.register(
            position_db_id=pos.id,
            portfolio_id=signal.portfolio_id,
            symbol=signal.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            trail_points=signal.trail_points,
            trail_offset=signal.trail_offset,
          )
          # Persist trail distance on Position for exchange-side TSL (C2)
          pos.trailing_stop_distance = signal.trail_points
          if pos.trailing_extreme is None:
            pos.trailing_extreme = pos.entry_price
          db.commit()
      finally:
        db.close()

    # 2. SL modification — detect when strategy moves SL (e.g., TP1 → BE)
    if signal.sl_price > 0 and self._order_manager:
      db = self._db_factory()
      try:
        pos = db.query(Position).filter(
          Position.portfolio_id == signal.portfolio_id,
          Position.symbol == signal.symbol,
          Position.is_open == True,
        ).first()
        if pos and pos.sl_price:
          # Sync DB to exchange SL first (respect manual overrides)
          if pos.sl_order_link_id:
            exchange_sl_map = self._sync_exchange_sl_prices()
            if pos.sl_order_link_id in exchange_sl_map:
              exchange_sl = exchange_sl_map[pos.sl_order_link_id]
              if abs(exchange_sl - pos.sl_price) > 0.0001:
                logger.warning(
                  "SL synced from exchange before strategy amend: %s DB=%.4f -> exchange=%.4f",
                  pos.symbol, pos.sl_price, exchange_sl,
                )
                pos.sl_price = exchange_sl
                db.commit()

          # Only amend if strategy SL is tighter (better) than current
          # For longs: tighter = higher SL; for shorts: tighter = lower SL
          if pos.side == "Buy" and signal.sl_price <= pos.sl_price:
            pass  # Strategy SL is worse (lower) — skip
          elif pos.side == "Sell" and signal.sl_price >= pos.sl_price:
            pass  # Strategy SL is worse (higher) — skip
          elif abs(signal.sl_price - pos.sl_price) > 0.0001:
            logger.info(
              "SL change detected: %s %s %.4f -> %.4f (entry=%.4f)",
              pos.symbol, pos.side, pos.sl_price, signal.sl_price, pos.entry_price,
            )
            sl_ok = self._order_manager.modify_sl_order(pos.id, signal.sl_price)

            if not sl_ok:
              logger.error(
                "SL amendment FAILED for %s pos=%d — exchange SL may be stale (%.4f)",
                pos.symbol, pos.id, pos.sl_price,
              )
              # Don't update risk_removed — SL was NOT moved on exchange
            else:
              # 3. Auto-detect risk removal: SL at or past breakeven
              if pos.side == "Buy" and signal.sl_price >= pos.entry_price:
                if not getattr(pos, "risk_removed", False):
                  pos.risk_removed = True
                  logger.info("Risk removed: %s %s (SL >= entry)", pos.symbol, pos.side)
              elif pos.side == "Sell" and signal.sl_price <= pos.entry_price:
                if not getattr(pos, "risk_removed", False):
                  pos.risk_removed = True
                  logger.info("Risk removed: %s %s (SL <= entry)", pos.symbol, pos.side)
            db.commit()
      finally:
        db.close()

  def _on_tsl_trigger(
    self, position_db_id: int, symbol: str, side: str,
    qty: float, trigger_price: float,
  ) -> None:
    """Handle a trailing stop trigger — close the position."""
    if not self._order_manager:
      return

    db = self._db_factory()
    try:
      pos = db.get(Position, position_db_id)
      if pos and pos.is_open:
        close_qty = qty if qty > 0 else pos.quantity
        self._order_manager.place_market_close(
          position_db_id, symbol, side, close_qty, reason="tsl",
        )
        logger.info("TSL close: %s %s @ %.4f", symbol, side, trigger_price)
    finally:
      db.close()

  def _on_position_opened(self, position_id: int) -> None:
    """Callback when OrderManager creates a new position.

    Also registers TSL immediately if the position has trailing_stop_distance,
    so tick-by-tick tracking starts right away (no 1-bar delay).
    """
    # Clear in-flight guard so future signals can enter
    db = self._db_factory()
    try:
      pos = db.get(Position, position_id)
      if pos:
        flight_key = f"{pos.portfolio_id}_{pos.symbol}_{pos.side}"
        self._in_flight_entries.pop(flight_key, None)
        self._open_position_keys.add(flight_key)

        # Register TSL immediately if trail distance was set on the position
        if (pos.trailing_stop_distance and pos.trailing_stop_distance > 0
            and self._tsl_tracker):
          self._tsl_tracker.register(
            position_db_id=pos.id,
            portfolio_id=pos.portfolio_id,
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            trail_points=pos.trailing_stop_distance,
            trail_offset=0,
            initial_extreme=pos.trailing_extreme,
          )
          logger.info(
            "TSL registered on entry fill: pos=%d %s trail=%.4f",
            pos.id, pos.symbol, pos.trailing_stop_distance,
          )
    except Exception as e:
      logger.error("Error in _on_position_opened: %s", e)
    finally:
      db.close()

    if self._broadcast:
      self._schedule(self._broadcast({
        "type": "position_opened",
        "data": {"position_id": position_id},
      }))

  def _on_position_closed(self, position_id: int) -> None:
    """Callback when OrderManager closes a position."""
    # Clear in-flight guard and open position cache
    db_flight = self._db_factory()
    try:
      pos = db_flight.get(Position, position_id)
      if pos:
        flight_key = f"{pos.portfolio_id}_{pos.symbol}_{pos.side}"
        self._in_flight_entries.pop(flight_key, None)
        self._open_position_keys.discard(flight_key)
    except Exception as e:
      logger.warning("Error in _on_position_closed flight cleanup: %s", e)
    finally:
      db_flight.close()

    # Remove from TSL tracking
    if self._tsl_tracker:
      self._tsl_tracker.unregister(position_id)

    # Record trade result in safety monitor (circuit breaker, daily PnL)
    if self._safety_monitor:
      db = self._db_factory()
      try:
        pos = db.get(Position, position_id)
        if pos:
          # Find the Trade that was just created for this position
          trade = db.query(Trade).filter(
            Trade.portfolio_id == pos.portfolio_id,
            Trade.symbol == pos.symbol,
            Trade.opened_at == pos.opened_at,
          ).order_by(Trade.id.desc()).first()
          if trade:
            self._safety_monitor.record_trade_result(
              db, trade.realized_pnl, trade.commission, trade.portfolio_id,
            )
      except Exception as e:
        logger.error("Failed to record trade result in safety monitor: %s", e)
      finally:
        db.close()

    if self._broadcast:
      self._schedule(self._broadcast({
        "type": "position_closed",
        "data": {"position_id": position_id},
      }))

  # ── Reconciliation ────────────────────────────────────────────────

  async def _reconciliation_loop(self) -> None:
    """Periodically sync local state vs exchange."""
    interval = self._settings.reconciliation_interval_seconds
    while self._running:
      await asyncio.sleep(interval)
      if not self._running:
        break

      # Heartbeat — /health uses this to detect a stuck live engine.
      heartbeats.beat("engine_reconciliation")

      try:
        loop = asyncio.get_running_loop()
        # Reconcile orders (non-blocking — runs in thread pool, H1 audit fix)
        if self._order_manager:
          om = self._order_manager
          recon = await loop.run_in_executor(None, om.reconcile_orders)
          if recon.get("orphan_exchange") or recon.get("orphan_local"):
            logger.warning("Order discrepancies found — cleaning up")
            await loop.run_in_executor(None, om.cancel_orphan_orders)

        # Reconcile positions (non-blocking)
        if self._position_manager:
          db = self._db_factory()
          try:
            pm = self._position_manager
            result = await loop.run_in_executor(None, lambda: pm.reconcile_and_fix(db))
            await loop.run_in_executor(None, lambda: pm.update_funding_fees(db))
            # If reconciliation closed any DB-only positions, resync the
            # dedup cache so stale entries don't block future entries (Bug 42)
            if result.get("closed_db_only", 0) > 0:
              actual_open = db.query(Position).filter(
                Position.is_open == True,
              ).all()
              fresh_keys = {
                f"{p.portfolio_id}_{p.symbol}_{p.side}" for p in actual_open
              }
              stale = self._open_position_keys - fresh_keys
              if stale:
                logger.info("Reconciliation cache cleanup: removing stale keys %s", stale)
                self._open_position_keys -= stale
          finally:
            db.close()

      except Exception as e:
        logger.error("Reconciliation error: %s", e)

  # ── REST Fill Polling ───────────────────────────────────────────

  async def _fill_poll_loop(self) -> None:
    """Poll Bybit REST API for filled orders that the WS may have missed.

    Runs every 5 seconds. Checks all "open" exit orders in the DB against
    the exchange order history. If an order was filled on Bybit but our WS
    missed the notification, this triggers the fill handler.

    This is a safety net — in normal operation, the WS handles fills in
    real-time and this loop finds nothing to do.
    """
    POLL_INTERVAL = 5  # seconds
    await asyncio.sleep(5)  # Initial delay to let things settle

    while self._running:
      await asyncio.sleep(POLL_INTERVAL)
      if not self._running:
        break

      try:
        if not self._order_manager or not self._trading_client:
          continue

        # Get open exit orders from DB (SL, TP, close)
        db = self._db_factory()
        try:
          open_exits = db.query(Order).filter(
            Order.role.in_(["sl", "tp", "tp1", "tsl", "close"]),
            Order.status.in_(["open", "submitting"]),
          ).all()

          if not open_exits:
            continue

          # Batch by symbol to minimize API calls
          symbols = {o.symbol for o in open_exits}
          link_to_order = {o.order_link_id: o for o in open_exits}

          loop = asyncio.get_running_loop()
          for symbol in symbols:
            try:
              tc = self._trading_client
              history = await loop.run_in_executor(None, lambda s=symbol: tc.get_order_history(s, limit=20))
              for h in history:
                link_id = h.get("orderLinkId", "")
                status = h.get("orderStatus", "")
                if link_id in link_to_order and status == "Filled":
                  logger.info(
                    "REST poll detected missed fill: %s %s linkId=%s",
                    symbol, link_to_order[link_id].role, link_id,
                  )
                  self._order_manager.on_order_update(h)
                  self._order_manager.on_execution(h)
            except Exception as sym_err:
              logger.warning("Fill poll failed for %s: %s", symbol, sym_err)

        finally:
          db.close()

      except Exception as e:
        logger.error("Fill poll loop error: %s", e)

  # ── Wallet Cache Refresh ──────────────────────────────────────────

  async def _wallet_refresh_loop(self) -> None:
    """Refresh cached wallet data every 5 seconds.

    The frontend polls /api/live/status and /api/live/account-summary
    frequently. Without caching, each poll triggers 2+ Bybit REST calls.
    This loop refreshes the cache in the background so frontend reads
    are instant (no blocking REST calls).
    """
    REFRESH_INTERVAL = 5  # seconds
    await asyncio.sleep(2)  # Initial delay

    loop = asyncio.get_running_loop()
    while self._running:
      try:
        if self._position_manager:
          # Non-blocking wallet refresh (H1 audit fix)
          await loop.run_in_executor(None, self._position_manager.refresh_wallet_cache)
      except Exception as e:
        logger.error("Wallet cache refresh error: %s", e)
      await asyncio.sleep(REFRESH_INTERVAL)

  # ── Helpers ───────────────────────────────────────────────────────

  async def _close_all_portfolio_positions(self, portfolio_id: int) -> None:
    """Close all open positions for a specific portfolio."""
    db = self._db_factory()
    try:
      positions = db.query(Position).filter(
        Position.portfolio_id == portfolio_id,
        Position.is_open == True,
      ).all()

      loop = asyncio.get_running_loop()
      for pos in positions:
        if self._order_manager:
          om = self._order_manager
          # Run blocking REST call in thread pool (H1 audit fix)
          await loop.run_in_executor(
            None,
            lambda p=pos: om.place_market_close(
              p.id, p.symbol, p.side, p.quantity, reason="deactivation",
            ),
          )
        if self._tsl_tracker:
          self._tsl_tracker.unregister(pos.id)

    finally:
      db.close()

  def _get_total_equity(self) -> float:
    """Get total equity (reserve + wallet balance) for circuit breaker calculations.

    Uses wallet_balance (realized only) instead of total_equity so that
    unrealized PnL from open positions doesn't affect the calculation.
    """
    wallet_balance = 0.0
    if self._position_manager:
      wallet_balance = self._position_manager.get_wallet_balance()
    return self._settings.reserve_account_usd + wallet_balance

  async def _kelly_recompute_loop(self) -> None:
    """Check every hour if any active portfolio needs Kelly allocation recompute."""
    INTERVAL = 3600  # 1 hour
    await asyncio.sleep(60)  # Initial delay — let everything else start first
    while self._running:
      try:
        await asyncio.to_thread(self._check_kelly_recomputes)
      except Exception as e:
        logger.warning("Kelly recompute check failed (non-fatal): %s", e)
      await asyncio.sleep(INTERVAL)

  def _check_kelly_recomputes(self) -> None:
    """For each active portfolio with kelly_allocation config, check if recompute is due."""
    from datetime import timedelta
    db = self._db_factory()
    try:
      active = db.query(Portfolio).filter(Portfolio.is_active == True).all()
      for p in active:
        settings = json.loads(p.source_strategies or "{}")
        ka_config = settings.get("kelly_allocation_config")
        if not ka_config:
          continue

        schedule_type = ka_config.get("schedule_type", "monthly")
        schedule_trades = ka_config.get("schedule_trade_count", 100)

        # Find current allocation
        from backend.db.models import KellyAllocation
        current = (
          db.query(KellyAllocation)
          .filter(
            KellyAllocation.portfolio_id == p.id,
            KellyAllocation.is_current == True,
          )
          .first()
        )
        if not current:
          continue  # No initial allocation — user must trigger manually first

        # CS-044: SQLite stores DateTime columns as naive (no timezone=True on
        # the column), but default=lambda: datetime.now(timezone.utc) writes an
        # aware value at insert time. After a round-trip through SQLite the
        # tz is lost and we read a naive datetime. Coerce to UTC-aware so the
        # `now - computed_at` arithmetic below does not raise TypeError.
        computed_at = current.computed_at
        if computed_at is not None and computed_at.tzinfo is None:
          computed_at = computed_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        # Check if recompute is due based on schedule
        should_recompute = False
        if schedule_type == "by_trades":
          # Count trades since last recompute
          trade_count = (
            db.query(Trade)
            .filter(
              Trade.portfolio_id == p.id,
              Trade.closed_at >= computed_at,
            )
            .count()
          )
          if trade_count >= schedule_trades:
            should_recompute = True
        else:
          # Time-based schedules
          intervals = {
            "weekly": timedelta(weeks=1),
            "biweekly": timedelta(weeks=2),
            "monthly": timedelta(days=30),
            "2month": timedelta(days=60),
            "6month": timedelta(days=180),
          }
          interval = intervals.get(schedule_type, timedelta(days=30))
          if now - computed_at >= interval:
            should_recompute = True

        if should_recompute:
          self._run_kelly_recompute(db, p, ka_config)

    finally:
      db.close()

  def _run_kelly_recompute(self, db: Session, portfolio: Portfolio, ka_config: dict) -> None:
    """Recompute Kelly allocation for a portfolio and store result."""
    from backend.analysis.kelly_allocator import KellyAllocConfig, compute_kelly_allocation
    from backend.analysis.daily_pnl import build_daily_pnl_matrix
    from backend.api.routes.kelly_allocation import _load_portfolio_is_data
    from backend.db.models import KellyAllocation

    config = KellyAllocConfig(
      mode=ka_config.get("mode", "per_strategy"),
      half_kelly_multiplier=ka_config.get("half_kelly_multiplier", 0.5),
      sr_threshold=ka_config.get("sr_threshold", 0.5),
      max_cap_pct=ka_config.get("max_cap_pct", 0.40),
      corr_warning_threshold=ka_config.get("corr_warning_threshold", 0.7),
      regime_dampening_factor=ka_config.get("regime_dampening_factor", 0.5),
      min_trades_per_strategy=ka_config.get("min_trades_per_strategy", 100),
      is_lookback_days=ka_config.get("is_lookback_days", 0),
      schedule_type=ka_config.get("schedule_type", "monthly"),
      schedule_trade_count=ka_config.get("schedule_trade_count", 100),
    )

    settings = json.loads(portfolio.source_strategies or "{}")
    daily_pnl, oos_sharpe = _load_portfolio_is_data(db, portfolio, settings, config)

    if daily_pnl.empty:
      logger.warning("Kelly recompute for portfolio %d: no IS data — skipping", portfolio.id)
      return

    result = compute_kelly_allocation(daily_pnl, oos_sharpe, config)

    # Store (mark old as not current)
    db.query(KellyAllocation).filter(
      KellyAllocation.portfolio_id == portfolio.id,
      KellyAllocation.is_current == True,
    ).update({"is_current": False})

    from dataclasses import asdict
    alloc = KellyAllocation(
      portfolio_id=portfolio.id,
      weights_json=json.dumps(result.weights),
      result_json=json.dumps(result.to_dict()),
      config_json=json.dumps(asdict(config)),
      computed_at=datetime.now(timezone.utc),
      trigger="scheduled",
      is_current=True,
    )
    db.add(alloc)
    db.commit()

    # Refresh in-memory cache
    self._kelly_alloc_cache[portfolio.id] = {
      "weights": result.weights,
      "n_assets": len(result.weights),
      "computed_at": alloc.computed_at.isoformat(),
    }

    logger.info(
      "Kelly allocation recomputed (scheduled) for portfolio %d: %d strategies, HHI=%.3f",
      portfolio.id, len(result.weights), result.herfindahl_index,
    )

  def _get_portfolio_risk(self, portfolio_id: int, symbol: str = "", batch_id: int | None = None) -> float:
    """Get the risk amount in USD for a portfolio.

    Checks for a portfolio-level risk_profile override first.
    Falls back to global settings (risk_mode: pct or fixed).

    Supported risk_profile modes:
    - "fixed": flat dollar amount from initial_risk_usd
    - "pct": equity * risk_pct / 100
    - "martingale": base * multiplier^min(loss_streak, max_streak)
    - "anti_martingale": base * multiplier^min(win_streak, max_streak)
    - "kelly": falls back to fixed (not yet implemented live)

    Martingale streaks are grouped by martingale_scope:
    - "asset": per symbol across all batches (default)
    - "portfolio": all assets share one streak
    - "asset_batch": per symbol within a specific batch
    - "batch": all assets in a batch share one streak
    """
    db = self._db_factory()
    try:
      portfolio = db.get(Portfolio, portfolio_id)
      source = json.loads(portfolio.source_strategies or "{}") if portfolio else {}
      profile = source.get("risk_profile", {})

      # Check if portfolio override is enabled
      if profile and profile.get("override_enabled", True):
        # ── Group multiplier: auto-scale risk by batch group's pnl_multiplier ──
        # When enabled, base risk is multiplied by the batch's group multiplier
        # (e.g. Group A = 2x, Group B/C = 1x). User sets one base risk value
        # and each batch automatically gets the correct sizing.
        apply_group_mult = profile.get("apply_group_multiplier", False)

        # ── Per-batch sizing master: resolve batch-specific config ──
        if profile.get("sizing_master") == "per_batch" and profile.get("batch_configs"):
          # Use signal's batch_id directly; fall back to DB lookup only if not provided
          resolved_bid = batch_id if batch_id is not None else self._resolve_batch_id(db, portfolio_id, symbol)
          bcfg = None
          if resolved_bid is not None:
            bcfg = profile["batch_configs"].get(str(resolved_bid))
          if bcfg:
            # Recursively apply this batch's config as if it were the portfolio profile
            batch_profile = {
              "mode": bcfg.get("mode", "fixed"),
              "initial_risk_usd": float(bcfg.get("value", 0)),
              "initial_capital": float(bcfg.get("initialCapital", 10000)),
              "multiplier": float(bcfg.get("multiplier", 2.0)),
              "max_streak": int(bcfg.get("maxStreak", 3)),
              "martingale_compound": bcfg.get("martingaleCompound", "off"),
              "martingale_scope": "asset_batch",  # forced in per-batch mode
              "kelly_n": float(bcfg.get("kellyN", 2)),
              "kelly_warmup": int(bcfg.get("kellyWarmup", 20)),
              "kelly_scope": bcfg.get("kellyScope", "portfolio"),
              "kelly_lookback": int(bcfg.get("kellyLookback", 0)),
              "kelly_cap": float(bcfg.get("kellyCap", 0.0)),
              "override_enabled": True,
              "apply_group_multiplier": apply_group_mult,  # inherit flag
              "sizing_master": "portfolio",  # prevent recursion
            }
            # Swap profile and fall through to normal logic
            profile = batch_profile

        mode = profile.get("mode", "fixed")
        base_risk = profile.get("initial_risk_usd", self._settings.risk_per_trade_usd)

        # Compute risk based on mode
        risk = None

        if mode == "fixed" or mode == "kelly":
          risk = base_risk if base_risk > 0 else self._settings.risk_per_trade_usd
          # Apply Kelly allocation weight if available (portfolio-level capital distribution)
          kelly_weight = self._get_kelly_weight(db, portfolio_id, symbol)
          if kelly_weight is not None:
            cached = self._kelly_alloc_cache.get(portfolio_id, {})
            n_assets = cached.get("n_assets", 1)
            risk = risk * kelly_weight * n_assets

        elif mode == "pct":
          equity = self._get_total_equity_for_risk()
          if equity <= 0:
            risk = base_risk if base_risk > 0 else 100.0
          else:
            # Portfolio save stores the % as initial_risk_usd — check both field names
            risk_pct = profile.get("risk_pct", profile.get("initial_risk_usd", self._settings.risk_per_trade_pct))
            risk = equity * (risk_pct / 100)

        elif mode in ("martingale", "anti_martingale", "inv_martingale", "inv_anti_martingale"):
          multiplier = profile.get("multiplier", 2.0)
          max_streak = profile.get("max_streak", 3)
          compound = profile.get("martingale_compound", "off")
          scope = profile.get("martingale_scope", "asset")
          # CS-031: validate against whitelist. An unknown scope means the
          # persisted JSON is malformed (typo, stale migration, hand-edit).
          # Falling through silently to "asset" lets the wrong streak grouping
          # run in production — warn loudly and normalize to the safe default.
          if scope not in _VALID_MARTINGALE_SCOPES:
            logger.warning(
              "Portfolio %d: unrecognized martingale_scope=%r — falling back to 'asset'. "
              "Valid scopes: %s",
              portfolio_id, scope, sorted(_VALID_MARTINGALE_SCOPES),
            )
            scope = "asset"
          # Backwards compat: old portfolios may still have portfolio_wide=True
          if profile.get("portfolio_wide", False) and scope == "asset":
            scope = "portfolio"

          # Determine streak filter params based on scope
          # Use signal's batch_id directly when available; fall back to DB lookup
          resolved_bid = batch_id if batch_id is not None else self._resolve_batch_id(db, portfolio_id, symbol)
          if scope == "portfolio":
            streak_symbol = ""
            streak_batch_id = None
          elif scope == "batch":
            streak_symbol = ""
            streak_batch_id = resolved_bid
          elif scope == "asset_batch":
            streak_symbol = symbol
            streak_batch_id = resolved_bid
          else:  # "asset" (default)
            streak_symbol = symbol
            streak_batch_id = None

          streak = self._get_martingale_streak(
            db, portfolio_id, mode, streak_symbol, batch_id=streak_batch_id,
          )
          capped = min(streak, max_streak)
          if capped > 0:
            if mode in ("inv_martingale", "inv_anti_martingale"):
              factor = (1.0 / multiplier) ** capped
            else:
              factor = multiplier ** capped
          else:
            factor = 1.0

          if compound == "pct":
            equity = self._get_total_equity_for_risk()
            risk_pct = profile.get("risk_pct", profile.get("initial_risk_usd", self._settings.risk_per_trade_pct))
            if equity > 0:
              risk = max(equity * (risk_pct / 100) * factor, 0.0)
          elif compound == "dollar":
            equity = self._get_total_equity_for_risk()
            initial_cap = profile.get("initial_capital", 10000)
            if equity > 0 and initial_cap > 0:
              equity_ratio = equity / initial_cap
              risk = max(base_risk * factor * equity_ratio, 0.0)

          if risk is None:
            # "off" or fallback: flat base with streak multiplier only
            risk = base_risk * factor

        if risk is not None:
          # Apply group multiplier if enabled (e.g. Group A = 2x base risk)
          if apply_group_mult:
            resolved_bid = batch_id if batch_id is not None else self._resolve_batch_id(db, portfolio_id, symbol)
            gm = self._get_group_multiplier(db, resolved_bid)
            if gm != 1.0:
              logger.info(
                "Group multiplier %.1fx applied: %s batch=%s risk $%.2f -> $%.2f",
                gm, symbol, resolved_bid, risk, risk * gm,
              )
            risk *= gm
          return risk

      # Fallback: global settings (legacy path)
      risk_mode = source.get("risk_mode", self._settings.risk_mode)

      if risk_mode == "fixed":
        return source.get("risk_per_trade_usd", self._settings.risk_per_trade_usd)

      # Percentage mode: calculate from total equity
      risk_pct = source.get("risk_per_trade_pct", self._settings.risk_per_trade_pct)
      total_equity = self._get_total_equity_for_risk()

      if total_equity <= 0:
        logger.warning("Cannot determine equity — using fallback risk $100")
        return 100.0

      return total_equity * (risk_pct / 100)
    finally:
      db.close()

  def _get_position_margin_cap(self, portfolio_id: int, batch_id: int | None = None) -> float | None:
    """Get per-group max margin cap from portfolio's per-batch config.

    Returns the maxMargin value in USD if configured for this batch's group,
    or None to use the global default (max_position_pct).
    """
    db = self._db_factory()
    try:
      portfolio = db.get(Portfolio, portfolio_id)
      source = json.loads(portfolio.source_strategies or "{}") if portfolio else {}
      profile = source.get("risk_profile", {})
      if not profile or not profile.get("override_enabled", True):
        return None
      if profile.get("sizing_master") != "per_batch" or not profile.get("batch_configs"):
        return None
      if batch_id is None:
        return None
      bcfg = profile["batch_configs"].get(str(batch_id))
      if not bcfg:
        return None
      mm = bcfg.get("maxMargin")
      return float(mm) if mm and float(mm) > 0 else None
    finally:
      db.close()

  def _get_portfolio_max_position_pct(self, portfolio_id: int) -> float | None:
    """Get portfolio-level max_position_pct override (% of equity per single position).

    Returns the override value if set and override_enabled is True,
    or None to use the global Settings default.
    """
    db = self._db_factory()
    try:
      portfolio = db.get(Portfolio, portfolio_id)
      source = json.loads(portfolio.source_strategies or "{}") if portfolio else {}
      profile = source.get("risk_profile", {})
      if not profile or not profile.get("override_enabled", True):
        return None
      mpp = profile.get("max_position_pct")
      if mpp is None:
        return None
      # 0 is a legitimate "disable cap" value — pass it through
      return float(mpp) if float(mpp) >= 0 else None
    finally:
      db.close()

  def _get_total_equity_for_risk(self) -> float:
    """Get total equity for risk calculation (wallet + reserve)."""
    wallet_balance = 0.0
    if self._position_manager:
      wallet_balance = self._position_manager.get_wallet_balance()
    reserve = self._settings.reserve_account_usd
    total_equity = reserve + wallet_balance
    # CS-024: total_equity_override is deprecated — log when non-zero so the
    # user sees it's still being consulted and can migrate away before the
    # setting is removed entirely.
    if total_equity <= 0 and self._settings.total_equity_override > 0:
      logger.warning(
        "CS-024 DEPRECATED: total_equity_override=$%.2f used as risk-equity "
        "fallback (wallet+reserve=0). Migrate to reserve_account_usd.",
        self._settings.total_equity_override,
      )
      total_equity = self._settings.total_equity_override
    return total_equity

  def _resolve_batch_id(
    self, db: Session, portfolio_id: int, symbol: str,
  ) -> int | None:
    """Look up the batch_id for a symbol from the most recent open position or trade.

    Returns None if no batch_id can be determined.
    """
    # First try open positions
    pos = (
      db.query(Position)
      .filter(
        Position.portfolio_id == portfolio_id,
        Position.symbol == symbol,
        Position.is_open == True,
      )
      .first()
    )
    if pos and pos.batch_id is not None:
      return pos.batch_id

    # Fall back to most recent closed trade
    trade = (
      db.query(Trade)
      .filter(
        Trade.portfolio_id == portfolio_id,
        Trade.symbol == symbol,
        Trade.batch_id.isnot(None),
      )
      .order_by(Trade.closed_at.desc())
      .first()
    )
    if trade and trade.batch_id is not None:
      return trade.batch_id

    return None

  def _get_kelly_weight(
    self, db: Session, portfolio_id: int, symbol: str,
  ) -> float | None:
    """Look up the Kelly allocation weight for a symbol in a portfolio.

    Checks the in-memory cache first (refreshed from DB on miss).
    Returns weight as a float, or None if Kelly allocation is not configured.
    """
    cached = self._kelly_alloc_cache.get(portfolio_id)
    if cached is None:
      # Load from DB
      from backend.db.models import KellyAllocation
      row = (
        db.query(KellyAllocation)
        .filter(
          KellyAllocation.portfolio_id == portfolio_id,
          KellyAllocation.is_current == True,
        )
        .first()
      )
      if not row:
        # No allocation configured — cache the miss to avoid repeated DB hits
        self._kelly_alloc_cache[portfolio_id] = {"weights": {}, "n_assets": 0}
        return None
      weights = json.loads(row.weights_json or "{}")
      self._kelly_alloc_cache[portfolio_id] = {
        "weights": weights,
        "n_assets": len(weights),
        "computed_at": row.computed_at.isoformat() if row.computed_at else "",
      }
      cached = self._kelly_alloc_cache[portfolio_id]

    weights = cached.get("weights", {})
    if not weights:
      return None

    # Try multiple key formats: "SYMBOL_Long", "SYMBOL_Short", just "SYMBOL"
    # The asset_key in kelly_allocator uses include_side=True format
    for side_suffix in ["_Long", "_Short", ""]:
      key = f"{symbol}{side_suffix}"
      if key in weights:
        return weights[key]

    return None

  def refresh_kelly_cache(self, portfolio_id: int) -> bool:
    """Repopulate the in-memory KellyAllocation cache for a portfolio.

    Called by the nightly hybrid refresh service after writing a new
    KellyAllocation row (trigger='step2_nightly'). Does NOT change the
    lookup path used by _get_portfolio_risk — only overwrites the cache
    so the executor picks up the new weights on the next signal.

    Returns True if a current allocation row was found and loaded.
    """
    db = self._db_factory()
    try:
      from backend.db.models import KellyAllocation
      row = (
        db.query(KellyAllocation)
        .filter(
          KellyAllocation.portfolio_id == portfolio_id,
          KellyAllocation.is_current == True,
        )
        .first()
      )
      if not row:
        self._kelly_alloc_cache[portfolio_id] = {"weights": {}, "n_assets": 0}
        logger.info("refresh_kelly_cache: no current allocation for portfolio %d", portfolio_id)
        return False

      weights = json.loads(row.weights_json or "{}")
      self._kelly_alloc_cache[portfolio_id] = {
        "weights": weights,
        "n_assets": len(weights),
        "computed_at": row.computed_at.isoformat() if row.computed_at else "",
      }
      logger.info(
        "refresh_kelly_cache: portfolio %d -> %d weights reloaded (trigger=%s)",
        portfolio_id, len(weights), row.trigger,
      )
      return True
    finally:
      db.close()

  def _get_martingale_streak(
    self, db: Session, portfolio_id: int, mode: str, symbol: str = "",
    batch_id: int | None = None,
  ) -> int:
    """Count consecutive win/loss streak from recent closed trades.

    Streaks are tracked per-symbol by default (matching backtest behaviour).
    batch_id filtering is used for "batch" and "asset_batch" scopes.
    Mode-scoped: paper trades never feed into live streak and vice versa —
    the query filters by is_paper matching the engine's current mode.
    CS-048: breakeven tolerance is at float-noise level (1e-9) so truly
    tiny-notional wins/losses still break martingale streaks — kept in
    lock-step with ``backtester/engine.py:_fill_exit``.
    """
    is_paper = bool(self._settings.paper_trading)
    query = (
      db.query(Trade)
      .filter(Trade.portfolio_id == portfolio_id)
      .filter(Trade.is_paper == is_paper)
    )
    if symbol:
      query = query.filter(Trade.symbol == symbol)
    if batch_id is not None:
      query = query.filter(Trade.batch_id == batch_id)
    recent_trades = (
      query.order_by(Trade.closed_at.desc())
      .limit(200)
      .all()
    )
    if not recent_trades:
      return 0

    # anti_martingale / inv_anti_martingale: streak counts consecutive wins
    # martingale / inv_martingale: streak counts consecutive losses
    target_is_win = mode in ("anti_martingale", "inv_anti_martingale")
    streak = 0
    _BE_EPS = 1e-9
    for t in recent_trades:
      pnl = t.realized_pnl or 0.0
      # Breakeven trades don't affect streaks — match engine.py (CS-048).
      if -_BE_EPS <= pnl <= _BE_EPS:
        continue
      is_win = pnl > _BE_EPS
      if is_win == target_is_win:
        streak += 1
      else:
        break
    return streak

  def _get_group_multiplier(
    self, db: Session, batch_id: int | None,
  ) -> float:
    """Look up the pnl_multiplier for a batch's group.

    Returns the group's pnl_multiplier (e.g. 2.0 for Group A).
    Returns 1.0 if batch_id is None or group has no multiplier.
    """
    if batch_id is None:
      return 1.0
    from backend.db.models import BatchBacktestRun, BatchGroup
    batch = db.get(BatchBacktestRun, batch_id)
    if not batch or not batch.group_id:
      return 1.0
    group = db.get(BatchGroup, batch.group_id)
    if not group:
      return 1.0
    return group.pnl_multiplier or 1.0

  async def _broadcast_status(self) -> None:
    """Broadcast engine status to frontend."""
    if self._broadcast:
      await self._broadcast({
        "type": "engine_status",
        "data": self.get_status(),
      })

  def _log_event(
    self, portfolio_id: int | None, event_type: str,
    symbol: str, details: dict,
  ) -> None:
    """Write an audit event."""
    db = self._db_factory()
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
    finally:
      db.close()

  def set_broadcast(self, broadcast_fn: Callable) -> None:
    """Set the broadcast function for pushing updates to frontend WS."""
    self._broadcast = broadcast_fn


# Singleton instance — created once, shared across the app
_engine: EngineCoordinator | None = None


def get_engine() -> EngineCoordinator:
  """Get the singleton engine coordinator."""
  global _engine
  if _engine is None:
    from backend.db.database import SessionLocal
    _engine = EngineCoordinator(db_factory=SessionLocal)
  return _engine
