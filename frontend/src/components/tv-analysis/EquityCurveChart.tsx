"use client";

import { useState, useEffect, useMemo } from "react";
import { api, DashboardAnalysisRequest, EquityCurvePoint } from "@/lib/api";

interface Props {
  req: DashboardAnalysisRequest;
}

function fmt$(v: number): string {
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPct(v: number): string {
  return `${v.toFixed(2)}%`;
}

function numColor(v: number): string {
  return v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";
}

export default function EquityCurveChart({ req }: Props) {
  const [data, setData] = useState<EquityCurvePoint[]>([]);
  const [oosStartDate, setOosStartDate] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showDD, setShowDD] = useState(true);

  useEffect(() => {
    if (!req.strategy_filename && !req.batch_id && !req.run_id && !req.portfolio_id) return;
    setLoading(true);
    api.dashboardEquityCurve(req)
      .then((r) => {
        setData(r.data);
        setOosStartDate((r as Record<string, unknown>).oos_start_date as string | null ?? null);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [req.strategy_filename, req.batch_id, req.portfolio_id, req.run_id, req.side_filter, req.date_to, req.min_trades]);

  const W = 800;
  const H = 300;
  const PAD = { top: 20, right: 55, bottom: 30, left: 60 };

  const computed = useMemo(() => {
    if (!data.length)
      return {
        isEqPath: "", oosEqPath: "", ddPath: "",
        yMin: 0, yMax: 0, ddPctMin: 0, xLabels: [],
        oosLineX: null as number | null, oosIdx: -1,
        stats: null,
      };

    const equities = data.map((d) => d.equity);
    const ddPcts = data.map((d) => d.drawdown_pct ?? 0);
    const eqMin = Math.min(0, ...equities);
    const eqMax = Math.max(...equities);
    const ddPctMinVal = Math.min(0, ...ddPcts);

    const chartW = W - PAD.left - PAD.right;
    const chartH = H - PAD.top - PAD.bottom;

    const xScale = (i: number) => PAD.left + (i / Math.max(data.length - 1, 1)) * chartW;
    const yScale = (v: number) =>
      PAD.top + chartH - ((v - eqMin) / Math.max(eqMax - eqMin, 1)) * chartH;
    const ddPctScale = (v: number) =>
      PAD.top + chartH - ((v - ddPctMinVal) / Math.max(-ddPctMinVal, 0.01)) * chartH;

    // Find OOS split index
    let oosIdx = -1;
    if (oosStartDate) {
      const cutoff = oosStartDate.slice(0, 10);
      for (let i = 0; i < data.length; i++) {
        if (data[i].date.slice(0, 10) >= cutoff) {
          oosIdx = i;
          break;
        }
      }
    }

    // Build equity paths — split at OOS boundary
    const allPts = data.map((_, i) => `${xScale(i)},${yScale(equities[i])}`);

    let isEqPath: string;
    let oosEqPath = "";
    let oosLineX: number | null = null;

    if (oosIdx > 0) {
      // IS path: from start to OOS boundary (inclusive)
      isEqPath = `M${allPts.slice(0, oosIdx + 1).join("L")}`;
      // OOS path: from boundary to end (overlap the boundary point so the line connects)
      oosEqPath = `M${allPts.slice(oosIdx).join("L")}`;
      oosLineX = xScale(oosIdx);
    } else {
      isEqPath = `M${allPts.join("L")}`;
    }

    const ddPts = data.map((_, i) => `${xScale(i)},${ddPctScale(ddPcts[i])}`);

    const step = Math.max(1, Math.floor(data.length / 6));
    const labels = data
      .filter((_, i) => i % step === 0)
      .map((d, idx) => ({ x: xScale(idx * step), label: d.date.slice(0, 10) }));

    // Compute stats
    const totalPnl = data.reduce((s, d) => s + d.pnl, 0);
    const maxDD = Math.min(...data.map((d) => d.drawdown));
    const maxDDPct = Math.min(...ddPcts);
    const finalEquity = equities[equities.length - 1];
    const startEquity = equities[0] - data[0].pnl; // initial capital
    const totalReturn = startEquity > 0 ? ((finalEquity - startEquity) / startEquity) * 100 : 0;

    // Compute IS/OOS separate stats
    let isStats = null;
    let oosStats = null;
    if (oosIdx > 0) {
      const isData = data.slice(0, oosIdx);
      const oosData = data.slice(oosIdx);
      const isPnl = isData.reduce((s, d) => s + d.pnl, 0);
      const oosPnl = oosData.reduce((s, d) => s + d.pnl, 0);
      const isMaxDD = Math.min(...isData.map((d) => d.drawdown));
      const oosMaxDD = oosData.length ? Math.min(...oosData.map((d) => d.drawdown)) : 0;
      const isMaxDDPct = Math.min(...isData.map((d) => d.drawdown_pct ?? 0));
      const oosMaxDDPct = oosData.length ? Math.min(...oosData.map((d) => d.drawdown_pct ?? 0)) : 0;
      const isEndEquity = isData.length ? equities[oosIdx - 1] : startEquity;
      const isReturn = startEquity > 0 ? ((isEndEquity - startEquity) / startEquity) * 100 : 0;
      const oosReturn = isEndEquity > 0 ? ((finalEquity - isEndEquity) / isEndEquity) * 100 : 0;

      isStats = {
        pnl: isPnl, trades: isData.length, maxDD: isMaxDD, maxDDPct: isMaxDDPct,
        returnPct: isReturn, endEquity: isEndEquity,
        dateRange: `${isData[0]?.date.slice(0, 10)} — ${isData[isData.length - 1]?.date.slice(0, 10)}`,
      };
      oosStats = {
        pnl: oosPnl, trades: oosData.length, maxDD: oosMaxDD, maxDDPct: oosMaxDDPct,
        returnPct: oosReturn, endEquity: finalEquity,
        dateRange: oosData.length ? `${oosData[0]?.date.slice(0, 10)} — ${oosData[oosData.length - 1]?.date.slice(0, 10)}` : "",
      };
    }

    return {
      isEqPath, oosEqPath, ddPath: `M${ddPts.join("L")}`,
      yMin: eqMin, yMax: eqMax, ddPctMin: ddPctMinVal,
      xLabels: labels, oosLineX, oosIdx,
      stats: {
        totalPnl, maxDD, maxDDPct, finalEquity, startEquity, totalReturn,
        totalTrades: data.length,
        dateRange: `${data[0].date.slice(0, 10)} — ${data[data.length - 1].date.slice(0, 10)}`,
        isStats, oosStats,
      },
    };
  }, [data, oosStartDate]);

  if (loading)
    return (
      <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted">
        Loading chart...
      </div>
    );
  if (!data.length) return null;

  const { isEqPath, oosEqPath, ddPath, yMin, yMax, ddPctMin, xLabels, oosLineX, stats } = computed;
  const chartH = H - PAD.top - PAD.bottom;
  const yTicks = 5;
  const ddTicks = 5;

  return (
    <div className="space-y-3">
      {/* Chart */}
      <div className="bg-ag-card border border-ag-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-ag-text">Equity Curve</h3>
          <div className="flex items-center gap-4">
            {oosEqPath && (
              <div className="flex items-center gap-3 text-[10px]">
                <span className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-ag-green inline-block rounded" />
                  <span className="text-ag-text-muted">In-Sample</span>
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-ag-red inline-block rounded" />
                  <span className="text-ag-text-muted">Out-of-Sample</span>
                </span>
              </div>
            )}
            <label className="flex items-center gap-1.5 text-xs text-ag-text-muted cursor-pointer">
              <input
                type="checkbox"
                checked={showDD}
                onChange={() => setShowDD(!showDD)}
                className="rounded"
              />
              Drawdown %
            </label>
          </div>
        </div>

        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="xMidYMid meet">
          {/* Left y-axis — Equity $ */}
          {Array.from({ length: yTicks + 1 }).map((_, i) => {
            const y = PAD.top + (i / yTicks) * chartH;
            const val = yMax - (i / yTicks) * (yMax - yMin);
            return (
              <g key={`eq-${i}`}>
                <line
                  x1={PAD.left} y1={y} x2={W - PAD.right} y2={y}
                  stroke="var(--color-ag-border)" strokeWidth="0.5"
                />
                <text x={PAD.left - 5} y={y + 3} textAnchor="end"
                  fill="var(--color-ag-text-muted)" fontSize="9">
                  ${Math.round(val)}
                </text>
              </g>
            );
          })}

          {/* Right y-axis — DD % */}
          {showDD && ddPctMin < 0 &&
            Array.from({ length: ddTicks + 1 }).map((_, i) => {
              const y = PAD.top + (i / ddTicks) * chartH;
              const val = -((i / ddTicks) * Math.abs(ddPctMin));
              return (
                <text key={`dd-${i}`} x={W - PAD.right + 5} y={y + 3}
                  textAnchor="start" fill="var(--color-ag-red)" fontSize="9" opacity="0.7">
                  {val.toFixed(1)}%
                </text>
              );
            })}

          {/* X labels */}
          {xLabels.map((l, i) => (
            <text key={i} x={l.x} y={H - 5} textAnchor="middle"
              fill="var(--color-ag-text-muted)" fontSize="8">
              {l.label}
            </text>
          ))}

          {/* OOS shaded region */}
          {oosLineX != null && (
            <rect
              x={oosLineX} y={PAD.top}
              width={W - PAD.right - oosLineX} height={chartH}
              fill="var(--color-ag-red)" opacity="0.04"
            />
          )}

          {/* IS equity line (green) */}
          <path d={isEqPath} fill="none" stroke="var(--color-ag-green)" strokeWidth="1.5" />

          {/* OOS equity line (red) */}
          {oosEqPath && (
            <path d={oosEqPath} fill="none" stroke="var(--color-ag-red)" strokeWidth="1.5" />
          )}

          {/* OOS vertical divider */}
          {oosLineX != null && (
            <line
              x1={oosLineX} y1={PAD.top} x2={oosLineX} y2={PAD.top + chartH}
              stroke="var(--color-ag-accent)" strokeWidth="1" strokeDasharray="4,3" opacity="0.8"
            />
          )}

          {/* OOS label */}
          {oosLineX != null && (
            <text x={oosLineX + 4} y={PAD.top + 10}
              fill="var(--color-ag-accent)" fontSize="8" fontWeight="600">
              OOS Start
            </text>
          )}

          {/* Drawdown % line */}
          {showDD && ddPath && (
            <path d={ddPath} fill="none" stroke="var(--color-ag-red)" strokeWidth="1" opacity="0.4" />
          )}

          {/* Zero line */}
          <line
            x1={PAD.left}
            y1={PAD.top + chartH - ((0 - yMin) / Math.max(yMax - yMin, 1)) * chartH}
            x2={W - PAD.right}
            y2={PAD.top + chartH - ((0 - yMin) / Math.max(yMax - yMin, 1)) * chartH}
            stroke="var(--color-ag-text-muted)" strokeWidth="0.5" strokeDasharray="4,4"
          />
        </svg>
      </div>

      {/* Summary stats */}
      {stats && (
        <div className="space-y-3">
          {/* Overall stats */}
          <div>
            <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
              Overall Performance
            </h4>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatCard label="Total Net Profit" value={fmt$(stats.totalPnl)} color={numColor(stats.totalPnl)} />
              <StatCard label="Total Return" value={fmtPct(stats.totalReturn)} color={numColor(stats.totalReturn)} />
              <StatCard label="Max Drawdown" value={fmt$(stats.maxDD)} color="text-ag-red" />
              <StatCard label="Max DD %" value={fmtPct(stats.maxDDPct)} color="text-ag-red" />
              <StatCard label="Final Equity" value={fmt$(stats.finalEquity)} />
              <StatCard label="Initial Capital" value={fmt$(stats.startEquity)} />
              <StatCard label="Total Trades" value={String(stats.totalTrades)} />
              <StatCard label="Date Range" value={stats.dateRange} small />
            </div>
          </div>

          {/* IS/OOS comparison when available */}
          {stats.isStats && stats.oosStats && (
            <div>
              <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
                In-Sample vs Out-of-Sample
              </h4>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <CompareCard label="Net Profit" isVal={fmt$(stats.isStats.pnl)} oosVal={fmt$(stats.oosStats.pnl)}
                  isColor={numColor(stats.isStats.pnl)} oosColor={numColor(stats.oosStats.pnl)} />
                <CompareCard label="Return %" isVal={fmtPct(stats.isStats.returnPct)} oosVal={fmtPct(stats.oosStats.returnPct)}
                  isColor={numColor(stats.isStats.returnPct)} oosColor={numColor(stats.oosStats.returnPct)} />
                <CompareCard label="Max Drawdown" isVal={fmt$(stats.isStats.maxDD)} oosVal={fmt$(stats.oosStats.maxDD)}
                  isColor="text-ag-red" oosColor="text-ag-red" />
                <CompareCard label="Max DD %" isVal={fmtPct(stats.isStats.maxDDPct)} oosVal={fmtPct(stats.oosStats.maxDDPct)}
                  isColor="text-ag-red" oosColor="text-ag-red" />
                <CompareCard label="Trades" isVal={String(stats.isStats.trades)} oosVal={String(stats.oosStats.trades)} />
                <CompareCard label="End Equity" isVal={fmt$(stats.isStats.endEquity)} oosVal={fmt$(stats.oosStats.endEquity)} />
                <CompareCard label="IS Period" isVal={stats.isStats.dateRange} oosVal="" small />
                <CompareCard label="OOS Period" isVal="" oosVal={stats.oosStats.dateRange} small />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, color, small }: {
  label: string; value: string; color?: string; small?: boolean;
}) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-3">
      <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">{label}</p>
      <p className={`${small ? "text-xs" : "text-sm"} font-semibold ${color || ""}`}>{value}</p>
    </div>
  );
}

function CompareCard({ label, isVal, oosVal, isColor, oosColor, small }: {
  label: string; isVal: string; oosVal: string;
  isColor?: string; oosColor?: string; small?: boolean;
}) {
  const textSize = small ? "text-xs" : "text-sm";
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-3">
      <p className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-0.5">{label}</p>
      <div className="grid grid-cols-2 gap-1">
        {isVal && (
          <div>
            <p className="text-[8px] text-ag-text-muted/60 uppercase">IS</p>
            <p className={`${textSize} font-semibold ${isColor || ""}`}>{isVal}</p>
          </div>
        )}
        {oosVal && (
          <div>
            <p className="text-[8px] text-ag-text-muted/60 uppercase">OOS</p>
            <p className={`${textSize} font-semibold ${oosColor || ""}`}>{oosVal}</p>
          </div>
        )}
      </div>
    </div>
  );
}
