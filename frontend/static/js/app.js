const map = L.map("map").setView([37.7748, -122.4177], 16);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

const VERDICT_COLOR = { pass: "#22c55e", tight: "#f59e0b", fail: "#ef4444" };
let intersectionLayers = [];
let routeLayers = [];

async function loadVehicles() {
  const res = await fetch("/api/vehicles");
  const vehicles = await res.json();
  const sel = document.getElementById("vehicle-select");
  for (const [id, v] of Object.entries(vehicles)) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = v.name;
    sel.appendChild(opt);
  }
}

async function loadIntersectionMarkers() {
  const res = await fetch("/api/intersections");
  const fc = await res.json();
  fc.features.forEach(f => {
    const coords = f.geometry.coordinates[0];
    const latlngs = coords.map(c => [c[1], c[0]]);
    const poly = L.polygon(latlngs, { color: "#555", weight: 1, fillOpacity: 0.2 }).addTo(map);
    poly.bindTooltip(f.properties.name);
    intersectionLayers.push(poly);
  });
}

async function checkFeasibility() {
  const vehicleId = document.getElementById("vehicle-select").value;
  const res = await fetch("/api/feasibility", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vehicle_id: vehicleId }),
  });
  const results = await res.json();

  // Clear old intersection overlays
  intersectionLayers.forEach(l => map.removeLayer(l));
  intersectionLayers = [];

  const fc = await (await fetch("/api/intersections")).json();
  const byId = Object.fromEntries(fc.features.map(f => [f.properties.id, f]));

  const resultsEl = document.getElementById("results");
  resultsEl.innerHTML = "<strong style='font-size:12px;color:#888'>FEASIBILITY RESULTS</strong>";

  results.forEach(r => {
    const feature = byId[r.intersection_id];
    if (feature) {
      const coords = feature.geometry.coordinates[0];
      const latlngs = coords.map(c => [c[1], c[0]]);
      const color = VERDICT_COLOR[r.verdict] || "#555";
      const poly = L.polygon(latlngs, { color, weight: 2, fillOpacity: 0.35 }).addTo(map);
      poly.bindPopup(`<b>${feature.properties.name}</b><br>${r.verdict.toUpperCase()}${r.reason ? "<br>" + r.reason : ""}`);
      intersectionLayers.push(poly);
    }

    const card = document.createElement("div");
    card.className = `result-card ${r.verdict}`;
    card.innerHTML = `
      <div class="name">${r.intersection_id}</div>
      <span class="badge ${r.verdict}">${r.verdict}</span>
      ${r.reason ? `<div class="reason">${r.reason}</div>` : ""}
      ${r.clearance_margin_ft != null ? `<div class="reason">Margin: ${r.clearance_margin_ft} ft</div>` : ""}
    `;
    resultsEl.appendChild(card);
  });
}

async function findRoute() {
  const vehicleId = document.getElementById("vehicle-select").value;
  const res = await fetch("/api/route", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vehicle_id: vehicleId }),
  });
  const data = await res.json();

  routeLayers.forEach(l => map.removeLayer(l));
  routeLayers = [];

  const fc = await (await fetch("/api/intersections")).json();
  const byId = Object.fromEntries(fc.features.map(f => [f.properties.id, f]));

  const nodeCoords = {
    "start":             [37.7755, -122.4200],
    "end":               [37.7738, -122.4150],
    "intersection-001":  [37.7751, -122.4192],
    "intersection-002":  [37.7748, -122.4177],
    "intersection-003":  [37.7743, -122.4162],
  };

  function pathToLatLngs(path) {
    return path.map(id => nodeCoords[id] || [37.7748, -122.4177]);
  }

  if (data.naive_route) {
    const naive = L.polyline(pathToLatLngs(data.naive_route), { color: "#ef4444", weight: 3, dashArray: "6 4" }).addTo(map);
    naive.bindTooltip("Naive route (may fail)");
    routeLayers.push(naive);
  }
  if (data.safe_route && data.rerouted) {
    const safe = L.polyline(pathToLatLngs(data.safe_route), { color: "#22c55e", weight: 3 }).addTo(map);
    safe.bindTooltip("Safe rerouted path");
    routeLayers.push(safe);
  }

  const resultsEl = document.getElementById("results");
  const card = document.createElement("div");
  card.className = `result-card ${data.rerouted ? "fail" : "pass"}`;
  card.innerHTML = `
    <div class="name">Routing Result</div>
    <span class="badge ${data.rerouted ? "fail" : "pass"}">${data.rerouted ? "Rerouted" : "Clear"}</span>
    <div class="reason">Blocked: ${data.blocked_intersections.join(", ") || "none"}</div>
  `;
  resultsEl.appendChild(card);
}

document.getElementById("check-btn").addEventListener("click", checkFeasibility);
document.getElementById("route-btn").addEventListener("click", findRoute);

loadVehicles();
loadIntersectionMarkers();
