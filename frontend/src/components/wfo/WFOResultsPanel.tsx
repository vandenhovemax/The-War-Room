"use client";
import { useState } from "react";
import { Save } from "lucide-react";
import { api } from "@/lib/api";
import type { WFOResult, WFOTrack, WFOWindowCandidate, WFOWindowResult, EquityCurvePoint, MonthlyMatrixData } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import WFOEquityCurve from "./WFOEquityCurve";
import WFOMonthlyMatrix from "./WFOMonthlyMatrix";
import WFOMetricsPanel from "./WFOMetricsPanel";
import WFASMonthTradesModal from "./WFASMonthTradesModal";
import WFOAssetBreakdown from "./WFOAssetBreakdown";

interface Props {
  result: WFOResult;
  wfoConfig?: Record<string, unknown>;
  showTracksOnly?: boolean;
}

type ResultTab = "cum_is" | "cum_oos" | "cum_h1" | "cum_h2" | "tracks";

/* ── Number formatting helpers ─────────────────── */
function fmtDollar(v: number): string {
  const abs = Math.abs(v);
  const str = abs >= 1000
    ? abs.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })
    : abs.toFixed(2);
  return `${v < 0 ? "-" : ""}$${str}`;
}

function fmtPct(v: number, decimals = 1): string {
  return `${(v * 100).toFixed(decimals)}%`;
}

function fmtNumber(v: number): string {
  return v.toLocaleString("en-US");
}

export default function WFOResultsPanel({ result, wfoConfig, showTracksOnly }: Props) {
  const [tab, setTab] = useState<ResultTab>("cum_oos");

  const isOverlapping = result.time_mode === "overlapping";
  const s = result.summary;

  if (showTracksOnly) {
    return <TracksView tracks={result.tracks} windows={result.windows} wfoConfig={wfoConfig ?? {}} />;
  }

  const tabs: { id: ResultTab; label: string }[] = [
    { id: "cum_is", label: "Cumulative IS" },
    { id: "cum_oos", label: "Cumulative OOS" },
    ...(isOverlapping ? [
      { id: "cum_h1" as ResultTab, label: "Cumulative H1" },
      { id: "cum_h2" as ResultTab, label: "Cumulative H2" },
    ] : []),
    { id: "tracks", label: "By Track" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 border-b border-ag-border/50 pb-2">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`px-3 py-1.5 text-xs rounded-t transition-colors ${
              tab === t.id
                ? "bg-ag-accent/15 text-ag-accent font-medium"
                : "text-ag-text-muted hover:text-ag-text"
            }`}>
            {t.label}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-3 text-xs text-ag-text-muted">
          {s && (
            <span>{s.total_windows} windows · WFE: {fmtPct(s.aggregate_wfe ?? 0, 0)}</span>
          )}
          {isOverlapping && s?.avg_degradation != null && (
            <span className={s.avg_degradation >= 0.8 ? "text-ag-green font-medium" : s.avg_degradation >= 0.5 ? "text-amber-400 font-medium" : "text-ag-red font-medium"}>
              Avg DR: {fmtPct(s.avg_degradation, 0)}
            </span>
          )}
        </div>
      </div>

      {/* Batch Selection summary banner */}
      {s?.mode === "batch_selection" && (
        <BatchSelectionBanner summary={s} />
      )}

      {/* Health check warnings */}
      {result.health_check && !result.health_check.healthy && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg px-4 py-3 space-y-1">
          <p className="text-xs font-medium text-amber-400">Health Check Warnings</p>
          {result.health_check.warnings.map((w, i) => (
            <p key={i} className="text-xs text-amber-300/80">{w}</p>
          ))}
        </div>
      )}

      {/* Risk profile diagnostic */}
      {result.risk_profile_diagnostic && (
        <RiskProfileBanner diag={result.risk_profile_diagnostic} />
      )}

      {/* PnL Scaling diagnostic */}
      {result.pnl_scaling_diagnostic && (
        <PnlScalingBanner diag={result.pnl_scaling_diagnostic} />
      )}

      {/* Window generation diagnostic */}
      {result.window_diagnostic && (
        <WindowDiagnosticBanner diag={result.window_diagnostic} />
      )}

      {tab === "cum_is" && <CumulativeView metrics={result.cumulative_is} label="In-Sample" />}
      {tab === "cum_oos" && (
        <CumulativeOOSView
          isMetrics={result.cumulative_is}
          oosMetrics={result.cumulative_oos}
          expectedOos={result.expected_oos ?? undefined}
          wfe={s?.aggregate_wfe ?? 0}
          qStarts={s?.q_starts}
          equityCurve={result.aggregate_oos_equity_curve}
          monthlyMatrix={result.aggregate_oos_monthly_matrix}
          breakdown={result.aggregate_oos_breakdown}
          wfoRunId={result.wfo_run_id}
          phase2Metrics={result.step2?.cumulative_metrics}
          phase2EquityCurve={result.step2?.aggregate_equity_curve}
          phase2MonthlyMatrix={result.step2?.aggregate_monthly_matrix}
          phase2Breakdown={result.step2?.aggregate_breakdown}
          phase2SelectionChanges={result.step2?.selection_changes}
          phase2SelectedRecords={result.step2?.selected_records}
          phase2ConfigUsed={result.step2?.config_used}
          phase2WinningBatches={result.step2?.winning_batches}
          phase2PerWindow={result.step2?.per_window}
          wfoConfig={wfoConfig}
          windows={result.windows}
        />
      )}
      {tab === "cum_h1" && isOverlapping && result.cumulative_oos_h1 && (
        <CumulativeView metrics={result.cumulative_oos_h1} label="OOS — H1 (first half)" />
      )}
      {tab === "cum_h2" && isOverlapping && result.cumulative_oos_h2 && (
        <CumulativeView metrics={result.cumulative_oos_h2} label="OOS — H2 (second half)" />
      )}
      {tab === "tracks" && <TracksView tracks={result.tracks} windows={result.windows} wfoConfig={wfoConfig ?? {}} />}
    </div>
  );
}

/* ── Batch Selection KPI Banner ─────────────────── */
function BatchSelectionBanner({ summary: s }: { summary: WFOResult["summary"] }) {
  const wrColor = (pct: number) =>
    pct >= 60 ? "text-ag-green" : pct >= 40 ? "text-amber-400" : "text-ag-red";

  const deltaColor = (d: number) =>
    d >= 0 ? "text-ag-green" : d > -15 ? "text-amber-400" : "text-ag-red";

  return (
    <div className="px-4 py-3 bg-ag-accent/5 border border-ag-accent/20 rounded-lg space-y-3">
      {/* Row 1: Primary WR + PnL */}
      <div className="flex items-center gap-6 flex-wrap text-xs">
        {/* Calibrated WR (exec-filtered if available, else full) */}
        {s.calibrated_wr && (
          <div>
            <span className="text-ag-text-muted">OOS WR: </span>
            <span className={`font-bold text-sm ${wrColor(s.calibrated_wr_pct ?? 0)}`}>
              {s.calibrated_wr} ({s.calibrated_wr_pct}%)
            </span>
          </div>
        )}
        {s.calibrated_pnl != null && (
          <div>
            <span className="text-ag-text-muted">OOS PnL: </span>
            <span className={`font-mono font-medium ${s.calibrated_pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
              {fmtDollar(s.calibrated_pnl)}
            </span>
            {s.expected_calibrated_pnl != null && (
              <span className="ml-1 text-amber-400 text-[10px]">
                (expected: {fmtDollar(s.expected_calibrated_pnl)})
              </span>
            )}
          </div>
        )}
        {s.total_batches_pool != null && (
          <div>
            <span className="text-ag-text-muted">Pool: </span>
            <span className="font-mono">
              {s.active_batches_pool ?? s.total_batches_pool}/{s.total_batches_pool} batches
            </span>
          </div>
        )}
        {s.q_starts != null && s.q_starts > 1 && (
          <div className="text-ag-text-muted">Q-starts: {s.q_starts}</div>
        )}
        <div className="text-ag-text-muted ml-auto text-[10px]">Cold-start windows excluded</div>
      </div>

      {/* Row 2: Full vs Exec split (only when exec filter is active) */}
      {s.has_exec_filter && s.full_wr && s.exec_wr && (
        <div className="flex items-center gap-4 flex-wrap text-xs border-t border-ag-border/20 pt-2">
          <div>
            <span className="text-ag-text-muted">Full WR: </span>
            <span className={`font-mono font-medium ${wrColor(s.full_wr_pct ?? 0)}`}>
              {s.full_wr} ({s.full_wr_pct}%)
            </span>
          </div>
          <div>
            <span className="text-ag-text-muted">Exec WR: </span>
            <span className={`font-mono font-medium ${wrColor(s.exec_wr_pct ?? 0)}`}>
              {s.exec_wr} ({s.exec_wr_pct}%)
            </span>
          </div>
          {s.delta_pp != null && (
            <div>
              <span className="text-ag-text-muted">Delta: </span>
              <span className={`font-mono font-medium ${deltaColor(s.delta_pp)}`}>
                {s.delta_pp > 0 ? "+" : ""}{s.delta_pp}pp
              </span>
            </div>
          )}
          {s.ef_ratio_pct != null && (
            <div>
              <span className="text-ag-text-muted">e/f: </span>
              <span className="font-mono">{s.ef_ratio_pct}%</span>
            </div>
          )}
          {s.full_pnl != null && (
            <div>
              <span className="text-ag-text-muted">Full PnL: </span>
              <span className={`font-mono ${s.full_pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                {fmtDollar(s.full_pnl)}
              </span>
            </div>
          )}
          {s.exec_pnl != null && (
            <div>
              <span className="text-ag-text-muted">Exec PnL: </span>
              <span className={`font-mono ${s.exec_pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                {fmtDollar(s.exec_pnl)}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Row 3: Concurrent trades */}
      {s.oos_max_concurrent != null && (
        <div className="flex items-center gap-4 flex-wrap text-xs border-t border-ag-border/20 pt-2">
          <span className="text-ag-text-muted">Concurrent Trades:</span>
          <div>
            <span className="text-ag-text-muted">Peak: </span>
            <span className="font-mono font-medium">{s.oos_max_concurrent}</span>
          </div>
          {s.oos_avg_max_concurrent != null && (
            <div>
              <span className="text-ag-text-muted">Avg Max/Window: </span>
              <span className="font-mono">{s.oos_avg_max_concurrent}</span>
            </div>
          )}
          {s.oos_avg_concurrent != null && (
            <div>
              <span className="text-ag-text-muted">Avg: </span>
              <span className="font-mono">{s.oos_avg_concurrent}</span>
            </div>
          )}
        </div>
      )}

      {/* Row 4: Consecutive wins/losses */}
      {(s.max_consec_wins != null || s.max_consec_losses != null) && (
        <div className="flex items-center gap-4 flex-wrap text-xs border-t border-ag-border/20 pt-2">
          <span className="text-ag-text-muted">Consecutive:</span>
          {s.max_consec_wins != null && (
            <div>
              <span className="text-ag-text-muted">Max Wins: </span>
              <span className="font-mono text-ag-green">{s.max_consec_wins}</span>
            </div>
          )}
          {s.avg_consec_wins != null && (
            <div>
              <span className="text-ag-text-muted">Avg Wins: </span>
              <span className="font-mono">{s.avg_consec_wins}</span>
            </div>
          )}
          {s.max_consec_losses != null && (
            <div>
              <span className="text-ag-text-muted">Max Losses: </span>
              <span className="font-mono text-ag-red">{s.max_consec_losses}</span>
            </div>
          )}
          {s.avg_consec_losses != null && (
            <div>
              <span className="text-ag-text-muted">Avg Losses: </span>
              <span className="font-mono">{s.avg_consec_losses}</span>
            </div>
          )}
        </div>
      )}

      {/* Row 5: Active features */}
      {(s.l2_groups || s.pruning_enabled || s.past_oos_confirm) && (
        <div className="flex gap-2 flex-wrap text-[10px] border-t border-ag-border/20 pt-2">
          {s.l2_groups && <span className="px-1.5 py-0.5 bg-ag-accent/10 text-ag-accent rounded">L2 Groups</span>}
          {s.pruning_enabled && <span className="px-1.5 py-0.5 bg-ag-accent/10 text-ag-accent rounded">Pruning</span>}
          {s.past_oos_confirm && <span className="px-1.5 py-0.5 bg-ag-accent/10 text-ag-accent rounded">Past OOS</span>}
        </div>
      )}
    </div>
  );
}

function CumulativeView({ metrics, label }: {
  metrics: Record<string, number | null>;
  label: string;
}) {
  // Primary row: big KPIs
  const primaryKeys = ["net_profit", "profit_factor", "win_rate", "total_trades"];
  // Extended row: per-window stats
  const extendedKeys = [
    "max_drawdown", "cumulative_max_dd", "avg_trade", "avg_window_pnl",
    "window_wr", "avg_wr_per_window", "avg_trades_per_window",
    "sortino", "sharpe", "best_window_pnl", "worst_window_pnl",
  ];
  return (
    <div className="space-y-3">
      <h4 className="text-xs text-ag-text-muted">Aggregated {label} across all windows</h4>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
        {primaryKeys.map((k) => {
          const v = metrics[k];
          if (v == null) return null;
          const isPositive = k.includes("profit") ? v > 0 : undefined;
          return (
            <div key={k} className="px-3 py-2 bg-ag-dark/50 rounded-lg">
              <div className="text-ag-text-muted text-[10px] mb-0.5">{formatLabel(k)}</div>
              <div className={`font-mono font-bold text-sm ${isPositive === true ? "text-ag-green" : isPositive === false ? "text-ag-red" : ""}`}>
                {formatValue(k, v)}
              </div>
            </div>
          );
        })}
      </div>
      {/* Extended stats (only show if available) */}
      {extendedKeys.some(k => metrics[k] != null) && (
        <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-2 text-xs">
          {extendedKeys.map((k) => {
            const v = metrics[k];
            if (v == null) return null;
            return (
              <div key={k} className="px-2 py-1.5 bg-ag-dark/30 rounded">
                <div className="text-ag-text-muted text-[10px]">{formatLabel(k)}</div>
                <div className="font-mono font-medium">{formatValue(k, v)}</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function CumulativeOOSView({ isMetrics, oosMetrics, expectedOos, wfe, qStarts, equityCurve, monthlyMatrix, breakdown, wfoRunId, phase2Metrics, phase2EquityCurve, phase2MonthlyMatrix, phase2Breakdown, phase2SelectionChanges, phase2SelectedRecords, phase2ConfigUsed, phase2WinningBatches, phase2PerWindow, wfoConfig, windows }: {
  isMetrics: Record<string, number | null>;
  oosMetrics: Record<string, number | null>;
  expectedOos?: Record<string, number | null>;
  wfe: number;
  qStarts?: number;
  equityCurve?: EquityCurvePoint[];
  monthlyMatrix?: MonthlyMatrixData;
  breakdown?: NonNullable<WFOWindowCandidate["asset_breakdown"]>;
  wfoRunId?: number;
  phase2Metrics?: Record<string, number | null>;
  phase2EquityCurve?: EquityCurvePoint[];
  phase2MonthlyMatrix?: MonthlyMatrixData;
  phase2Breakdown?: NonNullable<WFOWindowCandidate["asset_breakdown"]>;
  phase2SelectionChanges?: { date: string; added: string[]; removed: string[]; window_num?: number }[];
  phase2SelectedRecords?: { dt: string; pnl: number; asset: string; symbol: string; side: string; timeframe: string; batch_id?: number; batch_name?: string; batch_group?: string; pnl_multiplier?: number }[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  phase2ConfigUsed?: Record<string, any>;
  phase2WinningBatches?: number[];
  phase2PerWindow?: Array<Record<string, unknown>>;
  wfoConfig?: Record<string, unknown>;
  windows?: WFOWindowResult[];
}) {
  const hasPhase2 = !!phase2Metrics && Object.keys(phase2Metrics).length > 0;
  const [monthModal, setMonthModal] = useState<{ year: number; month: number } | null>(null);
  const [savingPhase2, setSavingPhase2] = useState(false);
  const hasExpected = expectedOos && qStarts && qStarts > 1;

  // ── Save Phase 2 as Portfolio ──
  const handleSavePhase2 = async () => {
    if (!phase2SelectionChanges?.length || !phase2SelectedRecords?.length) return;
    const defaultName = phase2ConfigUsed?.preset_id
      ? `Phase 2 — ${phase2ConfigUsed.preset_id}`
      : "Phase 2 Portfolio";
    const name = prompt("Portfolio name:", defaultName);
    if (!name) return;
    setSavingPhase2(true);
    try {
      // Extract the currently active assets from the LAST selection change
      // Walk through all changes chronologically to reconstruct the final portfolio
      const sortedChanges = [...phase2SelectionChanges].sort((a, b) => a.date.localeCompare(b.date));
      const currentPortfolio = new Set<string>();
      for (const chg of sortedChanges) {
        for (const a of (chg.added || [])) currentPortfolio.add(a);
        for (const r of (chg.removed || [])) currentPortfolio.delete(r);
      }
      // If no selection changes reconstructed a portfolio, fall back to
      // unique assets from the last N selected records
      if (currentPortfolio.size === 0) {
        const lastRecords = phase2SelectedRecords.slice(-50);
        for (const r of lastRecords) {
          if (r.asset) currentPortfolio.add(r.asset);
        }
      }
      // Build asset list with metadata from selected_records
      // Asset key formats: "SYMBOL_Side" (legacy) or "B{batch_id}_SYMBOL_Side" (per-batch hybrid)
      const assetMap = new Map<string, { symbol: string; side: string; batch_id?: number; timeframe?: string; asset_key: string }>();
      for (const rec of phase2SelectedRecords) {
        if (currentPortfolio.has(rec.asset) && !assetMap.has(rec.asset)) {
          // Parse per-batch key: "B46_BTCUSDT_Long" → batch_id=46, symbol=BTCUSDT, side=Long
          const perBatchMatch = rec.asset.match(/^B(\d+)_(.+)_(Long|Short)$/);
          // Fallback: simple key "BTCUSDT_Long"
          const simpleMatch = !perBatchMatch ? rec.asset.match(/^(.+)_(Long|Short)$/) : null;

          let symbol: string, side: string, batchId: number | undefined;
          if (perBatchMatch) {
            batchId = parseInt(perBatchMatch[1], 10);
            symbol = perBatchMatch[2];
            side = perBatchMatch[3];
          } else if (simpleMatch) {
            symbol = simpleMatch[1];
            side = simpleMatch[2];
            batchId = (rec as Record<string, unknown>).batch_id as number | undefined;
          } else {
            symbol = rec.symbol || rec.asset;
            side = rec.side || "both";
            batchId = (rec as Record<string, unknown>).batch_id as number | undefined;
          }

          assetMap.set(rec.asset, {
            symbol,
            side,
            batch_id: batchId,
            timeframe: rec.timeframe,
            asset_key: rec.asset,
          });
        }
      }
      const assets = Array.from(assetMap.values());
      if (assets.length === 0) {
        alert("No active assets found in Phase 2 results.");
        setSavingPhase2(false);
        return;
      }
      // Derive initial_basket (basket active BEFORE the first selection_change).
      // Replay all changes backwards from the final reconstructed set.
      const initialBasketSet = new Set(currentPortfolio);
      for (const c of [...sortedChanges].reverse()) {
        for (const a of (c.added || [])) initialBasketSet.delete(a);
        for (const r of (c.removed || [])) initialBasketSet.add(r);
      }
      const windows_meta = (windows ?? []).map((w) => ({
        window_num: w.window_num,
        is_start: w.is_start,
        is_end: w.is_end,
        oos_start: w.oos_start,
        oos_end: w.oos_end,
        oos_h1_start: w.oos_h1_start,
        oos_h1_end: w.oos_h1_end,
        oos_h2_start: w.oos_h2_start,
        oos_h2_end: w.oos_h2_end,
      }));
      const result = await api.wfoSavePhase2Track({
        name,
        batch_ids: (wfoConfig?.batch_ids as number[]) ?? [],
        assets,
        preset_config: wfoConfig ?? {},
        metrics_snapshot: phase2Metrics as Record<string, unknown>,
        winning_batch_ids: phase2WinningBatches ?? [],
        optimization_end_date: (wfoConfig?.last_oos_end as string) ?? undefined,
        preset_id: phase2ConfigUsed?.preset_id ?? undefined,
        selection_changes: sortedChanges.map((c) => ({
          step: c.step,
          date: c.date,
          added: c.added ?? [],
          removed: c.removed ?? [],
        })),
        initial_basket: Array.from(initialBasketSet).sort(),
        selected_records: phase2SelectedRecords as unknown as Array<Record<string, unknown>> | undefined,
        aggregate_monthly_matrix: phase2MonthlyMatrix as unknown as Record<string, unknown> | undefined,
        aggregate_equity_curve: phase2EquityCurve as unknown as Array<Record<string, unknown>> | undefined,
        aggregate_breakdown: phase2Breakdown as unknown as Array<Record<string, unknown>> | undefined,
        cumulative_metrics: phase2Metrics as unknown as Record<string, unknown> | undefined,
        windows_meta,
        wfo_per_window: phase2PerWindow,
      });
      alert(`Phase 2 portfolio saved! "${result.name}" with ${result.asset_count} active assets.`);
    } catch (e) {
      alert("Failed to save Phase 2 portfolio: " + (e instanceof Error ? e.message : String(e)));
    }
    setSavingPhase2(false);
  };
  // IS vs OOS comparison table
  const compareKeys = [
    "net_profit", "profit_factor", "win_rate", "total_trades",
    "max_drawdown", "avg_trade", "sharpe", "sortino",
    "recovery_factor", "calmar",
  ];
  // Keys where expected differs from raw OOS (absolute metrics that get divided by q_starts)
  const absoluteKeys = new Set([
    "net_profit", "total_trades", "max_drawdown", "cumulative_max_dd",
  ]);
  // OOS-only extended stats (no IS equivalent)
  const oosOnlyKeys = [
    "cumulative_max_dd", "avg_window_pnl", "window_wr", "windows_profitable",
    "avg_wr_per_window", "avg_trades_per_window",
    "sortino", "sharpe", "best_window_pnl", "worst_window_pnl",
    // Rich cumulative KPIs
    "recovery_factor", "calmar", "longest_dd_windows",
    // Averaged per-window KPIs
    "avg_stability", "avg_expectancy", "avg_payoff",
    "avg_avg_win", "avg_avg_loss", "avg_recovery",
    "max_longest_dd_days",
  ];

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 px-3 py-2 rounded bg-ag-dark/50">
        <span className="text-xs text-ag-text-muted">
          Walk Forward Efficiency
          <HintTip text="WFE = OOS performance ÷ IS performance. Measures how well in-sample results translate to out-of-sample. >50% = good, 30-50% = acceptable, <30% = potential overfit." />
          :
        </span>
        <span className={`text-sm font-bold ${wfe >= 0.5 ? "text-ag-green" : wfe >= 0.3 ? "text-amber-400" : "text-ag-red"}`}>
          {fmtPct(wfe)}
        </span>
        {hasExpected && (
          <span className="ml-auto text-[10px] text-amber-400/80">
            Q-starts={qStarts} — OOS×{qStarts} overlapped · Expected = OOS÷{qStarts}
          </span>
        )}
      </div>

      {/* IS vs OOS comparison table */}
      <div className="overflow-x-auto">
        <h4 className="text-[10px] text-ag-text-muted uppercase mb-1">
          IS vs OOS Comparison
          <HintTip text="Side-by-side comparison of cumulative In-Sample (training) vs Out-of-Sample (unseen) metrics across all windows. Change % shows how much performance degrades from IS to OOS — smaller drops = more robust." />
        </h4>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-ag-text-muted border-b border-ag-border/50">
              <th className="text-left py-1 pr-4">Metric</th>
              <th className="text-right py-1 px-3" title="In-Sample: cumulative metrics from all training periods">IS</th>
              <th className="text-right py-1 px-3" title="Out-of-Sample: cumulative metrics from all unseen test periods">OOS</th>
              {hasExpected && <th className="text-right py-1 px-3 text-amber-400" title="Expected values adjusted for Q-aligned window overlap">Expected</th>}
              {hasPhase2 && <th className="text-right py-1 px-3 text-ag-accent" title="Phase 2 (Hybrid Step 2): tradeable numbers after walk-forward composite re-rank on Phase 1 winners">Phase 2</th>}
              <th className="text-right py-1 pl-3" title="Percentage change from IS to OOS — negative = performance degradation">Change</th>
              {hasPhase2 && <th className="text-right py-1 pl-3 text-ag-accent" title="Phase 2 vs OOS delta — positive = Phase 2 selection improved on raw OOS">P2 Δ</th>}
            </tr>
          </thead>
          <tbody>
            {compareKeys.map((k) => {
              const isVal = isMetrics[k];
              const oosVal = oosMetrics[k];
              const expVal = hasExpected ? (expectedOos?.[k] ?? null) : null;
              const p2Val = hasPhase2 ? (phase2Metrics?.[k] ?? null) : null;
              if (isVal == null && oosVal == null && p2Val == null) return null;
              const change = isVal && oosVal && isVal !== 0
                ? ((oosVal - isVal) / Math.abs(isVal)) * 100 : null;
              const p2Delta = p2Val != null && oosVal != null && oosVal !== 0
                ? ((p2Val - oosVal) / Math.abs(oosVal)) * 100 : null;
              const showExp = hasExpected && absoluteKeys.has(k);
              return (
                <tr key={k} className="border-b border-ag-border/20">
                  <td className="py-1.5 pr-4 text-ag-text-muted">{formatLabel(k)}</td>
                  <td className="py-1.5 px-3 text-right font-mono">{isVal != null ? formatValue(k, isVal) : "—"}</td>
                  <td className={`py-1.5 px-3 text-right font-mono ${
                    k === "net_profit" && oosVal != null ? (oosVal >= 0 ? "text-ag-green" : "text-ag-red") : ""
                  }`}>
                    {oosVal != null ? formatValue(k, oosVal) : "—"}
                  </td>
                  {hasExpected && (
                    <td className={`py-1.5 px-3 text-right font-mono text-amber-400 ${
                      k === "net_profit" && expVal != null ? (expVal >= 0 ? "text-ag-green" : "text-ag-red") : ""
                    }`}>
                      {showExp && expVal != null ? formatValue(k, expVal) : "—"}
                    </td>
                  )}
                  {hasPhase2 && (
                    <td className={`py-1.5 px-3 text-right font-mono ${
                      k === "net_profit" && p2Val != null ? (p2Val >= 0 ? "text-ag-green" : "text-ag-red") : "text-ag-accent"
                    }`}>
                      {p2Val != null ? formatValue(k, p2Val) : "—"}
                    </td>
                  )}
                  <td className={`py-1.5 pl-3 text-right font-mono ${
                    change != null ? (change >= 0 ? "text-ag-green" : "text-ag-red") : ""
                  }`}>
                    {change != null ? `${change >= 0 ? "+" : ""}${change.toFixed(1)}%` : "—"}
                  </td>
                  {hasPhase2 && (
                    <td className={`py-1.5 pl-3 text-right font-mono ${
                      p2Delta != null ? (p2Delta >= 0 ? "text-ag-green" : "text-ag-red") : ""
                    }`}>
                      {p2Delta != null ? `${p2Delta >= 0 ? "+" : ""}${p2Delta.toFixed(1)}%` : "—"}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* OOS extended KPIs grid */}
      {oosOnlyKeys.some(k => oosMetrics[k] != null) && (
        <div>
          <h4 className="text-[10px] text-ag-text-muted uppercase mb-2">
            OOS Extended Stats
            <HintTip text="Detailed OOS-only metrics aggregated across all windows. Includes per-window averages (avg WR, avg trades), drawdown metrics (cumulative max DD, longest DD), and risk-adjusted ratios (Sharpe, Sortino, Calmar, Recovery). These have no IS equivalent." />
          </h4>
          <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-2 text-xs">
            {oosOnlyKeys.map((k) => {
              const v = oosMetrics[k];
              if (v == null) return null;
              return (
                <div key={k} className="px-2 py-1.5 bg-ag-dark/30 rounded">
                  <div className="text-ag-text-muted text-[10px]">{formatLabel(k)}</div>
                  <div className="font-mono font-medium">{formatValue(k, v)}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Aggregate OOS Equity Curve */}
      {equityCurve && equityCurve.length > 0 && (
        <div>
          <div className="flex items-center gap-1 mb-1">
            <h4 className="text-[10px] text-ag-text-muted uppercase">Aggregate OOS Equity Curve</h4>
            <HintTip text="Cumulative PnL from all OOS windows stitched chronologically. Each window's daily PnL is extracted and chained together. Shows what your account equity would look like if you traded every OOS period back-to-back. Drawdown % shown as red shading." />
          </div>
          <WFOEquityCurve data={equityCurve} title="" />
        </div>
      )}

      {/* Aggregate OOS Monthly Matrix */}
      {monthlyMatrix && monthlyMatrix.years.length > 0 && (
        <div>
          <div className="flex items-center gap-1 mb-1">
            <h4 className="text-[10px] text-ag-text-muted uppercase">Aggregate OOS Monthly PnL</h4>
            <HintTip text="Monthly PnL breakdown from all OOS windows combined. Click any cell to see the individual trades that contributed to that month's PnL. Green = profitable month, red = losing month. Only includes trades from assets that were SELECTED by the optimizer for that window." />
          </div>
          <WFOMonthlyMatrix
            data={monthlyMatrix}
            title=""
            onCellClick={wfoRunId ? (year, month) => setMonthModal({ year, month }) : undefined}
          />
        </div>
      )}

      {/* Month trades modal */}
      {monthModal && wfoRunId && (
        <WFASMonthTradesModal
          wfoRunId={wfoRunId}
          year={monthModal.year}
          month={monthModal.month}
          onClose={() => setMonthModal(null)}
          selectionChanges={phase2SelectionChanges}
          phase2Records={phase2SelectedRecords}
        />
      )}

      {/* Aggregate OOS Asset Breakdown */}
      {breakdown && breakdown.length > 0 && (
        <div>
          <div className="flex items-center gap-1 mb-1">
            <h4 className="text-[10px] text-ag-text-muted uppercase">Aggregate OOS Asset Breakdown</h4>
            <HintTip text="Performance breakdown of all assets selected across all OOS windows. Group by: Group (batch group A/B), TF (timeframe), Side (Long/Short), or Per Asset. 'Batches' = unique batch_ids selected by the optimizer. IS and OOS metrics are aggregated across all windows where each asset appeared." />
          </div>
          <WFOAssetBreakdown
            breakdown={breakdown}
            title=""
          />
        </div>
      )}

      {/* ─── Phase 2 (Hybrid Step 2) aggregate views ─── */}
      {hasPhase2 && (phase2EquityCurve?.length || phase2MonthlyMatrix?.years?.length || phase2Breakdown?.length) && (
        <div className="border-2 border-ag-accent/30 rounded-lg p-3 space-y-3 bg-ag-accent/5">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-bold text-ag-accent">Phase 2 — Hybrid Step 2 Aggregates</h4>
            <button
              onClick={handleSavePhase2}
              disabled={savingPhase2 || !phase2SelectionChanges?.length}
              className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 transition-colors disabled:opacity-50"
            >
              <Save size={12} />
              {savingPhase2 ? "Saving..." : "Save Phase 2 as Portfolio"}
            </button>
          </div>

          {/* Config digest: show exactly what Step 2 used (diagnose stale-preset issues) */}
          {phase2ConfigUsed && (
            <div className="text-[10px] text-ag-text-muted font-mono bg-ag-bg-tertiary rounded px-2 py-1 flex flex-wrap gap-x-3">
              <span>n_trades={phase2ConfigUsed.n_trades}</span>
              <span>n_assets={phase2ConfigUsed.n_assets}</span>
              <span>min_hold={phase2ConfigUsed.min_hold}</span>
              <span>rebalance={phase2ConfigUsed.rebalance_every}</span>
              <span>scoring={phase2ConfigUsed.scoring_method}</span>
              <span>obj={phase2ConfigUsed.objective}</span>
              <span>excl_days={JSON.stringify(phase2ConfigUsed.session_filter_exclude_days)}</span>
              {phase2ConfigUsed.preset_id && <span className="text-ag-accent">preset={phase2ConfigUsed.preset_id}</span>}
            </div>
          )}

          {phase2EquityCurve && phase2EquityCurve.length > 0 && (
            <div>
              <div className="flex items-center gap-1 mb-1">
                <h4 className="text-[10px] text-ag-text-muted uppercase">Phase 2 Equity Curve</h4>
                <HintTip text="Cumulative PnL from Phase 2 (Hybrid Step 2) selected trades only — stitched chronologically across all OOS windows. These are the TRUE tradeable numbers after composite walk-forward re-ranking." />
              </div>
              <WFOEquityCurve data={phase2EquityCurve} title="" />
            </div>
          )}

          {phase2MonthlyMatrix && phase2MonthlyMatrix.years.length > 0 && (
            <div>
              <div className="flex items-center gap-1 mb-1">
                <h4 className="text-[10px] text-ag-text-muted uppercase">Phase 2 Monthly PnL</h4>
                <HintTip text="Monthly PnL from Phase 2 selected trades only. Click any cell to see the trades + asset selection changes for that month." />
              </div>
              <WFOMonthlyMatrix
                data={phase2MonthlyMatrix}
                title=""
                onCellClick={wfoRunId ? (year, month) => setMonthModal({ year, month }) : undefined}
              />
            </div>
          )}

          {phase2Breakdown && phase2Breakdown.length > 0 && (
            <div>
              <div className="flex items-center gap-1 mb-1">
                <h4 className="text-[10px] text-ag-text-muted uppercase">Phase 2 Asset Breakdown</h4>
                <HintTip text="Per-asset performance from Phase 2 selected trades only. Only trades where the asset was a current portfolio member are counted." />
              </div>
              <WFOAssetBreakdown breakdown={phase2Breakdown} title="" />
            </div>
          )}
        </div>
      )}

      {/* Aggregate OOS Full Metrics — hidden when Phase 2 is present (Phase 1 pool numbers
          are not the tradeable truth once hybrid re-ranking is in play) */}
      {!hasPhase2 && Object.keys(oosMetrics).length > 0 && (
        <div>
          <div className="flex items-center gap-1 mb-1">
            <h4 className="text-[10px] text-ag-text-muted uppercase">Aggregate OOS Metrics</h4>
            <HintTip text="Full set of 22+ metrics computed from all OOS trades combined. Includes core metrics (PnL, PF, WR), drawdown metrics (Max DD, Recovery), and professional metrics (Sharpe, Sortino, Calmar). These are the 'real' numbers — computed on data the optimizer never saw during training." />
          </div>
          <WFOMetricsPanel
            metrics={Object.fromEntries(
              Object.entries(oosMetrics).filter((e): e is [string, number] => e[1] != null)
            )}
            title=""
          />
        </div>
      )}
    </div>
  );
}

function TracksView({ tracks, windows, wfoConfig }: { tracks: WFOTrack[]; windows?: WFOWindowResult[]; wfoConfig: Record<string, unknown> }) {
  const [saving, setSaving] = useState<number | null>(null);

  const handleSave = async (track: WFOTrack) => {
    const name = prompt("Portfolio name:", `WFO Track #${track.rank}`);
    if (!name) return;
    setSaving(track.rank);
    try {
      const wfoBatchIds = (wfoConfig.batch_ids as number[]) ?? [];
      const singleBatchId = wfoBatchIds.length === 1 ? wfoBatchIds[0] : undefined;
      const extractBatchId = (s: string): number | undefined => {
        const m = s.match(/~B(-?\d+)/);
        return m ? parseInt(m[1], 10) : undefined;
      };
      const stripTag = (s: string) => s.replace(/~B-?\d+/g, "");
      const assets = track.final_is_assets.map((a) => {
        const taggedBatchId = extractBatchId(a);
        const clean = stripTag(a);
        const side = clean.endsWith("_Long") ? "Long" : clean.endsWith("_Short") ? "Short" : "both";
        const symbol = clean.replace(/_Long$/, "").replace(/_Short$/, "");
        return { symbol, side, batch_id: taggedBatchId ?? singleBatchId };
      });
      // Per-window rotation for this track (walk-forward reconstruction)
      const per_window = track.per_window.map((pw) => ({
        window_num: pw.window_num,
        assets: pw.assets,
        oos_pnl: pw.oos_pnl,
        oos_wfe: pw.oos_wfe,
        oos_h1_pnl: pw.oos_h1_pnl,
        oos_h2_pnl: pw.oos_h2_pnl,
        wfe_h1: pw.wfe_h1,
        degradation_ratio: pw.degradation_ratio,
      }));
      // Window date metadata (stripped down — only what the loader needs)
      const windows_meta = (windows ?? []).map((w) => ({
        window_num: w.window_num,
        is_start: w.is_start,
        is_end: w.is_end,
        oos_start: w.oos_start,
        oos_end: w.oos_end,
        oos_h1_start: w.oos_h1_start,
        oos_h1_end: w.oos_h1_end,
        oos_h2_start: w.oos_h2_start,
        oos_h2_end: w.oos_h2_end,
      }));
      await api.wfoSaveTrack({
        name,
        batch_ids: (wfoConfig.batch_ids as number[]) ?? [],
        track_rank: track.rank,
        assets,
        wfo_settings: wfoConfig,
        metrics_snapshot: track.compiled_metrics as Record<string, number>,
        optimization_end_date: (wfoConfig.last_oos_end as string) ?? undefined,
        per_window,
        windows_meta,
      });
      alert("Track saved as portfolio!");
    } catch { alert("Failed to save track."); }
    setSaving(null);
  };

  return (
    <div className="space-y-3">
      {tracks.map((track) => (
        <div key={track.rank} className="border border-ag-border rounded-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <h5 className="text-sm font-medium">Track #{track.rank}</h5>
            <button onClick={() => handleSave(track)} disabled={saving === track.rank}
              className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 transition-colors disabled:opacity-50">
              <Save size={12} />
              {saving === track.rank ? "Saving..." : "Save as Portfolio"}
            </button>
          </div>
          {/* Compiled metrics */}
          <div className="grid grid-cols-3 sm:grid-cols-5 gap-2 text-xs mb-2">
            {Object.entries(track.compiled_metrics).map(([k, v]) => (
              v != null && (
                <div key={k} className="px-2 py-1 bg-ag-dark/50 rounded">
                  <span className="text-ag-text-muted">{formatLabel(k)}: </span>
                  <span className="font-mono">{formatValue(k, Number(v))}</span>
                </div>
              )
            ))}
          </div>
          {/* Final IS assets */}
          <div className="flex flex-wrap gap-1">
            {track.final_is_assets.map((a) => {
              const clean = a.replace(/~B-?\d+/g, "");
              const isLong = clean.endsWith("_Long");
              const isShort = clean.endsWith("_Short");
              const color = isLong ? "bg-ag-green/15 text-ag-green"
                : isShort ? "bg-ag-red/15 text-ag-red"
                : "bg-ag-accent/15 text-ag-accent";
              return (
                <span key={a} className={`px-2 py-0.5 ${color} rounded text-[10px]`}>
                  {clean.replace("USDT_Long", "").replace("USDT_Short", "").replace("USDT", "")}
                  {(isLong || isShort) && ` (${clean.split("_").pop()?.[0]})`}
                </span>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function formatLabel(key: string): string {
  const map: Record<string, string> = {
    net_profit: "Net Profit",
    profit_factor: "PF",
    max_drawdown: "Max DD",
    cumulative_max_dd: "Cumul. Max DD",
    win_rate: "Win Rate",
    total_trades: "Positions",
    avg_trade: "Avg Trade",
    avg_window_pnl: "Avg Window PnL",
    avg_wr_per_window: "Avg WR / Window",
    avg_trades_per_window: "Avg Trades / Win",
    window_wr: "Window WR",
    windows_profitable: "Win. Profitable",
    total_windows_evaluated: "Windows Eval'd",
    sortino: "Sortino",
    sharpe: "Sharpe",
    calmar: "Calmar",
    recovery_factor: "Recovery Factor",
    return_dd_ratio: "Return/DD",
    best_window_pnl: "Best Window",
    worst_window_pnl: "Worst Window",
    avg_wfe: "Avg WFE",
    total_oos_pnl: "OOS PnL",
    avg_oos_wfe: "Avg WFE",
    // Rich cumulative KPIs
    longest_dd_windows: "Longest DD (windows)",
    max_longest_dd_days: "Max DD Duration (days)",
    // Averaged per-window KPIs
    avg_stability: "Avg Stability R²",
    avg_expectancy: "Avg Expectancy",
    avg_payoff: "Avg Payoff Ratio",
    avg_avg_win: "Avg Win Day",
    avg_avg_loss: "Avg Loss Day",
    avg_recovery: "Avg Recovery",
    avg_sortino: "Avg Sortino",
    avg_sharpe: "Avg Sharpe",
    avg_calmar: "Avg Calmar",
    avg_longest_dd_days: "Avg DD Duration (days)",
    // Per-window rich metrics
    stability: "Stability R²",
    expectancy: "Expectancy",
    payoff_ratio: "Payoff Ratio",
    longest_dd_days: "DD Duration (days)",
    avg_win: "Avg Win Day",
    avg_loss: "Avg Loss Day",
    n_wins: "Wins",
    n_losses: "Losses",
  };
  return map[key] ?? key.replace(/_/g, " ");
}

function formatValue(key: string, value: number): string {
  // Ratios displayed as plain numbers (2 decimals)
  const ratioKeys = [
    "sortino", "sharpe", "calmar", "recovery_factor", "return_dd_ratio",
    "avg_sortino", "avg_sharpe", "avg_calmar", "avg_recovery",
    "avg_payoff", "payoff_ratio",
  ];
  if (ratioKeys.includes(key)) return value.toFixed(2);
  // Stability as percentage
  if (key.includes("stability")) return fmtPct(value);
  // Rates and WR as percentages
  if (key.includes("rate") || key.includes("wfe") || key === "window_wr" || key === "avg_wr_per_window") return fmtPct(value);
  // Day/window counts
  if (key.includes("longest_dd_days") || key === "longest_dd_windows") return `${Math.round(value)}`;
  // Counts
  if (key.includes("trades") || key.includes("count") || key === "windows_profitable" || key === "total_windows_evaluated" || key === "n_wins" || key === "n_losses") return fmtNumber(Math.round(value));
  // Per-window avg trades (1 decimal)
  if (key === "avg_trades_per_window") return value.toFixed(1);
  // Profit factor
  if (key.includes("factor")) return value.toFixed(2);
  // Everything else is dollar
  return fmtDollar(value);
}

const MODE_LABELS: Record<string, string> = {
  fixed: "Fixed $", pct: "% of Equity", martingale: "Martingale",
  anti_martingale: "Anti-Martingale", kelly: "Kelly Criterion",
};

function RiskProfileBanner({ diag }: { diag: NonNullable<WFOResult["risk_profile_diagnostic"]> }) {
  const modeLabel = MODE_LABELS[diag.mode] ?? diag.mode;
  const worked = diag.scaling_worked;
  const borderColor = worked ? "border-ag-accent/40" : "border-amber-500/40";
  const bgColor = worked ? "bg-ag-accent/5" : "bg-amber-500/5";

  return (
    <div className={`${bgColor} border ${borderColor} rounded-lg px-4 py-3 space-y-2`}>
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-ag-accent">Position Sizing Applied</span>
        <span className="text-xs text-ag-text-muted">
          {modeLabel}
          {diag.mode === "fixed" && ` — ${fmtDollar(diag.value)}`}
          {diag.mode === "pct" && ` — ${diag.value}% of ${fmtDollar(diag.initial_capital)}`}
          {(diag.mode === "martingale" || diag.mode === "anti_martingale") && ` — ${fmtDollar(diag.value)}`}
          {diag.mode === "kelly" && ` — starting ${fmtDollar(diag.initial_capital)}`}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-3 text-xs">
        <div>
          <span className="text-ag-text-muted">Before: </span>
          <span className="font-mono">{fmtDollar(diag.pre_total_pnl)}</span>
        </div>
        <div>
          <span className="text-ag-text-muted">After: </span>
          <span className="font-mono">{fmtDollar(diag.post_total_pnl)}</span>
        </div>
        <div>
          <span className="text-ag-text-muted">Change: </span>
          <span className={`font-mono ${diag.pnl_change_pct >= 0 ? "text-ag-green" : "text-ag-red"}`}>
            {diag.pnl_change_pct >= 0 ? "+" : ""}{diag.pnl_change_pct}%
          </span>
        </div>
      </div>
      {diag.note && (
        <p className="text-[10px] text-ag-text-muted leading-tight mt-1">{diag.note}</p>
      )}
      {!worked && (
        <p className="text-[10px] text-amber-400 font-medium">
          Scaling had no effect on total PnL. Check that risk value is different from batch risk.
        </p>
      )}
    </div>
  );
}

function PnlScalingBanner({ diag }: { diag: NonNullable<WFOResult["pnl_scaling_diagnostic"]> }) {
  const modeLabels: Record<string, string> = {
    group: "From Group", custom: "Custom", per_batch: "Per Batch", none: "None (raw $100)",
  };
  const mults = diag.batch_multipliers;
  const uniqueMults = [...new Set(Object.values(mults))];
  const allOne = uniqueMults.length === 1 && uniqueMults[0] === 1;
  const borderColor = diag.any_scaling ? "border-ag-accent/40" : "border-amber-500/40";
  const bgColor = diag.any_scaling ? "bg-ag-accent/5" : "bg-amber-500/5";

  return (
    <div className={`${bgColor} border ${borderColor} rounded-lg px-4 py-3 space-y-2`}>
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-ag-accent">PnL Scaling</span>
        <span className="text-xs text-ag-text-muted">
          Mode: {modeLabels[diag.mode] ?? diag.mode}
          {diag.mode === "custom" && diag.custom_value != null && ` — ${diag.custom_value}×`}
        </span>
      </div>
      <div className="text-xs">
        {diag.any_scaling ? (
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {uniqueMults.sort((a, b) => b - a).map((m) => {
              const bids = Object.entries(mults).filter(([, v]) => v === m).map(([k]) => k);
              return (
                <span key={m} className="text-ag-text-muted">
                  <span className="font-mono font-medium text-ag-text">{m}×</span>
                  {" "}→ {bids.length} batch{bids.length !== 1 ? "es" : ""}
                </span>
              );
            })}
          </div>
        ) : (
          <p className="text-amber-400 font-medium">
            ⚠ No scaling applied — all batch multipliers are 1.0.
            {diag.mode === "group" && " Check that your batch groups have pnl_multiplier set (Batch Groups settings)."}
          </p>
        )}
      </div>
      {allOne && diag.mode === "group" && (
        <p className="text-[10px] text-ag-text-muted">
          Batch IDs checked: {Object.keys(mults).slice(0, 10).join(", ")}{Object.keys(mults).length > 10 ? ` (+${Object.keys(mults).length - 10} more)` : ""}
        </p>
      )}
    </div>
  );
}

function WindowDiagnosticBanner({ diag }: { diag: NonNullable<WFOResult["window_diagnostic"]> }) {
  return (
    <div className="bg-ag-panel/50 border border-ag-border/50 rounded-lg px-4 py-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-ag-accent">Window Diagnostic</span>
        <span className="text-xs text-ag-text-muted">
          {diag.oos_windows} OOS + {diag.total_windows - diag.oos_windows} IS-only = {diag.total_windows} total
        </span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 text-xs">
        <div>
          <span className="text-ag-text-muted">Data Start: </span>
          <span className="font-mono text-ag-text">{diag.data_start}</span>
        </div>
        <div>
          <span className="text-ag-text-muted">Last OOS End: </span>
          <span className="font-mono text-ag-text">{diag.last_oos_end}</span>
        </div>
        <div>
          <span className="text-ag-text-muted">Earliest Trade: </span>
          <span className="font-mono text-ag-text">{diag.earliest_trade ?? "—"}</span>
        </div>
        <div>
          <span className="text-ag-text-muted">Latest Trade: </span>
          <span className="font-mono text-ag-text">{diag.latest_trade ?? "—"}</span>
        </div>
      </div>
      {diag.first_window && diag.last_oos_window && (
        <div className="grid grid-cols-2 gap-x-4 text-xs">
          <div>
            <span className="text-ag-text-muted">First Window: </span>
            <span className="font-mono text-ag-text">
              IS {diag.first_window.is_start} → {diag.first_window.is_end} | OOS → {diag.first_window.oos_end}
            </span>
          </div>
          <div>
            <span className="text-ag-text-muted">Last OOS Window: </span>
            <span className="font-mono text-ag-text">
              IS {diag.last_oos_window.is_start} → {diag.last_oos_window.is_end} | OOS → {diag.last_oos_window.oos_end}
            </span>
          </div>
        </div>
      )}
      <div className="text-[10px] text-ag-text-muted">
        {diag.total_trades_loaded.toLocaleString()} trades loaded from {diag.batch_ids_used.length} batches
        {diag.date_from_param && <> | date_from: {diag.date_from_param}</>}
      </div>
    </div>
  );
}
