"use client";
export const dynamic = "force-dynamic";
import React, { useState, useEffect, Suspense, lazy } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { BatchBacktestRunItem, BacktestRunItem, DashboardAnalysisRequest, TVStrategyListItem, PortfolioItem } from "@/lib/api";

// Lazy-load heavy analysis panels — only loads when tab is first activated
const MonteCarloPanel = lazy(() => import("@/components/analysis/MonteCarloPanel"));
const MonteCarloTabContainer = lazy(() => import("@/components/analysis/MonteCarloTabContainer"));
const ComparisonPanel = lazy(() => import("@/components/analysis/ComparisonPanel"));
const BatchDashboardPanel = lazy(() => import("@/components/analysis/BatchDashboardPanel"));
const PFHistogram = lazy(() => import("@/components/analysis/PFHistogram"));
const RobustnessPanel = lazy(() => import("@/components/tv-analysis/RobustnessPanel"));
const MetricsPanel = lazy(() => import("@/components/tv-analysis/MetricsPanel"));
const EquityCurveChart = lazy(() => import("@/components/tv-analysis/EquityCurveChart"));
const MonthlyMatrix = lazy(() => import("@/components/tv-analysis/MonthlyMatrix"));
const FullAnalysisPanel = lazy(() => import("@/components/tv-analysis/FullAnalysisPanel"));
const AssetBreakdown = lazy(() => import("@/components/tv-analysis/AssetBreakdown"));
const OOSResultsPanel = lazy(() => import("@/components/analysis/OOSResultsPanel"));
const RunTradesPanel = lazy(() => import("@/components/analysis/RunTradesPanel"));
const CategoryBreakdownPanel = lazy(() => import("@/components/analysis/CategoryBreakdownPanel"));
const PortfolioInDepthPanel = lazy(() => import("@/components/analysis/PortfolioInDepthPanel"));
const StructuralQualityPanel = lazy(() => import("@/components/analysis/StructuralQualityPanel"));
const AssetStructuralPanel = lazy(() => import("@/components/analysis/AssetStructuralPanel"));
const FilterSummaryStrip = lazy(() => import("@/components/analysis/FilterSummaryStrip"));
const BatchCatalog = lazy(() => import("@/components/analysis/BatchCatalog"));
const PortfolioCatalog = lazy(() => import("@/components/analysis/PortfolioCatalog"));
const SessionIntelPanel = lazy(() => import("@/components/analysis/SessionIntelPanel"));
const BatchGroupedSelector = lazy(() => import("@/components/common/BatchGroupedSelector"));

const BATCH_TABS_BASE = [
  { id: "dashboard", label: "Dashboard" },
  { id: "structure", label: "Structure" },
  { id: "robustness", label: "Robustness" },
  { id: "montecarlo", label: "Monte Carlo" },
  { id: "compare", label: "Comparison" },
  { id: "categories", label: "Categories" },
  { id: "session_intel", label: "Session Intel" },
] as const;

const OOS_TAB = { id: "oos" as const, label: "OOS Results" };

const RUN_TABS = [
  { id: "full", label: "Full Analysis" },
  { id: "overview", label: "Overview" },
  { id: "equity", label: "Equity Curve" },
  { id: "monthly", label: "Monthly" },
  { id: "trades", label: "Trades" },
  { id: "montecarlo", label: "Monte Carlo" },
] as const;

const TV_TABS = [
  { id: "overview", label: "Overview" },
  { id: "monthly", label: "Monthly" },
  { id: "assets", label: "Assets" },
  { id: "robustness", label: "Robustness" },
] as const;

const PORTFOLIO_TABS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "robustness", label: "Robustness" },
  { id: "montecarlo", label: "Monte Carlo" },
  { id: "compare", label: "Comparison" },
  { id: "categories", label: "Categories" },
  { id: "indepth", label: "In Depth" },
] as const;

type TabId = "dashboard" | "structure" | "robustness" | "montecarlo" | "compare" | "categories" | "session_intel" | "oos" | "overview" | "equity" | "monthly" | "full" | "assets" | "trades" | "indepth" | "assets_tab";

function AnalysisContent() {
  const searchParams = useSearchParams();
  const preselectedBatch = searchParams.get("batch");
  const preselectedRun = searchParams.get("run");

  const [sourceType, setSourceType] = useState<"batch" | "run" | "tv" | "portfolio" | "assets_tab">(
    preselectedRun ? "run" : "batch",
  );
  const [batches, setBatches] = useState<BatchBacktestRunItem[]>([]);
  const [runs, setRuns] = useState<BacktestRunItem[]>([]);
  const [selectedBatch, setSelectedBatch] = useState<number | null>(null);
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [tab, setTab] = useState<TabId>(preselectedRun ? "full" : "dashboard");
  const [loading, setLoading] = useState(true);

  // OOS (Out-of-Sample) date cutoff — hides data after this date from analysis/optimizer
  const [oosDate, setOosDate] = useState("");
  // Global filters — applied to all analysis tabs
  const [minTrades, setMinTrades] = useState(0);
  const [excludeTop, setExcludeTop] = useState(0);
  const [pfMin, setPfMin] = useState<string>("");
  const [pfMax, setPfMax] = useState<string>("");
  const [wrMin, setWrMin] = useState<string>("");

  // Portfolio state
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [selectedPortfolio, setSelectedPortfolio] = useState<number | null>(null);
  const [portfoliosLoading, setPortfoliosLoading] = useState(false);

  // Catalog view toggles
  const [showBatchCatalog, setShowBatchCatalog] = useState(false);
  const [showPortfolioCatalog, setShowPortfolioCatalog] = useState(false);

  // TV Analysis state
  const [tvStrategies, setTvStrategies] = useState<TVStrategyListItem[]>([]);
  const [tvSelected, setTvSelected] = useState("");
  const [tvSideFilter, setTvSideFilter] = useState("All");
  const [tvFolderPath, setTvFolderPath] = useState("");
  const [tvImporting, setTvImporting] = useState(false);

  useEffect(() => {
    Promise.all([api.batchRuns(), api.backtestRuns()])
      .then(([batchData, runData]) => {
        const completedBatches = batchData.filter((b) => b.status === "completed");
        const completedRuns = runData.filter((r) => r.status === "completed" && !r.batch_id);
        setBatches(completedBatches);
        setRuns(completedRuns);

        if (preselectedRun) {
          const target = completedRuns.find((r) => r.id === Number(preselectedRun));
          if (target) setSelectedRun(target.id);
        } else if (preselectedBatch) {
          const target = completedBatches.find((b) => b.id === Number(preselectedBatch));
          if (target) setSelectedBatch(target.id);
        } else {
          if (completedBatches[0]) setSelectedBatch(completedBatches[0].id);
          if (completedRuns[0]) setSelectedRun(completedRuns[0].id);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [preselectedBatch, preselectedRun]);

  const loadTvStrategies = () => {
    api.tvStrategies().then((r) => setTvStrategies(r.strategies)).catch(() => {});
  };

  // Load TV strategies when switching to TV mode
  useEffect(() => {
    if (sourceType === "tv" && tvStrategies.length === 0) {
      loadTvStrategies();
    }
  }, [sourceType, tvStrategies.length]);

  // Load portfolios when switching to portfolio mode
  useEffect(() => {
    if (sourceType === "portfolio" && portfolios.length === 0) {
      setPortfoliosLoading(true);
      api.portfolios()
        .then((p) => {
          setPortfolios(p);
          if (p.length > 0 && !selectedPortfolio) setSelectedPortfolio(p[0].id);
        })
        .catch(() => {})
        .finally(() => setPortfoliosLoading(false));
    }
  }, [sourceType]);

  const handleTvFolderImport = async () => {
    if (!tvFolderPath.trim()) return;
    setTvImporting(true);
    try {
      const result = await api.tvImportFolder(tvFolderPath.trim());
      loadTvStrategies();
      setTvSelected(result.filename);
      setTvFolderPath("");
    } catch { /* handled by UI */ }
    setTvImporting(false);
  };

  const currentBatch = batches.find((b) => b.id === selectedBatch);
  const currentRunItem = runs.find((r) => r.id === selectedRun);
  // Show OOS Results tab only when OOS date is set in batch mode
  const batchTabs = oosDate
    ? [...BATCH_TABS_BASE, OOS_TAB]
    : BATCH_TABS_BASE;
  const tabs = sourceType === "tv" ? TV_TABS : sourceType === "run" ? RUN_TABS : sourceType === "portfolio" ? PORTFOLIO_TABS : batchTabs;

  const handleSourceChange = (type: "batch" | "run" | "tv" | "portfolio" | "assets_tab") => {
    setSourceType(type);
    setShowBatchCatalog(false);
    setShowPortfolioCatalog(false);
    if (type === "run") setTab("full");
    else if (type === "tv") setTab("overview");
    else if (type === "portfolio") setTab("dashboard");
    else if (type === "assets_tab") setTab("assets_tab");
    else setTab("dashboard");
  };

  // Dashboard request for individual run analysis components
  const runReq: DashboardAnalysisRequest | null = selectedRun
    ? { run_id: selectedRun }
    : null;

  // Dashboard request for TV analysis
  const tvReq: DashboardAnalysisRequest = {
    strategy_filename: tvSelected || undefined,
    side_filter: tvSideFilter,
  };

  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <h1 className="text-2xl font-bold">Analysis</h1>
        <p className="text-ag-text-muted text-sm mt-1">
          Full performance analysis, Monte Carlo simulations, and portfolio optimization.
        </p>
      </div>

      {/* Source selector — always visible */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex rounded-lg border border-ag-border overflow-hidden">
          {(["batch", "run", "tv", "portfolio", "assets_tab"] as const).map((type) => (
            <button
              key={type}
              onClick={() => handleSourceChange(type)}
              className={`px-3 py-1.5 text-sm font-medium transition-colors ${
                sourceType === type
                  ? "bg-ag-accent text-white"
                  : "bg-ag-dark text-ag-text-muted hover:text-ag-text"
              }`}
            >
              {type === "batch" ? "Batch Backtest" : type === "run" ? "Individual Run" : type === "tv" ? "TV Analysis" : type === "portfolio" ? "Portfolio" : "Assets"}
            </button>
          ))}
        </div>

        {sourceType === "assets_tab" ? null : sourceType === "portfolio" ? (
          portfoliosLoading ? (
            <span className="text-sm text-ag-text-muted">Loading portfolios...</span>
          ) : portfolios.length === 0 ? (
            <span className="text-sm text-ag-text-muted">No saved portfolios.</span>
          ) : (
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowPortfolioCatalog(!showPortfolioCatalog)}
                className={`px-2.5 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                  showPortfolioCatalog
                    ? "bg-ag-accent text-white border-ag-accent"
                    : "bg-ag-dark text-ag-text-muted border-ag-border hover:text-ag-text hover:border-ag-accent/50"
                }`}
                title="Browse all portfolios in a categorized view"
              >
                Catalog
              </button>
              <select
                value={selectedPortfolio ?? ""}
                onChange={(e) => { setSelectedPortfolio(Number(e.target.value)); setShowPortfolioCatalog(false); }}
                className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm min-w-[250px]"
              >
                {portfolios.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {p.asset_count} assets
                    {p.metrics_snapshot.profit_factor !== undefined ? ` · ${p.optimization_end_date ? "IS " : ""}PF: ${(p.metrics_snapshot.profit_factor as number).toFixed(2)}` : ""}
                    {` · ID:${p.id}`}
                  </option>
                ))}
              </select>
            </div>
          )
        ) : loading ? (
          <span className="text-sm text-ag-text-muted">Loading...</span>
        ) : sourceType === "batch" ? (
          batches.length === 0 ? (
            <span className="text-sm text-ag-text-muted">No completed batch backtests.</span>
          ) : (
            <div className="flex items-center gap-2">
              <Suspense fallback={<div className="text-sm text-ag-text-muted">Loading...</div>}>
                <BatchGroupedSelector
                  batches={batches}
                  selectedBatch={selectedBatch}
                  onSelect={(id) => { setSelectedBatch(id); setShowBatchCatalog(false); }}
                />
              </Suspense>
              <button
                onClick={() => setShowBatchCatalog(!showBatchCatalog)}
                className={`px-2.5 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                  showBatchCatalog
                    ? "bg-ag-accent text-white border-ag-accent"
                    : "bg-ag-dark text-ag-text-muted border-ag-border hover:text-ag-text hover:border-ag-accent/50"
                }`}
                title="Browse all batches in a categorized view"
              >
                Catalog
              </button>
              {selectedBatch && (
                <button
                  onClick={async () => {
                    const batchName = currentBatch?.name || `Batch #${selectedBatch}`;
                    if (!confirm(`Delete "${batchName}" and all its runs?`)) return;
                    await api.deleteBatchRun(selectedBatch);
                    setBatches((prev) => prev.filter((b) => b.id !== selectedBatch));
                    setSelectedBatch(batches.find((b) => b.id !== selectedBatch)?.id ?? null);
                  }}
                  className="px-2 py-1.5 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg border border-red-500/30"
                  title="Delete this batch"
                >
                  Delete
                </button>
              )}
            </div>
          )
        ) : sourceType === "run" ? (
          runs.length === 0 ? (
            <span className="text-sm text-ag-text-muted">No completed backtest runs.</span>
          ) : (
            <div className="flex items-center gap-2">
              <select
                value={selectedRun ?? ""}
                onChange={(e) => setSelectedRun(Number(e.target.value))}
                className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
              >
                {runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} — {r.symbol} {r.timeframe}m
                    {r.created_at ? ` · ${new Date(r.created_at).toLocaleDateString()}` : ""}
                  </option>
                ))}
              </select>
              {selectedRun && (
                <button
                  onClick={async () => {
                    if (!confirm(`Delete run #${selectedRun}?`)) return;
                    await api.deleteBacktestRun(selectedRun);
                    setRuns((prev) => prev.filter((r) => r.id !== selectedRun));
                    setSelectedRun(runs.find((r) => r.id !== selectedRun)?.id ?? null);
                  }}
                  className="px-2 py-1.5 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg border border-red-500/30"
                  title="Delete this run"
                >
                  Delete
                </button>
              )}
            </div>
          )
        ) : (
          <>
            <select
              value={tvSelected}
              onChange={(e) => setTvSelected(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm min-w-[200px]"
            >
              <option value="">Select strategy...</option>
              {tvStrategies.map((s) => (
                <option key={s.filename} value={s.filename}>{s.name}</option>
              ))}
            </select>
            <select
              value={tvSideFilter}
              onChange={(e) => setTvSideFilter(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
            >
              <option value="All">All Sides</option>
              <option value="Long">Long</option>
              <option value="Short">Short</option>
            </select>
            <div className="flex gap-1 items-center">
              <input
                value={tvFolderPath}
                onChange={(e) => setTvFolderPath(e.target.value)}
                placeholder="Folder path with .xlsx files..."
                onKeyDown={(e) => e.key === "Enter" && handleTvFolderImport()}
                className="bg-ag-dark border border-ag-border rounded-l-lg px-3 py-1.5 text-sm min-w-[250px]"
              />
              <button
                onClick={handleTvFolderImport}
                disabled={tvImporting || !tvFolderPath.trim()}
                className="px-3 py-1.5 text-sm bg-ag-accent text-white rounded-r-lg disabled:opacity-50 hover:bg-ag-accent/90"
              >
                {tvImporting ? "..." : "Import"}
              </button>
            </div>
          </>
        )}

        {sourceType === "run" && currentRunItem && (
          <span className="text-xs text-ag-text-muted">
            {currentRunItem.symbol} · {currentRunItem.start_date?.split("T")[0]} → {currentRunItem.end_date?.split("T")[0]}
          </span>
        )}
      </div>

      {/* Assets tab — standalone, no sub-tabs needed */}
      {sourceType === "assets_tab" && (
        <Suspense fallback={<div className="flex items-center justify-center h-32"><Loader2 className="animate-spin text-ag-accent" size={24} /></div>}>
          <AssetStructuralPanel />
        </Suspense>
      )}

      {/* OOS Date Cutoff + Filters — batch and portfolio modes */}
      {((sourceType === "batch" && selectedBatch) || (sourceType === "portfolio" && selectedPortfolio)) && (
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-xs text-ag-text-muted">OOS Cutoff Date:</label>
          <input
            type="date"
            value={oosDate}
            onChange={(e) => setOosDate(e.target.value)}
            className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
          />
          {oosDate && (
            <>
              <span className="text-xs px-2 py-0.5 rounded bg-yellow-500/15 text-yellow-400">
                IS Mode: data before {oosDate}
              </span>
              <button
                onClick={() => setOosDate("")}
                className="text-xs text-ag-text-muted hover:text-ag-text underline"
              >
                Clear
              </button>
            </>
          )}
          <span className="w-px h-5 bg-ag-border" />
          <label className="text-xs text-ag-text-muted">Min Trades:</label>
          <input
            type="number"
            min={0}
            value={minTrades}
            onChange={(e) => setMinTrades(Math.max(0, parseInt(e.target.value) || 0))}
            className="w-16 bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm text-center"
          />
          <span className="w-px h-5 bg-ag-border" />
          <label className="text-xs text-ag-text-muted">Exclude Top:</label>
          <input
            type="number"
            min={0}
            value={excludeTop}
            onChange={(e) => setExcludeTop(Math.max(0, parseInt(e.target.value) || 0))}
            className="w-16 bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm text-center"
          />
          <span className="w-px h-5 bg-ag-border" />
          <label className="text-xs text-ag-text-muted">PF Min:</label>
          <input
            type="number"
            step="0.1"
            min={0}
            value={pfMin}
            onChange={(e) => setPfMin(e.target.value)}
            placeholder="—"
            className="w-16 bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm text-center"
          />
          <label className="text-xs text-ag-text-muted">PF Max:</label>
          <input
            type="number"
            step="0.1"
            min={0}
            value={pfMax}
            onChange={(e) => setPfMax(e.target.value)}
            placeholder="—"
            className="w-16 bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm text-center"
          />
          <span className="w-px h-5 bg-ag-border" />
          <label className="text-xs text-ag-text-muted">Min WR:</label>
          <input
            type="number"
            step="1"
            min={0}
            max={100}
            value={wrMin}
            onChange={(e) => setWrMin(e.target.value)}
            placeholder="—"
            className="w-16 bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm text-center"
          />
          {(minTrades > 0 || excludeTop > 0 || pfMin || pfMax || wrMin) && (
            <div className="flex flex-wrap gap-1">
              {minTrades > 0 && (
                <span className="text-xs px-2 py-0.5 rounded bg-orange-500/15 text-orange-400">
                  Min {minTrades} trades
                </span>
              )}
              {excludeTop > 0 && (
                <span className="text-xs px-2 py-0.5 rounded bg-purple-500/15 text-purple-400">
                  Excluding top {excludeTop}
                </span>
              )}
              {pfMin && (
                <span className="text-xs px-2 py-0.5 rounded bg-blue-500/15 text-blue-400">
                  PF &gt;= {pfMin}
                </span>
              )}
              {pfMax && (
                <span className="text-xs px-2 py-0.5 rounded bg-blue-500/15 text-blue-400">
                  PF &lt;= {pfMax}
                </span>
              )}
              {wrMin && (
                <span className="text-xs px-2 py-0.5 rounded bg-green-500/15 text-green-400">
                  WR &gt;= {wrMin}%
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {/* Live filter summary strip — shown for batch, TV analysis, individual run, assets (NOT portfolio) */}
      {sourceType !== "portfolio" && ((sourceType === "batch" && selectedBatch) || sourceType === "tv" || sourceType === "run" || sourceType === "assets_tab") && (
        <Suspense fallback={null}>
          <FilterSummaryStrip
            batchId={sourceType === "batch" ? selectedBatch ?? undefined : undefined}
            oosDate={oosDate || undefined}
            minTrades={minTrades || undefined}
            excludeTop={excludeTop || undefined}
            pfMin={pfMin ? parseFloat(pfMin) : undefined}
            pfMax={pfMax ? parseFloat(pfMax) : undefined}
            wrMin={wrMin ? parseFloat(wrMin) : undefined}
          />
        </Suspense>
      )}

      {/* Batch Catalog overlay */}
      {sourceType === "batch" && showBatchCatalog && (
        <Suspense fallback={<div className="flex items-center justify-center h-32"><Loader2 className="animate-spin text-ag-accent" size={24} /></div>}>
          <div className="bg-ag-card border border-ag-border rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-ag-text">Batch Backtest Catalog</h3>
              <button onClick={() => setShowBatchCatalog(false)} className="text-xs text-ag-text-muted hover:text-ag-text">Close</button>
            </div>
            <BatchCatalog
              batches={batches}
              selectedId={selectedBatch}
              onSelect={(id) => { setSelectedBatch(id); setShowBatchCatalog(false); }}
            />
          </div>
        </Suspense>
      )}

      {/* Portfolio Catalog overlay */}
      {sourceType === "portfolio" && showPortfolioCatalog && (
        <Suspense fallback={<div className="flex items-center justify-center h-32"><Loader2 className="animate-spin text-ag-accent" size={24} /></div>}>
          <div className="bg-ag-card border border-ag-border rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-ag-text">Portfolio Catalog</h3>
              <button onClick={() => setShowPortfolioCatalog(false)} className="text-xs text-ag-text-muted hover:text-ag-text">Close</button>
            </div>
            <PortfolioCatalog
              portfolios={portfolios}
              selectedId={selectedPortfolio}
              onSelect={(id) => { setSelectedPortfolio(id); setShowPortfolioCatalog(false); }}
            />
          </div>
        </Suspense>
      )}

      {/* Tabs — hidden for assets_tab mode */}
      {sourceType !== "assets_tab" && <div className="flex gap-1 border-b border-ag-border">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm transition-colors border-b-2 -mb-px ${
              tab === t.id
                ? "border-ag-accent text-ag-accent"
                : "border-transparent text-ag-text-muted hover:text-ag-text"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>}

      {/* Tab Content — hidden for assets_tab mode */}
      {sourceType !== "assets_tab" && <Suspense fallback={<div className="flex items-center justify-center h-32"><Loader2 className="animate-spin text-ag-accent" size={24} /></div>}>
      <div>
        {/* --- Batch tabs --- */}
        {tab === "dashboard" && sourceType === "batch" && selectedBatch && (
          <BatchDashboardPanel batchId={selectedBatch} dateTo={oosDate || undefined} minTrades={minTrades || undefined} excludeTopN={excludeTop || undefined} pfMin={pfMin ? parseFloat(pfMin) : undefined} pfMax={pfMax ? parseFloat(pfMax) : undefined} wrMin={wrMin ? parseFloat(wrMin) : undefined} timeframe={currentBatch?.timeframe} strategyId={currentBatch?.strategy_id} />
        )}
        {tab === "structure" && sourceType === "batch" && selectedBatch && (
          <StructuralQualityPanel batchId={selectedBatch} dateTo={oosDate || undefined} minTrades={minTrades || undefined} />
        )}
        {tab === "robustness" && sourceType === "batch" && selectedBatch && (
          <div className="space-y-4">
            <PFHistogram req={{ batch_id: selectedBatch, date_to: oosDate || undefined, min_trades: minTrades || undefined, exclude_top_n: excludeTop || undefined, pf_min: pfMin ? parseFloat(pfMin) : undefined, pf_max: pfMax ? parseFloat(pfMax) : undefined, wr_min: wrMin ? parseFloat(wrMin) : undefined }} />
            <RobustnessPanel req={{ batch_id: selectedBatch, date_to: oosDate || undefined, min_trades: minTrades || undefined, exclude_top_n: excludeTop || undefined, pf_min: pfMin ? parseFloat(pfMin) : undefined, pf_max: pfMax ? parseFloat(pfMax) : undefined, wr_min: wrMin ? parseFloat(wrMin) : undefined }} />
          </div>
        )}
        {tab === "montecarlo" && sourceType === "batch" && selectedBatch && (
          <MonteCarloPanel batchId={selectedBatch} dateTo={oosDate || undefined} minTrades={minTrades || undefined} excludeTopN={excludeTop || undefined} pfMin={pfMin ? parseFloat(pfMin) : undefined} pfMax={pfMax ? parseFloat(pfMax) : undefined} wrMin={wrMin ? parseFloat(wrMin) : undefined} />
        )}
        {tab === "compare" && <ComparisonPanel />}
        {tab === "categories" && sourceType === "batch" && selectedBatch && (
          <CategoryBreakdownPanel batchId={selectedBatch} dateTo={oosDate || undefined} minTrades={minTrades || undefined} excludeTopN={excludeTop || undefined} pfMin={pfMin ? parseFloat(pfMin) : undefined} pfMax={pfMax ? parseFloat(pfMax) : undefined} wrMin={wrMin ? parseFloat(wrMin) : undefined} />
        )}
        {tab === "session_intel" && sourceType === "batch" && selectedBatch && (
          <SessionIntelPanel batchId={selectedBatch} />
        )}
        {tab === "oos" && sourceType === "batch" && selectedBatch && oosDate && (
          <OOSResultsPanel batchId={selectedBatch} oosDate={oosDate} minTrades={minTrades || undefined} />
        )}

        {/* --- Individual run tabs --- */}
        {tab === "full" && sourceType === "run" && runReq && (
          <FullAnalysisPanel req={runReq} />
        )}
        {tab === "overview" && sourceType === "run" && runReq && (
          <MetricsPanel req={runReq} />
        )}
        {tab === "equity" && sourceType === "run" && runReq && (
          <EquityCurveChart req={runReq} />
        )}
        {tab === "monthly" && sourceType === "run" && runReq && (
          <MonthlyMatrix req={runReq} />
        )}
        {tab === "trades" && sourceType === "run" && selectedRun && (
          <RunTradesPanel runId={selectedRun} symbol={currentRunItem?.symbol} timeframe={currentRunItem?.timeframe} strategyId={currentRunItem?.strategy_id} />
        )}
        {tab === "montecarlo" && sourceType === "run" && selectedRun && (
          <MonteCarloPanel runId={selectedRun} />
        )}

        {/* --- TV Analysis tabs --- */}
        {sourceType === "tv" && tvSelected ? (
          <>
            {tab === "overview" && (
              <div className="space-y-4">
                <MetricsPanel req={tvReq} />
                <EquityCurveChart req={tvReq} />
              </div>
            )}
            {tab === "monthly" && <MonthlyMatrix req={tvReq} />}
            {tab === "assets" && <AssetBreakdown req={tvReq} />}
            {tab === "robustness" && <RobustnessPanel req={tvReq} />}
          </>
        ) : sourceType === "tv" && !tvSelected ? (
          <div className="text-center py-12">
            <p className="text-ag-text-muted">Select a TV strategy to view analysis.</p>
          </div>
        ) : null}

        {/* --- Portfolio tabs --- */}
        {sourceType === "portfolio" && selectedPortfolio && (
          <>
            {tab === "dashboard" && (
              <BatchDashboardPanel portfolioId={selectedPortfolio} dateTo={oosDate || undefined} minTrades={minTrades || undefined} excludeTopN={excludeTop || undefined} pfMin={pfMin ? parseFloat(pfMin) : undefined} pfMax={pfMax ? parseFloat(pfMax) : undefined} wrMin={wrMin ? parseFloat(wrMin) : undefined} timeframe={String(portfolios.find(x => x.id === selectedPortfolio)?.source_strategies?.timeframe ?? "")} strategyId={Number(portfolios.find(x => x.id === selectedPortfolio)?.source_strategies?.strategy_id) || undefined} />
            )}
            {tab === "robustness" && (
              <div className="space-y-4">
                <PFHistogram req={{ portfolio_id: selectedPortfolio, date_to: oosDate || undefined, min_trades: minTrades || undefined, exclude_top_n: excludeTop || undefined, pf_min: pfMin ? parseFloat(pfMin) : undefined, pf_max: pfMax ? parseFloat(pfMax) : undefined, wr_min: wrMin ? parseFloat(wrMin) : undefined }} />
                <RobustnessPanel req={{ portfolio_id: selectedPortfolio, date_to: oosDate || undefined, min_trades: minTrades || undefined, exclude_top_n: excludeTop || undefined, pf_min: pfMin ? parseFloat(pfMin) : undefined, pf_max: pfMax ? parseFloat(pfMax) : undefined, wr_min: wrMin ? parseFloat(wrMin) : undefined }} />
              </div>
            )}
            {tab === "montecarlo" && (() => {
              const p = portfolios.find(x => x.id === selectedPortfolio);
              const isDateTo = oosDate || p?.optimization_end_date || undefined;
              return <MonteCarloTabContainer portfolioId={selectedPortfolio} dateTo={isDateTo} minTrades={minTrades || undefined} excludeTopN={excludeTop || undefined} pfMin={pfMin ? parseFloat(pfMin) : undefined} pfMax={pfMax ? parseFloat(pfMax) : undefined} wrMin={wrMin ? parseFloat(wrMin) : undefined} />;
            })()}
            {tab === "compare" && <ComparisonPanel />}
            {tab === "categories" && (
              <CategoryBreakdownPanel portfolioId={selectedPortfolio} dateTo={oosDate || undefined} minTrades={minTrades || undefined} excludeTopN={excludeTop || undefined} pfMin={pfMin ? parseFloat(pfMin) : undefined} pfMax={pfMax ? parseFloat(pfMax) : undefined} wrMin={wrMin ? parseFloat(wrMin) : undefined} />
            )}
            {tab === "indepth" && (
              <PortfolioInDepthPanel portfolioId={selectedPortfolio} />
            )}
          </>
        )}

        {/* Empty state for batch/run/portfolio */}
        {!loading && (
          (sourceType === "batch" && !selectedBatch && (tab === "dashboard" || tab === "robustness" || tab === "montecarlo")) ||
          (sourceType === "run" && !selectedRun) ||
          (sourceType === "portfolio" && !selectedPortfolio)
        ) && (
          <div className="text-center py-12">
            <p className="text-ag-text-muted">
              {sourceType === "portfolio"
                ? "Select a portfolio to view analysis."
                : `Select a completed ${sourceType === "batch" ? "batch backtest" : "backtest run"} to begin analysis.`}
            </p>
          </div>
        )}
      </div>
      </Suspense>}
    </div>
  );
}

export default function AnalysisPage() {
  return (
    <Suspense fallback={<div className="text-ag-text-muted p-6">Loading...</div>}>
      <AnalysisContent />
    </Suspense>
  );
}
