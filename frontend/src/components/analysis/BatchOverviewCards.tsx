"use client";

import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { DashboardAnalysisRequest, BatchOverviewResult, LiveViewsResult, PortfolioDetail } from "@/lib/api";
import { ComparisonMetricGrid } from "./metrics";
import type { PeriodData } from "./metrics";
import { ClippedTradesMessage } from "./ClippedTradesMessage";

interface Props {
  req: DashboardAnalysisRequest;
}

function fmt$(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "\u2014";
  if (typeof v === "string") return v;
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtN(v: number | string | null | undefined, dp: number = 2): string {
  if (v === null || v === undefined) return "\u2014";
  if (typeof v === "string") return v;
  return v.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function fmtPct(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "\u2014";
  if (typeof v === "string") return v;
  return `${v.toFixed(2)}%`;
}

function valColor(v: number | string | null | undefined): string {
  if (v === null || v === undefined || typeof v === "string") return "";
  return v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";
}

interface CardDef {
  label: string;
  key: string;
  fmt: "dollar" | "number" | "pct" | "ratio" | "time";
}

const KPI_ROWS: CardDef[][] = [
  [
    { label: "Total Net Profit", key: "Total Net Profit", fmt: "dollar" },
    { label: "Total Positions", key: "Total Positions", fmt: "number" },
    { label: "Win Rate", key: "Win Rate", fmt: "pct" },
    { label: "Profit Factor", key: "Profit Factor", fmt: "ratio" },
  ],
  [
    { label: "Avg Trade Profit", key: "Avg Trade Profit", fmt: "dollar" },
    { label: "Avg Positions/Month", key: "Avg Positions/Month", fmt: "number" },
    { label: "Max Drawdown", key: "Max Drawdown ($)", fmt: "dollar" },
    { label: "Max DD Duration", key: "Max DD Duration", fmt: "time" },
  ],
  [
    { label: "Sortino Ratio", key: "Sortino Ratio", fmt: "ratio" },
    { label: "SQN", key: "SQN", fmt: "ratio" },
    { label: "Recovery Factor", key: "Recovery Factor", fmt: "ratio" },
    { label: "Calmar Ratio", key: "Calmar Ratio", fmt: "ratio" },
  ],
  [
    { label: "Avg Win", key: "Avg Win ($)", fmt: "dollar" },
    { label: "Avg Loss", key: "Avg Loss ($)", fmt: "dollar" },
    { label: "Avg Win/Loss", key: "Avg Win/Loss Ratio", fmt: "ratio" },
    { label: "Ulcer Index", key: "Ulcer Index", fmt: "ratio" },
  ],
  [
    { label: "Avg Drawdown", key: "Avg Drawdown ($)", fmt: "dollar" },
    { label: "Avg DD Duration", key: "Avg DD Duration", fmt: "time" },
    { label: "Best Run-up", key: "Best Run-up ($)", fmt: "dollar" },
    { label: "Max Open Positions", key: "Max Simultaneous Trades", fmt: "number" },
  ],
];

function formatCard(v: number | string | null | undefined, fmt: string): string {
  if (v === null || v === undefined) return "\u2014";
  if (typeof v === "string") return v;
  switch (fmt) {
    case "dollar": return fmt$(v);
    case "pct": return fmtPct(v);
    case "ratio": return fmtN(v);
    case "number": return fmtN(v, 0);
    case "time": return String(v);
    default: return String(v);
  }
}

/** Portfolio mode: IS/OOS comparison cards */
function PortfolioOverview({ portfolioId }: { portfolioId: number }) {
  const [data, setData] = useState<LiveViewsResult | null>(null);
  const [detail, setDetail] = useState<PortfolioDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    Promise.all([
      api.portfolioLiveViews(portfolioId),
      api.portfolioDetail(portfolioId),
    ])
      .then(([lv, pd]) => { setData(lv); setDetail(pd); })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }, [portfolioId]);

  if (loading) {
    return (
      <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted">
        Loading overview...
      </div>
    );
  }
  if (error) {
    return (
      <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-red text-sm">
        {error}
      </div>
    );
  }
  if (!data) return null;

  const periods: PeriodData[] = [];
  const isView = data.in_sample;
  // Use clipped OOS (oos) for consistency; oos_full = unclipped for reference
  const oosView = data.oos;
  const liveView = data.live;

  if (isView?.full_metrics && Object.keys(isView.full_metrics).length > 0) {
    periods.push({ label: "IS", metrics: isView.full_metrics });
  }
  if (oosView?.full_metrics && Object.keys(oosView.full_metrics).length > 0) {
    periods.push({ label: "OOS", metrics: oosView.full_metrics });
  }
  if (liveView?.full_metrics && Object.keys(liveView.full_metrics).length > 0 && (liveView.trade_count ?? 0) > 0) {
    periods.push({ label: "Live", metrics: liveView.full_metrics });
  }

  if (periods.length === 0) {
    return <p className="text-xs text-ag-text-muted">No IS/OOS data available.</p>;
  }

  // Summary counts
  const isTrades = isView?.trade_count ?? 0;
  const oosTrades = oosView?.trade_count ?? 0;
  const liveTrades = liveView?.trade_count ?? 0;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-4 text-xs text-ag-text-muted">
        <span>IS: {isTrades} trades</span>
        <span>OOS: {oosTrades} trades</span>
        {liveTrades > 0 && <span>Live: {liveTrades} trades</span>}
        <span>Total: {isTrades + oosTrades + liveTrades} trades</span>
      </div>
      {/* Stress cost summary */}
      {data.cost_summary && data.cost_summary.total_stress_cost > 0 && (
        <div className="flex flex-wrap items-center gap-3 text-[10px] text-ag-text-muted bg-ag-dark/30 rounded px-3 py-1.5">
          <span className="font-medium text-yellow-400/80">Stress Costs</span>
          <span>Total: <span className="text-ag-red font-mono">${data.cost_summary.total_stress_cost.toLocaleString()}</span></span>
          <span>Avg/Trade: <span className="text-ag-red font-mono">${data.cost_summary.avg_cost_per_trade.toFixed(2)}</span></span>
          {data.cost_summary.cost_breakdown.slippage > 0 && <span>Slip: ${data.cost_summary.cost_breakdown.slippage.toLocaleString()}</span>}
          {data.cost_summary.cost_breakdown.commission > 0 && <span>Comm: ${data.cost_summary.cost_breakdown.commission.toLocaleString()}</span>}
          {data.cost_summary.cost_breakdown.spread > 0 && <span>Spread: ${data.cost_summary.cost_breakdown.spread.toLocaleString()}</span>}
          {data.cost_summary.cost_breakdown.funding > 0 && <span>Funding: ${data.cost_summary.cost_breakdown.funding.toLocaleString()}</span>}
        </div>
      )}
      <ComparisonMetricGrid periods={periods} />
      {/* Clipped trades warning */}
      {data.clipped_trades && data.clipped_trades.length > 0 && (
        <ClippedTradesMessage clippedTrades={data.clipped_trades} />
      )}
      {/* Batch / Asset composition table */}
      {detail && detail.assets.length > 0 && (
        <BatchAssetComposition detail={detail} />
      )}
    </div>
  );
}

/** Batch / Asset composition table for portfolio overview */
function BatchAssetComposition({ detail }: { detail: PortfolioDetail }) {
  // Group assets by batch
  const batchMap = detail.batch_id_map || {};
  const batchGroups: Record<number, typeof detail.assets> = {};
  // Count how many batches each symbol appears in
  const symbolBatchCount: Record<string, Set<number>> = {};

  for (const a of detail.assets) {
    const bid = a.batch_id ?? 0;
    if (!batchGroups[bid]) batchGroups[bid] = [];
    batchGroups[bid].push(a);
    if (!symbolBatchCount[a.symbol]) symbolBatchCount[a.symbol] = new Set();
    symbolBatchCount[a.symbol].add(bid);
  }

  const batchEntries = Object.entries(batchGroups)
    .map(([bid, assets]) => ({ batchId: Number(bid), name: batchMap[Number(bid)] || `Batch ${bid}`, assets }))
    .sort((a, b) => a.name.localeCompare(b.name));

  // Unique symbols sorted
  const allSymbols = [...new Set(detail.assets.map((a) => a.symbol))].sort();
  const maxOverlap = Math.max(...allSymbols.map((s) => symbolBatchCount[s]?.size ?? 0));

  return (
    <div className="bg-ag-darker border border-ag-border rounded-lg p-3">
      <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-2 font-medium">
        Batch / Asset Composition
        <span className="ml-2 normal-case tracking-normal">
          {batchEntries.length} batch{batchEntries.length !== 1 ? "es" : ""} &middot; {allSymbols.length} unique symbol{allSymbols.length !== 1 ? "s" : ""} &middot; {detail.assets.length} total asset{detail.assets.length !== 1 ? "s" : ""}
        </span>
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-ag-text-muted border-b border-ag-border/30">
              <th className="text-left py-1.5 px-2 font-medium">Batch</th>
              <th className="text-center py-1.5 px-2 font-medium">Assets</th>
              <th className="text-left py-1.5 px-2 font-medium">Symbols</th>
            </tr>
          </thead>
          <tbody>
            {batchEntries.map((b) => (
              <tr key={b.batchId} className="border-b border-ag-border/10">
                <td className="py-1.5 px-2 text-ag-text font-medium max-w-[200px] truncate" title={b.name}>
                  {b.name}
                </td>
                <td className="py-1.5 px-2 text-center text-ag-text-muted">{b.assets.length}</td>
                <td className="py-1.5 px-2">
                  <div className="flex flex-wrap gap-1">
                    {b.assets
                      .sort((a, c) => a.symbol.localeCompare(c.symbol))
                      .map((a) => {
                        const overlapCount = symbolBatchCount[a.symbol]?.size ?? 1;
                        const isOverlap = overlapCount > 1;
                        return (
                          <span
                            key={`${a.symbol}_${a.side}`}
                            className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] ${
                              isOverlap
                                ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                                : "bg-ag-dark text-ag-text-muted border border-ag-border/20"
                            }`}
                            title={`${a.symbol} (${a.side})${isOverlap ? ` — in ${overlapCount} batches` : ""}`}
                          >
                            {a.symbol.replace("USDT", "")}
                            <span className={a.side === "Long" ? "text-ag-green/60" : a.side === "Short" ? "text-ag-red/60" : "text-ag-text-muted/60"}>
                              {a.side === "Long" ? "L" : a.side === "Short" ? "S" : "B"}
                            </span>
                            {isOverlap && (
                              <span className="text-amber-400/80 font-semibold ml-0.5">
                                {overlapCount}x
                              </span>
                            )}
                          </span>
                        );
                      })}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Overlap summary */}
      {maxOverlap > 1 && (
        <div className="mt-2 text-[10px] text-amber-400/70 flex items-center gap-1">
          <span className="bg-amber-500/10 px-1.5 py-0.5 rounded border border-amber-500/20 font-medium">
            {allSymbols.filter((s) => (symbolBatchCount[s]?.size ?? 0) > 1).length} symbol{allSymbols.filter((s) => (symbolBatchCount[s]?.size ?? 0) > 1).length !== 1 ? "s" : ""}
          </span>
          <span>appear in multiple batches (highlighted in amber with overlap count)</span>
        </div>
      )}
    </div>
  );
}

/** Backtest configuration display for the Analysis page */
function AnalysisConfigPanel({ params }: { params: Record<string, unknown> }) {
  if (!params || Object.keys(params).length === 0) return null;

  const slipMode = (params.slippage_mode as string) ?? "pct";
  const slipVal = (params.slippage_value as number) ?? 0;
  const slipLabel = slipMode === "pips" ? "Pips" : slipMode === "risk_pct" ? "% Risk" : "% Price";
  const costsOn = params.include_costs !== false;

  const items: { label: string; value: string; highlight?: boolean }[] = [
    { label: "Initial Capital", value: `$${((params.initial_capital as number) ?? 10000).toLocaleString()}` },
    { label: "Trading Costs", value: costsOn ? "ON" : "OFF", highlight: !costsOn },
  ];

  if (costsOn) {
    items.push(
      { label: "Commission", value: `${(params.commission_pct as number) ?? 0.055}%`, highlight: true },
      { label: "Slippage", value: slipVal > 0 ? `${slipVal} (${slipLabel})` : "None", highlight: slipVal > 0 },
    );
  }

  items.push(
    { label: "Leverage", value: `${(params.leverage as number) ?? 1}x` },
    { label: "Max Pyramiding", value: `${(params.max_pyramiding as number) ?? 1}` },
    { label: "Mode", value: `${(params.mode as string) ?? "individual"}` },
  );

  const riskMode = params.risk_mode as string;
  const riskVal = params.risk_value as number;
  if (riskMode && riskVal && riskVal > 0) {
    items.push({ label: "Risk", value: `${riskVal} (${riskMode})` });
  }

  const warmup = params.warmup_bars as number;
  if (warmup && warmup > 0) {
    items.push({ label: "Warmup Bars", value: `${warmup}` });
  }

  if (params.martingale_enabled) {
    items.push({
      label: "Martingale",
      value: `${(params.martingale_type as string) ?? "anti"} ${(params.martingale_multiplier as number) ?? 2}x (max ${(params.martingale_max_streak as number) ?? 3})`,
    });
  }

  if (params.vol_filter_enabled) {
    items.push({
      label: "Vol Filter",
      value: `ATR(${(params.vol_filter_atr_length as number) ?? 14}) < ${(params.vol_filter_atr_ceiling as number) ?? 0}`,
    });
  }

  const minAdv = params.min_avg_daily_volume as number;
  if (minAdv && minAdv > 0) {
    items.push({ label: "Min ADV", value: `$${minAdv.toLocaleString()}` });
  }

  // Strategy inputs (parameter values used for the backtest)
  const stratInputs = params.strategy_inputs as Record<string, unknown> | undefined;
  const stratItems: { label: string; value: string }[] = [];
  if (stratInputs && typeof stratInputs === "object") {
    for (const [key, val] of Object.entries(stratInputs)) {
      if (val === null || val === undefined) continue;
      // Convert snake_case key to Title Case label
      const label = key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
      let display: string;
      if (typeof val === "boolean") display = val ? "Yes" : "No";
      else if (typeof val === "number") display = val % 1 === 0 ? String(val) : val.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
      else display = String(val);
      stratItems.push({ label, value: display });
    }
  }

  return (
    <div className="space-y-2">
      <div className="bg-ag-darker border border-ag-border rounded-lg p-3">
        <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-2 font-medium">Backtest Configuration</p>
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-x-4 gap-y-1.5">
          {items.map((item) => (
            <div key={item.label}>
              <p className="text-[10px] text-ag-text-muted uppercase tracking-wide">{item.label}</p>
              <p className={`text-xs font-medium ${item.highlight ? "text-amber-400" : "text-ag-text"}`}>
                {item.value}
              </p>
            </div>
          ))}
        </div>
      </div>
      {stratItems.length > 0 && (
        <div className="bg-ag-darker border border-ag-border rounded-lg p-3">
          <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-2 font-medium">Strategy Configuration</p>
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-x-4 gap-y-1.5">
            {stratItems.map((item) => (
              <div key={item.label}>
                <p className="text-[10px] text-ag-text-muted uppercase tracking-wide">{item.label}</p>
                <p className="text-xs font-medium text-ag-text">{item.value}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Batch mode: single-value KPI cards OR side-by-side when filters active */
function BatchOverview({ req }: { req: DashboardAnalysisRequest }) {
  const [data, setData] = useState<BatchOverviewResult | null>(null);
  const [unfilteredData, setUnfilteredData] = useState<BatchOverviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const hasFilters = !!(req.date_to || req.min_trades || req.exclude_top_n
    || req.pf_min !== undefined || req.pf_max !== undefined || req.wr_min !== undefined);

  useEffect(() => {
    if (!req.batch_id && !req.portfolio_id) return;
    setLoading(true);
    setError("");

    const unfilteredReq: DashboardAnalysisRequest = {
      batch_id: req.batch_id,
      portfolio_id: req.portfolio_id,
      side_filter: req.side_filter,
    };

    const promises: Promise<BatchOverviewResult>[] = [api.dashboardBatchOverview(req)];
    if (hasFilters) promises.push(api.dashboardBatchOverview(unfilteredReq));

    Promise.all(promises)
      .then(([filtered, unfiltered]) => {
        setData(filtered);
        setUnfilteredData(unfiltered ?? null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }, [req.batch_id, req.portfolio_id, req.side_filter, req.date_to, req.min_trades, req.exclude_top_n, req.pf_min, req.pf_max, req.wr_min]);

  if (loading) {
    return (
      <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted">
        Loading overview...
      </div>
    );
  }
  if (error) {
    return (
      <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-red text-sm">
        {error}
      </div>
    );
  }
  if (!data) return null;

  // Side-by-side comparison when filters are active
  if (hasFilters && unfilteredData) {
    const periods: PeriodData[] = [
      { label: "Unfiltered", metrics: unfilteredData.aggregate },
      { label: "Filtered", metrics: data.aggregate },
    ];
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-4 text-xs text-ag-text-muted">
          <span>Unfiltered: {unfilteredData.trade_count} trades · {unfilteredData.symbols.length} assets</span>
          <span className="text-ag-accent">Filtered: {data.trade_count} trades · {data.symbols.length} assets</span>
        </div>
        <ComparisonMetricGrid periods={periods} />
        {data.parameters && <AnalysisConfigPanel params={data.parameters} />}
      </div>
    );
  }

  // Single-value cards (no filters)
  const m = data.aggregate;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-4 text-xs text-ag-text-muted">
        <span>{data.trade_count} trades</span>
        <span>{data.symbols.length} assets</span>
      </div>
      {KPI_ROWS.map((row, ri) => (
        <div key={ri} className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {row.map((card) => {
            const val = m[card.key];
            return (
              <div
                key={card.key}
                className="bg-ag-card border border-ag-border rounded-lg p-3"
              >
                <p className="text-[10px] text-ag-text-muted mb-0.5 uppercase tracking-wider">
                  {card.label}
                </p>
                <p className={`text-sm font-semibold ${valColor(val as number)}`}>
                  {formatCard(val as number, card.fmt)}
                </p>
              </div>
            );
          })}
        </div>
      ))}
      {data.parameters && <AnalysisConfigPanel params={data.parameters} />}
    </div>
  );
}

export default function BatchOverviewCards({ req }: Props) {
  // Portfolio mode: show IS/OOS comparison cards
  if (req.portfolio_id) {
    return <PortfolioOverview portfolioId={req.portfolio_id} />;
  }
  // Batch mode: single-value cards (unchanged)
  return <BatchOverview req={req} />;
}
