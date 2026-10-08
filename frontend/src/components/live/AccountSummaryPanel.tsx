"use client";
import type { AccountSummary } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  data: AccountSummary;
}

function fmt(n: number, decimals = 2): string {
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function KpiCard({ label, value, hint, color }: {
  label: string; value: string; hint: string; color?: string;
}) {
  return (
    <div className="bg-ag-dark/50 rounded-lg p-3">
      <div className="flex items-center gap-1 mb-1">
        <span className="text-[10px] text-ag-text-muted uppercase tracking-wide">{label}</span>
        <HintTip text={hint} />
      </div>
      <p className={`text-lg font-semibold ${color ?? "text-ag-text"}`}>{value}</p>
    </div>
  );
}

export default function AccountSummaryPanel({ data }: Props) {
  const pnlColor = data.daily_pnl >= 0 ? "text-ag-green" : "text-ag-red";
  const upnlColor = data.unrealized_pnl >= 0 ? "text-ag-green" : "text-ag-red";

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">Account Overview</h3>
        <span className="text-[10px] text-ag-text-muted">
          Risk: {data.risk_display} per trade
        </span>
      </div>

      {/* Row 1: Equity breakdown */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
        <KpiCard
          label="Total Equity"
          value={`$${fmt(data.equity)}`}
          hint="Your full account value including unrealized PnL from open positions."
        />
        <KpiCard
          label="Available Balance"
          value={`$${fmt(data.available_balance)}`}
          hint="Cash available for new positions. Equity minus margin locked in open positions."
        />
        <KpiCard
          label="Margin Used"
          value={`$${fmt(data.margin_used)}`}
          hint="Total margin locked in your open positions. This is equity minus available balance."
        />
        <KpiCard
          label="Unrealized PnL"
          value={`${data.unrealized_pnl >= 0 ? "+" : ""}$${fmt(data.unrealized_pnl)}`}
          hint="Combined profit/loss from all open positions at current market prices. Not yet realized."
          color={upnlColor}
        />
      </div>

      {/* Row 2: Positions + Daily stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <KpiCard
          label="Positions"
          value={`${data.long_count}L / ${data.short_count}S`}
          hint={`${data.position_count} total open positions. ${data.long_count} long (buy) and ${data.short_count} short (sell).`}
        />
        <KpiCard
          label="Daily PnL"
          value={`${data.daily_pnl >= 0 ? "+" : ""}$${fmt(data.daily_pnl)}`}
          hint="Net realized profit/loss today (New York time). Resets at midnight NYT."
          color={pnlColor}
        />
        <KpiCard
          label="Daily W/L"
          value={`${data.daily_wins}W / ${data.daily_losses}L`}
          hint={`${data.daily_trades} trades today. ${data.daily_wins} winners, ${data.daily_losses} losers.`}
        />
        <KpiCard
          label="Circuit Breaker"
          value={data.circuit_breaker ? "ACTIVE" : "OFF"}
          hint="When daily losses exceed the limit, the circuit breaker activates and blocks all new entries. Exit orders still work."
          color={data.circuit_breaker ? "text-ag-red" : "text-ag-green"}
        />
      </div>
    </div>
  );
}
