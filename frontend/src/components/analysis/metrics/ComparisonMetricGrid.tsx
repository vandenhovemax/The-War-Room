/**
 * Full grid of comparison KPI cards organized in titled section rows.
 * Shows 1-3 period values side by side within each card.
 */
import ComparisonMetricCard from "./ComparisonMetricCard";
import type { PeriodValue } from "./ComparisonMetricCard";
import { METRIC_TOOLTIPS } from "./metricDefinitions";
import {
  fmtDollar, fmtNum, fmtPct, fmtInt, fmtDuration, numColor,
} from "./MetricFormatters";

// Allow detail objects alongside scalar values
type M = Record<string, number | string | null | Record<string, unknown>>;

export interface PeriodData {
  label: string;
  metrics: M;
}

interface Props {
  periods: PeriodData[];
  /** Which sections to show. Defaults to all three. */
  sections?: ("core" | "quality" | "distribution")[];
  /** Optional: show all 7 detailed sections (used in Full Analysis) */
  showAll?: boolean;
}

interface KpiDef {
  label: string;
  key: string;
  fmt: (v: unknown) => string;
  color?: (v: unknown) => string;
  tooltip?: string;
  /** Key in metrics dict holding detail object (makes card clickable) */
  detailKey?: string;
}

function t(label: string): string | undefined {
  return METRIC_TOOLTIPS[label] || undefined;
}

const CORE: KpiDef[] = [
  { label: "Net Profit", key: "Total Net Profit", fmt: fmtDollar, color: numColor, tooltip: t("Net Profit") },
  { label: "Gross Profit", key: "Gross Profit", fmt: fmtDollar, color: () => "text-ag-green", tooltip: t("Gross Profit") },
  { label: "Gross Loss", key: "Gross Loss", fmt: fmtDollar, color: () => "text-ag-red", tooltip: t("Gross Loss") },
  { label: "Positions", key: "Total Positions", fmt: fmtInt, tooltip: t("Positions") },
  { label: "Avg/Month", key: "Avg Positions/Month", fmt: (v) => fmtNum(v, 1), tooltip: t("Avg/Month") },
  { label: "Win Rate", key: "Win Rate", fmt: fmtPct, tooltip: t("Win Rate") },
  { label: "Profit Factor", key: "Profit Factor", fmt: (v) => fmtNum(v, 2), tooltip: t("Profit Factor") },
  { label: "Max DD", key: "Max Drawdown ($)", fmt: fmtDollar, color: () => "text-ag-red", tooltip: t("Max DD") },
  { label: "Max DD Dur", key: "Max DD Duration", fmt: fmtDuration, tooltip: t("Max DD Dur") },
];

const QUALITY: KpiDef[] = [
  { label: "SQN", key: "SQN", fmt: (v) => fmtNum(v, 2), tooltip: t("SQN") },
  { label: "Recovery", key: "Recovery Factor", fmt: (v) => fmtNum(v, 2), tooltip: t("Recovery") },
  { label: "Sortino", key: "Sortino Ratio", fmt: (v) => fmtNum(v, 2), tooltip: t("Sortino") },
  { label: "Calmar", key: "Calmar Ratio", fmt: (v) => fmtNum(v, 2), tooltip: t("Calmar") },
  { label: "Stability", key: "Stability (R\u00b2)", fmt: (v) => fmtNum(v, 4), tooltip: t("Stability") },
  { label: "Ulcer Index", key: "Ulcer Index", fmt: (v) => fmtNum(v, 4), tooltip: t("Ulcer Index") },
  { label: "Avg DD", key: "Avg Drawdown ($)", fmt: fmtDollar, tooltip: t("Avg DD") },
  { label: "Avg DD Dur", key: "Avg DD Duration", fmt: fmtDuration, tooltip: t("Avg DD Dur") },
];

const DISTRIBUTION: KpiDef[] = [
  { label: "Avg Trade", key: "Avg Trade Profit", fmt: fmtDollar, color: numColor, tooltip: t("Avg Trade") },
  { label: "Avg Win", key: "Avg Win ($)", fmt: fmtDollar, color: () => "text-ag-green", tooltip: t("Avg Win") },
  { label: "Avg Loss", key: "Avg Loss ($)", fmt: fmtDollar, color: () => "text-ag-red", tooltip: t("Avg Loss") },
  { label: "W/L Ratio", key: "Avg Win/Loss Ratio", fmt: (v) => fmtNum(v, 2), tooltip: t("W/L Ratio") },
  { label: "Best Run-up", key: "Best Run-up ($)", fmt: fmtDollar, color: () => "text-ag-green", tooltip: t("Best Run-up") },
  { label: "Max Open", key: "Max Simultaneous Trades", fmt: fmtInt, tooltip: t("Max Simult."), detailKey: "Max Open Details" },
  { label: "Max Open Net", key: "Max Open Post-TP1", fmt: fmtInt, tooltip: t("Max Open Net"), detailKey: "Max Open Post-TP1 Details" },
  { label: "Peak Margin", key: "Peak Margin Used", fmt: fmtDollar, tooltip: t("Peak Margin"), detailKey: "Peak Margin Details" },
];

const EXCURSION: KpiDef[] = [
  { label: "MAE Worst", key: "MAE Max ($)", fmt: fmtDollar, tooltip: t("MAE Worst") },
  { label: "Avg MAE", key: "MAE Avg ($)", fmt: fmtDollar, tooltip: t("Avg MAE") },
  { label: "MFE Best", key: "MFE Max ($)", fmt: fmtDollar, color: () => "text-ag-green", tooltip: t("MFE Best") },
  { label: "Avg MFE", key: "MFE Avg ($)", fmt: fmtDollar, tooltip: t("Avg MFE") },
  { label: "Comm. Drag", key: "Commission Drag (%)", fmt: fmtPct, tooltip: t("Comm. Drag") },
];

const RISK: KpiDef[] = [
  { label: "Max Win", key: "Max Win ($)", fmt: fmtDollar, color: () => "text-ag-green", tooltip: t("Max Win") },
  { label: "Max Loss", key: "Max Loss ($)", fmt: fmtDollar, color: () => "text-ag-red", tooltip: t("Max Loss") },
  { label: "Consec Wins", key: "Max Consecutive Wins", fmt: fmtInt, tooltip: t("Consec Wins") },
  { label: "Consec Losses", key: "Max Consecutive Losses", fmt: fmtInt, tooltip: t("Consec Losses") },
];

const STATS: KpiDef[] = [
  { label: "T-Stat", key: "T-Statistic", fmt: (v) => fmtNum(v, 2), tooltip: t("T-Stat") },
  { label: "N Assets", key: "N Assets", fmt: fmtInt, tooltip: t("N Assets") },
  { label: "Long Assets", key: "N Long Assets", fmt: fmtInt, tooltip: t("Long Assets") },
  { label: "Short Assets", key: "N Short Assets", fmt: fmtInt, tooltip: t("Short Assets") },
];

const SECTION_MAP: Record<string, { title: string; kpis: KpiDef[] }> = {
  core: { title: "Core Performance", kpis: CORE },
  quality: { title: "System Quality", kpis: QUALITY },
  distribution: { title: "Trade Distribution", kpis: DISTRIBUTION },
  excursion: { title: "MAE/MFE & Operational", kpis: EXCURSION },
  risk: { title: "Win/Loss Detail", kpis: RISK },
  stats: { title: "Statistical Significance", kpis: STATS },
};

function buildPeriodValues(kpi: KpiDef, periods: PeriodData[]): PeriodValue[] {
  return periods.map((p) => {
    const raw = p.metrics[kpi.key];
    return {
      label: p.label,
      value: kpi.fmt(raw),
      color: kpi.color ? kpi.color(raw) : undefined,
    };
  });
}

export default function ComparisonMetricGrid({ periods, sections, showAll }: Props) {
  if (periods.length === 0) return null;

  const sectionIds = showAll
    ? ["core", "quality", "distribution", "risk", "excursion", "stats"]
    : (sections ?? ["core", "quality", "distribution"]);

  // Grid columns based on period count: 2 periods = wider cards, 3 = narrower
  const gridCols = periods.length >= 3
    ? "grid-cols-2 sm:grid-cols-3 md:grid-cols-4"
    : "grid-cols-2 sm:grid-cols-3 md:grid-cols-5";

  return (
    <div className="space-y-4">
      {sectionIds.map((id) => {
        const section = SECTION_MAP[id];
        if (!section) return null;
        return (
          <div key={id}>
            <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
              {section.title}
            </h4>
            <div className={`grid ${gridCols} gap-3`}>
              {section.kpis.map((kpi) => {
                // Collect detail objects from first period that has them
                const detail = kpi.detailKey
                  ? periods.find((p) => p.metrics[kpi.detailKey!])?.metrics[kpi.detailKey!] as Record<string, unknown> | undefined
                  : undefined;
                return (
                  <ComparisonMetricCard
                    key={kpi.key}
                    metricLabel={kpi.label}
                    periods={buildPeriodValues(kpi, periods)}
                    tooltip={kpi.tooltip}
                    detail={detail}
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
