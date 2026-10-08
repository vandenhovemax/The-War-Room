"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Database, Code2, FlaskConical,
  BarChart3, Target, Briefcase, BookOpen, Zap, Settings, Activity,
  PanelLeftClose, PanelLeftOpen,
} from "lucide-react";
import SidebarJournal from "./SidebarJournal";
import { useSidebar } from "@/contexts/SidebarContext";

type Status = "done" | "wip" | "planned";

const navItems: { href: string; label: string; icon: typeof LayoutDashboard; status: Status }[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard, status: "done" },
  { href: "/journal", label: "Journal", icon: BookOpen, status: "done" },
  { href: "/strategies", label: "Strategies", icon: Code2, status: "done" },
  { href: "/backtest", label: "Backtest", icon: FlaskConical, status: "done" },
  { href: "/optimization", label: "Optimization", icon: Target, status: "wip" },
  { href: "/analysis", label: "Analysis", icon: BarChart3, status: "done" },
  { href: "/portfolios", label: "Portfolios", icon: Briefcase, status: "done" },
  { href: "/trading", label: "Trading", icon: Zap, status: "wip" },
  { href: "/live", label: "Live Engine", icon: Activity, status: "wip" },
  { href: "/settings", label: "Settings", icon: Settings, status: "planned" },
  { href: "/data", label: "Data", icon: Database, status: "done" },
];

const statusStyle: Record<Status, string> = {
  done: "bg-ag-green/15 text-ag-green",
  wip: "bg-yellow-500/15 text-yellow-400",
  planned: "bg-ag-border text-ag-text-muted",
};

const statusIcon: Record<Status, string> = {
  done: "\u2713",
  wip: "\u25CF",
  planned: "\u2014",
};

export default function Sidebar() {
  const pathname = usePathname();
  const { collapsed, toggle } = useSidebar();

  return (
    <aside
      className={`fixed left-0 top-0 h-screen bg-ag-darker border-r border-ag-border flex flex-col z-50 transition-[width] duration-200 ${
        collapsed ? "w-14" : "w-56"
      }`}
    >
      {/* Logo */}
      <div className="p-4 border-b border-ag-border flex items-center justify-between min-h-[60px]">
        {!collapsed && (
          <div>
            <h1 className="text-lg font-bold text-ag-accent">The War Room</h1>
            <p className="text-xs text-ag-text-muted">Trading Platform</p>
          </div>
        )}
        <button
          onClick={toggle}
          className="text-ag-text-muted hover:text-ag-text transition-colors shrink-0"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-2 space-y-1 overflow-y-auto">
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              title={collapsed ? item.label : undefined}
              className={`flex items-center gap-3 rounded-lg text-sm transition-colors ${
                collapsed ? "justify-center px-2 py-2.5" : "px-3 py-2.5"
              } ${
                isActive
                  ? "bg-ag-accent/15 text-ag-accent"
                  : "text-ag-text-muted hover:bg-ag-card-hover hover:text-ag-text"
              }`}
            >
              <Icon size={18} className="shrink-0" />
              {!collapsed && (
                <>
                  <span>{item.label}</span>
                  <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded ${statusStyle[item.status]}`}>
                    {statusIcon[item.status]}
                  </span>
                </>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Quick Journal Note */}
      <SidebarJournal collapsed={collapsed} />

      {/* Footer */}
      <div className={`p-3 border-t border-ag-border ${collapsed ? "flex justify-center" : ""}`}>
        <div className="flex items-center gap-2" title={collapsed ? "Paper Trading" : undefined}>
          <div className="w-2 h-2 rounded-full bg-ag-green animate-pulse" />
          {!collapsed && <span className="text-xs text-ag-text-muted">Paper Trading</span>}
        </div>
      </div>
    </aside>
  );
}
