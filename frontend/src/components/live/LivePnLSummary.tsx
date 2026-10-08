"use client";
import type { PnLSummary } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  data: PnLSummary;
}

function fmt(n: number): string {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function PnlCell({ label, value, hint }: { label: string; value: number; hint: string }) {
  const color = value >= 0 ? "text-ag-green" : "text-ag-red";
  return (
    <div className="text-center">
      <div className="flex items-center justify-center gap-1 mb-0.5">
        <span className="text-[10px] text-ag-text-muted uppercase">{label}</span>
        <HintTip text={hint} />
      </div>
      <p className={`text-sm font-semibold ${color}`}>
        {value >= 0 ? "+" : ""}${fmt(value)}
      </p>
    </div>
  );
}

export default function LivePnLSummary({ data }: Props) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">PnL Summary</h3>
        <div className="flex items-center gap-2 text-[10px] text-ag-text-muted">
          <span>{data.total_trades} trades</span>
          <span className="text-ag-accent">{data.win_rate.toFixed(1)}% win rate</span>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <PnlCell label="Today" value={data.today}
          hint="Realized PnL today (New York time)." />
        <PnlCell label="This Week" value={data.week}
          hint="Realized PnL since Monday." />
        <PnlCell label="This Month" value={data.month}
          hint="Realized PnL since the 1st of the month." />
        <PnlCell label="All Time" value={data.all_time}
          hint="Total realized PnL across all trading history." />
      </div>

      {data.total_trades > 0 && (
        <div className="flex items-center justify-center gap-6 mt-3 pt-3 border-t border-ag-border">
          <span className="text-[10px] text-ag-text-muted">
            Avg Win: <span className="text-ag-green">${fmt(data.avg_win)}</span>
          </span>
          <span className="text-[10px] text-ag-text-muted">
            Avg Loss: <span className="text-ag-red">${fmt(data.avg_loss)}</span>
          </span>
          <span className="text-[10px] text-ag-text-muted">
            {data.winning_trades}W / {data.total_trades - data.winning_trades}L
          </span>
        </div>
      )}
    </div>
  );
}
