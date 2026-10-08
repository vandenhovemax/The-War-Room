"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { Play, Loader2, AlertCircle, X } from "lucide-react";
import {
  api, DataStatusItem, WatchlistItem, StrategyItem, StrategyConfigItem,
  ParameterDefinition, SweepParam, SweepProgress, SweepDetail,
} from "@/lib/api";
import type { DirectionValue } from "./DirectionFilter";
import SymbolMultiSelect from "./SymbolMultiSelect";
import SweepComparisonTable from "./SweepComparisonTable";
import HintTip from "@/components/common/HintTip";
import { useTaskManager } from "@/contexts/TaskManager";
import SweepParamSetup from "./SweepParamSetup";

interface Props {
  strategyId: number;
  strategies: StrategyItem[];
  dataStatus: DataStatusItem[];
  direction: DirectionValue;
  timeframe: string;
  capital: string;
  commission: string;
  includeCosts: boolean;
  slippageMode: string;
  slippageValue: string;
  leverage: string;
  pyramiding: string;
  minAdv: string;
  maxAdv: string;
  minPositions: string;
  configMode: "default" | "custom" | number;
  configs: StrategyConfigItem[];
  strategyInputs: Record<string, number | boolean | string>;
  normalized: boolean;
  riskMode: string;
  riskValue: string;
  martingaleEnabled: boolean;
  martingaleType: string;
  martingaleMultiplier: string;
  martingaleMaxStreak: string;
  volFilterEnabled: boolean;
  volFilterAtrCeiling: string;
  volFilterAtrLength: string;
  processOrdersOnClose: boolean;
  minDataDate: string;
  startDate: string;
  endDate: string;
  fundingRate: string;
  spread: string;
  prefilterEnabled: boolean;
  prefilterMode: "trend" | "meanrev";
  prefilterHurstMin: string;
  prefilterTerPctl: string;
  prefilterHurstMax: string;
  prefilterVrMax: string;
  prefilterMinCr: string;
  enforceBybitLimits: boolean;
  expectedLiveSize: string;
  maxMarginPerTrade: string;
  marginFilterTolerance: string;
  bybitRejectPct: string;
}

export default function SweepTab(p: Props) {
  const { addToast } = useTaskManager();
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [mode, setMode] = useState<"individual" | "portfolio">("individual");
  const [sweepMode, setSweepMode] = useState<"grid" | "one_at_a_time">("grid");
  const [sweepParams, setSweepParams] = useState<SweepParam[]>([]);
  const [paramDefs, setParamDefs] = useState<ParameterDefinition[]>([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Sweep state
  const [sweepId, setSweepId] = useState<number | null>(null);
  const [progress, setProgress] = useState<SweepProgress | null>(null);
  const [result, setResult] = useState<SweepDetail | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.watchlist().then((wl: WatchlistItem[]) => setWatchlist(wl.map((w) => w.symbol)));
  }, []);

  // Load param defs when strategy changes
  useEffect(() => {
    if (!p.strategyId) { setParamDefs([]); return; }
    api.strategyParams(p.strategyId).then(setParamDefs).catch(() => setParamDefs([]));
  }, [p.strategyId]);

  // Compute total combos
  const sweepable = paramDefs.filter((d) => d.type === "int" || d.type === "float" || d.type === "bool");
  const comboCount = computeComboCount(sweepParams, sweepMode);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  // Cleanup on unmount
  useEffect(() => () => stopPolling(), [stopPolling]);

  const startPolling = useCallback((sid: number) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const prog = await api.sweepProgress(sid);
        setProgress(prog);
        if (prog.status === "completed" || prog.status === "cancelled" || prog.status === "error" || prog.status === "interrupted") {
          stopPolling();
          // Load final results
          const detail = await api.sweepDetail(sid);
          setResult(detail);
        }
      } catch { /* ignore transient errors */ }
    }, 2000);
  }, [stopPolling]);

  const handleRun = async () => {
    if (!p.strategyId || selectedSymbols.length === 0 || sweepParams.length === 0 || submitting) return;
    if (comboCount > 200 && !confirm(`${comboCount} combos × ${selectedSymbols.length} symbols = ${(comboCount * selectedSymbols.length).toLocaleString()} backtests. This will take a long time. Continue?`)) {
      return;
    }
    setError("");
    setSubmitting(true);
    setResult(null);
    setProgress(null);

    try {
      const cfgId = typeof p.configMode === "number" ? p.configMode : undefined;
      const inputs = p.configMode === "custom" ? p.strategyInputs : undefined;
      const res = await api.startSweep({
        strategy_id: p.strategyId,
        symbols: selectedSymbols,
        timeframe: p.timeframe,
        direction_filter: p.direction,
        sweep_params: sweepParams,
        sweep_mode: sweepMode,
        initial_capital: parseFloat(p.capital),
        commission_pct: p.includeCosts ? parseFloat(p.commission) : 0,
        slippage_mode: p.slippageMode,
        slippage_value: p.includeCosts ? (parseFloat(p.slippageValue) || 0) : 0,
        leverage: parseInt(p.leverage),
        min_avg_daily_volume: parseFloat(p.minAdv) || 0,
        max_avg_daily_volume: parseFloat(p.maxAdv) || 0,
        min_positions: parseInt(p.minPositions) || 0,
        config_id: cfgId,
        strategy_inputs: inputs,
        max_pyramiding: parseInt(p.pyramiding) || 1,
        risk_pct: 0,
        risk_mode: p.riskMode,
        risk_value: parseFloat(p.riskValue) || 0,
        martingale_enabled: p.martingaleEnabled,
        martingale_type: p.martingaleType,
        martingale_multiplier: parseFloat(p.martingaleMultiplier) || 2,
        martingale_max_streak: parseInt(p.martingaleMaxStreak) || 3,
        vol_filter_enabled: p.volFilterEnabled,
        vol_filter_atr_ceiling: parseFloat(p.volFilterAtrCeiling) || 0,
        vol_filter_atr_length: parseInt(p.volFilterAtrLength) || 14,
        mode,
        normalized: p.normalized,
        include_costs: p.includeCosts,
        funding_rate_daily_pct: p.includeCosts ? (parseFloat(p.fundingRate) || 0) : 0,
        spread_pct: p.includeCosts ? (parseFloat(p.spread) || 0) : 0,
        prefilter_enabled: p.prefilterEnabled,
        prefilter_mode: p.prefilterMode,
        prefilter_hurst_min: parseFloat(p.prefilterHurstMin) || 0.5,
        prefilter_ter_percentile: parseInt(p.prefilterTerPctl) || 25,
        prefilter_hurst_max: parseFloat(p.prefilterHurstMax) || 0.5,
        prefilter_vr_max: parseFloat(p.prefilterVrMax) || 1.0,
        prefilter_min_crossing_rate: parseFloat(p.prefilterMinCr) || 0.05,
        min_data_date: p.minDataDate || undefined,
        process_orders_on_close: p.processOrdersOnClose,
        enforce_bybit_limits: p.enforceBybitLimits,
        expected_live_size: parseFloat(p.expectedLiveSize) || 0,
        max_margin_per_trade: parseFloat(p.maxMarginPerTrade) || 0,
        margin_filter_tolerance: parseFloat(p.marginFilterTolerance) || 10,
        bybit_reject_pct: parseFloat(p.bybitRejectPct) || 0,
      });
      setSweepId(res.sweep_id);
      startPolling(res.sweep_id);
      addToast(`Sweep started: ${res.total_combos} combos`, "success");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start sweep");
    }
    setSubmitting(false);
  };

  const handleCancel = async () => {
    if (!sweepId) return;
    if (!confirm("Stop this sweep? Completed combos will be saved and you can resume later.")) return;
    try {
      const res = await api.cancelSweep(sweepId);
      stopPolling();
      addToast(`Sweep stopped: ${res.completed_count ?? 0} combos saved. Resume from Saved Backtests → Sweep tab.`, "success");
      // Load whatever results we have
      try {
        const detail = await api.sweepDetail(sweepId);
        setResult(detail);
      } catch { /* ok */ }
    } catch { addToast("Failed to stop sweep", "error"); }
  };

  const running = progress?.status === "running";
  const isLongShort = p.direction === "long_plus_short";

  return (
    <div className="space-y-4">
      {/* Mode toggle */}
      <div>
        <label className="text-xs text-ag-text-muted mb-1 block">
          Mode<HintTip text="Individual: each symbol tested independently. Portfolio: shared equity pool." />
        </label>
        <div className="flex gap-1 bg-ag-dark rounded-lg p-1 w-fit">
          {(["individual", "portfolio"] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)}
              className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${
                mode === m ? "bg-ag-accent text-white" : "text-ag-text-muted hover:text-ag-text"
              }`}>
              {m === "individual" ? "Individual" : "Portfolio"}
            </button>
          ))}
        </div>
      </div>

      {/* Symbol multi-select */}
      <div>
        <label className="text-xs text-ag-text-muted mb-1 block">Symbols</label>
        <SymbolMultiSelect dataStatus={p.dataStatus} watchlist={watchlist}
          selected={selectedSymbols} onChange={setSelectedSymbols}
          minAdv={parseFloat(p.minAdv) || 0} maxAdv={parseFloat(p.maxAdv) || 0} />
      </div>

      {/* Sweep param setup */}
      <SweepParamSetup
        sweepable={sweepable}
        sweepParams={sweepParams}
        onSweepParamsChange={setSweepParams}
        sweepMode={sweepMode}
        onSweepModeChange={setSweepMode}
        comboCount={comboCount}
        symbolCount={selectedSymbols.length}
      />

      {/* Run / Cancel buttons */}
      <div className="flex gap-2">
        <button onClick={handleRun}
          disabled={selectedSymbols.length === 0 || !p.strategyId || sweepParams.length === 0 || submitting}
          className="flex-1 flex items-center justify-center gap-2 py-3 bg-ag-accent text-white rounded-lg font-medium disabled:opacity-50 hover:bg-ag-accent/90">
          {submitting ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} />}
          Run Sweep ({comboCount} combos × {selectedSymbols.length} symbols)
        </button>
        {running && (
          <button onClick={handleCancel}
            className="px-4 py-3 bg-ag-red/15 text-ag-red border border-ag-red/30 rounded-lg font-medium hover:bg-ag-red/25 transition-colors">
            Stop
          </button>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-3 bg-ag-red/10 border border-ag-red/30 rounded-lg text-ag-red text-sm">
          <AlertCircle size={16} /> {error}
          <button onClick={() => setError("")} className="ml-auto"><X size={14} /></button>
        </div>
      )}

      {/* Progress */}
      {running && progress && (
        <div className="bg-ag-card border border-ag-border rounded-xl p-5">
          <h2 className="text-sm font-medium mb-3">
            Sweep Progress — {progress.completed}/{progress.total_combos} combos completed
          </h2>
          <div className="w-full bg-ag-dark rounded-full h-2 mb-3">
            <div className="bg-ag-accent h-2 rounded-full transition-all"
              style={{ width: `${progress.total_combos ? (progress.completed / progress.total_combos) * 100 : 0}%` }} />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs text-ag-text-muted">
            {progress.combos.filter((c) => c.status === "running").slice(0, 4).map((c) => (
              <div key={c.batch_id} className="bg-ag-dark rounded px-2 py-1">
                <span className="text-ag-text">{c.current_symbol || "starting..."}</span>
                <span className="ml-1">({c.completed_symbols}/{c.total_symbols})</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Results */}
      {result && result.combos.length > 0 && (
        <div className="bg-ag-card border border-ag-border rounded-xl p-5">
          <h2 className="text-lg font-bold mb-4">
            Sweep Results — {result.sweep.name}
          </h2>
          <SweepComparisonTable
            combos={result.combos}
            sweptParams={result.sweep.sweep_config.params}
            isLongShort={isLongShort}
          />
        </div>
      )}
    </div>
  );
}

function computeComboCount(params: SweepParam[], mode: string): number {
  if (params.length === 0) return 0;
  if (mode === "one_at_a_time") {
    return params.reduce((sum, p) => {
      if (p.step <= 0) return sum;
      return sum + Math.floor((p.max - p.min) / p.step) + 1;
    }, 0);
  }
  // Grid
  return params.reduce((product, p) => {
    if (p.step <= 0) return product;
    return product * (Math.floor((p.max - p.min) / p.step) + 1);
  }, 1);
}
