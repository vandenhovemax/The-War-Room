"use client";
import { useState } from "react";
import { ChevronDown, ChevronRight, Shield, Link2, Zap, Copy, AlertTriangle, Info } from "lucide-react";
import type { AffinityReportResult, AffinityPairMetrics, AffinityBatchRole } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

// ── Metric descriptions ────────────────────────────────────────────────
const METRIC_INFO: Record<string, { label: string; tip: string }> = {
  return_correlation: { label: "Return Corr", tip: "Daily PnL correlation. High score = low correlation = good diversification." },
  correlation_stability: { label: "Corr Stability", tip: "How stable is the correlation over time? High = predictable relationship, most reliable predictor of OOS behavior." },
  drawdown_overlap: { label: "DD Overlap", tip: "Fraction of drawdown days that don't overlap. High = they drawdown at different times = portfolio survives." },
  stress_divergence: { label: "Stress Diverge", tip: "Correlation during worst 20% of days. High = they diverge under stress = insurance." },
  complementarity: { label: "Complement", tip: "Fraction of days one is up while other is down. High = they take turns carrying the portfolio." },
  recovery_offset: { label: "Recovery Offset", tip: "How offset are drawdown troughs? High = they recover at different times = faster portfolio recovery." },
  shape_diversity: { label: "Shape Diversity", tip: "Equity curve shape difference (vol, Sharpe distribution, win/loss ratio). High = different profiles." },
};

const ROLE_CONFIG: Record<string, { icon: typeof Shield; color: string; label: string; bg: string }> = {
  anchor: { icon: Shield, color: "text-green-400", label: "Anchor", bg: "bg-green-500/15" },
  strong_complement: { icon: Link2, color: "text-blue-400", label: "Strong Complement", bg: "bg-blue-500/15" },
  conditional: { icon: Zap, color: "text-yellow-400", label: "Conditional", bg: "bg-yellow-500/15" },
  redundant: { icon: Copy, color: "text-orange-400", label: "Redundant", bg: "bg-orange-500/15" },
  outlier: { icon: AlertTriangle, color: "text-red-400", label: "Outlier", bg: "bg-red-500/15" },
};

function scoreColor(v: number): string {
  if (v >= 0.7) return "text-green-400";
  if (v >= 0.5) return "text-yellow-400";
  if (v >= 0.35) return "text-orange-400";
  return "text-red-400";
}

function scoreBg(v: number): string {
  if (v >= 0.7) return "bg-green-500/15";
  if (v >= 0.5) return "bg-yellow-500/15";
  if (v >= 0.35) return "bg-orange-500/15";
  return "bg-red-500/15";
}

function statusColor(s: string): string {
  if (s === "certified") return "text-green-400";
  if (s === "improved") return "text-blue-400";
  if (s === "degraded") return "text-orange-400";
  if (s === "spurious") return "text-red-400";
  return "text-ag-text-muted";
}

function bn(id: number | string, names: Record<string, string>): string {
  return names[String(id)] || `Batch ${id}`;
}

// ── Pairwise Matrix ────────────────────────────────────────────────────

function PairwiseMatrix({ report }: { report: AffinityReportResult }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const ids = report.batch_ids;
  const pairMap: Record<string, AffinityPairMetrics> = {};
  for (const p of report.pairs) {
    pairMap[`${p.id_a}-${p.id_b}`] = p;
    pairMap[`${p.id_b}-${p.id_a}`] = p;
  }

  return (
    <div className="overflow-x-auto">
      <table className="text-[10px] border-collapse">
        <thead>
          <tr>
            <th className="p-1" />
            {ids.map((id) => (
              <th key={id} className="p-1 text-ag-text-muted font-normal truncate max-w-[80px]" title={bn(id, report.batch_names)}>
                {bn(id, report.batch_names).slice(0, 12)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ids.map((rowId) => (
            <tr key={rowId}>
              <td className="p-1 text-ag-text-muted font-medium truncate max-w-[80px]" title={bn(rowId, report.batch_names)}>
                {bn(rowId, report.batch_names).slice(0, 12)}
              </td>
              {ids.map((colId) => {
                if (rowId === colId) return <td key={colId} className="p-1 bg-ag-dark/30 text-center">—</td>;
                const p = pairMap[`${rowId}-${colId}`];
                if (!p) return <td key={colId} className="p-1">—</td>;
                const key = `${p.id_a}-${p.id_b}`;
                return (
                  <td key={colId}
                    className={`p-1 text-center cursor-pointer hover:ring-1 hover:ring-ag-accent/50 rounded ${scoreBg(p.composite_score)}`}
                    onClick={() => setExpanded(expanded === key ? null : key)}
                    title={`Composite: ${p.composite_score.toFixed(3)}`}
                  >
                    <span className={`font-bold ${scoreColor(p.composite_score)}`}>
                      {p.composite_score.toFixed(2)}
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {expanded && pairMap[expanded] && (() => {
        const p = pairMap[expanded];
        return (
          <div className="mt-2 bg-ag-dark/50 border border-ag-border rounded p-2 text-xs">
            <span className="font-medium">{bn(p.id_a, report.batch_names)} × {bn(p.id_b, report.batch_names)}</span>
            <div className="grid grid-cols-4 gap-x-4 gap-y-1 mt-1">
              {Object.entries(METRIC_INFO).map(([key, info]) => (
                <div key={key}>
                  <span className="text-ag-text-muted">{info.label}:</span>{" "}
                  <span className={scoreColor((p as unknown as Record<string, number>)[key] ?? 0)}>
                    {((p as unknown as Record<string, number>)[key] ?? 0).toFixed(3)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

// ── Batch Scorecards ───────────────────────────────────────────────────

function BatchScorecard({ role, names }: { role: AffinityBatchRole; names: Record<string, string> }) {
  const cfg = ROLE_CONFIG[role.role] || ROLE_CONFIG.conditional;
  const Icon = cfg.icon;
  return (
    <div className={`flex items-start gap-2 p-2 rounded border border-ag-border/50 ${cfg.bg}`}>
      <Icon size={14} className={`mt-0.5 ${cfg.color}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium truncate">{bn(role.batch_id, names)}</span>
          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${cfg.bg} ${cfg.color}`}>{cfg.label}</span>
          {role.batch_grade && (
            <span className="text-[10px] px-1 py-0.5 rounded bg-ag-dark/50 text-ag-text-muted">{role.batch_grade}</span>
          )}
          <span className={`text-[10px] font-bold ${scoreColor(role.combined_score)}`}>
            Combined: {role.combined_score.toFixed(3)}
          </span>
        </div>
        <div className="text-[10px] text-ag-text-muted mt-0.5 flex flex-wrap gap-x-3">
          <span>Affinity: <span className={scoreColor(role.team_player_score)}>{role.team_player_score.toFixed(3)}</span></span>
          <span>Quality: <span className={scoreColor(role.quality_score)}>{role.quality_score.toFixed(3)}</span></span>
          {role.avg_profit_factor > 0 && <span>PF: <span className={role.avg_profit_factor >= 1.5 ? "text-green-400" : role.avg_profit_factor >= 1 ? "text-yellow-400" : "text-red-400"}>{role.avg_profit_factor.toFixed(2)}</span></span>}
          {role.avg_win_rate > 0 && <span>WR: {role.avg_win_rate.toFixed(1)}%</span>}
        </div>
        <div className="text-[10px] text-ag-text-muted mt-0.5 flex flex-wrap gap-x-3">
          {role.best_partner != null && (
            <span>Best: <span className="text-green-400">{bn(role.best_partner, names)}</span> ({role.best_partner_score.toFixed(2)})</span>
          )}
          {role.worst_partner != null && (
            <span>Worst: <span className="text-red-400">{bn(role.worst_partner, names)}</span> ({role.worst_partner_score.toFixed(2)})</span>
          )}
          {role.redundant_with != null && (
            <span className="text-orange-400">Redundant with: {bn(role.redundant_with, names)}</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Cluster view ───────────────────────────────────────────────────────

function ClusterView({ report }: { report: AffinityReportResult }) {
  return (
    <div className="space-y-1.5">
      {report.clusters.map((c) => (
        <div key={c.cluster_id} className="flex items-center gap-2 text-xs">
          <span className="text-ag-text-muted font-medium">Cluster {c.cluster_id + 1}:</span>
          <span>{c.batch_ids.map((id) => bn(id, report.batch_names)).join(", ")}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${scoreBg(c.avg_internal_harmony)} ${scoreColor(c.avg_internal_harmony)}`}>
            {c.avg_internal_harmony.toFixed(3)}
          </span>
        </div>
      ))}
      {report.clusters.length > 1 && (() => {
        // Cross-cluster harmony
        const clusterSets = report.clusters.map((c) => new Set(c.batch_ids.map(String)));
        const crossScores: number[] = [];
        for (const p of report.pairs) {
          const aCluster = clusterSets.findIndex((s) => s.has(String(p.id_a)));
          const bCluster = clusterSets.findIndex((s) => s.has(String(p.id_b)));
          if (aCluster !== bCluster) crossScores.push(p.composite_score);
        }
        const avg = crossScores.length ? crossScores.reduce((a, b) => a + b, 0) / crossScores.length : 0;
        return (
          <div className="text-[10px] text-ag-text-muted mt-1">
            Cross-cluster harmony: <span className={scoreColor(avg)}>{avg.toFixed(3)}</span>
          </div>
        );
      })()}
    </div>
  );
}

// ── Per-metric leaderboard ─────────────────────────────────────────────

function MetricLeaderboard({ report }: { report: AffinityReportResult }) {
  const [selectedMetric, setSelectedMetric] = useState("drawdown_overlap");
  const entries = report.metric_leaderboard[selectedMetric] || [];

  return (
    <div className="space-y-2">
      <div className="flex gap-1 flex-wrap">
        {Object.entries(METRIC_INFO).map(([key, info]) => (
          <button key={key} onClick={() => setSelectedMetric(key)}
            className={`px-2 py-0.5 rounded text-[10px] transition-colors ${selectedMetric === key ? "bg-ag-accent/20 text-ag-accent" : "text-ag-text-muted hover:text-ag-text"}`}>
            {info.label}
          </button>
        ))}
      </div>
      <div className="space-y-0.5">
        {entries.slice(0, 10).map((e, i) => (
          <div key={i} className="flex items-center gap-2 text-[10px]">
            <span className="text-ag-text-muted w-4">{i + 1}.</span>
            <span className="flex-1">{bn(e.pair[0], report.batch_names)} × {bn(e.pair[1], report.batch_names)}</span>
            <span className={`font-medium ${scoreColor(e.score)}`}>{e.score.toFixed(3)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── OOS Certification ──────────────────────────────────────────────────

function OOSCertification({ report }: { report: AffinityReportResult }) {
  const [groupByMetric, setGroupByMetric] = useState(true);

  if (!report.oos_validations) return null;

  // Group by metric
  const byMetric: Record<string, typeof report.oos_validations> = {};
  for (const v of report.oos_validations) {
    if (!byMetric[v.metric]) byMetric[v.metric] = [];
    byMetric[v.metric]!.push(v);
  }

  return (
    <div className="space-y-2">
      {/* Metric reliability summary */}
      {report.metric_reliability && (
        <div className="grid grid-cols-4 gap-x-4 gap-y-1 text-[10px]">
          <span className="text-ag-text-muted font-medium col-span-4">Metric Predictiveness (IS→OOS correlation):</span>
          {Object.entries(report.metric_reliability)
            .sort(([, a], [, b]) => b - a)
            .map(([metric, rel]) => (
              <div key={metric} className="flex items-center gap-1">
                <span className="text-ag-text-muted">{METRIC_INFO[metric]?.label || metric}:</span>
                <span className={scoreColor(rel)}>{(rel * 100).toFixed(0)}%</span>
                {rel >= 0.7 && <span className="text-green-400/60">★</span>}
              </div>
            ))}
        </div>
      )}

      {/* Validations by metric */}
      <div className="flex gap-1 mb-1">
        <button onClick={() => setGroupByMetric(true)} className={`text-[10px] px-2 py-0.5 rounded ${groupByMetric ? "bg-ag-accent/20 text-ag-accent" : "text-ag-text-muted"}`}>By Metric</button>
        <button onClick={() => setGroupByMetric(false)} className={`text-[10px] px-2 py-0.5 rounded ${!groupByMetric ? "bg-ag-accent/20 text-ag-accent" : "text-ag-text-muted"}`}>By Pair</button>
      </div>

      {groupByMetric ? (
        <div className="space-y-2 max-h-48 overflow-y-auto">
          {Object.entries(byMetric).map(([metric, vals]) => (
            <div key={metric}>
              <span className="text-[10px] text-ag-accent font-medium">{METRIC_INFO[metric]?.label || metric}</span>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 ml-2 text-[10px]">
                {vals!.map((v, i) => (
                  <div key={i} className="flex items-center gap-1">
                    <span className="text-ag-text-muted truncate">{bn(v.id_a, report.batch_names).slice(0, 8)}×{bn(v.id_b, report.batch_names).slice(0, 8)}</span>
                    <span className="text-ag-text-muted">{v.is_value.toFixed(2)}→{v.oos_value.toFixed(2)}</span>
                    <span className={`font-medium ${statusColor(v.status)}`}>{v.status}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-1 max-h-48 overflow-y-auto text-[10px]">
          {report.pairs.map((p) => {
            const pairVals = report.oos_validations!.filter(
              (v) => (v.id_a === p.id_a && v.id_b === p.id_b) || (v.id_a === p.id_b && v.id_b === p.id_a)
            );
            const certified = pairVals.filter((v) => v.status === "certified").length;
            const degraded = pairVals.filter((v) => v.status === "degraded" || v.status === "spurious").length;
            return (
              <div key={`${p.id_a}-${p.id_b}`} className="flex items-center gap-2">
                <span className="font-medium">{bn(p.id_a, report.batch_names)} × {bn(p.id_b, report.batch_names)}</span>
                <span className="text-green-400">{certified} certified</span>
                {degraded > 0 && <span className="text-red-400">{degraded} degraded</span>}
              </div>
            );
          })}
        </div>
      )}

      {report.oos_window && (
        <p className="text-[10px] text-ag-text-muted italic">
          OOS window: {report.oos_window.start} → {report.oos_window.end}
          {report.oos_window.oos2_excluded_from && ` (OOS2 from ${report.oos_window.oos2_excluded_from} excluded)`}
        </p>
      )}
    </div>
  );
}

// ── Recommendation ─────────────────────────────────────────────────────

function Recommendation({ report }: { report: AffinityReportResult }) {
  const rec = report.recommendation;
  if (rec.error) return <p className="text-xs text-ag-text-muted">{rec.error}</p>;

  return (
    <div className="space-y-2 text-xs">
      {rec.anchor && (
        <div className="flex items-center gap-2">
          <Shield size={14} className="text-green-400" />
          <span>Anchor: <span className="font-medium text-green-400">{bn(rec.anchor.batch_id, report.batch_names)}</span></span>
          <span className={`text-[10px] ${scoreColor(rec.anchor.score)}`}>(combined: {rec.anchor.score.toFixed(3)})</span>
          {rec.anchor.affinity != null && <span className="text-[10px] text-ag-text-muted">affinity: {rec.anchor.affinity.toFixed(3)}</span>}
          {rec.anchor.quality != null && <span className="text-[10px] text-ag-text-muted">quality: {rec.anchor.quality.toFixed(3)}</span>}
        </div>
      )}

      {rec.keep && rec.keep.length > 0 && (
        <div>
          <span className="text-ag-text-muted">Keep:</span>{" "}
          <span className="text-green-400">{rec.keep.map((id) => bn(id, report.batch_names)).join(", ")}</span>
        </div>
      )}

      {rec.conditional && rec.conditional.length > 0 && (
        <div>
          <span className="text-ag-text-muted">Conditional (context-dependent):</span>{" "}
          <span className="text-yellow-400">{rec.conditional.map((id) => bn(id, report.batch_names)).join(", ")}</span>
        </div>
      )}

      {rec.drop && rec.drop.length > 0 && (
        <div>
          <span className="text-ag-text-muted">Consider dropping:</span>{" "}
          {rec.drop.map((id) => (
            <span key={String(id)} className="inline-flex items-center gap-1 mr-2">
              <span className="text-red-400">{bn(id, report.batch_names)}</span>
              {rec.drop_reasons?.[String(id)] && (
                <span className="text-[10px] text-ag-text-muted">({rec.drop_reasons[String(id)]})</span>
              )}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-4 text-[10px]">
        <span>Current avg harmony: <span className={scoreColor(rec.current_avg_harmony ?? 0)}>{(rec.current_avg_harmony ?? 0).toFixed(3)}</span></span>
        <span>Recommended: <span className={scoreColor(rec.recommended_avg_harmony ?? 0)}>{(rec.recommended_avg_harmony ?? 0).toFixed(3)}</span></span>
        {rec.improvement_pct != null && rec.improvement_pct > 0 && (
          <span className="text-green-400">+{rec.improvement_pct.toFixed(1)}%</span>
        )}
      </div>

      {rec.oos_warnings && rec.oos_warnings.length > 0 && (
        <div className="space-y-0.5 mt-1">
          {rec.oos_warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-1 text-[10px] text-orange-400">
              <AlertTriangle size={10} className="mt-0.5 shrink-0" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────

interface Props {
  report: AffinityReportResult;
}

export default function BatchAffinityReport({ report }: Props) {
  const [openSection, setOpenSection] = useState<string | null>("recommendation");

  const sections = [
    { id: "recommendation", label: "Next-Run Recommendation" },
    { id: "roles", label: "Batch Scorecards" },
    { id: "matrix", label: "Pairwise Affinity Matrix" },
    { id: "clusters", label: "Affinity Clusters" },
    { id: "leaderboard", label: "Per-Metric Leaderboard" },
    ...(report.oos_validations ? [{ id: "oos", label: "OOS Certification" }] : []),
  ];

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg overflow-hidden">
      <div className="px-3 py-2 border-b border-ag-border flex items-center gap-2">
        <span className="text-sm font-semibold">Batch Affinity Report</span>
        <HintTip text="Post-optimization analysis of how your batches work together. Shows pairwise metrics, batch roles (anchor/complement/outlier), natural clusters, and a recommended batch set for your next run." />
        <span className="text-[10px] text-ag-text-muted ml-auto">
          {report.batch_ids.length} batches · {report.pairs.length} pairs
          {report.oos_validations && " · OOS validated"}
        </span>
      </div>

      <div className="divide-y divide-ag-border/50">
        {sections.map((s) => (
          <div key={s.id}>
            <button
              onClick={() => setOpenSection(openSection === s.id ? null : s.id)}
              className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium hover:bg-ag-dark/30 transition-colors"
            >
              {openSection === s.id ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              {s.label}
            </button>
            {openSection === s.id && (
              <div className="px-3 pb-3">
                {s.id === "recommendation" && <Recommendation report={report} />}
                {s.id === "roles" && (
                  <div className="space-y-1.5">
                    {report.roles
                      .sort((a, b) => b.combined_score - a.combined_score)
                      .map((r) => <BatchScorecard key={String(r.batch_id)} role={r} names={report.batch_names} />)}
                  </div>
                )}
                {s.id === "matrix" && <PairwiseMatrix report={report} />}
                {s.id === "clusters" && <ClusterView report={report} />}
                {s.id === "leaderboard" && <MetricLeaderboard report={report} />}
                {s.id === "oos" && <OOSCertification report={report} />}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
