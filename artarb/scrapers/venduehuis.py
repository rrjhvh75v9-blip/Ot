"""Scraper for Venduehuis — Dutch auction house (Den Haag).

Targets upcoming and recent auction listings.
Focus: Dutch Golden Age, Hague School, CoBrA, applied arts.
"""
import logging, re, time, uuid
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag
from sqlalchemy.orm import Session

from artarb.database import get_session
from artarb.scrapers._base import (
    build_http_session, log_scrape, parse_date, parse_dimensions,
    parse_price, upsert_item,
)

log = logging.getLogger(__name__)

PLATFORM = "venduehuis"
COUNTRY_SALE = "NL"
CURRENCY = "EUR"
BASE_URL = "https://www.venduehuis.com"
LISTINGS_PATH = "/nl/veilingen"
RATE_LIMIT_SEC = 3
REQUEST_TIMEOUT = 25

ITEM_SELECTORS = ["article.lot", "div.lot", ".auction-lot", "[data-lot-id]", "article", "li.item"]
FIELD_SELECTORS = {
    "link":     ["a[href*='/kavel']", "a[href*='/lot']", "a"],
    "title":    [".lot-title", ".kavel-titel", "h2", "h3"],
    "artist":   [".lot-artist", ".kunstenaar", "[data-artist]", ".maker"],
    "technique":["technique", ".techniek", ".medium"],
    "dims":     [".dimensions", ".afmetingen", ".maten"],
    "estimate": [".estimate", ".schatting", ".verwachte-prijs"],
    "hammer":   [".hammer-price", ".toewijzing", ".resultaat", ".prijs"],
    "date":     ["time[datetime]", ".sale-date", ".veilingdatum"],
}


def scrape(max_pages: Optional[int] = None) -> None:
    http = build_http_session({"Referer": BASE_URL, "Accept-Language": "nl-NL,nl;q=0.9"})
    with get_session() as db:
        for page in range(1, (max_pages or 50) + 1):
            n, err = _scrape_page(db, http, page)
            if n == 0 and not err:
                break
            time.sleep(RATE_LIMIT_SEC)


def _scrape_page(db: Session, http: requests.Session, page: int) -> tuple[int, Optional[str]]:
    url = f"{BASE_URL}{LISTINGS_PATH}?page={page}"
    start = datetime.now(timezone.utc)
    status_code = error_msg = None
    items_found = 0
    try:
        resp = http.get(url, timeout=REQUEST_TIMEOUT)
        status_code = resp.status_code
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        raw = _parse_items(soup)
        items_found = len(raw)
        log.info("venduehuis page=%d items=%d", page, items_found)
        for item in raw:
            try:
                upsert_item(db, PLATFORM, item)
            except Exception as exc:
                log.error("venduehuis item error: %s", exc)
    except requests.HTTPError as exc:
        error_msg = f"HTTP {exc.response.status_code}"
        log.error("venduehuis page=%d %s", page, error_msg)
    except Exception as exc:
        error_msg = str(exc)
        log.error("venduehuis page=%d %s", page, exc)
    finally:
        ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        log_scrape(db, PLATFORM, url, status_code, items_found, error_msg, ms)
    return items_found, error_msg


def _parse_items(soup: BeautifulSoup) -> list[dict]:
    for sel in ITEM_SELECTORS:
        tags = soup.select(sel)
        if tags:
            return [p for t in tags if (p := _parse_item(t))]
    return []


def _parse_item(tag: Tag) -> Optional[dict]:
    link = tag.select_one(" ".join(FIELD_SELECTORS["link"]).replace(" ", ", "))
    if not link:
        for s in FIELD_SELECTORS["link"]:
            link = tag.select_one(s)
            if link:
                break
    href = link["href"] if link and link.has_attr("href") else None
    if href and href.startswith("/"):
        href = BASE_URL + href

    def txt(selectors):
        for s in selectors:
            el = tag.select_one(s)
            if el:
                t = el.get_text(" ", strip=True)
                if t:
                    return t
        return None

    title = txt(FIELD_SELECTORS["title"])
    if not title:
        return None

    artist = txt(FIELD_SELECTORS["artist"])
    dims_raw = txt(FIELD_SELECTORS["dims"])
    w, h = parse_dimensions(dims_raw)
    est_raw = txt(FIELD_SELECTORS["estimate"])
    hammer_raw = txt(FIELD_SELECTORS["hammer"])
    hammer = parse_price(hammer_raw)
    est_parts = re.split(r"\s*[-–]\s*", est_raw) if est_raw else []
    est_low = parse_price(est_parts[0]) if est_parts else None
    est_high = parse_price(est_parts[1]) if len(est_parts) > 1 else None

    date_el = tag.select_one("time[datetime]")
    sale_date = parse_date(date_el["datetime"] if date_el else txt(FIELD_SELECTORS["date"]))

    status = "sold" if hammer else "active"
    return {
        "title": title, "artist_name": artist,
        "technique": txt(FIELD_SELECTORS["technique"]),
        "width_cm": w, "height_cm": h,
        "source_lot_id": tag.get("data-lot-id") or _lot_id(href),
        "source_url": href,
        "status": status,
        "hammer_price": hammer, "estimate_low": est_low, "estimate_high": est_high,
        "sale_date": sale_date if status == "sold" else None,
        "currency": CURRENCY, "country_sale": COUNTRY_SALE,
        "opening_bid": est_low, "ends_at": sale_date if status == "active" else None,
    }


def _lot_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/kavel/([^/?#]+)|/lot/([^/?#]+)|/(\d{4,})", url)
    return next((g for g in (m.groups() if m else []) if g), None)
