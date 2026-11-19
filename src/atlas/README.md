# GDPR Damages Atlas – ingestiemodule

Kill-test: telt art. 82 AVG/DSGVO-uitspraken met een toegekend schadebedrag in DE, AT en NL.
Deze code wordt later de ingestiemodule van het product.

## Structuur
- `src/atlas/` – code (`verkenning.py` = fase 1; fases 2–5 volgen)
- `data/` – lokale cache van ruwe teksten (**staat in .gitignore; niet publiceren of herverdelen**)
- `out/` – resultaten (`fase1_hits.json`, `fase1_verkenning.md`; later `uitspraken.csv`, `review_steekproef.csv`, `rapport.md`)

## Opnieuw draaien

Vereist: Python ≥ 3.10, alleen de standaardbibliotheek (fase 1).

Netwerk: deze hosts moeten bereikbaar zijn:
uitspraken.rechtspraak.nl, data.rechtspraak.nl, data.bka.gv.at, ogd.ris.bka.gv.at,
testphase.rechtsinformationen.bund.de, www.gesetze-bayern.de, nrwesuche.justiz.nrw.de, de.openlegaldata.io

```bash
export ATLAS_USER_AGENT="GDPRDamagesAtlas/0.1 (+mailto:<contactadres>)"
python -m src.atlas.verkenning --van 2023-05-04 --tot 2026-11-19 --out out/fase1_hits.json
# eventueel per land:  --landen NL   of   --landen AT,DE
```

Looptijd: ≈ 5 minuten, ≈ 150 verzoeken. Rate-limit: 1 verzoek/s per portaal, 1,5 s bij OLDP, 0,5 s bij rechtspraak.nl.

## Randvoorwaarden (vast)
- Geen namen van rechters en geen natuurlijke personen opslaan. Alleen rechtspersonen als partij.
- Ruwe teksten blijven lokaal in `data/`.
- Respecteer robots.txt en gebruiksvoorwaarden. Portalen die automatische toegang verbieden worden niet gescrapet, maar handmatig geteld (zie `out/fase1_verkenning.md` §2).
