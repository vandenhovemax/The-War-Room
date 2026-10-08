"""CS-034: ``load_trades_lightweight`` schema introspection must NOT
catch ``Exception`` broadly.

The loader introspects two tables to decide whether the DB has had
its latest migration applied:

  * ``batch_groups`` — has columns like ``bybit_limit`` /
    ``pnl_multiplier``?
  * ``batch_backtest_runs`` — has columns like ``group_id`` /
    ``max_margin_per_trade``?

Pre-migration DBs raise ``NoSuchTableError`` / ``OperationalError``.
Those two cases must degrade gracefully (treat as pre-migration).

Everything else (a transient DB lock, a genuine driver bug, a typo
in a column name) MUST propagate — otherwise WFAS and the dashboard
return numerically wrong ``$PnL`` values AS IF successful (missing
``pnl_multiplier`` silently collapses scaled trades to raw).

This test locks the invariant by source inspection.
"""
from __future__ import annotations

import inspect

from backend.analysis import batch_selection


def test_load_trades_lightweight_does_not_swallow_generic_exception():
  """The two schema-probe try/except blocks must be narrowed to
  ``(NoSuchTableError, OperationalError)``.
  """
  src = inspect.getsource(batch_selection.load_trades_lightweight)

  # Both probes must be present, narrowed.
  assert src.count("except (NoSuchTableError, OperationalError)") >= 2, (
    "CS-034 REGRESSION: load_trades_lightweight no longer narrows its "
    "two schema-probe except blocks to NoSuchTableError + "
    "OperationalError. A bare ``except Exception`` silently collapses "
    "migrated DBs to 'pre-migration' semantics — WFAS and the "
    "dashboard would return numerically wrong $PnL as if successful."
  )

  # The bare forms must be absent from this function.
  # (We allow ``except Exception`` in comment text but not as a
  # control-flow statement.)
  code_lines = [ln for ln in src.splitlines()
                if not ln.lstrip().startswith("#")]
  code_only = "\n".join(code_lines)

  assert "except Exception" not in code_only, (
    "CS-034 REGRESSION: ``except Exception`` reappeared in "
    "load_trades_lightweight's schema-probe path. That was exactly "
    "the bug: a transient DB lock silently degraded PnL to raw. "
    "Re-narrow to (NoSuchTableError, OperationalError)."
  )
  assert "except:" not in code_only, (
    "CS-034 REGRESSION: bare ``except:`` reappeared in "
    "load_trades_lightweight."
  )
