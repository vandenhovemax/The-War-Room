"use client";
import { useState, useEffect, useMemo, useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { AssetStructuralResult } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

type SortKey = "symbol" | "regime" | "trend_grade" | "meanrev_grade" | "Hurst" | "TER" | "VR5" | "AC1" | "DSR" | "GPR" | "MRI" | "HalfLife" | "CrossRate" | "pass" | "max_qty" | "max_notional" | "min_qty" | "lot_size";
type SortDir = "asc" | "desc";

function fmtNum(v: number): string {
  if (!isFinite(v)) return "\u2014";
  return v.toFixed(4);
}

function gradeOrder(g: string): number {
  const map: Record<string, number> = { A: 5, B: 4, C: 3, D: 2, F: 1 };
  return map[g] ?? 0;
}

interface PortfolioOption { id: number; name: string }
type InstrumentSpec = { min_qty: number; max_qty: number; lot_size: number };

export default function AssetStructuralPanel() {
  const [timeframes, setTimeframes] = useState<{ timeframe: string; symbol_count: number }[]>([]);
  const [selectedTf, setSelectedTf] = useState("");
  const [results, setResults] = useState<AssetStructuralResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [cachedAt, setCachedAt] = useState<number | null>(null);

  // Filter state
  const [mode, setMode] = useState<"trend" | "meanrev">("trend");
  const [hurstMin, setHurstMin] = useState(0.50);
  const [terPercentile, setTerPercentile] = useState(25);
  const [hurstMax, setHurstMax] = useState(0.50);
  const [vrMax, setVrMax] = useState(1.0);
  const [minCrossingRate, setMinCrossingRate] = useState(0.05);

  // Sort
  const [sortKey, setSortKey] = useState<SortKey>("symbol");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // Bybit instrument specs
  const [specs, setSpecs] = useState<Record<string, InstrumentSpec>>({});

  // Portfolio filter
  const [portfolios, setPortfolios] = useState<PortfolioOption[]>([]);
  const [selectedPortfolio, setSelectedPortfolio] = useState<number | "all">("all");

  // Size checker
  const [riskAmount, setRiskAmount] = useState("");
  const [leverageInput, setLeverageInput] = useState("");

  // Load available timeframes, instrument specs, and portfolios on mount
  useEffect(() => {
    api.assetAvailableTimeframes()
      .then((tfs) => {
        setTimeframes(tfs);
        if (tfs.length > 0) {
          const def = tfs.find((t) => t.timeframe === "30") ?? tfs[0];
          setSelectedTf(def.timeframe);
        }
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load timeframes"));

    api.assetInstrumentSpecs()
      .then((r) => setSpecs(r.specs))
      .catch(() => {/* non-critical — table just won't show Bybit columns */});

    api.portfolios()
      .then((list) => {
        setPortfolios(list.map((p) => ({ id: p.id, name: p.name })));
      })
      .catch(() => {/* non-critical */});
  }, []);

  // Fetch portfolio assets when selection changes
  const [portfolioSymbols, setPortfolioSymbols] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (selectedPortfolio === "all") {
      setPortfolioSymbols(new Set());
      return;
    }
    api.portfolioDetail(selectedPortfolio)
      .then((d) => {
        const syms = new Set(d.assets.map((a: { symbol: string }) => a.symbol));
        setPortfolioSymbols(syms);
      })
      .catch(() => setPortfolioSymbols(new Set()));
  }, [selectedPortfolio]);

  // Size checker computed values
  const checkerNotional = useMemo(() => {
    const r = parseFloat(riskAmount) || 0;
    const l = parseFloat(leverageInput) || 0;
    return r * l;
  }, [riskAmount, leverageInput]);

  // Load metrics when TF changes
  const loadMetrics = useCallback((tf: string) => {
    setLoading(true);
    setError("");
    api.assetStructuralMetrics(tf)
      .then((r) => {
        setResults(r.results);
        setCachedAt(r.cached_at ?? null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load metrics"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (selectedTf) loadMetrics(selectedTf);
  }, [selectedTf, loadMetrics]);

  // Refresh: clear cache then reload
  const handleRefresh = async () => {
    if (!selectedTf || refreshing) return;
    setRefreshing(true);
    try {
      await api.assetRefreshStructuralMetrics(selectedTf);
      loadMetrics(selectedTf);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  };

  // Format cached_at for display
  const cachedAgo = cachedAt
    ? (() => {
        const mins = Math.floor((Date.now() / 1000 - cachedAt) / 60);
        if (mins < 1) return "just now";
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        return `${Math.floor(hrs / 24)}d ago`;
      })()
    : null;

  // Apply filters
  const filtered = useMemo(() => {
    // Portfolio filter
    const baseResults = portfolioSymbols.size > 0
      ? results.filter((r) => portfolioSymbols.has(r.symbol))
      : results;

    // Compute TER percentile threshold
    const terValues = baseResults
      .map((r) => r.TER)
      .filter((v) => v > 0)
      .sort((a, b) => a - b);
    const terThreshold = terValues.length > 0
      ? terValues[Math.floor(terValues.length * terPercentile / 100)] ?? 0
      : 0;

    return baseResults.map((r) => {
      let pass = true;
      let reason = "";

      if (mode === "trend") {
        if (r.Hurst < hurstMin) {
          pass = false;
          reason = `Hurst ${r.Hurst.toFixed(3)} < ${hurstMin}`;
        } else if (r.TER < terThreshold) {
          pass = false;
          reason = `TER ${r.TER.toFixed(3)} < p${terPercentile} (${terThreshold.toFixed(3)})`;
        }
      } else {
        if (r.Hurst > hurstMax) {
          pass = false;
          reason = `Hurst ${r.Hurst.toFixed(3)} > ${hurstMax}`;
        } else if (r.VR5 > vrMax) {
          pass = false;
          reason = `VR ${r.VR5.toFixed(3)} > ${vrMax}`;
        } else if (r.CrossRate < minCrossingRate) {
          pass = false;
          reason = `CR ${r.CrossRate.toFixed(3)} < ${minCrossingRate}`;
        }
      }

      // Bybit spec data
      const sp = specs[r.symbol];
      const max_qty = sp?.max_qty ?? 0;
      const min_qty = sp?.min_qty ?? 0;
      const lot_size = sp?.lot_size ?? 0;
      const lastPrice = r.last_close ?? 0;
      const max_notional = max_qty > 0 && lastPrice > 0 ? max_qty * lastPrice : 0;
      const exceedsLimit = checkerNotional > 0 && max_notional > 0 && checkerNotional > max_notional;

      return { data: r, pass, reason, max_qty, min_qty, lot_size, max_notional, exceedsLimit };
    });
  }, [results, mode, hurstMin, terPercentile, hurstMax, vrMax, minCrossingRate, portfolioSymbols, specs, checkerNotional]);

  // Sort
  const sorted = useMemo(() => {
    const copy = [...filtered];
    copy.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "symbol":
          cmp = a.data.symbol.localeCompare(b.data.symbol);
          break;
        case "regime":
          cmp = a.data.regime.localeCompare(b.data.regime);
          break;
        case "trend_grade":
          cmp = gradeOrder(a.data.trend_grade) - gradeOrder(b.data.trend_grade);
          break;
        case "meanrev_grade":
          cmp = gradeOrder(a.data.meanrev_grade) - gradeOrder(b.data.meanrev_grade);
          break;
        case "pass":
          cmp = (a.pass ? 1 : 0) - (b.pass ? 1 : 0);
          break;
        case "max_qty":
          cmp = a.max_qty - b.max_qty;
          break;
        case "max_notional":
          cmp = a.max_notional - b.max_notional;
          break;
        case "min_qty":
          cmp = a.min_qty - b.min_qty;
          break;
        case "lot_size":
          cmp = a.lot_size - b.lot_size;
          break;
        default:
          cmp = (a.data[sortKey] ?? 0) - (b.data[sortKey] ?? 0);
      }
      return sortDir === "desc" ? -cmp : cmp;
    });
    return copy;
  }, [filtered, sortKey, sortDir]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("desc"); }
  };

  const passCount = filtered.filter((r) => r.pass).length;
  const failCount = filtered.length - passCount;
  const exceedCount = filtered.filter((r) => r.exceedsLimit).length;
  const hasSpecs = Object.keys(specs).length > 0;
  const tfLabel = (tf: string) => {
    const map: Record<string, string> = { "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "120": "2h", "240": "4h", "360": "6h", "720": "12h", D: "Daily", W: "Weekly" };
    return map[tf] ?? `${tf}m`;
  };

  const sortIcon = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " \u25B2" : " \u25BC") : "";

  return (
    <div className="space-y-4 py-4">
      {/* Header */}
      <div className="bg-ag-card border border-ag-border rounded-lg p-4 space-y-3">
        <div className="flex items-center gap-3 flex-wrap">
          <h3 className="text-sm font-medium">Asset Structural Quality</h3>
          <HintTip text="Shows OHLCV-based structural metrics for every asset with data. These measure the asset's inherent trending/mean-reverting behavior, independent of any strategy." />

          <div className="flex items-center gap-2 ml-auto">
            {cachedAgo && (
              <span className="text-[10px] text-ag-text-muted">Computed {cachedAgo}</span>
            )}
            <button
              onClick={handleRefresh}
              disabled={refreshing || loading}
              className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded border border-ag-border hover:border-ag-accent/50 text-ag-text-muted hover:text-ag-text transition-colors disabled:opacity-50"
              title="Recompute all asset metrics from current OHLCV data (use after downloading new candles)"
            >
              <RefreshCw size={11} className={refreshing ? "animate-spin" : ""} />
              Refresh
            </button>
            <label className="text-xs text-ag-text-muted">Timeframe:</label>
            <select
              value={selectedTf}
              onChange={(e) => setSelectedTf(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs"
            >
              {timeframes.map((t) => (
                <option key={t.timeframe} value={t.timeframe}>
                  {tfLabel(t.timeframe)} ({t.symbol_count} symbols)
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Portfolio filter + Size checker */}
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted">Portfolio:<HintTip text="Filter the table to only show assets from a specific portfolio." /></label>
            <select
              value={selectedPortfolio}
              onChange={(e) => setSelectedPortfolio(e.target.value === "all" ? "all" : Number(e.target.value))}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs max-w-[180px]"
            >
              <option value="all">All Assets</option>
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          {hasSpecs && (
            <>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">$ Risk:<HintTip text="Enter your risk amount in dollars. Combined with leverage, this computes the notional position size to check against Bybit limits." /></label>
                <input type="number" min={0} step={10} value={riskAmount}
                  onChange={(e) => setRiskAmount(e.target.value)}
                  placeholder="250"
                  className="w-20 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Leverage:</label>
                <input type="number" min={1} max={125} step={1} value={leverageInput}
                  onChange={(e) => setLeverageInput(e.target.value)}
                  placeholder="10"
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
              {checkerNotional > 0 && (
                <span className="text-xs font-medium text-ag-text">
                  Notional: ${checkerNotional.toLocaleString()}
                </span>
              )}
            </>
          )}
        </div>

        {/* Filters */}
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-xs text-ag-text-muted">Mode:<HintTip text="Trend: keeps assets with high Hurst/TER. Mean-Rev: keeps assets with low Hurst, low VR, high crossing rate." /></label>
            <select value={mode} onChange={(e) => setMode(e.target.value as "trend" | "meanrev")} className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
              <option value="trend">Trend-Following</option>
              <option value="meanrev">Mean-Reversion</option>
            </select>
          </div>

          {mode === "trend" ? (
            <>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Min Hurst:<HintTip text="Minimum Hurst exponent. Higher = stricter (only strong trends). Assets below this value are removed." /></label>
                <input type="number" min={0.3} max={0.8} step={0.01} value={hurstMin}
                  onChange={(e) => setHurstMin(Number(e.target.value))}
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">TER Pctl:<HintTip text="TER percentile threshold. Assets below this percentile of trend efficiency are removed." /></label>
                <input type="number" min={0} max={75} step={5} value={terPercentile}
                  onChange={(e) => setTerPercentile(Number(e.target.value))}
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Max Hurst:<HintTip text="Maximum Hurst exponent. Lower = stricter (more mean-reverting). Assets above this value are removed." /></label>
                <input type="number" min={0.3} max={0.7} step={0.01} value={hurstMax}
                  onChange={(e) => setHurstMax(Number(e.target.value))}
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Max VR:<HintTip text="Maximum Variance Ratio. Assets with VR above this are removed. VR < 1 indicates mean-reversion." /></label>
                <input type="number" min={0.5} max={1.5} step={0.05} value={vrMax}
                  onChange={(e) => setVrMax(Number(e.target.value))}
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] text-ag-text-muted">Min Cross Rate:<HintTip text="Minimum crossing rate. Assets with fewer sign changes than this are removed." /></label>
                <input type="number" min={0} max={0.5} step={0.01} value={minCrossingRate}
                  onChange={(e) => setMinCrossingRate(Number(e.target.value))}
                  className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center" />
              </div>
            </>
          )}
        </div>

        {/* Summary */}
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium">
            Passing: <span className="text-ag-green">{passCount}</span>/{filtered.length} assets
          </span>
          {failCount > 0 && (
            <span className="text-xs px-2 py-0.5 rounded bg-red-500/15 text-red-400">
              {failCount} removed
            </span>
          )}
          {exceedCount > 0 && checkerNotional > 0 && (
            <span className="text-xs px-2 py-0.5 rounded bg-orange-500/15 text-orange-400">
              {exceedCount} of {filtered.length} exceed Bybit max notional at ${checkerNotional.toLocaleString()}
            </span>
          )}
          {loading && <span className="text-xs text-ag-text-muted animate-pulse">Loading...</span>}
        </div>
      </div>

      {/* Error */}
      {error && <p className="text-xs text-red-400 text-center py-4">{error}</p>}

      {/* Table */}
      {!loading && results.length > 0 && (
        <div className="bg-ag-card border border-ag-border rounded-lg overflow-hidden">
          <div className="max-h-[60vh] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-ag-card z-10">
                <tr className="text-ag-text-muted border-b border-ag-border">
                  <Th label="Symbol" k="symbol" current={sortKey} dir={sortDir} onClick={handleSort}
                    tip="Trading pair symbol" />
                  <Th label="Regime" k="regime" current={sortKey} dir={sortDir} onClick={handleSort} center
                    tip="Classification: TREND, MEANREV, or NEUTRAL based on composite scores" />
                  <Th label="Trend" k="trend_grade" current={sortKey} dir={sortDir} onClick={handleSort} center
                    tip="Letter grade (A+ to F) for trend-following suitability. Based on TER, Hurst, GPR, and autocorrelation." />
                  <Th label="MR" k="meanrev_grade" current={sortKey} dir={sortDir} onClick={handleSort} center
                    tip="Letter grade (A+ to F) for mean-reversion suitability. Based on MRI, Hurst, VR, half-life, and crossing rate." />
                  <Th label="Hurst" k="Hurst" current={sortKey} dir={sortDir} onClick={handleSort} right
                    tip="Hurst exponent: >0.5 = trending, <0.5 = mean-reverting, 0.5 = random walk." />
                  <Th label="TER" k="TER" current={sortKey} dir={sortDir} onClick={handleSort} right
                    tip="Trend Efficiency Ratio: 1.0 = perfectly straight trend, 0.0 = pure noise." />
                  <Th label="VR" k="VR5" current={sortKey} dir={sortDir} onClick={handleSort} right
                    tip="Variance Ratio (5-lag): >1 = trending, <1 = mean-reverting." />
                  <Th label="AC" k="AC1" current={sortKey} dir={sortDir} onClick={handleSort} right
                    tip="Autocorrelation (lag-1): >0 = momentum, <0 = mean-reversion." />
                  <Th label="MRI" k="MRI" current={sortKey} dir={sortDir} onClick={handleSort} right
                    tip="Mean-Reversion Intensity: strength of pull-back behavior. Higher = stronger reversion." />
                  <Th label="HalfLife" k="HalfLife" current={sortKey} dir={sortDir} onClick={handleSort} right
                    tip="Bars needed for price to revert halfway to the mean. Lower = faster reversion. Infinity = no reversion detected." />
                  <Th label="CrossRate" k="CrossRate" current={sortKey} dir={sortDir} onClick={handleSort} right
                    tip="Fraction of bars where returns flip sign. Higher = more reversals." />
                  <Th label="DSR" k="DSR" current={sortKey} dir={sortDir} onClick={handleSort} right
                    tip="Deflated Sharpe Ratio: Sharpe ratio adjusted for multiple-testing bias." />
                  <Th label="GPR" k="GPR" current={sortKey} dir={sortDir} onClick={handleSort} right
                    tip="Gain-to-Pain Ratio: sum of positive returns / sum of |negative returns|. >1 = net positive." />
                  <Th label="Pass" k="pass" current={sortKey} dir={sortDir} onClick={handleSort} center
                    tip="Whether the asset passes the current filter criteria." />
                  {hasSpecs && (
                    <>
                      <Th label="Max Notional" k="max_notional" current={sortKey} dir={sortDir} onClick={handleSort} right
                        tip="Maximum notional position value allowed by Bybit (max_qty × last price). Red if your $ × leverage exceeds this." />
                      <Th label="Max Qty" k="max_qty" current={sortKey} dir={sortDir} onClick={handleSort} right
                        tip="Bybit maximum order quantity (contracts) for this symbol." />
                      <Th label="Min Qty" k="min_qty" current={sortKey} dir={sortDir} onClick={handleSort} right
                        tip="Bybit minimum order quantity (contracts). Orders below this are rejected." />
                      <Th label="Lot Size" k="lot_size" current={sortKey} dir={sortDir} onClick={handleSort} right
                        tip="Bybit lot size step. Order quantities must be multiples of this value." />
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => (
                  <tr
                    key={r.data.symbol}
                    className={`border-b border-ag-border/20 transition-colors ${
                      r.pass ? "hover:bg-ag-accent/5" : "bg-red-500/5 hover:bg-red-500/10"
                    }`}
                    title={r.reason || "Passes filter"}
                  >
                    <td className="py-1.5 px-3 font-medium">{r.data.symbol}</td>
                    <td className="py-1.5 px-2 text-center">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                        r.data.regime === "TREND" ? "bg-ag-green/15 text-ag-green"
                        : r.data.regime === "MEANREV" ? "bg-ag-accent/15 text-ag-accent"
                        : "bg-ag-border/30 text-ag-text-muted"
                      }`}>{r.data.regime}</span>
                    </td>
                    <td className="py-1.5 px-2 text-center text-[10px] font-medium">{r.data.trend_grade}</td>
                    <td className="py-1.5 px-2 text-center text-[10px] font-medium">{r.data.meanrev_grade}</td>
                    <td className={`py-1.5 px-2 text-right text-[10px] ${
                      r.data.Hurst > 0.55 ? "text-ag-green" : r.data.Hurst >= 0.50 ? "text-yellow-400" : "text-ag-red"
                    }`}>{fmtNum(r.data.Hurst)}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{fmtNum(r.data.TER)}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{fmtNum(r.data.VR5)}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{fmtNum(r.data.AC1)}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{fmtNum(r.data.MRI)}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{r.data.HalfLife >= 999 ? "\u221E" : r.data.HalfLife.toFixed(1)}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{fmtNum(r.data.CrossRate)}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{fmtNum(r.data.DSR)}</td>
                    <td className="py-1.5 px-2 text-right text-[10px]">{fmtNum(r.data.GPR)}</td>
                    <td className="py-1.5 px-2 text-center">
                      {r.pass ? (
                        <span className="text-ag-green text-sm">&#10003;</span>
                      ) : (
                        <span className="text-ag-red text-sm">&#10007;</span>
                      )}
                    </td>
                    {hasSpecs && (
                      <>
                        <td className={`py-1.5 px-2 text-right text-[10px] ${r.exceedsLimit ? "bg-red-500/20 text-red-400 font-bold" : ""}`}>
                          {r.max_notional > 0 ? `$${r.max_notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "\u2014"}
                        </td>
                        <td className="py-1.5 px-2 text-right text-[10px]">
                          {r.max_qty > 0 ? r.max_qty.toLocaleString() : "\u2014"}
                        </td>
                        <td className="py-1.5 px-2 text-right text-[10px]">
                          {r.min_qty > 0 ? r.min_qty : "\u2014"}
                        </td>
                        <td className="py-1.5 px-2 text-right text-[10px]">
                          {r.lot_size > 0 ? r.lot_size : "\u2014"}
                        </td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!loading && results.length === 0 && !error && (
        <p className="text-xs text-ag-text-muted text-center py-8">
          No OHLCV data found for this timeframe. Download data first.
        </p>
      )}
    </div>
  );
}

function Th({ label, k, current, dir, onClick, center, right, tip }: {
  label: string;
  k: SortKey;
  current: SortKey;
  dir: SortDir;
  onClick: (k: SortKey) => void;
  center?: boolean;
  right?: boolean;
  tip?: string;
}) {
  const align = center ? "text-center" : right ? "text-right" : "text-left";
  const active = current === k;
  return (
    <th
      className={`py-2 px-2 cursor-pointer hover:text-ag-text select-none ${align} ${active ? "text-ag-accent" : ""}`}
      onClick={() => onClick(k)}
    >
      {label}{tip && <HintTip text={tip} />}{active ? (dir === "asc" ? " \u25B2" : " \u25BC") : ""}
    </th>
  );
}
