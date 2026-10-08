"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { FullMetricsRows, LSBreakdown, EquityCurve, MonthlyMatrix } from "./metrics";
import FilterableMonthlyPnL from "./FilterableMonthlyPnL";
import MonthTradesModal from "./MonthTradesModal";
import HintTip from "@/components/common/HintTip";

interface FullMetricsData {
  metrics: Record<string, number | string | null>;
  long_metrics: Record<string, number | string | null>;
  short_metrics: Record<string, number | string | null>;
  equity_curve: { date: string; equity: number }[];
  full_equity_curve?: { date: string; equity: number }[];
  monthly_matrix: Record<string, Record<string, number>>;
  optimization_end_date?: string | null;
  risk_adjust_applied?: boolean;
  risk_adjust_supported?: boolean;
}

interface Props {
  portfolioId: number;
  optimizationEndDate?: string | null;
  source?: "default" | "wfo";
}

export default function PortfolioFullMetrics({ portfolioId, optimizationEndDate, source = "default" }: Props) {
  const [data, setData] = useState<FullMetricsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [monthModal, setMonthModal] = useState<{ year: number; month: number; period: string } | null>(null);
  const [riskAdjust, setRiskAdjust] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError("");
    const loader = source === "wfo"
      ? api.portfolioFullMetricsWfo(portfolioId, riskAdjust)
      : api.portfolioFullMetrics(portfolioId);
    loader
      .then((d) => setData(d as FullMetricsData))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load metrics"))
      .finally(() => setLoading(false));
  }, [portfolioId, source, riskAdjust]);

  if (loading) return <p className="text-xs text-ag-text-muted py-2">Loading full metrics...</p>;
  if (error) return <p className="text-xs text-ag-red py-2">{error}</p>;
  if (!data || !Object.keys(data.metrics).length) {
    return <p className="text-xs text-ag-text-muted py-2">No trade data available.</p>;
  }

  // Use full equity curve (IS+OOS) when available for the split chart
  const eqPoints = data.full_equity_curve && data.full_equity_curve.length > 0
    ? data.full_equity_curve
    : data.equity_curve;
  const oosCutoff = optimizationEndDate || data.optimization_end_date || undefined;

  // Risk-adjust toggle (WFO source only). Toggling re-fetches with
  // ?risk_adjust=1; backend rescales DB $100-base PnLs to the portfolio's
  // user-defined risk. Martingale/Kelly are not yet adjusted — backend sets
  // risk_adjust_supported=false and we disable+explain in the HintTip.
  const supported = data.risk_adjust_supported !== false;
  const applied = !!data.risk_adjust_applied;

  return (
    <div className="space-y-4">
      {source === "wfo" && (
        <label className="flex items-center gap-2 text-xs text-ag-text-muted">
          <input
            type="checkbox"
            checked={riskAdjust}
            onChange={(e) => setRiskAdjust(e.target.checked)}
            disabled={riskAdjust && !supported}
            className="accent-ag-accent"
          />
          <span>
            Adjust PnL to user-defined risk
            {riskAdjust && !supported && (
              <span className="ml-2 text-ag-yellow">— not yet adjusted for this risk mode</span>
            )}
            {riskAdjust && applied && (
              <span className="ml-2 text-ag-green">— adjusted</span>
            )}
          </span>
          <HintTip text="Rescales the table from the DB's $100-per-trade base to the portfolio's actual risk setting. Supports fixed-$ and %-equity modes. Martingale and Kelly are not yet adjusted — toggling has no effect for those risk profiles. Does NOT affect live order sizing." />
        </label>
      )}

      <FullMetricsRows metrics={data.metrics} />

      {(Object.keys(data.long_metrics).length > 0 || Object.keys(data.short_metrics).length > 0) && (
        <LSBreakdown long={data.long_metrics} short={data.short_metrics} />
      )}

      {eqPoints.length > 0 && (
        <EquityCurve points={eqPoints} oosCutoffDate={oosCutoff} />
      )}

      {/* Monthly PnL — with filters if cutoff date exists, otherwise simple */}
      {oosCutoff ? (
        <FilterableMonthlyPnL
          portfolioId={portfolioId}
          cutoffDate={oosCutoff}
          mode="both"
          onCellClick={(year, month, period) => setMonthModal({ year, month, period })}
        />
      ) : (
        Object.keys(data.monthly_matrix).length > 0 && (
          <MonthlyMatrix
            matrix={data.monthly_matrix}
            onCellClick={(year, month) => setMonthModal({ year, month, period: "all" })}
          />
        )
      )}

      {/* Month Trades Modal — drill-down into individual trades */}
      {monthModal && (
        <MonthTradesModal
          portfolioId={portfolioId}
          year={monthModal.year}
          month={monthModal.month}
          cutoffDate={oosCutoff ?? null}
          period={monthModal.period}
          onClose={() => setMonthModal(null)}
        />
      )}
    </div>
  );
}
