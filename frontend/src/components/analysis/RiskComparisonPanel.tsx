"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { PortfolioDetail } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import { Plus, Trash2, Loader2 } from "lucide-react";

type RiskMode = "fixed" | "pct" | "martingale" | "anti_martingale" | "kelly";

interface ProfileConfig {
  id: number;
  name: string;
  mode: RiskMode;
  risk_value: number;
  initial_capital: number;
  multiplier: number;
  max_streak: number;
  kelly_fraction: number;
}

interface ProfileResult {
  name: string;
  mode: string;
  metrics: Record<string, number>;
  equity_curve: number[];
}

interface ComparisonResult {
  profiles: ProfileResult[];
  batch_risk_amount: number;
  is_normalized: boolean;
  trades_used: number;
}

const COLORS = ["#22c55e", "#3b82f6", "#f59e0b", "#ef4444", "#a855f7", "#06b6d4"];
const MODE_LABELS: Record<RiskMode, string> = {
  fixed: "Fixed $",
  pct: "% of Equity",
  martingale: "Martingale",
  anti_martingale: "Anti-Martingale",
  kelly: "Kelly Criterion",
};

let nextId = 1;
function makeProfile(): ProfileConfig {
  return {
    id: nextId++,
    name: "",
    mode: "fixed",
    risk_value: 250,
    initial_capital: 10000,
    multiplier: 2,
    max_streak: 3,
    kelly_fraction: 1.0,
  };
}

interface Props {
  portfolio: PortfolioDetail;
}

export default function RiskComparisonPanel({ portfolio }: Props) {
  const [profiles, setProfiles] = useState<ProfileConfig[]>([makeProfile()]);
  const [oosOnly, setOosOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [error, setError] = useState("");

  const addProfile = () => {
    if (profiles.length >= 6) return;
    setProfiles([...profiles, makeProfile()]);
  };

  const removeProfile = (id: number) => {
    setProfiles(profiles.filter((p) => p.id !== id));
  };

  const updateProfile = (id: number, patch: Partial<ProfileConfig>) => {
    setProfiles(profiles.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  };

  const runComparison = async () => {
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await api.riskComparison(portfolio.id, {
        profiles: profiles.map((p, i) => ({
          name: p.name || `${MODE_LABELS[p.mode]} ${p.mode === "pct" ? `${p.risk_value}%` : `$${p.risk_value}`}`,
          mode: p.mode,
          risk_value: p.risk_value,
          initial_capital: p.initial_capital,
          multiplier: p.multiplier,
          max_streak: p.max_streak,
          kelly_fraction: p.kelly_fraction,
        })),
        oos_only: oosOnly,
      });
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to run comparison");
    }
    setLoading(false);
  };

  return (
    <div className="space-y-4">
      <div className="text-xs text-ag-text-muted">
        Compare different risk profiles on your portfolio&apos;s trades.
        Uses instant math scaling — no engine re-run needed.
      </div>

      {/* Profile configs */}
      <div className="space-y-3">
        {profiles.map((p, i) => (
          <ProfileRow
            key={p.id}
            profile={p}
            index={i}
            onChange={(patch) => updateProfile(p.id, patch)}
            onRemove={profiles.length > 1 ? () => removeProfile(p.id) : undefined}
          />
        ))}
      </div>

      <div className="flex items-center gap-3">
        {profiles.length < 6 && (
          <button onClick={addProfile}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-ag-border rounded-lg hover:border-ag-accent transition-colors">
            <Plus size={14} /> Add Profile
          </button>
        )}

        {portfolio.optimization_end_date && (
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={oosOnly} onChange={(e) => setOosOnly(e.target.checked)}
              className="rounded border-ag-border bg-ag-dark accent-ag-accent" />
            <span className="text-ag-text-muted">OOS Only</span>
            <HintTip text="Only use Out-of-Sample trades (after the optimization end date)." />
          </label>
        )}

        <button onClick={runComparison} disabled={loading || profiles.length === 0}
          className="flex items-center gap-1.5 px-4 py-1.5 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50 transition-colors">
          {loading ? <><Loader2 size={14} className="animate-spin" /> Comparing...</> : "Compare"}
        </button>
      </div>

      {error && <p className="text-xs text-ag-red">{error}</p>}

      {result && <ComparisonResults result={result} />}
    </div>
  );
}


function ProfileRow({ profile, index, onChange, onRemove }: {
  profile: ProfileConfig;
  index: number;
  onChange: (patch: Partial<ProfileConfig>) => void;
  onRemove?: () => void;
}) {
  const color = COLORS[index % COLORS.length];
  const showMartFields = profile.mode === "martingale" || profile.mode === "anti_martingale";
  const showKellyFields = profile.mode === "kelly";
  const showRiskValue = profile.mode !== "kelly";

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-2">
        <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ background: color }} />
        <div className="grid grid-cols-2 sm:grid-cols-6 gap-2 flex-1">
          <div>
            <label className="block text-[10px] text-ag-text-muted mb-0.5">Mode</label>
            <select value={profile.mode} onChange={(e) => onChange({ mode: e.target.value as RiskMode })}
              className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
              <option value="fixed">Fixed $</option>
              <option value="pct">% of Equity</option>
              <option value="martingale">Martingale</option>
              <option value="anti_martingale">Anti-Martingale</option>
              <option value="kelly">Kelly Criterion</option>
            </select>
          </div>
          {showRiskValue && (
            <div>
              <label className="block text-[10px] text-ag-text-muted mb-0.5">
                {profile.mode === "pct" ? "Risk %" : "Risk $"}
              </label>
              <input type="number" value={profile.risk_value} min={0}
                step={profile.mode === "pct" ? 0.5 : 50}
                onChange={(e) => onChange({ risk_value: Number(e.target.value) })}
                className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
            </div>
          )}
          <div>
            <label className="block text-[10px] text-ag-text-muted mb-0.5">Capital</label>
            <input type="number" value={profile.initial_capital} min={100}
              onChange={(e) => onChange({ initial_capital: Number(e.target.value) })}
              className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
          </div>
          {showMartFields && (
            <>
              <div>
                <label className="block text-[10px] text-ag-text-muted mb-0.5">Multiplier</label>
                <input type="number" value={profile.multiplier} min={1} step={0.5}
                  onChange={(e) => onChange({ multiplier: Number(e.target.value) })}
                  className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
              </div>
              <div>
                <label className="block text-[10px] text-ag-text-muted mb-0.5">Max Streak</label>
                <input type="number" value={profile.max_streak} min={1} max={10}
                  onChange={(e) => onChange({ max_streak: Number(e.target.value) })}
                  className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
              </div>
            </>
          )}
          {showKellyFields && (
            <div>
              <label className="block text-[10px] text-ag-text-muted mb-0.5">Kelly Fraction</label>
              <div className="flex gap-1">
                {[1, 0.5, 0.25].map((f) => (
                  <button key={f} onClick={() => onChange({ kelly_fraction: f })}
                    className={`px-1.5 py-0.5 text-[10px] rounded border ${
                      profile.kelly_fraction === f
                        ? "bg-ag-accent text-white border-ag-accent"
                        : "border-ag-border hover:border-ag-accent"
                    }`}>
                    {f === 1 ? "Full" : f === 0.5 ? "Half" : "Quarter"}
                  </button>
                ))}
                <input type="number" value={profile.kelly_fraction} min={0.01} max={2} step={0.05}
                  onChange={(e) => onChange({ kelly_fraction: Number(e.target.value) })}
                  className="w-14 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-[10px]" />
              </div>
            </div>
          )}
          <div>
            <label className="block text-[10px] text-ag-text-muted mb-0.5">Label</label>
            <input value={profile.name} placeholder="Auto"
              onChange={(e) => onChange({ name: e.target.value })}
              className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
          </div>
        </div>
        {onRemove && (
          <button onClick={onRemove} className="text-ag-text-muted hover:text-ag-red transition-colors p-1">
            <Trash2 size={14} />
          </button>
        )}
      </div>
    </div>
  );
}


function ComparisonResults({ result }: { result: ComparisonResult }) {
  const { profiles } = result;
  if (profiles.length === 0) return null;

  return (
    <div className="space-y-4">
      {/* Header info */}
      <div className="flex gap-4 text-xs text-ag-text-muted">
        <span>Trades used: {result.trades_used}</span>
        <span>Batch risk: ${result.batch_risk_amount}</span>
        {result.is_normalized && (
          <span className="text-blue-400">Normalized</span>
        )}
        {!result.is_normalized && (
          <span className="text-yellow-400">Non-normalized (results approximate)</span>
        )}
      </div>

      {/* Overlaid equity curves */}
      <OverlaidEquityCurves profiles={profiles} />

      {/* KPI comparison table */}
      <KpiTable profiles={profiles} />
    </div>
  );
}


function OverlaidEquityCurves({ profiles }: { profiles: ProfileResult[] }) {
  // Find global min/max across all curves
  let globalMin = Infinity;
  let globalMax = -Infinity;
  let maxLen = 0;
  for (const p of profiles) {
    for (const v of p.equity_curve) {
      if (v < globalMin) globalMin = v;
      if (v > globalMax) globalMax = v;
    }
    if (p.equity_curve.length > maxLen) maxLen = p.equity_curve.length;
  }
  const range = globalMax - globalMin || 1;
  const h = 160;
  const w = 700;

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        Equity Curves Overlay
      </h4>
      <div className="bg-ag-dark/50 rounded p-3 overflow-x-auto">
        <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ maxHeight: "200px" }}>
          {/* Zero / initial capital line */}
          {profiles[0] && (
            <line
              x1="0"
              y1={h - ((profiles[0].equity_curve[0] - globalMin) / range) * (h - 10) - 5}
              x2={w}
              y2={h - ((profiles[0].equity_curve[0] - globalMin) / range) * (h - 10) - 5}
              stroke="#555" strokeWidth="0.5" strokeDasharray="4 2"
            />
          )}
          {profiles.map((p, pi) => {
            const curve = p.equity_curve;
            const path = curve.map((v, i) => {
              const x = maxLen > 1 ? (i / (maxLen - 1)) * w : 0;
              const y = h - ((v - globalMin) / range) * (h - 10) - 5;
              return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
            }).join(" ");
            return (
              <path key={pi} d={path} fill="none"
                stroke={COLORS[pi % COLORS.length]} strokeWidth="1.5" opacity={0.85} />
            );
          })}
        </svg>
        {/* Legend */}
        <div className="flex flex-wrap gap-3 mt-2">
          {profiles.map((p, i) => (
            <div key={i} className="flex items-center gap-1.5 text-[10px]">
              <div className="w-3 h-0.5 rounded" style={{ background: COLORS[i % COLORS.length] }} />
              <span>{p.name}</span>
              <span className={p.metrics.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}>
                ${p.metrics.net_profit?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? "—"}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}


const KPI_ROWS: { key: string; label: string; format: (v: number) => string; bestIs?: "max" | "min" }[] = [
  { key: "net_profit", label: "Net Profit", format: (v) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, bestIs: "max" },
  { key: "roi_pct", label: "ROI %", format: (v) => `${v.toFixed(1)}%`, bestIs: "max" },
  { key: "profit_factor", label: "Profit Factor", format: (v) => v.toFixed(2), bestIs: "max" },
  { key: "win_rate", label: "Win Rate", format: (v) => `${v.toFixed(1)}%`, bestIs: "max" },
  { key: "max_dd_dollar", label: "Max DD $", format: (v) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, bestIs: "max" },
  { key: "max_dd_pct", label: "Max DD %", format: (v) => `${v.toFixed(1)}%`, bestIs: "max" },
  { key: "recovery_factor", label: "Recovery Factor", format: (v) => v.toFixed(2), bestIs: "max" },
  { key: "sharpe", label: "Sharpe", format: (v) => v.toFixed(2), bestIs: "max" },
  { key: "sortino", label: "Sortino", format: (v) => v.toFixed(2), bestIs: "max" },
  { key: "calmar", label: "Calmar", format: (v) => v.toFixed(2), bestIs: "max" },
  { key: "stability", label: "Stability", format: (v) => v.toFixed(4), bestIs: "max" },
  { key: "avg_trade", label: "Avg Trade", format: (v) => `$${v.toFixed(2)}`, bestIs: "max" },
  { key: "expectancy", label: "Expectancy", format: (v) => `$${v.toFixed(2)}`, bestIs: "max" },
  { key: "largest_win", label: "Largest Win", format: (v) => `$${v.toFixed(0)}`, bestIs: "max" },
  { key: "largest_loss", label: "Largest Loss", format: (v) => `$${v.toFixed(0)}`, bestIs: "max" },
  { key: "total_trades", label: "Total Trades", format: (v) => String(v) },
  { key: "commission_total", label: "Commission", format: (v) => `$${v.toFixed(0)}` },
  { key: "kelly_pct", label: "Kelly %", format: (v) => `${v.toFixed(1)}%` },
];

function KpiTable({ profiles }: { profiles: ProfileResult[] }) {
  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        KPI Comparison
      </h4>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-ag-border">
              <th className="text-left py-1.5 px-2 text-ag-text-muted">Metric</th>
              {profiles.map((p, i) => (
                <th key={i} className="text-right py-1.5 px-2" style={{ color: COLORS[i % COLORS.length] }}>
                  {p.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {KPI_ROWS.map((row) => {
              const values = profiles.map((p) => p.metrics[row.key] ?? 0);
              const bestIdx = row.bestIs
                ? values.indexOf(row.bestIs === "max" ? Math.max(...values) : Math.min(...values))
                : -1;

              return (
                <tr key={row.key} className="border-b border-ag-border/30 hover:bg-ag-card-hover">
                  <td className="py-1 px-2 text-ag-text-muted">{row.label}</td>
                  {profiles.map((p, i) => {
                    const val = p.metrics[row.key];
                    const isBest = i === bestIdx && profiles.length > 1;
                    return (
                      <td key={i} className={`text-right py-1 px-2 font-mono ${isBest ? "text-ag-green font-semibold" : ""}`}>
                        {val != null ? row.format(val) : "—"}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
