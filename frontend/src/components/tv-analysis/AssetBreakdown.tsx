"use client";

import { useState, useEffect } from "react";
import { api, DashboardAnalysisRequest, AssetBreakdownItem } from "@/lib/api";

interface Props {
  req: DashboardAnalysisRequest;
}

type SortKey = "asset" | "trades" | "net_profit" | "profit_factor" | "win_rate" | "avg_trade";

export default function AssetBreakdown({ req }: Props) {
  const [assets, setAssets] = useState<AssetBreakdownItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("net_profit");
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => {
    if (!req.strategy_filename && !req.batch_id) return;
    setLoading(true);
    api.dashboardAssetBreakdown(req)
      .then((r) => setAssets(r.assets))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [req.strategy_filename, req.batch_id, req.side_filter]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  const sorted = [...assets].sort((a, b) => {
    const va = a[sortKey] ?? 0;
    const vb = b[sortKey] ?? 0;
    if (typeof va === "string") return sortAsc ? va.localeCompare(vb as string) : (vb as string).localeCompare(va);
    return sortAsc ? (va as number) - (vb as number) : (vb as number) - (va as number);
  });

  if (loading) return <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted">Loading assets...</div>;
  if (!assets.length) return null;

  const cols: { key: SortKey; label: string; fmt: (v: AssetBreakdownItem) => string; align: string }[] = [
    { key: "asset", label: "Asset", fmt: (r) => r.asset, align: "text-left" },
    { key: "trades", label: "Trades", fmt: (r) => String(r.trades), align: "text-right" },
    { key: "net_profit", label: "Net Profit", fmt: (r) => `$${r.net_profit.toLocaleString(undefined, { maximumFractionDigits: 2 })}`, align: "text-right" },
    { key: "profit_factor", label: "PF", fmt: (r) => r.profit_factor !== null ? r.profit_factor.toFixed(2) : "—", align: "text-right" },
    { key: "win_rate", label: "Win %", fmt: (r) => `${r.win_rate.toFixed(1)}%`, align: "text-right" },
    { key: "avg_trade", label: "Avg Trade", fmt: (r) => `$${r.avg_trade.toFixed(2)}`, align: "text-right" },
  ];

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4 overflow-x-auto">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-ag-text">Asset Breakdown</h3>
        <span className="text-xs text-ag-text-muted">{assets.length} assets</span>
      </div>

      <table className="w-full text-xs">
        <thead>
          <tr>
            {cols.map((col) => (
              <th
                key={col.key}
                onClick={() => handleSort(col.key)}
                className={`py-1.5 px-2 ${col.align} text-ag-text-muted cursor-pointer hover:text-ag-text select-none`}
              >
                {col.label}
                {sortKey === col.key && (sortAsc ? " \u25B2" : " \u25BC")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.asset} className="border-t border-ag-border/50 hover:bg-ag-card-hover">
              <td className="py-1.5 px-2 text-ag-text">
                <span className={`inline-block w-2 h-2 rounded-full mr-1.5 ${row.side === "Long" ? "bg-ag-green" : "bg-ag-red"}`} />
                {row.asset}
              </td>
              <td className="py-1.5 px-2 text-right text-ag-text">{row.trades}</td>
              <td className={`py-1.5 px-2 text-right ${row.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                ${row.net_profit.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </td>
              <td className={`py-1.5 px-2 text-right ${(row.profit_factor ?? 0) >= 1.5 ? "text-ag-green" : (row.profit_factor ?? 0) >= 1 ? "text-ag-yellow" : "text-ag-red"}`}>
                {row.profit_factor !== null ? row.profit_factor.toFixed(2) : "—"}
              </td>
              <td className={`py-1.5 px-2 text-right ${row.win_rate >= 50 ? "text-ag-green" : "text-ag-red"}`}>
                {row.win_rate.toFixed(1)}%
              </td>
              <td className={`py-1.5 px-2 text-right ${row.avg_trade >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                ${row.avg_trade.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Summary row */}
      <div className="mt-3 pt-2 border-t border-ag-border flex gap-6 text-xs text-ag-text-muted">
        <span>Total P&L: <span className={assets.reduce((s, a) => s + a.net_profit, 0) >= 0 ? "text-ag-green" : "text-ag-red"}>
          ${assets.reduce((s, a) => s + a.net_profit, 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}
        </span></span>
        <span>Avg PF: {(assets.filter(a => a.profit_factor !== null).reduce((s, a) => s + (a.profit_factor ?? 0), 0) / Math.max(assets.filter(a => a.profit_factor !== null).length, 1)).toFixed(2)}</span>
        <span>Avg WR: {(assets.reduce((s, a) => s + a.win_rate, 0) / Math.max(assets.length, 1)).toFixed(1)}%</span>
      </div>
    </div>
  );
}
