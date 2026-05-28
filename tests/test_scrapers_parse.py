"""
Parse-logic unit tests for all 15 new auction house scrapers.

Each test builds a minimal HTML fragment that looks like one lot card
from that platform, calls the scraper's ``_parse_item`` or ``_parse_items``
function with it, and asserts the returned dict has the expected values.

No network access, no database — all I/O is mocked out.
"""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def first_tag(html: str, sel: str):
    return soup(html).select_one(sel)


# ---------------------------------------------------------------------------
# 1. Venduehuis
# ---------------------------------------------------------------------------

class TestVenduehuis:
    # artist: .lot-artist | .kunstenaar | [data-artist] | .maker
    # hammer: .hammer-price | .toewijzing | .resultaat | .prijs
    HTML = """
    <article class="lot" data-lot-id="VH-001">
      <a href="/nl/kavel/VH-001">Link</a>
      <h2 class="lot-title">Bruin schilderij</h2>
      <span class="lot-artist">Jan Steen</span>
      <span class="dimensions">40 x 30 cm</span>
      <span class="estimate">€ 500 - 800</span>
      <span class="hammer-price">€ 950</span>
      <time datetime="2024-03-15">15 maart 2024</time>
    </article>
    """

    def test_title_and_artist(self):
        from artarb.scrapers.venduehuis import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Bruin schilderij"
        assert result["artist_name"] == "Jan Steen"

    def test_prices_and_status(self):
        from artarb.scrapers.venduehuis import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result["hammer_price"] == pytest.approx(950.0)
        assert result["estimate_low"] == pytest.approx(500.0)
        assert result["estimate_high"] == pytest.approx(800.0)
        assert result["status"] == "sold"

    def test_dimensions(self):
        from artarb.scrapers.venduehuis import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result["width_cm"] == pytest.approx(40.0)
        assert result["height_cm"] == pytest.approx(30.0)

    def test_lot_id(self):
        from artarb.scrapers.venduehuis import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result["source_lot_id"] == "VH-001"

    def test_country_currency(self):
        from artarb.scrapers.venduehuis import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result["currency"] == "EUR"
        assert result["country_sale"] == "NL"


# ---------------------------------------------------------------------------
# 2. Bernaerts
# ---------------------------------------------------------------------------

class TestBernaerts:
    # artist: .lot-artist | .kunstenaar | .auteur | [data-artist]
    # hammer: .hammer | .toewijzing | .resultaat | [class*='result']
    HTML = """
    <article class="lot" data-lot-id="BERN-55">
      <a href="/nl/lot/BERN-55">Link</a>
      <h2 class="lot-title">Zomers landschap</h2>
      <span class="lot-artist">Léon Spilliaert</span>
      <span class="dimensions">25 x 35 cm</span>
      <span class="estimate">€ 1.200 - 1.800</span>
      <span class="hammer">€ 2.100</span>
      <time datetime="2024-04-20">20 april 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.bernaerts import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Zomers landschap"
        assert result["artist_name"] == "Léon Spilliaert"
        assert result["status"] == "sold"
        assert result["hammer_price"] == pytest.approx(2100.0)
        assert result["country_sale"] == "BE"


# ---------------------------------------------------------------------------
# 3. Vavato
# ---------------------------------------------------------------------------

class TestVavato:
    # title: .lot-card__title | .item-title | h2 | h3
    # bid: .current-bid | .bod | [data-current-bid] | .price
    # Vavato has no hammer_price in its item dict — active only
    HTML = """
    <article class="lot-card" data-lot-id="VAV-99">
      <a href="/nl/lot/VAV-99">Link</a>
      <h2 class="lot-card__title">Keramische vaas</h2>
      <span class="lot-card__artist">Unknown</span>
      <span class="estimate">€ 80 - 120</span>
      <span class="current-bid">€ 60</span>
    </article>
    """

    def test_active_listing(self):
        from artarb.scrapers.vavato import _parse_item
        tag = first_tag(self.HTML, "article.lot-card")
        result = _parse_item(tag)
        assert result is not None
        assert result["status"] == "active"
        assert "hammer_price" not in result
        assert result["opening_bid"] == pytest.approx(80.0)
        assert result["current_bid"] == pytest.approx(60.0)
        assert result["country_sale"] == "BE"


# ---------------------------------------------------------------------------
# 4. Bassenge
# ---------------------------------------------------------------------------

class TestBassenge:
    HTML = """
    <article class="lot" data-lot-id="BAS-1001">
      <a href="/auktionen/lot/BAS-1001">Link</a>
      <h2 class="lot-title">Expressionistische Zeichnung</h2>
      <span class="artist">Ernst Ludwig Kirchner</span>
      <span class="technique">Kohle auf Papier</span>
      <span class="dimensions">30 x 40 cm</span>
      <span class="estimate">€ 3.000 - 5.000</span>
      <span class="hammer">€ 6.200</span>
      <time datetime="2024-05-10">10. Mai 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.bassenge import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Expressionistische Zeichnung"
        assert result["hammer_price"] == pytest.approx(6200.0)
        assert result["status"] == "sold"
        assert result["country_sale"] == "DE"


# ---------------------------------------------------------------------------
# 5. Van Ham
# ---------------------------------------------------------------------------

class TestVanHam:
    HTML = """
    <div data-lot-id="VH-2024-500">
      <a href="/en/results/lot/VH-2024-500">Link</a>
      <h2 class="lot-title">CoBrA composition</h2>
      <span class="lot-artist">Karel Appel</span>
      <span class="technique">Oil on canvas</span>
      <span class="dimensions">80 x 60 cm</span>
      <span class="estimate">€ 15.000 - 20.000</span>
      <span class="hammer-price">€ 22.000</span>
      <time datetime="2024-06-01">1 June 2024</time>
    </div>
    """

    def test_basics(self):
        from artarb.scrapers.van_ham import _parse_item
        tag = first_tag(self.HTML, "[data-lot-id]")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "CoBrA composition"
        assert result["artist_name"] == "Karel Appel"
        assert result["hammer_price"] == pytest.approx(22000.0)
        assert result["status"] == "sold"
        assert result["country_sale"] == "DE"


# ---------------------------------------------------------------------------
# 6. Arenberg
# ---------------------------------------------------------------------------

class TestArenberg:
    HTML = """
    <article class="lot" data-lot="ARB-300">
      <a href="/nl/kavel/ARB-300">Link</a>
      <h2 class="lot-title">Porselein vaas met deksel</h2>
      <span class="kunstenaar">James Ensor</span>
      <span class="techniek">Olieverf</span>
      <span class="afmetingen">45 x 45 cm</span>
      <span class="schatting">€ 800 - 1.200</span>
      <span class="toewijzing">€ 1.400</span>
      <time datetime="2024-03-22">22 maart 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.arenberg import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Porselein vaas met deksel"
        assert result["artist_name"] == "James Ensor"
        assert result["hammer_price"] == pytest.approx(1400.0)
        assert result["status"] == "sold"
        assert result["country_sale"] == "BE"
        assert result["source_lot_id"] == "ARB-300"


# ---------------------------------------------------------------------------
# 7. PIASA
# ---------------------------------------------------------------------------

class TestPiasa:
    HTML = """
    <article class="lot" data-lot-id="PIA-2024-88">
      <a href="/lot/PIA-2024-88">Link</a>
      <h2 class="lot-title">Composition surréaliste</h2>
      <span class="artiste">Toyen</span>
      <span class="technique">Huile sur toile</span>
      <span class="dimensions">50 x 65 cm</span>
      <span class="estimation">€ 8.000 - 12.000</span>
      <span class="adjudication">€ 14.500</span>
      <time datetime="2024-04-05">5 avril 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.piasa import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Composition surréaliste"
        assert result["artist_name"] == "Toyen"
        assert result["hammer_price"] == pytest.approx(14500.0)
        assert result["estimate_low"] == pytest.approx(8000.0)
        assert result["estimate_high"] == pytest.approx(12000.0)
        assert result["country_sale"] == "FR"


# ---------------------------------------------------------------------------
# 8. Lempertz
# ---------------------------------------------------------------------------

class TestLempertz:
    HTML = """
    <article class="lot" data-lot-id="LEM-1234">
      <a href="/en/catalogues/lot/LEM-1234">Link</a>
      <h2 class="lot-title">Abstrakte Komposition</h2>
      <span class="lot-artist">Wols</span>
      <span class="technik">Öl auf Leinwand</span>
      <span class="masse">70 x 50 cm</span>
      <span class="schaetzung">€ 20.000 - 30.000</span>
      <span class="zuschlag">€ 35.000</span>
      <time datetime="2024-05-18">18 May 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.lempertz_results import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Abstrakte Komposition"
        assert result["artist_name"] == "Wols"
        assert result["hammer_price"] == pytest.approx(35000.0)
        assert result["country_sale"] == "DE"


# ---------------------------------------------------------------------------
# 9. Aguttes
# ---------------------------------------------------------------------------

class TestAguttes:
    HTML = """
    <article class="lot" data-lot-id="AGU-555">
      <a href="/lot/AGU-555">Link</a>
      <h2 class="lot-title">Peinture CoBrA</h2>
      <span class="artiste">Pierre Alechinsky</span>
      <span class="technique">Acrylique sur papier</span>
      <span class="dimensions">40 x 55 cm</span>
      <span class="estimation">€ 5.000 - 8.000</span>
      <span class="adjudication">€ 9.200</span>
      <time datetime="2024-06-15">15 juin 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.aguttes import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Peinture CoBrA"
        assert result["artist_name"] == "Pierre Alechinsky"
        assert result["hammer_price"] == pytest.approx(9200.0)
        assert result["country_sale"] == "FR"


# ---------------------------------------------------------------------------
# 10. Millon
# ---------------------------------------------------------------------------

class TestMillon:
    HTML = """
    <article class="lot" data-lot-id="MIL-777">
      <a href="/lot/MIL-777">Link</a>
      <h2 class="lot-title">Gravure originale signée</h2>
      <span class="artiste">André Masson</span>
      <span class="technique">Lithographie</span>
      <span class="dimensions">32 x 45 cm</span>
      <span class="estimation">€ 400 - 600</span>
      <span class="adjudication">€ 720</span>
      <time datetime="2024-04-28">28 avril 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.millon import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Gravure originale signée"
        assert result["hammer_price"] == pytest.approx(720.0)
        assert result["country_sale"] == "FR"

    def test_lot_id_from_data_attr(self):
        from artarb.scrapers.millon import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result["source_lot_id"] == "MIL-777"


# ---------------------------------------------------------------------------
# 11. Dorotheum
# ---------------------------------------------------------------------------

class TestDorotheum:
    HTML = """
    <div data-lot-id="DOR-20240601-42">
      <a href="/en/auctions/results/lot/DOR-20240601-42">Link</a>
      <h2 class="lot-title">Wiener Werkstätte vase</h2>
      <span class="artist">Koloman Moser</span>
      <span class="technique">Porcelain with enamel decoration</span>
      <span class="dimensions">28 x 12 cm</span>
      <span class="estimate">€ 6.000 - 9.000</span>
      <span class="hammer-price">€ 11.000</span>
      <time datetime="2024-06-01">1 June 2024</time>
    </div>
    """

    def test_basics(self):
        from artarb.scrapers.dorotheum import _parse_item
        tag = first_tag(self.HTML, "[data-lot-id]")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Wiener Werkstätte vase"
        assert result["hammer_price"] == pytest.approx(11000.0)
        assert result["country_sale"] == "AT"
        assert result["currency"] == "EUR"


# ---------------------------------------------------------------------------
# 12. Ketterer
# ---------------------------------------------------------------------------

class TestKetterer:
    HTML = """
    <article class="lot" data-lot-id="KET-2024-88">
      <a href="/ergebnisse/detail/KET-2024-88">Link</a>
      <h2 class="werktitel">Berliner Strasse</h2>
      <span class="kuenstler">Ernst Ludwig Kirchner</span>
      <span class="technik">Holzschnitt</span>
      <span class="masse">38 x 28 cm</span>
      <span class="schaetzpreis">€ 12.000 - 18.000</span>
      <span class="zuschlag">€ 21.000</span>
      <time datetime="2024-05-25">25 Mai 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.ketterer import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Berliner Strasse"
        assert result["artist_name"] == "Ernst Ludwig Kirchner"
        assert result["hammer_price"] == pytest.approx(21000.0)
        assert result["country_sale"] == "DE"


# ---------------------------------------------------------------------------
# 13. Drouot
# ---------------------------------------------------------------------------

class TestDrouot:
    HTML = """
    <article class="lot" data-lot-id="DRO-2024-1234">
      <a href="/lots/DRO-2024-1234">Link</a>
      <h2 class="lot-title">Affiche originale lithographiée</h2>
      <span class="artiste">Henri Matisse</span>
      <span class="technique">Lithographie couleur</span>
      <span class="dimensions">60 x 45 cm</span>
      <span class="estimation">€ 1.500 - 2.000</span>
      <span class="adjudication">€ 2.800</span>
      <time datetime="2024-04-12">12 avril 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.drouot import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Affiche originale lithographiée"
        assert result["hammer_price"] == pytest.approx(2800.0)
        assert result["country_sale"] == "FR"


# ---------------------------------------------------------------------------
# 14. Bonhams
# ---------------------------------------------------------------------------

class TestBonhams:
    HTML = """
    <article class="lot" data-lot-id="BON-20240501-99">
      <a href="/lot/BON-20240501-99">Link</a>
      <h2 class="lot-title">Abstract Composition</h2>
      <span class="lot-artist">Karel Appel</span>
      <span class="technique">Oil on canvas</span>
      <span class="dimensions">100 x 80 cm</span>
      <span class="estimate">£ 15,000 - 20,000</span>
      <span class="hammer-price">£ 25,000</span>
      <time datetime="2024-05-01">1 May 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.bonhams_results import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Abstract Composition"
        assert result["artist_name"] == "Karel Appel"
        assert result["hammer_price"] == pytest.approx(25000.0)
        assert result["country_sale"] == "GB"
        assert result["currency"] == "GBP"

    def test_currency_detection_eur(self):
        from artarb.scrapers.bonhams_results import _parse_item
        html = """
        <article class="lot" data-lot-id="BON-EUR-1">
          <a href="/lot/BON-EUR-1">Link</a>
          <h2 class="lot-title">Test lot EUR</h2>
          <span class="estimate">€ 1.000 - 2.000</span>
          <span class="hammer-price">€ 2.200</span>
        </article>
        """
        tag = first_tag(html, "article.lot")
        result = _parse_item(tag)
        assert result["currency"] == "EUR"


# ---------------------------------------------------------------------------
# 15. Phillips
# ---------------------------------------------------------------------------

class TestPhillips:
    HTML = """
    <article class="lot" data-lot-id="PHI-2024-250">
      <a href="/lot/PHI-2024-250">Link</a>
      <h2 class="lot-title">Untitled (CoBrA)</h2>
      <span class="lot-artist">Asger Jorn</span>
      <span class="technique">Oil on canvas</span>
      <span class="dimensions">90 x 70 cm</span>
      <span class="estimate">£ 20,000 - 30,000</span>
      <span class="hammer-price">£ 38,000</span>
      <time datetime="2024-06-10">10 June 2024</time>
    </article>
    """

    def test_basics(self):
        from artarb.scrapers.phillips_results import _parse_item
        tag = first_tag(self.HTML, "article.lot")
        result = _parse_item(tag)
        assert result is not None
        assert result["title"] == "Untitled (CoBrA)"
        assert result["artist_name"] == "Asger Jorn"
        assert result["hammer_price"] == pytest.approx(38000.0)
        assert result["country_sale"] == "GB"
        assert result["currency"] == "GBP"

    def test_no_title_returns_none(self):
        from artarb.scrapers.phillips_results import _parse_item
        html = """<article class="lot" data-lot-id="PHI-NOTITLE"><a href="/lot/x">L</a></article>"""
        tag = first_tag(html, "article.lot")
        assert _parse_item(tag) is None


# ---------------------------------------------------------------------------
# _parse_items: selector fallback logic (shared pattern across scrapers)
# ---------------------------------------------------------------------------

class TestParseItemsSelectorFallback:
    """Verify that _parse_items tries selectors in order and takes first match."""

    def test_venduehuis_article_fallback(self):
        """Lot wrapped in bare <article> still parsed when article.lot fails."""
        from artarb.scrapers.venduehuis import _parse_items
        html = """
        <html><body>
        <article>
          <a href="/nl/kavel/99">Link</a>
          <h2 class="lot-title">Fallback test</h2>
          <span class="artist">X</span>
        </article>
        </body></html>
        """
        results = _parse_items(soup(html))
        assert len(results) == 1
        assert results[0]["title"] == "Fallback test"

    def test_empty_page_returns_empty(self):
        from artarb.scrapers.venduehuis import _parse_items
        results = _parse_items(soup("<html><body><p>No lots here</p></body></html>"))
        assert results == []


# ---------------------------------------------------------------------------
# _lot_id helpers
# ---------------------------------------------------------------------------

class TestLotIdHelpers:
    def test_aguttes_lot_url(self):
        from artarb.scrapers.aguttes import _lot_id
        assert _lot_id("https://www.aguttes.com/lot/12345") == "12345"

    def test_aguttes_oeuvre_url(self):
        from artarb.scrapers.aguttes import _lot_id
        assert _lot_id("https://www.aguttes.com/oeuvre/abc-def") == "abc-def"

    def test_piasa_lot_url(self):
        from artarb.scrapers.piasa import _lot_id
        assert _lot_id("https://www.piasa.fr/lot/PIA-99") == "PIA-99"

    def test_lempertz_numeric(self):
        from artarb.scrapers.lempertz_results import _lot_id
        assert _lot_id("https://www.lempertz.com/en/catalogues/2024") == "2024"

    def test_arenberg_kavel(self):
        from artarb.scrapers.arenberg import _lot_id
        assert _lot_id("https://www.arenberg-auctions.com/nl/kavel/ARB-100") == "ARB-100"

    def test_drouot_lots(self):
        from artarb.scrapers.drouot import _lot_id
        assert _lot_id("https://www.drouot.com/lots/DRO-2024-9999") == "DRO-2024-9999"

    def test_none_input(self):
        from artarb.scrapers.aguttes import _lot_id
        assert _lot_id(None) is None

    def test_unrecognised_url_returns_none(self):
        from artarb.scrapers.millon import _lot_id
        assert _lot_id("https://www.millon.com/") is None

    def test_ketterer_detail(self):
        from artarb.scrapers.ketterer import _lot_id
        assert _lot_id("https://www.kettererkunst.de/ergebnisse/detail/KET-88") == "KET-88"

    def test_dorotheum_result(self):
        from artarb.scrapers.dorotheum import _lot_id
        assert _lot_id("https://www.dorotheum.com/en/auctions/results/lot/DOR-42") == "DOR-42"
