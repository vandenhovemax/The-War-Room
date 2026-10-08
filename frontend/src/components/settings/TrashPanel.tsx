"use client";
import { useState, useCallback } from "react";
import { Trash2, RotateCcw, AlertTriangle, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type {
  DeletedStrategy,
  DeletedBacktest,
  DeletedBatch,
  DeletedWFO,
} from "@/lib/api";
import { useApi } from "@/hooks/useApi";

type TrashTab = "strategies" | "backtests" | "batches" | "wfo";

function timeAgo(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

const TAB_LABELS: Record<TrashTab, string> = {
  strategies: "Strategies",
  backtests: "Backtests",
  batches: "Batches",
  wfo: "WFO Runs",
};

export default function TrashPanel() {
  const [tab, setTab] = useState<TrashTab>("strategies");
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  const [confirmTabEmpty, setConfirmTabEmpty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const strategies = useApi(
    useCallback(() => api.trashStrategies(), [])
  );
  const backtests = useApi(
    useCallback(() => api.trashBacktests(), [])
  );
  const batches = useApi(
    useCallback(() => api.trashBatches(), [])
  );
  const wfo = useApi(
    useCallback(() => api.trashWfo(), [])
  );

  const refetchAll = () => {
    strategies.refetch();
    backtests.refetch();
    batches.refetch();
    wfo.refetch();
  };

  const totalCount =
    (strategies.data?.length ?? 0) +
    (backtests.data?.length ?? 0) +
    (batches.data?.length ?? 0) +
    (wfo.data?.length ?? 0);

  const tabCounts: Record<TrashTab, number> = {
    strategies: strategies.data?.length ?? 0,
    backtests: backtests.data?.length ?? 0,
    batches: batches.data?.length ?? 0,
    wfo: wfo.data?.length ?? 0,
  };

  async function handleRestore(type: TrashTab, id: number) {
    const key = `restore-${type}-${id}`;
    setBusy(key);
    setError(null);
    try {
      await api.trashRestore(type, id);
      refetchAll();
    } catch (e) {
      setError(`Restore failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    } finally {
      setBusy(null);
    }
  }

  async function handlePermanentDelete(type: TrashTab, id: number) {
    const key = `delete-${type}-${id}`;
    setBusy(key);
    setError(null);
    try {
      await api.trashPermanentDelete(type, id);
      refetchAll();
    } catch (e) {
      setError(`Delete failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    } finally {
      setBusy(null);
    }
  }

  async function handleEmptyAll() {
    setBusy("empty-all");
    setError(null);
    try {
      await api.trashEmpty();
      setConfirmEmpty(false);
      refetchAll();
    } catch (e) {
      setError(`Empty all failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    } finally {
      setBusy(null);
    }
  }

  async function handleEmptyTab() {
    setBusy("empty-tab");
    setError(null);
    try {
      await api.trashEmptyTab(tab);
      setConfirmTabEmpty(false);
      refetchAll();
    } catch (e) {
      setError(`Empty ${TAB_LABELS[tab]} failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    } finally {
      setBusy(null);
    }
  }

  const tabs: { id: TrashTab; label: string }[] = [
    { id: "strategies", label: "Strategies" },
    { id: "backtests", label: "Backtests" },
    { id: "batches", label: "Batches" },
    { id: "wfo", label: "WFO Runs" },
  ];

  return (
    <div className="space-y-4">
      {/* Trash header with empty all */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Trash2 size={16} className="text-ag-text-muted" />
          <span className="text-sm text-ag-text-muted">
            {totalCount} deleted item{totalCount !== 1 ? "s" : ""}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refetchAll}
            className="flex items-center gap-1.5 text-xs text-ag-text-muted hover:text-ag-text px-3 py-1.5 rounded border border-ag-border hover:bg-ag-card-hover transition-colors"
          >
            <RefreshCw size={12} />
            Refresh
          </button>
          {totalCount > 0 && !confirmEmpty && (
            <button
              onClick={() => setConfirmEmpty(true)}
              className="flex items-center gap-1.5 text-xs text-red-400 hover:text-red-300 px-3 py-1.5 rounded border border-red-500/30 hover:bg-red-500/10 transition-colors"
            >
              <Trash2 size={12} />
              Empty All Trash
            </button>
          )}
          {confirmEmpty && (
            <div className="flex items-center gap-2">
              <AlertTriangle size={14} className="text-red-400" />
              <span className="text-xs text-red-400">
                Permanently delete all {totalCount} items?
              </span>
              <button
                onClick={handleEmptyAll}
                disabled={busy === "empty-all"}
                className="text-xs px-3 py-1.5 rounded bg-red-500 text-white hover:bg-red-600 disabled:opacity-50 transition-colors"
              >
                {busy === "empty-all" ? "Deleting..." : "Yes, delete all"}
              </button>
              <button
                onClick={() => setConfirmEmpty(false)}
                className="text-xs px-3 py-1.5 rounded border border-ag-border text-ag-text-muted hover:text-ag-text transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Sub-tabs */}
      <div className="flex gap-1 bg-ag-dark border border-ag-border rounded-lg p-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => { setTab(t.id); setConfirmTabEmpty(false); }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
              tab === t.id
                ? "bg-ag-card text-ag-text"
                : "text-ag-text-muted hover:text-ag-text"
            }`}
          >
            {t.label}
            {tabCounts[t.id] > 0 && (
              <span className="bg-ag-border text-ag-text-muted px-1.5 py-0.5 rounded-full text-[10px]">
                {tabCounts[t.id]}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Per-tab empty button */}
      {tabCounts[tab] > 0 && (
        <div className="flex items-center justify-end">
          {!confirmTabEmpty ? (
            <button
              onClick={() => setConfirmTabEmpty(true)}
              className="flex items-center gap-1.5 text-xs text-red-400 hover:text-red-300 px-3 py-1.5 rounded border border-red-500/30 hover:bg-red-500/10 transition-colors"
            >
              <Trash2 size={12} />
              Delete all {TAB_LABELS[tab]} ({tabCounts[tab]})
            </button>
          ) : (
            <div className="flex items-center gap-2">
              <AlertTriangle size={14} className="text-red-400" />
              <span className="text-xs text-red-400">
                Permanently delete all {tabCounts[tab]} {TAB_LABELS[tab].toLowerCase()}?
              </span>
              <button
                onClick={handleEmptyTab}
                disabled={busy === "empty-tab"}
                className="text-xs px-3 py-1.5 rounded bg-red-500 text-white hover:bg-red-600 disabled:opacity-50 transition-colors"
              >
                {busy === "empty-tab" ? "Deleting..." : "Yes, delete all"}
              </button>
              <button
                onClick={() => setConfirmTabEmpty(false)}
                className="text-xs px-3 py-1.5 rounded border border-ag-border text-ag-text-muted hover:text-ag-text transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {/* Error message */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 flex items-center gap-2">
          <AlertTriangle size={14} className="text-red-400 shrink-0" />
          <p className="text-xs text-red-400">{error}</p>
          <button
            onClick={() => setError(null)}
            className="ml-auto text-xs text-red-400/60 hover:text-red-400"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Tab content */}
      <div className="bg-ag-card border border-ag-border rounded-xl overflow-hidden">
        {tab === "strategies" && (
          <TrashTable
            items={strategies.data ?? []}
            loading={strategies.loading}
            type="strategies"
            busy={busy}
            onRestore={handleRestore}
            onDelete={handlePermanentDelete}
            columns={["Name", "Type", "Created", "Deleted"]}
            renderRow={(s: DeletedStrategy) => (
              <>
                <td className="py-2 px-3 text-sm font-medium">{s.name}</td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {s.source_type}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {timeAgo(s.created_at)}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {timeAgo(s.deleted_at)}
                </td>
              </>
            )}
          />
        )}

        {tab === "backtests" && (
          <TrashTable
            items={backtests.data ?? []}
            loading={backtests.loading}
            type="backtests"
            busy={busy}
            onRestore={handleRestore}
            onDelete={handlePermanentDelete}
            columns={["Symbol", "TF", "Status", "Created", "Deleted"]}
            renderRow={(r: DeletedBacktest) => (
              <>
                <td className="py-2 px-3 text-sm font-medium">
                  {r.symbol?.replace("USDT", "") ?? `#${r.id}`}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {r.timeframe}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {r.status}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {timeAgo(r.created_at)}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {timeAgo(r.deleted_at)}
                </td>
              </>
            )}
          />
        )}

        {tab === "batches" && (
          <TrashTable
            items={batches.data ?? []}
            loading={batches.loading}
            type="batches"
            busy={busy}
            onRestore={handleRestore}
            onDelete={handlePermanentDelete}
            columns={["Name", "TF", "Symbols", "Status", "Deleted"]}
            renderRow={(b: DeletedBatch) => (
              <>
                <td className="py-2 px-3 text-sm font-medium">{b.name}</td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {b.timeframe}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {b.total_symbols}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {b.status}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {timeAgo(b.deleted_at)}
                </td>
              </>
            )}
          />
        )}

        {tab === "wfo" && (
          <TrashTable
            items={wfo.data ?? []}
            loading={wfo.loading}
            type="wfo"
            busy={busy}
            onRestore={handleRestore}
            onDelete={handlePermanentDelete}
            columns={["ID", "IS Months", "OOS Months", "Status", "Deleted"]}
            renderRow={(w: DeletedWFO) => (
              <>
                <td className="py-2 px-3 text-sm font-medium">#{w.id}</td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {w.is_months}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {w.oos_months}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {w.status}
                </td>
                <td className="py-2 px-3 text-xs text-ag-text-muted">
                  {timeAgo(w.deleted_at)}
                </td>
              </>
            )}
          />
        )}
      </div>
    </div>
  );
}

/* ── Generic trash table ─────────────────────────────────── */

interface TrashTableProps<T extends { id: number }> {
  items: T[];
  loading: boolean;
  type: TrashTab;
  busy: string | null;
  onRestore: (type: TrashTab, id: number) => void;
  onDelete: (type: TrashTab, id: number) => void;
  columns: string[];
  renderRow: (item: T) => React.ReactNode;
}

function TrashTable<T extends { id: number }>({
  items,
  loading,
  type,
  busy,
  onRestore,
  onDelete,
  columns,
  renderRow,
}: TrashTableProps<T>) {
  const [confirmId, setConfirmId] = useState<number | null>(null);

  if (loading) {
    return (
      <div className="p-8 text-center">
        <p className="text-sm text-ag-text-muted">Loading...</p>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="p-8 text-center">
        <Trash2 size={24} className="mx-auto mb-2 text-ag-text-muted/50" />
        <p className="text-sm text-ag-text-muted">No deleted items</p>
      </div>
    );
  }

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-ag-border text-ag-text-muted">
          {columns.map((col) => (
            <th key={col} className="text-left py-2 px-3 font-medium">
              {col}
            </th>
          ))}
          <th className="text-right py-2 px-3 font-medium">Actions</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.id} className="border-b border-ag-border/50 hover:bg-ag-card-hover/50">
            {renderRow(item)}
            <td className="py-2 px-3 text-right">
              <div className="flex items-center justify-end gap-1.5">
                <button
                  onClick={() => onRestore(type, item.id)}
                  disabled={busy === `restore-${type}-${item.id}`}
                  className="flex items-center gap-1 text-ag-accent hover:text-ag-accent/80 px-2 py-1 rounded border border-ag-accent/30 hover:bg-ag-accent/10 disabled:opacity-50 transition-colors"
                  title="Restore"
                >
                  <RotateCcw size={11} />
                  Restore
                </button>
                {confirmId !== item.id ? (
                  <button
                    onClick={() => setConfirmId(item.id)}
                    className="flex items-center gap-1 text-red-400 hover:text-red-300 px-2 py-1 rounded border border-red-500/30 hover:bg-red-500/10 transition-colors"
                    title="Permanently delete"
                  >
                    <Trash2 size={11} />
                    Delete
                  </button>
                ) : (
                  <div className="flex items-center gap-1">
                    <AlertTriangle size={11} className="text-red-400" />
                    <button
                      onClick={() => {
                        onDelete(type, item.id);
                        setConfirmId(null);
                      }}
                      disabled={busy === `delete-${type}-${item.id}`}
                      className="text-red-400 hover:text-red-300 px-2 py-1 rounded bg-red-500/10 border border-red-500/30 disabled:opacity-50 transition-colors"
                    >
                      Confirm
                    </button>
                    <button
                      onClick={() => setConfirmId(null)}
                      className="text-ag-text-muted px-2 py-1 rounded border border-ag-border transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
