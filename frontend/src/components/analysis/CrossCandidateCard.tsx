"use client";
import React, { useState, useEffect, useRef } from "react";
import { api } from "@/lib/api";
import type { CrossStrategyCandidate, OOSCompareResult } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import { ClippedTradesMessage } from "@/components/analysis/ClippedTradesMessage";

// Inline ErrorBoundary — prevents OOS render crash from killing the page
class OOSErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; message: string }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, message: "" };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, message: error.message || "Render error" };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="mt-3 pt-3 border-t border-ag-border">
          <p className="text-xs text-ag-red">OOS display error: {this.state.message}</p>
          <button onClick={() => this.setState({ hasError: false, message: "" })}
            className="text-xs text-ag-accent underline mt-1">Retry</button>
        </div>
      );
    }
    return this.props.children;
  }
}

interface ExtraCosts {
  extra_slippage_pct?: number;
  extra_commission_pct?: number;
  funding_daily_pct?: number;
  spread_pct?: number;
}

interface RiskProfile {
  mode: string;
  value: number;
  initialCapital?: number;
  multiplier?: number;
  maxStreak?: number;
  compound?: string;
  scope?: string;
  countMode?: string;
  oosReset?: boolean;
  startAfter?: number;
  resetAfter?: number;
  kellyN?: number;
  kellyWarmup?: number;
  kellyScope?: string;
  kellyLookback?: number;
  kellyCap?: number;
  autoAssetConfigs?: string;
}

export interface OosSummary {
  oosProfit: number;
  grade: string | null;
  gradeScore: number;
  pf: number;
  wfe: number;
  stability: number;
  sortino: number;
  winRate: number;
  maxDd: number;
  trades: number;
  returnDd: number;
}

interface PerBatchConfig {
  sizingMaster: string;
  batchConfigs: string;
}

interface Props {
  index: number;
  candidate: CrossStrategyCandidate;
  saving: boolean;
  onSave: () => void;
  saved?: boolean;
  savedPortfolioId?: number;
  onViewPortfolios?: () => void;
  dateTo?: string;
  batchIds?: number[];
  clipIS?: boolean;
  clipOOS?: boolean;
  oosEndDate?: string;
  extraCosts?: ExtraCosts;
  riskProfile?: RiskProfile;
  perBatchConfig?: PerBatchConfig;
  onOosLoaded?: (summary: OosSummary) => void;
  /** Per-asset auto-risk mode map: symbol → mode string */
  autoRiskModes?: Record<string, string>;
}

// Safe number formatter — never throws on null/undefined/NaN
const safeNum = (v: unknown, fallback = 0): number => {
  if (v == null || typeof v !== "number" || !isFinite(v)) return fallback;
  return v;
};

export default function CrossCandidateCard({ index, candidate, saving, onSave, saved, savedPortfolioId, onViewPortfolios, dateTo, batchIds, clipIS, clipOOS, oosEndDate, extraCosts, riskProfile, perBatchConfig, onOosLoaded, autoRiskModes }: Props) {
  const [oos, setOos] = useState<OOSCompareResult | null>(null);
  const [oosLoading, setOosLoading] = useState(false);
  const [oosError, setOosError] = useState("");
  const raw = candidate.metrics ?? {};
  const [localM, setLocalM] = useState({
    net_profit: safeNum(raw.net_profit),
    profit_factor: safeNum(raw.profit_factor),
    max_drawdown: safeNum(raw.max_drawdown),
    stability: safeNum(raw.stability),
  });
  const m = localM;

  // Strip batch tags for display: BTCUSDT~B5_Short → BTCUSDT_Short
  const stripTag = (s: string) => s.replace(/~B-?\d+/g, "");

  // Group assets by strategy — use tagged keys for attribution lookup, display clean names
  const byStrategy: Record<string, { tagged: string; display: string }[]> = {};
  const assets = candidate.assets ?? [];
  const attribution = candidate.attribution ?? {};
  for (const a of assets) {
    const strat = attribution[a] || "Unknown";
    if (!byStrategy[strat]) byStrategy[strat] = [];
    byStrategy[strat].push({ tagged: a, display: stripTag(a) });
  }

  const revealOOS = async () => {
    if (!dateTo || !batchIds?.length) return;
    setOosLoading(true);
    setOosError("");
    try {
      // Saved portfolios use the portfolio endpoint (correct loader, stored costs, no dual-batch bug)
      if (savedPortfolioId) {
        const pResult = await api.portfolioOOSDate(savedPortfolioId, dateTo, clipIS, clipOOS, oosEndDate);
        // Map OOSDateResult → OOSCompareResult shape for the OOSReveal component
        // Derive per_batch from per_asset strategy_name for saved portfolios
        const perBatchDerived: OOSCompareResult["per_batch"] = (() => {
          const groups: Record<string, typeof pResult.per_asset> = {};
          for (const a of pResult.per_asset) {
            const name = a.strategy_name || "Unknown";
            if (!groups[name]) groups[name] = [];
            groups[name].push(a);
          }
          const entries = Object.entries(groups);
          if (entries.length < 2) return undefined;
          return entries.map(([name, assets], i) => {
            const oosGP = assets.reduce((s, a) => s + (a.OOS_GP ?? 0), 0);
            const oosGL = assets.reduce((s, a) => s + Math.abs(a.OOS_GL ?? 0), 0);
            const totOos = assets.reduce((s, a) => s + a.OOS_Trades, 0);
            const wVals = assets.filter(a => (a.WFE_PF ?? 0) !== 0);
            const wAvg = wVals.length > 0
              ? wVals.reduce((s, a) => s + (a.WFE_PF ?? 0) * Math.max(a.OOS_Trades, 1), 0)
                / wVals.reduce((s, a) => s + Math.max(a.OOS_Trades, 1), 0)
              : 0;
            return {
              batch_id: i,
              batch_name: name,
              assets: assets.length,
              OOS_PnL: assets.reduce((s, a) => s + a.OOS_PnL, 0),
              IS_PnL: assets.reduce((s, a) => s + a.IS_PnL, 0),
              OOS_PF: oosGL > 0 ? Math.min(oosGP / oosGL, 10) : oosGP > 0 ? 10 : 0,
              WFE_PF: wAvg,
              IS_Trades: assets.reduce((s, a) => s + a.IS_Trades, 0),
              OOS_Trades: totOos,
            };
          });
        })();
        const mapped: OOSCompareResult = {
          is_metrics: pResult.is_full_metrics ?? pResult.is_metrics ?? {},
          oos_metrics: pResult.oos_full_metrics ?? pResult.oos_metrics ?? {},
          is_trades: pResult.is_trades,
          oos_trades: pResult.oos_trades,
          aggregate_wfe: pResult.wfe,
          aggregate_wfe_pf: pResult.wfe_pf,
          aggregate_is_pf: (pResult.is_metrics?.profit_factor as number) ?? null,
          aggregate_oos_pf: (pResult.oos_metrics?.profit_factor as number) ?? null,
          per_asset: pResult.per_asset,
          per_batch: perBatchDerived,
          cutoff: pResult.cutoff ?? dateTo,
          return_to_dd_ratio: pResult.return_to_dd_ratio,
          portfolio_grade: pResult.portfolio_grade,
          portfolio_grade_score: pResult.portfolio_grade_score,
          clipped_trades: pResult.clipped_trades,
          // Map quick metrics from the oos_metrics (already computed by compute_portfolio_metrics_from_trades)
          oos_quick_metrics: pResult.oos_metrics as Record<string, number> | undefined,
          is_quick_metrics: pResult.is_metrics as Record<string, number> | undefined,
        };
        setOos(mapped);
        // Update IS metrics with risk-scaled values from portfolio OOS
        const isQm = pResult.is_metrics as Record<string, number> | undefined;
        if (isQm) {
          setLocalM((prev) => ({
            net_profit: isQm.net_profit ?? prev.net_profit,
            profit_factor: isQm.profit_factor ?? prev.profit_factor,
            max_drawdown: isQm.max_drawdown ?? prev.max_drawdown,
            stability: isQm.stability ?? prev.stability,
          }));
        }
        if (onOosLoaded) {
          const qm = pResult.oos_metrics as Record<string, number> | undefined;
          onOosLoaded({
            oosProfit: safeNum(qm?.net_profit),
            grade: pResult.portfolio_grade || null,
            gradeScore: pResult.portfolio_grade_score ?? 0,
            pf: safeNum(qm?.profit_factor ?? pResult.oos_metrics?.["Profit Factor"]),
            wfe: safeNum(pResult.wfe),
            stability: safeNum(qm?.stability ?? pResult.oos_metrics?.["Stability (R²)"]),
            sortino: safeNum(qm?.sortino ?? pResult.oos_metrics?.["Sortino Ratio"]),
            winRate: safeNum(qm?.win_rate ?? pResult.oos_metrics?.["Win Rate"]),
            maxDd: safeNum(qm?.max_drawdown ?? pResult.oos_metrics?.["Max Drawdown ($)"]),
            trades: pResult.oos_trades ?? 0,
            returnDd: safeNum(pResult.return_to_dd_ratio),
          });
        }
      } else {
        // Unsaved candidates use oos-compare (preview mode)
        const hasCosts = extraCosts && (
          (extraCosts.extra_slippage_pct ?? 0) > 0 || (extraCosts.extra_commission_pct ?? 0) > 0 ||
          (extraCosts.funding_daily_pct ?? 0) > 0 || (extraCosts.spread_pct ?? 0) > 0
        );
        const hasRisk = riskProfile?.mode && (riskProfile.value > 0 || riskProfile.autoAssetConfigs);
        const result = await api.dashboardOOSCompare(
          dateTo, undefined, undefined, undefined, candidate.assets ?? [], undefined, batchIds, undefined, clipIS, clipOOS, oosEndDate,
          hasCosts ? extraCosts : undefined,
          hasRisk ? riskProfile : undefined,
          perBatchConfig,
        );
        setOos(result);
        // Update IS metrics with risk-scaled values from OOS comparison
        const qm = result.is_quick_metrics as Record<string, number> | undefined;
        const fm = result.is_metrics as Record<string, number | string | null> | undefined;
        if (qm || fm) {
          const n = (key: string, alt: string) => {
            const v = qm?.[key] ?? fm?.[alt];
            return typeof v === "number" ? v : undefined;
          };
          setLocalM((prev) => ({
            net_profit: n("net_profit", "Total Net Profit") ?? prev.net_profit,
            profit_factor: n("profit_factor", "Profit Factor") ?? prev.profit_factor,
            max_drawdown: n("max_drawdown", "Max Drawdown ($)") ?? prev.max_drawdown,
            stability: n("stability", "Stability (R²)") ?? prev.stability,
          }));
        }
        if (onOosLoaded) {
          const oosQm = result.oos_quick_metrics as Record<string, number> | undefined;
          const oosM = result.oos_metrics as Record<string, number | string | null> | undefined;
          onOosLoaded({
            oosProfit: safeNum(oosQm?.net_profit ?? oosM?.["Total Net Profit"]),
            grade: result.portfolio_grade || null,
            gradeScore: result.portfolio_grade_score ?? 0,
            pf: safeNum(oosQm?.profit_factor ?? result.aggregate_oos_pf),
            wfe: safeNum(result.aggregate_wfe),
            stability: safeNum(oosQm?.stability ?? oosM?.["Stability (R²)"]),
            sortino: safeNum(oosQm?.sortino ?? oosM?.["Sortino Ratio"]),
            winRate: safeNum(oosQm?.win_rate ?? oosM?.["Win Rate"]),
            maxDd: safeNum(oosQm?.max_drawdown ?? oosM?.["Max Drawdown ($)"]),
            trades: result.oos_trades ?? 0,
            returnDd: safeNum(result.return_to_dd_ratio),
          });
        }
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "OOS analysis failed";
      console.error("Cross-strat Reveal OOS error:", e);
      setOosError(msg);
    }
    setOosLoading(false);
  };

  // Auto-reveal OOS on mount when cutoff date is available
  const didAutoReveal = useRef(false);
  useEffect(() => {
    if (!didAutoReveal.current && dateTo && batchIds?.length && !oos && !oosLoading) {
      didAutoReveal.current = true;
      revealOOS();
    }
  }, [dateTo, batchIds]);

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h4 className="text-sm font-medium">Portfolio #{index + 1}</h4>
          <p className="text-xs text-ag-text-muted">
            {assets.length} assets ({(candidate.longs ?? []).length}L / {(candidate.shorts ?? []).length}S)
            · {Object.keys(byStrategy).length} strategies
          </p>
        </div>
        <div className="flex gap-2 items-center">
          {oosLoading && <span className="text-xs text-yellow-400 animate-pulse">Loading OOS...</span>}
          {saved ? (
            <>
              <span className="px-3 py-1 bg-ag-green/15 text-ag-green rounded text-xs">Saved</span>
              {onViewPortfolios && (
                <button onClick={onViewPortfolios}
                  className="px-3 py-1 bg-ag-accent/20 text-ag-accent rounded text-xs hover:bg-ag-accent/30">
                  Analyze in Portfolios &rarr;
                </button>
              )}
            </>
          ) : (
            <button onClick={onSave} disabled={saving}
              className="px-3 py-1 bg-ag-green/20 text-ag-green rounded text-xs hover:bg-ag-green/30 disabled:opacity-50">
              {saving ? "Saving..." : "Save"}
            </button>
          )}
        </div>
      </div>

      {/* Assets grouped by strategy */}
      <div className="space-y-2 mb-3">
        {Object.entries(byStrategy).map(([strat, items]) => (
          <div key={strat}>
            <span className="text-[10px] text-ag-text-muted uppercase tracking-wider">{strat}</span>
            <div className="flex flex-wrap gap-1 mt-0.5">
              {items.map(({ tagged, display }) => {
                const isLong = display.endsWith("_Long");
                // Look up auto-risk mode — strip direction + USDT suffix + batch tag
                const rawSym = display.replace(/_Long$/, "").replace(/_Short$/, "");
                const rMode = autoRiskModes?.[rawSym] ?? autoRiskModes?.[tagged];
                const rmBadge = rMode === "anti_martingale" ? { l: "AM", c: "bg-ag-green/20 text-ag-green" }
                  : rMode === "martingale" ? { l: "M", c: "bg-orange-500/20 text-orange-400" }
                  : rMode === "fixed" ? { l: "F", c: "bg-ag-text-muted/20 text-ag-text-muted" }
                  : rMode ? { l: rMode.substring(0, 2).toUpperCase(), c: "bg-ag-text-muted/20 text-ag-text-muted" }
                  : null;
                return (
                  <span key={tagged} className={`inline-flex items-center gap-0.5 px-2 py-0.5 rounded text-xs ${
                    isLong ? "bg-ag-green/15 text-ag-green" : "bg-ag-red/15 text-ag-red"
                  }`} title={rMode ? `Risk: ${rMode}` : undefined}>
                    {display.replace("USDT_Long", "").replace("USDT_Short", "").replace("USDT", "")}
                    {isLong ? " L" : " S"}
                    {rmBadge && <span className={`ml-0.5 px-1 rounded text-[9px] font-semibold ${rmBadge.c}`}>{rmBadge.l}</span>}
                  </span>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Metrics — 2 rows of 4 */}
      <div className="space-y-1.5">
        <div className="grid grid-cols-4 gap-2 text-xs">
          <div>
            <span className="text-ag-text-muted">{oos ? "IS " : ""}Net Profit</span>
            <p className={m.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}>
              ${m.net_profit.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </p>
          </div>
          <div>
            <span className="text-ag-text-muted">{oos ? "IS " : ""}Profit Factor</span>
            <p>{m.profit_factor.toFixed(2)}</p>
          </div>
          <div>
            <span className="text-ag-text-muted">{oos ? "IS " : ""}Max DD</span>
            <p className="text-ag-red">${m.max_drawdown.toFixed(2)}</p>
          </div>
          <div>
            <span className="text-ag-text-muted">{oos ? "IS " : ""}Stability</span>
            <p>{(m.stability * 100).toFixed(1)}%</p>
          </div>
        </div>
      </div>

      {/* Blueprint: per-asset marginal contribution */}
      {candidate.blueprint && candidate.blueprint.length > 0 && (
        <details className="text-xs mt-2">
          <summary className="cursor-pointer text-ag-text-muted hover:text-ag-text">
            Blueprint ({candidate.blueprint.length} assets)
          </summary>
          <div className="mt-1 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-ag-text-muted border-b border-ag-border">
                  <th className="text-left py-1 pr-2">Asset</th>
                  <th className="text-right py-1 px-1">PF &Delta;</th>
                  <th className="text-right py-1 px-1">Stab &Delta;</th>
                  <th className="text-right py-1 px-1">Sortino &Delta;</th>
                  <th className="text-right py-1 px-1">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {candidate.blueprint.map((b: Record<string, unknown>) => {
                  const asset = String(b.asset ?? "");
                  const pfD = Number(b.pf_delta ?? 0);
                  const stabD = Number(b.stability_delta ?? 0);
                  const sortD = Number(b.sortino_delta ?? 0);
                  const verdict = String(b.verdict ?? "neutral");
                  const vColor = verdict === "helpful" ? "text-ag-green" : verdict === "harmful" ? "text-ag-red" : "text-ag-text-muted";
                  const dColor = (v: number) => v > 0.01 ? "text-ag-green" : v < -0.01 ? "text-ag-red" : "text-ag-text-muted";
                  return (
                    <tr key={asset} className="border-b border-ag-border/30">
                      <td className="py-0.5 pr-2">{stripTag(asset).replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")}</td>
                      <td className={`text-right py-0.5 px-1 ${dColor(pfD)}`}>{pfD > 0 ? "+" : ""}{pfD.toFixed(3)}</td>
                      <td className={`text-right py-0.5 px-1 ${dColor(stabD)}`}>{stabD > 0 ? "+" : ""}{(stabD * 100).toFixed(1)}%</td>
                      <td className={`text-right py-0.5 px-1 ${dColor(sortD)}`}>{sortD > 0 ? "+" : ""}{sortD.toFixed(2)}</td>
                      <td className={`text-right py-0.5 px-1 ${vColor} capitalize`}>{verdict}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {/* OOS Reveal Section — wrapped in ErrorBoundary to prevent page crash */}
      {oos && (
        <OOSErrorBoundary>
          <OOSReveal oos={oos} />
        </OOSErrorBoundary>
      )}
      {oosError && <p className="text-xs text-ag-red mt-2">{oosError}</p>}
    </div>
  );
}

function wfeColor(wfe: number): string {
  if (wfe >= 0.8) return "text-ag-green";
  if (wfe >= 0.5) return "text-yellow-400";
  return "text-ag-red";
}

function stabilityColor(v: number): string {
  if (v >= 0.85) return "text-ag-green";
  if (v >= 0.70) return "text-yellow-400";
  return "text-ag-red";
}

function returnDdColor(v: number): string {
  if (v >= 3.0) return "text-ag-green";
  if (v >= 1.5) return "text-yellow-400";
  return "text-ag-red";
}

function sortinoColor(v: number): string {
  if (v >= 2.0) return "text-ag-green";
  if (v >= 1.0) return "text-yellow-400";
  return "text-ag-red";
}

function ddDurationColor(days: number): string {
  if (days < 30) return "text-ag-green";
  if (days < 60) return "text-yellow-400";
  return "text-ag-red";
}

function gradeColor(grade: string): string {
  if (grade.startsWith("A")) return "bg-ag-green/20 text-ag-green border-ag-green/30";
  if (grade.startsWith("B")) return "bg-lime-500/20 text-lime-400 border-lime-500/30";
  if (grade.startsWith("C")) return "bg-yellow-500/20 text-yellow-400 border-yellow-500/30";
  if (grade.startsWith("D")) return "bg-orange-500/20 text-orange-400 border-orange-500/30";
  return "bg-ag-red/20 text-ag-red border-ag-red/30";
}

/** Parse "X days HH:MM:SS" or similar timedelta string into approximate days */
function parseDdDurationDays(raw: unknown): number | null {
  if (raw == null) return null;
  const s = String(raw);
  const dayMatch = s.match(/(\d+)\s*day/);
  if (dayMatch) return parseInt(dayMatch[1], 10);
  // If it's just "HH:MM:SS" with no "day", it's < 1 day
  if (/^\d{1,2}:\d{2}:\d{2}/.test(s)) return 0;
  return null;
}

function OOSReveal({ oos }: { oos: OOSCompareResult }) {
  const oosMetrics = oos.oos_metrics ?? {};
  // Prefer unified quick metrics (daily-PnL DD, position-merged — same as portfolio tabs)
  const quick = oos.oos_quick_metrics as Record<string, number> | undefined;
  const oosPnl = quick?.net_profit ?? (typeof oosMetrics["Total Net Profit"] === "number" ? oosMetrics["Total Net Profit"] : 0);
  const oosPf = quick?.profit_factor ?? oos.aggregate_oos_pf;
  const oosWr = quick?.win_rate ?? (typeof oosMetrics["Win Rate"] === "number" ? oosMetrics["Win Rate"] : null);
  const oosDd = quick?.max_drawdown ?? (typeof oosMetrics["Max Drawdown ($)"] === "number" ? oosMetrics["Max Drawdown ($)"] : null);
  const oosTradeCount = quick?.total_trades ?? oos.oos_trades;
  const dateRange = oos.trade_date_range;

  if (!oosTradeCount) {
    return (
      <div className="mt-3 pt-3 border-t border-ag-border space-y-1">
        <p className="text-xs font-medium text-yellow-400">OOS Results</p>
        <p className="text-xs text-ag-text-muted">
          0 trades after cutoff {oos.cutoff}.
          {dateRange && dateRange.to && (
            <> Data goes from <strong>{dateRange.from}</strong> to <strong>{dateRange.to}</strong>.
            {dateRange.to <= (oos.cutoff || "") && (
              <span className="text-ag-red"> Batches have NO data after the cutoff. Re-run with data beyond OOS date.</span>
            )}
            </>
          )}
        </p>
        <p className="text-[10px] text-ag-text-muted">IS trades: {oos.is_trades}. Batch must contain trades AFTER the OOS cutoff.</p>
      </div>
    );
  }

  // Robustness metrics — prefer quick metrics (same formula as portfolio tabs)
  const oosStability = quick?.stability ?? (typeof oosMetrics["Stability (R\u00B2)"] === "number" ? oosMetrics["Stability (R\u00B2)"] as number : null);
  const oosSortino = quick?.sortino ?? (typeof oosMetrics["Sortino Ratio"] === "number" ? oosMetrics["Sortino Ratio"] as number : null);
  const oosMaxDdDur = parseDdDurationDays(oosMetrics["Max DD Duration"]);
  const returnDd = safeNum(oos.return_to_dd_ratio);
  const grade = oos.portfolio_grade || null;
  const stratType: string = (oos as unknown as Record<string, unknown>).strategy_type as string || "mixed";
  const stratLabel = stratType === "trend_following" ? "Trend Following"
    : stratType === "mean_reversion" ? "Mean Reversion" : "Mixed";

  // Build grade tooltip with strategy-aware weights
  const gw = stratType === "trend_following"
    ? { stab: 10, wfe: 25, rdd: 20, sort: 30, pf: 15 }
    : stratType === "mean_reversion"
    ? { stab: 30, wfe: 25, rdd: 20, sort: 10, pf: 15 }
    : { stab: 25, wfe: 25, rdd: 20, sort: 15, pf: 15 };
  const gradeTooltip = `Portfolio Grade: ${grade} (${stratLabel})\n\nWeighted score from:\n- Stability R\u00B2 (${gw.stab}%)\n- WFE (${gw.wfe}%)\n- Return/MaxDD (${gw.rdd}%)\n- Sortino (${gw.sort}%)\n- Profit Factor (${gw.pf}%)\n\nScore: ${((oos.portfolio_grade_score ?? 0) * 100).toFixed(0)}%`;

  // Max DD as percentage of peak equity
  const oosEquityPeak = oosPnl - (oosDd ?? 0); // peak ~ net + |dd|
  const oosDdPct = (oosDd != null && oosEquityPeak > 0) ? Math.abs(oosDd) / oosEquityPeak * 100 : null;

  return (
    <div className="mt-3 pt-3 border-t border-ag-border space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs font-medium text-yellow-400">OOS Results</span>
        {grade && (
          <span className={`px-2 py-0.5 rounded text-xs font-bold border ${gradeColor(grade)}`}
            title={gradeTooltip}>
            {grade}
          </span>
        )}
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-ag-border text-ag-text-muted"
          title={`Auto-detected from OOS win rate & payoff ratio.\nWeights adjusted: Stability R\u00B2 ${gw.stab}%, Sortino ${gw.sort}%`}>
          {stratLabel}
        </span>
        <span className={`text-sm font-bold ${wfeColor(safeNum(oos.aggregate_wfe_pf ?? oos.aggregate_wfe))}`}
          title="PF-based WFE: (OOS_PF-1)/(IS_PF-1) — edge retention">
          WFE: {(safeNum(oos.aggregate_wfe_pf ?? oos.aggregate_wfe) * 100).toFixed(1)}%
        </span>
        <span className="text-[10px] text-ag-text-muted" title="Time-normalized WFE (daily rate ratio)">
          Rate: {(safeNum(oos.aggregate_wfe) * 100).toFixed(0)}%
        </span>
        <span className="text-xs text-ag-text-muted">
          {oosTradeCount} trades after {oos.cutoff}
          {dateRange && dateRange.to && ` (data to ${dateRange.to})`}
        </span>
      </div>

      {/* Row 1: Core performance metrics */}
      <div className="grid grid-cols-4 gap-2 text-xs">
        <div>
          <span className="text-ag-text-muted">OOS Net Profit</span>
          <p className={oosPnl >= 0 ? "text-ag-green" : "text-ag-red"}>
            ${oosPnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </p>
        </div>
        <div>
          <span className="text-ag-text-muted">OOS Profit Factor</span>
          <p>{oosPf != null ? oosPf.toFixed(2) : "\u2014"}</p>
        </div>
        <div>
          <span className="text-ag-text-muted">
            OOS Max DD
            <HintTip text="Maximum drawdown in dollars and as % of peak equity. Shows the worst peak-to-trough decline." />
          </span>
          <p className="text-ag-red">
            {oosDd != null ? `$${oosDd.toFixed(2)}` : "\u2014"}
            {oosDdPct != null && <span className="text-ag-text-muted ml-1">({oosDdPct.toFixed(1)}%)</span>}
          </p>
        </div>
        <div>
          <span className="text-ag-text-muted">OOS Win Rate</span>
          <p>{oosWr != null ? `${oosWr.toFixed(1)}%` : "\u2014"}</p>
        </div>
      </div>

      {/* Row 2: Robustness metrics */}
      <div className="grid grid-cols-4 gap-2 text-xs">
        <div>
          <span className="text-ag-text-muted">
            Stability R²
            <HintTip text={stratType === "trend_following"
              ? "R-squared of equity curve linear regression. Trend-following strategies naturally produce lumpy (staircase) equity curves due to TSL big wins — lower R² is expected and grade weight is reduced (10% vs 25%)."
              : stratType === "mean_reversion"
              ? "R-squared of equity curve linear regression. Mean-reversion strategies should produce smooth curves — R² is weighted heavily in the grade (30%)."
              : "R-squared of equity curve linear regression. Measures how smooth and consistent the equity growth is. Green >= 0.85, Yellow >= 0.70."} />
          </span>
          <p className={oosStability != null ? stabilityColor(oosStability) : ""}>
            {oosStability != null ? `${(oosStability * 100).toFixed(1)}%` : "\u2014"}
          </p>
        </div>
        <div>
          <span className="text-ag-text-muted">
            Return/MaxDD
            <HintTip text="Net profit divided by max drawdown. How much you earned per unit of pain. Green >= 3.0, Yellow >= 1.5." />
          </span>
          <p className={returnDdColor(returnDd)}>
            {returnDd.toFixed(2)}x
          </p>
        </div>
        <div>
          <span className="text-ag-text-muted">
            Sortino
            <HintTip text="Risk-adjusted return using only downside deviation. Higher = better return per unit of downside risk. Green >= 2.0, Yellow >= 1.0." />
          </span>
          <p className={oosSortino != null ? sortinoColor(oosSortino) : ""}>
            {oosSortino != null ? oosSortino.toFixed(2) : "\u2014"}
          </p>
        </div>
        <div>
          <span className="text-ag-text-muted">
            Longest DD
            <HintTip text="Duration of the longest drawdown period in days. Shows how long the portfolio stayed underwater. Green < 30d, Yellow < 60d." />
          </span>
          <p className={oosMaxDdDur != null ? ddDurationColor(oosMaxDdDur) : ""}>
            {oosMaxDdDur != null ? `${oosMaxDdDur}d` : "\u2014"}
          </p>
        </div>
      </div>

      {/* Per-batch OOS breakdown — uses backend-computed data */}
      {oos.per_batch && oos.per_batch.length >= 2 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-ag-text-muted hover:text-ag-text font-medium">
            Per-batch OOS ({oos.per_batch.length} batches)
          </summary>
          <div className="mt-1.5 space-y-1">
            {oos.per_batch.map((b) => {
              const shortName = b.batch_name.length > 55 ? b.batch_name.slice(0, 52) + "..." : b.batch_name;
              return (
                <div key={b.batch_id} className="bg-ag-dark/50 rounded px-2.5 py-1.5">
                  <div className="flex items-center justify-between gap-2 mb-0.5">
                    <span className="text-ag-text font-medium truncate" title={b.batch_name}>{shortName}</span>
                    <span className="text-ag-text-muted flex-shrink-0">{b.assets} asset{b.assets !== 1 ? "s" : ""}</span>
                  </div>
                  <div className="flex items-center gap-4 text-[10px]">
                    <span>OOS PnL: <span className={b.OOS_PnL >= 0 ? "text-ag-green font-mono" : "text-ag-red font-mono"}>${b.OOS_PnL.toFixed(0)}</span></span>
                    <span>OOS PF: <span className={`font-mono ${b.OOS_PF >= 1.5 ? "text-ag-green" : b.OOS_PF >= 1 ? "text-ag-text" : "text-ag-red"}`}>{b.OOS_PF.toFixed(2)}</span></span>
                    <span>WFE: <span className={wfeColor(b.WFE_PF)}>{(b.WFE_PF * 100).toFixed(0)}%</span></span>
                    <span className="text-ag-text-muted">IS: {b.IS_Trades} / OOS: {b.OOS_Trades} trades</span>
                  </div>
                </div>
              );
            })}
          </div>
        </details>
      )}

      {/* Per-asset WFE mini table */}
      {oos.per_asset && oos.per_asset.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-ag-text-muted hover:text-ag-text">
            Per-asset WFE ({oos.per_asset.length} assets)
          </summary>
          <div className="mt-1 grid grid-cols-2 sm:grid-cols-3 gap-1">
            {oos.per_asset.map((a) => (
              <div key={a.asset} className="flex items-center justify-between px-2 py-0.5 bg-ag-dark/50 rounded">
                <span>{a.asset.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")}</span>
                <span className={wfeColor(safeNum(a.WFE_PF ?? a.WFE))}>{(safeNum(a.WFE_PF ?? a.WFE) * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Clipped trades warning */}
      {oos.clipped_trades && oos.clipped_trades.length > 0 && (
        <ClippedTradesMessage
          clippedTrades={oos.clipped_trades}
          boundaryDate={dateRange?.to ?? null}
        />
      )}
    </div>
  );
}
