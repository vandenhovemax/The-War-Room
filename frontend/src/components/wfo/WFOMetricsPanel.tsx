"use client";

interface Props {
  metrics: Record<string, number>;
  title?: string;
}

interface MetricDef {
  key: string;
  label: string;
  format: "dollar" | "pct" | "ratio" | "int" | "days";
}

const METRIC_GROUPS: { title: string; metrics: MetricDef[] }[] = [
  {
    title: "Core",
    metrics: [
      { key: "net_profit", label: "Net Profit", format: "dollar" },
      { key: "profit_factor", label: "Profit Factor", format: "ratio" },
      { key: "win_rate", label: "Win Rate", format: "pct" },
      { key: "total_positions", label: "Total Positions", format: "int" },
      { key: "avg_trade", label: "Avg Trade", format: "dollar" },
      { key: "expectancy", label: "Expectancy", format: "dollar" },
    ],
  },
  {
    title: "Drawdown",
    metrics: [
      { key: "max_drawdown", label: "Max Drawdown", format: "dollar" },
      { key: "longest_dd_days", label: "Longest DD (days)", format: "days" },
      { key: "recovery_factor", label: "Recovery Factor", format: "ratio" },
      { key: "return_dd_ratio", label: "Return/DD Ratio", format: "ratio" },
    ],
  },
  {
    title: "Risk-Adjusted",
    metrics: [
      { key: "sharpe", label: "Sharpe", format: "ratio" },
      { key: "sortino", label: "Sortino", format: "ratio" },
      { key: "calmar", label: "Calmar", format: "ratio" },
      { key: "stability", label: "Stability (R²)", format: "ratio" },
    ],
  },
  {
    title: "Win/Loss",
    metrics: [
      { key: "n_wins", label: "Wins", format: "int" },
      { key: "n_losses", label: "Losses", format: "int" },
      { key: "avg_win", label: "Avg Win", format: "dollar" },
      { key: "avg_loss", label: "Avg Loss", format: "dollar" },
      { key: "payoff_ratio", label: "Payoff Ratio", format: "ratio" },
      { key: "max_consec_wins", label: "Max Consec Wins", format: "int" },
      { key: "max_consec_losses", label: "Max Consec Losses", format: "int" },
      { key: "gross_profit", label: "Gross Profit", format: "dollar" },
      { key: "gross_loss", label: "Gross Loss", format: "dollar" },
    ],
  },
];

function formatValue(v: number | undefined, format: string): string {
  if (v === undefined || v === null) return "—";
  switch (format) {
    case "dollar":
      return `$${v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
    case "pct":
      return `${(v * 100).toFixed(1)}%`;
    case "ratio":
      return v.toFixed(2);
    case "int":
      return Math.round(v).toLocaleString();
    case "days":
      return `${Math.round(v)}d`;
    default:
      return String(v);
  }
}

function valueColor(v: number | undefined, format: string): string {
  if (v === undefined || v === null) return "";
  if (format === "dollar" || format === "ratio") {
    return v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";
  }
  return "";
}

export default function WFOMetricsPanel({ metrics, title = "Metrics" }: Props) {
  if (!metrics || Object.keys(metrics).length === 0) return null;

  // Fallback: use total_trades if total_positions not computed yet (old saved runs)
  if (metrics.total_positions == null && metrics.total_trades != null) {
    metrics = { ...metrics, total_positions: metrics.total_trades };
  }

  return (
    <div className="bg-ag-dark/50 border border-ag-border/50 rounded-lg p-3">
      <h4 className="text-xs font-medium text-ag-text mb-2">{title}</h4>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {METRIC_GROUPS.map((group) => (
          <div key={group.title}>
            <p className="text-[10px] text-ag-text-muted font-medium mb-1 uppercase tracking-wider">
              {group.title}
            </p>
            <div className="space-y-0.5">
              {group.metrics.map((m) => {
                const val = metrics[m.key];
                return (
                  <div key={m.key} className="flex justify-between text-[11px]">
                    <span className="text-ag-text-muted">{m.label}</span>
                    <span className={`font-mono ${valueColor(val, m.format)}`}>
                      {formatValue(val, m.format)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
