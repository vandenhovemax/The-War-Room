"use client";
import { useState, useCallback } from "react";
import Link from "next/link";
import { BarChart3, ChevronDown, ChevronRight, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";

function KpiRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center justify-between py-1.5 px-2">
      <span className="text-xs text-ag-text-muted">{label}</span>
      <span className={`text-xs font-mono font-bold ${color || ""}`}>{value}</span>
    </div>
  );
}

function Section({ title, defaultOpen, children }: {
  title: string; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="border border-ag-border/30 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-ag-text-muted hover:bg-ag-dark/50 transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {title}
      </button>
      {open && <div className="border-t border-ag-border/20 divide-y divide-ag-border/10">{children}</div>}
    </div>
  );
}

export default function AccountKPIs() {
  const { data: kpis } = useApi(
    useCallback(() => api.accountKpis(), []),
    "account-kpis",
  );

  if (!kpis || kpis.total_trades === 0) return null;

  const pnlColor = (v: number) => v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "text-ag-text-muted";
  const fmt = (v: number) => `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart3 size={16} className="text-ag-accent" />
          <h3 className="text-sm font-semibold">Account KPIs</h3>
        </div>
        <Link href="/journal?tab=trading" className="text-xs text-ag-accent hover:underline flex items-center gap-1">
          Full Journal <ArrowRight size={12} />
        </Link>
      </div>

      {/* Summary row — always visible */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted uppercase">Total P&L</p>
          <p className={`text-lg font-bold ${pnlColor(kpis.total_pnl)}`}>{fmt(kpis.total_pnl)}</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted uppercase">Win Rate</p>
          <p className="text-lg font-bold">{kpis.win_rate.toFixed(1)}%</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted uppercase">Trades</p>
          <p className="text-lg font-bold">{kpis.total_trades}</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-ag-text-muted uppercase">Open</p>
          <p className="text-lg font-bold text-ag-yellow">{kpis.open_positions}</p>
        </div>
      </div>

      {/* Expandable detail sections */}
      <div className="space-y-2">
        <Section title="Profitability" defaultOpen>
          <KpiRow label="Net Profit" value={fmt(kpis.total_pnl)} color={pnlColor(kpis.total_pnl)} />
          <KpiRow label="Profit Factor" value={kpis.profit_factor != null ? kpis.profit_factor.toFixed(2) : "---"} />
          <KpiRow label="Avg Trade" value={fmt(kpis.avg_trade)} color={pnlColor(kpis.avg_trade)} />
          <KpiRow label="Best Trade" value={fmt(kpis.best_trade)} color="text-ag-green" />
          <KpiRow label="Worst Trade" value={fmt(kpis.worst_trade)} color="text-ag-red" />
        </Section>

        <Section title="Win/Loss Breakdown">
          <KpiRow label="Wins" value={String(kpis.win_count)} color="text-ag-green" />
          <KpiRow label="Losses" value={String(kpis.loss_count)} color="text-ag-red" />
          <KpiRow label="Avg Win" value={fmt(kpis.avg_win)} color="text-ag-green" />
          <KpiRow label="Avg Loss" value={fmt(kpis.avg_loss)} color="text-ag-red" />
        </Section>

        <Section title="Risk">
          <KpiRow label="Max Drawdown" value={fmt(kpis.max_drawdown)} color="text-ag-red" />
        </Section>
      </div>
    </div>
  );
}
