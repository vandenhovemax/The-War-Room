"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { BarChart3, Eye, Loader2, ChevronDown, ChevronUp, Settings } from "lucide-react";
import type { BatchResultSummary, BatchSymbolResult, BacktestRunDetail, BacktestTradeItem } from "@/lib/api";
import { api } from "@/lib/api";
import BacktestResults from "./BacktestResults";
import CategoryBreakdownPanel from "@/components/analysis/CategoryBreakdownPanel";

type SortKey = "symbol" | "net_profit" | "win_rate" | "profit_factor" | "total_trades" | "max_drawdown_pct" | "sharpe_ratio" | "regime" | "trend_grade" | "meanrev_grade" | "Hurst" | "pnl_regime" | "pnl_trend_grade" | "pnl_meanrev_grade" | "agreement" | "avg_consec_bull" | "avg_consec_bear" | "avg_consec_all";

interface BatchResultsSummaryProps {
  result: BatchResultSummary;
  onViewSymbol: (runId: number) => void;
}

function fmt(v: number | undefined, decimals: number = 2): string {
  if (v === undefined || v === null) return "—";
  if (!isFinite(v)) return "—";
  return v.toFixed(decimals);
}

function fmtRatio(v: number | undefined, decimals: number = 2): string {
  if (v === undefined || v === null) return "—";
  if (!isFinite(v)) return "—";
  return Math.min(v, 99.99).toFixed(decimals);
}

function fmtDollar(v: number | undefined): string {
  if (v === undefined || v === null) return "—";
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function BatchResultsSummary({ result, onViewSymbol }: BatchResultsSummaryProps) {
  const router = useRouter();
  const [sortKey, setSortKey] = useState<SortKey>("net_profit");
  const [sortAsc, setSortAsc] = useState(false);
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null);
  const [expandedRun, setExpandedRun] = useState<BacktestRunDetail | null>(null);
  const [expandedTrades, setExpandedTrades] = useState<BacktestTradeItem[]>([]);
  const [expandLoading, setExpandLoading] = useState(false);
  const [showCategories, setShowCategories] = useState(false);
  const [showConfig, setShowConfig] = useState(true);
  const agg = result.aggregate_metrics;
  const params = result.batch.parameters ?? {};

  const gradeOrd = (g: unknown): number => {
    const map: Record<string, number> = { A: 5, B: 4, C: 3, D: 2, F: 1 };
    return map[String(g ?? "")] ?? 0;
  };

  const sorted = [...result.per_symbol].sort((a, b) => {
    if (sortKey === "symbol") {
      return sortAsc ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol);
    }
    if (sortKey === "regime") {
      const cmp = String(a.metrics.regime ?? "").localeCompare(String(b.metrics.regime ?? ""));
      return sortAsc ? cmp : -cmp;
    }
    if (sortKey === "trend_grade" || sortKey === "meanrev_grade" || sortKey === "pnl_trend_grade" || sortKey === "pnl_meanrev_grade") {
      const cmp = gradeOrd(a.metrics[sortKey]) - gradeOrd(b.metrics[sortKey]);
      return sortAsc ? cmp : -cmp;
    }
    if (sortKey === "pnl_regime") {
      const cmp = String(a.metrics.pnl_regime ?? "").localeCompare(String(b.metrics.pnl_regime ?? ""));
      return sortAsc ? cmp : -cmp;
    }
    if (sortKey === "agreement") {
      const agreeA = a.metrics.regime && a.metrics.pnl_regime && a.metrics.regime === a.metrics.pnl_regime ? 1 : 0;
      const agreeB = b.metrics.regime && b.metrics.pnl_regime && b.metrics.regime === b.metrics.pnl_regime ? 1 : 0;
      return sortAsc ? agreeA - agreeB : agreeB - agreeA;
    }
    const av = (a.metrics[sortKey] ?? 0) as number;
    const bv = (b.metrics[sortKey] ?? 0) as number;
    return sortAsc ? av - bv : bv - av;
  });

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  const toggleExpand = async (runId: number) => {
    if (expandedRunId === runId) { setExpandedRunId(null); return; }
    setExpandedRunId(runId);
    setExpandLoading(true);
    try {
      const [detail, trades] = await Promise.all([
        api.backtestRunDetail(runId),
        api.backtestRunTrades(runId),
      ]);
      setExpandedRun(detail);
      setExpandedTrades(trades);
    } catch { /* silently fail — row collapses on next click */ }
    setExpandLoading(false);
  };

  const hdr = (key: SortKey, label: string, tooltip?: string) => (
    <th
      onClick={() => handleSort(key)}
      className="px-3 py-2 text-left text-xs font-medium text-ag-text-muted cursor-pointer hover:text-ag-text select-none"
      title={tooltip}
    >
      {label} {sortKey === key ? (sortAsc ? "▲" : "▼") : ""}
    </th>
  );

  return (
    <div className="space-y-4">
      {/* Aggregate stats */}
      <div className="grid grid-cols-2 sm:grid-cols-7 gap-3">
        {agg.batch_grade && (
          <GradeCard grade={agg.batch_grade as string} score={agg.batch_grade_score as number} />
        )}
        <AggCard label="Total Net Profit" value={fmtDollar(agg.total_net_profit as number)}
          positive={(agg.total_net_profit as number) > 0} />
        <AggCard label="Profitable Symbols"
          value={`${agg.profitable_symbols ?? 0} / ${agg.symbols_with_trades ?? 0}`} />
        <AggCard label="Avg Win Rate" value={`${fmt(agg.avg_win_rate as number)}%`} />
        <AggCard label="Avg PF" value={fmtRatio(agg.avg_profit_factor as number)} />
        <AggCard label="Total Positions" value={`${agg.total_trades_all ?? 0}`} />
        <AggCard label="Max Open Positions" value={`${agg.max_simultaneous_trades ?? 0}`} />
      </div>

      {/* Backtest Configuration */}
      <BacktestConfigDisplay params={params} show={showConfig} />

      {/* Action buttons */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => router.push(`/analysis?batch=${result.batch.id}`)}
          className="flex items-center gap-2 px-4 py-2 bg-ag-accent/10 text-ag-accent rounded-lg text-sm hover:bg-ag-accent/20 transition-colors"
        >
          <BarChart3 size={16} />
          Analyze Portfolio
        </button>
        <button
          onClick={() => setShowConfig(!showConfig)}
          className="flex items-center gap-1.5 px-3 py-2 text-xs text-ag-text-muted hover:text-ag-text rounded-lg border border-ag-border hover:border-ag-accent/50 transition-colors"
        >
          <Settings size={14} />
          {showConfig ? "Hide" : "Show"} Config
        </button>
        <button
          onClick={() => setShowCategories(!showCategories)}
          className="flex items-center gap-1.5 px-3 py-2 text-xs text-ag-text-muted hover:text-ag-text rounded-lg border border-ag-border hover:border-ag-accent/50 transition-colors"
        >
          {showCategories ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          Category Breakdown
        </button>
      </div>

      {/* Inline category breakdown */}
      {showCategories && (
        <div className="bg-ag-darker border border-ag-border rounded-lg p-4">
          <CategoryBreakdownPanel batchId={result.batch.id} />
        </div>
      )}

      {/* Per-symbol table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-ag-border">
            <tr>
              <th className="w-8 px-1 py-2" />
              {hdr("symbol", "Symbol", "Trading pair symbol")}
              {hdr("net_profit", "Net Profit", "Total net profit/loss")}
              {hdr("win_rate", "Win Rate", "Percentage of profitable trades")}
              {hdr("profit_factor", "PF", "Profit Factor — gross profit / gross loss. >1.5 good, >2.0 great")}
              {hdr("total_trades", "Positions", "Total number of closed positions")}
              {hdr("max_drawdown_pct", "Max DD%", "Maximum peak-to-trough equity drawdown as percentage")}
              {hdr("sharpe_ratio", "Sharpe", "Sharpe Ratio — risk-adjusted return metric")}
              {hdr("regime", "Regime", "OHLCV price regime: TREND, MEANREV, or NEUTRAL")}
              {hdr("trend_grade", "Trend", "Trend-following suitability grade (A = best, F = worst)")}
              {hdr("meanrev_grade", "MR", "Mean-reversion suitability grade (A = best, F = worst)")}
              {hdr("pnl_regime", "PnL Regime", "PnL-based regime classification")}
              {hdr("pnl_trend_grade", "PnL T", "PnL-based trend-following grade")}
              {hdr("pnl_meanrev_grade", "PnL MR", "PnL-based mean-reversion grade")}
              {hdr("agreement", "Match", "Whether OHLCV and PnL regime classifications agree")}
              {hdr("Hurst", "Hurst", "Hurst exponent: >0.5 = trending, 0.5 = random walk, <0.5 = mean-reverting")}
              {hdr("avg_consec_bull", "Avg Bull", "Average consecutive bull (long) ranges from market structure detection")}
              {hdr("avg_consec_bear", "Avg Bear", "Average consecutive bear (short) ranges from market structure detection")}
              {hdr("avg_consec_all", "Avg L+S", "Average of bull + bear consecutive ranges (combined)")}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <SymbolRow key={r.symbol} row={r}
                onClick={() => onViewSymbol(r.run_id)}
                onExpand={() => toggleExpand(r.run_id)}
                isExpanded={expandedRunId === r.run_id}
                isLoading={expandedRunId === r.run_id && expandLoading}
                expandedRun={expandedRunId === r.run_id ? expandedRun : null}
                expandedTrades={expandedRunId === r.run_id ? expandedTrades : []}
                onCollapse={() => setExpandedRunId(null)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AggCard({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-3">
      <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">{label}</p>
      <p className={`text-sm font-semibold ${
        positive === true ? "text-ag-green" : positive === false ? "text-ag-red" : "text-ag-text"
      }`}>{value}</p>
    </div>
  );
}

function GradeCard({ grade, score }: { grade: string; score?: number }) {
  const color = grade.startsWith("A") ? "border-ag-green/50 text-ag-green"
    : grade.startsWith("B") ? "border-lime-500/50 text-lime-400"
    : grade.startsWith("C") ? "border-yellow-500/50 text-yellow-400"
    : grade.startsWith("D") ? "border-orange-500/50 text-orange-400"
    : "border-ag-red/50 text-ag-red";
  return (
    <div className={`bg-ag-card border-2 ${color} rounded-lg p-3 flex flex-col items-center justify-center`}
      title="Batch grade based on: avg PF (30%), win rate (15%), trade count (15%), profitable symbol ratio (20%), PF distribution quality (20%)">
      <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">Grade</p>
      <p className={`text-2xl font-bold ${color.split(" ")[1]}`}>{grade}</p>
      {score != null && <p className="text-[9px] text-ag-text-muted mt-0.5">{(score * 100).toFixed(0)}%</p>}
    </div>
  );
}

function BacktestConfigDisplay({ params, show }: { params: Record<string, unknown>; show: boolean }) {
  if (!show || Object.keys(params).length === 0) return null;

  const slipMode = (params.slippage_mode as string) ?? "pct";
  const slipVal = (params.slippage_value as number) ?? 0;
  const slipLabel = slipMode === "pips" ? "Pips" : slipMode === "risk_pct" ? "% Risk" : "% Price";

  const costsOn = params.include_costs !== false;

  const items: { label: string; value: string; highlight?: boolean }[] = [
    { label: "Initial Capital", value: `$${((params.initial_capital as number) ?? 10000).toLocaleString()}` },
    { label: "Trading Costs", value: costsOn ? "ON" : "OFF", highlight: !costsOn },
  ];

  if (costsOn) {
    items.push(
      { label: "Commission", value: `${(params.commission_pct as number) ?? 0.055}%`, highlight: true },
      { label: "Slippage", value: slipVal > 0 ? `${slipVal} (${slipLabel})` : "None", highlight: slipVal > 0 },
    );
  }

  items.push(
    { label: "Leverage", value: `${(params.leverage as number) ?? 1}x` },
    { label: "Max Pyramiding", value: `${(params.max_pyramiding as number) ?? 1}` },
    { label: "Mode", value: `${(params.mode as string) ?? "individual"}` },
  );

  const riskMode = params.risk_mode as string;
  const riskVal = params.risk_value as number;
  if (riskMode && riskVal && riskVal > 0) {
    items.push({ label: "Risk", value: `${riskVal} (${riskMode})` });
  }

  const warmup = params.warmup_bars as number;
  if (warmup && warmup > 0) {
    items.push({ label: "Warmup Bars", value: `${warmup}` });
  }

  if (params.martingale_enabled) {
    items.push({
      label: "Martingale",
      value: `${(params.martingale_type as string) ?? "anti"} ${(params.martingale_multiplier as number) ?? 2}x (max ${(params.martingale_max_streak as number) ?? 3})`,
    });
  }

  if (params.vol_filter_enabled) {
    items.push({
      label: "Vol Filter",
      value: `ATR(${(params.vol_filter_atr_length as number) ?? 14}) < ${(params.vol_filter_atr_ceiling as number) ?? 0}`,
    });
  }

  const minAdv = params.min_avg_daily_volume as number;
  if (minAdv && minAdv > 0) {
    items.push({ label: "Min ADV", value: `$${minAdv.toLocaleString()}` });
  }

  // Strategy parameters (from strategy_inputs)
  const stratInputs = params.strategy_inputs as Record<string, unknown> | undefined;
  const sweepCombo = params.sweep_combo as Record<string, unknown> | undefined;

  return (
    <div className="bg-ag-darker border border-ag-border rounded-lg p-3 space-y-3">
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-x-4 gap-y-1.5">
        {items.map((item) => (
          <div key={item.label}>
            <p className="text-[10px] text-ag-text-muted uppercase tracking-wide">{item.label}</p>
            <p className={`text-xs font-medium ${item.highlight ? "text-amber-400" : "text-ag-text"}`}>
              {item.value}
            </p>
          </div>
        ))}
      </div>
      {stratInputs && Object.keys(stratInputs).length > 0 && (
        <div>
          <p className="text-[10px] text-ag-text-muted uppercase tracking-wide mb-1">Strategy Parameters</p>
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-x-4 gap-y-1.5">
            {Object.entries(stratInputs).map(([key, val]) => {
              const isSwept = sweepCombo ? key in sweepCombo : false;
              return (
                <div key={key}>
                  <p className={`text-[10px] uppercase tracking-wide ${isSwept ? "text-ag-accent" : "text-ag-text-muted"}`}>{key}</p>
                  <p className={`text-xs font-medium ${isSwept ? "text-ag-accent font-bold" : "text-ag-text"}`}>
                    {typeof val === "boolean" ? (val ? "On" : "Off") : String(val ?? "—")}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}


function SymbolRow({ row, onClick, onExpand, isExpanded, isLoading, expandedRun, expandedTrades, onCollapse }: {
  row: BatchSymbolResult; onClick: () => void; onExpand: () => void;
  isExpanded: boolean; isLoading: boolean;
  expandedRun: BacktestRunDetail | null; expandedTrades: BacktestTradeItem[];
  onCollapse: () => void;
}) {
  const m = row.metrics;
  const profit = m.net_profit ?? 0;
  return (
    <>
      <tr className="border-b border-ag-border/50 hover:bg-ag-card-hover transition-colors">
        <td className="px-1 py-2 text-center">
          <button onClick={(e) => { e.stopPropagation(); onExpand(); }}
            className="p-1 rounded hover:bg-ag-border/50 text-ag-text-muted hover:text-ag-accent transition-colors"
            title="Preview details inline">
            {isLoading ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
          </button>
        </td>
        <td className="px-3 py-2 font-medium cursor-pointer" onClick={onClick}>{row.symbol}</td>
        <td className={`px-3 py-2 cursor-pointer ${profit > 0 ? "text-ag-green" : profit < 0 ? "text-ag-red" : ""}`} onClick={onClick}>
          {fmtDollar(profit)}
        </td>
        <td className="px-3 py-2 cursor-pointer" onClick={onClick}>{fmt(m.win_rate)}%</td>
        <td className="px-3 py-2 cursor-pointer" onClick={onClick}>{fmtRatio(m.profit_factor)}</td>
        <td className="px-3 py-2 cursor-pointer" onClick={onClick}>{m.total_trades ?? 0}</td>
        <td className="px-3 py-2 text-ag-red cursor-pointer" onClick={onClick}>{fmt(m.max_drawdown_pct)}%</td>
        <td className="px-3 py-2 cursor-pointer" onClick={onClick}>{fmtRatio(m.sharpe_ratio)}</td>
        <td className="px-3 py-2 text-center">
          {m.regime && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              String(m.regime) === "TREND" ? "bg-green-500/15 text-green-400"
              : String(m.regime) === "MEANREV" ? "bg-blue-500/15 text-blue-400"
              : "bg-gray-500/15 text-gray-400"
            }`}>{String(m.regime)}</span>
          )}
        </td>
        <td className="px-3 py-2 text-center text-[10px] font-medium">{m.trend_grade ?? ""}</td>
        <td className="px-3 py-2 text-center text-[10px] font-medium">{m.meanrev_grade ?? ""}</td>
        <td className="px-3 py-2 text-center">
          {m.pnl_regime && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              String(m.pnl_regime) === "TREND" ? "bg-green-500/15 text-green-400"
              : String(m.pnl_regime) === "MEANREV" ? "bg-blue-500/15 text-blue-400"
              : "bg-gray-500/15 text-gray-400"
            }`}>{String(m.pnl_regime)}</span>
          )}
        </td>
        <td className="px-3 py-2 text-center text-[10px] font-medium">{m.pnl_trend_grade ?? ""}</td>
        <td className="px-3 py-2 text-center text-[10px] font-medium">{m.pnl_meanrev_grade ?? ""}</td>
        <td className="px-3 py-2 text-center text-[10px]">
          {m.regime && m.pnl_regime ? (
            m.regime === m.pnl_regime
              ? <span className="text-green-400" title="OHLCV and PnL regime agree">&#10003;</span>
              : <span className="text-amber-400" title={`OHLCV: ${m.regime}, PnL: ${m.pnl_regime}`}>&#9888;</span>
          ) : ""}
        </td>
        <td className={`px-3 py-2 text-xs ${
          Number(m.Hurst ?? 0) > 0.55 ? "text-green-400" : Number(m.Hurst ?? 0) >= 0.50 ? "text-yellow-400" : Number(m.Hurst ?? 0) > 0 ? "text-red-400" : ""
        }`}>{m.Hurst != null ? Number(m.Hurst).toFixed(3) : ""}</td>
        <td className="px-3 py-2 text-xs">{m.avg_consec_bull != null && Number(m.avg_consec_bull) > 0 ? Number(m.avg_consec_bull).toFixed(2) : ""}</td>
        <td className="px-3 py-2 text-xs">{m.avg_consec_bear != null && Number(m.avg_consec_bear) > 0 ? Number(m.avg_consec_bear).toFixed(2) : ""}</td>
        <td className="px-3 py-2 text-xs">{m.avg_consec_all != null && Number(m.avg_consec_all) > 0 ? Number(m.avg_consec_all).toFixed(2) : ""}</td>
      </tr>
      {isExpanded && !isLoading && expandedRun && (
        <tr>
          <td colSpan={19} className="p-0">
            <div className="border-l-2 border-ag-accent ml-4 pl-2 py-2">
              <BacktestResults run={expandedRun} trades={expandedTrades} onClose={onCollapse} />
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
