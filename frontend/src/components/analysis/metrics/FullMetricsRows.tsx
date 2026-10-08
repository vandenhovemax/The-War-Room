/**
 * All 8 metric row sections for comprehensive portfolio analysis.
 * Reused by PortfolioFullMetrics, OOS panels, and compare panels.
 */
import MetricRow from "./MetricRow";
import type { MetricItem } from "./MetricRow";
import { METRIC_TOOLTIPS } from "./metricDefinitions";
import {
  fmtDollar, fmtNum, fmtPct, fmtInt, fmtDuration,
  numColor, sqnLabel, tStatConfidence, fmtShortDate,
} from "./MetricFormatters";

type M = Record<string, number | string | null>;

interface Props {
  metrics: M;
  /** Optional title prefix like "OOS" or "IS" */
  prefix?: string;
}

function t(label: string) { return METRIC_TOOLTIPS[label] || ""; }

function sub(v: unknown): string | undefined {
  if (v == null) return undefined;
  const s = String(v);
  return s === "None" || s === "NaT" ? undefined : s;
}

export default function FullMetricsRows({ metrics: m, prefix }: Props) {
  const p = prefix ? `${prefix} ` : "";

  return (
    <div className="space-y-4">
      {/* Row 1: Core Performance */}
      <MetricRow title={`${p}Core Performance`} metrics={coreRow(m)} />
      {/* Row 2: System Quality */}
      <MetricRow title={`${p}System Quality`} metrics={qualityRow(m)} />
      {/* Row 3: Risk-Adjusted */}
      <MetricRow title={`${p}Risk-Adjusted`} metrics={riskRow(m)} />
      {/* Row 4: Trade Distribution */}
      <MetricRow title={`${p}Trade Distribution`} metrics={tradeDistRow(m)}
        cols="grid-cols-3 sm:grid-cols-5 md:grid-cols-9" />
      {/* Row 5: Exit Reason Breakdown */}
      {hasExitReasons(m) && (
        <MetricRow title={`${p}Exit Reason Breakdown`} metrics={exitReasonRow(m)} />
      )}
      {/* Row 6: Trade Excursion */}
      <MetricRow title={`${p}MAE/MFE & Operational`} metrics={excursionRow(m)} />
      {/* Row 7: Statistical Significance */}
      <MetricRow title={`${p}Statistical Significance`} metrics={statsRow(m)} />
    </div>
  );
}

function coreRow(m: M): MetricItem[] {
  const minM = m["Min Positions/Month"];
  const maxM = m["Max Positions/Month"];
  const avgSub = (minM != null && maxM != null)
    ? `Min ${fmtInt(minM)} / Max ${fmtInt(maxM)} /mo` : undefined;
  const ddStart = fmtShortDate(m["Max DD Start"]);
  const ddEnd = fmtShortDate(m["Max DD End"]);
  const ddSub = (ddStart && ddEnd) ? `${ddStart} — ${ddEnd}` : undefined;

  return [
    { label: "Net Profit", value: fmtDollar(m["Total Net Profit"]), color: numColor(m["Total Net Profit"]), tooltip: t("Net Profit") },
    { label: "Avg Trade", value: fmtDollar(m["Avg Trade Profit"]), color: numColor(m["Avg Trade Profit"]), tooltip: t("Avg Trade") },
    { label: "Positions", value: fmtInt(m["Total Positions"]), tooltip: t("Positions") },
    { label: "Avg/Month", value: fmtNum(m["Avg Positions/Month"], 1), sub: avgSub, tooltip: t("Avg/Month") },
    { label: "Win Rate", value: fmtPct(m["Win Rate"]), tooltip: t("Win Rate") },
    { label: "Profit Factor", value: fmtNum(m["Profit Factor"], 2), tooltip: t("Profit Factor") },
    { label: "Max DD", value: fmtDollar(m["Max Drawdown ($)"]), color: "text-ag-red", tooltip: t("Max DD") },
    { label: "Max DD Dur", value: fmtDuration(m["Max DD Duration"]), sub: ddSub, tooltip: t("Max DD Dur") },
  ];
}

function qualityRow(m: M): MetricItem[] {
  const sqn = Number(m["SQN"]) || 0;
  return [
    { label: "SQN", value: fmtNum(m["SQN"], 2), sub: sqnLabel(sqn), tooltip: t("SQN") },
    { label: "Recovery", value: fmtNum(m["Recovery Factor"], 2), tooltip: t("Recovery") },
    { label: "Ulcer Index", value: fmtNum(m["Ulcer Index"], 4), tooltip: t("Ulcer Index") },
    { label: "Avg DD", value: fmtDollar(m["Avg Drawdown ($)"]), tooltip: t("Avg DD") },
    { label: "Avg DD Dur", value: fmtDuration(m["Avg DD Duration"]), tooltip: t("Avg DD Dur") },
  ];
}

function riskRow(m: M): MetricItem[] {
  return [
    { label: "Sortino", value: fmtNum(m["Sortino Ratio"], 2), tooltip: t("Sortino") },
    { label: "Calmar", value: fmtNum(m["Calmar Ratio"], 2), tooltip: t("Calmar") },
    { label: "Best Run-up", value: fmtDollar(m["Best Run-up ($)"]), color: "text-ag-green", tooltip: t("Best Run-up") },
    { label: "Run-up Dur", value: fmtDuration(m["Max Run-up Duration"]), tooltip: t("Run-up Dur") },
    { label: "Avg Run-up", value: fmtDollar(m["Avg Run-up ($)"]), tooltip: t("Avg Run-up") },
    { label: "Avg RU Dur", value: fmtDuration(m["Avg Run-up Duration"]), tooltip: t("Avg RU Dur") },
  ];
}

function tradeDistRow(m: M): MetricItem[] {
  return [
    { label: "Max Win", value: fmtDollar(m["Max Win ($)"]), color: "text-ag-green", sub: sub(m["Max Win Detail"]) as string | undefined, tooltip: t("Max Win") },
    { label: "Avg Win", value: fmtDollar(m["Avg Win ($)"]), color: "text-ag-green", tooltip: t("Avg Win") },
    { label: "Avg Win Dur", value: fmtDuration(m["Avg Win Duration"]), tooltip: t("Avg Win Dur") },
    { label: "Max Loss", value: fmtDollar(m["Max Loss ($)"]), color: "text-ag-red", sub: sub(m["Max Loss Detail"]) as string | undefined, tooltip: t("Max Loss") },
    { label: "Avg Loss", value: fmtDollar(m["Avg Loss ($)"]), color: "text-ag-red", tooltip: t("Avg Loss") },
    { label: "Avg Loss Dur", value: fmtDuration(m["Avg Loss Duration"]), tooltip: t("Avg Loss Dur") },
    { label: "W/L Ratio", value: fmtNum(m["Avg Win/Loss Ratio"], 2), tooltip: t("W/L Ratio") },
    { label: "Consec Wins", value: fmtInt(m["Max Consecutive Wins"]), sub: streakSub(m, "win"), tooltip: t("Consec Wins") },
    { label: "Consec Losses", value: fmtInt(m["Max Consecutive Losses"]), sub: streakSub(m, "loss"), tooltip: t("Consec Losses") },
  ];
}

function streakSub(m: M, kind: "win" | "loss"): string | undefined {
  const startKey = kind === "win" ? "Max Consec Wins Period Start" : "Max Consec Losses Period Start";
  const endKey = kind === "win" ? "Max Consec Wins Period End" : "Max Consec Losses Period End";
  const s = fmtShortDate(m[startKey]);
  const e = fmtShortDate(m[endKey]);
  return (s && e) ? `${s} — ${e}` : undefined;
}

function hasExitReasons(m: M): boolean {
  return m["TSL Win Rate"] != null || m["TP Exit Count"] != null;
}

function exitReasonRow(m: M): MetricItem[] {
  return [
    { label: "TSL WR", value: fmtPct(m["TSL Win Rate"]), tooltip: t("TSL WR") },
    { label: "Avg Win TP", value: fmtDollar(m["Avg Win TP"]), tooltip: t("Avg Win TP") },
    { label: "Avg Win TSL", value: fmtDollar(m["Avg Win TSL"]), tooltip: t("Avg Win TSL") },
    { label: "TP Exits", value: fmtInt(m["TP Exit Count"]), tooltip: t("TP Exits") },
    { label: "TSL Exits", value: fmtInt(m["TSL Exit Count"]), tooltip: t("TSL Exits") },
    { label: "SL Exits", value: fmtInt(m["SL Exit Count"]), tooltip: t("SL Exits") },
  ];
}

function excursionRow(m: M): MetricItem[] {
  return [
    { label: "MAE Worst", value: fmtDollar(m["MAE Max ($)"]), sub: sub(m["MAE Detail"]) as string | undefined, tooltip: t("MAE Worst") },
    { label: "Avg MAE", value: fmtDollar(m["MAE Avg ($)"]), tooltip: t("Avg MAE") },
    { label: "MFE Best", value: fmtDollar(m["MFE Max ($)"]), color: "text-ag-green", sub: sub(m["MFE Detail"]) as string | undefined, tooltip: t("MFE Best") },
    { label: "Avg MFE", value: fmtDollar(m["MFE Avg ($)"]), tooltip: t("Avg MFE") },
    { label: "Comm. Drag", value: fmtPct(m["Commission Drag (%)"]), tooltip: t("Comm. Drag") },
  ];
}

function statsRow(m: M): MetricItem[] {
  const tStat = Number(m["T-Statistic"]) || 0;
  const nAssets = Number(m["N Assets"]) || 0;
  const nTrades = Number(m["Total Positions"]) || 0;
  const avgPerAsset = nAssets > 0 ? (nTrades / nAssets).toFixed(1) : "\u2014";

  return [
    { label: "T-Stat", value: fmtNum(m["T-Statistic"], 2), sub: `${tStatConfidence(tStat)} conf.`, tooltip: t("T-Stat") },
    { label: "N Assets", value: fmtInt(m["N Assets"]), tooltip: t("N Assets") },
    { label: "Long Assets", value: fmtInt(m["N Long Assets"]), tooltip: t("Long Assets") },
    { label: "Short Assets", value: fmtInt(m["N Short Assets"]), tooltip: t("Short Assets") },
    { label: "Max Simult.", value: fmtInt(m["Max Simultaneous Trades"]), tooltip: t("Max Simult.") },
    { label: "Avg Tr/Asset", value: avgPerAsset, tooltip: t("Avg Tr/Asset") },
  ];
}
