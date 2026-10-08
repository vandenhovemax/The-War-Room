"""
Backtest performance metrics — calculated from trade fills and equity curve.
"""
from __future__ import annotations

import numpy as np

from backend.backtester.models import OrderFill
from backend.constants import BE_ZONE, COMMISSION_PCT


def _bars_per_year(timeframe: str) -> int:
  """Return approximate number of bars per trading year for annualization.

  Crypto markets trade 365 days/year, 24h/day.
  """
  tf = timeframe.lower().strip()
  mapping = {
    "1m": 365 * 24 * 60,       # 525_600
    "3m": 365 * 24 * 20,       # 175_200
    "5m": 365 * 24 * 12,       # 105_120
    "15m": 365 * 24 * 4,       # 35_040
    "30m": 365 * 24 * 2,       # 17_520
    "1h": 365 * 24,            # 8_760
    "2h": 365 * 12,            # 4_380
    "4h": 365 * 6,             # 2_190
    "6h": 365 * 4,             # 1_460
    "12h": 365 * 2,            # 730
    "d": 365,
    "1d": 365,
    "w": 52,
    "1w": 52,
  }
  # Also handle minute-style aliases like "60" (= 1h), "240" (= 4h)
  minute_aliases = {"60": "1h", "120": "2h", "240": "4h", "360": "6h", "720": "12h"}
  tf = minute_aliases.get(tf, tf)
  return mapping.get(tf, 365)  # default to daily if unknown


def calculate_metrics(
  trades: list[OrderFill],
  equity_curve: list[float],
  initial_capital: float,
  total_bars: int = 0,
  first_price: float = 0.0,
  last_price: float = 0.0,
  commission_pct: float = COMMISSION_PCT,
  timeframe: str = "1d",
) -> dict:
  """Calculate backtest performance metrics."""
  exits = [t for t in trades if t.action == "exit"]

  if not exits:
    return _empty_metrics()

  # Group exit fills by position_id to get per-position P&L.
  # Multi-exit positions (TP1+TP2) share a position_id → count as 1.
  from collections import defaultdict

  # Entry commissions (deducted from cash but not reflected in exit PnL).
  # Subtracting them here makes net_profit match the equity curve.
  entry_comm: dict[str, float] = defaultdict(float)
  for t in trades:
    if t.action == "entry" and t.commission > 0:
      key = t.position_id if t.position_id else f"_solo_{id(t)}"
      entry_comm[key] += t.commission

  pos_pnl: dict[str, float] = defaultdict(float)
  for t in exits:
    key = t.position_id if t.position_id else f"_solo_{id(t)}"
    pos_pnl[key] += t.pnl
  # Subtract entry commissions so each position PnL reflects full round-trip cost
  for key in pos_pnl:
    pos_pnl[key] -= entry_comm.get(key, 0.0)
  pnls = list(pos_pnl.values())
  wins = [p for p in pnls if p > BE_ZONE]
  losses = [p for p in pnls if p < -BE_ZONE]

  total_trades = len(pos_pnl)  # unique positions, not exit fills
  win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0.0
  # Breakevens (|pnl| <= BE_ZONE) are neither wins nor losses. `100 - win_rate`
  # counted them as losses, so expectancy fell below the average trade.
  loss_rate = len(losses) / total_trades * 100 if total_trades > 0 else 0.0
  gross_profit = sum(wins) if wins else 0.0
  gross_loss = abs(sum(losses)) if losses else 0.0
  profit_factor = min(gross_profit / gross_loss, 10.0) if gross_loss > 0 else 10.0
  net_profit = sum(pnls)

  # Max drawdown
  eq = np.array(equity_curve) if equity_curve else np.array([initial_capital])
  peak = np.maximum.accumulate(eq)
  drawdown = peak - eq
  max_dd = float(drawdown.max())
  max_dd_pct = float((drawdown / peak).max() * 100) if peak.max() > 0 else 0.0

  # Sharpe ratio (annualized, timeframe-aware)
  returns = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([0.0])
  ann_factor = np.sqrt(_bars_per_year(timeframe))
  sharpe = 0.0
  if len(returns) > 1 and np.std(returns) > 0:
    sharpe = float(np.mean(returns) / np.std(returns) * ann_factor)

  # Sortino ratio (annualized, downside deviation only)
  sortino = 0.0
  if len(returns) > 1:
    downside = returns[returns < 0]
    down_std = float(np.sqrt(np.mean(downside ** 2))) if len(downside) > 0 else 0.0
    if down_std > 0:
      sortino = float(np.mean(returns) / down_std * ann_factor)

  # Consecutive wins/losses
  max_consec_wins = _max_consecutive(pnls, lambda x: x > 0)
  max_consec_losses = _max_consecutive(pnls, lambda x: x <= 0)

  # Averages
  avg_win = float(np.mean(wins)) if wins else 0.0
  avg_loss = float(np.mean(losses)) if losses else 0.0

  # Extended TV-compatible metrics
  # Cap ratios: low trade counts get tighter cap (avoids misleading outliers)
  _ratio_cap = 10.0 if total_trades < 10 else 99.0
  win_loss_ratio = min(abs(avg_win / avg_loss), _ratio_cap) if avg_loss != 0 else _ratio_cap
  expectancy = (win_rate / 100 * avg_win) - (loss_rate / 100 * abs(avg_loss))
  recovery_factor = min(net_profit / max_dd, _ratio_cap) if max_dd > 0 else _ratio_cap
  bars_in = _bars_in_trade(trades)
  exposure_pct = (bars_in / total_bars * 100) if total_bars > 0 else 0.0
  avg_dur = _avg_trade_duration(trades)
  total_comm = _total_commission(trades, commission_pct)
  buy_hold = ((last_price - first_price) / first_price * 100) if first_price > 0 else 0.0
  max_sim = _max_simultaneous_positions(trades)

  return {
    "total_trades": total_trades,
    "win_rate": round(win_rate, 2),
    "profit_factor": round(profit_factor, 2),
    "net_profit": round(net_profit, 2),
    "net_profit_pct": round(net_profit / initial_capital * 100, 2),
    "max_drawdown": round(max_dd, 2),
    "max_drawdown_pct": round(max_dd_pct, 2),
    "sharpe_ratio": round(sharpe, 2),
    "sortino_ratio": round(sortino, 2),
    "avg_trade": round(net_profit / total_trades, 2),
    "largest_win": round(max(wins), 2) if wins else 0.0,
    "largest_loss": round(min(losses), 2) if losses else 0.0,
    "avg_win": round(avg_win, 2),
    "avg_loss": round(avg_loss, 2),
    "max_consecutive_wins": max_consec_wins,
    "max_consecutive_losses": max_consec_losses,
    # Extended metrics (TradingView-compatible)
    "win_loss_ratio": round(win_loss_ratio, 2),
    "expectancy": round(expectancy, 2),
    "recovery_factor": round(recovery_factor, 2),
    "exposure_pct": round(exposure_pct, 2),
    "avg_duration_bars": round(avg_dur, 1),
    "total_commission": round(total_comm, 2),
    "total_bars": total_bars,
    "buy_hold_return_pct": round(buy_hold, 2),
    "max_simultaneous_trades": max_sim,
  }


def _empty_metrics() -> dict:
  return {
    "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
    "net_profit": 0.0, "net_profit_pct": 0.0,
    "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
    "sharpe_ratio": 0.0, "sortino_ratio": 0.0, "avg_trade": 0.0,
    "largest_win": 0.0, "largest_loss": 0.0,
    "avg_win": 0.0, "avg_loss": 0.0,
    "max_consecutive_wins": 0, "max_consecutive_losses": 0,
    "win_loss_ratio": 0.0, "expectancy": 0.0,
    "recovery_factor": 0.0, "exposure_pct": 0.0,
    "avg_duration_bars": 0.0, "total_commission": 0.0,
    "total_bars": 0, "buy_hold_return_pct": 0.0,
    "max_simultaneous_trades": 0,
    "avg_consec_bull": 0.0, "avg_consec_bear": 0.0,
    "avg_consec_all": 0.0, "total_ranges": 0,
  }


def _max_consecutive(values: list[float], predicate) -> int:
  """Count max consecutive items matching predicate."""
  max_count = 0
  current = 0
  for v in values:
    if predicate(v):
      current += 1
      max_count = max(max_count, current)
    else:
      current = 0
  return max_count


def _bars_in_trade(trades: list[OrderFill]) -> int:
  """Count total bars spent in positions.

  For multi-exit positions (e.g. TP1 at 50%, TP2 at 50%), uses the
  LAST exit bar so exposure is measured until the position is fully closed.
  """
  entries: dict[str, int] = {}
  last_exits: dict[str, int] = {}
  for t in trades:
    if t.action == "entry":
      entries[t.position_id] = t.bar_index
    elif t.action == "exit":
      last_exits[t.position_id] = max(
        last_exits.get(t.position_id, 0), t.bar_index,
      )
  total = 0
  for pid, entry_bar in entries.items():
    if pid in last_exits:
      total += last_exits[pid] - entry_bar
  return total


def _avg_trade_duration(trades: list[OrderFill]) -> float:
  """Average number of bars per completed trade.

  For multi-exit positions, duration = entry bar to LAST exit bar.
  """
  entries: dict[str, int] = {}
  last_exits: dict[str, int] = {}
  for t in trades:
    if t.action == "entry":
      entries[t.position_id] = t.bar_index
    elif t.action == "exit":
      last_exits[t.position_id] = max(
        last_exits.get(t.position_id, 0), t.bar_index,
      )
  durations = [
    last_exits[pid] - entry_bar
    for pid, entry_bar in entries.items()
    if pid in last_exits
  ]
  return float(np.mean(durations)) if durations else 0.0


def _max_simultaneous_positions(trades: list[OrderFill]) -> int:
  """Track peak concurrent open positions from entry/exit events.

  Only counts one entry and one exit per position_id so partial exits
  (e.g. TP1 at 50%) don't prematurely decrement the open count.
  """
  entry_bars: dict[str, int] = {}
  last_exit_bar: dict[str, int] = {}
  for t in trades:
    if t.action == "entry":
      entry_bars[t.position_id] = t.bar_index
    elif t.action == "exit":
      last_exit_bar[t.position_id] = max(
        last_exit_bar.get(t.position_id, 0), t.bar_index,
      )
  # Build events: +1 at entry, -1 at last exit only
  events: list[tuple[int, int]] = []
  for pid, bar in entry_bars.items():
    events.append((bar, 1))
  for pid, bar in last_exit_bar.items():
    events.append((bar, -1))
  # Sort by bar; exits before entries at same bar (conservative)
  events.sort(key=lambda e: (e[0], e[1]))
  current = 0
  peak = 0
  for _, delta in events:
    current += delta
    peak = max(peak, current)
  return peak


def _total_commission(trades: list[OrderFill], commission_pct: float) -> float:
  """Total commission from all fills (entry + exit).

  Uses recorded commission when available (accurate), falls back to
  recalculating from price*qty*pct (legacy fills without commission field).
  """
  recorded = sum(t.commission for t in trades)
  if recorded > 0:
    return recorded
  # Fallback for old fills that don't have commission recorded
  return sum(t.price * t.qty * commission_pct / 100 for t in trades)


def compute_avg_consecutive_ranges(range_history: list[int]) -> dict:
  """Compute average consecutive same-sided range counts from range_history.

  range_history is a list of 1 (bull) and -1 (bear) appended each time
  a confirmed market structure range is detected by the strategy.

  Returns avg_consec_bull, avg_consec_bear, avg_consec_all, total_ranges.
  avg_consec_all = (avg_bull + avg_bear) / 2.
  """
  if not range_history:
    return {
      "avg_consec_bull": 0.0, "avg_consec_bear": 0.0,
      "avg_consec_all": 0.0, "total_ranges": 0,
    }

  from itertools import groupby

  bull_runs: list[int] = []
  bear_runs: list[int] = []

  for direction, group in groupby(range_history):
    run_len = sum(1 for _ in group)
    if direction == 1:
      bull_runs.append(run_len)
    elif direction == -1:
      bear_runs.append(run_len)

  avg_bull = float(np.mean(bull_runs)) if bull_runs else 0.0
  avg_bear = float(np.mean(bear_runs)) if bear_runs else 0.0

  # L+S = average of the two side averages
  if avg_bull > 0 and avg_bear > 0:
    avg_all = (avg_bull + avg_bear) / 2.0
  elif avg_bull > 0:
    avg_all = avg_bull
  elif avg_bear > 0:
    avg_all = avg_bear
  else:
    avg_all = 0.0

  return {
    "avg_consec_bull": round(avg_bull, 2),
    "avg_consec_bear": round(avg_bear, 2),
    "avg_consec_all": round(avg_all, 2),
    "total_ranges": len(range_history),
  }
