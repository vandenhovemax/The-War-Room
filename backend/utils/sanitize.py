"""Shared JSON sanitization — convert inf/nan/numpy types to JSON-safe values."""
from __future__ import annotations

import math

import numpy as np


def sanitize_for_json(obj: object) -> object:
  """Recursively convert numpy types and inf/nan to JSON-safe values.

  - numpy int/float/bool → Python equivalents
  - numpy ndarray → list (recursed)
  - float inf / nan → None
  - dicts and lists recursed
  """
  if isinstance(obj, dict):
    return {k: sanitize_for_json(v) for k, v in obj.items()}
  if isinstance(obj, list):
    return [sanitize_for_json(v) for v in obj]
  if isinstance(obj, np.integer):
    return int(obj)
  if isinstance(obj, np.floating):
    v = float(obj)
    return None if (math.isinf(v) or math.isnan(v)) else v
  if isinstance(obj, np.bool_):
    return bool(obj)
  if isinstance(obj, np.ndarray):
    return sanitize_for_json(obj.tolist())
  if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
    return None
  return obj
