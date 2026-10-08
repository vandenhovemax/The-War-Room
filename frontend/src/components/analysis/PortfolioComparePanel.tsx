"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { PortfolioCompareEntry } from "@/lib/api";
import EquityCurveOverlay from "./metrics/EquityCurveOverlay";

interface Props {
  portfolioIds: number[];
}

const METRIC_KEYS: { key: string; label: string; higher_better: boolean }[] = [
  { key: "net_profit", label: "Net Profit", higher_better: true },
  { key: "trades", label: "Trades", higher_better: true },
  { key: "profit_factor", label: "Profit Factor", higher_better: true },
  { key: "win_rate", label: "Win Rate %", higher_better: true },
  { key: "max_drawdown", label: "Max Drawdown", higher_better: false },
  { key: "avg_trade", label: "Avg Trade", higher_better: true },
];

const OOS_METRIC_KEYS: { key: string; label: string; higher_better: boolean; fromOosMetrics?: boolean; pct?: boolean }[] = [
  { key: "net_profit", label: "OOS Net Profit", higher_better: true, fromOosMetrics: true },
  { key: "profit_factor", label: "OOS Profit Factor", higher_better: true, fromOosMetrics: true },
  { key: "win_rate", label: "OOS Win Rate %", higher_better: true, fromOosMetrics: true, pct: true },
  { key: "max_drawdown", label: "OOS Max Drawdown", higher_better: false, fromOosMetrics: true },
  { key: "wfe_pf", label: "WFE (Edge) %", higher_better: true, pct: true },
  { key: "return_to_dd_ratio", label: "Return / MaxDD", higher_better: true },
  { key: "oos_trades", label: "OOS Trades", higher_better: true },
];

function gradeColor(g: string | null): string {
  if (!g) return "text-ag-text-muted";
  if (g === "A" || g === "A+") return "text-ag-green";
  if (g === "B" || g === "B+") return "text-blue-400";
  if (g === "C" || g === "C+") return "text-yellow-400";
  if (g === "D" || g === "D+") return "text-orange-400";
  return "text-ag-red";
}

export default function PortfolioComparePanel({ portfolioIds }: Props) {
  const [data, setData] = useState<PortfolioCompareEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const r = await api.portfolioCompare(portfolioIds);
      setData(r.portfolios);
      setLoaded(true);
    } catch {
      setData([]);
    }
    setLoading(false);
  };

  if (!loaded) {
    return (
      <div className="text-center py-4">
        <button onClick={run} disabled={loading}
          className="px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
          {loading ? "Loading..." : `Compare ${portfolioIds.length} Portfolios`}
        </button>
      </div>
    );
  }

  if (data.length < 2) {
    return <p className="text-sm text-ag-text-muted">Not enough data to compare.</p>;
  }

  // Find best values for highlighting
  const bestIdx: Record<string, number> = {};
  for (const mk of METRIC_KEYS) {
    let bestI = 0;
    let bestV = Number(data[0].metrics[mk.key] ?? 0);
    for (let i = 1; i < data.length; i++) {
      const v = Number(data[i].metrics[mk.key] ?? 0);
      const isBetter = mk.higher_better ? v > bestV : v < bestV;
      if (isBetter) { bestV = v; bestI = i; }
    }
    bestIdx[mk.key] = bestI;
  }

  // Check if any portfolio has OOS data
  const hasOos = data.some((p) => p.cutoff);

  // Best OOS values for highlighting
  const oosBestIdx: Record<string, number> = {};
  if (hasOos) {
    for (const mk of OOS_METRIC_KEYS) {
      let bestI = -1;
      let bestV = -Infinity;
      for (let i = 0; i < data.length; i++) {
        const raw = mk.fromOosMetrics
          ? data[i].oos_metrics?.[mk.key]
          : (data[i] as unknown as Record<string, unknown>)[mk.key];
        const v = Number(raw ?? NaN);
        if (isNaN(v)) continue;
        const cmp = mk.higher_better ? v > bestV : v < bestV;
        if (bestI === -1 || cmp) { bestV = v; bestI = i; }
      }
      if (bestI >= 0) oosBestIdx[mk.key] = bestI;
    }
  }

  // Build equity curve series
  const eqSeries = hasOos
    ? data
        .filter((p) => p.oos_equity_curve && p.oos_equity_curve.length >= 2)
        .map((p) => ({ name: p.name, points: p.oos_equity_curve }))
    : [];

  return (
    <div className="space-y-4">
      {/* ── Basic Metrics ── */}
      <h4 className="text-sm font-semibold text-ag-text">Portfolio Comparison</h4>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr>
              <th className="text-left py-1.5 px-2 text-ag-text-muted">Metric</th>
              {data.map((p) => (
                <th key={p.id} className="text-right py-1.5 px-2 text-ag-text-muted">
                  {p.name.length > 20 ? p.name.slice(0, 20) + "..." : p.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {METRIC_KEYS.map((mk) => (
              <tr key={mk.key} className="border-t border-ag-border/50">
                <td className="py-1.5 px-2 text-ag-text">{mk.label}</td>
                {data.map((p, i) => {
                  const v = p.metrics[mk.key];
                  const isBest = bestIdx[mk.key] === i;
                  return (
                    <td key={p.id}
                      className={`py-1.5 px-2 text-right font-mono ${isBest ? "text-ag-green font-semibold" : "text-ag-text"}`}>
                      {v != null ? (typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : v) : "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── OOS Metrics ── */}
      {hasOos && (
        <>
          <h4 className="text-sm font-semibold text-ag-text mt-2">Out-of-Sample Comparison</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr>
                  <th className="text-left py-1.5 px-2 text-ag-text-muted">OOS Metric</th>
                  {data.map((p) => (
                    <th key={p.id} className="text-right py-1.5 px-2 text-ag-text-muted">
                      {p.name.length > 20 ? p.name.slice(0, 20) + "..." : p.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {/* Grade row */}
                <tr className="border-t border-ag-border/50">
                  <td className="py-1.5 px-2 text-ag-text">Grade</td>
                  {data.map((p) => (
                    <td key={p.id} className={`py-1.5 px-2 text-right font-mono font-semibold ${gradeColor(p.portfolio_grade ?? null)}`}>
                      {p.portfolio_grade ?? "—"}
                    </td>
                  ))}
                </tr>
                {/* Numeric OOS metrics */}
                {OOS_METRIC_KEYS.map((mk) => (
                  <tr key={mk.key} className="border-t border-ag-border/50">
                    <td className="py-1.5 px-2 text-ag-text">{mk.label}</td>
                    {data.map((p, i) => {
                      const raw = mk.fromOosMetrics
                        ? p.oos_metrics?.[mk.key]
                        : (p as unknown as Record<string, unknown>)[mk.key];
                      const v = raw != null ? Number(raw) : null;
                      const isBest = oosBestIdx[mk.key] === i;
                      let display = "—";
                      if (v != null && !isNaN(v)) {
                        display = mk.pct
                          ? `${(v * (v <= 1 && mk.key !== "win_rate" ? 100 : 1)).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`
                          : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
                      }
                      return (
                        <td key={p.id}
                          className={`py-1.5 px-2 text-right font-mono ${isBest ? "text-ag-green font-semibold" : "text-ag-text"}`}>
                          {p.cutoff ? display : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
                {/* Cutoff date row */}
                <tr className="border-t border-ag-border/50">
                  <td className="py-1.5 px-2 text-ag-text">Cutoff Date</td>
                  {data.map((p) => (
                    <td key={p.id} className="py-1.5 px-2 text-right font-mono text-ag-text-muted">
                      {p.cutoff ?? "—"}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>

          {/* ── OOS Equity Curves Overlay ── */}
          {eqSeries.length >= 2 && (
            <EquityCurveOverlay series={eqSeries} title="OOS Equity Curves — All Portfolios" />
          )}
        </>
      )}
    </div>
  );
}
