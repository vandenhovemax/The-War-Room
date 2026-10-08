"use client";

import { useState, useEffect } from "react";
import { api, DashboardAnalysisRequest, MonthlyMatrixData } from "@/lib/api";

interface Props {
  req: DashboardAnalysisRequest;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function cellColor(val: number, maxAbs: number): string {
  if (val === 0) return "bg-ag-card";
  const intensity = Math.min(Math.abs(val) / Math.max(maxAbs, 1), 1);
  if (val > 0) return `bg-ag-green/${Math.round(10 + intensity * 30)}`;
  return `bg-ag-red/${Math.round(10 + intensity * 30)}`;
}

export default function MonthlyMatrix({ req }: Props) {
  const [data, setData] = useState<MonthlyMatrixData | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!req.strategy_filename && !req.batch_id && !req.run_id && !req.portfolio_id) return;
    setLoading(true);
    api.dashboardMonthlyMatrix(req)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [req.strategy_filename, req.batch_id, req.portfolio_id, req.run_id, req.side_filter, req.date_to, req.min_trades]);

  if (loading) return <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted">Loading monthly data...</div>;
  if (!data || !data.years.length) return null;

  const allVals = data.years.flatMap((y) =>
    Object.values(data.matrix[y] || {})
  );
  const maxAbs = Math.max(...allVals.map(Math.abs), 1);

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4 overflow-x-auto">
      <h3 className="text-sm font-semibold text-ag-text mb-3">Monthly P&L Matrix</h3>
      <table className="w-full text-xs">
        <thead>
          <tr>
            <th className="text-left py-1 px-2 text-ag-text-muted">Year</th>
            {MONTHS.map((m) => (
              <th key={m} className="text-right py-1 px-2 text-ag-text-muted">{m}</th>
            ))}
            <th className="text-right py-1 px-2 text-ag-text-muted font-bold">Total</th>
          </tr>
        </thead>
        <tbody>
          {data.years.map((year) => (
            <tr key={year} className="border-t border-ag-border/50">
              <td className="py-1.5 px-2 text-ag-text font-medium">{year}</td>
              {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => {
                const val = data.matrix[year]?.[m] ?? 0;
                return (
                  <td key={m} className={`py-1.5 px-2 text-right ${val > 0 ? "text-ag-green" : val < 0 ? "text-ag-red" : "text-ag-text-muted"}`}>
                    {val !== 0 ? `$${val.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
                  </td>
                );
              })}
              <td className={`py-1.5 px-2 text-right font-semibold ${(data.row_totals[year] ?? 0) >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                ${(data.row_totals[year] ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </td>
            </tr>
          ))}
          {/* Column totals */}
          <tr className="border-t-2 border-ag-border font-semibold">
            <td className="py-1.5 px-2 text-ag-text">Total</td>
            {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => {
              const val = data.col_totals[m] ?? 0;
              return (
                <td key={m} className={`py-1.5 px-2 text-right ${val >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                  ${val.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
              );
            })}
            <td className={`py-1.5 px-2 text-right ${data.grand_total >= 0 ? "text-ag-green" : "text-ag-red"}`}>
              ${data.grand_total.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
