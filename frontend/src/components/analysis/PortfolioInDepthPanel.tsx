"use client";
import { useState, useEffect } from "react";
import { Loader2, FlaskConical, Wifi, RefreshCw, GitCompare } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioDetail, PortfolioItem, CostSummary } from "@/lib/api";
import PortfolioOOSPanel from "./PortfolioOOSPanel";
import PortfolioComparePanel from "./PortfolioComparePanel";
import AssetSwapTool from "./AssetSwapTool";
import LiveViewsPanel from "@/components/portfolios/LiveViewsPanel";
import PortfolioTestPanel from "./PortfolioTestPanel";
import RiskComparisonPanel from "./RiskComparisonPanel";
import PortfolioFullMetrics from "./PortfolioFullMetrics";
import PortfolioAssetTab from "./PortfolioAssetTab";
import ISvsLivePanel from "@/components/portfolios/ISvsLivePanel";
import LiveSettingsPanel from "./LiveSettingsPanel";
import FilterableMonthlyPnL from "./FilterableMonthlyPnL";
import PortfolioHealthMonitor from "./PortfolioHealthMonitor";
import SavedPresetTab from "./SavedPresetTab";
import WalkForwardPanel from "./WalkForwardPanel";
import OOSvsLivePanel from "./OOSvsLivePanel";

type SubTab = "details" | "assets" | "oos" | "health" | "swap" | "live" | "test" | "risk" | "settings" | "preset";

interface Props {
  portfolioId: number;
}

export default function PortfolioInDepthPanel({ portfolioId }: Props) {
  const [detail, setDetail] = useState<PortfolioDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [subTab, setSubTab] = useState<SubTab>("details");

  const loadDetail = async () => {
    setLoading(true);
    try {
      setDetail(await api.portfolioDetail(portfolioId));
    } catch {
      setDetail(null);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadDetail();
    setSubTab("details");
  }, [portfolioId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32">
        <Loader2 className="animate-spin text-ag-accent" size={24} />
      </div>
    );
  }

  if (!detail) {
    return <p className="text-sm text-ag-text-muted">Failed to load portfolio details.</p>;
  }

  return <DetailView detail={detail} subTab={subTab} setSubTab={setSubTab} onRefresh={loadDetail} />;
}

function DetailView({ detail, subTab, setSubTab, onRefresh }: {
  detail: PortfolioDetail; subTab: SubTab; setSubTab: (t: SubTab) => void; onRefresh: () => void;
}) {
  const [promoting, setPromoting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [desc, setDesc] = useState(detail.description || "");
  const [descSaving, setDescSaving] = useState(false);
  const [descDirty, setDescDirty] = useState(false);

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
    { id: "risk", label: "Risk Profiles" },
    { id: "settings", label: "Live Settings" },
    { id: "test", label: "Test Portfolio" },
    { id: "preset", label: "Saved Preset" },
  ];

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const { task_id } = await api.refreshPortfolio(detail.id);
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
    if (!confirm(`Promote "${detail.name}" to ${label}?`)) return;
    setPromoting(true);
    try {
      await api.promotePortfolio(detail.id, type);
      alert(`Portfolio promoted to ${label}!`);
    } catch { alert("Failed to promote portfolio."); }
    setPromoting(false);
  };

  return (
    <div className="space-y-3">
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
        <div className="flex gap-1">
          {tabs.map((t) => (
            <button key={t.id} onClick={() => setSubTab(t.id)}
              className={`px-3 py-1 text-xs rounded-t transition-colors ${
                subTab === t.id ? "bg-ag-accent/15 text-ag-accent font-medium" : "text-ag-text-muted hover:text-ag-text"
              }`}>{t.label}</button>
          ))}
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

      {subTab === "details" && <DetailsSubView detail={detail} />}
      {subTab === "assets" && <PortfolioAssetTab portfolioId={detail.id} timeframe={String(detail.source_strategies?.timeframe ?? "")} strategyId={Number(detail.source_strategies?.strategy_id) || undefined} />}
      {subTab === "oos" && (
        <PortfolioOOSPanel
          portfolioId={detail.id}
          optimizationEndDate={detail.optimization_end_date}
          timeframe={String(detail.source_strategies?.timeframe ?? "")}
          strategyId={Number(detail.source_strategies?.strategy_id) || undefined}
        />
      )}
      {subTab === "health" && <PortfolioHealthMonitor portfolioId={detail.id} portfolioType={detail.portfolio_type} />}
      {subTab === "swap" && <AssetSwapTool portfolio={detail} onSwapped={onRefresh} />}
      {subTab === "live" && <LiveViewsPanel portfolioId={detail.id} />}
      {subTab === "risk" && <RiskComparisonPanel portfolio={detail} />}
      {subTab === "settings" && <LiveSettingsPanel portfolioId={detail.id} />}
      {subTab === "test" && <PortfolioTestPanel portfolio={detail} />}
      {subTab === "preset" && <SavedPresetTab detail={detail} onRefreshed={onRefresh} />}
    </div>
  );
}

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
    stability: fallback.stability ?? 0,
    calmar_ratio: g("Calmar Ratio"),
    recovery_factor: g("Recovery Factor"),
  };
}

function DetailsSubView({ detail }: { detail: PortfolioDetail }) {
  const ss = detail.source_strategies as Record<string, string | number | boolean | null | undefined | string[]> | undefined;
  const hasSettings = ss && Object.keys(ss).length > 0;
  const [oosMetrics, setOosMetrics] = useState<Record<string, number>>({});
  // IS metrics must ONLY come from the /oos-date endpoint (IS-only split).
  // Never fall back to metrics_snapshot — it could contain combined IS+OOS data.
  const [isMetrics, setIsMetrics] = useState<Record<string, number>>(
    detail.optimization_end_date ? {} : detail.metrics_snapshot
  );
  const [isLoadError, setIsLoadError] = useState(false);
  const [costSummary, setCostSummary] = useState<CostSummary | null>(null);

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
        setCostSummary(r.cost_summary || null);
      })
      .catch(() => {
        setIsLoadError(true);
      });
  }, [detail.id, detail.optimization_end_date]);

  const isWfoSource =
    detail.portfolio_source === "wfo" || detail.portfolio_source === "wfas_phase2";

  return (
    <>
      {/* Real walk-forward view (WFO tracks + hybrid Phase 2 only). */}
      <WalkForwardPanel detail={detail} />

      {/* OOS vs LIVE comparison — trades since save date vs walk-forward expectation. */}
      {isWfoSource && <OOSvsLivePanel detail={detail} />}

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

      {/* IS + OOS Monthly PnL — with batch + direction filters */}
      {!isWfoSource && detail.optimization_end_date && (
        <FilterableMonthlyPnL
          portfolioId={detail.id}
          cutoffDate={detail.optimization_end_date}
          mode="both"
        />
      )}

      {!isWfoSource && <PortfolioFullMetrics portfolioId={detail.id} />}

      {/* Full OOS-only KPI panel for WFO portfolios (walk-forward stitched). */}
      {isWfoSource && (
        <div className="border border-ag-accent/20 rounded-lg p-3 bg-ag-accent/5">
          <div className="text-sm font-semibold text-ag-accent mb-2">
            Full Metrics (OOS walk-forward only)
          </div>
          <PortfolioFullMetrics portfolioId={detail.id} source="wfo" />
        </div>
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
              {ss!.objective != null && <SettingBadge label="Objective" value={String(ss!.objective)} />}
              {ss!.corr_method != null && <SettingBadge label="Correlation" value={String(ss!.corr_method)} />}
              {ss!.div_algo != null && <SettingBadge label="Diversification" value={String(ss!.div_algo)} />}
              {ss!.n_assets != null && <SettingBadge label="# Assets" value={String(ss!.n_assets)} />}
              {ss!.n_portfolios != null && <SettingBadge label="# Portfolios" value={String(ss!.n_portfolios)} />}
              {ss!.n_candidates != null && <SettingBadge label="# Candidates" value={String(ss!.n_candidates)} />}
              {ss!.split_mode != null && <SettingBadge label="Split Mode" value={ss!.split_mode ? "L/S Split" : "Combined"} />}
              {ss!.split_mode && ss!.n_longs != null && <SettingBadge label="# Longs" value={String(ss!.n_longs)} />}
              {ss!.split_mode && ss!.n_shorts != null && <SettingBadge label="# Shorts" value={String(ss!.n_shorts)} />}
              {ss!.within_objective != null && <SettingBadge label="Within Obj" value={String(ss!.within_objective)} />}
              {ss!.combined_objective != null && <SettingBadge label="Combined Obj" value={String(ss!.combined_objective)} />}
              {ss!.max_overlap != null && <SettingBadge label="Max Overlap" value={String(ss!.max_overlap)} />}
              {ss!.portfolio_source != null && <SettingBadge label="Source" value={String(ss!.portfolio_source)} />}
              {typeof ss!.pool_per_batch === "number" && Number(ss!.pool_per_batch) > 0 && (
                <SettingBadge label="Pool/Batch" value={String(ss!.pool_per_batch)} />
              )}
            </SettingsGroup>

            {/* ── Data Period ── */}
            <SettingsGroup title="Data Period">
              {ss!.date_from != null && <SettingBadge label="Start Date" value={String(ss!.date_from)} />}
              {ss!.date_to != null && <SettingBadge label="OOS Cutoff" value={String(ss!.date_to)} />}
              {ss!.timeframe != null && <SettingBadge label="Timeframe" value={String(ss!.timeframe)} />}
              {Array.isArray(ss!.extra_timeframes) && (ss!.extra_timeframes as string[]).length > 0 && (
                <SettingBadge label="Extra TFs" value={(ss!.extra_timeframes as string[]).join(", ")} />
              )}
            </SettingsGroup>

            {/* ── Pre-Filters ── */}
            <SettingsGroup title="Pre-Filters">
              {ss!.min_trades != null && <SettingBadge label="Min Trades" value={String(ss!.min_trades)} />}
              {ss!.min_pf != null && <SettingBadge label="Min PF" value={String(ss!.min_pf)} />}
              {ss!.min_wr != null && <SettingBadge label="Min WR" value={`${ss!.min_wr}%`} />}
              {Array.isArray(ss!.category_filter) && (ss!.category_filter as string[]).length > 0 && (
                <SettingBadge label="Categories" value={(ss!.category_filter as string[]).join(", ")} />
              )}
              {ss!.max_allowed_drawdown_pct != null && (
                <SettingBadge label="Max DD" value={`${ss!.max_allowed_drawdown_pct}%`} />
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
                  <SettingBadge label="Risk Value" value={`${detail.batch_settings.risk_value}%`} />
                )}
              </SettingsGroup>
            )}

            {/* ── Post-Hoc Stress Costs ── */}
            {(Number(ss!.extra_slippage_pct) > 0 || Number(ss!.extra_commission_pct) > 0 ||
              Number(ss!.funding_daily_pct) > 0 || Number(ss!.spread_pct) > 0) && (
              <SettingsGroup title="Stress-Test Costs">
                {Number(ss!.extra_slippage_pct) > 0 && (
                  <SettingBadge label="Extra Slippage" value={`${ss!.extra_slippage_pct}%`} />
                )}
                {Number(ss!.extra_commission_pct) > 0 && (
                  <SettingBadge label="Extra Commission" value={`${ss!.extra_commission_pct}%`} />
                )}
                {Number(ss!.funding_daily_pct) > 0 && (
                  <SettingBadge label="Funding Rate" value={`${ss!.funding_daily_pct}%/day`} />
                )}
                {Number(ss!.spread_pct) > 0 && (
                  <SettingBadge label="Spread" value={`${ss!.spread_pct}%`} />
                )}
                {costSummary && costSummary.total_stress_cost > 0 && (
                  <div className="w-full mt-1 flex flex-wrap gap-3 text-[10px] text-ag-text-muted">
                    <span>Total Cost: <span className="text-ag-red font-mono font-bold">${costSummary.total_stress_cost.toLocaleString()}</span></span>
                    <span>Avg/Trade: <span className="text-ag-red font-mono font-bold">${costSummary.avg_cost_per_trade.toFixed(2)}</span></span>
                    {costSummary.cost_breakdown.slippage > 0 && <span>Slip: ${costSummary.cost_breakdown.slippage.toLocaleString()}</span>}
                    {costSummary.cost_breakdown.commission > 0 && <span>Comm: ${costSummary.cost_breakdown.commission.toLocaleString()}</span>}
                    {costSummary.cost_breakdown.spread > 0 && <span>Spread: ${costSummary.cost_breakdown.spread.toLocaleString()}</span>}
                    {costSummary.cost_breakdown.funding > 0 && <span>Funding: ${costSummary.cost_breakdown.funding.toLocaleString()}</span>}
                  </div>
                )}
              </SettingsGroup>
            )}

            {/* ── Seed Ranking ── */}
            {(Array.isArray(ss!.seed_metrics) || ss!.max_seeds != null || ss!.prioritize_stability) && (
              <SettingsGroup title="Seed Ranking">
                {Array.isArray(ss!.seed_metrics) && (ss!.seed_metrics as string[]).length > 0 && (
                  <SettingBadge label="Metrics" value={(ss!.seed_metrics as string[]).join(", ")} />
                )}
                {ss!.max_seeds != null && <SettingBadge label="Max Seeds" value={String(ss!.max_seeds)} />}
                {ss!.prioritize_stability && <SettingBadge label="Stability" value="Prioritized" />}
                {ss!.seed_weights != null && typeof ss!.seed_weights === "object" && !Array.isArray(ss!.seed_weights) && (
                  <SettingBadge label="Weights" value={Object.entries(ss!.seed_weights as unknown as Record<string, number>).map(([k, v]) => `${k}:${v}`).join(", ")} />
                )}
              </SettingsGroup>
            )}

            {/* ── Trade-Count Constraints ── */}
            {ss!.tc_enabled && (
              <SettingsGroup title="Trade-Count Constraints">
                <SettingBadge label="TC" value="Active" />
                {ss!.tc_low_threshold != null && <SettingBadge label="Low Threshold" value={String(ss!.tc_low_threshold)} />}
                {ss!.tc_max_low != null && <SettingBadge label="Max Below Low" value={String(ss!.tc_max_low)} />}
                {ss!.tc_high_threshold != null && <SettingBadge label="High Threshold" value={String(ss!.tc_high_threshold)} />}
                {ss!.tc_min_high != null && <SettingBadge label="Min Above High" value={String(ss!.tc_min_high)} />}
              </SettingsGroup>
            )}

            {/* ── Benchmark Robustness ── */}
            {ss!.benchmark_enabled && (
              <SettingsGroup title="Benchmark Robustness">
                <SettingBadge label="Benchmark" value="Active" />
                {ss!.benchmark_source != null && <SettingBadge label="Source" value={String(ss!.benchmark_source)} />}
                {ss!.benchmark_split != null && <SettingBadge label="Split" value={String(ss!.benchmark_split)} />}
                {ss!.benchmark_threshold != null && <SettingBadge label="PF Threshold" value={`≥${ss!.benchmark_threshold}`} />}
                {ss!.benchmark_elite_target != null && <SettingBadge label="Elite Target" value={String(ss!.benchmark_elite_target)} />}
                {ss!.benchmark_exclude_fragile && <SettingBadge label="Excl. Fragile" value="Yes" />}
                {ss!.benchmark_dsr_gate && <SettingBadge label="DSR Gate" value="Yes" />}
                {ss!.benchmark_consistency_blend != null && (
                  <SettingBadge label="Consistency" value={String(ss!.benchmark_consistency_blend)} />
                )}
                {ss!.benchmark_fragility_blend != null && (
                  <SettingBadge label="Fragility" value={String(ss!.benchmark_fragility_blend)} />
                )}
                {ss!.benchmark_wr_harmony_blend != null && (
                  <SettingBadge label="WR Harmony" value={String(ss!.benchmark_wr_harmony_blend)} />
                )}
              </SettingsGroup>
            )}

            {/* ── Recency Weighting ── */}
            {ss!.recency_enabled && (
              <SettingsGroup title="Recency Weighting">
                <SettingBadge label="Recency" value="Active" />
                {ss!.recency_mode != null && <SettingBadge label="Mode" value={String(ss!.recency_mode)} />}
                {ss!.recency_batch_size != null && <SettingBadge label="Batch Size" value={String(ss!.recency_batch_size)} />}
                {ss!.recency_boost_factor != null && <SettingBadge label="Boost Factor" value={`${ss!.recency_boost_factor}x`} />}
              </SettingsGroup>
            )}

            {/* ── Risk Profile ── */}
            {ss!.risk_profile_mode != null && (
              <SettingsGroup title="Risk Profile">
                <SettingBadge label="Mode" value={String(ss!.risk_profile_mode)} />
                {ss!.risk_profile_value != null && (
                  <SettingBadge label="Value" value={String(ss!.risk_profile_value)} />
                )}
                {ss!.risk_profile_initial_capital != null && (
                  <SettingBadge label="Initial Capital" value={`$${Number(ss!.risk_profile_initial_capital).toLocaleString()}`} />
                )}
                {ss!.risk_profile_multiplier != null && (
                  <SettingBadge label="Multiplier" value={`${ss!.risk_profile_multiplier}x`} />
                )}
                {ss!.risk_profile_max_streak != null && (
                  <SettingBadge label="Max Streak" value={String(ss!.risk_profile_max_streak)} />
                )}
                {ss!.risk_profile_martingale_compound != null && (
                  <SettingBadge label="Compound" value={String(ss!.risk_profile_martingale_compound)} />
                )}
                {ss!.risk_profile_sizing_master === "per_batch" && (
                  <SettingBadge label="Sizing" value="Per Batch" />
                )}
                {ss!.risk_profile_martingale_scope && ss!.risk_profile_martingale_scope !== "asset" && ss!.risk_profile_sizing_master !== "per_batch" && (
                  <SettingBadge label="Scope" value={
                    ss!.risk_profile_martingale_scope === "portfolio" ? "Portfolio-wide" :
                    ss!.risk_profile_martingale_scope === "asset_batch" ? "Asset + Batch" :
                    String(ss!.risk_profile_martingale_scope)
                  } />
                )}
                {ss!.risk_profile_kelly_n != null && (
                  <SettingBadge label="Kelly N" value={String(ss!.risk_profile_kelly_n)} />
                )}
                {ss!.risk_profile_kelly_warmup != null && (
                  <SettingBadge label="Kelly Warmup" value={`${ss!.risk_profile_kelly_warmup} trades`} />
                )}
              </SettingsGroup>
            )}

            {/* ── Quality Pre-Filter ── */}
            {ss!.prefilter_enabled && (
              <SettingsGroup title="Quality Pre-Filter">
                <SettingBadge label="Pre-Filter" value="Active" />
                {ss!.prefilter_mode != null && <SettingBadge label="Mode" value={String(ss!.prefilter_mode)} />}
                {ss!.prefilter_hurst_min != null && <SettingBadge label="Hurst Min" value={String(ss!.prefilter_hurst_min)} />}
                {ss!.prefilter_hurst_max != null && <SettingBadge label="Hurst Max" value={String(ss!.prefilter_hurst_max)} />}
                {ss!.prefilter_ter_percentile != null && <SettingBadge label="TER %ile" value={String(ss!.prefilter_ter_percentile)} />}
                {ss!.prefilter_vr_max != null && <SettingBadge label="VR Max" value={String(ss!.prefilter_vr_max)} />}
                {ss!.prefilter_min_crossing_rate != null && (
                  <SettingBadge label="Min Cross Rate" value={String(ss!.prefilter_min_crossing_rate)} />
                )}
              </SettingsGroup>
            )}

            {/* ── AC Post-Filter ── */}
            {ss!.ac_postfilter && (
              <SettingsGroup title="Autocorrelation Filter">
                <SettingBadge label="AC Filter" value="Active" />
                {ss!.ac_lag != null && <SettingBadge label="Lag" value={String(ss!.ac_lag)} />}
              </SettingsGroup>
            )}

            {/* ── WFO Settings ── */}
            {(ss!.is_months != null || ss!.oos_months != null) && (
              <SettingsGroup title="Walk-Forward">
                {ss!.is_months != null && <SettingBadge label="IS Months" value={String(ss!.is_months)} />}
                {ss!.oos_months != null && <SettingBadge label="OOS Months" value={String(ss!.oos_months)} />}
                {ss!.last_oos_end != null && <SettingBadge label="Last OOS End" value={String(ss!.last_oos_end)} />}
                {ss!.time_mode != null && <SettingBadge label="Time Mode" value={String(ss!.time_mode)} />}
                {ss!.event_mode && <SettingBadge label="Event Mode" value="Yes" />}
              </SettingsGroup>
            )}

            {/* ── Event-Based Mode ── */}
            {ss!.event_mode && (
              <SettingsGroup title="Event-Based Mode">
                {ss!.n_trades != null && <SettingBadge label="Lookback Trades" value={String(ss!.n_trades)} />}
                {ss!.staleness_days != null && <SettingBadge label="Staleness Days" value={String(ss!.staleness_days)} />}
                {ss!.n_per_group != null && <SettingBadge label="Per Group" value={String(ss!.n_per_group)} />}
                {ss!.rebalance_every != null && <SettingBadge label="Rebal Every" value={String(ss!.rebalance_every)} />}
                {ss!.rebalance_mode != null && <SettingBadge label="Rebal Mode" value={String(ss!.rebalance_mode)} />}
              </SettingsGroup>
            )}

            {/* ── Directional Batch / Manual Mode ── */}
            {(ss!.directional_batches || ss!.wfo_mode === "manual") && (
              <SettingsGroup title="Advanced Mode">
                {ss!.wfo_mode != null && <SettingBadge label="WFO Mode" value={String(ss!.wfo_mode)} />}
                {ss!.directional_batches && <SettingBadge label="Directional" value="Yes" />}
                {ss!.obj_layer1 != null && <SettingBadge label="Layer 1" value={String(ss!.obj_layer1)} />}
                {ss!.obj_layer2 != null && <SettingBadge label="Layer 2" value={String(ss!.obj_layer2)} />}
                {ss!.obj_layer3 != null && <SettingBadge label="Layer 3" value={String(ss!.obj_layer3)} />}
                {ss!.n_per_batch != null && <SettingBadge label="Per Batch" value={String(ss!.n_per_batch)} />}
              </SettingsGroup>
            )}

            {/* ── Build Performance ── */}
            {ss!.optimization_timing && typeof ss!.optimization_timing === "object" && !Array.isArray(ss!.optimization_timing) && (
              <SettingsGroup title="Build Performance">
                {Object.entries(ss!.optimization_timing as unknown as Record<string, number>)
                  .filter(([, val]) => typeof val === "number")
                  .map(([key, val]) => (
                    <SettingBadge key={key} label={key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())} value={`${val.toFixed(1)}s`} />
                  ))}
              </SettingsGroup>
            )}

          </div>
        </details>
      )}
      <div>
        <h4 className="text-xs text-ag-text-muted mb-1">Assets</h4>
        <div className="flex flex-wrap gap-1.5">
          {detail.assets.filter(a => a.is_active).map((a) => {
            const isLong = a.side === "Long";
            const isShort = a.side === "Short";
            const color = isLong ? "bg-ag-green/15 text-ag-green"
              : isShort ? "bg-ag-red/15 text-ag-red"
              : "bg-ag-accent/15 text-ag-accent";
            return (
              <span key={a.id} className={`px-2 py-0.5 ${color} rounded text-xs`}>
                {a.symbol.replace("USDT_Long", "").replace("USDT_Short", "").replace("USDT", "")}
                {a.side !== "both" && ` (${a.side[0]})`}
                {a.strategy_name && a.strategy_name !== "Unknown" && (
                  <span className="opacity-60 ml-1">{a.strategy_name}</span>
                )}
              </span>
            );
          })}
        </div>
      </div>
    </>
  );
}

function SettingsGroup({ title, children }: { title: string; children: React.ReactNode }) {
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
