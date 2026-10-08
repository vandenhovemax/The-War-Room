"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { OOSCompareResult } from "@/lib/api";

interface RiskProfile {
  mode: string;
  value: number;
  initialCapital?: number;
  multiplier?: number;
  maxStreak?: number;
  compound?: string;
  scope?: string;
  countMode?: string;
  oosReset?: boolean;
  startAfter?: number;
  resetAfter?: number;
  kellyN?: number;
  kellyWarmup?: number;
}

interface PerBatchConfig {
  sizingMaster: string;
  batchConfigs: string;
}

interface OOSResultsPanelProps {
  batchId: number;
  oosDate: string;
  minTrades?: number;
  /** Active risk profile for OOS scaling */
  riskProfile?: RiskProfile;
  /** Per-batch risk config */
  perBatchConfig?: PerBatchConfig;
}

const KEY_METRICS = [
  { key: "Net Profit", label: "Net Profit", fmt: "$" },
  { key: "Profit Factor", label: "Profit Factor", fmt: "x" },
  { key: "Win Rate %", label: "Win Rate", fmt: "%" },
  { key: "Total Positions", label: "Positions", fmt: "#" },
  { key: "Max Drawdown", label: "Max DD", fmt: "$" },
  { key: "Sharpe Ratio", label: "Sharpe", fmt: "x" },
  { key: "Sortino Ratio", label: "Sortino", fmt: "x" },
  { key: "Recovery Factor", label: "Recovery Factor", fmt: "x" },
  { key: "Avg Trade", label: "Avg Trade", fmt: "$" },
  { key: "Calmar Ratio", label: "Calmar", fmt: "x" },
];

function fmtVal(v: number | string | null | undefined, fmt: string): string {
  if (v == null || v === "") return "—";
  const n = typeof v === "string" ? parseFloat(v) : v;
  if (isNaN(n)) return String(v);
  if (fmt === "$") return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (fmt === "%") return `${n.toFixed(1)}%`;
  if (fmt === "#") return n.toLocaleString();
  return n.toFixed(2);
}

function colorVal(v: number | string | null | undefined): string {
  if (v == null) return "";
  const n = typeof v === "string" ? parseFloat(v) : v;
  if (isNaN(n)) return "";
  return n > 0 ? "text-ag-green" : n < 0 ? "text-ag-red" : "";
}

function wfeColor(wfe: number): string {
  if (wfe >= 0.8) return "text-ag-green";
  if (wfe >= 0.5) return "text-yellow-400";
  return "text-ag-red";
}

export default function OOSResultsPanel({ batchId, oosDate, minTrades, riskProfile, perBatchConfig }: OOSResultsPanelProps) {
  const [data, setData] = useState<OOSCompareResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [clipIS, setClipIS] = useState(true);
  const [clipOOS, setClipOOS] = useState(true);
  const [oosEndDate, setOosEndDate] = useState("");

  // Auto-populate OOS end date from the data on first load
  useEffect(() => {
    if (data?.trade_date_range?.to && !oosEndDate) {
      setOosEndDate(data.trade_date_range.to);
    }
  }, [data, oosEndDate]);

  useEffect(() => {
    setLoading(true);
    setError("");
    api.dashboardOOSCompare(
      oosDate, batchId, undefined, undefined, undefined, undefined, undefined, minTrades, clipIS, clipOOS, oosEndDate || undefined,
      undefined,
      riskProfile?.mode && riskProfile.value > 0 ? riskProfile : undefined,
      perBatchConfig,
    )
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load OOS data"))
      .finally(() => setLoading(false));
  }, [batchId, oosDate, minTrades, clipIS, clipOOS, oosEndDate, riskProfile, perBatchConfig]);

  if (loading) return <p className="text-ag-text-muted text-sm py-8 text-center">Loading OOS analysis...</p>;
  if (error) return <p className="text-ag-red text-sm py-8 text-center">{error}</p>;
  if (!data) return null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-ag-card border border-ag-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <h3 className="text-sm font-medium">OOS Reveal — Cutoff: {oosDate}</h3>
          <div className="flex items-center gap-4 flex-wrap">
            <label className="flex items-center gap-1.5 text-xs cursor-pointer" title="Force-close IS trades at boundary close price. OFF = count full PnL.">
              <input type="checkbox" checked={clipIS}
                onChange={(e) => setClipIS(e.target.checked)}
                className="accent-ag-accent" />
              <span className="text-ag-text-muted">Clip IS</span>
            </label>
            <div className="flex items-center gap-1.5">
              <label className="flex items-center gap-1.5 text-xs cursor-pointer" title="Force-close OOS trades at OOS End close price. OFF = exclude OOS trades exiting after OOS End.">
                <input type="checkbox" checked={clipOOS}
                  onChange={(e) => setClipOOS(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-ag-text-muted">Clip OOS</span>
              </label>
              <input type="date" value={oosEndDate}
                onChange={(e) => setOosEndDate(e.target.value)}
                title="OOS End Date — boundary for Clip OOS. Auto-detected from data."
                className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-[11px] w-[120px]" />
            </div>
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          <div>
            <span className="text-ag-text-muted">IS Trades</span>
            <p className="font-medium">{data.is_trades.toLocaleString()}</p>
          </div>
          <div>
            <span className="text-ag-text-muted">OOS Trades</span>
            <p className="font-medium">{data.oos_trades.toLocaleString()}</p>
          </div>
          <div>
            <span className="text-ag-text-muted">WFE (Edge)</span>
            <p className={`font-bold text-lg ${wfeColor(data.aggregate_wfe_pf ?? data.aggregate_wfe)}`}
              title="PF-based WFE: (OOS_PF-1)/(IS_PF-1) — edge retention">
              {((data.aggregate_wfe_pf ?? data.aggregate_wfe) * 100).toFixed(1)}%
            </p>
          </div>
          <div>
            <span className="text-ag-text-muted">WFE (Rate)</span>
            <p className={`font-medium text-sm ${wfeColor(data.aggregate_wfe)}`}
              title="Time-normalized WFE: OOS daily profit rate / IS daily profit rate">
              {(data.aggregate_wfe * 100).toFixed(1)}%
            </p>
          </div>
          <div>
            <span className="text-ag-text-muted">PF (IS / OOS)</span>
            <p className="font-medium">
              {data.aggregate_is_pf?.toFixed(2) ?? "—"} / {data.aggregate_oos_pf?.toFixed(2) ?? "—"}
            </p>
          </div>
          {data.oos_concurrent && (
            <>
              <div title="Peak number of trades open simultaneously during OOS">
                <span className="text-ag-text-muted">Max Concurrent</span>
                <p className="font-medium">{data.oos_concurrent.max_concurrent}</p>
              </div>
              <div title="Time-weighted average number of trades open simultaneously during OOS">
                <span className="text-ag-text-muted">Avg Concurrent</span>
                <p className="font-medium">{data.oos_concurrent.avg_concurrent}</p>
              </div>
            </>
          )}
        </div>
      </div>

      {/* IS vs OOS Key Metrics Comparison */}
      <div className="bg-ag-card border border-ag-border rounded-lg p-4">
        <h3 className="text-sm font-medium mb-3">IS vs OOS Metrics</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-ag-border text-ag-text-muted text-xs">
                <th className="text-left py-2 pr-4">Metric</th>
                <th className="text-right py-2 px-3">In-Sample</th>
                <th className="text-right py-2 px-3">Out-of-Sample</th>
              </tr>
            </thead>
            <tbody>
              {KEY_METRICS.map((m) => {
                const isVal = data.is_metrics[m.key];
                const oosVal = data.oos_metrics[m.key];
                return (
                  <tr key={m.key} className="border-b border-ag-border/50">
                    <td className="py-1.5 pr-4 text-ag-text-muted">{m.label}</td>
                    <td className={`py-1.5 px-3 text-right ${colorVal(isVal)}`}>
                      {fmtVal(isVal, m.fmt)}
                    </td>
                    <td className={`py-1.5 px-3 text-right ${colorVal(oosVal)}`}>
                      {fmtVal(oosVal, m.fmt)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Per-Asset WFE */}
      <div className="bg-ag-card border border-ag-border rounded-lg p-4">
        <h3 className="text-sm font-medium mb-3">Per-Asset WFE</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-ag-border text-ag-text-muted">
                <th className="text-left py-2 pr-3">Asset</th>
                <th className="text-right py-2 px-2">WFE</th>
                <th className="text-right py-2 px-2">IS PF</th>
                <th className="text-right py-2 px-2">OOS PF</th>
                <th className="text-right py-2 px-2">IS PnL</th>
                <th className="text-right py-2 px-2">OOS PnL</th>
                <th className="text-right py-2 px-2">IS Trades</th>
                <th className="text-right py-2 px-2">OOS Trades</th>
              </tr>
            </thead>
            <tbody>
              {data.per_asset.map((a) => (
                <tr key={a.asset} className="border-b border-ag-border/50">
                  <td className="py-1.5 pr-3 font-medium">
                    {a.asset.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")}
                  </td>
                  <td className={`py-1.5 px-2 text-right font-medium ${wfeColor(a.WFE_PF ?? a.WFE)}`}>
                    {((a.WFE_PF ?? a.WFE) * 100).toFixed(1)}%
                  </td>
                  <td className="py-1.5 px-2 text-right">{a.IS_PF?.toFixed(2) ?? "—"}</td>
                  <td className="py-1.5 px-2 text-right">{a.OOS_PF?.toFixed(2) ?? "—"}</td>
                  <td className={`py-1.5 px-2 text-right ${a.IS_PnL >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                    ${a.IS_PnL.toFixed(2)}
                  </td>
                  <td className={`py-1.5 px-2 text-right ${a.OOS_PnL >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                    ${a.OOS_PnL.toFixed(2)}
                  </td>
                  <td className="py-1.5 px-2 text-right">{a.IS_Trades}</td>
                  <td className="py-1.5 px-2 text-right">{a.OOS_Trades}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
