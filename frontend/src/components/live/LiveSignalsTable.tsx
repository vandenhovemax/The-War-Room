"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { LiveSignal } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  signals: LiveSignal[];
  onRefresh: () => void;
}

const statusColors: Record<string, string> = {
  new: "bg-blue-500/20 text-blue-400",
  seen: "bg-yellow-500/20 text-yellow-400",
  acted: "bg-green-500/20 text-green-400",
};

const FILTERS = ["all", "new", "seen", "acted"] as const;
type Filter = (typeof FILTERS)[number];

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return "-";
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = Math.max(0, now - then);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatNYT(dateStr: string | null): string {
  if (!dateStr) return "-";
  const d = new Date(dateStr);
  const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  // bar_timestamp is already NYT — display directly
  const month = d.toLocaleString("en-US", { month: "short" });
  const day = d.getDate();
  const dayName = dayNames[d.getDay()];
  const hours = d.getHours();
  const minutes = d.getMinutes().toString().padStart(2, "0");
  const ampm = hours >= 12 ? "PM" : "AM";
  const h12 = hours % 12 || 12;
  return `${dayName} ${month} ${day}, ${h12}:${minutes} ${ampm}`;
}

function chartUrl(symbol: string): string {
  const clean = symbol.replace("USDT", "");
  return `https://www.tradingview.com/chart/?symbol=BYBIT%3A${clean}USDT.P`;
}

export default function LiveSignalsTable({ signals, onRefresh }: Props) {
  const [filter, setFilter] = useState<Filter>("all");
  const [expanded, setExpanded] = useState<number | null>(null);

  const filtered = filter === "all"
    ? signals
    : signals.filter((s) => s.status === filter);

  const markStatus = async (id: number, status: string) => {
    try {
      await api.updateSignalStatus(id, status);
      onRefresh();
    } catch {
      // ignore
    }
  };

  if (signals.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-ag-text-muted text-sm">No signals yet</p>
        <p className="text-ag-text-muted text-xs mt-1">
          Signals appear when the scanner detects strategy entries/exits on saved (non-activated) portfolios
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Header with explanation */}
      <div className="flex items-center justify-between">
        <div className="flex gap-1">
          {FILTERS.map((f) => {
            const count = f === "all"
              ? signals.length
              : signals.filter((s) => s.status === f).length;
            return (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                  filter === f
                    ? "border-ag-accent bg-ag-accent/10 text-ag-accent"
                    : "border-ag-border text-ag-text-muted hover:text-ag-text"
                }`}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)} ({count})
              </button>
            );
          })}
        </div>
        <span className="text-[10px] text-ag-text-muted">
          Scanner alerts from saved portfolios (not live)
          <HintTip text="Signals are detected by the scanner running strategies on candle data for saved portfolios that are NOT activated for live/paper trading. They show where your strategy would have entered." />
        </span>
      </div>

      {/* Signal cards */}
      <div className="space-y-2">
        {filtered.map((s) => {
          const isExpanded = expanded === s.id;
          const windowBadge = s.in_window === null
            ? null
            : s.in_window
              ? <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400">IN window</span>
              : <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">OUT window</span>;

          return (
            <div
              key={s.id}
              className={`bg-ag-card border rounded-lg overflow-hidden transition-colors ${
                s.in_window === false
                  ? "border-red-500/30"
                  : s.in_window === true
                    ? "border-green-500/30"
                    : "border-ag-border"
              }`}
            >
              {/* Main row — always visible */}
              <div
                className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-ag-card-hover transition-colors"
                onClick={() => setExpanded(isExpanded ? null : s.id)}
              >
                {/* Direction + Action badge */}
                <div className="flex flex-col items-center gap-0.5 w-12">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                    s.action === "entry"
                      ? "bg-ag-accent/20 text-ag-accent"
                      : "bg-orange-500/20 text-orange-400"
                  }`}>
                    {s.action}
                  </span>
                  <span className={`text-xs font-medium ${
                    s.direction === "long" ? "text-ag-green" : "text-ag-red"
                  }`}>
                    {s.direction}
                  </span>
                </div>

                {/* Symbol + price */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-sm">
                      {s.symbol.replace("USDT", "")}
                    </span>
                    <span className="text-ag-text-muted text-xs">
                      @ {s.close_price ? s.close_price.toFixed(4) : "-"}
                    </span>
                    {s.timeframe && (
                      <span className="text-[10px] px-1 py-0.5 rounded bg-ag-border/50 text-ag-text-muted">
                        {s.timeframe}m
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-ag-text-muted truncate">
                    {s.portfolio_name}
                    {s.batch_name && <span className="mx-1">&middot;</span>}
                    {s.batch_name && <span className="text-ag-text-muted/70">{s.batch_name}</span>}
                  </div>
                </div>

                {/* Time (NYT) + age */}
                <div className="text-right min-w-[130px]">
                  <div className="text-xs">{formatNYT(s.bar_timestamp)}</div>
                  <div className="text-[10px] text-ag-text-muted">{timeAgo(s.bar_timestamp)} NYT</div>
                </div>

                {/* Window badge */}
                <div className="w-20 text-center">
                  {windowBadge}
                </div>

                {/* Status */}
                <div className="w-14 text-center">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${statusColors[s.status] || ""}`}>
                    {s.status}
                  </span>
                </div>

                {/* Actions */}
                <div className="flex gap-1 w-24 justify-end">
                  {s.status === "new" && (
                    <button
                      onClick={(e) => { e.stopPropagation(); markStatus(s.id, "seen"); }}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30"
                    >
                      Seen
                    </button>
                  )}
                  {(s.status === "new" || s.status === "seen") && (
                    <button
                      onClick={(e) => { e.stopPropagation(); markStatus(s.id, "acted"); }}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400 hover:bg-green-500/30"
                    >
                      Acted
                    </button>
                  )}
                </div>

                {/* Expand arrow */}
                <span className={`text-ag-text-muted text-xs transition-transform ${isExpanded ? "rotate-180" : ""}`}>
                  ▼
                </span>
              </div>

              {/* Expanded detail panel */}
              {isExpanded && (
                <div className="border-t border-ag-border bg-ag-bg/50 px-4 py-3 space-y-2">
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                    <div>
                      <span className="text-ag-text-muted block">Portfolio</span>
                      <span className="font-medium">{s.portfolio_name}</span>
                    </div>
                    <div>
                      <span className="text-ag-text-muted block">Strategy</span>
                      <span className="font-medium">{s.strategy_name || "-"}</span>
                    </div>
                    <div>
                      <span className="text-ag-text-muted block">Batch</span>
                      <span className="font-medium">{s.batch_name || "-"}</span>
                    </div>
                    <div>
                      <span className="text-ag-text-muted block">Timeframe</span>
                      <span className="font-medium">{s.timeframe ? `${s.timeframe}m` : "-"}</span>
                    </div>
                    <div>
                      <span className="text-ag-text-muted block">Entry ID</span>
                      <span className="font-medium">{s.entry_id || "-"}</span>
                    </div>
                    <div>
                      <span className="text-ag-text-muted block">SL / TP / Trail</span>
                      <span className="font-medium">
                        {s.sl_price ? s.sl_price.toFixed(4) : "-"} / {s.tp_price ? s.tp_price.toFixed(4) : "-"} / {s.trail_points ? s.trail_points.toFixed(2) : "-"}
                      </span>
                    </div>
                    <div>
                      <span className="text-ag-text-muted block">Execution Window</span>
                      {s.in_window === null
                        ? <span className="font-medium text-ag-text-muted">No filter</span>
                        : s.in_window
                          ? <span className="font-medium text-green-400">Inside window</span>
                          : <span className="font-medium text-red-400">Outside window</span>
                      }
                    </div>
                    <div>
                      <span className="text-ag-text-muted block">Trade Outcome</span>
                      {s.trade_pnl !== null ? (
                        <span className={`font-medium ${s.trade_pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                          {s.trade_pnl >= 0 ? "+" : ""}{s.trade_pnl.toFixed(2)} ({s.trade_status})
                        </span>
                      ) : (
                        <span className="font-medium text-ag-text-muted">No trade</span>
                      )}
                    </div>
                  </div>

                  {/* Comment */}
                  {s.comment && (
                    <div className="text-xs">
                      <span className="text-ag-text-muted">Comment: </span>
                      <span>{s.comment}</span>
                    </div>
                  )}

                  {/* Chart link */}
                  <div className="flex gap-2 pt-1">
                    <a
                      href={chartUrl(s.symbol)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[11px] px-2 py-1 rounded bg-ag-accent/10 text-ag-accent hover:bg-ag-accent/20 transition-colors"
                    >
                      Open on TradingView
                    </a>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
