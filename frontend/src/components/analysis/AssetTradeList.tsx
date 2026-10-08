"use client";
import { useState, useEffect } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioAssetTrade, CostSummary } from "@/lib/api";
import TradeChartButton from "@/components/common/TradeChartButton";
import TradeChartPopup from "@/components/common/TradeChartPopup";
import type { TradeChartData } from "@/components/common/TradeChartPopup";

interface Props {
  portfolioId: number;
  assetName: string;
  /** When set, only show trades with exit_time after this date (OOS filter) */
  cutoffDate?: string | null;
  timeframe?: string;
  strategyId?: number;
}

const numColor = (v: number | null) =>
  v == null ? "" : v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";

const fmtDollar = (v: number | null) =>
  v == null ? "\u2014" : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** Format a backend-stored NYT datetime (tz-naive) for display.
 * Backend stores trade times as NYT-naive strings — we parse the parts
 * directly to avoid a silent shift when the user's browser isn't in NYT.
 */
const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtTime = (v: string | null) => {
  if (!v) return "\u2014";
  const s = v.replace("T", " ").split(".")[0].replace("Z", "").trim();
  const [datePart, timePart] = s.split(" ");
  if (!datePart) return v;
  const [y, m, d] = datePart.split("-").map(Number);
  const [hhStr = "00", mmStr = "00"] = (timePart || "").split(":");
  if (!y || !m || !d) return v;
  const monLabel = MONTHS_SHORT[(m - 1) % 12] || "";
  const yy = String(y).slice(-2);
  const hh = hhStr.padStart(2, "0");
  const mm = mmStr.padStart(2, "0");
  return `${monLabel} ${d}, ${yy} ${hh}:${mm}`;
};

export default function AssetTradeList({ portfolioId, assetName, cutoffDate, timeframe, strategyId }: Props) {
  const [trades, setTrades] = useState<PortfolioAssetTrade[]>([]);
  const [costSummary, setCostSummary] = useState<CostSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [chartTrade, setChartTrade] = useState<TradeChartData | null>(null);

  useEffect(() => {
    setLoading(true);
    setError("");
    api.portfolioAssetTrades(portfolioId, assetName)
      .then((r) => {
        let filtered = r.trades;
        // When cutoffDate is provided, only show OOS trades (exit after cutoff)
        if (cutoffDate) {
          const cutoff = new Date(cutoffDate);
          filtered = filtered.filter((t) => {
            const exit = t.exit_time ? new Date(t.exit_time) : null;
            return exit && exit > cutoff;
          });
          // Re-number trades sequentially after filtering
          filtered = filtered.map((t, i) => ({ ...t, num: i + 1 }));
        }
        setTrades(filtered);
        setCostSummary(r.cost_summary ?? null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load trades"))
      .finally(() => setLoading(false));
  }, [portfolioId, assetName, cutoffDate]);

  if (loading) return <p className="text-xs text-ag-text-muted py-2 pl-4">Loading trades...</p>;
  if (error) return <p className="text-xs text-ag-red py-2 pl-4">{error}</p>;
  if (trades.length === 0) return <p className="text-xs text-ag-text-muted py-2 pl-4">No trades.</p>;

  const hasCosts = costSummary && costSummary.total_stress_cost > 0;
  const hasStratCol = trades.some((t) => t.strategy_name);
  const hasSizeCol = trades.some((t) => t.size_pct != null && t.size_pct !== 100);

  const openChart = (t: PortfolioAssetTrade) => {
    setChartTrade({
      symbol: assetName,
      timeframe: timeframe || "30",
      side: t.side,
      entryTime: t.entry_time || "",
      exitTime: t.exit_time || null,
      entryPrice: t.entry_price || 0,
      exitPrice: t.exit_price,
      slPrice: null,
      tpPrice: null,
      netProfit: t.net_profit,
      strategyId,
    });
  };

  return (
    <div className="overflow-x-auto">
      {/* Cost summary banner */}
      {hasCosts && (
        <div className="flex flex-wrap gap-3 text-[10px] text-ag-text-muted px-2 py-1.5 mb-1 bg-ag-dark/20 rounded border border-ag-border/20">
          <span className="font-medium text-ag-text">Stress costs applied:</span>
          {costSummary.extra_slippage_pct > 0 && (
            <span>Slippage {costSummary.extra_slippage_pct}%</span>
          )}
          {costSummary.extra_commission_pct > 0 && (
            <span>Commission {costSummary.extra_commission_pct}%</span>
          )}
          {costSummary.funding_daily_pct > 0 && (
            <span>Funding {costSummary.funding_daily_pct}%/day</span>
          )}
          {costSummary.spread_pct > 0 && (
            <span>Spread {costSummary.spread_pct}%</span>
          )}
          <span className="text-ag-red font-medium ml-auto">
            Total: -${costSummary.total_stress_cost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        </div>
      )}

      <table className="w-full text-xs">
        <thead>
          <tr className="text-ag-text-muted border-b border-ag-border/30">
            <th className="text-left py-1 px-2 font-medium">#</th>
            {hasStratCol && <th className="text-left py-1 px-2 font-medium">Strategy</th>}
            <th className="text-left py-1 px-2 font-medium">Side</th>
            <th className="text-left py-1 px-2 font-medium">Entry</th>
            <th className="text-right py-1 px-2 font-medium">Entry Price</th>
            <th className="text-left py-1 px-2 font-medium">Exit</th>
            {hasSizeCol && <th className="text-right py-1 px-2 font-medium">Size</th>}
            {hasCosts && <th className="text-right py-1 px-2 font-medium">Cost</th>}
            <th className="text-right py-1 px-2 font-medium">P&L</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => {
            const hasFills = t.fills && t.fills.length > 0;
            const isExp = expanded === t.num;
            return (
              <TradeRow
                key={t.num}
                trade={t}
                hasFills={hasFills}
                isExpanded={isExp}
                onToggle={() => setExpanded(isExp ? null : t.num)}
                onChartClick={() => openChart(t)}
                showChartBtn={!!timeframe}
                showCost={!!hasCosts}
                showStrat={hasStratCol}
                showSize={hasSizeCol}
              />
            );
          })}
        </tbody>
      </table>

      {chartTrade && (
        <TradeChartPopup
          trade={chartTrade}
          onClose={() => setChartTrade(null)}
        />
      )}
    </div>
  );
}

function TradeRow({ trade: t, hasFills, isExpanded, onToggle, onChartClick, showChartBtn, showCost, showStrat, showSize }: {
  trade: PortfolioAssetTrade; hasFills: boolean; isExpanded: boolean;
  onToggle: () => void; onChartClick: () => void; showChartBtn: boolean; showCost: boolean; showStrat: boolean; showSize: boolean;
}) {
  const sideColor = t.side === "Long" ? "text-ag-green" : t.side === "Short" ? "text-ag-red" : "";

  return (
    <>
      <tr
        onClick={hasFills ? onToggle : undefined}
        className={`border-b border-ag-border/10 ${hasFills ? "cursor-pointer hover:bg-ag-dark/30" : ""}`}
      >
        <td className="py-1 px-2 text-ag-text-muted">{t.num}</td>
        {showStrat && (
          <td className="py-1 px-2 text-ag-text-muted max-w-[140px] truncate" title={`${t.strategy_name || ""}${t.config_name ? ` — ${t.config_name}` : ""}`}>
            {t.strategy_name || "\u2014"}
            {t.config_name && <span className="text-ag-text-muted/60"> — {t.config_name}</span>}
          </td>
        )}
        <td className={`py-1 px-2 ${sideColor}`}>
          {hasFills && (isExpanded ? <ChevronDown size={10} className="inline mr-0.5" /> : <ChevronRight size={10} className="inline mr-0.5" />)}
          {t.side.toLowerCase()}
        </td>
        <td className="py-1 px-2">{fmtTime(t.entry_time)}</td>
        <td className="py-1 px-2 text-right">{t.entry_price != null ? t.entry_price.toFixed(6) : "\u2014"}</td>
        <td className="py-1 px-2">
          {hasFills ? (
            <span className="text-ag-text-muted">{isExpanded ? "" : `\u25B8 ${t.fills.length} exits`}</span>
          ) : (
            fmtTime(t.exit_time)
          )}
        </td>
        {showSize && (
          <td className={`py-1 px-2 text-right ${
            (t.size_pct ?? 100) > 100 ? "text-ag-green" : (t.size_pct ?? 100) < 100 ? "text-ag-red" : "text-ag-text-muted"
          }`}>
            {(t.size_pct ?? 100).toFixed(0)}%
          </td>
        )}
        {showCost && (
          <td className="py-1 px-2 text-right text-ag-text-muted">
            {t.stress_cost != null ? `-$${t.stress_cost.toFixed(2)}` : "\u2014"}
          </td>
        )}
        <td className={`py-1 px-2 text-right font-medium ${numColor(t.net_profit)}`}>
          {fmtDollar(t.net_profit)}
          {showChartBtn && (
            <TradeChartButton onClick={() => onChartClick()} />
          )}
        </td>
      </tr>
      {isExpanded && t.fills.map((f, idx) => (
        <tr key={idx} className="bg-ag-dark/20 border-b border-ag-border/5">
          <td className="py-1 px-2" />
          {showStrat && <td className="py-1 px-2" />}
          <td className="py-1 px-2 text-[10px] text-ag-text-muted pl-6">Exit Time</td>
          <td className="py-1 px-2 text-[10px]">{fmtTime(f.exit_time)}</td>
          <td className="py-1 px-2 text-right text-[10px]">
            {f.exit_price != null ? f.exit_price.toFixed(6) : "\u2014"}
          </td>
          <td className="py-1 px-2 text-[10px] text-ag-text-muted">
            {f.exit_reason}
          </td>
          {showSize && <td className="py-1 px-2" />}
          {showCost && <td className="py-1 px-2" />}
          <td className={`py-1 px-2 text-right text-[10px] ${numColor(f.net_profit)}`}>
            {fmtDollar(f.net_profit)}
          </td>
        </tr>
      ))}
    </>
  );
}
