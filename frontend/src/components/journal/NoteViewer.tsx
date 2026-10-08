"use client";
import { useState } from "react";
import { ArrowLeft, Pencil, Trash2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { JournalNoteItem, ChecklistItem } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import { getNoteTypeLabel, getNoteTypeBadge, getColorDot } from "./noteTypes";

interface Props {
  note: JournalNoteItem;
  onEdit: () => void;
  onDeleted: () => void;
  onBack: () => void;
}

function formatDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}

/** Try to parse TODO content as checklist JSON. */
function parseChecklist(content: string): ChecklistItem[] | null {
  try {
    const parsed = JSON.parse(content);
    if (Array.isArray(parsed) && parsed.every((item: unknown) =>
      typeof item === "object" && item !== null && "text" in item && "checked" in item
    )) {
      return parsed as ChecklistItem[];
    }
  } catch { /* not JSON */ }
  return null;
}

export default function NoteViewer({ note, onEdit, onDeleted, onBack }: Props) {
  const { addToast } = useTaskManager();
  const [confirming, setConfirming] = useState(false);
  const [expandedImg, setExpandedImg] = useState<string | null>(null);
  const images = note.images ?? [];
  const isTodo = note.note_type === "todo";
  const checklist = isTodo ? parseChecklist(note.content) : null;

  // Allow toggling checkboxes directly in the viewer
  const [checkItems, setCheckItems] = useState<ChecklistItem[]>(checklist ?? []);
  const toggleCheck = async (idx: number) => {
    const updated = [...checkItems];
    updated[idx] = { ...updated[idx], checked: !updated[idx].checked };
    setCheckItems(updated);
    try {
      await api.journalNoteUpdate(note.id, { content: JSON.stringify(updated) });
    } catch { setCheckItems(checkItems); addToast("Failed to save checklist", "error"); }
  };

  const handleDelete = async () => {
    await api.journalNoteDelete(note.id);
    onDeleted();
  };

  // Render content with markdown-style headers
  const renderContent = (text: string) => {
    return text.split("\n").map((line, i) => {
      if (line.startsWith("## ")) {
        return <h3 key={i} className="text-base font-semibold mt-4 mb-1 text-ag-text">{line.slice(3)}</h3>;
      }
      if (line.startsWith("# ")) {
        return <h2 key={i} className="text-lg font-bold mt-4 mb-1 text-ag-text">{line.slice(2)}</h2>;
      }
      if (line.trim() === "") {
        return <br key={i} />;
      }
      return <p key={i} className="text-sm text-ag-text leading-relaxed">{line}</p>;
    });
  };

  const completedCount = checkItems.filter((c) => c.checked).length;

  return (
    <div className="max-w-3xl space-y-6">
      {/* Top bar */}
      <div className="flex items-center justify-between">
        <button onClick={onBack}
          className="flex items-center gap-1.5 text-sm text-ag-text-muted hover:text-ag-text">
          <ArrowLeft size={16} /> Back to Calendar
        </button>
        <div className="flex items-center gap-2">
          <button onClick={onEdit}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-ag-border rounded-lg text-xs hover:bg-ag-card-hover">
            <Pencil size={12} /> Edit
          </button>
          {!confirming ? (
            <button onClick={() => setConfirming(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-ag-red/30 rounded-lg text-xs text-ag-red hover:bg-ag-red/10">
              <Trash2 size={12} /> Delete
            </button>
          ) : (
            <div className="flex items-center gap-1">
              <button onClick={handleDelete}
                className="px-3 py-1.5 bg-ag-red text-white rounded-lg text-xs font-medium">
                Confirm Delete
              </button>
              <button onClick={() => setConfirming(false)}
                className="px-2 py-1.5 text-xs text-ag-text-muted hover:text-ag-text">
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Note header */}
      <div className="bg-ag-card border border-ag-border rounded-xl p-6 space-y-3">
        <div className="flex items-center gap-2">
          <span className={`w-3 h-3 rounded-full ${getColorDot(note.color)}`} />
          <span className={`text-xs px-2 py-0.5 rounded font-medium ${getNoteTypeBadge(note.note_type)}`}>
            {getNoteTypeLabel(note.note_type)}
          </span>
          <span className="text-xs text-ag-text-muted">{formatDate(note.note_date)}</span>
        </div>

        <h1 className="text-2xl font-bold">{note.title}</h1>
        {note.subject && <p className="text-sm text-ag-text-muted">{note.subject}</p>}

        {note.period_start && note.period_end && (
          <p className="text-xs text-ag-text-muted">
            Period: {formatDate(note.period_start)} — {formatDate(note.period_end)}
          </p>
        )}

        {/* Progress bar for TODO notes */}
        {isTodo && checkItems.length > 0 && (
          <div className="pt-2">
            <div className="flex items-center justify-between text-xs text-ag-text-muted mb-1">
              <span>{completedCount}/{checkItems.length} completed</span>
              <span>{Math.round((completedCount / checkItems.length) * 100)}%</span>
            </div>
            <div className="h-1.5 bg-ag-dark rounded-full overflow-hidden">
              <div className="h-full bg-orange-500 rounded-full transition-all"
                style={{ width: `${(completedCount / checkItems.length) * 100}%` }} />
            </div>
          </div>
        )}
      </div>

      {/* Content — checklist for TODO, text for others */}
      <div className="bg-ag-card border border-ag-border rounded-xl p-6">
        {isTodo && checkItems.length > 0 ? (
          <div className="space-y-2">
            {checkItems.map((item, i) => (
              <button key={i} onClick={() => toggleCheck(i)}
                className="flex items-center gap-3 w-full text-left p-2 rounded-lg hover:bg-ag-dark/50 transition-colors">
                <span className={`w-5 h-5 rounded border-2 shrink-0 flex items-center justify-center transition-colors ${
                  item.checked
                    ? "bg-orange-500 border-orange-500 text-white"
                    : "border-ag-border"
                }`}>
                  {item.checked && <span className="text-xs">&#10003;</span>}
                </span>
                <span className={`text-sm ${item.checked ? "line-through text-ag-text-muted" : "text-ag-text"}`}>
                  {item.text}
                </span>
              </button>
            ))}
          </div>
        ) : note.content ? (
          renderContent(note.content)
        ) : (
          <p className="text-sm text-ag-text-muted italic">No content written yet.</p>
        )}
      </div>

      {/* Images */}
      {images.length > 0 && (
        <div className="bg-ag-card border border-ag-border rounded-xl p-6 space-y-3">
          <h3 className="text-sm font-semibold text-ag-text-muted">Attached Images</h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {images.map((img, i) => (
              <button key={i} onClick={() => setExpandedImg(img)} className="block">
                <img src={`/api/journal-notes/images/${img}`} alt=""
                  className="w-full h-32 object-cover rounded-lg border border-ag-border hover:border-ag-accent transition-colors" />
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Expanded image overlay */}
      {expandedImg && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-8"
          onClick={() => setExpandedImg(null)}>
          <button className="absolute top-4 right-4 text-white hover:text-ag-text-muted">
            <X size={24} />
          </button>
          <img src={`/api/journal-notes/images/${expandedImg}`} alt=""
            className="max-w-full max-h-full object-contain rounded-lg" />
        </div>
      )}
    </div>
  );
}
