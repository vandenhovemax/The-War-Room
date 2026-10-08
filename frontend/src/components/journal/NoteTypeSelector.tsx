"use client";
import { NotebookPen, RotateCcw, CheckSquare } from "lucide-react";

interface Props {
  onSelect: (type: string) => void;
  onCancel: () => void;
}

export default function NoteTypeSelector({ onSelect, onCancel }: Props) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">What would you like to write?</h3>
        <button onClick={onCancel} className="text-xs text-ag-text-muted hover:text-ag-text">Cancel</button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <button onClick={() => onSelect("journal")}
          className="flex items-center gap-3 p-4 bg-ag-dark border border-ag-border rounded-lg hover:bg-ag-card-hover hover:border-ag-accent/50 transition-colors text-left">
          <div className="p-2 rounded-lg bg-ag-accent/15 text-ag-accent">
            <NotebookPen size={20} />
          </div>
          <div>
            <p className="font-medium text-sm">Journal</p>
            <p className="text-xs text-ag-text-muted">Daily notes, observations, thoughts</p>
          </div>
        </button>

        <button onClick={() => onSelect("recap")}
          className="flex items-center gap-3 p-4 bg-ag-dark border border-ag-border rounded-lg hover:bg-ag-card-hover hover:border-blue-400/50 transition-colors text-left">
          <div className="p-2 rounded-lg bg-blue-500/15 text-blue-400">
            <RotateCcw size={20} />
          </div>
          <div>
            <p className="font-medium text-sm">Recap</p>
            <p className="text-xs text-ag-text-muted">Weekly, monthly, or any period review</p>
          </div>
        </button>

        <button onClick={() => onSelect("todo")}
          className="flex items-center gap-3 p-4 bg-ag-dark border border-ag-border rounded-lg hover:bg-ag-card-hover hover:border-orange-400/50 transition-colors text-left">
          <div className="p-2 rounded-lg bg-orange-500/15 text-orange-400">
            <CheckSquare size={20} />
          </div>
          <div>
            <p className="font-medium text-sm">TODO</p>
            <p className="text-xs text-ag-text-muted">Checklist of tasks to complete</p>
          </div>
        </button>
      </div>
    </div>
  );
}
