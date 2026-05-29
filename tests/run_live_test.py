#!/usr/bin/env python3
"""
Live integration test for all 15 auction house scrapers.

For each scraper:
  - Attempts one real HTTP request to the site's results/listings page
  - Reports status code, item count, sample titles, and any errors
  - Tries alternate approaches if the primary is blocked

Run with:  python tests/run_live_test.py
Output:    tests/live_test_results.txt
"""

from __future__ import annotations
import sys, time, traceback, textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Target table: (name, url, item_selectors, title_selectors)
# ---------------------------------------------------------------------------

TARGETS = [
    {
        "name": "kunstveiling",
        "url": "https://www.kunstveiling.nl/veilingen?page=1",
        "fallback_url": "https://www.kunstveiling.nl/veilingen",
        "item_sel": ["article.lot", ".lot-item", "[data-lot-id]", "article", "li.kavel"],
        "title_sel": [".lot-title", ".kavel-titel", "h2", "h3"],
        "country": "NL",
    },
    {
        "name": "catawiki",
        "url": "https://www.catawiki.com/en/c/1-art?page=1",
        "fallback_url": "https://www.catawiki.com/en/c/1-art",
        "item_sel": ["article", "[data-lot-id]", ".lot-card", "li"],
        "title_sel": ["h2", "h3", ".lot-title", "[class*='title']"],
        "country": "NL",
    },
    {
        "name": "barnebys",
        "url": "https://www.barnebys.com/buy?q=painting&page=1",
        "fallback_url": "https://www.barnebys.com/buy?q=painting",
        "item_sel": ["[data-lot-id]", "article", ".lot-card", "li"],
        "title_sel": [".lot-title", "h2", "h3", "[class*='title']"],
        "country": "SE",
    },
    {
        "name": "venduehuis",
        "url": "https://www.venduehuis.com/nl/veilingen?page=1",
        "fallback_url": "https://www.venduehuis.com/nl/veilingen",
        "item_sel": ["article.lot", "[data-lot-id]", "article", "li"],
        "title_sel": [".lot-title", ".kavel-titel", "h2", "h3"],
        "country": "NL",
    },
    {
        "name": "bernaerts",
        "url": "https://www.bernaerts.be/nl/veilingen?page=1",
        "fallback_url": "https://www.bernaerts.be/nl/veilingen",
        "item_sel": ["article.lot", "[data-lot-id]", "article", "li"],
        "title_sel": [".lot-title", ".lot-naam", "h2", "h3"],
        "country": "BE",
    },
    {
        "name": "vavato",
        "url": "https://www.vavato.com/nl/veilingen?page=1",
        "fallback_url": "https://www.vavato.com/nl/veilingen",
        "item_sel": ["[data-lot-id]", "article.lot-card", "[class*='lot-card']", "article", "li"],
        "title_sel": [".lot-card__title", ".item-title", "h2", "h3"],
        "country": "BE",
    },
    {
        "name": "bassenge",
        "url": "https://www.bassenge.com/auktionen?seite=1",
        "fallback_url": "https://www.bassenge.com/auktionen",
        "item_sel": ["article.lot", "[data-lot-id]", "article", "li.lot"],
        "title_sel": [".lot-title", ".werktitel", "h2", "h3"],
        "country": "DE",
    },
    {
        "name": "van_ham",
        "url": "https://auction.van-ham.com/en/results?page=1",
        "fallback_url": "https://auction.van-ham.com/en/results",
        "item_sel": ["[data-lot-id]", "article.lot", "div.lot-result", "article", "li.lot"],
        "title_sel": [".lot-title", ".artwork-title", "h2", "h3"],
        "country": "DE",
    },
    {
        "name": "arenberg",
        "url": "https://www.arenberg-auctions.com/nl/veilingen?page=1",
        "fallback_url": "https://www.arenberg-auctions.com/nl/veilingen",
        "item_sel": ["article.lot", ".lot-item", "[data-lot]", "div.kavel", "article", "li"],
        "title_sel": [".lot-title", ".lot-name", "h2", "h3"],
        "country": "BE",
    },
    {
        "name": "piasa",
        "url": "https://www.piasa.fr/resultats?page=1",
        "fallback_url": "https://www.piasa.fr/resultats",
        "item_sel": ["article.lot", ".lot-item", "[data-lot-id]", "article", "li.lot"],
        "title_sel": [".lot-title", ".artwork-title", "h2", "h3", ".titre"],
        "country": "FR",
    },
    {
        "name": "lempertz",
        "url": "https://www.lempertz.com/en/catalogues?page=1",
        "fallback_url": "https://www.lempertz.com/en/catalogues",
        "item_sel": ["article.lot", "tr.lot", ".lot-result", "[data-lot-id]", "article", "li.lot"],
        "title_sel": [".lot-title", ".werk-titel", "h2", "h3", "td.title"],
        "country": "DE",
    },
    {
        "name": "aguttes",
        "url": "https://www.aguttes.com/resultats?page=1",
        "fallback_url": "https://www.aguttes.com/resultats",
        "item_sel": ["article.lot", ".lot-item", "[data-lot-id]", "article", "li.lot"],
        "title_sel": [".lot-title", ".artwork-title", "h2", "h3"],
        "country": "FR",
    },
    {
        "name": "millon",
        "url": "https://www.millon.com/resultats?page=1",
        "fallback_url": "https://www.millon.com/resultats",
        "item_sel": ["article.lot", ".lot-item", "[data-lot-id]", "article", "li.lot"],
        "title_sel": [".lot-title", ".artwork-title", ".titre-lot", "h2", "h3"],
        "country": "FR",
    },
    {
        "name": "dorotheum",
        "url": "https://www.dorotheum.com/en/auctions/results?page=1",
        "fallback_url": "https://www.dorotheum.com/en/auctions/results",
        "item_sel": ["[data-lot-id]", "article.lot", ".lot-result", "article", "li"],
        "title_sel": [".lot-title", ".artwork-title", ".title", "h2", "h3"],
        "country": "AT",
    },
    {
        "name": "ketterer",
        "url": "https://www.kettererkunst.de/ergebnisse?seite=1",
        "fallback_url": "https://www.kettererkunst.de/ergebnisse",
        "item_sel": ["[data-lot-id]", "article.lot", ".lot-ergebnis", "article", "li.lot"],
        "title_sel": [".lot-title", ".werktitel", ".titel", "h2", "h3"],
        "country": "DE",
    },
    {
        "name": "drouot",
        "url": "https://www.drouot.com/lots?page=1",
        "fallback_url": "https://www.drouot.com/lots",
        "item_sel": ["[data-lot-id]", "article.lot", ".lot-card", "article", "li"],
        "title_sel": [".lot-title", ".LotCard__title", ".titre", "h2", "h3"],
        "country": "FR",
    },
    {
        "name": "bonhams",
        "url": "https://www.bonhams.com/results?page=1",
        "fallback_url": "https://www.bonhams.com/results",
        "item_sel": ["[data-lot-id]", "article.lot", ".lot-result", "article", "li"],
        "title_sel": [".lot-title", ".LotItem__title", ".artwork-title", "h2", "h3"],
        "country": "GB",
    },
    {
        "name": "phillips",
        "url": "https://www.phillips.com/results?page=1",
        "fallback_url": "https://www.phillips.com/results",
        "item_sel": ["[data-lot-id]", "article.lot", ".lot-result", "article", "li"],
        "title_sel": [".lot-title", ".LotItem__title", ".work-title", "h2", "h3"],
        "country": "GB",
    },
]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

HEADERS_SETS = [
    # Primary: Chrome-like UA
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "DNT": "1",
    },
    # Fallback: Firefox-like UA
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) "
            "Gecko/20100101 Firefox/125.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    },
]

TIMEOUT = 20


def _fetch(url: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Try fetching URL with each header set. Returns (status, html, error)."""
    last_status, last_error = None, None
    for headers in HEADERS_SETS:
        try:
            s = requests.Session()
            s.headers.update(headers)
            # Warm up with a root request to pick up cookies/redirect chain
            root = "/".join(url.split("/")[:3])
            try:
                s.get(root, timeout=10)
            except Exception:
                pass
            time.sleep(0.5)
            resp = s.get(url, timeout=TIMEOUT, allow_redirects=True)
            last_status = resp.status_code
            if resp.status_code == 200:
                return resp.status_code, resp.text, None
            last_error = f"HTTP {resp.status_code}"
        except requests.exceptions.ConnectionError as e:
            last_error = f"ConnectionError: {e}"
        except requests.exceptions.Timeout:
            last_error = "Timeout"
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
    return last_status, None, last_error


def _extract(html: str, item_sels: list[str], title_sels: list[str]) -> tuple[int, list[str]]:
    """Return (item_count, sample_titles[0:3])."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for sel in item_sels:
        tags = soup.select(sel)
        if tags:
            items = tags
            break
    titles: list[str] = []
    for tag in items:
        for ts in title_sels:
            el = tag.select_one(ts)
            if el:
                t = el.get_text(" ", strip=True)
                if t and len(t) > 3:
                    titles.append(t)
                    break
    return len(items), titles[:3]


# ---------------------------------------------------------------------------
# Result struct
# ---------------------------------------------------------------------------

class Result:
    def __init__(self, name: str):
        self.name = name
        self.status_code: Optional[int] = None
        self.items_found: int = 0
        self.sample_titles: list[str] = []
        self.error: Optional[str] = None
        self.note: str = ""
        self.duration_ms: int = 0
        self.final_url: str = ""

    @property
    def colour(self) -> str:
        if self.error and not self.items_found:
            return "RED"
        if self.items_found >= 5:
            return "GREEN"
        if self.items_found > 0:
            return "YELLOW"
        return "RED"


# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------

def run_tests() -> list[Result]:
    results: list[Result] = []
    for t in TARGETS:
        name = t["name"]
        print(f"  Testing {name:20s} ...", end="", flush=True)
        r = Result(name)
        t0 = time.monotonic()

        url = t["url"]
        status, html, error = _fetch(url)
        r.status_code = status
        r.final_url = url

        if html:
            r.items_found, r.sample_titles = _extract(html, t["item_sel"], t["title_sel"])
            if r.items_found == 0:
                # Maybe the page structure differs — try fallback URL
                fb_status, fb_html, fb_error = _fetch(t.get("fallback_url", url))
                if fb_html:
                    r.items_found, r.sample_titles = _extract(fb_html, t["item_sel"], t["title_sel"])
                    r.status_code = fb_status
                    if r.items_found == 0:
                        r.note = "Got 200 but no recognisable lot elements found"
                    else:
                        r.note = "fallback URL worked"
                else:
                    r.note = "Got 200 but no lot elements; fallback also failed"
        else:
            r.error = error

        r.duration_ms = int((time.monotonic() - t0) * 1000)

        colour = r.colour
        flag = {"GREEN": "✓", "YELLOW": "~", "RED": "✗"}[colour]
        items_str = f"{r.items_found} lots" if r.items_found else (r.error or "0 lots")
        print(f" {flag} {colour:6s}  {str(r.status_code):5s}  {items_str[:45]}")
        results.append(r)

        time.sleep(2)  # polite inter-request delay

    return results


# ---------------------------------------------------------------------------
# Write report
# ---------------------------------------------------------------------------

REPORT_PATH = Path(__file__).parent / "live_test_results.txt"


def _bar(pct: float, width: int = 30) -> str:
    filled = int(round(pct * width / 100))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def write_report(results: list[Result]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    green  = [r for r in results if r.colour == "GREEN"]
    yellow = [r for r in results if r.colour == "YELLOW"]
    red    = [r for r in results if r.colour == "RED"]

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"  ArtArb — Live Integration Test Results")
    lines.append(f"  Generated: {now}")
    lines.append("=" * 72)
    lines.append("")

    # Summary table
    lines.append("SUMMARY TABLE")
    lines.append("-" * 72)
    lines.append(f"  {'Scraper':<20}  {'Status':>6}  {'Items':>5}  {'ms':>5}  Result")
    lines.append(f"  {'-'*20}  {'------':>6}  {'-----':>5}  {'-----':>5}  ------")
    for r in results:
        status_str = str(r.status_code) if r.status_code else "—"
        colour = r.colour
        flag = {"GREEN": "GREEN ✓", "YELLOW": "YELLOW~", "RED": "RED   ✗"}[colour]
        lines.append(
            f"  {r.name:<20}  {status_str:>6}  {r.items_found:>5}  {r.duration_ms:>5}  {flag}"
        )
    lines.append("")

    # Score bar
    total = len(results)
    pct_green  = 100 * len(green)  // total
    pct_yellow = 100 * len(yellow) // total
    pct_red    = 100 * len(red)    // total
    lines.append(f"  GREEN  {len(green):>2}/{total}  {_bar(pct_green)}")
    lines.append(f"  YELLOW {len(yellow):>2}/{total}  {_bar(pct_yellow)}")
    lines.append(f"  RED    {len(red):>2}/{total}  {_bar(pct_red)}")
    lines.append("")

    # Detailed per-scraper sections
    for r in results:
        lines.append("─" * 72)
        lines.append(f"  {r.name.upper():<20}  [{r.colour}]  HTTP {r.status_code or '—'}  {r.duration_ms}ms")
        lines.append(f"  URL: {r.final_url}")
        if r.error:
            lines.append(f"  ERROR: {r.error}")
        if r.note:
            lines.append(f"  NOTE: {r.note}")
        if r.sample_titles:
            lines.append(f"  Items found: {r.items_found}")
            lines.append("  Sample lot titles:")
            for i, title in enumerate(r.sample_titles, 1):
                wrapped = textwrap.fill(title, width=65, subsequent_indent="         ")
                lines.append(f"    {i}. {wrapped}")
        elif r.items_found:
            lines.append(f"  Items found: {r.items_found}  (no titles extractable)")
        else:
            lines.append("  Items found: 0")
        lines.append("")

    # Sites that block us
    if red:
        lines.append("=" * 72)
        lines.append("  BLOCKED / FAILED SITES")
        lines.append("─" * 72)
        for r in red:
            lines.append(f"  • {r.name:<18}  HTTP {r.status_code or '—':<5}  {r.error or 'No lots extracted'}")
        lines.append("")
        lines.append("  Recommendation: use Playwright with stealth plugin or")
        lines.append("  residential proxy rotation for these sites.")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  Report written to: {REPORT_PATH}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"\nArtArb Live Integration Test — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("=" * 72)
    print("  Running requests against 18 live sites (2 s delay between each)")
    print("  Playwright tests skipped (not installed in this environment)")
    print("=" * 72)
    results = run_tests()
    write_report(results)

    green  = [r for r in results if r.colour == "GREEN"]
    yellow = [r for r in results if r.colour == "YELLOW"]
    red    = [r for r in results if r.colour == "RED"]
    print(f"\n  Final score:  GREEN {len(green)}  YELLOW {len(yellow)}  RED {len(red)}  / {len(results)} total")
