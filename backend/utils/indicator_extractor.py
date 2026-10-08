"""
Extract indicator calls (ta.xxx) from strategy Python source code.

Parses the AST to find all ta.xxx() calls, resolves numeric parameters
(including self.input() defaults), and returns a list of indicator specs
that can be computed generically on any OHLCV DataFrame.
"""
import ast
from typing import Any


# ── Source column mapping ────────────────────────────────────────────
_SERIES_MAP = {
    "close_series": "close",
    "open_series": "open",
    "high_series": "high",
    "low_series": "low",
    "volume_series": "volume",
}

# ── Indicator category: overlay (on price) vs oscillator (separate pane)
OVERLAYS = {
    "sma", "ema", "wma", "hma", "alma", "vwma", "rma",
    "bb", "kc", "donchian", "ichimoku", "supertrend", "sar", "vwap",
}
OSCILLATORS = {
    "rsi", "macd", "stoch", "cci", "mfi", "dmi", "atr",
}

# ── Color palettes ──────────────────────────────────────────────────
OVERLAY_COLORS = [
    "#2962FF", "#FF6D00", "#E040FB", "#00BCD4",
    "#FFEB3B", "#4CAF50", "#FF4081", "#00E5FF",
]
OSCILLATOR_COLORS = [
    "#7B1FA2", "#FF5722", "#009688", "#3F51B5",
]


def extract_indicators(source: str) -> list[dict]:
    """Extract ta.xxx() indicator calls from strategy Python source.

    Returns a list of dicts like:
      [
        {"name": "ema", "args": [20], "source": "close", "category": "overlay"},
        {"name": "rsi", "args": [14], "source": "close", "category": "oscillator"},
        {"name": "bb",  "args": [20, 2.0], "source": "close", "category": "overlay"},
      ]
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # Pass 1: build map of variable → default value from self.input() calls
    input_defaults = _collect_input_defaults(tree)

    # Pass 2: find all ta.xxx() calls
    indicators: list[dict] = []
    seen: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_ta_call(node.func):
            continue

        name = node.func.attr  # type: ignore[union-attr]
        if name in ("crossover", "crossunder", "change", "barssince",
                     "valuewhen", "cum", "highest", "lowest",
                     "pivothigh", "pivotlow", "tr"):
            continue  # utility functions, not visual indicators

        # Resolve args to numeric values
        numeric_args = _resolve_args(node, input_defaults)

        # Detect source column from first arg
        source_col = _detect_source(node)

        # Categorize. Unknown indicators default to "oscillator" so they
        # don't pollute the chart's price scale (would compress candles).
        category = "overlay" if name in OVERLAYS else "oscillator"

        # Deduplicate by name + args
        key = f"{name}:{','.join(str(a) for a in numeric_args)}"
        if key in seen:
            continue
        seen.add(key)

        indicators.append({
            "name": name,
            "args": numeric_args,
            "source": source_col,
            "category": category,
        })

    return indicators


def _is_ta_call(func: ast.AST) -> bool:
    """Check if call target is ta.xxx."""
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "ta"
    )


def _collect_input_defaults(tree: ast.AST) -> dict[str, Any]:
    """Find all `var = self.input(defval=X, ...)` and map var name → X."""
    defaults: dict[str, Any] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue

        # Get target variable name
        target = node.targets[0]
        if isinstance(target, ast.Name):
            var_name = target.id
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            var_name = f"self.{target.attr}"
        else:
            continue

        # Check if RHS is self.input(...)
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        if not (isinstance(call.func, ast.Attribute)
                and call.func.attr == "input"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "self"):
            continue

        # Extract defval
        defval = _extract_defval(call)
        if defval is not None:
            defaults[var_name] = defval

    return defaults


def _extract_defval(call: ast.Call) -> Any:
    """Extract defval from a self.input() call."""
    # Check keyword defval=
    for kw in call.keywords:
        if kw.arg == "defval" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    # Check first positional (could be defval or title)
    if call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, (int, float)):
            return first.value
        # If first is string and second is numeric: input("Title", 14)
        if (isinstance(first, ast.Constant) and isinstance(first.value, str)
                and len(call.args) > 1):
            second = call.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, (int, float)):
                return second.value
    return None


def _resolve_args(call: ast.Call, input_defaults: dict) -> list[float]:
    """Resolve numeric arguments from a ta.xxx() call."""
    result: list[float] = []

    for arg in call.args:
        val = _resolve_value(arg, input_defaults)
        if val is not None:
            result.append(val)

    for kw in call.keywords:
        val = _resolve_value(kw.value, input_defaults)
        if val is not None:
            result.append(val)

    return result


def _resolve_value(node: ast.AST, input_defaults: dict) -> float | None:
    """Try to resolve an AST node to a numeric value."""
    # Direct constant
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)

    # Variable reference → check input_defaults
    if isinstance(node, ast.Name) and node.id in input_defaults:
        v = input_defaults[node.id]
        if isinstance(v, (int, float)):
            return float(v)

    # self.xxx attribute → check input_defaults
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        key = f"self.{node.attr}"
        if key in input_defaults:
            v = input_defaults[key]
            if isinstance(v, (int, float)):
                return float(v)

    # Inline self.input(defval=X) call
    if isinstance(node, ast.Call):
        func = node.func
        if (isinstance(func, ast.Attribute)
                and func.attr == "input"
                and isinstance(func.value, ast.Name)
                and func.value.id == "self"):
            defval = _extract_defval(node)
            if isinstance(defval, (int, float)):
                return float(defval)

    # Negative number: UnaryOp(USub, Constant)
    if (isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))):
        return -float(node.operand.value)

    return None


def _detect_source(call: ast.Call) -> str:
    """Detect which OHLCV column the first arg references."""
    if not call.args:
        return "close"

    first = call.args[0]

    # self.close_series → "close"
    if (isinstance(first, ast.Attribute)
            and isinstance(first.value, ast.Name)
            and first.value.id == "self"):
        return _SERIES_MAP.get(first.attr, "close")

    # Variable name heuristic
    if isinstance(first, ast.Name):
        name = first.id.lower()
        if "high" in name:
            return "high"
        if "low" in name:
            return "low"
        if "open" in name:
            return "open"
        if "volume" in name or "vol" in name:
            return "volume"

    return "close"
