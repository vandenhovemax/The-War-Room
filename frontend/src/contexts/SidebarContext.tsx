"use client";
import { createContext, useContext, useState, useEffect, type ReactNode } from "react";

interface SidebarState {
  collapsed: boolean;
  toggle: () => void;
}

const Ctx = createContext<SidebarState>({ collapsed: false, toggle: () => {} });

const STORAGE_KEY = "sidebar-collapsed";

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(localStorage.getItem(STORAGE_KEY) === "true");
  }, []);

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem(STORAGE_KEY, String(next));
  };

  return <Ctx.Provider value={{ collapsed, toggle }}>{children}</Ctx.Provider>;
}

export function useSidebar() {
  return useContext(Ctx);
}
