"""
FastAPI application factory.
Creates the app with CORS, lifespan events, and route registration.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text as sa_text

from backend.api.websocket import ws_manager
from backend.config import get_settings
from backend.db.database import SessionLocal, init_db
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def build_live_loop_scheduler():
  """Build and return a configured (but not-yet-started) BackgroundScheduler
  with the L1/L2/L3/L4 nightly jobs registered. Extracted from `lifespan` so
  it can be unit-tested without spinning up the whole app.

  T-L003: uses APScheduler's BackgroundScheduler (thread-pool based), NOT
  AsyncIOScheduler. The asyncio variant silently dropped multi-hour cron
  fires on Windows when the FastAPI event loop was idle — observed overnight
  Apr 23→24 where L1/L2 never fired. BackgroundScheduler runs in its own
  thread pool and is decoupled from the event loop.

  All jobs are sync functions that open their own SessionLocal() and commit
  before returning — thread-safe with SQLite WAL.
  """
  from apscheduler.schedulers.background import BackgroundScheduler
  from apscheduler.triggers.cron import CronTrigger
  from backend.services.batch_store_refresh import refresh_all_active_batches
  from backend.services.hybrid_portfolio_refresh import refresh_all_hybrid_portfolios
  from backend.services.oos_boundary import check_and_enqueue as check_oos_boundaries
  from backend.services.paper_refresh import run_refresh_all_active
  from backend.services.parity_canary import run_parity_canary
  from backend.services.phase2_audit import run_phase2_audit_and_persist

  # Cron gaps widened: L1 iterates ~32k runs and was occasionally still
  # writing when L2 started reading. Spacing stages at hourly boundaries
  # gives L1 a comfortable buffer (typical pass now ~10-20 min with the
  # candle cache) and keeps each stage reading a settled view of the DB.
  scheduler = BackgroundScheduler(timezone="America/New_York")
  scheduler.add_job(
    refresh_all_active_batches,
    CronTrigger(hour=1, minute=0, timezone="America/New_York"),
    id="l1_batch_store_refresh",
    misfire_grace_time=3600,
    coalesce=True,
    max_instances=1,
  )
  scheduler.add_job(
    refresh_all_hybrid_portfolios,
    CronTrigger(hour=3, minute=0, timezone="America/New_York"),
    id="l2_hybrid_portfolio_refresh",
    misfire_grace_time=3600,
    coalesce=True,
    max_instances=1,
  )
  # T-002 v2 Item 1 — phase-2 recorder audit, post-Y-B refresh.
  # 30-min buffer after L2 (typical L2 finishes in ~5-10 min for 50
  # portfolios; 30 min is generous). Writes one ParityCanaryReport
  # row per run with kind='phase2_recorder_continuity'.
  scheduler.add_job(
    run_phase2_audit_and_persist,
    CronTrigger(hour=3, minute=30, timezone="America/New_York"),
    id="l2b_phase2_recorder_audit",
    misfire_grace_time=3600,
    coalesce=True,
    max_instances=1,
  )
  scheduler.add_job(
    check_oos_boundaries,
    CronTrigger(hour=4, minute=0, timezone="America/New_York"),
    id="l3_oos_boundary",
    misfire_grace_time=3600,
    coalesce=True,
    max_instances=1,
  )
  scheduler.add_job(
    run_parity_canary,
    CronTrigger(day=1, hour=5, minute=0, timezone="America/New_York"),
    id="l4_parity_canary",
    misfire_grace_time=3600 * 12,
    coalesce=True,
    max_instances=1,
  )
  # L5 — Weekly paper-portfolio bulk refresh (Sun 02:00 NYT). Sits between
  # L1 batch_store_refresh (Sun 01:00) and L2 hybrid_portfolio_refresh (03:00),
  # so it picks up the freshest trade pool and reconciles each paper/real
  # portfolio's live_metrics_snapshot to truth without the user having to
  # click Refresh on every row. ~30 portfolios × ~30s each = 15-30 min;
  # the 4-hour grace window covers a missed Sunday from a brief outage.
  scheduler.add_job(
    run_refresh_all_active,
    CronTrigger(day_of_week="sun", hour=2, minute=0, timezone="America/New_York"),
    id="l5_weekly_paper_refresh",
    misfire_grace_time=3600 * 4,
    coalesce=True,
    max_instances=1,
  )
  return scheduler


CATCHUP_STALE_HOURS = 36


def _maybe_start_downtime_catchup() -> bool:
  """If the batch store is stale (backend was down past the cron windows),
  kick candles + L1 in a background daemon thread. Returns True when a
  catch-up thread was started.

  Staleness probe: MAX(backtest_runs.end_date) — L1 advances it on every
  successful nightly tick, so it is a faithful "last time the pipeline
  ran" marker. Threshold 36h tolerates a normal day gap + DST wobble.
  """
  import threading
  from datetime import datetime, timedelta, timezone as _tz

  probe_db = SessionLocal()
  try:
    row = probe_db.execute(
      sa_text("SELECT MAX(end_date) FROM backtest_runs")
    ).fetchone()
  finally:
    probe_db.close()

  newest = row[0] if row else None
  if newest is None:
    return False  # empty store — nothing to extend
  if isinstance(newest, str):
    try:
      newest = datetime.fromisoformat(newest[:19])
    except ValueError:
      return False
  if newest.tzinfo is None:
    newest = newest.replace(tzinfo=_tz.utc)

  age = datetime.now(_tz.utc) - newest
  if age < timedelta(hours=CATCHUP_STALE_HOURS):
    return False

  logger.warning(
    "Batch store is %.1f days stale (newest run end_date %s) — starting "
    "downtime catch-up (candles + L1) in background",
    age.total_seconds() / 86400, newest.isoformat(),
  )

  def _run():
    try:
      from backend.services.candle_catchup import catchup_candles
      rep = catchup_candles()
      logger.info(
        "Downtime catch-up: candles done (+%s new, %s pairs failed)",
        rep.get("new_candles"), rep.get("pairs_failed"),
      )
      from backend.services.batch_store_refresh import refresh_all_active_batches
      rep2 = refresh_all_active_batches()
      done = rep2.get("batches_done", rep2.get("ok", "?")) if isinstance(rep2, dict) else "?"
      logger.info("Downtime catch-up: L1 batch extension done (%s)", done)
    except Exception as e:
      logger.error("Downtime catch-up failed: %s", e)

  threading.Thread(target=_run, name="downtime-catchup", daemon=True).start()
  return True


@asynccontextmanager
async def lifespan(app: FastAPI):
  """Startup and shutdown events."""
  settings = get_settings()
  logger.info("Starting %s", settings.app_name)
  logger.info("Paper trading: %s", settings.paper_trading)

  init_db()
  logger.info("Database initialized")

  # Warm up SQLite — checkpoint WAL and touch heavy tables so first
  # user request doesn't hit a 30-60s cold start on the 5.5GB DB.
  try:
    warm_db = SessionLocal()
    warm_db.execute(sa_text("PRAGMA wal_checkpoint(TRUNCATE)"))
    warm_db.execute(sa_text("SELECT COUNT(*) FROM backtest_trades"))
    warm_db.execute(sa_text("SELECT COUNT(*) FROM symbol_categories"))
    warm_db.close()
    logger.info("SQLite WAL checkpoint + warmup complete")
  except Exception as e:
    logger.warning("SQLite warmup failed (non-fatal): %s", e)

  # WFAS overhaul: add new columns if missing (safe for existing DBs)
  try:
    from backend.db.migrate_wfas import migrate_wfas_columns
    migrate_wfas_columns()
    logger.info("WFAS schema migration checked")
  except Exception as e:
    logger.warning("WFAS migration failed (non-fatal): %s", e)

  # Audit fixes: constraint migrations
  try:
    from backend.db.migrate_live_state import (
      migrate_live_state_constraint, migrate_daily_pnl_null_constraint,
    )
    migrate_live_state_constraint()
    migrate_daily_pnl_null_constraint()
    logger.info("Audit constraint migrations checked")
  except Exception as e:
    logger.warning("Audit constraint migration failed (non-fatal): %s", e)

  # Sweep: add sweep_id column to batch_backtest_runs if missing
  try:
    from backend.db.migrate_sweep import migrate_sweep_columns, recover_stale_sweeps
    migrate_sweep_columns()
    recover_stale_sweeps()
    logger.info("Sweep schema migration + recovery checked")
  except Exception as e:
    logger.warning("Sweep migration/recovery failed (non-fatal): %s", e)

  # Live Loop: portfolios.mode column + backfill from source_strategies.hybrid_mode
  try:
    from backend.db.migrate_live_loop import migrate_live_loop_columns
    migrate_live_loop_columns()
    logger.info("Live Loop schema migration checked")
  except Exception as e:
    logger.warning("Live Loop migration failed (non-fatal): %s", e)

  # is_paper flag on trades — prevents paper/live martingale streak crossover
  try:
    from backend.db.migrate_is_paper import migrate_trades_is_paper
    migrate_trades_is_paper()
    logger.info("trades.is_paper migration checked")
  except Exception as e:
    logger.warning("is_paper migration failed (non-fatal): %s", e)

  # T-002 — composite index for phase-2 live_event lookups (route + audit)
  try:
    from backend.db.migrate_phase2_events_index import migrate_phase2_events_index
    migrate_phase2_events_index()
    logger.info("Phase-2 live_events index migration checked")
  except Exception as e:
    logger.warning("Phase-2 events index migration failed (non-fatal): %s", e)

  # Candle downloading is handled by candle_sync.py (separate process)

  # Live Loop nightly jobs (00:15 NYT batch store refresh, 00:30 NYT hybrid
  # portfolio refresh). Scheduler is optional — if the backend is running in
  # a worker where scheduled jobs are not wanted, set WARROOM_DISABLE_SCHEDULER=1.
  scheduler = None
  import os as _os
  if _os.getenv("WARROOM_DISABLE_SCHEDULER") != "1":
    try:
      scheduler = build_live_loop_scheduler()
      scheduler.start()
      logger.info(
        "Live Loop scheduler started "
        "(L1 01:00 / L2 03:00 / L2b 03:30 / L3 04:00 / L4 1st-05:00 / "
        "L5 Sun-02:00 paper-refresh, NYT)"
      )
    except Exception as e:
      logger.warning("Live Loop scheduler failed to start (non-fatal): %s", e)
      scheduler = None
  else:
    logger.info("Live Loop scheduler disabled via WARROOM_DISABLE_SCHEDULER=1")

  # Downtime catch-up (2026-06-10): cron jobs do NOT replay missed fires
  # beyond their 1-hour misfire grace. After the May-25→Jun-10 outage the
  # batch store sat 16 days stale and every "live" view with it. If the
  # newest batch-run end_date is older than the threshold at boot, run
  # candle catch-up + L1 batch extension in a daemon thread (both are
  # append-only and idempotent). L2 (hybrid refresh) is NOT triggered
  # here — it runs on its normal 03:00 NYT schedule once data is fresh.
  if _os.getenv("WARROOM_DISABLE_SCHEDULER") != "1":
    try:
      _maybe_start_downtime_catchup()
    except Exception as e:
      logger.warning("Downtime catch-up check failed (non-fatal): %s", e)

  yield

  # Stop the Live Loop scheduler on shutdown
  if scheduler is not None:
    try:
      scheduler.shutdown(wait=False)
      logger.info("Live Loop scheduler stopped")
    except Exception as e:
      logger.warning("Scheduler shutdown failed: %s", e)

  # WAL checkpoint on shutdown — prevents data loss on crash/force-kill
  try:
    shutdown_db = SessionLocal()
    shutdown_db.execute(sa_text("PRAGMA wal_checkpoint(TRUNCATE)"))
    shutdown_db.close()
    logger.info("SQLite WAL checkpoint on shutdown complete")
  except Exception as e:
    logger.warning("SQLite WAL checkpoint on shutdown failed: %s", e)

  logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
  settings = get_settings()

  app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
  )

  # API key auth — only active when secret_key is set in .env
  # MUST be added BEFORE CORSMiddleware so CORS runs first (Starlette
  # processes middlewares in reverse add order: last-added runs first).
  from backend.api.auth import APIKeyMiddleware
  app.add_middleware(APIKeyMiddleware)

  app.add_middleware(
    CORSMiddleware,
    allow_origins=[
      settings.frontend_url,
      "http://localhost:3000",
      "http://127.0.0.1:3000",
      "http://localhost:3033",
      "http://127.0.0.1:3033",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
  )

  # Register routes
  from backend.api.routes import (
    activity,
    analysis,
    analysis_extra,
    assets,
    backtest,
    batch_backtest,
    batch_groups,
    chart_data,
    sweep_backtest,
    cross_strategy,
    dashboard,
    dashboard_api,
    dashboard_extras_api,
    data,
    exchange,
    integrity,
    journal,
    kelly_allocation,
    journal_notes,
    live,
    live_loop,
    maintenance,
    market,
    portfolio,
    portfolio_analysis,
    portfolio_asset_routes,
    portfolio_health,
    portfolio_paper,
    positions,
    rebalance,
    robustness,
    settings_api,
    strategies,
    strategy_configs,
    strategy_configs_tv,
    session_analysis,
    session_masks,
    tax_records,
    trades,
    trash,
    tv_import,
    # webhook,  # H3 audit: placeholder with no auth — disabled until needed
    wfo,
  )

  app.include_router(assets.router, prefix="/api/assets", tags=["Assets"])
  app.include_router(dashboard.router, prefix="/api", tags=["Dashboard"])
  app.include_router(data.router, prefix="/api/data", tags=["Data"])
  app.include_router(strategies.router, prefix="/api/strategies", tags=["Strategies"])
  app.include_router(strategy_configs.router, prefix="/api/strategies", tags=["Strategy Configs"])
  app.include_router(backtest.router, prefix="/api/backtest", tags=["Backtest"])
  app.include_router(batch_backtest.router, prefix="/api/backtest", tags=["Batch Backtest"])
  app.include_router(batch_groups.router, prefix="/api/batch-groups", tags=["Batch Groups"])
  app.include_router(sweep_backtest.router, prefix="/api/backtest", tags=["Sweep Backtest"])
  # portfolio_analysis MUST mount before portfolio: portfolio.py owns the
  # catch-all `/{portfolio_id}` route, which would otherwise match any
  # static path like /paper-overview and try to int-parse the segment as
  # portfolio_id, returning 422 instead of letting the static route win.
  # All portfolio_analysis routes are either /{portfolio_id}/<sub-path>
  # or unique static paths (/compare, /paper-overview, /test-progress/...);
  # none of them shadow portfolio.py's static endpoints (/, /count,
  # /builder/batches), so this order is safe.
  app.include_router(portfolio_analysis.router, prefix="/api/portfolios", tags=["Portfolio Analysis"])
  app.include_router(portfolio.router, prefix="/api/portfolios", tags=["Portfolios"])
  app.include_router(trades.router, prefix="/api/trades", tags=["Trades"])
  # app.include_router(webhook.router, prefix="/api/webhook", tags=["Webhook"])  # H3 audit: disabled
  app.include_router(positions.router, prefix="/api/positions", tags=["Positions"])
  app.include_router(settings_api.router, prefix="/api/settings", tags=["Settings"])
  app.include_router(analysis.router, prefix="/api/analysis", tags=["Analysis"])
  app.include_router(tv_import.router, prefix="/api/tv-import", tags=["TV Import"])
  app.include_router(dashboard_api.router, prefix="/api/dashboard", tags=["Dashboard Analysis"])
  app.include_router(dashboard_extras_api.router, prefix="/api/dashboard", tags=["Dashboard Extras"])
  app.include_router(strategy_configs_tv.router, prefix="/api/tv-strategies", tags=["TV Strategies"])
  app.include_router(analysis_extra.router, prefix="/api/analysis", tags=["Analysis Extra"])
  app.include_router(cross_strategy.router, prefix="/api/analysis", tags=["Cross-Strategy"])
  app.include_router(journal.router, prefix="/api/journal", tags=["Journal"])
  app.include_router(journal_notes.router, prefix="/api/journal-notes", tags=["Journal Notes"])
  app.include_router(portfolio_health.router, prefix="/api/portfolios", tags=["Portfolio Health"])
  app.include_router(portfolio_paper.router, prefix="/api/portfolios", tags=["Portfolio Paper"])
  app.include_router(portfolio_asset_routes.router, prefix="/api/portfolios", tags=["Portfolio Assets"])
  app.include_router(exchange.router, prefix="/api/exchange", tags=["Exchange"])
  app.include_router(market.router, prefix="/api/market", tags=["Market"])
  app.include_router(activity.router, prefix="/api/activity", tags=["Activity"])
  app.include_router(wfo.router, prefix="/api/wfo", tags=["WFO"])
  app.include_router(live.router, prefix="/api", tags=["Live Trading"])
  app.include_router(trash.router, prefix="/api", tags=["Trash"])
  app.include_router(robustness.router, prefix="/api/analysis", tags=["Robustness"])
  app.include_router(maintenance.router, prefix="/api", tags=["Maintenance"])
  app.include_router(integrity.router, prefix="/api/integrity", tags=["Integrity"])
  app.include_router(kelly_allocation.router, prefix="/api/kelly-allocation", tags=["Kelly Allocation"])
  app.include_router(chart_data.router, prefix="/api/chart", tags=["Chart Data"])
  app.include_router(session_analysis.router, prefix="/api/analysis", tags=["Session Analysis"])
  app.include_router(session_masks.router, prefix="/api/session-masks", tags=["Session Masks"])
  app.include_router(tax_records.router, prefix="/api/tax-records", tags=["Tax Records"])
  app.include_router(rebalance.router, prefix="/api/rebalance", tags=["Rebalance Proposals"])
  app.include_router(live_loop.router, prefix="/api/live-loop", tags=["Live Loop"])

  # Wire engine coordinator to WS broadcast
  from backend.exchange.engine_coordinator import get_engine
  engine = get_engine()
  engine.set_broadcast(ws_manager.broadcast)

  @app.websocket("/ws")
  async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
      while True:
        await websocket.receive_text()
    except WebSocketDisconnect:
      ws_manager.disconnect(websocket)

  return app
