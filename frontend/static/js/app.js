// ── Map ───────────────────────────────────────────────────────────────────
const map = L.map("map", { zoomControl: true }).setView([42.3818, -71.1035], 15);
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  attribution: "© OpenStreetMap, © CARTO", maxZoom: 20,
}).addTo(map);

const PROFILE_COLOR = { fastest: "#2563eb", smoothest: "#0ea5e9", largevehicle: "#7c3aed" };
const PCI_COLOR = pci =>
  pci >= 80 ? "#22c55e" : pci >= 65 ? "#84cc16" : pci >= 55 ? "#eab308" : pci >= 40 ? "#f97316" : "#ef4444";

let SCENARIO = null;
let profile = "fastest";
let baseLayers = [];     // network + markers
let routeLayers = [];    // route polylines + swept overlays

const $ = id => document.getElementById(id);
const clear = arr => { arr.forEach(l => map.removeLayer(l)); arr.length = 0; };
const LL = lonlat => [lonlat[1], lonlat[0]];   // [lon,lat] -> [lat,lon]

// ── Boot ──────────────────────────────────────────────────────────────────
async function boot() {
  SCENARIO = await (await fetch("/api/scenario")).json();
  $("source-badge").textContent = SCENARIO.source === "cyvl" ? "live cyvl" : "demo data";
  $("source-badge").className = SCENARIO.source === "cyvl" ? "badge-live" : "badge-baked";

  // destinations
  const ds = $("dest-select");
  SCENARIO.destinations.forEach(d => ds.add(new Option(d.name, d.id)));
  ds.value = "market_basket";
  $("start-name").textContent = SCENARIO.nodes[SCENARIO.origin].name;

  // vehicle presets
  const vehicles = await (await fetch("/api/vehicles")).json();
  window.VEHICLES = vehicles;
  const ps = $("preset-select");
  Object.entries(vehicles).forEach(([id, v]) => ps.add(new Option(v.name, id)));
  ps.value = "WB-67";
  fillDims(vehicles["WB-67"]);

  drawNetwork();
}

// ── Base network ───────────────────────────────────────────────────────────
function drawNetwork() {
  clear(baseLayers);
  const colorByPci = profile === "smoothest";

  SCENARIO.edges.forEach(e => {
    const a = SCENARIO.nodes[e.a], b = SCENARIO.nodes[e.b];
    const line = L.polyline([[a.lat, a.lon], [b.lat, b.lon]], {
      color: colorByPci ? PCI_COLOR(e.pci) : "#cbd2da",
      weight: colorByPci ? 5 : 3, opacity: .85,
    }).addTo(map);
    if (colorByPci) line.bindTooltip(`PCI ${e.pci}`);
    baseLayers.push(line);
  });

  Object.entries(SCENARIO.nodes).forEach(([id, n]) => {
    let layer;
    if (n.kind === "origin") {
      layer = L.marker([n.lat, n.lon]).bindTooltip(n.name, { permanent: false });
    } else if (n.kind === "dest") {
      layer = L.circleMarker([n.lat, n.lon], { radius: 6, color: "#475569", fillColor: "#fff", fillOpacity: 1, weight: 2 })
        .bindTooltip(n.name);
    } else {
      layer = L.circleMarker([n.lat, n.lon], { radius: 4, color: "#94a3b8", fillColor: "#fff", fillOpacity: 1, weight: 1.5 })
        .bindTooltip(`${n.name} · corner ${n.corner_radius_ft}ft · PCI ${n.pci}`);
    }
    layer.addTo(map);
    baseLayers.push(layer);
  });
}

// ── Truck dimension form ─────────────────────────────────────────────────────
function fillDims(v) {
  $("d-length").value = v.length_ft ?? "";
  $("d-width").value = v.width_ft ?? "";
  $("d-wheelbase").value = v.wheelbase_ft ?? "";
  $("d-radius").value = v.turning_radius_ft ?? "";
  refreshSwept();
}

function readDims() {
  return {
    id: $("preset-select").value,
    length_ft: parseFloat($("d-length").value) || null,
    width_ft: parseFloat($("d-width").value) || null,
    wheelbase_ft: parseFloat($("d-wheelbase").value) || null,
    turning_radius_ft: parseFloat($("d-radius").value) || null,
  };
}

// Re-derive turning radius from wheelbase (unless the user typed a radius)
async function deriveRadius() {
  const dims = readDims();
  dims.turning_radius_ft = null;            // force re-derivation from wheelbase
  const g = await (await fetch("/api/turning-radius", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(dims),
  })).json();
  $("d-radius").value = g.turning_radius_ft;
  refreshSwept();
}

async function refreshSwept() {
  const dims = readDims();
  const g = await (await fetch("/api/turning-radius", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(dims),
  })).json();
  $("swept-hint").innerHTML =
    `Outer swing radius <b>${g.outer_radius_ft} ft</b> · swept band <b>${g.swept_width_ft} ft</b> wide.<br>` +
    `Checked against each corner's LiDAR-measured curb geometry.`;
}

// ── Routing ──────────────────────────────────────────────────────────────────
async function getDirections() {
  clear(routeLayers);
  const body = {
    profile,
    start: SCENARIO.origin,
    end: $("dest-select").value,
    vehicle: profile === "largevehicle" ? readDims() : null,
  };
  const r = await (await fetch("/api/route", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })).json();
  if (r.detail) { $("results").innerHTML = `<div class="reroute-banner">${r.detail}</div>`; return; }
  drawRoute(r);
  renderResults(r);
}

function pathLatLngs(path) {
  return path.map(id => [SCENARIO.nodes[id].lat, SCENARIO.nodes[id].lon]);
}

function drawRoute(r) {
  // naive "before" route, shown dashed when it differs from the chosen route
  if (r.rerouted && r.naive_route) {
    routeLayers.push(L.polyline(pathLatLngs(r.naive_route),
      { color: "#ef4444", weight: 4, opacity: .7, dashArray: "7 7" })
      .bindTooltip("Naive route — sends the vehicle through a turn it can't make").addTo(map));
  }
  // chosen route
  if (r.route) {
    routeLayers.push(L.polyline(pathLatLngs(r.route),
      { color: PROFILE_COLOR[r.profile], weight: 6, opacity: .95 })
      .bindTooltip(r.rerouted ? "ClearPath route" : "Route").addTo(map));
    map.fitBounds(L.polyline(pathLatLngs(r.route)).getBounds(), { padding: [70, 70] });
  }

  // swept-path money shot at each failing / tight intersection
  if (r.feasibility) {
    Object.entries(r.feasibility).forEach(([id, f]) => {
      if (f.verdict === "pass" || !f.swept_polygon) return;
      const color = f.verdict === "fail" ? "#ef4444" : "#f59e0b";
      routeLayers.push(L.polygon(f.swept_polygon.map(LL),
        { color, weight: 2, fillColor: color, fillOpacity: .35 })
        .bindPopup(`<b>${SCENARIO.nodes[id].name}</b><br>${f.verdict.toUpperCase()}: ${f.reason}`).addTo(map));
      const n = SCENARIO.nodes[id];
      routeLayers.push(L.circleMarker([n.lat, n.lon], { radius: 7, color, fillColor: color, fillOpacity: .9 }).addTo(map));
      (n.obstacles || []).forEach(o => {
        if (o.lon && o.lat)
          routeLayers.push(L.circleMarker([o.lat, o.lon], { radius: 4, color: "#111", fillColor: "#fbbf24", fillOpacity: 1, weight: 1 })
            .bindTooltip(o.type).addTo(map));
      });
    });
  }
}

// ── Results panel ─────────────────────────────────────────────────────────────
function renderResults(r) {
  const m = r.metrics || {};
  let html = "";

  if (r.profile === "largevehicle" && r.blocked_on_naive_route.length) {
    const bn = r.blocked_on_naive_route.map(id => SCENARIO.nodes[id].name).join(", ");
    html += `<div class="reroute-banner"><span>⚠️</span><div><b>Rerouted.</b> The fastest path turns through
      <b>${bn}</b>, but the ${r.vehicle ? r.vehicle.name || r.vehicle.id : "vehicle"}'s swept path overruns the curb there.
      ClearPath routed around it.</div></div>`;
  } else if (r.profile === "smoothest" && r.rerouted) {
    html += `<div class="reroute-banner ok"><span>🚑</span><div><b>Smoother route found.</b>
      Avoided rough pavement for a higher average PCI ride.</div></div>`;
  } else if (!r.rerouted) {
    html += `<div class="reroute-banner ok"><span>✓</span><div>Direct route is clear for this profile.</div></div>`;
  }

  html += `<div class="metrics">
      <div class="metric"><div class="val">${m.distance_m ? (m.distance_m/1000).toFixed(2) : "—"}</div><div class="lab">km</div></div>
      <div class="metric"><div class="val">${m.eta_min ?? "—"}</div><div class="lab">min</div></div>
      <div class="metric"><div class="val">${m.avg_pci ?? "—"}</div><div class="lab">avg PCI</div></div>
    </div>`;

  // per-turn feasibility cards (truck profile)
  if (r.feasibility) {
    Object.entries(r.feasibility)
      .sort((a, b) => ({fail:0,tight:1,pass:2}[a[1].verdict] - {fail:0,tight:1,pass:2}[b[1].verdict]))
      .forEach(([id, f]) => {
        if (f.verdict === "pass") return;
        html += `<div class="verdict-card ${f.verdict}">
            <div class="title"><span class="pill ${f.verdict}">${f.verdict}</span> ${SCENARIO.nodes[id].name}</div>
            <div>${f.reason || ""}</div></div>`;
      });
  }

  if (r.profile === "smoothest") {
    html += `<div class="legend">Pavement: <span><i style="background:#22c55e"></i>good</span>
      <span><i style="background:#eab308"></i>fair</span><span><i style="background:#ef4444"></i>poor</span></div>`;
  }

  $("results").innerHTML = html;
}

// ── Events ────────────────────────────────────────────────────────────────────
document.querySelectorAll(".profile").forEach(btn => btn.addEventListener("click", () => {
  document.querySelectorAll(".profile").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  profile = btn.dataset.profile;
  $("truck-form").classList.toggle("hidden", profile !== "largevehicle");
  clear(routeLayers);
  $("results").innerHTML = "";
  drawNetwork();
}));

$("preset-select").addEventListener("change", e => fillDims(window.VEHICLES[e.target.value]));
$("d-wheelbase").addEventListener("change", deriveRadius);
["d-length", "d-width", "d-radius"].forEach(id => $(id).addEventListener("change", refreshSwept));
$("go-btn").addEventListener("click", getDirections);

boot();
