"use client";

import { useState, useEffect } from "react";
import { api, TVStrategyListItem, DashboardAnalysisRequest } from "@/lib/api";
import MetricsPanel from "@/components/tv-analysis/MetricsPanel";
import EquityCurveChart from "@/components/tv-analysis/EquityCurveChart";
import MonthlyMatrix from "@/components/tv-analysis/MonthlyMatrix";
import AssetBreakdown from "@/components/tv-analysis/AssetBreakdown";
import RobustnessPanel from "@/components/tv-analysis/RobustnessPanel";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "monthly", label: "Monthly" },
  { id: "assets", label: "Assets" },
  { id: "robustness", label: "Robustness" },
];

export default function TVAnalysisPage() {
  const [strategies, setStrategies] = useState<TVStrategyListItem[]>([]);
  const [selected, setSelected] = useState("");
  const [sideFilter, setSideFilter] = useState("All");
  const [activeTab, setActiveTab] = useState("overview");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.tvStrategies().then((r) => setStrategies(r.strategies)).catch(() => {});
  }, []);

  const req: DashboardAnalysisRequest = {
    strategy_filename: selected || undefined,
    side_filter: sideFilter,
  };

  const hasStrategy = selected.length > 0;

  return (
    <div className="space-y-4">
      {/* Strategy selector + filters */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <label className="text-sm text-ag-text-muted">Strategy:</label>
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            className="bg-ag-card border border-ag-border rounded px-3 py-1.5 text-sm text-ag-text min-w-[200px]"
          >
            <option value="">Select strategy...</option>
            {strategies.map((s) => (
              <option key={s.filename} value={s.filename}>
                {s.name}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label className="text-sm text-ag-text-muted">Side:</label>
          <select
            value={sideFilter}
            onChange={(e) => setSideFilter(e.target.value)}
            className="bg-ag-card border border-ag-border rounded px-3 py-1.5 text-sm text-ag-text"
          >
            <option value="All">All</option>
            <option value="Long">Long</option>
            <option value="Short">Short</option>
          </select>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-ag-border">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.id
                ? "border-ag-accent text-ag-accent"
                : "border-transparent text-ag-text-muted hover:text-ag-text"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Content */}
      {!hasStrategy ? (
        <div className="bg-ag-card border border-ag-border rounded-lg p-12 text-center">
          <p className="text-ag-text-muted">Select a strategy to view analysis</p>
        </div>
      ) : (
        <>
          {activeTab === "overview" && (
            <div className="space-y-4">
              <MetricsPanel req={req} />
              <EquityCurveChart req={req} />
            </div>
          )}
          {activeTab === "monthly" && <MonthlyMatrix req={req} />}
          {activeTab === "assets" && <AssetBreakdown req={req} />}
          {activeTab === "robustness" && <RobustnessPanel req={req} />}
        </>
      )}
    </div>
  );
}
