"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { BookOpen, ChevronUp, ChevronDown, Plus, Check, X, ListTodo, StickyNote } from "lucide-react";
import { api } from "@/lib/api";
import type { MonthlyObjectiveItem, ChecklistItem } from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DRAFT_KEY = "sidebar-journal-draft";
const OPEN_KEY = "sidebar-journal-open";
const TAB_KEY = "sidebar-journal-tab";
const AUTOSAVE_MS = 2000; // autosave draft every 2s of inactivity

type TabId = "note" | "todo";

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function currentYear() { return new Date().getFullYear(); }
function currentMonth() { return new Date().getMonth() + 1; } // 1-indexed for API

// ---------------------------------------------------------------------------
// Quick Note (with auto-save draft)
// ---------------------------------------------------------------------------

function QuickNote() {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState("");
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load draft on mount
  useEffect(() => {
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      if (raw) {
        const draft = JSON.parse(raw);
        if (draft.title) setTitle(draft.title);
        if (draft.content) setContent(draft.content);
      }
    } catch { /* ignore */ }
  }, []);

  // Auto-save draft to localStorage on changes
  const saveDraft = useCallback((t: string, c: string) => {
    if (draftTimer.current) clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(() => {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ title: t, content: c }));
    }, AUTOSAVE_MS);
  }, []);

  const handleTitleChange = (val: string) => {
    setTitle(val);
    saveDraft(val, content);
  };

  const handleContentChange = (val: string) => {
    setContent(val);
    saveDraft(title, val);
  };

  // "Done" — save to journal and clear
  const handleDone = async () => {
    if (!title.trim() && !content.trim()) return;
    setSaving(true);
    try {
      await api.journalNoteCreate({
        note_type: "journal",
        title: title.trim() || "Quick Note",
        content: content.trim(),
        note_date: todayStr(),
        color: "indigo",
      });
      setTitle("");
      setContent("");
      localStorage.removeItem(DRAFT_KEY);
      setFlash("Saved to Journal!");
      setTimeout(() => setFlash(""), 2000);
    } catch {
      setFlash("Error saving");
      setTimeout(() => setFlash(""), 3000);
    }
    setSaving(false);
  };

  return (
    <div className="space-y-1.5">
      <input
        type="text"
        value={title}
        onChange={(e) => handleTitleChange(e.target.value)}
        placeholder="Title..."
        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs placeholder:text-ag-text-muted/50"
      />
      <textarea
        value={content}
        onChange={(e) => handleContentChange(e.target.value)}
        placeholder="Write your note..."
        rows={4}
        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs resize-none placeholder:text-ag-text-muted/50"
      />
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-ag-text-muted">
          {flash || "Auto-saved \u00b7 click Done to journal"}
        </span>
        <button
          onClick={handleDone}
          disabled={saving || (!title.trim() && !content.trim())}
          className="flex items-center gap-1 px-2 py-1 text-[10px] rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 disabled:opacity-40 transition-colors"
          title="Save as journal entry and clear"
        >
          <Check size={10} /> {saving ? "Saving..." : "Done"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quick Todo
// ---------------------------------------------------------------------------

type TodoPeriod = "daily" | "weekly" | "monthly";

function QuickTodo() {
  const [objectives, setObjectives] = useState<MonthlyObjectiveItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<TodoPeriod | null>("daily");
  const [newItemText, setNewItemText] = useState<Record<TodoPeriod, string>>({ daily: "", weekly: "", monthly: "" });
  const saveTimers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});

  const year = currentYear();
  const month = currentMonth();

  const load = useCallback(() => {
    setLoading(true);
    api.monthlyObjectivesList(year, month)
      .then(setObjectives)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [year, month]);

  useEffect(() => { load(); }, [load]);

  // Ensure each period has an objective
  const getOrCreate = async (period: TodoPeriod): Promise<MonthlyObjectiveItem | null> => {
    const existing = objectives.find(o => o.period === period);
    if (existing) return existing;
    try {
      const label = period === "daily" ? "Today" : period === "weekly" ? "This Week" : "This Month";
      const obj = await api.monthlyObjectiveCreate({
        title: label,
        checklist: [],
        period,
        year,
        month,
      });
      setObjectives(prev => [...prev, obj]);
      return obj;
    } catch { return null; }
  };

  // Auto-save checklist changes with debounce
  const autoSaveChecklist = (id: number, checklist: ChecklistItem[]) => {
    if (saveTimers.current[id]) clearTimeout(saveTimers.current[id]);
    saveTimers.current[id] = setTimeout(async () => {
      try {
        await api.monthlyObjectiveUpdate(id, { checklist });
      } catch { /* silent */ }
    }, 800);
  };

  const toggleItem = (objId: number, itemIdx: number) => {
    setObjectives(prev => prev.map(o => {
      if (o.id !== objId) return o;
      const updated = [...o.checklist];
      updated[itemIdx] = {
        ...updated[itemIdx],
        checked: !updated[itemIdx].checked,
        completed_at: !updated[itemIdx].checked ? new Date().toISOString() : null,
      };
      autoSaveChecklist(o.id, updated);
      return { ...o, checklist: updated };
    }));
  };

  const removeItem = (objId: number, itemIdx: number) => {
    setObjectives(prev => prev.map(o => {
      if (o.id !== objId) return o;
      const updated = o.checklist.filter((_, i) => i !== itemIdx);
      autoSaveChecklist(o.id, updated);
      return { ...o, checklist: updated };
    }));
  };

  const addItem = async (period: TodoPeriod) => {
    const text = newItemText[period]?.trim();
    if (!text) return;
    const obj = await getOrCreate(period);
    if (!obj) return;
    const updated = [...obj.checklist, { text, checked: false }];
    setObjectives(prev => prev.map(o =>
      o.id === obj.id ? { ...o, checklist: updated } : o
    ));
    setNewItemText(prev => ({ ...prev, [period]: "" }));
    autoSaveChecklist(obj.id, updated);
  };

  const periods: { id: TodoPeriod; label: string; color: string }[] = [
    { id: "daily", label: "Today", color: "text-ag-accent" },
    { id: "weekly", label: "This Week", color: "text-lime-400" },
    { id: "monthly", label: "This Month", color: "text-yellow-400" },
  ];

  if (loading) return <p className="text-[10px] text-ag-text-muted py-2">Loading...</p>;

  return (
    <div className="space-y-1">
      {periods.map(({ id, label, color }) => {
        const obj = objectives.find(o => o.period === id);
        const items = obj?.checklist || [];
        const done = items.filter(i => i.checked).length;
        const isExpanded = expanded === id;

        return (
          <div key={id} className="border border-ag-border/50 rounded overflow-hidden">
            {/* Accordion header */}
            <button
              onClick={() => setExpanded(isExpanded ? null : id)}
              className="w-full flex items-center justify-between px-2 py-1 hover:bg-ag-dark/30 transition-colors"
            >
              <span className={`text-[10px] font-medium ${color}`}>
                {label}
                {items.length > 0 && (
                  <span className="ml-1 text-ag-text-muted">
                    {done}/{items.length}
                  </span>
                )}
              </span>
              {isExpanded ? <ChevronUp size={10} className="text-ag-text-muted" /> : <ChevronDown size={10} className="text-ag-text-muted" />}
            </button>

            {/* Accordion content */}
            {isExpanded && (
              <div className="px-2 pb-1.5 space-y-0.5">
                {/* Pending items only — completed items are visible in the Journal page */}
                {items.map((item, idx) => {
                  if (item.checked) return null;
                  return (
                    <div key={idx} className="flex items-start gap-1.5 group">
                      <button
                        onClick={() => obj && toggleItem(obj.id, idx)}
                        className="mt-0.5 w-3 h-3 rounded-sm border border-ag-border hover:border-ag-accent/50 flex-shrink-0 flex items-center justify-center transition-colors"
                      >
                      </button>
                      <span className="text-[10px] flex-1 leading-tight text-ag-text">
                        {item.text}
                      </span>
                      <button
                        onClick={() => obj && removeItem(obj.id, idx)}
                        className="opacity-0 group-hover:opacity-100 text-ag-text-muted hover:text-ag-red transition-opacity flex-shrink-0"
                      >
                        <X size={8} />
                      </button>
                    </div>
                  );
                })}

                {/* Add new item */}
                <div className="flex items-center gap-1 mt-1">
                  <input
                    type="text"
                    value={newItemText[id] || ""}
                    onChange={(e) => setNewItemText(prev => ({ ...prev, [id]: e.target.value }))}
                    onKeyDown={(e) => { if (e.key === "Enter") addItem(id); }}
                    placeholder="Add task..."
                    className="flex-1 bg-transparent border-b border-ag-border/30 text-[10px] py-0.5 placeholder:text-ag-text-muted/40 focus:border-ag-accent/50 outline-none"
                  />
                  <button
                    onClick={() => addItem(id)}
                    disabled={!newItemText[id]?.trim()}
                    className="text-ag-text-muted hover:text-ag-accent disabled:opacity-30 transition-colors"
                  >
                    <Plus size={10} />
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main SidebarJournal
// ---------------------------------------------------------------------------

export default function SidebarJournal({ collapsed }: { collapsed?: boolean }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TabId>("note");

  // Persist open/closed state and active tab
  useEffect(() => {
    if (localStorage.getItem(OPEN_KEY) === "true") setOpen(true);
    const savedTab = localStorage.getItem(TAB_KEY) as TabId;
    if (savedTab === "note" || savedTab === "todo") setTab(savedTab);
  }, []);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    localStorage.setItem(OPEN_KEY, String(next));
  };

  const switchTab = (t: TabId) => {
    setTab(t);
    localStorage.setItem(TAB_KEY, t);
  };

  // Collapsed sidebar — just show the icon
  if (collapsed) {
    return (
      <button
        onClick={toggle}
        className="mx-auto mb-1 p-2 rounded-lg text-ag-text-muted hover:bg-ag-card-hover hover:text-ag-text transition-colors"
        title="Quick Note & Todo"
      >
        <BookOpen size={18} />
      </button>
    );
  }

  if (!open) {
    return (
      <button
        onClick={toggle}
        className="mx-2 mb-1 flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-ag-text-muted hover:bg-ag-card-hover hover:text-ag-text transition-colors"
        title="Open quick note & todo — auto-saves, persists across pages"
      >
        <BookOpen size={14} />
        <span>Quick Note & Todo</span>
      </button>
    );
  }

  return (
    <div className="mx-2 mb-1 border border-ag-border rounded-lg bg-ag-card overflow-hidden">
      {/* Header with tabs */}
      <div className="flex items-center justify-between px-1.5 py-1 border-b border-ag-border bg-ag-darker">
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => switchTab("note")}
            className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] transition-colors ${
              tab === "note"
                ? "bg-ag-accent/15 text-ag-accent font-medium"
                : "text-ag-text-muted hover:text-ag-text"
            }`}
          >
            <StickyNote size={10} /> Note
          </button>
          <button
            onClick={() => switchTab("todo")}
            className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] transition-colors ${
              tab === "todo"
                ? "bg-ag-accent/15 text-ag-accent font-medium"
                : "text-ag-text-muted hover:text-ag-text"
            }`}
          >
            <ListTodo size={10} /> Todo
          </button>
        </div>
        <button onClick={toggle} className="text-ag-text-muted hover:text-ag-text p-0.5" title="Collapse">
          <ChevronUp size={12} />
        </button>
      </div>

      {/* Content */}
      <div className="p-2">
        {tab === "note" && <QuickNote />}
        {tab === "todo" && <QuickTodo />}
      </div>
    </div>
  );
}
