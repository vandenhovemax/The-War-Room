"use client";
import { Briefcase } from "lucide-react";
import HintTip from "@/components/common/HintTip";
import type { PortfolioItem } from "@/lib/api";

interface Props {
  portfolios: PortfolioItem[];
  selectedIds: Set<number>;
  onToggle: (id: number) => void;
}

/**
 * Reusable saved-portfolio selector for cross-strategy / WFO panels.
 * Each portfolio acts as a "virtual batch" with pre-selected assets.
 */
export default function PortfolioSelector({ portfolios, selectedIds, onToggle }: Props) {
  const saved = portfolios.filter((p) => p.portfolio_type === "saved" && !p.is_hidden);

  if (saved.length === 0) return null;

  return (
    <div>
      <label className="text-xs text-ag-text-muted flex items-center gap-1 mb-1">
        <Briefcase size={12} />
        Saved Portfolios
        <HintTip text="Use a previously saved portfolio as a data source. Its pre-selected assets and their trade data will be loaded for optimization alongside batch backtests." />
      </label>
      <div className="max-h-32 overflow-y-auto space-y-1 border border-ag-border rounded-lg p-2 bg-ag-dark">
        {saved.map((p) => (
          <label
            key={p.id}
            className="flex items-center gap-2 px-2 py-1 hover:bg-ag-border/30 rounded cursor-pointer text-xs"
          >
            <input
              type="checkbox"
              checked={selectedIds.has(p.id)}
              onChange={() => onToggle(p.id)}
              className="accent-ag-accent"
            />
            <span className="truncate flex-1">
              {p.name}
            </span>
            <span className="text-ag-text-muted whitespace-nowrap">
              {p.asset_count} assets
              {p.metrics_snapshot?.profit_factor
                ? ` · ${p.optimization_end_date ? "IS " : ""}PF ${p.metrics_snapshot.profit_factor}`
                : ""}
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}
