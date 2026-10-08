"use client";
import type { MonteCarloResult, FatTailBootstrapResult, RandomExitResult, RobustnessGrade } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  mcResult: MonteCarloResult | null;
  fatTailResult: FatTailBootstrapResult | null;
  randomExitResult: RandomExitResult | null;
  initialCapital?: number;
}

function gradeSequence(ddDollars: number, capital: number): RobustnessGrade {
  const pct = Math.abs(ddDollars) / capital * 100;
  if (pct < 15) return "A";
  if (pct < 25) return "B";
  if (pct < 40) return "C";
  return "F";
}

function gradeStructural(ratio: number): RobustnessGrade {
  if (ratio >= 0.85) return "A";
  if (ratio >= 0.70) return "B";
  if (ratio >= 0.50) return "C";
  return "F";
}

function gradeEdge(z: number): RobustnessGrade {
  if (z >= 3.0) return "A";
  if (z >= 2.0) return "B";
  if (z >= 1.0) return "C";
  return "F";
}

function verdict(grades: (RobustnessGrade)[]): { text: string; color: string } {
  const active = grades.filter((g): g is "A" | "B" | "C" | "F" => g !== null);
  if (active.length === 0) return { text: "Run at least one module", color: "text-ag-text-muted" };
  if (active.includes("F")) return { text: "Fragile: Performance relies too heavily on trade sequence/luck.", color: "text-ag-red" };
  const strong = active.filter(g => g === "A" || g === "B").length;
  if (strong >= 2 || active.every(g => g === "A" || g === "B"))
    return { text: "Robust: Strategy shows structural edge and survives outlier removal.", color: "text-ag-green" };
  return { text: "Marginal: Mixed signals \u2014 some resilience but not fully proven.", color: "text-ag-yellow" };
}

const GRADE_STYLE: Record<string, string> = {
  A: "text-ag-green bg-green-900/20 border-green-700/40",
  B: "text-blue-400 bg-blue-900/20 border-blue-700/40",
  C: "text-yellow-400 bg-yellow-900/20 border-yellow-700/40",
  F: "text-ag-red bg-red-900/20 border-red-700/40",
};

function GradeBadge({ label, grade, hint }: { label: string; grade: RobustnessGrade; hint: string }) {
  const style = grade ? GRADE_STYLE[grade] : "text-ag-text-muted bg-ag-card border-ag-border";
  return (
    <div className={`flex-1 rounded-lg border p-3 text-center ${style}`}>
      <p className="text-[10px] uppercase tracking-wider opacity-70 mb-1">{label}<HintTip text={hint} /></p>
      <p className="text-2xl font-bold font-mono">{grade ?? "\u2014"}</p>
    </div>
  );
}

export default function RobustnessScorecard({ mcResult, fatTailResult, randomExitResult, initialCapital = 10000 }: Props) {
  const seqGrade = mcResult ? gradeSequence(mcResult.stats.median_max_dd, initialCapital) : null;
  const structGrade = fatTailResult ? gradeStructural(fatTailResult.stats.fragility_ratio) : null;
  const edgeGrade = randomExitResult ? gradeEdge(randomExitResult.z_score) : null;
  const v = verdict([seqGrade, structGrade, edgeGrade]);

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4 space-y-3">
      <h3 className="text-sm font-semibold">Robustness Scorecard</h3>
      <div className="flex gap-3">
        <GradeBadge label="Sequence" grade={seqGrade}
          hint="Based on median max drawdown from Monte Carlo as % of initial capital. Grades: A < 15% DD, B < 25%, C < 40%, F >= 40%." />
        <GradeBadge label="Structural" grade={structGrade}
          hint="Based on Fragility Ratio from the Fat-Tail Bootstrap. Ratio = median bootstrap profit / original profit. Grades: A >= 0.85, B >= 0.70, C >= 0.50, F < 0.50." />
        <GradeBadge label="Edge" grade={edgeGrade}
          hint="Based on Z-Score from the Random Exit test. Measures how many standard deviations your exit logic outperforms random exits. Grades: A >= 3.0, B >= 2.0, C >= 1.0, F < 1.0." />
      </div>
      <p className={`text-xs font-medium ${v.color}`}>{v.text}</p>
    </div>
  );
}
