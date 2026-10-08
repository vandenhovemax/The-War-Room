"use client";

import { useEffect, useRef } from "react";

interface TVMiniChartProps {
  symbol: string;
  colorTheme?: "dark" | "light";
  height?: number;
}

/**
 * Wraps a TradingView Mini Symbol Overview widget.
 * Renders an embedded chart with price, change, and mini sparkline.
 */
export default function TVMiniChart({
  symbol,
  colorTheme = "dark",
  height = 220,
}: TVMiniChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Clear previous widget
    container.innerHTML = "";

    const widgetDiv = document.createElement("div");
    widgetDiv.className = "tradingview-widget-container__widget";
    container.appendChild(widgetDiv);

    // Suppress harmless TradingView cross-origin iframe error
    const origError = console.error;
    console.error = (...args: unknown[]) => {
      const msg = typeof args[0] === "string" ? args[0] : "";
      if (msg.includes("contentWindow")) return;
      origError.apply(console, args);
    };

    const script = document.createElement("script");
    script.src =
      "https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js";
    script.async = true;
    script.textContent = JSON.stringify({
      symbol,
      width: "100%",
      height,
      locale: "en",
      dateRange: "12M",
      colorTheme,
      isTransparent: true,
      autosize: false,
      largeChartUrl: "",
    });
    container.appendChild(script);

    // Restore console.error after script loads
    script.onload = () => { console.error = origError; };
    // Also restore after a timeout in case onload doesn't fire
    const timer = setTimeout(() => { console.error = origError; }, 3000);

    return () => {
      clearTimeout(timer);
      console.error = origError;
      container.innerHTML = "";
    };
  }, [symbol, colorTheme, height]);

  return (
    <div
      ref={containerRef}
      className="tradingview-widget-container rounded-lg overflow-hidden"
    />
  );
}
