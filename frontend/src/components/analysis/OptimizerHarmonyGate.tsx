"use client";
import HintTip from "@/components/common/HintTip";

export interface HarmonyConfig {
  enabled: boolean;
  strength_l15: number;
  strength_l25: number;
  per_group: Record<string, boolean>;  // group index → L1.5 toggle (manual mode)
}

export const DEFAULT_HARMONY_CONFIG: HarmonyConfig = {
  enabled: false,
  strength_l15: 50,
  strength_l25: 50,
  per_group: {},
};

interface Props {
  show: boolean;
  onToggle: () => void;
  config: HarmonyConfig;
  onConfigChange: (c: HarmonyConfig) => void;
  /** Group labels for per-group L1.5 toggles in manual mode */
  groupLabels?: string[];
}

function StrengthSlider({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  onChange: (v: number) => void;
}) {
  const color =
    value === 0
      ? "text-ag-text-muted"
      : value <= 25
        ? "text-green-400"
        : value <= 50
          ? "text-yellow-400"
          : value <= 75
            ? "text-orange-400"
            : "text-red-400";

  const levelLabel =
    value === 0
      ? "Off"
      : value <= 25
        ? "Loose"
        : value <= 50
          ? "Moderate"
          : value <= 75
            ? "Strict"
            : "Maximum";

  return (
    <div>
      <label className="block text-[10px] text-ag-text-muted mb-1">
        {label}
        <HintTip text={hint} />
      </label>
      <div className="flex items-center gap-2">
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="flex-1 accent-ag-accent h-1.5"
        />
        <span className={`text-xs font-mono w-20 text-right ${color}`}>
          {value} <span className="text-[9px] opacity-70">({levelLabel})</span>
        </span>
      </div>
    </div>
  );
}

export default function OptimizerHarmonyGate({
  show,
  onToggle,
  config,
  onConfigChange,
  groupLabels,
}: Props) {
  const badge = config.enabled
    ? `L1.5: ${config.strength_l15} · L2.5: ${config.strength_l25}`
    : "";

  const update = (patch: Partial<HarmonyConfig>) =>
    onConfigChange({ ...config, ...patch });

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium hover:bg-ag-border/30 transition-colors rounded-lg"
      >
        <span className="flex items-center gap-2">
          Batch Harmony Gate
          <HintTip text="Filters batch and group combinations by inter-batch affinity. L1.5 checks whether batches within a group complement each other (correlation, drawdown overlap, stress behavior). L2.5 checks whether group outputs complement each other before L3 assembly. Higher strength = stricter filtering." />
          <input
            type="checkbox"
            checked={config.enabled}
            onChange={(e) => {
              e.stopPropagation();
              update({ enabled: e.target.checked });
            }}
            className="accent-ag-accent"
            onClick={(e) => e.stopPropagation()}
          />
          {config.enabled && !show && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-ag-accent/20 text-ag-accent">
              {badge}
            </span>
          )}
        </span>
        <span className="text-ag-text-muted text-xs">
          {show ? "\u25b2" : "\u25bc"}
        </span>
      </button>

      {show && config.enabled && (
        <div className="px-4 pb-4 border-t border-ag-border pt-3 space-y-4">
          {/* Core strength sliders */}
          <StrengthSlider
            label="L1.5 Strength — Within Groups"
            hint="Controls how aggressively to filter batches within each L2 group. Higher = only keep batches that strongly complement each other. 0 = disabled."
            value={config.strength_l15}
            onChange={(v) => update({ strength_l15: v })}
          />
          <StrengthSlider
            label="L2.5 Strength — Across Groups"
            hint="Controls how aggressively to penalize group combinations at L3. Higher = L3 strongly penalizes group pairs with poor harmony (high drawdown overlap, correlated stress). 0 = disabled."
            value={config.strength_l25}
            onChange={(v) => update({ strength_l25: v })}
          />

          {/* Per-group L1.5 toggles (manual mode) */}
          {groupLabels && groupLabels.length > 0 && (
            <details className="mt-2">
              <summary className="text-[10px] text-ag-text-muted cursor-pointer hover:text-ag-text select-none">
                Per-Group L1.5 Overrides
                <HintTip text="In manual mode, you can enable or disable L1.5 harmony filtering per group. Useful when some groups are curated (don't filter) and others are exploratory (filter to prune redundant batches)." />
              </summary>
              <div className="mt-2 space-y-1.5">
                {groupLabels.map((label, idx) => (
                  <label
                    key={idx}
                    className="flex items-center gap-2 text-xs text-ag-text-muted"
                  >
                    <input
                      type="checkbox"
                      checked={config.per_group[String(idx)] ?? true}
                      onChange={(e) => {
                        const pg = { ...config.per_group };
                        pg[String(idx)] = e.target.checked;
                        update({ per_group: pg });
                      }}
                      className="accent-ag-accent"
                    />
                    {label}
                  </label>
                ))}
              </div>
            </details>
          )}

          {/* Metrics info */}
          <details className="mt-1">
            <summary className="text-[10px] text-ag-text-muted cursor-pointer hover:text-ag-text select-none">
              What does it measure?
            </summary>
            <div className="mt-2 text-[10px] text-ag-text-muted space-y-1 leading-relaxed">
              <p>
                <strong className="text-ag-text">Return Correlation</strong>{" "}
                (20%) — Do the batches move together? Lower is better.
              </p>
              <p>
                <strong className="text-ag-text">Correlation Stability</strong>{" "}
                (15%) — Is the relationship reliable across time windows?
              </p>
              <p>
                <strong className="text-ag-text">Drawdown Overlap</strong>{" "}
                (25%) — Do they bleed at the same time? The #1 portfolio
                killer.
              </p>
              <p>
                <strong className="text-ag-text">Stress Correlation</strong>{" "}
                (25%) — Does diversification vanish during the worst days?
              </p>
              <p>
                <strong className="text-ag-text">Complementarity Ratio</strong>{" "}
                (15%) — Is the combined Sharpe better than the individual
                parts?
              </p>
            </div>
          </details>
        </div>
      )}
    </div>
  );
}
