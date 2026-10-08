"use client";

import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { LiveViewsResult } from "@/lib/api";
import { ComparisonMetricGrid } from "./metrics";
import type { PeriodData } from "./metrics";

interface Props {
  portfolioId: number;
}

type PeriodKey = "is" | "oos" | "live";

type LiveViewKey = "in_sample" | "oos" | "oos_full" | "live" | "since_saved" | "since_optimized";

const PERIOD_CONFIG: { key: PeriodKey; label: string; dataKey: LiveViewKey }[] = [
  { key: "is", label: "In-Sample", dataKey: "in_sample" },
  { key: "oos", label: "Out-of-Sample", dataKey: "oos_full" },
  { key: "live", label: "Live", dataKey: "live" },
];

export default function PortfolioFullAnalysisPanel({ portfolioId }: Props) {
  const [data, setData] = useState<LiveViewsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Set<PeriodKey>>(new Set(["is", "oos"]));

  useEffect(() => {
    setLoading(true);
    setError("");
    api.portfolioLiveViews(portfolioId)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }, [portfolioId]);

  const toggle = (key: PeriodKey) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        if (next.size > 1) next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  if (loading) {
    return <p className="text-xs text-ag-text-muted py-4">Loading full analysis...</p>;
  }
  if (error) {
    return <p className="text-xs text-ag-red py-4">{error}</p>;
  }
  if (!data) return null;

  // Build period data from selected toggles
  const periods: PeriodData[] = [];
  for (const cfg of PERIOD_CONFIG) {
    if (!selected.has(cfg.key)) continue;
    const view = data[cfg.dataKey];
    if (view?.full_metrics && Object.keys(view.full_metrics).length > 0) {
      periods.push({ label: cfg.label, metrics: view.full_metrics });
    }
  }

  return (
    <div className="space-y-4">
      {/* Period toggles */}
      <div className="flex items-center gap-3">
        <span className="text-xs text-ag-text-muted">Compare:</span>
        {PERIOD_CONFIG.map((cfg) => {
          const view = data[cfg.dataKey];
          const hasTrades = view && view.trade_count > 0;
          return (
            <label
              key={cfg.key}
              className={`flex items-center gap-1.5 text-xs cursor-pointer ${
                !hasTrades ? "opacity-40 cursor-not-allowed" : ""
              }`}
            >
              <input
                type="checkbox"
                checked={selected.has(cfg.key)}
                onChange={() => hasTrades && toggle(cfg.key)}
                disabled={!hasTrades}
                className="w-3.5 h-3.5 rounded accent-ag-accent"
              />
              <span className={selected.has(cfg.key) ? "text-ag-accent font-medium" : "text-ag-text-muted"}>
                {cfg.label}
              </span>
              {hasTrades && (
                <span className="text-[10px] text-ag-text-muted/60">
                  ({view.trade_count})
                </span>
              )}
            </label>
          );
        })}
      </div>

      {periods.length > 0 ? (
        <ComparisonMetricGrid periods={periods} showAll />
      ) : (
        <p className="text-xs text-ag-text-muted">No data for selected periods.</p>
      )}
    </div>
  );
}
