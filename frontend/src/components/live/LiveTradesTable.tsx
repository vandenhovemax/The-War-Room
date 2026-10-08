"use client";
import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { LiveTrade } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import { ClosedTradeDetail } from "./PositionDetailPanel";

interface Props {
  trades: LiveTrade[];
  timeframe?: string;
  strategyId?: number;
}

const reasonLabels: Record<string, string> = {
  sl: "Stop Loss",
  tp: "Take Profit",
  tsl: "Trailing Stop",
  close: "Market Close",
  manual: "Manual",
  signal: "Signal",
  tp_timeout: "TP Timeout",
  emergency_close: "Emergency",
  deactivation: "Deactivated",
};

export default function LiveTradesTable({ trades }: Props) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  if (trades.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-ag-text-muted text-sm">No closed trades yet</p>
      </div>
    );
  }

  const toggleExpand = (id: number) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-ag-border text-ag-text-muted">
            <th className="w-6 py-2 px-1"></th>
            <th className="text-left py-2 px-2">Symbol</th>
            <th className="text-left py-2 px-2">Side</th>
            <th className="text-right py-2 px-2">Entry</th>
            <th className="text-right py-2 px-2">Exit</th>
            <th className="text-right py-2 px-2">Qty</th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">PnL <HintTip text="Realized PnL after all fees." /></span>
            </th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">Fees <HintTip text="Total fees: entry + exit commissions + funding." /></span>
            </th>
            <th className="text-left py-2 px-2">Reason</th>
            <th className="text-right py-2 px-2">Duration</th>
            <th className="text-right py-2 px-3">Closed</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => {
            const isExpanded = expandedId === t.id;
            const totalFees = t.entry_commission + t.exit_commission + t.funding_fee;
            const durationMs = (t.opened_at && t.closed_at)
              ? new Date(t.closed_at).getTime() - new Date(t.opened_at).getTime()
              : 0;
            const durationStr = durationMs > 0
              ? formatDurationShort(durationMs)
              : "—";

            return (
              <Fragment key={t.id}>
                <tr
                  onClick={() => toggleExpand(t.id)}
                  className={`border-b border-ag-border/50 cursor-pointer transition-colors ${
                    isExpanded ? "bg-ag-dark/50" : "hover:bg-ag-dark/30"
                  }`}
                >
                  <td className="py-1.5 px-1 text-ag-text-muted">
                    {isExpanded
                      ? <ChevronDown size={14} />
                      : <ChevronRight size={14} />
                    }
                  </td>
                  <td className="py-1.5 px-2 font-medium">{t.symbol.replace("USDT", "")}</td>
                  <td className={`py-1.5 px-2 ${t.side === "Long" ? "text-ag-green" : "text-ag-red"}`}>
                    {t.side}
                  </td>
                  <td className="py-1.5 px-2 text-right">{t.entry_price.toFixed(4)}</td>
                  <td className="py-1.5 px-2 text-right">{t.exit_price.toFixed(4)}</td>
                  <td className="py-1.5 px-2 text-right">{t.quantity}</td>
                  <td className={`py-1.5 px-2 text-right font-medium ${t.realized_pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                    ${t.realized_pnl.toFixed(2)}
                  </td>
                  <td className="py-1.5 px-2 text-right text-ag-text-muted">
                    ${totalFees.toFixed(2)}
                  </td>
                  <td className="py-1.5 px-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-ag-border text-ag-text-muted">
                      {reasonLabels[t.close_reason] || t.close_reason}
                    </span>
                  </td>
                  <td className="py-1.5 px-2 text-right text-ag-text-muted">
                    {durationStr}
                  </td>
                  <td className="py-1.5 px-3 text-right text-ag-text-muted">
                    {t.closed_at ? new Date(t.closed_at).toLocaleString("en-US", {
                      timeZone: "America/New_York",
                      month: "short", day: "numeric", year: "2-digit",
                      hour: "2-digit", minute: "2-digit",
                    }) : "-"}
                  </td>
                </tr>

                {/* Expanded detail row */}
                {isExpanded && (
                  <tr>
                    <td colSpan={11}>
                      <ClosedTradeDetail trade={t} />
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

/* Short duration formatter for the table column */
function formatDurationShort(ms: number): string {
  const mins = Math.floor(ms / 60_000);
  const hrs = Math.floor(mins / 60);
  const days = Math.floor(hrs / 24);
  if (days > 0) return `${days}d ${hrs % 24}h`;
  if (hrs > 0) return `${hrs}h ${mins % 60}m`;
  return `${mins}m`;
}
