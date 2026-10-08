"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { MonteCarloResult } from "@/lib/api";
import EquityCone from "./EquityCone";
import HintTip from "@/components/common/HintTip";

type MCGrade = "A" | "B" | "C" | "F";

function gradeDDCapital(medianDD: number, capital: number): MCGrade {
  const pct = Math.abs(medianDD) / capital * 100;
  if (pct < 15) return "A";
  if (pct < 25) return "B";
  if (pct < 40) return "C";
  return "F";
}

function gradeDDPeak(ddPct: number): MCGrade {
  const pct = Math.abs(ddPct);
  if (pct < 10) return "A";
  if (pct < 20) return "B";
  if (pct < 35) return "C";
  return "F";
}

function gradeRecovery(medianReturn: number, medianDD: number): MCGrade {
  const dd = Math.abs(medianDD);
  if (dd === 0) return medianReturn > 0 ? "A" : "F";
  const ratio = medianReturn / dd;
  if (ratio >= 10) return "A";
  if (ratio >= 5) return "B";
  if (ratio >= 2) return "C";
  return "F";
}

function gradeRuin(probRuin: number): MCGrade {
  if (probRuin === 0) return "A";
  if (probRuin < 5) return "B";
  if (probRuin < 20) return "C";
  return "F";
}

const GRADE_COLORS: Record<MCGrade, string> = {
  A: "text-ag-green bg-green-900/20 border-green-700/40",
  B: "text-blue-400 bg-blue-900/20 border-blue-700/40",
  C: "text-yellow-400 bg-yellow-900/20 border-yellow-700/40",
  F: "text-ag-red bg-red-900/20 border-red-700/40",
};

function GradeCard({ label, grade, hint }: { label: string; grade: MCGrade; hint: string }) {
  return (
    <div className={`flex-1 rounded-lg border p-2.5 text-center ${GRADE_COLORS[grade]}`}>
      <p className="text-[9px] uppercase tracking-wider opacity-70 mb-0.5">{label}<HintTip text={hint} /></p>
      <p className="text-xl font-bold font-mono">{grade}</p>
    </div>
  );
}

interface MonteCarloPanelProps {
  batchId?: number;
  runId?: number;
  portfolioId?: number;
  dateFrom?: string;
  dateTo?: string;
  minTrades?: number;
  excludeTopN?: number;
  pfMin?: number;
  pfMax?: number;
  wrMin?: number;
  initialCapital?: number;
  onResult?: (result: MonteCarloResult) => void;
}

export default function MonteCarloPanel({ batchId, runId, portfolioId, dateFrom, dateTo, minTrades, excludeTopN, pfMin, pfMax, wrMin, initialCapital = 10000, onResult }: MonteCarloPanelProps) {
  const [nSims, setNSims] = useState(1000);
  const [ruinPct, setRuinPct] = useState(50);
  const [result, setResult] = useState<MonteCarloResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const runMC = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.monteCarlo(batchId, runId, nSims, ruinPct, dateTo, minTrades, portfolioId, excludeTopN, pfMin, pfMax, dateFrom, wrMin);
      setResult(res);
      if (onResult) onResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Monte Carlo failed");
    } finally {
      setLoading(false);
    }
  };

  const ruinLabel = result?.stats.ruin_threshold_pct
    ? `Prob. Ruin (${result.stats.ruin_threshold_pct}% DD)`
    : `Prob. Ruin (${ruinPct}% DD)`;

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex items-end gap-4 flex-wrap">
        <div className="flex-1 max-w-xs">
          <label className="block text-xs text-ag-text-muted mb-1">
            Simulations: {nSims.toLocaleString()}
            <HintTip text="Number of reshuffled equity paths. Each simulation randomizes trade order and computes a new equity curve. More = smoother results but slower." />
          </label>
          <input type="range" min={500} max={5000} step={500} value={nSims}
            onChange={(e) => setNSims(Number(e.target.value))}
            className="w-full accent-ag-accent" />
        </div>
        <div className="w-28">
          <label className="block text-xs text-ag-text-muted mb-1">
            Ruin DD %: {ruinPct}%
            <HintTip text="Drawdown threshold for 'ruin'. If a simulation's drawdown exceeds this % of peak equity, it counts as a ruin event. Lower = stricter survival test." />
          </label>
          <input type="range" min={10} max={90} step={5} value={ruinPct}
            onChange={(e) => setRuinPct(Number(e.target.value))}
            className="w-full accent-ag-accent" />
        </div>
        <button onClick={runMC} disabled={loading}
          className="px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
          {loading ? "Simulating..." : "Run Monte Carlo"}
        </button>
      </div>

      {error && <p className="text-sm text-ag-red">{error}</p>}

      {result && (
        <div className="space-y-4">
          {/* Stats Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            <StatCard label="Median Return" value={`$${result.stats.median_return.toFixed(2)}`} positive={result.stats.median_return > 0}
              hint="Middle simulated return. Half of all simulations end above this, half below. Shows your 'typical' outcome." />
            <StatCard label="Prob. Profit" value={`${result.stats.prob_profit.toFixed(1)}%`} positive={result.stats.prob_profit > 50}
              hint="Percentage of simulations that ended profitable. Above 80% = strong. Below 50% = strategy may not be robust." />
            <StatCard label={ruinLabel} value={`${result.stats.prob_ruin.toFixed(1)}%`} positive={result.stats.prob_ruin < 20}
              hint="Percentage of simulations where drawdown hit the ruin threshold. Below 10% = safe. Above 30% = high sequence risk." />
            <StatCard label="Median Max DD" value={`$${result.stats.median_max_dd.toFixed(2)}`}
              hint="Typical worst drawdown across all simulations. This is the drawdown you should realistically prepare for." />
            <StatCard label="Median Sharpe" value={result.stats.median_sharpe.toFixed(2)} positive={result.stats.median_sharpe > 1}
              hint="Middle Sharpe ratio across simulations. Above 1.0 = good risk-adjusted return. Above 2.0 = excellent." />
            <StatCard label="Worst Case Return" value={`$${result.stats.worst_case_return.toFixed(2)}`}
              hint="5th percentile return: only 5% of simulations did worse. This is your realistic worst-case scenario." />
            <StatCard label="Best Case Return" value={`$${result.stats.best_case_return.toFixed(2)}`} positive
              hint="95th percentile return: only 5% of simulations did better. Shows your realistic upside potential." />
            <StatCard label="Worst Max DD" value={`$${result.stats.worst_case_max_dd.toFixed(2)}`}
              hint="5th percentile max drawdown: the deepest drawdown you might face with unlucky trade ordering. Prepare for this." />
            <StatCard label="Sharpe Range" value={`${result.stats.sharpe_5th.toFixed(2)} \u2014 ${result.stats.sharpe_95th.toFixed(2)}`}
              hint="5th to 95th percentile Sharpe ratio. A tight range means the strategy's risk-reward is consistent regardless of trade order." />
            <StatCard label="N Trades" value={result.stats.n_trades.toString()}
              hint="Total number of trades used in the simulation. More trades = more reliable Monte Carlo results." />
          </div>

          {/* MC Grades */}
          <div className="flex gap-2">
            <GradeCard label="DD / Capital" grade={gradeDDCapital(result.stats.median_max_dd, initialCapital)}
              hint="Median Max Drawdown as % of initial capital. The most conservative measure — how much of your starting money you could lose. A < 15%, B < 25%, C < 40%, F >= 40%." />
            <GradeCard label="DD / Peak" grade={gradeDDPeak(result.stats.median_max_dd_pct ?? (Math.abs(result.stats.median_max_dd) / initialCapital * 100))}
              hint="Median Max Drawdown as % of peak account equity. Industry-standard drawdown measure — how much you give back from your highest point. A < 10%, B < 20%, C < 35%, F >= 35%." />
            <GradeCard label="Recovery" grade={gradeRecovery(result.stats.median_return, result.stats.median_max_dd)}
              hint="Recovery Factor = Median Return / |Median Max DD|. How much profit you earn per dollar of drawdown pain. A >= 10x, B >= 5x, C >= 2x, F < 2x." />
            <GradeCard label="Ruin Risk" grade={gradeRuin(result.stats.prob_ruin)}
              hint={`Probability of hitting the ${result.stats.ruin_threshold_pct}% drawdown ruin threshold. A = 0%, B < 5%, C < 20%, F >= 20%.`} />
          </div>

          {/* Original vs Simulated */}
          <div className="bg-ag-card border border-ag-border rounded-lg p-4">
            <h3 className="text-sm font-medium mb-2">Original vs Simulated</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-ag-text-muted">Original Return: </span>
                <span className={result.stats.original_return >= 0 ? "text-ag-green" : "text-ag-red"}>
                  ${result.stats.original_return.toFixed(2)}
                </span>
              </div>
              <div>
                <span className="text-ag-text-muted">Original Max DD: </span>
                <span className="text-ag-red">${result.stats.original_max_dd.toFixed(2)}</span>
              </div>
            </div>
          </div>

          {/* Equity Cone */}
          <div className="bg-ag-card border border-ag-border rounded-lg p-4">
            <h3 className="text-sm font-medium mb-2">Equity Cone</h3>
            <EquityCone cone={result.cone} originalEquity={result.original_equity} />
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, positive, hint }: { label: string; value: string; positive?: boolean; hint?: string }) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-3">
      <p className="text-[10px] text-ag-text-muted mb-0.5">{label}{hint && <HintTip text={hint} />}</p>
      <p className={`text-sm font-medium ${positive === true ? "text-ag-green" : positive === false ? "text-ag-red" : ""}`}>
        {value}
      </p>
    </div>
  );
}
