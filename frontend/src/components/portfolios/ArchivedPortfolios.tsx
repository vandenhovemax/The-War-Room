"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import type { PortfolioItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import {
  Archive, RotateCcw, ChevronDown, ChevronRight,
  TrendingUp, TrendingDown,
} from "lucide-react";

function MetricBadge({ label, value, fmt }: {
  label: string; value: number | undefined; fmt: "pct" | "usd" | "x" | "num";
}) {
  if (value === undefined || value === null) return null;
  let text = "";
  let color = "text-ag-text";
  if (fmt === "pct") {
    text = `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
    color = value > 0 ? "text-ag-green" : value < 0 ? "text-ag-red" : "text-ag-text-muted";
  } else if (fmt === "usd") {
    text = `${value >= 0 ? "+" : ""}$${Math.abs(value) < 1000 ? value.toFixed(0) : `${(value / 1000).toFixed(1)}k`}`;
    color = value > 0 ? "text-ag-green" : value < 0 ? "text-ag-red" : "text-ag-text-muted";
  } else if (fmt === "x") {
    text = `${value.toFixed(2)}x`;
    color = value >= 1.5 ? "text-ag-green" : value < 1.0 ? "text-ag-red" : "text-ag-text";
  } else {
    text = `${value.toFixed(1)}`;
  }
  return (
    <div className="text-center min-w-[50px]">
      <p className="text-[9px] text-ag-text-muted uppercase">{label}</p>
      <p className={`text-xs font-mono font-bold ${color}`}>{text}</p>
    </div>
  );
}

export default function ArchivedPortfolios() {
  const { addToast } = useTaskManager();
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPortfolios(await api.portfoliosByType("archived"));
    } catch {
      addToast("Failed to load archived portfolios", "error");
    }
    setLoading(false);
  }, [addToast]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <p className="text-sm text-ag-text-muted">Loading archived portfolios...</p>;

  if (portfolios.length === 0) {
    return (
      <div className="text-center py-12">
        <div className="inline-flex p-3 rounded-xl bg-ag-blue/10 text-ag-blue mb-3">
          <Archive size={28} />
        </div>
        <h3 className="text-sm font-semibold mb-1">Archived Portfolios</h3>
        <p className="text-xs text-ag-text-muted max-w-sm mx-auto">
          No archived portfolios yet. Stop a paper/real portfolio and click <strong>Archive</strong> to preserve it here with all its data.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {portfolios.map((p) => (
        <ArchivedCard
          key={p.id}
          portfolio={p}
          isExpanded={expanded === p.id}
          onToggle={() => setExpanded(expanded === p.id ? null : p.id)}
          onUpdate={load}
        />
      ))}
    </div>
  );
}

function ArchivedCard({ portfolio: p, isExpanded, onToggle, onUpdate }: {
  portfolio: PortfolioItem; isExpanded: boolean; onToggle: () => void; onUpdate: () => void;
}) {
  const { addToast } = useTaskManager();

  const handleRestore = async () => {
    if (!confirm(`Restore "${p.name}" back to Saved Portfolios?`)) return;
    try {
      await api.unarchivePortfolio(p.id);
      addToast(`"${p.name}" restored`, "success");
      onUpdate();
    } catch {
      addToast("Failed to restore portfolio", "error");
    }
  };

  // Use the best available metrics (live > regular)
  const metrics = Object.keys(p.live_metrics_snapshot || {}).length > 0
    ? p.live_metrics_snapshot
    : p.metrics_snapshot;
  const pnl = metrics?.net_profit ?? metrics?.total_pnl ?? 0;
  const isUp = pnl >= 0;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg">
      <div className="flex items-center gap-3 p-3 cursor-pointer hover:bg-ag-card-hover" onClick={onToggle}>
        {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            {isUp ? <TrendingUp size={14} className="text-ag-green" /> : <TrendingDown size={14} className="text-ag-red" />}
            <p className="text-sm font-medium truncate">{p.name}</p>
            <span className="px-1.5 py-0.5 bg-ag-blue/15 text-ag-blue rounded text-[10px]">Archived</span>
          </div>
          <p className="text-[10px] text-ag-text-muted">
            {p.asset_count} assets
            {p.activated_at && ` · Active ${new Date(p.activated_at).toLocaleDateString()}`}
            {p.deactivated_at && ` — ${new Date(p.deactivated_at).toLocaleDateString()}`}
          </p>
        </div>

        <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
          <button onClick={handleRestore}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25"
            title="Restore to Saved Portfolios">
            <RotateCcw size={12} /> Restore
          </button>
        </div>
      </div>

      {isExpanded && (
        <div className="border-t border-ag-border p-3">
          <div className="flex items-center gap-3 overflow-x-auto">
            <MetricBadge label="PnL" value={pnl} fmt="usd" />
            <MetricBadge label="WR" value={metrics?.win_rate} fmt="pct" />
            <MetricBadge label="PF" value={metrics?.profit_factor} fmt="x" />
            <MetricBadge label="DD" value={metrics?.max_drawdown ? -Math.abs(metrics.max_drawdown) : undefined} fmt="usd" />
            <MetricBadge label="Trades" value={metrics?.total_trades} fmt="num" />
          </div>
          {p.description && (
            <p className="text-xs text-ag-text-muted mt-2">{p.description}</p>
          )}
        </div>
      )}
    </div>
  );
}
