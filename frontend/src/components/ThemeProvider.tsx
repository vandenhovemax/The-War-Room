"use client";
import { createContext, useContext, useEffect, useState } from "react";

type Theme = "dark" | "light";

const DARK_VARS: Record<string, string> = {
  "--color-ag-dark": "#0a0a0f",
  "--color-ag-darker": "#060609",
  "--color-ag-card": "#12121a",
  "--color-ag-card-hover": "#1a1a25",
  "--color-ag-border": "#1e1e2e",
  "--color-ag-text": "#e2e2e8",
  "--color-ag-text-muted": "#8888a0",
  "--color-ag-accent": "#6366f1",
  "--color-ag-accent-hover": "#818cf8",
  "--color-ag-green": "#22c55e",
  "--color-ag-red": "#ef4444",
  "--color-ag-yellow": "#eab308",
  "--color-ag-blue": "#3b82f6",
};

const LIGHT_VARS: Record<string, string> = {
  "--color-ag-dark": "#f5f5f7",
  "--color-ag-darker": "#eaeaef",
  "--color-ag-card": "#ffffff",
  "--color-ag-card-hover": "#f0f0f5",
  "--color-ag-border": "#d4d4de",
  "--color-ag-text": "#1a1a2e",
  "--color-ag-text-muted": "#6b6b80",
  "--color-ag-accent": "#6366f1",
  "--color-ag-accent-hover": "#4f46e5",
  "--color-ag-green": "#16a34a",
  "--color-ag-red": "#dc2626",
  "--color-ag-yellow": "#ca8a04",
  "--color-ag-blue": "#2563eb",
};

const ThemeContext = createContext<{
  theme: Theme;
  toggleTheme: () => void;
}>({
  theme: "dark",
  toggleTheme: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

function applyTheme(theme: Theme) {
  const vars = theme === "light" ? LIGHT_VARS : DARK_VARS;
  const root = document.documentElement;
  for (const [key, value] of Object.entries(vars)) {
    root.style.setProperty(key, value);
  }
}

export default function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    const saved = localStorage.getItem("ag-theme") as Theme | null;
    if (saved === "light" || saved === "dark") {
      setTheme(saved);
      applyTheme(saved);
    }
  }, []);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("ag-theme", next);
    applyTheme(next);
  };

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
