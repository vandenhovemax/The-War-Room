"use client";
import { BarChart2 } from "lucide-react";

interface Props {
  onClick: (e: React.MouseEvent) => void;
}

export default function TradeChartButton({ onClick }: Props) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onClick(e);
      }}
      className="ml-1.5 p-0.5 rounded hover:bg-ag-border/50 text-ag-text-muted hover:text-ag-accent transition-colors inline-flex items-center"
      title="View trade on chart"
    >
      <BarChart2 size={14} />
    </button>
  );
}
