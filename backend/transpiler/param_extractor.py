"""Backward-compatibility shim — safe to remove once no code imports from here.

Canonical location: backend/utils/param_extractor.py
"""
from backend.utils.param_extractor import (  # noqa: F401
  extract_from_python,
  extract_params_from_json_block,
)
