"use client";
import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import type { BatchBacktestRunItem, BacktestRunItem, StrategyItem, SweepRunItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import BatchHistoryView from "./BatchHistoryView";
import SingleHistoryView from "./SingleHistoryView";
import SweepHistoryView from "./SweepHistoryView";

type HistorySubTab = "batch" | "single" | "sweep";

export default function SavedBacktestsTab() {
  const { addToast } = useTaskManager();
  const [subTab, setSubTab] = useState<HistorySubTab>("batch");
  const [batches, setBatches] = useState<BatchBacktestRunItem[]>([]);
  const [runs, setRuns] = useState<BacktestRunItem[]>([]);
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [sweeps, setSweeps] = useState<SweepRunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showArchived, setShowArchived] = useState(false);
  // Per-source error tracking so one failed endpoint doesn't blank the whole tab.
  // Fix (2026-04-08): previous version used Promise.all which made this
  // all-or-nothing — a single slow/failed endpoint left every list empty.
  const [loadErrors, setLoadErrors] = useState<string[]>([]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadErrors([]);
    const results = await Promise.allSettled([
      api.batchRuns({ includeArchived: showArchived }),
      api.backtestRuns({ singleOnly: true }),
      api.strategies(),
      api.sweepRuns(),
    ]);
    const errs: string[] = [];
    const [bRes, rRes, sRes, swRes] = results;
    if (bRes.status === "fulfilled") setBatches(bRes.value);
    else errs.push(`Batches: ${bRes.reason instanceof Error ? bRes.reason.message : "failed to load"}`);
    if (rRes.status === "fulfilled") setRuns(rRes.value);
    else errs.push(`Single runs: ${rRes.reason instanceof Error ? rRes.reason.message : "failed to load"}`);
    if (sRes.status === "fulfilled") setStrategies(sRes.value);
    else errs.push(`Strategies: ${sRes.reason instanceof Error ? sRes.reason.message : "failed to load"}`);
    if (swRes.status === "fulfilled") setSweeps(swRes.value);
    else errs.push(`Sweeps: ${swRes.reason instanceof Error ? swRes.reason.message : "failed to load"}`);
    if (errs.length > 0) {
      setLoadErrors(errs);
      addToast("Some saved backtests failed to load — see banner", "error");
    }
    setLoading(false);
  }, [showArchived, addToast]);

  useEffect(() => { loadData(); }, [loadData]);

  if (loading) {
    return <p className="text-sm text-ag-text-muted py-8 text-center">Loading history...</p>;
  }

  return (
    <div className="space-y-4">
      {loadErrors.length > 0 && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300 flex items-start gap-3">
          <div className="flex-1">
            <div className="font-medium mb-1">Some data failed to load:</div>
            <ul className="list-disc list-inside text-xs space-y-0.5">
              {loadErrors.map((e, i) => (<li key={i}>{e}</li>))}
            </ul>
          </div>
          <button onClick={loadData} disabled={loading}
            className="text-xs px-2 py-1 rounded bg-red-500/20 hover:bg-red-500/30 border border-red-500/40 disabled:opacity-50 whitespace-nowrap">
            Retry
          </button>
        </div>
      )}
      <div className="flex items-center gap-3">
        <div className="flex gap-1 bg-ag-dark rounded-lg p-1 w-fit">
          <SubTabBtn active={subTab === "batch"} onClick={() => setSubTab("batch")}>
            Batch ({batches.length})
          </SubTabBtn>
          <SubTabBtn active={subTab === "single"} onClick={() => setSubTab("single")}>
            Single ({runs.filter((r) => !r.batch_id).length})
          </SubTabBtn>
          <SubTabBtn active={subTab === "sweep"} onClick={() => setSubTab("sweep")}>
            Sweep ({sweeps.length})
          </SubTabBtn>
        </div>
        <button onClick={loadData} disabled={loading}
          className="text-xs text-ag-text-muted hover:text-ag-text transition-colors disabled:opacity-50"
          title="Refresh list">
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {subTab === "batch" && (
        <BatchHistoryView batches={batches} strategies={strategies} onRefresh={loadData}
          showArchived={showArchived} onToggleArchived={() => setShowArchived((v) => !v)} />
      )}
      {subTab === "single" && (
        <SingleHistoryView runs={runs.filter((r) => !r.batch_id)} strategies={strategies} onRefresh={loadData} />
      )}
      {subTab === "sweep" && (
        <SweepHistoryView sweeps={sweeps} onRefresh={loadData} />
      )}
    </div>
  );
}

function SubTabBtn({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button onClick={onClick}
      className={`px-4 py-1.5 text-sm rounded-md font-medium transition-colors ${
        active ? "bg-ag-accent text-white" : "text-ag-text-muted hover:text-ag-text"
      }`}>
      {children}
    </button>
  );
}
