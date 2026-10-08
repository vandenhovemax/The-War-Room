"use client";
import { useState, useEffect, useRef } from "react";
import { api } from "@/lib/api";
import type { OptimizerCandidate, LSOptimizerCandidate, CorrelationResult, OOSCompareResult, PoolStats, SessionTimeMask, MaskPreviewResult } from "@/lib/api";
import PoolStatsPanel from "./PoolStatsPanel";
import CorrelationHeatmap from "./CorrelationHeatmap";
import OptimizerAdvanced from "./OptimizerAdvanced";
import OptimizerTC from "./OptimizerTC";
import type { TCConstraints } from "./OptimizerTC";
import OptimizerBenchmark from "./OptimizerBenchmark";
import type { BenchmarkConfig } from "./OptimizerBenchmark";
import CandidateCard from "./CandidateCard";
import OptimizerConfigManager from "./OptimizerConfigManager";
import OptimizerRiskProfile from "./OptimizerRiskProfile";
import type { RiskProfileConfig, AutoRiskResult } from "./OptimizerRiskProfile";
import { ALL_OBJECTIVES, DIVERSIFICATION_ALGOS } from "./objectiveConstants";
import HintTip from "@/components/common/HintTip";
import UserNote from "./UserNote";
import CategoryFilterSelect from "@/components/common/CategoryFilterSelect";
import OptimizationProgress from "@/components/common/OptimizationProgress";
import SavedMasksList from "@/components/common/SavedMasksList";
import SessionFilterConfig, { defaultSessionFilter } from "@/components/common/SessionFilterConfig";
import type { SessionFilterState } from "@/components/common/SessionFilterConfig";
import { useTaskManager } from "@/contexts/TaskManager";

interface OptimizerPanelProps {
  batchId: number;
  totalSymbols: number;
  dateTo?: string;
  directionFilter?: string;
  dataEndDate?: string;
}

export type AnyCandidate = OptimizerCandidate | LSOptimizerCandidate;

export function isLSCandidate(c: AnyCandidate): c is LSOptimizerCandidate {
  return "longs" in c;
}

export default function OptimizerPanel({ batchId, totalSymbols, dateTo, directionFilter, dataEndDate }: OptimizerPanelProps) {
  const [splitMode, setSplitMode] = useState(false);
  // Standard mode
  const [nAssets, setNAssets] = useState(Math.min(5, totalSymbols));
  const [objective, setObjective] = useState("hybrid");
  // L/S split mode
  const [nLongs, setNLongs] = useState(3);
  const [nShorts, setNShorts] = useState(3);
  const [withinObj, setWithinObj] = useState("uncorrelated");
  const [combinedObj, setCombinedObj] = useState("stability");
  // Shared
  const [corrMethod, setCorrMethod] = useState("standard");
  const [divAlgo, setDivAlgo] = useState("greedy");
  const [nPortfolios, setNPortfolios] = useState(3);
  // Pre-optimization filters
  const [minTrades, setMinTrades] = useState(3);
  const [minPF, setMinPF] = useState(1.3);
  const [minWR, setMinWR] = useState(0);
  const [extraSlippage, setExtraSlippage] = useState(0);
  const [extraCommission, setExtraCommission] = useState(0);
  const [fundingDaily, setFundingDaily] = useState(0);
  const [spread, setSpread] = useState(0);
  const [showFilters, setShowFilters] = useState(false);
  // Advanced: seed ranking, stability, start date
  const [seedMetrics, setSeedMetrics] = useState<string[]>(["sortino"]);
  const [maxSeeds, setMaxSeeds] = useState(20);
  const [seedWeights, setSeedWeights] = useState<Record<string, number>>({});
  const [prioritizeStability, setPrioritizeStability] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  // Trade-count constraints
  const [tcEnabled, setTcEnabled] = useState(false);
  const [showTC, setShowTC] = useState(false);
  const [tc, setTc] = useState<TCConstraints>({ low_threshold: 10, max_low: 2, high_threshold: 50, min_high: 1 });
  const [tcLongs, setTcLongs] = useState<TCConstraints>({ low_threshold: 10, max_low: 2, high_threshold: 50, min_high: 1 });
  const [tcShorts, setTcShorts] = useState<TCConstraints>({ low_threshold: 10, max_low: 2, high_threshold: 50, min_high: 1 });
  // Benchmark robustness
  const [bmEnabled, setBmEnabled] = useState(false);
  const [showBM, setShowBM] = useState(false);
  const [bmConfig, setBmConfig] = useState<BenchmarkConfig>({
    source: "", split: "time", threshold: 1.2, elite_target: 1.0, exclude_fragile: true,
    consistency_blend: 0, fragility_blend: 0, wr_harmony_blend: 0, dsr_gate: false,
  });
  // Recency-weighted seed scoring
  const [recencyEnabled, setRecencyEnabled] = useState(false);
  const [recencyMode, setRecencyMode] = useState("gradual");
  const [recencyBatchSize, setRecencyBatchSize] = useState(10);
  const [recencyBoostFactor, setRecencyBoostFactor] = useState(3.0);
  // Category filter
  const [categoryFilter, setCategoryFilter] = useState<string[]>([]);
  // Portfolio diversity
  const [maxOverlap, setMaxOverlap] = useState("off");
  // Risk profile
  const [showRiskProfile, setShowRiskProfile] = useState(false);
  const [riskProfile, setRiskProfile] = useState<RiskProfileConfig>({
    mode: "fixed", value: 0, initialCapital: 10000,
    multiplier: 2, maxStreak: 3, martingaleCompound: "off", martingaleScope: "asset",
    martingaleCountMode: "full_position", martingaleOosReset: true,
    martingaleStartAfter: 0, antiMartingaleResetAfter: 0, kellyN: 2, kellyWarmup: 20,
    kellyScope: "asset", kellyLookback: 50, kellyCap: 0.25, slippage: 0,
  });
  // Auto risk profile
  const [autoRiskEnabled, setAutoRiskEnabled] = useState(false);
  const [autoRiskAggression, setAutoRiskAggression] = useState("conservative");
  const [autoRiskLoading, setAutoRiskLoading] = useState(false);
  const [autoRiskResult, setAutoRiskResult] = useState<AutoRiskResult | null>(null);
  const [autoBaseRiskMart, setAutoBaseRiskMart] = useState(50);
  const [autoMinConfidence, setAutoMinConfidence] = useState(0);
  const [autoStrategyHint, setAutoStrategyHint] = useState("auto");
  // Pre-filter (structural quality)
  const [prefilterEnabled, setPrefilterEnabled] = useState(false);
  const [prefilterMode, setPrefilterMode] = useState("trend");
  const [prefilterHurstMin, setPrefilterHurstMin] = useState(0.50);
  const [prefilterTerPercentile, setPrefilterTerPercentile] = useState(25);
  const [prefilterHurstMax, setPrefilterHurstMax] = useState(0.50);
  const [prefilterVrMax, setPrefilterVrMax] = useState(1.0);
  const [prefilterMinCrossingRate, setPrefilterMinCrossingRate] = useState(0.05);
  // AC post-filter
  const [acPostfilter, setAcPostfilter] = useState(false);
  const [acPostfilterType, setAcPostfilterType] = useState("trend");
  const [acLag, setAcLag] = useState(1);
  // Avg Range seed config
  const [avgRangeBullThreshold, setAvgRangeBullThreshold] = useState(0);
  const [avgRangeBearThreshold, setAvgRangeBearThreshold] = useState(0);
  const [avgRangeAllThreshold, setAvgRangeAllThreshold] = useState(0);
  const [avgRangeFilterMode, setAvgRangeFilterMode] = useState(false);
  // Asset Quality seed
  const [assetQualityMode, setAssetQualityMode] = useState("trend");
  const [assetQualityGradeMode, setAssetQualityGradeMode] = useState("absolute");
  const [assetQualityMinGrade, setAssetQualityMinGrade] = useState("");
  const [assetQualityMinTrades, setAssetQualityMinTrades] = useState(0);
  const [assetQualityFilter, setAssetQualityFilter] = useState(false);
  const [assetQualityHurstMin, setAssetQualityHurstMin] = useState(0.50);
  const [assetQualityTerPercentile, setAssetQualityTerPercentile] = useState(25);
  const [assetQualityHurstMax, setAssetQualityHurstMax] = useState(0.50);
  const [assetQualityVrMax, setAssetQualityVrMax] = useState(1.0);
  const [assetQualityMinCr, setAssetQualityMinCr] = useState(0.05);
  // Strategy Quality seed
  const [stratQualityMode, setStratQualityMode] = useState("trend");
  const [stratQualityMinGrade, setStratQualityMinGrade] = useState("");
  const [stratQualityFilter, setStratQualityFilter] = useState(false);
  // IS-WFE seed
  const [isWfeIsPct, setIsWfeIsPct] = useState(60);
  const [isWfeMinTrades, setIsWfeMinTrades] = useState(0);
  const [isWfeFilter, setIsWfeFilter] = useState(false);
  const [isWfeMinWfe, setIsWfeMinWfe] = useState(0.5);
  // Max drawdown hard limit
  const [maxAllowedDdPct, setMaxAllowedDdPct] = useState(0);
  // Bybit size gate
  const [bybitSizeGate, setBybitSizeGate] = useState<"off" | "binary" | "rate">("off");
  const [bybitSizeGateMaxPct, setBybitSizeGateMaxPct] = useState(10);
  // Session Time Mask (legacy — kept for backward compat with saved mask mode)
  const [sessionMaskId, setSessionMaskId] = useState<number | null>(null);
  const [masks, setMasks] = useState<SessionTimeMask[]>([]);
  const [maskPreview, setMaskPreview] = useState<MaskPreviewResult | null>(null);
  const [maskPreviewLoading, setMaskPreviewLoading] = useState(false);
  // Session Filter (new inline config)
  const [sessionFilter, setSessionFilter] = useState<SessionFilterState>(defaultSessionFilter);
  // Boundary clipping
  const [clipIS, setClipIS] = useState(true);
  const [clipOOS, setClipOOS] = useState(true);
  const [oosEndDate, setOosEndDate] = useState("");
  const oosDateManualRef = useRef(false);

  // Auto-default OOS end date from batch data_end_date
  useEffect(() => {
    if (oosDateManualRef.current) return;
    if (dataEndDate) setOosEndDate(dataEndDate.slice(0, 10));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataEndDate]);

  // Fetch saved session masks on mount
  useEffect(() => {
    api.sessionMasks().then(setMasks).catch(() => {});
  }, []);

  // Fetch mask preview when mask selected
  useEffect(() => {
    if (!sessionMaskId) { setMaskPreview(null); return; }
    setMaskPreviewLoading(true);
    const nfp = splitMode ? nLongs + nShorts : nAssets;
    api.sessionMaskPreview({ batch_id: batchId, mask_id: sessionMaskId, date_to: dateTo, date_from: undefined, n_free_params: nfp })
      .then(setMaskPreview)
      .catch(() => setMaskPreview(null))
      .finally(() => setMaskPreviewLoading(false));
  }, [sessionMaskId, batchId, dateTo, splitMode, nLongs, nShorts, nAssets]);

  const [candidates, setCandidates] = useState<AnyCandidate[]>([]);
  const [poolStats, setPoolStats] = useState<PoolStats | null>(null);
  const [corr, setCorr] = useState<CorrelationResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [optimStep, setOptimStep] = useState(0);
  const [optimTotal, setOptimTotal] = useState(0);
  const [optimLabel, setOptimLabel] = useState("");
  const [saving, setSaving] = useState<number | null>(null);
  const [savedIndices, setSavedIndices] = useState<Set<number>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const stored = localStorage.getItem("optimizer_saved_indices");
      return stored ? new Set(JSON.parse(stored) as number[]) : new Set();
    } catch { return new Set(); }
  });
  const [error, setError] = useState("");
  const [buildId, setBuildId] = useState(0);

  // Persist savedIndices to localStorage
  useEffect(() => {
    localStorage.setItem("optimizer_saved_indices", JSON.stringify([...savedIndices]));
  }, [savedIndices]);

  // Persist results across navigation via TaskManager
  const { startOptimization, latestOptimizationTask, addToast } = useTaskManager();
  const TASK_LABEL = "Single Strategy Optimization";

  // Config save/load — collect all settings into a single object
  const currentConfig = {
    splitMode, nAssets, objective, nLongs, nShorts, withinObj, combinedObj,
    corrMethod, divAlgo, nPortfolios, minTrades, minPF, minWR, extraSlippage, extraCommission, fundingDaily, spread,
    seedMetrics, maxSeeds, seedWeights, prioritizeStability, dateFrom,
    tcEnabled, tc, tcLongs, tcShorts,
    bmEnabled, bmConfig,
    recencyEnabled, recencyMode, recencyBatchSize, recencyBoostFactor,
    categoryFilter, maxOverlap, riskProfile,
    prefilterEnabled, prefilterMode, prefilterHurstMin, prefilterTerPercentile,
    prefilterHurstMax, prefilterVrMax, prefilterMinCrossingRate,
    acPostfilter, acPostfilterType, acLag,
    avgRangeBullThreshold, avgRangeBearThreshold, avgRangeAllThreshold, avgRangeFilterMode,
    assetQualityMode, assetQualityGradeMode, assetQualityMinGrade, assetQualityMinTrades,
    assetQualityFilter, assetQualityHurstMin, assetQualityTerPercentile,
    assetQualityHurstMax, assetQualityVrMax, assetQualityMinCr,
    stratQualityMode, stratQualityMinGrade, stratQualityFilter,
    isWfeIsPct, isWfeMinTrades, isWfeFilter, isWfeMinWfe,
    maxAllowedDdPct, bybitSizeGate, bybitSizeGateMaxPct, clipIS, clipOOS, oosEndDate,
    sessionMaskId,
  };

  // Build descriptive default name from current settings
  const buildDefaultName = (): string => {
    // Format: objective / algo / seed ranking(s) / correlation
    const obj = objective || "stability";
    const algo = divAlgo || "greedy";
    const seeds = seedMetrics.length ? seedMetrics.join(",") : "none";
    const corr = corrMethod || "standard";
    return `${obj} / ${algo} / ${seeds} / ${corr}`;
  };

  const loadConfig = (c: typeof currentConfig) => {
    setSplitMode(c.splitMode ?? false);
    setNAssets(c.nAssets ?? 5);
    setObjective(c.objective ?? "hybrid");
    setNLongs(c.nLongs ?? 3);
    setNShorts(c.nShorts ?? 3);
    setWithinObj(c.withinObj ?? "uncorrelated");
    setCombinedObj(c.combinedObj ?? "stability");
    setCorrMethod(c.corrMethod ?? "standard");
    setDivAlgo(c.divAlgo ?? "greedy");
    setNPortfolios(c.nPortfolios ?? 3);
    setMinTrades(c.minTrades ?? 3);
    setMinPF(c.minPF ?? 1.3);
    setMinWR(c.minWR ?? 0);
    setExtraSlippage(c.extraSlippage ?? 0);
    setExtraCommission(c.extraCommission ?? 0);
    setFundingDaily(c.fundingDaily ?? 0);
    setSpread(c.spread ?? 0);
    setSeedMetrics(c.seedMetrics ?? ["sortino"]);
    setMaxSeeds(c.maxSeeds ?? 20);
    setSeedWeights(c.seedWeights ?? {});
    setPrioritizeStability(c.prioritizeStability ?? false);
    setDateFrom(c.dateFrom ?? "");
    setTcEnabled(c.tcEnabled ?? false);
    if (c.tc) setTc(c.tc);
    if (c.tcLongs) setTcLongs(c.tcLongs);
    if (c.tcShorts) setTcShorts(c.tcShorts);
    setBmEnabled(c.bmEnabled ?? false);
    if (c.bmConfig) setBmConfig(c.bmConfig);
    setRecencyEnabled(c.recencyEnabled ?? false);
    setRecencyMode(c.recencyMode ?? "gradual");
    setRecencyBatchSize(c.recencyBatchSize ?? 10);
    setRecencyBoostFactor(c.recencyBoostFactor ?? 3.0);
    setCategoryFilter(c.categoryFilter ?? []);
    setMaxOverlap(c.maxOverlap ?? "off");
    if (c.riskProfile) setRiskProfile(c.riskProfile);
    setPrefilterEnabled(c.prefilterEnabled ?? false);
    setPrefilterMode(c.prefilterMode ?? "trend");
    setPrefilterHurstMin(c.prefilterHurstMin ?? 0.50);
    setPrefilterTerPercentile(c.prefilterTerPercentile ?? 25);
    setPrefilterHurstMax(c.prefilterHurstMax ?? 0.50);
    setPrefilterVrMax(c.prefilterVrMax ?? 1.0);
    setPrefilterMinCrossingRate(c.prefilterMinCrossingRate ?? 0.05);
    setAcPostfilter(c.acPostfilter ?? false);
    setAcPostfilterType(c.acPostfilterType ?? "trend");
    setAcLag(c.acLag ?? 1);
    setAvgRangeBullThreshold(c.avgRangeBullThreshold ?? 0);
    setAvgRangeBearThreshold(c.avgRangeBearThreshold ?? 0);
    setAvgRangeAllThreshold(c.avgRangeAllThreshold ?? 0);
    setAvgRangeFilterMode(c.avgRangeFilterMode ?? false);
    setAssetQualityMode(c.assetQualityMode ?? "trend");
    setAssetQualityGradeMode(c.assetQualityGradeMode ?? "absolute");
    setAssetQualityMinGrade(c.assetQualityMinGrade ?? "");
    setAssetQualityMinTrades(c.assetQualityMinTrades ?? 0);
    setAssetQualityFilter(c.assetQualityFilter ?? false);
    setAssetQualityHurstMin(c.assetQualityHurstMin ?? 0.50);
    setAssetQualityTerPercentile(c.assetQualityTerPercentile ?? 25);
    setAssetQualityHurstMax(c.assetQualityHurstMax ?? 0.50);
    setAssetQualityVrMax(c.assetQualityVrMax ?? 1.0);
    setAssetQualityMinCr(c.assetQualityMinCr ?? 0.05);
    setStratQualityMode(c.stratQualityMode ?? "trend");
    setStratQualityMinGrade(c.stratQualityMinGrade ?? "");
    setStratQualityFilter(c.stratQualityFilter ?? false);
    setIsWfeIsPct(c.isWfeIsPct ?? 60);
    setIsWfeMinTrades(c.isWfeMinTrades ?? 0);
    setIsWfeFilter(c.isWfeFilter ?? false);
    setIsWfeMinWfe(c.isWfeMinWfe ?? 0.5);
    setMaxAllowedDdPct(c.maxAllowedDdPct ?? 0);
    setBybitSizeGate(c.bybitSizeGate ?? "off");
    setBybitSizeGateMaxPct(c.bybitSizeGateMaxPct ?? 10);
    setClipIS(c.clipIS ?? true);
    setClipOOS(c.clipOOS ?? true);
    setOosEndDate(c.oosEndDate ?? "");
    if (c.oosEndDate) oosDateManualRef.current = true;
    setSessionMaskId(c.sessionMaskId ?? null);
    addToast("Config loaded!", "info");
  };

  const advOpts = {
    seed_metrics: seedMetrics,
    max_seeds: maxSeeds,
    ...(Object.keys(seedWeights).length > 0 ? { seed_weights: seedWeights } : {}),
    prioritize_stability: prioritizeStability,
    date_from: dateFrom || undefined,
    // TC constraints — combined mode
    ...(tcEnabled && !splitMode ? {
      tc_low_threshold: tc.low_threshold, tc_max_low: tc.max_low,
      tc_high_threshold: tc.high_threshold, tc_min_high: tc.min_high,
    } : {}),
    // TC constraints — split mode
    ...(tcEnabled && splitMode ? { tc_longs: tcLongs, tc_shorts: tcShorts } : {}),
    // Benchmark robustness
    ...(bmEnabled ? {
      benchmark_enabled: true, benchmark_source: bmConfig.source,
      benchmark_split: bmConfig.split, benchmark_threshold: bmConfig.threshold,
      benchmark_elite_target: bmConfig.elite_target, benchmark_exclude_fragile: bmConfig.exclude_fragile,
      benchmark_consistency_blend: bmConfig.consistency_blend,
      benchmark_fragility_blend: bmConfig.fragility_blend,
      benchmark_wr_harmony_blend: bmConfig.wr_harmony_blend,
    } : {}),
    // Recency-weighted seed scoring
    ...(recencyEnabled ? {
      recency_enabled: true, recency_mode: recencyMode,
      recency_batch_size: recencyBatchSize, recency_boost_factor: recencyBoostFactor,
    } : {}),
    div_algo: divAlgo,
    // Category filter
    ...(categoryFilter.length > 0 ? { category_filter: categoryFilter } : {}),
    // Portfolio diversity
    ...(maxOverlap !== "off" ? { max_overlap: parseFloat(maxOverlap) } : {}),
    // Risk profile
    ...(riskProfile.mode !== "fixed" || riskProfile.value > 0 ? {
      risk_profile_mode: riskProfile.mode,
      risk_profile_value: riskProfile.value,
      risk_profile_initial_capital: riskProfile.initialCapital,
      risk_profile_multiplier: riskProfile.multiplier,
      risk_profile_max_streak: riskProfile.maxStreak,
      risk_profile_martingale_compound: riskProfile.martingaleCompound,
      risk_profile_martingale_scope: riskProfile.martingaleScope,
      risk_profile_martingale_count_mode: riskProfile.martingaleCountMode,
      risk_profile_martingale_oos_reset: riskProfile.martingaleOosReset,
      risk_profile_martingale_start_after: riskProfile.martingaleStartAfter,
      risk_profile_anti_martingale_reset_after: riskProfile.antiMartingaleResetAfter,
      risk_profile_kelly_n: riskProfile.kellyN,
      risk_profile_kelly_warmup: riskProfile.kellyWarmup,
    } : {}),
    // Auto-risk per-asset configs (when auto-risk analysis has been run)
    ...(autoRiskEnabled && autoRiskResult?.per_asset ? {
      risk_profile_auto_asset_configs: JSON.stringify(
        Object.fromEntries(
          Object.entries(autoRiskResult.per_asset).map(([sym, cfg]) => [sym, {
            ...cfg,
            valueFixed: autoRiskResult.config.valueFixed,
            valueMartingale: autoRiskResult.config.valueMartingale,
            initialCapital: autoRiskResult.config.initialCapital,
            multiplier: autoRiskResult.config.multiplier,
          }])
        )
      ),
    } : {}),
    // Pre-filter (structural quality)
    ...(prefilterEnabled ? {
      prefilter_enabled: true, prefilter_mode: prefilterMode,
      prefilter_hurst_min: prefilterHurstMin, prefilter_ter_percentile: prefilterTerPercentile,
      prefilter_hurst_max: prefilterHurstMax, prefilter_vr_max: prefilterVrMax,
      prefilter_min_crossing_rate: prefilterMinCrossingRate,
    } : {}),
    // DSR benchmark gate
    ...(bmEnabled && bmConfig.dsr_gate ? { benchmark_dsr_gate: true } : {}),
    // AC post-filter
    ...(acPostfilter ? {
      ac_postfilter: true, ac_postfilter_type: acPostfilterType, ac_lag: acLag,
    } : {}),
    // Avg Range seed config
    ...(seedMetrics.includes("avg_range") ? {
      avg_range_bull_threshold: avgRangeBullThreshold,
      avg_range_bear_threshold: avgRangeBearThreshold,
      avg_range_all_threshold: avgRangeAllThreshold,
      avg_range_filter_mode: avgRangeFilterMode,
    } : {}),
    // Asset Quality seed
    ...(seedMetrics.includes("asset_quality") ? {
      asset_quality_mode: assetQualityMode,
      asset_quality_grade_mode: assetQualityGradeMode,
      asset_quality_min_grade: assetQualityMinGrade,
      asset_quality_min_trades: assetQualityMinTrades,
      asset_quality_filter: assetQualityFilter,
      asset_quality_hurst_min: assetQualityHurstMin,
      asset_quality_ter_percentile: assetQualityTerPercentile,
      asset_quality_hurst_max: assetQualityHurstMax,
      asset_quality_vr_max: assetQualityVrMax,
      asset_quality_min_cr: assetQualityMinCr,
    } : {}),
    // Strategy Quality seed
    ...(seedMetrics.includes("strat_quality") ? {
      strat_quality_mode: stratQualityMode,
      strat_quality_min_grade: stratQualityMinGrade,
      strat_quality_filter: stratQualityFilter,
    } : {}),
    // IS-WFE seed
    ...(seedMetrics.includes("is_wfe") ? {
      is_wfe_is_pct: isWfeIsPct,
      is_wfe_min_trades: isWfeMinTrades,
      is_wfe_filter: isWfeFilter,
      is_wfe_min_wfe: isWfeMinWfe,
    } : {}),
    // Max drawdown hard limit
    ...(maxAllowedDdPct > 0 ? { max_allowed_drawdown_pct: maxAllowedDdPct } : {}),
    // Bybit size gate
    ...(bybitSizeGate !== "off" ? {
      bybit_size_gate: true,
      bybit_size_gate_max_reject_pct: bybitSizeGate === "binary" ? 0 : bybitSizeGateMaxPct,
    } : {}),
    // Boundary clipping
    clip_is: clipIS,
    // Post-hoc trading costs
    ...(extraCommission > 0 ? { extra_commission_pct: extraCommission } : {}),
    ...(fundingDaily > 0 ? { funding_daily_pct: fundingDaily } : {}),
    ...(spread > 0 ? { spread_pct: spread } : {}),
    // Session Time Mask (legacy — only when inline filter is off)
    ...(!sessionFilter.enabled && sessionMaskId ? { session_mask_id: sessionMaskId } : {}),
    // Session Filter (inline)
    ...(sessionFilter.enabled ? {
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
    } : {}),
  };

  // Gate: disable optimizer when mask preview fails safeguard thresholds
  const maskGateFailed = !!(!sessionFilter.enabled && sessionMaskId && maskPreview && !maskPreview.ok);

  // ── Auto Risk Profile: analyze IS streaks and apply result ──
  const runAutoRiskAnalyze = async () => {
    setAutoRiskLoading(true);
    try {
      const data = await api.autoRiskProfile({
        batch_ids: [batchId],
        date_to: dateTo || undefined,
        base_risk: riskProfile.value || 100,
        base_risk_mart: autoBaseRiskMart,
        multiplier: riskProfile.multiplier,
        initial_capital: riskProfile.initialCapital,
        aggression: autoRiskAggression,
        min_confidence: autoMinConfidence,
        strategy_hint: autoStrategyHint,
      });
      setAutoRiskResult(data);
      setRiskProfile((prev) => ({
        ...prev,
        mode: data.config.mode,
        value: data.config.value || prev.value,
        maxStreak: data.config.maxStreak,
        martingaleStartAfter: data.config.martingaleStartAfter,
        antiMartingaleResetAfter: data.config.antiMartingaleResetAfter,
      }));
    } catch {
      // silently fail — user can retry
    } finally {
      setAutoRiskLoading(false);
    }
  };

  const runOptimizer = () => {
    setLoading(true);
    setError("");
    setCandidates([]);
    setPoolStats(null);
    setCorr(null);
    setSavedIndices(new Set());
    setBuildId((prev) => prev + 1);
    setOptimStep(0);
    setOptimTotal(0);
    setOptimLabel("");

    // Register with TaskManager — single request, results stored in context
    // TaskManager fires toast on completion and persists results across navigation
    const fetchFn = async (taskProgress?: (pct: number, label: string) => void, onBackendTaskId?: (backendId: string) => void) => {
      const onProgress = (step: number, totalSteps: number, label: string) => {
        setOptimStep(step);
        setOptimTotal(totalSteps);
        setOptimLabel(label);
        if (taskProgress) {
          const pct = totalSteps > 0 ? Math.round((step / totalSteps) * 100) : 0;
          taskProgress(pct, label);
        }
      };
      if (splitMode) {
        const [optResult, corrResult] = await Promise.all([
          api.optimizeLS(
            batchId, nLongs, nShorts, withinObj, combinedObj, corrMethod, nPortfolios, dateTo,
            minTrades, minPF, minWR, extraSlippage, advOpts, onProgress, onBackendTaskId,
          ),
          api.correlation(batchId, corrMethod, minTrades, minPF, minWR, dateTo),
        ]);
        return {
          candidates: optResult.candidates,
          poolStats: optResult.pool_stats ?? null,
          corr: corrResult,
        };
      } else {
        const [optResult, corrResult] = await Promise.all([
          api.optimize(
            batchId, nAssets, objective, corrMethod, nPortfolios, dateTo,
            minTrades, minPF, minWR, extraSlippage, advOpts, onProgress, onBackendTaskId,
          ),
          api.correlation(batchId, corrMethod, minTrades, minPF, minWR, dateTo),
        ]);
        return {
          candidates: optResult.candidates,
          poolStats: optResult.pool_stats ?? null,
          corr: corrResult,
        };
      }
    };

    startOptimization(fetchFn, TASK_LABEL);
  };

  // Sync local state from TaskManager when results arrive (single source of truth)
  useEffect(() => {
    const task = latestOptimizationTask(TASK_LABEL);
    if (!task) return;
    if (task.status === "completed" && task.result) {
      setCandidates(task.result.candidates as AnyCandidate[]);
      setPoolStats(task.result.poolStats);
      setCorr(task.result.corr);
      setLoading(false);
    }
    if (task.status === "error") {
      setError(task.error ?? "Optimization failed");
      setLoading(false);
    }
  }, [latestOptimizationTask]);

  const saveCandidate = async (idx: number, c: AnyCandidate) => {
    setSaving(idx);
    try {
      const assets = c.assets.map((s) => {
        // Determine side from suffix; fall back to batch direction_filter
        const side = s.endsWith("_Long") ? "Long"
          : s.endsWith("_Short") ? "Short"
          : directionFilter === "long" ? "Long"
          : directionFilter === "short" ? "Short"
          : "both";
        const symbol = s.replace(/_Long$/, "").replace(/_Short$/, "");
        // Attach auto-risk mode if available
        const riskMode = autoRiskEnabled && autoRiskResult?.per_asset
          ? autoRiskResult.per_asset[symbol]?.mode ?? autoRiskResult.per_asset[s]?.mode
          : undefined;
        return { symbol, side, batch_id: batchId, risk_mode: riskMode };
      });
      await api.savePortfolio(
        `Portfolio ${new Date().toLocaleDateString()} #${idx + 1}`,
        assets, batchId,
        {
          // Spread ALL advanced optimizer settings (seeds, filters, prefilters,
          // AC, asset/strat quality, IS-WFE, max DD, clip_is, benchmark DSR,
          // risk profile, recency, TC, benchmark, etc.)
          ...advOpts,
          // Top-level fields not in advOpts
          objective: splitMode ? `${withinObj}/${combinedObj}` : objective,
          corr_method: corrMethod, split_mode: splitMode,
          n_assets: splitMode ? nLongs + nShorts : nAssets,
          n_portfolios: nPortfolios,
          min_trades: minTrades, min_pf: minPF, min_wr: minWR,
          date_to: dateTo,
          extra_slippage_pct: extraSlippage,
          ...(splitMode ? { n_longs: nLongs, n_shorts: nShorts, within_objective: withinObj, combined_objective: combinedObj } : {}),
          ...(tcEnabled ? { tc_enabled: true } : {}),
          ...(oosEndDate ? { oos_end_date: oosEndDate } : {}),
          clip_oos: clipOOS,
        },
        { ...c.metrics },
        { optimization_end_date: dateTo || undefined, portfolio_source: "optimizer" },
      );
      setSavedIndices((prev) => new Set([...prev, idx]));
      addToast("Portfolio saved!", "success");
    } catch (e) {
      addToast(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="space-y-4">
      {/* Config save/load */}
      <OptimizerConfigManager storageKey="optimizer_single_configs" currentConfig={currentConfig} onLoad={loadConfig} defaultName={buildDefaultName()} />

      {/* Mode toggle + shared settings */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex rounded-lg border border-ag-border overflow-hidden">
          <button onClick={() => setSplitMode(false)}
            className={`px-3 py-1 text-xs font-medium transition-colors ${!splitMode ? "bg-ag-accent text-white" : "bg-ag-dark text-ag-text-muted hover:text-ag-text"}`}>
            Combined
          </button>
          <button onClick={() => setSplitMode(true)}
            className={`px-3 py-1 text-xs font-medium transition-colors ${splitMode ? "bg-ag-accent text-white" : "bg-ag-dark text-ag-text-muted hover:text-ag-text"}`}>
            Long / Short Split
          </button>
        </div>
        <span className="text-xs text-ag-text-muted">{totalSymbols} symbols</span>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Results:<HintTip text="Number of portfolio candidates to generate. More = more options to compare." /></label>
          <input type="number" min={1} max={10} value={nPortfolios}
            onChange={(e) => setNPortfolios(Math.max(1, Math.min(10, Number(e.target.value))))}
            className="w-12 bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-xs text-center" />
        </div>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={prioritizeStability}
            onChange={(e) => setPrioritizeStability(e.target.checked)}
            className="accent-ag-accent" />
          <span className="text-ag-text-muted">Prioritize Stability<HintTip text="Weight drawdown and volatility higher in the objective scoring. Favors smoother equity curves over raw returns." /></span>
        </label>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">Diversity:<HintTip text="Controls how different the returned portfolio candidates must be from each other. Off = no filter (default). Low–Max = increasingly strict Jaccard overlap threshold. Higher diversity means candidates share fewer assets." /></label>
          <select value={maxOverlap} onChange={(e) => setMaxOverlap(e.target.value)}
            className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
            <option value="off">Off</option>
            <option value="0.70">Low</option>
            <option value="0.50">Medium</option>
            <option value="0.30">High</option>
            <option value="0.10">Max</option>
          </select>
        </div>
      </div>

      {/* Pre-optimization filters */}
      <div className="bg-ag-card border border-ag-border rounded-lg">
        <button
          onClick={() => setShowFilters(!showFilters)}
          className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium hover:bg-ag-border/30 transition-colors rounded-lg"
        >
          <span>
            Filters
            {(minTrades > 0 || minPF > 0 || minWR > 0 || extraSlippage > 0 || extraCommission > 0 || fundingDaily > 0 || spread > 0 || maxAllowedDdPct > 0 || bybitSizeGate !== "off") && (
              <span className="ml-2 text-xs px-1.5 py-0.5 rounded bg-ag-accent/20 text-ag-accent">
                {[
                  minTrades > 0 && `≥${minTrades} trades`,
                  minPF > 0 && `PF≥${minPF}`,
                  minWR > 0 && `WR≥${minWR}%`,
                  extraSlippage > 0 && `+${extraSlippage}% slip`,
                  extraCommission > 0 && `+${extraCommission}% comm`,
                  fundingDaily > 0 && `${fundingDaily}%/d fund`,
                  spread > 0 && `${spread}% spread`,
                  maxAllowedDdPct > 0 && `DD≤${maxAllowedDdPct}%`,
                  bybitSizeGate === "binary" && "Bybit Gate",
                  bybitSizeGate === "rate" && `Bybit Gate ≤${bybitSizeGateMaxPct}%`,
                ].filter(Boolean).join(" · ")}
              </span>
            )}
          </span>
          <span className="text-ag-text-muted text-xs">{showFilters ? "▲" : "▼"}</span>
        </button>
        {showFilters && (
          <div className="px-4 pb-4 space-y-3 border-t border-ag-border pt-3">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Min Positions<HintTip text="Minimum number of closed positions required per asset. Assets with fewer positions are excluded from optimization. Ensures statistical significance — recommended: 10+ for reliable metrics. 0 = no filter." /></label>
                <input type="number" min={0} value={minTrades}
                  onChange={(e) => setMinTrades(Math.max(0, Number(e.target.value)))}
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Min Profit Factor<HintTip text="Minimum Profit Factor threshold. PF = Gross Profit / Gross Loss. PF < 1.0 = losing strategy. PF 1.3+ recommended. 0 = no filter." /></label>
                <input type="number" min={0} step={0.1} value={minPF}
                  onChange={(e) => setMinPF(Math.max(0, Number(e.target.value)))}
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Min Win Rate %<HintTip text="Minimum win rate percentage. Assets below this are excluded. Note: low win rate with high R:R can still be profitable. 0 = no filter." /></label>
                <input type="number" min={0} max={100} step={1} value={minWR}
                  onChange={(e) => setMinWR(Math.max(0, Math.min(100, Number(e.target.value))))}
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Max DD %<HintTip text="Hard limit on portfolio max drawdown as % of peak equity. Candidates exceeding this are rejected. E.g. 30 = reject portfolios with >30% drawdown. 0 = no limit." /></label>
                <input type="number" min={0} max={100} step={1} value={maxAllowedDdPct}
                  onChange={(e) => setMaxAllowedDdPct(Math.max(0, Math.min(100, Number(e.target.value))))}
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Bybit Size Gate<HintTip text="Check each trade's scaled position size against Bybit instrument limits (max/min qty). Binary = any trade exceeding limits drops the asset. Rate = drop asset if more than X% of trades exceed limits." /></label>
                <select value={bybitSizeGate}
                  onChange={(e) => setBybitSizeGate(e.target.value as "off" | "binary" | "rate")}
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
                  <option value="off">Off</option>
                  <option value="binary">Binary (zero tolerance)</option>
                  <option value="rate">Max Rejection %</option>
                </select>
              </div>
              {bybitSizeGate === "rate" && (
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">Max Reject %<HintTip text="Maximum percentage of trades that can exceed Bybit size limits before the asset is dropped. E.g. 10 = drop if >10% of trades are rejected." /></label>
                  <input type="number" min={0.1} max={100} step={1} value={bybitSizeGateMaxPct}
                    onChange={(e) => setBybitSizeGateMaxPct(Math.max(0.1, Math.min(100, Number(e.target.value))))}
                    className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
                </div>
              )}
            </div>
            {/* Post-hoc trading costs — deducted from P&L before optimization */}
            <div>
              <label className="block text-xs text-ag-text-muted mb-2 font-medium">Trading Costs (Post-Hoc)<HintTip text="These costs are deducted from each trade's P&L BEFORE optimization runs. They simulate realistic trading friction on top of whatever was already applied during backtesting. Each cost is applied as a round-trip % of notional position value (entry + exit), except Funding which is per day held." /></label>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">Extra Slippage %<HintTip text="Simulates additional slippage per side (entry + exit). Crypto guidance: 0.01–0.03% conservative (large-cap limit orders), 0.05% moderate (market orders on liquid alts), 0.10% aggressive (mid-cap alts). If your portfolio survives 0.05–0.10%, it's robust." /></label>
                  <input type="number" min={0} step={0.01} value={extraSlippage}
                    onChange={(e) => setExtraSlippage(Math.max(0, Number(e.target.value)))}
                    className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
                </div>
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">Extra Commission %<HintTip text="Additional commission per side on top of backtest commission. E.g. if backtest used 0.055% but you want to stress-test with 0.075% total, add 0.02% here. Applied to entry + exit notional." /></label>
                  <input type="number" min={0} step={0.01} value={extraCommission}
                    onChange={(e) => setExtraCommission(Math.max(0, Number(e.target.value)))}
                    className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
                </div>
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">Funding %/day<HintTip text="Daily funding rate for perpetual contracts. Applied proportionally to hold duration. Bybit typical: 0.01% per 8h = ~0.03%/day. Charged on notional position size × days held." /></label>
                  <input type="number" min={0} step={0.005} value={fundingDaily}
                    onChange={(e) => setFundingDaily(Math.max(0, Number(e.target.value)))}
                    className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
                </div>
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">Spread %<HintTip text="Bid-ask spread cost. Half-spread is applied on each side (entry + exit). E.g. 0.02% spread means 0.01% deducted at entry and 0.01% at exit. Liquid large-caps: 0.01%. Mid-caps: 0.02-0.05%. Small-caps: 0.05-0.10%." /></label>
                  <input type="number" min={0} step={0.01} value={spread}
                    onChange={(e) => setSpread(Math.max(0, Number(e.target.value)))}
                    className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
                </div>
              </div>
            </div>
            <div className="flex items-center gap-4 pt-1 flex-wrap">
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={clipIS}
                  onChange={(e) => setClipIS(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-ag-text-muted">Clip IS<HintTip text="Force-close trades that enter during the In-Sample period but exit after the OOS cutoff date. Their PnL is recalculated using the boundary candle close price instead of the actual exit. OFF = count full PnL regardless of when the trade closed." /></span>
              </label>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={clipOOS}
                  onChange={(e) => setClipOOS(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-ag-text-muted">Clip OOS<HintTip text="Force-close OOS trades straddling the OOS End Date at the boundary candle close price. OFF = exclude OOS trades that exit after OOS End Date. Either way, the OOS window is bounded by OOS End Date." /></span>
              </label>
              <label className="flex items-center gap-1.5 text-xs">
                <span className="text-ag-text-muted whitespace-nowrap">OOS End<HintTip text="Defines the end of the Out-of-Sample window. OOS runs from the IS cutoff (Date To) to this date. Set it earlier than your data end to reserve an untouched OOS2 section. Auto-detected from batch data end dates." /></span>
                <input type="date" value={oosEndDate}
                  onChange={(e) => { oosDateManualRef.current = true; setOosEndDate(e.target.value); }}
                  className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-[11px] w-[120px]" />
              </label>
            </div>
            <CategoryFilterSelect selected={categoryFilter} onChange={setCategoryFilter} />

            {/* Session Filter (inline config — replaces old session mask dropdown) */}
            <div className="pt-1">
              <SessionFilterConfig value={sessionFilter} onChange={setSessionFilter} />
            </div>
          </div>
        )}
      </div>

      {/* Advanced: Seed Ranking, Start Date */}
      <OptimizerAdvanced
        show={showAdvanced} onToggle={() => setShowAdvanced(!showAdvanced)}
        seedMetrics={seedMetrics} onSeedMetricsChange={setSeedMetrics}
        maxSeeds={maxSeeds} onMaxSeedsChange={setMaxSeeds}
        seedWeights={seedWeights} onSeedWeightsChange={setSeedWeights}
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
        assetQualityMode={assetQualityMode} onAssetQualityModeChange={setAssetQualityMode}
        assetQualityGradeMode={assetQualityGradeMode} onAssetQualityGradeModeChange={setAssetQualityGradeMode}
        assetQualityMinGrade={assetQualityMinGrade} onAssetQualityMinGradeChange={setAssetQualityMinGrade}
        assetQualityMinTrades={assetQualityMinTrades} onAssetQualityMinTradesChange={setAssetQualityMinTrades}
        assetQualityFilter={assetQualityFilter} onAssetQualityFilterChange={setAssetQualityFilter}
        assetQualityHurstMin={assetQualityHurstMin} onAssetQualityHurstMinChange={setAssetQualityHurstMin}
        assetQualityTerPercentile={assetQualityTerPercentile} onAssetQualityTerPercentileChange={setAssetQualityTerPercentile}
        assetQualityHurstMax={assetQualityHurstMax} onAssetQualityHurstMaxChange={setAssetQualityHurstMax}
        assetQualityVrMax={assetQualityVrMax} onAssetQualityVrMaxChange={setAssetQualityVrMax}
        assetQualityMinCr={assetQualityMinCr} onAssetQualityMinCrChange={setAssetQualityMinCr}
        stratQualityMode={stratQualityMode} onStratQualityModeChange={setStratQualityMode}
        stratQualityMinGrade={stratQualityMinGrade} onStratQualityMinGradeChange={setStratQualityMinGrade}
        stratQualityFilter={stratQualityFilter} onStratQualityFilterChange={setStratQualityFilter}
        isWfeIsPct={isWfeIsPct} onIsWfeIsPctChange={setIsWfeIsPct}
        isWfeMinTrades={isWfeMinTrades} onIsWfeMinTradesChange={setIsWfeMinTrades}
        isWfeFilter={isWfeFilter} onIsWfeFilterChange={setIsWfeFilter}
        isWfeMinWfe={isWfeMinWfe} onIsWfeMinWfeChange={setIsWfeMinWfe}
      />

      {/* Trade-Count Constraints */}
      <OptimizerTC
        show={showTC} onToggle={() => setShowTC(!showTC)}
        splitMode={splitMode} enabled={tcEnabled} onEnabledChange={setTcEnabled}
        tc={tc} onTCChange={setTc}
        tcLongs={tcLongs} onTCLongsChange={setTcLongs}
        tcShorts={tcShorts} onTCShortsChange={setTcShorts}
      />

      {/* Benchmark Robustness */}
      <OptimizerBenchmark
        show={showBM} onToggle={() => setShowBM(!showBM)}
        enabled={bmEnabled} onEnabledChange={setBmEnabled}
        config={bmConfig} onConfigChange={setBmConfig}
      />

      {/* Position Sizing Profile */}
      <OptimizerRiskProfile
        show={showRiskProfile} onToggle={() => setShowRiskProfile(!showRiskProfile)}
        config={riskProfile} onChange={setRiskProfile}
        autoEnabled={autoRiskEnabled} onAutoEnabledChange={setAutoRiskEnabled}
        autoAggression={autoRiskAggression} onAutoAggressionChange={setAutoRiskAggression}
        onAutoAnalyze={runAutoRiskAnalyze} autoLoading={autoRiskLoading}
        autoResult={autoRiskResult}
        autoBaseRiskMart={autoBaseRiskMart} onAutoBaseRiskMartChange={setAutoBaseRiskMart}
        autoMinConfidence={autoMinConfidence} onAutoMinConfidenceChange={setAutoMinConfidence}
        autoStrategyHint={autoStrategyHint} onAutoStrategyHintChange={setAutoStrategyHint}
      />

      {/* Controls — mode-specific */}
      {splitMode ? (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">N Longs<HintTip text="Number of long-side assets to include." /></label>
            <input type="range" min={1} max={Math.max(1, Math.floor(totalSymbols / 2))}
              value={nLongs} onChange={(e) => setNLongs(Number(e.target.value))}
              className="w-full accent-ag-green" />
            <span className="text-xs text-ag-green">{nLongs}</span>
          </div>
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">N Shorts<HintTip text="Number of short-side assets to include." /></label>
            <input type="range" min={1} max={Math.max(1, Math.floor(totalSymbols / 2))}
              value={nShorts} onChange={(e) => setNShorts(Number(e.target.value))}
              className="w-full accent-ag-red" />
            <span className="text-xs text-ag-red">{nShorts}</span>
          </div>
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">Within Group<HintTip text="How to rank and select assets within each direction (longs or shorts separately)." /></label>
            <div className="flex items-center gap-1">
              <select value={withinObj} onChange={(e) => setWithinObj(e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
                {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value} title={o.desc}>{o.label}</option>)}
              </select>
              <UserNote noteKey={`obj:${withinObj}`} />
            </div>
            <span className="text-[10px] text-ag-text-muted/70 mt-0.5 block">{ALL_OBJECTIVES.find(o => o.value === withinObj)?.desc}</span>
          </div>
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">Combined<HintTip text="How to score the final portfolio after combining longs and shorts together." /></label>
            <div className="flex items-center gap-1">
              <select value={combinedObj} onChange={(e) => setCombinedObj(e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
                {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value} title={o.desc}>{o.label}</option>)}
              </select>
              <UserNote noteKey={`obj:${combinedObj}`} />
            </div>
            <span className="text-[10px] text-ag-text-muted/70 mt-0.5 block">{ALL_OBJECTIVES.find(o => o.value === combinedObj)?.desc}</span>
          </div>
          <div className="flex items-end">
            <button onClick={runOptimizer} disabled={loading || maskGateFailed}
              title={maskGateFailed ? "Session mask significance gate failed — see preview above" : undefined}
              className="w-full px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
              {loading ? "Running..." : "Optimize L/S"}
            </button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">N Assets<HintTip text="Total number of assets to select from the pool." /></label>
            <input type="range" min={2} max={totalSymbols} value={nAssets}
              onChange={(e) => setNAssets(Number(e.target.value))}
              className="w-full accent-ag-accent" />
            <span className="text-xs text-ag-text">{nAssets} / {totalSymbols}</span>
          </div>
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">Objective<HintTip text="Portfolio optimization goal. Smart Balance blends correlation + stability. Other options focus on a single metric." /></label>
            <div className="flex items-center gap-1">
              <select value={objective} onChange={(e) => setObjective(e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
                {ALL_OBJECTIVES.map((o) => <option key={o.value} value={o.value} title={o.desc}>{o.label}</option>)}
              </select>
              <UserNote noteKey={`obj:${objective}`} />
            </div>
            <span className="text-[10px] text-ag-text-muted/70 mt-0.5 block">{ALL_OBJECTIVES.find(o => o.value === objective)?.desc}</span>
          </div>
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">Correlation<HintTip text="Standard: Pearson on daily PnL (zero-fill). Trade Overlap: only compares days both assets traded." /></label>
            <div className="flex items-center gap-1">
              <select value={corrMethod} onChange={(e) => setCorrMethod(e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
                <option value="standard">Standard</option>
                <option value="trade_overlap">Trade Overlap</option>
              </select>
              <UserNote noteKey={`corr:${corrMethod}`} />
            </div>
          </div>
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">Algorithm<HintTip text="Greedy: correlation-based greedy search (default). HRP: Hierarchical Risk Parity — clusters assets by correlation tree, allocates by inverse variance. PCA: Principal Component clustering — groups assets by independent risk factors." /></label>
            <div className="flex items-center gap-1">
              <select value={divAlgo} onChange={(e) => setDivAlgo(e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
                {DIVERSIFICATION_ALGOS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
              </select>
              <UserNote noteKey={`algo:${divAlgo}`} />
            </div>
          </div>
          <div className="flex items-end">
            <button onClick={runOptimizer} disabled={loading || maskGateFailed}
              title={maskGateFailed ? "Session mask significance gate failed — see preview above" : undefined}
              className="w-full px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
              {loading ? "Running..." : "Optimize"}
            </button>
          </div>
        </div>
      )}

      {/* Correlation method + algorithm — shown in L/S mode since it's not in the grid */}
      {splitMode && (
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted">Correlation:</label>
            <select value={corrMethod} onChange={(e) => setCorrMethod(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
              <option value="standard">Standard</option>
              <option value="trade_overlap">Trade Overlap</option>
            </select>
            <UserNote noteKey={`corr:${corrMethod}`} />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted">Algorithm:</label>
            <select value={divAlgo} onChange={(e) => setDivAlgo(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
              {DIVERSIFICATION_ALGOS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
            <UserNote noteKey={`algo:${divAlgo}`} />
          </div>
        </div>
      )}

      {error && <p className="text-sm text-ag-red">{error}</p>}

      {/* Optimization progress */}
      <OptimizationProgress step={optimStep} totalSteps={optimTotal} label={optimLabel} isRunning={loading} />

      {/* Pool stats */}
      {poolStats && <PoolStatsPanel stats={poolStats} />}

      {/* Results */}
      {candidates.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-medium">Candidates ({candidates.length})</h3>
          {candidates.map((c, i) => (
            <CandidateCard key={`${buildId}-${i}`} index={i} candidate={c}
              saving={saving === i} onSave={() => saveCandidate(i, c)} saved={savedIndices.has(i)}
              dateTo={dateTo} dateFrom={dateFrom || undefined} batchId={batchId}
              clipIS={clipIS} clipOOS={clipOOS} oosEndDate={oosEndDate || undefined}
              autoRiskModes={autoRiskEnabled && autoRiskResult?.per_asset
                ? Object.fromEntries(Object.entries(autoRiskResult.per_asset).map(([s, r]) => [s, r.mode]))
                : undefined}
              riskProfile={
                (riskProfile.mode !== "fixed" || riskProfile.value > 0 || (autoRiskEnabled && autoRiskResult?.per_asset))
                ? {
                    mode: riskProfile.mode, value: riskProfile.value,
                    initialCapital: riskProfile.initialCapital,
                    multiplier: riskProfile.multiplier, maxStreak: riskProfile.maxStreak,
                    compound: riskProfile.martingaleCompound, scope: riskProfile.martingaleScope,
                    countMode: riskProfile.martingaleCountMode, oosReset: riskProfile.martingaleOosReset,
                    startAfter: riskProfile.martingaleStartAfter, resetAfter: riskProfile.antiMartingaleResetAfter,
                    kellyN: riskProfile.kellyN, kellyWarmup: riskProfile.kellyWarmup,
                    ...(autoRiskEnabled && autoRiskResult?.per_asset ? {
                      autoAssetConfigs: JSON.stringify(
                        Object.fromEntries(
                          Object.entries(autoRiskResult.per_asset).map(([sym, cfg]) => [sym, {
                            ...cfg,
                            valueFixed: autoRiskResult.config.valueFixed,
                            valueMartingale: autoRiskResult.config.valueMartingale,
                            initialCapital: autoRiskResult.config.initialCapital,
                            multiplier: autoRiskResult.config.multiplier,
                          }])
                        )
                      ),
                    } : {}),
                  }
                : undefined
              } />
          ))}
        </div>
      )}

      {/* Correlation Heatmap — collapsed by default */}
      {corr && (
        <details className="group">
          <summary className="text-sm font-medium cursor-pointer hover:text-ag-accent flex items-center gap-1">
            <span className="text-ag-text-muted group-open:rotate-90 transition-transform">&#9654;</span>
            Correlation Matrix
            <span className="text-xs text-ag-text-muted font-normal ml-2">
              Mean: {corr.stats.mean?.toFixed(3) ?? "—"}
            </span>
          </summary>
          <div className="bg-ag-card border border-ag-border rounded-lg p-3 mt-2">
            <CorrelationHeatmap symbols={corr.symbols} matrix={corr.matrix} />
            <div className="grid grid-cols-3 gap-2 mt-3 text-xs text-ag-text-muted">
              <p>Mean: {corr.stats.mean?.toFixed(3) ?? "—"}</p>
              <p>Median: {corr.stats.median?.toFixed(3) ?? "—"}</p>
              <p>Range: {corr.stats.min?.toFixed(3) ?? "—"} to {corr.stats.max?.toFixed(3) ?? "—"}</p>
            </div>
          </div>
        </details>
      )}
    </div>
  );
}
