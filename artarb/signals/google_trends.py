"""
Google Trends weekly signal fetcher.

For each tracked artist this module:
  1. Queries Google Trends for the artist's name (one keyword per request to
     keep scores independent and on a consistent 0-100 scale).
  2. Upserts weekly relative-interest rows into the search_trends table.
  3. Compares the latest complete week against the prior 4-week rolling
     average and logs a WARNING when the increase exceeds 50 %.

Entry points
------------
One-shot (all artists in DB):
    python -m artarb.signals.google_trends

Specific artists:
    from artarb.signals.google_trends import run
    run(artist_uuids=[uuid1, uuid2])

Weekly scheduler (blocks forever):
    from artarb.signals.google_trends import schedule_weekly
    schedule_weekly()

Rate-limit notes
----------------
Google silently throttles aggressive scrapers.  This module queries one
artist at a time, sleeps BETWEEN_ARTISTS_SEC between artists, and backs
off exponentially on TooManyRequestsError (429).  Do not lower the sleep
values without testing for sustained runs.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import schedule
from pytrends.exceptions import ResponseError, TooManyRequestsError
from pytrends.request import TrendReq
from sqlalchemy import select
from sqlalchemy.orm import Session

from artarb.database import get_session
from artarb.models.base import Artist, SearchTrend

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# "today 3-m" yields weekly granularity (~13 data points) and is enough
# to compute a 4-week average with a few weeks of history to spare.
TIMEFRAME: str = "today 3-m"

# Geo targets.  Empty string = worldwide; stored as None in DB.
# Add or remove entries to change which regions are tracked.
GEO_TARGETS: list[str] = ["", "NL", "BE"]

# Seconds to sleep between consecutive artist requests.  Google's unofficial
# rate limit is roughly one request per 5-10 s for sustained access.
BETWEEN_ARTISTS_SEC: int = 8

# Shorter pause between geo variants for the same artist (same session).
BETWEEN_GEOS_SEC: int = 3

# Backoff settings on TooManyRequestsError (429).
INITIAL_BACKOFF_SEC: int = 60
MAX_BACKOFF_SEC: int = 900     # 15 minutes
MAX_RETRIES: int = 6

# Spike detection
SPIKE_THRESHOLD: float = 0.50   # current week > 4w-avg by this fraction
COMPARISON_WEEKS: int = 4       # rolling-average window (weeks before latest)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class RunStats:
    artists_processed: int = 0
    rows_written: int = 0
    spikes_detected: int = 0
    errors: int = 0

    def __str__(self) -> str:
        return (
            f"artists={self.artists_processed} "
            f"rows={self.rows_written} "
            f"spikes={self.spikes_detected} "
            f"errors={self.errors}"
        )


def run(artist_uuids: Optional[list[uuid.UUID]] = None) -> RunStats:
    """Fetch Google Trends for *artist_uuids* (or all artists if None).

    Each artist/geo combination is one API call.  Sleeps between calls to
    avoid rate-limiting.  Returns aggregate stats.
    """
    stats = RunStats()
    pt = _build_client()

    with get_session() as db:
        artists = _load_artists(db, artist_uuids)
        log.info("Processing %d artist(s) × %d geo(s)", len(artists), len(GEO_TARGETS))

        for idx, artist in enumerate(artists):
            search_term = _search_name(artist)
            log.info(
                "[%d/%d] %r  (search=%r)",
                idx + 1, len(artists), artist.name_canonical, search_term,
            )

            artist_had_error = False
            for geo in GEO_TARGETS:
                try:
                    df = _fetch_with_backoff(pt, search_term, geo)
                    if df.empty:
                        log.debug("  geo=%r → empty response", geo or "WW")
                        continue

                    written = _upsert_trends(db, artist.id, geo or None, df, search_term)
                    stats.rows_written += written
                    log.debug("  geo=%r → %d rows upserted", geo or "WW", written)

                    spike = _check_spike(db, artist.id, geo or None, search_term)
                    if spike:
                        stats.spikes_detected += 1

                    if len(GEO_TARGETS) > 1:
                        time.sleep(BETWEEN_GEOS_SEC)

                except TooManyRequestsError:
                    # Handled inside _fetch_with_backoff; if it re-raises we
                    # give up on this artist but continue with the next.
                    log.error("  Rate limit exceeded for %r — skipping", search_term)
                    artist_had_error = True
                    break
                except Exception as exc:
                    log.error("  geo=%r error: %s", geo or "WW", exc)
                    artist_had_error = True

            if artist_had_error:
                stats.errors += 1
            else:
                stats.artists_processed += 1

            if idx < len(artists) - 1:
                time.sleep(BETWEEN_ARTISTS_SEC)

    log.info("Run complete — %s", stats)
    return stats


def schedule_weekly(run_at: str = "09:00") -> None:
    """Configure a Monday 09:00 job and block forever.

    Args:
        run_at: Wall-clock time string in HH:MM format (local time).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Scheduling Google Trends fetch every Monday at %s", run_at)
    schedule.every().monday.at(run_at).do(run)

    # Fire immediately on first start so we don't have to wait until Monday.
    log.info("Running initial fetch …")
    run()

    while True:
        schedule.run_pending()
        time.sleep(60)


# ---------------------------------------------------------------------------
# Artist loading
# ---------------------------------------------------------------------------

def _load_artists(db: Session, artist_uuids: Optional[list[uuid.UUID]]) -> list[Artist]:
    stmt = select(Artist)
    if artist_uuids:
        stmt = stmt.where(Artist.id.in_(artist_uuids))
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# pytrends client & fetch
# ---------------------------------------------------------------------------

def _build_client() -> TrendReq:
    return TrendReq(
        hl="nl",        # Dutch UI language — relevant for Dutch art market
        tz=60,          # CET (UTC+1)
        timeout=(10, 25),
        retries=2,
        backoff_factor=0.5,
    )


def _fetch_with_backoff(
    pt: TrendReq,
    search_term: str,
    geo: str,
) -> pd.DataFrame:
    """Call pytrends and return the interest_over_time DataFrame.

    Retries up to MAX_RETRIES times on 429, doubling the wait each time.
    Raises TooManyRequestsError if all retries are exhausted.
    """
    backoff = INITIAL_BACKOFF_SEC

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            pt.build_payload(
                kw_list=[search_term],
                timeframe=TIMEFRAME,
                geo=geo,
            )
            return pt.interest_over_time()

        except TooManyRequestsError:
            if attempt == MAX_RETRIES:
                log.error(
                    "429 on %r geo=%r — all %d retries exhausted",
                    search_term, geo or "WW", MAX_RETRIES,
                )
                raise
            log.warning(
                "429 on %r geo=%r — waiting %ds (attempt %d/%d)",
                search_term, geo or "WW", backoff, attempt, MAX_RETRIES,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SEC)

        except ResponseError as exc:
            # Non-429 HTTP errors (e.g. 500) — don't retry, propagate
            log.error("ResponseError for %r geo=%r: %s", search_term, geo or "WW", exc)
            raise

    # Unreachable, but satisfies type checkers
    raise TooManyRequestsError("Retries exhausted", None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Database upsert
# ---------------------------------------------------------------------------

def _upsert_trends(
    db: Session,
    artist_id: uuid.UUID,
    geo: Optional[str],          # None = worldwide
    df: pd.DataFrame,
    search_term: str,
) -> int:
    """Write weekly rows from *df* into search_trends.

    Skips weeks marked isPartial=True (incomplete current week).
    Returns the number of rows inserted or updated.
    """
    written = 0

    for ts, row in df.iterrows():
        # Skip the current in-progress week — its score will change
        if row.get("isPartial", False):
            continue

        week_start: date = ts.date()
        score_raw = row.get(search_term)
        if score_raw is None:
            # Fall back to first non-isPartial numeric column
            numeric_cols = [c for c in df.columns if c != "isPartial"]
            score_raw = row[numeric_cols[0]] if numeric_cols else None

        relative_interest: Optional[int] = (
            int(score_raw) if score_raw is not None and pd.notna(score_raw) else None
        )

        existing = db.execute(
            select(SearchTrend).where(
                SearchTrend.artist_id == artist_id,
                SearchTrend.week_start == week_start,
                SearchTrend.geo == geo,
            )
        ).scalar_one_or_none()

        if existing is None:
            db.add(SearchTrend(
                id=uuid.uuid4(),
                artist_id=artist_id,
                week_start=week_start,
                geo=geo,
                relative_interest=relative_interest,
            ))
            written += 1
        elif existing.relative_interest != relative_interest:
            existing.relative_interest = relative_interest
            written += 1

    return written


# ---------------------------------------------------------------------------
# Spike detection
# ---------------------------------------------------------------------------

def _check_spike(
    db: Session,
    artist_id: uuid.UUID,
    geo: Optional[str],
    name: str,
) -> bool:
    """Return True and emit a WARNING when interest spikes > SPIKE_THRESHOLD.

    Logic:
        latest_complete_week  = most recent non-partial week in DB
        rolling_avg           = mean of the COMPARISON_WEEKS weeks before it
        spike                 = (latest - rolling_avg) / rolling_avg > 0.50

    The current week is excluded by excluding the week that starts on or
    after the most recent Monday (it may still be accumulating data).
    """
    current_week_start = _monday_of(date.today())

    rows = db.execute(
        select(SearchTrend)
        .where(
            SearchTrend.artist_id == artist_id,
            SearchTrend.geo == geo,
            SearchTrend.week_start < current_week_start,   # exclude live week
            SearchTrend.relative_interest.isnot(None),
        )
        .order_by(SearchTrend.week_start.desc())
        .limit(COMPARISON_WEEKS + 1)
    ).scalars().all()

    # Need the latest week plus at least COMPARISON_WEEKS prior weeks
    if len(rows) < COMPARISON_WEEKS + 1:
        log.debug(
            "Not enough history for spike check: %s geo=%r (%d rows)",
            name, geo, len(rows),
        )
        return False

    latest_score: int = rows[0].relative_interest
    prior_scores: list[int] = [r.relative_interest for r in rows[1:]]
    rolling_avg: float = sum(prior_scores) / len(prior_scores)

    if rolling_avg == 0:
        return False   # artist has zero baseline interest; avoid division by zero

    increase = (latest_score - rolling_avg) / rolling_avg

    if increase > SPIKE_THRESHOLD:
        log.warning(
            "TREND SPIKE ▲  %-40s  geo=%-4s  "
            "latest=%3d  4w_avg=%5.1f  +%5.1f%%",
            name,
            geo or "WW",
            latest_score,
            rolling_avg,
            increase * 100,
        )
        return True

    log.debug(
        "No spike: %s geo=%r  latest=%d  4w_avg=%.1f  Δ=%.1f%%",
        name, geo, latest_score, rolling_avg, increase * 100,
    )
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _search_name(artist: Artist) -> str:
    """Return a Google-search-friendly name for an artist.

    RKD stores names as 'Lastname, Firstname'.  We flip to 'Firstname Lastname'
    because that is what a collector would type into a search engine.
    Mononyms (e.g. 'Corneille') are returned unchanged.
    """
    name = artist.name_canonical
    parts = name.split(",", 1)
    if len(parts) == 2:
        return f"{parts[1].strip()} {parts[0].strip()}"
    return name


def _monday_of(d: date) -> date:
    """Return the Monday that starts the ISO week containing *d*."""
    return d - timedelta(days=d.weekday())   # weekday(): Mon=0, Sun=6


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Fetch Google Trends data for tracked artists."
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Block and run weekly (every Monday 09:00).  Default: run once.",
    )
    parser.add_argument(
        "--uuids",
        nargs="*",
        metavar="UUID",
        help="Restrict to these artist UUIDs.  Default: all artists.",
    )
    args = parser.parse_args()

    uuids = [uuid.UUID(u) for u in args.uuids] if args.uuids else None

    if args.schedule:
        schedule_weekly()
    else:
        stats = run(uuids)
        print(f"\nDone — {stats}")
