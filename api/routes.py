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
import hashlib
import io
import os
import urllib.parse
import urllib.request
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from data.loaders import load_vehicle_templates
from data.scenario import SOMERVILLE_PROJECT_ID
from geometry.turning import turning_geometry

router = APIRouter()

_USE_CYVL = bool(os.getenv("CYVL_API_KEY"))

# The road graph is heavy to build (~1s) — build once, cache for the process.
_cyvl_graph = None


def _get_cyvl_graph():
    global _cyvl_graph
    if _cyvl_graph is None:
        from routing.cyvl_graph import build_from_shapefiles
        _cyvl_graph = build_from_shapefiles()
    return _cyvl_graph


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


@router.get("/intersection/imagery")
def intersection_imagery(
    lon: float = Query(...),
    lat: float = Query(...),
    radius_m: float = Query(30),
    verdict: str | None = Query(None),     # "fail" | "tight" — tunes the search query
):
    """
    Street-level photos near a failing/tight turn so the user can *see* the
    obstruction. Each failure point gets a real, high-quality 3D/360° photo.

    Source order:
      1. Bundled Cyvl imagery shapefiles in data/ (no API key needed) — the
         nearest 360° panoramic frame to the turn. This always works.
      2. If that finds nothing, live Cyvl semantic search (when a key is set).

    Best-effort: returns {"images": []} if nothing is found, so the UI degrades
    gracefully.
    """
    from data.local_imagery import nearby_imagery as local_imagery
    # Local 360° frames keyed by proximity — the "3D high quality photo" of the
    # exact spot the swept path fails.
    images = local_imagery(lat, lon, radius_m=max(radius_m, 40), limit=2, layer="panoramic")

    if not images and _USE_CYVL:
        from data.cyvl_client import nearby_imagery as cyvl_imagery
        query = "narrow intersection corner with curb and obstructions blocking a wide turn"
        if verdict == "tight":
            query = "tight street corner with curb, parked cars, and roadside objects"
        images = cyvl_imagery(SOMERVILLE_PROJECT_ID, lat, lon, radius_m=radius_m, query=query)

    return {"images": images}


# ── Thumbnail proxy ───────────────────────────────────────────────────────────
# The bundled 360° frames are full-resolution equirectangular jpgs (~3-4 MB
# each). Loading several of those raw as <img> thumbnails is what makes the
# "Loading site photos…" spinner hang. This downloads each frame once, resizes
# it small, caches it to disk, and serves the lightweight version — so repeat
# views are instant.

_THUMB_CACHE = Path(__file__).parent.parent / "data" / ".thumb_cache"
_THUMB_HOSTS = ("cloudfront.net", "cyvl.ai", "cyvl.app")


@router.get("/intersection/photo-thumb")
def photo_thumb(url: str = Query(...), w: int = Query(480, ge=64, le=1024)):
    """Resized, disk-cached thumbnail of a remote frame jpg."""
    host = urllib.parse.urlparse(url).hostname or ""
    if not url.startswith("https://") or not any(host.endswith(h) for h in _THUMB_HOSTS):
        raise HTTPException(400, "URL not allowed")

    _THUMB_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{url}|{w}".encode()).hexdigest()
    cache_file = _THUMB_CACHE / f"{key}.jpg"
    if cache_file.exists():
        return Response(cache_file.read_bytes(), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})

    try:
        from PIL import Image
        req = urllib.request.Request(url, headers={"User-Agent": "ClearPath/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
        if img.width > w:
            img = img.resize((w, max(1, round(img.height * w / img.width))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80, optimize=True)
        data = buf.getvalue()
    except Exception as e:
        raise HTTPException(502, f"Could not fetch image: {e}")

    cache_file.write_bytes(data)
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})
