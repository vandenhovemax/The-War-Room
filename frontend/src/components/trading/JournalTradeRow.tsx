"use client";
import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { JournalTradeItem, StrategyItem } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import PositionChart from "@/components/live/PositionChart";
import DetailGrid from "@/components/live/DetailGrid";

interface Props {
  trades: JournalTradeItem[];
  strategies: StrategyItem[];
}

const REASON_LABELS: Record<string, string> = {
  sl: "Stop Loss", tp: "Take Profit", tsl: "Trailing Stop",
  close: "Market Close", manual: "Manual", signal: "Signal",
  tp_timeout: "TP Timeout", emergency_close: "Emergency",
  deactivation: "Deactivated",
};

function formatDuration(ms: number): string {
  const mins = Math.floor(ms / 60_000);
  const hrs = Math.floor(mins / 60);
  const days = Math.floor(hrs / 24);
  if (days > 0) return `${days}d ${hrs % 24}h`;
  if (hrs > 0) return `${hrs}h ${mins % 60}m`;
  return `${mins}m`;
}

export default function JournalTradeRow({ trades, strategies }: Props) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  if (trades.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-ag-text-muted text-sm">No trades found</p>
      </div>
    );
  }

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-ag-border text-ag-text-muted">
            <th className="w-6 py-2 px-1"></th>
            <th className="text-left py-2 px-2">Symbol</th>
            <th className="text-left py-2 px-2">Side</th>
            <th className="text-left py-2 px-2">Strategy</th>
            <th className="text-right py-2 px-2">Entry</th>
            <th className="text-right py-2 px-2">Exit</th>
            <th className="text-right py-2 px-2">Qty</th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">PnL <HintTip text="Net profit/loss for this trade." /></span>
            </th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">R <HintTip text="R-multiple: PnL / initial risk amount." /></span>
            </th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">Fees <HintTip text="Actual commission paid." /></span>
            </th>
            <th className="text-left py-2 px-2">Reason</th>
            <th className="text-right py-2 px-2">Duration</th>
            <th className="text-right py-2 px-3">Date</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => {
            const isExpanded = expandedId === t.id;
            const rMult = t.risk_amount && t.risk_amount > 0 && t.net_profit !== null
              ? t.net_profit / t.risk_amount : null;
            const durationMs = (t.entry_time && t.exit_time)
              ? new Date(t.exit_time).getTime() - new Date(t.entry_time).getTime() : 0;
            const stratName = t.strategy_name || strategies.find(s => s.id === t.strategy_id)?.name || "—";

            return (
              <Fragment key={t.id}>
                <tr
                  onClick={() => setExpandedId(isExpanded ? null : t.id)}
                  className={`border-b border-ag-border/50 cursor-pointer transition-colors ${
                    isExpanded ? "bg-ag-dark/50" : "hover:bg-ag-dark/30"
                  }`}
                >
                  <td className="py-1.5 px-1 text-ag-text-muted">
                    {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </td>
                  <td className="py-1.5 px-2 font-medium">{t.symbol.replace("USDT", "")}</td>
                  <td className={`py-1.5 px-2 ${t.side === "Long" ? "text-ag-green" : "text-ag-red"}`}>{t.side}</td>
                  <td className="py-1.5 px-2 text-ag-text-muted truncate max-w-[100px]">{stratName}</td>
                  <td className="py-1.5 px-2 text-right">{t.entry_price.toFixed(4)}</td>
                  <td className="py-1.5 px-2 text-right">{t.exit_price ? t.exit_price.toFixed(4) : "—"}</td>
                  <td className="py-1.5 px-2 text-right">{t.quantity}</td>
                  <td className={`py-1.5 px-2 text-right font-medium ${
                    (t.net_profit ?? 0) >= 0 ? "text-ag-green" : "text-ag-red"
                  }`}>
                    {t.net_profit !== null ? `$${t.net_profit.toFixed(2)}` : "—"}
                  </td>
                  <td className={`py-1.5 px-2 text-right font-medium ${
                    rMult === null ? "text-ag-text-muted" : rMult >= 0 ? "text-ag-green" : "text-ag-red"
                  }`}>
                    {rMult !== null ? `${rMult.toFixed(1)}R` : "—"}
                  </td>
                  <td className="py-1.5 px-2 text-right text-ag-text-muted">
                    {t.actual_commission !== null ? `$${t.actual_commission.toFixed(2)}` : "—"}
                  </td>
                  <td className="py-1.5 px-2">
                    {t.exit_reason && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-ag-border text-ag-text-muted">
                        {REASON_LABELS[t.exit_reason] || t.exit_reason}
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 px-2 text-right text-ag-text-muted">
                    {durationMs > 0 ? formatDuration(durationMs) : "—"}
                  </td>
                  <td className="py-1.5 px-3 text-right text-ag-text-muted">
                    {t.entry_time ? new Date(t.entry_time).toLocaleString("en-US", {
                      timeZone: "America/New_York",
                      month: "short", day: "numeric", year: "2-digit",
                      hour: "2-digit", minute: "2-digit",
                    }) : "—"}
                  </td>
                </tr>

                {isExpanded && (
                  <tr>
                    <td colSpan={13}>
                      <JournalTradeDetail trade={t} strategyName={stratName} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Expanded detail panel for a single journal trade. */
function JournalTradeDetail({ trade: t, strategyName }: { trade: JournalTradeItem; strategyName: string }) {
  return (
    <div className="space-y-4 p-4 bg-ag-dark/20 border-t border-ag-border/30">
      {/* Chart */}
      {t.entry_time && t.status === "closed" && t.exit_price && (
        <PositionChart
          symbol={t.symbol}
          side={t.side}
          entryTime={t.entry_time}
          exitTime={t.exit_time}
          entryPrice={t.entry_price}
          exitPrice={t.exit_price}
          slPrice={t.sl_price}
          tpPrice={t.tp_price}
        />
      )}

      {/* Reuse DetailGrid with mapped data */}
      <DetailGrid data={{
        symbol: t.symbol,
        side: t.side,
        strategyName,
        entryPrice: t.entry_price,
        exitPrice: t.exit_price,
        quantity: t.quantity,
        slPrice: t.sl_price,
        tpPrice: t.tp_price,
        leverage: 0,
        marginMode: "—",
        trailingStopDistance: null,
        trailingExtreme: null,
        entryCommission: t.expected_commission || 0,
        exitCommission: 0,
        fundingFee: 0,
        pnl: t.net_profit ?? 0,
        riskRemoved: false,
        openedAt: t.entry_time,
        closedAt: t.exit_time,
        closeReason: t.exit_reason,
      }} />

      {/* Extra journal-specific info */}
      {(t.risk_amount || t.expected_pnl !== null || t.actual_commission !== null) && (
        <div className="bg-ag-dark/40 rounded-lg p-3 grid grid-cols-2 sm:grid-cols-4 gap-3">
          {t.risk_amount != null && (
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] text-ag-text-muted flex items-center gap-1">
                Planned Risk <HintTip text="The dollar risk amount set when entering this trade." />
              </span>
              <span className="text-sm font-medium">${t.risk_amount.toFixed(2)}</span>
            </div>
          )}
          {t.expected_pnl !== null && (
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] text-ag-text-muted flex items-center gap-1">
                Expected PnL <HintTip text="Auto-calculated PnL from entry/exit prices." />
              </span>
              <span className={`text-sm font-medium ${(t.expected_pnl ?? 0) >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                ${t.expected_pnl.toFixed(2)}
              </span>
            </div>
          )}
          {t.expected_commission > 0 && (
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] text-ag-text-muted">Expected Fees</span>
              <span className="text-sm font-medium">${t.expected_commission.toFixed(2)}</span>
            </div>
          )}
          {t.actual_commission !== null && (
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] text-ag-text-muted">Actual Fees</span>
              <span className="text-sm font-medium">${t.actual_commission.toFixed(2)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
