"""
Portfolio backtest engine — runs a strategy across multiple symbols with shared equity.
All symbols share one capital pool. Risk-based sizing uses portfolio-level equity.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.engine import BacktestConfig, BacktestEngine
from backend.backtester.metrics import calculate_metrics
from backend.backtester.models import OrderFill


def _sanitize_dst_timestamps(tf_data: dict[str, pd.DataFrame]):
  """Remove DST-ambiguous timestamps from TF DataFrames.

  When strategy init() calls tz_localize('America/New_York') on naive NYT
  timestamps, DST fall-back creates ambiguity (e.g. 2023-11-05 01:00:00).
  This removes those rows so tz_localize succeeds on retry.
  """
  from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError
  for tf_key in list(tf_data.keys()):
    tf_df = tf_data[tf_key]
    if tf_df.index.tz is not None:
      continue
    # CS-007: match by typed exception, not bare Exception. Bare except
    # here used to swallow real bugs (missing index, bad dtype, memory
    # error) and silently NaT-fill their rows. Narrow to the canonical
    # pytz classes plus pandas' own wrapper; re-raise everything else.
    try:
      tf_df.index.tz_localize("America/New_York", ambiguous="raise")
    except (AmbiguousTimeError, NonExistentTimeError):
      safe_idx = tf_df.index.tz_localize(
        "America/New_York", ambiguous="NaT", nonexistent="NaT",
      )
      mask = safe_idx.notna()
      tf_data[tf_key] = tf_df[mask]


@dataclass
class PortfolioResult:
  """Output of a portfolio backtest."""
  per_symbol_trades: dict[str, list[OrderFill]] = field(default_factory=dict)
  per_symbol_metrics: dict[str, dict] = field(default_factory=dict)
  combined_equity: list[float] = field(default_factory=list)
  combined_metrics: dict = field(default_factory=dict)
  bars_processed: int = 0


class _PortfolioEngine(BacktestEngine):
  """Engine subclass that calculates equity across all portfolio symbols."""

  def __init__(self, config: BacktestConfig, all_strategies: dict[str, BaseStrategy]):
    super().__init__(config)
    self._all_strategies = all_strategies
    # Portfolio engine bypasses engine.run() which normally initializes
    # _atr_cache. Set it here to prevent AttributeError when _fill_entry()
    # checks self._atr_cache for volatility filter.
    self._atr_cache = None

  def _calculate_equity(self, strategy: BaseStrategy) -> float:
    """Portfolio equity = shared cash + unrealized PnL across ALL symbols."""
    equity = strategy._cash
    for s in self._all_strategies.values():
      if not s._positions or s._bar < 0:
        continue
      try:
        price = s._closes[s._bar]
      except (IndexError, AttributeError):
        continue
      for pos in s._positions.values():
        if pos.side == "long":
          equity += (price - pos.entry_price) * pos.qty
        else:
          equity += (pos.entry_price - price) * pos.qty
    return equity


def run_portfolio_backtest(
  strategy_source: str,
  symbol_dfs: dict[str, pd.DataFrame],
  config: BacktestConfig,
  inputs: dict | None = None,
  extra_tf_dfs: dict[str, dict[str, pd.DataFrame]] | None = None,
  timeframe: str = "1d",
) -> PortfolioResult:
  """Run a portfolio backtest with shared equity across all symbols."""
  if not symbol_dfs:
    return PortfolioResult()

  # Create one strategy instance per symbol
  strategies: dict[str, BaseStrategy] = {}
  from backend.utils.safe_exec import safe_exec
  for symbol in symbol_dfs:
    cls = safe_exec(strategy_source, label=f"portfolio_{symbol}")
    if not cls:
      raise ValueError("Code does not define TranspiledStrategy")
    strategies[symbol] = cls()

  # Portfolio-aware engine (shared across all symbols)
  engine = _PortfolioEngine(config, strategies)
  shared_cash = config.initial_capital
  warmup = config.warmup_bars

  # Initialize each strategy
  for symbol, strategy in strategies.items():
    df = symbol_dfs[symbol]
    strategy._df = df
    strategy._initial_capital = config.initial_capital
    strategy._cash = 0.0
    strategy._positions = {}
    strategy._positions_by_side = {"long": 0, "short": 0}  # CS-050 counter
    strategy._exit_orders = {}
    strategy._consumed_exits = {}
    strategy._trades = []
    strategy._pending_orders = []
    strategy._equity_curve = []
    strategy._inputs = inputs or {}
    strategy._plot_data = {}
    strategy._max_pyramiding = config.max_pyramiding
    strategy._consecutive_wins = 0
    strategy._consecutive_losses = 0
    strategy._closed_trades_count = 0
    strategy._tf_data = {tf: df.copy() for tf, df in (extra_tf_dfs or {}).get(symbol, {}).items()}
    strategy._bar = 0
    try:
      strategy.init()
    except Exception as e:
      # DST ambiguity/gap during indicator tz-localize — match by
      # exception type instead of substring. pytz raises these as
      # canonical classes; relying on str(e) breaks on message changes.
      from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError
      if isinstance(e, (AmbiguousTimeError, NonExistentTimeError)):
        _sanitize_dst_timestamps(strategy._tf_data)
        strategy.init()
      else:
        raise

  # Build unified sorted timeline from all DataFrames
  all_timestamps = sorted(set().union(*(df.index for df in symbol_dfs.values())))
  combined_equity: list[float] = []

  # CS-009: precompute timestamp -> bar-index dict per symbol. The original
  # code called ``df.index.get_loc(ts)`` for every (ts, symbol) pair inside
  # the hot bar loop — that's O(log n) × N_bars × N_symbols, and pandas'
  # get_loc pays a per-call DatetimeIndex overhead. A flat dict lookup is
  # O(1) and roughly 50-100x faster per call. Kept as an additive cache so
  # the fallback below still handles any index values that are missing
  # (defensive — shouldn't happen for normal inputs).
  _ts_to_bar: dict[str, dict] = {
    sym: {ts: i for i, ts in enumerate(df.index)}
    for sym, df in symbol_dfs.items()
  }

  # Bar-by-bar loop across unified timeline
  for ts in all_timestamps:
    for symbol, df in symbol_dfs.items():
      if ts not in df.index:
        continue

      # Fast path: O(1) dict lookup. Falls back to the original
      # get_loc() branch only if the precomputed map is missing a ts.
      _sym_map = _ts_to_bar.get(symbol)
      if _sym_map is not None and ts in _sym_map:
        bar = _sym_map[ts]
      else:
        bar = df.index.get_loc(ts)
        if isinstance(bar, slice):
          bar = bar.start
        elif isinstance(bar, np.ndarray):
          bar = int(np.flatnonzero(bar)[0])
      bar = int(bar)  # ensure scalar int (not np.int64 or array)

      strategy = strategies[symbol]
      strategy._bar = bar
      is_warmup = bar < warmup

      # Sync shared cash into this strategy
      strategy._cash = shared_cash

      if not is_warmup and not config.process_orders_on_close:
        engine._process_pending_orders(strategy, bar)
      if not is_warmup and bar > 0:
        engine._process_exit_orders(strategy, bar)

      # Update position extremes for MAE/MFE tracking
      if not is_warmup:
        engine._update_position_extremes(strategy, bar)

      strategy.next()

      # Process_orders_on_close: fill entries at this bar's close
      if not is_warmup and config.process_orders_on_close:
        engine._process_close_orders(strategy, bar)
        # Register exit orders for just-filled positions so SL is
        # active when bar N+1 opens. Also update extremes.
        engine._register_exits_for_close_fills(strategy, bar)
        engine._update_position_extremes(strategy, bar)

      # Post-next risk resize: override strategy sizing with engine config
      if not is_warmup and (config.risk_value > 0 or config.risk_pct > 0):
        engine._post_next_risk_resize(strategy, bar)

      if is_warmup:
        strategy._pending_orders.clear()
        strategy._exit_orders.clear()

      # Sync cash back (fills may have changed it)
      shared_cash = strategy._cash

    # Record portfolio equity at this timestamp
    ref = strategies[next(iter(strategies))]
    ref._cash = shared_cash
    combined_equity.append(engine._calculate_equity(ref))

  # Close all open positions at end
  for symbol, strategy in strategies.items():
    df = symbol_dfs[symbol]
    strategy._cash = shared_cash
    for pid in list(strategy._positions.keys()):
      engine._close_position_by_id(strategy, len(df) - 1, pid, "backtest_end")
    shared_cash = strategy._cash

  # Collect per-symbol results
  per_symbol_trades: dict[str, list[OrderFill]] = {}
  per_symbol_metrics: dict[str, dict] = {}
  all_trades: list[OrderFill] = []

  for symbol, strategy in strategies.items():
    df = symbol_dfs[symbol]
    per_symbol_trades[symbol] = strategy._trades
    all_trades.extend(strategy._trades)
    per_symbol_metrics[symbol] = calculate_metrics(
      strategy._trades, [], config.initial_capital,
      total_bars=len(df),
      first_price=float(df["open"].iloc[0]),
      last_price=float(df["close"].iloc[-1]),
      commission_pct=config.commission_pct,
      timeframe=timeframe,
    )

  # Combined portfolio metrics
  combined_metrics = calculate_metrics(
    all_trades, combined_equity, config.initial_capital,
    total_bars=len(all_timestamps),
    commission_pct=config.commission_pct,
    timeframe=timeframe,
  )

  return PortfolioResult(
    per_symbol_trades=per_symbol_trades,
    per_symbol_metrics=per_symbol_metrics,
    combined_equity=combined_equity,
    combined_metrics=combined_metrics,
    bars_processed=len(all_timestamps),
  )
