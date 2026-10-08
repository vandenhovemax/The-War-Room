"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import type { PortfolioItem } from "@/lib/api";
import { Wifi, FlaskConical, FileText, ChevronDown, ChevronRight } from "lucide-react";

/* ── Compact portfolio row ── */

function PortfolioRow({ p }: { p: PortfolioItem }) {
  const live = p.live_metrics_snapshot ?? {};
  const pnl = live.net_profit ?? live.total_pnl;
  const wr = live.win_rate;
  const pf = live.profit_factor;
  const isActive = !p.deactivated_at;

  return (
    <div className="flex items-center gap-3 px-3 py-2 bg-ag-dark/30 rounded-lg">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium truncate">{p.name}</span>
          {isActive ? (
            <span className="px-1.5 py-0.5 bg-ag-green/15 text-ag-green rounded text-[9px]">Active</span>
          ) : (
            <span className="px-1.5 py-0.5 bg-ag-text-muted/15 text-ag-text-muted rounded text-[9px]">Stopped</span>
          )}
        </div>
        <span className="text-[10px] text-ag-text-muted">
          {p.asset_count} assets
          {p.activated_at && ` · Since ${new Date(p.activated_at).toLocaleDateString()}`}
        </span>
      </div>
      <div className="flex items-center gap-4 text-[11px] font-mono">
        {pnl !== undefined && (
          <span className={pnl >= 0 ? "text-ag-green" : "text-ag-red"}>
            {pnl >= 0 ? "+" : ""}${Math.abs(pnl) < 1000 ? pnl.toFixed(0) : `${(pnl / 1000).toFixed(1)}k`}
          </span>
        )}
        {wr !== undefined && <span>{wr.toFixed(1)}%</span>}
        {pf !== undefined && <span>{pf.toFixed(2)}x</span>}
      </div>
    </div>
  );
}

/* ── Section with heading ── */

function Section({ title, icon: Icon, color, portfolios, emptyText }: {
  title: string;
  icon: typeof Wifi;
  color: string;
  portfolios: PortfolioItem[];
  emptyText: string;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Icon size={14} className={color} />
        <h3 className={`text-xs font-semibold uppercase ${color}`}>{title}</h3>
        <span className="text-[10px] text-ag-text-muted">({portfolios.length})</span>
      </div>
      {portfolios.length === 0 ? (
        <p className="text-xs text-ag-text-muted pl-6">{emptyText}</p>
      ) : (
        <div className="space-y-1.5">
          {portfolios.map((p) => <PortfolioRow key={p.id} p={p} />)}
        </div>
      )}
    </div>
  );
}

/* ── Saved portfolios accordion ── */

function SavedAccordion({ portfolios }: { portfolios: PortfolioItem[] }) {
  const [open, setOpen] = useState(false);

  if (portfolios.length === 0) return null;

  return (
    <div className="border border-ag-border/50 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-xs font-medium text-ag-text-muted hover:bg-ag-dark/30 transition-colors"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <FileText size={14} />
        <span>Saved Portfolios</span>
        <span className="text-[10px] text-ag-text-muted ml-1">({portfolios.length})</span>
      </button>
      {open && (
        <div className="border-t border-ag-border/30 p-2 space-y-1.5 max-h-[400px] overflow-y-auto">
          {portfolios.map((p) => (
            <div key={p.id} className="flex items-center gap-3 px-3 py-1.5 bg-ag-dark/20 rounded text-xs">
              <span className="truncate flex-1">{p.name}</span>
              <span className="text-ag-text-muted">{p.asset_count} assets</span>
              {p.metrics_snapshot?.profit_factor !== undefined && (
                <span className="text-ag-text-muted font-mono">
                  PF: {p.metrics_snapshot.profit_factor.toFixed(2)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Main component ── */

export default function PortfolioOverviewTab() {
  const [all, setAll] = useState<PortfolioItem[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setAll(await api.portfolios());
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <p className="text-sm text-ag-text-muted">Loading portfolios...</p>;

  const realPortfolios = all.filter((p) => p.portfolio_type === "real" && !p.is_hidden && !p.is_deleted);
  const paperPortfolios = all.filter((p) => p.portfolio_type === "paper" && !p.is_hidden && !p.is_deleted);
  const savedPortfolios = all.filter((p) => p.portfolio_type === "saved" && !p.is_hidden && !p.is_deleted);

  return (
    <div className="space-y-6">
      {/* Real Trading — always prominent */}
      <Section
        title="Real Trading"
        icon={Wifi}
        color="text-ag-green"
        portfolios={realPortfolios}
        emptyText="No real trading portfolios. Promote one from Saved → Real."
      />

      {/* Paper Trading — always prominent */}
      <Section
        title="Paper Trading"
        icon={FlaskConical}
        color="text-ag-yellow"
        portfolios={paperPortfolios}
        emptyText="No paper trading portfolios. Promote one from Saved → Paper."
      />

      {/* Saved — collapsible accordion */}
      <SavedAccordion portfolios={savedPortfolios} />
    </div>
  );
}
