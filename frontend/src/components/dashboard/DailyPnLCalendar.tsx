"use client";
import { useState, useCallback, useMemo } from "react";
import { Calendar, ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";

interface DayData {
  date: string;
  pnl: number;
  trades: number;
  wins: number;
  losses: number;
}

interface Props {
  portfolioId?: number;
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function getPnlColor(pnl: number, maxAbs: number): string {
  if (pnl === 0) return "bg-ag-dark/40";
  const intensity = Math.min(Math.abs(pnl) / (maxAbs || 1), 1);
  if (pnl > 0) {
    if (intensity > 0.7) return "bg-green-500/70";
    if (intensity > 0.4) return "bg-green-500/40";
    return "bg-green-500/20";
  }
  if (intensity > 0.7) return "bg-red-500/70";
  if (intensity > 0.4) return "bg-red-500/40";
  return "bg-red-500/20";
}

export default function DailyPnLCalendar({ portfolioId }: Props) {
  const now = new Date();
  const [viewYear, setViewYear] = useState(now.getFullYear());
  const [viewMonth, setViewMonth] = useState(now.getMonth());
  const [hoveredDay, setHoveredDay] = useState<DayData | null>(null);

  const { data } = useApi(
    useCallback(() => api.dailyPnlHeatmap(3, portfolioId), [portfolioId]),
    `daily-pnl-heatmap-${portfolioId ?? "all"}`,
  );

  const dayMap = useMemo(() => {
    const map: Record<string, DayData> = {};
    for (const d of data?.days ?? []) map[d.date] = d;
    return map;
  }, [data]);

  // Max absolute P&L for color scaling
  const maxAbs = useMemo(() => {
    const days = data?.days ?? [];
    if (days.length === 0) return 100;
    return Math.max(...days.map((d: DayData) => Math.abs(d.pnl)), 1);
  }, [data]);

  // Build calendar grid for current month
  const calendarDays = useMemo(() => {
    const firstDay = new Date(viewYear, viewMonth, 1);
    const lastDay = new Date(viewYear, viewMonth + 1, 0);
    // Monday-based week: 0=Mon, 6=Sun
    const startDow = (firstDay.getDay() + 6) % 7;
    const totalDays = lastDay.getDate();

    const cells: (string | null)[] = [];
    // Pad before first day
    for (let i = 0; i < startDow; i++) cells.push(null);
    // Days of month
    for (let d = 1; d <= totalDays; d++) {
      const dateStr = `${viewYear}-${String(viewMonth + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      cells.push(dateStr);
    }
    return cells;
  }, [viewYear, viewMonth]);

  // Month stats
  const monthStats = useMemo(() => {
    let pnl = 0, trades = 0, wins = 0, losses = 0, greenDays = 0, redDays = 0;
    for (const cell of calendarDays) {
      if (!cell) continue;
      const d = dayMap[cell];
      if (!d) continue;
      pnl += d.pnl;
      trades += d.trades;
      wins += d.wins;
      losses += d.losses;
      if (d.pnl > 0) greenDays++;
      if (d.pnl < 0) redDays++;
    }
    return { pnl, trades, wins, losses, greenDays, redDays };
  }, [calendarDays, dayMap]);

  const prevMonth = () => {
    if (viewMonth === 0) { setViewYear(viewYear - 1); setViewMonth(11); }
    else setViewMonth(viewMonth - 1);
  };
  const nextMonth = () => {
    if (viewMonth === 11) { setViewYear(viewYear + 1); setViewMonth(0); }
    else setViewMonth(viewMonth + 1);
  };

  const monthLabel = new Date(viewYear, viewMonth).toLocaleString("en-US", { month: "long", year: "numeric" });

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Calendar size={16} className="text-ag-accent" />
          <h3 className="text-sm font-semibold">Daily P&L</h3>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={prevMonth} className="p-1 hover:bg-ag-dark rounded transition-colors">
            <ChevronLeft size={16} className="text-ag-text-muted" />
          </button>
          <span className="text-sm font-medium min-w-[140px] text-center">{monthLabel}</span>
          <button onClick={nextMonth} className="p-1 hover:bg-ag-dark rounded transition-colors">
            <ChevronRight size={16} className="text-ag-text-muted" />
          </button>
        </div>
      </div>

      {/* Month summary badges */}
      <div className="grid grid-cols-4 gap-2">
        <div className="text-center py-1.5 bg-ag-dark/40 rounded-lg">
          <p className="text-[9px] text-ag-text-muted uppercase">Month P&L</p>
          <p className={`text-sm font-bold font-mono ${monthStats.pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
            {monthStats.pnl >= 0 ? "+" : ""}${monthStats.pnl.toFixed(0)}
          </p>
        </div>
        <div className="text-center py-1.5 bg-ag-dark/40 rounded-lg">
          <p className="text-[9px] text-ag-text-muted uppercase">Trades</p>
          <p className="text-sm font-bold">{monthStats.trades}</p>
        </div>
        <div className="text-center py-1.5 bg-ag-dark/40 rounded-lg">
          <p className="text-[9px] text-ag-text-muted uppercase">Green Days</p>
          <p className="text-sm font-bold text-ag-green">{monthStats.greenDays}</p>
        </div>
        <div className="text-center py-1.5 bg-ag-dark/40 rounded-lg">
          <p className="text-[9px] text-ag-text-muted uppercase">Red Days</p>
          <p className="text-sm font-bold text-ag-red">{monthStats.redDays}</p>
        </div>
      </div>

      {/* Calendar grid */}
      <div>
        {/* Weekday headers */}
        <div className="grid grid-cols-7 gap-1 mb-1">
          {WEEKDAYS.map((d) => (
            <div key={d} className="text-center text-[9px] text-ag-text-muted uppercase py-1">{d}</div>
          ))}
        </div>
        {/* Day cells */}
        <div className="grid grid-cols-7 gap-1">
          {calendarDays.map((dateStr, i) => {
            if (!dateStr) return <div key={`empty-${i}`} className="aspect-square" />;
            const dayNum = parseInt(dateStr.split("-")[2]);
            const d = dayMap[dateStr];
            const isToday = dateStr === `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;

            return (
              <div
                key={dateStr}
                className={`aspect-square rounded-md flex flex-col items-center justify-center cursor-default relative transition-all
                  ${d ? getPnlColor(d.pnl, maxAbs) : "bg-ag-dark/20"}
                  ${isToday ? "ring-1 ring-ag-accent" : ""}
                  hover:ring-1 hover:ring-ag-text/30`}
                onMouseEnter={() => d && setHoveredDay(d)}
                onMouseLeave={() => setHoveredDay(null)}
              >
                <span className={`text-[10px] ${d ? "font-medium" : "text-ag-text-muted/50"}`}>{dayNum}</span>
                {d && (
                  <span className={`text-[8px] font-mono font-bold ${d.pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                    {d.pnl >= 0 ? "+" : ""}{Math.abs(d.pnl) >= 1000 ? `${(d.pnl / 1000).toFixed(1)}k` : d.pnl.toFixed(0)}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Hover tooltip */}
      {hoveredDay && (
        <div className="flex items-center gap-4 px-3 py-2 bg-ag-dark/60 rounded-lg text-xs">
          <span className="text-ag-text-muted">{new Date(hoveredDay.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}</span>
          <span className={`font-bold font-mono ${hoveredDay.pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
            {hoveredDay.pnl >= 0 ? "+" : ""}${hoveredDay.pnl.toFixed(2)}
          </span>
          <span className="text-ag-text-muted">{hoveredDay.trades} trade{hoveredDay.trades !== 1 ? "s" : ""}</span>
          <span className="text-ag-green">{hoveredDay.wins}W</span>
          <span className="text-ag-red">{hoveredDay.losses}L</span>
        </div>
      )}
    </div>
  );
}
