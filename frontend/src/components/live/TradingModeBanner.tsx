"use client";
import { Info } from "lucide-react";
import type { LiveEngineStatus } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  status: LiveEngineStatus | null;
}

export default function TradingModeBanner({ status }: Props) {
  if (!status) return null;

  const { paper_trading, testnet, testnet_use_mainnet_prices } = status;

  if (paper_trading) {
    return (
      <div className="flex items-center gap-3 rounded-lg px-4 py-3 bg-purple-500/10 border border-purple-500/30">
        <Info size={18} className="text-purple-400 shrink-0" />
        <div className="flex-1">
          <span className="text-sm font-semibold text-purple-400">PAPER MODE</span>
          <span className="text-sm text-ag-text-muted ml-2">
            Simulating with real mainnet prices. No money at risk.
          </span>
        </div>
        <HintTip text="Paper trading simulates order fills against live Bybit mainnet prices. You get realistic position sizing, PnL, and risk calculations without risking real funds. Ideal for strategy validation." />
      </div>
    );
  }

  if (testnet) {
    const isHybrid = testnet_use_mainnet_prices;
    return (
      <div className={`flex items-center gap-3 rounded-lg px-4 py-3 ${
        isHybrid
          ? "bg-teal-500/10 border border-teal-500/30"
          : "bg-yellow-500/10 border border-yellow-500/30"
      }`}>
        <Info size={18} className={`shrink-0 ${isHybrid ? "text-teal-400" : "text-yellow-400"}`} />
        <div className="flex-1">
          <span className={`text-sm font-semibold ${isHybrid ? "text-teal-400" : "text-yellow-400"}`}>
            {isHybrid ? "TESTNET + REAL PRICES" : "TESTNET"}
          </span>
          <span className="text-sm text-ag-text-muted ml-2">
            {isHybrid
              ? "Real mainnet prices for candles & risk. Orders execute on testnet."
              : "Testnet prices (may differ from mainnet). Order flow testing only."}
          </span>
        </div>
        <HintTip text={isHybrid
          ? "Hybrid mode: candle data and price feeds come from Bybit mainnet (real prices) so risk calculations are accurate. Orders are placed on testnet so no real money is at risk. This validates your full execution pipeline with realistic data."
          : "Pure testnet mode: all data and orders use Bybit testnet. Prices often don't match mainnet, making risk calculations unreliable. Enable 'Use Real Prices on Testnet' in Settings for accurate testing."
        } />
      </div>
    );
  }

  // Live mainnet
  return (
    <div className="flex items-center gap-3 rounded-lg px-4 py-3 bg-ag-red/10 border border-ag-red/30">
      <Info size={18} className="text-ag-red shrink-0" />
      <div className="flex-1">
        <span className="text-sm font-semibold text-ag-red">LIVE TRADING</span>
        <span className="text-sm text-ag-text-muted ml-2">
          Real money. All orders execute on Bybit mainnet.
        </span>
      </div>
      <HintTip text="Live trading mode: all orders are placed on Bybit mainnet with real funds. Double-check your risk settings before activating portfolios. The circuit breaker will stop trading if daily losses exceed your limit." />
    </div>
  );
}
