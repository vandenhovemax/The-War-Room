"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PortfolioDetail } from "@/lib/api";

type LiveMode = "real" | "paper" | "both";

interface MetricsBundle {
  metrics: Record<string, number | string | null>;
  equity_curve: { date: string; equity: number }[];
  trade_count?: number;
  since?: string | null;
}

/** Side-by-side comparison of walk-forward OOS vs live trading results.
 *
 * OOS column: aggregated per-window OOS from /full-metrics-wfo (same numbers
 * shown in the Walk-Forward Truth panel).
 * LIVE column: actual trades from the Trade table closed after the portfolio
 * was saved (real, paper, or both).
 */
export default function OOSvsLivePanel({ detail }: { detail: PortfolioDetail }) {
  const [mode, setMode] = useState<LiveMode>("real");
  const [oos, setOos] = useState<MetricsBundle | null>(null);
  const [live, setLive] = useState<MetricsBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      api.portfolioFullMetricsWfo(detail.id).catch(() => null),
      api.portfolioLiveMetrics(detail.id, mode).catch(() => null),
    ])
      .then(([a, b]) => {
        setOos(a as MetricsBundle | null);
        setLive(b as MetricsBundle | null);
      })
      .catch((e) => setError(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, [detail.id, mode]);

  const fmtMoney = (v: unknown) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    const abs = Math.abs(n);
    const str = abs >= 1000 ? abs.toLocaleString("en-US", { maximumFractionDigits: 0 }) : abs.toFixed(2);
    return `${n < 0 ? "-" : ""}$${str}`;
  };
  const fmtPct = (v: unknown) => {
    const n = Number(v);
    return Number.isFinite(n) ? `${n.toFixed(1)}%` : "—";
  };
  const fmtNum = (v: unknown, digits = 2) => {
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(digits) : "—";
  };

  const rows: { label: string; key: string; fmt: (v: unknown) => string; dirColor?: boolean }[] = [
    { label: "Net Profit", key: "net_profit", fmt: fmtMoney, dirColor: true },
    { label: "Profit Factor", key: "profit_factor", fmt: (v) => fmtNum(v) },
    { label: "Win Rate", key: "win_rate", fmt: fmtPct },
    { label: "Total Trades", key: "total_trades", fmt: (v) => fmtNum(v, 0) },
    { label: "Avg Trade", key: "avg_trade", fmt: fmtMoney, dirColor: true },
    { label: "Gross Profit", key: "gross_profit", fmt: fmtMoney },
    { label: "Gross Loss", key: "gross_loss", fmt: fmtMoney },
    { label: "Max Drawdown", key: "max_drawdown", fmt: fmtMoney },
    { label: "Sortino", key: "sortino", fmt: (v) => fmtNum(v) },
    { label: "Calmar", key: "calmar_ratio", fmt: (v) => fmtNum(v) },
    { label: "Recovery", key: "recovery_factor", fmt: (v) => fmtNum(v) },
    { label: "Stability (R²)", key: "stability", fmt: (v) => fmtNum(v) },
  ];

  const liveTrades = live?.trade_count ?? 0;
  const since = live?.since ? new Date(live.since).toLocaleDateString() : null;

  return (
    <div className="border border-ag-border rounded-lg p-3 bg-ag-dark/20">
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className="text-sm font-semibold text-ag-text">OOS vs LIVE</div>
          <div className="text-[11px] text-ag-text-muted">
            Walk-forward OOS expectation vs real trades since the portfolio was saved
            {since ? ` (${since})` : ""}.
          </div>
        </div>
        <div className="flex items-center gap-1 text-[11px]">
          {(["real", "paper", "both"] as LiveMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`px-2 py-1 rounded ${
                mode === m
                  ? "bg-ag-accent/25 text-ag-accent"
                  : "bg-ag-dark/30 text-ag-text-muted hover:text-ag-text"
              }`}
            >
              {m[0].toUpperCase() + m.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="text-xs text-ag-text-muted py-2">Loading comparison…</div>}
      {error && <div className="text-xs text-ag-red py-2">{error}</div>}

      {!loading && !error && (
        <>
          {liveTrades === 0 && (
            <div className="text-[11px] text-yellow-400/80 mb-2">
              No {mode} trades yet since save date — live column will be empty. Numbers will
              populate as trades close.
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-ag-text-muted">
                <tr className="border-b border-ag-border">
                  <th className="text-left px-2 py-1">Metric</th>
                  <th className="text-right px-2 py-1">OOS (walk-forward)</th>
                  <th className="text-right px-2 py-1">
                    LIVE ({mode}){liveTrades ? ` · ${liveTrades} trades` : ""}
                  </th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {rows.map((r) => {
                  const o = oos?.metrics?.[r.key];
                  const l = live?.metrics?.[r.key];
                  const oColor = r.dirColor
                    ? Number(o) >= 0
                      ? "text-ag-green"
                      : "text-ag-red"
                    : "";
                  const lColor = r.dirColor
                    ? Number(l) >= 0
                      ? "text-ag-green"
                      : "text-ag-red"
                    : "";
                  return (
                    <tr key={r.key} className="border-b border-ag-border/30 hover:bg-ag-dark/30">
                      <td className="px-2 py-1 text-ag-text-muted">{r.label}</td>
                      <td className={`px-2 py-1 text-right ${oColor}`}>{r.fmt(o)}</td>
                      <td className={`px-2 py-1 text-right ${lColor}`}>{r.fmt(l)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
