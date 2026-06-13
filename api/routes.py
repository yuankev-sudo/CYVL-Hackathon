"""
ClearPath API.

The frontend is a Google-Maps-style planner:
  GET  /scenario        -> the corridor (nodes, edges, origin, destinations)
  GET  /vehicles        -> AASHTO presets (prefill the truck dimension form)
  POST /turning-radius  -> dimensions -> turning geometry (live form helper)
  POST /route           -> plan a route under a profile (+ vehicle for trucks)

Plus thin Cyvl pass-throughs for the optional data overlays.
"""
from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from data.loaders import load_vehicle_templates
from data.scenario import build_scenario, Scenario
from geometry.turning import turning_geometry
from routing.graph import plan_route

router = APIRouter()

_USE_CYVL = bool(os.getenv("CYVL_API_KEY"))

# ---------------------------------------------------------------------------
# Cyvl graph + LiDAR obstacles — loaded once, cached for process lifetime
# ---------------------------------------------------------------------------
_cyvl_graph = None
_obstacles: list[tuple[float, float]] = []

def _get_cyvl_graph():
    global _cyvl_graph, _obstacles
    if _cyvl_graph is None:
        from routing.cyvl_graph import build_from_shapefiles
        _cyvl_graph = build_from_shapefiles()
        _obstacles = _load_obstacles()
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

class FeasibilityRequest(BaseModel):
    vehicle_id: str
    intersection_ids: list[str] | None = None


class RouteRequest(BaseModel):
    profile: str = "fastest"            # fastest | smoothest | largevehicle
    start: str
    end: str
    vehicle: dict | None = None         # dims (+ optional preset id) for largevehicle
    live: bool = False                  # pull live Cyvl PCI/obstacles into the graph


class VehicleSpec(BaseModel):
    id: str | None = None
    length_ft: float | None = None
    width_ft: float | None = None
    wheelbase_ft: float | None = None
    steer_max_deg: float | None = None
    turning_radius_ft: float | None = None
    overhang_front_ft: float | None = None
    track_width_ft: float | None = None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_vehicle(spec: dict | None) -> dict:
    """Merge a preset (by id) with any user-supplied overrides."""
    spec = dict(spec or {})
    base: dict = {}
    if spec.get("id"):
        templates = load_vehicle_templates()
        base = dict(templates.get(spec["id"], {}))
        base["id"] = spec["id"]
    # user-supplied non-null fields win
    for k, v in spec.items():
        if v is not None:
            base[k] = v
    base.setdefault("id", "custom")
    base.setdefault("width_ft", 8.5)
    return base


def _scenario_payload(sc: Scenario) -> dict:
    return {
        "origin": sc.origin,
        "destinations": [
            {"id": d, "name": sc.nodes[d]["name"], "lon": sc.nodes[d]["lon"], "lat": sc.nodes[d]["lat"]}
            for d in sc.destinations
        ],
        "source": sc.source,
        "nodes": {
            nid: {
                "name": n["name"], "kind": n["kind"], "lon": n["lon"], "lat": n["lat"],
                "corner_radius_ft": n.get("corner_radius_ft"),
                "road_width_ft": n.get("road_width_ft"),
                "pci": n.get("pci"),
                "pci_source": n.get("pci_source", "baked"),
                "obstacles": n.get("obstacles", []),
            } for nid, n in sc.nodes.items()
        },
        "edges": [{"a": e.a, "b": e.b, "length_m": e.length_m, "pci": e.pci} for e in sc.edges],
    }


# ── Reference ─────────────────────────────────────────────────────────────────

@router.get("/status")
def status():
    return {"cyvl_connected": _USE_CYVL, "autodesk_connected": bool(os.getenv("APS_CLIENT_ID"))}


@router.get("/vehicles")
def get_vehicles():
    return load_vehicle_templates()


@router.get("/scenario")
def get_scenario(live: bool = Query(False, description="Enrich PCI/obstacles from Cyvl")):
    sc = build_scenario(live=live and _USE_CYVL)
    return _scenario_payload(sc)


# ── Turning radius (live form helper) ─────────────────────────────────────────

@router.post("/turning-radius")
def post_turning_radius(spec: VehicleSpec):
    vehicle = _resolve_vehicle(spec.model_dump())
    # If the user edited wheelbase/steer but not the radius, re-derive it.
    if spec.turning_radius_ft is None and (spec.wheelbase_ft or spec.steer_max_deg):
        vehicle.pop("turning_radius_ft", None)
    g = turning_geometry(vehicle)
    return {
        "turning_radius_ft": g.turning_radius_ft,
        "inner_radius_ft": g.inner_radius_ft,
        "outer_radius_ft": g.outer_radius_ft,
        "swept_width_ft": g.swept_width_ft,
    }


# ── Routing ───────────────────────────────────────────────────────────────────

@router.post("/route")
def post_route(req: RouteRequest):
    if req.profile not in ("fastest", "smoothest", "largevehicle"):
        raise HTTPException(400, f"Unknown profile: {req.profile}")

    sc = build_scenario(live=req.live and _USE_CYVL)
    if req.start not in sc.nodes or req.end not in sc.nodes:
        raise HTTPException(404, "Unknown start/end node")

    vehicle = None
    if req.profile == "largevehicle":
        vehicle = _resolve_vehicle(req.vehicle)
        if req.vehicle and req.vehicle.get("turning_radius_ft") is None and req.vehicle.get("wheelbase_ft"):
            vehicle.pop("turning_radius_ft", None)
        vehicle["turning_geometry"] = turning_geometry(vehicle).__dict__

    try:
        result = plan_route(sc, req.start, req.end, req.profile, vehicle)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # attach coordinates so the frontend can draw paths without a second fetch
    result["node_coords"] = {nid: [n["lon"], n["lat"]] for nid, n in sc.nodes.items()}
    if vehicle:
        result["vehicle"] = {k: v for k, v in vehicle.items() if k != "turning_geometry"}
        result["turning_geometry"] = vehicle["turning_geometry"]
    return result


# ── Cyvl pass-throughs (optional overlays) ────────────────────────────────────

def _require_cyvl():
    if not _USE_CYVL:
        raise HTTPException(503, "CYVL_API_KEY not configured")


@router.get("/cyvl/assets")
def cyvl_assets(lon: float = Query(...), lat: float = Query(...),
                radius_m: float = Query(40), asset_type: str | None = Query(None)):
    _require_cyvl()
    from data.cyvl_client import get_assets, radius_filter
    from data.scenario import SOMERVILLE_PROJECT_ID
    return get_assets(SOMERVILLE_PROJECT_ID, radius_filter(lat, lon, radius_m), asset_type=asset_type)


@router.get("/cyvl/pavement")
def cyvl_pavement(lon: float = Query(...), lat: float = Query(...), radius_m: float = Query(60)):
    _require_cyvl()
    from data.cyvl_client import get_markings, intersection_bbox
    return get_markings(_project_id(), intersection_bbox(lon, lat, radius_deg))


@router.post("/cyvl/image-search")
def cyvl_image_search(body: dict):
    _require_cyvl()
    from data.cyvl_client import search_images
    pid = os.getenv("CYVL_PROJECT_ID")
    return search_images(body.get("query", ""), project_id=pid, page_size=body.get("page_size", 20))


# ---------------------------------------------------------------------------
# Cyvl-native network + dynamic routing
# ---------------------------------------------------------------------------

@router.get("/network")
def get_network():
    """Full Somerville road network as GeoJSON, edges colored by PCI score."""
    from routing.cyvl_graph import graph_to_geojson
    g = _get_cyvl_graph()
    return graph_to_geojson(g)


class DynamicRouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    vehicle_id: str
    pci_penalty: float = 2.0


@router.post("/route/dynamic")
def dynamic_route(req: DynamicRouteRequest):
    """
    A-to-B routing on the real Somerville road network.
    Checks turn feasibility at every intersection along the naive route using
    the vehicle's turning radius vs. actual road geometry from the centerline.
    Returns naive route, safe reroute, and blocked intersection details as GeoJSON.
    """
    from routing.cyvl_graph import path_to_geojson

    vehicles = load_vehicle_templates()
    if req.vehicle_id not in vehicles:
        raise HTTPException(404, f"Unknown vehicle: {req.vehicle_id}")
    vehicle = {**vehicles[req.vehicle_id], "id": req.vehicle_id}

    g = _get_cyvl_graph()

    # Snap clicked coordinates to nearest routable nodes
    start_node = g.nearest_node(req.start_lon, req.start_lat, min_degree=2)
    end_node   = g.nearest_node(req.end_lon,   req.end_lat,   min_degree=2)

    if not start_node or not end_node:
        raise HTTPException(400, "Could not snap start/end to road network")

    # 1. Naive route (distance only, no blocks)
    naive_path = g.dijkstra(start_node, end_node)
    if not naive_path:
        raise HTTPException(400, "No path found between selected points")

    # 2. Check turn feasibility — uses LiDAR tree/pole positions as obstacles
    feasibility = g.check_route_feasibility(naive_path, vehicle, obstacles=_obstacles)
    blocked_ids = {r["node_id"] for r in feasibility if r["verdict"] == "fail"}

    # 3. Safe route avoids blocked intersections, prefers good pavement
    safe_path = g.dijkstra(
        start_node, end_node,
        blocked=blocked_ids,
        pci_penalty_factor=req.pci_penalty,
    ) or naive_path  # fall back to naive if no safe path exists

    # 4. Build GeoJSON for both routes
    naive_geojson = path_to_geojson(g, naive_path)
    naive_geojson["properties"]["route_type"] = "naive"
    naive_geojson["properties"]["color"] = "#ef4444"

    safe_geojson = path_to_geojson(g, safe_path)
    safe_geojson["properties"]["route_type"] = "safe"
    safe_geojson["properties"]["color"] = "#22c55e"

    # 5. Compute route lengths
    def path_length_m(path):
        total = 0.0
        for a, b in zip(path, path[1:]):
            e = g.edge_meta.get((a, b)) or g.edge_meta.get((b, a))
            if e:
                total += e.length_m
        return round(total)

    naive_len = path_length_m(naive_path)
    safe_len  = path_length_m(safe_path)

    # 6. Snap coords for start/end markers
    sn = g.nodes[start_node]
    en = g.nodes[end_node]

    return {
        "naive_route":   naive_geojson,
        "safe_route":    safe_geojson,
        "rerouted":      naive_path != safe_path,
        "blocked_intersections": feasibility,
        "start": {"node_id": start_node, "lat": sn.lat, "lon": sn.lon},
        "end":   {"node_id": end_node,   "lat": en.lat, "lon": en.lon},
        "stats": {
            "naive_length_m": naive_len,
            "safe_length_m":  safe_len,
            "intersections_checked": len(feasibility),
            "blocked_count": len(blocked_ids),
            "vehicle": vehicle["name"],
        },
    }
