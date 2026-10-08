"use client";
import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import type {
  LiveEngineStatus, LivePosition, LiveOrder, LiveTrade,
  LiveSignal, PortfolioItem, AccountSummary, PnLSummary,
} from "@/lib/api";
import EngineControls from "@/components/live/EngineControls";
import TradingModeBanner from "@/components/live/TradingModeBanner";
import AccountSummaryPanel from "@/components/live/AccountSummaryPanel";
import LivePnLSummary from "@/components/live/LivePnLSummary";
import LiveChartPanel from "@/components/live/LiveChartPanel";
import LivePositionsTable from "@/components/live/LivePositionsTable";
import LiveOrdersTable from "@/components/live/LiveOrdersTable";
import LiveTradesTable from "@/components/live/LiveTradesTable";
import LiveSignalsTable from "@/components/live/LiveSignalsTable";
import PortfolioActivator from "@/components/live/PortfolioActivator";

export default function LivePage() {
  const [status, setStatus] = useState<LiveEngineStatus | null>(null);
  const [positions, setPositions] = useState<LivePosition[]>([]);
  const [orders, setOrders] = useState<LiveOrder[]>([]);
  const [trades, setTrades] = useState<LiveTrade[]>([]);
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [account, setAccount] = useState<AccountSummary | null>(null);
  const [pnl, setPnl] = useState<PnLSummary | null>(null);
  const [signals, setSignals] = useState<LiveSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"positions" | "orders" | "trades" | "signals">("positions");

  const refresh = useCallback(async () => {
    try {
      const [s, p, o, t, pf, acct, pnlData, sig] = await Promise.all([
        api.liveStatus(),
        api.livePositions(),
        api.liveOrders("all", 50),
        api.liveTrades(50),
        api.portfolios(),
        api.liveAccountSummary().catch(() => null),
        api.livePnlSummary().catch(() => null),
        api.liveSignals({ limit: 100 }).catch(() => [] as LiveSignal[]),
      ]);
      setStatus(s);
      setPositions(p);
      setOrders(o);
      setTrades(t);
      setPortfolios(pf);
      setSignals(sig);
      if (acct) setAccount(acct);
      if (pnlData) setPnl(pnlData);
    } catch {
      // Backend might not be running
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <p className="text-ag-text-muted">Loading live engine status...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-7xl">
      <div>
        <h1 className="text-2xl font-bold">Live Trading Engine</h1>
        <p className="text-ag-text-muted text-sm mt-1">
          Automated strategy execution on Bybit
        </p>
      </div>

      {/* Trading mode banner */}
      <TradingModeBanner status={status} />

      {/* Engine status + controls */}
      <EngineControls status={status} onRefresh={refresh} />

      {/* Account summary (replaces old EquityPanel) */}
      {status?.running && account && <AccountSummaryPanel data={account} />}

      {/* Portfolio activator */}
      <PortfolioActivator
        portfolios={portfolios}
        activePortfolioIds={status?.active_portfolios ?? []}
        engineRunning={status?.running ?? false}
        onRefresh={refresh}
      />

      {/* Charts for active positions */}
      <LiveChartPanel positions={positions} />

      {/* PnL summary */}
      {pnl && <LivePnLSummary data={pnl} />}

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-ag-border">
        {(["positions", "orders", "trades", "signals"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t
                ? "border-ag-accent text-ag-accent"
                : "border-transparent text-ag-text-muted hover:text-ag-text"
            }`}
          >
            {t === "positions" ? `Positions (${positions.length})` :
             t === "orders" ? `Orders (${orders.length})` :
             t === "signals" ? `Signals (${signals.length})` :
             `Trades (${trades.length})`}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "positions" && (
        <LivePositionsTable positions={positions} onRefresh={refresh} />
      )}
      {tab === "orders" && <LiveOrdersTable orders={orders} />}
      {tab === "trades" && <LiveTradesTable trades={trades} timeframe="30" />}
      {tab === "signals" && <LiveSignalsTable signals={signals} onRefresh={refresh} />}
    </div>
  );
}
