"""
Dual-pass backtest — runs long-only then short-only, merges results.

Used for the "Long + Short" direction mode where longs and shorts are
evaluated independently rather than mixed in a single pass.
"""
from __future__ import annotations

import pandas as pd

from backend.backtester.engine import BacktestConfig, BacktestEngine, BacktestResult
from backend.backtester.metrics import calculate_metrics
from backend.backtester.models import OrderFill


def run_dual_pass(
  python_source: str,
  df: pd.DataFrame,
  symbol: str,
  timeframe: str,
  inputs: dict | None,
  extra_tf_dfs: dict[str, pd.DataFrame],
  initial_capital: float,
  commission_pct: float,
  slippage_mode: str,
  slippage_value: float,
  leverage: int,
  max_pyramiding: int,
  warmup_bars: int,
  risk_pct: float,
  risk_mode: str = "pct",
  risk_value: float = 0.0,
  martingale_enabled: bool = False,
  martingale_type: str = "anti",
  martingale_multiplier: float = 2.0,
  martingale_max_streak: int = 3,
  vol_filter_enabled: bool = False,
  vol_filter_atr_ceiling: float = 0.0,
  vol_filter_atr_length: int = 14,
  strategy_risk_mode: str = "fixed",
  strategy_risk_value: float = 0.0,
  process_orders_on_close: bool = False,
) -> BacktestResult:
  """Run long-only + short-only passes, merge into single result."""

  _ps = dict(
    risk_mode=risk_mode, risk_value=risk_value,
    strategy_risk_mode=strategy_risk_mode, strategy_risk_value=strategy_risk_value,
    martingale_enabled=martingale_enabled, martingale_type=martingale_type,
    martingale_multiplier=martingale_multiplier, martingale_max_streak=martingale_max_streak,
    vol_filter_enabled=vol_filter_enabled, vol_filter_atr_ceiling=vol_filter_atr_ceiling,
    vol_filter_atr_length=vol_filter_atr_length,
    process_orders_on_close=process_orders_on_close,
  )

  def _make_config(direction: str) -> BacktestConfig:
    return BacktestConfig(
      initial_capital=initial_capital, commission_pct=commission_pct,
      slippage_mode=slippage_mode, slippage_value=slippage_value,
      leverage=leverage, direction_filter=direction,
      max_pyramiding=max_pyramiding, warmup_bars=warmup_bars,
      risk_pct=risk_pct, **_ps,
    )

  def _run_pass(direction: str) -> BacktestResult:
    from backend.utils.safe_exec import safe_exec
    strategy_cls = safe_exec(python_source, label=f"dual_pass_{direction}")
    if not strategy_cls:
      raise ValueError("Code does not define TranspiledStrategy")
    engine = BacktestEngine(_make_config(direction))
    return engine.run(
      strategy_cls(), df, symbol=symbol, timeframe=timeframe,
      inputs=inputs, extra_timeframes=extra_tf_dfs,
    )

  result_long = _run_pass("long")
  result_short = _run_pass("short")

  return _merge_results(result_long, result_short, df, initial_capital, commission_pct)


def _merge_results(
  long_r: BacktestResult,
  short_r: BacktestResult,
  df: pd.DataFrame,
  initial_capital: float,
  commission_pct: float,
) -> BacktestResult:
  """Merge two BacktestResults into one combined result."""
  # Merge trades sorted by timestamp
  all_trades = sorted(
    long_r.trades + short_r.trades,
    key=lambda t: (t.timestamp, t.bar_index),
  )

  # Merge equity curves — sum the PnL deltas from each pass
  long_eq = long_r.equity_curve or []
  short_eq = short_r.equity_curve or []
  max_len = max(len(long_eq), len(short_eq))
  merged_equity: list[float] = []
  for i in range(max_len):
    long_val = long_eq[i] if i < len(long_eq) else initial_capital
    short_val = short_eq[i] if i < len(short_eq) else initial_capital
    # Each pass starts at initial_capital, so combined = long + short - initial
    merged_equity.append(long_val + short_val - initial_capital)

  # Recalculate metrics on merged trades + equity
  metrics = calculate_metrics(
    all_trades, merged_equity, initial_capital,
    total_bars=max(long_r.bars_processed, short_r.bars_processed),
    first_price=float(df["open"].iloc[0]),
    last_price=float(df["close"].iloc[-1]),
    commission_pct=commission_pct,
    timeframe=long_r.timeframe or "1d",
  )

  return BacktestResult(
    trades=all_trades,
    equity_curve=merged_equity,
    metrics=metrics,
    config=long_r.config,
    symbol=long_r.symbol,
    timeframe=long_r.timeframe,
    bars_processed=max(long_r.bars_processed, short_r.bars_processed),
  )


def merge_portfolio_results(
  long_r: "PortfolioResult",
  short_r: "PortfolioResult",
  initial_capital: float,
  timeframe: str = "1d",
) -> "PortfolioResult":
  """Merge two PortfolioResults (long-only + short-only) into one."""
  from backend.backtester.portfolio_engine import PortfolioResult

  # Merge per-symbol trades
  all_symbols = set(long_r.per_symbol_trades) | set(short_r.per_symbol_trades)
  merged_trades: dict[str, list[OrderFill]] = {}
  merged_metrics: dict[str, dict] = {}
  all_trades_combined: list[OrderFill] = []

  for sym in all_symbols:
    long_trades = long_r.per_symbol_trades.get(sym, [])
    short_trades = short_r.per_symbol_trades.get(sym, [])
    sym_trades = sorted(
      long_trades + short_trades,
      key=lambda t: (t.timestamp, t.bar_index),
    )
    merged_trades[sym] = sym_trades
    all_trades_combined.extend(sym_trades)
    # Build per-symbol equity curve from trade fills so drawdown/Sharpe/Sortino
    # are computed correctly (passing [] would zero out all risk metrics)
    sym_equity = [initial_capital]
    for t in sym_trades:
      if t.action == "exit":
        sym_equity.append(sym_equity[-1] + t.pnl - t.commission)
      elif t.action == "entry" and t.commission > 0:
        sym_equity.append(sym_equity[-1] - t.commission)
    # Recalculate per-symbol metrics from merged trades
    merged_metrics[sym] = calculate_metrics(
      sym_trades, sym_equity, initial_capital,
      timeframe=timeframe,
    )
    merged_metrics[sym]["direction_mode"] = "long_plus_short"

  # Merge combined equity
  le = long_r.combined_equity or []
  se = short_r.combined_equity or []
  max_len = max(len(le), len(se))
  merged_equity: list[float] = []
  for i in range(max_len):
    lv = le[i] if i < len(le) else initial_capital
    sv = se[i] if i < len(se) else initial_capital
    merged_equity.append(lv + sv - initial_capital)

  # Recalculate combined metrics from all merged trades + equity
  bars = max(long_r.bars_processed, short_r.bars_processed)
  combined = calculate_metrics(
    all_trades_combined, merged_equity, initial_capital,
    total_bars=bars,
    timeframe=timeframe,
  )
  combined["direction_mode"] = "long_plus_short"

  return PortfolioResult(
    per_symbol_trades=merged_trades,
    per_symbol_metrics=merged_metrics,
    combined_equity=merged_equity,
    combined_metrics=combined,
    bars_processed=bars,
  )
