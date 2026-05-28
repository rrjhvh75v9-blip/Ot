"""
Arbitrage opportunity detector.

For every active listing the detector:
  1. Resolves the lot's artist and fetches the best available
     RegionalPriceIndex entry for that artist.
  2. Calculates expected profit using a conservative model:
         buy_cost      = current_bid × 1.15   (includes buyer's premium)
         sell_estimate = median_hammer × 0.80  (net after ~20 % seller fee)
         expected_profit = sell_estimate − buy_cost
  3. Scores confidence from six independent signals (max 1.0):
         +0.20  sample_size > 10
         +0.20  sell_through_rate > 0.70
         +0.20  edition_type in {"AP", "HC"}
         +0.15  catalogue_reference is not null
         +0.10  bid_count < 3  (low competition)
         +0.15  obituary market-event in last 12 months
  4. Writes an Opportunity row when expected_profit > 200 and
     confidence_score > 0.50.  Existing open opportunities for the same
     listing are updated rather than duplicated.

The rationale JSONB column stores every intermediate value so decisions
can be audited and back-tested.

Usage
-----
One-shot:
    python -m artarb.arbitrage.opportunity_detector

Programmatic:
    from artarb.arbitrage.opportunity_detector import run
    stats = run()

Scheduled via main.py every 6 hours automatically.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artarb.database import get_session
from artarb.models.base import (
    Artist, Listing, Lot, MarketEvent, Opportunity, RegionalPriceIndex,
)
from artarb.arbitrage import specialty_scorer

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds & weights
# ---------------------------------------------------------------------------

MIN_EXPECTED_PROFIT: float = 200.0   # EUR
MIN_CONFIDENCE: float = 0.50

BUYER_PREMIUM: float = 0.15          # 15 % on top of hammer
SELLER_COMMISSION: float = 0.20      # 20 % deducted from hammer on resale

# Confidence factors
W_SAMPLE_SIZE: float = 0.20
W_SELL_THROUGH: float = 0.20
W_EDITION: float = 0.20
W_CATALOGUE: float = 0.15
W_LOW_BIDS: float = 0.10
W_OBITUARY: float = 0.15

SAMPLE_SIZE_THRESHOLD: int = 10
SELL_THROUGH_THRESHOLD: float = 0.70
PREMIUM_EDITIONS: frozenset[str] = frozenset({"AP", "HC"})
OBITUARY_EVENT_TYPES: frozenset[str] = frozenset({"obituary", "death", "overlijden"})
OBITUARY_WINDOW_DAYS: int = 365

# Listings we consider "active"
ACTIVE_STATUSES: frozenset[Optional[str]] = frozenset({"active", "live", "open", None})


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class RunStats:
    listings_scanned: int = 0
    no_price_index: int = 0       # skipped: no RegionalPriceIndex found
    no_bid: int = 0               # skipped: no current_bid or opening_bid
    below_threshold: int = 0      # evaluated but didn't meet profit/confidence
    opportunities_created: int = 0
    opportunities_updated: int = 0
    errors: int = 0

    def __str__(self) -> str:
        return (
            f"scanned={self.listings_scanned} "
            f"created={self.opportunities_created} "
            f"updated={self.opportunities_updated} "
            f"below_threshold={self.below_threshold} "
            f"no_index={self.no_price_index} "
            f"no_bid={self.no_bid} "
            f"errors={self.errors}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run() -> RunStats:
    """Scan all active listings and write qualifying opportunities.

    Returns:
        RunStats aggregate.
    """
    stats = RunStats()

    with get_session() as db:
        listings = _load_active_listings(db)
        log.info("Opportunity detector: %d active listing(s) to evaluate", len(listings))

        for listing, lot, artist in listings:
            stats.listings_scanned += 1
            try:
                _process_one(db, listing, lot, artist, stats)
            except Exception as exc:
                log.error(
                    "Error evaluating listing %s (lot %s, artist %r): %s",
                    listing.id, lot.id, artist.name_canonical, exc,
                )
                stats.errors += 1

    log.info("Opportunity detector complete — %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Per-listing logic
# ---------------------------------------------------------------------------

def _process_one(
    db: Session,
    listing: Listing,
    lot: Lot,
    artist: Artist,
    stats: RunStats,
) -> None:
    # 1. Resolve bid price (current > opening > skip)
    bid = _effective_bid(listing)
    if bid is None:
        log.debug("  listing %s: no bid price — skip", listing.id)
        stats.no_bid += 1
        return

    # 2. Find the best RegionalPriceIndex for this artist
    idx = _best_price_index(db, artist.id, listing.platform, lot.country_sale)
    if idx is None or idx.median_hammer is None:
        log.debug(
            "  listing %s: no price index for artist %r — skip",
            listing.id, artist.name_canonical,
        )
        stats.no_price_index += 1
        return

    # 3. Financial model
    buy_cost = round(float(bid) * (1 + BUYER_PREMIUM), 2)
    sell_estimate = round(float(idx.median_hammer) * (1 - SELLER_COMMISSION), 2)
    expected_profit = round(sell_estimate - buy_cost, 2)

    # 4. Confidence score
    has_obituary = _recent_obituary(db, artist.id)
    base_score, factors = _confidence_score(listing, lot, idx, has_obituary)

    # 4b. Specialty bonus
    specialty_bonus, specialty_factors, arbitrage_category = specialty_scorer.score(
        listing, lot, artist, bid
    )
    score = round(base_score + specialty_bonus, 4)
    factors.update(specialty_factors)

    log.debug(
        "  listing %s  artist=%r  bid=%.0f  buy=%.0f  sell=%.0f  "
        "profit=%.0f  conf=%.2f (base=%.2f specialty=%.2f)",
        listing.id, artist.name_canonical,
        float(bid), buy_cost, sell_estimate, expected_profit,
        score, base_score, specialty_bonus,
    )

    # 5. Gate
    if expected_profit <= MIN_EXPECTED_PROFIT or score <= MIN_CONFIDENCE:
        log.debug(
            "  → below threshold (profit=%.0f min=%.0f, conf=%.2f min=%.2f)",
            expected_profit, MIN_EXPECTED_PROFIT, score, MIN_CONFIDENCE,
        )
        stats.below_threshold += 1
        return

    # 6. Upsert
    rationale = _build_rationale(
        bid, buy_cost, sell_estimate, expected_profit, score, factors, idx,
        base_score, specialty_bonus,
    )
    action = _upsert_opportunity(
        db, listing, lot, artist, idx,
        buy_cost, sell_estimate, expected_profit, score, rationale,
        arbitrage_category,
    )

    if action == "created":
        log.info(
            "OPPORTUNITY  %-35s  profit=€%.0f  conf=%.2f  listing=%s",
            artist.name_canonical, expected_profit, score, listing.id,
        )
        stats.opportunities_created += 1
    elif action == "updated":
        log.info(
            "OPP UPDATED  %-35s  profit=€%.0f  conf=%.2f  listing=%s",
            artist.name_canonical, expected_profit, score, listing.id,
        )
        stats.opportunities_updated += 1


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _confidence_score(
    listing: Listing,
    lot: Lot,
    idx: RegionalPriceIndex,
    has_obituary: bool,
) -> tuple[float, dict]:
    """Return (total_score, factor_dict) for the six confidence signals."""
    factors: dict[str, bool] = {}
    score = 0.0

    factors["sample_size_gt_10"] = (idx.sample_size or 0) > SAMPLE_SIZE_THRESHOLD
    if factors["sample_size_gt_10"]:
        score += W_SAMPLE_SIZE

    factors["sell_through_gt_70pct"] = (idx.sell_through_rate or 0) > SELL_THROUGH_THRESHOLD
    if factors["sell_through_gt_70pct"]:
        score += W_SELL_THROUGH

    factors["premium_edition"] = (lot.edition_type or "").upper() in PREMIUM_EDITIONS
    if factors["premium_edition"]:
        score += W_EDITION

    factors["catalogue_referenced"] = lot.catalogue_reference is not None
    if factors["catalogue_referenced"]:
        score += W_CATALOGUE

    factors["low_competition"] = (listing.bid_count or 0) < 3
    if factors["low_competition"]:
        score += W_LOW_BIDS

    factors["recent_obituary"] = has_obituary
    if factors["recent_obituary"]:
        score += W_OBITUARY

    return round(score, 4), factors


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _load_active_listings(
    db: Session,
) -> list[tuple[Listing, Lot, Artist]]:
    """Return all active listings joined with their lot and artist."""
    now = datetime.now(timezone.utc)

    rows = db.execute(
        select(Listing, Lot, Artist)
        .join(Lot, Listing.lot_id == Lot.id)
        .join(Artist, Lot.artist_id == Artist.id)
        .where(
            Listing.status.in_([s for s in ACTIVE_STATUSES if s is not None])
            | Listing.status.is_(None)
        )
        .where(
            Listing.ends_at.is_(None) | (Listing.ends_at > now)
        )
    ).all()

    return [(r.Listing, r.Lot, r.Artist) for r in rows]


def _best_price_index(
    db: Session,
    artist_id: uuid.UUID,
    platform: str,
    country: Optional[str],
) -> Optional[RegionalPriceIndex]:
    """Return the most useful RegionalPriceIndex for this artist.

    Priority order:
        1. Exact (platform + country) match with most samples
        2. Platform-only match with most samples
        3. Any index for the artist with most samples
    """
    base = (
        select(RegionalPriceIndex)
        .where(
            RegionalPriceIndex.artist_id == artist_id,
            RegionalPriceIndex.median_hammer.isnot(None),
        )
        .order_by(RegionalPriceIndex.sample_size.desc().nullslast())
        .limit(1)
    )

    # Try exact match first
    if country:
        exact = db.execute(
            base.where(
                RegionalPriceIndex.platform == platform,
                RegionalPriceIndex.country == country,
            )
        ).scalar_one_or_none()
        if exact:
            return exact

    # Platform-only match
    platform_match = db.execute(
        base.where(RegionalPriceIndex.platform == platform)
    ).scalar_one_or_none()
    if platform_match:
        return platform_match

    # Any index for this artist
    return db.execute(base).scalar_one_or_none()


def _recent_obituary(db: Session, artist_id: uuid.UUID) -> bool:
    """Return True if an obituary market-event exists in the last 12 months."""
    cutoff = date.today() - timedelta(days=OBITUARY_WINDOW_DAYS)
    count = db.scalar(
        select(func.count())
        .select_from(MarketEvent)
        .where(
            MarketEvent.artist_id == artist_id,
            MarketEvent.event_type.in_(list(OBITUARY_EVENT_TYPES)),
            MarketEvent.event_date >= cutoff,
        )
    )
    return (count or 0) > 0


def _upsert_opportunity(
    db: Session,
    listing: Listing,
    lot: Lot,
    artist: Artist,
    idx: RegionalPriceIndex,
    buy_cost: float,
    sell_estimate: float,
    expected_profit: float,
    confidence_score: float,
    rationale: dict,
    arbitrage_category: Optional[str] = None,
) -> str:
    """Insert or update an Opportunity.  Returns 'created', 'updated', or 'unchanged'."""
    existing = db.execute(
        select(Opportunity).where(
            Opportunity.listing_id == listing.id,
            Opportunity.status == "open",
        )
    ).scalar_one_or_none()

    new_values = dict(
        buy_platform=listing.platform,
        buy_price_estimate=buy_cost,
        sell_platform=idx.platform,
        sell_price_estimate=sell_estimate,
        expected_profit=expected_profit,
        confidence_score=confidence_score,
        rationale=rationale,
        status="open",
        arbitrage_category=arbitrage_category,
    )

    if existing is None:
        db.add(Opportunity(
            id=uuid.uuid4(),
            listing_id=listing.id,
            artist_id=artist.id,
            **new_values,
        ))
        return "created"

    # Update if financials shifted materially (> €1) or arbitrage_category changed
    profit_shifted = abs(float(existing.expected_profit or 0) - expected_profit) > 1.0
    category_changed = existing.arbitrage_category != arbitrage_category
    if profit_shifted or category_changed:
        for k, v in new_values.items():
            setattr(existing, k, v)
        return "updated"

    return "unchanged"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _effective_bid(listing: Listing) -> Optional[float]:
    """Return current_bid if positive, else opening_bid, else None.

    A current_bid of 0 means no bids have been placed yet; fall through
    to opening_bid so the financial model uses a real reserve price.
    """
    if listing.current_bid is not None and float(listing.current_bid) > 0:
        return float(listing.current_bid)
    if listing.opening_bid is not None:
        return float(listing.opening_bid)
    return None


def _build_rationale(
    bid: float,
    buy_cost: float,
    sell_estimate: float,
    expected_profit: float,
    confidence_score: float,
    factors: dict,
    idx: RegionalPriceIndex,
    base_score: float = 0.0,
    specialty_bonus: float = 0.0,
) -> dict:
    return {
        "bid": bid,
        "buy_cost": buy_cost,
        "sell_estimate": sell_estimate,
        "expected_profit": expected_profit,
        "confidence_score": confidence_score,
        "base_score": base_score,
        "specialty_bonus": specialty_bonus,
        "confidence_factors": factors,
        "price_index": {
            "id": str(idx.id),
            "country": idx.country,
            "platform": idx.platform,
            "median_hammer": float(idx.median_hammer),
            "sample_size": idx.sample_size,
            "sell_through_rate": float(idx.sell_through_rate) if idx.sell_through_rate else None,
            "period_start": idx.period_start.isoformat() if idx.period_start else None,
            "period_end": idx.period_end.isoformat() if idx.period_end else None,
        },
    }


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
        description="Detect arbitrage opportunities from active listings."
    )
    parser.parse_args()
    stats = run()
    print(f"\nDone — {stats}")
