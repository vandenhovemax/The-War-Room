"use client";
import Link from "next/link";
import type { LucideIcon } from "lucide-react";

interface StatCardProps {
  label: string;
  value: string | number;
  icon: LucideIcon;
  color?: "accent" | "green" | "red" | "yellow" | "blue";
  href?: string;
}

const colorMap = {
  accent: "text-ag-accent bg-ag-accent/10",
  green: "text-ag-green bg-ag-green/10",
  red: "text-ag-red bg-ag-red/10",
  yellow: "text-ag-yellow bg-ag-yellow/10",
  blue: "text-ag-blue bg-ag-blue/10",
};

export default function StatCard({ label, value, icon: Icon, color = "accent", href }: StatCardProps) {
  const card = (
    <div className={`bg-ag-card border border-ag-border rounded-xl p-5 hover:border-ag-accent/30 transition-colors ${href ? "cursor-pointer" : ""}`}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-ag-text-muted">{label}</span>
        <div className={`p-2 rounded-lg ${colorMap[color]}`}>
          <Icon size={18} />
        </div>
      </div>
      <p className="text-2xl font-bold">{value}</p>
    </div>
  );

  if (href) {
    return <Link href={href}>{card}</Link>;
  }
  return card;
}
