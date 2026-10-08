"""CS-026: OG v1/v2 are FROZEN pinned A/B experiments.

Decision: freeze + pin (do NOT delete). Reasons:
* Saved research runs were produced with those engines; deleting them would
  silently invalidate stored comparisons.
* The owner has saved runs on these versions and has not migrated them off.

Contract pinned by these tests:

1. All 8 OG-v{1,2} files still exist (no accidental delete).
2. Each file's top docstring names "LEGACY-FROZEN" — a scan tool can find
   them at a glance.
3. The central dispatcher `_get_optimizer_funcs` emits a ``CS-026`` warning
   when engine is ``og_v1`` or ``og_v2``.
4. The beam dispatch in ``cross_strategy.py`` emits a ``CS-026`` warning
   when engine is ``og_v1`` or ``og_v2``.

Any future agent refactoring the frozen files must either regenerate these
banners or delete the whole lineage deliberately (and the test will fail
loudly first).
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path

OG_FILES = [
  "backend/analysis/optimizer_og_v1.py",
  "backend/analysis/optimizer_og_v2.py",
  "backend/analysis/optimizer_split_og_v1.py",
  "backend/analysis/optimizer_split_og_v2.py",
  "backend/analysis/optimizer_tc_og_v1.py",
  "backend/analysis/optimizer_tc_og_v2.py",
  "backend/analysis/beam_optimizer_og_v1.py",
  "backend/analysis/beam_optimizer_og_v2.py",
]


def _repo_root() -> Path:
  # Walk up until we find the `backend` directory (we're in backend/tests/).
  here = Path(__file__).resolve()
  for parent in [here, *here.parents]:
    if (parent / "backend").is_dir():
      return parent
  raise RuntimeError("Cannot locate repo root from test file")


def test_all_og_files_still_exist():
  """Prevent accidental deletion of any pinned A/B variant."""
  root = _repo_root()
  missing = [p for p in OG_FILES if not (root / p).is_file()]
  assert not missing, (
    f"CS-026: {len(missing)} OG v1/v2 file(s) missing — these are FROZEN A/B "
    f"variants pinned for research reproducibility. If you intended to "
    f"delete the lineage, also remove the optimizer_engine 'og_v1'/'og_v2' "
    f"dispatch paths and this test. Missing: {missing}"
  )


def test_all_og_files_carry_frozen_banner():
  """Each OG file must advertise LEGACY-FROZEN at the top of its docstring."""
  root = _repo_root()
  offenders: list[str] = []
  for p in OG_FILES:
    head = (root / p).read_text(encoding="utf-8")[:400]
    if "LEGACY-FROZEN" not in head:
      offenders.append(p)
  assert not offenders, (
    f"CS-026: OG v1/v2 file(s) missing LEGACY-FROZEN banner in their top "
    f"docstring: {offenders}"
  )


def test_diversification_dispatcher_warns_on_og_v1(caplog):
  from backend.analysis import diversification

  importlib.reload(diversification)  # fresh logger state
  with caplog.at_level(logging.WARNING, logger="backend.analysis.diversification"):
    try:
      diversification._get_optimizer_funcs("og_v1")
    except Exception:
      # Import or dispatch may raise in constrained envs — the warning must
      # still fire BEFORE any failure. That's what we're pinning.
      pass
  assert any(
    "CS-026" in r.message for r in caplog.records if r.levelno == logging.WARNING
  ), "CS-026: _get_optimizer_funcs must emit a WARNING mentioning CS-026 on og_v1"


def test_diversification_dispatcher_warns_on_og_v2(caplog):
  from backend.analysis import diversification

  importlib.reload(diversification)
  with caplog.at_level(logging.WARNING, logger="backend.analysis.diversification"):
    try:
      diversification._get_optimizer_funcs("og_v2")
    except Exception:
      pass
  assert any(
    "CS-026" in r.message for r in caplog.records if r.levelno == logging.WARNING
  ), "CS-026: _get_optimizer_funcs must emit a WARNING mentioning CS-026 on og_v2"


def test_cross_strategy_beam_dispatch_source_logs_cs026_on_og_v1_v2():
  """Pin the exact warning block in the HTTP router.

  Using source inspection instead of a live route call: the route pulls a
  DB session, full request model, and task-id state. Inspecting the source
  catches the honour-system exactly as for CS-005/CS-015/CS-016.
  """
  root = _repo_root()
  src = (root / "backend/api/routes/cross_strategy.py").read_text(encoding="utf-8")
  # Both branches must emit the CS-026 warning.
  og_v1_block_ok = 'CS-026 LEGACY-FROZEN: beam optimizer_engine=\'og_v1\'' in src
  og_v2_block_ok = 'CS-026 LEGACY-FROZEN: beam optimizer_engine=\'og_v2\'' in src
  assert og_v1_block_ok, (
    "cross_strategy.py beam dispatch must log CS-026 when engine='og_v1'"
  )
  assert og_v2_block_ok, (
    "cross_strategy.py beam dispatch must log CS-026 when engine='og_v2'"
  )


def test_current_engine_path_does_not_log_cs026_warning(caplog):
  """Regression: the default/current engine path must NOT spam CS-026 warnings."""
  from backend.analysis import diversification

  importlib.reload(diversification)
  with caplog.at_level(logging.WARNING, logger="backend.analysis.diversification"):
    diversification._get_optimizer_funcs("current")
    diversification._get_optimizer_funcs("fast")
  assert not any("CS-026" in r.message for r in caplog.records), (
    "CS-026 warning must fire ONLY for og_v1/og_v2 — not for current/fast"
  )
