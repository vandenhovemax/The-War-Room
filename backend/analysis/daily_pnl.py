"""Convert BacktestTrade records into a daily PnL matrix for correlation analysis."""
from __future__ import annotations

import pandas as pd

from backend.db.models import BacktestTrade


def _asset_key(t: BacktestTrade, include_side: bool = False) -> str:
  """Return the column name for a trade: raw symbol or SYMBOL_Long/SYMBOL_Short."""
  if include_side:
    return f"{t.symbol}_{(t.side or 'Long').title()}"
  return t.symbol


def build_daily_pnl_matrix(
  trades: list[BacktestTrade],
  include_side: bool = False,
) -> pd.DataFrame:
  """Build a daily PnL matrix (index=Date, columns=Symbol).

  Each cell = sum of net_profit for that symbol on that day.
  Non-trading days are filled with 0 (standard for portfolio correlation).

  include_side: when True, columns become SYMBOL_Long / SYMBOL_Short
  (required for split L/S optimization and cross-strategy).
  """
  if not trades:
    return pd.DataFrame()

  rows = [
    {
      "date": pd.Timestamp(t.exit_time).floor("D"),
      "symbol": _asset_key(t, include_side),
      "pnl": t.net_profit,
    }
    for t in trades
    if t.exit_time and t.net_profit is not None
  ]
  if not rows:
    return pd.DataFrame()

  df = pd.DataFrame(rows)
  daily = df.pivot_table(
    index="date", columns="symbol", values="pnl", aggfunc="sum",
  ).fillna(0)

  # Fill to full date range so correlation sees every day
  if not daily.empty:
    full_range = pd.date_range(
      start=daily.index.min(), end=daily.index.max(), freq="D",
    )
    daily = daily.reindex(full_range).fillna(0)

  return daily
