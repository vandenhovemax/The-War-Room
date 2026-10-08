"""
Extract parameter definitions from Python strategy code.
Parses self.input() calls to build a list of parameter definitions.

Used by all strategy creation flows (AI, paste).
Relocated from backend/transpiler/param_extractor.py on 2026-03-13.
"""
import ast
import json
import re
from typing import Any


def extract_from_python(source: str) -> list[dict]:
  """Extract parameter definitions from Python strategy source.

  Finds all self.input(...) calls and extracts:
    - name (from title= kwarg)
    - default value (from defval= kwarg or first positional arg)
    - type (inferred from default value)
    - min/max/step/options (from kwargs if present)

  Returns a list of parameter definition dicts.
  """
  try:
    tree = ast.parse(source)
  except SyntaxError:
    return []

  params: list[dict] = []
  _walk_tree(tree, params)
  return params


def _walk_tree(node: ast.AST, params: list[dict]) -> None:
  """Recursively walk AST looking for self.input() calls."""
  for child in ast.walk(node):
    if not isinstance(child, ast.Call):
      continue
    # Match self.input(...)
    if not _is_self_input(child.func):
      continue
    param = _parse_input_call(child)
    if param and param.get("name"):
      # Avoid duplicates by title
      if not any(p["name"] == param["name"] for p in params):
        params.append(param)


def _is_self_input(func: ast.AST) -> bool:
  """Check if a call target is self.input."""
  if isinstance(func, ast.Attribute):
    if func.attr == "input" and isinstance(func.value, ast.Name):
      return func.value.id == "self"
  return False


def _parse_input_call(call: ast.Call) -> dict | None:
  """Parse a self.input(...) call into a parameter definition."""
  kwargs: dict[str, Any] = {}

  # Extract keyword arguments
  for kw in call.keywords:
    if kw.arg and isinstance(kw.value, ast.Constant):
      kwargs[kw.arg] = kw.value.value
    elif kw.arg == "options" and isinstance(kw.value, ast.List):
      kwargs["options"] = [
        elt.value for elt in kw.value.elts
        if isinstance(elt, ast.Constant)
      ]

  # Extract positional args: self.input(defval, title, ...)
  positional = []
  for arg in call.args:
    if isinstance(arg, ast.Constant):
      positional.append(arg.value)

  # Resolve defval: keyword takes priority, then first positional
  # PineScript convention: input(defval, title) — but some strategies
  # use input(title, defval) (input.int style). Detect and swap if
  # first positional is a string and second is numeric.
  if (
    "defval" not in kwargs
    and "title" not in kwargs
    and len(positional) >= 2
    and isinstance(positional[0], str)
    and isinstance(positional[1], (int, float, bool))
  ):
    # Swapped order: input("Title", 14) — title first, defval second
    defval = positional[1]
    title = positional[0]
  else:
    defval = kwargs.get("defval", positional[0] if positional else None)
    title = kwargs.get("title", positional[1] if len(positional) > 1 else "")

  if not title:
    return None

  # Infer type from default value
  param_type = _infer_type(defval)

  result: dict[str, Any] = {
    "name": title,
    "type": param_type,
    "default": defval if defval is not None else 0,
  }

  # Add optional constraints
  if "minval" in kwargs:
    result["min"] = kwargs["minval"]
  if "maxval" in kwargs:
    result["max"] = kwargs["maxval"]
  if "step" in kwargs:
    result["step"] = kwargs["step"]
  if "options" in kwargs:
    result["options"] = kwargs["options"]

  return result


def _infer_type(value: Any) -> str:
  """Infer parameter type from its default value."""
  if isinstance(value, bool):
    return "bool"
  if isinstance(value, int):
    return "int"
  if isinstance(value, float):
    return "float"
  if isinstance(value, str):
    return "string"
  return "float"


def extract_params_from_json_block(text: str) -> list[dict]:
  """Extract PARAMETERS: JSON block from Claude AI output.

  Looks for a block like:
    PARAMETERS:
    [{"name": "Fast Length", ...}, ...]
  """
  # Find PARAMETERS: marker followed by a JSON array
  match = re.search(r'PARAMETERS:\s*\n\s*(\[[\s\S]*?\])', text)
  if not match:
    return []
  try:
    params = json.loads(match.group(1))
    if isinstance(params, list):
      return params
  except (json.JSONDecodeError, ValueError):
    pass
  return []
