"use client";

import { useState } from "react";
import type { DashboardAnalysisRequest } from "@/lib/api";
import BatchOverviewCards from "./BatchOverviewCards";
import LSBreakdownTable from "./LSBreakdownTable";
import EquityCurveChart from "@/components/tv-analysis/EquityCurveChart";
import MonthlyMatrix from "@/components/tv-analysis/MonthlyMatrix";
import FullAnalysisPanel from "@/components/tv-analysis/FullAnalysisPanel";
import PortfolioFullAnalysisPanel from "./PortfolioFullAnalysisPanel";
import HealthPanel from "./HealthPanel";
import OutlierTradesPanel from "./OutlierTradesPanel";
import BTCContextOverlay from "./BTCContextOverlay";
import AggregatedPnLChart from "./AggregatedPnLChart";

interface Props {
  batchId?: number;
  portfolioId?: number;
  dateFrom?: string;
  dateTo?: string;
  minTrades?: number;
  excludeTopN?: number;
  pfMin?: number;
  pfMax?: number;
  wrMin?: number;
  timeframe?: string;
  strategyId?: number;
}

const SUB_TABS = [
  { id: "overview", label: "Overview" },
  { id: "ls", label: "All Positions" },
  { id: "full", label: "Full Analysis" },
  { id: "equity", label: "Equity Curve" },
  { id: "btc", label: "Equity vs BTC" },
  { id: "monthly", label: "Monthly" },
  { id: "period-pnl", label: "Period PnL" },
  { id: "health", label: "Health" },
] as const;

type SubTabId = (typeof SUB_TABS)[number]["id"];

export default function BatchDashboardPanel({ batchId, portfolioId, dateFrom, dateTo, minTrades, excludeTopN, pfMin, pfMax, wrMin, timeframe, strategyId }: Props) {
  const [subTab, setSubTab] = useState<SubTabId>("overview");

  const req: DashboardAnalysisRequest = {
    ...(portfolioId ? { portfolio_id: portfolioId } : { batch_id: batchId }),
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    min_trades: minTrades || undefined,
    exclude_top_n: excludeTopN || undefined,
    pf_min: pfMin,
    pf_max: pfMax,
    wr_min: wrMin,
  };

  return (
    <div className="space-y-4">
      {/* Sub-tab navigation */}
      <div className="flex gap-1 border-b border-ag-border/50">
        {SUB_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setSubTab(t.id)}
            className={`px-3 py-1.5 text-xs transition-colors border-b-2 -mb-px ${
              subTab === t.id
                ? "border-ag-accent text-ag-accent"
                : "border-transparent text-ag-text-muted hover:text-ag-text"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Sub-tab content */}
      {subTab === "overview" && <BatchOverviewCards req={req} />}
      {subTab === "ls" && <LSBreakdownTable req={req} timeframe={timeframe} strategyId={strategyId} />}
      {subTab === "full" && (
        portfolioId
          ? <PortfolioFullAnalysisPanel portfolioId={portfolioId} />
          : <FullAnalysisPanel req={req} />
      )}
      {subTab === "equity" && <EquityCurveChart req={req} />}
      {subTab === "btc" && <BTCContextOverlay req={req} />}
      {subTab === "monthly" && <MonthlyMatrix req={req} />}
      {subTab === "period-pnl" && <AggregatedPnLChart req={req} />}
      {subTab === "health" && (
        <div className="space-y-4">
          <HealthPanel req={req} />
          <OutlierTradesPanel req={req} timeframe={timeframe} strategyId={strategyId} />
        </div>
      )}
    </div>
  );
}
