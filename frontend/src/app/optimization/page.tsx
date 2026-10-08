"use client";
import React, { useState, useEffect, Suspense, lazy } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { BatchBacktestRunItem } from "@/lib/api";
import BatchGroupedSelector from "@/components/common/BatchGroupedSelector";

const OptimizerPanel = lazy(() => import("@/components/analysis/OptimizerPanel"));
const CrossStrategyBuilder = lazy(() => import("@/components/analysis/CrossStrategyBuilder"));
const WFOAssetSelectionTab = lazy(() => import("@/components/wfo/WFOAssetSelectionTab"));
const SavedRunsTab = lazy(() => import("@/components/analysis/SavedRunsTab"));

const SUB_TABS = [
  { id: "cross", label: "Cross-Strategy" },
  { id: "single", label: "Single Strategy" },
  { id: "wfas", label: "WFAS" },
  { id: "wfo", label: "WFO" },
  { id: "saved", label: "Saved Runs" },
] as const;

type SubTabId = (typeof SUB_TABS)[number]["id"];

const ENGINE_TABS = [
  { id: "current", label: "Principal" },
  { id: "fast", label: "Fast" },
  { id: "classic", label: "Classic" },
  { id: "og_v1", label: "OG v1 (Mar 11)" },
  { id: "og_v2", label: "OG v2 (Mar 12)" },
] as const;

type EngineId = (typeof ENGINE_TABS)[number]["id"];

function OptimizationContent() {
  const [subTab, setSubTab] = useState<SubTabId>("cross");
  const [engineTab, setEngineTab] = useState<EngineId>("fast");
  const [batches, setBatches] = useState<BatchBacktestRunItem[]>([]);
  const [selectedBatch, setSelectedBatch] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  // Persist OOS date across navigation so Reveal OOS still works after leaving/returning
  // Initialize empty to avoid hydration mismatch, then load from localStorage after mount
  const [oosDate, setOosDate] = useState("");
  const [oosHydrated, setOosHydrated] = useState(false);
  useEffect(() => {
    const saved = localStorage.getItem("optimization_oos_date") || "";
    if (saved) setOosDate(saved);
    setOosHydrated(true);
  }, []);
  useEffect(() => {
    if (oosHydrated) localStorage.setItem("optimization_oos_date", oosDate);
  }, [oosDate, oosHydrated]);

  useEffect(() => {
    api.batchRuns()
      .then((data) => {
        const completed = data.filter((b) => b.status === "completed");
        setBatches(completed);
        if (completed[0]) setSelectedBatch(completed[0].id);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const currentBatch = batches.find((b) => b.id === selectedBatch);
  const needsBatch = subTab === "single" || subTab === "cross";

  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <h1 className="text-2xl font-bold">Optimization</h1>
        <p className="text-ag-text-muted text-sm mt-1">
          Portfolio optimization, cross-strategy building, and walk-forward analysis.
        </p>
      </div>

      {/* Batch selector — shown for Single Strategy & Cross-Strategy */}
      {needsBatch && (
        loading ? (
          <span className="text-sm text-ag-text-muted">Loading...</span>
        ) : batches.length === 0 ? (
          <span className="text-sm text-ag-text-muted">No completed batch backtests.</span>
        ) : (
          <BatchGroupedSelector
            batches={batches}
            selectedBatch={selectedBatch}
            onSelect={(id) => setSelectedBatch(id)}
          />
        )
      )}

      {/* OOS Cutoff Date — shown for Single Strategy & Cross-Strategy */}
      {needsBatch && (
        <div className="flex items-center gap-2">
          <label className="text-xs text-ag-text-muted font-medium whitespace-nowrap">OOS Cutoff:</label>
          <input
            type="date"
            value={oosDate}
            onChange={(e) => setOosDate(e.target.value)}
            className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
          />
          {oosDate && (
            <button
              onClick={() => setOosDate("")}
              className="text-xs text-ag-text-muted hover:text-ag-red"
              title="Clear OOS date"
            >
              x
            </button>
          )}
          {oosDate && (
            <span className="text-[10px] text-ag-accent font-medium px-1.5 py-0.5 bg-ag-accent/10 rounded">
              IS only
            </span>
          )}
        </div>
      )}

      {/* Sub-tab navigation */}
      <div className="flex gap-1 border-b border-ag-border">
        {SUB_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setSubTab(t.id)}
            className={`px-4 py-2 text-sm transition-colors border-b-2 -mb-px ${
              subTab === t.id
                ? "border-ag-accent text-ag-accent"
                : "border-transparent text-ag-text-muted hover:text-ag-text"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Sub-tab content */}
      <Suspense
        fallback={
          <div className="flex items-center justify-center h-32">
            <Loader2 className="animate-spin text-ag-accent" size={24} />
          </div>
        }
      >
        {subTab === "single" && selectedBatch && currentBatch && (
          <OptimizerPanel
            batchId={selectedBatch}
            totalSymbols={currentBatch.completed_symbols}
            directionFilter={currentBatch.direction_filter}
            dateTo={oosDate || undefined}
            dataEndDate={currentBatch.data_end_date || undefined}
          />
        )}

        {subTab === "cross" && (
          <>
            {/* Engine sub-tabs */}
            <div className="flex items-center gap-2 mb-4">
              <span className="text-xs text-ag-text-muted font-medium mr-1">Engine:</span>
              {ENGINE_TABS.map((e) => (
                <button
                  key={e.id}
                  onClick={() => setEngineTab(e.id)}
                  className={`px-3 py-1 text-xs rounded-full transition-colors ${
                    engineTab === e.id
                      ? "bg-ag-accent text-white"
                      : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
                  }`}
                >
                  {e.label}
                </button>
              ))}
              <span className="text-[10px] text-ag-text-muted ml-2">
                {engineTab === "current" && "Verified-correct optimizer (default)"}
                {engineTab === "fast" && "Batch-vectorized scoring (faster, slight FP drift)"}
                {engineTab === "classic" && "Pre-2026-03-12 optimizer (another agent)"}
                {engineTab === "og_v1" && "Original search logic from Mar 11 (before all speed changes)"}
                {engineTab === "og_v2" && "Mar 12 search logic (numpy vectorization, no batch scoring)"}
              </span>
            </div>
            <CrossStrategyBuilder
              dateTo={oosDate || undefined}
              optimizerEngine={engineTab}
            />
          </>
        )}

        {subTab === "wfas" && (
          <WFOAssetSelectionTab />
        )}

        {subTab === "wfo" && (
          <div className="text-center py-16">
            <p className="text-ag-text-muted text-lg mb-2">Walk-Forward Optimization</p>
            <p className="text-ag-text-muted text-sm">Coming Soon</p>
          </div>
        )}

        {subTab === "saved" && <SavedRunsTab />}

        {/* Empty state when batch needed but none selected */}
        {needsBatch && !loading && !selectedBatch && (
          <div className="text-center py-12">
            <p className="text-ag-text-muted">
              Select a completed batch backtest to begin optimization.
            </p>
          </div>
        )}
      </Suspense>
    </div>
  );
}

export default function OptimizationPage() {
  return (
    <Suspense fallback={<div className="text-ag-text-muted p-6">Loading...</div>}>
      <OptimizationContent />
    </Suspense>
  );
}
