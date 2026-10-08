"use client";
import type { WFOWindowResult } from "@/lib/api";

interface Props {
  windows: WFOWindowResult[];
  selectedWindow: number | null;
  onSelectWindow: (num: number) => void;
}

function fmtDollarShort(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${v < 0 ? "-" : ""}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${v < 0 ? "-" : ""}$${(abs / 1_000).toFixed(1)}K`;
  return `${v < 0 ? "-" : ""}$${abs.toFixed(0)}`;
}

export default function WFOWindowTimeline({ windows, selectedWindow, onSelectWindow }: Props) {
  if (windows.length === 0) return null;

  // Count profitable OOS windows (exclude live windows from count)
  const oosWindows = windows.filter(w => w.oos_start && !w.is_live);
  const profitableCount = oosWindows.filter(w => {
    const pnl = w.candidates?.[0]?.oos_metrics?.net_profit ?? 0;
    return pnl > 0;
  }).length;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-xs font-semibold text-ag-text-muted uppercase">
          Window Timeline
        </h4>
        {oosWindows.length > 0 && (
          <span className="text-xs text-ag-text-muted">
            <span className={profitableCount / oosWindows.length >= 0.5 ? "text-ag-green font-medium" : "text-ag-red font-medium"}>
              {profitableCount}/{oosWindows.length}
            </span>
            {" "}OOS profitable ({(profitableCount / oosWindows.length * 100).toFixed(0)}%)
          </span>
        )}
      </div>
      <div className="flex gap-1 overflow-x-auto pb-2">
        {windows.map((w) => {
          const hasOOS = !!w.oos_start;
          const isSelected = selectedWindow === w.window_num;
          const isLive = !!w.is_live;
          const oosPnl = w.candidates?.[0]?.oos_metrics?.net_profit;
          const isProfit = oosPnl != null && oosPnl > 0;
          const isLoss = oosPnl != null && oosPnl <= 0;

          return (
            <button key={w.window_num} onClick={() => onSelectWindow(w.window_num)}
              className={`flex-shrink-0 rounded-lg border transition-all ${
                isSelected
                  ? isLive ? "border-ag-green ring-1 ring-ag-green/30" : "border-ag-accent ring-1 ring-ag-accent/30"
                  : isLive ? "border-ag-green/50 hover:border-ag-green" : "border-ag-border hover:border-ag-text-muted"
              }`}>
              <div className="flex h-10">
                {/* IS segment */}
                <div className={`px-3 flex items-center text-[10px] font-medium rounded-l-lg ${
                  isSelected ? "bg-blue-500/20 text-blue-400" : "bg-blue-500/10 text-blue-400/70"
                }`}>
                  IS
                </div>
                {/* OOS / Live segment */}
                {isLive ? (
                  <div className={`px-3 flex items-center gap-1 text-[10px] font-medium rounded-r-lg ${
                    isSelected ? "bg-ag-green/20 text-ag-green" : "bg-ag-green/10 text-ag-green/70"
                  }`}>
                    <span className="w-1.5 h-1.5 bg-ag-green rounded-full animate-pulse" />
                    LIVE
                  </div>
                ) : hasOOS ? (
                  <div className={`px-3 flex items-center text-[10px] font-medium rounded-r-lg ${
                    isSelected
                      ? (isProfit ? "bg-ag-green/20 text-ag-green" : isLoss ? "bg-ag-red/20 text-ag-red" : "bg-amber-500/20 text-amber-400")
                      : (isProfit ? "bg-ag-green/10 text-ag-green/70" : isLoss ? "bg-ag-red/10 text-ag-red/70" : "bg-amber-500/10 text-amber-400/70")
                  }`}>
                    OOS
                  </div>
                ) : (
                  <div className={`px-2 flex items-center text-[10px] font-medium rounded-r-lg ${
                    isSelected ? "bg-blue-500/15 text-blue-300" : "bg-ag-dark text-ag-text-muted"
                  }`}>
                    Final
                  </div>
                )}
              </div>
              <div className="px-2 py-1 text-center">
                <div className="text-[10px] text-ag-text-muted">
                  {isLive ? "NOW" : `W${w.window_num}`}
                </div>
                {isLive ? (
                  <div className="text-[9px] text-ag-green/80 font-medium">
                    {w.candidates?.[0]?.n_assets ?? 0} assets
                  </div>
                ) : hasOOS && oosPnl != null ? (
                  <div className={`text-[9px] font-mono ${isProfit ? "text-ag-green/80" : "text-ag-red/80"}`}>
                    {fmtDollarShort(oosPnl)}
                  </div>
                ) : (
                  <div className="text-[9px] text-ag-text-muted/60">
                    {w.is_start.slice(0, 7)}
                  </div>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
