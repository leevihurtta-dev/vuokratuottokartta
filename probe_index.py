#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_index.py – KERTALUONTEINEN KARTOITUS, ei osa varsinaista pipelineä.

Selvittää StatFinin hintaindeksitaulukoiden (13mq, 13mz) rakenteen: mitkä
muuttujat, mitkä aluearvot ja mitkä tietosisällöt niissä on. Tulostaa kaiken
lokiin, jotta aluekartoitus (kunta -> indeksialue) voidaan kirjoittaa oikein
ilman arvailua.

Ei kirjoita mitään tiedostoja eikä muuta sivustoa mitenkään.
Ajetaan GitHub Actionsissa, koska stat.fi vaatii verkkoyhteyden.
"""
import json
import sys
import time
import urllib.error
import urllib.request

USER_AGENT = "vuokratuottokartta/1.0 (kartoitus; PxWeb)"

CANDIDATES = {
    # vuosi-indeksi 2020=100, 2020-2025
    "13mq": [
        "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/statfin_ashi_pxt_13mq.px",
        "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/13mq.px",
    ],
    # pitkä vuosisarja 1988-2025, useita perusvuosia
    "13mz": [
        "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/statfin_ashi_pxt_13mz.px",
        "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/13mz.px",
    ],
}


def _request(url, data=None, retries=3, timeout=120):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body,
                                     method="POST" if body else "GET")
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json")
        if body:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
    raise RuntimeError(f"epäonnistui: {url}: {last}")


def probe(label, urls):
    print("=" * 70)
    print(f"TAULUKKO {label}")
    print("=" * 70)
    meta = None
    used = None
    for u in urls:
        try:
            meta = json.loads(_request(u).decode("utf-8"))
            used = u
            break
        except Exception as e:  # noqa: BLE001
            print(f"  (ei toiminut: {u} -> {e})")
    if not meta:
        print("  !! metatietoja ei saatu\n")
        return
    print(f"  Toimiva osoite: {used}")
    print(f"  Otsikko: {meta.get('title')}")
    print()
    for var in meta.get("variables", []):
        code = var.get("code")
        text = var.get("text")
        vals = var.get("values", [])
        txts = var.get("valueTexts", [])
        print(f"  MUUTTUJA code={code!r} text={text!r} "
              f"arvoja={len(vals)} elim={var.get('elimination')}")
        # Aluemuuttuja on tärkein -> tulostetaan KAIKKI arvot
        if len(vals) > 30 and (code or "").lower() in ("alue", "area"):
            print("    -- kaikki aluearvot (koodi | nimi) --")
            for v, t in zip(vals, txts):
                print(f"      {v} | {t}")
        elif len(vals) <= 30:
            for v, t in zip(vals, txts):
                print(f"      {v} | {t}")
        else:
            print(f"    ensimmäiset 10: "
                  f"{list(zip(vals[:10], txts[:10]))}")
            print(f"    viimeiset 5:   "
                  f"{list(zip(vals[-5:], txts[-5:]))}")
        print()


def sample_query(label, urls):
    """Kokeilee pientä hakua, jotta nähdään että data tulee ulos oikein."""
    print("-" * 70)
    print(f"KOEHAKU {label}: koko maa, kaikki vuodet, indeksipisteluku")
    for u in urls:
        try:
            meta = json.loads(_request(u).decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        varmap = {v.get("code"): v for v in meta.get("variables", [])}
        area_var = next((c for c in varmap
                         if (c or "").lower() in ("alue", "area")), None)
        time_var = next((c for c in varmap
                         if (c or "").lower() in ("vuosi", "year", "time")),
                        None)
        info_var = next((c for c in varmap
                         if (c or "").lower() in ("tiedot", "contentscode")),
                        None)
        if not (area_var and time_var and info_var):
            print(f"  muuttujia ei tunnistettu: {list(varmap)}")
            return
        area_vals = varmap[area_var]["values"]
        info_vals = varmap[info_var]["values"]
        q = {"query": [
            {"code": area_var, "selection":
                {"filter": "item", "values": [area_vals[0]]}},
            {"code": info_var, "selection":
                {"filter": "item", "values": [info_vals[0]]}},
        ], "response": {"format": "json-stat2"}}
        try:
            res = json.loads(_request(u, data=q).decode("utf-8"))
            vals = res.get("value", [])
            dim = res.get("dimension", {})
            years = list(dim.get(time_var, {}).get("category", {})
                         .get("index", {}))
            print(f"  OK. Alue={area_vals[0]!r} Tieto={info_vals[0]!r}")
            print(f"  Vuodet: {years}")
            print(f"  Arvot:  {vals}")
        except Exception as e:  # noqa: BLE001
            print(f"  koehaku epäonnistui: {e}")
        return


def main():
    print("StatFin hintaindeksien kartoitus")
    print("(tämä skripti ei muuta sivustoa mitenkään)\n")
    for label, urls in CANDIDATES.items():
        try:
            probe(label, urls)
            sample_query(label, urls)
            print()
        except Exception as e:  # noqa: BLE001
            print(f"  !! {label} kaatui: {e}\n")
    print("Valmis. Kopioi tämä loki keskusteluun.")


if __name__ == "__main__":
    sys.exit(main())
