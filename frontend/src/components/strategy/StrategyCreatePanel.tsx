"use client";
import { useState } from "react";
import { Sparkles, Copy, CheckCircle2, AlertCircle, Save, FileCode2 } from "lucide-react";
import { api, ParameterDefinition } from "@/lib/api";
import { generateClaudePrompt } from "@/lib/claude-prompt";

interface Props {
  mode: "ai" | "paste";
  onSaved: () => void;
  onClose: () => void;
}

export default function StrategyCreatePanel({ mode, onSaved, onClose }: Props) {
  if (mode === "ai") return <AIBuilder onSaved={onSaved} onClose={onClose} />;
  return <PastePanel onSaved={onSaved} onClose={onClose} />;
}

/* ── AI Builder ───────────────────────────────────────────── */

function AIBuilder({ onSaved, onClose }: { onSaved: () => void; onClose: () => void }) {
  const [step, setStep] = useState<"paste" | "prompt" | "save">("paste");
  const [pine, setPine] = useState("");
  const [prompt, setPrompt] = useState("");
  const [copied, setCopied] = useState(false);
  const [matchTV, setMatchTV] = useState(true);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [python, setPython] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleGenerate = () => {
    setPrompt(generateClaudePrompt(pine, matchTV));
    setStep("prompt");
    setCopied(false);
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 3000);
    } catch {
      setError("Failed to copy — try selecting all text manually");
    }
  };

  const handleSave = async () => {
    if (!name.trim() || !python.trim()) {
      setError("Name and Python code are required");
      return;
    }
    setSaving(true);
    setError("");
    try {
      let params: ParameterDefinition[] | undefined;
      const m = python.match(/PARAMETERS:\s*\n\s*(\[[\s\S]*?\])/);
      if (m) {
        try { params = JSON.parse(m[1]) as ParameterDefinition[]; } catch { /* ignore */ }
      }
      await api.createPythonStrategy(name, desc, python, pine, params);
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-ag-card border border-purple-500/30 rounded-xl p-6 space-y-4">
      <div className="flex items-center gap-2 mb-2">
        <Sparkles size={18} className="text-purple-400" />
        <h2 className="text-lg font-semibold">Build with Claude AI</h2>
        <div className="flex items-center gap-1 ml-auto text-xs text-ag-text-muted">
          <span className={`px-2 py-0.5 rounded ${step === "paste" ? "bg-purple-600 text-white" : "bg-ag-border"}`}>1. Paste</span>
          <span className={`px-2 py-0.5 rounded ${step === "prompt" ? "bg-purple-600 text-white" : "bg-ag-border"}`}>2. Copy</span>
          <span className={`px-2 py-0.5 rounded ${step === "save" ? "bg-purple-600 text-white" : "bg-ag-border"}`}>3. Save</span>
        </div>
      </div>

      {step === "paste" && (
        <>
          <p className="text-sm text-ag-text-muted">
            Paste your PineScript strategy below. A prompt will be generated that you can copy into Claude.ai — it will ask you
            clarifying questions and generate a Python strategy that works with the backtester.
          </p>
          <textarea
            value={pine}
            onChange={(e) => setPine(e.target.value)}
            placeholder={`//@version=6\nstrategy("My Strategy", overlay=true)\n\n// Paste your full PineScript here...`}
            rows={12}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm font-mono focus:border-purple-500 outline-none resize-y"
          />
          <div className="flex items-center gap-4">
            <button
              onClick={handleGenerate}
              disabled={!pine.trim()}
              className="flex items-center gap-2 bg-purple-600 hover:bg-purple-500 text-white px-5 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            >
              Generate Prompt for Claude
            </button>
            <label className="flex items-center gap-2 text-sm text-ag-text-muted cursor-pointer select-none">
              <input
                type="checkbox"
                checked={matchTV}
                onChange={(e) => setMatchTV(e.target.checked)}
                className="rounded border-ag-border accent-purple-500"
              />
              <span className="text-purple-400 font-medium">Match TV</span>
              <span className="text-xs">(include smoothing method reference)</span>
            </label>
          </div>
        </>
      )}

      {step === "prompt" && (
        <>
          <p className="text-sm text-ag-text-muted">
            Copy this prompt and paste it into <strong>Claude.ai</strong>. Claude will read your strategy, ask questions, then
            generate the Python code. When done, click &quot;Next&quot; to save the result.
          </p>
          <div className="relative">
            <textarea
              value={prompt}
              readOnly
              rows={10}
              className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-xs font-mono text-ag-text-muted resize-y"
            />
            <button
              onClick={handleCopy}
              className={`absolute top-2 right-2 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                copied ? "bg-ag-green text-white" : "bg-purple-600 hover:bg-purple-500 text-white"
              }`}
            >
              {copied ? <><CheckCircle2 size={12} /> Copied!</> : <><Copy size={12} /> Copy to Clipboard</>}
            </button>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={() => setStep("paste")} className="text-ag-text-muted hover:text-ag-text px-4 py-2 text-sm transition-colors">
              Back
            </button>
            <button
              onClick={() => setStep("save")}
              className="flex items-center gap-2 bg-purple-600 hover:bg-purple-500 text-white px-5 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              Next — Save Python Code
            </button>
          </div>
        </>
      )}

      {step === "save" && (
        <>
          <p className="text-sm text-ag-text-muted">
            Paste the Python code that Claude generated below, give it a name, and save.
            If Claude included a <strong>PARAMETERS:</strong> block, paste it too — the parameters will be auto-detected.
          </p>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-ag-text-muted mb-1">Strategy Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My AI Strategy"
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm text-ag-text-muted mb-1">Description</label>
              <input
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                placeholder="Describe the strategy logic"
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-purple-500 outline-none"
              />
            </div>
          </div>
          <div>
            <label className="block text-sm text-ag-text-muted mb-1">Python Code (from Claude)</label>
            <textarea
              value={python}
              onChange={(e) => setPython(e.target.value)}
              placeholder={`from backend.backtester.base_strategy import BaseStrategy\nfrom backend.backtester.indicators import ta\n\nclass TranspiledStrategy(BaseStrategy):\n    def init(self):\n        ...\n    def next(self):\n        ...`}
              rows={14}
              className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm font-mono focus:border-purple-500 outline-none resize-y"
            />
          </div>
          <div className="flex items-center gap-3">
            <button onClick={() => setStep("prompt")} className="text-ag-text-muted hover:text-ag-text px-4 py-2 text-sm transition-colors">
              Back
            </button>
            <button
              onClick={handleSave}
              disabled={saving || !name.trim() || !python.trim()}
              className="flex items-center gap-2 bg-ag-green hover:bg-ag-green/80 text-white px-5 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            >
              <Save size={14} />
              {saving ? "Saving..." : "Save Strategy"}
            </button>
          </div>
          {error && (
            <div className="flex items-center gap-2 text-ag-red text-sm">
              <AlertCircle size={14} /> {error}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ── Paste Python ─────────────────────────────────────────── */

function PastePanel({ onSaved, onClose }: { onSaved: () => void; onClose: () => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [python, setPython] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSave = async () => {
    if (!name.trim() || !python.trim()) {
      setError("Name and Python code are required");
      return;
    }
    setSaving(true);
    setError("");
    try {
      let params: ParameterDefinition[] | undefined;
      const m = python.match(/PARAMETERS:\s*\n\s*(\[[\s\S]*?\])/);
      if (m) {
        try { params = JSON.parse(m[1]) as ParameterDefinition[]; } catch { /* ignore */ }
      }
      await api.createPythonStrategy(name, desc, python, "", params);
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-ag-card border border-emerald-500/30 rounded-xl p-6 space-y-4">
      <div className="flex items-center gap-2 mb-2">
        <FileCode2 size={18} className="text-emerald-400" />
        <h2 className="text-lg font-semibold">Paste Python Strategy</h2>
      </div>
      <p className="text-sm text-ag-text-muted">
        Paste a Python strategy class directly. The class must inherit from <code className="text-emerald-400">BaseStrategy</code> and
        implement <code className="text-emerald-400">init()</code> and <code className="text-emerald-400">next()</code> methods.
      </p>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm text-ag-text-muted mb-1">Strategy Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Market Structure Light"
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-emerald-500 outline-none"
          />
        </div>
        <div>
          <label className="block text-sm text-ag-text-muted mb-1">Description</label>
          <input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="Describe the strategy logic"
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-emerald-500 outline-none"
          />
        </div>
      </div>
      <div>
        <label className="block text-sm text-ag-text-muted mb-1">Python Code</label>
        <textarea
          value={python}
          onChange={(e) => setPython(e.target.value)}
          placeholder={`from backend.backtester.base_strategy import BaseStrategy\nfrom backend.backtester.indicators import IndicatorLibrary as ta\n\nclass TranspiledStrategy(BaseStrategy):\n    def init(self):\n        ...\n    def next(self):\n        ...`}
          rows={16}
          className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm font-mono focus:border-emerald-500 outline-none resize-y"
        />
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving || !name.trim() || !python.trim()}
          className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white px-5 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
        >
          <Save size={14} />
          {saving ? "Saving..." : "Save Strategy"}
        </button>
        <button
          onClick={onClose}
          className="text-ag-text-muted hover:text-ag-text px-4 py-2 text-sm transition-colors"
        >
          Cancel
        </button>
      </div>
      {error && (
        <div className="flex items-center gap-2 text-ag-red text-sm">
          <AlertCircle size={14} /> {error}
        </div>
      )}
    </div>
  );
}
