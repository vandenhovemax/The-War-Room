"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";

interface Props {
  portfolioId: number;
}

const SCHEDULE_LABELS: Record<string, string> = {
  weekly: "Weekly",
  biweekly: "Bi-weekly",
  monthly: "Monthly",
  "2month": "Every 2 Months",
  "6month": "Every 6 Months",
  by_trades: "By Trade Count",
};

const MODE_LABELS: Record<string, string> = {
  per_strategy: "Per Strategy",
  per_asset: "Per Asset",
};

export default function KellyAllocationPanel({ portfolioId }: Props) {
  const [data, setData] = useState<{
    enabled: boolean;
    weights: Record<string, number>;
    config: Record<string, unknown>;
    result: Record<string, unknown>;
    computed_at: string | null;
    trigger: string | null;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [computing, setComputing] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [toast, setToast] = useState("");

  // Config editing state
  const [editConfig, setEditConfig] = useState({
    mode: "per_strategy",
    half_kelly_multiplier: 0.5,
    sr_threshold: 0.5,
    max_cap_pct: 0.40,
    corr_warning_threshold: 0.7,
    regime_dampening_factor: 0.5,
    min_trades_per_strategy: 100,
    is_lookback_days: 0,
    schedule_type: "monthly",
    schedule_trade_count: 100,
  });

  const load = () => {
    setLoading(true);
    api.getKellyAllocation(portfolioId)
      .then((d) => {
        setData(d);
        if (d.config && Object.keys(d.config).length > 0) {
          setEditConfig((prev) => ({ ...prev, ...d.config }));
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [portfolioId]);

  const handleCompute = async () => {
    setComputing(true);
    try {
      const res = await api.computeKellyAllocation(portfolioId);
      setData({
        enabled: true,
        weights: res.weights,
        config: res.config,
        result: res.result,
        computed_at: res.computed_at,
        trigger: "manual",
      });
      setToast("Allocation computed!");
      setTimeout(() => setToast(""), 3000);
    } catch {
      setToast("Compute failed");
      setTimeout(() => setToast(""), 3000);
    } finally {
      setComputing(false);
    }
  };

  const handleSaveConfig = async () => {
    try {
      await api.updateKellyConfig(portfolioId, editConfig);
      setToast("Config saved!");
      setTimeout(() => setToast(""), 2000);
    } catch {
      setToast("Save failed");
      setTimeout(() => setToast(""), 3000);
    }
  };

  if (loading) return <p className="text-sm text-ag-text-muted p-4">Loading...</p>;

  const weights = data?.weights || {};
  const result = (data?.result || {}) as Record<string, unknown>;
  const sortedEntries = Object.entries(weights).sort(([, a], [, b]) => b - a);
  const maxWeight = sortedEntries.length > 0 ? Math.max(...sortedEntries.map(([, w]) => w)) : 1;
  const hhi = typeof result.herfindahl_index === "number" ? result.herfindahl_index : null;
  const regime = typeof result.regime_state === "string" ? result.regime_state : null;
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  const concentrationFlags = Array.isArray(result.concentration_flags) ? result.concentration_flags : [];

  return (
    <div className="rounded-lg border border-ag-border p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ag-text">
          Capital Allocation (Kelly)
        </h3>
        <div className="flex items-center gap-2">
          {toast && <span className="text-xs text-ag-green">{toast}</span>}
          <button
            onClick={handleCompute}
            disabled={computing}
            className="px-3 py-1 rounded bg-ag-accent text-white text-xs font-medium hover:bg-ag-accent/80 disabled:opacity-50"
          >
            {computing ? "Computing..." : sortedEntries.length > 0 ? "Recompute" : "Compute Now"}
          </button>
        </div>
      </div>

      {/* Current weights display */}
      {sortedEntries.length > 0 ? (
        <div className="space-y-2">
          <div className="flex items-center gap-3 text-xs text-ag-text-muted">
            <span>Mode: <span className="text-ag-text">{MODE_LABELS[editConfig.mode] || editConfig.mode}</span></span>
            <span>Schedule: <span className="text-ag-text">{SCHEDULE_LABELS[editConfig.schedule_type] || editConfig.schedule_type}</span></span>
          </div>

          {/* Weight bars */}
          <div className="space-y-1.5">
            {sortedEntries.map(([name, weight]) => (
              <div key={name} className="flex items-center gap-2 text-xs">
                <span className="w-32 truncate text-ag-text-muted" title={name}>
                  {name}
                </span>
                <div className="flex-1 h-4 bg-ag-bg rounded overflow-hidden">
                  <div
                    className="h-full bg-ag-accent/60 rounded"
                    style={{ width: `${(weight / maxWeight) * 100}%` }}
                  />
                </div>
                <span className="w-14 text-right text-ag-text font-mono">
                  {(weight * 100).toFixed(1)}%
                </span>
              </div>
            ))}
          </div>

          {/* Diagnostics row */}
          <div className="flex items-center gap-4 text-xs text-ag-text-muted pt-1">
            {hhi !== null && <span>Herfindahl: {hhi.toFixed(3)}</span>}
            {regime && (
              <span className={
                regime === "high_vol" ? "text-ag-red" :
                regime === "low_vol" ? "text-ag-green" : ""
              }>
                Regime: {regime.replace("_", " ")}
              </span>
            )}
            {data?.computed_at && (
              <span>Last: {new Date(data.computed_at).toLocaleDateString()}</span>
            )}
          </div>

          {/* Warnings */}
          {concentrationFlags.length > 0 && (
            <div className="space-y-0.5">
              {concentrationFlags.map((f, i) => (
                <p key={i} className="text-xs text-ag-yellow flex items-center gap-1">
                  <span>!</span> {String(f)}
                </p>
              ))}
            </div>
          )}
          {warnings.length > 0 && (
            <div className="space-y-0.5">
              {warnings.map((w, i) => (
                <p key={i} className="text-xs text-ag-red flex items-center gap-1">
                  <span>!</span> {String(w)}
                </p>
              ))}
            </div>
          )}
        </div>
      ) : (
        <p className="text-xs text-ag-text-muted italic">
          No allocation computed yet. Click "Compute Now" to generate Kelly weights from IS data.
        </p>
      )}

      {/* Advanced Settings */}
      <div>
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="text-xs text-ag-accent hover:underline"
        >
          {showAdvanced ? "Hide" : "Show"} Advanced Settings
        </button>

        {showAdvanced && (
          <div className="mt-2 space-y-2 border-t border-ag-border pt-2">
            <div className="grid grid-cols-2 gap-2">
              {/* Mode */}
              <label className="text-xs text-ag-text-muted">
                Mode
                <select
                  value={editConfig.mode}
                  onChange={(e) => setEditConfig({ ...editConfig, mode: e.target.value })}
                  className="block w-full mt-0.5 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
                >
                  <option value="per_strategy">Per Strategy</option>
                  <option value="per_asset">Per Asset</option>
                </select>
              </label>

              {/* Schedule */}
              <label className="text-xs text-ag-text-muted">
                Recompute Schedule
                <select
                  value={editConfig.schedule_type}
                  onChange={(e) => setEditConfig({ ...editConfig, schedule_type: e.target.value })}
                  className="block w-full mt-0.5 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
                >
                  {Object.entries(SCHEDULE_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
              </label>

              {/* Half-Kelly multiplier */}
              <label className="text-xs text-ag-text-muted">
                Half-Kelly Mult
                <input
                  type="number" min={0.1} max={1} step={0.1}
                  value={editConfig.half_kelly_multiplier}
                  onChange={(e) => setEditConfig({ ...editConfig, half_kelly_multiplier: parseFloat(e.target.value) || 0.5 })}
                  className="block w-full mt-0.5 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
                />
              </label>

              {/* SR threshold */}
              <label className="text-xs text-ag-text-muted">
                SR Threshold
                <input
                  type="number" min={0} max={3} step={0.1}
                  value={editConfig.sr_threshold}
                  onChange={(e) => setEditConfig({ ...editConfig, sr_threshold: parseFloat(e.target.value) || 0 })}
                  className="block w-full mt-0.5 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
                />
              </label>

              {/* Max cap */}
              <label className="text-xs text-ag-text-muted">
                Max Cap / Strategy
                <input
                  type="number" min={0.05} max={1} step={0.05}
                  value={editConfig.max_cap_pct}
                  onChange={(e) => setEditConfig({ ...editConfig, max_cap_pct: parseFloat(e.target.value) || 0.4 })}
                  className="block w-full mt-0.5 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
                />
              </label>

              {/* Corr threshold */}
              <label className="text-xs text-ag-text-muted">
                Correlation Warn
                <input
                  type="number" min={0.3} max={1} step={0.05}
                  value={editConfig.corr_warning_threshold}
                  onChange={(e) => setEditConfig({ ...editConfig, corr_warning_threshold: parseFloat(e.target.value) || 0.7 })}
                  className="block w-full mt-0.5 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
                />
              </label>

              {/* Min trades */}
              <label className="text-xs text-ag-text-muted">
                Min Trades
                <input
                  type="number" min={10} max={1000} step={10}
                  value={editConfig.min_trades_per_strategy}
                  onChange={(e) => setEditConfig({ ...editConfig, min_trades_per_strategy: parseInt(e.target.value) || 100 })}
                  className="block w-full mt-0.5 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
                />
              </label>

              {/* Regime dampening */}
              <label className="text-xs text-ag-text-muted">
                Regime Dampening
                <input
                  type="number" min={0} max={1} step={0.1}
                  value={editConfig.regime_dampening_factor}
                  onChange={(e) => setEditConfig({ ...editConfig, regime_dampening_factor: parseFloat(e.target.value) || 0 })}
                  className="block w-full mt-0.5 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
                />
              </label>

              {/* Trade count (for by_trades schedule) */}
              {editConfig.schedule_type === "by_trades" && (
                <label className="text-xs text-ag-text-muted">
                  Recompute Every N Trades
                  <input
                    type="number" min={10} max={1000} step={10}
                    value={editConfig.schedule_trade_count}
                    onChange={(e) => setEditConfig({ ...editConfig, schedule_trade_count: parseInt(e.target.value) || 100 })}
                    className="block w-full mt-0.5 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
                  />
                </label>
              )}
            </div>

            <button
              onClick={handleSaveConfig}
              className="px-3 py-1 rounded bg-ag-accent text-white text-xs font-medium hover:bg-ag-accent/80"
            >
              Save Config
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
