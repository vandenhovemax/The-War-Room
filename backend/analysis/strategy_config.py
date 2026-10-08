"""Strategy config CRUD — save/load/list/delete JSON strategy configs.

Each strategy is stored as a JSON file in data/strategies/.
Ported from market_structure_dashboard/core/strategy_manager.py.
"""
from __future__ import annotations

import json
import os
import re

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRATEGIES_DIR = os.path.join(_BACKEND_ROOT, "data", "strategies")


def _ensure_dir() -> None:
    os.makedirs(STRATEGIES_DIR, exist_ok=True)


def list_strategies() -> list[tuple[str, str]]:
    """Return list of (filename, display_name) sorted by name."""
    _ensure_dir()
    strategies = []
    for f in os.listdir(STRATEGIES_DIR):
        if not f.endswith(".json"):
            continue
        path = os.path.join(STRATEGIES_DIR, f)
        try:
            with open(path, "r") as fh:
                data = json.load(fh)
                name = data.get("name", f.replace(".json", ""))
        except (json.JSONDecodeError, OSError):
            name = f.replace(".json", "")
        strategies.append((f, name))
    return sorted(strategies, key=lambda x: x[1].lower())


def load_strategy(filename: str) -> dict | None:
    """Load a strategy config from JSON. Returns dict or None."""
    _ensure_dir()
    path = os.path.join(STRATEGIES_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def save_strategy(config: dict) -> str:
    """Save a strategy config. Returns the filename used."""
    _ensure_dir()

    if "filename" in config:
        filename = config.pop("filename")
    else:
        safe = re.sub(r"[^\w\s.\-]", "", config.get("name", "Strategy")).strip()
        safe = re.sub(r"\s+", "_", safe)
        filename = f"{safe}.json"
        base = filename
        counter = 2
        while os.path.exists(os.path.join(STRATEGIES_DIR, filename)):
            filename = base.replace(".json", f"_{counter}.json")
            counter += 1

    path = os.path.join(STRATEGIES_DIR, filename)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return filename


def delete_strategy(filename: str) -> bool:
    """Delete a strategy file. Returns True if deleted."""
    path = os.path.join(STRATEGIES_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def get_default_config() -> dict:
    """Default strategy config with all fields."""
    return {
        "name": "New Strategy",
        "dir_mode": "split",
        "long_dir": "",
        "short_dir": "",
        "single_dir": "",
        "group_multi_tp": True,
        "min_trades": 3,
        "min_pf": 1.3,
        "corr_tf": "ME",
        "opt_universe": 430,
        "opt_initial_pf1": 164,
        "opt_candles": 20000,
        "opt_commission": 0.08,
        "exclude_seen": "",
        "wfe_enabled": False,
        "wfe_tf": "30m",
        "unseen_candles": 4000,
        "total_samples": 20000,
        "min_wfe": 0.6,
        "max_wfe": 10.0,
        "min_oos_pf": 1.0,
        "benchmark_enabled": False,
        "benchmark_apply_filter": False,
        "strategy_style": "Trend Following / Breakout",
    }
