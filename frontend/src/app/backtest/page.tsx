"use client";
import { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { Loader2 } from "lucide-react";
import { api, StrategyItem, DataStatusItem, ParameterDefinition, StrategyConfigItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import ParameterForm from "@/components/strategy/ParameterForm";
import type { DirectionValue } from "@/components/backtest/DirectionFilter";
import BacktestConfigPanel from "@/components/backtest/BacktestConfigPanel";
import PositionSizingSection from "@/components/backtest/PositionSizingSection";

// Lazy-load tab content — only loads when the tab is first activated
const SingleBacktestTab = lazy(() => import("@/components/backtest/SingleBacktestTab"));
const BatchBacktestTab = lazy(() => import("@/components/backtest/BatchBacktestTab"));
const SweepTab = lazy(() => import("@/components/backtest/SweepTab"));
const WFOTab = lazy(() => import("@/components/backtest/WFOTab"));
const SavedBacktestsTab = lazy(() => import("@/components/backtest/SavedBacktestsTab"));

type TopTab = "fixed" | "wfo" | "history";
type SubTab = "single" | "batch" | "sweep";
type WfoSubTab = "wfas" | "wfo";

export default function BacktestPage() {
  const { addToast } = useTaskManager();
  const [topTab, setTopTab] = useState<TopTab>("fixed");
  const [subTab, setSubTab] = useState<SubTab>("single");
  const [wfoSubTab, setWfoSubTab] = useState<WfoSubTab>("wfas");
  const [loading, setLoading] = useState(true);

  // Shared data
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [dataStatus, setDataStatus] = useState<DataStatusItem[]>([]);

  // Shared config
  const [strategyId, setStrategyId] = useState<number>(0);
  const [direction, setDirection] = useState<DirectionValue>("both");
  const [timeframe, setTimeframe] = useState("30");
  const [capital, setCapital] = useState("10000");
  const [commission, setCommission] = useState("0.08");
  const [includeCosts, setIncludeCosts] = useState(false);
  const [slippageMode, setSlippageMode] = useState<"pct" | "pips" | "risk_pct">("pct");
  const [slippageValue, setSlippageValue] = useState("0");
  const [fundingRate, setFundingRate] = useState("0");
  const [spread, setSpread] = useState("0");
  const [leverage, setLeverage] = useState("1");
  const [pyramiding, setPyramiding] = useState("1");
  const [minAdv, setMinAdv] = useState("5000000");
  const [maxAdv, setMaxAdv] = useState("0");
  const [minPositions, setMinPositions] = useState("0");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [minDataDate, setMinDataDate] = useState("");

  // L0 Pre-filter
  const [prefilterEnabled, setPrefilterEnabled] = useState(false);
  const [prefilterMode, setPrefilterMode] = useState<"trend" | "meanrev">("trend");
  const [prefilterHurstMin, setPrefilterHurstMin] = useState("0.50");
  const [prefilterTerPctl, setPrefilterTerPctl] = useState("25");
  const [prefilterHurstMax, setPrefilterHurstMax] = useState("0.50");
  const [prefilterVrMax, setPrefilterVrMax] = useState("1.0");
  const [prefilterMinCr, setPrefilterMinCr] = useState("0.05");

  // Order fill timing
  const [processOrdersOnClose, setProcessOrdersOnClose] = useState(true);

  // Position sizing
  const [normalized, setNormalized] = useState(true);
  const [riskMode, setRiskMode] = useState("fixed");
  const [riskValue, setRiskValue] = useState("100");
  const [martingaleEnabled, setMartingaleEnabled] = useState(false);
  const [martingaleType, setMartingaleType] = useState("anti");
  const [martingaleMultiplier, setMartingaleMultiplier] = useState("2");
  const [martingaleMaxStreak, setMartingaleMaxStreak] = useState("3");
  const [volFilterEnabled, setVolFilterEnabled] = useState(false);
  const [volFilterAtrCeiling, setVolFilterAtrCeiling] = useState("0");
  const [volFilterAtrLength, setVolFilterAtrLength] = useState("14");
  const [enforceBybitLimits, setEnforceBybitLimits] = useState(false);
  const [expectedLiveSize, setExpectedLiveSize] = useState("0");
  const [maxMarginPerTrade, setMaxMarginPerTrade] = useState("0");
  const [marginFilterTolerance, setMarginFilterTolerance] = useState("10");
  const [bybitRejectPct, setBybitRejectPct] = useState("0");

  // Strategy params
  const [paramDefs, setParamDefs] = useState<ParameterDefinition[]>([]);
  const [configs, setConfigs] = useState<StrategyConfigItem[]>([]);
  const [configMode, setConfigMode] = useState<"default" | "custom" | number>("default");
  const [strategyInputs, setStrategyInputs] = useState<Record<string, number | boolean | string>>({});

  // Load strategies + data status
  const loadData = useCallback(async () => {
    try {
      const [strats, status] = await Promise.all([api.strategies(), api.dataStatus()]);
      setStrategies(strats);
      setDataStatus(status);
      if (strats.length > 0 && !strategyId) setStrategyId(strats[0].id);
    } catch { addToast("Failed to load backtest data", "error"); }
    setLoading(false);
  }, [strategyId]);

  useEffect(() => { loadData(); }, [loadData]);

  // Load params when strategy changes
  useEffect(() => {
    if (!strategyId) { setParamDefs([]); setConfigs([]); return; }
    (async () => {
      try {
        const [defs, cfgs] = await Promise.all([
          api.strategyParams(strategyId), api.strategyConfigs(strategyId),
        ]);
        setParamDefs(defs);
        setConfigs(cfgs);
        setConfigMode("default");
        const defaults: Record<string, number | boolean | string> = {};
        for (const p of defs) defaults[p.name] = p.default;
        setStrategyInputs(defaults);
        // Auto-fill pyramiding from strategy's "Max Concurrent Trades" default
        const mct = defs.find((d) => /^Max Concurrent/i.test(d.name));
        if (mct && typeof mct.default === "number") setPyramiding(String(mct.default));
      } catch { setParamDefs([]); setConfigs([]); }
    })();
  }, [strategyId]);

  // Auto-bump leverage to 25 when risk sizing is enabled (was at 1)
  const riskSizingActive = parseFloat(riskValue) > 0;
  useEffect(() => {
    if (riskSizingActive && parseInt(leverage) <= 1) setLeverage("25");
  }, [riskSizingActive, leverage]);

  const handleConfigModeChange = (mode: "default" | "custom" | number) => {
    setConfigMode(mode);
    if (mode === "default") {
      const defaults: Record<string, number | boolean | string> = {};
      for (const p of paramDefs) defaults[p.name] = p.default;
      setStrategyInputs(defaults);
    } else if (typeof mode === "number") {
      const cfg = configs.find((c) => c.id === mode);
      if (cfg) setStrategyInputs({ ...cfg.parameter_values });
    }
  };

  if (loading) {
    return <div className="flex items-center justify-center h-64"><Loader2 className="animate-spin text-ag-accent" size={32} /></div>;
  }

  const shared = {
    strategyId, strategies, dataStatus, direction, timeframe,
    capital, commission, includeCosts, slippageMode, slippageValue, leverage, pyramiding,
    minAdv, maxAdv, minPositions, startDate, endDate, minDataDate,
    fundingRate, spread,
    prefilterEnabled, prefilterMode, prefilterHurstMin, prefilterTerPctl,
    prefilterHurstMax, prefilterVrMax, prefilterMinCr,
    configMode, configs, strategyInputs,
    normalized, riskMode, riskValue,
    martingaleEnabled, martingaleType, martingaleMultiplier, martingaleMaxStreak,
    volFilterEnabled, volFilterAtrCeiling, volFilterAtrLength,
    processOrdersOnClose, enforceBybitLimits, expectedLiveSize,
    maxMarginPerTrade, marginFilterTolerance, bybitRejectPct,
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Backtester</h1>

      {/* Top tabs */}
      <div className="flex gap-1 bg-ag-card border border-ag-border rounded-lg p-1 w-fit">
        <TabBtn active={topTab === "fixed"} onClick={() => setTopTab("fixed")}>Fixed Parameters</TabBtn>
        <TabBtn active={topTab === "wfo"} onClick={() => setTopTab("wfo")}>Walk Forwards</TabBtn>
        <TabBtn active={topTab === "history"} onClick={() => setTopTab("history")}>Saved Backtests</TabBtn>
      </div>

      <Suspense fallback={<div className="flex items-center justify-center h-32"><Loader2 className="animate-spin text-ag-accent" size={24} /></div>}>
      {topTab === "wfo" && (
        <>
          <div className="flex gap-1 bg-ag-dark rounded-lg p-1 w-fit">
            <TabBtn active={wfoSubTab === "wfas"} onClick={() => setWfoSubTab("wfas")}>WFAS</TabBtn>
            <TabBtn active={wfoSubTab === "wfo"} onClick={() => setWfoSubTab("wfo")}>WFO</TabBtn>
          </div>
          {wfoSubTab === "wfas" && <WFOTab />}
          {wfoSubTab === "wfo" && (
            <div className="text-center py-12">
              <p className="text-ag-text-muted text-sm">Walk Forward Optimisation — coming soon.</p>
            </div>
          )}
        </>
      )}
      {topTab === "history" && <SavedBacktestsTab />}

      {topTab === "fixed" && (
        <>
          {/* Sub-tabs */}
          <div className="flex gap-1 bg-ag-dark rounded-lg p-1 w-fit">
            <TabBtn active={subTab === "single"} onClick={() => setSubTab("single")}>Single Strategy</TabBtn>
            <TabBtn active={subTab === "batch"} onClick={() => setSubTab("batch")}>Batch Test</TabBtn>
            <TabBtn active={subTab === "sweep"} onClick={() => setSubTab("sweep")}>Sweep</TabBtn>
          </div>

          {/* Shared config panel */}
          <BacktestConfigPanel
            strategyId={strategyId} setStrategyId={setStrategyId}
            strategies={strategies} direction={direction} setDirection={setDirection}
            timeframe={timeframe} setTimeframe={setTimeframe}
            capital={capital} setCapital={setCapital}
            commission={commission} setCommission={setCommission}
            includeCosts={includeCosts} setIncludeCosts={setIncludeCosts}
            slippageMode={slippageMode} setSlippageMode={setSlippageMode}
            slippageValue={slippageValue} setSlippageValue={setSlippageValue}
            leverage={leverage} setLeverage={setLeverage}
            pyramiding={pyramiding} setPyramiding={setPyramiding}
            minAdv={minAdv} setMinAdv={setMinAdv}
            maxAdv={maxAdv} setMaxAdv={setMaxAdv}
            minPositions={minPositions} setMinPositions={setMinPositions}
            startDate={startDate} setStartDate={setStartDate}
            endDate={endDate} setEndDate={setEndDate}
            minDataDate={minDataDate} setMinDataDate={setMinDataDate}
            fundingRate={fundingRate} setFundingRate={setFundingRate}
            spread={spread} setSpread={setSpread}
            prefilterEnabled={prefilterEnabled} setPrefilterEnabled={setPrefilterEnabled}
            prefilterMode={prefilterMode} setPrefilterMode={setPrefilterMode}
            prefilterHurstMin={prefilterHurstMin} setPrefilterHurstMin={setPrefilterHurstMin}
            prefilterTerPctl={prefilterTerPctl} setPrefilterTerPctl={setPrefilterTerPctl}
            prefilterHurstMax={prefilterHurstMax} setPrefilterHurstMax={setPrefilterHurstMax}
            prefilterVrMax={prefilterVrMax} setPrefilterVrMax={setPrefilterVrMax}
            prefilterMinCr={prefilterMinCr} setPrefilterMinCr={setPrefilterMinCr}
            processOrdersOnClose={processOrdersOnClose} setProcessOrdersOnClose={setProcessOrdersOnClose}
          />

          {/* Position sizing */}
          <PositionSizingSection
            normalized={normalized} setNormalized={setNormalized}
            riskMode={riskMode} setRiskMode={setRiskMode}
            riskValue={riskValue} setRiskValue={setRiskValue}
            martingaleEnabled={martingaleEnabled} setMartingaleEnabled={setMartingaleEnabled}
            martingaleType={martingaleType} setMartingaleType={setMartingaleType}
            martingaleMultiplier={martingaleMultiplier} setMartingaleMultiplier={setMartingaleMultiplier}
            martingaleMaxStreak={martingaleMaxStreak} setMartingaleMaxStreak={setMartingaleMaxStreak}
            volFilterEnabled={volFilterEnabled} setVolFilterEnabled={setVolFilterEnabled}
            volFilterAtrCeiling={volFilterAtrCeiling} setVolFilterAtrCeiling={setVolFilterAtrCeiling}
            volFilterAtrLength={volFilterAtrLength} setVolFilterAtrLength={setVolFilterAtrLength}
            enforceBybitLimits={enforceBybitLimits} setEnforceBybitLimits={setEnforceBybitLimits}
            expectedLiveSize={expectedLiveSize} setExpectedLiveSize={setExpectedLiveSize}
            maxMarginPerTrade={maxMarginPerTrade} setMaxMarginPerTrade={setMaxMarginPerTrade}
            marginFilterTolerance={marginFilterTolerance} setMarginFilterTolerance={setMarginFilterTolerance}
            bybitRejectPct={bybitRejectPct} setBybitRejectPct={setBybitRejectPct}
          />

          {/* Strategy Parameters */}
          {paramDefs.length > 0 && (
            <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold">Strategy Parameters</h3>
                <select value={typeof configMode === "number" ? configMode : configMode}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === "default") handleConfigModeChange("default");
                    else if (v === "custom") handleConfigModeChange("custom");
                    else handleConfigModeChange(parseInt(v));
                  }}
                  className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm focus:border-ag-accent outline-none">
                  <option value="default">Default Values</option>
                  {configs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  <option value="custom">Custom...</option>
                </select>
              </div>
              <ParameterForm definitions={paramDefs} values={strategyInputs}
                onChange={setStrategyInputs} disabled={configMode !== "custom"} />
            </div>
          )}

          {/* Active sub-tab content */}
          {subTab === "single" && <SingleBacktestTab {...shared} />}
          {subTab === "batch" && <BatchBacktestTab {...shared} />}
          {subTab === "sweep" && <SweepTab {...shared} />}
        </>
      )}
      </Suspense>
    </div>
  );
}

function TabBtn({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button onClick={onClick}
      className={`px-4 py-1.5 text-sm rounded-md font-medium transition-colors ${
        active ? "bg-ag-accent text-white" : "text-ag-text-muted hover:text-ag-text"
      }`}>
      {children}
    </button>
  );
}
