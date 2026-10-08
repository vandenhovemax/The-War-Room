"use client";
import { useState, useEffect, useCallback } from "react";
import { Layers, Play, Loader2, AlertCircle, Wrench } from "lucide-react";
import {
  api, StrategyItem, DataStatusItem, WatchlistItem,
  ParameterDefinition, StrategyConfigItem,
} from "@/lib/api";
import DirectionFilter from "@/components/backtest/DirectionFilter";
import SymbolMultiSelect from "@/components/backtest/SymbolMultiSelect";
import BatchProgressComp from "@/components/backtest/BatchProgress";
import BatchResultsSummary from "@/components/backtest/BatchResultsSummary";
import ParameterForm from "@/components/strategy/ParameterForm";
import { useTaskManager } from "@/contexts/TaskManager";

export default function BatchBacktestPage() {
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [dataStatus, setDataStatus] = useState<DataStatusItem[]>([]);
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  // Form
  const [strategyId, setStrategyId] = useState<number>(0);
  const [direction, setDirection] = useState<"long" | "short" | "both" | "long_plus_short">("both");
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [timeframe, setTimeframe] = useState("30");
  const [capital, setCapital] = useState("10000");
  const [commission, setCommission] = useState("0.08");
  const [includeCosts, setIncludeCosts] = useState(false);
  const [slippageMode, setSlippageMode] = useState<"pct" | "pips" | "risk_pct">("pct");
  const [slippageValue, setSlippageValue] = useState("0");
  const [fundingRate, setFundingRate] = useState("0");
  const [spread, setSpread] = useState("0");
  const [leverage, setLeverage] = useState("1");
  const [riskPct, setRiskPct] = useState("2.5");
  const [minAdv, setMinAdv] = useState("5000000");
  const [minPositions, setMinPositions] = useState("0");
  const [mode, setMode] = useState<"individual" | "portfolio">("individual");

  const [minDataDate, setMinDataDate] = useState("");

  // L0 Pre-filter
  const [prefilterEnabled, setPrefilterEnabled] = useState(false);
  const [prefilterMode, setPrefilterMode] = useState<"trend" | "meanrev">("trend");
  const [prefilterHurstMin, setPrefilterHurstMin] = useState("0.50");
  const [prefilterTerPctl, setPrefilterTerPctl] = useState("25");
  const [prefilterHurstMax, setPrefilterHurstMax] = useState("0.50");
  const [prefilterVrMax, setPrefilterVrMax] = useState("1.0");
  const [prefilterMinCr, setPrefilterMinCr] = useState("0.05");

  // Strategy params
  const [paramDefs, setParamDefs] = useState<ParameterDefinition[]>([]);
  const [configs, setConfigs] = useState<StrategyConfigItem[]>([]);
  const [configMode, setConfigMode] = useState<"default" | "custom" | number>("default");
  const [strategyInputs, setStrategyInputs] = useState<Record<string, number | boolean | string>>({});

  // Batch state — from global TaskManager (persists across navigation)
  const { startBatch, latestBatchTask } = useTaskManager();
  const running = latestBatchTask?.status === "running";
  const batchIds = latestBatchTask?.batchIds ?? [];
  const progressMap = latestBatchTask?.progressMap ?? {};
  const results = latestBatchTask?.results ?? [];
  const [error, setError] = useState("");
  const skippedCount = latestBatchTask?.skippedCount ?? 0;
  const [prefilteredCount, setPrefilteredCount] = useState(0);
  const [skippedDataDateCount, setSkippedDataDateCount] = useState(0);

  // Repair All state
  const [repairingAll, setRepairingAll] = useState(false);
  const [repairAllResult, setRepairAllResult] = useState<string | null>(null);

  const handleRepairAll = async () => {
    setRepairingAll(true);
    setRepairAllResult(null);
    try {
      const res = await api.repairAllStructuralMetrics();
      setRepairAllResult(
        `Done — ${res.batches_processed} batches, ${res.total_repaired} symbols repaired`
      );
    } catch (e: unknown) {
      setRepairAllResult(e instanceof Error ? e.message : "Repair failed");
    } finally {
      setRepairingAll(false);
    }
  };

  const loadData = useCallback(async () => {
    try {
      const [strats, status, wl] = await Promise.all([
        api.strategies(), api.dataStatus(), api.watchlist(),
      ]);
      setStrategies(strats);
      setDataStatus(status);
      setWatchlist(wl.map((w: WatchlistItem) => w.symbol));
      if (strats.length > 0 && !strategyId) setStrategyId(strats[0].id);
    } catch { setError("Failed to load data"); }
    setLoading(false);
  }, [strategyId]);

  useEffect(() => { loadData(); }, [loadData]);

  // Load params when strategy changes
  useEffect(() => {
    if (!strategyId) return;
    (async () => {
      try {
        const [params, cfgs] = await Promise.all([
          api.strategyParams(strategyId), api.strategyConfigs(strategyId),
        ]);
        setParamDefs(params);
        setConfigs(cfgs);
        setConfigMode("default");
        const defaults: Record<string, number | boolean | string> = {};
        params.forEach((p: ParameterDefinition) => { defaults[p.name] = p.default; });
        setStrategyInputs(defaults);
      } catch { /* no params */ }
    })();
  }, [strategyId]);

  const handleRun = async () => {
    if (!strategyId || selectedSymbols.length === 0) return;
    setError("");
    try {
      const cfgId = typeof configMode === "number" ? configMode : undefined;
      const inputs = configMode === "custom" ? strategyInputs : undefined;
      const res = await api.startBatchBacktest(
        strategyId, selectedSymbols, timeframe, direction,
        parseFloat(capital),
        parseFloat(commission),
        parseInt(leverage), parseFloat(minAdv) || 0, cfgId, inputs,
        parseFloat(riskPct) || 0, mode,
        slippageMode,
        parseFloat(slippageValue) || 0,
        "pct", 0, false, "anti", 2, 3, false, 0, 14, 1,
        parseInt(minPositions) || 0,
        0, true, includeCosts,
        includeCosts ? (parseFloat(fundingRate) || 0) : 0,
        includeCosts ? (parseFloat(spread) || 0) : 0,
        prefilterEnabled, prefilterMode,
        parseFloat(prefilterHurstMin) || 0.50,
        parseInt(prefilterTerPctl) || 25,
        parseFloat(prefilterHurstMax) || 0.50,
        parseFloat(prefilterVrMax) || 1.0,
        parseFloat(prefilterMinCr) || 0.05,
        minDataDate || undefined,
      );
      const ids = res.batch_ids || [res.batch_id];
      const label = `Batch Backtest (${selectedSymbols.length} symbols)`;
      setPrefilteredCount(res.prefiltered ?? 0);
      setSkippedDataDateCount(res.skipped_data_date ?? 0);
      startBatch(ids, label, res.skipped_low_adv ?? 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start batch");
    }
  };

  const handleViewSymbol = (runId: number) => {
    window.open(`/backtest?run=${runId}`, "_blank");
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="animate-spin text-ag-accent" size={32} />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Layers className="text-ag-accent" size={28} />
        <div>
          <h1 className="text-2xl font-bold">Batch Backtest</h1>
          <p className="text-sm text-ag-text-muted">Test one strategy across multiple symbols</p>
        </div>
      </div>

      {(error || latestBatchTask?.error) && (
        <div className="flex items-center gap-2 p-3 bg-ag-red/10 border border-ag-red/30 rounded-lg text-ag-red text-sm">
          <AlertCircle size={16} /> {error || latestBatchTask?.error}
        </div>
      )}

      {/* Config section */}
      <div className="bg-ag-card rounded-xl p-5 space-y-4">
        {/* Row 1: Strategy + Direction + Timeframe + Min Data Date */}
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
          <div>
            <label className="text-xs text-ag-text-muted mb-1 block">Strategy</label>
            <select value={strategyId} onChange={(e) => setStrategyId(Number(e.target.value))}
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm">
              {strategies.filter((s) => s.has_python).map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-ag-text-muted mb-1 block">Direction</label>
            <DirectionFilter value={direction} onChange={setDirection} />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted mb-1 block">Timeframe</label>
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm">
              {["1","3","5","15","30","60","120","240","D"].map((tf) => (
                <option key={tf} value={tf}>{tf === "D" ? "1D" : `${tf}m`}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-ag-text-muted mb-1 block">Data Must Start Before</label>
            <input type="date" value={minDataDate} onChange={(e) => setMinDataDate(e.target.value)}
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm" />
            {minDataDate && (
              <p className="text-[10px] text-ag-text-muted mt-0.5">
                Skips assets listed after {minDataDate}
              </p>
            )}
          </div>
        </div>

        {/* Trading Costs toggle */}
        <div className="flex items-center gap-3 py-1">
          <button
            type="button"
            onClick={() => setIncludeCosts(!includeCosts)}
            className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
              includeCosts ? "bg-ag-accent" : "bg-ag-border"
            }`}
          >
            <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
              includeCosts ? "translate-x-[18px]" : "translate-x-[3px]"
            }`} />
          </button>
          <span className="text-xs text-ag-text-muted">
            Trading Costs {includeCosts ? <span className="text-ag-accent font-medium">ON</span> : <span className="text-ag-text-muted">OFF</span>}
          </span>
          {!includeCosts && (
            <span className="text-[10px] text-ag-text-muted/60">All trading costs disabled — pure strategy validation</span>
          )}
        </div>

        {/* Row 2: Capital, Commission, Slippage, Funding, Spread, Risk %, Leverage, Min ADV */}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-4">
          <div>
            <label className="text-xs text-ag-text-muted mb-1 block">Capital (USDT)</label>
            <input value={capital} onChange={(e) => setCapital(e.target.value)}
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm" />
          </div>
          <div className={!includeCosts ? "opacity-40 pointer-events-none" : ""}>
            <label className="text-xs text-ag-text-muted mb-1 block">Commission %</label>
            <input value={commission} onChange={(e) => setCommission(e.target.value)}
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm" />
          </div>
          <div className={!includeCosts ? "opacity-40 pointer-events-none" : ""}>
            <label className="text-xs text-ag-text-muted mb-1 block">Slippage</label>
            <div className="flex gap-1">
              <select value={slippageMode} onChange={(e) => setSlippageMode(e.target.value as "pct" | "pips" | "risk_pct")}
                className="w-20 px-1.5 py-2 bg-ag-darker border border-ag-border rounded-l text-xs">
                <option value="pct">% Price</option>
                <option value="pips">Pips</option>
                <option value="risk_pct">% Risk</option>
              </select>
              <input value={slippageValue} onChange={(e) => setSlippageValue(e.target.value)}
                placeholder="0"
                className="flex-1 min-w-0 px-2 py-2 bg-ag-darker border border-ag-border rounded-r text-sm" />
            </div>
          </div>
          <div className={!includeCosts ? "opacity-40 pointer-events-none" : ""}>
            <label className="text-xs text-ag-text-muted mb-1 block">Funding %/day</label>
            <input value={fundingRate} onChange={(e) => setFundingRate(e.target.value)}
              placeholder="0"
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm" />
          </div>
          <div className={!includeCosts ? "opacity-40 pointer-events-none" : ""}>
            <label className="text-xs text-ag-text-muted mb-1 block">Spread %</label>
            <input value={spread} onChange={(e) => setSpread(e.target.value)}
              placeholder="0"
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted mb-1 block">Risk % per Trade</label>
            <input value={riskPct} onChange={(e) => setRiskPct(e.target.value)}
              placeholder="0 = manual leverage"
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted mb-1 block">Leverage</label>
            <input value={leverage} onChange={(e) => setLeverage(e.target.value)}
              disabled={parseFloat(riskPct) > 0}
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm disabled:opacity-40" />
            {parseFloat(riskPct) > 0 && (
              <p className="text-[10px] text-ag-accent mt-0.5">Auto-sized from SL</p>
            )}
          </div>
          <div>
            <label className="text-xs text-ag-text-muted mb-1 block">Min ADV (USDT)</label>
            <input value={minAdv} onChange={(e) => setMinAdv(e.target.value)}
              placeholder="0 = no filter"
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm" />
            {parseFloat(minAdv) > 0 && (
              <p className="text-[10px] text-ag-text-muted mt-0.5">
                Skips low-volume assets
              </p>
            )}
          </div>
        </div>

        {/* Row 3: Min Positions */}
        <div className="grid grid-cols-2 sm:grid-cols-6 gap-4">
          <div>
            <label className="text-xs text-ag-text-muted mb-1 block">Min Positions</label>
            <input value={minPositions} onChange={(e) => setMinPositions(e.target.value)}
              placeholder="0 = no filter"
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm" />
            {parseInt(minPositions) > 0 && (
              <p className="text-[10px] text-ag-text-muted mt-0.5">
                Excludes assets with fewer trades
              </p>
            )}
          </div>
        </div>

        {/* L0 Pre-Filter */}
        <div className="border border-ag-border rounded-lg p-3 space-y-3">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setPrefilterEnabled(!prefilterEnabled)}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                prefilterEnabled ? "bg-ag-accent" : "bg-ag-border"
              }`}
            >
              <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                prefilterEnabled ? "translate-x-[18px]" : "translate-x-[3px]"
              }`} />
            </button>
            <span className="text-xs font-medium">
              L0 Pre-Filter {prefilterEnabled ? <span className="text-ag-accent">ON</span> : <span className="text-ag-text-muted">OFF</span>}
            </span>
            <span className="text-[10px] text-ag-text-muted">
              Skip assets that fail structural quality checks before backtesting
            </span>
          </div>

          {prefilterEnabled && (
            <div className="flex items-center gap-4 flex-wrap pl-12">
              <div className="flex items-center gap-2">
                <label className="text-[10px] text-ag-text-muted">Mode:</label>
                <select value={prefilterMode} onChange={(e) => setPrefilterMode(e.target.value as "trend" | "meanrev")}
                  className="bg-ag-darker border border-ag-border rounded px-2 py-1 text-xs">
                  <option value="trend">Trend</option>
                  <option value="meanrev">Mean-Rev</option>
                </select>
              </div>
              {prefilterMode === "trend" ? (
                <>
                  <div className="flex items-center gap-1.5">
                    <label className="text-[10px] text-ag-text-muted">Min Hurst:</label>
                    <input type="number" min={0.3} max={0.8} step={0.01} value={prefilterHurstMin}
                      onChange={(e) => setPrefilterHurstMin(e.target.value)}
                      className="w-16 bg-ag-darker border border-ag-border rounded px-2 py-1 text-xs text-center" />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <label className="text-[10px] text-ag-text-muted">TER Pctl:</label>
                    <input type="number" min={0} max={75} step={5} value={prefilterTerPctl}
                      onChange={(e) => setPrefilterTerPctl(e.target.value)}
                      className="w-16 bg-ag-darker border border-ag-border rounded px-2 py-1 text-xs text-center" />
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center gap-1.5">
                    <label className="text-[10px] text-ag-text-muted">Max Hurst:</label>
                    <input type="number" min={0.3} max={0.7} step={0.01} value={prefilterHurstMax}
                      onChange={(e) => setPrefilterHurstMax(e.target.value)}
                      className="w-16 bg-ag-darker border border-ag-border rounded px-2 py-1 text-xs text-center" />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <label className="text-[10px] text-ag-text-muted">Max VR:</label>
                    <input type="number" min={0.5} max={1.5} step={0.05} value={prefilterVrMax}
                      onChange={(e) => setPrefilterVrMax(e.target.value)}
                      className="w-16 bg-ag-darker border border-ag-border rounded px-2 py-1 text-xs text-center" />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <label className="text-[10px] text-ag-text-muted">Min Cross Rate:</label>
                    <input type="number" min={0} max={0.5} step={0.01} value={prefilterMinCr}
                      onChange={(e) => setPrefilterMinCr(e.target.value)}
                      className="w-16 bg-ag-darker border border-ag-border rounded px-2 py-1 text-xs text-center" />
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        {/* Strategy params (if any) */}
        {paramDefs.length > 0 && (
          <div>
            <label className="text-xs text-ag-text-muted mb-1 block">Config</label>
            <select value={configMode} onChange={(e) => {
              const v = e.target.value;
              if (v === "default") setConfigMode("default");
              else if (v === "custom") setConfigMode("custom");
              else setConfigMode(Number(v));
            }}
              className="w-full px-3 py-2 bg-ag-darker border border-ag-border rounded text-sm mb-2">
              <option value="default">Default Parameters</option>
              {configs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              <option value="custom">Custom...</option>
            </select>
            {configMode === "custom" && (
              <ParameterForm definitions={paramDefs} values={strategyInputs} onChange={setStrategyInputs} />
            )}
          </div>
        )}

        {/* Mode toggle */}
        <div>
          <label className="text-xs text-ag-text-muted mb-1 block">Mode</label>
          <div className="flex gap-1 bg-ag-darker rounded-lg p-1">
            <button
              type="button"
              onClick={() => setMode("individual")}
              className={`flex-1 px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                mode === "individual"
                  ? "bg-ag-accent text-white"
                  : "text-ag-text-muted hover:text-ag-text"
              }`}
            >
              Individual
            </button>
            <button
              type="button"
              onClick={() => setMode("portfolio")}
              className={`flex-1 px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                mode === "portfolio"
                  ? "bg-ag-accent text-white"
                  : "text-ag-text-muted hover:text-ag-text"
              }`}
            >
              Portfolio
            </button>
          </div>
          <p className="text-[10px] text-ag-text-muted mt-1">
            {mode === "individual"
              ? "Each symbol tested independently with its own capital."
              : "All symbols share one equity pool. Risk % uses total portfolio value."}
          </p>
        </div>

        {/* Symbol selector */}
        <div>
          <label className="text-xs text-ag-text-muted mb-1 block">Symbols</label>
          <SymbolMultiSelect
            dataStatus={dataStatus} watchlist={watchlist}
            selected={selectedSymbols} onChange={setSelectedSymbols}
            minAdv={parseFloat(minAdv) || 0}
          />
        </div>

        {/* Run button */}
        <button onClick={handleRun} disabled={running || selectedSymbols.length === 0 || !strategyId}
          className="w-full flex items-center justify-center gap-2 py-3 bg-ag-accent text-white rounded-lg font-medium disabled:opacity-50 hover:bg-ag-accent/90 transition-colors">
          {running ? <Loader2 className="animate-spin" size={18} /> : <Play size={18} />}
          {running ? "Running..." : `Run Batch (${selectedSymbols.length} symbols)`}
        </button>
      </div>

      {/* ADV skip notice */}
      {skippedCount > 0 && (
        <div className="flex items-center gap-2 p-3 bg-ag-yellow/10 border border-ag-yellow/30 rounded-lg text-ag-yellow text-sm">
          <AlertCircle size={16} /> {skippedCount} symbol(s) skipped — ADV below minimum threshold
        </div>
      )}
      {skippedDataDateCount > 0 && (
        <div className="flex items-center gap-2 p-3 bg-orange-500/10 border border-orange-500/30 rounded-lg text-orange-400 text-sm">
          <AlertCircle size={16} /> {skippedDataDateCount} symbol(s) skipped — no data before {minDataDate}
        </div>
      )}
      {prefilteredCount > 0 && (
        <div className="flex items-center gap-2 p-3 bg-purple-500/10 border border-purple-500/30 rounded-lg text-purple-400 text-sm">
          <AlertCircle size={16} /> {prefilteredCount} symbol(s) pre-filtered out by L0 structural quality check
        </div>
      )}

      {/* Progress — persists across page navigation via TaskManager */}
      {running && batchIds.length > 0 && Object.keys(progressMap).length > 0 && (
        <div className="bg-ag-card rounded-xl p-5">
          <h2 className="text-sm font-medium mb-3">Progress</h2>
          {batchIds.length > 1 ? (
            <div className="space-y-3">
              {batchIds.map((bid, i) => {
                const prog = progressMap[bid];
                if (!prog) return null;
                return (
                  <div key={bid}>
                    <p className="text-xs text-ag-text-muted mb-1">{i === 0 ? "Long" : "Short"} Batch</p>
                    <BatchProgressComp progress={prog} />
                  </div>
                );
              })}
            </div>
          ) : progressMap[batchIds[0]] ? (
            <BatchProgressComp progress={progressMap[batchIds[0]]} />
          ) : null}
        </div>
      )}

      {/* Repair All Batches */}
      {!running && (
        <div className="flex items-center gap-3">
          <button
            onClick={handleRepairAll}
            disabled={repairingAll}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg border border-ag-border hover:border-ag-accent/50 text-ag-text-muted hover:text-ag-text transition-colors disabled:opacity-50"
            title="Re-compute OHLCV + PnL structural metrics for ALL completed batches"
          >
            {repairingAll ? <Loader2 size={16} className="animate-spin" /> : <Wrench size={16} />}
            {repairingAll ? "Repairing All Batches..." : "Repair All Structural Metrics"}
          </button>
          {repairAllResult && (
            <span className="text-xs text-ag-green">{repairAllResult}</span>
          )}
        </div>
      )}

      {/* Results */}
      {results.map((result, i) => (
        <div key={result.batch.id} className="bg-ag-card rounded-xl p-5">
          <h2 className="text-lg font-bold mb-4">
            {batchIds.length > 1 ? `Results — ${i === 0 ? "Long" : "Short"}` : "Results"}
          </h2>
          <BatchResultsSummary result={result} onViewSymbol={handleViewSymbol} />
        </div>
      ))}
    </div>
  );
}
