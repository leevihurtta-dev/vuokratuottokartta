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
        if len(vals) > 30 and (code or "").lower().startswith(("alue", "area")):
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
                         if (c or "").lower().startswith(("alue", "area"))),
                        None)
        time_var = next((c for c in varmap
                         if (c or "").lower().startswith(
                             ("timeperiod", "vuosi", "year", "time"))), None)
        info_var = next((c for c in varmap
                         if (c or "").lower() == "contentscode"), None)
        if not (area_var and time_var and info_var):
            print(f"  muuttujia ei tunnistettu: {list(varmap)}")
            return
        area_vals = varmap[area_var]["values"]
        info_vals = varmap[info_var]["values"]
        # Koko maa + Helsinki jos löytyy; indeksipisteluku (ensimmäinen tieto).
        pick = [a for a in ("ksu", "091") if a in area_vals] or [area_vals[0]]
        # Suositaan 2015=100 -indeksiä, jos se on tarjolla (pitkä sarja).
        info = "ind15" if "ind15" in info_vals else info_vals[0]
        q = {"query": [
            {"code": area_var, "selection":
                {"filter": "item", "values": pick}},
            {"code": info_var, "selection":
                {"filter": "item", "values": [info]}},
        ], "response": {"format": "json-stat2"}}
        try:
            res = json.loads(_request(u, data=q).decode("utf-8"))
            vals = res.get("value", [])
            dim = res.get("dimension", {})
            years = list(dim.get(time_var, {}).get("category", {})
                         .get("index", {}))
            areas = list(dim.get(area_var, {}).get("category", {})
                         .get("label", {}).items())
            print(f"  OK. Alueet={areas} Tieto={info!r}")
            print(f"  Vuosia: {len(years)} ({years[0]}…{years[-1]})"
                  if years else "  ei vuosia")
            print(f"  Arvoja: {len(vals)}")
            print(f"  Ensimmäiset 12: {vals[:12]}")
            print(f"  Viimeiset 12:   {vals[-12:]}")
        except Exception as e:  # noqa: BLE001
            print(f"  koehaku epäonnistui: {e}")
        return


def map_kunnat(urls):
    """Vertaa sivuston kuntia indeksitaulukon aluearvoihin ja kertoo, mille
    kunnille löytyy oma indeksialue ja mille ei (jolloin tarvitaan maakunta)."""
    print("=" * 70)
    print("KUNTAKARTOITUS: sivuston kunnat vs. indeksialueet")
    print("=" * 70)
    meta = None
    for u in urls:
        try:
            meta = json.loads(_request(u).decode("utf-8"))
            break
        except Exception:  # noqa: BLE001
            continue
    if not meta:
        print("  metatietoja ei saatu\n")
        return
    area_var = next((v for v in meta.get("variables", [])
                     if (v.get("code") or "").lower().startswith("alue")), None)
    if not area_var:
        print("  aluemuuttujaa ei löytynyt\n")
        return
    names = {}
    for code, text in zip(area_var["values"], area_var["valueTexts"]):
        names[text.strip().lower()] = code

    try:
        with open("postal_yields.geojson", encoding="utf-8") as f:
            fc = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  geojsonia ei voitu lukea: {e}\n")
        return
    kunnat = sorted({(ft.get("properties") or {}).get("kunta")
                     for ft in fc.get("features", [])
                     if (ft.get("properties") or {}).get("kunta")})
    hit, miss = [], []
    for k in kunnat:
        code = names.get(k.strip().lower())
        (hit if code else miss).append((k, code))
    print(f"  Sivustolla kuntia: {len(kunnat)}")
    print(f"  Omalla indeksialueella: {len(hit)}")
    for k, c in hit:
        print(f"    {c} | {k}")
    print(f"\n  ILMAN omaa indeksialuetta (tarvitsevat maakunnan): {len(miss)}")
    print("    " + ", ".join(k for k, _ in miss))
    print("\n  Maakunta-aluekoodit taulukossa:")
    for code, text in zip(area_var["values"], area_var["valueTexts"]):
        if str(code).startswith("MK"):
            print(f"    {code} | {text}")
    print()


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
    try:
        map_kunnat(CANDIDATES["13mz"])
    except Exception as e:  # noqa: BLE001
        print(f"  !! kuntakartoitus kaatui: {e}\n")
    print("Valmis. Kopioi tämä loki keskusteluun.")


if __name__ == "__main__":
    sys.exit(main())
