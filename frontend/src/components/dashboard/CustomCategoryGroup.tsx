"use client";
import { useState, useEffect, useMemo } from "react";
import { api } from "@/lib/api";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts";

const TIMEFRAMES = ["1W", "1M", "3M", "6M", "1Y", "ALL"] as const;
const COLORS = [
  "#22c55e", "#3b82f6", "#f59e0b", "#ef4444", "#a855f7",
  "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#6366f1",
];
const AGG_METHODS = ["Average", "Median", "Weighted"] as const;
type AggMethod = (typeof AGG_METHODS)[number];

interface CatData {
  d: number; w: number; m: number; y: number;
  asset_count: number; symbols: string[];
}

interface Props {
  allCategories: string[];
  source: string;
  categoryData: Record<string, CatData>;
}

function median(arr: number[]): number {
  if (arr.length === 0) return 0;
  const sorted = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function aggregate(
  selected: string[],
  dwmy: Record<string, CatData>,
  method: AggMethod,
): { d: number; w: number; m: number; y: number; totalAssets: number } | null {
  if (selected.length === 0) return null;
  const cats = selected.filter((c) => dwmy[c]);
  if (cats.length === 0) return null;
  const totalAssets = cats.reduce((s, c) => s + (dwmy[c]?.asset_count ?? 0), 0);

  if (method === "Average") {
    return {
      d: cats.reduce((s, c) => s + (dwmy[c]?.d ?? 0), 0) / cats.length,
      w: cats.reduce((s, c) => s + (dwmy[c]?.w ?? 0), 0) / cats.length,
      m: cats.reduce((s, c) => s + (dwmy[c]?.m ?? 0), 0) / cats.length,
      y: cats.reduce((s, c) => s + (dwmy[c]?.y ?? 0), 0) / cats.length,
      totalAssets,
    };
  }
  if (method === "Median") {
    return {
      d: median(cats.map((c) => dwmy[c]?.d ?? 0)),
      w: median(cats.map((c) => dwmy[c]?.w ?? 0)),
      m: median(cats.map((c) => dwmy[c]?.m ?? 0)),
      y: median(cats.map((c) => dwmy[c]?.y ?? 0)),
      totalAssets,
    };
  }
  // Weighted by asset count
  if (totalAssets === 0) return { d: 0, w: 0, m: 0, y: 0, totalAssets: 0 };
  return {
    d: cats.reduce((s, c) => s + (dwmy[c]?.d ?? 0) * (dwmy[c]?.asset_count ?? 0), 0) / totalAssets,
    w: cats.reduce((s, c) => s + (dwmy[c]?.w ?? 0) * (dwmy[c]?.asset_count ?? 0), 0) / totalAssets,
    m: cats.reduce((s, c) => s + (dwmy[c]?.m ?? 0) * (dwmy[c]?.asset_count ?? 0), 0) / totalAssets,
    y: cats.reduce((s, c) => s + (dwmy[c]?.y ?? 0) * (dwmy[c]?.asset_count ?? 0), 0) / totalAssets,
    totalAssets,
  };
}

export default function CustomCategoryGroup({ allCategories, source, categoryData }: Props) {
  const [selected, setSelected] = useState<string[]>([]);
  const [timeframe, setTimeframe] = useState<string>("1M");
  const [aggMethod, setAggMethod] = useState<AggMethod>("Average");
  const [chartData, setChartData] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);

  const toggleCat = (cat: string) => {
    setSelected((prev) =>
      prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat],
    );
  };

  // Fetch chart data when selection or timeframe changes
  useEffect(() => {
    if (selected.length === 0) {
      setChartData([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api.categoryChart(selected, timeframe, source).then((r) => {
      if (cancelled) return;
      const data = r.dates.map((date: string, i: number) => {
        const point: Record<string, unknown> = { date: date.slice(5) };
        for (const cat of selected) {
          point[cat] = r.series[cat]?.[i] ?? 0;
        }
        return point;
      });
      setChartData(data);
      setLoading(false);
    }).catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selected, timeframe, source]);

  const combined = useMemo(
    () => aggregate(selected, categoryData, aggMethod),
    [selected, categoryData, aggMethod],
  );

  return (
    <div className="bg-ag-dark/30 rounded-lg p-3 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10px] text-ag-text-muted uppercase font-medium whitespace-nowrap">
          Custom Group
        </p>
        <div className="flex items-center gap-2">
          {/* Aggregation method */}
          <select
            value={aggMethod}
            onChange={(e) => setAggMethod(e.target.value as AggMethod)}
            className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-[9px] text-ag-text"
          >
            {AGG_METHODS.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          {/* Timeframe */}
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
      </div>

      {/* Category multi-select */}
      <div className="flex flex-wrap gap-1">
        {allCategories.map((cat) => (
          <button key={cat} onClick={() => toggleCat(cat)}
            className={`px-2 py-0.5 rounded-full text-[10px] font-medium border transition-colors ${
              selected.includes(cat)
                ? "bg-ag-accent/20 text-ag-accent border-ag-accent/40"
                : "bg-ag-dark/50 text-ag-text-muted border-ag-border hover:border-ag-text-muted"
            }`}>
            {cat}
          </button>
        ))}
      </div>

      {/* Combined D/W/M/Y with asset count */}
      {combined && (
        <div className="flex items-center gap-4">
          <div className="text-center">
            <p className="text-[9px] text-ag-text-muted uppercase">Assets</p>
            <p className="text-sm font-mono font-bold text-ag-text">{combined.totalAssets}</p>
          </div>
          {(["d", "w", "m", "y"] as const).map((p) => (
            <div key={p} className="text-center">
              <p className="text-[9px] text-ag-text-muted uppercase">
                {p === "d" ? "Day" : p === "w" ? "Week" : p === "m" ? "Month" : "Year"}
              </p>
              <p className={`text-sm font-mono font-bold ${combined[p] >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                {combined[p] >= 0 ? "+" : ""}{combined[p].toFixed(1)}%
              </p>
            </div>
          ))}
          <p className="text-[9px] text-ag-text-muted ml-auto">({aggMethod})</p>
        </div>
      )}

      {/* Line chart with legend + labeled tooltip */}
      {selected.length > 0 && chartData.length > 0 && (
        <div className="h-52">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <XAxis dataKey="date" tick={{ fontSize: 9, fill: "#6b7280" }} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 9, fill: "#6b7280" }} tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
              <Tooltip
                contentStyle={{ background: "#1a1a2e", border: "1px solid #2a2a3e", borderRadius: 8, fontSize: 11 }}
                formatter={(value: number, name: string) => [`${value.toFixed(1)}%`, name]}
                labelStyle={{ color: "#9ca3af", fontSize: 10 }}
              />
              <Legend
                wrapperStyle={{ fontSize: 10 }}
                iconType="circle"
                iconSize={8}
              />
              {selected.map((cat, i) => (
                <Line key={cat} type="monotone" dataKey={cat} name={cat}
                  stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={1.5} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {loading && <p className="text-[10px] text-ag-text-muted">Loading chart...</p>}
      {selected.length === 0 && (
        <p className="text-[10px] text-ag-text-muted">Select categories above to see combined performance.</p>
      )}
    </div>
  );
}
