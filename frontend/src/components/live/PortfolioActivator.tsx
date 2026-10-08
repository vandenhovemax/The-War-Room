"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { PortfolioItem } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import { RefreshCw } from "lucide-react";

interface Props {
  portfolios: PortfolioItem[];
  activePortfolioIds: number[];
  engineRunning: boolean;
  onRefresh: () => void;
}

type PState = "saved" | "monitoring" | "live";

function getState(p: PortfolioItem, activeIds: number[]): PState {
  if (activeIds.includes(p.id)) return "live";
  if (p.is_active) return "monitoring";
  return "saved";
}

export default function PortfolioActivator({
  portfolios, activePortfolioIds, engineRunning, onRefresh,
}: Props) {
  const [acting, setActing] = useState<number | null>(null);
  const [bulkActing, setBulkActing] = useState(false);
  const [refreshing, setRefreshing] = useState<number | null>(null);
  const [refreshResult, setRefreshResult] = useState<string | null>(null);

  // Show all portfolios except hidden/deleted
  const visible = portfolios.filter(
    (p) => !p.is_hidden && !p.is_deleted,
  );

  // Split into paper vs live/real groups
  const paperPortfolios = visible.filter((p) => p.portfolio_type === "paper");
  const livePortfolios = visible.filter((p) => p.portfolio_type !== "paper");

  const handleActivate = async (id: number) => {
    setActing(id);
    try {
      await api.liveMonitor(id);
      onRefresh();
    } catch {
      alert("Failed to start monitoring.");
    } finally {
      setActing(null);
    }
  };

  const handleStopMonitoring = async (id: number) => {
    setActing(id);
    try {
      await api.liveStopMonitoring(id);
      onRefresh();
    } catch {
      alert("Failed to stop monitoring.");
    } finally {
      setActing(null);
    }
  };

  const handleGoLive = async (id: number) => {
    if (!confirm(
      "Start LIVE TRADING for this portfolio?\n\n" +
      "This will execute REAL orders on Bybit.\n" +
      "Make sure your .env guardrails are set correctly."
    )) return;
    setActing(id);
    try {
      await api.liveActivate(id);
      onRefresh();
    } catch {
      alert("Failed to go live. Is the engine running?");
    } finally {
      setActing(null);
    }
  };

  const handleStopTrading = async (id: number) => {
    if (!confirm(
      "Stop live trading?\n\n" +
      "OK = keep positions open (exit orders remain on exchange).\n" +
      "Cancel = keep trading."
    )) return;
    setActing(id);
    try {
      await api.liveDeactivate(id, false);
      onRefresh();
    } catch {
      alert("Failed to stop trading.");
    } finally {
      setActing(null);
    }
  };

  // Bulk actions — split by paper vs live
  const paperSaved = paperPortfolios.filter((p) => getState(p, activePortfolioIds) === "saved");
  const paperMonitoring = paperPortfolios.filter((p) => getState(p, activePortfolioIds) === "monitoring");
  const liveSaved = livePortfolios.filter((p) => getState(p, activePortfolioIds) === "saved");
  const liveMonitoring = livePortfolios.filter((p) => getState(p, activePortfolioIds) === "monitoring");

  const handleBulkActivate = async (items: PortfolioItem[], label: string) => {
    if (items.length === 0) return;
    setBulkActing(true);
    try {
      for (const p of items) {
        await api.liveMonitor(p.id);
      }
      onRefresh();
    } catch {
      alert(`Some ${label} portfolios failed to activate.`);
    } finally {
      setBulkActing(false);
    }
  };

  const handleBulkGoLive = async (items: PortfolioItem[], label: string) => {
    if (items.length === 0) return;
    if (!confirm(
      `Start ${label.toUpperCase()} TRADING for ${items.length} portfolios?\n\n` +
      (label === "paper" ? "Paper mode — no real orders." : "This will execute REAL orders on Bybit.\nMake sure your .env guardrails are set correctly.")
    )) return;
    setBulkActing(true);
    try {
      for (const p of items) {
        await api.liveActivate(p.id);
      }
      onRefresh();
    } catch {
      alert(`Some ${label} portfolios failed to go live. Is the engine running?`);
    } finally {
      setBulkActing(false);
    }
  };

  const handleRefresh = async (id: number) => {
    setRefreshing(id);
    setRefreshResult(null);
    try {
      const result = await api.liveRefreshPortfolio(id);
      const parts: string[] = [];
      if (result.added.length > 0) parts.push(`+${result.added.length} added`);
      if (result.removed.length > 0) parts.push(`-${result.removed.length} removed`);
      if (parts.length === 0) parts.push("No changes");
      setRefreshResult(parts.join(", "));
      onRefresh();
      setTimeout(() => setRefreshResult(null), 5000);
    } catch {
      setRefreshResult("Refresh failed");
      setTimeout(() => setRefreshResult(null), 3000);
    } finally {
      setRefreshing(null);
    }
  };

  if (visible.length === 0) {
    return (
      <div className="bg-ag-card border border-ag-border rounded-lg p-4">
        <p className="text-sm text-ag-text-muted">
          No portfolios available. Save a portfolio from the optimizer first.
        </p>
      </div>
    );
  }

  const renderGroup = (
    items: PortfolioItem[],
    label: string,
    savedItems: PortfolioItem[],
    monitoringItems: PortfolioItem[],
    badgeColor: string,
  ) => {
    if (items.length === 0) return null;
    return (
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className={`text-[10px] font-semibold uppercase tracking-wider ${badgeColor}`}>
            {label} ({items.length})
          </span>
          <div className="flex items-center gap-1.5">
            {savedItems.length > 0 && (
              <button
                onClick={() => handleBulkActivate(savedItems, label)}
                disabled={bulkActing}
                className="px-2.5 py-0.5 bg-ag-green/20 text-ag-green border border-ag-green/30 rounded text-[10px] hover:bg-ag-green/30 disabled:opacity-50"
              >
                {bulkActing ? "..." : `Activate All (${savedItems.length})`}
              </button>
            )}
            {monitoringItems.length > 0 && (
              <button
                onClick={() => handleBulkGoLive(monitoringItems, label)}
                disabled={bulkActing || !engineRunning}
                title={!engineRunning ? "Start the engine first" : `Go live with ${monitoringItems.length} ${label} portfolios`}
                className="px-2.5 py-0.5 bg-ag-accent/20 text-ag-accent border border-ag-accent/30 rounded text-[10px] hover:bg-ag-accent/30 disabled:opacity-50"
              >
                {bulkActing ? "..." : `Go Live All (${monitoringItems.length})`}
              </button>
            )}
          </div>
        </div>
        <div className="space-y-1.5">
          {items.map((p) => {
            const state = getState(p, activePortfolioIds);
            return (
              <PortfolioRow
                key={p.id}
                portfolio={p}
                state={state}
                acting={acting === p.id}
                refreshing={refreshing === p.id}
                engineRunning={engineRunning}
                onActivate={() => handleActivate(p.id)}
                onStopMonitoring={() => handleStopMonitoring(p.id)}
                onGoLive={() => handleGoLive(p.id)}
                onStopTrading={() => handleStopTrading(p.id)}
                onRefresh={() => handleRefresh(p.id)}
              />
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <div className="flex items-center gap-1 mb-3">
        <h3 className="text-sm font-medium">Portfolio Management</h3>
        <HintTip text="Activate = start candle gathering (monitoring). Go Live = start executing real orders. Stop Trading = back to saved." />
      </div>
      <div className="space-y-4">
        {renderGroup(livePortfolios, "Live", liveSaved, liveMonitoring, "text-ag-green")}
        {renderGroup(paperPortfolios, "Paper", paperSaved, paperMonitoring, "text-yellow-400")}
      </div>
      {refreshResult && (
        <p className="text-[10px] text-ag-accent mt-2">{refreshResult}</p>
      )}
    </div>
  );
}

/* ── Per-portfolio row ─────────────────────────────────────────── */

interface RowProps {
  portfolio: PortfolioItem;
  state: PState;
  acting: boolean;
  refreshing: boolean;
  engineRunning: boolean;
  onActivate: () => void;
  onStopMonitoring: () => void;
  onGoLive: () => void;
  onStopTrading: () => void;
  onRefresh: () => void;
}

const DOT_CLASSES: Record<PState, string> = {
  saved: "bg-ag-border",
  monitoring: "bg-yellow-400",
  live: "bg-ag-green animate-pulse",
};

const STATE_LABELS: Record<PState, string> = {
  saved: "Saved",
  monitoring: "Monitoring",
  live: "Live",
};

const STATE_BADGE_CLASSES: Record<PState, string> = {
  saved: "bg-ag-border/30 text-ag-text-muted",
  monitoring: "bg-yellow-500/15 text-yellow-400",
  live: "bg-ag-green/15 text-ag-green",
};

function PortfolioRow({
  portfolio: p, state, acting, refreshing, engineRunning,
  onActivate, onStopMonitoring, onGoLive, onStopTrading, onRefresh,
}: RowProps) {
  return (
    <div className="flex items-center justify-between px-3 py-2 bg-ag-dark/50 rounded">
      {/* Left: status dot + name + badges */}
      <div className="flex items-center gap-3">
        <div className={`w-2 h-2 rounded-full ${DOT_CLASSES[state]}`} />
        <span className="text-sm font-medium">{p.name}</span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${STATE_BADGE_CLASSES[state]}`}>
          {STATE_LABELS[state]}
        </span>
        <span className="text-[10px] text-ag-text-muted">
          {p.asset_count} assets
        </span>
      </div>

      {/* Right: action buttons */}
      <div className="flex items-center gap-1.5">
        {state === "saved" && (
          <button
            onClick={onActivate}
            disabled={acting}
            className="px-3 py-1 bg-ag-green/20 text-ag-green border border-ag-green/30 rounded text-[10px] hover:bg-ag-green/30 disabled:opacity-50"
          >
            {acting ? "..." : "Activate"}
          </button>
        )}

        {state === "monitoring" && (
          <>
            <button
              onClick={onGoLive}
              disabled={acting || !engineRunning}
              title={!engineRunning ? "Start the engine first" : "Start live trading"}
              className="px-3 py-1 bg-ag-green/20 text-ag-green border border-ag-green/30 rounded text-[10px] hover:bg-ag-green/30 disabled:opacity-50"
            >
              {acting ? "..." : "Go Live"}
            </button>
            <button
              onClick={onStopMonitoring}
              disabled={acting}
              className="px-3 py-1 bg-ag-red/20 text-ag-red border border-ag-red/30 rounded text-[10px] hover:bg-ag-red/30 disabled:opacity-50"
            >
              {acting ? "..." : "Stop"}
            </button>
          </>
        )}

        {state === "live" && (
          <>
            <button
              onClick={onRefresh}
              disabled={refreshing}
              title="Refresh portfolio asset list (after swaps)"
              className="p-1 bg-ag-accent/20 text-ag-accent border border-ag-accent/30 rounded hover:bg-ag-accent/30 disabled:opacity-50"
            >
              <RefreshCw size={12} className={refreshing ? "animate-spin" : ""} />
            </button>
            <button
              onClick={onStopTrading}
              disabled={acting}
              className="px-3 py-1 bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 rounded text-[10px] hover:bg-yellow-500/30 disabled:opacity-50"
            >
              {acting ? "..." : "Stop Trading"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
