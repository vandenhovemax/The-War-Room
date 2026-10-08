"use client";
import { useState, useEffect } from "react";
import type { DashboardAnalysisRequest } from "@/lib/api";

interface HealthData {
  correlation_pctiles: Record<string, number>;
  risk_kpis: Record<string, number | null>;
  n_assets: number;
  excluded_assets?: string[];
}

interface Props {
  req: DashboardAnalysisRequest;
}

function corrLabel(val: number): { text: string; color: string } {
  if (val >= 0.7) return { text: "High risk", color: "text-ag-red" };
  if (val >= 0.5) return { text: "Moderate", color: "text-ag-yellow" };
  if (val >= 0.3) return { text: "Low", color: "text-ag-green" };
  return { text: "Excellent", color: "text-ag-green" };
}

function Gauge({ label, value, subtext, color }: {
  label: string; value: string; subtext?: string; color?: string;
}) {
  return (
    <div className="text-center">
      <p className="text-[10px] text-ag-text-muted uppercase tracking-wider">{label}</p>
      <p className={`text-lg font-semibold ${color ?? "text-ag-text"}`}>{value}</p>
      {subtext && <p className="text-[9px] text-ag-text-muted">{subtext}</p>}
    </div>
  );
}

export default function HealthPanel({ req }: Props) {
  const [data, setData] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(false);
  const [corrMethod, setCorrMethod] = useState("standard");

  useEffect(() => {
    if (!req.batch_id && !req.strategy_filename && !req.portfolio_id) return;
    setLoading(true);
    fetch("/api/dashboard/health", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...req, corr_method: corrMethod }),
    })
      .then((r) => r.json())
      .then((d) => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [req.batch_id, req.portfolio_id, req.strategy_filename, req.side_filter, req.date_to, req.min_trades, req.exclude_top_n, req.pf_min, req.pf_max, req.wr_min, corrMethod]);

  if (loading) return <div className="bg-ag-card border border-ag-border rounded-lg p-4 text-sm text-ag-text-muted">Loading health data...</div>;
  if (!data || data.n_assets === 0) return null;

  const cp = data.correlation_pctiles;
  const rk = data.risk_kpis;
  const hasCorrData = Object.keys(cp).length > 0;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-ag-text">Portfolio Health</h3>
        <div className="flex items-center gap-2">
          <select value={corrMethod} onChange={(e) => setCorrMethod(e.target.value)}
            className="text-xs bg-ag-dark border border-ag-border rounded px-2 py-1">
            <option value="standard">Standard Correlation</option>
            <option value="trade_overlap">Trade Overlap</option>
          </select>
        </div>
      </div>

      {/* Risk KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
        <Gauge label="Assets" value={String(rk.total_assets ?? 0)} />
        <Gauge label="Profit Factor"
          value={rk.profit_factor != null ? String(rk.profit_factor) : "\u2014"}
          color={(rk.profit_factor ?? 0) >= 1.5 ? "text-ag-green" : (rk.profit_factor ?? 0) >= 1 ? "text-ag-yellow" : "text-ag-red"} />
        <Gauge label="Mean PF"
          value={rk.mean_pf != null ? String(rk.mean_pf) : "\u2014"}
          subtext="per-asset avg"
          color={(rk.mean_pf ?? 0) >= 1.5 ? "text-ag-green" : (rk.mean_pf ?? 0) >= 1 ? "text-ag-yellow" : "text-ag-red"} />
        <Gauge label="Median PF"
          value={rk.median_pf != null ? String(rk.median_pf) : "\u2014"}
          subtext="per-asset mid"
          color={(rk.median_pf ?? 0) >= 1.5 ? "text-ag-green" : (rk.median_pf ?? 0) >= 1 ? "text-ag-yellow" : "text-ag-red"} />
        <Gauge label="Win Rate"
          value={`${rk.win_rate ?? 0}%`}
          color={(rk.win_rate ?? 0) >= 50 ? "text-ag-green" : "text-ag-red"} />
        <Gauge label="Max Drawdown"
          value={`$${(rk.max_drawdown ?? 0).toLocaleString()}`}
          color="text-ag-red" />
        <Gauge label="Top Asset %"
          value={`${rk.top_asset_concentration ?? 0}%`}
          subtext="concentration"
          color={(rk.top_asset_concentration ?? 0) > 40 ? "text-ag-red" : (rk.top_asset_concentration ?? 0) > 25 ? "text-ag-yellow" : "text-ag-green"} />
      </div>

      {/* Correlation / Diversification */}
      {hasCorrData && (
        <div>
          <h4 className="text-xs text-ag-text-muted uppercase tracking-wider mb-2">Diversification (Correlation Percentiles)</h4>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            <CorrStat label="95th %ile" value={cp.p95} note="5% MORE correlated" />
            <CorrStat label="80th %ile" value={cp.p80} note="20% more correlated" />
            <CorrStat label="Median" value={cp.median} note="typical pair" />
            <CorrStat label="25th %ile" value={cp.p25} note="diverse pairs" />
            <CorrStat label="Mean" value={cp.mean} note="avg correlation" />
          </div>

          {/* Pair count diagnostic */}
          {cp.pair_count !== undefined && (
            <p className="text-[9px] text-ag-text-muted mt-1">
              {cp.pair_count} pairs analyzed{cp.non_zero_pairs !== undefined ? `, ${cp.non_zero_pairs} with non-zero correlation` : ""}.
              {corrMethod === "trade_overlap" && " Trade Overlap: pairs with <3 shared active days default to 0."}
            </p>
          )}

          {/* Interpretation guide */}
          <div className="mt-3 text-[10px] text-ag-text-muted grid grid-cols-2 sm:grid-cols-4 gap-1">
            <span><span className="text-ag-red font-medium">0.7–1.0</span> Concentration risk</span>
            <span><span className="text-ag-yellow font-medium">0.3–0.7</span> Moderate diversity</span>
            <span><span className="text-ag-green font-medium">0–0.3</span> Well diversified</span>
            <span><span className="text-ag-green font-medium">&lt;0</span> Hedge / anti-correlated</span>
          </div>
        </div>
      )}
    </div>
  );
}

function CorrStat({ label, value, note }: { label: string; value: number; note: string }) {
  const { text, color } = corrLabel(value);
  return (
    <div className="text-center">
      <p className="text-[10px] text-ag-text-muted">{label}</p>
      <p className={`text-base font-semibold font-mono ${color}`}>{value.toFixed(3)}</p>
      <p className="text-[9px] text-ag-text-muted">{text} · {note}</p>
    </div>
  );
}
