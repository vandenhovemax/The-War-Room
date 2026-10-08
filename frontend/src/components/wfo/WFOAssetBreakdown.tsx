"use client";
import { useState, useMemo } from "react";
import type { WFOWindowCandidate } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

type GroupBy = "asset" | "group" | "timeframe" | "direction";

interface Props {
  breakdown: NonNullable<WFOWindowCandidate["asset_breakdown"]>;
  title?: string;
}

function fmtPct(v: number): string { return `${(v * 100).toFixed(1)}%`; }
function fmtPF(v: number): string { return v.toFixed(2); }
function fmt$(v: number): string {
  const abs = Math.abs(v);
  const str = abs >= 1000
    ? `$${(abs / 1000).toFixed(1)}k`
    : `$${abs.toFixed(0)}`;
  return v < 0 ? `-${str}` : str;
}

function numColor(v: number): string {
  return v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";
}
function wrColor(v: number): string {
  return v >= 0.5 ? "text-ag-green" : v >= 0.4 ? "text-amber-400" : "text-ag-red";
}
function pfColor(v: number): string {
  return v >= 1.5 ? "text-ag-green" : v >= 1.0 ? "text-amber-400" : "text-ag-red";
}

interface AggRow {
  label: string;
  is_wr: number; is_pf: number; is_pnl: number; is_trades: number;
  oos_wr: number; oos_pf: number; oos_pnl: number; oos_trades: number;
  count: number;       // unique assets in this group
  batchCount: number;  // unique batch_ids in this group
}

function aggregateRows(
  items: NonNullable<WFOWindowCandidate["asset_breakdown"]>,
  groupBy: GroupBy,
): AggRow[] {
  const groups: Record<string, typeof items> = {};
  for (const item of items) {
    let key: string;
    if (groupBy === "group") key = item.group;
    else if (groupBy === "timeframe") key = item.timeframe;
    else if (groupBy === "direction") key = item.side;
    else key = `${item.symbol} ${item.side[0]}`;
    if (!groups[key]) groups[key] = [];
    groups[key].push(item);
  }

  return Object.entries(groups).map(([label, items]) => {
    const is_pnl = items.reduce((s, i) => s + i.is_pnl, 0);
    const oos_pnl = items.reduce((s, i) => s + i.oos_pnl, 0);
    const is_trades = items.reduce((s, i) => s + i.is_trades, 0);
    const oos_trades = items.reduce((s, i) => s + i.oos_trades, 0);
    // Weighted WR by trade count
    const is_wr = is_trades > 0
      ? items.reduce((s, i) => s + i.is_wr * i.is_trades, 0) / is_trades : 0;
    const oos_wr = oos_trades > 0
      ? items.reduce((s, i) => s + i.oos_wr * i.oos_trades, 0) / oos_trades : 0;
    // Aggregate PF from gross profit / gross loss
    const is_gp = items.reduce((s, i) => s + Math.max(i.is_pnl, 0), 0);
    const is_gl = items.reduce((s, i) => s + Math.min(i.is_pnl, 0), 0);
    const is_pf = is_gl !== 0 ? Math.min(is_gp / Math.abs(is_gl), 10) : (is_gp > 0 ? 10 : 0);
    const oos_gp = items.reduce((s, i) => s + Math.max(i.oos_pnl, 0), 0);
    const oos_gl = items.reduce((s, i) => s + Math.min(i.oos_pnl, 0), 0);
    const oos_pf = oos_gl !== 0 ? Math.min(oos_gp / Math.abs(oos_gl), 10) : (oos_gp > 0 ? 10 : 0);

    // Unique batch count
    const batchCount = new Set(items.map(i => i.batch_id)).size;

    return { label, is_wr, is_pf, is_pnl, is_trades, oos_wr, oos_pf, oos_pnl, oos_trades, count: items.length, batchCount };
  }).sort((a, b) => b.oos_pnl - a.oos_pnl);
}

export default function WFOAssetBreakdown({ breakdown, title = "Asset Breakdown" }: Props) {
  const [groupBy, setGroupBy] = useState<GroupBy>("group");
  const hasOOS = breakdown.some(b => b.oos_trades > 0);

  const rows = useMemo(() => aggregateRows(breakdown, groupBy), [breakdown, groupBy]);

  // Totals
  const totals = useMemo(() => {
    const is_pnl = breakdown.reduce((s, i) => s + i.is_pnl, 0);
    const oos_pnl = breakdown.reduce((s, i) => s + i.oos_pnl, 0);
    const is_trades = breakdown.reduce((s, i) => s + i.is_trades, 0);
    const oos_trades = breakdown.reduce((s, i) => s + i.oos_trades, 0);
    const is_wr = is_trades > 0
      ? breakdown.reduce((s, i) => s + i.is_wr * i.is_trades, 0) / is_trades : 0;
    const oos_wr = oos_trades > 0
      ? breakdown.reduce((s, i) => s + i.oos_wr * i.oos_trades, 0) / oos_trades : 0;
    return { is_pnl, oos_pnl, is_trades, oos_trades, is_wr, oos_wr };
  }, [breakdown]);

  if (!breakdown.length) return null;

  const GROUP_TABS: { id: GroupBy; label: string; tip: string }[] = [
    { id: "group", label: "By Group", tip: "Aggregate by batch group (A, B, etc). Shows how many unique batches from each group were selected, and their combined IS/OOS performance." },
    { id: "timeframe", label: "By TF", tip: "Aggregate by timeframe (M15, M30). Shows which timeframes contributed trades and how they performed in IS vs OOS." },
    { id: "direction", label: "By Side", tip: "Aggregate by trade direction (Long vs Short). Shows if one side performs better OOS." },
    { id: "asset", label: "Per Asset", tip: "Individual asset-side performance across all windows. Each row = one symbol+side+batch combo. Sorted by OOS PnL." },
  ];

  return (
    <div className="bg-ag-dark/50 border border-ag-border/50 rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs font-medium text-ag-text">{title}</h4>
        <div className="flex items-center gap-1">
          {GROUP_TABS.map((t) => (
            <button key={t.id} onClick={() => setGroupBy(t.id)}
              title={t.tip}
              className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                groupBy === t.id
                  ? "bg-ag-accent/15 text-ag-accent font-medium"
                  : "text-ag-text-muted hover:text-ag-text"
              }`}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr className="text-ag-text-muted border-b border-ag-border/50">
              <th className="text-left py-1 pr-2 font-medium">
                {groupBy === "asset" ? "Asset" : groupBy === "group" ? "Group" : groupBy === "timeframe" ? "TF" : "Side"}
              </th>
              {groupBy !== "asset" && (
                <th className="text-right py-1 px-1 font-medium" title={groupBy === "group" ? "Unique batches selected across all windows" : "Count"}>
                  {groupBy === "group" ? "Batches" : "#"}
                </th>
              )}
              <th className="text-right py-1 px-1 font-medium" title="In-Sample win rate (weighted by trade count)">IS WR</th>
              <th className="text-right py-1 px-1 font-medium" title="In-Sample profit factor (gross profit / gross loss)">IS PF</th>
              <th className="text-right py-1 px-1 font-medium" title="In-Sample net profit (training period)">IS PnL</th>
              <th className="text-right py-1 px-1 font-medium" title="Total In-Sample trades">IS Trd</th>
              {hasOOS && (
                <>
                  <th className="text-right py-1 px-1 font-medium border-l border-ag-border/30" title="Out-of-Sample win rate — the real test (unseen data)">OOS WR</th>
                  <th className="text-right py-1 px-1 font-medium" title="Out-of-Sample profit factor — >1.0 = profitable on unseen data">OOS PF</th>
                  <th className="text-right py-1 px-1 font-medium" title="Out-of-Sample net profit — actual $ made on unseen data">OOS PnL</th>
                  <th className="text-right py-1 px-1 font-medium" title="Total Out-of-Sample trades">OOS Trd</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-b border-ag-border/10 hover:bg-ag-dark/30">
                <td className="py-1 pr-2 font-medium text-ag-text">{r.label}</td>
                {groupBy !== "asset" && (
                  <td className="py-1 px-1 text-right text-ag-text-muted" title={groupBy === "group" ? `${r.batchCount} unique batches, ${r.count} asset-side combos` : undefined}>
                    {groupBy === "group" ? r.batchCount : r.count}
                  </td>
                )}
                <td className={`py-1 px-1 text-right font-mono ${wrColor(r.is_wr)}`}>{fmtPct(r.is_wr)}</td>
                <td className={`py-1 px-1 text-right font-mono ${pfColor(r.is_pf)}`}>{fmtPF(r.is_pf)}</td>
                <td className={`py-1 px-1 text-right font-mono ${numColor(r.is_pnl)}`}>{fmt$(r.is_pnl)}</td>
                <td className="py-1 px-1 text-right font-mono text-ag-text-muted">{r.is_trades}</td>
                {hasOOS && (
                  <>
                    <td className={`py-1 px-1 text-right font-mono border-l border-ag-border/30 ${wrColor(r.oos_wr)}`}>{fmtPct(r.oos_wr)}</td>
                    <td className={`py-1 px-1 text-right font-mono ${pfColor(r.oos_pf)}`}>{fmtPF(r.oos_pf)}</td>
                    <td className={`py-1 px-1 text-right font-mono ${numColor(r.oos_pnl)}`}>{fmt$(r.oos_pnl)}</td>
                    <td className="py-1 px-1 text-right font-mono text-ag-text-muted">{r.oos_trades}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-ag-border font-medium">
              <td className="py-1 pr-2 text-ag-text-muted">Total</td>
              {groupBy !== "asset" && (
                <td className="py-1 px-1 text-right text-ag-text-muted">
                  {groupBy === "group"
                    ? new Set(breakdown.map(b => b.batch_id)).size
                    : breakdown.length}
                </td>
              )}
              <td className={`py-1 px-1 text-right font-mono ${wrColor(totals.is_wr)}`}>{fmtPct(totals.is_wr)}</td>
              <td className="py-1 px-1 text-right font-mono">—</td>
              <td className={`py-1 px-1 text-right font-mono ${numColor(totals.is_pnl)}`}>{fmt$(totals.is_pnl)}</td>
              <td className="py-1 px-1 text-right font-mono text-ag-text-muted">{totals.is_trades}</td>
              {hasOOS && (
                <>
                  <td className={`py-1 px-1 text-right font-mono border-l border-ag-border/30 ${wrColor(totals.oos_wr)}`}>{fmtPct(totals.oos_wr)}</td>
                  <td className="py-1 px-1 text-right font-mono">—</td>
                  <td className={`py-1 px-1 text-right font-mono ${numColor(totals.oos_pnl)}`}>{fmt$(totals.oos_pnl)}</td>
                  <td className="py-1 px-1 text-right font-mono text-ag-text-muted">{totals.oos_trades}</td>
                </>
              )}
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}
