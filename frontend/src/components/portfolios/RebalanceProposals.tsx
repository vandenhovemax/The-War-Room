"use client";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowRight, Check, RefreshCw, X } from "lucide-react";
import { api, type RebalanceProposal } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import LiveLoopPanel from "./LiveLoopPanel";

type StatusFilter = "pending" | "approved" | "rejected" | "all";

function statusBadge(status: string): string {
  if (status === "pending") return "bg-yellow-500/15 text-yellow-400 border-yellow-500/30";
  if (status === "approved") return "bg-ag-green/15 text-ag-green border-ag-green/30";
  if (status === "rejected") return "bg-ag-red/15 text-ag-red border-ag-red/30";
  return "bg-ag-dark border-ag-border text-ag-text-muted";
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function RebalanceProposals() {
  const [proposals, setProposals] = useState<RebalanceProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<StatusFilter>("pending");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.listRebalanceProposals(
        status === "all" ? {} : { status },
      );
      setProposals(resp.proposals);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [status]);

  const onApprove = async (p: RebalanceProposal) => {
    if (!confirm(
      `Approve proposal #${p.id} for portfolio #${p.portfolio_id}?\n\n` +
      `This will create a NEW portfolio row with ${p.proposed_basket.length} assets ` +
      `(${p.diff.added.length} added, ${p.diff.removed.length} removed). ` +
      `The old portfolio will be archived but not deleted.`
    )) return;
    setBusyId(p.id);
    try {
      const resp = await api.approveRebalanceProposal(p.id);
      alert(`Approved. New portfolio #${resp.new_portfolio_id} created.`);
      await load();
    } catch (e) {
      alert(`Approve failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  const onReject = async (p: RebalanceProposal) => {
    const reason = prompt(
      `Reject proposal #${p.id}? Enter a brief reason (optional):`,
      "",
    );
    if (reason === null) return; // cancelled
    setBusyId(p.id);
    try {
      await api.rejectRebalanceProposal(p.id, reason || undefined);
      await load();
    } catch (e) {
      alert(`Reject failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-4">
      <LiveLoopPanel />

      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-ag-text">
            Rebalance Proposals
          </h2>
          <HintTip text="Step-1 re-runs triggered at each portfolio's OOS boundary. Every proposal is pending human approval — no basket rotates until you click Approve." />
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-ag-text-muted">Filter</label>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as StatusFilter)}
            className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-sm text-ag-text"
          >
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="all">All</option>
          </select>
          <button
            onClick={load}
            className="flex items-center gap-1 bg-ag-dark border border-ag-border rounded px-2 py-1 text-sm text-ag-text hover:bg-ag-border"
            disabled={loading}
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-ag-red/10 border border-ag-red/30 rounded p-3 text-sm text-ag-red">
          <AlertCircle size={16} />
          {error}
        </div>
      )}

      {!loading && proposals.length === 0 && (
        <div className="bg-ag-dark border border-ag-border rounded p-6 text-center text-sm text-ag-text-muted">
          No {status === "all" ? "" : status} proposals.
        </div>
      )}

      <div className="space-y-3">
        {proposals.map((p) => (
          <div
            key={p.id}
            className="bg-ag-dark border border-ag-border rounded-lg p-4 space-y-3"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-ag-text">
                    Proposal #{p.id}
                  </span>
                  <span className={`text-xs px-2 py-0.5 rounded border ${statusBadge(p.status)}`}>
                    {p.status}
                  </span>
                  <span className="text-xs text-ag-text-muted">
                    {p.trigger_type === "oos_boundary" ? "OOS boundary" : "Manual"}
                  </span>
                </div>
                <div className="text-xs text-ag-text-muted">
                  Portfolio #{p.portfolio_id} · created {formatDate(p.created_at)}
                </div>
              </div>
              {p.status === "pending" && (
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => onApprove(p)}
                    disabled={busyId === p.id}
                    className="flex items-center gap-1 bg-ag-green/15 border border-ag-green/40 text-ag-green px-3 py-1 rounded text-sm hover:bg-ag-green/25 disabled:opacity-50"
                  >
                    <Check size={14} />
                    Approve
                  </button>
                  <button
                    onClick={() => onReject(p)}
                    disabled={busyId === p.id}
                    className="flex items-center gap-1 bg-ag-red/15 border border-ag-red/40 text-ag-red px-3 py-1 rounded text-sm hover:bg-ag-red/25 disabled:opacity-50"
                  >
                    <X size={14} />
                    Reject
                  </button>
                </div>
              )}
              {p.status === "approved" && p.resulting_portfolio_id && (
                <div className="text-xs text-ag-text-muted">
                  → new portfolio #{p.resulting_portfolio_id}
                </div>
              )}
            </div>

            <div className="grid grid-cols-3 gap-3 text-xs">
              <div className="bg-ag-green/5 border border-ag-green/20 rounded p-2">
                <div className="font-semibold text-ag-green mb-1">
                  Added ({p.diff.added.length})
                </div>
                <div className="text-ag-text-muted font-mono break-words">
                  {p.diff.added.length === 0 ? "—" : p.diff.added.join(", ")}
                </div>
              </div>
              <div className="bg-ag-red/5 border border-ag-red/20 rounded p-2">
                <div className="font-semibold text-ag-red mb-1">
                  Removed ({p.diff.removed.length})
                </div>
                <div className="text-ag-text-muted font-mono break-words">
                  {p.diff.removed.length === 0 ? "—" : p.diff.removed.join(", ")}
                </div>
              </div>
              <div className="bg-ag-dark border border-ag-border rounded p-2">
                <div className="font-semibold text-ag-text mb-1">
                  Unchanged ({p.diff.unchanged.length})
                </div>
                <div className="text-ag-text-muted font-mono break-words">
                  {p.diff.unchanged.length === 0 ? "—" : p.diff.unchanged.slice(0, 6).join(", ")}
                  {p.diff.unchanged.length > 6 && ` +${p.diff.unchanged.length - 6} more`}
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2 text-xs text-ag-text-muted">
              <span>{p.current_basket.length} current</span>
              <ArrowRight size={12} />
              <span>{p.proposed_basket.length} proposed</span>
              {p.reviewed_at && (
                <span className="ml-auto">
                  {p.status} by {p.reviewed_by || "user"} · {formatDate(p.reviewed_at)}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
