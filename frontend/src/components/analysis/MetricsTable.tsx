"use client";

interface Column {
  id: number;
  label: string;
}

interface MetricsTableProps {
  columns: Column[];
  metricKeys: string[];
  getValue: (colIdx: number, key: string) => number | string | undefined;
}

export default function MetricsTable({ columns, metricKeys, getValue }: MetricsTableProps) {
  const getBestIdx = (key: string): number => {
    const isLowerBetter = key.includes("drawdown") || key.includes("loss");
    let bestIdx = 0;
    let bestVal = Number(getValue(0, key) ?? 0);
    columns.forEach((_, i) => {
      const v = Number(getValue(i, key) ?? 0);
      if (isLowerBetter ? v < bestVal : v > bestVal) {
        bestVal = v;
        bestIdx = i;
      }
    });
    return bestIdx;
  };

  return (
    <div className="overflow-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-ag-border">
            <th className="text-left p-2 text-ag-text-muted font-normal">Metric</th>
            {columns.map((c) => (
              <th key={c.id} className="text-right p-2 text-ag-text-muted font-normal truncate max-w-[180px]" title={c.label}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {metricKeys.map((key) => {
            const bestIdx = getBestIdx(key);
            return (
              <tr key={key} className="border-b border-ag-border/50">
                <td className="p-2 text-ag-text-muted text-xs">{key.replace(/_/g, " ")}</td>
                {columns.map((c, i) => {
                  const v = getValue(i, key);
                  const num = typeof v === "number" ? v : parseFloat(String(v ?? ""));
                  return (
                    <td key={c.id}
                      className={`p-2 text-right font-mono text-xs ${i === bestIdx ? "text-ag-green font-bold" : ""}`}>
                      {isNaN(num) ? String(v ?? "\u2014") : num.toFixed(2)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
