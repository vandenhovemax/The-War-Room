"use client";
import { useCallback } from "react";
import Link from "next/link";
import { Wallet, Wifi, WifiOff, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { usePolling } from "@/hooks/usePolling";

export default function AccountSummary() {
  const { data: status } = useApi(
    useCallback(() => api.exchangeStatus(), []),
    "exchange-status",
  );
  const connected = status?.connected ?? false;

  const balanceFetcher = useCallback(() => api.exchangeBalance(), []);
  const positionsFetcher = useCallback(() => api.exchangePositions(), []);

  const { data: balance } = usePolling(balanceFetcher, 60_000, connected);
  const { data: positions } = usePolling(positionsFetcher, 60_000, connected);

  const posCount = positions?.positions?.length ?? 0;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-lg bg-ag-accent/10 text-ag-accent">
            <Wallet size={18} />
          </div>
          <h4 className="font-semibold">Exchange Account</h4>
        </div>
        <div className="flex items-center gap-1.5">
          {connected ? (
            <>
              <Wifi size={14} className="text-ag-green" />
              <span className="text-xs text-ag-green">Connected</span>
            </>
          ) : (
            <>
              <WifiOff size={14} className="text-ag-text-muted" />
              <span className="text-xs text-ag-text-muted">Offline</span>
            </>
          )}
        </div>
      </div>

      {!connected ? (
        <div className="text-center py-4">
          <p className="text-sm text-ag-text-muted mb-3">
            Connect your Bybit API keys to see account data.
          </p>
          <Link
            href="/settings"
            className="text-xs text-ag-accent hover:underline flex items-center gap-1 justify-center"
          >
            Go to Settings <ArrowRight size={12} />
          </Link>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-ag-text-muted">Total Equity</span>
            <span className="font-semibold">
              ${balance?.total_equity?.toLocaleString() ?? "—"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-ag-text-muted">Available</span>
            <span className="font-medium text-sm">
              ${balance?.available?.toLocaleString() ?? "—"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-ag-text-muted">Open Positions</span>
            <span className="font-medium text-sm">{posCount}</span>
          </div>
          {balance?.total_equity && balance?.available ? (
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-ag-text-muted">Margin Used</span>
                <span className="text-xs text-ag-text-muted">
                  {(
                    ((balance.total_equity - balance.available) /
                      balance.total_equity) *
                    100
                  ).toFixed(1)}
                  %
                </span>
              </div>
              <div className="w-full bg-ag-bg rounded-full h-2">
                <div
                  className="bg-ag-accent h-2 rounded-full transition-all"
                  style={{
                    width: `${Math.min(
                      ((balance.total_equity - balance.available) /
                        balance.total_equity) *
                        100,
                      100,
                    )}%`,
                  }}
                />
              </div>
            </div>
          ) : null}
          {status?.testnet && (
            <div className="text-[11px] text-ag-yellow bg-ag-yellow/10 rounded px-2 py-1 text-center">
              Testnet Mode
            </div>
          )}
        </div>
      )}
    </div>
  );
}
