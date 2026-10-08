"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { BarChart3 } from "lucide-react";
import type { BacktestRunDetail, BacktestTradeItem, DashboardAnalysisRequest } from "@/lib/api";
import TradeTable from "./TradeTable";
import MetricsPanel from "@/components/tv-analysis/MetricsPanel";
import EquityCurveChart from "@/components/tv-analysis/EquityCurveChart";
import MonthlyMatrix from "@/components/tv-analysis/MonthlyMatrix";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "monthly", label: "Monthly" },
  { id: "trades", label: "Trades" },
];

interface Props {
  run: BacktestRunDetail;
  trades: BacktestTradeItem[];
  onClose: () => void;
}

export default function BacktestResults({ run, trades, onClose }: Props) {
  const router = useRouter();
  const [tab, setTab] = useState("overview");
  const m = run.metrics;

  const kpis = [
    { label: "Net Profit", key: "net_profit", fmt: "$" },
    { label: "Positions", key: "total_trades", fmt: "#" },
    { label: "Win Rate", key: "win_rate", fmt: "%" },
    { label: "Profit Factor", key: "profit_factor", fmt: "x" },
    { label: "Max DD %", key: "max_drawdown_pct", fmt: "%" },
    { label: "Sortino", key: "sortino_ratio", fmt: "x" },
    { label: "Avg Trade", key: "avg_trade", fmt: "$" },
    { label: "Max Open", key: "max_simultaneous_trades", fmt: "#" },
  ];

  const fmtVal = (v: number, fmt: string) =>
    isNaN(v) ? "—"
    : fmt === "$" ? `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : fmt === "%" ? `${v.toFixed(1)}%`
    : fmt === "x" ? v.toFixed(2)
    : v.toLocaleString();

  const kpiColor = (key: string, n: number) =>
    (key === "net_profit" || key === "avg_trade") ? (n > 0 ? "text-ag-green" : n < 0 ? "text-ag-red" : "")
    : key === "win_rate" ? (n >= 50 ? "text-ag-green" : "text-ag-red")
    : key === "profit_factor" ? (n >= 1.5 ? "text-ag-green" : n >= 1 ? "text-ag-yellow" : "text-ag-red")
    : key === "max_drawdown_pct" ? (n < -20 ? "text-ag-red" : "text-ag-yellow")
    : "";

  return (
    <div className="space-y-4">
      <div className="bg-ag-card border border-ag-border rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">
            Results — {run.symbol} {run.timeframe}m
          </h2>
          <div className="flex items-center gap-3">
            <button onClick={() => router.push(`/analysis?run=${run.id}`)}
              className="flex items-center gap-1.5 text-sm text-ag-accent hover:text-ag-accent/80">
              <BarChart3 size={14} /> Analyze
            </button>
            <button onClick={onClose} className="text-sm text-ag-text-muted hover:text-ag-text">Close</button>
          </div>
        </div>

        {run.status === "error" && (
          <div className="text-ag-red text-sm mb-4">{run.error_message}</div>
        )}

        {m && (
          <div className="grid grid-cols-4 sm:grid-cols-4 md:grid-cols-8 gap-3 mb-4">
            {kpis.map(({ label, key, fmt }) => {
              const n = typeof m[key] === "number" ? m[key] : parseFloat(String(m[key] ?? ""));
              return (
                <div key={key} className="bg-ag-card border border-ag-border rounded-lg p-3">
                  <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">{label}</p>
                  <p className={`text-sm font-semibold ${kpiColor(key, n)}`}>{fmtVal(n, fmt)}</p>
                </div>
              );
            })}
          </div>
        )}

        {/* Market Structure — avg consecutive ranges (only shown when data exists) */}
        {m && Number(m.total_ranges ?? 0) > 0 && (
          <div className="grid grid-cols-4 gap-3 mb-4">
            <div className="bg-ag-card border border-ag-border rounded-lg p-3">
              <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">Avg Bull Range</p>
              <p className="text-sm font-semibold">{Number(m.avg_consec_bull ?? 0).toFixed(2)}</p>
            </div>
            <div className="bg-ag-card border border-ag-border rounded-lg p-3">
              <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">Avg Bear Range</p>
              <p className="text-sm font-semibold">{Number(m.avg_consec_bear ?? 0).toFixed(2)}</p>
            </div>
            <div className="bg-ag-card border border-ag-border rounded-lg p-3">
              <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">Avg L+S Range</p>
              <p className="text-sm font-semibold">{Number(m.avg_consec_all ?? 0).toFixed(2)}</p>
            </div>
            <div className="bg-ag-card border border-ag-border rounded-lg p-3">
              <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">Total Ranges</p>
              <p className="text-sm font-semibold">{Number(m.total_ranges ?? 0)}</p>
            </div>
          </div>
        )}

        <div className="flex gap-1 border-b border-ag-border">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === t.id ? "border-ag-accent text-ag-accent" : "border-transparent text-ag-text-muted hover:text-ag-text"
              }`}>{t.label}</button>
          ))}
        </div>
      </div>

      {tab === "overview" && (
        <div className="space-y-4">
          <MetricsPanel req={{ run_id: run.id } as DashboardAnalysisRequest} />
          <EquityCurveChart req={{ run_id: run.id } as DashboardAnalysisRequest} />
        </div>
      )}
      {tab === "monthly" && <MonthlyMatrix req={{ run_id: run.id } as DashboardAnalysisRequest} />}
      {tab === "trades" && trades.length > 0 && (
        <div className="bg-ag-card border border-ag-border rounded-xl p-6">
          <h3 className="text-lg font-semibold mb-4">Trades ({trades.length} positions)</h3>
          <TradeTable trades={trades} symbol={run.symbol} timeframe={String(run.timeframe)} strategyId={run.strategy_id} />
        </div>
      )}
    </div>
  );
}
