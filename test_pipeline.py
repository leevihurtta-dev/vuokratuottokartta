# -*- coding: utf-8 -*-
"""Testaa fetch_data.py:n parsinta- ja laskentalogiikan mock-json-stat2-datalla."""
import fetch_data as fd

# Simuloitu json-stat2-vastaus: 1 kausi x 3 postinumeroa x 1 talotyyppi x 2 tietoa
DS = {
    "class": "dataset",
    "id": ["Vuosineljännes", "Postinumero", "Talotyyppi", "Tiedot"],
    "size": [1, 3, 1, 2],
    "dimension": {
        "Vuosineljännes": {"category": {"index": {"2026Q1": 0},
                                        "label": {"2026Q1": "2026Q1"}}},
        "Postinumero": {"category": {
            "index": {"00120": 0, "00500": 1, "99999": 2},
            "label": {"00120": "00120 Punavuori (Helsinki)",
                      "00500": "00500 Sörnäinen (Helsinki)",
                      "99999": "99999 Testi (Testikunta)"}}},
        # yksialkioinen dimensio ILMAN index-kenttää (json-stat2 sallii tämän)
        "Talotyyppi": {"category": {"label": {"0": "Talotyypit yhteensä"}}},
        "Tiedot": {"category": {"index": ["keskihinta", "lkm"],
                                "label": {"keskihinta": "Neliöhinta (EUR/m2)",
                                          "lkm": "Kauppojen lukumäärä"}}},
    },
    # järjestys: postinumero (3) x tiedot (2), rivi-major
    "value": [8600.0, 41, 6900.0, 33, None, None],
}

get = fd.jsonstat_reader(DS)
assert get(**{"Vuosineljännes": "2026Q1", "Postinumero": "00120",
              "Talotyyppi": "0", "Tiedot": "keskihinta"}) == 8600.0
assert get(**{"Vuosineljännes": "2026Q1", "Postinumero": "00120",
              "Talotyyppi": "0", "Tiedot": "lkm"}) == 41
assert get(**{"Vuosineljännes": "2026Q1", "Postinumero": "00500",
              "Talotyyppi": "0", "Tiedot": "keskihinta"}) == 6900.0
assert get(**{"Vuosineljännes": "2026Q1", "Postinumero": "99999",
              "Talotyyppi": "0", "Tiedot": "keskihinta"}) is None  # peitetty
assert get(**{"Vuosineljännes": "2026Q1", "Postinumero": "00130",
              "Talotyyppi": "0", "Tiedot": "keskihinta"}) is None  # ei mukana

labels = fd.category_labels(DS, "Postinumero")
assert fd.kunta_from_label(labels["00120"]) == "Helsinki"
assert fd.nimi_from_label(labels["00120"]) == "Punavuori"
assert fd.nimi_from_label("00500 Sörnäinen") == "Sörnäinen"

# Laskenta
assert fd.brutto_pct(8600, 24.0) == 3.35
assert fd.brutto_pct(None, 24.0) is None
assert fd.brutto_pct(0, 24.0) is None            # ei jakoa nollalla
b = fd.netto_pct(8600, 24.0)
# ((24-4.5)*12*0.95)/(8600*1.015)*100 = 222.3/8729*100 = 2.5467...
assert abs(b - 2.55) < 0.01, b
assert fd.netto_pct(8600, None) is None
assert fd.masked_stat(-1) is None
assert fd.masked_stat(4231) == 4231.0

# Kuntatason fallback
assert fd.normalize_kunta("KU091") == "091"
assert fd.normalize_kunta("091") == "091"
assert fd.normalize_kunta(49) == "049"
assert fd.normalize_kunta("") == ""
# postinumerotason arvo voittaa aina
assert fd.with_fallback({"arvo": 8600.0, "n": 41},
                        {"arvo": 5000.0, "n": 900}) == (8600.0, 41, "pno")
# peitetty postinumerotaso -> kuntataso
assert fd.with_fallback({"arvo": None, "n": None},
                        {"arvo": 5000.0, "n": 900}) == (5000.0, 900, "kunta")
assert fd.with_fallback(None, {"arvo": 21.5, "n": 5200}) == (21.5, 5200, "kunta")
# ei kummankaan tason dataa
assert fd.with_fallback({}, None) == (None, None, None)
assert fd.with_fallback({"arvo": None}, {"arvo": None}) == (None, None, None)

# Arvokoodien poiminta metatiedoista
meta = {"variables": [
    {"code": "Vuosineljännes", "text": "Vuosineljännes",
     "values": ["2025Q4", "2026Q1"], "valueTexts": ["2025Q4", "2026Q1"],
     "time": True},
    {"code": "Talotyyppi", "text": "Talotyyppi", "values": ["0", "1", "3"],
     "valueTexts": ["Talotyypit yhteensä", "Kerrostalot", "Rivitalot"]},
    {"code": "Tiedot", "text": "Tiedot",
     "values": ["keskihinta", "lkm", "muutos"],
     "valueTexts": ["Neliöhinta (EUR/m2)", "Kauppojen lukumäärä",
                    "Neliöhinnan vuosimuutos, %"]},
]}
assert fd.time_variable(meta)["code"] == "Vuosineljännes"
tv = fd.find_variable(meta, "talotyyppi")
assert fd.pick_value(tv, ["yhteensä"]) == ("0", "Talotyypit yhteensä")
td = fd.find_variable(meta, "tiedot")
assert fd.pick_value(td, ["neliöhinta"],
                     exclude=["lukumäärä", "muutos", "indeksi"])[0] == "keskihinta"
assert fd.pick_value(td, ["lukumäärä"])[0] == "lkm"

# Luokkavalinta: regressiotesti aiemmasta bugista, jossa pelkkä "yhteensä"
# osui arvoon "Rivitalot yhteensä", kun kaikkien talotyyppien
# yhteensä-luokkaa ei ollut.
tt = {"code": "Talotyyppi", "text": "Talotyyppi",
      "values": ["1", "2", "3", "4"],
      "valueTexts": ["Kerrostalo yksiöt", "Kerrostalo kaksiot",
                     "Kerrostalo kolmiot+", "Rivitalot yhteensä"]}
codes, text = fd.class_codes_for(tt, "yhteensa",
                                 fd.TALOTYYPPI_HAKUSANAT,
                                 fd.TALOTYYPPI_SUODATIN)
assert codes == ["1", "2", "3", "4"], codes   # kaikki, EI "Rivitalot yhteensä"
assert text.startswith("painotettu keskiarvo")
codes, _ = fd.class_codes_for(tt, "kerrostalot",
                              fd.TALOTYYPPI_HAKUSANAT, fd.TALOTYYPPI_SUODATIN)
assert codes == ["1", "2", "3"], codes
codes, _ = fd.class_codes_for(tt, "rivitalot",
                              fd.TALOTYYPPI_HAKUSANAT, fd.TALOTYYPPI_SUODATIN)
assert codes == ["4"], codes
tt2 = {"code": "Talotyyppi", "text": "Talotyyppi",
       "values": ["0", "1"],
       "valueTexts": ["Talotyypit yhteensä", "Kerrostalot yhteensä"]}
codes, text = fd.class_codes_for(tt2, "yhteensa",
                                 fd.TALOTYYPPI_HAKUSANAT,
                                 fd.TALOTYYPPI_SUODATIN)
assert (codes, text) == (["0"], "Talotyypit yhteensä")

print("Kaikki testit OK ✓")
