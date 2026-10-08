"use client";
import { useState, useEffect } from "react";
import { Upload, X, FileDown, Plus, Trash2, GripVertical, ChevronDown } from "lucide-react";
import { api } from "@/lib/api";
import type { JournalNoteItem, ChecklistItem, CustomTemplateItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import { getNoteTypeLabel, getNoteTypeBadge, COLOR_OPTIONS, getColorDot } from "./noteTypes";

interface Props {
  noteType: string;
  existingNote: JournalNoteItem | null;
  preloadTemplate?: CustomTemplateItem | null;
  defaultDate: string;
  onSaved: () => void;
  onCancel: () => void;
}

/** Try to parse content as a JSON checklist array. */
function parseChecklist(content: string): ChecklistItem[] | null {
  try {
    const parsed = JSON.parse(content);
    if (Array.isArray(parsed) && parsed.every((item: unknown) =>
      typeof item === "object" && item !== null && "text" in item && "checked" in item
    )) {
      return parsed as ChecklistItem[];
    }
  } catch { /* not JSON — that's fine */ }
  return null;
}

export default function NoteEditor({ noteType, existingNote, preloadTemplate, defaultDate, onSaved, onCancel }: Props) {
  const { addToast } = useTaskManager();
  const isEdit = !!existingNote;
  const isTodo = noteType === "todo";
  const [title, setTitle] = useState(existingNote?.title ?? "");
  const [subject, setSubject] = useState(existingNote?.subject ?? "");
  const [content, setContent] = useState(existingNote?.content ?? preloadTemplate?.content ?? "");
  const [noteDate, setNoteDate] = useState(existingNote?.note_date ?? defaultDate);
  const [color, setColor] = useState(existingNote?.color ?? preloadTemplate?.color ?? (isTodo ? "orange" : "indigo"));
  const [images, setImages] = useState<string[]>(existingNote?.images ?? []);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);

  // Checklist state for TODO notes
  const [checkItems, setCheckItems] = useState<ChecklistItem[]>(() => {
    if (isTodo && existingNote?.content) {
      const parsed = parseChecklist(existingNote.content);
      if (parsed) return parsed;
    }
    return [{ text: "", checked: false }];
  });

  // Load template for new non-journal notes
  useEffect(() => {
    if (isEdit) return;
    if (noteType === "journal") return;
    api.journalTemplateGet(noteType)
      .then((t) => {
        if (isTodo) {
          const parsed = parseChecklist(t.content);
          if (parsed) setCheckItems(parsed);
        } else {
          if (!content) setContent(t.content);
        }
      })
      .catch(() => {});
  }, [noteType, isEdit]); // eslint-disable-line react-hooks/exhaustive-deps

  // Custom templates for the "Load Template" dropdown
  const [customTemplates, setCustomTemplates] = useState<CustomTemplateItem[]>([]);
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);

  useEffect(() => {
    api.customTemplatesList().then(setCustomTemplates).catch(() => {});
  }, []);

  const loadDefaultTemplate = () => {
    api.journalTemplateGet(noteType)
      .then((t) => {
        if (isTodo) {
          const parsed = parseChecklist(t.content);
          if (parsed) setCheckItems(parsed);
        } else {
          setContent(t.content);
        }
      })
      .catch(() => {});
    setShowTemplatePicker(false);
  };

  const loadCustomTemplate = (tpl: CustomTemplateItem) => {
    setContent(tpl.content);
    setShowTemplatePicker(false);
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const result = await api.journalUploadImage(file);
      setImages([...images, result.filename]);
    } catch { addToast("Failed to upload image", "error"); }
    setUploading(false);
    e.target.value = "";
  };

  const handlePaste = async (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of Array.from(items)) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (!file) continue;
        setUploading(true);
        try {
          const result = await api.journalUploadImage(file);
          setImages((prev) => [...prev, result.filename]);
        } catch { addToast("Failed to upload image", "error"); }
        setUploading(false);
        return;
      }
    }
  };

  const removeImage = (idx: number) => setImages(images.filter((_, i) => i !== idx));

  // Checklist helpers
  const addCheckItem = () => setCheckItems([...checkItems, { text: "", checked: false }]);
  const updateCheckItem = (idx: number, text: string) => {
    const updated = [...checkItems];
    updated[idx] = { ...updated[idx], text };
    setCheckItems(updated);
  };
  const toggleCheckItem = (idx: number) => {
    const updated = [...checkItems];
    updated[idx] = { ...updated[idx], checked: !updated[idx].checked };
    setCheckItems(updated);
  };
  const removeCheckItem = (idx: number) => {
    if (checkItems.length <= 1) return;
    setCheckItems(checkItems.filter((_, i) => i !== idx));
  };

  const handleSave = async () => {
    if (!title.trim()) return;
    setSaving(true);
    const finalContent = isTodo ? JSON.stringify(checkItems.filter((c) => c.text.trim())) : content;
    try {
      if (isEdit && existingNote) {
        await api.journalNoteUpdate(existingNote.id, {
          title, subject, content: finalContent, note_date: noteDate, color, images,
        });
      } else {
        await api.journalNoteCreate({
          note_type: noteType, title, subject, content: finalContent, note_date: noteDate, color, images,
        });
      }
      onSaved();
    } catch { addToast("Failed to save note", "error"); setSaving(false); }
  };

  return (
    <div className="space-y-5 max-w-3xl" onPaste={handlePaste}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded ${getNoteTypeBadge(noteType)}`}>
            {getNoteTypeLabel(noteType)}
          </span>
          <h2 className="text-lg font-bold">{isEdit ? "Edit Note" : "New Note"}</h2>
        </div>
        <button onClick={onCancel} className="text-sm text-ag-text-muted hover:text-ag-text">Cancel</button>
      </div>

      {/* Date + Color row */}
      <div className="flex items-end gap-6">
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Date</label>
          <input type="date" value={noteDate} onChange={(e) => setNoteDate(e.target.value)}
            className="bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm w-48" />
        </div>
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Color Marker</label>
          <div className="flex items-center gap-1.5">
            {COLOR_OPTIONS.map((c) => (
              <button key={c.id} onClick={() => setColor(c.id)} title={c.label}
                className={`w-6 h-6 rounded-full ${getColorDot(c.id)} transition-all ${
                  color === c.id ? "ring-2 ring-offset-1 ring-offset-ag-dark ring-current scale-110" : "opacity-60 hover:opacity-100"
                }`} />
            ))}
          </div>
        </div>
      </div>

      {/* Title */}
      <div>
        <label className="block text-xs text-ag-text-muted mb-1">Title</label>
        <input type="text" value={title} onChange={(e) => setTitle(e.target.value)}
          placeholder={isTodo ? "TODO list title" : "What is this about?"}
          className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm" />
      </div>

      {/* Subject */}
      <div>
        <label className="block text-xs text-ag-text-muted mb-1">Subject (short summary)</label>
        <input type="text" value={subject} onChange={(e) => setSubject(e.target.value)}
          placeholder="A few words..."
          className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm" />
      </div>

      {/* Content — different for TODO vs others */}
      {isTodo ? (
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-ag-text-muted">Checklist</label>
            <button onClick={loadDefaultTemplate}
              className="text-[10px] text-orange-400 hover:underline flex items-center gap-1">
              <FileDown size={10} /> Load Default
            </button>
          </div>
          <div className="space-y-1.5">
            {checkItems.map((item, i) => (
              <div key={i} className="flex items-center gap-2 group">
                <GripVertical size={14} className="text-ag-text-muted opacity-0 group-hover:opacity-50 shrink-0" />
                <button onClick={() => toggleCheckItem(i)}
                  className={`w-5 h-5 rounded border-2 shrink-0 flex items-center justify-center transition-colors ${
                    item.checked
                      ? "bg-orange-500 border-orange-500 text-white"
                      : "border-ag-border hover:border-orange-400"
                  }`}>
                  {item.checked && <span className="text-xs">&#10003;</span>}
                </button>
                <input type="text" value={item.text}
                  onChange={(e) => updateCheckItem(i, e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCheckItem(); } }}
                  placeholder={`Task ${i + 1}`}
                  className={`flex-1 bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm ${
                    item.checked ? "line-through text-ag-text-muted" : ""
                  }`} />
                <button onClick={() => removeCheckItem(i)}
                  className="text-ag-text-muted hover:text-ag-red opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
            <button onClick={addCheckItem}
              className="flex items-center gap-1.5 text-xs text-orange-400 hover:text-orange-300 mt-2 ml-7">
              <Plus size={12} /> Add item
            </button>
          </div>
        </div>
      ) : (
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-ag-text-muted">Content</label>
            <div className="relative">
              <button onClick={() => setShowTemplatePicker(!showTemplatePicker)}
                className="text-[10px] text-ag-accent hover:underline flex items-center gap-1">
                <FileDown size={10} /> Load Template <ChevronDown size={8} />
              </button>
              {showTemplatePicker && (
                <div className="absolute right-0 top-full mt-1 w-56 bg-ag-card border border-ag-border rounded-lg shadow-xl z-20 py-1 max-h-60 overflow-y-auto">
                  <button onClick={loadDefaultTemplate}
                    className="w-full text-left px-3 py-1.5 text-xs hover:bg-ag-card-hover text-ag-text-muted">
                    Default ({getNoteTypeLabel(noteType)})
                  </button>
                  {customTemplates.length > 0 && (
                    <div className="border-t border-ag-border my-1" />
                  )}
                  {customTemplates.map((tpl) => (
                    <button key={tpl.id} onClick={() => loadCustomTemplate(tpl)}
                      className="w-full text-left px-3 py-1.5 text-xs hover:bg-ag-card-hover truncate">
                      {tpl.name}
                    </button>
                  ))}
                  {customTemplates.length === 0 && (
                    <p className="px-3 py-1.5 text-[10px] text-ag-text-muted italic">
                      No custom templates yet
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>
          <textarea value={content} onChange={(e) => setContent(e.target.value)}
            rows={12} placeholder="Write your thoughts..."
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm font-mono resize-y" />
        </div>
      )}

      {/* Images */}
      <div>
        <label className="block text-xs text-ag-text-muted mb-1">Images</label>
        <div className="flex flex-wrap gap-2 mb-2">
          {images.map((img, i) => (
            <div key={i} className="relative group">
              <img src={`/api/journal-notes/images/${img}`} alt=""
                className="h-16 w-16 object-cover rounded-lg border border-ag-border" />
              <button onClick={() => removeImage(i)}
                className="absolute -top-1 -right-1 bg-ag-red text-white rounded-full p-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                <X size={10} />
              </button>
            </div>
          ))}
        </div>
        <label className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-ag-border rounded-lg text-xs text-ag-text-muted hover:text-ag-text hover:bg-ag-card-hover cursor-pointer">
          <Upload size={12} /> {uploading ? "Uploading..." : "Upload Image"}
          <input type="file" accept="image/*" onChange={handleUpload} className="hidden" />
        </label>
      </div>

      {/* Actions */}
      <div className="flex gap-3">
        <button onClick={handleSave} disabled={saving || !title.trim()}
          className="px-6 py-2 bg-ag-accent text-white rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
          {saving ? "Saving..." : isEdit ? "Update" : "Save"}
        </button>
        <button onClick={onCancel}
          className="px-4 py-2 border border-ag-border rounded-lg text-sm text-ag-text-muted hover:text-ag-text">
          Cancel
        </button>
      </div>
    </div>
  );
}
