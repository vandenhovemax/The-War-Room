"use client";
import { useState, useEffect } from "react";
import { ArrowLeft } from "lucide-react";
import { api } from "@/lib/api";
import type { JournalTradeItem, StrategyItem, DashboardSymbolStats } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import JournalTradeRow from "./JournalTradeRow";

interface Props {
  symbol: string;
  symbolStats: DashboardSymbolStats;
  strategies: StrategyItem[];
  onBack: () => void;
}

export default function TradingAssetDetail({ symbol, symbolStats, strategies, onBack }: Props) {
  const [trades, setTrades] = useState<JournalTradeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterStrategy, setFilterStrategy] = useState<number | null>(null);

  useEffect(() => {
    setLoading(true);
    api.journalList({ symbol, strategy_id: filterStrategy ?? undefined, limit: 500 })
      .then(setTrades)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [symbol, filterStrategy]);

  // Compute KPIs from the loaded trades
  const closed = trades.filter((t) => t.status === "closed");
  const totalPnl = closed.reduce((s, t) => s + (t.net_profit ?? 0), 0);
  const wins = closed.filter((t) => (t.net_profit ?? 0) > 0).length;
  const winRate = closed.length > 0 ? (wins / closed.length) * 100 : 0;
  const totalExpectedFees = trades.reduce((s, t) => s + (t.expected_commission ?? 0), 0);
  const totalActualFees = trades.reduce((s, t) => s + (t.actual_commission ?? 0), 0);
  const feeDelta = totalActualFees - totalExpectedFees;
  const avgPnl = closed.length > 0 ? totalPnl / closed.length : 0;
  const bestTrade = closed.reduce<JournalTradeItem | null>((best, t) => {
    if (!best || (t.net_profit ?? 0) > (best.net_profit ?? 0)) return t;
    return best;
  }, null);
  const worstTrade = closed.reduce<JournalTradeItem | null>((worst, t) => {
    if (!worst || (t.net_profit ?? 0) < (worst.net_profit ?? 0)) return t;
    return worst;
  }, null);
  const totalRisk = trades.reduce((s, t) => s + (t.risk_amount ?? 0), 0);
  const avgRisk = trades.length > 0 && totalRisk > 0 ? totalRisk / trades.length : 0;

  // Unique strategies from trades
  const tradeStrategyIds = [...new Set(trades.map((t) => t.strategy_id).filter(Boolean))];

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button onClick={onBack}
          className="flex items-center gap-1.5 text-sm text-ag-text-muted hover:text-ag-text transition-colors px-2 py-1 rounded hover:bg-ag-dark/50">
          <ArrowLeft size={16} /> Back to Dashboard
        </button>
      </div>

      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">{symbol.replace("USDT", "")} / USDT</h2>
          <p className="text-ag-text-muted text-sm mt-0.5">
            {trades.length} total trades ({closed.length} closed)
          </p>
        </div>

        {/* Strategy filter */}
        <select value={filterStrategy ?? ""}
          onChange={(e) => setFilterStrategy(Number(e.target.value) || null)}
          className="bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
          <option value="">All Strategies</option>
          {strategies
            .filter((s) => tradeStrategyIds.includes(s.id) || filterStrategy === null)
            .map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Kpi label="Total P&L" value={`$${totalPnl.toFixed(2)}`}
          color={totalPnl >= 0 ? "text-ag-green" : "text-ag-red"} />
        <Kpi label="Win Rate" value={`${winRate.toFixed(1)}%`}
          color={winRate >= 50 ? "text-ag-green" : "text-ag-red"}
          sub={`${wins}W / ${closed.length - wins}L`} />
        <Kpi label="Avg Trade" value={`$${avgPnl.toFixed(2)}`}
          color={avgPnl >= 0 ? "text-ag-green" : "text-ag-red"} />
        <Kpi label="Actual Fees" value={`$${totalActualFees.toFixed(2)}`}
          sub={feeDelta !== 0 ? `Delta: $${feeDelta >= 0 ? "+" : ""}${feeDelta.toFixed(2)}` : undefined} />
      </div>

      {/* Second row of KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Kpi label="Best Trade" value={bestTrade ? `$${(bestTrade.net_profit ?? 0).toFixed(2)}` : "—"}
          color="text-ag-green"
          sub={bestTrade?.entry_time ? new Date(bestTrade.entry_time).toLocaleDateString() : undefined} />
        <Kpi label="Worst Trade" value={worstTrade ? `$${(worstTrade.net_profit ?? 0).toFixed(2)}` : "—"}
          color="text-ag-red"
          sub={worstTrade?.entry_time ? new Date(worstTrade.entry_time).toLocaleDateString() : undefined} />
        <Kpi label="Avg Risk / Trade" value={avgRisk > 0 ? `$${avgRisk.toFixed(2)}` : "—"}
          sub={totalRisk > 0 ? `Total: $${totalRisk.toFixed(2)}` : undefined} />
        <Kpi label="Expected Fees" value={`$${totalExpectedFees.toFixed(2)}`}
          sub={`Rate: ${trades[0]?.commission_rate ?? 0.055}%`} />
      </div>

      {/* Trade list */}
      {loading ? (
        <p className="text-sm text-ag-text-muted py-8 text-center">Loading trades...</p>
      ) : (
        <JournalTradeRow trades={trades} strategies={strategies} />
      )}
    </div>
  );
}

function Kpi({ label, value, color, sub }: {
  label: string; value: string; color?: string; sub?: string;
}) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-3">
      <p className="text-[10px] text-ag-text-muted uppercase tracking-wider">{label}</p>
      <p className={`text-lg font-bold mt-0.5 ${color || ""}`}>{value}</p>
      {sub && <p className="text-[10px] text-ag-text-muted mt-0.5">{sub}</p>}
    </div>
  );
}
