"use client";
import { useState, useEffect } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import type { OOSDateResult, StrategyGroupResult, WFEAssetResult } from "@/lib/api";
import { FullMetricsRows, EquityCurve } from "./metrics";
import HintTip from "@/components/common/HintTip";
import SynergyAuditPanel from "./SynergyAuditPanel";
import { ClippedTradesMessage } from "./ClippedTradesMessage";
import AssetTradeList from "./AssetTradeList";
import OOSAnalysisTabs from "./OOSAnalysisTabs";
import MonthTradesModal from "./MonthTradesModal";
import FilterableMonthlyPnL from "./FilterableMonthlyPnL";

interface Props {
  portfolioId: number;
  optimizationEndDate?: string | null;
  timeframe?: string;
  strategyId?: number;
}

export default function PortfolioOOSPanel({ portfolioId, optimizationEndDate, timeframe, strategyId }: Props) {
  const [cutoffDate, setCutoffDate] = useState(optimizationEndDate || "");
  const [data, setData] = useState<OOSDateResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [clipIS, setClipIS] = useState(true);
  const [clipOOS, setClipOOS] = useState(true);
  const [oosEndDate, setOosEndDate] = useState("");

  // Auto-populate OOS end date from the response on first load
  useEffect(() => {
    if (data?.oos_end_date && !oosEndDate) {
      setOosEndDate(data.oos_end_date);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const runOOS = async () => {
    setLoading(true);
    setError("");
    try {
      const r = await api.portfolioOOSDate(portfolioId, cutoffDate || undefined, clipIS, clipOOS, oosEndDate || undefined);
      if (!r.cutoff) {
        setError("No OOS cutoff date found. Set one below or save a portfolio with an OOS cutoff date.");
        setData(null);
      } else {
        setData(r);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "OOS analysis failed");
      setData(null);
    }
    setLoading(false);
  };

  useEffect(() => { runOOS(); }, [portfolioId]);

  if (loading) return <p className="text-xs text-ag-text-muted py-2">Computing OOS analysis...</p>;

  return (
    <div className="space-y-4">
      {/* OOS window controls */}
      <div className="flex items-center gap-3 flex-wrap">
        <label className="flex items-center gap-1.5 text-xs">
          <span className="text-ag-text-muted whitespace-nowrap">OOS Start<HintTip text="The IS/OOS boundary date. Trades entering before this date are In-Sample, trades entering after are Out-of-Sample. Defaults to the optimization end date saved with the portfolio." /></span>
          <input type="date" value={cutoffDate}
            onChange={(e) => setCutoffDate(e.target.value)}
            className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
        </label>
        <label className="flex items-center gap-1.5 text-xs">
          <span className="text-ag-text-muted whitespace-nowrap">OOS End<HintTip text="Defines the end of the Out-of-Sample window. OOS runs from OOS Start to this date. Set it earlier than your data end to test a specific OOS section and reserve untouched data beyond. Auto-detected from portfolio settings or batch data end." /></span>
          <input type="date" value={oosEndDate}
            onChange={(e) => setOosEndDate(e.target.value)}
            className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
        </label>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={clipIS}
            onChange={(e) => setClipIS(e.target.checked)}
            className="accent-ag-accent" />
          <span className="text-ag-text-muted">Clip IS<HintTip text="Force-close trades that enter during IS but exit after OOS Start. Their PnL is recalculated at the boundary candle close price. OFF = count full PnL regardless of when the trade closed." /></span>
        </label>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={clipOOS}
            onChange={(e) => setClipOOS(e.target.checked)}
            className="accent-ag-accent" />
          <span className="text-ag-text-muted">Clip OOS<HintTip text="Force-close OOS trades straddling the OOS End Date at the boundary candle close price. OFF = exclude OOS trades that exit after OOS End Date. Either way, the OOS window is bounded by OOS End Date." /></span>
        </label>
        <button onClick={runOOS}
          className="px-2 py-1 bg-ag-accent/20 text-ag-accent rounded text-xs hover:bg-ag-accent/30">
          Recalculate
        </button>
        {optimizationEndDate && cutoffDate !== optimizationEndDate && (
          <span className="text-[10px] text-ag-text-muted">
            Saved cutoff: {optimizationEndDate}
          </span>
        )}
      </div>

      {error && <p className="text-xs text-ag-red">{error}</p>}

      {data && data.cutoff && <OOSResults data={data} portfolioId={portfolioId} timeframe={timeframe} strategyId={strategyId} />}

      {/* OOS Deep Dive — full analysis tabs scoped to OOS period only */}
      {data && data.cutoff && data.oos_trades > 0 && (
        <div className="mt-6 pt-4 border-t border-ag-border">
          <OOSAnalysisTabs portfolioId={portfolioId} cutoffDate={data.cutoff} strategyId={strategyId} />
        </div>
      )}

      {/* Synergy Audit — operates on OOS trades */}
      {data && data.cutoff && data.oos_trades > 0 && (
        <div className="mt-6 pt-4 border-t border-ag-border">
          <SynergyAuditPanel portfolioId={portfolioId} />
        </div>
      )}
    </div>
  );
}

function OOSResults({ data, portfolioId, timeframe, strategyId }: { data: OOSDateResult; portfolioId: number; timeframe?: string; strategyId?: number }) {
  const [monthModal, setMonthModal] = useState<{ year: number; month: number; period: string } | null>(null);
  const wfePf = data.wfe_pf ?? 0;
  const wfeClr = wfePf >= 0.5 ? "text-ag-green" : wfePf >= 0.25 ? "text-yellow-400" : "text-ag-red";
  const wfeTimeClr = data.wfe >= 0.5 ? "text-ag-green" : data.wfe >= 0.25 ? "text-yellow-400" : "text-ag-red";
  const oosPnl = data.oos_metrics.net_profit ?? 0;
  const oosPf = data.oos_metrics.profit_factor;
  const oosWr = data.oos_metrics.win_rate;
  const hasIS = Object.keys(data.is_full_metrics).length > 0;
  const hasOOS = Object.keys(data.oos_full_metrics).length > 0;

  // Robustness metrics from oos_metrics (quick metrics) and oos_full_metrics
  const oosStab = typeof data.oos_metrics.stability === "number" ? data.oos_metrics.stability : null;
  const oosSortino = typeof data.oos_metrics.sortino === "number" ? data.oos_metrics.sortino : null;
  const oosDdDur = parseDdDurationDays(data.oos_full_metrics["Max DD Duration"]);
  const retDd = data.return_to_dd_ratio ?? 0;
  const grade = data.portfolio_grade || null;
  const stratType: string = (data as unknown as Record<string, unknown>).strategy_type as string || "mixed";
  const stratLabel = stratType === "trend_following" ? "Trend Following"
    : stratType === "mean_reversion" ? "Mean Reversion" : "Mixed";

  // Strategy-aware grade weights for tooltip
  const gw = stratType === "trend_following"
    ? { stab: 10, wfe: 25, rdd: 20, sort: 30, pf: 15 }
    : stratType === "mean_reversion"
    ? { stab: 30, wfe: 25, rdd: 20, sort: 10, pf: 15 }
    : { stab: 25, wfe: 25, rdd: 20, sort: 15, pf: 15 };

  // Max DD % relative to peak equity
  const oosDd = typeof data.oos_metrics.max_drawdown === "number" ? data.oos_metrics.max_drawdown : 0;
  const oosPeak = Number(oosPnl) - oosDd; // peak ~ net + |dd|
  const oosDdPct = (oosDd !== 0 && oosPeak > 0) ? Math.abs(oosDd) / oosPeak * 100 : null;

  return (
    <div className="space-y-4">
      {/* OOS window info bar */}
      <div className="flex items-center gap-2 text-[10px] text-ag-text-muted">
        <span>OOS Window:</span>
        <span className="font-mono text-ag-text">{data.cutoff}</span>
        <span>→</span>
        <span className="font-mono text-ag-text">{data.oos_end_date || "data end"}</span>
        <span className="text-ag-text-muted">({data.oos_trades} trades)</span>
      </div>
      {/* Grade badge + WFE headline */}
      <div className="bg-ag-dark/50 rounded-lg p-3">
        {/* Grade badge row */}
        {grade && (
          <div className="flex items-center gap-3 mb-3">
            <span className={`px-3 py-1 rounded-lg text-lg font-bold border ${gradeColor(grade)}`}
              title={`Portfolio Grade: ${grade} (${stratLabel})\n\nWeighted score from:\n- Stability R\u00B2 (${gw.stab}%)\n- WFE (${gw.wfe}%)\n- Return/MaxDD (${gw.rdd}%)\n- Sortino (${gw.sort}%)\n- Profit Factor (${gw.pf}%)\n\nScore: ${((data.portfolio_grade_score ?? 0) * 100).toFixed(0)}%`}>
              {grade}
            </span>
            <div>
              <p className="text-xs text-ag-text-muted">Portfolio Grade</p>
              <p className="text-[10px] text-ag-text-muted">
                Score: {((data.portfolio_grade_score ?? 0) * 100).toFixed(0)}%
                <HintTip text={`Weighted grade adapted to strategy type (${stratLabel}). Weights: Stability R² (${gw.stab}%), WFE (${gw.wfe}%), Return/MaxDD (${gw.rdd}%), Sortino (${gw.sort}%), Profit Factor (${gw.pf}%). A+ >= 90%, A >= 80%, B+ >= 70%, B >= 60%.`} />
              </p>
            </div>
            <span className="text-[10px] px-1.5 py-0.5 rounded border border-ag-border text-ag-text-muted"
              title={`Auto-detected from OOS win rate & payoff ratio.\nTrend Following: low win rate + high payoff (TSL big wins).\nMean Reversion: high win rate + low payoff.\nWeights adjusted: Stability R\u00B2 ${gw.stab}%, Sortino ${gw.sort}%`}>
              {stratLabel}
            </span>
          </div>
        )}

        {/* Stress cost summary (OOS only) */}
        {(() => {
          const cs = data.oos_cost_summary || data.cost_summary;
          return cs && cs.total_stress_cost > 0 ? (
            <div className="flex flex-wrap items-center gap-3 text-[10px] text-ag-text-muted bg-ag-dark/30 rounded px-3 py-1.5 mb-2">
              <span className="font-medium text-yellow-400/80">Stress Costs{data.oos_cost_summary ? " (OOS)" : ""}</span>
              <span>Total: <span className="text-ag-red font-mono">${cs.total_stress_cost.toLocaleString()}</span></span>
              <span>Avg/Trade: <span className="text-ag-red font-mono">${cs.avg_cost_per_trade.toFixed(2)}</span></span>
              {cs.cost_breakdown.slippage > 0 && <span>Slip: ${cs.cost_breakdown.slippage.toLocaleString()}</span>}
              {cs.cost_breakdown.commission > 0 && <span>Comm: ${cs.cost_breakdown.commission.toLocaleString()}</span>}
              {cs.cost_breakdown.spread > 0 && <span>Spread: ${cs.cost_breakdown.spread.toLocaleString()}</span>}
              {cs.cost_breakdown.funding > 0 && <span>Funding: ${cs.cost_breakdown.funding.toLocaleString()}</span>}
            </div>
          ) : null;
        })()}

        {/* Row 1: Core performance */}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
          <MetricCell label="WFE (Edge)" value={`${(wfePf * 100).toFixed(0)}%`} color={wfeClr}
            hint="PF-based Walk-Forward Efficiency: (OOS_PF-1)/(IS_PF-1). Measures how much of the IS edge survived out-of-sample. 50%+ is good." />
          <MetricCell label="WFE (Rate)" value={`${(data.wfe * 100).toFixed(0)}%`} color={wfeTimeClr}
            hint="Time-normalized WFE: OOS daily profit rate / IS daily profit rate. Can be inflated when IS period is much longer than OOS." />
          <MetricCell label="OOS Net Profit"
            value={`$${Number(oosPnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
            color={Number(oosPnl) >= 0 ? "text-ag-green" : "text-ag-red"} />
          <MetricCell label="OOS Profit Factor"
            value={oosPf != null ? Number(oosPf).toFixed(2) : "\u2014"} />
          <MetricCell label="OOS Win Rate"
            value={oosWr != null ? `${Number(oosWr).toFixed(1)}%` : "\u2014"} />
          <MetricCell label="OOS Max DD"
            value={oosDd !== 0 ? `$${oosDd.toFixed(0)}${oosDdPct != null ? ` (${oosDdPct.toFixed(1)}%)` : ""}` : "\u2014"}
            color="text-ag-red"
            hint="Maximum drawdown in dollars and as % of peak equity." />
          <MetricCell label="OOS Trades" value={String(data.oos_trades)} />
          <MetricCell label="Stability R²"
            value={oosStab != null ? `${(oosStab * 100).toFixed(1)}%` : "\u2014"}
            color={oosStab != null ? stabilityColor(oosStab) : undefined}
            hint={stratType === "trend_following"
              ? "R-squared of equity curve. Trend-following strategies naturally produce staircase equity curves (TSL big wins) — lower R² is expected. Grade weight reduced to 10%."
              : stratType === "mean_reversion"
              ? "R-squared of equity curve. Mean-reversion strategies should produce smooth curves — R² is weighted heavily in the grade (30%)."
              : "R-squared of equity curve linear regression. Measures how smooth and consistent the equity growth is. Green >= 85%, Yellow >= 70%."} />
        </div>

        {/* Row 2: Robustness metrics */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-2">
          <MetricCell label="Return/MaxDD"
            value={`${retDd.toFixed(2)}x`}
            color={returnDdColor(retDd)}
            hint="Net profit divided by max drawdown. How much you earned per unit of pain. Green >= 3.0x, Yellow >= 1.5x." />
          <MetricCell label="Sortino"
            value={oosSortino != null ? oosSortino.toFixed(2) : "\u2014"}
            color={oosSortino != null ? sortinoColor(oosSortino) : undefined}
            hint="Risk-adjusted return using only downside deviation. Higher = better return per unit of downside risk. Green >= 2.0, Yellow >= 1.0." />
          <MetricCell label="Longest DD"
            value={oosDdDur != null ? `${oosDdDur}d` : "\u2014"}
            color={oosDdDur != null ? ddDurationColor(oosDdDur) : undefined}
            hint="Duration of the longest drawdown period in days. Shows how long the portfolio stayed underwater. Green < 30d, Yellow < 60d." />
          <MetricCell label="Gross P/L"
            value={`+$${Number(data.oos_metrics.gross_profit ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })} / -$${Number(data.oos_metrics.gross_loss ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
            hint="Total gross profit (green) and gross loss (red) in the OOS period." />
        </div>

        <div className="flex items-center gap-3 mt-2 text-xs text-ag-text-muted">
          <span>Cutoff: {data.cutoff}</span>
          <span>|</span>
          <span>IS: {data.is_trades} trades</span>
          <span>|</span>
          <span>OOS: {data.oos_trades} trades</span>
        </div>

        {/* Clipped trades warning */}
        {data.clipped_trades && data.clipped_trades.length > 0 && (
          <ClippedTradesMessage
            clippedTrades={data.clipped_trades}
            boundaryDate={data.oos_end_date ?? null}
          />
        )}
      </div>

      {/* IS Full Metrics (collapsible) */}
      {hasIS && (
        <details className="group">
          <summary className="cursor-pointer text-xs text-ag-text-muted hover:text-ag-text font-medium">
            In-Sample Full Metrics ({data.is_trades} trades)
          </summary>
          <div className="mt-2">
            <FullMetricsRows metrics={data.is_full_metrics} prefix="IS" />
          </div>
        </details>
      )}

      {/* IS + OOS Monthly PnL — with batch + direction filters, clickable cells */}
      <FilterableMonthlyPnL
        portfolioId={portfolioId}
        cutoffDate={data.cutoff ?? undefined}
        mode="both"
        onCellClick={(year, month, period) => setMonthModal({ year, month, period })}
      />

      {/* OOS Full Metrics (always visible) */}
      {hasOOS && (
        <div>
          <h4 className="text-xs font-semibold mb-2">Out-of-Sample Full Metrics</h4>
          <FullMetricsRows metrics={data.oos_full_metrics} prefix="OOS" />
        </div>
      )}

      {/* OOS Equity Curve */}
      {data.oos_equity_curve.length >= 2 && (
        <EquityCurve points={data.oos_equity_curve} title="OOS Equity Curve" showMDD />
      )}

      {/* Month Trades Modal */}
      {monthModal && (
        <MonthTradesModal
          portfolioId={portfolioId}
          year={monthModal.year}
          month={monthModal.month}
          cutoffDate={data.cutoff}
          period={monthModal.period}
          timeframe={timeframe}
          strategyId={strategyId}
          onClose={() => setMonthModal(null)}
        />
      )}

      {/* Strategy Group Breakdown */}
      {data.strategy_groups && data.strategy_groups.length > 0 && (
        <StrategyGroupBreakdown
          groups={data.strategy_groups}
          perAsset={data.per_asset}
          portfolioId={portfolioId}
          cutoffDate={data.cutoff}
          timeframe={timeframe}
          strategyId={strategyId}
        />
      )}

      {/* Per-Asset WFE */}
      {data.per_asset.length > 0 && (
        <PerAssetWFE assets={data.per_asset} />
      )}
    </div>
  );
}

function StrategyGroupBreakdown({ groups, perAsset, portfolioId, cutoffDate, timeframe, strategyId }: {
  groups: StrategyGroupResult[];
  perAsset: WFEAssetResult[];
  portfolioId: number;
  cutoffDate?: string | null;
  timeframe?: string;
  strategyId?: number;
}) {
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);
  const [expandedAsset, setExpandedAsset] = useState<string | null>(null);

  const toggleGroup = (key: string) => {
    setExpandedGroup(expandedGroup === key ? null : key);
    setExpandedAsset(null);
  };

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        OOS by Strategy &amp; Direction
      </h4>
      <div className="grid gap-2">
        {groups.map((g) => {
          const m = g.group_metrics;
          const pnlColor = m.net_profit >= 0 ? "text-ag-green" : "text-ag-red";
          const groupKey = `${g.strategy_name}-${g.direction}`;
          const isExpanded = expandedGroup === groupKey;
          // Match per_asset entries to this group
          const groupAssets = perAsset.filter((a) =>
            g.assets.includes(a.asset)
          );

          return (
            <div key={groupKey}>
              <div
                onClick={() => toggleGroup(groupKey)}
                className="bg-ag-dark/50 rounded-lg px-3 py-2 flex items-center justify-between gap-4 cursor-pointer hover:bg-ag-dark/70 transition-colors">
                <div className="flex items-center gap-2 min-w-0">
                  {isExpanded
                    ? <ChevronDown size={14} className="text-ag-text-muted shrink-0" />
                    : <ChevronRight size={14} className="text-ag-text-muted shrink-0" />
                  }
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    g.direction === "Long" ? "bg-ag-green/15 text-ag-green" :
                    g.direction === "Short" ? "bg-ag-red/15 text-ag-red" :
                    "bg-ag-accent/15 text-ag-accent"
                  }`}>
                    {g.direction}
                  </span>
                  <span className="text-xs font-medium text-ag-text truncate">
                    {g.strategy_name}
                  </span>
                  <span className="text-[10px] text-ag-text-muted">
                    ({g.assets.length} asset{g.assets.length !== 1 ? "s" : ""})
                  </span>
                </div>
                <div className="flex items-center gap-4 text-xs shrink-0">
                  <span className={`font-mono font-medium ${pnlColor}`}>
                    ${m.net_profit.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </span>
                  <span className="text-ag-green font-mono text-[10px]">
                    +${m.gross_profit.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </span>
                  <span className="text-ag-red font-mono text-[10px]">
                    -${m.gross_loss.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </span>
                  <span className="text-ag-text-muted">
                    PF: <span className="font-mono">{m.profit_factor != null ? m.profit_factor.toFixed(2) : "\u2014"}</span>
                  </span>
                  <span className="text-ag-text-muted">
                    WR: <span className="font-mono">{m.win_rate.toFixed(1)}%</span>
                  </span>
                  <span className="text-ag-text-muted">
                    <span className="font-mono">{m.trades}</span> trades
                  </span>
                </div>
              </div>

              {/* Expanded asset rows */}
              {isExpanded && (
                <div className="ml-6 mt-1 space-y-1">
                  {groupAssets.length === 0 ? (
                    <p className="text-[10px] text-ag-text-muted py-1">No per-asset data available.</p>
                  ) : (
                    groupAssets.map((a) => {
                      const assetKey = a.asset;
                      const isAssetExp = expandedAsset === assetKey;
                      const assetPnlColor = a.OOS_PnL >= 0 ? "text-ag-green" : "text-ag-red";
                      return (
                        <div key={assetKey}>
                          <div
                            onClick={() => setExpandedAsset(isAssetExp ? null : assetKey)}
                            className="bg-ag-card/50 border border-ag-border/30 rounded px-3 py-1.5 flex items-center justify-between gap-3 cursor-pointer hover:bg-ag-card-hover transition-colors">
                            <div className="flex items-center gap-2 min-w-0">
                              {isAssetExp
                                ? <ChevronDown size={12} className="text-ag-text-muted shrink-0" />
                                : <ChevronRight size={12} className="text-ag-text-muted shrink-0" />
                              }
                              <span className="text-xs font-medium">
                                {a.asset.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")}
                              </span>
                            </div>
                            <div className="flex items-center gap-3 text-[11px] shrink-0">
                              <span className={`font-mono font-medium ${assetPnlColor}`}>
                                ${a.OOS_PnL.toFixed(0)}
                              </span>
                              <span className="text-ag-green font-mono text-[10px]">
                                +${(a.OOS_GP ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                              </span>
                              <span className="text-ag-red font-mono text-[10px]">
                                -${(a.OOS_GL ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                              </span>
                              <span className="text-ag-text-muted">
                                PF: <span className="font-mono">{a.OOS_PF.toFixed(2)}</span>
                              </span>
                              <span className="text-ag-text-muted">
                                WR: <span className="font-mono">{a.OOS_WR != null ? `${a.OOS_WR.toFixed(1)}%` : "\u2014"}</span>
                              </span>
                              <span className="text-ag-text-muted">
                                <span className="font-mono">{a.OOS_Trades}</span> trades
                              </span>
                              <span className={`font-mono text-[10px] ${(a.WFE_PF ?? a.WFE) >= 0.5 ? "text-ag-green" : (a.WFE_PF ?? a.WFE) >= 0.25 ? "text-yellow-400" : "text-ag-red"}`}>
                                WFE: {((a.WFE_PF ?? a.WFE) * 100).toFixed(0)}%
                              </span>
                            </div>
                          </div>

                          {/* Expanded trade list */}
                          {isAssetExp && (
                            <div className="ml-4 mt-1 mb-2 border-l-2 border-ag-border/30 pl-3">
                              <AssetTradeList portfolioId={portfolioId} assetName={a.asset} cutoffDate={cutoffDate} timeframe={timeframe} strategyId={strategyId} />
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PerAssetWFE({ assets }: { assets: WFEAssetResult[] }) {
  const hasStrategy = assets.some((a) => a.strategy_name && a.strategy_name !== "Unknown");
  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">Per-Asset WFE</h4>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-ag-border text-ag-text-muted">
              <th className="text-left py-1.5 px-2">Asset</th>
              {hasStrategy && <th className="text-left py-1.5 px-2">Strategy</th>}
              <th className="text-right py-1.5 px-2">WFE</th>
              <th className="text-right py-1.5 px-2">IS PF</th>
              <th className="text-right py-1.5 px-2">OOS PF</th>
              <th className="text-right py-1.5 px-2">OOS WR</th>
              <th className="text-right py-1.5 px-2">IS PnL</th>
              <th className="text-right py-1.5 px-2">OOS PnL</th>
              <th className="text-right py-1.5 px-2 text-ag-green">OOS GP</th>
              <th className="text-right py-1.5 px-2 text-ag-red">OOS GL</th>
              <th className="text-right py-1.5 px-2">IS #</th>
              <th className="text-right py-1.5 px-2">OOS #</th>
            </tr>
          </thead>
          <tbody>
            {assets.map((a) => (
              <tr key={a.asset} className="border-b border-ag-border/20 hover:bg-ag-card-hover">
                <td className="py-1.5 px-2 font-medium">
                  {a.asset.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")}
                </td>
                {hasStrategy && (
                  <td className="py-1.5 px-2 text-ag-text-muted text-[10px] max-w-[120px] truncate">
                    {a.strategy_name || "\u2014"}
                  </td>
                )}
                <td className={`text-right py-1.5 px-2 font-mono ${wfeColor(a.WFE_PF ?? a.WFE)}`}>
                  {((a.WFE_PF ?? a.WFE) * 100).toFixed(0)}%
                </td>
                <td className="text-right py-1.5 px-2 font-mono">{a.IS_PF.toFixed(2)}</td>
                <td className={`text-right py-1.5 px-2 font-mono ${a.OOS_PF >= 1 ? "text-ag-green" : "text-ag-red"}`}>
                  {a.OOS_PF.toFixed(2)}
                </td>
                <td className={`text-right py-1.5 px-2 font-mono ${(a.OOS_WR ?? 0) >= 50 ? "text-ag-green" : "text-ag-red"}`}>
                  {a.OOS_WR != null ? `${a.OOS_WR.toFixed(1)}%` : "\u2014"}
                </td>
                <td className={`text-right py-1.5 px-2 font-mono ${a.IS_PnL >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                  ${a.IS_PnL.toFixed(0)}
                </td>
                <td className={`text-right py-1.5 px-2 font-mono ${a.OOS_PnL >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                  ${a.OOS_PnL.toFixed(0)}
                </td>
                <td className="text-right py-1.5 px-2 font-mono text-ag-green">
                  ${(a.OOS_GP ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
                <td className="text-right py-1.5 px-2 font-mono text-ag-red">
                  ${(a.OOS_GL ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
                <td className="text-right py-1.5 px-2">{a.IS_Trades}</td>
                <td className="text-right py-1.5 px-2">{a.OOS_Trades}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MetricCell({ label, value, color, hint }: { label: string; value: string; color?: string; hint?: string }) {
  return (
    <div>
      <p className="text-ag-text-muted text-[10px] uppercase tracking-wider">
        {label}
        {hint && <HintTip text={hint} />}
      </p>
      <p className={`text-sm font-semibold ${color ?? "text-ag-text"}`}>{value}</p>
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
  if (/^\d{1,2}:\d{2}:\d{2}/.test(s)) return 0;
  return null;
}
