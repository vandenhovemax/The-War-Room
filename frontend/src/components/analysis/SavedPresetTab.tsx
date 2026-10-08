"use client";
import { useState } from "react";
import { PlayCircle, ExternalLink, Loader2, CheckCircle2, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioDetail, CostSummary } from "@/lib/api";

type Src = Record<string, string | number | boolean | null | undefined | string[] | Record<string, unknown>>;

interface Props {
  detail: PortfolioDetail;
  costSummary?: CostSummary | null;
  onRefreshed?: () => void;
}

function SettingsGroup({ title, children }: { title: string; children: React.ReactNode }) {
  const kids = Array.isArray(children) ? children.filter(Boolean) : children ? [children] : [];
  if (kids.length === 0) return null;
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-ag-text-muted/60 mb-1">{title}</p>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1.5">
        {children}
      </div>
    </div>
  );
}

function SettingBadge({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className={`flex items-center gap-1 px-2 py-1 rounded text-xs ${
      highlight ? "bg-ag-accent/15 text-ag-accent" : "bg-ag-dark/50"
    }`}>
      <span className={highlight ? "text-ag-accent/80" : "text-ag-text-muted"}>{label}:</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

export default function SavedPresetTab({ detail, costSummary, onRefreshed }: Props) {
  const ss = (detail.source_strategies as Src | undefined) ?? {};
  const isWFAS = detail.portfolio_source === "wfo" || detail.portfolio_source === "wfas_phase2";
  const isCross = Boolean(detail.is_cross_strat) || Boolean(ss.is_cross_strat);

  const sourceLabel =
    detail.portfolio_source === "wfas_phase2" ? "WFAS (Phase 2 — Live Signal)" :
    detail.portfolio_source === "wfo" ? "WFAS (Asset Selection)" :
    detail.portfolio_source === "cross_strategy" ? "Cross-Strategy Optimization" :
    detail.portfolio_source === "manual" ? "Manual" :
    "Batch Optimization";

  // Execution-time filter detection. Different code paths store the filter
  // under different keys — cover all of them so the badge lights up whenever
  // ANY form of time filter exists on this preset.
  const sessionFilterEnabled = Boolean(ss.session_filter_enabled);
  const sessionExcludeHours = Array.isArray(ss.session_filter_exclude_hours)
    ? (ss.session_filter_exclude_hours as unknown[])
    : [];
  const sessionExcludeDays = Array.isArray(ss.session_filter_exclude_days)
    ? (ss.session_filter_exclude_days as unknown[])
    : [];
  const execFilter =
    ss.exec_time_filter ??
    ss.execution_time_filter ??
    ss.exec_filter ??
    ss.oos_exec_filter ??
    null;
  const execEnabled = Boolean(
    ss.exec_time_filter_enabled ??
    ss.execution_time_filter_enabled ??
    sessionFilterEnabled ??
    sessionExcludeHours.length > 0 ??
    sessionExcludeDays.length > 0 ??
    (typeof execFilter === "string" && execFilter) ??
    (typeof execFilter === "object" && execFilter !== null)
  );
  const execDescription: string =
    sessionExcludeHours.length > 0 || sessionExcludeDays.length > 0
      ? [
          sessionExcludeHours.length > 0 ? `Excluded hours (NYT): ${sessionExcludeHours.join(", ")}` : "",
          sessionExcludeDays.length > 0 ? `Excluded days: ${sessionExcludeDays.join(", ")}` : "",
        ].filter(Boolean).join(" · ")
      : typeof execFilter === "string"
      ? execFilter
      : typeof execFilter === "object" && execFilter
      ? JSON.stringify(execFilter)
      : "";

  // Rerun state
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const handleRerun = async () => {
    const target = isWFAS
      ? `Re-run the FULL WFAS replay for "${detail.name}"?\n\n• Uses every setting exactly as saved (batches, IS/OOS months, risk profile, exec filter, etc.).\n• Creates a new WFAS result you can compare against this portfolio.\n\nThis may take several minutes.`
      : `Re-run the backtest for "${detail.name}" with fresh candle data?\n\n• Uses every saved setting (costs, risk profile, batches).\n• Updates the live metrics shown on this portfolio.`;
    if (!confirm(target)) return;

    setResult(null);
    setRunning(true);
    try {
      if (isWFAS) {
        // For WFAS portfolios we don't have a stored wfo_run_id on the
        // portfolio itself (the run is referenced via source_strategies).
        // Navigate the user to the WFAS saved-runs tab so they can pick the
        // source run and click "Replay Exact" — that endpoint already exists
        // (/api/wfo/runs/{id}/replay-exact) and creates a new reproducible run.
        window.location.href = "/wfo";
        return;
      }
      const { task_id } = await api.refreshPortfolio(detail.id);
      const poll = setInterval(async () => {
        try {
          const status = await api.refreshStatus(task_id);
          if (status.status === "completed") {
            clearInterval(poll);
            setRunning(false);
            setResult({ ok: true, message: "Backtest re-ran successfully with fresh data." });
            onRefreshed?.();
          } else if (status.status === "error") {
            clearInterval(poll);
            setRunning(false);
            setResult({ ok: false, message: "Refresh failed — see server logs." });
          }
        } catch {
          // transient errors during polling — ignore, next tick retries
        }
      }, 3000);
    } catch (e) {
      setRunning(false);
      setResult({
        ok: false,
        message: e instanceof Error ? e.message : "Failed to trigger rerun.",
      });
    }
  };

  return (
    <div className="space-y-4">
      {/* ── Top banner: what this portfolio is + rerun action ── */}
      <div className="rounded-lg border border-ag-border bg-gradient-to-b from-ag-accent/5 to-transparent p-3">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="space-y-1 min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-ag-text-muted">
              Portfolio preset
            </p>
            <h3 className="text-sm font-semibold text-ag-accent">{sourceLabel}</h3>
            <p className="text-[11px] text-ag-text-muted">
              {isWFAS
                ? "This portfolio was produced by a Walk-Forward Asset Selection run. Rerun will replay the same WFAS with identical settings."
                : isCross
                ? "Cross-strategy portfolio — assets come from multiple batches, each keeps its own strategy parameters."
                : "Single-strategy optimized portfolio — all assets share the same strategy config."}
            </p>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handleRerun}
              disabled={running}
              className="flex items-center gap-1.5 px-3 py-2 text-xs rounded bg-ag-accent text-ag-dark font-medium hover:bg-ag-accent/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title={
                isWFAS
                  ? "Open WFAS saved runs so you can Replay Exact this portfolio's source run"
                  : "Re-run the backtest with fresh candles and all saved settings"
              }
            >
              {running ? <Loader2 size={13} className="animate-spin" /> : <PlayCircle size={13} />}
              {running ? "Running..." : isWFAS ? "Replay in WFAS" : "Rerun Portfolio"}
            </button>
            {isWFAS && (
              <a
                href="/wfo"
                className="flex items-center gap-1 px-2.5 py-2 text-xs rounded border border-ag-border text-ag-text-muted hover:text-ag-accent hover:border-ag-accent/40 transition-colors"
                title="Go to the WFAS Saved Runs page"
              >
                <ExternalLink size={12} /> WFAS page
              </a>
            )}
          </div>
        </div>

        {result && (
          <div className={`mt-3 flex items-start gap-2 text-xs rounded px-2 py-1.5 ${
            result.ok ? "bg-ag-green/10 text-ag-green" : "bg-ag-red/10 text-ag-red"
          }`}>
            {result.ok ? <CheckCircle2 size={13} className="mt-0.5" /> : <AlertTriangle size={13} className="mt-0.5" />}
            <span>{result.message}</span>
          </div>
        )}
      </div>

      {/* ── Execution-time filter status (dedicated, requested by the user) ── */}
      <div className={`rounded-lg border p-3 ${
        execEnabled ? "border-ag-green/30 bg-ag-green/5" : "border-yellow-400/30 bg-yellow-400/5"
      }`}>
        <div className="flex items-start gap-2">
          {execEnabled
            ? <CheckCircle2 size={14} className="text-ag-green mt-0.5" />
            : <AlertTriangle size={14} className="text-yellow-400 mt-0.5" />}
          <div className="space-y-1">
            <p className="text-xs font-semibold">
              Execution-Time Filter:{" "}
              <span className={execEnabled ? "text-ag-green" : "text-yellow-400"}>
                {execEnabled ? "ACTIVE" : "NOT SET"}
              </span>
            </p>
            {execEnabled && execDescription && (
              <p className="text-[11px] text-ag-text-muted font-mono break-all">
                {execDescription}
              </p>
            )}
            {execEnabled && !execDescription && (
              <p className="text-[11px] text-ag-text-muted">
                Custom filter — see stored settings below.
              </p>
            )}
            <p className="text-[10px] text-ag-text-muted">
              Exec filter decides which NYT hours/weekdays the engine accepts new
              entries. Enforced in BOTH optimization scoring AND live trading —
              entries outside the window are blocked before the order is placed.
            </p>
          </div>
        </div>
      </div>

      {/* ── All settings, expanded by default ── */}
      <div className="space-y-3">

        <SettingsGroup title="Identification">
          <SettingBadge label="Portfolio ID" value={String(detail.id)} />
          <SettingBadge label="Name" value={String(detail.name)} />
          <SettingBadge label="Type" value={String(detail.portfolio_type)} highlight />
          <SettingBadge label="Mode" value={String((detail as unknown as { mode?: string }).mode || "non_hybrid")} />
          <SettingBadge label="Source" value={sourceLabel} />
          {detail.optimization_end_date && (
            <SettingBadge label="OOS Cutoff" value={String(detail.optimization_end_date).slice(0, 10)} />
          )}
          {detail.created_at && (
            <SettingBadge label="Created" value={String(detail.created_at).slice(0, 10)} />
          )}
        </SettingsGroup>

        <SettingsGroup title="Core Optimization">
          {ss.objective != null && <SettingBadge label="Objective" value={String(ss.objective)} highlight />}
          {ss.corr_method != null && <SettingBadge label="Correlation" value={String(ss.corr_method)} />}
          {ss.div_algo != null && <SettingBadge label="Diversification" value={String(ss.div_algo)} />}
          {ss.n_assets != null && <SettingBadge label="# Assets" value={String(ss.n_assets)} />}
          {ss.n_portfolios != null && <SettingBadge label="# Portfolios" value={String(ss.n_portfolios)} />}
          {ss.n_candidates != null && <SettingBadge label="# Candidates" value={String(ss.n_candidates)} />}
          {ss.split_mode != null && <SettingBadge label="Split Mode" value={ss.split_mode ? "L/S Split" : "Combined"} />}
          {Boolean(ss.split_mode) && ss.n_longs != null && <SettingBadge label="# Longs" value={String(ss.n_longs)} />}
          {Boolean(ss.split_mode) && ss.n_shorts != null && <SettingBadge label="# Shorts" value={String(ss.n_shorts)} />}
          {ss.within_objective != null && <SettingBadge label="Within Obj" value={String(ss.within_objective)} />}
          {ss.combined_objective != null && <SettingBadge label="Combined Obj" value={String(ss.combined_objective)} />}
          {ss.max_overlap != null && <SettingBadge label="Max Overlap" value={String(ss.max_overlap)} />}
          {typeof ss.pool_per_batch === "number" && Number(ss.pool_per_batch) > 0 && (
            <SettingBadge label="Pool/Batch" value={String(ss.pool_per_batch)} />
          )}
        </SettingsGroup>

        <SettingsGroup title="Data Period">
          {ss.date_from != null && <SettingBadge label="Start" value={String(ss.date_from)} />}
          {ss.date_to != null && <SettingBadge label="OOS Cutoff" value={String(ss.date_to)} highlight />}
          {ss.timeframe != null && <SettingBadge label="Timeframe" value={String(ss.timeframe)} />}
          {Array.isArray(ss.extra_timeframes) && (ss.extra_timeframes as string[]).length > 0 && (
            <SettingBadge label="Extra TFs" value={(ss.extra_timeframes as string[]).join(", ")} />
          )}
        </SettingsGroup>

        <SettingsGroup title="Walk-Forward (WFAS)">
          {ss.is_months != null && <SettingBadge label="IS Months" value={String(ss.is_months)} highlight />}
          {ss.oos_months != null && <SettingBadge label="OOS Months" value={String(ss.oos_months)} highlight />}
          {ss.last_oos_end != null && <SettingBadge label="Last OOS End" value={String(ss.last_oos_end)} />}
          {ss.time_mode != null && <SettingBadge label="Time Mode" value={String(ss.time_mode)} />}
          {ss.event_mode && <SettingBadge label="Event Mode" value="Yes" />}
          {ss.track_rank != null && <SettingBadge label="Track Rank" value={String(ss.track_rank)} />}
          {ss.preset_id != null && <SettingBadge label="Preset ID" value={String(ss.preset_id)} />}
        </SettingsGroup>

        <SettingsGroup title="Risk Profile (from optimization)">
          {ss.risk_profile_mode != null && <SettingBadge label="Mode" value={String(ss.risk_profile_mode)} highlight />}
          {ss.risk_profile_value != null && <SettingBadge label="Base Risk $" value={String(ss.risk_profile_value)} />}
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
          {ss.risk_profile_martingale_scope != null && (
            <SettingBadge label="Scope" value={String(ss.risk_profile_martingale_scope)} />
          )}
          {ss.risk_profile_kelly_n != null && <SettingBadge label="Kelly N" value={String(ss.risk_profile_kelly_n)} />}
          {ss.risk_profile_kelly_warmup != null && (
            <SettingBadge label="Kelly Warmup" value={`${ss.risk_profile_kelly_warmup} trades`} />
          )}
          {ss.risk_profile_sizing_master != null && (
            <SettingBadge label="Sizing Master" value={String(ss.risk_profile_sizing_master)} />
          )}
        </SettingsGroup>

        {detail.batch_settings && Object.keys(detail.batch_settings).length > 0 && (
          <SettingsGroup title="Backtest Costs (from source batch)">
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

        {(Number(ss.extra_slippage_pct) > 0 || Number(ss.extra_commission_pct) > 0 ||
          Number(ss.funding_daily_pct) > 0 || Number(ss.spread_pct) > 0) && (
          <SettingsGroup title="Stress-Test Costs">
            {Number(ss.extra_slippage_pct) > 0 && <SettingBadge label="Extra Slip" value={`${ss.extra_slippage_pct}%`} />}
            {Number(ss.extra_commission_pct) > 0 && <SettingBadge label="Extra Comm" value={`${ss.extra_commission_pct}%`} />}
            {Number(ss.funding_daily_pct) > 0 && <SettingBadge label="Funding" value={`${ss.funding_daily_pct}%/d`} />}
            {Number(ss.spread_pct) > 0 && <SettingBadge label="Spread" value={`${ss.spread_pct}%`} />}
            {costSummary && costSummary.total_stress_cost > 0 && (
              <SettingBadge label="Total Cost" value={`$${costSummary.total_stress_cost.toLocaleString()}`} />
            )}
          </SettingsGroup>
        )}

        <SettingsGroup title="Pre-Filters">
          {ss.min_trades != null && <SettingBadge label="Min Trades" value={String(ss.min_trades)} />}
          {ss.min_pf != null && <SettingBadge label="Min PF" value={String(ss.min_pf)} />}
          {ss.min_wr != null && <SettingBadge label="Min WR" value={`${ss.min_wr}%`} />}
          {Array.isArray(ss.category_filter) && (ss.category_filter as string[]).length > 0 && (
            <SettingBadge label="Categories" value={(ss.category_filter as string[]).join(", ")} />
          )}
          {ss.max_allowed_drawdown_pct != null && (
            <SettingBadge label="Max DD" value={`${ss.max_allowed_drawdown_pct}%`} />
          )}
        </SettingsGroup>

        {Boolean(ss.prefilter_enabled) && (
          <SettingsGroup title="Quality Pre-Filter">
            <SettingBadge label="Active" value="Yes" highlight />
            {ss.prefilter_mode != null && <SettingBadge label="Mode" value={String(ss.prefilter_mode)} />}
            {ss.prefilter_hurst_min != null && <SettingBadge label="Hurst Min" value={String(ss.prefilter_hurst_min)} />}
            {ss.prefilter_hurst_max != null && <SettingBadge label="Hurst Max" value={String(ss.prefilter_hurst_max)} />}
            {ss.prefilter_ter_percentile != null && <SettingBadge label="TER %ile" value={String(ss.prefilter_ter_percentile)} />}
            {ss.prefilter_vr_max != null && <SettingBadge label="VR Max" value={String(ss.prefilter_vr_max)} />}
          </SettingsGroup>
        )}

        {Boolean(ss.benchmark_enabled) && (
          <SettingsGroup title="Benchmark Robustness">
            <SettingBadge label="Active" value="Yes" highlight />
            {ss.benchmark_source != null && <SettingBadge label="Source" value={String(ss.benchmark_source)} />}
            {ss.benchmark_split != null && <SettingBadge label="Split" value={String(ss.benchmark_split)} />}
            {ss.benchmark_threshold != null && <SettingBadge label="PF Threshold" value={`≥${ss.benchmark_threshold}`} />}
            {ss.benchmark_elite_target != null && <SettingBadge label="Elite Target" value={String(ss.benchmark_elite_target)} />}
          </SettingsGroup>
        )}

        {Boolean(ss.recency_enabled) && (
          <SettingsGroup title="Recency Weighting">
            <SettingBadge label="Active" value="Yes" highlight />
            {ss.recency_mode != null && <SettingBadge label="Mode" value={String(ss.recency_mode)} />}
            {ss.recency_batch_size != null && <SettingBadge label="Batch Size" value={String(ss.recency_batch_size)} />}
            {ss.recency_boost_factor != null && <SettingBadge label="Boost" value={`${ss.recency_boost_factor}x`} />}
          </SettingsGroup>
        )}

        {Boolean(ss.tc_enabled) && (
          <SettingsGroup title="Trade-Count Constraints">
            <SettingBadge label="Active" value="Yes" highlight />
            {ss.tc_low_threshold != null && <SettingBadge label="Low Thresh" value={String(ss.tc_low_threshold)} />}
            {ss.tc_max_low != null && <SettingBadge label="Max Below" value={String(ss.tc_max_low)} />}
            {ss.tc_high_threshold != null && <SettingBadge label="High Thresh" value={String(ss.tc_high_threshold)} />}
            {ss.tc_min_high != null && <SettingBadge label="Min Above" value={String(ss.tc_min_high)} />}
          </SettingsGroup>
        )}

        {/* Raw JSON for anything we missed — guarantees "EVERYTHING selected" */}
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-ag-text-muted hover:text-ag-text">
            Raw stored settings JSON (everything else)
          </summary>
          <pre className="mt-2 p-2 bg-ag-dark/80 rounded text-[10px] text-ag-text-muted overflow-auto max-h-96 border border-ag-border/50">
{JSON.stringify(ss, null, 2)}
          </pre>
        </details>
      </div>
    </div>
  );
}
