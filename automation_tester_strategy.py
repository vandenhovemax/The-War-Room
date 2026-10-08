"""Automation Tester — a simple strategy for testing the live execution engine.

Fires entries on every bar (live engine deduplicates via DB check).
Supports 4 test modes to exercise all exit types: SL, TP, TSL, and walking SL.

IMPORTANT: This file is a REFERENCE COPY. The actual strategy code must be
inserted into the database strategies.python_source column. Use the War Room
UI "Paste Python" feature, or the retranspile API endpoint.
"""

from backend.backtester.base_strategy import BaseStrategy


class TranspiledStrategy(BaseStrategy):

  def init(self):
    self._warmup_bars = 0
    self._max_pyramiding = 1

    # ── Parameters ──────────────────────────────────────────────
    self.p_test_mode = self.input(
      defval="walking_sl", title="Test Mode",
      options=["sl_tp", "tsl", "all_exits", "walking_sl"],
    )
    self.p_direction = self.input(
      defval="long", title="Direction",
      options=["long", "short"],
    )
    self.p_sl_pct = self.input(defval=0.5, title="SL Distance %")
    self.p_tp_pct = self.input(defval=0.2, title="TP Distance %")
    self.p_trail_pct = self.input(defval=0.5, title="Trail Distance %")
    self.p_trail_offset_pct = self.input(defval=0.3, title="Trail Offset %")

    # Walking SL state
    self._walk_counter = 0
    self._walk_base = 0.0

  def next(self):
    c = self.close
    go_long = (self.p_direction == "long")
    direction = "long" if go_long else "short"
    # Fixed ID so exit orders always match the entry across bars
    tid = "LONG1" if go_long else "SHORT1"

    # ── Calculate exit levels from current close ────────────────
    sl_pct = self.p_sl_pct / 100.0
    tp_pct = self.p_tp_pct / 100.0
    trail_pct = self.p_trail_pct / 100.0
    trail_off_pct = self.p_trail_offset_pct / 100.0

    if go_long:
      sl_price = c * (1.0 - sl_pct)
      tp_price = c * (1.0 + tp_pct)
    else:
      sl_price = c * (1.0 + sl_pct)
      tp_price = c * (1.0 - tp_pct)

    trail_points = c * trail_pct
    trail_offset = c * trail_off_pct

    # ── Walking SL: gradually move SL toward current price ──────
    if self.p_test_mode == "walking_sl":
      self._walk_counter += 1
      if self._walk_counter > 10 or self._walk_base == 0.0:
        self._walk_counter = 1
        self._walk_base = c
      step = 0.001
      remaining = max(0.0, sl_pct - self._walk_counter * step)
      if go_long:
        sl_price = self._walk_base * (1.0 - remaining)
        # Clamp: SL must be below current price for Bybit to accept (Bug 37)
        # Use 0.01% margin — tight enough to allow risk_removed to trigger
        sl_price = min(sl_price, c * 0.9999)
      else:
        sl_price = self._walk_base * (1.0 + remaining)
        # Clamp: SL must be above current price for shorts (Bug 37)
        sl_price = max(sl_price, c * 1.0001)

    # ── Emit entry every bar ────────────────────────────────────
    # In live mode position_size is always 0 (no internal engine).
    # The live engine deduplicates by checking the DB for open
    # positions. When the exchange position closes, the next
    # entry passes through and opens a new trade automatically.
    self.entry(id=tid, direction=direction)

    # ── Register exit orders (test-mode dependent) ──────────────
    mode = self.p_test_mode

    if mode == "sl_tp":
      self.exit(id="SL_" + tid, from_entry=tid, stop=sl_price)
      self.exit(id="TP_" + tid, from_entry=tid, limit=tp_price)

    elif mode == "tsl":
      if go_long:
        safety_sl = c * 0.95
      else:
        safety_sl = c * 1.05
      self.exit(id="SL_" + tid, from_entry=tid, stop=safety_sl)
      self.exit(
        id="TSL_" + tid, from_entry=tid,
        trail_points=trail_points, trail_offset=trail_offset,
      )

    elif mode == "all_exits":
      self.exit(id="SL_" + tid, from_entry=tid, stop=sl_price)
      self.exit(id="TP_" + tid, from_entry=tid, limit=tp_price)
      self.exit(
        id="TSL_" + tid, from_entry=tid,
        trail_points=trail_points, trail_offset=trail_offset,
      )

    elif mode == "walking_sl":
      self.exit(id="SL_" + tid, from_entry=tid, stop=sl_price)
      self.exit(id="TP_" + tid, from_entry=tid, limit=tp_price)
