/**
 * Multi-series equity curve overlay for comparing portfolios.
 * All curves normalized to start at $0 so you compare growth, not absolute values.
 */

const COLORS = ["#22c55e", "#3b82f6", "#f97316", "#a855f7", "#06b6d4", "#ec4899", "#eab308", "#ef4444"];

interface Series {
  name: string;
  points: { date: string; equity: number }[];
}

interface Props {
  series: Series[];
  title?: string;
}

export default function EquityCurveOverlay({ series, title = "OOS Equity Curves" }: Props) {
  // Filter to series that have at least 2 points
  const valid = series.filter((s) => s.points.length >= 2);
  if (valid.length === 0) return null;

  const h = 140;
  const w = 700;
  const pad = { top: 5, bottom: 5, left: 0, right: 0 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;

  // Find global date range
  let allDates: string[] = [];
  for (const s of valid) {
    for (const p of s.points) {
      allDates.push(p.date);
    }
  }
  allDates = [...new Set(allDates)].sort();
  if (allDates.length < 2) return null;

  const d0 = new Date(allDates[0]).getTime();
  const d1 = new Date(allDates[allDates.length - 1]).getTime();
  const dateRange = d1 - d0 || 1;

  // Find global min/max equity (all series normalized to start at 0)
  let globalMin = 0;
  let globalMax = 0;
  const normalized = valid.map((s) => {
    const offset = s.points[0].equity;
    const pts = s.points.map((p) => ({
      date: p.date,
      equity: p.equity - offset,
    }));
    for (const p of pts) {
      if (p.equity < globalMin) globalMin = p.equity;
      if (p.equity > globalMax) globalMax = p.equity;
    }
    return { name: s.name, points: pts };
  });

  const eqRange = globalMax - globalMin || 1;

  const toX = (date: string) => pad.left + ((new Date(date).getTime() - d0) / dateRange) * plotW;
  const toY = (eq: number) => pad.top + plotH - ((eq - globalMin) / eqRange) * plotH;

  // Build SVG paths
  const paths = normalized.map((s, si) => {
    const color = COLORS[si % COLORS.length];
    const d = s.points.map((p, i) => {
      const x = toX(p.date);
      const y = toY(p.equity);
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(" ");
    return { d, color, name: s.name, final: s.points[s.points.length - 1].equity };
  });

  // Zero line
  const zeroY = toY(0);

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">{title}</h4>
      <div className="bg-ag-dark/50 rounded p-2 overflow-x-auto">
        <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ maxHeight: "160px" }}>
          {/* Zero line */}
          <line x1={pad.left} y1={zeroY} x2={w - pad.right} y2={zeroY}
            stroke="#555" strokeWidth="0.5" strokeDasharray="4 2" />
          {/* Paths */}
          {paths.map((p, i) => (
            <path key={i} d={p.d} fill="none" stroke={p.color} strokeWidth="1.5" opacity="0.85" />
          ))}
        </svg>
        {/* Date range */}
        <div className="flex justify-between text-[10px] text-ag-text-muted mt-1">
          <span>{allDates[0]}</span>
          <span>{allDates[allDates.length - 1]}</span>
        </div>
        {/* Legend */}
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
          {paths.map((p, i) => (
            <div key={i} className="flex items-center gap-1.5 text-[11px]">
              <span className="w-3 h-0.5 rounded-full inline-block" style={{ backgroundColor: p.color }} />
              <span className="text-ag-text-muted truncate max-w-[150px]">{p.name}</span>
              <span className={`font-mono font-medium ${p.final >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                ${p.final.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
