"use client";
import { useState, useEffect, useCallback } from "react";
import {
  Database, Code2, FlaskConical, BarChart3,
  Briefcase, BookOpen, Zap, ChevronDown, ChevronRight,
} from "lucide-react";
import ModuleCard from "@/components/common/ModuleCard";
import QuickStats from "@/components/dashboard/QuickStats";
import AccountEquityCurve from "@/components/dashboard/AccountEquityCurve";
import AccountKPIs from "@/components/dashboard/AccountKPIs";
import RecentTrades from "@/components/dashboard/RecentTrades";
import PortfolioOverview from "@/components/dashboard/PortfolioOverview";
import AccountSummary from "@/components/dashboard/AccountSummary";
import ActivityFeed from "@/components/dashboard/ActivityFeed";
import MarketOverview from "@/components/dashboard/MarketOverview";
import PortfolioPerformance from "@/components/dashboard/PortfolioPerformance";
import CategoryPerformance from "@/components/dashboard/CategoryPerformance";
import QuickActivate from "@/components/dashboard/QuickActivate";

const modules = [
  { title: "Data Collection", description: "Download OHLCV candles from Bybit.", href: "/data", icon: Database, phase: 2, status: "live" as const },
  { title: "Strategies", description: "PineScript transpiler for TV strategies.", href: "/strategies", icon: Code2, phase: 3, status: "planned" as const },
  { title: "Backtester", description: "Bar-by-bar engine with full metrics.", href: "/backtest", icon: FlaskConical, phase: 4, status: "planned" as const },
  { title: "Analysis", description: "KPIs, WFE, correlation, Monte Carlo.", href: "/analysis", icon: BarChart3, phase: 5, status: "planned" as const },
  { title: "Portfolios", description: "Optimizer with 10 objectives.", href: "/portfolios", icon: Briefcase, phase: 6, status: "planned" as const },
  { title: "Journal", description: "Auto-logged trades with notes.", href: "/journal", icon: BookOpen, phase: 7, status: "planned" as const },
  { title: "Live Trading", description: "Automated execution via webhooks.", href: "/trading", icon: Zap, phase: 8, status: "planned" as const },
];

const STORAGE_KEY = "warroom_dashboard_sections";
type SectionState = Record<string, boolean>;

function loadSections(): SectionState {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function CollapsibleSection({ id, title, defaultOpen = true, children }: {
  id: string; title: string; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [sections, setSections] = useState<SectionState>({});
  const [mounted, setMounted] = useState(false);

  useEffect(() => { setSections(loadSections()); setMounted(true); }, []);

  const isOpen = mounted ? (sections[id] ?? defaultOpen) : defaultOpen;

  const toggle = useCallback(() => {
    setSections((prev) => {
      const next = { ...prev, [id]: !isOpen };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, [id, isOpen]);

  return (
    <div>
      <button onClick={toggle}
        className="flex items-center gap-1.5 mb-2 text-xs font-semibold text-ag-text-muted uppercase tracking-wider hover:text-ag-text transition-colors">
        {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {title}
      </button>
      {isOpen && children}
    </div>
  );
}

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      {/* Quick stats hero bar */}
      <QuickStats />

      {/* Quick Activate — one-click portfolio activation */}
      <CollapsibleSection id="quick-activate" title="Quick Activate">
        <QuickActivate />
      </CollapsibleSection>

      {/* Equity Curve + KPIs */}
      <CollapsibleSection id="account-perf" title="Account Performance">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2">
            <AccountEquityCurve />
          </div>
          <AccountKPIs />
        </div>
      </CollapsibleSection>

      {/* Portfolio Performance */}
      <CollapsibleSection id="portfolio-perf" title="Portfolio Performance">
        <PortfolioPerformance />
      </CollapsibleSection>

      {/* Recent Trades + Account Summary */}
      <CollapsibleSection id="trading-perf" title="Trading Performance">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2">
            <RecentTrades />
          </div>
          <AccountSummary />
        </div>
      </CollapsibleSection>

      {/* Market Overview */}
      <CollapsibleSection id="market" title="Market Overview">
        <MarketOverview />
      </CollapsibleSection>

      {/* Category Performance */}
      <CollapsibleSection id="categories" title="Category Performance">
        <CategoryPerformance />
      </CollapsibleSection>

      {/* Portfolio overview */}
      <CollapsibleSection id="portfolio-overview" title="Portfolio Overview" defaultOpen={false}>
        <PortfolioOverview />
      </CollapsibleSection>

      {/* Activity feed + Modules */}
      <CollapsibleSection id="modules" title="Activity & Modules" defaultOpen={false}>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <ActivityFeed />
          <div className="lg:col-span-2">
            <h3 className="text-sm font-semibold text-ag-text-muted uppercase tracking-wider mb-3">Modules</h3>
            <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
              {modules.map((m) => <ModuleCard key={m.href} {...m} />)}
            </div>
          </div>
        </div>
      </CollapsibleSection>
    </div>
  );
}
