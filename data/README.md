# `data/` — Canonical Storage

> Canonical path set in `backend/config/settings.py:26`.

## Application DB
- **`data/antigravity.db`** — the one and only main SQLite database. All strategies, portfolios, backtest runs, trade history, etc. live here.
  - Companion files: `antigravity.db-wal`, `antigravity.db-shm` (WAL journal + shared memory, SQLite-managed — never commit).
  - Do NOT rename, move, or create alternatives at other paths.

## OHLCV DBs (per timeframe, partitioned)
- **`data/ohlcv/ohlcv_<TF>.db`** — raw bars by timeframe (`1`, `5`, `15`, `30`, `60`, `240`, `720`, `D`, `3D`, `W`).
- Companion `-wal` / `-shm` files are SQLite-managed.

## Caches / uploads
- `data/wfas_cache/` — walk-forward analysis cache (regenerable).
- `data/uploads/` — user-uploaded CSVs or artifacts routed via the UI.

## Maintenance
- `data/cleanup_orphans.py` — removes orphaned rows (run manually when trade_count / run bookkeeping drifts).

---

## What used to exist (now deleted — 2026-04-21)

Five dead placeholder files were deleted during the 2026-04-21 cleanup. None had any code, script, or config reference anywhere in the repo. Backup at `backups/ghost_dbs_2026-04-21.zip`.

| File | Size | Why it was dead |
|------|------|-----------------|
| `antigravity.db` (repo root) | 0 B | Old placeholder. Canonical is `data/antigravity.db`. |
| `backend/antigravity.db` | 0 B | Old placeholder when the DB briefly lived under `backend/`. |
| `backend/data/antigravity.db` | 0 B | Old placeholder when the DB briefly lived under `backend/data/`. |
| `backend/data/trading.db` | 0 B | Early prototype name before `antigravity.db` stuck. |
| `antigravity_local.db` (repo root) | 153 MB | Stale snapshot from ~March 2026. Zero code references. |

**Going forward:** if any code path tries to open a DB at a path other than `data/antigravity.db` (or `data/ohlcv/*.db`), that's a bug — fix the path, don't create a new file.
