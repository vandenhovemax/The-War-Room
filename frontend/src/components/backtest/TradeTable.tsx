"use client";
import { useState } from "react";
import { ChevronDown, ChevronRight, TrendingUp, TrendingDown } from "lucide-react";
import { BacktestTradeItem } from "@/lib/api";
import TradeChartButton from "@/components/common/TradeChartButton";
import TradeChartPopup from "@/components/common/TradeChartPopup";
import type { TradeChartData } from "@/components/common/TradeChartPopup";

interface TradeTableProps {
  trades: BacktestTradeItem[];
  symbol?: string;
  timeframe?: string;
  strategyId?: number;
}

export default function TradeTable({ trades, symbol, timeframe, strategyId }: TradeTableProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [chartTrade, setChartTrade] = useState<TradeChartData | null>(null);

  const toggleExpand = (posId: string) => {
    setExpandedId(expandedId === posId ? null : posId);
  };

  const fmtDate = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "short", day: "numeric", year: "2-digit",
      hour: "2-digit", minute: "2-digit",
    }) : "-";

  const fmtPrice = (p: number) => {
    if (!p) return "-";
    if (p >= 1000) return p.toFixed(2);
    if (p >= 1) return p.toFixed(4);
    return p.toFixed(6);
  };

  const openChart = (t: BacktestTradeItem) => {
    const lastExit = t.exits[t.exits.length - 1];
    const tradeSymbol = t.symbol || symbol || "";
    const tradeTf = timeframe || "30";
    setChartTrade({
      symbol: tradeSymbol,
      timeframe: tradeTf,
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

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-ag-text-muted text-xs border-b border-ag-border">
            <th className="w-6 py-2 px-1" />
            <th className="text-left py-2 px-2">#</th>
            <th className="text-left py-2 px-2">Side</th>
            <th className="text-left py-2 px-2">Entry</th>
            <th className="text-left py-2 px-2">Exit</th>
            <th className="text-right py-2 px-2">Entry Price</th>
            <th className="text-right py-2 px-2">Exits</th>
            <th className="text-right py-2 px-2">Qty</th>
            <th className="text-right py-2 px-2">P&L</th>
          </tr>
        </thead>
        <tbody>
          {trades.slice(0, 200).map((t) => {
            const isExpanded = expandedId === t.position_id;
            return (
              <TradeRow
                key={t.position_id}
                trade={t}
                isExpanded={isExpanded}
                onToggle={() => toggleExpand(t.position_id)}
                onChartClick={() => openChart(t)}
                showChartBtn={!!timeframe}
                fmtDate={fmtDate}
                fmtPrice={fmtPrice}
              />
            );
          })}
        </tbody>
      </table>
      {trades.length > 200 && (
        <p className="text-xs text-ag-text-muted mt-2 text-center">
          Showing first 200 of {trades.length} positions
        </p>
      )}

      {chartTrade && (
        <TradeChartPopup
          trade={chartTrade}
          onClose={() => setChartTrade(null)}
        />
      )}
    </div>
  );
}

function TradeRow({
  trade: t, isExpanded, onToggle, onChartClick, showChartBtn, fmtDate, fmtPrice,
}: {
  trade: BacktestTradeItem;
  isExpanded: boolean;
  onToggle: () => void;
  onChartClick: () => void;
  showChartBtn: boolean;
  fmtDate: (iso: string | null) => string;
  fmtPrice: (p: number) => string;
}) {
  const lastExit = t.exits[t.exits.length - 1];
  const exitLabel = t.exit_count > 1
    ? `${t.exit_count} exits`
    : (lastExit?.exit_reason || "signal");
  // Agglomerated view: last exit time
  const lastExitTime = lastExit?.exit_time || null;

  return (
    <>
      {/* Main row — always visible */}
      <tr
        onClick={onToggle}
        className="border-b border-ag-border/50 hover:bg-ag-dark/50 cursor-pointer select-none"
      >
        <td className="py-2 px-1 text-ag-text-muted">
          {t.exit_count > 1 ? (
            isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />
          ) : <span className="w-3.5 inline-block" />}
        </td>
        <td className="py-2 px-2 text-ag-text-muted">{t.trade_num}</td>
        <td className="py-2 px-2">
          <span className={`inline-flex items-center gap-1 ${
            t.side === "long" ? "text-ag-green" : "text-ag-red"
          }`}>
            {t.side === "long" ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
            {t.side}
          </span>
        </td>
        <td className="py-2 px-2 text-ag-text-muted text-xs">
          {fmtDate(t.entry_time)}
        </td>
        <td className="py-2 px-2 text-ag-text-muted text-xs">
          {fmtDate(lastExitTime)}
        </td>
        <td className="py-2 px-2 text-right">{fmtPrice(t.entry_price)}</td>
        <td className="py-2 px-2 text-right text-ag-text-muted text-xs">
          {exitLabel}
        </td>
        <td className="py-2 px-2 text-right text-ag-text-muted">
          {t.total_qty.toFixed(2)}
        </td>
        <td className={`py-2 px-2 text-right font-medium ${
          t.net_profit >= 0 ? "text-ag-green" : "text-ag-red"
        }`}>
          ${t.net_profit.toFixed(2)}
          {showChartBtn && (
            <TradeChartButton onClick={() => onChartClick()} />
          )}
        </td>
      </tr>

      {/* Expanded detail panel */}
      {isExpanded && (
        <tr>
          <td colSpan={9} className="bg-ag-dark/30 px-4 py-3">
            <TradeDetail trade={t} fmtDate={fmtDate} fmtPrice={fmtPrice} />
          </td>
        </tr>
      )}
    </>
  );
}

function TradeDetail({
  trade: t, fmtDate, fmtPrice,
}: {
  trade: BacktestTradeItem;
  fmtDate: (iso: string | null) => string;
  fmtPrice: (p: number) => string;
}) {
  return (
    <div className="space-y-3">
      {/* Entry + SL/TP summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <DetailCell label="Entry Price" value={fmtPrice(t.entry_price)} />
        <DetailCell label="Entry Time" value={fmtDate(t.entry_time)} />
        <DetailCell
          label="Stop Loss"
          value={t.sl_price ? fmtPrice(t.sl_price) : "None"}
          muted={!t.sl_price}
        />
        <DetailCell
          label="Take Profit"
          value={t.tp_price ? fmtPrice(t.tp_price) : "None"}
          muted={!t.tp_price}
        />
      </div>

      {/* Exit fills table */}
      <div>
        <p className="text-xs text-ag-text-muted mb-1 font-medium">
          Exit Fills ({t.exits.length})
        </p>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-ag-text-muted border-b border-ag-border/30">
              <th className="text-left py-1 px-2">Reason</th>
              <th className="text-left py-1 px-2">Entry Time</th>
              <th className="text-left py-1 px-2">Exit Time</th>
              <th className="text-right py-1 px-2">Price</th>
              <th className="text-right py-1 px-2">Qty</th>
              <th className="text-right py-1 px-2">P&L</th>
            </tr>
          </thead>
          <tbody>
            {t.exits.map((ex, i) => (
              <tr key={i} className="border-b border-ag-border/20">
                <td className="py-1 px-2 font-medium">{ex.exit_reason}</td>
                <td className="py-1 px-2 text-ag-text-muted">
                  {fmtDate(ex.entry_time ?? t.entry_time)}
                </td>
                <td className="py-1 px-2 text-ag-text-muted">{fmtDate(ex.exit_time)}</td>
                <td className="py-1 px-2 text-right">{fmtPrice(ex.exit_price)}</td>
                <td className="py-1 px-2 text-right text-ag-text-muted">
                  {ex.quantity.toFixed(2)}
                </td>
                <td className={`py-1 px-2 text-right font-medium ${
                  ex.net_profit >= 0 ? "text-ag-green" : "text-ag-red"
                }`}>
                  ${ex.net_profit.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-ag-border/50 font-medium">
              <td colSpan={5} className="py-1 px-2 text-right text-ag-text-muted">
                Total P&L:
              </td>
              <td className={`py-1 px-2 text-right ${
                t.net_profit >= 0 ? "text-ag-green" : "text-ag-red"
              }`}>
                ${t.net_profit.toFixed(2)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

function DetailCell({ label, value, muted = false }: {
  label: string; value: string; muted?: boolean;
}) {
  return (
    <div className="bg-ag-darker/50 rounded px-2 py-1.5">
      <div className="text-ag-text-muted text-[10px] mb-0.5">{label}</div>
      <div className={muted ? "text-ag-text-muted" : ""}>{value}</div>
    </div>
  );
}
