"use client";
import { useState, useEffect, useMemo } from "react";
import { api } from "@/lib/api";
import type { DashboardAnalysisRequest, AggregatedPnLBar } from "@/lib/api";

interface Props {
  req: DashboardAnalysisRequest;
}

const PERIODS = [
  { id: "W", label: "Weekly" },
  { id: "2W", label: "Bi-Weekly" },
  { id: "SMS", label: "Bi-Monthly" },
  { id: "ME", label: "Monthly" },
] as const;

const W = 800, H = 280;
const PAD = { top: 20, right: 20, bottom: 45, left: 65 };
const PW = W - PAD.left - PAD.right;
const PH = H - PAD.top - PAD.bottom;

function fmtDollar(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1000) return `${v < 0 ? "-" : ""}$${(abs / 1000).toFixed(0)}k`;
  return `$${v.toFixed(0)}`;
}

export default function AggregatedPnLChart({ req }: Props) {
  const [period, setPeriod] = useState("W");
  const [bars, setBars] = useState<AggregatedPnLBar[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!req.batch_id && !req.strategy_filename && !req.run_id && !req.portfolio_id) return;
    setLoading(true);
    api.dashboardAggregatedPnL(req, period)
      .then((r) => setBars(r.bars))
      .catch(() => setBars([]))
      .finally(() => setLoading(false));
  }, [req.batch_id, req.portfolio_id, req.strategy_filename, req.run_id, req.side_filter, req.date_from, req.date_to, req.min_trades, period]);

  const chart = useMemo(() => {
    if (!bars.length) return null;
    const yMax = Math.max(0, ...bars.map((b) => b.gross_profit));
    const yMin = Math.min(0, ...bars.map((b) => b.gross_loss));
    const yRange = (yMax - yMin) || 1;
    const zeroY = PAD.top + (yMax / yRange) * PH;
    const n = bars.length;
    const gap = Math.max(1, Math.min(3, Math.floor(60 / n)));
    const barW = Math.max(2, (PW / n) - gap);
    // Show every Nth label to avoid overlap
    const labelStep = Math.max(1, Math.ceil(n / 20));

    // Y-axis ticks (5 ticks)
    const yTicks: number[] = [];
    const step = yRange / 4;
    for (let i = 0; i <= 4; i++) yTicks.push(yMax - i * step);

    return { yMax, yMin, yRange, zeroY, barW, gap, labelStep, yTicks, n };
  }, [bars]);

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">Aggregated PnL Over Time</h3>
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button key={p.id} onClick={() => setPeriod(p.id)}
              className={`px-2.5 py-1 rounded text-xs transition-colors ${
                period === p.id ? "bg-ag-accent text-ag-darker font-medium" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
              }`}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="text-ag-text-muted text-xs py-8 text-center">Loading...</div>}

      {!loading && chart && (
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="xMidYMid meet">
          {/* Y-axis ticks + gridlines */}
          {chart.yTicks.map((v, i) => {
            const y = PAD.top + ((chart.yMax - v) / chart.yRange) * PH;
            return (
              <g key={i}>
                <line x1={PAD.left} y1={y} x2={W - PAD.right} y2={y}
                  stroke="currentColor" strokeWidth="0.3" className="text-ag-border" strokeDasharray={v === 0 ? "0" : "4 3"} />
                <text x={PAD.left - 6} y={y + 3} textAnchor="end" fill="currentColor"
                  fontSize="9" className="text-ag-text-muted">{fmtDollar(v)}</text>
              </g>
            );
          })}

          {/* Zero line (solid) */}
          <line x1={PAD.left} y1={chart.zeroY} x2={W - PAD.right} y2={chart.zeroY}
            stroke="currentColor" strokeWidth="0.8" className="text-ag-text-muted" />

          {/* Bars */}
          {bars.map((bar, i) => {
            const x = PAD.left + i * (chart.barW + chart.gap);
            const profitH = bar.gross_profit > 0 ? (bar.gross_profit / chart.yRange) * PH : 0;
            const lossH = bar.gross_loss < 0 ? (Math.abs(bar.gross_loss) / chart.yRange) * PH : 0;
            return (
              <g key={i}>
                {profitH > 0 && (
                  <rect x={x} y={chart.zeroY - profitH} width={chart.barW} height={profitH}
                    fill="#22c55e" opacity={0.85} rx={1}>
                    <title>{bar.period_label}: +${bar.gross_profit.toLocaleString()}</title>
                  </rect>
                )}
                {lossH > 0 && (
                  <rect x={x} y={chart.zeroY} width={chart.barW} height={lossH}
                    fill="#ef4444" opacity={0.85} rx={1}>
                    <title>{bar.period_label}: -${Math.abs(bar.gross_loss).toLocaleString()}</title>
                  </rect>
                )}
                {/* X-axis label */}
                {i % chart.labelStep === 0 && (
                  <text x={x + chart.barW / 2} y={H - PAD.bottom + 14} textAnchor="middle"
                    fill="currentColor" fontSize="8" className="text-ag-text-muted">
                    {bar.period_label}
                  </text>
                )}
              </g>
            );
          })}

          {/* Y-axis line */}
          <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={H - PAD.bottom}
            stroke="currentColor" strokeWidth="0.5" className="text-ag-border" />

          {/* Legend */}
          <rect x={W - PAD.right - 130} y={6} width={10} height={10} fill="#22c55e" opacity={0.85} rx={1} />
          <text x={W - PAD.right - 116} y={15} fill="currentColor" fontSize="9" className="text-ag-text-muted">
            Gross Profit
          </text>
          <rect x={W - PAD.right - 55} y={6} width={10} height={10} fill="#ef4444" opacity={0.85} rx={1} />
          <text x={W - PAD.right - 41} y={15} fill="currentColor" fontSize="9" className="text-ag-text-muted">
            Gross Loss
          </text>
        </svg>
      )}

      {!loading && !chart && <div className="text-ag-text-muted text-xs py-8 text-center">No data available</div>}
    </div>
  );
}
