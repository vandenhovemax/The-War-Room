"""Shared constants used across the War Room backend."""

# Trades within +/- this dollar amount are classified as breakeven.
# Used by metrics.py, full_metrics.py, full_metrics_helpers.py.
BE_ZONE = 0.01

# CS-023: Default round-trip commission percent (per leg, in percent — 0.055
# means 5.5 bps). Bybit linear perpetuals taker fee reference. Historical
# tests pin the literal 0.055 on purpose to verify math independently of
# this constant; do not replace those literals with this name.
COMMISSION_PCT = 0.055
