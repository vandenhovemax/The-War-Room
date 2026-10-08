"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { Play, Calendar, Layers, Info, ChevronDown, ChevronUp } from "lucide-react";
import HintTip from "@/components/common/HintTip";
import L2GroupEditor from "@/components/common/L2GroupEditor";
import { api } from "@/lib/api";
import type { BatchBacktestRunItem, BatchGroupItem, PortfolioItem } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import PortfolioSelector from "@/components/common/PortfolioSelector";
import CategoryFilterSelect from "@/components/common/CategoryFilterSelect";
import OptimizerAdvanced from "@/components/analysis/OptimizerAdvanced";
import OptimizerTC from "@/components/analysis/OptimizerTC";
import type { TCConstraints } from "@/components/analysis/OptimizerTC";
import OptimizerBenchmark from "@/components/analysis/OptimizerBenchmark";
import type { BenchmarkConfig } from "@/components/analysis/OptimizerBenchmark";
import OptimizerRiskProfile from "@/components/analysis/OptimizerRiskProfile";
import type { RiskProfileConfig } from "@/components/analysis/OptimizerRiskProfile";
import {
  ALL_OBJECTIVES, CORRELATION_METHODS, DIVERSIFICATION_ALGOS,
} from "@/components/analysis/objectiveConstants";
import SessionFilterConfig, { defaultSessionFilter } from "@/components/common/SessionFilterConfig";
import type { SessionFilterState } from "@/components/common/SessionFilterConfig";
import WFOResearchPresets from "@/components/wfo/WFOResearchPresets";
import type { ResearchPreset } from "@/components/wfo/WFOResearchPresets";
import WFOPresetQueue from "@/components/wfo/WFOPresetQueue";
import OptimizerConfigManager from "@/components/analysis/OptimizerConfigManager";

const defaultTC: TCConstraints = { low_threshold: 10, max_low: 2, high_threshold: 50, min_high: 1 };
const defaultBM: BenchmarkConfig = {
  source: "", split: "time", threshold: 1.2, elite_target: 1.0, exclude_fragile: true,
  consistency_blend: 0, fragility_blend: 0, wr_harmony_blend: 0, dsr_gate: false,
};

const BS_METRICS = [
  { value: "net_pnl", label: "Net PnL", tip: "Total profit/loss. Simple and direct — picks whatever made the most money. Risk: favors volatile, high-variance assets." },
  { value: "lake_ratio", label: "Lake Ratio", tip: "Time and depth spent in drawdown. Lower is better. Picks assets that stay near equity highs — complements Stability well." },
  { value: "tail_ratio", label: "Tail Ratio", tip: "Big wins vs big losses (positive skew). Best for trend-following — picks assets where the best days far exceed the worst days." },
  { value: "sortino", label: "Sortino", tip: "Return per unit of downside risk. Only penalizes drops, not upside swings. Good for aggressive traders who want risk management." },
  { value: "calmar", label: "Calmar", tip: "Annual return / max drawdown. Picks assets with high returns relative to their worst loss. Good balance of growth + protection." },
  { value: "pf", label: "Profit Factor", tip: "Gross profit / gross loss. Above 1.5 is good, above 2.0 is excellent. Simple, robust, hard to overfit." },
  { value: "gain_pain", label: "Gain/Pain", tip: "Total gains / total pain. Works better than Sharpe/Sortino with small trade counts. Simple and robust." },
  { value: "expectancy", label: "Expectancy", tip: "Average profit per trade. Picks assets where each trade is worth taking. Best when trade frequency is consistent." },
  { value: "stability", label: "Stability (R\u00b2)", tip: "R-squared of equity curve. 1.0 = perfectly linear growth. Picks the smoothest, most predictable equity curves. BEST for asset selection (Step 3) — proven in research." },
  { value: "robust_sortino", label: "Robust Sortino", tip: "Sortino adjusted for extreme moves. More reliable than plain Sortino for crypto's fat-tailed distributions." },
  { value: "sharpe", label: "Sharpe", tip: "Return per unit of total volatility. Penalizes ALL swings (up and down). More conservative than Sortino." },
  { value: "ulcer", label: "Ulcer Index", tip: "Depth and duration of drawdowns combined. Lower is better. Picks assets that are least painful to hold through bad periods." },
  { value: "recovery", label: "Recovery Factor", tip: "Net profit / max drawdown. Higher = faster bounce-back from losses. Favors resilient assets that earn back their worst loss quickly." },
  { value: "payoff", label: "Payoff Ratio", tip: "Average win / average loss. Above 1 means wins are bigger than losses. Best for trend-following with trailing stops." },
  { value: "cde", label: "CDE", tip: "Conditional Drawdown at risk — controls the worst drawdowns. More thorough than max DD — averages the worst 5% of drawdown events." },
  { value: "sqn", label: "SQN", tip: "System Quality Number. 2+ Good, 3+ Excellent, 5+ Superb. Measures edge consistency relative to variability." },
  { value: "omega", label: "Omega", tip: "Probability-weighted ratio of gains over losses. Considers the entire return distribution, not just mean/variance." },
  { value: "est_efficiency", label: "EST Efficiency", tip: "Time-to-return efficiency. Picks assets that don't waste time — every day of holding generates meaningful risk-adjusted return." },
  { value: "rapid_recovery", label: "Rapid Recovery", tip: "Fastest bounce-back from drawdowns. Favors assets with V-shaped recoveries — can handle short dips but needs quick recovery." },
  { value: "gpr", label: "GPR", tip: "Gain-to-Pain Ratio on equity. Measures smooth, consistent growth. Good alternative to Stability for batch ranking." },
  { value: "ter", label: "TER", tip: "Trend Efficiency Ratio — straightness of equity curve. Picks assets with the cleanest upward equity movement." },
  { value: "keep_score", label: "KeepScore", tip: "Combined trend quality score (stability + momentum + consistency). One-number summary of how well an asset trends. BEST for batch ranking (Step 1) — proven in research." },
  { value: "mr_score", label: "MR Score", tip: "Combined mean-reversion quality score. Use only if your strategies are mean-reversion based — picks assets that mean-revert most reliably." },
  { value: "crossing_rate", label: "Crossing Rate", tip: "How often equity crosses its average. High = frequent mean-reversion. Use only for mean-reversion strategy types." },
];

const COMPOSITE_METRICS = [
  { value: "sortino", label: "Sortino" },
  { value: "pf", label: "Profit Factor" },
  { value: "recovery", label: "Recovery" },
  { value: "wr", label: "Win Rate" },
  { value: "calmar", label: "Calmar" },
  { value: "net_profit", label: "Net Profit" },
  { value: "max_dd", label: "Max Drawdown" },
  { value: "ulcer", label: "Ulcer Index" },
];

interface Props {
  onRun: (config: Record<string, unknown>) => void;
  running: boolean;
  onConfigLoaderReady?: (loader: (settings: Record<string, unknown>) => void) => void;
}

export default function WFOConfigPanel({ onRun, running, onConfigLoaderReady }: Props) {
  const { data: batches } = useApi(useCallback(() => api.batchRuns(), []), "batch-runs");
  const { data: batchGroups } = useApi(useCallback(() => api.batchGroups(), []), "batch-groups");
  const { data: portfolios } = useApi(useCallback(() => api.portfolios(), []), "portfolios");
  const [selectedBatchIds, setSelectedBatchIds] = useState<number[]>([]);
  const [selectedPortfolioIds, setSelectedPortfolioIds] = useState<Set<number>>(new Set());
  // Batch filter state
  const [batchFilterGrade, setBatchFilterGrade] = useState("");
  const [batchFilterPersonal, setBatchFilterPersonal] = useState("");
  const [batchFilterPf, setBatchFilterPf] = useState(0);

  // Event vs Time mode
  const [eventMode, setEventMode] = useState(false);
  const [nTrades, setNTrades] = useState(10);
  const [stalenessDays, setStalenessDays] = useState(180);
  const [nPerGroup, setNPerGroup] = useState(15);
  const [rebalanceEvery, setRebalanceEvery] = useState(1);
  const [minHold, setMinHold] = useState(0);
  const [warmupTrades, setWarmupTrades] = useState(0);
  const [rebalanceMode, setRebalanceMode] = useState("global");
  const [portfolioRebalanceEvery, setPortfolioRebalanceEvery] = useState(5);
  // L1 quality gate (Top N vs Quality Gate)
  const [l1QualityGate, setL1QualityGate] = useState(false);

  // Window config
  const [isMonths, setIsMonths] = useState(24);
  const [oosMonths, setOosMonths] = useState(6);
  const [lastOosEnd, setLastOosEnd] = useState(() => new Date().toISOString().split("T")[0]);

  // Time sub-mode (time-based only)
  const [timeMode, setTimeMode] = useState<"rolling" | "static" | "overlapping">("rolling");
  const [staticStepMonths, setStaticStepMonths] = useState(3);
  const [minOosMonths, setMinOosMonths] = useState(3);
  const [oosTotalMonths, setOosTotalMonths] = useState(6);
  const [dataStart, setDataStart] = useState("");

  // WFO Mode: auto | manual | directional | batch_selection
  const [wfoMode, setWfoMode] = useState<"auto" | "manual" | "directional" | "batch_selection">("auto");
  // Guard: skip IS/OOS defaults when a preset just set the mode (avoids useEffect race)
  const presetJustApplied = useRef(false);

  // Directional batches mode (beam search)
  const [l2Groups, setL2Groups] = useState<number[][]>([]);
  const [nPerBatch, setNPerBatch] = useState(5);
  const [dirObjL1, setDirObjL1] = useState("hybrid");
  const [dirObjL2, setDirObjL2] = useState("uncorrelated");
  const [dirObjL3, setDirObjL3] = useState("max_sortino");
  const [kL1, setKL1] = useState(5);
  const [kL2, setKL2] = useState(5);
  const [kL3, setKL3] = useState(3);

  // Manual mode settings
  const [manualL2Groups, setManualL2Groups] = useState<number[][]>([]);
  const [objLayer1, setObjLayer1] = useState("hybrid");
  const [objLayer2, setObjLayer2] = useState("stability");
  const [objLayer3, setObjLayer3] = useState("hybrid");
  const [candidatesPerStrategy, setCandidatesPerStrategy] = useState(3);
  const [nFinalStrats, setNFinalStrats] = useState(2);

  // Auto sub-mode: combined vs L/S split
  const [splitMode, setSplitMode] = useState(false);
  // Auto objective complexity: unified (1 objective) vs granular (within + combined)
  const [autoGranular, setAutoGranular] = useState(false);
  // Manual objective complexity: unified (1 obj all layers) vs advanced (L1/L2/L3)
  const [manualAdvanced, setManualAdvanced] = useState(false);
  const [unifiedObj, setUnifiedObj] = useState("hybrid");

  // Combined mode
  const [nAssets, setNAssets] = useState(5);
  const [objective, setObjective] = useState("hybrid");

  // LS mode
  const [nLongs, setNLongs] = useState(3);
  const [nShorts, setNShorts] = useState(3);
  const [withinObj, setWithinObj] = useState("uncorrelated");
  const [combinedObj, setCombinedObj] = useState("stability");

  // Shared
  const [corrMethod, setCorrMethod] = useState("standard");
  const [divAlgo, setDivAlgo] = useState("greedy");
  const [nCandidates, setNCandidates] = useState(1);
  const [minTrades, setMinTrades] = useState(0);
  const [minPf, setMinPf] = useState(0);
  const [minWr, setMinWr] = useState(0);
  const [extraSlippage, setExtraSlippage] = useState(0);
  const [extraCommission, setExtraCommission] = useState(0);
  const [fundingDaily, setFundingDaily] = useState(0);
  const [spread, setSpread] = useState(0);
  const [prioritizeStability, setPrioritizeStability] = useState(false);
  const [poolPerBatch, setPoolPerBatch] = useState(0);

  // Advanced (seed ranking + start date)
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [seedMetrics, setSeedMetrics] = useState<string[]>(["sortino"]);
  const [maxSeeds, setMaxSeeds] = useState(20);
  const [dateFrom, setDateFrom] = useState("");
  // Recency-weighted seed scoring
  const [recencyEnabled, setRecencyEnabled] = useState(false);
  const [recencyMode, setRecencyMode] = useState("gradual");
  const [recencyBatchSize, setRecencyBatchSize] = useState(10);
  const [recencyBoostFactor, setRecencyBoostFactor] = useState(3.0);

  // TC Constraints
  const [showTC, setShowTC] = useState(false);
  const [tcEnabled, setTcEnabled] = useState(false);
  const [tc, setTc] = useState<TCConstraints>({ ...defaultTC });
  const [tcLongs, setTcLongs] = useState<TCConstraints>({ ...defaultTC });
  const [tcShorts, setTcShorts] = useState<TCConstraints>({ ...defaultTC });

  // Benchmark
  const [showBM, setShowBM] = useState(false);
  const [bmEnabled, setBmEnabled] = useState(false);
  const [bmConfig, setBmConfig] = useState<BenchmarkConfig>({ ...defaultBM });

  // Category filter
  const [categoryFilter, setCategoryFilter] = useState<string[]>([]);

  // Pre-filter (structural quality gate)
  const [prefilterEnabled, setPrefilterEnabled] = useState(false);
  const [prefilterMode, setPrefilterMode] = useState("trend");
  const [prefilterHurstMin, setPrefilterHurstMin] = useState(0.50);
  const [prefilterTerPercentile, setPrefilterTerPercentile] = useState(25);
  const [prefilterHurstMax, setPrefilterHurstMax] = useState(0.50);
  const [prefilterVrMax, setPrefilterVrMax] = useState(1.0);
  const [prefilterMinCrossingRate, setPrefilterMinCrossingRate] = useState(0.05);
  // AC postfilter
  const [acPostfilter, setAcPostfilter] = useState(false);
  const [acPostfilterType, setAcPostfilterType] = useState("trend");
  const [acLag, setAcLag] = useState(1);
  // Avg Range seed config
  const [avgRangeBullThreshold, setAvgRangeBullThreshold] = useState(0);
  const [avgRangeBearThreshold, setAvgRangeBearThreshold] = useState(0);
  const [avgRangeAllThreshold, setAvgRangeAllThreshold] = useState(0);
  const [avgRangeFilterMode, setAvgRangeFilterMode] = useState(false);

  // Risk profile (position sizing)
  const [showRiskProfile, setShowRiskProfile] = useState(false);
  const [riskProfile, setRiskProfile] = useState<RiskProfileConfig>({
    mode: "fixed", value: 0, initialCapital: 10000,
    multiplier: 2, maxStreak: 3, martingaleCompound: "off", martingaleScope: "asset",
    martingaleCountMode: "full_position", martingaleOosReset: true,
    martingaleStartAfter: 0, antiMartingaleResetAfter: 0, kellyN: 2, kellyWarmup: 20,
    kellyScope: "asset", kellyLookback: 50, kellyCap: 0.25, slippage: 0,
  });
  const [sizingMaster, setSizingMaster] = useState("portfolio");
  const [batchConfigs, setBatchConfigs] = useState<Record<string, RiskProfileConfig>>({});
  // Boundary clipping
  const [clipIS, setClipIS] = useState(true);

  // Batch Selection mode settings
  const [bsMaxBatches, setBsMaxBatches] = useState(4);
  const [bsAssetsPerBatch, setBsAssetsPerBatch] = useState(5);
  const [bsNCandidates, setBsNCandidates] = useState(20);
  const [bsMinCalibrated, setBsMinCalibrated] = useState(3);
  const [bsMinPf, setBsMinPf] = useState(1.0);
  const [bsMinIsDays, setBsMinIsDays] = useState(30);
  const [bsMinAssetDays, setBsMinAssetDays] = useState(5);
  const [bsAssetMustProfit, setBsAssetMustProfit] = useState(false);
  const [bsCalMustProfit, setBsCalMustProfit] = useState(true);
  const [bsBatchRankMetric, setBsBatchRankMetric] = useState("recovery");
  const [bsCalRankMetric, setBsCalRankMetric] = useState("ulcer");
  const [bsAssetRankMetric, setBsAssetRankMetric] = useState("net_pnl");
  const [bsMaxTpm, setBsMaxTpm] = useState(40);
  const [bsAssetWrGate, setBsAssetWrGate] = useState(0.20);
  // Advanced batch selection features
  const [bsRecencyWeight, setBsRecencyWeight] = useState(0);
  const [bsAdaptiveMb, setBsAdaptiveMb] = useState(false);
  const [bsAdaptiveMbMin, setBsAdaptiveMbMin] = useState(2);
  const [bsAdaptiveMbMax, setBsAdaptiveMbMax] = useState(7);
  const [bsAdaptiveMbGapPct, setBsAdaptiveMbGapPct] = useState(75);
  const [bsEnsembleCalK, setBsEnsembleCalK] = useState(0);
  const [bsRegimeWeight, setBsRegimeWeight] = useState(0);
  const [bsEloIterations, setBsEloIterations] = useState(0);
  // Research-validated features (co-work sweeps)
  const [bsL2Groups, setBsL2Groups] = useState(false);
  const [bsPruningEnabled, setBsPruningEnabled] = useState(false);
  const [bsPruneLong, setBsPruneLong] = useState(0);
  const [bsPruneShort, setBsPruneShort] = useState(0);
  const [bsPastOosConfirm, setBsPastOosConfirm] = useState(false);
  const [bsQStarts, setBsQStarts] = useState(1);
  // Overfit guard — legacy BS-mode-specific state (kept for old preset compat)
  const [bsOverfitGuardEnabled, setBsOverfitGuardEnabled] = useState(false);
  const [bsOverfitGuardMode, setBsOverfitGuardMode] = useState("log");
  // Overfit guard — NEW top-level toggle (works across all wfo_modes).
  // Default OFF so existing runs are byte-identical to today.
  const [overfitGuardEnabled, setOverfitGuardEnabled] = useState(false);
  const [overfitGuardMode, setOverfitGuardMode] = useState("log");
  const [bsIncludeLiveWindow, setBsIncludeLiveWindow] = useState(true);
  const isBatchSelection = wfoMode === "batch_selection";
  // 2-Step Hybrid: batch selection → event-based
  const [hybridMode, setHybridMode] = useState(false);
  const [hybridNTrades, setHybridNTrades] = useState(10);
  const [hybridNAssets, setHybridNAssets] = useState(3);
  const [hybridStalenessDays, setHybridStalenessDays] = useState(180);
  const [hybridMinPf, setHybridMinPf] = useState(1.0);
  const [hybridMinWr, setHybridMinWr] = useState(30.0);
  const [hybridRebalanceEvery, setHybridRebalanceEvery] = useState(10);
  const [hybridRebalanceMode, setHybridRebalanceMode] = useState("global");
  const [hybridWarmupTrades, setHybridWarmupTrades] = useState(0);
  const [hybridMinHold, setHybridMinHold] = useState(0);
  const [hybridScoringMethod, setHybridScoringMethod] = useState<"single" | "composite">("composite");
  const [hybridObjective, setHybridObjective] = useState("sortino");
  const [hybridCompositeSlots, setHybridCompositeSlots] = useState([
    { metric: "sortino", weight: 20 },
    { metric: "pf", weight: 45 },
    { metric: "recovery", weight: 20 },
    { metric: "wr", weight: 15 },
  ]);
  const [hybridExecDayProfit, setHybridExecDayProfit] = useState(true);
  // Feature 2: sample-count floor (main ranking eligibility)
  const [hybridSampleCountFloor, setHybridSampleCountFloor] = useState(0);
  // Features 5 + 6 + E: rescue system (promote non-Step-1 winners)
  const [hybridRescueEnabled, setHybridRescueEnabled] = useState(false);
  const [hybridRescueQuotaPct, setHybridRescueQuotaPct] = useState(0.10);
  const [hybridRescueMinTrades, setHybridRescueMinTrades] = useState(30);
  const [hybridRescueMinScore, setHybridRescueMinScore] = useState(0.0);
  const [hybridRescueConfidenceGap, setHybridRescueConfidenceGap] = useState(0.15);
  // Category Filter (additive — uses CoinGecko categories from symbol_categories table)
  const [categoryFilterEnabled, setCategoryFilterEnabled] = useState(false);
  const [categoryInfluence, setCategoryInfluence] = useState<"none" | "low" | "mid" | "high" | "all" | "event">("none");
  const [categoryLookbackTrades, setCategoryLookbackTrades] = useState(20);
  const [categoryMaxPerCat, setCategoryMaxPerCat] = useState(0);
  // ── On-fire-streak event mode (2026-04-17-B) — defaults preserve legacy ──
  const [categoryMode, setCategoryMode] = useState<"tertile" | "streak_event">("tertile");
  const [categoryStreakRequired, setCategoryStreakRequired] = useState(5);
  const [categoryFireMultiplier, setCategoryFireMultiplier] = useState(1.30);
  const [categoryFireDurationTrades, setCategoryFireDurationTrades] = useState(10);
  // PnL Scaling (position sizing) — applies group multiplier before WFAS runs
  const [pnlScalingMode, setPnlScalingMode] = useState<"group" | "custom" | "per_batch" | "none">("group");
  const [pnlScalingCustom, setPnlScalingCustom] = useState(1.0);
  const [pnlScalingPerBatch, setPnlScalingPerBatch] = useState<Record<string, number>>({});
  // Session Filter
  const [sessionFilter, setSessionFilter] = useState<SessionFilterState>(defaultSessionFilter);
  // Track which research preset was applied (for display in saved runs)
  const [appliedPresetId, setAppliedPresetId] = useState<string | null>(null);

  // Event mode: composite scoring + per-asset day filter
  const [scoringMethod, setScoringMethod] = useState<"single" | "composite">("single");
  const [compositeSlots, setCompositeSlots] = useState([
    { metric: "sortino", weight: 40 },
    { metric: "pf", weight: 25 },
    { metric: "recovery", weight: 20 },
    { metric: "wr", weight: 15 },
  ]);
  const [execDayProfit, setExecDayProfit] = useState(false);
  const [showGuide, setShowGuide] = useState(false);

  // Auto-select first batch
  useEffect(() => {
    if (batches && batches.length > 0 && selectedBatchIds.length === 0) {
      setSelectedBatchIds([batches[0].id]);
    }
  }, [batches, selectedBatchIds.length]);

  // Switch IS/OOS defaults when entering batch selection mode
  // BUT skip if a preset just applied (it sets its own IS/OOS values)
  useEffect(() => {
    if (presetJustApplied.current) {
      presetJustApplied.current = false;
      return;
    }
    if (wfoMode === "batch_selection") {
      setIsMonths(12);
      setOosMonths(3);
    }
  }, [wfoMode]);

  const toggleBatch = (id: number) => {
    setSelectedBatchIds((prev) =>
      prev.includes(id) ? prev.filter((b) => b !== id) : [...prev, id],
    );
  };

  const togglePortfolio = (id: number) => {
    setSelectedPortfolioIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const portfolioIds = Array.from(selectedPortfolioIds);
  const totalSources = selectedBatchIds.length + portfolioIds.length;

  const windowCount = oosMonths > 0 ? Math.floor(isMonths / oosMonths) + 1 : 1;

  const canManual = totalSources >= 2;
  const canDirectional = totalSources >= 2;

  // Build risk profile fields for API (shared across all modes)
  const riskProfileFields = () => {
    if (sizingMaster === "per_batch") {
      return {
        risk_profile_sizing_master: "per_batch",
        risk_profile_batch_configs: JSON.stringify(batchConfigs),
      };
    }
    return riskProfile.mode !== "fixed" || riskProfile.value > 0
      ? {
          risk_profile_mode: riskProfile.mode,
          risk_profile_value: riskProfile.value,
          risk_profile_initial_capital: riskProfile.initialCapital,
          risk_profile_multiplier: riskProfile.multiplier,
          risk_profile_max_streak: riskProfile.maxStreak,
          risk_profile_martingale_compound: riskProfile.martingaleCompound,
          risk_profile_martingale_scope: riskProfile.martingaleScope,
          risk_profile_martingale_count_mode: riskProfile.martingaleCountMode,
          risk_profile_martingale_oos_reset: riskProfile.martingaleOosReset,
          risk_profile_kelly_n: riskProfile.kellyN,
          risk_profile_kelly_warmup: riskProfile.kellyWarmup,
        }
      : {};
  };

  // Build session filter fields for API (shared across all modes)
  const sessionFilterFields = () => {
    if (!sessionFilter.enabled) return {};
    return {
      session_filter_enabled: true,
      session_filter_mode: sessionFilter.mode,
      session_filter_exclude_days: sessionFilter.excludeDays,
      session_filter_exclude_hours: sessionFilter.excludeHours,
      session_filter_auto_min_trades: sessionFilter.autoMinTrades,
      session_filter_auto_include_insufficient: sessionFilter.autoIncludeInsufficient,
      session_filter_grade_on_cropped: sessionFilter.gradeOnCropped,
      ...(sessionFilter.mode === "saved_mask" && sessionFilter.savedMaskId
        ? { session_mask_id: sessionFilter.savedMaskId }
        : {}),
    };
  };

  // ── WFO Config Save/Load ──────────────────────────────────────
  const wfoCurrentConfig = {
    // Core
    wfoMode, eventMode, isMonths, oosMonths, selectedBatchIds, selectedPortfolioIds: [...selectedPortfolioIds],
    // Batch selection
    bsMaxBatches, bsAssetsPerBatch, bsNCandidates, bsMinCalibrated,
    bsMinPf, bsMinIsDays, bsMinAssetDays, bsAssetMustProfit, bsCalMustProfit,
    bsBatchRankMetric, bsCalRankMetric, bsAssetRankMetric, bsMaxTpm, bsAssetWrGate,
    // Advanced
    bsRecencyWeight, bsAdaptiveMb, bsAdaptiveMbMin, bsAdaptiveMbMax, bsAdaptiveMbGapPct,
    bsEnsembleCalK, bsRegimeWeight, bsEloIterations,
    // Research-validated features
    bsL2Groups, bsPruningEnabled, bsPruneLong, bsPruneShort, bsPastOosConfirm, bsQStarts,
    // Overfit guard + live window
    bsOverfitGuardEnabled, bsOverfitGuardMode, bsIncludeLiveWindow,
    // Top-level overfit guard (works in all wfo_modes)
    overfitGuardEnabled, overfitGuardMode,
    // PnL Scaling
    pnlScalingMode, pnlScalingCustom, pnlScalingPerBatch,
    // Session filter
    sessionFilter,
    // Time mode
    timeMode, staticStepMonths, minOosMonths, oosTotalMonths,
    // Shared
    nAssets, objective, dataStart, lastOosEnd, categoryFilter,
  };

  const wfoLoadConfig = (c: typeof wfoCurrentConfig) => {
    // Core
    presetJustApplied.current = true;  // prevent useEffect from clobbering IS/OOS
    if (c.wfoMode) setWfoMode(c.wfoMode);
    if (c.eventMode !== undefined) setEventMode(c.eventMode);
    setIsMonths(c.isMonths ?? 12);
    setOosMonths(c.oosMonths ?? 3);
    if (c.selectedBatchIds?.length) setSelectedBatchIds(c.selectedBatchIds);
    // Batch selection
    setBsMaxBatches(c.bsMaxBatches ?? 4);
    setBsAssetsPerBatch(c.bsAssetsPerBatch ?? 5);
    setBsNCandidates(c.bsNCandidates ?? 20);
    setBsMinCalibrated(c.bsMinCalibrated ?? 3);
    setBsMinPf(c.bsMinPf ?? 1.0);
    setBsMinIsDays(c.bsMinIsDays ?? 30);
    setBsMinAssetDays(c.bsMinAssetDays ?? 5);
    setBsAssetMustProfit(c.bsAssetMustProfit ?? false);
    setBsCalMustProfit(c.bsCalMustProfit ?? true);
    setBsBatchRankMetric(c.bsBatchRankMetric ?? "recovery");
    setBsCalRankMetric(c.bsCalRankMetric ?? "ulcer");
    setBsAssetRankMetric(c.bsAssetRankMetric ?? "net_pnl");
    setBsMaxTpm(c.bsMaxTpm ?? 40);
    setBsAssetWrGate(c.bsAssetWrGate ?? 0.20);
    // Advanced
    setBsRecencyWeight(c.bsRecencyWeight ?? 0);
    setBsAdaptiveMb(c.bsAdaptiveMb ?? false);
    setBsAdaptiveMbMin(c.bsAdaptiveMbMin ?? 2);
    setBsAdaptiveMbMax(c.bsAdaptiveMbMax ?? 7);
    setBsAdaptiveMbGapPct(c.bsAdaptiveMbGapPct ?? 75);
    setBsEnsembleCalK(c.bsEnsembleCalK ?? 0);
    setBsRegimeWeight(c.bsRegimeWeight ?? 0);
    setBsEloIterations(c.bsEloIterations ?? 0);
    // Research-validated features
    setBsL2Groups(c.bsL2Groups ?? false);
    setBsPruningEnabled(c.bsPruningEnabled ?? false);
    setBsPruneLong(c.bsPruneLong ?? 0);
    setBsPruneShort(c.bsPruneShort ?? 0);
    setBsPastOosConfirm(c.bsPastOosConfirm ?? false);
    setBsQStarts(c.bsQStarts ?? 1);
    setBsOverfitGuardEnabled(c.bsOverfitGuardEnabled ?? false);
    setBsOverfitGuardMode(c.bsOverfitGuardMode ?? "log");
    setBsIncludeLiveWindow(c.bsIncludeLiveWindow ?? true);
    // Top-level overfit guard. Backwards-compat: if a legacy preset only sets
    // bsOverfitGuardEnabled, mirror it into the top-level toggle so the UI
    // checkbox correctly reflects an active guard.
    setOverfitGuardEnabled(c.overfitGuardEnabled ?? c.bsOverfitGuardEnabled ?? false);
    setOverfitGuardMode(c.overfitGuardMode ?? c.bsOverfitGuardMode ?? "log");
    // PnL Scaling
    setPnlScalingMode(c.pnlScalingMode ?? "group");
    setPnlScalingCustom(c.pnlScalingCustom ?? 1.0);
    setPnlScalingPerBatch(c.pnlScalingPerBatch ?? {});
    // Session filter
    if (c.sessionFilter) setSessionFilter(c.sessionFilter);
    // Shared
    if (c.nAssets) setNAssets(c.nAssets);
    if (c.dataStart) setDataStart(c.dataStart);
    if (c.lastOosEnd) setLastOosEnd(c.lastOosEnd);
    if (c.categoryFilter) setCategoryFilter(c.categoryFilter);
  };

  // Expose a loader that accepts backend optimizer_settings (snake_case) and fills the config panel
  const loadFromOptimizerSettings = useCallback((s: Record<string, unknown>) => {
    wfoLoadConfig({
      wfoMode: (s.wfo_mode as string) ?? "batch_selection",
      eventMode: false,
      isMonths: (s.is_months as number) ?? isMonths,
      oosMonths: (s.oos_months as number) ?? oosMonths,
      selectedBatchIds: [],
      selectedPortfolioIds: [],
      bsMaxBatches: (s.bs_max_batches as number) ?? 4,
      bsAssetsPerBatch: (s.bs_assets_per_batch as number) ?? 5,
      bsNCandidates: (s.bs_n_candidates as number) ?? 20,
      bsMinCalibrated: (s.bs_min_calibrated as number) ?? 3,
      bsMinPf: (s.bs_min_pf as number) ?? 1.0,
      bsMinIsDays: (s.bs_min_is_days as number) ?? 30,
      bsMinAssetDays: (s.bs_min_asset_days as number) ?? 5,
      bsAssetMustProfit: (s.bs_asset_must_profit as boolean) ?? false,
      bsCalMustProfit: (s.bs_cal_must_profit as boolean) ?? true,
      bsBatchRankMetric: (s.bs_batch_rank_metric as string) ?? "recovery",
      bsCalRankMetric: (s.bs_cal_rank_metric as string) ?? "ulcer",
      bsAssetRankMetric: (s.bs_asset_rank_metric as string) ?? "net_pnl",
      bsMaxTpm: (s.bs_max_tpm as number) ?? 40,
      bsAssetWrGate: (s.bs_asset_wr_gate as number) ?? 0.20,
      bsRecencyWeight: (s.bs_recency_weight as number) ?? 0,
      bsAdaptiveMb: (s.bs_adaptive_mb as boolean) ?? false,
      bsAdaptiveMbMin: (s.bs_adaptive_mb_min as number) ?? 2,
      bsAdaptiveMbMax: (s.bs_adaptive_mb_max as number) ?? 7,
      bsAdaptiveMbGapPct: (s.bs_adaptive_mb_gap_pct as number) ?? 75,
      bsEnsembleCalK: (s.bs_ensemble_cal_k as number) ?? 0,
      bsRegimeWeight: (s.bs_regime_weight as number) ?? 0,
      bsEloIterations: (s.bs_elo_iterations as number) ?? 0,
      bsL2Groups: (s.bs_l2_groups as boolean) ?? false,
      bsPruningEnabled: (s.bs_pruning_enabled as boolean) ?? false,
      bsPruneLong: (s.bs_prune_long as number) ?? 0,
      bsPruneShort: (s.bs_prune_short as number) ?? 0,
      bsPastOosConfirm: (s.bs_past_oos_confirm as boolean) ?? false,
      bsQStarts: (s.bs_q_starts as number) ?? 1,
      bsOverfitGuardEnabled: (s.bs_overfit_guard_enabled as boolean) ?? false,
      bsOverfitGuardMode: (s.bs_overfit_guard_mode as string) ?? "log",
      bsIncludeLiveWindow: (s.bs_include_live_window as boolean) ?? true,
      // Top-level overfit guard (default false → preserves old saved-run replay behavior)
      overfitGuardEnabled: (s.overfit_guard_enabled as boolean) ?? false,
      overfitGuardMode: (s.overfit_guard_mode as string) ?? "log",
      pnlScalingMode: (s.pnl_scaling_mode as "group" | "custom" | "per_batch" | "none") ?? "group",
      pnlScalingCustom: (s.pnl_scaling_custom as number) ?? 1.0,
      pnlScalingPerBatch: {},
      sessionFilter: s.session_filter_enabled ? {
        enabled: true,
        mode: (s.session_filter_mode as "fixed" | "auto" | "saved_mask") ?? "fixed",
        excludeDays: (s.session_filter_exclude_days as number[]) ?? [],
        excludeHours: (s.session_filter_exclude_hours as number[]) ?? [],
        gradeOnCropped: (s.session_filter_grade_on_cropped as boolean) ?? false,
      } : undefined,
      timeMode: "rolling",
      staticStepMonths: 3,
      minOosMonths: 1,
      oosTotalMonths: 6,
      nAssets: 3,
      objective: "sortino",
      dataStart: (s.data_start as string) ?? "2022-01-01",
      lastOosEnd: (s.last_oos_end as string) ?? "",
      categoryFilter: [],
    } as unknown as typeof wfoCurrentConfig);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (onConfigLoaderReady) onConfigLoaderReady(loadFromOptimizerSettings);
  }, [onConfigLoaderReady, loadFromOptimizerSettings]);

  const wfoBuildDefaultName = (): string => {
    if (wfoMode === "batch_selection") {
      const parts = [
        `BS mb=${bsMaxBatches} apb=${bsAssetsPerBatch}`,
        bsBatchRankMetric,
        bsCalRankMetric,
      ];
      if (bsRecencyWeight > 0) parts.push(`mom=${bsRecencyWeight}`);
      if (bsAdaptiveMb) parts.push("adaptive");
      if (bsEnsembleCalK > 0) parts.push(`ens=${bsEnsembleCalK}`);
      if (bsRegimeWeight > 0) parts.push(`regime=${bsRegimeWeight}`);
      if (bsEloIterations > 0) parts.push(`elo=${bsEloIterations}`);
      return parts.join(" / ");
    }
    return `${wfoMode} / ${objective} / IS=${isMonths} OOS=${oosMonths}`;
  };

  // Apply a research preset — sets all relevant state variables
  const handleApplyPreset = (preset: ResearchPreset) => {
    const c = preset.config;
    setAppliedPresetId(preset.id);

    // Batch Selection mode presets
    if ("mode" in c && c.mode === "batch_selection") {
      presetJustApplied.current = true;  // prevent useEffect from clobbering IS/OOS
      setEventMode(false);
      setWfoMode("batch_selection");
      // Auto-select batches as the search pool. When the preset specifies
      // bs_group_filter (a list of group_ids, e.g. [1] for Group A only),
      // restrict to those groups. Otherwise, auto-select ALL completed
      // batches (legacy behaviour for older presets).
      const groupFilter = (c as Record<string, unknown>).bs_group_filter as number[] | undefined;
      const allIds = (batches ?? [])
        .filter((b: BatchBacktestRunItem) => b.status === "completed")
        .filter((b: BatchBacktestRunItem) =>
          !groupFilter || groupFilter.length === 0 ||
          (b.group_id != null && groupFilter.includes(b.group_id))
        )
        .map((b: BatchBacktestRunItem) => b.id);
      if (allIds.length > 0) setSelectedBatchIds(allIds);
      setIsMonths(c.is_months);
      setOosMonths(c.oos_months);
      // Pin window boundaries if preset specifies them (critical for reproducibility)
      if (c.last_oos_end) setLastOosEnd(c.last_oos_end);
      // Always set dataStart — derive from is_months if preset omits it.
      // Without this, stale dataStart from a previous preset/queue item persists,
      // causing wrong window counts (e.g. 18 windows instead of 12).
      if (c.data_start) {
        setDataStart(c.data_start);
      } else {
        // Default: most session presets used "2022-01-01" directly.
        // p-series (pre-date-correction) used "2020-07-01" for IS=18.
        // When no data_start in preset, fall back to "2022-01-01" (the common case).
        setDataStart("2022-01-01");
      }
      setBsMaxBatches(c.bs_max_batches);
      setBsAssetsPerBatch(c.bs_assets_per_batch);
      setBsBatchRankMetric(c.bs_batch_rank_metric);
      setBsCalRankMetric(c.bs_cal_rank_metric);
      setBsAssetRankMetric(c.bs_asset_rank_metric);
      // All fields set unconditionally — presets include BS_DEFAULTS for full reproducibility
      setBsNCandidates(c.bs_n_candidates ?? 20);
      setBsMinCalibrated(c.bs_min_calibrated ?? 3);
      setBsMinPf(c.bs_min_pf ?? 1.0);
      setBsMinIsDays(c.bs_min_is_days ?? 30);
      setBsMinAssetDays(c.bs_min_asset_days ?? 5);
      setBsAssetMustProfit(c.bs_asset_must_profit ?? false);
      setBsCalMustProfit(c.bs_cal_must_profit ?? true);
      setBsMaxTpm(c.bs_max_tpm ?? 40);
      setBsAssetWrGate(c.bs_asset_wr_gate ?? 0.20);
      // Advanced features — always reset (avoids stale values from previous preset)
      setBsRecencyWeight(c.bs_recency_weight ?? 0);
      setBsAdaptiveMb(c.bs_adaptive_mb ?? false);
      setBsAdaptiveMbMin(c.bs_adaptive_mb_min ?? 2);
      setBsAdaptiveMbMax(c.bs_adaptive_mb_max ?? 7);
      setBsAdaptiveMbGapPct(c.bs_adaptive_mb_gap_pct ?? 75);
      setBsEnsembleCalK(c.bs_ensemble_cal_k ?? 0);
      setBsRegimeWeight(c.bs_regime_weight ?? 0);
      setBsEloIterations(c.bs_elo_iterations ?? 0);
      // Research-validated features (co-work sweeps)
      setBsL2Groups(c.bs_l2_groups ?? false);
      setBsPruningEnabled(c.bs_pruning_enabled ?? false);
      setBsPruneLong(c.bs_prune_long ?? 0);
      setBsPruneShort(c.bs_prune_short ?? 0);
      setBsPastOosConfirm(c.bs_past_oos_confirm ?? false);
      setBsQStarts(c.bs_q_starts ?? 1);
      // Overfit guard + live window — preset values feed both legacy bs_* fields
      // and the new top-level guard. The visible UI checkbox reflects whichever
      // is on (so a preset that sets only bs_overfit_guard_enabled still ticks
      // the visible checkbox).
      setBsOverfitGuardEnabled(c.bs_overfit_guard_enabled ?? false);
      setBsOverfitGuardMode(c.bs_overfit_guard_mode ?? "log");
      setBsIncludeLiveWindow(c.bs_include_live_window ?? true);
      const _topOgEnabled = ((c as Record<string, unknown>).overfit_guard_enabled as boolean | undefined);
      const _topOgMode = ((c as Record<string, unknown>).overfit_guard_mode as string | undefined);
      setOverfitGuardEnabled(_topOgEnabled ?? c.bs_overfit_guard_enabled ?? false);
      setOverfitGuardMode(_topOgMode ?? c.bs_overfit_guard_mode ?? "log");
      // 2-Step Hybrid settings
      if ("hybrid_mode" in c && (c as Record<string, unknown>).hybrid_mode) {
        const h = c as Record<string, unknown>;
        setHybridMode(true);
        setHybridNTrades((h.hybrid_n_trades as number) ?? 10);
        setHybridNAssets((h.hybrid_n_assets as number) ?? 3);
        setHybridStalenessDays((h.hybrid_staleness_days as number) ?? 180);
        setHybridMinPf((h.hybrid_min_pf as number) ?? 1.0);
        setHybridMinWr((h.hybrid_min_wr as number) ?? 30.0);
        setHybridRebalanceEvery((h.hybrid_rebalance_every as number) ?? 10);
        setHybridRebalanceMode((h.hybrid_rebalance_mode as string) ?? "global");
        setHybridWarmupTrades((h.hybrid_warmup_trades as number) ?? 0);
        setHybridMinHold((h.hybrid_min_hold as number) ?? 0);
        setHybridScoringMethod(((h.hybrid_scoring_method as string) ?? "composite") as "single" | "composite");
        setHybridObjective((h.hybrid_objective as string) ?? "sortino");
        setHybridExecDayProfit((h.hybrid_exec_day_profit as boolean) ?? true);
        setHybridSampleCountFloor((h.hybrid_sample_count_floor as number) ?? 0);
        setHybridRescueEnabled((h.hybrid_rescue_enabled as boolean) ?? false);
        setHybridRescueQuotaPct((h.hybrid_rescue_quota_pct as number) ?? 0.10);
        setHybridRescueMinTrades((h.hybrid_rescue_min_trades as number) ?? 30);
        setHybridRescueMinScore((h.hybrid_rescue_min_score as number) ?? 0.0);
        setHybridRescueConfidenceGap((h.hybrid_rescue_confidence_gap as number) ?? 0.15);
        setCategoryFilterEnabled((h.category_filter_enabled as boolean) ?? false);
        setCategoryInfluence(((h.category_influence as string) ?? "none") as "none" | "low" | "mid" | "high" | "all" | "event");
        setCategoryLookbackTrades((h.category_lookback_trades as number) ?? 20);
        setCategoryMaxPerCat((h.category_max_per_cat as number) ?? 0);
        // On-fire-streak event mode (2026-04-17-B)
        setCategoryMode(((h.category_mode as string) ?? "tertile") as "tertile" | "streak_event");
        setCategoryStreakRequired((h.category_streak_required as number) ?? 5);
        setCategoryFireMultiplier((h.category_fire_multiplier as number) ?? 1.30);
        setCategoryFireDurationTrades((h.category_fire_duration_trades as number) ?? 10);
        if (h.hybrid_composite_weights) {
          const w = h.hybrid_composite_weights as Record<string, number>;
          setHybridCompositeSlots(Object.entries(w).map(([metric, weight]) => ({ metric, weight: Math.round(weight * 100) })));
        }
      } else {
        setHybridMode(false);
      }
      // PnL Scaling — presets default to "group" if not specified
      setPnlScalingMode(c.pnl_scaling_mode ?? "group");
      setPnlScalingCustom(c.pnl_scaling_custom ?? 1.0);
      setPnlScalingPerBatch({});
      // Session filter (entry-hour restriction)
      if (c.session_filter) {
        setSessionFilter({
          enabled: c.session_filter.enabled,
          mode: c.session_filter.mode,
          excludeDays: c.session_filter.excludeDays,
          excludeHours: c.session_filter.excludeHours,
          gradeOnCropped: c.session_filter.gradeOnCropped ?? false,
          autoMinTrades: 10,
          autoIncludeInsufficient: true,
          savedMaskId: null,
        });
      } else {
        // No session filter — reset to default
        setSessionFilter({ ...defaultSessionFilter });
      }
      return;
    }

    // Event mode presets (legacy + new)
    setEventMode(true);
    setWfoMode("auto");

    const ec = c as { n_trades: number; n_assets: number; rebalance_every: number;
      rebalance_mode?: string; min_pf: number; min_wr: number; staleness_days: number;
      date_from: string; scoring_method: "single" | "composite"; objective?: string;
      composite_weights?: Record<string, number>;
      exec_day_profit: boolean; exec_exclude_days?: number[];
      min_hold?: number; warmup_trades?: number };
    // Core settings
    setNTrades(ec.n_trades);
    setNAssets(ec.n_assets);
    setRebalanceEvery(ec.rebalance_every);
    setRebalanceMode(ec.rebalance_mode || "global");
    setMinPf(ec.min_pf);
    setMinWr(ec.min_wr);
    setStalenessDays(ec.staleness_days);
    setDateFrom(ec.date_from);
    setMinHold(ec.min_hold ?? 0);
    setWarmupTrades(ec.warmup_trades ?? 0);
    // Scoring method
    setScoringMethod(ec.scoring_method);
    if (ec.scoring_method === "composite" && ec.composite_weights) {
      const entries = Object.entries(ec.composite_weights);
      const slots = entries.map(([metric, w]) => ({
        metric,
        weight: Math.round(w * 100),
      }));
      // Pad to 4 slots if fewer
      while (slots.length < 4) slots.push({ metric: "sortino", weight: 0 });
      setCompositeSlots(slots.slice(0, 4));
    }
    if (ec.objective) setObjective(ec.objective);
    // Execution filter
    setExecDayProfit(ec.exec_day_profit);
    // Day exclusion via session filter (e.g., Monday=0 for Tier 3)
    if (ec.exec_exclude_days && ec.exec_exclude_days.length > 0) {
      setSessionFilter({
        ...defaultSessionFilter,
        enabled: true,
        mode: "fixed",
        excludeDays: ec.exec_exclude_days,
        excludeHours: [],
      });
    } else {
      setSessionFilter({ ...defaultSessionFilter });
    }
  };

  // Ref to the latest handleRun closure. The Preset Queue Runner needs this:
  // it calls handleApplyPreset (which schedules ~30 setState updates) then
  // setTimeout(handleRun). Without a ref, setTimeout captures the OLD closure
  // and reads stale state — the run fires with the previous panel's values
  // (e.g. hybrid_mode=false), producing junk 11-second runs. The ref is
  // re-pointed every render to the fresh closure that sees committed state.
  const handleRunRef = useRef<() => void>(() => {});
  // Re-point the ref to the latest handleRun on every render so the queue's
  // setTimeout always invokes a closure that reads CURRENT state.
  useEffect(() => { handleRunRef.current = handleRun; });

  const handleRun = () => {
    if (totalSources === 0) return;

    // Event-based mode
    if (eventMode) {
      const config: Record<string, unknown> = {
        batch_ids: selectedBatchIds,
        ...(portfolioIds.length > 0 ? { portfolio_ids: portfolioIds } : {}),
        event_mode: true,
        n_trades: nTrades,
        n_assets: nAssets,
        staleness_days: stalenessDays,
        min_pf: minPf,
        min_wr: minWr,
        extra_slippage_pct: extraSlippage,
        extra_commission_pct: extraCommission,
        funding_daily_pct: fundingDaily,
        spread_pct: spread,
        ...(dataStart || dateFrom ? { date_from: dataStart || dateFrom } : {}),
        ...(warmupTrades > 0 ? { warmup_trades: warmupTrades } : {}),
        rebalance_mode: rebalanceMode,
        ...(rebalanceMode === "global" ? { rebalance_every: rebalanceEvery } : {}),
        ...((rebalanceMode === "portfolio" || rebalanceMode === "spot_batch") ? { portfolio_rebalance_every: portfolioRebalanceEvery } : {}),
        min_hold: minHold,
        ...(wfoMode === "directional" && canDirectional ? {
          directional_batches: true,
          l2_groups: l2Groups,
          n_per_group: nPerGroup,
          obj_l2: dirObjL2,
          obj_l3: dirObjL3,
        } : {
          objective,
        }),
        // Composite scoring + per-asset day filter
        scoring_method: scoringMethod,
        ...(scoringMethod === "composite" ? {
          composite_weights: Object.fromEntries(
            compositeSlots
              .filter((s) => s.weight > 0)
              .map((s) => [s.metric, s.weight / 100])
          ),
        } : {}),
        exec_day_profit: execDayProfit,
        // Recency-weighted seed scoring
        ...(recencyEnabled ? {
          recency_enabled: true, recency_mode: recencyMode,
          recency_batch_size: recencyBatchSize, recency_boost_factor: recencyBoostFactor,
        } : {}),
        // Category filter
        ...(categoryFilter.length > 0 ? { category_filter: categoryFilter } : {}),
        // Structural quality pre-filter
        ...(prefilterEnabled ? {
          prefilter_enabled: true, prefilter_mode: prefilterMode,
          prefilter_hurst_min: prefilterHurstMin, prefilter_ter_percentile: prefilterTerPercentile,
          prefilter_hurst_max: prefilterHurstMax, prefilter_vr_max: prefilterVrMax,
          prefilter_min_crossing_rate: prefilterMinCrossingRate,
        } : {}),
        // AC postfilter
        ...(acPostfilter ? { ac_postfilter: true, ac_postfilter_type: acPostfilterType, ac_lag: acLag } : {}),
        // Avg Range seed config
        ...(seedMetrics.includes("avg_range") ? {
          avg_range_bull_threshold: avgRangeBullThreshold,
          avg_range_bear_threshold: avgRangeBearThreshold,
          avg_range_all_threshold: avgRangeAllThreshold,
          avg_range_filter_mode: avgRangeFilterMode,
        } : {}),
        // Risk profile (position sizing)
        ...riskProfileFields(),
        ...sessionFilterFields(),
        // Need dummy time values for API validation (not used in event mode)
        is_months: isMonths,
        oos_months: oosMonths,
        last_oos_end: lastOosEnd,
      };
      onRun(config);
      return;
    }

    // Batch Selection mode: 3-step hierarchical pipeline
    if (wfoMode === "batch_selection") {
      const config: Record<string, unknown> = {
        batch_ids: selectedBatchIds,
        ...(portfolioIds.length > 0 ? { portfolio_ids: portfolioIds } : {}),
        is_months: isMonths,
        oos_months: oosMonths,
        last_oos_end: lastOosEnd,
        wfo_mode: "batch_selection",
        ...(dataStart ? { date_from: dataStart } : {}),
        bs_max_batches: bsMaxBatches,
        bs_assets_per_batch: bsAssetsPerBatch,
        bs_n_candidates: bsNCandidates,
        bs_min_calibrated: bsMinCalibrated,
        bs_min_pf: bsMinPf,
        bs_min_is_days: bsMinIsDays,
        bs_min_asset_days: bsMinAssetDays,
        bs_asset_must_profit: bsAssetMustProfit,
        bs_cal_must_profit: bsCalMustProfit,
        bs_batch_rank_metric: bsBatchRankMetric,
        bs_cal_rank_metric: bsCalRankMetric,
        bs_asset_rank_metric: bsAssetRankMetric,
        bs_max_tpm: bsMaxTpm,
        bs_asset_wr_gate: bsAssetWrGate,
        // Advanced features
        bs_recency_weight: bsRecencyWeight,
        bs_adaptive_mb: bsAdaptiveMb,
        bs_adaptive_mb_min: bsAdaptiveMbMin,
        bs_adaptive_mb_max: bsAdaptiveMbMax,
        bs_adaptive_mb_gap_pct: bsAdaptiveMbGapPct,
        bs_ensemble_cal_k: bsEnsembleCalK,
        bs_regime_weight: bsRegimeWeight,
        bs_elo_iterations: bsEloIterations,
        // Research-validated features (co-work sweeps)
        bs_l2_groups: bsL2Groups,
        bs_pruning_enabled: bsPruningEnabled,
        bs_prune_long: bsPruneLong,
        bs_prune_short: bsPruneShort,
        bs_past_oos_confirm: bsPastOosConfirm,
        bs_q_starts: bsQStarts,
        // Overfit guard + live window — both legacy bs_* (BS pipeline) and the
        // new top-level fields are sent. Backend ORs them so old presets keep
        // working unchanged.
        bs_overfit_guard_enabled: bsOverfitGuardEnabled,
        bs_overfit_guard_mode: bsOverfitGuardMode,
        bs_include_live_window: bsIncludeLiveWindow,
        overfit_guard_enabled: overfitGuardEnabled,
        overfit_guard_mode: overfitGuardMode,
        // 2-Step Hybrid: chain event-based on winning batches
        hybrid_mode: hybridMode,
        ...(hybridMode ? {
          hybrid_n_trades: hybridNTrades,
          hybrid_n_assets: hybridNAssets,
          hybrid_staleness_days: hybridStalenessDays,
          hybrid_min_pf: hybridMinPf,
          hybrid_min_wr: hybridMinWr,
          hybrid_rebalance_every: hybridRebalanceEvery,
          hybrid_rebalance_mode: hybridRebalanceMode,
          hybrid_warmup_trades: hybridWarmupTrades,
          hybrid_min_hold: hybridMinHold,
          hybrid_scoring_method: hybridScoringMethod,
          // hybrid_objective is only live under scoring_method="single" —
          // omit it under "composite" so dead knob doesn't sneak into
          // saved payloads / research configs (silent-lie trap).
          hybrid_objective: hybridScoringMethod === "single" ? hybridObjective : undefined,
          hybrid_composite_weights: hybridScoringMethod === "composite"
            ? Object.fromEntries(hybridCompositeSlots.map(s => [s.metric, s.weight / 100]))
            : undefined,
          hybrid_exec_day_profit: hybridExecDayProfit,
          hybrid_sample_count_floor: hybridSampleCountFloor,
          hybrid_rescue_enabled: hybridRescueEnabled,
          hybrid_rescue_quota_pct: hybridRescueQuotaPct,
          hybrid_rescue_min_trades: hybridRescueMinTrades,
          hybrid_rescue_min_score: hybridRescueMinScore,
          hybrid_rescue_confidence_gap: hybridRescueConfidenceGap,
        } : {}),
        // Category filter (sent unconditionally so non-hybrid + category runs are reproducible)
        category_filter_enabled: categoryFilterEnabled,
        category_influence: categoryInfluence,
        category_lookback_trades: categoryLookbackTrades,
        category_max_per_cat: categoryMaxPerCat,
        category_score_metric: "composite",
        category_refresh_mode: "per_window",
        // On-fire-streak event mode (2026-04-17-B; defaults preserve legacy)
        category_mode: categoryMode,
        category_streak_required: categoryStreakRequired,
        category_fire_multiplier: categoryFireMultiplier,
        category_fire_duration_trades: categoryFireDurationTrades,
        // PnL Scaling (position sizing)
        pnl_scaling_mode: pnlScalingMode,
        pnl_scaling_custom: pnlScalingCustom,
        ...(pnlScalingMode === "per_batch" ? { pnl_scaling_per_batch: pnlScalingPerBatch } : {}),
        // Category filter
        ...(categoryFilter.length > 0 ? { category_filter: categoryFilter } : {}),
        // Risk profile (position sizing)
        ...riskProfileFields(),
        ...sessionFilterFields(),
        // Track which preset was used (for saved runs display)
        ...(appliedPresetId ? { preset_id: appliedPresetId } : {}),
      };
      onRun(config);
      return;
    }

    // Manual mode: 3-layer optimization
    if (wfoMode === "manual" && canManual) {
      // Unified: all 3 layers use unifiedObj. Advanced: L1/L2/L3 individually.
      const l1 = manualAdvanced ? objLayer1 : unifiedObj;
      const l2 = manualAdvanced ? objLayer2 : unifiedObj;
      const l3 = manualAdvanced ? objLayer3 : unifiedObj;
      const config: Record<string, unknown> = {
        batch_ids: selectedBatchIds,
        ...(portfolioIds.length > 0 ? { portfolio_ids: portfolioIds } : {}),
        is_months: isMonths,
        oos_months: oosMonths,
        last_oos_end: lastOosEnd,
        time_mode: timeMode,
        ...(timeMode === "static" ? { static_step_months: staticStepMonths, min_oos_months: minOosMonths } : {}),
        ...(timeMode === "overlapping" ? { oos_total_months: oosTotalMonths } : {}),
        wfo_mode: "manual",
        l1_quality_gate: l1QualityGate,
        ...(l1QualityGate ? { n_per_group: nPerGroup } : {}),
        obj_layer1: l1, obj_layer2: l2, obj_layer3: l3,
        candidates_per_strategy: candidatesPerStrategy,
        n_final_strats: nFinalStrats,
        n_candidates: nCandidates,
        n_assets: nFinalStrats * candidatesPerStrategy,
        corr_method: corrMethod,
        div_algo: divAlgo,
        min_trades: minTrades,
        min_pf: minPf,
        min_wr: minWr,
        extra_slippage_pct: extraSlippage,
        extra_commission_pct: extraCommission,
        funding_daily_pct: fundingDaily,
        spread_pct: spread,
        seed_metrics: seedMetrics,
        max_seeds: maxSeeds,

        ...(poolPerBatch > 0 ? { pool_per_batch: poolPerBatch } : {}),
        prioritize_stability: prioritizeStability,
        ...(dataStart || dateFrom ? { date_from: dataStart || dateFrom } : {}),
        ...(manualL2Groups.length > 0 ? { l2_groups: manualL2Groups } : {}),
        ...(tcEnabled ? {
          tc_low_threshold: tc.low_threshold, tc_max_low: tc.max_low,
          tc_high_threshold: tc.high_threshold, tc_min_high: tc.min_high,
        } : {}),
        ...(bmEnabled ? {
          benchmark_enabled: true,
          benchmark_source: bmConfig.source,
          benchmark_split: bmConfig.split,
          benchmark_threshold: bmConfig.threshold,
          benchmark_elite_target: bmConfig.elite_target,
          benchmark_exclude_fragile: bmConfig.exclude_fragile,
          benchmark_consistency_blend: bmConfig.consistency_blend,
          benchmark_fragility_blend: bmConfig.fragility_blend,
          benchmark_wr_harmony_blend: bmConfig.wr_harmony_blend,
          benchmark_dsr_gate: bmConfig.dsr_gate ?? false,
        } : {}),
        ...(recencyEnabled ? {
          recency_enabled: true, recency_mode: recencyMode,
          recency_batch_size: recencyBatchSize, recency_boost_factor: recencyBoostFactor,
        } : {}),
        // Category filter
        ...(categoryFilter.length > 0 ? { category_filter: categoryFilter } : {}),
        // Structural quality pre-filter
        ...(prefilterEnabled ? {
          prefilter_enabled: true, prefilter_mode: prefilterMode,
          prefilter_hurst_min: prefilterHurstMin, prefilter_ter_percentile: prefilterTerPercentile,
          prefilter_hurst_max: prefilterHurstMax, prefilter_vr_max: prefilterVrMax,
          prefilter_min_crossing_rate: prefilterMinCrossingRate,
        } : {}),
        // AC postfilter
        ...(acPostfilter ? { ac_postfilter: true, ac_postfilter_type: acPostfilterType, ac_lag: acLag } : {}),
        // Avg Range seed config
        ...(seedMetrics.includes("avg_range") ? {
          avg_range_bull_threshold: avgRangeBullThreshold,
          avg_range_bear_threshold: avgRangeBearThreshold,
          avg_range_all_threshold: avgRangeAllThreshold,
          avg_range_filter_mode: avgRangeFilterMode,
        } : {}),
        // Risk profile (position sizing)
        ...riskProfileFields(),
        ...sessionFilterFields(),
        // Overfit Guard (top-level, additive — only included when enabled)
        ...(overfitGuardEnabled ? {
          overfit_guard_enabled: true,
          overfit_guard_mode: overfitGuardMode,
        } : {}),
        // Boundary clipping
        clip_is: clipIS,
      };
      onRun(config);
      return;
    }

    // Directional batches mode: beam search optimization
    if (wfoMode === "directional" && canDirectional) {
      const config: Record<string, unknown> = {
        batch_ids: selectedBatchIds,
        ...(portfolioIds.length > 0 ? { portfolio_ids: portfolioIds } : {}),
        is_months: isMonths,
        oos_months: oosMonths,
        last_oos_end: lastOosEnd,
        time_mode: timeMode,
        ...(timeMode === "static" ? { static_step_months: staticStepMonths, min_oos_months: minOosMonths } : {}),
        ...(timeMode === "overlapping" ? { oos_total_months: oosTotalMonths } : {}),
        directional_batches: true,
        l1_quality_gate: l1QualityGate,
        l2_groups: l2Groups,
        n_per_batch: nPerBatch,
        ...(l1QualityGate ? { n_per_group: nPerGroup } : {}),
        obj_l1: dirObjL1,
        obj_l2: dirObjL2,
        obj_l3: dirObjL3,
        k_l1: kL1,
        k_l2: kL2,
        k_l3: kL3,
        n_candidates: nCandidates,
        corr_method: corrMethod,
        div_algo: divAlgo,
        min_trades: minTrades,
        min_pf: minPf,
        min_wr: minWr,
        extra_slippage_pct: extraSlippage,
        extra_commission_pct: extraCommission,
        funding_daily_pct: fundingDaily,
        spread_pct: spread,
        seed_metrics: seedMetrics,
        max_seeds: maxSeeds,

        ...(poolPerBatch > 0 ? { pool_per_batch: poolPerBatch } : {}),
        prioritize_stability: prioritizeStability,
        ...(dataStart || dateFrom ? { date_from: dataStart || dateFrom } : {}),
        ...(tcEnabled ? {
          tc_low_threshold: tc.low_threshold, tc_max_low: tc.max_low,
          tc_high_threshold: tc.high_threshold, tc_min_high: tc.min_high,
        } : {}),
        ...(bmEnabled ? {
          benchmark_enabled: true,
          benchmark_source: bmConfig.source,
          benchmark_split: bmConfig.split,
          benchmark_threshold: bmConfig.threshold,
          benchmark_elite_target: bmConfig.elite_target,
          benchmark_exclude_fragile: bmConfig.exclude_fragile,
          benchmark_consistency_blend: bmConfig.consistency_blend,
          benchmark_fragility_blend: bmConfig.fragility_blend,
          benchmark_wr_harmony_blend: bmConfig.wr_harmony_blend,
          benchmark_dsr_gate: bmConfig.dsr_gate ?? false,
        } : {}),
        ...(recencyEnabled ? {
          recency_enabled: true, recency_mode: recencyMode,
          recency_batch_size: recencyBatchSize, recency_boost_factor: recencyBoostFactor,
        } : {}),
        // Category filter
        ...(categoryFilter.length > 0 ? { category_filter: categoryFilter } : {}),
        // Structural quality pre-filter
        ...(prefilterEnabled ? {
          prefilter_enabled: true, prefilter_mode: prefilterMode,
          prefilter_hurst_min: prefilterHurstMin, prefilter_ter_percentile: prefilterTerPercentile,
          prefilter_hurst_max: prefilterHurstMax, prefilter_vr_max: prefilterVrMax,
          prefilter_min_crossing_rate: prefilterMinCrossingRate,
        } : {}),
        // AC postfilter
        ...(acPostfilter ? { ac_postfilter: true, ac_postfilter_type: acPostfilterType, ac_lag: acLag } : {}),
        // Avg Range seed config
        ...(seedMetrics.includes("avg_range") ? {
          avg_range_bull_threshold: avgRangeBullThreshold,
          avg_range_bear_threshold: avgRangeBearThreshold,
          avg_range_all_threshold: avgRangeAllThreshold,
          avg_range_filter_mode: avgRangeFilterMode,
        } : {}),
        // Risk profile (position sizing)
        ...riskProfileFields(),
        ...sessionFilterFields(),
        // Overfit Guard (top-level, additive — only included when enabled)
        ...(overfitGuardEnabled ? {
          overfit_guard_enabled: true,
          overfit_guard_mode: overfitGuardMode,
        } : {}),
        // Boundary clipping
        clip_is: clipIS,
      };
      onRun(config);
      return;
    }

    // Auto mode: resolve within/combined objectives based on Unified vs Granular
    const autoWithin = autoGranular ? withinObj : "hybrid";
    const autoCombined = autoGranular ? combinedObj : objective;

    const config: Record<string, unknown> = {
      batch_ids: selectedBatchIds,
      ...(portfolioIds.length > 0 ? { portfolio_ids: portfolioIds } : {}),
      is_months: isMonths,
      oos_months: oosMonths,
      last_oos_end: lastOosEnd,
      time_mode: timeMode,
      ...(timeMode === "static" ? { static_step_months: staticStepMonths, min_oos_months: minOosMonths } : {}),
      ...(timeMode === "overlapping" ? { oos_total_months: oosTotalMonths } : {}),
      n_assets: splitMode ? nLongs + nShorts : nAssets,
      objective: autoCombined,
      corr_method: corrMethod,
      div_algo: divAlgo,
      n_candidates: nCandidates,
      min_trades: minTrades,
      min_pf: minPf,
      min_wr: minWr,
      extra_slippage_pct: extraSlippage,
      extra_commission_pct: extraCommission,
      funding_daily_pct: fundingDaily,
      spread_pct: spread,
      seed_metrics: seedMetrics,
      max_seeds: maxSeeds,

        ...(poolPerBatch > 0 ? { pool_per_batch: poolPerBatch } : {}),
      prioritize_stability: prioritizeStability,
      ...(dataStart || dateFrom ? { date_from: dataStart || dateFrom } : {}),
      ...(splitMode ? {
        split_mode: true,
        n_longs: nLongs,
        n_shorts: nShorts,
        within_objective: autoWithin,
        combined_objective: autoCombined,
      } : {}),
      ...(tcEnabled && !splitMode ? {
        tc_low_threshold: tc.low_threshold,
        tc_max_low: tc.max_low,
        tc_high_threshold: tc.high_threshold,
        tc_min_high: tc.min_high,
      } : {}),
      ...(tcEnabled && splitMode ? {
        tc_longs: tcLongs,
        tc_shorts: tcShorts,
      } : {}),
      ...(bmEnabled ? {
        benchmark_enabled: true,
        benchmark_source: bmConfig.source,
        benchmark_split: bmConfig.split,
        benchmark_threshold: bmConfig.threshold,
        benchmark_elite_target: bmConfig.elite_target,
        benchmark_exclude_fragile: bmConfig.exclude_fragile,
        benchmark_dsr_gate: bmConfig.dsr_gate ?? false,
      } : {}),
      ...(recencyEnabled ? {
        recency_enabled: true, recency_mode: recencyMode,
        recency_batch_size: recencyBatchSize, recency_boost_factor: recencyBoostFactor,
      } : {}),
      // Category filter
      ...(categoryFilter.length > 0 ? { category_filter: categoryFilter } : {}),
      // Structural quality pre-filter
      ...(prefilterEnabled ? {
        prefilter_enabled: true, prefilter_mode: prefilterMode,
        prefilter_hurst_min: prefilterHurstMin, prefilter_ter_percentile: prefilterTerPercentile,
        prefilter_hurst_max: prefilterHurstMax, prefilter_vr_max: prefilterVrMax,
        prefilter_min_crossing_rate: prefilterMinCrossingRate,
      } : {}),
      // AC postfilter
      ...(acPostfilter ? { ac_postfilter: true, ac_postfilter_type: acPostfilterType, ac_lag: acLag } : {}),
      // Avg Range seed config
      ...(seedMetrics.includes("avg_range") ? {
        avg_range_bull_threshold: avgRangeBullThreshold,
        avg_range_bear_threshold: avgRangeBearThreshold,
        avg_range_all_threshold: avgRangeAllThreshold,
        avg_range_filter_mode: avgRangeFilterMode,
      } : {}),
      // Risk profile (position sizing)
      ...riskProfileFields(),
      // Overfit Guard (top-level, additive — only included when enabled)
      ...(overfitGuardEnabled ? {
        overfit_guard_enabled: true,
        overfit_guard_mode: overfitGuardMode,
      } : {}),
      // Boundary clipping
      clip_is: clipIS,
    };
    onRun(config);
  };

  const completedBatches = (batches ?? []).filter(
    (b: BatchBacktestRunItem) => b.status === "completed",
  );

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
      <h3 className="text-sm font-semibold flex items-center gap-2">
        <Layers size={16} className="text-ag-accent" />
        WFO Configuration
      </h3>

      {/* Research Presets — load proven configs from testing sessions */}
      <WFOResearchPresets onApply={handleApplyPreset} />

      {/* Preset Queue Runner — sequentially run multiple presets (overnight batch mode) */}
      <WFOPresetQueue
        running={running}
        applyAndRun={(preset) => {
          handleApplyPreset(preset);
          // 500ms lets React commit the ~30 setState updates from
          // handleApplyPreset. We then call handleRunRef.current() — NOT
          // handleRun directly — so the freshly-rendered closure runs and
          // sees the new state (hybrid_mode, hybrid_n_assets, etc).
          // Calling handleRun directly would read stale closure state.
          setTimeout(() => { handleRunRef.current(); }, 500);
        }}
      />

      {/* User-saved configs — save/load your own WFO presets */}
      <OptimizerConfigManager
        storageKey="wfo_batch_selection_configs"
        currentConfig={wfoCurrentConfig}
        onLoad={wfoLoadConfig}
        defaultName={wfoBuildDefaultName()}
      />

      {/* Workflow Guide — how to use WFAS */}
      <div className="border border-ag-border/50 rounded-lg overflow-hidden">
        <button onClick={() => setShowGuide(!showGuide)}
          className="w-full flex items-center gap-2 px-3 py-2 text-xs text-ag-text-muted hover:text-ag-text hover:bg-ag-dark/50 transition-colors">
          <Info size={13} className="text-ag-accent/60" />
          <span>How does WFAS work? (2-step workflow)</span>
          {showGuide ? <ChevronUp size={13} className="ml-auto" /> : <ChevronDown size={13} className="ml-auto" />}
        </button>
        {showGuide && (
          <div className="px-3 pb-3 space-y-2 text-[11px] text-ag-text-muted/80 leading-relaxed border-t border-ag-border/30">
            <div className="pt-2">
              <p className="font-medium text-ag-text/90 mb-1">Step 1 — Which batches to trade? (Batch Selection mode)</p>
              <p>Select ALL your batches, switch to <span className="text-ag-accent">Time-Based</span> → <span className="text-ag-accent">Batch Selection</span> mode.
              The 3-step pipeline ranks batches on rolling IS/OOS windows (e.g. 12mo IS, 3mo OOS) and picks the top performers.
              Every quarter the windows roll forward — so it automatically adapts as markets change. This tells you <em>which batches are working now</em>.</p>
            </div>
            <div>
              <p className="font-medium text-ag-text/90 mb-1">Step 2 — Which assets within those batches? (Event mode)</p>
              <p>Take the winning batches from Step 1, switch to <span className="text-ag-accent">Event-Based</span> mode.
              It walks through every trade chronologically, re-scoring assets every N trades using composite scoring,
              and picks the top 3 to trade. The per-asset day filter skips unprofitable weekdays per asset.
              This updates <em>trade-by-trade</em> — continuous rotation as performance shifts.</p>
            </div>
            <div>
              <p className="font-medium text-ag-text/90 mb-1">How it adapts over time</p>
              <ul className="list-disc pl-4 space-y-0.5">
                <li><strong>Batch Selection</strong> adapts <em>quarterly</em> (OOS windows roll forward)</li>
                <li><strong>Asset Selection</strong> adapts <em>every 10 trades</em> (continuous rotation)</li>
                <li><strong>Day Profitability filter</strong> adapts <em>every trade</em> (cumulative tracking)</li>
              </ul>
            </div>
            <div className="pt-1 text-[10px] text-ag-text-muted/60">
              Research presets set the <em>method</em> (scoring, filters, parameters) — you choose which batches to run them on.
              The numbers shown in preset descriptions were from specific test runs and will vary with your batch selection.
            </div>
          </div>
        )}
      </div>

      {/* Multi-batch + portfolio selector */}
      <div className="space-y-3">
        <div>
          <label className="text-xs text-ag-text-muted block mb-1">
            Batches ({selectedBatchIds.length} selected)<HintTip text="Select one or more completed batch backtests. Their trade results are used as the data pool for WFO optimization. Batch Selection mode auto-selects all when you apply a preset." />
          </label>

          {/* Filter bar */}
          <div className="flex items-center gap-2 flex-wrap mb-1.5">
            <button onClick={() => setSelectedBatchIds(completedBatches.map((b: BatchBacktestRunItem) => b.id))}
              className="px-2 py-0.5 rounded text-[10px] bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-accent hover:border-ag-accent/40 transition-colors">All</button>
            <button onClick={() => setSelectedBatchIds([])}
              className="px-2 py-0.5 rounded text-[10px] bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-accent hover:border-ag-accent/40 transition-colors">None</button>
            <span className="text-ag-border/40">|</span>
            <button onClick={() => setSelectedBatchIds(completedBatches.filter((b: BatchBacktestRunItem) => b.direction_filter === "Long").map((b: BatchBacktestRunItem) => b.id))}
              className="px-2 py-0.5 rounded text-[10px] bg-ag-dark border border-ag-border text-ag-text-muted hover:text-green-400 hover:border-green-400/40 transition-colors">Long</button>
            <button onClick={() => setSelectedBatchIds(completedBatches.filter((b: BatchBacktestRunItem) => b.direction_filter === "Short").map((b: BatchBacktestRunItem) => b.id))}
              className="px-2 py-0.5 rounded text-[10px] bg-ag-dark border border-ag-border text-ag-text-muted hover:text-red-400 hover:border-red-400/40 transition-colors">Short</button>
            <span className="text-ag-border/40">|</span>
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-ag-text-muted/60">Grade≥</span>
              <select value={batchFilterGrade} onChange={(e) => {
                const grade = e.target.value;
                setBatchFilterGrade(grade);
                if (!grade) return;
                const gradeOrder = ["A+","A","A-","B+","B","B-","C+","C","C-","D+","D","D-","F"];
                const cutoff = gradeOrder.indexOf(grade);
                setSelectedBatchIds(completedBatches
                  .filter((b: BatchBacktestRunItem) => {
                    const g = b.batch_grade;
                    if (!g) return false;
                    const idx = gradeOrder.indexOf(g);
                    return idx >= 0 && idx <= cutoff;
                  })
                  .map((b: BatchBacktestRunItem) => b.id));
              }}
                className="px-1.5 py-0.5 bg-ag-dark border border-ag-border rounded text-[10px] text-ag-text-muted">
                <option value="">—</option>
                {["A+","A","A-","B+","B","B-","C+","C","C-","D"].map(g => <option key={g} value={g}>{g}</option>)}
              </select>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-ag-text-muted/60">My≥</span>
              <select value={batchFilterPersonal} onChange={(e) => {
                const grade = e.target.value;
                setBatchFilterPersonal(grade);
                if (!grade) return;
                const gradeOrder = ["A+","A","A-","B+","B","B-","C+","C","C-","D+","D","D-","F"];
                const cutoff = gradeOrder.indexOf(grade);
                setSelectedBatchIds(completedBatches
                  .filter((b: BatchBacktestRunItem) => {
                    const g = b.personal_grade;
                    if (!g) return false;
                    const idx = gradeOrder.indexOf(g);
                    return idx >= 0 && idx <= cutoff;
                  })
                  .map((b: BatchBacktestRunItem) => b.id));
              }}
                className="px-1.5 py-0.5 bg-ag-dark border border-ag-border rounded text-[10px] text-ag-text-muted">
                <option value="">—</option>
                {["A+","A","A-","B+","B","B-","C+","C","C-","D"].map(g => <option key={g} value={g}>{g}</option>)}
              </select>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-ag-text-muted/60">PF≥</span>
              <input type="number" min={0} max={10} step={0.1} value={batchFilterPf}
                onChange={(e) => {
                  const pf = Number(e.target.value);
                  setBatchFilterPf(pf);
                  if (pf <= 0) return;
                  setSelectedBatchIds(completedBatches
                    .filter((b: BatchBacktestRunItem) => (b.avg_profit_factor ?? 0) >= pf)
                    .map((b: BatchBacktestRunItem) => b.id));
                }}
                className="w-14 px-1.5 py-0.5 bg-ag-dark border border-ag-border rounded text-[10px] text-ag-text-muted" />
            </div>
          </div>

          {/* Batch table */}
          <div className="max-h-52 overflow-y-auto border border-ag-border rounded-lg bg-ag-dark">
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-ag-dark z-10">
                <tr className="text-ag-text-muted/60 border-b border-ag-border/50">
                  <th className="w-6 px-1.5 py-1"></th>
                  <th className="text-left px-1.5 py-1 font-medium">Batch</th>
                  <th className="text-center px-1 py-1 font-medium">Dir</th>
                  <th className="text-center px-1 py-1 font-medium">Grade</th>
                  <th className="text-center px-1 py-1 font-medium">My</th>
                  <th className="text-right px-1 py-1 font-medium">PF</th>
                  <th className="text-right px-1 py-1 font-medium">WR</th>
                  <th className="text-right px-1.5 py-1 font-medium">Sym</th>
                </tr>
              </thead>
              <tbody>
                {completedBatches.length === 0 && (
                  <tr><td colSpan={8} className="text-center text-ag-text-muted py-3">No completed batches</td></tr>
                )}
                {completedBatches.map((b: BatchBacktestRunItem) => {
                  const isLong = b.direction_filter === "Long";
                  return (
                    <tr key={b.id} onClick={() => toggleBatch(b.id)}
                      className={`cursor-pointer hover:bg-ag-border/20 transition-colors ${selectedBatchIds.includes(b.id) ? "bg-ag-accent/5" : ""}`}>
                      <td className="px-1.5 py-0.5 text-center">
                        <input type="checkbox" checked={selectedBatchIds.includes(b.id)}
                          onChange={() => toggleBatch(b.id)} className="accent-ag-accent" />
                      </td>
                      <td className="px-1.5 py-0.5 truncate max-w-[180px]" title={b.name || `#${b.id}`}>
                        <span className="text-ag-text">{b.name || `#${b.id}`}</span>
                        <span className="text-ag-text-muted/50 ml-1">{b.timeframe}m</span>
                      </td>
                      <td className={`text-center px-1 py-0.5 font-medium ${isLong ? "text-green-400" : "text-red-400"}`}>
                        {isLong ? "L" : "S"}
                      </td>
                      <td className="text-center px-1 py-0.5">
                        <span className={`px-1 rounded text-[10px] font-medium ${
                          (b.batch_grade || "").startsWith("A") ? "text-green-400 bg-green-400/10" :
                          (b.batch_grade || "").startsWith("B") ? "text-blue-400 bg-blue-400/10" :
                          (b.batch_grade || "").startsWith("C") ? "text-yellow-400 bg-yellow-400/10" :
                          "text-ag-text-muted bg-ag-border/20"
                        }`}>{b.batch_grade || "—"}</span>
                      </td>
                      <td className="text-center px-1 py-0.5">
                        <span className={`px-1 rounded text-[10px] font-medium ${
                          (b.personal_grade || "").startsWith("A") ? "text-green-400 bg-green-400/10" :
                          (b.personal_grade || "").startsWith("B") ? "text-blue-400 bg-blue-400/10" :
                          (b.personal_grade || "").startsWith("C") ? "text-yellow-400 bg-yellow-400/10" :
                          b.personal_grade ? "text-ag-text-muted bg-ag-border/20" : "text-ag-text-muted/30"
                        }`}>{b.personal_grade || "—"}</span>
                      </td>
                      <td className="text-right px-1 py-0.5 tabular-nums">
                        {b.avg_profit_factor != null ? b.avg_profit_factor.toFixed(2) : "—"}
                      </td>
                      <td className="text-right px-1 py-0.5 tabular-nums">
                        {b.avg_win_rate != null ? `${(b.avg_win_rate * 100).toFixed(0)}%` : "—"}
                      </td>
                      <td className="text-right px-1.5 py-0.5 tabular-nums text-ag-text-muted">
                        {b.completed_symbols}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Saved portfolio selector */}
        <PortfolioSelector
          portfolios={portfolios ?? []}
          selectedIds={selectedPortfolioIds}
          onToggle={togglePortfolio}
        />

        {totalSources > 0 && (
          <p className="text-xs text-ag-text-muted">
            {totalSources} source{totalSources !== 1 ? "s" : ""} selected
            ({selectedBatchIds.length} batch{selectedBatchIds.length !== 1 ? "es" : ""}
            {portfolioIds.length > 0 ? `, ${portfolioIds.length} portfolio${portfolioIds.length !== 1 ? "s" : ""}` : ""})
          </p>
        )}
      </div>

      {/* Mode: Time-Based vs Event-Based */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-ag-text-muted">Engine:<HintTip text="Time-Based: sliding IS/OOS date windows (e.g. 24 months IS, 6 months OOS). Best for strategies that trade frequently — tests if selection holds across different market regimes. Event-Based: trade-by-trade simulation using last N trades per asset. Best for rare-trading strategies where time windows produce too few trades for reliable metrics. No windows — one continuous equity curve." /></span>
        <button onClick={() => setEventMode(false)}
          className={`px-3 py-1 rounded text-xs font-medium transition-colors ${!eventMode ? "bg-ag-accent text-ag-darker" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"}`}>
          Time-Based
        </button>
        <button onClick={() => setEventMode(true)}
          className={`px-3 py-1 rounded text-xs font-medium transition-colors ${eventMode ? "bg-ag-accent text-ag-darker" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"}`}>
          Event-Based
        </button>
      </div>

      {/* Time sub-mode toggle (time-based only, not batch selection) */}
      {!eventMode && !isBatchSelection && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-ag-text-muted">Time Mode:<HintTip text="Rolling: fixed-length IS+OOS windows sliding forward (standard WFO). Static: fixed start + fixed end — IS grows while OOS shrinks each step (tests how much IS data improves optimization). Overlapping: OOS split into H1+H2 halves with overlap between consecutive segments — measures selection degradation over time." /></span>
          {(["rolling", "static", "overlapping"] as const).map((m) => (
            <button key={m} onClick={() => setTimeMode(m)}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                timeMode === m ? "bg-ag-accent text-ag-darker" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
              }`}>
              {m === "rolling" ? "Rolling" : m === "static" ? "Static" : "Overlapping"}
            </button>
          ))}
        </div>
      )}

      {/* Event-Based config */}
      {eventMode && (
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Last N Trades<HintTip text="Number of most recent trades per asset to evaluate. The rolling lookback window (5-200)." /></label>
            <input type="number" min={5} max={200} value={nTrades}
              onChange={(e) => setNTrades(Math.max(5, Number(e.target.value)))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Top K Assets<HintTip text="Final portfolio size — how many assets to select at each step." /></label>
            <input type="number" min={3} max={50} value={nAssets}
              onChange={(e) => setNAssets(Math.max(3, Number(e.target.value)))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Staleness Days<HintTip text="Exclude assets whose last trade was more than this many days ago. 0 = disabled." /></label>
            <input type="number" min={0} max={730} value={stalenessDays}
              onChange={(e) => setStalenessDays(Math.max(0, Number(e.target.value)))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
        </div>
      )}

      {/* Event walk-forward controls: Warmup, Rebalance Mode, Frequency */}
      {eventMode && (
        <>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Start Date<HintTip text="Trades before this date warm up asset buffers but don't count toward the equity curve. Leave empty to use all data from the start." /></label>
              <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Warmup Trades<HintTip text="Minimum global trades before the walk-forward starts. Works WITH Start Date: date sets the window, this sets the minimum trade count within that window. E.g. Start Date = 2024-06-01, Warmup = 500 → counting begins June 2024, walk-forward starts after 500 trades. 0 = disabled." /></label>
              <input type="number" min={0} max={5000} value={warmupTrades}
                onChange={(e) => setWarmupTrades(Math.max(0, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Min Hold<HintTip text="Once an asset enters the portfolio, it stays for at least this many of its own trades before it can be removed. Prevents rapid cycling. 0 = no lock (default)." /></label>
              <input type="number" min={0} max={100} value={minHold}
                onChange={(e) => setMinHold(Math.max(0, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          </div>

          {/* Rebalance Mode */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-ag-text-muted">Rebalance:<HintTip text="Global Pulse: full portfolio re-rank every N total trades. Portfolio Pulse: full re-rank every N portfolio-member trades (ignores non-portfolio trades). Spot Single: when a portfolio asset trades, compare only that asset vs the best contender — swap if outranked. Spot Batch: accumulate portfolio-asset trades, periodically compare all accumulated vs contenders." /></span>
            {([
              { id: "global", label: "Global Pulse" },
              { id: "portfolio", label: "Portfolio Pulse" },
              { id: "spot_single", label: "Spot Single" },
              { id: "spot_batch", label: "Spot Batch" },
            ] as const).map((m) => (
              <button key={m.id} onClick={() => setRebalanceMode(m.id)}
                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                  rebalanceMode === m.id ? "bg-ag-accent text-ag-darker" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
                }`}>
                {m.label}
              </button>
            ))}
          </div>

          {/* Rebalance frequency (conditional on mode) */}
          {rebalanceMode === "global" && (
            <div className="w-1/3">
              <label className="text-xs text-ag-text-muted block mb-1">Rebalance Every<HintTip text="Re-evaluate portfolio every N global trades. 1 = every trade (most granular, default). Higher = more realistic, less noisy. E.g. 50 = re-rank assets every 50th trade across all symbols." /></label>
              <input type="number" min={1} max={500} value={rebalanceEvery}
                onChange={(e) => setRebalanceEvery(Math.max(1, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          )}
          {(rebalanceMode === "portfolio" || rebalanceMode === "spot_batch") && (
            <div className="w-1/3">
              <label className="text-xs text-ag-text-muted block mb-1">{rebalanceMode === "portfolio" ? "Portfolio Trades" : "Batch Every"}<HintTip text={rebalanceMode === "portfolio" ? "Re-evaluate portfolio every N trades from portfolio-member assets. Non-portfolio trades are ignored for this counter." : "Accumulate portfolio-asset trades and check for replacements every N portfolio trades."} /></label>
              <input type="number" min={1} max={100} value={portfolioRebalanceEvery}
                onChange={(e) => setPortfolioRebalanceEvery(Math.max(1, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          )}
        </>
      )}

      {/* Data Start (time-based only, all modes) */}
      {!eventMode && (
        <div className="w-1/3">
          <label className="text-xs text-ag-text-muted block mb-1">Data Start<HintTip text="Earliest date to use for window generation. Leave empty to auto-detect from earliest trade. Useful for excluding noisy early data or aligning with a specific date." /></label>
          <input type="date" value={dataStart} onChange={(e) => setDataStart(e.target.value)}
            className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm"
            placeholder="Auto-detect" />
        </div>
      )}

      {/* Window config (time-based only, mode-aware) */}
      {!eventMode && (timeMode === "rolling" || isBatchSelection) && (
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">IS Months<HintTip text="In-Sample period in months. This is the training window where the optimizer selects the best portfolio. Longer = more data but less adaptive." /></label>
            <input type="number" min={3} max={60} value={isMonths}
              onChange={(e) => setIsMonths(Number(e.target.value))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">OOS Months<HintTip text="Out-of-Sample period in months. This is the testing window where the IS-optimized portfolio is validated on unseen data." /></label>
            <input type="number" min={1} max={24} value={oosMonths}
              onChange={(e) => setOosMonths(Number(e.target.value))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Last OOS End<HintTip text="End date of the final OOS window. Windows are built backwards from this date. Everything after this date is unused future data." /></label>
            <input type="date" value={lastOosEnd} onChange={(e) => setLastOosEnd(e.target.value)}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
        </div>
      )}
      {!eventMode && timeMode === "static" && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">IS Months (initial)<HintTip text="Initial In-Sample length for the first window. Each subsequent window grows IS by Step Months — testing whether more training data improves selection." /></label>
            <input type="number" min={3} max={60} value={isMonths}
              onChange={(e) => setIsMonths(Number(e.target.value))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">OOS End Date<HintTip text="Fixed end date for OOS. The IS/OOS boundary advances forward each step — IS grows, OOS shrinks. OOS always ends on this date." /></label>
            <input type="date" value={lastOosEnd} onChange={(e) => setLastOosEnd(e.target.value)}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Step Months<HintTip text="How many months to advance the IS/OOS boundary each iteration. Smaller step = more windows but slower." /></label>
            <input type="number" min={1} max={12} value={staticStepMonths}
              onChange={(e) => setStaticStepMonths(Math.max(1, Number(e.target.value)))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Min OOS Months<HintTip text="Stop generating windows once remaining OOS would be shorter than this. Prevents tiny OOS periods with too few trades." /></label>
            <input type="number" min={1} max={12} value={minOosMonths}
              onChange={(e) => setMinOosMonths(Math.max(1, Number(e.target.value)))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
        </div>
      )}
      {!eventMode && timeMode === "overlapping" && (
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">IS Months<HintTip text="In-Sample period in months. Training window for optimization." /></label>
            <input type="number" min={3} max={60} value={isMonths}
              onChange={(e) => setIsMonths(Number(e.target.value))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">OOS Total Months<HintTip text="Total OOS period split into H1 + H2 halves. E.g. 6 = H1 is 3 months, H2 is 3 months. H1 of segment N overlaps with H2 of segment N-1 — degradation ratio (H2/H1) reveals if selection decays." /></label>
            <input type="number" min={2} max={24} step={2} value={oosTotalMonths}
              onChange={(e) => setOosTotalMonths(Math.max(2, Number(e.target.value)))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Last OOS End<HintTip text="End date of the final OOS window. Windows are built backwards from this date." /></label>
            <input type="date" value={lastOosEnd} onChange={(e) => setLastOosEnd(e.target.value)}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
        </div>
      )}

      {/* Window preview (time-based only) */}
      {!eventMode && (
        <div className="flex items-center gap-2 px-3 py-2 bg-ag-dark/50 rounded text-xs">
          <Calendar size={12} className="text-ag-accent" />
          <span className="text-ag-text-muted">
            {isBatchSelection && `~${Math.max(1, windowCount - 1)} windows (no trailing IS-only) · Window 1 = cold start (IS-only ranking)`}
            {!isBatchSelection && timeMode === "rolling" && `~${windowCount} windows (including final IS-only)`}
            {!isBatchSelection && timeMode === "static" && "IS grows each step, OOS shrinks — tests optimal training length"}
            {!isBatchSelection && timeMode === "overlapping" && `OOS split into H1 (${oosTotalMonths / 2}mo) + H2 (${oosTotalMonths / 2}mo) with overlap`}
          </span>
        </div>
      )}

      {/* WFO Mode toggle */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-ag-text-muted">Mode:<HintTip text={eventMode
          ? "Auto: Best for most users. Flat ranking by one objective — finds the best N assets across all batches. Works with 1+ batches.\n\nDirectional: Multi-layer (L1 quality gate → L2 group ranking → L3 final assembly) with different objectives per layer. Use when you have 2+ strategies and want guaranteed representation from each strategy group. Requires 2+ batches."
          : "Auto: Greedy optimizer finds the best N assets using correlation + scoring. Works with 1+ batches.\n\nManual: Triple-layer optimization (L1 per-batch → L2 cross-batch → L3 final assembly). Requires 2+ batches.\n\nDirectional: Beam search with strategy groups. Requires 2+ batches.\n\nBatch Selection: 3-step hierarchical pipeline (IS Screening → Calibration Gate → Asset Picking). No optimizer — pure metric-based ranking with walk-forward calibration. Best for evaluating batch quality across time."} /></span>
        {(eventMode
          ? (["auto", "directional"] as const)
          : (["auto", "manual", "directional", "batch_selection"] as const)
        ).map((m) => (
          <button key={m} onClick={() => setWfoMode(m)}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
              wfoMode === m ? "bg-ag-accent text-ag-darker" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
            }`}>
            {m === "auto" ? "Auto" : m === "manual" ? "Manual" : m === "directional" ? "Directional" : "Batch Selection"}
          </button>
        ))}
      </div>

      {/* ── Overfit Guard (always visible, works in all modes) ── */}
      <div className="flex items-center gap-3 flex-wrap px-3 py-2 bg-ag-dark/40 border border-ag-border/50 rounded">
        <label className="flex items-center gap-1.5 text-xs cursor-pointer font-medium">
          <input
            type="checkbox"
            checked={overfitGuardEnabled}
            onChange={(e) => setOverfitGuardEnabled(e.target.checked)}
            className="accent-ag-accent"
          />
          Overfit Guard
          <HintTip text="Statistical filter against overfitted picks. Default OFF — when off, runs are byte-identical to a guard-free run.\n\nBatch Selection mode: applies the 5-approach asset-level guard (log diminish, z-score penalty, Bayesian shrinkage, sigmoid cap, normal-range bonus) using the Mode dropdown.\n\nAuto / Manual / Directional modes: applies portfolio-level walk-forward re-rank — splits the IS window into 70% train / 30% validation and re-ranks candidates by validation-window stability. Mode dropdown is unused in these modes.\n\nOld presets that set bs_overfit_guard_enabled keep working unchanged." />
        </label>
        <span className="text-xs text-ag-text-muted">Mode:</span>
        <select
          value={overfitGuardMode}
          onChange={(e) => setOverfitGuardMode(e.target.value)}
          disabled={!overfitGuardEnabled}
          className="px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs disabled:opacity-50"
        >
          <option value="log">Log Diminish</option>
          <option value="zscore">Z-Score Penalty</option>
          <option value="bayes">Bayesian Shrinkage</option>
          <option value="log_zscore">Log + Z-Score</option>
          <option value="full">Full (all 5)</option>
        </select>
        {overfitGuardEnabled && !isBatchSelection && (
          <span className="text-xs text-ag-text-muted italic">
            (Mode applies in Batch Selection only — other modes use walk-forward re-rank)
          </span>
        )}
      </div>

      {/* Manual/Directional mode hint when <2 batches */}
      {!eventMode && wfoMode === "manual" && !canManual && (
        <p className="text-xs text-yellow-500 px-3 py-2 bg-yellow-500/10 rounded">
          Manual mode requires 2+ batches selected (currently {selectedBatchIds.length})
        </p>
      )}
      {wfoMode === "directional" && !canDirectional && (
        <p className="text-xs text-yellow-500 px-3 py-2 bg-yellow-500/10 rounded">
          Directional mode requires 2+ batches selected (currently {selectedBatchIds.length})
        </p>
      )}

      {/* BATCH SELECTION mode controls (time-based only) */}
      {!eventMode && isBatchSelection && (
        <div className="space-y-3 border border-ag-border/50 rounded-lg p-4 bg-ag-dark/30">
          <h4 className="text-xs font-semibold text-ag-accent">Batch Selection Pipeline</h4>
          <p className="text-xs text-ag-text-muted leading-relaxed">
            Don't know what to pick? Use Research Presets above → click <strong>"START HERE"</strong>. It sets everything for you. Just select your batches and hit Run.
          </p>

          {/* PnL Scaling (Position Sizing) */}
          <div className="border border-ag-border/30 rounded p-3 bg-ag-dark/20 space-y-2">
            <label className="text-xs font-semibold text-ag-text-muted">
              PnL Scaling
              <HintTip text="Scale trade PnL to reflect production position sizing. 'From Group' uses each batch's group pnl_multiplier (e.g. Group A = 2×). 'Per Batch' lets you set a custom multiplier for each batch. 'Custom' applies one global multiplier. 'None' uses raw $100 backtest PnL. Scaling happens BEFORE all scoring, so all metrics reflect real sizing." />
            </label>
            <div className="flex items-center gap-3">
              <select value={pnlScalingMode} onChange={(e) => {
                const mode = e.target.value as "group" | "custom" | "per_batch" | "none";
                setPnlScalingMode(mode);
                // Pre-fill per_batch map with group multipliers when switching to per_batch
                if (mode === "per_batch" && batches && batchGroups) {
                  const groupMap: Record<number, number> = {};
                  for (const g of batchGroups) groupMap[g.id] = g.pnl_multiplier ?? 1.0;
                  const newMap: Record<string, number> = {};
                  for (const bid of selectedBatchIds) {
                    const b = batches.find((x: BatchBacktestRunItem) => x.id === bid);
                    newMap[String(bid)] = b?.group_id ? (groupMap[b.group_id] ?? 1.0) : 1.0;
                  }
                  setPnlScalingPerBatch(newMap);
                }
              }}
                className="px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm min-w-[140px]">
                <option value="group">From Group</option>
                <option value="per_batch">Per Batch</option>
                <option value="custom">Custom (all)</option>
                <option value="none">None (raw $100)</option>
              </select>
              {pnlScalingMode === "custom" && (
                <div className="flex items-center gap-1.5">
                  <label className="text-xs text-ag-text-muted whitespace-nowrap">Multiplier:</label>
                  <input type="number" min={0.1} max={100} step={0.1} value={pnlScalingCustom}
                    onChange={(e) => setPnlScalingCustom(Math.max(0.1, Number(e.target.value)))}
                    className="w-20 px-2 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
                  <span className="text-xs text-ag-text-muted">×</span>
                </div>
              )}
              {pnlScalingMode === "group" && (
                <span className="text-xs text-ag-text-muted">Uses each batch group's pnl_multiplier</span>
              )}
              {pnlScalingMode === "none" && (
                <span className="text-xs text-yellow-500">⚠ Raw backtest PnL — no position sizing</span>
              )}
            </div>
            {/* Per-batch multiplier table */}
            {pnlScalingMode === "per_batch" && selectedBatchIds.length > 0 && (
              <div className="mt-2 max-h-48 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-ag-text-muted border-b border-ag-border/30">
                      <th className="text-left py-1 pr-2">Batch</th>
                      <th className="text-left py-1 pr-2">TF</th>
                      <th className="text-left py-1 pr-2">Group</th>
                      <th className="text-right py-1 w-24">Multiplier</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedBatchIds.map((bid) => {
                      const b = batches?.find((x: BatchBacktestRunItem) => x.id === bid);
                      const groupName = b?.group_id && batchGroups
                        ? batchGroups.find((g: BatchGroupItem) => g.id === b.group_id)?.name ?? "—"
                        : "—";
                      return (
                        <tr key={bid} className="border-b border-ag-border/10">
                          <td className="py-1 pr-2 text-ag-text truncate max-w-[180px]" title={b?.name ?? `#${bid}`}>
                            #{bid} {b?.name ? `— ${b.name.split(" — ")[0]}` : ""}
                          </td>
                          <td className="py-1 pr-2 text-ag-text-muted">{b?.timeframe ?? "?"}</td>
                          <td className="py-1 pr-2 text-ag-text-muted">{groupName}</td>
                          <td className="py-1 text-right">
                            <input type="number" min={0.1} max={100} step={0.1}
                              value={pnlScalingPerBatch[String(bid)] ?? 1.0}
                              onChange={(e) => setPnlScalingPerBatch(prev => ({
                                ...prev, [String(bid)]: Math.max(0.1, Number(e.target.value))
                              }))}
                              className="w-20 px-2 py-0.5 bg-ag-dark border border-ag-border rounded text-sm text-right" />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Selection sizing */}
          <div className="grid grid-cols-4 gap-3">
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Max Batches<HintTip text="Maximum number of batches selected per window after calibration. The top-ranked batches (by calibration score) are kept." /></label>
              <input type="number" min={1} max={20} value={bsMaxBatches}
                onChange={(e) => setBsMaxBatches(Math.max(1, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Assets / Batch<HintTip text="Maximum assets picked from each selected batch. Ranked by asset_rank_metric on IS data." /></label>
              <input type="number" min={1} max={20} value={bsAssetsPerBatch}
                onChange={(e) => setBsAssetsPerBatch(Math.max(1, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">IS Candidates<HintTip text="Top N batches to keep after IS screening (Step 1). These candidates enter the calibration gate. Higher = more thorough but slower." /></label>
              <input type="number" min={5} max={100} value={bsNCandidates}
                onChange={(e) => setBsNCandidates(Math.max(5, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Min Calibrated<HintTip text="Minimum batches that must pass calibration. If fewer pass, remaining slots are filled from IS-ranked candidates (fallback)." /></label>
              <input type="number" min={1} max={10} value={bsMinCalibrated}
                onChange={(e) => setBsMinCalibrated(Math.max(1, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          </div>

          {/* Scoring metrics */}
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">IS Rank Metric<HintTip text="Step 1 — Batch Screening: Ranks all batches by this metric on the IS (training) period. Top N candidates pass to Step 2. Think of it as 'which strategies performed best in training?' Best: KeepScore (combined trend quality). Hover each metric in the dropdown for details." /></label>
              <select value={bsBatchRankMetric} onChange={(e) => setBsBatchRankMetric(e.target.value)}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                {BS_METRICS.map((m) => <option key={m.value} value={m.value} title={m.tip}>{m.label}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Cal Rank Metric<HintTip text="Step 2 — Calibration Gate: Re-ranks the IS survivors by this metric on the PREVIOUS window's OOS period (data the batch has never seen). Confirms the batch isn't just overfit to IS. Best: KeepScore or Payoff. Hover each metric in the dropdown for details." /></label>
              <select value={bsCalRankMetric} onChange={(e) => setBsCalRankMetric(e.target.value)}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                {BS_METRICS.map((m) => <option key={m.value} value={m.value} title={m.tip}>{m.label}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Asset Rank Metric<HintTip text="Step 3 — Asset Picking: Within each winning batch, ranks individual assets (symbols) by this metric on the IS period. Picks the top N per batch for the final portfolio. Best: Stability (R² of equity curve) — proven to produce the most reliable OOS results. Hover each metric in the dropdown for details." /></label>
              <select value={bsAssetRankMetric} onChange={(e) => setBsAssetRankMetric(e.target.value)}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                {BS_METRICS.map((m) => <option key={m.value} value={m.value} title={m.tip}>{m.label}</option>)}
              </select>
            </div>
          </div>

          {/* Quality gates */}
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Min PF (IS)<HintTip text="Step 1: Minimum profit factor required during IS screening. Batches below this are excluded. 1.0 = break-even." /></label>
              <input type="number" min={0} max={5} step={0.1} value={bsMinPf}
                onChange={(e) => setBsMinPf(Number(e.target.value))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Min IS Days<HintTip text="Minimum non-zero PnL days required in IS period. Batches with fewer active trading days are skipped. Counts actual trading days, not calendar days." /></label>
              <input type="number" min={5} max={100} value={bsMinIsDays}
                onChange={(e) => setBsMinIsDays(Math.max(5, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Min Asset Days<HintTip text="Minimum non-zero PnL days for an individual asset in IS period. Assets with fewer active days are skipped during asset picking (Step 3)." /></label>
              <input type="number" min={5} max={100} value={bsMinAssetDays}
                onChange={(e) => setBsMinAssetDays(Math.max(5, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          </div>

          {/* Checkboxes + TPM */}
          <div className="flex items-center gap-6 flex-wrap">
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={bsCalMustProfit}
                onChange={(e) => setBsCalMustProfit(e.target.checked)}
                className="accent-ag-accent" />
              Cal Must Profit<HintTip text="Step 2: Require positive net PnL on the previous OOS window to pass calibration. Disabled = batches can pass calibration even if they lost money on the previous OOS." />
            </label>
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={bsAssetMustProfit}
                onChange={(e) => setBsAssetMustProfit(e.target.checked)}
                className="accent-ag-accent" />
              Asset Must Profit<HintTip text="Step 3: Require positive net PnL for an asset during the IS window. Disabled = loss-making assets can still be picked if they score well on the asset rank metric." />
            </label>
            <div className="flex items-center gap-2">
              <label className="text-xs text-ag-text-muted">Max TPM<HintTip text="Maximum estimated trades per month for the selected assets in a window. Prevents over-trading. Once adding another asset would exceed this limit, no more assets are added to that batch." /></label>
              <input type="number" min={5} max={200} value={bsMaxTpm}
                onChange={(e) => setBsMaxTpm(Math.max(5, Number(e.target.value)))}
                className="w-20 px-2 py-1 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs text-ag-text-muted">Asset WR Gate<HintTip text="Step 3: Minimum win rate (trade-level) in IS window for an asset to be eligible. Research used 0.20 (20%). Set to 0 to disable." /></label>
              <input type="number" min={0} max={1} step={0.05} value={bsAssetWrGate}
                onChange={(e) => setBsAssetWrGate(Math.max(0, Math.min(1, Number(e.target.value))))}
                className="w-20 px-2 py-1 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          </div>

          {/* ── Advanced Batch Selection Features ── */}
          <div className="border-t border-ag-border/50 pt-3 space-y-3">
            <p className="text-xs font-semibold text-ag-text-muted uppercase tracking-wider">Advanced Features</p>

            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">Recency Weight<HintTip text="Recency Momentum: weight recent IS PnL more heavily in Step 1 batch ranking. 0 = uniform (off). 2.0 = newest day scores 2x the oldest day. Tested range: 1.5–4.0. Best: 2.0 with net_pnl CAL." /></label>
                <input type="number" min={0} max={10} step={0.5} value={bsRecencyWeight}
                  onChange={(e) => setBsRecencyWeight(Math.max(0, Number(e.target.value)))}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">Ensemble CAL K<HintTip text="Ensemble Calibration Gate: require batch to rank in top-50% on K out of 5 risk metrics (ulcer, recovery, sortino, pf, tail_ratio) to pass Step 2. 0 = single metric (default). K=2 achieved 100% exec WR in testing." /></label>
                <input type="number" min={0} max={5} value={bsEnsembleCalK}
                  onChange={(e) => setBsEnsembleCalK(Math.max(0, Math.min(5, Number(e.target.value))))}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">Regime Weight<HintTip text="Regime-Conditional Selection: multiplies IS score by cosine similarity of batch regime profile (volatility, trend, autocorrelation) vs the aggregate IS regime. 0 = off. 1.0 = full weight. Batches that match the current market regime score higher." /></label>
                <input type="number" min={0} max={5} step={0.5} value={bsRegimeWeight}
                  onChange={(e) => setBsRegimeWeight(Math.max(0, Number(e.target.value)))}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">ELO Iters<HintTip text="ELO Tournament Selection: run round-robin tournament where batch pairs compete on cosine similarity to ideal equity curve using random 70% subsamples. 0 = off. 5 = standard. Replaces IS metric ranking with ELO ratings." /></label>
                <input type="number" min={0} max={20} value={bsEloIterations}
                  onChange={(e) => setBsEloIterations(Math.max(0, Number(e.target.value)))}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-1.5 text-xs cursor-pointer pb-1.5">
                  <input type="checkbox" checked={bsAdaptiveMb}
                    onChange={(e) => setBsAdaptiveMb(e.target.checked)}
                    className="accent-ag-accent" />
                  Adaptive mb<HintTip text="Auto-size max_batches by finding quality gaps in IS ranking. Instead of fixed mb, analyzes score drop-offs between consecutive batches and cuts where the biggest gap is. Max Batches becomes the upper bound." />
                </label>
              </div>
            </div>

            {bsAdaptiveMb && (
              <div className="grid grid-cols-3 gap-3 pl-3 border-l-2 border-ag-accent/30">
                <div>
                  <label className="text-xs text-ag-text-muted block mb-1">Adaptive Min<HintTip text="Minimum number of batches the adaptive algorithm can select. Even with a big quality gap, never go below this." /></label>
                  <input type="number" min={1} max={10} value={bsAdaptiveMbMin}
                    onChange={(e) => setBsAdaptiveMbMin(Math.max(1, Number(e.target.value)))}
                    className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
                </div>
                <div>
                  <label className="text-xs text-ag-text-muted block mb-1">Adaptive Max<HintTip text="Maximum number of batches the adaptive algorithm can select. Overrides Max Batches when adaptive mode is on." /></label>
                  <input type="number" min={2} max={20} value={bsAdaptiveMbMax}
                    onChange={(e) => setBsAdaptiveMbMax(Math.max(2, Number(e.target.value)))}
                    className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
                </div>
                <div>
                  <label className="text-xs text-ag-text-muted block mb-1">Gap %ile<HintTip text="Percentile threshold for the quality gap. Higher = more selective (only cuts at very large gaps). 75 = cut at the 75th percentile gap. Tested: 50, 75, 90." /></label>
                  <input type="number" min={10} max={99} value={bsAdaptiveMbGapPct}
                    onChange={(e) => setBsAdaptiveMbGapPct(Math.max(10, Math.min(99, Number(e.target.value))))}
                    className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
                </div>
              </div>
            )}
          </div>

          {/* ── Research-Validated Features (co-work sweeps) ── */}
          <div className="border-t border-ag-border/50 pt-3 space-y-3">
            <p className="text-xs font-semibold text-ag-text-muted uppercase tracking-wider">Research-Validated Features</p>

            <div className="grid grid-cols-3 gap-3">
              <div className="flex items-end">
                <label className="flex items-center gap-1.5 text-xs cursor-pointer pb-1.5">
                  <input type="checkbox" checked={bsL2Groups}
                    onChange={(e) => setBsL2Groups(e.target.checked)}
                    className="accent-ag-accent" />
                  L2 Groups<HintTip text="Long/Short Split Selection: select ceil(mb/2) from each side independently, preventing one side from dominating. Research finding: +20pp WR at mb=3. Single most impactful structural improvement." />
                </label>
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-1.5 text-xs cursor-pointer pb-1.5">
                  <input type="checkbox" checked={bsPastOosConfirm}
                    onChange={(e) => setBsPastOosConfirm(e.target.checked)}
                    className="accent-ag-accent" />
                  Past OOS Confirm<HintTip text="Require assets to also be profitable in the PREVIOUS OOS window before selecting them. Adds temporal confirmation — assets must show profit in both IS ranking and recent OOS. Research Tier 1 config with mb=7 apb=10." />
                </label>
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">Q-Starts<HintTip text="Q-Aligned Averaging: run the pipeline from 1 or 4 different quarter-aligned starting points (Jan/Apr/Jul/Oct) and average results. 4 = research standard (every reported WR is a 4-start average). 1 = single start (faster, but results depend on alignment)." /></label>
                <select value={bsQStarts}
                  onChange={(e) => setBsQStarts(Number(e.target.value))}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                  <option value={1}>1 (Single Start)</option>
                  <option value={4}>4 (Q-Aligned Average)</option>
                </select>
              </div>
            </div>

            <div className="flex items-end gap-3">
              <label className="flex items-center gap-1.5 text-xs cursor-pointer pb-1.5">
                <input type="checkbox" checked={bsPruningEnabled}
                  onChange={(e) => setBsPruningEnabled(e.target.checked)}
                  className="accent-ag-accent" />
                Progressive Pruning<HintTip text="Pre-filter the batch pool before WFO: score all batches across all windows, track appearance counts and average rank, then remove the worst N from each side. Dramatically improves signal by reducing a large pool to elite batches. CAVEAT: Static pruning uses all windows including future — in production, use rolling pruning." />
              </label>
            </div>

            {bsPruningEnabled && (
              <div className="grid grid-cols-2 gap-3 pl-3 border-l-2 border-ag-accent/30">
                <div>
                  <label className="text-xs text-ag-text-muted block mb-1">Prune Long<HintTip text="Number of worst-performing LONG batches to remove from the pool. Research used 4 (Pr4R8 config). Set to 0 to skip long-side pruning." /></label>
                  <input type="number" min={0} max={50} value={bsPruneLong}
                    onChange={(e) => setBsPruneLong(Math.max(0, Number(e.target.value)))}
                    className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
                </div>
                <div>
                  <label className="text-xs text-ag-text-muted block mb-1">Prune Short<HintTip text="Number of worst-performing SHORT batches to remove from the pool. Research used 8 (Pr4R8 config). Set to 0 to skip short-side pruning." /></label>
                  <input type="number" min={0} max={50} value={bsPruneShort}
                    onChange={(e) => setBsPruneShort(Math.max(0, Number(e.target.value)))}
                    className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
                </div>
              </div>
            )}
          </div>

          {/* Q-alignment recommendation */}
          <div className="px-3 py-2 bg-ag-accent/5 border border-ag-accent/20 rounded text-xs text-ag-text-muted">
            <strong className="text-ag-accent">Q-Alignment Tip:</strong> Use a Data Start date aligned to quarter boundaries (Jan 1, Apr 1, Jul 1, Oct 1) for best results. Testing showed 68% avg calibrated WR with Q-aligned starts vs 42% without (26pp gap).{bsQStarts === 4 && " With Q-Starts=4 enabled, all 4 alignments are tested automatically."}
          </div>

          {/* ── 2-Step Hybrid: Batch Selection → Event-Based ── */}
          <div className="border-t border-ag-border/50 pt-3 space-y-3">
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={hybridMode}
                  onChange={(e) => setHybridMode(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="font-semibold text-ag-text uppercase tracking-wider text-xs">
                  2-Step Hybrid
                </span>
                <HintTip text="Chain Step 1 (Batch Selection) → Step 2 (Event-Based). Batch Selection picks which batches to trade each quarter. Event-Based then walks through those batches' trades chronologically and selects the best assets to trade in real time. Session 28-B showed C3_pf+DayFilter achieves PF 2.76, 88.2% quarterly WR." />
              </label>
            </div>

            {hybridMode && (
              <div className="pl-3 border-l-2 border-ag-accent/40 space-y-3">
                <p className="text-[10px] text-ag-text-muted/70">
                  Step 1 uses the Batch Selection config above. Step 2 (below) scores assets within the winning batches.
                </p>

                {/* Scoring Method */}
                <div className="flex items-center gap-3">
                  <span className="text-xs text-ag-text-muted">Step 2 Scoring:</span>
                  <button onClick={() => setHybridScoringMethod("single")}
                    className={`px-2 py-0.5 rounded text-[10px] transition-colors ${hybridScoringMethod === "single" ? "bg-ag-accent text-ag-darker font-medium" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"}`}>
                    Single Metric
                  </button>
                  <button onClick={() => setHybridScoringMethod("composite")}
                    className={`px-2 py-0.5 rounded text-[10px] transition-colors ${hybridScoringMethod === "composite" ? "bg-ag-accent text-ag-darker font-medium" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"}`}>
                    Composite
                  </button>
                </div>

                {/* Single metric objective */}
                {hybridScoringMethod === "single" && (
                  <div>
                    <label className="text-xs text-ag-text-muted block mb-1">
                      Objective<HintTip text="Single metric to rank assets. max_dd (drawdown) was best event-mode objective on the 92-batch pool ($88K PnL, PF 1.52)." />
                    </label>
                    <select value={hybridObjective}
                      onChange={(e) => setHybridObjective(e.target.value)}
                      className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                      {COMPOSITE_METRICS.map((m) => (
                        <option key={m.value} value={m.value}>{m.label}</option>
                      ))}
                    </select>
                  </div>
                )}

                {/* Composite weights */}
                {hybridScoringMethod === "composite" && (
                  <div className="space-y-1.5">
                    <p className="text-xs text-ag-text-muted">Composite Weights<HintTip text="Weighted blend of multiple metrics. C3_pf_heavy (20/45/20/15) was the best composite in Session 28-B: PF 2.76, Sortino 0.76." /></p>
                    {hybridCompositeSlots.map((slot, i) => (
                      <div key={i} className="flex items-center gap-2">
                        <select value={slot.metric}
                          onChange={(e) => {
                            const next = [...hybridCompositeSlots];
                            next[i] = { ...next[i], metric: e.target.value };
                            setHybridCompositeSlots(next);
                          }}
                          className="flex-1 px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs">
                          {COMPOSITE_METRICS.map((m) => (
                            <option key={m.value} value={m.value}>{m.label}</option>
                          ))}
                        </select>
                        <input type="number" min={0} max={100} value={slot.weight}
                          onChange={(e) => {
                            const next = [...hybridCompositeSlots];
                            next[i] = { ...next[i], weight: Math.max(0, Math.min(100, Number(e.target.value))) };
                            setHybridCompositeSlots(next);
                          }}
                          className="w-16 px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs text-center" />
                        <span className="text-[10px] text-ag-text-muted">%</span>
                      </div>
                    ))}
                    {(() => {
                      const total = hybridCompositeSlots.reduce((s, sl) => s + sl.weight, 0);
                      return total !== 100 ? (
                        <p className="text-[10px] text-amber-400">Total: {total}% (should be 100%)</p>
                      ) : null;
                    })()}
                  </div>
                )}

                {/* Key params: 2x3 grid */}
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="text-[10px] text-ag-text-muted block mb-0.5">Portfolio Size<HintTip text="Top K assets to hold. Session 28-B used 3." /></label>
                    <input type="number" min={1} max={50} value={hybridNAssets}
                      onChange={(e) => setHybridNAssets(Math.max(1, Number(e.target.value)))}
                      className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                  </div>
                  <div>
                    <label className="text-[10px] text-ag-text-muted block mb-0.5">Lookback (N trades)<HintTip text="Last N trades per asset used for scoring. Default 10." /></label>
                    <input type="number" min={3} max={100} value={hybridNTrades}
                      onChange={(e) => setHybridNTrades(Math.max(3, Number(e.target.value)))}
                      className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                  </div>
                  <div>
                    <label className="text-[10px] text-ag-text-muted block mb-0.5">Rebalance Every<HintTip text="Re-evaluate portfolio every N global trades. Session 28-B used 10." /></label>
                    <input type="number" min={1} max={100} value={hybridRebalanceEvery}
                      onChange={(e) => setHybridRebalanceEvery(Math.max(1, Number(e.target.value)))}
                      className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                  </div>
                  <div>
                    <label className="text-[10px] text-ag-text-muted block mb-0.5">Min PF<HintTip text="Minimum profit factor to be eligible for selection." /></label>
                    <input type="number" min={0} max={10} step={0.1} value={hybridMinPf}
                      onChange={(e) => setHybridMinPf(Math.max(0, Number(e.target.value)))}
                      className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                  </div>
                  <div>
                    <label className="text-[10px] text-ag-text-muted block mb-0.5">Min WR %<HintTip text="Minimum win rate percentage to be eligible." /></label>
                    <input type="number" min={0} max={100} step={1} value={hybridMinWr}
                      onChange={(e) => setHybridMinWr(Math.max(0, Number(e.target.value)))}
                      className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                  </div>
                  <div>
                    <label className="text-[10px] text-ag-text-muted block mb-0.5">Staleness (days)<HintTip text="Max days since last trade before an asset is excluded." /></label>
                    <input type="number" min={30} max={999} value={hybridStalenessDays}
                      onChange={(e) => setHybridStalenessDays(Math.max(30, Number(e.target.value)))}
                      className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                  </div>
                </div>

                {/* DayFilter toggle */}
                <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                  <input type="checkbox" checked={hybridExecDayProfit}
                    onChange={(e) => setHybridExecDayProfit(e.target.checked)}
                    className="accent-ag-accent" />
                  Per-Asset Day Profitability Filter
                  <HintTip text="Track each asset's cumulative PnL per weekday. Only execute trades on days where that asset has been historically profitable. Session 28-B showed +35% PF boost across all composite presets. ON by default for hybrid mode." />
                </label>

                {/* Sample-count floor (main ranking eligibility) */}
                <div>
                  <label className="text-[10px] text-ag-text-muted block mb-0.5">
                    Sample-Count Floor<HintTip text="Excludes any asset with fewer than this many total trades from Step-2 ranking (independent of Lookback). 0 = disabled. Protects the portfolio from noisy low-sample assets." />
                  </label>
                  <input type="number" min={0} max={999} value={hybridSampleCountFloor}
                    onChange={(e) => setHybridSampleCountFloor(Math.max(0, Number(e.target.value)))}
                    className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                </div>

                {/* ── Rescue system (promotes non-Step-1 winners) ── */}
                <div className="border-t border-ag-border/40 pt-3 space-y-2">
                  <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                    <input type="checkbox" checked={hybridRescueEnabled}
                      onChange={(e) => setHybridRescueEnabled(e.target.checked)}
                      className="accent-ag-accent" />
                    <span className="font-semibold text-ag-text uppercase tracking-wider text-[10px]">
                      Rescue System
                    </span>
                    <HintTip text="Allows Step 2 to rescue assets from Phase-1-REJECTED batches. A rescue candidate is admitted only if its event-phase score exceeds the worst accepted asset by the Confidence Gap, and only up to Quota % of the slate per rebalance. UNTESTED on real data (backend built + unit-tested 2026-04-08). Rescued assets currently influence composition only — they do NOT yet contribute their own P&L to the equity curve." />
                  </label>

                  {hybridRescueEnabled && (
                    <div className="pl-3 border-l-2 border-amber-500/40 space-y-2">
                      <p className="text-[10px] text-amber-400/80">
                        🟡 Untested on real data. Backend verified (211/211 unit tests).
                      </p>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className="text-[10px] text-ag-text-muted block mb-0.5">
                            Quota %<HintTip text="Max fraction of Portfolio Size that can be rescued per rebalance. E.g. with Portfolio Size 10 and Quota 20%, at most 2 rescues per rebalance." />
                          </label>
                          <input type="number" min={0} max={1} step={0.05} value={hybridRescueQuotaPct}
                            onChange={(e) => setHybridRescueQuotaPct(Math.max(0, Math.min(1, Number(e.target.value))))}
                            className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                        </div>
                        <div>
                          <label className="text-[10px] text-ag-text-muted block mb-0.5">
                            Confidence Gap<HintTip text="Min score advantage a rescue candidate needs over the worst accepted asset to be admitted. Higher = more conservative rescues. Default 0.15." />
                          </label>
                          <input type="number" min={0} max={10} step={0.01} value={hybridRescueConfidenceGap}
                            onChange={(e) => setHybridRescueConfidenceGap(Math.max(0, Number(e.target.value)))}
                            className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                        </div>
                        <div>
                          <label className="text-[10px] text-ag-text-muted block mb-0.5">
                            Min Trades (Rescue Floor)<HintTip text="Minimum trade count for a rescue candidate to be eligible for ranking. Prevents admitting noisy low-sample candidates from rejected batches. Default 30." />
                          </label>
                          <input type="number" min={0} max={999} value={hybridRescueMinTrades}
                            onChange={(e) => setHybridRescueMinTrades(Math.max(0, Number(e.target.value)))}
                            className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                        </div>
                        <div>
                          <label className="text-[10px] text-ag-text-muted block mb-0.5">
                            Min Score<HintTip text="Absolute score floor for rescue candidates. A candidate below this score is never admitted regardless of the confidence gap. 0 = disabled." />
                          </label>
                          <input type="number" min={0} max={10} step={0.01} value={hybridRescueMinScore}
                            onChange={(e) => setHybridRescueMinScore(Math.max(0, Number(e.target.value)))}
                            className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* DIRECTIONAL mode controls */}
      {wfoMode === "directional" && canDirectional && (
        <div className="space-y-3">
          {/* L2 Group Editor */}
          <L2GroupEditor
            batches={completedBatches
              .filter((b: BatchBacktestRunItem) => selectedBatchIds.includes(b.id))
              .map((b: BatchBacktestRunItem) => ({
                id: b.id,
                label: `${b.name || `#${b.id}`} · ${b.direction_filter}`,
              }))}
            groups={l2Groups}
            onGroupsChange={setL2Groups}
          />

          {/* L1 Mode toggle */}
          {!eventMode && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-ag-text-muted">L1 Mode:<HintTip text="Top N: L1 selects the best N assets per batch (current behavior). Quality Gate: L1 only filters by min PF/WR — ALL survivors pass to L2/L3 for wider optimization." /></span>
              <button onClick={() => setL1QualityGate(false)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${!l1QualityGate ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
                Top N
              </button>
              <button onClick={() => setL1QualityGate(true)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${l1QualityGate ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
                Quality Gate
              </button>
            </div>
          )}

          {/* L1 Config (Top N mode only, time-based only) */}
          {!eventMode && !l1QualityGate && (
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">L1 Objective<HintTip text="How to rank and select assets within each individual batch. Runs per-batch, selecting the best N assets." /></label>
                <select value={dirObjL1} onChange={(e) => setDirObjL1(e.target.value)}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                  {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">N per Batch<HintTip text="Number of assets to select from each batch at L1. Each batch is optimized independently." /></label>
                <input type="number" min={1} max={30} value={nPerBatch}
                  onChange={(e) => setNPerBatch(Math.max(1, Number(e.target.value)))}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">K1 Variants<HintTip text="How many diverse portfolio variants to keep from L1 per batch. More = better L2/L3 combinations but slower. Deduplication removes variants with >70% overlap." /></label>
                <input type="number" min={1} max={20} value={kL1}
                  onChange={(e) => setKL1(Math.max(1, Math.min(20, Number(e.target.value))))}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
              </div>
            </div>
          )}

          {/* N per Group (Quality Gate mode or Event mode) */}
          {(l1QualityGate || eventMode) && (
            <div className="w-1/3">
              <label className="text-xs text-ag-text-muted block mb-1">N per Group<HintTip text="L2 keeps the top N assets per group. Generous pool for L3 final assembly." /></label>
              <input type="number" min={5} max={40} value={nPerGroup}
                onChange={(e) => setNPerGroup(Math.max(5, Number(e.target.value)))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          )}

          {/* L2 Config */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">L2 Objective<HintTip text="How to score batch combinations within each strategy group. Exhaustive search over all L1 variant combos." /></label>
              <select value={dirObjL2} onChange={(e) => setDirObjL2(e.target.value)}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">K2 Variants<HintTip text="How many best L2 combinations to keep per strategy group for the final L3 assembly." /></label>
              <input type="number" min={1} max={20} value={kL2}
                onChange={(e) => setKL2(Math.max(1, Math.min(20, Number(e.target.value))))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          </div>

          {/* L3 Config */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">L3 Objective<HintTip text="How to score the final global portfolio combining all strategy groups. This is the top-level optimization." /></label>
              <select value={dirObjL3} onChange={(e) => setDirObjL3(e.target.value)}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">K3 Results<HintTip text="Number of final portfolio candidates to return from the beam search. Top K3 best global assemblies." /></label>
              <input type="number" min={1} max={10} value={kL3}
                onChange={(e) => setKL3(Math.max(1, Math.min(10, Number(e.target.value))))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          </div>
        </div>
      )}

      {/* MANUAL mode controls (time-based only) */}
      {!eventMode && wfoMode === "manual" && canManual && (
        <div className="space-y-3">
          {/* L2 Group Editor (strategy pairing) */}
          <L2GroupEditor
            batches={completedBatches
              .filter((b: BatchBacktestRunItem) => selectedBatchIds.includes(b.id))
              .map((b: BatchBacktestRunItem) => ({
                id: b.id,
                label: `${b.name || `#${b.id}`} · ${b.direction_filter}`,
              }))}
            groups={manualL2Groups}
            onGroupsChange={setManualL2Groups}
          />
          {/* L1 Mode toggle */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-ag-text-muted">L1 Mode:<HintTip text="Top N: L1 selects the best N assets per batch (current behavior). Quality Gate: L1 only filters by min PF/WR — ALL survivors pass to L2/L3 for wider optimization." /></span>
            <button onClick={() => setL1QualityGate(false)}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${!l1QualityGate ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
              Top N
            </button>
            <button onClick={() => setL1QualityGate(true)}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${l1QualityGate ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
              Quality Gate
            </button>
          </div>
          {/* Unified vs Advanced toggle */}
          <div className="flex items-center gap-3">
            <label className="text-xs text-ag-text-muted">Objectives:<HintTip text="Unified: same objective for all 3 layers. Advanced (Triple-Layer): set a different objective for each layer — L1 within-group, L2 long+short pairing, L3 inter-strategy." /></label>
            <button onClick={() => setManualAdvanced(false)}
              className={`px-3 py-1 text-xs rounded ${!manualAdvanced ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
              Unified
            </button>
            <button onClick={() => setManualAdvanced(true)}
              className={`px-3 py-1 text-xs rounded ${manualAdvanced ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
              Advanced (Triple-Layer)
            </button>
          </div>

          {/* Unified: single objective for all layers */}
          {!manualAdvanced && (
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Objective (All Layers)</label>
              <select value={unifiedObj} onChange={(e) => setUnifiedObj(e.target.value)}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
          )}

          {/* Advanced: L1/L2/L3 individually */}
          {manualAdvanced && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-ag-text-muted block mb-1">L1: Within-Group<HintTip text="How to rank and select assets within each direction (longs together, shorts together) for a single strategy." /></label>
                  <select value={objLayer1} onChange={(e) => setObjLayer1(e.target.value)}
                    className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                    {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-ag-text-muted block mb-1">L2: L+S Pairing<HintTip text="How to combine the long group and short group into a single strategy pair. Optimizes the balance between long and short sides." /></label>
                  <select value={objLayer2} onChange={(e) => setObjLayer2(e.target.value)}
                    className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                    {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">L3: Inter-Strategy<HintTip text="How to mix multiple strategies together into the final portfolio. This is the top-level optimization across all strategy pairs." /></label>
                <select value={objLayer3} onChange={(e) => setObjLayer3(e.target.value)}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                  {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            </>
          )}

          {/* Manual mode sizing */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Candidates / Strategy<HintTip text="How many candidate L+S combinations to keep per strategy before the L3 inter-strategy mix. More = slower but more thorough." /></label>
              <input type="number" min={1} max={10} value={candidatesPerStrategy}
                onChange={(e) => setCandidatesPerStrategy(Math.max(1, Math.min(10, Number(e.target.value))))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Strategy Slots (L3)<HintTip text="Total number of strategy slots in the final portfolio. E.g. 4 = the portfolio will include assets from up to 4 different strategies." /></label>
              <input type="number" min={1} max={20} value={nFinalStrats}
                onChange={(e) => setNFinalStrats(Math.max(1, Math.min(20, Number(e.target.value))))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          </div>
          <div className="text-xs text-ag-text-muted px-2">
            {nFinalStrats * candidatesPerStrategy} total asset slots
          </div>
        </div>
      )}

      {/* AUTO mode controls — event mode: scoring method + objective + day filter */}
      {eventMode && wfoMode === "auto" && (
        <div className="space-y-3">
          {/* Scoring Method toggle */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-ag-text-muted">Scoring:<HintTip text="Single Metric: rank assets by one metric (sortino, calmar, etc.). Composite: blend 4 normalized metrics (sortino, PF, recovery, win rate) with custom weights. Composite is more robust — proven PF 2.00 vs 1.91 single-metric in testing." /></span>
            <button onClick={() => setScoringMethod("single")}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                scoringMethod === "single" ? "bg-ag-accent text-ag-darker" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
              }`}>Single Metric</button>
            <button onClick={() => setScoringMethod("composite")}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                scoringMethod === "composite" ? "bg-ag-accent text-ag-darker" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
              }`}>Composite</button>
          </div>

          {/* Single metric: objective dropdown */}
          {scoringMethod === "single" && (
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Ranking Objective<HintTip text="Metric used to rank assets. Higher is better (except max_dd/ulcer)." /></label>
              <select value={objective} onChange={(e) => setObjective(e.target.value)}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
          )}

          {/* Composite: metric dropdowns + weight sliders */}
          {scoringMethod === "composite" && (
            <div className="grid grid-cols-4 gap-2">
              {compositeSlots.map((slot, idx) => {
                // Metrics already used by other slots (prevent duplicates)
                const usedMetrics = compositeSlots
                  .filter((_, i) => i !== idx)
                  .map((s) => s.metric);
                return (
                  <div key={idx}>
                    <select
                      value={slot.metric}
                      onChange={(e) => {
                        const next = [...compositeSlots];
                        next[idx] = { ...next[idx], metric: e.target.value };
                        setCompositeSlots(next);
                      }}
                      className="w-full px-1 py-0.5 bg-ag-dark border border-ag-border rounded text-xs mb-1"
                    >
                      {COMPOSITE_METRICS.map((m) => (
                        <option key={m.value} value={m.value} disabled={usedMetrics.includes(m.value)}>
                          {m.label}
                        </option>
                      ))}
                    </select>
                    <div className="flex items-center gap-1">
                      <input type="range" min={0} max={100} step={5} value={slot.weight}
                        onChange={(e) => {
                          const next = [...compositeSlots];
                          next[idx] = { ...next[idx], weight: Number(e.target.value) };
                          setCompositeSlots(next);
                        }}
                        className="flex-1 h-1.5 accent-ag-accent" />
                      <span className="text-ag-accent text-xs w-8 text-right">{slot.weight}%</span>
                    </div>
                  </div>
                );
              })}
              {(() => {
                const total = compositeSlots.reduce((sum, s) => sum + s.weight, 0);
                return (
                  <div className="col-span-4 text-xs text-ag-text-muted">
                    Total: {total}%
                    {total !== 100 && (
                      <span className="text-yellow-400 ml-2">(weights don&apos;t sum to 100%)</span>
                    )}
                  </div>
                );
              })()}
            </div>
          )}

          {/* Per-asset day profitability exec filter */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={execDayProfit} onChange={(e) => setExecDayProfit(e.target.checked)}
              className="accent-ag-accent" />
            <span className="text-xs text-ag-text-muted">Per-asset day-of-week filter<HintTip text="Track each asset's cumulative PnL per weekday (Mon-Sun). Only count trades on days where that specific asset has been historically profitable. Scoring always sees ALL data — this only filters execution. Proven to boost PF from 2.00 to 2.73 in testing." /></span>
          </label>
        </div>
      )}

      {/* AUTO mode controls — time-based: full panel */}
      {!eventMode && wfoMode === "auto" && (
        <>
          {/* Sub-mode: Combined vs L/S Split */}
          <div className="flex items-center gap-3">
            <label className="text-xs text-ag-text-muted">Sub-mode:<HintTip text="Combined: pick N best assets from the full pool. L/S Split: separately pick N longs and N shorts, then combine them." /></label>
            <button onClick={() => setSplitMode(false)}
              className={`px-3 py-1 text-xs rounded ${!splitMode ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
              Combined
            </button>
            <button onClick={() => setSplitMode(true)}
              className={`px-3 py-1 text-xs rounded ${splitMode ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
              L/S Split
            </button>
          </div>

          {/* Asset counts */}
          {splitMode ? (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">N Longs<HintTip text="Number of long-side assets to include in the portfolio." /></label>
                <input type="number" min={1} max={20} value={nLongs}
                  onChange={(e) => setNLongs(Number(e.target.value))}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">N Shorts<HintTip text="Number of short-side assets to include in the portfolio." /></label>
                <input type="number" min={1} max={20} value={nShorts}
                  onChange={(e) => setNShorts(Number(e.target.value))}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
              </div>
            </div>
          ) : (
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">N Assets<HintTip text="Total number of assets to select from the pool for the final portfolio." /></label>
              <input type="number" min={1} max={20} value={nAssets}
                onChange={(e) => setNAssets(Number(e.target.value))}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
            </div>
          )}

          {/* Objective complexity: Unified vs Granular */}
          <div className="flex items-center gap-3">
            <label className="text-xs text-ag-text-muted">Objectives:<HintTip text="Unified: one objective for all optimization layers (within auto-set to Smart Balance). Granular: set separate Within (inside one direction) and Combined (across directions) objectives." /></label>
            <button onClick={() => setAutoGranular(false)}
              className={`px-3 py-1 text-xs rounded ${!autoGranular ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
              Unified
            </button>
            <button onClick={() => setAutoGranular(true)}
              className={`px-3 py-1 text-xs rounded ${autoGranular ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted"}`}>
              Granular
            </button>
          </div>

          {/* Unified: single objective */}
          {!autoGranular && (
            <div>
              <label className="text-xs text-ag-text-muted block mb-1">Objective</label>
              <select value={objective} onChange={(e) => setObjective(e.target.value)}
                className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
          )}

          {/* Granular: within + combined objectives */}
          {autoGranular && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">Within Objective<HintTip text="How to rank and select assets within each direction (longs or shorts). Same objectives as Combined — pick any scoring method." /></label>
                <select value={withinObj} onChange={(e) => setWithinObj(e.target.value)}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                  {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">Combined Objective<HintTip text="How to score the final portfolio after combining longs and shorts. All 9 objectives available." /></label>
                <select value={combinedObj} onChange={(e) => setCombinedObj(e.target.value)}
                  className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
                  {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            </div>
          )}
        </>
      )}

      {/* Correlation + Algorithm (time-based only, not batch selection) */}
      {!eventMode && !isBatchSelection && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Correlation<HintTip text="Standard: Pearson correlation on daily PnL (zero-fill days with no trades). Trade Overlap: only correlates days where both assets have active trades." /></label>
            <select value={corrMethod} onChange={(e) => setCorrMethod(e.target.value)}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
              {CORRELATION_METHODS.map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Algorithm<HintTip text="Greedy: iterative correlation scoring (default). HRP: Hierarchical Risk Parity — clusters assets by similarity, allocates by inverse variance. PCA: Principal Component Analysis — groups assets by shared risk factors." /></label>
            <select value={divAlgo} onChange={(e) => setDivAlgo(e.target.value)}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm">
              {DIVERSIFICATION_ALGOS.map((a) => (
                <option key={a.value} value={a.value}>{a.label}</option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* Filters (hidden in batch selection — it has its own) */}
      {!isBatchSelection && <div className="grid grid-cols-2 gap-3">
        {!eventMode && (
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Min Positions<HintTip text="Assets with fewer positions than this are excluded. Ensures statistical significance. 0 = no filter." /></label>
            <input type="number" min={0} value={minTrades}
              onChange={(e) => setMinTrades(Number(e.target.value))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
        )}
        <div>
          <label className="text-xs text-ag-text-muted block mb-1">Min PF<HintTip text={eventMode ? "L1 quality gate: assets with PF below this are excluded from the portfolio. 1.0 = break-even." : "Minimum Profit Factor. Assets below this are excluded. 1.0 = break-even. Higher = more profitable required. 0 = no filter."} /></label>
          <input type="number" min={0} step={0.1} value={minPf}
            onChange={(e) => setMinPf(Number(e.target.value))}
            className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
        </div>
        <div>
          <label className="text-xs text-ag-text-muted block mb-1">Min WR %<HintTip text={eventMode ? "L1 quality gate: assets with Win Rate below this are excluded. 0 = no filter." : "Minimum Win Rate percentage. Assets below this are excluded. 0 = no filter."} /></label>
          <input type="number" min={0} max={100} value={minWr}
            onChange={(e) => setMinWr(Number(e.target.value))}
            className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
        </div>
        <div>
          <label className="text-xs text-ag-text-muted block mb-1">Extra Slip %<HintTip text="Simulates additional slippage per side (entry + exit). Crypto guidance: 0.01–0.03% conservative, 0.05% moderate, 0.10% aggressive." /></label>
          <input type="number" min={0} step={0.01} value={extraSlippage}
            onChange={(e) => setExtraSlippage(Number(e.target.value))}
            className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
        </div>
        <div>
          <label className="text-xs text-ag-text-muted block mb-1">Extra Comm %<HintTip text="Additional commission per side on top of backtest commission. Applied to entry + exit notional." /></label>
          <input type="number" min={0} step={0.01} value={extraCommission}
            onChange={(e) => setExtraCommission(Number(e.target.value))}
            className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
        </div>
        <div>
          <label className="text-xs text-ag-text-muted block mb-1">Funding %/d<HintTip text="Daily funding rate for perpetual contracts. Applied proportionally to hold duration. Bybit typical: ~0.03%/day." /></label>
          <input type="number" min={0} step={0.005} value={fundingDaily}
            onChange={(e) => setFundingDaily(Number(e.target.value))}
            className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
        </div>
        <div>
          <label className="text-xs text-ag-text-muted block mb-1">Spread %<HintTip text="Bid-ask spread cost. Half-spread applied on each side. Liquid large-caps: 0.01%. Mid-caps: 0.02-0.05%." /></label>
          <input type="number" min={0} step={0.01} value={spread}
            onChange={(e) => setSpread(Number(e.target.value))}
            className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
        </div>
        {!eventMode && (
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Max Seeds<HintTip text="Top N pre-ranked assets become starting points for the greedy optimizer. Higher = more thorough search but slower. The seed ranking uses the selected seed metrics (sortino, calmar, etc.) to narrow the pool before optimization." /></label>
            <input type="number" min={5} max={200} value={maxSeeds}
              onChange={(e) => setMaxSeeds(Math.max(5, Math.min(200, Number(e.target.value))))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
        )}
        {!eventMode && (
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Pool / Batch<HintTip text="Top K assets kept per batch after seed ranking. Ensures each strategy gets fair representation in the pool. 0 = auto (max(2×N Assets, Max Seeds)). Match your Cross-Strategy Pool/Batch setting for equivalent results." /></label>
            <input type="number" min={0} max={500} value={poolPerBatch}
              onChange={(e) => setPoolPerBatch(Math.max(0, Number(e.target.value)))}
              className="w-full px-3 py-1.5 bg-ag-dark border border-ag-border rounded text-sm" />
          </div>
        )}
      </div>}

      {/* Category filter */}
      <CategoryFilterSelect selected={categoryFilter} onChange={setCategoryFilter} />

      {/* Category Filter — rolling per-category composite multiplier (Phase 2 / hybrid event mode) */}
      <div className="border border-ag-border rounded-lg p-3 bg-ag-dark/40 space-y-2">
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={categoryFilterEnabled}
            onChange={(e) => setCategoryFilterEnabled(e.target.checked)}
            className="accent-ag-accent" />
          <span className="font-semibold">Category Filter (Phase 2 multiplier)<HintTip text="Boost or gate Phase 2 candidates by their CoinGecko category strength. Each rebalance, every category gets a rolling composite score from its tagged assets' recent trades; categories are tertile-bucketed (top/mid/bottom) and each candidate asset is multiplied by the most generous multiplier across its categories. Off = baseline behavior. Uses the symbol_categories table." /></span>
        </label>
        {categoryFilterEnabled && (
          <>
            <div className="grid grid-cols-4 gap-2 pl-6">
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">Mode<HintTip text="Category scoring mode. 'tertile' (legacy) = continuous top/mid/bottom multipliers from a rolling composite score — use with Influence low/mid/high/all. 'streak_event' = event-driven on-fire detector (session 2026-04-17-B T06 winner): categories are flagged 'on fire' when N consecutive pooled trades win, and assets in those categories get a fire_multiplier boost for fire_duration_trades after. Automatically forces Influence='event'." /></label>
                <select value={categoryMode}
                  onChange={(e) => setCategoryMode(e.target.value as "tertile" | "streak_event")}
                  className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs">
                  <option value="tertile">tertile (legacy)</option>
                  <option value="streak_event">streak_event (on-fire)</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">Influence<HintTip text="Strength of the category multiplier. For mode=tertile: none (passthrough), low (±10%), mid (±25%), high (±50%), all (HARD GATE — only top-tertile categories pass). For mode=streak_event: use 'event' (on-fire categories get fire_multiplier, others 1.00x)." /></label>
                <select value={categoryInfluence}
                  onChange={(e) => setCategoryInfluence(e.target.value as "none" | "low" | "mid" | "high" | "all" | "event")}
                  className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs">
                  <option value="none">none</option>
                  <option value="low">low (±10%)</option>
                  <option value="mid">mid (±25%)</option>
                  <option value="high">high (±50%)</option>
                  <option value="all">all (hard gate)</option>
                  <option value="event">event (on-fire boost)</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">Lookback Trades<HintTip text="How many recent pooled trades per category go into scoring. tertile mode: 15-30 sweet spot (short enough to be regime-sensitive, long enough to be stable). streak_event mode: set to ~100 so the 'last (streak+duration) pooled trades' window has enough history to detect streaks. Leakage guard: only trades closed strictly before the current decision are used." /></label>
                <input type="number" min={5} max={500} value={categoryLookbackTrades}
                  onChange={(e) => setCategoryLookbackTrades(Math.max(5, Math.min(500, Number(e.target.value))))}
                  className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
              </div>
              <div>
                <label className="text-xs text-ag-text-muted block mb-1">Max per Category<HintTip text="Cap on simultaneously-held portfolio assets sharing the same primary (first) category. 0 = uncapped. 1-3 prevents the portfolio from concentrating into a single hot category. Applied AFTER the category multiplier re-rank." /></label>
                <input type="number" min={0} max={50} value={categoryMaxPerCat}
                  onChange={(e) => setCategoryMaxPerCat(Math.max(0, Number(e.target.value)))}
                  className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
              </div>
            </div>
            {categoryMode === "streak_event" && (
              <div className="grid grid-cols-3 gap-2 pl-6">
                <div>
                  <label className="text-xs text-ag-text-muted block mb-1">Streak Required<HintTip text="Number of consecutive winning pooled trades required to flag a category as 'on fire'. Default 5. Smaller values (3-4) fire more often (noisier), larger (6-7) fire less often (sparser). Session 2026-04-17-B validated 5 as the T06 winner — parameter robustness still pending (item #4 in the session recommendations)." /></label>
                  <input type="number" min={1} max={20} value={categoryStreakRequired}
                    onChange={(e) => setCategoryStreakRequired(Math.max(1, Math.min(20, Number(e.target.value))))}
                    className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                </div>
                <div>
                  <label className="text-xs text-ag-text-muted block mb-1">Fire Multiplier<HintTip text="Score boost applied to assets in 'on fire' categories during the fire window. Default 1.30 (30% boost). Cold / untagged assets pass through at 1.00x. Higher values (1.5-2.0) concentrate exposure into hot categories more aggressively; use with caution — T06's +DD reduction came from 1.30, not higher." /></label>
                  <input type="number" step="0.05" min={1.0} max={3.0} value={categoryFireMultiplier}
                    onChange={(e) => setCategoryFireMultiplier(Math.max(1.0, Math.min(3.0, Number(e.target.value))))}
                    className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                </div>
                <div>
                  <label className="text-xs text-ag-text-muted block mb-1">Fire Duration Trades<HintTip text="How many pooled trades after the streak during which the boost stays on. Default 10. Streak is re-checked every rebalance — if the category is still hot, boost auto-continues; if it cools, boost decays after this many trades. Essentially the 'half-life' of the momentum signal." /></label>
                  <input type="number" min={0} max={100} value={categoryFireDurationTrades}
                    onChange={(e) => setCategoryFireDurationTrades(Math.max(0, Math.min(100, Number(e.target.value))))}
                    className="w-full px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs" />
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Session Filter */}
      <SessionFilterConfig value={sessionFilter} onChange={setSessionFilter} />

      {/* Prioritize Stability (time-based only, not batch selection) */}
      {!eventMode && !isBatchSelection && (
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={prioritizeStability}
            onChange={(e) => setPrioritizeStability(e.target.checked)}
            className="accent-ag-accent" />
          <span>Prioritize Stability<HintTip text="When enabled, drawdown and volatility are weighted higher in the objective scoring. Favors smoother equity curves over raw returns." /></span>
        </label>
      )}

      {/* Advanced (collapsible, time-based only, not batch selection) */}
      {!eventMode && !isBatchSelection && (
        <OptimizerAdvanced show={showAdvanced} onToggle={() => setShowAdvanced(!showAdvanced)}
          seedMetrics={seedMetrics} onSeedMetricsChange={setSeedMetrics}
          maxSeeds={maxSeeds} onMaxSeedsChange={setMaxSeeds}
          dateFrom={dateFrom} onDateFromChange={setDateFrom}
          recencyEnabled={recencyEnabled} onRecencyEnabledChange={setRecencyEnabled}
          recencyMode={recencyMode} onRecencyModeChange={setRecencyMode}
          recencyBatchSize={recencyBatchSize} onRecencyBatchSizeChange={setRecencyBatchSize}
          recencyBoostFactor={recencyBoostFactor} onRecencyBoostFactorChange={setRecencyBoostFactor}
          prefilterEnabled={prefilterEnabled} onPrefilterEnabledChange={setPrefilterEnabled}
          prefilterMode={prefilterMode} onPrefilterModeChange={setPrefilterMode}
          prefilterHurstMin={prefilterHurstMin} onPrefilterHurstMinChange={setPrefilterHurstMin}
          prefilterTerPercentile={prefilterTerPercentile} onPrefilterTerPercentileChange={setPrefilterTerPercentile}
          prefilterHurstMax={prefilterHurstMax} onPrefilterHurstMaxChange={setPrefilterHurstMax}
          prefilterVrMax={prefilterVrMax} onPrefilterVrMaxChange={setPrefilterVrMax}
          prefilterMinCrossingRate={prefilterMinCrossingRate} onPrefilterMinCrossingRateChange={setPrefilterMinCrossingRate}
          acPostfilter={acPostfilter} onAcPostfilterChange={setAcPostfilter}
          acPostfilterType={acPostfilterType} onAcPostfilterTypeChange={setAcPostfilterType}
          acLag={acLag} onAcLagChange={setAcLag}
          avgRangeBullThreshold={avgRangeBullThreshold} onAvgRangeBullThresholdChange={setAvgRangeBullThreshold}
          avgRangeBearThreshold={avgRangeBearThreshold} onAvgRangeBearThresholdChange={setAvgRangeBearThreshold}
          avgRangeAllThreshold={avgRangeAllThreshold} onAvgRangeAllThresholdChange={setAvgRangeAllThreshold}
          avgRangeFilterMode={avgRangeFilterMode} onAvgRangeFilterModeChange={setAvgRangeFilterMode}
          hideMaxSeeds hideDateFrom />
      )}

      {/* TC Constraints (collapsible, time-based only, not batch selection) */}
      {!eventMode && !isBatchSelection && (
        <OptimizerTC show={showTC} onToggle={() => setShowTC(!showTC)}
          splitMode={wfoMode === "auto" && splitMode} enabled={tcEnabled} onEnabledChange={setTcEnabled}
          tc={tc} onTCChange={setTc}
          tcLongs={tcLongs} onTCLongsChange={setTcLongs}
          tcShorts={tcShorts} onTCShortsChange={setTcShorts} />
      )}

      {/* Benchmark (collapsible, time-based only, not batch selection) */}
      {!eventMode && !isBatchSelection && (
        <OptimizerBenchmark show={showBM} onToggle={() => setShowBM(!showBM)}
          enabled={bmEnabled} onEnabledChange={setBmEnabled}
          config={bmConfig} onConfigChange={setBmConfig} />
      )}

      {/* Top N quick buttons */}
      {!eventMode && !isBatchSelection && wfoMode !== "directional" && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-ag-text-muted">Quick Top N:</span>
          {[3, 5, 8, 10, 15].map((n) => (
            <button key={n} onClick={() => setNAssets(n)}
              className={`px-2 py-0.5 rounded text-xs ${nAssets === n ? "bg-ag-accent/15 text-ag-accent font-medium" : "text-ag-text-muted hover:text-ag-text"}`}>
              Top {n}
            </button>
          ))}
        </div>
      )}

      {/* Run button */}
      <button onClick={handleRun}
        disabled={totalSources === 0 || running || (wfoMode === "manual" && !canManual) || (wfoMode === "directional" && !canDirectional)}
        className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-ag-accent text-white rounded-lg font-medium text-sm hover:bg-ag-accent/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
        {running ? (
          <>
            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"/></svg>
            Running...
          </>
        ) : (
          <>Run WFO</>
        )}
      </button>
    </div>
  );
}
