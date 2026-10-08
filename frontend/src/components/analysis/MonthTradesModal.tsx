"use client";
import { useState, useEffect } from "react";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import type { MonthTrade } from "@/lib/api";
import TradeChartButton from "@/components/common/TradeChartButton";
import TradeChartPopup from "@/components/common/TradeChartPopup";
import type { TradeChartData } from "@/components/common/TradeChartPopup";

const MONTH_NAMES = [
  "", "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

interface Props {
  portfolioId: number;
  year: number;
  month: number;
  cutoffDate?: string | null;
  period?: string;
  timeframe?: string;
  strategyId?: number;
  onClose: () => void;
}

const numColor = (v: number) =>
  v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";

const fmtDollar = (v: number | null) =>
  v == null ? "\u2014" : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** Format a backend-stored NYT datetime (tz-naive) for display.
 *
 * Backend stores BacktestTrade.entry_time as NYT-naive (e.g.
 * "2024-03-15 14:30:00"). Passing that string into `new Date()` parses it
 * using the BROWSER's local timezone, so if the user's machine isn't in
 * NYT the value gets silently shifted. We bypass Date entirely and format
 * the stored parts directly — what's stored IS what should be displayed.
 */
const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtTime = (v: string | null) => {
  if (!v) return "\u2014";
  // Accept either "YYYY-MM-DDTHH:MM:SS" or "YYYY-MM-DD HH:MM:SS" (with optional .ffffff / Z)
  const s = v.replace("T", " ").split(".")[0].replace("Z", "").trim();
  const [datePart, timePart] = s.split(" ");
  if (!datePart) return v;
  const [y, m, d] = datePart.split("-").map(Number);
  const [hhStr = "00", mmStr = "00"] = (timePart || "").split(":");
  if (!y || !m || !d) return v;
  const monLabel = MONTHS_SHORT[(m - 1) % 12] || "";
  const hh = hhStr.padStart(2, "0");
  const mm = mmStr.padStart(2, "0");
  return `${monLabel} ${d}, ${hh}:${mm}`;
};

export default function MonthTradesModal({
  portfolioId, year, month, cutoffDate, period = "oos", timeframe, strategyId, onClose,
}: Props) {
  const [trades, setTrades] = useState<MonthTrade[]>([]);
  const [selectionChanges, setSelectionChanges] = useState<
    { date: string; added: string[]; removed: string[]; window_num?: number }[]
  >([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [chartTrade, setChartTrade] = useState<TradeChartData | null>(null);

  useEffect(() => {
    setLoading(true);
    setError("");
    api.portfolioMonthTrades(portfolioId, year, month, cutoffDate ?? undefined, period)
      .then((r) => {
        setTrades(r.trades ?? []);
        setSelectionChanges(r.selection_changes ?? []);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load trades"))
      .finally(() => setLoading(false));
  }, [portfolioId, year, month, cutoffDate, period]);

  // Filter selection changes to those that fell inside THIS month
  const monthPrefix = `${year}-${String(month).padStart(2, "0")}`;
  const monthChanges = selectionChanges
    .filter((c) => (c.date || "").startsWith(monthPrefix))
    .sort((a, b) => a.date.localeCompare(b.date));

  // Close on Escape key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const totalPnl = trades.reduce((s, t) => s + t.net_profit, 0);

  const openChart = (t: MonthTrade) => {
    // Extract symbol from asset field (e.g. "BTCUSDT_Long" → "BTCUSDT")
    const sym = t.asset.replace(/_Long$/, "").replace(/_Short$/, "");
    // Prefer per-trade strategy_id (comes from the batch/asset this trade
    // belongs to) — fall back to the prop so single-strategy callers keep
    // working. This ensures cross-strategy portfolios load the right
    // indicators for each trade's originating strategy.
    const sid = t.strategy_id ?? strategyId;
    setChartTrade({
      symbol: sym,
      timeframe: timeframe || "30",
      side: t.side || "",
      entryTime: t.entry_time || "",
      exitTime: t.exit_time || null,
      entryPrice: t.entry_price ?? 0,
      exitPrice: t.exit_price,
      slPrice: null,
      tpPrice: null,
      netProfit: t.net_profit,
      strategyId: sid ?? undefined,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-ag-card border border-ag-border rounded-xl shadow-2xl w-full max-w-3xl max-h-[80vh] flex flex-col mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-ag-border">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-semibold text-ag-text">
              {MONTH_NAMES[month]} {year}
            </h3>
            <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-ag-accent/15 text-ag-accent font-medium">
              {period.toUpperCase()}
            </span>
            <span className="text-xs text-ag-text-muted">
              {trades.length} trade{trades.length !== 1 ? "s" : ""}
            </span>
            {!loading && trades.length > 0 && (
              <span className={`text-xs font-mono font-medium ${numColor(totalPnl)}`}>
                {fmtDollar(totalPnl)}
              </span>
            )}
          </div>
          <button onClick={onClose} className="p-1 hover:bg-ag-dark/50 rounded transition-colors">
            <X size={16} className="text-ag-text-muted" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-3">
          {/* Hybrid / Phase 2 rotation events that happened in this month */}
          {monthChanges.length > 0 && (
            <div className="mb-4 border border-ag-accent/30 rounded-lg bg-ag-accent/5 p-3">
              <div className="text-[10px] uppercase text-ag-accent font-semibold mb-2">
                Asset Selection Changes This Month ({monthChanges.length})
              </div>
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-ag-text-muted border-b border-ag-border/30">
                    <th className="text-left py-1 px-2">Date</th>
                    <th className="text-left py-1 px-2">In</th>
                    <th className="text-left py-1 px-2">Out</th>
                  </tr>
                </thead>
                <tbody>
                  {monthChanges.map((c, i) => (
                    <tr key={`${c.date}-${i}`} className="border-b border-ag-border/10">
                      <td className="py-1 px-2 font-mono text-ag-text-muted">{c.date}</td>
                      <td className="py-1 px-2">
                        {c.added.length > 0 ? (
                          <span className="text-ag-green font-mono">
                            {c.added.map(a => a.replace("USDT_Long", "↑").replace("USDT_Short", "↓").replace("USDT", "")).join(", ")}
                          </span>
                        ) : <span className="text-ag-text-muted/40">—</span>}
                      </td>
                      <td className="py-1 px-2">
                        {c.removed.length > 0 ? (
                          <span className="text-ag-red font-mono">
                            {c.removed.map(a => a.replace("USDT_Long", "↑").replace("USDT_Short", "↓").replace("USDT", "")).join(", ")}
                          </span>
                        ) : <span className="text-ag-text-muted/40">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {loading && (
            <p className="text-xs text-ag-text-muted py-4">Loading trades...</p>
          )}
          {error && (
            <p className="text-xs text-ag-red py-4">{error}</p>
          )}
          {!loading && !error && trades.length === 0 && (
            <p className="text-xs text-ag-text-muted py-4">No trades for this month.</p>
          )}
          {!loading && !error && trades.length > 0 && (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-ag-text-muted border-b border-ag-border/30">
                  <th className="text-left py-1.5 px-2 font-medium">#</th>
                  <th className="text-left py-1.5 px-2 font-medium">Group</th>
                  <th className="text-left py-1.5 px-2 font-medium">Batch</th>
                  <th className="text-left py-1.5 px-2 font-medium">Asset</th>
                  <th className="text-left py-1.5 px-2 font-medium">Side</th>
                  <th className="text-left py-1.5 px-2 font-medium" title="Entry time shown in New York time (NYT)">
                    Entry <span className="text-[9px] opacity-60">NYT</span>
                  </th>
                  <th className="text-right py-1.5 px-2 font-medium">Entry $</th>
                  <th className="text-left py-1.5 px-2 font-medium" title="Exit time shown in New York time (NYT)">
                    Exit <span className="text-[9px] opacity-60">NYT</span>
                  </th>
                  <th className="text-right py-1.5 px-2 font-medium">Exit $</th>
                  <th className="text-right py-1.5 px-2 font-medium">P&L</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => {
                  const sideColor = t.side === "Long"
                    ? "text-ag-green" : t.side === "Short"
                    ? "text-ag-red" : "";
                  return (
                    <tr key={t.num} className="border-b border-ag-border/10 hover:bg-ag-dark/20">
                      <td className="py-1.5 px-2 text-ag-text-muted">{t.num}</td>
                      <td className="py-1.5 px-2 text-ag-accent text-[10px]">{t.batch_group || "\u2014"}</td>
                      <td className="py-1.5 px-2 text-ag-text-muted text-[10px]" title={t.batch_name || ""}>
                        {t.batch_id ?? "\u2014"}
                        {t.pnl_multiplier != null && t.pnl_multiplier !== 1 && (
                          <span className="ml-1 text-purple-400">{t.pnl_multiplier}x</span>
                        )}
                      </td>
                      <td className="py-1.5 px-2 font-medium">{t.asset_display}</td>
                      <td className={`py-1.5 px-2 ${sideColor}`}>
                        {t.side ? t.side.charAt(0) : ""}
                      </td>
                      <td className="py-1.5 px-2">{fmtTime(t.entry_time)}</td>
                      <td className="py-1.5 px-2 text-right font-mono">
                        {t.entry_price != null ? t.entry_price.toFixed(4) : "\u2014"}
                      </td>
                      <td className="py-1.5 px-2">{fmtTime(t.exit_time)}</td>
                      <td className="py-1.5 px-2 text-right font-mono">
                        {t.exit_price != null ? t.exit_price.toFixed(4) : "\u2014"}
                      </td>
                      <td className={`py-1.5 px-2 text-right font-mono font-medium ${numColor(t.net_profit)}`}>
                        {fmtDollar(t.net_profit)}
                        <TradeChartButton onClick={() => openChart(t)} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="border-t border-ag-border font-semibold">
                  <td colSpan={9} className="py-1.5 px-2 text-right text-ag-text-muted">Total:</td>
                  <td className={`py-1.5 px-2 text-right font-mono ${numColor(totalPnl)}`}>
                    {fmtDollar(totalPnl)}
                  </td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>
      </div>

      {chartTrade && (
        <TradeChartPopup
          trade={chartTrade}
          onClose={() => setChartTrade(null)}
        />
      )}
    </div>
  );
}
