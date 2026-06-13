// ClearPath — dynamic A-to-B routing frontend
// Map centered on Somerville, MA

const SOMERVILLE_CENTER = [42.387, -71.100];
const SOMERVILLE_ZOOM   = 14;

const map = L.map("map").setView(SOMERVILLE_CENTER, SOMERVILLE_ZOOM);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

// ── State ────────────────────────────────────────────────────────────────────
let clickMode   = "start";   // "start" | "end" | null
let startLatLng = null;
let endLatLng   = null;
let startMarker = null;
let endMarker   = null;
let networkLayer  = null;
let naiveLayer    = null;
let safeLayer     = null;
let blockedLayers = [];

// ── Marker icons ─────────────────────────────────────────────────────────────
function pinIcon(color, label) {
  return L.divIcon({
    className: "",
    html: `<div style="
      background:${color};width:28px;height:28px;border-radius:50% 50% 50% 0;
      transform:rotate(-45deg);border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.4);
      display:flex;align-items:center;justify-content:center;">
      <span style="transform:rotate(45deg);color:#fff;font-weight:700;font-size:13px">${label}</span>
    </div>`,
    iconSize:   [28, 28],
    iconAnchor: [14, 28],
    popupAnchor:[0, -30],
  });
}

// ── Load vehicles ─────────────────────────────────────────────────────────────
async function loadVehicles() {
  const res = await fetch("/api/vehicles");
  const vehicles = await res.json();
  const sel = document.getElementById("vehicle-select");
  for (const [id, v] of Object.entries(vehicles)) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = `${v.name} (turn r: ${v.turning_radius_ft}ft)`;
    sel.appendChild(opt);
  }
}

// ── Load road network ─────────────────────────────────────────────────────────
async function loadNetwork() {
  setLoading(true, "Loading road network…");
  try {
    const res = await fetch("/api/network");
    const fc  = await res.json();

    networkLayer = L.geoJSON(fc, {
      filter: f => f.geometry.type === "LineString",
      style: f => ({
        color:   f.properties.color || "#aaaaaa",
        weight:  2.5,
        opacity: 0.75,
      }),
      onEachFeature: (f, layer) => {
        const p = f.properties;
        if (p.pci_score != null) {
          layer.bindTooltip(
            `PCI: ${p.pci_score.toFixed(0)} — ${p.pci_label}<br>${p.length_m}m`,
            { sticky: true }
          );
        }
      },
    }).addTo(map);
  } catch (e) {
    console.error("Network load failed:", e);
  } finally {
    setLoading(false);
  }
}

// ── Click mode handling ───────────────────────────────────────────────────────
document.getElementById("btn-set-start").addEventListener("click", () => setMode("start"));
document.getElementById("btn-set-end").addEventListener("click",   () => setMode("end"));

function setMode(mode) {
  clickMode = mode;
  document.getElementById("btn-set-start").classList.toggle("active", mode === "start");
  document.getElementById("btn-set-end").classList.toggle("active",   mode === "end");
  const hint = document.getElementById("pin-hint");
  hint.textContent = mode === "start"
    ? "Click the map to place Start point (A)"
    : "Click the map to place End point (B)";
  hint.classList.remove("hidden");
  map.getContainer().style.cursor = "crosshair";
}

map.on("click", e => {
  if (!clickMode) return;
  if (clickMode === "start") {
    startLatLng = e.latlng;
    if (startMarker) map.removeLayer(startMarker);
    startMarker = L.marker(e.latlng, { icon: pinIcon("#2563eb", "A") })
      .addTo(map)
      .bindPopup("Start (A)");
    document.getElementById("start-coords").textContent =
      `${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`;
    document.getElementById("pin-summary").classList.remove("hidden");
    setMode("end");
  } else if (clickMode === "end") {
    endLatLng = e.latlng;
    if (endMarker) map.removeLayer(endMarker);
    endMarker = L.marker(e.latlng, { icon: pinIcon("#059669", "B") })
      .addTo(map)
      .bindPopup("End (B)");
    document.getElementById("end-coords").textContent =
      `${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`;
    clickMode = null;
    map.getContainer().style.cursor = "";
    document.getElementById("pin-hint").classList.add("hidden");
    document.querySelectorAll(".pin-btn").forEach(b => b.classList.remove("active"));
  }
  updateRouteButton();
});

function updateRouteButton() {
  document.getElementById("route-btn").disabled = !(startLatLng && endLatLng);
}

// ── Find route ────────────────────────────────────────────────────────────────
document.getElementById("route-btn").addEventListener("click", findRoute);

async function findRoute() {
  if (!startLatLng || !endLatLng) return;
  const vehicleId = document.getElementById("vehicle-select").value;

  setLoading(true, "Computing route…");
  clearRoutes();

  try {
    const res = await fetch("/api/route/dynamic", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_lat:  startLatLng.lat,
        start_lon:  startLatLng.lng,
        end_lat:    endLatLng.lat,
        end_lon:    endLatLng.lng,
        vehicle_id: vehicleId,
        pci_penalty: 2.0,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      alert("Routing error: " + (err.detail || res.statusText));
      return;
    }

    const data = await res.json();
    renderRoutes(data);
    renderResults(data);
  } catch (e) {
    alert("Request failed: " + e.message);
  } finally {
    setLoading(false);
  }
}

function renderRoutes(data) {
  // Naive route — red dashed
  if (data.naive_route) {
    naiveLayer = L.geoJSON(data.naive_route, {
      style: { color: "#ef4444", weight: 4, dashArray: "8 5", opacity: 0.85 },
    }).addTo(map);
    naiveLayer.bindTooltip("Naive route (may clip a turn)", { sticky: true });
  }

  // Safe route — green solid (only draw if different)
  if (data.safe_route && data.rerouted) {
    safeLayer = L.geoJSON(data.safe_route, {
      style: { color: "#22c55e", weight: 5, opacity: 0.95 },
    }).addTo(map);
    safeLayer.bindTooltip("Safe rerouted path", { sticky: true });
  } else if (data.safe_route && !data.rerouted) {
    safeLayer = L.geoJSON(data.safe_route, {
      style: { color: "#22c55e", weight: 5, opacity: 0.95 },
    }).addTo(map);
    safeLayer.bindTooltip("Route is clear — no reroute needed", { sticky: true });
  }

  // Blocked intersection markers
  (data.blocked_intersections || []).forEach(b => {
    const color = b.verdict === "fail" ? "#ef4444" : "#f59e0b";
    const circle = L.circleMarker([b.lat, b.lon], {
      radius: 8, color, fillColor: color, fillOpacity: 0.9, weight: 2,
    }).addTo(map);
    circle.bindPopup(
      `<b>${b.verdict.toUpperCase()}</b><br>${b.reason || ""}`
    );
    blockedLayers.push(circle);
  });

  // Fit map to the safe route bounds
  const target = safeLayer || naiveLayer;
  if (target) {
    try { map.fitBounds(target.getBounds().pad(0.15)); } catch (_) {}
  }
}

function renderResults(data) {
  const s = data.stats || {};

  // Stats card
  document.getElementById("stat-naive-dist").textContent =
    s.naive_length_m != null ? `${(s.naive_length_m / 1000).toFixed(2)} km` : "—";
  document.getElementById("stat-safe-dist").textContent =
    s.safe_length_m != null ? `${(s.safe_length_m / 1000).toFixed(2)} km` : "—";
  document.getElementById("stat-isect").textContent = s.intersections_checked ?? "—";
  document.getElementById("stat-blocked").textContent = s.blocked_count ?? "—";
  document.getElementById("stats-card").classList.remove("hidden");

  // Result cards
  const el = document.getElementById("results");
  el.innerHTML = "";

  // Summary card
  const summary = document.createElement("div");
  const status  = data.rerouted ? "fail" : "pass";
  summary.className = `result-card ${status}`;
  summary.innerHTML = `
    <div class="name">${s.vehicle || "Vehicle"}</div>
    <span class="badge ${status}">${data.rerouted ? "Rerouted" : "Route Clear"}</span>
    <div class="reason">${data.rerouted
      ? `Avoided ${s.blocked_count} blocked turn${s.blocked_count !== 1 ? "s" : ""}`
      : "No turn conflicts on this route"}</div>
  `;
  el.appendChild(summary);

  // Per-intersection cards (only show fails/tights)
  (data.blocked_intersections || [])
    .filter(b => b.verdict !== "pass")
    .forEach(b => {
      const card = document.createElement("div");
      card.className = `result-card ${b.verdict}`;
      card.innerHTML = `
        <div class="name">Node ${b.node_id.slice(0, 8)}</div>
        <span class="badge ${b.verdict}">${b.verdict}</span>
        <div class="reason">${b.reason || ""}</div>
        <div class="reason coords">${b.lat.toFixed(5)}, ${b.lon.toFixed(5)}</div>
      `;
      card.addEventListener("click", () => map.setView([b.lat, b.lon], 17));
      el.appendChild(card);
    });
}

// ── Clear ────────────────────────────────────────────────────────────────────
document.getElementById("clear-btn").addEventListener("click", clearAll);

function clearRoutes() {
  if (naiveLayer)  { map.removeLayer(naiveLayer);  naiveLayer = null; }
  if (safeLayer)   { map.removeLayer(safeLayer);   safeLayer  = null; }
  blockedLayers.forEach(l => map.removeLayer(l));
  blockedLayers = [];
}

function clearAll() {
  clearRoutes();
  if (startMarker) { map.removeLayer(startMarker); startMarker = null; }
  if (endMarker)   { map.removeLayer(endMarker);   endMarker   = null; }
  startLatLng = endLatLng = null;
  document.getElementById("results").innerHTML = "";
  document.getElementById("stats-card").classList.add("hidden");
  document.getElementById("pin-summary").classList.add("hidden");
  document.getElementById("pin-hint").textContent = "Click the map to place Start point";
  document.getElementById("pin-hint").classList.remove("hidden");
  document.getElementById("start-coords").textContent = "—";
  document.getElementById("end-coords").textContent   = "—";
  updateRouteButton();
  setMode("start");
}

// ── Loading overlay ───────────────────────────────────────────────────────────
function setLoading(on, text) {
  const el = document.getElementById("loading-overlay");
  if (on) {
    el.querySelector(".loading-text").textContent = text || "Loading…";
    el.classList.remove("hidden");
  } else {
    el.classList.add("hidden");
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────
loadVehicles();
loadNetwork();
setMode("start");
