"use client";
import { useState, useEffect, useMemo, useCallback } from "react";
import { Loader2, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import type { BatchResultSummary, BatchSymbolResult } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  batchId: number;
  dateTo?: string;
  minTrades?: number;
}

type SortKey = "symbol" | "regime" | "trend_grade" | "meanrev_grade" | "Hurst" | "TER" | "VR" | "AC1" | "DSR" | "GPR" | "net_profit" | "profit_factor" | "win_rate" | "avg_consec_bull" | "avg_consec_bear" | "avg_consec_all" | "pass";
type SortDir = "asc" | "desc";

function fmtNum(v: unknown): string {
  const n = Number(v ?? 0);
  if (!isFinite(n)) return "—";
  return n.toFixed(4);
}

function gradeOrder(g: string): number {
  const map: Record<string, number> = { "A+": 7, A: 6, "B+": 5, B: 4, "C+": 3, C: 2, D: 1, F: 0 };
  return map[g] ?? -1;
}

export default function StructuralQualityPanel({ batchId, dateTo, minTrades: masterMinTrades }: Props) {
  const [detail, setDetail] = useState<BatchResultSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Data source toggle: OHLCV (asset) vs PnL (strategy)
  const [dataSource, setDataSource] = useState<"ohlcv" | "pnl">("ohlcv");
  const pfx = dataSource === "pnl" ? "pnl_" : "";

  // Filter state
  const [mode, setMode] = useState<"trend" | "meanrev">("trend");
  const [hurstMin, setHurstMin] = useState(0.50);
  const [terPercentile, setTerPercentile] = useState(25);
  const [hurstMax, setHurstMax] = useState(0.50);
  const [vrMax, setVrMax] = useState(1.0);
  const [minCrossingRate, setMinCrossingRate] = useState(0.05);

  // Avg range threshold filters
  const [minAvgBull, setMinAvgBull] = useState(0);
  const [minAvgBear, setMinAvgBear] = useState(0);
  const [minAvgAll, setMinAvgAll] = useState(0);

  // Grade gate filters (empty = no filter)
  const [minTrendGrade, setMinTrendGrade] = useState("");
  const [minMrGrade, setMinMrGrade] = useState("");

  // Auto-zoom grading mode (min trades comes from master filter prop)
  const [gradeMode, setGradeMode] = useState<"absolute" | "autozoom">("absolute");
  const minTrades = masterMinTrades ?? 0;

  // Include/Exclude failed assets toggle
  const [includeFailed, setIncludeFailed] = useState(true);

  // Sort state
  const [sortKey, setSortKey] = useState<SortKey>("symbol");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // Repair state
  const [repairing, setRepairing] = useState(false);
  const [repairResult, setRepairResult] = useState<string | null>(null);

  const loadData = useCallback(() => {
    setLoading(true);
    setError("");
    api.batchRunDetail(batchId, { dateTo })
      .then((d) => setDetail(d))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [batchId, dateTo]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleRepair = async () => {
    setRepairing(true);
    setRepairResult(null);
    try {
      const res = await api.repairStructuralMetrics(batchId);
      setRepairResult(`Repaired ${res.repaired}/${res.total} symbols`);
      loadData(); // reload data to show updated metrics
    } catch (e: unknown) {
      setRepairResult(e instanceof Error ? e.message : "Repair failed");
    } finally {
      setRepairing(false);
    }
  };

  // Compute pass/fail for each symbol
  const results = useMemo(() => {
    if (!detail) return [];
    const symbols = detail.per_symbol;

    // For TER percentile: compute the percentile threshold from all TER values
    const terValues = symbols
      .map((s) => Number(s.metrics?.[`${pfx}TER`] ?? 0))
      .filter((v) => v > 0)
      .sort((a, b) => a - b);
    const terThreshold = terValues.length > 0
      ? terValues[Math.floor(terValues.length * terPercentile / 100)] ?? 0
      : 0;

    // Auto-zoom: collect all scores for normalization
    const allTrend = symbols.map(s => Number(s.metrics?.[`${pfx}trend_score`] ?? 0));
    const allMr = symbols.map(s => Number(s.metrics?.[`${pfx}meanrev_score`] ?? 0));
    const zoomNorm = (score: number, all: number[]): number => {
      const lo = Math.min(...all);
      const hi = Math.max(...all);
      if (hi <= lo) return 0.15;
      return (score - lo) / (hi - lo);
    };
    const toGrade = (score: number): string => {
      if (score >= 0.45) return "A+";
      if (score >= 0.35) return "A";
      if (score >= 0.28) return "B+";
      if (score >= 0.22) return "B";
      if (score >= 0.16) return "C+";
      if (score >= 0.10) return "C";
      if (score >= 0.05) return "D";
      return "F";
    };

    return symbols.map((s) => {
      const m = s.metrics ?? {};
      const hurst = Number(m[`${pfx}Hurst`] ?? 0);
      const ter = Number(m[`${pfx}TER`] ?? 0);
      const vr = Number(m[`${pfx}VR5`] ?? 0);
      const cr = Number(m[`${pfx}CrossRate`] ?? 0);

      let pass = true;
      let reason = "";

      if (mode === "trend") {
        if (hurst < hurstMin) {
          pass = false;
          reason = `Hurst ${hurst.toFixed(3)} < ${hurstMin}`;
        } else if (ter < terThreshold) {
          pass = false;
          reason = `TER ${ter.toFixed(3)} < p${terPercentile} (${terThreshold.toFixed(3)})`;
        }
      } else {
        if (hurst > hurstMax) {
          pass = false;
          reason = `Hurst ${hurst.toFixed(3)} > ${hurstMax}`;
        } else if (vr >= vrMax) {
          pass = false;
          reason = `VR ${vr.toFixed(3)} >= ${vrMax}`;
        } else if (cr < minCrossingRate) {
          pass = false;
          reason = `CR ${cr.toFixed(3)} < ${minCrossingRate}`;
        }
      }

      // Compute display grades (absolute or auto-zoom)
      const rawTs = Number(m[`${pfx}trend_score`] ?? 0);
      const rawMs = Number(m[`${pfx}meanrev_score`] ?? 0);
      const useZoom = gradeMode === "autozoom" && dataSource === "ohlcv";
      const displayTrendGrade = useZoom
        ? toGrade(zoomNorm(rawTs, allTrend))
        : String(m[`${pfx}trend_grade`] ?? "");
      const displayMrGrade = useZoom
        ? toGrade(zoomNorm(rawMs, allMr))
        : String(m[`${pfx}meanrev_grade`] ?? "");

      // Grade gate filter (uses display grades)
      let gradeHidden = false;
      if (minTrendGrade && displayTrendGrade) {
        if (gradeOrder(displayTrendGrade) < gradeOrder(minTrendGrade)) gradeHidden = true;
      }
      if (minMrGrade && displayMrGrade) {
        if (gradeOrder(displayMrGrade) < gradeOrder(minMrGrade)) gradeHidden = true;
      }

      // Avg range filters
      const ab = Number(m.avg_consec_bull ?? 0);
      const abr = Number(m.avg_consec_bear ?? 0);
      const aall = Number(m.avg_consec_all ?? 0);
      let rangeHidden = false;
      if (minAvgBull > 0 && ab > 0 && ab < minAvgBull) rangeHidden = true;
      if (minAvgBear > 0 && abr > 0 && abr < minAvgBear) rangeHidden = true;
      if (minAvgAll > 0 && aall > 0 && aall < minAvgAll) rangeHidden = true;

      // Min trades filter
      const totalTrades = Number(m.total_trades ?? 0);
      const tradesHidden = minTrades > 0 && totalTrades < minTrades;

      return { sym: s, pass, reason, gradeHidden, rangeHidden, tradesHidden, displayTrendGrade, displayMrGrade };
    });
  }, [detail, mode, hurstMin, terPercentile, hurstMax, vrMax, minCrossingRate, pfx, minTrendGrade, minMrGrade, minAvgBull, minAvgBear, minAvgAll, gradeMode, minTrades]);

  // Sort results
  const sorted = useMemo(() => {
    const copy = [...results];
    copy.sort((a, b) => {
      const ma = a.sym.metrics ?? {};
      const mb = b.sym.metrics ?? {};
      let cmp = 0;

      switch (sortKey) {
        case "symbol":
          cmp = a.sym.symbol.localeCompare(b.sym.symbol);
          break;
        case "regime":
          cmp = String(ma[`${pfx}regime`] ?? "").localeCompare(String(mb[`${pfx}regime`] ?? ""));
          break;
        case "trend_grade":
          cmp = gradeOrder(a.displayTrendGrade) - gradeOrder(b.displayTrendGrade);
          break;
        case "meanrev_grade":
          cmp = gradeOrder(a.displayMrGrade) - gradeOrder(b.displayMrGrade);
          break;
        case "pass":
          cmp = (a.pass ? 1 : 0) - (b.pass ? 1 : 0);
          break;
        case "net_profit":
        case "profit_factor":
        case "win_rate":
        case "avg_consec_bull":
        case "avg_consec_bear":
        case "avg_consec_all":
          cmp = Number(ma[sortKey] ?? 0) - Number(mb[sortKey] ?? 0);
          break;
        default:
          cmp = Number(ma[`${pfx}${sortKey}`] ?? 0) - Number(mb[`${pfx}${sortKey}`] ?? 0);
      }

      return sortDir === "desc" ? -cmp : cmp;
    });
    return copy;
  }, [results, sortKey, sortDir, pfx]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  // Apply grade + avg range visibility filters + optional exclude-failed
  const visible = useMemo(() => sorted.filter((r) =>
    !r.gradeHidden && !r.rangeHidden && !r.tradesHidden && (includeFailed || r.pass)
  ), [sorted, includeFailed]);

  // PnL summary — includes all visible when includeFailed, only passing when excluded
  const summary = useMemo(() => {
    // When includeFailed: count ALL visible (pass + fail). When exclude: visible = pass-only already.
    const pool = includeFailed ? visible : visible;
    const pnls: number[] = [];
    let pfSum = 0;
    let wrSum = 0;
    let pfCount = 0;
    let wrCount = 0;
    for (const r of pool) {
      const m = r.sym.metrics ?? {};
      pnls.push(Number(m.net_profit ?? 0));
      const pf = Number(m.profit_factor ?? 0);
      if (pf > 0) { pfSum += pf; pfCount++; }
      const wr = Number(m.win_rate ?? 0);
      if (wr > 0) { wrSum += wr; wrCount++; }
    }
    const totalPnl = pnls.reduce((a, b) => a + b, 0);
    const avgPnl = pnls.length > 0 ? totalPnl / pnls.length : 0;
    const sortedPnls = [...pnls].sort((a, b) => a - b);
    const medianPnl = sortedPnls.length > 0
      ? sortedPnls.length % 2 === 1
        ? sortedPnls[Math.floor(sortedPnls.length / 2)]
        : (sortedPnls[sortedPnls.length / 2 - 1] + sortedPnls[sortedPnls.length / 2]) / 2
      : 0;
    // Collect PF values for median calculation
    const pfValues: number[] = [];
    for (const r of pool) {
      const pf = Number((r.sym.metrics ?? {}).profit_factor ?? 0);
      if (pf > 0) pfValues.push(pf);
    }
    const sortedPfs = [...pfValues].sort((a, b) => a - b);
    const medianPf = sortedPfs.length > 0
      ? sortedPfs.length % 2 === 1
        ? sortedPfs[Math.floor(sortedPfs.length / 2)]
        : (sortedPfs[sortedPfs.length / 2 - 1] + sortedPfs[sortedPfs.length / 2]) / 2
      : 0;
    return {
      count: pool.length,
      totalPnl,
      avgPnl,
      medianPnl,
      avgPf: pfCount > 0 ? pfSum / pfCount : 0,
      medianPf,
      avgWr: wrCount > 0 ? wrSum / wrCount : 0,
    };
  }, [visible, includeFailed]);

  const passCount = results.filter((r) => r.pass && !r.gradeHidden && !r.rangeHidden && !r.tradesHidden).length;
  const totalCount = results.length;
  const visibleCount = visible.length;
  const failCount = visibleCount - passCount;

  if (loading) return <p className="text-xs text-ag-text-muted py-6 text-center">Loading structural metrics...</p>;
  if (error) return <p className="text-xs text-red-400 py-6 text-center">{error}</p>;
  if (!detail) return null;

  const sortIcon = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " \u25B2" : " \u25BC") : "";

  return (
    <div className="space-y-4 py-4">
      {/* Header + Controls */}
      <div className="bg-ag-card border border-ag-border rounded-lg p-4 space-y-3">
        <div className="flex items-center gap-3 flex-wrap">
          <h3 className="text-sm font-medium">Pre-Filter Explorer</h3>
          <HintTip text="Simulate pre-filter settings on your batch results. See which assets would pass or fail based on their structural metrics (Hurst, TER, VR, etc). Adjust the thresholds to find the right balance." />
          <div className="flex items-center gap-2 ml-auto">
            <button
              onClick={handleRepair}
              disabled={repairing}
              className="flex items-center gap-1.5 px-3 py-1 text-[10px] font-medium rounded-lg border border-ag-border hover:border-ag-accent/50 text-ag-text-muted hover:text-ag-text transition-colors disabled:opacity-50"
              title="Re-compute both OHLCV and PnL structural metrics for all symbols in this batch"
            >
              {repairing ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
              Repair Metrics
            </button>
            {repairResult && (
              <span className="text-[10px] text-ag-green">{repairResult}</span>
            )}
            <div className="flex rounded-lg border border-ag-border overflow-hidden">
              <button
                onClick={() => setDataSource("ohlcv")}
                className={`px-3 py-1 text-[10px] font-medium transition-colors ${
                  dataSource === "ohlcv" ? "bg-ag-accent text-white" : "bg-ag-dark text-ag-text-muted hover:text-ag-text"
                }`}
              >Asset (OHLCV)</button>
              <button
                onClick={() => setDataSource("pnl")}
                className={`px-3 py-1 text-[10px] font-medium transition-colors ${
                  dataSource === "pnl" ? "bg-ag-accent text-white" : "bg-ag-dark text-ag-text-muted hover:text-ag-text"
                }`}
              >Strategy (PnL)</button>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted">Mode:<HintTip text="Trend-Following: keeps assets with high Hurst/TER. Mean-Reversion: keeps assets with low Hurst, low VR, high crossing rate." /></label>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as "trend" | "meanrev")}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs"
            >
              <option value="trend">Trend-Following</option>
              <option value="meanrev">Mean-Reversion</option>
            </select>
          </div>

          {mode === "trend" ? (
            <>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Min Hurst:<HintTip text="Minimum Hurst exponent. Assets below this are removed. 0.50 = random walk boundary." /></label>
                <input type="number" min={0.3} max={0.8} step={0.01} value={hurstMin}
                  onChange={(e) => setHurstMin(Number(e.target.value))}
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">TER Percentile:<HintTip text="Remove bottom N% by Trend Efficiency Ratio. 25 = cut the worst quarter." /></label>
                <input type="number" min={0} max={75} step={5} value={terPercentile}
                  onChange={(e) => setTerPercentile(Number(e.target.value))}
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Max Hurst:<HintTip text="Max Hurst exponent. Assets above this are trending (bad for mean-rev)." /></label>
                <input type="number" min={0.3} max={0.7} step={0.01} value={hurstMax}
                  onChange={(e) => setHurstMax(Number(e.target.value))}
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Max VR:<HintTip text="Max Variance Ratio. VR < 1.0 = mean-reverting. VR >= 1.0 = trending." /></label>
                <input type="number" min={0.5} max={1.5} step={0.05} value={vrMax}
                  onChange={(e) => setVrMax(Number(e.target.value))}
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Min Cross Rate:<HintTip text="Minimum crossing rate. More crossings = more mean-rev opportunities." /></label>
                <input type="number" min={0} max={0.5} step={0.01} value={minCrossingRate}
                  onChange={(e) => setMinCrossingRate(Number(e.target.value))}
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
            </>
          )}
        </div>

        {/* Grade Gate + Avg Range Filters */}
        <div className="flex items-center gap-4 flex-wrap border-t border-ag-border/50 pt-3">
          <div className="flex items-center gap-2">
            <label className="text-[10px] text-ag-text-muted">Grading:<HintTip text="Absolute: fixed score thresholds (A+ >= 0.45, etc). Auto-Zoom: normalizes scores within this batch to 0-1 before grading — spreads grades A+ to F even when all scores cluster in a narrow range." /></label>
            <div className="flex rounded-lg border border-ag-border overflow-hidden">
              <button onClick={() => setGradeMode("absolute")}
                className={`px-2 py-0.5 text-[10px] font-medium transition-colors ${
                  gradeMode === "absolute" ? "bg-ag-accent text-white" : "bg-ag-dark text-ag-text-muted hover:text-ag-text"
                }`}>Absolute</button>
              <button onClick={() => setGradeMode("autozoom")}
                className={`px-2 py-0.5 text-[10px] font-medium transition-colors ${
                  gradeMode === "autozoom" ? "bg-ag-accent text-white" : "bg-ag-dark text-ag-text-muted hover:text-ag-text"
                }`}>Auto-Zoom</button>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-[10px] text-ag-text-muted">Min Trend Grade:<HintTip text="Hide assets with Trend grade below this. A+ = best, F = worst. Empty = show all." /></label>
            <select value={minTrendGrade} onChange={(e) => setMinTrendGrade(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
              <option value="">Any</option>
              <option value="A+">A+</option>
              <option value="A">A</option>
              <option value="B+">B+</option>
              <option value="B">B</option>
              <option value="C+">C+</option>
              <option value="C">C</option>
              <option value="D">D</option>
            </select>
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-[10px] text-ag-text-muted">Min MR Grade:<HintTip text="Hide assets with Mean-Rev grade below this. A+ = best, F = worst. Empty = show all." /></label>
            <select value={minMrGrade} onChange={(e) => setMinMrGrade(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
              <option value="">Any</option>
              <option value="A+">A+</option>
              <option value="A">A</option>
              <option value="B+">B+</option>
              <option value="B">B</option>
              <option value="C+">C+</option>
              <option value="C">C</option>
              <option value="D">D</option>
            </select>
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-[10px] text-ag-text-muted">Min Avg Bull:<HintTip text="Hide assets with average consecutive bull ranges below this. 0 = no filter." /></label>
            <input type="number" min={0} max={20} step={0.5} value={minAvgBull}
              onChange={(e) => setMinAvgBull(Math.max(0, Number(e.target.value)))}
              className="w-14 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-[10px] text-ag-text-muted">Min Avg Bear:<HintTip text="Hide assets with average consecutive bear ranges below this. 0 = no filter." /></label>
            <input type="number" min={0} max={20} step={0.5} value={minAvgBear}
              onChange={(e) => setMinAvgBear(Math.max(0, Number(e.target.value)))}
              className="w-14 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-[10px] text-ag-text-muted">Min Avg L+S:<HintTip text="Hide assets with average combined ranges below this. 0 = no filter." /></label>
            <input type="number" min={0} max={20} step={0.5} value={minAvgAll}
              onChange={(e) => setMinAvgAll(Math.max(0, Number(e.target.value)))}
              className="w-14 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
          </div>
        </div>

        {/* Summary + PnL */}
        <div className="flex items-center gap-4 flex-wrap border-t border-ag-border/50 pt-3">
          <span className="text-xs font-medium">
            Visible: <span className="text-ag-green">{passCount}</span> pass / <span className="text-ag-text-muted">{visibleCount}</span> shown / {totalCount} total
          </span>
          {failCount > 0 && (
            <span className="text-xs px-2 py-0.5 rounded bg-red-500/15 text-red-400">
              {failCount} failed
            </span>
          )}
          {(visibleCount < totalCount) && (
            <span className="text-xs px-2 py-0.5 rounded bg-ag-accent/15 text-ag-accent">
              {totalCount - visibleCount} hidden by gate
            </span>
          )}
          <label className="flex items-center gap-1.5 text-xs cursor-pointer">
            <input type="checkbox" checked={!includeFailed}
              onChange={(e) => setIncludeFailed(!e.target.checked)}
              className="accent-ag-accent" />
            <span className="text-ag-text-muted">Exclude Failed</span>
            <HintTip text="ON: hides failed assets and excludes them from PnL/KPI calculations. OFF: includes all assets (failed shown with ✗ but neutral background, counted in calculations)." />
          </label>
          {summary.count > 0 && (
            <div className="flex items-center gap-3 flex-wrap ml-auto">
              <span className={`text-xs font-bold ${summary.totalPnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                Total: ${summary.totalPnl.toFixed(2)}
              </span>
              <span className="text-xs text-ag-text-muted">
                Avg: <span className={`font-medium ${summary.avgPnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>${summary.avgPnl.toFixed(2)}</span>
              </span>
              <span className="text-xs text-ag-text-muted">
                Median: <span className={`font-medium ${summary.medianPnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>${summary.medianPnl.toFixed(2)}</span>
              </span>
              <span className="text-xs text-ag-text-muted">
                Avg PF: <span className="text-ag-text font-medium">{summary.avgPf.toFixed(2)}</span>
              </span>
              <span className="text-xs text-ag-text-muted">
                Median PF: <span className="text-ag-text font-medium">{summary.medianPf.toFixed(2)}</span>
              </span>
              <span className="text-xs text-ag-text-muted">
                Avg WR: <span className="text-ag-text font-medium">{summary.avgWr.toFixed(1)}%</span>
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="bg-ag-card border border-ag-border rounded-lg overflow-hidden">
        <div className="max-h-[60vh] overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-ag-card z-10">
              <tr className="text-ag-text-muted border-b border-ag-border">
                <Th label="Symbol" sortKey="symbol" current={sortKey} dir={sortDir} onClick={handleSort} tooltip="Trading pair symbol" />
                <Th label="Regime" sortKey="regime" current={sortKey} dir={sortDir} onClick={handleSort} center tooltip="Price regime: TREND, MEANREV, or NEUTRAL" />
                <Th label="Trend" sortKey="trend_grade" current={sortKey} dir={sortDir} onClick={handleSort} center tooltip="Trend-following suitability grade (A = best, F = worst)" />
                <Th label="MR" sortKey="meanrev_grade" current={sortKey} dir={sortDir} onClick={handleSort} center tooltip="Mean-reversion suitability grade (A = best, F = worst)" />
                <Th label="Hurst" sortKey="Hurst" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Hurst exponent: >0.5 = trending, 0.5 = random walk, <0.5 = mean-reverting" />
                <Th label="TER" sortKey="TER" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Trend Efficiency Ratio — directional efficiency of price movement" />
                <Th label="VR" sortKey="VR" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Variance Ratio — <1 mean-reverting, >1 trending" />
                <Th label="AC" sortKey="AC1" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Autocorrelation lag-1 — positive = trending, negative = mean-reverting" />
                <Th label="DSR" sortKey="DSR" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Deflated Sharpe Ratio — Sharpe adjusted for multiple testing bias" />
                <Th label="GPR" sortKey="GPR" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Gain-to-Pain Ratio — net gain / total pain (sum of losses)" />
                <Th label="PnL" sortKey="net_profit" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Total net profit/loss for this asset" />
                <Th label="PF" sortKey="profit_factor" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Profit Factor — gross profit / gross loss. >1.5 good, >2.0 great" />
                <Th label="WR%" sortKey="win_rate" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Win Rate — percentage of profitable trades" />
                <Th label="Avg Bull" sortKey="avg_consec_bull" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Average consecutive bull (long) ranges from market structure detection" />
                <Th label="Avg Bear" sortKey="avg_consec_bear" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Average consecutive bear (short) ranges from market structure detection" />
                <Th label="Avg L+S" sortKey="avg_consec_all" current={sortKey} dir={sortDir} onClick={handleSort} right tooltip="Average of bull + bear consecutive ranges (combined)" />
                <Th label="Pass" sortKey="pass" current={sortKey} dir={sortDir} onClick={handleSort} center tooltip="Pass = meets Mode thresholds above. Trend: Hurst ≥ min + TER ≥ percentile. Mean-Rev: Hurst ≤ max + VR ≤ max + CR ≥ min. Independent from grades and range gates." />
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => {
                const m = r.sym.metrics ?? {};
                const hurst = Number(m[`${pfx}Hurst`] ?? 0);
                const regime = m[`${pfx}regime`];
                return (
                  <tr
                    key={r.sym.symbol}
                    className={`border-b border-ag-border/20 transition-colors ${
                      r.pass ? "hover:bg-ag-accent/5"
                        : includeFailed ? "hover:bg-ag-accent/5"
                        : "bg-red-500/5 hover:bg-red-500/10"
                    }`}
                    title={r.reason || "Passes filter"}
                  >
                    <td className="py-1.5 px-3 font-medium">{r.sym.symbol}</td>
                    <td className="py-1.5 px-2 text-center">
                      {regime && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                          String(regime) === "TREND" ? "bg-ag-green/15 text-ag-green"
                          : String(regime) === "MEANREV" ? "bg-ag-accent/15 text-ag-accent"
                          : "bg-ag-border/30 text-ag-text-muted"
                        }`}>{String(regime)}</span>
                      )}
                    </td>
                    <td className="py-1.5 px-2 text-center text-[10px] font-medium">{r.displayTrendGrade}</td>
                    <td className="py-1.5 px-2 text-center text-[10px] font-medium">{r.displayMrGrade}</td>
                    <td className={`py-1.5 px-2 text-right text-[10px] ${
                      hurst > 0.55 ? "text-ag-green" : hurst >= 0.50 ? "text-yellow-400" : hurst > 0 ? "text-ag-red" : ""
                    }`}>{m[`${pfx}Hurst`] != null ? fmtNum(m[`${pfx}Hurst`]) : ""}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{m[`${pfx}TER`] != null ? fmtNum(m[`${pfx}TER`]) : ""}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{m[`${pfx}VR5`] != null ? fmtNum(m[`${pfx}VR5`]) : ""}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{m[`${pfx}AC1`] != null ? fmtNum(m[`${pfx}AC1`]) : ""}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{m[`${pfx}DSR`] != null ? fmtNum(m[`${pfx}DSR`]) : ""}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{m[`${pfx}GPR`] != null ? fmtNum(m[`${pfx}GPR`]) : ""}</td>
                    <td className={`py-1.5 px-2 text-right text-[10px] font-medium ${Number(m.net_profit ?? 0) >= 0 ? "text-ag-green" : "text-ag-red"}`}>{m.net_profit != null ? `$${Number(m.net_profit).toFixed(2)}` : ""}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{m.profit_factor != null && Number(m.profit_factor) > 0 ? Number(m.profit_factor).toFixed(2) : ""}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{m.win_rate != null && Number(m.win_rate) > 0 ? `${Number(m.win_rate).toFixed(1)}%` : ""}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{m.avg_consec_bull != null && Number(m.avg_consec_bull) > 0 ? Number(m.avg_consec_bull).toFixed(2) : ""}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{m.avg_consec_bear != null && Number(m.avg_consec_bear) > 0 ? Number(m.avg_consec_bear).toFixed(2) : ""}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{m.avg_consec_all != null && Number(m.avg_consec_all) > 0 ? Number(m.avg_consec_all).toFixed(2) : ""}</td>
                    <td className="py-1.5 px-2 text-center">
                      {r.pass ? (
                        <span className="text-ag-green text-sm">&#10003;</span>
                      ) : (
                        <span className="text-ag-red text-sm">&#10007;</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Th({ label, sortKey, current, dir, onClick, center, right, tooltip }: {
  label: string;
  sortKey: SortKey;
  current: SortKey;
  dir: SortDir;
  onClick: (k: SortKey) => void;
  center?: boolean;
  right?: boolean;
  tooltip?: string;
}) {
  const align = center ? "text-center" : right ? "text-right" : "text-left";
  const active = current === sortKey;
  return (
    <th
      className={`py-2 px-2 cursor-pointer hover:text-ag-text select-none ${align} ${active ? "text-ag-accent" : ""}`}
      onClick={() => onClick(sortKey)}
      title={tooltip}
    >
      {label}{active ? (dir === "asc" ? " \u25B2" : " \u25BC") : ""}
    </th>
  );
}
