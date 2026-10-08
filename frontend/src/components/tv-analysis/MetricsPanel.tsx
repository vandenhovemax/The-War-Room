"use client";

import { useState, useEffect } from "react";
import { api, DashboardAnalysisRequest } from "@/lib/api";

interface Props {
  req: DashboardAnalysisRequest;
}

const METRIC_GROUPS = [
  {
    title: "Core",
    keys: [
      { key: "Total Net Profit", fmt: "$", tip: "Sum of all trade P&L" },
      { key: "Total Positions", fmt: "#", tip: "Total closed positions" },
      { key: "Win Rate", fmt: "%", tip: "Percentage of winning trades" },
      { key: "Profit Factor", fmt: "x", tip: "Gross profit / gross loss" },
      { key: "Avg Trade Profit", fmt: "$", tip: "Average P&L per trade" },
      { key: "Avg Positions/Month", fmt: "#", tip: "Position frequency per month" },
      { key: "Max Simultaneous Trades", fmt: "#", tip: "Peak concurrent open positions" },
    ],
  },
  {
    title: "Drawdown",
    keys: [
      { key: "Max Drawdown ($)", fmt: "$", tip: "Largest peak-to-trough decline" },
      { key: "Avg Drawdown ($)", fmt: "$", tip: "Average drawdown during DD periods" },
      { key: "Max DD Duration", fmt: "t", tip: "Longest drawdown period" },
      { key: "Avg DD Duration", fmt: "t", tip: "Average drawdown duration" },
    ],
  },
  {
    title: "Professional",
    keys: [
      { key: "Sortino Ratio", fmt: "x", tip: "Return / downside deviation" },
      { key: "SQN", fmt: "x", tip: "System Quality Number" },
      { key: "Recovery Factor", fmt: "x", tip: "Net profit / max drawdown" },
      { key: "Calmar Ratio", fmt: "x", tip: "Annualized return / max DD" },
      { key: "Ulcer Index", fmt: "x", tip: "RMS of drawdowns (lower = less pain)" },
      { key: "Avg Win/Loss Ratio", fmt: "x", tip: "Avg win / avg loss" },
    ],
  },
  {
    title: "Win/Loss",
    keys: [
      { key: "Avg Win ($)", fmt: "$", tip: "Average winning trade" },
      { key: "Avg Loss ($)", fmt: "$", tip: "Average losing trade" },
      { key: "Max Win ($)", fmt: "$", tip: "Largest single win" },
      { key: "Max Loss ($)", fmt: "$", tip: "Largest single loss" },
      { key: "Max Consecutive Wins", fmt: "#", tip: "Longest winning streak" },
      { key: "Max Consecutive Losses", fmt: "#", tip: "Longest losing streak" },
    ],
  },
];

function formatValue(v: unknown, fmt: string): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  if (isNaN(n)) return String(v);
  if (fmt === "$") return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (fmt === "%") return `${n.toFixed(1)}%`;
  if (fmt === "x") return n.toFixed(2);
  if (fmt === "#") return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
  if (fmt === "t") return String(v);
  return String(v);
}

function valueColor(key: string, v: unknown): string {
  if (v === null || v === undefined) return "text-ag-text-muted";
  const n = Number(v);
  if (isNaN(n)) return "text-ag-text";
  if (key.includes("Drawdown") || key.includes("Loss")) return n < 0 ? "text-ag-red" : "text-ag-green";
  if (key.includes("Profit") || key.includes("Win") || key === "Total Net Profit") return n > 0 ? "text-ag-green" : n < 0 ? "text-ag-red" : "text-ag-text";
  if (key === "Win Rate") return n >= 50 ? "text-ag-green" : "text-ag-red";
  if (key === "Profit Factor") return n >= 1.5 ? "text-ag-green" : n >= 1 ? "text-ag-yellow" : "text-ag-red";
  return "text-ag-text";
}

export default function MetricsPanel({ req }: Props) {
  const [metrics, setMetrics] = useState<Record<string, unknown>>({});
  const [tradeCount, setTradeCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!req.strategy_filename && !req.batch_id && !req.run_id && !req.portfolio_id) return;
    setLoading(true);
    setError("");
    api.dashboardMetrics(req)
      .then((r) => { setMetrics(r.metrics); setTradeCount(r.trade_count); })
      .catch((e) => { setError(e instanceof Error ? e.message : "Failed to load metrics"); })
      .finally(() => setLoading(false));
  }, [req.strategy_filename, req.batch_id, req.portfolio_id, req.run_id, req.side_filter]);

  if (loading) return <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted">Loading metrics...</div>;
  if (error) return <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-red text-sm">{error}</div>;
  if (!Object.keys(metrics).length) return <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted text-sm">No metrics available for this run.</div>;

  return (
    <div className="space-y-3">
      {METRIC_GROUPS.map((group) => (
        <div key={group.title}>
          <h3 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">{group.title}</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-7 gap-3">
            {group.keys.map(({ key, fmt, tip }) => (
              <div key={key} className="bg-ag-card border border-ag-border rounded-lg p-3 group relative">
                <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5 truncate">{key}</p>
                <p className={`text-sm font-semibold ${valueColor(key, metrics[key])}`}>
                  {formatValue(metrics[key], fmt)}
                </p>
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 bg-ag-darker border border-ag-border rounded text-[10px] text-ag-text-muted whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10">
                  {tip}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
