"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { StreakStudyResult } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  batchId: number;
  dateTo?: string;
}

export default function StreakStudyPanel({ batchId, dateTo }: Props) {
  const [data, setData] = useState<StreakStudyResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.streakStudy([batchId], dateTo);
      setData(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  if (!data && !loading && !error) {
    return (
      <button onClick={load}
        className="text-[10px] text-ag-accent hover:underline">
        Run Streak Study
        <HintTip text="Analyse IS trade streak patterns to determine if Fixed, Martingale, or Anti-Martingale sizing is statistically justified for this strategy." />
      </button>
    );
  }

  if (loading) {
    return <span className="text-[10px] text-ag-text-muted animate-pulse">Analysing streaks…</span>;
  }

  if (error) {
    return (
      <div className="text-[10px] text-red-400 flex items-center gap-2">
        {error}
        <button onClick={load} className="text-ag-accent hover:underline">Retry</button>
      </div>
    );
  }

  if (!data) return null;

  const tm = data.transition_matrix;
  const rec = data.recommendation;
  const comp = data.comparison;
  const sd = data.streak_distributions;
  const pFmt = (v: number | null) => v === null ? "—" : `${(v * 100).toFixed(1)}%`;
  const confColor = rec.confidence === "high" ? "text-green-400" : rec.confidence === "medium" ? "text-yellow-400" : "text-ag-text-muted";
  const methodLabel: Record<string, string> = { fixed: "Fixed", martingale: "Martingale", anti_martingale: "Anti-Martingale" };

  return (
    <div className="space-y-2 pt-1">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium text-ag-text-muted">
          Streak Study
          <HintTip text="Uses IS trades only. Shows whether win/loss streaks are random or statistically dependent — which determines if Martingale or Anti-Martingale sizing can add value." />
        </span>
        <span className="text-[10px] text-ag-text-muted">{data.trade_count} trades (excl. breakeven)</span>
      </div>

      {/* Transition matrix */}
      <div className="grid grid-cols-2 gap-1 text-[10px]">
        <div className="bg-ag-dark rounded px-2 py-1">
          <span className="text-ag-text-muted">After Win → Win:</span>{" "}
          <span className="text-green-400 font-medium">{pFmt(tm.p_win_after_win)}</span>
        </div>
        <div className="bg-ag-dark rounded px-2 py-1">
          <span className="text-ag-text-muted">After Win → Loss:</span>{" "}
          <span className="text-red-400 font-medium">{pFmt(tm.p_loss_after_win)}</span>
        </div>
        <div className="bg-ag-dark rounded px-2 py-1">
          <span className="text-ag-text-muted">After Loss → Win:</span>{" "}
          <span className="text-green-400 font-medium">{pFmt(tm.p_win_after_loss)}</span>
        </div>
        <div className="bg-ag-dark rounded px-2 py-1">
          <span className="text-ag-text-muted">After Loss → Loss:</span>{" "}
          <span className="text-red-400 font-medium">{pFmt(tm.p_loss_after_loss)}</span>
        </div>
      </div>

      {/* Stats row */}
      <div className="flex items-center gap-3 text-[10px]">
        <span className="text-ag-text-muted">
          Autocorr: <span className="text-ag-text font-medium">{data.autocorrelation !== null ? data.autocorrelation.toFixed(4) : "—"}</span>
          <HintTip text="Positive = streaky (wins follow wins). Negative = mean-reverting (wins follow losses). ~0 = random." />
        </span>
        <span className="text-ag-text-muted">
          p-value: <span className={`font-medium ${data.chi_squared_p !== null && data.chi_squared_p < 0.05 ? "text-green-400" : "text-ag-text"}`}>
            {data.chi_squared_p !== null ? data.chi_squared_p.toFixed(4) : "—"}
          </span>
          <HintTip text="Chi-squared p-value. Below 0.05 = statistically significant streak dependence." />
        </span>
        <span className="text-ag-text-muted">
          Max streaks: <span className="text-green-400">{sd.max_win_streak}W</span> / <span className="text-red-400">{sd.max_loss_streak}L</span>
        </span>
      </div>

      {/* Recommendation */}
      <div className="bg-ag-dark rounded px-2 py-1.5 text-[10px]">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="font-medium">Recommended: {methodLabel[rec.best_method] || rec.best_method}</span>
          <span className={`px-1 py-0.5 rounded text-[9px] ${confColor} bg-ag-card`}>{rec.confidence}</span>
        </div>
        <span className="text-ag-text-muted">{rec.reason}</span>
      </div>

      {/* Simulated comparison */}
      {Object.keys(comp).length > 0 && (
        <div className="text-[10px]">
          <span className="text-ag-text-muted font-medium">Simulated Comparison (IS trades):</span>
          <div className="grid grid-cols-3 gap-1 mt-1">
            {(["fixed", "martingale", "anti_martingale"] as const).map((m) => {
              const c = comp[m];
              if (!c) return null;
              const best = rec.best_method === m;
              return (
                <div key={m} className={`bg-ag-dark rounded px-2 py-1 ${best ? "ring-1 ring-ag-accent" : ""}`}>
                  <div className="font-medium mb-0.5">{methodLabel[m]}</div>
                  <div className={c.net_profit >= 0 ? "text-green-400" : "text-red-400"}>
                    ${c.net_profit.toLocaleString()}
                  </div>
                  <div className="text-ag-text-muted">DD: {c.max_drawdown_pct.toFixed(1)}%</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Re-run button */}
      <button onClick={load} className="text-[10px] text-ag-accent hover:underline">Re-run</button>
    </div>
  );
}
