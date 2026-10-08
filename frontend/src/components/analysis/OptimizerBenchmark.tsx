"use client";
import HintTip from "@/components/common/HintTip";
import UserNote from "./UserNote";

export interface BenchmarkConfig {
  source: string;
  split: string;
  threshold: number;
  elite_target: number;
  exclude_fragile: boolean;
  consistency_blend: number;
  fragility_blend: number;
  wr_harmony_blend: number;
  dsr_gate: boolean;
}

interface Props {
  show: boolean;
  onToggle: () => void;
  enabled: boolean;
  onEnabledChange: (v: boolean) => void;
  config: BenchmarkConfig;
  onConfigChange: (c: BenchmarkConfig) => void;
}

export default function OptimizerBenchmark({ show, onToggle, enabled, onEnabledChange, config, onConfigChange }: Props) {
  const update = (patch: Partial<BenchmarkConfig>) => onConfigChange({ ...config, ...patch });

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg">
      <button onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium hover:bg-ag-border/30 transition-colors rounded-lg">
        <span className="flex items-center gap-2">
          Benchmark Robustness<HintTip text="Splits your data in half and checks that profit holds in BOTH halves. Removes assets that only work in one half (lucky/fragile). If 'Benchmark' seed is also selected, the quality grades (Elite/Strong/Stable) help RANK the survivors." />
          {enabled && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-ag-accent/20 text-ag-accent">
              {config.source || "PF mode"} · {config.split} · PF&ge;{config.threshold}
            </span>
          )}
        </span>
        <span className="text-ag-text-muted text-xs">{show ? "\u25B2" : "\u25BC"}</span>
      </button>
      {show && (
        <div className="px-4 pb-4 border-t border-ag-border pt-3 space-y-3">
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={enabled} onChange={(e) => onEnabledChange(e.target.checked)}
              className="accent-ag-accent" />
            <span>Enable Benchmark Robustness</span>
            <UserNote noteKey="bench:enable" />
          </label>
          {enabled && (<>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Source Asset<HintTip text="Optional. Compare against a reference asset (e.g. BTCUSDT). Leave empty to grade by absolute Profit Factor instead." /><UserNote noteKey="bench:source" /></label>
                <input type="text" value={config.source} onChange={(e) => update({ source: e.target.value })}
                  placeholder="Leave empty for PF-only mode"
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
                <span className="text-[10px] text-ag-text-muted">Optional — grades by absolute PF if empty</span>
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Split Method<HintTip text="Time: splits by date (first half vs second half). Trades: splits by trade count (first 50% of trades vs last 50%)." /><UserNote noteKey="bench:split" /></label>
                <select value={config.split} onChange={(e) => update({ split: e.target.value })}
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
                  <option value="time">Time 50/50</option>
                  <option value="trades">Trade Count 50/50</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Validation Threshold<HintTip text="Minimum PF required in BOTH halves. Must pass in both to be considered robust. 1.0 = break-even." /><UserNote noteKey="bench:threshold" /></label>
                <input type="number" min={0.5} max={5} step={0.1} value={config.threshold}
                  onChange={(e) => update({ threshold: Math.max(0.5, Number(e.target.value)) })}
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
                <span className="text-[10px] text-ag-text-muted">Min PF in both halves</span>
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Elite Target<HintTip text="PF threshold for Elite status. With source: Elite = Source PF × this. Without source: Elite = PF ≥ this value." /><UserNote noteKey="bench:elite" /></label>
                <input type="number" min={0.5} max={3} step={0.1} value={config.elite_target}
                  onChange={(e) => update({ elite_target: Math.max(0.5, Number(e.target.value)) })}
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
                <span className="text-[10px] text-ag-text-muted">Multiplier for Elite status</span>
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-xs cursor-pointer pb-1.5">
                  <input type="checkbox" checked={config.exclude_fragile}
                    onChange={(e) => update({ exclude_fragile: e.target.checked })}
                    className="accent-ag-accent" />
                  <span>Exclude Fragile<HintTip text="Remove assets that pass in one half but fail in the other. These are unreliable." /><UserNote noteKey="bench:exclude_fragile" /></span>
                </label>
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-xs cursor-pointer pb-1.5">
                  <input type="checkbox" checked={config.dsr_gate || false}
                    onChange={(e) => update({ dsr_gate: e.target.checked })}
                    className="accent-ag-accent" />
                  <span>DSR Gate<HintTip text="Penalizes assets whose returns might be luck (DSR below 0.50 = 50% penalty to benchmark score). Does NOT remove them — just lowers their rank. For standalone DSR ranking without Benchmark, use the DSR seed button instead." /><UserNote noteKey="bench:dsr_gate" /></span>
                </label>
              </div>
            </div>
            {/* Enhanced Benchmark Multipliers (off by default) */}
            <details className="mt-2">
              <summary className="text-xs text-ag-text-muted cursor-pointer hover:text-ag-text">
                Advanced: Seed Score Enhancements<HintTip text="Fine-tune how Benchmark ranks assets. Only matters when 'Benchmark' seed is selected. If Benchmark is just a pass/fail filter, these do nothing." />
              </summary>
              <div className="grid grid-cols-3 gap-3 mt-2">
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">
                    Consistency<HintTip text="Penalizes when the two halves have very different PFs. Balanced (2.0, 2.0) beats lopsided (3.0, 1.0). 0 = off." /><UserNote noteKey="bench:consistency" />
                  </label>
                  <input type="range" min={0} max={0.5} step={0.05} value={config.consistency_blend}
                    onChange={(e) => update({ consistency_blend: Number(e.target.value) })}
                    className="w-full accent-ag-accent" />
                  <span className="text-[10px] text-ag-text-muted">{config.consistency_blend > 0 ? config.consistency_blend.toFixed(2) : "Off"}</span>
                </div>
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">
                    Fragility<HintTip text="Removes the top 5% best trades and checks if PF still holds. Catches assets that depend on a few lucky trades. 0 = off." /><UserNote noteKey="bench:fragility" />
                  </label>
                  <input type="range" min={0} max={0.5} step={0.05} value={config.fragility_blend}
                    onChange={(e) => update({ fragility_blend: Number(e.target.value) })}
                    className="w-full accent-ag-accent" />
                  <span className="text-[10px] text-ag-text-muted">{config.fragility_blend > 0 ? config.fragility_blend.toFixed(2) : "Off"}</span>
                </div>
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">
                    WR Harmony<HintTip text="Penalizes when win rate flips between halves (e.g. 70% → 30%). Catches behavior changes hidden by PF. 0 = off." /><UserNote noteKey="bench:wr_harmony" />
                  </label>
                  <input type="range" min={0} max={0.5} step={0.05} value={config.wr_harmony_blend}
                    onChange={(e) => update({ wr_harmony_blend: Number(e.target.value) })}
                    className="w-full accent-ag-accent" />
                  <span className="text-[10px] text-ag-text-muted">{config.wr_harmony_blend > 0 ? config.wr_harmony_blend.toFixed(2) : "Off"}</span>
                </div>
              </div>
            </details>
          </>)}
        </div>
      )}
    </div>
  );
}
