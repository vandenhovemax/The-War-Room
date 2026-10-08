"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { BarChart3, ExternalLink } from "lucide-react";
import { api } from "@/lib/api";
import type { BatchResultSummary, BatchSymbolResult } from "@/lib/api";

interface Props {
  batchId: number;
}

export default function BatchExpandedDetail({ batchId }: Props) {
  const router = useRouter();
  const [detail, setDetail] = useState<BatchResultSummary | null>(null);
  const [originalAgg, setOriginalAgg] = useState<Record<string, number | string> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    api.batchRunDetail(batchId)
      .then((d) => {
        setDetail(d);
        // If this is a refreshed batch, load original for comparison
        if (d.batch.refreshed_from_id) {
          api.batchRunDetail(d.batch.refreshed_from_id)
            .then((orig) => setOriginalAgg(orig.aggregate_metrics))
            .catch(() => {}); // original may be deleted
        }
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [batchId]);

  if (loading) return <p className="text-xs text-ag-text-muted py-3 px-4">Loading...</p>;
  if (error) return <p className="text-xs text-red-400 py-3 px-4">{error}</p>;
  if (!detail) return null;

  const agg = detail.aggregate_metrics;
  const symbols = [...detail.per_symbol].sort((a, b) => {
    const pa = a.metrics?.net_profit ?? 0;
    const pb = b.metrics?.net_profit ?? 0;
    return pb - pa;
  });

  return (
    <div className="px-4 pb-3 space-y-3">
      {/* Comparison panel for refreshed batches */}
      {originalAgg && (
        <ComparisonPanel original={originalAgg} refreshed={agg} />
      )}

      {/* Aggregate metrics row */}
      <div className="grid grid-cols-4 gap-3">
        <Metric label="Net Profit" value={fmt$(agg.total_net_profit)} green={Number(agg.total_net_profit ?? 0) > 0} />
        <Metric label="Avg Win Rate" value={fmtPct(agg.avg_win_rate)} />
        <Metric label="Avg PF" value={fmtNum(agg.avg_profit_factor)} />
        <Metric label="Total Positions" value={String(agg.total_trades_all ?? 0)} />
      </div>

      {/* Analyze Portfolio button */}
      <button
        onClick={() => router.push(`/analysis?batch=${batchId}`)}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-ag-accent/10 text-ag-accent rounded text-xs hover:bg-ag-accent/20 transition-colors"
      >
        <BarChart3 size={12} />
        Analyze Portfolio
      </button>

      {/* Per-symbol table — clickable rows */}
      {symbols.length > 0 && (
        <div className="max-h-48 overflow-y-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-ag-text-muted border-b border-ag-border">
                <th className="text-left py-1 px-2">Symbol</th>
                <th className="text-right py-1 px-2">Net Profit</th>
                <th className="text-right py-1 px-2">WR %</th>
                <th className="text-right py-1 px-2">PF</th>
                <th className="text-right py-1 px-2">Positions</th>
                <th className="text-center py-1 px-2">Regime</th>
                <th className="text-center py-1 px-2">Trend</th>
                <th className="text-center py-1 px-2">MR</th>
                <th className="text-center py-1 px-2">PnL Regime</th>
                <th className="text-center py-1 px-2">PnL T</th>
                <th className="text-center py-1 px-2">PnL MR</th>
                <th className="text-center py-1 px-2">Match</th>
                <th className="text-right py-1 px-2">TER</th>
                <th className="text-right py-1 px-2">Hurst</th>
                <th className="text-center py-1 px-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {symbols.map((s) => <SymRow key={s.symbol} s={s} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SymRow({ s }: { s: BatchSymbolResult }) {
  const m = s.metrics as Record<string, number | string> ?? {};
  const np = Number(m.net_profit ?? 0);
  return (
    <tr
      className="border-b border-ag-border/20 hover:bg-ag-accent/10 cursor-pointer transition-colors group"
      onClick={() => window.open(`/analysis?run=${s.run_id}`, "_blank")}
      title="Click to analyze this symbol"
    >
      <td className="py-1 px-2 font-medium group-hover:text-ag-accent">
        {s.symbol}
        <ExternalLink size={10} className="inline ml-1 opacity-0 group-hover:opacity-100 transition-opacity" />
      </td>
      <td className={`py-1 px-2 text-right ${np > 0 ? "text-ag-green" : np < 0 ? "text-ag-red" : ""}`}>
        {fmt$(np)}
      </td>
      <td className="py-1 px-2 text-right">{fmtPct(m.win_rate)}</td>
      <td className="py-1 px-2 text-right">{fmtNum(m.profit_factor)}</td>
      <td className="py-1 px-2 text-right">{m.total_trades ?? 0}</td>
      <td className="py-1 px-2 text-center">
        {m.regime && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${
            m.regime === "TREND" ? "bg-ag-green/15 text-ag-green"
            : m.regime === "MEANREV" ? "bg-ag-accent/15 text-ag-accent"
            : "bg-ag-border/30 text-ag-text-muted"
          }`}>{m.regime}</span>
        )}
      </td>
      <td className="py-1 px-2 text-center text-[10px]">{m.trend_grade ?? ""}</td>
      <td className="py-1 px-2 text-center text-[10px]">{m.meanrev_grade ?? ""}</td>
      <td className="py-1 px-2 text-center">
        {m.pnl_regime && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${
            m.pnl_regime === "TREND" ? "bg-ag-green/15 text-ag-green"
            : m.pnl_regime === "MEANREV" ? "bg-ag-accent/15 text-ag-accent"
            : "bg-ag-border/30 text-ag-text-muted"
          }`}>{m.pnl_regime}</span>
        )}
      </td>
      <td className="py-1 px-2 text-center text-[10px]">{m.pnl_trend_grade ?? ""}</td>
      <td className="py-1 px-2 text-center text-[10px]">{m.pnl_meanrev_grade ?? ""}</td>
      <td className="py-1 px-2 text-center text-[10px]">
        {m.regime && m.pnl_regime ? (
          m.regime === m.pnl_regime
            ? <span className="text-ag-green" title="OHLCV and PnL regime agree">&#10003;</span>
            : <span className="text-amber-400" title={`OHLCV: ${String(m.regime)}, PnL: ${String(m.pnl_regime)}`}>&#9888;</span>
        ) : ""}
      </td>
      <td className="py-1 px-2 text-right text-[10px]">{m.TER != null ? fmtNum(m.TER) : ""}</td>
      <td className={`py-1 px-2 text-right text-[10px] ${
        Number(m.Hurst ?? 0) > 0.55 ? "text-ag-green" : Number(m.Hurst ?? 0) >= 0.50 ? "text-yellow-400" : Number(m.Hurst ?? 0) > 0 ? "text-ag-red" : ""
      }`}>{m.Hurst != null ? fmtNum(Number(m.Hurst)) : ""}</td>
      <td className="py-1 px-2 text-center">
        <span className={`inline-block w-2 h-2 rounded-full ${
          s.status === "completed" ? "bg-ag-green" : s.status === "error" ? "bg-ag-red" : "bg-yellow-500"
        }`} />
      </td>
    </tr>
  );
}

function ComparisonPanel({ original, refreshed }: {
  original: Record<string, number | string>;
  refreshed: Record<string, number | string>;
}) {
  const rows: { label: string; key: string; fmt: (v: unknown) => string }[] = [
    { label: "Net Profit", key: "total_net_profit", fmt: fmt$ },
    { label: "Avg Win Rate", key: "avg_win_rate", fmt: fmtPct },
    { label: "Avg PF", key: "avg_profit_factor", fmt: fmtNum },
    { label: "Total Trades", key: "total_trades_all", fmt: (v) => String(Number(v ?? 0)) },
  ];
  return (
    <div className="bg-ag-dark/30 border border-ag-border/50 rounded-lg p-2">
      <div className="text-[10px] text-ag-text-muted mb-1.5 font-medium">Original vs Refreshed</div>
      <div className="grid grid-cols-4 gap-2">
        {rows.map((r) => {
          const oVal = Number(original[r.key] ?? 0);
          const nVal = Number(refreshed[r.key] ?? 0);
          const delta = nVal - oVal;
          const deltaStr = r.key === "total_trades_all"
            ? (delta >= 0 ? `+${delta}` : `${delta}`)
            : r.key === "avg_win_rate"
              ? `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}%`
              : `${delta >= 0 ? "+" : ""}${fmt$(delta)}`;
          return (
            <div key={r.key} className="text-center">
              <div className="text-[10px] text-ag-text-muted">{r.label}</div>
              <div className="text-[10px]">
                <span className="text-ag-text-muted">{r.fmt(oVal)}</span>
                <span className="mx-1">&rarr;</span>
                <span className="font-medium">{r.fmt(nVal)}</span>
              </div>
              <div className={`text-[10px] font-medium ${delta > 0 ? "text-ag-green" : delta < 0 ? "text-ag-red" : "text-ag-text-muted"}`}>
                {deltaStr}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Metric({ label, value, green }: { label: string; value: string; green?: boolean }) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-3">
      <div className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">{label}</div>
      <div className={`text-sm font-semibold ${green === true ? "text-ag-green" : green === false ? "text-ag-red" : ""}`}>
        {value}
      </div>
    </div>
  );
}

function fmt$(v: unknown): string {
  const n = Number(v ?? 0);
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}
function fmtPct(v: unknown): string {
  const n = Number(v ?? 0);
  return `${n.toFixed(1)}%`;
}
function fmtNum(v: unknown): string {
  const n = Number(v ?? 0);
  if (!isFinite(n)) return "10.00";
  return Math.min(n, 10).toFixed(2);
}
