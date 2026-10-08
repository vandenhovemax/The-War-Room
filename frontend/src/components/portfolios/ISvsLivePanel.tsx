"use client";

import { useState } from "react";

interface Props {
  isMetrics: Record<string, number>;
  liveMetrics: Record<string, number>;
  oosMetrics?: Record<string, number>;
  paperMetrics?: Record<string, number>;
  /** When true, IS column is hidden by default with a "Show IS" toggle */
  showIsToggle?: boolean;
}

const KPI_KEYS = [
  { key: "net_profit", label: "Net Profit", fmt: "$", flip: false },
  { key: "gross_profit", label: "Gross Profit", fmt: "$", flip: false },
  { key: "gross_loss", label: "Gross Loss", fmt: "$", flip: true },
  { key: "profit_factor", label: "Profit Factor", fmt: "", flip: false },
  { key: "win_rate", label: "Win Rate", fmt: "%", flip: false },
  { key: "max_drawdown", label: "Max Drawdown", fmt: "$", flip: true },
  { key: "avg_trade", label: "Avg Trade", fmt: "$", flip: false },
  { key: "sortino", label: "Sortino", fmt: "", flip: false },
  { key: "total_trades", label: "Total Positions", fmt: "int", flip: false },
  { key: "stability", label: "Stability (R\u00b2)", fmt: "", flip: false },
  { key: "calmar_ratio", label: "Calmar", fmt: "", flip: false },
  { key: "recovery_factor", label: "Recovery", fmt: "", flip: false },
];

function fmtVal(v: number | undefined, fmt: string): string {
  if (v === undefined || v === null) return "\u2014";
  if (fmt === "$") return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  if (fmt === "%") return `${v.toFixed(1)}%`;
  if (fmt === "int") return String(Math.round(v));
  return v.toFixed(2);
}

/** Absolute color: green if the value is "good", red if "bad" */
function absColor(v: number | undefined, flip: boolean): string {
  if (v === undefined || v === null) return "";
  // For "flip" metrics (drawdown), less negative = better (green)
  if (flip) return v >= 0 ? "text-ag-green" : "text-ag-red";
  return v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";
}

/** WFE color: green ≥ 0.8, yellow ≥ 0.5, red < 0.5 */
function wfeColor(wfe: number): string {
  if (wfe >= 0.8) return "text-ag-green";
  if (wfe >= 0.5) return "text-yellow-400";
  return "text-ag-red";
}

export default function ISvsLivePanel({ isMetrics, liveMetrics, oosMetrics, paperMetrics, showIsToggle }: Props) {
  const [showIS, setShowIS] = useState(!showIsToggle); // hidden by default when toggle enabled
  const hasLive = Object.keys(liveMetrics).length > 0;
  const hasOOS = oosMetrics && Object.keys(oosMetrics).length > 0;
  const hasPaper = paperMetrics && Object.keys(paperMetrics).length > 0;
  const isVisible = showIS && Object.keys(isMetrics).length > 0;

  // Dynamic column count: KPI + (IS?) + (OOS?) + (Paper?) + Live
  const colCount = 1 + (isVisible ? 1 : 0) + (hasOOS ? 1 : 0) + (hasPaper ? 1 : 0) + 1;
  const cols = `grid-cols-${colCount}`;

  // Use backend's time-normalized WFE (profit/day OOS ÷ profit/day IS)
  const wfe = oosMetrics?.wfe;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg overflow-hidden">
      {/* Show IS toggle */}
      {showIsToggle && (
        <div className="px-3 py-1.5 border-b border-ag-border/40 flex justify-end">
          <label className="flex items-center gap-1.5 cursor-pointer text-[10px] text-ag-text-muted">
            <input type="checkbox" checked={showIS} onChange={(e) => setShowIS(e.target.checked)} className="accent-ag-accent" />
            Show In-Sample
          </label>
        </div>
      )}
      <div className={`grid ${cols} text-xs border-b border-ag-border bg-ag-darker`}>
        <div className="px-3 py-2 font-medium text-ag-text-muted">KPI</div>
        {isVisible && <div className="px-3 py-2 font-medium text-center text-ag-text-muted">In-Sample (Backtest)</div>}
        {hasOOS && (
          <div className="px-3 py-2 font-medium text-center text-ag-text-muted">Out-of-Sample</div>
        )}
        {hasPaper && (
          <div className="px-3 py-2 font-medium text-center text-ag-text-muted">Paper</div>
        )}
        <div className="px-3 py-2 font-medium text-center text-ag-text-muted">
          Live {!hasLive && <span className="text-[10px] opacity-60">(not yet refreshed)</span>}
        </div>
      </div>

      {/* WFE row — only shown when OOS exists */}
      {hasOOS && (
        <div className={`grid ${cols} text-xs border-b border-ag-border/40 bg-ag-darker/20`}>
          <div className="px-3 py-1.5 font-medium text-ag-text-muted">WFE</div>
          {isVisible && <div className="px-3 py-1.5 text-center font-mono">{"\u2014"}</div>}
          <div className={`px-3 py-1.5 text-center font-mono font-medium ${wfe !== undefined ? wfeColor(wfe) : ""}`}>
            {wfe !== undefined ? wfe.toFixed(2) : "\u2014"}
          </div>
          {hasPaper && <div className="px-3 py-1.5 text-center font-mono">{"\u2014"}</div>}
          <div className="px-3 py-1.5 text-center font-mono">{"\u2014"}</div>
        </div>
      )}

      {KPI_KEYS.map(({ key, label, fmt, flip }) => {
        const isV = isMetrics[key];
        const oosV = oosMetrics?.[key];
        const paperV = paperMetrics?.[key];
        const liveV = liveMetrics[key];
        return (
          <div key={key} className={`grid ${cols} text-xs border-b border-ag-border/40 hover:bg-ag-darker/30`}>
            <div className="px-3 py-1.5 text-ag-text-muted">{label}</div>
            {isVisible && <div className="px-3 py-1.5 text-center font-mono">{fmtVal(isV, fmt)}</div>}
            {hasOOS && (
              <div className={`px-3 py-1.5 text-center font-mono ${absColor(oosV, flip)}`}>
                {fmtVal(oosV, fmt)}
              </div>
            )}
            {hasPaper && (
              <div className={`px-3 py-1.5 text-center font-mono ${absColor(paperV, flip)}`}>
                {fmtVal(paperV, fmt)}
              </div>
            )}
            <div className={`px-3 py-1.5 text-center font-mono ${absColor(liveV, flip)}`}>
              {hasLive ? fmtVal(liveV, fmt) : "\u2014"}
            </div>
          </div>
        );
      })}
    </div>
  );
}
