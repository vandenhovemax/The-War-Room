"use client";
import { useState, useEffect, useMemo } from "react";
import { api } from "@/lib/api";
import type { DashboardAnalysisRequest, EquityCurvePoint } from "@/lib/api";

interface BTCDay {
  date: string;
  close: number;
}

interface Props {
  req: DashboardAnalysisRequest;
}

export default function BTCContextOverlay({ req }: Props) {
  const [equityData, setEquityData] = useState<EquityCurvePoint[]>([]);
  const [btcData, setBtcData] = useState<BTCDay[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!req.batch_id && !req.run_id && !req.strategy_filename && !req.portfolio_id) return;
    setLoading(true);
    api.dashboardEquityCurve(req)
      .then((r) => {
        setEquityData(r.data);
        if (r.data.length >= 2) {
          const start = r.data[0].date.slice(0, 10);
          const end = r.data[r.data.length - 1].date.slice(0, 10);
          return fetch(`/api/dashboard/btc-daily?start=${start}&end=${end}`).then((res) => res.json());
        }
        return { data: [] };
      })
      .then((btc) => setBtcData(btc.data || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [req.batch_id, req.portfolio_id, req.run_id, req.strategy_filename, req.side_filter, req.date_to, req.min_trades]);

  const W = 800, H = 250;
  const PAD = { top: 15, right: 55, bottom: 25, left: 60 };

  const computed = useMemo(() => {
    if (!equityData.length) return null;

    const equities = equityData.map((d) => d.equity);
    const eqMin = Math.min(0, ...equities);
    const eqMax = Math.max(...equities);
    const chartW = W - PAD.left - PAD.right;
    const chartH = H - PAD.top - PAD.bottom;

    const xScale = (i: number) => PAD.left + (i / Math.max(equityData.length - 1, 1)) * chartW;
    const yScale = (v: number) => PAD.top + chartH - ((v - eqMin) / Math.max(eqMax - eqMin, 1)) * chartH;

    const eqPts = equityData.map((_, i) => `${xScale(i)},${yScale(equities[i])}`);
    const eqPath = `M${eqPts.join("L")}`;

    // BTC overlay — normalized to percentage change from start
    let btcPath = "";
    if (btcData.length >= 2) {
      const btcStart = btcData[0].close;
      const btcPcts = btcData.map((d) => ((d.close - btcStart) / btcStart) * 100);
      const btcMin = Math.min(0, ...btcPcts);
      const btcMax = Math.max(0, ...btcPcts);
      const btcRange = Math.max(btcMax - btcMin, 0.01);

      const btcYScale = (v: number) => PAD.top + chartH - ((v - btcMin) / btcRange) * chartH;
      const btcXScale = (i: number) => PAD.left + (i / Math.max(btcData.length - 1, 1)) * chartW;
      const btcPts = btcData.map((_, i) => `${btcXScale(i)},${btcYScale(btcPcts[i])}`);
      btcPath = `M${btcPts.join("L")}`;
    }

    const step = Math.max(1, Math.floor(equityData.length / 6));
    const labels = equityData
      .filter((_, i) => i % step === 0)
      .map((d, idx) => ({ x: xScale(idx * step), label: d.date.slice(0, 10) }));

    return { eqPath, btcPath, yMin: eqMin, yMax: eqMax, xLabels: labels };
  }, [equityData, btcData]);

  if (loading) return <div className="bg-ag-card border border-ag-border rounded-lg p-4 text-ag-text-muted text-sm">Loading BTC overlay...</div>;
  if (!computed) return null;

  const chartH = H - PAD.top - PAD.bottom;
  const yTicks = 4;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <div className="flex items-center gap-3 mb-2">
        <h3 className="text-sm font-semibold text-ag-text">Equity vs BTC</h3>
        <div className="flex items-center gap-3 text-[10px]">
          <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-ag-green inline-block" /> Portfolio</span>
          <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-yellow-500 inline-block" /> BTC %</span>
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="xMidYMid meet">
        {/* Grid + Y ticks */}
        {Array.from({ length: yTicks + 1 }).map((_, i) => {
          const y = PAD.top + (i / yTicks) * chartH;
          const val = computed.yMax - (i / yTicks) * (computed.yMax - computed.yMin);
          return (
            <g key={i}>
              <line x1={PAD.left} y1={y} x2={W - PAD.right} y2={y}
                stroke="var(--color-ag-border)" strokeWidth="0.5" />
              <text x={PAD.left - 5} y={y + 3} textAnchor="end"
                fill="var(--color-ag-text-muted)" fontSize="9">${Math.round(val)}</text>
            </g>
          );
        })}

        {/* X labels */}
        {computed.xLabels.map((l, i) => (
          <text key={i} x={l.x} y={H - 3} textAnchor="middle"
            fill="var(--color-ag-text-muted)" fontSize="8">{l.label}</text>
        ))}

        {/* Equity line */}
        <path d={computed.eqPath} fill="none" stroke="var(--color-ag-green)" strokeWidth="1.5" />

        {/* BTC line */}
        {computed.btcPath && (
          <path d={computed.btcPath} fill="none" stroke="#eab308" strokeWidth="1" opacity="0.7" strokeDasharray="3,2" />
        )}
      </svg>
    </div>
  );
}
