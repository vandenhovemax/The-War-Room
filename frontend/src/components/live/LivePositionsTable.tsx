"use client";
import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import type { LivePosition } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import { OpenPositionDetail } from "./PositionDetailPanel";

interface Props {
  positions: LivePosition[];
  onRefresh: () => void;
}

export default function LivePositionsTable({ positions, onRefresh }: Props) {
  const [closing, setClosing] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const handleClose = async (posId: number, symbol: string) => {
    if (!confirm(`Close position ${symbol}? This will place a market order.`)) return;
    setClosing(posId);
    try {
      await api.liveClosePosition(posId);
      onRefresh();
    } catch {
      alert("Failed to close position");
    } finally {
      setClosing(null);
    }
  };

  const toggleExpand = (id: number) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  if (positions.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-ag-text-muted text-sm">No open positions</p>
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
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">Qty <HintTip text="Position size in contracts/coins." /></span>
            </th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">Entry <HintTip text="Average fill price when the position was opened." /></span>
            </th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">SL <HintTip text="Stop loss price. If hit, position closes at market to limit your loss." /></span>
            </th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">TP <HintTip text="Take profit price. If hit, position closes to lock in profit. Can be limit or market order depending on settings." /></span>
            </th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">uPnL <HintTip text="Unrealized PnL — paper profit or loss at current market price. Not yet locked in until position closes." /></span>
            </th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">R <HintTip text="R-multiple: how many times your initial risk you've gained or lost. Requires SL." /></span>
            </th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">Fees <HintTip text="Total fees paid: entry commission + accumulated funding fees." /></span>
            </th>
            <th className="text-right py-2 px-2">Opened</th>
            <th className="text-right py-2 px-3"></th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const isExpanded = expandedId === p.id;
            const riskAmt = p.sl_price
              ? Math.abs(p.entry_price - p.sl_price) * p.quantity
              : null;
            const rMult = riskAmt && riskAmt > 0
              ? p.unrealized_pnl / riskAmt
              : null;

            return (
              <Fragment key={p.id}>
                {/* Main row */}
                <tr
                  onClick={() => toggleExpand(p.id)}
                  className={`border-b border-ag-border/50 cursor-pointer transition-colors ${
                    isExpanded ? "bg-ag-dark/50" : "hover:bg-ag-dark/30"
                  }`}
                >
                  <td className="py-2 px-1 text-ag-text-muted">
                    {isExpanded
                      ? <ChevronDown size={14} />
                      : <ChevronRight size={14} />
                    }
                  </td>
                  <td className="py-2 px-2 font-medium">{p.symbol.replace("USDT", "")}</td>
                  <td className={`py-2 px-2 ${p.side === "Buy" ? "text-ag-green" : "text-ag-red"}`}>
                    {p.side === "Buy" ? "Long" : "Short"}
                  </td>
                  <td className="py-2 px-2 text-right">{p.quantity}</td>
                  <td className="py-2 px-2 text-right">{p.entry_price.toFixed(4)}</td>
                  <td className="py-2 px-2 text-right text-ag-red">
                    {p.sl_price ? p.sl_price.toFixed(4) : "-"}
                  </td>
                  <td className="py-2 px-2 text-right text-ag-green">
                    {p.tp_price ? p.tp_price.toFixed(4) : "-"}
                  </td>
                  <td className={`py-2 px-2 text-right font-medium ${p.unrealized_pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                    ${p.unrealized_pnl.toFixed(2)}
                  </td>
                  <td className={`py-2 px-2 text-right font-medium ${
                    rMult === null ? "text-ag-text-muted" : rMult >= 0 ? "text-ag-green" : "text-ag-red"
                  }`}>
                    {rMult !== null ? `${rMult.toFixed(1)}R` : "—"}
                  </td>
                  <td className="py-2 px-2 text-right text-ag-text-muted">
                    ${(p.entry_commission + (p.funding_accumulated || 0)).toFixed(2)}
                  </td>
                  <td className="py-2 px-2 text-right text-ag-text-muted">
                    {p.opened_at ? new Date(p.opened_at).toLocaleString() : "-"}
                  </td>
                  <td className="py-2 px-3 text-right">
                    <button
                      onClick={(e) => { e.stopPropagation(); handleClose(p.id, p.symbol); }}
                      disabled={closing === p.id}
                      className="px-2 py-0.5 bg-ag-red/20 text-ag-red border border-ag-red/30 rounded text-[10px] hover:bg-ag-red/30 disabled:opacity-50"
                    >
                      {closing === p.id ? "..." : "Close"}
                    </button>
                  </td>
                </tr>

                {/* Expanded detail row */}
                {isExpanded && (
                  <tr>
                    <td colSpan={12}>
                      <OpenPositionDetail position={p} />
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
