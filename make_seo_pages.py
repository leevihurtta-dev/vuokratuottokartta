#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_seo_pages.py – Generoi staattiset SEO-sivut postal_yields.geojson-datasta.

Tuottaa:
  alue/<postinumero>/index.html   – oma sivu jokaiselle alueelle, jolla on tuotto
  kunta/<kunta-slug>/index.html   – kuntahakemisto (alueet tuottojärjestyksessä)
  alueet/index.html               – kaikkien kuntien hakemisto + top-listat
  sitemap.xml, robots.txt         – hakukoneita varten

Hakukoneoptimointi: jokaisella aluesivulla on ainutlaatuinen sanallinen
yhteenveto datasta, JSON-LD-rakenteinen data (Dataset + BreadcrumbList),
Open Graph -tiedot ja lähialueiden ristiinlinkitys – nämä parantavat
indeksointia ja pitkän hännän hakunäkyvyyttä.

Ajetaan GitHub Actions -workflow'ssa heti fetch_data.py:n perään, jolloin
sivut päivittyvät automaattisesti aina datan mukana.

Käyttö: python make_seo_pages.py
"""
import datetime as _dt
import html
import json
import os
import re
import shutil
import sys

BASE_URL = "https://vuokratuottokartta.fi"
DATA_FILE = "postal_yields.geojson"
OUT_DIRS = ("alue", "kunta", "alueet")

CSS = """
:root{--paper:#f6f7f4;--ink:#1d2733;--soft:#55606c;--petrol:#0e5f57;
--line:#d8dcd6;--warn-bg:#fdf3dd;--warn:#8a5a12}
*{box-sizing:border-box}body{margin:0;font-family:"Space Grotesk",system-ui,
sans-serif;background:var(--paper);color:var(--ink);line-height:1.55}
main{max-width:760px;margin:0 auto;padding:20px 16px 40px}
a{color:var(--petrol)}h1{font-size:26px;line-height:1.2;margin:8px 0 2px}
.crumb{font-size:13px;color:var(--soft)}.lead{color:var(--soft);margin:2px 0 18px}
.big{font-size:40px;font-weight:700;color:var(--petrol);margin:6px 0 2px}
.biglabel{font-size:13px;color:var(--soft);text-transform:uppercase;
letter-spacing:.06em}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14.5px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;
color:var(--soft)}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.btn{display:inline-block;background:var(--petrol);color:#fff;font-weight:600;
padding:11px 18px;border-radius:8px;text-decoration:none;margin:10px 0}
.note{background:var(--warn-bg);color:var(--warn);border-radius:8px;
padding:10px 12px;font-size:13.5px;margin:14px 0}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);
font-size:12.5px;color:var(--soft)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));
gap:6px;padding:0;margin:12px 0;list-style:none}
.grid a{text-decoration:none}
main.wide{max-width:1000px}
#q{width:100%;max-width:420px;font-size:15px;padding:9px 11px;
border:1px solid var(--line);border-radius:8px;background:#fff;margin:6px 0}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
th.sortable{cursor:pointer;user-select:none;white-space:nowrap}
th.sortable:hover{color:var(--ink)}
th.sorted-asc::after{content:" ▲";font-size:9px}
th.sorted-desc::after{content:" ▼";font-size:9px}
#tbl td a{text-decoration:none}
#tbl tbody tr:hover{background:#eef2ee}
.mk{color:var(--soft);font-weight:400;font-size:11px}
@media (max-width:640px){main.wide{padding-left:10px;padding-right:10px}
#tbl{font-size:12.5px}th,td{padding:6px 7px}}
"""


def esc(s):
    return html.escape(str(s if s is not None else ""))


def prose_summary(p, kunta_areas, national_median):
    """Rakentaa muutaman lauseen sanallisen yhteenvedon alueen luvuista.
    Ainutlaatuinen teksti parantaa hakukonenäkyvyyttä ja auttaa lukijaa."""
    nimi = p.get("nimi") or p["posti_alue"]
    kunta = p.get("kunta") or ""
    b = p.get("brutto_pct")
    sentences = []

    # 1) Suhde kunnan muihin alueisiin.
    same = sorted((a["brutto_pct"] for a in kunta_areas
                   if a.get("brutto_pct") is not None), reverse=True)
    if b is not None and len(same) >= 3:
        rank = same.index(b) + 1 if b in same else None
        kmed = same[len(same) // 2]
        if b >= kmed * 1.15:
            taso = "selvästi kunnan keskitasoa korkeampi"
        elif b <= kmed * 0.85:
            taso = "kunnan keskitasoa matalampi"
        else:
            taso = "lähellä kunnan keskitasoa"
        rankstr = (f" ja sijoittuu {rank}. korkeimmaksi kunnan "
                   f"{len(same)} alueesta" if rank and rank <= 5 else "")
        sentences.append(
            f"Alueen {fnum(b, 2)} %:n bruttovuokratuotto on {taso}"
            f"{rankstr}.")

    # 2) Mikä ajaa tuottoa (hinta vs. vuokra).
    h, v = p.get("hinta_eur_m2"), p.get("vuokra_eur_m2")
    if h is not None and v is not None:
        if h >= 5000:
            sentences.append(
                f"Korkea neliöhinta ({fnum(h)} €/m²) painaa tuottoa, "
                f"vaikka {fnum(v, 2)} €/m²/kk:n keskivuokra on kysytyllä "
                f"tasolla.")
        elif h <= 2200:
            sentences.append(
                f"Matala neliöhinta ({fnum(h)} €/m²) nostaa laskennallista "
                f"tuottoa; tällaisilla alueilla kannattaa kuitenkin arvioida "
                f"arvonkehitys ja vuokrausaste erikseen.")
        else:
            sentences.append(
                f"Neliöhinta on {fnum(h)} €/m² ja keskineliövuokra "
                f"{fnum(v, 2)} €/m²/kk.")

    # 2b) Alueen väestö ja tulotaso. Nämä ovat aidosti postinumerokohtaisia
    # lukuja, joten kappale erottaa myös ne sivut, joilla tuotto perustuu
    # kunnan keskiarvoon – poistaa lähes identtisen kaksoissisällön.
    vak = p.get("vakiluku")
    tulo = p.get("mediaanitulo")
    if vak:
        if vak < 500:
            koko = "asukasluvultaan pieni"
        elif vak < 2000:
            koko = "melko pieni"
        elif vak < 7000:
            koko = "keskikokoinen"
        else:
            koko = "suuri"
        vaklause = (f"{nimi} on {koko} postinumeroalue "
                    f"({fnum(vak)} asukasta).")
        tulot = sorted(a["mediaanitulo"] for a in kunta_areas
                       if a.get("mediaanitulo"))
        if tulo and len(tulot) >= 3:
            tmed = tulot[len(tulot) // 2]
            if tulo >= tmed * 1.12:
                tulolause = (f" Talouksien mediaanitulo ({fnum(tulo)} €/v) on "
                             f"kunnan {kunta} korkeimpia, mikä tukee "
                             f"vuokrakysyntää.")
            elif tulo <= tmed * 0.88:
                tulolause = (f" Talouksien mediaanitulo ({fnum(tulo)} €/v) jää "
                             f"kunnan {kunta} keskitason alapuolelle.")
            else:
                tulolause = (f" Talouksien mediaanitulo on {fnum(tulo)} €/v, "
                             f"lähellä kunnan keskitasoa.")
        elif tulo:
            tulolause = f" Talouksien mediaanitulo on {fnum(tulo)} €/v."
        else:
            tulolause = ""
        sentences.append(vaklause + tulolause)

    # 3) Suhde koko maahan.
    if b is not None and national_median is not None:
        if b >= national_median + 1:
            sentences.append(
                "Koko maan mittakaavassa tuotto on keskimääräistä korkeampi.")
        elif b <= national_median - 1:
            sentences.append(
                "Koko maan mittakaavassa tuotto on maltillinen, mikä on "
                "tyypillistä kalliimmille kasvualueille.")

    # 4) Luotettavuusvaroitus.
    if p.get("taso") == "kunta":
        sentences.append(
            "Postinumerotason vuokratieto on tietosuojasyistä peitetty, joten "
            "laskelmassa on käytetty kunnan keskiarvoa – tulkitse luku "
            "suuntaa-antavana.")
    elif p.get("vuokra_taso") == "malli":
        sentences.append(
            "Postinumerotason vuokratietoa ei julkaista tälle alueelle, joten "
            "keskineliövuokra ja siitä laskettu tuotto ovat arvioita: vuokra on "
            "mallinnettu alueen toteutuneesta neliöhinnasta. Neliöhinta ja "
            "kauppamäärä ovat aitoa postinumerotason dataa.")
    elif ((isinstance(p.get("n_kaupat"), (int, float)) and p["n_kaupat"] < 10)
          or (isinstance(p.get("n_vuokrat"), (int, float))
              and p["n_vuokrat"] < 30)):
        sentences.append(
            "Havaintojen määrä alueella on pieni, joten keskiarvot ovat "
            "tavallista epävarmempia.")

    return " ".join(sentences)


def slugify(name):
    s = str(name or "").strip().lower()
    s = (s.replace("ä", "a").replace("ö", "o").replace("å", "a")
          .replace("é", "e").replace("ü", "u"))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "muu"


def fnum(v, dec=0, unit=""):
    if v is None:
        return "–"
    s = f"{v:,.{dec}f}".replace(",", " ").replace(".", ",")
    return s + ("\u00a0" + unit if unit else "")


def page(title, description, canonical, body, breadcrumb="", jsonld=None,
         wide=False):
    ld = ""
    if jsonld:
        ld = ('<script type="application/ld+json">'
              + json.dumps(jsonld, ensure_ascii=False) + "</script>")
    return f"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{canonical}">
<meta property="og:locale" content="fi_FI">
<meta property="og:image" content="{BASE_URL}/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{BASE_URL}/og-image.png">
{ld}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
<script data-goatcounter="https://lerpou.goatcounter.com/count"
        async src="//gc.zgo.at/count.js"></script>
</head>
<body>
<main{' class="wide"' if wide else ''}>
<p class="crumb"><a href="/">Vuokratuottokartta</a>{breadcrumb}</p>
{body}
<footer>
Luvut ovat Tilastokeskuksen avoimesta datasta laskettuja alueellisia
keskiarvoja (CC BY 4.0) eivätkä ole sijoitusneuvontaa. Bruttotuotto =
keskineliövuokra × 12 ÷ neliöhinta. Yksittäisten asuntojen tuotot voivat
poiketa merkittävästi alueen keskiarvosta.
</footer>
</main>
</body>
</html>"""


def taso_mark(p):
    if p.get("taso") == "kunta":
        return " ※"
    if p.get("taso") == "malli":
        return " △"
    return ""


def _centroid(geom):
    """Karkea keskipiste (bbox-keskikohta) mille tahansa Polygon/MultiPolygon
    -geometrialle. Riittää naapurialueiden löytämiseen."""
    if not geom:
        return None
    xs, ys = [], []

    def walk(c):
        if isinstance(c, (int, float)):
            return
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for x in c:
                walk(x)
    walk(geom.get("coordinates"))
    if not xs:
        return None
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _nearest(code, centroids, by_code, k=6):
    """Palauttaa k lähintä aluetta (properties-dict) etäisyyden mukaan.
    Etäisyys lasketaan keskipisteiden välillä; leveysasteen kosini korjaa
    Suomen mittasuhteet karkeasti."""
    import math
    here = centroids.get(code)
    if not here:
        return []
    lon0, lat0 = here
    kx = math.cos(math.radians(lat0))
    dists = []
    for other, c in centroids.items():
        if other == code:
            continue
        dx = (c[0] - lon0) * kx
        dy = c[1] - lat0
        dists.append((dx * dx + dy * dy, other))
    dists.sort()
    out = []
    for _, other in dists[:k]:
        p = by_code.get(other)
        if p:
            out.append(p)
    return out


def area_extras(p, kausi, national_median, kunta_median):
    """Rakentaa aluesivun lisäosiot: esimerkkilaskelma (45 m² kaksio),
    vertailu maan/kunnan keskiarvoon, takaisinmaksuaika ja usein kysyttyä.
    Palauttaa (html, faq_jsonld). Näytetään vain, jos hinta ja vuokra ovat
    käytettävissä."""
    nimi = p.get("nimi") or p["posti_alue"]
    kunta = p.get("kunta") or ""
    b = p.get("brutto_pct")
    netto = p.get("netto_pct")
    h = p.get("hinta_eur_m2")
    v = p.get("vuokra_eur_m2")
    if b is None or not h or not v:
        return "", None

    # --- Esimerkkilaskelma: 45 m² kaksio ---
    M2 = 45
    hinta_yht = h * M2
    vuokra_kk = v * M2
    vuokra_v = vuokra_kk * 12
    # nettovuokra (samat oletukset kuin sivun netto_pct: hoito 4,5 €/m²/kk,
    # vajaakäyttö 5 %, ei rahoitusvastiketta/remontteja).
    hoito_kk = 4.5 * M2
    netto_v = (vuokra_kk - hoito_kk) * 12 * 0.95
    esimerkki = f"""
<h2>Esimerkkilaskelma: 45 m² kaksio</h2>
<p>Havainnollistava esimerkki alueen keskiarvoilla. Todellinen kohde voi
poiketa merkittävästi – tämä auttaa hahmottamaan luvut euroina.</p>
<table>
<tr><th>Velaton hinta (45 m²)</th><td class=num>{fnum(hinta_yht, 0, "€")}</td></tr>
<tr><th>Vuokra kuukaudessa</th><td class=num>{fnum(vuokra_kk, 0, "€/kk")}</td></tr>
<tr><th>Vuokratulo vuodessa</th><td class=num>{fnum(vuokra_v, 0, "€/v")}</td></tr>
<tr><th>Bruttovuokratuotto</th><td class=num>{fnum(b, 2, "%")}</td></tr>
<tr><th>Arvioitu nettotulo vuodessa*</th><td class=num>{fnum(netto_v, 0, "€/v")}</td></tr>
<tr><th>Nettovuokratuotto*</th><td class=num>{fnum(netto, 2, "%")}</td></tr>
</table>
<p class="about-links">* Oletuksin: hoitovastike 4,5 €/m²/kk, vajaakäyttö 5 %.
Ei sisällä remontteja, rahoitusvastiketta, rahoituskuluja eikä
varainsiirtoveroa. Nettotuoton oletuksia voi säätää
<a href="/#{p['posti_alue']}">kartalla</a>.</p>"""

    # --- Vertailu ---
    def suhde(x, ref, mika):
        if ref is None:
            return ""
        ero = x - ref
        if ero >= 0.5:
            return f"{mika} keskitasoa ({fnum(ref, 2)} %) korkeampi"
        if ero <= -0.5:
            return f"{mika} keskitasoa ({fnum(ref, 2)} %) matalampi"
        return f"lähellä {mika} keskitasoa ({fnum(ref, 2)} %)"
    vertailut = []
    s1 = suhde(b, kunta_median, f"kunnan {kunta}")
    s2 = suhde(b, national_median, "koko maan")
    if s1:
        vertailut.append(s1)
    if s2:
        vertailut.append(s2)
    vertailu = ""
    if vertailut:
        # Kontekstilause: koko maan mediaania nostavat halpojen, usein
        # supistuvien paikkakuntien korkeat bruttotuotot. Ilman tätä
        # kasvukeskusten sivut näyttäisivät perusteettomasti "alle keskitason".
        konteksti = ""
        if national_median is not None:
            if b >= national_median + 3:
                konteksti = (" Näin korkea bruttotuotto liittyy tyypillisesti "
                             "mataliin asuntohintoihin; suurin laskennallinen "
                             "tuotto löytyy usein muuttotappiopaikkakunnilta, "
                             "joilla arvonkehitys ja asunnon myytävyys ovat "
                             "heikkoja – arvioi ne erikseen.")
            elif b <= national_median - 2:
                konteksti = (" Koko maan mediaania nostavat halpojen, usein "
                             "väestöltään supistuvien paikkakuntien korkeat "
                             "bruttotuotot, joten kasvukeskusten matalampi luku "
                             "on odotettua eikä sellaisenaan kerro heikosta "
                             "sijoituksesta.")
        vertailu = (f'<h2>Miten tuotto vertautuu?</h2><p>Alueen '
                    f'{fnum(b, 2)} %:n bruttovuokratuotto on '
                    + " ja ".join(vertailut) + "." + konteksti + "</p>")

    # --- Takaisinmaksuaika ---
    vuodet = round(100 / b, 0) if b else None
    netto_vuodet = round(100 / netto, 0) if netto and netto > 0 else None
    takaisin = ""
    if vuodet:
        nettolause = (f" Nettotuotolla ({fnum(netto, 2)} %) vastaava aika on "
                      f"noin {fnum(netto_vuodet, 0)} vuotta."
                      if netto_vuodet else "")
        takaisin = (f'<h2>Takaisinmaksuaika</h2><p>Bruttovuokratuotolla '
                    f'{fnum(b, 2)} % sijoitus maksaisi itsensä takaisin '
                    f'pelkillä vuokratuloilla noin {fnum(vuodet, 0)} vuodessa '
                    f'(ennen kuluja ja veroja).{nettolause} Luku on '
                    f'suuntaa-antava eikä huomioi arvonmuutosta tai '
                    f'rahoitusta.</p>')

    # --- Usein kysyttyä (myös FAQ-rakenteinen data) ---
    faqs = [
        (f"Mikä on vuokratuotto alueella {nimi} ({p['posti_alue']})?",
         f"Vanhojen osakeasuntojen keskimääräinen bruttovuokratuotto alueella "
         f"{nimi} on {fnum(b, 2)} % (tilastovuosi {kausi}, lähde "
         f"Tilastokeskus). Nettovuokratuotto on tyypillisillä oletuksilla "
         f"noin {fnum(netto, 2)} %."),
        (f"Paljonko asunnot maksavat alueella {nimi}?",
         f"Vanhojen osakeasuntojen keskimääräinen neliöhinta on "
         f"{fnum(h, 0)} €/m² ja keskineliövuokra {fnum(v, 2)} €/m²/kk. "
         f"Esimerkiksi 45 m² kaksio maksaisi keskimäärin noin "
         f"{fnum(h * 45, 0)} €."),
        ("Onko korkea vuokratuotto aina hyvä asia?",
         "Ei välttämättä. Korkea laskennallinen tuotto liittyy usein "
         "matalampiin hintoihin ja voi kertoa hitaammasta arvonkehityksestä "
         "tai suuremmasta vuokrausriskistä. Tuotto kannattaa suhteuttaa "
         "alueen väestö- ja hintakehitykseen sekä vuokrakysyntään."),
    ]
    faq_html = "<h2>Usein kysyttyä</h2>" + "".join(
        f"<h3 style=\"font-size:14px;margin:12px 0 3px\">{esc(q)}</h3>"
        f"<p>{esc(a)}</p>" for q, a in faqs)
    faq_jsonld = {
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in faqs],
    }

    html_out = esimerkki + vertailu + takaisin + faq_html
    return html_out, faq_jsonld


def area_page(p, kausi, kunta_areas, national_median, neighbours):
    code, nimi, kunta = p["posti_alue"], p.get("nimi") or p["posti_alue"], p.get("kunta") or ""
    kslug = slugify(kunta)
    title = f"Vuokratuotto {nimi} ({code}), {kunta} – {fnum(p['brutto_pct'], 2)} %"
    desc = (f"{nimi} ({code}), {kunta}: bruttovuokratuotto "
            f"{fnum(p['brutto_pct'], 2)} %, neliöhinta {fnum(p['hinta_eur_m2'])} €/m², "
            f"keskineliövuokra {fnum(p['vuokra_eur_m2'], 2)} €/m²/kk. "
            f"Tilastovuosi {kausi}, lähde Tilastokeskus.")
    rows = [
        ("Bruttovuokratuotto", fnum(p["brutto_pct"], 2, "%") + taso_mark(p)),
        ("Nettotuotto (oletuksilla)", fnum(p["netto_pct"], 2, "%")),
        ("Neliöhinta", fnum(p["hinta_eur_m2"], 0, "€/m²")
         + (" ※" if p.get("hinta_taso") == "kunta" else "")),
        ("Keskineliövuokra", fnum(p["vuokra_eur_m2"], 2, "€/m²/kk")
         + (" ※" if p.get("vuokra_taso") == "kunta"
            else " △" if p.get("vuokra_taso") == "malli" else "")),
        ("Asuntokauppoja", fnum(p["n_kaupat"])),
        ("Vuokrahavaintoja", fnum(p["n_vuokrat"])),
        ("Väkiluku", fnum(p["vakiluku"])),
        ("Talouksien mediaanitulo", fnum(p["mediaanitulo"], 0, "€/v")),
    ]
    trs = "\n".join(f"<tr><th>{esc(a)}</th><td class=num>{b}</td></tr>"
                    for a, b in rows)

    summary = prose_summary(p, kunta_areas, national_median)
    summary_html = f"<p>{esc(summary)}</p>" if summary else ""

    note = ""
    if p.get("taso") == "kunta":
        note = ('<p class="note">※ Postinumerotason tieto on tietosuojasyistä '
                'peitetty, joten merkityissä luvuissa on käytetty koko kunnan '
                'keskiarvoa. Se tasoittaa alueiden välisiä eroja – tulkitse '
                'suuntaa-antavana.</p>')
    elif p.get("taso") == "malli":
        note = ('<p class="note">△ Postinumerotason vuokratietoa ei julkaista '
                'tälle alueelle, joten keskineliövuokra ja siitä laskettu '
                'tuotto ovat arvioita. Vuokra on mallinnettu alueen '
                'toteutuneesta neliöhinnasta kunnan sisäisellä '
                'hinta–vuokra-suhteella; neliöhinta ja kauppamäärä ovat aitoa '
                'postinumerotason dataa. Tulkitse tuotto suuntaa-antavana.</p>')

    nb_html = ""
    if neighbours:
        items = "".join(
            f'<li><a href="/alue/{n["posti_alue"]}/">{n["posti_alue"]} '
            f'{esc(n.get("nimi") or "")}</a> – {fnum(n["brutto_pct"], 2, "%")}'
            f'</li>' for n in neighbours)
        nb_html = (f'<h2>Lähialueet</h2><ul class="grid">{items}</ul>')

    # Lisäosiot: esimerkkilaskelma, vertailu, takaisinmaksuaika, usein kysyttyä.
    kb = sorted(a["brutto_pct"] for a in kunta_areas
                if a.get("brutto_pct") is not None)
    kunta_median = kb[len(kb) // 2] if kb else None
    extras_html, faq_jsonld = area_extras(p, kausi, national_median,
                                          kunta_median)

    body = f"""
<h1>{esc(nimi)} <span style="color:var(--petrol)">{esc(code)}</span></h1>
<p class="lead"><a href="/kunta/{kslug}/">{esc(kunta)}</a> · tilastovuosi {esc(kausi)}</p>
<p class="biglabel">Bruttovuokratuotto</p>
<p class="big">{fnum(p["brutto_pct"], 2, "%")}</p>
<a class="btn" href="/#{esc(code)}">Näytä kartalla</a>
{summary_html}
<table>{trs}</table>
{note}
{extras_html}
{nb_html}
<p>Vertaa muihin alueisiin: <a href="/kunta/{kslug}/">kaikki kunnan
{esc(kunta)} postinumeroalueet</a> tai <a href="/alueet/">koko Suomen
hakemisto</a>. Nettotuoton oletuksia (hoitovastike, vajaakäyttö,
varainsiirtovero) voit säätää itse <a href="/#{esc(code)}">kartalla</a>.</p>"""
    bc = f' › <a href="/kunta/{kslug}/">{esc(kunta)}</a> › {esc(code)}'

    canonical = f"{BASE_URL}/alue/{code}/"
    jsonld = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Dataset",
                "name": f"Vuokratuotto {nimi} ({code}), {kunta}",
                "description": desc,
                "url": canonical,
                "isAccessibleForFree": True,
                "creator": {"@type": "Organization",
                            "name": "Vuokratuottokartta"},
                "license": "https://creativecommons.org/licenses/by/4.0/",
                "temporalCoverage": str(kausi),
                "variableMeasured": [
                    {"@type": "PropertyValue",
                     "name": "Bruttovuokratuotto",
                     "value": p.get("brutto_pct"), "unitText": "%"},
                    {"@type": "PropertyValue", "name": "Neliöhinta",
                     "value": p.get("hinta_eur_m2"), "unitText": "EUR/m2"},
                    {"@type": "PropertyValue", "name": "Keskineliövuokra",
                     "value": p.get("vuokra_eur_m2"), "unitText": "EUR/m2/kk"},
                ],
                "spatialCoverage": {
                    "@type": "Place",
                    "name": f"{nimi}, {kunta}",
                    "address": {"@type": "PostalAddress",
                                "postalCode": code,
                                "addressLocality": kunta,
                                "addressCountry": "FI"},
                },
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Etusivu",
                     "item": f"{BASE_URL}/"},
                    {"@type": "ListItem", "position": 2, "name": kunta,
                     "item": f"{BASE_URL}/kunta/{kslug}/"},
                    {"@type": "ListItem", "position": 3,
                     "name": f"{nimi} ({code})", "item": canonical},
                ],
            },
        ],
    }
    if faq_jsonld:
        jsonld["@graph"].append(faq_jsonld)
    return page(title, desc, canonical, body, bc, jsonld)


def kunta_page(kunta, areas, kausi):
    kslug = slugify(kunta)
    areas = sorted(areas, key=lambda p: p["brutto_pct"], reverse=True)
    title = f"Vuokratuotot {kunta} postinumeroittain – {len(areas)} aluetta"
    desc = (f"Asuntojen bruttovuokratuotot kunnassa {kunta} "
            f"postinumeroalueittain, tilastovuosi {kausi}. "
            f"Korkein {fnum(areas[0]['brutto_pct'], 2)} %, "
            f"matalin {fnum(areas[-1]['brutto_pct'], 2)} %.")
    trs = "\n".join(
        f'<tr><td><a href="/alue/{p["posti_alue"]}/">{p["posti_alue"]} '
        f'{esc(p.get("nimi") or "")}</a></td>'
        f'<td class=num>{fnum(p["brutto_pct"], 2, "%")}{taso_mark(p)}</td>'
        f'<td class=num>{fnum(p["hinta_eur_m2"], 0, "€/m²")}</td>'
        f'<td class=num>{fnum(p["vuokra_eur_m2"], 2, "€/m²")}</td>'
        f'<td class=num>{fnum(p["n_kaupat"])}</td></tr>'
        for p in areas)
    body = f"""
<h1>Vuokratuotot: {esc(kunta)}</h1>
<p class="lead">{len(areas)} postinumeroaluetta · tilastovuosi {esc(kausi)} ·
järjestetty bruttotuoton mukaan</p>
<table>
<tr><th>Alue</th><th class=num>Brutto</th><th class=num>Hinta</th>
<th class=num>Vuokra</th><th class=num>Kauppoja</th></tr>
{trs}
</table>
<p class="note">※ = luvussa on käytetty kuntatason keskiarvoa, koska
postinumerotason tieto on peitetty. △ = vuokra ja tuotto on arvioitu alueen
neliöhinnasta (postinumerotason vuokraa ei julkaista).</p>
<p><a class="btn" href="/">Avaa koko kartta</a></p>"""
    bc = f' › {esc(kunta)}'
    return page(title, desc, f"{BASE_URL}/kunta/{kslug}/", body, bc)


def index_page(by_kunta, all_areas, kausi):
    kuntas = "\n".join(
        f'<li><a href="/kunta/{slugify(k)}/">{esc(k)} '
        f'({len(v)})</a></li>'
        for k, v in sorted(by_kunta.items()))
    title = "Vuokratuotot postinumeroittain – koko Suomen lista ja hakemisto"
    desc = (f"Selaa asuntojen bruttovuokratuottoja koko Suomessa "
            f"postinumeroalueittain listana: järjestä tuoton, hinnan, vuokran "
            f"tai nimen mukaan. {len(all_areas)} aluetta, {len(by_kunta)} "
            f"kuntaa. Tilastovuosi {kausi}, lähde Tilastokeskus.")

    # Kaikki alueet kompaktina datana selainpuolen lajittelua varten.
    rows_data = [{
        "c": p["posti_alue"],
        "n": p.get("nimi") or "",
        "k": p.get("kunta") or "",
        "b": p.get("brutto_pct"),
        "net": p.get("netto_pct"),
        "h": p.get("hinta_eur_m2"),
        "v": p.get("vuokra_eur_m2"),
        "kp": p.get("n_kaupat") if isinstance(p.get("n_kaupat"), (int, float)) else None,
        "t": 1 if p.get("taso") == "kunta" else 0,
    } for p in all_areas]
    data_json = json.dumps(rows_data, ensure_ascii=False, separators=(",", ":"))

    body = f"""
<h1>Vuokratuotot alueittain – lista</h1>
<p class="lead">{len(all_areas)} postinumeroaluetta · {len(by_kunta)} kuntaa ·
tilastovuosi {esc(kausi)}. Järjestä saraketta klikkaamalla.</p>
<p><a class="btn" href="/">Avaa kartta</a></p>

<input id="q" type="search" placeholder="Suodata: postinumero, alue tai kunta…"
       autocomplete="off" aria-label="Suodata listaa">
<p id="count" class="lead"></p>

<div class="table-wrap">
<table id="tbl">
<thead><tr>
<th data-k="c" class="sortable">Postinumero</th>
<th data-k="n" class="sortable">Alue</th>
<th data-k="k" class="sortable">Kunta</th>
<th data-k="b" class="sortable num sorted-desc">Brutto&nbsp;%</th>
<th data-k="net" class="sortable num">Netto&nbsp;%</th>
<th data-k="h" class="sortable num">€/m²</th>
<th data-k="v" class="sortable num">Vuokra</th>
<th data-k="kp" class="sortable num">Kauppoja</th>
</tr></thead>
<tbody id="tbody"></tbody>
</table>
</div>

<h2>Selaa kunnittain</h2>
<ul class="grid">{kuntas}</ul>

<script>
const DATA = {data_json};
const tbody = document.getElementById("tbody");
const q = document.getElementById("q");
const countEl = document.getElementById("count");
let sortKey = "b", sortDir = -1;   // oletus: korkein brutto ensin
const numKeys = new Set(["b","net","h","v","kp"]);

function fmt(x, dec, unit) {{
  if (x === null || x === undefined) return "–";
  const s = Number(x).toLocaleString("fi-FI",
    {{minimumFractionDigits:dec, maximumFractionDigits:dec}});
  return unit ? s + unit : s;
}}
function render() {{
  const term = q.value.trim().toLowerCase();
  let rows = DATA;
  if (term) rows = rows.filter(r =>
    (r.c+" "+r.n+" "+r.k).toLowerCase().includes(term));
  rows = rows.slice().sort((a,b) => {{
    let x=a[sortKey], y=b[sortKey];
    if (numKeys.has(sortKey)) {{
      x = (x===null||x===undefined)?-Infinity:x;
      y = (y===null||y===undefined)?-Infinity:y;
      return (x-y)*sortDir;
    }}
    return String(x).localeCompare(String(y),"fi")*sortDir;
  }});
  countEl.textContent = rows.length + " aluetta";
  const kunta = (r) => r.t ? ' <span class="mk">(kunta)</span>' : '';
  tbody.innerHTML = rows.map(r =>
    `<tr>`+
    `<td><a href="/alue/${{r.c}}/">${{r.c}}</a></td>`+
    `<td><a href="/alue/${{r.c}}/">${{r.n}}</a></td>`+
    `<td>${{r.k}}</td>`+
    `<td class=num>${{fmt(r.b,2," %")}}${{kunta(r)}}</td>`+
    `<td class=num>${{fmt(r.net,2," %")}}</td>`+
    `<td class=num>${{fmt(r.h,0)}}</td>`+
    `<td class=num>${{fmt(r.v,2)}}</td>`+
    `<td class=num>${{fmt(r.kp,0)}}</td>`+
    `</tr>`).join("");
}}
document.querySelectorAll("th.sortable").forEach(th => {{
  th.addEventListener("click", () => {{
    const k = th.dataset.k;
    if (sortKey === k) {{ sortDir = -sortDir; }}
    else {{ sortKey = k; sortDir = numKeys.has(k) ? -1 : 1; }}
    document.querySelectorAll("th").forEach(h =>
      h.classList.remove("sorted-asc","sorted-desc"));
    th.classList.add(sortDir>0 ? "sorted-asc" : "sorted-desc");
    render();
  }});
}});
q.addEventListener("input", render);
render();
</script>"""
    return page(title, desc, f"{BASE_URL}/alueet/", body, " › Alueet",
                wide=True)


def weighted_median(items, value_key="brutto_pct", weight_key="n_kaupat"):
    """Painotettu mediaani. Kauppapainotus (n_kaupat) kuvaa 'tyypillistä
    kaupan kohteena ollutta asuntoa' paremmin kuin polygonimediaani, jota
    vinouttavat lukuisat halvat, vähän vaihdetut muuttotappioalueet, joilla
    laskennallinen bruttotuotto on hyvin korkea. Jos painoja ei ole
    lainkaan, palautuu tavallinen mediaani."""
    data = sorted(((p[value_key], (p.get(weight_key) or 0)) for p in items
                   if p.get(value_key) is not None), key=lambda t: t[0])
    if not data:
        return None
    total = sum(w for _, w in data)
    if total <= 0:
        vals = [v for v, _ in data]
        return vals[len(vals) // 2]
    acc = 0.0
    for val, w in data:
        acc += w
        if acc >= total / 2:
            return val
    return data[-1][0]


def main():
    if not os.path.exists(DATA_FILE):
        raise SystemExit(f"{DATA_FILE} puuttuu – aja ensin fetch_data.py")
    with open(DATA_FILE, encoding="utf-8") as f:
        fc = json.load(f)
    meta = fc.get("metadata", {})
    kausi = meta.get("kausi", "")
    if meta.get("demo"):
        print("VAROITUS: data on demo-dataa – sivut generoidaan silti, "
              "mutta aja fetch_data.py ennen julkaisua.")

    def has_page(pr):
        # Aluesivu tehdään vain, jos alueella on laskettu tuotto JA asukkaita.
        # Nollaväestön postinumerot (postikeskukset, PL-koodit) eivät ole
        # aitoja asuinalueita – ne jätetään pois, jottei synny ohutta,
        # lähes identtistä kaksoissisältöä hakukoneille.
        return pr.get("brutto_pct") is not None and bool(pr.get("vakiluku"))

    all_props = [ft["properties"] for ft in fc.get("features", [])]
    areas = [pr for pr in all_props if has_page(pr)]
    skipped = sum(1 for pr in all_props
                  if pr.get("brutto_pct") is not None
                  and not pr.get("vakiluku"))
    if not areas:
        raise SystemExit("Datassa ei ole yhtään aluetta, jolla on tuotto.")

    # Koko maan vertailuluku: kauppapainotettu mediaani. Tämä kuvaa
    # tyypillistä kaupan kohteena ollutta asuntoa; raaka polygonimediaani
    # vinoutuisi ylös lukuisten halpojen muuttotappioalueiden korkeista
    # bruttotuotoista (joilla harva asunto oikeasti vaihtaa omistajaa).
    national_median = weighted_median(areas, "brutto_pct", "n_kaupat")

    # Keskipisteet naapurihakua varten (kevyt bbox-keskikohta geometriasta).
    centroids = {}
    for ft in fc.get("features", []):
        pr = ft.get("properties", {})
        code = pr.get("posti_alue")
        if code is None or not has_page(pr):
            continue
        c = _centroid(ft.get("geometry"))
        if c:
            centroids[code] = c

    # Siivoa vanhat generoinnit, jotta poistuneet alueet eivät jää roikkumaan.
    for d in OUT_DIRS:
        shutil.rmtree(d, ignore_errors=True)

    urls = [f"{BASE_URL}/", f"{BASE_URL}/alueet/"]
    by_kunta = {}
    for p in areas:
        by_kunta.setdefault(str(p.get("kunta") or "Muu"), []).append(p)
    by_code = {p["posti_alue"]: p for p in areas}

    for p in areas:
        code = p["posti_alue"]
        kunta_areas = by_kunta[str(p.get("kunta") or "Muu")]
        neighbours = _nearest(code, centroids, by_code, k=6)
        d = os.path.join("alue", code)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
            f.write(area_page(p, kausi, kunta_areas, national_median,
                              neighbours))
        urls.append(f"{BASE_URL}/alue/{code}/")

    for kunta, plist in by_kunta.items():
        d = os.path.join("kunta", slugify(kunta))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
            f.write(kunta_page(kunta, plist, kausi))
        urls.append(f"{BASE_URL}/kunta/{slugify(kunta)}/")

    os.makedirs("alueet", exist_ok=True)
    with open(os.path.join("alueet", "index.html"), "w", encoding="utf-8") as f:
        f.write(index_page(by_kunta, areas, kausi))

    today = _dt.date.today().isoformat()
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sm += [f"<url><loc>{u}</loc><lastmod>{today}</lastmod></url>" for u in urls]
    sm.append("</urlset>")
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write("\n".join(sm))
    with open("robots.txt", "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")

    print(f"Generoitu: {len(areas)} aluesivua, {len(by_kunta)} kuntasivua, "
          f"hakemisto, sitemap.xml ({len(urls)} osoitetta) ja robots.txt. "
          f"Ohitettu {skipped} nollaväestön postinumeroa.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
