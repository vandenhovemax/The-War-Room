"use client";
import { useState } from "react";
import { X, Save } from "lucide-react";
import { api } from "@/lib/api";
import type { WFOWindowResult, WFOWindowCandidate, OosConcentration } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import WFOEquityCurve from "./WFOEquityCurve";
import WFOMonthlyMatrix from "./WFOMonthlyMatrix";
import WFOMetricsPanel from "./WFOMetricsPanel";
import WFASMonthTradesModal from "./WFASMonthTradesModal";
import WFOAssetBreakdown from "./WFOAssetBreakdown";

interface Props {
  window: WFOWindowResult;
  onClose: () => void;
  timeMode?: string;
  isLastOOSWindow?: boolean;
  wfoConfig?: Record<string, unknown>;
  wfoRunId?: number;
}

/* ── Number formatting helpers ─────────────────── */
function fmtDollar(v: number): string {
  const abs = Math.abs(v);
  const str = abs >= 1000
    ? abs.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })
    : abs.toFixed(2);
  return `${v < 0 ? "-" : ""}$${str}`;
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function fmtNumber(v: number): string {
  return Math.round(v).toLocaleString("en-US");
}

/**
 * Concentration badge — surfaces the 25%-of-OOS-cum rule.
 *   🟢 clean (all four gates satisfied, max_trade_pct_of_oos ≤ 25%)
 *   🟡 single-trade between 25-40% or other gate tripped (soft warning)
 *   🔴 single-trade > 40% or multiple gates tripped (outlier-driven config)
 *
 * Hover to see the exact max_trade% and any violation messages.
 */
function ConcentrationBadge({ conc }: { conc: OosConcentration }) {
  const pct = conc.max_trade_pct_of_oos;
  const passes = conc.passes_concentration_gate;
  const severity =
    !passes && pct > 0.4 ? "danger"
    : !passes ? "warn"
    : "ok";
  const tone =
    severity === "danger" ? "bg-ag-red/15 text-ag-red border-ag-red/40"
    : severity === "warn" ? "bg-amber-400/15 text-amber-400 border-amber-400/40"
    : "bg-ag-green/15 text-ag-green border-ag-green/40";
  const icon = severity === "danger" ? "🔴" : severity === "warn" ? "⚠️" : "✓";

  const tip = passes
    ? `Max single trade = ${(pct * 100).toFixed(1)}% of OOS cum ($${Math.round(conc.cum_oos_pnl).toLocaleString()}). All concentration gates pass.`
    : `Concentration gate FAILED:\n${conc.violations.map((v) => `• ${v}`).join("\n")}`;

  return (
    <span
      title={tip}
      className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${tone} cursor-help`}
    >
      {icon} max-trade {(pct * 100).toFixed(0)}%
    </span>
  );
}

export default function WFOWindowDetail({ window: w, onClose, timeMode, isLastOOSWindow, wfoConfig, wfoRunId }: Props) {
  const [saving, setSaving] = useState(false);
  const hasOOS = !!w.oos_start;
  const isLive = !!w.is_live;
  const isBatchSelection = w.candidates?.[0]?.selected_batches != null;
  const isColdStart = isBatchSelection && w.window_num === 1 && !isLive;

  const handleSavePortfolio = async () => {
    const c = w.candidates[0];
    if (!c) return;
    const name = prompt("Portfolio name:", `WFAS — Last Window (${w.oos_end?.slice(0, 10)})`);
    if (!name) return;
    setSaving(true);
    try {
      const wfoBatchIds = (wfoConfig?.batch_ids as number[]) ?? [];
      const extractBatchId = (s: string): number | undefined => {
        const m = s.match(/~B(-?\d+)/);
        return m ? parseInt(m[1], 10) : undefined;
      };
      const stripTag = (s: string) => s.replace(/~B-?\d+/g, "");
      // Build lookup from asset_details for timeframe/group
      const detailMap = new Map<string, { timeframe?: string; group?: string }>();
      if (c.asset_details) {
        for (const ad of c.asset_details) {
          detailMap.set(ad.key, { timeframe: ad.timeframe, group: ad.group });
        }
      }
      const assets = c.assets.map((a) => {
        const taggedBatchId = extractBatchId(a);
        const clean = stripTag(a);
        const side = clean.endsWith("_Long") ? "Long" : clean.endsWith("_Short") ? "Short" : "both";
        const symbol = clean.replace(/_Long$/, "").replace(/_Short$/, "");
        const detail = detailMap.get(a) ?? {};
        return { symbol, side, batch_id: taggedBatchId ?? (wfoBatchIds.length === 1 ? wfoBatchIds[0] : undefined), timeframe: detail.timeframe, group_name: detail.group };
      });
      await api.wfoSaveTrack({
        name,
        batch_ids: wfoBatchIds,
        track_rank: c.rank,
        assets,
        wfo_settings: wfoConfig ?? {},
        metrics_snapshot: c.is_metrics as Record<string, number>,
        optimization_end_date: w.oos_end ?? (wfoConfig?.last_oos_end as string) ?? undefined,
      });
      alert("Saved as portfolio! Go to Portfolios → Live Trading to deploy it.");
    } catch { alert("Failed to save."); }
    setSaving(false);
  };

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-semibold">
            {isLive ? (
              <span className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 bg-ag-green rounded-full animate-pulse" />
                Live Window — Trade Now
              </span>
            ) : (
              `Window ${w.window_num} Detail`
            )}
          </h4>
          {isColdStart && (
            <span className="px-2 py-0.5 bg-amber-500/15 text-amber-400 rounded text-[10px] font-normal">
              Cold Start (IS-only)
            </span>
          )}
          {isLastOOSWindow && hasOOS && !isLive && (
            <span className="px-2 py-0.5 bg-ag-green/15 text-ag-green rounded text-[10px] font-normal">
              Latest Window
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {((isLastOOSWindow && hasOOS) || isLive) && wfoConfig && (
            <button onClick={handleSavePortfolio} disabled={saving}
              className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-green/15 text-ag-green hover:bg-ag-green/25 transition-colors disabled:opacity-50">
              <Save size={12} />
              {saving ? "Saving..." : "Save for Live Trading"}
              <HintTip text="Save this window's selected assets as a portfolio. You can then deploy it in Portfolios → Live Trading." />
            </button>
          )}
          <button onClick={onClose} className="p-1 text-ag-text-muted hover:text-ag-text">
            <X size={16} />
          </button>
        </div>
      </div>

      {/* Date ranges */}
      <div className={`grid gap-3 text-xs ${timeMode === "overlapping" && hasOOS && w.oos_h1_start ? "grid-cols-3" : "grid-cols-2"}`}>
        <div className="px-3 py-2 bg-blue-500/10 rounded-lg">
          <div className="text-blue-400 font-medium mb-1">In-Sample</div>
          <div className="text-ag-text-muted">
            {w.is_start.slice(0, 10)} → {w.is_end.slice(0, 10)}
          </div>
          <div className="text-ag-text-muted mt-0.5">
            {fmtNumber(w.is_trade_count)} trades · {w.is_symbol_count} selected{w.is_pool_size ? ` / ${w.is_pool_size} pool` : ""}
          </div>
        </div>
        {timeMode === "overlapping" && hasOOS && w.oos_h1_start ? (
          <>
            <div className="px-3 py-2 bg-amber-500/10 rounded-lg">
              <div className="text-amber-400 font-medium mb-1">OOS — H1</div>
              <div className="text-ag-text-muted">
                {w.oos_h1_start?.slice(0, 10)} → {w.oos_h1_end?.slice(0, 10)}
              </div>
            </div>
            <div className="px-3 py-2 bg-purple-500/10 rounded-lg">
              <div className="text-purple-400 font-medium mb-1">OOS — H2</div>
              <div className="text-ag-text-muted">
                {w.oos_h2_start?.slice(0, 10)} → {w.oos_h2_end?.slice(0, 10)}
              </div>
              {w.degradation_ratio != null && (
                <div className={`mt-0.5 font-medium ${w.degradation_ratio >= 0.8 ? "text-ag-green" : w.degradation_ratio >= 0.5 ? "text-amber-400" : "text-ag-red"}`}>
                  DR: {(w.degradation_ratio * 100).toFixed(0)}%
                </div>
              )}
            </div>
          </>
        ) : isLive ? (
          <div className="px-3 py-2 bg-ag-green/10 rounded-lg border border-ag-green/20">
            <div className="text-ag-green font-medium mb-1 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-ag-green rounded-full animate-pulse" />
              Live Period
            </div>
            <div className="text-ag-text-muted">
              {w.oos_start?.slice(0, 10)} → present
            </div>
            <div className="text-ag-text-muted mt-0.5 text-[10px]">
              No OOS evaluation — assets selected for live trading
            </div>
          </div>
        ) : hasOOS ? (
          <div className="px-3 py-2 bg-amber-500/10 rounded-lg">
            <div className="text-amber-400 font-medium mb-1">Out-of-Sample</div>
            <div className="text-ag-text-muted">
              {w.oos_start?.slice(0, 10)} → {w.oos_end?.slice(0, 10)}
            </div>
          </div>
        ) : (
          <div className="px-3 py-2 bg-ag-dark/50 rounded-lg">
            <div className="text-ag-text-muted font-medium mb-1">Final IS Window</div>
            <div className="text-ag-text-muted">No OOS — current optimization</div>
          </div>
        )}
      </div>

      {/* Candidates */}
      {w.candidates.map((c) => (
        <CandidateDetail key={c.rank} candidate={c} hasOOS={hasOOS} timeMode={timeMode} wfoRunId={wfoRunId} windowNum={w.window_num} />
      ))}
    </div>
  );
}

type AnalysisTab = "overview" | "is_analysis" | "oos_analysis";

function CandidateDetail({ candidate: c, hasOOS, timeMode, wfoRunId, windowNum }: {
  candidate: WFOWindowCandidate;
  hasOOS: boolean;
  timeMode?: string;
  wfoRunId?: number;
  windowNum?: number;
}) {
  const [analysisTab, setAnalysisTab] = useState<AnalysisTab>("overview");
  const [monthModal, setMonthModal] = useState<{ year: number; month: number } | null>(null);
  const isLive = !!c.is_live;

  // Extended metric set for batch selection
  const isBatchSelection = c.selected_batches != null;
  const compareKeys = isBatchSelection
    ? ["net_profit", "profit_factor", "max_drawdown", "win_rate", "total_trades", "avg_trade",
       "sharpe", "sortino", "recovery_factor", "calmar", "stability", "expectancy", "payoff_ratio",
       "longest_dd_days", "avg_win", "avg_loss"]
    : ["net_profit", "profit_factor", "max_drawdown", "win_rate", "total_trades"];

  // Check if full analysis data is available
  const hasOOSAnalysis = !!(c.oos_equity_curve && c.oos_equity_curve.length > 0);
  const hasISAnalysis = !!(c.is_equity_curve && c.is_equity_curve.length > 0);

  const analysisTabs: { id: AnalysisTab; label: string; show: boolean }[] = [
    { id: "overview", label: "Overview", show: true },
    { id: "is_analysis", label: "IS Analysis", show: hasISAnalysis },
    { id: "oos_analysis", label: isLive ? "Live Assets" : "OOS Analysis", show: hasOOS && hasOOSAnalysis },
  ];

  return (
    <div className="border border-ag-border/50 rounded-lg p-3 space-y-2">
      <div className="flex items-center justify-between">
        <h5 className="text-xs font-medium">
          {isLive ? (
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 bg-ag-green rounded-full animate-pulse" />
              LIVE — Assets to Trade Now
            </span>
          ) : (
            `Rank #${c.rank}`
          )}
        </h5>
        {hasOOS && !isLive && (
          <div className="flex items-center gap-2">
            <span className={`text-xs font-medium ${
              c.oos_wfe >= 0.5 ? "text-ag-green" : c.oos_wfe >= 0.3 ? "text-amber-400" : "text-ag-red"
            }`}>
              WFE: {fmtPct(c.oos_wfe)}
            </span>
            {timeMode === "overlapping" && c.degradation_ratio != null && (
              <span className={`text-xs font-medium ${
                c.degradation_ratio >= 0.8 ? "text-ag-green" : c.degradation_ratio >= 0.5 ? "text-amber-400" : "text-ag-red"
              }`}>
                DR: {(c.degradation_ratio * 100).toFixed(0)}%
              </span>
            )}
            {c.oos_concentration && (
              <ConcentrationBadge conc={c.oos_concentration} />
            )}
          </div>
        )}
      </div>

      {/* Assets */}
      <div className="flex flex-wrap gap-1">
        {c.assets.map((a) => {
          const aIsLong = a.endsWith("_Long");
          const aIsShort = a.endsWith("_Short");
          const color = aIsLong ? "bg-ag-green/15 text-ag-green"
            : aIsShort ? "bg-ag-red/15 text-ag-red"
            : "bg-ag-accent/15 text-ag-accent";
          return (
            <span key={a} className={`px-1.5 py-0.5 ${color} rounded text-[10px]`}>
              {a.replace("USDT_Long", "").replace("USDT_Short", "").replace("USDT", "")}
              {(aIsLong || aIsShort) && ` (${a.split("_").pop()?.[0]})`}
            </span>
          );
        })}
      </div>

      {/* Batch Selection extras */}
      {c.selected_batches && (
        <div className="space-y-1.5 text-[11px]">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-ag-text-muted">
              {c.n_batches} batch{c.n_batches !== 1 ? "es" : ""} · {c.n_assets} asset{c.n_assets !== 1 ? "s" : ""}
              {c.estimated_tpm != null && ` · ~${c.estimated_tpm} TPM`}
            </span>
            {c.selected_batches.length > 0 && (
              <span className="text-ag-text-muted">
                Batches: {c.selected_batches.join(", ")}
              </span>
            )}
          </div>
          {c.calibration_results && Object.keys(c.calibration_results).length > 0 && (
            <div className="flex flex-wrap gap-1">
              {Object.entries(c.calibration_results).map(([bid, cal]) => (
                <span key={bid} className={`px-1.5 py-0.5 rounded text-[10px] ${cal.passed ? "bg-ag-green/15 text-ag-green" : "bg-ag-red/15 text-ag-red"}`}>
                  B{bid}: {cal.passed ? "PASS" : "FAIL"} ({cal.score != null ? cal.score.toFixed(2) : "n/a"})
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Analysis tab bar */}
      {(hasISAnalysis || hasOOSAnalysis) && (
        <div className="flex items-center gap-1 border-b border-ag-border/30 pt-1">
          {analysisTabs.filter(t => t.show).map((t) => (
            <button key={t.id} onClick={() => setAnalysisTab(t.id)}
              className={`px-2.5 py-1 text-[10px] rounded-t transition-colors ${
                analysisTab === t.id
                  ? "bg-ag-accent/15 text-ag-accent font-medium"
                  : "text-ag-text-muted hover:text-ag-text"
              }`}>
              {t.label}
            </button>
          ))}
        </div>
      )}

      {/* Tab: Overview (original IS vs OOS comparison) */}
      {analysisTab === "overview" && (
        <>
          {hasOOS && !isLive ? (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-ag-text-muted border-b border-ag-border/30">
                    <th className="text-left py-1 pr-3">Metric</th>
                    <th className="text-right py-1 px-2">IS</th>
                    <th className="text-right py-1 px-2">OOS</th>
                    <th className="text-right py-1 pl-2">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {compareKeys.map((k) => {
                    const isVal = c.is_metrics[k];
                    const oosVal = c.oos_metrics[k];
                    if (isVal == null && oosVal == null) return null;
                    const change = isVal && oosVal && isVal !== 0
                      ? ((oosVal - isVal) / Math.abs(isVal)) * 100 : null;
                    return (
                      <tr key={k} className="border-b border-ag-border/10">
                        <td className="py-1 pr-3 text-ag-text-muted">{formatLabel(k)}</td>
                        <td className="py-1 px-2 text-right font-mono">
                          {isVal != null ? fmtVal(k, isVal) : "—"}
                        </td>
                        <td className={`py-1 px-2 text-right font-mono ${
                          oosVal != null ? (k === "net_profit" ? (oosVal >= 0 ? "text-ag-green" : "text-ag-red") : "") : ""
                        }`}>
                          {oosVal != null ? fmtVal(k, oosVal) : "—"}
                        </td>
                        <td className={`py-1 pl-2 text-right font-mono ${
                          change != null ? (change >= 0 ? "text-ag-green" : "text-ag-red") : ""
                        }`}>
                          {change != null ? `${change >= 0 ? "+" : ""}${change.toFixed(1)}%` : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-2 text-[11px]">
              {compareKeys.map((k) => {
                const v = c.is_metrics[k];
                if (v == null) return null;
                return (
                  <div key={k} className="px-2 py-1 bg-ag-dark/50 rounded">
                    <span className="text-ag-text-muted">{formatLabel(k)}: </span>
                    <span className="font-mono">{fmtVal(k, v)}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Concurrent trades (batch selection only) */}
          {c.oos_max_concurrent != null && (
            <div className="flex items-center gap-3 mt-2 text-[11px]">
              <span className="text-ag-text-muted">Concurrent Trades:</span>
              <span className="px-1.5 py-0.5 bg-ag-dark/50 rounded text-ag-text font-mono">
                Max {c.oos_max_concurrent}
              </span>
              {c.oos_avg_concurrent != null && (
                <span className="px-1.5 py-0.5 bg-ag-dark/50 rounded text-ag-text font-mono">
                  Avg {c.oos_avg_concurrent.toFixed(1)}
                </span>
              )}
            </div>
          )}

          {/* Consecutive wins/losses */}
          {(c.oos_metrics?.max_consec_wins != null || c.oos_metrics?.max_consec_losses != null) && (
            <div className="flex items-center gap-3 mt-1 text-[11px]">
              <span className="text-ag-text-muted">Consecutive:</span>
              {c.oos_metrics.max_consec_wins != null && (
                <span className="px-1.5 py-0.5 bg-ag-dark/50 rounded font-mono text-ag-green">
                  W {c.oos_metrics.max_consec_wins}
                </span>
              )}
              {c.oos_metrics.max_consec_losses != null && (
                <span className="px-1.5 py-0.5 bg-ag-dark/50 rounded font-mono text-ag-red">
                  L {c.oos_metrics.max_consec_losses}
                </span>
              )}
            </div>
          )}

          {/* H1 vs H2 breakdown (overlapping mode only) */}
          {timeMode === "overlapping" && c.oos_h1_metrics && c.oos_h2_metrics && (
            <div className="border-t border-ag-border/30 pt-2 mt-2">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-[10px] text-ag-text-muted font-medium">H1 → H2 Degradation</span>
                {c.degradation_ratio != null && (
                  <span className={`text-[10px] font-bold ${c.degradation_ratio >= 0.8 ? "text-ag-green" : c.degradation_ratio >= 0.5 ? "text-amber-400" : "text-ag-red"}`}>
                    {(c.degradation_ratio * 100).toFixed(0)}%
                  </span>
                )}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-ag-text-muted border-b border-ag-border/30">
                      <th className="text-left py-1 pr-3">Metric</th>
                      <th className="text-right py-1 px-2 text-amber-400">H1</th>
                      <th className="text-right py-1 px-2 text-purple-400">H2</th>
                    </tr>
                  </thead>
                  <tbody>
                    {compareKeys.map((k) => {
                      const h1 = c.oos_h1_metrics?.[k];
                      const h2 = c.oos_h2_metrics?.[k];
                      if (h1 == null && h2 == null) return null;
                      return (
                        <tr key={k} className="border-b border-ag-border/10">
                          <td className="py-1 pr-3 text-ag-text-muted">{formatLabel(k)}</td>
                          <td className="py-1 px-2 text-right font-mono">{h1 != null ? fmtVal(k, h1) : "—"}</td>
                          <td className="py-1 px-2 text-right font-mono">{h2 != null ? fmtVal(k, h2) : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Live window: asset details table */}
          {isLive && c.asset_details && c.asset_details.length > 0 && (
            <div className="border-t border-ag-border/30 pt-2 mt-2">
              <h6 className="text-[10px] text-ag-text-muted font-medium mb-1.5 uppercase tracking-wider">Assets to Trade</h6>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-ag-text-muted border-b border-ag-border/30">
                      <th className="text-left py-1 pr-2">Symbol</th>
                      <th className="text-left py-1 px-2">Side</th>
                      <th className="text-right py-1 px-2">Batch</th>
                      <th className="text-right py-1 px-2">IS Score</th>
                      <th className="text-right py-1 pl-2">TPM Est</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.asset_details.map((ad, i) => (
                      <tr key={i} className="border-b border-ag-border/10">
                        <td className="py-1 pr-2 font-medium text-ag-text">{ad.symbol}</td>
                        <td className={`py-1 px-2 ${ad.side === "Long" ? "text-ag-green" : "text-ag-red"}`}>
                          {ad.side}
                        </td>
                        <td className="py-1 px-2 text-right text-ag-text-muted">{ad.batch_id}</td>
                        <td className="py-1 px-2 text-right font-mono">{ad.is_score.toFixed(2)}</td>
                        <td className="py-1 pl-2 text-right font-mono">{ad.tpm_est.toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {/* Tab: IS Analysis */}
      {analysisTab === "is_analysis" && hasISAnalysis && (
        <div className="space-y-3">
          <WFOEquityCurve
            data={c.is_equity_curve ?? []}
            title="In-Sample Equity Curve"
          />
          <WFOMonthlyMatrix
            data={c.is_monthly_matrix ?? { years: [], matrix: {}, row_totals: {}, col_totals: {}, grand_total: 0 }}
            title="In-Sample Monthly PnL"
            onCellClick={wfoRunId ? (year, month) => setMonthModal({ year, month }) : undefined}
          />
          <WFOMetricsPanel
            metrics={c.is_metrics as Record<string, number>}
            title="In-Sample Metrics"
          />
          {c.asset_breakdown && c.asset_breakdown.length > 0 && (
            <WFOAssetBreakdown
              breakdown={c.asset_breakdown}
              title="In-Sample Asset Breakdown"
            />
          )}
        </div>
      )}

      {/* Tab: OOS Analysis */}
      {analysisTab === "oos_analysis" && hasOOS && hasOOSAnalysis && (
        <div className="space-y-3">
          <WFOEquityCurve
            data={c.oos_equity_curve ?? []}
            title={isLive ? "Live Period Equity Curve" : "Out-of-Sample Equity Curve"}
          />
          <WFOMonthlyMatrix
            data={c.oos_monthly_matrix ?? { years: [], matrix: {}, row_totals: {}, col_totals: {}, grand_total: 0 }}
            title={isLive ? "Live Period Monthly PnL" : "Out-of-Sample Monthly PnL"}
            onCellClick={wfoRunId ? (year, month) => setMonthModal({ year, month }) : undefined}
          />
          <WFOMetricsPanel
            metrics={c.oos_metrics as Record<string, number>}
            title={isLive ? "Live Period Metrics" : "Out-of-Sample Metrics"}
          />
          {c.asset_breakdown && c.asset_breakdown.length > 0 && (
            <WFOAssetBreakdown
              breakdown={c.asset_breakdown}
              title={isLive ? "Live Asset Breakdown" : "OOS Asset Breakdown"}
            />
          )}
        </div>
      )}

      {/* Month trades modal */}
      {monthModal && wfoRunId && (
        <WFASMonthTradesModal
          wfoRunId={wfoRunId}
          year={monthModal.year}
          month={monthModal.month}
          windowNum={windowNum}
          onClose={() => setMonthModal(null)}
        />
      )}
    </div>
  );
}

function formatLabel(key: string): string {
  const map: Record<string, string> = {
    net_profit: "Net Profit", profit_factor: "PF", max_drawdown: "Max DD",
    win_rate: "Win Rate", total_trades: "Positions", avg_trade: "Avg Trade",
    sharpe: "Sharpe", sortino: "Sortino", calmar: "Calmar",
    recovery_factor: "Recovery", stability: "Stability R²",
    expectancy: "Expectancy", payoff_ratio: "Payoff",
    longest_dd_days: "DD Duration (days)", avg_win: "Avg Win Day", avg_loss: "Avg Loss Day",
    n_wins: "Wins", n_losses: "Losses",
  };
  return map[key] ?? key.replace(/_/g, " ");
}

function fmtVal(key: string, value: number): string {
  const ratioKeys = ["sortino", "sharpe", "calmar", "recovery_factor", "payoff_ratio"];
  if (ratioKeys.includes(key)) return value.toFixed(2);
  if (key === "stability") return fmtPct(value);
  if (key.includes("rate")) return fmtPct(value);
  if (key === "longest_dd_days" || key === "n_wins" || key === "n_losses") return `${Math.round(value)}`;
  if (key.includes("trades") || key.includes("count")) return fmtNumber(value);
  if (key.includes("factor")) return value.toFixed(2);
  return fmtDollar(value);
}
