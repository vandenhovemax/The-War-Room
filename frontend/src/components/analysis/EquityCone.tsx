"use client";

interface EquityConeProps {
  cone: Record<string, number[]>;
  originalEquity: number[];
}

const BAND_COLORS = [
  { fill: "rgba(59,130,246,0.10)", stroke: "rgba(59,130,246,0.3)" },  // 5-95 outer
  { fill: "rgba(59,130,246,0.18)", stroke: "rgba(59,130,246,0.4)" },  // 25-75 inner
];

const WIDTH = 700;
const HEIGHT = 300;
const PAD = { top: 20, right: 20, bottom: 30, left: 60 };
const PW = WIDTH - PAD.left - PAD.right;
const PH = HEIGHT - PAD.top - PAD.bottom;

export default function EquityCone({ cone, originalEquity }: EquityConeProps) {
  const p5 = cone["5"] ?? [];
  const p25 = cone["25"] ?? [];
  const p50 = cone["50"] ?? [];
  const p75 = cone["75"] ?? [];
  const p95 = cone["95"] ?? [];

  if (p50.length === 0) return <p className="text-xs text-ag-text-muted">No data</p>;

  const allVals = [...p5, ...p95, ...originalEquity];
  const yMin = Math.min(...allVals);
  const yMax = Math.max(...allVals);
  const yRange = yMax - yMin || 1;
  const n = p50.length;

  const xScale = (i: number) => PAD.left + (i / (n - 1)) * PW;
  const yScale = (v: number) => PAD.top + PH - ((v - yMin) / yRange) * PH;

  const makePath = (arr: number[]) =>
    arr.map((v, i) => `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},${yScale(v).toFixed(1)}`).join(" ");

  // Band = upper line forward + lower line reversed
  const makeBand = (upper: number[], lower: number[]) => {
    const fwd = upper.map((v, i) => `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},${yScale(v).toFixed(1)}`).join(" ");
    const rev = [...lower].reverse().map((v, i) => {
      const idx = n - 1 - i;
      return `L${xScale(idx).toFixed(1)},${yScale(v).toFixed(1)}`;
    }).join(" ");
    return `${fwd} ${rev} Z`;
  };

  // Y-axis ticks
  const yTicks = 5;
  const yTickVals = Array.from({ length: yTicks + 1 }, (_, i) => yMin + (yRange / yTicks) * i);

  // X-axis ticks
  const xTicks = Math.min(10, n - 1);
  const xTickIdxs = Array.from({ length: xTicks + 1 }, (_, i) => Math.round((i / xTicks) * (n - 1)));

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" preserveAspectRatio="xMidYMid meet">
      {/* Grid lines */}
      {yTickVals.map((v) => (
        <line
          key={v}
          x1={PAD.left} y1={yScale(v)} x2={PAD.left + PW} y2={yScale(v)}
          stroke="var(--color-ag-border)" strokeWidth={1}
        />
      ))}

      {/* Bands: 5-95 and 25-75 */}
      <path d={makeBand(p95, p5)} fill={BAND_COLORS[0].fill} />
      <path d={makeBand(p75, p25)} fill={BAND_COLORS[1].fill} />

      {/* Percentile lines */}
      <path d={makePath(p5)} fill="none" stroke={BAND_COLORS[0].stroke} strokeWidth={1} />
      <path d={makePath(p95)} fill="none" stroke={BAND_COLORS[0].stroke} strokeWidth={1} />
      <path d={makePath(p25)} fill="none" stroke={BAND_COLORS[1].stroke} strokeWidth={1} />
      <path d={makePath(p75)} fill="none" stroke={BAND_COLORS[1].stroke} strokeWidth={1} />

      {/* Median line */}
      <path d={makePath(p50)} fill="none" stroke="rgba(59,130,246,0.8)" strokeWidth={2} />

      {/* Original equity line */}
      <path d={makePath(originalEquity)} fill="none" stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 2" />

      {/* Y-axis labels */}
      {yTickVals.map((v) => (
        <text key={v} x={PAD.left - 5} y={yScale(v) + 3} textAnchor="end" fontSize={9} fill="var(--color-ag-text-muted)">
          ${v.toFixed(0)}
        </text>
      ))}

      {/* X-axis labels */}
      {xTickIdxs.map((idx) => (
        <text key={idx} x={xScale(idx)} y={HEIGHT - 8} textAnchor="middle" fontSize={9} fill="var(--color-ag-text-muted)">
          {idx}
        </text>
      ))}

      {/* X-axis label */}
      <text x={PAD.left + PW / 2} y={HEIGHT - 0} textAnchor="middle" fontSize={10} fill="var(--color-ag-text-muted)">
        Trade #
      </text>

      {/* Legend */}
      <line x1={PAD.left + 10} y1={PAD.top + 5} x2={PAD.left + 30} y2={PAD.top + 5} stroke="rgba(59,130,246,0.8)" strokeWidth={2} />
      <text x={PAD.left + 33} y={PAD.top + 8} fontSize={9} fill="var(--color-ag-text-muted)">Median</text>
      <line x1={PAD.left + 80} y1={PAD.top + 5} x2={PAD.left + 100} y2={PAD.top + 5} stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 2" />
      <text x={PAD.left + 103} y={PAD.top + 8} fontSize={9} fill="var(--color-ag-text-muted)">Original</text>
    </svg>
  );
}
