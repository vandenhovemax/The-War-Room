/**
 * KPI card showing 1-3 period values side by side.
 * Used for IS/OOS/Live comparison in Portfolio analysis.
 *
 * When `detail` is provided (Max Open / Peak Margin), the card becomes
 * clickable and expands inline to show the peak sequence of trades.
 */
import { useState } from "react";
import HintTip from "@/components/common/HintTip";

export interface PeriodValue {
  label: string;
  value: string;
  color?: string;
}

interface TradeDetail {
  asset: string;
  side?: string;
  entry_time: string;
  exit_time?: string;
  exit_reason?: string;
  had_tp1?: boolean;
  margin?: number;
}

interface DetailData {
  count?: number;
  peak_time?: string;
  peak_margin?: number;
  peak_margin_net?: number;
  trades?: TradeDetail[];
}

interface Props {
  metricLabel: string;
  periods: PeriodValue[];
  tooltip?: string;
  /** If present, card becomes clickable and shows trade detail on expand */
  detail?: Record<string, unknown>;
}

function formatTime(ts: string): string {
  if (!ts || ts === "None" || ts === "NaT") return "—";
  try {
    const d = new Date(ts);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" })
      + " " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch { return ts.slice(0, 16); }
}

export default function ComparisonMetricCard({ metricLabel, periods, tooltip, detail }: Props) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = detail && (detail as DetailData).trades && ((detail as DetailData).trades?.length ?? 0) > 0;
  const d = detail as DetailData | undefined;

  const cardClass = `bg-ag-card border border-ag-border rounded-lg p-3${
    hasDetail ? " cursor-pointer hover:border-ag-accent/40 transition-colors" : ""
  }`;

  const header = (
    <p className="text-[10px] text-ag-text-muted uppercase tracking-wider flex items-center gap-0.5 mb-0.5">
      {metricLabel}
      {hasDetail && (
        <span className="text-ag-accent text-[8px] ml-0.5">{expanded ? "▼" : "▶"}</span>
      )}
      {tooltip && <HintTip text={tooltip} />}
    </p>
  );

  // Extract optional context fields from detail object
  const dAny = d as Record<string, unknown> | undefined;
  const stratName = dAny?.strategy ? String(dAny.strategy) : "";
  const cfgName = dAny?.config ? String(dAny.config) : "";
  const lev = dAny?.leverage ? Number(dAny.leverage) : 1;

  const detailPanel = expanded && hasDetail && d ? (
    <div className="mt-2 pt-2 border-t border-ag-border/50">
      {/* Strategy / Config context */}
      {stratName && (
        <p className="text-[9px] text-ag-text-muted mb-0.5">
          {stratName}
          {cfgName && (
            <span className="text-ag-accent ml-1">— {cfgName}</span>
          )}
        </p>
      )}
      {d.peak_time && (
        <p className="text-[9px] text-ag-text-muted mb-1">
          Peak at: <span className="text-ag-text">{formatTime(d.peak_time)}</span>
          {lev > 1 && (
            <span className="ml-2 text-yellow-400">{lev}x leverage</span>
          )}
          {d.peak_margin != null && (
            <span className="ml-2">
              Margin: <span className="text-ag-accent">${d.peak_margin.toLocaleString()}</span>
              {d.peak_margin_net != null && d.peak_margin_net !== d.peak_margin && (
                <span className="text-ag-green ml-1">(net ${d.peak_margin_net.toLocaleString()})</span>
              )}
            </span>
          )}
        </p>
      )}
      <div className="space-y-0.5 max-h-[200px] overflow-y-auto">
        {d.trades?.map((t, i) => (
          <div key={i} className="flex items-center gap-2 text-[9px] py-0.5 px-1.5 rounded bg-ag-bg/50">
            <span className="font-medium text-ag-text min-w-[80px] truncate">{t.asset}</span>
            {t.side && (
              <span className={`px-1 rounded text-[8px] ${
                t.side === "Long" ? "bg-ag-green/15 text-ag-green" : "bg-ag-red/15 text-ag-red"
              }`}>{t.side}</span>
            )}
            <span className="text-ag-text-muted">{formatTime(t.entry_time)}</span>
            {t.exit_time && <span className="text-ag-text-muted">→ {formatTime(t.exit_time)}</span>}
            {t.exit_reason && (
              <span className={`px-1 rounded text-[8px] ${
                t.exit_reason.toUpperCase().includes("TP1") ? "bg-ag-green/15 text-ag-green"
                : t.exit_reason.toUpperCase().includes("TP2") ? "bg-ag-accent/15 text-ag-accent"
                : t.exit_reason.toUpperCase().includes("TSL") ? "bg-yellow-500/15 text-yellow-400"
                : t.exit_reason.toUpperCase().includes("SL") ? "bg-ag-red/15 text-ag-red"
                : "bg-ag-text-muted/10 text-ag-text-muted"
              }`}>{t.exit_reason}</span>
            )}
            {t.had_tp1 && (
              <span className="px-1 rounded text-[8px] bg-ag-green/15 text-ag-green">TP1 ✓</span>
            )}
            {t.margin != null && (
              <span className="text-ag-text-muted ml-auto">${t.margin.toLocaleString()}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  ) : null;

  // Single period — render like a normal metric card
  if (periods.length === 1) {
    const p = periods[0];
    return (
      <div className={cardClass} onClick={hasDetail ? () => setExpanded(!expanded) : undefined}>
        {header}
        <p className={`text-sm font-semibold ${p.color || ""}`}>{p.value}</p>
        {detailPanel}
      </div>
    );
  }

  // Multi-period — show side by side columns
  return (
    <div className={cardClass} onClick={hasDetail ? () => setExpanded(!expanded) : undefined}>
      {header}
      <div className={`grid grid-cols-${periods.length} gap-1`}>
        {periods.map((p) => (
          <div key={p.label}>
            <p className="text-[8px] text-ag-text-muted/60 uppercase leading-tight">{p.label}</p>
            <p className={`text-sm font-semibold ${p.color || ""}`}>{p.value}</p>
          </div>
        ))}
      </div>
      {detailPanel}
    </div>
  );
}
