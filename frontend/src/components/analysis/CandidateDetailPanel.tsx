"use client";
import type { CandidateMetrics } from "@/lib/api";

interface Props {
  metrics: CandidateMetrics;
  equityCurve?: number[];
  longs?: string[];
  shorts?: string[];
}

function fmt(v: number | undefined, dec = 2): string {
  if (v === undefined || v === null) return "\u2014";
  return v.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function MiniEquity({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const W = 400, H = 80, PAD = 4;
  const min = Math.min(0, ...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const xStep = (W - PAD * 2) / (data.length - 1);
  const y = (v: number) => PAD + (H - PAD * 2) - ((v - min) / range) * (H - PAD * 2);
  const pts = data.map((v, i) => `${PAD + i * xStep},${y(v)}`).join("L");
  const zeroY = y(0);
  // Fill area under curve
  const fill = `M${PAD},${zeroY}L${pts}L${PAD + (data.length - 1) * xStep},${zeroY}Z`;
  const lastVal = data[data.length - 1];
  const color = lastVal >= 0 ? "var(--color-ag-green)" : "var(--color-ag-red)";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-20" preserveAspectRatio="xMidYMid meet">
      <path d={fill} fill={color} opacity="0.1" />
      <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY}
        stroke="var(--color-ag-text-muted)" strokeWidth="0.5" strokeDasharray="3,3" />
      <path d={`M${pts}`} fill="none" stroke={color} strokeWidth="1.5" />
      <text x={W - PAD} y={PAD + 8} textAnchor="end" fill="var(--color-ag-text-muted)" fontSize="9">
        ${fmt(lastVal, 0)}
      </text>
    </svg>
  );
}

function KPICell({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="px-2 py-1.5 bg-ag-dark/50 rounded">
      <p className="text-[10px] text-ag-text-muted truncate">{label}</p>
      <p className={`text-xs font-medium ${color ?? "text-ag-text"}`}>{value}</p>
    </div>
  );
}

export default function CandidateDetailPanel({ metrics: m, equityCurve, longs, shorts }: Props) {
  const pnlColor = m.net_profit >= 0 ? "text-ag-green" : "text-ag-red";

  return (
    <div className="mt-3 pt-3 border-t border-ag-border space-y-3">
      {/* Equity Curve */}
      {equityCurve && equityCurve.length > 1 && (
        <div>
          <p className="text-[10px] text-ag-text-muted mb-1">Cumulative Equity</p>
          <MiniEquity data={equityCurve} />
        </div>
      )}

      {/* Full KPI Grid — Row 1: Core Performance */}
      <div className="grid grid-cols-4 gap-1.5">
        <KPICell label="Net Profit" value={`$${fmt(m.net_profit)}`} color={pnlColor} />
        <KPICell label="Profit Factor" value={fmt(m.profit_factor)} />
        <KPICell label="Win Rate" value={`${fmt(m.win_rate, 1)}%`} />
        <KPICell label="Total Positions" value={`${m.total_trades ?? 0}`} />
      </div>

      {/* Row 2: Drawdown & Risk */}
      <div className="grid grid-cols-4 gap-1.5">
        <KPICell label="Max Drawdown" value={`$${fmt(m.max_drawdown)}`} color="text-ag-red" />
        <KPICell label="Stability (R\u00b2)" value={`${fmt((m.stability ?? 0) * 100, 1)}%`} />
        <KPICell label="Recovery Factor" value={fmt(m.recovery_factor)} />
        <KPICell label="Avg Corr" value={fmt(m.avg_corr, 3)} />
      </div>

      {/* Row 3: Risk-Adjusted */}
      <div className="grid grid-cols-4 gap-1.5">
        <KPICell label="Sortino" value={fmt(m.sortino)} />
        <KPICell label="Calmar" value={fmt(m.calmar)} />
        <KPICell label="Trades/Month" value={fmt(m.avg_trades_per_month, 1)} />
        <KPICell label="Assets" value={`${m.n_assets}`} />
      </div>

      {/* L/S Breakdown counts */}
      {longs && shorts && (longs.length > 0 || shorts.length > 0) && (
        <div className="flex gap-4 text-xs">
          <span className="text-ag-green">{longs.length} Longs</span>
          <span className="text-ag-red">{shorts.length} Shorts</span>
        </div>
      )}
    </div>
  );
}
