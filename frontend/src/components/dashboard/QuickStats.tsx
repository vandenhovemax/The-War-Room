"use client";
import { useCallback } from "react";
import {
  TrendingUp, TrendingDown, Target, Activity,
  DollarSign, Percent, BarChart3, Wallet,
} from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioItem } from "@/lib/api";
import { useApi } from "@/hooks/useApi";

interface HeroStatsProps {
  portfolioId?: number;
  onPortfolioChange?: (id: number | undefined) => void;
}

function PnlCard({ label, value, size = "normal" }: {
  label: string; value: number | null | undefined; size?: "large" | "normal";
}) {
  const isPositive = (value ?? 0) >= 0;
  const color = value == null ? "text-ag-text-muted" : isPositive ? "text-ag-green" : "text-ag-red";
  const textSize = size === "large" ? "text-2xl" : "text-lg";

  return (
    <div className="text-center">
      <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">{label}</p>
      <p className={`${textSize} font-bold font-mono ${color}`}>
        {value != null
          ? `${isPositive ? "+" : ""}$${Math.abs(value) >= 1000
              ? `${(value / 1000).toFixed(1)}k`
              : value.toFixed(2)}`
          : "---"}
      </p>
    </div>
  );
}

function MiniStat({ label, value, icon: Icon, color = "text-ag-text" }: {
  label: string; value: string; icon: typeof Target; color?: string;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-ag-dark/40 rounded-lg">
      <Icon size={14} className={color} />
      <div>
        <p className="text-[9px] text-ag-text-muted uppercase">{label}</p>
        <p className={`text-sm font-bold ${color}`}>{value}</p>
      </div>
    </div>
  );
}

export default function QuickStats({ portfolioId, onPortfolioChange }: HeroStatsProps) {
  const { data: stats } = useApi(useCallback(() => api.dashboardStats(), []), "dashboard-stats");
  const { data: kpis } = useApi(
    useCallback(() => api.accountKpis(portfolioId), [portfolioId]),
    `account-kpis-hero-${portfolioId ?? "all"}`,
  );
  const { data: tradeStats } = useApi(useCallback(() => api.tradeStats(), []), "trade-stats");
  const { data: paper } = useApi(
    useCallback(() => api.portfoliosByType("paper"), []),
    "hero-portfolios-paper",
  );
  const { data: real } = useApi(
    useCallback(() => api.portfoliosByType("real"), []),
    "hero-portfolios-real",
  );

  // Merge portfolios for selector
  const allPortfolios: PortfolioItem[] = [...(paper ?? []), ...(real ?? [])];

  const totalPnl = kpis?.total_pnl ?? tradeStats?.total_pnl ?? null;
  const isPositive = (totalPnl ?? 0) >= 0;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <div className="flex flex-col lg:flex-row lg:items-center gap-4">
        {/* Left: P&L Hero */}
        <div className="flex items-center gap-6 flex-1">
          <div className="flex items-center gap-3">
            <div className={`p-3 rounded-xl ${isPositive ? "bg-ag-green/10" : "bg-ag-red/10"}`}>
              {isPositive ? (
                <TrendingUp size={24} className="text-ag-green" />
              ) : (
                <TrendingDown size={24} className="text-ag-red" />
              )}
            </div>
            <PnlCard label="Total P&L" value={totalPnl} size="large" />
          </div>

          <div className="hidden sm:flex items-center gap-6 border-l border-ag-border/30 pl-6">
            <div className="text-center">
              <p className="text-[10px] text-ag-text-muted uppercase mb-0.5">Win Rate</p>
              <p className="text-lg font-bold">
                {kpis?.win_rate != null ? `${kpis.win_rate.toFixed(1)}%` : tradeStats?.win_rate != null ? `${tradeStats.win_rate.toFixed(1)}%` : "---"}
              </p>
            </div>
            <div className="text-center">
              <p className="text-[10px] text-ag-text-muted uppercase mb-0.5">Profit Factor</p>
              <p className="text-lg font-bold">
                {kpis?.profit_factor != null ? kpis.profit_factor.toFixed(2) : "---"}
              </p>
            </div>
            <div className="text-center">
              <p className="text-[10px] text-ag-text-muted uppercase mb-0.5">Max DD</p>
              <p className="text-lg font-bold text-ag-red">
                {kpis?.max_drawdown != null ? `$${kpis.max_drawdown.toFixed(0)}` : "---"}
              </p>
            </div>
          </div>
        </div>

        {/* Right: Quick stats + Portfolio selector */}
        <div className="flex flex-col gap-2">
          {/* Portfolio selector — only shown when callback provided */}
          {onPortfolioChange && allPortfolios.length > 0 && (
            <select
              value={portfolioId ?? ""}
              onChange={(e) => onPortfolioChange(e.target.value ? Number(e.target.value) : undefined)}
              className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-xs w-full"
            >
              <option value="">All Portfolios</option>
              {allPortfolios.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          )}

          <div className="grid grid-cols-3 gap-2">
            <MiniStat label="Strategies" value={String(stats?.strategies ?? "—")} icon={Target} color="text-ag-accent" />
            <MiniStat label="Open" value={String(kpis?.open_positions ?? stats?.open_positions ?? "—")} icon={Activity} color="text-ag-yellow" />
            <MiniStat label="Trades" value={String(kpis?.total_trades ?? tradeStats?.total_trades ?? "—")} icon={BarChart3} color="text-ag-green" />
          </div>
        </div>
      </div>

      {/* Mobile: show stats that were hidden on desktop */}
      <div className="sm:hidden grid grid-cols-3 gap-3 mt-3 pt-3 border-t border-ag-border/20">
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted uppercase">Win Rate</p>
          <p className="text-sm font-bold">
            {kpis?.win_rate != null ? `${kpis.win_rate.toFixed(1)}%` : "---"}
          </p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted uppercase">PF</p>
          <p className="text-sm font-bold">
            {kpis?.profit_factor != null ? kpis.profit_factor.toFixed(2) : "---"}
          </p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted uppercase">Max DD</p>
          <p className="text-sm font-bold text-ag-red">
            {kpis?.max_drawdown != null ? `$${kpis.max_drawdown.toFixed(0)}` : "---"}
          </p>
        </div>
      </div>
    </div>
  );
}
