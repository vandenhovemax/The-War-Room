"use client";

import type { ClippedTradeDetail } from "@/lib/api";

interface ClippedTradesMessageProps {
  clippedTrades: ClippedTradeDetail[];
  boundaryDate?: string | null;
}

/**
 * Displays a summary of trades that were force-closed at the OOS end boundary.
 * Shows count, per-trade details (asset, clipped PnL, original PnL delta).
 */
export function ClippedTradesMessage({ clippedTrades, boundaryDate }: ClippedTradesMessageProps) {
  if (!clippedTrades || clippedTrades.length === 0) return null;

  const totalClippedPnl = clippedTrades.reduce((s, t) => s + t.clipped_pnl, 0);
  const totalOriginalPnl = clippedTrades.reduce((s, t) => s + t.original_pnl, 0);
  const delta = totalOriginalPnl - totalClippedPnl;

  return (
    <div
      className="mt-2 p-2 rounded border border-yellow-500/30 bg-yellow-500/5 text-xs space-y-1"
      title="Trades that straddled the OOS end boundary were force-closed at that date's close price. The clipped PnL is the official OOS number."
    >
      <div className="flex items-center gap-1.5 text-yellow-400 font-medium">
        <span>&#9888;</span>
        <span>
          {clippedTrades.length} trade{clippedTrades.length > 1 ? "s" : ""} clipped
          {boundaryDate ? ` at ${boundaryDate}` : ""} close
        </span>
      </div>
      <div className="space-y-0.5 text-ag-text-muted pl-4">
        {clippedTrades.map((t, i) => {
          const d = t.original_pnl - t.clipped_pnl;
          const dSign = d >= 0 ? "+" : "";
          return (
            <div key={i} className="flex gap-2 flex-wrap">
              <span className="font-mono">{t.asset}</span>
              <span>clipped: ${t.clipped_pnl.toFixed(2)}</span>
              <span className="text-ag-text-muted">
                (original: ${t.original_pnl.toFixed(2)} — {dSign}${d.toFixed(2)})
              </span>
            </div>
          );
        })}
      </div>
      {clippedTrades.length > 1 && (
        <div className="pl-4 text-ag-text-muted border-t border-ag-border pt-1 mt-1">
          Total clipped: ${totalClippedPnl.toFixed(2)}
          {delta !== 0 && (
            <span className="text-ag-text-muted">
              {" "}(original total: ${totalOriginalPnl.toFixed(2)} — {delta >= 0 ? "+" : ""}${delta.toFixed(2)})
            </span>
          )}
        </div>
      )}
    </div>
  );
}
