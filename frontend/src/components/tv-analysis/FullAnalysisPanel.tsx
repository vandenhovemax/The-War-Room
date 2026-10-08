"use client";

import { useState, useEffect } from "react";
import { api, DashboardAnalysisRequest, TVFullAnalysis, TVAnalysisRow } from "@/lib/api";

interface Props {
  req: DashboardAnalysisRequest;
}

function fmtVal(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  return `${v.toFixed(2)}%`;
}

function valColor(v: number | string | null | undefined): string {
  if (v === null || v === undefined || typeof v === "string") return "";
  return v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";
}

function SectionTable({ title, rows }: { title: string; rows: TVAnalysisRow[] }) {
  if (!rows.length) return null;
  const hasLong = rows.some((r) => r.long_val != null || r.long_pct != null);
  const hasShort = rows.some((r) => r.short_val != null || r.short_pct != null);

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg overflow-hidden">
      <h3 className="text-xs font-semibold text-ag-text-muted uppercase tracking-wider px-4 py-2 bg-ag-darker border-b border-ag-border">
        {title}
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-ag-border text-ag-text-muted">
              <th className="text-left px-3 py-1.5 font-medium w-[30%]" />
              <th className="text-right px-2 py-1.5 font-medium">All USDT</th>
              <th className="text-right px-2 py-1.5 font-medium">All %</th>
              {hasLong && <th className="text-right px-2 py-1.5 font-medium">Long USDT</th>}
              {hasLong && <th className="text-right px-2 py-1.5 font-medium">Long %</th>}
              {hasShort && <th className="text-right px-2 py-1.5 font-medium">Short USDT</th>}
              {hasShort && <th className="text-right px-2 py-1.5 font-medium">Short %</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-ag-border/50 hover:bg-ag-darker/30">
                <td className="px-3 py-1.5 text-ag-text-muted font-medium">{row.label}</td>
                <td className={`text-right px-2 py-1.5 ${valColor(row.all_val)}`}>
                  {fmtVal(row.all_val)}
                </td>
                <td className={`text-right px-2 py-1.5 ${valColor(row.all_pct)}`}>
                  {fmtPct(row.all_pct)}
                </td>
                {hasLong && (
                  <td className={`text-right px-2 py-1.5 ${valColor(row.long_val)}`}>
                    {fmtVal(row.long_val)}
                  </td>
                )}
                {hasLong && (
                  <td className={`text-right px-2 py-1.5 ${valColor(row.long_pct)}`}>
                    {fmtPct(row.long_pct)}
                  </td>
                )}
                {hasShort && (
                  <td className={`text-right px-2 py-1.5 ${valColor(row.short_val)}`}>
                    {fmtVal(row.short_val)}
                  </td>
                )}
                {hasShort && (
                  <td className={`text-right px-2 py-1.5 ${valColor(row.short_pct)}`}>
                    {fmtPct(row.short_pct)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function FullAnalysisPanel({ req }: Props) {
  const [data, setData] = useState<TVFullAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!req.strategy_filename && !req.batch_id && !req.run_id && !req.portfolio_id) return;
    setLoading(true);
    setError("");
    api.dashboardFullAnalysis(req)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }, [req.strategy_filename, req.batch_id, req.portfolio_id, req.run_id, req.side_filter, req.date_to, req.min_trades]);

  if (loading) return <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted">Loading full analysis...</div>;
  if (error) return <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-red text-sm">{error}</div>;
  if (!data) return null;

  return (
    <div className="space-y-4">
      <SectionTable title="Performance" rows={data.performance} />
      <SectionTable title="Trades Analysis" rows={data.trades_analysis} />
      <SectionTable title="Risk-Adjusted Performance" rows={data.risk_adjusted} />
    </div>
  );
}
