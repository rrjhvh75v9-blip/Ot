"""Fase 2 – kandidaten verzamelen, ontdubbelen en volledige teksten ophalen (met cache en herkomstlog).

Gebruik:
    export ATLAS_CONTACT=...            # verplicht: contactadres voor de User-Agent
    python -m src.atlas.ophalen [--van 2023-05-04] [--tot JJJJ-MM-DD] [--bronnen nl,at,neuris,nrw,bayern,oldp]

Stappen per bron:
  1. zoeken  → data/kandidaten/<bron>.jsonl   (metadata per treffer, met de zoektermen die raakten)
  2. ontdubbelen over bronnen (ECLI, anders Aktenzeichen + datum); officiële bronnen gaan voor OLDP
  3. tekst   → data/raw/<bron>/<id>.<ext>     (cache: bestaat het bestand al, dan geen nieuw verzoek)
  4. herkomst → data/herkomst.jsonl           (bron, url, ophaaldatum, licentie, toegangsweg, sha256)

Resultaat: data/corpus.jsonl (één regel per ontdubbelde uitspraak, met pad naar de lokale tekst).
"""
from __future__ import annotations

import argparse
import calendar
import collections
import datetime as dt
import hashlib
import html
import json
import re
import urllib.parse
from pathlib import Path

from .http import Http
from .verkenning import TERMEN

DATA = Path("data")

# ------------------------------------------------------------------ bronbeschrijving (herkomstlog)
BRONNEN = {
    "nl": dict(land="NL", pauze=0.5,
               licentie="CC0 1.0 – Open Data van de Rechtspraak",
               toegangsweg="Tekst: gedocumenteerde open-data-API data.rechtspraak.nl. Zoeken: publieke zoekfunctie "
                           "uitspraken.rechtspraak.nl/api/zoek (robots.txt staat dit toe)."),
    "at": dict(land="AT", pauze=1.0,
               licentie="CC BY 4.0 – RIS, Rechtsinformationssystem des Bundes (Bundeskanzleramt)",
               toegangsweg="Gedocumenteerde open-data-API RIS OGD v2.6 (data.bka.gv.at, ogd.ris.bka.gv.at). "
                           "robots.txt heeft Disallow: / voor crawlers; met akkoord van de opdrachtgever gebruikt als "
                           "officiële API, max. 1 verzoek/s."),
    "neuris": dict(land="DE", pauze=1.0,
                   licentie="Amtliches Werk, gemeinfrei (§ 5 UrhG) – Rechtsinformationen des Bundes (NeuRIS)",
                   toegangsweg="Gedocumenteerde open-data-API NeuRIS (testphase.rechtsinformationen.bund.de/v1). "
                               "robots.txt heeft Disallow: / voor crawlers; met akkoord van de opdrachtgever gebruikt "
                               "als officiële API, max. 1 verzoek/s."),
    "nrw": dict(land="DE", pauze=1.0,
                licentie="Amtliches Werk, gemeinfrei (§ 5 UrhG) – NRWE: ‘Abruf und Nutzung kostenfrei, Art der Nutzung "
                         "unerheblich’",
                toegangsweg="Publieke zoekfunctie nrwesuche.justiz.nrw.de (geen robots.txt) en tekst op "
                            "nrwe.justiz.nrw.de (geen robots.txt)."),
    "bayern": dict(land="DE", pauze=1.0,
                   licentie="Entscheidungstext: amtliches Werk (§ 5 UrhG). Redaktionelle Leitsätze (Beck/GRUR) mogelijk "
                            "auteursrechtelijk beschermd: niet hergebruiken.",
                   toegangsweg="Publieke zoekfunctie gesetze-bayern.de (robots.txt: Allow: /)."),
    "oldp": dict(land="DE", pauze=1.5,
                 licentie="Entscheidungstext: amtliches Werk (§ 5 UrhG). Databank Open Legal Data: open data "
                          "(licentievoorwaarden OLDP vóór hergebruik verifiëren).",
                 toegangsweg="OLDP MCP-endpoint de.openlegaldata.io/mcp (search_cases/get_case). robots.txt verbiedt "
                             "/api en /search en wijst AI-agents expliciet naar dit MCP-endpoint."),
}
# volgorde bij ontdubbelen: eerste wint
PRIORITEIT = ["nl", "at", "neuris", "nrw", "bayern", "oldp"]
NIET_DUITS = {"EuGH", "EuG", "EGMR", "EUGH"}


def nu() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def veilig(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:150]


def az_norm(az: str | None) -> str:
    return re.sub(r"[\s.\-–]", "", (az or "").lower())


def isodatum(s: str) -> str:
    s = s.strip()
    if re.match(r"\d{2}[.-]\d{2}[.-]\d{4}$", s):
        return f"{s[6:10]}-{s[3:5]}-{s[0:2]}"
    return s[:10]


# ------------------------------------------------------------------ zoeken per bron
def zoek_nl(http: Http, van, tot):
    from .verkenning import nl_zoek
    uit = {}
    for t in TERMEN["NL"]:
        for x in nl_zoek(http, t, van, tot):
            if x.get("GerechtelijkProductType") != "uitspraak":
                continue  # conclusies A-G/PG zijn geen rechterlijke beslissing
            ecli = x["TitelEmphasis"]
            delen = [p.strip() for p in x["Titel"].split(",")]
            k = uit.setdefault(ecli, dict(id=ecli, ecli=ecli, gericht=delen[0], datum=isodatum(x["Uitspraakdatum"]),
                                          az=", ".join(delen[2:]) or None,
                                          procedure=";".join(x.get("Proceduresoorten") or []),
                                          rechtsgebied=";".join(x.get("Rechtsgebieden") or []),
                                          tekst_url=f"https://data.rechtspraak.nl/uitspraken/content?id={ecli}",
                                          ext="xml", termen=[]))
            k["termen"].append(t)
    return list(uit.values())


def zoek_at(http: Http, van, tot):
    B = "https://data.bka.gv.at/ris/api/v2.6/Judikatur"
    uit = {}
    for veld, waarde in TERMEN["AT"]:
        pg, gezien, n = 0, 0, None
        while n is None or gezien < n:
            pg += 1
            p = {"Applikation": "Justiz", "EntscheidungsdatumVon": van, "EntscheidungsdatumBis": tot,
                 "DokumenteProSeite": "OneHundred", "Seitennummer": str(pg), veld: waarde,
                 "Dokumenttyp.SucheInEntscheidungstexten": "true", "Dokumenttyp.SucheInRechtssaetzen": "false"}
            r = http.json(B + "?" + urllib.parse.urlencode(p))["OgdSearchResult"]
            res = r.get("OgdDocumentResults")
            if not res:
                break
            n = int(res["Hits"]["#text"])
            refs = res.get("OgdDocumentReference", [])
            refs = [refs] if isinstance(refs, dict) else refs
            if not refs:
                break
            gezien += len(refs)
            for x in refs:
                m = x["Data"]["Metadaten"]
                if m["Judikatur"]["Dokumenttyp"] != "Text":
                    continue  # alleen Entscheidungstexte, geen Rechtssätze
                urls = x["Data"]["Dokumentliste"]["ContentReference"]
                urls = urls[0] if isinstance(urls, list) else urls
                xml = [u["Url"] for u in urls["Urls"]["ContentUrl"] if u["DataType"] == "Xml"][0]
                gz = m["Judikatur"].get("Geschaeftszahl", {}).get("item")
                gz = gz if isinstance(gz, str) else "; ".join(gz or [])
                k = uit.setdefault(m["Technisch"]["ID"], dict(
                    id=m["Technisch"]["ID"], ecli=m["Judikatur"].get("EuropeanCaseLawIdentifier"),
                    gericht=m["Technisch"]["Organ"], az=gz, datum=m["Judikatur"].get("Entscheidungsdatum"),
                    tekst_url=xml, ext="xml", termen=[]))
                k["termen"].append(f"{veld}={waarde}")
    return list(uit.values())


def zoek_neuris(http: Http, van, tot):
    B = "https://testphase.rechtsinformationen.bund.de/v1/rechtsprechung"
    uit = {}
    for t in TERMEN["DE"]:
        p, n = 0, None
        while n is None or p * 100 < n:
            d = http.json(B + "?" + urllib.parse.urlencode(
                {"searchTerm": t, "size": 100, "pageIndex": p, "dateFrom": van, "dateTo": tot}))
            n = d["totalItems"]
            p += 1
            for m in d["member"]:
                it = m["item"]
                k = uit.setdefault(it["documentNumber"], dict(
                    id=it["documentNumber"], ecli=it.get("ecli"), gericht=it["courtName"],
                    az=(it.get("fileNumbers") or [None])[0], datum=it["decisionDate"], art=it.get("documentType"),
                    tekst_url=f"{B}/{it['documentNumber']}.xml", ext="xml", termen=[]))
                k["termen"].append(t)
            if not d["member"]:
                break
    return list(uit.values())


def zoek_nrw(http: Http, van, tot):
    d0, d1 = (dt.date.fromisoformat(x).strftime("%d.%m.%Y") for x in (van, tot))
    uit = {}
    for t in TERMEN["DE"]:
        data = urllib.parse.urlencode({"q": t, "method": "stem", "von": d0, "bis": d1, "date": "", "gerichtstyp": "",
                                       "gerichtsbarkeit": "", "gerichtsort": "", "entscheidungsart": "",
                                       "aktenzeichen": "", "schlagwoerter": "", "qSize": "1000", "sortieren_nach": "",
                                       "absenden": "Suchen", "validFrom": "", "von2": "", "bis2": "",
                                       "advanced_search": ""}).encode()
        h = http.get("https://nrwesuche.justiz.nrw.de/index.php", data=data).decode("utf-8", "replace")
        totaal = int((re.search(r"Es wurden <strong>(\d+)</strong>", h) or [0, 0])[1])
        blokken = re.findall(r"<div class='einErgebnis'>(.*?)</div>", h, flags=re.S)
        if len(blokken) < totaal:
            print(f"  ! NRW '{t}': {len(blokken)} van {totaal} op de pagina")
        for b in blokken:
            url = re.search(r"href='([^']+)'", b)[1]
            veld = lambda naam: (re.search(naam + r":\s*(.*?)\s*<br", b) or [None, None])[1]
            ecli = (re.search(r"(ECLI:[A-Z]{2}:[^<\s]+)", b) or [None, None])[1]
            k = uit.setdefault(url, dict(id=ecli or url, ecli=ecli, gericht=html.unescape(veld("Gericht") or ""),
                                         az=veld("Aktenzeichen"), datum=isodatum(veld("Entscheidungsdatum") or ""),
                                         art=veld("Entscheidungsart"), tekst_url=url, ext="html", termen=[]))
            k["termen"].append(t)
    return list(uit.values())


def zoek_bayern(http: Http, van, tot):
    d0, d1 = (dt.date.fromisoformat(x).strftime("%d.%m.%Y") for x in (van, tot))
    uit = {}
    for t in TERMEN["DE"]:
        h = http.get("https://www.gesetze-bayern.de/Search/Filter/DOKTYP/rspr").decode()
        tok = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', h)[1]
        data = urllib.parse.urlencode({"SearchFields.Content": t, "SearchFields.Norm": "",
                                       "SearchFields.Aktenzeichen": "", "SearchFields.DatumVon": d0,
                                       "SearchFields.DatumBis": d1, "__RequestVerificationToken": tok}).encode()
        h = http.get("https://www.gesetze-bayern.de/Search", data=data).decode()
        totaal = int(((re.search(r"([\d.]+) Treffer", h) or [0, "0"])[1]).replace(".", ""))
        pagina, gezien = 1, 0
        while True:
            items = re.findall(r'<li class="hitlistItem">(.*?)</li>', h, flags=re.S)
            for it in items:
                docid = re.search(r'/Content/Document/(Y-[^"?]+)', it)[1]
                titel = html.unescape(re.sub(r"<[^>]+>", "", re.search(r'class="hltitel">(.*?)</p>', it, re.S)[1])).strip()
                sub = html.unescape(re.sub(r"\s+", " ", re.search(r'class="hlSubTitel">(.*?)</p>', it, re.S)[1])).strip()
                m = re.match(r"(.*?) vom (\d{2}\.\d{2}\.\d{4}) [–-] (.*)", sub)
                k = uit.setdefault(docid, dict(
                    id=docid, ecli=None, gericht=titel.split(":")[0].strip(),
                    art=m[1] if m else None, datum=isodatum(m[2]) if m else None, az=m[3].strip() if m else None,
                    tekst_url=f"https://www.gesetze-bayern.de/Content/Document/{docid}", ext="html", termen=[]))
                k["termen"].append(t)
            gezien += len(items)
            if not items or gezien >= totaal:
                break
            pagina += 1
            h = http.get(f"https://www.gesetze-bayern.de/Search/Page/{pagina}").decode()
    return list(uit.values())


def maanden(van, tot):
    d = dt.date.fromisoformat(van).replace(day=1)
    eind = dt.date.fromisoformat(tot)
    while d <= eind:
        laatste = d.replace(day=calendar.monthrange(d.year, d.month)[1])
        yield max(d, dt.date.fromisoformat(van)), min(laatste, eind)
        d = laatste + dt.timedelta(days=1)


class Oldp:
    def __init__(self, http: Http):
        self.http = http

    def tool(self, name, **args):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        d = self.http.json("https://de.openlegaldata.io/mcp", data=json.dumps(body).encode(),
                           headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        if "error" in d:
            raise RuntimeError(d["error"])
        return json.loads(d["result"]["content"][0]["text"])

    def zoek_venster(self, q, a: dt.date, b: dt.date, uit, t):
        r = self.tool("search_cases", query=q, start_date=a.isoformat(), end_date=b.isoformat(), limit=50)
        if r["total"] > len(r["results"]) and a < b:  # meer dan 50: venster halveren
            m = a + (b - a) // 2
            self.zoek_venster(q, a, m, uit, t)
            self.zoek_venster(q, m + dt.timedelta(days=1), b, uit, t)
            return
        if r["total"] > len(r["results"]):
            print(f"  ! OLDP '{q}' {a}: {len(r['results'])} van {r['total']} (één dag, niet verder te splitsen)")
        for x in r["results"]:
            if x["court"] in NIET_DUITS:
                continue
            k = uit.setdefault(str(x["id"]), dict(id=str(x["id"]), ecli=None, gericht=x["court"], az=x.get("file_number"),
                                                  datum=x["date"], art=x.get("decision_type"),
                                                  tekst_url=f"oldp:get_case:{x['id']}", ext="json", termen=[]))
            if t not in k["termen"]:
                k["termen"].append(t)


def zoek_oldp(http: Http, van, tot):
    o, uit = Oldp(http), {}
    gezien_q = set()
    for t in TERMEN["DE"]:
        q = " AND ".join(re.findall(r'"[^"]+"|\S+', t))
        if q.replace(".", "") in gezien_q:  # "Art 82" en "Art. 82" geven in Elasticsearch dezelfde tokens
            continue
        gezien_q.add(q.replace(".", ""))
        for a, b in maanden(van, tot):
            o.zoek_venster(q, a, b, uit, t)
        print(f"  OLDP '{t}': cumulatief {len(uit)}")
    return list(uit.values())


ZOEK = {"nl": zoek_nl, "at": zoek_at, "neuris": zoek_neuris, "nrw": zoek_nrw, "bayern": zoek_bayern, "oldp": zoek_oldp}


# ------------------------------------------------------------------ ontdubbelen
def sleutel(k) -> str:
    if k.get("ecli"):
        return k["ecli"].upper()
    return f"{az_norm(k.get('az'))}|{k.get('datum')}"


def ontdubbel(kandidaten: dict[str, list]):
    """Eén record per beslissing. ECLI waar bekend; anders Aktenzeichen + datum (gerecht kan per portaal anders heten)."""
    corpus, index_az = {}, {}
    for bron in PRIORITEIT:
        for k in kandidaten.get(bron, []):
            s = sleutel(k)
            s_az = f"{az_norm(k.get('az'))}|{k.get('datum')}" if k.get("az") else None
            doel = corpus.get(s) or (corpus.get(index_az[s_az]) if s_az in index_az else None)
            if doel:
                doel["ook_in"].append({"bron": bron, "id": k["id"], "url": k["tekst_url"]})
                doel["termen"] = sorted(set(doel["termen"]) | set(k["termen"]))
                continue
            rec = dict(k, bron=bron, land=BRONNEN[bron]["land"], ook_in=[], termen=sorted(set(k["termen"])))
            corpus[s] = rec
            if s_az:
                index_az.setdefault(s_az, s)
    return list(corpus.values())


# ------------------------------------------------------------------ tekst ophalen
def haal_tekst(rec, https: dict, herkomst):
    bron = rec["bron"]
    pad = DATA / "raw" / bron / f"{veilig(rec['id'])}.{rec['ext']}"
    rec["tekst_pad"] = str(pad)
    if pad.exists() and pad.stat().st_size > 0:
        return False
    pad.parent.mkdir(parents=True, exist_ok=True)
    if bron == "oldp":
        o = Oldp(https["oldp"])
        r = o.tool("get_case", case_id=int(rec["id"]))
        rec["ecli"] = rec.get("ecli") or r.get("ecli")
        inhoud = json.dumps({k: r.get(k) for k in ("id", "slug", "file_number", "date", "type", "ecli", "court", "content")},
                            ensure_ascii=False).encode()
        url = f"https://de.openlegaldata.io/case/{r.get('slug')}"
    else:
        inhoud = https[bron].get(rec["tekst_url"])
        url = rec["tekst_url"]
    pad.write_bytes(inhoud)
    herkomst.write(json.dumps({
        "id": rec["id"], "land": rec["land"], "bron": bron, "url": url, "lokaal": str(pad), "opgehaald": nu(),
        "licentie": BRONNEN[bron]["licentie"], "toegangsweg": BRONNEN[bron]["toegangsweg"],
        "sha256": hashlib.sha256(inhoud).hexdigest(), "bytes": len(inhoud)}, ensure_ascii=False) + "\n")
    herkomst.flush()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--van", default="2023-05-04")
    ap.add_argument("--tot", default=dt.date.today().isoformat())
    ap.add_argument("--bronnen", default=",".join(PRIORITEIT))
    ap.add_argument("--opnieuw-zoeken", action="store_true", help="kandidatenlijsten opnieuw opbouwen")
    a = ap.parse_args()
    bronnen = a.bronnen.split(",")
    https = {b: Http(BRONNEN[b]["pauze"]) for b in PRIORITEIT}
    print("User-Agent:", https["nl"].ua.split(";")[0] + "; contact: <ATLAS_CONTACT>)")

    kdir = DATA / "kandidaten"
    kdir.mkdir(parents=True, exist_ok=True)
    kandidaten = {}
    for b in PRIORITEIT:
        f = kdir / f"{b}.jsonl"
        if b in bronnen and (a.opnieuw_zoeken or not f.exists()):
            print(f"[{b}] zoeken…", flush=True)
            res = ZOEK[b](https[b], a.van, a.tot)
            f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in res))
        if f.exists():
            kandidaten[b] = [json.loads(l) for l in f.open()]
            print(f"[{b}] {len(kandidaten[b])} kandidaten")

    corpus = ontdubbel(kandidaten)
    tel = collections.Counter((r["land"], r["bron"]) for r in corpus)
    print("na ontdubbeling:", dict(tel), "totaal", len(corpus))

    nieuw = 0
    with (DATA / "herkomst.jsonl").open("a") as herkomst:
        for i, rec in enumerate(corpus, 1):
            if rec["bron"] not in bronnen:
                continue
            try:
                nieuw += haal_tekst(rec, https, herkomst)
            except Exception as e:  # noqa: BLE001 – één mislukt document mag de run niet stoppen
                rec["fout"] = f"{type(e).__name__}: {e}"[:300]
                print(f"  ! {rec['bron']} {rec['id']}: {rec['fout']}")
            if i % 50 == 0:
                print(f"  {i}/{len(corpus)} (nieuw opgehaald: {nieuw})", flush=True)
    (DATA / "corpus.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in corpus))
    print(f"klaar: {len(corpus)} uitspraken, {nieuw} nieuw opgehaald, "
          f"verzoeken: { {b: h.n for b, h in https.items()} }")


if __name__ == "__main__":
    main()
