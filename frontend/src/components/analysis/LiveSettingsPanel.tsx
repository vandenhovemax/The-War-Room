"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import KellyAllocationPanel from "./KellyAllocationPanel";

interface RiskProfile {
  mode?: string;
  initial_risk_usd?: number;
  initial_capital?: number;
  multiplier?: number;
  max_streak?: number;
  kelly_n?: number;
  kelly_warmup?: number;
  kelly_scope?: string;
  kelly_lookback?: number;
  kelly_cap?: number;
  override_enabled?: boolean;
  tp1_pct?: number;
  tp2_pct?: number;
  risk_pct?: number;
  sizing_master?: string;
  batch_configs?: Record<string, { mode?: string; value?: number; maxMargin?: number }>;
  max_position_pct?: number;
}

interface AssetGroup {
  group_name: string;
  timeframe: string;
  batch_ids: number[];
  asset_count: number;
}

interface Guardrails {
  daily_loss_limit_pct: number;
  max_daily_loss_usd: number;
  max_positions_long: number;
  max_positions_short: number;
  max_positions_total: number;
  max_position_pct: number;
  margin_breathing_room_pct: number;
}

interface Props {
  portfolioId: number;
}

const MODE_LABELS: Record<string, string> = {
  fixed: "Fixed $",
  pct: "% of Equity",
  martingale: "Martingale",
  anti_martingale: "Anti-Martingale",
  kelly: "Kelly Criterion",
};

export default function LiveSettingsPanel({ portfolioId }: Props) {
  const [profile, setProfile] = useState<RiskProfile>({});
  const [guardrails, setGuardrails] = useState<Guardrails | null>(null);
  const [batchRisk, setBatchRisk] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editRisk, setEditRisk] = useState<number>(0);
  const [editMaxPosPct, setEditMaxPosPct] = useState<number | "">("");
  const [overrideEnabled, setOverrideEnabled] = useState(true);
  const [toast, setToast] = useState("");
  const [assetGroups, setAssetGroups] = useState<AssetGroup[]>([]);
  const [groupSizing, setGroupSizing] = useState<Record<string, { risk_usd: number; max_margin: number }>>({});

  useEffect(() => {
    setLoading(true);
    api.getRiskProfile(portfolioId)
      .then((data) => {
        const rp = data.risk_profile || {};
        setProfile(rp);
        setGuardrails(data.guardrails);
        setBatchRisk(data.batch_risk_amount);
        setEditRisk(rp.initial_risk_usd || 0);
        setEditMaxPosPct(rp.max_position_pct !== undefined ? rp.max_position_pct : "");
        setOverrideEnabled(rp.override_enabled ?? true);
        // Per-group sizing
        const groups = data.asset_groups || [];
        setAssetGroups(groups);
        // Initialize sizing from existing batch_configs or defaults
        const bc = rp.batch_configs || {};
        const initSizing: Record<string, { risk_usd: number; max_margin: number }> = {};
        for (const g of groups) {
          const key = `${g.group_name}|${g.timeframe}`;
          // Use first batch's config as the group default
          const firstBid = g.batch_ids[0];
          const cfg = firstBid ? bc[String(firstBid)] : undefined;
          initSizing[key] = {
            risk_usd: cfg?.value || 0,
            max_margin: cfg?.maxMargin || 0,
          };
        }
        setGroupSizing(initSizing);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [portfolioId]);

  const handleSave = async () => {
    setSaving(true);
    try {
      // Build group_sizing payload if groups have non-zero sizing
      const gsPayload = assetGroups
        .filter((g) => {
          const key = `${g.group_name}|${g.timeframe}`;
          return (groupSizing[key]?.risk_usd || 0) > 0;
        })
        .map((g) => {
          const key = `${g.group_name}|${g.timeframe}`;
          const s = groupSizing[key];
          return {
            batch_ids: g.batch_ids,
            risk_usd: s.risk_usd,
            max_margin: s.max_margin > 0 ? s.max_margin : undefined,
          };
        });

      const res = await api.updateRiskProfile(portfolioId, {
        initial_risk_usd: editRisk > 0 ? editRisk : undefined,
        override_enabled: overrideEnabled,
        group_sizing: gsPayload.length > 0 ? gsPayload : undefined,
        max_position_pct: editMaxPosPct === "" ? undefined : editMaxPosPct,
      });
      setProfile(res.risk_profile as RiskProfile);
      setToast("Saved!");
      setTimeout(() => setToast(""), 2000);
    } catch {
      setToast("Save failed");
      setTimeout(() => setToast(""), 3000);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <p className="text-sm text-ag-text-muted p-4">Loading...</p>;

  const mode = profile.mode || "fixed";
  const hasProfile = mode !== "fixed" || (profile.initial_risk_usd && profile.initial_risk_usd > 0);
  const optimizationRisk = profile.initial_risk_usd ?? 0;
  const matchesOptimization = editRisk === optimizationRisk;

  return (
    <div className="space-y-5 p-4">
      {/* Paper ↔ Live parity banner — spells out what's identical and what isn't */}
      <div className="rounded-lg border border-ag-accent/30 bg-ag-accent/5 p-3 text-xs space-y-1">
        <p className="font-semibold text-ag-accent">Paper ↔ Live uses the same risk profile</p>
        <p className="text-ag-text-muted">
          The settings below drive both <strong>paper</strong> and <strong>real</strong> trading identically.
          Only differences between paper and real:
        </p>
        <ul className="text-ag-text-muted list-disc ml-5 space-y-0.5">
          <li>Paper simulates fees ({(0.055).toFixed(3)}% taker) and slippage (<code>PAPER_SLIPPAGE_PCT</code> in .env)</li>
          <li>Real uses the broker&apos;s actual fees + market slippage — no simulation</li>
        </ul>
      </div>

      {/* Section 1: Risk Profile Override */}
      <div className="rounded-lg border border-ag-border p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-ag-text">Portfolio Risk Profile</h3>
          <label className="flex items-center gap-2 text-xs text-ag-text-muted" title="When ON: this portfolio's profile is used. When OFF: falls back to global .env settings (RISK_PER_TRADE_USD etc).">
            <input
              type="checkbox"
              checked={overrideEnabled}
              onChange={(e) => setOverrideEnabled(e.target.checked)}
              className="accent-ag-green"
            />
            Override global Settings
          </label>
        </div>

        {!hasProfile && (
          <p className="text-xs text-ag-text-muted italic">
            No risk profile applied during optimization. Set a base risk below to override the global .env settings for this portfolio — or leave blank to fall back to the global Settings page.
          </p>
        )}

        <div className={`space-y-2 ${!overrideEnabled ? "opacity-40 pointer-events-none" : ""}`}>
          {/* Mode badge — only when an optimizer profile exists */}
          {hasProfile && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-ag-text-muted">Mode:</span>
              <span className="px-2 py-0.5 rounded bg-ag-accent/20 text-ag-accent text-xs font-medium">
                {MODE_LABELS[mode] || mode}
              </span>
              {mode === "fixed" && profile.initial_risk_usd && (
                <span className="text-xs text-ag-text-muted">${profile.initial_risk_usd}</span>
              )}
              {mode === "pct" && profile.risk_pct && (
                <span className="text-xs text-ag-text-muted">{profile.risk_pct}%</span>
              )}
            </div>
          )}

          {/* Martingale details */}
          {hasProfile && (mode === "martingale" || mode === "anti_martingale") && (
            <div className="grid grid-cols-2 gap-2 text-xs text-ag-text-muted">
              <span>Multiplier: {profile.multiplier ?? 2}x</span>
              <span>Max Streak: {profile.max_streak ?? 3}</span>
            </div>
          )}

          {/* Kelly details */}
          {hasProfile && mode === "kelly" && (
            <div className="grid grid-cols-2 gap-2 text-xs text-ag-text-muted">
              <span>N Divisor: {profile.kelly_n ?? 2} ({profile.kelly_n === 1 ? "Full" : profile.kelly_n === 2 ? "Half" : "Quarter"} Kelly)</span>
              <span>Warmup: {profile.kelly_warmup ?? 20} trades</span>
              <span>Scope: {(profile.kelly_scope || "portfolio") === "asset" ? "Per Asset" : "Portfolio"}</span>
              <span>Lookback: {profile.kelly_lookback && profile.kelly_lookback > 0 ? `${profile.kelly_lookback} trades` : "All"}</span>
              {profile.kelly_cap && profile.kelly_cap > 0 && (
                <span>Cap: {profile.kelly_cap}% of equity</span>
              )}
            </div>
          )}

          {/* Editable initial risk — always shown; label changes based on context */}
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <label className="text-xs text-ag-text-muted w-24" title="Base dollar risk per trade. The live engine uses this as the base for fixed/martingale/kelly sizing, and category-on-fire scaling multiplies it.">
              {!hasProfile ? "Base Risk ($)" : (mode === "martingale" || mode === "anti_martingale" ? "Base Risk ($)" : "Initial Risk ($)")}
            </label>
            <input
              type="number" min={0} step={10}
              value={editRisk}
              onChange={(e) => setEditRisk(parseFloat(e.target.value) || 0)}
              placeholder={!hasProfile ? "e.g. 250" : ""}
              className="w-28 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
            />
            {hasProfile && (
              matchesOptimization ? (
                <span className="px-2 py-0.5 rounded bg-ag-green/15 text-ag-green text-[10px] font-medium" title="This value matches what was set during optimization — paper & live will trade with the optimized sizing.">
                  ✓ Matches Optimization
                </span>
              ) : (
                <button
                  onClick={() => setEditRisk(optimizationRisk)}
                  className="px-2 py-0.5 rounded bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-accent hover:border-ag-accent/40 text-[10px] transition-colors"
                  title={`Restore to the exact dollar amount used during the optimization run (${optimizationRisk})`}
                >
                  ↺ Reset to Optimization (${optimizationRisk})
                </button>
              )
            )}
            <span className="text-xs text-ag-text-muted" title="Risk amount saved by the source batch backtest — used as fallback if this portfolio has no profile.">
              Batch risk: ${batchRisk}
            </span>
          </div>

          {/* Max Position % — portfolio override for the global size cap */}
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <label className="text-xs text-ag-text-muted w-24" title="Max margin (equity tied up) per single position, as % of total equity. Overrides the global Settings page value for this portfolio only. Leave blank to use global, enter 0 to disable the cap entirely.">
              Max Position %
            </label>
            <input
              type="number" min={0} max={100} step={1}
              value={editMaxPosPct}
              onChange={(e) => {
                const v = e.target.value;
                setEditMaxPosPct(v === "" ? "" : parseFloat(v));
              }}
              placeholder={guardrails ? `${guardrails.max_position_pct} (global)` : ""}
              className="w-28 rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text"
            />
            <span className="text-xs text-ag-text-muted">
              {editMaxPosPct === ""
                ? "using global"
                : editMaxPosPct === 0
                ? "cap disabled"
                : `${editMaxPosPct}% of equity per position`}
            </span>
          </div>

          {/* Effective per-trade risk preview */}
          <div className="rounded bg-ag-dark/40 border border-ag-border/40 p-2 text-[11px] text-ag-text-muted">
            <span className="font-medium text-ag-text">Effective fresh-trade risk:</span>{" "}
            <span className="text-ag-green font-mono">
              {editRisk <= 0
                ? "(falls back to global .env)"
                : mode === "pct"
                ? `${profile.risk_pct ?? editRisk}% of equity`
                : mode === "martingale" || mode === "anti_martingale"
                ? `$${editRisk} base × ${profile.multiplier ?? 2}^streak (max ${profile.max_streak ?? 3})`
                : mode === "kelly"
                ? `Kelly/${profile.kelly_n ?? 2} of $${editRisk} after ${profile.kelly_warmup ?? 20} warmup trades`
                : `$${editRisk} flat`}
            </span>
            <span className="ml-2">— this is what the live engine will risk on the next trade.</span>
          </div>

          {/* Save button */}
          <div className="flex items-center gap-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-3 py-1 rounded bg-ag-accent text-white text-xs font-medium hover:bg-ag-accent/80 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save"}
            </button>
            {toast && <span className="text-xs text-ag-green">{toast}</span>}
          </div>
        </div>
      </div>

      {/* Section 2: TP Split */}
      <div className="rounded-lg border border-ag-border p-4 space-y-2">
        <h3 className="text-sm font-semibold text-ag-text flex items-center gap-2">
          TP Split
          <svg className="w-3.5 h-3.5 text-ag-text-muted" fill="currentColor" viewBox="0 0 20 20"><path d="M5 9V7a5 5 0 0 1 10 0v2a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2zm8-2v2H7V7a3 3 0 0 1 6 0z"/></svg>
        </h3>
        {profile.tp2_pct && profile.tp2_pct > 0 ? (
          <div className="flex items-center gap-4 text-xs">
            <span className="px-2 py-0.5 rounded bg-ag-green/20 text-ag-green">
              TP1: {profile.tp1_pct ?? 100}%
            </span>
            <span className="px-2 py-0.5 rounded bg-ag-accent/20 text-ag-accent">
              TP2: {profile.tp2_pct}%
            </span>
          </div>
        ) : (
          <p className="text-xs text-ag-text-muted">Single TP: 100%</p>
        )}
        <p className="text-xs text-ag-text-muted italic">
          Locked from your strategy. Cannot be changed.
        </p>
      </div>

      {/* Section 3: Per-Group Sizing (only for multi-group WFAS portfolios) */}
      {assetGroups.length > 0 && (
        <div className="rounded-lg border border-ag-border p-4 space-y-3">
          <h3 className="text-sm font-semibold text-ag-text">Per-Group Sizing</h3>
          <p className="text-xs text-ag-text-muted">
            Set the real trading risk amount ($) and max margin per group/timeframe.
            This overrides the single risk value above for grouped portfolios.
          </p>
          <div className="space-y-2">
            {assetGroups.map((g) => {
              const key = `${g.group_name}|${g.timeframe}`;
              const s = groupSizing[key] || { risk_usd: 0, max_margin: 0 };
              return (
                <div key={key} className="flex items-center gap-3 px-3 py-2 bg-ag-dark/30 rounded">
                  <div className="flex-shrink-0 w-32">
                    <span className="text-xs font-medium text-ag-text">
                      Group {g.group_name || "—"}
                    </span>
                    <span className="text-[10px] text-ag-text-muted ml-1">
                      ({g.timeframe}m · {g.asset_count} assets)
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <label className="text-[10px] text-ag-text-muted">Risk $</label>
                    <input
                      type="number" min={0} step={10}
                      value={s.risk_usd || ""}
                      onChange={(e) => setGroupSizing((prev) => ({
                        ...prev,
                        [key]: { ...prev[key], risk_usd: parseFloat(e.target.value) || 0 },
                      }))}
                      placeholder="0"
                      className="w-20 rounded border border-ag-border bg-ag-bg px-2 py-1 text-xs text-ag-text"
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <label className="text-[10px] text-ag-text-muted">Max Margin $</label>
                    <input
                      type="number" min={0} step={100}
                      value={s.max_margin || ""}
                      onChange={(e) => setGroupSizing((prev) => ({
                        ...prev,
                        [key]: { ...prev[key], max_margin: parseFloat(e.target.value) || 0 },
                      }))}
                      placeholder="0 (no limit)"
                      className="w-24 rounded border border-ag-border bg-ag-bg px-2 py-1 text-xs text-ag-text"
                    />
                  </div>
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-ag-text-muted italic">
            Saved with the profile above. Groups with $0 risk use the global risk value.
          </p>
        </div>
      )}

      {/* Guardrails */}
      {guardrails && (
        <div className="rounded-lg border border-ag-border p-4 space-y-2">
          <h3 className="text-sm font-semibold text-ag-text">Guardrails (Global Settings)</h3>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-ag-text-muted">
            <span>Daily Loss Limit: {guardrails.daily_loss_limit_pct}%</span>
            <span>Max Daily Loss: ${guardrails.max_daily_loss_usd}</span>
            <span>Max Long Positions: {guardrails.max_positions_long}</span>
            <span>Max Short Positions: {guardrails.max_positions_short}</span>
            <span>Max Total Positions: {guardrails.max_positions_total}</span>
            <span>Max Position Size: {guardrails.max_position_pct}% equity</span>
            <span>Margin Buffer: {guardrails.margin_breathing_room_pct}%</span>
          </div>
          <p className="text-xs text-ag-text-muted italic">
            Managed in global Settings. Apply to ALL portfolios.
          </p>
        </div>
      )}

      {/* Section 4: Kelly Portfolio Allocation */}
      <KellyAllocationPanel portfolioId={portfolioId} />
    </div>
  );
}
