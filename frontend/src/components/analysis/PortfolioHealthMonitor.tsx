"use client";
import { useState, useEffect } from "react";
import { Activity, TrendingUp, TrendingDown, Minus, AlertTriangle, CheckCircle, XCircle, Clock } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioHealthResult, PortfolioHealthWindow, PortfolioHealthAsset } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  portfolioId: number;
  portfolioType?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function statusColor(status: string): string {
  if (status === "healthy") return "text-ag-green";
  if (status === "warning") return "text-yellow-400";
  if (status === "critical") return "text-ag-red";
  return "text-ag-text-muted";
}

function statusBg(status: string): string {
  if (status === "healthy") return "bg-ag-green/15 border-ag-green/30";
  if (status === "warning") return "bg-yellow-400/15 border-yellow-400/30";
  if (status === "critical") return "bg-ag-red/15 border-ag-red/30";
  return "bg-ag-dark border-ag-border";
}

function StatusIcon({ status }: { status: string }) {
  if (status === "healthy") return <CheckCircle size={14} className="text-ag-green" />;
  if (status === "warning") return <AlertTriangle size={14} className="text-yellow-400" />;
  if (status === "critical") return <XCircle size={14} className="text-ag-red" />;
  return <Clock size={14} className="text-ag-text-muted" />;
}

function TrendIcon({ trend }: { trend: string }) {
  if (trend === "improving") return <TrendingUp size={14} className="text-ag-green" />;
  if (trend === "declining") return <TrendingDown size={14} className="text-ag-red" />;
  return <Minus size={14} className="text-ag-text-muted" />;
}

function gradeColor(grade: string): string {
  if (grade.startsWith("A")) return "bg-ag-green/20 text-ag-green border-ag-green/30";
  if (grade.startsWith("B")) return "bg-lime-500/20 text-lime-400 border-lime-500/30";
  if (grade.startsWith("C")) return "bg-yellow-500/20 text-yellow-400 border-yellow-500/30";
  if (grade.startsWith("D")) return "bg-orange-500/20 text-orange-400 border-orange-500/30";
  return "bg-ag-red/20 text-ag-red border-ag-red/30";
}

function pfColor(pf: number): string {
  if (pf >= 1.5) return "text-ag-green";
  if (pf >= 1.0) return "text-yellow-400";
  return "text-ag-red";
}

function trendArrow(val: number, invert: boolean = false): string {
  const v = invert ? -val : val;
  if (v > 0.1) return "text-ag-green";
  if (v < -0.1) return "text-ag-red";
  return "text-ag-text-muted";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function OverallStatus({ data }: { data: PortfolioHealthResult }) {
  return (
    <div className={`rounded-lg border p-4 ${statusBg(data.overall_status)}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <StatusIcon status={data.overall_status} />
          <span className={`text-sm font-semibold capitalize ${statusColor(data.overall_status)}`}>
            {data.overall_status}
          </span>
          {data.overall_grade && (
            <span className={`px-2 py-0.5 rounded text-xs font-bold border ${gradeColor(data.overall_grade)}`}
              title={`Grade score: ${(data.overall_grade_score * 100).toFixed(0)}%`}>
              {data.overall_grade}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <TrendIcon trend={data.trend} />
          <span className={`text-xs capitalize ${
            data.trend === "improving" ? "text-ag-green" :
            data.trend === "declining" ? "text-ag-red" : "text-ag-text-muted"
          }`}>
            {data.trend}
          </span>
        </div>
      </div>

      {data.trend_detail && (
        <p className="text-[10px] text-ag-text-muted mb-3">{data.trend_detail}</p>
      )}

      {/* Asset status summary */}
      <div className="grid grid-cols-4 gap-2 text-xs">
        <div className="text-center">
          <p className="text-ag-green font-semibold">{data.summary.healthy_assets}</p>
          <p className="text-[10px] text-ag-text-muted">Healthy</p>
        </div>
        <div className="text-center">
          <p className="text-yellow-400 font-semibold">{data.summary.warning_assets}</p>
          <p className="text-[10px] text-ag-text-muted">Warning</p>
        </div>
        <div className="text-center">
          <p className="text-ag-red font-semibold">{data.summary.critical_assets}</p>
          <p className="text-[10px] text-ag-text-muted">Critical</p>
        </div>
        <div className="text-center">
          <p className="text-ag-text-muted font-semibold">{data.summary.inactive_assets}</p>
          <p className="text-[10px] text-ag-text-muted">Inactive</p>
        </div>
      </div>
    </div>
  );
}

function WindowsTable({ windows }: { windows: PortfolioHealthWindow[] }) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-3">
      <h4 className="text-xs font-semibold text-ag-text mb-2 flex items-center gap-1.5">
        Rolling Window Metrics
        <HintTip text="Portfolio performance across different time windows. Compare recent windows to full period to spot decay. A declining PF in recent windows signals the edge may be fading for some assets." />
      </h4>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-ag-text-muted border-b border-ag-border">
              <th className="text-left py-1.5 pr-3">Period</th>
              <th className="text-right px-2">Trades</th>
              <th className="text-right px-2">Net Profit</th>
              <th className="text-right px-2">PF</th>
              <th className="text-right px-2">Win Rate</th>
              <th className="text-right px-2">Max DD</th>
              <th className="text-right px-2">Stability</th>
              <th className="text-right px-2">Sortino</th>
              <th className="text-right px-2">Grade</th>
            </tr>
          </thead>
          <tbody>
            {windows.map((w) => (
              <tr key={w.window_days} className="border-b border-ag-border/50 hover:bg-ag-dark/30">
                <td className="py-1.5 pr-3 font-medium">{w.label}</td>
                <td className="text-right px-2">{w.trades}</td>
                <td className={`text-right px-2 ${w.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                  ${w.net_profit.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
                <td className={`text-right px-2 font-semibold ${pfColor(w.profit_factor)}`}>
                  {w.profit_factor.toFixed(2)}
                </td>
                <td className="text-right px-2">{w.win_rate.toFixed(1)}%</td>
                <td className="text-right px-2 text-ag-red">
                  ${Math.abs(w.max_drawdown).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
                <td className="text-right px-2">{(w.stability * 100).toFixed(0)}%</td>
                <td className="text-right px-2">{w.sortino.toFixed(1)}</td>
                <td className="text-right px-2">
                  {w.grade && (
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${gradeColor(w.grade)}`}>
                      {w.grade}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AssetHealthTable({ assets }: { assets: PortfolioHealthAsset[] }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? assets : assets.slice(0, 10);

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-3">
      <h4 className="text-xs font-semibold text-ag-text mb-2 flex items-center gap-1.5">
        Per-Asset Health
        <HintTip text="Health status of each asset in the portfolio. Assets are sorted: critical first, then warning, then by contribution. Trends compare recent window to historical performance. Negative PF trend means the asset is performing worse recently." />
      </h4>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-ag-text-muted border-b border-ag-border">
              <th className="text-left py-1.5 pr-2">Status</th>
              <th className="text-left pr-2">Asset</th>
              <th className="text-right px-2">Trades</th>
              <th className="text-right px-2">Net P&L</th>
              <th className="text-right px-2">PF</th>
              <th className="text-right px-2">WR</th>
              <th className="text-right px-2">Contrib%</th>
              <th className="text-right px-2">
                PF Trend
                <HintTip text="Recent PF minus historical PF. Positive = improving. Negative = decaying." />
              </th>
              <th className="text-right px-2">
                WR Trend
                <HintTip text="Recent win rate minus historical win rate." />
              </th>
              <th className="text-left px-2">Note</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((a) => (
              <tr key={a.asset} className={`border-b border-ag-border/50 hover:bg-ag-dark/30 ${
                a.status === "critical" ? "bg-ag-red/5" :
                a.status === "warning" ? "bg-yellow-400/5" : ""
              }`}>
                <td className="py-1.5 pr-2">
                  <StatusIcon status={a.status} />
                </td>
                <td className="pr-2 font-mono">
                  {a.asset.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")}
                </td>
                <td className="text-right px-2">{a.trades}</td>
                <td className={`text-right px-2 ${a.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                  ${a.net_profit.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
                <td className={`text-right px-2 font-semibold ${pfColor(a.profit_factor)}`}>
                  {a.profit_factor.toFixed(2)}
                </td>
                <td className="text-right px-2">{a.win_rate.toFixed(0)}%</td>
                <td className="text-right px-2">
                  {a.contribution_pct.toFixed(1)}%
                </td>
                <td className={`text-right px-2 font-mono ${trendArrow(a.pf_trend)}`}>
                  {a.pf_trend > 0 ? "+" : ""}{a.pf_trend.toFixed(2)}
                </td>
                <td className={`text-right px-2 font-mono ${trendArrow(a.wr_trend)}`}>
                  {a.wr_trend > 0 ? "+" : ""}{a.wr_trend.toFixed(0)}%
                </td>
                <td className="px-2 text-[10px] text-ag-text-muted max-w-[180px] truncate" title={a.decay_reason}>
                  {a.decay_reason}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {assets.length > 10 && (
        <button onClick={() => setShowAll(!showAll)}
          className="mt-2 text-xs text-ag-accent hover:underline">
          {showAll ? "Show less" : `Show all ${assets.length} assets`}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function PortfolioHealthMonitor({ portfolioId, portfolioType }: Props) {
  const [data, setData] = useState<PortfolioHealthResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [recentDays, setRecentDays] = useState(180);
  const [includeIS, setIncludeIS] = useState(false);

  const fetchHealth = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.portfolioHealth(portfolioId, {
        windows: [7, 14, 30, 60, 90, 180],
        recent_days: recentDays,
        include_is: includeIS,
      });
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load health data");
      setData(null);
    }
    setLoading(false);
  };

  useEffect(() => { fetchHealth(); }, [portfolioId]);

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-ag-text-muted">
            Recent window
            <HintTip text="Number of days for per-asset 'recent' period when computing decay. Assets' recent metrics are compared to their full history to detect degradation." />
          </label>
          <select value={recentDays} onChange={(e) => setRecentDays(Number(e.target.value))}
            className="text-xs bg-ag-dark border border-ag-border rounded px-2 py-1">
            <option value={30}>30 days</option>
            <option value={60}>60 days</option>
            <option value={90}>90 days</option>
            <option value={180}>180 days</option>
          </select>
        </div>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={includeIS}
            onChange={(e) => setIncludeIS(e.target.checked)}
            className="accent-ag-accent" />
          <span className="text-ag-text-muted">
            Include IS period
            <HintTip text="Include in-sample trades in the analysis. Default: OOS only (trades after optimization end date). Turn on to see full picture, but OOS-only is the true health indicator." />
          </span>
        </label>
        <button onClick={fetchHealth} disabled={loading}
          className="px-3 py-1 bg-ag-accent/20 text-ag-accent rounded text-xs hover:bg-ag-accent/30 disabled:opacity-50">
          {loading ? "Analyzing..." : "Refresh"}
        </button>
        {data && (
          <span className="text-[10px] text-ag-text-muted">
            Analyzed: {data.analysis_date} · {data.summary.oos_trades} OOS trades · Data: {data.summary.data_start} → {data.summary.data_end}
          </span>
        )}
      </div>

      {error && <p className="text-xs text-ag-red">{error}</p>}

      {loading && !data && (
        <p className="text-xs text-ag-text-muted py-4">Computing portfolio health...</p>
      )}

      {data && (
        <>
          {/* Overall status banner */}
          <OverallStatus data={data} />

          {/* Rolling windows table */}
          {data.windows.length > 0 && <WindowsTable windows={data.windows} />}

          {/* Per-asset health */}
          {data.assets.length > 0 && <AssetHealthTable assets={data.assets} />}
        </>
      )}
    </div>
  );
}
