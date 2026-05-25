"""
RKD (Rijksbureau voor Kunsthistorische Documentatie) artist import.

Fetches Dutch and Belgian artists born 1880-1980 from the RKD Research API
and upserts them into the artists table keyed on rkd_id (= priref).

Usage
-----
Full import:
    python -m artarb.data.rkd_import

Dry-run (prints records, no DB writes):
    python -m artarb.data.rkd_import --dry-run

Print one raw API record to inspect/verify field names:
    python -m artarb.data.rkd_import --probe

Limit records (smoke-test):
    python -m artarb.data.rkd_import --limit 50

Programmatic:
    from artarb.data.rkd_import import run
    stats = run(dry_run=False, limit=None)
"""

import argparse
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import select

from artarb.database import get_session
from artarb.models.base import Artist

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API constants
# ---------------------------------------------------------------------------

RKD_BASE_URL = "https://api.rkd.nl/api/record/artists"

ROWS_PER_PAGE = 20          # RKD max is 20
RATE_LIMIT_SEC = 1.0        # pause between requests
REQUEST_TIMEOUT = 30
MAX_RETRIES = 4
RETRY_BACKOFF = 1.0         # seconds; doubles on each retry

# ---------------------------------------------------------------------------
# Target criteria
# ---------------------------------------------------------------------------

# Birth-year window
BIRTH_YEAR_MIN = 1880
BIRTH_YEAR_MAX = 1980

# RKD stores nationality in Dutch.  Common values that map to NL/BE:
TARGET_NATIONALITIES = [
    "Nederlands",           # Dutch (NL)
    "Dutch",                # English label occasionally present
    "Belgisch",             # Belgian (BE)
    "Vlaams",               # Flemish (BE/NL)
    "Vlaming",              # Flemish – person noun
    "Belgian",              # English label occasionally present
]

# ---------------------------------------------------------------------------
# RKD field mapping
# ---------------------------------------------------------------------------
# The RKD Adlib schema exposes fields with varying names depending on
# API version and language.  Each entry is a priority list; the first
# non-empty value found in a record wins.
#
# Run `python -m artarb.data.rkd_import --probe` to print a raw record
# and verify these names match the live API before a full import.

FIELD_CANDIDATES: dict[str, list[str]] = {
    # Canonical display name (typically "Lastname, Firstname" in RKD)
    "name_canonical": [
        "artist_name",
        "name",
        "kname",
        "creator",
        "title",               # fallback — some Adlib exports use 'title'
    ],
    # All alternate names / pseudonyms stored as JSONB list
    "name_variants": [
        "FRNA",                # "Verder bekend als" (also known as)
        "also_known_as",
        "alias",
        "alt_name",
    ],
    # Birth year (may be a full date string like "1853-07-30" or just "1853")
    "born": [
        "birthyear",
        "born",
        "birth_year",
        "geboortejaar",
        "birth_date",
        "date_of_birth",
    ],
    # Death year
    "died": [
        "deathyear",
        "died",
        "death_year",
        "sterfjaar",
        "death_date",
        "date_of_death",
    ],
    # Nationality label
    "nationality": [
        "nationality",
        "nationaliteit",
        "nat",
    ],
    # Artistic movement / school
    "movement": [
        "movement",
        "school",
        "school_or_movement",
        "style",
        "richting",
        "kunststroming",
    ],
}

# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class ImportStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    pages: int = 0

    def total_processed(self) -> int:
        return self.inserted + self.updated + self.skipped + self.errors

    def __str__(self) -> str:
        return (
            f"pages={self.pages} "
            f"inserted={self.inserted} "
            f"updated={self.updated} "
            f"skipped={self.skipped} "
            f"errors={self.errors}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> ImportStats:
    """Import RKD artists into the database.

    Args:
        dry_run: Log records without writing to the database.
        limit:   Stop after this many records (None = all).

    Returns:
        ImportStats with counts of inserted / updated / skipped / errors.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    http = _build_http_session()
    stats = ImportStats()

    # We issue one query per nationality to avoid complex OR filters and to
    # stay within RKD API query-complexity limits.
    seen_priref: set[str] = set()   # deduplicate across nationality queries

    for nationality in TARGET_NATIONALITIES:
        log.info("Querying nationality=%r …", nationality)
        _import_nationality(http, nationality, stats, seen_priref, dry_run, limit)

        if limit and stats.total_processed() >= limit:
            log.info("Reached --limit=%d, stopping.", limit)
            break

    log.info("Import complete — %s", stats)
    return stats


def probe() -> None:
    """Fetch a single record and print its raw JSON for field-name inspection."""
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    import json

    http = _build_http_session()
    params = _build_params(nationality=TARGET_NATIONALITIES[0], start=0, rows=1)
    log.info("GET %s  params=%s", RKD_BASE_URL, params)
    resp = http.get(RKD_BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    docs = _extract_docs(data)

    print("\n── Raw response envelope (keys) ──")
    print(sorted(data.keys()))

    if docs:
        print("\n── First record fields ──")
        print(json.dumps(docs[0], indent=2, ensure_ascii=False))
    else:
        print("\n── No docs returned ──")
        print(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Per-nationality import loop
# ---------------------------------------------------------------------------

def _import_nationality(
    http: requests.Session,
    nationality: str,
    stats: ImportStats,
    seen: set[str],
    dry_run: bool,
    limit: Optional[int],
) -> None:
    start = 0
    total: Optional[int] = None

    while True:
        if limit and stats.total_processed() >= limit:
            return

        try:
            data = _fetch_page(http, nationality, start)
        except Exception as exc:
            log.error("Failed to fetch page start=%d nat=%r: %s", start, nationality, exc)
            stats.errors += 1
            return

        if total is None:
            total = _extract_num_found(data)
            log.info("  nat=%r  numFound=%d", nationality, total)
            if total == 0:
                return

        docs = _extract_docs(data)
        if not docs:
            log.info("  nat=%r  no docs on start=%d — done.", nationality, start)
            return

        _process_page(docs, nationality, seen, stats, dry_run)
        stats.pages += 1

        log.info(
            "  nat=%r  start=%d/%d  page_docs=%d  running=%s",
            nationality, start, total, len(docs), stats,
        )

        start += ROWS_PER_PAGE
        if start >= total:
            return

        time.sleep(RATE_LIMIT_SEC)


def _process_page(
    docs: list[dict],
    nationality: str,
    seen: set[str],
    stats: ImportStats,
    dry_run: bool,
) -> None:
    """Map and upsert one page of docs. Each page is one DB transaction."""
    with get_session() as db:
        for doc in docs:
            priref = _priref(doc)
            if not priref:
                log.warning("Record missing priref, skipping: %s", list(doc.keys())[:5])
                stats.errors += 1
                continue

            if priref in seen:
                stats.skipped += 1
                continue
            seen.add(priref)

            mapped = _map_record(doc)
            if not mapped.get("name_canonical"):
                log.debug("Skipping priref=%s — no name found", priref)
                stats.skipped += 1
                continue

            if dry_run:
                log.info(
                    "[DRY-RUN] priref=%s  name=%r  born=%s  nat=%r",
                    priref,
                    mapped.get("name_canonical"),
                    mapped.get("born"),
                    mapped.get("nationality"),
                )
                stats.skipped += 1
                continue

            try:
                action = _upsert_artist(db, priref, mapped)
                if action == "inserted":
                    stats.inserted += 1
                elif action == "updated":
                    stats.updated += 1
                else:
                    stats.skipped += 1
            except Exception as exc:
                log.error("DB error for priref=%s: %s", priref, exc)
                stats.errors += 1


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _build_http_session() -> requests.Session:
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist={429, 500, 502, 503, 504},
        allowed_methods={"GET"},
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s = requests.Session()
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": "artarb/1.0 (art-arbitrage-research; contact@example.com)",
        "Accept": "application/json",
        "Accept-Language": "nl",
    })
    return s


def _build_params(nationality: str, start: int, rows: int = ROWS_PER_PAGE) -> dict:
    """Build RKD query parameters.

    RKD API uses bracket-notation for filters:
        filters[nationaliteit]=Dutch
        filters[birthyear_begin]=1880
        filters[birthyear_end]=1980

    If the live API uses Solr fq= syntax instead, replace with:
        "fq": [
            f"nationaliteit:{nationality}",
            f"birthyear:[{BIRTH_YEAR_MIN} TO {BIRTH_YEAR_MAX}]",
        ]
    """
    return {
        "format": "json",
        "language": "nl",
        "start": start,
        "rows": rows,
        "filters[nationaliteit]": nationality,
        "filters[birthyear_begin]": BIRTH_YEAR_MIN,
        "filters[birthyear_end]": BIRTH_YEAR_MAX,
    }


def _fetch_page(http: requests.Session, nationality: str, start: int) -> dict:
    params = _build_params(nationality, start)
    log.debug("GET %s  start=%d  nat=%r", RKD_BASE_URL, start, nationality)
    resp = http.get(RKD_BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _extract_num_found(data: dict) -> int:
    """Handle both Solr-style and direct numFound locations."""
    # Solr envelope: data["response"]["numFound"]
    if "response" in data:
        return int(data["response"].get("numFound", 0))
    # Flat envelope
    for key in ("numFound", "num_found", "total", "totalnumberofresults", "count"):
        if key in data:
            return int(data[key])
    return 0


def _extract_docs(data: dict) -> list[dict]:
    """Handle both Solr-style and direct doc locations."""
    if "response" in data and "docs" in data["response"]:
        return data["response"]["docs"]
    for key in ("docs", "records", "artists", "results", "items"):
        if key in data and isinstance(data[key], list):
            return data[key]
    # Adlib-style: {"recordList": {"record": [...]}}
    if "recordList" in data:
        rl = data["recordList"]
        if isinstance(rl, dict) and "record" in rl:
            return rl["record"] if isinstance(rl["record"], list) else [rl["record"]]
    return []


def _priref(doc: dict) -> Optional[str]:
    for key in ("priref", "@priref", "id", "record_id"):
        val = doc.get(key)
        if val:
            return str(_scalar(val)).strip()
    return None


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------

def _map_record(doc: dict) -> dict:
    """Map a raw RKD record dict to Artist schema fields."""
    name_raw = _first_val(doc, FIELD_CANDIDATES["name_canonical"])
    variant_vals = _all_vals(doc, FIELD_CANDIDATES["name_variants"])

    return {
        "name_canonical": _clean_str(name_raw),
        "name_variants": variant_vals if variant_vals else None,
        "nationality": _clean_str(_first_val(doc, FIELD_CANDIDATES["nationality"])),
        "born": _parse_year(_first_val(doc, FIELD_CANDIDATES["born"])),
        "died": _parse_year(_first_val(doc, FIELD_CANDIDATES["died"])),
        "movement": _clean_str(_first_val(doc, FIELD_CANDIDATES["movement"])),
    }


# ---------------------------------------------------------------------------
# Database upsert
# ---------------------------------------------------------------------------

def _upsert_artist(db, priref: str, mapped: dict) -> str:
    """Insert or update an Artist row. Returns 'inserted'/'updated'/'skipped'."""
    existing = db.execute(
        select(Artist).where(Artist.rkd_id == priref)
    ).scalar_one_or_none()

    if existing is None:
        db.add(Artist(id=uuid.uuid4(), rkd_id=priref, **mapped))
        return "inserted"

    dirty = False
    for col, value in mapped.items():
        if getattr(existing, col) != value:
            setattr(existing, col, value)
            dirty = True

    return "updated" if dirty else "skipped"


# ---------------------------------------------------------------------------
# Value extraction helpers
# ---------------------------------------------------------------------------

def _first_val(doc: dict, keys: list[str]) -> Any:
    """Return the first non-empty value found under any of the given keys."""
    for key in keys:
        val = doc.get(key)
        if val is not None and val != "" and val != []:
            return _scalar(val)
    return None


def _all_vals(doc: dict, keys: list[str]) -> list[str]:
    """Collect every non-empty value found under the given keys as a flat list."""
    result = []
    for key in keys:
        val = doc.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            result.extend(str(v).strip() for v in val if v and str(v).strip())
        elif str(val).strip():
            result.append(str(val).strip())
    return result


def _scalar(val: Any) -> Any:
    """Unwrap single-element lists (common in Solr/Adlib responses)."""
    if isinstance(val, list):
        return val[0] if val else None
    return val


def _clean_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = " ".join(str(val).split())   # normalise whitespace
    return s if s else None


def _parse_year(val: Any) -> Optional[int]:
    """Extract a 4-digit year from strings like '1853', '1853-07-30', 'ca. 1850'."""
    if val is None:
        return None
    text = str(val).strip()
    m = re.search(r"\b(1[3-9]\d{2}|20\d{2})\b", text)
    if m:
        year = int(m.group(1))
        # Sanity-check: discard obviously wrong values
        if 1300 <= year <= 2050:
            return year
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Import Dutch/Belgian artists (1880-1980) from the RKD API.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Log records without writing to the database.",
    )
    p.add_argument(
        "--probe",
        action="store_true",
        help="Fetch one record and print raw JSON for field-name inspection.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N records (useful for testing).",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.probe:
        probe()
    else:
        stats = run(dry_run=args.dry_run, limit=args.limit)
        print(f"\nDone — {stats}")
