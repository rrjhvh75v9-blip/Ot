"""
Shared keyword-watchlist detector used by all scrapers.

Any lot whose title matches a watchlist keyword gets a row inserted into
specialty_watchlist so a human reviewer can assess it even when the artist
is not yet in the database.

Duplicate (lot_id, trigger_keyword) pairs are ignored — re-scraping is safe.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from artarb.models.base import Lot, SpecialtyWatchlist

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword registry
# ---------------------------------------------------------------------------
# Each entry is (search_string, canonical_label).
# search_string is matched as a whole-word / phrase substring (case-insensitive).
# canonical_label is what gets stored in trigger_keyword.

_RAW_KEYWORDS: list[tuple[str, str]] = [
    # Japanese sword fittings & decorative art
    ("tsuba",                   "tsuba"),
    ("netsuke",                 "netsuke"),
    ("okimono",                 "okimono"),
    ("fuchi",                   "fuchi"),
    ("menuki",                  "menuki"),
    # French art publications (Maeght gallery / Mourlot atelier)
    ("derrière le miroir",      "derriere_le_miroir"),
    ("derriere le miroir",      "derriere_le_miroir"),
    ("dlm",                     "dlm"),
    ("maeght",                  "maeght"),
    # German print editions
    ("griffelkunst",            "griffelkunst"),
    # Prestigious Paris print workshop
    ("mourlot",                 "mourlot"),
    # Provenance / authentication markers
    ("atelier stamp",           "atelier_stamp"),
    ("atelierstempel",          "atelierstempel"),
    ("estate stamp",            "estate_stamp"),
    # British studio ceramics
    ("studio pottery",          "studio_pottery"),
    ("stoneware",               "stoneware"),
    ("studio keramiek",         "studio_keramiek"),
]

# De-duplicate labels: keep only the first label for each canonical name
# so both "derrière le miroir" and "derriere le miroir" map to the same row.
# Build ordered list of (pattern, label) for matching.
_PATTERNS: list[tuple[re.Pattern[str], str]] = []
_seen_labels: set[str] = set()
for _kw, _label in _RAW_KEYWORDS:
    # Escape for regex; use word-boundary anchors for short tokens (≤5 chars)
    # to avoid false positives like "fuchi" → "fuchsia" or "dlm" in other words.
    _escaped = re.escape(_kw)
    if len(_kw) <= 5:
        _pat = re.compile(r"(?<!\w)" + _escaped + r"(?!\w)", re.IGNORECASE)
    else:
        _pat = re.compile(_escaped, re.IGNORECASE)
    _PATTERNS.append((_pat, _label))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_keywords(title: str) -> list[str]:
    """Return deduplicated list of canonical labels matching the title."""
    seen: set[str] = set()
    results: list[str] = []
    for pat, label in _PATTERNS:
        if label not in seen and pat.search(title):
            seen.add(label)
            results.append(label)
    return results


def flag_lot(db: Session, lot: Lot) -> int:
    """Detect watchlist keywords in lot.title and upsert matching rows.

    Returns:
        Number of *new* specialty_watchlist rows inserted (0 if all existed).
    """
    if not lot.title:
        return 0

    keywords = detect_keywords(lot.title)
    if not keywords:
        return 0

    new_count = 0
    for kw in keywords:
        existing = db.execute(
            select(SpecialtyWatchlist).where(
                SpecialtyWatchlist.lot_id == lot.id,
                SpecialtyWatchlist.trigger_keyword == kw,
            )
        ).scalar_one_or_none()

        if existing is None:
            db.add(SpecialtyWatchlist(
                id=uuid.uuid4(),
                lot_id=lot.id,
                trigger_keyword=kw,
                reviewed=False,
            ))
            log.info(
                "WATCHLIST  keyword=%r  lot=%s  title=%r",
                kw, lot.id, lot.title[:80],
            )
            new_count += 1

    return new_count
