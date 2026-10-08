"use client";
import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import type { ReactNode } from "react";

interface CalendarGridProps {
  year: number;
  month: number; // 0-indexed (JS Date convention)
  onMonthChange: (year: number, month: number) => void;
  onDayClick: (date: string) => void;
  onAddTrade?: (date: string) => void;
  renderCell: (date: string) => ReactNode;
  selectedDate?: string;
}

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function toISO(y: number, m: number, d: number): string {
  return `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

function todayISO(): string {
  const d = new Date();
  return toISO(d.getFullYear(), d.getMonth(), d.getDate());
}

export default function CalendarGrid({
  year, month, onMonthChange, onDayClick, onAddTrade, renderCell, selectedDate,
}: CalendarGridProps) {
  const today = todayISO();
  const monthName = new Date(year, month).toLocaleString("default", { month: "long" });
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  // Monday = 0, Sunday = 6
  const firstWeekday = (new Date(year, month, 1).getDay() + 6) % 7;

  // Previous month padding
  const prevMonth = month === 0 ? 11 : month - 1;
  const prevYear = month === 0 ? year - 1 : year;
  const daysInPrev = new Date(prevYear, prevMonth + 1, 0).getDate();

  const cells: { date: string; day: number; current: boolean }[] = [];

  // Pad with previous month days
  for (let i = firstWeekday - 1; i >= 0; i--) {
    const d = daysInPrev - i;
    cells.push({ date: toISO(prevYear, prevMonth, d), day: d, current: false });
  }
  // Current month days
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push({ date: toISO(year, month, d), day: d, current: true });
  }
  // Pad with next month days to fill grid (6 rows)
  const nextMonth = month === 11 ? 0 : month + 1;
  const nextYear = month === 11 ? year + 1 : year;
  let nextDay = 1;
  while (cells.length < 42) {
    cells.push({ date: toISO(nextYear, nextMonth, nextDay), day: nextDay, current: false });
    nextDay++;
  }

  const goPrev = () => onMonthChange(month === 0 ? year - 1 : year, month === 0 ? 11 : month - 1);
  const goNext = () => onMonthChange(month === 11 ? year + 1 : year, month === 11 ? 0 : month + 1);

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <button onClick={goPrev}
          className="p-1.5 rounded-lg hover:bg-ag-card-hover text-ag-text-muted hover:text-ag-text">
          <ChevronLeft size={18} />
        </button>
        <h3 className="text-lg font-semibold">{monthName} {year}</h3>
        <button onClick={goNext}
          className="p-1.5 rounded-lg hover:bg-ag-card-hover text-ag-text-muted hover:text-ag-text">
          <ChevronRight size={18} />
        </button>
      </div>

      {/* Day-of-week headers */}
      <div className="grid grid-cols-7 gap-1 mb-1">
        {DAY_LABELS.map((d) => (
          <div key={d} className="text-center text-[10px] font-medium text-ag-text-muted uppercase py-1">
            {d}
          </div>
        ))}
      </div>

      {/* Day cells */}
      <div className="grid grid-cols-7 gap-1">
        {cells.map((cell) => {
          const isToday = cell.date === today;
          const isSelected = cell.date === selectedDate;
          return (
            <button key={cell.date} onClick={() => onDayClick(cell.date)}
              className={`group relative min-h-[56px] p-1 rounded-lg border text-left transition-colors flex flex-col ${
                !cell.current ? "opacity-30 border-transparent" :
                isSelected ? "border-ag-accent bg-ag-accent/10" :
                isToday ? "border-ag-accent/50 bg-ag-card" :
                "border-ag-border bg-ag-card hover:bg-ag-card-hover"
              }`}>
              <span className={`text-xs font-medium ${
                isToday ? "text-ag-accent" : cell.current ? "text-ag-text" : "text-ag-text-muted"
              }`}>
                {cell.day}
              </span>
              {cell.current && onAddTrade && (
                <span
                  onClick={(e) => { e.stopPropagation(); onAddTrade(cell.date); }}
                  className="absolute top-0.5 right-0.5 p-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity bg-ag-accent/20 text-ag-accent hover:bg-ag-accent/40 cursor-pointer"
                  title="Add trade on this day"
                >
                  <Plus size={10} />
                </span>
              )}
              {cell.current && (
                <div className="flex-1 mt-0.5 overflow-hidden">
                  {renderCell(cell.date)}
                </div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
