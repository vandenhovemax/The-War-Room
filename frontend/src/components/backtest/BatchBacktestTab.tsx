"use client";
import { useState, useEffect, useRef, useMemo } from "react";
import { Play, Loader2, AlertCircle, ListOrdered, X, CheckSquare, Square, FolderOpen } from "lucide-react";
import {
  api, DataStatusItem, WatchlistItem, StrategyItem, StrategyConfigItem,
} from "@/lib/api";
import type { DirectionValue } from "./DirectionFilter";
import SymbolMultiSelect from "./SymbolMultiSelect";
import BatchProgressComp from "./BatchProgress";
import BatchResultsSummary from "./BatchResultsSummary";
import HintTip from "@/components/common/HintTip";
import { useTaskManager } from "@/contexts/TaskManager";

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

// Virtual ID for "Default Values" config
const DEFAULT_CONFIG_ID = 0;

export default function BatchBacktestTab(p: Props) {
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [mode, setMode] = useState<"individual" | "portfolio">("individual");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submitGuardRef = useRef(false);

  // Multi-config selection
  const [checkedIds, setCheckedIds] = useState<Set<number>>(() => {
    // Pre-check the currently active config
    if (typeof p.configMode === "number") return new Set([p.configMode]);
    return new Set([DEFAULT_CONFIG_ID]);
  });

  const {
    queueBatch, cancelTask, cancelAllTasks,
    latestBatchTask, batchQueue, removeFromQueue,
  } = useTaskManager();
  const running = latestBatchTask?.status === "running";
  const batchIds = latestBatchTask?.batchIds ?? [];
  const progressMap = latestBatchTask?.progressMap ?? {};
  const results = latestBatchTask?.results ?? [];
  const skippedCount = latestBatchTask?.skippedCount ?? 0;
  const skippedDataDateCount = latestBatchTask?.skippedDataDateCount ?? 0;
  const prefilteredCount = latestBatchTask?.prefilteredCount ?? 0;

  useEffect(() => {
    api.watchlist().then((wl: WatchlistItem[]) => setWatchlist(wl.map((w) => w.symbol)));
  }, []);

  // Build the config list: Default Values + saved configs
  const configList = useMemo(() => {
    const items: { id: number; name: string; tag: string | null; description: string; paramSummary: string }[] = [
      { id: DEFAULT_CONFIG_ID, name: "Default Values", tag: null, description: "Strategy defaults", paramSummary: "" },
    ];
    for (const c of p.configs) {
      items.push({
        id: c.id,
        name: c.name,
        tag: c.tag,
        description: c.description,
        paramSummary: Object.entries(c.parameter_values).map(([k, v]) => `${k}: ${v}`).join(" · "),
      });
    }
    return items;
  }, [p.configs]);

  // Group by tag if any tags exist
  const groupedConfigs = useMemo(() => {
    const hasTags = configList.some(c => c.tag);
    if (!hasTags) return null;
    const groups: Record<string, typeof configList> = {};
    for (const c of configList) {
      const t = c.tag || "(untagged)";
      if (!groups[t]) groups[t] = [];
      groups[t].push(c);
    }
    const sorted = Object.keys(groups).sort((a, b) => {
      if (a === "(untagged)") return 1;
      if (b === "(untagged)") return -1;
      return a.localeCompare(b);
    });
    return sorted.map(t => ({ tag: t, items: groups[t] }));
  }, [configList]);

  const toggleConfig = (id: number) => {
    setCheckedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (checkedIds.size === configList.length) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(configList.map(c => c.id)));
    }
  };

  const toggleTagGroup = (items: typeof configList) => {
    const allChecked = items.every(c => checkedIds.has(c.id));
    setCheckedIds(prev => {
      const next = new Set(prev);
      for (const c of items) {
        if (allChecked) next.delete(c.id);
        else next.add(c.id);
      }
      return next;
    });
  };

  const handleRun = () => {
    if (!p.strategyId || selectedSymbols.length === 0 || submitting || submitGuardRef.current) return;
    if (checkedIds.size === 0) { setError("Select at least one config to run"); return; }

    submitGuardRef.current = true;
    setError("");
    setSubmitting(true);
    setTimeout(() => { setSubmitting(false); submitGuardRef.current = false; }, 2000);

    const capturedSymbols = [...selectedSymbols];
    const capturedProps = { ...p };
    const stratName = p.strategies.find((s) => s.id === p.strategyId)?.name ?? "";

    // Queue one batch per checked config
    for (const cfgId of checkedIds) {
      const cfgName = cfgId === DEFAULT_CONFIG_ID
        ? "Default"
        : p.configs.find(c => c.id === cfgId)?.name ?? `Config #${cfgId}`;
      const label = `${stratName} · ${cfgName} · ${capturedSymbols.length} sym · ${p.direction}`;

      queueBatch({
        label,
        startFn: async () => {
          const resolvedCfgId = cfgId === DEFAULT_CONFIG_ID ? undefined : cfgId;
          const res = await api.startBatchBacktest(
            capturedProps.strategyId, capturedSymbols, capturedProps.timeframe, capturedProps.direction,
            parseFloat(capturedProps.capital),
            capturedProps.includeCosts ? parseFloat(capturedProps.commission) : 0,
            parseInt(capturedProps.leverage), parseFloat(capturedProps.minAdv) || 0,
            resolvedCfgId, undefined, 0, mode,
            capturedProps.slippageMode,
            capturedProps.includeCosts ? (parseFloat(capturedProps.slippageValue) || 0) : 0,
            capturedProps.riskMode, parseFloat(capturedProps.riskValue) || 0,
            capturedProps.martingaleEnabled, capturedProps.martingaleType,
            parseFloat(capturedProps.martingaleMultiplier) || 2, parseInt(capturedProps.martingaleMaxStreak) || 3,
            capturedProps.volFilterEnabled, parseFloat(capturedProps.volFilterAtrCeiling) || 0,
            parseInt(capturedProps.volFilterAtrLength) || 14,
            parseInt(capturedProps.pyramiding) || 1,
            parseInt(capturedProps.minPositions) || 0,
            parseFloat(capturedProps.maxAdv) || 0,
            capturedProps.normalized,
            capturedProps.includeCosts,
            capturedProps.includeCosts ? (parseFloat(capturedProps.fundingRate) || 0) : 0,
            capturedProps.includeCosts ? (parseFloat(capturedProps.spread) || 0) : 0,
            capturedProps.prefilterEnabled, capturedProps.prefilterMode,
            parseFloat(capturedProps.prefilterHurstMin) || 0.50,
            parseInt(capturedProps.prefilterTerPctl) || 25,
            parseFloat(capturedProps.prefilterHurstMax) || 0.50,
            parseFloat(capturedProps.prefilterVrMax) || 1.0,
            parseFloat(capturedProps.prefilterMinCr) || 0.05,
            capturedProps.minDataDate || undefined,
            capturedProps.processOrdersOnClose,
            capturedProps.enforceBybitLimits,
            parseFloat(capturedProps.expectedLiveSize) || 0,
            parseFloat(capturedProps.maxMarginPerTrade) || 0,
            parseFloat(capturedProps.marginFilterTolerance) || 10,
            parseFloat(capturedProps.bybitRejectPct) || 0,
          );
          const ids = res.batch_ids || [res.batch_id];
          return {
            batchIds: ids,
            label: `Batch Backtest (${capturedSymbols.length} symbols)`,
            skippedCount: res.skipped_low_adv ?? 0,
            skippedDataDateCount: res.skipped_data_date ?? 0,
            prefilteredCount: res.prefiltered ?? 0,
          };
        },
      });
    }
  };

  // Compute combined progress for display
  const totalSymbols = Object.values(progressMap).reduce((s, prog) => s + (prog.total_symbols || 0), 0);
  const completedSymbols = Object.values(progressMap).reduce((s, prog) => s + (prog.completed_symbols || 0), 0);
  const isDualBatch = batchIds.length > 1;
  const anyLoading = Object.values(progressMap).some((prog) => prog.phase === "loading");

  const renderConfigCheckbox = (c: typeof configList[0]) => (
    <label
      key={c.id}
      className={`flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
        checkedIds.has(c.id)
          ? "bg-ag-accent/10 border border-ag-accent/30"
          : "bg-ag-dark border border-ag-border hover:border-ag-accent/20"
      }`}
    >
      <input
        type="checkbox"
        checked={checkedIds.has(c.id)}
        onChange={() => toggleConfig(c.id)}
        className="sr-only"
      />
      {checkedIds.has(c.id) ? (
        <CheckSquare size={16} className="text-ag-accent shrink-0" />
      ) : (
        <Square size={16} className="text-ag-text-muted shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{c.name}</span>
          {c.tag && (
            <span className="text-[10px] bg-blue-500/10 text-blue-400 px-1.5 py-0.5 rounded flex items-center gap-0.5">
              <FolderOpen size={8} /> {c.tag}
            </span>
          )}
        </div>
        {c.paramSummary && (
          <p className="text-[10px] text-ag-text-muted truncate">{c.paramSummary}</p>
        )}
      </div>
    </label>
  );

  return (
    <div className="space-y-4">
      {/* Mode toggle */}
      <div>
        <label className="text-xs text-ag-text-muted mb-1 block">Mode<HintTip text="Individual: each symbol gets its own capital pool and is tested independently. Portfolio: all symbols share one equity pool — position sizing uses total portfolio value." /></label>
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
        <p className="text-[10px] text-ag-text-muted mt-1">
          {mode === "individual"
            ? "Each symbol tested independently with its own capital."
            : "All symbols share one equity pool. Risk % uses total portfolio value."}
        </p>
      </div>

      {/* Symbol multi-select */}
      <div>
        <label className="text-xs text-ag-text-muted mb-1 block">Symbols</label>
        <SymbolMultiSelect dataStatus={p.dataStatus} watchlist={watchlist}
          selected={selectedSymbols} onChange={setSelectedSymbols}
          minAdv={parseFloat(p.minAdv) || 0} maxAdv={parseFloat(p.maxAdv) || 0} />
      </div>

      {/* Multi-config selection */}
      {configList.length > 1 && (
        <div className="bg-ag-card border border-ag-border rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">
              Configs to Run
              <HintTip text="Tick multiple configs to queue them all at once. Each checked config runs as a separate batch with the same symbols and settings." />
            </h3>
            <button
              onClick={toggleAll}
              className="text-xs text-ag-accent hover:text-ag-accent/80 transition-colors"
            >
              {checkedIds.size === configList.length ? "Deselect All" : "Select All"}
            </button>
          </div>

          {groupedConfigs ? (
            /* Grouped by tag */
            <div className="space-y-3">
              {groupedConfigs.map(({ tag: tagName, items }) => (
                <div key={tagName} className="space-y-1.5">
                  <button
                    onClick={() => toggleTagGroup(items)}
                    className="flex items-center gap-1.5 text-xs text-ag-text-muted hover:text-ag-text transition-colors"
                  >
                    <FolderOpen size={10} />
                    <span className="font-medium">{tagName}</span>
                    <span className="opacity-60">({items.length})</span>
                    <span className="text-[10px] ml-1">
                      {items.every(c => checkedIds.has(c.id)) ? "(all selected)" : ""}
                    </span>
                  </button>
                  <div className="grid grid-cols-1 gap-1.5 pl-3">
                    {items.map(renderConfigCheckbox)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            /* Flat list */
            <div className="grid grid-cols-1 gap-1.5">
              {configList.map(renderConfigCheckbox)}
            </div>
          )}

          <p className="text-[10px] text-ag-text-muted">
            {checkedIds.size} config{checkedIds.size !== 1 ? "s" : ""} selected
            {checkedIds.size > 1 && ` — will queue ${checkedIds.size} batches`}
          </p>
        </div>
      )}

      {/* Run / Stop / Stop All buttons */}
      <div className="flex gap-2">
        <button onClick={handleRun} disabled={selectedSymbols.length === 0 || !p.strategyId || submitting || checkedIds.size === 0}
          className="flex-1 flex items-center justify-center gap-2 py-3 bg-ag-accent text-white rounded-lg font-medium disabled:opacity-50 hover:bg-ag-accent/90">
          {running ? <ListOrdered size={18} /> : <Play size={18} />}
          {checkedIds.size > 1
            ? `${running ? "Queue" : "Run"} ${checkedIds.size} Configs (${selectedSymbols.length} sym each)`
            : running
              ? `Queue Batch (${selectedSymbols.length} symbols)`
              : `Run Batch (${selectedSymbols.length} symbols)`}
        </button>
        {running && latestBatchTask && (
          <button
            onClick={() => { if (confirm("Stop the current batch? Completed symbols will be kept.")) cancelTask(latestBatchTask.id); }}
            className="px-4 py-3 bg-ag-red/15 text-ag-red border border-ag-red/30 rounded-lg font-medium hover:bg-ag-red/25 transition-colors"
          >
            Stop
          </button>
        )}
        {(running || batchQueue.length > 0) && (
          <button
            onClick={() => { if (confirm("Stop ALL running batches and clear the queue?")) cancelAllTasks(); }}
            className="px-4 py-3 bg-ag-red/15 text-ag-red border border-ag-red/30 rounded-lg font-medium hover:bg-ag-red/25 transition-colors"
            title="Stop all running tasks and clear the queue"
          >
            Stop All
          </button>
        )}
      </div>

      {/* Info: Long+Short creates 2 batches */}
      {p.direction === "long_plus_short" && !running && results.length === 0 && (
        <p className="text-[10px] text-ag-text-muted">
          Long+Short creates 2 separate batches (one Long, one Short). You can merge them later from Saved Backtests.
        </p>
      )}

      {/* Queue display */}
      {batchQueue.length > 0 && (
        <div className="bg-ag-card border border-ag-border rounded-xl p-4">
          <h2 className="text-sm font-medium mb-2 flex items-center gap-2">
            <ListOrdered size={14} />
            Queue ({batchQueue.length} waiting)
          </h2>
          <div className="space-y-1.5">
            {batchQueue.map((item, i) => (
              <div key={item.id} className="flex items-center justify-between bg-ag-dark rounded-lg px-3 py-2">
                <span className="text-xs text-ag-text-muted">
                  <span className="text-ag-text font-medium mr-2">#{i + 1}</span>
                  {item.label}
                </span>
                <button onClick={() => removeFromQueue(item.id)}
                  className="p-1 text-ag-text-muted hover:text-ag-red transition-colors"
                  title="Remove from queue">
                  <X size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Error / Skip notice */}
      {(error || latestBatchTask?.error) && (
        <div className="flex items-center gap-2 p-3 bg-ag-red/10 border border-ag-red/30 rounded-lg text-ag-red text-sm">
          <AlertCircle size={16} /> {error || latestBatchTask?.error}
        </div>
      )}
      {skippedCount > 0 && (
        <div className="flex items-center gap-2 p-3 bg-ag-yellow/10 border border-ag-yellow/30 rounded-lg text-ag-yellow text-sm">
          <AlertCircle size={16} /> {skippedCount} symbol(s) skipped — ADV below minimum
        </div>
      )}
      {skippedDataDateCount > 0 && (
        <div className="flex items-center gap-2 p-3 bg-orange-500/10 border border-orange-500/30 rounded-lg text-orange-400 text-sm">
          <AlertCircle size={16} /> {skippedDataDateCount} symbol(s) skipped — data too recent (listed after cutoff date)
        </div>
      )}
      {prefilteredCount > 0 && (
        <div className="flex items-center gap-2 p-3 bg-purple-500/10 border border-purple-500/30 rounded-lg text-purple-400 text-sm">
          <AlertCircle size={16} /> {prefilteredCount} symbol(s) pre-filtered out by L0 structural quality check
        </div>
      )}

      {/* Progress — persists across page navigation via TaskManager */}
      {running && batchIds.length > 0 && (
        <div className="bg-ag-card border border-ag-border rounded-xl p-5">
          <h2 className="text-sm font-medium mb-3">
            {anyLoading
              ? "Loading candle data..."
              : isDualBatch
                ? `Progress (${completedSymbols}/${totalSymbols} total across Long & Short)`
                : `Progress (${completedSymbols}/${totalSymbols})`}
          </h2>
          {anyLoading ? (
            <div className="flex items-center gap-2 text-xs text-ag-text-muted">
              <Loader2 size={14} className="animate-spin text-ag-accent" />
              Pre-loading candle data for all symbols... backtesting will start shortly.
            </div>
          ) : isDualBatch ? (
            <div className="space-y-3">
              {batchIds.map((bid, i) => {
                const prog = progressMap[bid];
                if (!prog) return null;
                const label = i === 0 ? "Long" : "Short";
                return (
                  <div key={bid}>
                    <p className="text-xs text-ag-text-muted mb-1">{label} Batch</p>
                    <BatchProgressComp progress={prog} startedAt={latestBatchTask?.startedAt} />
                  </div>
                );
              })}
            </div>
          ) : (
            <BatchProgressComp
              progress={progressMap[batchIds[0]]}
              startedAt={latestBatchTask?.startedAt}
            />
          )}
        </div>
      )}

      {/* Results */}
      {results.map((result, i) => (
        <div key={result.batch.id} className="bg-ag-card border border-ag-border rounded-xl p-5">
          <h2 className="text-lg font-bold mb-4">
            {isDualBatch ? `Results — ${i === 0 ? "Long" : "Short"}` : "Results"}
          </h2>
          <BatchResultsSummary result={result}
            onViewSymbol={(runId) => window.open(`/backtest?run=${runId}`, "_blank")} />
        </div>
      ))}
    </div>
  );
}
