"use client";
import { useState, useEffect, useRef } from "react";
import { ChevronDown, ChevronRight, Code2, SlidersHorizontal, FolderOpen, Pencil, Save, X } from "lucide-react";
import { api, ParameterDefinition } from "@/lib/api";
import { useTaskManager } from "@/contexts/TaskManager";
import ConfigManager from "./ConfigManager";

interface Props {
  strategyId: number;
  onClose: () => void;
}

export default function StrategyDetailView({ strategyId, onClose }: Props) {
  const { addToast } = useTaskManager();
  const configRef = useRef<HTMLDivElement>(null);

  const [detailPine, setDetailPine] = useState("");
  const [detailPython, setDetailPython] = useState("");
  const [detailParams, setDetailParams] = useState<ParameterDefinition[]>([]);

  // All sections collapsed by default
  const [showCode, setShowCode] = useState(false);
  const [showParams, setShowParams] = useState(false);
  const [showConfigs, setShowConfigs] = useState(false);

  // Python source edit mode
  const [editingPython, setEditingPython] = useState(false);
  const [pythonDraft, setPythonDraft] = useState("");
  const [savingPython, setSavingPython] = useState(false);

  const reload = async () => {
    try {
      const [detail, params] = await Promise.all([
        api.strategyDetail(strategyId),
        api.strategyParams(strategyId),
      ]);
      setDetailPine(detail.pine_source);
      setDetailPython(detail.python_source);
      setDetailParams(params);
    } catch {
      addToast("Failed to load strategy details", "error");
    }
  };

  useEffect(() => { reload(); }, [strategyId]); // eslint-disable-line react-hooks/exhaustive-deps

  const startEditPython = () => {
    setPythonDraft(detailPython);
    setEditingPython(true);
    setShowCode(true);  // make sure code section is open
  };

  const cancelEditPython = () => {
    setEditingPython(false);
    setPythonDraft("");
  };

  const savePython = async () => {
    if (!pythonDraft.trim()) {
      addToast("Python source can't be empty", "error");
      return;
    }
    setSavingPython(true);
    try {
      const r = await api.updateStrategySource(strategyId, pythonDraft, true);
      addToast(`Saved — ${r.param_count} parameters extracted`, "success");
      setEditingPython(false);
      setPythonDraft("");
      await reload();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Save failed";
      addToast(`Failed to save: ${msg}`, "error");
    } finally {
      setSavingPython(false);
    }
  };

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-6 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Strategy Detail</h2>
        <button
          onClick={onClose}
          className="text-sm text-ag-text-muted hover:text-ag-text"
        >
          Close
        </button>
      </div>

      {/* Strategy Code — accordion, closed by default */}
      <div className="flex items-center justify-between gap-2">
        <button
          onClick={() => setShowCode(!showCode)}
          className="flex items-center gap-2 flex-1 text-left py-2 px-3 rounded-lg hover:bg-ag-dark transition-colors"
        >
          {showCode ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <Code2 size={14} className="text-ag-accent" />
          <span className="text-sm font-medium">Strategy Code</span>
          <span className="text-xs text-ag-text-muted">
            {detailPine ? "Pine + Python" : detailPython ? "Python" : ""}
          </span>
        </button>
        {!editingPython ? (
          <button
            onClick={startEditPython}
            className="flex items-center gap-1 text-xs text-ag-text-muted hover:text-ag-accent px-3 py-1.5 rounded-lg hover:bg-ag-dark transition-colors"
            title="Edit Python source — re-extracts parameters on save"
          >
            <Pencil size={12} />
            Edit Python
          </button>
        ) : (
          <div className="flex items-center gap-1">
            <button
              onClick={savePython}
              disabled={savingPython}
              className="flex items-center gap-1 text-xs text-white bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-3 py-1.5 rounded-lg transition-colors"
            >
              <Save size={12} />
              {savingPython ? "Saving..." : "Save"}
            </button>
            <button
              onClick={cancelEditPython}
              disabled={savingPython}
              className="flex items-center gap-1 text-xs text-ag-text-muted hover:text-ag-text px-3 py-1.5 rounded-lg hover:bg-ag-dark transition-colors disabled:opacity-50"
            >
              <X size={12} />
              Cancel
            </button>
          </div>
        )}
      </div>
      {showCode && (
        <div className="grid grid-cols-2 gap-4 pl-3">
          <div>
            <h3 className="text-sm text-ag-text-muted mb-2">PineScript v6</h3>
            <pre className="bg-ag-dark border border-ag-border rounded-lg p-4 text-sm font-mono overflow-auto max-h-96 text-ag-text-muted whitespace-pre-wrap">
              {detailPine || "No PineScript source"}
            </pre>
          </div>
          <div>
            <h3 className="text-sm text-ag-text-muted mb-2">
              Python Source
              {editingPython && <span className="ml-2 text-amber-400">(editing — Save replaces source + re-extracts params)</span>}
            </h3>
            {editingPython ? (
              <textarea
                value={pythonDraft}
                onChange={(e) => setPythonDraft(e.target.value)}
                disabled={savingPython}
                spellCheck={false}
                className="w-full bg-ag-dark border border-ag-accent/40 focus:border-ag-accent rounded-lg p-4 text-sm font-mono outline-none h-96 resize-y disabled:opacity-50"
                placeholder="Paste your Python strategy source here..."
              />
            ) : (
              <pre className="bg-ag-dark border border-ag-border rounded-lg p-4 text-sm font-mono overflow-auto max-h-96 text-ag-text-muted whitespace-pre-wrap">
                {detailPython || "No Python source"}
              </pre>
            )}
          </div>
        </div>
      )}

      {/* Parameters — accordion, closed by default */}
      {detailParams.length > 0 && (
        <>
          <button
            onClick={() => setShowParams(!showParams)}
            className="flex items-center gap-2 w-full text-left py-2 px-3 rounded-lg hover:bg-ag-dark transition-colors"
          >
            {showParams ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <SlidersHorizontal size={14} className="text-purple-400" />
            <span className="text-sm font-medium">Parameters</span>
            <span className="text-xs text-ag-text-muted">({detailParams.length})</span>
          </button>
          {showParams && (
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2 pl-3">
              {detailParams.map((p) => (
                <div key={p.name} className="bg-ag-dark border border-ag-border rounded-lg px-3 py-2">
                  <div className="text-[10px] text-ag-text-muted uppercase">{p.name}</div>
                  <div className="text-sm font-medium">{String(p.default)}</div>
                  <div className="text-[10px] text-ag-text-muted">
                    {p.type}{p.min !== undefined ? ` · ${p.min}–${p.max}` : ""}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* Saved Configs — accordion, closed by default */}
      {detailParams.length > 0 && (
        <>
          <button
            onClick={() => setShowConfigs(!showConfigs)}
            className="flex items-center gap-2 w-full text-left py-2 px-3 rounded-lg hover:bg-ag-dark transition-colors"
          >
            {showConfigs ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <FolderOpen size={14} className="text-amber-400" />
            <span className="text-sm font-medium">Saved Configs</span>
          </button>
          {showConfigs && (
            <div ref={configRef} className="pl-3">
              <ConfigManager
                strategyId={strategyId}
                definitions={detailParams}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
