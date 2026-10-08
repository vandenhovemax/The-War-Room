"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { MonthlyRecapData } from "@/lib/api";

interface Props {
  year: number;
  month: number; // 0-indexed (JS convention)
}

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function fmt(n: number, decimals = 2): string {
  return n.toFixed(decimals);
}

function fmtDate(d: string): string {
  const dt = new Date(d + "T12:00:00");
  return dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export default function MonthlyRecap({ year, month }: Props) {
  const [data, setData] = useState<MonthlyRecapData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.journalMonthlyRecap(year, month + 1)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [year, month]);

  if (loading) {
    return (
      <div className="bg-ag-card border border-ag-border rounded-xl p-6">
        <p className="text-sm text-ag-text-muted animate-pulse">Loading monthly recap...</p>
      </div>
    );
  }

  if (!data || data.total_trades === 0) {
    return (
      <div className="bg-ag-card border border-ag-border rounded-xl p-6">
        <p className="text-sm text-ag-text-muted">No trades for {MONTH_NAMES[month]} {year}.</p>
      </div>
    );
  }

  const pnlColor = data.total_pnl >= 0 ? "text-ag-green" : "text-ag-red";
  const pnlSign = data.total_pnl >= 0 ? "+" : "";

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-base">
          {MONTH_NAMES[month]} {year} Recap
        </h3>
        <span className={`text-lg font-bold ${pnlColor}`}>
          {pnlSign}${fmt(data.total_pnl)}
        </span>
      </div>

      {/* KPI Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KPI label="Total Trades" value={String(data.total_trades)} />
        <KPI label="Win Rate" value={`${fmt(data.win_rate, 1)}%`}
          color={data.win_rate >= 50 ? "text-ag-green" : "text-ag-red"} />
        <KPI label="Avg Trade" value={`$${fmt(data.avg_trade_pnl)}`}
          color={data.avg_trade_pnl >= 0 ? "text-ag-green" : "text-ag-red"} />
        <KPI label="Total Fees" value={`$${fmt(data.total_fees)}`} />
        <KPI label="Wins" value={String(data.win_count)} color="text-ag-green" />
        <KPI label="Losses" value={String(data.loss_count)} color="text-ag-red" />
        {data.best_day && (
          <KPI label="Best Day" value={`+$${fmt(data.best_day.pnl)}`}
            sub={fmtDate(data.best_day.date)} color="text-ag-green" />
        )}
        {data.worst_day && (
          <KPI label="Worst Day" value={`$${fmt(data.worst_day.pnl)}`}
            sub={fmtDate(data.worst_day.date)} color="text-ag-red" />
        )}
      </div>

      {/* Assets breakdown */}
      {data.assets.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-ag-text-muted mb-2">
            Assets Traded ({data.assets.length})
          </h4>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-ag-text-muted border-b border-ag-border">
                  <th className="text-left py-1.5 pr-3">Symbol</th>
                  <th className="text-right py-1.5 px-2">Trades</th>
                  <th className="text-right py-1.5 px-2">P&L</th>
                  <th className="text-right py-1.5 pl-2">Win Rate</th>
                </tr>
              </thead>
              <tbody>
                {data.assets.map((a) => (
                  <tr key={a.symbol} className="border-b border-ag-border/50 hover:bg-ag-card-hover">
                    <td className="py-1.5 pr-3 font-medium">{a.symbol}</td>
                    <td className="text-right py-1.5 px-2">{a.trades}</td>
                    <td className={`text-right py-1.5 px-2 font-medium ${a.pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                      {a.pnl >= 0 ? "+" : ""}${fmt(a.pnl)}
                    </td>
                    <td className="text-right py-1.5 pl-2">{fmt(a.win_rate, 1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function KPI({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div className="bg-ag-dark border border-ag-border/50 rounded-lg px-3 py-2">
      <p className="text-[10px] text-ag-text-muted">{label}</p>
      <p className={`text-sm font-semibold ${color ?? "text-ag-text"}`}>{value}</p>
      {sub && <p className="text-[9px] text-ag-text-muted">{sub}</p>}
    </div>
  );
}
