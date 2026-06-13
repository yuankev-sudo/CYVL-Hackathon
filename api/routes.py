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


# ── All-profiles route (single call returns fastest + smoothest + largevehicle) ──

class AllRoutesRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    vehicle_id: str | None = None
    vehicle: dict | None = None  # dimension overrides for the truck profile


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
    paths = {}
    for profile, defaults in PROFILE_DEFAULTS.items():
        pci   = defaults["pci_penalty"]
        blocks = blocked_ids if defaults["block_turns"] else set()
        path  = g.dijkstra(start_node, end_node, blocked=blocks,
                           pci_penalty_factor=pci) or naive_path
        paths[profile] = path
        gj = path_to_geojson(g, path)
        gj["properties"].update({"route_type": profile, "color": ROUTE_COLORS[profile]})

        length_m  = _path_length_m(g, path)
        naive_len = _path_length_m(g, naive_path)
        avg_pci   = _route_avg_pci(g, path)
        naive_pci = _route_avg_pci(g, naive_path)

        routes[profile] = {
            "geojson": gj,
            "stats": {
                "length_m":             length_m,
                "extra_m":              (length_m or 0) - (naive_len or 0),
                "avg_pci":              avg_pci,
                "naive_avg_pci":        naive_pci,
                "blocked_count":        len(blocks),
                "intersections_checked": len(feasibility),
            },
        }

    # Conflict corners ON the route the truck actually drives (the large-vehicle
    # path) — these are the tight turns a driver must operate carefully and can
    # simulate. The fail corners it routed around are reported separately.
    lv_path = paths["largevehicle"]
    route_feasibility = g.check_route_feasibility(lv_path, vehicle, obstacles=_obstacles)
    lv_node_set = set(lv_path)
    avoided = [r for r in feasibility if r["verdict"] == "fail" and r["node_id"] not in lv_node_set]

    sn, en = g.nodes[start_node], g.nodes[end_node]
    return {
        "routes":      routes,
        "feasibility": route_feasibility,   # corners on the large-vehicle route
        "avoided":     avoided,             # fail corners the reroute avoided
        "lv_nodes":    lv_path,             # node sequence of the large-vehicle route
        "start": {"node_id": start_node, "lat": sn.lat, "lon": sn.lon},
        "end":   {"node_id": end_node,   "lat": en.lat, "lon": en.lon},
        "vehicle": {
            "id":                vehicle["id"],
            "name":              vehicle.get("name"),
            "turning_radius_ft": tg.turning_radius_ft,
            "outer_radius_ft":   tg.outer_radius_ft,
            "swept_width_ft":    tg.swept_width_ft,
        },
    }


# ── Per-corner 3D maneuver scene (lazy — only for clicked conflict corners) ───

class CornerRequest(BaseModel):
    node_id: str
    prev_id: str | None = None     # route node entering this corner (approach arm)
    next_id: str | None = None     # route node leaving this corner (exit arm)
    vehicle_id: str | None = None
    vehicle: dict | None = None


def _node_arms(g, node_id: str):
    """
    Return [(neighbor_id, (lon,lat) direction-point)] for every distinct road
    touching the node — scanning edge_meta directly so ONE-WAY approach arms
    (whose edge is only stored in the incoming direction) are included too.
    """
    arms, seen = [], set()
    nx_, ny_ = g.nodes[node_id].lon, g.nodes[node_id].lat
    for (a, b), e in g.edge_meta.items():
        if a == node_id:
            nb = b
        elif b == node_id:
            nb = a
        else:
            continue
        if nb in seen:
            continue
        seen.add(nb)
        dirpt = None
        if e and e.points and len(e.points) >= 2:
            pts = e.points
            head_near = abs(pts[0][0] - nx_) + abs(pts[0][1] - ny_)
            tail_near = abs(pts[-1][0] - nx_) + abs(pts[-1][1] - ny_)
            dirpt = pts[1] if head_near <= tail_near else pts[-2]
        else:
            dirpt = (g.nodes[nb].lon, g.nodes[nb].lat)
        arms.append((nb, (dirpt[0], dirpt[1])))
    return arms


@router.post("/corner")
def corner_scene(req: CornerRequest):
    """
    Build the 3D maneuver scene for ONE intersection node: reconstructed road
    geometry, the vehicle's swept path, conflict zones, dense truck poses, driver
    instructions, and the nearest Cyvl 360° panorama. Generated on demand so we
    only pay for corners the user actually inspects.
    """
    from geometry.corner import build_corner
    from data.imagery import nearest_panorama

    g = _get_cyvl_graph()
    if req.node_id not in g.nodes:
        raise HTTPException(404, f"Unknown node: {req.node_id}")

    vehicle = _resolve_vehicle(req.vehicle_id, req.vehicle)
    vehicle["turning_radius_ft"] = turning_geometry(vehicle).turning_radius_ft

    arms = _node_arms(g, req.node_id)
    if len(arms) < 2:
        raise HTTPException(400, "Node is not a turnable intersection (need >= 2 arms)")
    neighbor_ids = [a[0] for a in arms]

    # Approach/exit arms from the route context, else the two sharpest arms.
    in_idx = neighbor_ids.index(req.prev_id) if req.prev_id in neighbor_ids else 0
    out_idx = neighbor_ids.index(req.next_id) if req.next_id in neighbor_ids else (1 if len(arms) > 1 else 0)
    if in_idx == out_idx:
        out_idx = (in_idx + 1) % len(arms)

    # Verdict consistent with routing when we have the route context.
    verdict = None
    if req.prev_id and req.next_id:
        feas = g.check_route_feasibility([req.prev_id, req.node_id, req.next_id], vehicle,
                                         obstacles=_obstacles)
        for r in feas:
            if r["node_id"] == req.node_id:
                verdict = r["verdict"]
                break

    node = g.nodes[req.node_id]
    scene = build_corner(
        (node.lon, node.lat),
        [a[1] for a in arms],
        in_idx=in_idx, out_idx=out_idx,
        vehicle=vehicle,
        intersection_id=req.node_id,
        verdict=verdict,
    )
    scene["panorama"] = nearest_panorama(node.lat, node.lon)
    return scene


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
