"use client";
import { useState, useCallback, useRef } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { WFOResult, EventWFOResult, WFORunItem } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import WFOConfigPanel from "./WFOConfigPanel";
import WFOWindowTimeline from "./WFOWindowTimeline";
import WFOResultsPanel from "./WFOResultsPanel";
import WFOWindowDetail from "./WFOWindowDetail";
import EventWFOResultsPanel from "./EventWFOResultsPanel";
import WFOSavedRunsTab from "./results/WFOSavedRunsTab";
import OptimizationProgress from "../common/OptimizationProgress";

type ResultType = WFOResult | EventWFOResult | null;
type ResultsTab = "windows" | "aggregated" | "tracks" | "saved";

function isEventResult(r: ResultType): r is EventWFOResult {
  return r !== null && "mode" in r && r.mode === "event";
}

export default function WFOAssetSelectionTab() {
  const [result, setResult] = useState<ResultType>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedWindow, setSelectedWindow] = useState<number | null>(null);
  const [lastConfig, setLastConfig] = useState<Record<string, unknown>>({});
  const [resultsTab, setResultsTab] = useState<ResultsTab>("windows");
  const [progressStep, setProgressStep] = useState(0);
  const [progressTotal, setProgressTotal] = useState(0);
  const [progressLabel, setProgressLabel] = useState("");
  const [bulkReplayProgress, setBulkReplayProgress] = useState<
    { current: number; total: number; currentRunId: number | null } | null
  >(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const configLoaderRef = useRef<((settings: Record<string, unknown>) => void) | null>(null);
  // Fix (2026-04-08): also consume loading/error so the Saved Runs tab can
  // distinguish "still loading" and "fetch failed" from "legitimately empty".
  // Previously the child only saw `data` and rendered "No saved WFO runs yet"
  // on every non-success state, making transient failures look permanent.
  const {
    data: savedRuns,
    loading: savedRunsLoading,
    error: savedRunsError,
    refetch: refetchRuns,
  } = useApi<WFORunItem[]>(
    useCallback(() => api.wfoRuns(), []),
    "wfo-runs",
  );

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const handleRun = async (config: Record<string, unknown>) => {
    setRunning(true);
    setError(null);
    setResult(null);
    setSelectedWindow(null);
    setLastConfig(config);
    setResultsTab("windows");
    setProgressStep(0);
    setProgressTotal(0);
    setProgressLabel("Loading trades...");

    const isBatchSelection = config.wfo_mode === "batch_selection";

    try {
      if (isBatchSelection) {
        // Async mode: start computation, poll for progress
        const { run_id } = await api.wfoAssetSelectionAsync(
          config as Parameters<typeof api.wfoAssetSelection>[0],
        );

        // Poll progress every 2s
        await new Promise<void>((resolve, reject) => {
          pollRef.current = setInterval(async () => {
            try {
              const prog = await api.wfoProgress(run_id);
              setProgressStep(prog.step);
              setProgressTotal(prog.total);
              setProgressLabel(prog.label);

              if (prog.done) {
                stopPolling();
                try {
                  const res = await api.wfoResult(run_id);
                  setResult(res);
                  refetchRuns();
                  resolve();
                } catch (e) {
                  reject(e);
                }
              }
            } catch (e) {
              stopPolling();
              reject(e);
            }
          }, 2000);
        });
      } else {
        // Synchronous mode (non-batch-selection)
        const res = await api.wfoAssetSelection(
          config as Parameters<typeof api.wfoAssetSelection>[0],
        );
        setResult(res);
        refetchRuns();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "WFO failed");
    }
    setRunning(false);
    setProgressStep(0);
    setProgressTotal(0);
    setProgressLabel("");
  };

  const handleLoadRun = async (run: WFORunItem) => {
    try {
      const detail = await api.wfoRunDetail(run.id);
      setResult(detail);
      setLastConfig(run.optimizer_settings as Record<string, unknown>);
      setResultsTab("windows");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load run");
    }
  };

  const handleRerun = async (runId: number) => {
    setRunning(true);
    setError(null);
    try {
      const detail = await api.wfoRerunLastWindow(runId);
      setResult(detail);
      setResultsTab("windows");
      refetchRuns();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Re-run failed");
    }
    setRunning(false);
  };

  // Replay Exact: fresh full run with the saved config and saved batch_ids.
  // Uses a dedicated backend endpoint that bypasses the form's lossy loader,
  // guaranteeing bit-for-bit reproducibility regardless of current form state
  // or newly added batches in the DB.
  const handleReplayExact = async (runId: number) => {
    setRunning(true);
    setError(null);
    try {
      const detail = await api.wfoReplayExact(runId);
      setResult(detail as typeof result);
      setResultsTab("windows");
      refetchRuns();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Replay Exact failed");
    }
    setRunning(false);
  };

  // Bulk replay: run each selected saved run as a fresh full optimization,
  // sequentially, so SQLite stays happy. Each one creates a NEW saved run
  // alongside the original. Errors on any single run are collected and
  // reported at the end; they do not stop the batch.
  const handleBulkReplayExact = async (runIds: number[]) => {
    if (runIds.length === 0) return;
    setError(null);
    setBulkReplayProgress({ current: 0, total: runIds.length, currentRunId: null });
    const failures: { id: number; err: string }[] = [];
    for (let i = 0; i < runIds.length; i++) {
      const id = runIds[i];
      setBulkReplayProgress({ current: i + 1, total: runIds.length, currentRunId: id });
      try {
        await api.wfoReplayExact(id);
        refetchRuns();
      } catch (e) {
        failures.push({ id, err: e instanceof Error ? e.message : "Replay failed" });
      }
    }
    setBulkReplayProgress(null);
    if (failures.length > 0) {
      setError(
        `Bulk replay finished with ${failures.length} failure(s):\n` +
        failures.map(f => `  Run #${f.id}: ${f.err}`).join("\n"),
      );
    }
  };

  const timeResult = !isEventResult(result) ? result : null;
  const selectedWindowData = timeResult?.windows.find(
    (w) => w.window_num === selectedWindow,
  ) ?? null;
  const timeMode = (lastConfig.time_mode as string) ?? "rolling";

  // Find the last OOS window number (for "Save for Live Trading" button)
  const lastOOSWindowNum = timeResult?.windows
    .filter(w => w.oos_start)
    .reduce<number | null>((max, w) => (max === null || w.window_num > max) ? w.window_num : max, null);

  const resultsTabs: { id: ResultsTab; label: string }[] = [
    { id: "windows", label: "Windows" },
    { id: "aggregated", label: "Aggregated" },
    { id: "tracks", label: "Tracks" },
    { id: "saved", label: "Saved Runs" },
  ];

  return (
    <div className="space-y-4">
      {/* Config panel — full width on top */}
      <WFOConfigPanel
        onRun={handleRun}
        running={running}
        onConfigLoaderReady={(loader) => { configLoaderRef.current = loader; }}
      />

      {/* Results below */}
      <div className="space-y-4">
        {running && (
          <div className="py-4">
            {progressTotal > 0 ? (
              <OptimizationProgress
                step={progressStep}
                totalSteps={progressTotal}
                label={progressLabel}
                isRunning={running}
              />
            ) : (
              <div className="flex flex-col items-center gap-3 justify-center py-12">
                <div className="flex items-center gap-3">
                  <Loader2 size={24} className="animate-spin text-ag-accent" />
                  <span className="text-sm text-ag-text-muted">
                    {progressLabel || "Loading trades and preparing WFO..."}
                  </span>
                </div>
                <span className="text-xs text-ag-text-muted">This may take several minutes depending on the number of windows and assets.</span>
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="bg-ag-red/10 border border-ag-red/30 rounded-lg p-4 text-sm text-ag-red">
            {error}
          </div>
        )}

        {/* Event-based results (standalone, non-hybrid) */}
        {isEventResult(result) && !running && (
          <EventWFOResultsPanel result={result} />
        )}

        {/* Time-based results: tabbed */}
        {(timeResult || (!result && !running && !error)) && (
          <div className="bg-ag-card border border-ag-border rounded-xl">
            {/* Tab bar */}
            <div className="flex items-center gap-1 border-b border-ag-border/50 px-4 pt-3">
              {resultsTabs.map((t) => (
                <button key={t.id} onClick={() => setResultsTab(t.id)}
                  className={`px-3 py-1.5 text-xs rounded-t transition-colors ${
                    resultsTab === t.id
                      ? "bg-ag-accent/15 text-ag-accent font-medium"
                      : "text-ag-text-muted hover:text-ag-text"
                  }`}>
                  {t.label}
                </button>
              ))}
            </div>

            <div className="p-4">
              {/* Windows tab */}
              {resultsTab === "windows" && timeResult && !running && (
                <div className="space-y-4">
                  <WFOWindowTimeline
                    windows={timeResult.windows}
                    selectedWindow={selectedWindow}
                    onSelectWindow={(num) => setSelectedWindow(
                      selectedWindow === num ? null : num,
                    )}
                  />
                  {selectedWindowData && (
                    <WFOWindowDetail
                      window={selectedWindowData}
                      onClose={() => setSelectedWindow(null)}
                      timeMode={timeMode}
                      isLastOOSWindow={selectedWindowData.window_num === lastOOSWindowNum}
                      wfoConfig={lastConfig}
                      wfoRunId={timeResult?.wfo_run_id}
                    />
                  )}
                </div>
              )}

              {/* Aggregated tab */}
              {resultsTab === "aggregated" && timeResult && !running && (
                <WFOResultsPanel result={timeResult} wfoConfig={lastConfig} />
              )}

              {/* Tracks tab */}
              {resultsTab === "tracks" && timeResult && !running && (
                <WFOResultsPanel result={timeResult} wfoConfig={lastConfig} showTracksOnly />
              )}

              {/* Saved Runs tab */}
              {resultsTab === "saved" && (
                <WFOSavedRunsTab
                  runs={savedRuns ?? []}
                  loading={savedRunsLoading}
                  error={savedRunsError}
                  onRefresh={refetchRuns}
                  onLoad={handleLoadRun}
                  onRerun={handleRerun}
                  onReplayExact={handleReplayExact}
                  onBulkReplayExact={handleBulkReplayExact}
                  bulkReplayProgress={bulkReplayProgress}
                  onDelete={async (id) => {
                    await api.wfoDeleteRun(id);
                    refetchRuns();
                  }}
                  onReapplyConfig={(settings) => {
                    if (configLoaderRef.current) {
                      configLoaderRef.current(settings);
                      setResultsTab("windows");
                    }
                  }}
                  running={running}
                />
              )}

              {/* Empty state */}
              {resultsTab !== "saved" && !timeResult && !running && !error && (
                <div className="text-center py-8">
                  <p className="text-ag-text-muted text-sm">
                    Configure WFO settings and click Run to start.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* 2-Step Hybrid: Step 2 Event-Based Results */}
        {timeResult?.step2 && !running && (
          <div className="bg-ag-card border-2 border-ag-accent/30 rounded-xl">
            <div className="px-4 py-3 border-b border-ag-accent/20 flex items-center gap-3">
              <span className="text-sm font-bold text-ag-accent">Step 2 — Event-Based Asset Selection</span>
              <span className="text-xs text-ag-text-muted">
                Winning batches: [{timeResult.step2.winning_batches.join(", ")}]
              </span>
            </div>
            <div className="p-4">
              <EventWFOResultsPanel result={timeResult.step2} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
