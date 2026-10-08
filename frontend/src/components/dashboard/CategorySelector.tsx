"use client";

interface Props {
  categories: string[];
  included: string[];
  excluded: string[];
  onChange: (included: string[], excluded: string[]) => void;
}

/**
 * Category pill selector with tri-state: neutral → included (green) → excluded (red) → neutral.
 * Excluded categories override included: if an asset belongs to both, it's excluded.
 */
export default function CategorySelector({ categories, included, excluded, onChange }: Props) {
  const cycle = (cat: string) => {
    if (included.includes(cat)) {
      // included → excluded
      onChange(included.filter((c) => c !== cat), [...excluded, cat]);
    } else if (excluded.includes(cat)) {
      // excluded → neutral
      onChange(included, excluded.filter((c) => c !== cat));
    } else {
      // neutral → included
      onChange([...included, cat], excluded);
    }
  };

  return (
    <div className="flex flex-wrap gap-1.5">
      {categories.map((cat) => {
        const isIncluded = included.includes(cat);
        const isExcluded = excluded.includes(cat);
        let cls = "px-2 py-0.5 rounded-full text-[10px] font-medium cursor-pointer transition-colors border ";
        if (isIncluded) {
          cls += "bg-ag-green/15 text-ag-green border-ag-green/40";
        } else if (isExcluded) {
          cls += "bg-red-500/15 text-red-400 border-red-500/40 line-through";
        } else {
          cls += "bg-ag-dark/50 text-ag-text-muted border-ag-border hover:border-ag-text-muted";
        }
        return (
          <button key={cat} onClick={() => cycle(cat)} className={cls}>
            {cat}
          </button>
        );
      })}
    </div>
  );
}
