"use client";
export const dynamic = "force-dynamic";
import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { BookOpen, NotebookPen } from "lucide-react";
import TradingJournalTab from "@/components/journal/TradingJournalTab";
import PersonalJournalTab from "@/components/journal/PersonalJournalTab";

type Tab = "personal" | "trading";

const TABS: { id: Tab; label: string; icon: typeof BookOpen }[] = [
  { id: "personal", label: "Professional", icon: NotebookPen },
  { id: "trading", label: "Trading", icon: BookOpen },
];

export default function JournalPage() {
  return (
    <Suspense fallback={<div className="p-4 text-ag-text-muted text-sm">Loading…</div>}>
      <JournalPageInner />
    </Suspense>
  );
}

function JournalPageInner() {
  const searchParams = useSearchParams();
  const urlTab = searchParams.get("tab");
  const [tab, setTab] = useState<Tab>(urlTab === "trading" ? "trading" : "personal");

  // Sync with URL param changes
  useEffect(() => {
    if (urlTab === "trading" || urlTab === "personal") setTab(urlTab);
  }, [urlTab]);

  return (
    <div className="space-y-6">
      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b border-ag-border">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                active
                  ? "border-ag-accent text-ag-accent"
                  : "border-transparent text-ag-text-muted hover:text-ag-text"
              }`}>
              <Icon size={16} />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {tab === "trading" && <TradingJournalTab />}
      {tab === "personal" && <PersonalJournalTab />}
    </div>
  );
}
