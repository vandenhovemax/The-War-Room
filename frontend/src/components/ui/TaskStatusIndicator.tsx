"use client";
import { useState, useEffect, useRef } from "react";
import { useTaskManager } from "@/contexts/TaskManager";
import { Loader2, Square, ChevronDown, StopCircle, ListOrdered } from "lucide-react";
import type { AnyTask, BatchTask, DownloadTask, OptimizationTask } from "@/contexts/taskTypes";

function formatEta(ms: number): string {
  if (ms <= 0) return "";
  const totalSec = Math.ceil(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return sec > 0 ? `${min}m ${sec}s` : `${min}m`;
}

function getTaskProgress(task: AnyTask, now: number): { pct: number; eta: string; label: string; loading: boolean } {
  if (task.type === "batch") {
    const bt = task as BatchTask;
    const total = Object.values(bt.progressMap).reduce((s, p) => s + (p.total_symbols || 0), 0);
    const done = Object.values(bt.progressMap).reduce((s, p) => s + (p.completed_symbols || 0), 0);
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    const loading = Object.values(bt.progressMap).some((p) => p.phase === "loading");
    let eta = "";
    if (done > 0 && done < total && !loading) {
      const elapsed = now - bt.startedAt;
      const remaining = ((total - done) / done) * elapsed;
      eta = formatEta(remaining);
    }
    return { pct, eta, label: "Batch", loading };
  }
  if (task.type === "download") {
    const dl = task as DownloadTask;
    const entries = Object.values(dl.progress);
    const total = entries.length;
    const done = entries.filter((e) => e.status === "done" || e.status === "error" || e.status === "cancelled").length;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    let eta = "";
    if (done > 0 && done < total) {
      const elapsed = now - dl.startedAt;
      const remaining = ((total - done) / done) * elapsed;
      eta = formatEta(remaining);
    }
    return { pct, eta, label: "Download", loading: false };
  }
  if (task.type === "optimization") {
    const ot = task as OptimizationTask;
    const pct = ot.progressPct ?? 0;
    let eta = "";
    if (pct > 0 && pct < 100) {
      const elapsed = now - ot.startedAt;
      const remaining = (elapsed / pct) * (100 - pct);
      eta = formatEta(remaining);
    }
    // Use the task label (e.g. "Cross-Strategy Build" or "Recommender")
    const shortLabel = ot.label.length > 20 ? ot.label.slice(0, 18) + "…" : ot.label;
    return { pct, eta, label: shortLabel, loading: pct === 0 };
  }
  return { pct: 0, eta: "", label: "Build", loading: false };
}

export default function TaskStatusIndicator() {
  const { activeTasks, cancelTask, cancelAllTasks, batchQueue } = useTaskManager();
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const dropRef = useRef<HTMLDivElement>(null);

  // Tick every second for ETA
  useEffect(() => {
    if (activeTasks.length === 0) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [activeTasks.length]);

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  if (activeTasks.length === 0 && batchQueue.length === 0) return null;

  // Summary for the pill
  const firstTask = activeTasks[0];
  const info = firstTask ? getTaskProgress(firstTask, now) : null;
  const parts: string[] = [];
  if (activeTasks.length === 1 && info) {
    parts.push(info.loading ? `${info.label} loading...` : `${info.label} ${info.pct}%`);
  } else if (activeTasks.length > 1) {
    parts.push(`${activeTasks.length} tasks`);
  }
  if (batchQueue.length > 0) {
    parts.push(`${batchQueue.length} queued`);
  }
  const summary = parts.join(" · ");

  return (
    <div className="relative" ref={dropRef}>
      {/* Pill button */}
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-ag-accent/15 text-ag-accent text-xs font-medium hover:bg-ag-accent/25 transition-colors"
      >
        {activeTasks.length > 0 ? (
          <Loader2 size={12} className="animate-spin" />
        ) : (
          <ListOrdered size={12} />
        )}
        <span>{summary}</span>
        {info?.eta && <span className="text-ag-text-muted tabular-nums">{info.eta}</span>}
        <ChevronDown size={10} className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {/* Dropdown with task details */}
      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 bg-ag-darker border border-ag-border rounded-xl shadow-xl z-50 overflow-hidden">
          <div className="px-3 py-2 border-b border-ag-border/50 flex items-center justify-between">
            <span className="text-xs text-ag-text-muted font-medium">Running Tasks</span>
            {(activeTasks.length > 0 || batchQueue.length > 0) && (
              <button
                onClick={() => { if (confirm("Stop ALL tasks and clear queue?")) cancelAllTasks(); }}
                className="flex items-center gap-1 px-2 py-0.5 rounded bg-ag-red/15 text-ag-red text-[10px] font-medium hover:bg-ag-red/25 transition-colors"
              >
                <StopCircle size={10} />
                Stop All
              </button>
            )}
          </div>

          {/* Active tasks */}
          {activeTasks.map((task) => {
            const taskInfo = getTaskProgress(task, now);
            return (
              <div key={task.id} className="px-3 py-2.5 border-b border-ag-border/30 last:border-b-0">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs font-medium text-ag-text truncate flex-1">
                    {task.label}
                  </span>
                  <div className="flex items-center gap-2 shrink-0 ml-2">
                    <span className="text-[11px] text-ag-text-muted tabular-nums">
                      {taskInfo.loading ? "Loading..." : `${taskInfo.pct}%`}
                    </span>
                    <button
                      onClick={() => cancelTask(task.id)}
                      className="p-1 rounded bg-ag-red/15 text-ag-red hover:bg-ag-red/25 transition-colors"
                      title="Stop"
                    >
                      <Square size={10} fill="currentColor" />
                    </button>
                  </div>
                </div>
                {/* Mini progress bar */}
                {taskInfo.loading ? (
                  <div className="h-1.5 bg-ag-dark rounded-full overflow-hidden">
                    <div className="h-full bg-ag-accent/50 rounded-full animate-pulse w-full" />
                  </div>
                ) : (
                  <div className="h-1.5 bg-ag-dark rounded-full overflow-hidden">
                    <div
                      className="h-full bg-ag-accent rounded-full transition-all duration-500"
                      style={{ width: `${taskInfo.pct}%` }}
                    />
                  </div>
                )}
                {taskInfo.eta && (
                  <p className="text-[10px] text-ag-text-muted mt-1 tabular-nums">
                    ~{taskInfo.eta} remaining
                  </p>
                )}
              </div>
            );
          })}

          {/* Queue section */}
          {batchQueue.length > 0 && (
            <>
              <div className="px-3 py-1.5 border-b border-ag-border/50 bg-ag-dark/50">
                <span className="text-[10px] text-ag-text-muted font-medium">
                  Queue ({batchQueue.length} waiting)
                </span>
              </div>
              {batchQueue.map((item, i) => (
                <div key={item.id} className="px-3 py-2 border-b border-ag-border/30 last:border-b-0 flex items-center justify-between">
                  <span className="text-[11px] text-ag-text-muted truncate">
                    <span className="text-ag-text font-medium mr-1">#{i + 1}</span>
                    {item.label}
                  </span>
                </div>
              ))}
            </>
          )}

          {/* Empty state */}
          {activeTasks.length === 0 && batchQueue.length === 0 && (
            <div className="px-3 py-4 text-center text-xs text-ag-text-muted">
              No active tasks
            </div>
          )}
        </div>
      )}
    </div>
  );
}
