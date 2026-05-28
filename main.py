"""
artarb — main orchestrator.

Runs every module on a fixed schedule inside a single process.
A failure in any job is caught, logged, and the loop continues.

Schedule
--------
startup        RKD artist import  (only when artists table is empty)
every  6 h     kunstveiling scraper  →  opportunity detector
every 24 h     news monitor  →  exhibition scraper
every  7 d     google trends  →  price index calculator

Usage
-----
    python main.py

All configuration is read from environment variables / .env (see .env.example).
"""

import logging
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable

import schedule
from sqlalchemy import func, select, text

from artarb.database import SessionLocal, check_connection
from artarb.models.base import Artist

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("artarb.main")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown = False


def _handle_signal(signum: int, _frame: Any) -> None:
    global _shutdown
    log.info("Signal %d received — finishing current job then exiting …", signum)
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ---------------------------------------------------------------------------
# Job runner — wraps every callable with timing, logging, and error handling
# ---------------------------------------------------------------------------

def _run_job(label: str, fn: Callable, *args: Any, **kwargs: Any) -> bool:
    """Execute *fn* and return True on success, False on any exception.

    Logs start/finish lines with elapsed time so the console output is
    easy to scan.
    """
    log.info("▶  START  %s", label)
    t0 = time.monotonic()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.monotonic() - t0
        result_str = f"  result={result}" if result is not None else ""
        log.info("✔  DONE   %s  (%.1fs)%s", label, elapsed, result_str)
        return True
    except Exception as exc:
        elapsed = time.monotonic() - t0
        log.error("✖  FAIL   %s  (%.1fs)  %s: %s", label, elapsed, type(exc).__name__, exc)
        return False


def _import_optional(module_path: str, fn_name: str = "run") -> Callable | None:
    """Import *module_path* and return its *fn_name* attribute, or None."""
    import importlib
    try:
        mod = importlib.import_module(module_path)
        fn = getattr(mod, fn_name, None)
        if fn is None:
            log.warning("Module %r has no %r function — skipping", module_path, fn_name)
        return fn
    except ImportError as exc:
        log.warning("Module %r not yet available (%s) — skipping", module_path, exc)
        return None


# ---------------------------------------------------------------------------
# Individual jobs
# ---------------------------------------------------------------------------

def job_rkd_import() -> None:
    """Run RKD import unconditionally (called only when artists table is empty)."""
    from artarb.data.rkd_import import run as rkd_run
    _run_job("rkd_import", rkd_run)


def job_scrapers() -> None:
    """Run all auction scrapers sequentially."""
    from artarb.scrapers import ALL_SCRAPERS
    for name, scrape_fn in ALL_SCRAPERS:
        _run_job(f"scraper:{name}", scrape_fn)


def job_opportunity_detector() -> None:
    fn = _import_optional("artarb.arbitrage.opportunity_detector")
    if fn:
        _run_job("opportunity_detector", fn)
    else:
        log.info("—  SKIP   opportunity_detector (module not yet implemented)")


def job_news_monitor() -> None:
    from artarb.signals.news_monitor import run as nm_run
    _run_job("news_monitor", nm_run)


def job_exhibition_scraper() -> None:
    fn = _import_optional("artarb.scrapers.exhibition", "scrape")
    if fn:
        _run_job("scraper:exhibition", fn)
    else:
        log.info("—  SKIP   exhibition_scraper (module not yet implemented)")


def job_google_trends() -> None:
    from artarb.signals.google_trends import run as gt_run
    _run_job("google_trends", gt_run)


def job_price_index() -> None:
    fn = _import_optional("artarb.signals.price_index")
    if fn:
        _run_job("price_index", fn)
    else:
        log.info("—  SKIP   price_index (module not yet implemented)")


# Composite jobs registered with `schedule` — each runs its steps in order.

def scheduled_6h() -> None:
    _log_schedule_header("6-HOUR RUN")
    job_scrapers()
    job_opportunity_detector()


def scheduled_24h() -> None:
    _log_schedule_header("24-HOUR RUN")
    job_news_monitor()
    job_exhibition_scraper()


def scheduled_7d() -> None:
    _log_schedule_header("7-DAY RUN")
    job_google_trends()
    job_price_index()


def _log_schedule_header(label: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log.info("=" * 60)
    log.info("  %s  —  %s", label, now)
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# Startup: conditional RKD import
# ---------------------------------------------------------------------------

def _artists_table_empty() -> bool:
    """Return True if the artists table has zero rows."""
    try:
        db = SessionLocal()
        try:
            count = db.scalar(select(func.count()).select_from(Artist))
            return (count or 0) == 0
        finally:
            db.close()
    except Exception as exc:
        log.error("Could not count artists table: %s", exc)
        # Assume non-empty so we don't accidentally re-import on DB errors.
        return False


def _startup_import() -> None:
    """Run RKD import once if the artists table is empty."""
    if _artists_table_empty():
        log.info("Artists table is empty — running initial RKD import …")
        job_rkd_import()
    else:
        log.info("Artists table already populated — skipping RKD import.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("artarb orchestrator starting up")

    # 1. Verify DB connectivity before scheduling anything.
    if not check_connection():
        log.critical(
            "Cannot reach the database. "
            "Check DATABASE_URL / DB_* env vars and retry."
        )
        sys.exit(1)
    log.info("Database connection OK")

    # 2. Conditional RKD seed import.
    _startup_import()

    # 3. Register recurring jobs with `schedule`.
    schedule.every(6).hours.do(scheduled_6h)
    schedule.every(24).hours.do(scheduled_24h)
    schedule.every(7).days.do(scheduled_7d)
    log.info(
        "Schedule registered: scrapers=6h  news+exhibitions=24h  trends+index=7d"
    )

    # 4. Fire all jobs once immediately so data is fresh from the first second,
    #    rather than waiting up to 7 days for the first weekly run.
    log.info("Running all jobs once on startup …")
    scheduled_6h()
    scheduled_24h()
    scheduled_7d()

    # 5. Main loop — runs pending jobs, sleeps 30 s, checks shutdown flag.
    log.info("Entering main loop (Ctrl-C or SIGTERM to stop) …")
    while not _shutdown:
        schedule.run_pending()
        time.sleep(30)

    log.info("artarb orchestrator stopped.")


if __name__ == "__main__":
    main()
