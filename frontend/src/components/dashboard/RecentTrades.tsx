"use client";
import { useCallback } from "react";
import Link from "next/link";
import { BookOpen, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import type { JournalTradeItem } from "@/lib/api";
import { useApi } from "@/hooks/useApi";

function timeAgo(ts: string | null): string {
  if (!ts) return "";
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export default function RecentTrades() {
  const { data: trades } = useApi(
    useCallback(() => api.journalList({ status: "closed", limit: 12 }), []),
    "recent-trades-dashboard",
  );

  const recentTrades: JournalTradeItem[] = trades ?? [];

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BookOpen size={16} className="text-ag-accent" />
          <h3 className="text-sm font-semibold">Recent Trades</h3>
          <span className="text-[10px] text-ag-text-muted">Last {recentTrades.length}</span>
        </div>
        <Link href="/journal?tab=trading" className="text-xs text-ag-accent hover:underline flex items-center gap-1">
          Full Journal <ArrowRight size={12} />
        </Link>
      </div>

      {recentTrades.length === 0 ? (
        <p className="text-xs text-ag-text-muted text-center py-6">
          No closed trades yet. Log trades in the{" "}
          <Link href="/journal?tab=trading" className="text-ag-accent hover:underline">Journal</Link>.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[10px] text-ag-text-muted uppercase tracking-wider border-b border-ag-border/30">
                <th className="py-2 px-2 text-left">Symbol</th>
                <th className="py-2 px-2 text-left">Side</th>
                <th className="py-2 px-2 text-right">Entry</th>
                <th className="py-2 px-2 text-right">Exit</th>
                <th className="py-2 px-2 text-right">P&L</th>
                <th className="py-2 px-2 text-left">Source</th>
                <th className="py-2 px-2 text-right">When</th>
              </tr>
            </thead>
            <tbody>
              {recentTrades.map((t) => (
                <tr key={t.id} className="border-b border-ag-border/10 hover:bg-ag-dark/30 transition-colors">
                  <td className="py-2 px-2 font-medium">
                    {t.symbol.replace("USDT", "")}
                  </td>
                  <td className="py-2 px-2">
                    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                      t.side === "Long" ? "bg-ag-green/15 text-ag-green" : "bg-ag-red/15 text-ag-red"
                    }`}>
                      {t.side === "Long" ? "LONG" : "SHORT"}
                    </span>
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-ag-text-muted">
                    {t.entry_price != null ? `$${t.entry_price.toFixed(2)}` : "—"}
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-ag-text-muted">
                    {t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : "—"}
                  </td>
                  <td className={`py-2 px-2 text-right font-mono font-bold ${
                    t.net_profit != null ? (t.net_profit >= 0 ? "text-ag-green" : "text-ag-red") : "text-ag-text-muted"
                  }`}>
                    {t.net_profit != null
                      ? `${t.net_profit >= 0 ? "+" : ""}$${t.net_profit.toFixed(2)}`
                      : "—"}
                  </td>
                  <td className="py-2 px-2 text-ag-text-muted capitalize">
                    {t.source}
                  </td>
                  <td className="py-2 px-2 text-right text-ag-text-muted whitespace-nowrap">
                    {timeAgo(t.exit_time)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
