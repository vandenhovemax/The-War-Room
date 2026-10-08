"use client";
import { useState, useEffect } from "react";
import { Save, Trash2, FolderOpen, X, StickyNote, Copy } from "lucide-react";

interface SavedConfig<T> {
  name: string;
  config: T;
  savedAt: string;
  notes?: string;
  category?: string;
}

interface Props<T> {
  storageKey: string;
  currentConfig: T;
  onLoad: (config: T) => void;
  /** Auto-generated name from current settings — pre-fills save form */
  defaultName?: string;
}

export default function OptimizerConfigManager<T>({ storageKey, currentConfig, onLoad, defaultName }: Props<T>) {
  const [configs, setConfigs] = useState<SavedConfig<T>[]>([]);
  const [selectedName, setSelectedName] = useState("");
  const [showSaveForm, setShowSaveForm] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveNotes, setSaveNotes] = useState("");
  const [saveCategory, setSaveCategory] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [editingNotes, setEditingNotes] = useState(false);
  const [editNotesValue, setEditNotesValue] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Load saved configs from localStorage
  useEffect(() => {
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) setConfigs(JSON.parse(stored) as SavedConfig<T>[]);
    } catch { /* ignore corrupt data */ }
  }, [storageKey]);

  // Derive unique categories from saved configs
  const categories = Array.from(new Set(configs.map((c) => c.category).filter(Boolean))) as string[];

  const persist = (updated: SavedConfig<T>[]) => {
    setConfigs(updated);
    localStorage.setItem(storageKey, JSON.stringify(updated));
  };

  const handleSave = () => {
    if (!saveName.trim()) return;
    const trimmed = saveName.trim();
    // Use new category if typed, otherwise use selected dropdown value
    const cat = newCategory.trim() || saveCategory || undefined;
    const filtered = configs.filter((c) => c.name !== trimmed);
    persist([...filtered, {
      name: trimmed,
      config: currentConfig,
      savedAt: new Date().toISOString(),
      notes: saveNotes.trim() || undefined,
      category: cat,
    }]);
    setSelectedName(trimmed);
    setShowSaveForm(false);
    setSaveName("");
    setSaveNotes("");
    setSaveCategory("");
    setNewCategory("");
  };

  const handleLoad = () => {
    const found = configs.find((c) => c.name === selectedName);
    if (found) onLoad(found.config);
  };

  const handleDelete = () => {
    if (!selectedName) return;
    persist(configs.filter((c) => c.name !== selectedName));
    setSelectedName("");
    setConfirmDelete(false);
  };

  const handleCopy = () => {
    const found = configs.find((c) => c.name === selectedName);
    if (!found) return;
    // Find a unique name: "Original (Copy)", "Original (Copy 2)", etc.
    let copyName = `${found.name} (Copy)`;
    let n = 2;
    while (configs.some((c) => c.name === copyName)) {
      copyName = `${found.name} (Copy ${n})`;
      n++;
    }
    persist([...configs, {
      ...found,
      name: copyName,
      savedAt: new Date().toISOString(),
    }]);
    setSelectedName(copyName);
    // Open save form so user can rename immediately
    setShowSaveForm(true);
    setSaveName(copyName);
    setSaveNotes(found.notes || "");
    setSaveCategory(found.category || "");
    setNewCategory("");
  };

  const selectedConfig = configs.find((c) => c.name === selectedName);

  const handleSaveNotes = () => {
    if (!selectedConfig) return;
    const updated = configs.map((c) =>
      c.name === selectedName ? { ...c, notes: editNotesValue.trim() || undefined } : c
    );
    persist(updated);
    setEditingNotes(false);
  };

  // Group configs by category for the dropdown
  const uncategorized = configs.filter((c) => !c.category);
  const categorized = new Map<string, SavedConfig<T>[]>();
  for (const c of configs) {
    if (c.category) {
      const list = categorized.get(c.category) || [];
      list.push(c);
      categorized.set(c.category, list);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-ag-text-muted font-medium">Config:</span>
        <select
          value={selectedName}
          onChange={(e) => { setSelectedName(e.target.value); setEditingNotes(false); setConfirmDelete(false); }}
          className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs min-w-[140px]"
        >
          <option value="">— select —</option>
          {/* Uncategorized configs first */}
          {uncategorized.map((c) => (
            <option key={c.name} value={c.name} title={c.notes || ""}>
              {c.name}{c.notes ? " *" : ""}
            </option>
          ))}
          {/* Categorized configs in optgroups */}
          {Array.from(categorized.entries()).map(([cat, items]) => (
            <optgroup key={cat} label={`── ${cat} ──`}>
              {items.map((c) => (
                <option key={c.name} value={c.name} title={c.notes || ""}>
                  {c.name}{c.notes ? " *" : ""}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <button onClick={handleLoad} disabled={!selectedName}
          className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-ag-accent/15 text-ag-accent hover:bg-ag-accent/25 disabled:opacity-40 transition-colors"
          title="Load selected config — applies all saved optimizer settings">
          <FolderOpen size={12} /> Load
        </button>
        <button onClick={() => {
          setShowSaveForm(!showSaveForm);
          // Auto-increment name if defaultName already exists in saved configs
          let base = defaultName || "";
          if (base && configs.some((c) => c.name === base)) {
            let n = 2;
            while (configs.some((c) => c.name === `${base} (${n})`)) n++;
            base = `${base} (${n})`;
          }
          setSaveName(base);
          setSaveNotes(""); setSaveCategory(""); setNewCategory("");
        }}
          className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-ag-green/15 text-ag-green hover:bg-ag-green/25 transition-colors"
          title="Save current optimizer settings as a named config">
          <Save size={12} /> Save
        </button>
        {selectedName && (
          <>
            <button onClick={handleCopy}
              className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-ag-accent/10 text-ag-accent hover:bg-ag-accent/20 transition-colors"
              title="Duplicate this config — creates a copy you can rename and tweak">
              <Copy size={12} />
            </button>
            {confirmDelete ? (
              <span className="flex items-center gap-1">
                <span className="text-[10px] text-ag-red">Delete &ldquo;{selectedName}&rdquo;?</span>
                <button onClick={handleDelete}
                  className="px-2 py-1 text-[10px] rounded bg-ag-red/20 text-ag-red hover:bg-ag-red/30 font-medium transition-colors"
                  title="Confirm delete">
                  Yes
                </button>
                <button onClick={() => setConfirmDelete(false)}
                  className="px-2 py-1 text-[10px] rounded bg-ag-border text-ag-text-muted hover:text-ag-text transition-colors"
                  title="Cancel">
                  No
                </button>
              </span>
            ) : (
              <button onClick={() => setConfirmDelete(true)}
                className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-ag-red/15 text-ag-red hover:bg-ag-red/25 transition-colors"
                title="Delete selected config">
                <Trash2 size={12} />
              </button>
            )}
          </>
        )}
      </div>

      {/* Inline save form */}
      {showSaveForm && (
        <div className="bg-ag-dark/50 border border-ag-border rounded-lg p-3 space-y-2">
          <input
            type="text"
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            placeholder="Config name..."
            className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs"
            autoFocus
            onKeyDown={(e) => { if (e.key === "Enter") handleSave(); if (e.key === "Escape") setShowSaveForm(false); }}
          />
          {/* Category selector */}
          <div className="flex items-center gap-2">
            <select
              value={saveCategory}
              onChange={(e) => { setSaveCategory(e.target.value); if (e.target.value) setNewCategory(""); }}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs flex-1"
              title="Choose an existing category or type a new one below"
            >
              <option value="">— no category —</option>
              {categories.map((cat) => (
                <option key={cat} value={cat}>{cat}</option>
              ))}
            </select>
            <input
              type="text"
              value={newCategory}
              onChange={(e) => { setNewCategory(e.target.value); if (e.target.value) setSaveCategory(""); }}
              placeholder="New category..."
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs flex-1"
              title="Type a new category name (overrides dropdown)"
            />
          </div>
          <textarea
            value={saveNotes}
            onChange={(e) => setSaveNotes(e.target.value)}
            placeholder="Notes (optional) — describe what this config is for..."
            rows={2}
            className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs resize-none placeholder:text-ag-text-muted/50"
          />
          <div className="flex items-center gap-2">
            <button onClick={handleSave} disabled={!saveName.trim()}
              className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-ag-green/15 text-ag-green hover:bg-ag-green/25 disabled:opacity-40 transition-colors"
              title="Save config with this name and notes">
              <Save size={10} /> Save
            </button>
            <button onClick={() => setShowSaveForm(false)}
              className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-ag-border text-ag-text-muted hover:text-ag-text transition-colors"
              title="Cancel saving">
              <X size={10} /> Cancel
            </button>
          </div>
        </div>
      )}

      {/* Notes display/edit for selected config */}
      {selectedConfig && !showSaveForm && (
        <div className="text-xs text-ag-text-muted">
          {editingNotes ? (
            <div className="flex items-start gap-2">
              <textarea
                value={editNotesValue}
                onChange={(e) => setEditNotesValue(e.target.value)}
                rows={2}
                className="flex-1 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs resize-none"
                autoFocus
                onKeyDown={(e) => { if (e.key === "Escape") setEditingNotes(false); }}
              />
              <button onClick={handleSaveNotes}
                className="px-2 py-1 text-[10px] rounded bg-ag-green/15 text-ag-green hover:bg-ag-green/25 transition-colors"
                title="Save notes for this config">
                Save
              </button>
              <button onClick={() => setEditingNotes(false)}
                className="px-2 py-1 text-[10px] rounded bg-ag-border text-ag-text-muted hover:text-ag-text transition-colors"
                title="Cancel editing">
                <X size={10} />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              {selectedConfig.category && (
                <span className="px-1.5 py-0.5 rounded bg-ag-accent/10 text-ag-accent text-[10px]">{selectedConfig.category}</span>
              )}
              {selectedConfig.notes ? (
                <span className="italic">{selectedConfig.notes}</span>
              ) : (
                <span className="opacity-50">No notes</span>
              )}
              <button
                onClick={() => { setEditingNotes(true); setEditNotesValue(selectedConfig.notes || ""); }}
                className="text-ag-accent hover:text-ag-accent/80 transition-colors"
                title="Edit notes for this config"
              >
                <StickyNote size={12} />
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
