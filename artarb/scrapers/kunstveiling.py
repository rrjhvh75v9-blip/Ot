"""
Scraper for kunstveiling.nl — Dutch online art auction platform.

Entry point:
    from artarb.scrapers.kunstveiling import scrape
    scrape()                   # all pages
    scrape(max_pages=3)        # first 3 pages only

Debug selector tuning:
    Set env var ARTARB_DEBUG_HTML=1 to write raw HTML of each page
    to /tmp/kunstveiling_page_<offset>.html for offline inspection.
"""

import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag
from sqlalchemy import select
from sqlalchemy.orm import Session

from artarb.database import get_session
from artarb.models.base import Artist, Listing, Lot, ScrapeLog

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLATFORM = "kunstveiling.nl"
BASE_URL = "https://www.kunstveiling.nl/items/nieuw-aanbod"
PAGE_SIZE = 24          # items per page; update if pagination stride differs
RATE_LIMIT_SEC = 2      # minimum seconds between requests
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.kunstveiling.nl/",
}

# ---------------------------------------------------------------------------
# CSS selectors
# ---------------------------------------------------------------------------
# Each value is a priority-ordered list: the first selector that finds a
# non-empty result wins. Inspect raw HTML (ARTARB_DEBUG_HTML=1) to tune these
# if the site changes its markup.

# Selectors for the container of a single lot card on the listing page
ITEM_SELECTORS: list[str] = [
    "article.lot",
    "div.lot",
    "li.lot",
    "article.item",
    "div.item",
    "li.item",
    "[data-lot-id]",
    "[data-item-id]",
    "article",
]

# Field selectors within each lot card
FIELD_SELECTORS: dict[str, list[str]] = {
    "link":        ["a[href*='/items/']", "a[href*='/lot/']", "a"],
    "title":       [
        ".lot-title", ".item-title", ".title",
        "h2", "h3", "h4",
    ],
    "artist":      [
        ".lot-artist", ".item-artist", ".artist", ".maker",
        "[data-artist]", "[itemprop='author']",
    ],
    "technique":   [
        ".lot-technique", ".technique", ".item-technique",
        ".material", ".medium",
    ],
    "dimensions":  [
        ".lot-dimensions", ".dimensions", ".afmetingen",
        ".size", ".item-size", ".maten",
    ],
    "opening_bid": [
        ".opening-bid", ".openingsbod", ".start-bid",
        ".start-price", "[data-opening-bid]",
    ],
    "current_bid": [
        ".current-bid", ".huidig-bod", ".bod",
        ".highest-bid", "[data-current-bid]",
        ".price", ".item-price",
    ],
    "bid_count":   [
        ".bid-count", ".biedingen", ".aantal-biedingen",
        ".number-of-bids", "[data-bid-count]",
    ],
    "sale_type":   [
        ".sale-type", ".veilingtype", ".type",
        "[data-sale-type]",
    ],
    "ends_at":     [
        "time[datetime]", ".ends-at", ".einddatum",
        ".end-date", ".sluitingstijd", ".closing-time",
        "[data-ends-at]",
    ],
}

# Dutch month abbreviations / full names for date parsing
_NL_MONTHS = {
    "jan": 1, "feb": 2, "mrt": 3, "mar": 3,
    "apr": 4, "mei": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9,
    "okt": 10, "oct": 10, "nov": 11, "dec": 12,
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "juni": 6, "juli": 7, "augustus": 8, "september": 9,
    "oktober": 10, "november": 11, "december": 12,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape(max_pages: Optional[int] = None) -> None:
    """Scrape kunstveiling.nl new listings, paginating until no more results.

    Args:
        max_pages: Stop after this many pages (None = unlimited).
    """
    http = _build_session()
    pages_scraped = 0

    with get_session() as db:
        offset = 0
        while True:
            if max_pages is not None and pages_scraped >= max_pages:
                log.info("Reached max_pages=%d, stopping.", max_pages)
                break

            items_found, error = _scrape_page(db, http, offset)
            pages_scraped += 1

            if error:
                log.warning("Page offset=%d error: %s", offset, error)

            if items_found == 0 and error is None:
                log.info("No items on offset=%d — pagination complete.", offset)
                break

            offset += PAGE_SIZE
            time.sleep(RATE_LIMIT_SEC)

    log.info("Scrape complete. Pages: %d", pages_scraped)


# ---------------------------------------------------------------------------
# Per-page logic
# ---------------------------------------------------------------------------

def _scrape_page(
    db: Session,
    http: requests.Session,
    offset: int,
) -> tuple[int, Optional[str]]:
    """Fetch one page and upsert all lot/listing records found.

    Returns:
        (items_found, error_message)  — error_message is None on success.
    """
    start = datetime.now(timezone.utc)
    status_code: Optional[int] = None
    error_msg: Optional[str] = None
    items_found = 0

    try:
        resp = _fetch(http, offset)
        status_code = resp.status_code
        resp.raise_for_status()

        _maybe_save_debug_html(resp.text, offset)

        soup = BeautifulSoup(resp.text, "html.parser")
        raw_items = _parse_items(soup)
        items_found = len(raw_items)
        log.info("offset=%d  items_parsed=%d", offset, items_found)

        for item in raw_items:
            try:
                _upsert_item(db, item)
            except Exception as exc:
                log.error("Failed to write item %s: %s", item.get("source_url"), exc)

    except requests.HTTPError as exc:
        error_msg = f"HTTP {exc.response.status_code}: {exc}"
        log.error("offset=%d  %s", offset, error_msg)
    except requests.RequestException as exc:
        error_msg = str(exc)
        log.error("offset=%d  request error: %s", offset, error_msg)
    except Exception as exc:
        error_msg = str(exc)
        log.exception("offset=%d  unexpected error: %s", offset, error_msg)
    finally:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        _log_scrape(
            db,
            url=f"{BASE_URL}?offset={offset}",
            status_code=status_code,
            items_found=items_found,
            error_message=error_msg,
            duration_ms=duration_ms,
        )

    return items_found, error_msg


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _fetch(http: requests.Session, offset: int) -> requests.Response:
    url = f"{BASE_URL}?offset={offset}"
    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.debug("GET %s  (attempt %d)", url, attempt)
            resp = http.get(url, timeout=REQUEST_TIMEOUT)
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SEC * attempt
                log.warning("Request failed (%s), retrying in %ds…", exc, wait)
                time.sleep(wait)

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def _parse_items(soup: BeautifulSoup) -> list[dict]:
    """Return a list of raw dicts, one per lot card found on the page."""
    items: list[Tag] = []

    for selector in ITEM_SELECTORS:
        candidates = soup.select(selector)
        if candidates:
            log.debug("Item container matched by selector %r (%d items)", selector, len(candidates))
            items = candidates
            break

    if not items:
        log.warning("No lot items found on page — HTML structure may have changed.")
        return []

    results = []
    for tag in items:
        parsed = _parse_item(tag)
        if parsed:
            results.append(parsed)
    return results


def _parse_item(tag: Tag) -> Optional[dict]:
    """Extract fields from a single lot card tag. Returns None if no title found."""
    link_tag = _first(tag, FIELD_SELECTORS["link"])
    href = link_tag["href"] if link_tag and link_tag.has_attr("href") else None
    if href and href.startswith("/"):
        href = "https://www.kunstveiling.nl" + href

    title = _text(tag, FIELD_SELECTORS["title"])
    if not title:
        return None  # skip cards without a title (ads, banners, etc.)

    artist_raw = _text(tag, FIELD_SELECTORS["artist"])
    technique = _text(tag, FIELD_SELECTORS["technique"])
    dimensions_raw = _text(tag, FIELD_SELECTORS["dimensions"])
    width_cm, height_cm = _parse_dimensions(dimensions_raw)

    opening_bid = _parse_price(_text(tag, FIELD_SELECTORS["opening_bid"]))
    current_bid = _parse_price(_text(tag, FIELD_SELECTORS["current_bid"]))
    bid_count = _parse_int(_text(tag, FIELD_SELECTORS["bid_count"]))
    sale_type = _text(tag, FIELD_SELECTORS["sale_type"])

    ends_at_raw = _ends_at_raw(tag)
    ends_at = _parse_date(ends_at_raw)

    # Derive source_lot_id from URL slug or a data attribute
    source_lot_id = (
        tag.get("data-lot-id")
        or tag.get("data-id")
        or tag.get("data-item-id")
        or _lot_id_from_url(href)
    )

    return {
        "title": title,
        "artist_name": _normalise_name(artist_raw),
        "technique": technique,
        "width_cm": width_cm,
        "height_cm": height_cm,
        "opening_bid": opening_bid,
        "current_bid": current_bid,
        "bid_count": bid_count,
        "sale_type": sale_type,
        "ends_at": ends_at,
        "source_url": href,
        "source_lot_id": source_lot_id,
    }


# ---------------------------------------------------------------------------
# Database upserts
# ---------------------------------------------------------------------------

def _upsert_item(db: Session, item: dict) -> None:
    artist = _upsert_artist(db, item["artist_name"]) if item["artist_name"] else None
    if artist is None:
        # Create a placeholder artist for lots without an identified maker
        artist = _upsert_artist(db, "Unknown / Onbekend")

    lot = _upsert_lot(db, item, artist)
    _upsert_listing(db, item, lot)
    db.flush()


def _upsert_artist(db: Session, name: str) -> Artist:
    canonical = name.strip()
    row = db.execute(
        select(Artist).where(Artist.name_canonical.ilike(canonical))
    ).scalar_one_or_none()

    if row is None:
        row = Artist(
            id=uuid.uuid4(),
            name_canonical=canonical,
        )
        db.add(row)
        db.flush()

    return row


def _upsert_lot(db: Session, item: dict, artist: Artist) -> Lot:
    source_lot_id = item.get("source_lot_id")
    row: Optional[Lot] = None

    if source_lot_id:
        row = db.execute(
            select(Lot).where(
                Lot.source_platform == PLATFORM,
                Lot.source_lot_id == source_lot_id,
            )
        ).scalar_one_or_none()

    if row is None:
        row = Lot(
            id=uuid.uuid4(),
            artist_id=artist.id,
            source_platform=PLATFORM,
            source_lot_id=source_lot_id,
            source_url=item["source_url"],
            title=item["title"],
            technique=item["technique"],
            width_cm=item["width_cm"],
            height_cm=item["height_cm"],
        )
        db.add(row)
    else:
        # Refresh mutable fields that may have changed
        row.title = item["title"]
        row.source_url = item["source_url"]
        if item["width_cm"] is not None:
            row.width_cm = item["width_cm"]
        if item["height_cm"] is not None:
            row.height_cm = item["height_cm"]

    return row


def _upsert_listing(db: Session, item: dict, lot: Lot) -> Listing:
    row: Optional[Listing] = db.execute(
        select(Listing).where(
            Listing.lot_id == lot.id,
            Listing.platform == PLATFORM,
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if row is None:
        row = Listing(
            id=uuid.uuid4(),
            lot_id=lot.id,
            platform=PLATFORM,
            listing_url=item["source_url"],
            status="active",
            opening_bid=item["opening_bid"],
            current_bid=item["current_bid"],
            bid_count=item["bid_count"],
            sale_type=item["sale_type"],
            ends_at=item["ends_at"],
            discovered_at=now,
            last_checked_at=now,
        )
        db.add(row)
    else:
        row.current_bid = item["current_bid"]
        row.bid_count = item["bid_count"]
        row.ends_at = item["ends_at"]
        row.status = "active"
        row.last_checked_at = now

    return row


def _log_scrape(
    db: Session,
    url: str,
    status_code: Optional[int],
    items_found: int,
    error_message: Optional[str],
    duration_ms: int,
) -> None:
    db.add(
        ScrapeLog(
            id=uuid.uuid4(),
            platform=PLATFORM,
            url=url,
            status_code=status_code,
            items_found=items_found,
            error_message=error_message,
            duration_ms=duration_ms,
        )
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _first(tag: Tag, selectors: list[str]) -> Optional[Tag]:
    """Return the first child element matching any of the selectors."""
    for sel in selectors:
        found = tag.select_one(sel)
        if found:
            return found
    return None


def _text(tag: Tag, selectors: list[str]) -> Optional[str]:
    """Return stripped text of the first matching child element."""
    el = _first(tag, selectors)
    if el is None:
        return None
    text = el.get_text(separator=" ", strip=True)
    return text or None


def _ends_at_raw(tag: Tag) -> Optional[str]:
    """Prefer time[datetime] attribute over text content for dates."""
    el = tag.select_one("time[datetime]")
    if el and el.get("datetime"):
        return el["datetime"]
    return _text(tag, FIELD_SELECTORS["ends_at"])


def _parse_price(text: Optional[str]) -> Optional[float]:
    """Parse Dutch/EU price strings to float.

    Examples: '€ 1.234,56'  '€ 2.500'  '100,-'  '€ 50'  '1234.00'
    """
    if not text:
        return None
    # Strip currency symbols, non-breaking spaces
    cleaned = re.sub(r"[€$\xa0]", "", text).strip()
    # Handle Dutch trailing dash for zero cents: '100,-'
    cleaned = re.sub(r",-$", "", cleaned)

    if "," in cleaned and "." in cleaned:
        # e.g. "1.234,56" — period is thousands sep, comma is decimal sep
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        # e.g. "1.234,56" without period, or "750,00" — comma is decimal
        cleaned = cleaned.replace(",", ".")
    else:
        # Period only: if it is followed by exactly 3 digits it is a thousands
        # separator (Dutch convention: "2.500" = 2500, not 2.5).
        cleaned = re.sub(r"\.(\d{3})(?!\d)", lambda m: m.group(1), cleaned)

    try:
        return float(re.sub(r"[^\d.]", "", cleaned))
    except ValueError:
        return None


def _parse_int(text: Optional[str]) -> Optional[int]:
    """Extract the first integer from a string like '3 biedingen'."""
    if not text:
        return None
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def _parse_dimensions(text: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """Parse dimension strings like '30 x 40 cm' or '30×40'.

    Returns (width_cm, height_cm).
    """
    if not text:
        return None, None
    # Normalise multiplication signs
    text = text.replace("×", "x").replace("X", "x")
    # Look for two numbers separated by 'x'
    m = re.search(r"(\d+[.,]?\d*)\s*x\s*(\d+[.,]?\d*)", text, re.IGNORECASE)
    if m:
        w = float(m.group(1).replace(",", "."))
        h = float(m.group(2).replace(",", "."))
        return w, h
    return None, None


def _parse_date(text: Optional[str]) -> Optional[datetime]:
    """Parse Dutch date strings into an aware UTC datetime.

    Handles:
        ISO 8601:  '2025-06-15T20:00:00'  '2025-06-15'
        Dutch:     '15 jun 2025 20:00'    '15 juni 2025'
        Numeric:   '15-06-2025'           '15/06/2025'
    """
    if not text:
        return None
    text = text.strip()

    # ISO 8601 (from time[datetime] attribute)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # Dutch textual: "15 jun 2025 20:00" or "15 juni 2025"
    m = re.search(
        r"(\d{1,2})\s+([a-z]+)\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?",
        text,
        re.IGNORECASE,
    )
    if m:
        day = int(m.group(1))
        month_str = m.group(2).lower()
        year = int(m.group(3))
        hour = int(m.group(4)) if m.group(4) else 0
        minute = int(m.group(5)) if m.group(5) else 0
        month = _NL_MONTHS.get(month_str)
        if month:
            try:
                return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            except ValueError:
                pass

    # Numeric: "15-06-2025" or "15/06/2025"
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text)
    if m:
        try:
            return datetime(
                int(m.group(3)), int(m.group(2)), int(m.group(1)),
                tzinfo=timezone.utc,
            )
        except ValueError:
            pass

    log.debug("Could not parse date: %r", text)
    return None


def _lot_id_from_url(url: Optional[str]) -> Optional[str]:
    """Extract trailing slug or numeric ID from a lot URL."""
    if not url:
        return None
    # Match /items/some-slug-12345 or /lot/12345
    m = re.search(r"/(?:items|lot|kavel)/([^/?#]+)", url)
    return m.group(1) if m else None


def _normalise_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return " ".join(name.strip().split())


def _maybe_save_debug_html(html: str, offset: int) -> None:
    if os.getenv("ARTARB_DEBUG_HTML"):
        path = f"/tmp/kunstveiling_page_{offset}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log.debug("Saved raw HTML to %s", path)
