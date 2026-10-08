/**
 * Monthly PnL heatmap (Year x Month) for portfolio analysis.
 * Supports both plain number format and enriched { pnl, trades, win_rate } format.
 * Cells with data are clickable when onCellClick is provided.
 */

type CellValue = number | { pnl: number; trades: number; win_rate: number };

interface Props {
  matrix: Record<string, Record<string, CellValue>>;
  title?: string;
  /** Called when a month cell with data is clicked */
  onCellClick?: (year: number, month: number) => void;
}

const MONTHS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"];
const LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function getPnl(v: CellValue | undefined): number | null {
  if (v == null) return null;
  return typeof v === "number" ? v : v.pnl;
}

function getTrades(v: CellValue | undefined): number | null {
  if (v == null || typeof v === "number") return null;
  return v.trades;
}

function getWinRate(v: CellValue | undefined): number | null {
  if (v == null || typeof v === "number") return null;
  return v.win_rate;
}

function fmtPnl(v: number): string {
  return Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(0);
}

export default function MonthlyMatrix({ matrix, title = "Monthly PnL", onCellClick }: Props) {
  const years = Object.keys(matrix).sort();
  if (years.length === 0) return null;

  // Find max absolute PnL for heatmap intensity
  let maxAbs = 0;
  for (const yr of years) {
    for (const mo of MONTHS) {
      const pnl = getPnl(matrix[yr]?.[mo]);
      if (pnl != null) maxAbs = Math.max(maxAbs, Math.abs(pnl));
    }
  }

  // Detect if any cell has enriched data (trades + win rate)
  const hasEnriched = years.some((yr) =>
    MONTHS.some((mo) => {
      const v = matrix[yr]?.[mo];
      return v != null && typeof v !== "number";
    })
  );

  const clickable = !!onCellClick;

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        {title}
      </h4>
      <div className="overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr className="text-ag-text-muted">
              <th className="py-1 px-1 text-left">Year</th>
              {LABELS.map((ml) => (
                <th key={ml} className="py-1 px-1 text-center">{ml}</th>
              ))}
              <th className="py-1 px-1 text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            {years.map((yr) => {
              let yearPnl = 0;
              let yearTrades = 0;
              let yearWins = 0;
              for (const mo of MONTHS) {
                const cell = matrix[yr]?.[mo];
                const pnl = getPnl(cell);
                const trades = getTrades(cell);
                const wr = getWinRate(cell);
                if (pnl != null) yearPnl += pnl;
                if (trades != null) {
                  yearTrades += trades;
                  yearWins += Math.round(trades * (wr ?? 0) / 100);
                }
              }
              const yearWR = yearTrades > 0 ? (yearWins / yearTrades * 100) : 0;

              return (
                <tr key={yr} className="border-t border-ag-border/20">
                  <td className="py-1 px-1 text-ag-text-muted font-medium">{yr}</td>
                  {MONTHS.map((mo) => {
                    const cell = matrix[yr]?.[mo];
                    const pnl = getPnl(cell);
                    if (pnl == null) {
                      return (
                        <td key={mo} className="py-1 px-1 text-center text-ag-text-muted">
                          &mdash;
                        </td>
                      );
                    }
                    const trades = getTrades(cell);
                    const wr = getWinRate(cell);
                    const intensity = maxAbs > 0 ? Math.min(Math.abs(pnl) / maxAbs, 1) : 0;
                    const bg = pnl >= 0
                      ? `rgba(34, 197, 94, ${0.1 + intensity * 0.4})`
                      : `rgba(239, 68, 68, ${0.1 + intensity * 0.4})`;

                    return (
                      <td
                        key={mo}
                        className={`py-0.5 px-1 text-center font-mono${clickable ? " cursor-pointer hover:ring-1 hover:ring-ag-accent/50 transition-shadow rounded-sm" : ""}`}
                        style={{ backgroundColor: bg }}
                        onClick={clickable ? () => onCellClick(Number(yr), Number(mo)) : undefined}
                      >
                        <div>{fmtPnl(pnl)}</div>
                        {hasEnriched && trades != null && (
                          <div className="text-[8px] text-ag-text-muted/70 leading-tight">
                            {trades}tr · {wr?.toFixed(0)}%
                          </div>
                        )}
                      </td>
                    );
                  })}
                  <td className={`py-1 px-1 text-right font-mono font-medium ${yearPnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                    <div>${fmtPnl(yearPnl)}</div>
                    {hasEnriched && yearTrades > 0 && (
                      <div className="text-[8px] text-ag-text-muted/70 leading-tight">
                        {yearTrades}tr · {yearWR.toFixed(0)}%
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
