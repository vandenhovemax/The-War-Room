/**
 * Infer visual groups from parameter names using keyword/prefix matching.
 * First matching group wins; unmatched params fall into "General".
 */

export interface ParamGroup {
  id: string;
  label: string;
  patterns: RegExp[];
}

// Ordered — first match wins.
// Stop Loss / Take Profit / Trailing Stop / Trade Entry come BEFORE
// Pattern Detection so that names like "Stop Loss Anchor", "Cancel pending
// if low < bowl_low", "TP1 % of Position" don't get falsely pulled into
// Pattern Detection by their substrings ("Anchor", "bowl_low", "TP1").
export const PARAM_GROUPS: ParamGroup[] = [
  // Stop Loss (static): SL Mode + buffer + ATR sizing for the static SL.
  {
    id: "stop_loss",
    label: "Stop Loss",
    patterns: [/^SL Mode|^SL Buffer|^SL ATR|Stop Loss Anchor|SL ATR Buffer|^SL Distance/i],
  },
  {
    id: "tp",
    label: "Take Profit",
    patterns: [/^TP\d|Take Profit|TP\d Mode|TP\d Target|^TP |R-Multiple|Fib Extension/i],
  },
  {
    id: "tsl",
    label: "Trailing Stop",
    patterns: [/^TSL|Trailing Stop|^Trail|Pivot Lookback|Use TSL/i],
  },
  {
    id: "entry",
    label: "Trade Entry",
    patterns: [
      /Trade Direction|Risk Mode|Risk Amount|Risk %|Max Concurrent|Skip Entry|Skip 1st|Only Skip|Skip Lookback|Min SL|Break Mode|^Entry Mode|Pullback|Entry Timing|Wait Bars|Cancel pending/i,
    ],
  },
  // Pattern Detection: bowl / curve / pivot / drop-filter / anchor / HL chain.
  // Catches Rounding Bottom + HL Break Long pattern params under one roof.
  {
    id: "pattern_detection",
    label: "Pattern Detection",
    patterns: [
      /Bowl|Curve|Fit Curve|Push at End|Cooldown|Anchor|Drop|Reference High|Prior High Lookback|^Pivot Source|^Pivot Length|at Start|Higher Lows Required|Max Bars Between|HL1|HL2/i,
    ],
  },
  // Structure Detection: Market Structure pivot logic (HH/HL/LL/LH).
  // \bLL\b / \bHH\b uses word boundaries so "smaLLest" and similar don't
  // false-match (was pulling "Smallest Bowl" out of Pattern Detection).
  {
    id: "structure",
    label: "Structure Detection",
    patterns: [/\bHH\b|\bLL\b|Higher High|Lower Low|Higher Low|Retracement|Min Bars Between/i],
  },
  {
    id: "range",
    label: "Range Filter",
    patterns: [/Range|Oversized Leg|Reduced Retrace/i],
  },
  {
    id: "volatility",
    label: "Volatility Filter",
    patterns: [/Volatility|ATR Ceiling/i],
  },
  {
    id: "martingale",
    label: "Martingale",
    patterns: [/Martingale/i],
  },
  {
    id: "volume",
    label: "Volume Filter",
    patterns: [/Volume|Vol Confirm|Vol Mult|Min Fib Depth|Max Fib Depth|Candle Direction|Require Deepest/i],
  },
  {
    id: "candle",
    label: "Candlestick Pattern",
    patterns: [/Candlestick Pattern|Candle Pattern/i],
  },
  {
    id: "ema",
    label: "EMA Filter",
    patterns: [/EMA/i],
  },
  {
    id: "rsi",
    label: "RSI Filter",
    patterns: [/RSI/i],
  },
  {
    id: "poc",
    label: "POC Filter",
    patterns: [/POC/i],
  },
  {
    id: "time",
    label: "Time Filter",
    patterns: [/Time Filter|Timezone|^Monday$|^Tuesday$|^Wednesday$|^Thursday$|^Friday$|^Saturday$|^Sunday$|Start Hour|End Hour|Start Min|End Min/i],
  },
  {
    id: "price_ma",
    label: "Price MA Filter",
    patterns: [/Price MA/i],
  },
  {
    id: "ma_trend",
    label: "MA Trend Filter",
    patterns: [/MA Filter|MA Trend|Above\/Below|Max Body Crosses/i],
  },
];

export interface GroupedParams<T> {
  id: string;
  label: string;
  params: T[];
}

export function groupParameters<T extends { name: string }>(
  params: T[],
): GroupedParams<T>[] {
  const grouped = new Map<string, T[]>();
  const groupOrder: string[] = [];

  for (const param of params) {
    const match = PARAM_GROUPS.find((g) =>
      g.patterns.some((p) => p.test(param.name)),
    );
    const key = match?.id ?? "__general__";
    if (!grouped.has(key)) {
      grouped.set(key, []);
      groupOrder.push(key);
    }
    grouped.get(key)!.push(param);
  }

  return groupOrder.map((key) => {
    const group = PARAM_GROUPS.find((g) => g.id === key);
    return {
      id: key,
      label: group?.label ?? "General",
      params: grouped.get(key)!,
    };
  });
}
