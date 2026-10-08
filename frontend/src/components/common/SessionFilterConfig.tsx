"use client";
import { useState, useEffect } from "react";
import HintTip from "./HintTip";
import SavedMasksList from "./SavedMasksList";
import { api } from "@/lib/api";
import type { SessionTimeMask } from "@/lib/api";

/* ── Types ─────────────────────────────────────────── */
export interface SessionFilterState {
  enabled: boolean;
  mode: "fixed" | "auto" | "saved_mask";
  excludeDays: number[];
  excludeHours: number[];
  autoMinTrades: number;
  autoIncludeInsufficient: boolean;
  gradeOnCropped: boolean;
  savedMaskId: number | null;
}

export const defaultSessionFilter: SessionFilterState = {
  enabled: false,
  mode: "fixed",
  excludeDays: [],
  excludeHours: [],
  autoMinTrades: 10,
  autoIncludeInsufficient: true,
  gradeOnCropped: false,
  savedMaskId: null,
};

interface Props {
  value: SessionFilterState;
  onChange: (v: SessionFilterState) => void;
}

/* ── Hour handling ──────────────────────────────────
 * IMPORTANT: entry_time in the DB is stored as NYT (naive datetime).
 * The backend filters on entry_time.hour directly → these are NYT hours.
 * excludeHours[] stores NYT hours. No UTC conversion needed.
 * ─────────────────────────────────────────────────── */
const NYT_HOURS = Array.from({ length: 24 }, (_, nyt) => nyt);

/* ── Named session presets (NYT hours — stored and sent as-is) ── */
const SESSION_PRESETS = [
  { name: "Asia",          nytHours: [19, 20, 21, 22, 23, 0], color: "blue" },
  { name: "London",        nytHours: [3, 4, 5],               color: "green" },
  { name: "NY AM",         nytHours: [8, 9, 10, 11],          color: "red" },
  { name: "NY PM",         nytHours: [12, 13, 14, 15],        color: "red" },
  { name: "Post-Market",   nytHours: [16, 17, 18],            color: "purple" },
] as const;

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const PRESET_COLORS: Record<string, string> = {
  blue: "bg-blue-500/20 border-blue-500/50 text-blue-300",
  green: "bg-green-500/20 border-green-500/50 text-green-300",
  red: "bg-red-500/20 border-red-500/50 text-red-300",
  purple: "bg-purple-500/20 border-purple-500/50 text-purple-300",
  cyan: "bg-cyan-500/20 border-cyan-500/50 text-cyan-300",
};

/* ── Component ─────────────────────────────────────── */
export default function SessionFilterConfig({ value, onChange }: Props) {
  const [masks, setMasks] = useState<SessionTimeMask[]>([]);

  useEffect(() => {
    if (value.enabled && value.mode === "saved_mask") {
      api.sessionMasks().then(setMasks).catch(() => {});
    }
  }, [value.enabled, value.mode]);

  const set = (patch: Partial<SessionFilterState>) =>
    onChange({ ...value, ...patch });

  const toggleDay = (d: number) => {
    const next = value.excludeDays.includes(d)
      ? value.excludeDays.filter((x) => x !== d)
      : [...value.excludeDays, d];
    set({ excludeDays: next });
  };

  const toggleHour = (h: number) => {
    const next = value.excludeHours.includes(h)
      ? value.excludeHours.filter((x) => x !== h)
      : [...value.excludeHours, h];
    set({ excludeHours: next });
  };

  const togglePreset = (nytHours: readonly number[]) => {
    // Hours are already NYT — store directly, no conversion needed
    const allExcluded = nytHours.every((h) => value.excludeHours.includes(h));
    if (allExcluded) {
      const hourSet = new Set(nytHours);
      set({ excludeHours: value.excludeHours.filter((h) => !hourSet.has(h)) });
    } else {
      const merged = new Set([...value.excludeHours, ...nytHours]);
      set({ excludeHours: [...merged] });
    }
  };

  return (
    <div className="space-y-2">
      {/* Master toggle */}
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={value.enabled}
          onChange={(e) => set({ enabled: e.target.checked })}
          className="accent-ag-accent"
        />
        <span className="text-xs font-medium text-ag-text">Session Filter</span>
        <HintTip text="Filter trades by time-of-day and day-of-week before optimization. Fixed = manual exclusions; Auto = keep only profitable hours/days; Saved Mask = use a previously saved mask." />
      </label>

      {!value.enabled ? null : (
        <div className="pl-1 space-y-3">
          {/* Mode selector */}
          <div className="flex gap-1.5">
            {(["fixed", "auto", "saved_mask"] as const).map((m) => (
              <button
                key={m}
                onClick={() => set({ mode: m })}
                className={`px-2.5 py-1 text-xs rounded-lg border transition-colors ${
                  value.mode === m
                    ? "bg-ag-accent/20 border-ag-accent text-ag-accent"
                    : "bg-ag-card border-ag-border text-ag-text-muted hover:border-ag-accent/50"
                }`}
              >
                {m === "fixed" ? "Fixed" : m === "auto" ? "Auto" : "Saved Mask"}
              </button>
            ))}
          </div>

          {/* ── Fixed mode ──────────────────────── */}
          {value.mode === "fixed" && (
            <FixedPanel
              excludeDays={value.excludeDays}
              excludeHoursNyt={value.excludeHours}
              onToggleDay={toggleDay}
              onToggleHour={toggleHour}
              onTogglePreset={togglePreset}
            />
          )}

          {/* ── Auto mode ──────────────────────── */}
          {value.mode === "auto" && (
            <AutoPanel
              autoMinTrades={value.autoMinTrades}
              autoIncludeInsufficient={value.autoIncludeInsufficient}
              onMinTradesChange={(v) => set({ autoMinTrades: v })}
              onIncludeInsChange={(v) => set({ autoIncludeInsufficient: v })}
            />
          )}

          {/* ��─ Saved Mask mode ────────────────── */}
          {value.mode === "saved_mask" && (
            <SavedMaskPanel
              masks={masks}
              selectedId={value.savedMaskId}
              onSelect={(id) => set({ savedMaskId: id })}
              onRefresh={() => api.sessionMasks().then(setMasks).catch(() => {})}
            />
          )}

          {/* Grade on cropped toggle (all modes) */}
          <label className="flex items-center gap-2 cursor-pointer pt-1 border-t border-ag-border/50">
            <input
              type="checkbox"
              checked={value.gradeOnCropped}
              onChange={(e) => set({ gradeOnCropped: e.target.checked })}
              className="accent-ag-accent"
            />
            <span className="text-[11px] text-ag-text-muted">Grade on cropped data</span>
            <HintTip text="OFF (default): optimizer filters trades for asset selection, but IS grading uses ALL trades. ON: both optimization AND grading use only the filtered trades." />
          </label>
        </div>
      )}
    </div>
  );
}

/* ── Fixed sub-panel ───────────────────────────────── */
function FixedPanel({
  excludeDays, excludeHoursNyt, onToggleDay, onToggleHour, onTogglePreset,
}: {
  excludeDays: number[];
  excludeHoursNyt: number[];
  onToggleDay: (d: number) => void;
  onToggleHour: (nytH: number) => void;
  onTogglePreset: (nytHours: readonly number[]) => void;
}) {
  return (
    <div className="space-y-2.5">
      {/* Days */}
      <div className="space-y-1">
        <div className="flex items-center gap-1">
          <span className="text-[11px] text-ag-text-muted font-medium">Exclude Days</span>
          <HintTip text="Check days to exclude from trading. Trades opening on excluded days are removed before optimization." />
        </div>
        <div className="flex gap-1">
          {DAY_NAMES.map((name, i) => {
            const excluded = excludeDays.includes(i);
            return (
              <button
                key={i}
                onClick={() => onToggleDay(i)}
                className={`px-2 py-0.5 text-[11px] rounded border transition-colors ${
                  excluded
                    ? "bg-red-500/20 border-red-500/50 text-red-300"
                    : "bg-ag-card border-ag-border text-ag-text-muted hover:border-ag-accent/50"
                }`}
              >
                {name}
              </button>
            );
          })}
        </div>
      </div>

      {/* Session presets */}
      <div className="space-y-1">
        <div className="flex items-center gap-1">
          <span className="text-[11px] text-ag-text-muted font-medium">Session Presets</span>
          <HintTip text="Click a session to exclude all its hours. Click again to re-include. All times are NYT (New York Time). These values are sent directly to the backend which filters on entry_time.hour (also NYT)." />
        </div>
        <div className="flex flex-wrap gap-1">
          {SESSION_PRESETS.map((p) => {
            const allExcl = p.nytHours.every((h) => excludeHoursNyt.includes(h));
            const first = p.nytHours[0];
            const last = p.nytHours[p.nytHours.length - 1];
            return (
              <button
                key={p.name}
                onClick={() => onTogglePreset(p.nytHours)}
                className={`px-2 py-0.5 text-[11px] rounded border transition-colors ${
                  allExcl
                    ? PRESET_COLORS[p.color]
                    : "bg-ag-card border-ag-border text-ag-text-muted hover:border-ag-accent/50"
                }`}
              >
                {p.name} ({first > 12 ? first - 12 : first}{first >= 12 ? "p" : "a"}-{last > 12 ? last - 12 : last}{last >= 12 ? "p" : "a"})
              </button>
            );
          })}
        </div>
      </div>

      {/* Individual hours — NYT throughout */}
      <div className="space-y-1">
        <div className="flex items-center gap-1">
          <span className="text-[11px] text-ag-text-muted font-medium">Exclude Hours (NYT)</span>
          <HintTip text="Hours in New York Time (NYT). Click to exclude. Red = excluded. These values are sent directly to the backend — no UTC conversion needed since trade timestamps are stored as NYT." />
        </div>
        <div className="grid grid-cols-12 gap-0.5">
          {NYT_HOURS.map((nyt) => {
            const excluded = excludeHoursNyt.includes(nyt);
            const ampm = nyt >= 12 ? "p" : "a";
            const h12 = nyt === 0 ? 12 : nyt > 12 ? nyt - 12 : nyt;
            return (
              <button
                key={nyt}
                onClick={() => onToggleHour(nyt)}
                title={`NYT ${nyt}:00`}
                className={`py-0.5 text-[10px] rounded border transition-colors ${
                  excluded
                    ? "bg-red-500/20 border-red-500/50 text-red-300"
                    : "bg-ag-card border-ag-border text-ag-text-muted hover:border-ag-accent/50"
                }`}
              >
                {h12}{ampm}
              </button>
            );
          })}
        </div>
      </div>

      {/* Summary */}
      {(excludeDays.length > 0 || excludeHoursNyt.length > 0) && (
        <div className="text-[10px] text-ag-text-muted">
          Excluding: {excludeDays.length} day(s), {excludeHoursNyt.length} hour(s)
          <span className="text-ag-text-muted/40 ml-2">(NYT hours: {[...excludeHoursNyt].sort((a, b) => a - b).join(", ")})</span>
        </div>
      )}
    </div>
  );
}

/* ── Auto sub-panel ────────────────────────────────── */
function AutoPanel({
  autoMinTrades, autoIncludeInsufficient, onMinTradesChange, onIncludeInsChange,
}: {
  autoMinTrades: number;
  autoIncludeInsufficient: boolean;
  onMinTradesChange: (v: number) => void;
  onIncludeInsChange: (v: boolean) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="text-[11px] text-ag-text-muted leading-snug">
        Automatically keeps only hours and days with positive PnL from the IS data.
        In WFO, each window computes its own mask.
      </div>
      <div className="flex items-center gap-3">
        <label className="text-[11px] text-ag-text-muted">
          Min trades per slot
          <HintTip text="Hours/days must have at least this many trades AND positive PnL to be kept. Set higher for more statistical confidence." />
        </label>
        <input
          type="number"
          min={1}
          max={200}
          value={autoMinTrades}
          onChange={(e) => onMinTradesChange(Math.max(1, Number(e.target.value)))}
          className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-0.5 text-xs text-ag-text"
        />
      </div>
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={autoIncludeInsufficient}
          onChange={(e) => onIncludeInsChange(e.target.checked)}
          className="accent-ag-accent"
        />
        <span className="text-[11px] text-ag-text-muted">Include insufficient-data slots</span>
        <HintTip text="ON: hours/days with positive PnL but fewer trades than the minimum are still included. OFF: they are excluded (stricter)." />
      </label>
    </div>
  );
}

/* ── Saved Mask sub-panel ──────────────────────────── */
function SavedMaskPanel({
  masks, selectedId, onSelect, onRefresh,
}: {
  masks: SessionTimeMask[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  onRefresh: () => void;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-[11px] text-ag-text-muted flex items-center gap-1">
        Saved Mask
        <HintTip text="Select a previously saved session mask from the Session Intelligence page. The mask's active hours/days will filter the trade pool." />
      </label>
      <select
        value={selectedId ?? ""}
        onChange={(e) => onSelect(e.target.value ? Number(e.target.value) : null)}
        className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm"
      >
        <option value="">None (use all trades)</option>
        {masks.map((m) => (
          <option key={m.id} value={m.id}>{m.name}</option>
        ))}
      </select>
      <SavedMasksList
        compact
        refreshKey={0}
        onMaskDeleted={() => {
          onRefresh();
          if (selectedId) {
            api.sessionMaskDetail(selectedId).catch(() => onSelect(null));
          }
        }}
      />
    </div>
  );
}
