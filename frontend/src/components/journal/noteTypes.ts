/** Shared note type constants for personal journal. */

export const NOTE_TYPES = [
  { id: "journal", label: "Journal", badgeColor: "bg-ag-accent/15 text-ag-accent" },
  { id: "recap", label: "Recap", badgeColor: "bg-blue-500/15 text-blue-400" },
  { id: "todo", label: "TODO", badgeColor: "bg-orange-500/15 text-orange-400" },
] as const;

export type NoteType = (typeof NOTE_TYPES)[number]["id"];

/** Color options for visual differentiation on the calendar. */
export const COLOR_OPTIONS = [
  { id: "indigo", label: "Indigo", dot: "bg-indigo-400", ring: "ring-indigo-400/40" },
  { id: "blue", label: "Blue", dot: "bg-blue-400", ring: "ring-blue-400/40" },
  { id: "green", label: "Green", dot: "bg-emerald-400", ring: "ring-emerald-400/40" },
  { id: "amber", label: "Amber", dot: "bg-amber-400", ring: "ring-amber-400/40" },
  { id: "red", label: "Red", dot: "bg-rose-400", ring: "ring-rose-400/40" },
  { id: "purple", label: "Purple", dot: "bg-purple-400", ring: "ring-purple-400/40" },
  { id: "teal", label: "Teal", dot: "bg-teal-400", ring: "ring-teal-400/40" },
  { id: "pink", label: "Pink", dot: "bg-pink-400", ring: "ring-pink-400/40" },
  { id: "orange", label: "Orange", dot: "bg-orange-400", ring: "ring-orange-400/40" },
] as const;

export function getNoteTypeLabel(type: string): string {
  return NOTE_TYPES.find((t) => t.id === type)?.label ?? type;
}

export function getNoteTypeBadge(type: string): string {
  return NOTE_TYPES.find((t) => t.id === type)?.badgeColor ?? "bg-ag-border text-ag-text-muted";
}

export function getColorDot(color: string): string {
  return COLOR_OPTIONS.find((c) => c.id === color)?.dot ?? "bg-indigo-400";
}

export function getColorRing(color: string): string {
  return COLOR_OPTIONS.find((c) => c.id === color)?.ring ?? "ring-indigo-400/40";
}
