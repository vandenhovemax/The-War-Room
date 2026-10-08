"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import CategorySelector from "./CategorySelector";

const TIMEFRAMES = ["1W", "1M", "3M", "6M", "1Y", "ALL"] as const;

interface Props {
  allCategories: string[];
  source: string;
}

interface FilteredResult {
  d: number; w: number; m: number; y: number;
  asset_count: number; symbols: string[];
  chart: { dates: string[]; values: number[] };
}

export default function FilteredCategoryGroup({ allCategories, source }: Props) {
  const [included, setIncluded] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [timeframe, setTimeframe] = useState<string>("1M");
  const [data, setData] = useState<FilteredResult | null>(null);
  const [chartData, setChartData] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);

  const hasSelection = included.length > 0 || excluded.length > 0;

  useEffect(() => {
    if (!hasSelection) {
      setData(null);
      setChartData([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api.filteredAssetReturns(included, excluded, timeframe, source).then((r) => {
      if (cancelled) return;
      setData(r);
      // Build chart data
      const points = r.chart.dates.map((date: string, i: number) => ({
        date: date.slice(5),
        return: r.chart.values[i] ?? 0,
      }));
      setChartData(points);
      setLoading(false);
    }).catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [included, excluded, timeframe, source, hasSelection]);

  // Build description
  const desc = [];
  if (included.length > 0) desc.push(included.join(" + "));
  if (excluded.length > 0) desc.push(`NOT ${excluded.join(", ")}`);
  const label = desc.join(" ") || "Filtered Group";

  return (
    <div className="bg-ag-dark/30 rounded-lg p-3 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10px] text-ag-text-muted uppercase font-medium whitespace-nowrap">
          Filtered Group
        </p>
        <div className="flex gap-1">
          {TIMEFRAMES.map((tf) => (
            <button key={tf} onClick={() => setTimeframe(tf)}
              className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${
                timeframe === tf ? "bg-ag-accent text-white" : "text-ag-text-muted hover:text-ag-text"
              }`}>
              {tf}
            </button>
          ))}
        </div>
      </div>

      {/* Tri-state category selector */}
      <CategorySelector
        categories={allCategories}
        included={included}
        excluded={excluded}
        onChange={(inc, exc) => { setIncluded(inc); setExcluded(exc); }}
      />

      {/* Description of the filter */}
      {hasSelection && (
        <p className="text-[10px] text-ag-text-muted">
          Showing: <span className="text-ag-text font-medium">{label}</span>
          {data && <span> ({data.asset_count} assets: {data.symbols.slice(0, 5).join(", ")}{data.symbols.length > 5 ? ` +${data.symbols.length - 5}` : ""})</span>}
        </p>
      )}

      {/* D/W/M/Y summary */}
      {data && (
        <div className="flex items-center gap-4">
          <div className="text-center">
            <p className="text-[9px] text-ag-text-muted uppercase">Assets</p>
            <p className="text-sm font-mono font-bold text-ag-text">{data.asset_count}</p>
          </div>
          {(["d", "w", "m", "y"] as const).map((p) => (
            <div key={p} className="text-center">
              <p className="text-[9px] text-ag-text-muted uppercase">
                {p === "d" ? "Day" : p === "w" ? "Week" : p === "m" ? "Month" : "Year"}
              </p>
              <p className={`text-sm font-mono font-bold ${data[p] >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                {data[p] >= 0 ? "+" : ""}{data[p].toFixed(1)}%
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Line chart */}
      {hasSelection && chartData.length > 0 && (
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <XAxis dataKey="date" tick={{ fontSize: 9, fill: "#6b7280" }} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 9, fill: "#6b7280" }} tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
              <Tooltip
                contentStyle={{ background: "#1a1a2e", border: "1px solid #2a2a3e", borderRadius: 8, fontSize: 11 }}
                formatter={(value: number) => [`${value.toFixed(1)}%`, label]}
                labelStyle={{ color: "#9ca3af", fontSize: 10 }}
              />
              <Line type="monotone" dataKey="return" name={label} stroke="#22c55e" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {loading && <p className="text-[10px] text-ag-text-muted">Loading filtered data...</p>}
      {!hasSelection && (
        <p className="text-[10px] text-ag-text-muted">
          Use the pills above: click to include (green), click again to exclude (red strikethrough). Shows assets in included categories that are NOT in excluded categories.
        </p>
      )}
    </div>
  );
}
