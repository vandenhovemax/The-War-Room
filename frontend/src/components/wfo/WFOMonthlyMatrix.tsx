"use client";

import type { MonthlyMatrixData } from "@/lib/api";

interface Props {
  data: MonthlyMatrixData;
  title?: string;
  onCellClick?: (year: number, month: number) => void;
}

const MONTHS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"];
const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function cellColor(v: number): string {
  if (v > 0) return "bg-ag-green/20 text-ag-green";
  if (v < 0) return "bg-ag-red/20 text-ag-red";
  return "text-ag-text-muted";
}

function fmt$(v: number): string {
  if (Math.abs(v) >= 1000) return `$${(v / 1000).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

export default function WFOMonthlyMatrix({ data, title = "Monthly PnL", onCellClick }: Props) {
  if (!data.years.length) return null;

  const clickable = !!onCellClick;

  return (
    <div className="bg-ag-dark/50 border border-ag-border/50 rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs font-medium text-ag-text">{title}</h4>
        {clickable && (
          <span className="text-[9px] text-ag-text-muted">Click a cell to view trades</span>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr className="text-ag-text-muted">
              <th className="text-left px-1 py-0.5 font-medium">Year</th>
              {MONTH_LABELS.map((m) => (
                <th key={m} className="text-right px-1 py-0.5 font-medium">{m}</th>
              ))}
              <th className="text-right px-1 py-0.5 font-medium border-l border-ag-border/50">Total</th>
            </tr>
          </thead>
          <tbody>
            {data.years.map((year) => (
              <tr key={year} className="border-t border-ag-border/30">
                <td className="px-1 py-0.5 font-medium text-ag-text">{year}</td>
                {MONTHS.map((m, idx) => {
                  const val = (data.matrix[year] as Record<string, number> | undefined)?.[m] ?? 0;
                  const hasValue = val !== 0;
                  return (
                    <td
                      key={m}
                      className={`text-right px-1 py-0.5 ${cellColor(val)} ${
                        clickable && hasValue ? "cursor-pointer hover:bg-ag-accent/20 hover:text-ag-accent rounded transition-colors" : ""
                      }`}
                      onClick={() => clickable && hasValue && onCellClick?.(Number(year), idx + 1)}
                      title={clickable && hasValue ? `Click to view ${MONTH_LABELS[idx]} ${year} trades` : undefined}
                    >
                      {hasValue ? fmt$(val) : "—"}
                    </td>
                  );
                })}
                <td className={`text-right px-1 py-0.5 font-medium border-l border-ag-border/50 ${cellColor(data.row_totals[year] ?? 0)}`}>
                  {fmt$(data.row_totals[year] ?? 0)}
                </td>
              </tr>
            ))}
            {/* Column totals */}
            <tr className="border-t border-ag-border font-medium">
              <td className="px-1 py-0.5 text-ag-text-muted">Total</td>
              {MONTHS.map((m) => {
                const val = (data.col_totals as Record<string, number>)[m] ?? 0;
                return (
                  <td key={m} className={`text-right px-1 py-0.5 ${cellColor(val)}`}>
                    {val !== 0 ? fmt$(val) : "—"}
                  </td>
                );
              })}
              <td className={`text-right px-1 py-0.5 border-l border-ag-border/50 ${cellColor(data.grand_total)}`}>
                {fmt$(data.grand_total)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
