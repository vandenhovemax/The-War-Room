"""Walk Forward Optimization — Track compilation & cumulative metrics.

Supports rolling/static (standard) and overlapping (H1/H2) modes.
"""
from __future__ import annotations

from datetime import date

from backend.db.models import BacktestTrade


def _phase_window_days(window_results: list[dict], phase: str) -> int:
  """Calendar-day span of the stitched window series for the given phase.

  Uses first window start → last window end to avoid double-counting
  overlapping H1/H2 segments. Returns 0 if no usable window dates are found.
  """
  start_key = "is_start" if phase == "is" else "oos_start"
  end_key = "is_end" if phase == "is" else "oos_end"
  starts: list[date] = []
  ends: list[date] = []
  for w in window_results:
    s = w.get(start_key)
    e = w.get(end_key)
    if s and e:
      try:
        starts.append(date.fromisoformat(s))
        ends.append(date.fromisoformat(e))
      except (ValueError, TypeError):
        continue
  if not starts or not ends:
    return 0
  span_days = (max(ends) - min(starts)).days + 1
  return max(span_days, 0)


def compile_tracks(
  window_results: list[dict],
  n_candidates: int,
  all_trades: list[BacktestTrade],
  mode: str = "rolling",
) -> list[dict]:
  """Compile per-rank tracks across all windows.

  For overlapping mode, each per_window entry includes
  oos_h1_pnl, oos_h2_pnl, and degradation_ratio.
  """
  tracks: list[dict] = []
  for rank in range(1, n_candidates + 1):
    per_window: list[dict] = []
    all_oos_pnls: list[float] = []
    all_degradations: list[float] = []
    final_assets: list[str] = []

    for w in window_results:
      cand = next((c for c in w["candidates"] if c["rank"] == rank), None)
      if not cand:
        continue

      oos_pnl = cand["oos_metrics"]["net_profit"] if cand.get("oos_metrics") else None
      entry: dict = {
        "window_num": w["window_num"],
        "assets": cand["assets"],
        "is_metrics": cand["is_metrics"],
        "oos_pnl": oos_pnl,
        "oos_wfe": cand.get("oos_wfe"),
      }

      # Overlapping mode extras
      if mode == "overlapping":
        h1m = cand.get("oos_h1_metrics")
        h2m = cand.get("oos_h2_metrics")
        entry["oos_h1_pnl"] = h1m["net_profit"] if h1m else None
        entry["oos_h2_pnl"] = h2m["net_profit"] if h2m else None
        entry["wfe_h1"] = cand.get("wfe_h1")
        entry["degradation_ratio"] = cand.get("degradation_ratio")
        if cand.get("degradation_ratio") is not None:
          all_degradations.append(cand["degradation_ratio"])

      per_window.append(entry)
      if oos_pnl is not None:
        all_oos_pnls.append(oos_pnl)
      final_assets = cand["assets"]

    wfes = [pw["oos_wfe"] for pw in per_window if pw.get("oos_wfe") is not None]
    compiled: dict = {
      "total_oos_pnl": round(sum(all_oos_pnls), 2),
      "avg_wfe": round(sum(wfes) / len(wfes), 4) if wfes else 0.0,
      "windows_profitable": sum(1 for p in all_oos_pnls if p > 0),
      "windows_total": len(all_oos_pnls),
    }

    if mode == "overlapping" and all_degradations:
      compiled["avg_degradation"] = round(
        sum(all_degradations) / len(all_degradations), 4,
      )

    tracks.append({
      "rank": rank,
      "per_window": per_window,
      "compiled_metrics": compiled,
      "final_is_assets": final_assets,
    })

  return tracks


def cumulative_metrics(window_results: list[dict], phase: str) -> dict:
  """Aggregate metrics across all windows for a given phase.

  phase: "is" | "oos" | "oos_h1" | "oos_h2"

  Returns a comprehensive set of KPIs computed from per-window metrics.
  """
  import math

  total_pnl = 0.0
  total_trades = 0
  total_positions = 0
  total_wins = 0
  all_gp = 0.0
  all_gl = 0.0

  # Per-window PnL series (for drawdown, Sortino, Sharpe, monthly stats)
  per_window_pnls: list[float] = []
  per_window_wrs: list[float] = []
  per_window_trades: list[int] = []
  per_window_max_dds: list[float] = []
  # Rich per-window metrics (from _rich_metrics_from_daily)
  per_window_sortinos: list[float] = []
  per_window_sharpes: list[float] = []
  per_window_calmars: list[float] = []
  per_window_recoveries: list[float] = []
  per_window_stabilities: list[float] = []
  per_window_longest_dds: list[int] = []
  per_window_expectancies: list[float] = []
  per_window_payoffs: list[float] = []
  per_window_avg_wins: list[float] = []
  per_window_avg_losses: list[float] = []
  per_window_consec_wins: list[int] = []
  per_window_consec_losses: list[int] = []
  per_window_max_concurrent: list[int] = []
  per_window_avg_concurrent: list[float] = []

  # Map phase to the correct metrics key on each candidate
  metrics_key = {
    "is": "is_metrics",
    "oos": "oos_metrics",
    "oos_h1": "oos_h1_metrics",
    "oos_h2": "oos_h2_metrics",
  }.get(phase, "oos_metrics")

  for w in window_results:
    if not w["candidates"]:
      continue
    c = w["candidates"][0]  # Track #1
    metrics = c.get(metrics_key)
    if not metrics:
      continue

    np_val = metrics.get("net_profit", 0)
    tc = metrics.get("total_trades", 0)
    wr = metrics.get("win_rate", 0)
    md = metrics.get("max_drawdown", 0)

    total_pnl += np_val
    total_trades += tc
    total_positions += metrics.get("total_positions", tc)  # fallback to trades if no position count
    total_wins += int(wr * tc)
    per_window_pnls.append(np_val)
    per_window_wrs.append(wr)
    per_window_trades.append(tc)
    if md is not None:
      per_window_max_dds.append(md)

    # Rich metrics (available when _rich_metrics_from_daily was used)
    if metrics.get("sortino") is not None:
      per_window_sortinos.append(metrics.get("sortino", 0))
    if metrics.get("sharpe") is not None:
      per_window_sharpes.append(metrics.get("sharpe", 0))
    if metrics.get("calmar") is not None:
      per_window_calmars.append(metrics.get("calmar", 0))
    if metrics.get("recovery_factor") is not None:
      per_window_recoveries.append(metrics.get("recovery_factor", 0))
    if metrics.get("stability") is not None:
      per_window_stabilities.append(metrics.get("stability", 0))
    if metrics.get("longest_dd_days") is not None:
      per_window_longest_dds.append(metrics.get("longest_dd_days", 0))
    if metrics.get("expectancy") is not None:
      per_window_expectancies.append(metrics.get("expectancy", 0))
    if metrics.get("payoff_ratio") is not None:
      per_window_payoffs.append(metrics.get("payoff_ratio", 0))
    if metrics.get("avg_win") is not None:
      per_window_avg_wins.append(metrics.get("avg_win", 0))
    if metrics.get("avg_loss") is not None:
      per_window_avg_losses.append(metrics.get("avg_loss", 0))
    if metrics.get("max_consec_wins") is not None:
      per_window_consec_wins.append(metrics.get("max_consec_wins", 0))
    if metrics.get("max_consec_losses") is not None:
      per_window_consec_losses.append(metrics.get("max_consec_losses", 0))

    # Concurrent trades (stored on candidate, not metrics)
    mc = c.get("oos_max_concurrent", 0)
    ac = c.get("oos_avg_concurrent", 0)
    if mc:
      per_window_max_concurrent.append(mc)
    if ac:
      per_window_avg_concurrent.append(ac)

    # Prefer direct gross_profit/gross_loss
    gp = metrics.get("gross_profit", 0)
    gl = metrics.get("gross_loss", 0)
    if gp > 0 or gl > 0:
      all_gp += gp
      all_gl += gl
    else:
      # Fallback: reconstruct from PF + net_profit (legacy data)
      pf = metrics.get("profit_factor", 0)
      if pf > 1 and np_val > 0:
        gl_est = np_val / (pf - 1)
        all_gp += np_val + gl_est
        all_gl += gl_est
      elif pf > 0 and np_val > 0:
        all_gp += np_val

  n_windows = len(per_window_pnls)

  # ── Derived stats ──
  avg_trade = round(total_pnl / total_trades, 2) if total_trades > 0 else 0
  avg_window_pnl = round(total_pnl / n_windows, 2) if n_windows > 0 else 0
  avg_wr = round(sum(per_window_wrs) / n_windows, 4) if n_windows > 0 else 0
  avg_trades_per_window = round(total_trades / n_windows, 1) if n_windows > 0 else 0

  # Window-level WR (% of windows profitable)
  windows_profitable = sum(1 for p in per_window_pnls if p > 0)
  window_wr = round(windows_profitable / n_windows, 4) if n_windows > 0 else 0

  # Max drawdown (worst across all windows)
  max_dd = min(per_window_max_dds) if per_window_max_dds else 0

  # Cumulative equity drawdown (across windows sequentially)
  #
  # We need TWO views here:
  #   1. `cum_equity` at window granularity — drives `cum_longest_dd_windows`
  #      (how many consecutive windows were in drawdown), which is a
  #      window-level concept and must stay window-level.
  #   2. `cum_max_dd` — the worst stitched-equity drawdown in dollars. This
  #      must be computed on *daily* data, not window totals, otherwise a
  #      strategy that is net positive every window but has a -$5k intra-
  #      window dip registers `cum_max_dd = 0`, which zeros out recovery
  #      factor, return/DD ratio, and Calmar in the UI (bug observed on T16
  #      2026-04-20).
  cum_equity = []
  running = 0.0
  for p in per_window_pnls:
    running += p
    cum_equity.append(running)
  cum_max_dd = 0.0

  # Preferred: stitch per-window daily equity curves into a single daily PnL
  # series, then take the worst peak-to-trough. IS and OOS phases expose
  # per-candidate daily curves; H1/H2 slices do not, so they fall through
  # to the legacy window-sum approach below.
  eq_curve_key = {
    "is": "is_equity_curve",
    "oos": "oos_equity_curve",
  }.get(phase)
  stitched_daily: list[tuple[str, float]] = []
  if eq_curve_key:
    for w in window_results:
      if not w["candidates"]:
        continue
      c = w["candidates"][0]
      ec = c.get(eq_curve_key) or []
      prev_eq = 0.0
      for pt in ec:
        try:
          if isinstance(pt, dict):
            eq_val = float(pt["equity"])
            date_s = str(pt["date"])
          else:
            date_s = str(pt[0])
            eq_val = float(pt[1])
        except (KeyError, IndexError, TypeError, ValueError):
          continue
        stitched_daily.append((date_s, eq_val - prev_eq))
        prev_eq = eq_val

  if stitched_daily:
    stitched_daily.sort(key=lambda x: x[0])
    cumulative = 0.0
    peak = 0.0
    for _, delta in stitched_daily:
      cumulative += delta
      if cumulative > peak:
        peak = cumulative
      dd = cumulative - peak
      if dd < cum_max_dd:
        cum_max_dd = dd
  elif cum_equity:
    # Legacy / H1 / H2 fallback — window-level drawdown
    peak = cum_equity[0]
    for eq in cum_equity:
      if eq > peak:
        peak = eq
      dd = eq - peak
      if dd < cum_max_dd:
        cum_max_dd = dd

  # Final fallback — a monotonically rising stitched curve yields 0 DD but
  # the underlying windows still had intra-window drawdowns. Surface the
  # worst one so recovery/calmar stay meaningful rather than silently
  # collapsing to 0.
  if cum_max_dd == 0 and per_window_max_dds:
    cum_max_dd = min(per_window_max_dds)

  # Sortino ratio (on per-window PnL returns)
  # Canonical convention (matches backend.analysis.scoring.sortino_ratio):
  # downside deviation is measured against the MAR target (0), and averaged
  # over the number of negative observations — not over n_windows.
  sortino = 0.0
  if n_windows >= 2:
    mean_pnl = total_pnl / n_windows
    negatives = [p for p in per_window_pnls if p < 0]
    n_neg = len(negatives)
    if n_neg > 0:
      dd_dev = math.sqrt(sum(p * p for p in negatives) / n_neg)
      sortino = round(mean_pnl / dd_dev, 4) if dd_dev > 0 else 0

  # Sharpe ratio (on per-window PnL)
  sharpe = 0.0
  if n_windows >= 2:
    mean_pnl = total_pnl / n_windows
    var = sum((p - mean_pnl) ** 2 for p in per_window_pnls) / (n_windows - 1)
    std = math.sqrt(var) if var > 0 else 0
    sharpe = round(mean_pnl / std, 4) if std > 0 else 0

  # Best / worst window
  best_window_pnl = max(per_window_pnls) if per_window_pnls else 0
  worst_window_pnl = min(per_window_pnls) if per_window_pnls else 0

  # Recovery factor on cumulative equity (total PnL / worst cumulative DD)
  cum_recovery = round(total_pnl / abs(cum_max_dd), 4) if cum_max_dd != 0 else 0
  cum_return_dd = cum_recovery  # alias

  # Calmar on cumulative: annualised return / cumulative max DD.
  # Annualisation uses the actual calendar span of the phase windows
  # (crypto = 365 days/year). Falls back to a neutral 0.0 if dates are
  # unavailable, instead of the historical hardcoded "*4" (3-month window
  # assumption), which silently over- or under-stated Calmar whenever the
  # OOS window length drifted from 3 months.
  cum_calmar = 0.0
  if cum_max_dd != 0 and n_windows >= 2:
    phase_days = _phase_window_days(window_results, phase)
    if phase_days > 0:
      annualised_pnl = total_pnl / phase_days * 365.0
      cum_calmar = round(annualised_pnl / abs(cum_max_dd), 4)

  # Longest cumulative drawdown (in windows)
  cum_longest_dd_windows = 0
  if cum_equity:
    peak = cum_equity[0]
    current_dd_len = 0
    for eq in cum_equity:
      if eq >= peak:
        peak = eq
        current_dd_len = 0
      else:
        current_dd_len += 1
        cum_longest_dd_windows = max(cum_longest_dd_windows, current_dd_len)

  # Average per-window rich metrics (where available)
  def _safe_avg(lst: list[float]) -> float:
    return round(sum(lst) / len(lst), 4) if lst else 0.0

  # Derived: wins/losses/gross for WFOMetricsPanel compatibility
  n_wins = total_wins
  n_losses = total_trades - total_wins
  gross_profit = round(all_gp, 2)
  gross_loss = round(all_gl, 2)
  avg_win_val = round(all_gp / n_wins, 2) if n_wins > 0 else 0
  avg_loss_val = round(all_gl / n_losses, 2) if n_losses > 0 else 0
  payoff = round(avg_win_val / avg_loss_val, 4) if avg_loss_val > 0 else 0

  return {
    "net_profit": round(total_pnl, 2),
    "total_trades": total_trades,
    "total_positions": total_positions,
    "win_rate": round(total_wins / total_trades, 4) if total_trades > 0 else 0,
    "profit_factor": round(all_gp / all_gl, 4) if all_gl > 0 else (10.0 if all_gp > 0 else 0),
    "max_drawdown": round(max_dd, 2),
    "cumulative_max_dd": round(cum_max_dd, 2),
    "avg_trade": avg_trade,
    "avg_window_pnl": avg_window_pnl,
    "avg_wr_per_window": avg_wr,
    "avg_trades_per_window": avg_trades_per_window,
    "window_wr": window_wr,
    "windows_profitable": windows_profitable,
    "total_windows_evaluated": n_windows,
    "sortino": sortino,
    "sharpe": sharpe,
    "best_window_pnl": round(best_window_pnl, 2),
    "worst_window_pnl": round(worst_window_pnl, 2),
    # Rich KPIs — cumulative equity-level
    "recovery_factor": cum_recovery,
    "return_dd_ratio": cum_return_dd,
    "calmar": cum_calmar,
    "longest_dd_windows": cum_longest_dd_windows,
    # WFOMetricsPanel-compatible keys (aggregate across all windows)
    "n_wins": n_wins,
    "n_losses": n_losses,
    "gross_profit": gross_profit,
    "gross_loss": gross_loss,
    "avg_win": avg_win_val,
    "avg_loss": avg_loss_val,
    "payoff_ratio": payoff,
    "longest_dd_days": max(per_window_longest_dds) if per_window_longest_dds else 0,
    "stability": _safe_avg(per_window_stabilities),
    # Real expectancy: wr * avg_win − (1 − wr) * avg_loss  (avg_loss is a
    # positive number here). The previous definition, total_pnl/total_trades,
    # is "avg_trade" — mathematically identical for IS summaries but
    # misleadingly labelled. Keep avg_trade separate (line 294).
    "expectancy": round(
      (total_wins / total_trades) * avg_win_val
      - (1 - total_wins / total_trades) * avg_loss_val,
      2,
    ) if total_trades > 0 else 0,
    # Rich KPIs — averaged across per-window values
    "avg_sortino": _safe_avg(per_window_sortinos),
    "avg_sharpe": _safe_avg(per_window_sharpes),
    "avg_calmar": _safe_avg(per_window_calmars),
    "avg_recovery": _safe_avg(per_window_recoveries),
    "avg_stability": _safe_avg(per_window_stabilities),
    "avg_longest_dd_days": round(sum(per_window_longest_dds) / len(per_window_longest_dds)) if per_window_longest_dds else 0,
    "avg_expectancy": _safe_avg(per_window_expectancies),
    "avg_payoff": _safe_avg(per_window_payoffs),
    "avg_avg_win": _safe_avg(per_window_avg_wins),
    "avg_avg_loss": _safe_avg(per_window_avg_losses),
    "max_longest_dd_days": max(per_window_longest_dds) if per_window_longest_dds else 0,
    # Consecutive wins/losses
    "max_consec_wins": max(per_window_consec_wins) if per_window_consec_wins else 0,
    "max_consec_losses": max(per_window_consec_losses) if per_window_consec_losses else 0,
    "avg_consec_wins": _safe_avg(per_window_consec_wins),
    "avg_consec_losses": _safe_avg(per_window_consec_losses),
    # Concurrent trades
    "oos_max_concurrent": max(per_window_max_concurrent) if per_window_max_concurrent else 0,
    "oos_avg_max_concurrent": _safe_avg(per_window_max_concurrent),
    "oos_avg_concurrent": _safe_avg(per_window_avg_concurrent),
  }


def aggregate_summary(
  window_results: list[dict],
  mode: str = "rolling",
) -> dict:
  """Build mode-appropriate summary across all windows."""
  oos_windows = [w for w in window_results if w.get("oos_start")]

  # Average WFE (track #1)
  wfes: list[float] = []
  for w in oos_windows:
    for c in w["candidates"][:1]:
      if c.get("oos_wfe") is not None:
        wfes.append(c["oos_wfe"])
  avg_wfe = round(sum(wfes) / len(wfes), 4) if wfes else 0.0

  summary: dict = {
    "total_windows": len(window_results),
    "windows_with_oos": len(oos_windows),
    "aggregate_wfe": avg_wfe,
  }

  # Overlapping-specific: average degradation ratio
  if mode == "overlapping":
    degradations: list[float] = []
    for w in oos_windows:
      for c in w["candidates"][:1]:
        if c.get("degradation_ratio") is not None:
          degradations.append(c["degradation_ratio"])
    summary["avg_degradation"] = (
      round(sum(degradations) / len(degradations), 4) if degradations else None
    )

  return summary
