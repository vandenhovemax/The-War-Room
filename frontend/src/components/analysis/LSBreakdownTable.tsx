"use client";

import React, { useState, useEffect, useMemo } from "react";
import { ChevronDown, ChevronRight, TrendingUp, TrendingDown } from "lucide-react";
import { api } from "@/lib/api";
import type { DashboardAnalysisRequest, LSBreakdownSymbol, BacktestTradeItem } from "@/lib/api";
import TradeChartButton from "@/components/common/TradeChartButton";
import TradeChartPopup from "@/components/common/TradeChartPopup";
import type { TradeChartData } from "@/components/common/TradeChartPopup";

interface Props {
  req: DashboardAnalysisRequest;
  timeframe?: string;
  strategyId?: number;
}

type SortKey = "symbol" | "total_pnl" | "total_trades"
  | "long_pnl" | "long_pf" | "long_wr" | "long_trades"
  | "short_pnl" | "short_pf" | "short_wr" | "short_trades"
  | "regime" | "trend_grade" | "meanrev_grade" | "hurst"
  | "avg_consec_bull" | "avg_consec_bear" | "avg_consec_all"
  | "risk_mode";

function fmt$(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPf(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(2);
}

function valColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  return v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";
}

function pfColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  if (v >= 2.0) return "text-ag-green";
  if (v >= 1.5) return "text-ag-green/80";
  if (v >= 1.0) return "text-ag-text";
  return "text-ag-red";
}

const GRADE_ORDER: Record<string, number> = { "A+": 6, A: 5, "B+": 4.5, B: 4, "C+": 3.5, C: 3, D: 2, F: 1 };

function getSortVal(row: LSBreakdownSymbol, key: SortKey): number | string {
  switch (key) {
    case "symbol": return row.symbol;
    case "total_pnl": return row.total_pnl;
    case "total_trades": return row.total_trades;
    case "long_pnl": return row.long?.net_profit ?? -Infinity;
    case "long_pf": return row.long?.profit_factor ?? -Infinity;
    case "long_wr": return row.long?.win_rate ?? -Infinity;
    case "long_trades": return row.long?.trades ?? 0;
    case "short_pnl": return row.short?.net_profit ?? -Infinity;
    case "short_pf": return row.short?.profit_factor ?? -Infinity;
    case "short_wr": return row.short?.win_rate ?? -Infinity;
    case "short_trades": return row.short?.trades ?? 0;
    case "regime": return row.structural?.regime ?? "";
    case "trend_grade": return GRADE_ORDER[row.structural?.trend_grade ?? ""] ?? 0;
    case "meanrev_grade": return GRADE_ORDER[row.structural?.meanrev_grade ?? ""] ?? 0;
    case "hurst": return row.structural?.Hurst ?? 0;
    case "avg_consec_bull": return row.structural?.avg_consec_bull ?? 0;
    case "avg_consec_bear": return row.structural?.avg_consec_bear ?? 0;
    case "avg_consec_all": return row.structural?.avg_consec_all ?? 0;
    case "risk_mode": return row.risk_mode ?? "";
    default: return 0;
  }
}

export default function LSBreakdownTable({ req, timeframe, strategyId }: Props) {
  const [data, setData] = useState<LSBreakdownSymbol[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("total_pnl");
  const [sortAsc, setSortAsc] = useState(false);
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);
  const [expandedTrades, setExpandedTrades] = useState<BacktestTradeItem[]>([]);
  const [tradesLoading, setTradesLoading] = useState(false);

  useEffect(() => {
    if (!req.batch_id && !req.portfolio_id) return;
    setLoading(true);
    setError("");
    api.dashboardBatchLSBreakdown(req)
      .then((r) => setData(r.symbols))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }, [req.batch_id, req.portfolio_id, req.side_filter, req.date_to, req.min_trades]);

  const sorted = useMemo(() => {
    const copy = [...data];
    copy.sort((a, b) => {
      const va = getSortVal(a, sortKey);
      const vb = getSortVal(b, sortKey);
      if (typeof va === "string" && typeof vb === "string") {
        return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      return sortAsc
        ? (va as number) - (vb as number)
        : (vb as number) - (va as number);
    });
    return copy;
  }, [data, sortKey, sortAsc]);

  const hasLong = data.some((r) => r.long !== null);
  const hasShort = data.some((r) => r.short !== null);
  const hasRiskMode = data.some((r) => r.risk_mode);

  // Auto-zoom Trend/MR grades: when all assets share the same grade, normalize
  // using raw scores to spread them into differentiated grades.
  const zoomGrades = useMemo(() => {
    const map: Record<string, { trend: string; mr: string }> = {};
    const hasStruct = data.filter(r => r.structural);
    if (hasStruct.length < 2) return { active: false, map };

    const trendGrades = hasStruct.map(r => r.structural!.trend_grade ?? "").filter(Boolean);
    const mrGrades = hasStruct.map(r => r.structural!.meanrev_grade ?? "").filter(Boolean);
    const allSameTrend = trendGrades.length >= 2 && new Set(trendGrades).size === 1;
    const allSameMr = mrGrades.length >= 2 && new Set(mrGrades).size === 1;

    if (!allSameTrend && !allSameMr) return { active: false, map };

    // Zoom normalization: map scores to 0-1 range, then re-grade
    const toGrade = (score: number): string => {
      if (score >= 0.85) return "A+";
      if (score >= 0.70) return "A";
      if (score >= 0.58) return "B+";
      if (score >= 0.45) return "B";
      if (score >= 0.32) return "C+";
      if (score >= 0.20) return "C";
      if (score >= 0.10) return "D";
      return "F";
    };
    const zoomNorm = (score: number, all: number[]): number => {
      const lo = Math.min(...all);
      const hi = Math.max(...all);
      if (hi <= lo) return 0.15;
      return (score - lo) / (hi - lo);
    };

    const trendScores = hasStruct.map(r => r.structural!.trend_score ?? 0);
    const mrScores = hasStruct.map(r => r.structural!.meanrev_score ?? 0);
    const hasScores = trendScores.some(v => v > 0) || mrScores.some(v => v > 0);
    if (!hasScores) return { active: false, map };

    for (const r of hasStruct) {
      const ts = r.structural!.trend_score ?? 0;
      const ms = r.structural!.meanrev_score ?? 0;
      map[r.symbol] = {
        trend: allSameTrend && ts > 0 ? toGrade(zoomNorm(ts, trendScores)) : (r.structural!.trend_grade ?? ""),
        mr: allSameMr && ms > 0 ? toGrade(zoomNorm(ms, mrScores)) : (r.structural!.meanrev_grade ?? ""),
      };
    }
    return { active: allSameTrend || allSameMr, map };
  }, [data]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  const arrow = (key: SortKey) =>
    sortKey === key ? (sortAsc ? " \u25B2" : " \u25BC") : "";

  const handleExpand = async (symbol: string) => {
    if (expandedSymbol === symbol) {
      setExpandedSymbol(null);
      return;
    }
    if (!req.batch_id && !req.portfolio_id) return;
    setExpandedSymbol(symbol);
    setTradesLoading(true);
    try {
      const trades = await api.dashboardBatchSymbolTrades(symbol, {
        batch_id: req.batch_id,
        portfolio_id: req.portfolio_id,
      });
      setExpandedTrades(trades);
    } catch {
      setExpandedTrades([]);
    }
    setTradesLoading(false);
  };

  if (loading) {
    return (
      <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted">
        Loading L/S breakdown...
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
  if (!data.length) return null;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-ag-border text-ag-text-muted bg-ag-darker">
              <Th label="Symbol" sortKey="symbol" cur={sortKey} arrow={arrow} onClick={handleSort} tooltip="Trading pair symbol" />
              <Th label="Total PnL" sortKey="total_pnl" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Total net profit/loss across all sides" />
              <Th label="Positions" sortKey="total_trades" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Total number of closed positions" />
              {hasRiskMode && (
                <Th label="Risk" sortKey="risk_mode" cur={sortKey} arrow={arrow} onClick={handleSort} tooltip="Auto-risk mode applied: F = Fixed, M = Martingale, AM = Anti-Martingale" />
              )}
              <Th label="Regime" sortKey="regime" cur={sortKey} arrow={arrow} onClick={handleSort} tooltip="Price regime: TREND, MEANREV, or NEUTRAL" />
              <Th label={zoomGrades.active ? "Trend*" : "Trend"} sortKey="trend_grade" cur={sortKey} arrow={arrow} onClick={handleSort}
                tooltip={zoomGrades.active ? "Trend grade (auto-zoom: all assets had same grade, rescaled by raw score)" : "Trend-following suitability grade (A = best, F = worst)"} />
              <Th label={zoomGrades.active ? "MR*" : "MR"} sortKey="meanrev_grade" cur={sortKey} arrow={arrow} onClick={handleSort}
                tooltip={zoomGrades.active ? "MR grade (auto-zoom: all assets had same grade, rescaled by raw score)" : "Mean-reversion suitability grade (A = best, F = worst)"} />
              <Th label="Hurst" sortKey="hurst" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Hurst exponent: >0.5 = trending, 0.5 = random walk, <0.5 = mean-reverting" />
              <Th label="Avg Bull" sortKey="avg_consec_bull" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Average consecutive bull (long) ranges from market structure detection" />
              <Th label="Avg Bear" sortKey="avg_consec_bear" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Average consecutive bear (short) ranges from market structure detection" />
              <Th label="Avg L+S" sortKey="avg_consec_all" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Average of bull + bear consecutive ranges (combined)" />
              {hasLong && (
                <>
                  <th className="px-2 py-1.5 font-medium text-center border-l border-ag-border text-ag-green/70" colSpan={4}>
                    LONG
                  </th>
                </>
              )}
              {hasShort && (
                <>
                  <th className="px-2 py-1.5 font-medium text-center border-l border-ag-border text-ag-red/70" colSpan={4}>
                    SHORT
                  </th>
                </>
              )}
            </tr>
            <tr className="border-b border-ag-border text-ag-text-muted bg-ag-darker/50">
              <th className="px-3 py-1" />
              <th className="px-2 py-1" />
              <th className="px-2 py-1" />
              <th className="px-2 py-1" />
              <th className="px-2 py-1" />
              <th className="px-2 py-1" />
              <th className="px-2 py-1" />
              <th className="px-2 py-1" />
              <th className="px-2 py-1" />
              <th className="px-2 py-1" />
              {hasLong && (
                <>
                  <Th label="PnL" sortKey="long_pnl" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" border tooltip="Long side net profit/loss" />
                  <Th label="PF" sortKey="long_pf" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Long side Profit Factor" />
                  <Th label="WR%" sortKey="long_wr" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Long side Win Rate %" />
                  <Th label="#" sortKey="long_trades" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Long side trade count" />
                </>
              )}
              {hasShort && (
                <>
                  <Th label="PnL" sortKey="short_pnl" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" border tooltip="Short side net profit/loss" />
                  <Th label="PF" sortKey="short_pf" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Short side Profit Factor" />
                  <Th label="WR%" sortKey="short_wr" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Short side Win Rate %" />
                  <Th label="#" sortKey="short_trades" cur={sortKey} arrow={arrow} onClick={handleSort} align="right" tooltip="Short side trade count" />
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => {
              const isExpanded = expandedSymbol === row.symbol;
              const colSpan = 10 + (hasRiskMode ? 1 : 0) + (hasLong ? 4 : 0) + (hasShort ? 4 : 0);
              return (
                <SymbolRow key={row.symbol} row={row} isExpanded={isExpanded}
                  hasLong={hasLong} hasShort={hasShort} hasRiskMode={hasRiskMode} colSpan={colSpan}
                  tradesLoading={tradesLoading} expandedTrades={expandedTrades}
                  timeframe={timeframe} strategyId={strategyId}
                  onToggle={() => handleExpand(row.symbol)}
                  zoomTrend={zoomGrades.map[row.symbol]?.trend}
                  zoomMr={zoomGrades.map[row.symbol]?.mr} />
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const RISK_MODE_LABEL: Record<string, { text: string; cls: string }> = {
  anti_martingale: { text: "AM", cls: "bg-green-500/20 text-green-400" },
  martingale: { text: "M", cls: "bg-orange-500/20 text-orange-400" },
  inv_martingale: { text: "iM", cls: "bg-orange-500/20 text-orange-400" },
  inv_anti_martingale: { text: "iAM", cls: "bg-green-500/20 text-green-400" },
  fixed: { text: "F", cls: "bg-gray-500/20 text-gray-400" },
};

function SymbolRow({ row, isExpanded, hasLong, hasShort, hasRiskMode, colSpan, tradesLoading, expandedTrades, timeframe, strategyId, onToggle, zoomTrend, zoomMr }: {
  row: LSBreakdownSymbol; isExpanded: boolean;
  hasLong: boolean; hasShort: boolean; hasRiskMode: boolean; colSpan: number;
  tradesLoading: boolean; expandedTrades: BacktestTradeItem[];
  timeframe?: string; strategyId?: number;
  onToggle: () => void;
  zoomTrend?: string; zoomMr?: string;
}) {
  const [expandedTradeId, setExpandedTradeId] = useState<string | null>(null);
  const [chartTrade, setChartTrade] = useState<TradeChartData | null>(null);

  const openChart = (t: BacktestTradeItem) => {
    const lastExit = t.exits[t.exits.length - 1];
    setChartTrade({
      symbol: row.symbol,
      timeframe: timeframe || "30",
      side: t.side,
      entryTime: t.entry_time || "",
      exitTime: lastExit?.exit_time || null,
      entryPrice: t.entry_price,
      exitPrice: lastExit?.exit_price,
      slPrice: t.sl_price,
      tpPrice: t.tp_price,
      netProfit: t.net_profit,
      strategyId,
    });
  };

  const fmtDate = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "short", day: "numeric", year: "2-digit",
      hour: "2-digit", minute: "2-digit",
    }) : "—";

  const fmtPrice = (v: number) => v.toFixed(v >= 1 ? 4 : 6);

  return (
    <>
      <tr onClick={onToggle}
        className="border-b border-ag-border/50 hover:bg-ag-darker/30 cursor-pointer select-none">
        <td className="px-3 py-1.5 font-medium">
          <span className="inline-flex items-center gap-1">
            {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            {row.symbol}
          </span>
        </td>
        <td className={`text-right px-2 py-1.5 ${valColor(row.total_pnl)}`}>{fmt$(row.total_pnl)}</td>
        <td className="text-right px-2 py-1.5">{row.total_trades}</td>
        {hasRiskMode && (
          <td className="px-2 py-1.5 text-center">
            {row.risk_mode ? (() => {
              const rm = RISK_MODE_LABEL[row.risk_mode] || { text: row.risk_mode, cls: "bg-gray-500/20 text-gray-400" };
              return <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${rm.cls}`}>{rm.text}</span>;
            })() : "—"}
          </td>
        )}
        <td className="px-2 py-1.5 text-center">
          {row.structural?.regime && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              row.structural.regime === "TREND" ? "bg-green-500/15 text-green-400"
              : row.structural.regime === "MEANREV" ? "bg-blue-500/15 text-blue-400"
              : "bg-gray-500/15 text-gray-400"
            }`}>{row.structural.regime}</span>
          )}
        </td>
        <td className="px-2 py-1.5 text-center text-[10px] font-medium">{zoomTrend ?? row.structural?.trend_grade ?? ""}</td>
        <td className="px-2 py-1.5 text-center text-[10px] font-medium">{zoomMr ?? row.structural?.meanrev_grade ?? ""}</td>
        <td className={`px-2 py-1.5 text-right text-[10px] ${
          (row.structural?.Hurst ?? 0) > 0.55 ? "text-green-400" : (row.structural?.Hurst ?? 0) >= 0.50 ? "text-yellow-400" : (row.structural?.Hurst ?? 0) > 0 ? "text-red-400" : ""
        }`}>{row.structural?.Hurst != null ? row.structural.Hurst.toFixed(3) : ""}</td>
        <td className="px-2 py-1.5 text-right text-[10px]">{row.structural?.avg_consec_bull != null && row.structural.avg_consec_bull > 0 ? row.structural.avg_consec_bull.toFixed(2) : ""}</td>
        <td className="px-2 py-1.5 text-right text-[10px]">{row.structural?.avg_consec_bear != null && row.structural.avg_consec_bear > 0 ? row.structural.avg_consec_bear.toFixed(2) : ""}</td>
        <td className="px-2 py-1.5 text-right text-[10px]">{row.structural?.avg_consec_all != null && row.structural.avg_consec_all > 0 ? row.structural.avg_consec_all.toFixed(2) : ""}</td>
        {hasLong && (
          <>
            <td className={`text-right px-2 py-1.5 border-l border-ag-border/30 ${valColor(row.long?.net_profit)}`}>
              {row.long ? fmt$(row.long.net_profit) : "—"}
            </td>
            <td className={`text-right px-2 py-1.5 ${pfColor(row.long?.profit_factor)}`}>
              {row.long ? fmtPf(row.long.profit_factor) : "—"}
            </td>
            <td className="text-right px-2 py-1.5">{row.long ? `${row.long.win_rate.toFixed(1)}%` : "—"}</td>
            <td className="text-right px-2 py-1.5">{row.long?.trades ?? "—"}</td>
          </>
        )}
        {hasShort && (
          <>
            <td className={`text-right px-2 py-1.5 border-l border-ag-border/30 ${valColor(row.short?.net_profit)}`}>
              {row.short ? fmt$(row.short.net_profit) : "—"}
            </td>
            <td className={`text-right px-2 py-1.5 ${pfColor(row.short?.profit_factor)}`}>
              {row.short ? fmtPf(row.short.profit_factor) : "—"}
            </td>
            <td className="text-right px-2 py-1.5">{row.short ? `${row.short.win_rate.toFixed(1)}%` : "—"}</td>
            <td className="text-right px-2 py-1.5">{row.short?.trades ?? "—"}</td>
          </>
        )}
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={colSpan} className="bg-ag-dark/40 px-4 py-3">
            {tradesLoading ? (
              <p className="text-xs text-ag-text-muted">Loading positions...</p>
            ) : expandedTrades.length === 0 ? (
              <p className="text-xs text-ag-text-muted">No positions found.</p>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-ag-text-muted border-b border-ag-border/30">
                    <th className="text-left py-1 px-2">#</th>
                    {expandedTrades.some((t) => t.strategy_name) && (
                      <th className="text-left py-1 px-2">Strategy</th>
                    )}
                    <th className="text-left py-1 px-2">Side</th>
                    <th className="text-left py-1 px-2">Entry</th>
                    <th className="text-right py-1 px-2">Entry Price</th>
                    <th className="text-center py-1 px-2">Exit</th>
                    {expandedTrades.some((t) => t.size_pct != null && t.size_pct !== 100) && (
                      <th className="text-right py-1 px-2">Size</th>
                    )}
                    <th className="text-right py-1 px-2">P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {expandedTrades.map((t) => {
                    const isTradeExpanded = expandedTradeId === t.position_id;
                    const toggleTrade = () => setExpandedTradeId(isTradeExpanded ? null : t.position_id);
                    return (
                      <React.Fragment key={t.position_id}>
                        <tr
                          onClick={toggleTrade}
                          className="border-b border-ag-border/20 hover:bg-ag-darker/20 cursor-pointer"
                        >
                          <td className="py-1 px-2 text-ag-text-muted">{t.trade_num}</td>
                          {expandedTrades.some((tr) => tr.strategy_name) && (
                            <td className="py-1 px-2 text-ag-text-muted max-w-[140px] truncate" title={`${t.strategy_name || ""}${t.config_name ? ` — ${t.config_name}` : ""}`}>
                              {t.strategy_name || "—"}
                              {t.config_name && <span className="text-ag-text-muted/60"> — {t.config_name}</span>}
                            </td>
                          )}
                          <td className="py-1 px-2">
                            <span className={`inline-flex items-center gap-0.5 ${t.side === "long" ? "text-ag-green" : "text-ag-red"}`}>
                              {t.side === "long" ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                              {t.side}
                            </span>
                          </td>
                          <td className="py-1 px-2 text-ag-text-muted">{fmtDate(t.entry_time)}</td>
                          <td className="py-1 px-2 text-right">{fmtPrice(t.entry_price)}</td>
                          <td className="py-1 px-2 text-center text-ag-text-muted">
                            <span className="inline-flex items-center gap-0.5">
                              {isTradeExpanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                              {t.exit_count > 1 ? `${t.exit_count} exits` : (t.exits[0]?.exit_reason ?? "—")}
                            </span>
                          </td>
                          {expandedTrades.some((tr) => tr.size_pct != null && tr.size_pct !== 100) && (
                            <td className={`py-1 px-2 text-right ${
                              (t.size_pct ?? 100) > 100 ? "text-ag-green" : (t.size_pct ?? 100) < 100 ? "text-ag-red" : "text-ag-text-muted"
                            }`}>
                              {(t.size_pct ?? 100).toFixed(0)}%
                            </td>
                          )}
                          <td className={`py-1 px-2 text-right font-medium ${t.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                            ${t.net_profit.toFixed(2)}
                            {timeframe && <TradeChartButton onClick={() => openChart(t)} />}
                          </td>
                        </tr>
                        {isTradeExpanded && t.exits.length > 0 && (
                          <tr>
                            <td colSpan={6 + (expandedTrades.some((tr) => tr.strategy_name) ? 1 : 0) + (expandedTrades.some((tr) => tr.size_pct != null && tr.size_pct !== 100) ? 1 : 0)} className="bg-ag-darker/50 px-6 py-2">
                              <table className="w-full text-[11px]">
                                <thead>
                                  <tr className="text-ag-text-muted/70 border-b border-ag-border/20">
                                    <th className="text-left py-0.5 px-1.5">Exit Time</th>
                                    <th className="text-right py-0.5 px-1.5">Exit Price</th>
                                    <th className="text-right py-0.5 px-1.5">Qty</th>
                                    <th className="text-right py-0.5 px-1.5">P&L</th>
                                    <th className="text-left py-0.5 px-1.5">Reason</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {t.exits.map((ex, i) => (
                                    <tr key={i} className="border-b border-ag-border/10">
                                      <td className="py-0.5 px-1.5 text-ag-text-muted">{fmtDate(ex.exit_time)}</td>
                                      <td className="py-0.5 px-1.5 text-right">{fmtPrice(ex.exit_price)}</td>
                                      <td className="py-0.5 px-1.5 text-right text-ag-text-muted">{ex.quantity.toFixed(4)}</td>
                                      <td className={`py-0.5 px-1.5 text-right font-medium ${ex.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                                        ${ex.net_profit.toFixed(2)}
                                      </td>
                                      <td className="py-0.5 px-1.5 text-ag-text-muted">{ex.exit_reason || "—"}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            )}
          </td>
        </tr>
      )}
      {chartTrade && (
        <TradeChartPopup trade={chartTrade} onClose={() => setChartTrade(null)} />
      )}
    </>
  );
}

function Th({ label, sortKey, cur, arrow, onClick, align, border, tooltip }: {
  label: string;
  sortKey: SortKey;
  cur: SortKey;
  arrow: (k: SortKey) => string;
  onClick: (k: SortKey) => void;
  align?: "right" | "left";
  border?: boolean;
  tooltip?: string;
}) {
  return (
    <th
      className={`px-2 py-1.5 font-medium cursor-pointer hover:text-ag-text select-none ${align === "right" ? "text-right" : "text-left"} ${border ? "border-l border-ag-border/30" : ""}`}
      onClick={() => onClick(sortKey)}
      title={tooltip}
    >
      {label}{arrow(sortKey)}
    </th>
  );
}
