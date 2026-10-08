"""WFO Window/Fold Scanner — validates walk-forward window integrity."""
from __future__ import annotations

import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner


class WFOFoldScanner(AbstractScanner):
  name = "wfo_fold"
  category = "WFO Fold Integrity"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    windows = ctx.get("windows", [])

    if windows:
      findings.extend(self._check_is_oos_boundary(windows))
      findings.extend(self._check_oos_non_overlap(windows))
      findings.extend(self._check_expanding_is(windows))
      findings.extend(self._check_h1_h2_coverage(windows))
      findings.extend(self._check_window_trade_independence(windows, ctx))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_is_oos_boundary(self, windows):
    """Each window: IS end must be before OOS start."""
    violations = []
    for w in windows:
      is_end = w.get("is_end")
      oos_start = w.get("oos_start") or w.get("oos_h1_start")
      if is_end and oos_start:
        if pd.Timestamp(is_end) >= pd.Timestamp(oos_start):
          violations.append({
            "window": w.get("window_num"),
            "is_end": is_end, "oos_start": oos_start,
          })
    if not violations:
      return []
    return [self._finding(
      "WFO-001", "CRITICAL",
      f"{len(violations)} windows have IS/OOS overlap",
      "IS end date >= OOS start date — data leakage between periods",
      evidence={"violations": violations[:10]},
      remediation="Fix window generation: ensure IS end < OOS start",
    )]

  def _check_oos_non_overlap(self, windows):
    """OOS windows should not overlap (except overlapping mode)."""
    oos_windows = [
      w for w in windows
      if w.get("oos_start") and w.get("oos_end") and not w.get("oos_h1_start")
    ]
    if len(oos_windows) < 2:
      return []
    overlaps = []
    sorted_w = sorted(oos_windows, key=lambda w: w["oos_start"])
    for i in range(len(sorted_w) - 1):
      curr_end = pd.Timestamp(sorted_w[i]["oos_end"])
      next_start = pd.Timestamp(sorted_w[i + 1]["oos_start"])
      if curr_end > next_start:
        overlaps.append({
          "window_a": sorted_w[i].get("window_num"),
          "window_b": sorted_w[i + 1].get("window_num"),
          "overlap_days": (curr_end - next_start).days,
        })
    if not overlaps:
      return []
    return [self._finding(
      "WFO-002", "HIGH",
      f"{len(overlaps)} OOS window overlaps detected",
      "OOS trades may be counted in multiple windows — inflating results",
      evidence={"overlaps": overlaps[:10]},
      remediation="Adjust window step size to prevent OOS overlap",
    )]

  def _check_expanding_is(self, windows):
    """Static mode: IS should grow monotonically."""
    sorted_w = sorted(windows, key=lambda w: w.get("window_num", 0))
    is_shrink = []
    for i in range(1, len(sorted_w)):
      prev_end = sorted_w[i - 1].get("is_end")
      curr_end = sorted_w[i].get("is_end")
      if prev_end and curr_end:
        if pd.Timestamp(curr_end) < pd.Timestamp(prev_end):
          is_shrink.append({
            "window": sorted_w[i].get("window_num"),
            "prev_is_end": prev_end, "curr_is_end": curr_end,
          })
    if not is_shrink:
      return []
    return [self._finding(
      "WFO-003", "MEDIUM",
      f"IS period shrinks in {len(is_shrink)} windows",
      "Non-monotonic IS end dates — may indicate misconfigured window generation",
      evidence={"shrinks": is_shrink[:5]},
      confidence=0.7,
    )]

  def _check_h1_h2_coverage(self, windows):
    """Overlapping mode: H1 + H2 should equal full OOS."""
    h1h2_windows = [w for w in windows if w.get("oos_h1_start") and w.get("oos_h2_end")]
    if not h1h2_windows:
      return []
    gaps = []
    for w in h1h2_windows:
      h1_end = pd.Timestamp(w.get("oos_h1_end", ""))
      h2_start = pd.Timestamp(w.get("oos_h2_start", ""))
      if h1_end and h2_start:
        gap = (h2_start - h1_end).days
        if gap > 1:
          gaps.append({"window": w.get("window_num"), "gap_days": gap})
    if not gaps:
      return []
    return [self._finding(
      "WFO-004", "MEDIUM",
      f"{len(gaps)} windows have gaps between H1 and H2",
      "H1/H2 OOS phases should be contiguous",
      evidence={"gaps": gaps[:5]},
    )]

  def _check_window_trade_independence(self, windows, ctx):
    """Verify no trade ID appears in multiple IS windows."""
    is_trades = ctx.get("is_trades", [])
    if not is_trades or len(windows) < 2:
      return []
    # Build window → trade_ids map based on exit_time
    window_trades: dict[int, set] = {}
    for w in windows:
      w_num = w.get("window_num", 0)
      is_start = w.get("is_start")
      is_end = w.get("is_end")
      if not is_start or not is_end:
        continue
      start_ts = pd.Timestamp(is_start)
      end_ts = pd.Timestamp(is_end) + pd.Timedelta(days=1)
      tids = {
        t.id for t in is_trades
        if t.exit_time and start_ts <= pd.Timestamp(t.exit_time) <= end_ts
      }
      window_trades[w_num] = tids

    # Check for shared trade IDs across windows
    all_windows = sorted(window_trades.keys())
    shared = 0
    for i in range(len(all_windows)):
      for j in range(i + 1, len(all_windows)):
        common = window_trades[all_windows[i]] & window_trades[all_windows[j]]
        shared += len(common)
    if shared == 0:
      return []
    return [self._finding(
      "WFO-005", "MEDIUM",
      f"{shared} trade appearances shared across IS windows",
      "Same trades used in multiple IS windows may inflate optimization confidence",
      evidence={"shared_appearances": shared},
      confidence=0.6,
    )]
