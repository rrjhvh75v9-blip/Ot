"""
Scraper for Barnebys — international art auction search aggregator.

Barnebys aggregates upcoming and past lots from hundreds of auction houses.
This scraper targets the upcoming-lots search pages, which render SSR HTML.

Entry point:
    from artarb.scrapers.barnebys import scrape
    scrape()               # all configured search queries, all pages
    scrape(max_pages=5)    # first 5 pages per query

Debug:
    Set ARTARB_DEBUG_HTML=1 to write raw HTML to /tmp/barnebys_page_<q>_<page>.html
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
from artarb.scrapers.keyword_watchlist import flag_lot

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLATFORM = "barnebys"
BASE_URL = "https://www.barnebys.com"

# Search queries targeting the specialty categories we care about.
# Each is appended to the upcoming-lots search URL.
SEARCH_QUERIES: list[str] = [
    "",            # general upcoming lots (no filter)
    "netsuke",
    "tsuba",
    "studio pottery",
    "mourlot",
    "griffelkunst",
    "maeght",
]

RATE_LIMIT_SEC = 3          # Barnebys is more rate-sensitive than kunstveiling
REQUEST_TIMEOUT = 25
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.barnebys.com/",
}

# ---------------------------------------------------------------------------
# CSS selectors  (verify against live HTML if site markup changes)
# ---------------------------------------------------------------------------

ITEM_SELECTORS: list[str] = [
    "[data-object-id]",
    "article.item",
    "div.item",
    "li.item",
    "article[class*='object']",
    "div[class*='object-card']",
    "article",
]

FIELD_SELECTORS: dict[str, list[str]] = {
    "link":        ["a[href*='/buy/']", "a[href*='/lot/']", "a[href*='/realised']", "a"],
    "title":       [
        "[data-object-title]",
        ".item__title", ".object-card__title",
        "[class*='item-title']", "[class*='object-title']",
        "h2", "h3",
    ],
    "artist":      [
        "[data-artist-name]",
        ".item__artist", ".object-card__artist",
        "[class*='artist']", "[class*='maker']",
    ],
    "estimate":    [
        "[data-estimate]",
        ".item__estimate", ".object-card__estimate",
        "[class*='estimate']", "[class*='price']",
    ],
    "current_bid": [
        "[data-current-bid]", "[data-bid]",
        ".item__bid", "[class*='current-bid']",
    ],
    "bid_count": [
        "[data-bid-count]", "[class*='bid-count']",
    ],
    "ends_at": [
        "time[datetime]",
        "[data-end-time]", "[data-ends-at]",
        "[class*='end-time']", "[class*='closing']", "[class*='ends']",
    ],
    "sale_type": [
        "[data-sale-type]", "[class*='sale-type']",
    ],
    "auction_house": [
        "[data-auction-house]",
        ".item__auction-house", "[class*='auction-house']",
        "[class*='house-name']",
    ],
}

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape(max_pages: Optional[int] = None) -> None:
    """Scrape all configured Barnebys search queries.

    Args:
        max_pages: Maximum pages per search query (None = unlimited).
    """
    http = _build_session()
    total_pages = 0

    with get_session() as db:
        for query in SEARCH_QUERIES:
            label = query or "(all)"
            log.info("Barnebys: scraping query %r", label)
            pages = _scrape_query(db, http, query, max_pages)
            total_pages += pages

    log.info("Barnebys scrape complete. Total pages: %d", total_pages)


# ---------------------------------------------------------------------------
# Per-query / per-page logic
# ---------------------------------------------------------------------------

def _scrape_query(
    db: Session,
    http: requests.Session,
    query: str,
    max_pages: Optional[int],
) -> int:
    pages_scraped = 0
    page = 1

    while True:
        if max_pages is not None and pages_scraped >= max_pages:
            break

        items_found, error = _scrape_page(db, http, query, page)
        pages_scraped += 1

        if error:
            log.warning("  query=%r page=%d error: %s", query or "(all)", page, error)

        if items_found == 0 and error is None:
            log.info("  query=%r page=%d: no items — done", query or "(all)", page)
            break

        page += 1
        time.sleep(RATE_LIMIT_SEC)

    return pages_scraped


def _scrape_page(
    db: Session,
    http: requests.Session,
    query: str,
    page: int,
) -> tuple[int, Optional[str]]:
    url = _build_url(query, page)
    start = datetime.now(timezone.utc)
    status_code: Optional[int] = None
    error_msg: Optional[str] = None
    items_found = 0

    try:
        resp = _fetch(http, url)
        status_code = resp.status_code
        resp.raise_for_status()

        _maybe_save_debug_html(resp.text, query or "all", page)

        soup = BeautifulSoup(resp.text, "html.parser")
        raw_items = _parse_items(soup)
        items_found = len(raw_items)
        log.info("  query=%r page=%d  items=%d", query or "(all)", page, items_found)

        for item in raw_items:
            try:
                _upsert_item(db, item)
            except Exception as exc:
                log.error("Failed to write barnebys item %s: %s", item.get("source_url"), exc)

    except requests.HTTPError as exc:
        error_msg = f"HTTP {exc.response.status_code}: {exc}"
        log.error("  query=%r page=%d  %s", query or "(all)", page, error_msg)
    except requests.RequestException as exc:
        error_msg = str(exc)
        log.error("  query=%r page=%d  request error: %s", query or "(all)", page, error_msg)
    except Exception as exc:
        error_msg = str(exc)
        log.exception("  query=%r page=%d  unexpected: %s", query or "(all)", page, error_msg)
    finally:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        _log_scrape(db, url=url, status_code=status_code,
                    items_found=items_found, error_message=error_msg,
                    duration_ms=duration_ms)

    return items_found, error_msg


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _build_url(query: str, page: int) -> str:
    """Construct the Barnebys upcoming-lots search URL."""
    params = f"type=upcoming&page={page}&language=en"
    if query:
        params += f"&q={requests.utils.quote(query)}"
    return f"{BASE_URL}/search?{params}"


def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _fetch(http: requests.Session, url: str) -> requests.Response:
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.debug("GET %s  (attempt %d)", url, attempt)
            return http.get(url, timeout=REQUEST_TIMEOUT)
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
    items: list[Tag] = []
    for selector in ITEM_SELECTORS:
        candidates = soup.select(selector)
        if candidates:
            log.debug("Barnebys item container: %r (%d items)", selector, len(candidates))
            items = candidates
            break
    if not items:
        log.warning("Barnebys: no lot items found — HTML structure may have changed.")
        return []
    return [p for tag in items if (p := _parse_item(tag)) is not None]


def _parse_item(tag: Tag) -> Optional[dict]:
    link_tag = _first(tag, FIELD_SELECTORS["link"])
    href = link_tag["href"] if link_tag and link_tag.has_attr("href") else None
    if href and href.startswith("/"):
        href = BASE_URL + href

    title = _text(tag, FIELD_SELECTORS["title"])
    if not title:
        return None

    artist_raw = _text(tag, FIELD_SELECTORS["artist"])
    # Barnebys shows estimate ranges; use lower bound as opening_bid
    estimate_raw = _text(tag, FIELD_SELECTORS["estimate"])
    opening_bid = _parse_price_range_low(estimate_raw)
    current_bid = _parse_price(_text(tag, FIELD_SELECTORS["current_bid"]))
    bid_count = _parse_int(_text(tag, FIELD_SELECTORS["bid_count"]))
    sale_type = _text(tag, FIELD_SELECTORS["sale_type"])
    ends_at = _parse_date(_ends_at_raw(tag))
    auction_house = _text(tag, FIELD_SELECTORS["auction_house"])

    source_lot_id = (
        tag.get("data-object-id")
        or tag.get("data-id")
        or _lot_id_from_url(href)
    )

    return {
        "title": title,
        "artist_name": _normalise_name(artist_raw),
        "auction_house": auction_house,
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
        artist = _upsert_artist(db, "Unknown")

    lot = _upsert_lot(db, item, artist)
    _upsert_listing(db, item, lot)
    flag_lot(db, lot)
    db.flush()


def _upsert_artist(db: Session, name: str) -> Artist:
    canonical = name.strip()
    row = db.execute(
        select(Artist).where(Artist.name_canonical.ilike(canonical))
    ).scalar_one_or_none()
    if row is None:
        row = Artist(id=uuid.uuid4(), name_canonical=canonical)
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
        )
        db.add(row)
    else:
        row.title = item["title"]
        row.source_url = item["source_url"]

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
    db.add(ScrapeLog(
        id=uuid.uuid4(),
        platform=PLATFORM,
        url=url,
        status_code=status_code,
        items_found=items_found,
        error_message=error_message,
        duration_ms=duration_ms,
    ))


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _first(tag: Tag, selectors: list[str]) -> Optional[Tag]:
    for sel in selectors:
        found = tag.select_one(sel)
        if found:
            return found
    return None


def _text(tag: Tag, selectors: list[str]) -> Optional[str]:
    el = _first(tag, selectors)
    if el is None:
        return None
    text = el.get_text(separator=" ", strip=True)
    return text or None


def _ends_at_raw(tag: Tag) -> Optional[str]:
    el = tag.select_one("time[datetime]")
    if el and el.get("datetime"):
        return el["datetime"]
    return _text(tag, FIELD_SELECTORS["ends_at"])


def _parse_price(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    cleaned = re.sub(r"[€$£\xa0]", "", text).strip()
    cleaned = re.sub(r",-$", "", cleaned)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = re.sub(r"\.(\d{3})(?!\d)", lambda m: m.group(1), cleaned)
    try:
        return float(re.sub(r"[^\d.]", "", cleaned))
    except ValueError:
        return None


def _parse_price_range_low(text: Optional[str]) -> Optional[float]:
    """For estimate ranges '€ 200 - € 400', return the lower bound."""
    if not text:
        return None
    # Split on en-dash, em-dash or hyphen with spaces
    parts = re.split(r"\s*[-–—]\s*", text, maxsplit=1)
    return _parse_price(parts[0])


def _parse_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def _parse_date(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                            tzinfo=timezone.utc)
        except ValueError:
            pass
    log.debug("Barnebys: could not parse date: %r", text)
    return None


def _lot_id_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/(?:buy|lot|realised-prices/lot)/([^/?#]+)", url)
    return m.group(1) if m else None


def _normalise_name(name: Optional[str]) -> Optional[str]:
    return " ".join(name.strip().split()) if name else None


def _maybe_save_debug_html(html: str, query: str, page: int) -> None:
    if os.getenv("ARTARB_DEBUG_HTML"):
        safe = re.sub(r"[^a-z0-9]", "_", query.lower())[:20]
        path = f"/tmp/barnebys_page_{safe}_{page}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log.debug("Saved raw HTML to %s", path)
