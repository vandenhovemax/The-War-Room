"""Monte Carlo simulation — bootstrap trade sequences to estimate risk.

Ported from the Streamlit Market Structure Dashboard optimizer.py.
"""
from __future__ import annotations

import numpy as np


def monte_carlo_simulation(
  trades_pnl: np.ndarray | list[float],
  n_simulations: int = 1000,
  initial_capital: float = 10000.0,
  ruin_threshold_pct: float = 50.0,
  trades_per_year: float = 365.0,
) -> dict | None:
  """Bootstrap Monte Carlo over trade PnLs.

  Returns dict with:
    - stats: summary metrics (median return, prob profit, etc.)
    - cone: {5: [...], 25: [...], 50: [...], 75: [...], 95: [...]}
    - original_equity: cumulative equity from actual trade order
  Returns None if no trades.
  """
  pnl = np.asarray(trades_pnl, dtype=float)
  n_trades = len(pnl)

  if n_trades == 0:
    return None

  original_equity = np.cumsum(pnl)

  # Pre-allocate simulation paths
  sim_paths = np.zeros((n_simulations, n_trades))
  end_equities = np.zeros(n_simulations)
  max_drawdowns = np.zeros(n_simulations)
  max_dd_pcts = np.zeros(n_simulations)
  sharpe_ratios = np.zeros(n_simulations)

  for i in range(n_simulations):
    sim_pnl = np.random.choice(pnl, size=n_trades, replace=True)
    sim_equity = np.cumsum(sim_pnl)
    sim_paths[i] = sim_equity

    end_equities[i] = sim_equity[-1]

    # Max drawdown (dollar terms)
    peak = np.maximum.accumulate(sim_equity)
    dd = sim_equity - peak
    max_drawdowns[i] = np.min(dd)

    # Max drawdown as % of peak account equity (not initial capital)
    account_eq = initial_capital + sim_equity
    account_peak = np.maximum.accumulate(account_eq)
    dd_pct = np.where(account_peak > 0, (account_eq - account_peak) / account_peak * 100, 0.0)
    max_dd_pcts[i] = np.min(dd_pct)  # most negative percentage

    # Annualised Sharpe (trade-level)
    std = np.std(sim_pnl)
    if std > 0:
      sharpe_ratios[i] = np.mean(sim_pnl) / std * np.sqrt(trades_per_year)

  # Cone percentiles
  percentiles = [5, 25, 50, 75, 95]
  cone: dict[int, list[float]] = {}
  for p in percentiles:
    cone[p] = np.percentile(sim_paths, p, axis=0).tolist()

  # Original max DD (dollar and %)
  orig_peak = np.maximum.accumulate(original_equity)
  orig_max_dd = float(np.min(original_equity - orig_peak))
  orig_account = initial_capital + original_equity
  orig_account_peak = np.maximum.accumulate(orig_account)
  orig_dd_pct = np.where(
    orig_account_peak > 0,
    (orig_account - orig_account_peak) / orig_account_peak * 100, 0.0,
  )
  orig_max_dd_pct = float(np.min(orig_dd_pct))

  # Probability of profit
  prob_profit = float(np.sum(end_equities > 0) / n_simulations * 100)

  # Probability of ruin (DD exceeds user-defined % of account equity peak)
  ruin_frac = ruin_threshold_pct / 100.0
  ruin_count = 0
  for i in range(n_simulations):
    account_eq = initial_capital + sim_paths[i]
    peak = np.maximum.accumulate(account_eq)
    dd = account_eq - peak
    if np.any(np.abs(dd) > peak * ruin_frac):
      ruin_count += 1
  prob_ruin = float(ruin_count / n_simulations * 100)

  stats = {
    "median_return": float(np.median(end_equities)),
    "worst_case_return": float(np.percentile(end_equities, 5)),
    "best_case_return": float(np.percentile(end_equities, 95)),
    "prob_profit": prob_profit,
    "prob_ruin": prob_ruin,
    "median_max_dd": float(np.median(max_drawdowns)),
    "worst_case_max_dd": float(np.percentile(max_drawdowns, 5)),
    "best_case_max_dd": float(np.percentile(max_drawdowns, 95)),
    "median_max_dd_pct": float(np.median(max_dd_pcts)),
    "worst_case_max_dd_pct": float(np.percentile(max_dd_pcts, 5)),
    "median_sharpe": float(np.median(sharpe_ratios)),
    "sharpe_5th": float(np.percentile(sharpe_ratios, 5)),
    "sharpe_95th": float(np.percentile(sharpe_ratios, 95)),
    "original_max_dd": orig_max_dd,
    "original_max_dd_pct": orig_max_dd_pct,
    "original_return": float(original_equity[-1]),
    "n_trades": n_trades,
    "n_simulations": n_simulations,
    "ruin_threshold_pct": ruin_threshold_pct,
  }

  return {
    "stats": stats,
    "cone": cone,
    "original_equity": original_equity.tolist(),
  }
