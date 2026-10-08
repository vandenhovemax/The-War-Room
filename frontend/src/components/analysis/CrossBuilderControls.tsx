"use client";

import { ALL_OBJECTIVES, DIVERSIFICATION_ALGOS } from "./objectiveConstants";
import { SEED_METRICS, SEED_CATEGORIES, FILTERABLE_SEEDS } from "./OptimizerAdvanced";
import HintTip from "@/components/common/HintTip";
import L2GroupEditor from "@/components/common/L2GroupEditor";
import SessionFilterConfig from "@/components/common/SessionFilterConfig";
import type { SessionFilterState } from "@/components/common/SessionFilterConfig";
import UserNote from "./UserNote";

export interface AutoSettings {
  splitMode: boolean; nAssets: number;
  nLongs: number; nShorts: number;
  withinObj: string; combinedObj: string; nPortfolios: number;
  poolPerBatch: number;
}

export interface ManualSettings {
  l2Groups: number[][];
  objLayer1: string; objLayer2: string; objLayer3: string;
  candidatesPerStrategy: number; nFinalStrats: number; nPortfolios: number;
  l1QualityGate: boolean; nPerGroup: number;
  // Pool mode
  poolMode: boolean;
  poolPerBatch: number;
  l2NAssets: number;
  l2NVariants: number;
  l2MaxOverlap: number;
  l3MaxOverlap: number;
  // Cross-group symbol dedup
  l2SymbolDedup: string;      // "keep_all" | "same_side" | "any_side"
  l2DedupTiebreaker: string;  // "pnl" | "seed_rank"
}

export interface SharedSettings {
  corrMethod: string;
  divAlgo: string;
  seedMetrics: string[];
  maxSeeds: number;
  minTrades: number;
  minPf: number;
  minWr: number;
  // Per-asset max PF filter
  maxAssetPf: number;
  capAssetPf: boolean;
  // Portfolio-level quality gates
  portfolioMinPf: number;
  portfolioMinWr: number;
  dateFrom: string;
  extraSlippage: number;
  extraCommission: number;
  fundingDaily: number;
  spread: number;
  // Recency-weighted seed scoring
  recencyEnabled: boolean;
  recencyMode: string;
  recencyBatchSize: number;
  recencyBoostFactor: number;
  // Streamlit compatibility
  streamlitLogic: boolean;
  // Portfolio diversity: max Jaccard overlap between candidates
  maxOverlap: string; // "off"|"0.70"|"0.50"|"0.30"|"0.10"
  // Greedy pool expansion: when true, greedy picks from ALL profitable assets
  expandGreedyPool: boolean;
  // Per-metric seed weights: {metric_code: weight}
  seedWeights: Record<string, number>;
  // Seed prior: soft tiebreaker in greedy (0 = off, 0.01-0.30 = on)
  seedPriorWeight: number;
  // Pre-filter (structural quality)
  prefilterEnabled: boolean;
  prefilterMode: string;
  prefilterHurstMin: number;
  prefilterTerPercentile: number;
  prefilterHurstMax: number;
  prefilterVrMax: number;
  prefilterMinCrossingRate: number;
  // AC post-filter
  acPostfilter: boolean;
  acPostfilterType: string;
  acLag: number;
  // Avg Range seed config
  avgRangeBullThreshold: number;
  avgRangeBearThreshold: number;
  avgRangeAllThreshold: number;
  avgRangeFilterMode: boolean;
  // Asset Quality seed config
  assetQualityMode: string;
  assetQualityGradeMode: string;
  assetQualityMinGrade: string;
  assetQualityMinTrades: number;
  assetQualityFilter: boolean;
  assetQualityHurstMin: number;
  assetQualityTerPercentile: number;
  assetQualityHurstMax: number;
  assetQualityVrMax: number;
  assetQualityMinCr: number;
  // Strategy Quality seed config
  stratQualityMode: string;
  stratQualityMinGrade: string;
  stratQualityFilter: boolean;
  // IS-WFE seed config
  isWfeIsPct: number;
  isWfeMinTrades: number;
  isWfeFilter: boolean;
  isWfeMinWfe: number;
  // Seed-blind selection: when true, Tournament/Quality First/Marginal Sharpe/Ensemble
  // treat all pool assets equally instead of using seed rankings (more diverse, old behavior)
  seedBlindSelection: boolean;
  // Correlation jitter
  corrJitterEnabled: boolean;
  corrJitterTemperature: number;
  // Post-construction refinement
  refinementEnabled: boolean;
  refinementMode: string;
  refinementMetric: string;
  // ── Overfitting Guard ──────────────────────────────────────────
  overfitGuardEnabled: boolean;
  ofLogEnabled: boolean;
  ofLogCapPf: number;
  ofLogCapSortino: number;
  ofLogCapPayoff: number;
  ofLogCapWr: number;
  ofLogCapSharpe: number;
  ofLogCapCalmar: number;
  ofZScoreEnabled: boolean;
  ofZThreshold: number;
  ofZPenaltyStrength: number;
  ofBayesEnabled: boolean;
  ofBayesK: number;
  ofSigmoidEnabled: boolean;
  ofSigCapPf: number;
  ofSigCapSortino: number;
  ofSigCapPayoff: number;
  ofSigCapWr: number;
  ofSigCapSharpe: number;
  ofSigCapCalmar: number;
  ofNormalRangeEnabled: boolean;
  ofNrPctLow: number;
  ofNrPctHigh: number;
  ofNrPenaltyScale: number;
  ofNrBonus: number;
  ofWfRerankEnabled: boolean;
  ofWfSplitPct: number;
  ofWfValidationMetric: string;
  // Genetic algorithm params
  geneticGenerations: number;
  geneticPopulation: number;
  geneticMutationRate: number;
  geneticCrossoverRate: number;
  geneticElitePct: number;
  // Bybit size gate
  bybitSizeGate: "off" | "binary" | "rate";
  bybitSizeGateMaxPct: number;
  // Session Filter
  sessionFilterEnabled: boolean;
  sessionFilterMode: "fixed" | "auto" | "saved_mask";
  sessionFilterExcludeDays: number[];
  sessionFilterExcludeHours: number[];
  sessionFilterAutoMinTrades: number;
  sessionFilterAutoIncludeInsufficient: boolean;
  sessionFilterGradeOnCropped: boolean;
  sessionFilterSavedMaskId: number | null;
}

export interface DirectionalSettings {
  l2Groups: number[][];
  nPerBatch: number;
  objL1: string;
  objL2: string;
  objL3: string;
  kL1: number;
  kL2: number;
  kL3: number;
  l1QualityGate: boolean;
  nPerGroup: number;
}

interface Props {
  mode: "auto" | "manual" | "directional";
  onModeChange: (m: "auto" | "manual" | "directional") => void;
  auto: AutoSettings;
  onAutoChange: (patch: Partial<AutoSettings>) => void;
  manual: ManualSettings;
  onManualChange: (patch: Partial<ManualSettings>) => void;
  directional: DirectionalSettings;
  onDirectionalChange: (patch: Partial<DirectionalSettings>) => void;
  shared: SharedSettings;
  onSharedChange: (patch: Partial<SharedSettings>) => void;
  prioritizeStability: boolean;
  onStabilityChange: (v: boolean) => void;
  nBatches: number;
  batchOptions: { id: number; label: string }[];
  onRun: () => void;
  loading: boolean;
  disabled: boolean;
}

export default function CrossBuilderControls({
  mode, onModeChange, auto, onAutoChange, manual, onManualChange,
  directional, onDirectionalChange,
  shared, onSharedChange, prioritizeStability, onStabilityChange,
  nBatches, batchOptions, onRun, loading, disabled,
}: Props) {
  const toggleSeed = (m: string) => {
    const cur = shared.seedMetrics;
    if (cur.includes(m)) {
      const next = cur.filter((x) => x !== m);
      onSharedChange({ seedMetrics: next.length > 0 ? next : ["sortino"] });
    } else {
      onSharedChange({ seedMetrics: [...cur, m] });
    }
  };

  return (
    <div className="space-y-3">
      {/* Mode toggle */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-ag-text-muted">Mode:<HintTip text="Auto: Best for most users. Finds the best portfolio from all batches using correlation to avoid redundant assets.\n\nManual: 3-layer process — best when using 3+ strategies and you want each strategy to contribute assets. Needs 2+ batches.\n\nDirectional: Best for separate Long/Short batches. Carries multiple candidates forward to avoid picking a winner too early. Needs 2+ batches." /></span>
        {(["auto", "manual", "directional"] as const).map((m) => (
          <button key={m} onClick={() => onModeChange(m)}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${mode === m ? "bg-ag-accent text-ag-darker" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
              }`}>
            {m === "auto" ? "Auto" : m === "manual" ? "Manual" : "Directional Batches"}
          </button>
        ))}
      </div>

      {mode === "auto" ? (
        <AutoControls s={auto} onChange={onAutoChange} onRun={onRun} loading={loading} disabled={disabled} />
      ) : mode === "manual" ? (
        <ManualControls s={manual} onChange={onManualChange} nBatches={nBatches}
          batchOptions={batchOptions} onRun={onRun} loading={loading} disabled={disabled} />
      ) : (
        <DirectionalControls s={directional} onChange={onDirectionalChange}
          batchOptions={batchOptions} onRun={onRun} loading={loading} disabled={disabled} />
      )}

      {/* Shared controls: pre-filters → correlation → stability → date */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Min Positions:<HintTip text="Skip assets with fewer trades than this. 0 = no filter." /></label>
          <input type="number" min={0} value={shared.minTrades}
            onChange={(e) => onSharedChange({ minTrades: Math.max(0, Number(e.target.value)) })}
            className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs text-center" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Min PF:<HintTip text="Skip assets with Profit Factor below this. 0 = no filter." /></label>
          <input type="number" min={0} step={0.1} value={shared.minPf}
            onChange={(e) => onSharedChange({ minPf: Math.max(0, Number(e.target.value)) })}
            className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs text-center" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Min WR%:<HintTip text="Skip assets with Win Rate below this %. 0 = no filter." /></label>
          <input type="number" min={0} max={100} step={1} value={shared.minWr}
            onChange={(e) => onSharedChange({ minWr: Math.max(0, Math.min(100, Number(e.target.value))) })}
            className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs text-center" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Bybit Gate:<HintTip text="Check each trade's scaled position size against Bybit instrument limits. Binary = any trade exceeding limits drops the asset. Rate = drop asset if more than X% of trades exceed limits." /></label>
          <select value={shared.bybitSizeGate}
            onChange={(e) => onSharedChange({ bybitSizeGate: e.target.value as "off" | "binary" | "rate" })}
            className="bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs">
            <option value="off">Off</option>
            <option value="binary">Binary</option>
            <option value="rate">Rate %</option>
          </select>
          {shared.bybitSizeGate === "rate" && (
            <input type="number" min={0.1} max={100} step={1} value={shared.bybitSizeGateMaxPct}
              onChange={(e) => onSharedChange({ bybitSizeGateMaxPct: Math.max(0.1, Math.min(100, Number(e.target.value))) })}
              className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-1 text-xs text-center" />
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Max Asset PF:<HintTip text="Remove assets with PF above this value before optimization. Prevents one outlier asset (e.g. PF=10) from inflating the portfolio. 0 = no filter. Typical: 3-5." /></label>
          <input type="number" min={0} step={0.5} value={shared.maxAssetPf}
            onChange={(e) => onSharedChange({ maxAssetPf: Math.max(0, Number(e.target.value)) })}
            className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs text-center" />
          <label className="flex items-center gap-1 text-[10px] text-ag-text cursor-pointer"
            title="When checked, assets above the Max PF are KEPT but their PF is capped at this value when computing the portfolio's aggregate PF. This prevents one outlier from making a bad portfolio look good, without losing the asset entirely.">
            <input type="checkbox" checked={shared.capAssetPf} onChange={(e) => onSharedChange({ capAssetPf: e.target.checked })}
              className="rounded border-ag-border accent-ag-accent" />
            Cap
          </label>
        </div>
      </div>
      {/* Portfolio-level quality gates */}
      <div className="flex items-center gap-4 flex-wrap">
        <span className="text-[10px] text-ag-text-muted uppercase tracking-wider font-medium">Portfolio Gates</span>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Min PF:<HintTip text="Reject portfolios with aggregate Profit Factor below this. Applied AFTER the optimizer builds candidates. 0 = off. Typical: 1.2-1.5." /></label>
          <input type="number" min={0} step={0.1} value={shared.portfolioMinPf}
            onChange={(e) => onSharedChange({ portfolioMinPf: Math.max(0, Number(e.target.value)) })}
            className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs text-center" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Min WR%:<HintTip text="Reject portfolios with aggregate Win Rate below this %. 0 = off." /></label>
          <input type="number" min={0} max={100} step={1} value={shared.portfolioMinWr}
            onChange={(e) => onSharedChange({ portfolioMinWr: Math.max(0, Math.min(100, Number(e.target.value))) })}
            className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs text-center" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Extra Slip %<HintTip text="Stress test — adds extra slippage cost on top of backtests. Typical: 0.01-0.03% for large-caps, 0.05% for alts, 0.10%+ for small-caps. If the portfolio still works at 0.05-0.10%, it's robust." /></label>
          <input type="number" min={0} step={0.01} value={shared.extraSlippage}
            onChange={(e) => onSharedChange({ extraSlippage: Math.max(0, Number(e.target.value)) })}
            className="w-16 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs text-center" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Extra Comm %<HintTip text="Extra commission on top of backtest commission. Applied to both entry and exit." /></label>
          <input type="number" min={0} step={0.01} value={shared.extraCommission}
            onChange={(e) => onSharedChange({ extraCommission: Math.max(0, Number(e.target.value)) })}
            className="w-16 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs text-center" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Funding %/d<HintTip text="Daily funding cost for perps. Bybit typical: ~0.03%/day. Charged based on position size × days held." /></label>
          <input type="number" min={0} step={0.005} value={shared.fundingDaily}
            onChange={(e) => onSharedChange({ fundingDaily: Math.max(0, Number(e.target.value)) })}
            className="w-16 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs text-center" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Spread %<HintTip text="Bid-ask spread cost on entry + exit. Large-caps: 0.01%. Mid-caps: 0.02-0.05%. Small-caps: 0.05-0.10%." /></label>
          <input type="number" min={0} step={0.01} value={shared.spread}
            onChange={(e) => onSharedChange({ spread: Math.max(0, Number(e.target.value)) })}
            className="w-16 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs text-center" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Correlation:<HintTip text="Standard: compares all days. Trade Overlap: only compares days both assets traded (better for low-trade strategies)." /></label>
          <select value={shared.corrMethod} onChange={(e) => onSharedChange({ corrMethod: e.target.value })}
            className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
            <option value="standard">Standard</option>
            <option value="trade_overlap">Trade Overlap</option>
          </select>
          <UserNote noteKey={`corr:${shared.corrMethod}`} />
        </div>
        <div className="flex flex-col gap-0.5">
          <div className="flex items-center gap-1.5">
            <label className="text-xs text-ag-text-muted">Algorithm:<HintTip text="Iterative: objective-driven greedy (default). HRP: risk-parity clustering. PCA: factor-based. Quality First: top quality, drop correlated. Tournament: random drafts. Marginal Sharpe: risk-adjusted contribution. Ensemble: multi-method consensus." /></label>
            <select value={shared.divAlgo} onChange={(e) => onSharedChange({ divAlgo: e.target.value })}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
              {DIVERSIFICATION_ALGOS.map((a) => <option key={a.value} value={a.value} title={a.desc}>{a.label}</option>)}
            </select>
            <UserNote noteKey={`algo:${shared.divAlgo}`} />
          </div>
          {(() => { const sel = DIVERSIFICATION_ALGOS.find((a) => a.value === shared.divAlgo); return sel?.desc ? <span className="text-[10px] text-ag-text-muted/70 leading-tight">{sel.desc}</span> : null; })()}
        </div>
        {shared.divAlgo === "genetic" && (
          <div className="flex flex-col gap-1.5 pl-2 border-l-2 border-ag-accent/30">
            <span className="text-[10px] text-ag-accent font-medium">Genetic Algorithm Settings</span>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
              <div className="flex items-center gap-1.5">
                <label className="text-xs text-ag-text-muted whitespace-nowrap">Generations:<HintTip text="How many evolution cycles to run. More generations = better solutions but slower. 30-100 is typical." /></label>
                <input type="number" min={10} max={500} value={shared.geneticGenerations}
                  onChange={(e) => onSharedChange({ geneticGenerations: Math.max(10, Math.min(500, parseInt(e.target.value) || 50)) })}
                  className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs w-16" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-xs text-ag-text-muted whitespace-nowrap">Population:<HintTip text="Number of candidate portfolios in each generation. Larger = more diverse search. 50-300 is typical." /></label>
                <input type="number" min={20} max={1000} value={shared.geneticPopulation}
                  onChange={(e) => onSharedChange({ geneticPopulation: Math.max(20, Math.min(1000, parseInt(e.target.value) || 100)) })}
                  className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs w-16" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-xs text-ag-text-muted whitespace-nowrap">Mutation %:<HintTip text="Chance of randomly swapping assets in a portfolio. Higher = more exploration, lower = more stable. 5-20% typical." /></label>
                <input type="number" min={1} max={50} value={Math.round(shared.geneticMutationRate * 100)}
                  onChange={(e) => onSharedChange({ geneticMutationRate: Math.max(0.01, Math.min(0.50, (parseInt(e.target.value) || 10) / 100)) })}
                  className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs w-16" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-xs text-ag-text-muted whitespace-nowrap">Crossover %:<HintTip text="Chance of breeding two portfolios to create offspring. Higher = more mixing of good traits. 50-90% typical." /></label>
                <input type="number" min={10} max={100} value={Math.round(shared.geneticCrossoverRate * 100)}
                  onChange={(e) => onSharedChange({ geneticCrossoverRate: Math.max(0.10, Math.min(1.00, (parseInt(e.target.value) || 70) / 100)) })}
                  className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs w-16" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-xs text-ag-text-muted whitespace-nowrap">Elite %:<HintTip text="Percentage of top portfolios kept unchanged each generation. Protects best solutions. 5-20% typical." /></label>
                <input type="number" min={1} max={50} value={Math.round(shared.geneticElitePct * 100)}
                  onChange={(e) => onSharedChange({ geneticElitePct: Math.max(0.01, Math.min(0.50, (parseInt(e.target.value) || 10) / 100)) })}
                  className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs w-16" />
              </div>
            </div>
          </div>
        )}
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={shared.corrJitterEnabled} onChange={(e) => onSharedChange({ corrJitterEnabled: e.target.checked })}
            className="accent-ag-accent" />
          <span className="text-ag-text-muted">Corr Jitter<HintTip text="Add small random noise to the correlation matrix so each candidate sees slightly different correlations. Creates more diverse portfolios without changing the algorithm." /><UserNote noteKey="toggle:corr_jitter" /></span>
        </label>
        {shared.corrJitterEnabled && (
          <div className="flex items-center gap-1.5">
            <label className="text-xs text-ag-text-muted">Temperature:</label>
            <input type="range" min={0.01} max={0.10} step={0.01} value={shared.corrJitterTemperature}
              onChange={(e) => onSharedChange({ corrJitterTemperature: parseFloat(e.target.value) })}
              className="w-16 accent-ag-accent" />
            <span className="text-xs text-ag-text-muted">{shared.corrJitterTemperature.toFixed(2)}</span>
          </div>
        )}
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={shared.refinementEnabled} onChange={(e) => onSharedChange({ refinementEnabled: e.target.checked })}
            className="accent-ag-accent" />
          <span className="text-ag-text-muted">Refinement<HintTip text="After the optimizer builds each candidate, run a refinement pass to prune harmful assets and/or swap in better alternatives from the pool." /><UserNote noteKey="toggle:refinement" /></span>
        </label>
        {shared.refinementEnabled && (
          <div className="flex items-center gap-1.5">
            <select value={shared.refinementMode} onChange={(e) => onSharedChange({ refinementMode: e.target.value })}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
              <option value="prune_and_swap">Prune + Swap</option>
              <option value="prune">Prune Only</option>
              <option value="swap">Swap Only</option>
            </select>
          </div>
        )}
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={prioritizeStability} onChange={(e) => onStabilityChange(e.target.checked)}
            className="accent-ag-accent" />
          <span className="text-ag-text-muted">Prioritize Stability<HintTip text="Favor smoother equity curves — weights drawdown and volatility higher in scoring." /><UserNote noteKey="toggle:prioritize_stability" /></span>
        </label>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Diversity:<HintTip text="How different the returned portfolio candidates must be. Off = no constraint. Higher = candidates share fewer assets with each other." /></label>
          <select value={shared.maxOverlap} onChange={(e) => onSharedChange({ maxOverlap: e.target.value })}
            className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
            <option value="off">Off</option>
            <option value="0.70">Low</option>
            <option value="0.50">Medium</option>
            <option value="0.30">High</option>
            <option value="0.10">Max</option>
          </select>
        </div>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={shared.expandGreedyPool} onChange={(e) => onSharedChange({ expandGreedyPool: e.target.checked })}
            className="accent-ag-accent" />
          <span className="text-ag-text-muted">Expand Greedy Pool<HintTip text="ON: optimizer can pick ANY profitable asset, not just top seeds. More thorough. OFF: only picks from top-ranked seeds (faster but narrower)." /><UserNote noteKey="toggle:expand_greedy" /></span>
        </label>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={shared.seedBlindSelection} onChange={(e) => onSharedChange({ seedBlindSelection: e.target.checked })}
            className="accent-ag-accent" />
          <span className="text-ag-text-muted">Unbiased Diversity<HintTip text="When ON, Tournament, Quality First, Marginal Sharpe, and Ensemble treat all filtered pool assets equally — selection is driven purely by diversity and the algorithm's own logic, not seed rankings. Can produce more diverse portfolios. When OFF, these algorithms use seed rankings to prefer higher-quality assets." /><UserNote noteKey="toggle:seed_blind" /></span>
        </label>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={shared.streamlitLogic} onChange={(e) => onSharedChange({ streamlitLogic: e.target.checked })}
            className="accent-ag-accent" />
          <span className="text-ag-text-muted">Streamlit Logic (Pure Correlation)<HintTip text="Picks assets based ONLY on correlation (ignoring profit during selection). Profit scoring happens only in the final ranking step. Matches the Streamlit Dashboard logic." /><UserNote noteKey="toggle:streamlit_logic" /></span>
        </label>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Start Date:</label>
          <input type="date" value={shared.dateFrom}
            onChange={(e) => onSharedChange({ dateFrom: e.target.value })}
            className="bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs" />
        </div>
      </div>

      {/* Session Filter */}
      <SessionFilterConfig
        value={{
          enabled: shared.sessionFilterEnabled,
          mode: shared.sessionFilterMode,
          excludeDays: shared.sessionFilterExcludeDays,
          excludeHours: shared.sessionFilterExcludeHours,
          autoMinTrades: shared.sessionFilterAutoMinTrades,
          autoIncludeInsufficient: shared.sessionFilterAutoIncludeInsufficient,
          gradeOnCropped: shared.sessionFilterGradeOnCropped,
          savedMaskId: shared.sessionFilterSavedMaskId,
        }}
        onChange={(v: SessionFilterState) => onSharedChange({
          sessionFilterEnabled: v.enabled,
          sessionFilterMode: v.mode,
          sessionFilterExcludeDays: v.excludeDays,
          sessionFilterExcludeHours: v.excludeHours,
          sessionFilterAutoMinTrades: v.autoMinTrades,
          sessionFilterAutoIncludeInsufficient: v.autoIncludeInsufficient,
          sessionFilterGradeOnCropped: v.gradeOnCropped,
          sessionFilterSavedMaskId: v.savedMaskId,
        })}
      />

      {/* ── Overfitting Guard ────────────────────────────────────── */}
      <details className="group border border-ag-border rounded p-2">
        <summary className="cursor-pointer text-xs font-semibold text-ag-text hover:text-ag-accent select-none flex items-center gap-2">
          <span>Overfitting Guard</span>
          <HintTip text="Detect and penalize 'too good to be true' assets that are likely overfitted. WHEN TO USE: Always recommended when you have 5–25 trades per asset — few trades make metrics unreliable. Less critical with 50+ trades. Start with Log + Bayesian (gentle, safe combo), then add Z-Score or Normal Range if you suspect cherry-picking. WF Re-rank is the strongest single guard but works at portfolio level, not asset level." />
          <label className="ml-auto flex items-center gap-1 cursor-pointer" onClick={(e) => e.stopPropagation()}>
            <input type="checkbox" checked={shared.overfitGuardEnabled}
              onChange={(e) => onSharedChange({ overfitGuardEnabled: e.target.checked })}
              className="accent-ag-accent" />
            <span className="text-[10px] text-ag-text-muted">{shared.overfitGuardEnabled ? "ON" : "OFF"}</span>
          </label>
        </summary>
        {shared.overfitGuardEnabled && (
          <div className="mt-2 space-y-3 pl-1">
            {/* Asset-Level Guards */}
            <div className="space-y-2">
              <span className="text-[10px] font-semibold text-ag-text-muted uppercase tracking-wide">Asset-Level Guards</span>

              {/* Approach 1: Diminishing Returns (Log) */}
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1 text-xs cursor-pointer">
                  <input type="checkbox" checked={shared.ofLogEnabled}
                    onChange={(e) => onSharedChange({ ofLogEnabled: e.target.checked })}
                    className="accent-ag-accent" />
                  <span className="text-ag-text-muted">Diminishing Returns (Log)<HintTip text="Compresses extreme metric values logarithmically — PF going from 1.5→2.5 matters a lot, but 5→8 barely changes the score. BEST FOR: General-purpose guard, safe to always leave on. Gentle — doesn't remove assets, just reduces the bonus from extreme values. USE WHEN: You want a soft guard that doesn't aggressively punish outliers. SKIP WHEN: You already use Sigmoid (they do similar things — pick one)." /><UserNote noteKey="og:log" /></span>
                </label>
                {shared.ofLogEnabled && (
                  <div className="flex flex-wrap gap-1.5 text-[10px]">
                    {[
                      {k:"ofLogCapPf",l:"PF",tip:"Profit Factor cap. 0 = OFF. Range: 1.5–10. Default 4. Above this, improvements barely affect score. Realistic good PF is 1.5–3."},
                      {k:"ofLogCapSortino",l:"Sort",tip:"Sortino cap. 0 = OFF. Range: 1–8. Default 3. Sortino above 3 from few trades is often overfitted."},
                      {k:"ofLogCapPayoff",l:"Pay",tip:"Payoff Ratio cap. 0 = OFF. Range: 1–10. Default 3. How much bigger winners are vs losers. Set 0 if you use TSL (can produce legitimately high payoff)."},
                      {k:"ofLogCapWr",l:"WR%",tip:"Win Rate cap (%). 0 = OFF. Range: 40–95. Default 70. Win rates above 70% are rare in crypto — higher often means overfitting."},
                      {k:"ofLogCapSharpe",l:"Sha",tip:"Sharpe cap. 0 = OFF. Range: 0.5–5. Default 2.5. Sharpe above 2.5 is exceptional — above 4 is almost certainly overfitted."},
                      {k:"ofLogCapCalmar",l:"Cal",tip:"Calmar cap. 0 = OFF. Range: 1–10. Default 3. Return / max drawdown. Above 3 is great; above 5 is suspicious."},
                    ].map(({k,l,tip})=>(
                      <label key={k} className="flex items-center gap-0.5 text-ag-text-muted" title={tip}>
                        {l}
                        <input type="number" step="0.5" min="0" value={(shared as unknown as Record<string,unknown>)[k] as number}
                          onChange={(e) => onSharedChange({ [k]: Number(e.target.value) })}
                          title={tip}
                          className="w-10 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center text-[10px]" />
                      </label>
                    ))}
                  </div>
                )}
              </div>

              {/* Approach 2: Z-Score Suspicion */}
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1 text-xs cursor-pointer">
                  <input type="checkbox" checked={shared.ofZScoreEnabled}
                    onChange={(e) => onSharedChange({ ofZScoreEnabled: e.target.checked })}
                    className="accent-ag-accent" />
                  <span className="text-ag-text-muted">Z-Score Suspicion<HintTip text="Flags assets that are statistical outliers within their batch — the more extreme vs peers, the bigger the penalty. BEST FOR: Catching the 'one weird asset' that looks amazing but is just noise. Works best with 20+ assets per batch. USE WHEN: You suspect some assets have inflated metrics from lucky trades. Pairs well with Bayesian. SKIP WHEN: You have very few assets per batch (<10) — z-scores need a group to compare against." /><UserNote noteKey="og:zscore" /></span>
                </label>
                {shared.ofZScoreEnabled && (
                  <div className="flex gap-2 text-[10px]">
                    <label className="flex items-center gap-0.5 text-ag-text-muted"
                      title="How many standard deviations above normal before flagging. 0 = OFF. Range: 1.0–3.0. Default 2. Lower = stricter. 1.5 = aggressive, 2.0 = balanced, 2.5 = lenient.">
                      Threshold
                      <input type="number" step="0.1" min="0" max="3" value={shared.ofZThreshold}
                        onChange={(e) => onSharedChange({ ofZThreshold: Number(e.target.value) })}
                        title="How many standard deviations above normal before flagging. 0 = OFF. Range: 1.0–3.0. Default 2. Lower = stricter. 1.5 = aggressive, 2.0 = balanced, 2.5 = lenient."
                        className="w-10 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center text-[10px]" />
                    </label>
                    <label className="flex items-center gap-0.5 text-ag-text-muted"
                      title="How hard to penalize each unit of excess z-score. 0 = OFF. Range: 0.05–0.50. Default 0.15. Higher = more aggressive. 0.10 = gentle, 0.15 = balanced, 0.30 = harsh.">
                      Strength
                      <input type="number" step="0.05" min="0" max="0.5" value={shared.ofZPenaltyStrength}
                        onChange={(e) => onSharedChange({ ofZPenaltyStrength: Number(e.target.value) })}
                        title="How hard to penalize each unit of excess z-score. 0 = OFF. Range: 0.05–0.50. Default 0.15. Higher = more aggressive. 0.10 = gentle, 0.15 = balanced, 0.30 = harsh."
                        className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center text-[10px]" />
                    </label>
                  </div>
                )}
              </div>

              {/* Approach 3: Bayesian Shrinkage */}
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1 text-xs cursor-pointer">
                  <input type="checkbox" checked={shared.ofBayesEnabled}
                    onChange={(e) => onSharedChange({ ofBayesEnabled: e.target.checked })}
                    className="accent-ag-accent" />
                  <span className="text-ag-text-muted">Bayesian Shrinkage<HintTip text="Pulls extreme metrics toward the batch average — stronger pull when fewer trades (less confidence). PF 8.0 from 5 trades gets pulled hard toward average; PF 8.0 from 50 trades barely moves. BEST FOR: Low-trade strategies (5–20 trades). The single best guard for small sample sizes. USE WHEN: Your strategy has few trades per asset and you worry that high metrics are just luck. SKIP WHEN: You have 50+ trades per asset — with many trades, metrics are already reliable." /><UserNote noteKey="og:bayes" /></span>
                </label>
                {shared.ofBayesEnabled && (
                  <label className="flex items-center gap-0.5 text-[10px] text-ag-text-muted"
                    title="'Pseudo-trades' added to pull metrics toward the batch average. 0 = OFF. Range: 1–30. Default 5. Higher = stronger shrinkage. k=3: light pull, k=5: balanced, k=15: heavy pull. With 10 real trades and k=5, metrics are 67% real + 33% batch average.">
                    k =
                    <input type="number" step="1" min="0" max="30" value={shared.ofBayesK}
                      onChange={(e) => onSharedChange({ ofBayesK: Number(e.target.value) })}
                      title="'Pseudo-trades' added to pull metrics toward the batch average. 0 = OFF. Range: 1–30. Default 5. Higher = stronger shrinkage. k=3: light pull, k=5: balanced, k=15: heavy pull. With 10 real trades and k=5, metrics are 67% real + 33% batch average."
                      className="w-10 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center text-[10px]" />
                  </label>
                )}
              </div>

              {/* Approach 4: Metric Capping (Sigmoid) */}
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1 text-xs cursor-pointer">
                  <input type="checkbox" checked={shared.ofSigmoidEnabled}
                    onChange={(e) => onSharedChange({ ofSigmoidEnabled: e.target.checked })}
                    className="accent-ag-accent" />
                  <span className="text-ag-text-muted">Metric Capping (Sigmoid)<HintTip text="Hard ceiling on metrics using a smooth S-curve — PF 5 and PF 10 become virtually equal. Sharper cutoff than Log. BEST FOR: When you want to completely neutralize extreme values. More aggressive than Log — flattens everything above the cap. USE WHEN: You see extreme metrics (PF>6, Sharpe>4) that you're sure are overfitted. Good for aggressive overfitting control. SKIP WHEN: You already use Log (they overlap — pick the one matching your aggression level). Log = gentle, Sigmoid = strict." /><UserNote noteKey="og:sigmoid" /></span>
                </label>
                {shared.ofSigmoidEnabled && (
                  <div className="flex flex-wrap gap-1.5 text-[10px]">
                    {[
                      {k:"ofSigCapPf",l:"PF",tip:"Sigmoid midpoint for Profit Factor. 0 = OFF. Range: 1.5–10. Default 4. Values above this get flattened hard. PF 5 and PF 10 score almost the same."},
                      {k:"ofSigCapSortino",l:"Sort",tip:"Sigmoid midpoint for Sortino. 0 = OFF. Range: 1–8. Default 3. Above 3 the curve plateaus — no extra credit for extreme values."},
                      {k:"ofSigCapPayoff",l:"Pay",tip:"Sigmoid midpoint for Payoff Ratio. 0 = OFF. Range: 1–10. Default 3. Set 0 if you use TSL (can produce legitimately high payoff)."},
                      {k:"ofSigCapWr",l:"WR%",tip:"Sigmoid midpoint for Win Rate (%). 0 = OFF. Range: 40–95. Default 70. Win rates above 70% get virtually no extra score."},
                      {k:"ofSigCapSharpe",l:"Sha",tip:"Sigmoid midpoint for Sharpe. 0 = OFF. Range: 0.5–5. Default 2.5. Above 2.5 all Sharpe values score the same."},
                      {k:"ofSigCapCalmar",l:"Cal",tip:"Sigmoid midpoint for Calmar. 0 = OFF. Range: 1–10. Default 3. Hard cap — Calmar 4 and Calmar 20 score identically."},
                    ].map(({k,l,tip})=>(
                      <label key={k} className="flex items-center gap-0.5 text-ag-text-muted" title={tip}>
                        {l}
                        <input type="number" step="0.5" min="0" value={(shared as unknown as Record<string,unknown>)[k] as number}
                          onChange={(e) => onSharedChange({ [k]: Number(e.target.value) })}
                          title={tip}
                          className="w-10 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center text-[10px]" />
                      </label>
                    ))}
                  </div>
                )}
              </div>

              {/* Approach 5: Normal Range Bonus */}
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1 text-xs cursor-pointer">
                  <input type="checkbox" checked={shared.ofNormalRangeEnabled}
                    onChange={(e) => onSharedChange({ ofNormalRangeEnabled: e.target.checked })}
                    className="accent-ag-accent" />
                  <span className="text-ag-text-muted">Normal Range Bonus<HintTip text="Rewards 'normal' performance and penalizes extremes in BOTH directions — too bad AND too good get penalized. BEST FOR: Catching curve-fitted assets with suspiciously low drawdowns or impossibly high win rates. Unlike Log/Sigmoid which only cap the upside, this also flags assets that are 'too perfect'. USE WHEN: You suspect some assets look flawless because the strategy was tailored to their history. Good companion to Z-Score. SKIP WHEN: You have very few assets per batch — the percentile boundaries need enough data to be meaningful." /><UserNote noteKey="og:normal_range" /></span>
                </label>
                {shared.ofNormalRangeEnabled && (
                  <div className="flex flex-wrap gap-1.5 text-[10px]">
                    <label className="flex items-center gap-0.5 text-ag-text-muted"
                      title="Bottom percentile boundary — assets below this are penalized (too bad). 0 = OFF. Range: 5–45. Default 20. The bottom 20% of assets in each batch get a score penalty.">
                      Low%
                      <input type="number" step="5" min="0" max="45" value={shared.ofNrPctLow}
                        onChange={(e) => onSharedChange({ ofNrPctLow: Number(e.target.value) })}
                        title="Bottom percentile boundary — assets below this are penalized (too bad). 0 = OFF. Range: 5–45. Default 20. The bottom 20% of assets in each batch get a score penalty."
                        className="w-10 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center text-[10px]" />
                    </label>
                    <label className="flex items-center gap-0.5 text-ag-text-muted"
                      title="Top percentile boundary — assets above this are penalized (too good = suspicious). 0 = OFF. Range: 50–90. Default 60. The top 40% most extreme assets get penalized. Lower = stricter.">
                      High%
                      <input type="number" step="5" min="0" max="90" value={shared.ofNrPctHigh}
                        onChange={(e) => onSharedChange({ ofNrPctHigh: Number(e.target.value) })}
                        title="Top percentile boundary — assets above this are penalized (too good = suspicious). 0 = OFF. Range: 50–90. Default 60. The top 40% most extreme assets get penalized. Lower = stricter."
                        className="w-10 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center text-[10px]" />
                    </label>
                    <label className="flex items-center gap-0.5 text-ag-text-muted"
                      title="Penalty multiplier for assets outside the normal range. 0 = OFF. Range: 0.10–0.80. Default 0.3. Higher = harsher. 0.1 = gentle, 0.3 = noticeable, 0.6 = aggressive.">
                      Scale
                      <input type="number" step="0.05" min="0" max="0.8" value={shared.ofNrPenaltyScale}
                        onChange={(e) => onSharedChange({ ofNrPenaltyScale: Number(e.target.value) })}
                        title="Penalty multiplier for assets outside the normal range. 0 = OFF. Range: 0.10–0.80. Default 0.3. Higher = harsher. 0.1 = gentle, 0.3 = noticeable, 0.6 = aggressive."
                        className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center text-[10px]" />
                    </label>
                    <label className="flex items-center gap-0.5 text-ag-text-muted"
                      title="Score boost for assets inside the normal range. 0 = no bonus (penalty-only mode). Range: 0–0.30. Default 0.1. 0.05 = subtle, 0.1 = balanced, 0.2 = strong preference for normal.">
                      Bonus
                      <input type="number" step="0.05" min="0" max="0.3" value={shared.ofNrBonus}
                        onChange={(e) => onSharedChange({ ofNrBonus: Number(e.target.value) })}
                        title="Score boost for assets inside the normal range. 0 = no bonus (penalty-only mode). Range: 0–0.30. Default 0.1. 0.05 = subtle, 0.1 = balanced, 0.2 = strong preference for normal."
                        className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center text-[10px]" />
                    </label>
                  </div>
                )}
              </div>
            </div>

            {/* Portfolio-Level Guards */}
            <div className="space-y-2 border-t border-ag-border pt-2">
              <span className="text-[10px] font-semibold text-ag-text-muted uppercase tracking-wide">Portfolio-Level Guards</span>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1 text-xs cursor-pointer">
                  <input type="checkbox" checked={shared.ofWfRerankEnabled}
                    onChange={(e) => onSharedChange({ ofWfRerankEnabled: e.target.checked })}
                    className="accent-ag-accent" />
                  <span className="text-ag-text-muted">Re-rank by WF Validation<HintTip text="After the optimizer builds portfolio candidates, re-ranks them by performance on held-out data (last 30% of IS period). Overfitted portfolios degrade; robust ones hold up. BEST FOR: The strongest single overfitting guard — works at portfolio level, not asset level. Gold standard when you have enough IS data. USE WHEN: Always recommended as a final safety net. Catches overfitting that asset-level guards miss. SKIP WHEN: Very short IS period (<6 months) — not enough data to split meaningfully. Also slightly slower since it re-evaluates all candidates." /><UserNote noteKey="og:wf_rerank" /></span>
                </label>
                {shared.ofWfRerankEnabled && (
                  <div className="flex gap-2 text-[10px]">
                    <label className="flex items-center gap-0.5 text-ag-text-muted"
                      title="% of IS data used for training. The rest is held-out validation. 0 = OFF. Range: 50–85. Default 70. 70 = standard (70% train, 30% validate). 60 = conservative, 70 = balanced, 80 = more train data.">
                      Split%
                      <input type="number" step="5" min="0" max="85" value={shared.ofWfSplitPct}
                        onChange={(e) => onSharedChange({ ofWfSplitPct: Number(e.target.value) })}
                        title="% of IS data used for training. The rest is held-out validation. 0 = OFF. Range: 50–85. Default 70. 70 = standard (70% train, 30% validate). 60 = conservative, 70 = balanced, 80 = more train data."
                        className="w-10 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center text-[10px]" />
                    </label>
                    <label className="flex items-center gap-0.5 text-ag-text-muted"
                      title="Which metric to use when scoring portfolios on the validation window. Stability = smoothest equity (recommended). Sortino = best risk-adjusted return. PF = most profitable. Sharpe = return/volatility.">
                      Metric
                      <select value={shared.ofWfValidationMetric}
                        onChange={(e) => onSharedChange({ ofWfValidationMetric: e.target.value })}
                        title="Which metric to use when scoring portfolios on the validation window. Stability = smoothest equity (recommended). Sortino = best risk-adjusted return. PF = most profitable. Sharpe = return/volatility."
                        className="bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-[10px]">
                        <option value="stability" title="Smoothest equity curve on validation data. Best all-rounder — recommended for most users.">Stability</option>
                        <option value="sortino" title="Best return per unit of downside risk on validation data. Good for strategies that accept upside volatility.">Sortino</option>
                        <option value="pf" title="Highest profit factor on validation data. Simple: total wins / total losses.">Profit Factor</option>
                        <option value="net_profit" title="Highest raw profit on validation data. Ignores risk — only cares about absolute returns.">Net Profit</option>
                        <option value="sharpe" title="Best return per unit of total volatility on validation data. Penalizes ALL volatility, including upside.">Sharpe</option>
                      </select>
                    </label>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </details>

      {/* Seed Ranking — grouped by category */}
      <div>
        <div className="flex items-center gap-3 mb-1.5">
          <label className="text-xs text-ag-text-muted">
            Seed Ranking<HintTip text="Pick which metrics rank your assets before the optimizer runs. Top-ranked assets become starting points (seeds) for portfolio construction. TIPS: Use 1–3 seeds for focused ranking. Using many seeds dilutes each one's influence. RECOMMENDED: Sortino + Stability for most strategies. Add Trend Score or MR Score if you know your strategy type. Add IS-WFE if you suspect overfitting." />
          </label>
          <div className="flex items-center gap-1">
            <label className="text-[10px] text-ag-text-muted">Max Seeds:</label>
            <input type="number" min={5} max={200} value={shared.maxSeeds}
              onChange={(e) => onSharedChange({ maxSeeds: Math.max(5, Math.min(200, Number(e.target.value))) })}
              className="w-12 bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-xs text-center" />
          </div>
        </div>
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
                    <button onClick={() => toggleSeed(m.value)}
                      title={m.desc}
                      className={`px-2.5 py-1 rounded text-xs font-medium whitespace-nowrap transition-colors ${shared.seedMetrics.includes(m.value)
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
        {/* Recency config — shown when recency seed is selected */}
        {shared.seedMetrics.includes("recency") && (
          <div className="flex items-center gap-3 flex-wrap bg-ag-dark/50 rounded-lg px-3 py-2 mt-2">
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={shared.recencyEnabled}
                onChange={(e) => onSharedChange({ recencyEnabled: e.target.checked })}
                className="accent-ag-accent" />
              <span className="text-ag-text-muted">Enable Recency<HintTip text="Weight recent trades higher in scoring. Assets still performing well NOW rank better." /><UserNote noteKey="toggle:recency" /></span>
            </label>
            {shared.recencyEnabled && (
              <>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Mode:</label>
                  <select value={shared.recencyMode}
                    onChange={(e) => onSharedChange({ recencyMode: e.target.value })}
                    className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-xs">
                    <option value="gradual">Gradual Decay</option>
                    <option value="batch">Recent Batch</option>
                  </select>
                </div>
                {shared.recencyMode === "batch" && (
                  <div className="flex items-center gap-1.5">
                    <label className="text-[10px] text-ag-text-muted">Batch Size:</label>
                    <input type="number" min={3} max={100} value={shared.recencyBatchSize}
                      onChange={(e) => onSharedChange({ recencyBatchSize: Math.max(3, Math.min(100, Number(e.target.value))) })}
                      className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                  </div>
                )}
                {shared.recencyMode === "batch" && (
                  <div className="flex items-center gap-1.5">
                    <label className="text-[10px] text-ag-text-muted">Boost:</label>
                    <input type="number" min={1} max={10} step={0.5} value={shared.recencyBoostFactor}
                      onChange={(e) => onSharedChange({ recencyBoostFactor: Math.max(1, Math.min(10, Number(e.target.value))) })}
                      className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                  </div>
                )}
              </>
            )}
          </div>
        )}
        {/* Avg Range config — shown when avg_range seed is selected */}
        {shared.seedMetrics.includes("avg_range") && (
          <div className="flex items-center gap-3 flex-wrap bg-ag-dark/50 rounded-lg px-3 py-2 mt-2">
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-ag-text-muted">Long:</label>
              <input type="number" min={0} max={20} step={0.5}
                value={shared.avgRangeBullThreshold}
                onChange={(e) => onSharedChange({ avgRangeBullThreshold: Math.max(0, Number(e.target.value)) })}
                className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-xs text-center" />
            </div>
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-ag-text-muted">Short:</label>
              <input type="number" min={0} max={20} step={0.5}
                value={shared.avgRangeBearThreshold}
                onChange={(e) => onSharedChange({ avgRangeBearThreshold: Math.max(0, Number(e.target.value)) })}
                className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-xs text-center" />
            </div>
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-ag-text-muted">L+S:</label>
              <input type="number" min={0} max={20} step={0.5}
                value={shared.avgRangeAllThreshold}
                onChange={(e) => onSharedChange({ avgRangeAllThreshold: Math.max(0, Number(e.target.value)) })}
                className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-xs text-center" />
            </div>
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={shared.avgRangeFilterMode}
                onChange={(e) => onSharedChange({ avgRangeFilterMode: e.target.checked })}
                className="accent-ag-accent" />
              <span className="text-ag-text-muted">Use this as a filter<HintTip text="ON: remove assets below thresholds entirely. OFF: keep them but rank them lower." /></span>
            </label>
          </div>
        )}
        {/* Asset Quality config — shown when asset_quality seed is selected */}
        {shared.seedMetrics.includes("asset_quality") && (
          <div className="bg-ag-dark/50 rounded-lg px-3 py-2 mt-2 space-y-2">
            <label className="block text-[10px] text-ag-text-muted mb-1">
              Asset Quality Config<HintTip text="OHLCV-based quality scoring. Mode picks Trend (Hurst + TER) or Mean-Rev (Hurst + VR + CR) thresholds." />
            </label>
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Mode:</label>
                <select value={shared.assetQualityMode} onChange={(e) => onSharedChange({ assetQualityMode: e.target.value })}
                  className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs">
                  <option value="trend">Trend-Following</option>
                  <option value="meanrev">Mean-Reversion</option>
                </select>
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Grading:</label>
                <select value={shared.assetQualityGradeMode} onChange={(e) => onSharedChange({ assetQualityGradeMode: e.target.value })}
                  className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs">
                  <option value="absolute">Absolute</option>
                  <option value="autozoom">Auto-Zoom</option>
                </select>
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Min Grade:</label>
                <select value={shared.assetQualityMinGrade} onChange={(e) => onSharedChange({ assetQualityMinGrade: e.target.value })}
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
                <input type="number" min={0} max={500} step={1} value={shared.assetQualityMinTrades}
                  onChange={(e) => onSharedChange({ assetQualityMinTrades: Math.max(0, Number(e.target.value)) })}
                  className="w-14 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
              </div>
            </div>
            {shared.assetQualityMode === "trend" ? (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-1">Min Hurst</label>
                  <input type="number" min={0.3} max={0.8} step={0.01} value={shared.assetQualityHurstMin}
                    onChange={(e) => onSharedChange({ assetQualityHurstMin: Number(e.target.value) })}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-1">TER Percentile</label>
                  <input type="number" min={0} max={75} step={5} value={shared.assetQualityTerPercentile}
                    onChange={(e) => onSharedChange({ assetQualityTerPercentile: Number(e.target.value) })}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-1">Max Hurst</label>
                  <input type="number" min={0.3} max={0.7} step={0.01} value={shared.assetQualityHurstMax}
                    onChange={(e) => onSharedChange({ assetQualityHurstMax: Number(e.target.value) })}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-1">Max VR</label>
                  <input type="number" min={0.5} max={1.5} step={0.05} value={shared.assetQualityVrMax}
                    onChange={(e) => onSharedChange({ assetQualityVrMax: Number(e.target.value) })}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-1">Min Cross Rate</label>
                  <input type="number" min={0} max={0.5} step={0.01} value={shared.assetQualityMinCr}
                    onChange={(e) => onSharedChange({ assetQualityMinCr: Number(e.target.value) })}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
              </div>
            )}
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={shared.assetQualityFilter}
                onChange={(e) => onSharedChange({ assetQualityFilter: e.target.checked })}
                className="accent-ag-accent" />
              <span className="text-ag-text-muted">Use as filter<HintTip text="ON: remove assets that fail thresholds entirely. OFF: keep them but rank lower." /></span>
            </label>
          </div>
        )}
        {/* Strategy Quality config — shown when strat_quality seed is selected */}
        {shared.seedMetrics.includes("strat_quality") && (
          <div className="bg-ag-dark/50 rounded-lg px-3 py-2 mt-2 space-y-2">
            <label className="block text-[10px] text-ag-text-muted mb-1">
              Strategy Quality Config<HintTip text="PnL-based quality scoring. Mode picks Trend or Mean-Rev grade. Always absolute (no zoom)." />
            </label>
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Mode:</label>
                <select value={shared.stratQualityMode} onChange={(e) => onSharedChange({ stratQualityMode: e.target.value })}
                  className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs">
                  <option value="trend">Trend-Following</option>
                  <option value="meanrev">Mean-Reversion</option>
                </select>
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Min Grade:</label>
                <select value={shared.stratQualityMinGrade} onChange={(e) => onSharedChange({ stratQualityMinGrade: e.target.value })}
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
              <input type="checkbox" checked={shared.stratQualityFilter}
                onChange={(e) => onSharedChange({ stratQualityFilter: e.target.checked })}
                className="accent-ag-accent" />
              <span className="text-ag-text-muted">Use as filter<HintTip text="ON: remove assets below min grade entirely. OFF: keep them but rank lower." /></span>
            </label>
          </div>
        )}
        {/* IS-WFE config — shown when is_wfe seed is selected */}
        {shared.seedMetrics.includes("is_wfe") && (
          <div className="bg-ag-dark/50 rounded-lg px-3 py-2 mt-2 space-y-2">
            <label className="block text-[10px] text-ag-text-muted mb-1">
              IS-WFE Config<HintTip text="Splits each asset's daily PnL into In-Sample and Out-of-Sample windows, then computes WFE = (OOS daily profit rate) / (IS daily profit rate). Higher WFE = the edge held up out of sample." />
            </label>
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">IS:OOS Split:<HintTip text="How to divide the data. 60:40 means 60% In-Sample, 40% Out-of-Sample. Higher IS = more training data but less OOS to validate." /></label>
                <select value={shared.isWfeIsPct} onChange={(e) => onSharedChange({ isWfeIsPct: Number(e.target.value) })}
                  className="bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs">
                  <option value={50}>50:50</option>
                  <option value={60}>60:40</option>
                  <option value={70}>70:30</option>
                  <option value={75}>75:25</option>
                  <option value={80}>80:20</option>
                </select>
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Min IS Trades:<HintTip text="Minimum trades in the IS window for the asset to be scored. 0 = no minimum." /></label>
                <input type="number" min={0} max={500} step={1} value={shared.isWfeMinTrades}
                  onChange={(e) => onSharedChange({ isWfeMinTrades: Math.max(0, Number(e.target.value)) })}
                  className="w-14 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
              </div>
            </div>
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={shared.isWfeFilter}
                onChange={(e) => onSharedChange({ isWfeFilter: e.target.checked })}
                className="accent-ag-accent" />
              <span className="text-ag-text-muted">Use as filter<HintTip text="ON: remove assets below Min WFE entirely. OFF: keep them but rank them lower." /></span>
            </label>
            {shared.isWfeFilter && (
              <div className="flex items-center gap-1.5 ml-5">
                <label className="text-[10px] text-ag-text-muted">Min WFE:<HintTip text="Minimum WFE to pass. 0.5 = OOS must retain at least 50% of IS daily profit rate." /></label>
                <input type="number" min={0} max={2} step={0.1} value={shared.isWfeMinWfe}
                  onChange={(e) => onSharedChange({ isWfeMinWfe: Math.max(0, Number(e.target.value)) })}
                  className="w-14 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
              </div>
            )}
          </div>
        )}
        {/* Seed Weights — shown when 2+ metrics selected */}
        {shared.seedMetrics.length > 1 && (
          <div className="bg-ag-dark/50 rounded-lg px-3 py-2 mt-2">
            <label className="block text-[10px] text-ag-text-muted mb-1.5">
              Seed Weights<HintTip text="How much each metric matters in ranking. Higher weight = more influence. Default: 1 for all." />
            </label>
            <div className="flex items-center gap-3 flex-wrap">
              {shared.seedMetrics.map((m) => {
                const meta = SEED_METRICS.find(s => s.value === m);
                return (
                <div key={m} className="flex items-center gap-1">
                  <span className="text-[10px] text-ag-text-muted" title={meta?.desc}>{meta?.label || m}:</span>
                  <input type="number" min={0.5} max={3} step={0.5}
                    value={shared.seedWeights[m] ?? 1.0}
                    onChange={(e) => onSharedChange({
                      seedWeights: { ...shared.seedWeights, [m]: Math.max(0.5, Math.min(3, Number(e.target.value))) }
                    })}
                    className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-xs text-center" />
                </div>
                );
              })}
            </div>
          </div>
        )}
        {/* Seed Prior */}
        <div className="flex items-center gap-2 mt-2">
          <label className="flex items-center gap-1.5 text-xs cursor-pointer">
            <input type="checkbox" checked={shared.seedPriorWeight > 0}
              onChange={(e) => onSharedChange({ seedPriorWeight: e.target.checked ? 0.15 : 0 })}
              className="accent-ag-accent" />
            <span className="text-ag-text-muted">Seed Prior<HintTip text="When two assets score similarly, prefer the one with a better seed rank. Acts as a tiebreaker. Recommended: 0.10–0.20." /><UserNote noteKey="toggle:seed_prior" /></span>
          </label>
          {shared.seedPriorWeight > 0 && (
            <input type="number" min={0.01} max={0.30} step={0.01}
              value={shared.seedPriorWeight}
              onChange={(e) => onSharedChange({ seedPriorWeight: Math.max(0.01, Math.min(0.30, Number(e.target.value))) })}
              className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-xs text-center" />
          )}
        </div>
      </div>

      {/* Pre-Filter (Structural Quality) */}
      <div className="border-t border-ag-border pt-3 mt-3">
        <label className="flex items-center gap-2 text-xs cursor-pointer mb-2">
          <input type="checkbox" checked={shared.prefilterEnabled}
            onChange={(e) => onSharedChange({ prefilterEnabled: e.target.checked })}
            className="accent-ag-accent" />
          <span className="font-medium">Pre-Filter (Structural Quality)<HintTip text="Remove assets that don't fit your strategy type before optimization starts. Trend mode: removes non-trending assets. Mean-Rev mode: removes trending assets." /><UserNote noteKey="toggle:prefilter" /></span>
        </label>
        {shared.prefilterEnabled && (
          <div className="space-y-2 ml-5">
            <div className="flex items-center gap-3">
              <label className="text-[10px] text-ag-text-muted">Mode:</label>
              <select value={shared.prefilterMode}
                onChange={(e) => onSharedChange({ prefilterMode: e.target.value })}
                className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
                <option value="trend">Trend-Following</option>
                <option value="meanrev">Mean-Reversion</option>
              </select>
              <UserNote noteKey={`prefilter_mode:${shared.prefilterMode}`} />
            </div>
            {shared.prefilterMode === "trend" ? (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-1">Min Hurst</label>
                  <input type="number" min={0.3} max={0.8} step={0.01} value={shared.prefilterHurstMin}
                    onChange={(e) => onSharedChange({ prefilterHurstMin: Number(e.target.value) })}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-1">TER Percentile</label>
                  <input type="number" min={0} max={75} step={5} value={shared.prefilterTerPercentile}
                    onChange={(e) => onSharedChange({ prefilterTerPercentile: Number(e.target.value) })}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-1">Max Hurst</label>
                  <input type="number" min={0.3} max={0.7} step={0.01} value={shared.prefilterHurstMax}
                    onChange={(e) => onSharedChange({ prefilterHurstMax: Number(e.target.value) })}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-1">Max VR</label>
                  <input type="number" min={0.5} max={1.5} step={0.05} value={shared.prefilterVrMax}
                    onChange={(e) => onSharedChange({ prefilterVrMax: Number(e.target.value) })}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-1">Min Cross Rate</label>
                  <input type="number" min={0} max={0.5} step={0.01} value={shared.prefilterMinCrossingRate}
                    onChange={(e) => onSharedChange({ prefilterMinCrossingRate: Number(e.target.value) })}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* AC Post-Filter */}
      <div className="border-t border-ag-border pt-3 mt-3">
        <label className="flex items-center gap-2 text-xs cursor-pointer mb-2">
          <input type="checkbox" checked={shared.acPostfilter}
            onChange={(e) => onSharedChange({ acPostfilter: e.target.checked })}
            className="accent-ag-accent" />
          <span className="font-medium">AC Post-Filter<HintTip text="After optimization, flags assets whose price behavior doesn't match the strategy type (e.g. a mean-reverting asset in a trend portfolio)." /><UserNote noteKey="toggle:ac_postfilter" /></span>
        </label>
        {shared.acPostfilter && (
          <div className="flex items-center gap-3 ml-5">
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-ag-text-muted">Type:</label>
              <select value={shared.acPostfilterType}
                onChange={(e) => onSharedChange({ acPostfilterType: e.target.value })}
                className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
                <option value="trend">Trend</option>
                <option value="meanrev">Mean-Rev</option>
              </select>
              <UserNote noteKey={`ac_type:${shared.acPostfilterType}`} />
            </div>
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-ag-text-muted">Lag:</label>
              <input type="number" min={1} max={10} value={shared.acLag}
                onChange={(e) => onSharedChange({ acLag: Math.max(1, Number(e.target.value)) })}
                className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-1 text-xs text-center" />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function AutoControls({ s, onChange, onRun, loading, disabled }: {
  s: AutoSettings; onChange: (p: Partial<AutoSettings>) => void;
  onRun: () => void; loading: boolean; disabled: boolean;
}) {
  return (
    <div className="space-y-3">
      {/* Sub-mode: Combined vs L/S Split */}
      <div className="flex items-center gap-3">
        <label className="text-xs text-ag-text-muted">Sub-mode:<HintTip text="Combined: pick the best N assets from the full pool (any direction). L/S Split: pick N longs and N shorts separately, then combine." /></label>
        <button onClick={() => onChange({ splitMode: false })}
          className={`px-3 py-1 text-xs rounded font-medium transition-colors ${!s.splitMode ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted bg-ag-dark border border-ag-border"}`}>
          Combined
        </button>
        <button onClick={() => onChange({ splitMode: true })}
          className={`px-3 py-1 text-xs rounded font-medium transition-colors ${s.splitMode ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted bg-ag-dark border border-ag-border"}`}>
          L/S Split
        </button>
      </div>

      {s.splitMode ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-7 gap-3">
          <NumInput label="N Longs" value={s.nLongs} min={1} max={50} onChange={(v) => onChange({ nLongs: v })} hint="Long-side assets to include." />
          <NumInput label="N Shorts" value={s.nShorts} min={1} max={50} onChange={(v) => onChange({ nShorts: v })} hint="Short-side assets to include." />
          <SelectInput label="Within" value={s.withinObj} options={ALL_OBJECTIVES}
            onChange={(v) => onChange({ withinObj: v })} hint="How to rank assets within each direction." />
          <SelectInput label="Combined" value={s.combinedObj} options={ALL_OBJECTIVES}
            onChange={(v) => onChange({ combinedObj: v })} hint="How to score the final combined portfolio." />
          <NumInput label="Pool / Batch" value={s.poolPerBatch} min={0} max={200} onChange={(v) => onChange({ poolPerBatch: v })} hint="Assets to keep per batch before combining. 0 = auto. Bigger = more diversity." />
          <NumInput label="Results" value={s.nPortfolios} min={1} max={10} onChange={(v) => onChange({ nPortfolios: v })} hint="Number of portfolio candidates to generate." />
          <div className="flex items-end">
            <button onClick={onRun} disabled={loading || disabled}
              className="w-full px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
              {loading ? "Building..." : "Build"}
            </button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <NumInput label="N Assets" value={s.nAssets} min={2} max={100} onChange={(v) => onChange({ nAssets: v })} hint="Total assets to pick (any direction)." />
          <SelectInput label="Objective" value={s.combinedObj} options={ALL_OBJECTIVES}
            onChange={(v) => onChange({ combinedObj: v })} hint="How to score the final portfolio." />
          <NumInput label="Pool / Batch" value={s.poolPerBatch} min={0} max={200} onChange={(v) => onChange({ poolPerBatch: v })} hint="Assets to keep per batch before combining. 0 = auto. Bigger = more diversity." />
          <NumInput label="Results" value={s.nPortfolios} min={1} max={10} onChange={(v) => onChange({ nPortfolios: v })} hint="Number of portfolio candidates to generate." />
          <div className="flex items-end">
            <button onClick={onRun} disabled={loading || disabled}
              className="w-full px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
              {loading ? "Building..." : "Build"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ManualControls({ s, onChange, nBatches, batchOptions, onRun, loading, disabled }: {
  s: ManualSettings; onChange: (p: Partial<ManualSettings>) => void;
  nBatches: number; batchOptions: { id: number; label: string }[];
  onRun: () => void; loading: boolean; disabled: boolean;
}) {
  return (
    <div className="space-y-3">
      {/* Strategy Groups (L2 Pairing) */}
      <L2GroupEditor batches={batchOptions} groups={s.l2Groups}
        onGroupsChange={(g) => onChange({ l2Groups: g })} />

      {/* Classic / Pool mode toggle */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-ag-text-muted">Pipeline:<HintTip text="Classic: L1 optimizes per batch (N Longs + N Shorts), L2 groups variants, L3 picks best combo. Pool: L1 seed-ranks per batch (no optimizer), L2 optimizes across grouped pools, L3 mix-and-matches L2 selections." /></span>
        <button onClick={() => onChange({ poolMode: false })}
          className={`px-3 py-1 rounded text-xs font-medium transition-colors ${!s.poolMode ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
          Classic
        </button>
        <button onClick={() => onChange({ poolMode: true })}
          className={`px-3 py-1 rounded text-xs font-medium transition-colors ${s.poolMode ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
          Pool
        </button>
      </div>

      {s.poolMode ? (
        /* ── Pool Mode Controls ── */
        <>
          {/* L1: Seed ranking */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <NumInput label="L1: Pool / Batch" value={s.poolPerBatch} min={5} max={200}
              onChange={(v) => onChange({ poolPerBatch: v })} hint="How many assets to keep per batch via seed ranking. No optimizer at L1 — just ranks by seed metrics and keeps the top N. Higher = more assets pass to L2." />
          </div>

          {/* L2: Optimizer */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <SelectInput label="L2: Objective" value={s.objLayer2} options={ALL_OBJECTIVES}
              onChange={(v) => onChange({ objLayer2: v })} hint="Objective used by L2 to select the best N assets from the combined L1 pool within each group. This is where the real optimization happens." />
            <NumInput label="L2: Assets" value={s.l2NAssets} min={2} max={100}
              onChange={(v) => onChange({ l2NAssets: v })} hint="How many total assets each L2 variant contains. Each variant is a different selection of assets from the L1 pool." />
            <NumInput label="L2: Variants" value={s.l2NVariants} min={2} max={50}
              onChange={(v) => onChange({ l2NVariants: v })} hint="How many different asset selections L2 produces per group. More variants = wider search for L3. Assets can overlap between variants." />
            <NumInput label="L2: Diversity %" value={Math.round(s.l2MaxOverlap * 100)} min={0} max={100}
              onChange={(v) => onChange({ l2MaxOverlap: v / 100 })} hint="Max overlap allowed between L2 variants (Jaccard %). Lower = more diverse variants. 100% = no diversity filter." />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <SelectInput label="L2: Symbol Dedup" value={s.l2SymbolDedup} notePrefix="dedup"
              options={[
                { value: "keep_all", label: "Keep All", desc: "Every occurrence stays — different batch = different asset" },
                { value: "same_side", label: "Same-Side Dedup", desc: "Same symbol + same direction from multiple batches → keep only the best" },
                { value: "any_side", label: "Any-Side Dedup", desc: "Same base symbol regardless of direction → keep only the best" },
              ]}
              onChange={(v) => onChange({ l2SymbolDedup: v })}
              hint="Controls duplicate base symbols after L3 merges groups.\n\n• Keep All: every occurrence stays (different batch = different asset).\n• Same-Side: BTCUSDT_Long from two batches → keep only the best.\n• Any-Side: BTCUSDT_Long + BTCUSDT_Short → keep only the best." />
            {s.l2SymbolDedup !== "keep_all" && (
              <SelectInput label="Dedup Tiebreaker" value={s.l2DedupTiebreaker} notePrefix="tiebreaker"
                options={[
                  { value: "pnl", label: "By PnL", desc: "Keep the asset with highest total Net Profit" },
                  { value: "seed_rank", label: "By Seed Rank", desc: "Keep the asset ranked higher in L1 seed ranking" },
                ]}
                onChange={(v) => onChange({ l2DedupTiebreaker: v })}
                hint="How to pick which duplicate to keep.\n\n• By PnL: keep the one with highest total Net Profit.\n• By Seed Rank: keep the one ranked higher in L1 seed ranking." />
            )}
          </div>

          {/* L3: Combiner */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <SelectInput label="L3: Objective" value={s.objLayer3} options={ALL_OBJECTIVES}
              onChange={(v) => onChange({ objLayer3: v })} hint="Objective used by L3 to score combinations of L2 variants. L3 mix-and-matches L2 selections to find the best final portfolio." />
            <NumInput label="L3: Combinations" value={s.nFinalStrats} min={1} max={100}
              onChange={(v) => onChange({ nFinalStrats: v })} hint="How many different combinations of L2 variants to try. Each combination picks 1 variant per group and scores the result. Higher = wider search. 'Results' controls how many of these to keep." />
            <NumInput label="Results" value={s.nPortfolios} min={1} max={10}
              onChange={(v) => onChange({ nPortfolios: v })} hint="Number of final portfolio candidates to return." />
            <NumInput label="L3: Diversity %" value={Math.round(s.l3MaxOverlap * 100)} min={0} max={100}
              onChange={(v) => onChange({ l3MaxOverlap: v / 100 })} hint="Max overlap allowed between final portfolio candidates (Jaccard %). Lower = more diverse results. 100% = no diversity filter." />
          </div>

          <div className="flex justify-end">
            <button onClick={onRun} disabled={loading || disabled}
              className="px-6 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
              {loading ? "Building..." : "Build Pool"}
            </button>
          </div>
        </>
      ) : (
        /* ── Classic Mode Controls ── */
        <>
          <div className="flex items-center gap-2">
            <span className="text-xs text-ag-text-muted">L1 Mode:<HintTip text="Top N: picks the best N assets per batch. Quality Gate: only filters by min PF/WR and passes ALL survivors forward — gives later layers more to work with." /></span>
            <button onClick={() => onChange({ l1QualityGate: false })}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${!s.l1QualityGate ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
              Top N
            </button>
            <button onClick={() => onChange({ l1QualityGate: true })}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${s.l1QualityGate ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
              Quality Gate
            </button>
          </div>
          {s.l1QualityGate && (
            <div className="w-1/3">
              <NumInput label="Pool / Group" value={s.nPerGroup} min={5} max={400}
                onChange={(v) => onChange({ nPerGroup: v })} hint="Same as 'Pool / Batch' in auto mode. Max assets kept per group before L1 optimization runs. Higher = wider search but slower." />
            </div>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <SelectInput label="L1: Within-Batch" value={s.objLayer1} options={ALL_OBJECTIVES}
              onChange={(v) => onChange({ objLayer1: v })} hint="Same as 'Within' in auto mode. Objective used to pick the best Long+Short assets inside each batch." />
            <SelectInput label="L2: Cross-Batch" value={s.objLayer2} options={ALL_OBJECTIVES}
              onChange={(v) => onChange({ objLayer2: v })} hint="How to score combinations when mixing variants across grouped batches (L2 Groups). Only active when L2 groups are defined." />
            <SelectInput label="L3: Final Portfolio" value={s.objLayer3} options={ALL_OBJECTIVES}
              onChange={(v) => onChange({ objLayer3: v })} hint="Same as 'Combined' in auto mode. Objective used to assemble the final portfolio from all batch variants." />
            <NumInput label="Results" value={s.nPortfolios} min={1} max={10} onChange={(v) => onChange({ nPortfolios: v })} hint="Number of final portfolio candidates to output." />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <NumInput label="Variants / Batch" value={s.candidatesPerStrategy} min={1} max={400}
              onChange={(v) => onChange({ candidatesPerStrategy: v })} hint="How many different Long+Short portfolio variants each batch produces. Like running the auto builder N times per batch. Higher = wider search." />
            <NumInput label="Batch Slots (L3)" value={s.nFinalStrats} min={1} max={100}
              onChange={(v) => onChange({ nFinalStrats: v })} hint="Max batch variants the final portfolio can include. E.g. 5 = pick best 5 variants out of all batches. The per-batch N Longs/N Shorts (in the allocation panel below) controls assets per variant." />
            <div className="sm:col-span-2 flex items-end">
              <button onClick={onRun} disabled={loading || disabled}
                className="w-full px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
                {loading ? "Building..." : "Build Manual"}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function DirectionalControls({ s, onChange, batchOptions, onRun, loading, disabled }: {
  s: DirectionalSettings; onChange: (p: Partial<DirectionalSettings>) => void;
  batchOptions: { id: number; label: string }[];
  onRun: () => void; loading: boolean; disabled: boolean;
}) {
  return (
    <div className="space-y-3">
      <L2GroupEditor batches={batchOptions} groups={s.l2Groups}
        onGroupsChange={(g) => onChange({ l2Groups: g })} />

      <div className="flex items-center gap-2">
        <span className="text-xs text-ag-text-muted">L1 Mode:<HintTip text="Top N: picks the best N assets per batch. Quality Gate: only filters by min PF/WR and passes ALL survivors forward — gives later layers more to work with." /></span>
        <button onClick={() => onChange({ l1QualityGate: false })}
          className={`px-3 py-1 rounded text-xs font-medium transition-colors ${!s.l1QualityGate ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
          Top N
        </button>
        <button onClick={() => onChange({ l1QualityGate: true })}
          className={`px-3 py-1 rounded text-xs font-medium transition-colors ${s.l1QualityGate ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
          Quality Gate
        </button>
      </div>
      {!s.l1QualityGate && (
        <div className="grid grid-cols-3 gap-3">
          <SelectInput label="L1 Objective" value={s.objL1} options={ALL_OBJECTIVES}
            onChange={(v) => onChange({ objL1: v })} hint="How to rank assets within each batch." />
          <NumInput label="N per Batch" value={s.nPerBatch} min={1} max={100}
            onChange={(v) => onChange({ nPerBatch: v })} hint="Assets to pick per batch." />
          <NumInput label="K1 Variants" value={s.kL1} min={1} max={20}
            onChange={(v) => onChange({ kL1: v })} hint="Portfolio variants to carry forward per batch." />
        </div>
      )}
      {s.l1QualityGate && (
        <div className="w-1/3">
          <NumInput label="Pool / Group" value={s.nPerGroup} min={5} max={400}
            onChange={(v) => onChange({ nPerGroup: v })} hint="Same as 'Pool / Batch'. Max assets kept per group before optimization runs. Higher = wider search but slower." />
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        <SelectInput label="L2 Objective" value={s.objL2} options={ALL_OBJECTIVES}
          onChange={(v) => onChange({ objL2: v })} hint="How to score batch combinations within each group." />
        <NumInput label="K2 Variants" value={s.kL2} min={1} max={20}
          onChange={(v) => onChange({ kL2: v })} hint="Best combinations to carry forward per group." />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <SelectInput label="L3 Objective" value={s.objL3} options={ALL_OBJECTIVES}
          onChange={(v) => onChange({ objL3: v })} hint="How to score the final combined portfolio." />
        <NumInput label="K3 Results" value={s.kL3} min={1} max={10}
          onChange={(v) => onChange({ kL3: v })} hint="Final portfolio candidates to return." />
      </div>
      <button onClick={onRun} disabled={loading || disabled}
        className="w-full px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
        {loading ? "Building..." : "Build Directional"}
      </button>
    </div>
  );
}

function NumInput({ label, value, min, max, onChange, hint }: {
  label: string; value: number; min: number; max: number; onChange: (v: number) => void; hint?: string;
}) {
  return (
    <div>
      <label className="block text-xs text-ag-text-muted mb-1">{label}{hint && <HintTip text={hint} />}</label>
      <input type="number" min={min} max={max} value={value}
        onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value))))}
        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
    </div>
  );
}

function SelectInput({ label, value, options, onChange, hint, notePrefix }: {
  label: string; value: string; options: { value: string; label: string; desc?: string }[];
  onChange: (v: string) => void; hint?: string; notePrefix?: string;
}) {
  const selected = options.find((o) => o.value === value);
  const prefix = notePrefix || "obj";
  return (
    <div>
      <label className="block text-xs text-ag-text-muted mb-1">{label}{hint && <HintTip text={hint} />}</label>
      <div className="flex items-center gap-1">
        <select value={value} onChange={(e) => onChange(e.target.value)}
          className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
          {options.map((o) => <option key={o.value} value={o.value} title={o.desc}>{o.label}</option>)}
        </select>
        <UserNote noteKey={`${prefix}:${value}`} />
      </div>
      {selected?.desc && <span className="text-[10px] text-ag-text-muted/70 mt-0.5 block leading-tight">{selected.desc}</span>}
    </div>
  );
}
