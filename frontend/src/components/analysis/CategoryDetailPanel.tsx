"use client";
import type { BatchSymbolSummary } from "@/lib/api";
import { useState } from "react";

interface Props {
  symbols: BatchSymbolSummary[];
}

function fmt(v: number | null | undefined, d: number = 2): string {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  return Math.min(v, 10).toFixed(d);
}

function fmtDollar(v: number): string {
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

type SortKey = "asset" | "net_profit" | "profit_factor" | "win_rate" | "trades";

export default function CategoryDetailPanel({ symbols }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("net_profit");
  const [sortAsc, setSortAsc] = useState(false);

  const sorted = [...symbols].sort((a, b) => {
    if (sortKey === "asset") {
      return sortAsc ? a.asset.localeCompare(b.asset) : b.asset.localeCompare(a.asset);
    }
    const av = (a[sortKey] ?? 0) as number;
    const bv = (b[sortKey] ?? 0) as number;
    return sortAsc ? av - bv : bv - av;
  });

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  // Bar chart scale: find max absolute net profit for proportional bars
  const maxAbs = Math.max(...symbols.map((s) => Math.abs(s.net_profit)), 1);

  const hdr = (key: SortKey, label: string) => (
    <th
      onClick={() => handleSort(key)}
      className="px-2 py-1.5 text-left text-[10px] font-medium text-ag-text-muted cursor-pointer hover:text-ag-text select-none"
    >
      {label} {sortKey === key ? (sortAsc ? "▲" : "▼") : ""}
    </th>
  );

  return (
    <div className="space-y-3 pt-1">
      {/* Profit bar chart */}
      <div className="space-y-1">
        <p className="text-[10px] text-ag-text-muted font-medium">Net Profit by Asset</p>
        {sorted.map((s) => {
          const pct = (Math.abs(s.net_profit) / maxAbs) * 100;
          const positive = s.net_profit > 0;
          return (
            <div key={s.asset} className="flex items-center gap-2 text-[11px]">
              <span className="w-28 truncate text-ag-text-muted" title={s.asset}>
                {s.asset.replace(/_Long|_Short/g, "")}
                <span className="text-[9px] opacity-60">
                  {s.asset.includes("_Long") ? " L" : s.asset.includes("_Short") ? " S" : ""}
                </span>
              </span>
              <div className="flex-1 h-4 bg-ag-darker rounded overflow-hidden relative">
                <div
                  className={`h-full rounded ${positive ? "bg-ag-green/60" : "bg-ag-red/60"}`}
                  style={{ width: `${Math.max(pct, 1)}%` }}
                />
              </div>
              <span className={`w-20 text-right text-[10px] font-medium ${positive ? "text-ag-green" : "text-ag-red"}`}>
                {fmtDollar(s.net_profit)}
              </span>
            </div>
          );
        })}
      </div>

      {/* Asset table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="border-b border-ag-border/50">
            <tr>
              {hdr("asset", "Asset")}
              {hdr("net_profit", "Net Profit")}
              {hdr("profit_factor", "PF")}
              {hdr("win_rate", "Win Rate")}
              {hdr("trades", "Positions")}
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => (
              <tr key={s.asset} className="border-b border-ag-border/30 hover:bg-ag-card-hover/50">
                <td className="px-2 py-1.5 font-medium">{s.asset}</td>
                <td className={`px-2 py-1.5 ${s.net_profit > 0 ? "text-ag-green" : s.net_profit < 0 ? "text-ag-red" : ""}`}>
                  {fmtDollar(s.net_profit)}
                </td>
                <td className="px-2 py-1.5">{fmt(s.profit_factor)}</td>
                <td className="px-2 py-1.5">{fmt(s.win_rate)}%</td>
                <td className="px-2 py-1.5">{s.trades}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
