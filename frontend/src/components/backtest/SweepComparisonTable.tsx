"use client";
import { useState } from "react";
import { ChevronDown, ChevronUp, Trophy } from "lucide-react";
import type { SweepComboResult, SweepParam, BatchResultSummary } from "@/lib/api";
import { api } from "@/lib/api";
import BatchResultsSummary from "./BatchResultsSummary";

interface Props {
  combos: SweepComboResult[];
  sweptParams: SweepParam[];
  isLongShort: boolean;
}

type SortKey = "combo_index" | "grade_score" | "pf" | "win_rate" | "trades" | "pnl";

export default function SweepComparisonTable({ combos, sweptParams, isLongShort }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("grade_score");
  const [sortAsc, setSortAsc] = useState(false);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [expandedResult, setExpandedResult] = useState<BatchResultSummary | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [showCompletedOnly, setShowCompletedOnly] = useState(true);

  // Count completed vs total combos
  const completedCombos = combos.filter((c) => c.batches.some((b) => b.status === "completed"));
  const visibleCombos = showCompletedOnly ? completedCombos : combos;

  // Find best combo by grade score (among visible combos only)
  const bestIdx = visibleCombos.reduce((best, c) => {
    const score = Math.max(...c.batches.map((b) => b.batch_grade_score ?? 0));
    const bestScore = Math.max(...(visibleCombos.find((v) => v.combo_index === best)?.batches ?? []).map((b) => b.batch_grade_score ?? 0));
    return score > bestScore ? c.combo_index : best;
  }, visibleCombos[0]?.combo_index ?? 0);

  const getMetric = (c: SweepComboResult, key: string): number => {
    // Average across completed batches only
    const done = c.batches.filter((b) => b.status === "completed");
    const vals = done.map((b) => {
      const v = b.aggregate_metrics?.[key];
      return typeof v === "number" ? v : 0;
    });
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
  };

  const sorted = [...visibleCombos].sort((a, b) => {
    let va = 0, vb = 0;
    if (sortKey === "combo_index") { va = a.combo_index; vb = b.combo_index; }
    else if (sortKey === "grade_score") { va = getMetric(a, "batch_grade_score"); vb = getMetric(b, "batch_grade_score"); }
    else if (sortKey === "pf") { va = getMetric(a, "avg_profit_factor"); vb = getMetric(b, "avg_profit_factor"); }
    else if (sortKey === "win_rate") { va = getMetric(a, "avg_win_rate"); vb = getMetric(b, "avg_win_rate"); }
    else if (sortKey === "trades") { va = getMetric(a, "total_trades_all"); vb = getMetric(b, "total_trades_all"); }
    else if (sortKey === "pnl") { va = getMetric(a, "total_net_profit"); vb = getMetric(b, "total_net_profit"); }
    return sortAsc ? va - vb : vb - va;
  });

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  const handleExpand = async (comboIdx: number, batchId: number) => {
    if (expandedIdx === comboIdx) { setExpandedIdx(null); setExpandedResult(null); return; }
    setExpandedIdx(comboIdx);
    setLoadingDetail(true);
    try {
      const detail = await api.batchRunDetail(batchId);
      setExpandedResult(detail);
    } catch { setExpandedResult(null); }
    setLoadingDetail(false);
  };

  const SortIcon = ({ k }: { k: SortKey }) => {
    if (sortKey !== k) return null;
    return sortAsc ? <ChevronUp size={12} /> : <ChevronDown size={12} />;
  };

  const fmtGrade = (grade: string | null, score: number | null) => {
    if (!grade) return "—";
    const color = (score ?? 0) >= 0.7 ? "text-green-400" : (score ?? 0) >= 0.4 ? "text-yellow-400" : "text-red-400";
    return <span className={color}>{grade} ({((score ?? 0) * 100).toFixed(0)})</span>;
  };

  return (
    <div className="overflow-x-auto">
      {/* Summary + filter */}
      <div className="flex items-center justify-between mb-2 text-xs">
        <span className="text-ag-text-muted">
          {completedCombos.length} of {combos.length} combos completed
          {showCompletedOnly && completedCombos.length < combos.length && ` (${combos.length - completedCombos.length} hidden)`}
        </span>
        {completedCombos.length < combos.length && (
          <button onClick={() => setShowCompletedOnly(!showCompletedOnly)}
            className="text-ag-accent hover:underline">
            {showCompletedOnly ? "Show all" : "Show completed only"}
          </button>
        )}
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-ag-text-muted border-b border-ag-border">
            <th className="text-left py-2 px-2 cursor-pointer" onClick={() => toggleSort("combo_index")}># <SortIcon k="combo_index" /></th>
            {sweptParams.map((p) => <th key={p.name} className="text-left py-2 px-2 text-ag-accent">{p.name}</th>)}
            {isLongShort && <th className="text-left py-2 px-2">Dir</th>}
            <th className="text-right py-2 px-2 cursor-pointer" onClick={() => toggleSort("grade_score")}>Grade <SortIcon k="grade_score" /></th>
            <th className="text-right py-2 px-2 cursor-pointer" onClick={() => toggleSort("pnl")}>Total PnL <SortIcon k="pnl" /></th>
            <th className="text-right py-2 px-2 cursor-pointer" onClick={() => toggleSort("pf")}>Avg PF <SortIcon k="pf" /></th>
            <th className="text-right py-2 px-2 cursor-pointer" onClick={() => toggleSort("win_rate")}>Avg WR <SortIcon k="win_rate" /></th>
            <th className="text-right py-2 px-2 cursor-pointer" onClick={() => toggleSort("trades")}>Trades <SortIcon k="trades" /></th>
            <th className="text-center py-2 px-2">Detail</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => {
            const isBest = c.combo_index === bestIdx;
            return c.batches.map((batch, bi) => (
              <tr key={`${c.combo_index}-${bi}`}
                className={`border-b border-ag-border/50 hover:bg-ag-dark/50 ${isBest ? "bg-green-500/5" : ""}`}>
                {bi === 0 && (
                  <>
                    <td className="py-2 px-2 font-medium" rowSpan={isLongShort ? c.batches.length : 1}>
                      {isBest && <Trophy size={12} className="inline text-yellow-400 mr-1" />}
                      {c.combo_index + 1}
                    </td>
                    {sweptParams.map((p) => (
                      <td key={p.name} className="py-2 px-2 font-mono text-ag-accent" rowSpan={isLongShort ? c.batches.length : 1}>
                        {c.combo_values[p.name] ?? "—"}
                      </td>
                    ))}
                  </>
                )}
                {isLongShort && <td className="py-2 px-2 capitalize">{batch.direction}</td>}
                <td className="text-right py-2 px-2">{fmtGrade(batch.batch_grade, batch.batch_grade_score)}</td>
                <td className="text-right py-2 px-2 font-mono">
                  {typeof batch.aggregate_metrics?.total_net_profit === "number"
                    ? `$${Number(batch.aggregate_metrics.total_net_profit).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                    : "—"}
                </td>
                <td className="text-right py-2 px-2">{typeof batch.aggregate_metrics?.avg_profit_factor === "number" ? Number(batch.aggregate_metrics.avg_profit_factor).toFixed(2) : "—"}</td>
                <td className="text-right py-2 px-2">{typeof batch.aggregate_metrics?.avg_win_rate === "number" ? `${Number(batch.aggregate_metrics.avg_win_rate).toFixed(1)}%` : "—"}</td>
                <td className="text-right py-2 px-2">{batch.aggregate_metrics?.total_trades_all ?? "—"}</td>
                <td className="text-center py-2 px-2">
                  <button onClick={() => handleExpand(c.combo_index * 10 + bi, batch.batch_id)}
                    className="text-ag-accent hover:underline text-[10px]">
                    {expandedIdx === c.combo_index * 10 + bi ? "Hide" : "View"}
                  </button>
                </td>
              </tr>
            ));
          })}
        </tbody>
      </table>

      {expandedIdx !== null && (
        <div className="mt-4 border border-ag-border rounded-xl p-4">
          {loadingDetail ? (
            <p className="text-sm text-ag-text-muted text-center py-4">Loading detail...</p>
          ) : expandedResult ? (
            <BatchResultsSummary result={expandedResult}
              onViewSymbol={(runId) => window.open(`/backtest?run=${runId}`, "_blank")} />
          ) : (
            <p className="text-sm text-ag-text-muted text-center py-4">Failed to load detail</p>
          )}
        </div>
      )}
    </div>
  );
}
