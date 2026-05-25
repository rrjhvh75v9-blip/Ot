"""
Regional price index calculator.

Aggregates historical hammer prices from price_events into the
regional_price_index table, grouped by (artist, country, platform).

The index is the foundation for opportunity_detector: without it no
expected-profit calculations can be made.

Metrics produced per (artist, country, platform) combination
------------------------------------------------------------
median_hammer       Median EUR hammer price across all sold lots in the window
sample_size         Number of lots that hammered (not total listings)
sell_through_rate   hammered_lots / total_lots_listed on that platform

Usage
-----
One-shot:
    python -m artarb.arbitrage.price_index

Programmatic:
    from artarb.arbitrage.price_index import run
    stats = run(lookback_days=730)
"""

import logging
import statistics
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artarb.database import get_session
from artarb.models.base import Listing, Lot, PriceEvent, RegionalPriceIndex

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# How far back to look when computing the index (rolling window).
DEFAULT_LOOKBACK_DAYS: int = 730   # 2 years

# event_type values that represent a completed sale.
SOLD_EVENT_TYPES: frozenset[str] = frozenset({"hammer", "sold", "buy_now_sold"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class RunStats:
    combos_processed: int = 0
    rows_written: int = 0
    errors: int = 0

    def __str__(self) -> str:
        return (
            f"combos={self.combos_processed} "
            f"rows={self.rows_written} "
            f"errors={self.errors}"
        )


def run(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> RunStats:
    """Recompute all regional price indices and upsert results.

    Args:
        lookback_days: Only include price_events from the last N days.

    Returns:
        RunStats with counts of combos processed and rows written.
    """
    stats = RunStats()
    cutoff = date.today() - timedelta(days=lookback_days)

    with get_session() as db:
        combos = _distinct_combos(db, cutoff)
        log.info(
            "Price index: %d (artist, country, platform) combo(s) to process",
            len(combos),
        )

        for artist_id, country, platform in combos:
            try:
                written = _compute_and_upsert(db, artist_id, country, platform, cutoff)
                stats.rows_written += written
                stats.combos_processed += 1
            except Exception as exc:
                log.error(
                    "Error computing index for artist=%s country=%r platform=%r: %s",
                    artist_id, country, platform, exc,
                )
                stats.errors += 1

    log.info("Price index run complete — %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def _distinct_combos(
    db: Session,
    cutoff: date,
) -> list[tuple[uuid.UUID, Optional[str], str]]:
    """Return all (artist_id, country_sale, platform) combos with sold events."""
    rows = db.execute(
        select(
            Lot.artist_id,
            Lot.country_sale,
            PriceEvent.platform,
        )
        .join(PriceEvent, PriceEvent.lot_id == Lot.id)
        .where(
            PriceEvent.event_type.in_(list(SOLD_EVENT_TYPES)),
            PriceEvent.amount_eur.isnot(None),
            PriceEvent.amount_eur > 0,
            PriceEvent.sale_date >= datetime(
                cutoff.year, cutoff.month, cutoff.day, tzinfo=timezone.utc
            ),
        )
        .group_by(Lot.artist_id, Lot.country_sale, PriceEvent.platform)
    ).all()
    return [(r.artist_id, r.country_sale, r.platform) for r in rows]


def _compute_and_upsert(
    db: Session,
    artist_id: uuid.UUID,
    country: Optional[str],
    platform: str,
    cutoff: date,
) -> int:
    """Compute metrics for one combo and upsert into regional_price_index.

    Returns 1 if a row was inserted or updated, 0 if unchanged.
    """
    cutoff_dt = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=timezone.utc)

    # --- Hammer prices for this combo ---
    price_rows = db.execute(
        select(PriceEvent.amount_eur, PriceEvent.sale_date)
        .join(Lot, PriceEvent.lot_id == Lot.id)
        .where(
            Lot.artist_id == artist_id,
            Lot.country_sale == country,
            PriceEvent.platform == platform,
            PriceEvent.event_type.in_(list(SOLD_EVENT_TYPES)),
            PriceEvent.amount_eur.isnot(None),
            PriceEvent.amount_eur > 0,
            PriceEvent.sale_date >= cutoff_dt,
        )
        .order_by(PriceEvent.sale_date)
    ).all()

    if not price_rows:
        return 0

    amounts = [float(r.amount_eur) for r in price_rows]
    median_hammer = statistics.median(amounts)
    sample_size = len(amounts)

    dates = [r.sale_date for r in price_rows if r.sale_date]
    period_start = min(dates).date() if dates else cutoff
    period_end = max(dates).date() if dates else date.today()

    # --- Sell-through rate: hammered / total lots listed on this platform ---
    total_listed = db.scalar(
        select(func.count(func.distinct(Listing.lot_id)))
        .join(Lot, Listing.lot_id == Lot.id)
        .where(
            Lot.artist_id == artist_id,
            Lot.country_sale == country,
            Listing.platform == platform,
        )
    ) or 0

    sell_through_rate = (sample_size / total_listed) if total_listed > 0 else None

    # --- Upsert ---
    existing = db.execute(
        select(RegionalPriceIndex).where(
            RegionalPriceIndex.artist_id == artist_id,
            RegionalPriceIndex.country == (country or ""),
            RegionalPriceIndex.platform == platform,
        )
    ).scalar_one_or_none()

    new_values = {
        "median_hammer": round(median_hammer, 2),
        "sample_size": sample_size,
        "sell_through_rate": round(sell_through_rate, 4) if sell_through_rate is not None else None,
        "period_start": period_start,
        "period_end": period_end,
    }

    if existing is None:
        db.add(RegionalPriceIndex(
            id=uuid.uuid4(),
            artist_id=artist_id,
            country=country or "",
            platform=platform,
            **new_values,
        ))
        log.debug(
            "  INSERT index: artist=%s country=%r platform=%r "
            "median=%.0f n=%d str=%.2f",
            artist_id, country, platform,
            median_hammer, sample_size, sell_through_rate or 0,
        )
        return 1

    dirty = any(getattr(existing, k) != v for k, v in new_values.items())
    if dirty:
        for k, v in new_values.items():
            setattr(existing, k, v)
        log.debug(
            "  UPDATE index: artist=%s country=%r platform=%r",
            artist_id, country, platform,
        )
        return 1

    return 0


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
    p = argparse.ArgumentParser(description="Recompute regional price indices.")
    p.add_argument(
        "--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
        metavar="N", help=f"Days of history to include (default {DEFAULT_LOOKBACK_DAYS}).",
    )
    args = p.parse_args()
    stats = run(lookback_days=args.lookback_days)
    print(f"\nDone — {stats}")
