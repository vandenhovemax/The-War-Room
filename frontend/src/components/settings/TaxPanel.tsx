"use client";
import { useState } from "react";
import { Calculator, FileText } from "lucide-react";
import TaxWithholdingPanel from "./TaxWithholdingPanel";
import TaxReportPanel from "./TaxReportPanel";

const TABS = [
  { key: "report", label: "Tax Report", icon: FileText },
  { key: "withholding", label: "Withholding Planner", icon: Calculator },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function TaxPanel() {
  const [tab, setTab] = useState<TabKey>("report");

  return (
    <div className="space-y-6">
      {/* Sub-tab navigation */}
      <div className="flex gap-1 bg-ag-dark/50 p-1 rounded-lg w-fit">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === key
                ? "bg-ag-card text-ag-text shadow-sm"
                : "text-ag-text-muted hover:text-ag-text"
            }`}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "withholding" && <TaxWithholdingPanel />}
      {tab === "report" && <TaxReportPanel />}
    </div>
  );
}
