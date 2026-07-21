#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_data.py — Vanhojen osakeasuntojen bruttovuokratuotto postinumeroalueittain.

Hakee Tilastokeskuksen avoimesta datasta (CC BY 4.0):
  1) Vanhojen osakeasuntojen neliöhinnat postinumeroittain (statfin_ashi_pxt_13mt)
  2) Vapaarahoitteisten vuokra-asuntojen keskineliövuokrat (statfin_asvu_pxt_13eb)
  3) Postinumeroalueiden geometriat + Paavo-taustatiedot (geo.stat.fi WFS)

Yhdistää postinumerolla, laskee brutto- ja nettovuokratuoton ja kirjoittaa
staattisen postal_yields.geojson-tiedoston frontendille.

Käyttö (rakennusjärjestyksen mukaisesti):
  python fetch_data.py --test 00120        # Vaihe 1: yhden postinumeron arvot
  python fetch_data.py --intermediate      # Vaihe 2: tallenna välitiedosto
  python fetch_data.py                     # Vaiheet 2–3: koko maa -> GeoJSON
  python fetch_data.py --kausi 2026Q1      # pakota tietty vuosineljännes
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
PRICE_TABLE_CANDIDATES = [
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/statfin_ashi_pxt_13mt.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/statfin_ashi_pxt_13mt.px",
    "https://statfin.stat.fi/PxWeb/api/v1/fi/StatFin/ashi/statfin_ashi_pxt_13mt.px",
]
RENT_TABLE_CANDIDATES = [
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/asvu/statfin_asvu_pxt_13eb.px",
    "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/statfin_asvu_pxt_13eb.px",
    "https://statfin.stat.fi/PxWeb/api/v1/fi/StatFin/asvu/statfin_asvu_pxt_13eb.px",
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
            raise
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

def resolve_table(candidates, label):
    """Palauttaa (url, metatiedot) ensimmäiselle toimivalle API-osoitteelle."""
    errors = []
    for url in candidates:
        try:
            meta = get_json(url)
            if isinstance(meta, dict) and "variables" in meta:
                print(f"[{label}] Taulukko löytyi: {url}")
                return url, meta
            errors.append(f"{url}: odottamaton vastaus")
        except Exception as e:  # noqa: BLE001 - raportoidaan kootusti
            errors.append(f"{url}: {e}")
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


def pick_value(var, include, exclude=()):
    """Palauttaa (koodi, teksti) arvolle, jonka teksti täsmää hakusanoihin."""
    for code, text in zip(var["values"], var["valueTexts"]):
        t = text.lower()
        if any(n.lower() in t for n in include) and not any(
            x.lower() in t for x in exclude
        ):
            return code, text
    raise SystemExit(
        f"Arvoa ei löytynyt muuttujasta {var.get('code')} hakusanoilla {include}. "
        f"Saatavilla: {var['valueTexts']}"
    )


TALOTYYPPI_HAKUSANAT = {
    "yhteensa": ["yhteensä"],
    "kerrostalot": ["kerrostalo"],
    "rivitalot": ["rivitalo"],
}
HUONELUKU_HAKUSANAT = {
    "yhteensa": ["yhteensä"],
    "yksiot": ["yksiö"],
    "kaksiot": ["kaksio"],
    "kolmiot": ["kolme", "3h"],
}


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
            pos = positions[dim_id].get(coords[dim_id])
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

def fetch_pxweb(url, meta, kausi, postal_codes, class_var, class_code,
                value_code, count_code, label):
    """Hakee yhden taulukon yhdelle vuosineljännekselle.

    Palauttaa: dict postinumero -> {"arvo": float|None, "n": int|None,
                                     "label": "00120 Punavuori (Helsinki)"}
    """
    tvar = time_variable(meta)
    pvar = find_variable(meta, "postinumero")
    ivar = find_variable(meta, "tiedot")

    query = {
        "query": [
            {"code": tvar["code"],
             "selection": {"filter": "item", "values": [kausi]}},
            {"code": pvar["code"],
             "selection": {"filter": "item", "values": postal_codes}},
            {"code": class_var["code"],
             "selection": {"filter": "item", "values": [class_code]}},
            {"code": ivar["code"],
             "selection": {"filter": "item", "values": [value_code, count_code]}},
        ],
        "response": {"format": "json-stat2"},
    }
    print(f"[{label}] Haetaan {len(postal_codes)} postinumeroa, kausi {kausi}…")
    ds = post_json(url, query)
    get = jsonstat_reader(ds)
    labels = category_labels(ds, pvar["code"])

    out = {}
    for code in postal_codes:
        if not re.fullmatch(r"\d{5}", code):
            continue  # ohita mahdolliset koostealueet
        coords = {tvar["code"]: kausi, pvar["code"]: code,
                  class_var["code"]: class_code}
        arvo = get(**coords, **{ivar["code"]: value_code})
        n = get(**coords, **{ivar["code"]: count_code})
        out[code] = {
            "arvo": float(arvo) if arvo is not None else None,
            "n": int(n) if n is not None else None,
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
    ap.add_argument("--kausi", help="Vuosineljännes, esim. 2026Q1. "
                                    "Oletus: uusin molemmista taulukoista löytyvä.")
    ap.add_argument("--talotyyppi", default="yhteensa",
                    choices=sorted(TALOTYYPPI_HAKUSANAT))
    ap.add_argument("--huoneluku", default="yhteensa",
                    choices=sorted(HUONELUKU_HAKUSANAT))
    ap.add_argument("--simplify", type=float, default=0.0005,
                    help="Geometrian yksinkertaistustoleranssi asteina "
                         "(oletus 0.0005 ≈ 40 m; 0 = ei yksinkertaistusta).")
    ap.add_argument("--intermediate", action="store_true",
                    help="Tallenna välitiedosto prices_rents.json (vaihe 2).")
    ap.add_argument("--out", default="postal_yields.geojson")
    args = ap.parse_args()

    # --- Metatiedot ja arvokoodit ------------------------------------------
    price_url, price_meta = resolve_table(PRICE_TABLE_CANDIDATES, "hinnat")
    rent_url, rent_meta = resolve_table(RENT_TABLE_CANDIDATES, "vuokrat")

    talotyyppi_var = find_variable(price_meta, "talotyyppi")
    talotyyppi_code, talotyyppi_text = pick_value(
        talotyyppi_var, TALOTYYPPI_HAKUSANAT[args.talotyyppi])
    p_tiedot = find_variable(price_meta, "tiedot")
    price_value_code, price_value_text = pick_value(
        p_tiedot, ["neliöhinta"], exclude=["lukumäärä", "muutos", "indeksi"])
    price_count_code, _ = pick_value(p_tiedot, ["lukumäärä"])

    huoneluku_var = find_variable(rent_meta, "huoneluku", "huoneistotyyppi")
    huoneluku_code, huoneluku_text = pick_value(
        huoneluku_var, HUONELUKU_HAKUSANAT[args.huoneluku])
    r_tiedot = find_variable(rent_meta, "tiedot")
    rent_value_code, rent_value_text = pick_value(
        r_tiedot, ["vuokra"], exclude=["lukumäärä", "muutos", "indeksi"])
    rent_count_code, _ = pick_value(r_tiedot, ["lukumäärä"])

    print(f"[hinnat]  Talotyyppi: {talotyyppi_text!r}, tieto: {price_value_text!r}")
    print(f"[vuokrat] Huoneluku: {huoneluku_text!r}, tieto: {rent_value_text!r}")

    # --- Vuosineljännes -----------------------------------------------------
    price_quarters = set(time_variable(price_meta)["values"])
    rent_quarters = set(time_variable(rent_meta)["values"])
    common = sorted(price_quarters & rent_quarters)
    if not common:
        raise SystemExit("Taulukoilla ei ole yhteisiä vuosineljänneksiä!")
    kausi = args.kausi or common[-1]
    if kausi not in common:
        raise SystemExit(f"Kausi {kausi} ei ole molemmissa taulukoissa. "
                         f"Uusimmat yhteiset: {common[-4:]}")
    print(f"Käytetään vuosineljännestä: {kausi}")

    price_codes = [c for c in find_variable(price_meta, "postinumero")["values"]
                   if re.fullmatch(r"\d{5}", c)]
    rent_codes = [c for c in find_variable(rent_meta, "postinumero")["values"]
                  if re.fullmatch(r"\d{5}", c)]

    # --- Vaihe 1: yhden postinumeron testi ---------------------------------
    if args.test:
        code = args.test
        prices = fetch_pxweb(price_url, price_meta, kausi,
                             [code] if code in price_codes else price_codes[:1],
                             talotyyppi_var, talotyyppi_code,
                             price_value_code, price_count_code, "hinnat")
        rents = fetch_pxweb(rent_url, rent_meta, kausi,
                            [code] if code in rent_codes else rent_codes[:1],
                            huoneluku_var, huoneluku_code,
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
    prices = fetch_pxweb(price_url, price_meta, kausi, price_codes,
                         talotyyppi_var, talotyyppi_code,
                         price_value_code, price_count_code, "hinnat")
    rents = fetch_pxweb(rent_url, rent_meta, kausi, rent_codes,
                        huoneluku_var, huoneluku_code,
                        rent_value_code, rent_count_code, "vuokrat")

    if args.intermediate:
        with open("prices_rents.json", "w", encoding="utf-8") as f:
            json.dump({"kausi": kausi, "hinnat": prices, "vuokrat": rents},
                      f, ensure_ascii=False, indent=1)
        print("Välitiedosto kirjoitettu: prices_rents.json")

    # --- Vaihe 3: geometria ja yhdistäminen --------------------------------
    feats, postal_key = fetch_geometry()

    out_features = []
    n_brutto = 0
    for feat in feats:
        props = feat.get("properties", {})
        geom = feat.get("geometry")
        code = str(props.get(postal_key, "")).strip()
        if not re.fullmatch(r"\d{5}", code) or not geom:
            continue

        p = prices.get(code, {})
        r = rents.get(code, {})
        hinta = p.get("arvo")
        vuokra = r.get("arvo")
        label = p.get("label") or r.get("label") or ""

        brutto = brutto_pct(hinta, vuokra)
        if brutto is not None:
            n_brutto += 1

        out_features.append({
            "type": "Feature",
            "geometry": simplify_geometry(geom, args.simplify),
            "properties": {
                "posti_alue": code,
                "nimi": props.get("nimi") or nimi_from_label(label) or code,
                "kunta": kunta_from_label(label) or props.get("kunta"),
                "hinta_eur_m2": round(hinta, 0) if hinta is not None else None,
                "vuokra_eur_m2": round(vuokra, 2) if vuokra is not None else None,
                "brutto_pct": brutto,
                "netto_pct": netto_pct(hinta, vuokra),
                "n_kaupat": p.get("n"),
                "n_vuokrat": r.get("n"),
                "vakiluku": masked_stat(props.get("he_vakiy")),
                "mediaanitulo": masked_stat(props.get("tr_mtu")),
            },
        })

    fc = {
        "type": "FeatureCollection",
        "metadata": {
            "kausi": kausi,
            "talotyyppi": talotyyppi_text,
            "huoneluku": huoneluku_text,
            "generoitu": _dt.date.today().isoformat(),
            "demo": False,
            "lahde": "Tilastokeskus, StatFin (ashi 13mt, asvu 13eb) ja "
                     "Paavo-postinumeroalueet. Lisenssi CC BY 4.0.",
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
    if bruttos:
        print(f"  Brutto min/mediaani/max: {bruttos[0]} / {med} / {bruttos[-1]} % "
              f"(järkevä haarukka kaupungeissa ~3–6 %)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
