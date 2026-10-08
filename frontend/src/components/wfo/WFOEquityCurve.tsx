"use client";

import { useMemo } from "react";

interface EquityPoint {
  date: string;
  equity: number;
  drawdown_pct: number;
}

interface Props {
  data: EquityPoint[];
  title?: string;
}

function fmt$(v: number): string {
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

export default function WFOEquityCurve({ data, title = "Equity Curve" }: Props) {
  const W = 700;
  const H = 250;
  const PAD = { top: 15, right: 50, bottom: 25, left: 55 };

  const computed = useMemo(() => {
    if (!data.length)
      return { eqPath: "", ddPath: "", yMin: 0, yMax: 0, ddMin: 0, xLabels: [] as { x: number; label: string }[] };

    const equities = data.map((d) => d.equity);
    const ddPcts = data.map((d) => d.drawdown_pct ?? 0);
    const eqMin = Math.min(0, ...equities);
    const eqMax = Math.max(...equities);
    const ddMinVal = Math.min(0, ...ddPcts);

    const chartW = W - PAD.left - PAD.right;
    const chartH = H - PAD.top - PAD.bottom;

    const xScale = (i: number) => PAD.left + (i / Math.max(data.length - 1, 1)) * chartW;
    const yScale = (v: number) =>
      PAD.top + chartH - ((v - eqMin) / Math.max(eqMax - eqMin, 1)) * chartH;
    const ddScale = (v: number) =>
      PAD.top + chartH - ((v - ddMinVal) / Math.max(-ddMinVal, 0.01)) * chartH;

    const eqPts = data.map((_, i) => `${xScale(i)},${yScale(equities[i])}`);
    const ddPts = data.map((_, i) => `${xScale(i)},${ddScale(ddPcts[i])}`);

    const step = Math.max(1, Math.floor(data.length / 5));
    const labels = data
      .filter((_, i) => i % step === 0)
      .map((d, idx) => ({ x: xScale(idx * step), label: d.date.slice(0, 7) }));

    return {
      eqPath: `M${eqPts.join("L")}`,
      ddPath: `M${ddPts.join("L")}`,
      yMin: eqMin, yMax: eqMax, ddMin: ddMinVal,
      xLabels: labels,
    };
  }, [data]);

  if (!data.length) return null;

  const { eqPath, ddPath, yMin, yMax, ddMin, xLabels } = computed;
  const chartH = H - PAD.top - PAD.bottom;
  const finalEquity = data[data.length - 1].equity;
  const maxDDPct = Math.min(...data.map((d) => d.drawdown_pct));
  // Also compute dollar-based max DD (more reliable for aggregate curves)
  const equities = data.map((d) => d.equity);
  let peakEq = 0;
  let maxDDDollar = 0;
  for (const eq of equities) {
    if (eq > peakEq) peakEq = eq;
    const dd = eq - peakEq;
    if (dd < maxDDDollar) maxDDDollar = dd;
  }

  return (
    <div className="bg-ag-dark/50 border border-ag-border/50 rounded-lg p-3">
      <div className="flex items-center justify-between mb-1">
        <h4 className="text-xs font-medium text-ag-text">{title}</h4>
        <div className="flex items-center gap-3 text-[10px] text-ag-text-muted">
          <span>Final: <span className={finalEquity > 0 ? "text-ag-green" : "text-ag-red"}>{fmt$(finalEquity)}</span></span>
          <span>Max DD: <span className="text-ag-red">{fmt$(maxDDDollar)}</span></span>
          {maxDDPct > -100 && <span>(<span className="text-ag-red">{maxDDPct.toFixed(1)}%</span>)</span>}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="xMidYMid meet">
        {/* Y-axis labels */}
        {[0, 0.25, 0.5, 0.75, 1].map((pct) => {
          const v = yMin + (yMax - yMin) * (1 - pct);
          const y = PAD.top + chartH * pct;
          return (
            <g key={pct}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} stroke="#333" strokeWidth={0.5} />
              <text x={PAD.left - 4} y={y + 3} textAnchor="end" fill="#888" fontSize={9}>{fmt$(v)}</text>
            </g>
          );
        })}

        {/* Equity line */}
        <path d={eqPath} fill="none" stroke="#22c55e" strokeWidth={1.5} />

        {/* Drawdown fill */}
        {ddMin < -1 && (
          <path
            d={ddPath}
            fill="none"
            stroke="rgba(239,68,68,0.4)"
            strokeWidth={1}
          />
        )}

        {/* X labels */}
        {xLabels.map((l, i) => (
          <text key={i} x={l.x} y={H - 5} textAnchor="middle" fill="#888" fontSize={8}>{l.label}</text>
        ))}
      </svg>
    </div>
  );
}
