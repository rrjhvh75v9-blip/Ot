#!/usr/bin/env python3
"""
Art Arbitrage Terminal Dashboard.

Two sections:

  SPECIALTY OPPORTUNITIES   — open opportunities flagged by the specialty scorer,
                              grouped by niche with colour-coded urgency.

  KEYWORD ALERTS            — lots whose titles matched a watchlist keyword but
  NEEDS REVIEW                whose artist is not yet in the database.  Grouped
                              by trigger keyword for efficient batch review.

Usage
-----
    python dashboard.py                    # live DB data
    python dashboard.py --demo             # synthetic demo data, no DB needed
    python dashboard.py --refresh 30       # auto-refresh every N seconds
    python dashboard.py --min-conf 0.70    # filter by minimum confidence
"""

import argparse
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Category metadata
# ---------------------------------------------------------------------------

CATEGORY_LABELS: dict[str, str] = {
    "czech_avantgarde": "Czech Avant-garde",
    "studio_pottery": "Studio Pottery",
    "german_expressionist_prints": "German Expressionist",
    "mucha_drawings": "Mucha",
    "russian_futurist": "Russian Futurist",
}

CATEGORY_STYLES: dict[str, str] = {
    "czech_avantgarde": "blue",
    "studio_pottery": "green",
    "german_expressionist_prints": "red",
    "mucha_drawings": "magenta",
    "russian_futurist": "yellow",
}

CATEGORY_ORDER = list(CATEGORY_LABELS.keys())

console = Console()


# ---------------------------------------------------------------------------
# Data transfer object
# ---------------------------------------------------------------------------

@dataclass
class OpportunityRow:
    """Flattened view of everything needed to render one specialty opportunity."""
    opportunity_id: uuid.UUID
    artist_name: str
    specialty_category: Optional[str]
    arbitrage_notes: Optional[str]
    platform: str
    current_bid: Optional[float]
    listing_url: Optional[str]
    ends_at: Optional[datetime]
    expected_profit: float
    confidence_score: float
    sell_price_estimate: Optional[float]
    median_hammer: Optional[float]
    arbitrage_category: Optional[str]


@dataclass
class WatchlistRow:
    """Flattened view of a specialty_watchlist entry for the KEYWORD ALERTS section."""
    watchlist_id: uuid.UUID
    lot_title: str
    artist_name: str
    trigger_keyword: str
    platform: Optional[str]
    current_bid: Optional[float]
    listing_url: Optional[str]
    ends_at: Optional[datetime]
    flagged_at: datetime
    reviewed: bool
    notes: Optional[str]


# Human-readable labels for each canonical trigger keyword
KEYWORD_LABELS: dict[str, str] = {
    "tsuba":                "Tsuba (Japanese sword guard)",
    "netsuke":              "Netsuke",
    "okimono":              "Okimono",
    "fuchi":                "Fuchi (sword fitting)",
    "menuki":               "Menuki (sword grip ornament)",
    "derriere_le_miroir":   "Derrière le Miroir",
    "dlm":                  "DLM (Derrière le Miroir abbrev.)",
    "maeght":               "Maeght (Paris gallery)",
    "griffelkunst":         "Griffelkunst (German print edition)",
    "mourlot":              "Mourlot (Paris print workshop)",
    "atelier_stamp":        "Atelier Stamp",
    "atelierstempel":       "Atelierstempel",
    "estate_stamp":         "Estate Stamp",
    "studio_pottery":       "Studio Pottery",
    "stoneware":            "Stoneware (British ceramics)",
    "studio_keramiek":      "Studio Keramiek",
}


# ---------------------------------------------------------------------------
# Urgency helpers
# ---------------------------------------------------------------------------

def _urgency(ends_at: Optional[datetime]) -> tuple[str, str]:
    """Return (label, rich_style) based on how much time remains."""
    if ends_at is None:
        return "● open", "green"
    now = datetime.now(timezone.utc)
    delta = ends_at - now
    secs = delta.total_seconds()
    if secs <= 0:
        return "● ended", "dim red"
    hours = secs / 3600
    if hours <= 24:
        h, m = int(hours), int((secs % 3600) / 60)
        return f"● {h}h {m:02d}m", "bold red"
    if hours <= 72:
        return f"● {int(hours)}h", "bold yellow"
    return f"● {int(hours / 24)}d", "green"


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _tip_snippet(arbitrage_notes: Optional[str], max_len: int = 58) -> str:
    """First sentence of arbitrage_notes, truncated."""
    if not arbitrage_notes:
        return "—"
    tip = arbitrage_notes.split(".")[0].strip()
    return tip if len(tip) <= max_len else tip[:max_len - 1] + "…"


def _category_table(category: str, rows: list[OpportunityRow]) -> Table:
    label = CATEGORY_LABELS.get(category, category.replace("_", " ").title())
    border = CATEGORY_STYLES.get(category, "white")
    count = len(rows)
    noun = "opportunity" if count == 1 else "opportunities"

    t = Table(
        title=f"[bold]{label}[/bold]  ·  {count} {noun}",
        title_style=f"bold white on {border}",
        border_style=border,
        show_lines=True,
        expand=True,
        padding=(0, 1),
    )
    t.add_column("Urgency",      width=11,  no_wrap=True)
    t.add_column("Artist",       min_width=22)
    t.add_column("Platform\nBid", min_width=16)
    t.add_column("Resale\nEst.", min_width=10, justify="right")
    t.add_column("Profit",       min_width=9,  justify="right")
    t.add_column("Conf.",        min_width=6,  justify="right")
    t.add_column("Authentication tip",         min_width=30)
    t.add_column("URL",          min_width=12)

    for row in sorted(rows, key=lambda r: r.confidence_score, reverse=True):
        urgency_label, urgency_style = _urgency(row.ends_at)

        # Artist cell — name + specialty category below
        cat_label = CATEGORY_LABELS.get(row.specialty_category or "", "")
        artist_txt = Text()
        artist_txt.append(row.artist_name, style=f"bold {border}")
        if cat_label:
            artist_txt.append(f"\n{cat_label}", style="dim")

        # Platform / bid cell
        bid_str = f"€{row.current_bid:,.0f}" if row.current_bid else "—"
        platform_txt = Text()
        platform_txt.append(row.platform, style="magenta")
        platform_txt.append(f"\n{bid_str}", style="bold white")

        # Resale estimate: prefer opportunity sell_price_estimate
        resale = row.sell_price_estimate or row.median_hammer
        resale_str = f"€{resale:,.0f}" if resale else "—"
        resale_style = "bold green" if resale else "dim"

        # Profit
        profit_str = f"€{row.expected_profit:,.0f}"
        profit_style = "bold green" if row.expected_profit > 0 else "red"

        # Confidence
        conf_pct = f"{row.confidence_score:.0%}"
        if row.confidence_score >= 0.75:
            conf_style = "bold green"
        elif row.confidence_score >= 0.50:
            conf_style = "yellow"
        else:
            conf_style = "dim red"

        # URL — terminal hyperlink when supported
        url_txt = Text()
        if row.listing_url:
            url_txt.append("↗ view lot", style=f"link {row.listing_url} blue underline")
        else:
            url_txt.append("—", style="dim")

        t.add_row(
            Text(urgency_label, style=urgency_style),
            artist_txt,
            platform_txt,
            Text(resale_str, style=resale_style),
            Text(profit_str, style=profit_style),
            Text(conf_pct, style=conf_style),
            _tip_snippet(row.arbitrage_notes),
            url_txt,
        )

    return t


def _stat_box(label: str, value: str, style: str = "white") -> Panel:
    return Panel(
        Text(value, style=f"bold {style}", justify="center"),
        title=f"[dim]{label}[/dim]",
        border_style=style,
        expand=True,
        padding=(0, 1),
    )


def _summary_row(rows: list[OpportunityRow], n_alerts: int = 0) -> Columns:
    now = datetime.now(timezone.utc)

    n_urgent = sum(
        1 for r in rows
        if r.ends_at and 0 < (r.ends_at - now).total_seconds() <= 86_400
    )
    avg_conf = sum(r.confidence_score for r in rows) / len(rows) if rows else 0.0
    total_profit = sum(r.expected_profit for r in rows)

    return Columns([
        _stat_box("Specialty opps",  str(len(rows)),           "white"),
        _stat_box("Ending < 24 h",   str(n_urgent),            "red"   if n_urgent  else "dim"),
        _stat_box("Avg confidence",  f"{avg_conf:.0%}",        "green" if avg_conf >= 0.65 else "yellow"),
        _stat_box("Expected profit", f"€{total_profit:,.0f}",  "green"),
        _stat_box("Keyword alerts",  str(n_alerts),            "cyan"  if n_alerts  else "dim"),
    ], equal=True, expand=True)


# ---------------------------------------------------------------------------
# Watchlist rendering
# ---------------------------------------------------------------------------

def _watchlist_keyword_table(keyword: str, rows: list[WatchlistRow]) -> Table:
    label = KEYWORD_LABELS.get(keyword, keyword.replace("_", " ").title())
    count = len(rows)
    noun = "lot" if count == 1 else "lots"

    t = Table(
        title=f"[bold]{label}[/bold]  ·  {count} unreviewed {noun}",
        title_style="bold black on cyan",
        border_style="cyan",
        show_lines=True,
        expand=True,
        padding=(0, 1),
    )
    t.add_column("Urgency",       width=11,  no_wrap=True)
    t.add_column("Lot Title",     min_width=30)
    t.add_column("Artist",        min_width=20)
    t.add_column("Platform\nBid", min_width=16)
    t.add_column("Flagged",       min_width=12, no_wrap=True)
    t.add_column("URL",           min_width=12)

    for row in sorted(rows, key=lambda r: r.flagged_at, reverse=True):
        urgency_label, urgency_style = _urgency(row.ends_at)

        # Truncate long titles
        title_str = row.lot_title if len(row.lot_title) <= 55 else row.lot_title[:54] + "…"
        title_txt = Text(title_str, style="white")

        artist_txt = Text(row.artist_name, style="bold dim")

        bid_str = f"€{row.current_bid:,.0f}" if row.current_bid else "—"
        platform_txt = Text()
        platform_txt.append(row.platform or "?", style="magenta")
        platform_txt.append(f"\n{bid_str}", style="white")

        flagged_str = row.flagged_at.strftime("%m-%d %H:%M")

        url_txt = Text()
        if row.listing_url:
            url_txt.append("↗ view lot", style=f"link {row.listing_url} blue underline")
        else:
            url_txt.append("—", style="dim")

        t.add_row(
            Text(urgency_label, style=urgency_style),
            title_txt,
            artist_txt,
            platform_txt,
            flagged_str,
            url_txt,
        )

    return t


def _watchlist_section(watchlist_rows: list[WatchlistRow]) -> list:
    """Return a list of renderables for the KEYWORD ALERTS section."""
    if not watchlist_rows:
        return [
            Rule("[bold cyan]KEYWORD ALERTS — NEEDS REVIEW[/bold cyan]"),
            Panel("[dim]No unreviewed keyword alerts.[/dim]", border_style="dim cyan"),
        ]

    # Group by trigger_keyword
    by_kw: dict[str, list[WatchlistRow]] = {}
    for row in watchlist_rows:
        by_kw.setdefault(row.trigger_keyword, []).append(row)

    # Keyword order: by count descending, then alphabetical
    ordered_kws = sorted(by_kw.keys(), key=lambda k: (-len(by_kw[k]), k))

    # Build a compact keyword summary line
    summary_parts = [
        f"{KEYWORD_LABELS.get(k, k)} ({len(by_kw[k])})"
        for k in ordered_kws
    ]
    summary_txt = "  ·  ".join(summary_parts)

    renderables: list = [
        Text(""),
        Rule("[bold cyan]KEYWORD ALERTS — NEEDS REVIEW[/bold cyan]"),
        Panel(
            f"[cyan]{len(watchlist_rows)} unreviewed lot(s) across "
            f"{len(by_kw)} keyword(s)[/cyan]\n[dim]{summary_txt}[/dim]",
            border_style="cyan",
            padding=(0, 2),
        ),
    ]

    for kw in ordered_kws:
        renderables.append(Text(""))
        renderables.append(_watchlist_keyword_table(kw, by_kw[kw]))

    return renderables


def build_layout(
    rows: list[OpportunityRow],
    watchlist_rows: Optional[list[WatchlistRow]] = None,
    refresh_interval: Optional[int] = None,
) -> Group:
    """Assemble the full dashboard as a single renderable Group."""
    watchlist_rows = watchlist_rows or []
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M UTC")
    subtitle = f"[dim]{now_str}"
    if refresh_interval:
        subtitle += f"  ·  auto-refresh every {refresh_interval}s"
    subtitle += "[/dim]"

    header = Panel(
        f"[bold white]ArtArb — Specialty Opportunities Dashboard[/bold white]\n{subtitle}",
        style="bold blue",
        padding=(0, 2),
    )

    renderables: list = [header, _summary_row(rows, n_alerts=len(watchlist_rows))]

    # ── Section 1: Specialty Opportunities ───────────────────────────────────
    renderables.append(Rule("[bold blue]SPECIALTY OPPORTUNITIES[/bold blue]"))

    if not rows:
        renderables.append(Panel(
            "[yellow]No specialty opportunities found.[/yellow]\n"
            "Run the scraper + opportunity detector first, or pass --demo.",
            border_style="yellow",
        ))
    else:
        by_cat: dict[str, list[OpportunityRow]] = {}
        for row in rows:
            k = row.specialty_category or "unknown"
            by_cat.setdefault(k, []).append(row)

        ordered = [k for k in CATEGORY_ORDER if k in by_cat]
        ordered += [k for k in by_cat if k not in ordered]

        for cat in ordered:
            renderables.append(Text(""))
            renderables.append(_category_table(cat, by_cat[cat]))

    # ── Section 2: Keyword Alerts ─────────────────────────────────────────────
    renderables.extend(_watchlist_section(watchlist_rows))

    return Group(*renderables)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_from_db() -> list[OpportunityRow]:
    from sqlalchemy import select

    from artarb.database import get_session
    from artarb.models.base import (
        Artist, Listing, Lot, Opportunity, RegionalPriceIndex,
    )

    with get_session() as db:
        result_rows = db.execute(
            select(Opportunity, Listing, Lot, Artist)
            .join(Listing, Opportunity.listing_id == Listing.id)
            .join(Lot, Listing.lot_id == Lot.id)
            .join(Artist, Opportunity.artist_id == Artist.id)
            .where(
                Opportunity.arbitrage_category.isnot(None),
                Opportunity.status == "open",
            )
            .order_by(Artist.specialty_category, Opportunity.confidence_score.desc())
        ).all()

        # Bulk-load best price index per artist
        artist_ids = list({r.Artist.id for r in result_rows})
        price_indices: dict[uuid.UUID, RegionalPriceIndex] = {}
        if artist_ids:
            for idx in db.execute(
                select(RegionalPriceIndex)
                .where(RegionalPriceIndex.artist_id.in_(artist_ids))
                .order_by(
                    RegionalPriceIndex.artist_id,
                    RegionalPriceIndex.sample_size.desc().nullslast(),
                )
            ).scalars():
                price_indices.setdefault(idx.artist_id, idx)

        output: list[OpportunityRow] = []
        for r in result_rows:
            idx = price_indices.get(r.Artist.id)
            output.append(OpportunityRow(
                opportunity_id=r.Opportunity.id,
                artist_name=r.Artist.name_canonical,
                specialty_category=r.Artist.specialty_category,
                arbitrage_notes=r.Artist.arbitrage_notes,
                platform=r.Listing.platform,
                current_bid=float(r.Listing.current_bid) if r.Listing.current_bid else None,
                listing_url=r.Listing.listing_url,
                ends_at=r.Listing.ends_at,
                expected_profit=float(r.Opportunity.expected_profit or 0),
                confidence_score=float(r.Opportunity.confidence_score or 0),
                sell_price_estimate=(
                    float(r.Opportunity.sell_price_estimate)
                    if r.Opportunity.sell_price_estimate else None
                ),
                median_hammer=(
                    float(idx.median_hammer)
                    if idx and idx.median_hammer else None
                ),
                arbitrage_category=r.Opportunity.arbitrage_category,
            ))
        return output


def _load_watchlist_from_db() -> list[WatchlistRow]:
    from sqlalchemy import select

    from artarb.database import get_session
    from artarb.models.base import Artist, Listing, Lot, SpecialtyWatchlist

    with get_session() as db:
        # Join watchlist → lot → artist; LEFT JOIN to latest active listing
        rows = db.execute(
            select(SpecialtyWatchlist, Lot, Artist)
            .join(Lot, SpecialtyWatchlist.lot_id == Lot.id)
            .join(Artist, Lot.artist_id == Artist.id)
            .where(SpecialtyWatchlist.reviewed.is_(False))
            .order_by(SpecialtyWatchlist.flagged_at.desc())
            .limit(300)
        ).all()

        # Collect lot IDs to bulk-load the most recent active listing per lot
        lot_ids = list({r.Lot.id for r in rows})
        best_listing: dict[uuid.UUID, Listing] = {}
        if lot_ids:
            for lst in db.execute(
                select(Listing)
                .where(
                    Listing.lot_id.in_(lot_ids),
                    Listing.status.in_(["active", "live", "open"]),
                )
                .order_by(Listing.discovered_at.desc())
            ).scalars():
                best_listing.setdefault(lst.lot_id, lst)

        output: list[WatchlistRow] = []
        for r in rows:
            lst = best_listing.get(r.Lot.id)
            output.append(WatchlistRow(
                watchlist_id=r.SpecialtyWatchlist.id,
                lot_title=r.Lot.title or "(no title)",
                artist_name=r.Artist.name_canonical,
                trigger_keyword=r.SpecialtyWatchlist.trigger_keyword,
                platform=lst.platform if lst else None,
                current_bid=float(lst.current_bid) if lst and lst.current_bid else None,
                listing_url=(lst.listing_url or r.Lot.source_url) if lst else r.Lot.source_url,
                ends_at=lst.ends_at if lst else None,
                flagged_at=r.SpecialtyWatchlist.flagged_at,
                reviewed=r.SpecialtyWatchlist.reviewed,
                notes=r.SpecialtyWatchlist.notes,
            ))
        return output


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------

def _demo_rows() -> list[OpportunityRow]:
    now = datetime.now(timezone.utc)
    return [
        # ── Czech Avant-garde ────────────────────────────────────────────────
        OpportunityRow(
            opportunity_id=uuid.uuid4(),
            artist_name="Toyen",
            specialty_category="czech_avantgarde",
            arbitrage_notes=(
                "Verify signature 'Toyen' lower right in black ink. "
                "Certificate from Jindrich Toman archive adds 30–50% premium. "
                "Belgian private collections consistently undervalue vs Paris market."
            ),
            platform="catawiki",
            current_bid=1_200.0,
            listing_url="https://www.catawiki.com/en/l/12345678",
            ends_at=now + timedelta(hours=18),
            expected_profit=4_800.0,
            confidence_score=0.85,
            sell_price_estimate=7_200.0,
            median_hammer=6_800.0,
            arbitrage_category="specialty:czech_avantgarde | platform_premium | low_bid_tier1",
        ),
        OpportunityRow(
            opportunity_id=uuid.uuid4(),
            artist_name="František Kupka",
            specialty_category="czech_avantgarde",
            arbitrage_notes=(
                "Look for estate stamp on verso. "
                "Paris Orphism works fetch 3× Dutch estimates. "
                "Only signed works qualify; 'attributed' lots are common fakes."
            ),
            platform="kunstveiling",
            current_bid=3_500.0,
            listing_url="https://www.kunstveiling.nl/lot/45678",
            ends_at=now + timedelta(hours=52),
            expected_profit=9_200.0,
            confidence_score=0.78,
            sell_price_estimate=14_000.0,
            median_hammer=13_500.0,
            arbitrage_category="specialty:czech_avantgarde | signed_pencil | platform_premium",
        ),
        OpportunityRow(
            opportunity_id=uuid.uuid4(),
            artist_name="Bohumil Kubišta",
            specialty_category="czech_avantgarde",
            arbitrage_notes=(
                "Kubišta's Expressionist-Cubist oil sketches appear anonymously. "
                "Compare geometric faceting with authenticated works in Prague NG. "
                "Only ~40 confirmed oils exist; any attribution warrants investigation."
            ),
            platform="catawiki",
            current_bid=650.0,
            listing_url=None,
            ends_at=now + timedelta(days=5),
            expected_profit=2_100.0,
            confidence_score=0.62,
            sell_price_estimate=3_200.0,
            median_hammer=3_100.0,
            arbitrage_category="specialty:czech_avantgarde | misattribution_keyword | platform_premium",
        ),
        # ── Studio Pottery ───────────────────────────────────────────────────
        OpportunityRow(
            opportunity_id=uuid.uuid4(),
            artist_name="Bernard Leach",
            specialty_category="studio_pottery",
            arbitrage_notes=(
                "Look for impressed 'BL' monogram and St Ives pottery seal on base. "
                "Works without seal sell for 60% less — authentication critical. "
                "Japan export pieces carry highest premium."
            ),
            platform="catawiki",
            current_bid=280.0,
            listing_url="https://www.catawiki.com/en/l/55443322",
            ends_at=now + timedelta(hours=8),
            expected_profit=1_420.0,
            confidence_score=0.92,
            sell_price_estimate=1_900.0,
            median_hammer=1_750.0,
            arbitrage_category="specialty:studio_pottery | pottery_seal_mark | platform_premium | low_bid_tier1",
        ),
        OpportunityRow(
            opportunity_id=uuid.uuid4(),
            artist_name="Lucie Rie",
            specialty_category="studio_pottery",
            arbitrage_notes=(
                "Impressed 'LR' seal is mandatory for attribution. "
                "Bronze-inlaid rims and sgraffito decoration are signature features. "
                "London specialist auctions outperform Dutch venues by 2–4×."
            ),
            platform="kunstveiling",
            current_bid=420.0,
            listing_url="https://www.kunstveiling.nl/lot/77889",
            ends_at=now + timedelta(hours=61),
            expected_profit=3_100.0,
            confidence_score=0.80,
            sell_price_estimate=4_000.0,
            median_hammer=3_800.0,
            arbitrage_category="specialty:studio_pottery | pottery_seal_mark | platform_premium | low_bid_tier1",
        ),
        # ── German Expressionist ─────────────────────────────────────────────
        OpportunityRow(
            opportunity_id=uuid.uuid4(),
            artist_name="Käthe Kollwitz",
            specialty_category="german_expressionist_prints",
            arbitrage_notes=(
                "Cross-reference Knesebeck catalogue raisonné number. "
                "Signed impressions in pencil command 2× unsigned price. "
                "Posthumous prints must be disclosed — check paper watermark date."
            ),
            platform="catawiki",
            current_bid=800.0,
            listing_url="https://www.catawiki.com/en/l/33221144",
            ends_at=now + timedelta(days=4),
            expected_profit=2_600.0,
            confidence_score=0.75,
            sell_price_estimate=4_000.0,
            median_hammer=3_800.0,
            arbitrage_category="specialty:german_expressionist_prints | signed_pencil | platform_premium",
        ),
        # ── Russian Futurist ─────────────────────────────────────────────────
        OpportunityRow(
            opportunity_id=uuid.uuid4(),
            artist_name="El Lissitzky",
            specialty_category="russian_futurist",
            arbitrage_notes=(
                "Check colophon for Maeght or Mourlot imprint. "
                "Proun series lithographs are most traded; verify edition number. "
                "Russian-language first editions carry 40% collector premium."
            ),
            platform="catawiki",
            current_bid=2_100.0,
            listing_url="https://www.catawiki.com/en/l/66554433",
            ends_at=now + timedelta(hours=14),
            expected_profit=11_500.0,
            confidence_score=0.90,
            sell_price_estimate=15_200.0,
            median_hammer=14_800.0,
            arbitrage_category="specialty:russian_futurist | russian_futurist_pub | platform_premium",
        ),
        OpportunityRow(
            opportunity_id=uuid.uuid4(),
            artist_name="Alexander Rodchenko",
            specialty_category="russian_futurist",
            arbitrage_notes=(
                "Constructivist photomontages: verify provenance to 1920s Soviet publications. "
                "Lissitzky/Rodchenko collaborations carry 50% premium. "
                "Circle-and-line composition is signature Rodchenko geometry."
            ),
            platform="kunstveiling",
            current_bid=4_500.0,
            listing_url="https://www.kunstveiling.nl/lot/11223344",
            ends_at=now + timedelta(days=8),
            expected_profit=7_800.0,
            confidence_score=0.72,
            sell_price_estimate=14_000.0,
            median_hammer=13_200.0,
            arbitrage_category="specialty:russian_futurist | russian_futurist_pub | misattribution_keyword",
        ),
        # ── Mucha ────────────────────────────────────────────────────────────
        OpportunityRow(
            opportunity_id=uuid.uuid4(),
            artist_name="Alphonse Mucha",
            specialty_category="mucha_drawings",
            arbitrage_notes=(
                "Pencil sketches for poster commissions are undervalued vs finished works. "
                "Verify period paper (pre-1939) with UV lamp — reproductions fluoresce. "
                "Archive stamp from Mucha Foundation Prague confirms authenticity."
            ),
            platform="catawiki",
            current_bid=1_800.0,
            listing_url="https://www.catawiki.com/en/l/88776655",
            ends_at=now + timedelta(hours=70),
            expected_profit=6_200.0,
            confidence_score=0.83,
            sell_price_estimate=9_500.0,
            median_hammer=9_200.0,
            arbitrage_category="specialty:mucha_drawings | signed_pencil | platform_premium",
        ),
    ]


def _demo_watchlist_rows() -> list[WatchlistRow]:
    now = datetime.now(timezone.utc)
    return [
        # ── Netsuke ──────────────────────────────────────────────────────────
        WatchlistRow(
            watchlist_id=uuid.uuid4(),
            lot_title="Netsuke ivory rabbit figure, Meiji period, signed",
            artist_name="Unknown",
            trigger_keyword="netsuke",
            platform="catawiki",
            current_bid=350.0,
            listing_url="https://www.catawiki.com/en/l/22334455",
            ends_at=now + timedelta(hours=6),
            flagged_at=now - timedelta(hours=2),
            reviewed=False,
            notes=None,
        ),
        WatchlistRow(
            watchlist_id=uuid.uuid4(),
            lot_title="Fine netsuke: seated monkey, boxwood, Edo period",
            artist_name="Unknown",
            trigger_keyword="netsuke",
            platform="barnebys",
            current_bid=None,
            listing_url="https://www.barnebys.com/buy/lot/11223344",
            ends_at=now + timedelta(days=3),
            flagged_at=now - timedelta(hours=5),
            reviewed=False,
            notes=None,
        ),
        # ── Tsuba ────────────────────────────────────────────────────────────
        WatchlistRow(
            watchlist_id=uuid.uuid4(),
            lot_title="Antique Japanese tsuba, iron nanako, Edo, attributed Myochin school",
            artist_name="Unknown",
            trigger_keyword="tsuba",
            platform="kunstveiling",
            current_bid=120.0,
            listing_url="https://www.kunstveiling.nl/lot/55667788",
            ends_at=now + timedelta(hours=19),
            flagged_at=now - timedelta(hours=1),
            reviewed=False,
            notes=None,
        ),
        # ── Derrière le Miroir ───────────────────────────────────────────────
        WatchlistRow(
            watchlist_id=uuid.uuid4(),
            lot_title="Derrière le Miroir No. 125, Giacometti original lithograph",
            artist_name="Unknown",
            trigger_keyword="derriere_le_miroir",
            platform="catawiki",
            current_bid=280.0,
            listing_url="https://www.catawiki.com/en/l/77889900",
            ends_at=now + timedelta(hours=44),
            flagged_at=now - timedelta(hours=3),
            reviewed=False,
            notes=None,
        ),
        WatchlistRow(
            watchlist_id=uuid.uuid4(),
            lot_title="DLM no. 98, Miro colour lithograph on Arches paper",
            artist_name="Unknown",
            trigger_keyword="dlm",
            platform="kunstveiling",
            current_bid=180.0,
            listing_url="https://www.kunstveiling.nl/lot/44556677",
            ends_at=now + timedelta(days=2),
            flagged_at=now - timedelta(hours=8),
            reviewed=False,
            notes=None,
        ),
        # ── Griffelkunst ─────────────────────────────────────────────────────
        WatchlistRow(
            watchlist_id=uuid.uuid4(),
            lot_title="Griffelkunst Jahresmappe 1978, portfolio with 5 signed prints",
            artist_name="Unknown",
            trigger_keyword="griffelkunst",
            platform="kunstveiling",
            current_bid=45.0,
            listing_url="https://www.kunstveiling.nl/lot/33445566",
            ends_at=now + timedelta(days=6),
            flagged_at=now - timedelta(hours=12),
            reviewed=False,
            notes=None,
        ),
        # ── Mourlot ──────────────────────────────────────────────────────────
        WatchlistRow(
            watchlist_id=uuid.uuid4(),
            lot_title="Chagall, Mourlot atelier original lithograph 'Les Amoureux', 1963",
            artist_name="Unknown",
            trigger_keyword="mourlot",
            platform="catawiki",
            current_bid=1_200.0,
            listing_url="https://www.catawiki.com/en/l/99001122",
            ends_at=now + timedelta(hours=10),
            flagged_at=now - timedelta(hours=4),
            reviewed=False,
            notes=None,
        ),
        # ── Estate Stamp ─────────────────────────────────────────────────────
        WatchlistRow(
            watchlist_id=uuid.uuid4(),
            lot_title="Oil on canvas, landscape, estate stamp verso, Dutch school c.1920",
            artist_name="Unknown / Onbekend",
            trigger_keyword="estate_stamp",
            platform="kunstveiling",
            current_bid=90.0,
            listing_url="https://www.kunstveiling.nl/lot/22334455",
            ends_at=now + timedelta(days=4),
            flagged_at=now - timedelta(hours=6),
            reviewed=False,
            notes=None,
        ),
        # ── Stoneware ────────────────────────────────────────────────────────
        WatchlistRow(
            watchlist_id=uuid.uuid4(),
            lot_title="Stoneware vessel, impressed mark, mid-century British studio pottery",
            artist_name="Unknown",
            trigger_keyword="stoneware",
            platform="barnebys",
            current_bid=65.0,
            listing_url="https://www.barnebys.com/buy/lot/99887766",
            ends_at=now + timedelta(days=5),
            flagged_at=now - timedelta(hours=9),
            reviewed=False,
            notes=None,
        ),
    ]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ArtArb specialty opportunities dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--demo", action="store_true",
                        help="Use synthetic demo data (no DB connection needed).")
    parser.add_argument("--refresh", type=int, metavar="N",
                        help="Auto-refresh every N seconds (uses full-screen Live mode).")
    parser.add_argument("--min-conf", type=float, default=0.0, metavar="F",
                        help="Only show opportunities with confidence >= F (e.g. 0.70).")
    args = parser.parse_args()

    def load() -> tuple[list[OpportunityRow], list[WatchlistRow]]:
        opp_rows = _demo_rows() if args.demo else _load_from_db()
        if args.min_conf > 0:
            opp_rows = [r for r in opp_rows if r.confidence_score >= args.min_conf]
        wl_rows = _demo_watchlist_rows() if args.demo else _load_watchlist_from_db()
        return opp_rows, wl_rows

    if args.refresh:
        opp, wl = load()
        with Live(
            build_layout(opp, wl, args.refresh),
            console=console,
            screen=True,
            refresh_per_second=2,
        ) as live:
            while True:
                time.sleep(args.refresh)
                opp, wl = load()
                live.update(build_layout(opp, wl, args.refresh))
    else:
        opp, wl = load()
        console.print(build_layout(opp, wl))


if __name__ == "__main__":
    main()
