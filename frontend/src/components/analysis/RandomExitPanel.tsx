"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { RandomExitResult } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  portfolioId: number;
  dateFrom?: string;
  dateTo?: string;
  onResult: (result: RandomExitResult) => void;
}

export default function RandomExitPanel({ portfolioId, dateFrom, dateTo, onResult }: Props) {
  const [exitMode, setExitMode] = useState("random_bars");
  const [maxHold, setMaxHold] = useState(50);
  const [nIter, setNIter] = useState(500);
  const [result, setResult] = useState<RandomExitResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.randomExit(portfolioId, nIter, exitMode, maxHold, dateFrom, dateTo);
      setResult(res);
      onResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Random exit test failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      {/* Controls */}
      <div className="flex items-end gap-4 flex-wrap">
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">
            Exit Mode<HintTip text="Random Bars: exit after a random number of bars. Random ATR: exit when price moves a random ATR multiple from entry." />
          </label>
          <select value={exitMode} onChange={(e) => setExitMode(e.target.value)}
            className="bg-ag-dark border border-ag-border rounded px-2 py-1.5 text-xs">
            <option value="random_bars">Random Bars</option>
            <option value="random_atr">Random ATR</option>
          </select>
        </div>
        <div className="flex-1 max-w-[160px]">
          <label className="block text-xs text-ag-text-muted mb-1">
            Max Hold: {maxHold} bars<HintTip text="Maximum bars a random trade can stay open. If no ATR target is hit within this window, the trade closes at current price." />
          </label>
          <input type="range" min={5} max={100} step={5} value={maxHold}
            onChange={(e) => setMaxHold(Number(e.target.value))}
            className="w-full accent-ag-accent" />
        </div>
        <div className="flex-1 max-w-[160px]">
          <label className="block text-xs text-ag-text-muted mb-1">
            Iterations: {nIter}<HintTip text="Number of random exit simulations. Each iteration randomizes ALL exits simultaneously and computes a portfolio Profit Factor." />
          </label>
          <input type="range" min={100} max={1000} step={100} value={nIter}
            onChange={(e) => setNIter(Number(e.target.value))}
            className="w-full accent-ag-accent" />
        </div>
        <button onClick={run} disabled={loading}
          className="px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
          {loading ? "Running..." : "Run Exit Test"}
        </button>
      </div>

      {error && <p className="text-sm text-ag-red">{error}</p>}

      {result && (
        <div className="space-y-3">
          {/* Key metrics */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            <StatCard label="Actual PF" value={result.actual_pf.toFixed(2)} positive
              hint="Your strategy's real Profit Factor using its actual exit logic (TP/SL/TSL). This is what you're testing against random exits." />
            <StatCard label="Avg Random PF" value={result.mean_random_pf.toFixed(2)}
              hint="Average Profit Factor when all exits are randomized. If your exit logic has no edge, your Actual PF would be close to this value." />
            <StatCard label="Z-Score" value={result.z_score.toFixed(2)} positive={result.z_score >= 2}
              hint="How many standard deviations your exit logic outperforms random exits. Z \u2265 2.0 = statistically significant (p < 0.023). Z \u2265 3.0 = strong edge. Z < 1.0 = no proven edge." />
            <StatCard label="p-value" value={result.p_value < 0.001 ? result.p_value.toExponential(2) : result.p_value.toFixed(4)}
              positive={result.p_value < 0.05}
              hint="Probability of seeing this result by pure chance. p < 0.05 = significant, p < 0.01 = very significant, p < 0.001 = highly significant. Lower = stronger evidence your exit logic has a real edge." />
            <StatCard label="Alpha Gap" value={result.alpha_gap.toFixed(2)} positive={result.alpha_gap > 0}
              hint="Difference between your Actual PF and average random PF. Larger gap = stronger exit edge. Negative gap = your exits are WORSE than random." />
          </div>

          {/* Significance badge */}
          <div className={`text-xs font-medium px-3 py-2 rounded-lg border ${
            result.is_significant
              ? "text-ag-green bg-green-900/15 border-green-700/30"
              : "text-ag-red bg-red-900/15 border-red-700/30"
          }`}>
            {result.is_significant
              ? `Edge Confirmed (Z = ${result.z_score.toFixed(1)}, p < ${result.p_value < 0.001 ? "0.001" : result.p_value.toFixed(3)})`
              : `No Significant Edge (Z = ${result.z_score.toFixed(1)}) — exit logic not provably better than random`}
          </div>

          {/* PF Histogram */}
          <div className="bg-ag-card border border-ag-border rounded-lg p-4">
            <h4 className="text-sm font-medium mb-2">
              Random PF Distribution ({result.n_trades_tested} trades, {result.n_iterations} iterations)
            </h4>
            <PFHistogram randomPFs={result.random_pfs} actualPF={result.actual_pf} />
          </div>
        </div>
      )}
    </div>
  );
}

function PFHistogram({ randomPFs, actualPF }: { randomPFs: number[]; actualPF: number }) {
  const W = 700, H = 200, PAD = { top: 10, right: 20, bottom: 30, left: 40 };
  const pw = W - PAD.left - PAD.right;
  const ph = H - PAD.top - PAD.bottom;

  // Build histogram bins
  const all = [...randomPFs, actualPF];
  const mn = Math.min(...all), mx = Math.max(...all);
  const range = mx - mn || 1;
  const nBins = 30;
  const binW = range / nBins;
  const bins = Array.from({ length: nBins }, () => 0);
  for (const v of randomPFs) {
    const idx = Math.min(Math.floor((v - mn) / binW), nBins - 1);
    bins[idx]++;
  }
  const maxCount = Math.max(...bins, 1);

  const xScale = (v: number) => PAD.left + ((v - mn) / range) * pw;
  const yScale = (c: number) => PAD.top + ph - (c / maxCount) * ph;
  const barPx = pw / nBins;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="xMidYMid meet">
      {/* Bars */}
      {bins.map((c, i) => (
        <rect key={i} x={PAD.left + i * barPx} y={yScale(c)}
          width={barPx - 1} height={ph - (yScale(c) - PAD.top)}
          fill="rgba(59,130,246,0.35)" stroke="rgba(59,130,246,0.5)" strokeWidth={0.5} />
      ))}
      {/* Actual PF line */}
      <line x1={xScale(actualPF)} y1={PAD.top} x2={xScale(actualPF)} y2={PAD.top + ph}
        stroke="#ef4444" strokeWidth={2} strokeDasharray="4 2" />
      <text x={xScale(actualPF) + 4} y={PAD.top + 12} fontSize={9} fill="#ef4444" fontWeight="bold">
        Actual: {actualPF.toFixed(2)}
      </text>
      {/* X-axis labels */}
      {[0, 0.25, 0.5, 0.75, 1].map(frac => {
        const v = mn + range * frac;
        return (
          <text key={frac} x={xScale(v)} y={H - 8} textAnchor="middle" fontSize={9} fill="var(--color-ag-text-muted)">
            {v.toFixed(2)}
          </text>
        );
      })}
      <text x={PAD.left + pw / 2} y={H - 0} textAnchor="middle" fontSize={10} fill="var(--color-ag-text-muted)">
        Profit Factor
      </text>
    </svg>
  );
}

function StatCard({ label, value, positive, hint }: { label: string; value: string; positive?: boolean; hint?: string }) {
  return (
    <div className="bg-ag-dark border border-ag-border rounded-lg p-2">
      <p className="text-[10px] text-ag-text-muted">{label}{hint && <HintTip text={hint} />}</p>
      <p className={`text-sm font-medium ${positive === true ? "text-ag-green" : positive === false ? "text-ag-red" : ""}`}>
        {value}
      </p>
    </div>
  );
}
