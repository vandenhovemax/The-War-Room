"use client";
import type { PortfolioItem } from "@/lib/api";

interface PortfolioCatalogProps {
  portfolios: PortfolioItem[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}

type GroupKey = string;

export default function PortfolioCatalog({ portfolios, selectedId, onSelect }: PortfolioCatalogProps) {
  // Group portfolios by source type and optimization context
  const groups = new Map<GroupKey, PortfolioItem[]>();

  for (const p of portfolios) {
    if (p.is_deleted) continue;
    let key: string;
    if (p.portfolio_source === "cross_strategy" || p.is_cross_strat) {
      key = "Cross-Strategy";
    } else if (p.portfolio_source === "single_strategy") {
      key = "Single Strategy";
    } else if (p.portfolio_type === "paper") {
      key = "Paper Trading";
    } else if (p.portfolio_type === "real") {
      key = "Real Trading";
    } else {
      key = "Saved";
    }
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(p);
  }

  if (portfolios.length === 0) {
    return (
      <div className="text-center py-12 text-ag-text-muted text-sm">
        No saved portfolios.
      </div>
    );
  }

  const typeColor = (type: string) => {
    if (type === "Cross-Strategy") return "text-purple-400 bg-purple-500/10 border-purple-500/30";
    if (type === "Single Strategy") return "text-blue-400 bg-blue-500/10 border-blue-500/30";
    if (type === "Paper Trading") return "text-yellow-400 bg-yellow-500/10 border-yellow-500/30";
    if (type === "Real Trading") return "text-green-400 bg-green-500/10 border-green-500/30";
    return "text-ag-text-muted bg-ag-card border-ag-border";
  };

  const fmtNum = (v: number | undefined, decimals = 2) =>
    v !== undefined && v !== null ? v.toFixed(decimals) : "—";

  return (
    <div className="space-y-6">
      {Array.from(groups.entries()).map(([groupName, items]) => (
        <div key={groupName}>
          <h3 className="text-sm font-semibold text-ag-text mb-3 flex items-center gap-2">
            <span className={`px-2 py-0.5 rounded text-xs font-bold border ${typeColor(groupName)}`}>
              {groupName}
            </span>
            <span className="text-ag-text-muted text-xs">({items.length})</span>
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {items.map((p) => {
              const isActive = p.id === selectedId;
              const ms = p.metrics_snapshot || {};
              return (
                <button
                  key={p.id}
                  onClick={() => onSelect(p.id)}
                  className={`text-left p-3 rounded-lg border transition-all ${
                    isActive
                      ? "border-ag-accent bg-ag-accent/10 ring-1 ring-ag-accent/30"
                      : "border-ag-border bg-ag-card hover:border-ag-accent/50 hover:bg-ag-card-hover"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <h4 className="text-sm font-medium text-ag-text leading-tight">
                      {p.name}
                    </h4>
                    {p.is_active && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/15 text-green-400 border border-green-500/30 shrink-0">
                        LIVE
                      </span>
                    )}
                  </div>
                  <div className="grid grid-cols-3 gap-x-3 gap-y-1.5 text-xs mt-2">
                    <div>
                      <span className="text-ag-text-muted block">Assets</span>
                      <span className="text-ag-text font-semibold">{p.asset_count}</span>
                    </div>
                    <div>
                      <span className="text-ag-text-muted block">
                        {p.optimization_end_date ? "IS PF" : "PF"}
                      </span>
                      <span className={`font-semibold ${
                        (ms.profit_factor ?? 0) >= 1.5 ? "text-green-400" :
                        (ms.profit_factor ?? 0) >= 1.0 ? "text-yellow-400" : "text-red-400"
                      }`}>
                        {fmtNum(ms.profit_factor as number | undefined)}
                      </span>
                    </div>
                    <div>
                      <span className="text-ag-text-muted block">Win Rate</span>
                      <span className="text-ag-text font-semibold">
                        {ms.win_rate !== undefined ? `${(ms.win_rate as number * 100).toFixed(1)}%` : fmtNum(ms.win_pct as number | undefined, 1) + "%"}
                      </span>
                    </div>
                    {ms.max_drawdown !== undefined && (
                      <div>
                        <span className="text-ag-text-muted block">Max DD</span>
                        <span className="text-red-400 font-semibold">
                          {fmtNum(ms.max_drawdown as number | undefined)}%
                        </span>
                      </div>
                    )}
                    {ms.sortino !== undefined && (
                      <div>
                        <span className="text-ag-text-muted block">Sortino</span>
                        <span className="text-ag-text font-semibold">
                          {fmtNum(ms.sortino as number | undefined)}
                        </span>
                      </div>
                    )}
                    {ms.net_profit !== undefined && (
                      <div>
                        <span className="text-ag-text-muted block">Net Profit</span>
                        <span className={`font-semibold ${(ms.net_profit as number) >= 0 ? "text-green-400" : "text-red-400"}`}>
                          ${(ms.net_profit as number).toFixed(0)}
                        </span>
                      </div>
                    )}
                  </div>
                  {(p.created_at || p.description) && (
                    <div className="mt-2 pt-2 border-t border-ag-border/50 text-[10px] text-ag-text-muted">
                      {p.created_at && (
                        <span>Created {new Date(p.created_at).toLocaleDateString()}</span>
                      )}
                      {p.description && (
                        <p className="truncate mt-0.5">{p.description}</p>
                      )}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
