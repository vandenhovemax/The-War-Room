"use client";
import { Ban } from "lucide-react";
import HintTip from "@/components/common/HintTip";
import type { PortfolioItem } from "@/lib/api";

export type ExcludeMode = "full" | "partial";
export type ExcludeMethod = "score" | "correlation" | "blend";

interface Props {
  portfolios: PortfolioItem[];
  selectedIds: Set<number>;
  onToggle: (id: number) => void;
  mode: ExcludeMode;
  onModeChange: (mode: ExcludeMode) => void;
  keepPct: number;
  onKeepPctChange: (pct: number) => void;
  method: ExcludeMethod;
  onMethodChange: (method: ExcludeMethod) => void;
}

/**
 * Portfolio exclusion selector for cross-strategy optimizer.
 * Lets the user pick portfolios whose assets should be excluded
 * (fully or partially) from the optimization pool.
 */
export default function PortfolioExcludeSelector({
  portfolios, selectedIds, onToggle,
  mode, onModeChange, keepPct, onKeepPctChange,
  method, onMethodChange,
}: Props) {
  const saved = portfolios.filter((p) => p.portfolio_type === "saved" && !p.is_hidden);

  if (saved.length === 0) return null;

  const hasSelection = selectedIds.size > 0;

  return (
    <div>
      <label className="text-xs text-ag-text-muted flex items-center gap-1 mb-1">
        <Ban size={12} />
        Exclude Portfolio Assets
        <HintTip text="Remove assets from a saved portfolio from the optimization pool. 'Full' removes all matching assets. 'Partial' keeps only X% of them — the optimizer picks which ones work best with the rest of the pool using the selected method (score, correlation, or a blend of both)." />
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
              className="accent-red-500"
            />
            <span className="truncate flex-1">{p.name}</span>
            <span className="text-ag-text-muted whitespace-nowrap">
              {p.asset_count} assets
            </span>
          </label>
        ))}
      </div>

      {hasSelection && (
        <div className="mt-2 flex items-center gap-3 flex-wrap">
          {/* Mode toggle */}
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-ag-text-muted">Mode:</span>
            <select
              value={mode}
              onChange={(e) => onModeChange(e.target.value as ExcludeMode)}
              className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-[11px]"
            >
              <option value="full">Full Exclude</option>
              <option value="partial">Partial (keep %)</option>
            </select>
          </div>

          {mode === "partial" && (
            <>
              {/* Keep % */}
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-ag-text-muted">Keep:</span>
                <input
                  type="number"
                  min={0} max={100} step={5}
                  value={keepPct}
                  onChange={(e) => onKeepPctChange(Math.max(0, Math.min(100, Number(e.target.value))))}
                  className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-[11px] w-14 text-center"
                />
                <span className="text-[11px] text-ag-text-muted">%</span>
              </div>

              {/* Method */}
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-ag-text-muted">Method:</span>
                <select
                  value={method}
                  onChange={(e) => onMethodChange(e.target.value as ExcludeMethod)}
                  className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-[11px]"
                >
                  <option value="score">Score (best quality)</option>
                  <option value="correlation">Correlation (most diverse)</option>
                  <option value="blend">Blend (50/50)</option>
                </select>
                <HintTip text="Score: keeps the highest-scoring assets from the excluded portfolio. Correlation: keeps assets least correlated with the rest (most diverse). Blend: 50/50 mix of both criteria." />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
