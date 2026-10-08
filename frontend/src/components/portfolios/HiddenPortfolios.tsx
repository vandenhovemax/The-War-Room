"use client";
import { useState, useEffect } from "react";
import { EyeOff, RotateCcw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";

export default function HiddenPortfolios() {
  const { addToast } = useTaskManager();
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try { setPortfolios(await api.portfoliosHidden()); } catch { addToast("Failed to load hidden portfolios", "error"); }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleRestore = async (id: number) => {
    try {
      await api.unhidePortfolio(id);
      setPortfolios((prev) => prev.filter((p) => p.id !== id));
    } catch { addToast("Failed to unhide portfolio", "error"); }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Move this portfolio to Deleted? You can restore it from the Deleted tab.")) return;
    try {
      await api.deletePortfolio(id);
      setPortfolios((prev) => prev.filter((p) => p.id !== id));
    } catch { addToast("Failed to delete portfolio", "error"); }
  };

  if (loading) return <p className="text-sm text-ag-text-muted">Loading...</p>;

  if (portfolios.length === 0) {
    return (
      <div className="text-center py-8">
        <EyeOff size={24} className="mx-auto text-ag-text-muted mb-2" />
        <p className="text-ag-text-muted text-sm">No hidden portfolios.</p>
        <p className="text-ag-text-muted text-xs mt-1">
          Hide portfolios from the Saved tab to move them here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-xs text-ag-text-muted">
        {portfolios.length} hidden portfolio{portfolios.length !== 1 ? "s" : ""}
      </p>
      {portfolios.map((p) => (
        <div key={p.id} className="flex items-center gap-3 p-3 bg-ag-card border border-ag-border rounded-lg">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium truncate">{p.name}</p>
            <p className="text-[10px] text-ag-text-muted">
              {p.asset_count} assets
              {p.created_at && ` · ${new Date(p.created_at).toLocaleDateString()}`}
            </p>
          </div>
          <div className="flex items-center gap-3 text-xs text-ag-text-muted">
            {p.metrics_snapshot?.profit_factor !== undefined && (
              <span>{p.optimization_end_date ? "IS " : ""}PF: {Number(p.metrics_snapshot.profit_factor).toFixed(2)}</span>
            )}
          </div>
          <button onClick={() => handleRestore(p.id)}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-ag-green/15 text-ag-green hover:bg-ag-green/25 transition-colors"
            title="Restore to Saved">
            <RotateCcw size={12} /> Restore
          </button>
          <button onClick={() => handleDelete(p.id)}
            className="p-1.5 text-ag-text-muted hover:text-ag-red transition-colors"
            title="Delete permanently">
            <Trash2 size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
