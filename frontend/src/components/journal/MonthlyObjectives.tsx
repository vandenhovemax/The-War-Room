"use client";
import { useState, useEffect, useCallback } from "react";
import { Plus, Save, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { MonthlyObjectiveItem, ChecklistItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";

const PERIODS = [
  { id: "daily", label: "Daily" },
  { id: "weekly", label: "Weekly" },
  { id: "monthly", label: "Monthly" },
] as const;

interface MonthlyObjectivesProps {
  year: number;
  month: number; // 0-indexed (JS Date convention)
}

export default function MonthlyObjectives({ year, month }: MonthlyObjectivesProps) {
  const { addToast } = useTaskManager();
  const [objectives, setObjectives] = useState<MonthlyObjectiveItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Convert from 0-indexed (JS) to 1-indexed (API/DB)
  const apiMonth = month + 1;

  const load = useCallback(() => {
    setLoading(true);
    api.monthlyObjectivesList(year, apiMonth)
      .then((data) => { setObjectives(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, [year, apiMonth]);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async (period: string) => {
    try {
      const obj = await api.monthlyObjectiveCreate({
        title: `${period.charAt(0).toUpperCase() + period.slice(1)} Goals`,
        checklist: [{ text: "", checked: false }],
        period,
        year,
        month: apiMonth,
      });
      setObjectives([...objectives, obj]);
    } catch { addToast("Failed to save objective", "error"); }
  };

  const handleUpdate = async (id: number, update: Partial<MonthlyObjectiveItem>) => {
    try {
      const updated = await api.monthlyObjectiveUpdate(id, update);
      setObjectives(objectives.map((o) => (o.id === id ? updated : o)));
    } catch { addToast("Failed to update objective", "error"); }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.monthlyObjectiveDelete(id);
      setObjectives(objectives.filter((o) => o.id !== id));
    } catch { addToast("Failed to delete objective", "error"); }
  };

  if (loading) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-ag-text-muted uppercase tracking-wider">
        {new Date(year, month).toLocaleDateString("en-US", { month: "long", year: "numeric" })} Objectives
      </h3>
      {/* 3-column layout: Daily | Weekly | Monthly */}
      <div className="grid grid-cols-3 gap-3">
        {PERIODS.map((p) => {
          const periodObjs = objectives.filter((o) => o.period === p.id);
          return (
            <PeriodColumn key={p.id} period={p.id} label={p.label}
              objectives={periodObjs}
              onAdd={() => handleAdd(p.id)}
              onUpdate={handleUpdate} onDelete={handleDelete} />
          );
        })}
      </div>
    </div>
  );
}

function PeriodColumn({ period, label, objectives, onAdd, onUpdate, onDelete }: {
  period: string;
  label: string;
  objectives: MonthlyObjectiveItem[];
  onAdd: () => void;
  onUpdate: (id: number, update: Partial<MonthlyObjectiveItem>) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-xl flex flex-col min-h-[200px]">
      {/* Column header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-ag-border">
        <span className="text-xs font-semibold text-ag-text-muted">{label}</span>
        <button onClick={onAdd}
          className="text-ag-text-muted hover:text-ag-accent transition-colors">
          <Plus size={14} />
        </button>
      </div>

      {/* Objective cards */}
      <div className="flex-1 p-2 space-y-2 overflow-y-auto max-h-[500px]">
        {objectives.length === 0 ? (
          <button onClick={onAdd}
            className="w-full py-6 text-center text-xs text-ag-text-muted hover:text-ag-accent border border-dashed border-ag-border rounded-lg hover:border-ag-accent/30 transition-colors">
            + Add {label.toLowerCase()} objective
          </button>
        ) : (
          objectives.map((obj) => (
            <ObjectiveCard key={obj.id} objective={obj}
              onUpdate={onUpdate} onDelete={onDelete} />
          ))
        )}
      </div>
    </div>
  );
}

function ObjectiveCard({ objective, onUpdate, onDelete }: {
  objective: MonthlyObjectiveItem;
  onUpdate: (id: number, update: Partial<MonthlyObjectiveItem>) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}) {
  const [title, setTitle] = useState(objective.title);
  const [items, setItems] = useState<ChecklistItem[]>(objective.checklist);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Sort: unchecked first, then checked at the bottom
  const sortedItems = [...items].sort((a, b) => {
    if (a.checked === b.checked) return 0;
    return a.checked ? 1 : -1;
  });

  const completedCount = items.filter((c) => c.checked).length;
  const totalCount = items.filter((c) => c.text.trim()).length;

  const toggleCheck = async (targetIdx: number) => {
    // targetIdx is in sortedItems — find the original item
    const targetItem = sortedItems[targetIdx];
    const origIdx = items.indexOf(targetItem);
    if (origIdx === -1) return;

    const updated = [...items];
    const nowChecked = !updated[origIdx].checked;
    updated[origIdx] = {
      ...updated[origIdx],
      checked: nowChecked,
      completed_at: nowChecked ? new Date().toISOString().slice(0, 10) : null,
    };
    setItems(updated);
    await onUpdate(objective.id, { checklist: updated });
  };

  const updateItemText = (targetIdx: number, text: string) => {
    const targetItem = sortedItems[targetIdx];
    const origIdx = items.indexOf(targetItem);
    if (origIdx === -1) return;
    const updated = [...items];
    updated[origIdx] = { ...updated[origIdx], text };
    setItems(updated);
    setDirty(true);
  };

  const addItem = () => {
    setItems([...items, { text: "", checked: false }]);
    setDirty(true);
  };

  const removeItem = (targetIdx: number) => {
    if (items.length <= 1) return;
    const targetItem = sortedItems[targetIdx];
    const origIdx = items.indexOf(targetItem);
    if (origIdx === -1) return;
    setItems(items.filter((_, i) => i !== origIdx));
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    await onUpdate(objective.id, {
      title,
      checklist: items.filter((c) => c.text.trim()),
    });
    setDirty(false);
    setSaving(false);
  };

  return (
    <div className="bg-ag-dark rounded-lg p-2 space-y-1.5 group/card">
      {/* Title + progress */}
      <div className="flex items-center gap-1.5">
        <input type="text" value={title}
          onChange={(e) => { setTitle(e.target.value); setDirty(true); }}
          className="flex-1 bg-transparent text-xs font-semibold outline-none min-w-0 placeholder:text-ag-text-muted"
          placeholder="Title" />
        {totalCount > 0 && (
          <span className="text-[9px] text-ag-text-muted shrink-0 whitespace-nowrap">
            {completedCount}/{totalCount}
          </span>
        )}
      </div>

      {/* Progress bar */}
      {totalCount > 0 && (
        <div className="h-1 bg-ag-card rounded-full overflow-hidden">
          <div className="h-full bg-orange-500 rounded-full transition-all"
            style={{ width: `${(completedCount / totalCount) * 100}%` }} />
        </div>
      )}

      {/* Checklist — sorted: unchecked first, checked at bottom */}
      <div className="space-y-0.5">
        {sortedItems.map((item, i) => {
          const origIdx = items.indexOf(item);
          return (
          <div key={`item-${origIdx}`} className="flex items-start gap-1.5 group">
            <button onClick={() => toggleCheck(i)}
              className={`w-3.5 h-3.5 rounded border shrink-0 flex items-center justify-center transition-colors mt-0.5 ${
                item.checked
                  ? "bg-orange-500 border-orange-500 text-white"
                  : "border-ag-border hover:border-orange-400"
              }`}>
              {item.checked && <span className="text-[8px]">&#10003;</span>}
            </button>
            <div className="flex-1 min-w-0">
              <input type="text" value={item.text}
                onChange={(e) => updateItemText(i, e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addItem(); } }}
                placeholder="Task..."
                className={`w-full bg-transparent border-none outline-none text-xs leading-tight ${
                  item.checked ? "line-through text-ag-text-muted" : ""
                }`} />
              {item.checked && item.completed_at && (
                <span className="text-[9px] text-ag-text-muted">{item.completed_at}</span>
              )}
            </div>
            <button onClick={() => removeItem(i)}
              className="text-ag-text-muted hover:text-ag-red opacity-0 group-hover:opacity-100 transition-opacity shrink-0 mt-0.5">
              <Trash2 size={10} />
            </button>
          </div>
          );
        })}
      </div>

      <button onClick={addItem}
        className="text-[9px] text-orange-400 hover:text-orange-300 flex items-center gap-0.5 ml-5">
        <Plus size={8} /> Add
      </button>

      {/* Save / Delete row */}
      <div className="flex items-center justify-between pt-0.5">
        {dirty ? (
          <button onClick={handleSave} disabled={saving}
            className="flex items-center gap-0.5 px-2 py-0.5 bg-ag-accent text-white rounded text-[9px] font-medium hover:brightness-110 disabled:opacity-50">
            <Save size={8} /> {saving ? "..." : "Save"}
          </button>
        ) : <span />}
        {!confirmDelete ? (
          <button onClick={() => setConfirmDelete(true)}
            className="text-[9px] text-ag-text-muted hover:text-ag-red opacity-0 group-hover/card:opacity-100 transition-opacity">
            Delete
          </button>
        ) : (
          <div className="flex items-center gap-1">
            <button onClick={() => onDelete(objective.id)}
              className="text-[9px] px-1.5 py-0.5 bg-ag-red text-white rounded">Yes</button>
            <button onClick={() => setConfirmDelete(false)}
              className="text-[9px] text-ag-text-muted">No</button>
          </div>
        )}
      </div>
    </div>
  );
}
