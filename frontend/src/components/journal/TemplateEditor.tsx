"use client";
import { useState, useEffect } from "react";
import { ArrowLeft, Save, RotateCcw, Plus, Trash2, Pencil, Play } from "lucide-react";
import { api } from "@/lib/api";
import type { JournalTemplateItem, CustomTemplateItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import { NOTE_TYPES, getNoteTypeLabel, getNoteTypeBadge, COLOR_OPTIONS, getColorDot } from "./noteTypes";

interface Props {
  onBack: () => void;
  onUseTemplate?: (template: CustomTemplateItem) => void;
}

type Tab = "defaults" | "custom";

export default function TemplateEditor({ onBack, onUseTemplate }: Props) {
  const { addToast } = useTaskManager();
  const [tab, setTab] = useState<Tab>("custom");

  // --- Default templates state ---
  const [selected, setSelected] = useState<string>(NOTE_TYPES[0].id);
  const [templates, setTemplates] = useState<JournalTemplateItem[]>([]);
  const [defaultContent, setDefaultContent] = useState("");
  const [defaultSaving, setDefaultSaving] = useState(false);
  const [defaultSaved, setDefaultSaved] = useState(false);

  // --- Custom templates state ---
  const [customs, setCustoms] = useState<CustomTemplateItem[]>([]);
  const [editingCustom, setEditingCustom] = useState<CustomTemplateItem | null>(null);
  const [newName, setNewName] = useState("");
  const [newContent, setNewContent] = useState("");
  const [newColor, setNewColor] = useState("indigo");
  const [customSaving, setCustomSaving] = useState(false);
  const [showNewForm, setShowNewForm] = useState(false);

  useEffect(() => {
    api.journalTemplates().then((ts) => {
      setTemplates(ts);
      const t = ts.find((x) => x.template_type === selected);
      if (t) setDefaultContent(t.content);
    }).catch(() => {});
    api.customTemplatesList().then(setCustoms).catch(() => {});
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // Default template handlers
  const handleDefaultSelect = (type: string) => {
    setSelected(type);
    setDefaultSaved(false);
    const t = templates.find((x) => x.template_type === type);
    setDefaultContent(t?.content ?? "");
  };

  const handleDefaultSave = async () => {
    setDefaultSaving(true);
    try {
      await api.journalTemplateSave(selected, defaultContent);
      const ts = await api.journalTemplates();
      setTemplates(ts);
      setDefaultSaved(true);
      setTimeout(() => setDefaultSaved(false), 2000);
    } catch { addToast("Failed to save template", "error"); }
    setDefaultSaving(false);
  };

  const handleDefaultReset = () => {
    api.journalTemplateGet(selected)
      .then((t) => setDefaultContent(t.content))
      .catch(() => {});
  };

  // Custom template handlers
  const handleCreateCustom = async () => {
    if (!newName.trim()) return;
    setCustomSaving(true);
    try {
      const created = await api.customTemplateCreate(newName.trim(), newContent, newColor);
      setCustoms([...customs, created]);
      setNewName("");
      setNewContent("");
      setNewColor("indigo");
      setShowNewForm(false);
    } catch { addToast("Failed to create template", "error"); }
    setCustomSaving(false);
  };

  const handleUpdateCustom = async () => {
    if (!editingCustom) return;
    setCustomSaving(true);
    try {
      const updated = await api.customTemplateUpdate(editingCustom.id, {
        name: newName.trim() || editingCustom.name,
        content: newContent,
        color: newColor,
      });
      setCustoms(customs.map((c) => (c.id === updated.id ? updated : c)));
      setEditingCustom(null);
      setNewName("");
      setNewContent("");
      setNewColor("indigo");
    } catch { addToast("Failed to update template", "error"); }
    setCustomSaving(false);
  };

  const handleDeleteCustom = async (id: number) => {
    try {
      await api.customTemplateDelete(id);
      setCustoms(customs.filter((c) => c.id !== id));
      if (editingCustom?.id === id) {
        setEditingCustom(null);
        setNewName("");
        setNewContent("");
        setNewColor("indigo");
      }
    } catch { addToast("Failed to delete template", "error"); }
  };

  const startEditCustom = (t: CustomTemplateItem) => {
    setEditingCustom(t);
    setNewName(t.name);
    setNewContent(t.content);
    setNewColor(t.color || "indigo");
    setShowNewForm(false);
  };

  const cancelCustomForm = () => {
    setEditingCustom(null);
    setShowNewForm(false);
    setNewName("");
    setNewContent("");
    setNewColor("indigo");
  };

  return (
    <div className="max-w-3xl space-y-5">
      <div className="flex items-center justify-between">
        <button onClick={onBack}
          className="flex items-center gap-1.5 text-sm text-ag-text-muted hover:text-ag-text">
          <ArrowLeft size={16} /> Back to Calendar
        </button>
        <h2 className="text-lg font-bold">Templates</h2>
      </div>

      {/* Tab switcher */}
      <div className="flex gap-1 border-b border-ag-border">
        <button onClick={() => setTab("custom")}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            tab === "custom" ? "border-ag-accent text-ag-accent" : "border-transparent text-ag-text-muted hover:text-ag-text"
          }`}>
          My Templates
        </button>
        <button onClick={() => setTab("defaults")}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            tab === "defaults" ? "border-ag-accent text-ag-accent" : "border-transparent text-ag-text-muted hover:text-ag-text"
          }`}>
          Default Templates
        </button>
      </div>

      {tab === "defaults" && (
        <>
          <p className="text-xs text-ag-text-muted">
            Edit the default content that gets pre-filled when you create a new note.
          </p>

          <div className="flex flex-wrap gap-1.5">
            {NOTE_TYPES.map((t) => (
              <button key={t.id} onClick={() => handleDefaultSelect(t.id)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  selected === t.id
                    ? getNoteTypeBadge(t.id) + " ring-1 ring-current"
                    : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"
                }`}>
                {getNoteTypeLabel(t.id)}
              </button>
            ))}
          </div>

          <div>
            <label className="block text-xs text-ag-text-muted mb-1">
              Template for &quot;{getNoteTypeLabel(selected)}&quot;
            </label>
            <textarea value={defaultContent} onChange={(e) => { setDefaultContent(e.target.value); setDefaultSaved(false); }}
              rows={14}
              placeholder="Enter your template content... Use ## for section headings."
              className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm font-mono resize-y" />
          </div>

          <div className="flex items-center gap-3">
            <button onClick={handleDefaultSave} disabled={defaultSaving}
              className="flex items-center gap-1.5 px-5 py-2 bg-ag-accent text-white rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
              <Save size={14} /> {defaultSaving ? "Saving..." : defaultSaved ? "Saved!" : "Save Template"}
            </button>
            <button onClick={handleDefaultReset}
              className="flex items-center gap-1.5 px-3 py-2 border border-ag-border rounded-lg text-xs text-ag-text-muted hover:text-ag-text hover:bg-ag-card-hover">
              <RotateCcw size={12} /> Reset to Default
            </button>
          </div>
        </>
      )}

      {tab === "custom" && (
        <>
          <p className="text-xs text-ag-text-muted">
            Create reusable templates you can load when writing any note.
            Click the <Play size={10} className="inline" /> button to start a new note from a template.
          </p>

          {/* Existing custom templates list */}
          {customs.length > 0 && (
            <div className="space-y-2">
              {customs.map((t) => (
                <div key={t.id}
                  className={`bg-ag-card border rounded-lg p-3 flex items-center gap-3 ${
                    editingCustom?.id === t.id ? "border-ag-accent" : "border-ag-border"
                  }`}>
                  <span className={`w-3 h-3 rounded-full shrink-0 ${getColorDot(t.color || "indigo")}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{t.name}</p>
                    <p className="text-xs text-ag-text-muted truncate">
                      {t.content.slice(0, 80)}{t.content.length > 80 ? "..." : ""}
                    </p>
                  </div>
                  {onUseTemplate && (
                    <button onClick={() => onUseTemplate(t)}
                      title="Use this template"
                      className="px-2.5 py-1.5 bg-ag-accent/15 text-ag-accent rounded-lg hover:bg-ag-accent/25 shrink-0 flex items-center gap-1 text-xs font-medium">
                      <Play size={12} /> Use
                    </button>
                  )}
                  <button onClick={() => startEditCustom(t)}
                    title="Edit template"
                    className="text-ag-text-muted hover:text-ag-accent shrink-0">
                    <Pencil size={14} />
                  </button>
                  <button onClick={() => handleDeleteCustom(t.id)}
                    title="Delete template"
                    className="text-ag-text-muted hover:text-ag-red shrink-0">
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* New / Edit form */}
          {(showNewForm || editingCustom) ? (
            <div className="bg-ag-card border border-ag-border rounded-xl p-4 space-y-3">
              <h4 className="text-sm font-semibold">
                {editingCustom ? "Edit Template" : "New Template"}
              </h4>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Template Name</label>
                <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)}
                  placeholder="e.g. Trading Day Review, Weekly Planning..."
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Color Marker</label>
                <div className="flex items-center gap-1.5">
                  {COLOR_OPTIONS.map((c) => (
                    <button key={c.id} onClick={() => setNewColor(c.id)} title={c.label}
                      className={`w-6 h-6 rounded-full ${getColorDot(c.id)} transition-all ${
                        newColor === c.id ? "ring-2 ring-offset-1 ring-offset-ag-dark ring-current scale-110" : "opacity-60 hover:opacity-100"
                      }`} />
                  ))}
                </div>
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Template Content</label>
                <textarea value={newContent} onChange={(e) => setNewContent(e.target.value)}
                  rows={10}
                  placeholder={"## Section 1\n\n## Section 2\n\nUse ## for headings."}
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm font-mono resize-y" />
              </div>
              <div className="flex items-center gap-2">
                <button onClick={editingCustom ? handleUpdateCustom : handleCreateCustom}
                  disabled={customSaving || !newName.trim()}
                  className="flex items-center gap-1.5 px-4 py-2 bg-ag-accent text-white rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
                  <Save size={14} /> {customSaving ? "Saving..." : editingCustom ? "Update" : "Create"}
                </button>
                <button onClick={cancelCustomForm}
                  className="px-3 py-2 border border-ag-border rounded-lg text-sm text-ag-text-muted hover:text-ag-text">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button onClick={() => { setShowNewForm(true); setEditingCustom(null); setNewName(""); setNewContent(""); setNewColor("indigo"); }}
              className="flex items-center gap-1.5 px-4 py-2 bg-ag-accent text-white rounded-lg text-sm font-medium hover:brightness-110">
              <Plus size={14} /> Create Template
            </button>
          )}
        </>
      )}
    </div>
  );
}
