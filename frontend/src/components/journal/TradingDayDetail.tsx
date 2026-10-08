"use client";
import type { JournalTradeItem, StrategyItem } from "@/lib/api";
import TradeCard from "./TradeCard";

interface Props {
  date: string;
  trades: JournalTradeItem[];
  strategies: StrategyItem[];
  loading: boolean;
  onUpdated: () => void;
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}

export default function TradingDayDetail({ date, trades, strategies, loading, onUpdated }: Props) {
  const closed = trades.filter((t) => t.status === "closed");
  const totalPnl = closed.reduce((sum, t) => sum + (t.net_profit ?? 0), 0);
  const wins = closed.filter((t) => (t.net_profit ?? 0) > 0).length;
  const wr = closed.length > 0 ? (wins / closed.length) * 100 : 0;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">{formatDate(date)}</h3>
        {trades.length > 0 && (
          <div className="flex items-center gap-4 text-xs">
            <span className="text-ag-text-muted">{trades.length} trade{trades.length !== 1 ? "s" : ""}</span>
            <span className={totalPnl >= 0 ? "text-ag-green font-medium" : "text-ag-red font-medium"}>
              {totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}
            </span>
            {closed.length > 0 && (
              <span className={wr >= 50 ? "text-ag-green" : "text-ag-red"}>
                {wr.toFixed(0)}% WR
              </span>
            )}
          </div>
        )}
      </div>

      {loading ? (
        <p className="text-sm text-ag-text-muted py-4 text-center">Loading trades...</p>
      ) : trades.length === 0 ? (
        <p className="text-sm text-ag-text-muted py-4 text-center">No trades on this day.</p>
      ) : (
        <div className="space-y-2">
          {trades.map((t) => (
            <TradeCard key={t.id} trade={t} strategies={strategies} onUpdated={onUpdated} />
          ))}
        </div>
      )}
    </div>
  );
}
