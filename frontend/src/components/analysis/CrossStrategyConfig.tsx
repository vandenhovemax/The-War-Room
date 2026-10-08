"use client";
import { useState } from "react";
import HintTip from "@/components/common/HintTip";

export interface PerBatchConfig {
  min_trades: number;
  min_pf: number;
  min_wr: number;
  max_longs: number;
  max_shorts: number;
  // Per-batch L1 engine override (undefined = inherit global setting)
  event_mode?: boolean;
  n_trades?: number;
}

export interface PerBatchAllocation {
  n_longs: number;
  n_shorts: number;
}

interface BatchInfo {
  id: number;
  label: string;
}

interface Props {
  batches: BatchInfo[];
  configs: Record<string, PerBatchConfig>;
  onConfigsChange: (configs: Record<string, PerBatchConfig>) => void;
  allocations?: Record<string, PerBatchAllocation>;
  onAllocationsChange?: (allocs: Record<string, PerBatchAllocation>) => void;
  showAllocation?: boolean;
}

const DEFAULT: PerBatchConfig = { min_trades: 0, min_pf: 0, min_wr: 0, max_longs: 999, max_shorts: 999 };
const DEFAULT_ALLOC: PerBatchAllocation = { n_longs: 5, n_shorts: 5 };

export default function CrossStrategyConfig({
  batches, configs, onConfigsChange,
  allocations, onAllocationsChange, showAllocation,
}: Props) {
  const [expanded, setExpanded] = useState<number | null>(null);

  const update = (bid: number, patch: Partial<PerBatchConfig>) => {
    const key = String(bid);
    const current = configs[key] || { ...DEFAULT };
    onConfigsChange({ ...configs, [key]: { ...current, ...patch } });
  };

  const updateAlloc = (bid: number, patch: Partial<PerBatchAllocation>) => {
    if (!onAllocationsChange) return;
    const key = String(bid);
    const current = allocations?.[key] || { ...DEFAULT_ALLOC };
    onAllocationsChange({ ...(allocations || {}), [key]: { ...current, ...patch } });
  };

  if (batches.length === 0) return null;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg">
      <div className="px-4 py-2.5 text-sm font-medium">
        {showAllocation ? "Per-Strategy Allocation & Filters" : "Per-Strategy Filters"}
      </div>
      <div className="border-t border-ag-border">
        {batches.map((b) => {
          const cfg = configs[String(b.id)] || DEFAULT;
          const alloc = allocations?.[String(b.id)] || DEFAULT_ALLOC;
          const isExp = expanded === b.id;
          const active = cfg.min_trades > 0 || cfg.min_pf > 0 || cfg.min_wr > 0 || cfg.max_longs < 999 || cfg.max_shorts < 999;
          const eventOverride = cfg.event_mode !== undefined;
          return (
            <div key={b.id} className="border-b border-ag-border/50 last:border-b-0">
              <button onClick={() => setExpanded(isExp ? null : b.id)}
                className="w-full flex items-center justify-between px-4 py-2 text-xs hover:bg-ag-card-hover transition-colors">
                <span className="flex items-center gap-2">
                  <span>{b.label}</span>
                  {active && (
                    <span className="px-1.5 py-0.5 rounded bg-ag-accent/20 text-ag-accent text-[10px]">Filtered</span>
                  )}
                  {eventOverride && (
                    <span className="px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400 text-[10px]">
                      {cfg.event_mode ? "Event" : "Time"}
                    </span>
                  )}
                  {showAllocation && (
                    <span className="px-1.5 py-0.5 rounded bg-ag-dark text-ag-text-muted text-[10px]">
                      {alloc.n_longs}L / {alloc.n_shorts}S
                    </span>
                  )}
                </span>
                <span className="text-ag-text-muted">{isExp ? "\u25B2" : "\u25BC"}</span>
              </button>
              {isExp && (
                <div className="px-4 pb-3 space-y-2">
                  {showAllocation && (
                    <div className="grid grid-cols-2 gap-2">
                      <CfgInput label="N Longs" value={alloc.n_longs} min={0} max={50}
                        onChange={(v) => updateAlloc(b.id, { n_longs: v })} hint="Same as 'N Longs' in auto mode. How many Long assets L1 picks from this strategy per variant. This is the final asset output count for longs." />
                      <CfgInput label="N Shorts" value={alloc.n_shorts} min={0} max={50}
                        onChange={(v) => updateAlloc(b.id, { n_shorts: v })} hint="Same as 'N Shorts' in auto mode. How many Short assets L1 picks from this strategy per variant. This is the final asset output count for shorts." />
                    </div>
                  )}
                  <div className="grid grid-cols-5 gap-2">
                    <CfgInput label="Min Positions" value={cfg.min_trades} min={0}
                      onChange={(v) => update(b.id, { min_trades: v })} hint="Exclude assets with fewer positions." />
                    <CfgInput label="Min PF" value={cfg.min_pf} min={0} step={0.1}
                      onChange={(v) => update(b.id, { min_pf: v })} hint="Exclude assets below this PF." />
                    <CfgInput label="Min WR %" value={cfg.min_wr} min={0} max={100}
                      onChange={(v) => update(b.id, { min_wr: v })} hint="Exclude assets below this win rate." />
                    <CfgInput label="Max Longs" value={cfg.max_longs} min={0} max={999}
                      onChange={(v) => update(b.id, { max_longs: v })} hint="Cap long positions from this batch." />
                    <CfgInput label="Max Shorts" value={cfg.max_shorts} min={0} max={999}
                      onChange={(v) => update(b.id, { max_shorts: v })} hint="Cap short positions from this batch." />
                  </div>
                  {/* Per-batch L1 engine override */}
                  <div className="flex items-center gap-2 pt-1">
                    <span className="text-[10px] text-ag-text-muted">L1 Override:<HintTip text="Override the global L1 engine for this batch only. 'Inherit' uses the global setting." /></span>
                    <button onClick={() => update(b.id, { event_mode: undefined, n_trades: undefined })}
                      className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${cfg.event_mode === undefined ? "bg-ag-accent text-white" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"}`}>
                      Inherit
                    </button>
                    <button onClick={() => update(b.id, { event_mode: false })}
                      className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${cfg.event_mode === false ? "bg-ag-accent text-white" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"}`}>
                      Time
                    </button>
                    <button onClick={() => update(b.id, { event_mode: true, n_trades: cfg.n_trades ?? 20 })}
                      className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${cfg.event_mode === true ? "bg-ag-accent text-white" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"}`}>
                      Event
                    </button>
                    {cfg.event_mode === true && (
                      <div className="flex items-center gap-1 ml-1">
                        <span className="text-[10px] text-ag-text-muted">N trades:</span>
                        <input type="number" min={5} max={500} value={cfg.n_trades ?? 20}
                          onChange={(e) => update(b.id, { n_trades: Math.max(5, Number(e.target.value)) })}
                          className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-[10px] text-center" />
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CfgInput({ label, value, min, max, step, onChange, hint }: {
  label: string; value: number; min?: number; max?: number; step?: number;
  onChange: (v: number) => void; hint?: string;
}) {
  return (
    <div>
      <label className="block text-[10px] text-ag-text-muted mb-0.5">{label}{hint && <HintTip text={hint} />}</label>
      <input type="number" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Math.max(min ?? 0, Math.min(max ?? 9999, Number(e.target.value))))}
        className="w-full bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-xs" />
    </div>
  );
}
