"use client";
import { useCallback } from "react";
import {
  FlaskConical, Briefcase, BookOpen, Clock,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ActivityEvent } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";

const iconMap: Record<string, typeof FlaskConical> = {
  flask: FlaskConical,
  briefcase: Briefcase,
  book: BookOpen,
};

function timeAgo(ts: string | null): string {
  if (!ts) return "";
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export default function ActivityFeed() {
  const fetcher = useCallback(() => api.activityRecent(8), []);
  const { data, loading } = usePolling(fetcher, 120_000);
  const events = data?.events ?? [];

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <div className="flex items-center gap-2.5 mb-4">
        <div className="p-2 rounded-lg bg-ag-accent/10 text-ag-accent">
          <Clock size={18} />
        </div>
        <h4 className="font-semibold">Recent Activity</h4>
      </div>

      {loading && events.length === 0 ? (
        <p className="text-sm text-ag-text-muted">Loading...</p>
      ) : events.length === 0 ? (
        <p className="text-sm text-ag-text-muted">
          No recent activity. Run a backtest to get started!
        </p>
      ) : (
        <div className="space-y-3">
          {events.map((ev: ActivityEvent, i: number) => {
            const Icon = iconMap[ev.icon] ?? FlaskConical;
            return (
              <div key={`${ev.type}-${i}`} className="flex items-start gap-3">
                <div className="mt-0.5 p-1.5 rounded-md bg-ag-bg text-ag-text-muted">
                  <Icon size={14} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium truncate">
                      {ev.title}
                    </span>
                    <span className="text-[11px] text-ag-text-muted ml-2 flex-shrink-0">
                      {timeAgo(ev.timestamp)}
                    </span>
                  </div>
                  <p className="text-xs text-ag-text-muted truncate">
                    {ev.detail}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
