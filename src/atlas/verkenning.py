"""Fase 1 – verkenning: telt zoekhits per bron en zoekterm, zonder bulk-download.

Gebruik:
    python -m src.atlas.verkenning [--van 2023-05-04] [--tot 2026-11-19] [--out out/fase1_hits.json]

Per bron worden alleen zoekresultaten/metadata opgevraagd (geen volledige teksten).
Rate-limits: portals 1 verzoek/s (OLDP 1,5 s), rechtspraak.nl ruim onder 10/s.
De User-Agent komt uit de omgevingsvariabele ATLAS_USER_AGENT (in fase 2 met contactadres).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import http.cookiejar
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = os.environ.get("ATLAS_USER_AGENT", "GDPRDamagesAtlas-feasibility/0.1 (research; exploration phase)")

TERMEN = {
    "NL": ['"artikel 82 AVG"', '"art. 82 AVG"', '"artikel 82 lid 1 AVG"', '"art. 82 lid 1 AVG"',
           '"artikel 82 van de AVG"', '"artikel 82" AVG', '"immateriële schade" AVG'],
    "AT": [("Suchworte", "Art 82 DSGVO"), ("Suchworte", '"Art 82 DSGVO"'), ("Suchworte", "Art. 82 DSGVO"),
           ("Norm", "DSGVO Art82"), ("Suchworte", "DSGVO immaterieller Schaden"),
           ("Suchworte", "DSGVO Schadenersatz"), ("Suchworte", "§ 29 DSG"), ("Norm", "DSG §29")],
    "DE": ['"Art. 82 DSGVO"', '"Art 82 DSGVO"', '"Art. 82 DS-GVO"', '"Art. 82 Abs. 1 DSGVO"',
           '"Art. 82 Abs. 1 DS-GVO"', '"immaterieller Schaden" DSGVO', 'Schadensersatz DSGVO',
           'Schmerzensgeld DSGVO', 'Kontrollverlust DSGVO', 'Scraping DSGVO'],
}


class Http:
    """Kleine HTTP-client met vaste pauze tussen verzoeken en cookie-ondersteuning."""

    def __init__(self, pauze: float):
        self.pauze = pauze
        self._laatste = 0.0
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.n = 0

    def _wacht(self):
        delta = time.monotonic() - self._laatste
        if delta < self.pauze:
            time.sleep(self.pauze - delta)
        self._laatste = time.monotonic()

    def get(self, url: str, data: bytes | None = None, headers: dict | None = None, timeout=90) -> bytes:
        self._wacht()
        h = {"User-Agent": UA, **(headers or {})}
        self.n += 1
        return self.opener.open(urllib.request.Request(url, data=data, headers=h), timeout=timeout).read()

    def json(self, *a, **k):
        return json.loads(self.get(*a, **k))


# ---------------------------------------------------------------- NL
def nl_zoek(http: Http, term: str, van: str, tot: str) -> list[dict]:
    """uitspraken.rechtspraak.nl zoek-API (robots.txt staat dit toe; alleen /api/pdfdownload is verboden)."""
    d0, d1 = (dt.date.fromisoformat(x).strftime("%d-%m-%Y") for x in (van, tot))
    uit, start = [], 0
    while True:
        body = {"StartRow": start, "PageSize": 50, "ShouldReturnHighlights": False, "ShouldCountFacets": False,
                "SortOrder": "Relevance", "SearchTerms": [{"Term": term, "Field": "AlleVelden"}],
                "Contentsoorten": [], "Rechtsgebieden": [], "Instanties": [], "DatumPublicatie": [],
                "DatumUitspraak": [], "Proceduresoorten": [], "CorrelationId": "atlas",
                "Advanced": {"PublicatieStatus": "AlleenGepubliceerd", "UitspraakdatumRange": {"From": d0, "To": d1}}}
        d = http.json("https://uitspraken.rechtspraak.nl/api/zoek", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
        uit += d["Results"]
        start += 50
        if start >= d["ResultCount"] or not d["Results"]:
            return uit


def nl(van, tot):
    http = Http(0.5)
    per_term, U = {}, {}
    for t in TERMEN["NL"]:
        res = nl_zoek(http, t, van, tot)
        per_term[t] = len(res)
        for x in res:
            U[x["TitelEmphasis"]] = {"titel": x["Titel"], "type": x.get("GerechtelijkProductType")}
    uitspraken = {k: v for k, v in U.items() if v["type"] == "uitspraak"}
    gerechten = collections.Counter(v["titel"].split(",")[0] for v in uitspraken.values())
    return {"bron": "uitspraken.rechtspraak.nl (zoek-API) + data.rechtspraak.nl (tekst, CC0)",
            "per_term": per_term, "unie_ecli": len(U), "unie_uitspraken": len(uitspraken),
            "conclusies_uitgesloten": len(U) - len(uitspraken), "per_gerecht": gerechten.most_common(),
            "ids": sorted(uitspraken), "verzoeken": http.n}


# ---------------------------------------------------------------- AT
def at(van, tot):
    http = Http(1.0)
    B = "https://data.bka.gv.at/ris/api/v2.6/Judikatur"
    per_term, U = {}, {}
    for veld, waarde in TERMEN["AT"]:
        docs, pg, n = [], 0, None
        while n is None or (len(docs) < n and pg < 20):
            pg += 1
            p = {"Applikation": "Justiz", "EntscheidungsdatumVon": van, "EntscheidungsdatumBis": tot,
                 "DokumenteProSeite": "OneHundred", "Seitennummer": str(pg), veld: waarde,
                 "Dokumenttyp.SucheInEntscheidungstexten": "true", "Dokumenttyp.SucheInRechtssaetzen": "false"}
            r = http.json(B + "?" + urllib.parse.urlencode(p))["OgdSearchResult"]
            if "OgdDocumentResults" not in r:
                n = 0
                break
            res = r["OgdDocumentResults"]
            n = int(res["Hits"]["#text"])
            refs = res.get("OgdDocumentReference", [])
            refs = [refs] if isinstance(refs, dict) else refs
            if not refs:
                break
            docs += refs
        typen = collections.Counter()
        for x in docs:
            m = x["Data"]["Metadaten"]
            typen[m["Judikatur"]["Dokumenttyp"]] += 1
            if m["Judikatur"]["Dokumenttyp"] == "Text":  # alleen Entscheidungstexte, geen Rechtssätze
                U[m["Technisch"]["ID"]] = {"gericht": m["Technisch"]["Organ"],
                                           "gz": m["Judikatur"].get("Geschaeftszahl", {}).get("item"),
                                           "datum": m["Judikatur"].get("Entscheidungsdatum")}
        per_term[f"{veld}={waarde}"] = dict(typen)
    return {"bron": "RIS OGD API v2.6, Applikation Justiz", "per_term": per_term,
            "unie_entscheidungstexte": len(U),
            "per_gericht": collections.Counter(v["gericht"] for v in U.values()).most_common(),
            "ids": sorted(U), "verzoeken": http.n}


# ---------------------------------------------------------------- DE
def de_neuris(http, van, tot):
    B = "https://testphase.rechtsinformationen.bund.de/v1/rechtsprechung"
    per_term, U = {}, {}
    for t in TERMEN["DE"]:
        items, p, n = [], 0, None
        while n is None or (len(items) < n and p < 30):
            d = http.json(B + "?" + urllib.parse.urlencode(
                {"searchTerm": t, "size": 100, "pageIndex": p, "dateFrom": van, "dateTo": tot}))
            n = d["totalItems"]
            if not d["member"]:
                break
            items += d["member"]
            p += 1
        per_term[t] = n
        for m in items:
            it = m["item"]
            U[it["documentNumber"]] = (it["courtName"], (it.get("fileNumbers") or [""])[0], it["decisionDate"])
    return per_term, U


def de_bayern(http, van, tot):
    d0, d1 = (dt.date.fromisoformat(x).strftime("%d.%m.%Y") for x in (van, tot))
    per_term = {}
    for t in TERMEN["DE"]:
        h = http.get("https://www.gesetze-bayern.de/Search/Filter/DOKTYP/rspr").decode()
        tok = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', h).group(1)
        data = urllib.parse.urlencode({"SearchFields.Content": t, "SearchFields.Norm": "", "SearchFields.Aktenzeichen": "",
                                       "SearchFields.DatumVon": d0, "SearchFields.DatumBis": d1,
                                       "__RequestVerificationToken": tok}).encode()
        h = http.get("https://www.gesetze-bayern.de/Search", data=data).decode()
        m = re.search(r"([\d.]+) Treffer", h)
        per_term[t] = int(m.group(1).replace(".", "")) if m else 0
    return per_term


def de_nrw(http, van, tot):
    d0, d1 = (dt.date.fromisoformat(x).strftime("%d.%m.%Y") for x in (van, tot))
    per_term = {}
    for t in TERMEN["DE"]:
        data = urllib.parse.urlencode({"q": t, "method": "stem", "von": d0, "bis": d1, "date": "", "gerichtstyp": "",
                                       "gerichtsbarkeit": "", "gerichtsort": "", "entscheidungsart": "", "aktenzeichen": "",
                                       "schlagwoerter": "", "qSize": "10", "sortieren_nach": "", "absenden": "Suchen",
                                       "validFrom": "", "von2": "", "bis2": "", "advanced_search": ""}).encode()
        h = http.get("https://nrwesuche.justiz.nrw.de/index.php", data=data).decode("utf-8", "replace")
        h = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
        m = re.search(r"Es wurden ([\d.]+) Dokumente", h)
        per_term[t] = int(m.group(1).replace(".", "")) if m else 0
    return per_term


def de_oldp(http, van, tot):
    """Open Legal Data via hun publieke MCP-endpoint (robots.txt verwijst agents daarheen; /api is verboden)."""
    def tool(name, **args):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        d = http.json("https://de.openlegaldata.io/mcp", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        return json.loads(d["result"]["content"][0]["text"])
    per_term = {}
    for t in TERMEN["DE"]:
        q = " AND ".join(re.findall(r'"[^"]+"|\S+', t))  # Lucene: losse delen expliciet met AND
        per_term[t] =tool("search_cases", query=q, start_date=van, end_date=tot, limit=1)["total"]
    return per_term


def de(van, tot):
    out = {}
    h = Http(1.0)
    pt, U = de_neuris(h, van, tot)
    out["neuris"] = {"per_term": pt, "unie": len(U), "per_gericht": collections.Counter(v[0] for v in U.values()).most_common(),
                     "ids": sorted(U), "verzoeken": h.n}
    h = Http(1.0)
    out["bayern"] = {"per_term": de_bayern(h, van, tot), "verzoeken": h.n}
    h = Http(1.0)
    out["nrw"] = {"per_term": de_nrw(h, van, tot), "verzoeken": h.n}
    h = Http(1.5)
    out["oldp"] = {"per_term": de_oldp(h, van, tot), "verzoeken": h.n}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--van", default="2023-05-04")
    ap.add_argument("--tot", default=dt.date.today().isoformat())
    ap.add_argument("--out", default="out/fase1_hits.json")
    ap.add_argument("--landen", default="NL,AT,DE")
    a = ap.parse_args()
    res = {"van": a.van, "tot": a.tot, "opgehaald": dt.datetime.now().isoformat(timespec="seconds"), "user_agent": UA}
    for land in a.landen.split(","):
        print(f"[{land}] tellen…", flush=True)
        res[land] = {"NL": nl, "AT": at, "DE": de}[land](a.van, a.tot)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(f"→ {a.out}")


if __name__ == "__main__":
    main()
