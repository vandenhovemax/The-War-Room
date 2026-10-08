/**
 * Shared formatting functions for metric display.
 * Used by FullMetricsRows, LSBreakdown, and OOS panels.
 */

export function fmtDollar(v: unknown): string {
  if (v == null) return "\u2014";
  const n = Number(v);
  if (isNaN(n)) return "\u2014";
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

export function fmtNum(v: unknown, decimals: number): string {
  if (v == null) return "\u2014";
  const n = Number(v);
  if (isNaN(n)) return "\u2014";
  return n.toFixed(decimals);
}

export function fmtPct(v: unknown): string {
  if (v == null) return "\u2014";
  const n = Number(v);
  if (isNaN(n)) return "\u2014";
  return `${n.toFixed(1)}%`;
}

export function fmtInt(v: unknown): string {
  if (v == null) return "\u2014";
  const n = Number(v);
  if (isNaN(n)) return "\u2014";
  return String(Math.round(n));
}

export function fmtDuration(v: unknown): string {
  if (v == null) return "\u2014";
  const s = String(v);
  const daysMatch = s.match(/(\d+)\s*days?/);
  if (daysMatch) return `${daysMatch[1]}d`;
  if (s === "0 days 00:00:00" || s === "0:00:00") return "0d";
  return s.replace(/ days?/, "d").replace(/\s+\d+:\d+:\d+/, "");
}

export function numColor(v: unknown): string {
  if (v == null) return "";
  return Number(v) >= 0 ? "text-ag-green" : "text-ag-red";
}

/** SQN quality label per Van Tharp */
export function sqnLabel(sqn: number): string {
  if (sqn >= 7) return "Holy Grail";
  if (sqn >= 5) return "Superb";
  if (sqn >= 3) return "Excellent";
  if (sqn >= 2) return "Good";
  if (sqn >= 1.5) return "Average";
  return "Poor";
}

/** T-Statistic → confidence level text */
export function tStatConfidence(t: number): string {
  const abs = Math.abs(t);
  if (abs >= 3.291) return "99.9%";
  if (abs >= 2.576) return "99%";
  if (abs >= 1.96) return "95%";
  if (abs >= 1.645) return "90%";
  return "< 90%";
}

/** Format a timestamp string to short date */
export function fmtShortDate(v: unknown): string {
  if (v == null) return "";
  const s = String(v);
  try {
    const d = new Date(s);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  } catch {
    return "";
  }
}
