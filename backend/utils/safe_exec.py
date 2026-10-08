"""Sandboxed exec() for strategy code.

Validates strategy source via AST inspection (blocks dangerous imports)
and restricts builtins (removes exec/eval/compile/open). Includes a
hash-keyed compilation cache for batch performance.
"""
import ast
import builtins
import hashlib

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Modules that strategy code is NOT allowed to import
_BLOCKED_MODULES = frozenset({
  "os", "sys", "subprocess", "shutil", "pathlib",
  "importlib", "socket", "http", "urllib", "requests",
  "ctypes", "signal", "multiprocessing", "threading",
  "pickle", "shelve", "tempfile", "glob", "io",
  "code", "codeop", "compileall", "py_compile",
  "webbrowser", "antigravity", "ftplib", "smtplib",
  "xmlrpc", "pdb", "runpy",
})

# Builtins to remove from exec context
_BLOCKED_BUILTINS = frozenset({
  "exec", "eval", "compile", "open", "breakpoint",
  "exit", "quit", "input", "__import__",
})

# Cache: source hash → TranspiledStrategy class
_cache: dict[str, type] = {}


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
  """Import function that blocks dangerous modules."""
  root = name.split(".")[0]
  if root in _BLOCKED_MODULES:
    raise ImportError(f"Import of '{name}' is blocked in strategy code")
  return builtins.__import__(name, globals, locals, fromlist, level)


def safe_exec(source: str, label: str = "strategy") -> type | None:
  """Execute strategy source and return the TranspiledStrategy class.

  1. Checks AST for blocked imports (catches `import os`, `from os import ...`)
  2. Provides restricted __import__ (catches `__import__("os")` calls)
  3. Removes exec/eval/compile/open from builtins
  4. Caches compiled classes by source hash

  Returns the TranspiledStrategy class, or None if not defined.
  Raises ValueError for blocked imports or SyntaxError for bad code.
  """
  # Check cache first
  key = hashlib.md5(source.encode()).hexdigest()
  if key in _cache:
    return _cache[key]

  # Step 1: Parse AST and scan for blocked imports
  try:
    tree = ast.parse(source)
  except SyntaxError as e:
    logger.error("Strategy %s has syntax error: %s", label, e)
    raise

  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      for alias in node.names:
        root = alias.name.split(".")[0]
        if root in _BLOCKED_MODULES:
          raise ValueError(
            f"Blocked import '{alias.name}' in {label} (line {node.lineno})"
          )
    elif isinstance(node, ast.ImportFrom):
      if node.module:
        root = node.module.split(".")[0]
        if root in _BLOCKED_MODULES:
          raise ValueError(
            f"Blocked import '{node.module}' in {label} (line {node.lineno})"
          )

  # Step 2: Build restricted builtins
  safe_builtins = {
    k: getattr(builtins, k)
    for k in dir(builtins)
    if not k.startswith("_") and k not in _BLOCKED_BUILTINS
  }
  # Add dunders required for class definitions and imports
  safe_builtins["__import__"] = _restricted_import
  safe_builtins["__build_class__"] = builtins.__build_class__
  safe_builtins["__name__"] = "__main__"

  # Step 3: Execute with restricted globals
  exec_globals: dict = {"__builtins__": safe_builtins}
  exec(source, exec_globals)

  cls = exec_globals.get("TranspiledStrategy")
  if cls is not None:
    _cache[key] = cls
  return cls


def clear_strategy_cache() -> None:
  """Clear the compiled strategy cache (call after retranspile)."""
  _cache.clear()
