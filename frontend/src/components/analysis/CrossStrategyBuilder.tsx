"use client";
import { useState, useEffect, useRef, useMemo } from "react";
import { api } from "@/lib/api";
import type { BatchBacktestRunItem, CrossStrategyCandidate, CrossBuildResult, CorrelationResult, PortfolioItem, AffinityReportResult, SmartGroupResult } from "@/lib/api";
import CorrelationHeatmap from "./CorrelationHeatmap";
import CrossCandidateCard from "./CrossCandidateCard";
import type { OosSummary } from "./CrossCandidateCard";
import CrossStrategyConfig from "./CrossStrategyConfig";
import CrossBuilderControls from "./CrossBuilderControls";
import OptimizerTC from "./OptimizerTC";
import type { TCConstraints } from "./OptimizerTC";
import OptimizerBenchmark from "./OptimizerBenchmark";
import type { BenchmarkConfig } from "./OptimizerBenchmark";
import OptimizerHarmonyGate from "./OptimizerHarmonyGate";
import type { HarmonyConfig } from "./OptimizerHarmonyGate";
import { DEFAULT_HARMONY_CONFIG } from "./OptimizerHarmonyGate";
import type { PerBatchConfig, PerBatchAllocation } from "./CrossStrategyConfig";
import type { AutoSettings, ManualSettings, SharedSettings, DirectionalSettings } from "./CrossBuilderControls";
import PortfolioSelector from "@/components/common/PortfolioSelector";
import PortfolioExcludeSelector from "@/components/common/PortfolioExcludeSelector";
import type { ExcludeMode, ExcludeMethod } from "@/components/common/PortfolioExcludeSelector";
import CategoryFilterSelect from "@/components/common/CategoryFilterSelect";
import HintTip from "@/components/common/HintTip";
import OptimizerConfigManager from "./OptimizerConfigManager";
import OptimizerRiskProfile from "./OptimizerRiskProfile";
import type { RiskProfileConfig, AutoRiskResult } from "./OptimizerRiskProfile";
import OptimizationProgress from "@/components/common/OptimizationProgress";
import OptimizerExplainer from "./OptimizerExplainer";
import SettingsRecommender from "./SettingsRecommender";
import BatchAffinityReport from "./BatchAffinityReport";
import { useTaskManager } from "@/contexts/TaskManager";

interface CrossBuilderProps {
  dateTo?: string;
  onNavigateTab?: (tab: string) => void;
  optimizerEngine?: string;
}

const PERSONAL_GRADES = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"] as const;

function batchGradeBadgeColor(g: string | null | undefined): string {
  if (!g) return "";
  if (g.startsWith("A")) return "text-ag-green bg-ag-green/15";
  if (g.startsWith("B")) return "text-blue-400 bg-blue-400/15";
  if (g.startsWith("C")) return "text-yellow-400 bg-yellow-400/15";
  if (g.startsWith("D") || g === "F") return "text-ag-red bg-ag-red/15";
  return "text-ag-text-muted bg-ag-dark";
}

/** Single batch row in the optimizer selector — shows grades, metrics, notes, personal grade editor */
function BatchSelectorRow({ b, selected, onToggle, editingId, onEditStart, onEditDone, onEditCancel }: {
  b: BatchBacktestRunItem;
  selected: boolean;
  onToggle: () => void;
  editingId: number | null;
  onEditStart: (id: number) => void;
  onEditDone: (updated: BatchBacktestRunItem) => void;
  onEditCancel: () => void;
}) {
  const grade = b.batch_grade;
  const pg = b.personal_grade;
  const pf = b.avg_profit_factor;
  const wr = b.avg_win_rate;
  const pnl = b.total_net_profit;
  const isEditing = editingId === b.id;
  const [editGrade, setEditGrade] = useState(pg || "");
  const [editNote, setEditNote] = useState(b.notes || "");
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await api.updateBatchNotes(b.id, undefined, editNote || undefined, editGrade || "");
      onEditDone(res);
    } catch { onEditCancel(); }
    setSaving(false);
  };

  return (
    <div className="relative">
      <div className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-ag-card-hover text-xs group">
        <input type="checkbox" checked={selected} onChange={onToggle}
          className="accent-ag-accent flex-shrink-0 cursor-pointer" />
        <span className="text-ag-text truncate min-w-0 cursor-pointer" onClick={onToggle}>{b.name || `#${b.id}`}</span>
        {/* Note indicator — hover to see */}
        {b.notes && (
          <span className="relative flex-shrink-0 cursor-help" title={b.notes}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-ag-accent/60">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
            </svg>
          </span>
        )}
        <span className="ml-auto flex items-center gap-2 flex-shrink-0">
          <span className="text-ag-text-muted">{b.completed_symbols} sym</span>
          {/* Personal grade — outlined style to distinguish from computed */}
          {pg && <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold border border-current ${batchGradeBadgeColor(pg)}`} title={`My grade: ${pg}${b.notes ? "\n" + b.notes : ""}`}>★{pg}</span>}
          {/* Computed grade */}
          {grade && <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${batchGradeBadgeColor(grade)}`}>{grade}</span>}
          {pf != null && <span className="text-ag-text-muted">PF <span className={`font-mono ${pf >= 1.5 ? "text-ag-green" : pf >= 1 ? "text-ag-text" : "text-ag-red"}`}>{pf.toFixed(2)}</span></span>}
          {wr != null && <span className="text-ag-text-muted">WR <span className="font-mono text-ag-text">{wr.toFixed(1)}%</span></span>}
          {pnl != null && <span className={`font-mono ${pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>{pnl >= 0 ? "+" : ""}${pnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>}
          {/* Edit button */}
          <button onClick={(e) => { e.stopPropagation(); onEditStart(b.id); setEditGrade(pg || ""); setEditNote(b.notes || ""); }}
            className="opacity-0 group-hover:opacity-100 transition-opacity text-ag-text-muted hover:text-ag-accent"
            title="Edit personal grade & note">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
            </svg>
          </button>
        </span>
      </div>
      {/* Inline editor popover */}
      {isEditing && (
        <div className="absolute z-50 right-0 top-full mt-1 bg-ag-dark border border-ag-border rounded-lg shadow-lg p-3 space-y-2 w-72"
          onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-2">
            <label className="text-[10px] text-ag-text-muted uppercase w-16">My Grade</label>
            <select value={editGrade} onChange={(e) => setEditGrade(e.target.value)}
              className="flex-1 text-xs bg-ag-card border border-ag-border rounded px-2 py-1 text-ag-text">
              <option value="">— None —</option>
              {PERSONAL_GRADES.map(g => <option key={g} value={g}>{g}</option>)}
            </select>
          </div>
          <div className="flex items-start gap-2">
            <label className="text-[10px] text-ag-text-muted uppercase w-16 pt-1">Note</label>
            <textarea value={editNote} onChange={(e) => setEditNote(e.target.value)}
              rows={2} placeholder="Personal notes..."
              className="flex-1 text-xs bg-ag-card border border-ag-border rounded px-2 py-1 text-ag-text resize-none" />
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={onEditCancel} className="text-[10px] text-ag-text-muted hover:text-ag-text px-2 py-0.5">Cancel</button>
            <button onClick={handleSave} disabled={saving}
              className="text-[10px] bg-ag-accent text-white px-3 py-0.5 rounded hover:bg-ag-accent/80 disabled:opacity-50">
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function CrossStrategyBuilder({ dateTo, onNavigateTab, optimizerEngine = "current" }: CrossBuilderProps) {
  const [batches, setBatches] = useState<BatchBacktestRunItem[]>([]);
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [selectedPortfolioIds, setSelectedPortfolioIds] = useState<Set<number>>(new Set());
  const [mode, setMode] = useState<"auto" | "manual" | "directional">("auto");
  const [dirS, setDirS] = useState<DirectionalSettings>({
    l2Groups: [], nPerBatch: 5,
    objL1: "hybrid", objL2: "uncorrelated", objL3: "max_sortino",
    kL1: 5, kL2: 5, kL3: 3,
    l1QualityGate: false, nPerGroup: 15,
  });
  const [autoS, setAutoS] = useState<AutoSettings>({
    splitMode: true, nAssets: 10,
    nLongs: 5, nShorts: 5, withinObj: "uncorrelated", combinedObj: "stability", nPortfolios: 3,
    poolPerBatch: 0,
  });
  const [manualS, setManualS] = useState<ManualSettings>({
    l2Groups: [],
    objLayer1: "hybrid", objLayer2: "stability", objLayer3: "hybrid",
    candidatesPerStrategy: 3, nFinalStrats: 2, nPortfolios: 3,
    l1QualityGate: false, nPerGroup: 15,
    poolMode: false, poolPerBatch: 50, l2NAssets: 15, l2NVariants: 10,
    l2MaxOverlap: 0.7, l3MaxOverlap: 0.7,
    l2SymbolDedup: "keep_all", l2DedupTiebreaker: "pnl",
  });
  const [shared, setShared] = useState<SharedSettings>({
    corrMethod: "standard", divAlgo: "greedy", seedMetrics: ["sortino"], maxSeeds: 20, minTrades: 0, minPf: 0, minWr: 0, maxAssetPf: 0, capAssetPf: false, portfolioMinPf: 0, portfolioMinWr: 0, dateFrom: "", extraSlippage: 0, extraCommission: 0, fundingDaily: 0, spread: 0,
    recencyEnabled: false, recencyMode: "gradual", recencyBatchSize: 10, recencyBoostFactor: 3.0,
    streamlitLogic: false, maxOverlap: "off",
    expandGreedyPool: true, seedWeights: {}, seedPriorWeight: 0,
    prefilterEnabled: false, prefilterMode: "trend", prefilterHurstMin: 0.50, prefilterTerPercentile: 25,
    prefilterHurstMax: 0.50, prefilterVrMax: 1.0, prefilterMinCrossingRate: 0.05,
    acPostfilter: false, acPostfilterType: "trend", acLag: 1,
    avgRangeBullThreshold: 0, avgRangeBearThreshold: 0, avgRangeAllThreshold: 0, avgRangeFilterMode: false,
    assetQualityMode: "trend", assetQualityGradeMode: "absolute",
    assetQualityMinGrade: "", assetQualityMinTrades: 0,
    assetQualityFilter: false, assetQualityHurstMin: 0.50,
    assetQualityTerPercentile: 25, assetQualityHurstMax: 0.50,
    assetQualityVrMax: 1.0, assetQualityMinCr: 0.05,
    stratQualityMode: "trend", stratQualityMinGrade: "",
    stratQualityFilter: false,
    isWfeIsPct: 60, isWfeMinTrades: 0, isWfeFilter: false, isWfeMinWfe: 0.5,
    seedBlindSelection: true,
    corrJitterEnabled: false, corrJitterTemperature: 0.03,
    refinementEnabled: false, refinementMode: "prune_and_swap", refinementMetric: "",
    // Overfitting Guard
    overfitGuardEnabled: false,
    ofLogEnabled: false, ofLogCapPf: 4.0, ofLogCapSortino: 3.0, ofLogCapPayoff: 3.0, ofLogCapWr: 70.0, ofLogCapSharpe: 2.5, ofLogCapCalmar: 3.0,
    ofZScoreEnabled: false, ofZThreshold: 2.0, ofZPenaltyStrength: 0.15,
    ofBayesEnabled: false, ofBayesK: 5.0,
    ofSigmoidEnabled: false, ofSigCapPf: 4.0, ofSigCapSortino: 3.0, ofSigCapPayoff: 3.0, ofSigCapWr: 70.0, ofSigCapSharpe: 2.5, ofSigCapCalmar: 3.0,
    ofNormalRangeEnabled: false, ofNrPctLow: 20, ofNrPctHigh: 60, ofNrPenaltyScale: 0.3, ofNrBonus: 0.1,
    ofWfRerankEnabled: false, ofWfSplitPct: 70, ofWfValidationMetric: "stability",
    geneticGenerations: 50, geneticPopulation: 100, geneticMutationRate: 0.10, geneticCrossoverRate: 0.70, geneticElitePct: 0.10,
    bybitSizeGate: "off", bybitSizeGateMaxPct: 10,
    sessionFilterEnabled: false, sessionFilterMode: "fixed" as const,
    sessionFilterExcludeDays: [] as number[], sessionFilterExcludeHours: [] as number[],
    sessionFilterAutoMinTrades: 10, sessionFilterAutoIncludeInsufficient: true,
    sessionFilterGradeOnCropped: false, sessionFilterSavedMaskId: null as number | null,
  });
  const [prioritizeStability, setPrioritizeStability] = useState(false);
  const [clipIS, setClipIS] = useState(true);
  const [clipOOS, setClipOOS] = useState(true);
  const [oosEndDate, setOosEndDate] = useState("");
  const [perBatch, setPerBatch] = useState<Record<string, PerBatchConfig>>({});
  const [allocations, setAllocations] = useState<Record<string, PerBatchAllocation>>({});
  const [showPerBatch, setShowPerBatch] = useState(false);
  const [perBatchEnabled, setPerBatchEnabled] = useState(true);
  const [tcEnabled, setTcEnabled] = useState(false);
  const [showTC, setShowTC] = useState(false);
  const [tc, setTc] = useState<TCConstraints>({ low_threshold: 10, max_low: 2, high_threshold: 50, min_high: 1 });
  const [tcLongs, setTcLongs] = useState<TCConstraints>({ low_threshold: 10, max_low: 2, high_threshold: 50, min_high: 1 });
  const [tcShorts, setTcShorts] = useState<TCConstraints>({ low_threshold: 10, max_low: 2, high_threshold: 50, min_high: 1 });
  const [bmEnabled, setBmEnabled] = useState(false);
  const [showBM, setShowBM] = useState(false);
  const [bmConfig, setBmConfig] = useState<BenchmarkConfig>({ source: "", split: "time", threshold: 1.2, elite_target: 1.5, exclude_fragile: true, consistency_blend: 0, fragility_blend: 0, wr_harmony_blend: 0, dsr_gate: false });
  // Harmony gate
  const [showHarmony, setShowHarmony] = useState(false);
  const [harmonyConfig, setHarmonyConfig] = useState<HarmonyConfig>(DEFAULT_HARMONY_CONFIG);
  // Category filter
  const [categoryFilter, setCategoryFilter] = useState<string[]>([]);
  // Portfolio exclusion filter
  const [excludePortfolioIds, setExcludePortfolioIds] = useState<Set<number>>(new Set());
  const [excludeMode, setExcludeMode] = useState<ExcludeMode>("full");
  const [excludeKeepPct, setExcludeKeepPct] = useState(20);
  const [excludeMethod, setExcludeMethod] = useState<ExcludeMethod>("score");
  // Risk profile
  const [showRiskProfile, setShowRiskProfile] = useState(false);
  const [riskProfile, setRiskProfile] = useState<RiskProfileConfig>({
    mode: "fixed", value: 0, initialCapital: 10000,
    multiplier: 2, maxStreak: 3, martingaleCompound: "off", martingaleScope: "asset",
    martingaleCountMode: "full_position", martingaleOosReset: true,
    martingaleStartAfter: 0, antiMartingaleResetAfter: 0, kellyN: 2, kellyWarmup: 20,
    kellyScope: "portfolio", kellyLookback: 0, kellyCap: 0, slippage: 0,
  });
  const [sizingMaster, setSizingMaster] = useState("portfolio");
  const [tfConfigs, setTfConfigs] = useState<Record<string, RiskProfileConfig>>({});
  // Auto risk profile
  const [autoRiskEnabled, setAutoRiskEnabled] = useState(false);
  const [autoRiskAggression, setAutoRiskAggression] = useState("conservative");
  const [autoRiskLoading, setAutoRiskLoading] = useState(false);
  const [autoRiskResult, setAutoRiskResult] = useState<AutoRiskResult | null>(null);
  const [autoBaseRiskMart, setAutoBaseRiskMart] = useState(50);
  const [autoMinConfidence, setAutoMinConfidence] = useState(0);
  const [autoStrategyHint, setAutoStrategyHint] = useState("auto");
  // Batch sort + personal grade editing
  const [batchSort, setBatchSort] = useState<"name" | "personal_grade" | "grade" | "pf" | "pnl">("name");
  const [editingBatchId, setEditingBatchId] = useState<number | null>(null);

  const [candidates, setCandidates] = useState<CrossStrategyCandidate[]>([]);
  const [strategies, setStrategies] = useState<Record<number, string>>({});
  const [corr, setCorr] = useState<CorrelationResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<number | null>(null);
  const [savedIndices, setSavedIndices] = useState<Set<number>>(new Set());
  const [savedPortfolioIds, setSavedPortfolioIds] = useState<Map<number, number>>(new Map());
  const [existingFingerprints, setExistingFingerprints] = useState<Set<string>>(new Set());
  const [oosSummaries, setOosSummaries] = useState<Map<number, OosSummary>>(new Map());
  const [error, setError] = useState("");
  const [buildId, setBuildId] = useState(0);
  // Progress bar state
  const [optimStep, setOptimStep] = useState(0);
  const [optimTotal, setOptimTotal] = useState(0);
  const [optimLabel, setOptimLabel] = useState("");
  const [buildTiming, setBuildTiming] = useState<Record<string, number> | null>(null);
  // Affinity report
  const [affinityReport, setAffinityReport] = useState<AffinityReportResult | null>(null);
  const [affinityLoading, setAffinityLoading] = useState(false);
  const [affinityIncludeOos, setAffinityIncludeOos] = useState(false);
  const [affinityOos1Months, setAffinityOos1Months] = useState(6);
  const [affinityOos2Months, setAffinityOos2Months] = useState(3);
  const [affinityPinnedAnchors, setAffinityPinnedAnchors] = useState<Set<number>>(new Set());
  // Smart Groups
  const [smartGroupLoading, setSmartGroupLoading] = useState(false);
  const [smartGroupResult, setSmartGroupResult] = useState<SmartGroupResult | null>(null);
  const [smartGroupApplied, setSmartGroupApplied] = useState(false);
  // V2: Smart Selection presets + advanced settings
  const [sgPreset, setSgPreset] = useState<"balanced" | "conservative" | "aggressive">("balanced");
  const [sgAdvanced, setSgAdvanced] = useState(false);
  const [sgHoldoutRatio, setSgHoldoutRatio] = useState(0.15);
  const [sgHoldoutWeight, setSgHoldoutWeight] = useState(0.4);
  const [sgBootstrapEnabled, setSgBootstrapEnabled] = useState(false);
  const [sgBootstrapN, setSgBootstrapN] = useState(100);
  const [sgBootstrapDrop, setSgBootstrapDrop] = useState(0.15);
  const [sgRegimeRefDays, setSgRegimeRefDays] = useState(0);
  const [sgRegimeBandwidth, setSgRegimeBandwidth] = useState(0.0);
  const [sgTrendLookback, setSgTrendLookback] = useState(20);
  const [sgTrendRegimePct, setSgTrendRegimePct] = useState(0.65);
  const [sgTrendStrength, setSgTrendStrength] = useState<string>("small");
  const [preSmartGroupIds, setPreSmartGroupIds] = useState<Set<number> | null>(null); // for undo

  // Persist results across navigation via TaskManager
  const { startOptimization, latestOptimizationTask, addToast } = useTaskManager();
  const CS_TASK_LABEL = "Cross-Strategy Build";

  // Config save/load — collect all settings into a single object
  const currentConfig = {
    mode, autoS, manualS, dirS, shared, prioritizeStability, clipIS, clipOOS, oosEndDate,
    tcEnabled, tc, tcLongs, tcShorts,
    bmEnabled, bmConfig, categoryFilter, riskProfile,
    excludePortfolioIds: Array.from(excludePortfolioIds), excludeMode, excludeKeepPct, excludeMethod,
    harmonyConfig,
    affinityIncludeOos, affinityOos1Months, affinityOos2Months,
    savedBatchIds: Array.from(selectedIds),
    savedPortfolioIds: Array.from(selectedPortfolioIds),
  };

  // Build descriptive default name from current settings
  const buildDefaultName = (): string => {
    // Format: objective / algo / seed ranking(s) / correlation / overfitguard
    // Objective
    let obj = "";
    if (mode === "auto") obj = autoS.combinedObj;
    else if (mode === "manual") obj = manualS.objLayer2;
    else obj = dirS.objL1;

    // Algo
    const algo = shared.divAlgo || "greedy";

    // Seed rankings
    const seeds = shared.seedMetrics.length ? shared.seedMetrics.join(",") : "none";

    // Correlation method
    const corr = shared.corrMethod || "standard";

    // Overfit guard — show abbreviated names of active approaches
    let og = "OFF";
    if (shared.overfitGuardEnabled) {
      const parts: string[] = [];
      if (shared.ofLogEnabled) parts.push("Log");
      if (shared.ofZScoreEnabled) parts.push("Zsc");
      if (shared.ofBayesEnabled) parts.push("Bay");
      if (shared.ofSigmoidEnabled) parts.push("Sig");
      if (shared.ofNormalRangeEnabled) parts.push("NR");
      if (shared.ofWfRerankEnabled) parts.push("WF");
      og = parts.length ? parts.join("+") : "ON";
    }

    return `${obj} / ${algo} / ${seeds} / ${corr} / OG:${og}`;
  };

  const loadConfig = (c: typeof currentConfig) => {
    if (c.mode) setMode(c.mode);
    if (c.autoS) setAutoS((prev) => ({ ...prev, ...c.autoS }));
    if (c.manualS) setManualS((prev) => ({ ...prev, ...c.manualS }));
    if (c.dirS) setDirS((prev) => ({ ...prev, ...c.dirS }));
    if (c.shared) setShared((prev) => ({ ...prev, ...c.shared }));
    setPrioritizeStability(c.prioritizeStability ?? false);
    setClipIS(c.clipIS ?? true);
    setClipOOS(c.clipOOS ?? true);
    setOosEndDate(c.oosEndDate ?? "");
    if (c.oosEndDate) oosDateManualRef.current = true;
    setTcEnabled(c.tcEnabled ?? false);
    if (c.tc) setTc(c.tc);
    if (c.tcLongs) setTcLongs(c.tcLongs);
    if (c.tcShorts) setTcShorts(c.tcShorts);
    setBmEnabled(c.bmEnabled ?? false);
    if (c.bmConfig) setBmConfig(c.bmConfig);
    setCategoryFilter(c.categoryFilter ?? []);
    if (c.excludePortfolioIds) setExcludePortfolioIds(new Set(c.excludePortfolioIds as number[]));
    if (c.excludeMode) setExcludeMode(c.excludeMode as ExcludeMode);
    if (c.excludeKeepPct != null) setExcludeKeepPct(c.excludeKeepPct as number);
    if (c.excludeMethod) setExcludeMethod(c.excludeMethod as ExcludeMethod);
    if (c.riskProfile) setRiskProfile(c.riskProfile);
    if (c.harmonyConfig) setHarmonyConfig(c.harmonyConfig);
    if (c.affinityIncludeOos != null) setAffinityIncludeOos(c.affinityIncludeOos);
    if (c.affinityOos1Months != null) setAffinityOos1Months(c.affinityOos1Months);
    if (c.affinityOos2Months != null) setAffinityOos2Months(c.affinityOos2Months);
    // Restore batch + portfolio selection
    if (c.savedBatchIds && Array.isArray(c.savedBatchIds) && c.savedBatchIds.length > 0) {
      setSelectedIds(new Set(c.savedBatchIds as number[]));
    }
    if (c.savedPortfolioIds && Array.isArray(c.savedPortfolioIds) && c.savedPortfolioIds.length > 0) {
      setSelectedPortfolioIds(new Set(c.savedPortfolioIds as number[]));
    }
    addToast("Config loaded!", "info");
  };

  /** Compute backend-matching fingerprint from raw asset tags (e.g. "BTCUSDT~B138_Long") */
  const candidateFingerprint = (rawAssets: string[]): string => {
    const tags = rawAssets.map(s => {
      const bm = s.match(/~B(-?\d+)/);
      const batchId = bm ? bm[1] : "";
      const clean = s.replace(/~B-?\d+/g, "");
      const side = clean.endsWith("_Long") ? "Long" : clean.endsWith("_Short") ? "Short" : "both";
      const symbol = clean.replace(/_Long$/, "").replace(/_Short$/, "");
      return `${symbol}_${side}_B${batchId}`;
    });
    return tags.sort().join("|");
  };

  /** Extract fingerprints from portfolio list for duplicate detection */
  const refreshFingerprints = (portfolioList: PortfolioItem[]) => {
    const fps = new Set<string>();
    for (const p of portfolioList) {
      const fp = (p.source_strategies as Record<string, unknown>)?.asset_fingerprint;
      if (typeof fp === "string") fps.add(fp);
    }
    setExistingFingerprints(fps);
  };

  useEffect(() => {
    api.batchRuns().then((data) => {
      setBatches(data.filter((b) => b.status === "completed"));
    }).catch(() => { });
    api.portfolios().then((list) => {
      setPortfolios(list);
      refreshFingerprints(list);
    }).catch(() => { });
  }, []);

  const toggleBatch = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const togglePortfolio = (id: number) => {
    setSelectedPortfolioIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleExcludePortfolio = (id: number) => {
    setExcludePortfolioIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const batchIds = Array.from(selectedIds);
  const portfolioIds = Array.from(selectedPortfolioIds);
  const totalSources = batchIds.length + portfolioIds.length;
  const selectedBatches = batches.filter((b) => selectedIds.has(b.id));

  // Guard: only restore settings from TaskManager once per task (prevents re-applying on every render)
  const settingsRestoredForTaskRef = useRef<string | null>(null);

  // Auto-default OOS end date to the latest data_end_date of selected batches
  const oosDateManualRef = useRef(false);
  useEffect(() => {
    if (oosDateManualRef.current) return;
    const dates = selectedBatches
      .map((b) => b.data_end_date)
      .filter((d): d is string => !!d);
    if (dates.length > 0) {
      const maxDate = dates.sort().pop()!.slice(0, 10);
      setOosEndDate(maxDate);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIds.size]);

  const buildPerBatch = () => {
    // Inject shared min trades/pf/wr into per-batch configs
    // If per-batch overrides are disabled, only use shared filters (no per-batch customization)
    const merged = perBatchEnabled ? { ...perBatch } : {};
    const hasSharedFilters = shared.minTrades > 0 || shared.minPf > 0 || shared.minWr > 0;
    if (hasSharedFilters) {
      for (const bid of batchIds) {
        const key = String(bid);
        const cur = merged[key] || { min_trades: 0, min_pf: 0, min_wr: 0, max_longs: 999, max_shorts: 999 };
        merged[key] = {
          ...cur,
          min_trades: Math.max(cur.min_trades, shared.minTrades),
          min_pf: Math.max(cur.min_pf, shared.minPf),
          min_wr: Math.max(cur.min_wr, shared.minWr),
        };
      }
    }
    const hasAny = Object.values(merged).some(
      (c) => c.min_trades > 0 || c.min_pf > 0 || c.min_wr > 0 || c.max_longs < 999 || c.max_shorts < 999,
    );
    return hasAny ? merged : undefined;
  };

  const buildAllocations = () => {
    const hasAny = Object.values(allocations).some((a) => a.n_longs !== 5 || a.n_shorts !== 5);
    return hasAny ? allocations : undefined;
  };

  // Expand per-TF configs → per-batch for API (risk profile + slippage)
  const buildPerTfPayload = (): { risk_profile_sizing_master?: string; risk_profile_batch_configs?: string; per_batch_costs?: string } => {
    if (sizingMaster !== "per_tf" || Object.keys(tfConfigs).length === 0) return {};
    // Map each batch to its TF's risk profile config
    const batchRiskConfigs: Record<string, RiskProfileConfig> = {};
    const batchCosts: Record<string, { slippage: number }> = {};
    let hasAnyCost = false;
    for (const b of selectedBatches) {
      const tf = b.timeframe || "?";
      const cfg = tfConfigs[tf];
      if (!cfg) continue;
      batchRiskConfigs[String(b.id)] = cfg;
      if (cfg.slippage > 0) {
        batchCosts[String(b.id)] = { slippage: cfg.slippage };
        hasAnyCost = true;
      }
    }
    return {
      risk_profile_sizing_master: "per_batch",
      risk_profile_batch_configs: JSON.stringify(batchRiskConfigs),
      ...(hasAnyCost ? { per_batch_costs: JSON.stringify(batchCosts) } : {}),
    };
  };

  // ── Auto Risk Profile: analyze IS streaks and apply result ──
  const runAutoRiskAnalyze = async () => {
    const batchIds = selectedBatches.map((b) => b.id);
    if (batchIds.length === 0) return;
    setAutoRiskLoading(true);
    try {
      const data = await api.autoRiskProfile({
        batch_ids: batchIds,
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
      // Apply the recommended config to the main riskProfile
      setRiskProfile((prev) => ({
        ...prev,
        mode: data.config.mode,
        value: data.config.value || prev.value,
        maxStreak: data.config.maxStreak,
        martingaleStartAfter: data.config.martingaleStartAfter,
        antiMartingaleResetAfter: data.config.antiMartingaleResetAfter,
      }));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Auto risk analysis failed";
      setError(msg);
    } finally {
      setAutoRiskLoading(false);
    }
  };

  const runBuilder = () => {
    if (totalSources < 2) { setError("Select at least 2 data sources"); return; }
    setLoading(true); setError("");
    setBuildId((prev) => prev + 1);
    setSavedIndices(new Set());
    setSavedPortfolioIds(new Map());
    setOosSummaries(new Map());
    setCandidates([]);
    setStrategies({});
    setCorr(null);
    setOptimStep(0); setOptimTotal(0); setOptimLabel(""); setBuildTiming(null);
    settingsRestoredForTaskRef.current = null;

    const pb = buildPerBatch();

    const sharedOpts = {
      seed_metrics: shared.seedMetrics,
      max_seeds: shared.maxSeeds,
      prioritize_stability: prioritizeStability,
      date_from: shared.dateFrom || undefined,
      date_to: dateTo || undefined,
      extra_slippage_pct: shared.extraSlippage > 0 ? shared.extraSlippage : undefined,
      extra_commission_pct: shared.extraCommission > 0 ? shared.extraCommission : undefined,
      funding_daily_pct: shared.fundingDaily > 0 ? shared.fundingDaily : undefined,
      spread_pct: shared.spread > 0 ? shared.spread : undefined,
      per_batch: pb,
      portfolio_ids: portfolioIds.length > 0 ? portfolioIds : undefined,
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
      } : {}),
      ...(shared.recencyEnabled ? {
        recency_enabled: true, recency_mode: shared.recencyMode,
        recency_batch_size: shared.recencyBatchSize, recency_boost_factor: shared.recencyBoostFactor,
      } : {}),
      div_algo: shared.divAlgo,
      ...(shared.divAlgo === "genetic" ? {
        genetic_generations: shared.geneticGenerations,
        genetic_population: shared.geneticPopulation,
        genetic_mutation_rate: shared.geneticMutationRate,
        genetic_crossover_rate: shared.geneticCrossoverRate,
        genetic_elite_pct: shared.geneticElitePct,
      } : {}),
      streamlit_logic: shared.streamlitLogic,
      expand_greedy_pool: shared.expandGreedyPool,
      seed_blind_selection: shared.seedBlindSelection,
      ...(shared.seedPriorWeight > 0 ? { seed_prior_weight: shared.seedPriorWeight } : {}),
      ...(Object.keys(shared.seedWeights).length > 0 ? { seed_weights: shared.seedWeights } : {}),
      ...(categoryFilter.length > 0 ? { category_filter: categoryFilter } : {}),
      ...(excludePortfolioIds.size > 0 ? {
        exclude_portfolio_ids: Array.from(excludePortfolioIds),
        exclude_portfolio_mode: excludeMode,
        ...(excludeMode === "partial" ? {
          exclude_portfolio_keep_pct: excludeKeepPct,
          exclude_portfolio_method: excludeMethod,
        } : {}),
      } : {}),
      ...(shared.maxOverlap !== "off" ? { max_overlap: parseFloat(shared.maxOverlap) } : {}),
      ...(sizingMaster === "per_tf" ? buildPerTfPayload()
        : (riskProfile.mode !== "fixed" || riskProfile.value > 0) ? {
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
        risk_profile_kelly_scope: riskProfile.kellyScope,
        risk_profile_kelly_lookback: riskProfile.kellyLookback,
        risk_profile_kelly_cap: riskProfile.kellyCap,
        ...(riskProfile.applyGroupMultiplier ? { risk_profile_apply_group_multiplier: true } : {}),
      } : {}),
      // Auto-risk per-asset configs
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
      ...(shared.prefilterEnabled ? {
        prefilter_enabled: true, prefilter_mode: shared.prefilterMode,
        prefilter_hurst_min: shared.prefilterHurstMin, prefilter_ter_percentile: shared.prefilterTerPercentile,
        prefilter_hurst_max: shared.prefilterHurstMax, prefilter_vr_max: shared.prefilterVrMax,
        prefilter_min_crossing_rate: shared.prefilterMinCrossingRate,
      } : {}),
      // DSR gate (from Benchmark panel)
      ...(bmEnabled && bmConfig.dsr_gate ? { benchmark_dsr_gate: true } : {}),
      // AC post-filter
      ...(shared.acPostfilter ? {
        ac_postfilter: true, ac_postfilter_type: shared.acPostfilterType, ac_lag: shared.acLag,
      } : {}),
      // Avg range seed config
      ...(shared.seedMetrics.includes("avg_range") ? {
        avg_range_bull_threshold: shared.avgRangeBullThreshold,
        avg_range_bear_threshold: shared.avgRangeBearThreshold,
        avg_range_all_threshold: shared.avgRangeAllThreshold,
        avg_range_filter_mode: shared.avgRangeFilterMode,
      } : {}),
      // Asset Quality seed
      ...(shared.seedMetrics.includes("asset_quality") ? {
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
      } : {}),
      // Strategy Quality seed
      ...(shared.seedMetrics.includes("strat_quality") ? {
        strat_quality_mode: shared.stratQualityMode,
        strat_quality_min_grade: shared.stratQualityMinGrade,
        strat_quality_filter: shared.stratQualityFilter,
      } : {}),
      // IS-WFE seed
      ...(shared.seedMetrics.includes("is_wfe") ? {
        is_wfe_is_pct: shared.isWfeIsPct,
        is_wfe_min_trades: shared.isWfeMinTrades,
        is_wfe_filter: shared.isWfeFilter,
        is_wfe_min_wfe: shared.isWfeMinWfe,
      } : {}),
      // Correlation jitter
      ...(shared.corrJitterEnabled ? {
        corr_jitter_enabled: true,
        corr_jitter_temperature: shared.corrJitterTemperature,
      } : {}),
      // Post-construction refinement
      ...(shared.refinementEnabled ? {
        refinement_enabled: true,
        refinement_mode: shared.refinementMode,
        ...(shared.refinementMetric ? { refinement_metric: shared.refinementMetric } : {}),
      } : {}),
      // Overfitting Guard
      ...(shared.overfitGuardEnabled ? {
        overfit_guard_enabled: true,
        of_log_enabled: shared.ofLogEnabled,
        of_log_cap_pf: shared.ofLogCapPf, of_log_cap_sortino: shared.ofLogCapSortino,
        of_log_cap_payoff: shared.ofLogCapPayoff, of_log_cap_wr: shared.ofLogCapWr,
        of_log_cap_sharpe: shared.ofLogCapSharpe, of_log_cap_calmar: shared.ofLogCapCalmar,
        of_z_score_enabled: shared.ofZScoreEnabled,
        of_z_threshold: shared.ofZThreshold, of_z_penalty_strength: shared.ofZPenaltyStrength,
        of_bayes_enabled: shared.ofBayesEnabled, of_bayes_k: shared.ofBayesK,
        of_sigmoid_enabled: shared.ofSigmoidEnabled,
        of_sig_cap_pf: shared.ofSigCapPf, of_sig_cap_sortino: shared.ofSigCapSortino,
        of_sig_cap_payoff: shared.ofSigCapPayoff, of_sig_cap_wr: shared.ofSigCapWr,
        of_sig_cap_sharpe: shared.ofSigCapSharpe, of_sig_cap_calmar: shared.ofSigCapCalmar,
        of_normal_range_enabled: shared.ofNormalRangeEnabled,
        of_nr_pct_low: shared.ofNrPctLow, of_nr_pct_high: shared.ofNrPctHigh,
        of_nr_penalty_scale: shared.ofNrPenaltyScale, of_nr_bonus: shared.ofNrBonus,
        of_wf_rerank_enabled: shared.ofWfRerankEnabled,
        of_wf_split_pct: shared.ofWfSplitPct, of_wf_validation_metric: shared.ofWfValidationMetric,
      } : {}),
      // Boundary clipping
      clip_is: clipIS,
      // Optimizer engine
      optimizer_engine: optimizerEngine,
      // Bybit size gate
      ...(shared.bybitSizeGate !== "off" ? {
        bybit_size_gate: true,
        bybit_size_gate_max_reject_pct: shared.bybitSizeGate === "binary" ? 0 : shared.bybitSizeGateMaxPct,
      } : {}),
      // Harmony gate
      ...(harmonyConfig.enabled ? {
        harmony_enabled: true,
        harmony_strength_l15: harmonyConfig.strength_l15,
        harmony_strength_l25: harmonyConfig.strength_l25,
        ...(Object.keys(harmonyConfig.per_group).length > 0 ? {
          harmony_per_group: harmonyConfig.per_group,
        } : {}),
      } : {}),
      // Session Filter
      ...(shared.sessionFilterEnabled ? {
        session_filter_enabled: true,
        session_filter_mode: shared.sessionFilterMode,
        session_filter_exclude_days: shared.sessionFilterExcludeDays,
        session_filter_exclude_hours: shared.sessionFilterExcludeHours,
        session_filter_auto_min_trades: shared.sessionFilterAutoMinTrades,
        session_filter_auto_include_insufficient: shared.sessionFilterAutoIncludeInsufficient,
        session_filter_grade_on_cropped: shared.sessionFilterGradeOnCropped,
        ...(shared.sessionFilterMode === "saved_mask" && shared.sessionFilterSavedMaskId
          ? { session_mask_id: shared.sessionFilterSavedMaskId }
          : {}),
      } : {}),
    };

    // Capture current mode/settings for the closure
    const curMode = mode;
    const curBatchIds = batchIds;
    const curAutoS = { ...autoS };
    const curManualS = { ...manualS };
    const curDirS = { ...dirS };
    const curShared = { ...shared };
    const curPortfolioIds = [...portfolioIds];
    // Full snapshot — survives navigation via TaskManager
    const curSettingsSnapshot = {
      mode: curMode, autoS: curAutoS, manualS: curManualS, dirS: curDirS,
      shared: curShared, prioritizeStability, clipIS, clipOOS, oosEndDate,
      tcEnabled, tc: { ...tc }, tcLongs: { ...tcLongs }, tcShorts: { ...tcShorts },
      bmEnabled, bmConfig: { ...bmConfig }, categoryFilter: [...categoryFilter],
      excludePortfolioIds: Array.from(excludePortfolioIds), excludeMode, excludeKeepPct, excludeMethod,
      riskProfile: { ...riskProfile }, sizingMaster, tfConfigs: { ...tfConfigs },
      harmonyConfig: { ...harmonyConfig },
      affinityIncludeOos, affinityOos1Months, affinityOos2Months,
    };

    // Register with TaskManager — single request, survives navigation
    const fetchFn = async (taskProgress?: (pct: number, label: string) => void, onBackendTaskId?: (backendId: string) => void) => {
      const onProgress = (step: number, totalSteps: number, label: string) => {
        setOptimStep(step); setOptimTotal(totalSteps); setOptimLabel(label);
        // Also update TaskManager's top-level progress
        if (taskProgress) {
          const pct = totalSteps > 0 ? Math.round((step / totalSteps) * 100) : 0;
          taskProgress(pct, label);
        }
      };

      const buildResult: CrossBuildResult = curMode === "directional"
        ? await api.crossStrategyBuildDirectional({
          batch_ids: curBatchIds,
          l2_groups: curDirS.l2Groups,
          n_per_batch: curDirS.nPerBatch,
          obj_l1: curDirS.objL1, obj_l2: curDirS.objL2, obj_l3: curDirS.objL3,
          k_l1: curDirS.kL1, k_l2: curDirS.kL2, k_l3: curDirS.kL3,
          l1_quality_gate: curDirS.l1QualityGate,
          ...(curDirS.l1QualityGate ? { n_per_group: curDirS.nPerGroup } : {}),
          corr_method: curShared.corrMethod,
          ...sharedOpts,
        }, onProgress, onBackendTaskId)
        : curMode === "manual"
          ? await api.crossStrategyBuildManual({
            batch_ids: curBatchIds,
            allocations: curManualS.poolMode ? undefined : buildAllocations(),
            l2_groups: curManualS.l2Groups.length > 0 ? curManualS.l2Groups : undefined,
            obj_layer1: curManualS.objLayer1, obj_layer2: curManualS.objLayer2, obj_layer3: curManualS.objLayer3,
            candidates_per_strategy: curManualS.candidatesPerStrategy,
            n_final_strats: curManualS.nFinalStrats, n_portfolios: curManualS.nPortfolios,
            l1_quality_gate: curManualS.l1QualityGate,
            ...(curManualS.l1QualityGate ? { n_per_group: curManualS.nPerGroup } : {}),
            // Pool mode fields
            pool_mode: curManualS.poolMode,
            ...(curManualS.poolMode ? {
              pool_per_batch: curManualS.poolPerBatch,
              l2_n_assets: curManualS.l2NAssets,
              l2_n_variants: curManualS.l2NVariants,
              l2_max_overlap: curManualS.l2MaxOverlap,
              l3_max_overlap: curManualS.l3MaxOverlap,
              l2_symbol_dedup: curManualS.l2SymbolDedup,
              l2_dedup_tiebreaker: curManualS.l2DedupTiebreaker,
            } : {}),
            corr_method: curShared.corrMethod,
            ...sharedOpts,
          }, onProgress, onBackendTaskId)
          : await api.crossStrategyBuild(
            curBatchIds, curAutoS.nLongs, curAutoS.nShorts, curAutoS.withinObj, curAutoS.combinedObj,
            curShared.corrMethod, curAutoS.nPortfolios, {
            ...sharedOpts,
            split_mode: curAutoS.splitMode,
            ...(curAutoS.splitMode ? {} : { n_assets: curAutoS.nAssets, objective: curAutoS.combinedObj }),
            pool_per_batch: curAutoS.poolPerBatch > 0 ? curAutoS.poolPerBatch : undefined,
          }, onProgress, onBackendTaskId,
          );

      // Capture timing from build result for portfolio save
      if (buildResult.timing) setBuildTiming(buildResult.timing);

      // Correlation runs after build — failure is non-critical
      let corrResult: CorrelationResult | null = null;
      try {
        corrResult = await api.crossStrategyCorrelation(curBatchIds, curShared.corrMethod, curPortfolioIds, curShared.dateFrom || undefined, dateTo || undefined);
      } catch {
        // Correlation failed but build succeeded
      }

      return {
        candidates: buildResult.candidates,
        poolStats: null,
        corr: corrResult,
        extra: { strategies: buildResult.strategies, dateTo: dateTo, batchIds: curBatchIds, settingsSnapshot: curSettingsSnapshot },
      };
    };

    startOptimization(fetchFn, CS_TASK_LABEL);
  };

  // Sync local state from TaskManager when results arrive
  useEffect(() => {
    const task = latestOptimizationTask(CS_TASK_LABEL);
    if (!task) return;
    if (task.status === "completed" && task.result) {
      const rawCandidates = task.result.candidates;
      const cands = Array.isArray(rawCandidates) ? rawCandidates as CrossStrategyCandidate[] : [];
      setCandidates(cands);
      setCorr(task.result.corr);
      if (task.result.extra?.strategies) {
        setStrategies(task.result.extra.strategies as Record<number, string>);
      }
      // Restore batch selection from stored task result (survives navigation)
      if (task.result.extra?.batchIds && Array.isArray(task.result.extra.batchIds)) {
        const storedIds = task.result.extra.batchIds as number[];
        setSelectedIds((prev) => prev.size === 0 ? new Set(storedIds) : prev);
      }
      // Restore ALL optimizer settings from snapshot (survives navigation)
      // Only once per task to avoid re-applying on every render cycle
      if (task.result.extra?.settingsSnapshot && settingsRestoredForTaskRef.current !== task.id) {
        settingsRestoredForTaskRef.current = task.id;
        const snap = task.result.extra.settingsSnapshot as Record<string, unknown>;
        if (snap.mode) setMode(snap.mode as "auto" | "manual" | "directional");
        if (snap.autoS) setAutoS((prev) => ({ ...prev, ...(snap.autoS as Partial<AutoSettings>) }));
        if (snap.manualS) setManualS((prev) => ({ ...prev, ...(snap.manualS as Partial<ManualSettings>) }));
        if (snap.dirS) setDirS((prev) => ({ ...prev, ...(snap.dirS as Partial<DirectionalSettings>) }));
        if (snap.shared) setShared((prev) => ({ ...prev, ...(snap.shared as Partial<SharedSettings>) }));
        if (snap.prioritizeStability != null) setPrioritizeStability(snap.prioritizeStability as boolean);
        if (snap.clipIS != null) setClipIS(snap.clipIS as boolean);
        if (snap.clipOOS != null) setClipOOS(snap.clipOOS as boolean);
        if (snap.oosEndDate != null) { setOosEndDate(snap.oosEndDate as string); oosDateManualRef.current = true; }
        if (snap.tcEnabled != null) setTcEnabled(snap.tcEnabled as boolean);
        if (snap.tc) setTc(snap.tc as TCConstraints);
        if (snap.tcLongs) setTcLongs(snap.tcLongs as TCConstraints);
        if (snap.tcShorts) setTcShorts(snap.tcShorts as TCConstraints);
        if (snap.bmEnabled != null) setBmEnabled(snap.bmEnabled as boolean);
        if (snap.bmConfig) setBmConfig(snap.bmConfig as BenchmarkConfig);
        if (snap.categoryFilter) setCategoryFilter(snap.categoryFilter as string[]);
        if (snap.excludePortfolioIds) setExcludePortfolioIds(new Set(snap.excludePortfolioIds as number[]));
        if (snap.excludeMode) setExcludeMode(snap.excludeMode as ExcludeMode);
        if (snap.excludeKeepPct != null) setExcludeKeepPct(snap.excludeKeepPct as number);
        if (snap.excludeMethod) setExcludeMethod(snap.excludeMethod as ExcludeMethod);
        if (snap.riskProfile) setRiskProfile(snap.riskProfile as RiskProfileConfig);
        if (snap.sizingMaster) setSizingMaster(snap.sizingMaster as string);
        if (snap.tfConfigs) setTfConfigs(snap.tfConfigs as Record<string, RiskProfileConfig>);
        if (snap.harmonyConfig) setHarmonyConfig(snap.harmonyConfig as HarmonyConfig);
        if (snap.affinityIncludeOos != null) setAffinityIncludeOos(snap.affinityIncludeOos as boolean);
        if (snap.affinityOos1Months != null) setAffinityOos1Months(snap.affinityOos1Months as number);
        if (snap.affinityOos2Months != null) setAffinityOos2Months(snap.affinityOos2Months as number);
      }
      // Check which candidates are already saved (fingerprint match)
      const alreadySaved = new Set<number>();
      if (cands.length > 0 && existingFingerprints.size > 0) {
        cands.forEach((c, i) => {
          if (existingFingerprints.has(candidateFingerprint(c.assets))) alreadySaved.add(i);
        });
      }
      // Always set — clears stale "Saved" badges when no fingerprints match
      setSavedIndices(alreadySaved);
      setSavedPortfolioIds(new Map());
      setLoading(false);
    }
    if (task.status === "error") {
      setError(task.error ?? "Build failed");
      setLoading(false);
    }
  }, [latestOptimizationTask, existingFingerprints]);

  // Fire affinity report automatically when candidates arrive
  const affinityFiredRef = useRef<string | null>(null);
  useEffect(() => {
    if (candidates.length < 1 || batchIds.length < 2) return;
    const key = `${batchIds.join(",")}-${candidates.length}`;
    if (affinityFiredRef.current === key) return;
    affinityFiredRef.current = key;
    runAffinityReport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates, batchIds]);

  const runAffinityReport = async () => {
    if (batchIds.length < 2) return;
    setAffinityLoading(true);
    setAffinityReport(null);
    try {
      const report = await api.affinityReport({
        batch_ids: batchIds,
        portfolio_ids: portfolioIds.length > 0 ? portfolioIds : undefined,
        date_from: shared.dateFrom || undefined,
        date_to: dateTo || undefined,
        clip_is: clipIS,
        include_oos: affinityIncludeOos,
        oos1_months: affinityOos1Months,
        oos2_months: affinityOos2Months,
        pinned_anchors: affinityPinnedAnchors.size > 0 ? Array.from(affinityPinnedAnchors) : undefined,
      });
      setAffinityReport(report);
    } catch (e) {
      console.error("Affinity report failed:", e);
    } finally {
      setAffinityLoading(false);
    }
  };

  const applySmartGroupPreset = (preset: "conservative" | "balanced" | "aggressive") => {
    setSgPreset(preset);
    if (preset === "conservative") {
      setSgHoldoutRatio(0.20); setSgHoldoutWeight(0.5);
      setSgBootstrapEnabled(true); setSgBootstrapN(100); setSgBootstrapDrop(0.15);
      setSgRegimeRefDays(30); setSgRegimeBandwidth(0.0);
      setSgTrendStrength("medium"); setSgTrendLookback(20); setSgTrendRegimePct(0.70);
    } else if (preset === "balanced") {
      setSgHoldoutRatio(0.15); setSgHoldoutWeight(0.4);
      setSgBootstrapEnabled(false); setSgBootstrapN(100); setSgBootstrapDrop(0.15);
      setSgRegimeRefDays(0); setSgRegimeBandwidth(0.0);
      setSgTrendStrength("small"); setSgTrendLookback(20); setSgTrendRegimePct(0.65);
    } else {
      setSgHoldoutRatio(0); setSgHoldoutWeight(0);
      setSgBootstrapEnabled(false); setSgBootstrapN(100); setSgBootstrapDrop(0.15);
      setSgRegimeRefDays(0); setSgRegimeBandwidth(0.0);
      setSgTrendStrength("none"); setSgTrendLookback(20); setSgTrendRegimePct(0.65);
    }
  };

  const runSmartGroups = async () => {
    if (batchIds.length < 2) return;
    setSmartGroupLoading(true);
    setSmartGroupResult(null);
    setSmartGroupApplied(false);
    try {
      const result = await api.smartGroups({
        batch_ids: batchIds,
        date_from: shared.dateFrom || undefined,
        date_to: dateTo || undefined,
        clip_is: clipIS,
        // Pass the same IS filters the optimizer uses
        min_pf: shared.minPf || undefined,
        min_wr: shared.minWr || undefined,
        min_trades: shared.minTrades || undefined,
        // V2 enhancements
        holdout_ratio: sgHoldoutRatio,
        holdout_weight: sgHoldoutWeight,
        skip_bootstrap: !sgBootstrapEnabled,
        bootstrap_n: sgBootstrapN,
        bootstrap_drop_pct: sgBootstrapDrop,
        regime_ref_days: sgRegimeRefDays,
        regime_bandwidth: sgRegimeBandwidth,
        trend_strength: sgTrendStrength,
        trend_lookback: sgTrendLookback,
        trend_regime_pct: sgTrendRegimePct,
      });
      setSmartGroupResult(result);
      // Auto-apply: set L2 groups for current mode
      if (result.l2_groups.length > 0) {
        if (mode === "manual") {
          setManualS((s) => ({ ...s, l2Groups: result.l2_groups }));
        } else if (mode === "directional") {
          setDirS((s) => ({ ...s, l2Groups: result.l2_groups }));
        }
        const cvNote = result.cv_stability?.n_folds
          ? ` | CV: ${result.cv_stability.stable_selections?.length ?? "?"} stable across ${result.cv_stability.n_folds} folds`
          : "";
        addToast(
          `Smart Groups: ${result.families.length} families, ${result.recommended_ids.length} recommended, ${result.drop_ids.length} redundant${cvNote}`,
          "info"
        );
      }
    } catch (e) {
      console.error("Smart groups failed:", e);
      addToast("Smart Groups failed — check console", "error");
    } finally {
      setSmartGroupLoading(false);
    }
  };

  const saveCandidate = async (idx: number, c: CrossStrategyCandidate) => {
    setSaving(idx);
    try {
      // Extract batch_id from tag (BTCUSDT~B42_Short → batch_id=42) then strip
      const extractBatchId = (s: string): number | undefined => {
        const m = s.match(/~B(-?\d+)/);
        return m ? parseInt(m[1], 10) : undefined;
      };
      const stripTag = (s: string) => s.replace(/~B-?\d+/g, "");
      const assets = c.assets.map((s) => {
        const batchId = extractBatchId(s);
        const clean = stripTag(s);
        const side = clean.endsWith("_Long") ? "Long" : clean.endsWith("_Short") ? "Short" : "both";
        const symbol = clean.replace(/_Long$/, "").replace(/_Short$/, "");
        // Attach auto-risk mode if available (try composite key first, then plain symbol)
        const riskMode = autoRiskEnabled && autoRiskResult?.per_asset
          ? (autoRiskResult.per_asset[`${symbol}__batch${batchId}`]?.mode
             ?? autoRiskResult.per_asset[symbol]?.mode)
          : undefined;
        return { symbol, side, strategy_name: c.attribution[s] || "", batch_id: batchId, risk_mode: riskMode };
      });
      const pIds = portfolioIds.length > 0 ? portfolioIds : undefined;
      // Shared save settings — extracted so all 3 modes stay in sync
      const sharedSave = {
        batch_ids: batchIds, portfolio_ids: pIds, is_cross_strat: true,
        corr_method: shared.corrMethod, div_algo: shared.divAlgo,
        ...(shared.divAlgo === "genetic" ? {
          genetic_generations: shared.geneticGenerations, genetic_population: shared.geneticPopulation,
          genetic_mutation_rate: shared.geneticMutationRate, genetic_crossover_rate: shared.geneticCrossoverRate,
          genetic_elite_pct: shared.geneticElitePct,
        } : {}),
        seed_metrics: shared.seedMetrics, max_seeds: shared.maxSeeds,
        prioritize_stability: prioritizeStability, per_batch: buildPerBatch(),
        date_from: shared.dateFrom || undefined, date_to: dateTo || undefined, oos_end_date: oosEndDate || undefined,
        min_trades: shared.minTrades, min_pf: shared.minPf, min_wr: shared.minWr,
        ...(shared.maxAssetPf > 0 ? { max_asset_pf: shared.maxAssetPf, cap_asset_pf: shared.capAssetPf } : {}),
        ...(shared.portfolioMinPf > 0 || shared.portfolioMinWr > 0 ? {
          portfolio_min_pf: shared.portfolioMinPf,
          portfolio_min_wr: shared.portfolioMinWr,
        } : {}),
        extra_slippage_pct: shared.extraSlippage,
        extra_commission_pct: shared.extraCommission,
        funding_daily_pct: shared.fundingDaily,
        spread_pct: shared.spread,
        streamlit_logic: shared.streamlitLogic,
        expand_greedy_pool: shared.expandGreedyPool,
        seed_blind_selection: shared.seedBlindSelection,
        clip_is: clipIS,
        ...(shared.seedPriorWeight > 0 ? { seed_prior_weight: shared.seedPriorWeight } : {}),
        ...(Object.keys(shared.seedWeights).length > 0 ? { seed_weights: shared.seedWeights } : {}),
        ...(tcEnabled ? {
          tc_enabled: true, tc_low_threshold: tc.low_threshold, tc_max_low: tc.max_low,
          tc_high_threshold: tc.high_threshold, tc_min_high: tc.min_high,
        } : {}),
        ...(shared.recencyEnabled ? {
          recency_enabled: true, recency_mode: shared.recencyMode,
          recency_batch_size: shared.recencyBatchSize, recency_boost_factor: shared.recencyBoostFactor,
        } : {}),
        ...(bmEnabled ? {
          benchmark_enabled: true, benchmark_source: bmConfig.source,
          benchmark_split: bmConfig.split, benchmark_threshold: bmConfig.threshold,
          benchmark_elite_target: bmConfig.elite_target, benchmark_exclude_fragile: bmConfig.exclude_fragile,
          benchmark_consistency_blend: bmConfig.consistency_blend,
          benchmark_fragility_blend: bmConfig.fragility_blend,
          benchmark_wr_harmony_blend: bmConfig.wr_harmony_blend,
          ...(bmConfig.dsr_gate ? { benchmark_dsr_gate: true } : {}),
        } : {}),
        ...(harmonyConfig.enabled ? {
          harmony_enabled: true,
          harmony_strength_l15: harmonyConfig.strength_l15,
          harmony_strength_l25: harmonyConfig.strength_l25,
          ...(Object.keys(harmonyConfig.per_group).length > 0 ? { harmony_per_group: harmonyConfig.per_group } : {}),
        } : {}),
        ...(categoryFilter.length > 0 ? { category_filter: categoryFilter } : {}),
        ...(excludePortfolioIds.size > 0 ? {
          exclude_portfolio_ids: Array.from(excludePortfolioIds),
          exclude_portfolio_mode: excludeMode,
          ...(excludeMode === "partial" ? {
            exclude_portfolio_keep_pct: excludeKeepPct,
            exclude_portfolio_method: excludeMethod,
          } : {}),
        } : {}),
        ...(shared.maxOverlap !== "off" ? { max_overlap: parseFloat(shared.maxOverlap) } : {}),
        ...(sizingMaster === "per_tf" ? buildPerTfPayload()
          : (riskProfile.mode !== "fixed" || riskProfile.value > 0) ? {
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
          risk_profile_kelly_scope: riskProfile.kellyScope,
          risk_profile_kelly_lookback: riskProfile.kellyLookback,
          risk_profile_kelly_cap: riskProfile.kellyCap,
          ...(riskProfile.applyGroupMultiplier ? { risk_profile_apply_group_multiplier: true } : {}),
        } : {}),
        // Auto-risk per-asset configs
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
        ...(shared.prefilterEnabled ? {
          prefilter_enabled: true, prefilter_mode: shared.prefilterMode,
          prefilter_hurst_min: shared.prefilterHurstMin, prefilter_ter_percentile: shared.prefilterTerPercentile,
          prefilter_hurst_max: shared.prefilterHurstMax, prefilter_vr_max: shared.prefilterVrMax,
          prefilter_min_crossing_rate: shared.prefilterMinCrossingRate,
        } : {}),
        // AC post-filter
        ...(shared.acPostfilter ? {
          ac_postfilter: true, ac_postfilter_type: shared.acPostfilterType, ac_lag: shared.acLag,
        } : {}),
        // Avg range seed config
        ...(shared.seedMetrics.includes("avg_range") ? {
          avg_range_bull_threshold: shared.avgRangeBullThreshold,
          avg_range_bear_threshold: shared.avgRangeBearThreshold,
          avg_range_all_threshold: shared.avgRangeAllThreshold,
          avg_range_filter_mode: shared.avgRangeFilterMode,
        } : {}),
        // Asset quality seed
        ...(shared.seedMetrics.includes("asset_quality") ? {
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
        } : {}),
        // Strategy quality seed
        ...(shared.seedMetrics.includes("strat_quality") ? {
          strat_quality_mode: shared.stratQualityMode,
          strat_quality_min_grade: shared.stratQualityMinGrade,
          strat_quality_filter: shared.stratQualityFilter,
        } : {}),
        // IS-WFE seed
        ...(shared.seedMetrics.includes("is_wfe") ? {
          is_wfe_is_pct: shared.isWfeIsPct,
          is_wfe_min_trades: shared.isWfeMinTrades,
          is_wfe_filter: shared.isWfeFilter,
          is_wfe_min_wfe: shared.isWfeMinWfe,
        } : {}),
        // Correlation jitter
        ...(shared.corrJitterEnabled ? {
          corr_jitter_enabled: true,
          corr_jitter_temperature: shared.corrJitterTemperature,
        } : {}),
        // Post-construction refinement
        ...(shared.refinementEnabled ? {
          refinement_enabled: true,
          refinement_mode: shared.refinementMode,
          ...(shared.refinementMetric ? { refinement_metric: shared.refinementMetric } : {}),
        } : {}),
        // Overfitting Guard
        ...(shared.overfitGuardEnabled ? {
          overfit_guard_enabled: true,
          of_log_enabled: shared.ofLogEnabled,
          of_log_cap_pf: shared.ofLogCapPf, of_log_cap_sortino: shared.ofLogCapSortino,
          of_log_cap_payoff: shared.ofLogCapPayoff, of_log_cap_wr: shared.ofLogCapWr,
          of_log_cap_sharpe: shared.ofLogCapSharpe, of_log_cap_calmar: shared.ofLogCapCalmar,
          of_z_score_enabled: shared.ofZScoreEnabled,
          of_z_threshold: shared.ofZThreshold, of_z_penalty_strength: shared.ofZPenaltyStrength,
          of_bayes_enabled: shared.ofBayesEnabled, of_bayes_k: shared.ofBayesK,
          of_sigmoid_enabled: shared.ofSigmoidEnabled,
          of_sig_cap_pf: shared.ofSigCapPf, of_sig_cap_sortino: shared.ofSigCapSortino,
          of_sig_cap_payoff: shared.ofSigCapPayoff, of_sig_cap_wr: shared.ofSigCapWr,
          of_sig_cap_sharpe: shared.ofSigCapSharpe, of_sig_cap_calmar: shared.ofSigCapCalmar,
          of_normal_range_enabled: shared.ofNormalRangeEnabled,
          of_nr_pct_low: shared.ofNrPctLow, of_nr_pct_high: shared.ofNrPctHigh,
          of_nr_penalty_scale: shared.ofNrPenaltyScale, of_nr_bonus: shared.ofNrBonus,
          of_wf_rerank_enabled: shared.ofWfRerankEnabled,
          of_wf_split_pct: shared.ofWfSplitPct, of_wf_validation_metric: shared.ofWfValidationMetric,
        } : {}),
        // Bybit size gate
        ...(shared.bybitSizeGate !== "off" ? {
          bybit_size_gate: true,
          bybit_size_gate_max_reject_pct: shared.bybitSizeGate === "binary" ? 0 : shared.bybitSizeGateMaxPct,
        } : {}),
        // Session Filter
        ...(shared.sessionFilterEnabled ? {
          session_filter_enabled: true,
          session_filter_mode: shared.sessionFilterMode,
          session_filter_exclude_days: shared.sessionFilterExcludeDays,
          session_filter_exclude_hours: shared.sessionFilterExcludeHours,
          session_filter_auto_min_trades: shared.sessionFilterAutoMinTrades,
          session_filter_auto_include_insufficient: shared.sessionFilterAutoIncludeInsufficient,
          session_filter_grade_on_cropped: shared.sessionFilterGradeOnCropped,
          ...(shared.sessionFilterMode === "saved_mask" && shared.sessionFilterSavedMaskId
            ? { session_mask_id: shared.sessionFilterSavedMaskId }
            : {}),
        } : {}),
      };
      const settings = mode === "directional"
        ? {
          ...sharedSave, mode: "directional",
          l2_groups: dirS.l2Groups,
          n_per_batch: dirS.nPerBatch,
          obj_l1: dirS.objL1, obj_l2: dirS.objL2, obj_l3: dirS.objL3,
          k_l1: dirS.kL1, k_l2: dirS.kL2, k_l3: dirS.kL3,
          l1_quality_gate: dirS.l1QualityGate,
          ...(dirS.l1QualityGate ? { n_per_group: dirS.nPerGroup } : {}),
        }
        : mode === "manual"
          ? {
            ...sharedSave, mode: "manual", allocations: manualS.poolMode ? undefined : buildAllocations(),
            obj_layer1: manualS.objLayer1, obj_layer2: manualS.objLayer2, obj_layer3: manualS.objLayer3,
            candidates_per_strategy: manualS.candidatesPerStrategy, n_final_strats: manualS.nFinalStrats,
            n_portfolios: manualS.nPortfolios,
            l1_quality_gate: manualS.l1QualityGate,
            ...(manualS.l1QualityGate ? { n_per_group: manualS.nPerGroup } : {}),
            ...(manualS.l2Groups.length > 0 ? { l2_groups: manualS.l2Groups } : {}),
            pool_mode: manualS.poolMode,
            ...(manualS.poolMode ? {
              pool_per_batch: manualS.poolPerBatch, l2_n_assets: manualS.l2NAssets,
              l2_n_variants: manualS.l2NVariants, l2_max_overlap: manualS.l2MaxOverlap,
              l3_max_overlap: manualS.l3MaxOverlap,
              l2_symbol_dedup: manualS.l2SymbolDedup,
              l2_dedup_tiebreaker: manualS.l2DedupTiebreaker,
            } : {}),
          }
          : {
            ...sharedSave, mode: "auto",
            n_longs: autoS.nLongs, n_shorts: autoS.nShorts,
            within_objective: autoS.withinObj, combined_objective: autoS.combinedObj,
            split_mode: autoS.splitMode,
            ...(autoS.splitMode ? {} : { n_assets: autoS.nAssets }),
            ...(autoS.poolPerBatch > 0 ? { pool_per_batch: autoS.poolPerBatch } : {}),
            n_portfolios: autoS.nPortfolios,
          };

      // Build descriptive portfolio name
      const now = new Date();
      const dateStr = `${String(now.getMonth() + 1).padStart(2, "0")}/${String(now.getDate()).padStart(2, "0")}`;
      const timeStr = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      const obj = mode === "directional" ? dirS.objL2
        : mode === "manual" ? manualS.objLayer2
        : autoS.combinedObj;
      const algo = shared.divAlgo;
      const oosSummary = oosSummaries.get(idx);
      const oosStr = oosSummary ? ` OOS$${Math.round(oosSummary.oosProfit).toLocaleString()}` : "";
      const gradeStr = oosSummary?.grade ? ` ${oosSummary.grade}` : "";
      const savedPortfolio = await api.savePortfolio(
        `C-S ${dateStr} ${timeStr} ${obj} ${algo}${oosStr}${gradeStr} #${idx + 1}`,
        assets,
        undefined, // no single batch_id for cross-strategy
        settings,
        c.metrics as Record<string, number | null>,
        {
          batch_ids: batchIds,
          portfolio_source: "cross_strategy",
          optimization_end_date: dateTo || undefined,
          is_start_date: shared.dateFrom || undefined,
          ...(buildTiming ? { optimization_timing: buildTiming } : {}),
        },
      );
      setSavedIndices((prev) => new Set(prev).add(idx));
      if (savedPortfolio?.id) {
        setSavedPortfolioIds((prev) => new Map(prev).set(idx, savedPortfolio.id));
      }
      // Refresh fingerprints so other pages/candidates also see this save
      api.portfolios().then((list) => {
        setPortfolios(list);
        refreshFingerprints(list);
      }).catch(() => { });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Save failed";
      alert(`Save error: ${msg}`);
    }
    setSaving(null);
  };

  return (
    <div className="space-y-4">
      {/* Config save/load */}
      <OptimizerConfigManager storageKey="optimizer_cross_configs" currentConfig={currentConfig} onLoad={loadConfig} defaultName={buildDefaultName()} />

      <p className="text-sm text-ag-text-muted">
        Select 2+ data sources (batches and/or saved portfolios) to build a combined portfolio.
      </p>

      {/* Batch selector */}
      <div className="bg-ag-card border border-ag-border rounded-lg p-3 space-y-3">
        <div>
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs text-ag-text-muted uppercase tracking-wider">Select Batches</h4>
            <select value={batchSort} onChange={(e) => setBatchSort(e.target.value as typeof batchSort)}
              className="text-[10px] bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-ag-text-muted">
              <option value="name">Sort: Name</option>
              <option value="personal_grade">Sort: My Grade</option>
              <option value="grade">Sort: Computed Grade</option>
              <option value="pf">Sort: Profit Factor</option>
              <option value="pnl">Sort: Net Profit</option>
            </select>
          </div>
          <div className="max-h-52 overflow-y-auto space-y-3">
            {(() => {
              // Grade ordering for sorting
              const gradeOrder: Record<string, number> = {
                "A+": 1, "A": 2, "A-": 3, "B+": 4, "B": 5, "B-": 6,
                "C+": 7, "C": 8, "C-": 9, "D+": 10, "D": 11, "D-": 12, "F": 13,
              };
              const sortBatches = (items: BatchBacktestRunItem[]) => {
                const sorted = [...items];
                sorted.sort((a, z) => {
                  if (batchSort === "personal_grade") {
                    const ag = gradeOrder[a.personal_grade || ""] ?? 99;
                    const zg = gradeOrder[z.personal_grade || ""] ?? 99;
                    return ag - zg;
                  }
                  if (batchSort === "grade") {
                    const ag = gradeOrder[a.batch_grade || ""] ?? 99;
                    const zg = gradeOrder[z.batch_grade || ""] ?? 99;
                    return ag - zg;
                  }
                  if (batchSort === "pf") return (z.avg_profit_factor ?? 0) - (a.avg_profit_factor ?? 0);
                  if (batchSort === "pnl") return (z.total_net_profit ?? 0) - (a.total_net_profit ?? 0);
                  return (a.name || "").localeCompare(z.name || "");
                });
                return sorted;
              };
              // Group batches by TF → Direction
              const groups = new Map<string, Map<string, BatchBacktestRunItem[]>>();
              for (const b of batches) {
                const tf = b.timeframe || "?";
                const dir = b.direction_filter || "both";
                if (!groups.has(tf)) groups.set(tf, new Map());
                const dirMap = groups.get(tf)!;
                if (!dirMap.has(dir)) dirMap.set(dir, []);
                dirMap.get(dir)!.push(b);
              }
              return Array.from(groups.entries()).map(([tf, dirMap]) =>
                Array.from(dirMap.entries()).map(([dir, items]) => (
                  <div key={`${tf}-${dir}`}>
                    <p className="text-[10px] text-ag-text-muted font-semibold uppercase tracking-wider px-2 mb-1 border-b border-ag-border/50 pb-1">
                      {tf}m · {dir.charAt(0).toUpperCase() + dir.slice(1)}
                    </p>
                    <div className="grid grid-cols-1 gap-1">
                      {sortBatches(items).map((b) => (
                        <BatchSelectorRow key={b.id} b={b} selected={selectedIds.has(b.id)}
                          onToggle={() => toggleBatch(b.id)}
                          editingId={editingBatchId} onEditStart={(id) => setEditingBatchId(id)}
                          onEditDone={(updated) => {
                            setBatches(prev => prev.map(x => x.id === updated.id ? { ...x, ...updated } : x));
                            setEditingBatchId(null);
                          }}
                          onEditCancel={() => setEditingBatchId(null)} />
                      ))}
                    </div>
                  </div>
                ))
              ).flat(2);
            })()}
          </div>
          {batches.length === 0 && <p className="text-xs text-ag-text-muted">No completed batch backtests.</p>}
        </div>

        {/* Saved portfolio selector */}
        <PortfolioSelector
          portfolios={portfolios}
          selectedIds={selectedPortfolioIds}
          onToggle={togglePortfolio}
        />

        {totalSources > 0 && (
          <p className="text-xs text-ag-text-muted">
            {totalSources} source{totalSources !== 1 ? "s" : ""} selected
            ({batchIds.length} batch{batchIds.length !== 1 ? "es" : ""}
            {portfolioIds.length > 0 ? `, ${portfolioIds.length} portfolio${portfolioIds.length !== 1 ? "s" : ""}` : ""})
          </p>
        )}
      </div>

      {/* Smart Groups — auto-detect families & assign L2 groups */}
      {totalSources >= 2 && (
        <div className="bg-ag-card border border-ag-border rounded-lg px-3 py-2">
          <div className="flex items-center gap-3 text-xs">
            <button onClick={runSmartGroups} disabled={smartGroupLoading || batchIds.length < 2}
              className="px-3 py-1.5 rounded bg-purple-500/15 text-purple-400 hover:bg-purple-500/25 disabled:opacity-50 transition-colors font-medium whitespace-nowrap">
              {smartGroupLoading ? "Detecting…" : "Auto Smart Groups"}
            </button>
            <span className="text-ag-text-muted">
              Auto-detect strategy families, pick best composition, assign L2 groups
            </span>
            <HintTip text="Step 1: Choose a preset (or tweak Advanced). Step 2: Click 'Auto Smart Groups' to analyze (IS data ONLY). Step 3: Review families, greedy Sharpe, bootstrap stability, trend regime. Step 4: Click 'Apply to Build'. Uses concentration penalty, IS holdout validation, trend tilt, and optional bootstrap." />
            {smartGroupResult && (
              <>
                {!smartGroupApplied ? (
                  <button onClick={() => {
                    setPreSmartGroupIds(new Set(selectedIds));
                    const keepSet = new Set(smartGroupResult!.recommended_ids);
                    setSelectedIds((prev) => {
                      const next = new Set<number>();
                      prev.forEach((id) => { if (keepSet.has(id)) next.add(id); });
                      return next;
                    });
                    if (mode === "manual") {
                      setManualS((s) => ({ ...s, l2Groups: smartGroupResult!.l2_groups }));
                    } else if (mode === "directional") {
                      setDirS((s) => ({ ...s, l2Groups: smartGroupResult!.l2_groups }));
                    }
                    setSmartGroupApplied(true);
                    const removed = smartGroupResult!.drop_ids.length;
                    addToast(
                      `Smart Groups applied — ${removed} redundant batch${removed !== 1 ? "es" : ""} deselected` +
                      (mode === "manual" || mode === "directional" ? ", L2 groups assigned" : ""),
                      "info"
                    );
                  }} className="px-2 py-0.5 rounded bg-green-500/15 text-green-400 hover:bg-green-500/25 text-[10px] font-medium whitespace-nowrap">
                    Apply to Build
                  </button>
                ) : (
                  <span className="px-2 py-0.5 rounded bg-green-500/10 text-green-400 text-[10px] font-medium">
                    Applied
                  </span>
                )}
                <button onClick={() => {
                  if (smartGroupApplied && preSmartGroupIds) {
                    setSelectedIds(preSmartGroupIds);
                  }
                  setPreSmartGroupIds(null);
                  setSmartGroupResult(null);
                  setSmartGroupApplied(false);
                  if (mode === "manual") setManualS((s) => ({ ...s, l2Groups: [] }));
                  else if (mode === "directional") setDirS((s) => ({ ...s, l2Groups: [] }));
                  addToast("Smart Groups cleared — batch selection restored", "info");
                }} className="px-2 py-0.5 rounded bg-red-500/10 text-red-400 hover:bg-red-500/20 text-[10px] whitespace-nowrap">
                  Clear
                </button>
                <span className="ml-auto text-[10px] text-ag-text-muted">
                  {smartGroupResult.families.length} families · {smartGroupResult.recommended_ids.length} recommended · {smartGroupResult.drop_ids.length} redundant
                  {smartGroupApplied && " · ✓ applied"}
                </span>
              </>
            )}
          </div>
          {/* V2: Presets + Advanced settings */}
          <div className="flex items-center gap-2 mt-1.5 text-[10px]">
            <span className="text-ag-text-muted">Preset:</span>
            {(["conservative", "balanced", "aggressive"] as const).map((p) => (
              <button key={p} onClick={() => applySmartGroupPreset(p)}
                className={`px-2 py-0.5 rounded capitalize transition-colors ${sgPreset === p
                  ? "bg-purple-500/25 text-purple-300 font-medium"
                  : "bg-ag-dark/50 text-ag-text-muted hover:text-ag-text hover:bg-ag-dark"}`}>
                {p}
              </button>
            ))}
            <button onClick={() => setSgAdvanced(!sgAdvanced)}
              className={`ml-2 px-2 py-0.5 rounded transition-colors ${sgAdvanced
                ? "bg-amber-500/20 text-amber-400" : "bg-ag-dark/50 text-ag-text-muted hover:text-ag-text"}`}>
              {sgAdvanced ? "Hide Advanced" : "Advanced"}
            </button>
            <HintTip text="Conservative: strictest guardrails (20% holdout, bootstrap on, regime families, medium trend tilt). Balanced: moderate (15% holdout, no bootstrap, small tilt). Aggressive: minimal guardrails (closest to V1 behavior)." />
          </div>
          {sgAdvanced && (
            <div className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[10px] bg-ag-dark/30 rounded p-2 border border-ag-border/30">
              {/* IS Holdout */}
              <div className="col-span-2 text-ag-text-muted font-medium border-b border-ag-border/20 pb-0.5">IS Holdout Validation</div>
              <label className="flex items-center gap-2">
                <span className="text-ag-text-muted w-24">Holdout Ratio</span>
                <input type="range" min={0} max={0.30} step={0.05} value={sgHoldoutRatio}
                  onChange={(e) => { setSgHoldoutRatio(Number(e.target.value)); setSgPreset("balanced"); }}
                  className="flex-1 accent-purple-400 h-1" />
                <span className="w-10 text-right">{Math.round(sgHoldoutRatio * 100)}%</span>
                <HintTip text="Fraction of IS data held out for validation. 0% = disabled. The greedy selection optimizes on train only; holdout checks if batches help on unseen IS data." />
              </label>
              <label className="flex items-center gap-2">
                <span className="text-ag-text-muted w-24">Holdout Weight</span>
                <input type="range" min={0} max={1} step={0.1} value={sgHoldoutWeight}
                  onChange={(e) => { setSgHoldoutWeight(Number(e.target.value)); setSgPreset("balanced"); }}
                  className="flex-1 accent-purple-400 h-1" />
                <span className="w-10 text-right">{sgHoldoutWeight.toFixed(1)}</span>
                <HintTip text="How much holdout performance weighs in the net marginal. Higher = more conservative (batches must help on holdout too)." />
              </label>
              {/* Bootstrap */}
              <div className="col-span-2 text-ag-text-muted font-medium border-b border-ag-border/20 pb-0.5 mt-1">Bootstrap Stability</div>
              <label className="flex items-center gap-2">
                <span className="text-ag-text-muted w-24">Enable</span>
                <input type="checkbox" checked={sgBootstrapEnabled}
                  onChange={(e) => { setSgBootstrapEnabled(e.target.checked); setSgPreset("balanced"); }}
                  className="accent-purple-400" />
                <span className="text-ag-text-muted">{sgBootstrapEnabled ? "On (slower)" : "Off"}</span>
                <HintTip text="Randomly drops 15-20% of trades N times and re-runs greedy selection. Measures how stable each batch's selection is. Compute-intensive." />
              </label>
              {sgBootstrapEnabled && (
                <>
                  <label className="flex items-center gap-2">
                    <span className="text-ag-text-muted w-24">Iterations</span>
                    <input type="number" min={50} max={200} step={25} value={sgBootstrapN}
                      onChange={(e) => setSgBootstrapN(Number(e.target.value))}
                      className="w-16 bg-ag-dark border border-ag-border/50 rounded px-1 py-0.5 text-center" />
                    <HintTip text="Number of bootstrap iterations. More = more accurate stability scores but slower." />
                  </label>
                  <label className="flex items-center gap-2">
                    <span className="text-ag-text-muted w-24">Drop %</span>
                    <input type="range" min={0.10} max={0.25} step={0.05} value={sgBootstrapDrop}
                      onChange={(e) => setSgBootstrapDrop(Number(e.target.value))}
                      className="flex-1 accent-purple-400 h-1" />
                    <span className="w-10 text-right">{Math.round(sgBootstrapDrop * 100)}%</span>
                    <HintTip text="Fraction of trades dropped per iteration. Higher = more aggressive test of robustness." />
                  </label>
                </>
              )}
              {/* Family Detection */}
              <div className="col-span-2 text-ag-text-muted font-medium border-b border-ag-border/20 pb-0.5 mt-1">Family Detection</div>
              <label className="flex items-center gap-2">
                <span className="text-ag-text-muted w-24">Regime Ref Days</span>
                <input type="number" min={0} max={60} step={5} value={sgRegimeRefDays}
                  onChange={(e) => { setSgRegimeRefDays(Number(e.target.value)); setSgPreset("balanced"); }}
                  className="w-16 bg-ag-dark border border-ag-border/50 rounded px-1 py-0.5 text-center" />
                <span className="text-ag-text-muted">{sgRegimeRefDays === 0 ? "(off — use median)" : "days"}</span>
                <HintTip text="0 = use standard median correlation (V1). >0 = weight correlation windows by vol similarity to the last N days of IS. Families stable under current-like conditions are preserved." />
              </label>
              <label className="flex items-center gap-2">
                <span className="text-ag-text-muted w-24">Bandwidth</span>
                <input type="number" min={0} max={5} step={0.5} value={sgRegimeBandwidth}
                  onChange={(e) => { setSgRegimeBandwidth(Number(e.target.value)); setSgPreset("balanced"); }}
                  className="w-16 bg-ag-dark border border-ag-border/50 rounded px-1 py-0.5 text-center" />
                <span className="text-ag-text-muted">{sgRegimeBandwidth === 0 ? "(auto)" : ""}</span>
                <HintTip text="Gaussian kernel bandwidth for vol-weighting. 0 = auto (1 std of vol distribution). Manual value = custom bandwidth." />
              </label>
              {/* Trend Direction Tilt */}
              <div className="col-span-2 text-ag-text-muted font-medium border-b border-ag-border/20 pb-0.5 mt-1">Trend Direction Tilt</div>
              <label className="flex items-center gap-2">
                <span className="text-ag-text-muted w-24">Past Ranges</span>
                <input type="number" min={5} max={50} step={5} value={sgTrendLookback}
                  onChange={(e) => { setSgTrendLookback(Number(e.target.value)); setSgPreset("balanced"); }}
                  className="w-16 bg-ag-dark border border-ag-border/50 rounded px-1 py-0.5 text-center" />
                <HintTip text="How many recent close-to-close direction streaks (ranges) to examine across all symbols to determine the market regime." />
              </label>
              <label className="flex items-center gap-2">
                <span className="text-ag-text-muted w-24">Regime Threshold</span>
                <input type="range" min={0.50} max={0.90} step={0.05} value={sgTrendRegimePct}
                  onChange={(e) => { setSgTrendRegimePct(Number(e.target.value)); setSgPreset("balanced"); }}
                  className="flex-1 accent-purple-400 h-1" />
                <span className="w-10 text-right">{Math.round(sgTrendRegimePct * 100)}%</span>
                <HintTip text="What % of recent ranges must be bullish (or bearish) to declare a directional regime. Higher = harder to trigger a tilt." />
              </label>
              <label className="flex items-center gap-2 col-span-2">
                <span className="text-ag-text-muted w-24">Promotion</span>
                <select value={sgTrendStrength}
                  onChange={(e) => { setSgTrendStrength(e.target.value); setSgPreset("balanced"); }}
                  className="bg-ag-dark border border-ag-border/50 rounded px-1.5 py-0.5 text-[10px]">
                  <option value="none">None (1.0x)</option>
                  <option value="small">Small (1.05x)</option>
                  <option value="medium">Medium (1.10x)</option>
                  <option value="high">High (1.20x)</option>
                  <option value="extreme">Extreme (1.35x)</option>
                </select>
                <HintTip text="How much to boost direction-aligned batches during greedy selection. If regime is bullish, Long batches get this boost. Short batches are never penalized, just not boosted." />
              </label>
            </div>
          )}
          {smartGroupResult && smartGroupResult.families.length > 0 && (
            <div className="mt-2 pt-2 border-t border-ag-border/50 space-y-2 text-[11px]">
              {smartGroupResult.greedy_order.length > 0 && (
                <div>
                  <span className="text-ag-text-muted font-medium">
                    Greedy Deflated Sharpe Selection
                    <HintTip text="IS Sharpe is deflated ~35% to approximate OOS performance. Batches with fewer trades get extra penalty. Starts with highest deflated-Sharpe batch, greedily adds batches improving portfolio Sharpe. Uses IS-filtered asset curves (your min PF/WR/trades filters applied)." />
                  </span>
                  <div className="mt-1 space-y-0.5">
                    {smartGroupResult.greedy_order.map((e, i) => {
                      const name = smartGroupResult.batch_names[String(e.batch_id)] || `#${e.batch_id}`;
                      const hasRaw = e.sharpe_raw !== undefined && e.sharpe_raw !== null;
                      const hasMult = e.deflation_mult !== undefined && e.deflation_mult !== null;
                      const hasConc = e.concentration_mult !== undefined && e.concentration_mult !== null && e.concentration_mult < 1.0;
                      const hasTilt = e.trend_tilt !== undefined && e.trend_tilt !== null && e.trend_tilt !== 1.0;
                      const hasHoldout = e.holdout_marginal !== undefined && e.holdout_marginal !== null;
                      const hasNet = e.net_marginal !== undefined && e.net_marginal !== null;
                      return (
                        <div key={i} className="flex items-center gap-2 flex-wrap">
                          <span className="w-5 text-ag-text-muted">{e.step}.</span>
                          <span className={e.action === "skip" ? "text-red-400 line-through" : e.action === "seed" ? "text-green-400 font-medium" : "text-green-400"}>
                            {name}
                          </span>
                          <span className="text-ag-text-muted">
                            Defl {e.sharpe_before.toFixed(3)} → {e.sharpe_after.toFixed(3)}
                            {hasRaw && <span className="text-ag-text-muted/60 ml-1">(raw {e.sharpe_raw!.toFixed(3)})</span>}
                          </span>
                          <span className={e.marginal >= 0.02 ? "text-green-400" : e.marginal >= 0 ? "text-yellow-400" : "text-red-400"}>
                            {e.marginal >= 0 ? "+" : ""}{e.marginal.toFixed(4)}
                          </span>
                          {hasMult && (
                            <span className="text-[9px] text-ag-text-muted/60">×{e.deflation_mult!.toFixed(2)}</span>
                          )}
                          {hasConc && (
                            <span className="text-[9px] text-orange-400/70">conc×{e.concentration_mult!.toFixed(2)}</span>
                          )}
                          {hasTilt && (
                            <span className="text-[9px] text-cyan-400/70">tilt×{e.trend_tilt!.toFixed(2)}</span>
                          )}
                          {hasHoldout && (
                            <span className={`text-[9px] ${e.holdout_marginal! >= 0 ? "text-green-400/60" : "text-red-400/60"}`}>
                              hld{e.holdout_marginal! >= 0 ? "+" : ""}{e.holdout_marginal!.toFixed(4)}
                            </span>
                          )}
                          {hasNet && (
                            <span className="text-[9px] text-purple-400/70">
                              net{e.net_marginal! >= 0 ? "+" : ""}{e.net_marginal!.toFixed(4)}
                            </span>
                          )}
                          <span className="text-[9px] text-ag-text-muted">
                            {e.action === "seed" ? "anchor" : e.action === "add" ? "added" : "skipped"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {smartGroupResult.assets_filtered && Object.keys(smartGroupResult.assets_filtered).length > 0 && (
                <div>
                  <span className="text-ag-text-muted font-medium">
                    IS-Filtered Asset Pool
                    <HintTip text="Same min PF / WR / trades filters the optimizer uses. Batch curves built from SURVIVING assets only." />
                  </span>
                  <div className="mt-1 flex flex-wrap gap-3">
                    {Object.entries(smartGroupResult.assets_filtered).map(([bid, stats]) => {
                      const name = smartGroupResult.batch_names[bid] || `#${bid}`;
                      const [total, surviving] = stats;
                      const pct = total > 0 ? Math.round(surviving / total * 100) : 0;
                      return (
                        <span key={bid} className="text-ag-text-muted">
                          {name.split("—")[0].trim()}: <span className={pct > 30 ? "text-green-400" : "text-orange-400"}>{surviving}/{total}</span> ({pct}%)
                        </span>
                      );
                    })}
                  </div>
                </div>
              )}
              <div>
                <span className="text-ag-text-muted font-medium">Strategy Families (rolling corr &gt; 0.65, 75d windows, direction-aware):</span>
                <div className="mt-1 space-y-0.5">
                  {smartGroupResult.families.map((fam) => {
                    const repName = smartGroupResult.batch_names[String(fam.representative_id)] || `#${fam.representative_id}`;
                    const isRecommended = smartGroupResult.recommended_ids.includes(fam.representative_id);
                    return (
                      <div key={fam.family_id} className="flex items-center gap-2">
                        <span className="text-purple-400 font-medium w-16">Family {fam.family_id + 1}</span>
                        <span className={fam.side === "Long" ? "text-green-400 text-[9px] w-10" : fam.side === "Short" ? "text-red-400 text-[9px] w-10" : "text-ag-text-muted text-[9px] w-10"}>
                          {fam.side || "—"}
                        </span>
                        <span className="text-ag-text-muted">({fam.batch_ids.length} batches, corr {fam.avg_internal_corr.toFixed(2)})</span>
                        <span className={isRecommended ? "text-green-400" : "text-orange-400"}>
                          Best: {repName} {isRecommended ? "✓" : "(skipped by Sharpe)"}
                        </span>
                        {fam.batch_ids.length > 1 && (
                          <span className="text-ag-text-muted truncate">
                            Others: {fam.batch_ids.filter((b) => b !== fam.representative_id).map((b) =>
                              smartGroupResult.batch_names[String(b)] || `#${b}`
                            ).join(", ")}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
              {smartGroupResult.cv_stability && smartGroupResult.cv_stability.n_folds > 0 && (
                <div>
                  <span className="text-ag-text-muted font-medium">
                    IS Cross-Validation ({smartGroupResult.cv_stability.n_folds}-fold)
                    <HintTip text="Runs on K time-based folds of IS (leave-one-out). Batches stable across ≥ K-1 folds are ✓. Unstable = regime-dependent." />
                  </span>
                  <div className="mt-1 flex flex-wrap gap-3">
                    {smartGroupResult.cv_stability.selection_counts && Object.entries(smartGroupResult.cv_stability.selection_counts).map(([bid, count]) => {
                      const name = smartGroupResult.batch_names[bid] || `#${bid}`;
                      const isStable = (smartGroupResult.cv_stability?.stable_selections ?? []).includes(Number(bid));
                      const nFolds = smartGroupResult.cv_stability?.n_folds ?? 5;
                      return (
                        <span key={bid} className={isStable ? "text-green-400" : "text-orange-400"}>
                          {name.split("—")[0].trim()}: {count}/{nFolds} {isStable ? "✓" : "⚠"}
                        </span>
                      );
                    })}
                  </div>
                </div>
              )}
              {/* V2: Trend Regime display */}
              {smartGroupResult.trend_regime && smartGroupResult.trend_regime.regime !== "neutral" && (
                <div>
                  <span className="text-ag-text-muted font-medium">
                    Trend Regime
                    <HintTip text="Detected from close-to-close price direction streaks across all symbols (IS-only). Aligned-direction batches get a mild promotion in greedy selection." />
                  </span>
                  <div className="mt-1 flex items-center gap-3">
                    <span className={smartGroupResult.trend_regime.regime === "bullish" ? "text-green-400 font-medium" : "text-red-400 font-medium"}>
                      {smartGroupResult.trend_regime.regime === "bullish" ? "Bullish" : "Bearish"}
                    </span>
                    <span className="text-ag-text-muted">
                      {Math.round(smartGroupResult.trend_regime.bull_pct * 100)}% bull / {Math.round(smartGroupResult.trend_regime.bear_pct * 100)}% bear
                      {smartGroupResult.trend_regime.total_ranges && ` (${smartGroupResult.trend_regime.total_ranges} ranges)`}
                    </span>
                    <span className="text-ag-text-muted">
                      Streak ratio: {smartGroupResult.trend_regime.streak_ratio.toFixed(2)}x
                      {smartGroupResult.trend_regime.streak_ratio > 1.5 && " (extended — tilt reduced)"}
                    </span>
                  </div>
                </div>
              )}
              {/* V2: Bootstrap Stability display */}
              {smartGroupResult.bootstrap_stability && Object.keys(smartGroupResult.bootstrap_stability).length > 0 && (
                <div>
                  <span className="text-ag-text-muted font-medium">
                    Bootstrap Stability
                    <HintTip text="Each batch was tested by randomly dropping 15-20% of trades and re-running selection 100 times. Score = how often the batch was still selected. Below 60% = unstable (carried by a few lucky trades)." />
                  </span>
                  <div className="mt-1 flex flex-wrap gap-3">
                    {Object.entries(smartGroupResult.bootstrap_stability).map(([bid, score]) => {
                      const name = smartGroupResult.batch_names[bid] || `#${bid}`;
                      const pct = Math.round(score * 100);
                      return (
                        <span key={bid} className={pct >= 80 ? "text-green-400" : pct >= 60 ? "text-yellow-400" : "text-red-400"}>
                          {name.split("—")[0].trim()}: {pct}/100 {pct >= 80 ? "✓" : pct >= 60 ? "~" : "⚠"}
                        </span>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Per-strategy filters + allocation */}
      {selectedBatches.length > 0 && (
        <div className={`bg-ag-card border rounded-lg ${perBatchEnabled ? "border-ag-border" : "border-ag-border/50 opacity-60"}`}>
          <div className="flex items-center justify-between px-4 py-2.5">
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 cursor-pointer" onClick={(e) => e.stopPropagation()}>
                <input type="checkbox" checked={perBatchEnabled}
                  onChange={(e) => setPerBatchEnabled(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-sm font-medium">{mode === "manual" && !manualS.poolMode ? "Per-Strategy Allocation & Filters" : "Per-Strategy Filters"}</span>
              </label>
              <HintTip text="When OFF, per-batch overrides are ignored — only the shared Min PF / WR / Trades apply uniformly. When ON, you can customize filters per batch (e.g. different min trades for different strategies)." />
              {!perBatchEnabled && <span className="text-[10px] text-ag-text-muted">off — shared filters only</span>}
            </div>
            <button onClick={() => setShowPerBatch(!showPerBatch)} className="text-ag-text-muted text-xs hover:text-ag-text px-2 py-1">
              {showPerBatch ? "\u25B2" : "\u25BC"}
            </button>
          </div>
          {showPerBatch && perBatchEnabled && (
            <div className="border-t border-ag-border">
              <CrossStrategyConfig
                batches={selectedBatches.map((b) => ({ id: b.id, label: `${b.name || "#" + b.id} · ${b.direction_filter} · ${b.completed_symbols} sym` }))}
                configs={perBatch} onConfigsChange={setPerBatch}
                allocations={allocations} onAllocationsChange={setAllocations}
                showAllocation={mode === "manual" && !manualS.poolMode} />
            </div>
          )}
        </div>
      )}

      {/* Category filter + Exclusion + Clip toggles */}
      <div className="bg-ag-card border border-ag-border rounded-lg px-4 py-3 space-y-3">
        <CategoryFilterSelect selected={categoryFilter} onChange={setCategoryFilter} />
        <PortfolioExcludeSelector
          portfolios={portfolios}
          selectedIds={excludePortfolioIds}
          onToggle={toggleExcludePortfolio}
          mode={excludeMode}
          onModeChange={setExcludeMode}
          keepPct={excludeKeepPct}
          onKeepPctChange={setExcludeKeepPct}
          method={excludeMethod}
          onMethodChange={setExcludeMethod}
        />
        <div className="flex items-center gap-4 flex-wrap">
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
      </div>

      {/* Position Sizing Profile */}
      <OptimizerRiskProfile
        show={showRiskProfile} onToggle={() => setShowRiskProfile(!showRiskProfile)}
        config={riskProfile} onChange={setRiskProfile}
        sizingMaster={sizingMaster} onSizingMasterChange={setSizingMaster}
        tfConfigs={tfConfigs} onTfConfigsChange={setTfConfigs}
        batches={selectedBatches.map((b) => ({ id: b.id, label: b.name || `#${b.id}`, timeframe: b.timeframe }))}
        autoEnabled={autoRiskEnabled} onAutoEnabledChange={setAutoRiskEnabled}
        autoAggression={autoRiskAggression} onAutoAggressionChange={setAutoRiskAggression}
        onAutoAnalyze={runAutoRiskAnalyze} autoLoading={autoRiskLoading}
        autoResult={autoRiskResult}
        autoBaseRiskMart={autoBaseRiskMart} onAutoBaseRiskMartChange={setAutoBaseRiskMart}
        autoMinConfidence={autoMinConfidence} onAutoMinConfidenceChange={setAutoMinConfidence}
        autoStrategyHint={autoStrategyHint} onAutoStrategyHintChange={setAutoStrategyHint}
        dateTo={dateTo} />

      {/* Optimizer feature toggles — rendered before detailed controls so they're visible */}
      <OptimizerTC show={showTC} onToggle={() => setShowTC(!showTC)} splitMode={false}
        enabled={tcEnabled} onEnabledChange={setTcEnabled}
        tc={tc} onTCChange={setTc}
        tcLongs={tcLongs} onTCLongsChange={setTcLongs}
        tcShorts={tcShorts} onTCShortsChange={setTcShorts} />

      <OptimizerBenchmark show={showBM} onToggle={() => setShowBM(!showBM)}
        enabled={bmEnabled} onEnabledChange={setBmEnabled}
        config={bmConfig} onConfigChange={setBmConfig} />

      <OptimizerHarmonyGate show={showHarmony} onToggle={() => setShowHarmony(!showHarmony)}
        config={harmonyConfig} onConfigChange={setHarmonyConfig}
        groupLabels={
          mode === "directional"
            ? dirS.l2Groups.map((g, i) => `Group ${i + 1} (${g.length} batches)`)
            : mode === "manual"
              ? manualS.l2Groups.map((g, i) => `Group ${i + 1} (${g.length} batches)`)
              : undefined
        } />

      {/* Controls */}
      <CrossBuilderControls mode={mode} onModeChange={setMode}
        auto={autoS} onAutoChange={(p) => setAutoS((s) => ({ ...s, ...p }))}
        manual={manualS} onManualChange={(p) => setManualS((s) => ({ ...s, ...p }))}
        directional={dirS} onDirectionalChange={(p) => setDirS((s) => ({ ...s, ...p }))}
        shared={shared} onSharedChange={(p) => setShared((s) => ({ ...s, ...p }))}
        prioritizeStability={prioritizeStability} onStabilityChange={setPrioritizeStability}
        nBatches={totalSources}
        batchOptions={[
          ...selectedBatches.map((b) => ({
            id: b.id,
            label: `${b.name || `#${b.id}`} · ${b.direction_filter}`,
          })),
          ...portfolioIds.map((pid) => {
            const p = portfolios.find((x) => x.id === pid);
            return { id: -pid, label: p ? `${p.name} (portfolio)` : `Portfolio #${pid}` };
          }),
        ]}
        onRun={runBuilder} loading={loading} disabled={totalSources < 2} />

      {error && <p className="text-sm text-ag-red">{error}</p>}

      {/* Progress bar */}
      <OptimizationProgress step={optimStep} totalSteps={optimTotal} label={optimLabel} isRunning={loading} />

      {/* Settings Recommender */}
      {totalSources >= 1 && (
        <SettingsRecommender
          batchIds={batchIds}
          portfolioIds={portfolioIds}
          shared={shared}
          auto={autoS}
          onSharedChange={(p) => setShared((s) => ({ ...s, ...p }))}
          onAutoChange={(p) => setAutoS((s) => ({ ...s, ...p }))}
          dateFrom={shared.dateFrom}
          dateTo={dateTo}
          clipIS={clipIS}
        />
      )}

      {/* How It Works explainer */}
      <OptimizerExplainer
        divAlgo={shared.divAlgo}
        corrJitterEnabled={shared.corrJitterEnabled}
        refinementEnabled={shared.refinementEnabled}
        refinementMode={shared.refinementMode}
      />

      {/* Results */}
      {candidates.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium">Cross-Strategy Candidates ({candidates.length})</h3>
            <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
              optimizerEngine === "fast" ? "bg-amber-500/15 text-amber-400" :
              optimizerEngine === "classic" ? "bg-purple-500/15 text-purple-400" :
              optimizerEngine === "og_v1" ? "bg-green-500/15 text-green-400" :
              optimizerEngine === "og_v2" ? "bg-cyan-500/15 text-cyan-400" :
              "bg-ag-accent/15 text-ag-accent"
            }`}>
              {optimizerEngine === "fast" ? "Fast Engine" :
               optimizerEngine === "classic" ? "Classic Engine" :
               optimizerEngine === "og_v1" ? "OG v1 (Mar 11)" :
               optimizerEngine === "og_v2" ? "OG v2 (Mar 12)" :
               "Principal Engine"}
            </span>
          </div>
          {candidates.map((c, i) => (
            <CrossCandidateCard key={`${buildId}-${i}`} index={i} candidate={c}
              saving={saving === i} onSave={() => saveCandidate(i, c)}
              saved={savedIndices.has(i)}
              savedPortfolioId={savedPortfolioIds.get(i)}
              onViewPortfolios={onNavigateTab ? () => onNavigateTab("portfolios") : undefined}
              dateTo={dateTo} batchIds={batchIds}
              clipIS={clipIS} clipOOS={clipOOS} oosEndDate={oosEndDate || undefined}
              extraCosts={{
                extra_slippage_pct: shared.extraSlippage || 0,
                extra_commission_pct: shared.extraCommission || 0,
                funding_daily_pct: shared.fundingDaily || 0,
                spread_pct: shared.spread || 0,
              }}
              riskProfile={(riskProfile.mode !== "fixed" || riskProfile.value > 0 || (autoRiskEnabled && autoRiskResult?.per_asset))
                ? {
                    mode: riskProfile.mode, value: riskProfile.value,
                    initialCapital: riskProfile.initialCapital,
                    multiplier: riskProfile.multiplier, maxStreak: riskProfile.maxStreak,
                    compound: riskProfile.martingaleCompound, scope: riskProfile.martingaleScope,
                    countMode: riskProfile.martingaleCountMode, oosReset: riskProfile.martingaleOosReset,
                    startAfter: riskProfile.martingaleStartAfter, resetAfter: riskProfile.antiMartingaleResetAfter,
                    kellyN: riskProfile.kellyN, kellyWarmup: riskProfile.kellyWarmup,
                    kellyScope: riskProfile.kellyScope, kellyLookback: riskProfile.kellyLookback,
                    kellyCap: riskProfile.kellyCap,
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
              }
              perBatchConfig={sizingMaster === "per_tf"
                ? (() => {
                    const p = buildPerTfPayload();
                    return p.risk_profile_sizing_master
                      ? { sizingMaster: p.risk_profile_sizing_master, batchConfigs: p.risk_profile_batch_configs || "" }
                      : undefined;
                  })()
                : undefined
              }
              onOosLoaded={(summary) => setOosSummaries((prev) => new Map(prev).set(i, summary))}
              autoRiskModes={autoRiskEnabled && autoRiskResult?.per_asset
                ? Object.fromEntries(Object.entries(autoRiskResult.per_asset).map(([s, r]) => [s, r.mode]))
                : undefined} />
          ))}
        </div>
      )}

      {/* Aggregate OOS robustness summary */}
      {oosSummaries.size >= 2 && <OosAggregateSummary summaries={oosSummaries} totalCandidates={candidates.length} />}

      {/* Batch Affinity Report */}
      {(affinityReport || affinityLoading) && (
        <div className="space-y-2">
          {/* Controls bar — ABOVE the report */}
          {candidates.length > 0 && batchIds.length >= 2 && (
            <div className="bg-ag-card border border-ag-border rounded-lg p-3 space-y-2">
              {/* Row 1: Pin Anchors */}
              <div className="flex items-start gap-2 text-xs">
                <span className="text-ag-text-muted font-medium whitespace-nowrap mt-0.5">Pin Anchors</span>
                <HintTip text="Select your best batch(es). Pinned batches are forced to 'Anchor' role — never marked redundant or dropped. Leave empty to auto-discover." />
                <div className="flex gap-1 flex-wrap flex-1">
                  {selectedBatches.filter(b => batchIds.includes(b.id)).map((b) => {
                    const isPinned = affinityPinnedAnchors.has(b.id);
                    return (
                      <button key={b.id}
                        onClick={() => {
                          const next = new Set(affinityPinnedAnchors);
                          if (isPinned) next.delete(b.id); else next.add(b.id);
                          setAffinityPinnedAnchors(next);
                        }}
                        className={`px-2 py-1 rounded text-[11px] border transition-colors max-w-[220px] ${
                          isPinned
                            ? "bg-green-500/20 border-green-500/50 text-green-400 font-medium"
                            : "bg-ag-dark border-ag-border text-ag-text-muted hover:border-ag-accent/50 hover:text-ag-text"
                        }`}
                        title={b.name || `#${b.id}`}
                      >
                        {isPinned ? "★ " : ""}{(b.name || `#${b.id}`).slice(0, 28)}
                      </button>
                    );
                  })}
                </div>
              </div>
              {/* Row 2: OOS settings + Re-run */}
              <div className="flex items-center gap-3 text-xs flex-wrap border-t border-ag-border/50 pt-2">
                <label className="flex items-center gap-1">
                  <input type="checkbox" checked={affinityIncludeOos}
                    onChange={(e) => setAffinityIncludeOos(e.target.checked)}
                    className="accent-ag-accent" />
                  Include OOS
                  <HintTip text="Validate affinity metrics out-of-sample. Uses OOS1 window shifted back to protect OOS2. OOS2 is NEVER touched." />
                </label>
                {affinityIncludeOos && (
                  <>
                    <label className="flex items-center gap-1">
                      OOS1
                      <HintTip text="Length of OOS1 window in months. Shifted backward by OOS2 length so OOS2 stays pristine." />
                      <input type="number" min={1} max={24} value={affinityOos1Months}
                        onChange={(e) => setAffinityOos1Months(Math.max(1, Number(e.target.value)))}
                        className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center" />
                      <span className="text-ag-text-muted">mo</span>
                    </label>
                    <label className="flex items-center gap-1">
                      OOS2
                      <HintTip text="Length of OOS2 (untouchable) window in months. Excluded from OOS1 validation." />
                      <input type="number" min={1} max={12} value={affinityOos2Months}
                        onChange={(e) => setAffinityOos2Months(Math.max(1, Number(e.target.value)))}
                        className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-center" />
                      <span className="text-ag-text-muted">mo</span>
                    </label>
                  </>
                )}
                <button onClick={runAffinityReport} disabled={affinityLoading}
                  className="ml-auto px-3 py-1 rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 disabled:opacity-50 transition-colors font-medium">
                  {affinityLoading ? "Running…" : "Re-run Report"}
                </button>
              </div>
            </div>
          )}
          {affinityLoading && !affinityReport && (
            <div className="bg-ag-card border border-ag-border rounded-lg px-3 py-4 text-xs text-ag-text-muted flex items-center gap-2">
              <span className="animate-spin h-3 w-3 border-2 border-ag-accent border-t-transparent rounded-full" />
              Computing Batch Affinity Report…
            </div>
          )}
          {affinityReport && <BatchAffinityReport report={affinityReport} />}
        </div>
      )}

      {/* Correlation heatmap — collapsed by default */}
      {corr && (
        <details className="group">
          <summary className="text-sm font-medium cursor-pointer hover:text-ag-accent flex items-center gap-1">
            <span className="text-ag-text-muted group-open:rotate-90 transition-transform">&#9654;</span>
            Cross-Strategy Correlation
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


// ---------------------------------------------------------------------------
// Aggregate OOS Robustness Summary
// ---------------------------------------------------------------------------

function gradeColor(grade: string): string {
  if (grade.startsWith("A")) return "bg-ag-green/20 text-ag-green border-ag-green/30";
  if (grade.startsWith("B")) return "bg-lime-500/20 text-lime-400 border-lime-500/30";
  if (grade.startsWith("C")) return "bg-yellow-500/20 text-yellow-400 border-yellow-500/30";
  if (grade.startsWith("D")) return "bg-orange-500/20 text-orange-400 border-orange-500/30";
  return "bg-ag-red/20 text-ag-red border-ag-red/30";
}

function pfColor(pf: number): string {
  if (pf >= 1.5) return "text-ag-green";
  if (pf >= 1.0) return "text-yellow-400";
  return "text-ag-red";
}

function verdictColor(verdict: string): string {
  if (verdict === "Robust") return "text-ag-green";
  if (verdict === "Solid") return "text-lime-400";
  if (verdict === "Acceptable") return "text-yellow-400";
  if (verdict === "Marginal") return "text-orange-400";
  return "text-ag-red";
}

function OosAggregateSummary({ summaries, totalCandidates }: {
  summaries: Map<number, OosSummary>;
  totalCandidates: number;
}) {
  const agg = useMemo(() => {
    const entries = Array.from(summaries.values()).filter(s => s.grade);
    if (entries.length === 0) return null;

    const n = entries.length;
    const avgPf = entries.reduce((s, e) => s + e.pf, 0) / n;
    const avgWfe = entries.reduce((s, e) => s + e.wfe, 0) / n;
    const avgStab = entries.reduce((s, e) => s + e.stability, 0) / n;
    const avgSortino = entries.reduce((s, e) => s + e.sortino, 0) / n;
    const avgWr = entries.reduce((s, e) => s + e.winRate, 0) / n;
    const avgRetDd = entries.reduce((s, e) => s + e.returnDd, 0) / n;
    const avgGradeScore = entries.reduce((s, e) => s + e.gradeScore, 0) / n;
    const totalProfit = entries.reduce((s, e) => s + e.oosProfit, 0);
    const avgProfit = totalProfit / n;
    const avgTrades = entries.reduce((s, e) => s + e.trades, 0) / n;

    // Best and worst
    const sorted = [...entries].sort((a, b) => b.gradeScore - a.gradeScore);
    const best = sorted[0];
    const worst = sorted[sorted.length - 1];

    // Grade distribution
    const gradeCounts: Record<string, number> = {};
    entries.forEach(e => {
      const g = e.grade || "?";
      gradeCounts[g] = (gradeCounts[g] || 0) + 1;
    });

    // Profitable count
    const profitable = entries.filter(e => e.pf >= 1.0).length;
    const strongProfitable = entries.filter(e => e.pf >= 1.3).length;

    // Average grade from score
    let avgGrade: string;
    if (avgGradeScore >= 0.90) avgGrade = "A+";
    else if (avgGradeScore >= 0.80) avgGrade = "A";
    else if (avgGradeScore >= 0.70) avgGrade = "B+";
    else if (avgGradeScore >= 0.60) avgGrade = "B";
    else if (avgGradeScore >= 0.50) avgGrade = "C+";
    else if (avgGradeScore >= 0.40) avgGrade = "C";
    else if (avgGradeScore >= 0.30) avgGrade = "D";
    else if (avgGradeScore >= 0.20) avgGrade = "D-";
    else avgGrade = "F";

    // Robustness verdict
    let verdict: string;
    let verdictDetail: string;
    if (profitable === n && avgPf >= 1.5 && strongProfitable >= n * 0.7) {
      verdict = "Robust";
      verdictDetail = `All ${n} candidates profitable, ${strongProfitable} with PF≥1.3. Optimizer settings are well-calibrated.`;
    } else if (profitable === n && avgPf >= 1.2) {
      verdict = "Solid";
      verdictDetail = `All ${n} profitable (avg PF ${avgPf.toFixed(2)}). Settings produce consistent results.`;
    } else if (profitable >= n * 0.8 && avgPf >= 1.0) {
      verdict = "Acceptable";
      verdictDetail = `${profitable}/${n} profitable. Most compositions work, but some are weak.`;
    } else if (profitable >= n * 0.5) {
      verdict = "Marginal";
      verdictDetail = `Only ${profitable}/${n} profitable. Consider different optimizer settings.`;
    } else {
      verdict = "Weak";
      verdictDetail = `Only ${profitable}/${n} profitable. These settings don't generalize well OOS.`;
    }

    return {
      n, avgPf, avgWfe, avgStab, avgSortino, avgWr, avgRetDd, avgGradeScore,
      avgGrade, avgProfit, avgTrades, best, worst, gradeCounts, profitable,
      strongProfitable, verdict, verdictDetail,
    };
  }, [summaries]);

  if (!agg) return null;

  const gradeEntries = Object.entries(agg.gradeCounts).sort((a, b) => {
    const order = ["A+", "A", "B+", "B", "C+", "C", "D", "D-", "F"];
    return order.indexOf(a[0]) - order.indexOf(b[0]);
  });

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-sm font-semibold text-ag-text flex items-center gap-2">
          OOS Robustness Summary
          <span className="text-[10px] text-ag-text-muted font-normal">
            {agg.n}/{totalCandidates} candidates revealed
          </span>
        </h3>
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-xs font-bold border ${gradeColor(agg.avgGrade)}`}
            title={`Avg grade score: ${(agg.avgGradeScore * 100).toFixed(0)}%`}>
            Avg: {agg.avgGrade}
          </span>
          <span className={`text-sm font-bold ${verdictColor(agg.verdict)}`}>
            {agg.verdict}
          </span>
        </div>
      </div>

      <p className="text-[10px] text-ag-text-muted">{agg.verdictDetail}</p>

      {/* Key average metrics */}
      <div className="grid grid-cols-4 sm:grid-cols-8 gap-2 text-xs">
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted">Avg PF</p>
          <p className={`font-bold ${pfColor(agg.avgPf)}`}>{agg.avgPf.toFixed(2)}</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted">Avg WFE</p>
          <p className="font-bold">{(agg.avgWfe * 100).toFixed(0)}%</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted">Avg Stab</p>
          <p className="font-bold">{(agg.avgStab * 100).toFixed(0)}%</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted">Avg Sortino</p>
          <p className="font-bold">{agg.avgSortino.toFixed(1)}</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted">Avg WR</p>
          <p className="font-bold">{agg.avgWr.toFixed(0)}%</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted">Avg Ret/DD</p>
          <p className="font-bold">{agg.avgRetDd.toFixed(1)}x</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted">Avg Profit</p>
          <p className={`font-bold ${agg.avgProfit >= 0 ? "text-ag-green" : "text-ag-red"}`}>
            ${agg.avgProfit.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted">Profitable</p>
          <p className={`font-bold ${agg.profitable === agg.n ? "text-ag-green" : agg.profitable >= agg.n * 0.7 ? "text-yellow-400" : "text-ag-red"}`}>
            {agg.profitable}/{agg.n}
          </p>
        </div>
      </div>

      {/* Grade distribution */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] text-ag-text-muted mr-1">Grades:</span>
        {gradeEntries.map(([grade, count]) => (
          <span key={grade} className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${gradeColor(grade)}`}>
            {count}× {grade}
          </span>
        ))}
      </div>

      {/* Best / Worst */}
      <div className="flex items-center gap-4 text-[10px] text-ag-text-muted">
        <span>
          Best: <span className={`font-bold ${pfColor(agg.best.pf)}`}>PF {agg.best.pf.toFixed(2)}</span>
          {agg.best.grade && <span className={`ml-1 px-1 rounded border ${gradeColor(agg.best.grade)}`}>{agg.best.grade}</span>}
          <span className="ml-1">WFE {(agg.best.wfe * 100).toFixed(0)}%</span>
        </span>
        <span>
          Worst: <span className={`font-bold ${pfColor(agg.worst.pf)}`}>PF {agg.worst.pf.toFixed(2)}</span>
          {agg.worst.grade && <span className={`ml-1 px-1 rounded border ${gradeColor(agg.worst.grade)}`}>{agg.worst.grade}</span>}
          <span className="ml-1">WFE {(agg.worst.wfe * 100).toFixed(0)}%</span>
        </span>
      </div>
    </div>
  );
}
