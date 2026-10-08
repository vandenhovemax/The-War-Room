"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

type MonthBucket = { trades: number; pnl: number; win_rate: number };
type EngineBucket = MonthBucket & { first_at: string | null; last_at: string | null };

type Row = {
  id: number;
  name: string;
  mode: string;
  portfolio_type: string;
  activated_at: string | null;
  last_refreshed_at: string | null;
  trades_count: number;
  net_pnl: number;
  win_rate: number;
  avg_trade_pnl: number;
  sortino: number | null;
  profit_factor: number | null;
  max_dd: number | null;
  sparkline: { t: string; pnl: number }[];
  needs_refresh: boolean;
  current_month: MonthBucket;
  prev_month: MonthBucket;
  current_month_label: string | null;
  prev_month_label: string | null;
  engine: EngineBucket;
};

type SortKey = "id" | "name" | "trades" | "pnl" | "wr";

export default function PaperOverviewPanel() {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("pnl");
  const [sortDesc, setSortDesc] = useState(true);
  const [filter, setFilter] = useState<"all" | "paper" | "real" | "hybrid" | "non_hybrid">("all");

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.paperOverview(30);
      setRows(data.portfolios || []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load overview");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const filtered = rows.filter((r) => {
    if (filter === "all") return true;
    if (filter === "paper" || filter === "real") return r.portfolio_type === filter;
    return r.mode === filter;
  });

  const sorted = [...filtered].sort((a, b) => {
    let av: string | number, bv: string | number;
    switch (sortKey) {
      case "id": av = a.id; bv = b.id; break;
      case "name": av = a.name; bv = b.name; break;
      case "trades": av = a.trades_count; bv = b.trades_count; break;
      case "pnl": av = a.net_pnl; bv = b.net_pnl; break;
      case "wr": av = a.win_rate; bv = b.win_rate; break;
    }
    if (av === bv) return 0;
    const cmp = av < bv ? -1 : 1;
    return sortDesc ? -cmp : cmp;
  });

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) setSortDesc(!sortDesc);
    else { setSortKey(k); setSortDesc(true); }
  };

  const totalPnl = rows.reduce((s, r) => s + r.net_pnl, 0);
  const totalTrades = rows.reduce((s, r) => s + r.trades_count, 0);
  const winners = rows.filter((r) => r.net_pnl > 0).length;
  const losers = rows.filter((r) => r.net_pnl < 0).length;
  const engineTrades = rows.reduce((s, r) => s + r.engine.trades, 0);
  const enginePnl = rows.reduce((s, r) => s + r.engine.pnl, 0);
  const portfoliosWithEngineFills = rows.filter((r) => r.engine.trades > 0).length;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold">Paper Trading Overview</h3>
          <HintTip text="Each portfolio's PnL and equity curve since the moment it was saved from the optimizer. Strategy column = what the strategy would have done (basket-at-t correct for hybrid). Engine column = what the live engine actually executed (Bybit fills for real, paper-sim fills for paper). Equity sparkline shows progression from $0 at save → current PnL." />
        </div>
        <div className="flex items-center gap-2">
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value as typeof filter)}
            className="text-xs bg-ag-bg-deep border border-ag-border rounded px-2 py-1"
          >
            <option value="all">All</option>
            <option value="paper">Paper only</option>
            <option value="real">Real only</option>
            <option value="hybrid">Hybrid only</option>
            <option value="non_hybrid">Non-hybrid only</option>
          </select>
          <button
            onClick={fetchData}
            disabled={loading}
            className="text-xs px-3 py-1 bg-ag-accent/15 text-ag-accent border border-ag-accent/30 rounded hover:bg-ag-accent/25 disabled:opacity-50"
          >
            {loading ? "Loading..." : "Reload"}
          </button>
        </div>
      </div>
      <div className="text-[10px] text-ag-text-muted opacity-70 mb-2">
        Performance since each portfolio was saved from the optimizer.
        Strategy = backtest replay (basket-at-t correct). Engine = actual live or paper-sim fills.
      </div>

      {/* Aggregate stats — split into Strategy (replay) vs Engine (actual fills) */}
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs mb-3 text-ag-text-muted">
        <div className="font-medium text-[10px] uppercase opacity-50 self-center">Strategy:</div>
        <div>
          <span className="opacity-70">Total PnL: </span>
          <span className={totalPnl >= 0 ? "text-ag-green font-medium" : "text-ag-red font-medium"}>
            ${totalPnl.toFixed(2)}
          </span>
        </div>
        <div>
          <span className="opacity-70">Trades: </span>
          <span className="font-medium text-ag-text">{totalTrades}</span>
        </div>
        <div>
          <span className="opacity-70">Winners/Losers: </span>
          <span className="font-medium text-ag-green">{winners}</span>
          <span className="opacity-70"> / </span>
          <span className="font-medium text-ag-red">{losers}</span>
        </div>
        <div className="font-medium text-[10px] uppercase opacity-50 self-center ml-3">Engine:</div>
        <div>
          <span className="opacity-70">Actual PnL: </span>
          <span className={enginePnl >= 0 ? "text-ag-green font-medium" : "text-ag-red font-medium"}>
            ${enginePnl.toFixed(2)}
          </span>
        </div>
        <div>
          <span className="opacity-70">Fills: </span>
          <span className="font-medium text-ag-text">{engineTrades}</span>
        </div>
        <div>
          <span className="opacity-70">Active: </span>
          <span className="font-medium text-ag-text">{portfoliosWithEngineFills}</span>
          <span className="opacity-70"> / {rows.length}</span>
        </div>
        <div className="ml-auto">
          <span className="opacity-70">Showing: </span>
          <span className="font-medium text-ag-text">{filtered.length} / {rows.length}</span>
        </div>
      </div>

      {error && <div className="text-xs text-ag-red mb-2">{error}</div>}

      {/* Header row — two-tier: top tier = column groups, bottom = sub-columns */}
      <div className="grid grid-cols-[50px_1fr_80px_120px_120px_120px_120px_140px] items-end gap-2 text-[10px] uppercase text-ag-text-muted px-2 pb-1 border-b border-ag-border/40">
        <div></div>
        <div></div>
        <div></div>
        <div className="text-center font-medium text-ag-accent/80 border-l border-ag-border/40 pl-2">Strategy (replay)</div>
        <div className="text-center font-medium text-purple-400/80 border-l border-ag-border/40 pl-2">Engine (actual fills)</div>
        <div className="text-right">Prev month</div>
        <div className="text-right">This month</div>
        <div>Equity</div>
      </div>
      <div className="grid grid-cols-[50px_1fr_80px_120px_120px_120px_120px_140px] items-center gap-2 text-[10px] uppercase text-ag-text-muted px-2 py-1 border-b border-ag-border/40">
        <button onClick={() => toggleSort("id")} className="text-left hover:text-ag-text">ID {sortKey === "id" ? (sortDesc ? "↓" : "↑") : ""}</button>
        <button onClick={() => toggleSort("name")} className="text-left hover:text-ag-text">Name {sortKey === "name" ? (sortDesc ? "↓" : "↑") : ""}</button>
        <div>Mode/Type</div>
        <div className="text-right border-l border-ag-border/40 pl-2">
          <button onClick={() => toggleSort("pnl")} className="hover:text-ag-text">PnL {sortKey === "pnl" ? (sortDesc ? "↓" : "↑") : ""}</button>
          <span className="opacity-50"> · </span>
          <button onClick={() => toggleSort("trades")} className="hover:text-ag-text">trades</button>
          <span className="opacity-50"> · WR</span>
        </div>
        <div className="text-right border-l border-ag-border/40 pl-2">PnL · trades · WR</div>
        <div className="text-right">PnL · trades · WR</div>
        <div className="text-right">PnL · trades · WR</div>
        <div>since activation</div>
      </div>

      {/* Rows */}
      <div className="divide-y divide-ag-border/30">
        {sorted.length === 0 && !loading && (
          <div className="text-xs text-ag-text-muted py-4 text-center">No portfolios match filter.</div>
        )}
        {sorted.map((r) => (
          <PortfolioRow key={r.id} row={r} />
        ))}
      </div>

      {sorted.some((r) => r.prev_month_label || r.current_month_label) && (
        <div className="text-[10px] text-ag-text-muted opacity-70 mt-1 px-2">
          Months computed by trade exit time. Win % = (winning trades / total trades) for that month.
          A "0/0" cell means the portfolio had no trades in that month.
        </div>
      )}
    </div>
  );
}

function PortfolioRow({ row }: { row: Row }) {
  const pnlColor = (n: number) =>
    n > 0 ? "text-ag-green" : n < 0 ? "text-ag-red" : "text-ag-text-muted";

  const compactCell = (
    pnl: number,
    trades: number,
    wr: number,
    leftBorder: boolean = false,
    emptyLabel: string = "0/0",
  ) => {
    if (trades === 0) {
      return (
        <div className={`text-right text-[10px] text-ag-text-muted opacity-50 ${leftBorder ? "border-l border-ag-border/40 pl-2" : ""}`}>
          {emptyLabel}
        </div>
      );
    }
    return (
      <div className={`text-right ${leftBorder ? "border-l border-ag-border/40 pl-2" : ""}`}>
        <div className={`text-xs font-medium tabular-nums ${pnlColor(pnl)}`}>
          ${pnl.toFixed(2)}
        </div>
        <div className="text-[10px] tabular-nums opacity-70">
          {trades}t · {wr.toFixed(0)}%
        </div>
      </div>
    );
  };

  if (row.needs_refresh) {
    return (
      <div className="grid grid-cols-[50px_1fr_80px_120px_120px_120px_120px_140px] items-center gap-2 px-2 py-1.5 text-xs hover:bg-ag-bg-deep/50">
        <div className="font-mono opacity-70">#{row.id}</div>
        <div className="truncate">{row.name}</div>
        <div className="flex flex-col text-[10px] gap-0.5">
          <span className={row.mode === "hybrid" ? "text-purple-400" : "text-ag-text-muted"}>{row.mode}</span>
          <span className="opacity-60">{row.portfolio_type}</span>
        </div>
        <div className="col-span-5 text-[10px] text-yellow-400 italic opacity-80 border-l border-ag-border/40 pl-2">
          No snapshot — click Refresh on this portfolio's row below to populate (or wait for Sun 02:00 NYT auto-refresh).
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[50px_1fr_80px_120px_120px_120px_120px_140px] items-center gap-2 px-2 py-1.5 text-xs hover:bg-ag-bg-deep/50">
      <div className="font-mono opacity-70">#{row.id}</div>
      <div className="truncate">{row.name}</div>
      <div className="flex flex-col text-[10px] gap-0.5">
        <span className={row.mode === "hybrid" ? "text-purple-400" : "text-ag-text-muted"}>{row.mode}</span>
        <span className="opacity-60">{row.portfolio_type}</span>
      </div>
      {/* Strategy (replay) */}
      {compactCell(row.net_pnl, row.trades_count, row.win_rate, true, "no replay")}
      {/* Engine (actual fills) */}
      {compactCell(row.engine.pnl, row.engine.trades, row.engine.win_rate, true, "no fills yet")}
      {/* Prev month */}
      {compactCell(row.prev_month.pnl, row.prev_month.trades, row.prev_month.win_rate, false, "0/0")}
      {/* This month */}
      {compactCell(row.current_month.pnl, row.current_month.trades, row.current_month.win_rate, false, "0/0")}
      {/* Sparkline since-save */}
      <div className="flex items-center justify-end">
        <Sparkline
          points={row.sparkline}
          pnl={row.net_pnl}
          activatedAt={row.activated_at}
        />
      </div>
    </div>
  );
}

function Sparkline({
  points,
  pnl,
  activatedAt,
}: {
  points: { t: string; pnl: number }[];
  pnl: number;
  activatedAt: string | null;
}) {
  // Compact "since save" date hint shown above the chart so the user
  // immediately knows the time origin of the curve.
  const sinceLabel = (() => {
    if (!activatedAt) return null;
    try {
      const d = new Date(activatedAt);
      const now = new Date();
      const days = Math.max(0, Math.floor((now.getTime() - d.getTime()) / 86400000));
      const monthShort = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
      return `since ${monthShort} (${days}d)`;
    } catch {
      return null;
    }
  })();

  if (!points || points.length < 2) {
    return (
      <div className="flex flex-col items-end gap-0.5">
        {sinceLabel && <span className="text-[9px] text-ag-text-muted opacity-50">{sinceLabel}</span>}
        <div className="text-[10px] text-ag-text-muted opacity-50">no trades</div>
      </div>
    );
  }
  const W = 130;
  const H = 32;
  const PAD = 1;
  const ys = points.map((p) => p.pnl);
  const minY = Math.min(0, ...ys);
  const maxY = Math.max(0, ...ys);
  const yRange = maxY - minY || 1;
  const path = points
    .map((p, i) => {
      const x = PAD + (i / (points.length - 1)) * (W - PAD * 2);
      const y = H - PAD - ((p.pnl - minY) / yRange) * (H - PAD * 2);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  // Zero baseline (helpful when PnL crosses zero)
  const zeroY = H - PAD - ((0 - minY) / yRange) * (H - PAD * 2);
  const stroke =
    pnl > 0 ? "rgb(74,222,128)" : pnl < 0 ? "rgb(248,113,113)" : "rgb(148,163,184)";
  // Filled area under (or above, for negative) the curve, alpha-tinted
  const fillPath =
    `${path} L${W - PAD},${zeroY.toFixed(1)} L${PAD},${zeroY.toFixed(1)} Z`;
  // Native browser tooltip with first/last PnL — quick hover hint
  const firstPnl = points[0].pnl;
  const lastPnl = points[points.length - 1].pnl;
  const title = `From $${firstPnl.toFixed(2)} → $${lastPnl.toFixed(2)} (${points.length} trades)`;
  return (
    <div className="flex flex-col items-end gap-0.5" title={title}>
      {sinceLabel && (
        <span className="text-[9px] text-ag-text-muted opacity-60 leading-none">
          {sinceLabel}
        </span>
      )}
      <svg width={W} height={H} className="overflow-visible">
        {minY < 0 && maxY > 0 && (
          <line
            x1={PAD} x2={W - PAD} y1={zeroY} y2={zeroY}
            stroke="rgb(148,163,184)" strokeOpacity={0.25} strokeDasharray="2,2"
          />
        )}
        <path d={fillPath} fill={stroke} fillOpacity={0.12} stroke="none" />
        <path d={path} fill="none" stroke={stroke} strokeWidth="1.4" strokeLinecap="round" />
        {/* Endpoint dot for "current" position */}
        <circle
          cx={W - PAD}
          cy={H - PAD - ((lastPnl - minY) / yRange) * (H - PAD * 2)}
          r="1.8"
          fill={stroke}
        />
      </svg>
    </div>
  );
}
