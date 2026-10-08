"""Shared portfolio metrics — single source of truth.

Every view that shows portfolio KPIs (optimizer candidates, saved portfolio
details, ISvsLivePanel, OOS analysis) MUST call `compute_portfolio_metrics()`
from this module.  No other file should reimplement these calculations.

Two entry points:
  compute_portfolio_metrics(daily_pnl, assets, trade_df)
      Used by the optimizer and any code that already has a daily PnL matrix
      and (optionally) a trade-level DataFrame with position_id.

  compute_portfolio_metrics_from_trades(trade_df)
      Convenience wrapper for the analysis/portfolio-detail view which only
      has a position-merged trade DataFrame (from trades_from_db).  Builds
      the daily PnL internally and then delegates to the main function.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.scoring import calculate_stability
from backend.utils.sanitize import sanitize_for_json


def compute_portfolio_metrics(
  daily_pnl: pd.DataFrame,
  assets: list[str],
  trade_df: pd.DataFrame | None = None,
) -> dict:
  """Compute standard portfolio metrics for a set of assets.

  Expectancy metrics (PF, Win Rate, Avg Trade, Net Profit) are calculated
  at trade-level resolution when trade_df is available, merging multi-exit
  fills by position_id into positions.
  Time-series metrics (drawdown, stability, sortino, calmar, recovery) use
  daily PnL which is the standard approach in portfolio theory.

  Parameters
  ----------
  daily_pnl : DataFrame
      Daily PnL matrix with one column per asset (date index).
  assets : list[str]
      Which columns to include.
  trade_df : DataFrame, optional
      Columns: [Asset, Net Profit, position_id].  When provided, expectancy
      metrics are computed from trades grouped by (Asset, position_id).
  """
  cols = [c for c in assets if c in daily_pnl.columns]
  if not cols:
    return {
      "net_profit": 0, "profit_factor": 0, "max_drawdown": 0,
      "stability": 0, "n_assets": 0, "win_rate": 0, "total_trades": 0,
      "avg_trade": 0, "gross_profit": 0, "gross_loss": 0,
      "sortino": 0, "calmar_ratio": 0, "recovery_factor": 0,
    }

  combined = daily_pnl[cols].sum(axis=1)
  equity = combined.cumsum()
  dd = float((equity - equity.cummax()).min())
  stab = float(calculate_stability(combined))

  # --- Expectancy metrics from trade-level data (position-merged) ---
  net = 0.0
  pf = 0.0
  gp = 0.0
  gl = 0.0
  total_trades = 0
  win_rate = 0.0
  avg_trade = 0.0

  if trade_df is not None and not trade_df.empty:
    ptf = trade_df[trade_df["Asset"].isin(cols)]
    if not ptf.empty and "position_id" in ptf.columns:
      pos_pnl = ptf.groupby(["Asset", "position_id"])["Net Profit"].sum()
      total_trades = len(pos_pnl)
      net = float(pos_pnl.sum())
      gp = float(pos_pnl[pos_pnl > 0].sum())
      gl = abs(float(pos_pnl[pos_pnl <= 0].sum()))
      pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)
      if total_trades > 0:
        wins = int((pos_pnl > 0).sum())
        win_rate = round(wins / total_trades * 100, 1)
        avg_trade = round(net / total_trades, 2)
    elif not ptf.empty:
      # Fallback: no position_id — count rows as trades
      total_trades = len(ptf)
      net = float(ptf["Net Profit"].sum())
      gp = float(ptf.loc[ptf["Net Profit"] > 0, "Net Profit"].sum())
      gl = abs(float(ptf.loc[ptf["Net Profit"] <= 0, "Net Profit"].sum()))
      pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)
      if total_trades > 0:
        wins = int((ptf["Net Profit"] > 0).sum())
        win_rate = round(wins / total_trades * 100, 1)
        avg_trade = round(net / total_trades, 2)

  if total_trades == 0:
    # No trade data — fall back to daily PnL for net profit and PF
    net = float(combined.sum())
    gp = float(combined[combined > 0].sum())
    gl = float(abs(combined[combined < 0].sum()))
    pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)

  # --- Time-series metrics from daily PnL (standard approach) ---
  neg = combined[combined < 0]
  downside_dev = float(np.sqrt(np.mean(neg.values ** 2))) if len(neg) > 0 else 0.0
  daily_mean = float(combined.mean()) if len(combined) > 0 else 0.0
  # Annualize using 365 for crypto
  sortino = round(daily_mean / downside_dev * (365 ** 0.5), 2) if downside_dev > 0 else 0.0

  # Calmar = annualized return / |max DD|
  annual_ret = daily_mean * 365
  calmar = round(annual_ret / abs(dd), 2) if dd < 0 else 0.0

  # Recovery factor = net profit / |max DD|
  recovery = round(net / abs(dd), 2) if dd < 0 else 0.0

  return sanitize_for_json({
    "net_profit": round(net, 2),
    "profit_factor": pf,
    "max_drawdown": round(dd, 2),
    "stability": round(stab, 4),
    "n_assets": len(cols),
    "win_rate": win_rate,
    "total_trades": total_trades,
    "avg_trade": avg_trade,
    "gross_profit": round(gp, 2),
    "gross_loss": round(-gl, 2),
    "sortino": sortino,
    "calmar_ratio": calmar,
    "recovery_factor": recovery,
  })


def compute_portfolio_metrics_from_trades(
  df: pd.DataFrame,
) -> dict:
  """Compute portfolio metrics from a position-merged trade DataFrame.

  This is the entry point for the portfolio-detail view which receives
  trades from trades_from_db() (already position-merged).

  Builds a daily PnL matrix internally and delegates to
  compute_portfolio_metrics() for consistent results.
  """
  if df.empty:
    return {}

  # Ensure timestamps are parsed
  if "Exit Time" in df.columns:
    df = df.copy()
    df["Exit Time"] = pd.to_datetime(df["Exit Time"])

  # Build daily PnL matrix (same logic as _portfolio_metrics uses)
  assets = df["Asset"].unique().tolist() if "Asset" in df.columns else []
  if not assets:
    return {}

  # Build pivot: date x asset → daily PnL
  daily_rows = []
  for _, row in df.iterrows():
    if pd.notna(row.get("Exit Time")) and row.get("Net Profit") is not None:
      daily_rows.append({
        "date": pd.Timestamp(row["Exit Time"]).floor("D"),
        "symbol": row["Asset"],
        "pnl": row["Net Profit"],
      })
  if not daily_rows:
    return {}

  raw = pd.DataFrame(daily_rows)
  daily_pnl = raw.pivot_table(
    index="date", columns="symbol", values="pnl", aggfunc="sum",
  ).fillna(0)

  if not daily_pnl.empty:
    full_range = pd.date_range(
      start=daily_pnl.index.min(), end=daily_pnl.index.max(), freq="D",
    )
    daily_pnl = daily_pnl.reindex(full_range).fillna(0)

  # Build trade_df for position-level metrics
  # trades_from_db already merged by position_id, so each row = 1 position.
  # We still pass it through with a synthetic position_id so the function
  # does the right thing (each row is its own position).
  trade_df = df[["Asset", "Net Profit"]].copy()
  trade_df["position_id"] = range(len(trade_df))

  return compute_portfolio_metrics(daily_pnl, assets, trade_df)
