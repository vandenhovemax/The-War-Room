"use client";

import HintTip from "./HintTip";

interface BatchInfo {
  id: number;
  label: string;
}

interface L2GroupEditorProps {
  batches: BatchInfo[];
  groups: number[][];
  onGroupsChange: (groups: number[][]) => void;
}

/**
 * Visual editor for building L2 strategy groups (batch pairings).
 * Each group typically pairs a Long batch with its Short counterpart.
 * L3 combines all groups into the final portfolio.
 */
export default function L2GroupEditor({ batches, groups, onGroupsChange }: L2GroupEditorProps) {
  // All batch IDs already assigned to a group
  const assigned = new Set(groups.flat());

  // Available batches for a given group (exclude assigned except own members)
  const available = (groupIdx: number) => {
    const ownMembers = new Set(groups[groupIdx] ?? []);
    return batches.filter((b) => !assigned.has(b.id) || ownMembers.has(b.id));
  };

  const addGroup = () => {
    onGroupsChange([...groups, []]);
  };

  const removeGroup = (gi: number) => {
    onGroupsChange(groups.filter((_, i) => i !== gi));
  };

  const addBatchToGroup = (gi: number, batchId: number) => {
    const updated = groups.map((g, i) => (i === gi ? [...g, batchId] : g));
    onGroupsChange(updated);
  };

  const removeBatchFromGroup = (gi: number, batchId: number) => {
    const updated = groups.map((g, i) => (i === gi ? g.filter((id) => id !== batchId) : g));
    onGroupsChange(updated);
  };

  const batchLabel = (id: number) => batches.find((b) => b.id === id)?.label ?? `#${id}`;

  return (
    <div className="space-y-2">
      <label className="text-xs text-ag-text-muted flex items-center">
        Strategy Groups (L2 Pairing)
        <HintTip text="Define which batches pair together. Each group typically pairs a Long batch with its Short counterpart. L2 finds the best asset combination within each group, then L3 combines all groups into your final portfolio." />
      </label>

      {groups.map((group, gi) => (
        <div key={gi} className="flex items-center gap-1.5 flex-wrap bg-ag-dark rounded-lg px-2.5 py-1.5 border border-ag-border">
          <span className="text-[10px] text-ag-text-muted font-medium w-14 shrink-0">
            Group {gi + 1}:
          </span>

          {/* Batch chips */}
          {group.map((bid) => (
            <span
              key={bid}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded bg-ag-accent/15 text-ag-accent border border-ag-accent/30"
            >
              {batchLabel(bid)}
              <button
                onClick={() => removeBatchFromGroup(gi, bid)}
                className="hover:text-red-400 transition-colors text-xs leading-none"
                title="Remove from group"
              >
                x
              </button>
            </span>
          ))}

          {/* Add batch dropdown */}
          <select
            className="text-[11px] bg-ag-darker border border-ag-border rounded px-1.5 py-0.5 text-ag-text-muted hover:text-ag-text"
            value=""
            onChange={(e) => {
              if (e.target.value) addBatchToGroup(gi, Number(e.target.value));
            }}
          >
            <option value="">+ Batch</option>
            {available(gi)
              .filter((b) => !group.includes(b.id))
              .map((b) => (
                <option key={b.id} value={b.id}>{b.label}</option>
              ))}
          </select>

          {/* Remove group */}
          <button
            onClick={() => removeGroup(gi)}
            className="ml-auto text-ag-text-muted hover:text-red-400 transition-colors text-xs"
            title="Delete group"
          >
            del
          </button>
        </div>
      ))}

      <button
        onClick={addGroup}
        className="w-full py-1.5 text-xs text-ag-text-muted hover:text-ag-accent border border-dashed border-ag-border hover:border-ag-accent rounded-lg transition-colors"
      >
        + Add Strategy Group
      </button>

      {/* Ungrouped notice */}
      {batches.filter((b) => !assigned.has(b.id)).length > 0 && (
        <p className="text-[10px] text-ag-text-muted italic">
          {batches.filter((b) => !assigned.has(b.id)).length} ungrouped batch(es) will auto-pass through L2 as solo groups.
        </p>
      )}
    </div>
  );
}
