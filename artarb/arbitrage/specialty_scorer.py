"""
Specialty scoring bonuses for the arbitrage opportunity detector.

Applies additional confidence score bonuses for listings where the artist
has a specialty_category, and generates an arbitrage_category tag string
explaining why the opportunity was flagged.

Signal weights
--------------
is_specialty_artist     +0.25  artist.specialty_category is not null
misattribution_kw       +0.15  title contains attribution-hedging keywords
signed_pencil           +0.20  title contains signed + pencil in original language
platform_premium        +0.20  catawiki/kunstveiling + market_tier == 1
low_bid_tier1           +0.15  current_bid < 500 + market_tier == 1
pottery_seal_mark       +0.25  studio_pottery + seal/stamp keywords in title
russian_futurist_pub    +0.30  russian_futurist + publisher/artist keywords in title

Usage
-----
    from artarb.arbitrage.specialty_scorer import score
    bonus, factors, category = score(listing, lot, artist, effective_bid)
    total_confidence = base_confidence + bonus
"""

from __future__ import annotations

from typing import Optional

from artarb.models.base import Artist, Listing, Lot

# ---------------------------------------------------------------------------
# Keyword constants
# ---------------------------------------------------------------------------

MISATTRIBUTION_KEYWORDS: frozenset[str] = frozenset({
    "school of",
    "attributed",
    "circle of",
    "follower of",
    "manner of",
    "toegeschreven",
    "school van",
    "omgeving van",
    "atelier van",
    "navolger",
})

SIGNED_PENCIL_KEYWORDS: frozenset[str] = frozenset({
    "signed",
    "gesigneerd",
})

PENCIL_KEYWORDS: frozenset[str] = frozenset({
    "pencil",
    "potlood",
    "in pencil",
    "met potlood",
})

PLATFORM_PREMIUM_PLATFORMS: frozenset[str] = frozenset({
    "catawiki",
    "kunstveiling",
})

POTTERY_SEAL_KEYWORDS: frozenset[str] = frozenset({
    "seal",
    "stempel",
    "gemerkt",
    "impressed mark",
    "impressed seal",
    "studio mark",
    "pottery mark",
    "impressed",
    "mark",
})

RUSSIAN_FUTURIST_KEYWORDS: frozenset[str] = frozenset({
    "maeght",
    "mourlot",
    "lissitzky",
    "rodchenko",
    "constructivist",
    "constructivism",
    "futurist",
    "futurism",
    "vkhutemas",
    "proun",
    "suprematist",
})

LOW_BID_THRESHOLD: float = 500.0
SPECIALTY_CATEGORY_STUDIO_POTTERY: str = "studio_pottery"
SPECIALTY_CATEGORY_RUSSIAN_FUTURIST: str = "russian_futurist"


# ---------------------------------------------------------------------------
# Bonus weights
# ---------------------------------------------------------------------------

W_SPECIALTY_ARTIST: float = 0.25
W_MISATTRIBUTION: float = 0.15
W_SIGNED_PENCIL: float = 0.20
W_PLATFORM_PREMIUM: float = 0.20
W_LOW_BID_TIER1: float = 0.15
W_POTTERY_SEAL: float = 0.25
W_RUSSIAN_FUTURIST_PUB: float = 0.30


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score(
    listing: Listing,
    lot: Lot,
    artist: Artist,
    effective_bid: Optional[float],
) -> tuple[float, dict, Optional[str]]:
    """Compute specialty bonus score for a listing.

    Args:
        listing: Active Listing ORM object.
        lot:     Associated Lot ORM object.
        artist:  Associated Artist ORM object.
        effective_bid: Pre-resolved bid price (may be None).

    Returns:
        (bonus_score, bonus_factors, arbitrage_category)
        bonus_score: float to add to base confidence score.
        bonus_factors: dict mapping factor name → bool.
        arbitrage_category: pipe-separated tag string, or None if no specialty signals.
    """
    bonus: float = 0.0
    factors: dict[str, bool] = {}
    tags: list[str] = []

    title_lower = (lot.title or "").lower()
    specialty = artist.specialty_category or ""

    # 1. Specialty artist base bonus
    factors["is_specialty_artist"] = bool(specialty)
    if factors["is_specialty_artist"]:
        bonus += W_SPECIALTY_ARTIST
        tags.append(f"specialty:{specialty}")

    # 2. Misattribution / attribution-hedging keywords in title
    factors["misattribution_keyword"] = _contains_any(title_lower, MISATTRIBUTION_KEYWORDS)
    if factors["misattribution_keyword"]:
        bonus += W_MISATTRIBUTION
        tags.append("misattribution_keyword")

    # 3. Signed in pencil — both a signing keyword AND a pencil keyword required
    has_signed = _contains_any(title_lower, SIGNED_PENCIL_KEYWORDS)
    has_pencil = _contains_any(title_lower, PENCIL_KEYWORDS)
    factors["signed_pencil"] = has_signed and has_pencil
    if factors["signed_pencil"]:
        bonus += W_SIGNED_PENCIL
        tags.append("signed_pencil")

    # 4. Platform premium: catawiki/kunstveiling + market_tier == 1
    on_premium_platform = listing.platform.lower() in PLATFORM_PREMIUM_PLATFORMS
    is_tier1 = str(artist.market_tier or "").strip() == "1"
    factors["platform_premium"] = on_premium_platform and is_tier1
    if factors["platform_premium"]:
        bonus += W_PLATFORM_PREMIUM
        tags.append("platform_premium")

    # 5. Low bid (< €500) + market_tier == 1
    bid_low = effective_bid is not None and effective_bid < LOW_BID_THRESHOLD
    factors["low_bid_tier1"] = bid_low and is_tier1
    if factors["low_bid_tier1"]:
        bonus += W_LOW_BID_TIER1
        tags.append("low_bid_tier1")

    # 6. Studio pottery seal/mark keywords
    is_pottery = specialty == SPECIALTY_CATEGORY_STUDIO_POTTERY
    factors["pottery_seal_mark"] = is_pottery and _contains_any(title_lower, POTTERY_SEAL_KEYWORDS)
    if factors["pottery_seal_mark"]:
        bonus += W_POTTERY_SEAL
        tags.append("pottery_seal_mark")

    # 7. Russian futurist publisher/artist keywords
    is_russian_futurist = specialty == SPECIALTY_CATEGORY_RUSSIAN_FUTURIST
    factors["russian_futurist_pub"] = is_russian_futurist and _contains_any(
        title_lower, RUSSIAN_FUTURIST_KEYWORDS
    )
    if factors["russian_futurist_pub"]:
        bonus += W_RUSSIAN_FUTURIST_PUB
        tags.append("russian_futurist_pub")

    arbitrage_category = " | ".join(tags) if tags else None
    return round(bonus, 4), factors, arbitrage_category


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _contains_any(text: str, keywords: frozenset[str]) -> bool:
    """Return True if any keyword appears as a substring in text."""
    return any(kw in text for kw in keywords)
