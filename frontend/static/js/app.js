// ClearPath — show all three routing profiles at once.

function _miles(m) { return m != null ? Math.round(m / 1609.344 * 100) / 100 : null; }

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
let signLayers    = [];
let roughLayers   = [];
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
  $("d-height").value    = v.height_ft ?? "";
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

function readHeight() {
  return parseFloat($("d-height").value) || null;
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
      height_ft:  readHeight(),
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
    }).addTo(map).bindPopup(
      `<b>${b.verdict.toUpperCase()}</b><br>${b.reason || ""}` +
      `<div class="popup-photos">Loading site photos…</div>` +
      `<button class="popup-sim">▶ Simulate turn in 3D</button>`,
      { maxWidth: 260 });
    // On first open: wire the simulate button + lazy-load the site photo(s).
    circle.on("popupopen", async ev => {
      const root = ev.popup.getElement();
      const sim = root.querySelector(".popup-sim");
      if (sim) sim.onclick = () => openCornerSim(b);
      const host = root.querySelector(".popup-photos");
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

  // Rough-pavement evidence on the FASTEST route (real Cyvl distress + photo)
  renderRoughMarkers(data);

  // Sign warnings — show for the active profile's route
  renderSignMarkers(data.routes[activeProfile]?.sign_warnings || []);

  // Fit to the active route
  const target = routeLayers[activeProfile];
  if (target) { try { map.fitBounds(target.getBounds().pad(0.15)); } catch (_) {} }
}

// Clickable roughest-pavement points on the fastest route. Each is a real Cyvl
// inspection with surveyed distress + a masked photo — proof the PCI is measured.
function renderRoughMarkers(data) {
  const rough = data.rough_points || [];
  const fpci = data.routes?.fastest?.stats?.avg_pci;
  const spci = data.routes?.smoothest?.stats?.avg_pci;

  rough.forEach(rp => {
    const icon = L.divIcon({
      className: "",
      html: `<div class="rough-pin" title="Rough pavement — PCI ${rp.score}">⚠</div>`,
      iconSize: [30, 30], iconAnchor: [15, 15], popupAnchor: [0, -14],
    });
    const distressRows = (rp.distresses || []).map(d => {
      const amt = d.qty ? `×${d.qty}` : (d.area_sqft != null ? `${d.area_sqft} ft²` : "");
      return `<li><span class="sev ${d.severity}">${d.severity}</span> ${d.type} <span class="amt">${amt}</span></li>`;
    }).join("");
    const cmp = (fpci != null && spci != null)
      ? `<div class="dp-cmp">Fastest avg PCI <b>${fpci}</b> · Smoothest <b>${spci}</b></div>` : "";
    const avoided = rp.avoided_by_smoothest
      ? `<div class="dp-avoid">✓ The Smoothest route avoids this segment</div>` : "";

    const html = `<div class="distress-pop">
        <div class="dp-head"><span class="badge fail">PCI ${rp.score} · ${rp.label}</span> ${rp.address}</div>
        <img class="dp-img" src="${rp.image}" alt="Cyvl pavement distress survey" loading="lazy"
             onclick="window.open('${rp.image}','_blank')" title="Open full survey photo" />
        <div class="dp-cap">Cyvl pavement survey — distress highlighted · inspection ${rp.inspect_id}</div>
        <ul class="dp-list">${distressRows}</ul>
        ${avoided}${cmp}
      </div>`;

    // Bold red highlight on the rough stretch of road (under the pin).
    if (rp.highlight && rp.highlight.length >= 2) {
      const latlngs = rp.highlight.map(([lo, la]) => [la, lo]);
      const halo = L.polyline(latlngs, { color: "#fde68a", weight: 14, opacity: 0.9 }).addTo(map);
      const seg = L.polyline(latlngs, { color: "#dc2626", weight: 8, opacity: 0.95 })
        .addTo(map).bindPopup(html, { maxWidth: 320 });
      roughLayers.push(halo, seg);
    }
    const m = L.marker([rp.lat, rp.lon], { icon })
      .addTo(map)
      .bindPopup(html, { maxWidth: 320 });
    roughLayers.push(m);
  });
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
  // Refresh sign markers for the newly selected route
  if (lastRouteData) {
    clearSignMarkers();
    renderSignMarkers(lastRouteData.routes[profile]?.sign_warnings || []);
  }
}

// ── Sign markers ──────────────────────────────────────────────────────────────
const SIGN_COLORS = { fail: "#ef4444", tight: "#f59e0b", warn: "#f59e0b", info: "#60a5fa" };
const SIGN_ICONS  = { fail: "⛔", tight: "⚠️", warn: "⚠️", info: "ℹ️" };

function signIcon(verdict) {
  const color = SIGN_COLORS[verdict] || "#94a3b8";
  const icon  = SIGN_ICONS[verdict]  || "ℹ️";
  return L.divIcon({
    className: "",
    html: `<div style="background:${color};width:26px;height:26px;border-radius:4px;
      border:2px solid #fff;box-shadow:0 2px 5px rgba(0,0,0,.4);
      display:flex;align-items:center;justify-content:center;font-size:14px">${icon}</div>`,
    iconSize: [26, 26], iconAnchor: [13, 13],
  });
}

function renderSignMarkers(warnings) {
  (warnings || []).forEach(w => {
    const imgLink = w.image_url
      ? `<br><a href="${w.image_url}" target="_blank" style="color:#60a5fa;font-size:11px">View street photo</a>`
      : "";
    const marker = L.marker([w.lat, w.lon], { icon: signIcon(w.verdict) })
      .addTo(map)
      .bindPopup(
        `<b>${w.label}</b> (${w.mutcd})<br>${w.message}${imgLink}`
      );
    signLayers.push(marker);
  });
}

function clearSignMarkers() {
  signLayers.forEach(l => map.removeLayer(l));
  signLayers = [];
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

    const miles   = s.distance_miles != null ? `${s.distance_miles} mi` : "—";
    const mins    = s.time_min       != null ? `${s.time_min} min`      : "";
    const extra   = s.extra_m > 80
      ? `+${(_miles(s.extra_m)).toFixed(2)} mi detour` : null;
    const pci     = s.avg_pci != null ? `PCI ${s.avg_pci}` : null;
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
        <span class="rc-dist" style="color:${meta.color}">${miles}</span>
        ${mins ? `<span class="rc-time">${mins}</span>` : ""}
      </div>
      <div class="rc-pills">${pills}</div>
      <div class="rc-summary">${sumText}</div>
    `;
    card.addEventListener("click", () => selectProfile(profile));
    container.appendChild(card);
  });

  el.appendChild(container);

  // Road distress on the Fastest route — real Cyvl survey photos, shown inline.
  const rough = data.rough_points || [];
  if (rough.length) {
    const h = document.createElement("div");
    h.className = "section-label";
    h.style.marginTop = "10px";
    h.textContent = `Road distress · Fastest route (${rough.length}) — Cyvl survey`;
    el.appendChild(h);

    rough.forEach(rp => {
      const dl = (rp.distresses || []).slice(0, 3)
        .map(d => `${d.type}${d.severity === "high" ? " (high)" : ""}`).join(", ");
      const card = document.createElement("div");
      card.className = "rough-card";
      card.innerHTML = `
        <img class="rough-thumb" src="${rp.image}" alt="Cyvl distress survey" loading="lazy"
             onclick="event.stopPropagation();window.open('${rp.image}','_blank')" title="Open full survey photo">
        <div class="rough-meta">
          <span class="badge fail">PCI ${rp.score} · ${rp.label}</span>
          <div class="rough-addr">${rp.address}</div>
          <div class="rough-dl">${dl}</div>
          ${rp.avoided_by_smoothest ? '<div class="rough-avoid">✓ Smoothest avoids this</div>' : ""}
        </div>`;
      card.addEventListener("click", () => map.setView([rp.lat, rp.lon], 18));
      el.appendChild(card);
    });
  }

  // Sign warnings for the active profile
  const activeWarnings = (data.routes[activeProfile]?.sign_warnings || []);
  if (activeWarnings.length) {
    const wHeader = document.createElement("div");
    wHeader.className = "section-label";
    wHeader.style.marginTop = "8px";
    wHeader.textContent = `Road signs along route (${activeWarnings.length})`;
    el.appendChild(wHeader);

    activeWarnings.forEach(w => {
      const card = document.createElement("div");
      const severity = w.verdict === "fail" ? "fail" : w.verdict === "tight" ? "tight" : "warn";
      card.className = `result-card ${severity === "warn" ? "tight" : severity}`;
      const imgHtml = w.image_url
        ? `<a href="${w.image_url}" target="_blank" class="sign-photo-link">View street photo →</a>`
        : "";
      card.innerHTML = `
        <div class="name">
          <span class="badge ${severity === "warn" ? "tight" : severity}">${w.mutcd}</span>
          ${w.label} · ${w.distance_m}m away
        </div>
        <div class="reason">${w.message}</div>
        ${imgHtml}
      `;
      card.addEventListener("click", () => map.setView([w.lat, w.lon], 17));
      el.appendChild(card);
    });
  }

  // Blocked turns detail (largevehicle only)
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
        <div class="name"><span class="badge ${b.verdict}">${b.verdict}</span> ${b.lat.toFixed(4)}, ${b.lon.toFixed(4)}</div>
        <div class="reason">${b.reason || ""}</div>
        <div class="sim-hint">▶ Click to simulate the turn in 3D</div>
        <div class="reason coords">${b.lat.toFixed(5)}, ${b.lon.toFixed(5)}</div>
        <div class="photos" aria-live="polite"></div>`;
      // Click the card body to recenter + simulate; ignore clicks on a photo.
      card.addEventListener("click", e => {
        if (e.target.closest(".photos")) return;
        map.setView([b.lat, b.lon], 17);
        selectProfile("largevehicle");
        openCornerSim(b);
      });
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
  Object.values(routeLayers).forEach(l => map.removeLayer(l));
  routeLayers = {};
  blockedLayers.forEach(l => map.removeLayer(l));
  blockedLayers = [];
  roughLayers.forEach(l => map.removeLayer(l));
  roughLayers = [];
  clearSignMarkers();
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
// Hardcoded default demo route so the distress highlights + photos show on load.
const DEFAULT_ROUTE = { start: [42.3966, -71.1226], end: [42.3770, -71.0939] }; // Davis Sq -> Union Sq

function placePin(kind, lat, lon) {
  const ll = L.latLng(lat, lon);
  if (kind === "start") {
    startLatLng = ll;
    if (startMarker) map.removeLayer(startMarker);
    startMarker = L.marker(ll, { icon: pinIcon("#2563eb", "A") }).addTo(map).bindPopup("Start (A)");
    $("start-coords").textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  } else {
    endLatLng = ll;
    if (endMarker) map.removeLayer(endMarker);
    endMarker = L.marker(ll, { icon: pinIcon("#059669", "B") }).addTo(map).bindPopup("End (B)");
    $("end-coords").textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  }
  $("pin-summary").classList.remove("hidden");
  $("route-btn").disabled = !(startLatLng && endLatLng);
}

(async function init() {
  $("profile-blurb").textContent = PROFILES[activeProfile].blurb;
  await loadVehicles();
  await loadNetwork();
  placePin("start", ...DEFAULT_ROUTE.start);
  placePin("end", ...DEFAULT_ROUTE.end);
  $("pin-hint").classList.add("hidden");
  document.querySelectorAll(".pin-btn").forEach(b => b.classList.remove("active"));
  clickMode = "start";   // still ready to drop a new A/B
  await findRoute();
})();
