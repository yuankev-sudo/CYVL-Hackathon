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


# ── Models ──────────────────────────────────────────────────────────────────

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
    from data.cyvl_client import get_pavement_scores, radius_filter
    from data.scenario import SOMERVILLE_PROJECT_ID
    return get_pavement_scores(SOMERVILLE_PROJECT_ID, radius_filter(lat, lon, radius_m))
