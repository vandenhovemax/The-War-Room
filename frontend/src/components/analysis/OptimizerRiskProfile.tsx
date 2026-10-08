"use client";
import { useMemo } from "react";
import HintTip from "@/components/common/HintTip";
import StreakStudyPanel from "./StreakStudyPanel";

const RISK_MODES = [
  { value: "fixed", label: "Fixed $" },
  { value: "pct", label: "% of Equity" },
  { value: "martingale", label: "Martingale" },
  { value: "anti_martingale", label: "Anti-Martingale" },
  { value: "inv_martingale", label: "Inverse Martingale" },
  { value: "inv_anti_martingale", label: "Inverse Anti-Martingale" },
  { value: "kelly", label: "Kelly Criterion" },
];

export interface RiskProfileConfig {
  mode: string;
  value: number;
  initialCapital: number;
  multiplier: number;
  maxStreak: number;
  martingaleCompound: string;
  martingaleScope: string;
  martingaleCountMode: string;
  martingaleOosReset: boolean;
  martingaleStartAfter: number;
  antiMartingaleResetAfter: number;
  kellyN: number;
  kellyWarmup: number;
  kellyScope: string;
  kellyLookback: number;
  kellyCap: number;
  /** Extra slippage % for per-timeframe mode (0 = use global) */
  slippage: number;
  /** Auto-multiply base risk by each batch's group pnl_multiplier (e.g. Group A = 2×) */
  applyGroupMultiplier: boolean;
}

export interface BatchInfo {
  id: number;
  label: string;
  timeframe?: string;
}

/** Per-asset recommendation returned by /auto-risk-profile */
export interface AutoRiskAssetResult {
  mode: string;
  max_streak: number;
  start_after: number;
  reset_after: number;
  confidence: number;
  reason: string;
  derivation_method?: string;
  batch_id?: number;
  strategy_name?: string;
}

/** Full auto-risk API response */
export interface AutoRiskResult {
  dominant_mode: string;
  per_asset: Record<string, AutoRiskAssetResult>;
  config: {
    mode: string;
    value: number;
    valueFixed: number;
    valueMartingale: number;
    initialCapital: number;
    multiplier: number;
    maxStreak: number;
    martingaleStartAfter: number;
    antiMartingaleResetAfter: number;
  };
}

export const DEFAULT_RISK_PROFILE: RiskProfileConfig = {
  mode: "fixed", value: 0, initialCapital: 10000,
  multiplier: 2, maxStreak: 3, martingaleCompound: "off",
  martingaleScope: "asset", martingaleCountMode: "full_position",
  martingaleOosReset: true, martingaleStartAfter: 0,
  antiMartingaleResetAfter: 0, kellyN: 2, kellyWarmup: 20,
  kellyScope: "portfolio", kellyLookback: 0, kellyCap: 0,
  slippage: 0,
  applyGroupMultiplier: false,
};

interface Props {
  show: boolean;
  onToggle: () => void;
  config: RiskProfileConfig;
  onChange: (c: RiskProfileConfig) => void;
  /** Master sizing mode: "portfolio" (one shared config) or "per_tf" */
  sizingMaster?: string;
  onSizingMasterChange?: (v: string) => void;
  /** Per-timeframe configs (keyed by timeframe string, e.g. "30") */
  tfConfigs?: Record<string, RiskProfileConfig>;
  onTfConfigsChange?: (tc: Record<string, RiskProfileConfig>) => void;
  /** Available batches — timeframe used for grouping in per-TF mode */
  batches?: BatchInfo[];
  /** Auto risk profile */
  autoEnabled?: boolean;
  onAutoEnabledChange?: (v: boolean) => void;
  autoAggression?: string;
  onAutoAggressionChange?: (v: string) => void;
  /** Called when auto-analyze should run (parent handles the API call) */
  onAutoAnalyze?: () => void;
  autoLoading?: boolean;
  autoResult?: AutoRiskResult | null;
  /** Separate base risk for martingale modes (auto only) */
  autoBaseRiskMart?: number;
  onAutoBaseRiskMartChange?: (v: number) => void;
  /** Min confidence % — below this → force Fixed Risk (0 = disabled) */
  autoMinConfidence?: number;
  onAutoMinConfidenceChange?: (v: number) => void;
  /** Strategy hint: "trend" | "meanrev" | "auto" — biases thresholds */
  autoStrategyHint?: string;
  onAutoStrategyHintChange?: (v: string) => void;
  /** OOS boundary date — streak study uses only IS trades before this */
  dateTo?: string;
}

/* ─── Streak counting mode — how exits affect the win/loss counter ─── */
const COUNT_MODE_OPTIONS = [
  { value: "full_position", label: "Full Position" },
  { value: "each_exit", label: "Each Exit" },
  { value: "confirmed_tsl", label: "TP + Confirmed TSL" },
];

/* ─── Martingale scope options — "batch" removed, now a master toggle ─── */
const SCOPE_OPTIONS = [
  { value: "asset", label: "Per Asset" },
  { value: "portfolio", label: "Portfolio-wide" },
  { value: "asset_batch", label: "Per Asset + Batch" },
];

const MODE_LABELS: Record<string, string> = {
  fixed: "Fixed $",
  pct: "% of Equity",
  martingale: "Martingale",
  anti_martingale: "Anti-Martingale",
  inv_martingale: "Inv. Martingale",
  inv_anti_martingale: "Inv. Anti-Mart.",
  kelly: "Kelly",
};

/* ─── Single config panel (reused for portfolio + each per-TF) ─── */
function ConfigPanel({
  config, onChange, showScope, compact, label, batchId, dateTo, showSlippage,
}: {
  config: RiskProfileConfig;
  onChange: (c: RiskProfileConfig) => void;
  showScope: boolean;
  compact?: boolean;
  label?: string;
  batchId?: number;
  dateTo?: string;
  /** Show per-TF slippage input (only in per-timeframe mode) */
  showSlippage?: boolean;
}) {
  const set = (key: keyof RiskProfileConfig, val: string | number) =>
    onChange({ ...config, [key]: val });

  const isMart = ["martingale", "anti_martingale", "inv_martingale", "inv_anti_martingale"].includes(config.mode);
  const inputCls = "rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text";

  return (
    <div className={`space-y-2 ${compact ? "p-2 rounded border border-ag-border bg-ag-dark/30" : ""}`}>
      {label && <div className="text-xs font-medium text-ag-accent truncate mb-1">{label}</div>}

      {/* Mode */}
      <div className="flex items-center gap-2 flex-wrap">
        <label className="text-xs text-ag-text-muted w-20 shrink-0">Mode</label>
        <select value={config.mode} onChange={(e) => set("mode", e.target.value)} className={inputCls}>
          {RISK_MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </div>

      {/* Fixed $ */}
      {config.mode === "fixed" && (
        <div className="flex items-center gap-2">
          <label className="text-xs text-ag-text-muted w-20 shrink-0">Risk ($)</label>
          <input type="number" min={0} step={10} value={config.value}
            onChange={(e) => set("value", parseFloat(e.target.value) || 0)}
            className={`w-24 ${inputCls}`} />
          <span className="text-xs text-ag-text-muted">0 = batch risk</span>
        </div>
      )}

      {/* % of Equity */}
      {config.mode === "pct" && (
        <>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Risk %</label>
            <input type="number" min={0.1} max={100} step={0.1} value={config.value}
              onChange={(e) => set("value", parseFloat(e.target.value) || 2)}
              className={`w-24 ${inputCls}`} />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Start Capital</label>
            <input type="number" min={100} step={100} value={config.initialCapital}
              onChange={(e) => set("initialCapital", parseFloat(e.target.value) || 10000)}
              className={`w-24 ${inputCls}`} />
          </div>
        </>
      )}

      {/* Group Multiplier toggle */}
      <div className="flex items-center gap-2">
        <label className="text-xs text-ag-text-muted w-20 shrink-0">Group Scale</label>
        <input type="checkbox" checked={config.applyGroupMultiplier ?? false}
          onChange={(e) => onChange({ ...config, applyGroupMultiplier: e.target.checked })}
          className="accent-ag-accent w-4 h-4" />
        <span className="text-xs text-ag-text-muted">Auto-multiply by group size</span>
        <HintTip text="When enabled, the base risk is automatically multiplied by each batch's group pnl_multiplier in live trading. E.g. Group A = 2x means a $250 base risk becomes $500 for Group A batches, while Group B/C (1x) stay at $250." />
      </div>

      {/* Martingale variants */}
      {isMart && (
        <>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Compound</label>
            <select value={config.martingaleCompound} onChange={(e) => set("martingaleCompound", e.target.value)} className={inputCls}>
              <option value="off">Off (flat base)</option>
              <option value="dollar">$ Compound</option>
              <option value="pct">% of Equity</option>
            </select>
            <HintTip text="Off = fixed base risk + streak multiplier. $ Compound = base risk grows with equity. % of Equity = risk is X% of current equity × streak multiplier." />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">
              {config.martingaleCompound === "pct" ? "Risk %" : "Base Risk ($)"}
            </label>
            <input type="number"
              min={config.martingaleCompound === "pct" ? 0.1 : 1}
              max={config.martingaleCompound === "pct" ? 100 : undefined}
              step={config.martingaleCompound === "pct" ? 0.1 : 10}
              value={config.value}
              onChange={(e) => set("value", parseFloat(e.target.value) || (config.martingaleCompound === "pct" ? 2 : 100))}
              className={`w-24 ${inputCls}`} />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Multiplier</label>
            <input type="number" min={1} max={10} step={0.1} value={config.multiplier}
              onChange={(e) => set("multiplier", parseFloat(e.target.value) || 2)}
              className={`w-24 ${inputCls}`} />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Max Streak</label>
            <input type="number" min={1} max={10} step={1} value={config.maxStreak}
              onChange={(e) => set("maxStreak", parseInt(e.target.value) || 3)}
              className={`w-24 ${inputCls}`} />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Start Capital</label>
            <input type="number" min={100} step={100} value={config.initialCapital}
              onChange={(e) => set("initialCapital", parseFloat(e.target.value) || 10000)}
              className={`w-24 ${inputCls}`} />
          </div>
          {/* Scope — only shown in portfolio master mode */}
          {showScope && (
            <div className="flex items-center gap-2">
              <label className="text-xs text-ag-text-muted w-20 shrink-0">Scope</label>
              <select value={config.martingaleScope ?? "asset"}
                onChange={(e) => onChange({ ...config, martingaleScope: e.target.value })}
                className={inputCls}>
                {SCOPE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              <HintTip text="Per Asset: streaks tracked per symbol across all batches. Portfolio-wide: one shared streak across ALL assets. Per Asset + Batch: per symbol within each batch (BTC batch 1 ≠ BTC batch 3)." />
            </div>
          )}
          {/* Streak counting mode */}
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Count Mode</label>
            <select value={config.martingaleCountMode ?? "full_position"}
              onChange={(e) => onChange({ ...config, martingaleCountMode: e.target.value })}
              className={inputCls}>
              {COUNT_MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <HintTip text="How exits update the win/loss streak counter. Full Position: entire position PnL = one event (TP1+TSL summed). Each Exit: every fill (TP1, TSL) counts independently. TP + Confirmed TSL: TP exits always count; TSL only counts if it closes at profit (SL moved past BE). For pyramids, the TSL confirmation is backdated to when the SL crossed past breakeven. Multiplier is always locked at position entry." />
          </div>
          {/* OOS streak continuation */}
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">OOS Streaks</label>
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={!config.martingaleOosReset}
                onChange={(e) => onChange({ ...config, martingaleOosReset: !e.target.checked })}
                className="accent-ag-accent" />
              <span className="text-xs text-ag-text">Continue into OOS</span>
            </label>
            <HintTip text="When checked, the win/loss streak carries from In-Sample into Out-of-Sample without resetting. When unchecked (default), the streak counter resets to 0 at the IS/OOS boundary so OOS starts fresh at 1x sizing." />
          </div>
          {/* Start After X Losses — only for martingale / inv_martingale */}
          {["martingale", "inv_martingale"].includes(config.mode) && (
            <div className="flex items-center gap-2">
              <label className="text-xs text-ag-text-muted w-20 shrink-0">Start After</label>
              <input type="number" min={0} max={20} step={1}
                value={config.martingaleStartAfter ?? 0}
                onChange={(e) => set("martingaleStartAfter", parseInt(e.target.value) || 0)}
                className={`w-24 ${inputCls}`} />
              <HintTip text="Only start applying the multiplier after X consecutive losses. The first X losses use base position size. Loss X+1 gets multiplier×1, loss X+2 gets multiplier×2, etc. 0 = disabled (multiply from the first loss)." />
            </div>
          )}
          {/* Reset After X Wins — only for anti_martingale / inv_anti_martingale */}
          {["anti_martingale", "inv_anti_martingale"].includes(config.mode) && (
            <div className="flex items-center gap-2">
              <label className="text-xs text-ag-text-muted w-20 shrink-0">Reset After</label>
              <input type="number" min={0} max={20} step={1}
                value={config.antiMartingaleResetAfter ?? 0}
                onChange={(e) => set("antiMartingaleResetAfter", parseInt(e.target.value) || 0)}
                className={`w-24 ${inputCls}`} />
              <HintTip text="Reset the win streak counter back to 0 (base size) after X consecutive wins. This prevents the position size from growing indefinitely during long win streaks. 0 = disabled (never auto-reset)." />
            </div>
          )}
        </>
      )}

      {/* Kelly */}
      {config.mode === "kelly" && (() => {
        const presets = [{ f: 1, l: "Full" }, { f: 0.5, l: "Half" }, { f: 0.25, l: "Quarter" }];
        const curFrac = parseFloat((1 / config.kellyN).toFixed(2));
        const isCustom = !presets.some((p) => Math.abs(config.kellyN - 1 / p.f) < 0.01);
        return (
        <>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Kelly ×</label>
            <div className="flex gap-1.5 items-center">
              {presets.map((p) => {
                const n = 1 / p.f;
                const active = Math.abs(config.kellyN - n) < 0.01;
                return (
                  <button key={p.f} type="button" onClick={() => set("kellyN", n)}
                    className={`px-2 py-1 text-xs rounded border transition-colors ${
                      active
                        ? "bg-ag-accent text-white border-ag-accent"
                        : "border-ag-border text-ag-text-muted hover:border-ag-accent hover:text-ag-text"
                    }`}>{p.l}</button>
                );
              })}
              <button type="button"
                className={`px-2 py-1 text-xs rounded border transition-colors ${
                  isCustom
                    ? "bg-ag-accent text-white border-ag-accent"
                    : "border-ag-border text-ag-text-muted hover:border-ag-accent hover:text-ag-text"
                }`}
                onClick={() => set("kellyN", 1 / 0.75)}>Custom</button>
              <input type="number" min={0.05} max={2} step={0.05}
                value={curFrac}
                onChange={(e) => {
                  const frac = Math.max(0.05, Math.min(2, parseFloat(e.target.value) || 0.5));
                  set("kellyN", 1 / frac);
                }}
                className={`w-16 text-center ${inputCls}`}
                title="Kelly fraction (0.05 - 2.0)" />
              <HintTip text="What fraction of the Kelly criterion to use. Full (1.0) = maximum growth but volatile. Half (0.5) = recommended balance of growth vs. safety. Custom = type any fraction in the number box." />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Warmup</label>
            <input type="number" min={5} max={200} step={5} value={config.kellyWarmup}
              onChange={(e) => set("kellyWarmup", parseInt(e.target.value) || 20)}
              className={`w-24 ${inputCls}`} />
            <span className="text-xs text-ag-text-muted">trades before full Kelly</span>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Scope</label>
            <select value={config.kellyScope ?? "portfolio"}
              onChange={(e) => onChange({ ...config, kellyScope: e.target.value })}
              className={inputCls}>
              <option value="asset">Per Asset</option>
              <option value="portfolio">Portfolio-wide</option>
            </select>
            <HintTip text="Per Asset = each symbol computes Kelly from its own trades independently. Portfolio-wide = all trades pooled for a single Kelly fraction (recommended when individual assets have few trades)." />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Lookback</label>
            <input type="number" min={0} max={1000} step={10} value={config.kellyLookback ?? 0}
              onChange={(e) => set("kellyLookback", parseInt(e.target.value) || 0)}
              className={`w-24 ${inputCls}`} />
            <span className="text-xs text-ag-text-muted">0 = all trades</span>
            <HintTip text="How many recent trades to use for Kelly calculation. 0 = use entire trade history. Lower values make Kelly adapt faster to changing market conditions." />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Cap %</label>
            <input type="number" min={0} max={100} step={0.5} value={config.kellyCap ?? 0}
              onChange={(e) => set("kellyCap", parseFloat(e.target.value) || 0)}
              className={`w-24 ${inputCls}`} />
            <span className="text-xs text-ag-text-muted">0 = no cap</span>
            <HintTip text="Maximum % of equity Kelly can risk per trade. Safety valve to prevent over-betting even when Kelly suggests aggressive sizing. 0 = disabled (no cap)." />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted w-20 shrink-0">Start Capital</label>
            <input type="number" min={100} step={100} value={config.initialCapital}
              onChange={(e) => set("initialCapital", parseFloat(e.target.value) || 10000)}
              className={`w-24 ${inputCls}`} />
          </div>
        </>
        );
      })()}

      {/* Slippage — shown in per-timeframe mode */}
      {showSlippage && (
        <div className="flex items-center gap-2">
          <label className="text-xs text-ag-text-muted w-20 shrink-0">Slippage %</label>
          <input type="number" min={0} max={1} step={0.01}
            value={config.slippage ?? 0}
            onChange={(e) => set("slippage", Math.max(0, parseFloat(e.target.value) || 0))}
            className={`w-24 ${inputCls}`} />
          <HintTip text="Extra slippage cost for this timeframe. Lower TFs typically need more slippage (0.03-0.05%). Higher TFs need less (0.01-0.02%). 0 = use the global Extra Slip % setting." />
        </div>
      )}

      {/* Streak study — per-batch only */}
      {batchId !== undefined && (
        <div className="border-t border-ag-border/30 pt-2 mt-2">
          <StreakStudyPanel batchId={batchId} dateTo={dateTo} />
        </div>
      )}
    </div>
  );
}

/* ─── Auto Risk Controls — shown when Auto is enabled ─── */
function AutoRiskControls({
  config, onChange, aggression, onAggressionChange,
  onAnalyze, loading, result,
  baseRiskMart, onBaseRiskMartChange,
  minConfidence = 0, onMinConfidenceChange,
  strategyHint = "auto", onStrategyHintChange,
}: {
  config: RiskProfileConfig;
  onChange: (c: RiskProfileConfig) => void;
  aggression: string;
  onAggressionChange: (v: string) => void;
  onAnalyze?: () => void;
  loading?: boolean;
  result?: AutoRiskResult | null;
  baseRiskMart: number;
  onBaseRiskMartChange?: (v: number) => void;
  minConfidence?: number;
  onMinConfidenceChange?: (v: number) => void;
  strategyHint?: string;
  onStrategyHintChange?: (v: string) => void;
}) {
  const set = (key: keyof RiskProfileConfig, val: string | number) =>
    onChange({ ...config, [key]: val });
  const inputCls = "rounded border border-ag-border bg-ag-bg px-2 py-1 text-sm text-ag-text";

  return (
    <div className="space-y-3">
      {/* Two base risk inputs: Fixed and Martingale */}
      <div className="flex items-center gap-2">
        <label className="text-xs text-ag-text-muted w-24 shrink-0">
          Fixed Risk ($)
          <HintTip text="Dollar risk per trade for assets where auto selects Fixed mode (no streak edge detected). This is the flat position size." />
        </label>
        <input type="number" min={1} step={10} value={config.value}
          onChange={(e) => set("value", parseFloat(e.target.value) || 100)}
          className={`w-24 ${inputCls}`} />
      </div>
      <div className="flex items-center gap-2">
        <label className="text-xs text-ag-text-muted w-24 shrink-0">
          Mart. Base ($)
          <HintTip text="Base dollar risk at 1x for assets where auto selects Martingale or Anti-Martingale. This is the starting size before the multiplier scales it up on streaks. Typically lower than Fixed Risk since the multiplier amplifies it." />
        </label>
        <input type="number" min={1} step={10} value={baseRiskMart}
          onChange={(e) => onBaseRiskMartChange?.(parseFloat(e.target.value) || 50)}
          className={`w-24 ${inputCls}`} />
      </div>
      <div className="flex items-center gap-2">
        <label className="text-xs text-ag-text-muted w-24 shrink-0">
          Multiplier
          <HintTip text="The streak multiplier to use if auto selects a martingale/anti-martingale mode. E.g. 2x = double per streak level." />
        </label>
        <input type="number" min={1} max={10} step={0.1} value={config.multiplier}
          onChange={(e) => set("multiplier", parseFloat(e.target.value) || 2)}
          className={`w-24 ${inputCls}`} />
      </div>
      <div className="flex items-center gap-2">
        <label className="text-xs text-ag-text-muted w-24 shrink-0">
          Start Capital
          <HintTip text="Initial equity for compounding modes." />
        </label>
        <input type="number" min={100} step={100} value={config.initialCapital}
          onChange={(e) => set("initialCapital", parseFloat(e.target.value) || 10000)}
          className={`w-24 ${inputCls}`} />
      </div>

      {/* Aggression toggle */}
      <div className="flex items-center gap-2">
        <label className="text-xs text-ag-text-muted w-20 shrink-0">
          Style
          <HintTip text="Conservative = tighter max streak, longer pause before starting, earlier reset. Aggressive = higher max streak, earlier entry, later reset — more risk for more reward." />
        </label>
        <div className="flex gap-1">
          {(["conservative", "aggressive"] as const).map((v) => (
            <button key={v} type="button"
              onClick={() => onAggressionChange(v)}
              className={`px-3 py-1 text-xs rounded border transition-colors capitalize ${
                aggression === v
                  ? "bg-ag-accent text-white border-ag-accent"
                  : "border-ag-border text-ag-text-muted hover:border-ag-accent hover:text-ag-text"
              }`}>{v}</button>
          ))}
        </div>
      </div>

      {/* Min confidence threshold — below this → force Fixed */}
      <div className="flex items-center gap-2">
        <label className="text-xs text-ag-text-muted w-24 shrink-0">
          Min Conf %
          <HintTip text="Minimum confidence threshold. Assets where the auto-analyzer is less confident than this % get forced to Fixed Risk. Set to 0 to disable (accept any confidence)." />
        </label>
        <input type="number" min={0} max={95} step={5} value={Math.round(minConfidence * 100)}
          onChange={(e) => onMinConfidenceChange?.(Math.min(0.95, Math.max(0, (parseFloat(e.target.value) || 0) / 100)))}
          className={`w-24 ${inputCls}`} />
        <span className="text-[10px] text-ag-text-muted">%</span>
      </div>

      {/* Strategy hint — bias toward trend (anti-mart) or meanrev (mart) */}
      <div className="flex items-center gap-2">
        <label className="text-xs text-ag-text-muted w-24 shrink-0">
          Strategy Hint
          <HintTip text="Bias the auto-selection based on your strategy type. Trend = lower threshold for Anti-Martingale (trend strategies naturally produce win streaks). Mean-Rev = lower threshold for Martingale. Auto = no bias, use default thresholds." />
        </label>
        <div className="flex gap-1">
          {([
            { value: "auto", label: "Auto" },
            { value: "trend", label: "Trend" },
            { value: "meanrev", label: "Mean-Rev" },
          ] as const).map((o) => (
            <button key={o.value} type="button"
              onClick={() => onStrategyHintChange?.(o.value)}
              className={`px-2 py-1 text-xs rounded border transition-colors ${
                strategyHint === o.value
                  ? "bg-ag-accent text-white border-ag-accent"
                  : "border-ag-border text-ag-text-muted hover:border-ag-accent hover:text-ag-text"
              }`}>{o.label}</button>
          ))}
        </div>
      </div>

      {/* Analyze button */}
      {onAnalyze && (
        <button type="button" onClick={onAnalyze} disabled={loading}
          className="px-3 py-1.5 text-xs font-medium rounded border border-ag-accent text-ag-accent hover:bg-ag-accent hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
          {loading ? "Analyzing IS streaks…" : "Analyze & Auto-Select"}
        </button>
      )}

      {/* Results panel */}
      {result && (
        <div className="space-y-2 mt-2 p-2 rounded border border-ag-border bg-ag-dark/40">
          <div className="flex items-center gap-2">
            <span className="text-xs text-ag-text-muted">Recommended:</span>
            <span className="text-xs font-semibold text-ag-accent">
              {MODE_LABELS[result.dominant_mode] || result.dominant_mode}
            </span>
            {result.config.mode !== "fixed" && (
              <span className="text-xs text-ag-text-muted">
                (max streak {result.config.maxStreak}
                {result.config.martingaleStartAfter > 0 && `, start after ${result.config.martingaleStartAfter}`}
                {result.config.antiMartingaleResetAfter > 0 && `, reset after ${result.config.antiMartingaleResetAfter}`})
              </span>
            )}
          </div>

          {/* Per-asset breakdown */}
          <div className="max-h-40 overflow-y-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-ag-text-muted border-b border-ag-border">
                  <th className="text-left py-1 pr-2">Asset</th>
                  {Object.values(result.per_asset).some(a => a.strategy_name) && (
                    <th className="text-left py-1 pr-2">Strategy</th>
                  )}
                  <th className="text-left py-1 pr-2">Mode</th>
                  <th className="text-left py-1 pr-2">Method</th>
                  <th className="text-right py-1 pr-2">Conf</th>
                  <th className="text-left py-1">Reason</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(result.per_asset).map(([key, a]) => {
                  // Strip composite key format: "BTCUSDT__batch42" → "BTCUSDT"
                  const displaySym = key.includes("__batch") ? key.split("__batch")[0] : key;
                  return (
                  <tr key={key} className="border-b border-ag-border/30">
                    <td className="py-1 pr-2 text-ag-text font-mono">{displaySym.replace("USDT", "")}</td>
                    {Object.values(result.per_asset).some(aa => aa.strategy_name) && (
                      <td className="py-1 pr-2 text-ag-text-muted text-[10px] truncate max-w-[100px]" title={a.strategy_name}>{a.strategy_name}</td>
                    )}
                    <td className="py-1 pr-2">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        a.mode === "anti_martingale" ? "bg-green-500/20 text-green-400" :
                        a.mode === "martingale" ? "bg-orange-500/20 text-orange-400" :
                        "bg-gray-500/20 text-gray-400"
                      }`}>
                        {MODE_LABELS[a.mode] || a.mode}
                      </span>
                    </td>
                    <td className="py-1 pr-2">
                      {a.derivation_method && a.mode !== "fixed" ? (
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                          a.derivation_method === "pattern" ? "bg-purple-500/20 text-purple-400" :
                          a.derivation_method === "statistical" ? "bg-blue-500/20 text-blue-400" :
                          "bg-gray-500/20 text-gray-400"
                        }`}>
                          {a.derivation_method}
                        </span>
                      ) : (
                        <span className="text-[10px] text-ag-text-muted">—</span>
                      )}
                    </td>
                    <td className="py-1 pr-2 text-right text-ag-text-muted">{(a.confidence * 100).toFixed(0)}%</td>
                    <td className="py-1 text-ag-text-muted truncate max-w-[240px]" title={a.reason}>{a.reason}</td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Friendly timeframe labels ─── */
const TF_LABELS: Record<string, string> = {
  "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
  "60": "1h", "120": "2h", "240": "4h", "360": "6h", "720": "12h",
  "D": "Daily", "W": "Weekly",
};

/* ─── Main component ─── */
export default function OptimizerRiskProfile({
  show, onToggle, config, onChange,
  sizingMaster = "portfolio", onSizingMasterChange,
  tfConfigs = {}, onTfConfigsChange,
  batches,
  autoEnabled = false, onAutoEnabledChange,
  autoAggression = "conservative", onAutoAggressionChange,
  onAutoAnalyze, autoLoading = false, autoResult,
  autoBaseRiskMart, onAutoBaseRiskMartChange,
  autoMinConfidence = 0, onAutoMinConfidenceChange,
  autoStrategyHint = "auto", onAutoStrategyHintChange,
  dateTo,
}: Props) {

  const hasBatches = batches && batches.length > 1;
  const isPerTf = sizingMaster === "per_tf";

  // Group batches by timeframe for per-TF mode
  const tfGroups = useMemo(() => {
    if (!batches || batches.length === 0) return [];
    const map = new Map<string, BatchInfo[]>();
    for (const b of batches) {
      const tf = b.timeframe || "?";
      if (!map.has(tf)) map.set(tf, []);
      map.get(tf)!.push(b);
    }
    // Sort by timeframe numerically (1, 3, 5, 15, 30, 60, ...)
    return Array.from(map.entries()).sort((a, b) => {
      const na = parseInt(a[0]) || 9999;
      const nb = parseInt(b[0]) || 9999;
      return na - nb;
    });
  }, [batches]);

  // Multiple unique timeframes available?
  const hasMultipleTfs = tfGroups.length > 1;

  const setTfConfig = (tf: string, cfg: RiskProfileConfig) => {
    if (!onTfConfigsChange) return;
    // Force martingale scope to asset_batch for all martingale modes in per-TF
    const isMart = ["martingale", "anti_martingale", "inv_martingale", "inv_anti_martingale"].includes(cfg.mode);
    const finalCfg = isMart ? { ...cfg, martingaleScope: "asset_batch" } : cfg;
    onTfConfigsChange({ ...tfConfigs, [tf]: finalCfg });
  };

  return (
    <div>
      <button onClick={onToggle}
        className="text-xs font-medium text-ag-text-muted hover:text-ag-text flex items-center gap-1">
        <span className={`transition-transform ${show ? "rotate-90" : ""}`}>&#9654;</span>
        Position Sizing Profile
        <HintTip text="Apply a position sizing model to all trades before optimization. Default (Fixed $0) = use batch risk as-is. Per Timeframe = each timeframe group gets its own risk profile + slippage." />
      </button>

      {show && (
        <div className="mt-2 space-y-3 p-3 rounded-lg border border-ag-border bg-ag-dark/50">

          {/* Auto Risk toggle */}
          {onAutoEnabledChange && (
            <div className="flex items-center gap-3 flex-wrap">
              <label className="text-xs text-ag-text-muted w-20">Auto Risk</label>
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" checked={autoEnabled}
                  onChange={(e) => onAutoEnabledChange(e.target.checked)}
                  className="accent-ag-accent" />
                <span className="text-xs text-ag-text">Auto-select from IS stats</span>
              </label>
              <HintTip text="Automatically analyze each asset's IS trade streak patterns to determine the best risk profile. High consecutive win probability → Anti-Martingale. High consecutive loss probability → Martingale. Random/low streak probability → Fixed. You provide base risk + multiplier; max streak, start-after, reset-after are auto-derived." />
            </div>
          )}

          {/* Auto mode: simplified controls */}
          {autoEnabled && onAutoAggressionChange && (
            <AutoRiskControls
              config={config}
              onChange={onChange}
              aggression={autoAggression}
              onAggressionChange={onAutoAggressionChange}
              onAnalyze={onAutoAnalyze}
              loading={autoLoading}
              result={autoResult}
              baseRiskMart={autoBaseRiskMart ?? 50}
              onBaseRiskMartChange={onAutoBaseRiskMartChange}
              minConfidence={autoMinConfidence}
              onMinConfidenceChange={onAutoMinConfidenceChange}
              strategyHint={autoStrategyHint}
              onStrategyHintChange={onAutoStrategyHintChange}
            />
          )}

          {/* Manual mode: full controls */}
          {!autoEnabled && (
            <>
              {/* Master toggle — only show if multiple TFs available */}
              {hasBatches && hasMultipleTfs && onSizingMasterChange && (
                <div className="flex items-center gap-3 flex-wrap">
                  <label className="text-xs text-ag-text-muted w-20">Sizing</label>
                  <div className="flex gap-1">
                    {[
                      { value: "portfolio", label: "Portfolio" },
                      { value: "per_tf", label: "Per Timeframe" },
                    ].map((o) => (
                      <button key={o.value} type="button"
                        onClick={() => onSizingMasterChange(o.value)}
                        className={`px-3 py-1 text-xs rounded border transition-colors ${
                          sizingMaster === o.value
                            ? "bg-ag-accent text-white border-ag-accent"
                            : "border-ag-border text-ag-text-muted hover:border-ag-accent hover:text-ag-text"
                        }`}>{o.label}</button>
                    ))}
                  </div>
                  <HintTip text="Portfolio: one shared position sizing config for all batches. Per Timeframe: batches grouped by their timeframe — each TF group gets its own risk profile and slippage. E.g. 15m batches can have different sizing than 30m batches." />
                </div>
              )}

              {/* Portfolio mode: single config panel */}
              {!isPerTf && (
                <ConfigPanel config={config} onChange={onChange} showScope={true} />
              )}

              {/* Per-timeframe mode: one panel per unique TF, side by side */}
              {isPerTf && tfGroups.length > 0 && (
                <div className="grid gap-3" style={{
                  gridTemplateColumns: `repeat(${Math.min(tfGroups.length, 3)}, minmax(0, 1fr))`,
                }}>
                  {tfGroups.map(([tf, batchesInTf]) => {
                    const cfg = tfConfigs[tf] || { ...DEFAULT_RISK_PROFILE };
                    const tfLabel = TF_LABELS[tf] || tf;
                    const batchNames = batchesInTf.map(b => b.label).join(", ");
                    // Use the first batch ID for streak study
                    const firstBatchId = batchesInTf[0]?.id;
                    return (
                      <ConfigPanel key={tf} config={cfg}
                        onChange={(c) => setTfConfig(tf, c)}
                        showScope={false} compact showSlippage
                        label={`${tfLabel} (${batchNames})`}
                        batchId={firstBatchId} dateTo={dateTo} />
                    );
                  })}
                </div>
              )}

              {/* Per-TF mode but no batches selected */}
              {isPerTf && tfGroups.length === 0 && (
                <p className="text-xs text-ag-text-muted">Select batches above to configure per-timeframe sizing.</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
