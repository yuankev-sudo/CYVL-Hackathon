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
