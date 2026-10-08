"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Phase2Event, Phase2EventsResponse } from "@/lib/api";
import { Activity, CheckCircle2, AlertCircle, Clock, WifiOff, Hammer } from "lucide-react";
import HintTip from "@/components/common/HintTip";

interface Props {
  portfolioId: number;
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  hybrid_refresh: "Basket changed",
  hybrid_rotation: "Rotation",
  phase2_evaluated_no_change: "No change",
};

function eventTypeColor(t: string): string {
  if (t === "hybrid_refresh") return "text-ag-blue";
  if (t === "hybrid_rotation") return "text-ag-accent";
  return "text-ag-text-muted";
}

function dispatchBadge(outcome: string | null) {
  if (outcome === "success") return { Icon: CheckCircle2, color: "text-ag-green", label: "dispatched" };
  if (outcome === "failed") return { Icon: AlertCircle, color: "text-ag-red", label: "dispatch failed" };
  if (outcome === "engine_offline") return { Icon: WifiOff, color: "text-ag-text-muted", label: "engine offline" };
  return { Icon: Clock, color: "text-ag-yellow", label: "pending" };
}

function shortBasketKey(k: string): string {
  // "B1_BTCUSDT_Long" → "BTC L"
  const parts = k.split("_");
  const sym = parts[1] ?? k;
  const side = parts[2] ?? "";
  const base = sym.replace("USDT", "");
  if (side === "Long") return `${base} L`;
  if (side === "Short") return `${base} S`;
  return base;
}

export default function Phase2EventFeed({ portfolioId }: Props) {
  const [events, setEvents] = useState<Phase2Event[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.portfolioPhase2Events(portfolioId, { limit: showAll ? 50 : 10, offset: 0 })
      .then((r: Phase2EventsResponse) => {
        if (cancelled) return;
        setEvents(r.events);
        setTotal(r.total);
      })
      .catch(() => {
        if (cancelled) return;
        setError("Failed to load phase-2 events");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [portfolioId, showAll]);

  if (loading) {
    return (
      <div className="bg-ag-dark/30 rounded-lg p-2">
        <div className="flex items-center gap-1.5 mb-1">
          <Activity size={12} className="text-ag-text-muted" />
          <p className="text-[10px] text-ag-text-muted uppercase">Phase-2 Events</p>
        </div>
        <p className="text-[10px] text-ag-text-muted">Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-ag-dark/30 rounded-lg p-2">
        <div className="flex items-center gap-1.5 mb-1">
          <Activity size={12} className="text-ag-text-muted" />
          <p className="text-[10px] text-ag-text-muted uppercase">Phase-2 Events</p>
        </div>
        <p className="text-[10px] text-ag-red">{error}</p>
      </div>
    );
  }

  if (total === 0) return null;

  return (
    <div className="bg-ag-dark/30 rounded-lg p-2">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1.5">
          <Activity size={12} className="text-ag-text-muted" />
          <p className="text-[10px] text-ag-text-muted uppercase">Phase-2 Events</p>
          <HintTip text={
            "Nightly phase-2 evaluations recorded by the live engine. " +
            "Each row shows what the optimizer decided at decision time " +
            "(basket diff, dispatch outcome). Synthetic baseline rows " +
            "(cold-save) are marked separately."
          } />
        </div>
        <span className="text-[9px] text-ag-text-muted">
          {events.length} of {total}
        </span>
      </div>
      <div className="space-y-1 max-h-[260px] overflow-y-auto">
        {events.map((ev) => {
          const d = ev.details;
          const date = ev.created_at ? new Date(ev.created_at) : null;
          const typeColor = eventTypeColor(ev.event_type);
          const typeLabel = EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type;
          const dispatch = dispatchBadge(d.dispatch_outcome);
          const Icon = dispatch.Icon;
          return (
            <div key={ev.id} className="flex items-center gap-2 text-[10px] py-0.5">
              <span className="text-ag-text-muted w-[64px] shrink-0">
                {date ? date.toLocaleDateString() : "—"}
              </span>
              <span className={`${typeColor} w-[88px] shrink-0`}>{typeLabel}</span>
              <div className="flex items-center gap-1 flex-1 min-w-0">
                {d.basket_unchanged ? (
                  <span className="text-ag-text-muted truncate">
                    {(d.chosen_basket || []).slice(0, 4).map(shortBasketKey).join(", ")}
                    {(d.chosen_basket || []).length > 4 ? "…" : ""}
                  </span>
                ) : (
                  <span className="truncate">
                    {d.removed.length > 0 && (
                      <span className="text-ag-red">
                        −{d.removed.map(shortBasketKey).join(", ")}
                      </span>
                    )}
                    {d.removed.length > 0 && d.added.length > 0 && (
                      <span className="text-ag-text-muted"> / </span>
                    )}
                    {d.added.length > 0 && (
                      <span className="text-ag-green">
                        +{d.added.map(shortBasketKey).join(", ")}
                      </span>
                    )}
                  </span>
                )}
              </div>
              <span
                className={`flex items-center gap-0.5 ${dispatch.color} shrink-0`}
                title={dispatch.label}
              >
                <Icon size={10} />
              </span>
              {d.reconstructed && (
                <span
                  className="flex items-center gap-0.5 text-ag-text-muted shrink-0"
                  title={
                    d.trigger === "cold_save"
                      ? "Synthetic baseline (cold-save)"
                      : "Reconstructed event"
                  }
                >
                  <Hammer size={10} />
                </span>
              )}
            </div>
          );
        })}
      </div>
      {!showAll && total > events.length && (
        <button
          onClick={() => setShowAll(true)}
          className="mt-1 text-[10px] text-ag-blue hover:underline"
        >
          Show more ({total - events.length} more)
        </button>
      )}
    </div>
  );
}
