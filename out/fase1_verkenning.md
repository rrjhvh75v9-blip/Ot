# GDPR Damages Atlas – Fase 1: verkenning (DE, AT, NL)

Peildatum: 19-11-2026 · datumvenster: 04-05-2023 t/m 19-11-2026 · ruwe tellingen: `out/fase1_hits.json` · script: `python -m src.atlas.verkenning`

In deze fase heb ik alleen zoekresultaten en metadata opgevraagd: **geen volledige teksten, geen bulk**. Over alle bronnen samen waren dat ongeveer 150 verzoeken, telkens met minstens 1 seconde pauze (rechtspraak.nl: 0,5 s).

## 1. Samenvatting

| Land | Bron (automatisch) | Kandidaten na ontdubbeling* | Opmerking |
|---|---|---|---|
| NL | uitspraken.rechtspraak.nl (zoeken) + data.rechtspraak.nl (tekst) | **260** uitspraken (+5 conclusies A-G/PG, uitgesloten) | waarvan 43 Raad van State; CRvB zit erin |
| AT | RIS OGD API (Justiz) | **184** Entscheidungstexte (Rechtssätze uitgesloten) | OGH 82, OLG 101, LG 1 |
| DE | NeuRIS (Bund) | **67** (BGH 36, BAG 18, BFH 9, BVerwG 2, BSG 2) | |
| DE | Bayern (gesetze-bayern.de) | 126–159 per term | |
| DE | NRW (NRWE) | 135–202 per term | |
| DE | Open Legal Data (MCP-zoekfunctie) | 368 („Art. 82 DSGVO") tot 531 („Schadensersatz AND DSGVO") | aggregeert ook lagere gerechten uit BW, Berlin, Hessen, NDS enz. |
| DE | **na ontdubbeling (schatting)** | **≈ 450–600** | grote overlap OLDP ↔ NRW/Bayern/Bund; exacte unie volgt in fase 2 |

\* Kandidaat = treffer op minstens één zoekterm. Of art. 82 inhoudelijk beoordeeld is en of er een bedrag is toegekend, blijkt pas in fase 3.

**IJkzaken: alle drie gevonden.** BGH VI ZR 10/24 (18-11-2024, met daarnaast een Beschluss van 31-10-2024 onder hetzelfde zaaknummer), BGH VI ZR 183/22 (28-01-2025, in NeuRIS) en OGH 6 Ob 113/24x (03-07-2025, in RIS).

## 2. Per bron: toegang, robots.txt, voorwaarden, licentie

### NL
| | |
|---|---|
| Zoeken | `POST https://uitspraken.rechtspraak.nl/api/zoek`. Dit is de JSON-API achter de publieke zoekpagina. Ze ondersteunt full-text zoeken met een datumfilter (`UitspraakdatumRange`), maar is niet als open-data-API gedocumenteerd. |
| robots.txt | Alleen `/api/pdfdownload` is verboden. Zoeken is toegestaan. |
| Tekst | `https://data.rechtspraak.nl/uitspraken/content?id=<ECLI>` (officiële Open Data, XML) |
| Licentie | De uitspraken zijn openbaar en de open data is vrij herbruikbaar (CC0-achtig). In de README noteer ik dit als „CC0, verifiëren bij oplevering". |
| Alternatief zonder zoek-API | Het datafilter van data.rechtspraak.nl geeft 592.353 uitspraken in het venster (civiel 212.253, bestuur 284.354). Lokaal filteren betekent ongeveer 500k downloads (circa 14 uur bij 10/s). **Niet nodig**, want de zoek-API werkt. |
| Opmerking | Booleaanse operatoren werken onbetrouwbaar in deze API. Daarom draai ik elke term apart en neem ik zelf de unie. |

### AT
| | |
|---|---|
| Zoeken | `https://data.bka.gv.at/ris/api/v2.6/Judikatur?Applikation=Justiz&…` met `Dokumenttyp.SucheInEntscheidungstexten=true` en `SucheInRechtssaetzen=false` |
| robots.txt | `data.bka.gv.at` heeft `Disallow: /` voor alle crawlers, behalve DG_JUSTICE_CRAWLER. **Let op:** de RIS-OGD-API is wel juist bedoeld voor machinale toegang (Open Government Data, gedocumenteerd). Ik behandel de robots-regel als bedoeld voor crawlers, niet voor API-clients. **Beslispunt voor jou, zie §5.** |
| Tekst | `https://ogd.ris.bka.gv.at/Dokumente/Justiz/<ID>/<ID>.xml`. Host `ogd.ris.bka.gv.at` moet nog op de netwerk-allowlist. `www.ris.bka.gv.at` geeft een bot-check (503). |
| Licentie | RIS OGD: CC BY 4.0 (bronvermelding „RIS – Rechtsinformationssystem des Bundes") |
| Opmerking | Vrije zoektermen zonder aanhalingstekens („Art 82 DSGVO") matchen losse woorden. Daardoor is 184 een ruime bovengrens: met aanhalingstekens zijn het er 44. RIS publiceert vooral OGH- en OLG-beslissingen; LG/BG nauwelijks. |

### DE – Bund
| | |
|---|---|
| NeuRIS | `https://testphase.rechtsinformationen.bund.de/v1/rechtsprechung?searchTerm=…&dateFrom=…`. Het oude pad `/v1/case-law` geeft 410. Tekst via `/v1/rechtsprechung/<docnr>.xml`. |
| robots.txt | `testphase…`: `Disallow: /`. Ook dit is een officiële, publiek gedocumenteerde API. **Zelfde beslispunt als AT.** De productiehost `rechtsinformationen.bund.de` gaf een 502 (upstream). |
| BGH/BAG-sites | Overbodig naast NeuRIS (BGH en BAG staan volledig in NeuRIS). Die sites zijn niet gebruikt. |
| Licentie | Gerechtelijke beslissingen zijn amtliche Werke (§ 5 UrhG) en vrij. |

### DE – Länder
| Portaal | Toegang | robots.txt | Status |
|---|---|---|---|
| **NRW** (NRWE) | `POST https://nrwesuche.justiz.nrw.de/index.php` | geen robots.txt (404). justiz.nrw.de verbiedt alleen /core, /search/ e.d. NRWE vermeldt: „Abruf und Nutzung … kostenfrei. Auf die Art der Nutzung kommt es nicht an." | ✅ automatisch |
| **Bayern** | `POST https://www.gesetze-bayern.de/Search` (formulier + token) | `Allow: /` | ✅ automatisch |
| **BW** (landesrecht-bw.de, juris) | SPA | `User-agent: * Disallow: /` (alleen zoekmachines toegestaan) | ❌ **handmatig, niet haalbaar** |
| **Berlin-Brandenburg** (gesetze.berlin.de, juris) | – | verbinding geweigerd (reset) vanuit deze omgeving | ❌ handmatig. Zelfde juris-platform als BW, dus vermoedelijk hetzelfde verbod. |
| **Hamburg** (landesrecht-hamburg.de, juris) | – | verbinding geweigerd | ❌ handmatig |
| **Hessen** (lareda.hessenrecht.hessen.de, juris) | – | verbinding geweigerd | ❌ handmatig |
| **Niedersachsen** (voris.wolterskluwer-online.de) | – | `Disallow: /search/` | ❌ handmatig |
| **Open Legal Data** | MCP-endpoint `https://de.openlegaldata.io/mcp` (geen login nodig) | `Disallow: /api` en `/search`, met de expliciete tekst: „AI agents can use the MCP" en „download the data dumps on Hugging Face" | ✅ via MCP: `search_cases` (max. 50 per vraag, dus opknippen per maand) en `get_case` (volledige tekst) |
| OLDP-dump (Hugging Face) | `openlegaldata/court-decisions-germany`, dump 20-05-2026, 54 parquet-bestanden, **≈ 11 GB** | gated: HF-account, voorwaarden accepteren, token | alternatief. Mist uitspraken na mei 2026. |

**Exacte zoekopdrachten voor handmatige telling** (BW, Berlin, Hamburg, Hessen, NDS): zoekveld „Volltext", Dokumenttyp „Rechtsprechung", Datum 04.05.2023 – 19.11.2026, en dan achtereenvolgens:
`"Art. 82 DSGVO"` · `"Art. 82 DS-GVO"` · `"Art. 82 Abs. 1 DSGVO"` · `"immaterieller Schaden" DSGVO` · `Schadensersatz DSGVO` · `Schmerzensgeld DSGVO` · `Kontrollverlust DSGVO` · `Scraping DSGVO`.
Een groot deel van deze uitspraken komt via OLDP alsnog automatisch binnen. In fase 2 meet ik per deelstaat hoeveel OLDP dekt.

## 3. Zoekhits per zoekterm (venster 04-05-2023 – 19-11-2026)

**NL** (uitspraken.rechtspraak.nl)
| term | hits |
|---|---|
| "artikel 82 AVG" | 40 |
| "art. 82 AVG" | 9 |
| "artikel 82 lid 1 AVG" | 9 |
| "art. 82 lid 1 AVG" | 4 |
| "artikel 82 van de AVG" (extra variant) | 33 |
| "artikel 82" AVG | 107 |
| "immateriële schade" AVG | 221 |
| **unie** | **265 ECLI's → 260 uitspraken** |

„immateriële schade" zonder AVG geeft 21.343 hits: te breed, niet gebruikt.

**AT** (RIS, alleen Entscheidungstexte)
| term | Texte | (Rechtssätze, niet geteld) |
|---|---|---|
| Art 82 DSGVO / Art. 82 DSGVO | 91 | 12 |
| "Art 82 DSGVO" (frase) | 44 | 5 |
| immaterieller Schaden + DSGVO | 32 | 0 |
| Schadenersatz + DSGVO | 64 | 2 |
| § 29 DSG | 110 | 20 |
| Norm-veld DSGVO Art82 / DSG §29 | 0 | 4 / 3 |
| **unie** | **184** | |

**DE**
| term | NeuRIS | Bayern | NRW¹ | OLDP |
|---|---|---|---|---|
| "Art. 82 DSGVO" | 40 | 126 | 202 | 368 |
| "Art 82 DSGVO" | 40 | 126 | 202 | 368 |
| "Art. 82 DS-GVO" | 1 | 0 | 135 | 69 |
| "Art. 82 Abs. 1 DSGVO" | 37 | 117 | 202 | 358 |
| "Art. 82 Abs. 1 DS-GVO" | 5 | 0 | 135 | 56 |
| "immaterieller Schaden" DSGVO | 34 | 133 | 151 | 409 |
| Schadensersatz DSGVO | 40 | 159 | 162 | 531 |
| Schmerzensgeld DSGVO | 12 | 49 | 59 | 165 |
| Kontrollverlust DSGVO | 23 | 105 | 118 | 327 |
| Scraping DSGVO | 5 | 57 | 60 | 192 |
| unie (ids bekend) | 67 | – | – | – |

¹ NRWE zoekt met stemming, dus woordgroepen worden niet exact gematcht („Art. 82 Abs. 1 DSGVO" geeft hetzelfde als „Art. 82 DSGVO"). OLDP bevat ook EuGH-stukken; die vallen er in fase 2 uit.

## 4. Schatting downloadtijd en API-kosten (fase 2–3)

**Ophalen** (1 verzoek/s; rechtspraak.nl 2/s):

| bron | verzoeken | tijd |
|---|---|---|
| NL tekst | ≈ 260 | ≈ 3 min |
| AT tekst | ≈ 190 | ≈ 4 min |
| NeuRIS tekst | ≈ 70 | ≈ 2 min |
| Bayern zoeken + tekst | ≈ 30 + 160 | ≈ 4 min |
| NRW zoeken + tekst | ≈ 30 + 220 | ≈ 5 min |
| OLDP zoeken (per maand × term, ≈ 430) + get_case (≈ 500) | ≈ 930 × 1,5 s | ≈ 25 min |
| **totaal** | **≈ 1.900** | **≈ 45 min**, opslag < 200 MB |

**LLM-kosten.** Uitgangspunt: ≈ 1.000 kandidaten, waarvan na de regex ≈ 600 naar triage en ≈ 400 naar extractie. Tarieven volgens de Anthropic-prijslijst van 03-11-2026.

| stap | model | tokens | kosten |
|---|---|---|---|
| triage | Claude Haiku 5.5 ($0,10 / $0,50 per M) | ≈ 7M in, 0,1M uit | ≈ $0,75 |
| extractie | Claude Sonnet 5.5 ($2 / $10 per M) | ≈ 6M in, 0,4M uit | ≈ $16 |
| **totaal** | | | **≈ $17 (≈ €16)** |

Met de Batch API (–50%) wordt dat ≈ €8. Met Opus 5.5 in plaats van Sonnet voor de extractie ≈ €30. In alle varianten blijft het ruim onder €50. Bronnen kosten niets: alle bronnen zijn gratis.

## 5. Beslispunten voor jou vóór fase 2

1. **Contactadres voor de User-Agent.** Welk e-mailadres mag erin? Ik gebruik niets zonder jouw akkoord.
2. **robots.txt `Disallow: /` op de officiële API-hosts** van RIS (data.bka.gv.at) en NeuRIS (testphase.rechtsinformationen.bund.de). Dit zijn gedocumenteerde open-data-API's, bedoeld voor machinale toegang. Mijn voorstel: wel gebruiken, rustig (1/s), met contactadres in de UA. Als je strikt wilt zijn: AT telt dan alleen handmatig (zoekopdracht zoals in §3), en DE-Bund loopt dan via OLDP.
3. **Open Legal Data via MCP** (≈ 930 verzoeken à 1,5 s) of via de HF-dump (11 GB, HF-token nodig, stand mei 2026)? Mijn voorstel: MCP, want die is actueel en door hen zelf aangewezen voor agents.
4. **Anthropic API-sleutel** voor fase 3. In deze omgeving is er nog geen. Die moet ik straks van je krijgen.
5. **Netwerk**: voor fase 2 voeg ik nog `ogd.ris.bka.gv.at` toe (AT-teksten). Daar vraag ik je dan apart toestemming voor.

## 6. Voorlopige indicatie (niet gevalideerd)

Dit is een gok om de beslissing te kaderen, geen meting. Uit de snippets blijkt dat veel Duitse scraping-zaken van vóór BGH VI ZR 10/24 zijn afgewezen (geen Kontrollverlust bewezen), en dat er daarna vaker kleine bedragen (€100–500) worden toegekend. Als 25–40% van de DE-kandidaten haalbaar is, en 10–20% van NL en AT, dan uitkomst **≈ 150–270**. Dat ligt rond de drempel van 150, dus een **grensgeval is goed mogelijk**. Fase 2–3 moet dit beslechten.
