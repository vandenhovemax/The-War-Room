"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { OOSCompareResult, CandidateMetrics } from "@/lib/api";
import type { AnyCandidate } from "./OptimizerPanel";
import { isLSCandidate } from "./OptimizerPanel";
import CandidateDetailPanel from "./CandidateDetailPanel";

interface RiskProfile {
  mode: string;
  value: number;
  initialCapital?: number;
  multiplier?: number;
  maxStreak?: number;
  compound?: string;
  scope?: string;
  countMode?: string;
  oosReset?: boolean;
  startAfter?: number;
  resetAfter?: number;
  kellyN?: number;
  kellyWarmup?: number;
  autoAssetConfigs?: string;
}

interface PerBatchConfig {
  sizingMaster: string;
  batchConfigs: string;
}

interface Props {
  index: number;
  candidate: AnyCandidate;
  saving: boolean;
  onSave: () => void;
  saved?: boolean;
  dateTo?: string;
  dateFrom?: string;
  batchId: number;
  clipIS?: boolean;
  clipOOS?: boolean;
  oosEndDate?: string;
  /** Per-asset auto-risk mode map: symbol → mode string */
  autoRiskModes?: Record<string, string>;
  /** Active risk profile for OOS scaling */
  riskProfile?: RiskProfile;
  /** Per-batch risk config */
  perBatchConfig?: PerBatchConfig;
}

export default function CandidateCard({ index, candidate, saving, onSave, saved, dateTo, dateFrom, batchId, clipIS, clipOOS, oosEndDate, autoRiskModes, riskProfile, perBatchConfig }: Props) {
  const [oos, setOos] = useState<OOSCompareResult | null>(null);
  const [oosLoading, setOosLoading] = useState(false);
  const [oosError, setOosError] = useState("");
  const [showDetail, setShowDetail] = useState(false);
  const ls = isLSCandidate(candidate);

  // Mutable local state for asset removal
  const [localAssets, setLocalAssets] = useState(candidate.assets);
  const [localLongs, setLocalLongs] = useState(ls ? candidate.longs : []);
  const [localShorts, setLocalShorts] = useState(ls ? candidate.shorts : []);
  const [localMetrics, setLocalMetrics] = useState<CandidateMetrics>(candidate.metrics);
  const [localEquity, setLocalEquity] = useState(candidate.equity_curve);
  const [removing, setRemoving] = useState<string | null>(null);
  const m = localMetrics;
  // AC post-filter flags (if present)
  const acFlags = new Set((candidate as unknown as Record<string, unknown>).ac_flags as string[] ?? []);
  const acScores = ((candidate as unknown as Record<string, unknown>).ac_scores ?? {}) as Record<string, number>;
  // Split-half correlation stability (if present)
  const corrStability = (candidate as unknown as Record<string, unknown>).corr_stability as
    { stable: boolean; unstable_pairs: { pair: string[]; h1: number; h2: number }[] } | undefined;

  const removeAsset = async (asset: string) => {
    const newAssets = localAssets.filter((a) => a !== asset);
    if (newAssets.length < 2) return;
    setRemoving(asset);
    try {
      const result = await api.recalculateCandidate(batchId, newAssets, dateTo, dateFrom);
      setLocalAssets(result.assets);
      setLocalMetrics(result.metrics);
      setLocalEquity(result.equity_curve);
      setLocalLongs((prev) => prev.filter((a) => a !== asset));
      setLocalShorts((prev) => prev.filter((a) => a !== asset));
    } catch { /* keep previous state */ }
    setRemoving(null);
  };

  const revealOOS = async () => {
    if (!dateTo) return;
    setOosLoading(true);
    setOosError("");
    try {
      // Pass risk profile when: value > 0 (mart/anti/fixed), OR autoAssetConfigs present (auto-risk per asset)
      const hasRisk = riskProfile?.mode && (riskProfile.value > 0 || riskProfile.autoAssetConfigs);
      const result = await api.dashboardOOSCompare(
        dateTo, batchId, undefined, undefined, localAssets, undefined, undefined, undefined, clipIS, clipOOS, oosEndDate,
        undefined,
        hasRisk ? riskProfile : undefined,
        perBatchConfig,
      );
      setOos(result);
      // Update IS metrics with risk-scaled values from the OOS comparison.
      // is_quick_metrics uses lowercase keys (net_profit, profit_factor, …)
      // is_metrics uses Title Case keys ("Total Net Profit", "Profit Factor", …)
      const qm = result.is_quick_metrics;
      const fm = result.is_metrics;
      if (qm || fm) {
        const updatedMetrics: CandidateMetrics = { ...localMetrics };
        const n = (key: string, alt: string) => {
          const v = qm?.[key] ?? fm?.[alt];
          return typeof v === "number" ? v : undefined;
        };
        const np = n("net_profit", "Total Net Profit"); if (np !== undefined) updatedMetrics.net_profit = np;
        const pf = n("profit_factor", "Profit Factor"); if (pf !== undefined) updatedMetrics.profit_factor = pf;
        const dd = n("max_drawdown", "Max Drawdown ($)"); if (dd !== undefined) updatedMetrics.max_drawdown = dd;
        const wr = n("win_rate", "Win Rate"); if (wr !== undefined) updatedMetrics.win_rate = wr;
        const so = n("sortino", "Sortino"); if (so !== undefined) updatedMetrics.sortino = so;
        const tt = n("total_trades", "Total Trades"); if (tt !== undefined) updatedMetrics.total_trades = tt;
        const st = n("stability", "Stability (R²)"); if (st !== undefined) updatedMetrics.stability = st;
        setLocalMetrics(updatedMetrics);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "OOS analysis failed";
      console.error("Reveal OOS error:", e);
      setOosError(msg);
    }
    setOosLoading(false);
  };

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h4 className="text-sm font-medium">Portfolio #{index + 1}</h4>
          <p className="text-xs text-ag-text-muted">
            {localAssets.length} assets
            {ls && ` (${localLongs.length}L / ${localShorts.length}S)`}
            {localAssets.length < candidate.assets.length && (
              <span className="text-yellow-400 ml-1">({candidate.assets.length - localAssets.length} removed)</span>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowDetail(!showDetail)}
            className="px-3 py-1 bg-ag-accent/10 text-ag-accent rounded text-xs hover:bg-ag-accent/20">
            {showDetail ? "Hide" : "Details"}
          </button>
          {!oos && (
            <button onClick={revealOOS} disabled={oosLoading || !dateTo}
              className="px-3 py-1 bg-yellow-500/20 text-yellow-400 rounded text-xs hover:bg-yellow-500/30 disabled:opacity-50"
              title={!dateTo ? "Set OOS Cutoff Date above to enable" : "Show out-of-sample results"}>
              {oosLoading ? "Loading..." : "Reveal OOS"}
            </button>
          )}
          {saved ? (
            <span className="px-3 py-1 bg-ag-green/10 text-ag-green/60 rounded text-xs cursor-default">Saved</span>
          ) : (
            <button onClick={onSave} disabled={saving}
              className="px-3 py-1 bg-ag-green/20 text-ag-green rounded text-xs hover:bg-ag-green/30 disabled:opacity-50">
              {saving ? "Saving..." : "Save"}
            </button>
          )}
        </div>
      </div>

      {/* Asset chips with X remove — grouped by side if L/S split */}
      {ls ? (
        <div className="space-y-2 mb-3">
          <div>
            <span className="text-[10px] text-ag-green uppercase tracking-wider">Long</span>
            <div className="flex flex-wrap gap-1 mt-0.5">
              {localLongs.map((a) => {
                const sym = a.replace(/_Long$/, "").replace(/_Short$/, "");
                return (
                  <AssetChip key={a} asset={a} color="green" removing={removing === a}
                    onRemove={localAssets.length > 2 ? () => removeAsset(a) : undefined}
                    acFlagged={acFlags.has(a)} acScore={acScores[a]}
                    riskMode={autoRiskModes?.[sym] ?? autoRiskModes?.[a]} />
                );
              })}
            </div>
          </div>
          <div>
            <span className="text-[10px] text-ag-red uppercase tracking-wider">Short</span>
            <div className="flex flex-wrap gap-1 mt-0.5">
              {localShorts.map((a) => {
                const sym = a.replace(/_Long$/, "").replace(/_Short$/, "");
                return (
                  <AssetChip key={a} asset={a} color="red" removing={removing === a}
                    onRemove={localAssets.length > 2 ? () => removeAsset(a) : undefined}
                    acFlagged={acFlags.has(a)} acScore={acScores[a]}
                    riskMode={autoRiskModes?.[sym] ?? autoRiskModes?.[a]} />
                );
              })}
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {localAssets.map((a) => {
            // Strip direction suffix to look up auto-risk mode
            const sym = a.replace(/_Long$/, "").replace(/_Short$/, "");
            return (
              <AssetChip key={a} asset={a} color="accent" removing={removing === a}
                onRemove={localAssets.length > 2 ? () => removeAsset(a) : undefined}
                acFlagged={acFlags.has(a)} acScore={acScores[a]}
                riskMode={autoRiskModes?.[sym] ?? autoRiskModes?.[a]} />
            );
          })}
        </div>
      )}

      {/* Summary metrics — 2 rows of 4 */}
      <div className="space-y-1.5">
        <div className="grid grid-cols-4 gap-2 text-xs">
          <div>
            <span className="text-ag-text-muted">{oos ? "IS " : ""}Net Profit</span>
            <p className={m.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}>
              ${m.net_profit.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </p>
          </div>
          <div>
            <span className="text-ag-text-muted">{oos ? "IS " : ""}PF</span>
            <p>{m.profit_factor.toFixed(2)}</p>
          </div>
          <div>
            <span className="text-ag-text-muted">{oos ? "IS " : ""}Max DD</span>
            <p className="text-ag-red">${m.max_drawdown.toFixed(2)}</p>
          </div>
          <div>
            <span className="text-ag-text-muted">{oos ? "IS " : ""}Stability</span>
            <p>{(m.stability * 100).toFixed(1)}%</p>
          </div>
        </div>
        <div className="grid grid-cols-4 gap-2 text-xs">
          <div>
            <span className="text-ag-text-muted">Win Rate</span>
            <p>{(m.win_rate ?? 0).toFixed(1)}%</p>
          </div>
          <div>
            <span className="text-ag-text-muted">Positions</span>
            <p>{m.total_trades ?? 0}</p>
          </div>
          <div>
            <span className="text-ag-text-muted">Sortino</span>
            <p>{(m.sortino ?? 0).toFixed(2)}</p>
          </div>
          <div>
            <span className="text-ag-text-muted">Avg Corr</span>
            <p>{(m.avg_corr ?? 0).toFixed(3)}</p>
          </div>
        </div>
      </div>

      {/* Correlation stability warning */}
      {corrStability && !corrStability.stable && (
        <div className="bg-amber-500/10 border border-amber-500/20 rounded px-3 py-2 mt-2">
          <p className="text-[10px] font-medium text-amber-400 mb-1">
            Correlation Instability ({corrStability.unstable_pairs.length} pair{corrStability.unstable_pairs.length > 1 ? "s" : ""})
          </p>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5">
            {corrStability.unstable_pairs.slice(0, 5).map((p, i) => (
              <span key={i} className="text-[10px] text-amber-300/70">
                {p.pair[0].replace("USDT", "")}/{p.pair[1].replace("USDT", "")}:
                H1={p.h1.toFixed(2)} H2={p.h2.toFixed(2)}
              </span>
            ))}
            {corrStability.unstable_pairs.length > 5 && (
              <span className="text-[10px] text-amber-300/50">+{corrStability.unstable_pairs.length - 5} more</span>
            )}
          </div>
        </div>
      )}

      {/* Expandable Detail Panel */}
      {showDetail && (
        <CandidateDetailPanel
          metrics={m}
          equityCurve={localEquity}
          longs={ls ? localLongs : undefined}
          shorts={ls ? localShorts : undefined}
        />
      )}

      {/* OOS Reveal Section */}
      {oos && <OOSReveal oos={oos} />}
      {oosError && (
        <p className="text-xs text-ag-red mt-2">{oosError}</p>
      )}
    </div>
  );
}

const RISK_MODE_BADGE: Record<string, { label: string; cls: string }> = {
  anti_martingale: { label: "AM", cls: "bg-ag-green/20 text-ag-green" },
  martingale: { label: "M", cls: "bg-orange-500/20 text-orange-400" },
  inv_martingale: { label: "iM", cls: "bg-orange-500/20 text-orange-400" },
  inv_anti_martingale: { label: "iAM", cls: "bg-ag-green/20 text-ag-green" },
  fixed: { label: "F", cls: "bg-ag-text-muted/20 text-ag-text-muted" },
};

function AssetChip({ asset, color, removing, onRemove, acFlagged, acScore, riskMode }: {
  asset: string; color: "green" | "red" | "accent"; removing: boolean;
  onRemove?: () => void; acFlagged?: boolean; acScore?: number; riskMode?: string;
}) {
  const label = asset.replace("USDT_Long", "").replace("USDT_Short", "").replace("USDT", "");
  const bg = acFlagged
    ? "bg-orange-500/15 text-orange-400 ring-1 ring-orange-500/30"
    : color === "green" ? "bg-ag-green/15 text-ag-green"
    : color === "red" ? "bg-ag-red/15 text-ag-red"
    : "bg-ag-accent/15 text-ag-accent";
  const badge = riskMode ? RISK_MODE_BADGE[riskMode] : undefined;
  return (
    <span className={`group inline-flex items-center gap-0.5 px-2 py-0.5 ${bg} rounded text-xs ${removing ? "opacity-50" : ""}`}
      title={acFlagged ? `AC mismatch: ${acScore?.toFixed(4) ?? "?"}` : riskMode ? `Risk: ${riskMode}` : undefined}>
      {label}
      {badge && <span className={`ml-0.5 px-1 rounded text-[9px] font-semibold ${badge.cls}`}>{badge.label}</span>}
      {acFlagged && <span className="text-[9px]" title="AC mismatch">!</span>}
      {onRemove && (
        <button onClick={(e) => { e.stopPropagation(); onRemove(); }}
          className="opacity-0 group-hover:opacity-100 ml-0.5 hover:text-white transition-opacity"
          title={`Remove ${label}`}>
          &times;
        </button>
      )}
    </span>
  );
}

function wfeColor(wfe: number): string {
  if (wfe >= 0.8) return "text-ag-green";
  if (wfe >= 0.5) return "text-yellow-400";
  return "text-ag-red";
}

function OOSReveal({ oos }: { oos: OOSCompareResult }) {
  // Prefer oos_quick_metrics (daily-PnL DD, position-merged — same method as dashboard)
  // to avoid ~$1k discrepancy vs full_metrics which includes trades with NaT exit times
  const quick = oos.oos_quick_metrics as Record<string, number> | undefined;
  const fm = oos.oos_metrics;
  const oosPnl = quick?.net_profit ?? (typeof fm["Total Net Profit"] === "number" ? fm["Total Net Profit"] : 0);
  const oosPf = quick?.profit_factor ?? oos.aggregate_oos_pf;
  const oosWr = quick?.win_rate ?? (typeof fm["Win Rate"] === "number" ? fm["Win Rate"] : null);
  const oosDd = quick?.max_drawdown ?? (typeof fm["Max Drawdown ($)"] === "number" ? fm["Max Drawdown ($)"] : null);
  const dateRange = oos.trade_date_range;
  const debug = oos.debug;

  // Show diagnostic when 0 OOS trades found
  if (oos.oos_trades === 0) {
    return (
      <div className="mt-3 pt-3 border-t border-ag-border space-y-1">
        <p className="text-xs font-medium text-yellow-400">OOS Results</p>
        <p className="text-xs text-ag-text-muted">
          0 trades after cutoff {oos.cutoff}.
          {dateRange && dateRange.to && (
            <> Batch data goes from <strong>{dateRange.from}</strong> to <strong>{dateRange.to}</strong>.
            {dateRange.to <= (oos.cutoff || "") && (
              <span className="text-ag-red"> Your batch has NO data after the cutoff date. Re-run the batch with data beyond the OOS date.</span>
            )}
            </>
          )}
          {debug && (
            <> {String(debug.matched ?? 0)} of {String(debug.requested_assets ?? "?")} assets matched.</>
          )}
        </p>
        <p className="text-[10px] text-ag-text-muted">
          IS trades: {oos.is_trades}.
          To get OOS results, the batch must contain trade data AFTER the OOS cutoff date.
        </p>
      </div>
    );
  }

  return (
    <div className="mt-3 pt-3 border-t border-ag-border space-y-2">
      <div className="flex items-center gap-3">
        <span className="text-xs font-medium text-yellow-400">OOS Results</span>
        <span className={`text-sm font-bold ${wfeColor(oos.aggregate_wfe_pf ?? oos.aggregate_wfe)}`}
          title="PF-based WFE: (OOS_PF-1)/(IS_PF-1) — edge retention">
          WFE: {((oos.aggregate_wfe_pf ?? oos.aggregate_wfe) * 100).toFixed(1)}%
        </span>
        <span className="text-[10px] text-ag-text-muted" title="Time-normalized WFE (daily rate ratio)">
          Rate: {(oos.aggregate_wfe * 100).toFixed(0)}%
        </span>
        <span className="text-xs text-ag-text-muted">
          {oos.oos_trades} trades after {oos.cutoff}
          {dateRange && dateRange.to && ` (data to ${dateRange.to})`}
        </span>
      </div>
      <div className="grid grid-cols-4 gap-2 text-xs">
        <div>
          <span className="text-ag-text-muted">OOS Net Profit</span>
          <p className={oosPnl >= 0 ? "text-ag-green" : "text-ag-red"}>
            ${oosPnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </p>
        </div>
        <div>
          <span className="text-ag-text-muted">OOS Profit Factor</span>
          <p>{oosPf != null ? oosPf.toFixed(2) : "\u2014"}</p>
        </div>
        <div>
          <span className="text-ag-text-muted">OOS Max DD</span>
          <p className="text-ag-red">{oosDd != null ? `$${oosDd.toFixed(2)}` : "\u2014"}</p>
        </div>
        <div>
          <span className="text-ag-text-muted">OOS Win Rate</span>
          <p>{oosWr != null ? `${oosWr.toFixed(1)}%` : "\u2014"}</p>
        </div>
      </div>

      {/* Per-asset WFE mini table */}
      {oos.per_asset.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-ag-text-muted hover:text-ag-text">
            Per-asset WFE ({oos.per_asset.length} assets)
          </summary>
          <div className="mt-1 grid grid-cols-2 sm:grid-cols-3 gap-1">
            {oos.per_asset.map((a) => (
              <div key={a.asset} className="flex items-center justify-between px-2 py-0.5 bg-ag-dark/50 rounded">
                <span>{a.asset.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")}</span>
                <span className={wfeColor(a.WFE_PF ?? a.WFE)}>{((a.WFE_PF ?? a.WFE) * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
