"use client";

export type DirectionValue = "long" | "short" | "both" | "long_plus_short";

interface DirectionFilterProps {
  value: DirectionValue;
  onChange: (v: DirectionValue) => void;
}

const options: { value: DirectionValue; label: string }[] = [
  { value: "long", label: "Long Only" },
  { value: "short", label: "Short Only" },
  { value: "both", label: "Both" },
  { value: "long_plus_short", label: "Long + Short" },
];

const activeStyle: Record<DirectionValue, string> = {
  long: "bg-ag-green/20 text-ag-green",
  short: "bg-ag-red/20 text-ag-red",
  both: "bg-ag-accent/20 text-ag-accent",
  long_plus_short: "bg-purple-500/20 text-purple-400",
};

export default function DirectionFilter({ value, onChange }: DirectionFilterProps) {
  return (
    <div className="flex rounded-lg overflow-hidden border border-ag-border">
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            value === opt.value
              ? activeStyle[opt.value]
              : "text-ag-text-muted hover:bg-ag-card-hover"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
