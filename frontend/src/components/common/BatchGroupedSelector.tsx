"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { FolderOpen, Plus, Trash2, MoveRight, X, Settings } from "lucide-react";
import { api } from "@/lib/api";
import type { BatchBacktestRunItem, BatchGroupItem } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  batches: BatchBacktestRunItem[];
  selectedBatch: number | null;
  onSelect: (id: number) => void;
}

interface GroupedBatch {
  groupName: string;
  groupId: number | null;
  tfDirGroups: {
    label: string;
    batches: BatchBacktestRunItem[];
  }[];
}

function groupBatches(batches: BatchBacktestRunItem[], groups: BatchGroupItem[]): GroupedBatch[] {
  const groupMap = new Map(groups.map((g) => [g.id, g]));
  const result: GroupedBatch[] = [];

  // Buckets: group_id -> batches
  const buckets = new Map<number | null, BatchBacktestRunItem[]>();
  for (const b of batches) {
    const gid = b.group_id ?? null;
    if (!buckets.has(gid)) buckets.set(gid, []);
    buckets.get(gid)!.push(b);
  }

  // Named groups first (sorted by sort_order, then name)
  const sortedGroups = [...groups].sort((a, b) => (a.sort_order - b.sort_order) || a.name.localeCompare(b.name));
  for (const g of sortedGroups) {
    const items = buckets.get(g.id) || [];
    if (items.length === 0) continue; // skip empty groups in selector
    result.push({
      groupName: g.name,
      groupId: g.id,
      tfDirGroups: buildTfDirGroups(items),
    });
  }

  // Ungrouped at the bottom
  const ungrouped = buckets.get(null) || [];
  if (ungrouped.length > 0) {
    result.push({
      groupName: "Ungrouped",
      groupId: null,
      tfDirGroups: buildTfDirGroups(ungrouped),
    });
  }

  return result;
}

function buildTfDirGroups(batches: BatchBacktestRunItem[]) {
  const map = new Map<string, BatchBacktestRunItem[]>();
  for (const b of batches) {
    const key = `${b.timeframe || "?"}m · ${(b.direction_filter || "both").charAt(0).toUpperCase() + (b.direction_filter || "both").slice(1)}`;
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(b);
  }
  return Array.from(map.entries()).map(([label, items]) => ({ label, batches: items }));
}

export default function BatchGroupedSelector({ batches, selectedBatch, onSelect }: Props) {
  const [groups, setGroups] = useState<BatchGroupItem[]>([]);
  const [showManager, setShowManager] = useState(false);
  const managerRef = useRef<HTMLDivElement>(null);

  const fetchGroups = useCallback(() => {
    api.batchGroups().then(setGroups).catch(() => setGroups([]));
  }, []);

  useEffect(() => { fetchGroups(); }, [fetchGroups]);

  // Close manager on outside click
  useEffect(() => {
    if (!showManager) return;
    const handler = (e: MouseEvent) => {
      if (managerRef.current && !managerRef.current.contains(e.target as Node)) {
        setShowManager(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showManager]);

  const grouped = groupBatches(batches, groups);

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <label className="text-xs text-ag-text-muted font-medium flex items-center gap-1">
        Batch
        <HintTip text="Select a completed batch backtest. Batches are grouped by your custom categories, then by timeframe and direction." />
      </label>
      <select
        value={selectedBatch ?? ""}
        onChange={(e) => onSelect(Number(e.target.value))}
        className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm flex-1 min-w-[200px]"
      >
        {grouped.map((g) => {
          // If only one TF×Dir group, flatten
          if (g.tfDirGroups.length === 1) {
            const tfg = g.tfDirGroups[0];
            return (
              <optgroup key={g.groupName} label={`${g.groupName} · ${tfg.label}`}>
                {tfg.batches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name || `Batch #${b.id}`} · {b.completed_symbols} sym · ID:{b.id}
                  </option>
                ))}
              </optgroup>
            );
          }
          // Multiple TF×Dir groups — nested optgroups (HTML doesn't support nesting,
          // so we prefix the label with the group name)
          return g.tfDirGroups.map((tfg) => (
            <optgroup key={`${g.groupName}-${tfg.label}`} label={`${g.groupName} › ${tfg.label}`}>
              {tfg.batches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name || `Batch #${b.id}`} · {b.completed_symbols} sym · ID:{b.id}
                </option>
              ))}
            </optgroup>
          ));
        })}
      </select>

      {/* Group management button */}
      <div className="relative" ref={managerRef}>
        <button
          onClick={() => setShowManager(!showManager)}
          className="p-1.5 rounded-lg border border-ag-border text-ag-text-muted hover:text-ag-text hover:border-ag-border/80 transition-colors"
          title="Manage batch groups"
        >
          <Settings size={14} />
        </button>

        {showManager && (
          <GroupManagerPopover
            groups={groups}
            batches={batches}
            onRefresh={fetchGroups}
            onClose={() => setShowManager(false)}
          />
        )}
      </div>
    </div>
  );
}


// --- Group Manager Popover ---

function GroupManagerPopover({
  groups, batches, onRefresh, onClose,
}: {
  groups: BatchGroupItem[];
  batches: BatchBacktestRunItem[];
  onRefresh: () => void;
  onClose: () => void;
}) {
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [assignMode, setAssignMode] = useState<number | null>(null); // batch id being assigned
  const [delConfirm, setDelConfirm] = useState<number | null>(null);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    setError("");
    try {
      await api.batchGroupCreate({ name: newName.trim() });
      setNewName("");
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create group");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.batchGroupDelete(id);
      onRefresh();
      setDelConfirm(null);
    } catch { /* ignore */ }
  };

  const handleAssign = async (batchId: number, groupId: number | null) => {
    try {
      await api.batchGroupAssign({ batch_ids: [batchId], group_id: groupId });
      onRefresh();
      setAssignMode(null);
    } catch { /* ignore */ }
  };

  return (
    <div className="absolute right-0 top-full mt-1 z-50 w-80 bg-ag-card border border-ag-border rounded-xl shadow-2xl p-4 space-y-3 text-xs">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ag-text">Batch Groups</h3>
        <button onClick={onClose} className="text-ag-text-muted hover:text-ag-text"><X size={14} /></button>
      </div>

      {/* Create new group */}
      <div className="flex gap-2">
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          placeholder="New group name..."
          className="flex-1 bg-ag-dark border border-ag-border rounded-lg px-2.5 py-1.5 text-xs"
        />
        <button
          onClick={handleCreate}
          disabled={creating || !newName.trim()}
          className="px-2.5 py-1.5 rounded-lg bg-ag-accent text-white text-xs font-medium hover:bg-ag-accent/80 disabled:opacity-50"
        >
          <Plus size={12} />
        </button>
      </div>
      {error && <p className="text-red-400 text-[10px]">{error}</p>}

      {/* Existing groups */}
      <div className="space-y-1 max-h-36 overflow-y-auto">
        {groups.length === 0 && (
          <p className="text-ag-text-muted/60 text-center py-2">No groups yet. Create one above.</p>
        )}
        {groups.map((g) => (
          <div key={g.id} className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-ag-dark/40 group">
            <FolderOpen size={12} className="text-ag-accent/60 shrink-0" />
            <span className="text-ag-text truncate flex-1">{g.name}</span>
            <span className="text-ag-text-muted/50">{g.batch_count}</span>
            {delConfirm === g.id ? (
              <span className="flex gap-1">
                <button onClick={() => handleDelete(g.id)} className="text-red-400 hover:text-red-300 font-medium">Yes</button>
                <button onClick={() => setDelConfirm(null)} className="text-ag-text-muted hover:text-ag-text">No</button>
              </span>
            ) : (
              <button
                onClick={() => setDelConfirm(g.id)}
                className="opacity-0 group-hover:opacity-100 text-ag-text-muted/40 hover:text-red-400"
                title="Delete group"
              >
                <Trash2 size={11} />
              </button>
            )}
          </div>
        ))}
      </div>

      {/* Assign batches section */}
      <div className="border-t border-ag-border/30 pt-3 space-y-1.5">
        <span className="text-ag-text-muted font-medium">Assign batch to group:</span>
        <div className="max-h-40 overflow-y-auto space-y-0.5">
          {batches.map((b) => {
            const currentGroup = groups.find((g) => g.id === b.group_id);
            return (
              <div key={b.id} className="flex items-center gap-2 px-2 py-1 rounded hover:bg-ag-dark/40">
                <span className="text-ag-text truncate flex-1 text-[10px]">
                  {b.name || `Batch #${b.id}`}
                </span>
                {assignMode === b.id ? (
                  <select
                    autoFocus
                    value={b.group_id ?? ""}
                    onChange={(e) => handleAssign(b.id, e.target.value ? Number(e.target.value) : null)}
                    onBlur={() => setAssignMode(null)}
                    className="bg-ag-dark border border-ag-border rounded px-1.5 py-0.5 text-[10px]"
                  >
                    <option value="">None</option>
                    {groups.map((g) => (
                      <option key={g.id} value={g.id}>{g.name}</option>
                    ))}
                  </select>
                ) : (
                  <button
                    onClick={() => setAssignMode(b.id)}
                    className="text-[10px] text-ag-text-muted/60 hover:text-ag-accent flex items-center gap-1"
                  >
                    {currentGroup ? currentGroup.name : "—"}
                    <MoveRight size={10} />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
