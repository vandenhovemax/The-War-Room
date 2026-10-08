/**
 * Shared constants and helpers for the Settings Recommender.
 * Used by both SettingsRecommender.tsx (live panel) and SavedRunsTab.tsx (saved detail view).
 */

import { ALL_OBJECTIVES } from "./objectiveConstants";

/** Algorithm metadata — display names and tooltips. */
export const ALGO_INFO: Record<string, { display: string; tip: string }> = {
  greedy: { display: "Greedy", tip: "Starts with the best asset, then adds whichever improves the objective most at each step. Fast, reliable, most customisable." },
  hrp: { display: "HRP", tip: "Hierarchical Risk Parity — clusters correlated assets and allocates risk across clusters." },
  pca: { display: "PCA", tip: "Factor Clustering — groups assets by principal components to maximise factor diversification." },
  quality_first: { display: "Quality First", tip: "Ranks assets by individual quality metrics first, then drops correlated duplicates." },
  tournament: { display: "Tournament", tip: "Random draft sampling for broad diversity across the pool." },
  marginal_sharpe: { display: "Marginal Sharpe", tip: "Adds assets based on their marginal risk-adjusted contribution to the portfolio." },
  ensemble: { display: "Ensemble", tip: "Runs multiple methods and combines their consensus for robust selection." },
  walk_forward: { display: "Walk-Forward", tip: "Validates selections on a held-out period to check robustness before finalising." },
};

/** Human-readable labels for backend setting keys. */
export const SETTING_LABELS: Record<string, string> = {
  div_algo: "Algorithm",
  objective: "Objective",
  seed_prior_weight: "Seed Prior Weight",
  seed_metrics: "Seed Metrics",
  guard_preset: "Guard Preset",
  min_pf: "Min Profit Factor",
  min_trades: "Min Trades",
  min_wr: "Min Win Rate",
  overfit_guard_enabled: "Overfit Guard",
  of_log_enabled: "Log Cap Guard",
  of_log_cap_pf: "Log Cap PF",
  of_z_score_enabled: "Z-Score Guard",
  of_z_threshold: "Z-Score Threshold",
  of_z_penalty_strength: "Z-Score Penalty",
  of_bayes_enabled: "Bayesian Guard",
  of_bayes_k: "Bayes K",
  of_sigmoid_enabled: "Sigmoid Guard",
  of_normal_range_enabled: "Normal Range Guard",
  of_nr_pct_low: "NR Low %",
  of_nr_pct_high: "NR High %",
  of_nr_penalty_scale: "NR Penalty Scale",
  of_nr_bonus: "NR Bonus",
  of_wf_rerank_enabled: "WF Re-rank Guard",
  of_wf_split_pct: "WF Split %",
  of_wf_validation_metric: "WF Validation Metric",
  refinement_enabled: "Refinement",
  refinement_mode: "Refinement Mode",
  corr_jitter_enabled: "Correlation Jitter",
  corr_jitter_temperature: "Jitter Temperature",
  max_asset_pf: "Max Asset PF",
  cap_asset_pf: "Cap Asset PF",
  portfolio_min_pf: "Portfolio Min PF",
  portfolio_min_wr: "Portfolio Min WR",
};

/** Format any setting value for display. */
export function formatSettingValue(v: unknown): string {
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) return v.join(", ");
  if (v === null || v === undefined) return "—";
  return String(v);
}

/** Display a recommendation value using friendly labels instead of raw keys. */
export function friendlySettingValue(setting: string, value: unknown): string {
  if (setting === "div_algo") return ALGO_INFO[String(value)]?.display || String(value);
  if (setting === "objective") {
    const obj = ALL_OBJECTIVES.find(o => o.value === String(value));
    return obj?.label || String(value);
  }
  if (setting === "refinement_mode") {
    const modes: Record<string, string> = { prune_and_swap: "Prune & Swap", prune_only: "Prune Only", swap_only: "Swap Only" };
    return modes[String(value)] || String(value);
  }
  if (setting === "of_wf_validation_metric") {
    const obj = ALL_OBJECTIVES.find(o => o.value === String(value));
    return obj?.label || String(value);
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

/** Human-readable labels for pool profile keys. */
export const PROFILE_LABELS: Record<string, string> = {
  n_assets: "Assets",
  n_batches: "Batches",
  n_longs: "Longs",
  n_shorts: "Shorts",
  trade_count_median: "Median Trades",
  trade_count_mean: "Mean Trades",
  trade_count_min: "Min Trades",
  trade_count_max: "Max Trades",
  trade_count_p25: "Trades P25",
  trade_count_p75: "Trades P75",
  pct_below_10_trades: "< 10 Trades %",
  pct_below_20_trades: "< 20 Trades %",
  corr_mean_abs: "Avg |Corr|",
  corr_median_abs: "Median |Corr|",
  corr_p90_abs: "|Corr| P90",
  corr_density_above_50: "|Corr| > 0.50 %",
  corr_density_above_30: "|Corr| > 0.30 %",
  pf_spread: "PF Spread",
  quality_gini: "Quality Gini",
  batch_balance_ratio: "Batch Balance",
  n_suspicious: "Suspicious",
};
