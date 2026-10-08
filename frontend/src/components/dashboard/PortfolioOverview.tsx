"use client";
import { useCallback, useMemo } from "react";
import Link from "next/link";
import { FileText, Zap, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import type { PortfolioItem } from "@/lib/api";
import { useApi } from "@/hooks/useApi";

function PortfolioSection({
  title,
  icon: Icon,
  items,
  emptyText,
  href,
  iconColor,
}: {
  title: string;
  icon: typeof FileText;
  items: PortfolioItem[] | null;
  emptyText: string;
  href: string;
  iconColor: string;
}) {
  const count = items?.length ?? 0;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2.5">
          <div className={`p-2 rounded-lg ${iconColor}`}>
            <Icon size={18} />
          </div>
          <h4 className="font-semibold">{title}</h4>
        </div>
        <Link
          href={href}
          className="text-xs text-ag-accent hover:underline flex items-center gap-1"
        >
          View All <ArrowRight size={12} />
        </Link>
      </div>
      {count === 0 ? (
        <p className="text-sm text-ag-text-muted">{emptyText}</p>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-ag-text-muted">Active</span>
            <span className="font-semibold">{count}</span>
          </div>
          {items!.slice(0, 3).map((p) => (
            <div
              key={p.id}
              className="text-xs text-ag-text-muted truncate"
            >
              {p.name}
            </div>
          ))}
          {count > 3 && (
            <div className="text-xs text-ag-text-muted">
              +{count - 3} more
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function PortfolioOverview() {
  // Load all portfolios and filter client-side (same approach as QuickActivate)
  // This ensures paper portfolios show up consistently
  const { data: allPortfolios } = useApi(
    useCallback(() => api.portfolios(), []),
    "portfolios-all-overview",
  );

  const paper = useMemo(
    () => allPortfolios?.filter((p) => p.portfolio_type === "paper" && !p.is_hidden && !p.is_deleted) ?? null,
    [allPortfolios],
  );
  const real = useMemo(
    () => allPortfolios?.filter((p) => p.portfolio_type === "real" && !p.is_hidden && !p.is_deleted) ?? null,
    [allPortfolios],
  );

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <PortfolioSection
        title="Paper Trading"
        icon={FileText}
        items={paper}
        emptyText="No paper portfolios yet. Promote one from Saved."
        href="/portfolios"
        iconColor="bg-ag-yellow/10 text-ag-yellow"
      />
      <PortfolioSection
        title="Real Trading"
        icon={Zap}
        items={real}
        emptyText="No live portfolios. Connect exchange first."
        href="/portfolios"
        iconColor="bg-ag-green/10 text-ag-green"
      />
    </div>
  );
}
