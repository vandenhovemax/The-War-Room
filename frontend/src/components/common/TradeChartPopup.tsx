"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import { X, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { CandleData, ChartReplayData, IndicatorSeries } from "@/lib/api";

/* ── Trade data the popup needs ── */
export interface TradeChartData {
  symbol: string;
  timeframe: string;
  side: string;
  entryTime: string;
  exitTime: string | null;
  entryPrice: number;
  exitPrice?: number | null;
  slPrice?: number | null;
  tpPrice?: number | null;
  netProfit: number;
  strategyId?: number;
  runId?: number | null;
}

/* ── Timeframe helpers ── */
const TF_MS: Record<string, number> = {
  "1": 60_000, "3": 180_000, "5": 300_000, "15": 900_000,
  "30": 1_800_000, "60": 3_600_000, "120": 7_200_000,
  "240": 14_400_000, "360": 21_600_000, "720": 43_200_000,
  "D": 86_400_000, "W": 604_800_000,
};

const TF_LABELS: Record<string, string> = {
  "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
  "60": "1h", "120": "2h", "240": "4h", "360": "6h", "720": "12h",
  "D": "1D", "W": "1W",
};

const BARS_BEFORE = 200;
const BARS_AFTER = 50;

/**
 * Parse a tz-naive ISO string (represents NYT) into a "fake UTC" unix second.
 * The backend shifts candle times the same way, so markers align with candles
 * and the chart x-axis displays NYT — regardless of the user's browser TZ.
 */
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
  trade: TradeChartData;
  onClose: () => void;
}

export default function TradeChartPopup({ trade, onClose }: Props) {
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [replay, setReplay] = useState<ChartReplayData | null>(null);
  const [overlays, setOverlays] = useState<IndicatorSeries[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  /* ── Drag state ── */
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragOffset = useRef({ x: 0, y: 0 });

  /* ── Chart refs ── */
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartApiRef = useRef<unknown>(null);

  const tfLabel = TF_LABELS[trade.timeframe] || trade.timeframe;

  /* ── Center on mount ── */
  useEffect(() => {
    const w = Math.min(800, window.innerWidth - 32);
    setPos({
      x: Math.max(0, (window.innerWidth - w) / 2),
      y: Math.max(0, (window.innerHeight - 580) / 2),
    });
  }, []);

  /* ── Fetch candles + indicators ── */
  useEffect(() => {
    const barMs = TF_MS[trade.timeframe] ?? 1_800_000;
    const entryMs = new Date(trade.entryTime).getTime();
    const exitMs = trade.exitTime
      ? new Date(trade.exitTime).getTime()
      : entryMs;
    const startMs = entryMs - BARS_BEFORE * barMs;
    const endMs = exitMs + BARS_AFTER * barMs;

    setLoading(true);
    setError("");
    api
      .chartCandles(
        trade.symbol,
        trade.timeframe,
        new Date(startMs).toISOString(),
        new Date(endMs).toISOString(),
        undefined,
        trade.strategyId,
        trade.runId ?? null,
      )
      .then((res) => {
        if (res.candles.length === 0) {
          setError("No candle data available for this time range");
        }
        setCandles(res.candles);
        setOverlays(res.indicators?.overlays ?? []);
        setReplay(res.replay ?? null);
      })
      .catch((e) =>
        setError(e instanceof Error ? e.message : "Failed to load chart data"),
      )
      .finally(() => setLoading(false));
  }, [trade.symbol, trade.timeframe, trade.entryTime, trade.exitTime, trade.strategyId, trade.runId]);

  /* ── Render main price chart ── */
  useEffect(() => {
    if (!chartContainerRef.current || candles.length === 0) return;

    let disposed = false;

    (async () => {
      const lc = await import("lightweight-charts");

      if (disposed || !chartContainerRef.current) return;

      // Clean up previous chart
      if (chartApiRef.current) {
        (chartApiRef.current as { remove: () => void }).remove();
        chartApiRef.current = null;
      }

      const chartHeight = 400;

      const chart = lc.createChart(chartContainerRef.current, {
        width: chartContainerRef.current.clientWidth,
        height: chartHeight,
        layout: {
          background: { type: lc.ColorType.Solid, color: "#131722" },
          textColor: "#d1d4dc",
        },
        grid: {
          vertLines: { color: "#1e222d" },
          horzLines: { color: "#1e222d" },
        },
        crosshair: { mode: lc.CrosshairMode.Normal },
        timeScale: {
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 6,
          barSpacing: 6,
          minBarSpacing: 0.5,
        },
        rightPriceScale: {
          borderColor: "#1e222d",
          autoScale: true,
        },
        handleScroll: {
          mouseWheel: true,
          pressedMouseMove: true,
          horzTouchDrag: true,
          vertTouchDrag: true,
        },
        handleScale: {
          axisPressedMouseMove: true,
          mouseWheel: true,
          pinch: true,
        },
      });

      const series = chart.addSeries(lc.CandlestickSeries, {
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderUpColor: "#22c55e",
        borderDownColor: "#ef4444",
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      series.setData(candles as any);

      /* ── Indicator overlays (EMAs etc.) + structural overlays (fibs/pivots) ── */
      const allOverlays: IndicatorSeries[] = [
        ...overlays,
        ...(replay?.structural_overlays ?? []),
      ];
      for (const ov of allOverlays) {
        if (!ov.data || ov.data.length === 0) continue;
        const line = chart.addSeries(lc.LineSeries, {
          color: ov.color || "#94a3b8",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          // Candles drive the Y-axis. Overlays render where their values sit
          // but don't pull the autoscale — a bad indicator can't squash price.
          autoscaleInfoProvider: () => null,
        });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        line.setData(ov.data as any);
      }

      /* ── Markers: strategy triggers + entry + exit ── */
      const isLong = trade.side.toLowerCase().includes("long");
      const entryUnix = nytToChartUnix(trade.entryTime);

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const markers: any[] = [];

      // Strategy-signal triggers (small dots — below bar for long, above for short)
      for (const t of replay?.triggers ?? []) {
        // Skip the trigger that coincides with the actual entry (avoid visual clash)
        if (Math.abs(t.time - entryUnix) < 1) continue;
        markers.push({
          time: t.time,
          position: t.direction === "long" ? "belowBar" : "aboveBar",
          color: t.direction === "long" ? "#22c55e" : "#ef4444",
          shape: "circle",
          size: 0.7,
          text: t.comment || "",
        });
      }

      // Actual entry (large arrow)
      markers.push({
        time: entryUnix,
        position: isLong ? "belowBar" : "aboveBar",
        color: isLong ? "#22c55e" : "#ef4444",
        shape: isLong ? "arrowUp" : "arrowDown",
        text: `Entry ${trade.entryPrice}`,
      });

      if (trade.exitTime && trade.exitPrice) {
        const exitUnix = nytToChartUnix(trade.exitTime);
        markers.push({
          time: exitUnix,
          position: isLong ? "aboveBar" : "belowBar",
          color: "#3b82f6",
          shape: "circle",
          text: `Exit ${trade.exitPrice}`,
        });
      }

      markers.sort(
        (a: { time: number }, b: { time: number }) => a.time - b.time,
      );
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      lc.createSeriesMarkers(series as any, markers);

      /* ── Price lines: SL + TP ── */
      if (trade.slPrice) {
        series.createPriceLine({
          price: trade.slPrice,
          color: "#ef4444",
          lineWidth: 1,
          lineStyle: lc.LineStyle.Dashed,
          axisLabelVisible: true,
          title: "SL",
        });
      }

      if (trade.tpPrice) {
        series.createPriceLine({
          price: trade.tpPrice,
          color: "#22c55e",
          lineWidth: 1,
          lineStyle: lc.LineStyle.Dashed,
          axisLabelVisible: true,
          title: "TP",
        });
      }

      /* ── Entry price line ── */
      series.createPriceLine({
        price: trade.entryPrice,
        color: "#6366f1",
        lineWidth: 1,
        lineStyle: lc.LineStyle.Dotted,
        axisLabelVisible: true,
        title: "Entry",
      });

      chart.timeScale().fitContent();
      chartApiRef.current = chart;

      /* ── Resize observer ── */
      const ro = new ResizeObserver(() => {
        if (chartContainerRef.current) {
          chart.applyOptions({
            width: chartContainerRef.current.clientWidth,
          });
        }
      });
      ro.observe(chartContainerRef.current);

      return () => ro.disconnect();
    })();

    return () => {
      disposed = true;
      if (chartApiRef.current) {
        (chartApiRef.current as { remove: () => void }).remove();
        chartApiRef.current = null;
      }
    };
  }, [candles, trade, overlays, replay]);

  /* ── Drag handlers ── */
  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      setDragging(true);
      dragOffset.current = { x: e.clientX - pos.x, y: e.clientY - pos.y };
    },
    [pos],
  );

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      setPos({
        x: e.clientX - dragOffset.current.x,
        y: e.clientY - dragOffset.current.y,
      });
    };
    const onUp = () => setDragging(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragging]);

  /* ── Escape key to close ── */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  /* Portal to document.body — prevents hydration errors when rendered inside <tbody> or other strict DOM contexts */
  return createPortal(
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 z-[200]"
        onClick={onClose}
      />

      {/* Popup */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ left: pos.x, top: pos.y }}
        className="fixed w-[800px] max-w-[calc(100vw-32px)] bg-[#131722] border border-ag-border rounded-xl shadow-2xl z-[201] overflow-hidden"
      >
        {/* ── Title bar (draggable) ── */}
        <div
          onMouseDown={onMouseDown}
          className="flex items-center justify-between px-4 py-2.5 bg-ag-dark border-b border-ag-border cursor-move select-none"
        >
          <div className="flex items-center gap-3 text-sm">
            <span className="font-semibold text-ag-text">
              {trade.symbol}
            </span>
            <span className="text-ag-text-muted">{tfLabel}</span>
            <span
              className={
                trade.side.toLowerCase().includes("long")
                  ? "text-ag-green"
                  : "text-ag-red"
              }
            >
              {trade.side.toUpperCase()}
            </span>
            <span
              className={
                trade.netProfit >= 0 ? "text-ag-green" : "text-ag-red"
              }
            >
              ${trade.netProfit.toFixed(2)}
            </span>
            {replay && !replay.error && (
              <span className="text-[11px] text-ag-text-muted">
                <span className="text-ag-green">{replay.trigger_count.long}L</span>
                {" · "}
                <span className="text-ag-red">{replay.trigger_count.short}S</span>
                {" signals"}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-ag-text-muted hover:text-ag-text p-1 rounded hover:bg-ag-border/50"
          >
            <X size={16} />
          </button>
        </div>

        {/* ── Chart area ── */}
        <div className="p-2">
          {loading && (
            <div className="h-[400px] flex items-center justify-center text-ag-text-muted gap-2">
              <Loader2 size={18} className="animate-spin" />
              Loading candles...
            </div>
          )}
          {error && !loading && (
            <div className="h-[400px] flex items-center justify-center text-ag-red text-sm">
              {error}
            </div>
          )}
          {/* Main price chart */}
          <div
            ref={chartContainerRef}
            className={loading || error ? "hidden" : ""}
            style={{ height: 400 }}
          />
          {!loading && !error && (
            <div className="flex items-center justify-between px-1 pt-1.5 text-[10px] text-ag-text-muted">
              <span>
                🖱 Scroll = zoom · Drag = pan · Drag axis = stretch
              </span>
              <button
                onClick={() => {
                  const c = chartApiRef.current as { timeScale: () => { fitContent: () => void } } | null;
                  if (c) c.timeScale().fitContent();
                }}
                className="px-2 py-0.5 rounded border border-ag-border/70 hover:border-ag-accent/50 hover:text-ag-accent transition-colors"
                title="Reset chart to fit entry + exit"
              >
                Reset view
              </button>
            </div>
          )}
        </div>
      </div>
    </>,
    document.body,
  );
}
