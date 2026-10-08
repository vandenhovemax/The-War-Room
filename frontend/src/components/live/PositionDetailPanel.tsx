"use client";
import type { LivePosition, LiveTrade } from "@/lib/api";
import PositionChart from "./PositionChart";
import DetailGrid from "./DetailGrid";

/** Expandable detail for an OPEN position (used by LivePositionsTable). */
export function OpenPositionDetail({ position: p }: { position: LivePosition }) {
  return (
    <div className="space-y-4 p-4 bg-ag-dark/20 border-t border-ag-border/30">
      {p.opened_at && (
        <PositionChart
          symbol={p.symbol}
          side={p.side}
          entryTime={p.opened_at}
          exitTime={null}
          entryPrice={p.entry_price}
          exitPrice={null}
          slPrice={p.sl_price}
          tpPrice={p.tp_price}
        />
      )}
      <DetailGrid data={{
        symbol: p.symbol,
        side: p.side,
        strategyName: p.strategy_name,
        entryPrice: p.entry_price,
        exitPrice: null,
        quantity: p.quantity,
        slPrice: p.sl_price,
        tpPrice: p.tp_price,
        leverage: p.leverage,
        marginMode: p.margin_mode,
        trailingStopDistance: p.trailing_stop_distance,
        trailingExtreme: p.trailing_extreme,
        entryCommission: p.entry_commission,
        exitCommission: p.exit_commission,
        fundingFee: p.funding_accumulated,
        pnl: p.unrealized_pnl,
        riskRemoved: p.risk_removed,
        openedAt: p.opened_at,
        closedAt: null,
        closeReason: null,
      }} />
    </div>
  );
}

/** Expandable detail for a CLOSED trade (used by LiveTradesTable). */
export function ClosedTradeDetail({ trade: t }: { trade: LiveTrade }) {
  return (
    <div className="space-y-4 p-4 bg-ag-dark/20 border-t border-ag-border/30">
      {t.opened_at && (
        <PositionChart
          symbol={t.symbol}
          side={t.side}
          entryTime={t.opened_at}
          exitTime={t.closed_at}
          entryPrice={t.entry_price}
          exitPrice={t.exit_price}
          slPrice={null}
          tpPrice={null}
        />
      )}
      <DetailGrid data={{
        symbol: t.symbol,
        side: t.side,
        strategyName: t.strategy_name,
        entryPrice: t.entry_price,
        exitPrice: t.exit_price,
        quantity: t.quantity,
        slPrice: null,
        tpPrice: null,
        leverage: 0,
        marginMode: "—",
        trailingStopDistance: null,
        trailingExtreme: null,
        entryCommission: t.entry_commission,
        exitCommission: t.exit_commission,
        fundingFee: t.funding_fee,
        pnl: t.realized_pnl,
        riskRemoved: false,
        openedAt: t.opened_at,
        closedAt: t.closed_at,
        closeReason: t.close_reason,
      }} />
    </div>
  );
}
