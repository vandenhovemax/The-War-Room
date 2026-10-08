"use client";
import { useState, useEffect, useRef } from "react";
import { Pencil } from "lucide-react";

const STORAGE_KEY = "optimizer_user_notes";

function loadNotes(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); }
  catch { return {}; }
}

/** Tiny personal-note icon. Hover = see note. Click = edit inline (multiline). */
export default function UserNote({ noteKey }: { noteKey: string }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { setValue(loadNotes()[noteKey] || ""); }, [noteKey]);

  // Auto-resize textarea to fit content
  useEffect(() => {
    if (ref.current) {
      ref.current.style.height = "auto";
      ref.current.style.height = Math.min(ref.current.scrollHeight, 120) + "px";
    }
  }, [value, editing]);

  const save = () => {
    const notes = loadNotes();
    if (value.trim()) notes[noteKey] = value.trim();
    else delete notes[noteKey];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(notes));
    setEditing(false);
  };

  if (editing) {
    return (
      <span className="inline-flex items-start gap-0.5 ml-0.5" onClick={(e) => e.stopPropagation()}>
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="w-40 bg-ag-dark border border-ag-accent/50 rounded px-1 py-0.5 text-[10px] text-ag-text resize-none leading-tight"
          autoFocus
          rows={2}
          placeholder="Your note... (Esc to cancel)"
          onKeyDown={(e) => {
            if (e.key === "Escape") { setValue(loadNotes()[noteKey] || ""); setEditing(false); }
          }}
          onBlur={save}
        />
      </span>
    );
  }

  const hasNote = value.trim().length > 0;
  return (
    <button
      onClick={(e) => { e.stopPropagation(); e.preventDefault(); setEditing(true); }}
      title={hasNote ? value : "Click to add a personal note"}
      className={`inline-flex items-center ml-0.5 shrink-0 ${
        hasNote ? "text-yellow-400" : "text-ag-text-muted/20 hover:text-ag-text-muted/50"
      } transition-colors`}
    >
      <Pencil size={8} />
    </button>
  );
}
