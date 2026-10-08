"use client";
import { ShieldAlert, Clock, DollarSign, TrendingUp } from "lucide-react";
import HintTip from "@/components/common/HintTip";

function formatDuration(ms: number): string {
  const mins = Math.floor(ms / 60_000);
  const hrs = Math.floor(mins / 60);
  const days = Math.floor(hrs / 24);
  if (days > 0) return `${days}d ${hrs % 24}h ${mins % 60}m`;
  if (hrs > 0) return `${hrs}h ${mins % 60}m`;
  return `${mins}m`;
}

function Stat({ label, value, hint, color }: {
  label: string; value: string; hint?: string; color?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] text-ag-text-muted flex items-center gap-1">
        {label}
        {hint && <HintTip text={hint} />}
      </span>
      <span className={`text-sm font-medium ${color || "text-ag-text"}`}>{value}</span>
    </div>
  );
}

export interface DetailData {
  symbol: string;
  side: string;
  strategyName: string;
  entryPrice: number;
  exitPrice: number | null;
  quantity: number;
  slPrice: number | null;
  tpPrice: number | null;
  leverage: number;
  marginMode: string;
  trailingStopDistance: number | null;
  trailingExtreme: number | null;
  entryCommission: number;
  exitCommission: number;
  fundingFee: number;
  pnl: number;
  riskRemoved: boolean;
  openedAt: string | null;
  closedAt: string | null;
  closeReason: string | null;
}

const REASON_LABELS: Record<string, string> = {
  sl: "Stop Loss", tp: "Take Profit", tsl: "Trailing Stop",
  close: "Market Close", manual: "Manual", signal: "Signal",
  tp_timeout: "TP Timeout", emergency_close: "Emergency",
  deactivation: "Deactivated",
};

export default function DetailGrid({ data: d }: { data: DetailData }) {
  const isLong = d.side === "Buy" || d.side === "Long";
  const isClosed = d.closedAt !== null;
  const durationMs = d.openedAt
    ? (d.closedAt ? new Date(d.closedAt).getTime() : Date.now()) - new Date(d.openedAt).getTime()
    : 0;
  const notional = d.quantity * d.entryPrice;
  const totalFees = d.entryCommission + d.exitCommission + d.fundingFee;
  const riskAmount = d.slPrice ? Math.abs(d.entryPrice - d.slPrice) * d.quantity : null;
  const rrRatio = (d.slPrice && d.tpPrice)
    ? Math.abs(d.tpPrice - d.entryPrice) / Math.abs(d.entryPrice - d.slPrice) : null;
  const rMultiple = riskAmount && riskAmount > 0 ? d.pnl / riskAmount : null;
  const netPnl = d.pnl - totalFees;
  const grossPnl = d.exitPrice
    ? (isLong ? d.exitPrice - d.entryPrice : d.entryPrice - d.exitPrice) * d.quantity : d.pnl;
  const riskPct = riskAmount && notional > 0 ? (riskAmount / notional) * 100 : null;
  const marginUsed = d.leverage > 0 ? notional / d.leverage : notional;
  const tslLevel = (d.trailingStopDistance && d.trailingExtreme)
    ? (isLong ? d.trailingExtreme - d.trailingStopDistance : d.trailingExtreme + d.trailingStopDistance)
    : null;

  const fmtDate = (iso: string) => new Date(iso).toLocaleString("en-US", {
    timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* Column 1: Position Info */}
      <div className="bg-ag-dark/40 rounded-lg p-3 space-y-3">
        <h4 className="text-xs font-semibold text-ag-text-muted uppercase tracking-wide flex items-center gap-1.5">
          <TrendingUp size={12} /> Position Info
        </h4>
        <div className="grid grid-cols-2 gap-3">
          <Stat label="Strategy" value={d.strategyName || "—"} />
          <Stat label="Direction" value={isLong ? "Long" : "Short"} color={isLong ? "text-ag-green" : "text-ag-red"} />
          <Stat label="Entry Price" value={`$${d.entryPrice.toFixed(4)}`} />
          {isClosed && d.exitPrice !== null && <Stat label="Exit Price" value={`$${d.exitPrice.toFixed(4)}`} />}
          <Stat label="Quantity" value={String(d.quantity)} />
          <Stat label="Notional" value={`$${notional.toFixed(2)}`} hint="Total position value = qty × entry price" />
          {d.leverage > 0 && <Stat label="Leverage" value={`${d.leverage}x`} />}
          {d.marginMode !== "—" && <Stat label="Margin Mode" value={d.marginMode} />}
          {d.leverage > 0 && <Stat label="Margin Used" value={`$${marginUsed.toFixed(2)}`} hint="Approximate margin = notional / leverage" />}
          {d.slPrice != null && <Stat label="Stop Loss" value={`$${d.slPrice.toFixed(4)}`} color="text-ag-red" />}
          {d.tpPrice != null && <Stat label="Take Profit" value={`$${d.tpPrice.toFixed(4)}`} color="text-ag-green" />}
          {d.trailingStopDistance != null && <Stat label="TSL Distance" value={`$${d.trailingStopDistance.toFixed(4)}`} hint="Trailing stop distance from extreme price" />}
          {tslLevel != null && <Stat label="TSL Level" value={`$${tslLevel.toFixed(4)}`} hint="Current trailing stop trigger price" color="text-ag-yellow" />}
          {isClosed && d.closeReason && <Stat label="Close Reason" value={REASON_LABELS[d.closeReason] || d.closeReason} />}
        </div>
      </div>

      {/* Column 2: Risk Analytics */}
      <div className="bg-ag-dark/40 rounded-lg p-3 space-y-3">
        <h4 className="text-xs font-semibold text-ag-text-muted uppercase tracking-wide flex items-center gap-1.5">
          <ShieldAlert size={12} /> Risk Analytics
        </h4>
        <div className="grid grid-cols-2 gap-3">
          <Stat label="Risk Amount" value={riskAmount ? `$${riskAmount.toFixed(2)}` : "No SL"}
            hint="Dollar risk = |entry − SL| × qty. Without a SL, risk is unlimited."
            color={riskAmount ? "text-ag-text" : "text-ag-yellow"} />
          <Stat label="Risk % of Notional" value={riskPct ? `${riskPct.toFixed(2)}%` : "—"} hint="How much of the position value you're risking" />
          <Stat label="Risk:Reward" value={rrRatio ? `1:${rrRatio.toFixed(2)}` : "—"} hint="Ratio of potential reward (TP distance) to risk (SL distance)" />
          <Stat label="R-Multiple" value={rMultiple !== null ? `${rMultiple.toFixed(2)}R` : "—"}
            hint="How many 'R' you've gained or lost. 1R = your initial risk amount."
            color={rMultiple !== null ? (rMultiple >= 0 ? "text-ag-green" : "text-ag-red") : undefined} />
          <Stat label="Risk Removed" value={d.riskRemoved ? "Yes (BE+)" : "No"}
            hint="Whether SL has been moved to breakeven or better (e.g. after TP1 hit)"
            color={d.riskRemoved ? "text-ag-green" : "text-ag-text-muted"} />
          {d.trailingExtreme != null && <Stat label="Best Price" value={`$${d.trailingExtreme.toFixed(4)}`} hint="Most favorable price reached since entry (used for TSL)" />}
        </div>
        {riskAmount && d.slPrice && d.tpPrice && (
          <div className="mt-2 pt-2 border-t border-ag-border/50">
            <div className="text-[10px] text-ag-text-muted mb-1">Risk / Reward Visual</div>
            <div className="flex items-center gap-1 h-4">
              <div className="bg-ag-red/30 border border-ag-red/50 rounded-sm h-full flex items-center justify-center text-[9px] text-ag-red"
                style={{ width: `${Math.min(50, (1 / (1 + (rrRatio || 1))) * 100)}%` }}>Risk</div>
              <div className="bg-ag-green/30 border border-ag-green/50 rounded-sm h-full flex-1 flex items-center justify-center text-[9px] text-ag-green">Reward</div>
            </div>
          </div>
        )}
      </div>

      {/* Column 3: Fees & PnL */}
      <div className="bg-ag-dark/40 rounded-lg p-3 space-y-3">
        <h4 className="text-xs font-semibold text-ag-text-muted uppercase tracking-wide flex items-center gap-1.5">
          <DollarSign size={12} /> Fees & PnL
        </h4>
        <div className="grid grid-cols-2 gap-3">
          <Stat label="Entry Commission" value={`$${d.entryCommission.toFixed(4)}`} hint="Fee paid when opening the position" />
          <Stat label="Exit Commission" value={`$${d.exitCommission.toFixed(4)}`} hint="Fee paid when closing the position" />
          <Stat label="Funding Fees" value={`$${d.fundingFee.toFixed(4)}`}
            hint="Accumulated funding rate payments (positive = you paid, negative = you received)"
            color={d.fundingFee > 0 ? "text-ag-red" : d.fundingFee < 0 ? "text-ag-green" : undefined} />
          <Stat label="Total Fees" value={`$${totalFees.toFixed(4)}`} hint="Sum of all fees: entry + exit commissions + funding" color="text-ag-red" />
        </div>
        <div className="border-t border-ag-border/50 pt-2 grid grid-cols-2 gap-3">
          <Stat label="Gross PnL" value={`$${grossPnl.toFixed(2)}`} hint="Price movement PnL before fees" color={grossPnl >= 0 ? "text-ag-green" : "text-ag-red"} />
          <Stat label={isClosed ? "Realized PnL" : "Unrealized PnL"} value={`$${d.pnl.toFixed(2)}`}
            hint={isClosed ? "Final PnL as reported by the exchange" : "Current paper profit/loss (not locked in)"}
            color={d.pnl >= 0 ? "text-ag-green" : "text-ag-red"} />
          <Stat label="Net PnL (after fees)" value={`$${netPnl.toFixed(2)}`} hint="PnL minus all fees — your actual profit or loss"
            color={netPnl >= 0 ? "text-ag-green" : "text-ag-red"} />
        </div>
        <div className="border-t border-ag-border/50 pt-2">
          <div className="flex items-center gap-3">
            <Clock size={12} className="text-ag-text-muted" />
            <Stat label="Time in Trade" value={durationMs > 0 ? formatDuration(durationMs) : "—"} hint="How long the position has been (or was) open" />
            {d.openedAt && <Stat label="Opened" value={fmtDate(d.openedAt)} />}
            {d.closedAt && <Stat label="Closed" value={fmtDate(d.closedAt)} />}
          </div>
        </div>
      </div>
    </div>
  );
}
