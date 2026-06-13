"""API routes for feasibility checks and routing."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from data.loaders import load_vehicle_templates, load_intersections
from geometry.extractor import load_from_geojson
from sweptpath.autodesk_backend import compute_swept_path
from routing.graph import build_demo_graph, route

router = APIRouter()


class FeasibilityRequest(BaseModel):
    vehicle_id: str
    intersection_ids: list[str] | None = None  # None = all


class RouteRequest(BaseModel):
    vehicle_id: str
    start: str = "start"
    end: str = "end"


@router.get("/vehicles")
def get_vehicles():
    return load_vehicle_templates()


@router.get("/intersections")
def get_intersections():
    return load_intersections()


@router.post("/feasibility")
def feasibility(req: FeasibilityRequest):
    vehicles = load_vehicle_templates()
    if req.vehicle_id not in vehicles:
        raise HTTPException(404, f"Unknown vehicle: {req.vehicle_id}")

    vehicle = vehicles[req.vehicle_id]
    vehicle["id"] = req.vehicle_id

    fc = load_intersections()
    features = fc["features"]
    if req.intersection_ids:
        features = [f for f in features if f["properties"]["id"] in req.intersection_ids]

    results = []
    for feature in features:
        geom = load_from_geojson(feature)
        result = compute_swept_path(geom, vehicle)
        results.append({
            "intersection_id": result.intersection_id,
            "verdict": result.verdict.value,
            "reason": result.reason,
            "clearance_margin_ft": result.clearance_margin_ft,
            "swept_polygon": result.swept_polygon,
        })
    return results


@router.post("/route")
def get_route(req: RouteRequest):
    vehicles = load_vehicle_templates()
    if req.vehicle_id not in vehicles:
        raise HTTPException(404, f"Unknown vehicle: {req.vehicle_id}")

    vehicle = vehicles[req.vehicle_id]
    vehicle["id"] = req.vehicle_id

    fc = load_intersections()
    feasibility_results = []
    for feature in fc["features"]:
        geom = load_from_geojson(feature)
        feasibility_results.append(compute_swept_path(geom, vehicle))

    graph = build_demo_graph()
    return route(graph, req.start, req.end, feasibility_results)
