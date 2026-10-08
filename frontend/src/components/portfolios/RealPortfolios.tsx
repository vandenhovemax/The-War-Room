"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import type { PortfolioItem, PortfolioHealthSummary } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import { Wifi, RefreshCw, Square, ChevronDown, ChevronRight, AlertTriangle, Trash2, Archive } from "lucide-react";
import ISvsLivePanel from "./ISvsLivePanel";

export default function RealPortfolios({ defaultExpandedId }: { defaultExpandedId?: number } = {}) {
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<number | null>(defaultExpandedId ?? null);
  const [exchStatus, setExchStatus] = useState<{ connected: boolean; testnet: boolean; error?: string } | null>(null);
  const [engineRunning, setEngineRunning] = useState<boolean | null>(null);
  const [healthMap, setHealthMap] = useState<Record<number, PortfolioHealthSummary>>({});

  useEffect(() => {
    api.portfolioHealthOverview({ include_real: true })
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
      const [p, s, eng] = await Promise.all([
        api.portfoliosByType("real"),
        api.exchangeStatus(),
        api.liveStatus().catch(() => null),
      ]);
      setPortfolios(p);
      setExchStatus(s);
      setEngineRunning(eng?.running ?? false);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <p className="text-sm text-ag-text-muted">Loading real portfolios...</p>;

  return (
    <div className="space-y-4">
      {/* Exchange status banner */}
      {exchStatus && (
        <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs ${
          exchStatus.connected
            ? exchStatus.testnet ? "bg-yellow-500/10 text-yellow-500" : "bg-ag-green/10 text-ag-green"
            : "bg-ag-red/10 text-ag-red"
        }`}>
          <div className={`w-2 h-2 rounded-full ${exchStatus.connected ? "bg-current" : "bg-ag-red"}`} />
          {exchStatus.connected
            ? exchStatus.testnet ? "Connected to Bybit Testnet" : "Connected to Bybit Mainnet"
            : `Not connected: ${exchStatus.error || "API keys not configured"}`
          }
          {exchStatus.testnet && exchStatus.connected && (
            <span className="ml-auto flex items-center gap-1 text-yellow-500">
              <AlertTriangle size={12} /> Testnet — no real funds
            </span>
          )}
        </div>
      )}

      {/* Engine stopped banner */}
      {engineRunning === false && portfolios.some((p) => !p.deactivated_at) && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs bg-yellow-500/10 text-yellow-400 border border-yellow-500/20">
          <div className="w-2 h-2 rounded-full bg-yellow-400" />
          Engine is stopped — portfolios below will auto-resume when the engine starts
        </div>
      )}

      {/* Portfolio list */}
      {portfolios.length === 0 ? (
        <div className="text-center py-12">
          <div className="inline-flex p-3 rounded-xl bg-ag-accent/10 text-ag-accent mb-3">
            <Wifi size={28} />
          </div>
          <h3 className="text-sm font-semibold mb-1">Real Trading</h3>
          <p className="text-xs text-ag-text-muted max-w-sm mx-auto">
            No real trading portfolios yet. Go to <strong>Saved Portfolios</strong>, expand one, and click <strong>Real</strong>.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {portfolios.map((p) => (
            <RealCard
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
      )}
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

function RealCard({ portfolio: p, health, engineRunning, isExpanded, onToggle, onUpdate }: {
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

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const handleDeactivate = async () => {
    if (!confirm(`Stop real trading "${p.name}"? This will NOT close exchange positions.`)) return;
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
          {isActive && (
            <>
              <button onClick={handleRefresh} disabled={refreshing}
                className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 disabled:opacity-50">
                <RefreshCw size={12} className={refreshing ? "animate-spin" : ""} /> Refresh
              </button>
              <button onClick={handleDeactivate}
                className="flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-ag-red/15 text-ag-red hover:bg-ag-red/25">
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
        <div className="border-t border-ag-border p-3">
          <ISvsLivePanel isMetrics={isMetrics} liveMetrics={p.live_metrics_snapshot} oosMetrics={oosMetrics} showIsToggle />
          {isLoadError && (
            <p className="text-[10px] text-yellow-400 mt-1">
              Could not load IS-only metrics — showing &ldquo;&mdash;&rdquo; to prevent data contamination.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
