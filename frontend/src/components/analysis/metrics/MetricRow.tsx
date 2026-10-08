/**
 * Shared MetricRow component with HintTip tooltip support.
 * Renders a titled row of metric cells in a responsive grid.
 */
import HintTip from "@/components/common/HintTip";

export interface MetricItem {
  label: string;
  value: string;
  color?: string;
  /** Sub-text below the value (e.g., date range, asset detail) */
  sub?: string;
  /** Tooltip explanation shown via ? icon */
  tooltip?: string;
}

interface MetricRowProps {
  title: string;
  metrics: MetricItem[];
  /** Grid columns override (default: responsive 3→4→6) */
  cols?: string;
}

export default function MetricRow({ title, metrics, cols }: MetricRowProps) {
  const gridClass = cols || "grid-cols-3 sm:grid-cols-4 md:grid-cols-6";

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        {title}
      </h4>
      <div className={`grid ${gridClass} gap-3`}>
        {metrics.map((m) => (
          <div key={m.label} className="bg-ag-card border border-ag-border rounded-lg p-3">
            <p className="text-[10px] text-ag-text-muted uppercase tracking-wider flex items-center gap-0.5 mb-0.5">
              {m.label}
              {m.tooltip && <HintTip text={m.tooltip} />}
            </p>
            <p className={`text-sm font-semibold ${m.color || ""}`}>
              {m.value}
            </p>
            {m.sub && (
              <p className="text-[9px] text-ag-text-muted mt-0.5 leading-tight">
                {m.sub}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
