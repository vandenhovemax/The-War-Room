"use client";
import type { PoolStats } from "@/lib/api";

interface Props {
  stats: PoolStats;
}

const BINS = [
  { label: "<1.0", min: 0, max: 1.0, color: "bg-ag-red/60" },
  { label: "1.0-1.5", min: 1.0, max: 1.5, color: "bg-ag-yellow/60" },
  { label: "1.5-2.0", min: 1.5, max: 2.0, color: "bg-ag-green/40" },
  { label: "2.0-3.0", min: 2.0, max: 3.0, color: "bg-ag-green/60" },
  { label: "3.0+", min: 3.0, max: Infinity, color: "bg-ag-accent/60" },
];

export default function PoolStatsPanel({ stats }: Props) {
  // Compute histogram bins
  const counts = BINS.map((bin) =>
    stats.pf_values.filter((v) => v >= bin.min && v < bin.max).length,
  );
  const maxCount = Math.max(...counts, 1);

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <h4 className="text-sm font-medium mb-3">Optimizer Pool Quality</h4>

      {/* Summary stats */}
      <div className="grid grid-cols-5 gap-3 mb-4 text-xs">
        <div>
          <span className="text-ag-text-muted">Total Assets</span>
          <p className="text-lg font-semibold">{stats.total}</p>
        </div>
        <div>
          <span className="text-ag-text-muted">Median PF</span>
          <p className="text-lg font-semibold">{stats.median_pf.toFixed(2)}</p>
        </div>
        <div>
          <span className="text-ag-text-muted">Mean PF</span>
          <p className="text-lg font-semibold">{stats.mean_pf.toFixed(2)}</p>
        </div>
        <div>
          <span className="text-ag-text-muted">PF &ge; 1.5</span>
          <p className="text-lg font-semibold text-ag-green">{stats.pf_above_1_5}</p>
        </div>
        <div>
          <span className="text-ag-text-muted">PF &ge; 2.0</span>
          <p className="text-lg font-semibold text-ag-accent">{stats.pf_above_2_0}</p>
        </div>
      </div>

      {/* Histogram */}
      <div className="space-y-1.5">
        <span className="text-xs text-ag-text-muted">PF Distribution</span>
        {BINS.map((bin, i) => (
          <div key={bin.label} className="flex items-center gap-2 text-xs">
            <span className="w-14 text-ag-text-muted text-right">{bin.label}</span>
            <div className="flex-1 h-5 bg-ag-dark rounded overflow-hidden">
              <div className={`h-full ${bin.color} rounded transition-all`}
                style={{ width: `${(counts[i] / maxCount) * 100}%` }} />
            </div>
            <span className="w-6 text-right text-ag-text-muted">{counts[i]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
