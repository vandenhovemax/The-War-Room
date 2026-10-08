"use client";

interface OptimizationProgressProps {
  step: number;
  totalSteps: number;
  label: string;
  isRunning: boolean;
}

const STEP_HINTS: Record<number, string> = {
  1: "Loading trades from database (or cache)",
  2: "Applying quality filters (min trades, PF, win rate)",
  3: "Computing benchmark & event scores",
  4: "Ranking and scoring seed strategies",
  5: "Filtering the candidate pool",
  6: "Building correlation matrix",
  7: "Running portfolio optimizer",
  8: "Computing final metrics and building results",
};

export default function OptimizationProgress({ step, totalSteps, label, isRunning }: OptimizationProgressProps) {
  if (!isRunning || totalSteps === 0) return null;

  const pct = Math.round((step / totalSteps) * 100);

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg px-4 py-3 space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className="text-ag-text font-medium">
          Step {step}/{totalSteps}: {label}
        </span>
        <span className="text-ag-text-muted">{pct}%</span>
      </div>
      <div className="w-full h-2 bg-ag-dark rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      {STEP_HINTS[step] && (
        <p className="text-[11px] text-ag-text-muted">{STEP_HINTS[step]}</p>
      )}
    </div>
  );
}
