"""Gedeelde HTTP-client: vaste pauze per bron, retries en een User-Agent met contactadres.

Het contactadres komt uitsluitend uit de omgevingsvariabele ATLAS_CONTACT (door de gebruiker gezet).
Zonder ATLAS_CONTACT weigert `Http(..., bulk=True)` te starten.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request

VERSIE = "0.2"


def user_agent(bulk: bool) -> str:
    contact = os.environ.get("ATLAS_CONTACT", "").strip()
    if contact:
        return f"GDPRDamagesAtlas/{VERSIE} (research feasibility study; contact: {contact})"
    if bulk:
        raise SystemExit("ATLAS_CONTACT is niet gezet. Zet je contactadres in die omgevingsvariabele vóór het ophalen.")
    return f"GDPRDamagesAtlas/{VERSIE} (research feasibility study)"


class Http:
    def __init__(self, pauze: float, bulk: bool = True, pogingen: int = 4):
        self.pauze = pauze
        self.pogingen = pogingen
        self.ua = user_agent(bulk)
        self._laatste = 0.0
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.n = 0

    def _wacht(self):
        delta = time.monotonic() - self._laatste
        if delta < self.pauze:
            time.sleep(self.pauze - delta)
        self._laatste = time.monotonic()

    def get(self, url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 120) -> bytes:
        h = {"User-Agent": self.ua, **(headers or {})}
        for poging in range(self.pogingen):
            self._wacht()
            self.n += 1
            try:
                return self.opener.open(urllib.request.Request(url, data=data, headers=h), timeout=timeout).read()
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and poging < self.pogingen - 1:
                    time.sleep(5 * 2 ** poging)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if poging < self.pogingen - 1:
                    time.sleep(5 * 2 ** poging)
                    continue
                raise

    def json(self, *a, **k):
        return json.loads(self.get(*a, **k))
