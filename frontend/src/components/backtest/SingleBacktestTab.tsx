"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { Play, Loader2, AlertCircle } from "lucide-react";
import {
  api, BacktestRunItem, BacktestRunDetail, BacktestTradeItem,
  BacktestProgress, DataStatusItem, BatchBacktestRunItem, StrategyItem,
} from "@/lib/api";
import type { DirectionValue } from "./DirectionFilter";
import BacktestHistory from "./BacktestHistory";
import BacktestResults from "./BacktestResults";

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
  startDate: string;
  endDate: string;
  configMode: "default" | "custom" | number;
  strategyInputs: Record<string, number | boolean | string>;
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
}

export default function SingleBacktestTab(p: Props) {
  const [runs, setRuns] = useState<BacktestRunItem[]>([]);
  const [batchRuns, setBatchRuns] = useState<BatchBacktestRunItem[]>([]);
  const [symbol, setSymbol] = useState("");
  const [running, setRunning] = useState(false);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  const [progress, setProgress] = useState<BacktestProgress | null>(null);
  const [error, setError] = useState("");
  const [selectedRun, setSelectedRun] = useState<BacktestRunDetail | null>(null);
  const [trades, setTrades] = useState<BacktestTradeItem[]>([]);
  const loadedRef = useRef(false);

  const loadHistory = useCallback(async () => {
    const [r, b] = await Promise.all([api.backtestRuns(), api.batchRuns()]);
    setRuns(r);
    setBatchRuns(b);
  }, []);

  useEffect(() => { if (!loadedRef.current) { loadedRef.current = true; loadHistory(); } }, [loadHistory]);

  // Poll progress
  useEffect(() => {
    if (!activeRunId) return;
    const iv = setInterval(async () => {
      const prog = await api.backtestProgress(activeRunId);
      setProgress(prog);
      if (prog.status === "completed" || prog.status === "error") {
        setRunning(false);
        setActiveRunId(null);
        loadHistory();
      }
    }, 1500);
    return () => clearInterval(iv);
  }, [activeRunId, loadHistory]);

  const handleRun = async () => {
    if (!p.strategyId || !symbol) { setError("Select a strategy and symbol"); return; }
    setRunning(true); setError(""); setProgress(null);
    try {
      const cfgId = typeof p.configMode === "number" ? p.configMode : undefined;
      const inputs = p.configMode === "custom" ? p.strategyInputs : undefined;
      const res = await api.startBacktest(
        p.strategyId, symbol, p.timeframe, p.direction,
        parseFloat(p.capital),
        p.includeCosts ? parseFloat(p.commission) : 0,
        parseInt(p.leverage), parseFloat(p.minAdv) || 0,
        cfgId, inputs, 0,
        p.startDate || undefined, p.endDate || undefined,
        p.slippageMode,
        p.includeCosts ? (parseFloat(p.slippageValue) || 0) : 0,
        p.riskMode, parseFloat(p.riskValue) || 0,
        p.martingaleEnabled, p.martingaleType,
        parseFloat(p.martingaleMultiplier) || 2, parseInt(p.martingaleMaxStreak) || 3,
        p.volFilterEnabled, parseFloat(p.volFilterAtrCeiling) || 0,
        parseInt(p.volFilterAtrLength) || 14,
        parseInt(p.pyramiding) || 1,
        parseFloat(p.maxAdv) || 0,
        p.processOrdersOnClose,
      );
      setActiveRunId(res.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start backtest");
      setRunning(false);
    }
  };

  const handleViewRun = async (runId: number) => {
    const [detail, t] = await Promise.all([api.backtestRunDetail(runId), api.backtestRunTrades(runId)]);
    setSelectedRun(detail);
    setTrades(t);
  };

  const handleDeleteRun = async (runId: number) => {
    await api.deleteBacktestRun(runId);
    setRuns((prev) => prev.filter((r) => r.id !== runId));
    if (selectedRun?.id === runId) { setSelectedRun(null); setTrades([]); }
  };

  const handleDeleteBatch = async (batchId: number) => {
    await api.deleteBatchRun(batchId);
    setBatchRuns((prev) => prev.filter((b) => b.id !== batchId));
    setRuns((prev) => prev.filter((r) => r.batch_id !== batchId));
    if (selectedRun?.batch_id === batchId) { setSelectedRun(null); setTrades([]); }
  };

  const availableSymbols = [...new Set(p.dataStatus.map((d) => d.symbol))];
  const fmtAdv = (v: number) => v >= 1e9 ? `${(v/1e9).toFixed(1)}B` : v >= 1e6 ? `${(v/1e6).toFixed(1)}M` : v >= 1e3 ? `${(v/1e3).toFixed(0)}K` : `${v.toFixed(0)}`;

  return (
    <div className="space-y-4">
      {/* Symbol + Run */}
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <label className="block text-xs text-ag-text-muted mb-1">Symbol</label>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}
            className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none">
            <option value="">Select...</option>
            {availableSymbols.map((s) => {
              const adv = p.dataStatus.find((d) => d.symbol === s && d.timeframe === p.timeframe)?.avg_daily_volume ?? 0;
              return <option key={s} value={s}>{s}{adv > 0 ? ` (${fmtAdv(adv)} USDT)` : ""}</option>;
            })}
          </select>
        </div>
        <div className="pt-5">
          <button onClick={handleRun} disabled={running || !p.strategyId || !symbol}
            className="flex items-center gap-2 bg-ag-accent hover:bg-ag-accent/80 text-white px-5 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
            {running ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
            {running ? "Running..." : "Run Backtest"}
          </button>
        </div>
      </div>

      {/* Progress / Error */}
      {progress?.status === "running" && (
        <p className="text-sm text-ag-text-muted">Processing {progress.bars_done?.toLocaleString()} / {progress.total_bars?.toLocaleString()} bars...</p>
      )}
      {progress?.status === "completed" && (
        <p className="text-sm text-ag-green">Completed — {progress.metrics?.total_trades} positions</p>
      )}
      {error && <div className="flex items-center gap-2 text-ag-red text-sm"><AlertCircle size={14} /> {error}</div>}

      {/* Results */}
      {selectedRun && (
        <BacktestResults run={selectedRun} trades={trades}
          onClose={() => { setSelectedRun(null); setTrades([]); }} />
      )}

      {/* History */}
      <div className="bg-ag-card border border-ag-border rounded-xl p-5">
        <h2 className="text-sm font-semibold mb-3">Backtest History</h2>
        <BacktestHistory runs={runs} batchRuns={batchRuns} strategies={p.strategies}
          selectedRunId={selectedRun?.id ?? null}
          onViewRun={handleViewRun} onDeleteRun={handleDeleteRun}
          onViewBatch={(id) => handleViewRun(id)}
          onDeleteBatch={handleDeleteBatch} />
      </div>
    </div>
  );
}
