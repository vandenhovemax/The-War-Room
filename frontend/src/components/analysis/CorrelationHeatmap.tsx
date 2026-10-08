"use client";

interface CorrelationHeatmapProps {
  symbols: string[];
  matrix: number[][];
}

function getColor(val: number): string {
  // Red (negative) → White (0) → Green (positive)
  const clamped = Math.max(-1, Math.min(1, val));
  if (clamped >= 0) {
    const g = Math.round(180 * clamped);
    return `rgb(${255 - g}, 255, ${255 - g})`;
  }
  const r = Math.round(180 * Math.abs(clamped));
  return `rgb(255, ${255 - r}, ${255 - r})`;
}

export default function CorrelationHeatmap({ symbols, matrix }: CorrelationHeatmapProps) {
  if (symbols.length === 0) return null;

  return (
    <div className="overflow-auto">
      <table className="text-[10px] border-collapse">
        <thead>
          <tr>
            <th className="p-1" />
            {symbols.map((s, idx) => (
              <th key={idx} className="p-1 text-ag-text-muted font-normal -rotate-45 origin-left whitespace-nowrap">
                {s.replace("USDT", "")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {symbols.map((row, i) => (
            <tr key={i}>
              <td className="p-1 text-ag-text-muted whitespace-nowrap pr-2">{row.replace("USDT", "")}</td>
              {matrix[i].map((val, j) => (
                <td
                  key={`${i}-${j}`}
                  className="p-0.5 text-center min-w-[28px]"
                  title={`${row} / ${symbols[j]}: ${val.toFixed(2)}`}
                >
                  <div
                    className="rounded-sm px-0.5 py-0.5 text-[9px] font-mono"
                    style={{
                      backgroundColor: getColor(val),
                      color: Math.abs(val) > 0.6 ? "#fff" : "#000",
                    }}
                  >
                    {val.toFixed(2)}
                  </div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
