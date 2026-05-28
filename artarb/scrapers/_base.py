"""
Shared DB-write and parsing utilities for all auction scrapers.

Every auction scraper imports from here to avoid duplicating the same
upsert / parse logic 15 times.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from artarb.models.base import Artist, Listing, Lot, PriceEvent, ScrapeLog
from artarb.scrapers.keyword_watchlist import flag_lot

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

COMMON_HEADERS: dict = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def build_http_session(extra_headers: Optional[dict] = None) -> requests.Session:
    s = requests.Session()
    s.headers.update(COMMON_HEADERS)
    if extra_headers:
        s.headers.update(extra_headers)
    return s


def try_playwright_get(
    url: str,
    wait_selector: Optional[str] = None,
    timeout_ms: int = 30_000,
) -> Optional[str]:
    """Fetch *url* with Playwright, return HTML string or None if unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=timeout_ms)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=10_000)
                except Exception:
                    pass
            else:
                page.wait_for_load_state("networkidle", timeout=15_000)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        log.warning("Playwright fetch failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def upsert_artist(db: Session, name: Optional[str]) -> Artist:
    canonical = " ".join((name or "Unknown").strip().split())
    row = db.execute(
        select(Artist).where(Artist.name_canonical.ilike(canonical))
    ).scalar_one_or_none()
    if row is None:
        row = Artist(id=uuid.uuid4(), name_canonical=canonical)
        db.add(row)
        db.flush()
    return row


def upsert_lot(db: Session, platform: str, item: dict, artist: Artist) -> Lot:
    source_lot_id = item.get("source_lot_id")
    row: Optional[Lot] = None
    if source_lot_id:
        row = db.execute(
            select(Lot).where(
                Lot.source_platform == platform,
                Lot.source_lot_id == source_lot_id,
            )
        ).scalar_one_or_none()
    if row is None:
        row = Lot(
            id=uuid.uuid4(),
            artist_id=artist.id,
            source_platform=platform,
            source_lot_id=source_lot_id,
            source_url=item.get("source_url"),
            title=item.get("title"),
            technique=item.get("technique"),
            medium=item.get("medium"),
            width_cm=item.get("width_cm"),
            height_cm=item.get("height_cm"),
            country_sale=item.get("country_sale"),
        )
        db.add(row)
    else:
        if item.get("title"):
            row.title = item["title"]
        if item.get("source_url"):
            row.source_url = item["source_url"]
        if item.get("technique"):
            row.technique = item["technique"]
    return row


def upsert_listing(db: Session, platform: str, item: dict, lot: Lot) -> Listing:
    row: Optional[Listing] = db.execute(
        select(Listing).where(
            Listing.lot_id == lot.id,
            Listing.platform == platform,
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = Listing(
            id=uuid.uuid4(),
            lot_id=lot.id,
            platform=platform,
            listing_url=item.get("source_url"),
            status=item.get("status", "active"),
            opening_bid=item.get("opening_bid"),
            current_bid=item.get("current_bid"),
            bid_count=item.get("bid_count"),
            sale_type=item.get("sale_type"),
            ends_at=item.get("ends_at"),
            discovered_at=now,
            last_checked_at=now,
        )
        db.add(row)
    else:
        row.current_bid = item.get("current_bid")
        row.bid_count = item.get("bid_count")
        row.ends_at = item.get("ends_at")
        row.status = item.get("status", "active")
        row.last_checked_at = now
    return row


def write_price_event(db: Session, platform: str, item: dict, lot: Lot) -> None:
    """Write hammer + optional estimate price events for a sold lot."""
    currency = item.get("currency", "EUR")
    sale_date = item.get("sale_date")
    _upsert_event(db, lot, platform, "hammer",        item.get("hammer_price"),  currency, sale_date)
    _upsert_event(db, lot, platform, "estimate_low",  item.get("estimate_low"),  currency, sale_date)
    _upsert_event(db, lot, platform, "estimate_high", item.get("estimate_high"), currency, sale_date)


def _upsert_event(
    db: Session,
    lot: Lot,
    platform: str,
    event_type: str,
    amount: Optional[float],
    currency: str,
    sale_date: Optional[datetime],
) -> None:
    if amount is None:
        return
    existing = db.execute(
        select(PriceEvent).where(
            PriceEvent.lot_id == lot.id,
            PriceEvent.platform == platform,
            PriceEvent.event_type == event_type,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(PriceEvent(
            id=uuid.uuid4(),
            lot_id=lot.id,
            platform=platform,
            event_type=event_type,
            amount=amount,
            currency=currency,
            sale_date=sale_date,
        ))
    elif abs(float(existing.amount or 0) - amount) > 1.0:
        existing.amount = amount
        existing.sale_date = sale_date


def log_scrape(
    db: Session,
    platform: str,
    url: str,
    status_code: Optional[int],
    items_found: int,
    error_message: Optional[str],
    duration_ms: int,
) -> None:
    db.add(ScrapeLog(
        id=uuid.uuid4(),
        platform=platform,
        url=url,
        status_code=status_code,
        items_found=items_found,
        error_message=error_message,
        duration_ms=duration_ms,
    ))


def upsert_item(db: Session, platform: str, item: dict) -> None:
    """Full pipeline: artist → lot → listing or price_event → keyword flags."""
    artist = upsert_artist(db, item.get("artist_name"))
    lot = upsert_lot(db, platform, item, artist)
    if item.get("status") == "sold":
        write_price_event(db, platform, item, lot)
    else:
        upsert_listing(db, platform, item, lot)
    flag_lot(db, lot)
    db.flush()


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def parse_price(text: Optional[str]) -> Optional[float]:
    """Parse EU/UK price strings.  Handles ranges (returns lower bound)."""
    if not text:
        return None
    if re.search(r"[-–—]", text):
        text = re.split(r"\s*[-–—]\s*", text, maxsplit=1)[0]
    cleaned = re.sub(r"[€£$\xa0 ]", "", text).strip()
    cleaned = re.sub(r"[,.]?-+$", "", cleaned)
    if "," in cleaned and "." in cleaned:
        if cleaned.index(",") < cleaned.index("."):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = re.sub(r",(\d{2})$", r".\1", cleaned).replace(",", "")
    else:
        cleaned = re.sub(r"\.(\d{3})(?!\d)", lambda m: m.group(1), cleaned)
    try:
        return float(re.sub(r"[^\d.]", "", cleaned)) or None
    except ValueError:
        return None


def parse_date(text: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 and common EU date strings to UTC datetime."""
    if not text:
        return None
    text = text.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d",
        "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%d-%m-%Y %H:%M", "%d-%m-%Y",
        "%d.%m.%Y %H:%M", "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                            tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def parse_dimensions(text: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    if not text:
        return None, None
    text = text.replace("×", "x").replace("X", "x")
    m = re.search(r"(\d+[.,]?\d*)\s*x\s*(\d+[.,]?\d*)", text, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
    return None, None


def detect_currency(text: Optional[str]) -> str:
    if not text:
        return "EUR"
    if "£" in text or "GBP" in text:
        return "GBP"
    if "$" in text or "USD" in text:
        return "USD"
    return "EUR"


def lot_id_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    for pat in (r"/lot[s]?/([^/?#]+)", r"/kavel/([^/?#]+)", r"/items?/([^/?#]+)",
                r"/ergebnis/([^/?#]+)", r"/result/([^/?#]+)"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    m = re.search(r"/([A-Za-z0-9_-]{4,})/?(?:[?#]|$)", url)
    return m.group(1) if m else None
