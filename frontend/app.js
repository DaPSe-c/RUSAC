/* RUSAC · MVP local — lógica de la página (sin frameworks). */
"use strict";

const $ = (s) => document.querySelector(s);
const S = {
  meta: null,
  session: getSession(),
  origin: null, dest: null, me: null,
  route: null,
  picking: false, pickStep: 0,
  walking: false, walkTimer: null, walkPath: [], walkPos: 0,
  offline: false, tileErrors: 0,
  risk: null, blocked: new Set(),
  reportTarget: null,
  geoWatch: null,
};

// ---------- utilidades ----------
function getSession() {
  // sessionStorage: cada pestaña es una "persona" distinta (útil para probar confirmaciones)
  let id = sessionStorage.getItem("rusac-sesion");
  if (!id) {
    id = (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36));
    sessionStorage.setItem("rusac-sesion", id);
  }
  return id;
}

async function api(path, body) {
  const opts = { headers: { "X-Sesion": S.session } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : "No se pudo completar la solicitud.");
  }
  return data;
}

function distM(a, b) {
  const R = 6371000, toR = Math.PI / 180;
  const dLat = (b[0] - a[0]) * toR, dLon = (b[1] - a[1]) * toR;
  const x = dLon * Math.cos(((a[0] + b[0]) / 2) * toR);
  return Math.sqrt(x * x + dLat * dLat) * R;
}

function level(r) {
  const u = S.meta.umbral;
  return r >= u.rojo ? "rojo" : r >= u.amarillo ? "amarillo" : "verde";
}
const LEVEL_TXT = { verde: "bajo", amarillo: "medio", rojo: "alto" };
const COLORS = { verde: "#1f7a45", amarillo: "#e9a100", rojo: "#d63b2f" };

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

let toastTimer;
function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = isError ? "error" : "";
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3800);
}

let bannerTimer;
function banner(html, alerta = false, ms = 6000) {
  const el = $("#banner");
  el.innerHTML = html;
  el.className = alerta ? "alerta" : "";
  el.hidden = false;
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => (el.hidden = true), ms);
}

function hourText(h) {
  const tramo = h >= 20 || h < 6 ? "noche" : h < 12 ? "mañana" : "tarde";
  return `${String(h).padStart(2, "0")}:00 · ${tramo}`;
}

// ---------- mapa ----------
const map = L.map("map", { zoomControl: true, preferCanvas: true });
const canvas = L.canvas({ padding: 0.3 });
const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
tiles.on("tileerror", () => {
  S.tileErrors += 1;
  if (S.tileErrors === 6 && !S.offline) {
    S.offline = true;
    addNotice("Sin conexión al mapa base: se muestran las calles desde los datos locales.");
    styleStreets();
  }
});

const streetsLayer = L.layerGroup().addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const reportsLayer = L.layerGroup().addTo(map);
let streetLines = [];

const icon = (cls, html = "") => L.divIcon({ className: "", html: `<div class="${cls}">${html}</div>`, iconSize: null });
const markerA = L.marker([0, 0], { draggable: true, icon: icon("endpoint a", "A"), zIndexOffset: 500, title: "Origen" });
const markerB = L.marker([0, 0], { draggable: true, icon: icon("endpoint b", "B"), zIndexOffset: 500, title: "Destino" });
const markerMe = L.marker([0, 0], {
  draggable: true, zIndexOffset: 1000, title: "Tu posición (arrástrala para simular)",
  icon: L.divIcon({ className: "", html: '<div class="me"></div><div class="me-label">Tú</div>', iconSize: [20, 20], iconAnchor: [10, 10] }),
});

markerA.on("dragend", () => { S.origin = latlng(markerA); setMe(S.origin); setCustom(); computeRoute(); });
markerB.on("dragend", () => { S.dest = latlng(markerB); setCustom(); computeRoute(); });
markerMe.on("dragend", () => { S.me = latlng(markerMe); });

function latlng(m) { const p = m.getLatLng(); return [p.lat, p.lng]; }
function setMe(p) { S.me = p; markerMe.setLatLng(p).addTo(map); }

function addNotice(text) {
  const d = document.createElement("div");
  d.className = "notice";
  d.textContent = text;
  $("#notices").appendChild(d);
}

// ---------- calles y semáforo ----------
async function loadStreets() {
  const geoms = await api("/api/calles");
  streetLines = geoms.map((g) => L.polyline(g, { renderer: canvas, interactive: false, weight: 2, opacity: 0 }).addTo(streetsLayer));
  styleStreets();
}

async function updateRisk() {
  if (!$("#showRisk").checked) { styleStreets(); return; }
  const data = await api(`/api/riesgo?hora=${$("#hour").value}`);
  S.risk = data.riesgo;
  S.blocked = new Set(data.bloqueadas);
  styleStreets();
}

function styleStreets() {
  const showRisk = $("#showRisk").checked && S.risk;
  $("#legend").hidden = !showRisk;
  streetLines.forEach((line, i) => {
    if (showRisk) {
      const blocked = S.blocked.has(i);
      line.setStyle({
        color: blocked ? "#111" : COLORS[level(S.risk[i])],
        weight: blocked ? 4 : 3, opacity: 0.85, dashArray: blocked ? "4 6" : null,
      });
    } else if (S.offline) {
      line.setStyle({ color: "#9aa59f", weight: 2, opacity: 0.9, dashArray: null });
    } else {
      line.setStyle({ opacity: 0 });
    }
  });
}

// ---------- rutas ----------
function routeKey(r) { return r ? JSON.stringify(r.segura.coords) : ""; }

async function computeRoute({ from = null, reason = null } = {}) {
  if (!S.origin || !S.dest) return null;
  const prev = S.route;
  let data;
  try {
    data = await api("/api/ruta", {
      origen: from || S.origin, destino: S.dest,
      hora: +$("#hour").value, alfa: +$("#alpha").value,
    });
  } catch (e) {
    toast(e.message, true);
    return null;
  }
  const changed = prev && routeKey(prev) !== routeKey(data);
  S.route = data;
  drawRoute(data);
  renderResults(data);
  if (reason && changed) {
    banner(`🔁 <b>Ruta recalculada</b> · ${reason}<br>Nueva ruta: ${data.segura.longitud_m} m, ${data.segura.minutos} min`, true, 7000);
  }
  return { data, changed };
}

function drawRoute(r) {
  routeLayer.clearLayers();
  L.polyline(r.corta.coords, { color: "#333", weight: 4, opacity: 0.7, dashArray: "6 8" })
    .bindTooltip("Ruta más corta").addTo(routeLayer);
  L.polyline(r.segura.coords, { color: "#fff", weight: 10, opacity: 0.9 }).addTo(routeLayer);
  L.polyline(r.segura.coords, { color: "#1f7a45", weight: 6, opacity: 1 })
    .bindTooltip("Ruta más segura").addTo(routeLayer);
  // tramos de riesgo alto que la ruta segura no pudo evitar
  r.segura.tramos.filter((t) => t.nivel !== "verde").forEach((t) =>
    L.polyline(t.geom, { color: COLORS[t.nivel], weight: 6, opacity: 1 })
      .bindTooltip(`Tramo con riesgo ${LEVEL_TXT[t.nivel]}`).addTo(routeLayer));
}

function card(kind, title, x) {
  const lv = level(x.riesgo_promedio);
  const calles = x.calles.length ? `Por: ${x.calles.slice(0, 5).join(" → ")}${x.calles.length > 5 ? " …" : ""}` : "";
  return `<div class="card ${kind}" data-kind="${kind}">
    <h3><i class="swatch"></i>${title} <span class="chip ${lv}">riesgo ${LEVEL_TXT[lv]}</span></h3>
    <div class="metrics">
      <span><b>${x.longitud_m.toLocaleString("es-MX")}</b> m</span>
      <span><b>${x.minutos}</b> min</span>
      <span>${x.tramos_rojos} tramo(s) rojo(s)</span>
      ${x.reportes_en_ruta ? `<span>⚠ ${x.reportes_en_ruta} reporte(s)</span>` : ""}
    </div>
    <div class="streets">${calles}</div>
  </div>`;
}

function renderResults(r) {
  const c = r.comparacion;
  let compare;
  if (r.alfa === 0) {
    compare = "Con prioridad 0 solo se calcula la ruta más corta. Sube el control para buscar una más segura.";
  } else if (r.misma_ruta) {
    compare = `A las ${String(r.hora).padStart(2, "0")}:00 la ruta más corta <b>ya es la más segura</b>.`;
  } else {
    const extra = c.metros_extra > 0 ? `<b>+${c.metros_extra} m</b> (+${c.minutos_extra} min)` : "<b>sin caminar más</b>";
    compare = `Caminando ${extra} evitas <b>${c.reduccion_riesgo_pct}%</b> del riesgo del trayecto.`;
  }
  $("#results").innerHTML = `<div class="cards">
      ${card("safe", "Más segura", r.segura)}
      ${r.misma_ruta ? "" : card("short", "Más corta", r.corta)}
    </div>
    <p class="compare">${compare}</p>
    ${r.calles_bloqueadas ? `<p class="hint">🚧 ${r.calles_bloqueadas} calle(s) cerrada(s) excluida(s).</p>` : ""}`;
  document.querySelectorAll(".card").forEach((el) => el.addEventListener("click", () => {
    const coords = el.dataset.kind === "safe" ? r.segura.coords : r.corta.coords;
    map.fitBounds(L.latLngBounds(coords).pad(0.15));
  }));
}

// ---------- trayectos ----------
function setCustom() { $("#preset").value = "custom"; }

function applyPreset(id) {
  stopWalk();
  if (id === "custom") {
    S.picking = true; S.pickStep = 0;
    $("#pickHint").hidden = false;
    routeLayer.clearLayers();
    $("#results").innerHTML = '<p class="hint">Marca el origen y el destino en el mapa.</p>';
    return;
  }
  S.picking = false;
  $("#pickHint").hidden = true;
  const p = S.meta.trayectos.find((t) => t.id === id);
  S.origin = p.origen; S.dest = p.destino;
  markerA.setLatLng(S.origin).addTo(map);
  markerB.setLatLng(S.dest).addTo(map);
  setMe(S.origin);
  S.route = null;
  computeRoute().then((res) => res && map.fitBounds(L.latLngBounds([...res.data.segura.coords, ...res.data.corta.coords]).pad(0.2)));
}

map.on("click", (e) => {
  if (!S.picking) return;
  const p = [e.latlng.lat, e.latlng.lng];
  if (S.pickStep === 0) {
    S.origin = p; markerA.setLatLng(p).addTo(map); setMe(p);
    S.pickStep = 1;
    toast("Ahora marca el destino");
  } else {
    S.dest = p; markerB.setLatLng(p).addTo(map);
    S.picking = false; $("#pickHint").hidden = true;
    S.route = null;
    computeRoute();
  }
});

// ---------- reportes ----------
async function loadReports() {
  reportsLayer.clearLayers();
  if (!$("#showReports").checked) return;
  const reps = await api("/api/reportes");
  for (const r of reps) {
    const cls = `pin ${r.verificado ? "" : "sin-verificar"} ${r.categoria === "patrulla" ? "patrulla" : ""}`;
    const m = L.marker([r.lat, r.lon], { icon: icon(cls, r.icono), opacity: 0.35 + 0.65 * r.vigencia });
    const edad = r.edad_min < 1 ? "hace un momento" : r.edad_min < 60 ? `hace ${Math.round(r.edad_min)} min` : `hace ${Math.round(r.edad_min / 60)} h`;
    m.bindPopup(() => {
      const div = document.createElement("div");
      div.className = "popup";
      div.innerHTML = `<h4>${r.icono} ${r.nombre}</h4>
        <p>${edad} · vigencia ${Math.round(r.vigencia * 100)}%</p>
        <p>${r.verificado ? "✅ Verificado" : `Sin verificar (${r.confirmaciones}/2 confirmaciones)`}</p>
        ${r.verificado ? "" : '<button class="btn">Yo también lo veo</button>'}`;
      const b = div.querySelector("button");
      if (b) b.onclick = async () => {
        try {
          await api(`/api/reportes/${r.id}/confirmar`, { posicion: S.me });
          toast("Gracias, confirmaste el reporte");
          map.closePopup();
        } catch (e) { toast(e.message, true); }
      };
      return div;
    });
    m.addTo(reportsLayer);
  }
}

function openReport(target) {
  if (!S.me) { toast("Primero elige un trayecto", true); return; }
  S.reportTarget = target || S.me;
  $("#reportWhere").textContent = target
    ? "Se reporta en el punto que marcaste (debe estar a menos de 150 m de “Tú”). Es anónimo."
    : "Se reporta en tu posición actual (marcador “Tú”). Es anónimo.";
  $("#reportDialog").showModal();
}

function buildReportGrid() {
  const grid = $("#reportGrid");
  for (const [key, c] of Object.entries(S.meta.categorias)) {
    const b = document.createElement("button");
    b.type = "button";
    b.innerHTML = `<span>${c.icono}</span>${c.nombre}`;
    b.onclick = async () => {
      $("#reportDialog").close();
      const [lat, lon] = S.reportTarget;
      try {
        await api("/api/reportes", { lat, lon, categoria: key, posicion: S.me });
        toast(`Reporte enviado: ${c.icono} ${c.nombre}`);
      } catch (e) { toast(e.message, true); }
    };
    grid.appendChild(b);
  }
}

// Clic derecho / mantener presionado: reportar en otro punto (se valida que estés cerca)
map.on("contextmenu", (e) => openReport([e.latlng.lat, e.latlng.lng]));

// ---------- eventos en vivo ----------
function connectEvents() {
  const es = new EventSource("/api/eventos");
  const onChange = async (ev) => {
    const d = ev.data ? JSON.parse(ev.data) : {};
    await loadReports();
    if ($("#showRisk").checked) await updateRisk();
    if (!S.route) return;
    let reason = "cambió el estado de los reportes";
    if (ev.type === "reporte") reason = `nuevo reporte: ${d.icono} ${d.nombre}`;
    if (ev.type === "confirmacion") reason = `reporte ${d.verificado ? "verificado" : "confirmado"}: ${d.icono} ${d.nombre}`;
    const res = await computeRoute({ from: S.walking ? S.me : null, reason });
    if (res && S.walking && res.changed) startWalkOn(res.data.segura.coords);
    if (res && !res.changed && ev.type === "reporte") banner(`${d.icono} Nuevo reporte: ${d.nombre}. Tu ruta no cambia.`);
  };
  ["reporte", "confirmacion", "limpiar"].forEach((t) => es.addEventListener(t, onChange));
}

// ---------- simulación de caminata ----------
const WALK_STEP_M = 4;    // metros por cuadro
const WALK_TICK_MS = 100; // ≈ 30x la velocidad real

function startWalkOn(coords) {
  S.walkPath = coords.slice();
  S.walkPos = 0;
}

function walkTick() {
  let remaining = WALK_STEP_M;
  while (remaining > 0 && S.walkPos < S.walkPath.length - 1) {
    const a = S.me, b = S.walkPath[S.walkPos + 1];
    const d = distM(a, b);
    if (d <= remaining) {
      S.me = b; S.walkPos += 1; remaining -= d;
    } else {
      const f = remaining / d;
      S.me = [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
      remaining = 0;
    }
  }
  markerMe.setLatLng(S.me);
  if (S.walkPos >= S.walkPath.length - 1) {
    stopWalk();
    toast("Llegaste a tu destino 🎉");
  }
}

function startWalk() {
  if (!S.route) { toast("Primero elige un trayecto", true); return; }
  setMe(S.route.segura.coords[0]);
  startWalkOn(S.route.segura.coords);
  S.walking = true;
  $("#walkBtn").textContent = "⏸ Detener caminata";
  S.walkTimer = setInterval(walkTick, WALK_TICK_MS);
  banner("Caminata simulada: reporta algo desde otra pestaña y mira cómo se recalcula la ruta.", false, 5000);
}

function stopWalk() {
  clearInterval(S.walkTimer);
  S.walking = false;
  $("#walkBtn").textContent = "▶ Simular caminata";
}

// ---------- GPS ----------
function setupGps() {
  const btn = $("#gpsBtn");
  if (!window.isSecureContext || !navigator.geolocation) {
    btn.disabled = true;
    btn.title = "El GPS solo funciona en localhost o con HTTPS (ver README).";
    return;
  }
  btn.onclick = () => {
    if (S.geoWatch !== null) {
      navigator.geolocation.clearWatch(S.geoWatch);
      S.geoWatch = null;
      btn.textContent = "📍 Usar mi GPS";
      return;
    }
    stopWalk();
    S.geoWatch = navigator.geolocation.watchPosition(
      (pos) => { setMe([pos.coords.latitude, pos.coords.longitude]); },
      (err) => { toast(`GPS: ${err.message}`, true); },
      { enableHighAccuracy: true, maximumAge: 5000 },
    );
    btn.textContent = "📍 GPS activo";
  };
}

// ---------- arranque ----------
async function init() {
  S.meta = await api("/api/meta");
  const m = S.meta;
  map.fitBounds([[m.bbox[0], m.bbox[1]], [m.bbox[2], m.bbox[3]]]);

  if (m.mapa_sintetico) {
    addNotice("Mapa SINTÉTICO de prueba: las calles no son las reales. Ejecuta scripts/descargar_mapa.py con internet.");
    S.offline = true; // el mapa base real no coincidiría con la cuadrícula
    map.removeLayer(tiles);
  }
  const fuentes = m.incidentes || {};
  if (fuentes.simulado && !fuentes.fgj) addNotice(`Histórico de incidentes SIMULADO (${fuentes.simulado}). Importa datos reales con scripts/importar_fgj.py.`);
  else if (fuentes.fgj) addNotice(`Histórico: ${fuentes.fgj} incidentes de la FGJ-CDMX${fuentes.simulado ? ` + ${fuentes.simulado} simulados` : ""}.`);

  const sel = $("#preset");
  for (const t of m.trayectos) sel.add(new Option(t.nombre, t.id));
  sel.add(new Option("📍 Elegir en el mapa…", "custom"));
  sel.onchange = () => applyPreset(sel.value);

  const hour = $("#hour");
  hour.value = m.hora_actual;
  $("#hourLabel").textContent = hourText(+hour.value);
  const onParams = debounce(() => { computeRoute(); updateRisk(); }, 200);
  hour.oninput = () => { $("#hourLabel").textContent = hourText(+hour.value); onParams(); };
  $("#nowBtn").onclick = () => { hour.value = new Date().getHours(); hour.oninput(); };

  const alpha = $("#alpha");
  alpha.max = m.alfa_max; alpha.value = m.alfa_defecto;
  $("#alphaLabel").textContent = alpha.value;
  const onAlpha = debounce(() => computeRoute(), 200);
  alpha.oninput = () => { $("#alphaLabel").textContent = alpha.value; onAlpha(); };

  $("#showRisk").onchange = updateRisk;
  $("#showReports").onchange = loadReports;
  $("#reportFab").onclick = () => openReport(null);
  $("#walkBtn").onclick = () => (S.walking ? stopWalk() : startWalk());
  $("#togglePanel").onclick = () => $("#panel").classList.toggle("collapsed");
  $("#clearBtn").onclick = async () => {
    if (!window.confirm("¿Borrar todos los reportes de la demostración?")) return;
    await api("/api/demo/limpiar", {});
  };

  buildReportGrid();
  setupGps();
  await loadStreets();
  await loadReports();
  connectEvents();
  if (m.trayectos.length) { sel.value = m.trayectos[0].id; applyPreset(sel.value); }
}

init().catch((e) => { console.error(e); toast(`Error al iniciar: ${e.message}`, true); });
