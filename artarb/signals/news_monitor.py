"""
Artist news monitor.

Queries multiple free news sources for each tracked artist and upserts
results into the market_events table (event_type = 'news_mention').
After each fetch it checks whether the weekly mention count has spiked
more than SPIKE_THRESHOLD above the 4-week rolling average.

Sources (tried in order, first non-empty result wins per artist):
  1. Google News RSS  — news.google.com/rss/search?q=...
  2. DuckDuckGo HTML  — duckduckgo.com/html/?q=...+nieuws
  3. Bing News RSS    — feeds.news.bing.com/news/search?q=...

Entry points
------------
One-shot (all DB artists):
    python -m artarb.signals.news_monitor

Specific artist names (bypasses DB lookup):
    python -m artarb.signals.news_monitor --names "Karel Appel" "Corneille"

Demo mode (no DB, no network — seeded sample data):
    python -m artarb.signals.news_monitor --demo --names "Karel Appel" "Pierre Alechinsky" "Corneille"

Daily scheduler (blocks forever):
    from artarb.signals.news_monitor import schedule_daily
    schedule_daily()

Rate-limit notes
----------------
Each source is queried once per artist with BETWEEN_ARTISTS_SEC sleep between
artists to avoid triggering scraping defences.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from xml.etree import ElementTree as ET

import requests
import schedule
from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artarb.database import get_session
from artarb.models.base import Artist, MarketEvent

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Seconds to sleep between artists to avoid rate limiting
BETWEEN_ARTISTS_SEC: int = 5

# Max articles to store per artist per run (keeps market_events table clean)
MAX_ARTICLES_PER_ARTIST: int = 10

# HTTP request timeout (connect, read)
REQUEST_TIMEOUT: tuple = (10, 20)

# Spike detection: current-week count vs 4-week rolling avg
SPIKE_THRESHOLD: float = 0.50   # 50 % increase triggers WARNING
COMPARISON_WEEKS: int = 4

# User-agent string for HTTP requests
USER_AGENT = "artarb/1.0 (art-arbitrage-research; contact@example.com)"

# News sources, queried in order until one yields results
SOURCES = ["google_news", "duckduckgo", "bing_news"]

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

@dataclass
class Article:
    title: str
    url: str
    source: str
    published: Optional[date] = None

    def __str__(self) -> str:
        pub = self.published.isoformat() if self.published else "date unknown"
        return f"[{pub}] {self.source}: {self.title[:80]}"


@dataclass
class RunStats:
    artists_processed: int = 0
    articles_found: int = 0
    rows_written: int = 0
    spikes_detected: int = 0
    errors: int = 0
    demo_mode: bool = False

    def __str__(self) -> str:
        suffix = "  [DEMO]" if self.demo_mode else ""
        return (
            f"artists={self.artists_processed} "
            f"articles={self.articles_found} "
            f"rows={self.rows_written} "
            f"spikes={self.spikes_detected} "
            f"errors={self.errors}"
            f"{suffix}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    artist_names: Optional[list[str]] = None,
    demo: bool = False,
) -> RunStats:
    """Fetch news for *artist_names* (or all DB artists) and persist results.

    Args:
        artist_names: Plain-text names to search.  When None and demo=False,
                      loads all artists from the database.
        demo:         Run with pre-seeded sample data; no network, no DB.

    Returns:
        RunStats aggregate.
    """
    if demo:
        return _run_demo(artist_names or ["Karel Appel", "Pierre Alechinsky", "Corneille"])

    stats = RunStats()
    http = _build_http()

    with get_session() as db:
        if artist_names:
            # Caller supplied names; look them up by name
            artists = _lookup_by_names(db, artist_names)
        else:
            artists = list(db.execute(select(Artist)).scalars().all())

        log.info("Processing %d artist(s)", len(artists))

        for idx, artist in enumerate(artists):
            search_term = _display_name(artist)
            log.info("[%d/%d] %r", idx + 1, len(artists), search_term)

            try:
                articles = _fetch_articles(http, search_term)
                stats.articles_found += len(articles)
                log.info("  %d article(s) found", len(articles))
                for art in articles:
                    log.info("  %s", art)

                written = _persist_articles(db, artist.id, articles)
                stats.rows_written += written

                if _check_spike(db, artist.id, search_term):
                    stats.spikes_detected += 1

                stats.artists_processed += 1

            except Exception as exc:
                log.error("  Error for %r: %s", search_term, exc)
                stats.errors += 1

            if idx < len(artists) - 1:
                time.sleep(BETWEEN_ARTISTS_SEC)

    log.info("Run complete — %s", stats)
    return stats


def schedule_daily(run_at: str = "08:00") -> None:
    """Schedule a daily job at *run_at* (HH:MM local time) and block forever."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Scheduling news monitor daily at %s", run_at)
    schedule.every().day.at(run_at).do(run)

    log.info("Running initial fetch …")
    run()

    while True:
        schedule.run_pending()
        time.sleep(60)


# ---------------------------------------------------------------------------
# Article fetching — source adapters
# ---------------------------------------------------------------------------

def _fetch_articles(http: requests.Session, name: str) -> list[Article]:
    """Try each source in turn; return articles from the first that succeeds."""
    for source in SOURCES:
        try:
            articles: list[Article] = []
            if source == "google_news":
                articles = _google_news(http, name)
            elif source == "duckduckgo":
                articles = _duckduckgo(http, name)
            elif source == "bing_news":
                articles = _bing_news(http, name)

            if articles:
                log.debug("  source=%r yielded %d article(s)", source, len(articles))
                return articles[:MAX_ARTICLES_PER_ARTIST]

        except Exception as exc:
            log.debug("  source=%r failed: %s", source, exc)
            continue

    log.debug("  All sources exhausted for %r", name)
    return []


def _google_news(http: requests.Session, name: str) -> list[Article]:
    """Fetch Google News RSS for *name*."""
    url = "https://news.google.com/rss/search"
    params = {"q": name, "hl": "nl", "gl": "NL", "ceid": "NL:nl"}
    resp = http.get(url, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return _parse_rss(resp.text, source_label="Google News")


def _bing_news(http: requests.Session, name: str) -> list[Article]:
    """Fetch Bing News RSS for *name*."""
    url = "https://www.bing.com/news/search"
    params = {"q": name, "format": "rss", "mkt": "nl-NL"}
    resp = http.get(url, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return _parse_rss(resp.text, source_label="Bing News")


def _duckduckgo(http: requests.Session, name: str) -> list[Article]:
    """Scrape DuckDuckGo News HTML for *name*."""
    url = "https://duckduckgo.com/html/"
    params = {"q": f"{name} kunst nieuws OR auction OR exhibition"}
    resp = http.get(url, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    articles: list[Article] = []

    for result in soup.select(".result__title a.result__a")[:MAX_ARTICLES_PER_ARTIST]:
        title = result.get_text(strip=True)
        href = result.get("href", "")
        if title and href:
            articles.append(Article(title=title, url=href, source="DuckDuckGo"))

    return articles


def _parse_rss(xml_text: str, source_label: str) -> list[Article]:
    """Parse a standard RSS feed and return Article objects."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    ns = {"media": "http://search.yahoo.com/mrss/"}
    articles: list[Article] = []

    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")

        if title_el is None or link_el is None:
            continue

        title = (title_el.text or "").strip()
        url = (link_el.text or "").strip()
        if not title or not url:
            continue

        published: Optional[date] = None
        if pub_el is not None and pub_el.text:
            published = _parse_rss_date(pub_el.text.strip())

        articles.append(Article(title=title, url=url, source=source_label, published=published))

    return articles


def _parse_rss_date(s: str) -> Optional[date]:
    """Parse RFC 2822 date strings like 'Mon, 25 May 2026 07:00:00 GMT'."""
    formats = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------

def _persist_articles(
    db: Session,
    artist_id: uuid.UUID,
    articles: list[Article],
) -> int:
    """Upsert articles into market_events.  Returns rows written."""
    written = 0
    for art in articles:
        existing = db.execute(
            select(MarketEvent).where(
                MarketEvent.artist_id == artist_id,
                MarketEvent.url == art.url,
                MarketEvent.event_type == "news_mention",
            )
        ).scalar_one_or_none()

        if existing is None:
            db.add(MarketEvent(
                id=uuid.uuid4(),
                artist_id=artist_id,
                event_type="news_mention",
                event_date=art.published or date.today(),
                source=art.source,
                headline=art.title[:1000],
                url=art.url,
            ))
            written += 1

    return written


def _check_spike(
    db: Session,
    artist_id: uuid.UUID,
    name: str,
) -> bool:
    """Emit a WARNING when weekly mention count spikes > SPIKE_THRESHOLD."""
    current_week_start = _monday_of(date.today())
    prev_week_start = current_week_start - timedelta(weeks=1)

    # Count this week
    current_count = db.scalar(
        select(func.count()).select_from(MarketEvent).where(
            MarketEvent.artist_id == artist_id,
            MarketEvent.event_type == "news_mention",
            MarketEvent.event_date >= current_week_start,
        )
    ) or 0

    # Count COMPARISON_WEEKS prior weeks (total mentions / weeks for avg)
    prior_start = current_week_start - timedelta(weeks=COMPARISON_WEEKS)
    prior_count = db.scalar(
        select(func.count()).select_from(MarketEvent).where(
            MarketEvent.artist_id == artist_id,
            MarketEvent.event_type == "news_mention",
            MarketEvent.event_date >= prior_start,
            MarketEvent.event_date < current_week_start,
        )
    ) or 0

    weekly_avg = prior_count / COMPARISON_WEEKS

    if weekly_avg == 0:
        return False

    increase = (current_count - weekly_avg) / weekly_avg

    if increase > SPIKE_THRESHOLD:
        log.warning(
            "NEWS SPIKE ▲  %-40s  this_week=%d  4w_avg=%.1f  +%.1f%%",
            name, current_count, weekly_avg, increase * 100,
        )
        return True

    log.debug(
        "No spike: %s  this_week=%d  4w_avg=%.1f  Δ=%.1f%%",
        name, current_count, weekly_avg, increase * 100,
    )
    return False


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------

# Realistic pre-seeded articles for the three CoBrA artists.
# Dates are recent (relative to today so spike detection fires).
_DEMO_DATA: dict[str, list[dict]] = {
    "Karel Appel": [
        {
            "title": "Karel Appel-werk brengt recordprijs op Sotheby's Amsterdam",
            "url": "https://www.sothebys.com/nl/articles/appel-record-2026",
            "source": "Sotheby's NL",
            "days_ago": 2,
        },
        {
            "title": "CoBrA revisited: hoe Karel Appel de naoorlogse kunst opschudde",
            "url": "https://www.volkskrant.nl/cultuur/cobra-revisited",
            "source": "de Volkskrant",
            "days_ago": 4,
        },
        {
            "title": "Appel, Corneille en Alechinsky: CoBrA tentoonstelling in Stedelijk",
            "url": "https://www.stedelijk.nl/nl/nieuws/cobra-2026",
            "source": "Stedelijk Museum",
            "days_ago": 6,
        },
        {
            "title": "Dutch postwar art prices surge as collectors rediscover CoBrA",
            "url": "https://www.theartnewspaper.com/2026/05/cobra-prices",
            "source": "The Art Newspaper",
            "days_ago": 8,
        },
        {
            "title": "Karel Appel Foundation opent nieuw archief voor onderzoekers",
            "url": "https://www.karelappel.nl/nieuws/archief-2026",
            "source": "Karel Appel Foundation",
            "days_ago": 10,
        },
    ],
    "Pierre Alechinsky": [
        {
            "title": "Pierre Alechinsky fête ses 99 ans avec une grande rétrospective à Bruxelles",
            "url": "https://www.lesoir.be/alechinsky-retrospective-2026",
            "source": "Le Soir",
            "days_ago": 3,
        },
        {
            "title": "Alechinsky-werk voor €340.000 geveild bij Christie's",
            "url": "https://www.christies.com/results/alechinsky-2026",
            "source": "Christie's",
            "days_ago": 5,
        },
        {
            "title": "Alechinsky, the last CoBrA master still working at 98",
            "url": "https://www.theguardian.com/artanddesign/2026/alechinsky",
            "source": "The Guardian",
            "days_ago": 9,
        },
        {
            "title": "Nieuw grafisch werk Alechinsky onthuld in Musée d'Art Moderne",
            "url": "https://www.mam.paris/fr/news/alechinsky-2026",
            "source": "MAM Paris",
            "days_ago": 12,
        },
    ],
    "Corneille": [
        {
            "title": "Corneille-retrospectief trekt 40.000 bezoekers in Museum Cobra",
            "url": "https://www.museumcobra.nl/nieuws/corneille-retrospectief",
            "source": "Museum CoBrA",
            "days_ago": 1,
        },
        {
            "title": "'De vogels van Corneille': zeldzame vroege werken geveild voor €280.000",
            "url": "https://www.kunstveiling.nl/nieuws/corneille-vroege-werken",
            "source": "Kunstveiling.nl",
            "days_ago": 4,
        },
        {
            "title": "Guillaume Cornelis van Beverloo: de man achter Corneille",
            "url": "https://www.nrc.nl/nieuws/corneille-biografie-2026",
            "source": "NRC",
            "days_ago": 7,
        },
        {
            "title": "Corneille's Caribbean palette: new scholarship on his Caribbean years",
            "url": "https://artforum.com/news/corneille-caribbean-2026",
            "source": "Artforum",
            "days_ago": 11,
        },
        {
            "title": "CoBrA marktanalyse: Corneille bovenaan in veilingresultaten 2026",
            "url": "https://www.artprice.com/nl/reports/cobra-2026",
            "source": "Artprice",
            "days_ago": 14,
        },
    ],
}

# Prior-week baseline counts to make spike detection realistic for demo
_DEMO_PRIOR_COUNTS: dict[str, list[int]] = {
    "Karel Appel":       [2, 1, 1, 2],   # avg 1.5 → 5 this week → spike ▲
    "Pierre Alechinsky": [1, 2, 1, 0],   # avg 1.0 → 4 this week → spike ▲
    "Corneille":         [3, 2, 4, 3],   # avg 3.0 → 5 this week → spike ▲
}


def _run_demo(names: list[str]) -> RunStats:
    """Run with pre-seeded sample data — no network, no DB."""
    _print_demo_banner()
    stats = RunStats(demo_mode=True)
    today = date.today()

    for idx, name in enumerate(names):
        _print_separator()
        print(f"\n[{idx+1}/{len(names)}]  {name}")

        raw_articles = _DEMO_DATA.get(name, _DEMO_DATA.get(_closest_demo_key(name), []))
        articles = [
            Article(
                title=a["title"],
                url=a["url"],
                source=a["source"],
                published=today - timedelta(days=a["days_ago"]),
            )
            for a in raw_articles
        ]

        if not articles:
            print(f"  No demo data for {name!r}.")
            stats.errors += 1
            continue

        print(f"\n  Articles found ({len(articles)}):")
        for art in articles:
            print(f"    {art}")

        # Simulate DB write
        new_count = len(articles)
        stats.articles_found += new_count
        stats.rows_written += new_count
        print(f"\n  → {new_count} rows written to market_events (event_type='news_mention')")

        # Spike detection using demo baseline
        prior_counts = _DEMO_PRIOR_COUNTS.get(name, [1, 1, 1, 1])
        weekly_avg = sum(prior_counts) / len(prior_counts)
        this_week = new_count
        if weekly_avg > 0:
            increase = (this_week - weekly_avg) / weekly_avg
            if increase > SPIKE_THRESHOLD:
                print(
                    f"\n  ⚠  NEWS SPIKE ▲  this_week={this_week}  "
                    f"4w_avg={weekly_avg:.1f}  +{increase * 100:.1f}%"
                )
                stats.spikes_detected += 1
            else:
                print(
                    f"\n  ✓  No spike: this_week={this_week}  "
                    f"4w_avg={weekly_avg:.1f}  Δ={increase * 100:+.1f}%"
                )

        stats.artists_processed += 1

        if idx < len(names) - 1:
            print()

    _print_separator()
    print(f"\nRun complete — {stats}\n")
    return stats


def _print_demo_banner() -> None:
    width = 72
    print("=" * width)
    print("  NEWS MONITOR  —  DEMO MODE  (pre-seeded sample data)")
    print("  In production, live articles are fetched from Google News /")
    print("  DuckDuckGo / Bing News and persisted to market_events table.")
    print("=" * width)


def _print_separator() -> None:
    print("-" * 72)


def _closest_demo_key(name: str) -> str:
    """Best-effort fuzzy match against demo data keys (no extra deps)."""
    name_lower = name.lower()
    for key in _DEMO_DATA:
        if key.lower() in name_lower or name_lower in key.lower():
            return key
    # Try last word (surname)
    parts = name.split()
    if parts:
        surname = parts[-1].lower()
        for key in _DEMO_DATA:
            if surname in key.lower():
                return key
    return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_http() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "nl,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return s


def _display_name(artist: Artist) -> str:
    """Flip 'Lastname, Firstname' to 'Firstname Lastname' for search queries."""
    name = artist.name_canonical
    parts = name.split(",", 1)
    if len(parts) == 2:
        return f"{parts[1].strip()} {parts[0].strip()}"
    return name


def _lookup_by_names(db: Session, names: list[str]) -> list[Artist]:
    """Return Artist rows for exact canonical or display-name matches.

    Falls back to creating a temporary in-memory stub when the artist is
    not found in the DB (useful during development / demo runs with a DB).
    """
    from types import SimpleNamespace

    results: list[Artist] = []
    for name in names:
        # Try canonical "Lastname, Firstname" form
        flipped = _to_canonical(name)
        row = db.execute(
            select(Artist).where(
                (Artist.name_canonical == name) | (Artist.name_canonical == flipped)
            )
        ).scalar_one_or_none()
        if row:
            results.append(row)
        else:
            log.warning("Artist %r not found in DB — creating ephemeral stub", name)
            stub = SimpleNamespace(
                id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
                name_canonical=name,
            )
            results.append(stub)  # type: ignore[arg-type]
    return results


def _to_canonical(name: str) -> str:
    """'Firstname Lastname' → 'Lastname, Firstname' (best-effort, 2-word names)."""
    parts = name.strip().split()
    if len(parts) == 2:
        return f"{parts[1]}, {parts[0]}"
    return name


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Fetch news mentions for tracked artists."
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Block and run daily at 08:00.",
    )
    parser.add_argument(
        "--names",
        nargs="+",
        metavar="NAME",
        help="Artist names to search (default: all artists in DB).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with pre-seeded sample data (no network, no DB required).",
    )
    args = parser.parse_args()

    if args.schedule:
        schedule_daily()
    else:
        stats = run(artist_names=args.names, demo=args.demo)
        if not args.demo:
            print(f"\nDone — {stats}")
