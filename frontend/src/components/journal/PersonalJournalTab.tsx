"use client";
import { useState, useEffect, useCallback } from "react";
import { Plus, FileText } from "lucide-react";
import { api } from "@/lib/api";
import type { NoteCalendarSummary, JournalNoteItem, CustomTemplateItem } from "@/lib/api";
import CalendarGrid from "./CalendarGrid";
import NoteTypeSelector from "./NoteTypeSelector";
import NoteEditor from "./NoteEditor";
import NoteViewer from "./NoteViewer";
import TemplateEditor from "./TemplateEditor";
import MonthlyObjectives from "./MonthlyObjectives";
import { getNoteTypeLabel, getNoteTypeBadge, getColorDot } from "./noteTypes";

type View = "calendar" | "selecting" | "editing" | "viewing" | "templates";

export default function PersonalJournalTab() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth());
  const [summary, setSummary] = useState<NoteCalendarSummary[]>([]);
  const [notes, setNotes] = useState<JournalNoteItem[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [view, setView] = useState<View>("calendar");
  const [editNote, setEditNote] = useState<JournalNoteItem | null>(null);
  const [viewNote, setViewNote] = useState<JournalNoteItem | null>(null);
  const [newNoteType, setNewNoteType] = useState<string>("journal");
  const [preloadTemplate, setPreloadTemplate] = useState<CustomTemplateItem | null>(null);

  const summaryMap = Object.fromEntries(summary.map((s) => [s.date, s]));
  const notesByDate = new Map<string, JournalNoteItem[]>();
  for (const n of notes) {
    if (!n.note_date) continue;
    const arr = notesByDate.get(n.note_date) ?? [];
    arr.push(n);
    notesByDate.set(n.note_date, arr);
  }

  const loadData = useCallback(() => {
    const m = month + 1;
    Promise.all([
      api.journalNotesCalendarSummary(year, m),
      api.journalNotesList(year, m),
    ]).then(([s, n]) => { setSummary(s); setNotes(n); }).catch(() => {});
  }, [year, month]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleMonthChange = (y: number, m: number) => {
    setYear(y); setMonth(m); setSelectedDate(null);
  };

  // Just select the date — don't auto-open the first note
  const handleDayClick = (date: string) => {
    setSelectedDate(date);
  };

  const handleTypeSelected = (type: string) => {
    setNewNoteType(type); setEditNote(null); setView("editing");
  };

  const handleUseTemplate = (template: CustomTemplateItem) => {
    setPreloadTemplate(template);
    setNewNoteType("journal");
    setEditNote(null);
    setView("editing");
  };

  const handleSaved = () => { setView("calendar"); setEditNote(null); setPreloadTemplate(null); loadData(); };
  const handleEdit = (note: JournalNoteItem) => { setEditNote(note); setNewNoteType(note.note_type); setView("editing"); };
  const handleDeleted = () => { setView("calendar"); setViewNote(null); loadData(); };

  const renderCell = (date: string) => {
    const day = summaryMap[date];
    if (!day || day.note_count === 0) return null;
    const dayNotes = notesByDate.get(date) ?? [];
    return (
      <div className="space-y-0.5 overflow-hidden">
        {dayNotes.slice(0, 2).map((n) => (
          <div key={n.id} className="flex items-center gap-1 truncate">
            <span className={`w-2 h-2 rounded-full shrink-0 ${getColorDot(n.color)}`} />
            <p className="text-[9px] text-ag-text truncate leading-tight">{n.title}</p>
          </div>
        ))}
        {day.note_count > 2 && (
          <span className="text-[8px] text-ag-text-muted">+{day.note_count - 2} more</span>
        )}
      </div>
    );
  };

  if (view === "editing") {
    return <NoteEditor noteType={newNoteType} existingNote={editNote}
      preloadTemplate={preloadTemplate}
      defaultDate={selectedDate ?? new Date().toISOString().slice(0, 10)}
      onSaved={handleSaved} onCancel={() => { setView(editNote ? "viewing" : "calendar"); setPreloadTemplate(null); }} />;
  }
  if (view === "viewing" && viewNote) {
    return <NoteViewer note={viewNote} onEdit={() => handleEdit(viewNote)}
      onDeleted={handleDeleted} onBack={() => setView("calendar")} />;
  }
  if (view === "templates") {
    return <TemplateEditor onBack={() => setView("calendar")} onUseTemplate={handleUseTemplate} />;
  }

  const selectedDayNotes = selectedDate ? (notesByDate.get(selectedDate) ?? []) : [];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Professional Journal</h2>
          <p className="text-ag-text-muted text-sm mt-0.5">Notes, recaps, and objectives</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setView("templates")}
            className="px-3 py-2 border border-ag-border rounded-lg text-sm text-ag-text-muted hover:text-ag-text hover:bg-ag-card-hover flex items-center gap-1.5">
            <FileText size={14} /> Templates
          </button>
          <button onClick={() => setView("selecting")}
            className="px-4 py-2 bg-ag-accent text-white rounded-lg text-sm font-medium hover:brightness-110 flex items-center gap-1.5">
            <Plus size={14} /> Add Message
          </button>
        </div>
      </div>

      {view === "selecting" && (
        <NoteTypeSelector onSelect={handleTypeSelected} onCancel={() => setView("calendar")} />
      )}

      <CalendarGrid year={year} month={month}
        onMonthChange={handleMonthChange} onDayClick={handleDayClick}
        renderCell={renderCell} selectedDate={selectedDate ?? undefined} />

      {/* Day notes — side by side between calendar and monthly objectives */}
      {selectedDate && selectedDayNotes.length > 0 && (
        <DayNotesList date={selectedDate} notes={selectedDayNotes}
          onView={(n) => { setViewNote(n); setView("viewing"); }} />
      )}

      {/* Monthly Objectives — scoped to the currently viewed month */}
      <MonthlyObjectives year={year} month={month} />
    </div>
  );
}

function DayNotesList({ date, notes, onView }: {
  date: string; notes: JournalNoteItem[]; onView: (n: JournalNoteItem) => void;
}) {
  const formatted = new Date(date + "T12:00:00").toLocaleDateString("en-US", {
    weekday: "long", month: "long", day: "numeric", year: "numeric",
  });
  return (
    <div className="space-y-2">
      <h3 className="font-semibold text-sm text-ag-text-muted">{formatted}</h3>
      {/* Side by side layout — wraps on smaller screens */}
      <div className="flex flex-wrap gap-3">
        {notes.map((n) => (
          <button key={n.id} onClick={() => onView(n)}
            className="flex-1 min-w-[220px] max-w-[calc(50%-6px)] text-left bg-ag-card border border-ag-border rounded-lg p-3 hover:bg-ag-card-hover hover:border-ag-accent/30 transition-colors">
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-3 h-3 rounded-full shrink-0 ${getColorDot(n.color)}`} />
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${getNoteTypeBadge(n.note_type)}`}>
                {getNoteTypeLabel(n.note_type)}
              </span>
              <span className="text-sm font-medium truncate">{n.title}</span>
            </div>
            {n.subject && <p className="text-xs text-ag-text-muted truncate ml-5">{n.subject}</p>}
          </button>
        ))}
      </div>
    </div>
  );
}
