"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { FatTailBootstrapResult } from "@/lib/api";
import EquityCone from "./EquityCone";
import HintTip from "@/components/common/HintTip";

interface Props {
  portfolioId: number;
  dateFrom?: string;
  dateTo?: string;
  onResult: (result: FatTailBootstrapResult) => void;
}

export default function FatTailBootstrapPanel({ portfolioId, dateFrom, dateTo, onResult }: Props) {
  const [nSims, setNSims] = useState(1000);
  const [outlierPct, setOutlierPct] = useState(5);
  const [withReplacement, setWithReplacement] = useState(true);
  const [result, setResult] = useState<FatTailBootstrapResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.fatTailBootstrap(portfolioId, nSims, outlierPct, withReplacement, dateFrom, dateTo);
      setResult(res);
      onResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fat-tail bootstrap failed");
    } finally {
      setLoading(false);
    }
  };

  const fragile = result && result.stats.fragility_ratio < 0.6;

  return (
    <div className="space-y-3">
      {/* Controls */}
      <div className="flex items-end gap-4 flex-wrap">
        <div className="flex-1 max-w-[160px]">
          <label className="block text-xs text-ag-text-muted mb-1">
            Outlier %: {outlierPct}%
            <HintTip text="Top X% of trades by profit are force-included in every simulation. These are your 'Power Trades' \u2014 the big winners that define trend-following." />
          </label>
          <input type="range" min={1} max={20} step={1} value={outlierPct}
            onChange={(e) => setOutlierPct(Number(e.target.value))}
            className="w-full accent-ag-accent" />
        </div>
        <div className="flex-1 max-w-[160px]">
          <label className="block text-xs text-ag-text-muted mb-1">
            Simulations: {nSims.toLocaleString()}
            <HintTip text="Number of randomized equity paths to generate. More = smoother results but slower." />
          </label>
          <input type="range" min={500} max={5000} step={500} value={nSims}
            onChange={(e) => setNSims(Number(e.target.value))}
            className="w-full accent-ag-accent" />
        </div>
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">
            Sampling Mode
            <HintTip text="Bootstrap: resampling WITH replacement \u2014 same trade can appear multiple times, profit varies across simulations. Tests structural stability. Shuffle: WITHOUT replacement \u2014 each trade picked at most once, only order changes." />
          </label>
          <select
            value={withReplacement ? "bootstrap" : "shuffle"}
            onChange={(e) => setWithReplacement(e.target.value === "bootstrap")}
            className="bg-ag-dark border border-ag-border rounded px-2 py-1.5 text-xs"
          >
            <option value="bootstrap">Bootstrap (With Replacement)</option>
            <option value="shuffle">Shuffle (No Replacement)</option>
          </select>
        </div>
        <button onClick={run} disabled={loading}
          className="px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
          {loading ? "Running..." : "Run Fat-Tail Test"}
        </button>
      </div>

      {error && <p className="text-sm text-ag-red">{error}</p>}

      {result && (
        <div className="space-y-3">
          {/* Stats grid */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard label="Survival Prob" value={`${result.stats.survival_prob}%`}
              positive={result.stats.survival_prob > 50}
              hint="Percentage of simulations that ended with positive equity. Above 80% = strong survivability. Below 50% = strategy may collapse under stress." />
            <StatCard label="Fragility Ratio" value={result.stats.fragility_ratio.toFixed(2)}
              positive={result.stats.fragility_ratio >= 0.6}
              hint="Median simulation profit / original profit. 1.0 = simulations match reality. Above 0.85 = robust (Grade A). Below 0.50 = fragile, too dependent on specific trade sequence." />
            <StatCard label="Power Trades" value={`${result.power_trades.count}`}
              hint={`Number of top ${outlierPct}% trades force-included in every simulation. These are your biggest winners that the strategy depends on.`} />
            <StatCard label="Power % of Profit" value={`${result.power_trades.pct_of_profit}%`}
              hint="What percentage of total profit comes from Power Trades. If above 50%, the strategy is heavily dependent on a few big wins \u2014 typical for trend-following but risky." />
            <StatCard label="Median Return" value={`$${result.stats.median_return.toFixed(0)}`}
              positive={result.stats.median_return > 0}
              hint="Middle simulated end equity across all paths. Positive = strategy likely profitable even with randomized trade selection." />
            <StatCard label="Worst Case" value={`$${result.stats.worst_case_return.toFixed(0)}`}
              hint="5th percentile end equity \u2014 the worst realistic outcome. If this is deeply negative, the strategy has high structural risk." />
          </div>

          {fragile && (
            <p className="text-xs text-ag-red font-medium">
              Strategy is fragile &mdash; too dependent on perfect conditions (fragility ratio &lt; 0.6)
            </p>
          )}

          {/* Equity Cone */}
          <div className="bg-ag-card border border-ag-border rounded-lg p-4">
            <h4 className="text-sm font-medium mb-2">Fat-Tail Equity Cone</h4>
            <EquityCone cone={result.cone} originalEquity={result.original_equity} />
          </div>
        </div>
      )}
    </div>
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
