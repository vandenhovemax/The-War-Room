/** Canonical objective & correlation lists — shared across WFO + CrossBuilder. */

/** @deprecated Legacy 3-item subset — kept for backward compat with saved configs. Use ALL_OBJECTIVES instead. */
export const WITHIN_OBJECTIVES = [
  { value: "uncorrelated", label: "Uncorrelated", desc: "Pick assets that move independently — when one drops, others don't follow." },
  { value: "anti_correlated", label: "Anti-Correlated", desc: "Pick assets that move in opposite directions — natural hedges." },
  { value: "hybrid", label: "Smart Balance", desc: "Balance diversification with individual performance. Good default." },
];

/** Full objective list — used for Layer 2/3 + combined/global objectives. */
export const ALL_OBJECTIVES = [
  { value: "stability", label: "Stability (Equity Quality)", desc: "Smoothest equity curve — steady growth with small dips. BEST FOR: Most users. Produces the most reliable, walk-forward-friendly portfolios. USE WHEN: You want consistency above all else. RECOMMENDED default." },
  { value: "hybrid", label: "Smart Balance", desc: "Balances diversification with individual performance. USE WHEN: You want good diversity AND good returns — a middle ground. Slightly more aggressive than Stability but still diversified." },
  { value: "uncorrelated", label: "Uncorrelated (Diversity)", desc: "Maximum diversification — picks assets that move independently. USE WHEN: You have many correlated assets and want the optimizer to prioritize independence. Good for large pools where correlation is a problem." },
  { value: "anti_correlated", label: "Anti-Correlated (Hedge)", desc: "Picks assets that move in opposite directions — natural hedges. USE WHEN: You want maximum drawdown protection through hedging. Portfolio may sacrifice total return for smoother equity." },
  { value: "min_max_drawdown", label: "Min Drawdown", desc: "Smallest worst-case drop. USE WHEN: Capital preservation is #1. You'd rather make less money than risk a big drawdown. Best for conservative / low-risk profiles." },
  { value: "max_sortino", label: "Max Sortino", desc: "Best return per unit of downside risk. USE WHEN: You want high returns and tolerate upside volatility — only punishes drops. RECOMMENDED for aggressive traders who still want risk management." },
  { value: "max_sharpe", label: "Max Sharpe", desc: "Best return per unit of total volatility. USE WHEN: You want a smooth, steady ride with minimal swings in either direction. More conservative than Sortino because it penalizes upside moves too." },
  { value: "max_calmar", label: "Max Calmar", desc: "Best annual return ÷ worst drawdown. USE WHEN: You want high returns but refuse to accept deep drawdowns. Good for 'best of both worlds' — growth + protection." },
  { value: "max_recovery", label: "Max Recovery", desc: "Highest profit ÷ max drawdown. USE WHEN: You can tolerate drawdowns but need them to be earned back quickly. Favors portfolios that bounce back fast." },
  { value: "min_ulcer", label: "Min Ulcer Index", desc: "Least painful to hold — penalizes deep AND long-lasting drawdowns. USE WHEN: Long drawdowns stress you out as much as deep ones. Good for 'hold and forget' portfolios." },
  { value: "max_profit_factor", label: "Max Profit Factor", desc: "Highest ratio of total wins to total losses. USE WHEN: You want simple, robust profitability. Easy to understand and hard to overfit. Good for strategies with many trades." },
  { value: "max_expectancy", label: "Max Expectancy", desc: "Highest average profit per trade. USE WHEN: You run a strategy with consistent trade frequency and want to maximize edge per trade." },
  { value: "max_payoff", label: "Max Payoff Ratio", desc: "Biggest wins relative to losses. USE WHEN: You run trend-following and want portfolios with large winners vs small losers. Best with TSL/trailing stops." },
  { value: "min_lake_ratio", label: "Min Lake Ratio", desc: "Least time spent in drawdown. USE WHEN: You want equity that stays near all-time highs most of the time. Complements Stability well." },
  { value: "max_tail_ratio", label: "Max Tail Ratio", desc: "Big wins, small losses — positive skew. USE WHEN: You want asymmetric outcomes where the best days far exceed the worst days. Good for trend-following portfolios." },
  { value: "max_gain_pain", label: "Max Gain/Pain", desc: "Total gains ÷ total pain. USE WHEN: Few trades per asset — works better than Sharpe/Sortino with small samples. Simple and robust." },
  { value: "max_cde", label: "Max CDE", desc: "Controls the worst drawdowns. USE WHEN: You want to catch portfolios that look good on average but have ugly tail events. More thorough than Max DD — averages multiple bad drawdowns." },
  { value: "max_robust_sortino", label: "Max Robust Sortino", desc: "Sortino adjusted for extreme moves. USE WHEN: You trade crypto with wild price swings — more reliable than plain Sortino for fat-tailed distributions." },
  { value: "max_est_efficiency", label: "Max EST Efficiency", desc: "Time-to-return efficiency. USE WHEN: You want portfolios that don't waste time — every day of holding generates meaningful risk-adjusted return." },
  { value: "max_rapid_recovery", label: "Max Rapid Recovery", desc: "Fastest bounce-back from drawdowns. USE WHEN: You can handle short dips but need V-shaped recoveries. Favors resilient, quick-bouncing portfolios." },
  // Structural quality objectives
  { value: "max_gpr", label: "Max GPR", desc: "Gain-to-Pain on portfolio equity. USE WHEN: You want smooth, consistent growth measured at the portfolio level. Good alternative to Stability." },
  { value: "max_ter", label: "Max TER", desc: "Straightest equity curve. USE WHEN: You run trend-following and want the cleanest upward equity movement. Penalizes choppy equity curves." },
  { value: "max_keep_score", label: "Max KeepScore", desc: "Combined trend quality score. USE WHEN: You run a trend strategy and want the portfolio that trends most cleanly overall. One-number summary of trend fitness." },
  { value: "max_mr_score", label: "Max MR Score", desc: "Combined mean-reversion score. USE WHEN: You run a mean-reversion strategy — picks portfolios that mean-revert most reliably." },
  { value: "max_crossing_rate", label: "Max Crossing Rate", desc: "Most equity crossings of average. USE WHEN: You want frequent mean-reversion signals in the portfolio equity. Best for mean-rev strategy types." },
];

/** Correlation methods (2). */
export const CORRELATION_METHODS = [
  { value: "standard", label: "Standard", desc: "Measures how assets move together on every day. Most common method." },
  { value: "trade_overlap", label: "Trade Overlap", desc: "Only compares days when both assets had open trades. Better for strategies that trade infrequently." },
];

/** Diversification algorithms (9). */
export const DIVERSIFICATION_ALGOS = [
  { value: "greedy", label: "Iterative (Objective-Driven)", desc: "Starts with the best asset, adds whichever improves the objective most, one at a time. BEST FOR: Most users — fast, reliable, and responds to all settings (seeds, correlation, overlap). USE WHEN: You want full control over the process. RECOMMENDED default. Fast enough for quick iteration." },
  { value: "hrp", label: "HRP (Risk Parity)", desc: "Hierarchical Risk Parity — clusters similar assets, then picks across clusters for maximum spread. USE WHEN: You have many correlated assets (e.g., similar altcoins) and want guaranteed diversity. Works well with 30+ assets in the pool. Less responsive to seed rankings." },
  { value: "pca", label: "PCA (Factor Clustering)", desc: "Finds independent risk factors (like 'BTC-correlated' vs 'alt-season') and picks one asset per factor. USE WHEN: You want maximum statistical independence. Best for large pools where hidden correlations exist. SKIP WHEN: Few assets (<15) — PCA needs enough data to find meaningful factors." },
  { value: "quality_first", label: "Quality First", desc: "Starts with the top-quality assets, then removes the most correlated pair until target size. USE WHEN: You prioritize individual asset quality over diversity. Good when your seeds include strong assets you don't want to miss. Quality > diversity." },
  { value: "tournament", label: "Tournament Draft", desc: "Runs hundreds of random drafts, each balancing quality and diversity, picks the best one. USE WHEN: You want robustness — less sensitive to which single asset is 'best'. Good when top assets are close in quality. Slower than Greedy but more thorough." },
  { value: "genetic", label: "Genetic Algorithm", desc: "Evolves portfolios over generations using crossover and mutation. USE WHEN: Large pool (30+ assets) with many possible combinations. Explores the widest search space. Slower but finds combinations other algos miss. Best paired with OG guards to prevent overfitting to noise." },
  { value: "marginal_sharpe", label: "Marginal Sharpe", desc: "Adds the asset with the highest marginal risk-adjusted contribution at each step. USE WHEN: You want a pure math-driven approach with no tuning knobs. One formula, one answer. Fastest algo. SKIP WHEN: You want seed rankings to influence the result (this algo mostly ignores seeds)." },
  { value: "ensemble", label: "Ensemble Consensus", desc: "Runs Greedy + HRP + Marginal Sharpe internally, keeps assets that multiple methods agree on. USE WHEN: You want maximum confidence — if 3 different methods all pick an asset, it's probably good. Most robust single algo. Slightly slower (runs 3x)." },
  { value: "walk_forward", label: "Walk-Forward Validated", desc: "Builds portfolios on 70% of data, validates on 30% held-out data. Picks the portfolio that holds up best on unseen data. USE WHEN: Overfitting is your main concern. Gold standard for real-world robustness. Slower but produces the most trustworthy results. Best combined with Stability objective." },
];
