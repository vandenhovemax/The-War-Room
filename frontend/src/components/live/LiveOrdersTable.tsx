"use client";
import type { LiveOrder } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  orders: LiveOrder[];
}

const roleColors: Record<string, string> = {
  entry: "text-ag-accent",
  sl: "text-ag-red",
  tp: "text-ag-green",
  tsl: "text-yellow-400",
  close: "text-ag-text-muted",
};

const statusColors: Record<string, string> = {
  open: "bg-ag-accent/15 text-ag-accent",
  partial: "bg-yellow-500/15 text-yellow-400",
  filled: "bg-ag-green/15 text-ag-green",
  cancelled: "bg-ag-red/15 text-ag-red",
  failed: "bg-ag-red/15 text-ag-red",
  submitting: "bg-ag-border text-ag-text-muted",
};

export default function LiveOrdersTable({ orders }: Props) {
  if (orders.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-ag-text-muted text-sm">No orders</p>
      </div>
    );
  }

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-ag-border text-ag-text-muted">
            <th className="text-left py-2 px-3">Symbol</th>
            <th className="text-left py-2 px-2">Side</th>
            <th className="text-left py-2 px-2">
              <span className="flex items-center gap-1">Role <HintTip text="Purpose of this order: ENTRY opens a position, SL is stop loss, TP is take profit, TSL is trailing stop, CLOSE is manual close." /></span>
            </th>
            <th className="text-left py-2 px-2">
              <span className="flex items-center gap-1">Type <HintTip text="Order type: Market fills immediately at best price, Limit fills at specified price or better." /></span>
            </th>
            <th className="text-left py-2 px-2">
              <span className="flex items-center gap-1">Status <HintTip text="Order lifecycle: Open (working), Partial (partially filled), Filled (complete), Cancelled, Failed." /></span>
            </th>
            <th className="text-right py-2 px-2">Qty</th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">Filled <HintTip text="How much of the order has been filled so far." /></span>
            </th>
            <th className="text-right py-2 px-2">Price</th>
            <th className="text-right py-2 px-2">
              <span className="flex items-center justify-end gap-1">Fill Price <HintTip text="Average price at which the order was actually filled. May differ from requested price due to slippage." /></span>
            </th>
            <th className="text-right py-2 px-2">Fee</th>
            <th className="text-right py-2 px-3">Time</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id} className="border-b border-ag-border/50">
              <td className="py-1.5 px-3 font-medium">{o.symbol.replace("USDT", "")}</td>
              <td className={`py-1.5 px-2 ${o.side === "Buy" ? "text-ag-green" : "text-ag-red"}`}>
                {o.side}
              </td>
              <td className={`py-1.5 px-2 font-medium ${roleColors[o.role] || ""}`}>
                {o.role.toUpperCase()}
              </td>
              <td className="py-1.5 px-2">{o.order_type}</td>
              <td className="py-1.5 px-2">
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${statusColors[o.status] || ""}`}>
                  {o.status}
                </span>
              </td>
              <td className="py-1.5 px-2 text-right">{o.intended_qty.toFixed(4)}</td>
              <td className="py-1.5 px-2 text-right">{o.filled_qty.toFixed(4)}</td>
              <td className="py-1.5 px-2 text-right">
                {o.price ? o.price.toFixed(4) : "-"}
              </td>
              <td className="py-1.5 px-2 text-right">
                {o.avg_fill_price ? o.avg_fill_price.toFixed(4) : "-"}
              </td>
              <td className="py-1.5 px-2 text-right text-ag-text-muted">
                ${o.commission.toFixed(4)}
              </td>
              <td className="py-1.5 px-3 text-right text-ag-text-muted">
                {o.created_at ? new Date(o.created_at).toLocaleTimeString() : "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
