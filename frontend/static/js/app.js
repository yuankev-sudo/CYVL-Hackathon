// ClearPath — show all three routing profiles at once.

const SOMERVILLE_CENTER = [42.387, -71.100];
const SOMERVILLE_ZOOM   = 14;

const map = L.map("map").setView(SOMERVILLE_CENTER, SOMERVILLE_ZOOM);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: "© OpenStreetMap, © CARTO", maxZoom: 20,
}).addTo(map);

// ── State ────────────────────────────────────────────────────────────────────
let activeProfile = "fastest";
let clickMode     = "start";
let startLatLng   = null, endLatLng = null;
let startMarker   = null, endMarker = null;
let networkLayer  = null;
let routeLayers   = {};        // { fastest: L.GeoJSON, smoothest: ..., largevehicle: ... }
let blockedLayers = [];
let lastRouteData = null;
let VEHICLES      = {};

const $ = id => document.getElementById(id);

// ── Profile metadata ──────────────────────────────────────────────────────────
const PROFILES = {
  fastest: {
    icon: "🚗", label: "Fastest", color: "#f59e0b",
    blurb: "Shortest distance on the real road network.",
    summary: (s) => `No pavement or turn restrictions.`,
  },
  smoothest: {
    icon: "🚑", label: "Smoothest", color: "#38bdf8",
    blurb: "Avoids rough pavement — gentler ride for ambulances.",
    summary: (s) => s.avg_pci != null
      ? `Avg pavement PCI ${s.avg_pci}${s.extra_m > 50 ? ` · +${Math.round(s.extra_m)}m vs fastest` : " · same path"}.`
      : "Prefers well-maintained roads.",
  },
  largevehicle: {
    icon: "🚛", label: "Large Vehicle", color: "#4ade80",
    blurb: "Blocks turns the swept path can't clear, then reroutes.",
    summary: (s, v) => s.blocked_count > 0
      ? `Avoids ${s.blocked_count} turn${s.blocked_count !== 1 ? "s" : ""} too tight for ${v?.name ?? "this vehicle"}.`
      : `All corners clear for ${v?.name ?? "this vehicle"}.`,
  },
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
  sel.value = "FIRE-LADDER";
  fillDims(VEHICLES["FIRE-LADDER"]);
}

function fillDims(v) {
  $("d-length").value    = v.length_ft ?? "";
  $("d-width").value     = v.width_ft ?? "";
  $("d-wheelbase").value = v.wheelbase_ft ?? "";
  $("d-radius").value    = v.turning_radius_ft ?? "";
  refreshSwept();
}

function readDims() {
  return {
    id:                $("vehicle-select").value,
    length_ft:         parseFloat($("d-length").value)    || null,
    width_ft:          parseFloat($("d-width").value)     || null,
    wheelbase_ft:      parseFloat($("d-wheelbase").value) || null,
    turning_radius_ft: parseFloat($("d-radius").value)    || null,
  };
}

async function refreshSwept() {
  try {
    const g = await (await fetch("/api/turning-radius", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readDims()),
    })).json();
    $("swept-hint").innerHTML =
      `Outer swing <b>${g.outer_radius_ft} ft</b> · swept band <b>${g.swept_width_ft} ft</b> wide.`;
  } catch (_) {}
}

async function deriveRadius() {
  const dims = { ...readDims(), turning_radius_ft: null };
  try {
    const g = await (await fetch("/api/turning-radius", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dims),
    })).json();
    $("d-radius").value = g.turning_radius_ft;
    refreshSwept();
  } catch (_) {}
}

// ── Road network (muted background — routes pop on top) ───────────────────────
async function loadNetwork() {
  setLoading(true, "Loading road network…");
  try {
    const fc = await (await fetch("/api/network")).json();
    networkLayer = L.geoJSON(fc, {
      filter: f => f.geometry.type === "LineString",
      // Uniform muted gray — PCI info available on hover but doesn't compete with routes
      style: { color: "#64748b", weight: 1.5, opacity: 0.28 },
      onEachFeature: (f, layer) => {
        const p = f.properties;
        if (p.pci_score != null)
          layer.bindTooltip(
            `<b>${p.pci_label}</b> · PCI ${p.pci_score.toFixed(0)}<br>${p.length_m}m`,
            { sticky: true }
          );
      },
    }).addTo(map);
  } catch (e) { console.error("Network load failed:", e); }
  finally { setLoading(false); }
}

// ── Profile tab selection ──────────────────────────────────────────────────────
document.querySelectorAll(".profile").forEach(btn =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".profile").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const p = btn.dataset.profile;
    $("profile-blurb").textContent = PROFILES[p].blurb;
    $("truck-panel").classList.toggle("hidden", p !== "largevehicle");
  })
);

// ── Pin placement ──────────────────────────────────────────────────────────────
$("btn-set-start").addEventListener("click", () => setMode("start"));
$("btn-set-end").addEventListener("click",   () => setMode("end"));

function setMode(mode) {
  clickMode = mode;
  $("btn-set-start").classList.toggle("active", mode === "start");
  $("btn-set-end").classList.toggle("active",   mode === "end");
  $("pin-hint").textContent   = mode === "start"
    ? "Click the map to place Start (A)"
    : "Click the map to place End (B)";
  $("pin-hint").classList.remove("hidden");
  map.getContainer().style.cursor = "crosshair";
}

map.on("click", e => {
  if (!clickMode) return;
  if (clickMode === "start") {
    startLatLng = e.latlng;
    if (startMarker) map.removeLayer(startMarker);
    startMarker = L.marker(e.latlng, { icon: pinIcon("#2563eb", "A") })
      .addTo(map).bindPopup("Start (A)");
    $("start-coords").textContent = `${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`;
    $("pin-summary").classList.remove("hidden");
    setMode("end");
  } else {
    endLatLng = e.latlng;
    if (endMarker) map.removeLayer(endMarker);
    endMarker = L.marker(e.latlng, { icon: pinIcon("#059669", "B") })
      .addTo(map).bindPopup("End (B)");
    $("end-coords").textContent = `${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`;
    clickMode = null;
    map.getContainer().style.cursor = "";
    $("pin-hint").classList.add("hidden");
    document.querySelectorAll(".pin-btn").forEach(b => b.classList.remove("active"));
  }
  $("route-btn").disabled = !(startLatLng && endLatLng);
});

// ── Corner 3D maneuver simulator ───────────────────────────────────────────────
// prev/next route context for a node, taken from the large-vehicle route the
// truck actually drives (feasibility is now evaluated along that route).
function routeContext(nodeId) {
  const nodes = lastRouteData?.lv_nodes
             || lastRouteData?.routes?.largevehicle?.geojson?.properties?.nodes || [];
  const i = nodes.indexOf(nodeId);
  return {
    prev_id: i > 0 ? nodes[i - 1] : null,
    next_id: (i >= 0 && i < nodes.length - 1) ? nodes[i + 1] : null,
  };
}

// Only conflict corners are clickable -> we only build a 3D scene on demand.
async function openCornerSim(b) {
  if (!window.openCorner3D) { alert("3D module still loading — try again in a second."); return; }
  setLoading(true, "Building 3D corner…");
  try {
    const res = await fetch("/api/corner", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        node_id: b.node_id, ...routeContext(b.node_id),
        vehicle_id: $("vehicle-select").value, vehicle: readDims(),
      }),
    });
    if (!res.ok) { alert("Corner sim error: " + ((await res.json()).detail || res.statusText)); return; }
    window.openCorner3D(await res.json());
  } catch (e) { alert("Corner sim failed: " + e.message); }
  finally { setLoading(false); }
}

// ── Routing ──────────────────────────────────────────────────────────────────
$("route-btn").addEventListener("click", findRoute);

async function findRoute() {
  if (!startLatLng || !endLatLng) return;
  setLoading(true, "Computing 3 routes…");
  clearRoutes();

  try {
    const body = {
      start_lat:  startLatLng.lat, start_lon: startLatLng.lng,
      end_lat:    endLatLng.lat,   end_lon:   endLatLng.lng,
      vehicle_id: $("vehicle-select").value,
      vehicle:    readDims(),
    };
    const res = await fetch("/api/route/all", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json();
      alert("Routing error: " + (err.detail || res.statusText));
      return;
    }
    lastRouteData = await res.json();
    renderAllRoutes(lastRouteData);
    renderRouteSummaries(lastRouteData);
  } catch (e) {
    alert("Request failed: " + e.message);
  } finally {
    setLoading(false);
  }
}

// ── Draw all 3 routes ──────────────────────────────────────────────────────────
function renderAllRoutes(data) {
  Object.keys(PROFILES).forEach(profile => {
    const r = data.routes[profile];
    if (!r) return;
    const color   = r.geojson.properties.color;
    const isActive = profile === activeProfile;
    const layer = L.geoJSON(r.geojson, {
      style: routeStyle(color, isActive),
    }).addTo(map);
    layer.bindTooltip(PROFILES[profile].label, { sticky: true });
    routeLayers[profile] = layer;
  });

  // Blocked intersections (from large-vehicle feasibility — always shown)
  (data.feasibility || []).filter(b => b.verdict !== "pass").forEach(b => {
    const color = b.verdict === "fail" ? "#ef4444" : "#f59e0b";
    const circle = L.circleMarker([b.lat, b.lon], {
      radius: 7, color, fillColor: color, fillOpacity: 0.9, weight: 2,
    }).addTo(map)
      .bindTooltip(`<b>${b.verdict.toUpperCase()}</b> — ${b.reason || ""}<br><i>click to simulate</i>`, { sticky: true })
      .on("click", () => openCornerSim(b));
    blockedLayers.push(circle);
  });

  // Fit to the active route
  const target = routeLayers[activeProfile];
  if (target) { try { map.fitBounds(target.getBounds().pad(0.15)); } catch (_) {} }
}

function routeStyle(color, isActive) {
  return {
    color,
    weight:    isActive ? 7 : 2.5,
    opacity:   isActive ? 1.0 : 0.35,
    dashArray: isActive ? null : "5 4",
  };
}

function selectProfile(profile) {
  activeProfile = profile;
  Object.entries(routeLayers).forEach(([p, layer]) => {
    const color = PROFILES[p].color;
    layer.setStyle(routeStyle(color, p === profile));
    if (p === profile) layer.bringToFront();
  });
  document.querySelectorAll(".route-card").forEach(c => {
    c.classList.toggle("active", c.dataset.profile === profile);
  });
}

// ── Route summary cards ────────────────────────────────────────────────────────
function renderRouteSummaries(data) {
  const el = $("results");
  el.innerHTML = "";

  const vehicle = data.vehicle;
  const fastest_len = data.routes.fastest?.stats?.length_m;

  const container = document.createElement("div");
  container.className = "route-cards";

  Object.entries(PROFILES).forEach(([profile, meta]) => {
    const r = data.routes[profile];
    if (!r) return;
    const s = r.stats;

    const km     = s.length_m != null ? `${(s.length_m / 1000).toFixed(2)} km` : "—";
    const extra  = s.extra_m > 50 ? `+${(s.extra_m / 1000).toFixed(2)} km` : null;
    const pci    = s.avg_pci  != null ? `PCI ${s.avg_pci}` : null;
    const sumText = profile === "largevehicle"
      ? meta.summary(s, vehicle)
      : meta.summary(s);

    const card = document.createElement("div");
    card.className = `route-card${profile === activeProfile ? " active" : ""}`;
    card.dataset.profile = profile;
    card.style.setProperty("--accent", meta.color);

    const pills = [pci, extra].filter(Boolean)
      .map(t => `<span class="rc-pill">${t}</span>`).join("");

    card.innerHTML = `
      <div class="rc-top">
        <span class="rc-icon">${meta.icon}</span>
        <span class="rc-name">${meta.label}</span>
        <span class="rc-dist" style="color:${meta.color}">${km}</span>
      </div>
      <div class="rc-pills">${pills}</div>
      <div class="rc-summary">${sumText}</div>
    `;
    card.addEventListener("click", () => selectProfile(profile));
    container.appendChild(card);
  });

  el.appendChild(container);

  // Blocked turns detail (largevehicle only, collapsible)
  const fails = (data.feasibility || []).filter(b => b.verdict !== "pass");
  if (fails.length) {
    const header = document.createElement("div");
    header.className = "section-label";
    header.style.marginTop = "8px";
    header.textContent = `Blocked turns (${fails.length})`;
    el.appendChild(header);

    fails.forEach(b => {
      const card = document.createElement("div");
      card.className = `result-card ${b.verdict}`;
      card.innerHTML = `
        <div class="name">Node ${b.node_id.slice(0, 8)}</div>
        <span class="badge ${b.verdict}">${b.verdict}</span>
        <div class="reason">${b.reason || ""}</div>
        <div class="sim-hint">▶ Click to simulate the turn in 3D</div>
      `;
      card.addEventListener("click", () => {
        selectProfile("largevehicle");
        openCornerSim(b);
      });
      el.appendChild(card);
    });
  }
}

// ── Clear ──────────────────────────────────────────────────────────────────────
$("clear-btn").addEventListener("click", clearAll);

function clearRoutes() {
  Object.values(routeLayers).forEach(l => map.removeLayer(l));
  routeLayers = {};
  blockedLayers.forEach(l => map.removeLayer(l));
  blockedLayers = [];
}

function clearAll() {
  clearRoutes();
  [startMarker, endMarker].forEach(m => m && map.removeLayer(m));
  startMarker = endMarker = startLatLng = endLatLng = null;
  $("results").innerHTML = "";
  $("stats-card").classList.add("hidden");
  $("pin-summary").classList.add("hidden");
  $("start-coords").textContent = $("end-coords").textContent = "—";
  $("route-btn").disabled = true;
  lastRouteData = null;
  setMode("start");
}

// ── Loading overlay ──────────────────────────────────────────────────────────
function setLoading(on, text) {
  const el = $("loading-overlay");
  if (on) { el.querySelector(".loading-text").textContent = text || "Loading…"; el.classList.remove("hidden"); }
  else el.classList.add("hidden");
}

// ── Form events ───────────────────────────────────────────────────────────────
$("vehicle-select").addEventListener("change", e => fillDims(VEHICLES[e.target.value]));
$("d-wheelbase").addEventListener("change", deriveRadius);
["d-length", "d-width", "d-radius"].forEach(id => $(id).addEventListener("change", refreshSwept));

// ── Init ──────────────────────────────────────────────────────────────────────
$("profile-blurb").textContent = PROFILES[activeProfile].blurb;
loadVehicles();
loadNetwork();
setMode("start");
