"use client";
import { useState, useEffect, useMemo } from "react";
import { api } from "@/lib/api";
import { BarChart3 } from "lucide-react";
import CustomCategoryGroup from "./CustomCategoryGroup";
import FilteredCategoryGroup from "./FilteredCategoryGroup";
import HintTip from "@/components/common/HintTip";

interface CatReturn {
  d: number; w: number; m: number; y: number;
  asset_count: number; symbols: string[];
}

interface CatPnl {
  d: number; w: number; m: number; y: number;
  trade_count: number;
}

export default function CategoryPerformance() {
  const [source, setSource] = useState<"watchlist" | "all">("watchlist");
  const [returns, setReturns] = useState<Record<string, CatReturn>>({});
  const [pnl, setPnl] = useState<Record<string, CatPnl>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.categoryReturns(source),
      api.categoryTradingPnl(source),
    ]).then(([r, p]) => {
      if (cancelled) return;
      setReturns(r.categories);
      setPnl(p.categories);
      setLoading(false);
    }).catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [source]);

  const allCategories = useMemo(() =>
    Object.keys(returns).sort(), [returns],
  );

  const hasPnl = Object.keys(pnl).length > 0;

  // Sort categories by daily return descending for the table
  const sortedCategories = useMemo(() =>
    allCategories.map((cat) => ({ cat, data: returns[cat] }))
      .filter((item) => item.data)
      .sort((a, b) => a.cat.localeCompare(b.cat)),
    [allCategories, returns],
  );

  const pctCell = (val: number) => (
    <span className={`font-mono text-xs ${val > 0 ? "text-ag-green" : val < 0 ? "text-ag-red" : "text-ag-text-muted"}`}>
      {val > 0 ? "+" : ""}{val.toFixed(1)}%
    </span>
  );

  const pnlCell = (val: number) => (
    <span className={`font-mono text-xs ${val > 0 ? "text-ag-green" : val < 0 ? "text-ag-red" : "text-ag-text-muted"}`}>
      {val > 0 ? "+" : ""}{Math.abs(val) < 1000 ? `$${val.toFixed(0)}` : `$${(val / 1000).toFixed(1)}k`}
    </span>
  );

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart3 size={16} className="text-ag-accent" />
          <h3 className="text-sm font-semibold">Category Performance</h3>
          <HintTip text="Market returns by asset category. Use the Custom Group and Filtered Group below to combine/filter categories." />
        </div>
        <label className="flex items-center gap-2 text-xs text-ag-text-muted cursor-pointer">
          <input type="checkbox" checked={source === "watchlist"}
            onChange={(e) => setSource(e.target.checked ? "watchlist" : "all")}
            className="rounded border-ag-border" />
          Watchlist only
        </label>
      </div>

      {loading && <p className="text-xs text-ag-text-muted">Loading category data...</p>}

      {/* Market Returns Table — all categories, no filtering */}
      {sortedCategories.length > 0 && (
        <div>
          <p className="text-[10px] text-ag-text-muted uppercase font-medium mb-1">Market Returns</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] text-ag-text-muted border-b border-ag-border/30">
                  <th className="text-left py-1 px-2">Category</th>
                  <th className="text-right py-1 px-2">Assets</th>
                  <th className="text-right py-1 px-2">Day</th>
                  <th className="text-right py-1 px-2">Week</th>
                  <th className="text-right py-1 px-2">Month</th>
                  <th className="text-right py-1 px-2">Year</th>
                </tr>
              </thead>
              <tbody>
                {sortedCategories.map(({ cat, data }) => (
                  <tr key={cat} className="hover:bg-ag-card-hover border-b border-ag-border/10">
                    <td className="py-1 px-2 font-medium">{cat}</td>
                    <td className="text-right py-1 px-2 text-ag-text-muted">{data.asset_count}</td>
                    <td className="text-right py-1 px-2">{pctCell(data.d)}</td>
                    <td className="text-right py-1 px-2">{pctCell(data.w)}</td>
                    <td className="text-right py-1 px-2">{pctCell(data.m)}</td>
                    <td className="text-right py-1 px-2">{pctCell(data.y)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Trading PnL Table (only if data exists) */}
      {hasPnl && (
        <div>
          <p className="text-[10px] text-ag-text-muted uppercase font-medium mb-1">Trading PnL</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] text-ag-text-muted border-b border-ag-border/30">
                  <th className="text-left py-1 px-2">Category</th>
                  <th className="text-right py-1 px-2">Trades</th>
                  <th className="text-right py-1 px-2">Day</th>
                  <th className="text-right py-1 px-2">Week</th>
                  <th className="text-right py-1 px-2">Month</th>
                  <th className="text-right py-1 px-2">Year</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(pnl)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([cat, data]) => (
                    <tr key={cat} className="hover:bg-ag-card-hover border-b border-ag-border/10">
                      <td className="py-1 px-2 font-medium">{cat}</td>
                      <td className="text-right py-1 px-2 text-ag-text-muted">{data.trade_count}</td>
                      <td className="text-right py-1 px-2">{pnlCell(data.d)}</td>
                      <td className="text-right py-1 px-2">{pnlCell(data.w)}</td>
                      <td className="text-right py-1 px-2">{pnlCell(data.m)}</td>
                      <td className="text-right py-1 px-2">{pnlCell(data.y)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Custom Group — side-by-side categories (average/median/weighted) */}
      {allCategories.length > 0 && (
        <CustomCategoryGroup allCategories={allCategories} source={source} categoryData={returns} />
      )}

      {/* Filtered Group — tri-state include/exclude with asset-level logic */}
      {allCategories.length > 0 && (
        <FilteredCategoryGroup allCategories={allCategories} source={source} />
      )}
    </div>
  );
}
