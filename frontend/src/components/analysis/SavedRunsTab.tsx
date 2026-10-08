"use client";

import React, { useState, useEffect, useCallback } from "react";
import { Trash2, Save, Sparkles, Download } from "lucide-react";
import { api } from "@/lib/api";
import type { SavedRunSummary, SavedRunDetail as SavedRunDetailType, OOSCompareResult } from "@/lib/api";
import { ALGO_INFO, SETTING_LABELS, PROFILE_LABELS, formatSettingValue, friendlySettingValue } from "./recommenderConstants";

/**
 * Map recommender full_settings (snake_case) → optimizer SharedSettings (camelCase).
 * Mirrors the logic in SettingsRecommender.tsx applyPreset(), plus decomposes
 * nested dicts like of_log_caps into flat camelCase keys.
 */
function recommenderToSharedSettings(fs: Record<string, unknown>): Record<string, unknown> {
  const shared: Record<string, unknown> = {};

  // ── Algorithm & seeds ──
  if (fs.div_algo !== undefined) shared.divAlgo = String(fs.div_algo);
  if (fs.seed_prior_weight !== undefined) shared.seedPriorWeight = Number(fs.seed_prior_weight);
  if (fs.seed_metrics && Array.isArray(fs.seed_metrics)) shared.seedMetrics = fs.seed_metrics;

  // ── Filters ──
  if (fs.min_pf !== undefined) shared.minPf = Number(fs.min_pf);
  if (fs.min_trades !== undefined) shared.minTrades = Number(fs.min_trades);
  if (fs.min_wr !== undefined) shared.minWr = Number(fs.min_wr);

  // ── Portfolio gates & per-asset PF cap ──
  if (fs.max_asset_pf !== undefined) shared.maxAssetPf = Number(fs.max_asset_pf);
  if (fs.cap_asset_pf !== undefined) shared.capAssetPf = Boolean(fs.cap_asset_pf);
  if (fs.portfolio_min_pf !== undefined) shared.portfolioMinPf = Number(fs.portfolio_min_pf);
  if (fs.portfolio_min_wr !== undefined) shared.portfolioMinWr = Number(fs.portfolio_min_wr);

  // ── Bybit size gate ──
  if (fs.bybit_size_gate !== undefined) {
    const maxPct = Number(fs.bybit_size_gate_max_reject_pct ?? 0);
    shared.bybitSizeGate = Boolean(fs.bybit_size_gate) ? (maxPct > 0 ? "rate" : "binary") : "off";
    shared.bybitSizeGateMaxPct = maxPct > 0 ? maxPct : 10;
  }

  // ── Overfitting guard master toggle ──
  if (fs.overfit_guard_enabled !== undefined) shared.overfitGuardEnabled = Boolean(fs.overfit_guard_enabled);

  // ── Log caps: backend sends nested dict of_log_caps: {pf, sortino, payoff, wr, sharpe, calmar} ──
  if (fs.of_log_enabled !== undefined) shared.ofLogEnabled = Boolean(fs.of_log_enabled);
  if (fs.of_log_caps && typeof fs.of_log_caps === "object") {
    const caps = fs.of_log_caps as Record<string, number>;
    if (caps.pf !== undefined) shared.ofLogCapPf = Number(caps.pf);
    if (caps.sortino !== undefined) shared.ofLogCapSortino = Number(caps.sortino);
    if (caps.payoff !== undefined) shared.ofLogCapPayoff = Number(caps.payoff);
    if (caps.wr !== undefined) shared.ofLogCapWr = Number(caps.wr);
    if (caps.sharpe !== undefined) shared.ofLogCapSharpe = Number(caps.sharpe);
    if (caps.calmar !== undefined) shared.ofLogCapCalmar = Number(caps.calmar);
  }

  // ── Z-score guard ──
  if (fs.of_z_score_enabled !== undefined) shared.ofZScoreEnabled = Boolean(fs.of_z_score_enabled);
  if (fs.of_z_threshold !== undefined) shared.ofZThreshold = Number(fs.of_z_threshold);
  if (fs.of_z_penalty_strength !== undefined) shared.ofZPenaltyStrength = Number(fs.of_z_penalty_strength);

  // ── Bayesian guard ──
  if (fs.of_bayes_enabled !== undefined) shared.ofBayesEnabled = Boolean(fs.of_bayes_enabled);
  if (fs.of_bayes_k !== undefined) shared.ofBayesK = Number(fs.of_bayes_k);

  // ── Sigmoid guard ──
  if (fs.of_sigmoid_enabled !== undefined) shared.ofSigmoidEnabled = Boolean(fs.of_sigmoid_enabled);

  // ── Normal range guard ──
  if (fs.of_normal_range_enabled !== undefined) shared.ofNormalRangeEnabled = Boolean(fs.of_normal_range_enabled);
  if (fs.of_nr_pct_low !== undefined) shared.ofNrPctLow = Number(fs.of_nr_pct_low);
  if (fs.of_nr_pct_high !== undefined) shared.ofNrPctHigh = Number(fs.of_nr_pct_high);
  if (fs.of_nr_penalty_scale !== undefined) shared.ofNrPenaltyScale = Number(fs.of_nr_penalty_scale);
  if (fs.of_nr_bonus !== undefined) shared.ofNrBonus = Number(fs.of_nr_bonus);

  // ── Walk-forward rerank guard ──
  if (fs.of_wf_rerank_enabled !== undefined) shared.ofWfRerankEnabled = Boolean(fs.of_wf_rerank_enabled);
  if (fs.of_wf_split_pct !== undefined) shared.ofWfSplitPct = Number(fs.of_wf_split_pct);
  if (fs.of_wf_validation_metric !== undefined) shared.ofWfValidationMetric = String(fs.of_wf_validation_metric);

  // ── Refinement ──
  if (fs.refinement_enabled !== undefined) shared.refinementEnabled = Boolean(fs.refinement_enabled);
  if (fs.refinement_mode !== undefined) shared.refinementMode = String(fs.refinement_mode);

  // ── Correlation jitter ──
  if (fs.corr_jitter_enabled !== undefined) shared.corrJitterEnabled = Boolean(fs.corr_jitter_enabled);
  if (fs.corr_jitter_temperature !== undefined) shared.corrJitterTemperature = Number(fs.corr_jitter_temperature);

  // ── Run-context: costs, dates, corr method ──
  if (fs.extra_slippage_pct !== undefined) shared.extraSlippage = Number(fs.extra_slippage_pct);
  if (fs.extra_commission_pct !== undefined) shared.extraCommission = Number(fs.extra_commission_pct);
  if (fs.funding_daily_pct !== undefined) shared.fundingDaily = Number(fs.funding_daily_pct);
  if (fs.spread_pct !== undefined) shared.spread = Number(fs.spread_pct);
  if (fs.date_from) shared.dateFrom = String(fs.date_from);
  if (fs.corr_method) shared.corrMethod = String(fs.corr_method);

  return shared;
}

/** Save a recommender preset as an optimizer config in localStorage (same key as OptimizerConfigManager). */
function savePresetAsOptimizerConfig(
  fullSettings: Record<string, unknown>,
  presetName: string,
  notes?: string,
): boolean {
  const STORAGE_KEY = "optimizer_cross_configs";
  try {
    const shared = recommenderToSharedSettings(fullSettings);
    // Also map objective + asset counts + pool mode → autoS
    const autoS: Record<string, unknown> = {};
    if (fullSettings.objective) {
      autoS.combinedObj = String(fullSettings.objective);
      autoS.withinObj = "uncorrelated";
    }
    if (fullSettings.split_mode !== undefined) autoS.splitMode = Boolean(fullSettings.split_mode);
    if (fullSettings.n_assets !== undefined) autoS.nAssets = Number(fullSettings.n_assets);
    if (fullSettings.n_longs !== undefined) autoS.nLongs = Number(fullSettings.n_longs);
    if (fullSettings.n_shorts !== undefined) autoS.nShorts = Number(fullSettings.n_shorts);

    const config = { shared, autoS };
    const stored = localStorage.getItem(STORAGE_KEY);
    const configs: Array<{ name: string; config: unknown; savedAt: string; notes?: string }> =
      stored ? JSON.parse(stored) : [];
    // Overwrite if same name exists
    const filtered = configs.filter((c) => c.name !== presetName);
    filtered.push({
      name: presetName,
      config,
      savedAt: new Date().toISOString(),
      notes,
    });
    localStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
    return true;
  } catch {
    return false;
  }
}

interface CleanupFlag {
  filename: string; type: string; saved_at: string;
  n_batches: number; n_candidates: number; n_assets: number;
}

const TYPE_BADGES: Record<string, { label: string; color: string }> = {
  build: { label: "Build", color: "bg-blue-600/20 text-blue-400" },
  recommend_quick: { label: "Quick Recommend", color: "bg-green-600/20 text-green-400" },
  recommend_thorough: { label: "Thorough Recommend", color: "bg-purple-600/20 text-purple-400" },
};

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-US", {
      month: "short", day: "numeric", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/** Format seconds as human-readable elapsed time (e.g. "1h 34m 33s") */
function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.round(seconds % 60);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  return `${m}m ${s}s`;
}

/** Build a descriptive title from run summary fields */
function buildRunTitle(run: SavedRunSummary): string {
  const parts: string[] = ["C-S"];
  const s = run.summary;
  if (run.type === "build") {
    if (s.div_algo) parts.push(ALGO_INFO[s.div_algo]?.display || s.div_algo);
    if (s.combined_objective) {
      const friendly = friendlySettingValue("objective", s.combined_objective);
      parts.push(friendly);
    }
    if (s.seed_metrics && s.seed_metrics.length > 0) parts.push(s.seed_metrics.join(", "));
    if (s.n_final_assets) parts.push(`${s.n_final_assets} assets`);
    // Fallback if no config fields (legacy saves)
    if (parts.length === 1) {
      parts.push("Build");
      if (s.n_candidates) parts.push(`${s.n_candidates} candidates`);
    }
  } else if (run.type === "recommend_thorough") {
    parts.push("Thorough Recommend");
    if (s.n_assets) parts.push(`${s.n_assets} assets`);
    if (s.n_top_presets) parts.push(`${s.n_top_presets} presets`);
  } else if (run.type === "recommend_quick") {
    parts.push("Quick Recommend");
    if (s.n_assets) parts.push(`${s.n_assets} assets`);
  }
  return parts.join(" · ");
}

type RunFilter = "all" | "optimizations" | "recommender";

export default function SavedRunsTab() {
  const [runs, setRuns] = useState<SavedRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedFile, setExpandedFile] = useState<string | null>(null);
  const [detail, setDetail] = useState<SavedRunDetailType | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [cleanupPreview, setCleanupPreview] = useState<CleanupFlag[] | null>(null);
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [filter, setFilter] = useState<RunFilter>("all");

  // Filter runs by sub-tab
  const filteredRuns = runs.filter((r) => {
    if (filter === "optimizations") return r.type === "build";
    if (filter === "recommender") return r.type.startsWith("recommend");
    return true;
  });
  const nBuilds = runs.filter((r) => r.type === "build").length;
  const nRecommends = runs.filter((r) => r.type.startsWith("recommend")).length;

  const handleCleanup = async () => {
    // Step 1: dry run to preview
    setCleanupLoading(true);
    try {
      const res = await api.savedRunsCleanup({
        min_batches: 3, min_candidates: 3, min_assets: 50, dry_run: true,
      });
      if (res.flagged.length === 0) {
        alert("Nothing to clean up — all runs meet the quality thresholds (3+ batches, 3+ candidates, 50+ assets).");
        return;
      }
      setCleanupPreview(res.flagged);
    } catch {
      alert("Cleanup preview failed");
    } finally {
      setCleanupLoading(false);
    }
  };

  const confirmCleanup = async () => {
    if (!cleanupPreview) return;
    setCleanupLoading(true);
    try {
      const res = await api.savedRunsCleanup({
        min_batches: 3, min_candidates: 3, min_assets: 50, dry_run: false,
      });
      setCleanupPreview(null);
      fetchRuns();
      alert(`Deleted ${res.deleted} small run${res.deleted !== 1 ? "s" : ""}. ${res.kept} kept.`);
    } catch {
      alert("Cleanup failed");
    } finally {
      setCleanupLoading(false);
    }
  };

  const toggleSelect = (filename: string) => {
    setSelectedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) next.delete(filename);
      else next.add(filename);
      return next;
    });
  };

  const handleBulkDelete = async () => {
    if (selectedFiles.size === 0) return;
    const count = selectedFiles.size;
    if (!confirm(`Delete ${count} saved run${count !== 1 ? "s" : ""}? This cannot be undone.`)) return;
    setBulkDeleting(true);
    try {
      for (const filename of selectedFiles) {
        await api.savedRunsDelete(filename);
      }
      setSelectedFiles(new Set());
      if (expandedFile && selectedFiles.has(expandedFile)) {
        setExpandedFile(null);
        setDetail(null);
      }
      fetchRuns();
    } catch {
      fetchRuns();
    } finally {
      setBulkDeleting(false);
    }
  };

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    setError("");
    // Fix (2026-04-08): auto-retry once after a brief delay to absorb
    // cold-start / race-condition failures that previously left the list
    // blank with an easy-to-miss error.
    let lastErr: unknown = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const data = await api.savedRunsList();
        setRuns(data);
        setLoading(false);
        return;
      } catch (e: unknown) {
        lastErr = e;
        if (attempt === 0) {
          await new Promise((res) => setTimeout(res, 1000));
        }
      }
    }
    setError(lastErr instanceof Error ? lastErr.message : "Failed to load saved runs");
    setLoading(false);
  }, []);

  useEffect(() => { fetchRuns(); }, [fetchRuns]);

  const loadDetail = async (filename: string) => {
    setExpandedFile(filename);
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    try {
      const data = await api.savedRunsLoad(filename);
      setDetail(data);
    } catch (e: unknown) {
      setDetail(null);
      const msg = e instanceof Error ? e.message : "Failed to load details";
      setDetailError(msg);
      // If file was deleted (stale list), auto-refresh the list
      if (msg.includes("not found") || msg.includes("404")) {
        fetchRuns();
      }
    } finally {
      setDetailLoading(false);
    }
  };

  const handleExpand = async (filename: string) => {
    if (expandedFile === filename) {
      setExpandedFile(null);
      setDetail(null);
      setDetailError("");
      return;
    }
    loadDetail(filename);
  };

  const handleDelete = async (filename: string) => {
    setDeleting(filename);
    try {
      await api.savedRunsDelete(filename);
      setRuns((prev) => prev.filter((r) => r.filename !== filename));
      if (expandedFile === filename) {
        setExpandedFile(null);
        setDetail(null);
      }
    } catch {
      // silently fail
    } finally {
      setDeleting(null);
      setConfirmDelete(null);
    }
  };

  if (loading) {
    return (
      <div className="text-center py-12">
        <p className="text-ag-text-muted">Loading saved runs...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <p className="text-red-400 mb-2">{error}</p>
        <button onClick={fetchRuns} className="text-xs text-ag-accent underline">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-medium text-ag-text">
            Saved Runs ({runs.length})
          </h3>
          {/* Sub-tabs */}
          <div className="flex items-center gap-0.5 bg-ag-dark/50 rounded-md p-0.5">
            {([
              ["all", "All", runs.length],
              ["optimizations", "Optimizations", nBuilds],
              ["recommender", "Recommender", nRecommends],
            ] as [RunFilter, string, number][]).map(([key, label, count]) => (
              <button
                key={key}
                onClick={() => { setFilter(key); setSelectedFiles(new Set()); }}
                className={`px-2.5 py-1 text-[11px] rounded transition-colors ${
                  filter === key
                    ? "bg-ag-accent/20 text-ag-accent font-medium"
                    : "text-ag-text-muted hover:text-ag-text"
                }`}
              >
                {label} ({count})
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3">
          {filteredRuns.length > 0 && (
            <button
              onClick={() => {
                const allSelected = filteredRuns.every((r) => selectedFiles.has(r.filename));
                if (allSelected) {
                  setSelectedFiles(new Set());
                } else {
                  setSelectedFiles(new Set(filteredRuns.map((r) => r.filename)));
                }
              }}
              className="text-xs text-ag-accent hover:underline"
              title={filteredRuns.every((r) => selectedFiles.has(r.filename)) ? "Deselect all visible runs" : "Select all visible runs for bulk actions"}
            >
              {filteredRuns.every((r) => selectedFiles.has(r.filename)) ? "Deselect All" : "Select All"}
            </button>
          )}
          <button
            onClick={handleCleanup}
            disabled={cleanupLoading}
            className="flex items-center gap-1 text-xs text-orange-400 hover:text-orange-300 disabled:opacity-50"
            title="Auto-select small/test runs for deletion (less than 3 batches, 3 candidates, or 50 assets)"
          >
            <Sparkles size={12} />
            {cleanupLoading ? "Scanning..." : "Clean Up"}
          </button>
          <button
            onClick={fetchRuns}
            className="text-xs text-ag-accent hover:underline"
            title="Refresh the list of saved runs"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Cleanup preview */}
      {cleanupPreview && (
        <div className="bg-orange-600/10 border border-orange-500/30 rounded-lg px-4 py-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm text-orange-400 font-medium">
              {cleanupPreview.length} small run{cleanupPreview.length !== 1 ? "s" : ""} found
            </span>
            <div className="flex gap-2">
              <button onClick={() => setCleanupPreview(null)}
                className="text-xs text-ag-text-muted hover:text-ag-text px-2 py-1"
                title="Cancel cleanup">
                Cancel
              </button>
              <button onClick={confirmCleanup}
                disabled={cleanupLoading}
                className="flex items-center gap-1.5 text-xs bg-orange-600/80 hover:bg-orange-600 text-white px-3 py-1 rounded disabled:opacity-50"
                title="Permanently delete all flagged runs">
                <Trash2 size={12} />
                {cleanupLoading ? "Deleting..." : `Delete ${cleanupPreview.length}`}
              </button>
            </div>
          </div>
          <div className="text-[10px] text-ag-text-muted">
            Flagged: less than 3 batches, 3 candidates, or 50 assets
          </div>
          <div className="max-h-40 overflow-y-auto space-y-0.5">
            {cleanupPreview.map((f) => (
              <div key={f.filename} className="flex items-center gap-2 text-[10px] text-ag-text-muted">
                <span className="text-orange-400">{f.type}</span>
                <span>{formatDate(f.saved_at)}</span>
                <span>{f.n_batches}b</span>
                <span>{f.n_candidates}c</span>
                <span>{f.n_assets}a</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Bulk action bar */}
      {selectedFiles.size > 0 && (
        <div className="flex items-center justify-between bg-ag-accent/10 border border-ag-accent/30 rounded-lg px-4 py-2">
          <span className="text-sm text-ag-accent">
            {selectedFiles.size} run{selectedFiles.size !== 1 ? "s" : ""} selected
          </span>
          <div className="flex gap-2">
            <button onClick={() => setSelectedFiles(new Set())}
              className="text-xs text-ag-text-muted hover:text-ag-text px-2 py-1"
              title="Deselect all">
              Cancel
            </button>
            <button onClick={handleBulkDelete}
              disabled={bulkDeleting}
              className="flex items-center gap-1.5 text-xs bg-ag-red/80 hover:bg-ag-red text-white px-3 py-1 rounded disabled:opacity-50"
              title="Permanently delete all selected saved runs">
              <Trash2 size={12} />
              {bulkDeleting ? "Deleting..." : "Delete Selected"}
            </button>
          </div>
        </div>
      )}

      {filteredRuns.length === 0 ? (
        <div className="text-center py-12 bg-ag-card border border-ag-border rounded-lg">
          <p className="text-ag-text-muted text-sm">
            {runs.length === 0 ? "No saved runs yet" : `No ${filter === "optimizations" ? "optimization" : filter === "recommender" ? "recommender" : ""} runs`}
          </p>
          <p className="text-ag-text-muted text-xs mt-1">
            Results are auto-saved when an optimization or recommender analysis completes.
          </p>
        </div>
      ) : (
        <div className="space-y-1">
          {filteredRuns.map((run) => {
            const badge = TYPE_BADGES[run.type] ?? { label: run.type, color: "bg-ag-dark text-ag-text-muted" };
            const isExpanded = expandedFile === run.filename;

            return (
              <div key={run.filename} className="bg-ag-card border border-ag-border rounded-lg overflow-hidden">
                {/* Row */}
                <div
                  className="flex items-center gap-3 px-3 py-2.5 cursor-pointer hover:bg-ag-dark/30 transition-colors"
                  onClick={() => handleExpand(run.filename)}
                >
                  <input
                    type="checkbox"
                    checked={selectedFiles.has(run.filename)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggleSelect(run.filename)}
                    className="w-3.5 h-3.5 rounded border-ag-border accent-ag-accent cursor-pointer"
                    title="Select for bulk delete"
                  />
                  <span className="text-xs text-ag-text-muted">{isExpanded ? "▾" : "▸"}</span>

                  <span className={`px-2 py-0.5 text-[10px] font-medium rounded shrink-0 ${badge.color}`}>
                    {badge.label}
                  </span>

                  {/* Title: descriptive config summary */}
                  <span className="text-xs text-ag-text flex-1 min-w-0 truncate" title={buildRunTitle(run)}>
                    {buildRunTitle(run)}
                  </span>

                  {/* Summary chips — date + stats on the right */}
                  <div className="flex items-center gap-2 text-[10px] text-ag-text-muted shrink-0">
                    {/* Date (always shown on the right) */}
                    <span title="Saved at">{formatDate(run.saved_at)}</span>
                    {run.summary.batch_ids && run.summary.batch_ids.length > 0 && (
                      <span title="Batch IDs used">
                        {run.summary.batch_ids.length} batch{run.summary.batch_ids.length > 1 ? "es" : ""}
                      </span>
                    )}
                    {run.summary.n_candidates != null && (
                      <span title="Number of portfolio candidates">{run.summary.n_candidates} candidates</span>
                    )}
                    {run.summary.n_assets != null && (
                      <span title="Asset pool size">{run.summary.n_assets} assets</span>
                    )}
                    {run.summary.top_pf != null && (
                      <span title="Top candidate profit factor">PF {Number(run.summary.top_pf).toFixed(2)}</span>
                    )}
                    {run.summary.n_top_presets != null && run.summary.n_top_presets > 0 && (
                      <span title="Number of top presets from cross-validation">
                        {run.summary.n_top_presets} presets
                      </span>
                    )}
                    {run.summary.elapsed_seconds != null && (
                      <span title="Time taken to complete">{formatElapsed(Number(run.summary.elapsed_seconds))}</span>
                    )}
                    {run.summary.date_to && (
                      <span title="IS end date">to {run.summary.date_to}</span>
                    )}
                  </div>

                  {/* Delete button */}
                  {confirmDelete === run.filename ? (
                    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => handleDelete(run.filename)}
                        disabled={deleting === run.filename}
                        className="px-2 py-0.5 text-[10px] bg-red-600/20 text-red-400 rounded hover:bg-red-600/30 disabled:opacity-50"
                      >
                        {deleting === run.filename ? "..." : "Confirm"}
                      </button>
                      <button
                        onClick={() => setConfirmDelete(null)}
                        className="px-2 py-0.5 text-[10px] text-ag-text-muted hover:text-ag-text"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={(e) => { e.stopPropagation(); setConfirmDelete(run.filename); }}
                      className="text-[10px] text-ag-text-muted hover:text-red-400 px-1"
                      title="Delete this saved run permanently"
                    >
                      ✕
                    </button>
                  )}
                </div>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="border-t border-ag-border px-3 py-3">
                    {detailLoading ? (
                      <p className="text-xs text-ag-text-muted">Loading details...</p>
                    ) : detail ? (
                      <SavedRunDetailView detail={detail} filename={run.filename} />
                    ) : (
                      <div>
                        <p className="text-xs text-red-400">
                          {detailError || "Failed to load details"}
                        </p>
                        <button
                          onClick={(e) => { e.stopPropagation(); loadDetail(run.filename); }}
                          className="text-xs text-ag-accent underline mt-1"
                          title="Try loading this saved run again"
                        >
                          Retry
                        </button>
                      </div>
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
}

/* ── CV-validated insights derived from actual top presets ── */
interface CVInsight {
  setting: string;
  label: string;
  winner: string;
  winnerDisplay: string;
  count: number;
  total: number;
  presetIndices: number[];
  runners: { value: string; display: string; count: number; indices: number[] }[];
  explanation: string;
}

function deriveCVInsights(topPresets: Record<string, unknown>[]): CVInsight[] {
  const insights: CVInsight[] = [];
  const total = topPresets.length;
  if (total === 0) return insights;

  const analyzeKey = (key: string, label: string) => {
    const counts = new Map<string, number[]>();
    topPresets.forEach((p, i) => {
      const s = (p.settings ?? {}) as Record<string, unknown>;
      let raw = s[key];
      if (Array.isArray(raw)) raw = [...raw].sort().join(", ");
      const v = String(raw ?? "—");
      if (!counts.has(v)) counts.set(v, []);
      counts.get(v)!.push(i);
    });
    const sorted = [...counts.entries()].sort((a, b) => b[1].length - a[1].length);
    if (sorted.length === 0) return;
    const [winnerVal, winnerIdx] = sorted[0];
    const pct = Math.round((winnerIdx.length / total) * 100);
    const toDisplay = (v: string) => friendlySettingValue(key, key === "seed_metrics" ? v.split(", ") : v);
    const runners = sorted.slice(1).map(([v, idx]) => ({
      value: v, display: toDisplay(v), count: idx.length, indices: idx,
    }));
    let explanation: string;
    if (winnerIdx.length === total) explanation = `All ${total} top presets used this — strong consensus from cross-validation.`;
    else if (pct >= 60) explanation = `${winnerIdx.length} of ${total} top presets (${pct}%) used this — clear CV favorite.`;
    else explanation = `${winnerIdx.length} of ${total} (${pct}%) used this — consider runners-up below.`;

    insights.push({
      setting: key, label, winner: winnerVal, winnerDisplay: toDisplay(winnerVal),
      count: winnerIdx.length, total, presetIndices: winnerIdx, runners, explanation,
    });
  };

  analyzeKey("div_algo", "Algorithm");
  analyzeKey("objective", "Objective");
  analyzeKey("guard_preset", "Guard Preset");
  analyzeKey("seed_metrics", "Seed Metrics");
  return insights;
}

/** Single CV insight row — clickable to expand and show supporting presets */
function CVInsightRow({ insight, topPresets }: {
  insight: CVInsight;
  topPresets: Record<string, unknown>[];
}) {
  const [open, setOpen] = useState(false);
  const pct = Math.round((insight.count / insight.total) * 100);
  const barColor = pct === 100 ? "bg-green-500" : pct >= 60 ? "bg-blue-500" : "bg-orange-500";

  return (
    <div className="bg-ag-dark/40 rounded overflow-hidden">
      <button
        className="w-full text-left px-3 py-2 hover:bg-ag-dark/60 transition-colors"
        onClick={() => setOpen(!open)}
        title="Click to see which top presets support this finding"
      >
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="text-ag-text-muted">{open ? "▾" : "▸"}</span>
          <span className="font-medium text-ag-accent">{insight.label}</span>
          <span className="text-ag-text">= {insight.winnerDisplay}</span>
          <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
            pct === 100 ? "bg-green-500/20 text-green-400"
              : pct >= 60 ? "bg-blue-500/20 text-blue-400" : "bg-orange-500/20 text-orange-400"
          }`}>
            {insight.count}/{insight.total} presets ({pct}%)
          </span>
          {insight.runners.length > 0 && (
            <span className="text-[10px] text-ag-text-muted">
              also: {insight.runners.map(r => `${r.display} (${r.count})`).join(", ")}
            </span>
          )}
        </div>
        {/* Confidence bar */}
        <div className="mt-1.5 h-1 bg-ag-border/30 rounded-full overflow-hidden">
          <div className={`h-full ${barColor} rounded-full transition-all`} style={{ width: `${pct}%` }} />
        </div>
        <div className="text-[10px] text-ag-text-muted mt-1">{insight.explanation}</div>
      </button>
      {open && (
        <div className="px-3 pb-2 space-y-1.5">
          <div className="text-[10px] text-ag-text-muted font-medium">
            Top presets using {insight.winnerDisplay}:
          </div>
          <div className="space-y-0.5">
            {insight.presetIndices.map(idx => {
              const p = topPresets[idx];
              return (
                <div key={idx} className="flex items-center gap-3 text-[10px] bg-ag-dark/30 rounded px-2 py-1">
                  <span className="text-ag-text-muted">#{idx + 1}</span>
                  <span className="text-ag-text">
                    Val Stab: {((p.avg_val_stability as number) ?? 0).toFixed(3)}
                  </span>
                  <span className="text-ag-text">
                    Val PF: {((p.avg_val_pf as number) ?? 0).toFixed(2)}
                  </span>
                  <span className={`${
                    ((p.avg_overfit_ratio as number) ?? 0) >= 0.7 ? "text-green-400" : "text-orange-400"
                  }`}>
                    Overfit: {((p.avg_overfit_ratio as number) ?? 0).toFixed(2)}
                  </span>
                  <span className="text-ag-accent font-medium">
                    Score: {((p.composite_score as number) ?? 0).toFixed(3)}
                  </span>
                </div>
              );
            })}
          </div>
          {insight.runners.length > 0 && (
            <>
              <div className="text-[10px] text-ag-text-muted font-medium mt-2">Runners-up:</div>
              {insight.runners.map(r => (
                <div key={r.value} className="flex items-center gap-2 text-[10px] px-2 py-0.5">
                  <span className="text-ag-text">{r.display}</span>
                  <span className="text-ag-text-muted">— presets {r.indices.map(i => `#${i + 1}`).join(", ")}</span>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Pool-based heuristic recommendations — shown directly for quick mode, collapsed for thorough */
function RecommendationsList({ recommendations, isThorough }: {
  recommendations: Record<string, unknown>[];
  isThorough: boolean;
}) {
  const [open, setOpen] = useState(!isThorough);
  return (
    <div className="space-y-1">
      <button
        className="flex items-center gap-1.5 text-[10px] font-medium text-ag-text-muted uppercase tracking-wider hover:text-ag-text transition-colors"
        onClick={() => setOpen(!open)}
        title={isThorough
          ? "These are quick heuristic guesses based on pool statistics — NOT from the CV run"
          : "Recommendations based on pool statistics analysis"
        }
      >
        <span>{open ? "▾" : "▸"}</span>
        {isThorough ? "Pool Heuristics" : "Recommendations"} ({recommendations.length})
        {isThorough && <span className="normal-case italic font-normal ml-1">(not from CV — pool stats only)</span>}
      </button>
      {open && (
        <div className="space-y-1">
          {recommendations.map((rec, i) => {
            const setting = String(rec.setting);
            const label = SETTING_LABELS[setting] || setting;
            const value = friendlySettingValue(setting, rec.value);
            const conf = (rec.confidence as number) * 100;
            return (
              <div key={i} className="bg-ag-dark/40 rounded px-2 py-1.5">
                <div className="flex items-center gap-2 flex-wrap text-xs">
                  <span className="font-medium text-ag-accent">{label}</span>
                  <span className="text-ag-text">= {value}</span>
                  <span className={`text-[10px] ${conf >= 80 ? "text-green-400" : conf >= 60 ? "text-orange-400" : "text-ag-text-muted"}`}>
                    ({conf.toFixed(0)}%)
                  </span>
                </div>
                <div className="text-[11px] text-ag-text-muted mt-0.5 leading-relaxed">
                  {String(rec.reason)}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Render the expanded detail of a saved run */
function SavedRunDetailView({ detail, filename }: { detail: SavedRunDetailType; filename: string }) {
  const result = detail.result as Record<string, Record<string, unknown>[] | Record<string, unknown> | unknown>;
  const params = detail.params as Record<string, string | number | string[] | undefined>;
  const isBuild = detail.type === "build";
  const isRecommend = detail.type.startsWith("recommend");
  const isThorough = detail.type === "recommend_thorough";
  const recommendations = isRecommend && Array.isArray(result.recommendations)
    ? (result.recommendations as Record<string, unknown>[]) : null;
  const topPresets = isRecommend && Array.isArray(result.top_presets)
    ? (result.top_presets as Record<string, unknown>[]) : null;
  const cvResults = isRecommend && Array.isArray(result.cv_results)
    ? (result.cv_results as Record<string, unknown>[]) : null;
  const profile = isRecommend && result.profile && typeof result.profile === "object"
    ? (result.profile as Record<string, unknown>) : null;
  const timing = result.timing && typeof result.timing === "object"
    ? (result.timing as Record<string, number>) : null;
  const elapsedSec = typeof result.elapsed_seconds === "number" ? result.elapsed_seconds : null;

  const [expandedPreset, setExpandedPreset] = useState<number | null>(null);
  const [saveConfigToast, setSaveConfigToast] = useState("");

  return (
    <div className="space-y-3">
      {/* Elapsed time + summary banner (recommender) */}
      {isRecommend && (
        <div className="flex items-center gap-3 text-xs text-ag-text-muted flex-wrap">
          {elapsedSec != null && (
            <span className="font-medium text-ag-text">
              Completed in {formatElapsed(elapsedSec)}
            </span>
          )}
          {profile && (
            <>
              <span>·</span>
              <span>{String(profile.n_assets)} assets from {String(profile.n_batches)} batch{Number(profile.n_batches) !== 1 ? "es" : ""}</span>
            </>
          )}
          {cvResults && cvResults.length > 0 && (
            <>
              <span>·</span>
              <span>{cvResults.length} combos tested</span>
            </>
          )}
          {isThorough && <span className="text-purple-400 font-medium">Thorough Mode</span>}
        </div>
      )}

      {/* Params overview */}
      <div className="space-y-1">
        <div className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider">
          Input Parameters
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
          {Boolean(params.batch_ids) && (
            <ParamChip label="Batch IDs" value={String(params.batch_ids)} />
          )}
          {Boolean(params.date_from) && (
            <ParamChip label="IS From" value={String(params.date_from)} />
          )}
          {Boolean(params.date_to) && (
            <ParamChip label="IS To" value={String(params.date_to)} />
          )}
          {Boolean(params.corr_method) && (
            <ParamChip label="Correlation" value={String(params.corr_method)} />
          )}
          {Boolean(params.div_algo) && (
            <ParamChip label="Algorithm" value={friendlySettingValue("div_algo", params.div_algo)} />
          )}
          {Boolean(params.combined_objective) && (
            <ParamChip label="Objective" value={friendlySettingValue("objective", params.combined_objective)} />
          )}
          {params.n_assets != null && (
            <ParamChip label="N Assets" value={String(params.n_assets)} />
          )}
          {params.n_longs != null && (
            <ParamChip label="N Longs" value={String(params.n_longs)} />
          )}
          {params.n_shorts != null && (
            <ParamChip label="N Shorts" value={String(params.n_shorts)} />
          )}
          {Boolean(params.mode) && (
            <ParamChip label="Mode" value={String(params.mode)} />
          )}
          {Boolean(params.exclude_portfolio_ids) && (
            <ParamChip
              label="Exclude Portfolio"
              value={`${params.exclude_portfolio_mode === "partial"
                ? `Partial ${params.exclude_portfolio_keep_pct}% (${params.exclude_portfolio_method})`
                : "Full"} · IDs: ${String(params.exclude_portfolio_ids)}`}
            />
          )}
          {Boolean(params.bybit_size_gate) && (
            <ParamChip
              label="Bybit Size Gate"
              value={Number(params.bybit_size_gate_max_reject_pct) > 0
                ? `Rate ≤${params.bybit_size_gate_max_reject_pct}%`
                : "Binary"}
            />
          )}
        </div>
      </div>

      {/* Build results — candidates summary with OOS reveal */}
      {isBuild && Array.isArray(result.candidates) && (
        <SavedBuildCandidates
          candidates={result.candidates as Record<string, unknown>[]}
          params={params}
        />
      )}

      {/* ── Thorough mode: Top Presets FIRST (the real CV results) ── */}
      {topPresets && topPresets.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider">
            Cross-Validation Results — Top {topPresets.length} Presets
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-ag-text-muted border-b border-ag-border">
                  <th className="text-left py-1 pr-2" title="Rank by composite score">#</th>
                  <th className="text-left py-1 pr-2" title="Diversification algorithm">Algorithm</th>
                  <th className="text-left py-1 pr-2" title="Scoring function">Objective</th>
                  <th className="text-left py-1 pr-2" title="Overfitting guard preset">Guard</th>
                  <th className="text-left py-1 pr-2" title="Seed ranking metrics">Seeds</th>
                  <th className="text-right py-1 pr-2" title="Equity curve smoothness on unseen data (0-1)">Val Stability</th>
                  <th className="text-right py-1 pr-2" title="Profit factor on unseen data">Val PF</th>
                  <th className="text-right py-1 pr-2" title="Validation / training performance ratio">Overfit Ratio</th>
                  <th className="text-right py-1 pr-2" title="Blended score (higher = better)">Composite</th>
                </tr>
              </thead>
              <tbody>
                {topPresets.map((p, i) => {
                  const s = (p.settings ?? {}) as Record<string, unknown>;
                  const fs = (p.full_settings ?? p.settings ?? {}) as Record<string, unknown>;
                  const algoKey = String(s.div_algo ?? "");
                  const isOpen = expandedPreset === i;
                  return (
                    <React.Fragment key={i}>
                      <tr
                        className={`border-b border-ag-border/30 hover:bg-ag-dark/30 cursor-pointer ${isOpen ? "bg-ag-dark/30" : ""}`}
                        onClick={() => setExpandedPreset(isOpen ? null : i)}
                        title="Click to see all settings for this preset"
                      >
                        <td className="py-1.5 pr-2 text-ag-text-muted">{i + 1}</td>
                        <td className="py-1.5 pr-2 text-ag-text" title={ALGO_INFO[algoKey]?.tip}>
                          {ALGO_INFO[algoKey]?.display || algoKey || "—"}
                        </td>
                        <td className="py-1.5 pr-2 text-ag-text">
                          {friendlySettingValue("objective", s.objective)}
                        </td>
                        <td className="py-1.5 pr-2 text-ag-text">{String(s.guard_preset ?? "off")}</td>
                        <td className="py-1.5 pr-2 text-ag-text text-[10px]">
                          {Array.isArray(s.seed_metrics) ? (s.seed_metrics as string[]).join(", ") : "—"}
                        </td>
                        <td className="py-1.5 pr-2 text-right text-ag-text">
                          {(p.avg_val_stability as number)?.toFixed(3) ?? "—"}
                        </td>
                        <td className="py-1.5 pr-2 text-right text-ag-text">
                          {(p.avg_val_pf as number)?.toFixed(2) ?? "—"}
                        </td>
                        <td className={`py-1.5 pr-2 text-right ${
                          (p.avg_overfit_ratio as number) >= 0.7 ? "text-green-400" :
                          (p.avg_overfit_ratio as number) >= 0.5 ? "text-orange-400" : "text-red-400"
                        }`}>
                          {(p.avg_overfit_ratio as number)?.toFixed(2) ?? "—"}
                        </td>
                        <td className="py-1.5 pr-2 text-right font-medium text-ag-accent">
                          {(p.composite_score as number)?.toFixed(3) ?? "—"}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="border-b border-ag-border/30">
                          <td colSpan={9} className="py-2 px-3">
                            <div className="bg-ag-dark/50 rounded-lg p-3 border border-ag-border/30">
                              <div className="flex items-center justify-between mb-2">
                                <div className="text-[11px] font-medium text-ag-text-muted">
                                  All Settings for Preset #{i + 1}
                                </div>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    const algoLabel = ALGO_INFO[algoKey]?.display || algoKey;
                                    const defaultName = `Recommender #${i + 1} — ${algoLabel}`;
                                    const name = prompt("Save as optimizer config:", defaultName);
                                    if (!name) return;
                                    const ok = savePresetAsOptimizerConfig(
                                      fs,
                                      name,
                                      `From Settings Recommender preset #${i + 1} (${algoLabel}, composite ${(p.composite_score as number)?.toFixed(3) ?? "?"})`,
                                    );
                                    if (ok) {
                                      setSaveConfigToast(`Saved "${name}" — load it from the Config dropdown in the optimizer`);
                                      setTimeout(() => setSaveConfigToast(""), 4000);
                                    }
                                  }}
                                  className="flex items-center gap-1 px-2 py-1 text-[10px] rounded bg-ag-green/15 text-ag-green hover:bg-ag-green/25 transition-colors"
                                  title="Save these settings as a reusable optimizer config — load it from the Config dropdown when generating portfolios"
                                >
                                  <Download size={11} /> Save as Optimizer Config
                                </button>
                              </div>
                              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-4 gap-y-1">
                                {Object.entries(fs).map(([key, val]) => (
                                  <div key={key} className="flex items-baseline gap-1.5 text-[11px]">
                                    <span className="text-ag-text-muted shrink-0">
                                      {SETTING_LABELS[key] || key}:
                                    </span>
                                    <span className="text-ag-text font-mono truncate" title={formatSettingValue(val)}>
                                      {key === "div_algo" || key === "objective"
                                        ? friendlySettingValue(key, val)
                                        : formatSettingValue(val)}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {cvResults && cvResults.length > (topPresets?.length ?? 5) && (
            <div className="text-[10px] text-ag-text-muted">
              Showing top {topPresets.length} of {cvResults.length} tested combinations
            </div>
          )}
          {saveConfigToast && (
            <div className="text-xs text-ag-green bg-ag-green/10 rounded px-3 py-1.5 mt-1">
              {saveConfigToast}
            </div>
          )}
        </div>
      )}

      {/* ── Thorough mode: CV-Validated Insights (derived from actual top presets) ── */}
      {isThorough && topPresets && topPresets.length > 0 && (() => {
        const cvInsights = deriveCVInsights(topPresets);
        return cvInsights.length > 0 ? (
          <div className="space-y-1.5">
            <div className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider">
              CV-Validated Insights
            </div>
            <div className="text-[10px] text-ag-text-muted italic">
              Derived from the actual top {topPresets.length} presets above — click any row to see supporting presets.
            </div>
            <div className="space-y-1">
              {cvInsights.map(ins => (
                <CVInsightRow key={ins.setting} insight={ins} topPresets={topPresets} />
              ))}
            </div>
          </div>
        ) : null;
      })()}

      {/* ── Recommendations: shown directly for quick mode, collapsed for thorough ── */}
      {recommendations && recommendations.length > 0 && (
        <RecommendationsList
          recommendations={recommendations}
          isThorough={isThorough}
        />
      )}

      {/* ── Pool Profile ── */}
      {profile && (
        <div className="space-y-1">
          <div className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider">
            Pool Profile
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2 text-xs">
            {Object.entries(profile).map(([k, v]) => {
              if (typeof v === "object") return null;
              const label = PROFILE_LABELS[k] || k.replace(/_/g, " ");
              const isWarn = (k === "n_suspicious" && Number(v) > 0)
                || (k === "pct_below_10_trades" && Number(v) > 30)
                || (k === "corr_density_above_50" && Number(v) > 15);
              return (
                <div key={k} className="bg-ag-dark/40 rounded px-2 py-1.5">
                  <div className="text-[10px] text-ag-text-muted">{label}</div>
                  <div className={`text-xs font-medium ${isWarn ? "text-orange-400" : "text-ag-text"}`}>
                    {typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(2)) : String(v)}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Suspicious assets */}
      {profile && Array.isArray(profile.suspicious_tags) && (profile.suspicious_tags as string[]).length > 0 && (
        <div className="space-y-1">
          <div className="text-xs font-medium text-orange-400">
            Suspicious Assets ({String(profile.n_suspicious)})
          </div>
          <div className="text-[11px] text-ag-text-muted">
            2σ+ outliers on 3+ metrics within their batch — likely overfitted:
          </div>
          <div className="flex flex-wrap gap-1">
            {(profile.suspicious_tags as string[]).map((tag) => (
              <span key={tag} className="px-1.5 py-0.5 text-[10px] bg-orange-900/30 text-orange-400 rounded">
                {tag}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Timing (for builds) */}
      {timing && (
        <div className="space-y-1">
          <div className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider">
            Timing {timing.total != null && (
              <span className="text-ag-text font-normal ml-2">
                Total: {formatElapsed(timing.total)}
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-2 text-[10px] text-ag-text-muted">
            {Object.entries(timing).map(([k, v]) => (
              <span key={k} className="bg-ag-dark/40 rounded px-1.5 py-0.5">
                {k}: {typeof v === "number" ? formatElapsed(v) : String(v)}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Compute a backend-matching fingerprint from raw asset tags */
function candidateFingerprint(rawAssets: string[]): string {
  const tags = rawAssets.map(s => {
    const bm = s.match(/~B(-?\d+)/);
    const batchId = bm ? bm[1] : "";
    const clean = s.replace(/~B-?\d+/g, "");
    const side = clean.endsWith("_Long") ? "Long" : clean.endsWith("_Short") ? "Short" : "both";
    const symbol = clean.replace(/_Long$/, "").replace(/_Short$/, "");
    return `${symbol}_${side}_B${batchId}`;
  });
  return tags.sort().join("|");
}

/** Build candidates with OOS reveal and Save to Portfolio per candidate */
function SavedBuildCandidates({ candidates, params }: {
  candidates: Record<string, unknown>[];
  params: Record<string, string | number | string[] | undefined>;
}) {
  const [oosIdx, setOosIdx] = useState<number | null>(null);
  const [oosLoading, setOosLoading] = useState(false);
  const [oosResult, setOosResult] = useState<OOSCompareResult | null>(null);
  const [oosError, setOosError] = useState("");
  const [savingIdx, setSavingIdx] = useState<number | null>(null);
  const [savedIndices, setSavedIndices] = useState<Set<number>>(new Set());

  // Check existing portfolios for duplicate detection
  useEffect(() => {
    api.portfolios().then((list) => {
      const fps = new Set<string>();
      for (const p of list) {
        const fp = (p.source_strategies as Record<string, unknown>)?.asset_fingerprint;
        if (typeof fp === "string") fps.add(fp);
      }
      if (fps.size === 0) return;
      const alreadySaved = new Set<number>();
      candidates.forEach((c, i) => {
        const rawAssets = (c.assets ?? []) as string[];
        if (fps.has(candidateFingerprint(rawAssets))) alreadySaved.add(i);
      });
      if (alreadySaved.size > 0) setSavedIndices(alreadySaved);
    }).catch(() => { });
  }, [candidates]);

  const handleRevealOOS = async (idx: number) => {
    if (oosIdx === idx) {
      setOosIdx(null);
      setOosResult(null);
      setOosError("");
      return;
    }
    setOosIdx(idx);
    setOosResult(null);
    setOosError("");
    setOosLoading(true);
    try {
      const c = candidates[idx];
      const assets = (c.assets ?? []) as string[];
      const batchIds = ((params.batch_ids ?? []) as unknown) as number[];
      const oosDate = params.date_to ? String(params.date_to) : undefined;
      if (!oosDate) {
        setOosError("No IS end date available — cannot compute OOS.");
        return;
      }
      // Forward risk profile so OOS preview uses the same scaling as the build
      const rpMode = params.risk_profile_mode ? String(params.risk_profile_mode) : "";
      const rpValue = Number(params.risk_profile_value || 0);
      const riskProfile = (rpMode && rpMode !== "fixed") || rpValue > 0 ? {
        mode: rpMode,
        value: rpValue,
        initialCapital: Number(params.risk_profile_initial_capital || 10000),
        multiplier: Number(params.risk_profile_multiplier || 2),
        maxStreak: Number(params.risk_profile_max_streak || 3),
        compound: String(params.risk_profile_martingale_compound || "off"),
        scope: String(params.risk_profile_martingale_scope || "asset"),
        countMode: String(params.risk_profile_martingale_count_mode || "full_position"),
        oosReset: String(params.risk_profile_martingale_oos_reset) !== "false",
        kellyN: Number(params.risk_profile_kelly_n || 2),
        kellyWarmup: Number(params.risk_profile_kelly_warmup || 20),
        kellyScope: String(params.risk_profile_kelly_scope || "portfolio"),
        kellyLookback: Number(params.risk_profile_kelly_lookback || 0),
        kellyCap: Number(params.risk_profile_kelly_cap || 0),
        applyGroupMultiplier: !!params.risk_profile_apply_group_multiplier,
      } : undefined;
      // Forward per-batch sizing config if present
      const smStr = params.risk_profile_sizing_master ? String(params.risk_profile_sizing_master) : "portfolio";
      const perBatchConfig = smStr === "per_batch" && params.risk_profile_batch_configs
        ? { sizingMaster: "per_batch", batchConfigs: String(params.risk_profile_batch_configs) }
        : undefined;
      const res = await api.dashboardOOSCompare(
        oosDate,           // oos_date
        undefined,         // batch_id
        undefined,         // run_id
        undefined,         // strategy_filename
        assets,            // assets
        undefined,         // side_filter
        batchIds,          // batch_ids
        undefined,         // min_trades
        undefined,         // clip_is
        true,              // clip_oos
        undefined,         // oos_end_date
        undefined,         // extraCosts
        riskProfile,       // risk profile (martingale, kelly, etc.)
        perBatchConfig,    // per-batch position sizing
      );
      setOosResult(res);
    } catch (e: unknown) {
      setOosError(e instanceof Error ? e.message : "OOS comparison failed");
    } finally {
      setOosLoading(false);
    }
  };

  const handleSaveToPortfolio = async (idx: number) => {
    setSavingIdx(idx);
    try {
      const c = candidates[idx];
      const rawAssets = (c.assets ?? []) as string[];
      const attribution = (c.attribution ?? {}) as Record<string, string>;
      const batchIds = ((params.batch_ids ?? []) as unknown) as number[];
      const metrics = (c.metrics ?? {}) as Record<string, number | null>;

      // Parse asset tags: "DOGEUSDT~B1_Long" → symbol, side, batch_id, strategy
      const extractBatchId = (s: string): number | undefined => {
        const m = s.match(/~B(-?\d+)/);
        return m ? parseInt(m[1], 10) : undefined;
      };
      const stripTag = (s: string) => s.replace(/~B-?\d+/g, "");
      const assets = rawAssets.map((s) => {
        const batchId = extractBatchId(s);
        const clean = stripTag(s);
        const side = clean.endsWith("_Long") ? "Long" : clean.endsWith("_Short") ? "Short" : "both";
        const symbol = clean.replace(/_Long$/, "").replace(/_Short$/, "");
        return { symbol, side, strategy_name: attribution[s] || "", batch_id: batchId };
      });

      await api.savePortfolio(
        `Saved Run #${idx + 1} — ${new Date().toLocaleDateString()}`,
        assets,
        undefined,
        params as Record<string, unknown>,
        metrics,
        {
          batch_ids: batchIds,
          portfolio_source: "saved_run",
          optimization_end_date: params.date_to ? String(params.date_to) : undefined,
          is_start_date: params.date_from ? String(params.date_from) : undefined,
        },
      );
      setSavedIndices((prev) => new Set(prev).add(idx));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Save failed";
      alert(`Save error: ${msg}`);
    } finally {
      setSavingIdx(null);
    }
  };

  return (
    <div className="space-y-1">
      <div className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider">
        Candidates ({candidates.length})
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-ag-text-muted border-b border-ag-border">
              <th className="text-left py-1 pr-2">#</th>
              <th className="text-left py-1 pr-2">Assets</th>
              <th className="text-right py-1 pr-2" title="In-sample net profit">Net Profit</th>
              <th className="text-right py-1 pr-2" title="In-sample profit factor">PF</th>
              <th className="text-right py-1 pr-2" title="In-sample max drawdown">Max DD</th>
              <th className="text-right py-1 pr-2" title="In-sample equity curve stability (0-1)">Stability</th>
              <th className="text-right py-1 pr-2" title="In-sample win rate percentage">Win Rate</th>
              <th className="text-right py-1 pr-2" title="Total trades in-sample">Trades</th>
              <th className="text-center py-1 pr-2" title="Reveal out-of-sample performance">OOS</th>
              <th className="text-center py-1 pr-2" title="Save this candidate as a portfolio">Save</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c: Record<string, unknown>, i: number) => {
              const m = (c.metrics ?? {}) as Record<string, number>;
              const rawAssets = (c.assets ?? []) as string[];
              const isOosOpen = oosIdx === i;
              const isSaved = savedIndices.has(i);
              return (
                <React.Fragment key={i}>
                  <tr className={`border-b border-ag-border/30 hover:bg-ag-dark/30 ${isOosOpen ? "bg-ag-dark/20" : ""}`}>
                    <td className="py-1.5 pr-2 text-ag-text-muted">{i + 1}</td>
                    <td className="py-1.5 pr-2 text-ag-text max-w-[200px] truncate" title={rawAssets.join(", ")}>
                      {rawAssets.length} assets
                    </td>
                    <td className={`py-1.5 pr-2 text-right ${(m.net_profit ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                      ${(m.net_profit ?? 0).toFixed(0)}
                    </td>
                    <td className="py-1.5 pr-2 text-right text-ag-text">{(m.profit_factor ?? 0).toFixed(2)}</td>
                    <td className="py-1.5 pr-2 text-right text-red-400">{(m.max_drawdown ?? 0).toFixed(0)}</td>
                    <td className="py-1.5 pr-2 text-right text-ag-text">{(m.stability ?? 0).toFixed(3)}</td>
                    <td className="py-1.5 pr-2 text-right text-ag-text">{(m.win_rate ?? 0).toFixed(1)}%</td>
                    <td className="py-1.5 pr-2 text-right text-ag-text">{m.total_trades ?? "—"}</td>
                    <td className="py-1.5 pr-2 text-center">
                      <button
                        onClick={() => handleRevealOOS(i)}
                        className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                          isOosOpen
                            ? "bg-ag-accent text-white"
                            : "bg-ag-accent/10 text-ag-accent hover:bg-ag-accent/20"
                        }`}
                        title="Compare in-sample vs out-of-sample performance for this portfolio"
                      >
                        {isOosOpen ? "Hide" : "Reveal"}
                      </button>
                    </td>
                    <td className="py-1.5 pr-2 text-center">
                      {isSaved ? (
                        <span className="text-[10px] text-green-400" title="Saved to Portfolios tab">
                          Saved
                        </span>
                      ) : (
                        <button
                          onClick={() => handleSaveToPortfolio(i)}
                          disabled={savingIdx === i}
                          className="flex items-center gap-1 px-2 py-0.5 text-[10px] rounded bg-green-600/10 text-green-400 hover:bg-green-600/20 transition-colors disabled:opacity-50"
                          title="Save this candidate as a portfolio (appears in Portfolios tab)"
                        >
                          <Save size={10} />
                          {savingIdx === i ? "..." : "Save"}
                        </button>
                      )}
                    </td>
                  </tr>
                  {isOosOpen && (
                    <tr>
                      <td colSpan={10} className="py-2 px-2">
                        {oosLoading ? (
                          <p className="text-xs text-ag-text-muted">Loading OOS comparison...</p>
                        ) : oosError ? (
                          <p className="text-xs text-red-400">{oosError}</p>
                        ) : oosResult ? (
                          <OOSComparisonPanel result={oosResult} />
                        ) : null}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Inline OOS comparison panel */
function OOSComparisonPanel({ result }: { result: OOSCompareResult }) {
  const ism = result.is_metrics;
  const osm = result.oos_metrics;
  const rows: { label: string; isKey: string; oosKey: string; fmt: (v: unknown) => string; greenPositive?: boolean }[] = [
    { label: "Net Profit", isKey: "Total Net Profit", oosKey: "Total Net Profit", fmt: (v) => `$${Number(v ?? 0).toFixed(0)}`, greenPositive: true },
    { label: "Profit Factor", isKey: "Profit Factor", oosKey: "Profit Factor", fmt: (v) => Number(v ?? 0).toFixed(2) },
    { label: "Win Rate", isKey: "Win Rate", oosKey: "Win Rate", fmt: (v) => `${Number(v ?? 0).toFixed(1)}%` },
    { label: "Stability", isKey: "Stability (R\u00B2)", oosKey: "Stability (R\u00B2)", fmt: (v) => Number(v ?? 0).toFixed(3) },
    { label: "Max DD", isKey: "Max Drawdown ($)", oosKey: "Max Drawdown ($)", fmt: (v) => `$${Number(v ?? 0).toFixed(0)}` },
  ];
  const wfePf = result.aggregate_wfe_pf ?? result.aggregate_wfe;
  const wfeRate = result.aggregate_wfe;

  return (
    <div className="bg-ag-dark/40 border border-ag-border/50 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-3">
        <span className="text-[10px] font-medium text-ag-text-muted uppercase tracking-wider">
          IS vs OOS Comparison
        </span>
        <span className="text-[10px] text-ag-text-muted">
          Cutoff: {result.cutoff}
        </span>
        <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
          wfePf >= 0.7 ? "bg-green-500/20 text-green-400"
            : wfePf >= 0.4 ? "bg-orange-500/20 text-orange-400"
            : "bg-red-500/20 text-red-400"
        }`} title="PF-based WFE: (OOS_PF-1)/(IS_PF-1) — edge retention. Above 0.5 is good.">
          WFE: {(wfePf * 100).toFixed(1)}%
        </span>
        <span className="text-[10px] text-ag-text-muted" title="Time-normalized WFE (daily rate ratio)">
          Rate: {(wfeRate * 100).toFixed(0)}%
        </span>
      </div>
      <div className="grid grid-cols-5 gap-2">
        {rows.map((r) => {
          const isVal = Number(ism[r.isKey] ?? 0);
          const oosVal = Number(osm[r.oosKey] ?? 0);
          return (
            <div key={r.label} className="text-center">
              <div className="text-[10px] text-ag-text-muted">{r.label}</div>
              <div className="text-[10px]">
                <span className="text-ag-text-muted" title="In-sample">{r.fmt(isVal)}</span>
                <span className="mx-1 text-ag-text-muted">&rarr;</span>
                <span className={`font-medium ${
                  r.greenPositive
                    ? (oosVal > 0 ? "text-green-400" : "text-red-400")
                    : ""
                }`} title="Out-of-sample">{r.fmt(oosVal)}</span>
              </div>
            </div>
          );
        })}
      </div>
      {/* Per-asset breakdown */}
      {result.per_asset && result.per_asset.length > 0 && (
        <div className="mt-2">
          <div className="text-[10px] text-ag-text-muted mb-1">Per-Asset WFE</div>
          <div className="flex flex-wrap gap-1">
            {result.per_asset.map((a) => (
              <span key={a.asset} className={`text-[10px] px-1.5 py-0.5 rounded ${
                (a.WFE_PF ?? a.WFE ?? 0) >= 0.7 ? "bg-green-500/15 text-green-400"
                  : (a.WFE_PF ?? a.WFE ?? 0) >= 0.4 ? "bg-orange-500/15 text-orange-400"
                  : "bg-red-500/15 text-red-400"
              }`} title={`${a.asset}: IS PF ${(a.IS_PF ?? 0).toFixed(2)} → OOS PF ${(a.OOS_PF ?? 0).toFixed(2)}`}>
                {(a.asset ?? "").replace("USDT", "")} {((a.WFE_PF ?? a.WFE ?? 0) * 100).toFixed(0)}%
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ParamChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-ag-dark/40 rounded px-2 py-1">
      <div className="text-[10px] text-ag-text-muted">{label}</div>
      <div className="text-xs text-ag-text truncate" title={value}>{value}</div>
    </div>
  );
}
