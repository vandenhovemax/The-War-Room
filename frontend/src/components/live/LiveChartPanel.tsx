"use client";
import type { LivePosition } from "@/lib/api";
import TVMiniChart from "@/components/dashboard/TVMiniChart";

interface Props {
  positions: LivePosition[];
}

export default function LiveChartPanel({ positions }: Props) {
  // Extract unique symbols from open positions
  const symbols = [...new Set(positions.map((p) => p.symbol))];

  if (symbols.length === 0) return null;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <h3 className="text-sm font-semibold mb-3">
        Active Charts ({symbols.length})
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {symbols.map((sym) => (
          <div key={sym} className="bg-ag-dark/30 rounded-lg overflow-hidden">
            <div className="px-3 py-1.5 border-b border-ag-border">
              <span className="text-xs font-medium">{sym}</span>
              {positions
                .filter((p) => p.symbol === sym)
                .map((p) => (
                  <span
                    key={p.id}
                    className={`ml-2 text-[10px] px-1.5 py-0.5 rounded ${
                      p.side === "Buy"
                        ? "bg-ag-green/15 text-ag-green"
                        : "bg-ag-red/15 text-ag-red"
                    }`}
                  >
                    {p.side === "Buy" ? "LONG" : "SHORT"}
                  </span>
                ))}
            </div>
            <TVMiniChart symbol={`BYBIT:${sym}`} height={160} />
          </div>
        ))}
      </div>
    </div>
  );
}
