"""TV Strategy Config CRUD routes."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.analysis.strategy_config import (
    delete_strategy,
    get_default_config,
    list_strategies,
    load_strategy,
    save_strategy,
)

router = APIRouter()


class StrategyConfigPayload(BaseModel):
    config: dict


@router.get("/list")
def list_all():
    """List all saved TV strategy configs."""
    strategies = list_strategies()
    return {"strategies": [{"filename": f, "name": n} for f, n in strategies]}


@router.get("/default")
def default_config():
    """Get the default strategy config template."""
    return {"config": get_default_config()}


@router.get("/{filename}")
def get_one(filename: str):
    """Load a specific strategy config."""
    config = load_strategy(filename)
    if config is None:
        raise HTTPException(404, f"Strategy not found: {filename}")
    return {"config": config, "filename": filename}


@router.post("/save")
def save(payload: StrategyConfigPayload):
    """Save a strategy config. Returns the filename used."""
    filename = save_strategy(payload.config)
    return {"filename": filename, "status": "saved"}


@router.delete("/{filename}")
def remove(filename: str):
    """Delete a strategy config."""
    if not delete_strategy(filename):
        raise HTTPException(404, f"Strategy not found: {filename}")
    return {"status": "deleted", "filename": filename}


class FolderImportRequest(BaseModel):
    folder_path: str
    name: str = ""
    dir_mode: str = "single"  # "single" or "split"


@router.post("/import-folder")
def import_from_folder(req: FolderImportRequest):
    """Create a TV strategy config from a local folder path.

    For dir_mode="single", folder_path is used as single_dir.
    For dir_mode="split", expects Long/ and Short/ subfolders.
    """
    if not os.path.exists(req.folder_path):
        raise HTTPException(404, f"Folder not found: {req.folder_path}")

    xlsx_files = [f for f in os.listdir(req.folder_path) if f.lower().endswith(".xlsx")]
    if not xlsx_files:
        # Check for Long/Short subfolders
        long_dir = os.path.join(req.folder_path, "Long")
        short_dir = os.path.join(req.folder_path, "Short")
        has_split = os.path.isdir(long_dir) or os.path.isdir(short_dir)
        if not has_split:
            raise HTTPException(400, "No .xlsx files found in folder")

    # Auto-detect split vs single
    long_dir = os.path.join(req.folder_path, "Long")
    short_dir = os.path.join(req.folder_path, "Short")
    has_split = os.path.isdir(long_dir) and os.path.isdir(short_dir)

    strategy_name = req.name or os.path.basename(req.folder_path) or "TV Import"
    config = get_default_config()
    config["name"] = strategy_name

    if has_split and req.dir_mode == "split":
        config["dir_mode"] = "split"
        config["long_dir"] = long_dir
        config["short_dir"] = short_dir
    else:
        config["dir_mode"] = "single"
        config["single_dir"] = req.folder_path

    filename = save_strategy(config)
    return {
        "filename": filename,
        "name": strategy_name,
        "dir_mode": config["dir_mode"],
        "xlsx_count": len(xlsx_files),
        "status": "created",
    }
