# Vuokratuottokartta

Selaimessa toimiva karttasovellus, joka näyttää vanhojen osakeasuntojen
bruttovuokratuoton postinumeroalueittain koko Suomessa. Käyttäjä näkee
värikoodatun koropleettikartan, voi klikata aluetta ja nähdä neliöhinnan,
keskineliövuokran, lasketun brutto- ja nettovuokratuoton sekä taustatiedot
(väkiluku, mediaanitulo). Toimii myös mobiilissa.

Kaikki data on Tilastokeskuksen avointa dataa (CC BY 4.0). Sovelluksessa ei
ole backendiä eikä tietokantaa: data haetaan kerran rakennusvaiheessa yhdeksi
staattiseksi `postal_yields.geojson`-tiedostoksi, jonka frontend lataa. Näin
selain ei kutsu Tilastokeskuksen rajapintoja ajonaikaisesti (ei CORS- eikä
suorituskykyongelmia).

## Tiedostot

| Tiedosto | Tehtävä |
|---|---|
| `fetch_data.py` | Data-pipeline: hakee hinnat, vuokrat ja geometriat, yhdistää ja laskee tuotot → `postal_yields.geojson` |
| `postal_yields.geojson` | Valmis, yhdistetty data. **Huom:** repossa oleva versio on synteettistä demo-dataa (ks. alla). |
| `index.html`, `app.js`, `style.css` | Staattinen frontend (MapLibre GL JS) |
| `make_demo_data.py` | Tuottaa demo-datan kehityskäyttöön ilman verkkoa |
| `test_pipeline.py` | Pipelinen parsinta- ja laskentalogiikan testit |

## Pika-aloitus

```bash
# 1) Hae oikea data (vaatii verkkoyhteyden pxdata.stat.fi- ja geo.stat.fi-osoitteisiin)
pip install shapely            # valinnainen mutta vahvasti suositeltu (ks. alla)
python fetch_data.py           # → postal_yields.geojson (uusin yhteinen neljännes)

# 2) Käynnistä paikallinen palvelin ja avaa selain
python -m http.server 8000
# → http://localhost:8000
```

Sivua **ei** voi avata suoraan `file://`-osoitteesta, koska selaimet estävät
`fetch`-kutsut paikallisiin tiedostoihin. Mikä tahansa staattinen palvelin
kelpaa (myös GitHub Pages tms. julkaisuun).

### Demo-data

Mukana toimitettava `postal_yields.geojson` on `make_demo_data.py`:llä tehty
**synteettinen** esimerkkiaineisto (27 aluetta, yksinkertaistetut
neliögeometriat, suuruusluokaltaan realistiset mutta epäviralliset arvot),
jotta kartan voi avata heti ilman verkkoyhteyttä Tilastokeskukseen. Frontend
tunnistaa demo-datan (`metadata.demo: true`) ja näyttää varoitusbannerin.
Banneri katoaa, kun tiedosto korvataan ajamalla `fetch_data.py`.

## Datan päivitys uudelle vuosineljännekselle

Aja pipeline uudelleen — se valitsee automaattisesti uusimman neljänneksen,
joka löytyy sekä hinta- että vuokrataulukosta, ja kirjoittaa
`postal_yields.geojson`-tiedoston yli:

```bash
python fetch_data.py                  # uusin yhteinen neljännes
python fetch_data.py --kausi 2026Q1   # tai pakota tietty neljännes
```

Muita hyödyllisiä valitsimia:

```bash
python fetch_data.py --test 00120         # vaihe 1: yhden postinumeron arvot
python fetch_data.py --intermediate       # tallenna välitiedosto prices_rents.json
python fetch_data.py --simplify 0         # täysi geometriatarkkuus (iso tiedosto!)
python fetch_data.py --simplify 0.001     # karkeampi geometria, pienempi tiedosto
python fetch_data.py --no-fallback        # ilman kuntatason täydennystä
python fetch_data.py --talotyyppi kerrostalot --huoneluku yksiot  # edistynyt
```

Pipeline hakee arvokoodit (Talotyyppi, Tiedot, Huoneluku) taulukoiden
metatiedoista GET-kutsulla — mitään koodeja ei ole kovakoodattu, joten
taulukkorakenteen pienet muutokset eivät riko hakua. Jos PxWeb-API:n
polkurakenne muuttuu, päivitä `PRICE_TABLE_CANDIDATES`/`RENT_TABLE_CANDIDATES`
-listat skriptin alusta.

## Datalähteet

1. **Neliöhinnat** — StatFin `statfin_ashi_pxt_13mt`: vanhojen osakeasuntojen
   neliöhinnat ja kauppojen lukumäärät postinumeroalueittain,
   neljännesvuosittain (PxWeb, json-stat2).
2. **Vuokrat** — StatFin `statfin_asvu_pxt_13eb`: vapaarahoitteisten
   vuokra-asuntojen keskineliövuokrat postinumeroalueittain. **Tieto on
   peitetty**, jos havaintoja on alle 20 tai vuokrataloyhtiöiden osuus on
   suuri.
3. **Kuntatason varadata** — kun postinumerotason hinta tai vuokra on
   peitetty, arvo täydennetään oletuksena saman kunnan keskiarvolla
   (StatFinin kunnittaiset hinta- ja vuokrataulukot; pipeline etsii ne
   kansiolistauksesta hakusanalla "kunnittain", jos suorat osoitteet eivät
   toimi). Näin tärkeimmätkään alueet eivät jää harmaiksi, vaikka
   postinumerotason tieto puuttuisi. Täydennetyt arvot merkitään
   ominaisuuksiin `hinta_taso`/`vuokra_taso`/`taso = "kunta"`, ja frontend
   näyttää niistä huomautuksen popupissa; ne voi myös piilottaa
   käyttöliittymän suodattimesta. Poista pipelinesta:
   `python fetch_data.py --no-fallback`. Kokonaan harmaiksi jäävät enää
   alueet, joiden kunnallekaan ei ole dataa.
4. **Geometria + taustatiedot** — geo.stat.fi WFS, taso
   `postialue:pno_tilasto` (rantaviivalla leikattu, Paavo-tilastot mukana:
   väkiluku `he_vakiy`, talouksien mediaanitulo `tr_mtu`; peitetty arvo `-1`
   käsitellään puuttuvana). Geometria pyydetään suoraan WGS84:nä
   (`srsName=EPSG:4326`).

Liitosavain on 5-numeroinen postinumerokoodi (PxWebin koodi, ei selite;
geometriassa kenttä `posti_alue`). Hinta- ja vuokradatalle kohdistetaan sama
vuosineljännes.

## Laskenta

Koska sekä hinta (€/m²) että vuokra (€/m²/kk) ovat neliöperusteisia, tuotto
lasketaan suoraan ilman oletettua asuntokokoa:

- **Brutto-%** = vuokra × 12 ÷ hinta × 100
- **Netto-%** = (vuokra − hoitovastike) × 12 × (1 − vajaakäyttöaste)
  ÷ (hinta × (1 + varainsiirtovero)) × 100

Oletukset (säädettävissä käyttöliittymän liukusäätimillä, netto lasketaan
selaimessa reaaliajassa): hoitovastike 4,5 €/m²/kk, vajaakäyttö 5 %,
varainsiirtovero 1,5 %. Lisäksi valinnainen pääomatulovero 30/34 %.
Peitetyt arvot eivät kaada laskentaa: tuotto lasketaan vain, jos sekä hinta
että vuokra ovat saatavilla (ei NaN:ia, ei jakoa nollalla).

## Tunnetut rajoitteet (näytetään myös käyttöliittymän info-ruudussa)

- Hintadata jaotellaan talotyypin, vuokradata huoneluvun mukaan — täydellistä
  vastaavuutta ei ole. Kartta käyttää molempien "yhteensä"-tasoa; yksiöiden
  todellinen tuotto on tyypillisesti korkeampi.
- Postinumeroalueen keskiarvot kätkevät suuren hajonnan; työkalu on
  suuntaa-antava, ei kohdekohtainen.
- Peitetyt alueet täydennetään kuntatason keskiarvolla, joka tasoittaa
  alueiden välisiä eroja — "(kunta)"-merkittyjä lukuja pitää tulkita
  varovaisemmin. Ilman dataa jäävät enää alueet, joiden kunnallakaan ei ole
  julkaistua tietoa.
- Geometrian yksinkertaistus tehdään aluekohtaisesti, joten naapurialueiden
  rajoille voi syntyä hiuksenhienoja rakoja. Oletustoleranssi (~40 m) on
  visuaalisesti huomaamaton ja pudottaa tiedostokoon murto-osaan; ilman
  shapely-kirjastoa yksinkertaistus ohitetaan ja tiedostosta tulee suuri
  (kymmeniä megatavuja).

## Huomio tästä toimitusympäristöstä

Tämä paketti rakennettiin ympäristössä, jonka verkko ei salli yhteyksiä
`pxdata.stat.fi`- ja `geo.stat.fi`-osoitteisiin, joten pipelinea ei ole voitu
ajaa oikeaa dataa vasten (parsinta- ja laskentalogiikka on testattu
`test_pipeline.py`:n mock-datalla). Jos ensimmäinen ajo omalla koneellasi
kaatuu esim. muuttuneeseen API-polkuun, virheilmoitus kertoo mitä kokeiltiin —
korjaus on yleensä yhden URL-rivin päivitys kandidaattilistaan.
