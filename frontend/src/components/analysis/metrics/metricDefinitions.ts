/**
 * Plain-English tooltip definitions for every KPI.
 * Key = display label used in MetricRow.
 */
export const METRIC_TOOLTIPS: Record<string, string> = {
  // Row 1 — Core Performance
  "Net Profit": "Total profit/loss after commissions across all trades.",
  "Avg Trade": "Average profit/loss per trade (expectancy). Positive means each trade is worth taking on average.",
  "Positions": "Total number of completed trades. Multi-exit positions (TP1+TP2) count as one.",
  "Avg/Month": "Average number of trades per month over the backtest period.",
  "Win Rate": "Percentage of trades that closed in profit. Above 50% is generally positive.",
  "Profit Factor": "Gross profit / gross loss. Above 1.5 is good, above 2.0 is excellent.",
  "Max DD": "Maximum peak-to-trough decline in equity — the worst losing streak in dollar terms.",
  "Max DD Dur": "Duration of the worst drawdown period (from peak equity to full recovery).",

  "Gross Profit": "Sum of all winning trades — total earned before deducting losses.",
  "Gross Loss": "Sum of all losing trades (shown as negative). Total given back to the market.",
  "Stability": "R-squared of equity curve. 1.0 = perfectly linear growth. Above 0.9 is excellent.",

  // Row 2 — System Quality
  "SQN": "System Quality Number (Van Tharp). 2+ Good, 3+ Excellent, 5+ Superb, 7+ Holy Grail.",
  "Recovery": "Net profit divided by max drawdown. Higher = recovers faster from losses.",
  "Ulcer Index": "Measures depth and duration of drawdowns. Lower is better. 0 = no drawdowns.",
  "Avg DD": "Average dollar drawdown across all drawdown periods.",
  "Avg DD Dur": "Average duration of all drawdown periods.",

  // Row 3 — Risk-Adjusted
  "Sortino": "Risk-adjusted return penalizing only downside volatility. Higher is better. Above 2 is excellent.",
  "Calmar": "Annualized return / max drawdown. Above 1 means yearly profit > worst loss.",
  "Best Run-up": "Largest equity gain from a trough to the next peak.",
  "Run-up Dur": "Duration of the longest equity run-up period.",
  "Avg Run-up": "Average equity gain across all run-up cycles.",
  "Avg RU Dur": "Average duration of all equity run-up periods.",

  // Row 4 — Trade Distribution
  "Max Win": "Largest single winning trade in dollar terms.",
  "Avg Win": "Average profit on winning trades.",
  "Avg Win Dur": "Average duration of winning trades (entry to exit).",
  "Max Loss": "Largest single losing trade in dollar terms.",
  "Avg Loss": "Average loss on losing trades.",
  "Avg Loss Dur": "Average duration of losing trades (entry to exit).",
  "W/L Ratio": "Average win / average loss. Above 1 means wins are bigger than losses.",
  "Consec Wins": "Longest streak of consecutive winning trades.",
  "Consec Losses": "Longest streak of consecutive losing trades.",

  // Row 5 — Exit Reason Breakdown
  "TSL WR": "Win rate for trades that exited via trailing stop loss.",
  "Avg Win TP": "Average profit on trades that exited via take-profit.",
  "Avg Win TSL": "Average profit on trades that exited via trailing stop in profit.",
  "TP Exits": "Number of trades that exited via take-profit.",
  "TSL Exits": "Number of trades that exited via trailing stop loss.",
  "SL Exits": "Number of trades that exited via stop loss.",

  // Row 6 — Trade Excursion
  "MAE Worst": "Max Adverse Excursion — deepest intra-trade drawdown across all trades.",
  "Avg MAE": "Average max adverse excursion (worst point during each trade, then averaged).",
  "MFE Best": "Max Favorable Excursion — highest intra-trade run-up across all trades.",
  "Avg MFE": "Average max favorable excursion (best point during each trade, then averaged).",
  "Comm. Drag": "Commission cost as percentage of total net profit.",

  // Row 7 — Statistical Significance
  "T-Stat": "T-statistic measures if average trade profit is significantly different from zero.",
  "N Assets": "Number of unique assets (symbols) in this portfolio.",
  "Long Assets": "Number of assets traded on the long side.",
  "Short Assets": "Number of assets traded on the short side.",
  "Max Simult.": "Peak number of positions open at the same time. Click for details.",
  "Max Open Net": "Peak concurrent positions EXCLUDING TP1 exits. After TP1 hits, that margin is freed — this shows true capital demand.",
  "Peak Margin": "Estimated peak margin (capital) tied up at the moment of maximum concurrent positions.",
  "Peak Margin Net": "Peak margin after crediting back TP1 exits. Shows true max capital needed.",
  "Avg Tr/Asset": "Average number of trades per asset in this portfolio.",

  // Row 8 — Correlation Distribution
  "Corr p95": "95th percentile of pairwise correlations. Below 0.5 is good diversification.",
  "Corr p80": "80th percentile of pairwise correlations.",
  "Corr Median": "Median (50th percentile) of pairwise correlations. Closer to 0 is better.",
  "Corr p25": "25th percentile of pairwise correlations.",
  "Corr Mean": "Average of all pairwise correlations between portfolio assets.",
};
