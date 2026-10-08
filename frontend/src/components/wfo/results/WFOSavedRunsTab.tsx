"use client";
import { useState } from "react";
import { Trash2, Play, FolderOpen, Settings, ChevronDown, ChevronUp, Loader2, RefreshCw, Repeat } from "lucide-react";
import type { WFORunItem } from "@/lib/api";
import { RESEARCH_PRESETS } from "@/components/wfo/WFOResearchPresets";

interface Props {
  runs: WFORunItem[];
  loading?: boolean;
  error?: string | null;
  onRefresh?: () => void;
  onLoad: (run: WFORunItem) => void;
  onRerun: (runId: number) => void;
  onReplayExact?: (runId: number) => void;
  onBulkReplayExact?: (runIds: number[]) => void;
  bulkReplayProgress?: { current: number; total: number; currentRunId: number | null } | null;
  onDelete: (runId: number) => void;
  onReapplyConfig?: (settings: Record<string, unknown>) => void;
  running: boolean;
}

const MODE_LABELS: Record<string, string> = {
  rolling: "Rolling",
  static: "Static",
  overlapping: "Overlapping",
};

/* Friendly metric labels for batch-selection config keys */
const METRIC_LABELS: Record<string, string> = {
  keep_score: "Keep Score", recovery: "Recovery", sortino: "Sortino", calmar: "Calmar",
  pf: "Profit Factor", sharpe: "Sharpe", net_pnl: "Net PnL", ulcer: "Ulcer",
  payoff: "Payoff", stability: "Stability", gain_pain: "Gain/Pain",
  expectancy: "Expectancy", robust_sortino: "Robust Sortino", lake_ratio: "Lake Ratio",
  tail_ratio: "Tail Ratio",
};

function metricLabel(key: string): string {
  return METRIC_LABELS[key] ?? key;
}

/* Build a human-readable config summary from optimizer_settings */
function configSummary(s: Record<string, unknown>): { chips: string[]; execFilter: string } {
  const chips: string[] = [];

  // Core batch-selection params
  const mb = s.bs_max_batches as number | undefined;
  const apb = s.bs_assets_per_batch as number | undefined;
  const nc = s.bs_n_candidates as number | undefined;
  if (mb) chips.push(`mb=${mb}`);
  if (apb) chips.push(`apb=${apb}`);
  if (nc && nc !== 20) chips.push(`nc=${nc}`);

  // Metrics (3-letter shorthand)
  const batch = s.bs_batch_rank_metric as string | undefined;
  const cal = s.bs_cal_rank_metric as string | undefined;
  const asset = s.bs_asset_rank_metric as string | undefined;
  if (batch) chips.push(metricLabel(batch));
  if (cal) chips.push(metricLabel(cal));
  if (asset) chips.push(metricLabel(asset));

  // Advanced features
  const rw = s.bs_recency_weight as number | undefined;
  if (rw && rw > 0) chips.push(`rw=${rw}`);

  const wrg = s.bs_asset_wr_gate as number | undefined;
  if (wrg && wrg > 0.2) chips.push(`wrg=${wrg}`);

  const tpm = s.bs_max_tpm as number | undefined;
  if (tpm && tpm !== 40) chips.push(`tpm=${tpm}`);

  const guard = s.bs_overfit_guard_enabled as boolean | undefined;
  const guardMode = s.bs_overfit_guard_mode as string | undefined;
  if (guard) chips.push(`guard=${guardMode ?? "log"}`);

  const poc = s.bs_past_oos_confirm as boolean | undefined;
  if (poc) chips.push("poc");

  // Exec filter summary
  let execFilter = "None";
  const sfEnabled = s.session_filter_enabled as boolean | undefined;
  if (sfEnabled) {
    const exHours = (s.session_filter_exclude_hours as number[]) ?? [];
    const exDays = (s.session_filter_exclude_days as number[]) ?? [];
    const keptHours = Array.from({ length: 24 }, (_, i) => i).filter(h => !exHours.includes(h));
    const hasMF = exDays.includes(5) && exDays.includes(6);

    if (keptHours.length > 0 && keptHours.length < 24) {
      const min = Math.min(...keptHours);
      const max = Math.max(...keptHours);
      execFilter = `${hasMF ? "MF " : "NYT "}${min}-${max + 1}`;
    } else if (hasMF) {
      execFilter = "Mon-Fri only";
    }
  }

  return { chips, execFilter };
}

/* Format a number with sign and $ */
function fmtPnl(v: number | undefined | null): string {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtPct(v: number | undefined | null): string {
  if (v == null) return "—";
  // win_rate comes as 0-1 from backend
  const pct = v <= 1 ? v * 100 : v;
  return `${pct.toFixed(1)}%`;
}

function fmtPF(v: number | undefined | null): string {
  if (v == null) return "—";
  return v.toFixed(2);
}

/* Resolve preset name from optimizer_settings.preset_id */
function presetName(settings: Record<string, unknown>): string | null {
  const pid = settings.preset_id as string | undefined;
  if (!pid) return null;
  const p = RESEARCH_PRESETS.find(r => r.id === pid);
  return p ? p.name : pid;
}

export default function WFOSavedRunsTab({
  runs, loading, error, onRefresh, onLoad, onRerun, onReplayExact, onBulkReplayExact,
  bulkReplayProgress, onDelete, onReapplyConfig, running,
}: Props) {
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const toggleSelect = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const selectAll = () => setSelectedIds(new Set(runs.map(r => r.id)));
  const clearAll = () => setSelectedIds(new Set());
  const bulkRunning = bulkReplayProgress != null;

  // Fix (2026-04-08): distinguish loading / error / empty. Previously all
  // three rendered the same "No saved WFO runs yet" message, which made
  // transient fetch failures look like a permanently empty list.
  if (loading && runs.length === 0) {
    return (
      <div className="flex items-center justify-center gap-2 py-8 text-sm text-ag-text-muted">
        <Loader2 size={14} className="animate-spin" />
        Loading saved WFO runs...
      </div>
    );
  }

  if (error && runs.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-sm text-ag-red mb-2">Failed to load saved WFO runs</p>
        <p className="text-xs text-ag-text-muted mb-3">{error}</p>
        {onRefresh && (
          <button onClick={onRefresh}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-ag-red/15 text-ag-red hover:bg-ag-red/25 border border-ag-red/40 transition-colors">
            <RefreshCw size={12} /> Retry
          </button>
        )}
      </div>
    );
  }

  if (!runs.length) {
    return (
      <div className="text-center py-8 text-sm text-ag-text-muted">
        <div>No saved WFO runs yet. Run an optimization to create one.</div>
        {onRefresh && (
          <button onClick={onRefresh}
            className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-ag-dark hover:bg-ag-dark/70 border border-ag-border/40 text-ag-text-muted hover:text-ag-text transition-colors">
            <RefreshCw size={12} /> Refresh
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Bulk-select toolbar */}
      {onBulkReplayExact && (
        <div className="flex items-center justify-between gap-2 px-2 py-1.5 rounded border border-ag-border/40 bg-ag-dark/30">
          <div className="flex items-center gap-2 text-xs text-ag-text-muted">
            <button
              onClick={selectedIds.size === runs.length ? clearAll : selectAll}
              className="px-2 py-0.5 rounded bg-ag-dark hover:bg-ag-dark/70 border border-ag-border/40 text-ag-text"
              disabled={bulkRunning}>
              {selectedIds.size === runs.length ? "Clear all" : "Select all"}
            </button>
            <span>
              {selectedIds.size > 0
                ? `${selectedIds.size} selected`
                : "Tick runs to replay multiple at once"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {bulkReplayProgress && (
              <span className="text-xs text-ag-accent inline-flex items-center gap-1.5">
                <Loader2 size={12} className="animate-spin" />
                Running {bulkReplayProgress.current} of {bulkReplayProgress.total}
                {bulkReplayProgress.currentRunId != null && ` (Run #${bulkReplayProgress.currentRunId})`}
              </span>
            )}
            <button
              onClick={() => {
                const ids = Array.from(selectedIds);
                if (ids.length === 0) return;
                if (!confirm(`Replay ${ids.length} run(s) as fresh optimizations? Each will create a NEW saved run alongside the original. This may take a while.`)) return;
                onBulkReplayExact(ids);
              }}
              disabled={selectedIds.size === 0 || bulkRunning || running}
              className="inline-flex items-center gap-1.5 px-3 py-1 text-xs rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 border border-ag-accent/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
              <Repeat size={12} />
              Replay Selected ({selectedIds.size})
            </button>
          </div>
        </div>
      )}

      {runs.map((run) => {
        const { chips, execFilter } = configSummary(run.optimizer_settings);
        const oos = run.cumulative_oos;
        // Phase 2 (hybrid Step 2) result — stashed inside optimizer_settings.step2_result
        // by backend/api/routes/wfo.py. Present only on hybrid runs. The TRUE numbers
        // for live trading reproducibility live here, NOT in cumulative_oos (which is Phase 1).
        const step2 = (run.optimizer_settings?.step2_result ?? null) as
          | { summary?: { net_profit?: number; max_dd?: number; total_trades?: number; pf?: number; wr?: number; profit_factor?: number; win_rate?: number; sortino?: number; avg_window_pnl?: number; avg_per_quarter?: number } }
          | null;
        const p2raw = step2?.summary ?? null;
        // Backend key aliasing: agg_summary uses {pf, wr} (event_wfo.py _compute_event_metrics);
        // older runs may have used {profit_factor, win_rate}. Normalise both shapes here.
        const p2 = p2raw ? {
          ...p2raw,
          profit_factor: p2raw.pf ?? p2raw.profit_factor,
          win_rate: p2raw.wr ?? p2raw.win_rate,
        } : null;
        const isExpanded = expandedId === run.id;
        const pName = presetName(run.optimizer_settings);

        // Color WR based on quality
        const wr = oos?.win_rate != null ? (oos.win_rate <= 1 ? oos.win_rate * 100 : oos.win_rate) : null;
        const wrColor = wr != null
          ? wr >= 70 ? "text-ag-green" : wr >= 50 ? "text-amber-400" : "text-ag-red"
          : "text-ag-text-muted";

        // Color PnL
        const pnlColor = oos?.net_profit != null
          ? oos.net_profit > 0 ? "text-ag-green" : "text-ag-red"
          : "text-ag-text-muted";

        // Color PF
        const pf = oos?.profit_factor;
        const pfColor = pf != null
          ? pf >= 2 ? "text-ag-green" : pf >= 1.5 ? "text-amber-400" : "text-ag-red"
          : "text-ag-text-muted";

        const isSelected = selectedIds.has(run.id);
        const isCurrentlyReplaying = bulkReplayProgress?.currentRunId === run.id;
        return (
          <div key={run.id}
            className={`rounded-lg border transition-colors overflow-hidden ${
              isCurrentlyReplaying
                ? "border-ag-accent/70 bg-ag-accent/5"
                : isSelected
                  ? "border-ag-accent/40 bg-ag-accent/5"
                  : "border-ag-border/50 hover:border-ag-border"
            }`}>
            {/* Main row */}
            <div className="flex items-start justify-between gap-3 p-3">
              {onBulkReplayExact && (
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggleSelect(run.id)}
                  disabled={bulkRunning}
                  className="mt-1 shrink-0 cursor-pointer accent-ag-accent"
                  title="Select for bulk replay"
                />
              )}
              <div className="flex-1 min-w-0">
                {/* Top line: Run ID, mode, date */}
                <div className="flex items-center gap-2 text-xs">
                  <span className="font-medium text-ag-text">Run #{run.id}</span>
                  <span className="px-1.5 py-0.5 bg-ag-accent/10 text-ag-accent rounded text-[10px]">
                    {MODE_LABELS[run.time_mode] ?? run.time_mode}
                  </span>
                  <span className="text-ag-text-muted">
                    IS: {run.is_months}mo · OOS: {run.oos_months}mo
                  </span>
                  <span className="text-ag-text-muted/60 text-[10px]">
                    {run.created_at?.slice(0, 10)}
                  </span>
                  {execFilter !== "None" && (
                    <span className="px-1.5 py-0.5 bg-purple-500/10 text-purple-400 rounded text-[10px]">
                      {execFilter}
                    </span>
                  )}
                  {pName && (
                    <span className="px-1.5 py-0.5 bg-amber-500/10 text-amber-400 rounded text-[10px] truncate max-w-[200px]" title={pName}>
                      {pName}
                    </span>
                  )}
                </div>

                {/* OOS Metrics row — hidden when Phase 2 exists (Phase 1 only picks the pool, not tradeable) */}
                {oos && !p2 && (
                  <div className="flex items-center gap-3 mt-1.5 text-xs">
                    <span className={wrColor}>
                      WR {fmtPct(oos.win_rate)}
                    </span>
                    <span className={pfColor}>
                      PF {fmtPF(oos.profit_factor)}
                    </span>
                    <span className={pnlColor}>
                      {fmtPnl(oos.net_profit)}
                    </span>
                    <span className="text-ag-text-muted">
                      MDD {fmtPnl(oos.max_drawdown ? -Math.abs(oos.max_drawdown) : null)}
                    </span>
                    <span className="text-ag-text-muted">
                      {oos.total_trades ?? 0} trades
                    </span>
                    {oos.sortino != null && oos.sortino !== 0 && (
                      <span className={oos.sortino >= 2 ? "text-ag-green" : oos.sortino >= 1 ? "text-amber-400" : "text-ag-text-muted"}>
                        Sortino {oos.sortino.toFixed(2)}
                      </span>
                    )}
                    {oos.avg_window_pnl != null && oos.avg_window_pnl !== 0 && (
                      <span className={oos.avg_window_pnl > 0 ? "text-ag-green" : "text-ag-red"}>
                        Avg Q {fmtPnl(oos.avg_window_pnl)}
                      </span>
                    )}
                    {run.window_count != null && (
                      <span className="text-ag-text-muted/60">
                        {run.window_count} windows
                      </span>
                    )}
                  </div>
                )}

                {/* Phase 2 (Hybrid Step 2) metrics — TRUE tradeable numbers when present */}
                {p2 && (
                  <div className="flex items-center gap-3 mt-1 text-xs">
                    <span className="px-1.5 py-0.5 bg-ag-accent/15 text-ag-accent rounded text-[10px]" title="Hybrid Step 2: composite re-rank on Phase 1 winners. These are the tradeable numbers.">
                      Hybrid
                    </span>
                    <span className={(p2.win_rate ?? 0) >= 0.5 ? "text-ag-green" : "text-ag-red"}>
                      WR {fmtPct(p2.win_rate)}
                    </span>
                    <span className={(p2.profit_factor ?? 0) >= 1.5 ? "text-ag-green" : "text-amber-400"}>
                      PF {fmtPF(p2.profit_factor)}
                    </span>
                    <span className={(p2.net_profit ?? 0) > 0 ? "text-ag-green" : "text-ag-red"}>
                      {fmtPnl(p2.net_profit)}
                    </span>
                    <span className="text-ag-text-muted">
                      MDD {fmtPnl(p2.max_dd ? -Math.abs(p2.max_dd) : null)}
                    </span>
                    <span className="text-ag-text-muted">
                      {p2.total_trades ?? 0} trades
                    </span>
                    {p2.sortino != null && p2.sortino !== 0 && (
                      <span className={p2.sortino >= 2 ? "text-ag-green" : p2.sortino >= 1 ? "text-amber-400" : "text-ag-text-muted"}>
                        Sortino {p2.sortino.toFixed(2)}
                      </span>
                    )}
                    {p2.avg_window_pnl != null && p2.avg_window_pnl !== 0 && (
                      <span className={p2.avg_window_pnl > 0 ? "text-ag-green" : "text-ag-red"}>
                        Avg Q {fmtPnl(p2.avg_window_pnl)}
                      </span>
                    )}
                  </div>
                )}

                {/* Config chips */}
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {chips.map((chip, i) => (
                    <span key={i}
                      className="px-1.5 py-0.5 bg-ag-dark/50 text-ag-text-muted text-[10px] rounded border border-ag-border/30">
                      {chip}
                    </span>
                  ))}
                </div>
              </div>

              {/* Action buttons */}
              <div className="flex items-center gap-1.5 shrink-0">
                <button onClick={() => onLoad(run)}
                  className="flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 transition-colors"
                  title="Load results">
                  <FolderOpen size={12} /> Load
                </button>
                {onReapplyConfig && (
                  <button
                    onClick={() => onReapplyConfig(run.optimizer_settings)}
                    className="flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-purple-500/15 text-purple-400 hover:bg-purple-500/25 transition-colors"
                    title="Reapply this config to the settings panel">
                    <Settings size={12} /> Reapply
                  </button>
                )}
                <button onClick={() => onRerun(run.id)} disabled={running}
                  className="flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-ag-green/15 text-ag-green hover:bg-ag-green/25 transition-colors disabled:opacity-50"
                  title="Re-run: append a new window with fresh data">
                  <Play size={12} /> Re-run
                </button>
                {onReplayExact && (
                  <button onClick={() => onReplayExact(run.id)} disabled={running}
                    className="flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 transition-colors disabled:opacity-50"
                    title="Replay Exact: fresh full run with the IDENTICAL config and IDENTICAL batch_ids saved on this run. Guaranteed reproducible — use this to compare apples-to-apples.">
                    <Repeat size={12} /> Replay
                  </button>
                )}
                <button onClick={() => setExpandedId(isExpanded ? null : run.id)}
                  className="p-1 text-ag-text-muted hover:text-ag-text transition-colors"
                  title="Show full config details">
                  {isExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                </button>
                <button onClick={() => {
                    if (confirm("Delete this WFO run?")) onDelete(run.id);
                  }}
                  className="p-1 text-ag-text-muted hover:text-ag-red transition-colors"
                  title="Delete run">
                  <Trash2 size={13} />
                </button>
              </div>
            </div>

            {/* Expanded config details */}
            {isExpanded && (
              <div className="border-t border-ag-border/30 px-3 py-2 bg-ag-dark/30">
                <div className="text-[10px] text-ag-text-muted space-y-1">
                  <div className="font-medium text-ag-text/80 mb-1">Full Config</div>
                  <ConfigDetailGrid settings={run.optimizer_settings} />
                  {run.batch_ids.length > 0 && (
                    <div className="mt-2 pt-1 border-t border-ag-border/20">
                      <span className="text-ag-text-muted/60">
                        Batches ({run.batch_ids.length}): [{run.batch_ids.join(", ")}]
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


/* Grid view of all config key-value pairs */
function ConfigDetailGrid({ settings }: { settings: Record<string, unknown> }) {
  // Group settings into categories
  const bsKeys = Object.entries(settings).filter(([k]) => k.startsWith("bs_"));
  const sfKeys = Object.entries(settings).filter(([k]) => k.startsWith("session_filter"));
  const otherKeys = Object.entries(settings).filter(
    ([k]) => !k.startsWith("bs_") && !k.startsWith("session_filter") && k !== "wfo_mode",
  );

  const renderPairs = (pairs: [string, unknown][]) => (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-x-4 gap-y-0.5">
      {pairs.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-1">
          <span className="text-ag-text-muted/60 truncate">{k.replace(/^bs_/, "").replace(/^session_filter_/, "sf_")}</span>
          <span className="text-ag-text/80 font-mono shrink-0">
            {typeof v === "boolean" ? (v ? "yes" : "no")
              : Array.isArray(v) ? `[${v.length}]`
              : String(v ?? "—")}
          </span>
        </div>
      ))}
    </div>
  );

  return (
    <div className="space-y-1.5">
      {bsKeys.length > 0 && (
        <div>
          <div className="text-[9px] uppercase text-ag-accent/60 mb-0.5">Batch Selection</div>
          {renderPairs(bsKeys)}
        </div>
      )}
      {sfKeys.length > 0 && (
        <div>
          <div className="text-[9px] uppercase text-purple-400/60 mb-0.5">Exec Filter</div>
          {renderPairs(sfKeys)}
        </div>
      )}
      {otherKeys.length > 0 && (
        <div>
          <div className="text-[9px] uppercase text-ag-text-muted/60 mb-0.5">Other</div>
          {renderPairs(otherKeys)}
        </div>
      )}
    </div>
  );
}
