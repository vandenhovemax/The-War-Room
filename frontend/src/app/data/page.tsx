"use client";
import { useState, useCallback, useEffect } from "react";
import {
  Download, Database, Search, RefreshCw, CheckCircle2,
  Plus, Trash2, Play, Loader2, AlertCircle, Star, ChevronDown, ChevronRight,
  Pencil, X,
} from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import type { DataStatusItem, DownloadProgress, WatchlistItem } from "@/lib/api";
import CategoryFilterSelect from "@/components/common/CategoryFilterSelect";
import { useTaskManager } from "@/contexts/TaskManager";

const timeframes = [
  { value: "ALL", label: "All TF" },
  { value: "1", label: "1m" },
  { value: "5", label: "5m" },
  { value: "15", label: "15m" },
  { value: "30", label: "30m" },
  { value: "60", label: "1h" },
  { value: "240", label: "4h" },
  { value: "720", label: "12h" },
  { value: "D", label: "1D" },
  { value: "3D", label: "3D" },
];

function formatUsdt(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B USDT`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M USDT`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K USDT`;
  return `${value.toFixed(0)} USDT`;
}

const assetTabs = [
  { key: "crypto", label: "Crypto" },
  { key: "forex", label: "Forex" },
  { key: "commodities", label: "Commodities" },
  { key: "nyse", label: "NYSE" },
  { key: "tsx", label: "TSX" },
] as const;

type AssetClass = typeof assetTabs[number]["key"];

export default function DataPage() {
  const { data: symbolsData, loading: loadingSymbols } = useApi(
    useCallback(() => api.symbols(), []),
    "symbols",
  );
  const { data: status, refetch: refetchStatus } = useApi(
    useCallback(() => api.dataStatus(), []),
    "data-status",
  );
  const { data: watchlist, refetch: refetchWatchlist } = useApi(
    useCallback(() => api.watchlist(), []),
    "watchlist",
  );

  const [search, setSearch] = useState("");
  const [selectedTf, setSelectedTf] = useState("30");
  const [addSymbolInput, setAddSymbolInput] = useState("");
  const [activeTab, setActiveTab] = useState<"watchlist" | "all">("watchlist");
  const [assetClass, setAssetClass] = useState<AssetClass>("crypto");
  const [autoUsdt, setAutoUsdt] = useState(true);
  const [showStoredData, setShowStoredData] = useState(false);
  const [visibleCount, setVisibleCount] = useState(200);
  const [sortBy, setSortBy] = useState<"symbol" | "adv" | "candles" | "status">("symbol");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [backfillFrom, setBackfillFrom] = useState<Record<string, string>>({});
  const [categories, setCategories] = useState<Record<string, string[]>>({});
  const [editingCatSymbol, setEditingCatSymbol] = useState<string | null>(null);
  const [editingCatValue, setEditingCatValue] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string[]>([]);

  // Download progress — from global TaskManager (persists across navigation)
  const { startDownload, cancelTask, latestDownloadTask } = useTaskManager();
  const progress: Record<string, DownloadProgress> = latestDownloadTask?.progress ?? {};

  // Fetch asset categories
  useEffect(() => {
    api.categories().then(setCategories).catch(() => {});
  }, []);

  const symbols = symbolsData?.symbols ?? [];
  const watchlistSymbols = new Set((watchlist ?? []).map((w) => w.symbol));

  const filtered = symbols.filter((s) =>
    s.toLowerCase().includes(search.toLowerCase())
  );

  // Refresh stored data counts when downloads complete
  useEffect(() => {
    if (latestDownloadTask?.status === "completed") refetchStatus();
  }, [latestDownloadTask?.status, refetchStatus]);

  const handleDownload = async (symbol: string, startDate?: string) => {
    try {
      await api.download(symbol, selectedTf, startDate);
      // Start global polling via TaskManager (if not already running)
      if (!latestDownloadTask || latestDownloadTask.status !== "running") {
        startDownload(`Download ${symbol}`);
      }
    } catch (e) {
      console.error("Download failed:", e);
    }
  };

  const handleDownloadWatchlist = async () => {
    try {
      await api.downloadWatchlist(selectedTf);
      // Only create a new download task if one isn't already running (prevents double polling)
      if (!latestDownloadTask || latestDownloadTask.status !== "running") {
        startDownload("Download Watchlist");
      }
    } catch (e) {
      console.error("Batch download failed:", e);
    }
  };

  const handleAddToWatchlist = async () => {
    let syms = addSymbolInput
      .toUpperCase()
      .split(/[,\s]+/)
      .map((s) => s.trim().replace(/\.P$/, ""))  // strip .P suffix
      .filter((s) => s.length > 0);
    // Auto-append USDT if toggle is on and symbol doesn't already end with USDT
    if (autoUsdt) {
      syms = syms.map((s) => s.endsWith("USDT") ? s : s + "USDT");
    }
    // If Bybit symbols are loaded, validate — otherwise accept as-is
    if (symbols.length > 0) {
      const valid = syms.filter((s) => symbols.includes(s));
      const invalid = syms.filter((s) => !symbols.includes(s));
      if (invalid.length > 0 && valid.length === 0) {
        alert(`Symbol(s) not found on Bybit: ${invalid.join(", ")}\nUse the exact name without .P (e.g. ATOM, BTC, ETH — USDT is added automatically)`);
        return;
      }
      syms = valid;
    }
    if (syms.length === 0) return;
    await api.addToWatchlist(syms);
    setAddSymbolInput("");
    refetchWatchlist();
  };

  const handleRemoveFromWatchlist = async (symbol: string) => {
    await api.removeFromWatchlist(symbol);
    refetchWatchlist();
  };

  const handleAddFiltered = async () => {
    const syms = filtered.slice(0, 50);
    if (syms.length === 0) return;
    await api.addToWatchlist(syms);
    refetchWatchlist();
  };

  const startEditCategory = (symbol: string) => {
    setEditingCatSymbol(symbol);
    setEditingCatValue((categories[symbol] ?? []).join(", "));
  };

  const saveCategory = async (symbol: string) => {
    const cats = editingCatValue
      .split(/[,;]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    try {
      await api.updateCategories(symbol, cats);
      setCategories((prev) => ({ ...prev, [symbol]: cats }));
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to save categories");
    }
    setEditingCatSymbol(null);
  };

  const handleDeleteData = async (symbol: string, timeframe?: string) => {
    const tf = timeframe || selectedTf;
    const label = tf === "ALL" ? "ALL timeframes" : `${tfLabel(tf)}`;
    if (!confirm(`Delete stored data for ${symbol} (${label})? This cannot be undone.`)) return;
    setDeleting(symbol);
    try {
      await api.deleteOhlcv(symbol, tf === "ALL" ? undefined : tf);
      refetchStatus();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Delete failed");
    }
    setDeleting(null);
  };

  const getStoredInfo = (symbol: string): DataStatusItem | undefined =>
    selectedTf === "ALL"
      ? status?.find((s) => s.symbol === symbol)
      : status?.find((s) => s.symbol === symbol && s.timeframe === selectedTf);

  // When ALL TF is selected, get all status items for a symbol
  const getStoredInfoAll = (symbol: string): DataStatusItem[] =>
    status?.filter((s) => s.symbol === symbol) ?? [];

  // TF label helper
  const tfLabel = (val: string) =>
    val === "D" ? "1D" : val === "60" ? "1h" : val === "240" ? "4h" : `${val}m`;

  const getProgress = (symbol: string): DownloadProgress | undefined =>
    progress[symbol];

  const renderProgressBadge = (p: DownloadProgress) => {
    if (p.status === "queued")
      return <span className="text-ag-yellow text-xs">Queued...</span>;
    if (p.status === "downloading")
      return (
        <span className="text-ag-blue text-xs flex items-center gap-1">
          <Loader2 size={12} className="animate-spin" />
          Page {p.pages_done} · {p.candles_so_far.toLocaleString()} candles
        </span>
      );
    if (p.status === "done")
      return (
        <span className="text-ag-green text-xs flex items-center gap-1">
          <CheckCircle2 size={12} /> {p.candles_so_far.toLocaleString()} candles
        </span>
      );
    if (p.status === "error")
      return (
        <span className="text-ag-red text-xs flex items-center gap-1">
          <AlertCircle size={12} /> Error
        </span>
      );
    return null;
  };

  const activeDownloads = Object.values(progress).filter(
    (p) => p.status === "downloading" || p.status === "queued"
  ).length;

  // Symbols that have ANY downloaded data (for ALL TF mode)
  const downloadedSymbolSet = new Set(status?.map((s) => s.symbol) ?? []);

  const displaySymbols = (() => {
    const base = activeTab === "watchlist"
      ? symbols.filter((s) => watchlistSymbols.has(s))
      : filtered;
    // When ALL TF is selected, only show symbols with downloaded data
    let list = selectedTf === "ALL" ? base.filter((s) => downloadedSymbolSet.has(s)) : base;
    // Filter by selected categories (empty = show all)
    if (categoryFilter.length > 0) {
      list = list.filter((s) => {
        const cats = categories[s] ?? [];
        return cats.some((c) => categoryFilter.includes(c));
      });
    }
    // Sort
    return [...list].sort((a, b) => {
      const dir = sortDir === "asc" ? 1 : -1;
      if (sortBy === "symbol") return dir * a.localeCompare(b);
      const aInfo = status?.find((s) => s.symbol === a && (selectedTf === "ALL" || s.timeframe === selectedTf));
      const bInfo = status?.find((s) => s.symbol === b && (selectedTf === "ALL" || s.timeframe === selectedTf));
      if (sortBy === "adv") return dir * ((aInfo?.avg_daily_volume ?? 0) - (bInfo?.avg_daily_volume ?? 0));
      if (sortBy === "candles") return dir * ((aInfo?.candle_count ?? 0) - (bInfo?.candle_count ?? 0));
      if (sortBy === "status") {
        const aHas = aInfo ? 1 : 0;
        const bHas = bInfo ? 1 : 0;
        return dir * (aHas - bHas);
      }
      return 0;
    });
  })();

  const handleSort = (col: "symbol" | "adv" | "candles" | "status") => {
    if (sortBy === col) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortBy(col); setSortDir(col === "symbol" ? "asc" : "desc"); }
  };

  return (
    <div className="space-y-6">
      {/* Asset class tabs */}
      <div className="flex gap-1 bg-ag-card border border-ag-border rounded-lg p-1 w-fit">
        {assetTabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setAssetClass(tab.key)}
            className={`px-4 py-1.5 text-sm rounded-md transition-colors ${
              assetClass === tab.key
                ? "bg-ag-accent text-white"
                : "text-ag-text-muted hover:text-ag-text"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Placeholder for non-crypto asset classes */}
      {assetClass !== "crypto" && (
        <div className="bg-ag-card border border-ag-border rounded-xl p-12 text-center">
          <div className="inline-flex p-4 rounded-2xl bg-ag-accent/10 text-ag-accent mb-4">
            <Database size={40} />
          </div>
          <h2 className="text-lg font-semibold mb-2">
            {assetTabs.find((t) => t.key === assetClass)?.label} Data
          </h2>
          <p className="text-ag-text-muted text-sm">
            Coming soon — API integration for {assetTabs.find((t) => t.key === assetClass)?.label} data is not yet configured.
          </p>
        </div>
      )}

      {/* Crypto content — only shown when crypto tab is active */}
      {assetClass === "crypto" && <>

      {/* Controls bar */}
      <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
        <div className="flex flex-wrap items-end gap-4">
          {/* Timeframe */}
          <div>
            <label className="block text-xs text-ag-text-muted mb-1.5">Timeframe</label>
            <div className="flex gap-1">
              {timeframes.map((tf) => (
                <button
                  key={tf.value}
                  onClick={() => setSelectedTf(tf.value)}
                  className={`px-3 py-2 text-sm rounded-lg transition-colors ${
                    selectedTf === tf.value
                      ? "bg-ag-accent text-white"
                      : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
                  }`}
                >
                  {tf.label}
                </button>
              ))}
            </div>
          </div>

          {/* Actions */}
          {watchlistSymbols.size > 0 && selectedTf !== "ALL" && (
            <button
              onClick={handleDownloadWatchlist}
              disabled={activeDownloads > 0}
              className="flex items-center gap-2 bg-ag-accent hover:bg-ag-accent-hover disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm transition-colors"
            >
              {activeDownloads > 0 ? (
                <><Loader2 size={16} className="animate-spin" /> Downloading ({activeDownloads})...</>
              ) : (
                <><Play size={16} /> Download All Watchlist ({watchlistSymbols.size})</>
              )}
            </button>
          )}
          {activeDownloads > 0 && latestDownloadTask && (
            <button
              onClick={() => cancelTask(latestDownloadTask.id)}
              className="flex items-center gap-2 bg-ag-red/15 text-ag-red border border-ag-red/30 px-4 py-2 rounded-lg text-sm hover:bg-ag-red/25 transition-colors"
            >
              <X size={16} /> Stop Downloads
            </button>
          )}
          {watchlistSymbols.size > 0 && selectedTf !== "ALL" && activeDownloads === 0 && (
            <button
              onClick={handleDownloadWatchlist}
              className="flex items-center gap-2 bg-ag-card-hover border border-ag-border text-ag-text-muted hover:text-ag-text px-4 py-2 rounded-lg text-sm transition-colors"
              title="Download latest candles for all watchlist symbols"
            >
              <RefreshCw size={16} /> Refresh Data
            </button>
          )}
        </div>

        {/* Add to watchlist */}
        <div className="flex gap-2 items-center">
          <div className="relative flex-1">
            <Plus size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-ag-text-muted" />
            <input
              type="text"
              value={addSymbolInput}
              onChange={(e) => setAddSymbolInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAddToWatchlist()}
              placeholder={autoUsdt ? "Add to watchlist: BTC, ETH, SOL..." : "Add to watchlist: BTCUSDT, ETHUSDT..."}
              className="w-full bg-ag-dark border border-ag-border rounded-lg pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-ag-accent"
            />
          </div>
          <label className="flex items-center gap-1.5 text-xs text-ag-text-muted whitespace-nowrap cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoUsdt}
              onChange={(e) => setAutoUsdt(e.target.checked)}
              className="accent-ag-accent"
            />
            Auto USDT
          </label>
          <button
            onClick={handleAddToWatchlist}
            className="bg-ag-green/15 text-ag-green hover:bg-ag-green/25 px-4 py-2 rounded-lg text-sm transition-colors"
          >
            Add
          </button>
        </div>

        <p className="text-xs text-ag-text-muted">
          {loadingSymbols ? "Loading symbols from Bybit..." : `${symbolsData?.count ?? 0} USDT perpetuals available`}
          {" · "}{watchlistSymbols.size} in watchlist
          {" · "}{status?.length ?? 0} symbols with stored data
          {activeDownloads > 0 && ` · ${activeDownloads} downloads active`}
        </p>
      </div>

      {/* Stored data summary — collapsible */}
      {status && status.length > 0 && (
        <div className="bg-ag-card border border-ag-border rounded-xl p-5">
          <button
            onClick={() => setShowStoredData(!showStoredData)}
            className="w-full flex items-center gap-2 text-sm font-semibold hover:text-ag-accent transition-colors text-left"
          >
            {showStoredData ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            <Database size={16} className="text-ag-accent" />
            Stored Data ({status.length} symbol{status.length !== 1 ? "s" : ""})
          </button>
          {showStoredData && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2 mt-3">
              {status.map((item) => (
                <div key={`${item.symbol}-${item.timeframe}`} className="flex items-center justify-between bg-ag-dark rounded-lg px-3 py-2 text-sm">
                  <div>
                    <span className="font-medium">{item.symbol}</span>
                    <span className="text-ag-text-muted ml-2 text-xs">{item.timeframe}m</span>
                  </div>
                  <div className="text-right">
                    <span className="text-ag-green text-xs">{item.candle_count.toLocaleString()}</span>
                    {item.avg_daily_volume > 0 && (
                      <p className="text-[10px] text-ag-blue">
                        ADV {formatUsdt(item.avg_daily_volume)}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tab selector + search */}
      <div className="flex items-center gap-4">
        <div className="flex gap-1 bg-ag-card border border-ag-border rounded-lg p-1">
          <button
            onClick={() => setActiveTab("watchlist")}
            className={`px-4 py-1.5 text-sm rounded-md transition-colors ${
              activeTab === "watchlist" ? "bg-ag-accent text-white" : "text-ag-text-muted hover:text-ag-text"
            }`}
          >
            <Star size={14} className="inline mr-1.5 -mt-0.5" />
            Watchlist ({watchlistSymbols.size})
          </button>
          <button
            onClick={() => setActiveTab("all")}
            className={`px-4 py-1.5 text-sm rounded-md transition-colors ${
              activeTab === "all" ? "bg-ag-accent text-white" : "text-ag-text-muted hover:text-ag-text"
            }`}
          >
            All Symbols
          </button>
        </div>

        {activeTab === "all" && (
          <div className="relative flex-1 max-w-sm">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-ag-text-muted" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search symbols..."
              className="w-full bg-ag-card border border-ag-border rounded-lg pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-ag-accent"
            />
          </div>
        )}

        {activeTab === "all" && search && filtered.length > 0 && (
          <button
            onClick={handleAddFiltered}
            className="text-xs text-ag-accent hover:text-ag-accent-hover"
          >
            + Add {Math.min(filtered.length, 50)} to watchlist
          </button>
        )}
      </div>

      {/* Category filter */}
      <div className="bg-ag-card border border-ag-border rounded-xl px-4 py-3">
        <CategoryFilterSelect selected={categoryFilter} onChange={setCategoryFilter} />
      </div>

      {/* Symbol table */}
      <div className="bg-ag-card border border-ag-border rounded-xl overflow-hidden">
        <div className="max-h-[500px] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-ag-darker sticky top-0">
              <tr className="text-ag-text-muted text-xs">
                <th className="text-left px-4 py-2.5 cursor-pointer hover:text-ag-text select-none" onClick={() => handleSort("symbol")}>
                  Symbol {sortBy === "symbol" ? (sortDir === "asc" ? "\u25B2" : "\u25BC") : ""}
                </th>
                <th className="text-left px-4 py-2.5">Category</th>
                {selectedTf === "ALL" && <th className="text-left px-4 py-2.5">Timeframes</th>}
                <th className="text-right px-4 py-2.5 cursor-pointer hover:text-ag-text select-none" onClick={() => handleSort("candles")}>
                  Stored {sortBy === "candles" ? (sortDir === "asc" ? "\u25B2" : "\u25BC") : ""}
                </th>
                <th className="text-right px-4 py-2.5 cursor-pointer hover:text-ag-text select-none" onClick={() => handleSort("adv")}>
                  ADV (USDT) {sortBy === "adv" ? (sortDir === "asc" ? "\u25B2" : "\u25BC") : ""}
                </th>
                <th className="text-right px-4 py-2.5">Date Range</th>
                <th className="text-right px-4 py-2.5 cursor-pointer hover:text-ag-text select-none" onClick={() => handleSort("status")}>
                  Status {sortBy === "status" ? (sortDir === "asc" ? "\u25B2" : "\u25BC") : ""}
                </th>
                {selectedTf !== "ALL" && <th className="text-right px-4 py-2.5">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {displaySymbols.length === 0 && (
                <tr>
                  <td colSpan={selectedTf === "ALL" ? 8 : 7} className="px-4 py-8 text-center text-ag-text-muted">
                    {selectedTf === "ALL"
                      ? "No downloaded data found."
                      : activeTab === "watchlist"
                        ? "Watchlist is empty. Add symbols above or switch to \"All Symbols\" tab."
                        : "No symbols match your search."}
                  </td>
                </tr>
              )}
              {displaySymbols.slice(0, visibleCount).map((symbol) => {
                const stored = getStoredInfo(symbol);
                const allTfItems = selectedTf === "ALL" ? getStoredInfoAll(symbol) : [];
                const prog = getProgress(symbol);
                const isInWatchlist = watchlistSymbols.has(symbol);
                // For ALL mode: aggregate candles and pick max ADV
                const totalCandles = selectedTf === "ALL"
                  ? allTfItems.reduce((sum, i) => sum + i.candle_count, 0)
                  : stored?.candle_count ?? 0;
                const maxAdv = selectedTf === "ALL"
                  ? Math.max(0, ...allTfItems.map((i) => i.avg_daily_volume))
                  : stored?.avg_daily_volume ?? 0;
                // Date range: widest across all TFs
                const earliest = selectedTf === "ALL"
                  ? allTfItems.reduce((e, i) => (!e || (i.earliest && i.earliest < e) ? i.earliest : e), null as string | null)
                  : stored?.earliest ?? null;
                const latest = selectedTf === "ALL"
                  ? allTfItems.reduce((l, i) => (!l || (i.latest && i.latest > l) ? i.latest : l), null as string | null)
                  : stored?.latest ?? null;
                return (
                  <tr key={symbol} className="border-t border-ag-border/50 hover:bg-ag-card-hover">
                    <td className="px-4 py-2.5 font-medium">{symbol}</td>
                    <td className="px-4 py-2.5">
                      {editingCatSymbol === symbol ? (
                        <div className="flex items-center gap-1">
                          <input
                            type="text"
                            value={editingCatValue}
                            onChange={(e) => setEditingCatValue(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") saveCategory(symbol);
                              if (e.key === "Escape") setEditingCatSymbol(null);
                            }}
                            autoFocus
                            placeholder="AI, DeFi, L1..."
                            className="bg-ag-dark border border-ag-accent rounded px-1.5 py-0.5 text-[11px] w-[140px] focus:outline-none"
                          />
                          <button onClick={() => saveCategory(symbol)}
                            className="text-ag-green hover:text-ag-green/70" title="Save">
                            <CheckCircle2 size={13} />
                          </button>
                          <button onClick={() => setEditingCatSymbol(null)}
                            className="text-ag-text-muted hover:text-ag-red" title="Cancel">
                            <X size={13} />
                          </button>
                        </div>
                      ) : (
                        <div className="group flex items-center gap-1 cursor-pointer" onClick={() => startEditCategory(symbol)}>
                          {categories[symbol]?.length ? (
                            <div className="flex flex-wrap gap-0.5">
                              {categories[symbol].slice(0, 3).map((cat) => (
                                <span key={cat} className="inline-block px-1.5 py-0.5 text-[10px] rounded bg-ag-accent/10 text-ag-text-muted">
                                  {cat}
                                </span>
                              ))}
                              {categories[symbol].length > 3 && (
                                <span className="text-[10px] text-ag-text-muted">+{categories[symbol].length - 3}</span>
                              )}
                            </div>
                          ) : (
                            <span className="text-ag-text-muted text-xs">—</span>
                          )}
                          <Pencil size={10} className="text-ag-text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
                        </div>
                      )}
                    </td>
                    {selectedTf === "ALL" && (
                      <td className="px-4 py-2.5">
                        <div className="flex flex-wrap gap-1">
                          {allTfItems.map((i) => (
                            <span key={i.timeframe}
                              className="inline-block px-1.5 py-0.5 text-[10px] rounded bg-ag-accent/15 text-ag-accent font-medium">
                              {tfLabel(i.timeframe)}
                            </span>
                          ))}
                        </div>
                      </td>
                    )}
                    <td className="px-4 py-2.5 text-right">
                      {totalCandles > 0 ? (
                        <span className="text-ag-green">{totalCandles.toLocaleString()}</span>
                      ) : (
                        <span className="text-ag-text-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right text-xs">
                      {maxAdv > 0 ? (
                        <span className="text-ag-blue">{formatUsdt(maxAdv)}</span>
                      ) : (
                        <span className="text-ag-text-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right text-ag-text-muted text-xs">
                      {earliest && latest
                        ? `${earliest.slice(0, 10)} → ${latest.slice(0, 10)}`
                        : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {prog ? renderProgressBadge(prog) : null}
                    </td>
                    {selectedTf !== "ALL" && (
                      <td className="px-4 py-2.5 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {stored && (
                            <input
                              type="date"
                              value={backfillFrom[symbol] ?? ""}
                              onChange={(e) => setBackfillFrom((prev) => ({ ...prev, [symbol]: e.target.value }))}
                              className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-[11px] text-ag-text-muted w-[110px] focus:outline-none focus:border-ag-accent"
                              title="Backfill from date (download older data)"
                            />
                          )}
                          {stored ? (
                            <button
                              onClick={() => {
                                const sd = backfillFrom[symbol];
                                handleDownload(symbol, sd || undefined);
                              }}
                              disabled={prog?.status === "downloading" || prog?.status === "queued"}
                              className="text-ag-green hover:text-ag-green/70 text-xs disabled:opacity-50"
                              title={backfillFrom[symbol] ? `Backfill from ${backfillFrom[symbol]}` : "Refresh — download new candles"}
                            >
                              <RefreshCw size={14} />
                            </button>
                          ) : (
                            <button
                              onClick={() => handleDownload(symbol)}
                              disabled={prog?.status === "downloading" || prog?.status === "queued"}
                              className="text-ag-accent hover:text-ag-accent-hover text-xs disabled:opacity-50"
                              title="Download full history"
                            >
                              <Download size={14} />
                            </button>
                          )}
                          {activeTab === "all" && !isInWatchlist && (
                            <button
                              onClick={async () => {
                                await api.addToWatchlist([symbol]);
                                refetchWatchlist();
                              }}
                              className="text-ag-text-muted hover:text-ag-yellow text-xs"
                              title="Add to watchlist"
                            >
                              <Star size={14} />
                            </button>
                          )}
                          {isInWatchlist && (
                            <button
                              onClick={() => handleRemoveFromWatchlist(symbol)}
                              className="text-ag-text-muted hover:text-ag-red text-xs"
                              title="Remove from watchlist"
                            >
                              <Trash2 size={14} />
                            </button>
                          )}
                          {stored && (
                            <button
                              onClick={() => handleDeleteData(symbol)}
                              disabled={deleting === symbol}
                              className="text-ag-text-muted hover:text-ag-red text-xs disabled:opacity-50"
                              title={`Delete stored ${tfLabel(selectedTf)} data for ${symbol}`}
                            >
                              {deleting === symbol
                                ? <Loader2 size={14} className="animate-spin" />
                                : <Database size={14} className="text-ag-red/50 hover:text-ag-red" />}
                            </button>
                          )}
                        </div>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {displaySymbols.length > visibleCount && (
            <div className="text-center py-3 space-y-1">
              <p className="text-xs text-ag-text-muted">
                Showing {visibleCount} of {displaySymbols.length}
              </p>
              <button
                onClick={() => setVisibleCount((prev) => prev + 200)}
                className="text-xs text-ag-accent hover:text-ag-accent-hover underline"
              >
                Show {Math.min(200, displaySymbols.length - visibleCount)} more
              </button>
              {visibleCount < displaySymbols.length && (
                <button
                  onClick={() => setVisibleCount(displaySymbols.length)}
                  className="text-xs text-ag-text-muted hover:text-ag-text underline ml-3"
                >
                  Show all
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      </>}
    </div>
  );
}
