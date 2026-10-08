"use client";
import { useCallback } from "react";
import { BarChart3, Trophy, Flame, ArrowUpRight, ArrowDownRight } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";

interface Props {
  portfolioId?: number;
}

export default function PerformanceBreakdown({ portfolioId }: Props) {
  const { data } = useApi(
    useCallback(() => api.performanceBreakdown(portfolioId), [portfolioId]),
    `perf-breakdown-${portfolioId ?? "all"}`,
  );

  if (!data || (data.long_trades === 0 && data.short_trades === 0)) return null;

  const totalPnl = data.long_pnl + data.short_pnl;
  const longPct = totalPnl !== 0 ? Math.abs(data.long_pnl / (Math.abs(data.long_pnl) + Math.abs(data.short_pnl))) * 100 : 50;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
      <div className="flex items-center gap-2">
        <BarChart3 size={16} className="text-ag-accent" />
        <h3 className="text-sm font-semibold">Performance Breakdown</h3>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Long vs Short */}
        <div className="space-y-3">
          <p className="text-[10px] text-ag-text-muted uppercase tracking-wider">Long vs Short</p>
          {/* Visual bar */}
          <div className="flex h-3 rounded-full overflow-hidden bg-ag-dark/40">
            <div className="bg-ag-green/60 transition-all" style={{ width: `${longPct}%` }} />
            <div className="bg-ag-red/60 transition-all" style={{ width: `${100 - longPct}%` }} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="p-2 bg-ag-dark/40 rounded-lg">
              <div className="flex items-center gap-1 mb-1">
                <ArrowUpRight size={12} className="text-ag-green" />
                <span className="text-[10px] text-ag-text-muted">Long</span>
              </div>
              <p className={`text-sm font-bold font-mono ${data.long_pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                {data.long_pnl >= 0 ? "+" : ""}${data.long_pnl.toFixed(0)}
              </p>
              <p className="text-[10px] text-ag-text-muted">
                {data.long_trades} trades &middot; {data.long_win_rate.toFixed(0)}% WR
              </p>
            </div>
            <div className="p-2 bg-ag-dark/40 rounded-lg">
              <div className="flex items-center gap-1 mb-1">
                <ArrowDownRight size={12} className="text-ag-red" />
                <span className="text-[10px] text-ag-text-muted">Short</span>
              </div>
              <p className={`text-sm font-bold font-mono ${data.short_pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                {data.short_pnl >= 0 ? "+" : ""}${data.short_pnl.toFixed(0)}
              </p>
              <p className="text-[10px] text-ag-text-muted">
                {data.short_trades} trades &middot; {data.short_win_rate.toFixed(0)}% WR
              </p>
            </div>
          </div>
        </div>

        {/* Best / Worst Trade */}
        <div className="space-y-3">
          <p className="text-[10px] text-ag-text-muted uppercase tracking-wider">Notable Trades</p>
          {data.best_trade && (
            <div className="p-2.5 bg-ag-green/5 border border-ag-green/20 rounded-lg">
              <div className="flex items-center gap-1.5 mb-1">
                <Trophy size={12} className="text-ag-green" />
                <span className="text-[10px] text-ag-text-muted">Best Trade</span>
              </div>
              <p className="text-sm font-bold text-ag-green font-mono">
                +${data.best_trade.pnl.toFixed(2)}
              </p>
              <p className="text-[10px] text-ag-text-muted">
                {data.best_trade.symbol.replace("USDT", "")} {data.best_trade.side}
                {data.best_trade.date && ` · ${new Date(data.best_trade.date).toLocaleDateString("en-US", { month: "short", day: "numeric" })}`}
              </p>
            </div>
          )}
          {data.worst_trade && (
            <div className="p-2.5 bg-ag-red/5 border border-ag-red/20 rounded-lg">
              <div className="flex items-center gap-1.5 mb-1">
                <Flame size={12} className="text-ag-red" />
                <span className="text-[10px] text-ag-text-muted">Worst Trade</span>
              </div>
              <p className="text-sm font-bold text-ag-red font-mono">
                ${data.worst_trade.pnl.toFixed(2)}
              </p>
              <p className="text-[10px] text-ag-text-muted">
                {data.worst_trade.symbol.replace("USDT", "")} {data.worst_trade.side}
                {data.worst_trade.date && ` · ${new Date(data.worst_trade.date).toLocaleDateString("en-US", { month: "short", day: "numeric" })}`}
              </p>
            </div>
          )}
        </div>

        {/* Streaks */}
        <div className="space-y-3">
          <p className="text-[10px] text-ag-text-muted uppercase tracking-wider">Streaks</p>
          <div className="space-y-2">
            <div className="flex items-center justify-between p-2.5 bg-ag-dark/40 rounded-lg">
              <span className="text-xs text-ag-text-muted">Current Streak</span>
              <span className={`text-sm font-bold font-mono ${
                data.current_streak > 0 ? "text-ag-green" : data.current_streak < 0 ? "text-ag-red" : "text-ag-text-muted"
              }`}>
                {data.current_streak > 0 ? `${data.current_streak}W` : data.current_streak < 0 ? `${Math.abs(data.current_streak)}L` : "—"}
              </span>
            </div>
            <div className="flex items-center justify-between p-2.5 bg-ag-dark/40 rounded-lg">
              <span className="text-xs text-ag-text-muted">Best Win Streak</span>
              <span className="text-sm font-bold font-mono text-ag-green">{data.max_win_streak}</span>
            </div>
            <div className="flex items-center justify-between p-2.5 bg-ag-dark/40 rounded-lg">
              <span className="text-xs text-ag-text-muted">Worst Loss Streak</span>
              <span className="text-sm font-bold font-mono text-ag-red">{data.max_loss_streak}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
