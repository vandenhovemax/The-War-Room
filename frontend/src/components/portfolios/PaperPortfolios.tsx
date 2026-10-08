"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import type { PortfolioItem, PortfolioHealthSummary } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import { FlaskConical, RefreshCw, Square, ChevronDown, ChevronRight, Trash2, Archive, ExternalLink, History, X } from "lucide-react";
import ISvsLivePanel from "./ISvsLivePanel";
import PortfolioInDepthPanel from "@/components/analysis/PortfolioInDepthPanel";
import PaperOverviewPanel from "./PaperOverviewPanel";

export default function PaperPortfolios({ defaultExpandedId }: { defaultExpandedId?: number } = {}) {
  const { addToast } = useTaskManager();
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<number | null>(defaultExpandedId ?? null);
  const [engineRunning, setEngineRunning] = useState<boolean | null>(null);
  const [healthMap, setHealthMap] = useState<Record<number, PortfolioHealthSummary>>({});

  useEffect(() => {
    api.portfolioHealthOverview({ include_paper: true })
      .then((r) => {
        const m: Record<number, PortfolioHealthSummary> = {};
        r.portfolios.forEach((h) => { m[h.portfolio_id] = h; });
        setHealthMap(m);
      })
      .catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, eng] = await Promise.all([
        api.portfoliosByType("paper"),
        api.liveStatus().catch(() => null),
      ]);
      setPortfolios(p);
      setEngineRunning(eng?.running ?? false);
    } catch { addToast("Failed to load paper portfolios", "error"); }
    setLoading(false);
  }, [addToast]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <p className="text-sm text-ag-text-muted">Loading paper portfolios...</p>;

  if (portfolios.length === 0) {
    return (
      <div className="text-center py-12">
        <div className="inline-flex p-3 rounded-xl bg-ag-accent/10 text-ag-accent mb-3">
          <FlaskConical size={28} />
        </div>
        <h3 className="text-sm font-semibold mb-1">Paper Trading</h3>
        <p className="text-xs text-ag-text-muted max-w-sm mx-auto">
          No paper trading portfolios yet. Go to <strong>Saved Portfolios</strong>, expand one, and click <strong>Paper</strong>.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Engine stopped banner */}
      {engineRunning === false && portfolios.some((p) => !p.deactivated_at) && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs bg-yellow-500/10 text-yellow-400 border border-yellow-500/20">
          <div className="w-2 h-2 rounded-full bg-yellow-400" />
          Engine is stopped — portfolios below will auto-resume when the engine starts
        </div>
      )}
      {/* At-a-glance PnL + sparkline overview across every active paper/real
          portfolio. Lives at the top of the tab so the user doesn't have to
          click into each portfolio to see how it's doing. */}
      <PaperOverviewPanel />
      {portfolios.map((p) => (
        <PaperCard
          key={p.id}
          portfolio={p}
          health={healthMap[p.id]}
          engineRunning={engineRunning ?? false}
          isExpanded={expanded === p.id}
          onToggle={() => setExpanded(expanded === p.id ? null : p.id)}
          onUpdate={load}
        />
      ))}
    </div>
  );
}

function gradeColor(g: string): string {
  if (g.startsWith("A")) return "bg-ag-green/20 text-ag-green border-ag-green/30";
  if (g.startsWith("B")) return "bg-lime-500/20 text-lime-400 border-lime-500/30";
  if (g.startsWith("C")) return "bg-yellow-500/20 text-yellow-400 border-yellow-500/30";
  if (g.startsWith("D")) return "bg-orange-500/20 text-orange-400 border-orange-500/30";
  return "bg-ag-red/20 text-ag-red border-ag-red/30";
}

function PaperCard({ portfolio: p, health, engineRunning, isExpanded, onToggle, onUpdate }: {
  portfolio: PortfolioItem; health?: PortfolioHealthSummary; engineRunning: boolean; isExpanded: boolean; onToggle: () => void; onUpdate: () => void;
}) {
  const { addToast } = useTaskManager();
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState("");
  const [allFresh, setAllFresh] = useState<boolean | null>(null);
  const [ddStatus, setDDStatus] = useState<{
    in_drawdown: boolean; dd_days: number; dd_depth_usd: number; dd_depth_pct: number;
    oos_worst_dd_usd: number; oos_worst_dd_pct: number;
  } | null>(null);

  useEffect(() => {
    api.portfolioDDStatus(p.id).then(setDDStatus).catch(() => {});
  }, [p.id]);

  useEffect(() => {
    if (!p.deactivated_at) {
      api.portfolioDataFreshness(p.id)
        .then((r) => setAllFresh(r.all_fresh))
        .catch(() => {});
    }
  }, [p.id, p.deactivated_at, p.last_refreshed_at]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [oosMetrics, setOosMetrics] = useState<Record<string, number>>({});
  // IS metrics must ONLY come from the /oos-date endpoint (IS-only split).
  // Never fall back to metrics_snapshot — it could contain combined IS+OOS data.
  const [isMetrics, setIsMetrics] = useState<Record<string, number>>(
    p.optimization_end_date ? {} : p.metrics_snapshot
  );
  const [isLoadError, setIsLoadError] = useState(false);

  // Fetch OOS metrics when expanded and portfolio has OOS cutoff
  useEffect(() => {
    if (!isExpanded || !p.optimization_end_date) return;
    setIsLoadError(false);
    api.portfolioOOSDate(p.id, p.optimization_end_date)
      .then((r) => {
        if (r.oos_metrics) {
          const oos = { ...(r.oos_metrics as Record<string, number>) };
          if (r.wfe !== undefined) oos.wfe = r.wfe as number;
          setOosMetrics(oos);
        }
        if (r.is_metrics && Object.keys(r.is_metrics).length > 0) {
          setIsMetrics(r.is_metrics as Record<string, number>);
        }
        // If no IS metrics returned, keep empty {} — show "—" instead of combined data
      })
      .catch(() => {
        // On error, keep empty {} — show "—" instead of potentially contaminated data
        setIsLoadError(true);
      });
  }, [isExpanded, p.id, p.optimization_end_date]);

  const handleRefresh = async () => {
    setRefreshing(true);
    setRefreshMsg("Starting...");
    try {
      const { task_id } = await api.refreshPortfolio(p.id);
      pollRef.current = setInterval(async () => {
        try {
          const s = await api.refreshStatus(task_id);
          setRefreshMsg(s.current_asset || s.status);
          if (s.status === "completed" || s.status === "error") {
            if (pollRef.current) clearInterval(pollRef.current);
            setRefreshing(false);
            setRefreshMsg(s.status === "completed" ? "Done!" : "Error");
            onUpdate();
            setTimeout(() => setRefreshMsg(""), 3000);
          }
        } catch { /* keep polling */ }
      }, 2000);
    } catch {
      setRefreshing(false);
      setRefreshMsg("Failed");
    }
  };

  // ── Backfill history (hybrid portfolios only) ──────────────────────
  // Walks Phase 2 grading day-by-day from OOS end to today, writing one
  // basket event per day so the basket-at-t filter has segments to match
  // against. See backend/scripts/backfill_hybrid_basket_events.py.
  const [backfilling, setBackfilling] = useState(false);
  const [backfillMsg, setBackfillMsg] = useState("");
  const [backfillPlan, setBackfillPlan] = useState<{
    oos_end?: string;
    first_existing_event?: string | null;
    gap_start?: string;
    gap_end?: string;
    total_days_planned: number;
    estimated_minutes?: number;
    reason?: string;
  } | null>(null);
  const [showBackfillModal, setShowBackfillModal] = useState(false);
  const backfillPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const openBackfillModal = async () => {
    setBackfillMsg("Checking gap...");
    try {
      const plan = await api.backfillBasketPlan(p.id);
      setBackfillPlan(plan);
      setShowBackfillModal(true);
      setBackfillMsg("");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to check gap";
      setBackfillMsg(msg);
      setTimeout(() => setBackfillMsg(""), 4000);
    }
  };

  const handleBackfillConfirm = async () => {
    setShowBackfillModal(false);
    setBackfilling(true);
    setBackfillMsg("Starting backfill...");
    try {
      const { task_id } = await api.backfillBasketHistory(p.id);
      backfillPollRef.current = setInterval(async () => {
        try {
          const s = await api.backfillStatus(task_id);
          if (s.status === "running" || s.status === "starting") {
            const done = s.days_done ?? 0;
            const total = s.days_total ?? 0;
            const cur = s.current_day ?? "...";
            setBackfillMsg(`Day ${done}/${total} (${cur})`);
          } else if (s.status === "completed") {
            if (backfillPollRef.current) clearInterval(backfillPollRef.current);
            setBackfilling(false);
            const ok = s.result?.days_succeeded ?? 0;
            const fail = s.result?.days_failed ?? 0;
            setBackfillMsg(`Done — ${ok} ok / ${fail} fail. Click Refresh now.`);
            addToast(
              `Backfill complete for "${p.name}". Click Refresh to rebuild the snapshot.`,
              "success",
            );
            onUpdate();
            setTimeout(() => setBackfillMsg(""), 10000);
          } else if (s.status === "error") {
            if (backfillPollRef.current) clearInterval(backfillPollRef.current);
            setBackfilling(false);
            setBackfillMsg(`Error: ${s.error || "unknown"}`);
            setTimeout(() => setBackfillMsg(""), 8000);
          }
        } catch { /* keep polling */ }
      }, 2500);
    } catch (e) {
      setBackfilling(false);
      const msg = e instanceof Error ? e.message : "Failed to start";
      setBackfillMsg(msg);
      setTimeout(() => setBackfillMsg(""), 4000);
    }
  };

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (backfillPollRef.current) clearInterval(backfillPollRef.current);
    };
  }, []);

  const handleDeactivate = async () => {
    if (!confirm(`Stop paper trading "${p.name}"?`)) return;
    try { await api.deactivatePortfolio(p.id); onUpdate(); } catch { addToast("Failed to deactivate portfolio", "error"); }
  };

  const handleDelete = async () => {
    if (!confirm(`Permanently delete "${p.name}"? This cannot be undone.`)) return;
    try { await api.deletePortfolio(p.id); onUpdate(); } catch { addToast("Failed to delete portfolio", "error"); }
  };

  const handleArchive = async () => {
    if (!confirm(`Archive "${p.name}"? All data (trades, KPIs) will be preserved. You can restore it later from the Archived tab.`)) return;
    try { await api.archivePortfolio(p.id); addToast(`"${p.name}" archived`, "success"); onUpdate(); } catch { addToast("Failed to archive portfolio", "error"); }
  };

  const isActive = !p.deactivated_at;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg">
      <div className="flex items-center gap-3 p-3 cursor-pointer hover:bg-ag-card-hover" onClick={onToggle}>
        {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium truncate">{p.name}</p>
            {isActive && engineRunning ? (
              <span className="px-1.5 py-0.5 bg-ag-green/15 text-ag-green rounded text-[10px]">Active</span>
            ) : isActive && !engineRunning ? (
              <span className="px-1.5 py-0.5 bg-yellow-500/15 text-yellow-400 rounded text-[10px]" title="Engine is stopped — will auto-resume when started">Standby</span>
            ) : (
              <span className="px-1.5 py-0.5 bg-ag-text-muted/15 text-ag-text-muted rounded text-[10px]">Stopped</span>
            )}
            {allFresh !== null && (
              <span className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] ${
                allFresh ? "bg-ag-green/15 text-ag-green" : "bg-yellow-400/15 text-yellow-400"
              }`} title={allFresh ? "All candle data is up to date" : "Some assets have stale candle data — click Refresh"}>
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${allFresh ? "bg-ag-green" : "bg-yellow-400"}`} />
                {allFresh ? "Synced" : "Stale"}
              </span>
            )}
            {health?.overall_grade && (
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${gradeColor(health.overall_grade)}`}>
                {health.overall_grade}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 text-[10px] text-ag-text-muted">
            <span>
              {p.asset_count} assets
              {p.activated_at && ` · Since ${new Date(p.activated_at).toLocaleDateString()}`}
              {p.last_refreshed_at && ` · Refreshed ${new Date(p.last_refreshed_at).toLocaleString()}`}
            </span>
            {ddStatus && (
              ddStatus.in_drawdown ? (
                <span className={`font-semibold ${
                  Math.abs(ddStatus.dd_depth_usd) >= Math.abs(ddStatus.oos_worst_dd_usd) ? "text-ag-red" : "text-yellow-400"
                }`}>
                  {ddStatus.dd_days}d DD · ${Math.abs(ddStatus.dd_depth_usd).toFixed(0)} / {Math.abs(ddStatus.dd_depth_pct).toFixed(1)}%
                </span>
              ) : (
                <span className="font-semibold text-ag-green">No DD</span>
              )
            )}
          </div>
        </div>

        <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
          {refreshMsg && <span className="text-[10px] text-ag-text-muted max-w-[140px] truncate">{refreshMsg}</span>}
          {backfillMsg && <span className="text-[10px] text-purple-400 max-w-[200px] truncate" title={backfillMsg}>{backfillMsg}</span>}
          {isActive && (
            <>
              {/* Backfill button — only for hybrid portfolios. Walks Phase 2
                  grading day-by-day from OOS end to today, writing one event
                  per day so basket-at-t replay has segments to match. */}
              {p.mode === "hybrid" && (
                <button onClick={openBackfillModal} disabled={backfilling || refreshing}
                  className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-purple-500/15 text-purple-400 hover:bg-purple-500/25 disabled:opacity-50"
                  title="Walk Phase 2 day-by-day from OOS end → today, recording the basket each day. Run this BEFORE Refresh to unlock proper basket-at-t history.">
                  <History size={12} className={backfilling ? "animate-pulse" : ""} /> Backfill
                </button>
              )}
              <button onClick={handleRefresh} disabled={refreshing || backfilling}
                className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 disabled:opacity-50"
                title="Download latest candles and recompute live KPIs">
                <RefreshCw size={12} className={refreshing ? "animate-spin" : ""} /> Refresh
              </button>
              <button onClick={handleDeactivate}
                className="flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-ag-red/15 text-ag-red hover:bg-ag-red/25"
                title="Stop paper trading">
                <Square size={10} /> Stop
              </button>
            </>
          )}
          {!isActive && (
            <button onClick={handleArchive}
              className="flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-ag-blue/15 text-ag-blue hover:bg-ag-blue/25"
              title="Archive portfolio — preserves all data">
              <Archive size={12} /> Archive
            </button>
          )}
          <button onClick={handleDelete}
            className="p-1.5 text-ag-text-muted hover:text-ag-red transition-colors"
            title="Delete portfolio">
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {isExpanded && (
        <div className="border-t border-ag-border p-3 space-y-3">
          {/* Quick IS/OOS/Live KPI strip — kept for at-a-glance comparison */}
          <ISvsLivePanel isMetrics={isMetrics} liveMetrics={p.live_metrics_snapshot} oosMetrics={oosMetrics} showIsToggle />
          {isLoadError && (
            <p className="text-[10px] text-yellow-400 mt-1">
              Could not load IS-only metrics — showing &ldquo;&mdash;&rdquo; to prevent data contamination.
            </p>
          )}

          {/* Quick-link to the same portfolio in the Saved Portfolios (Management) view */}
          <div className="flex justify-end">
            <a
              href={`/portfolios?tab=saved&id=${p.id}`}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-accent hover:border-ag-accent/40 transition-colors"
              title="Open this portfolio in Management → Saved Portfolios"
            >
              <ExternalLink size={11} /> Open in Saved Portfolios
            </a>
          </div>

          {/* Full portfolio detail — same tabs as the Saved Portfolios view */}
          <PortfolioInDepthPanel portfolioId={p.id} />
        </div>
      )}

      {/* Backfill confirmation modal — shows the planned gap before the
          user commits to a multi-minute job. Hybrid only. */}
      {showBackfillModal && backfillPlan && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
             onClick={(e) => e.stopPropagation()}>
          <div className="bg-ag-card border border-ag-border rounded-lg max-w-md w-full p-5 shadow-xl"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-3">
              <div className="flex items-center gap-2">
                <History size={18} className="text-purple-400" />
                <h3 className="text-sm font-semibold">Backfill basket history</h3>
              </div>
              <button onClick={() => setShowBackfillModal(false)}
                className="text-ag-text-muted hover:text-ag-text">
                <X size={16} />
              </button>
            </div>

            <div className="space-y-2 text-xs mb-4">
              <p className="text-ag-text-muted">
                Walks Phase 2 grading day-by-day from your OOS end to today,
                recording what the basket would have been each day. Run this
                BEFORE clicking Refresh so basket-at-t replay sees the full timeline.
              </p>

              {backfillPlan.total_days_planned === 0 ? (
                <div className="bg-ag-green/10 border border-ag-green/30 rounded p-2 text-ag-green">
                  No gap detected — this portfolio already has complete event history.
                </div>
              ) : (
                <>
                  <div className="bg-ag-bg-deep border border-ag-border/40 rounded p-2 space-y-1">
                    <div className="flex justify-between">
                      <span className="opacity-70">OOS end:</span>
                      <span className="font-mono">{backfillPlan.oos_end}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="opacity-70">First existing event:</span>
                      <span className="font-mono">{backfillPlan.first_existing_event || "(none yet)"}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="opacity-70">Gap to backfill:</span>
                      <span className="font-mono">{backfillPlan.gap_start} → {backfillPlan.gap_end}</span>
                    </div>
                    <div className="flex justify-between font-medium">
                      <span>Total days:</span>
                      <span className="text-purple-400">{backfillPlan.total_days_planned}</span>
                    </div>
                    {backfillPlan.estimated_minutes !== undefined && (
                      <div className="flex justify-between">
                        <span className="opacity-70">Estimated runtime:</span>
                        <span>~{backfillPlan.estimated_minutes} min</span>
                      </div>
                    )}
                  </div>

                  <div className="bg-yellow-500/10 border border-yellow-500/30 rounded p-2 text-yellow-400">
                    ⚠ This writes ~{backfillPlan.total_days_planned} rows to the
                    live_events table. Snapshot of <code className="font-mono text-[10px]">data/antigravity.db</code> recommended
                    before running. Backfilled events are tagged
                    <code className="font-mono text-[10px]"> trigger:&quot;backfill&quot;</code> for rollback.
                  </div>
                </>
              )}
            </div>

            <div className="flex justify-end gap-2">
              <button onClick={() => setShowBackfillModal(false)}
                className="px-3 py-1.5 text-xs rounded bg-ag-bg-deep border border-ag-border text-ag-text-muted hover:text-ag-text">
                Cancel
              </button>
              {backfillPlan.total_days_planned > 0 && (
                <button onClick={handleBackfillConfirm}
                  className="px-3 py-1.5 text-xs rounded bg-purple-500/20 text-purple-300 border border-purple-500/40 hover:bg-purple-500/30">
                  Start backfill
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
