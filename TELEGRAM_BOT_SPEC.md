# Telegram Bot Companion — Full Specification

> **Note (2026-10-08):** this is the original design spec, written before the bot was built. The built bot is the `telegram_bot/` package (run `python -m telegram_bot`), and it calls the backend on port 8055, not 8000. Where this spec and the code differ, the code is right.

> Mobile companion for The War Room live trading platform.
> Monitor, control, and manage your live trading from your phone via Telegram.

---

## Why This Approach

- **Zero app development** — uses Telegram, which is already on your phone
- **Instant push notifications** — Telegram handles delivery reliably
- **No server exposure** — bot runs on your PC and talks to `localhost:8000`
- **Zero backend changes** — all needed API endpoints already exist
- **Simple setup** — 2 environment variables, 1 new Python file

---

## Architecture

```
Your Phone (Telegram App)
    ↕  (internet)
Telegram Cloud API
    ↕  (internet)
telegram_bot.py (runs on your PC)
    ↕  (localhost)
War Room Backend (port 8000)
```

- The bot script runs alongside the backend on your machine
- It connects to Telegram's cloud to receive your commands
- It calls `http://localhost:8000/api/...` to get data and execute actions
- Results are sent back to your phone as Telegram messages

---

## Existing API Endpoints (Already Built — No Changes Needed)

### Engine & Account

| Endpoint | Method | What it returns |
|----------|--------|----------------|
| `/api/health` | GET | Backend alive check |
| `/api/live/status` | GET | Engine running state, paper/live mode, equity, active portfolios |
| `/api/live/account-summary` | GET | Full account: equity, margin, positions count, daily P&L, circuit breaker |
| `/api/live/pnl-summary` | GET | P&L for today/week/month/all-time, win rate, avg win/loss |
| `/api/live/equity` | GET | Live equity curve data |

### Positions & Orders

| Endpoint | Method | What it returns |
|----------|--------|----------------|
| `/api/live/positions` | GET | All open positions with symbol, side, entry price, unrealized P&L |
| `/api/live/orders` | GET | All pending/active orders |
| `/api/live/tsl-status` | GET | Trailing stop loss status for all active TSLs |
| `/api/exchange/positions` | GET | Raw exchange positions (Bybit) |
| `/api/exchange/balance` | GET | Exchange account balance |

### Trades & History

| Endpoint | Method | What it returns |
|----------|--------|----------------|
| `/api/live/trades` | GET | Recent closed trades with realized P&L, fees, close reason |
| `/api/trades` | GET | Full trade history |
| `/api/trades/stats` | GET | Aggregate trade statistics |
| `/api/exchange/trades` | GET | Raw exchange trade history |

### Signals & Events

| Endpoint | Method | What it returns |
|----------|--------|----------------|
| `/api/live/signals` | GET | Active strategy signals (entries/exits generated) |
| `/api/live/events` | GET | Audit log: engine events, trade events, errors |
| `/api/activity/recent` | GET | Recent system activity feed |

### Engine Control

| Endpoint | Method | What it does |
|----------|--------|-------------|
| `/api/live/start` | POST | Start the live trading engine |
| `/api/live/stop` | POST | Graceful stop (cancel entries, keep exits alive) |
| `/api/live/emergency-stop` | POST | Kill switch — close ALL positions + shutdown |
| `/api/live/reset-safety` | POST | Reset circuit breakers after they trip |

### Position Management

| Endpoint | Method | What it does |
|----------|--------|-------------|
| `/api/live/close-position` | POST | Close a single position by ID |
| `/api/live/close-all` | POST | Close all open positions |

### Portfolio Control

| Endpoint | Method | What it does |
|----------|--------|-------------|
| `/api/portfolios` | GET | List all portfolios (with type filter) |
| `/api/portfolios/{id}` | GET | Portfolio details with assets |
| `/api/portfolios/{id}/assets` | GET | List assets in a portfolio |
| `/api/portfolios/{id}/full-metrics` | GET | Comprehensive portfolio metrics |
| `/api/portfolios/{id}/data-freshness` | GET | Check if OHLCV data is stale |
| `/api/live/activate` | POST | Activate a portfolio for live trading |
| `/api/live/deactivate` | POST | Deactivate a portfolio (optionally close positions) |
| `/api/live/monitor` | POST | Enable candle sync monitoring for a portfolio |
| `/api/live/stop-monitoring` | POST | Stop monitoring a portfolio |
| `/api/live/refresh-portfolio` | POST | Sync portfolio state with exchange |

### Signal Management

| Endpoint | Method | What it does |
|----------|--------|-------------|
| `PATCH /api/live/signals/{id}/status` | PATCH | Update signal status (acknowledge, dismiss, etc.) |

### System & Maintenance

| Endpoint | Method | What it does |
|----------|--------|-------------|
| `/api/exchange/status` | GET | Exchange connection status (Bybit API health) |
| `/api/db-size` | GET | Database size stats |
| `/api/settings` | GET | Current backend settings |
| `/api/dashboard/stats` | GET | Dashboard summary statistics |
| `/api/dashboard/account-kpis` | GET | Key performance indicators |
| `/api/dashboard/btc-change` | GET | BTC price change (market context) |

---

## Bot Commands — Complete Reference

### 📊 Monitoring Commands (read-only, safe)

| Command | Description | API Endpoint(s) |
|---------|-------------|-----------------|
| `/status` | Engine state + account overview + active portfolios | `live/status` + `live/account-summary` |
| `/positions` | All open positions with P&L, SL/TP, TSL status | `live/positions` + `live/tsl-status` |
| `/orders` | All pending/active orders | `live/orders` |
| `/pnl` | P&L breakdown: today/week/month/all-time + win rate | `live/pnl-summary` |
| `/trades [N]` | Last N closed trades (default 10, max 50) | `live/trades?limit=N` |
| `/signals` | Active strategy signals — entries/exits waiting | `live/signals` |
| `/events [N]` | Last N engine events — audit log (default 20) | `live/events?limit=N` |
| `/health` | Backend + exchange connection status | `health` + `exchange/status` |
| `/balance` | Exchange account balance (equity, margin, available) | `exchange/balance` |
| `/btc` | BTC current price + 24h change (market pulse) | `dashboard/btc-change` |
| `/kpis` | Key performance indicators from dashboard | `dashboard/account-kpis` |

### 📁 Portfolio Commands (read + activate/deactivate)

| Command | Description | API Endpoint(s) |
|---------|-------------|-----------------|
| `/portfolios` | List all portfolios with status (saved/paper/real) | `portfolios` |
| `/portfolio <id>` | Portfolio detail: assets, metrics, data freshness | `portfolios/{id}` + `portfolios/{id}/assets` + `portfolios/{id}/data-freshness` |
| `/activate <id>` | Activate a portfolio for live trading | `live/activate` |
| `/deactivate <id>` | Deactivate a portfolio | `live/deactivate` |
| `/monitor <id>` | Start candle sync monitoring (no trading) | `live/monitor` |
| `/stopmon <id>` | Stop monitoring a portfolio | `live/stop-monitoring` |
| `/refresh <id>` | Sync portfolio state with exchange | `live/refresh-portfolio` |

### ⚡ Engine Control Commands

| Command | Description | Confirmation? | API Endpoint |
|---------|-------------|---------------|-------------|
| `/start` | Start the trading engine | YES if LIVE mode | `live/start` |
| `/stop` | Graceful stop (cancel entries, keep exits) | No | `live/stop` |
| `/estop` | Emergency stop (close ALL + shutdown) | YES — "Type EMERGENCY to confirm" | `live/emergency-stop` |
| `/resetsafety` | Reset circuit breakers after they trip | YES — "Type RESET to confirm" | `live/reset-safety` |

### 🔧 Position Management Commands

| Command | Description | Confirmation? | API Endpoint |
|---------|-------------|---------------|-------------|
| `/close <id>` | Close a single position (ID from `/positions`) | No | `live/close-position` |
| `/closeall` | Close ALL open positions | YES — "Type CLOSEALL to confirm" | `live/close-all` |

### 🛠️ System Commands

| Command | Description | API Endpoint(s) |
|---------|-------------|-----------------|
| `/dbsize` | Database size and stats | `db-size` |
| `/settings` | Current backend settings (read-only) | `settings` |
| `/activity` | Recent system activity feed | `activity/recent` |
| `/help` | Show all available commands grouped by category | (local) |
| `/ping` | Bot alive check (doesn't call backend) | (local) |

---

## Confirmation Flow

Dangerous commands require typing a specific confirmation word (not just YES) to prevent accidental triggers.

### Emergency Stop
```
You:  /estop
Bot:  🚨 EMERGENCY STOP will:
      • Close ALL open positions at market
      • Shut down the trading engine
      • This cannot be undone.

      Type EMERGENCY to confirm, anything else to cancel.
You:  EMERGENCY
Bot:  🚨 Emergency stop executed. All positions closing. Engine shutting down.
```

### Close All Positions
```
You:  /closeall
Bot:  ⚠️ CLOSE ALL will close 5 open positions at market:
      1. BTCUSDT Long  | P&L: +$32.10
      2. ETHUSDT Short | P&L: -$8.20
      3. SOLUSDT Long  | P&L: +$12.50
      4. DOGEUSDT Long | P&L: +$3.80
      5. XRPUSDT Short | P&L: -$1.40

      Net P&L if closed now: +$38.80

      Type CLOSEALL to confirm, anything else to cancel.
You:  CLOSEALL
Bot:  ✅ All 5 positions closed.
```

### Start Engine (Live Mode)
```
You:  /start
Bot:  ⚠️ Engine will start in LIVE mode (real money).
      Active portfolios: Alpha-V3, BTC-Momentum

      Type START to confirm, anything else to cancel.
You:  START
Bot:  ✅ Engine started (LIVE mode). 2 portfolios active.
```
Note: `/start` skips confirmation in PAPER mode — just starts immediately.

### Reset Circuit Breaker
```
You:  /resetsafety
Bot:  ⚠️ This will reset ALL circuit breakers:
      • Daily loss limit (-$500 hit at 14:32)
      • Max drawdown (triggered at 15:01)

      Trading will resume with fresh safety limits.
      Type RESET to confirm.
You:  RESET
Bot:  ✅ Circuit breakers reset. Safety limits refreshed.
```

---

## Push Notifications (Automatic Alerts)

The bot polls `/api/live/events` every 10 seconds and sends messages when it detects new events.

### Trade Notifications

| Event Type | Notification |
|------------|-------------|
| Trade Opened | 🟢 **OPENED**: BTCUSDT Long @ 67,500 · SL: 66,800 · TP: 69,200 · Size: 0.15 BTC |
| Trade Closed (profit) | 🟢 **CLOSED**: BTCUSDT Long **+$45.20** · Reason: TP hit · Duration: 4h 22m |
| Trade Closed (loss) | 🔴 **CLOSED**: ETHUSDT Short **-$12.80** · Reason: SL hit · Duration: 1h 05m |
| TSL Activated | 📐 **TSL ACTIVE**: BTCUSDT Long · Trail: 66,800 → 67,200 |
| TSL Updated | 📐 **TSL MOVED**: BTCUSDT Long · Trail: 67,200 → 67,850 |

### Engine Notifications

| Event Type | Notification |
|------------|-------------|
| Engine Started | ▶️ Engine started (PAPER mode) · 2 portfolios active |
| Engine Stopped | ⏹️ Engine stopped gracefully |
| Circuit Breaker | 🚨 **CIRCUIT BREAKER**: Daily loss limit hit (-$500) · Trading paused |
| Entry Blocked | ⛔ Entry blocked: ETHUSDT — max positions reached |
| Emergency Stop | 🚨 **EMERGENCY STOP** executed · All positions closing |

### Signal Notifications

| Event Type | Notification |
|------------|-------------|
| New Signal (entry) | 📡 **SIGNAL**: BTCUSDT Long entry · Strategy: Alpha-V3 · Portfolio: Main |
| Missed Signal | ⚠️ **MISSED**: SOLUSDT Long entry — position limit reached |
| Signal Rejected | ⛔ **REJECTED**: XRPUSDT Short — circuit breaker active |

### System Notifications

| Event Type | Notification |
|------------|-------------|
| Exchange Disconnect | 🔌 **EXCHANGE DOWN**: Bybit connection lost · Reconnecting... |
| Exchange Reconnect | ✅ **EXCHANGE UP**: Bybit connection restored |
| Backend Error | ⚠️ **ERROR**: [error message summary] |
| Bot Started | 🤖 War Room Bot online · Backend: OK · Exchange: Connected |

### Notification Batching

When multiple events arrive in the same poll cycle (e.g., several trades close simultaneously), they are batched into a single message to avoid spam:

```
🔴 3 trades closed:
  BTCUSDT Long  +$45.20 (TP)
  ETHUSDT Short -$12.80 (SL)
  SOLUSDT Long  +$22.10 (signal)
Net: +$54.50
```

---

## Resilience & Error Handling

### Backend Polling

- Polls `/api/live/events` every **10 seconds** when engine is running
- Polls `/api/health` every **60 seconds** as a heartbeat (always, even when engine is off)
- On connection failure: exponential backoff (10s → 20s → 40s → max 120s)
- Sends **one** "Backend unreachable" notification on first failure
- Sends "Backend restored" notification on recovery
- Tracks last-seen event ID (monotonic integer) to prevent duplicate notifications

### Telegram Rate Limits

- Telegram allows ~30 messages/second to a single chat
- Bot batches events from the same poll cycle into one message
- Burst protection: if >10 events in a single cycle, consolidate into summary

### Graceful Shutdown

- `Ctrl+C` on the bot terminal stops polling cleanly
- Pending confirmations are cancelled
- Sends "Bot going offline" notification before exit

### Confirmation Timeout

- Confirmation prompts expire after **60 seconds**
- Only one pending confirmation at a time (new one cancels old)
- Non-matching text cancels the confirmation: "Cancelled. No action taken."

---

## Message Formatting

All bot messages use Telegram's MarkdownV2 formatting for readability. Key conventions:

- **Bold** for important values (P&L, equity, status)
- `Monospace` for IDs and technical values
- Emoji prefixes for quick visual scanning
- Numbers formatted with thousands separators
- P&L always includes + or - sign and $ symbol
- Position IDs displayed as short codes (first 8 chars) for easy `/close` usage

### Example: `/status` Response

```
⚡ THE WAR ROOM

Engine: 🟢 RUNNING (PAPER)
Uptime: 4h 32m

💰 Account
Equity:    $10,245.80
Margin:    $3,420.00 (33.4%)
Available: $6,825.80

📊 Positions: 5 open (3L / 2S)
Unrealized P&L: +$87.50

📈 Today: +$142.30 | Week: +$312.00

🔒 Circuit Breakers: OK
📡 Exchange: Connected
```

### Example: `/positions` Response

```
📊 5 Open Positions

1️⃣ BTCUSDT Long
   Entry: 67,500.00 | Now: 67,832.10
   P&L: +$32.10 (+0.49%)
   SL: 66,800 | TP: 69,200
   TSL: 67,200 (active, trailing)
   ID: a3f8c2d1

2️⃣ ETHUSDT Short
   Entry: 3,450.00 | Now: 3,458.20
   P&L: -$8.20 (-0.24%)
   SL: 3,520 | TP: 3,280
   ID: b7e4f9a2

[... more positions ...]

💡 Close with: /close a3f8c2d1
```

### Example: `/portfolios` Response

```
📁 Portfolios

🟢 ACTIVE (live trading)
  1. Alpha-V3        | 12 assets | Real
  2. BTC-Momentum    | 5 assets  | Paper

👁️ MONITORING (candle sync only)
  3. ETH-Scalper     | 8 assets  | Paper

💤 INACTIVE
  4. Old-Strat-v1    | 15 assets | Saved
  5. Test-Portfolio   | 3 assets  | Saved

💡 Details: /portfolio 1
💡 Activate: /activate 1
```

### Example: `/signals` Response

```
📡 Active Signals

🟢 PENDING ENTRY
  BTCUSDT Long | Alpha-V3 | 14:32 UTC
  SOLUSDT Long | BTC-Mom  | 14:28 UTC

🔴 PENDING EXIT
  ETHUSDT Short | Alpha-V3 | 14:35 UTC

⚠️ MISSED (not executed)
  XRPUSDT Long | Alpha-V3 | 14:30 UTC
  Reason: Max positions reached

⛔ REJECTED
  DOGEUSDT Short | BTC-Mom | 14:31 UTC
  Reason: Circuit breaker active
```

---

## Files to Create / Modify

### 1. NEW: `The_War_Room/telegram_bot.py` (~350 lines)

The main bot script. Uses:
- `python-telegram-bot` v21+ (async Telegram API wrapper)
- `httpx` (async HTTP client to call War Room API)

Structure:
```
telegram_bot.py
├── Config
│   ├── API_BASE = "http://localhost:8000/api"
│   ├── Load TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from settings
│   └── Security: only responds to TELEGRAM_CHAT_ID
│
├── API Client (async httpx wrapper)
│   ├── get(endpoint) → dict
│   ├── post(endpoint, data) → dict
│   └── Error handling + timeout (10s per request)
│
├── Monitoring Commands
│   ├── /status  → live/status + live/account-summary
│   ├── /positions → live/positions + live/tsl-status
│   ├── /orders → live/orders
│   ├── /pnl → live/pnl-summary
│   ├── /trades [N] → live/trades?limit=N
│   ├── /signals → live/signals
│   ├── /events [N] → live/events?limit=N
│   ├── /health → health + exchange/status
│   ├── /balance → exchange/balance
│   ├── /btc → dashboard/btc-change
│   └── /kpis → dashboard/account-kpis
│
├── Portfolio Commands
│   ├── /portfolios → portfolios (list)
│   ├── /portfolio <id> → portfolios/{id} + assets + data-freshness
│   ├── /activate <id> → live/activate
│   ├── /deactivate <id> → live/deactivate
│   ├── /monitor <id> → live/monitor
│   ├── /stopmon <id> → live/stop-monitoring
│   └── /refresh <id> → live/refresh-portfolio
│
├── Engine Control Commands
│   ├── /start → live/start (confirm if LIVE mode)
│   ├── /stop → live/stop
│   ├── /estop → live/emergency-stop (confirm: EMERGENCY)
│   └── /resetsafety → live/reset-safety (confirm: RESET)
│
├── Position Management Commands
│   ├── /close <id> → live/close-position
│   └── /closeall → live/close-all (confirm: CLOSEALL)
│
├── System Commands
│   ├── /dbsize → db-size
│   ├── /settings → settings
│   ├── /activity → activity/recent
│   ├── /help → (local command list)
│   └── /ping → (local bot alive check)
│
├── Confirmation Handler
│   ├── Pending confirmation state (action, confirm_word, timeout)
│   ├── Text message handler checks for confirmation match
│   ├── 60s timeout auto-cancel
│   └── Only one pending confirmation at a time
│
├── Event Poller (background job)
│   ├── Poll /live/events every 10s
│   ├── Track last_event_id for dedup
│   ├── Classify events → format notification
│   ├── Batch multiple events from same cycle
│   └── Exponential backoff on failure
│
├── Health Monitor (background job)
│   ├── Poll /health every 60s
│   ├── Send "Backend unreachable" on first failure
│   ├── Send "Backend restored" on recovery
│   └── Exponential backoff (10s → 120s max)
│
└── Startup
    ├── Validate token + chat_id
    ├── Test backend connection
    ├── Send "Bot online" notification
    └── Register all handlers + start polling
```

### 2. MODIFY: `backend/config/settings.py` (add 2 lines)

```python
# --- Telegram Bot ---
telegram_bot_token: str = ""
telegram_chat_id: str = ""
```

### 3. MODIFY: `backend/config/.env` (add 2 lines)

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

### 4. MODIFY: the Windows launcher (not published)

Add after the frontend launch section:

```bat
:: Start Telegram bot (only if token is configured)
echo Checking Telegram bot configuration...
pushd "<path-to>\The_War_Room"
python -c "from backend.config import get_settings; s=get_settings(); exit(0 if s.telegram_bot_token else 1)" 2>nul
if not errorlevel 1 (
    echo Starting Telegram bot...
    start "[5/5] THE WAR ROOM - TELEGRAM BOT (keep open)" cmd /k python telegram_bot.py
) else (
    echo Telegram bot skipped (no token configured)
)
popd
```

Window titles update: `[1/5]` through `[5/5]` (renumber existing windows).

### 5. MODIFY: `How_to.txt` (add new section)

Add a "Telegram Bot (Mobile Companion)" section explaining setup.

---

## Setup Instructions

### Step 1: Create a Telegram Bot

1. Open Telegram on your phone
2. Search for `@BotFather` and start a chat
3. Send `/newbot`
4. Follow the prompts:
   - Bot name: `War Room Bot` (or anything you like)
   - Bot username: `warroom_trading_bot` (must be unique, end with `bot`)
5. BotFather will give you a **token** like: `7123456789:AAHx2DfG...`
6. Save this token

### Step 2: Get Your Chat ID

1. Send any message to your new bot (e.g., "hello")
2. Open this URL in your browser (replace `<TOKEN>` with your actual token):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Look for `"chat":{"id":` — that number is your chat ID (e.g., `987654321`)

### Step 3: Configure The War Room

1. Open `The_War_Room/backend/config/.env`
2. Add your values:
   ```
   TELEGRAM_BOT_TOKEN=7123456789:AAHx2DfG...
   TELEGRAM_CHAT_ID=987654321
   ```

### Step 4: Install Dependency

```
pip install python-telegram-bot httpx
```

### Step 5: Restart The War Room

- Run the Windows launcher (not published) — a 5th window will appear for the Telegram bot
- Send `/health` from your phone to verify it's working

---

## Security

| Concern | Mitigation |
|---------|------------|
| Who can control my bot? | Only the configured `TELEGRAM_CHAT_ID` — all other users are silently ignored |
| Is my data exposed to the internet? | No — the bot calls `localhost:8000` only. Your API is never exposed |
| Can someone intercept commands? | Telegram uses encryption for bot communication |
| What about the bot token? | Stored in `.env` (never committed to git). If leaked, revoke via BotFather |
| Dangerous commands? | `/estop`, `/closeall`, `/resetsafety`, and live `/start` require unique confirmation words |
| Pocket-dial protection? | Confirmation words are specific to each command (EMERGENCY, CLOSEALL, START, RESET) |
| Stale confirmations? | Confirmations expire after 60 seconds |

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `python-telegram-bot` | 21.x | Telegram Bot API wrapper (async) |
| `httpx` | 0.27+ | Async HTTP client (calls War Room API) |

Both are well-maintained, widely used packages. `httpx` is already in the project.

---

## How to Verify It Works

1. **Start The War Room** — confirm window `[5/5] THE WAR ROOM - TELEGRAM BOT` appears
2. **Send `/ping`** — should reply instantly (no backend needed)
3. **Send `/health`** — should reply "Backend: OK | Exchange: Connected"
4. **Send `/status`** — should show engine state and account info
5. **Send `/positions`** — should show open positions (or "No open positions")
6. **Send `/pnl`** — should show P&L breakdown
7. **Send `/portfolios`** — should list all portfolios with their status
8. **Send `/signals`** — should show active/missed/rejected signals
9. **Send `/btc`** — should show BTC price and 24h change
10. **Start the engine** via `/start` — verify it starts (confirm if LIVE)
11. **Wait for a trade** — verify you get a push notification on your phone
12. **Send `/stop`** — verify graceful shutdown
13. **Test `/estop`** confirmation — send `/estop`, type "no", confirm it cancels
14. **Test `/estop`** confirmation — send `/estop`, wait 60s, confirm it times out

---

## What This Does NOT Include (Intentionally Excluded)

- **Backtesting** — desktop-only, requires visual chart review
- **Optimization** — compute-heavy, needs progress monitoring UI
- **Portfolio creation/modification** — complex multi-step workflow better suited to desktop
- **Strategy editing** — code editing doesn't work on mobile
- **Walk Forward Optimization** — desktop-only
- **Journal entries** — long-form writing not great on mobile
- **Charts or images** — text-only for reliability and speed
- **Multiple user access** — single-user, single-chat design
- **Data downloads** — long-running, desktop-only

The goal is a focused, reliable mobile companion for the things you actually need on the go: "Is it running? How's it doing? Something's wrong — shut it down."

---

## Command Quick Reference Card

```
📊 MONITORING                  📁 PORTFOLIOS
/status    Engine overview     /portfolios  List all
/positions Open positions      /portfolio   Detail view
/orders    Pending orders      /activate    Start trading
/pnl       P&L breakdown      /deactivate  Stop trading
/trades    Closed trades       /monitor     Candle sync
/signals   Strategy signals    /stopmon     Stop sync
/events    Audit log           /refresh     Sync exchange
/health    System check
/balance   Exchange balance    ⚡ ENGINE
/btc       BTC price           /start       Start engine
/kpis      Performance KPIs    /stop        Graceful stop
                               /estop       Emergency stop
🔧 POSITIONS                   /resetsafety Reset breakers
/close     Close one
/closeall  Close all           🛠️ SYSTEM
                               /dbsize   Database stats
                               /settings Backend config
                               /activity Recent activity
                               /ping     Bot alive check
                               /help     This list
```
