"""CS-053: ``engine.py`` and ``portfolio_engine.py`` form a module
cycle — ``engine`` calls into ``portfolio_engine`` via a lazy import
at the call site, and ``portfolio_engine`` imports from ``engine``
at module top-level.

The audit's concern is maintainability: a well-meaning maintainer
who "cleans up" and moves ``engine``'s lazy import to the top will
break startup. Until that structural fix happens (promote the
bar-loop building blocks to a public step_bar() API and move
``_sanitize_dst_timestamps`` to a shared module), we at minimum lock
the invariant that importing each module standalone still works —
the cycle must remain resolvable.
"""
from __future__ import annotations

import importlib
import sys


def test_engine_module_imports_standalone():
  """Importing the engine from a cold state must succeed. If someone
  hoists the lazy import to the top this test fails before startup.
  """
  # Force a cold reload to catch a hoisted top-level import.
  for mod in [
    "backend.backtester.engine",
    "backend.backtester.portfolio_engine",
  ]:
    if mod in sys.modules:
      del sys.modules[mod]

  importlib.import_module("backend.backtester.engine")


def test_portfolio_engine_module_imports_standalone():
  for mod in [
    "backend.backtester.engine",
    "backend.backtester.portfolio_engine",
  ]:
    if mod in sys.modules:
      del sys.modules[mod]

  importlib.import_module("backend.backtester.portfolio_engine")


def test_both_orders_work():
  """Import engine then portfolio_engine; and vice versa. Both must
  succeed so the cycle is resolvable regardless of which module a
  downstream caller imports first.
  """
  for first, second in [
    ("backend.backtester.engine", "backend.backtester.portfolio_engine"),
    ("backend.backtester.portfolio_engine", "backend.backtester.engine"),
  ]:
    for mod in (first, second):
      if mod in sys.modules:
        del sys.modules[mod]
    importlib.import_module(first)
    importlib.import_module(second)
