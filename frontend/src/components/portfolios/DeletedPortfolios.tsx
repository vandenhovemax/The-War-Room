"use client";
import { useState, useEffect } from "react";
import { Trash2, RotateCcw, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";

export default function DeletedPortfolios() {
  const { addToast } = useTaskManager();
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try { setPortfolios(await api.portfoliosDeleted()); } catch { addToast("Failed to load deleted portfolios", "error"); }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleRestore = async (id: number) => {
    try {
      await api.restorePortfolio(id);
      setPortfolios((prev) => prev.filter((p) => p.id !== id));
      addToast("Portfolio restored to Saved", "success");
    } catch { addToast("Failed to restore portfolio", "error"); }
  };

  const handlePermanentDelete = async (id: number) => {
    if (!confirm("PERMANENTLY delete this portfolio?\n\nThis cannot be undone. All portfolio data will be lost forever.")) return;
    try {
      await api.permanentlyDeletePortfolio(id);
      setPortfolios((prev) => prev.filter((p) => p.id !== id));
      addToast("Portfolio permanently deleted", "success");
    } catch { addToast("Failed to delete portfolio", "error"); }
  };

  if (loading) return <p className="text-sm text-ag-text-muted">Loading...</p>;

  if (portfolios.length === 0) {
    return (
      <div className="text-center py-8">
        <Trash2 size={24} className="mx-auto text-ag-text-muted mb-2" />
        <p className="text-ag-text-muted text-sm">No deleted portfolios.</p>
        <p className="text-ag-text-muted text-xs mt-1">
          Deleted portfolios appear here and can be restored.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 px-3 py-2 bg-yellow-500/10 text-yellow-500 rounded-lg text-xs">
        <AlertTriangle size={14} />
        Deleted portfolios can be restored or permanently removed.
      </div>
      <p className="text-xs text-ag-text-muted">
        {portfolios.length} deleted portfolio{portfolios.length !== 1 ? "s" : ""}
      </p>
      {portfolios.map((p) => (
        <div key={p.id} className="flex items-center gap-3 p-3 bg-ag-card border border-ag-border rounded-lg">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium truncate">{p.name}</p>
            <p className="text-[10px] text-ag-text-muted">
              {p.asset_count} assets
              {p.deleted_at && ` · Deleted ${new Date(p.deleted_at).toLocaleDateString()}`}
            </p>
          </div>
          <div className="flex items-center gap-3 text-xs text-ag-text-muted">
            {p.metrics_snapshot?.profit_factor !== undefined && (
              <span>{p.optimization_end_date ? "IS " : ""}PF: {Number(p.metrics_snapshot.profit_factor).toFixed(2)}</span>
            )}
          </div>
          <button onClick={() => handleRestore(p.id)}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-green/15 text-ag-green hover:bg-ag-green/25 transition-colors"
            title="Restore to Saved Portfolios">
            <RotateCcw size={12} /> Restore
          </button>
          <button onClick={() => handlePermanentDelete(p.id)}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-red/15 text-ag-red hover:bg-ag-red/25 transition-colors"
            title="Permanently delete — cannot be undone">
            <Trash2 size={12} /> Permanent
          </button>
        </div>
      ))}
    </div>
  );
}
