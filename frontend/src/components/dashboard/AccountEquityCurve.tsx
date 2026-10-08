"use client";
import { useState, useCallback } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from "recharts";
import { TrendingUp, TrendingDown } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";

const PERIODS = [
  { key: "w", label: "1W" },
  { key: "m", label: "1M" },
  { key: "3m", label: "3M" },
  { key: "6m", label: "6M" },
  { key: "y", label: "1Y" },
  { key: "all", label: "All" },
] as const;

interface Props {
  portfolioId?: number;
}

export default function AccountEquityCurve({ portfolioId }: Props) {
  const [period, setPeriod] = useState("3m");

  const { data } = useApi(
    useCallback(() => api.accountEquity(period, portfolioId), [period, portfolioId]),
    `account-equity-${period}-${portfolioId ?? "all"}`,
  );

  const points = data?.data ?? [];
  if (points.length === 0) return null;

  const lastEquity = points[points.length - 1]?.equity ?? 0;
  const isPositive = lastEquity >= 0;

  // Compute key metrics from the equity data
  const peak = Math.max(...points.map((p: { equity: number }) => p.equity));
  const maxDD = Math.min(...points.map((p: { drawdown?: number }) => p.drawdown ?? 0));
  const totalReturn = lastEquity;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`p-2 rounded-lg ${isPositive ? "bg-ag-green/10" : "bg-ag-red/10"}`}>
            {isPositive
              ? <TrendingUp size={18} className="text-ag-green" />
              : <TrendingDown size={18} className="text-ag-red" />}
          </div>
          <div>
            <h3 className="text-sm font-semibold">Equity Curve</h3>
            <span className={`text-xs font-mono font-bold ${isPositive ? "text-ag-green" : "text-ag-red"}`}>
              {isPositive ? "+" : ""}${lastEquity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-4">
          {/* Key metrics badges */}
          <div className="hidden md:flex items-center gap-3">
            <div className="text-center px-3 py-1 bg-ag-dark/60 rounded-lg">
              <p className="text-[9px] text-ag-text-muted uppercase">Peak</p>
              <p className="text-xs font-bold font-mono text-ag-green">
                ${peak.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
              </p>
            </div>
            <div className="text-center px-3 py-1 bg-ag-dark/60 rounded-lg">
              <p className="text-[9px] text-ag-text-muted uppercase">Max DD</p>
              <p className="text-xs font-bold font-mono text-ag-red">
                ${Math.abs(maxDD).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
              </p>
            </div>
            <div className="text-center px-3 py-1 bg-ag-dark/60 rounded-lg">
              <p className="text-[9px] text-ag-text-muted uppercase">Return</p>
              <p className={`text-xs font-bold font-mono ${totalReturn >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                {totalReturn >= 0 ? "+" : ""}${totalReturn.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
              </p>
            </div>
          </div>

          {/* Period selector */}
          <div className="flex gap-1 bg-ag-dark/40 rounded-lg p-0.5">
            {PERIODS.map((p) => (
              <button
                key={p.key}
                onClick={() => setPeriod(p.key)}
                className={`px-2.5 py-1 text-[10px] rounded-md font-medium transition-colors ${
                  period === p.key
                    ? "bg-ag-accent text-ag-darker"
                    : "text-ag-text-muted hover:text-ag-text"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Chart — larger height */}
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={isPositive ? "#22c55e" : "#ef4444"} stopOpacity={0.25} />
                <stop offset="95%" stopColor={isPositive ? "#22c55e" : "#ef4444"} stopOpacity={0} />
              </linearGradient>
              <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#ef4444" stopOpacity={0} />
                <stop offset="95%" stopColor="#ef4444" stopOpacity={0.15} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.1)" strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "#666" }}
              tickFormatter={(v: string) => {
                const d = new Date(v);
                return `${d.getMonth() + 1}/${d.getDate()}`;
              }}
              interval="preserveStartEnd"
              axisLine={{ stroke: "rgba(255,255,255,0.06)" }}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "#666" }}
              tickFormatter={(v: number) => `$${v}`}
              width={55}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "#1a1a2e",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 10,
                fontSize: 12,
                padding: "8px 12px",
              }}
              formatter={(value: number, name: string) => {
                if (name === "equity") return [`$${value.toFixed(2)}`, "Cumulative P&L"];
                if (name === "drawdown") return [`$${value.toFixed(2)}`, "Drawdown"];
                return [value, name];
              }}
              labelFormatter={(label: string) => new Date(label).toLocaleDateString()}
            />
            {/* Drawdown area (below zero) */}
            <Area
              type="monotone"
              dataKey="drawdown"
              stroke="#ef4444"
              fill="url(#ddGrad)"
              strokeWidth={1}
              strokeOpacity={0.4}
              dot={false}
            />
            {/* Equity line */}
            <Area
              type="monotone"
              dataKey="equity"
              stroke={isPositive ? "#22c55e" : "#ef4444"}
              fill="url(#eqGrad)"
              strokeWidth={2}
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Mobile metric badges — shown below chart on small screens */}
      <div className="md:hidden grid grid-cols-3 gap-2">
        <div className="text-center py-1.5 bg-ag-dark/40 rounded-lg">
          <p className="text-[9px] text-ag-text-muted uppercase">Peak</p>
          <p className="text-xs font-bold font-mono text-ag-green">${peak.toFixed(0)}</p>
        </div>
        <div className="text-center py-1.5 bg-ag-dark/40 rounded-lg">
          <p className="text-[9px] text-ag-text-muted uppercase">Max DD</p>
          <p className="text-xs font-bold font-mono text-ag-red">${Math.abs(maxDD).toFixed(0)}</p>
        </div>
        <div className="text-center py-1.5 bg-ag-dark/40 rounded-lg">
          <p className="text-[9px] text-ag-text-muted uppercase">Return</p>
          <p className={`text-xs font-bold font-mono ${totalReturn >= 0 ? "text-ag-green" : "text-ag-red"}`}>
            {totalReturn >= 0 ? "+" : ""}${totalReturn.toFixed(0)}
          </p>
        </div>
      </div>
    </div>
  );
}
