/* Vuokratuottokartta — frontend
 * Lataa staattisen postal_yields.geojson-tiedoston ja piirtää koropleettikartan
 * bruttovuokratuotosta MapLibre GL JS:llä. Ei backendiä.
 */
"use strict";

// ---------------------------------------------------------------------------
// Vakiot
// ---------------------------------------------------------------------------
const DATA_URL = "postal_yields.geojson";
const BASEMAP = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";
const FINLAND_BOUNDS = [[19.0, 59.6], [31.7, 70.1]];

const BREAKS = [3, 4, 5, 6]; // luokkarajat: <3, 3–4, 4–5, 5–6, >6 %
const COLORS = ["#c65f5f", "#e39b55", "#ecd06f", "#8fbf70", "#3f8f5f"];
const NODATA_COLOR = "#d3d3cf";
const SCALE_MAX = 8; // popupin asteikkopalkin yläraja (%)

// ---------------------------------------------------------------------------
// Tila
// ---------------------------------------------------------------------------
const state = {
  colorMode: "brutto",           // "brutto" | "netto"
  minBrutto: 0,
  minKaupat: 0,
  hoito: 4.5,                    // €/m²/kk
  vaja: 5,                       // %
  vero: 1.5,                     // %
  taxOn: false,
  taxRate: 30,                   // %
};

let map = null;
let popup = null;
let popupProps = null;           // avoimen popupin alueen ominaisuudet
let popupLngLat = null;
let searchIndex = [];            // {code, nimi, kunta, bbox, center, label}
let hoveredId = null;

const $ = (id) => document.getElementById(id);
const nf = (dec) => new Intl.NumberFormat("fi-FI", {
  minimumFractionDigits: dec, maximumFractionDigits: dec,
});
const fmt = (v, dec = 0, unit = "") =>
  (v === null || v === undefined || Number.isNaN(v))
    ? null
    : nf(dec).format(v) + unit;

// ---------------------------------------------------------------------------
// Laskenta (sama kaava kuin pipelinessa; netto liukusäätimien arvoilla)
// ---------------------------------------------------------------------------
function nettoPct(props) {
  const h = props.hinta_eur_m2;
  const v = props.vuokra_eur_m2;
  if (typeof h !== "number" || typeof v !== "number" || h <= 0) return null;
  let net = ((v - state.hoito) * 12 * (1 - state.vaja / 100)) /
            (h * (1 + state.vero / 100)) * 100;
  if (state.taxOn && net > 0) net *= (1 - state.taxRate / 100);
  return net;
}

// ---------------------------------------------------------------------------
// MapLibre-lausekkeet
// ---------------------------------------------------------------------------
function valueExpression() {
  if (state.colorMode === "brutto") return ["get", "brutto_pct"];
  // Netto lasketaan datavetoisesti liukusäätimien arvoilla.
  const factor = 100 * (state.taxOn ? (1 - state.taxRate / 100) : 1);
  return ["*", factor,
    ["/",
      ["*", ["-", ["get", "vuokra_eur_m2"], state.hoito],
        12 * (1 - state.vaja / 100)],
      ["*", ["get", "hinta_eur_m2"], 1 + state.vero / 100]]];
}

function colorExpression() {
  const expr = ["step", valueExpression(), COLORS[0]];
  BREAKS.forEach((b, i) => expr.push(b, COLORS[i + 1]));
  return expr;
}

function fillFilter() {
  return ["all",
    ["==", ["typeof", ["get", "brutto_pct"]], "number"],
    [">=", ["get", "brutto_pct"], state.minBrutto],
    [">=", ["coalesce", ["get", "n_kaupat"], 0], state.minKaupat],
  ];
}

function updateLayers() {
  if (!map || !map.getLayer("postal-fill")) return;
  map.setPaintProperty("postal-fill", "fill-color", colorExpression());
  map.setFilter("postal-fill", fillFilter());
  $("legend-title").textContent = state.colorMode === "brutto"
    ? "Bruttovuokratuotto"
    : (state.taxOn ? `Nettotuotto (vero ${state.taxRate} %)` : "Nettotuotto");
  refreshPopup();
}

// ---------------------------------------------------------------------------
// Legenda
// ---------------------------------------------------------------------------
function buildLegend() {
  const labels = [
    `alle ${BREAKS[0]} %`,
    ...BREAKS.slice(0, -1).map((b, i) => `${b}–${BREAKS[i + 1]} %`),
    `yli ${BREAKS[BREAKS.length - 1]} %`,
  ];
  const ul = $("legend-items");
  ul.innerHTML = "";
  labels.forEach((text, i) => ul.appendChild(legendRow(COLORS[i], text)));
  ul.appendChild(legendRow(NODATA_COLOR, "Ei dataa / suodatettu"));
}
function legendRow(color, text) {
  const li = document.createElement("li");
  const sw = document.createElement("span");
  sw.className = "sw";
  sw.style.background = color;
  li.append(sw, document.createTextNode(text));
  return li;
}

// ---------------------------------------------------------------------------
// Popup
// ---------------------------------------------------------------------------
function row(label, value, cls = "") {
  const v = value === null
    ? '<dd class="na">ei dataa</dd>'
    : `<dd class="${cls}">${value}</dd>`;
  return `<dt>${label}</dt>${v}`;
}

function popupHTML(p) {
  const brutto = typeof p.brutto_pct === "number" ? p.brutto_pct : null;
  const netto = nettoPct(p);

  let scale = "";
  if (brutto !== null) {
    const pos = Math.min(Math.max(brutto, 0), SCALE_MAX) / SCALE_MAX * 100;
    scale = `
      <div class="pp-scale" title="Bruttotuotto asteikolla 0–${SCALE_MAX} %">
        <div class="pp-scale-bar"><span class="pp-scale-marker" style="left:${pos.toFixed(1)}%"></span></div>
        <div class="pp-scale-labels"><span>0&nbsp;%</span><span>${SCALE_MAX}&nbsp;%</span></div>
      </div>`;
  }

  const smallSample =
    (typeof p.n_kaupat === "number" && p.n_kaupat < 10) ||
    (typeof p.n_vuokrat === "number" && p.n_vuokrat < 30);

  return `
    <h3 class="pp-title"><span class="code">${p.posti_alue}</span> ${p.nimi ?? ""}</h3>
    <p class="pp-kunta">${p.kunta ?? ""}</p>
    ${scale}
    <dl class="pp-grid">
      ${row("Bruttotuotto", fmt(brutto, 2, " %"), "big")}
      ${row("Nettotuotto*", fmt(netto, 2, " %"), "big")}
      ${row("Neliöhinta", fmt(p.hinta_eur_m2, 0, " €/m²"))}
      ${row("Keskineliövuokra", fmt(p.vuokra_eur_m2, 2, " €/m²/kk"))}
      ${row("Kauppoja", fmt(p.n_kaupat, 0))}
      ${row("Vuokrahavaintoja", fmt(p.n_vuokrat, 0))}
      ${row("Väkiluku", fmt(p.vakiluku, 0))}
      ${row("Mediaanitulo", fmt(p.mediaanitulo, 0, " €/v"))}
    </dl>
    ${smallSample ? '<p class="pp-warn">Pieni otos — keskiarvot ovat epävarmoja.</p>' : ""}
    <p class="hint">* oletuksillasi (säädä paneelista)</p>`;
}

function openPopup(lngLat, props) {
  popupProps = props;
  popupLngLat = lngLat;
  if (popup) popup.remove();
  popup = new maplibregl.Popup({ closeButton: true, maxWidth: "320px" })
    .setLngLat(lngLat)
    .setHTML(popupHTML(props))
    .addTo(map);
  popup.on("close", () => { popupProps = null; popupLngLat = null; });
}

function refreshPopup() {
  if (popup && popupProps && popup.isOpen()) {
    popup.setHTML(popupHTML(popupProps));
  }
}

// ---------------------------------------------------------------------------
// Haku
// ---------------------------------------------------------------------------
function buildSearchIndex(features) {
  const dl = $("search-options");
  const frag = document.createDocumentFragment();
  searchIndex = features.map((f) => {
    const p = f.properties;
    const bbox = geomBbox(f.geometry);
    const label = `${p.posti_alue} ${p.nimi ?? ""}${p.kunta ? ` (${p.kunta})` : ""}`;
    const opt = document.createElement("option");
    opt.value = label;
    frag.appendChild(opt);
    return {
      code: p.posti_alue,
      nimi: (p.nimi ?? "").toLowerCase(),
      kunta: (p.kunta ?? "").toLowerCase(),
      label,
      bbox,
      center: [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2],
      props: p,
    };
  });
  dl.replaceChildren(frag);
}

function geomBbox(geom) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const walk = (c) => {
    if (typeof c[0] === "number") {
      if (c[0] < minX) minX = c[0];
      if (c[0] > maxX) maxX = c[0];
      if (c[1] < minY) minY = c[1];
      if (c[1] > maxY) maxY = c[1];
    } else c.forEach(walk);
  };
  walk(geom.coordinates);
  return [minX, minY, maxX, maxY];
}

function doSearch() {
  const q = $("search").value.trim().toLowerCase();
  const msg = $("search-msg");
  msg.textContent = "";
  if (!q) return;

  const codeQ = (q.match(/^\d{2,5}/) || [null])[0];
  let hit =
    (codeQ && searchIndex.find((e) => e.code === codeQ)) ||
    (codeQ && searchIndex.find((e) => e.code.startsWith(codeQ))) ||
    searchIndex.find((e) => e.nimi.includes(q) || e.kunta.includes(q) ||
                            e.label.toLowerCase().includes(q));
  if (!hit) {
    msg.textContent = "Aluetta ei löytynyt. Kokeile 5-numeroista postinumeroa tai kaupunginosan nimeä.";
    return;
  }
  msg.textContent = `Löytyi: ${hit.label}`;
  if (window.matchMedia("(max-width: 720px)").matches) setPanelOpen(false);
  map.fitBounds([[hit.bbox[0], hit.bbox[1]], [hit.bbox[2], hit.bbox[3]]],
    { padding: 70, maxZoom: 12.5, duration: 900 });
  map.once("moveend", () => openPopup(hit.center, hit.props));
}

// ---------------------------------------------------------------------------
// Paneeli ja säätimet
// ---------------------------------------------------------------------------
function setPanelOpen(open) {
  $("panel").classList.toggle("closed", !open);
  $("panel-toggle").setAttribute("aria-expanded", String(open));
}

function bindControls() {
  $("panel-toggle").addEventListener("click", () =>
    setPanelOpen($("panel").classList.contains("closed")));
  $("panel-close").addEventListener("click", () => setPanelOpen(false));
  setPanelOpen(!window.matchMedia("(max-width: 720px)").matches);

  // Väritila
  document.querySelectorAll(".seg-btn").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll(".seg-btn").forEach((b) =>
        b.classList.toggle("active", b === btn));
      state.colorMode = btn.dataset.mode;
      updateLayers();
    }));

  // Suodattimet
  bindSlider("f-brutto", (v) => {
    state.minBrutto = v;
    $("f-brutto-out").textContent = `${nf(1).format(v)}\u00a0%`;
  });
  bindSlider("f-kaupat", (v) => {
    state.minKaupat = v;
    $("f-kaupat-out").textContent = nf(0).format(v);
  });

  // Nettotuoton oletukset
  bindSlider("a-hoito", (v) => {
    state.hoito = v;
    $("a-hoito-out").textContent = `${nf(1).format(v)}\u00a0€/m²/kk`;
  });
  bindSlider("a-vaja", (v) => {
    state.vaja = v;
    $("a-vaja-out").textContent = `${nf(0).format(v)}\u00a0%`;
  });
  bindSlider("a-vero", (v) => {
    state.vero = v;
    $("a-vero-out").textContent = `${nf(1).format(v)}\u00a0%`;
  });
  $("a-tax").addEventListener("change", (e) => {
    state.taxOn = e.target.checked;
    $("a-tax-rate").disabled = !state.taxOn;
    updateLayers();
  });
  $("a-tax-rate").addEventListener("change", (e) => {
    state.taxRate = Number(e.target.value);
    updateLayers();
  });

  // Haku
  $("search-btn").addEventListener("click", doSearch);
  $("search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doSearch();
  });
  $("search").addEventListener("change", doSearch); // datalist-valinta

  // Info
  $("info-open").addEventListener("click", () => $("info").showModal());
}

function bindSlider(id, apply) {
  const el = $(id);
  const handler = () => { apply(Number(el.value)); updateLayers(); };
  el.addEventListener("input", handler);
  apply(Number(el.value)); // alusta output-tekstit
}

// ---------------------------------------------------------------------------
// Kartta
// ---------------------------------------------------------------------------
function initMap(data) {
  map = new maplibregl.Map({
    container: "map",
    style: BASEMAP,
    bounds: FINLAND_BOUNDS,
    fitBoundsOptions: { padding: 20 },
    attributionControl: { compact: true },
    minZoom: 3.5,
    maxZoom: 15,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }));
  map.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }));

  map.on("load", () => {
    map.addSource("postal", { type: "geojson", data, generateId: true });

    // Pohjakerros: kaikki alueet harmaana ("ei dataa" / suodatettu jää näkyviin)
    map.addLayer({
      id: "postal-base",
      type: "fill",
      source: "postal",
      paint: { "fill-color": NODATA_COLOR, "fill-opacity": 0.5 },
    });
    // Värikerros: vain alueet, joilla on data ja jotka läpäisevät suodattimet
    map.addLayer({
      id: "postal-fill",
      type: "fill",
      source: "postal",
      filter: fillFilter(),
      paint: {
        "fill-color": colorExpression(),
        "fill-opacity": [
          "case", ["boolean", ["feature-state", "hover"], false], 0.92, 0.72,
        ],
      },
    });
    map.addLayer({
      id: "postal-line",
      type: "line",
      source: "postal",
      paint: {
        "line-color": "#ffffff",
        "line-width": [
          "case", ["boolean", ["feature-state", "hover"], false], 2, 0.6,
        ],
      },
    });

    // Klikkaus -> popup (myös harmaat alueet, jotta "ei dataa" on tutkittavissa)
    map.on("click", (e) => {
      const feats = map.queryRenderedFeatures(e.point,
        { layers: ["postal-fill", "postal-base"] });
      if (feats.length) openPopup(e.lngLat, feats[0].properties);
    });

    // Hover-korostus
    map.on("mousemove", "postal-base", (e) => {
      map.getCanvas().style.cursor = "pointer";
      const id = e.features?.[0]?.id;
      if (id === hoveredId) return;
      if (hoveredId !== null) {
        map.setFeatureState({ source: "postal", id: hoveredId }, { hover: false });
      }
      hoveredId = id ?? null;
      if (hoveredId !== null) {
        map.setFeatureState({ source: "postal", id: hoveredId }, { hover: true });
      }
    });
    map.on("mouseleave", "postal-base", () => {
      map.getCanvas().style.cursor = "";
      if (hoveredId !== null) {
        map.setFeatureState({ source: "postal", id: hoveredId }, { hover: false });
        hoveredId = null;
      }
    });

    $("loading").classList.add("hidden");
  });

  map.on("error", (e) => {
    // Pohjakartan tyylivirhe ei saa estää koropleetin käyttöä.
    console.warn("MapLibre:", e && e.error);
  });
}

// ---------------------------------------------------------------------------
// Käynnistys
// ---------------------------------------------------------------------------
async function main() {
  buildLegend();
  bindControls();

  let data;
  try {
    const resp = await fetch(DATA_URL);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (err) {
    $("loading").innerHTML =
      "<p><strong>Tuottodataa ei voitu ladata.</strong><br>" +
      "Varmista, että <code>postal_yields.geojson</code> on samassa kansiossa " +
      "ja että sivu on avattu paikallisen palvelimen kautta " +
      "(esim. <code>python -m http.server</code>) — selaimet estävät " +
      "fetch-kutsut file://-osoitteista.<br><small>" + err + "</small></p>";
    return;
  }

  const meta = data.metadata || {};
  if (meta.demo) {
    $("demo-banner").hidden = false;
    document.body.classList.add("has-banner");
  }
  if (meta.kausi && !meta.demo) {
    $("brand-kausi").textContent =
      `Vanhat osakeasunnot · ${meta.kausi} · postinumeroittain`;
  }

  buildSearchIndex(data.features);
  initMap(data);
}

main();
