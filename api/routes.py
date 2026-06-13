"""
ClearPath API — A-to-B routing on the real Somerville road network.

The frontend is a Google-Maps-style planner:
  GET  /network         -> full Somerville road graph as GeoJSON, PCI-colored
  GET  /vehicles        -> AASHTO presets (prefill the truck dimension form)
  POST /turning-radius  -> dimensions -> turning geometry (live form helper)
  POST /route/dynamic   -> route between two clicked points under a profile:
                             fastest | smoothest (ambulance) | largevehicle (truck)

The road network + pavement come from the Cyvl Somerville centerline/pavement
shapefiles (see routing/cyvl_graph.py). Plus thin Cyvl pass-throughs for the
optional data overlays.
"""
from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from data.loaders import load_vehicle_templates
from data.scenario import SOMERVILLE_PROJECT_ID
from geometry.turning import turning_geometry

router = APIRouter()

_USE_CYVL = bool(os.getenv("CYVL_API_KEY"))

# The road graph is heavy to build (~1s) — build once, cache for the process.
_cyvl_graph = None
_obstacles: list[tuple[float, float]] = []


_signs: list[dict] = []


def _get_cyvl_graph():
    global _cyvl_graph, _obstacles, _signs
    if _cyvl_graph is None:
        from routing.cyvl_graph import build_from_shapefiles
        _cyvl_graph = build_from_shapefiles()
        _obstacles = _load_obstacles()
        _signs = _load_signs()
    return _cyvl_graph

def _load_obstacles() -> list[tuple[float, float]]:
    """Load tree and utility pole positions from the LiDAR above-ground assets."""
    import json
    from pathlib import Path
    path = Path(__file__).parent.parent / "data" / "CityofSomervilleMAMarketingDemo-aboveGroundAssets.geojson"
    if not path.exists():
        return []
    with open(path) as f:
        fc = json.load(f)
    pts = []
    for feat in fc["features"]:
        at = feat["properties"].get("asset_type", "")
        if at not in ("TREE", "UTILITY_POLE"):
            continue
        geom = feat["geometry"]
        if geom["type"] == "Point":
            lon, lat = geom["coordinates"][:2]
            pts.append((lon, lat))
    return pts

def _load_signs() -> list[dict]:
    """Load large-vehicle relevant signs from the Cyvl signs shapefile."""
    import json
    from pathlib import Path
    import shapefile  # pyshp

    shp = Path(__file__).parent.parent / "data" / "CityofSomervilleMAMarketingDemo-signs" / "tmpvw1yibth.shp"
    if not shp.exists():
        return []

    # MUTCD code → (label, severity, assumed_clearance_ft or None)
    SIGN_META = {
        "W13-1P": ("Low Clearance",  "clearance", 13.5),
        "R12-1":  ("Weight Limit",   "warning",   None),
        "R12-2":  ("Weight Limit",   "warning",   None),
        "R3-4":   ("No Trucks",      "block",     None),
        "R3-5L":  ("No Trucks Left", "block",     None),
        "W1-1R":  ("Sharp Curve Right", "info",   None),
        "W1-1L":  ("Sharp Curve Left",  "info",   None),
        "W1-8L":  ("Curve Left",     "info",      None),
        "W1-8R":  ("Curve Right",    "info",      None),
        "W1-4R":  ("Winding Road",   "info",      None),
    }

    signs = []
    with shapefile.Reader(str(shp)) as sf:
        for rec in sf.iterRecords():
            mutcd = rec["mutcd"]
            if mutcd not in SIGN_META:
                continue
            loc_raw = rec["location_"]
            if not loc_raw:
                continue
            try:
                loc = json.loads(loc_raw)
            except Exception:
                continue
            lat, lon = loc.get("lat"), loc.get("lon")
            if lat is None or lon is None:
                continue
            label, severity, clearance_ft = SIGN_META[mutcd]
            signs.append({
                "mutcd":        mutcd,
                "label":        label,
                "severity":     severity,
                "clearance_ft": clearance_ft,
                "lat":          lat,
                "lon":          lon,
                "image_url":    rec["image_url"] or None,
                "condition":    rec["condition"] or None,
            })
    return signs


def _find_route_signs(
    g,
    path: list[str],
    signs: list[dict],
    vehicle_height_ft: float | None,
    proximity_m: float = 40.0,
) -> list[dict]:
    """
    Return signs within proximity_m of the route path, enriched with a
    vehicle-specific severity assessment.
    """
    if not signs or not path:
        return []

    from routing.cyvl_graph import _haversine_m

    # Collect all coordinate points along the route
    route_pts: list[tuple[float, float]] = []
    for a, b in zip(path, path[1:]):
        edge = g.edge_meta.get((a, b)) or g.edge_meta.get((b, a))
        if edge:
            route_pts.extend(edge.points)

    if not route_pts:
        return []

    warnings = []
    for sign in signs:
        dist = min(
            _haversine_m(sign["lon"], sign["lat"], pt[0], pt[1])
            for pt in route_pts
        )
        if dist > proximity_m:
            continue

        entry = dict(sign, distance_m=round(dist))

        # Assess against vehicle height for clearance signs
        if sign["severity"] == "clearance" and sign["clearance_ft"] and vehicle_height_ft:
            margin = sign["clearance_ft"] - vehicle_height_ft
            if margin < 0:
                entry["verdict"] = "fail"
                entry["message"] = (
                    f"Low clearance ({sign['clearance_ft']}ft assumed) — "
                    f"vehicle is {vehicle_height_ft}ft tall, {abs(margin):.1f}ft too tall to pass safely."
                )
            elif margin < 1.0:
                entry["verdict"] = "tight"
                entry["message"] = (
                    f"Low clearance ({sign['clearance_ft']}ft assumed) — "
                    f"only {margin:.1f}ft of headroom for a {vehicle_height_ft}ft vehicle. Proceed with caution."
                )
            else:
                entry["verdict"] = "warn"
                entry["message"] = (
                    f"Low clearance sign ahead. Assumed {sign['clearance_ft']}ft clearance, "
                    f"vehicle is {vehicle_height_ft}ft — {margin:.1f}ft margin. Verify before proceeding."
                )
        elif sign["severity"] == "block":
            entry["verdict"] = "fail"
            entry["message"] = f"{sign['label']} — this road legally restricts truck access."
        elif sign["severity"] == "warning":
            entry["verdict"] = "warn"
            entry["message"] = f"{sign['label']} — verify your vehicle meets the posted restriction."
        else:
            entry["verdict"] = "info"
            entry["message"] = f"{sign['label']} ahead — reduce speed and proceed carefully."

        warnings.append(entry)

    return sorted(warnings, key=lambda s: s["distance_m"])


# ── Models ──────────────────────────────────────────────────────────────────

class VehicleSpec(BaseModel):
    id: str | None = None
    length_ft: float | None = None
    width_ft: float | None = None
    wheelbase_ft: float | None = None
    steer_max_deg: float | None = None
    turning_radius_ft: float | None = None
    overhang_front_ft: float | None = None
    track_width_ft: float | None = None


class DynamicRouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    profile: str = "largevehicle"          # fastest | smoothest | largevehicle
    vehicle_id: str | None = None          # AASHTO preset
    vehicle: dict | None = None            # editable dimension overrides (truck)
    pci_penalty: float | None = None       # override the profile's default PCI weight


# Per-profile routing behavior: how hard to avoid rough pavement, and whether
# infeasible turns are hard blocks.
PROFILE_DEFAULTS = {
    "fastest":      {"pci_penalty": 0.0, "block_turns": False},
    "smoothest":    {"pci_penalty": 4.0, "block_turns": False},
    "largevehicle": {"pci_penalty": 1.0, "block_turns": True},
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_vehicle(vehicle_id: str | None, overrides: dict | None) -> dict:
    """Merge an AASHTO preset (by id) with any user-supplied dimension overrides."""
    base: dict = {}
    if vehicle_id:
        templates = load_vehicle_templates()
        if vehicle_id not in templates:
            raise HTTPException(404, f"Unknown vehicle: {vehicle_id}")
        base = dict(templates[vehicle_id])
        base["id"] = vehicle_id
    for k, v in (overrides or {}).items():
        if v is not None:
            base[k] = v
    base.setdefault("id", "custom")
    base.setdefault("name", base.get("id", "Custom vehicle"))
    base.setdefault("width_ft", 8.5)
    # If wheelbase was edited but the radius wasn't supplied, re-derive it.
    if (overrides or {}).get("turning_radius_ft") is None and (overrides or {}).get("wheelbase_ft"):
        base.pop("turning_radius_ft", None)
    return base


def _route_avg_pci(g, path: list[str] | None) -> float | None:
    """Length-weighted average PCI over the scored edges of a path."""
    if not path:
        return None
    num = den = 0.0
    for a, b in zip(path, path[1:]):
        e = g.edge_meta.get((a, b)) or g.edge_meta.get((b, a))
        if e and e.pci_score is not None:
            num += e.pci_score * e.length_m
            den += e.length_m
    return round(num / den, 1) if den else None


def _path_length_m(g, path: list[str] | None) -> int | None:
    if not path:
        return None
    total = 0.0
    for a, b in zip(path, path[1:]):
        e = g.edge_meta.get((a, b)) or g.edge_meta.get((b, a))
        if e:
            total += e.length_m
    return round(total)


# Average speeds (mph) per profile — reflects realistic urban Somerville pace
_PROFILE_SPEED_MPH = {
    "fastest":      22,   # normal city driving
    "smoothest":    18,   # cautious / ambulance
    "largevehicle": 14,   # slow for large trucks navigating tight streets
}


def _trip_time_min(length_m: int | None, profile: str) -> float | None:
    if length_m is None:
        return None
    mph = _PROFILE_SPEED_MPH.get(profile, 20)
    miles = length_m / 1609.344
    return round(miles / mph * 60, 1)


def _miles(length_m: int | None) -> float | None:
    if length_m is None:
        return None
    return round(length_m / 1609.344, 2)


# ── Reference ─────────────────────────────────────────────────────────────────

@router.get("/status")
def status():
    return {"cyvl_connected": _USE_CYVL, "autodesk_connected": bool(os.getenv("APS_CLIENT_ID"))}


@router.get("/vehicles")
def get_vehicles():
    return load_vehicle_templates()


# ── Turning radius (live form helper) ─────────────────────────────────────────

@router.post("/turning-radius")
def post_turning_radius(spec: VehicleSpec):
    vehicle = _resolve_vehicle(spec.id, spec.model_dump())
    g = turning_geometry(vehicle)
    return {
        "turning_radius_ft": g.turning_radius_ft,
        "inner_radius_ft": g.inner_radius_ft,
        "outer_radius_ft": g.outer_radius_ft,
        "swept_width_ft": g.swept_width_ft,
    }


# ── Road network ──────────────────────────────────────────────────────────────

@router.get("/network")
def get_network():
    """Full Somerville road network as GeoJSON, edges colored by PCI score."""
    from routing.cyvl_graph import graph_to_geojson
    return graph_to_geojson(_get_cyvl_graph())


# ── Dynamic A-to-B routing ────────────────────────────────────────────────────

@router.post("/route/dynamic")
def dynamic_route(req: DynamicRouteRequest):
    """
    Route between two clicked points on the real Somerville network under a
    profile:
      fastest      — shortest distance.
      smoothest    — distance penalized by rough pavement (ambulance).
      largevehicle — hard-blocks intersections whose swept path overruns the
                     curb for the given vehicle, and prefers good pavement.

    Returns the naive (distance-only) route and the chosen profile route as
    GeoJSON, plus per-intersection feasibility and trip stats.
    """
    if req.profile not in PROFILE_DEFAULTS:
        raise HTTPException(400, f"Unknown profile: {req.profile}")
    from routing.cyvl_graph import path_to_geojson

    defaults = PROFILE_DEFAULTS[req.profile]
    pci_penalty = req.pci_penalty if req.pci_penalty is not None else defaults["pci_penalty"]

    vehicle = _resolve_vehicle(req.vehicle_id, req.vehicle)
    tg = turning_geometry(vehicle)
    vehicle["turning_radius_ft"] = tg.turning_radius_ft  # keep consistent w/ derivation

    g = _get_cyvl_graph()
    start_node = g.nearest_node(req.start_lon, req.start_lat, min_degree=2)
    end_node = g.nearest_node(req.end_lon, req.end_lat, min_degree=2)
    if not start_node or not end_node:
        raise HTTPException(400, "Could not snap start/end to road network")

    # 1. Naive baseline: shortest distance, ignores feasibility + comfort.
    naive_path = g.dijkstra(start_node, end_node)
    if not naive_path:
        raise HTTPException(400, "No path found between selected points")

    # 2. Feasibility along the naive route (only the truck profile blocks turns).
    feasibility = g.check_route_feasibility(naive_path, vehicle)
    blocked_ids = ({r["node_id"] for r in feasibility if r["verdict"] == "fail"}
                   if defaults["block_turns"] else set())

    # 3. Chosen route under the profile.
    chosen_path = g.dijkstra(
        start_node, end_node, blocked=blocked_ids, pci_penalty_factor=pci_penalty
    ) or naive_path

    naive_geojson = path_to_geojson(g, naive_path)
    naive_geojson["properties"].update({"route_type": "naive", "color": "#ef4444"})
    chosen_color = {"fastest": "#3b82f6", "smoothest": "#0ea5e9", "largevehicle": "#22c55e"}[req.profile]
    chosen_geojson = path_to_geojson(g, chosen_path)
    chosen_geojson["properties"].update({"route_type": req.profile, "color": chosen_color})

    sn, en = g.nodes[start_node], g.nodes[end_node]
    return {
        "profile": req.profile,
        "naive_route": naive_geojson,
        "safe_route": chosen_geojson,            # kept name for frontend compatibility
        "rerouted": naive_path != chosen_path,
        "blocked_intersections": feasibility,
        "start": {"node_id": start_node, "lat": sn.lat, "lon": sn.lon},
        "end": {"node_id": end_node, "lat": en.lat, "lon": en.lon},
        "vehicle": {"id": vehicle["id"], "name": vehicle.get("name"),
                    "turning_radius_ft": tg.turning_radius_ft, "outer_radius_ft": tg.outer_radius_ft,
                    "swept_width_ft": tg.swept_width_ft},
        "stats": {
            "naive_length_m":    _path_length_m(g, naive_path),
            "safe_length_m":     _path_length_m(g, chosen_path),
            "distance_miles":    _miles(_path_length_m(g, chosen_path)),
            "time_min":          _trip_time_min(_path_length_m(g, chosen_path), req.profile),
            "naive_avg_pci":     _route_avg_pci(g, naive_path),
            "safe_avg_pci":      _route_avg_pci(g, chosen_path),
            "intersections_checked": len(feasibility),
            "blocked_count":     len(blocked_ids),
            "vehicle":           vehicle.get("name"),
        },
    }


# ── All-profiles route (single call returns fastest + smoothest + largevehicle) ──

class AllRoutesRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    vehicle_id: str | None = None
    vehicle: dict | None = None
    height_ft: float | None = None  # override vehicle height for clearance checks


ROUTE_COLORS = {
    "fastest":      "#f59e0b",   # amber
    "smoothest":    "#38bdf8",   # sky blue
    "largevehicle": "#4ade80",   # green
}


@router.post("/route/all")
def all_routes(req: AllRoutesRequest):
    """
    Compute fastest, smoothest, and large-vehicle routes in one request.
    Feasibility is evaluated once (on the naive path) and shared across profiles.
    """
    from routing.cyvl_graph import path_to_geojson

    vehicle = _resolve_vehicle(req.vehicle_id, req.vehicle)
    tg = turning_geometry(vehicle)
    vehicle["turning_radius_ft"] = tg.turning_radius_ft

    g = _get_cyvl_graph()
    start_node = g.nearest_node(req.start_lon, req.start_lat, min_degree=2)
    end_node   = g.nearest_node(req.end_lon,   req.end_lat,   min_degree=2)
    if not start_node or not end_node:
        raise HTTPException(400, "Could not snap start/end to road network")

    naive_path = g.dijkstra(start_node, end_node)
    if not naive_path:
        raise HTTPException(400, "No path found between selected points")

    # Run feasibility once on the naive path (used by largevehicle profile)
    feasibility = g.check_route_feasibility(naive_path, vehicle, obstacles=_obstacles)
    blocked_ids = {r["node_id"] for r in feasibility if r["verdict"] == "fail"}

    routes = {}
    for profile, defaults in PROFILE_DEFAULTS.items():
        pci   = defaults["pci_penalty"]
        blocks = blocked_ids if defaults["block_turns"] else set()
        path  = g.dijkstra(start_node, end_node, blocked=blocks,
                           pci_penalty_factor=pci) or naive_path
        gj = path_to_geojson(g, path)
        gj["properties"].update({"route_type": profile, "color": ROUTE_COLORS[profile]})

        length_m  = _path_length_m(g, path)
        naive_len = _path_length_m(g, naive_path)
        avg_pci   = _route_avg_pci(g, path)
        naive_pci = _route_avg_pci(g, naive_path)

        routes[profile] = {
            "geojson": gj,
            "stats": {
                "length_m":              length_m,
                "distance_miles":        _miles(length_m),
                "time_min":              _trip_time_min(length_m, profile),
                "extra_m":               (length_m or 0) - (naive_len or 0),
                "avg_pci":               avg_pci,
                "naive_avg_pci":         naive_pci,
                "blocked_count":         len(blocks),
                "intersections_checked": len(feasibility),
            },
        }

    # Resolve vehicle height: explicit override > template value > None
    height_ft = req.height_ft or vehicle.get("height_ft")

    # Find signs along each route path
    for profile, r in routes.items():
        path = g.dijkstra(
            start_node, end_node,
            blocked=(blocked_ids if PROFILE_DEFAULTS[profile]["block_turns"] else set()),
            pci_penalty_factor=PROFILE_DEFAULTS[profile]["pci_penalty"],
        ) or naive_path
        r["sign_warnings"] = _find_route_signs(g, path, _signs, height_ft)

    sn, en = g.nodes[start_node], g.nodes[end_node]
    return {
        "routes":      routes,
        "feasibility": feasibility,
        "start": {"node_id": start_node, "lat": sn.lat, "lon": sn.lon},
        "end":   {"node_id": end_node,   "lat": en.lat, "lon": en.lon},
        "vehicle": {
            "id":                vehicle["id"],
            "name":              vehicle.get("name"),
            "height_ft":         height_ft,
            "turning_radius_ft": tg.turning_radius_ft,
            "outer_radius_ft":   tg.outer_radius_ft,
            "swept_width_ft":    tg.swept_width_ft,
        },
    }


# ── Cyvl pass-throughs (optional overlays) ────────────────────────────────────

def _require_cyvl():
    if not _USE_CYVL:
        raise HTTPException(503, "CYVL_API_KEY not configured")


@router.get("/cyvl/assets")
def cyvl_assets(lon: float = Query(...), lat: float = Query(...),
                radius_m: float = Query(40), asset_type: str | None = Query(None)):
    _require_cyvl()
    from data.cyvl_client import get_assets, radius_filter
    return get_assets(SOMERVILLE_PROJECT_ID, radius_filter(lat, lon, radius_m), asset_type=asset_type)


@router.get("/cyvl/pavement")
def cyvl_pavement(lon: float = Query(...), lat: float = Query(...), radius_m: float = Query(60)):
    _require_cyvl()
    from data.cyvl_client import get_pavement_scores, radius_filter
    return get_pavement_scores(SOMERVILLE_PROJECT_ID, radius_filter(lat, lon, radius_m))
