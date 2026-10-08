"use client";
import { useState, useRef, useEffect } from "react";

interface HintTipProps {
  text: string;
}

/**
 * Small "?" circle that shows a tooltip on hover.
 * Auto-positions to avoid overflowing the viewport.
 */
export default function HintTip({ text }: HintTipProps) {
  const [show, setShow] = useState(false);
  const tipRef = useRef<HTMLSpanElement>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<"top" | "bottom">("top");

  useEffect(() => {
    if (show && wrapRef.current) {
      const rect = wrapRef.current.getBoundingClientRect();
      // If too close to top, show below
      setPos(rect.top < 80 ? "bottom" : "top");
    }
  }, [show]);

  return (
    <span
      ref={wrapRef}
      className="relative inline-flex items-center ml-1"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <span className="w-3.5 h-3.5 rounded-full bg-ag-border text-ag-text-muted text-[9px] font-bold flex items-center justify-center cursor-help hover:bg-ag-accent hover:text-white transition-colors">
        ?
      </span>
      {show && (
        <span
          ref={tipRef}
          className={`absolute z-50 w-56 px-2.5 py-1.5 text-[11px] leading-snug text-ag-text bg-ag-dark border border-ag-border rounded-lg shadow-lg block ${
            pos === "top"
              ? "bottom-full mb-1.5 left-1/2 -translate-x-1/2"
              : "top-full mt-1.5 left-1/2 -translate-x-1/2"
          }`}
        >
          {text}
        </span>
      )}
    </span>
  );
}
