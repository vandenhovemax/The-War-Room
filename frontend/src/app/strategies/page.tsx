"use client";
import { useState, useEffect, useMemo, useRef } from "react";
import { Code2, Trash2, Eye, Settings, Sparkles, FileCode2, ChevronDown, ChevronRight, FolderOpen, Tag } from "lucide-react";
import { api, StrategyItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import StrategyCreatePanel from "@/components/strategy/StrategyCreatePanel";
import StrategyDetailView from "@/components/strategy/StrategyDetailView";

type CreateMode = "ai" | "paste" | null;

export default function StrategiesPage() {
  const { addToast } = useTaskManager();
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createMode, setCreateMode] = useState<CreateMode>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [editingCatId, setEditingCatId] = useState<number | null>(null);
  const [catInput, setCatInput] = useState("");
  const catRef = useRef<HTMLInputElement>(null);

  const loadStrategies = async () => {
    try {
      setStrategies(await api.strategies());
    } catch {
      addToast("Failed to load strategies", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadStrategies(); }, []);

  // Focus category input when editing
  useEffect(() => {
    if (editingCatId !== null && catRef.current) catRef.current.focus();
  }, [editingCatId]);

  const handleDelete = async (id: number) => {
    try {
      await api.deleteStrategy(id);
      loadStrategies();
      if (selectedId === id) setSelectedId(null);
    } catch {
      addToast("Failed to delete strategy", "error");
    }
  };

  const toggleCreate = (mode: CreateMode) => {
    setCreateMode((prev) => (prev === mode ? null : mode));
  };

  // Group strategies by category
  const grouped = useMemo(() => {
    const groups: Record<string, StrategyItem[]> = {};
    for (const s of strategies) {
      const cat = s.category || "Uncategorized";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(s);
    }
    // Sort: named categories first (alphabetical), Uncategorized last
    const sorted = Object.keys(groups).sort((a, b) => {
      if (a === "Uncategorized") return 1;
      if (b === "Uncategorized") return -1;
      return a.localeCompare(b);
    });
    return sorted.map(cat => ({ category: cat, items: groups[cat] }));
  }, [strategies]);

  // Existing category names for dropdown suggestions
  const existingCategories = useMemo(() => {
    const cats = new Set<string>();
    for (const s of strategies) {
      if (s.category) cats.add(s.category);
    }
    return Array.from(cats).sort();
  }, [strategies]);

  const toggleCollapse = (cat: string) => {
    setCollapsed(prev => ({ ...prev, [cat]: !prev[cat] }));
  };

  const handleCategoryEdit = (s: StrategyItem) => {
    setEditingCatId(s.id);
    setCatInput(s.category || "");
  };

  const handleCategorySave = async (strategyId: number) => {
    const trimmed = catInput.trim();
    try {
      await api.updateStrategyCategory(strategyId, trimmed || null);
      setEditingCatId(null);
      loadStrategies();
    } catch {
      addToast("Failed to update category", "error");
    }
  };

  const handleCategoryKeyDown = (e: React.KeyboardEvent, strategyId: number) => {
    if (e.key === "Enter") handleCategorySave(strategyId);
    if (e.key === "Escape") setEditingCatId(null);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Strategies</h1>
          <p className="text-ag-text-muted text-sm mt-1">
            Manage your trading strategies
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => toggleCreate("ai")}
            className="flex items-center gap-2 bg-purple-600 hover:bg-purple-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          >
            <Sparkles size={16} />
            Build with AI
          </button>
          <button
            onClick={() => toggleCreate("paste")}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          >
            <FileCode2 size={16} />
            Paste Python
          </button>
        </div>
      </div>

      {/* Create panels */}
      {createMode && (
        <StrategyCreatePanel
          mode={createMode}
          onSaved={loadStrategies}
          onClose={() => setCreateMode(null)}
        />
      )}

      {/* Strategy list grouped by category */}
      <div className="space-y-4">
        {loading ? (
          <div className="text-ag-text-muted text-center py-12">Loading strategies...</div>
        ) : strategies.length === 0 ? (
          <div className="text-center py-12">
            <div className="inline-flex p-4 rounded-2xl bg-ag-accent/10 text-ag-accent mb-4">
              <Code2 size={40} />
            </div>
            <p className="text-ag-text-muted">
              No strategies yet. Use &quot;Build with AI&quot; or &quot;Paste Python&quot; to add one.
            </p>
          </div>
        ) : (
          grouped.map(({ category, items }) => (
            <div key={category} className="space-y-2">
              {/* Category header — only show if more than one group */}
              {grouped.length > 1 && (
                <button
                  onClick={() => toggleCollapse(category)}
                  className="flex items-center gap-2 text-sm font-medium text-ag-text-muted hover:text-ag-text transition-colors w-full text-left py-1"
                >
                  {collapsed[category] ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                  <FolderOpen size={14} />
                  <span>{category}</span>
                  <span className="text-xs opacity-60">({items.length})</span>
                </button>
              )}

              {/* Strategy cards */}
              {!collapsed[category] && (
                <div className="grid grid-cols-1 gap-3">
                  {items.map((s) => (
                    <div
                      key={s.id}
                      className="bg-ag-card border border-ag-border rounded-xl p-4 flex items-center justify-between hover:border-ag-accent/30 transition-colors"
                    >
                      <div className="flex items-center gap-4 min-w-0">
                        <div className="p-2 rounded-lg bg-ag-accent/10 text-ag-accent shrink-0">
                          <Code2 size={20} />
                        </div>
                        <div className="min-w-0">
                          <h3 className="font-medium">{s.name}</h3>
                          <div className="flex items-center gap-3 text-xs text-ag-text-muted mt-1 flex-wrap">
                            <span>{s.source_type}</span>
                            {s.has_pine && (
                              <span className="bg-ag-accent/10 text-ag-accent px-2 py-0.5 rounded">Pine</span>
                            )}
                            {s.has_python && (
                              <span className="bg-ag-green/10 text-ag-green px-2 py-0.5 rounded">Python</span>
                            )}
                            {(s.param_count ?? 0) > 0 && (
                              <span className="bg-purple-500/10 text-purple-400 px-2 py-0.5 rounded">
                                {s.param_count} params
                              </span>
                            )}
                            {(s.config_count ?? 0) > 0 && (
                              <span className="bg-amber-500/10 text-amber-400 px-2 py-0.5 rounded">
                                {s.config_count} configs
                              </span>
                            )}
                            {/* Category badge */}
                            {editingCatId === s.id ? (
                              <span className="relative">
                                <input
                                  ref={catRef}
                                  value={catInput}
                                  onChange={(e) => setCatInput(e.target.value)}
                                  onKeyDown={(e) => handleCategoryKeyDown(e, s.id)}
                                  onBlur={() => handleCategorySave(s.id)}
                                  placeholder="Category..."
                                  list="cat-suggestions"
                                  className="bg-ag-bg border border-ag-accent/50 rounded px-2 py-0.5 text-xs w-32 outline-none"
                                />
                                <datalist id="cat-suggestions">
                                  {existingCategories.map(c => (
                                    <option key={c} value={c} />
                                  ))}
                                </datalist>
                              </span>
                            ) : (
                              <button
                                onClick={() => handleCategoryEdit(s)}
                                className="flex items-center gap-1 bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 px-2 py-0.5 rounded transition-colors"
                                title="Click to change category"
                              >
                                <Tag size={10} />
                                {s.category || "Set category"}
                              </button>
                            )}
                            {s.created_at && (
                              <span>{new Date(s.created_at).toLocaleDateString()}</span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          onClick={() => setSelectedId(s.id)}
                          className="p-2 hover:bg-ag-border rounded-lg transition-colors text-ag-text-muted hover:text-ag-text"
                          title="View code"
                        >
                          <Eye size={16} />
                        </button>
                        <button
                          onClick={() => handleDelete(s.id)}
                          className="p-2 hover:bg-ag-red/10 rounded-lg transition-colors text-ag-text-muted hover:text-ag-red"
                          title="Delete"
                        >
                          <Trash2 size={16} />
                        </button>
                        <button
                          onClick={() => setSelectedId(s.id)}
                          className="p-2 hover:bg-purple-500/10 rounded-lg transition-colors text-ag-text-muted hover:text-purple-400"
                          title="Edit parameters"
                        >
                          <Settings size={16} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Detail view */}
      {selectedId && (
        <StrategyDetailView
          strategyId={selectedId}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
