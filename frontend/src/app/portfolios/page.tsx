"use client";
export const dynamic = "force-dynamic";

import { useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Briefcase, LayoutDashboard, Zap, Settings, FlaskConical, Wifi, Activity, FileText, PlusCircle, Archive, EyeOff, Trash2, Shuffle } from "lucide-react";
import PortfolioOverviewTab from "@/components/portfolios/PortfolioOverviewTab";
import SavedPortfolios from "@/components/analysis/SavedPortfolios";
import PaperPortfolios from "@/components/portfolios/PaperPortfolios";
import RealPortfolios from "@/components/portfolios/RealPortfolios";
import HiddenPortfolios from "@/components/portfolios/HiddenPortfolios";
import DeletedPortfolios from "@/components/portfolios/DeletedPortfolios";
import ArchivedPortfolios from "@/components/portfolios/ArchivedPortfolios";
import CreatePortfolioPanel from "@/components/portfolios/CreatePortfolioPanel";
import HealthDashboard from "@/components/portfolios/HealthDashboard";
import RebalanceProposals from "@/components/portfolios/RebalanceProposals";

type MainTab = "overview" | "trading" | "management";
type TradingSub = "paper" | "real" | "health" | "rebalance";
type MgmtSub = "saved" | "create" | "archived" | "hidden" | "deleted";

const MAIN_TABS: { id: MainTab; label: string; icon: typeof Briefcase }[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "trading", label: "Live Trading", icon: Zap },
  { id: "management", label: "Management", icon: Settings },
];

const TRADING_SUBS: { id: TradingSub; label: string; icon: typeof Briefcase }[] = [
  { id: "paper", label: "Paper Trading", icon: FlaskConical },
  { id: "real", label: "Real Trading", icon: Wifi },
  { id: "health", label: "Health Monitor", icon: Activity },
  { id: "rebalance", label: "Rebalance", icon: Shuffle },
];

const MGMT_SUBS: { id: MgmtSub; label: string; icon: typeof Briefcase }[] = [
  { id: "saved", label: "Saved Portfolios", icon: FileText },
  { id: "create", label: "Create", icon: PlusCircle },
  { id: "archived", label: "Archived", icon: Archive },
  { id: "hidden", label: "Hidden", icon: EyeOff },
  { id: "deleted", label: "Deleted", icon: Trash2 },
];

function resolveInitial(urlTab: string | null) {
  const tradingSubs = ["paper", "real", "health", "rebalance"];
  const mgmtSubs = ["saved", "create", "archived", "hidden", "deleted"];
  if (urlTab && tradingSubs.includes(urlTab))
    return { main: "trading" as MainTab, trading: urlTab as TradingSub, mgmt: "saved" as MgmtSub };
  if (urlTab && mgmtSubs.includes(urlTab))
    return { main: "management" as MainTab, trading: "paper" as TradingSub, mgmt: urlTab as MgmtSub };
  return { main: "overview" as MainTab, trading: "paper" as TradingSub, mgmt: "saved" as MgmtSub };
}

export default function PortfoliosPage() {
  return (
    <Suspense fallback={<div className="p-4 text-ag-text-muted text-sm">Loading…</div>}>
      <PortfoliosPageInner />
    </Suspense>
  );
}

function PortfoliosPageInner() {
  const searchParams = useSearchParams();
  const urlTab = searchParams.get("tab");
  const urlId = searchParams.get("id") ? Number(searchParams.get("id")) : undefined;

  const init = resolveInitial(urlTab);
  const [mainTab, setMainTab] = useState<MainTab>(init.main);
  const [tradingSub, setTradingSub] = useState<TradingSub>(init.trading);
  const [mgmtSub, setMgmtSub] = useState<MgmtSub>(init.mgmt);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 mb-2">
        <div className="p-2 rounded-lg bg-ag-accent/10 text-ag-accent">
          <Briefcase size={20} />
        </div>
        <h1 className="text-lg font-bold">Portfolio Hub</h1>
      </div>

      {/* Main tabs */}
      <div className="flex gap-1 border-b border-ag-border/50">
        {MAIN_TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => setMainTab(t.id)}
              className={`flex items-center gap-1.5 px-5 py-2.5 text-sm transition-colors border-b-2 -mb-px whitespace-nowrap ${
                mainTab === t.id
                  ? "border-ag-accent text-ag-accent font-medium"
                  : "border-transparent text-ag-text-muted hover:text-ag-text"
              }`}
            >
              <Icon size={15} />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Sub-tabs: Live Trading */}
      {mainTab === "trading" && (
        <div className="flex gap-1 border-b border-ag-border/30">
          {TRADING_SUBS.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                onClick={() => setTradingSub(t.id)}
                className={`flex items-center gap-1.5 px-4 py-2 text-xs transition-colors border-b-2 -mb-px whitespace-nowrap ${
                  tradingSub === t.id
                    ? "border-ag-accent/70 text-ag-accent font-medium"
                    : "border-transparent text-ag-text-muted hover:text-ag-text"
                }`}
              >
                <Icon size={13} />
                {t.label}
              </button>
            );
          })}
        </div>
      )}

      {/* Sub-tabs: Management */}
      {mainTab === "management" && (
        <div className="flex gap-1 border-b border-ag-border/30">
          {MGMT_SUBS.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                onClick={() => setMgmtSub(t.id)}
                className={`flex items-center gap-1.5 px-4 py-2 text-xs transition-colors border-b-2 -mb-px whitespace-nowrap ${
                  mgmtSub === t.id
                    ? "border-ag-accent/70 text-ag-accent font-medium"
                    : "border-transparent text-ag-text-muted hover:text-ag-text"
                }`}
              >
                <Icon size={13} />
                {t.label}
              </button>
            );
          })}
        </div>
      )}

      {/* Content */}
      {mainTab === "overview" && <PortfolioOverviewTab />}

      {mainTab === "trading" && tradingSub === "paper" && <PaperPortfolios defaultExpandedId={urlId} />}
      {mainTab === "trading" && tradingSub === "real" && <RealPortfolios defaultExpandedId={urlId} />}
      {mainTab === "trading" && tradingSub === "health" && <HealthDashboard />}
      {mainTab === "trading" && tradingSub === "rebalance" && <RebalanceProposals />}

      {mainTab === "management" && mgmtSub === "saved" && <SavedPortfolios defaultExpandedId={urlId} />}
      {mainTab === "management" && mgmtSub === "create" && <CreatePortfolioPanel />}
      {mainTab === "management" && mgmtSub === "archived" && <ArchivedPortfolios />}
      {mainTab === "management" && mgmtSub === "hidden" && <HiddenPortfolios />}
      {mainTab === "management" && mgmtSub === "deleted" && <DeletedPortfolios />}
    </div>
  );
}
