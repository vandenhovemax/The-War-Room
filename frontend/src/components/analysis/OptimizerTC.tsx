"use client";
import HintTip from "@/components/common/HintTip";

export interface TCConstraints {
  low_threshold: number;
  max_low: number;
  high_threshold: number;
  min_high: number;
}

interface Props {
  show: boolean;
  onToggle: () => void;
  splitMode: boolean;
  enabled: boolean;
  onEnabledChange: (v: boolean) => void;
  tc: TCConstraints;
  onTCChange: (tc: TCConstraints) => void;
  tcLongs: TCConstraints;
  onTCLongsChange: (tc: TCConstraints) => void;
  tcShorts: TCConstraints;
  onTCShortsChange: (tc: TCConstraints) => void;
}

function TCRow({ label, tc, onChange }: {
  label?: string; tc: TCConstraints;
  onChange: (tc: TCConstraints) => void;
}) {
  const field = (key: keyof TCConstraints, lbl: string, hint: string) => (
    <div>
      <label className="block text-[10px] text-ag-text-muted mb-0.5">{lbl}</label>
      <input type="number" min={0} value={tc[key]}
        onChange={(e) => onChange({ ...tc, [key]: Math.max(0, Number(e.target.value)) })}
        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
      <span className="text-[9px] text-ag-text-muted">{hint}</span>
    </div>
  );
  return (
    <div>
      {label && <p className="text-xs text-ag-text-muted font-medium mb-1.5">{label}</p>}
      <div className="grid grid-cols-4 gap-3">
        {field("low_threshold", "Low Threshold", "Positions \u2264 N = \u201clow\u201d")}
        {field("max_low", "Max Low", "Max low-position assets")}
        {field("high_threshold", "High Threshold", "Positions \u2265 N = \u201chigh\u201d")}
        {field("min_high", "Min High", "Min high-position assets")}
      </div>
    </div>
  );
}

export default function OptimizerTC({
  show, onToggle, splitMode, enabled, onEnabledChange,
  tc, onTCChange, tcLongs, onTCLongsChange, tcShorts, onTCShortsChange,
}: Props) {
  const badge = !splitMode
    ? `Low\u2264${tc.low_threshold} max ${tc.max_low} \u00b7 High\u2265${tc.high_threshold} min ${tc.min_high}`
    : "L/S constraints active";

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg">
      <button onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium hover:bg-ag-border/30 transition-colors rounded-lg">
        <span className="flex items-center gap-2">
          Position-Count Constraints<HintTip text="Controls the mix of high-position and low-position assets in the portfolio. Prevents the optimizer from picking only assets with very few positions (low statistical confidence)." />
          <input type="checkbox" checked={enabled}
            onChange={(e) => { e.stopPropagation(); onEnabledChange(e.target.checked); }}
            className="accent-ag-accent" onClick={(e) => e.stopPropagation()} />
          {enabled && !show && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-ag-accent/20 text-ag-accent">{badge}</span>
          )}
        </span>
        <span className="text-ag-text-muted text-xs">{show ? "\u25b2" : "\u25bc"}</span>
      </button>
      {show && enabled && (
        <div className="px-4 pb-4 border-t border-ag-border pt-3 space-y-3">
          {splitMode ? (
            <>
              <TCRow label="Longs" tc={tcLongs} onChange={onTCLongsChange} />
              <TCRow label="Shorts" tc={tcShorts} onChange={onTCShortsChange} />
            </>
          ) : (
            <TCRow tc={tc} onChange={onTCChange} />
          )}
        </div>
      )}
    </div>
  );
}
