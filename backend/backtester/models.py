"""
Backtester data models — shared dataclasses for positions, orders, and fills.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class OrderFill:
  """Record of a filled order."""
  bar_index: int
  timestamp: pd.Timestamp
  side: str           # "long" or "short"
  action: str         # "entry" or "exit"
  price: float
  qty: float
  label: str = ""
  pnl: float = 0.0
  position_id: str = ""  # links fill to specific position
  entry_price: float = 0.0  # set on exit fills: the position's entry price
  entry_time: pd.Timestamp | None = None  # set on exit fills: when the position was entered
  sl_price: float = 0.0  # set on exit fills: SL price at time of exit
  tp_price: float = 0.0  # set on exit fills: TP price at time of exit
  mae: float = 0.0       # Max Adverse Excursion in USDT (negative = against you)
  mfe: float = 0.0       # Max Favorable Excursion in USDT (positive = in your favor)
  commission: float = 0.0  # Round-trip commission for this exit
  sl_past_be_time: pd.Timestamp | None = None  # when trailing stop first moved past breakeven


@dataclass
class Position:
  """An open position."""
  id: str = ""        # unique ID = entry label
  side: str = ""      # "long", "short", or "" (flat)
  entry_price: float = 0.0
  qty: float = 0.0
  entry_bar: int = 0
  label: str = ""
  extreme_high: float = 0.0          # Highest price since entry (for MFE)
  extreme_low: float = float("inf")  # Lowest price since entry (for MAE)
  sl_past_be_time: pd.Timestamp | None = None  # when trailing stop first moved past breakeven
  was_injected: bool = False         # True if position was resumed via _inject_positions (L1 catch-up). Skipped in backtest_end force-close so we don't write fictional last-bar trade rows for positions that are actually still open across the L1 boundary.


@dataclass
class ExitOrder:
  """Persistent exit order — checked each bar until triggered or position closes.
  Supports stop loss, take profit, and trailing stop.
  """
  id: str = ""              # exit order identifier (e.g. "TP1", "SL1")
  from_entry: str = ""      # links to Position.id
  stop: float = 0.0         # stop loss price (0 = not set)
  limit: float = 0.0        # take profit price (0 = not set)
  trail_points: float = 0.0  # trailing stop distance in price units
  trail_offset: float = 0.0  # min profit before trail activates
  qty: float = 0.0          # 0 = close full position
  qty_percent: float = 0.0  # 1-100 = close that % of position (0 = use qty)
  # Trailing stop tracking state
  trail_extreme: float = 0.0  # best price since entry (updated each bar)
