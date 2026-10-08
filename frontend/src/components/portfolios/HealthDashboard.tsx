"use client";
import { useState, useEffect } from "react";
import { Activity, TrendingUp, TrendingDown, Minus, AlertTriangle, CheckCircle, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioHealthSummary } from "@/lib/api";
import PortfolioHealthMonitor from "@/components/analysis/PortfolioHealthMonitor";
import HintTip from "@/components/common/HintTip";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function statusDot(status: string): string {
  if (status === "healthy") return "bg-ag-green";
  if (status === "warning") return "bg-yellow-400";
  if (status === "critical") return "bg-ag-red";
  return "bg-ag-text-muted";
}

function statusLabel(status: string): string {
  if (status === "healthy") return "Healthy";
  if (status === "warning") return "Warning";
  if (status === "critical") return "Critical";
  return "Unknown";
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

function typeLabel(t: string): string {
  if (t === "saved") return "Saved";
  if (t === "paper") return "Paper";
  if (t === "real") return "Real";
  return t;
}

function typeBadge(t: string): string {
  if (t === "real") return "bg-ag-accent/15 text-ag-accent border-ag-accent/30";
  if (t === "paper") return "bg-ag-green/15 text-ag-green border-ag-green/30";
  return "bg-ag-dark border-ag-border text-ag-text-muted";
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function HealthDashboard() {
  const [portfolios, setPortfolios] = useState<PortfolioHealthSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [filters, setFilters] = useState({
    saved: true,
    paper: true,
    real: true,
  });

  const fetchOverview = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.portfolioHealthOverview({
        include_saved: filters.saved,
        include_paper: filters.paper,
        include_real: filters.real,
      });
      setPortfolios(result.portfolios);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load health overview");
      setPortfolios([]);
    }
    setLoading(false);
  };

  useEffect(() => { fetchOverview(); }, []);

  // Summary counts
  const healthy = portfolios.filter(p => p.overall_status === "healthy").length;
  const warning = portfolios.filter(p => p.overall_status === "warning").length;
  const critical = portfolios.filter(p => p.overall_status === "critical").length;

  return (
    <div className="space-y-4">
      {/* Header with summary */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-xs">
            <label className="flex items-center gap-1 cursor-pointer">
              <input type="checkbox" checked={filters.saved}
                onChange={(e) => setFilters(f => ({ ...f, saved: e.target.checked }))}
                className="accent-ag-accent" />
              <span className="text-ag-text-muted">Saved</span>
            </label>
            <label className="flex items-center gap-1 cursor-pointer">
              <input type="checkbox" checked={filters.paper}
                onChange={(e) => setFilters(f => ({ ...f, paper: e.target.checked }))}
                className="accent-ag-accent" />
              <span className="text-ag-text-muted">Paper</span>
            </label>
            <label className="flex items-center gap-1 cursor-pointer">
              <input type="checkbox" checked={filters.real}
                onChange={(e) => setFilters(f => ({ ...f, real: e.target.checked }))}
                className="accent-ag-accent" />
              <span className="text-ag-text-muted">Real</span>
            </label>
            <button onClick={fetchOverview} disabled={loading}
              className="ml-2 px-3 py-1 bg-ag-accent/20 text-ag-accent rounded text-xs hover:bg-ag-accent/30 disabled:opacity-50">
              {loading ? "Loading..." : "Refresh All"}
            </button>
          </div>
        </div>

        {/* Status summary badges */}
        {portfolios.length > 0 && (
          <div className="flex items-center gap-3 text-xs">
            <span className="flex items-center gap-1">
              <CheckCircle size={12} className="text-ag-green" />
              <span className="text-ag-green font-medium">{healthy}</span>
            </span>
            <span className="flex items-center gap-1">
              <AlertTriangle size={12} className="text-yellow-400" />
              <span className="text-yellow-400 font-medium">{warning}</span>
            </span>
            <span className="flex items-center gap-1">
              <XCircle size={12} className="text-ag-red" />
              <span className="text-ag-red font-medium">{critical}</span>
            </span>
            <span className="text-ag-text-muted">{portfolios.length} total</span>
          </div>
        )}
      </div>

      {error && <p className="text-xs text-ag-red">{error}</p>}

      {loading && portfolios.length === 0 && (
        <p className="text-xs text-ag-text-muted py-4">Analyzing all portfolios...</p>
      )}

      {/* Portfolio cards */}
      {portfolios.length > 0 && (
        <div className="space-y-2">
          {portfolios.map((p) => (
            <div key={p.portfolio_id} className="bg-ag-card border border-ag-border rounded-lg overflow-hidden">
              {/* Summary row */}
              <button
                onClick={() => setExpanded(expanded === p.portfolio_id ? null : p.portfolio_id)}
                className="w-full px-4 py-3 flex items-center gap-3 hover:bg-ag-dark/30 transition-colors text-left"
              >
                {/* Status dot */}
                <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${statusDot(p.overall_status)}`}
                  title={statusLabel(p.overall_status)} />

                {/* Name + type */}
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="text-sm font-medium text-ag-text truncate">{p.name}</span>
                  <span className={`px-1.5 py-0 rounded text-[10px] border ${typeBadge(p.portfolio_type)}`}>
                    {typeLabel(p.portfolio_type)}
                  </span>
                </div>

                {/* Grade */}
                {p.overall_grade && (
                  <span className={`px-2 py-0.5 rounded text-xs font-bold border flex-shrink-0 ${gradeColor(p.overall_grade)}`}>
                    {p.overall_grade}
                  </span>
                )}

                {/* Trend */}
                <span className="flex items-center gap-1 flex-shrink-0" title={p.trend_detail}>
                  {p.trend === "improving" && <TrendingUp size={12} className="text-ag-green" />}
                  {p.trend === "declining" && <TrendingDown size={12} className="text-ag-red" />}
                  {p.trend === "stable" && <Minus size={12} className="text-ag-text-muted" />}
                </span>

                {/* Key metrics: PF & WR per period */}
                <div className="flex items-center gap-3 text-[10px] flex-shrink-0">
                  <div className="text-center" title="Last 7 days">
                    <p className="text-[8px] text-ag-text-muted">7d</p>
                    <p className={`font-mono font-bold ${pfColor(p.pf_7d)}`}>{p.pf_7d.toFixed(2)}</p>
                    <p className="font-mono text-ag-text-muted">{p.wr_7d.toFixed(0)}%</p>
                  </div>
                  <div className="text-center" title="Last 30 days">
                    <p className="text-[8px] text-ag-text-muted">30d</p>
                    <p className={`font-mono font-bold ${pfColor(p.pf_30d)}`}>{p.pf_30d.toFixed(2)}</p>
                    <p className="font-mono text-ag-text-muted">{p.wr_30d.toFixed(0)}%</p>
                  </div>
                  <div className="text-center" title="Last 90 days">
                    <p className="text-[8px] text-ag-text-muted">90d</p>
                    <p className={`font-mono font-bold ${pfColor(p.pf_90d)}`}>{p.pf_90d.toFixed(2)}</p>
                    <p className="font-mono text-ag-text-muted">{p.wr_90d.toFixed(0)}%</p>
                  </div>
                  <div className="text-center border-l border-ag-border/30 pl-3" title="Full period">
                    <p className="text-[8px] text-ag-text-muted">Full</p>
                    <p className={`font-mono font-bold ${pfColor(p.full_pf)}`}>{p.full_pf.toFixed(2)}</p>
                  </div>
                  <span className="text-ag-text-muted" title="Assets">{p.total_assets}A</span>
                  <span className="text-ag-text-muted" title="OOS trades">{p.oos_trades}T</span>
                </div>

                {/* Asset health mini */}
                <div className="flex items-center gap-1 flex-shrink-0">
                  {p.healthy_assets > 0 && <span className="w-1.5 h-1.5 rounded-full bg-ag-green" title={`${p.healthy_assets} healthy`} />}
                  {p.warning_assets > 0 && <span className="w-1.5 h-1.5 rounded-full bg-yellow-400" title={`${p.warning_assets} warning`} />}
                  {p.critical_assets > 0 && <span className="w-1.5 h-1.5 rounded-full bg-ag-red" title={`${p.critical_assets} critical`} />}
                </div>
              </button>

              {/* Expanded detail */}
              {expanded === p.portfolio_id && (
                <div className="border-t border-ag-border px-4 py-3">
                  <PortfolioHealthMonitor portfolioId={p.portfolio_id} portfolioType={p.portfolio_type} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {!loading && portfolios.length === 0 && !error && (
        <p className="text-xs text-ag-text-muted py-4">No portfolios found matching the selected filters.</p>
      )}
    </div>
  );
}
