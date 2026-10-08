"""Strategy Executor — runs portfolio strategies on bar close, emits signals.

Loads transpiled Python strategies, warms them up with historical data,
then runs on each new bar close to generate entry/exit signals. Bridges
the backtester's BaseStrategy interface with the live OrderManager.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from backend.config.settings import BASE_DIR
from backend.db.models import Portfolio, PortfolioAsset, Position, Strategy, StrategyConfig
from backend.exchange.candle_aggregator import CandleAggregator
from backend.utils.candle_loader import to_nyt
from backend.utils.logger import get_logger
from backend.utils.tf_resolver import resolve_extra_tfs, resolve_timeframe

logger = get_logger(__name__)


# ── Engine state cache (incremental warmup) ──────────────────────────
#
# The strategy executor used to do a full historical replay (30k–70k
# bars per symbol × 30 portfolios × 30 symbols ≈ 30 minutes) on every
# `activate_portfolio` call, blocking the async event loop the entire
# time. The same `_capture_strategy_state_static` mechanism that the
# scanner ([scanner_executor.py]) and L1 batch refresh
# ([batch_store_refresh.py]) already use lets us persist the warmed-up
# state and resume on next start with only the new bars to replay.
#
# Storage: per-(portfolio_id, symbol) JSON file under
# `data/live_engine_state/{portfolio_id}/{symbol}.json`. Disk-based to
# avoid colliding with the scanner's `LiveStrategyState` table — the two
# subsystems must NOT share state because their .next() semantics differ.
#
# Behavior:
#   • _load_strategy_state returns None if the file is missing OR older
#     than ENGINE_STATE_MAX_AGE_DAYS — caller falls back to full replay.
#   • _save_strategy_state writes atomically (tmp + rename) so a crash
#     mid-write can't leave a half-written file.
#   • Save happens (a) at the end of warmup and (b) after each bar close
#     in on_bar_close — so steady-state restarts pick up at most 1 bar
#     of replay work.
#
# Migration / first-run impact: when this code first ships, no state
# files exist yet. The first start after deploy still does a full warmup
# (one last time), saves state at the end, and from there every restart
# is fast.

ENGINE_STATE_DIR_NAME = "live_engine_state"
ENGINE_STATE_MAX_AGE_DAYS = 7  # stale state → fall back to full warmup
ENGINE_STATE_VERSION = 1


def _engine_state_root() -> Path:
  """`data/live_engine_state/` under the project root."""
  # BASE_DIR is `.../The_War_Room/backend`, project root is its parent
  return BASE_DIR.parent / "data" / ENGINE_STATE_DIR_NAME


def _engine_state_file(portfolio_id: int, symbol: str) -> Path:
  return _engine_state_root() / str(portfolio_id) / f"{symbol}.json"


@dataclass
class StrategySignal:
  """A trading signal emitted by a strategy."""
  portfolio_id: int
  symbol: str
  action: str         # "entry" or "exit"
  direction: str      # "long" or "short"
  entry_id: str       # Strategy position ID (e.g. "LONG1")
  qty: float          # Strategy-level qty (PositionManager overrides for live)
  sl_price: float     # 0 = no SL
  tp_price: float     # 0 = no TP
  trail_points: float # 0 = no TSL
  trail_offset: float
  limit_price: float  # 0 = market entry
  comment: str
  qty_percent: float = 100.0  # % of position to close (default 100 = full)
  batch_id: int | None = None  # source batch for per-batch martingale


class ActivePortfolio:
  """Runtime state for an activated portfolio."""

  def __init__(
    self,
    portfolio_id: int,
    strategy_source: str,
    parameters: dict,
    base_tf: str,
    extra_tfs: list[str],
    assets: list[dict],
    warmup_bars: int = 200,
    exec_exclude_hours: set[int] | None = None,
    exec_exclude_days: set[int] | None = None,
  ):
    self.portfolio_id = portfolio_id
    self.strategy_source = strategy_source
    self.parameters = parameters
    self.base_tf = base_tf
    self.extra_tfs = extra_tfs
    self.assets = assets  # [{symbol, side}, ...]
    self.warmup_bars = warmup_bars

    # Execution-time filter (NYT). Mirrors optimization's _filter_trades_exec
    # exactly: entries whose bar timestamp hour falls in exec_exclude_hours
    # or whose weekday (Mon=0..Sun=6) falls in exec_exclude_days are blocked.
    # Empty sets ⇒ no filter applied.
    self.exec_exclude_hours: set[int] = exec_exclude_hours or set()
    self.exec_exclude_days: set[int] = exec_exclude_days or set()

    # Per-symbol strategy instances: symbol → strategy_instance
    self.strategies: dict = {}
    # Per-symbol bar counters
    self.bar_counts: dict[str, int] = {}
    # Whether warmup is complete per symbol
    self.warmed_up: dict[str, bool] = {}


class StrategyExecutor:
  """Runs strategies on bar close and emits trading signals.

  Lifecycle:
    1. activate_portfolio() — load strategy, create per-symbol instances, warmup
    2. on_bar_close() — called by CandleAggregator when a TF bar completes
    3. deactivate_portfolio() — stop monitoring

  Signals are emitted via the on_signal callback.
  """

  def __init__(
    self,
    candle_aggregator: CandleAggregator,
    db_factory: Callable[[], Session],
    on_signal: Callable[[StrategySignal], None] | None = None,
    position_key_check: Callable[[int, str, str], bool] | None = None,
  ):
    self._aggregator = candle_aggregator
    self._db_factory = db_factory
    self._on_signal = on_signal
    # CS-042: O(1) short-circuit. Coordinator owns the in-memory
    # _open_position_keys cache and is notified on open/close; wiring
    # that cache here avoids a DB session per entry signal during the
    # ~40-symbol M30 burst. Fall back to the DB query if not provided
    # (keeps executor usable standalone in tests).
    self._position_key_check = position_key_check
    # portfolio_id → ActivePortfolio
    self._active: dict[int, ActivePortfolio] = {}

  # ── Engine state persistence (incremental warmup) ────────────────
  #
  # Saves and restores per-symbol strategy state to disk so that
  # restarting the engine doesn't need to replay every historical bar
  # from scratch. See module-level docstring above for the design.

  def _save_strategy_state(
    self, portfolio_id: int, symbol: str, instance, last_bar_ts: datetime | None,
  ) -> None:
    """Capture the strategy's resumable state and write it atomically.

    Uses BacktestEngine._capture_strategy_state_static — the same helper
    the scanner and L1 batch refresh use — which intentionally skips
    indicator buffers (re-derivable from candles via init()) and saves
    only the market-structure / counter / trade-array state that's
    expensive to reconstruct.

    Errors are logged and swallowed: a state-save failure must NOT break
    live trading. The next save attempt will succeed or the next start
    falls back to full warmup.
    """
    try:
      from backend.backtester.engine import BacktestEngine

      state = BacktestEngine._capture_strategy_state_static(instance)
      payload = {
        "version": ENGINE_STATE_VERSION,
        "portfolio_id": portfolio_id,
        "symbol": symbol,
        "saved_at_iso": datetime.now(timezone.utc).isoformat(),
        "last_bar_timestamp_iso": last_bar_ts.isoformat() if last_bar_ts else None,
        "last_bar_index": int(getattr(instance, "_bar", 0) or 0),
        "state": state,
      }

      path = _engine_state_file(portfolio_id, symbol)
      path.parent.mkdir(parents=True, exist_ok=True)
      tmp = path.with_suffix(".json.tmp")
      tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
      tmp.replace(path)  # atomic on POSIX + Windows (Python 3.3+)
    except Exception as e:
      logger.warning(
        "Failed to save engine state for portfolio=%d symbol=%s: %s",
        portfolio_id, symbol, e,
      )

  def _load_strategy_state(self, portfolio_id: int, symbol: str) -> dict | None:
    """Read a previously-saved state file. Returns None if missing OR stale.

    Stale = older than ENGINE_STATE_MAX_AGE_DAYS. Caller treats None as
    "do a full warmup" — same code path as the pre-feature behavior.
    """
    try:
      path = _engine_state_file(portfolio_id, symbol)
      if not path.exists():
        return None
      payload = json.loads(path.read_text(encoding="utf-8"))
      if payload.get("version") != ENGINE_STATE_VERSION:
        logger.info(
          "Engine state for portfolio=%d symbol=%s has wrong version "
          "(%r vs %d); falling back to full warmup",
          portfolio_id, symbol, payload.get("version"), ENGINE_STATE_VERSION,
        )
        return None
      ts_iso = payload.get("last_bar_timestamp_iso")
      if not ts_iso:
        return None
      ts = datetime.fromisoformat(str(ts_iso))
      if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
      age = datetime.now(timezone.utc) - ts
      if age > timedelta(days=ENGINE_STATE_MAX_AGE_DAYS):
        logger.info(
          "Engine state for portfolio=%d symbol=%s is %d days old "
          "(threshold %d); falling back to full warmup",
          portfolio_id, symbol, age.days, ENGINE_STATE_MAX_AGE_DAYS,
        )
        return None
      return payload
    except Exception as e:
      logger.warning(
        "Failed to load engine state for portfolio=%d symbol=%s "
        "(falling back to full warmup): %s",
        portfolio_id, symbol, e,
      )
      return None

  # ── Activation ────────────────────────────────────────────────────

  def activate_portfolio(self, portfolio_id: int) -> bool:
    """Load a portfolio's strategy and prepare for live execution.

    1. Load portfolio + strategy from DB
    2. Compile strategy class
    3. Create per-symbol instances
    4. Warm up with historical data from DB
    5. Register symbols with CandleAggregator
    """
    if portfolio_id in self._active:
      logger.warning("Portfolio %d already active", portfolio_id)
      return True

    db = self._db_factory()
    try:
      portfolio = db.get(Portfolio, portfolio_id)
      if not portfolio:
        logger.error("Portfolio %d not found", portfolio_id)
        return False

      # Load portfolio assets
      assets = db.query(PortfolioAsset).filter(
        PortfolioAsset.portfolio_id == portfolio_id,
        PortfolioAsset.is_active == True,
      ).all()

      if not assets:
        logger.error("Portfolio %d has no active assets", portfolio_id)
        return False

      # Determine strategy source
      source_data = json.loads(portfolio.source_strategies or "{}")
      strategy_name = source_data.get("strategy_name", "")
      config_name = source_data.get("config_name", "")
      base_tf = source_data.get("timeframe", "")
      extra_tfs = source_data.get("extra_timeframes", [])

      # CS-029: Reject portfolios whose saved config declares max_pyramiding > 1.
      # The live engine is strictly 1-position-per-symbol-per-side (enforced in
      # _has_open_position); activating a max_pyramiding > 1 portfolio would run
      # with different execution semantics than its backtest, producing numbers
      # the user cannot compare back to the optimizer. Fail fast with a plain
      # error rather than letting the divergence happen silently.
      def _as_int(v, default=1):
        try:
          return int(v) if v not in (None, "") else default
        except (TypeError, ValueError):
          return default

      offending_pyr: list[str] = []
      top_pyr = _as_int(source_data.get("max_pyramiding", 1))
      if top_pyr > 1:
        offending_pyr.append(f"source_strategies.max_pyramiding={top_pyr}")
      from backend.db.models import BatchBacktestRun as _BBR
      for _bid in source_data.get("batch_ids", []) or []:
        _br = db.get(_BBR, _bid)
        if _br:
          _bp = json.loads(_br.parameters or "{}")
          _bpyr = _as_int(_bp.get("max_pyramiding", 1))
          if _bpyr > 1:
            offending_pyr.append(f"batch {_bid}: max_pyramiding={_bpyr}")
      if offending_pyr:
        logger.error(
          "Portfolio %d: cannot activate — max_pyramiding > 1 detected (%s). "
          "Live engine only supports 1 position per symbol/side; a portfolio "
          "optimized with pyramiding will NOT reproduce live. Fix: rerun the "
          "optimizer with max_pyramiding=1, or manually edit the saved config.",
          portfolio_id, "; ".join(offending_pyr),
        )
        return False

      # Resolve strategy row: try strategy_id first, then name, then batch fallback
      from backend.db.models import BatchBacktestRun
      strategy_row = None

      # 1. Try strategy_id from source_data (enriched at portfolio creation)
      if source_data.get("strategy_id"):
        strategy_row = db.get(Strategy, source_data["strategy_id"])
        if strategy_row:
          logger.info("Resolved strategy via source_data strategy_id=%d -> %s",
                      source_data["strategy_id"], strategy_row.name)

      # 2. Try strategy_name from source_data
      if not strategy_row and strategy_name:
        strategy_row = db.query(Strategy).filter(
          Strategy.name == strategy_name
        ).first()

      # 3. Fallback: resolve via batch_ids → BatchBacktestRun.strategy_id
      if not strategy_row and source_data.get("batch_ids"):
        for bid in source_data["batch_ids"]:
          batch = db.get(BatchBacktestRun, bid)
          if batch and batch.strategy_id:
            strategy_row = db.get(Strategy, batch.strategy_id)
            if strategy_row:
              logger.info("Resolved strategy via batch %d -> %s (ID=%d)",
                          bid, strategy_row.name, strategy_row.id)
              break

      # 4. Last resort: try first asset's batch_id
      if not strategy_row and assets:
        for asset in assets:
          if asset.batch_id:
            batch = db.get(BatchBacktestRun, asset.batch_id)
            if batch and batch.strategy_id:
              strategy_row = db.get(Strategy, batch.strategy_id)
              if strategy_row:
                logger.info("Resolved strategy via asset batch_id %d -> %s",
                            asset.batch_id, strategy_row.name)
                break

      if not strategy_row or not strategy_row.python_source:
        logger.error("No strategy found for portfolio %d (tried name='%s', batch_ids=%s)",
                     portfolio_id, strategy_name, source_data.get("batch_ids"))
        return False

      # Resolve base timeframe with batch fallback (shared utility)
      if not base_tf:
        base_tf = resolve_timeframe(db, source_data)
        logger.info("Resolved base TF -> %s for portfolio %d", base_tf, portfolio_id)

      # Resolve extra timeframes with batch fallback (shared utility)
      if not extra_tfs:
        extra_tfs = resolve_extra_tfs(db, source_data)
        if extra_tfs:
          logger.info("Resolved extra TFs from source/batch data: %s", extra_tfs)

      # Auto-detect extra timeframes from strategy source code (safety net)
      if not extra_tfs:
        tf_matches = re.findall(
          r'request_security\s*\(\s*["\'](\w+)["\']',
          strategy_row.python_source,
        )
        if tf_matches:
          extra_tfs = list(dict.fromkeys(tf_matches))
          logger.info("Auto-detected extra timeframes from source: %s", extra_tfs)

      # Build default parameters from strategy definition.
      # strategy.parameters can be a list of {name, type, default} defs
      # or a dict of {key: value} — handle both.
      raw_params = json.loads(strategy_row.parameters or "{}")
      if isinstance(raw_params, list):
        # Extract {name: default} from parameter definitions
        default_params = {p["name"]: p.get("default") for p in raw_params if "name" in p}
      else:
        default_params = raw_params

      # For single-strategy portfolios: load config overrides once
      # For cross-strategy: load per-asset from batch parameters
      is_cross = source_data.get("is_cross_strat", False)

      if not is_cross and config_name:
        config_row = db.query(StrategyConfig).filter(
          StrategyConfig.strategy_id == strategy_row.id,
          StrategyConfig.name == config_name,
        ).first()
        if config_row:
          overrides = json.loads(config_row.parameter_values or "{}")
          default_params.update(overrides)

      # Pre-load batch parameters for cross-strategy (batch_id → params dict)
      # Batch.parameters is a dict with 'strategy_inputs' key for actual values
      batch_params_cache: dict[int, dict] = {}
      if is_cross:
        from backend.db.models import BatchBacktestRun
        batch_ids = source_data.get("batch_ids", [])
        for bid in batch_ids:
          batch_row = db.get(BatchBacktestRun, bid)
          if batch_row:
            bp = json.loads(batch_row.parameters or "{}")
            # strategy_inputs contains the actual parameter overrides
            batch_params_cache[bid] = bp.get("strategy_inputs", bp)

      # Build asset list with per-asset batch_id for parameter lookup
      asset_list = [
        {"symbol": a.symbol, "side": a.trade_side, "batch_id": a.batch_id}
        for a in assets
      ]

      # Compile strategy class
      strategy_cls = self._compile_strategy(strategy_row.python_source)
      if not strategy_cls:
        return False

      # Load execution-time filter (NYT hours/days) from the saved portfolio.
      # Same exact keys the optimization stores (batch_selection._filter_trades_exec).
      # CS-041: the enabled-flag is authoritative. If the user disables the
      # session filter but the stored list is non-empty (leftover from a
      # prior save), the filter must NOT be applied — the old code OR'd
      # the enabled flag with the list-non-empty check, which contradicted
      # user intent. Now the list is only consulted when enabled=True.
      _sf_enabled = bool(source_data.get("session_filter_enabled", False))
      _excl_h_raw = source_data.get("session_filter_exclude_hours", []) or []
      _excl_d_raw = source_data.get("session_filter_exclude_days", []) or []
      try:
        exec_excl_hours = {int(h) for h in _excl_h_raw} if _sf_enabled else set()
        exec_excl_days = {int(d) for d in _excl_d_raw} if _sf_enabled else set()
      except (TypeError, ValueError) as e:
        logger.error("Portfolio %d session_filter parse failed (%s) — refusing to activate",
                     portfolio_id, e)
        return False
      if exec_excl_hours or exec_excl_days:
        logger.info(
          "Portfolio %d exec filter ACTIVE: exclude_hours=%s exclude_days=%s (NYT)",
          portfolio_id, sorted(exec_excl_hours), sorted(exec_excl_days),
        )

      # Create active portfolio
      active = ActivePortfolio(
        portfolio_id=portfolio_id,
        strategy_source=strategy_row.python_source,
        parameters=default_params,
        base_tf=base_tf,
        extra_tfs=extra_tfs,
        assets=asset_list,
        exec_exclude_hours=exec_excl_hours,
        exec_exclude_days=exec_excl_days,
      )

      # Catch-up download: ensure candle data is current before warmup
      unique_symbols = list({a["symbol"] for a in asset_list})
      all_tfs = [base_tf] + (extra_tfs or [])
      self._ensure_candles_current(db, unique_symbols, all_tfs)

      # Create per-symbol strategy instances and warm up
      for asset in asset_list:
        symbol = asset["symbol"]

        # Determine parameters for this asset
        asset_params = dict(default_params)  # start with defaults
        if is_cross and asset.get("batch_id"):
          batch_p = batch_params_cache.get(asset["batch_id"])
          if batch_p:
            asset_params.update(batch_p)
            logger.info("  %s: using batch %d params", symbol, asset["batch_id"])
        elif is_cross and not asset.get("batch_id"):
          # Backfill: try to find batch by matching symbol in BacktestRun records
          asset_params = self._resolve_batch_params(
            db, asset, source_data.get("batch_ids", []), batch_params_cache, default_params,
          )

        instance = self._create_strategy_instance(
          strategy_cls, symbol, asset_params, base_tf, extra_tfs, db,
          batch_id=asset.get("batch_id"),
          portfolio_id=portfolio_id,
        )
        if instance:
          active.strategies[symbol] = instance
          active.bar_counts[symbol] = instance._bar
          active.warmed_up[symbol] = instance._bar >= (instance._warmup_bars or 0)

          # Register with candle aggregator
          self._aggregator.register_symbol(symbol, base_tf, extra_tfs)

      self._active[portfolio_id] = active

      logger.info(
        "Portfolio %d activated: %d symbols, tf=%s, strategy=%s",
        portfolio_id, len(active.strategies), base_tf, strategy_name,
      )
      return True

    except Exception as e:
      logger.error("Failed to activate portfolio %d: %s", portfolio_id, e)
      return False
    finally:
      db.close()

  def _resolve_batch_params(
    self, db: Session, asset: dict, batch_ids: list[int],
    batch_params_cache: dict[int, dict], default_params: dict,
  ) -> dict:
    """For existing portfolios without per-asset batch_id, find the batch
    by matching symbol+side against BacktestRun records in each batch."""
    from backend.db.models import BacktestRun
    symbol = asset["symbol"]
    for bid in batch_ids:
      run = db.query(BacktestRun).filter(
        BacktestRun.batch_id == bid,
        BacktestRun.symbol == symbol,
      ).first()
      if run:
        params = dict(default_params)
        batch_p = batch_params_cache.get(bid)
        if batch_p:
          params.update(batch_p)
        logger.info("  %s: resolved to batch %d (backfill)", symbol, bid)
        return params
    logger.warning("  %s: no batch match found, using defaults", symbol)
    return dict(default_params)

  def deactivate_portfolio(self, portfolio_id: int) -> None:
    """Stop monitoring a portfolio."""
    active = self._active.pop(portfolio_id, None)
    if not active:
      return

    # Unregister symbols (only if no other portfolio needs them)
    all_symbols_needed = set()
    for pid, ap in self._active.items():
      for asset in ap.assets:
        all_symbols_needed.add(asset["symbol"])

    for asset in active.assets:
      if asset["symbol"] not in all_symbols_needed:
        self._aggregator.unregister_symbol(asset["symbol"])

    logger.info("Portfolio %d deactivated", portfolio_id)

  def refresh_portfolio(self, portfolio_id: int) -> dict:
    """Re-read active assets from DB and update symbol set.

    Adds new symbols (creates strategy instances, registers with aggregator).
    Removes old symbols only if they have no open positions.
    Preserves existing strategy instances and positions.

    Returns: {"added": [...], "removed": [...], "kept": [...]}
    """
    active = self._active.get(portfolio_id)
    if not active:
      return {"error": "Portfolio not active"}

    db = self._db_factory()
    try:
      from backend.db.models import PortfolioAsset as PA
      db_assets = db.query(PA).filter(
        PA.portfolio_id == portfolio_id, PA.is_active == True,
      ).all()

      new_asset_map = {a.symbol: a for a in db_assets}
      current_symbols = {a["symbol"] for a in active.assets}
      new_symbols = set(new_asset_map.keys())

      added = new_symbols - current_symbols
      removed = current_symbols - new_symbols
      kept = current_symbols & new_symbols

      # Check for open positions before removing
      from backend.db.models import Position
      actually_removed: list[str] = []
      for sym in removed:
        has_open = db.query(Position).filter(
          Position.symbol == sym, Position.is_open == True,
          Position.portfolio_id == portfolio_id,
        ).first()
        if has_open:
          logger.warning(
            "Cannot remove %s from portfolio %d — has open position",
            sym, portfolio_id,
          )
        else:
          actually_removed.append(sym)

      # Remove symbols (unregister + delete strategy instance)
      for sym in actually_removed:
        active.strategies.pop(sym, None)
        active.bar_counts.pop(sym, None)
        active.warmed_up.pop(sym, None)
        # Only unregister if no other portfolio uses this symbol
        all_other_symbols = set()
        for pid, ap in self._active.items():
          if pid != portfolio_id:
            for a in ap.assets:
              all_other_symbols.add(a["symbol"])
        if sym not in all_other_symbols:
          self._aggregator.unregister_symbol(sym)

      # Add new symbols (create strategy instances + register)
      strategy_cls = self._compile_strategy(active.strategy_source)
      source_data = {}
      portfolio = db.get(Portfolio, portfolio_id)
      if portfolio:
        source_data = json.loads(portfolio.source_strategies or "{}")
      is_cross = source_data.get("is_cross_strat", False)

      # Pre-load batch params for new symbols if cross-strategy
      batch_params_cache: dict[int, dict] = {}
      if is_cross:
        from backend.db.models import BatchBacktestRun
        batch_ids = source_data.get("batch_ids", [])
        for bid in batch_ids:
          batch_row = db.get(BatchBacktestRun, bid)
          if batch_row:
            bp = json.loads(batch_row.parameters or "{}")
            batch_params_cache[bid] = bp.get("strategy_inputs", bp)

      actually_added: list[str] = []
      for sym in added:
        asset_row = new_asset_map[sym]
        asset_params = dict(active.parameters)
        if is_cross and asset_row.batch_id:
          batch_p = batch_params_cache.get(asset_row.batch_id)
          if batch_p:
            asset_params.update(batch_p)

        if strategy_cls:
          instance = self._create_strategy_instance(
            strategy_cls, sym, asset_params,
            active.base_tf, active.extra_tfs, db,
            portfolio_id=portfolio_id,
          )
          if instance:
            active.strategies[sym] = instance
            active.bar_counts[sym] = instance._bar
            active.warmed_up[sym] = instance._bar >= (instance._warmup_bars or 0)
            self._aggregator.register_symbol(sym, active.base_tf, active.extra_tfs)
            actually_added.append(sym)

      # Rebuild asset list from DB
      active.assets = [
        {"symbol": a.symbol, "side": a.trade_side, "batch_id": a.batch_id}
        for a in db_assets
      ]

      result = {
        "added": actually_added,
        "removed": actually_removed,
        "kept": list(kept),
      }
      logger.info("Portfolio %d refreshed: %s", portfolio_id, result)
      return result

    except Exception as e:
      logger.error("Failed to refresh portfolio %d: %s", portfolio_id, e)
      return {"error": str(e)}
    finally:
      db.close()

  def get_active_portfolios(self) -> list[int]:
    """Get list of active portfolio IDs."""
    return list(self._active.keys())

  def is_active(self, portfolio_id: int) -> bool:
    """Check if a portfolio is currently active."""
    return portfolio_id in self._active

  # ── Bar Close Handler ─────────────────────────────────────────────

  def on_bar_close(self, symbol: str, timeframe: str, bar_data: dict) -> None:
    """Called by CandleAggregator when a TF bar closes.

    For each active portfolio that uses this symbol/TF:
    1. Append bar to strategy's DataFrame
    2. Update multi-TF data
    3. Run strategy.next()
    4. Read pending orders and emit signals
    """
    for portfolio_id, active in self._active.items():
      if timeframe != active.base_tf:
        # This is an extra TF bar — update the TF data but don't run strategy
        self._update_extra_tf(active, symbol, timeframe, bar_data)
        continue

      # Check if this symbol is in this portfolio
      asset_sides = {a["symbol"]: a["side"] for a in active.assets}
      asset_batch_ids = {a["symbol"]: a.get("batch_id") for a in active.assets}
      if symbol not in asset_sides:
        continue

      strategy = active.strategies.get(symbol)
      if not strategy:
        continue

      try:
        # Append new bar to strategy's DataFrame
        self._append_bar(strategy, bar_data)

        # Update extra TF DataFrames (convert UTC → NYT to match backtest)
        for tf in active.extra_tfs:
          tf_df = self._aggregator.get_history_df(symbol, tf)
          if not tf_df.empty:
            strategy._tf_data[tf] = to_nyt(tf_df)

        # Recompute indicators from updated _df (fixes stale numpy array length)
        self._refresh_indicators(strategy)

        # Advance bar counter
        strategy._bar = len(strategy._df) - 1
        active.bar_counts[symbol] = strategy._bar

        # Check warmup
        warmup = strategy._warmup_bars or 0
        if strategy._bar < warmup:
          # Still warming up — run next() for indicator computation
          # but suppress any orders
          strategy.next()
          strategy._pending_orders.clear()
          strategy._exit_orders.clear()
          continue

        if not active.warmed_up.get(symbol, False):
          active.warmed_up[symbol] = True
          logger.info("Warmup complete for %s in portfolio %d (bar %d)", symbol, portfolio_id, strategy._bar)

        # Sync DB positions → strategy._positions (live mode bridge).
        # Must run AFTER _refresh_indicators (which restores snapshot) and
        # BEFORE strategy.next() (which reads _positions for trade mgmt).
        self._sync_positions_from_db(strategy, portfolio_id, symbol)

        # Run strategy logic
        strategy.next()

        # Read and process signals. Pass the bar's NYT timestamp so the
        # exec-filter gate can reject entries whose bar hour/weekday is
        # excluded (mirrors optimization's _filter_trades_exec check).
        side_filter = asset_sides[symbol]  # "Long", "Short", or "Both"
        try:
          bar_nyt = strategy._df.index[-1].to_pydatetime()
        except (AttributeError, IndexError):
          bar_nyt = None
        signals = self._extract_signals(
          strategy, portfolio_id, symbol, side_filter,
          batch_id=asset_batch_ids.get(symbol),
          bar_nyt=bar_nyt,
          exec_exclude_hours=active.exec_exclude_hours,
          exec_exclude_days=active.exec_exclude_days,
        )

        for signal in signals:
          if self._on_signal:
            try:
              self._on_signal(signal)
            except Exception as e:
              logger.error("Signal callback error: %s", e)

        # Clear pending orders after processing
        strategy._pending_orders.clear()

        # Persist updated strategy state so the next restart resumes
        # from this bar (instead of replaying full history). Save is
        # best-effort — failure here must not break live trading.
        self._save_strategy_state(portfolio_id, symbol, strategy, bar_nyt)

      except Exception as e:
        logger.error(
          "Strategy execution error for %s in portfolio %d: %s",
          symbol, portfolio_id, e,
        )

  # ── Signal Extraction ─────────────────────────────────────────────

  def _extract_signals(
    self,
    strategy,
    portfolio_id: int,
    symbol: str,
    side_filter: str,
    batch_id: int | None = None,
    bar_nyt: datetime | None = None,
    exec_exclude_hours: set[int] | None = None,
    exec_exclude_days: set[int] | None = None,
  ) -> list[StrategySignal]:
    """Read pending orders from strategy and convert to signals.

    Applies side filtering (Long-only, Short-only, Both) based on
    the portfolio asset configuration, and — for entries only — the
    execution-time filter (NYT hour/weekday exclusion) that mirrors
    the optimization's _filter_trades_exec check. Exits are never
    blocked: if we entered a position we must be able to exit it.
    """
    signals = []
    excl_h = exec_exclude_hours or set()
    excl_d = exec_exclude_days or set()
    has_exec_filter = bool(excl_h or excl_d)

    for order in strategy._pending_orders:
      action = order.get("action", "")
      direction = order.get("direction", "long")
      entry_id = order.get("id", "")

      # Side filter
      if action == "entry":
        if side_filter == "Long" and direction == "short":
          continue
        if side_filter == "Short" and direction == "long":
          continue

      # Execution-time filter (entries only). Uses the bar's NYT timestamp
      # — identical to how the optimization checks entry_time.hour/weekday.
      # If we have a filter configured but NO bar timestamp, we refuse the
      # entry rather than silently trade (fail-closed).
      if action == "entry" and has_exec_filter:
        if bar_nyt is None:
          logger.warning(
            "Entry BLOCKED (no bar timestamp, filter active): %s %s %s p%d",
            symbol, direction, entry_id, portfolio_id,
          )
          continue
        _hour = bar_nyt.hour
        _wday = bar_nyt.weekday()
        if _hour in excl_h or _wday in excl_d:
          logger.info(
            "Entry BLOCKED by exec filter: %s %s %s @ NYT %s (hour=%d weekday=%d) p%d",
            symbol, direction, entry_id, bar_nyt.isoformat(), _hour, _wday, portfolio_id,
          )
          continue

      # Check for duplicate entries (signal dedup)
      if action == "entry" and self._has_open_position(portfolio_id, symbol, direction):
        logger.debug("Skipping duplicate entry: %s %s %s", symbol, direction, entry_id)
        continue

      # For entry orders: look up SL/TP/TSL from matching exit orders.
      # Strategy calls self.exit(from_entry=entry_id, stop=..., limit=...)
      # in the same next() call — we need to bridge this data to the signal
      # so PositionManager can size the position and OrderManager can place exits.
      sl_price = 0.0
      tp_price = 0.0
      trail_pts = 0.0
      trail_off = 0.0
      if action == "entry":
        for eo in strategy._exit_orders.values():
          if eo.from_entry == entry_id:
            if eo.stop > 0 and sl_price == 0:
              sl_price = eo.stop
            if eo.limit > 0 and tp_price == 0:
              tp_price = eo.limit
            if eo.trail_points > 0 and trail_pts == 0:
              trail_pts = eo.trail_points
              trail_off = eo.trail_offset

        # Fallback: duck-type from the strategy's own SL tracking arrays.
        # Mirrors the BacktestEngine workaround in _register_exits_for_close_fills
        # (engine.py:517) for strategies (e.g. Market Structure + vol) that
        # don't pass stop= on self.entry() and don't call self.exit(from_entry=..)
        # until the NEXT bar's _update_active_trades. Without this fallback every
        # entry from such a strategy hits engine_coordinator's sl_price<=0 guard
        # and gets silently dropped.
        # Window of validity: t_id[]/t_sl[] are populated by _execute_entry()
        # (line 1338-1345) immediately BEFORE _extract_signals reads them in the
        # same on_bar_close call — values are fresh and matched by entry_id.
        if sl_price == 0:
          t_id = getattr(strategy, "t_id", None)
          t_sl = getattr(strategy, "t_sl", None)
          if t_id and t_sl and len(t_id) == len(t_sl):
            for i in range(len(t_id) - 1, -1, -1):  # most-recent first
              if t_id[i] == entry_id:
                try:
                  v = float(t_sl[i])
                  if v > 0:
                    sl_price = v
                except (TypeError, ValueError):
                  pass
                break  # found matching tid, stop scanning
          # Optionally pull TP1 too (TP is not required by the engine guard,
          # but propagating it lets OrderManager place a TP order on entry).
          if tp_price == 0:
            t_tp1 = getattr(strategy, "t_tp1", None)
            if t_id and t_tp1 and len(t_id) == len(t_tp1):
              for i in range(len(t_id) - 1, -1, -1):
                if t_id[i] == entry_id:
                  try:
                    v = float(t_tp1[i])
                    if v > 0:
                      tp_price = v
                  except (TypeError, ValueError):
                    pass
                  break

      signal = StrategySignal(
        portfolio_id=portfolio_id,
        symbol=symbol,
        action=action,
        direction=direction,
        entry_id=entry_id,
        qty=order.get("qty", 0),
        sl_price=sl_price,
        tp_price=tp_price,
        trail_points=trail_pts,
        trail_offset=trail_off,
        limit_price=order.get("limit", 0),
        comment=order.get("comment", ""),
        batch_id=batch_id,
      )
      signals.append(signal)

    # Also check persistent exit orders for new SL/TP/TSL registrations
    for exit_id, eo in strategy._exit_orders.items():
      # These are informational — the live engine uses OrderManager for actual exits
      # We emit exit signals so the coordinator can place/update exchange orders
      signal = StrategySignal(
        portfolio_id=portfolio_id,
        symbol=symbol,
        action="register_exit",
        direction="",
        entry_id=eo.from_entry if hasattr(eo, 'from_entry') else "",
        qty=eo.qty if hasattr(eo, 'qty') else 0,
        sl_price=eo.stop if hasattr(eo, 'stop') else 0,
        tp_price=eo.limit if hasattr(eo, 'limit') else 0,
        trail_points=eo.trail_points if hasattr(eo, 'trail_points') else 0,
        trail_offset=eo.trail_offset if hasattr(eo, 'trail_offset') else 0,
        limit_price=0,
        comment=exit_id,
        batch_id=batch_id,
      )
      signals.append(signal)

    return signals

  def _has_open_position(self, portfolio_id: int, symbol: str, direction: str) -> bool:
    """Check if there's already an open position for this symbol/direction.

    CS-042: consult the coordinator's in-memory cache first (O(1)) to
    avoid a fresh DB session per candidate signal. The DB fallback is
    kept as a safety net for drift and for standalone executor tests.
    """
    if self._position_key_check is not None:
      if self._position_key_check(portfolio_id, symbol, direction):
        return True
      # Cache miss: trust the cache on the hot path. The coordinator's
      # periodic reconciliation (see _open_position_keys stale-sweep)
      # keeps it consistent with the DB.
      return False
    db = self._db_factory()
    try:
      side = "Buy" if direction == "long" else "Sell"
      exists = db.query(Position).filter(
        Position.portfolio_id == portfolio_id,
        Position.symbol == symbol,
        Position.side == side,
        Position.is_open == True,
      ).first()
      return exists is not None
    finally:
      db.close()

  # ── Strategy Compilation ──────────────────────────────────────────

  @staticmethod
  def _compile_strategy(python_source: str):
    """Compile strategy source code and return the class."""
    from backend.utils.safe_exec import safe_exec
    try:
      strategy_cls = safe_exec(python_source, label="live_strategy")
      if not strategy_cls:
        logger.error("Strategy source does not define TranspiledStrategy")
        return None
      return strategy_cls
    except (ValueError, SyntaxError) as e:
      logger.error("Failed to compile strategy: %s", e)
      return None

  def _create_strategy_instance(
    self,
    strategy_cls,
    symbol: str,
    parameters: dict,
    base_tf: str,
    extra_tfs: list[str],
    db: Session,
    batch_id: int | None = None,
    portfolio_id: int | None = None,
  ):
    """Create and warm up a strategy instance for a symbol.

    Default path is full replay of all available candles to converge
    indicators and market structure variables. When `portfolio_id` is
    supplied AND a recent state file exists at
    `data/live_engine_state/{portfolio_id}/{symbol}.json`, the saved
    state is restored and only bars after the saved last bar are
    replayed — turning a 30k–70k bar replay into typically 0–48 bars.

    `portfolio_id=None` (legacy callers) preserves the original
    full-replay behavior so this change is strictly additive.
    """
    try:
      instance = strategy_cls()

      # Load historical data — ALL candles (batch's actual data + gap to now)
      from backend.db.models import OHLCVData
      from backend.db.database import ohlcv_router
      _hist_db = ohlcv_router.get_session(base_tf)
      try:
        historical = _hist_db.query(OHLCVData).filter(
          OHLCVData.symbol == symbol,
          OHLCVData.timeframe == self._tf_to_db_format(base_tf),
        ).order_by(OHLCVData.timestamp.asc()).all()
      finally:
        _hist_db.close()

      if not historical:
        logger.warning("No historical data for %s %s — skipping warmup", symbol, base_tf)
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
      else:
        df = pd.DataFrame([{
          "timestamp": h.timestamp,
          "open": h.open,
          "high": h.high,
          "low": h.low,
          "close": h.close,
          "volume": h.volume,
        } for h in historical])
        df = df.set_index("timestamp")
        # Convert UTC → NYT to match backtest convention
        # so strategy time properties (hour, minute, dayofweek) see NYT values
        if not df.empty:
          df = to_nyt(df)

      # Set strategy internal state
      instance._df = df
      instance._bar = 0
      instance._inputs = parameters

      # Pre-extract OHLCV + time arrays (must match backtester engine.py setup)
      import numpy as np
      instance._opens = df["open"].values
      instance._highs = df["high"].values
      instance._lows = df["low"].values
      instance._closes = df["close"].values
      instance._volumes = df["volume"].values
      instance._timestamps = df.index
      instance._hours = df.index.hour
      instance._minutes = df.index.minute
      instance._seconds = df.index.second
      _pine_map = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 1}
      instance._dayofweek_pine = np.array([_pine_map[d] for d in df.index.dayofweek])
      instance._days = df.index.day
      instance._months = df.index.month
      instance._years = df.index.year
      logger.info("Strategy inputs for %s: %s", symbol, parameters)
      instance._positions = {}
      instance._exit_orders = {}
      instance._trades = []
      instance._pending_orders = []
      instance._equity_curve = []

      # Load extra TF data
      instance._tf_data = {}
      for tf in extra_tfs:
        _tf_db = ohlcv_router.get_session(tf)
        try:
          tf_hist = _tf_db.query(OHLCVData).filter(
            OHLCVData.symbol == symbol,
            OHLCVData.timeframe == self._tf_to_db_format(tf),
          ).order_by(OHLCVData.timestamp.asc()).all()
        finally:
          _tf_db.close()

        if tf_hist:
          tf_df = pd.DataFrame([{
            "timestamp": h.timestamp,
            "open": h.open,
            "high": h.high,
            "low": h.low,
            "close": h.close,
            "volume": h.volume,
          } for h in tf_hist])
          tf_df = tf_df.set_index("timestamp")
          instance._tf_data[tf] = to_nyt(tf_df)

          # Also seed the aggregator
          bars = [{"timestamp": h.timestamp, "open": h.open, "high": h.high,
                   "low": h.low, "close": h.close, "volume": h.volume}
                  for h in tf_hist]
          self._aggregator.seed_history(symbol, tf, bars)

      # Initialize strategy (indicators, etc.)
      try:
        instance.init()
        instance._convert_indicators_to_arrays()
      except Exception as e:
        logger.error(
          "Strategy init() FAILED for %s — skipping symbol: %s", symbol, e,
          exc_info=True,
        )
        return None

      # ── Determine warmup replay range ──
      strategy_warmup = instance._warmup_bars or 50

      # ── Incremental warmup (additive — falls through to full replay
      # ──   when no saved state exists or it's stale) ─────────────────
      replay_start = 0
      resumed_from_state = False
      if portfolio_id is not None:
        saved = self._load_strategy_state(portfolio_id, symbol)
        if saved is not None:
          try:
            from backend.backtester.engine import BacktestEngine

            # Find the index of the saved last bar in the freshly-loaded df.
            # If we can't locate it (df may extend further forward, or the
            # candle at saved_ts was rebuilt), fall through to full replay.
            saved_ts_iso = saved.get("last_bar_timestamp_iso")
            if saved_ts_iso:
              saved_ts = pd.Timestamp(saved_ts_iso)
              if saved_ts.tzinfo is not None:
                saved_ts = saved_ts.tz_convert("America/New_York").tz_localize(None)
              # df.index is tz-naive NYT (set above by to_nyt())
              boundary_idx = df.index.searchsorted(saved_ts, side="right") - 1
              if 0 <= boundary_idx < len(df):
                # Restore the saved per-strategy state (market structure,
                # counters, trade arrays). Indicators are re-derived above
                # in instance.init() from the full df, so they're already
                # correct; we ONLY restore the public attrs that
                # _capture_strategy_state_static persisted.
                BacktestEngine._restore_strategy_state(
                  instance, saved.get("state", {}),
                )
                # Replay only NEW bars after the saved boundary.
                replay_start = boundary_idx + 1
                resumed_from_state = True
                logger.info(
                  "Engine state restored for %s (portfolio=%d): saved "
                  "last_bar=%s, replaying %d new bars (vs %d total)",
                  symbol, portfolio_id, saved_ts.isoformat(),
                  len(df) - replay_start, len(df),
                )
          except Exception as e:
            logger.warning(
              "Engine state restore failed for %s (portfolio=%d) — "
              "falling back to full replay: %s",
              symbol, portfolio_id, e,
            )
            replay_start = 0
            resumed_from_state = False

      # ── Full or partial replay loop ─────────────────────────────────
      # If we resumed from state, replay_start > 0 and we replay only the
      # new bars. Otherwise (fresh start or stale state), replay_start=0
      # and we replay everything — identical to pre-feature behavior.
      for bar_idx in range(replay_start, len(df)):
        instance._bar = bar_idx
        instance.next()
        instance._pending_orders.clear()
        instance._exit_orders.clear()

      # ── Indicator warmup validation ──
      # Check that enough bars were available for indicator convergence.
      # HTF data (daily, weekly) is loaded in FULL (uncapped) — always sufficient.
      bars_replayed = len(df) - replay_start if replay_start < len(df) else len(df)
      min_needed = max(200, strategy_warmup * 4)
      if bars_replayed < min_needed:
        logger.warning(
          "WARMUP INSUFFICIENT for %s: replayed %d bars but need %d "
          "(warmup_bars=%d * 4). Indicators may not be converged!",
          symbol, bars_replayed, min_needed, strategy_warmup,
        )
      # Log HTF warmup status for each extra timeframe
      for tf in extra_tfs:
        tf_df = instance._tf_data.get(tf)
        tf_bars = len(tf_df) if tf_df is not None else 0
        htf_min = max(100, strategy_warmup * 5) if tf in ("D", "W") else min_needed
        if tf_bars < htf_min:
          logger.warning(
            "HTF warmup low for %s %s: %d bars loaded (recommend %d+)",
            symbol, tf, tf_bars, htf_min,
          )
        else:
          logger.info("HTF warmup OK for %s %s: %d bars loaded", symbol, tf, tf_bars)

      # Convert numpy arrays to Python lists for efficient O(1) append in live mode.
      # np.append() copies the entire array every call (O(n)); list.append() is O(1) amortized.
      # Lists work everywhere strategies read data: indexing, slicing, pd.Series() wrapping.
      for attr in (
        "_opens", "_highs", "_lows", "_closes", "_volumes",
        "_hours", "_minutes", "_seconds", "_dayofweek_pine",
        "_days", "_months", "_years",
      ):
        arr = getattr(instance, attr, None)
        if arr is not None and hasattr(arr, "tolist"):
          setattr(instance, attr, arr.tolist())

      logger.info(
        "Strategy warmed up for %s: %d bars loaded, replayed from idx %d "
        "(%s), warmup=%d, bar_idx=%d",
        symbol, len(df), replay_start,
        "RESUMED" if resumed_from_state else "full replay",
        strategy_warmup, instance._bar,
      )

      # Save state for incremental warmup on next start.
      # Only save if we have a portfolio_id context AND df has data —
      # legacy callers (portfolio_id=None) skip this entirely so behavior
      # matches pre-feature.
      if portfolio_id is not None and len(df) > 0:
        try:
          last_bar_ts = df.index[-1]
          if hasattr(last_bar_ts, "to_pydatetime"):
            last_bar_ts = last_bar_ts.to_pydatetime()
          self._save_strategy_state(portfolio_id, symbol, instance, last_bar_ts)
        except Exception as e:
          # Save failures must NOT break activation
          logger.warning(
            "post-warmup save failed for %s/%d: %s", symbol, portfolio_id, e,
          )

      return instance

    except Exception as e:
      logger.error("Failed to create strategy instance for %s: %s", symbol, e)
      return None

  # ── Catch-up Download ─────────────────────────────────────────

  def _ensure_candles_current(
    self, db: Session, symbols: list[str], timeframes: list[str],
  ) -> None:
    """Download missing candles from Bybit REST if DB data is stale.

    Checks the latest timestamp in OHLCVData for each (symbol, tf).
    If the gap exceeds 2× the TF period, fetches missing bars.
    Called during portfolio activation to handle offline gaps.
    """
    from datetime import datetime, timedelta, timezone

    from backend.data.bybit_downloader import BybitDownloader
    from backend.data.data_manager import DataManager
    from backend.db.models import OHLCVData

    tf_minutes = {
      "1": 1, "3": 3, "5": 5, "15": 15, "30": 30,
      "60": 60, "120": 120, "240": 240, "360": 360, "720": 720,
      "D": 1440, "W": 10080,
    }

    dm = DataManager()
    downloader = BybitDownloader()
    now_utc = datetime.now(timezone.utc)

    for symbol in symbols:
      for tf in timeframes:
        db_tf = self._tf_to_db_format(tf)
        minutes = tf_minutes.get(tf, 30)
        gap_threshold = timedelta(minutes=minutes * 2)

        # Find latest bar in DB
        from sqlalchemy import func
        from backend.db.database import ohlcv_router
        _gap_db = ohlcv_router.get_session(tf)
        try:
          latest_ts = _gap_db.query(func.max(OHLCVData.timestamp)).filter(
            OHLCVData.symbol == symbol,
            OHLCVData.timeframe == db_tf,
          ).scalar()
        finally:
          _gap_db.close()

        if latest_ts is None:
          logger.warning("No data for %s/%s — full download needed (skip catch-up)", symbol, tf)
          continue

        # Make latest_ts tz-aware for comparison
        if latest_ts.tzinfo is None:
          latest_ts = latest_ts.replace(tzinfo=timezone.utc)

        gap = now_utc - latest_ts
        if gap <= gap_threshold:
          continue  # Data is current

        logger.info(
          "Catch-up: %s/%s stale by %s — downloading missing bars",
          symbol, tf, str(gap).split(".")[0],
        )

        try:
          since_ms = int(latest_ts.timestamp() * 1000)
          # Run async download from sync context safely.
          # asyncio.run_until_complete() crashes inside a running event loop,
          # so we run the coroutine in a new thread with its own event loop.
          import concurrent.futures
          def _fetch():
            import asyncio as _aio
            return _aio.run(downloader.fetch_all_klines(symbol, tf, since_ms=since_ms))
          with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            candles = pool.submit(_fetch).result(timeout=120)
          if candles:
            dm.upsert_candles(symbol, db_tf, candles)
            logger.info("Catch-up: %s/%s — %d bars downloaded", symbol, tf, len(candles))
        except Exception as e:
          logger.error("Catch-up download failed for %s/%s: %s", symbol, tf, e)

  # ── Helpers ───────────────────────────────────────────────────────

  @staticmethod
  def _ts_to_nyt(ts: datetime) -> datetime:
    """Convert a single UTC timestamp to naive New York Time."""
    from zoneinfo import ZoneInfo
    nyt = ZoneInfo("America/New_York")
    if ts.tzinfo is None:
      ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(nyt).replace(tzinfo=None)

  @staticmethod
  def _append_bar(strategy, bar_data: dict) -> None:
    """Append a new bar to the strategy's DataFrame.

    Converts UTC timestamp to naive NYT to match backtest convention.
    """
    nyt_ts = StrategyExecutor._ts_to_nyt(bar_data["timestamp"])
    new_row = pd.DataFrame([{
      "open": bar_data["open"],
      "high": bar_data["high"],
      "low": bar_data["low"],
      "close": bar_data["close"],
      "volume": bar_data["volume"],
    }], index=[nyt_ts])
    strategy._df = pd.concat([strategy._df, new_row])

    # Append to Python lists — O(1) amortized (pre-allocated by Python internals).
    # Arrays were converted from numpy to lists after warmup in _create_strategy_instance().
    # Lists work everywhere strategies read data: indexing, slicing, and pd.Series() wrapping.
    _pine_map = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 1}
    strategy._opens.append(bar_data["open"])
    strategy._highs.append(bar_data["high"])
    strategy._lows.append(bar_data["low"])
    strategy._closes.append(bar_data["close"])
    strategy._volumes.append(bar_data["volume"])
    strategy._timestamps = strategy._df.index
    strategy._hours.append(nyt_ts.hour)
    strategy._minutes.append(nyt_ts.minute)
    strategy._seconds.append(nyt_ts.second)
    strategy._dayofweek_pine.append(_pine_map[nyt_ts.weekday()])
    strategy._days.append(nyt_ts.day)
    strategy._months.append(nyt_ts.month)
    strategy._years.append(nyt_ts.year)

  @staticmethod
  def _refresh_indicators(strategy) -> None:
    """Recompute indicators from the grown DataFrame.

    After warmup, indicator arrays are fixed-length numpy arrays.
    When new bars are appended, OHLCV lists grow but indicator
    arrays don't — causing _safe_iloc to return NaN for new bars.
    This re-runs init() to recompute fresh indicators from the
    full DataFrame, then restores all non-indicator state so
    strategy logic (pivots, trends, trades) is untouched.
    """
    import numpy as np

    # Snapshot all current state
    snapshot = dict(vars(strategy))

    try:
      # Re-run init() — recomputes indicator pd.Series from grown _df
      # (close_series etc. are @property that read from _df dynamically)
      strategy.init()

      # Convert fresh pd.Series to numpy arrays for fast per-bar access
      strategy._convert_indicators_to_arrays()
    except Exception as e:
      # Restore entire snapshot on failure — don't corrupt strategy state
      for attr_name, old_val in snapshot.items():
        setattr(strategy, attr_name, old_val)
      logger.error("_refresh_indicators failed, state restored: %s", e)
      return

    # Identify fresh indicators: public numpy array attributes
    fresh_indicators = set()
    for attr_name in list(vars(strategy)):
      if attr_name.startswith('_'):
        continue
      val = getattr(strategy, attr_name)
      if isinstance(val, np.ndarray):
        fresh_indicators.add(attr_name)

    # Restore all non-indicator state from snapshot
    for attr_name, old_val in snapshot.items():
      if attr_name in fresh_indicators:
        continue  # Keep fresh indicator arrays
      if attr_name == '_safe_iloc':
        continue  # Keep fresh override from _convert_indicators_to_arrays
      if attr_name == '_tf_data':
        continue  # Keep fresh HTF indicator columns from init()
      setattr(strategy, attr_name, old_val)

  def _sync_positions_from_db(
    self, strategy, portfolio_id: int, symbol: str,
  ) -> None:
    """Sync open DB positions into strategy._positions for live mode.

    The backtester engine manages strategy._positions directly (opens/closes
    on fill), but in live mode positions live in the DB via OrderManager.
    Without this bridge, strategy._positions is always empty, which causes:
      - _cleanup_closed_trades() to nuke all trade tracking arrays every bar
      - _update_active_trades() TSL/BE logic to never run
      - max-trades-per-range check to always pass

    Maps DB positions to strategy's t_id entries by matching direction
    (Buy→long, Sell→short). Live mode allows one position per symbol+direction,
    so this is a 1:1 mapping.
    """
    from backend.backtester.models import Position as StratPosition

    db = self._db_factory()
    try:
      open_positions = db.query(Position).filter(
        Position.portfolio_id == portfolio_id,
        Position.symbol == symbol,
        Position.is_open == True,
      ).all()
      db_data = [
        {"side": p.side, "entry_price": p.entry_price, "quantity": p.quantity}
        for p in open_positions
      ]
    finally:
      db.close()

    strategy._positions.clear()

    if not db_data:
      # CRITICAL: incremental-warmup state restore (commit 782d5cb)
      # brings back the strategy's `t_id` / `t_dir` / related arrays as
      # they were at end-of-replay — which often includes "trades" that
      # were technically still open on the last historical bar but were
      # never real DB positions in live mode. Without clearing them
      # when the DB says "no open positions", two things break:
      #   1. The strategy's exit-management logic emits register_exit
      #      signals for non-existent positions on every bar close
      #      (noisy but harmless on its own).
      #   2. The strategy's max-trades-per-range gate counts the
      #      phantom entries, hits the cap, and silently refuses to
      #      generate new entry signals — silently blocking ALL new
      #      entries on this symbol. This is the user-visible
      #      "no new entries since the 29th" symptom.
      # Fix: when the DB has no open positions for this (portfolio,
      # symbol), reset the strategy's trade-tracking arrays to match
      # reality. The DB is the source of truth.
      for _attr in (
        "t_id", "t_dir", "t_entry", "t_sl", "t_tp", "t_qty",
        "t_open", "t_close", "t_label",
      ):
        if hasattr(strategy, _attr):
          _cur = getattr(strategy, _attr)
          if isinstance(_cur, (list, dict)):
            _cur.clear()
      return

    # Build direction → DB position map (one per direction in live mode)
    db_by_dir: dict[str, dict] = {}
    for d in db_data:
      direction = "long" if d["side"] == "Buy" else "short"
      db_by_dir[direction] = d

    # Map strategy's tracked trades to DB positions by direction
    if not hasattr(strategy, 't_id') or not hasattr(strategy, 't_dir'):
      return

    for k in range(len(strategy.t_id)):
      if k >= len(strategy.t_dir):
        break
      tid = strategy.t_id[k]
      direction = "long" if strategy.t_dir[k] == 1 else "short"
      db_pos = db_by_dir.get(direction)
      if db_pos:
        strategy._positions[tid] = StratPosition(
          id=tid,
          side=direction,
          entry_price=db_pos["entry_price"],
          qty=db_pos["quantity"],
          entry_bar=0,
          label=tid,
        )

  def _update_extra_tf(self, active: ActivePortfolio, symbol: str, tf: str, bar_data: dict) -> None:
    """Update extra timeframe data for all strategies using this symbol."""
    strategy = active.strategies.get(symbol)
    if not strategy:
      return

    tf_df = self._aggregator.get_history_df(symbol, tf)
    if not tf_df.empty:
      strategy._tf_data[tf] = to_nyt(tf_df)

  @staticmethod
  def _tf_to_db_format(tf: str) -> str:
    """Normalize any TF string to canonical Bybit format for DB queries.

    Handles both display ('5m','1h','4h') and numeric ('5','60','240')
    formats, returns canonical: '5','30','60','240','D','W'.
    """
    from backend.utils.tf_resolver import canonical_tf
    return canonical_tf(tf)
