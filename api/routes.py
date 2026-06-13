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
            "naive_length_m": _path_length_m(g, naive_path),
            "safe_length_m": _path_length_m(g, chosen_path),
            "naive_avg_pci": _route_avg_pci(g, naive_path),
            "safe_avg_pci": _route_avg_pci(g, chosen_path),
            "intersections_checked": len(feasibility),
            "blocked_count": len(blocked_ids),
            "vehicle": vehicle.get("name"),
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
