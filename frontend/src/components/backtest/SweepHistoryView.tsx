"use client";
import { useState } from "react";
import { Trash2, ChevronDown, ChevronUp, Loader2, Play, Square } from "lucide-react";
import type { SweepRunItem, SweepDetail } from "@/lib/api";
import { api } from "@/lib/api";
import SweepComparisonTable from "./SweepComparisonTable";
import { useTaskManager } from "@/contexts/TaskManager";

interface Props {
  sweeps: SweepRunItem[];
  onRefresh: () => void;
}

export default function SweepHistoryView({ sweeps, onRefresh }: Props) {
  const { addToast } = useTaskManager();
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<SweepDetail | null>(null);
  const [loadingId, setLoadingId] = useState<number | null>(null);
  const [resumingId, setResumingId] = useState<number | null>(null);
  const [stoppingId, setStoppingId] = useState<number | null>(null);

  const handleExpand = async (id: number) => {
    if (expandedId === id) { setExpandedId(null); setDetail(null); return; }
    setExpandedId(id);
    setLoadingId(id);
    try {
      const d = await api.sweepDetail(id);
      setDetail(d);
    } catch { addToast("Failed to load sweep detail", "error"); setDetail(null); }
    setLoadingId(null);
  };

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Delete sweep "${name}" and all its child batches?`)) return;
    try {
      await api.deleteSweep(id);
      addToast("Sweep deleted", "success");
      onRefresh();
    } catch (e) {
      addToast(e instanceof Error ? e.message : "Failed to delete sweep", "error");
    }
  };

  const handleResume = async (id: number) => {
    if (!confirm("Resume this sweep? It will pick up where it left off.")) return;
    setResumingId(id);
    try {
      const res = await api.resumeSweep(id);
      if (res.status === "completed") {
        addToast("All combos were already completed!", "success");
      } else {
        addToast(`Sweep resumed: ${res.already_completed} done, ${res.remaining} remaining`, "success");
      }
      onRefresh();
    } catch (e) {
      addToast(e instanceof Error ? e.message : "Failed to resume sweep", "error");
    }
    setResumingId(null);
  };

  const handleStop = async (id: number) => {
    if (!confirm("Stop this sweep? Completed combos will be saved and you can resume later.")) return;
    setStoppingId(id);
    try {
      const res = await api.cancelSweep(id);
      addToast(
        `Sweep stopped: ${res.completed_count ?? 0} combos saved. You can resume anytime.`,
        "success",
      );
      onRefresh();
    } catch (e) {
      addToast(e instanceof Error ? e.message : "Failed to stop sweep", "error");
    }
    setStoppingId(null);
  };

  if (sweeps.length === 0) {
    return <p className="text-sm text-ag-text-muted py-8 text-center">No sweep runs yet.</p>;
  }

  return (
    <div className="space-y-3">
      {sweeps.map((s) => {
        const isExpanded = expandedId === s.id;
        const params = s.sweep_config?.params ?? [];
        const paramNames = params.map((p) => p.name).join(", ");
        const canResume = s.status === "interrupted" || s.status === "cancelled";
        return (
          <div key={s.id} className="bg-ag-card border border-ag-border rounded-xl overflow-hidden">
            <div className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-ag-dark/50"
              onClick={() => handleExpand(s.id)}>
              {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">{s.name}</div>
                <div className="text-[10px] text-ag-text-muted">
                  {s.completed_combos !== undefined && s.completed_combos < s.total_combos
                    ? `${s.completed_combos}/${s.total_combos} combos`
                    : `${s.total_combos} combos`
                  } · {s.sweep_config?.mode ?? "grid"} · {paramNames}
                  {s.created_at ? ` · ${new Date(s.created_at).toLocaleDateString()} ${new Date(s.created_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}` : ""}
                </div>
              </div>
              <span className={`text-xs px-2 py-0.5 rounded-full ${
                s.status === "completed" ? "bg-green-500/15 text-green-400"
                : s.status === "running" ? "bg-blue-500/15 text-blue-400"
                : s.status === "interrupted" ? "bg-orange-500/15 text-orange-400"
                : s.status === "cancelled" ? "bg-yellow-500/15 text-yellow-400"
                : "bg-ag-dark text-ag-text-muted"
              }`}>{s.status}</span>
              {s.status === "running" && (
                <button onClick={(e) => { e.stopPropagation(); handleStop(s.id); }}
                  disabled={stoppingId === s.id}
                  className="flex items-center gap-1 px-2.5 py-1 text-xs bg-ag-red/15 text-ag-red border border-ag-red/30 rounded-lg hover:bg-ag-red/25 transition-colors disabled:opacity-50"
                  title="Stop sweep — completed combos are saved, resume later">
                  {stoppingId === s.id ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />}
                  Stop
                </button>
              )}
              {canResume && (
                <button onClick={(e) => { e.stopPropagation(); handleResume(s.id); }}
                  disabled={resumingId === s.id}
                  className="flex items-center gap-1 px-2.5 py-1 text-xs bg-ag-accent/15 text-ag-accent border border-ag-accent/30 rounded-lg hover:bg-ag-accent/25 transition-colors disabled:opacity-50"
                  title="Resume sweep — picks up where it left off">
                  {resumingId === s.id ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                  Resume
                </button>
              )}
              <button onClick={(e) => { e.stopPropagation(); handleDelete(s.id, s.name ?? ""); }}
                className="p-1.5 text-ag-text-muted hover:text-ag-red transition-colors"
                title="Delete sweep">
                <Trash2 size={14} />
              </button>
            </div>
            {isExpanded && (
              <div className="border-t border-ag-border p-4">
                {loadingId === s.id ? (
                  <div className="flex items-center justify-center py-6">
                    <Loader2 size={20} className="animate-spin text-ag-accent" />
                  </div>
                ) : detail && detail.sweep.id === s.id ? (
                  <SweepComparisonTable
                    combos={detail.combos}
                    sweptParams={detail.sweep.sweep_config.params}
                    isLongShort={detail.combos.some((c) => c.batches.length > 1)}
                  />
                ) : (
                  <p className="text-sm text-ag-text-muted text-center py-4">Failed to load</p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
