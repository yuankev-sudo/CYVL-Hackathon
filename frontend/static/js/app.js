// ClearPath — profile-aware A-to-B routing on the real Somerville network.

const SOMERVILLE_CENTER = [42.387, -71.100];
const SOMERVILLE_ZOOM   = 14;

const map = L.map("map").setView(SOMERVILLE_CENTER, SOMERVILLE_ZOOM);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: "© OpenStreetMap, © CARTO", maxZoom: 20,
}).addTo(map);

// ── State ────────────────────────────────────────────────────────────────────
let profile     = "fastest";
let clickMode   = "start";
let startLatLng = null, endLatLng = null;
let startMarker = null, endMarker = null;
let networkLayer = null, naiveLayer = null, safeLayer = null;
let blockedLayers = [];
let VEHICLES = {};

const $ = id => document.getElementById(id);

const PROFILE_BLURB = {
  fastest:      "Shortest distance on the real road network.",
  smoothest:    "Avoids rough pavement (low PCI) — a gentler ride for ambulances.",
  largevehicle: "Blocks turns the vehicle's swept path can't clear, then reroutes around them.",
};

// ── Marker icons ─────────────────────────────────────────────────────────────
function pinIcon(color, label) {
  return L.divIcon({
    className: "",
    html: `<div style="background:${color};width:28px;height:28px;border-radius:50% 50% 50% 0;
      transform:rotate(-45deg);border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.4);
      display:flex;align-items:center;justify-content:center;">
      <span style="transform:rotate(45deg);color:#fff;font-weight:700;font-size:13px">${label}</span></div>`,
    iconSize: [28, 28], iconAnchor: [14, 28], popupAnchor: [0, -30],
  });
}

// ── Vehicles + dimension form ─────────────────────────────────────────────────
async function loadVehicles() {
  VEHICLES = await (await fetch("/api/vehicles")).json();
  const sel = $("vehicle-select");
  for (const [id, v] of Object.entries(VEHICLES)) sel.add(new Option(v.name, id));
  sel.value = "WB-67";
  fillDims(VEHICLES["WB-67"]);
}

function fillDims(v) {
  $("d-length").value = v.length_ft ?? "";
  $("d-width").value = v.width_ft ?? "";
  $("d-wheelbase").value = v.wheelbase_ft ?? "";
  $("d-radius").value = v.turning_radius_ft ?? "";
  refreshSwept();
}

function readDims() {
  return {
    id: $("vehicle-select").value,
    length_ft: parseFloat($("d-length").value) || null,
    width_ft: parseFloat($("d-width").value) || null,
    wheelbase_ft: parseFloat($("d-wheelbase").value) || null,
    turning_radius_ft: parseFloat($("d-radius").value) || null,
  };
}

async function turningGeometry(dims) {
  return (await fetch("/api/turning-radius", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(dims),
  })).json();
}

// Re-derive turning radius from wheelbase (user changed wheelbase)
async function deriveRadius() {
  const dims = readDims();
  dims.turning_radius_ft = null;          // force re-derivation
  const g = await turningGeometry(dims);
  $("d-radius").value = g.turning_radius_ft;
  showSwept(g);
}

async function refreshSwept() { showSwept(await turningGeometry(readDims())); }

function showSwept(g) {
  $("swept-hint").innerHTML =
    `Outer swing radius <b>${g.outer_radius_ft} ft</b> · swept band <b>${g.swept_width_ft} ft</b> wide. ` +
    `Checked against every corner's geometry along the route.`;
}

// ── Road network ──────────────────────────────────────────────────────────────
async function loadNetwork() {
  setLoading(true, "Loading road network…");
  try {
    const fc = await (await fetch("/api/network")).json();
    networkLayer = L.geoJSON(fc, {
      filter: f => f.geometry.type === "LineString",
      style: f => ({ color: f.properties.color || "#888", weight: 2.5, opacity: 0.7 }),
      onEachFeature: (f, layer) => {
        const p = f.properties;
        if (p.pci_score != null)
          layer.bindTooltip(`PCI ${p.pci_score.toFixed(0)} — ${p.pci_label}<br>${p.length_m}m`, { sticky: true });
      },
    }).addTo(map);
  } catch (e) { console.error("Network load failed:", e); }
  finally { setLoading(false); }
}

// ── Profile selection ──────────────────────────────────────────────────────────
document.querySelectorAll(".profile").forEach(btn => btn.addEventListener("click", () => {
  document.querySelectorAll(".profile").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  profile = btn.dataset.profile;
  $("profile-blurb").textContent = PROFILE_BLURB[profile];
  $("truck-panel").classList.toggle("hidden", profile !== "largevehicle");
  $("route-btn").textContent = profile === "largevehicle" ? "Check & Route" : "Get Directions";
}));

// ── Pin placement ──────────────────────────────────────────────────────────────
$("btn-set-start").addEventListener("click", () => setMode("start"));
$("btn-set-end").addEventListener("click", () => setMode("end"));

function setMode(mode) {
  clickMode = mode;
  $("btn-set-start").classList.toggle("active", mode === "start");
  $("btn-set-end").classList.toggle("active", mode === "end");
  const hint = $("pin-hint");
  hint.textContent = mode === "start" ? "Click the map to place Start (A)" : "Click the map to place End (B)";
  hint.classList.remove("hidden");
  map.getContainer().style.cursor = "crosshair";
}

map.on("click", e => {
  if (!clickMode) return;
  if (clickMode === "start") {
    startLatLng = e.latlng;
    if (startMarker) map.removeLayer(startMarker);
    startMarker = L.marker(e.latlng, { icon: pinIcon("#2563eb", "A") }).addTo(map).bindPopup("Start (A)");
    $("start-coords").textContent = `${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`;
    $("pin-summary").classList.remove("hidden");
    setMode("end");
  } else {
    endLatLng = e.latlng;
    if (endMarker) map.removeLayer(endMarker);
    endMarker = L.marker(e.latlng, { icon: pinIcon("#059669", "B") }).addTo(map).bindPopup("End (B)");
    $("end-coords").textContent = `${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`;
    clickMode = null;
    map.getContainer().style.cursor = "";
    $("pin-hint").classList.add("hidden");
    document.querySelectorAll(".pin-btn").forEach(b => b.classList.remove("active"));
  }
  $("route-btn").disabled = !(startLatLng && endLatLng);
});

// ── Routing ──────────────────────────────────────────────────────────────────
$("route-btn").addEventListener("click", findRoute);

async function findRoute() {
  if (!startLatLng || !endLatLng) return;
  setLoading(true, "Computing route…");
  clearRoutes();
  try {
    const body = {
      start_lat: startLatLng.lat, start_lon: startLatLng.lng,
      end_lat: endLatLng.lat, end_lon: endLatLng.lng,
      profile,
      vehicle_id: profile === "largevehicle" ? $("vehicle-select").value : null,
      vehicle: profile === "largevehicle" ? readDims() : null,
    };
    const res = await fetch("/api/route/dynamic", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok) { const err = await res.json(); alert("Routing error: " + (err.detail || res.statusText)); return; }
    const data = await res.json();
    renderRoutes(data);
    renderResults(data);
  } catch (e) { alert("Request failed: " + e.message); }
  finally { setLoading(false); }
}

function renderRoutes(data) {
  const truck = data.profile === "largevehicle";

  // Naive baseline — only show when the chosen route differs.
  if (data.naive_route && data.rerouted) {
    naiveLayer = L.geoJSON(data.naive_route, {
      style: { color: "#ef4444", weight: 4, dashArray: "8 5", opacity: 0.85 },
    }).addTo(map).bindTooltip(truck ? "Naive route — turns it can't clear" : "Fastest route", { sticky: true });
  }
  // Chosen route.
  if (data.safe_route) {
    const c = data.safe_route.properties.color || "#22c55e";
    safeLayer = L.geoJSON(data.safe_route, { style: { color: c, weight: 5, opacity: 0.95 } })
      .addTo(map).bindTooltip(data.rerouted ? "ClearPath route" : "Route", { sticky: true });
  }
  // Blocked / tight intersections (truck profile only).
  if (truck) {
    (data.blocked_intersections || []).filter(b => b.verdict !== "pass").forEach(b => {
      const color = b.verdict === "fail" ? "#ef4444" : "#f59e0b";
      const circle = L.circleMarker([b.lat, b.lon], { radius: 8, color, fillColor: color, fillOpacity: 0.9, weight: 2 })
        .addTo(map).bindPopup(
          `<b>${b.verdict.toUpperCase()}</b><br>${b.reason || ""}<div class="popup-photos">Loading site photos…</div>`,
          { maxWidth: 260 });
      // Fill in the photo(s) the first time the popup opens.
      circle.on("popupopen", async ev => {
        const host = ev.popup.getElement().querySelector(".popup-photos");
        if (!host || host.dataset.loaded) return;
        host.dataset.loaded = "1";
        try {
          const r = await fetch(`/api/intersection/imagery?lon=${b.lon}&lat=${b.lat}&verdict=${b.verdict}`);
          const { images = [] } = r.ok ? await r.json() : {};
          if (!images.length) { host.remove(); return; }
          host.innerHTML = "";
          const im = images[0];
          const t = document.createElement("img");
          t.src = thumbSrc(im, 480);
          t.alt = im.caption || "Intersection photo";
          t.title = im.viewer_url ? "Open interactive 3D view" : "Open full photo";
          if (im.viewer_url) t.classList.add("is-3d");
          t.addEventListener("click", () => openPhoto(im));
          host.appendChild(t);
          ev.popup.update();
        } catch (_) { host.remove(); }
      });
      blockedLayers.push(circle);
    });
  }
  const target = safeLayer || naiveLayer;
  if (target) { try { map.fitBounds(target.getBounds().pad(0.15)); } catch (_) {} }
}

function renderResults(data) {
  const s = data.stats || {};
  const truck = data.profile === "largevehicle";
  const smooth = data.profile === "smoothest";
  const km = m => m != null ? `${(m / 1000).toFixed(2)} km` : "—";

  // Stats card
  let rows = `<div class="stats-row"><span class="stats-label">Distance</span>
      <span class="stats-val">${km(s.safe_length_m)}</span></div>`;
  if (data.rerouted)
    rows += `<div class="stats-row"><span class="stats-label">Fastest (naive)</span>
        <span class="stats-val">${km(s.naive_length_m)}</span></div>`;
  if (s.safe_avg_pci != null) {
    const up = smooth && data.rerouted && s.safe_avg_pci > (s.naive_avg_pci ?? 0);
    rows += `<div class="stats-row"><span class="stats-label">Avg pavement (PCI)</span>
        <span class="stats-val ${up ? "up" : ""}">${s.safe_avg_pci}${up ? ` ▲ from ${s.naive_avg_pci}` : ""}</span></div>`;
  }
  if (truck) {
    rows += `<div class="stats-row"><span class="stats-label">Turns checked</span>
        <span class="stats-val">${s.intersections_checked ?? "—"}</span></div>
      <div class="stats-row"><span class="stats-label">Blocked turns</span>
        <span class="stats-val ${s.blocked_count ? "warn" : ""}">${s.blocked_count ?? "—"}</span></div>`;
    if (data.vehicle)
      rows += `<div class="stats-row"><span class="stats-label">${data.vehicle.name}</span>
          <span class="stats-val">${data.vehicle.outer_radius_ft} ft swing</span></div>`;
  }
  $("stats-card").innerHTML = rows;
  $("stats-card").classList.remove("hidden");

  // Result cards
  const el = $("results");
  el.innerHTML = "";
  const status = data.rerouted ? (truck ? "fail" : "tight") : "pass";
  const summary = document.createElement("div");
  summary.className = `result-card ${status}`;
  let msg;
  if (truck && data.rerouted) msg = `Rerouted around ${s.blocked_count} turn${s.blocked_count !== 1 ? "s" : ""} the swept path can't clear`;
  else if (truck) msg = "All turns clear for this vehicle";
  else if (smooth && data.rerouted) msg = `Smoother route — avg PCI ${s.safe_avg_pci} vs ${s.naive_avg_pci}`;
  else if (smooth) msg = "Fastest route is already the smoothest";
  else msg = "Shortest path on the network";
  summary.innerHTML = `<div class="name">${truck && data.vehicle ? data.vehicle.name : data.profile}</div>
    <span class="badge ${status}">${data.rerouted ? "Rerouted" : "Clear"}</span>
    <div class="reason">${msg}</div>`;
  el.appendChild(summary);

  if (truck) {
    (data.blocked_intersections || []).filter(b => b.verdict !== "pass").forEach(b => {
      const card = document.createElement("div");
      card.className = `result-card ${b.verdict}`;
      card.innerHTML = `<div class="name">Intersection ${b.node_id.slice(0, 8)}</div>
        <span class="badge ${b.verdict}">${b.verdict}</span>
        <div class="reason">${b.reason || ""}</div>
        <div class="reason coords">${b.lat.toFixed(5)}, ${b.lon.toFixed(5)}</div>
        <div class="photos" aria-live="polite"></div>`;
      // Center the map when the card body is clicked (but not when a photo is).
      card.addEventListener("click", e => { if (!e.target.closest(".photos")) map.setView([b.lat, b.lon], 17); });
      el.appendChild(card);
      loadCardImagery(card.querySelector(".photos"), b);
    });
  }
}

// Lazily fetch street-level photos of why a turn fails and append thumbnails.
async function loadCardImagery(host, b) {
  if (!host) return;
  host.innerHTML = `<span class="photos-status">Loading site photos…</span>`;
  try {
    const res = await fetch(`/api/intersection/imagery?lon=${b.lon}&lat=${b.lat}&verdict=${b.verdict}`);
    const { images = [] } = res.ok ? await res.json() : {};
    if (!images.length) { host.innerHTML = ""; return; }
    const has3d = images.some(img => img.viewer_url);
    host.innerHTML = `<div class="photos-label">${has3d ? "3D site photos" : "Site photos"} — why this turn fails</div>`;
    const strip = document.createElement("div");
    strip.className = "photo-strip";
    images.forEach(img => {
      const t = document.createElement("img");
      t.src = thumbSrc(img, 240);
      t.loading = "lazy";
      t.alt = img.caption || "Intersection street-level photo";
      t.title = img.viewer_url ? "Open interactive 3D view" : (img.caption || "Open full photo");
      if (img.viewer_url) t.classList.add("is-3d");
      t.addEventListener("click", e => { e.stopPropagation(); openPhoto(img); });
      strip.appendChild(t);
    });
    host.appendChild(strip);
  } catch (_) { host.innerHTML = ""; }
}

// Route a frame's jpg through the backend resize+cache proxy so the big
// full-res 360° images don't have to download raw as thumbnails.
function thumbSrc(img, w) {
  const raw = img.thumb || img.url;
  return `/api/intersection/photo-thumb?w=${w}&url=${encodeURIComponent(raw)}`;
}

// Open a frame: interactive 3D/360° viewer when available, else flat lightbox.
function openPhoto(img) {
  if (img && img.viewer_url) {
    window.open(img.viewer_url, "_blank", "noopener");
    return;
  }
  openLightbox(img.url, img.caption);
}

// Minimal fullscreen photo viewer.
function openLightbox(url, caption) {
  const ov = document.createElement("div");
  ov.className = "lightbox";
  ov.innerHTML = `<figure><img src="${url}" alt="${caption || ""}">
    ${caption ? `<figcaption>${caption}</figcaption>` : ""}</figure>`;
  ov.addEventListener("click", () => ov.remove());
  document.body.appendChild(ov);
}

// ── Clear ──────────────────────────────────────────────────────────────────────
$("clear-btn").addEventListener("click", clearAll);

function clearRoutes() {
  [naiveLayer, safeLayer].forEach(l => l && map.removeLayer(l));
  naiveLayer = safeLayer = null;
  blockedLayers.forEach(l => map.removeLayer(l));
  blockedLayers = [];
}

function clearAll() {
  clearRoutes();
  [startMarker, endMarker].forEach(m => m && map.removeLayer(m));
  startMarker = endMarker = null;
  startLatLng = endLatLng = null;
  $("results").innerHTML = "";
  $("stats-card").classList.add("hidden");
  $("pin-summary").classList.add("hidden");
  $("start-coords").textContent = $("end-coords").textContent = "—";
  $("route-btn").disabled = true;
  setMode("start");
}

// ── Loading overlay ──────────────────────────────────────────────────────────
function setLoading(on, text) {
  const el = $("loading-overlay");
  if (on) { el.querySelector(".loading-text").textContent = text || "Loading…"; el.classList.remove("hidden"); }
  else el.classList.add("hidden");
}

// ── Form events + init ─────────────────────────────────────────────────────────
$("vehicle-select").addEventListener("change", e => fillDims(VEHICLES[e.target.value]));
$("d-wheelbase").addEventListener("change", deriveRadius);
["d-length", "d-width", "d-radius"].forEach(id => $(id).addEventListener("change", refreshSwept));

$("profile-blurb").textContent = PROFILE_BLURB[profile];
loadVehicles();
loadNetwork();
setMode("start");
