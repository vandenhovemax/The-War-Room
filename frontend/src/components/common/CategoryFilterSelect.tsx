"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";

interface CategoryFilterSelectProps {
  selected: string[];
  onChange: (cats: string[]) => void;
}

export default function CategoryFilterSelect({ selected, onChange }: CategoryFilterSelectProps) {
  const [available, setAvailable] = useState<string[]>([]);

  useEffect(() => {
    api.categories().then((catMap) => {
      const labels = new Set<string>();
      for (const cats of Object.values(catMap)) {
        for (const c of cats) labels.add(c);
      }
      // Sort alphabetically so the order is stable and predictable
      setAvailable([...labels].sort((a, b) => a.localeCompare(b)));
    }).catch(() => {});
  }, []);

  const toggle = (cat: string) => {
    onChange(
      selected.includes(cat)
        ? selected.filter((c) => c !== cat)
        : [...selected, cat],
    );
  };

  if (available.length === 0) return null;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-xs text-ag-text-muted font-medium">Category Filter</span>
        <button
          onClick={() => onChange([...available])}
          className="text-[10px] text-ag-accent hover:underline"
        >
          All
        </button>
        <button
          onClick={() => onChange([])}
          className="text-[10px] text-ag-text-muted hover:underline"
        >
          None
        </button>
        {selected.length > 0 && (
          <span className="text-[10px] text-ag-text-muted">
            ({selected.length} selected)
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {available.map((cat) => {
          const active = selected.includes(cat);
          return (
            <button
              key={cat}
              onClick={() => toggle(cat)}
              className={`px-2 py-0.5 text-xs rounded-full border transition-colors ${
                active
                  ? "bg-ag-accent/20 border-ag-accent text-ag-accent"
                  : "bg-ag-card border-ag-border text-ag-text-muted hover:border-ag-accent/50"
              }`}
            >
              {cat}
            </button>
          );
        })}
      </div>
    </div>
  );
}
