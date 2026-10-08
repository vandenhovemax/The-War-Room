"""TV Import routes — scan directories and upload Excel files."""
from __future__ import annotations

import os

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from backend.analysis.tv_data_loader import load_tv_reports

router = APIRouter()

_BACKEND_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
UPLOAD_DIR = os.path.join(_BACKEND_ROOT, "data", "tv_exports")


class ScanRequest(BaseModel):
    directory: str
    force_side: str | None = None
    group_multi_tp: bool = True


@router.post("/scan")
def scan_directory(req: ScanRequest):
    """Scan a directory for TV export .xlsx files and return preview stats."""
    if not os.path.exists(req.directory):
        raise HTTPException(404, f"Directory not found: {req.directory}")

    files = [f for f in os.listdir(req.directory) if f.lower().endswith(".xlsx")]
    df = load_tv_reports(req.directory, req.force_side, req.group_multi_tp)
    assets = sorted(df["Asset"].unique().tolist()) if not df.empty else []

    return {
        "directory": req.directory,
        "files": files,
        "file_count": len(files),
        "trade_count": len(df),
        "assets": assets,
        "date_range": {
            "start": df["Exit Time"].min().isoformat() if not df.empty else None,
            "end": df["Exit Time"].max().isoformat() if not df.empty else None,
        },
    }


@router.post("/upload")
async def upload_files(
    strategy_name: str,
    files: list[UploadFile] = File(...),
):
    """Upload .xlsx files for a TV strategy."""
    dest = os.path.join(UPLOAD_DIR, strategy_name)
    os.makedirs(dest, exist_ok=True)

    saved = []
    for f in files:
        if not f.filename or not f.filename.lower().endswith(".xlsx"):
            continue
        path = os.path.join(dest, f.filename)
        content = await f.read()
        with open(path, "wb") as fh:
            fh.write(content)
        saved.append(f.filename)

    return {"strategy_name": strategy_name, "saved": saved, "count": len(saved)}
