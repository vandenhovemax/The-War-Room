"use client";
import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  Plus, Trash2, Save, Edit3, X, Copy, FolderOpen, CheckSquare, Square,
  CopyPlus, ChevronDown, ChevronRight, Pencil, SlidersHorizontal,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ParameterDefinition, StrategyConfigItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import ParameterForm from "./ParameterForm";

type ParamValues = Record<string, number | boolean | string>;

interface ConfigManagerProps {
  strategyId: number;
  definitions: ParameterDefinition[];
  onConfigSelect?: (config: StrategyConfigItem | null) => void;
}

export default function ConfigManager({ strategyId, definitions, onConfigSelect }: ConfigManagerProps) {
  const { addToast } = useTaskManager();
  const [configs, setConfigs] = useState<StrategyConfigItem[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [tag, setTag] = useState("");
  const [values, setValues] = useState<ParamValues>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  // Folder collapse state — all collapsed by default
  const [collapsedFolders, setCollapsedFolders] = useState<Record<string, boolean>>({});

  // Folder rename state
  const [renamingFolder, setRenamingFolder] = useState<string | null>(null);
  const [renameInput, setRenameInput] = useState("");
  const renameRef = useRef<HTMLInputElement>(null);

  // Bulk state (shared for copy + edit)
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkPanel, setBulkPanel] = useState<"copy" | "edit" | null>(null);
  const [bulkSuffix, setBulkSuffix] = useState(" (Copy)");
  const [bulkCategory, setBulkCategory] = useState("");
  const [bulkOverrides, setBulkOverrides] = useState<ParamValues>({});
  const [bulkSaving, setBulkSaving] = useState(false);
  const [bulkChangedParams, setBulkChangedParams] = useState<Set<string>>(new Set());

  const loadConfigs = async () => {
    try { setConfigs(await api.strategyConfigs(strategyId)); }
    catch { addToast("Failed to load configs", "error"); }
  };

  useEffect(() => { loadConfigs(); }, [strategyId]);
  useEffect(() => { if (renamingFolder && renameRef.current) renameRef.current.focus(); }, [renamingFolder]);

  const defaults = useCallback((): ParamValues => {
    const d: ParamValues = {};
    for (const p of definitions) d[p.name] = p.default;
    return d;
  }, [definitions]);

  const existingTags = useMemo(() => {
    const tags = new Set<string>();
    for (const c of configs) { if (c.tag) tags.add(c.tag); }
    return Array.from(tags).sort();
  }, [configs]);

  const grouped = useMemo(() => {
    const hasTags = configs.some(c => c.tag);
    if (!hasTags) return null;
    const groups: Record<string, StrategyConfigItem[]> = {};
    for (const c of configs) {
      const t = c.tag || "(uncategorized)";
      if (!groups[t]) groups[t] = [];
      groups[t].push(c);
    }
    const sorted = Object.keys(groups).sort((a, b) => {
      if (a === "(uncategorized)") return 1;
      if (b === "(uncategorized)") return -1;
      return a.localeCompare(b);
    });
    return sorted.map(t => ({ tag: t, items: groups[t] }));
  }, [configs]);

  // Folder helpers
  const isFolderCollapsed = (folderName: string) => collapsedFolders[folderName] !== false; // default collapsed
  const toggleFolder = (folderName: string) => {
    setCollapsedFolders(prev => ({ ...prev, [folderName]: !isFolderCollapsed(folderName) }));
  };

  const handleRenameFolder = async (oldName: string) => {
    const newName = renameInput.trim();
    if (!newName || newName === oldName) { setRenamingFolder(null); return; }
    try {
      const folderConfigs = configs.filter(c => (c.tag || "(uncategorized)") === oldName);
      for (const c of folderConfigs) {
        await api.updateStrategyConfig(strategyId, c.id, { tag: newName });
      }
      addToast(`Renamed folder to "${newName}"`, "success");
      setRenamingFolder(null);
      loadConfigs();
    } catch { addToast("Failed to rename folder", "error"); }
  };

  // Folder-level bulk actions
  const startFolderCopy = (items: StrategyConfigItem[]) => {
    setSelectMode(true);
    setSelectedIds(new Set(items.map(c => c.id)));
    setBulkPanel("copy");
    setBulkSuffix(" (Copy)"); setBulkCategory(""); setBulkOverrides(defaults());
    setBulkChangedParams(new Set()); setError("");
  };

  const startFolderEdit = (items: StrategyConfigItem[]) => {
    setSelectMode(true);
    setSelectedIds(new Set(items.map(c => c.id)));
    setBulkPanel("edit");
    setBulkOverrides(defaults()); setBulkChangedParams(new Set()); setError("");
  };

  // Single config CRUD
  const handleCreate = () => {
    setShowCreate(true); setEditingId(null); setSelectMode(false); setBulkPanel(null);
    setName(""); setDesc(""); setTag(""); setValues(defaults()); setError("");
  };
  const handleEdit = (c: StrategyConfigItem) => {
    setShowCreate(false); setEditingId(c.id); setSelectMode(false); setBulkPanel(null);
    setName(c.name); setDesc(c.description); setTag(c.tag || "");
    setValues({ ...defaults(), ...c.parameter_values }); setError("");
  };
  // Check if parameter values match any existing config
  const findDuplicate = (vals: ParamValues, excludeId?: number): string | null => {
    for (const c of configs) {
      if (excludeId && c.id === excludeId) continue;
      const keys = Object.keys(vals);
      const otherKeys = Object.keys(c.parameter_values);
      if (keys.length !== otherKeys.length) continue;
      const match = keys.every(k => String(vals[k]) === String(c.parameter_values[k]));
      if (match) return c.name;
    }
    return null;
  };

  const handleSave = async () => {
    if (!name.trim()) { setError("Name is required"); return; }
    const dupe = findDuplicate(values, editingId ?? undefined);
    if (dupe) { setError(`Same as "${dupe}" — parameters are identical`); return; }
    setSaving(true); setError("");
    try {
      if (editingId) {
        await api.updateStrategyConfig(strategyId, editingId, {
          name, description: desc, tag: tag.trim() || null, parameter_values: values,
        });
      } else {
        await api.createStrategyConfig(strategyId, name, desc, values, tag.trim() || null);
      }
      setShowCreate(false); setEditingId(null); loadConfigs();
    } catch (e) { setError(e instanceof Error ? e.message : "Save failed"); }
    finally { setSaving(false); }
  };
  const handleDelete = async (configId: number) => {
    try { await api.deleteStrategyConfig(strategyId, configId); setConfirmDeleteId(null); loadConfigs(); }
    catch { addToast("Failed to delete config", "error"); }
  };
  const handleCopy = (c: StrategyConfigItem) => {
    setShowCreate(true); setEditingId(null); setSelectMode(false); setBulkPanel(null);
    let copyName = `${c.name} (Copy)`;
    let n = 2;
    while (configs.some(cfg => cfg.name === copyName)) { copyName = `${c.name} (Copy ${n})`; n++; }
    setName(copyName); setDesc(c.description); setTag(c.tag || "");
    setValues({ ...defaults(), ...c.parameter_values }); setError("");
  };

  // Select mode
  const toggleSelect = (id: number) => {
    setSelectedIds(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  };
  const toggleSelectAll = () => {
    setSelectedIds(selectedIds.size === configs.length ? new Set() : new Set(configs.map(c => c.id)));
  };

  // Bulk panel
  const startBulkCopy = () => {
    setBulkPanel("copy"); setBulkSuffix(" (Copy)"); setBulkCategory("");
    setBulkOverrides(defaults()); setBulkChangedParams(new Set()); setError("");
  };
  const startBulkEdit = () => {
    setBulkPanel("edit"); setBulkOverrides(defaults()); setBulkChangedParams(new Set()); setError("");
  };

  const handleBulkOverrideChange = (newValues: ParamValues) => {
    const changed = new Set<string>();
    const d = defaults();
    for (const key of Object.keys(newValues)) { if (newValues[key] !== d[key]) changed.add(key); }
    setBulkChangedParams(changed);
    setBulkOverrides(newValues);
  };

  const handleBulkSave = async () => {
    // For copies: check if any would be duplicates of existing configs
    if (bulkPanel === "copy") {
      const selected = configs.filter(c => selectedIds.has(c.id));
      for (const src of selected) {
        const mergedValues = { ...src.parameter_values };
        for (const key of bulkChangedParams) mergedValues[key] = bulkOverrides[key];
        const dupe = findDuplicate(mergedValues);
        if (dupe) {
          setError(`"${src.name}" copy would be identical to "${dupe}" — change at least one parameter`);
          return;
        }
      }
    }
    setBulkSaving(true); setError("");
    const selected = configs.filter(c => selectedIds.has(c.id));
    let done = 0;
    try {
      if (bulkPanel === "copy") {
        for (const src of selected) {
          const mergedValues = { ...src.parameter_values };
          for (const key of bulkChangedParams) mergedValues[key] = bulkOverrides[key];
          const cat = bulkCategory.trim() || src.tag || null;
          await api.createStrategyConfig(strategyId, `${src.name}${bulkSuffix}`, src.description, mergedValues, cat);
          done++;
        }
        addToast(`Created ${done} config copies`, "success");
      } else {
        // Bulk edit — update existing configs
        for (const src of selected) {
          if (bulkChangedParams.size === 0) continue;
          const mergedValues = { ...src.parameter_values };
          for (const key of bulkChangedParams) mergedValues[key] = bulkOverrides[key];
          await api.updateStrategyConfig(strategyId, src.id, { parameter_values: mergedValues });
          done++;
        }
        addToast(`Updated ${done} configs`, "success");
      }
      setBulkPanel(null); setSelectMode(false); setSelectedIds(new Set());
      loadConfigs();
    } catch (e) {
      setError(`${done}/${selected.length} done — ${e instanceof Error ? e.message : "failed"}`);
    } finally { setBulkSaving(false); }
  };

  const isEditing = showCreate || editingId !== null;
  const isAnythingOpen = isEditing || bulkPanel !== null;

  const renderConfigCard = (c: StrategyConfigItem) => (
    <div
      key={c.id}
      className={`flex items-center justify-between bg-ag-dark border rounded-lg px-3 py-2 group cursor-pointer transition-colors ${
        selectMode && selectedIds.has(c.id) ? "border-ag-accent/50 bg-ag-accent/5" : "border-ag-border hover:border-ag-accent/50"
      }`}
      onClick={() => selectMode ? toggleSelect(c.id) : onConfigSelect?.(c)}
    >
      <div className="flex items-center gap-2 min-w-0">
        {selectMode && (
          selectedIds.has(c.id)
            ? <CheckSquare size={14} className="text-ag-accent shrink-0" />
            : <Square size={14} className="text-ag-text-muted shrink-0" />
        )}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{c.name}</span>
            {c.tag && !grouped && (
              <span className="text-[10px] bg-blue-500/10 text-blue-400 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                <FolderOpen size={8} /> {c.tag}
              </span>
            )}
          </div>
          {c.description && <span className="text-xs text-ag-text-muted">{c.description}</span>}
          <p className="text-[10px] text-ag-text-muted truncate max-w-[600px]">
            {(() => {
              const entries = Object.entries(c.parameter_values);
              const shown = entries.slice(0, 5).map(([k, v]) => `${k}: ${v}`).join(" · ");
              return entries.length > 5 ? `${shown} · +${entries.length - 5} more` : shown;
            })()}
          </p>
        </div>
      </div>
      {!selectMode && (
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
          <button onClick={(e) => { e.stopPropagation(); handleCopy(c); }} className="p-1 text-ag-text-muted hover:text-ag-accent" title="Duplicate"><Copy size={12} /></button>
          <button onClick={(e) => { e.stopPropagation(); handleEdit(c); }} className="p-1 text-ag-text-muted hover:text-ag-text" title="Edit"><Edit3 size={12} /></button>
          {confirmDeleteId === c.id ? (
            <span className="flex items-center gap-1 ml-1" onClick={(e) => e.stopPropagation()}>
              <span className="text-[10px] text-ag-red whitespace-nowrap">Delete?</span>
              <button onClick={() => handleDelete(c.id)} className="px-1.5 py-0.5 text-[10px] rounded bg-ag-red/20 text-ag-red hover:bg-ag-red/30 font-medium transition-colors">Yes</button>
              <button onClick={() => setConfirmDeleteId(null)} className="px-1.5 py-0.5 text-[10px] rounded bg-ag-border text-ag-text-muted hover:text-ag-text transition-colors">No</button>
            </span>
          ) : (
            <button onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(c.id); }} className="p-1 text-ag-text-muted hover:text-ag-red" title="Delete"><Trash2 size={12} /></button>
          )}
        </div>
      )}
    </div>
  );

  const renderFolderHeader = (tagName: string, items: StrategyConfigItem[]) => {
    const collapsed = isFolderCollapsed(tagName);
    const isRenaming = renamingFolder === tagName;

    return (
      <div className="flex items-center justify-between group/folder">
        <button onClick={() => toggleFolder(tagName)} className="flex items-center gap-1.5 text-xs text-ag-text-muted hover:text-ag-text transition-colors">
          {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
          <FolderOpen size={10} />
          {isRenaming ? (
            <input
              ref={renameRef}
              value={renameInput}
              onChange={(e) => setRenameInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleRenameFolder(tagName); if (e.key === "Escape") setRenamingFolder(null); }}
              onBlur={() => handleRenameFolder(tagName)}
              onClick={(e) => e.stopPropagation()}
              className="bg-ag-bg border border-ag-accent/50 rounded px-1.5 py-0.5 text-xs w-32 outline-none"
            />
          ) : (
            <>
              <span className="font-medium">{tagName}</span>
              <span className="opacity-60">({items.length})</span>
            </>
          )}
        </button>
        {!isAnythingOpen && !isRenaming && tagName !== "(uncategorized)" && (
          <div className="flex items-center gap-1 opacity-0 group-hover/folder:opacity-100 transition-opacity">
            <button
              onClick={() => { setRenamingFolder(tagName); setRenameInput(tagName); }}
              className="p-1 text-ag-text-muted hover:text-ag-text" title="Rename folder"
            ><Pencil size={10} /></button>
            <button
              onClick={() => startFolderCopy(items)}
              className="p-1 text-ag-text-muted hover:text-purple-400" title="Copy all in folder"
            ><CopyPlus size={10} /></button>
            <button
              onClick={() => startFolderEdit(items)}
              className="p-1 text-ag-text-muted hover:text-amber-400" title="Edit all in folder"
            ><SlidersHorizontal size={10} /></button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold">Saved Configs ({configs.length})</h4>
        {!isAnythingOpen && configs.length > 0 && (
          <div className="flex items-center gap-3">
            {selectMode && selectedIds.size > 0 && (
              <>
                <button onClick={startBulkCopy} className="flex items-center gap-1.5 text-xs text-purple-400 hover:text-purple-300">
                  <CopyPlus size={12} /> Copy ({selectedIds.size})
                </button>
                <button onClick={startBulkEdit} className="flex items-center gap-1.5 text-xs text-amber-400 hover:text-amber-300">
                  <SlidersHorizontal size={12} /> Edit ({selectedIds.size})
                </button>
              </>
            )}
            <button
              onClick={() => { setSelectMode(!selectMode); setSelectedIds(new Set()); }}
              className={`text-xs transition-colors ${selectMode ? "text-ag-accent" : "text-ag-text-muted hover:text-ag-text"}`}
            >{selectMode ? "Done" : "Select"}</button>
            {selectMode && (
              <button onClick={toggleSelectAll} className="text-xs text-ag-text-muted hover:text-ag-text">
                {selectedIds.size === configs.length ? "Deselect All" : "Select All"}
              </button>
            )}
            {!selectMode && (
              <button onClick={handleCreate} className="flex items-center gap-1.5 text-xs text-ag-accent hover:text-ag-accent/80">
                <Plus size={12} /> New Config
              </button>
            )}
          </div>
        )}
        {!isAnythingOpen && configs.length === 0 && (
          <button onClick={handleCreate} className="flex items-center gap-1.5 text-xs text-ag-accent hover:text-ag-accent/80">
            <Plus size={12} /> New Config
          </button>
        )}
      </div>

      {!isAnythingOpen && configs.length === 0 && (
        <p className="text-xs text-ag-text-muted">No configs yet. Create one to save parameter presets.</p>
      )}

      {/* Config list — grouped with collapsible folders */}
      {!isAnythingOpen && grouped ? (
        <div className="space-y-2">
          {grouped.map(({ tag: tagName, items }) => (
            <div key={tagName} className="space-y-1.5">
              {renderFolderHeader(tagName, items)}
              {!isFolderCollapsed(tagName) && items.map(renderConfigCard)}
            </div>
          ))}
        </div>
      ) : (
        !isAnythingOpen && configs.map(renderConfigCard)
      )}

      {/* Bulk Copy / Edit panel */}
      {bulkPanel && (
        <div className={`bg-ag-dark border rounded-lg p-4 space-y-3 ${bulkPanel === "copy" ? "border-purple-500/30" : "border-amber-500/30"}`}>
          <div className="flex items-center justify-between">
            <h5 className="text-sm font-medium flex items-center gap-2">
              {bulkPanel === "copy" ? (
                <><CopyPlus size={14} className="text-purple-400" /> Bulk Copy — {selectedIds.size} config{selectedIds.size !== 1 ? "s" : ""}</>
              ) : (
                <><SlidersHorizontal size={14} className="text-amber-400" /> Bulk Edit — {selectedIds.size} config{selectedIds.size !== 1 ? "s" : ""}</>
              )}
            </h5>
            <button onClick={() => { setBulkPanel(null); setSelectMode(false); setSelectedIds(new Set()); }} className="text-ag-text-muted hover:text-ag-text">
              <X size={14} />
            </button>
          </div>

          {bulkPanel === "copy" && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Name Suffix (appended to each copy)</label>
                <input value={bulkSuffix} onChange={(e) => setBulkSuffix(e.target.value)} placeholder=" (1h)"
                  className="w-full bg-ag-card border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent outline-none" />
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Category for copies (optional)</label>
                <input value={bulkCategory} onChange={(e) => setBulkCategory(e.target.value)} placeholder="Leave empty to keep original" list="bulk-cat-suggestions"
                  className="w-full bg-ag-card border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent outline-none" />
                <datalist id="bulk-cat-suggestions">{existingTags.map(t => <option key={t} value={t} />)}</datalist>
              </div>
            </div>
          )}

          <div>
            <p className="text-xs text-ag-text-muted mb-2">
              {bulkPanel === "copy"
                ? "Parameter Overrides — only changed parameters get applied to all copies."
                : "Change parameters below — only changed ones will be updated on all selected configs."}
              {bulkChangedParams.size > 0 && (
                <span className={`ml-1 font-medium ${bulkPanel === "copy" ? "text-purple-400" : "text-amber-400"}`}>
                  ({bulkChangedParams.size} change{bulkChangedParams.size !== 1 ? "s" : ""}: {Array.from(bulkChangedParams).join(", ")})
                </span>
              )}
            </p>
            <ParameterForm definitions={definitions} values={bulkOverrides} onChange={handleBulkOverrideChange} />
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleBulkSave}
              disabled={bulkSaving || (bulkPanel === "edit" && bulkChangedParams.size === 0)}
              className={`flex items-center gap-1.5 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 ${
                bulkPanel === "copy" ? "bg-purple-600 hover:bg-purple-500" : "bg-amber-600 hover:bg-amber-500"
              }`}
            >
              {bulkPanel === "copy" ? <CopyPlus size={12} /> : <SlidersHorizontal size={12} />}
              {bulkSaving
                ? (bulkPanel === "copy" ? "Creating..." : "Updating...")
                : bulkPanel === "copy"
                  ? `Create ${selectedIds.size} Copies`
                  : `Update ${selectedIds.size} Configs`}
            </button>
            {error && <span className="text-xs text-ag-red">{error}</span>}
          </div>
        </div>
      )}

      {/* Create / Edit single config form */}
      {isEditing && (
        <div className="bg-ag-dark border border-ag-border rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h5 className="text-sm font-medium">{editingId ? "Edit Config" : "New Config"}</h5>
            <button onClick={() => { setShowCreate(false); setEditingId(null); }} className="text-ag-text-muted hover:text-ag-text"><X size={14} /></button>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">Config Name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Conservative, Aggressive..."
                className="w-full bg-ag-card border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent outline-none" />
            </div>
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">Description</label>
              <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Optional notes"
                className="w-full bg-ag-card border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent outline-none" />
            </div>
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">Category</label>
              <input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="e.g. Conservative, Trend..." list="tag-suggestions"
                className="w-full bg-ag-card border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent outline-none" />
              <datalist id="tag-suggestions">{existingTags.map(t => <option key={t} value={t} />)}</datalist>
            </div>
          </div>
          <ParameterForm definitions={definitions} values={values} onChange={setValues} />
          <div className="flex items-center gap-2">
            <button onClick={handleSave} disabled={saving || !name.trim()}
              className="flex items-center gap-1.5 bg-ag-green hover:bg-ag-green/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50">
              <Save size={12} /> {saving ? "Saving..." : "Save Config"}
            </button>
            {error && <span className="text-xs text-ag-red">{error}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
