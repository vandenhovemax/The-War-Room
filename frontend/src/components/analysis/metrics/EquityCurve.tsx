/**
 * SVG equity curve chart for portfolio analysis.
 * Supports optional OOS cutoff — IS portion in green, OOS in red.
 * Shows slope ($/month) for IS and OOS when cutoff is provided.
 * Highlights MDD (max drawdown) region with red shading.
 */

interface Props {
  points: { date: string; equity: number }[];
  title?: string;
  oosCutoffDate?: string; // ISO date string — everything after this is OOS (red)
  showMDD?: boolean; // highlight the max drawdown period in red
}

function slopePerMonth(pts: { date: string; equity: number }[]): number | null {
  if (pts.length < 2) return null;
  const first = pts[0];
  const last = pts[pts.length - 1];
  const d0 = new Date(first.date).getTime();
  const d1 = new Date(last.date).getTime();
  const months = (d1 - d0) / (1000 * 60 * 60 * 24 * 30.44);
  if (months < 0.1) return null;
  return (last.equity - first.equity) / months;
}

/** Find the max drawdown period: returns { peakIdx, troughIdx, drawdown, drawdownPct } */
function findMDD(points: { equity: number }[]) {
  let peak = points[0].equity;
  let peakIdx = 0;
  let mddPeakIdx = 0;
  let mddTroughIdx = 0;
  let maxDD = 0;

  for (let i = 1; i < points.length; i++) {
    if (points[i].equity > peak) {
      peak = points[i].equity;
      peakIdx = i;
    }
    const dd = peak - points[i].equity;
    if (dd > maxDD) {
      maxDD = dd;
      mddPeakIdx = peakIdx;
      mddTroughIdx = i;
    }
  }

  const pctDD = peak !== 0 ? (maxDD / peak) * 100 : 0;
  return { peakIdx: mddPeakIdx, troughIdx: mddTroughIdx, drawdown: maxDD, drawdownPct: pctDD };
}

export default function EquityCurve({ points, title = "Equity Curve", oosCutoffDate, showMDD = false }: Props) {
  if (points.length < 2) return null;

  const maxEq = Math.max(...points.map((p) => p.equity));
  const minEq = Math.min(...points.map((p) => p.equity));
  const range = maxEq - minEq || 1;
  const h = 120;
  const w = 600;

  // Find OOS split index
  let splitIdx = -1;
  if (oosCutoffDate) {
    for (let i = 0; i < points.length; i++) {
      if (points[i].date > oosCutoffDate) {
        splitIdx = i;
        break;
      }
    }
  }

  const toXY = (p: { equity: number }, i: number) => {
    const x = (i / (points.length - 1)) * w;
    const y = h - ((p.equity - minEq) / range) * (h - 10) - 5;
    return { x, y };
  };

  // Build IS and OOS path segments
  let isPath = "";
  let oosPath = "";

  if (splitIdx > 0) {
    const isPoints = points.slice(0, splitIdx + 1);
    isPath = isPoints.map((p, i) => {
      const { x, y } = toXY(p, i);
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(" ");

    const oosPoints = points.slice(splitIdx);
    oosPath = oosPoints.map((p, j) => {
      const { x, y } = toXY(p, splitIdx + j);
      return `${j === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(" ");
  } else {
    isPath = points.map((p, i) => {
      const { x, y } = toXY(p, i);
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(" ");
  }

  const finalEq = points[points.length - 1].equity;
  const isColor = "#22c55e";
  const oosColor = "#ef4444";
  const singleColor = finalEq >= 0 ? isColor : oosColor;

  // Slopes
  const isSlope = splitIdx > 0 ? slopePerMonth(points.slice(0, splitIdx + 1)) : null;
  const oosSlope = splitIdx > 0 ? slopePerMonth(points.slice(splitIdx)) : null;
  const cutoffX = splitIdx > 0 ? (splitIdx / (points.length - 1)) * w : -1;

  // MDD region
  const mdd = showMDD ? findMDD(points) : null;
  const mddX1 = mdd ? (mdd.peakIdx / (points.length - 1)) * w : 0;
  const mddX2 = mdd ? (mdd.troughIdx / (points.length - 1)) * w : 0;

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        {title}
      </h4>
      <div className="bg-ag-dark/50 rounded p-2 overflow-x-auto">
        <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ maxHeight: "140px" }}>
          {/* MDD shaded region */}
          {mdd && mdd.drawdown > 0 && (
            <rect
              x={mddX1} y={0} width={mddX2 - mddX1} height={h}
              fill="#ef4444" opacity="0.12"
            />
          )}
          {/* Zero line */}
          {minEq < 0 && maxEq > 0 && (
            <line x1="0" y1={h - ((0 - minEq) / range) * (h - 10) - 5}
              x2={w} y2={h - ((0 - minEq) / range) * (h - 10) - 5}
              stroke="#555" strokeWidth="0.5" strokeDasharray="4 2" />
          )}
          {/* OOS cutoff vertical line */}
          {cutoffX > 0 && (
            <line x1={cutoffX} y1={0} x2={cutoffX} y2={h}
              stroke="#888" strokeWidth="0.8" strokeDasharray="3 2" />
          )}
          {/* IS path (green) */}
          <path d={isPath} fill="none" stroke={splitIdx > 0 ? isColor : singleColor} strokeWidth="1.5" />
          {/* OOS path (red) */}
          {oosPath && (
            <path d={oosPath} fill="none" stroke={oosColor} strokeWidth="1.5" />
          )}
          {/* MDD boundary lines */}
          {mdd && mdd.drawdown > 0 && (
            <>
              <line x1={mddX1} y1={0} x2={mddX1} y2={h}
                stroke="#ef4444" strokeWidth="0.6" strokeDasharray="2 2" opacity="0.5" />
              <line x1={mddX2} y1={0} x2={mddX2} y2={h}
                stroke="#ef4444" strokeWidth="0.6" strokeDasharray="2 2" opacity="0.5" />
            </>
          )}
        </svg>
        <div className="flex justify-between text-[10px] text-ag-text-muted mt-1">
          <span>{points[0].date}</span>
          <span className={finalEq >= 0 ? "text-ag-green" : "text-ag-red"}>
            ${finalEq.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </span>
          <span>{points[points.length - 1].date}</span>
        </div>
        {/* IS / OOS slopes */}
        {isSlope !== null && oosSlope !== null && (
          <div className="flex gap-4 mt-1 text-[10px]">
            <span className="text-ag-green">
              IS slope: ${isSlope.toFixed(0)}/mo
            </span>
            <span className="text-ag-red">
              OOS slope: ${oosSlope.toFixed(0)}/mo
            </span>
          </div>
        )}
        {/* MDD label */}
        {mdd && mdd.drawdown > 0 && (
          <div className="flex gap-3 mt-1 text-[10px]">
            <span className="text-ag-red">
              MDD: -${mdd.drawdown.toLocaleString(undefined, { maximumFractionDigits: 0 })} ({mdd.drawdownPct.toFixed(1)}%)
            </span>
            <span className="text-ag-text-muted">
              {points[mdd.peakIdx].date} → {points[mdd.troughIdx].date}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
