"""
Technical analysis indicator library (ta.* functions).
All functions operate on pandas Series or numpy arrays.

Used at runtime by every strategy — this is NOT transpiler-specific.
Relocated from backend/transpiler/indicators.py on 2026-03-13.
"""
import numpy as np
import pandas as pd


class IndicatorLibrary:
  """Namespace for technical analysis indicators (ta.xxx)."""

  # ── Moving Averages ─────────────────────────────────────────────
  @staticmethod
  def sma(source: pd.Series, length: int) -> pd.Series:
    """Simple Moving Average."""
    return source.rolling(window=length, min_periods=length).mean()

  @staticmethod
  def ema(source: pd.Series, length: int) -> pd.Series:
    """Exponential Moving Average."""
    return source.ewm(span=length, adjust=False).mean()

  @staticmethod
  def wma(source: pd.Series, length: int) -> pd.Series:
    """Weighted Moving Average."""
    weights = np.arange(1, length + 1, dtype=float)
    return source.rolling(window=length).apply(
      lambda x: np.dot(x, weights) / weights.sum(), raw=True,
    )

  @staticmethod
  def vwma(source: pd.Series, volume: pd.Series, length: int) -> pd.Series:
    """Volume-Weighted Moving Average."""
    return (source * volume).rolling(length).sum() / volume.rolling(length).sum()

  @staticmethod
  def rma(source: pd.Series, length: int) -> pd.Series:
    """Running Moving Average (Wilder's smoothing).

    Matches TradingView's ta.rma():
    - First `length - 1` bars → NaN
    - Bar `length - 1` → SMA seed (average of first `length` values)
    - Bar `length` onward → alpha * value + (1 - alpha) * prev
    """
    alpha = 1.0 / length
    vals = source.values.astype(float)
    out = np.full(len(vals), np.nan)

    # Find first window of `length` non-NaN values for SMA seed
    count = 0
    seed_end = -1
    for i in range(len(vals)):
      if not np.isnan(vals[i]):
        count += 1
        if count == length:
          seed_end = i
          break
      else:
        count = 0

    if seed_end < 0:
      return pd.Series(out, index=source.index)

    # SMA seed
    out[seed_end] = np.nanmean(vals[seed_end - length + 1 : seed_end + 1])

    # Exponential smoothing from seed onward
    for i in range(seed_end + 1, len(vals)):
      if np.isnan(vals[i]):
        out[i] = out[i - 1]  # carry forward on NaN input
      else:
        out[i] = alpha * vals[i] + (1.0 - alpha) * out[i - 1]

    return pd.Series(out, index=source.index)

  @staticmethod
  def hma(source: pd.Series, length: int) -> pd.Series:
    """Hull Moving Average."""
    half_len = int(length / 2)
    sqrt_len = int(np.sqrt(length))
    wma_half = IndicatorLibrary.wma(source, half_len)
    wma_full = IndicatorLibrary.wma(source, length)
    diff = 2 * wma_half - wma_full
    return IndicatorLibrary.wma(diff, sqrt_len)

  @staticmethod
  def swma(source: pd.Series) -> pd.Series:
    """Symmetrically Weighted Moving Average (4-bar)."""
    return source.rolling(4).apply(
      lambda x: (x.iloc[0] + 2 * x.iloc[1] + 2 * x.iloc[2] + x.iloc[3]) / 6,
      raw=False,
    )

  @staticmethod
  def alma(source: pd.Series, length: int, offset: float = 0.85, sigma: float = 6.0) -> pd.Series:
    """Arnaud Legoux Moving Average."""
    m = offset * (length - 1)
    s = length / sigma
    weights = np.exp(-((np.arange(length) - m) ** 2) / (2 * s * s))
    weights /= weights.sum()
    return source.rolling(length).apply(lambda x: np.dot(x, weights), raw=True)

  # ── Oscillators ─────────────────────────────────────────────────
  @staticmethod
  def rsi(source: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = source.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = IndicatorLibrary.rma(gain, length)
    avg_loss = IndicatorLibrary.rma(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

  @staticmethod
  def macd(source: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD — returns (macd_line, signal_line, histogram)."""
    fast_ema = IndicatorLibrary.ema(source, fast)
    slow_ema = IndicatorLibrary.ema(source, slow)
    macd_line = fast_ema - slow_ema
    signal_line = IndicatorLibrary.ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

  @staticmethod
  def stoch(high: pd.Series, low: pd.Series, close: pd.Series,
            length: int = 14) -> pd.Series:
    """Stochastic %K."""
    lowest = low.rolling(length).min()
    highest = high.rolling(length).max()
    denom = highest - lowest
    return 100 * (close - lowest) / denom.replace(0, np.nan)

  @staticmethod
  def cci(high: pd.Series, low: pd.Series, close: pd.Series,
          length: int = 20) -> pd.Series:
    """Commodity Channel Index."""
    tp = (high + low + close) / 3
    sma_tp = tp.rolling(length).mean()
    mad = tp.rolling(length).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma_tp) / (0.015 * mad)

  @staticmethod
  def mfi(high: pd.Series, low: pd.Series, close: pd.Series,
          volume: pd.Series, length: int = 14) -> pd.Series:
    """Money Flow Index."""
    tp = (high + low + close) / 3
    rmf = tp * volume
    delta = tp.diff()
    pos_flow = pd.Series(np.where(delta > 0, rmf, 0), index=tp.index)
    neg_flow = pd.Series(np.where(delta < 0, rmf, 0), index=tp.index)
    pos_sum = pos_flow.rolling(length).sum()
    neg_sum = neg_flow.rolling(length).sum()
    ratio = pos_sum / neg_sum.replace(0, np.nan)
    return 100 - (100 / (1 + ratio))

  @staticmethod
  def dmi(high: pd.Series, low: pd.Series, close: pd.Series,
          length: int = 14):
    """Directional Movement Index — returns (plus_di, minus_di, adx).
    All components use RMA (Wilder's smoothing) to match TradingView."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=high.index)
    atr_val = IndicatorLibrary.atr(high, low, close, length)
    plus_di = 100 * IndicatorLibrary.rma(plus_dm, length) / atr_val.replace(0, np.nan)
    minus_di = 100 * IndicatorLibrary.rma(minus_dm, length) / atr_val.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = IndicatorLibrary.rma(dx, length)
    return plus_di, minus_di, adx

  # ── Volatility ──────────────────────────────────────────────────
  @staticmethod
  def atr(high: pd.Series, low: pd.Series, close: pd.Series,
          length: int = 14) -> pd.Series:
    """Average True Range."""
    tr = IndicatorLibrary.tr(high, low, close)
    return IndicatorLibrary.rma(tr, length)

  @staticmethod
  def tr(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range."""
    prev_close = close.shift(1)
    return pd.concat([
      high - low,
      (high - prev_close).abs(),
      (low - prev_close).abs(),
    ], axis=1).max(axis=1)

  @staticmethod
  def bb(source: pd.Series, length: int = 20, mult: float = 2.0):
    """Bollinger Bands — returns (upper, basis, lower)."""
    basis = IndicatorLibrary.sma(source, length)
    std = source.rolling(length).std(ddof=0)  # population std to match TV
    upper = basis + mult * std
    lower = basis - mult * std
    return upper, basis, lower

  @staticmethod
  def kc(high: pd.Series, low: pd.Series, close: pd.Series,
         length: int = 20, mult: float = 1.5):
    """Keltner Channels — returns (upper, basis, lower)."""
    basis = IndicatorLibrary.ema(close, length)
    atr_val = IndicatorLibrary.atr(high, low, close, length)
    upper = basis + mult * atr_val
    lower = basis - mult * atr_val
    return upper, basis, lower

  # ── Trend ───────────────────────────────────────────────────────
  @staticmethod
  def donchian(high: pd.Series, low: pd.Series, length: int = 20):
    """Donchian Channels — returns (upper, basis, lower).
    Pure highest-high / lowest-low, no moving average."""
    upper = high.rolling(length).max()
    lower = low.rolling(length).min()
    basis = (upper + lower) / 2
    return upper, basis, lower

  @staticmethod
  def ichimoku(high: pd.Series, low: pd.Series,
               conversion: int = 9, base: int = 26, span_b: int = 52):
    """Ichimoku Cloud — returns (tenkan, kijun, senkou_a, senkou_b).
    All lines use (highest + lowest) / 2 — no moving average."""
    tenkan = (high.rolling(conversion).max() + low.rolling(conversion).min()) / 2
    kijun = (high.rolling(base).max() + low.rolling(base).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(base)
    senkou_b = ((high.rolling(span_b).max() + low.rolling(span_b).min()) / 2).shift(base)
    return tenkan, kijun, senkou_a, senkou_b

  @staticmethod
  def vwap(high: pd.Series, low: pd.Series, close: pd.Series,
           volume: pd.Series) -> pd.Series:
    """VWAP — cumulative sum(TP * vol) / sum(vol). No daily reset."""
    tp = (high + low + close) / 3
    return (tp * volume).cumsum() / volume.cumsum().replace(0, np.nan)

  @staticmethod
  def sar(high: pd.Series, low: pd.Series,
          start: float = 0.02, inc: float = 0.02, max_val: float = 0.2) -> pd.Series:
    """Parabolic SAR — recursive acceleration factor."""
    n = len(high)
    result = pd.Series(np.nan, index=high.index)
    bull = True
    af = start
    ep = low.iloc[0]
    sar_val = high.iloc[0]

    for i in range(1, n):
      prev_sar = sar_val
      if bull:
        sar_val = prev_sar + af * (ep - prev_sar)
        sar_val = min(sar_val, low.iloc[i - 1])
        if i >= 2:
          sar_val = min(sar_val, low.iloc[i - 2])
        if low.iloc[i] < sar_val:
          bull = False
          sar_val = ep
          af = start
          ep = low.iloc[i]
        elif high.iloc[i] > ep:
          ep = high.iloc[i]
          af = min(af + inc, max_val)
      else:
        sar_val = prev_sar + af * (ep - prev_sar)
        sar_val = max(sar_val, high.iloc[i - 1])
        if i >= 2:
          sar_val = max(sar_val, high.iloc[i - 2])
        if high.iloc[i] > sar_val:
          bull = True
          sar_val = ep
          af = start
          ep = high.iloc[i]
        elif low.iloc[i] < ep:
          ep = low.iloc[i]
          af = min(af + inc, max_val)
      result.iloc[i] = sar_val

    return result

  @staticmethod
  def supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
                 length: int = 10, mult: float = 3.0):
    """SuperTrend — returns (supertrend_line, direction)."""
    atr_val = IndicatorLibrary.atr(high, low, close, length)
    hl2 = (high + low) / 2
    upper = hl2 + mult * atr_val
    lower = hl2 - mult * atr_val

    st = pd.Series(np.nan, index=close.index)
    direction = pd.Series(1, index=close.index)

    for i in range(1, len(close)):
      if close.iloc[i] > upper.iloc[i - 1]:
        st.iloc[i] = lower.iloc[i]
        direction.iloc[i] = 1
      elif close.iloc[i] < lower.iloc[i - 1]:
        st.iloc[i] = upper.iloc[i]
        direction.iloc[i] = -1
      else:
        if direction.iloc[i - 1] == 1:
          st.iloc[i] = max(lower.iloc[i], st.iloc[i - 1]) if not np.isnan(st.iloc[i - 1]) else lower.iloc[i]
          direction.iloc[i] = 1
        else:
          st.iloc[i] = min(upper.iloc[i], st.iloc[i - 1]) if not np.isnan(st.iloc[i - 1]) else upper.iloc[i]
          direction.iloc[i] = -1

    return st, direction

  # ── Utility ─────────────────────────────────────────────────────
  @staticmethod
  def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    """True when a crosses above b."""
    return (a > b) & (a.shift(1) <= b.shift(1))

  @staticmethod
  def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    """True when a crosses below b."""
    return (a < b) & (a.shift(1) >= b.shift(1))

  @staticmethod
  def highest(source: pd.Series, length: int) -> pd.Series:
    return source.rolling(length).max()

  @staticmethod
  def lowest(source: pd.Series, length: int) -> pd.Series:
    return source.rolling(length).min()

  @staticmethod
  def change(source: pd.Series, length: int = 1) -> pd.Series:
    return source.diff(length)

  @staticmethod
  def barssince(condition: pd.Series) -> pd.Series:
    """Bars since condition was last true."""
    result = pd.Series(np.nan, index=condition.index)
    count = np.nan
    for i in range(len(condition)):
      if condition.iloc[i]:
        count = 0
      elif not np.isnan(count):
        count += 1
      result.iloc[i] = count
    return result

  @staticmethod
  def valuewhen(condition: pd.Series, source: pd.Series,
                occurrence: int = 0) -> pd.Series:
    """Value of source when condition was true (nth occurrence back)."""
    result = pd.Series(np.nan, index=source.index)
    hits: list[float] = []
    for i in range(len(condition)):
      if condition.iloc[i]:
        hits.append(source.iloc[i])
      if len(hits) > occurrence:
        result.iloc[i] = hits[-(occurrence + 1)]
    return result

  @staticmethod
  def cum(source: pd.Series) -> pd.Series:
    return source.cumsum()

  @staticmethod
  def pivothigh(source: pd.Series, left: int, right: int) -> pd.Series:
    """Pivot high detection."""
    result = pd.Series(np.nan, index=source.index)
    for i in range(left, len(source) - right):
      val = source.iloc[i]
      window = source.iloc[i - left : i + right + 1]
      if val == window.max():
        result.iloc[i + right] = val
    return result

  @staticmethod
  def pivotlow(source: pd.Series, left: int, right: int) -> pd.Series:
    """Pivot low detection."""
    result = pd.Series(np.nan, index=source.index)
    for i in range(left, len(source) - right):
      val = source.iloc[i]
      window = source.iloc[i - left : i + right + 1]
      if val == window.min():
        result.iloc[i + right] = val
    return result


# Singleton for import as `ta`
ta = IndicatorLibrary()
