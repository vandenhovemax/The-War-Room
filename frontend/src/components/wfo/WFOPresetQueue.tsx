"use client";
/**
 * WFOPresetQueue — Batch runner for research presets.
 *
 * Lets the user pick multiple presets, queue them, and run them sequentially
 * (e.g. overnight). Watches the `running` prop: when a run finishes, it
 * automatically applies + triggers the next preset in the queue.
 *
 * Persistence: queue selection + last progress are saved to localStorage so a
 * reload doesn't lose state. Hitting "Stop" pauses the auto-advance but keeps
 * the queue intact.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ListChecks, Play, Square, Trash2, ChevronDown, ChevronUp, CheckCircle2, Loader2, Clock, X } from "lucide-react";
import { RESEARCH_PRESETS, type ResearchPreset } from "./WFOResearchPresets";

const LS_QUEUE_KEY = "wfo_preset_queue_v1";
const LS_PROGRESS_KEY = "wfo_preset_queue_progress_v1";

type ItemStatus = "pending" | "running" | "done" | "failed" | "skipped";
interface QueueItem {
  id: string;
  status: ItemStatus;
  startedAt?: number;
  finishedAt?: number;
}

interface Props {
  applyAndRun: (preset: ResearchPreset) => void;
  running: boolean;
}

function loadJSON<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}
function saveJSON(key: string, value: unknown) {
  if (typeof window === "undefined") return;
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore quota */ }
}

function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m${rem.toString().padStart(2, "0")}s`;
}

export default function WFOPresetQueue({ applyAndRun, running }: Props) {
  const [open, setOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [queueIds, setQueueIds] = useState<string[]>(() => loadJSON<string[]>(LS_QUEUE_KEY, []));
  const [items, setItems] = useState<QueueItem[]>(() => loadJSON<QueueItem[]>(LS_PROGRESS_KEY, []));
  const [active, setActive] = useState(false);   // queue is currently auto-advancing
  const [currentIdx, setCurrentIdx] = useState<number>(-1);
  const prevRunningRef = useRef<boolean>(running);
  const startTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Persistence ──
  useEffect(() => { saveJSON(LS_QUEUE_KEY, queueIds); }, [queueIds]);
  useEffect(() => { saveJSON(LS_PROGRESS_KEY, items); }, [items]);

  // ── Group presets by conversation ──
  const grouped = useMemo(() => {
    const groups: Record<string, ResearchPreset[]> = {};
    for (const p of RESEARCH_PRESETS) {
      if (filter && !`${p.name} ${p.description} ${p.id}`.toLowerCase().includes(filter.toLowerCase())) continue;
      if (!groups[p.conversation]) groups[p.conversation] = [];
      groups[p.conversation].push(p);
    }
    return groups;
  }, [filter]);

  const queuePresets = useMemo(
    () => queueIds.map((id) => RESEARCH_PRESETS.find((p) => p.id === id)).filter((p): p is ResearchPreset => !!p),
    [queueIds],
  );

  // ── Toggle preset in queue ──
  const toggle = (id: string) => {
    setQueueIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };
  const removeAt = (idx: number) => {
    setQueueIds((prev) => prev.filter((_, i) => i !== idx));
    setItems((prev) => prev.filter((_, i) => i !== idx));
  };
  const clearAll = () => {
    if (active) return;
    setQueueIds([]);
    setItems([]);
    setCurrentIdx(-1);
  };
  const moveUp = (idx: number) => {
    if (idx <= 0) return;
    setQueueIds((prev) => {
      const c = [...prev];
      [c[idx - 1], c[idx]] = [c[idx], c[idx - 1]];
      return c;
    });
  };
  const moveDown = (idx: number) => {
    setQueueIds((prev) => {
      if (idx >= prev.length - 1) return prev;
      const c = [...prev];
      [c[idx], c[idx + 1]] = [c[idx + 1], c[idx]];
      return c;
    });
  };

  // ── Run a single queue item ──
  const runItem = (idx: number) => {
    if (idx < 0 || idx >= queueIds.length) return;
    const preset = RESEARCH_PRESETS.find((p) => p.id === queueIds[idx]);
    if (!preset) {
      // Mark missing preset as skipped, advance
      setItems((prev) => {
        const c = [...prev];
        c[idx] = { id: queueIds[idx], status: "skipped", startedAt: Date.now(), finishedAt: Date.now() };
        return c;
      });
      // chain to next
      const next = idx + 1;
      if (next < queueIds.length) {
        startTimerRef.current = setTimeout(() => runItem(next), 300);
        setCurrentIdx(next);
      } else {
        setActive(false);
        setCurrentIdx(-1);
      }
      return;
    }
    setCurrentIdx(idx);
    setItems((prev) => {
      const c = [...prev];
      c[idx] = { id: preset.id, status: "running", startedAt: Date.now() };
      return c;
    });
    // Apply preset → trigger run. applyAndRun handles state-flush delay.
    applyAndRun(preset);
  };

  // ── Start the queue ──
  const startQueue = () => {
    if (queueIds.length === 0 || running || active) return;
    // Initialise items as pending
    setItems(queueIds.map((id) => ({ id, status: "pending" as ItemStatus })));
    setActive(true);
    // Tiny delay so React commits the active=true state before runItem fires
    startTimerRef.current = setTimeout(() => runItem(0), 100);
  };

  // ── Stop the queue (does not interrupt the in-flight run) ──
  const stopQueue = () => {
    setActive(false);
    if (startTimerRef.current) {
      clearTimeout(startTimerRef.current);
      startTimerRef.current = null;
    }
  };

  // ── Resume from where we left off ──
  const resumeQueue = () => {
    if (queueIds.length === 0 || running || active) return;
    // Find first non-done item
    const startFrom = items.findIndex((it) => it && (it.status === "pending" || it.status === "running"));
    const idx = startFrom === -1 ? 0 : startFrom;
    // Re-init any missing items
    setItems((prev) => {
      const next = queueIds.map((id, i) => prev[i] ?? { id, status: "pending" as ItemStatus });
      return next;
    });
    setActive(true);
    startTimerRef.current = setTimeout(() => runItem(idx), 100);
  };

  // ── Auto-advance: detect running false→true→false for current item ──
  useEffect(() => {
    const wasRunning = prevRunningRef.current;
    prevRunningRef.current = running;
    if (!active) return;
    // Transition from running → idle = current item finished
    if (wasRunning && !running) {
      setItems((prev) => {
        const c = [...prev];
        if (c[currentIdx]) c[currentIdx] = { ...c[currentIdx], status: "done", finishedAt: Date.now() };
        return c;
      });
      const next = currentIdx + 1;
      if (next >= queueIds.length) {
        setActive(false);
        setCurrentIdx(-1);
      } else {
        // delay so the saved-runs refresh / UI settles before kicking next
        startTimerRef.current = setTimeout(() => runItem(next), 1500);
      }
    }
  }, [running, active, currentIdx, queueIds.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Cleanup ──
  useEffect(() => () => { if (startTimerRef.current) clearTimeout(startTimerRef.current); }, []);

  const doneCount = items.filter((i) => i?.status === "done").length;
  const totalCount = queueIds.length;
  const conversations = Object.keys(grouped).sort();

  return (
    <div className="border border-amber-500/40 rounded-lg overflow-hidden bg-amber-500/5">
      {/* ── Header ── */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-amber-500/10 transition-colors"
      >
        <ListChecks size={14} className="text-amber-400" />
        <span className="font-semibold text-amber-300">Preset Queue Runner</span>
        <span className="text-[10px] text-amber-300/70">
          {totalCount === 0
            ? "Run multiple presets sequentially (overnight batch mode)"
            : `${doneCount}/${totalCount} done${active ? " • RUNNING" : ""}`}
        </span>
        {open ? <ChevronUp size={13} className="ml-auto text-amber-300" /> : <ChevronDown size={13} className="ml-auto text-amber-300" />}
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-3 border-t border-amber-500/30">
          {/* ── Controls ── */}
          <div className="flex flex-wrap items-center gap-2 pt-3">
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              disabled={active}
              className="px-3 py-1.5 text-[11px] rounded bg-amber-500/20 text-amber-200 hover:bg-amber-500/30 border border-amber-500/40 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
            >
              <ListChecks size={12} /> Pick presets
            </button>

            {!active && doneCount === 0 && (
              <button
                type="button"
                onClick={startQueue}
                disabled={queueIds.length === 0 || running}
                className="px-3 py-1.5 text-[11px] rounded bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 font-semibold"
              >
                <Play size={12} /> Start queue ({queueIds.length})
              </button>
            )}

            {!active && doneCount > 0 && doneCount < totalCount && (
              <button
                type="button"
                onClick={resumeQueue}
                disabled={running}
                className="px-3 py-1.5 text-[11px] rounded bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 font-semibold"
              >
                <Play size={12} /> Resume ({totalCount - doneCount} left)
              </button>
            )}

            {active && (
              <button
                type="button"
                onClick={stopQueue}
                className="px-3 py-1.5 text-[11px] rounded bg-rose-600 text-white hover:bg-rose-500 flex items-center gap-1.5 font-semibold"
              >
                <Square size={12} /> Stop after current
              </button>
            )}

            <button
              type="button"
              onClick={clearAll}
              disabled={active}
              className="ml-auto px-2 py-1.5 text-[11px] rounded bg-zinc-700/40 text-zinc-300 hover:bg-zinc-700/60 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
            >
              <Trash2 size={12} /> Clear
            </button>
          </div>

          {/* ── Hint ── */}
          <p className="text-[10px] text-amber-300/60 leading-relaxed">
            Pick the presets you want to run, then click <span className="font-semibold">Start queue</span>.
            Each preset is applied and executed in order — when one finishes, the next starts automatically.
            Safe to leave running overnight: progress is saved to your browser, so you can refresh and{" "}
            <span className="font-semibold">Resume</span> if needed. The currently-running preset will not be interrupted by Stop.
          </p>

          {/* ── Queue list ── */}
          {queueIds.length === 0 ? (
            <div className="text-[11px] text-amber-300/50 italic py-2 text-center border border-dashed border-amber-500/20 rounded">
              No presets in queue. Click "Pick presets" to add some.
            </div>
          ) : (
            <div className="space-y-1 max-h-72 overflow-y-auto">
              {queuePresets.map((preset, idx) => {
                const item = items[idx];
                const status = item?.status ?? "pending";
                const isCurrent = active && idx === currentIdx;
                const dur = item?.startedAt && item?.finishedAt ? fmtDuration(item.finishedAt - item.startedAt) : null;
                return (
                  <div
                    key={`${preset.id}-${idx}`}
                    className={`flex items-center gap-2 px-2 py-1.5 rounded text-[11px] border ${
                      isCurrent
                        ? "bg-amber-500/20 border-amber-400"
                        : status === "done"
                        ? "bg-emerald-500/10 border-emerald-500/30"
                        : status === "failed"
                        ? "bg-rose-500/10 border-rose-500/30"
                        : "bg-zinc-800/40 border-zinc-700/50"
                    }`}
                  >
                    <span className="text-zinc-500 font-mono w-6 text-right">{idx + 1}.</span>
                    <span className="w-4 flex-shrink-0">
                      {status === "running" && <Loader2 size={12} className="animate-spin text-amber-400" />}
                      {status === "done" && <CheckCircle2 size={12} className="text-emerald-400" />}
                      {status === "pending" && <Clock size={12} className="text-zinc-500" />}
                      {status === "failed" && <X size={12} className="text-rose-400" />}
                      {status === "skipped" && <X size={12} className="text-zinc-500" />}
                    </span>
                    <span className="font-medium text-zinc-200 flex-1 truncate" title={preset.description}>
                      {preset.name}
                    </span>
                    <span className="text-zinc-500 text-[10px] hidden md:inline truncate max-w-[180px]">
                      {preset.conversation}
                    </span>
                    {dur && <span className="text-zinc-400 text-[10px] font-mono">{dur}</span>}
                    {!active && (
                      <div className="flex items-center gap-0.5">
                        <button
                          type="button"
                          onClick={() => moveUp(idx)}
                          disabled={idx === 0}
                          className="px-1 text-zinc-400 hover:text-zinc-200 disabled:opacity-30"
                          title="Move up"
                        >
                          <ChevronUp size={12} />
                        </button>
                        <button
                          type="button"
                          onClick={() => moveDown(idx)}
                          disabled={idx === queueIds.length - 1}
                          className="px-1 text-zinc-400 hover:text-zinc-200 disabled:opacity-30"
                          title="Move down"
                        >
                          <ChevronDown size={12} />
                        </button>
                        <button
                          type="button"
                          onClick={() => removeAt(idx)}
                          className="px-1 text-rose-400 hover:text-rose-300"
                          title="Remove"
                        >
                          <X size={12} />
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ── Picker modal ── */}
      {pickerOpen && (
        <div
          className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4"
          onClick={() => setPickerOpen(false)}
        >
          <div
            className="bg-zinc-900 border border-amber-500/40 rounded-lg max-w-3xl w-full max-h-[85vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-700">
              <ListChecks size={16} className="text-amber-400" />
              <h3 className="text-sm font-semibold text-amber-300">Pick presets for the queue</h3>
              <span className="text-[11px] text-zinc-400 ml-2">{queueIds.length} selected</span>
              <button
                type="button"
                onClick={() => setPickerOpen(false)}
                className="ml-auto text-zinc-400 hover:text-white"
              >
                <X size={18} />
              </button>
            </div>
            <div className="px-4 py-2 border-b border-zinc-800">
              <input
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter presets by name, id, or description..."
                className="w-full px-3 py-1.5 text-xs bg-zinc-800 border border-zinc-700 rounded text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:border-amber-500/60"
              />
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-2 space-y-3">
              {conversations.length === 0 && (
                <div className="text-[11px] text-zinc-500 italic text-center py-6">No presets match.</div>
              )}
              {conversations.map((conv) => (
                <div key={conv}>
                  <div className="text-[10px] uppercase tracking-wide text-amber-400/70 font-semibold mb-1 flex items-center gap-2">
                    <span>{conv}</span>
                    <button
                      type="button"
                      onClick={() => {
                        const ids = grouped[conv].map((p) => p.id);
                        const allIn = ids.every((id) => queueIds.includes(id));
                        if (allIn) {
                          setQueueIds((prev) => prev.filter((id) => !ids.includes(id)));
                        } else {
                          setQueueIds((prev) => Array.from(new Set([...prev, ...ids])));
                        }
                      }}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 normal-case"
                    >
                      toggle group
                    </button>
                  </div>
                  <div className="space-y-1">
                    {grouped[conv].map((p) => {
                      const checked = queueIds.includes(p.id);
                      return (
                        <label
                          key={p.id}
                          className={`flex items-start gap-2 px-2 py-1.5 rounded text-[11px] cursor-pointer border ${
                            checked
                              ? "bg-amber-500/10 border-amber-500/40"
                              : "bg-zinc-800/40 border-zinc-700/40 hover:bg-zinc-800/60"
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggle(p.id)}
                            className="mt-0.5 accent-amber-500"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="font-medium text-zinc-100">{p.name}</div>
                            <div className="text-zinc-500 text-[10px] truncate">{p.description}</div>
                          </div>
                        </label>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
            <div className="px-4 py-3 border-t border-zinc-800 flex items-center gap-2">
              <button
                type="button"
                onClick={() => { setQueueIds([]); }}
                className="px-3 py-1.5 text-[11px] rounded bg-zinc-700/40 text-zinc-300 hover:bg-zinc-700/60"
              >
                Deselect all
              </button>
              <span className="text-[11px] text-zinc-500 ml-2">{queueIds.length} preset(s) in queue</span>
              <button
                type="button"
                onClick={() => setPickerOpen(false)}
                className="ml-auto px-3 py-1.5 text-[11px] rounded bg-amber-500 text-zinc-900 hover:bg-amber-400 font-semibold"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
