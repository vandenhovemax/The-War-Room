"""Backward-compatibility shim — DO NOT DELETE.

Strategies stored in the database have `from backend.transpiler.indicators import ta`
hardcoded in their python_source column. This shim re-exports from the canonical
location so those imports keep working without a DB migration.

Canonical location: backend/backtester/indicators.py
"""
from backend.backtester.indicators import IndicatorLibrary, ta  # noqa: F401
