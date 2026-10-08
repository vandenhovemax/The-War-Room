"use client";
import { useSidebar } from "@/contexts/SidebarContext";

export default function MainContent({ children }: { children: React.ReactNode }) {
  const { collapsed } = useSidebar();
  return (
    <div
      className={`min-h-screen flex flex-col transition-[margin] duration-200 ${
        collapsed ? "ml-14" : "ml-56"
      }`}
    >
      {children}
    </div>
  );
}
