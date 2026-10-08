"""CS-046: audit-flagged ``except Exception`` clauses must stay narrowed.

CLAUDE.md calls out "silent exception swallowing" by name. A
try/except that catches everything:

  * hides typos in new migrations → schema drift,
  * hides numeric bugs in scoring → metrics silently default to 0,
  * hides WS schema changes → events drop without trace.

Each audit-cited site has been narrowed to the specific exception
class it can legitimately raise. This test locks the invariant by
inspecting the source of the containing function for a narrowed
``except (...)`` clause and asserting that no bare ``except
Exception`` or bare ``except:`` remains.
"""
from __future__ import annotations

import inspect
import re


def _code_only(src: str) -> str:
  """Strip comments — we only care about live control-flow tokens."""
  lines = []
  for ln in src.splitlines():
    stripped = ln.lstrip()
    if stripped.startswith("#"):
      continue
    # Remove trailing inline comments conservatively (only when #
    # follows whitespace, to avoid munging strings).
    lines.append(re.sub(r"\s+#.*$", "", ln))
  return "\n".join(lines)


def _assert_no_bare_except(fn, label: str):
  """Assert the given callable's source contains no
  ``except Exception`` or ``except:`` in live code.
  """
  src = _code_only(inspect.getsource(fn))
  assert "except Exception" not in src, (
    f"CS-046 REGRESSION: {label} still contains ``except Exception`` "
    "— narrow it to the specific classes that legitimately occur. "
    "CLAUDE.md flags silent exception swallowing as a class-one bug "
    "on any path touching migrations, scoring, or the order path."
  )
  assert not re.search(r"^\s*except\s*:", src, flags=re.MULTILINE), (
    f"CS-046 REGRESSION: {label} still contains a bare ``except:``."
  )


def test_init_db_migrations_use_narrowed_except():
  """The four audit-cited migration backfills in ``init_db`` must
  each catch ``SQLAlchemyError`` specifically — table/column missing
  or DB locked is legitimate, but silencing every possible bug
  (typos, AttributeError) is what CLAUDE.md calls out as class-one.

  The audit named four concrete sites (database.py:394, 412, 421,
  612). After the fix there must be at least four
  ``except SQLAlchemyError`` clauses in ``init_db`` — one per site.
  Other pre-existing narrow/broad ``except Exception`` clauses that
  the audit did NOT name are out of scope for this regression and
  remain as-is.
  """
  from backend.db import database as db_mod
  src = _code_only(inspect.getsource(db_mod.init_db))
  assert "SQLAlchemyError" in src, (
    "CS-046 REGRESSION: init_db no longer imports or catches "
    "``SQLAlchemyError``. The migration backfills need to catch DB "
    "errors specifically so typos and programming bugs surface."
  )
  narrowed_count = src.count("except SQLAlchemyError")
  assert narrowed_count >= 4, (
    f"CS-046 REGRESSION: init_db only has {narrowed_count} "
    "``except SQLAlchemyError`` clauses — expected at least 4 (one "
    "for each audit-cited migration backfill: min_adv, bybit_limit, "
    "max_margin_per_trade, paper-portfolio repair). A revert to "
    "``except Exception`` on any of those would silently swallow "
    "real bugs and leave the DB in an inconsistent state."
  )


def test_rich_metrics_from_daily_stability_except_is_narrowed():
  """The R² stability computation must catch only numerical errors
  (``LinAlgError``, ``ValueError``, ``FloatingPointError``), not
  every bug that could return ``stability=0``.
  """
  from backend.analysis import batch_selection as bs_mod
  _assert_no_bare_except(
    bs_mod._rich_metrics_from_daily,
    "batch_selection._rich_metrics_from_daily",
  )


def test_precompute_cache_load_save_except_is_narrowed():
  """Cache load/save must catch filesystem/pickling errors only.
  Anything else is a programming bug that should surface immediately.
  """
  from backend.analysis import batch_selection as bs_mod
  _assert_no_bare_except(
    bs_mod.precompute_pnl_structures,
    "batch_selection.precompute_pnl_structures",
  )
  src = inspect.getsource(bs_mod.precompute_pnl_structures)
  # Both the load path (UnpicklingError/EOFError) and the save path
  # (PicklingError/OSError) must remain typed.
  assert "pickle.UnpicklingError" in src or "UnpicklingError" in src, (
    "CS-046 REGRESSION: the cache-load except no longer mentions "
    "UnpicklingError. A broad except would silently rebuild the cache "
    "on any error — including a programming bug that would then "
    "keep triggering forever."
  )
  assert "PicklingError" in src or "OSError" in src, (
    "CS-046 REGRESSION: the cache-save except is no longer typed. "
    "Broad except would hide unpicklable-object bugs forever."
  )


def test_ws_is_alive_except_is_narrowed():
  """``_is_ws_alive`` probes pybit internals — the only legitimate
  failure is ``AttributeError`` if pybit renames an attribute. Any
  other exception should surface so reconnect logic can react.
  """
  from backend.exchange import ws_feeds as ws_mod
  _assert_no_bare_except(
    ws_mod.BybitWSManager._is_ws_alive,
    "ws_feeds.BybitWSManager._is_ws_alive",
  )
  src = inspect.getsource(ws_mod.BybitWSManager._is_ws_alive)
  assert "AttributeError" in src, (
    "CS-046 REGRESSION: _is_ws_alive no longer catches "
    "``AttributeError`` specifically. The probe will either "
    "re-swallow everything or lose its handling for pybit renames."
  )
