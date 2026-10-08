"""CS-022: no sync blockers inside ``async def`` functions.

Forbidden inside any ``async def`` under ``backend/``:

  * ``httpx.get/post/put/delete/Client(...)`` — sync HTTP stalls the
    event loop. Use ``httpx.AsyncClient`` or dispatch via
    ``loop.run_in_executor`` / ``asyncio.to_thread``.
  * ``time.sleep(...)`` — blocks the loop. Use ``asyncio.sleep``.
  * ``requests.get/post(...)`` — sync library; same problem.

Sync routes (``def my_route``) are FINE — FastAPI runs them in a
thread pool. Module-level helpers that happen to use sync httpx are
fine AS LONG AS no async caller invokes them directly. This test
walks the AST, tracks whether each ``Call`` node lies inside an
``AsyncFunctionDef``, and flags the forbidden sync APIs.

The recursive check walks back into nested sync ``def`` inside async
defs (sync nested defs are free to block — they're closures scheduled
elsewhere). That matches the actual semantic.
"""
from __future__ import annotations

import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[1]

FORBIDDEN_CALLS = {
  "httpx.get", "httpx.post", "httpx.put", "httpx.delete",
  "httpx.patch", "httpx.head", "httpx.request",
  "httpx.Client",
  "time.sleep",
  "requests.get", "requests.post", "requests.put", "requests.delete",
  "requests.patch", "requests.request",
}


def _scan_file(path: pathlib.Path) -> list[tuple[int, str]]:
  """Return (lineno, call_repr) hits inside ``async def`` in this file."""
  try:
    tree = ast.parse(path.read_text(encoding="utf-8"))
  except (SyntaxError, UnicodeDecodeError):
    return []

  hits: list[tuple[int, str]] = []

  class V(ast.NodeVisitor):
    def __init__(self):
      self.async_depth = 0

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
      self.async_depth += 1
      self.generic_visit(node)
      self.async_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef):
      # A sync def inside an async def escapes the async context (it
      # runs wherever the closure is dispatched — usually a thread).
      saved = self.async_depth
      self.async_depth = 0
      self.generic_visit(node)
      self.async_depth = saved

    def visit_Call(self, node: ast.Call):
      if self.async_depth > 0:
        try:
          repr_ = ast.unparse(node.func)
        except Exception:
          repr_ = ""
        if repr_ in FORBIDDEN_CALLS:
          hits.append((node.lineno, repr_))
      self.generic_visit(node)

  V().visit(tree)
  return hits


def test_no_sync_httpx_or_time_sleep_inside_async_def():
  offenders: list[str] = []
  for py_file in BACKEND.rglob("*.py"):
    parts = set(py_file.parts)
    if "tests" in parts or "__pycache__" in parts:
      continue
    for lineno, call in _scan_file(py_file):
      rel = py_file.relative_to(BACKEND)
      offenders.append(f"  {rel}:{lineno} — {call}(...)")

  assert not offenders, (
    "CS-022 REGRESSION: sync HTTP / sync sleep found inside an "
    "``async def`` — these block the event loop under concurrent "
    "load. Use httpx.AsyncClient, asyncio.to_thread, or asyncio.sleep.\n"
    + "\n".join(offenders)
  )
