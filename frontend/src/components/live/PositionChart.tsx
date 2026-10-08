"use client";
import { useState, useEffect, useRef } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { CandleData } from "@/lib/api";

const TF_MS: Record<string, number> = {
  "1": 60_000, "3": 180_000, "5": 300_000, "15": 900_000,
  "30": 1_800_000, "60": 3_600_000, "120": 7_200_000,
  "240": 14_400_000, "360": 21_600_000, "720": 43_200_000,
  "D": 86_400_000, "W": 604_800_000,
};
const BARS_BEFORE = 50;
const BARS_AFTER = 10;

/** Parse a tz-naive ISO string (represents NYT) into "fake UTC" unix second. */
function nytToChartUnix(iso: string): number {
  const [datePart, timePart] = iso.split("T");
  const [y, m, d] = datePart.split("-").map(Number);
  const parts = (timePart || "00:00:00").split(":");
  const h = Number(parts[0]) || 0;
  const min = Number(parts[1]) || 0;
  const s = Number(parts[2]?.split(".")[0]) || 0;
  return Date.UTC(y, m - 1, d, h, min, s) / 1000;
}

interface Props {
  symbol: string;
  side: string;
  entryTime: string;
  exitTime: string | null;
  entryPrice: number;
  exitPrice: number | null;
  slPrice: number | null;
  tpPrice: number | null;
}

export default function PositionChart({ symbol, side, entryTime, exitTime, entryPrice, exitPrice, slPrice, tpPrice }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<unknown>(null);
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const timeframe = "30";

  useEffect(() => {
    const barMs = TF_MS[timeframe] ?? 1_800_000;
    const entryMs = new Date(entryTime).getTime();
    const exitMs = exitTime ? new Date(exitTime).getTime() : entryMs;
    const startMs = entryMs - BARS_BEFORE * barMs;
    const endMs = exitMs + BARS_AFTER * barMs;

    setLoading(true);
    setError("");
    api.chartCandles(symbol, timeframe, new Date(startMs).toISOString(), new Date(endMs).toISOString())
      .then((res) => {
        if (res.candles.length === 0) setError("No candle data for this range");
        setCandles(res.candles);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load chart"))
      .finally(() => setLoading(false));
  }, [symbol, entryTime, exitTime]);

  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return;
    let disposed = false;

    (async () => {
      const lc = await import("lightweight-charts");
      if (disposed || !containerRef.current) return;

      if (chartRef.current) {
        (chartRef.current as { remove: () => void }).remove();
        chartRef.current = null;
      }

      const chart = lc.createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: 280,
        layout: {
          background: { type: lc.ColorType.Solid, color: "#131722" },
          textColor: "#d1d4dc",
        },
        grid: { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
        crosshair: { mode: lc.CrosshairMode.Normal },
        timeScale: { timeVisible: true, secondsVisible: false },
        rightPriceScale: { borderColor: "#1e222d" },
      });

      const series = chart.addSeries(lc.CandlestickSeries, {
        upColor: "#22c55e", downColor: "#ef4444",
        borderUpColor: "#22c55e", borderDownColor: "#ef4444",
        wickUpColor: "#22c55e", wickDownColor: "#ef4444",
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      series.setData(candles as any);

      const isLong = side === "Buy" || side.toLowerCase().includes("long");
      const entryUnix = nytToChartUnix(entryTime);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const markers: any[] = [{
        time: entryUnix,
        position: isLong ? "belowBar" : "aboveBar",
        color: isLong ? "#22c55e" : "#ef4444",
        shape: isLong ? "arrowUp" : "arrowDown",
        text: `Entry $${entryPrice.toFixed(2)}`,
      }];
      if (exitTime && exitPrice) {
        markers.push({
          time: nytToChartUnix(exitTime),
          position: isLong ? "aboveBar" : "belowBar",
          color: "#3b82f6", shape: "circle",
          text: `Exit $${exitPrice.toFixed(2)}`,
        });
      }
      markers.sort((a: { time: number }, b: { time: number }) => a.time - b.time);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      lc.createSeriesMarkers(series as any, markers);

      if (slPrice) {
        series.createPriceLine({
          price: slPrice, color: "#ef4444", lineWidth: 1,
          lineStyle: lc.LineStyle.Dashed, axisLabelVisible: true, title: "SL",
        });
      }
      if (tpPrice) {
        series.createPriceLine({
          price: tpPrice, color: "#22c55e", lineWidth: 1,
          lineStyle: lc.LineStyle.Dashed, axisLabelVisible: true, title: "TP",
        });
      }
      series.createPriceLine({
        price: entryPrice, color: "#6366f1", lineWidth: 1,
        lineStyle: lc.LineStyle.Dotted, axisLabelVisible: true, title: "Entry",
      });

      chart.timeScale().fitContent();
      chartRef.current = chart;

      const ro = new ResizeObserver(() => {
        if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
      });
      ro.observe(containerRef.current);
      return () => ro.disconnect();
    })();

    return () => {
      disposed = true;
      if (chartRef.current) {
        (chartRef.current as { remove: () => void }).remove();
        chartRef.current = null;
      }
    };
  }, [candles, side, entryTime, exitTime, entryPrice, exitPrice, slPrice, tpPrice]);

  return (
    <div className="bg-[#131722] rounded-lg overflow-hidden">
      {loading && (
        <div className="h-[280px] flex items-center justify-center text-ag-text-muted gap-2">
          <Loader2 size={16} className="animate-spin" /> Loading chart...
        </div>
      )}
      {error && !loading && (
        <div className="h-[280px] flex items-center justify-center text-ag-text-muted text-xs">
          {error}
        </div>
      )}
      <div ref={containerRef} className={loading || error ? "hidden" : ""} style={{ height: 280 }} />
    </div>
  );
}
