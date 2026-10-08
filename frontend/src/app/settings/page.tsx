"use client";
import { useState, useCallback, useEffect } from "react";
import { Settings, RefreshCw, Trash2, Save, Key, Shield, Zap, BarChart3, ArrowLeftRight, Download, Upload, HardDrive, MessageCircle, Receipt } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import type { AppSettings } from "@/lib/api";
import TrashPanel from "@/components/settings/TrashPanel";
import TaxPanel from "@/components/settings/TaxPanel";

type SettingsTab = "general" | "live" | "taxes" | "trash";
type LiveSubTab = "overview" | "bybit" | "telegram";

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>("general");
  const [liveSubTab, setLiveSubTab] = useState<LiveSubTab>("overview");
  const { data: settings, loading, error, refetch } = useApi(
    useCallback(() => api.settings(), [])
  );
  const [form, setForm] = useState<Partial<AppSettings>>({});
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ type: "ok" | "warn"; text: string } | null>(null);

  useEffect(() => {
    if (settings) setForm({ ...settings });
  }, [settings]);

  const update = (key: keyof AppSettings, value: unknown) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setSaveMsg(null);
  };

  const handleSave = async () => {
    // Safety validation only applies to the Overview sub-tab (risk/engine settings)
    if (liveSubTab === "overview") {
      const gaps: string[] = [];
      if (!(form.max_position_pct ?? 0) && !(form.max_position_notional ?? 0))
        gaps.push("Position Size Cap (both Max Position % and Max Position Notional $ are 0)");
      if (!(form.risk_per_trade_usd ?? 0) && !(form.risk_per_trade_pct ?? 0))
        gaps.push("Risk Per Trade Cap (both Max Risk $ and Max Risk % are 0)");
      if (!(form.daily_loss_limit_pct ?? 0) && !(form.max_daily_loss_usd ?? 0))
        gaps.push("Daily Loss Limit (both Daily Loss % and Daily Loss Cutoff $ are 0)");
      if (gaps.length > 0) {
        setSaveMsg({ type: "warn", text: `Cannot save — missing safety protection: ${gaps.join("; ")}. Each pair needs at least one non-zero value.` });
        return;
      }
    }

    setSaving(true);
    setSaveMsg(null);
    try {
      const res = await api.updateSettings(form);
      if (res.restart_required) {
        setSaveMsg({ type: "warn", text: "Saved. Restart the engine for changes to take full effect." });
      } else {
        setSaveMsg({ type: "ok", text: "Settings saved." });
      }
      refetch();
    } catch {
      setSaveMsg({ type: "warn", text: "Failed to save settings." });
    } finally {
      setSaving(false);
    }
  };

  const tabs = [
    { id: "general" as const, label: "General", icon: <Settings size={16} /> },
    { id: "live" as const, label: "Live Trading", icon: <Zap size={16} /> },
    { id: "taxes" as const, label: "Taxes", icon: <Receipt size={16} /> },
    { id: "trash" as const, label: "Deleted Items", icon: <Trash2 size={16} /> },
  ];

  const liveSubTabs = [
    { id: "overview" as const, label: "Overview" },
    { id: "bybit" as const, label: "Bybit" },
    { id: "telegram" as const, label: "Telegram" },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="inline-flex p-2.5 rounded-xl bg-ag-accent/10 text-ag-accent">
            <Settings size={24} />
          </div>
          <div>
            <h2 className="text-xl font-bold">Settings</h2>
            <p className="text-sm text-ag-text-muted">
              API keys, risk parameters, and engine configuration
            </p>
          </div>
        </div>
        {activeTab === "live" && (
          <div className="flex items-center gap-2">
            <button onClick={refetch}
              className="flex items-center gap-2 bg-ag-card-hover border border-ag-border text-ag-text-muted hover:text-ag-text px-4 py-2 rounded-lg text-sm transition-colors">
              <RefreshCw size={16} /> Refresh
            </button>
            <button onClick={handleSave} disabled={saving}
              className="flex items-center gap-2 bg-ag-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-ag-accent/80 disabled:opacity-50">
              <Save size={16} /> {saving ? "Saving..." : "Save Changes"}
            </button>
          </div>
        )}
      </div>

      {saveMsg && (
        <div className={`rounded-lg px-4 py-2 text-sm ${
          saveMsg.type === "ok"
            ? "bg-ag-green/10 border border-ag-green/30 text-ag-green"
            : "bg-yellow-500/10 border border-yellow-500/30 text-yellow-400"
        }`}>{saveMsg.text}</div>
      )}

      {/* Tab bar */}
      <div className="flex gap-1 bg-ag-card border border-ag-border rounded-lg p-1">
        {tabs.map((tab) => (
          <button key={tab.id} onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === tab.id
                ? "bg-ag-accent text-white"
                : "text-ag-text-muted hover:text-ag-text hover:bg-ag-card-hover"
            }`}>
            {tab.icon} {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "general" && (
        <div className="space-y-6">
          <BackupSection />
          <MaintenanceSection />
        </div>
      )}

      {activeTab === "live" && (
        <div className="space-y-6">
          {/* Sub-tab bar */}
          <div className="flex gap-1 bg-ag-dark/50 border border-ag-border rounded-lg p-1">
            {liveSubTabs.map((st) => (
              <button key={st.id} onClick={() => setLiveSubTab(st.id)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  liveSubTab === st.id
                    ? "bg-ag-accent/20 text-ag-accent border border-ag-accent/30"
                    : "text-ag-text-muted hover:text-ag-text hover:bg-ag-card-hover"
                }`}>
                {st.label}
              </button>
            ))}
          </div>

          {loading && <p className="text-ag-text-muted text-sm">Loading settings...</p>}
          {error && (
            <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
              <p className="text-red-400 text-sm">Failed to load settings: {error}</p>
            </div>
          )}
          {settings && liveSubTab === "overview" && (
            <>
              <RiskSection form={form} update={update} />
              <OrderTypesSection form={form} update={update} />
              <TradingModeSection form={form} update={update} />
              <EngineSection form={form} update={update} />
            </>
          )}
          {settings && liveSubTab === "bybit" && (
            <ApiKeysSection form={form} update={update} />
          )}
          {settings && liveSubTab === "telegram" && (
            <TelegramSection form={form} update={update} />
          )}
        </div>
      )}

      {activeTab === "taxes" && <TaxPanel />}

      {activeTab === "trash" && <TrashPanel />}
    </div>
  );
}

/* ── Section Components ────────────────────────────────────────── */

interface SectionProps {
  form: Partial<AppSettings>;
  update: (key: keyof AppSettings, value: unknown) => void;
}

function SectionHeader({ icon, title, desc }: {
  icon: React.ReactNode; title: string; desc: string;
}) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <div className="p-2 rounded-lg bg-ag-accent/10 text-ag-accent">{icon}</div>
      <div>
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="text-xs text-ag-text-muted">{desc}</p>
      </div>
    </div>
  );
}

function ApiKeysSection({ form, update }: SectionProps) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <SectionHeader icon={<Key size={18} />} title="Bybit API"
        desc="Exchange credentials and network" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="API Key" hint="From Bybit API management. Leave blank for paper-only.">
          <input type="password" value={form.bybit_api_key ?? ""}
            onChange={(e) => update("bybit_api_key", e.target.value)}
            placeholder="Enter API key..."
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent focus:outline-none" />
        </Field>
        <Field label="API Secret" hint="Keep this private. Never share with anyone.">
          <input type="password" value={form.bybit_api_secret ?? ""}
            onChange={(e) => update("bybit_api_secret", e.target.value)}
            placeholder="Enter API secret..."
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent focus:outline-none" />
        </Field>
        <Toggle label="Testnet Mode"
          hint="Use Bybit testnet (fake money) instead of mainnet"
          checked={form.bybit_testnet ?? true}
          onChange={(v) => update("bybit_testnet", v)} />
        {form.bybit_testnet && (
          <Toggle label="Use Real Prices on Testnet"
            hint="Uses real mainnet prices for candles and risk calculations while orders execute on testnet. Solves testnet's wrong-price problem."
            checked={form.testnet_use_mainnet_prices ?? true}
            onChange={(v) => update("testnet_use_mainnet_prices", v)} />
        )}
      </div>
      {form.has_api_keys ? (
        <p className="text-xs text-ag-green mt-3">API keys are configured</p>
      ) : (
        <p className="text-xs text-ag-text-muted mt-3">
          No API keys set — paper trading works without them
        </p>
      )}
    </div>
  );
}

function RiskSection({ form, update }: SectionProps) {
  const isFixed = form.risk_mode === "fixed";
  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <SectionHeader icon={<Shield size={18} />} title="Risk Management"
        desc="Position sizing, risk controls, and leverage" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <Field label="Risk Mode" hint="Percentage: risk = X% of Total Equity (Reserve + Bybit Wallet Balance). Fixed: risk = flat dollar amount per trade.">
          <select value={form.risk_mode ?? "pct"}
            onChange={(e) => update("risk_mode", e.target.value)}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent focus:outline-none">
            <option value="pct">Percentage of Equity</option>
            <option value="fixed">Fixed USD Amount</option>
          </select>
        </Field>
        {!isFixed ? (
          <NumField label="Risk Per Trade (%)" hint="% of Total Equity risked per trade. Total Equity = Reserve + Bybit Wallet Balance (realized only — unrealized PnL from open trades is excluded). Only updates when a trade closes. Example: 2.5% of $10k = $250 per trade, always, regardless of open positions."
            value={form.risk_per_trade_pct} onChange={(v) => update("risk_per_trade_pct", v)}
            min={0.1} max={25} step={0.1} />
        ) : (
          <NumField label="Risk Per Trade ($)" hint="Fixed dollar amount you risk per trade, regardless of account size."
            value={form.risk_per_trade_usd} onChange={(v) => update("risk_per_trade_usd", v)}
            min={1} max={10000} step={1} />
        )}
        <NumField label="Reserve Account ($)" hint="Money you hold OFF Bybit (e.g., bank savings). Added to your Bybit Wallet Balance to calculate Total Equity. Risk % is calculated from Total Equity. Margin and leverage only use your actual Bybit balance."
          value={form.reserve_account_usd} onChange={(v) => update("reserve_account_usd", v)}
          min={0} max={10000000} step={100} />
        <div className="flex flex-col gap-1">
          <span className="text-xs text-ag-text-muted">Total Equity (calculated)</span>
          <div className="w-full bg-ag-dark/50 border border-ag-border rounded-lg px-3 py-2 text-sm text-ag-text-muted">
            ${((form.reserve_account_usd ?? 0)).toLocaleString()} (reserve) + Bybit Balance (live)
          </div>
        </div>
        <NumField label="Margin Breathing Room (%)" hint="Safety buffer on your Bybit balance. 10% means only 90% of your Bybit balance is available for margin. Example: $5k balance × 90% = $4,500 usable. This is divided equally across your Max Total Positions slots."
          value={form.margin_breathing_room_pct} onChange={(v) => update("margin_breathing_room_pct", v)}
          min={0} max={50} step={1} />
        <NumField label="Max Position Margin (% of Equity)" hint="Guardrail: caps the maximum margin any single position can use. Example: 3% of $10k Total Equity = $300 max margin per position. If a calculation error produces a larger position, this cap blocks it. This does NOT limit notional — with 50x leverage, $300 margin still allows $15k notional."
          value={form.max_position_pct} onChange={(v) => update("max_position_pct", v)}
          min={0} max={100} step={0.5} />
        <NumField label="Max Risk Per Trade ($)" hint="Safety net: hard ceiling on dollar risk per trade (qty × SL distance). If a bad SL calculation or edge case makes the risk exceed this cap, the position size is reduced to fit. Set to 0 to disable. Example: normal risk = $250, cap = $300 catches any miscalculation that tries to risk more than $300."
          value={form.max_risk_per_trade_usd} onChange={(v) => update("max_risk_per_trade_usd", v)}
          min={0} max={10000} step={10} />
        <NumField label="Max Risk Per Trade (% of Equity)" hint="Same as above but as % of total equity. Only used if the $ cap above is 0. Example: 5% of $10k = $500 max risk per trade."
          value={form.max_risk_per_trade_pct} onChange={(v) => update("max_risk_per_trade_pct", v)}
          min={0} max={50} step={0.5} />
        <Toggle label="Skip Capped Entries"
          hint="When ON: if a trade can't be filled at the full risk-based size (margin, leverage, or risk cap reduces it), the entry is skipped entirely. When OFF: the trade is taken at the reduced size. Matches backtester behaviour when ON."
          checked={form.skip_capped_entries ?? true}
          onChange={(v) => update("skip_capped_entries", v)} />
        <NumField label="Default Leverage (floor)" hint="Minimum leverage. Actual leverage is auto-calculated: leverage = position notional / margin budget per slot. If the auto-calculated leverage is lower than this floor, this value is used instead."
          value={form.default_leverage} onChange={(v) => update("default_leverage", v)}
          min={1} max={100} step={1} />
        <NumField label="Daily Loss Limit (%)" hint="Circuit breaker: % of Total Equity. When your realized daily losses reach this %, all new entries are blocked until the next trading day (midnight NYT). Set to 0 to disable (uses Daily Loss Cutoff $ only). Example: 20% of $10k = $2k daily loss triggers shutdown."
          value={form.daily_loss_limit_pct} onChange={(v) => update("daily_loss_limit_pct", v)}
          min={0} max={50} step={0.5} />
        <NumField label="Max Long Positions" hint="Maximum long positions open at the same time. Risk-free positions (SL at breakeven or better) don't count against this limit."
          value={form.max_positions_long} onChange={(v) => update("max_positions_long", v)}
          min={1} max={50} step={1} />
        <NumField label="Max Short Positions" hint="Maximum short positions open at the same time. Risk-free positions (SL at breakeven or better) don't count against this limit."
          value={form.max_positions_short} onChange={(v) => update("max_positions_short", v)}
          min={1} max={50} step={1} />
        <NumField label="Max Total Positions" hint="Hard cap on all open positions. Your Bybit balance is divided into this many equal margin slots. Example: 10 slots with $5k Bybit balance and 10% breathing = $450 margin per slot."
          value={form.max_positions_total} onChange={(v) => update("max_positions_total", v)}
          min={1} max={100} step={1} />
      </div>
    </div>
  );
}

function OrderTypesSection({ form, update }: SectionProps) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <SectionHeader icon={<ArrowLeftRight size={18} />} title="Order Types"
        desc="How entries and exits are placed on the exchange" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <Field label="Entry Order Type" hint="Market fills immediately at current price. Limit waits at a set price, potentially saving on taker fees but risking no fill.">
          <select value={form.entry_order_type ?? "Market"}
            onChange={(e) => update("entry_order_type", e.target.value)}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent focus:outline-none">
            <option value="Market">Market</option>
            <option value="Limit">Limit</option>
          </select>
        </Field>
        {form.entry_order_type === "Limit" && (
          <NumField label="Entry Limit Offset (%)" hint="How far above/below current price to set limit entries. E.g., 0.05% on a $100 asset = $0.05 better than market."
            value={form.entry_limit_offset_pct} onChange={(v) => update("entry_limit_offset_pct", v)}
            min={0} max={1} step={0.01} />
        )}
        <Field label="Take Profit Order Type" hint="Limit TPs save on taker fees (maker rebate). Market TPs guarantee the fill but cost more in fees.">
          <select value={form.tp_order_type ?? "Limit"}
            onChange={(e) => update("tp_order_type", e.target.value)}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent focus:outline-none">
            <option value="Limit">Limit</option>
            <option value="Market">Market</option>
          </select>
        </Field>
        <Field label="Stop Loss Order Type" hint="Stop losses always use market orders to guarantee execution when your SL price is hit. This cannot be changed — a missed SL is far worse than paying taker fees.">
          <div className="w-full bg-ag-dark/50 border border-ag-border rounded-lg px-3 py-2 text-sm text-ag-text-muted">
            Market (always)
          </div>
        </Field>
        <Field label="Trailing Stop Order Type" hint="Trailing stops always fire as market orders when triggered. Speed of execution is critical to lock in profits.">
          <div className="w-full bg-ag-dark/50 border border-ag-border rounded-lg px-3 py-2 text-sm text-ag-text-muted">
            Market (always)
          </div>
        </Field>
      </div>
    </div>
  );
}

function TradingModeSection({ form, update }: SectionProps) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <SectionHeader icon={<BarChart3 size={18} />} title="Trading Mode"
        desc="Paper vs live trading settings" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <Toggle label="Paper Trading"
          hint="Simulate trades with virtual money (no real orders)"
          checked={form.paper_trading ?? true}
          onChange={(v) => update("paper_trading", v)} />
        <NumField label="Paper Starting Balance ($)" hint="Virtual account balance"
          value={form.paper_initial_balance} onChange={(v) => update("paper_initial_balance", v)}
          min={100} max={1000000} step={100} />
        <NumField label="Paper Slippage (%)" hint="Simulated fill slippage"
          value={form.paper_slippage_pct} onChange={(v) => update("paper_slippage_pct", v)}
          min={0} max={1} step={0.01} />
      </div>
    </div>
  );
}

function EngineSection({ form, update }: SectionProps) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <SectionHeader icon={<Zap size={18} />} title="Engine Configuration"
        desc="Order management and safety limits" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <Toggle label="Engine Enabled"
          hint="Master kill switch for the live trading engine. When off, no strategies will execute."
          checked={form.live_engine_enabled ?? false}
          onChange={(v) => update("live_engine_enabled", v)} />
        <NumField label="Max Position Notional ($) (fallback)" hint="Hard USD cap per position. Only used if Max Position % is set to 0 in Risk Management. Set to 0 to disable."
          value={form.max_position_notional} onChange={(v) => update("max_position_notional", v)}
          min={0} max={100000} step={100} />
        <NumField label="Daily Loss Cutoff ($)" hint="Hard USD daily loss limit. When total daily losses reach this amount, circuit breaker activates and blocks all new entries."
          value={form.max_daily_loss_usd} onChange={(v) => update("max_daily_loss_usd", v)}
          min={50} max={50000} step={50} />
        <NumField label="Entry Order TTL (sec)" hint="How long to wait for a limit entry to fill before cancelling. Prevents stale orders sitting on the book."
          value={form.entry_order_ttl_seconds} onChange={(v) => update("entry_order_ttl_seconds", v)}
          min={10} max={3600} step={10} />
        <NumField label="TP Order TTL (sec)" hint="How long to wait for a limit TP to fill. If it doesn't fill in time, switches to a market close to guarantee the exit. Set to 0 to disable (TP sits until filled)."
          value={form.tp_order_ttl_seconds} onChange={(v) => update("tp_order_ttl_seconds", v)}
          min={0} max={3600} step={10} />
        <Field label="Margin Mode" hint="Isolated: each position has its own margin (safer, position-level liquidation). Cross: all positions share margin (more capital efficient but riskier).">
          <select value={form.default_margin_mode ?? "isolated"}
            onChange={(e) => update("default_margin_mode", e.target.value)}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent focus:outline-none">
            <option value="isolated">Isolated</option>
            <option value="cross">Cross</option>
          </select>
        </Field>
      </div>
    </div>
  );
}

function TelegramSection({ form, update }: SectionProps) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5">
      <SectionHeader icon={<MessageCircle size={18} />} title="Telegram Bot"
        desc="Mobile companion — monitor and control from your phone" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="Bot Token" hint="From @BotFather on Telegram. Create a bot with /newbot to get this.">
          <input type="password" value={form.telegram_bot_token ?? ""}
            onChange={(e) => update("telegram_bot_token", e.target.value)}
            placeholder="Enter bot token..."
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent focus:outline-none" />
        </Field>
        <Field label="Chat ID" hint="Your Telegram chat ID. Send a message to your bot, then visit https://api.telegram.org/bot<TOKEN>/getUpdates to find it.">
          <input type="password" value={form.telegram_chat_id ?? ""}
            onChange={(e) => update("telegram_chat_id", e.target.value)}
            placeholder="Enter chat ID..."
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent focus:outline-none" />
        </Field>
      </div>
      {form.has_telegram ? (
        <p className="text-xs text-ag-green mt-3">Telegram bot is configured</p>
      ) : (
        <p className="text-xs text-ag-text-muted mt-3">
          No bot configured — see How_to.txt for setup instructions
        </p>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-5">
        <Toggle label="Push Notifications"
          hint="Send trade alerts, engine events, and health warnings to your phone"
          checked={form.telegram_notifications_enabled ?? true}
          onChange={(v) => update("telegram_notifications_enabled", v)} />
        <NumField label="Event Poll Interval (sec)" hint="How often the bot checks for new trading events to push to your phone. Lower = faster alerts but more API calls."
          value={form.telegram_event_poll_seconds} onChange={(v) => update("telegram_event_poll_seconds", v)}
          min={5} max={300} step={5} />
        <NumField label="Health Check Interval (sec)" hint="How often the bot checks if the backend is still running. Alerts you if it goes down."
          value={form.telegram_health_check_seconds} onChange={(v) => update("telegram_health_check_seconds", v)}
          min={15} max={600} step={15} />
        <NumField label="Confirmation Timeout (sec)" hint="How long you have to type the confirmation word for dangerous commands (EMERGENCY, CLOSEALL, etc.)"
          value={form.telegram_confirm_timeout_seconds} onChange={(v) => update("telegram_confirm_timeout_seconds", v)}
          min={15} max={300} step={15} />
      </div>
    </div>
  );
}

/* ── Reusable Input Components ─────────────────────────────────── */

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-ag-text-muted mb-1">{label}</label>
      {children}
      {hint && <p className="text-[10px] text-ag-text-muted mt-1 opacity-70">{hint}</p>}
    </div>
  );
}

function NumField({ label, hint, value, onChange, min, max, step }: {
  label: string; hint?: string; value: number | undefined;
  onChange: (v: number) => void; min?: number; max?: number; step?: number;
}) {
  return (
    <Field label={label} hint={hint}>
      <input type="number" value={value ?? ""} min={min} max={max} step={step}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
        className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
    </Field>
  );
}

function Toggle({ label, hint, checked, onChange }: {
  label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <Field label={label} hint={hint}>
      <button type="button" onClick={() => onChange(!checked)}
        className={`relative w-11 h-6 rounded-full transition-colors ${
          checked ? "bg-ag-accent" : "bg-ag-border"
        }`}>
        <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
          checked ? "translate-x-5" : "translate-x-0"
        }`} />
      </button>
    </Field>
  );
}

function BackupSection() {
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const handleExport = async () => {
    setExporting(true);
    setResult(null);
    try {
      const blob = await api.backupExport();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `warroom_backup_${new Date().toISOString().slice(0, 10).replace(/-/g, "")}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setResult("Backup downloaded.");
    } catch {
      setResult("Export failed.");
    } finally {
      setExporting(false);
    }
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true);
    setResult(null);
    try {
      const res = await api.backupRestore(file);
      const parts = Object.entries(res.counts)
        .filter(([, v]) => v > 0)
        .map(([k, v]) => `${k}: ${v}`);
      setResult(parts.length > 0 ? `Restored: ${parts.join(", ")}` : "No new data to restore.");
    } catch {
      setResult("Restore failed — check file format.");
    } finally {
      setImporting(false);
      e.target.value = "";
    }
  };

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-6">
      <SectionHeader
        icon={<Download size={20} />}
        title="Backup & Restore"
        desc="Export or import categories, journal, strategies, and portfolios"
      />
      <div className="flex items-center gap-3 mt-4">
        <button onClick={handleExport} disabled={exporting}
          className="flex items-center gap-2 px-4 py-2 bg-ag-accent/20 text-ag-accent border border-ag-accent/30 rounded-lg text-sm hover:bg-ag-accent/30 disabled:opacity-50">
          <Download size={16} /> {exporting ? "Exporting..." : "Export Backup"}
        </button>
        <label className="flex items-center gap-2 px-4 py-2 bg-ag-card-hover border border-ag-border rounded-lg text-sm text-ag-text-muted hover:text-ag-text cursor-pointer">
          <Upload size={16} /> {importing ? "Importing..." : "Import Backup"}
          <input type="file" accept=".json" onChange={handleImport} className="hidden" disabled={importing} />
        </label>
      </div>
      {result && (
        <p className={`text-xs mt-2 ${result.includes("failed") ? "text-red-400" : "text-ag-green"}`}>
          {result}
        </p>
      )}
      <p className="text-[10px] text-ag-text-muted mt-3">
        Backup includes: categories, watchlist, strategies, portfolios, journal notes, trades, templates, and objectives.
        Import merges with existing data (does not overwrite).
      </p>
    </div>
  );
}

type CleanupResult = {
  before_mb: number; after_mb: number; saved_mb: number;
  deleted_trades: number; deleted_runs: number; deleted_batches: number;
  deleted_strategies: number; deleted_wfo: number; deleted_portfolios: number;
  duplicate_ohlcv_removed: number;
  vacuum_error?: string | null;
};

// Weighted steps: deletes = 0-15%, checkpoint = 15-25%, vacuum = 25-95%, complete = 100%
const STEP_WEIGHTS: Record<string, number> = {
  "Starting...": 0,
  "Deleting trades...": 3,
  "Deleting runs...": 5,
  "Deleting batches...": 7,
  "Deleting strategies...": 9,
  "Deleting WFO runs...": 11,
  "Deleting portfolios...": 13,
  "Removing OHLCV duplicates...": 15,
  "Checkpointing WAL...": 20,
  "Vacuuming main database...": 30,
  "Vacuuming OHLCV databases...": 70,
  "Complete": 100,
};

function MaintenanceSection() {
  const [sizeMb, setSizeMb] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [step, setStep] = useState("");
  const [result, setResult] = useState<CleanupResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.dbSize().then((r) => setSizeMb(r.size_mb)).catch(() => {});
  }, []);

  const handleCleanup = async () => {
    setRunning(true);
    setResult(null);
    setError(null);
    setStep("Starting...");

    try {
      const start = await api.dbCleanup();
      if (start.status === "already_running") {
        setStep("Already running...");
      }

      // Poll for progress
      const poll = setInterval(async () => {
        try {
          const prog = await api.cleanupProgress();
          setStep(prog.step);

          if (prog.done) {
            clearInterval(poll);
            setRunning(false);
            if (prog.error) {
              setError(prog.error);
            } else if (prog.result) {
              setResult(prog.result);
              setSizeMb(prog.result.after_mb);
            }
          }
        } catch {
          // polling failed, keep trying
        }
      }, 1000);
    } catch {
      setError("Cleanup failed — is the backend running?");
      setRunning(false);
    }
  };

  const formatSize = (mb: number) => mb >= 1024
    ? `${(mb / 1024).toFixed(1)} GB`
    : `${mb.toFixed(0)} MB`;

  const progressPct = STEP_WEIGHTS[step]
    ?? (step.startsWith("Vacuuming ohlcv_") ? 75 : 0);

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-6">
      <SectionHeader
        icon={<HardDrive size={20} />}
        title="Database Maintenance"
        desc="Clean up deleted records and reclaim disk space"
      />
      <div className="flex items-center gap-4 mt-4">
        <div className="text-sm text-ag-text-muted">
          Current size: <span className="text-ag-text font-medium">
            {sizeMb !== null ? formatSize(sizeMb) : "..."}
          </span>
        </div>
        <button onClick={handleCleanup} disabled={running}
          className="flex items-center gap-2 px-4 py-2 bg-ag-accent/20 text-ag-accent border border-ag-accent/30 rounded-lg text-sm hover:bg-ag-accent/30 disabled:opacity-50">
          <HardDrive size={16} /> {running ? "Cleaning up..." : "Run Cleanup"}
        </button>
      </div>

      {running && (
        <div className="mt-3 space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-ag-text-muted">{step}</span>
            <span className="text-ag-accent font-medium">{progressPct}%</span>
          </div>
          <div className="w-full bg-ag-border rounded-full h-2 overflow-hidden">
            <div
              className="h-full bg-ag-accent rounded-full transition-all duration-500"
              style={{ width: `${Math.max(progressPct, 3)}%` }}
            />
          </div>
        </div>
      )}

      {result && !running && (
        <div className="mt-3 bg-ag-green/10 border border-ag-green/30 rounded-lg p-3">
          <p className="text-sm text-ag-green font-medium">
            Cleanup complete — saved {formatSize(result.saved_mb)}
          </p>
          <p className="text-xs text-ag-green/80 mt-1">
            {formatSize(result.before_mb)} → {formatSize(result.after_mb)}
          </p>
          <div className="text-xs text-ag-text-muted mt-2 space-y-0.5">
            {result.deleted_batches > 0 && <p>Deleted batches: {result.deleted_batches}</p>}
            {result.deleted_runs > 0 && <p>Deleted runs: {result.deleted_runs}</p>}
            {result.deleted_trades > 0 && <p>Deleted trades: {result.deleted_trades}</p>}
            {result.deleted_strategies > 0 && <p>Deleted strategies: {result.deleted_strategies}</p>}
            {result.deleted_wfo > 0 && <p>Deleted WFO runs: {result.deleted_wfo}</p>}
            {result.deleted_portfolios > 0 && <p>Deleted portfolios: {result.deleted_portfolios}</p>}
            {result.duplicate_ohlcv_removed > 0 && <p>OHLCV duplicates removed: {result.duplicate_ohlcv_removed}</p>}
            {result.deleted_batches === 0 && result.deleted_runs === 0 && result.duplicate_ohlcv_removed === 0 && (
              <p>No deleted records found — space reclaimed from fragmentation only</p>
            )}
          </div>
          {result.vacuum_error && (
            <p className="text-xs text-yellow-400 mt-2">
              VACUUM skipped (database locked by live trading) — WAL checkpoint still reclaimed space.
              For full compaction, stop the backend and run cleanup again.
            </p>
          )}
        </div>
      )}
      {error && (
        <p className="text-xs text-red-400 mt-2">{error}</p>
      )}
      <p className="text-[10px] text-ag-text-muted mt-3">
        Removes soft-deleted records, deduplicates candle data, and runs VACUUM to reclaim disk space.
        Active backtests, strategies, and all candle data are preserved. Size includes DB + WAL files.
      </p>
    </div>
  );
}
