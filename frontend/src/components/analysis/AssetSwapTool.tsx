"use client";
import { useState, useMemo } from "react";
import { api } from "@/lib/api";
import type { SwapCandidate, PortfolioDetail, SwapHistoryEntry } from "@/lib/api";
import { ArrowRightLeft, Filter, History } from "lucide-react";
import Phase2EventFeed from "./Phase2EventFeed";

interface Props {
  portfolio: PortfolioDetail;
  onSwapped: () => void;
}

/** Extract saved guardrails (min_trades, min_pf, min_wr, corr_method) from portfolio settings. */
function getSavedGuardrails(ss: Record<string, unknown>): {
  minTrades: number; minWr: number; minPf: number; corrMethod: string;
} {
  // Settings may be nested under optimizer_settings or builder_settings
  const src = (ss.optimizer_settings ?? ss.builder_settings ?? ss) as Record<string, unknown>;
  return {
    minTrades: typeof src.min_trades === "number" ? src.min_trades : 0,
    minWr: typeof src.min_wr === "number" ? src.min_wr : 0,
    minPf: typeof src.min_pf === "number" ? src.min_pf : 0,
    corrMethod: typeof src.corr_method === "string" ? src.corr_method : "anti",
  };
}

export default function AssetSwapTool({ portfolio, onSwapped }: Props) {
  const saved = useMemo(() => getSavedGuardrails(portfolio.source_strategies), [portfolio.source_strategies]);
  const [selectedAsset, setSelectedAsset] = useState("");
  const [candidates, setCandidates] = useState<SwapCandidate[]>([]);
  const [loading, setLoading] = useState(false);
  const [swapping, setSwapping] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [corrMethod, setCorrMethod] = useState(saved.corrMethod);
  const [minTrades, setMinTrades] = useState(saved.minTrades);
  const [minWr, setMinWr] = useState(saved.minWr);
  const [minPf, setMinPf] = useState(saved.minPf);
  const [resultInfo, setResultInfo] = useState<{
    is_only: boolean; method: string; seed_metrics: string[]; pool_size: number;
  } | null>(null);

  // Only show active assets in the dropdown
  const activeAssets = portfolio.assets.filter((a) => a.is_active);
  const batchMap = portfolio.batch_id_map || {};

  // Build dropdown items with strategy/batch context
  // Use DB row id as value to avoid key collisions when the same symbol+side
  // appears from different batches (e.g. two ACEUSDT_Short from batch 5 & 8).
  const assetOptions = activeAssets.map((a) => {
    const assetName = a.side === "Long" || a.side === "Short" ? `${a.symbol}_${a.side}` : a.symbol;
    const shortSymbol = assetName.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "");
    const batchLabel = a.batch_id && batchMap[a.batch_id] ? batchMap[a.batch_id] : "";
    const configLabel = a.config_name || "";
    // Build context string: "CGPT S  [Config | Batch Name]"
    const parts: string[] = [];
    if (configLabel) parts.push(configLabel);
    if (batchLabel) parts.push(batchLabel);
    const context = parts.length > 0 ? `  [${parts.join(" | ")}]` : "";
    return { value: String(a.id), assetName, label: `${shortSymbol}${context}`, batchId: a.batch_id };
  });

  // Resolve selected dropdown id → asset name and batch for API calls
  const selectedOption = assetOptions.find((o) => o.value === selectedAsset);
  const selectedAssetName = selectedOption?.assetName ?? "";
  const selectedBatchId = selectedOption?.batchId;

  const findCandidates = async () => {
    if (!selectedAssetName) return;
    setLoading(true);
    try {
      const r = await api.portfolioSwapCandidates(portfolio.id, selectedAssetName, {
        min_trades: minTrades, min_wr: minWr, min_pf: minPf,
        corr_method: corrMethod, use_seed_ranking: true,
      });
      setCandidates(r.candidates);
      setResultInfo({
        is_only: r.is_only, method: r.method,
        seed_metrics: r.seed_metrics, pool_size: r.pool_size,
      });
    } catch {
      setCandidates([]);
    }
    setLoading(false);
  };

  const doSwap = async (newAsset: string, batchId?: number | null) => {
    if (!confirm(`Replace ${selectedAssetName} with ${newAsset}?\n\nThis modifies the portfolio in-place and logs the swap.`)) return;
    setSwapping(true);
    try {
      await api.swapExecute(portfolio.id, selectedAssetName, newAsset,
        `Replaced via swap tool (corr: ${corrMethod})`, batchId, selectedBatchId,
      );
      setCandidates([]);
      setSelectedAsset("");
      setResultInfo(null);
      onSwapped();
    } catch {
      alert("Swap failed — check console for details");
    }
    setSwapping(false);
  };

  const swapHistory: SwapHistoryEntry[] = portfolio.swap_history || [];

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <ArrowRightLeft size={14} className="text-ag-text-muted" />
        <select value={selectedAsset} onChange={(e) => setSelectedAsset(e.target.value)}
          className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs flex-1">
          <option value="">Select asset to replace...</option>
          {assetOptions.map((a) => (
            <option key={a.value} value={a.value}>{a.label}</option>
          ))}
        </select>
        <button onClick={() => setShowFilters(!showFilters)}
          className={`p-1 rounded ${showFilters ? "text-ag-accent" : "text-ag-text-muted hover:text-ag-text"}`}>
          <Filter size={14} />
        </button>
        <button onClick={findCandidates} disabled={!selectedAsset || loading}
          className="px-2 py-1 bg-ag-accent/20 text-ag-accent rounded text-xs hover:bg-ag-accent/30 disabled:opacity-50">
          {loading ? "..." : "Find"}
        </button>
      </div>

      {/* Selected asset detail pill */}
      {selectedAsset && (() => {
        const match = activeAssets.find((a) => String(a.id) === selectedAsset);
        if (!match) return null;
        const bName = match.batch_id && batchMap[match.batch_id] ? batchMap[match.batch_id] : null;
        const cName = match.config_name || null;
        const sName = match.strategy_name || null;
        if (!bName && !cName && !sName) return null;
        return (
          <div className="flex items-center gap-1.5 flex-wrap text-[10px]">
            {sName && <span className="px-1.5 py-0.5 bg-ag-accent/10 text-ag-accent rounded">Strategy: {sName}</span>}
            {cName && <span className="px-1.5 py-0.5 bg-purple-500/10 text-purple-400 rounded">Config: {cName}</span>}
            {bName && <span className="px-1.5 py-0.5 bg-ag-dark rounded text-ag-text-muted">Batch: {bName}</span>}
          </div>
        );
      })()}

      {showFilters && (
        <div className="bg-ag-dark/30 rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-ag-text-muted">Corr Method:</label>
              <select value={corrMethod} onChange={(e) => setCorrMethod(e.target.value)}
                className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-[10px]">
                <option value="anti">Anti-correlated</option>
                <option value="smart">Smart (balanced)</option>
                <option value="hybrid">Hybrid (anti + profitable)</option>
              </select>
            </div>
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-ag-text-muted">Min Trades:</label>
              <input type="number" min={0} value={minTrades}
                onChange={(e) => setMinTrades(Math.max(0, parseInt(e.target.value) || 0))}
                className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-[10px] text-center" />
            </div>
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-ag-text-muted">Min WR%:</label>
              <input type="number" min={0} max={100} value={minWr}
                onChange={(e) => setMinWr(Math.max(0, parseFloat(e.target.value) || 0))}
                className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-[10px] text-center" />
            </div>
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-ag-text-muted">Min PF:</label>
              <input type="number" min={0} step={0.1} value={minPf}
                onChange={(e) => setMinPf(Math.max(0, parseFloat(e.target.value) || 0))}
                className="w-12 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-[10px] text-center" />
            </div>
          </div>
          <p className="text-[9px] text-ag-text-muted">
            Filters pre-filled from portfolio&apos;s saved optimizer guardrails.
            Candidates are seed-ranked using the portfolio&apos;s original scoring, then sorted by correlation fit.
            IS-only filtering is automatic when portfolio has an OOS cutoff date.
          </p>
        </div>
      )}

      {resultInfo && candidates.length > 0 && (
        <div className="flex items-center gap-2 text-[10px] text-ag-text-muted flex-wrap">
          <span className="px-1.5 py-0.5 bg-ag-dark rounded">
            {resultInfo.is_only ? "IS only" : "All data"}
          </span>
          <span className="px-1.5 py-0.5 bg-ag-dark rounded">
            {resultInfo.method === "anti" ? "Anti-corr" : resultInfo.method === "smart" ? "Smart" : "Hybrid"}
          </span>
          {resultInfo.seed_metrics.length > 0 && (
            <span className="px-1.5 py-0.5 bg-ag-accent/10 text-ag-accent rounded">
              Seed: {resultInfo.seed_metrics.join(", ")}
            </span>
          )}
          <span>Pool: {resultInfo.pool_size} &rarr; {candidates.length} shown</span>
        </div>
      )}

      {candidates.length > 0 && (
        <div className="bg-ag-dark/30 rounded-lg p-2">
          <p className="text-[10px] text-ag-text-muted uppercase mb-1">Top Replacements</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] text-ag-text-muted border-b border-ag-border/30">
                  <th className="text-left py-1 px-1.5">Asset</th>
                  <th className="text-left py-1 px-1.5">Source</th>
                  <th className="text-right py-1 px-1.5" title="Seed Rank">Seed</th>
                  <th className="text-right py-1 px-1.5">Corr</th>
                  <th className="text-right py-1 px-1.5">PnL</th>
                  <th className="text-right py-1 px-1.5">PF</th>
                  <th className="text-right py-1 px-1.5">WR</th>
                  <th className="text-right py-1 px-1.5">#</th>
                  <th className="py-1 px-1.5"></th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c, i) => (
                  <tr key={c.asset} className="hover:bg-ag-card-hover border-b border-ag-border/10">
                    <td className={`py-1 px-1.5 font-medium ${i === 0 ? "text-ag-green" : ""}`}>
                      {c.asset.replace("USDT_Long", " L").replace("USDT_Short", " S").replace("USDT", "")}
                    </td>
                    <td className="py-1 px-1.5 text-[10px] text-ag-text-muted max-w-[120px] truncate" title={c.batch_name}>
                      {c.batch_name || "-"}
                    </td>
                    <td className="text-right py-1 px-1.5 text-ag-text-muted">
                      #{c.seed_rank}
                    </td>
                    <td className="text-right py-1 px-1.5 font-mono text-ag-text-muted">
                      {c.corr_score.toFixed(3)}
                    </td>
                    <td className={`text-right py-1 px-1.5 font-mono ${c.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                      ${c.net_profit.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </td>
                    <td className="text-right py-1 px-1.5 font-mono">
                      {c.profit_factor.toFixed(2)}
                    </td>
                    <td className="text-right py-1 px-1.5 font-mono">
                      {c.win_rate.toFixed(1)}%
                    </td>
                    <td className="text-right py-1 px-1.5 text-ag-text-muted">
                      {c.trades}
                    </td>
                    <td className="py-1 px-1.5">
                      <button onClick={() => doSwap(c.asset, c.batch_id)} disabled={swapping}
                        className="px-1.5 py-0.5 bg-ag-green/20 text-ag-green rounded text-[10px] hover:bg-ag-green/30 disabled:opacity-50">
                        Swap
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {candidates.length === 0 && resultInfo && (
        <p className="text-xs text-ag-text-muted">No candidates match the current filters.</p>
      )}

      {/* T-002 — Live phase-2 nightly event feed (basket changes / no-change ticks). */}
      <Phase2EventFeed portfolioId={portfolio.id} />

      {swapHistory.length > 0 && (
        <div className="bg-ag-dark/30 rounded-lg p-2">
          <div className="flex items-center gap-1.5 mb-1">
            <History size={12} className="text-ag-text-muted" />
            <p className="text-[10px] text-ag-text-muted uppercase">Swap History</p>
          </div>
          <div className="space-y-1">
            {swapHistory.map((h, i) => (
              <div key={i} className="flex items-center gap-2 text-[10px]">
                <span className="text-ag-text-muted">{new Date(h.date).toLocaleDateString()}</span>
                <span className="text-ag-red">{h.removed.replace("USDT", "")}</span>
                <span className="text-ag-text-muted">&rarr;</span>
                <span className="text-ag-green">{h.added.replace("USDT", "")}</span>
                {h.reason && <span className="text-ag-text-muted truncate max-w-[200px]">({h.reason})</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
