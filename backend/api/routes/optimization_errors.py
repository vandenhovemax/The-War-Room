"""Descriptive error handling for optimization endpoints.

Translates raw Python exceptions into user-friendly messages that explain
WHAT failed and WHY, so non-programmers can understand 500 errors.
"""
from __future__ import annotations

import traceback
from contextlib import contextmanager

from fastapi import HTTPException

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# --- Descriptive error map ---
# Maps common exception patterns to plain-English explanations.
_ERROR_PATTERNS: list[tuple[str, str]] = [
  # Correlation / matrix issues
  ("singular matrix", "The correlation matrix is singular — some assets have identical or zero-variance returns. Try removing duplicate or flat assets."),
  ("LinAlgError", "Linear algebra error in the optimizer — likely caused by assets with identical or constant returns."),
  ("is not in the index", "One or more expected columns are missing from the data. A batch may have been deleted or its assets no longer match."),
  ("not in index", "A referenced asset column was not found in the PnL matrix. This usually means a batch was modified or deleted after it was selected."),
  ("KeyError", "An asset or column was referenced but does not exist in the dataset. Check that all selected batches/portfolios still have data."),

  # Empty / insufficient data
  ("No objects to concatenate", "No data to combine — all selected batches or portfolios returned empty results after filtering."),
  ("zero-size array", "One of the datasets became empty after quality filters were applied. Try loosening Min Trades, Min PF, or Min Win Rate."),
  ("cannot reindex", "Data alignment failed — date ranges between batches don't overlap. Make sure batches share overlapping time periods."),
  ("Length of values", "Mismatch between number of assets and data rows. This usually means a batch has corrupted or incomplete trade data."),

  # Division / math
  ("division by zero", "Division by zero during metrics calculation — likely an asset with zero trades or zero loss (infinite PF)."),
  ("ZeroDivisionError", "Division by zero — an asset may have no losing trades (infinite profit factor) or zero returns."),

  # Numpy / Pandas
  ("NaN", "Calculation produced NaN (not a number) — typically from assets with zero variance or missing data."),
  ("infinity", "Calculation produced infinity — usually from dividing by a very small or zero value in metrics."),

  # Type errors
  ("NoneType", "A required value was None (missing). This may mean a trade has no exit_time, no net_profit, or a batch reference is broken."),
  ("unsupported operand type", "Type mismatch during calculation — likely a None value where a number was expected. Check for incomplete trade data."),
  ("float() argument", "Could not convert a value to a number — a trade field may contain invalid data."),

  # Memory / performance
  ("MemoryError", "Ran out of memory — too many assets or too many combinations to evaluate. Try reducing the number of batches or increasing pool filters."),
  ("Timeout", "The optimization took too long. Try fewer batches, fewer assets, or increase quality filters to reduce the search space."),

  # Index / bounds
  ("index out of range", "Array index out of bounds — likely a batch or portfolio with zero qualifying assets after filters."),
  ("IndexError", "Tried to access data that doesn't exist — probably a window or batch ended up with zero assets after filtering."),

  # Import errors
  ("ModuleNotFoundError", "A required Python module is missing. The backend may need its dependencies reinstalled."),
  ("ImportError", "Failed to import a required component. Try restarting the backend."),
]


def _classify_error(exc: Exception) -> str:
  """Turn a raw exception into a user-friendly description."""
  exc_str = str(exc)
  exc_type = type(exc).__name__

  # Check against known patterns
  for pattern, description in _ERROR_PATTERNS:
    if pattern in exc_str or pattern in exc_type:
      return description

  # Fallback: return the raw error with type name
  return f"{exc_type}: {exc_str}"


@contextmanager
def optimization_stage(stage_name: str, endpoint_name: str):
  """Context manager that catches exceptions within a named pipeline stage.

  Usage:
    with optimization_stage("Loading trades from batches", "cross_strategy_build"):
      trades = load_trades(...)

  If an exception occurs, it raises an HTTPException(500) with a message like:
    "Cross Strategy Build failed during 'Loading trades from batches':
     The correlation matrix is singular — some assets have identical returns."
  """
  try:
    yield
  except HTTPException:
    raise  # Let 400/404 errors pass through
  except Exception as exc:
    user_msg = _classify_error(exc)
    full_msg = f"{endpoint_name} failed during '{stage_name}': {user_msg}"
    logger.exception(full_msg)
    raise HTTPException(status_code=500, detail=full_msg) from exc


def wrap_optimization_error(endpoint_name: str, exc: Exception) -> HTTPException:
  """Create a descriptive HTTPException from a raw exception.

  Use this in existing catch-all blocks:
    except Exception as exc:
      raise wrap_optimization_error("Cross Strategy Build", exc)
  """
  user_msg = _classify_error(exc)
  full_msg = f"{endpoint_name} failed: {user_msg}"
  logger.exception(full_msg)
  return HTTPException(status_code=500, detail=full_msg)
