"use client";
import { useState, useEffect } from "react";
import { Loader2, ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/lib/api";
import type { BatchOverviewResult, BatchSymbolSummary } from "@/lib/api";
import CategoryDetailPanel from "./CategoryDetailPanel";

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
}

interface CategoryStats {
  category: string;
  assets: number;
  netProfit: number;
  profitFactor: number | null;
  winRate: number;
  totalTrades: number;
  symbols: BatchSymbolSummary[];
}

function fmt(v: number | null | undefined, d: number = 2): string {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  return v.toFixed(d);
}

function fmtDollar(v: number): string {
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function CategoryBreakdownPanel({ batchId, portfolioId, dateFrom, dateTo, minTrades, excludeTopN, pfMin, pfMax, wrMin }: Props) {
  const [categories, setCategories] = useState<Record<string, string[]>>({});
  const [overview, setOverview] = useState<BatchOverviewResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedCat, setExpandedCat] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError("");
    const req = {
      ...(portfolioId ? { portfolio_id: portfolioId } : { batch_id: batchId }),
      date_from: dateFrom,
      date_to: dateTo,
      min_trades: minTrades,
      exclude_top_n: excludeTopN,
      pf_min: pfMin,
      pf_max: pfMax,
      wr_min: wrMin,
    };
    Promise.all([
      api.categories(),
      api.dashboardBatchOverview(req),
    ])
      .then(([cats, ov]) => {
        setCategories(cats);
        setOverview(ov);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [batchId, portfolioId, dateFrom, dateTo, minTrades, excludeTopN, pfMin, pfMax, wrMin]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Loader2 className="animate-spin text-ag-accent" size={28} />
      </div>
    );
  }

  if (error) return <p className="text-ag-red text-sm">{error}</p>;

  if (!overview || overview.symbols.length === 0) {
    return <p className="text-ag-text-muted text-sm">No symbol data available.</p>;
  }

  const catStats = buildCategoryStats(overview, categories);
  const uncategorized = overview.symbols.filter((s) => {
    const sym = s.asset.replace(/_Long|_Short/g, "");
    return !categories[sym] || categories[sym].length === 0;
  });

  const toggle = (cat: string) => setExpandedCat(expandedCat === cat ? null : cat);

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold">Category Breakdown</h3>

      {catStats.length === 0 ? (
        <p className="text-ag-text-muted text-sm py-4 text-center">
          No category data available. Add categories via the import script or UI.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {catStats.map((cat) => (
              <CategoryCard key={cat.category} stats={cat}
                isExpanded={expandedCat === cat.category}
                onToggle={() => toggle(cat.category)} />
            ))}
          </div>

          {/* Summary table */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-ag-border">
                <tr>
                  <th className="w-6 px-1 py-2" />
                  <Th>Category</Th>
                  <Th># Assets</Th>
                  <Th>Net Profit</Th>
                  <Th>PF</Th>
                  <Th>Win Rate</Th>
                  <Th>Positions</Th>
                </tr>
              </thead>
              <tbody>
                {catStats.map((cat) => (
                  <CategoryRow key={cat.category} stats={cat}
                    isExpanded={expandedCat === cat.category}
                    onToggle={() => toggle(cat.category)} />
                ))}
              </tbody>
            </table>
          </div>

          {uncategorized.length > 0 && (
            <p className="text-[10px] text-ag-text-muted">
              {uncategorized.length} symbol(s) uncategorized:{" "}
              {uncategorized.map((s) => s.asset).join(", ")}
            </p>
          )}
        </>
      )}
    </div>
  );
}

function CategoryCard({ stats, isExpanded, onToggle }: {
  stats: CategoryStats; isExpanded: boolean; onToggle: () => void;
}) {
  const profitable = stats.netProfit > 0;
  return (
    <div
      className={`bg-ag-card border rounded-lg p-4 space-y-2 cursor-pointer transition-colors ${
        profitable ? "border-ag-green/30 hover:border-ag-green/50" : "border-ag-red/30 hover:border-ag-red/50"
      } ${isExpanded ? "ring-1 ring-ag-accent/40" : ""}`}
      onClick={onToggle}
    >
      <div className="flex items-center justify-between">
        <span className="font-semibold text-sm">{stats.category}</span>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-ag-text-muted">{stats.assets} assets</span>
          {isExpanded ? <ChevronUp size={12} className="text-ag-accent" /> : <ChevronDown size={12} className="text-ag-text-muted" />}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        <KPI label="Net Profit" value={fmtDollar(stats.netProfit)}
          color={profitable ? "text-ag-green" : "text-ag-red"} />
        <KPI label="PF" value={fmt(stats.profitFactor)} />
        <KPI label="Win Rate" value={`${fmt(stats.winRate)}%`} />
        <KPI label="Positions" value={String(stats.totalTrades)} />
      </div>
      {isExpanded && (
        <div className="border-t border-ag-border/50 pt-2 mt-1" onClick={(e) => e.stopPropagation()}>
          <CategoryDetailPanel symbols={stats.symbols} />
        </div>
      )}
    </div>
  );
}

function CategoryRow({ stats, isExpanded, onToggle }: {
  stats: CategoryStats; isExpanded: boolean; onToggle: () => void;
}) {
  return (
    <>
      <tr className="border-b border-ag-border/50 hover:bg-ag-card-hover cursor-pointer" onClick={onToggle}>
        <td className="px-1 py-2 text-center">
          {isExpanded ? <ChevronUp size={12} className="text-ag-accent" /> : <ChevronDown size={12} className="text-ag-text-muted" />}
        </td>
        <td className="px-3 py-2 font-medium">{stats.category}</td>
        <td className="px-3 py-2">{stats.assets}</td>
        <td className={`px-3 py-2 ${stats.netProfit > 0 ? "text-ag-green" : stats.netProfit < 0 ? "text-ag-red" : ""}`}>
          {fmtDollar(stats.netProfit)}
        </td>
        <td className="px-3 py-2">{fmt(stats.profitFactor)}</td>
        <td className="px-3 py-2">{fmt(stats.winRate)}%</td>
        <td className="px-3 py-2">{stats.totalTrades}</td>
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={7} className="p-0">
            <div className="border-l-2 border-ag-accent ml-4 pl-3 py-2 bg-ag-darker/50">
              <CategoryDetailPanel symbols={stats.symbols} />
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function KPI({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <p className="text-[10px] text-ag-text-muted">{label}</p>
      <p className={`font-medium ${color || "text-ag-text"}`}>{value}</p>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-3 py-2 text-left text-xs font-medium text-ag-text-muted">
      {children}
    </th>
  );
}

function buildCategoryStats(
  overview: BatchOverviewResult,
  categories: Record<string, string[]>,
): CategoryStats[] {
  const catMap: Record<string, BatchSymbolSummary[]> = {};

  for (const sym of overview.symbols) {
    const base = sym.asset.replace(/_Long|_Short/g, "");
    const cats = categories[base] || [];
    if (cats.length === 0) continue;
    for (const cat of cats) {
      if (!catMap[cat]) catMap[cat] = [];
      catMap[cat].push(sym);
    }
  }

  const stats: CategoryStats[] = [];
  for (const [category, symbols] of Object.entries(catMap)) {
    const totalTrades = symbols.reduce((s, sym) => s + sym.trades, 0);
    const totalProfit = symbols.reduce((s, sym) => s + sym.net_profit, 0);
    const totalWins = symbols.reduce((s, sym) => {
      return s + Math.round(sym.trades * (sym.win_rate / 100));
    }, 0);
    const winRate = totalTrades > 0 ? (totalWins / totalTrades) * 100 : 0;

    const gains = symbols.filter((s) => s.net_profit > 0).reduce((s, sym) => s + sym.net_profit, 0);
    const losses = Math.abs(symbols.filter((s) => s.net_profit < 0).reduce((s, sym) => s + sym.net_profit, 0));
    const profitFactor = losses > 0 ? Math.min(gains / losses, 10) : gains > 0 ? 10 : null;

    stats.push({ category, assets: symbols.length, netProfit: totalProfit, profitFactor, winRate, totalTrades, symbols });
  }

  stats.sort((a, b) => b.netProfit - a.netProfit);
  return stats;
}
