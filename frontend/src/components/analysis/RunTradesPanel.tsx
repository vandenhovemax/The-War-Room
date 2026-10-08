"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { BacktestTradeItem } from "@/lib/api";
import TradeTable from "@/components/backtest/TradeTable";

interface Props {
  runId: number;
  symbol?: string;
  timeframe?: string;
  strategyId?: number;
}

export default function RunTradesPanel({ runId, symbol, timeframe, strategyId }: Props) {
  const [trades, setTrades] = useState<BacktestTradeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    api.backtestRunTrades(runId)
      .then(setTrades)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load trades"))
      .finally(() => setLoading(false));
  }, [runId]);

  if (loading) return <p className="text-sm text-ag-text-muted py-4">Loading trades...</p>;
  if (error) return <p className="text-sm text-ag-red py-4">{error}</p>;
  if (trades.length === 0) return <p className="text-sm text-ag-text-muted py-4">No trades found.</p>;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-6">
      <h3 className="text-lg font-semibold mb-4">Positions ({trades.length})</h3>
      <TradeTable trades={trades} symbol={symbol} timeframe={timeframe} strategyId={strategyId} />
    </div>
  );
}
