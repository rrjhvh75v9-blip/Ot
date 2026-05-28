"""Auction scraper modules.

Each module exposes a ``scrape(max_pages=None)`` callable.
Import everything via this package so main.py stays clean.
"""

from artarb.scrapers.aguttes import scrape as scrape_aguttes
from artarb.scrapers.arenberg import scrape as scrape_arenberg
from artarb.scrapers.barnebys import scrape as scrape_barnebys
from artarb.scrapers.bassenge import scrape as scrape_bassenge
from artarb.scrapers.bernaerts import scrape as scrape_bernaerts
from artarb.scrapers.bonhams_results import scrape as scrape_bonhams
from artarb.scrapers.catawiki import scrape as scrape_catawiki
from artarb.scrapers.dorotheum import scrape as scrape_dorotheum
from artarb.scrapers.drouot import scrape as scrape_drouot
from artarb.scrapers.ketterer import scrape as scrape_ketterer
from artarb.scrapers.kunstveiling import scrape as scrape_kunstveiling
from artarb.scrapers.lempertz_results import scrape as scrape_lempertz
from artarb.scrapers.millon import scrape as scrape_millon
from artarb.scrapers.phillips_results import scrape as scrape_phillips
from artarb.scrapers.piasa import scrape as scrape_piasa
from artarb.scrapers.van_ham import scrape as scrape_van_ham
from artarb.scrapers.vavato import scrape as scrape_vavato
from artarb.scrapers.venduehuis import scrape as scrape_venduehuis

ALL_SCRAPERS: list[tuple[str, object]] = [
    ("kunstveiling", scrape_kunstveiling),
    ("catawiki", scrape_catawiki),
    ("barnebys", scrape_barnebys),
    ("venduehuis", scrape_venduehuis),
    ("bernaerts", scrape_bernaerts),
    ("vavato", scrape_vavato),
    ("bassenge", scrape_bassenge),
    ("van_ham", scrape_van_ham),
    ("arenberg", scrape_arenberg),
    ("piasa", scrape_piasa),
    ("lempertz", scrape_lempertz),
    ("aguttes", scrape_aguttes),
    ("millon", scrape_millon),
    ("dorotheum", scrape_dorotheum),
    ("ketterer", scrape_ketterer),
    ("drouot", scrape_drouot),
    ("bonhams", scrape_bonhams),
    ("phillips", scrape_phillips),
]

__all__ = [
    "ALL_SCRAPERS",
    "scrape_aguttes", "scrape_arenberg", "scrape_barnebys", "scrape_bassenge",
    "scrape_bernaerts", "scrape_bonhams", "scrape_catawiki", "scrape_dorotheum",
    "scrape_drouot", "scrape_ketterer", "scrape_kunstveiling", "scrape_lempertz",
    "scrape_millon", "scrape_phillips", "scrape_piasa", "scrape_van_ham",
    "scrape_vavato", "scrape_venduehuis",
]
