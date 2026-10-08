"use client";
import { useState, useEffect, useRef } from "react";
import { api } from "@/lib/api";
import type { FilterSummaryResult, DashboardAnalysisRequest } from "@/lib/api";

interface Props {
  batchId?: number;
  portfolioId?: number;
  oosDate?: string;
  minTrades?: number;
  excludeTop?: number;
  pfMin?: number;
  pfMax?: number;
  wrMin?: number;
}

export default function FilterSummaryStrip({ batchId, portfolioId, oosDate, minTrades, excludeTop, pfMin, pfMax, wrMin }: Props) {
  const [summary, setSummary] = useState<FilterSummaryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Check if any filter is active
  const hasFilters = !!(oosDate || (minTrades && minTrades > 0) || (excludeTop && excludeTop > 0) || pfMin !== undefined || pfMax !== undefined || wrMin !== undefined);

  useEffect(() => {
    if (!batchId && !portfolioId) { setSummary(null); return; }

    // Debounce 300ms to avoid hammering on every keystroke
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      setLoading(true);
      const req: DashboardAnalysisRequest = {
        batch_id: batchId,
        portfolio_id: portfolioId,
        date_to: oosDate || undefined,
        min_trades: minTrades || undefined,
        exclude_top_n: excludeTop || undefined,
        pf_min: pfMin,
        pf_max: pfMax,
        wr_min: wrMin,
      };
      api.dashboardFilterSummary(req)
        .then(setSummary)
        .catch(() => setSummary(null))
        .finally(() => setLoading(false));
    }, 300);

    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [batchId, portfolioId, oosDate, minTrades, excludeTop, pfMin, pfMax, wrMin]);

  if (!summary) return null;

  const isFiltered = summary.total_assets !== summary.filtered_assets;
  const pnlColor = summary.total_pnl >= 0 ? "text-ag-green" : "text-ag-red";

  return (
    <div className={`flex items-center gap-4 flex-wrap px-3 py-2 rounded-lg border text-xs font-mono ${
      isFiltered ? "border-ag-accent/30 bg-ag-accent/5" : "border-ag-border bg-ag-card"
    } ${loading ? "opacity-60" : ""}`}>
      {/* Asset count */}
      <span className="font-semibold">
        {isFiltered ? (
          <><span className="text-ag-accent">{summary.filtered_assets}</span> / {summary.total_assets} assets</>
        ) : (
          <>{summary.total_assets} assets</>
        )}
      </span>

      <span className="w-px h-4 bg-ag-border" />

      {/* Total PnL */}
      <span>
        <span className="text-ag-text-muted mr-1">PnL:</span>
        <span className={pnlColor}>${summary.total_pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
      </span>

      <span className="w-px h-4 bg-ag-border" />

      {/* Real PF */}
      <span>
        <span className="text-ag-text-muted mr-1">Real PF:</span>
        <span className={summary.real_pf !== null && summary.real_pf >= 1 ? "text-ag-green" : "text-ag-red"}>
          {summary.real_pf?.toFixed(2) ?? "\u2014"}
        </span>
      </span>

      {/* Mean PF */}
      <span>
        <span className="text-ag-text-muted mr-1">Mean PF:</span>
        <span className={summary.mean_pf !== null && summary.mean_pf >= 1 ? "text-ag-green" : "text-ag-red"}>
          {summary.mean_pf?.toFixed(2) ?? "\u2014"}
        </span>
      </span>

      <span className="w-px h-4 bg-ag-border" />

      {/* Win Rate — both aggregate and average */}
      <span className="flex items-center gap-1">
        <span className="text-ag-text-muted mr-1">WR:</span>
        <span title="Aggregate: total winning trades / total trades across all symbols">{summary.agg_wr?.toFixed(1) ?? "\u2014"}%</span>
        <span className="text-ag-text-muted text-[9px]">agg</span>
        <span className="text-ag-text-muted mx-0.5">·</span>
        <span title="Average: mean of each symbol's individual win rate">{summary.avg_wr?.toFixed(1) ?? "\u2014"}%</span>
        <span className="text-ag-text-muted text-[9px]">avg</span>
      </span>

      {/* Trade count */}
      <span>
        <span className="text-ag-text-muted mr-1">Trades:</span>
        <span>{summary.total_trades.toLocaleString()}</span>
      </span>

      {hasFilters && isFiltered && (
        <span className="text-ag-accent text-[10px] font-sans font-medium">FILTERED</span>
      )}
    </div>
  );
}
