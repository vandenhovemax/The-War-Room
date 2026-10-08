"use client";

import { useState, useEffect } from "react";
import { api, RecommenderResult, RecommenderRecommendation } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import type { SharedSettings, AutoSettings } from "./CrossBuilderControls";
import { ALL_OBJECTIVES } from "./objectiveConstants";
import { ALGO_INFO, SETTING_LABELS, formatSettingValue, friendlySettingValue } from "./recommenderConstants";
import { useTaskManager } from "@/contexts/TaskManager";
import { SEED_CATEGORIES, SEED_METRICS } from "./OptimizerAdvanced";
import UserNote from "./UserNote";

interface Props {
  batchIds: number[];
  portfolioIds: number[];
  shared: SharedSettings;
  auto: AutoSettings;
  onSharedChange: (patch: Partial<SharedSettings>) => void;
  onAutoChange: (patch: Partial<AutoSettings>) => void;
  dateFrom?: string;
  dateTo?: string;
  clipIS: boolean;
}

const CATEGORY_LABELS: Record<string, string> = {
  algorithm: "Algorithm",
  objective: "Objective",
  seed: "Seed Scoring",
  filter: "Filters",
  overfitting_guard: "Overfitting Guard",
};

const CONFIDENCE_COLORS: Record<string, string> = {
  high: "text-green-400",
  medium: "text-orange-400",
  low: "text-ag-text-muted",
};

function confidenceLevel(c: number): string {
  if (c >= 0.8) return "high";
  if (c >= 0.6) return "medium";
  return "low";
}

function confidenceLabel(c: number): string {
  if (c >= 0.8) return "High";
  if (c >= 0.6) return "Medium";
  return "Low";
}

const ALL_ALGOS = ["greedy", "hrp", "pca", "quality_first", "tournament", "marginal_sharpe", "ensemble", "walk_forward"];

// Dimension labels for depth breakdown
const GUARD_LABELS = ["light", "moderate", "aggressive", "max"];
const SEED_LABELS = ["[sortino]", "[sortino, stability]", "[sortino, sharpe, stability]", "[sortino, sharpe, stability, calmar]"];

// The actual value pools (mirrors backend _PF_POOL, _TRADES_POOL, _WR_POOL)
const PF_POOL = [1.0, 1.2, 1.3, 1.4, 1.5, 1.7, 2.0];
const TRADES_POOL = [3, 4, 5, 6, 8, 10];
const WR_POOL = [20, 30, 35, 40, 45, 50, 55];

function depthToDim(d: number) {
  return {
    guards: d <= 35 ? 1 : d <= 65 ? 2 : d <= 85 ? 3 : 4,
    filterPf: d <= 45 ? 1 : d <= 55 ? 2 : d <= 65 ? 3 : d <= 75 ? 4 : d <= 85 ? 5 : d <= 95 ? 6 : 7,
    filterTrades: d <= 45 ? 1 : d <= 55 ? 2 : d <= 65 ? 3 : d <= 80 ? 4 : d <= 90 ? 5 : 6,
    filterWr: d <= 45 ? 1 : d <= 55 ? 2 : d <= 65 ? 3 : d <= 75 ? 4 : d <= 85 ? 5 : d <= 95 ? 6 : 7,
    seedCombos: d <= 20 ? 1 : d <= 50 ? 2 : d <= 75 ? 3 : 4,
    folds: d <= 25 ? 1 : d <= 65 ? 2 : 3,
  };
}

export default function SettingsRecommender({
  batchIds, portfolioIds, shared, auto,
  onSharedChange, onAutoChange, dateFrom, dateTo, clipIS,
}: Props) {
  const { startOptimization, latestOptimizationTask } = useTaskManager();
  const RECOMMENDER_LABEL = "Settings Recommender";
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RecommenderResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [mode, setMode] = useState<"quick" | "thorough">("quick");
  const [thoroughProgress, setThoroughProgress] = useState("");
  const [progressPct, setProgressPct] = useState(0);

  // Algorithm selection (all 8)
  const [selectedAlgos, setSelectedAlgos] = useState<string[]>([...ALL_ALGOS]);

  // Objective selection — default to the 5 most commonly used
  const DEFAULT_OBJECTIVES = ["stability", "hybrid", "max_sortino", "max_sharpe", "max_calmar"];
  const [selectedObjectives, setSelectedObjectives] = useState<string[]>([...DEFAULT_OBJECTIVES]);

  // Input overrides
  const [localSplitMode, setLocalSplitMode] = useState(auto.splitMode);
  const [localNAssets, setLocalNAssets] = useState(auto.nAssets);
  const [localNLongs, setLocalNLongs] = useState(auto.nLongs);
  const [localNShorts, setLocalNShorts] = useState(auto.nShorts);

  // Expanded preset detail view (index or null)
  const [expandedPreset, setExpandedPreset] = useState<number | null>(null);

  // Expanded recommendation detail (key like "div_algo" or null)
  const [expandedRec, setExpandedRec] = useState<string | null>(null);

  // Depth controls for thorough mode
  const [depthPct, setDepthPct] = useState(50);
  const [extremeValues, setExtremeValues] = useState(false);
  const [sweepSeedPrior, setSweepSeedPrior] = useState(false);

  // Per-dimension overrides (null = auto from master depth)
  const [dimGuards, setDimGuards] = useState<number | null>(null);
  const [dimFilterPf, setDimFilterPf] = useState<number | null>(null);
  const [dimFilterTrades, setDimFilterTrades] = useState<number | null>(null);
  const [dimFilterWr, setDimFilterWr] = useState<number | null>(null);
  const [dimSeedCombos, setDimSeedCombos] = useState<number | null>(null);
  const [dimFolds, setDimFolds] = useState<number | null>(null);

  // Custom seed metric combos — user-built list of combos to sweep
  const [useCustomSeeds, setUseCustomSeeds] = useState(false);
  const [customSeedCombos, setCustomSeedCombos] = useState<string[][]>([["sortino"]]);
  const [pendingComboMetrics, setPendingComboMetrics] = useState<string[]>([]);

  const totalSources = batchIds.length + portfolioIds.length;

  // Master slider resets all per-dimension overrides
  const handleMasterChange = (d: number) => {
    setDepthPct(d);
    setDimGuards(null);
    setDimFilterPf(null);
    setDimFilterTrades(null);
    setDimFilterWr(null);
    setDimSeedCombos(null);
    setDimFolds(null);
  };

  // Effective dimension counts
  const autoDims = depthToDim(depthPct);
  const eff = {
    guards: dimGuards ?? autoDims.guards,
    filterPf: dimFilterPf ?? autoDims.filterPf,
    filterTrades: dimFilterTrades ?? autoDims.filterTrades,
    filterWr: dimFilterWr ?? autoDims.filterWr,
    seedCombos: dimSeedCombos ?? autoDims.seedCombos,
    folds: dimFolds ?? autoDims.folds,
  };

  // Combo estimate
  const nSpw = !sweepSeedPrior ? 1 : depthPct < 60 ? 1 : depthPct < 85 ? 2 : 3;
  const nExtraGuards = extremeValues ? 2 : 0;
  const nFilterCombos = eff.filterPf * eff.filterTrades * eff.filterWr;
  const effSeedCount = useCustomSeeds ? Math.max(1, customSeedCombos.length) : eff.seedCombos;
  const combos = selectedAlgos.length * selectedObjectives.length * nSpw * (eff.guards + nExtraGuards) * nFilterCombos * effSeedCount;
  const evals = combos * eff.folds;
  const estMinutes = Math.round(evals * 0.15);  // ~9s per eval based on measured runs

  const toggleAlgo = (algo: string) => {
    setSelectedAlgos(prev => {
      if (prev.includes(algo)) {
        if (prev.length <= 1) return prev; // at least 1
        return prev.filter(a => a !== algo);
      }
      return [...prev, algo];
    });
  };

  const toggleObjective = (obj: string) => {
    setSelectedObjectives(prev => {
      if (prev.includes(obj)) {
        if (prev.length <= 1) return prev; // at least 1
        return prev.filter(o => o !== obj);
      }
      return [...prev, obj];
    });
  };

  const buildParams = () => ({
    batch_ids: batchIds,
    portfolio_ids: portfolioIds,
    split_mode: localSplitMode,
    n_assets: localNAssets,
    n_longs: localNLongs,
    n_shorts: localNShorts,
    algos: selectedAlgos,
    objectives: selectedObjectives,
    seed_metrics: shared.seedMetrics,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    clip_is: clipIS,
    corr_method: shared.corrMethod,
    min_trades: shared.minTrades,
    min_pf: shared.minPf,
    min_wr: shared.minWr,
    max_asset_pf: shared.maxAssetPf,
    cap_asset_pf: shared.capAssetPf,
    extra_slippage_pct: shared.extraSlippage,
    extra_commission_pct: shared.extraCommission,
    funding_daily_pct: shared.fundingDaily,
    spread_pct: shared.spread,
    // Snapshot ALL SharedSettings so saved presets can restore everything
    current_shared_settings: {
      // Recency
      recency_enabled: shared.recencyEnabled,
      recency_mode: shared.recencyMode,
      recency_batch_size: shared.recencyBatchSize,
      recency_boost_factor: shared.recencyBoostFactor,
      // Pre-filter
      prefilter_enabled: shared.prefilterEnabled,
      prefilter_mode: shared.prefilterMode,
      prefilter_hurst_min: shared.prefilterHurstMin,
      prefilter_ter_percentile: shared.prefilterTerPercentile,
      prefilter_hurst_max: shared.prefilterHurstMax,
      prefilter_vr_max: shared.prefilterVrMax,
      prefilter_min_crossing_rate: shared.prefilterMinCrossingRate,
      // AC post-filter
      ac_postfilter: shared.acPostfilter,
      ac_postfilter_type: shared.acPostfilterType,
      ac_lag: shared.acLag,
      // Asset Quality
      asset_quality_mode: shared.assetQualityMode,
      asset_quality_grade_mode: shared.assetQualityGradeMode,
      asset_quality_min_grade: shared.assetQualityMinGrade,
      asset_quality_min_trades: shared.assetQualityMinTrades,
      asset_quality_filter: shared.assetQualityFilter,
      asset_quality_hurst_min: shared.assetQualityHurstMin,
      asset_quality_ter_percentile: shared.assetQualityTerPercentile,
      asset_quality_hurst_max: shared.assetQualityHurstMax,
      asset_quality_vr_max: shared.assetQualityVrMax,
      asset_quality_min_cr: shared.assetQualityMinCr,
      // Strategy Quality
      strat_quality_mode: shared.stratQualityMode,
      strat_quality_min_grade: shared.stratQualityMinGrade,
      strat_quality_filter: shared.stratQualityFilter,
      // IS-WFE
      is_wfe_is_pct: shared.isWfeIsPct,
      is_wfe_min_trades: shared.isWfeMinTrades,
      is_wfe_filter: shared.isWfeFilter,
      is_wfe_min_wfe: shared.isWfeMinWfe,
      // Seed-blind
      seed_blind_selection: shared.seedBlindSelection,
      // Corr jitter
      corr_jitter_enabled: shared.corrJitterEnabled,
      corr_jitter_temperature: shared.corrJitterTemperature,
      // Refinement
      refinement_enabled: shared.refinementEnabled,
      refinement_mode: shared.refinementMode,
      refinement_metric: shared.refinementMetric,
      // Misc
      max_overlap: shared.maxOverlap,
      expand_greedy_pool: shared.expandGreedyPool,
      streamlit_logic: shared.streamlitLogic,
      max_seeds: shared.maxSeeds,
      seed_weights: shared.seedWeights,
      // Avg Range
      avg_range_bull_threshold: shared.avgRangeBullThreshold,
      avg_range_bear_threshold: shared.avgRangeBearThreshold,
      avg_range_all_threshold: shared.avgRangeAllThreshold,
      avg_range_filter_mode: shared.avgRangeFilterMode,
      // Portfolio gates & per-asset PF cap
      max_asset_pf: shared.maxAssetPf,
      cap_asset_pf: shared.capAssetPf,
      portfolio_min_pf: shared.portfolioMinPf,
      portfolio_min_wr: shared.portfolioMinWr,
    },
  });

  const runQuick = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.crossStrategyRecommendQuick(buildParams());
      setResult(res);
      setExpanded(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Analysis failed");
    } finally {
      setLoading(false);
    }
  };

  const runThorough = () => {
    setLoading(true);
    setError(null);
    setResult(null);
    setProgressPct(0);
    setThoroughProgress("Starting cross-validation...");

    const fetchFn = async (
      taskProgress?: (pct: number, label: string) => void,
      onBackendTaskId?: (backendId: string) => void,
    ) => {
      const res = await api.crossStrategyRecommendThorough(
        {
          ...buildParams(),
          depth_pct: depthPct,
          extreme_values: extremeValues,
          sweep_seed_prior: sweepSeedPrior,
          dim_guards: dimGuards,
          dim_filter_pf: dimFilterPf,
          dim_filter_trades: dimFilterTrades,
          dim_filter_wr: dimFilterWr,
          dim_seed_combos: useCustomSeeds ? null : dimSeedCombos,
          dim_folds: dimFolds,
          seed_combos: useCustomSeeds && customSeedCombos.length > 0 ? customSeedCombos : undefined,
        },
        (step, total, label) => {
          setThoroughProgress(label);
          const pctMatch = label.match(/\((\d+)%\)/);
          let pct = 0;
          if (pctMatch) {
            pct = parseInt(pctMatch[1], 10);
          } else if (total > 0) {
            pct = Math.round((step / total) * 100);
          }
          setProgressPct(pct);
          if (taskProgress) taskProgress(pct, label);
        },
        onBackendTaskId,
      );
      return { candidates: [], poolStats: null, corr: null, extra: { recommenderResult: res } };
    };

    startOptimization(fetchFn, RECOMMENDER_LABEL);
  };

  // Sync recommender results from TaskManager
  useEffect(() => {
    const task = latestOptimizationTask(RECOMMENDER_LABEL);
    if (!task) return;
    if (task.status === "completed" && task.result?.extra?.recommenderResult) {
      const res = task.result.extra.recommenderResult as RecommenderResult;
      setResult(res);
      setExpanded(true);
      setLoading(false);
      setThoroughProgress("");
      setProgressPct(0);
    } else if (task.status === "error") {
      setError(task.error ?? "Analysis failed");
      setLoading(false);
      setThoroughProgress("");
      setProgressPct(0);
    } else if (task.status === "cancelled") {
      setLoading(false);
      setThoroughProgress("");
      setProgressPct(0);
    }
  }, [latestOptimizationTask]);

  const handleRun = () => {
    if (mode === "quick") runQuick();
    else runThorough();
  };

  const applyRecommendation = (rec: RecommenderRecommendation) => {
    const v = rec.value;
    switch (rec.setting) {
      case "div_algo": onSharedChange({ divAlgo: String(v) }); break;
      case "objective": onAutoChange({ combinedObj: String(v), withinObj: "uncorrelated" }); break;
      case "seed_prior_weight": onSharedChange({ seedPriorWeight: Number(v) }); break;
      case "seed_metrics": if (Array.isArray(v)) onSharedChange({ seedMetrics: v as string[] }); break;
      case "min_pf": onSharedChange({ minPf: Number(v) }); break;
      case "min_trades": onSharedChange({ minTrades: Number(v) }); break;
      case "overfit_guard_enabled": onSharedChange({ overfitGuardEnabled: Boolean(v) }); break;
      case "of_log_enabled": onSharedChange({ ofLogEnabled: Boolean(v) }); break;
      case "of_log_cap_pf": onSharedChange({ ofLogCapPf: Number(v) }); break;
      case "of_z_score_enabled": onSharedChange({ ofZScoreEnabled: Boolean(v) }); break;
      case "of_z_threshold": onSharedChange({ ofZThreshold: Number(v) }); break;
      case "of_z_penalty_strength": onSharedChange({ ofZPenaltyStrength: Number(v) }); break;
      case "of_bayes_enabled": onSharedChange({ ofBayesEnabled: Boolean(v) }); break;
      case "of_bayes_k": onSharedChange({ ofBayesK: Number(v) }); break;
      case "of_sigmoid_enabled": onSharedChange({ ofSigmoidEnabled: Boolean(v) }); break;
      case "of_normal_range_enabled": onSharedChange({ ofNormalRangeEnabled: Boolean(v) }); break;
      case "of_nr_pct_low": onSharedChange({ ofNrPctLow: Number(v) }); break;
      case "of_nr_pct_high": onSharedChange({ ofNrPctHigh: Number(v) }); break;
      case "of_wf_rerank_enabled": onSharedChange({ ofWfRerankEnabled: Boolean(v) }); break;
      case "of_wf_split_pct": onSharedChange({ ofWfSplitPct: Number(v) }); break;
      case "of_wf_validation_metric": onSharedChange({ ofWfValidationMetric: String(v) }); break;
      case "refinement_enabled": onSharedChange({ refinementEnabled: Boolean(v) }); break;
      case "refinement_mode": onSharedChange({ refinementMode: String(v) }); break;
      case "corr_jitter_enabled": onSharedChange({ corrJitterEnabled: Boolean(v) }); break;
      case "corr_jitter_temperature": onSharedChange({ corrJitterTemperature: Number(v) }); break;
    }
  };

  const applyAll = () => {
    if (!result) return;
    for (const rec of result.recommendations) applyRecommendation(rec);
  };

  const applyPreset = (preset: Record<string, unknown>) => {
    const fs = preset as Record<string, unknown>;
    if (fs.div_algo) onSharedChange({ divAlgo: String(fs.div_algo) });
    if (fs.objective) onAutoChange({ combinedObj: String(fs.objective) });
    if (fs.seed_prior_weight !== undefined) onSharedChange({ seedPriorWeight: Number(fs.seed_prior_weight) });
    if (fs.min_pf !== undefined) onSharedChange({ minPf: Number(fs.min_pf) });
    if (fs.min_trades !== undefined) onSharedChange({ minTrades: Number(fs.min_trades) });
    if (fs.min_wr !== undefined) onSharedChange({ minWr: Number(fs.min_wr) });
    if (fs.max_asset_pf !== undefined) onSharedChange({ maxAssetPf: Number(fs.max_asset_pf) });
    if (fs.cap_asset_pf !== undefined) onSharedChange({ capAssetPf: Boolean(fs.cap_asset_pf) });
    if (fs.portfolio_min_pf !== undefined) onSharedChange({ portfolioMinPf: Number(fs.portfolio_min_pf) });
    if (fs.portfolio_min_wr !== undefined) onSharedChange({ portfolioMinWr: Number(fs.portfolio_min_wr) });
    if (fs.overfit_guard_enabled !== undefined) onSharedChange({ overfitGuardEnabled: Boolean(fs.overfit_guard_enabled) });
    if (fs.of_log_enabled !== undefined) onSharedChange({ ofLogEnabled: Boolean(fs.of_log_enabled) });
    if (fs.of_z_score_enabled !== undefined) onSharedChange({ ofZScoreEnabled: Boolean(fs.of_z_score_enabled) });
    if (fs.of_z_threshold !== undefined) onSharedChange({ ofZThreshold: Number(fs.of_z_threshold) });
    if (fs.of_z_penalty_strength !== undefined) onSharedChange({ ofZPenaltyStrength: Number(fs.of_z_penalty_strength) });
    if (fs.of_bayes_enabled !== undefined) onSharedChange({ ofBayesEnabled: Boolean(fs.of_bayes_enabled) });
    if (fs.of_bayes_k !== undefined) onSharedChange({ ofBayesK: Number(fs.of_bayes_k) });
    if (fs.of_sigmoid_enabled !== undefined) onSharedChange({ ofSigmoidEnabled: Boolean(fs.of_sigmoid_enabled) });
    if (fs.of_normal_range_enabled !== undefined) onSharedChange({ ofNormalRangeEnabled: Boolean(fs.of_normal_range_enabled) });
    if (fs.of_nr_pct_low !== undefined) onSharedChange({ ofNrPctLow: Number(fs.of_nr_pct_low) });
    if (fs.of_nr_pct_high !== undefined) onSharedChange({ ofNrPctHigh: Number(fs.of_nr_pct_high) });
    if (fs.of_nr_penalty_scale !== undefined) onSharedChange({ ofNrPenaltyScale: Number(fs.of_nr_penalty_scale) });
    if (fs.of_nr_bonus !== undefined) onSharedChange({ ofNrBonus: Number(fs.of_nr_bonus) });
    if (fs.of_wf_rerank_enabled !== undefined) onSharedChange({ ofWfRerankEnabled: Boolean(fs.of_wf_rerank_enabled) });
    if (fs.of_wf_split_pct !== undefined) onSharedChange({ ofWfSplitPct: Number(fs.of_wf_split_pct) });
    if (fs.of_wf_validation_metric !== undefined) onSharedChange({ ofWfValidationMetric: String(fs.of_wf_validation_metric) });
    if (fs.seed_metrics && Array.isArray(fs.seed_metrics)) onSharedChange({ seedMetrics: fs.seed_metrics as string[] });
    // Extract nested of_log_caps dict into individual flat fields
    if (fs.of_log_caps && typeof fs.of_log_caps === "object") {
      const caps = fs.of_log_caps as Record<string, number>;
      if (caps.pf !== undefined) onSharedChange({ ofLogCapPf: Number(caps.pf) });
      if (caps.sortino !== undefined) onSharedChange({ ofLogCapSortino: Number(caps.sortino) });
      if (caps.payoff !== undefined) onSharedChange({ ofLogCapPayoff: Number(caps.payoff) });
      if (caps.wr !== undefined) onSharedChange({ ofLogCapWr: Number(caps.wr) });
      if (caps.sharpe !== undefined) onSharedChange({ ofLogCapSharpe: Number(caps.sharpe) });
      if (caps.calmar !== undefined) onSharedChange({ ofLogCapCalmar: Number(caps.calmar) });
    }
    // Extract nested of_sig_caps dict into individual flat fields
    if (fs.of_sig_caps && typeof fs.of_sig_caps === "object") {
      const caps = fs.of_sig_caps as Record<string, number>;
      if (caps.pf !== undefined) onSharedChange({ ofSigCapPf: Number(caps.pf) });
      if (caps.sortino !== undefined) onSharedChange({ ofSigCapSortino: Number(caps.sortino) });
      if (caps.payoff !== undefined) onSharedChange({ ofSigCapPayoff: Number(caps.payoff) });
      if (caps.wr !== undefined) onSharedChange({ ofSigCapWr: Number(caps.wr) });
      if (caps.sharpe !== undefined) onSharedChange({ ofSigCapSharpe: Number(caps.sharpe) });
      if (caps.calmar !== undefined) onSharedChange({ ofSigCapCalmar: Number(caps.calmar) });
    }
    // ── Advanced SharedSettings (restored from current_shared_settings snapshot) ──
    // Recency
    if (fs.recency_enabled !== undefined) onSharedChange({ recencyEnabled: Boolean(fs.recency_enabled) });
    if (fs.recency_mode !== undefined) onSharedChange({ recencyMode: String(fs.recency_mode) });
    if (fs.recency_batch_size !== undefined) onSharedChange({ recencyBatchSize: Number(fs.recency_batch_size) });
    if (fs.recency_boost_factor !== undefined) onSharedChange({ recencyBoostFactor: Number(fs.recency_boost_factor) });
    // Pre-filter
    if (fs.prefilter_enabled !== undefined) onSharedChange({ prefilterEnabled: Boolean(fs.prefilter_enabled) });
    if (fs.prefilter_mode !== undefined) onSharedChange({ prefilterMode: String(fs.prefilter_mode) });
    if (fs.prefilter_hurst_min !== undefined) onSharedChange({ prefilterHurstMin: Number(fs.prefilter_hurst_min) });
    if (fs.prefilter_ter_percentile !== undefined) onSharedChange({ prefilterTerPercentile: Number(fs.prefilter_ter_percentile) });
    if (fs.prefilter_hurst_max !== undefined) onSharedChange({ prefilterHurstMax: Number(fs.prefilter_hurst_max) });
    if (fs.prefilter_vr_max !== undefined) onSharedChange({ prefilterVrMax: Number(fs.prefilter_vr_max) });
    if (fs.prefilter_min_crossing_rate !== undefined) onSharedChange({ prefilterMinCrossingRate: Number(fs.prefilter_min_crossing_rate) });
    // AC post-filter
    if (fs.ac_postfilter !== undefined) onSharedChange({ acPostfilter: Boolean(fs.ac_postfilter) });
    if (fs.ac_postfilter_type !== undefined) onSharedChange({ acPostfilterType: String(fs.ac_postfilter_type) });
    if (fs.ac_lag !== undefined) onSharedChange({ acLag: Number(fs.ac_lag) });
    // Asset Quality
    if (fs.asset_quality_mode !== undefined) onSharedChange({ assetQualityMode: String(fs.asset_quality_mode) });
    if (fs.asset_quality_grade_mode !== undefined) onSharedChange({ assetQualityGradeMode: String(fs.asset_quality_grade_mode) });
    if (fs.asset_quality_min_grade !== undefined) onSharedChange({ assetQualityMinGrade: String(fs.asset_quality_min_grade) });
    if (fs.asset_quality_min_trades !== undefined) onSharedChange({ assetQualityMinTrades: Number(fs.asset_quality_min_trades) });
    if (fs.asset_quality_filter !== undefined) onSharedChange({ assetQualityFilter: Boolean(fs.asset_quality_filter) });
    if (fs.asset_quality_hurst_min !== undefined) onSharedChange({ assetQualityHurstMin: Number(fs.asset_quality_hurst_min) });
    if (fs.asset_quality_ter_percentile !== undefined) onSharedChange({ assetQualityTerPercentile: Number(fs.asset_quality_ter_percentile) });
    if (fs.asset_quality_hurst_max !== undefined) onSharedChange({ assetQualityHurstMax: Number(fs.asset_quality_hurst_max) });
    if (fs.asset_quality_vr_max !== undefined) onSharedChange({ assetQualityVrMax: Number(fs.asset_quality_vr_max) });
    if (fs.asset_quality_min_cr !== undefined) onSharedChange({ assetQualityMinCr: Number(fs.asset_quality_min_cr) });
    // Strategy Quality
    if (fs.strat_quality_mode !== undefined) onSharedChange({ stratQualityMode: String(fs.strat_quality_mode) });
    if (fs.strat_quality_min_grade !== undefined) onSharedChange({ stratQualityMinGrade: String(fs.strat_quality_min_grade) });
    if (fs.strat_quality_filter !== undefined) onSharedChange({ stratQualityFilter: Boolean(fs.strat_quality_filter) });
    // IS-WFE
    if (fs.is_wfe_is_pct !== undefined) onSharedChange({ isWfeIsPct: Number(fs.is_wfe_is_pct) });
    if (fs.is_wfe_min_trades !== undefined) onSharedChange({ isWfeMinTrades: Number(fs.is_wfe_min_trades) });
    if (fs.is_wfe_filter !== undefined) onSharedChange({ isWfeFilter: Boolean(fs.is_wfe_filter) });
    if (fs.is_wfe_min_wfe !== undefined) onSharedChange({ isWfeMinWfe: Number(fs.is_wfe_min_wfe) });
    // Seed-blind
    if (fs.seed_blind_selection !== undefined) onSharedChange({ seedBlindSelection: Boolean(fs.seed_blind_selection) });
    // Corr jitter
    if (fs.corr_jitter_enabled !== undefined) onSharedChange({ corrJitterEnabled: Boolean(fs.corr_jitter_enabled) });
    if (fs.corr_jitter_temperature !== undefined) onSharedChange({ corrJitterTemperature: Number(fs.corr_jitter_temperature) });
    // Refinement
    if (fs.refinement_enabled !== undefined) onSharedChange({ refinementEnabled: Boolean(fs.refinement_enabled) });
    if (fs.refinement_mode !== undefined) onSharedChange({ refinementMode: String(fs.refinement_mode) });
    if (fs.refinement_metric !== undefined) onSharedChange({ refinementMetric: String(fs.refinement_metric) });
    // Misc
    if (fs.max_overlap !== undefined) onSharedChange({ maxOverlap: String(fs.max_overlap) });
    if (fs.expand_greedy_pool !== undefined) onSharedChange({ expandGreedyPool: Boolean(fs.expand_greedy_pool) });
    if (fs.streamlit_logic !== undefined) onSharedChange({ streamlitLogic: Boolean(fs.streamlit_logic) });
    if (fs.max_seeds !== undefined) onSharedChange({ maxSeeds: Number(fs.max_seeds) });
    if (fs.seed_weights && typeof fs.seed_weights === "object") onSharedChange({ seedWeights: fs.seed_weights as Record<string, number> });
    // Avg Range
    if (fs.avg_range_bull_threshold !== undefined) onSharedChange({ avgRangeBullThreshold: Number(fs.avg_range_bull_threshold) });
    if (fs.avg_range_bear_threshold !== undefined) onSharedChange({ avgRangeBearThreshold: Number(fs.avg_range_bear_threshold) });
    if (fs.avg_range_all_threshold !== undefined) onSharedChange({ avgRangeAllThreshold: Number(fs.avg_range_all_threshold) });
    if (fs.avg_range_filter_mode !== undefined) onSharedChange({ avgRangeFilterMode: Boolean(fs.avg_range_filter_mode) });

    // Run-context fields: asset counts, pool mode, costs, dates
    if (fs.split_mode !== undefined) {
      const sm = Boolean(fs.split_mode);
      setLocalSplitMode(sm);
      onAutoChange({ splitMode: sm });
    }
    if (fs.n_assets !== undefined) {
      const n = Number(fs.n_assets);
      setLocalNAssets(n);
      onAutoChange({ nAssets: n });
    }
    if (fs.n_longs !== undefined) {
      const n = Number(fs.n_longs);
      setLocalNLongs(n);
      onAutoChange({ nLongs: n });
    }
    if (fs.n_shorts !== undefined) {
      const n = Number(fs.n_shorts);
      setLocalNShorts(n);
      onAutoChange({ nShorts: n });
    }
    if (fs.extra_slippage_pct !== undefined) onSharedChange({ extraSlippage: Number(fs.extra_slippage_pct) });
    if (fs.extra_commission_pct !== undefined) onSharedChange({ extraCommission: Number(fs.extra_commission_pct) });
    if (fs.funding_daily_pct !== undefined) onSharedChange({ fundingDaily: Number(fs.funding_daily_pct) });
    if (fs.spread_pct !== undefined) onSharedChange({ spread: Number(fs.spread_pct) });
    if (fs.date_from) onSharedChange({ dateFrom: String(fs.date_from) });
    if (fs.corr_method) onSharedChange({ corrMethod: String(fs.corr_method) });
  };

  const grouped = result?.recommendations.reduce<Record<string, RecommenderRecommendation[]>>((acc, r) => {
    const cat = r.category;
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(r);
    return acc;
  }, {});

  return (
    <div className="border border-ag-border rounded-lg overflow-hidden">
      {/* Header bar */}
      <div
        className="flex items-center justify-between px-3 py-2 bg-ag-dark/50 cursor-pointer hover:bg-ag-dark/70 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-ag-text">
            Settings Recommender
          </span>
          <HintTip text="Analyses your IS data (without touching OOS) to recommend optimal optimizer settings. Quick mode uses heuristic rules. Thorough mode runs walk-forward cross-validation over setting combinations to find what works best." />
          {result && (
            <span className="text-[10px] text-ag-text-muted">
              ({result.profile.n_assets} assets, {result.elapsed_seconds}s)
            </span>
          )}
        </div>
        <span className="text-xs text-ag-text-muted">
          {expanded ? "▾" : "▸"}
        </span>
      </div>

      {expanded && (
        <div className="p-3 space-y-3 border-t border-ag-border">

          {/* ── Inherited settings reminder ── */}
          <div className="bg-blue-900/15 border border-blue-500/20 rounded-lg px-3 py-2 text-[11px] text-blue-300/80 leading-relaxed">
            <span className="font-medium text-blue-300">Inherited from main optimizer:</span>{" "}
            Cost stress (slippage, commission, funding, spread), quality filters (min trades / PF / WR),
            correlation method, seed metrics, and date range are picked up automatically.
            Only configure the settings below.
          </div>

          {/* ── Input Settings ── */}
          <div className="bg-ag-dark/30 rounded-lg p-3 border border-ag-border/50 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-medium text-ag-text-muted uppercase tracking-wider">Input</span>
              <HintTip text="Set these HERE — they are NOT inherited from the main optimizer. Choose how many assets to select and whether to split longs/shorts." />
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setLocalSplitMode(false)}
                  title="Combined mode — select N assets from the entire pool regardless of direction."
                  className={`px-2 py-0.5 text-[11px] rounded font-medium transition-colors ${!localSplitMode ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted bg-ag-dark border border-ag-border"}`}
                >
                  Combined
                </button>
                <button
                  onClick={() => setLocalSplitMode(true)}
                  title="L/S Split — select N longs and N shorts separately, then combine."
                  className={`px-2 py-0.5 text-[11px] rounded font-medium transition-colors ${localSplitMode ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted bg-ag-dark border border-ag-border"}`}
                >
                  L/S Split
                </button>
              </div>
              {localSplitMode ? (
                <>
                  <label className="flex items-center gap-1 text-[11px] text-ag-text" title="Number of long assets to select.">
                    Longs
                    <input type="number" min={1} max={50} value={localNLongs} onChange={(e) => setLocalNLongs(Number(e.target.value))}
                      className="w-12 px-1 py-0.5 text-[11px] bg-ag-dark border border-ag-border rounded text-center text-ag-text" />
                  </label>
                  <label className="flex items-center gap-1 text-[11px] text-ag-text" title="Number of short assets to select.">
                    Shorts
                    <input type="number" min={1} max={50} value={localNShorts} onChange={(e) => setLocalNShorts(Number(e.target.value))}
                      className="w-12 px-1 py-0.5 text-[11px] bg-ag-dark border border-ag-border rounded text-center text-ag-text" />
                  </label>
                </>
              ) : (
                <label className="flex items-center gap-1 text-[11px] text-ag-text" title="Total number of assets to select.">
                  Assets
                  <input type="number" min={2} max={100} value={localNAssets} onChange={(e) => setLocalNAssets(Number(e.target.value))}
                    className="w-14 px-1 py-0.5 text-[11px] bg-ag-dark border border-ag-border rounded text-center text-ag-text" />
                </label>
              )}
            </div>
          </div>

          {/* ── Algorithms ── */}
          <div className="bg-ag-dark/30 rounded-lg p-3 border border-ag-border/50 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-medium text-ag-text-muted uppercase tracking-wider">Algorithms</span>
              <HintTip text="Select which diversification algorithms to test HERE. The recommender will compare these to find which works best for your pool." />
            </div>
            <div className="flex flex-wrap gap-1.5">
              {ALL_ALGOS.map((algo) => {
                const info = ALGO_INFO[algo];
                const checked = selectedAlgos.includes(algo);
                return (
                  <span key={algo} className="inline-flex items-center">
                    <label
                      className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] cursor-pointer transition-colors border ${checked ? "bg-ag-accent/15 text-ag-accent border-ag-accent/30" : "text-ag-text-muted bg-ag-dark border-ag-border"}`}
                      title={info.tip}
                    >
                      <input type="checkbox" checked={checked}
                        onChange={() => toggleAlgo(algo)}
                        className="rounded border-ag-border accent-ag-accent w-3 h-3" />
                      {info.display}
                    </label>
                    <UserNote noteKey={`algo:${algo}`} />
                  </span>
                );
              })}
            </div>
          </div>

          {/* ── Objectives ── */}
          <div className="bg-ag-dark/30 rounded-lg p-3 border border-ag-border/50 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium text-ag-text-muted uppercase tracking-wider">Objectives</span>
                <HintTip text="Select which scoring functions to test HERE. In Quick mode, the recommender picks the best one heuristically. In Thorough mode, all selected objectives are swept via cross-validation. More objectives = more combinations = longer run time." />
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setSelectedObjectives(ALL_OBJECTIVES.map(o => o.value))}
                  className="px-1.5 py-0.5 text-[10px] text-ag-text-muted hover:text-ag-accent transition-colors"
                  title="Select all objectives"
                >
                  All
                </button>
                <span className="text-[10px] text-ag-text-muted">|</span>
                <button
                  onClick={() => setSelectedObjectives([...DEFAULT_OBJECTIVES])}
                  className="px-1.5 py-0.5 text-[10px] text-ag-text-muted hover:text-ag-accent transition-colors"
                  title="Reset to the 5 default objectives (stability, hybrid, sortino, sharpe, calmar)"
                >
                  Default
                </button>
                <span className="text-[10px] text-ag-text-muted">|</span>
                <button
                  onClick={() => setSelectedObjectives(["stability"])}
                  className="px-1.5 py-0.5 text-[10px] text-ag-text-muted hover:text-ag-accent transition-colors"
                  title="Deselect all except stability"
                >
                  None
                </button>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {ALL_OBJECTIVES.map((obj) => {
                const checked = selectedObjectives.includes(obj.value);
                return (
                  <span key={obj.value} className="inline-flex items-center">
                    <label
                      className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] cursor-pointer transition-colors border ${checked ? "bg-ag-accent/15 text-ag-accent border-ag-accent/30" : "text-ag-text-muted bg-ag-dark border-ag-border"}`}
                      title={obj.desc}
                    >
                      <input type="checkbox" checked={checked}
                        onChange={() => toggleObjective(obj.value)}
                        className="rounded border-ag-border accent-ag-accent w-3 h-3" />
                      {obj.label}
                    </label>
                    <UserNote noteKey={`obj:${obj.value}`} />
                  </span>
                );
              })}
            </div>
            <div className="text-[10px] text-ag-text-muted">
              {selectedObjectives.length} of {ALL_OBJECTIVES.length} selected
            </div>
          </div>

          {/* ── Mode + Analyse ── */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-1">
              <button
                onClick={() => setMode("quick")}
                title="Heuristic analysis — analyses pool statistics to recommend settings. Completes in ~10 seconds."
                className={`px-3 py-1 text-xs rounded font-medium transition-colors ${mode === "quick" ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted bg-ag-dark border border-ag-border"}`}
              >
                Quick
              </button>
              <button
                onClick={() => setMode("thorough")}
                title="Cross-validation sweep — tests setting combinations to find what works best. Use the depth slider to control speed vs thoroughness."
                className={`px-3 py-1 text-xs rounded font-medium transition-colors ${mode === "thorough" ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted bg-ag-dark border border-ag-border"}`}
              >
                Thorough
              </button>
            </div>
            <button
              onClick={handleRun}
              disabled={loading || totalSources < 1}
              className="px-4 py-1.5 bg-blue-600 text-white rounded text-xs font-medium hover:bg-blue-500 disabled:opacity-50 transition-colors"
            >
              {loading
                ? mode === "thorough" ? thoroughProgress || "Running CV..." : "Analysing..."
                : mode === "thorough" ? `Analyse (${combos} combos)` : "Analyse"}
            </button>
            {result && (
              <button onClick={applyAll}
                className="px-4 py-1.5 bg-green-600 text-white rounded text-xs font-medium hover:bg-green-500 transition-colors"
              >
                Apply All
              </button>
            )}
          </div>

          {/* ── Search Depth — thorough mode only ── */}
          {mode === "thorough" && !loading && (
            <div className="space-y-2 bg-ag-dark/30 rounded-lg p-3 border border-ag-border/50">
              {/* Master slider */}
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <label className="text-[11px] font-medium text-ag-text"
                    title="Controls how many setting combinations are tested. Lower = faster but less thorough. Higher = more combinations explored.">
                    Master Depth
                  </label>
                  <span className="text-[11px] font-mono text-ag-accent">{depthPct}%</span>
                </div>
                <input type="range" min={1} max={100} step={1} value={depthPct}
                  onChange={(e) => handleMasterChange(Number(e.target.value))}
                  className="w-full h-1.5 bg-ag-dark rounded-full appearance-none cursor-pointer accent-ag-accent"
                  title="Controls how many setting combinations are tested. Moving this resets all per-dimension overrides below." />
                <div className="flex justify-between text-[10px] text-ag-text-muted">
                  <span>Fast</span>
                  <span>Thorough</span>
                </div>
              </div>

              {/* Per-dimension breakdowns */}
              <div className="space-y-1.5">
                <DimensionRow label="Guards" value={eff.guards} max={4}
                  labels={GUARD_LABELS} isOverridden={dimGuards !== null}
                  onChange={(v) => setDimGuards(v)} onReset={() => setDimGuards(null)}
                  tip="Overfitting guard preset levels to sweep: light (log caps only) → moderate (+ Z-score + normal range) → aggressive (+ Bayesian shrinkage) → max (all guards at maximum strength)." />

                {/* Quality Filter sliders — 3 independent axes */}
                <div className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider mt-2 mb-0.5">
                  Quality Filters
                  <span className="ml-1 font-normal normal-case text-ag-text-muted/70">
                    — how many values to test per filter (centered on your current settings)
                  </span>
                </div>
                <DimensionRow label="Min PF" value={eff.filterPf} max={PF_POOL.length}
                  labels={PF_POOL.map(v => String(v))} isOverridden={dimFilterPf !== null}
                  onChange={(v) => setDimFilterPf(v)} onReset={() => setDimFilterPf(null)}
                  tip={`Min Profit Factor values to sweep. Pool: ${PF_POOL.join(", ")}. More values = tests stricter and more permissive PF thresholds around your current setting.`} />
                <DimensionRow label="Min Trades" value={eff.filterTrades} max={TRADES_POOL.length}
                  labels={TRADES_POOL.map(v => String(v))} isOverridden={dimFilterTrades !== null}
                  onChange={(v) => setDimFilterTrades(v)} onReset={() => setDimFilterTrades(null)}
                  tip={`Min Trades values to sweep. Pool: ${TRADES_POOL.join(", ")}. Tests whether requiring more or fewer trades per asset improves OOS performance.`} />
                <DimensionRow label="Min WR %" value={eff.filterWr} max={WR_POOL.length}
                  labels={WR_POOL.map(v => `${v}%`)} isOverridden={dimFilterWr !== null}
                  onChange={(v) => setDimFilterWr(v)} onReset={() => setDimFilterWr(null)}
                  tip={`Min Win Rate values to sweep. Pool: ${WR_POOL.join("%, ")}%. Tests whether requiring a minimum win rate helps or over-constrains the pool.`} />

                {/* Seed combos — toggle between depth-based default and custom picker */}
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    {!useCustomSeeds ? (
                      <DimensionRow label="Seeds" value={eff.seedCombos} max={4}
                        labels={SEED_LABELS} isOverridden={dimSeedCombos !== null}
                        onChange={(v) => setDimSeedCombos(v)} onReset={() => setDimSeedCombos(null)}
                        tip="Seed ranking metric combinations to sweep. Tests different ways of ranking assets before the optimizer selects them. More combos = tests whether sortino-only, sortino+stability, etc. matters." />
                    ) : (
                      <span className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider">
                        Custom Seed Combos ({customSeedCombos.length})
                      </span>
                    )}
                    <label className="flex items-center gap-1 text-[10px] text-ag-text cursor-pointer ml-auto shrink-0"
                      title="When enabled, lets you build your own seed metric combinations instead of using the 4 default presets.">
                      <input type="checkbox" checked={useCustomSeeds} onChange={(e) => setUseCustomSeeds(e.target.checked)}
                        className="rounded border-ag-border accent-ag-accent" />
                      Custom<UserNote noteKey="rec:custom_seeds" />
                    </label>
                  </div>

                  {useCustomSeeds && (
                    <div className="bg-ag-dark/50 rounded p-2 space-y-2 border border-ag-border/30">
                      {/* Existing combos */}
                      {customSeedCombos.map((combo, ci) => (
                        <div key={ci} className="flex items-center gap-1 text-[10px]">
                          <span className="text-ag-accent font-mono">{ci + 1}.</span>
                          <span className="text-ag-text">[{combo.join(", ")}]</span>
                          <button onClick={() => setCustomSeedCombos(prev => prev.filter((_, i) => i !== ci))}
                            className="ml-auto text-red-400 hover:text-red-300 text-[10px] px-1" title="Remove this combo">✕</button>
                        </div>
                      ))}

                      {/* Builder: add new combo */}
                      <div className="border-t border-ag-border/20 pt-1.5 space-y-1">
                        <div className="flex items-center gap-1 flex-wrap">
                          {pendingComboMetrics.map((m) => (
                            <span key={m} className="bg-ag-accent/20 text-ag-accent text-[9px] px-1.5 py-0.5 rounded flex items-center gap-0.5">
                              {SEED_METRICS.find(s => s.value === m)?.label || m}
                              <button onClick={() => setPendingComboMetrics(prev => prev.filter(x => x !== m))}
                                className="text-ag-accent/60 hover:text-ag-accent">✕</button>
                            </span>
                          ))}
                          {pendingComboMetrics.length === 0 && (
                            <span className="text-[9px] text-ag-text-muted italic">Pick metrics below to build a combo...</span>
                          )}
                        </div>
                        <div className="flex gap-1 flex-wrap">
                          <select
                            className="bg-ag-dark text-ag-text text-[10px] border border-ag-border/40 rounded px-1 py-0.5 max-w-[160px]"
                            value=""
                            onChange={(e) => {
                              const v = e.target.value;
                              if (v && !pendingComboMetrics.includes(v)) setPendingComboMetrics(prev => [...prev, v]);
                            }}
                          >
                            <option value="">+ Add metric...</option>
                            {SEED_CATEGORIES.map((cat) => (
                              <optgroup key={cat.group} label={cat.group}>
                                {cat.metrics.map((m) => (
                                  <option key={m.value} value={m.value} disabled={pendingComboMetrics.includes(m.value)}>
                                    {m.label}
                                  </option>
                                ))}
                              </optgroup>
                            ))}
                          </select>
                          <button
                            onClick={() => {
                              if (pendingComboMetrics.length > 0) {
                                setCustomSeedCombos(prev => [...prev, [...pendingComboMetrics]]);
                                setPendingComboMetrics([]);
                              }
                            }}
                            disabled={pendingComboMetrics.length === 0}
                            className="text-[10px] px-2 py-0.5 rounded bg-ag-accent/20 text-ag-accent hover:bg-ag-accent/30 disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            Add Combo
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
                <DimensionRow label="CV Folds" value={eff.folds} max={3}
                  labels={["1 fold (65% train)", "2 folds (65%+70%)", "3 folds (65%+70%+75%)"]} isOverridden={dimFolds !== null}
                  onChange={(v) => setDimFolds(v)} onReset={() => setDimFolds(null)}
                  tip="Cross-validation folds — how many train/validation splits to average over. 1 fold = fast but noisy. 3 folds = slower but much more reliable results." />
              </div>

              {/* Toggles */}
              <div className="flex items-center gap-4 flex-wrap">
                <label className="flex items-center gap-1.5 text-[11px] text-ag-text cursor-pointer"
                  title="When checked, adds extra presets that test unusually high and low parameter values (e.g., very permissive and very strict filters). Adds more combinations to the sweep.">
                  <input type="checkbox" checked={extremeValues} onChange={(e) => setExtremeValues(e.target.checked)}
                    className="rounded border-ag-border accent-ag-accent" />
                  Try extreme values<UserNote noteKey="rec:extreme_values" />
                </label>
                <label className="flex items-center gap-1.5 text-[11px] text-ag-text cursor-pointer"
                  title="When checked, tests different seed prior weights (0.0, 0.10, 0.20). When unchecked, uses 0.10 only. Can add up to 3x more combinations at high depth.">
                  <input type="checkbox" checked={sweepSeedPrior} onChange={(e) => setSweepSeedPrior(e.target.checked)}
                    className="rounded border-ag-border accent-ag-accent" />
                  Sweep seed weight<UserNote noteKey="rec:sweep_seed_prior" />
                </label>
              </div>

              {/* Estimate */}
              <div className="text-[10px] text-ag-text-muted">
                ~{combos} combos × {eff.folds} fold{eff.folds > 1 ? "s" : ""} = {evals} evaluations
                {" "}({selectedAlgos.length} algo{selectedAlgos.length > 1 ? "s" : ""}, {selectedObjectives.length} obj, {effSeedCount} seed{effSeedCount > 1 ? "s" : ""}, {eff.filterPf}×{eff.filterTrades}×{eff.filterWr} PF×Trades×WR)
                {estMinutes > 0 && (
                  <span className="ml-1 text-ag-accent">
                    · est. {estMinutes < 60 ? `${estMinutes} min` : `${(estMinutes / 60).toFixed(1)} hrs`}
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Progress bar — thorough mode */}
          {loading && mode === "thorough" && (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-ag-text-muted">{thoroughProgress || "Starting..."}</span>
                <span className="text-ag-accent font-medium">{progressPct}%</span>
              </div>
              <div className="w-full h-1.5 bg-ag-dark rounded-full overflow-hidden">
                <div className="h-full bg-ag-accent rounded-full transition-all duration-500 ease-out"
                  style={{ width: `${progressPct}%` }} />
              </div>
            </div>
          )}

          {error && (
            <div className="text-xs text-red-400 bg-red-900/20 rounded px-2 py-1">{error}</div>
          )}

          {/* Elapsed time + Pool Profile */}
          {result && (
            <div className="flex items-center gap-3 text-xs text-ag-text-muted">
              <span className="font-medium text-ag-text">Completed in {result.elapsed_seconds < 60 ? `${result.elapsed_seconds}s` : `${(result.elapsed_seconds / 60).toFixed(1)} min`}</span>
              <span>·</span>
              <span>{result.profile.n_assets} assets from {result.profile.n_batches} batch{result.profile.n_batches !== 1 ? "es" : ""}</span>
              {result.top_presets && result.top_presets.length > 0 && (
                <>
                  <span>·</span>
                  <span>{result.cv_results?.length ?? result.top_presets.length} combos tested</span>
                </>
              )}
            </div>
          )}
          {result && (
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2 text-xs">
              <ProfileStat label="Assets" value={result.profile.n_assets} />
              <ProfileStat label="Batches" value={result.profile.n_batches} />
              <ProfileStat label="Longs" value={result.profile.n_longs} />
              <ProfileStat label="Shorts" value={result.profile.n_shorts} />
              <ProfileStat label="Median Trades" value={result.profile.trade_count_median.toFixed(0)} warn={result.profile.trade_count_median < 15} />
              <ProfileStat label="Suspicious" value={result.profile.n_suspicious} warn={result.profile.n_suspicious > 0} />
              <ProfileStat label="Avg |Corr|" value={result.profile.corr_mean_abs.toFixed(2)} />
              <ProfileStat label="|Corr| > 0.50" value={`${result.profile.corr_density_above_50.toFixed(0)}%`} warn={result.profile.corr_density_above_50 > 15} />
              <ProfileStat label="PF Spread" value={result.profile.pf_spread.toFixed(1)} />
              <ProfileStat label="Quality Gini" value={result.profile.quality_gini.toFixed(2)} />
              <ProfileStat label="< 10 trades" value={`${result.profile.pct_below_10_trades.toFixed(0)}%`} warn={result.profile.pct_below_10_trades > 30} />
              <ProfileStat label="Batch Balance" value={result.profile.batch_balance_ratio.toFixed(2)} />
            </div>
          )}

          {/* Quick Mode Recommendations */}
          {result && grouped && (
            <div className="space-y-2">
              <div className="text-xs font-medium text-ag-text">Recommendations</div>
              {Object.entries(grouped).map(([cat, recs]) => (
                <div key={cat} className="space-y-1">
                  <div className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider">
                    {CATEGORY_LABELS[cat] || cat}
                  </div>
                  {recs.map((rec, i) => {
                    const friendlyName = SETTING_LABELS[rec.setting] || rec.setting;
                    const friendlyValue = friendlySettingValue(rec.setting, rec.value);
                    const isOpen = expandedRec === `${cat}-${i}`;
                    const alternatives = getAlternatives(rec.setting, rec.value);
                    return (
                      <div key={`${cat}-${i}`} className="bg-ag-dark/40 rounded overflow-hidden">
                        <div className="flex items-start gap-2 px-2 py-1.5">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-xs font-medium text-ag-accent">{friendlyName}</span>
                              <button
                                onClick={() => setExpandedRec(isOpen ? null : `${cat}-${i}`)}
                                className="text-xs text-ag-text hover:text-ag-accent transition-colors underline decoration-dotted underline-offset-2 cursor-pointer"
                                title={alternatives.length > 0 ? "Click to see alternatives" : friendlyValue}
                              >
                                = {friendlyValue} {alternatives.length > 0 ? (isOpen ? "▾" : "▸") : ""}
                              </button>
                              <span className={`text-[10px] ${CONFIDENCE_COLORS[confidenceLevel(rec.confidence)]}`}>
                                {confidenceLabel(rec.confidence)} ({(rec.confidence * 100).toFixed(0)}%)
                              </span>
                            </div>
                            <div className="text-[11px] text-ag-text-muted mt-0.5 leading-relaxed">{rec.reason}</div>
                          </div>
                          <button onClick={() => applyRecommendation(rec)}
                            className="shrink-0 px-2 py-0.5 text-[10px] bg-ag-accent/10 text-ag-accent rounded hover:bg-ag-accent/20 transition-colors"
                            title="Apply this setting">
                            Apply
                          </button>
                        </div>
                        {isOpen && alternatives.length > 0 && (
                          <div className="px-3 pb-2 space-y-1 border-t border-ag-border/20 pt-2">
                            <div className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider mb-1">
                              All options for {friendlyName}
                            </div>
                            {alternatives.map((alt) => (
                              <div key={alt.value} className={`flex items-start gap-2 text-[11px] rounded px-2 py-1 ${String(rec.value) === alt.value ? "bg-ag-accent/10 border border-ag-accent/20" : "bg-ag-dark/30"}`}>
                                <span className={`shrink-0 font-medium ${String(rec.value) === alt.value ? "text-ag-accent" : "text-ag-text"}`}>
                                  {alt.label} {String(rec.value) === alt.value ? "← recommended" : ""}
                                </span>
                                <span className="text-ag-text-muted leading-relaxed">{alt.desc}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          )}

          {/* Thorough Mode: Top Presets */}
          {result && result.top_presets && result.top_presets.length > 0 && (
            <div className="space-y-2">
              <div className="text-xs font-medium text-ag-text">
                Cross-Validation Results — Top {result.top_presets.length} Presets
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-ag-text-muted border-b border-ag-border">
                      <th className="text-left py-1 pr-2" title="Rank — lower is better. Ranked by composite score.">#</th>
                      <th className="text-left py-1 pr-2" title="Which optimizer algorithm was used for this preset.">Algorithm</th>
                      <th className="text-left py-1 pr-2" title="The scoring function used to select assets (e.g., stability favours smooth equity curves).">Objective</th>
                      <th className="text-left py-1 pr-2" title="Overfitting guard preset: light = gentle caps, moderate = Z-score + caps, aggressive = Bayesian shrinkage + tight caps, max = all guards on maximum.">Guard</th>
                      <th className="text-left py-1 pr-2" title="Which seed ranking metrics were used to score candidate assets before optimisation.">Seeds</th>
                      <th className="text-right py-1 pr-2" title="Validation stability — how smooth the equity curve is on unseen data (0-1, higher is better). Above 0.7 is good.">Val Stability</th>
                      <th className="text-right py-1 pr-2" title="Validation profit factor — gross profit ÷ gross loss on unseen data. Above 1.0 means profitable, above 1.5 is good.">Val PF</th>
                      <th className="text-right py-1 pr-2" title="Validation performance ÷ training performance. 1.0 = no overfitting. Above 0.7 is acceptable, below 0.5 suggests overfitting.">Overfit Ratio</th>
                      <th className="text-right py-1 pr-2" title="Combined score blending stability, profit factor, and overfit ratio. Higher is better. Used to rank the presets.">Composite</th>
                      <th className="py-1"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.top_presets.map((p, i) => {
                      const algoKey = String(p.settings?.div_algo || "");
                      const isExpanded = expandedPreset === i;
                      const fullSettings = (p as Record<string, unknown>).full_settings as Record<string, unknown> | undefined;
                      return (
                        <PresetRow
                          key={i}
                          index={i}
                          algoKey={algoKey}
                          preset={p}
                          fullSettings={fullSettings || p.settings || {}}
                          isExpanded={isExpanded}
                          onToggle={() => setExpandedPreset(isExpanded ? null : i)}
                          onApply={() => applyPreset(fullSettings || p.settings || {})}
                        />
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {result.cv_results && result.cv_results.length > 5 && (
                <div className="text-[10px] text-ag-text-muted">
                  Showing top 5 of {result.cv_results.length} tested combinations
                </div>
              )}
            </div>
          )}

          {/* Suspicious assets */}
          {result && result.profile.suspicious_tags.length > 0 && (
            <div className="space-y-1">
              <div className="text-xs font-medium text-orange-400">
                Suspicious Assets ({result.profile.n_suspicious})
              </div>
              <div className="text-[11px] text-ag-text-muted leading-relaxed">
                These assets are 2σ+ outliers on 3 or more metrics within their batch — likely overfitted:
              </div>
              <div className="flex flex-wrap gap-1">
                {result.profile.suspicious_tags.map((tag) => (
                  <span key={tag} className="px-1.5 py-0.5 text-[10px] bg-orange-900/30 text-orange-400 rounded">
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Sub-components ── */

function DimensionRow({ label, value, max, labels, isOverridden, onChange, onReset, tip }: {
  label: string;
  value: number;
  max: number;
  labels: string[];
  isOverridden: boolean;
  onChange: (v: number) => void;
  onReset: () => void;
  tip: string;
}) {
  const selectedLabels = labels.slice(0, value).join(", ");
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-20 text-ag-text-muted shrink-0" title={tip}>{label}</span>
      <input type="range" min={1} max={max} step={1} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="flex-1 h-1 bg-ag-dark rounded-full appearance-none cursor-pointer accent-ag-accent"
        title={tip} />
      <span className="w-8 text-center font-mono text-ag-accent">{value}/{max}</span>
      <span className="min-w-[10rem] max-w-[16rem] text-ag-text-muted text-[10px] truncate" title={selectedLabels}>
        {selectedLabels}
      </span>
      {isOverridden && (
        <button onClick={onReset} className="text-[10px] text-ag-text-muted hover:text-ag-accent" title="Reset to master depth">↺</button>
      )}
    </div>
  );
}

function ProfileStat({ label, value, warn }: { label: string; value: string | number; warn?: boolean }) {
  return (
    <div className="bg-ag-dark/40 rounded px-2 py-1.5">
      <div className="text-[10px] text-ag-text-muted">{label}</div>
      <div className={`text-xs font-medium ${warn ? "text-orange-400" : "text-ag-text"}`}>{value}</div>
    </div>
  );
}

/** Get alternatives with descriptions for discrete settings. */
function getAlternatives(setting: string, _currentValue: unknown): Array<{ value: string; label: string; desc: string }> {
  if (setting === "div_algo") {
    return ALL_ALGOS.map(a => ({
      value: a,
      label: ALGO_INFO[a]?.display || a,
      desc: ALGO_INFO[a]?.tip || "",
    }));
  }
  if (setting === "objective") {
    return ALL_OBJECTIVES.map(o => ({
      value: o.value,
      label: o.label,
      desc: o.desc,
    }));
  }
  if (setting === "refinement_mode") {
    return [
      { value: "prune_and_swap", label: "Prune & Swap", desc: "Full refinement — removes deadweight assets, then swaps in better alternatives. Most thorough." },
      { value: "prune_only", label: "Prune Only", desc: "Remove underperforming assets but don't try replacements. Faster, less aggressive." },
      { value: "swap_only", label: "Swap Only", desc: "Only try swapping assets for better ones. Keeps portfolio size fixed." },
    ];
  }
  if (setting === "of_wf_validation_metric") {
    return [
      { value: "stability", label: "Stability", desc: "Smoothest equity curve on validation data. Hardest to game, rewards consistency." },
      { value: "sortino", label: "Sortino", desc: "Best downside risk-adjusted return on validation data." },
      { value: "sharpe", label: "Sharpe", desc: "Best total risk-adjusted return on validation data." },
      { value: "profit_factor", label: "Profit Factor", desc: "Highest win/loss ratio on validation data." },
    ];
  }
  if (setting === "seed_metrics") {
    return [
      { value: "sortino, stability", label: "Sortino + Stability", desc: "Balanced — Sortino for risk-adjusted return, stability for consistency. Best for low trade counts." },
      { value: "sortino, sharpe", label: "Sortino + Sharpe", desc: "Dual risk-adjusted metrics. Good for pools with adequate trade counts." },
      { value: "sortino, sharpe, stability", label: "Sortino + Sharpe + Stability", desc: "Three-way ranking — more metrics means smoother seed scores." },
      { value: "stability", label: "Stability only", desc: "Pure consistency ranking — most conservative, best when trade counts are very low." },
    ];
  }
  // No expandable alternatives for numeric/boolean settings
  return [];
}

function PresetRow({ index, algoKey, preset, fullSettings, isExpanded, onToggle, onApply }: {
  index: number;
  algoKey: string;
  preset: { settings: Record<string, unknown>; avg_val_stability: number; avg_val_pf: number; avg_overfit_ratio: number; composite_score: number };
  fullSettings: Record<string, unknown>;
  isExpanded: boolean;
  onToggle: () => void;
  onApply: () => void;
}) {
  return (
    <>
      <tr className={`border-b border-ag-border/30 hover:bg-ag-dark/30 cursor-pointer ${isExpanded ? "bg-ag-dark/30" : ""}`}
        onClick={onToggle} title="Click to see all settings for this preset">
        <td className="py-1.5 pr-2 text-ag-text-muted">{index + 1}</td>
        <td className="py-1.5 pr-2 text-ag-text" title={ALGO_INFO[algoKey]?.tip}>
          {ALGO_INFO[algoKey]?.display || algoKey || "—"}
        </td>
        <td className="py-1.5 pr-2 text-ag-text">{String(preset.settings?.objective || "—")}</td>
        <td className="py-1.5 pr-2 text-ag-text">{String(preset.settings?.guard_preset || "off")}</td>
        <td className="py-1.5 pr-2 text-ag-text text-[10px]">
          {Array.isArray(preset.settings?.seed_metrics) ? (preset.settings.seed_metrics as string[]).join(", ") : "—"}
        </td>
        <td className="py-1.5 pr-2 text-right text-ag-text">{preset.avg_val_stability?.toFixed(3)}</td>
        <td className="py-1.5 pr-2 text-right text-ag-text">{preset.avg_val_pf?.toFixed(2)}</td>
        <td className={`py-1.5 pr-2 text-right ${preset.avg_overfit_ratio >= 0.7 ? "text-green-400" : preset.avg_overfit_ratio >= 0.5 ? "text-orange-400" : "text-red-400"}`}>
          {preset.avg_overfit_ratio?.toFixed(2)}
        </td>
        <td className="py-1.5 pr-2 text-right font-medium text-ag-accent">{preset.composite_score?.toFixed(3)}</td>
        <td className="py-1.5">
          <button
            onClick={(e) => { e.stopPropagation(); onApply(); }}
            className="px-2 py-0.5 text-[10px] bg-green-600/20 text-green-400 rounded hover:bg-green-600/30 transition-colors"
            title="Apply all settings from this preset to the optimizer">
            Apply
          </button>
        </td>
      </tr>
      {isExpanded && (
        <tr className="border-b border-ag-border/30">
          <td colSpan={10} className="py-2 px-3">
            <div className="bg-ag-dark/50 rounded-lg p-3 border border-ag-border/30">
              <div className="text-[11px] font-medium text-ag-text-muted mb-2">All Settings for Preset #{index + 1}</div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-4 gap-y-1">
                {Object.entries(fullSettings).map(([key, val]) => (
                  <div key={key} className="flex items-baseline gap-1.5 text-[11px]">
                    <span className="text-ag-text-muted shrink-0">{SETTING_LABELS[key] || key}:</span>
                    <span className="text-ag-text font-mono truncate" title={formatSettingValue(val)}>
                      {formatSettingValue(val)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
