"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Briefcase, ArrowRight, TrendingUp, TrendingDown, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioItem } from "@/lib/api";
import { useApi } from "@/hooks/useApi";

/* ── helpers ── */

function fmtUsd(v: number | undefined): string {
  if (v === undefined || v === null) return "—";
  const abs = Math.abs(v);
  const s = abs < 1000 ? `$${abs.toFixed(0)}` : `$${(abs / 1000).toFixed(1)}k`;
  return v < 0 ? `-${s}` : `+${s}`;
}

function fmtPct(v: number | undefined): string {
  if (v === undefined || v === null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

function fmtPf(v: number | undefined): string {
  if (v === undefined || v === null) return "—";
  return `${v.toFixed(2)}x`;
}

function fmtNum(v: number | undefined): string {
  if (v === undefined || v === null) return "—";
  return String(Math.round(v));
}

function valColor(v: number | undefined, invert = false): string {
  if (v === undefined || v === null) return "text-ag-text-muted";
  if (invert) return v <= 0 ? "text-ag-green" : "text-ag-red";
  return v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "text-ag-text-muted";
}

function pfColor(v: number | undefined): string {
  if (v === undefined || v === null) return "text-ag-text-muted";
  return v >= 1.5 ? "text-ag-green" : v < 1.0 ? "text-ag-red" : "text-ag-text";
}

/* ── Metric column (OOS or Live) ── */

function MetricColumn({ label, metrics, labelColor }: {
  label: string;
  metrics: Record<string, number>;
  labelColor: string;
}) {
  const pnl = metrics.net_profit ?? metrics.total_pnl;
  const wr = metrics.win_rate;
  const pf = metrics.profit_factor;
  const dd = metrics.max_drawdown;
  const trades = metrics.total_trades;
  const empty = Object.keys(metrics).length === 0;

  return (
    <div className="flex-1 min-w-0">
      <p className={`text-[9px] uppercase font-semibold mb-1.5 ${labelColor}`}>{label}</p>
      {empty ? (
        <p className="text-[10px] text-ag-text-muted italic">No data yet</p>
      ) : (
        <div className="grid grid-cols-5 gap-1 text-center">
          <div>
            <p className="text-[8px] text-ag-text-muted">PnL</p>
            <p className={`text-[11px] font-mono font-bold ${valColor(pnl)}`}>{fmtUsd(pnl)}</p>
          </div>
          <div>
            <p className="text-[8px] text-ag-text-muted">WR</p>
            <p className={`text-[11px] font-mono font-bold ${valColor(wr)}`}>{fmtPct(wr)}</p>
          </div>
          <div>
            <p className="text-[8px] text-ag-text-muted">PF</p>
            <p className={`text-[11px] font-mono font-bold ${pfColor(pf)}`}>{fmtPf(pf)}</p>
          </div>
          <div>
            <p className="text-[8px] text-ag-text-muted">DD</p>
            <p className={`text-[11px] font-mono font-bold text-ag-red`}>
              {dd !== undefined ? fmtUsd(-Math.abs(dd)) : "—"}
            </p>
          </div>
          <div>
            <p className="text-[8px] text-ag-text-muted">Trades</p>
            <p className="text-[11px] font-mono font-bold">{fmtNum(trades)}</p>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── DD counter badge ── */

interface DDStatus {
  in_drawdown: boolean;
  dd_days: number;
  dd_depth_usd: number;
  dd_depth_pct: number;
  oos_worst_dd_usd: number;
  oos_worst_dd_pct: number;
}

function DDCounterBadge({ dd }: { dd: DDStatus }) {
  if (!dd.in_drawdown) {
    return (
      <div className="rounded-md px-2 py-1.5 bg-ag-green/10 flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-ag-green" />
        <span className="text-[10px] font-semibold text-ag-green">No Drawdown</span>
      </div>
    );
  }

  // Color: green if less than OOS worst, yellow if > 50% of OOS worst, red if exceeding
  const depthAbs = Math.abs(dd.dd_depth_usd);
  const oosAbs = Math.abs(dd.oos_worst_dd_usd);
  const ratio = oosAbs > 0 ? depthAbs / oosAbs : 0;
  const color = ratio >= 1.0 ? "text-ag-red" : ratio >= 0.5 ? "text-yellow-400" : "text-ag-green";
  const bgColor = ratio >= 1.0 ? "bg-ag-red/10" : ratio >= 0.5 ? "bg-yellow-400/10" : "bg-ag-green/10";

  return (
    <div className={`rounded-md px-2 py-1.5 ${bgColor} space-y-0.5`}>
      <div className="flex items-center gap-1">
        {ratio >= 0.5 && <AlertTriangle size={10} className={color} />}
        <span className={`text-[10px] font-semibold ${color}`}>
          {dd.dd_days}d in DD
        </span>
      </div>
      <div className="flex items-center gap-2 text-[9px]">
        <span className={color}>
          {fmtUsd(dd.dd_depth_usd)} / {dd.dd_depth_pct.toFixed(1)}%
        </span>
        <span className="text-ag-text-muted">
          vs OOS: {fmtUsd(dd.oos_worst_dd_usd)} / {dd.oos_worst_dd_pct.toFixed(1)}%
        </span>
      </div>
    </div>
  );
}

/* ── Portfolio card (OOS | Live side-by-side) ── */

function PortfolioCard({ portfolio }: { portfolio: PortfolioItem }) {
  const [oosMetrics, setOosMetrics] = useState<Record<string, number>>({});
  const [ddStatus, setDDStatus] = useState<DDStatus | null>(null);

  const liveMetrics = portfolio.live_metrics_snapshot ?? {};
  const livePnl = liveMetrics.net_profit ?? liveMetrics.total_pnl ?? 0;
  const isUp = livePnl >= 0;

  // Fetch OOS metrics
  useEffect(() => {
    if (!portfolio.optimization_end_date) return;
    api.portfolioOOSDate(portfolio.id, portfolio.optimization_end_date)
      .then((r) => {
        if (r.oos_metrics && Object.keys(r.oos_metrics).length > 0) {
          setOosMetrics(r.oos_metrics as Record<string, number>);
        }
      })
      .catch(() => {});
  }, [portfolio.id, portfolio.optimization_end_date]);

  // Fetch DD status for active portfolios
  useEffect(() => {
    if (portfolio.portfolio_type !== "paper" && portfolio.portfolio_type !== "real") return;
    api.portfolioDDStatus(portfolio.id)
      .then(setDDStatus)
      .catch(() => {});
  }, [portfolio.id, portfolio.portfolio_type]);

  return (
    <Link
      href={`/portfolios?tab=${portfolio.portfolio_type === "paper" ? "paper" : "real"}&id=${portfolio.id}`}
      className="block bg-ag-dark/40 rounded-lg p-3 space-y-2 hover:bg-ag-dark/60 transition-colors cursor-pointer"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {isUp ? <TrendingUp size={14} className="text-ag-green" /> : <TrendingDown size={14} className="text-ag-red" />}
          <span className="text-xs font-medium truncate max-w-[250px]">{portfolio.name}</span>
        </div>
        <span className="text-[9px] text-ag-text-muted">{portfolio.asset_count} assets</span>
      </div>

      {/* OOS | Live side-by-side */}
      <div className="flex gap-3">
        <MetricColumn
          label="OOS (Backtest)"
          metrics={oosMetrics}
          labelColor="text-ag-accent"
        />
        <div className="w-px bg-ag-border/40 self-stretch" />
        <MetricColumn
          label={portfolio.portfolio_type === "paper" ? "Paper Trading" : "Live Trading"}
          metrics={liveMetrics}
          labelColor={portfolio.portfolio_type === "paper" ? "text-ag-yellow" : "text-ag-green"}
        />
      </div>

      {/* DD counter */}
      {ddStatus && <DDCounterBadge dd={ddStatus} />}
    </Link>
  );
}

/* ── Main component ── */

interface PortfolioPerformanceProps {
  /** Optional filter: show only "paper" or "real" portfolios. If omitted, shows both. */
  filterType?: "paper" | "real";
}

export default function PortfolioPerformance({ filterType }: PortfolioPerformanceProps = {}) {
  const { data: paper } = useApi(
    useCallback(() => api.portfoliosByType("paper"), []),
    "dash-portfolios-paper",
  );
  const { data: real } = useApi(
    useCallback(() => api.portfoliosByType("real"), []),
    "dash-portfolios-real",
  );

  const showPaper = !filterType || filterType === "paper";
  const showReal = !filterType || filterType === "real";
  const hasPaper = showPaper && (paper?.length ?? 0) > 0;
  const hasReal = showReal && (real?.length ?? 0) > 0;

  if (!hasPaper && !hasReal) return null;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Briefcase size={16} className="text-ag-accent" />
          <h3 className="text-sm font-semibold">Portfolio Performance</h3>
        </div>
        <Link href="/portfolios" className="text-xs text-ag-accent hover:underline flex items-center gap-1">
          View All <ArrowRight size={12} />
        </Link>
      </div>

      <div className="space-y-3">
        {/* Real Trading */}
        {hasReal && (
          <div className="space-y-2">
            <p className="text-[10px] text-ag-green uppercase font-medium flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-ag-green inline-block" /> Real Trading
            </p>
            {real!.slice(0, 4).map((p) => (
              <PortfolioCard key={p.id} portfolio={p} />
            ))}
            {(real!.length > 4) && (
              <p className="text-[9px] text-ag-text-muted">+{real!.length - 4} more</p>
            )}
          </div>
        )}

        {/* Paper Trading */}
        {hasPaper && (
          <div className="space-y-2">
            <p className="text-[10px] text-ag-yellow uppercase font-medium flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-ag-yellow inline-block" /> Paper Trading
            </p>
            {paper!.slice(0, 4).map((p) => (
              <PortfolioCard key={p.id} portfolio={p} />
            ))}
            {(paper!.length > 4) && (
              <p className="text-[9px] text-ag-text-muted">+{paper!.length - 4} more</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
