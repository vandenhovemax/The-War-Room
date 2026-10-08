"use client";
import { useState } from "react";
import type { DataStatusItem } from "@/lib/api";

type Mode = "watchlist" | "all" | "manual";

interface SymbolMultiSelectProps {
  dataStatus: DataStatusItem[];
  watchlist: string[];
  selected: string[];
  onChange: (symbols: string[]) => void;
  minAdv?: number;
  maxAdv?: number;
}

function formatAdv(adv: number): string {
  if (adv >= 1e9) return `${(adv / 1e9).toFixed(1)}B`;
  if (adv >= 1e6) return `${(adv / 1e6).toFixed(1)}M`;
  if (adv >= 1e3) return `${(adv / 1e3).toFixed(0)}K`;
  return adv.toFixed(0);
}

export default function SymbolMultiSelect({
  dataStatus, watchlist, selected, onChange, minAdv = 0, maxAdv = 0,
}: SymbolMultiSelectProps) {
  const [mode, setMode] = useState<Mode>("watchlist");
  const [search, setSearch] = useState("");

  // Deduplicate symbols (dataStatus has one entry per symbol+timeframe combo)
  const allSymbols = [...new Set(dataStatus.map((d) => d.symbol))];
  // Use the highest ADV across all timeframes for each symbol
  const advMap: Record<string, number> = {};
  for (const d of dataStatus) {
    advMap[d.symbol] = Math.max(advMap[d.symbol] ?? 0, d.avg_daily_volume);
  }

  const visibleSymbols = (() => {
    let base = mode === "watchlist" ? watchlist.filter((s) => allSymbols.includes(s))
      : mode === "all" ? allSymbols
      : allSymbols;
    if (search) {
      const q = search.toUpperCase();
      base = base.filter((s) => s.includes(q));
    }
    // Filter by minimum ADV when set
    if (minAdv > 0) {
      base = base.filter((s) => (advMap[s] ?? 0) >= minAdv);
    }
    // Filter by maximum ADV when set
    if (maxAdv > 0) {
      base = base.filter((s) => (advMap[s] ?? Infinity) <= maxAdv);
    }
    return base;
  })();

  const toggle = (sym: string) => {
    onChange(selected.includes(sym) ? selected.filter((s) => s !== sym) : [...selected, sym]);
  };

  const selectAll = () => {
    const merged = new Set([...selected, ...visibleSymbols]);
    onChange(Array.from(merged));
  };

  const deselectAll = () => {
    const visible = new Set(visibleSymbols);
    onChange(selected.filter((s) => !visible.has(s)));
  };

  return (
    <div className="space-y-3">
      {/* Mode tabs */}
      <div className="flex gap-1 bg-ag-darker rounded-lg p-1">
        {([["watchlist", "Watchlist"], ["all", "All Downloaded"], ["manual", "Manual"]] as [Mode, string][]).map(
          ([m, label]) => (
            <button key={m} onClick={() => setMode(m)}
              className={`flex-1 px-3 py-1.5 text-xs rounded-md transition-colors ${
                mode === m ? "bg-ag-accent/15 text-ag-accent" : "text-ag-text-muted hover:text-ag-text"
              }`}
            >{label}</button>
          ),
        )}
      </div>

      {/* Search + actions */}
      <div className="flex gap-2 items-center">
        <input
          type="text" placeholder="Filter symbols..."
          value={search} onChange={(e) => setSearch(e.target.value)}
          className="flex-1 px-3 py-1.5 bg-ag-darker border border-ag-border rounded text-sm"
        />
        <button onClick={selectAll} className="text-xs text-ag-accent hover:underline">All</button>
        <button onClick={deselectAll} className="text-xs text-ag-text-muted hover:underline">None</button>
        <span className="text-xs text-ag-accent font-medium">{selected.length} selected</span>
        {(minAdv > 0 || maxAdv > 0) && (
          <span className="text-[10px] text-ag-text-muted">
            ({visibleSymbols.length} in ADV range{minAdv > 0 ? ` ≥${formatAdv(minAdv)}` : ""}{maxAdv > 0 ? ` ≤${formatAdv(maxAdv)}` : ""})
          </span>
        )}
      </div>

      {/* Symbol grid */}
      <div className="max-h-48 overflow-y-auto grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-1">
        {visibleSymbols.map((sym) => (
          <label key={sym}
            className={`flex items-center gap-2 px-2 py-1.5 rounded text-xs cursor-pointer transition-colors ${
              selected.includes(sym) ? "bg-ag-accent/10 text-ag-text" : "text-ag-text-muted hover:bg-ag-card-hover"
            }`}
          >
            <input type="checkbox" checked={selected.includes(sym)}
              onChange={() => toggle(sym)} className="rounded border-ag-border"
            />
            <span className="truncate">{sym}</span>
            {advMap[sym] > 0 && (
              <span className="ml-auto text-[10px] text-ag-text-muted">{formatAdv(advMap[sym])}</span>
            )}
          </label>
        ))}
      </div>
      {visibleSymbols.length === 0 && (
        <p className="text-xs text-ag-text-muted text-center py-4">
          {mode === "watchlist" ? "Watchlist is empty or no data downloaded" : "No symbols match filter"}
        </p>
      )}
    </div>
  );
}
