"""Scraper for Phillips — International auction house (London / New York).

Focus: Design, Photographs, Editions & Works on Paper, 20th-century art.
Currency detection handles GBP, USD, EUR lots.
Requires Playwright (JavaScript-rendered results pages).
"""
import logging, re, time
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag
from sqlalchemy.orm import Session

from artarb.database import get_session
from artarb.scrapers._base import (
    build_http_session, detect_currency, log_scrape, parse_date, parse_dimensions,
    parse_price, try_playwright_get, upsert_item,
)

log = logging.getLogger(__name__)

PLATFORM = "phillips"
COUNTRY_SALE = "GB"
BASE_URL = "https://www.phillips.com"
RESULTS_PATH = "/results"
RATE_LIMIT_SEC = 4
REQUEST_TIMEOUT = 35

ITEM_SELECTORS = [
    "[data-lot-id]", "article.lot", ".lot-result",
    "[class*='LotItem']", "[class*='lot-row']",
    "[class*='result-item']", "article", "li",
]
FIELD_SELECTORS = {
    "link":     ["a[href*='/lot']", "a[href*='/auction']", "a"],
    "title":    [".lot-title", ".LotItem__title", ".work-title", "h2", "h3"],
    "artist":   [".lot-artist", ".LotItem__artist", ".maker", "[class*='artist']"],
    "technique":[".technique", ".medium", ".LotItem__medium"],
    "dims":     [".dimensions", ".LotItem__dimensions", "[class*='dimension']"],
    "estimate": [".estimate", ".LotItem__estimate", "[class*='estimate']"],
    "hammer":   [".hammer-price", ".LotItem__hammer", ".sold-for", "[class*='hammer']"],
    "date":     ["time[datetime]", ".sale-date", "[class*='date']"],
}


def scrape(max_pages: Optional[int] = None) -> None:
    http = build_http_session({"Referer": BASE_URL})
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
        html = try_playwright_get(
            url,
            wait_selector="[data-lot-id], article.lot, [class*='LotItem'], [class*='lot-row']",
            timeout_ms=40_000,
        )
        if html is None:
            resp = http.get(url, timeout=REQUEST_TIMEOUT)
            status_code = resp.status_code
            resp.raise_for_status()
            html = resp.text
        else:
            status_code = 200
        soup = BeautifulSoup(html, "html.parser")
        raw = _parse_items(soup)
        items_found = len(raw)
        log.info("phillips page=%d items=%d", page, items_found)
        for item in raw:
            try:
                upsert_item(db, PLATFORM, item)
            except Exception as exc:
                log.error("phillips item error: %s", exc)
    except requests.HTTPError as exc:
        error_msg = f"HTTP {exc.response.status_code}"
    except Exception as exc:
        error_msg = str(exc)
        log.error("phillips page=%d %s", page, exc)
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
    hammer_raw = txt("hammer")
    hammer = parse_price(hammer_raw)
    currency = detect_currency(hammer_raw or est_raw or "")
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
        "currency": currency, "country_sale": COUNTRY_SALE,
        "opening_bid": est_low, "ends_at": sale_date if status == "active" else None,
    }


def _lot_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/lot[s]?/([^/?#]+)|/auction/[^/?#]+/([^/?#]+)|/(\d{4,})", url)
    return next((g for g in (m.groups() if m else []) if g), None)
