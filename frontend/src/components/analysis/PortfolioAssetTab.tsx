"use client";
import { useState, useEffect, useMemo } from "react";
import { ChevronDown, ChevronRight, Scissors } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioAssetSummary } from "@/lib/api";
import AssetTradeList from "./AssetTradeList";

interface Props {
  portfolioId: number;
  timeframe?: string;
  strategyId?: number;
}

type SortKey = "asset" | "strategy_name" | "config_name" | "direction" | "net_profit" | "profit_factor" | "win_rate" | "trades" | "avg_trade" | "max_drawdown"
  | "regime" | "trend_grade" | "meanrev_grade" | "hurst" | "avg_consec_bull" | "avg_consec_bear" | "avg_consec_all";

const numColor = (v: number) => (v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "");

const fmtDollar = (v: number) =>
  `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

type FreshnessMap = Record<string, { is_fresh: boolean; candles_behind: number; latest_candle: string | null }>;

export default function PortfolioAssetTab({ portfolioId, timeframe, strategyId }: Props) {
  const [assets, setAssets] = useState<PortfolioAssetSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedAsset, setExpandedAsset] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("net_profit");
  const [sortAsc, setSortAsc] = useState(false);
  const [freshness, setFreshness] = useState<FreshnessMap>({});
  const [hasWfoBoundary, setHasWfoBoundary] = useState(false);
  const [costSettings, setCostSettings] = useState<{ extra_slippage_pct: number; extra_commission_pct: number; funding_daily_pct: number; spread_pct: number } | null>(null);
  const [bybitRejections, setBybitRejections] = useState<Record<string, number> | null>(null);

  useEffect(() => {
    setLoading(true);
    setError("");
    api.portfolioAssets(portfolioId)
      .then((r) => {
        setAssets(r.assets);
        setHasWfoBoundary(r.has_wfo_boundary ?? false);
        setCostSettings((r as Record<string, unknown>).cost_settings as typeof costSettings ?? null);
        setBybitRejections(r.bybit_rejections ?? null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
    // Fetch data freshness separately (non-blocking)
    api.portfolioDataFreshness(portfolioId)
      .then((r) => {
        const map: FreshnessMap = {};
        for (const a of r.assets) {
          map[a.symbol] = { is_fresh: a.is_fresh, candles_behind: a.candles_behind, latest_candle: a.latest_candle };
        }
        setFreshness(map);
      })
      .catch(() => {}); // silent — freshness is optional
  }, [portfolioId]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(key === "asset" || key === "strategy_name"); }
  };

  const GRADE_ORD: Record<string, number> = { A: 5, B: 4, C: 3, D: 2, F: 1 };

  const sorted = useMemo(() => {
    const copy = [...assets];
    copy.sort((a, b) => {
      let av: string | number;
      let bv: string | number;
      switch (sortKey) {
        case "regime": av = a.structural?.regime ?? ""; bv = b.structural?.regime ?? ""; break;
        case "trend_grade": av = GRADE_ORD[a.structural?.trend_grade ?? ""] ?? 0; bv = GRADE_ORD[b.structural?.trend_grade ?? ""] ?? 0; break;
        case "meanrev_grade": av = GRADE_ORD[a.structural?.meanrev_grade ?? ""] ?? 0; bv = GRADE_ORD[b.structural?.meanrev_grade ?? ""] ?? 0; break;
        case "hurst": av = a.structural?.Hurst ?? 0; bv = b.structural?.Hurst ?? 0; break;
        case "avg_consec_bull": av = a.structural?.avg_consec_bull ?? 0; bv = b.structural?.avg_consec_bull ?? 0; break;
        case "avg_consec_bear": av = a.structural?.avg_consec_bear ?? 0; bv = b.structural?.avg_consec_bear ?? 0; break;
        case "avg_consec_all": av = a.structural?.avg_consec_all ?? 0; bv = b.structural?.avg_consec_all ?? 0; break;
        default: av = (a as unknown as Record<string, string | number>)[sortKey] ?? 0; bv = (b as unknown as Record<string, string | number>)[sortKey] ?? 0;
      }
      if (typeof av === "string" && typeof bv === "string") return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortAsc ? Number(av) - Number(bv) : Number(bv) - Number(av);
    });
    return copy;
  }, [assets, sortKey, sortAsc]);

  if (loading) return <p className="text-xs text-ag-text-muted py-2">Loading assets...</p>;
  if (error) return <p className="text-xs text-ag-red py-2">{error}</p>;
  if (assets.length === 0) return <p className="text-xs text-ag-text-muted py-2">No asset data available.</p>;

  const totals = {
    net: assets.reduce((s, a) => s + a.net_profit, 0),
    trades: assets.reduce((s, a) => s + a.trades, 0),
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Per-Asset Breakdown</h3>
        <span className="text-[10px] text-ag-text-muted">
          {assets.length} assets · {totals.trades} trades · Net: <span className={numColor(totals.net)}>{fmtDollar(totals.net)}</span>
        </span>
      </div>

      {costSettings && (
        <div className="flex flex-wrap gap-3 text-[10px] text-ag-text-muted px-3 py-1.5 bg-ag-dark/20 rounded border border-ag-border/20">
          <span className="font-medium text-ag-text">Stress costs applied to all metrics:</span>
          {costSettings.extra_slippage_pct > 0 && <span>Slippage {costSettings.extra_slippage_pct}%</span>}
          {costSettings.extra_commission_pct > 0 && <span>Commission {costSettings.extra_commission_pct}%</span>}
          {costSettings.funding_daily_pct > 0 && <span>Funding {costSettings.funding_daily_pct}%/day</span>}
          {costSettings.spread_pct > 0 && <span>Spread {costSettings.spread_pct}%</span>}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-ag-text-muted border-b border-ag-border">
              <th className="w-6" />
              {COLUMNS.map((c) => (
                <th key={c.key} onClick={() => handleSort(c.key)} title={c.tooltip}
                  className={`py-1.5 px-2 font-medium cursor-pointer hover:text-ag-text transition-colors ${c.align === "right" ? "text-right" : "text-left"}`}>
                  {c.label} {sortKey === c.key && (sortAsc ? "\u25B2" : "\u25BC")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((a) => {
              const expanded = expandedAsset === a.asset;
              // Match asset name to freshness data (strip _Long/_Short suffix to get symbol)
              const sym = a.asset.replace(/_Long$/, "").replace(/_Short$/, "");
              const fresh = freshness[sym];
              return (
                <AssetRow key={a.asset} asset={a} expanded={expanded} portfolioId={portfolioId}
                  freshness={fresh} hasWfoBoundary={hasWfoBoundary} timeframe={timeframe} strategyId={strategyId}
                  onToggle={() => setExpandedAsset(expanded ? null : a.asset)} />
              );
            })}
          </tbody>
        </table>
      </div>

      {bybitRejections && Object.keys(bybitRejections).length > 0 && (() => {
        const totalRej = Object.values(bybitRejections).reduce((s, v) => s + v, 0);
        const assetCount = Object.keys(bybitRejections).length;
        return (
          <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-4 py-2.5 text-xs text-yellow-300">
            {totalRej} trade(s) across {assetCount} asset(s) were rejected — position size below Bybit&apos;s minimum order quantity
          </div>
        );
      })()}
    </div>
  );
}

const COLUMNS: { key: SortKey; label: string; align: "left" | "right"; tooltip?: string }[] = [
  { key: "asset", label: "Asset", align: "left", tooltip: "Trading pair symbol" },
  { key: "strategy_name", label: "Strategy", align: "left", tooltip: "Strategy name used for this asset" },
  { key: "config_name", label: "Config", align: "left", tooltip: "Configuration/parameter set name" },
  { key: "direction", label: "Dir", align: "left", tooltip: "Trade direction: Long or Short" },
  { key: "net_profit", label: "Net Profit", align: "right", tooltip: "Total net profit/loss for this asset" },
  { key: "profit_factor", label: "PF", align: "right", tooltip: "Profit Factor — gross profit / gross loss. >1.5 good, >2.0 great" },
  { key: "win_rate", label: "WR%", align: "right", tooltip: "Win Rate — percentage of profitable trades" },
  { key: "trades", label: "Trades", align: "right", tooltip: "Total number of closed positions" },
  { key: "avg_trade", label: "Avg Trade", align: "right", tooltip: "Average profit/loss per trade" },
  { key: "max_drawdown", label: "Max DD", align: "right", tooltip: "Maximum peak-to-trough equity drawdown" },
  { key: "regime", label: "Regime", align: "left", tooltip: "Price regime: TREND, MEANREV, or NEUTRAL" },
  { key: "trend_grade", label: "Trend", align: "left", tooltip: "Trend-following suitability grade (A = best, F = worst)" },
  { key: "meanrev_grade", label: "MR", align: "left", tooltip: "Mean-reversion suitability grade (A = best, F = worst)" },
  { key: "hurst", label: "Hurst", align: "right", tooltip: "Hurst exponent: >0.5 = trending, 0.5 = random walk, <0.5 = mean-reverting" },
  { key: "avg_consec_bull", label: "Avg Bull", align: "right", tooltip: "Average consecutive bull (long) ranges from market structure detection" },
  { key: "avg_consec_bear", label: "Avg Bear", align: "right", tooltip: "Average consecutive bear (short) ranges from market structure detection" },
  { key: "avg_consec_all", label: "Avg L+S", align: "right", tooltip: "Average of bull + bear consecutive ranges (combined)" },
];

function AssetRow({ asset, expanded, portfolioId, freshness, hasWfoBoundary, timeframe, strategyId, onToggle }: {
  asset: PortfolioAssetSummary; expanded: boolean; portfolioId: number;
  freshness?: { is_fresh: boolean; candles_behind: number; latest_candle: string | null };
  hasWfoBoundary: boolean; timeframe?: string; strategyId?: number;
  onToggle: () => void;
}) {
  const dirColor = asset.direction === "Long" ? "text-ag-green" : asset.direction === "Short" ? "text-ag-red" : "";

  // Freshness dot: green = fresh, yellow = slightly stale (3-10 candles), red = very stale or no data
  let dotColor = "bg-ag-text-muted/30"; // default: unknown
  let dotTitle = "Data freshness unknown";
  if (freshness) {
    if (freshness.candles_behind < 0) {
      dotColor = "bg-ag-red";
      dotTitle = "No candle data";
    } else if (freshness.is_fresh) {
      dotColor = "bg-ag-green";
      dotTitle = `Up to date (${freshness.candles_behind.toFixed(0)} candles behind)`;
    } else if (freshness.candles_behind < 10) {
      dotColor = "bg-yellow-400";
      dotTitle = `Slightly stale (${freshness.candles_behind.toFixed(0)} candles behind)`;
    } else {
      dotColor = "bg-ag-red";
      dotTitle = `Stale (${freshness.candles_behind.toFixed(0)} candles behind)`;
    }
  }

  // Boundary straddle tooltip (trades that crossed IS/OOS or OOS-end boundary)
  let straddleTitle = "";
  if (asset.has_boundary_straddles) {
    const parts: string[] = [];
    if (asset.is_boundary_straddles > 0)
      parts.push(`${asset.is_boundary_straddles} trade(s) straddled IS/OOS boundary`);
    if (asset.oos_boundary_straddles > 0)
      parts.push(`${asset.oos_boundary_straddles} trade(s) straddled OOS end boundary`);
    straddleTitle = parts.join(" | ");
  }

  return (
    <>
      <tr onClick={onToggle} className="border-b border-ag-border/20 hover:bg-ag-dark/30 cursor-pointer">
        <td className="py-1.5 px-1">
          {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </td>
        <td className="py-1.5 px-2 font-medium">
          <span className="flex items-center gap-1.5">
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${dotColor} shrink-0`} title={dotTitle} />
            {hasWfoBoundary && asset.has_boundary_straddles && (
              <span title={straddleTitle}><Scissors size={11} className="text-amber-400 shrink-0" /></span>
            )}
            {asset.asset.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")}
          </span>
        </td>
        <td className="py-1.5 px-2 text-ag-text-muted">{asset.strategy_name}</td>
        <td className="py-1.5 px-2 text-ag-text-muted text-[10px] max-w-[120px] truncate" title={asset.config_name}>{asset.config_name || "\u2014"}</td>
        <td className={`py-1.5 px-2 ${dirColor}`}>{asset.direction}</td>
        <td className={`py-1.5 px-2 text-right font-medium ${numColor(asset.net_profit)}`}>{fmtDollar(asset.net_profit)}</td>
        <td className="py-1.5 px-2 text-right">{asset.profit_factor != null ? asset.profit_factor.toFixed(2) : "—"}</td>
        <td className="py-1.5 px-2 text-right">{asset.win_rate.toFixed(1)}%</td>
        <td className="py-1.5 px-2 text-right">{asset.trades}</td>
        <td className={`py-1.5 px-2 text-right ${numColor(asset.avg_trade)}`}>{fmtDollar(asset.avg_trade)}</td>
        <td className={`py-1.5 px-2 text-right ${numColor(asset.max_drawdown)}`}>{fmtDollar(asset.max_drawdown)}</td>
        <td className="py-1.5 px-2 text-center">
          {asset.structural?.regime && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              asset.structural.regime === "TREND" ? "bg-green-500/15 text-green-400"
              : asset.structural.regime === "MEANREV" ? "bg-blue-500/15 text-blue-400"
              : "bg-gray-500/15 text-gray-400"
            }`}>{asset.structural.regime}</span>
          )}
        </td>
        <td className="py-1.5 px-2 text-center text-[10px] font-medium">{asset.structural?.trend_grade ?? ""}</td>
        <td className="py-1.5 px-2 text-center text-[10px] font-medium">{asset.structural?.meanrev_grade ?? ""}</td>
        <td className={`py-1.5 px-2 text-right text-[10px] ${
          (asset.structural?.Hurst ?? 0) > 0.55 ? "text-green-400" : (asset.structural?.Hurst ?? 0) >= 0.50 ? "text-yellow-400" : (asset.structural?.Hurst ?? 0) > 0 ? "text-red-400" : ""
        }`}>{asset.structural?.Hurst != null ? asset.structural.Hurst.toFixed(3) : ""}</td>
        <td className="py-1.5 px-2 text-right text-[10px]">{asset.structural?.avg_consec_bull != null && asset.structural.avg_consec_bull > 0 ? asset.structural.avg_consec_bull.toFixed(2) : ""}</td>
        <td className="py-1.5 px-2 text-right text-[10px]">{asset.structural?.avg_consec_bear != null && asset.structural.avg_consec_bear > 0 ? asset.structural.avg_consec_bear.toFixed(2) : ""}</td>
        <td className="py-1.5 px-2 text-right text-[10px]">{asset.structural?.avg_consec_all != null && asset.structural.avg_consec_all > 0 ? asset.structural.avg_consec_all.toFixed(2) : ""}</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={18} className="bg-ag-dark/20 p-2">
            <AssetTradeList portfolioId={portfolioId} assetName={asset.asset} timeframe={timeframe} strategyId={strategyId} />
          </td>
        </tr>
      )}
    </>
  );
}
