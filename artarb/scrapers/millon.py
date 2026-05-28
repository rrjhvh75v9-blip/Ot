"""Scraper for Millon — French auction house (Paris / Brussels / Vienna).

Focus: French modern art, decorative arts, sold price data.
"""
import logging, re, time
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

PLATFORM = "millon"
COUNTRY_SALE = "FR"
CURRENCY = "EUR"
BASE_URL = "https://www.millon.com"
RESULTS_PATH = "/resultats"
RATE_LIMIT_SEC = 3
REQUEST_TIMEOUT = 25

ITEM_SELECTORS = [
    "article.lot", ".lot-item", "[data-lot-id]",
    "[class*='lot-card']", "[class*='result-item']", "article", "li.lot",
]
FIELD_SELECTORS = {
    "link":     ["a[href*='/lot']", "a[href*='/oeuvre']", "a"],
    "title":    [".lot-title", ".artwork-title", ".titre-lot", "h2", "h3"],
    "artist":   [".artist", ".artiste", "[class*='artist']"],
    "technique":[".technique", ".medium", ".techniq"],
    "dims":     [".dimensions", ".mesures"],
    "estimate": [".estimate", ".estimation", "[class*='estimate']"],
    "hammer":   [".hammer", ".adjudication", ".result-price", "[class*='hammer']"],
    "date":     ["time[datetime]", ".date-vente", ".sale-date"],
}


def scrape(max_pages: Optional[int] = None) -> None:
    http = build_http_session({"Referer": BASE_URL, "Accept-Language": "fr-FR,fr;q=0.9"})
    with get_session() as db:
        for page in range(1, (max_pages or 50) + 1):
            n, err = _scrape_page(db, http, page)
            if n == 0 and not err:
                break
            time.sleep(RATE_LIMIT_SEC)


def _scrape_page(db: Session, http: requests.Session, page: int) -> tuple[int, Optional[str]]:
    url = f"{BASE_URL}{RESULTS_PATH}?page={page}"
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
        log.info("millon page=%d items=%d", page, items_found)
        for item in raw:
            try:
                upsert_item(db, PLATFORM, item)
            except Exception as exc:
                log.error("millon item error: %s", exc)
    except requests.HTTPError as exc:
        error_msg = f"HTTP {exc.response.status_code}"
    except Exception as exc:
        error_msg = str(exc)
        log.error("millon page=%d %s", page, exc)
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
    href = None
    for s in FIELD_SELECTORS["link"]:
        a = tag.select_one(s)
        if a and a.has_attr("href"):
            href = a["href"]
            break
    if href and href.startswith("/"):
        href = BASE_URL + href

    def txt(key):
        for s in FIELD_SELECTORS[key]:
            el = tag.select_one(s)
            if el:
                t = el.get_text(" ", strip=True)
                if t:
                    return t
        return None

    title = txt("title")
    if not title:
        return None

    w, h = parse_dimensions(txt("dims"))
    est_raw = txt("estimate")
    parts = re.split(r"\s*[-–/]\s*", est_raw) if est_raw else []
    est_low = parse_price(parts[0]) if parts else None
    est_high = parse_price(parts[1]) if len(parts) > 1 else None
    hammer = parse_price(txt("hammer"))
    date_el = tag.select_one("time[datetime]")
    sale_date = parse_date(date_el["datetime"] if date_el else txt("date"))
    status = "sold" if hammer else "active"

    return {
        "title": title, "artist_name": txt("artist"),
        "technique": txt("technique"), "width_cm": w, "height_cm": h,
        "source_lot_id": tag.get("data-lot-id") or _lot_id(href),
        "source_url": href, "status": status,
        "hammer_price": hammer, "estimate_low": est_low, "estimate_high": est_high,
        "sale_date": sale_date if status == "sold" else None,
        "currency": CURRENCY, "country_sale": COUNTRY_SALE,
        "opening_bid": est_low, "ends_at": sale_date if status == "active" else None,
    }


def _lot_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/lot[s]?/([^/?#]+)|/oeuvre/([^/?#]+)|/(\d{4,})", url)
    return next((g for g in (m.groups() if m else []) if g), None)
