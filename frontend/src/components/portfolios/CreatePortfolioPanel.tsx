"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { BuilderBatch, BuilderAsset } from "@/lib/api";
import { PlusCircle, Trash2, ChevronDown, ChevronUp, Check } from "lucide-react";

interface StrategyGroup {
  id: number;
  batchId: number | null;
  batchName: string;
  strategyName: string;
  direction: "Long" | "Short" | "Both";
  selectedAssets: Set<string>;
  availableAssets: BuilderAsset[];
  loadingAssets: boolean;
}

let groupIdCounter = 1;

export default function CreatePortfolioPanel() {
  const [batches, setBatches] = useState<BuilderBatch[]>([]);
  const [loadingBatches, setLoadingBatches] = useState(true);
  const [groups, setGroups] = useState<StrategyGroup[]>([]);
  const [name, setName] = useState("");
  const [isStartDate, setIsStartDate] = useState("");
  const [oosStartDate, setOosStartDate] = useState("");
  // Live-loop mode: hybrid = basket + weights refresh nightly via Step-2;
  // non_hybrid = basket frozen until OOS boundary (safer default for manual
  // saves where no Step-1/Step-2 pipeline is defined).
  const [mode, setMode] = useState<"hybrid" | "non_hybrid">("non_hybrid");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    api.builderBatches()
      .then((r) => setBatches(r.batches))
      .catch(() => setBatches([]))
      .finally(() => setLoadingBatches(false));
  }, []);

  const addGroup = () => {
    setGroups((prev) => [
      ...prev,
      {
        id: groupIdCounter++,
        batchId: null,
        batchName: "",
        strategyName: "",
        direction: "Long",
        selectedAssets: new Set(),
        availableAssets: [],
        loadingAssets: false,
      },
    ]);
  };

  const removeGroup = (id: number) => {
    setGroups((prev) => prev.filter((g) => g.id !== id));
  };

  const updateGroup = (id: number, updates: Partial<StrategyGroup>) => {
    setGroups((prev) =>
      prev.map((g) => (g.id === id ? { ...g, ...updates } : g))
    );
  };

  const loadAssetsForGroup = async (groupId: number, batchId: number, direction: string) => {
    updateGroup(groupId, { loadingAssets: true });
    try {
      const r = await api.builderBatchAssets(batchId, direction);
      updateGroup(groupId, {
        availableAssets: r.assets,
        strategyName: r.strategy_name,
        batchName: r.batch_name,
        loadingAssets: false,
        selectedAssets: new Set(),
      });
    } catch {
      updateGroup(groupId, { availableAssets: [], loadingAssets: false });
    }
  };

  const toggleAsset = (groupId: number, assetName: string) => {
    setGroups((prev) =>
      prev.map((g) => {
        if (g.id !== groupId) return g;
        const next = new Set(g.selectedAssets);
        if (next.has(assetName)) next.delete(assetName);
        else next.add(assetName);
        return { ...g, selectedAssets: next };
      })
    );
  };

  const selectAllAssets = (groupId: number) => {
    setGroups((prev) =>
      prev.map((g) => {
        if (g.id !== groupId) return g;
        const allSelected = g.availableAssets.every((a) => g.selectedAssets.has(a.asset_name));
        if (allSelected) return { ...g, selectedAssets: new Set() };
        return { ...g, selectedAssets: new Set(g.availableAssets.map((a) => a.asset_name)) };
      })
    );
  };

  const totalAssets = groups.reduce((sum, g) => sum + g.selectedAssets.size, 0);

  const handleSave = async () => {
    setError("");
    setSuccess("");
    if (!name.trim()) { setError("Portfolio name is required"); return; }
    if (totalAssets === 0) { setError("Select at least one asset"); return; }

    setSaving(true);
    try {
      const batchIds = [...new Set(groups.filter((g) => g.batchId).map((g) => g.batchId!))];
      const assets = groups.flatMap((g) =>
        [...g.selectedAssets].map((assetName) => {
          const info = g.availableAssets.find((a) => a.asset_name === assetName);
          return {
            symbol: info?.symbol || assetName.replace(/_Long$|_Short$/, ""),
            side: info?.side || g.direction,
            strategy_name: g.strategyName,
            batch_id: g.batchId ?? undefined,
          };
        })
      );

      await api.createPortfolioManual({
        name: name.trim(),
        batch_ids: batchIds,
        assets,
        is_start_date: isStartDate || undefined,
        optimization_end_date: oosStartDate || undefined,
        portfolio_source: "manual",
        mode,
        optimizer_settings: {
          batch_ids: batchIds,
          source: "manual_builder",
        },
      });

      setSuccess("Portfolio saved! Switch to the Saved Portfolios tab to view it.");
      setName("");
      setGroups([]);
      setIsStartDate("");
      setOosStartDate("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save portfolio");
    }
    setSaving(false);
  };

  if (loadingBatches) {
    return <p className="text-xs text-ag-text-muted py-4">Loading available batches...</p>;
  }

  if (batches.length === 0) {
    return (
      <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-center">
        <p className="text-sm text-ag-text-muted">No completed batch backtests found.</p>
        <p className="text-xs text-ag-text-muted mt-1">
          Run a batch backtest first, then come back here to build a portfolio.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Portfolio name + dates */}
      <div className="bg-ag-card border border-ag-border rounded-lg p-4 space-y-3">
        <div className="flex items-center gap-4">
          <div className="flex-1">
            <label className="text-xs text-ag-text-muted block mb-1">Portfolio Name</label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)}
              placeholder="e.g. RSI + Mean Reversion Manual"
              className="w-full bg-ag-dark border border-ag-border rounded px-3 py-1.5 text-sm" />
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">IS Start Date</label>
            <input type="date" value={isStartDate} onChange={(e) => setIsStartDate(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">OOS Start Date</label>
            <input type="date" value={oosStartDate} onChange={(e) => setOosStartDate(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
          </div>
          <div className="text-[10px] text-ag-text-muted mt-4">
            IS = training period &bull; OOS = unseen data starts here
          </div>
        </div>
        {/* Live-loop mode */}
        <div className="flex items-center gap-4 pt-2 border-t border-ag-border">
          <div>
            <label className="text-xs text-ag-text-muted block mb-1" title="Hybrid: basket + weights refresh nightly via Step-2. Non-hybrid: basket frozen until OOS boundary.">
              Mode
            </label>
            <div className="inline-flex rounded overflow-hidden border border-ag-border">
              <button type="button" onClick={() => setMode("non_hybrid")}
                className={`px-3 py-1 text-xs ${mode === "non_hybrid" ? "bg-ag-accent text-white" : "bg-ag-dark text-ag-text-muted hover:text-ag-text"}`}>
                Non-Hybrid (frozen)
              </button>
              <button type="button" onClick={() => setMode("hybrid")}
                className={`px-3 py-1 text-xs ${mode === "hybrid" ? "bg-ag-accent text-white" : "bg-ag-dark text-ag-text-muted hover:text-ag-text"}`}>
                Hybrid (nightly refresh)
              </button>
            </div>
          </div>
          <div className="text-[10px] text-ag-text-muted max-w-md">
            {mode === "hybrid"
              ? "Basket + weights refresh nightly from the latest trade data. Use when you want the portfolio to track Step-2 changes as new fills arrive."
              : "Basket is frozen until the next OOS boundary. Use when you want deterministic behavior (no mid-period basket drift)."}
          </div>
        </div>
      </div>

      {/* Strategy groups */}
      {groups.map((g) => (
        <StrategyGroupCard
          key={g.id}
          group={g}
          batches={batches}
          onRemove={() => removeGroup(g.id)}
          onBatchChange={(batchId, direction) => {
            updateGroup(g.id, { batchId, direction: direction as StrategyGroup["direction"] });
            loadAssetsForGroup(g.id, batchId, direction);
          }}
          onDirectionChange={(dir) => {
            updateGroup(g.id, { direction: dir as StrategyGroup["direction"] });
            if (g.batchId) loadAssetsForGroup(g.id, g.batchId, dir);
          }}
          onToggleAsset={(asset) => toggleAsset(g.id, asset)}
          onSelectAll={() => selectAllAssets(g.id)}
        />
      ))}

      {/* Add strategy group button */}
      <button onClick={addGroup}
        className="flex items-center gap-2 px-4 py-2 border border-dashed border-ag-border rounded-lg text-sm text-ag-text-muted hover:text-ag-accent hover:border-ag-accent transition-colors w-full justify-center">
        <PlusCircle size={16} />
        Add Strategy Group
      </button>

      {/* Summary + save */}
      {totalAssets > 0 && (
        <div className="bg-ag-card border border-ag-border rounded-lg p-4 flex items-center justify-between">
          <div className="text-sm">
            <span className="text-ag-text font-medium">{totalAssets} asset{totalAssets !== 1 ? "s" : ""}</span>
            <span className="text-ag-text-muted"> across </span>
            <span className="text-ag-text font-medium">{groups.filter((g) => g.selectedAssets.size > 0).length} group{groups.filter((g) => g.selectedAssets.size > 0).length !== 1 ? "s" : ""}</span>
          </div>
          <button onClick={handleSave} disabled={saving}
            className="px-4 py-2 bg-ag-accent text-white rounded-lg text-sm font-medium hover:bg-ag-accent/80 disabled:opacity-50">
            {saving ? "Saving..." : "Save Portfolio"}
          </button>
        </div>
      )}

      {error && <p className="text-xs text-ag-red">{error}</p>}
      {success && <p className="text-xs text-ag-green">{success}</p>}
    </div>
  );
}

function StrategyGroupCard({
  group, batches, onRemove, onBatchChange, onDirectionChange, onToggleAsset, onSelectAll,
}: {
  group: StrategyGroup;
  batches: BuilderBatch[];
  onRemove: () => void;
  onBatchChange: (batchId: number, direction: string) => void;
  onDirectionChange: (dir: string) => void;
  onToggleAsset: (asset: string) => void;
  onSelectAll: () => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const allSelected = group.availableAssets.length > 0 &&
    group.availableAssets.every((a) => group.selectedAssets.has(a.asset_name));

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-ag-dark/30">
        <div className="flex items-center gap-3">
          <button onClick={() => setExpanded(!expanded)} className="text-ag-text-muted hover:text-ag-text">
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
          <span className="text-sm font-medium text-ag-text">
            {group.strategyName || "Select a batch"}
          </span>
          {group.selectedAssets.size > 0 && (
            <span className="px-1.5 py-0.5 bg-ag-accent/15 text-ag-accent rounded text-[10px] font-medium">
              {group.selectedAssets.size} selected
            </span>
          )}
        </div>
        <button onClick={onRemove} className="text-ag-text-muted hover:text-ag-red">
          <Trash2 size={14} />
        </button>
      </div>

      {expanded && (
        <div className="px-4 py-3 space-y-3">
          {/* Batch + direction selection */}
          <div className="flex items-end gap-3">
            <div className="flex-1">
              <label className="text-[10px] text-ag-text-muted block mb-1">Batch Backtest</label>
              <select
                value={group.batchId ?? ""}
                onChange={(e) => {
                  const bid = parseInt(e.target.value);
                  if (!isNaN(bid)) onBatchChange(bid, group.direction);
                }}
                className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1.5 text-xs"
              >
                <option value="">Select batch...</option>
                {batches.map((b) => (
                  <option key={b.batch_id} value={b.batch_id}>
                    {b.name} ({b.strategy_name}, {b.completed_symbols} symbols)
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-ag-text-muted block mb-1">Direction</label>
              <div className="flex gap-1">
                {(["Long", "Short", "Both"] as const).map((dir) => (
                  <button key={dir} onClick={() => onDirectionChange(dir)}
                    className={`px-3 py-1.5 text-xs rounded transition-colors ${
                      group.direction === dir
                        ? dir === "Long" ? "bg-ag-green/15 text-ag-green font-medium"
                          : dir === "Short" ? "bg-ag-red/15 text-ag-red font-medium"
                          : "bg-ag-accent/15 text-ag-accent font-medium"
                        : "text-ag-text-muted hover:text-ag-text bg-ag-dark border border-ag-border"
                    }`}>
                    {dir}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Assets */}
          {group.loadingAssets && (
            <p className="text-xs text-ag-text-muted">Loading assets...</p>
          )}

          {!group.loadingAssets && group.availableAssets.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[10px] text-ag-text-muted uppercase tracking-wider">
                  Assets ({group.availableAssets.length})
                </span>
                <button onClick={onSelectAll}
                  className="text-[10px] text-ag-accent hover:underline">
                  {allSelected ? "Deselect all" : "Select all"}
                </button>
              </div>
              <div className="max-h-64 overflow-y-auto space-y-0.5">
                {group.availableAssets.map((a) => {
                  const selected = group.selectedAssets.has(a.asset_name);
                  return (
                    <button key={a.asset_name} onClick={() => onToggleAsset(a.asset_name)}
                      className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-xs transition-colors ${
                        selected ? "bg-ag-accent/10 border border-ag-accent/30" : "hover:bg-ag-dark/50 border border-transparent"
                      }`}>
                      <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${
                        selected ? "bg-ag-accent border-ag-accent" : "border-ag-border"
                      }`}>
                        {selected && <Check size={10} className="text-white" />}
                      </div>
                      <span className="font-medium text-left min-w-[100px]">
                        {a.symbol.replace("USDT", "")}
                      </span>
                      <span className={`font-mono ${a.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                        ${a.net_profit.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                      </span>
                      <span className="text-ag-text-muted">
                        PF: {a.profit_factor != null ? a.profit_factor.toFixed(2) : "\u2014"}
                      </span>
                      <span className="text-ag-text-muted">
                        WR: {a.win_rate.toFixed(1)}%
                      </span>
                      <span className="text-ag-text-muted">
                        {a.trades} trades
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {!group.loadingAssets && group.batchId && group.availableAssets.length === 0 && (
            <p className="text-xs text-ag-text-muted">
              No assets found for this batch/direction combo.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
