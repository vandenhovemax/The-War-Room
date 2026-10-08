"use client";
import { useState, useCallback } from "react";
import Link from "next/link";
import { Play, Zap, FileText, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioItem, LiveEngineStatus } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import HintTip from "@/components/common/HintTip";

type PState = "saved" | "monitoring" | "live";

function getState(p: PortfolioItem, activeIds: number[]): PState {
  if (activeIds.includes(p.id)) return "live";
  if (p.is_active) return "monitoring";
  return "saved";
}

export default function QuickActivate() {
  const [acting, setActing] = useState(false);

  const { data: status } = usePolling<LiveEngineStatus>(
    useCallback(() => api.liveStatus(), []),
    10000,
  );
  const { data: portfolios, refetch } = usePolling<PortfolioItem[]>(
    useCallback(() => api.portfolios(), []),
    10000,
  );

  const engineRunning = status?.running ?? false;
  const activeIds = status?.active_portfolios ?? [];
  const all = portfolios?.filter((p) => !p.is_hidden && !p.is_deleted) ?? [];

  const paperItems = all.filter((p) => p.portfolio_type === "paper");
  const liveItems = all.filter((p) => p.portfolio_type === "real");

  const paperSaved = paperItems.filter((p) => getState(p, activeIds) === "saved");
  const paperMon = paperItems.filter((p) => getState(p, activeIds) === "monitoring");
  const paperLive = paperItems.filter((p) => getState(p, activeIds) === "live");
  const liveSaved = liveItems.filter((p) => getState(p, activeIds) === "saved");
  const liveMon = liveItems.filter((p) => getState(p, activeIds) === "monitoring");
  const liveLive = liveItems.filter((p) => getState(p, activeIds) === "live");

  const handleBulkActivate = async (items: PortfolioItem[], label: string) => {
    if (items.length === 0) return;
    setActing(true);
    try {
      for (const p of items) await api.liveMonitor(p.id);
      refetch();
    } catch {
      alert(`Some ${label} portfolios failed to activate.`);
    } finally {
      setActing(false);
    }
  };

  const handleBulkGoLive = async (items: PortfolioItem[], label: string) => {
    if (items.length === 0) return;
    const msg = label === "paper"
      ? `Start PAPER TRADING for ${items.length} portfolios?\n\nPaper mode — no real orders.`
      : `Start LIVE TRADING for ${items.length} portfolios?\n\nThis will execute REAL orders on Bybit.`;
    if (!confirm(msg)) return;
    setActing(true);
    try {
      for (const p of items) await api.liveActivate(p.id);
      refetch();
    } catch {
      alert(`Some ${label} portfolios failed to go live.`);
    } finally {
      setActing(false);
    }
  };

  if (all.length === 0) return null;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-lg bg-ag-accent/10 text-ag-accent">
            <Play size={18} />
          </div>
          <h4 className="font-semibold">Quick Activate</h4>
          <HintTip text="Activate = start candle gathering. Go Live = start executing orders. Works per group (Paper or Live)." />
        </div>
        <Link
          href="/live"
          className="text-xs text-ag-accent hover:underline flex items-center gap-1"
        >
          Live Trading <ArrowRight size={12} />
        </Link>
      </div>

      {/* Engine status */}
      <div className="flex items-center gap-2 mb-4 text-xs">
        <div className={`w-2 h-2 rounded-full ${engineRunning ? "bg-ag-green animate-pulse" : "bg-ag-border"}`} />
        <span className="text-ag-text-muted">
          Engine: {engineRunning ? "Running" : "Stopped"}
          {status?.paper_trading ? " (Paper)" : status?.running ? " (Live)" : ""}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* Live group */}
        <GroupCard
          icon={Zap}
          label="Live Trading"
          color="text-ag-green"
          iconBg="bg-ag-green/10"
          saved={liveSaved.length}
          monitoring={liveMon.length}
          live={liveLive.length}
          acting={acting}
          engineRunning={engineRunning}
          onActivate={() => handleBulkActivate(liveSaved, "live")}
          onGoLive={() => handleBulkGoLive(liveMon, "live")}
        />
        {/* Paper group */}
        <GroupCard
          icon={FileText}
          label="Paper Trading"
          color="text-yellow-400"
          iconBg="bg-yellow-500/10"
          saved={paperSaved.length}
          monitoring={paperMon.length}
          live={paperLive.length}
          acting={acting}
          engineRunning={engineRunning}
          onActivate={() => handleBulkActivate(paperSaved, "paper")}
          onGoLive={() => handleBulkGoLive(paperMon, "paper")}
        />
      </div>
    </div>
  );
}

function GroupCard({ icon: Icon, label, color, iconBg, saved, monitoring, live, acting, engineRunning, onActivate, onGoLive }: {
  icon: typeof Zap;
  label: string;
  color: string;
  iconBg: string;
  saved: number;
  monitoring: number;
  live: number;
  acting: boolean;
  engineRunning: boolean;
  onActivate: () => void;
  onGoLive: () => void;
}) {
  const total = saved + monitoring + live;
  if (total === 0) return (
    <div className="bg-ag-dark/50 rounded-lg p-3">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={14} className={color} />
        <span className={`text-xs font-semibold ${color}`}>{label}</span>
      </div>
      <p className="text-[10px] text-ag-text-muted">No portfolios</p>
    </div>
  );

  return (
    <div className="bg-ag-dark/50 rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Icon size={14} className={color} />
          <span className={`text-xs font-semibold ${color}`}>{label}</span>
          <span className="text-[10px] text-ag-text-muted">({total})</span>
        </div>
      </div>

      {/* Status counts */}
      <div className="flex gap-3 text-[10px] mb-3">
        {saved > 0 && <span className="text-ag-text-muted">{saved} saved</span>}
        {monitoring > 0 && <span className="text-yellow-400">{monitoring} monitoring</span>}
        {live > 0 && <span className="text-ag-green">{live} live</span>}
      </div>

      {/* Action buttons */}
      <div className="flex gap-1.5">
        {saved > 0 && (
          <button
            onClick={onActivate}
            disabled={acting}
            className="px-2.5 py-1 bg-ag-green/20 text-ag-green border border-ag-green/30 rounded text-[10px] hover:bg-ag-green/30 disabled:opacity-50"
          >
            {acting ? "..." : `Activate All (${saved})`}
          </button>
        )}
        {monitoring > 0 && (
          <button
            onClick={onGoLive}
            disabled={acting || !engineRunning}
            title={!engineRunning ? "Start the engine first" : undefined}
            className="px-2.5 py-1 bg-ag-accent/20 text-ag-accent border border-ag-accent/30 rounded text-[10px] hover:bg-ag-accent/30 disabled:opacity-50"
          >
            {acting ? "..." : `Go Live All (${monitoring})`}
          </button>
        )}
        {saved === 0 && monitoring === 0 && live > 0 && (
          <span className="text-[10px] text-ag-green">All trading</span>
        )}
      </div>
    </div>
  );
}
