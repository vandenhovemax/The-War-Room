"use client";
import { useState } from "react";
import { Repeat2, Layers, Settings2 } from "lucide-react";
import WFOAssetSelectionTab from "@/components/wfo/WFOAssetSelectionTab";

const TABS = [
  { id: "asset", label: "Asset Selection", icon: Layers },
  { id: "param", label: "Parameter Optimization", icon: Settings2 },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function WFOPage() {
  const [tab, setTab] = useState<TabId>("asset");

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 mb-2">
        <div className="p-2 rounded-lg bg-ag-accent/10 text-ag-accent">
          <Repeat2 size={20} />
        </div>
        <h1 className="text-lg font-bold">Walk Forward Optimisation</h1>
      </div>

      {/* Tab navigation */}
      <div className="flex gap-1 border-b border-ag-border/50">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm transition-colors border-b-2 -mb-px ${
                tab === t.id
                  ? "border-ag-accent text-ag-accent font-medium"
                  : "border-transparent text-ag-text-muted hover:text-ag-text"
              }`}>
              <Icon size={14} />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {tab === "asset" && <WFOAssetSelectionTab />}
      {tab === "param" && (
        <div className="text-center py-12">
          <Settings2 size={32} className="mx-auto text-ag-text-muted mb-3" />
          <p className="text-ag-text-muted text-sm">Parameter Optimization</p>
          <div className="mt-3 inline-block px-4 py-2 bg-ag-border/30 rounded-lg text-xs text-ag-text-muted">
            Coming Soon
          </div>
        </div>
      )}
    </div>
  );
}
