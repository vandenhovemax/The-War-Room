"use client";
import { useState } from "react";
import type { CategoryComparison, CategoryCmpStats, CompareStrategy } from "@/lib/api";

type MetricKey = "net_profit" | "profit_factor" | "win_rate" | "trades" | "assets";

const METRIC_OPTIONS: { key: MetricKey; label: string; lowerBetter: boolean; fmt: (v: number | null) => string }[] = [
  { key: "net_profit", label: "Net Profit", lowerBetter: false, fmt: (v) => v == null ? "—" : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` },
  { key: "profit_factor", label: "Profit Factor", lowerBetter: false, fmt: (v) => v == null ? "—" : v.toFixed(2) },
  { key: "win_rate", label: "Win Rate", lowerBetter: false, fmt: (v) => v == null ? "—" : `${v.toFixed(1)}%` },
  { key: "trades", label: "Positions", lowerBetter: false, fmt: (v) => v == null ? "—" : String(v) },
  { key: "assets", label: "Assets", lowerBetter: false, fmt: (v) => v == null ? "—" : String(v) },
];

interface Props {
  data: CategoryComparison;
  strategies: CompareStrategy[];
}

export default function CategoryComparisonTable({ data, strategies }: Props) {
  const [metric, setMetric] = useState<MetricKey>("net_profit");
  const opt = METRIC_OPTIONS.find((o) => o.key === metric)!;

  if (data.all_categories.length === 0) {
    return (
      <p className="text-xs text-ag-text-muted py-2">
        No category data available. Assign categories to symbols first.
      </p>
    );
  }

  const batchLabels = data.batches.map((b) => {
    const s = strategies.find((st) => st.batch_id === b.batch_id);
    return s ? `${s.strategy_name} #${s.batch_id}` : `Batch #${b.batch_id}`;
  });

  const getValue = (catName: string, batchIdx: number): number | null => {
    const batchCats = data.batches[batchIdx]?.categories ?? {};
    const cs: CategoryCmpStats | undefined = batchCats[catName];
    if (!cs) return null;
    return cs[metric];
  };

  const getBestIdx = (catName: string): number => {
    let bestIdx = -1;
    let bestVal: number | null = null;
    data.batches.forEach((_, i) => {
      const v = getValue(catName, i);
      if (v === null) return;
      if (bestVal === null || (opt.lowerBetter ? v < bestVal : v > bestVal)) {
        bestVal = v;
        bestIdx = i;
      }
    });
    return bestIdx;
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold">Category Comparison</h4>
        <select
          value={metric}
          onChange={(e) => setMetric(e.target.value as MetricKey)}
          className="bg-ag-card border border-ag-border rounded-lg px-3 py-1.5 text-xs text-ag-text"
        >
          {METRIC_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>{o.label}</option>
          ))}
        </select>
      </div>

      <div className="overflow-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-ag-border">
              <th className="text-left p-2 text-ag-text-muted font-normal">Category</th>
              {batchLabels.map((label, i) => (
                <th key={i} className="text-right p-2 text-ag-text-muted font-normal truncate max-w-[180px]" title={label}>
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.all_categories.map((cat) => {
              const bestIdx = getBestIdx(cat);
              return (
                <tr key={cat} className="border-b border-ag-border/50">
                  <td className="p-2 text-ag-text text-xs font-medium">{cat}</td>
                  {data.batches.map((_, i) => {
                    const v = getValue(cat, i);
                    const isBest = i === bestIdx && v !== null;
                    const isProfit = metric === "net_profit" && v !== null;
                    let color = isBest ? "text-ag-green font-bold" : "";
                    if (isProfit && !isBest && v !== null) {
                      color = v > 0 ? "text-ag-green/70" : v < 0 ? "text-ag-red" : "";
                    }
                    return (
                      <td key={i} className={`p-2 text-right font-mono text-xs ${color}`}>
                        {opt.fmt(v)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
