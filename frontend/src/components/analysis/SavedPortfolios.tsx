"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { PortfolioItem, PortfolioDetail } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import {
  Trash2, ChevronDown, ChevronRight, GitCompare,
  FlaskConical, Wifi, EyeOff, RefreshCw, Pencil, Check, X,
} from "lucide-react";
import PortfolioOOSPanel from "./PortfolioOOSPanel";
import PortfolioHealthMonitor from "./PortfolioHealthMonitor";
import PortfolioComparePanel from "./PortfolioComparePanel";
import AssetSwapTool from "./AssetSwapTool";
import LiveViewsPanel from "@/components/portfolios/LiveViewsPanel";
import PortfolioTestPanel from "./PortfolioTestPanel";
import PortfolioFullMetrics from "./PortfolioFullMetrics";
import PortfolioAssetTab from "./PortfolioAssetTab";
import ISvsLivePanel from "@/components/portfolios/ISvsLivePanel";
import PortfolioInDepthPanel from "./PortfolioInDepthPanel";
import WalkForwardPanel from "./WalkForwardPanel";

type SubTab = "details" | "assets" | "oos" | "health" | "swap" | "live" | "test";

export default function SavedPortfolios({ defaultExpandedId }: { defaultExpandedId?: number } = {}) {
  const { addToast } = useTaskManager();
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [expanded, setExpanded] = useState<number | null>(defaultExpandedId ?? null);
  const [detail, setDetail] = useState<PortfolioDetail | null>(null);
  const [subTab, setSubTab] = useState<SubTab>("details");
  const [loading, setLoading] = useState(true);
  // Comparison mode
  const [compareMode, setCompareMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  // Rename mode
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const load = async () => {
    setLoading(true);
    try { setPortfolios(await api.portfolios()); } catch { addToast("Failed to load portfolios", "error"); }
    setLoading(false);
  };

  // Refresh both list AND expanded detail (e.g. after swap)
  const refresh = async () => {
    try { setPortfolios(await api.portfolios()); } catch { /* ignore — silent refresh */ }
    if (expanded) {
      try { setDetail(await api.portfolioDetail(expanded)); } catch { /* ignore — silent refresh */ }
    }
  };

  useEffect(() => { load(); }, []);

  // Auto-load the detail payload when an id was passed via URL (Paper → "Open in Saved Portfolios")
  useEffect(() => {
    if (defaultExpandedId && !detail) {
      api.portfolioDetail(defaultExpandedId).then(setDetail).catch(() => setDetail(null));
    }
  }, [defaultExpandedId, detail]);

  const toggleExpand = async (id: number) => {
    if (compareMode) return; // Don't expand in compare mode
    if (expanded === id) { setExpanded(null); setDetail(null); return; }
    setExpanded(id);
    setSubTab("details");
    try { setDetail(await api.portfolioDetail(id)); } catch { setDetail(null); }
  };

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this portfolio?")) return;
    try {
      await api.deletePortfolio(id);
      setPortfolios((prev) => prev.filter((p) => p.id !== id));
      if (expanded === id) { setExpanded(null); setDetail(null); }
      selected.delete(id);
    } catch { addToast("Failed to delete portfolio", "error"); }
  };

  const handleHide = async (id: number) => {
    try {
      await api.hidePortfolio(id);
      setPortfolios((prev) => prev.filter((p) => p.id !== id));
      if (expanded === id) { setExpanded(null); setDetail(null); }
    } catch { addToast("Failed to hide portfolio", "error"); }
  };

  const startRename = (id: number, currentName: string) => {
    setRenamingId(id);
    setRenameValue(currentName);
  };

  const saveRename = async () => {
    if (!renamingId || !renameValue.trim()) return;
    try {
      await api.updatePortfolio(renamingId, { name: renameValue.trim() });
      setPortfolios((prev) => prev.map((p) =>
        p.id === renamingId ? { ...p, name: renameValue.trim() } : p
      ));
      if (detail && detail.id === renamingId) {
        setDetail({ ...detail, name: renameValue.trim() });
      }
    } catch { addToast("Failed to rename portfolio", "error"); }
    setRenamingId(null);
  };

  if (loading) return <p className="text-sm text-ag-text-muted">Loading portfolios...</p>;
  if (portfolios.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-ag-text-muted text-sm">No saved portfolios yet.</p>
        <p className="text-ag-text-muted text-xs mt-1">Use the Optimizer tab to find and save portfolios.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <button onClick={() => { setCompareMode(!compareMode); setSelected(new Set()); setExpanded(null); }}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
            compareMode ? "bg-ag-accent text-white" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
          }`}>
          <GitCompare size={13} />
          {compareMode ? "Exit Compare" : "Compare Mode"}
        </button>
        {compareMode && selected.size >= 2 && (
          <span className="text-xs text-ag-accent">{selected.size} selected</span>
        )}
      </div>

      {/* Compare panel */}
      {compareMode && selected.size >= 2 && (
        <div className="bg-ag-card border border-ag-border rounded-lg p-4">
          <PortfolioComparePanel portfolioIds={Array.from(selected)} />
        </div>
      )}

      {/* Portfolio list */}
      {portfolios.map((p) => (
        <div key={p.id} className="bg-ag-card border border-ag-border rounded-lg">
          <div className="group flex items-center gap-3 p-3 cursor-pointer hover:bg-ag-card-hover transition-colors"
            onClick={() => compareMode ? toggleSelect(p.id) : toggleExpand(p.id)}>
            {compareMode ? (
              <input type="checkbox" checked={selected.has(p.id)}
                onChange={() => toggleSelect(p.id)}
                className="accent-ag-accent" />
            ) : (
              expanded === p.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />
            )}
            <div className="flex-1 min-w-0">
              {renamingId === p.id ? (
                <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                  <input type="text" value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") saveRename(); if (e.key === "Escape") setRenamingId(null); }}
                    className="bg-ag-dark border border-ag-accent rounded px-2 py-0.5 text-sm w-full"
                    autoFocus />
                  <button onClick={saveRename} className="p-1 text-ag-green hover:text-ag-green/80" title="Save">
                    <Check size={14} />
                  </button>
                  <button onClick={() => setRenamingId(null)} className="p-1 text-ag-text-muted hover:text-ag-red" title="Cancel">
                    <X size={14} />
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-1.5">
                  <p className="text-sm font-medium truncate">{p.name}</p>
                  <button onClick={(e) => { e.stopPropagation(); startRename(p.id, p.name); }}
                    className="p-0.5 text-ag-text-muted hover:text-ag-accent opacity-0 group-hover:opacity-100 transition-opacity"
                    title="Rename">
                    <Pencil size={12} />
                  </button>
                </div>
              )}
              <p className="text-[10px] text-ag-text-muted">
                {p.asset_count} assets · Created {p.created_at ? new Date(p.created_at).toLocaleDateString() : "—"}
              </p>
            </div>
            <div className="flex items-center gap-3 text-xs text-ag-text-muted">
              {/* For WFO / hybrid sources the fixed-basket PF/R² is misleading — hide it. */}
              {p.portfolio_source !== "wfo" && p.portfolio_source !== "wfas_phase2" && p.metrics_snapshot.profit_factor !== undefined && (
                <span>{p.optimization_end_date ? "IS " : ""}PF: {p.metrics_snapshot.profit_factor.toFixed(2)}</span>
              )}
              {p.portfolio_source !== "wfo" && p.portfolio_source !== "wfas_phase2" && p.metrics_snapshot.stability !== undefined && (
                <span>R\u00b2: {(p.metrics_snapshot.stability * 100).toFixed(0)}%</span>
              )}
              {(p.portfolio_source === "wfo" || p.portfolio_source === "wfas_phase2") && (
                <span className="text-ag-accent">Walk-forward — see Details</span>
              )}
              {/* WFO Track metrics (shown when portfolio was saved from a WFO Track) */}
              {p.metrics_snapshot.total_oos_pnl !== undefined && (
                <span>OOS: ${p.metrics_snapshot.total_oos_pnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
              )}
              {p.metrics_snapshot.avg_wfe !== undefined && (
                <span>WFE: {(p.metrics_snapshot.avg_wfe * 100).toFixed(1)}%</span>
              )}
              {p.metrics_snapshot.windows_profitable !== undefined && p.metrics_snapshot.windows_total !== undefined && (
                <span>Win: {p.metrics_snapshot.windows_profitable}/{p.metrics_snapshot.windows_total}</span>
              )}
            </div>
            <button onClick={(e) => { e.stopPropagation(); handleHide(p.id); }}
              className="p-1.5 text-ag-text-muted hover:text-ag-text transition-colors" title="Hide">
              <EyeOff size={14} />
            </button>
            <button onClick={(e) => { e.stopPropagation(); handleDelete(p.id); }}
              className="p-1.5 text-ag-text-muted hover:text-ag-red transition-colors" title="Delete">
              <Trash2 size={14} />
            </button>
          </div>

          {/* Expanded detail — unified in-depth panel (same tabs as Paper Trading expanded view) */}
          {expanded === p.id && detail && (
            <div className="border-t border-ag-border p-3">
              <PortfolioInDepthPanel portfolioId={p.id} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

type FreshnessData = { all_fresh: boolean; timeframe: string; assets: { symbol: string; is_fresh: boolean; candles_behind: number }[] };

function ExpandedDetail({ detail, subTab, setSubTab, onRefresh, onPromoted }: {
  detail: PortfolioDetail; subTab: SubTab; setSubTab: (t: SubTab) => void; onRefresh: () => void; onPromoted?: () => void;
}) {
  const [promoting, setPromoting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [desc, setDesc] = useState(detail.description || "");
  const [descSaving, setDescSaving] = useState(false);
  const [descDirty, setDescDirty] = useState(false);
  const [freshness, setFreshness] = useState<FreshnessData | null>(null);

  useEffect(() => {
    // Only check freshness for paper/real portfolios (saved data is naturally old)
    if (detail.portfolio_type === "paper" || detail.portfolio_type === "real") {
      api.portfolioDataFreshness(detail.id)
        .then((r) => setFreshness(r))
        .catch(() => {});
    }
  }, [detail.id, detail.portfolio_type]);

  const saveDescription = async () => {
    setDescSaving(true);
    try {
      await api.updatePortfolio(detail.id, { description: desc });
      setDescDirty(false);
    } catch { /* ignore */ }
    setDescSaving(false);
  };

  const tabs: { id: SubTab; label: string }[] = [
    { id: "details", label: "Details" },
    { id: "assets", label: "Assets" },
    { id: "oos", label: "OOS Analysis" },
    { id: "health", label: "Health" },
    { id: "swap", label: "Asset Swap" },
    { id: "live", label: "Live Views" },
    { id: "test", label: "Test Portfolio" },
  ];

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const { task_id } = await api.refreshPortfolio(detail.id);
      // Poll until complete
      const poll = setInterval(async () => {
        const status = await api.refreshStatus(task_id);
        if (status.status === "completed" || status.status === "error") {
          clearInterval(poll);
          setRefreshing(false);
          if (status.status === "completed") onRefresh();
        }
      }, 3000);
    } catch {
      setRefreshing(false);
    }
  };

  const handlePromote = async (type: "paper" | "real") => {
    const label = type === "paper" ? "Paper Trading" : "Real Trading";
    if (!confirm(`Move "${detail.name}" to ${label}?`)) return;
    setPromoting(true);
    try {
      await api.promotePortfolio(detail.id, type);
      onPromoted?.();
    } catch { alert("Failed to promote portfolio."); }
    setPromoting(false);
  };

  return (
    <div className="border-t border-ag-border p-3 space-y-3">
      {/* Strategy / Config Name Header */}
      {detail.batch_names && detail.batch_names.length > 0 && (
        <div className="space-y-1">
          {detail.batch_names.map((bn, i) => (
            <p key={i} className="text-sm font-medium text-ag-accent">{bn}</p>
          ))}
        </div>
      )}

      {/* Editable Description */}
      <div className="space-y-1">
        <textarea
          value={desc}
          onChange={(e) => { setDesc(e.target.value); setDescDirty(true); }}
          onBlur={() => { if (descDirty) saveDescription(); }}
          placeholder="Add a description..."
          rows={2}
          className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1.5 text-xs text-ag-text placeholder:text-ag-text-muted/50 resize-none focus:border-ag-accent focus:outline-none"
        />
        {descSaving && <p className="text-[10px] text-ag-text-muted">Saving...</p>}
      </div>

      <div className="flex items-center justify-between border-b border-ag-border/50 pb-1">
        <div className="flex items-center gap-2">
          <div className="flex gap-1">
            {tabs.map((t) => (
              <button key={t.id} onClick={() => setSubTab(t.id)}
                className={`px-3 py-1 text-xs rounded-t transition-colors ${
                  subTab === t.id ? "bg-ag-accent/15 text-ag-accent font-medium" : "text-ag-text-muted hover:text-ag-text"
                }`}>{t.label}</button>
            ))}
          </div>
          {freshness && (
            <span className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] ${
              freshness.all_fresh
                ? "bg-ag-green/15 text-ag-green"
                : "bg-yellow-400/15 text-yellow-400"
            }`} title={freshness.all_fresh ? "All asset data is up to date" : "Some assets have stale candle data"}>
              <span className={`inline-block w-1.5 h-1.5 rounded-full ${freshness.all_fresh ? "bg-ag-green" : "bg-yellow-400"}`} />
              {freshness.all_fresh ? "Data synced" : "Data stale"}
            </span>
          )}
        </div>
        <div className="flex gap-1.5">
          <button onClick={handleRefresh} disabled={refreshing}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text transition-colors disabled:opacity-50"
            title="Download latest candles and re-run backtest">
            <RefreshCw size={12} className={refreshing ? "animate-spin" : ""} />
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
          <button onClick={() => handlePromote("paper")} disabled={promoting}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-green/15 text-ag-green hover:bg-ag-green/25 transition-colors disabled:opacity-50">
            <FlaskConical size={12} /> Paper
          </button>
          <button onClick={() => handlePromote("real")} disabled={promoting}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 transition-colors disabled:opacity-50">
            <Wifi size={12} /> Real
          </button>
        </div>
      </div>

      {subTab === "details" && <DetailsView detail={detail} freshness={freshness} />}
      {subTab === "assets" && <PortfolioAssetTab portfolioId={detail.id} timeframe={String(detail.source_strategies?.timeframe ?? "")} strategyId={Number(detail.source_strategies?.strategy_id) || undefined} />}
      {subTab === "oos" && <PortfolioOOSPanel portfolioId={detail.id} optimizationEndDate={detail.optimization_end_date} timeframe={String(detail.source_strategies?.timeframe ?? "")} strategyId={Number(detail.source_strategies?.strategy_id) || undefined} />}
      {subTab === "health" && <PortfolioHealthMonitor portfolioId={detail.id} portfolioType={detail.portfolio_type} />}
      {subTab === "swap" && <AssetSwapTool portfolio={detail} onSwapped={onRefresh} />}
      {subTab === "live" && <LiveViewsPanel portfolioId={detail.id} />}
      {subTab === "test" && <PortfolioTestPanel portfolio={detail} />}
    </div>
  );
}

// Map human-readable full_metrics keys → snake_case ISvsLivePanel keys
function mapFullMetricsToKPI(
  m: Record<string, number | string | null>,
  fallback: Record<string, number>,
): Record<string, number> {
  const g = (k: string) => { const v = m[k]; return typeof v === "number" ? v : 0; };
  return {
    net_profit: g("Total Net Profit"),
    gross_profit: g("Gross Profit"),
    gross_loss: g("Gross Loss"),
    profit_factor: g("Profit Factor"),
    win_rate: g("Win Rate"),
    max_drawdown: g("Max Drawdown ($)"),
    avg_trade: g("Avg Trade Profit"),
    sortino: g("Sortino Ratio"),
    total_trades: g("Total Positions"),
    stability: fallback.stability ?? 0, // not in full_metrics, keep from snapshot
    calmar_ratio: g("Calmar Ratio"),
    recovery_factor: g("Recovery Factor"),
  };
}

function DetailsView({ detail, freshness }: { detail: PortfolioDetail; freshness: FreshnessData | null }) {
  const ss = (detail.source_strategies ?? {}) as Record<string, string | number | boolean | null | undefined | string[]>;
  const hasSettings = ss && Object.keys(ss).length > 0;
  const [oosMetrics, setOosMetrics] = useState<Record<string, number>>({});
  // IS metrics must ONLY come from the /oos-date endpoint (IS-only split).
  // Never fall back to metrics_snapshot — it could contain combined IS+OOS data.
  const [isMetrics, setIsMetrics] = useState<Record<string, number>>(
    detail.optimization_end_date ? {} : detail.metrics_snapshot
  );
  const [isLoadError, setIsLoadError] = useState(false);

  // Only use portfolioOOSDate() — it returns proper IS-only and OOS-only splits.
  // Never call portfolioFullMetrics() here — it returns combined IS+OOS data.
  useEffect(() => {
    if (!detail.optimization_end_date) return;
    setIsLoadError(false);
    api.portfolioOOSDate(detail.id, detail.optimization_end_date)
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
  }, [detail.id, detail.optimization_end_date]);

  // For WFO / hybrid Phase 2 portfolios, the fixed-basket metrics are a
  // misleading hypothetical — the real truth is the walk-forward rotation.
  // Hide the fixed-basket IS/OOS/Live comparison and Full Metrics panels for
  // those sources; show only the walk-forward panel.
  const isWfoSource =
    detail.portfolio_source === "wfo" || detail.portfolio_source === "wfas_phase2";

  return (
    <>
      {/* Real walk-forward view (WFO tracks + hybrid Phase 2 only). Shows
          stitched per-window / per-rotation OOS — the truth. */}
      <WalkForwardPanel detail={detail} />

      {/* IS vs OOS vs Live KPI comparison — hide for WFO (fixed-basket hypothetical) */}
      {!isWfoSource && (
        <>
          <ISvsLivePanel
            isMetrics={isMetrics}
            liveMetrics={detail.live_metrics_snapshot}
            oosMetrics={oosMetrics}
          />
          {isLoadError && (
            <p className="text-[10px] text-yellow-400 mt-1">
              Could not load IS-only metrics — showing &ldquo;&mdash;&rdquo; to prevent data contamination.
            </p>
          )}
        </>
      )}

      {/* Full KPI panel with equity curve, monthly matrix, L/S breakdown —
          hide for WFO sources (misleading fixed-basket hypothetical). */}
      {!isWfoSource && (
        <PortfolioFullMetrics portfolioId={detail.id} optimizationEndDate={detail.optimization_end_date} />
      )}

      {/* Optimizer Settings — full snapshot */}
      {hasSettings && (
        <details className="text-xs" open>
          <summary className="cursor-pointer text-ag-text-muted hover:text-ag-text font-medium">
            Optimizer Settings (Full)
          </summary>
          <div className="mt-2 space-y-3">

            {/* ── Core Optimization ── */}
            <SettingsGroup title="Core Optimization">
              {ss.objective != null && <SettingBadge label="Objective" value={String(ss.objective)} />}
              {ss.corr_method != null && <SettingBadge label="Correlation" value={String(ss.corr_method)} />}
              {ss.div_algo != null && <SettingBadge label="Diversification" value={String(ss.div_algo)} />}
              {ss.n_assets != null && <SettingBadge label="# Assets" value={String(ss.n_assets)} />}
              {ss.n_portfolios != null && <SettingBadge label="# Portfolios" value={String(ss.n_portfolios)} />}
              {ss.n_candidates != null && <SettingBadge label="# Candidates" value={String(ss.n_candidates)} />}
              {ss.split_mode != null && <SettingBadge label="Split Mode" value={ss.split_mode ? "L/S Split" : "Combined"} />}
              {ss.split_mode && ss.n_longs != null && <SettingBadge label="# Longs" value={String(ss.n_longs)} />}
              {ss.split_mode && ss.n_shorts != null && <SettingBadge label="# Shorts" value={String(ss.n_shorts)} />}
              {ss.within_objective != null && <SettingBadge label="Within Obj" value={String(ss.within_objective)} />}
              {ss.combined_objective != null && <SettingBadge label="Combined Obj" value={String(ss.combined_objective)} />}
              {ss.max_overlap != null && <SettingBadge label="Max Overlap" value={String(ss.max_overlap)} />}
              {ss.portfolio_source != null && <SettingBadge label="Source" value={String(ss.portfolio_source)} />}
            </SettingsGroup>

            {/* ── Data Period ── */}
            <SettingsGroup title="Data Period">
              {ss.date_from != null && <SettingBadge label="Start Date" value={String(ss.date_from)} />}
              {ss.date_to != null && <SettingBadge label="OOS Cutoff" value={String(ss.date_to)} />}
              {ss.timeframe != null && <SettingBadge label="Timeframe" value={String(ss.timeframe)} />}
              {Array.isArray(ss.extra_timeframes) && ss.extra_timeframes.length > 0 && (
                <SettingBadge label="Extra TFs" value={(ss.extra_timeframes as string[]).join(", ")} />
              )}
            </SettingsGroup>

            {/* ── Pre-Filters ── */}
            <SettingsGroup title="Pre-Filters">
              {ss.min_trades != null && <SettingBadge label="Min Trades" value={String(ss.min_trades)} />}
              {ss.min_pf != null && <SettingBadge label="Min PF" value={String(ss.min_pf)} />}
              {ss.min_wr != null && <SettingBadge label="Min WR" value={`${ss.min_wr}%`} />}
              {Array.isArray(ss.category_filter) && ss.category_filter.length > 0 && (
                <SettingBadge label="Categories" value={(ss.category_filter as string[]).join(", ")} />
              )}
              {ss.max_allowed_drawdown_pct != null && (
                <SettingBadge label="Max DD" value={`${ss.max_allowed_drawdown_pct}%`} />
              )}
            </SettingsGroup>

            {/* ── Backtest Costs (from batch) ── */}
            {detail.batch_settings && Object.keys(detail.batch_settings).length > 0 && (
              <SettingsGroup title="Backtest Costs">
                {detail.batch_settings.initial_capital != null && (
                  <SettingBadge label="Capital" value={`$${Number(detail.batch_settings.initial_capital).toLocaleString()}`} />
                )}
                {detail.batch_settings.commission_pct != null && (
                  <SettingBadge label="Commission" value={`${detail.batch_settings.commission_pct}%`} />
                )}
                {detail.batch_settings.slippage_value != null && (
                  <SettingBadge label="Slippage" value={`${detail.batch_settings.slippage_value}%`} />
                )}
                {detail.batch_settings.leverage != null && (
                  <SettingBadge label="Leverage" value={`${detail.batch_settings.leverage}x`} />
                )}
                {detail.batch_settings.risk_mode != null && (
                  <SettingBadge label="Risk Mode" value={String(detail.batch_settings.risk_mode)} />
                )}
                {detail.batch_settings.risk_value != null && Number(detail.batch_settings.risk_value) > 0 && (
                  <SettingBadge label="Risk Value" value={
                    detail.batch_settings.risk_mode === "fixed"
                      ? `$${detail.batch_settings.risk_value}`
                      : `${detail.batch_settings.risk_value}%`
                  } />
                )}
                {/* Warn when batches have different risk values */}
                {detail.batch_risk_values && Object.keys(detail.batch_risk_values).length > 1 && (
                  <div className="col-span-full bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-3 py-2 text-xs text-yellow-300">
                    <span className="font-semibold">Risk mismatch across batches:</span>{" "}
                    {Object.entries(detail.batch_risk_values as Record<string, {name: string; risk_value: number; risk_mode: string}>).map(([bid, info], i) => (
                      <span key={bid}>
                        {i > 0 && " · "}
                        {info.name}: {info.risk_mode === "fixed" ? `$${info.risk_value}` : `${info.risk_value}%`}
                      </span>
                    ))}
                  </div>
                )}
              </SettingsGroup>
            )}

            {/* ── Post-Hoc Stress Costs ── */}
            {(Number(ss.extra_slippage_pct) > 0 || Number(ss.extra_commission_pct) > 0 ||
              Number(ss.funding_daily_pct) > 0 || Number(ss.spread_pct) > 0) && (
              <SettingsGroup title="Stress-Test Costs">
                {Number(ss.extra_slippage_pct) > 0 && (
                  <SettingBadge label="Extra Slippage" value={`${ss.extra_slippage_pct}%`} />
                )}
                {Number(ss.extra_commission_pct) > 0 && (
                  <SettingBadge label="Extra Commission" value={`${ss.extra_commission_pct}%`} />
                )}
                {Number(ss.funding_daily_pct) > 0 && (
                  <SettingBadge label="Funding Rate" value={`${ss.funding_daily_pct}%/day`} />
                )}
                {Number(ss.spread_pct) > 0 && (
                  <SettingBadge label="Spread" value={`${ss.spread_pct}%`} />
                )}
              </SettingsGroup>
            )}

            {/* ── Seed Ranking ── */}
            {(Array.isArray(ss.seed_metrics) || ss.max_seeds != null || ss.prioritize_stability) && (
              <SettingsGroup title="Seed Ranking">
                {Array.isArray(ss.seed_metrics) && ss.seed_metrics.length > 0 && (
                  <SettingBadge label="Metrics" value={(ss.seed_metrics as string[]).join(", ")} />
                )}
                {ss.max_seeds != null && <SettingBadge label="Max Seeds" value={String(ss.max_seeds)} />}
                {ss.prioritize_stability && <SettingBadge label="Stability" value="Prioritized" />}
                {ss.seed_weights != null && typeof ss.seed_weights === "object" && !Array.isArray(ss.seed_weights) && (
                  <SettingBadge label="Weights" value={Object.entries(ss.seed_weights as unknown as Record<string, number>).map(([k, v]) => `${k}:${v}`).join(", ")} />
                )}
              </SettingsGroup>
            )}

            {/* ── Trade-Count Constraints ── */}
            {ss.tc_enabled && (
              <SettingsGroup title="Trade-Count Constraints">
                <SettingBadge label="TC" value="Active" />
                {ss.tc_low_threshold != null && <SettingBadge label="Low Threshold" value={String(ss.tc_low_threshold)} />}
                {ss.tc_max_low != null && <SettingBadge label="Max Below Low" value={String(ss.tc_max_low)} />}
                {ss.tc_high_threshold != null && <SettingBadge label="High Threshold" value={String(ss.tc_high_threshold)} />}
                {ss.tc_min_high != null && <SettingBadge label="Min Above High" value={String(ss.tc_min_high)} />}
              </SettingsGroup>
            )}

            {/* ── Benchmark Robustness ── */}
            {ss.benchmark_enabled && (
              <SettingsGroup title="Benchmark Robustness">
                <SettingBadge label="Benchmark" value="Active" />
                {ss.benchmark_source != null && <SettingBadge label="Source" value={String(ss.benchmark_source)} />}
                {ss.benchmark_split != null && <SettingBadge label="Split" value={String(ss.benchmark_split)} />}
                {ss.benchmark_threshold != null && <SettingBadge label="PF Threshold" value={`≥${ss.benchmark_threshold}`} />}
                {ss.benchmark_elite_target != null && <SettingBadge label="Elite Target" value={String(ss.benchmark_elite_target)} />}
                {ss.benchmark_exclude_fragile && <SettingBadge label="Excl. Fragile" value="Yes" />}
                {ss.benchmark_dsr_gate && <SettingBadge label="DSR Gate" value="Yes" />}
                {ss.benchmark_consistency_blend != null && (
                  <SettingBadge label="Consistency" value={String(ss.benchmark_consistency_blend)} />
                )}
                {ss.benchmark_fragility_blend != null && (
                  <SettingBadge label="Fragility" value={String(ss.benchmark_fragility_blend)} />
                )}
                {ss.benchmark_wr_harmony_blend != null && (
                  <SettingBadge label="WR Harmony" value={String(ss.benchmark_wr_harmony_blend)} />
                )}
              </SettingsGroup>
            )}

            {/* ── Recency Weighting ── */}
            {ss.recency_enabled && (
              <SettingsGroup title="Recency Weighting">
                <SettingBadge label="Recency" value="Active" />
                {ss.recency_mode != null && <SettingBadge label="Mode" value={String(ss.recency_mode)} />}
                {ss.recency_batch_size != null && <SettingBadge label="Batch Size" value={String(ss.recency_batch_size)} />}
                {ss.recency_boost_factor != null && <SettingBadge label="Boost Factor" value={`${ss.recency_boost_factor}x`} />}
              </SettingsGroup>
            )}

            {/* ── Risk Profile ── */}
            {ss.risk_profile_mode != null && (
              <SettingsGroup title="Risk Profile">
                <SettingBadge label="Mode" value={String(ss.risk_profile_mode)} />
                {ss.risk_profile_value != null && (
                  <SettingBadge label="Value" value={String(ss.risk_profile_value)} />
                )}
                {ss.risk_profile_initial_capital != null && (
                  <SettingBadge label="Initial Capital" value={`$${Number(ss.risk_profile_initial_capital).toLocaleString()}`} />
                )}
                {ss.risk_profile_multiplier != null && (
                  <SettingBadge label="Multiplier" value={`${ss.risk_profile_multiplier}x`} />
                )}
                {ss.risk_profile_max_streak != null && (
                  <SettingBadge label="Max Streak" value={String(ss.risk_profile_max_streak)} />
                )}
                {ss.risk_profile_martingale_compound != null && (
                  <SettingBadge label="Compound" value={String(ss.risk_profile_martingale_compound)} />
                )}
                {ss.risk_profile_sizing_master === "per_batch" && (
                  <SettingBadge label="Sizing" value="Per Batch" />
                )}
                {ss.risk_profile_martingale_scope && ss.risk_profile_martingale_scope !== "asset" && ss.risk_profile_sizing_master !== "per_batch" && (
                  <SettingBadge label="Scope" value={
                    ss.risk_profile_martingale_scope === "portfolio" ? "Portfolio-wide" :
                    ss.risk_profile_martingale_scope === "asset_batch" ? "Asset + Batch" :
                    String(ss.risk_profile_martingale_scope)
                  } />
                )}
                {ss.risk_profile_kelly_n != null && (
                  <SettingBadge label="Kelly N" value={String(ss.risk_profile_kelly_n)} />
                )}
                {ss.risk_profile_kelly_warmup != null && (
                  <SettingBadge label="Kelly Warmup" value={`${ss.risk_profile_kelly_warmup} trades`} />
                )}
              </SettingsGroup>
            )}

            {/* ── Quality Pre-Filter ── */}
            {ss.prefilter_enabled && (
              <SettingsGroup title="Quality Pre-Filter">
                <SettingBadge label="Pre-Filter" value="Active" />
                {ss.prefilter_mode != null && <SettingBadge label="Mode" value={String(ss.prefilter_mode)} />}
                {ss.prefilter_hurst_min != null && <SettingBadge label="Hurst Min" value={String(ss.prefilter_hurst_min)} />}
                {ss.prefilter_hurst_max != null && <SettingBadge label="Hurst Max" value={String(ss.prefilter_hurst_max)} />}
                {ss.prefilter_ter_percentile != null && <SettingBadge label="TER %ile" value={String(ss.prefilter_ter_percentile)} />}
                {ss.prefilter_vr_max != null && <SettingBadge label="VR Max" value={String(ss.prefilter_vr_max)} />}
                {ss.prefilter_min_crossing_rate != null && (
                  <SettingBadge label="Min Cross Rate" value={String(ss.prefilter_min_crossing_rate)} />
                )}
              </SettingsGroup>
            )}

            {/* ── AC Post-Filter ── */}
            {ss.ac_postfilter && (
              <SettingsGroup title="Autocorrelation Filter">
                <SettingBadge label="AC Filter" value="Active" />
                {ss.ac_postfilter_type != null && <SettingBadge label="Type" value={String(ss.ac_postfilter_type)} />}
                {ss.ac_lag != null && <SettingBadge label="Lag" value={String(ss.ac_lag)} />}
              </SettingsGroup>
            )}

            {/* ── IS-WFE Seed ── */}
            {(ss.is_wfe_filter || ss.is_wfe_is_pct != null) && (
              <SettingsGroup title="IS-WFE Seed">
                {ss.is_wfe_filter && <SettingBadge label="WFE Filter" value="Active" />}
                {ss.is_wfe_is_pct != null && <SettingBadge label="IS %" value={`${ss.is_wfe_is_pct}%`} />}
                {ss.is_wfe_min_trades != null && Number(ss.is_wfe_min_trades) > 0 && (
                  <SettingBadge label="Min Trades" value={String(ss.is_wfe_min_trades)} />
                )}
                {ss.is_wfe_min_wfe != null && <SettingBadge label="Min WFE" value={String(ss.is_wfe_min_wfe)} />}
              </SettingsGroup>
            )}

            {/* ── Asset Quality Seed ── */}
            {(ss.asset_quality_filter || ss.asset_quality_mode != null) && (
              <SettingsGroup title="Asset Quality Seed">
                {ss.asset_quality_filter && <SettingBadge label="Filter" value="Active" />}
                {ss.asset_quality_mode != null && <SettingBadge label="Mode" value={String(ss.asset_quality_mode)} />}
                {ss.asset_quality_grade_mode != null && <SettingBadge label="Grade Mode" value={String(ss.asset_quality_grade_mode)} />}
                {ss.asset_quality_min_grade != null && String(ss.asset_quality_min_grade) !== "" && (
                  <SettingBadge label="Min Grade" value={String(ss.asset_quality_min_grade)} />
                )}
                {ss.asset_quality_min_trades != null && Number(ss.asset_quality_min_trades) > 0 && (
                  <SettingBadge label="Min Trades" value={String(ss.asset_quality_min_trades)} />
                )}
                {ss.asset_quality_hurst_min != null && <SettingBadge label="Hurst Min" value={String(ss.asset_quality_hurst_min)} />}
                {ss.asset_quality_hurst_max != null && <SettingBadge label="Hurst Max" value={String(ss.asset_quality_hurst_max)} />}
                {ss.asset_quality_ter_percentile != null && <SettingBadge label="TER %ile" value={String(ss.asset_quality_ter_percentile)} />}
                {ss.asset_quality_vr_max != null && <SettingBadge label="VR Max" value={String(ss.asset_quality_vr_max)} />}
                {ss.asset_quality_min_cr != null && <SettingBadge label="Min CR" value={String(ss.asset_quality_min_cr)} />}
              </SettingsGroup>
            )}

            {/* ── Strategy Quality Seed ── */}
            {(ss.strat_quality_filter || ss.strat_quality_mode != null) && (
              <SettingsGroup title="Strategy Quality Seed">
                {ss.strat_quality_filter && <SettingBadge label="Filter" value="Active" />}
                {ss.strat_quality_mode != null && <SettingBadge label="Mode" value={String(ss.strat_quality_mode)} />}
                {ss.strat_quality_min_grade != null && String(ss.strat_quality_min_grade) !== "" && (
                  <SettingBadge label="Min Grade" value={String(ss.strat_quality_min_grade)} />
                )}
              </SettingsGroup>
            )}

            {/* ── Boundary Clipping ── */}
            {(ss.clip_is != null || ss.clip_oos != null || ss.oos_end_date != null) && (
              <SettingsGroup title="Boundary Clipping">
                {ss.clip_is != null && <SettingBadge label="Clip IS" value={ss.clip_is ? "Yes" : "No"} />}
                {ss.clip_oos != null && <SettingBadge label="Clip OOS" value={ss.clip_oos ? "Yes" : "No"} />}
                {ss.oos_end_date != null && <SettingBadge label="OOS End" value={String(ss.oos_end_date)} />}
              </SettingsGroup>
            )}

            {/* ── Correlation Jitter ── */}
            {ss.corr_jitter_enabled && (
              <SettingsGroup title="Correlation Jitter">
                <SettingBadge label="Jitter" value="Active" />
                {ss.corr_jitter_temperature != null && (
                  <SettingBadge label="Temperature" value={String(ss.corr_jitter_temperature)} />
                )}
              </SettingsGroup>
            )}

            {/* ── Post-Construction Refinement ── */}
            {ss.refinement_enabled && (
              <SettingsGroup title="Post-Construction Refinement">
                <SettingBadge label="Refinement" value="Active" />
                {ss.refinement_mode != null && <SettingBadge label="Mode" value={String(ss.refinement_mode)} />}
                {ss.refinement_metric != null && String(ss.refinement_metric) !== "" && (
                  <SettingBadge label="Metric" value={String(ss.refinement_metric)} />
                )}
              </SettingsGroup>
            )}

            {/* ── Overfitting Guards ── */}
            {ss.overfit_guard_enabled && (
              <SettingsGroup title="Overfitting Guards">
                <SettingBadge label="Guards" value="Active" />
                {ss.of_log_enabled && <SettingBadge label="Log Caps" value="On" />}
                {ss.of_z_score_enabled && (
                  <SettingBadge label="Z-Score" value={`thresh=${ss.of_z_threshold ?? "?"}, str=${ss.of_z_penalty_strength ?? "?"}`} />
                )}
                {ss.of_bayes_enabled && <SettingBadge label="Bayesian" value={`k=${ss.of_bayes_k ?? "?"}`} />}
                {ss.of_sigmoid_enabled && <SettingBadge label="Sigmoid Caps" value="On" />}
                {ss.of_normal_range_enabled && (
                  <SettingBadge label="Normal Range" value={`${ss.of_nr_pct_low ?? "?"}–${ss.of_nr_pct_high ?? "?"}%`} />
                )}
                {ss.of_wf_rerank_enabled && (
                  <SettingBadge label="WF Rerank" value={`split=${ss.of_wf_split_pct ?? "?"}%, metric=${ss.of_wf_validation_metric ?? "?"}`} />
                )}
              </SettingsGroup>
            )}

            {/* ── Cross-Strategy Beam Search ── */}
            {(ss.mode === "directional" || ss.obj_l1 != null) && (
              <SettingsGroup title="Beam Search (Cross-Strategy)">
                {ss.mode != null && <SettingBadge label="Mode" value={String(ss.mode)} />}
                {ss.obj_l1 != null && <SettingBadge label="L1 Objective" value={String(ss.obj_l1)} />}
                {ss.obj_l2 != null && <SettingBadge label="L2 Objective" value={String(ss.obj_l2)} />}
                {ss.obj_l3 != null && <SettingBadge label="L3 Objective" value={String(ss.obj_l3)} />}
                {ss.k_l1 != null && <SettingBadge label="K (L1)" value={String(ss.k_l1)} />}
                {ss.k_l2 != null && <SettingBadge label="K (L2)" value={String(ss.k_l2)} />}
                {ss.k_l3 != null && <SettingBadge label="K (L3)" value={String(ss.k_l3)} />}
                {ss.l1_quality_gate && <SettingBadge label="L1 Quality Gate" value="Active" />}
                {ss.n_per_group != null && <SettingBadge label="Per Group" value={String(ss.n_per_group)} />}
                {ss.n_per_batch != null && <SettingBadge label="Per Batch" value={String(ss.n_per_batch)} />}
                {Array.isArray(ss.l2_groups) && (ss.l2_groups as unknown[]).length > 0 && (
                  <SettingBadge label="L2 Groups" value={`${(ss.l2_groups as unknown[]).length} group(s)`} />
                )}
              </SettingsGroup>
            )}

            {/* ── Manual Mode (Cross-Strategy) ── */}
            {ss.mode === "manual" && (
              <SettingsGroup title="Manual Mode (Cross-Strategy)">
                {ss.obj_layer1 != null && <SettingBadge label="Layer 1" value={String(ss.obj_layer1)} />}
                {ss.obj_layer2 != null && <SettingBadge label="Layer 2" value={String(ss.obj_layer2)} />}
                {ss.obj_layer3 != null && <SettingBadge label="Layer 3" value={String(ss.obj_layer3)} />}
                {ss.candidates_per_strategy != null && <SettingBadge label="Candidates/Strat" value={String(ss.candidates_per_strategy)} />}
                {ss.n_final_strats != null && <SettingBadge label="Final Strats" value={String(ss.n_final_strats)} />}
                {ss.n_portfolios != null && <SettingBadge label="# Portfolios" value={String(ss.n_portfolios)} />}
              </SettingsGroup>
            )}

            {/* ── Misc Advanced ── */}
            {(ss.expand_greedy_pool || ss.seed_blind_selection || ss.streamlit_logic || Number(ss.seed_prior_weight) > 0 || Number(ss.pool_per_batch) > 0) && (
              <SettingsGroup title="Advanced Options">
                {ss.expand_greedy_pool && <SettingBadge label="Greedy Pool" value="Expanded" />}
                {ss.seed_blind_selection && <SettingBadge label="Blind Selection" value="Yes" />}
                {ss.streamlit_logic && <SettingBadge label="Streamlit Logic" value="Yes" />}
                {Number(ss.seed_prior_weight) > 0 && <SettingBadge label="Seed Prior" value={String(ss.seed_prior_weight)} />}
                {Number(ss.pool_per_batch) > 0 && <SettingBadge label="Pool/Batch" value={String(ss.pool_per_batch)} />}
              </SettingsGroup>
            )}

            {/* ── WFO Settings (if portfolio_source is wfo) ── */}
            {(ss.is_months != null || ss.oos_months != null) && (
              <SettingsGroup title="Walk-Forward">
                {ss.is_months != null && <SettingBadge label="IS Months" value={String(ss.is_months)} />}
                {ss.oos_months != null && <SettingBadge label="OOS Months" value={String(ss.oos_months)} />}
                {ss.last_oos_end != null && <SettingBadge label="Last OOS End" value={String(ss.last_oos_end)} />}
                {ss.time_mode != null && <SettingBadge label="Time Mode" value={String(ss.time_mode)} />}
                {ss.event_mode && <SettingBadge label="Event Mode" value="Yes" />}
              </SettingsGroup>
            )}

            {/* ── Event-Based Mode ── */}
            {ss.event_mode && (
              <SettingsGroup title="Event-Based Mode">
                {ss.n_trades != null && <SettingBadge label="Lookback Trades" value={String(ss.n_trades)} />}
                {ss.staleness_days != null && <SettingBadge label="Staleness Days" value={String(ss.staleness_days)} />}
                {ss.n_per_group != null && <SettingBadge label="Per Group" value={String(ss.n_per_group)} />}
                {ss.rebalance_every != null && <SettingBadge label="Rebal Every" value={String(ss.rebalance_every)} />}
                {ss.rebalance_mode != null && <SettingBadge label="Rebal Mode" value={String(ss.rebalance_mode)} />}
              </SettingsGroup>
            )}

            {/* ── Directional Batch / Manual Mode ── */}
            {(ss.directional_batches || ss.wfo_mode === "manual") && (
              <SettingsGroup title="Advanced Mode">
                {ss.wfo_mode != null && <SettingBadge label="WFO Mode" value={String(ss.wfo_mode)} />}
                {ss.directional_batches && <SettingBadge label="Directional" value="Yes" />}
                {ss.obj_layer1 != null && <SettingBadge label="Layer 1" value={String(ss.obj_layer1)} />}
                {ss.obj_layer2 != null && <SettingBadge label="Layer 2" value={String(ss.obj_layer2)} />}
                {ss.obj_layer3 != null && <SettingBadge label="Layer 3" value={String(ss.obj_layer3)} />}
                {ss.n_per_batch != null && <SettingBadge label="Per Batch" value={String(ss.n_per_batch)} />}
              </SettingsGroup>
            )}

          </div>
        </details>
      )}

      {/* Assets grouped by side — only active assets */}
      <div>
        <h4 className="text-xs text-ag-text-muted mb-1">Assets</h4>
        <div className="flex flex-wrap gap-1.5">
          {detail.assets.filter((a) => a.is_active !== false).map((a) => {
            const isLong = a.side === "Long";
            const isShort = a.side === "Short";
            const color = isLong ? "bg-ag-green/15 text-ag-green"
              : isShort ? "bg-ag-red/15 text-ag-red"
              : "bg-ag-accent/15 text-ag-accent";
            // Per-asset freshness dot
            const af = freshness?.assets.find((f) => f.symbol === a.symbol);
            const dotColor = !af ? "" : af.is_fresh ? "bg-ag-green" : af.candles_behind < 10 ? "bg-yellow-400" : "bg-ag-red";
            const dotTitle = !af ? "" : af.is_fresh ? "Data up to date" : `${af.candles_behind.toFixed(0)} candles behind`;
            // Batch name from batch_id_map
            const batchName = a.batch_id && detail.batch_id_map?.[a.batch_id]
              ? detail.batch_id_map[a.batch_id] : "";
            return (
              <span key={a.id} className={`inline-flex items-center gap-1 px-2 py-0.5 ${color} rounded text-xs`}>
                {af && <span className={`inline-block w-1.5 h-1.5 rounded-full ${dotColor} shrink-0`} title={dotTitle} />}
                {a.symbol.replace("USDT_Long", "").replace("USDT_Short", "").replace("USDT", "")}
                {a.side !== "both" && ` (${a.side[0]})`}
                {batchName && <span className="opacity-50 ml-0.5">{batchName}</span>}
              </span>
            );
          })}
        </div>
      </div>
    </>
  );
}

function SettingsGroup({ title, children }: { title: string; children: React.ReactNode }) {
  // Filter out null/false children (conditional renders that didn't match)
  const validChildren = Array.isArray(children)
    ? children.filter(Boolean)
    : children ? [children] : [];
  if (validChildren.length === 0) return null;
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-ag-text-muted/60 mb-1">{title}</p>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1.5">
        {children}
      </div>
    </div>
  );
}

function SettingBadge({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-1 px-2 py-1 bg-ag-dark/50 rounded">
      <span className="text-ag-text-muted">{label}:</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}
