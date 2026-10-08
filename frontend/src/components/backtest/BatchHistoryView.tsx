"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { ChevronDown, ChevronRight, Pencil, Trash2, Merge, RefreshCw, Archive, ArchiveRestore, PackageMinus, FolderOpen, Plus, X, Settings, MoveRight, Hammer } from "lucide-react";
import { api } from "@/lib/api";
import type { BatchBacktestRunItem, StrategyItem, BatchGroupItem, RebuildProgress } from "@/lib/api";
import BatchExpandedDetail from "./BatchExpandedDetail";
import HintTip from "@/components/common/HintTip";

interface Props {
  batches: BatchBacktestRunItem[];
  strategies: StrategyItem[];
  onRefresh: () => void;
  showArchived?: boolean;
  onToggleArchived?: () => void;
}

interface StrategyGroup {
  strategyId: number;
  strategyName: string;
  configs: ConfigGroup[];
}
interface TfGroup {
  tf: string;
  batches: BatchBacktestRunItem[];
}
interface ConfigGroup {
  configKey: string;
  configLabel: string;
  batches: BatchBacktestRunItem[];
  tfGroups: TfGroup[];
}

function extractConfig(b: BatchBacktestRunItem): { key: string; label: string } {
  if (b.config_group) return { key: b.config_group, label: b.config_group };
  const name = b.name || "";
  const sep = name.indexOf(" \u2014 ");
  if (sep > 0) {
    const configPart = name.substring(sep + 3);
    return { key: configPart, label: configPart };
  }
  return { key: "Default", label: "Default" };
}

// Sort TFs numerically: 1,3,5,15,30,60,120,240,360,720,D,W
function tfSortKey(tf: string | undefined): number {
  if (!tf) return 9999;
  const s = String(tf).replace("m", "");
  if (s === "D") return 1440;
  if (s === "W") return 10080;
  return parseInt(s) || 9999;
}

function buildGroups(batches: BatchBacktestRunItem[], strategies: StrategyItem[]): StrategyGroup[] {
  const stratMap = new Map(strategies.map((s) => [s.id, s.name]));
  const groups = new Map<number, { name: string; configs: Map<string, BatchBacktestRunItem[]> }>();
  for (const b of batches) {
    if (!groups.has(b.strategy_id)) {
      groups.set(b.strategy_id, { name: stratMap.get(b.strategy_id) || `Strategy #${b.strategy_id}`, configs: new Map() });
    }
    const g = groups.get(b.strategy_id)!;
    const { key } = extractConfig(b);
    if (!g.configs.has(key)) g.configs.set(key, []);
    g.configs.get(key)!.push(b);
  }
  const result: StrategyGroup[] = [];
  for (const [sid, g] of groups) {
    const configs: ConfigGroup[] = [];
    for (const [key, bList] of g.configs) {
      bList.sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime());
      // Build TF sub-groups within this config
      const tfMap = new Map<string, BatchBacktestRunItem[]>();
      for (const b of bList) {
        const tf = b.timeframe || "?";
        if (!tfMap.has(tf)) tfMap.set(tf, []);
        tfMap.get(tf)!.push(b);
      }
      const tfGroups: TfGroup[] = Array.from(tfMap.entries())
        .sort((a, b) => tfSortKey(a[0]) - tfSortKey(b[0]))
        .map(([tf, batches]) => ({ tf, batches }));
      configs.push({ configKey: key, configLabel: key, batches: bList, tfGroups });
    }
    result.push({ strategyId: sid, strategyName: g.name, configs });
  }
  return result.sort((a, b) => a.strategyName.localeCompare(b.strategyName));
}

export default function BatchHistoryView({ batches, strategies, onRefresh, showArchived, onToggleArchived }: Props) {
  const groups = buildGroups(batches, strategies);
  // All accordions open by default
  const [expandedStrategies, setExpandedStrategies] = useState<Set<number>>(
    () => new Set(groups.map((g) => g.strategyId))
  );
  const [expandedConfigs, setExpandedConfigs] = useState<Set<string>>(
    () => new Set(groups.flatMap((g) => g.configs.map((c) => `${g.strategyId}-${c.configKey}`)))
  );
  const [selectedForMerge, setSelectedForMerge] = useState<Set<number>>(new Set());
  const [merging, setMerging] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Batch groups state
  const [batchGroups, setBatchGroups] = useState<BatchGroupItem[]>([]);
  const [showGroupPanel, setShowGroupPanel] = useState(false);
  const [editingGroup, setEditingGroup] = useState<BatchGroupItem | null>(null);
  const [assigningToGroup, setAssigningToGroup] = useState(false);

  // Stale batch rebuild state (Live Loop — for batches made before strategy_state existed)
  const [staleCount, setStaleCount] = useState<{ batch_count: number; run_count: number } | null>(null);
  const [rebuild, setRebuild] = useState<RebuildProgress | null>(null);
  const [rebuildStarting, setRebuildStarting] = useState(false);
  const rebuildPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchGroups = useCallback(() => {
    api.batchGroups().then(setBatchGroups).catch(() => setBatchGroups([]));
  }, []);
  useEffect(() => { fetchGroups(); }, [fetchGroups]);

  const fetchStale = useCallback(() => {
    api.getStaleBatches()
      .then((s) => setStaleCount({ batch_count: s.batch_count, run_count: s.run_count }))
      .catch(() => setStaleCount(null));
  }, []);

  const pollRebuild = useCallback(async () => {
    try {
      const p = await api.getRebuildStatus();
      setRebuild(p);
      if (p.status !== "running" && rebuildPollRef.current) {
        clearInterval(rebuildPollRef.current);
        rebuildPollRef.current = null;
        fetchStale();
        onRefresh();
      }
    } catch { /* non-fatal */ }
  }, [fetchStale, onRefresh]);

  useEffect(() => {
    fetchStale();
    api.getRebuildStatus().then((p) => {
      setRebuild(p);
      if (p.status === "running" && !rebuildPollRef.current) {
        rebuildPollRef.current = setInterval(pollRebuild, 3000);
      }
    }).catch(() => { /* non-fatal */ });
    return () => {
      if (rebuildPollRef.current) clearInterval(rebuildPollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startRebuild = async (downloadCandles: boolean) => {
    if (!staleCount || staleCount.batch_count === 0) return;
    const msg = downloadCandles
      ? `Sync candles from Bybit AND rebuild ${staleCount.run_count} stale runs across ${staleCount.batch_count} batches?\n\n` +
        `Candle download is append-only (no re-download — Market Structure dashboard continuity preserved).\n` +
        `Rebuild replays each backtest end-to-end to capture strategy_state. Your batch_ids are preserved.\n` +
        `Can take 10-30+ minutes.`
      : `Rebuild ${staleCount.run_count} stale runs across ${staleCount.batch_count} batches?\n\n` +
        `Replays each backtest using candles already in the DB. Your batch_ids are preserved.`;
    if (!confirm(msg)) return;
    setRebuildStarting(true);
    try {
      const resp = await api.startRebuildStaleBatches(downloadCandles);
      setRebuild(resp.progress);
      if (!rebuildPollRef.current) {
        rebuildPollRef.current = setInterval(pollRebuild, 3000);
      }
    } catch (e) {
      alert(e instanceof Error ? e.message : "Rebuild failed to start");
    } finally {
      setRebuildStarting(false);
    }
  };

  const groupMap = new Map(batchGroups.map((g) => [g.id, g]));

  const handleBulkAssign = async (groupId: number | null) => {
    if (selectedForMerge.size === 0) return;
    setAssigningToGroup(true);
    try {
      await api.batchGroupAssign({ batch_ids: Array.from(selectedForMerge), group_id: groupId });
      setSelectedForMerge(new Set());
      onRefresh();
      fetchGroups();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Assign failed");
    } finally {
      setAssigningToGroup(false);
    }
  };

  const toggleMergeSelect = (id: number) => {
    setSelectedForMerge((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleMerge = async () => {
    if (selectedForMerge.size < 2) return;
    setMerging(true);
    try {
      await api.mergeBatches(Array.from(selectedForMerge));
      setSelectedForMerge(new Set());
      onRefresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Merge failed");
    } finally {
      setMerging(false);
    }
  };

  const handleBulkDelete = async () => {
    if (selectedForMerge.size === 0) return;
    const count = selectedForMerge.size;
    if (!confirm(`Delete ${count} selected batch${count !== 1 ? "es" : ""}? This removes all their runs and trades.`)) return;
    setDeleting(true);
    try {
      for (const id of selectedForMerge) {
        await api.deleteBatchRun(id);
      }
      setSelectedForMerge(new Set());
      onRefresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Delete failed");
      onRefresh();
    } finally {
      setDeleting(false);
    }
  };

  const handleBulkArchive = async () => {
    if (selectedForMerge.size === 0) return;
    try {
      for (const id of selectedForMerge) {
        await api.archiveBatch(id);
      }
      setSelectedForMerge(new Set());
      onRefresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Archive failed");
      onRefresh();
    }
  };

  const handleBulkCompact = async () => {
    if (selectedForMerge.size === 0) return;
    const count = selectedForMerge.size;
    if (!confirm(`Compact ${count} batch${count !== 1 ? "es" : ""}? This archives them and permanently deletes all individual trade data (keeps KPIs + config for re-run).`)) return;
    try {
      for (const id of selectedForMerge) {
        await api.compactBatch(id);
      }
      setSelectedForMerge(new Set());
      onRefresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Compact failed");
      onRefresh();
    }
  };

  const archivedCount = batches.filter((b) => b.is_archived).length;

  if (groups.length === 0) {
    return (
      <div className="space-y-2">
        {onToggleArchived && (
          <div className="flex justify-end">
            <button onClick={onToggleArchived}
              className="flex items-center gap-1.5 text-xs text-ag-text-muted hover:text-ag-text transition-colors">
              <Archive size={12} />
              {showArchived ? "Hide Archived" : "Show Archived"}
            </button>
          </div>
        )}
        <p className="text-sm text-ag-text-muted py-4">No batch backtests found.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Top bar: groups + archive toggle + cleanup */}
      {onToggleArchived && (
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <button onClick={() => setShowGroupPanel(!showGroupPanel)}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg border transition-colors ${
                showGroupPanel ? "bg-ag-accent/15 border-ag-accent/40 text-ag-accent" : "border-ag-border text-ag-text-muted hover:text-ag-text hover:border-ag-border/80"
              }`}
              title="Manage batch groups — organize batches into named groups with shared trading parameters">
              <FolderOpen size={12} />
              Groups
              {batchGroups.length > 0 && <span className="text-[10px] opacity-60">({batchGroups.length})</span>}
            </button>
          </div>
          <div className="flex gap-3">
            <button onClick={async () => {
                if (!confirm("Remove duplicate batches? (Same name, TF, direction, created within 1 minute — keeps the newest of each group)")) return;
                try {
                  const res = await api.cleanupDuplicateBatches();
                  alert(`Removed ${res.duplicates_removed} duplicate batch${res.duplicates_removed !== 1 ? "es" : ""} (${res.child_runs_removed} child runs)`);
                  onRefresh();
                } catch (e) { alert(e instanceof Error ? e.message : "Cleanup failed"); }
              }}
              className="flex items-center gap-1.5 text-xs text-ag-text-muted hover:text-orange-400 transition-colors"
              title="Find and remove duplicate batches (same name+TF+direction created within 1 minute, keeps newest)">
              <Trash2 size={12} />
              Clean Duplicates
            </button>
            <button onClick={onToggleArchived}
              className="flex items-center gap-1.5 text-xs text-ag-text-muted hover:text-ag-text transition-colors">
              <Archive size={12} />
              {showArchived ? `Hide Archived (${archivedCount})` : "Show Archived"}
            </button>
          </div>
        </div>
      )}

      {/* Stale batch rebuild banner (Live Loop — pre-strategy_state batches) */}
      {((staleCount && staleCount.batch_count > 0) || (rebuild && rebuild.status !== "idle")) && (
        <div className="bg-ag-yellow/5 border border-ag-yellow/30 rounded-lg p-3 space-y-2">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-2">
              <Hammer size={14} className="text-ag-yellow" />
              <span className="text-xs font-semibold text-ag-yellow">
                Stale batches (need one-off rebuild for Live Loop)
              </span>
              <HintTip text="These batches were created before the Live Loop existed, so their runs don't have strategy_state captured. The nightly L1 pipeline skips them until rebuilt. Click Sync + Rebuild to (a) download any missing candles from Bybit (append-only — Market Structure continuity preserved), then (b) replay each backtest end-to-end to capture strategy_state. Batch_ids and portfolio bindings are preserved throughout. Same (symbol, TF) loaded once across all runs via a shared candle cache — 3-5× faster than loading per run." />
            </div>
            {staleCount && staleCount.batch_count > 0 && (
              <span className="text-xs text-ag-text-muted">
                {staleCount.run_count} runs across {staleCount.batch_count} batches
              </span>
            )}
          </div>

          {rebuild && rebuild.status !== "idle" && (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs text-ag-text-muted">
                <span>
                  {rebuild.status === "running" && (
                    rebuild.phase === "downloading"
                      ? "⬇ Downloading new candles from Bybit…"
                      : rebuild.phase === "loading_candles"
                        ? `⚡ Preloading candles (${rebuild.candles_cached} cached)…`
                        : `Rebuilding: ${rebuild.current || "…"}`
                  )}
                  {rebuild.status === "completed" && (
                    <>
                      ✓ Rebuild complete — {rebuild.completed_runs} runs
                      {rebuild.failed_runs > 0 && <span className="text-ag-red"> ({rebuild.failed_runs} failed)</span>}
                      {rebuild.l0_summary && typeof rebuild.l0_summary.new_candles === "number" && (
                        <> · {rebuild.l0_summary.new_candles} new candles downloaded</>
                      )}
                    </>
                  )}
                  {rebuild.status === "error" && `⚠ ${rebuild.current}`}
                </span>
                {rebuild.phase === "rebuilding" && (
                  <span>
                    {rebuild.completed_runs + rebuild.failed_runs} / {rebuild.total_runs}
                    {rebuild.failed_runs > 0 && (
                      <span className="text-ag-red"> ({rebuild.failed_runs} failed)</span>
                    )}
                  </span>
                )}
              </div>
              <div className="w-full h-1.5 bg-ag-dark border border-ag-border rounded overflow-hidden">
                <div
                  className={
                    rebuild.status === "error"
                      ? "h-full bg-ag-red"
                      : rebuild.status === "completed"
                        ? "h-full bg-ag-green"
                        : "h-full bg-ag-yellow transition-all"
                  }
                  style={{
                    width: rebuild.status === "completed"
                      ? "100%"
                      : rebuild.total_runs > 0 && rebuild.phase === "rebuilding"
                        ? `${Math.min(100, Math.round(((rebuild.completed_runs + rebuild.failed_runs) / rebuild.total_runs) * 100))}%`
                        : rebuild.phase === "downloading" ? "15%"
                          : rebuild.phase === "loading_candles" ? "30%"
                            : "5%",
                  }}
                />
              </div>
            </div>
          )}

          {staleCount && staleCount.batch_count > 0 && (!rebuild || rebuild.status !== "running") && (
            <div className="flex gap-2 flex-wrap">
              <button
                onClick={() => startRebuild(true)}
                disabled={rebuildStarting}
                title="Download any missing candles (append-only) AND rebuild stale batches. Recommended for a first run or after a long gap."
                className="flex items-center gap-1 bg-ag-green/15 border border-ag-green/40 text-ag-green px-3 py-1.5 rounded text-xs hover:bg-ag-green/25 disabled:opacity-50"
              >
                <Hammer size={13} />
                Sync Candles + Rebuild Stale Batches
              </button>
              <button
                onClick={() => startRebuild(false)}
                disabled={rebuildStarting}
                title="Rebuild using candles already in the DB (skip the Bybit download step). Faster if you ran candle sync recently."
                className="flex items-center gap-1 bg-ag-yellow/15 border border-ag-yellow/40 text-ag-yellow px-3 py-1.5 rounded text-xs hover:bg-ag-yellow/25 disabled:opacity-50"
              >
                <Hammer size={13} />
                Rebuild Only (skip download)
              </button>
            </div>
          )}
        </div>
      )}

      {/* Batch Groups Management Panel */}
      {showGroupPanel && (
        <BatchGroupPanel
          groups={batchGroups}
          onRefresh={() => { fetchGroups(); onRefresh(); }}
          editingGroup={editingGroup}
          onEditGroup={setEditingGroup}
        />
      )}
      {/* Merge bar */}
      {selectedForMerge.size > 0 && (
        <div className="flex items-center justify-between bg-ag-accent/10 border border-ag-accent/30 rounded-lg px-4 py-2">
          <span className="text-sm text-ag-accent">
            {selectedForMerge.size} batch{selectedForMerge.size !== 1 ? "es" : ""} selected
          </span>
          <div className="flex gap-2">
            <button onClick={() => setSelectedForMerge(new Set())}
              className="text-xs text-ag-text-muted hover:text-ag-text px-2 py-1"
              title="Deselect all batches">
              Cancel
            </button>
            {/* Assign to group dropdown */}
            {batchGroups.length > 0 && (
              <div className="relative group/assign">
                <button
                  disabled={assigningToGroup}
                  className="flex items-center gap-1.5 text-xs bg-ag-accent/20 hover:bg-ag-accent/40 text-ag-accent px-3 py-1 rounded disabled:opacity-50"
                  title="Assign selected batches to a group">
                  <FolderOpen size={12} />
                  {assigningToGroup ? "Assigning..." : "Assign to Group"}
                  <ChevronDown size={10} />
                </button>
                <div className="hidden group-hover/assign:block absolute top-full left-0 mt-0.5 z-50 bg-ag-card border border-ag-border rounded-lg shadow-xl py-1 min-w-[140px]">
                  {batchGroups.map((g) => (
                    <button key={g.id} onClick={() => handleBulkAssign(g.id)}
                      className="w-full text-left px-3 py-1.5 text-xs hover:bg-ag-dark/60 flex items-center gap-2">
                      <FolderOpen size={10} className="text-ag-accent/60" />
                      {g.name}
                    </button>
                  ))}
                  <div className="border-t border-ag-border/30 my-0.5" />
                  <button onClick={() => handleBulkAssign(null)}
                    className="w-full text-left px-3 py-1.5 text-xs hover:bg-ag-dark/60 text-ag-text-muted">
                    Remove from group
                  </button>
                </div>
              </div>
            )}
            <button onClick={handleBulkArchive}
              className="flex items-center gap-1.5 text-xs bg-ag-accent/20 hover:bg-ag-accent/40 text-ag-accent px-3 py-1 rounded"
              title="Archive selected batches (hide from default view)">
              <Archive size={12} />
              Archive
            </button>
            <button onClick={handleBulkCompact}
              className="flex items-center gap-1.5 text-xs bg-orange-500/20 hover:bg-orange-500/40 text-orange-400 px-3 py-1 rounded"
              title="Archive + delete trade data (keeps KPIs + config for re-run, saves ~3.5 MB each)">
              <PackageMinus size={12} />
              Compact
            </button>
            <button onClick={handleBulkDelete}
              disabled={deleting}
              className="flex items-center gap-1.5 text-xs bg-ag-red/80 hover:bg-ag-red text-white px-3 py-1 rounded disabled:opacity-50"
              title="Permanently delete all selected batches and their runs/trades">
              <Trash2 size={12} />
              {deleting ? "Deleting..." : "Delete Selected"}
            </button>
            <button onClick={handleMerge}
              disabled={selectedForMerge.size < 2 || merging}
              className="flex items-center gap-1.5 text-xs bg-ag-accent text-white px-3 py-1 rounded disabled:opacity-50"
              title="Combine selected batches into a single merged batch (requires 2+)">
              <Merge size={12} />
              {merging ? "Merging..." : "Merge Selected"}
            </button>
          </div>
        </div>
      )}

      {groups.map((g) => {
        const isExpanded = expandedStrategies.has(g.strategyId);
        const totalBatches = g.configs.reduce((sum, c) => sum + c.batches.length, 0);
        const toggleStrategy = () => {
          setExpandedStrategies((prev) => {
            const next = new Set(prev);
            if (next.has(g.strategyId)) next.delete(g.strategyId);
            else next.add(g.strategyId);
            return next;
          });
        };
        return (
          <div key={g.strategyId} className="bg-ag-card border border-ag-border rounded-lg">
            <button onClick={toggleStrategy}
              className="w-full flex items-center justify-between px-4 py-3 hover:bg-ag-card-hover transition-colors rounded-lg">
              <div className="flex items-center gap-2">
                {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span className="text-sm font-medium">{g.strategyName}</span>
                <span className="text-xs text-ag-text-muted">
                  {totalBatches} batch{totalBatches !== 1 ? "es" : ""} · {g.configs.length} config{g.configs.length !== 1 ? "s" : ""}
                </span>
              </div>
            </button>
            {isExpanded && (
              <div className="border-t border-ag-border">
                {g.configs.map((c) => {
                  const cfgKey = `${g.strategyId}-${c.configKey}`;
                  const cfgExpanded = expandedConfigs.has(cfgKey);
                  const toggleConfig = () => {
                    setExpandedConfigs((prev) => {
                      const next = new Set(prev);
                      if (next.has(cfgKey)) next.delete(cfgKey);
                      else next.add(cfgKey);
                      return next;
                    });
                  };
                  return (
                    <div key={cfgKey} className="border-b border-ag-border/30 last:border-b-0">
                      <button onClick={toggleConfig}
                        className="w-full flex items-center justify-between px-6 py-2 text-xs hover:bg-ag-card-hover transition-colors">
                        <div className="flex items-center gap-2">
                          {cfgExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                          <span className="font-medium">{c.configLabel}</span>
                          <span className="text-ag-text-muted">{c.batches.length} run{c.batches.length !== 1 ? "s" : ""}</span>
                        </div>
                      </button>
                      {cfgExpanded && (
                        <div className="px-8 pb-2 space-y-1">
                          {c.tfGroups.length > 1 ? (
                            /* Multiple TFs — show TF sub-headers */
                            c.tfGroups.map((tfg) => (
                              <div key={tfg.tf}>
                                <div className="text-[10px] text-ag-text-muted font-medium uppercase mt-2 mb-0.5 border-b border-ag-border/20 pb-0.5">
                                  {tfg.tf}m · {tfg.batches.length} run{tfg.batches.length !== 1 ? "s" : ""}
                                </div>
                                {tfg.batches.map((b) => (
                                  <BatchRow key={b.id} batch={b} onRefresh={() => { onRefresh(); fetchGroups(); }}
                                    mergeSelected={selectedForMerge.has(b.id)}
                                    onToggleMerge={() => toggleMergeSelect(b.id)}
                                    groupMap={groupMap} batchGroups={batchGroups} />
                                ))}
                              </div>
                            ))
                          ) : (
                            /* Single TF — flat list as before */
                            c.batches.map((b) => (
                              <BatchRow key={b.id} batch={b} onRefresh={() => { onRefresh(); fetchGroups(); }}
                                mergeSelected={selectedForMerge.has(b.id)}
                                onToggleMerge={() => toggleMergeSelect(b.id)}
                                groupMap={groupMap} batchGroups={batchGroups} />
                            ))
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// --- Batch Group Management Panel ---

function BatchGroupPanel({
  groups, onRefresh, editingGroup, onEditGroup,
}: {
  groups: BatchGroupItem[];
  onRefresh: () => void;
  editingGroup: BatchGroupItem | null;
  onEditGroup: (g: BatchGroupItem | null) => void;
}) {
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [delConfirm, setDelConfirm] = useState<number | null>(null);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    setError("");
    try {
      await api.batchGroupCreate({ name: newName.trim() });
      setNewName("");
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create group");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.batchGroupDelete(id);
      setDelConfirm(null);
      if (editingGroup?.id === id) onEditGroup(null);
      onRefresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Delete failed");
    }
  };

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ag-text flex items-center gap-2">
          <FolderOpen size={14} className="text-ag-accent" />
          Batch Groups
          <HintTip text="Organize batches into named groups (A, B, C...) with shared trading parameters like position size, fees, and slippage. Co-Work sessions use these groups to load the right batches with correct PnL scaling." />
        </h3>
      </div>

      {/* Create new group */}
      <div className="flex gap-2">
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          placeholder="New group name (e.g. C, D, ...)"
          className="flex-1 bg-ag-dark border border-ag-border rounded-lg px-2.5 py-1.5 text-xs"
        />
        <button
          onClick={handleCreate}
          disabled={creating || !newName.trim()}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-ag-accent text-white text-xs font-medium hover:bg-ag-accent/80 disabled:opacity-50"
        >
          <Plus size={12} />
          Create
        </button>
      </div>
      {error && <p className="text-red-400 text-[10px]">{error}</p>}

      {/* Group cards */}
      {groups.length === 0 ? (
        <p className="text-ag-text-muted/60 text-center py-3 text-xs">No groups yet. Create one to start organizing your batches.</p>
      ) : (
        <div className="space-y-2">
          {groups.map((g) => (
            <div key={g.id} className={`rounded-lg border transition-colors ${
              editingGroup?.id === g.id ? "border-ag-accent/50 bg-ag-accent/5" : "border-ag-border/50 bg-ag-dark/30"
            }`}>
              {/* Group header row */}
              <div className="flex items-center justify-between px-3 py-2">
                <div className="flex items-center gap-3">
                  <FolderOpen size={14} className="text-ag-accent/60" />
                  <span className="text-sm font-medium">{g.name}</span>
                  <span className="text-[10px] text-ag-text-muted">
                    {g.batch_count} batch{g.batch_count !== 1 ? "es" : ""}
                  </span>
                  {/* Trading params summary */}
                  <div className="flex gap-1.5">
                    {g.timeframe && (
                      <span className="text-[10px] bg-blue-500/15 text-blue-400 px-1.5 py-0.5 rounded">{g.timeframe}</span>
                    )}
                    {g.bybit_limit != null && (
                      <span className="text-[10px] bg-green-500/15 text-green-400 px-1.5 py-0.5 rounded">${g.bybit_limit}</span>
                    )}
                    {(g.slippage_pct > 0 || g.fee_pct > 0) && (
                      <span className="text-[10px] bg-orange-500/15 text-orange-400 px-1.5 py-0.5 rounded">
                        {g.fee_pct > 0 ? `${g.fee_pct}% fee` : ""}{g.fee_pct > 0 && g.slippage_pct > 0 ? " + " : ""}{g.slippage_pct > 0 ? `${g.slippage_pct}% slip` : ""}
                      </span>
                    )}
                    {g.slippage_pct === 0 && g.fee_pct === 0 && (
                      <span className="text-[10px] text-ag-text-muted/40 px-1.5 py-0.5">no fees</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => onEditGroup(editingGroup?.id === g.id ? null : g)}
                    className={`text-xs px-2 py-0.5 rounded transition-colors ${
                      editingGroup?.id === g.id ? "bg-ag-accent text-white" : "text-ag-text-muted hover:text-ag-accent"
                    }`}
                    title="Edit group settings">
                    <Settings size={12} />
                  </button>
                  {delConfirm === g.id ? (
                    <span className="flex gap-1 text-xs">
                      <button onClick={() => handleDelete(g.id)} className="text-red-400 hover:text-red-300 font-medium">Delete</button>
                      <button onClick={() => setDelConfirm(null)} className="text-ag-text-muted hover:text-ag-text">Cancel</button>
                    </span>
                  ) : (
                    <button onClick={() => setDelConfirm(g.id)}
                      className="text-ag-text-muted/40 hover:text-red-400" title="Delete group (batches become ungrouped)">
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
              </div>

              {/* Expanded editor for trading params */}
              {editingGroup?.id === g.id && (
                <GroupSettingsEditor group={g} onSave={() => { onEditGroup(null); onRefresh(); }} onCancel={() => onEditGroup(null)} />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Group Settings Editor (trading params) ---

function GroupSettingsEditor({ group, onSave, onCancel }: {
  group: BatchGroupItem;
  onSave: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description || "");
  const [timeframe, setTimeframe] = useState(group.timeframe || "");
  const [bybitLimit, setBybitLimit] = useState(group.bybit_limit ?? "");
  const [maxMargin, setMaxMargin] = useState(group.max_margin_per_trade ?? "");
  const [minAdv, setMinAdv] = useState(group.min_adv ?? "");
  const [slippage, setSlippage] = useState(group.slippage_pct ?? 0);
  const [fee, setFee] = useState(group.fee_pct ?? 0);
  const [pnlMultiplier, setPnlMultiplier] = useState(group.pnl_multiplier ?? 1.0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      await api.batchGroupUpdate(group.id, {
        name: name.trim() || undefined,
        description: description || undefined,
        timeframe: timeframe || undefined,
        bybit_limit: bybitLimit !== "" ? Number(bybitLimit) : undefined,
        max_margin_per_trade: maxMargin !== "" ? Number(maxMargin) : undefined,
        min_adv: minAdv !== "" ? Number(minAdv) : undefined,
        slippage_pct: Number(slippage),
        fee_pct: Number(fee),
        pnl_multiplier: Number(pnlMultiplier),
      });
      onSave();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const fieldClass = "bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-ag-text w-full";

  return (
    <div className="border-t border-ag-border/30 px-3 pb-3 pt-2 space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div>
          <label className="text-[10px] text-ag-text-muted uppercase flex items-center gap-1">
            Name <HintTip text="Display name for this group (e.g. A, B, M30-Main)" />
          </label>
          <input value={name} onChange={(e) => setName(e.target.value)} className={fieldClass} />
        </div>
        <div>
          <label className="text-[10px] text-ag-text-muted uppercase flex items-center gap-1">
            Timeframe <HintTip text="Primary timeframe for batches in this group (e.g. M30, M15)" />
          </label>
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} className={fieldClass}>
            <option value="">—</option>
            <option value="M5">M5</option>
            <option value="M15">M15</option>
            <option value="M30">M30</option>
            <option value="H1">H1</option>
            <option value="H4">H4</option>
          </select>
        </div>
        <div>
          <label className="text-[10px] text-ag-text-muted uppercase flex items-center gap-1">
            Bybit Limit ($) <HintTip text="Position size in USD for live trading. Used by Co-Work to scale PnL from the backtest base." />
          </label>
          <input type="number" value={bybitLimit} onChange={(e) => setBybitLimit(e.target.value)} placeholder="e.g. 250" className={fieldClass} />
        </div>
        <div>
          <label className="text-[10px] text-ag-text-muted uppercase flex items-center gap-1">
            Max Margin ($) <HintTip text="Maximum margin per trade in USD" />
          </label>
          <input type="number" value={maxMargin} onChange={(e) => setMaxMargin(e.target.value)} placeholder="e.g. 1000" className={fieldClass} />
        </div>
        <div>
          <label className="text-[10px] text-ag-text-muted uppercase flex items-center gap-1">
            PnL Multiplier <HintTip text="Scaling ratio for Co-Work research. 2.5 = production trades are 2.5x bigger than backtest. Set to 1.0 if backtests already use production sizing." />
          </label>
          <input type="number" step="0.1" value={pnlMultiplier} onChange={(e) => setPnlMultiplier(Number(e.target.value))} className={fieldClass} />
        </div>
        <div>
          <label className="text-[10px] text-ag-text-muted uppercase flex items-center gap-1">
            Min ADV ($) <HintTip text="Minimum average daily volume filter for symbols" />
          </label>
          <input type="number" value={minAdv} onChange={(e) => setMinAdv(e.target.value)} placeholder="e.g. 1000000" className={fieldClass} />
        </div>
        <div>
          <label className="text-[10px] text-ag-text-muted uppercase flex items-center gap-1">
            Slippage % <HintTip text="Estimated slippage per trade as a percentage" />
          </label>
          <input type="number" step="0.01" value={slippage} onChange={(e) => setSlippage(Number(e.target.value))} className={fieldClass} />
        </div>
        <div>
          <label className="text-[10px] text-ag-text-muted uppercase flex items-center gap-1">
            Fee % <HintTip text="Trading fee per trade as a percentage" />
          </label>
          <input type="number" step="0.01" value={fee} onChange={(e) => setFee(Number(e.target.value))} className={fieldClass} />
        </div>
      </div>
      <div>
        <label className="text-[10px] text-ag-text-muted uppercase flex items-center gap-1">
          Description <HintTip text="Optional note about what this group contains" />
        </label>
        <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Optional description..." className={fieldClass} />
      </div>
      {error && <p className="text-red-400 text-[10px]">{error}</p>}
      <div className="flex justify-end gap-2">
        <button onClick={onCancel} className="text-xs text-ag-text-muted hover:text-ag-text px-3 py-1">Cancel</button>
        <button onClick={handleSave} disabled={saving}
          className="text-xs bg-ag-accent text-white px-4 py-1 rounded hover:bg-ag-accent/80 disabled:opacity-50">
          {saving ? "Saving..." : "Save Settings"}
        </button>
      </div>
    </div>
  );
}

const PERSONAL_GRADES = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"] as const;
function pgColor(g: string | null | undefined): string {
  if (!g) return "";
  if (g.startsWith("A")) return "text-green-400 bg-green-500/15";
  if (g.startsWith("B")) return "text-blue-400 bg-blue-500/15";
  if (g.startsWith("C")) return "text-yellow-400 bg-yellow-500/15";
  return "text-red-400 bg-red-500/15";
}

function BatchRow({ batch, onRefresh, mergeSelected, onToggleMerge, groupMap, batchGroups }: {
  batch: BatchBacktestRunItem;
  onRefresh: () => void;
  mergeSelected: boolean;
  onToggleMerge: () => void;
  groupMap: Map<number, BatchGroupItem>;
  batchGroups: BatchGroupItem[];
}) {
  const batchGroup = batch.group_id != null ? groupMap.get(batch.group_id) : undefined;
  const [showGroupAssign, setShowGroupAssign] = useState(false);
  const isArchived = batch.is_archived ?? false;
  const isCompacted = batch.is_compacted ?? false;
  const [expanded, setExpanded] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameName, setRenameName] = useState(batch.name || "");
  const [refreshing, setRefreshing] = useState(false);
  const [editingPg, setEditingPg] = useState(false);
  const [pgDraft, setPgDraft] = useState(batch.personal_grade || "");
  const [noteDraft, setNoteDraft] = useState(batch.notes || "");
  const [pgSaving, setPgSaving] = useState(false);
  const statusColor = batch.status === "completed" ? "bg-ag-green"
    : batch.status === "error" ? "bg-ag-red"
    : batch.status === "queued" ? "bg-ag-accent/50"
    : "bg-yellow-500 animate-pulse";

  const handleRename = async () => {
    if (renameName.trim() && renameName !== batch.name) {
      await api.updateBatchNotes(batch.id, renameName.trim());
      onRefresh();
    }
    setRenaming(false);
  };

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete batch "${batch.name || batch.id}"? This removes all its runs and trades.`)) return;
    await api.deleteBatchRun(batch.id);
    onRefresh();
  };

  const handleArchive = async (e: React.MouseEvent) => {
    e.stopPropagation();
    await api.archiveBatch(batch.id);
    onRefresh();
  };

  const handleCompact = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Compact "${batch.name || batch.id}"? This permanently deletes all trade data but keeps KPIs + config for re-run.`)) return;
    await api.compactBatch(batch.id);
    onRefresh();
  };

  const handleRefreshBatch = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (refreshing) return;
    setRefreshing(true);
    try {
      const res = await api.refreshBatch(batch.id);
      // Poll progress until complete
      const pollId = res.batch_id;
      const poll = setInterval(async () => {
        try {
          const progress = await api.batchProgress(pollId);
          if (progress.status === "completed" || progress.status === "error") {
            clearInterval(poll);
            setRefreshing(false);
            onRefresh();
          }
        } catch {
          clearInterval(poll);
          setRefreshing(false);
          onRefresh();
        }
      }, 2000);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Refresh failed");
      setRefreshing(false);
    }
  };

  const handlePgSave = async () => {
    setPgSaving(true);
    try {
      await api.updateBatchNotes(batch.id, undefined, noteDraft || undefined, pgDraft || "");
      onRefresh();
    } catch { /* ignore */ }
    setPgSaving(false);
    setEditingPg(false);
  };

  return (
    <div className="relative">
      <div className={`flex items-center justify-between py-1.5 px-2 rounded hover:bg-ag-dark/50 text-xs cursor-pointer group ${isArchived ? "opacity-50" : ""}`}
        onClick={() => setExpanded(!expanded)}>
        <div className="flex items-center gap-2">
          {/* Merge checkbox */}
          {batch.status === "completed" && (
            <input type="checkbox" checked={mergeSelected}
              onClick={(e) => e.stopPropagation()}
              onChange={onToggleMerge}
              className="w-3.5 h-3.5 rounded border-ag-border accent-ag-accent cursor-pointer" />
          )}
          {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
          <div className={`w-2 h-2 rounded-full ${statusColor}`} />
          {renaming ? (
            <input autoFocus value={renameName} onClick={(e) => e.stopPropagation()}
              onChange={(e) => setRenameName(e.target.value)}
              onBlur={handleRename} onKeyDown={(e) => { if (e.key === "Enter") handleRename(); if (e.key === "Escape") setRenaming(false); }}
              className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-xs w-48" />
          ) : (
            <span>{batch.name || `Batch #${batch.id}`}</span>
          )}
          <span className="text-ag-text-muted">{batch.timeframe}m · {batch.symbols_with_trades ?? batch.completed_symbols ?? batch.total_symbols} sym · {batch.direction_filter}</span>
          {/* Group badge */}
          {batchGroup ? (
            <button onClick={(e) => { e.stopPropagation(); setShowGroupAssign(!showGroupAssign); }}
              className="text-[10px] bg-ag-accent/15 text-ag-accent/80 px-1.5 py-0.5 rounded hover:bg-ag-accent/25 transition-colors flex items-center gap-1"
              title={`Group: ${batchGroup.name}${batchGroup.timeframe ? ` · ${batchGroup.timeframe}` : ""}${batchGroup.bybit_limit ? ` · $${batchGroup.bybit_limit}` : ""}`}>
              <FolderOpen size={9} />
              {batchGroup.name}
            </button>
          ) : batchGroups.length > 0 ? (
            <button onClick={(e) => { e.stopPropagation(); setShowGroupAssign(!showGroupAssign); }}
              className="text-[10px] text-ag-text-muted/40 hover:text-ag-text-muted px-1 py-0.5 rounded hover:bg-ag-dark/40 transition-colors"
              title="Assign to a group">
              <FolderOpen size={9} />
            </button>
          ) : null}
          {/* Grade / PF / WR badges */}
          {/* Personal grade — bordered style */}
          {batch.personal_grade && (
            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border border-current ${pgColor(batch.personal_grade)}`}
              title={`My grade: ${batch.personal_grade}${batch.notes ? "\n" + batch.notes : ""}`}>★{batch.personal_grade}</span>
          )}
          {/* Note indicator */}
          {batch.notes && (
            <span className="text-ag-accent/60 cursor-help" title={batch.notes}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
              </svg>
            </span>
          )}
          {batch.batch_grade && (
            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
              batch.batch_grade.startsWith("A") ? "bg-green-500/20 text-green-400"
              : batch.batch_grade.startsWith("B") ? "bg-blue-500/20 text-blue-400"
              : batch.batch_grade.startsWith("C") ? "bg-yellow-500/20 text-yellow-400"
              : "bg-red-500/20 text-red-400"
            }`}>{batch.batch_grade}</span>
          )}
          {batch.avg_profit_factor != null && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              batch.avg_profit_factor >= 1.3 ? "bg-green-500/15 text-green-400"
              : batch.avg_profit_factor >= 1.0 ? "bg-yellow-500/15 text-yellow-400"
              : "bg-red-500/15 text-red-400"
            }`}>PF {batch.avg_profit_factor.toFixed(2)}</span>
          )}
          {batch.avg_win_rate != null && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              batch.avg_win_rate >= 50 ? "bg-green-500/15 text-green-400"
              : batch.avg_win_rate >= 40 ? "bg-yellow-500/15 text-yellow-400"
              : "bg-red-500/15 text-red-400"
            }`}>WR {batch.avg_win_rate.toFixed(1)}%</span>
          )}
          {batch.source_batch_ids && (
            <span className="text-[10px] bg-ag-accent/20 text-ag-accent px-1.5 py-0.5 rounded">merged</span>
          )}
          {isCompacted && (
            <span className="text-[10px] bg-orange-500/20 text-orange-400 px-1.5 py-0.5 rounded">compacted</span>
          )}
          {batch.refreshed_from_id && (
            <span className="text-[10px] bg-green-500/20 text-green-400 px-1.5 py-0.5 rounded">refreshed</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-ag-text-muted">{batch.created_at ? `${new Date(batch.created_at).toLocaleDateString()} ${new Date(batch.created_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}` : "\u2014"}</span>
          {/* Personal grade/note editor button */}
          <button onClick={(e) => { e.stopPropagation(); setEditingPg(!editingPg); setPgDraft(batch.personal_grade || ""); setNoteDraft(batch.notes || ""); }}
            className="opacity-0 group-hover:opacity-100 transition-opacity text-ag-text-muted hover:text-ag-accent"
            title="Edit personal grade & note">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
            </svg>
          </button>
          {batch.status === "completed" && (
            <button onClick={handleRefreshBatch} title="Refresh with latest data"
              disabled={refreshing}
              className={`text-ag-text-muted hover:text-ag-accent ${refreshing ? "animate-spin" : ""}`}>
              <RefreshCw size={12} />
            </button>
          )}
          <button onClick={handleArchive}
            className="text-ag-text-muted hover:text-ag-accent"
            title={isArchived ? "Unarchive batch" : "Archive batch (hide from default view)"}>
            {isArchived ? <ArchiveRestore size={12} /> : <Archive size={12} />}
          </button>
          {!isCompacted && (
            <button onClick={handleCompact}
              className="text-ag-text-muted hover:text-orange-400"
              title="Compact: archive + delete trade data (~3.5 MB saved, keeps KPIs + config for re-run)">
              <PackageMinus size={12} />
            </button>
          )}
          <button onClick={(e) => { e.stopPropagation(); setRenaming(true); }} className="text-ag-text-muted hover:text-ag-accent">
            <Pencil size={12} />
          </button>
          <button onClick={handleDelete} className="text-ag-text-muted hover:text-ag-red">
            <Trash2 size={12} />
          </button>
        </div>
      </div>
      {/* Inline group assignment */}
      {showGroupAssign && (
        <div className="mx-2 mb-1 bg-ag-dark border border-ag-border rounded-lg p-2 flex items-center gap-2"
          onClick={(e) => e.stopPropagation()}>
          <label className="text-[10px] text-ag-text-muted uppercase shrink-0">Group</label>
          <select
            value={batch.group_id ?? ""}
            onChange={async (e) => {
              const gid = e.target.value ? Number(e.target.value) : null;
              try {
                await api.batchGroupAssign({ batch_ids: [batch.id], group_id: gid });
                onRefresh();
              } catch { /* ignore */ }
              setShowGroupAssign(false);
            }}
            className="flex-1 text-xs bg-ag-card border border-ag-border rounded px-2 py-1 text-ag-text">
            <option value="">None (ungrouped)</option>
            {batchGroups.map(g => <option key={g.id} value={g.id}>{g.name}{g.timeframe ? ` · ${g.timeframe}` : ""}{g.bybit_limit ? ` · $${g.bybit_limit}` : ""}</option>)}
          </select>
          <button onClick={() => setShowGroupAssign(false)} className="text-ag-text-muted hover:text-ag-text">
            <X size={12} />
          </button>
        </div>
      )}
      {/* Personal grade/note inline editor */}
      {editingPg && (
        <div className="mx-2 mb-1 bg-ag-dark border border-ag-border rounded-lg p-3 space-y-2"
          onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-2">
            <label className="text-[10px] text-ag-text-muted uppercase w-16">My Grade</label>
            <select value={pgDraft} onChange={(e) => setPgDraft(e.target.value)}
              className="flex-1 text-xs bg-ag-card border border-ag-border rounded px-2 py-1 text-ag-text">
              <option value="">— None —</option>
              {PERSONAL_GRADES.map(g => <option key={g} value={g}>{g}</option>)}
            </select>
          </div>
          <div className="flex items-start gap-2">
            <label className="text-[10px] text-ag-text-muted uppercase w-16 pt-1">Note</label>
            <textarea value={noteDraft} onChange={(e) => setNoteDraft(e.target.value)}
              rows={2} placeholder="Personal notes..."
              className="flex-1 text-xs bg-ag-card border border-ag-border rounded px-2 py-1 text-ag-text resize-none" />
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setEditingPg(false)} className="text-[10px] text-ag-text-muted hover:text-ag-text px-2 py-0.5">Cancel</button>
            <button onClick={handlePgSave} disabled={pgSaving}
              className="text-[10px] bg-ag-accent text-white px-3 py-0.5 rounded hover:bg-ag-accent/80 disabled:opacity-50">
              {pgSaving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      )}
      {expanded && !isCompacted && <BatchExpandedDetail batchId={batch.id} />}
      {expanded && isCompacted && (
        <div className="mx-2 mb-2 bg-ag-dark/60 border border-orange-500/20 rounded-lg p-3 space-y-3">
          <p className="text-[10px] text-orange-400/80 italic">Trade data removed — summary metrics &amp; config preserved.</p>

          {/* KPI grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-1.5 text-xs">
            {batch.batch_grade && (
              <div><span className="text-ag-text-muted">Grade:</span>{" "}
                <span className={`font-bold ${batch.batch_grade.startsWith("A") ? "text-green-400" : batch.batch_grade.startsWith("B") ? "text-blue-400" : batch.batch_grade.startsWith("C") ? "text-yellow-400" : "text-red-400"}`}>{batch.batch_grade}</span>
                {batch.batch_grade_score != null && <span className="text-ag-text-muted ml-1">({batch.batch_grade_score})</span>}
              </div>
            )}
            {batch.avg_profit_factor != null && (
              <div><span className="text-ag-text-muted">Avg PF:</span> <span className={batch.avg_profit_factor >= 1.3 ? "text-green-400" : batch.avg_profit_factor >= 1.0 ? "text-yellow-400" : "text-red-400"}>{batch.avg_profit_factor.toFixed(2)}</span></div>
            )}
            {batch.avg_win_rate != null && (
              <div><span className="text-ag-text-muted">Avg WR:</span> <span className={batch.avg_win_rate >= 50 ? "text-green-400" : batch.avg_win_rate >= 40 ? "text-yellow-400" : "text-red-400"}>{batch.avg_win_rate.toFixed(1)}%</span></div>
            )}
            {batch.total_net_profit != null && (
              <div><span className="text-ag-text-muted">Net Profit:</span> <span className={batch.total_net_profit >= 0 ? "text-green-400" : "text-red-400"}>${batch.total_net_profit.toLocaleString()}</span></div>
            )}
            {batch.total_trades_all != null && (
              <div><span className="text-ag-text-muted">Total Trades:</span> <span>{batch.total_trades_all.toLocaleString()}</span></div>
            )}
            {batch.avg_max_drawdown_pct != null && (
              <div><span className="text-ag-text-muted">Avg Max DD:</span> <span className="text-red-400">{batch.avg_max_drawdown_pct.toFixed(1)}%</span></div>
            )}
            {batch.symbols_with_trades != null && batch.total_symbols_tested != null && (
              <div><span className="text-ag-text-muted">Symbols:</span> <span>{batch.symbols_with_trades} / {batch.total_symbols_tested} active</span></div>
            )}
            {batch.profitable_symbols != null && batch.symbols_with_trades != null && (
              <div><span className="text-ag-text-muted">Profitable:</span> <span className={batch.profitable_symbols / Math.max(batch.symbols_with_trades, 1) >= 0.5 ? "text-green-400" : "text-yellow-400"}>{batch.profitable_symbols} ({(batch.profitable_symbols / Math.max(batch.symbols_with_trades, 1) * 100).toFixed(0)}%)</span></div>
            )}
            {batch.best_symbol && (
              <div><span className="text-ag-text-muted">Best:</span> <span className="text-green-400">{batch.best_symbol}</span></div>
            )}
            {batch.worst_symbol && (
              <div><span className="text-ag-text-muted">Worst:</span> <span className="text-red-400">{batch.worst_symbol}</span></div>
            )}
          </div>

          {/* Config / Parameters */}
          {batch.parameters && Object.keys(batch.parameters).length > 0 && (
            <details className="text-xs">
              <summary className="text-ag-text-muted cursor-pointer hover:text-ag-text select-none">Strategy Config &amp; Parameters</summary>
              <div className="mt-1.5 bg-ag-card/50 border border-ag-border rounded p-2 space-y-1 max-h-48 overflow-y-auto">
                {Object.entries(batch.parameters).map(([key, val]) => {
                  if (key === "inputs" && typeof val === "object" && val !== null) {
                    return (
                      <div key={key}>
                        <span className="text-ag-accent text-[10px] uppercase font-medium">Inputs</span>
                        <div className="ml-2 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-0.5 mt-0.5">
                          {Object.entries(val as Record<string, unknown>).map(([k, v]) => (
                            <div key={k}><span className="text-ag-text-muted">{k}:</span> <span>{String(v)}</span></div>
                          ))}
                        </div>
                      </div>
                    );
                  }
                  if (key === "extra_timeframes" && Array.isArray(val) && val.length > 0) {
                    return <div key={key}><span className="text-ag-text-muted">Extra TFs:</span> <span>{(val as string[]).join(", ")}</span></div>;
                  }
                  if (typeof val === "object" && val !== null) {
                    return (
                      <div key={key}>
                        <span className="text-ag-accent text-[10px] uppercase font-medium">{key}</span>
                        <div className="ml-2 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-0.5 mt-0.5">
                          {Object.entries(val as Record<string, unknown>).map(([k, v]) => (
                            <div key={k}><span className="text-ag-text-muted">{k}:</span> <span>{String(v)}</span></div>
                          ))}
                        </div>
                      </div>
                    );
                  }
                  return <div key={key}><span className="text-ag-text-muted">{key}:</span> <span>{String(val)}</span></div>;
                })}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
