"use client";
import { useState, useEffect, useCallback } from "react";
import type { DashboardSymbolStats, StrategyItem, AccountSummary as AccountSummaryType } from "@/lib/api";
import { api } from "@/lib/api";
import { Zap, FileText } from "lucide-react";
import TradingAssetDetail from "@/components/trading/TradingAssetDetail";
import QuickStats from "@/components/dashboard/QuickStats";
import AccountEquityCurve from "@/components/dashboard/AccountEquityCurve";
import DailyPnLCalendar from "@/components/dashboard/DailyPnLCalendar";
import AccountKPIs from "@/components/dashboard/AccountKPIs";
import RecentTrades from "@/components/dashboard/RecentTrades";
import PerformanceBreakdown from "@/components/dashboard/PerformanceBreakdown";
import TradingAnalysis from "@/components/trading/TradingAnalysis";
import EngineTradeManager from "@/components/trading/EngineTradeManager";
import PortfolioPerformance from "@/components/dashboard/PortfolioPerformance";
import AccountSummaryPanel from "@/components/live/AccountSummaryPanel";

type TradingTab = "live" | "paper";

/* ── Bybit equity mini-card (only on Live tab) ── */

function BybitEquityCard() {
  const [account, setAccount] = useState<AccountSummaryType | null>(null);

  useEffect(() => {
    api.liveAccountSummary().then(setAccount).catch(() => {});
    const interval = setInterval(() => {
      api.liveAccountSummary().then(setAccount).catch(() => {});
    }, 15000);
    return () => clearInterval(interval);
  }, []);

  if (!account) return null;
  return <AccountSummaryPanel data={account} />;
}

/* ── Main trading content (shared between tabs) ── */

function TradingContent({
  portfolioId,
  setPortfolioId,
  onAssetSelect,
}: {
  portfolioId: number | undefined;
  setPortfolioId: (id: number | undefined) => void;
  onAssetSelect: (sym: string, stats: DashboardSymbolStats) => void;
}) {
  const [refreshKey, setRefreshKey] = useState(0);
  const triggerRefresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  return (
    <>
      {/* Hero Stats with portfolio selector */}
      <QuickStats key={`qs-${refreshKey}`} portfolioId={portfolioId} onPortfolioChange={setPortfolioId} />

      {/* Manage engine trades (mark test / delete) */}
      <EngineTradeManager onChanged={triggerRefresh} />

      {/* Equity Curve — full width, tall */}
      <AccountEquityCurve key={`ec-${refreshKey}`} portfolioId={portfolioId} />

      {/* Daily P&L Calendar + Account KPIs side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <DailyPnLCalendar portfolioId={portfolioId} />
        </div>
        <AccountKPIs key={`kpi-${refreshKey}`} />
      </div>

      {/* Recent Trades — full width table */}
      <RecentTrades key={`rt-${refreshKey}`} />

      {/* Performance Breakdown — L/S, streaks, best/worst */}
      <PerformanceBreakdown key={`pb-${refreshKey}`} portfolioId={portfolioId} />

      {/* Trading Analysis — filters, by-symbol, by-strategy, monthly, fees */}
      <TradingAnalysis onAssetSelect={onAssetSelect} />
    </>
  );
}

/* ── Page ── */

export default function TradingPage() {
  const [tab, setTab] = useState<TradingTab>("live");
  const [portfolioId, setPortfolioId] = useState<number | undefined>();

  // Asset detail drill-down
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [selectedStats, setSelectedStats] = useState<DashboardSymbolStats | null>(null);
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);

  useEffect(() => {
    api.strategies().then(setStrategies).catch(() => {});
  }, []);

  const handleAssetSelect = (sym: string, stats: DashboardSymbolStats) => {
    setSelectedSymbol(sym);
    setSelectedStats(stats);
  };

  // Asset detail view — replaces entire page when a symbol is clicked
  if (selectedSymbol && selectedStats) {
    return (
      <div className="space-y-6">
        <TradingAssetDetail
          symbol={selectedSymbol}
          symbolStats={selectedStats}
          strategies={strategies}
          onBack={() => { setSelectedSymbol(null); setSelectedStats(null); }}
        />
      </div>
    );
  }

  const tabs: { id: TradingTab; label: string; icon: typeof Zap }[] = [
    { id: "live", label: "Live Trading", icon: Zap },
    { id: "paper", label: "Paper Trading", icon: FileText },
  ];

  return (
    <div className="space-y-6">
      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b border-ag-border">
        {tabs.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => { setTab(t.id); setPortfolioId(undefined); }}
              className={`flex items-center gap-1.5 px-5 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
                tab === t.id
                  ? "border-ag-accent text-ag-accent"
                  : "border-transparent text-ag-text-muted hover:text-ag-text"
              }`}
            >
              <Icon size={15} />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {tab === "live" && (
        <div className="space-y-6">
          {/* Bybit equity — only on Live tab */}
          <BybitEquityCard />

          {/* Portfolio Performance — real portfolios only, OOS vs Live */}
          <PortfolioPerformance filterType="real" />

          {/* Trading data */}
          <TradingContent
            portfolioId={portfolioId}
            setPortfolioId={setPortfolioId}
            onAssetSelect={handleAssetSelect}
          />
        </div>
      )}

      {tab === "paper" && (
        <div className="space-y-6">
          {/* Portfolio Performance — paper portfolios only, OOS vs Paper */}
          <PortfolioPerformance filterType="paper" />

          {/* Trading data */}
          <TradingContent
            portfolioId={portfolioId}
            setPortfolioId={setPortfolioId}
            onAssetSelect={handleAssetSelect}
          />
        </div>
      )}
    </div>
  );
}
