"use client";
import { useState } from "react";
import HintTip from "@/components/common/HintTip";

/* ── Preset config type ─────────────────────────── */
export interface ResearchPresetConfigEvent {
  mode: "event";
  n_trades: number;
  n_assets: number;
  rebalance_every: number;
  rebalance_mode?: string;
  min_pf: number;
  min_wr: number;
  staleness_days: number;
  date_from: string;
  scoring_method: "single" | "composite";
  objective?: string;
  composite_weights?: { sortino: number; pf: number; recovery: number; wr: number };
  exec_day_profit: boolean;
  exec_exclude_days?: number[];
  min_hold?: number;
  warmup_trades?: number;
}

export interface ResearchPresetConfigBatchSelection {
  mode: "batch_selection";
  is_months: number;
  oos_months: number;
  // Pin window boundaries for reproducibility (optional — defaults to auto)
  last_oos_end?: string;
  data_start?: string;
  bs_max_batches: number;
  bs_assets_per_batch: number;
  bs_batch_rank_metric: string;
  bs_cal_rank_metric: string;
  bs_asset_rank_metric: string;
  bs_n_candidates?: number;
  bs_min_calibrated?: number;
  bs_min_pf?: number;
  bs_min_is_days?: number;
  bs_min_asset_days?: number;
  bs_asset_must_profit?: boolean;
  bs_cal_must_profit?: boolean;
  bs_max_tpm?: number;
  bs_asset_wr_gate?: number;
  // Advanced features
  bs_recency_weight?: number;
  bs_adaptive_mb?: boolean;
  bs_adaptive_mb_min?: number;
  bs_adaptive_mb_max?: number;
  bs_adaptive_mb_gap_pct?: number;
  bs_ensemble_cal_k?: number;
  bs_regime_weight?: number;
  bs_elo_iterations?: number;
  // Research-validated features (co-work sweeps)
  bs_l2_groups?: boolean;
  bs_pruning_enabled?: boolean;
  bs_prune_long?: number;
  bs_prune_short?: number;
  bs_past_oos_confirm?: boolean;
  bs_q_starts?: number;
  // PnL Scaling (position sizing)
  pnl_scaling_mode?: "group" | "custom" | "per_batch" | "none";
  pnl_scaling_custom?: number;
  session_filter?: {
    enabled: boolean;
    mode: "fixed" | "auto" | "saved_mask";
    excludeDays: number[];
    excludeHours: number[];
    gradeOnCropped?: boolean;
  };
  // 2-Step Hybrid (Step 2 composite scoring) — optional extras
  hybrid_mode?: boolean;
  hybrid_n_trades?: number;
  hybrid_n_assets?: number;
  hybrid_min_pf?: number;
  hybrid_min_wr?: number;
  hybrid_staleness_days?: number;
  hybrid_rebalance_every?: number;
  hybrid_rebalance_mode?: string;
  hybrid_warmup_trades?: number;
  hybrid_min_hold?: number;
  hybrid_scoring_method?: "single" | "composite";
  hybrid_objective?: string;
  hybrid_composite_weights?: Partial<{ sortino: number; pf: number; recovery: number; wr: number; calmar: number; ulcer: number; max_dd: number }>;
  hybrid_exec_day_profit?: boolean;
  // Overfit guard extras (sometimes set by older saved configs)
  bs_overfit_guard_enabled?: boolean;
  bs_overfit_guard_mode?: string;
  // Include live/current window for forward-looking asset selection
  bs_include_live_window?: boolean;
  // Per-preset batch filter — limit auto-selection to specific groups by ID.
  // 1=A, 2=B, 3=C (queryable from batch_groups table). When omitted, ALL
  // completed batches are auto-selected (legacy behaviour).
  bs_group_filter?: number[];
  // ── Category filter (2026-04-17 — on-fire-streak event mode) ─────
  // All fields optional + backward-compatible. When category_mode is
  // omitted or "tertile" the pre-2026-04-17 behavior is preserved.
  category_filter_enabled?: boolean;
  category_influence?: "none" | "low" | "mid" | "high" | "all" | "event";
  category_mode?: "tertile" | "streak_event";
  category_lookback_trades?: number;
  category_streak_required?: number;       // consecutive winners to trigger "on fire"
  category_fire_multiplier?: number;       // score boost while on fire (e.g. 1.30)
  category_fire_duration_trades?: number;  // trailing window after streak
  // Tertile-mode scoring + refresh cadence + per-category cap (P7 addition)
  category_score_metric?: "single" | "composite";
  category_composite_weights?: Partial<{ sortino: number; pf: number; recovery: number; wr: number; calmar: number }>;
  category_refresh_mode?: "per_window" | "per_rebalance" | "static";
  category_max_per_cat?: number;           // 0 = unlimited, >0 = hard cap
}

export type ResearchPresetConfig = ResearchPresetConfigEvent | ResearchPresetConfigBatchSelection;


export interface ResearchPreset {
  id: string;
  name: string;
  description: string;
  conversation: string;
  config: ResearchPresetConfig;
}

/* Research presets (saved configurations and their results) are kept
 * private. The panel works the same way; it just starts empty. */
export const RESEARCH_PRESETS: ResearchPreset[] = [];

/* ── Component ──────────────────────────────────── */
interface Props {
  onApply: (preset: ResearchPreset) => void;
}

export default function WFOResearchPresets({ onApply }: Props) {
  const [selectedConv, setSelectedConv] = useState<string>("");
  const [selectedPreset, setSelectedPreset] = useState<string>("");
  const [bestOnly, setBestOnly] = useState<boolean>(false);

  // Unique conversation list, sorted (newest first by reverse-alpha which usually matches date prefix).
  const conversations = Array.from(new Set(RESEARCH_PRESETS.map(p => p.conversation))).sort().reverse();

  // Filter presets by selected conversation
  const conversationPresets = selectedConv
    ? RESEARCH_PRESETS.filter(p => p.conversation === selectedConv)
    : [];

  // "Best only" filter — keeps presets with winner/star/new markers
  const filteredPresets = bestOnly
    ? conversationPresets.filter(p => /[✅⭐🏆🆕]/.test(p.name))
    : conversationPresets;

  const handleApply = () => {
    const p = RESEARCH_PRESETS.find(pr => pr.id === selectedPreset);
    if (p) onApply(p);
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex gap-2 items-center flex-wrap">
        <select
          value={selectedConv}
          onChange={(e) => { setSelectedConv(e.target.value); setSelectedPreset(""); }}
          className="flex-1 min-w-0 px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs">
          <option value="">— Conversation ({conversations.length}) —</option>
          {conversations.map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        {selectedConv && (
          <label className="flex items-center gap-1 text-[10px] text-ag-text-muted">
            <input type="checkbox" checked={bestOnly} onChange={(e) => setBestOnly(e.target.checked)}
              className="accent-ag-accent w-3 h-3" />
            Best only
            <HintTip text="Show only presets marked with ✅ (winners / key findings). Hides WORSE, PLATEAU, UNTESTED, and redundant variants." />
          </label>
        )}
        {selectedConv && (
          <select value={selectedPreset} onChange={(e) => setSelectedPreset(e.target.value)}
            className="flex-1 min-w-0 px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs">
            <option value="">— Select Preset —({filteredPresets.length})</option>
            {filteredPresets.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        )}
        {selectedPreset && (
          <button onClick={handleApply}
            className="px-3 py-1 bg-ag-accent/15 text-ag-accent rounded text-xs font-medium hover:bg-ag-accent/25 transition-colors whitespace-nowrap">
            Apply
          </button>
        )}
      </div>
      {selectedPreset && (() => {
        const p = RESEARCH_PRESETS.find(pr => pr.id === selectedPreset);
        return p ? (
          <p className="text-[10px] text-ag-text-muted/70 pl-1">{p.description}</p>
        ) : null;
      })()}
    </div>
  );
}
