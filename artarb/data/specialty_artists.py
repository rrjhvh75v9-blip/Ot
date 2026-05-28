"""
Specialty artist importer — curated high-arbitrage niches.

Inserts or updates artists in five categories that the RKD API does not
cover or systematically under-covers:

  czech_avantgarde           Czech inter-war avant-garde (8 artists)
  studio_pottery             British studio pottery (8 artists)
  german_expressionist_prints  Käthe Kollwitz (1 artist)
  mucha_drawings             Alphonse Mucha drawings (1 artist)
  russian_futurist           Russian Futurist book illustrators (6 artists)

Deduplication key: name_canonical (exact match).
Running this importer repeatedly is safe — existing rows are updated in
place, new rows are inserted.  The rkd_id column is intentionally left
NULL for all specialty artists; they are tracked by name, not RKD priref.

Usage
-----
    python -m artarb.data.specialty_artists

Programmatic:
    from artarb.data.specialty_artists import run
    stats = run()
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from artarb.database import get_session
from artarb.models.base import Artist

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Curated artist records
# ---------------------------------------------------------------------------
# Each dict maps directly onto Artist columns.  name_variants is stored as
# a JSON array (list[str]) — the name_resolver indexes every entry, so all
# variant spellings become searchable immediately after import.

SPECIALTY_ARTISTS: list[dict] = [

    # ── Czech inter-war avant-garde ─────────────────────────────────────────

    {
        "name_canonical": "Toyen",
        "name_variants": [
            "Marie Čermínová",
            "Marie Cerminova",
            "Marie Toyen Čermínová",
        ],
        "nationality": "Czech",
        "born": 1902,
        "died": 1980,
        "movement": "Surrealism, Czech Avant-Garde",
        "market_tier": "1",
        "specialty_category": "czech_avantgarde",
        "arbitrage_notes": (
            "Works frequently priced below Parisian Surrealists despite equal historical "
            "standing. Signed 'Toyen' lower right; mononym is the only valid signature — "
            "any work signed 'Čermínová' or 'Cerminova' predates her adoption of the name "
            "and requires specialist vetting. Standard catalogue: Annie Le Brun, 'Toyen' "
            "(2002). Drawings and gouaches persistently undervalued vs. oils. Strong "
            "repatriation demand from Czech institutions (National Gallery Prague, Moravian "
            "Gallery) creates reliable buy-side at Prague auction. Works catalogued as "
            "'Artificialism' period (1926–1929, with Štyrský) command the highest premiums."
        ),
    },
    {
        "name_canonical": "Štyrský, Jindřich",
        "name_variants": [
            "Jindřich Štyrský",
            "Jindrich Styrsky",
            "Styrsky",
        ],
        "nationality": "Czech",
        "born": 1899,
        "died": 1942,
        "movement": "Czech Surrealism, Artificialism",
        "market_tier": "1",
        "specialty_category": "czech_avantgarde",
        "arbitrage_notes": (
            "Short career (died age 42) strictly caps supply. Collaborator of Toyen — "
            "joint or sequentially collected provenance from Czech collections adds a "
            "10–20% premium. Works appear infrequently at Western auction; Czech houses "
            "(Dorotheum Prague, Adolf Loos Apartment & Gallery) regularly achieve "
            "30–50% more than equivalent Vienna or London estimates. Erotic collage "
            "series ('Stěhování duší', 'Emilie přichází ke mně ve snu') are the most "
            "liquid and highest-value segment. Signed 'Štyrský' lower right; diacritics "
            "present on all authentic signatures."
        ),
    },
    {
        "name_canonical": "Šíma, Josef",
        "name_variants": [
            "Josef Šíma",
            "Josef Sima",
            "Joseph Sima",
        ],
        "nationality": "Czech",
        "born": 1891,
        "died": 1971,
        "movement": "Czech Surrealism, Le Grand Jeu",
        "market_tier": "1",
        "specialty_category": "czech_avantgarde",
        "arbitrage_notes": (
            "Associated with the French literary group Le Grand Jeu (René Daumal, Roger "
            "Vailland). Works held in both Czech and French collections create a persistent "
            "price gap: Paris sales undervalue by 25–40% compared to Prague. Lithographic "
            "prints especially mispriced in Western markets. Paintings of floating figures "
            "over Bohemian landscape are the signature and most sought subject. "
            "Authentication: Musée d'Art Moderne de Paris and Moravian Gallery Brno hold "
            "key comparison works."
        ),
    },
    {
        "name_canonical": "Kupka, František",
        "name_variants": [
            "František Kupka",
            "Frantisek Kupka",
            "Frank Kupka",
            "François Kupka",
            "Francis Kupka",
            "Franz Kupka",
        ],
        "nationality": "Czech",
        "born": 1871,
        "died": 1957,
        "movement": "Orphism, Abstract Art",
        "market_tier": "1",
        "specialty_category": "czech_avantgarde",
        "arbitrage_notes": (
            "Pioneer of pure abstraction, exhibiting non-representational work from "
            "1912 — contemporaneous with but independent of Kandinsky. Gouaches and "
            "pastels frequently misattributed to the French abstract school; correctly "
            "attributed works achieve substantially higher prices. Definitive catalogue "
            "raisonné: Félix Loys Kupka / Centre Pompidou archive. Beware of posthumous "
            "offset prints sold as original works — verify paper stock, registration "
            "marks, and margin annotations. Works from the 'Amorpha' and 'Localisations "
            "de mobiles graphiques' series command the highest premiums. Czech national "
            "identity and Centre Pompidou institutional demand sustain a strong floor price."
        ),
    },
    {
        "name_canonical": "Filla, Emil",
        "name_variants": [
            "Emil Filla",
        ],
        "nationality": "Czech",
        "born": 1882,
        "died": 1953,
        "movement": "Czech Cubism",
        "market_tier": "1",
        "specialty_category": "czech_avantgarde",
        "arbitrage_notes": (
            "Leading Czech Cubist; imprisoned in Dachau and Buchenwald 1939–1945. "
            "Pre-1938 drawings and watercolors are the highest-demand segment — "
            "supply definitively fixed by imprisonment. Works from the wartime period "
            "(camp drawings) are extremely rare and primarily institutional. National "
            "Gallery Prague catalogue ('Emil Filla', 1964) is the standard authentication "
            "reference. Works confiscated during Nazi occupation occasionally surface "
            "through restitution proceedings — provenance due diligence is essential. "
            "Dutch and Belgian auction houses systematically undervalue vs. Prague and Vienna."
        ),
    },
    {
        "name_canonical": "Kubín, Otakar",
        "name_variants": [
            "Otakar Kubín",
            "Othon Coubine",
            "Ottokar Kubin",
            "Kubín Otakar",
            "Coubine, Othon",
        ],
        "nationality": "Czech",
        "born": 1883,
        "died": 1969,
        "movement": "Post-Impressionism, Fauvism",
        "market_tier": "1",
        "specialty_category": "czech_avantgarde",
        "arbitrage_notes": (
            "Key arbitrage: legally changed name to Othon Coubine in France — works "
            "sold as 'Coubine' and 'Kubín' represent the same artist but trade in "
            "separate markets. Paris auction estimates for 'Coubine' typically run "
            "20–40% higher than identical-period works catalogued as 'Kubín' at Czech "
            "houses. Collecting both name-forms to resell under the higher-value identity "
            "is the primary strategy. Signed works: 'Kubín' before c.1920, 'Coubine' or "
            "'Othon Coubine' thereafter. Provence landscapes (Sanary-sur-Mer period, "
            "1920s–1930s) are the most liquid segment in both markets."
        ),
    },
    {
        "name_canonical": "Špála, Václav",
        "name_variants": [
            "Václav Špála",
            "Vaclav Spala",
            "Spala",
        ],
        "nationality": "Czech",
        "born": 1885,
        "died": 1946,
        "movement": "Czech Avant-Garde, Fauvism",
        "market_tier": "1",
        "specialty_category": "czech_avantgarde",
        "arbitrage_notes": (
            "Core member of Tvrdošíjní (Obstinate Ones) group. Strong and liquid secondary "
            "market in Prague but consistently underrepresented in Western catalogues. "
            "Bohemian landscapes and figure studies in bold colour are signature subjects. "
            "Works appear at Sotheby's and Christie's only rarely, creating consistent "
            "price gaps of 25–45% vs. Prague market. Signed 'Špála' with diacritics on "
            "all authentic works; beware of works signed 'Spala' (may indicate later "
            "transcription rather than original signature). Prague City Gallery holds "
            "primary comparison collection."
        ),
    },
    {
        "name_canonical": "Čapek, Josef",
        "name_variants": [
            "Josef Čapek",
            "Josef Capek",
            "Capek, Josef",
        ],
        "nationality": "Czech",
        "born": 1887,
        "died": 1945,
        "movement": "Czech Cubism",
        "market_tier": "1",
        "specialty_category": "czech_avantgarde",
        "arbitrage_notes": (
            "Brother of playwright Karel Čapek; died in Bergen-Belsen concentration camp, "
            "April 1945. Death circumstances lend strong historical significance and "
            "institutional collecting interest. Works from 1909–1938 are the core "
            "market segment. Drawings command a premium due to scarcity — he was "
            "primarily a painter, making works on paper rarer. Attribution caution: "
            "signature must read 'Josef Čapek' (or 'J. Čapek') — distinguish from "
            "brother Karel, who illustrated but rarely made fine art. National Gallery "
            "Prague and Museum of Decorative Arts hold primary reference collections."
        ),
    },

    # ── British studio pottery ───────────────────────────────────────────────

    {
        "name_canonical": "Rie, Lucie",
        "name_variants": [
            "Lucie Rie",
            "Lucie Gomperz",
            "Dame Lucie Rie",
            "L. Rie",
        ],
        "nationality": "British",
        "born": 1902,
        "died": 1995,
        "movement": "Studio Pottery",
        "market_tier": "1",
        "specialty_category": "studio_pottery",
        "arbitrage_notes": (
            "Most sought-after British studio potter; prices sustained by institutional "
            "and private demand globally. Authentication mark: impressed 'LR' seal in "
            "oval, typically on base. Catalogue raisonné: Emmanuel Cooper, 'The Pottery "
            "of Lucie Rie' (1981, revised 1994). Two clearly distinct market segments: "
            "Vienna period (pre-1938, before emigration) and London period — Vienna "
            "period commands significant premium for documented Austrian provenance. "
            "Sgraffito bowls, fluted forms, and works with uranium-oxide glazes are "
            "highest value. Unsigned works attributed to her workshop require provenance "
            "from Cooper or early exhibition records to authenticate."
        ),
    },
    {
        "name_canonical": "Coper, Hans",
        "name_variants": [
            "Hans Coper",
        ],
        "nationality": "British",
        "born": 1920,
        "died": 1981,
        "movement": "Studio Pottery",
        "market_tier": "1",
        "specialty_category": "studio_pottery",
        "arbitrage_notes": (
            "Worked with Lucie Rie 1946–1958 — joint or sequentially collected "
            "provenance from this period adds a 15–25% premium. Authentication mark: "
            "impressed 'HC' within oval on base. Limited output due to early death from "
            "motor neurone disease (aged 61) makes supply inelastic. Cycladic forms, "
            "spade-top ('Thistle') forms, and disc forms command the highest premiums. "
            "German and Swiss collectors create persistent demand above UK market prices "
            "— arbitrage opportunity by sourcing at UK specialist sales "
            "(Woolley & Wallis, Roseberys) and reselling to Continental European buyers. "
            "Exhibition provenance from Bonniers Gallery Stockholm (1973) or Boijmans "
            "Van Beuningen adds authentication value."
        ),
    },
    {
        "name_canonical": "Leach, Bernard",
        "name_variants": [
            "Bernard Leach",
            "Bernard Howell Leach",
            "B. Leach",
        ],
        "nationality": "British",
        "born": 1887,
        "died": 1979,
        "movement": "Studio Pottery, Mingei",
        "market_tier": "1",
        "specialty_category": "studio_pottery",
        "arbitrage_notes": (
            "Founding figure of British studio pottery. St. Ives Pottery mark: impressed "
            "'BL' personal seal alongside 'S' mark (St. Ives) and 'L' (Leach Pottery). "
            "Works from the 1920s–1940s are most sought; early pieces with Japanese "
            "influence command the highest premiums. Japanese collectors create strong "
            "independent demand for his Japan-influenced works — arbitrage between UK "
            "specialist sales and Japanese houses (SBI Art Auction, Mitsukoshi). "
            "'A Potter's Book' (1940) remains the foundational reference for attribution. "
            "Student pots occasionally misattributed to Leach — verify against Leach "
            "Pottery archive held at Tate St. Ives."
        ),
    },
    {
        "name_canonical": "Hamada, Shoji",
        "name_variants": [
            "Shoji Hamada",
            "Hamada Shōji",
            "濱田庄司",
            "Hamada Syoji",
            "Shoji Hamada",
        ],
        "nationality": "Japanese",
        "born": 1894,
        "died": 1978,
        "movement": "Mingei, Studio Pottery",
        "market_tier": "1",
        "specialty_category": "studio_pottery",
        "arbitrage_notes": (
            "Japanese Living National Treasure (designated 1955). Hamada rarely signed "
            "his work — authentication relies on exhibition history, Mashiko provenance "
            "(pieces wrapped in newspaper from his kiln village), and established "
            "collection history. Significant price gap: Japanese auction houses "
            "(Mitsukoshi, SBI Art Auction, Mainichi Auction) consistently 40–60% "
            "below Christie's London estimates for equivalent pieces. Handle shapes, "
            "wax-resist (hakeme and nuka) decoration, and Mashiko clay body are "
            "signature identifiers. Works from co-operative kilns with Bernard Leach "
            "(1920–1923 St. Ives period) are the rarest and highest-value segment."
        ),
    },
    {
        "name_canonical": "Cardew, Michael",
        "name_variants": [
            "Michael Cardew",
            "Michael Ambrose Cardew",
            "M. Cardew",
        ],
        "nationality": "British",
        "born": 1901,
        "died": 1983,
        "movement": "Studio Pottery, Mingei",
        "market_tier": "1",
        "specialty_category": "studio_pottery",
        "arbitrage_notes": (
            "Student of Bernard Leach; established three distinct pottery periods, each "
            "a separate market: Winchcombe Pottery (1926–1942), Wenford Bridge Pottery "
            "(1939–1983), and West African period (Achimota/Vumé, Ghana, 1942–1965). "
            "African-period works are dramatically undervalued outside specialist "
            "collectors — Ghana-made pieces with local clay and wood-ash glazes appear "
            "sporadically at general auction with little buyer awareness. Mark: impressed "
            "'MC' monogram or full 'Wenford' stamp (post-1952). Standard catalogue: "
            "Garth Clark, 'Michael Cardew' (1978). Winchcombe slip-ware cider jars are "
            "the most liquid and recognisable segment."
        ),
    },
    {
        "name_canonical": "Pleydell-Bouverie, Katherine",
        "name_variants": [
            "Katherine Pleydell-Bouverie",
            "Beano Pleydell-Bouverie",
            "K. Pleydell-Bouverie",
            "K.P.B.",
        ],
        "nationality": "British",
        "born": 1895,
        "died": 1985,
        "movement": "Studio Pottery",
        "market_tier": "1",
        "specialty_category": "studio_pottery",
        "arbitrage_notes": (
            "Student of Bernard Leach (1924); pioneered plant-ash glazes — her research "
            "notebooks (held at Crafts Study Centre, Farnham) are the primary technical "
            "reference. Mark: impressed 'KPB' or 'CPB' seal. Works appear infrequently "
            "at general auction; most have sold privately through Crafts Council or "
            "passed directly between specialist collectors. Coleshill period (1924–1946, "
            "with Norah Braden) vs. Kilmington Manor period (post-1946) are distinct "
            "market segments — Coleshill period preferred. Ash-glazed stoneware with "
            "subtle celadon and iron-brown surfaces are signature works. Very few "
            "forgeries due to relative obscurity outside specialist market."
        ),
    },
    {
        "name_canonical": "Braden, Norah",
        "name_variants": [
            "Norah Braden",
            "N. Braden",
        ],
        "nationality": "British",
        "born": 1901,
        "died": 2001,
        "movement": "Studio Pottery",
        "market_tier": "1",
        "specialty_category": "studio_pottery",
        "arbitrage_notes": (
            "Stopped potting in 1936, making the entire known output finite and fixed. "
            "Mark: impressed 'NB' seal. Worked at Coleshill with Katherine "
            "Pleydell-Bouverie 1928–1936 — joint exhibition records from this period "
            "are the strongest authentication signal for unsigned or ambiguously marked "
            "pieces. Market is almost entirely specialist; general auction estimates "
            "routinely undervalue by 50–100% relative to specialist sales "
            "(Bonhams Ceramics, Woolley & Wallis). Small output means any authenticated "
            "work is a material market event. Crafts Study Centre and Victoria & Albert "
            "Museum hold primary comparison collections."
        ),
    },
    {
        "name_canonical": "Murray, William Staite",
        "name_variants": [
            "William Staite Murray",
            "W. Staite Murray",
            "W.S. Murray",
        ],
        "nationality": "British",
        "born": 1881,
        "died": 1962,
        "movement": "Studio Pottery",
        "market_tier": "1",
        "specialty_category": "studio_pottery",
        "arbitrage_notes": (
            "Exhibited pottery as fine art alongside Ben Nicholson and Christopher Wood; "
            "charged prices equivalent to paintings. Authentication mark: impressed 'M' "
            "alongside individually inscribed title (e.g. 'The Bather', 'Wheel of Life'). "
            "Titled pots command substantial premiums over untitled — research titles "
            "against exhibition catalogues before bidding. Large forms (over 40 cm) are "
            "extremely rare; tall vases in reduced-atmosphere stoneware are signature "
            "works. Emigrated to Southern Rhodesia (Zimbabwe) 1940 — late works rarely "
            "appear at auction and provenance from African period requires verification. "
            "Victoria & Albert Museum holds primary comparison collection. Prices "
            "significantly lower at South African auction vs. London specialist sales."
        ),
    },

    # ── German Expressionist prints — Käthe Kollwitz ─────────────────────────

    {
        "name_canonical": "Kollwitz, Käthe",
        "name_variants": [
            "Käthe Kollwitz",
            "Kathe Kollwitz",
            "Käthe Schmidt-Kollwitz",
            "Kathe Schmidt Kollwitz",
            "К. Кольвиц",
            "Кете Кольвиц",
        ],
        "nationality": "German",
        "born": 1867,
        "died": 1945,
        "movement": "German Expressionism, Social Realism",
        "market_tier": "1",
        "specialty_category": "german_expressionist_prints",
        "arbitrage_notes": (
            "Standard print catalogue: August Klipstein, 'Käthe Kollwitz: Verzeichnis "
            "des graphischen Werkes' (1955) — every print has a 'Kl.' number. First "
            "states with wide margins command 3–5× premiums over later impressions from "
            "the same plate; always check impression number and state against Klipstein. "
            "Sculpture: posthumous bronze casts authorised by the Kollwitz Estate vs. "
            "unauthorised posthumous casts — Kollwitz Museum Köln maintains cast records; "
            "consult before bidding on any bronze. Signed impressions (pencil 'K.K.' "
            "lower right) vs. stamped posthumous editions — price differential can be "
            "3–10×. Regional German auction houses (Ketterer Kunst, Grisebach, "
            "Karl & Faber) consistently price 20–35% below London and New York for "
            "equivalent impression quality — primary arbitrage route."
        ),
    },

    # ── Alphonse Mucha drawings ──────────────────────────────────────────────

    {
        "name_canonical": "Mucha, Alphonse",
        "name_variants": [
            "Alphonse Mucha",
            "Alfons Mucha",
            "Alphonse Maria Mucha",
            "Alfons Maria Mucha",
        ],
        "nationality": "Czech",
        "born": 1860,
        "died": 1939,
        "movement": "Art Nouveau",
        "market_tier": "1",
        "specialty_category": "mucha_drawings",
        "arbitrage_notes": (
            "DRAWINGS AND WORKS ON PAPER ONLY — the poster market is oversupplied and "
            "frequently reproduced; do not buy posters without extensive authentication. "
            "Original drawings (chalk, pastel, pencil studies for poster compositions) "
            "are the arbitrage opportunity: vastly undervalued relative to the finished "
            "poster, yet carry greater scholarly and institutional interest. "
            "Authentication via Mucha Foundation, Prague (www.mucha.cz) — they maintain "
            "the authoritative archive. Key segment: preparatory studies for 'Slav Epic' "
            "cycles (1910–1928) are most sought by Czech institutional buyers and the "
            "Mucha Foundation itself. Provenance from the original Paris Mucha studio "
            "(Rue du Val-de-Grâce) is the strongest authentication signal. "
            "Photomechanical reproductions of drawings circulate widely — verify paper "
            "texture, pencil or chalk media, and surface handling under raking light. "
            "Czech collectors create particularly strong demand for Bohemian-themed and "
            "Slavic-subject works; Paris-period decorative works have broader "
            "international appeal."
        ),
    },

    # ── Russian Futurist book illustrators ──────────────────────────────────

    {
        "name_canonical": "Lissitzky, El",
        "name_variants": [
            "El Lissitzky",
            "Lazar Lissitzky",
            "Лазарь Лисицкий",
            "Лазарь Маркович Лисицкий",
            "Lazar Markovich Lissitzky",
            "El El",
            "L. Lissitzky",
        ],
        "nationality": "Soviet",
        "born": 1890,
        "died": 1941,
        "movement": "Constructivism, Russian Avant-Garde",
        "market_tier": "1",
        "specialty_category": "russian_futurist",
        "arbitrage_notes": (
            "PROUN series (Project for the Affirmation of the New) and book design are "
            "the core market; original watercolours extremely rare and only appear at "
            "major international auction. Key reference: Sophie Lissitzky-Küppers, "
            "'El Lissitzky: Life, Letters, Texts' (widow's catalogue, 1968). "
            "Distinguish lithographic originals from later offset reprints by paper stock, "
            "plate tone, and registration marks — originals show hand-applied colour "
            "variations across the edition. For PROUN lithographs: first edition "
            "portfolios (Berlin, 1923) are primary market; later facsimile editions "
            "(1970s–1990s) are common and worth 5–10% of originals. Swiss and German "
            "institutional collectors (Kunsthaus Zurich, Sprengel Museum) create "
            "strong demand at Bern and Zurich auction — arbitrage by sourcing at "
            "underattended UK or US sales."
        ),
    },
    {
        "name_canonical": "Rodchenko, Alexander",
        "name_variants": [
            "Alexander Rodchenko",
            "Александр Родченко",
            "Aleksandr Rodchenko",
            "A. Rodchenko",
            "Aleksander Rodchenko",
            "Александр Михайлович Родченко",
        ],
        "nationality": "Soviet",
        "born": 1891,
        "died": 1956,
        "movement": "Constructivism, Russian Avant-Garde",
        "market_tier": "1",
        "specialty_category": "russian_futurist",
        "arbitrage_notes": (
            "Photography (original gelatin silver prints) and photomontage are the "
            "primary and most liquid market segment. Original photomontages vs. later "
            "prints or reproductions: check paper aging (foxing, yellowing), printing "
            "technique (silver content visible under magnification), and verso "
            "inscriptions. Book covers for LEF (1923–1925) and Novyi LEF (1927–1928) "
            "are most sought; distinguish original magazine copies from later facsimile "
            "publications by binding signatures and paper weight. Significant price gap "
            "between Moscow specialist sales (MacDougall's Russian Art) and London/New "
            "York: works with Russian private collection provenance often 30–50% cheaper "
            "in Moscow. Estate stamps and Varvara Stepanova (life partner) provenance "
            "are positive authentication signals."
        ),
    },
    {
        "name_canonical": "Goncharova, Natalia",
        "name_variants": [
            "Natalia Goncharova",
            "Наталья Гончарова",
            "Natalya Goncharova",
            "Nathalie Gontcharova",
            "Natalie Gontcharova",
            "Наталья Сергеевна Гончарова",
        ],
        "nationality": "Russian",
        "born": 1881,
        "died": 1962,
        "movement": "Russian Futurism, Rayonism, Russian Avant-Garde",
        "market_tier": "1",
        "specialty_category": "russian_futurist",
        "arbitrage_notes": (
            "Russian period (1900–1915) significantly more valuable than Paris émigré "
            "period (post-1915) — verify period before bidding. Ballet Russes design "
            "work (Diaghilev commissions for 'Le Coq d'Or', 1914; 'Les Noces', 1923) "
            "carries 40–60% premium due to institutional demand. Authentication caution: "
            "Goncharova is subject to active forgery — Sotheby's Russian Art Department "
            "holds extensive comparison records and should be consulted for any major "
            "purchase. Provenance from Kahnweiler Gallery (Paris) or Diaghilev collections "
            "is the strongest authentication signal. Paris sales (Drouot, Christie's "
            "Paris) often underestimate vs. London and New York due to buyer base "
            "differences — arbitrage between Paris and Anglo-American market."
        ),
    },
    {
        "name_canonical": "Popova, Liubov",
        "name_variants": [
            "Liubov Popova",
            "Любовь Попова",
            "Lubov Popova",
            "Lyubov Popova",
            "L. Popova",
            "Liubov Sergeevna Popova",
            "Любовь Сергеевна Попова",
        ],
        "nationality": "Russian",
        "born": 1889,
        "died": 1924,
        "movement": "Constructivism, Suprematism, Russian Avant-Garde",
        "market_tier": "1",
        "specialty_category": "russian_futurist",
        "arbitrage_notes": (
            "Died at 35 — output strictly capped at approximately 150 known works. "
            "Textile and theater designs (Meyerhold productions) are a separate and "
            "more accessible market segment from paintings. Significant authentication "
            "risk: forgery activity documented and flagged by Christie's Russian Art "
            "Department — any work requires expert vetting from the Russian Avant-Garde "
            "Research Project (RARP, London) before purchase above €10,000. "
            "Provenance from the George Costakis collection (acquired 1950s–1970s) is "
            "the gold standard for authentication. 'Space-Force Constructions' and "
            "Suprematist compositions are the highest-value works. Each authentic "
            "work represents a significant market event given scarcity."
        ),
    },
    {
        "name_canonical": "Stepanova, Varvara",
        "name_variants": [
            "Varvara Stepanova",
            "Варвара Степанова",
            "Barbara Stepanova",
            "V. Stepanova",
            "Varvara Fyodorovna Stepanova",
            "Варвара Фёдоровна Степанова",
        ],
        "nationality": "Soviet",
        "born": 1894,
        "died": 1958,
        "movement": "Constructivism, Russian Avant-Garde",
        "market_tier": "1",
        "specialty_category": "russian_futurist",
        "arbitrage_notes": (
            "Life partner of Alexander Rodchenko — joint provenance (works from the "
            "same collection or with Rodchenko estate stamps) adds authentication "
            "weight and may add 10–20% to price. Textile designs for Moscow State "
            "Textile Factory (1924–1925) are the most liquid market segment; original "
            "fabric samples vs. later reproductions must be verified by textile analysis. "
            "Book covers and propaganda posters: distinguish original lithograph "
            "(plate tone, hand-finished elements) from modern digital reproduction. "
            "Significantly underdeveloped buyer base in the Netherlands and Belgium — "
            "works appear at MacDougall's and Bonhams Russian sales without Dutch or "
            "Belgian specialist bidders, creating consistent 20–35% undervaluation "
            "relative to works placed with Continental European collectors directly."
        ),
    },
    {
        "name_canonical": "Rozanova, Olga",
        "name_variants": [
            "Olga Rozanova",
            "Ольга Розанова",
            "Olga Vladimirovna Rozanova",
            "O. Rozanova",
            "Ольга Владимировна Розанова",
        ],
        "nationality": "Russian",
        "born": 1886,
        "died": 1918,
        "movement": "Russian Futurism, Suprematism, Russian Avant-Garde",
        "market_tier": "1",
        "specialty_category": "russian_futurist",
        "arbitrage_notes": (
            "Died at 32 from diphtheria — one of the rarest outputs in the Russian "
            "Avant-Garde. Book illustrations for Futurist zaum publications are the "
            "primary accessible market segment: 'Troe' (1913), 'Transratsional'naya "
            "kniga' (1916–1917), and 'Voina' (War, 1916). Distinguish original "
            "lithographic book copies from later facsimile editions by foxing patterns, "
            "binding signatures, paper weight, and plate wear — facsimiles (1970s–2000s) "
            "are common and worth 1–5% of originals. Very few confirmed original works "
            "appear per decade at international auction; each sale typically establishes "
            "a new artist record. Any work attributed to Rozanova requires specialist "
            "vetting from RARP (Russian Avant-Garde Research Project) and comparison "
            "with Tretyakov Gallery holdings."
        ),
    },
]


# ---------------------------------------------------------------------------
# Import stats
# ---------------------------------------------------------------------------

@dataclass
class ImportStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0

    def total(self) -> int:
        return self.inserted + self.updated + self.skipped + self.errors

    def __str__(self) -> str:
        return (
            f"inserted={self.inserted} "
            f"updated={self.updated} "
            f"skipped={self.skipped} "
            f"errors={self.errors}"
        )


# ---------------------------------------------------------------------------
# Upsert logic
# ---------------------------------------------------------------------------

def _upsert(db, record: dict) -> str:
    """Insert or update one artist record. Returns 'inserted', 'updated', or 'skipped'."""
    existing = db.execute(
        select(Artist).where(Artist.name_canonical == record["name_canonical"])
    ).scalar_one_or_none()

    if existing is None:
        db.add(Artist(id=uuid.uuid4(), **record))
        return "inserted"

    dirty = False
    for col, value in record.items():
        if getattr(existing, col) != value:
            setattr(existing, col, value)
            dirty = True

    return "updated" if dirty else "skipped"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run() -> ImportStats:
    """Import or refresh all specialty artists. Safe to run repeatedly.

    Returns:
        ImportStats with inserted / updated / skipped / errors counts.
    """
    stats = ImportStats()

    with get_session() as db:
        for record in SPECIALTY_ARTISTS:
            name = record["name_canonical"]
            try:
                action = _upsert(db, record)
                log.info("[%-8s] %s", action.upper(), name)
                if action == "inserted":
                    stats.inserted += 1
                elif action == "updated":
                    stats.updated += 1
                else:
                    stats.skipped += 1
            except Exception as exc:
                log.error("[ERROR   ] %s — %s: %s", name, type(exc).__name__, exc)
                stats.errors += 1

    log.info(
        "Specialty import complete (%d artists) — %s",
        len(SPECIALTY_ARTISTS), stats,
    )
    return stats


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
        description="Import curated specialty artists into the artists table."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print records without writing to the database.",
    )
    args = parser.parse_args()

    if args.dry_run:
        print(f"{'NAME':<40} {'CAT':<30} {'NAT':<10} {'BORN':>4} {'DIED':>4}")
        print("-" * 95)
        for r in SPECIALTY_ARTISTS:
            print(
                f"{r['name_canonical']:<40} "
                f"{r['specialty_category']:<30} "
                f"{r['nationality']:<10} "
                f"{r.get('born') or '':>4} "
                f"{r.get('died') or '':>4}"
            )
        print(f"\n{len(SPECIALTY_ARTISTS)} artists — dry run, no DB writes.")
    else:
        stats = run()
        print(f"\nDone — {stats}")
