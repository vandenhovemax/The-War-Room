The War Room

**A walk-forward backtesting and execution engine for crypto perpetual futures, connected live to Bybit V5.**

Most backtests lie. If you search across hundreds of altcoins in hindsight, you can always find a strategy that looks like a money printer. The War Room was built to answer one question: **is this strategy actually good, or am I just fooling myself?**

Instead of optimizing for shiny backtest charts, most of the engineering here goes into trying to prove a strategy wrong before it ever touches exchange keys.

---

## The Core Design

Trading logic moves through a strict pipeline: raw market data $\rightarrow$ strategy execution $\rightarrow$ walk-forward analysis $\rightarrow$ adversarial stress testing $\rightarrow$ portfolio assembly $\rightarrow$ paper trading $\rightarrow$ live execution.

### 1. Stopping Overfitting

* **Walk-forward asset selection:** The system picks top assets on past data, then measures how they actually perform on an unseen future window before rolling forward.
* **Strict out-of-sample rules:** To prevent data leaks, in-sample and out-of-sample data are strictly separated. Open trades that cross selection boundaries are clipped or ignored until closed so future knowledge never bleeds into past choices.
* **Anti-cherry-picking guard:** Rankings penalize low trade counts, outlier spikes, and assets that overperform their peer group simply by luck.

### 2. Stress Testing & Parity

* **Adversarial tests:** Strategies are tested against randomized exit timing, heavy-tail trade removal (testing survival without the biggest winning trades), and Monte Carlo sequence shuffling to check actual drawdown risk.
* **Backtest-to-live parity:** A dedicated harness runs the live trading loop and the backtest engine side-by-side over historical candles to verify that live entry and exit signals match backtest decisions field-for-field.
* **Incremental state snapshots:** Daily jobs snapshot indicator buffers and open positions rather than recalculating years of history from scratch. Regular automated audits rerun full backtests from clean data to detect quiet state drift.

### 3. Execution & Safety

* **Fail-closed live engine:** Circuit breakers, daily loss caps, position-sizing off realized wallet balance (instead of fluctuating unrealized equity), and automatic market-flattens if stop-loss orders fail to register.
* **Data hygiene:** Database operations for order execution are decoupled from the WebSocket socket thread to prevent ping timeouts during high-volatility market events.

---

## System Architecture

* **Engine & API:** Built with Python (FastAPI, SQLAlchemy, NumPy/Pandas) for data crunching and Bybit integration.
* **Database:** SQLite running in WAL mode to handle concurrent reads and writes across multiple local background processes without complex database server overhead.
* **UI:** Next.js dashboard for visual feedback, performance analytics, and portfolio management.
* **Monitoring:** A Telegram bot backed by background heartbeats for state changes, risk alerts, and emergency manual interventions.

---

## Getting Started

### Prerequisites

* Python 3.13+
* Node.js 20+

### Setup

1. **Start the API:**
```bash
pip install -r backend/requirements.txt
python -m backend.main

```


2. **Start the background workers:**
```bash
# Terminal 2: Data sync worker
python candle_sync.py

# Terminal 3: Download worker
python download_worker.py

```


3. **Start the UI:**
```bash
cd frontend
npm install
npm run dev

```



*Note: You can run historical backtests, walk-forward analysis, and paper trade right away without adding exchange keys.*

---

## Known Limitations

* **Intrabar Fills:** OHLC bar backtesting cannot know the sub-bar price sequence. Stop-loss orders take priority over targets conservatively, but trailing stops assume extreme price highs or lows were hit first.
* **Local Environment:** Designed for single-machine deployment with local storage.

---

## License

All rights reserved. This repository is published for review and code demonstration purposes only. Unlawful copying, distribution, or commercial use is strictly prohibited.
