"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { BacktestRunDetail } from "@/lib/api";

interface Props {
  runId: number;
}

export default function SingleRunDetail({ runId }: Props) {
  const [detail, setDetail] = useState<BacktestRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    api.backtestRunDetail(runId)
      .then(setDetail)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [runId]);

  if (loading) return <p className="text-xs text-ag-text-muted py-3 px-4">Loading...</p>;
  if (error) return <p className="text-xs text-red-400 py-3 px-4">{error}</p>;
  if (!detail) return null;

  const m = detail.metrics ?? {};
  const np = m.net_profit ?? 0;

  return (
    <div className="px-4 pb-3">
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
        <MetricCard label="Net Profit" value={fmt$(np)} color={np > 0 ? "green" : np < 0 ? "red" : undefined} />
        <MetricCard label="Win Rate" value={`${(m.win_rate ?? 0).toFixed(1)}%`} />
        <MetricCard label="Profit Factor" value={(m.profit_factor ?? 0).toFixed(2)} />
        <MetricCard label="Max DD %" value={`${(m.max_drawdown_pct ?? 0).toFixed(1)}%`} />
        <MetricCard label="Sharpe" value={(m.sharpe ?? 0).toFixed(2)} />
        <MetricCard label="Positions" value={String(m.total_trades ?? 0)} />
      </div>
    </div>
  );
}

function MetricCard({ label, value, color }: { label: string; value: string; color?: string }) {
  const cls = color === "green" ? "text-ag-green" : color === "red" ? "text-ag-red" : "";
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-3">
      <div className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">{label}</div>
      <div className={`text-sm font-semibold ${cls}`}>{value}</div>
    </div>
  );
}

function fmt$(v: number): string {
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}
