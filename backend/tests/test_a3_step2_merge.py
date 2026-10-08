"""DELETED — Stage 8 obsolete (2026-04-25).

This file used to test the legacy rotation-handler / boundary-cage
architecture (hybrid_rotation_handler.py + the gate branches in the
old hybrid_portfolio_refresh.py). That architecture was replaced by
the Y-B nightly refresh on 2026-04-25 (see backend/services/
hybrid_portfolio_refresh.py). Test coverage for the new contract
lives in backend/tests/test_hybrid_refresh.py (13 tests, all green).

The file is left in place as a tombstone — the FUSE mount used during
the promotion session did not allow `rm`. The next on-host cleanup
should `git rm` it. Search for "Stage 8 obsolete" to find every such
file at once.
"""
