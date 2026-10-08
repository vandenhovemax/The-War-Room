"use client";
import HintTip from "@/components/common/HintTip";
import UserNote from "./UserNote";

/** Seed metric categories — grouped by what they measure. */
export const SEED_CATEGORIES: { group: string; hint: string; metrics: { value: string; label: string; desc: string }[] }[] = [
  {
    group: "Core Performance",
    hint: "Basic profit and loss metrics — start here",
    metrics: [
      { value: "pnl", label: "Net PnL", desc: "Total dollar profit after all trades. USE WHEN: You want the most profitable assets regardless of risk. Simple and intuitive but ignores drawdowns — an asset that made $10k with a 90% drop still ranks high." },
      { value: "pf", label: "PF", desc: "Profit Factor — total wins ÷ total losses. Above 1.5 = solid. USE WHEN: You want consistently profitable assets. Best general-purpose seed — balances wins and losses naturally. Good default choice for most strategies." },
      { value: "wr", label: "Win Rate", desc: "% of trades that made money. USE WHEN: You run a high-frequency strategy with many small trades and want consistency. CAUTION: High WR with tiny wins can still lose overall — always pair with Payoff or PF." },
      { value: "expectancy", label: "Expectancy", desc: "Average dollar profit per trade. USE WHEN: You want to know the edge per trade. Good for comparing strategies with different trade frequencies. Positive = the system has an edge." },
      { value: "payoff", label: "Payoff", desc: "Average win ÷ average loss. USE WHEN: You run a trend-following strategy where you want big winners vs small losers. Pairs well with Win Rate — they trade off against each other." },
    ],
  },
  {
    group: "Risk-Adjusted Return",
    hint: "Return per unit of risk — best for ranking quality",
    metrics: [
      { value: "sortino", label: "Sortino", desc: "Return ÷ downside risk only. Ignores upside volatility (which is good). USE WHEN: Your strategy has volatile but upward moves — Sortino won't penalize those. Best risk-adjusted metric for crypto. RECOMMENDED as a default seed." },
      { value: "sharpe", label: "Sharpe", desc: "Return ÷ total volatility. USE WHEN: You want a smooth, steady equity curve with minimal swings in either direction. CAUTION: Penalizes upside volatility equally — a big winning day hurts the score." },
      { value: "calmar", label: "Calmar", desc: "Annual return ÷ worst drawdown. USE WHEN: You care most about avoiding deep drawdowns. Directly measures 'return per unit of pain'. Good for capital preservation strategies." },
      { value: "robust_sortino", label: "Robust Sortino", desc: "Sortino adjusted for extreme price moves. USE WHEN: You trade crypto with wild swings — more reliable than plain Sortino because it handles fat tails. Slightly more conservative rankings." },
      { value: "gain_pain", label: "Gain/Pain", desc: "Total gains ÷ total losses. USE WHEN: You have few trades per asset (<20) — simpler math makes it more stable than Sharpe/Sortino with small samples." },
      { value: "gpr", label: "GPR", desc: "Gain-to-Pain on the equity curve. USE WHEN: You want a portfolio-level view of smooth growth. Similar to Gain/Pain but computed on daily equity, not individual trades." },
    ],
  },
  {
    group: "Drawdown & Recovery",
    hint: "Capital protection — use if drawdown control is your priority",
    metrics: [
      { value: "max_dd", label: "Max DD", desc: "Worst peak-to-trough drop. USE WHEN: Capital preservation is #1 priority. Ranks assets by their worst-case scenario. CAUTION: Very conservative — may avoid high-return assets just because they had one bad dip." },
      { value: "avg_dd", label: "Avg DD", desc: "Average drawdown size. USE WHEN: You want a consistently smooth ride, not just avoiding the worst case. Better than Max DD for overall comfort." },
      { value: "ulcer", label: "Ulcer", desc: "Measures both depth AND duration of drawdowns. USE WHEN: Long drawdowns bother you as much as deep ones — e.g., a 5% drawdown lasting 3 months is worse than 10% lasting 1 week." },
      { value: "recovery", label: "Recovery", desc: "Net profit ÷ max drawdown. USE WHEN: You want assets that earn back losses quickly. Higher = more resilient. Good companion to Max DD." },
      { value: "lake_ratio", label: "Lake Ratio", desc: "Time spent in drawdown. USE WHEN: You want equity that stays near all-time highs. Low Lake Ratio = most of the time you're at or near peak equity." },
      { value: "cde", label: "CDE", desc: "Average of the worst drawdowns. USE WHEN: You want to catch assets that look good on average but have ugly tail events. Better than Max DD for finding consistent losers." },
      { value: "rapid_recovery", label: "Rapid Recovery", desc: "How fast drawdowns bounce back. USE WHEN: You can tolerate drawdowns but need them to be short-lived. Favors V-shaped recoveries over slow grinds back." },
    ],
  },
  {
    group: "Robustness & Stability",
    hint: "Is the edge real? — use to filter out noise and overfitting",
    metrics: [
      { value: "stability", label: "Stability", desc: "Equity curve smoothness — combines straightness, drawdown control, and consistency. USE WHEN: You want the most reliable, boring-in-a-good-way assets. RECOMMENDED for most users as a primary or secondary seed." },
      { value: "wfe", label: "Equity Smoothness", desc: "R² straightness + drawdown + consistency combined. USE WHEN: Similar to Stability but with more weight on equity curve straightness. Good for trend-following portfolios." },
      { value: "is_wfe", label: "IS-WFE", desc: "Walk-Forward Efficiency — splits data into IS/OOS and measures how well the edge survives. USE WHEN: You suspect overfitting. Closer to 1.0 = real edge. Below 0.5 = likely overfitted. BEST single metric for detecting fake edges. Requires enough trades to split meaningfully." },
      { value: "dsr", label: "DSR", desc: "Deflated Sharpe — adjusts Sharpe for multiple testing. USE WHEN: You've tested many assets and want to know which edges are real vs lucky. Above 0.95 = strong. Below 0.50 = likely noise." },
      { value: "benchmark", label: "Benchmark", desc: "Uses Benchmark Robustness grades to rank assets. USE WHEN: You have the Benchmark panel enabled and want the most thoroughly validated assets. Requires Benchmark to be computed first." },
      { value: "recency", label: "Recency", desc: "Weights recent trades heavier. USE WHEN: Markets change and you want assets performing well NOW, not just historically. Best combined with another seed (e.g., Sortino + Recency)." },
      { value: "est_efficiency", label: "EST Efficiency", desc: "Time-to-return efficiency. USE WHEN: You want assets that don't waste time — they convert every day of holding into meaningful returns." },
      { value: "tail_ratio", label: "Tail Ratio", desc: "Best outcomes ÷ worst outcomes (positive skew). USE WHEN: You want trend-following assets with big wins and small losses. High tail ratio = favorable asymmetry." },
    ],
  },
  {
    group: "Trend Suitability",
    hint: "OHLCV-based — pick if you run a trend-following strategy",
    metrics: [
      { value: "ter", label: "TER", desc: "Trend Efficiency Ratio — how straight-line price moves are. USE WHEN: You run a trend-following strategy and want assets that trend cleanly. 1.0 = perfect trend, low = choppy. Based on price data, not backtest results." },
      { value: "hurst", label: "Hurst", desc: "Persistence exponent. Above 0.5 = trending, below 0.5 = mean-reverting. USE WHEN: You want to match assets to your strategy type. Trend strategies want Hurst > 0.5. Mean-rev strategies want Hurst < 0.5." },
      { value: "ac", label: "AC", desc: "Autocorrelation — does today's move predict tomorrow's? USE WHEN: You want raw momentum data. Positive AC = momentum continues. Negative AC = reversals. Use AC (Trend) or AC (Mean-Rev) for pre-scored versions." },
      { value: "ac_trend", label: "AC (Trend)", desc: "AC scored for trend strategies — rewards positive momentum. USE WHEN: You specifically run trend-following and want assets where momentum persists day-to-day." },
      { value: "trend_score", label: "Trend Score", desc: "Combined trend grade (TER + Hurst + GPR + AC in one number). USE WHEN: You want a single trend-suitability ranking without picking individual metrics. Best single seed for trend strategies." },
    ],
  },
  {
    group: "Mean-Rev Suitability",
    hint: "OHLCV-based — pick if you run a mean-reversion strategy",
    metrics: [
      { value: "ac_meanrev", label: "AC (Mean-Rev)", desc: "AC scored for mean-reversion — rewards negative autocorrelation (today up → tomorrow down). USE WHEN: You run a mean-reversion strategy and want assets that snap back predictably." },
      { value: "mri", label: "MRI", desc: "Mean Reversion Intensity — how strongly price snaps back to average. USE WHEN: You want the most aggressively mean-reverting assets. Higher = stronger snap-back." },
      { value: "vr", label: "VR", desc: "Variance Ratio — below 1.0 = mean-reverting, above 1.0 = trending. USE WHEN: You want a clean split between trend and mean-rev assets. Below 0.8 = strong mean-reversion signal." },
      { value: "half_life", label: "Half Life", desc: "Bars until price snaps halfway back to mean. USE WHEN: You need to know how fast mean-reversion happens. Lower = faster snap-back = better for short-term mean-rev." },
      { value: "crossing_rate", label: "Cross Rate", desc: "How often price crosses its average. USE WHEN: You want frequent trading opportunities. More crossings = more entry signals for mean-rev strategies." },
      { value: "meanrev_score", label: "MR Score", desc: "Combined mean-rev grade (MRI + Hurst + VR + Half-Life + Crossing Rate). USE WHEN: You want a single mean-rev suitability ranking. Best single seed for mean-rev strategies." },
    ],
  },
  {
    group: "Market Structure",
    hint: "Price action patterns from range detection",
    metrics: [
      { value: "avg_range", label: "Avg Range", desc: "Average consecutive same-direction ranges (bull or bear streaks). USE WHEN: You want assets that trend in sustained runs rather than chopping back and forth. Higher = longer directional streaks. Can also work as a filter to remove choppy assets." },
    ],
  },
  {
    group: "Structural Quality",
    hint: "Grade-based quality — combines multiple structural metrics into one score",
    metrics: [
      { value: "asset_quality", label: "Asset Quality", desc: "OHLCV-based quality grade. Trend mode uses Hurst + TER; Mean-Rev mode uses Hurst + VR + Crossing Rate. USE WHEN: You want to pre-filter assets by how suitable their PRICE ACTION is for your strategy type, before even looking at backtest results. Can also filter by grade (A/B/C/D)." },
      { value: "strat_quality", label: "Strategy Quality", desc: "PnL-based quality grade from backtest returns. USE WHEN: You want to grade how well the STRATEGY PERFORMED on each asset (not just the asset's price structure). Filters assets where the strategy produced clean, gradeable PnL curves." },
    ],
  },
];

/** Flat list of all seed metrics — derived from categories. Used for lookups and backward compat. */
export const SEED_METRICS = SEED_CATEGORIES.flatMap((c) => c.metrics);

/** Seeds that can also act as pre-optimizer filters. Shown with a filter badge in the UI. */
export const FILTERABLE_SEEDS = new Set(["avg_range", "asset_quality", "strat_quality", "is_wfe"]);

interface Props {
  show: boolean;
  onToggle: () => void;
  seedMetrics: string[];
  onSeedMetricsChange: (v: string[]) => void;
  maxSeeds: number;
  onMaxSeedsChange: (v: number) => void;
  dateFrom: string;
  onDateFromChange: (v: string) => void;
  // Seed weights
  seedWeights?: Record<string, number>;
  onSeedWeightsChange?: (v: Record<string, number>) => void;
  // Recency-weighted seed scoring
  recencyEnabled?: boolean;
  onRecencyEnabledChange?: (v: boolean) => void;
  recencyMode?: string;
  onRecencyModeChange?: (v: string) => void;
  recencyBatchSize?: number;
  onRecencyBatchSizeChange?: (v: number) => void;
  recencyBoostFactor?: number;
  onRecencyBoostFactorChange?: (v: number) => void;
  hideMaxSeeds?: boolean;
  hideDateFrom?: boolean;
  // Pre-filter (structural quality)
  prefilterEnabled?: boolean;
  onPrefilterEnabledChange?: (v: boolean) => void;
  prefilterMode?: string;
  onPrefilterModeChange?: (v: string) => void;
  prefilterHurstMin?: number;
  onPrefilterHurstMinChange?: (v: number) => void;
  prefilterTerPercentile?: number;
  onPrefilterTerPercentileChange?: (v: number) => void;
  prefilterHurstMax?: number;
  onPrefilterHurstMaxChange?: (v: number) => void;
  prefilterVrMax?: number;
  onPrefilterVrMaxChange?: (v: number) => void;
  prefilterMinCrossingRate?: number;
  onPrefilterMinCrossingRateChange?: (v: number) => void;
  // AC post-filter
  acPostfilter?: boolean;
  onAcPostfilterChange?: (v: boolean) => void;
  acPostfilterType?: string;
  onAcPostfilterTypeChange?: (v: string) => void;
  acLag?: number;
  onAcLagChange?: (v: number) => void;
  // Avg Range seed config
  avgRangeBullThreshold?: number;
  onAvgRangeBullThresholdChange?: (v: number) => void;
  avgRangeBearThreshold?: number;
  onAvgRangeBearThresholdChange?: (v: number) => void;
  avgRangeAllThreshold?: number;
  onAvgRangeAllThresholdChange?: (v: number) => void;
  avgRangeFilterMode?: boolean;
  onAvgRangeFilterModeChange?: (v: boolean) => void;
  // Asset Quality seed config
  assetQualityMode?: string;
  onAssetQualityModeChange?: (v: string) => void;
  assetQualityGradeMode?: string;
  onAssetQualityGradeModeChange?: (v: string) => void;
  assetQualityMinGrade?: string;
  onAssetQualityMinGradeChange?: (v: string) => void;
  assetQualityMinTrades?: number;
  onAssetQualityMinTradesChange?: (v: number) => void;
  assetQualityFilter?: boolean;
  onAssetQualityFilterChange?: (v: boolean) => void;
  assetQualityHurstMin?: number;
  onAssetQualityHurstMinChange?: (v: number) => void;
  assetQualityTerPercentile?: number;
  onAssetQualityTerPercentileChange?: (v: number) => void;
  assetQualityHurstMax?: number;
  onAssetQualityHurstMaxChange?: (v: number) => void;
  assetQualityVrMax?: number;
  onAssetQualityVrMaxChange?: (v: number) => void;
  assetQualityMinCr?: number;
  onAssetQualityMinCrChange?: (v: number) => void;
  // Strategy Quality seed config
  stratQualityMode?: string;
  onStratQualityModeChange?: (v: string) => void;
  stratQualityMinGrade?: string;
  onStratQualityMinGradeChange?: (v: string) => void;
  stratQualityFilter?: boolean;
  onStratQualityFilterChange?: (v: boolean) => void;
  // IS-WFE seed config
  isWfeIsPct?: number;
  onIsWfeIsPctChange?: (v: number) => void;
  isWfeMinTrades?: number;
  onIsWfeMinTradesChange?: (v: number) => void;
  isWfeFilter?: boolean;
  onIsWfeFilterChange?: (v: boolean) => void;
  isWfeMinWfe?: number;
  onIsWfeMinWfeChange?: (v: number) => void;
}

export default function OptimizerAdvanced({
  show, onToggle, seedMetrics, onSeedMetricsChange,
  maxSeeds, onMaxSeedsChange, dateFrom, onDateFromChange,
  seedWeights = {}, onSeedWeightsChange,
  recencyEnabled = false, onRecencyEnabledChange,
  recencyMode = "gradual", onRecencyModeChange,
  recencyBatchSize = 10, onRecencyBatchSizeChange,
  recencyBoostFactor = 3.0, onRecencyBoostFactorChange,
  hideMaxSeeds = false, hideDateFrom = false,
  prefilterEnabled = false, onPrefilterEnabledChange,
  prefilterMode = "trend", onPrefilterModeChange,
  prefilterHurstMin = 0.50, onPrefilterHurstMinChange,
  prefilterTerPercentile = 25, onPrefilterTerPercentileChange,
  prefilterHurstMax = 0.50, onPrefilterHurstMaxChange,
  prefilterVrMax = 1.0, onPrefilterVrMaxChange,
  prefilterMinCrossingRate = 0.05, onPrefilterMinCrossingRateChange,
  acPostfilter = false, onAcPostfilterChange,
  acPostfilterType = "trend", onAcPostfilterTypeChange,
  acLag = 1, onAcLagChange,
  avgRangeBullThreshold = 0, onAvgRangeBullThresholdChange,
  avgRangeBearThreshold = 0, onAvgRangeBearThresholdChange,
  avgRangeAllThreshold = 0, onAvgRangeAllThresholdChange,
  avgRangeFilterMode = false, onAvgRangeFilterModeChange,
  assetQualityMode = "trend", onAssetQualityModeChange,
  assetQualityGradeMode = "absolute", onAssetQualityGradeModeChange,
  assetQualityMinGrade = "", onAssetQualityMinGradeChange,
  assetQualityMinTrades = 0, onAssetQualityMinTradesChange,
  assetQualityFilter = false, onAssetQualityFilterChange,
  assetQualityHurstMin = 0.50, onAssetQualityHurstMinChange,
  assetQualityTerPercentile = 25, onAssetQualityTerPercentileChange,
  assetQualityHurstMax = 0.50, onAssetQualityHurstMaxChange,
  assetQualityVrMax = 1.0, onAssetQualityVrMaxChange,
  assetQualityMinCr = 0.05, onAssetQualityMinCrChange,
  stratQualityMode = "trend", onStratQualityModeChange,
  stratQualityMinGrade = "", onStratQualityMinGradeChange,
  stratQualityFilter = false, onStratQualityFilterChange,
  isWfeIsPct = 60, onIsWfeIsPctChange,
  isWfeMinTrades = 0, onIsWfeMinTradesChange,
  isWfeFilter = false, onIsWfeFilterChange,
  isWfeMinWfe = 0.5, onIsWfeMinWfeChange,
}: Props) {
  const toggleMetric = (metric: string) => {
    if (seedMetrics.includes(metric)) {
      const next = seedMetrics.filter((m) => m !== metric);
      onSeedMetricsChange(next.length > 0 ? next : ["sortino"]);
    } else {
      onSeedMetricsChange([...seedMetrics, metric]);
    }
  };

  const activeCount = seedMetrics.length;
  const hasDateFrom = !hideDateFrom && dateFrom.length > 0;
  const hasRecency = recencyEnabled;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium hover:bg-ag-border/30 transition-colors rounded-lg"
      >
        <span>
          Advanced
          {(activeCount > 1 || hasDateFrom || hasRecency) && (
            <span className="ml-2 text-xs px-1.5 py-0.5 rounded bg-ag-accent/20 text-ag-accent">
              {[
                activeCount > 1 && `${activeCount} seed metrics`,
                hasRecency && "recency",
                hasDateFrom && `from ${dateFrom}`,
              ].filter(Boolean).join(" · ")}
            </span>
          )}
        </span>
        <span className="text-ag-text-muted text-xs">{show ? "▲" : "▼"}</span>
      </button>
      {show && (
        <div className="px-4 pb-4 border-t border-ag-border pt-3 space-y-4">
          {/* Seed Ranking Metrics — grouped by category */}
          <div>
            <label className="block text-xs text-ag-text-muted mb-2">
              Seed Ranking<HintTip text="Pick which metrics rank your assets before the optimizer runs. Top-ranked assets become starting points. You can pick from multiple categories." />
            </label>
            <div className="space-y-2">
              {SEED_CATEGORIES.map((cat) => (
                <div key={cat.group}>
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="text-[10px] font-semibold text-ag-text-muted uppercase tracking-wide">{cat.group}</span>
                    <span className="text-[9px] text-ag-text-muted/60">— {cat.hint}</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {cat.metrics.map((m) => (
                      <span key={m.value} className="inline-flex items-center">
                        <button
                          onClick={() => toggleMetric(m.value)}
                          title={m.desc}
                          className={`px-2.5 py-1 rounded text-xs font-medium whitespace-nowrap transition-colors ${
                            seedMetrics.includes(m.value)
                              ? "bg-ag-accent text-white"
                              : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
                          }`}>
                          {m.label}
                          {FILTERABLE_SEEDS.has(m.value) && <span className="ml-0.5 text-[8px] text-ag-accent/70 align-super" title="Can also work as a filter">ᶠ</span>}
                        </button>
                        <UserNote noteKey={`seed:${m.value}`} />
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Seed Weights — shown when 2+ metrics selected */}
          {seedMetrics.length > 1 && (
            <div className="bg-ag-dark/50 rounded-lg px-3 py-2">
              <label className="block text-[10px] text-ag-text-muted mb-1.5">
                Seed Weights<HintTip text="How much each metric matters in ranking. Higher weight = more influence. Default: 1 for all." />
              </label>
              <div className="flex items-center gap-3 flex-wrap">
                {seedMetrics.map((m) => {
                  const meta = SEED_METRICS.find(s => s.value === m);
                  return (
                    <div key={m} className="flex items-center gap-1">
                      <span className="text-[10px] text-ag-text-muted" title={meta?.desc}>{meta?.label || m}:</span>
                      <input type="number" min={0.5} max={3} step={0.5}
                        value={seedWeights[m] ?? 1}
                        onChange={(e) => onSeedWeightsChange?.({
                          ...seedWeights, [m]: Math.max(0.5, Math.min(3, Number(e.target.value)))
                        })}
                        className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Recency config — shown when recency seed is selected */}
          {seedMetrics.includes("recency") && (
            <div className="flex items-center gap-3 flex-wrap bg-ag-dark/50 rounded-lg px-3 py-2">
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={recencyEnabled}
                  onChange={(e) => onRecencyEnabledChange?.(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-ag-text-muted">Enable Recency<HintTip text="Weight recent trades higher in scoring. Assets still performing well NOW rank better." /></span>
              </label>
              {recencyEnabled && (
                <>
                  <div className="flex items-center gap-1.5">
                    <label className="text-[10px] text-ag-text-muted">Mode:</label>
                    <select value={recencyMode}
                      onChange={(e) => onRecencyModeChange?.(e.target.value)}
                      className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-xs">
                      <option value="gradual">Gradual Decay</option>
                      <option value="batch">Recent Batch</option>
                    </select>
                  </div>
                  {recencyMode === "batch" && (
                    <div className="flex items-center gap-1.5">
                      <label className="text-[10px] text-ag-text-muted">Batch Size:<HintTip text="How many recent trades get the boost. Older trades count normally." /></label>
                      <input type="number" min={3} max={100} value={recencyBatchSize}
                        onChange={(e) => onRecencyBatchSizeChange?.(Math.max(3, Math.min(100, Number(e.target.value))))}
                        className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                    </div>
                  )}
                  {recencyMode === "batch" && (
                    <div className="flex items-center gap-1.5">
                      <label className="text-[10px] text-ag-text-muted">Boost:<HintTip text="Multiplier for recent trades. 3.0 = recent trades count 3× more." /></label>
                      <input type="number" min={1} max={10} step={0.5} value={recencyBoostFactor}
                        onChange={(e) => onRecencyBoostFactorChange?.(Math.max(1, Math.min(10, Number(e.target.value))))}
                        className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* Avg Range config — shown when avg_range seed is selected */}
          {seedMetrics.includes("avg_range") && (
            <div className="bg-ag-dark/50 rounded-lg px-3 py-2 space-y-2">
              <label className="block text-[10px] text-ag-text-muted mb-1">
                Avg Range Thresholds<HintTip text="Minimum avg consecutive range counts. Higher values = better seed rank. With 'Use as filter', assets below thresholds are removed entirely." />
              </label>
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Long:</label>
                  <input type="number" min={0} max={20} step={0.5} value={avgRangeBullThreshold}
                    onChange={(e) => onAvgRangeBullThresholdChange?.(Math.max(0, Number(e.target.value)))}
                    className="w-14 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Short:</label>
                  <input type="number" min={0} max={20} step={0.5} value={avgRangeBearThreshold}
                    onChange={(e) => onAvgRangeBearThresholdChange?.(Math.max(0, Number(e.target.value)))}
                    className="w-14 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">L+S:</label>
                  <input type="number" min={0} max={20} step={0.5} value={avgRangeAllThreshold}
                    onChange={(e) => onAvgRangeAllThresholdChange?.(Math.max(0, Number(e.target.value)))}
                    className="w-14 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                </div>
              </div>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={avgRangeFilterMode}
                  onChange={(e) => onAvgRangeFilterModeChange?.(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-ag-text-muted">Use this as a filter<HintTip text="ON: remove assets below thresholds entirely. OFF: keep them but rank them lower." /></span>
              </label>
            </div>
          )}

          {/* Asset Quality config — shown when asset_quality seed is selected */}
          {seedMetrics.includes("asset_quality") && (
            <div className="bg-ag-dark/50 rounded-lg px-3 py-2 space-y-2">
              <label className="block text-[10px] text-ag-text-muted mb-1">
                Asset Quality Config<HintTip text="OHLCV-based quality scoring. Mode picks Trend (Hurst + TER) or Mean-Rev (Hurst + VR + CR) thresholds. Grade filters by structural score. Use as filter removes assets entirely." />
              </label>
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Mode:</label>
                  <select value={assetQualityMode} onChange={(e) => onAssetQualityModeChange?.(e.target.value)}
                    className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs">
                    <option value="trend">Trend-Following</option>
                    <option value="meanrev">Mean-Reversion</option>
                  </select>
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Grading:</label>
                  <select value={assetQualityGradeMode} onChange={(e) => onAssetQualityGradeModeChange?.(e.target.value)}
                    className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs">
                    <option value="absolute">Absolute</option>
                    <option value="autozoom">Auto-Zoom</option>
                  </select>
                  <HintTip text="Absolute: fixed grade thresholds. Auto-Zoom: normalizes within the batch so grades spread evenly — use when all assets cluster." />
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Min Grade:</label>
                  <select value={assetQualityMinGrade} onChange={(e) => onAssetQualityMinGradeChange?.(e.target.value)}
                    className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs">
                    <option value="">Any</option>
                    <option value="A+">A+</option><option value="A">A</option>
                    <option value="B+">B+</option><option value="B">B</option>
                    <option value="C+">C+</option><option value="C">C</option>
                    <option value="D">D</option>
                  </select>
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Min Trades:</label>
                  <input type="number" min={0} max={500} step={1} value={assetQualityMinTrades}
                    onChange={(e) => onAssetQualityMinTradesChange?.(Math.max(0, Number(e.target.value)))}
                    className="w-14 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                </div>
              </div>
              {/* Mode-dependent structural thresholds */}
              {assetQualityMode === "trend" ? (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[10px] text-ag-text-muted mb-1">Min Hurst<HintTip text="Assets below this Hurst value are filtered. 0.50 = random walk. Higher = more trending." /></label>
                    <input type="number" min={0.3} max={0.8} step={0.01} value={assetQualityHurstMin}
                      onChange={(e) => onAssetQualityHurstMinChange?.(Number(e.target.value))}
                      className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                  </div>
                  <div>
                    <label className="block text-[10px] text-ag-text-muted mb-1">TER Percentile<HintTip text="Remove the bottom N% by Trend Efficiency. 25 = cut the worst quarter." /></label>
                    <input type="number" min={0} max={75} step={5} value={assetQualityTerPercentile}
                      onChange={(e) => onAssetQualityTerPercentileChange?.(Number(e.target.value))}
                      className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-[10px] text-ag-text-muted mb-1">Max Hurst<HintTip text="Assets above this are too trending for mean-reversion." /></label>
                    <input type="number" min={0.3} max={0.7} step={0.01} value={assetQualityHurstMax}
                      onChange={(e) => onAssetQualityHurstMaxChange?.(Number(e.target.value))}
                      className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                  </div>
                  <div>
                    <label className="block text-[10px] text-ag-text-muted mb-1">Max VR<HintTip text="Below 1.0 = mean-reverting. Above 1.0 = trending." /></label>
                    <input type="number" min={0.5} max={1.5} step={0.05} value={assetQualityVrMax}
                      onChange={(e) => onAssetQualityVrMaxChange?.(Number(e.target.value))}
                      className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                  </div>
                  <div>
                    <label className="block text-[10px] text-ag-text-muted mb-1">Min Cross Rate<HintTip text="How often price crosses its average. More crossings = better for mean-reversion." /></label>
                    <input type="number" min={0} max={0.5} step={0.01} value={assetQualityMinCr}
                      onChange={(e) => onAssetQualityMinCrChange?.(Number(e.target.value))}
                      className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                  </div>
                </div>
              )}
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={assetQualityFilter}
                  onChange={(e) => onAssetQualityFilterChange?.(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-ag-text-muted">Use as filter<HintTip text="ON: remove assets that fail grade + structural thresholds entirely. OFF: keep them but rank them lower." /></span>
              </label>
            </div>
          )}

          {/* Strategy Quality config — shown when strat_quality seed is selected */}
          {seedMetrics.includes("strat_quality") && (
            <div className="bg-ag-dark/50 rounded-lg px-3 py-2 space-y-2">
              <label className="block text-[10px] text-ag-text-muted mb-1">
                Strategy Quality Config<HintTip text="PnL-based quality scoring from backtest returns. Mode picks Trend or Mean-Rev grade. Grade is always absolute (no zoom). Use as filter removes assets entirely." />
              </label>
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Mode:</label>
                  <select value={stratQualityMode} onChange={(e) => onStratQualityModeChange?.(e.target.value)}
                    className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs">
                    <option value="trend">Trend-Following</option>
                    <option value="meanrev">Mean-Reversion</option>
                  </select>
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Min Grade:</label>
                  <select value={stratQualityMinGrade} onChange={(e) => onStratQualityMinGradeChange?.(e.target.value)}
                    className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs">
                    <option value="">Any</option>
                    <option value="A+">A+</option><option value="A">A</option>
                    <option value="B+">B+</option><option value="B">B</option>
                    <option value="C+">C+</option><option value="C">C</option>
                    <option value="D">D</option>
                  </select>
                </div>
              </div>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={stratQualityFilter}
                  onChange={(e) => onStratQualityFilterChange?.(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-ag-text-muted">Use as filter<HintTip text="ON: remove assets below min grade entirely. OFF: keep them but rank lower." /></span>
              </label>
            </div>
          )}

          {/* IS-WFE config — shown when is_wfe seed is selected */}
          {seedMetrics.includes("is_wfe") && (
            <div className="bg-ag-dark/50 rounded-lg px-3 py-2 space-y-2">
              <label className="block text-[10px] text-ag-text-muted mb-1">
                IS-WFE Config<HintTip text="Splits each asset's daily PnL into In-Sample and Out-of-Sample windows, then computes WFE = (OOS daily profit rate) / (IS daily profit rate). Higher WFE = the edge held up out of sample." />
              </label>
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">IS:OOS Split:<HintTip text="How to divide the data. 60:40 means 60% In-Sample, 40% Out-of-Sample. Higher IS = more training data but less OOS to validate." /></label>
                  <select value={isWfeIsPct} onChange={(e) => onIsWfeIsPctChange?.(Number(e.target.value))}
                    className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs">
                    <option value={50}>50:50</option>
                    <option value={60}>60:40</option>
                    <option value={70}>70:30</option>
                    <option value={75}>75:25</option>
                    <option value={80}>80:20</option>
                  </select>
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Min IS Trades:<HintTip text="Minimum trades in the IS window for the asset to be scored. 0 = no minimum. Assets with fewer IS trades are excluded from WFE ranking." /></label>
                  <input type="number" min={0} max={500} step={1} value={isWfeMinTrades}
                    onChange={(e) => onIsWfeMinTradesChange?.(Math.max(0, Number(e.target.value)))}
                    className="w-14 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                </div>
              </div>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={isWfeFilter}
                  onChange={(e) => onIsWfeFilterChange?.(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-ag-text-muted">Use as filter<HintTip text="ON: remove assets below Min WFE entirely. OFF: keep them but rank them lower." /></span>
              </label>
              {isWfeFilter && (
                <div className="flex items-center gap-1.5 ml-5">
                  <label className="text-[10px] text-ag-text-muted">Min WFE:<HintTip text="Minimum WFE to pass the filter. 0.5 = OOS must retain at least 50% of IS daily profit rate. 1.0 = OOS must match IS." /></label>
                  <input type="number" min={0} max={2} step={0.1} value={isWfeMinWfe}
                    onChange={(e) => onIsWfeMinWfeChange?.(Math.max(0, Number(e.target.value)))}
                    className="w-14 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                </div>
              )}
            </div>
          )}

          {/* Max Seeds + Start Date (hidden when shown in main panel) */}
          {(!hideMaxSeeds || !hideDateFrom) && (
            <div className="grid grid-cols-2 gap-3">
              {!hideMaxSeeds && (
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">Max Seeds<HintTip text="Top N ranked assets become starting points. Higher = more thorough but slower." /></label>
                  <input type="number" min={5} max={200} value={maxSeeds}
                    onChange={(e) => onMaxSeedsChange(Math.max(5, Math.min(200, Number(e.target.value))))}
                    className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
                  <span className="text-[10px] text-ag-text-muted">Top-ranked assets as starting points</span>
                </div>
              )}
              {!hideDateFrom && (
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">Start Date<HintTip text="Only count trades after this date. Ignores older data." /></label>
                  <input type="date" value={dateFrom}
                    onChange={(e) => onDateFromChange(e.target.value)}
                    className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
                  <span className="text-[10px] text-ag-text-muted">Only use trades after this date</span>
                </div>
              )}
            </div>
          )}

          {/* Pre-Filter (Structural Quality) */}
          <div className="border-t border-ag-border pt-3">
            <label className="flex items-center gap-2 text-xs cursor-pointer mb-2">
              <input type="checkbox" checked={prefilterEnabled}
                onChange={(e) => onPrefilterEnabledChange?.(e.target.checked)}
                className="accent-ag-accent" />
              <span className="font-medium">Pre-Filter (Structural Quality)<HintTip text="Remove assets that don't fit your strategy type before optimization. Trend: removes non-trending assets. Mean-Rev: removes trending assets." /></span>
            </label>
            {prefilterEnabled && (
              <div className="space-y-2 ml-5">
                <div className="flex items-center gap-3">
                  <label className="text-[10px] text-ag-text-muted">Mode:<HintTip text="Trend: removes non-trending assets (low Hurst/TER). Mean-Rev: removes trending assets (high Hurst). Pick based on your strategy." /></label>
                  <select value={prefilterMode}
                    onChange={(e) => onPrefilterModeChange?.(e.target.value)}
                    className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
                    <option value="trend">Trend-Following</option>
                    <option value="meanrev">Mean-Reversion</option>
                  </select>
                </div>
                {prefilterMode === "trend" ? (
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[10px] text-ag-text-muted mb-1">Min Hurst<HintTip text="Assets below this are removed. 0.50 = random walk. Higher = more trending." /></label>
                      <input type="number" min={0.3} max={0.8} step={0.01} value={prefilterHurstMin}
                        onChange={(e) => onPrefilterHurstMinChange?.(Number(e.target.value))}
                        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                    </div>
                    <div>
                      <label className="block text-[10px] text-ag-text-muted mb-1">TER Percentile<HintTip text="Remove the bottom N% by Trend Efficiency. 25 = cut the worst quarter." /></label>
                      <input type="number" min={0} max={75} step={5} value={prefilterTerPercentile}
                        onChange={(e) => onPrefilterTerPercentileChange?.(Number(e.target.value))}
                        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="block text-[10px] text-ag-text-muted mb-1">Max Hurst<HintTip text="Assets above this are too trending for mean-reversion." /></label>
                      <input type="number" min={0.3} max={0.7} step={0.01} value={prefilterHurstMax}
                        onChange={(e) => onPrefilterHurstMaxChange?.(Number(e.target.value))}
                        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                    </div>
                    <div>
                      <label className="block text-[10px] text-ag-text-muted mb-1">Max VR<HintTip text="Below 1.0 = mean-reverting. Above 1.0 = trending. Remove trending assets." /></label>
                      <input type="number" min={0.5} max={1.5} step={0.05} value={prefilterVrMax}
                        onChange={(e) => onPrefilterVrMaxChange?.(Number(e.target.value))}
                        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                    </div>
                    <div>
                      <label className="block text-[10px] text-ag-text-muted mb-1">Min Cross Rate<HintTip text="How often price crosses its average. More crossings = better for mean-reversion." /></label>
                      <input type="number" min={0} max={0.5} step={0.01} value={prefilterMinCrossingRate}
                        onChange={(e) => onPrefilterMinCrossingRateChange?.(Number(e.target.value))}
                        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* AC Post-Filter */}
          <div className="border-t border-ag-border pt-3">
            <label className="flex items-center gap-2 text-xs cursor-pointer mb-2">
              <input type="checkbox" checked={acPostfilter}
                onChange={(e) => onAcPostfilterChange?.(e.target.checked)}
                className="accent-ag-accent" />
              <span className="font-medium">AC Post-Filter<HintTip text="After optimization, flags assets whose price behavior doesn't match the strategy type (e.g. mean-reverting asset in a trend portfolio)." /></span>
            </label>
            {acPostfilter && (
              <div className="flex items-center gap-3 ml-5">
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Type:<HintTip text="Match to your strategy. Trend: flags mean-reverting assets. Mean-Rev: flags trending assets." /></label>
                  <select value={acPostfilterType}
                    onChange={(e) => onAcPostfilterTypeChange?.(e.target.value)}
                    className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
                    <option value="trend">Trend</option>
                    <option value="meanrev">Mean-Rev</option>
                  </select>
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Lag:<HintTip text="How many bars back to check. 1 = next bar." /></label>
                  <input type="number" min={1} max={10} value={acLag}
                    onChange={(e) => onAcLagChange?.(Math.max(1, Number(e.target.value)))}
                    className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-1 text-xs text-center" />
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
