"use client";
import { useCallback, useMemo } from "react";
import { TrendingUp, TrendingDown, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { MarketTicker as TickerData } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { useApi } from "@/hooks/useApi";

function formatPrice(price: number): string {
  if (price >= 1000) return price.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (price >= 1) return price.toFixed(2);
  return price.toFixed(4);
}

function formatVolume(v: number): string {
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

export default function MarketTicker() {
  const { data: watchlist } = useApi(useCallback(() => api.watchlist(), []), "watchlist");

  const symbols = useMemo(() => {
    const base = ["BTCUSDT", "ETHUSDT"];
    if (watchlist) {
      const extra = watchlist
        .map((w) => w.symbol)
        .filter((s) => !base.includes(s))
        .slice(0, 4);
      return [...base, ...extra];
    }
    return [...base, "SOLUSDT", "BNBUSDT", "XRPUSDT"];
  }, [watchlist]);

  const fetcher = useCallback(
    () => api.marketTickers(symbols.join(",")),
    [symbols],
  );

  const { data, loading, error } = usePolling(fetcher, 30_000);
  const tickers = data?.tickers ?? [];

  if (loading && tickers.length === 0) {
    return (
      <div className="flex items-center gap-2 text-ag-text-muted text-sm py-3">
        <RefreshCw size={14} className="animate-spin" />
        Loading market data...
      </div>
    );
  }

  if (error && tickers.length === 0) {
    return (
      <div className="text-ag-text-muted text-sm py-3">
        Market data unavailable
      </div>
    );
  }

  return (
    <div className="overflow-x-auto scrollbar-thin">
      <div className="flex gap-3 min-w-max py-1">
        {tickers.map((t: TickerData) => {
          const up = t.price_change_pct >= 0;
          return (
            <div
              key={t.symbol}
              className="bg-ag-card border border-ag-border rounded-lg px-4 py-3 min-w-[160px]
                         hover:border-ag-accent/30 transition-colors flex-shrink-0"
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-semibold text-ag-text-muted">
                  {t.symbol.replace("USDT", "")}
                </span>
                {up ? (
                  <TrendingUp size={14} className="text-ag-green" />
                ) : (
                  <TrendingDown size={14} className="text-ag-red" />
                )}
              </div>
              <div className="text-lg font-bold mb-1">
                ${formatPrice(t.last_price)}
              </div>
              <div className="flex items-center justify-between">
                <span
                  className={`text-xs font-medium ${
                    up ? "text-ag-green" : "text-ag-red"
                  }`}
                >
                  {up ? "+" : ""}
                  {t.price_change_pct.toFixed(2)}%
                </span>
                <span className="text-[11px] text-ag-text-muted">
                  {formatVolume(t.volume_24h)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
