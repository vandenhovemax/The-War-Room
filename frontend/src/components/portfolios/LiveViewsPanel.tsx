"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { LiveViewsResult } from "@/lib/api";
import { Info } from "lucide-react";
import { ComparisonMetricGrid } from "@/components/analysis/metrics";
import { MonthlyMatrix, EquityCurve } from "@/components/analysis/metrics";

interface Props {
  portfolioId: number;
}

type ViewMode = "in_sample" | "oos" | "oos_full" | "live";

const VIEW_CONFIG: { id: ViewMode; label: string; tooltip: string }[] = [
  {
    id: "in_sample",
    label: "In-Sample",
    tooltip: "Training period the optimizer used to find this portfolio. Shows how the strategy performed on data it was fitted to.",
  },
  {
    id: "oos",
    label: "OOS (Split)",
    tooltip: "Out-of-sample period from optimization end to portfolio creation date. Unseen data the optimizer never touched — the true test.",
  },
  {
    id: "oos_full",
    label: "OOS (Full)",
    tooltip: "Full out-of-sample: from optimization end to present. Combines OOS + Live into one continuous view.",
  },
  {
    id: "live",
    label: "Live",
    tooltip: "Post-creation period: trades since this portfolio was saved. Real-world monitoring of ongoing performance.",
  },
];

export default function LiveViewsPanel({ portfolioId }: Props) {
  const [data, setData] = useState<LiveViewsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<ViewMode>("oos_full");
  const [hoveredTooltip, setHoveredTooltip] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    api.portfolioLiveViews(portfolioId)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [portfolioId]);

  if (loading) return <p className="text-xs text-ag-text-muted">Loading live views...</p>;
  if (!data) return <p className="text-xs text-ag-text-muted">Failed to load live views.</p>;

  const view = data[mode];
  if (!view) return <p className="text-xs text-ag-text-muted">No data for this view.</p>;

  const hasFullMetrics = view.full_metrics && Object.keys(view.full_metrics).length > 0;
  const currentLabel = VIEW_CONFIG.find((v) => v.id === mode)?.label ?? mode;

  return (
    <div className="space-y-3">
      {/* Period toggles with tooltips */}
      <div className="flex gap-1 flex-wrap">
        {VIEW_CONFIG.map((v) => (
          <div key={v.id} className="relative flex items-center">
            <button onClick={() => setMode(v.id)}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                mode === v.id
                  ? "bg-ag-accent/15 text-ag-accent font-medium"
                  : "text-ag-text-muted hover:text-ag-text"
              }`}>
              {v.label}
            </button>
            <div className="relative"
              onMouseEnter={() => setHoveredTooltip(v.id)}
              onMouseLeave={() => setHoveredTooltip(null)}>
              <Info size={12} className="text-ag-text-muted/50 hover:text-ag-text-muted cursor-help ml-0.5" />
              {hoveredTooltip === v.id && (
                <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-56 px-2.5 py-1.5 bg-ag-dark border border-ag-border rounded-lg shadow-lg text-[10px] text-ag-text-muted leading-relaxed">
                  {v.tooltip}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Date range & trade count */}
      <div className="flex items-center gap-4 text-xs text-ag-text-muted">
        {view.start_date && (
          <span>From: {new Date(view.start_date).toLocaleDateString()}</span>
        )}
        {view.end_date && (
          <span>To: {new Date(view.end_date).toLocaleDateString()}</span>
        )}
        {!view.end_date && view.start_date && <span>To: Present</span>}
        <span>{view.trade_count} trades</span>
      </div>

      {view.trade_count === 0 ? (
        <p className="text-xs text-ag-text-muted">No trades in this period.</p>
      ) : hasFullMetrics ? (
        <ComparisonMetricGrid
          periods={[{ label: currentLabel, metrics: view.full_metrics }]}
        />
      ) : (
        /* Fallback to quick metrics */
        <QuickMetricsGrid metrics={view.metrics} />
      )}

      {/* Equity Curve */}
      {view.equity_curve.length >= 2 && <EquityCurve points={view.equity_curve} />}

      {/* Monthly Matrix */}
      {Object.keys(view.monthly_matrix).length > 0 && (
        <MonthlyMatrix matrix={view.monthly_matrix} />
      )}
    </div>
  );
}

/** Fallback quick metrics grid when full_metrics unavailable */
function QuickMetricsGrid({ metrics }: { metrics: Record<string, number | null> }) {
  const items = [
    { label: "Net Profit", value: metrics.net_profit, prefix: "$", color: (metrics.net_profit ?? 0) >= 0 ? "text-ag-green" : "text-ag-red" },
    { label: "Profit Factor", value: metrics.profit_factor },
    { label: "Win Rate", value: metrics.win_rate, suffix: "%" },
    { label: "Max Drawdown", value: metrics.max_drawdown, prefix: "$", color: "text-ag-red" },
    { label: "Avg Trade", value: metrics.avg_trade, prefix: "$", color: (metrics.avg_trade ?? 0) >= 0 ? "text-ag-green" : "text-ag-red" },
    { label: "Positions", value: metrics.trades ?? metrics.total_trades },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
      {items.map((item) => {
        const formatted = item.value != null ? Number(item.value).toFixed(2) : "\u2014";
        return (
          <div key={item.label} className="px-2 py-1.5 bg-ag-dark/50 rounded">
            <span className="text-ag-text-muted">{item.label}: </span>
            <span className={`font-mono font-medium ${item.color ?? ""}`}>
              {item.prefix}{formatted}{item.suffix}
            </span>
          </div>
        );
      })}
    </div>
  );
}
