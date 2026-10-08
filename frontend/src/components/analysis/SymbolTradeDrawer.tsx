"use client";
import { useState, useEffect } from "react";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import type { BacktestTradeItem, CompareStrategy } from "@/lib/api";
import TradeTable from "@/components/backtest/TradeTable";

interface Props {
  symbolKey: string;
  batchIds: number[];
  strategies: CompareStrategy[];
  onClose: () => void;
  timeframe?: string;
  strategyId?: number;
}

export default function SymbolTradeDrawer({ symbolKey, batchIds, strategies, onClose, timeframe, strategyId }: Props) {
  const [batchTrades, setBatchTrades] = useState<Map<number, BacktestTradeItem[]>>(new Map());
  const [loading, setLoading] = useState(true);

  // Strip _Long/_Short suffix for API call, keep side for filtering
  const baseSymbol = symbolKey.replace(/_Long$/, "").replace(/_Short$/, "");
  const sideFilter = symbolKey.endsWith("_Long") ? "long" : symbolKey.endsWith("_Short") ? "short" : null;

  useEffect(() => {
    setLoading(true);
    setBatchTrades(new Map());

    Promise.all(
      batchIds.map((bid) =>
        api.dashboardBatchSymbolTrades(baseSymbol, { batch_id: bid })
          .then((trades) => [bid, trades] as const)
          .catch(() => [bid, [] as BacktestTradeItem[]] as const)
      )
    ).then((results) => {
      const map = new Map<number, BacktestTradeItem[]>();
      for (const [bid, trades] of results) {
        const filtered = sideFilter ? trades.filter((t) => t.side === sideFilter) : trades;
        map.set(bid, filtered);
      }
      setBatchTrades(map);
    }).finally(() => setLoading(false));
  }, [symbolKey, batchIds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  const getBatchLabel = (bid: number) => {
    const s = strategies.find((st) => st.batch_id === bid);
    return s ? `${s.strategy_name} #${s.batch_id}` : `Batch #${bid}`;
  };

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />

      {/* Drawer */}
      <div className="fixed top-0 right-0 h-full w-full max-w-3xl bg-ag-darker border-l border-ag-border z-50 flex flex-col shadow-2xl animate-in slide-in-from-right duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-ag-border shrink-0">
          <div>
            <h3 className="text-base font-semibold">{baseSymbol}</h3>
            <p className="text-xs text-ag-text-muted">{sideFilter ? sideFilter.charAt(0).toUpperCase() + sideFilter.slice(1) : "All"} trades across {batchIds.length} batches</p>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-ag-card text-ag-text-muted hover:text-ag-text">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {loading && <p className="text-sm text-ag-text-muted">Loading trades...</p>}

          {!loading && batchIds.map((bid) => {
            const trades = batchTrades.get(bid) ?? [];
            const totalPnl = trades.reduce((s, t) => s + t.net_profit, 0);
            const wins = trades.filter((t) => t.net_profit > 0).length;
            const wr = trades.length > 0 ? (wins / trades.length) * 100 : 0;

            return (
              <div key={bid} className="space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-medium">{getBatchLabel(bid)}</h4>
                  <div className="flex gap-4 text-xs text-ag-text-muted">
                    <span>{trades.length} trades</span>
                    <span className={totalPnl >= 0 ? "text-ag-green" : "text-ag-red"}>
                      ${totalPnl.toFixed(2)}
                    </span>
                    <span>{wr.toFixed(1)}% WR</span>
                  </div>
                </div>
                {trades.length > 0 ? (
                  <TradeTable trades={trades} symbol={baseSymbol} timeframe={timeframe} strategyId={strategyId} />
                ) : (
                  <p className="text-xs text-ag-text-muted py-2">No trades found.</p>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
