"use client";
import { useState, useCallback } from "react";
import type { MonteCarloResult, FatTailBootstrapResult, RandomExitResult } from "@/lib/api";
import RobustnessScorecard from "./RobustnessScorecard";
import MonteCarloPanel from "./MonteCarloPanel";
import FatTailBootstrapPanel from "./FatTailBootstrapPanel";
import RandomExitPanel from "./RandomExitPanel";
import { ChevronDown, ChevronRight } from "lucide-react";
import HintTip from "@/components/common/HintTip";

interface Props {
  portfolioId: number;
  dateFrom?: string;
  dateTo?: string;
  minTrades?: number;
  excludeTopN?: number;
  pfMin?: number;
  pfMax?: number;
  wrMin?: number;
}

function Section({ title, hint, open, onToggle, enabled, onToggleEnabled, children }: {
  title: string; hint: string; open: boolean; onToggle: () => void;
  enabled: boolean; onToggleEnabled: (v: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg">
      <div className="flex items-center gap-2 px-4 py-2.5 cursor-pointer select-none" onClick={onToggle}>
        <input type="checkbox" checked={enabled}
          onChange={(e) => { e.stopPropagation(); onToggleEnabled(e.target.checked); }}
          onClick={(e) => e.stopPropagation()}
          className="rounded border-ag-border bg-ag-dark" />
        {open ? <ChevronDown size={14} className="text-ag-text-muted" /> : <ChevronRight size={14} className="text-ag-text-muted" />}
        <h3 className="text-sm font-medium flex-1">{title}<HintTip text={hint} /></h3>
      </div>
      {open && <div className="px-4 pb-4">{children}</div>}
    </div>
  );
}

export default function MonteCarloTabContainer({ portfolioId, dateFrom, dateTo, minTrades, excludeTopN, pfMin, pfMax, wrMin }: Props) {
  const [mcResult, setMcResult] = useState<MonteCarloResult | null>(null);
  const [fatTailResult, setFatTailResult] = useState<FatTailBootstrapResult | null>(null);
  const [randomExitResult, setRandomExitResult] = useState<RandomExitResult | null>(null);

  // Section open/close state
  const [mcOpen, setMcOpen] = useState(true);
  const [ftOpen, setFtOpen] = useState(false);
  const [reOpen, setReOpen] = useState(false);

  // Module enabled (tick) state
  const [mcEnabled, setMcEnabled] = useState(true);
  const [ftEnabled, setFtEnabled] = useState(true);
  const [reEnabled, setReEnabled] = useState(true);

  const onMcResult = useCallback((r: MonteCarloResult) => setMcResult(r), []);
  const onFtResult = useCallback((r: FatTailBootstrapResult) => setFatTailResult(r), []);
  const onReResult = useCallback((r: RandomExitResult) => setRandomExitResult(r), []);

  return (
    <div className="space-y-4">
      {/* Scorecard — always visible */}
      <RobustnessScorecard
        mcResult={mcEnabled ? mcResult : null}
        fatTailResult={ftEnabled ? fatTailResult : null}
        randomExitResult={reEnabled ? randomExitResult : null}
      />

      {/* Module 1: Sequence Risk (existing MC) */}
      <Section title="Sequence Risk (Monte Carlo)" open={mcOpen} onToggle={() => setMcOpen(!mcOpen)}
        enabled={mcEnabled} onToggleEnabled={setMcEnabled}
        hint="Reshuffles trade order to test how drawdowns change with different sequences. Shows worst-case scenarios if trades happened in a different order.">
        <MonteCarloPanel portfolioId={portfolioId} dateFrom={dateFrom} dateTo={dateTo} minTrades={minTrades} excludeTopN={excludeTopN} pfMin={pfMin} pfMax={pfMax} wrMin={wrMin} onResult={onMcResult} />
      </Section>

      {/* Module 2: Fat-Tail Stress Test */}
      <Section title="Fat-Tail Stress Test" open={ftOpen} onToggle={() => setFtOpen(!ftOpen)}
        enabled={ftEnabled} onToggleEnabled={setFtEnabled}
        hint="Force-includes your biggest winners (Power Trades) while randomizing everything else. Tests if the strategy survives without relying on a few lucky trades.">
        <FatTailBootstrapPanel portfolioId={portfolioId} dateFrom={dateFrom} dateTo={dateTo} onResult={onFtResult} />
      </Section>

      {/* Module 3: Exit Edge Detector */}
      <Section title="Exit Edge Detector" open={reOpen} onToggle={() => setReOpen(!reOpen)}
        enabled={reEnabled} onToggleEnabled={setReEnabled}
        hint="Keeps your exact entries but randomizes all exits. Proves whether your TP/SL/TSL logic has a real statistical edge over random exits.">
        <RandomExitPanel portfolioId={portfolioId} dateFrom={dateFrom} dateTo={dateTo} onResult={onReResult} />
      </Section>
    </div>
  );
}
