"use client";

import { useState, useEffect, useMemo } from "react";
import { api } from "@/lib/api";
import type { DashboardAnalysisRequest, AssetBreakdownItem } from "@/lib/api";

interface Props {
  req: DashboardAnalysisRequest;
}

const BIN_EDGES = [0, 0.5, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, Infinity];
const BIN_LABELS = ["<0.5", "0.5-1", "1-1.25", "1.25-1.5", "1.5-2", "2-2.5", "2.5-3", "3-4", "4-5", "5+"];

function binColor(idx: number): string {
  if (idx < 2) return "#ef4444"; // red — losing/breakeven
  if (idx < 4) return "#f59e0b"; // yellow — marginal
  return "#22c55e"; // green — profitable
}

export default function PFHistogram({ req }: Props) {
  const [assets, setAssets] = useState<AssetBreakdownItem[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!req.batch_id && !req.strategy_filename && !req.portfolio_id) return;
    setLoading(true);
    api.dashboardAssetBreakdown(req)
      .then((r) => setAssets(r.assets))
      .catch(() => setAssets([]))
      .finally(() => setLoading(false));
  }, [req.batch_id, req.portfolio_id, req.strategy_filename, req.side_filter, req.date_to, req.min_trades]);

  const { bins, stats, infCount } = useMemo(() => {
    const counts = new Array(BIN_LABELS.length).fill(0);
    let inf = 0;
    const finitePFs: number[] = [];

    for (const a of assets) {
      const pf = a.profit_factor;
      if (pf === null || pf === undefined) {
        inf++;
        continue;
      }
      if (!isFinite(pf)) {
        inf++;
        continue;
      }
      finitePFs.push(pf);
      for (let i = 0; i < BIN_EDGES.length - 1; i++) {
        if (pf >= BIN_EDGES[i] && pf < BIN_EDGES[i + 1]) {
          counts[i]++;
          break;
        }
      }
    }

    const sorted = [...finitePFs].sort((a, b) => a - b);
    const median = sorted.length > 0 ? sorted[Math.floor(sorted.length / 2)] : 0;
    const mean = sorted.length > 0 ? sorted.reduce((s, v) => s + v, 0) / sorted.length : 0;
    const above1 = finitePFs.filter((v) => v > 1).length;
    const above15 = finitePFs.filter((v) => v > 1.5).length;
    const above2 = finitePFs.filter((v) => v > 2).length;

    return {
      bins: counts,
      stats: {
        total: assets.length,
        median: median,
        mean: mean,
        above1,
        above15,
        above2,
        above1Pct: assets.length > 0 ? (above1 / assets.length * 100) : 0,
        above15Pct: assets.length > 0 ? (above15 / assets.length * 100) : 0,
        above2Pct: assets.length > 0 ? (above2 / assets.length * 100) : 0,
      },
      infCount: inf,
    };
  }, [assets]);

  if (loading) {
    return <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted">Loading PF distribution...</div>;
  }
  if (!assets.length) return null;

  const maxCount = Math.max(...bins, 1);
  const barW = 100 / bins.length;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <h3 className="text-sm font-semibold text-ag-text mb-3">Profit Factor Distribution</h3>

      {/* Stats summary */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mb-4 text-xs">
        <Stat label="Total Assets" value={String(stats.total)} />
        <Stat label="No Losses" value={String(infCount)} sub="PF = Inf" />
        <Stat label="Median PF" value={stats.median.toFixed(2)} color={stats.median >= 1 ? "text-ag-green" : "text-ag-red"} />
        <Stat label="PF > 1.0" value={`${stats.above1} (${stats.above1Pct.toFixed(0)}%)`} />
        <Stat label="PF > 1.5" value={`${stats.above15} (${stats.above15Pct.toFixed(0)}%)`} />
        <Stat label="PF > 2.0" value={`${stats.above2} (${stats.above2Pct.toFixed(0)}%)`} />
      </div>

      {/* SVG Histogram */}
      <svg viewBox="0 0 400 160" className="w-full" preserveAspectRatio="xMidYMid meet">
        {bins.map((count, i) => {
          const barHeight = (count / maxCount) * 120;
          const x = 40 + i * ((400 - 50) / bins.length);
          const w = (400 - 50) / bins.length - 2;
          return (
            <g key={i}>
              <rect
                x={x} y={130 - barHeight} width={w} height={barHeight}
                fill={binColor(i)} opacity={0.85} rx={1}
              />
              {count > 0 && (
                <text x={x + w / 2} y={125 - barHeight} textAnchor="middle"
                  fill="currentColor" fontSize="8" className="text-ag-text">
                  {count}
                </text>
              )}
              <text x={x + w / 2} y={145} textAnchor="middle"
                fill="currentColor" fontSize="7" className="text-ag-text-muted">
                {BIN_LABELS[i]}
              </text>
            </g>
          );
        })}
        {/* Y axis */}
        <line x1="38" y1="10" x2="38" y2="130" stroke="currentColor" strokeWidth="0.5" className="text-ag-border" />
        <text x="35" y="15" textAnchor="end" fill="currentColor" fontSize="7" className="text-ag-text-muted">{maxCount}</text>
        <text x="35" y="130" textAnchor="end" fill="currentColor" fontSize="7" className="text-ag-text-muted">0</text>
        {/* X axis label */}
        <text x="220" y="158" textAnchor="middle" fill="currentColor" fontSize="8" className="text-ag-text-muted">
          Profit Factor
        </text>
      </svg>
    </div>
  );
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div>
      <p className="text-ag-text-muted text-[10px] uppercase tracking-wider">{label}</p>
      <p className={`text-sm font-semibold ${color ?? "text-ag-text"}`}>{value}</p>
      {sub && <p className="text-[9px] text-ag-text-muted">{sub}</p>}
    </div>
  );
}
