"""
Artist name resolver with fuzzy matching.

Usage
-----
Single lookup (cache auto-loads on first call):
    from artarb.utils.name_resolver import resolve
    artist_id = resolve("vincent van gogh")   # -> UUID or None

Force cache refresh after DB changes:
    from artarb.utils.name_resolver import refresh_cache
    refresh_cache()

The cache maps every normalised candidate string (canonical name, its
Firstname-Lastname flip, and all name_variants) to the artist's UUID.
Lookups are O(n_candidates) but run in C via rapidfuzz and stay well
under 1 ms even for 30 k candidates.

Unmatched names are written to unmatched_artists.log (one TSV line each)
for manual review and variant addition.
"""

import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process, utils as fuzz_utils
from sqlalchemy import select
from sqlalchemy.orm import Session

from artarb.database import SessionLocal
from artarb.models.base import Artist

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MATCH_THRESHOLD: float = 85.0

# Unmatched log path: override with ARTARB_UNMATCHED_LOG env var
UNMATCHED_LOG_PATH: Path = Path(
    os.getenv("ARTARB_UNMATCHED_LOG", "unmatched_artists.log")
)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

# Cache: normalised_candidate_string → (artist_uuid, canonical_name)
_cache: dict[str, tuple[uuid.UUID, str]] = {}
_cache_loaded: bool = False
_lock = threading.Lock()

# Separate logger whose sole handler writes to UNMATCHED_LOG_PATH
_unmatched_log: Optional[logging.Logger] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve(
    name: str,
    session: Optional[Session] = None,
) -> Optional[uuid.UUID]:
    """Fuzzy-match *name* against all artists in the database.

    Args:
        name:    Raw artist name string from a scraper or external source.
        session: Optional open SQLAlchemy session.  If omitted the resolver
                 opens its own session just for the initial cache load.

    Returns:
        The artist's UUID when the best match score >= MATCH_THRESHOLD,
        otherwise None.  Unmatched names are appended to UNMATCHED_LOG_PATH.
    """
    if not name or not name.strip():
        return None

    _ensure_cache(session)

    normalised_query = _normalise(name)
    if not normalised_query:
        return None

    if not _cache:
        log.warning("Artist cache is empty — no artists in database yet.")
        return None

    result = process.extractOne(
        normalised_query,
        _cache.keys(),
        scorer=_combined_scorer,
        processor=None,          # cache keys are already normalised
        score_cutoff=MATCH_THRESHOLD,
    )

    if result is None:
        _log_unmatched(name, normalised_query)
        return None

    matched_key, score, _ = result
    artist_id, canonical = _cache[matched_key]
    log.debug("Resolved %r -> %r  (score=%.1f)", name, canonical, score)
    return artist_id


def refresh_cache(session: Optional[Session] = None) -> int:
    """Reload the artist cache from the database.

    Call this after bulk inserts (e.g. after running rkd_import) to pick up
    new artists without restarting the process.

    Returns:
        Number of candidate strings loaded into the cache.
    """
    with _lock:
        _load_cache(session)
        return len(_cache)


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def _ensure_cache(session: Optional[Session] = None) -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    with _lock:
        if not _cache_loaded:          # double-checked under lock
            _load_cache(session)
            _cache_loaded = True


def _load_cache(session: Optional[Session] = None) -> None:
    """Populate _cache from the artists table.  Must be called under _lock."""
    global _cache
    _cache = {}

    def _fill(db: Session) -> None:
        artists = db.execute(select(Artist)).scalars().all()
        for artist in artists:
            _index_artist(artist)
        log.info(
            "Artist cache loaded: %d artists → %d candidate strings",
            len(artists),
            len(_cache),
        )

    if session is not None:
        _fill(session)
    else:
        db = SessionLocal()
        try:
            _fill(db)
        finally:
            db.close()


def _index_artist(artist: Artist) -> None:
    """Add all normalised name candidates for one artist to _cache."""
    entry = (artist.id, artist.name_canonical)

    for candidate in _all_candidates(artist.name_canonical, artist.name_variants):
        if candidate not in _cache:
            _cache[candidate] = entry
        # If two artists share a normalised candidate (rare but possible),
        # keep the first entry — the canonical name takes priority over
        # variants because we iterate canonical first.


def _all_candidates(canonical: str, name_variants) -> list[str]:
    """Return de-duplicated normalised candidate strings for one artist.

    Order:
        1. Canonical as stored         (e.g. "Mondriaan, Piet")
        2. Canonical flipped           (e.g. "Piet Mondriaan")
        3. Each variant as stored
        4. Each variant flipped
    """
    raw: list[str] = [canonical, _flip(canonical)]
    for v in _iter_variants(name_variants):
        raw.append(v)
        raw.append(_flip(v))

    seen: set[str] = set()
    result: list[str] = []
    for s in raw:
        n = _normalise(s)
        if n and n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _iter_variants(name_variants) -> list[str]:
    """Yield string variants from the JSONB field regardless of storage shape."""
    if name_variants is None:
        return []
    if isinstance(name_variants, list):
        return [v for v in name_variants if isinstance(v, str) and v.strip()]
    if isinstance(name_variants, dict):
        # e.g. {"en": "Vincent van Gogh", "nl": "..."}
        return [v for v in name_variants.values() if isinstance(v, str) and v.strip()]
    if isinstance(name_variants, str):
        return [name_variants] if name_variants.strip() else []
    return []


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _combined_scorer(s1: str, s2: str, **_kwargs) -> float:
    """Return the best score across WRatio and token_set_ratio.

    - WRatio:          handles standard similarity and partial matches.
    - token_set_ratio: catches surname-only queries like "Willink" matching
                       "willink carel" with score 100.
    Both inputs are already normalised (lowercased, stripped) by the caller.
    """
    return max(
        fuzz.WRatio(s1, s2),
        fuzz.token_set_ratio(s1, s2),
    )


def _normalise(s: str) -> str:
    """Lowercase, strip punctuation-adjacent whitespace via rapidfuzz default."""
    return fuzz_utils.default_process(s)


def _flip(name: str) -> str:
    """'Lastname, Firstname Middle' → 'Firstname Middle Lastname'."""
    parts = name.split(",", 1)
    if len(parts) == 2:
        return parts[1].strip() + " " + parts[0].strip()
    return name


# ---------------------------------------------------------------------------
# Unmatched logging
# ---------------------------------------------------------------------------

def _get_unmatched_log() -> logging.Logger:
    """Lazily initialise the unmatched-artists file logger (thread-safe)."""
    global _unmatched_log
    if _unmatched_log is not None:
        return _unmatched_log

    with _lock:
        if _unmatched_log is not None:
            return _unmatched_log

        logger = logging.getLogger("artarb.unmatched_artists")
        logger.setLevel(logging.INFO)
        logger.propagate = False          # don't echo to root/console logger

        try:
            UNMATCHED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(UNMATCHED_LOG_PATH, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s\t%(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            logger.addHandler(handler)
        except OSError as exc:
            log.warning("Could not open unmatched log at %s: %s", UNMATCHED_LOG_PATH, exc)

        _unmatched_log = logger
        return logger


def _log_unmatched(raw_name: str, normalised: str) -> None:
    """Append one line to the unmatched artists log file."""
    best = _best_below_threshold(normalised)
    if best:
        best_str = f"BEST_BELOW_THRESHOLD: {best[0]!r} @ {best[1]:.1f}"
    else:
        best_str = "NO_CANDIDATES"

    _get_unmatched_log().info(
        "UNMATCHED\tINPUT: %r\t%s",
        raw_name,
        best_str,
    )
    log.debug("No match for %r (%s)", raw_name, best_str)


def _best_below_threshold(normalised_query: str) -> Optional[tuple[str, float]]:
    """Return the closest candidate even if it falls below the threshold.

    This extra context in the unmatched log helps decide whether a new
    variant should be added to an existing artist or a new artist created.
    """
    if not _cache:
        return None
    result = process.extractOne(
        normalised_query,
        _cache.keys(),
        scorer=_combined_scorer,
        processor=None,
    )
    if result is None:
        return None
    matched_key, score, _ = result
    _, canonical = _cache[matched_key]
    return canonical, score
