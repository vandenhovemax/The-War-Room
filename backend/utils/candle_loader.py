"""Shared candle loading utilities used by backtest and paper refresh."""
import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session


def load_candles_df(
  db: Session, symbol: str, timeframe: str, *, limit: int = 0,
) -> pd.DataFrame:
  """Load candles from the per-TF OHLCV database as DatetimeIndex DataFrame.

  Uses raw SQL + pd.read_sql for speed — bypasses ORM object creation
  overhead which matters for 80K+ row datasets.

  Args:
    limit: If > 0, only load the most recent N bars (for scanner efficiency).
           0 means load all (default, for backtesting).

  NOTE: The ``db`` parameter is kept for backward compatibility but is
  NOT used.  Candles are loaded from the timeframe-specific OHLCV
  database via ``ohlcv_router``.
  """
  from backend.db.database import ohlcv_router
  from backend.utils.tf_resolver import canonical_tf as _canonical_tf

  # Normalize timeframe so the SQL WHERE clause matches stored data
  timeframe = _canonical_tf(timeframe)
  ohlcv_engine = ohlcv_router.get_engine(timeframe)

  if limit > 0:
    # Subquery: get the most recent N rows, then sort ascending
    sql = (
      "SELECT * FROM ("
      "  SELECT timestamp, open, high, low, close, volume "
      "  FROM ohlcv_data "
      "  WHERE symbol = :sym AND timeframe = :tf "
      "  ORDER BY timestamp DESC LIMIT :lim"
      ") sub ORDER BY timestamp"
    )
    params = {"sym": symbol, "tf": timeframe, "lim": limit}
  else:
    sql = (
      "SELECT timestamp, open, high, low, close, volume "
      "FROM ohlcv_data "
      "WHERE symbol = :sym AND timeframe = :tf "
      "ORDER BY timestamp"
    )
    params = {"sym": symbol, "tf": timeframe}

  with ohlcv_engine.connect() as conn:
    df = pd.read_sql(
      text(sql), conn, params=params, parse_dates=["timestamp"],
    )
  if df.empty:
    raise ValueError(f"No data for {symbol} {timeframe}")
  return df.set_index("timestamp")


def to_nyt(df: pd.DataFrame) -> pd.DataFrame:
  """Convert DataFrame index from UTC to New York Time (naive).

  Strategy time filters (hour, minute, dayofweek) use NYT.
  Result is tz-naive with values in America/New_York.

  DST fall-back (first Sunday of November, 02:00 EDT → 01:00 EST) makes
  the wall-clock 01:00 and 01:30 NYT happen TWICE — first in EDT, then
  again 1 hour later in EST. After ``tz_localize(None)`` they collapse
  to identical naive timestamps.

  Previously we dropped the duplicates with ``keep="first"``, which lost
  2 bars per DST event (~2 bars/year × N years of history) and shifted
  WR's bar_index out of alignment with PineScript (which keeps both
  bars). That tiny gap accumulates and breaks bar-index parity in
  downstream comparisons.

  Now: keep all rows, disambiguate duplicates by adding 1 ns to each
  successive duplicate. Strategy code never inspects sub-second
  precision (only hour/minute/dayofweek), so this is invisible to the
  strategy but preserves bar-count parity with PineScript.
  """
  if df.index.tz is None:
    df.index = df.index.tz_localize("UTC")
  df.index = df.index.tz_convert("America/New_York").tz_localize(None)
  if df.index.has_duplicates:
    ts_ns = df.index.values.copy()
    seen: dict[int, int] = {}
    for i in range(len(ts_ns)):
      key = int(ts_ns[i].astype("datetime64[ns]").astype("int64"))
      n = seen.get(key, 0)
      if n > 0:
        ts_ns[i] = ts_ns[i] + np.timedelta64(n, "ns")
      seen[key] = n + 1
    df.index = pd.DatetimeIndex(ts_ns)
  return df
