"use client";
import { useTaskManager } from "@/contexts/TaskManager";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";

const variantStyles: Record<string, string> = {
  success: "bg-ag-card border-ag-green/40 text-ag-green",
  error: "bg-ag-card border-ag-red/40 text-ag-red",
  info: "bg-ag-card border-ag-blue/40 text-ag-blue",
};

const icons: Record<string, React.ReactNode> = {
  success: <CheckCircle2 size={16} />,
  error: <AlertCircle size={16} />,
  info: <Info size={16} />,
};

export default function ToastStack() {
  const { toasts, dismissToast } = useTaskManager();

  if (toasts.length === 0) return null;

  return (
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 max-w-sm pointer-events-none">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`pointer-events-auto flex items-start gap-3 px-4 py-3 rounded-lg shadow-lg border
            animate-[slideInRight_0.3s_ease-out] ${variantStyles[toast.variant]}`}
        >
          <span className="mt-0.5 shrink-0">{icons[toast.variant]}</span>
          <p className="text-sm flex-1 text-ag-text">{toast.message}</p>
          <button
            onClick={() => dismissToast(toast.id)}
            className="text-ag-text-muted hover:text-ag-text shrink-0"
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
