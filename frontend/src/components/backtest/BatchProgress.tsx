"use client";
import { useState, useEffect } from "react";
import { Square } from "lucide-react";
import type { BatchProgress as BatchProgressType } from "@/lib/api";

interface BatchProgressProps {
  progress: BatchProgressType | null;
  startedAt?: number;
  onCancel?: () => void;
}

function formatEta(ms: number): string {
  if (ms <= 0) return "";
  const totalSec = Math.ceil(ms / 1000);
  if (totalSec < 60) return `~${totalSec}s remaining`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `~${min}m ${sec}s remaining`;
}

export default function BatchProgress({ progress, startedAt, onCancel }: BatchProgressProps) {
  const [now, setNow] = useState(Date.now());

  // Tick every second for ETA updates
  useEffect(() => {
    if (!progress || progress.status !== "running") return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [progress?.status]);

  if (!progress) return null;

  const { total_symbols, completed_symbols, current_symbol, symbol_statuses, status } = progress;
  const pct = total_symbols > 0 ? (completed_symbols / total_symbols) * 100 : 0;
  const symbols = Object.entries(symbol_statuses || {});

  // ETA calculation
  let etaStr = "";
  if (startedAt && status === "running" && completed_symbols > 0 && completed_symbols < total_symbols) {
    const elapsed = now - startedAt;
    const msPerSymbol = elapsed / completed_symbols;
    const remaining = (total_symbols - completed_symbols) * msPerSymbol;
    etaStr = formatEta(remaining);
  }

  return (
    <div className="space-y-3">
      {/* Progress bar + stop button */}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-3 bg-ag-darker rounded-full overflow-hidden">
          <div
            className="h-full bg-ag-accent rounded-full transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="text-sm text-ag-text font-medium whitespace-nowrap tabular-nums">
          {completed_symbols} / {total_symbols}
        </span>
        {(status === "running" || status === "queued") && onCancel && (
          <button
            onClick={onCancel}
            className="p-1.5 rounded-lg bg-ag-red/15 text-ag-red hover:bg-ag-red/25 transition-colors"
            title={status === "queued" ? "Remove from queue" : "Stop batch"}
          >
            <Square size={12} fill="currentColor" />
          </button>
        )}
      </div>

      {/* Queued status */}
      {status === "queued" && (
        <p className="text-sm text-ag-accent font-medium">
          Queued — waiting for previous batch to finish
          {progress.queue_position ? ` (position ${progress.queue_position})` : ""}
        </p>
      )}

      {/* Current symbol + ETA */}
      {status === "running" && current_symbol && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-ag-text-muted">
            Processing <span className="text-ag-accent font-medium">{current_symbol}</span>...
            ({completed_symbols + 1} of {total_symbols})
          </p>
          {etaStr && (
            <span className="text-xs text-ag-text-muted tabular-nums">{etaStr}</span>
          )}
        </div>
      )}
      {status === "completed" && (
        <p className="text-sm text-ag-green font-medium">Batch complete!</p>
      )}
      {status === "cancelled" && (
        <p className="text-sm text-ag-yellow font-medium">Batch cancelled</p>
      )}

      {/* Size-filtered banner */}
      {(progress.size_filtered_count ?? 0) > 0 && (
        <div className="flex items-center gap-2 p-2 bg-cyan-500/10 border border-cyan-500/30 rounded-lg text-cyan-400 text-xs">
          {progress.size_filtered_count} symbol(s) excluded — position size at expected live risk is below Bybit minimum
        </div>
      )}

      {/* Margin-filtered banner */}
      {(progress.margin_filtered_count ?? 0) > 0 && (
        <div className="flex items-center gap-2 p-2 bg-orange-500/10 border border-orange-500/30 rounded-lg text-orange-400 text-xs">
          {progress.margin_filtered_count} symbol(s) excluded — margin per trade exceeds limit
        </div>
      )}

      {/* Symbol status dots */}
      <div className="flex flex-wrap gap-1.5">
        {symbols.map(([sym, st]) => (
          <div key={sym} title={`${sym}: ${st}`}
            className={`w-2.5 h-2.5 rounded-full ${
              st === "completed" ? "bg-ag-green"
              : st === "running" ? "bg-amber-400 animate-pulse"
              : st === "error" ? "bg-ag-red"
              : st === "size_filtered" ? "bg-cyan-400"
              : st === "margin_filtered" ? "bg-orange-400"
              : "bg-ag-border"
            }`}
          />
        ))}
      </div>
    </div>
  );
}
