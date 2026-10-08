"""WFO health check — flags unhealthy degradation patterns.

Examines the top candidate (rank 0) across all windows and warns when:
- Average WFE is too low (< 0.5)
- 3+ consecutive windows have OOS PF < 1.0
- OOS Sortino shows a declining trend
"""
from __future__ import annotations


def wfo_health_check(window_results: list[dict]) -> dict:
  """Analyze WFO window results for degradation patterns.

  Returns {"healthy": bool, "warnings": list[str]}.
  """
  warnings: list[str] = []

  if not window_results:
    return {"healthy": True, "warnings": []}

  # Extract top candidate (rank 0) from each window
  top_wfes: list[float] = []
  top_oos_pfs: list[float] = []
  top_oos_sortinos: list[float] = []

  for w in window_results:
    candidates = w.get("candidates", [])
    if not candidates:
      continue
    top = candidates[0]  # rank 0 = best candidate

    wfe = top.get("oos_wfe")
    if wfe is not None:
      top_wfes.append(float(wfe))

    oos = top.get("oos_metrics") or {}
    pf = oos.get("profit_factor")
    if pf is not None:
      top_oos_pfs.append(float(pf))
    sortino = oos.get("sortino")
    if sortino is not None:
      top_oos_sortinos.append(float(sortino))

  # Check 1: Average WFE < 0.5
  if top_wfes:
    avg_wfe = sum(top_wfes) / len(top_wfes)
    if avg_wfe < 0.5:
      warnings.append(
        f"Low average WFE ({avg_wfe:.2f}). "
        f"OOS performance retains less than 50% of IS quality on average."
      )

  # Check 2: 3+ consecutive OOS PF < 1.0
  if len(top_oos_pfs) >= 3:
    max_consecutive_bad = 0
    current_streak = 0
    for pf in top_oos_pfs:
      if pf < 1.0:
        current_streak += 1
        max_consecutive_bad = max(max_consecutive_bad, current_streak)
      else:
        current_streak = 0
    if max_consecutive_bad >= 3:
      warnings.append(
        f"{max_consecutive_bad} consecutive windows with OOS PF < 1.0. "
        f"Strategy may be losing edge over time."
      )

  # Check 3: Declining OOS Sortino trend (simple linear regression slope)
  if len(top_oos_sortinos) >= 3:
    n = len(top_oos_sortinos)
    x_mean = (n - 1) / 2
    y_mean = sum(top_oos_sortinos) / n
    numerator = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(top_oos_sortinos))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    if denominator > 0:
      slope = numerator / denominator
      # Flag if slope is meaningfully negative (declining by >0.1 per window)
      if slope < -0.1:
        warnings.append(
          f"Declining OOS Sortino trend (slope: {slope:.3f}/window). "
          f"Strategy quality may be deteriorating."
        )

  return {
    "healthy": len(warnings) == 0,
    "warnings": warnings,
  }
