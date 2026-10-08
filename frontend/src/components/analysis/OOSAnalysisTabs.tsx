"use client";
import { useState, lazy, Suspense } from "react";
import { Loader2 } from "lucide-react";
import type { DashboardAnalysisRequest } from "@/lib/api";

const BatchDashboardPanel = lazy(() => import("./BatchDashboardPanel"));
const PFHistogram = lazy(() => import("./PFHistogram"));
const RobustnessPanel = lazy(() => import("@/components/tv-analysis/RobustnessPanel"));
const MonteCarloTabContainer = lazy(() => import("./MonteCarloTabContainer"));
const CategoryBreakdownPanel = lazy(() => import("./CategoryBreakdownPanel"));

const OOS_TABS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "robustness", label: "Robustness" },
  { id: "montecarlo", label: "Monte Carlo" },
  { id: "categories", label: "Categories" },
] as const;

type OOSTabId = (typeof OOS_TABS)[number]["id"];

interface Props {
  portfolioId: number;
  cutoffDate: string;
  strategyId?: number;
}

export default function OOSAnalysisTabs({ portfolioId, cutoffDate, strategyId }: Props) {
  const [tab, setTab] = useState<OOSTabId>("dashboard");

  // The day AFTER cutoff is the start of OOS period
  // (cutoff date itself is inclusive for IS, so OOS starts the next day)
  const oosDateFrom = nextDay(cutoffDate);

  const oosReq: DashboardAnalysisRequest = {
    portfolio_id: portfolioId,
    date_from: oosDateFrom,
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] text-ag-text-muted mr-2 uppercase tracking-wider">OOS Deep Dive</span>
        {OOS_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
              tab === t.id
                ? "bg-ag-accent text-ag-darker"
                : "bg-ag-card border border-ag-border text-ag-text-muted hover:text-ag-text"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <Suspense fallback={<Loading />}>
        {tab === "dashboard" && (
          <BatchDashboardPanel portfolioId={portfolioId} dateFrom={oosDateFrom} strategyId={strategyId} />
        )}
        {tab === "robustness" && (
          <div className="space-y-4">
            <PFHistogram req={oosReq} />
            <RobustnessPanel req={oosReq} />
          </div>
        )}
        {tab === "montecarlo" && (
          <MonteCarloTabContainer portfolioId={portfolioId} dateFrom={oosDateFrom} />
        )}
        {tab === "categories" && (
          <CategoryBreakdownPanel portfolioId={portfolioId} dateFrom={oosDateFrom} />
        )}
      </Suspense>
    </div>
  );
}

function Loading() {
  return (
    <div className="flex items-center justify-center h-32">
      <Loader2 className="animate-spin text-ag-accent" size={24} />
    </div>
  );
}

function nextDay(isoDate: string): string {
  const d = new Date(isoDate);
  d.setDate(d.getDate() + 1);
  return d.toISOString().split("T")[0];
}
