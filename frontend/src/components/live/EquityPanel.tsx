"use client";
import type { LiveEquity, LiveEngineStatus } from "@/lib/api";

interface Props {
  equity: LiveEquity;
  status: LiveEngineStatus;
}

export default function EquityPanel({ equity, status }: Props) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <KpiCard
        label="Total Equity"
        value={`$${equity.equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
      />
      <KpiCard
        label="Available Balance"
        value={`$${equity.available_balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
      />
      <KpiCard
        label="Active Portfolios"
        value={String(status.active_portfolios.length)}
      />
      <KpiCard
        label="TSL Tracked"
        value={String(status.tsl_tracked)}
        sub={`${status.websocket.kline_symbols} kline / ${status.websocket.ticker_symbols} ticker feeds`}
      />
    </div>
  );
}

function KpiCard({ label, value, color, sub }: {
  label: string; value: string; color?: string; sub?: string;
}) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <p className="text-[10px] text-ag-text-muted uppercase tracking-wider">{label}</p>
      <p className={`text-xl font-bold mt-1 ${color || ""}`}>{value}</p>
      {sub && <p className="text-[10px] text-ag-text-muted mt-1">{sub}</p>}
    </div>
  );
}
