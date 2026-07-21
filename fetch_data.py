#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_data.py — Vanhojen osakeasuntojen bruttovuokratuotto postinumeroalueittain.

Hakee Tilastokeskuksen avoimesta datasta (CC BY 4.0):
  1) Vanhojen osakeasuntojen neliöhinnat postinumeroittain, VUOSITASOLLA
     (ashi 13mu; varalla neljännestaulukko 13mt, jonka vuoden neljännekset
     yhdistetään kauppamäärillä painotettuna keskiarvona)
  2) Vapaarahoitteisten vuokra-asuntojen keskineliövuokrat postinumeroittain
     (asvu 13eb, neljännekset) — vuoden neljännekset yhdistetään painotettuna
     keskiarvona
  3) Postinumeroalueiden geometriat + Paavo-taustatiedot (geo.stat.fi WFS)

Yhdistää postinumerolla, laskee brutto- ja nettovuokratuoton ja kirjoittaa
staattisen postal_yields.geojson-tiedoston frontendille.

Kattavuus: data kohdistetaan KOKO VUODELLE, koska neljännestasolla kauppoja
ja vuokrahavaintoja on niin vähän, että valtaosa alueista on peitetty.
Silti peitetyiksi jäävät arvot täydennetään oletuksena saman kunnan
keskiarvolla kuntatason taulukoista. Täydennetyt arvot merkitään
ominaisuuksiin (hinta_taso/vuokra_taso/taso = 'kunta') ja frontend näyttää
niistä huomautuksen. Poista käytöstä: --no-fallback.

Käyttö (rakennusjärjestyksen mukaisesti):
  python fetch_data.py --test 00120        # Vaihe 1: yhden postinumeron arvot
  python fetch_data.py --intermediate      # Vaihe 2: tallenna välitiedosto
  python fetch_data.py                     # Vaiheet 2–3: koko maa -> GeoJSON
  python fetch_data.py --kausi 2025        # pakota tietty vuosi
  python fetch_data.py --simplify 0        # älä yksinkertaista geometriaa
  python fetch_data.py --talotyyppi kerrostalot --huoneluku yksiot   # (edistynyt)

Riippuvuudet: vain Pythonin standardikirjasto. Geometrian yksinkertaistus
(vahvasti suositeltu, pienentää tiedoston ~murto-osaan) vaatii shapelyn:
  pip install shapely
"""

import argparse
import datetime as _dt
import json
import re
import sys
import time
import urllib.error
import urllib.request

# ----------------------------------------------------------------------------
# Vakiot
# ----------------------------------------------------------------------------

USER_AGENT = "vuokratuottokartta/1.0 (avoin data; PxWeb + Paavo WFS)"

# PxWeb-API:n polkurakenne on vaihdellut vuosien varrella, joten kokeillaan
# useampaa kandidaattia ja käytetään ensimmäistä, joka palauttaa metatiedot.
#
# Hinnat haetaan ensisijaisesti VUOSITASON postinumerotaulukosta: neljännes-
# tasolla kauppoja on niin vähän, että valtaosa alueista on peitetty
# (havaittu ajossa: arvo vain 253/1724 alueella). 13mu on 13mt:n
# vuositason vastinpari (aikamuuttujan arvot 2023, 2024, 2025, …).
PRICE_TABLE_ANNUAL_CANDIDATES = [
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/13mu.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/statfin_ashi_pxt_13mu.px",
]
PRICE_TABLE_CANDIDATES = [
    # Kansio + lyhyt tunniste — verifioitu toimivaksi pxdata.stat.fi:ssä 7/2026:
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/13mt.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/statfin_ashi_pxt_13mt.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/statfin_ashi_pxt_13mt.px",
    # Varalle: arkistokanta, jos taulukko joskus arkistoidaan:
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin_Passiivi/ashi/13mt.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin_Passiivi/ashi/statfin_ashi_pxt_13mt.px",
    "https://statfin.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/statfin_ashi_pxt_13mt.px",
]
RENT_TABLE_CANDIDATES = [
    # HUOM: Tilastokeskus uudisti vuokratilaston 28.4.2026 ja arkistoi
    # postinumerotason taulukon 13eb (viimeinen tieto 2025Q4). Arkistokannan
    # (StatFin_Passiivi) taulukoita voi käyttää rajapinnasta samalla tavalla.
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin_Passiivi/asvu/13eb.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin_Passiivi/asvu/statfin_asvu_pxt_13eb.px",
    # Aktiivinen kanta varalle (jos postinumerotaulukko joskus palaa):
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/asvu/13eb.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/asvu/statfin_asvu_pxt_13eb.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/statfin_asvu_pxt_13eb.px",
    "https://statfin.stat.fi/PxWeb/api/v1/fi/StatFin/asvu/statfin_asvu_pxt_13eb.px",
]

# Kuntatason varataulukot: kun postinumerotason hinta tai vuokra on peitetty,
# täydennetään saman kunnan keskiarvolla, jotta tärkeimmät alueet eivät jää
# harmaiksi. Jos suorat osoitteet eivät toimi, resolve_table etsii
# kansiolistauksesta taulukon hakusanoilla, ja taulukon kelpoisuus
# varmistetaan (siinä on oltava kunta/alue-muuttuja).
PRICE_MUNI_TABLE_CANDIDATES = [
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/13mv.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/statfin_ashi_pxt_13mv.px",
]
RENT_MUNI_TABLE_CANDIDATES = [
    # 15fa = uudistetun vuokratilaston "Vuokraindeksi ja keskineliövuokrat,
    # neljännesvuosittain" (aluetasolla; havaittu API:n kansiolistauksesta).
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/asvu/15fa.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/asvu/statfin_asvu_pxt_15fa.px",
    # Arkistoitu vastine varalle (2015Q1–2025Q4):
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin_Passiivi/asvu/statfinpas_asvu_pxt_11x4_2025q4.px",
]

# pno_tilasto = rantaviivalla leikatut alueet + Paavo-tilastot valmiiksi mukana.
WFS_URL = (
    "https://geo.stat.fi/geoserver/postialue/wfs"
    "?service=WFS&version=2.0.0&request=GetFeature"
    "&typeName=postialue:pno_tilasto"
    "&srsName=EPSG:4326&outputFormat=application/json"
)

# Nettotuoton oletukset (frontend laskee myös itse liukusäätimillä; nämä
# kirjoitetaan GeoJSONiin oletusarvoisena netto_pct:nä).
DEFAULT_HOITOVASTIKE = 4.5     # €/m²/kk
DEFAULT_VAJAAKAYTTO = 0.05     # 5 %
DEFAULT_VARAINSIIRTOVERO = 0.015  # 1,5 % (osakehuoneisto)

COORD_DECIMALS = 5             # ~1 m tarkkuus WGS84:ssä


# ----------------------------------------------------------------------------
# HTTP-apurit (vain standardikirjasto, uudelleenyritys ruuhkatilanteissa)
# ----------------------------------------------------------------------------

def _request(url, data=None, retries=4, timeout=180):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json")
        if body:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  HTTP {e.code}, yritetään uudelleen {wait} s kuluttua…")
                time.sleep(wait)
                continue
            try:
                body = e.read().decode("utf-8", "replace").strip()[:300]
            except Exception:  # noqa: BLE001
                body = ""
            msg = f"HTTP {e.code} {e.reason}"
            if body:
                msg += f" — palvelimen selitys: {body!r}"
            raise RuntimeError(msg) from None
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
    raise RuntimeError(f"Pyyntö epäonnistui: {url}: {last_err}")


def get_json(url):
    return json.loads(_request(url).decode("utf-8"))


def post_json(url, payload):
    return json.loads(_request(url, data=payload).decode("utf-8"))


# ----------------------------------------------------------------------------
# PxWeb: metatiedot ja arvokoodien selvitys (EI kovakoodattuja koodeja)
# ----------------------------------------------------------------------------

def _table_ok(meta, require_vars, require_time):
    """Tarkistaa, että taulukossa on vaaditut muuttujat ja oikea aikataso
    ('annual' = vuodet, 'quarterly' = vuosineljännekset)."""
    try:
        for needles in (require_vars or ()):
            find_variable(meta, *needles)
        if require_time:
            tvar = time_variable(meta)
            pat = r"\d{4}" if require_time == "annual" else r"\d{4}Q\d"
            if not any(re.fullmatch(pat, str(v)) for v in tvar["values"]):
                return False
    except SystemExit:
        return False
    return True


def resolve_table(candidates, label, needles=(), require_vars=None,
                  require_time=None):
    """Palauttaa (url, metatiedot) ensimmäiselle toimivalle API-osoitteelle.

    Jos mikään suora osoite ei toimi, listataan kansion taulukot rajapinnasta
    ja etsitään taulukko tunnisteen tai nimen perusteella (needles).
    require_vars/require_time: hylkää taulukot, joista puuttuu vaadittu
    muuttuja tai joilla on väärä aikataso (esim. neljännestaulukko, kun
    tarvitaan vuositaso)."""
    errors = []
    for url in candidates:
        try:
            meta = get_json(url)
            if isinstance(meta, dict) and "variables" in meta:
                if not _table_ok(meta, require_vars, require_time):
                    errors.append(f"{url}: taulukko ei täytä vaatimuksia "
                                  f"(muuttujat tai aikataso)")
                    continue
                print(f"[{label}] Taulukko löytyi: {url}")
                return url, meta
            errors.append(f"{url}: odottamaton vastaus")
        except Exception as e:  # noqa: BLE001 - raportoidaan kootusti
            errors.append(f"{url}: {e}")

    # Itsekorjaus: kysytään rajapinnalta, mitä taulukoita kansiossa oikeasti on.
    listings = []
    for url in candidates:
        base = url.rsplit("/", 1)[0]
        if base not in listings:
            listings.append(base)
    for base in listings:
        try:
            entries = get_json(base)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{base} (kansiolistaus): {e}")
            continue
        if not isinstance(entries, list):
            continue
        tables = [e for e in entries if e.get("type", "t") in ("t", "T")]
        matches = [e for e in tables
                   if any(n.lower() in (str(e.get("id", "")) + " "
                                        + str(e.get("text", ""))).lower()
                          for n in needles)]
        for entry in matches:
            tid = str(entry.get("id", ""))
            if not tid:
                continue
            turl = base + "/" + (tid if tid.endswith(".px") else tid + ".px")
            try:
                meta = get_json(turl)
                if isinstance(meta, dict) and "variables" in meta:
                    if not _table_ok(meta, require_vars, require_time):
                        errors.append(f"{turl}: taulukko ei täytä vaatimuksia "
                                      f"(muuttujat tai aikataso)")
                        continue
                    print(f"[{label}] Taulukko löytyi kansiolistauksen kautta: "
                          f"{turl} ({entry.get('text', '')!r})")
                    return turl, meta
                errors.append(f"{turl}: odottamaton vastaus")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{turl}: {e}")
        print(f"[{label}] Kansion {base} taulukot: "
              + "; ".join(f"{e.get('id')}={e.get('text', '')!r}"
                          for e in tables[:20]))
    raise SystemExit(
        f"[{label}] Taulukon metatietoja ei saatu mistään kandidaatista:\n  "
        + "\n  ".join(errors)
        + "\nTarkista API-osoite osoitteesta https://pxdata.stat.fi ja "
          "verkkoyhteys (tässä ympäristössä pxdata.stat.fi voi olla estetty)."
    )


def find_variable(meta, *needles):
    """Etsii muuttujan, jonka code tai text sisältää jonkin hakusanoista."""
    for var in meta["variables"]:
        hay = (var.get("code", "") + " " + var.get("text", "")).lower()
        if any(n.lower() in hay for n in needles):
            return var
    raise SystemExit(
        "Muuttujaa ei löytynyt hakusanoilla "
        + repr(needles)
        + ". Saatavilla: "
        + ", ".join(v.get("code", "?") for v in meta["variables"])
    )


def time_variable(meta):
    for var in meta["variables"]:
        if var.get("time"):
            return var
    return find_variable(meta, "vuosineljännes", "vuosi", "kuukausi")


def pick_value(var, include, exclude=(), required=True):
    """Palauttaa (koodi, teksti) arvolle, jonka teksti täsmää hakusanoihin.

    Osumat etsitään tiukimmasta löyhimpään (täsmälleen sama -> alkaa sanalla
    -> sisältää sanan) ja hakusanalistan järjestyksessä, jotta esim.
    'Talotyypit yhteensä' voittaa arvon 'Rivitalot yhteensä'."""
    pairs = [(c, t) for c, t in zip(var["values"], var["valueTexts"])
             if not any(x.lower() in t.lower() for x in exclude)]
    for mode in ("exact", "prefix", "contains"):
        for needle in include:
            n = needle.lower()
            for code, text in pairs:
                t = text.lower().strip()
                if ((mode == "exact" and t == n)
                        or (mode == "prefix" and t.startswith(n))
                        or (mode == "contains" and n in t)):
                    return code, text
    if not required:
        return None
    raise SystemExit(
        f"Arvoa ei löytynyt muuttujasta {var.get('code')} hakusanoilla {include}. "
        f"Saatavilla: {var['valueTexts']}"
    )


TALOTYYPPI_HAKUSANAT = {
    # HUOM: pelkkä "yhteensä" poistettu tarkoituksella — se osui vahingossa
    # arvoon "Rivitalot yhteensä", kun taulukossa ei ollut kaikkien
    # talotyyppien yhteensä-luokkaa.
    "yhteensa": ["talotyypit yhteensä", "kaikki talotyypit"],
    "kerrostalot": ["kerrostalot yhteensä", "kerrostalot"],
    "rivitalot": ["rivitalot yhteensä", "rivitalot"],
}
TALOTYYPPI_SUODATIN = {
    "yhteensa": None, "kerrostalot": "kerrostalo", "rivitalot": "rivitalo",
}
HUONELUKU_HAKUSANAT = {
    "yhteensa": ["huoneluvut yhteensä", "kaikki huoneluvut"],
    "yksiot": ["yksiöt", "yksiö"],
    "kaksiot": ["kaksiot", "kaksio"],
    "kolmiot": ["kolmiot+", "kolmiot", "kolmio", "3h"],
}
HUONELUKU_SUODATIN = {
    "yhteensa": None, "yksiot": "yksiö", "kaksiot": "kaksio",
    "kolmiot": "kolmi",
}


def class_codes_for(var, choice, hakusanat, suodatin):
    """Palauttaa (koodilista, kuvausteksti) luokkamuuttujalle.

    Jos taulukossa on valmis luokka (esim. 'Talotyypit yhteensä'), käytetään
    sitä. Muuten palautetaan kaikki valintaan sopivat luokat — esim.
    'yhteensa' -> kaikki talotyypit, 'kerrostalot' -> kaikki
    kerrostalo-luokat — ja arvo lasketaan niistä havaintomäärillä
    painotettuna keskiarvona (fetch_pxweb hoitaa painotuksen)."""
    picked = pick_value(var, hakusanat[choice], required=False)
    if picked is not None:
        return [picked[0]], picked[1]
    if choice == "yhteensa":
        # Turvallinen erikoistapaus: luokka, jonka teksti on TÄSMÄLLEEN
        # "Yhteensä" (osumaa "Rivitalot yhteensä" -tyyppisiin ei sallita —
        # se aiheutti aiemmin väärän luokan valinnan).
        for c, t in zip(var["values"], var["valueTexts"]):
            if t.strip().lower() == "yhteensä":
                return [c], t
    word = suodatin[choice]
    pairs = list(zip(var["values"], var["valueTexts"]))
    if word is not None:
        matching = [(c, t) for c, t in pairs if word in t.lower()]
        if not matching:
            raise SystemExit(
                f"Muuttujasta {var.get('code')} ei löytynyt luokkaa "
                f"valinnalle {choice!r}. Saatavilla: {var['valueTexts']}")
        pairs = matching
    codes = [c for c, _ in pairs]
    if len(pairs) == 1:
        return codes, pairs[0][1]
    return codes, "painotettu keskiarvo: " + ", ".join(t for _, t in pairs)


# ----------------------------------------------------------------------------
# json-stat2-parsinta
# ----------------------------------------------------------------------------

def jsonstat_reader(ds):
    """Palauttaa funktion get(**{dim_id: category_code}) -> arvo tai None."""
    ids = ds["id"]
    sizes = ds["size"]
    strides = [1] * len(ids)
    for i in range(len(ids) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    positions = {}
    for dim_id in ids:
        cat = ds["dimension"][dim_id]["category"]
        idx = cat.get("index")
        if idx is None:  # json-stat2 sallii indeksin puuttua yksialkioisilta
            only = next(iter(cat.get("label", {"_": "_"})))
            positions[dim_id] = {only: 0}
        elif isinstance(idx, list):
            positions[dim_id] = {code: i for i, code in enumerate(idx)}
        else:
            positions[dim_id] = dict(idx)

    values = ds["value"]

    def get(**coords):
        i = 0
        for d, dim_id in enumerate(ids):
            if dim_id in coords:
                pos = positions[dim_id].get(coords[dim_id])
            elif sizes[d] == 1:
                pos = 0  # dimensio, jota ei annettu; vain yksi arvo -> se
            else:
                return None
            if pos is None:
                return None
            i += pos * strides[d]
        v = values[i] if 0 <= i < len(values) else None
        return v if isinstance(v, (int, float)) else None

    return get


def category_labels(ds, dim_id):
    cat = ds["dimension"][dim_id]["category"]
    return dict(cat.get("label", {}))


# ----------------------------------------------------------------------------
# PxWeb-haut
# ----------------------------------------------------------------------------

def fetch_pxweb(url, meta, kaudet, area_codes, class_var, class_codes,
                value_code, count_code, label,
                area_needles=("postinumero",), code_pattern=r"\d{5}"):
    """Hakee yhden taulukon yhdelle tai useammalle kaudelle.

    kaudet: lista aikamuuttujan arvoja (esim. ["2025"] tai vuoden neljä
    neljännestä). Jos kausia tai luokka-arvoja (class_codes) on useita,
    arvo lasketaan havaintomäärillä painotettuna keskiarvona — näin koko
    vuoden data yhdistyy, vaikka taulukko olisi neljännestasoinen, ja
    "yhteensä" saadaan, vaikka valmista yhteensä-luokkaa ei olisi.

    class_var voi olla None, jos taulukossa ei ole luokkajakoa.
    count_code voi olla None, jos taulukossa ei ole havaintomäärää.

    Palauttaa: dict aluekoodi -> {"arvo": float|None, "n": int|None,
                                   "label": "00120 Punavuori (Helsinki)"}
    """
    tvar = time_variable(meta)
    pvar = find_variable(meta, *area_needles)
    ivar = find_variable(meta, "tiedot")

    tiedot_values = [value_code]
    if count_code and count_code != value_code:
        tiedot_values.append(count_code)

    query_items = [
        {"code": tvar["code"],
         "selection": {"filter": "item", "values": list(kaudet)}},
        {"code": pvar["code"],
         "selection": {"filter": "item", "values": area_codes}},
    ]
    if class_var is not None:
        query_items.append(
            {"code": class_var["code"],
             "selection": {"filter": "item", "values": list(class_codes)}})
    query_items.append(
        {"code": ivar["code"],
         "selection": {"filter": "item", "values": tiedot_values}})

    # Taulukossa voi olla muuttujia, joita tämä skripti ei tunne (esim.
    # uudistetun vuokratilaston Rahoitusmuoto). Ne rajataan yhteen arvoon,
    # jotta vastauksen dimensiot pysyvät hallinnassa: suositaan
    # vapaarahoitteista tai yhteensä-luokkaa, muuten ensimmäistä arvoa.
    known = {tvar["code"], pvar["code"], ivar["code"]}
    if class_var is not None:
        known.add(class_var["code"])
    extra_coords = {}
    for var in meta.get("variables", []):
        vcode = var.get("code")
        if not vcode or vcode in known:
            continue
        pick = pick_value(var, ["vapaarahoit", "yhteensä", "kaikki",
                                "koko maa"], required=False)
        val_code, val_text = (pick if pick
                              else (var["values"][0], var["valueTexts"][0]))
        print(f"[{label}] Lisämuuttuja {vcode!r}: käytetään arvoa {val_text!r}")
        query_items.append({"code": vcode,
                            "selection": {"filter": "item",
                                          "values": [val_code]}})
        extra_coords[vcode] = val_code

    query = {"query": query_items, "response": {"format": "json-stat2"}}

    print(f"[{label}] Haetaan {len(area_codes)} aluetta, "
          f"kaudet: {', '.join(kaudet)}…")
    ds = post_json(url, query)
    get = jsonstat_reader(ds)
    labels = category_labels(ds, pvar["code"])

    out = {}
    for code in area_codes:
        if not re.fullmatch(code_pattern, str(code)):
            continue  # ohita mahdolliset koostealueet
        pairs = []
        for kk in kaudet:
            for cc in (class_codes if class_var is not None else [None]):
                coords = {tvar["code"]: kk, pvar["code"]: code}
                coords.update(extra_coords)
                if class_var is not None:
                    coords[class_var["code"]] = cc
                arvo = get(**coords, **{ivar["code"]: value_code})
                n = (get(**coords, **{ivar["code"]: count_code})
                     if len(tiedot_values) > 1 else None)
                if arvo is not None:
                    pairs.append((float(arvo), int(n) if n is not None else None))
        if not pairs:
            arvo_out, n_out = None, None
        elif len(pairs) == 1:
            arvo_out, n_out = pairs[0]
        else:
            weights = [n for _, n in pairs if n]
            if len(weights) == len(pairs) and sum(weights) > 0:
                tot = sum(weights)
                arvo_out = sum(a * n for a, n in pairs) / tot
                n_out = tot
            else:  # painoja ei saatavilla kaikille -> tavallinen keskiarvo
                arvo_out = sum(a for a, _ in pairs) / len(pairs)
                n_out = sum(n for _, n in pairs if n) or None
        out[code] = {
            "arvo": arvo_out,
            "n": n_out,
            "label": labels.get(code, code),
        }
    n_ok = sum(1 for v in out.values() if v["arvo"] is not None)
    print(f"[{label}] Arvo saatavilla {n_ok}/{len(out)} alueella "
          f"(loput peitetty/puuttuu — tämä on odotettua).")
    return out


def kunta_from_label(label):
    """'00120 Punavuori (Helsinki)' -> 'Helsinki'. Käytetään koodia liitokseen,
    selitteestä poimitaan vain kuntanimi popupia varten."""
    m = re.search(r"\(([^()]+)\)\s*$", label or "")
    return m.group(1) if m else None


def nimi_from_label(label):
    m = re.match(r"^\d{5}\s+(.*?)\s*(?:\([^()]*\))?\s*$", label or "")
    return m.group(1) if m else None


# ----------------------------------------------------------------------------
# Kuntatason varadata (täydentää peitetyt postinumerotason arvot)
# ----------------------------------------------------------------------------

def normalize_kunta(code):
    """'KU091' | '091' | 91 -> '091'. PxWebin kuntakoodissa on usein
    KU-etuliite, Paavon pno_tilasto-tasossa pelkkä 3-numeroinen koodi."""
    s = str(code or "").strip()
    if s[:2].upper() == "KU":
        s = s[2:]
    return s.zfill(3) if s.isdigit() else s


def with_fallback(primary, fallback):
    """Yhdistää postinumerotason arvon ja kuntatason varan.

    primary/fallback: {"arvo": float|None, "n": int|None} tai None.
    Palauttaa (arvo, n, taso), missä taso on 'pno', 'kunta' tai None."""
    p = primary or {}
    if p.get("arvo") is not None:
        return p["arvo"], p.get("n"), "pno"
    f = fallback or {}
    if f.get("arvo") is not None:
        return f["arvo"], f.get("n"), "kunta"
    return None, None, None


def fetch_muni_fallback(kind, kausi, class_choice):
    """Hakee kuntatason hinnat tai vuokrat peitettyjen alueiden täydentämiseksi.

    kind: 'hinnat' | 'vuokrat'. kausi: vuosi, esim. '2025'. Palauttaa
    (data, info), missä data on dict, jonka avaimina on sekä kuntakoodi
    ('091') että kunnan nimi pienillä kirjaimilla ('helsinki') — osa
    taulukoista käyttää koodeja, osa nimiä. Nostaa poikkeuksen, jos dataa
    ei saada — kutsuja saa jatkaa ilman varadataa."""
    if kind == "hinnat":
        candidates = PRICE_MUNI_TABLE_CANDIDATES
        needles = ["kunnittain"]
        class_needles = ("talotyyppi",)
        hakusanat, suodatin = TALOTYYPPI_HAKUSANAT, TALOTYYPPI_SUODATIN
        include = ["neliöhinta"]
        exclude = ["lukumäärä", "muutos", "indeksi", "jakauma"]
    else:
        candidates = RENT_MUNI_TABLE_CANDIDATES
        needles = ["keskineliövuokrat", "kunnittain", "alueittain"]
        class_needles = ("huoneluku", "huoneistotyyppi")
        hakusanat, suodatin = HUONELUKU_HAKUSANAT, HUONELUKU_SUODATIN
        include = ["keskineliövuokra", "neliövuokra", "vuokra"]
        exclude = ["lukumäärä", "muutos", "indeksi", "jakauma"]

    label = f"{kind}/kunta"
    url, meta = resolve_table(candidates, label, needles=needles,
                              require_vars=[("kunta", "alue"), ("tiedot",)])

    # Kausi: sama vuosi (vuositaso tai vuoden neljännekset yhdistettynä);
    # jos vuotta ei ole taulukossa, uusin saatavilla oleva.
    tvar = time_variable(meta)
    vals = [str(v) for v in tvar["values"]]
    periods = ([v for v in vals if v == kausi]
               or [v for v in vals if re.fullmatch(rf"{kausi}Q\d", v)])
    if not periods:
        years = sorted({m.group(1) for v in vals
                        for m in [re.match(r"^(\d{4})(?:Q\d)?$", v)] if m})
        if not years:
            raise RuntimeError(f"taulukon {url} aikamuuttujaa ei tunnistettu "
                               f"(viimeiset arvot: {vals[-3:]})")
        y = years[-1]
        periods = ([v for v in vals if v == y]
                   or [v for v in vals if re.fullmatch(rf"{y}Q\d", v)])
        print(f"[{label}] Vuotta {kausi} ei ole taulukossa — "
              f"käytetään lähintä saatavilla olevaa: {y}.")

    avar = find_variable(meta, "kunta", "alue")
    codes = [c for c in avar["values"]
             if re.fullmatch(r"KU\d{3}|\d{3}", str(c))]
    if not codes:  # tuntematon koodimuoto -> kaikki paitsi koko maa
        codes = [c for c in avar["values"] if str(c).upper() != "SSS"]

    # Luokkajako (talotyyppi/huoneluku) voi puuttua kokonaan.
    try:
        cvar = find_variable(meta, *class_needles)
    except SystemExit:
        cvar, ccodes = None, None
    else:
        ccodes, _ = class_codes_for(cvar, class_choice, hakusanat, suodatin)

    ivar = find_variable(meta, "tiedot")
    value_code, _ = pick_value(ivar, include, exclude=exclude)
    cnt = pick_value(ivar, ["lukumäärä"], required=False)
    count_code = cnt[0] if cnt else None

    data = fetch_pxweb(url, meta, periods, codes, cvar, ccodes,
                       value_code, count_code, label,
                       area_needles=("kunta", "alue"), code_pattern=r".+")
    out = {}
    n_alueita = 0
    for c, v in data.items():
        if v["arvo"] is None:
            continue
        n_alueita += 1
        out[normalize_kunta(c)] = v
        name = str(v.get("label") or "").strip().lower()
        if name:
            out.setdefault(name, v)
    if not out:
        raise RuntimeError(f"kuntatason taulukko {url} ei palauttanut arvoja "
                           f"kausille {periods}")
    return out, {"taulukko": url, "kaudet": periods, "alueita": n_alueita}


# ----------------------------------------------------------------------------
# WFS-geometria
# ----------------------------------------------------------------------------

POSTAL_KEY_CANDIDATES = ("posti_alue", "postinumeroalue", "pno", "postinumero")


def fetch_geometry():
    print("[geometria] Haetaan postinumeroalueet WFS:stä (voi kestää hetken)…")
    raw = _request(WFS_URL, timeout=600)
    fc = json.loads(raw.decode("utf-8"))
    feats = fc.get("features", [])
    if not feats:
        raise SystemExit("[geometria] WFS ei palauttanut yhtään aluetta.")
    props0 = feats[0].get("properties", {})
    postal_key = next((k for k in POSTAL_KEY_CANDIDATES if k in props0), None)
    if postal_key is None:
        raise SystemExit(
            "[geometria] Postinumerokenttää ei tunnistettu. Ominaisuudet: "
            + ", ".join(sorted(props0))
        )
    print(f"[geometria] {len(feats)} aluetta, postinumerokenttä '{postal_key}'.")
    return feats, postal_key


def _round_coords(coords):
    if isinstance(coords, (int, float)):
        return round(coords, COORD_DECIMALS)
    return [_round_coords(c) for c in coords]


def simplify_geometry(geom, tolerance):
    if tolerance <= 0:
        return {"type": geom["type"], "coordinates": _round_coords(geom["coordinates"])}
    try:
        from shapely.geometry import mapping, shape  # noqa: PLC0415
    except ImportError:
        print("VAROITUS: shapely puuttuu — geometriaa ei yksinkertaisteta "
              "(tiedostosta tulee iso). Asenna: pip install shapely")
        return {"type": geom["type"], "coordinates": _round_coords(geom["coordinates"])}
    simplified = shape(geom).simplify(tolerance, preserve_topology=True)
    if simplified.is_empty:
        simplified = shape(geom)
    m = mapping(simplified)
    return {"type": m["type"], "coordinates": _round_coords(m["coordinates"])}


def masked_stat(value):
    """Paavossa peitetty arvo on -1 -> None."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v < 0 else v


# ----------------------------------------------------------------------------
# Laskenta
# ----------------------------------------------------------------------------

def brutto_pct(hinta, vuokra):
    if hinta and vuokra and hinta > 0:
        return round(vuokra * 12.0 / hinta * 100.0, 2)
    return None


def netto_pct(hinta, vuokra,
              hoitovastike=DEFAULT_HOITOVASTIKE,
              vajaakaytto=DEFAULT_VAJAAKAYTTO,
              varainsiirtovero=DEFAULT_VARAINSIIRTOVERO):
    if hinta and vuokra and hinta > 0:
        netto = ((vuokra - hoitovastike) * 12.0 * (1.0 - vajaakaytto)) / (
            hinta * (1.0 + varainsiirtovero)
        ) * 100.0
        return round(netto, 2)
    return None


# ----------------------------------------------------------------------------
# Pääohjelma
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", metavar="POSTINUMERO",
                    help="Testaa yhdellä postinumerolla (esim. 00120) ja lopeta.")
    ap.add_argument("--kausi", help="Vuosi, esim. 2025. Oletus: uusin vuosi, "
                                    "jolta on sekä hinta- että vuokradataa.")
    ap.add_argument("--talotyyppi", default="yhteensa",
                    choices=sorted(TALOTYYPPI_HAKUSANAT))
    ap.add_argument("--huoneluku", default="yhteensa",
                    choices=sorted(HUONELUKU_HAKUSANAT))
    ap.add_argument("--simplify", type=float, default=0.0005,
                    help="Geometrian yksinkertaistustoleranssi asteina "
                         "(oletus 0.0005 ≈ 40 m; 0 = ei yksinkertaistusta).")
    ap.add_argument("--intermediate", action="store_true",
                    help="Tallenna välitiedosto prices_rents.json (vaihe 2).")
    ap.add_argument("--no-fallback", action="store_true",
                    help="Älä täydennä peitettyjä postinumeroalueita "
                         "kuntatason keskiarvoilla (oletuksena täydennetään).")
    ap.add_argument("--out", default="postal_yields.geojson")
    args = ap.parse_args()

    # --- Metatiedot ja arvokoodit ------------------------------------------
    # Hinnat: ensisijaisesti vuositason postinumerotaulukko (neljännestasolla
    # valtaosa alueista on peitetty vähäisten kauppamäärien vuoksi).
    price_annual = True
    try:
        price_url, price_meta = resolve_table(
            PRICE_TABLE_ANNUAL_CANDIDATES, "hinnat",
            needles=["13mu", "postinumero"],
            require_vars=[("postinumero",), ("talotyyppi",), ("tiedot",)],
            require_time="annual")
    except SystemExit:
        print("HUOM: vuositason hintataulukkoa ei löytynyt — käytetään "
              "neljännestaulukkoa ja yhdistetään vuoden neljännekset itse.")
        price_annual = False
        price_url, price_meta = resolve_table(
            PRICE_TABLE_CANDIDATES, "hinnat", needles=["13mt", "postinumero"],
            require_vars=[("postinumero",)], require_time="quarterly")
    rent_url, rent_meta = resolve_table(
        RENT_TABLE_CANDIDATES, "vuokrat", needles=["13eb", "postinumero"],
        require_vars=[("postinumero",)], require_time="quarterly")

    talotyyppi_var = find_variable(price_meta, "talotyyppi")
    talotyyppi_codes, talotyyppi_text = class_codes_for(
        talotyyppi_var, args.talotyyppi,
        TALOTYYPPI_HAKUSANAT, TALOTYYPPI_SUODATIN)
    p_tiedot = find_variable(price_meta, "tiedot")
    price_value_code, price_value_text = pick_value(
        p_tiedot, ["neliöhinta"], exclude=["lukumäärä", "muutos", "indeksi"])
    pc = pick_value(p_tiedot, ["lukumäärä"], required=False)
    price_count_code = pc[0] if pc else None

    huoneluku_var = find_variable(rent_meta, "huoneluku", "huoneistotyyppi")
    huoneluku_codes, huoneluku_text = class_codes_for(
        huoneluku_var, args.huoneluku,
        HUONELUKU_HAKUSANAT, HUONELUKU_SUODATIN)
    r_tiedot = find_variable(rent_meta, "tiedot")
    rent_value_code, rent_value_text = pick_value(
        r_tiedot, ["vuokra"], exclude=["lukumäärä", "muutos", "indeksi"])
    rc = pick_value(r_tiedot, ["lukumäärä"], required=False)
    rent_count_code = rc[0] if rc else None

    print(f"[hinnat]  Talotyyppi: {talotyyppi_text!r}, tieto: {price_value_text!r}")
    print(f"[vuokrat] Huoneluku: {huoneluku_text!r}, tieto: {rent_value_text!r}")

    # --- Kausi: kokonainen vuosi -------------------------------------------
    # Neljännestasolla kauppoja/havaintoja on vähän ja peittoa paljon, joten
    # data kohdistetaan koko vuodelle: hinnat vuositaulukosta (tai neljännekset
    # yhdistäen), vuokrat vuoden neljänneksistä painotettuna keskiarvona.
    def _years_of(values):
        return {m.group(1) for v in values
                for m in [re.match(r"^(\d{4})(?:Q\d)?$", str(v))] if m}

    price_times = [str(v) for v in time_variable(price_meta)["values"]]
    rent_times = [str(v) for v in time_variable(rent_meta)["values"]]
    common = sorted(_years_of(price_times) & _years_of(rent_times))
    if not common:
        raise SystemExit("Taulukoilla ei ole yhteisiä vuosia!")
    if args.kausi:
        m = re.match(r"^\s*(\d{4})", str(args.kausi))
        if not m:
            raise SystemExit(f"--kausi: anna vuosi, esim. 2025 "
                             f"(sain {args.kausi!r})")
        kausi = m.group(1)
        if kausi != str(args.kausi).strip():
            print(f"HUOM: kausi tulkittiin koko vuodeksi {kausi} "
                  f"({args.kausi!r}).")
        if kausi not in common:
            raise SystemExit(f"Vuosi {kausi} ei ole molemmissa taulukoissa. "
                             f"Yhteiset vuodet: {common[-6:]}")
    else:
        kausi = common[-1]
    price_periods = ([kausi] if price_annual
                     else [t for t in price_times
                           if re.fullmatch(rf"{kausi}Q\d", t)])
    rent_periods = [t for t in rent_times if re.fullmatch(rf"{kausi}Q\d", t)]
    if not rent_periods:
        raise SystemExit(f"Vuokrataulukossa ei ole neljänneksiä vuodelle "
                         f"{kausi}.")
    print(f"Käytetään vuotta {kausi} (hinnat: {', '.join(price_periods)}; "
          f"vuokrat: {', '.join(rent_periods)})")

    price_codes = [c for c in find_variable(price_meta, "postinumero")["values"]
                   if re.fullmatch(r"\d{5}", c)]
    rent_codes = [c for c in find_variable(rent_meta, "postinumero")["values"]
                  if re.fullmatch(r"\d{5}", c)]

    # --- Vaihe 1: yhden postinumeron testi ---------------------------------
    if args.test:
        code = args.test
        prices = fetch_pxweb(price_url, price_meta, price_periods,
                             [code] if code in price_codes else price_codes[:1],
                             talotyyppi_var, talotyyppi_codes,
                             price_value_code, price_count_code, "hinnat")
        rents = fetch_pxweb(rent_url, rent_meta, rent_periods,
                            [code] if code in rent_codes else rent_codes[:1],
                            huoneluku_var, huoneluku_codes,
                            rent_value_code, rent_count_code, "vuokrat")
        p = prices.get(code, {"arvo": None, "n": None, "label": code})
        r = rents.get(code, {"arvo": None, "n": None, "label": code})
        print("\n--- TESTI ---")
        print(f"Postinumero:      {p['label']}")
        print(f"Neliöhinta:       {p['arvo']} €/m² (kauppoja: {p['n']})")
        print(f"Keskineliövuokra: {r['arvo']} €/m²/kk (havaintoja: {r['n']})")
        print(f"Bruttotuotto:     {brutto_pct(p['arvo'], r['arvo'])} %")
        print(f"Nettotuotto:      {netto_pct(p['arvo'], r['arvo'])} % (oletuksilla)")
        return

    # --- Vaihe 2: koko maan hinnat ja vuokrat ------------------------------
    prices = fetch_pxweb(price_url, price_meta, price_periods, price_codes,
                         talotyyppi_var, talotyyppi_codes,
                         price_value_code, price_count_code, "hinnat")
    rents = fetch_pxweb(rent_url, rent_meta, rent_periods, rent_codes,
                        huoneluku_var, huoneluku_codes,
                        rent_value_code, rent_count_code, "vuokrat")

    # --- Vaihe 2b: kuntatason varadata peitetyille alueille -----------------
    muni_prices, muni_rents, muni_info = {}, {}, {}
    if not args.no_fallback:
        for kind, choice in (("hinnat", args.talotyyppi),
                             ("vuokrat", args.huoneluku)):
            try:
                data, info = fetch_muni_fallback(kind, kausi, choice)
            except (Exception, SystemExit) as e:  # noqa: BLE001 — varadata on
                # valinnaista: epäonnistuminen ei saa kaataa koko ajoa.
                print(f"VAROITUS: kuntatason {kind} eivät saatavilla ({e}). "
                      f"Peitetyt alueet jäävät tältä osin ilman dataa.")
                continue
            muni_info[kind] = info
            if kind == "hinnat":
                muni_prices = data
            else:
                muni_rents = data
        if muni_info:
            print("[kuntataso] Varadata: " + "; ".join(
                f"{k} {v['alueita']} alueelle" for k, v in muni_info.items()))

    if args.intermediate:
        with open("prices_rents.json", "w", encoding="utf-8") as f:
            json.dump({"kausi": kausi, "hinnat": prices, "vuokrat": rents,
                       "kunta_hinnat": muni_prices, "kunta_vuokrat": muni_rents},
                      f, ensure_ascii=False, indent=1)
        print("Välitiedosto kirjoitettu: prices_rents.json")

    # --- Vaihe 3: geometria ja yhdistäminen --------------------------------
    feats, postal_key = fetch_geometry()

    out_features = []
    n_brutto = 0
    n_fallback = 0
    for feat in feats:
        props = feat.get("properties", {})
        geom = feat.get("geometry")
        code = str(props.get(postal_key, "")).strip()
        if not re.fullmatch(r"\d{5}", code) or not geom:
            continue

        p = prices.get(code, {})
        r = rents.get(code, {})
        kcode = normalize_kunta(props.get("kunta"))
        label = p.get("label") or r.get("label") or ""
        kunta_nimi = (kunta_from_label(label)
                      or (muni_prices.get(kcode) or {}).get("label")
                      or (muni_rents.get(kcode) or {}).get("label")
                      or props.get("kunta"))
        kname = str(kunta_nimi or "").strip().lower()
        # osa kuntataulukoista käyttää koodeja, osa nimiä -> molemmat avaimet
        mp = muni_prices.get(kcode) or (muni_prices.get(kname) if kname else None)
        mr = muni_rents.get(kcode) or (muni_rents.get(kname) if kname else None)
        hinta, n_kaupat, hinta_taso = with_fallback(p, mp)
        vuokra, n_vuokrat, vuokra_taso = with_fallback(r, mr)

        brutto = brutto_pct(hinta, vuokra)
        taso = None
        if brutto is not None:
            n_brutto += 1
            taso = ("pno" if hinta_taso == "pno" and vuokra_taso == "pno"
                    else "kunta")
            if taso == "kunta":
                n_fallback += 1

        out_features.append({
            "type": "Feature",
            "geometry": simplify_geometry(geom, args.simplify),
            "properties": {
                "posti_alue": code,
                "nimi": props.get("nimi") or nimi_from_label(label) or code,
                "kunta": kunta_nimi,
                "hinta_eur_m2": round(hinta, 0) if hinta is not None else None,
                "vuokra_eur_m2": round(vuokra, 2) if vuokra is not None else None,
                "brutto_pct": brutto,
                "netto_pct": netto_pct(hinta, vuokra),
                "n_kaupat": n_kaupat,
                "n_vuokrat": n_vuokrat,
                # 'pno' = postinumerotason tieto, 'kunta' = kuntatason keskiarvo
                "hinta_taso": hinta_taso,
                "vuokra_taso": vuokra_taso,
                "taso": taso,
                "vakiluku": masked_stat(props.get("he_vakiy")),
                "mediaanitulo": masked_stat(props.get("tr_mtu")),
            },
        })

    fc = {
        "type": "FeatureCollection",
        "metadata": {
            "kausi": kausi,
            "hintakaudet": price_periods,
            "vuokrakaudet": rent_periods,
            "talotyyppi": talotyyppi_text,
            "huoneluku": huoneluku_text,
            "generoitu": _dt.date.today().isoformat(),
            "demo": False,
            "lahde": "Tilastokeskus, StatFin (ashi 13mt, asvu 13eb) ja "
                     "Paavo-postinumeroalueet. Lisenssi CC BY 4.0.",
            "hintataulukko": price_url,
            "vuokrataulukko": rent_url,
            "kuntataso_taydennys": bool(muni_info),
            "kuntataso_taulukot": muni_info or None,
            "oletukset": {
                "hoitovastike_eur_m2_kk": DEFAULT_HOITOVASTIKE,
                "vajaakayttoaste": DEFAULT_VAJAAKAYTTO,
                "varainsiirtovero": DEFAULT_VARAINSIIRTOVERO,
            },
        },
        "features": out_features,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, separators=(",", ":"))

    bruttos = sorted(x["properties"]["brutto_pct"] for x in out_features
                     if x["properties"]["brutto_pct"] is not None)
    med = bruttos[len(bruttos) // 2] if bruttos else None
    print(f"\nValmis: {args.out}")
    print(f"  Alueita kartalla: {len(out_features)}")
    print(f"  Bruttotuotto laskettavissa: {n_brutto} alueella")
    if n_fallback:
        print(f"  …joista kuntatason keskiarvolla täydennettyjä: {n_fallback} "
              f"(merkitty taso='kunta')")
    if bruttos:
        print(f"  Brutto min/mediaani/max: {bruttos[0]} / {med} / {bruttos[-1]} % "
              f"(järkevä haarukka kaupungeissa ~3–6 %)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
