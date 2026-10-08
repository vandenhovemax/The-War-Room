"use client";
import { useState, useMemo } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { TrendingUp, ArrowUpDown } from "lucide-react";
import type { EventWFOResult } from "@/lib/api";

interface Props {
  result: EventWFOResult;
}

type SortKey = "asset" | "selected" | "eligible" | "pct";

export default function EventWFOResultsPanel({ result }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("pct");
  const [sortAsc, setSortAsc] = useState(false);

  const chartData = useMemo(() => {
    // Prefer per-trade selected_records (smooth true curve) when available.
    // Falls back to the per-window stitched equity_curve for non-hybrid event runs.
    const recs = (result as unknown as { selected_records?: { dt: string; pnl: number }[] }).selected_records;
    if (recs && recs.length > 0) {
      const sorted = [...recs].sort((a, b) => a.dt.localeCompare(b.dt));
      let cum = 0;
      return sorted.map(r => {
        cum += r.pnl;
        return { date: (r.dt || "").slice(0, 10), pnl: Number(cum.toFixed(2)) };
      });
    }
    return result.equity_curve.map((val, i) => ({
      date: result.equity_dates[i] ?? "",
      pnl: val,
    }));
  }, [result]);

  const s = result.summary;

  const assetRows = useMemo(() => {
    const entries = Object.entries(result.asset_frequency).map(([asset, freq]) => ({
      asset,
      ...freq,
    }));
    entries.sort((a, b) => {
      const aVal = sortKey === "asset" ? a.asset : a[sortKey];
      const bVal = sortKey === "asset" ? b.asset : b[sortKey];
      if (typeof aVal === "string" && typeof bVal === "string")
        return sortAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      return sortAsc ? (aVal as number) - (bVal as number) : (bVal as number) - (aVal as number);
    });
    return entries;
  }, [result.asset_frequency, sortKey, sortAsc]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  return (
    <div className="space-y-4">
      {/* Risk profile diagnostic banner */}
      {result.risk_profile_diagnostic && (
        <RiskProfileBannerEvent diag={result.risk_profile_diagnostic} />
      )}

      {/* Header */}
      <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <TrendingUp size={16} className="text-ag-accent" />
          Selection Engine Performance
        </h3>

        {/* Equity Curve */}
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--ag-border)" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--ag-text-muted)" }}
                tickFormatter={(v: string) => v.slice(5)} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: "var(--ag-text-muted)" }}
                tickFormatter={(v: number) => `$${v.toFixed(0)}`} />
              <Tooltip contentStyle={{ background: "var(--ag-card)", border: "1px solid var(--ag-border)", borderRadius: 8, fontSize: 12 }}
                formatter={(v: number) => [`$${v.toFixed(2)}`, "Cumulative PnL"]} />
              <Line type="monotone" dataKey="pnl" stroke="#6366f1" dot={false} strokeWidth={1.5} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p className="text-xs text-ag-text-muted text-center">
          Composite equity curve — only trades where the asset was in the selected portfolio.
        </p>
      </div>

      {/* Summary Metrics */}
      <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-3">
        <h3 className="text-sm font-semibold">Summary Metrics</h3>
        <div className="grid grid-cols-4 gap-3">
          <Metric label="Net Profit" value={`$${s.net_profit?.toFixed(2) ?? "0"}`} />
          <Metric label="Profit Factor" value={s.pf?.toFixed(2) ?? "0"} />
          <Metric label="Win Rate" value={`${s.wr?.toFixed(1) ?? "0"}%`} />
          <Metric label="Max DD" value={`$${s.max_dd?.toFixed(2) ?? "0"}`} />
        </div>
        <div className="grid grid-cols-4 gap-3">
          <Metric label="Sortino" value={s.sortino?.toFixed(3) ?? "0"} />
          <Metric label="Calmar" value={s.calmar?.toFixed(3) ?? "0"} />
          <Metric label="Recovery" value={s.recovery?.toFixed(3) ?? "0"} />
          <Metric label="Ulcer" value={s.ulcer?.toFixed(3) ?? "0"} />
        </div>
        <div className="grid grid-cols-3 gap-3">
          <Metric label="Total Steps" value={String(s.total_steps ?? 0)} />
          <Metric label="Selected Trades" value={String(result.selected_trade_count)} />
          <Metric label="Avg Portfolio" value={`${s.avg_portfolio_size?.toFixed(1) ?? "0"} assets`} />
        </div>
      </div>

      {/* Asset Frequency Table */}
      <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-3">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <ArrowUpDown size={14} className="text-ag-accent" />
          Asset Selection Frequency
        </h3>
        <div className="max-h-72 overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-ag-card">
              <tr className="text-ag-text-muted border-b border-ag-border">
                <Th label="Asset" sortKey="asset" currentKey={sortKey} asc={sortAsc} onSort={toggleSort} />
                <Th label="Selected" sortKey="selected" currentKey={sortKey} asc={sortAsc} onSort={toggleSort} align="right" />
                <Th label="Eligible" sortKey="eligible" currentKey={sortKey} asc={sortAsc} onSort={toggleSort} align="right" />
                <Th label="%" sortKey="pct" currentKey={sortKey} asc={sortAsc} onSort={toggleSort} align="right" />
              </tr>
            </thead>
            <tbody>
              {assetRows.map((r) => (
                <tr key={r.asset} className="border-b border-ag-border/30 hover:bg-ag-dark/30">
                  <td className="py-1.5 px-2 font-mono">{r.asset}</td>
                  <td className="py-1.5 px-2 text-right">{r.selected}</td>
                  <td className="py-1.5 px-2 text-right">{r.eligible}</td>
                  <td className="py-1.5 px-2 text-right font-medium">
                    <span className={r.pct >= 50 ? "text-ag-green" : r.pct >= 25 ? "text-ag-text" : "text-ag-text-muted"}>
                      {r.pct.toFixed(1)}%
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-ag-dark/50 rounded-lg p-2.5 text-center">
      <div className="text-[10px] text-ag-text-muted mb-0.5">{label}</div>
      <div className="text-sm font-semibold">{value}</div>
    </div>
  );
}

function Th({ label, sortKey, currentKey, asc, onSort, align = "left" }: {
  label: string; sortKey: SortKey; currentKey: SortKey; asc: boolean;
  onSort: (k: SortKey) => void; align?: "left" | "right";
}) {
  const active = currentKey === sortKey;
  return (
    <th className={`py-1.5 px-2 cursor-pointer hover:text-ag-text ${align === "right" ? "text-right" : "text-left"}`}
      onClick={() => onSort(sortKey)}>
      {label} {active ? (asc ? "\u2191" : "\u2193") : ""}
    </th>
  );
}

const MODE_LABELS: Record<string, string> = {
  fixed: "Fixed $", pct: "% of Equity", martingale: "Martingale",
  anti_martingale: "Anti-Martingale", kelly: "Kelly Criterion",
};

function RiskProfileBannerEvent({ diag }: {
  diag: NonNullable<import("@/lib/api").EventWFOResult["risk_profile_diagnostic"]>;
}) {
  const modeLabel = MODE_LABELS[diag.mode] ?? diag.mode;
  const worked = diag.scaling_worked;
  const borderColor = worked ? "border-ag-accent/40" : "border-amber-500/40";
  const bgColor = worked ? "bg-ag-accent/5" : "bg-amber-500/5";

  return (
    <div className={`${bgColor} border ${borderColor} rounded-lg px-4 py-3 space-y-2`}>
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-ag-accent">Position Sizing Applied</span>
        <span className="text-xs text-ag-text-muted">
          {modeLabel}
          {diag.mode === "fixed" && ` \u2014 $${diag.value}`}
          {diag.mode === "pct" && ` \u2014 ${diag.value}% of $${diag.initial_capital.toLocaleString()}`}
          {(diag.mode === "martingale" || diag.mode === "anti_martingale") && ` \u2014 $${diag.value}`}
          {diag.mode === "kelly" && ` \u2014 starting $${diag.initial_capital.toLocaleString()}`}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-3 text-xs">
        <div>
          <span className="text-ag-text-muted">Before: </span>
          <span className="font-mono">${diag.pre_total_pnl.toLocaleString()}</span>
        </div>
        <div>
          <span className="text-ag-text-muted">After: </span>
          <span className="font-mono">${diag.post_total_pnl.toLocaleString()}</span>
        </div>
        <div>
          <span className="text-ag-text-muted">Change: </span>
          <span className={`font-mono ${diag.pnl_change_pct >= 0 ? "text-ag-green" : "text-ag-red"}`}>
            {diag.pnl_change_pct >= 0 ? "+" : ""}{diag.pnl_change_pct}%
          </span>
        </div>
      </div>
      {diag.note && (
        <p className="text-[10px] text-ag-text-muted leading-tight mt-1">{diag.note}</p>
      )}
      {!worked && (
        <p className="text-[10px] text-amber-400 font-medium">
          Scaling had no effect on total PnL. Check that risk value is different from batch risk.
        </p>
      )}
    </div>
  );
}
