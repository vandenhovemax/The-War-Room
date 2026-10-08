"use client";
import { useState, useEffect, useCallback } from "react";
import { Trash2, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { SessionTimeMask } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  /** Called after a mask is deleted so parent can refresh dependent state */
  onMaskDeleted?: () => void;
  /** External trigger to refresh the list (increment to refetch) */
  refreshKey?: number;
  /** If true, start expanded */
  defaultOpen?: boolean;
  /** Compact mode for tighter spaces (optimizer panel) */
  compact?: boolean;
}

export default function SavedMasksList({ onMaskDeleted, refreshKey = 0, defaultOpen = false, compact = false }: Props) {
  const [masks, setMasks] = useState<SessionTimeMask[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(defaultOpen);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);

  const fetchMasks = useCallback(() => {
    setLoading(true);
    api.sessionMasks()
      .then(setMasks)
      .catch(() => setMasks([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchMasks(); }, [fetchMasks, refreshKey]);

  const handleDelete = async (id: number) => {
    setDeleting(id);
    try {
      await api.sessionMaskDelete(id);
      setMasks((prev) => prev.filter((m) => m.id !== id));
      onMaskDeleted?.();
    } catch {
      // silently fail
    } finally {
      setDeleting(null);
      setConfirmId(null);
    }
  };

  const cellCount = (mask: SessionTimeMask) => {
    if (!mask.cells) return 0;
    return mask.cells.reduce((sum, c) => sum + (c.days?.length || 0), 0);
  };

  return (
    <div className={`border border-ag-border/50 rounded-lg ${compact ? "text-[10px]" : "text-xs"}`}>
      {/* Header */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-ag-text-muted hover:text-ag-text transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="font-medium">Saved Masks</span>
        <span className="text-ag-text-muted/60">({masks.length})</span>
        <HintTip text="Named hour×day masks saved from Session Intelligence. These can be applied in the optimizer to pre-filter trades by time." />
        <span className="flex-1" />
        {open && (
          <RefreshCw
            size={11}
            className={`text-ag-text-muted/50 hover:text-ag-text cursor-pointer ${loading ? "animate-spin" : ""}`}
            onClick={(e) => { e.stopPropagation(); fetchMasks(); }}
          />
        )}
      </button>

      {/* List */}
      {open && (
        <div className="border-t border-ag-border/30 max-h-48 overflow-y-auto">
          {masks.length === 0 && !loading && (
            <p className="px-3 py-3 text-ag-text-muted/60 text-center">No saved masks yet.</p>
          )}
          {loading && masks.length === 0 && (
            <p className="px-3 py-3 text-ag-text-muted/60 text-center">Loading...</p>
          )}
          {masks.map((m) => (
            <div
              key={m.id}
              className="flex items-center gap-2 px-3 py-1.5 hover:bg-ag-dark/30 group border-b border-ag-border/20 last:border-b-0"
            >
              <span className="font-medium text-ag-text truncate flex-1" title={m.name}>
                {m.name}
              </span>
              <span className="text-ag-text-muted/60 whitespace-nowrap">
                {cellCount(m)}/168
              </span>
              <span className="text-ag-text-muted/40 whitespace-nowrap">
                {m.mode || "include"}
              </span>
              {confirmId === m.id ? (
                <span className="flex items-center gap-1">
                  <button
                    onClick={() => handleDelete(m.id)}
                    disabled={deleting === m.id}
                    className="text-red-400 hover:text-red-300 font-medium"
                  >
                    {deleting === m.id ? "..." : "Yes"}
                  </button>
                  <button
                    onClick={() => setConfirmId(null)}
                    className="text-ag-text-muted hover:text-ag-text"
                  >
                    No
                  </button>
                </span>
              ) : (
                <button
                  onClick={() => setConfirmId(m.id)}
                  className="opacity-0 group-hover:opacity-100 text-ag-text-muted/40 hover:text-red-400 transition-all"
                  title="Delete mask"
                >
                  <Trash2 size={12} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
