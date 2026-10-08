"use client";
import type { ParameterDefinition, SweepParam } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  sweepable: ParameterDefinition[];
  sweepParams: SweepParam[];
  onSweepParamsChange: (params: SweepParam[]) => void;
  sweepMode: "grid" | "one_at_a_time";
  onSweepModeChange: (mode: "grid" | "one_at_a_time") => void;
  comboCount: number;
  symbolCount: number;
}

export default function SweepParamSetup({
  sweepable, sweepParams, onSweepParamsChange,
  sweepMode, onSweepModeChange, comboCount, symbolCount,
}: Props) {

  const toggleParam = (def: ParameterDefinition, checked: boolean) => {
    if (checked) {
      if (def.type === "bool") {
        // Boolean params always sweep Off(0) → On(1)
        onSweepParamsChange([...sweepParams, { name: def.name, min: 0, max: 1, step: 1 }]);
      } else {
        onSweepParamsChange([...sweepParams, {
          name: def.name,
          min: def.min ?? (typeof def.default === "number" ? def.default : 0),
          max: def.max ?? (typeof def.default === "number" ? def.default * 2 : 10),
          step: def.step ?? 1,
        }]);
      }
    } else {
      onSweepParamsChange(sweepParams.filter((p) => p.name !== def.name));
    }
  };

  const updateParam = (name: string, field: "min" | "max" | "step", value: string) => {
    onSweepParamsChange(sweepParams.map((p) =>
      p.name === name ? { ...p, [field]: parseFloat(value) || 0 } : p,
    ));
  };

  const totalBacktests = comboCount * Math.max(symbolCount, 1);
  const warning = comboCount > 200;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">
          Parameter Sweep Setup
          <HintTip text="Select parameters to sweep. Define min/max/step ranges. Grid mode tests all combinations; One-at-a-time varies each parameter individually while keeping others at default." />
        </h3>
        <div className="flex gap-1 bg-ag-dark rounded-lg p-1">
          {(["grid", "one_at_a_time"] as const).map((m) => (
            <button key={m} onClick={() => onSweepModeChange(m)}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                sweepMode === m ? "bg-ag-accent text-white" : "text-ag-text-muted hover:text-ag-text"
              }`}>
              {m === "grid" ? "Grid" : "One at a time"}
            </button>
          ))}
        </div>
      </div>

      {sweepable.length === 0 ? (
        <p className="text-xs text-ag-text-muted">No sweepable parameters (need int/float params).</p>
      ) : (
        <div className="space-y-2">
          {sweepable.map((def) => {
            const active = sweepParams.find((p) => p.name === def.name);
            return (
              <div key={def.name} className={`flex items-center gap-3 rounded-lg px-3 py-2 ${active ? "bg-ag-accent/10 border border-ag-accent/30" : "bg-ag-dark"}`}>
                <input type="checkbox" checked={!!active}
                  onChange={(e) => toggleParam(def, e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-sm font-medium min-w-[140px]">{def.name}</span>
                {active ? (
                  def.type === "bool" ? (
                    <span className="text-xs text-ag-accent font-medium">Off → On (2 values)</span>
                  ) : (
                  <div className="flex items-center gap-2 text-xs">
                    <label className="text-ag-text-muted">Min</label>
                    <input type="number" value={active.min} step={def.step ?? 1}
                      onChange={(e) => updateParam(def.name, "min", e.target.value)}
                      className="w-20 bg-ag-dark border border-ag-border rounded px-2 py-1 text-sm" />
                    <label className="text-ag-text-muted">Max</label>
                    <input type="number" value={active.max} step={def.step ?? 1}
                      onChange={(e) => updateParam(def.name, "max", e.target.value)}
                      className="w-20 bg-ag-dark border border-ag-border rounded px-2 py-1 text-sm" />
                    <label className="text-ag-text-muted">Step</label>
                    <input type="number" value={active.step} min={0.001}
                      onChange={(e) => updateParam(def.name, "step", e.target.value)}
                      className="w-20 bg-ag-dark border border-ag-border rounded px-2 py-1 text-sm" />
                    <span className="text-ag-text-muted ml-1">
                      ({active.step > 0 ? Math.floor((active.max - active.min) / active.step) + 1 : 0} values)
                    </span>
                  </div>
                  )
                ) : (
                  <span className="text-xs text-ag-text-muted">
                    {def.type === "bool"
                      ? `Default: ${def.default ? "On" : "Off"}`
                      : `Default: ${String(def.default)}${def.min != null ? ` (range: ${def.min}–${def.max})` : ""}`
                    }
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Combo count display */}
      {sweepParams.length > 0 && (
        <div className={`text-xs px-3 py-2 rounded-lg ${warning ? "bg-yellow-500/10 text-yellow-400" : "bg-ag-dark text-ag-text-muted"}`}>
          {comboCount} combination{comboCount !== 1 ? "s" : ""}
          {symbolCount > 0 ? ` × ${symbolCount} symbols = ${totalBacktests.toLocaleString()} backtests` : ""}
          {warning && " — this will take a very long time."}
        </div>
      )}
    </div>
  );
}
