/**
 * Long/Short breakdown table comparing key metrics by side.
 * Extracted from PortfolioFullMetrics for reuse.
 */
import { fmtDollar, fmtNum, fmtPct, fmtInt } from "./MetricFormatters";

interface Props {
  long: Record<string, number | string | null>;
  short: Record<string, number | string | null>;
}

const KEYS = [
  { k: "Total Net Profit", label: "Net Profit", fmt: fmtDollar },
  { k: "Total Positions", label: "Positions", fmt: fmtInt },
  { k: "Win Rate", label: "Win Rate", fmt: fmtPct },
  { k: "Profit Factor", label: "PF", fmt: (v: unknown) => fmtNum(v, 2) },
  { k: "Sortino Ratio", label: "Sortino", fmt: (v: unknown) => fmtNum(v, 2) },
  { k: "Recovery Factor", label: "Recovery", fmt: (v: unknown) => fmtNum(v, 2) },
  { k: "Max Drawdown ($)", label: "Max DD", fmt: fmtDollar },
  { k: "Avg Trade Profit", label: "Avg Trade", fmt: fmtDollar },
];

export default function LSBreakdown({ long, short }: Props) {
  if (!Object.keys(long).length && !Object.keys(short).length) return null;

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        Long / Short Breakdown
      </h4>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-ag-border text-ag-text-muted">
              <th className="text-left py-1 px-2">Metric</th>
              <th className="text-right py-1 px-2 text-ag-green">Long</th>
              <th className="text-right py-1 px-2 text-ag-red">Short</th>
            </tr>
          </thead>
          <tbody>
            {KEYS.map(({ k, label, fmt }) => (
              <tr key={k} className="border-b border-ag-border/20">
                <td className="py-1 px-2 text-ag-text-muted">{label}</td>
                <td className="py-1 px-2 text-right font-mono">
                  {long[k] != null ? fmt(long[k]) : "\u2014"}
                </td>
                <td className="py-1 px-2 text-right font-mono">
                  {short[k] != null ? fmt(short[k]) : "\u2014"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
