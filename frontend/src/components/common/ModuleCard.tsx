"use client";
import type { LucideIcon } from "lucide-react";
import Link from "next/link";

interface ModuleCardProps {
  title: string;
  description: string;
  href: string;
  icon: LucideIcon;
  phase: number;
  status: "live" | "building" | "planned";
}

const statusStyles = {
  live: { dot: "bg-ag-green", label: "Live", badge: "bg-ag-green/15 text-ag-green" },
  building: { dot: "bg-ag-yellow", label: "Building", badge: "bg-ag-yellow/15 text-ag-yellow" },
  planned: { dot: "bg-ag-text-muted", label: "Planned", badge: "bg-ag-border text-ag-text-muted" },
};

export default function ModuleCard({ title, description, href, icon: Icon, phase, status }: ModuleCardProps) {
  const s = statusStyles[status];

  return (
    <Link href={href} className="block">
      <div className="bg-ag-card border border-ag-border rounded-xl p-5 hover:border-ag-accent/30 transition-colors h-full">
        <div className="flex items-start justify-between mb-3">
          <div className="p-2.5 rounded-lg bg-ag-accent/10 text-ag-accent">
            <Icon size={22} />
          </div>
          <span className={`text-[11px] px-2 py-0.5 rounded-full ${s.badge}`}>
            {s.label}
          </span>
        </div>
        <h3 className="font-semibold mb-1">{title}</h3>
        <p className="text-sm text-ag-text-muted leading-relaxed">{description}</p>
      </div>
    </Link>
  );
}
