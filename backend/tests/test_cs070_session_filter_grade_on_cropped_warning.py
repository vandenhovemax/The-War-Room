"""CS-070: ``session_filter_grade_on_cropped`` on the batch-selection
WFO branch must emit a loud backend warning, because it is ignored.

Background — the audit found:

  * the request schema accepts the flag,
  * ``wfo.py`` persists it into saved-run settings,
  * but ``BatchSelectionConfig`` has no such field and
    ``run_batch_selection`` never reads it.

Option (b) from the audit (the safer one-file change) is to surface
the no-op explicitly so a non-coder user is not misled into
concluding "the knob does nothing therefore the effect is noise."

This test inspects the source of the batch-selection branch and
asserts the warning is present. A behavioural test (actually
invoking the route) would require the full FastAPI TestClient plus
a real DB — overkill for a log-emission invariant.
"""
from __future__ import annotations

import inspect
import re

from backend.api.routes import wfo as wfo_mod


def test_wfo_batch_selection_warns_when_grade_on_cropped_set():
  src = inspect.getsource(wfo_mod)

  # Locate the batch-selection branch.
  bs_branch_match = re.search(
    r'if req\.wfo_mode == "batch_selection":', src
  )
  assert bs_branch_match, (
    "CS-070 FIXTURE BROKEN: cannot find the "
    "``if req.wfo_mode == 'batch_selection':`` branch in wfo.py."
  )
  # Scan the ~60 lines immediately inside that branch for the guard.
  tail = src[bs_branch_match.start() : bs_branch_match.start() + 4000]

  assert "session_filter_grade_on_cropped" in tail, (
    "CS-070 REGRESSION: the batch-selection branch no longer "
    "references session_filter_grade_on_cropped at all. The field "
    "is still accepted by WFOAssetSelectionRequest and persisted "
    "into saved-run settings, which means a user can flip the toggle "
    "and receive identical numbers with no indication why. Restore "
    "the explicit warning inside the branch."
  )

  assert "IGNORED" in tail, (
    "CS-070 REGRESSION: the warning no longer says IGNORED. The "
    "copy was chosen to make the no-op impossible to miss in the "
    "logs. Revert any change that softens the message — a non-coder "
    "user must be able to grep the backend log and see that this "
    "knob is a no-op on this path."
  )

  # And the emit must be unconditional-on-value (True-branch only).
  assert "if req.session_filter_grade_on_cropped" in tail, (
    "CS-070 REGRESSION: the warning is not gated on the flag being "
    "True. Either the flag is silently ignored again (regression) "
    "or every WFO run now logs the message (spam)."
  )
