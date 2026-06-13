"""API routes for feasibility checks and routing."""
from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from data.loaders import load_vehicle_templates, load_intersections, load_intersections_live
from geometry.extractor import load_from_geojson
from geometry.from_cyvl import build_from_cyvl, build_all_somerville
from sweptpath.autodesk_backend import compute_swept_path
from routing.osm_graph import get_graph, route, build_demo_graph

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
    vehicle_id: str
    start: str = "start"
    end: str = "end"
    use_osm: bool = False


class SingleIntersectionRequest(BaseModel):
    vehicle_id: str
    intersection_id: str
    lon: float
    lat: float


# ── Reference ─────────────────────────────────────────────────────────────────

@router.get("/vehicles")
def get_vehicles():
    return load_vehicle_templates()


@router.get("/intersections")
def get_intersections(live: bool = Query(False, description="Pull from Cyvl API")):
    if live and _USE_CYVL:
        return load_intersections_live()
    return load_intersections()


@router.get("/status")
def status():
    return {
        "cyvl_connected": _USE_CYVL,
        "autodesk_connected": bool(os.getenv("APS_CLIENT_ID")),
    }


# ── Feasibility ───────────────────────────────────────────────────────────────

@router.post("/feasibility")
def feasibility(req: FeasibilityRequest):
    vehicles = load_vehicle_templates()
    if req.vehicle_id not in vehicles:
        raise HTTPException(404, f"Unknown vehicle: {req.vehicle_id}")

    vehicle = vehicles[req.vehicle_id]
    vehicle["id"] = req.vehicle_id

    # Use live Cyvl geometry if connected, otherwise local GeoJSON
    if _USE_CYVL:
        fc = load_intersections_live()
        features = fc.get("features", [])
        if req.intersection_ids:
            features = [f for f in features if f["properties"].get("id") in req.intersection_ids]
        geometries = build_all_somerville(features)
    else:
        fc = load_intersections()
        features = fc["features"]
        if req.intersection_ids:
            features = [f for f in features if f["properties"]["id"] in req.intersection_ids]
        geometries = [load_from_geojson(f) for f in features]

    results = []
    for geom in geometries:
        result = compute_swept_path(geom, vehicle)
        results.append({
            "intersection_id": result.intersection_id,
            "verdict": result.verdict.value,
            "reason": result.reason,
            "clearance_margin_ft": result.clearance_margin_ft,
            "swept_polygon": result.swept_polygon,
            "encroachments": geom.encroachments,
            "clearance_height_m": geom.clearance_height_m,
        })
    return results


@router.post("/feasibility/point")
def feasibility_at_point(req: SingleIntersectionRequest):
    """Check a single intersection by lon/lat — queries Cyvl live or stubs."""
    vehicles = load_vehicle_templates()
    if req.vehicle_id not in vehicles:
        raise HTTPException(404, f"Unknown vehicle: {req.vehicle_id}")

    vehicle = vehicles[req.vehicle_id]
    vehicle["id"] = req.vehicle_id

    geom = build_from_cyvl(req.intersection_id, req.lon, req.lat)
    result = compute_swept_path(geom, vehicle)
    return {
        "intersection_id": result.intersection_id,
        "verdict": result.verdict.value,
        "reason": result.reason,
        "clearance_margin_ft": result.clearance_margin_ft,
        "swept_polygon": result.swept_polygon,
        "encroachments": geom.encroachments,
        "clearance_height_m": geom.clearance_height_m,
    }


# ── Routing ───────────────────────────────────────────────────────────────────

@router.post("/route")
def get_route(req: RouteRequest):
    vehicles = load_vehicle_templates()
    if req.vehicle_id not in vehicles:
        raise HTTPException(404, f"Unknown vehicle: {req.vehicle_id}")

    vehicle = vehicles[req.vehicle_id]
    vehicle["id"] = req.vehicle_id

    if _USE_CYVL:
        fc = load_intersections_live()
        geometries = build_all_somerville(fc.get("features", []))
    else:
        fc = load_intersections()
        geometries = [load_from_geojson(f) for f in fc["features"]]

    feasibility_results = [compute_swept_path(g, vehicle) for g in geometries]

    graph = get_graph(use_osm=req.use_osm)
    return route(graph, req.start, req.end, feasibility_results)


# ── Cyvl pass-through (useful for frontend exploration) ───────────────────────

def _require_cyvl():
    if not _USE_CYVL:
        raise HTTPException(503, "CYVL_API_KEY not configured")


def _project_id() -> str:
    from data.cyvl_client import get_somerville_project_id
    pid = get_somerville_project_id()
    if not pid:
        raise HTTPException(503, "Could not resolve Somerville project_id — set CYVL_PROJECT_ID in .env")
    return pid


@router.get("/cyvl/assets")
def cyvl_assets(
    lon: float = Query(...), lat: float = Query(...),
    radius_deg: float = Query(0.0005),
    asset_type: str | None = Query(None),
):
    _require_cyvl()
    from data.cyvl_client import get_assets, intersection_bbox
    return get_assets(_project_id(), intersection_bbox(lon, lat, radius_deg), asset_type=asset_type)


@router.get("/cyvl/markings")
def cyvl_markings(
    lon: float = Query(...), lat: float = Query(...),
    radius_deg: float = Query(0.0005),
):
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
