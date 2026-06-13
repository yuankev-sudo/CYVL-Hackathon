"""
Build IntersectionGeometry from live Cyvl API data.

Given an intersection centroid (lon, lat), queries:
  - infrastructure/query   → curb polygon
  - assets (trees)         → encroachments + clearance height
  - markings               → lane edges

Falls back gracefully to the GeoJSON stub if the API is unavailable.
"""
from __future__ import annotations
import os
import logging
from typing import Any

from geometry.extractor import IntersectionGeometry

logger = logging.getLogger(__name__)

# Height above which a tree canopy is an overhead clearance concern (metres)
_TREE_CLEARANCE_THRESHOLD_M = 4.0


def _extract_coords(geometry: dict) -> list[tuple[float, float]]:
    """Pull exterior ring from any GeoJSON geometry type."""
    gtype = geometry.get("type", "")
    coords = geometry.get("coordinates", [])
    if gtype == "Polygon":
        return [(c[0], c[1]) for c in coords[0]]
    if gtype == "MultiPolygon":
        return [(c[0], c[1]) for c in coords[0][0]]
    if gtype in ("LineString", "MultiLineString"):
        ring = coords if gtype == "LineString" else coords[0]
        return [(c[0], c[1]) for c in ring]
    return []


def _best_curb_polygon(infra_response: dict, lon: float, lat: float) -> list[tuple[float, float]]:
    """
    Pick the road/curb polygon from an infrastructure query response.
    Falls back to a small square around the centroid if nothing useful comes back.
    """
    features = []
    if isinstance(infra_response, dict):
        features = infra_response.get("features", [])
    elif isinstance(infra_response, list):
        features = infra_response

    for f in features:
        geom = f.get("geometry") or {}
        if geom.get("type") in ("Polygon", "MultiPolygon"):
            coords = _extract_coords(geom)
            if len(coords) >= 3:
                return coords

    # Fallback: 30m square (degrees) around centroid
    d = 0.0003
    return [
        (lon - d, lat - d), (lon + d, lat - d),
        (lon + d, lat + d), (lon - d, lat + d),
        (lon - d, lat - d),
    ]


def _parse_encroachments(assets_response: dict) -> list[dict]:
    """Extract trees and poles as encroachment records."""
    encroachments = []
    features = []
    if isinstance(assets_response, dict):
        features = assets_response.get("features", assets_response.get("assets", []))
    elif isinstance(assets_response, list):
        features = assets_response

    for f in features:
        props = f.get("properties", f)
        asset_type = str(props.get("asset_type") or props.get("type") or "").lower()
        if asset_type not in ("tree", "pole", "sign", "hydrant"):
            continue
        geom = f.get("geometry", {})
        coords = geom.get("coordinates", [None, None])
        encroachments.append({
            "type": asset_type,
            "lon": coords[0],
            "lat": coords[1],
            "height_m": props.get("height_m") or props.get("height"),
            "label": props.get("label") or props.get("description"),
        })
    return encroachments


def _min_clearance(assets_response: dict) -> float | None:
    """Return minimum overhead clearance from tree/bridge assets, or None."""
    features = []
    if isinstance(assets_response, dict):
        features = assets_response.get("features", assets_response.get("assets", []))
    elif isinstance(assets_response, list):
        features = assets_response

    heights = []
    for f in features:
        props = f.get("properties", f)
        asset_type = str(props.get("asset_type") or props.get("type") or "").lower()
        if "tree" not in asset_type:
            continue
        h = props.get("height_m") or props.get("height") or props.get("canopy_height_m")
        if h and float(h) > _TREE_CLEARANCE_THRESHOLD_M:
            heights.append(float(h))
    return min(heights) if heights else None


def _parse_lane_edges(markings_response: dict) -> list[list[tuple[float, float]]]:
    """Extract lane-edge LineStrings from markings response."""
    edges = []
    features = []
    if isinstance(markings_response, dict):
        features = markings_response.get("features", markings_response.get("markings", []))
    elif isinstance(markings_response, list):
        features = markings_response

    for f in features:
        geom = f.get("geometry", {})
        if geom.get("type") in ("LineString", "MultiLineString"):
            edges.append(_extract_coords(geom))
    return edges


def build_from_cyvl(
    intersection_id: str,
    lon: float,
    lat: float,
    project_id: str | None = None,
) -> IntersectionGeometry:
    """
    Query Cyvl API and return an IntersectionGeometry.
    Falls back to a stub geometry if CYVL_API_KEY is not set.
    """
    from data.cyvl_client import (
        intersection_bbox, query_infrastructure,
        get_assets, get_markings, get_somerville_project_id,
    )

    spatial = intersection_bbox(lon, lat)
    pid = project_id or os.getenv("CYVL_PROJECT_ID") or get_somerville_project_id()
    if not pid:
        logger.warning("No project_id available — using centroid stub geometry")
        return IntersectionGeometry(
            intersection_id=intersection_id,
            curb_polygon=_best_curb_polygon({}, lon, lat),
            lane_edges=[], clearance_height_m=None, encroachments=[],
        )

    try:
        infra = query_infrastructure(pid, spatial)
    except Exception as e:
        logger.warning("Cyvl infrastructure query failed (%s) — using centroid stub", e)
        infra = {}

    try:
        assets = get_assets(pid, spatial)
    except Exception as e:
        logger.warning("Cyvl assets query failed (%s)", e)
        assets = {}

    try:
        markings = get_markings(pid, spatial)
    except Exception as e:
        logger.warning("Cyvl markings query failed (%s)", e)
        markings = {}

    return IntersectionGeometry(
        intersection_id=intersection_id,
        curb_polygon=_best_curb_polygon(infra, lon, lat),
        lane_edges=_parse_lane_edges(markings),
        clearance_height_m=_min_clearance(assets),
        encroachments=_parse_encroachments(assets),
    )


def build_all_somerville(
    intersections: list[dict],
    project_id: str | None = None,
) -> list[IntersectionGeometry]:
    """
    Build IntersectionGeometry for a list of intersection dicts.
    Each dict must have: id, lon, lat  (or geometry.coordinates centroid).
    """
    results = []
    for item in intersections:
        iid = item.get("id") or item["properties"]["id"]
        if "lon" in item:
            lon, lat = item["lon"], item["lat"]
        else:
            # GeoJSON feature — use polygon centroid
            coords = item["geometry"]["coordinates"][0]
            lon = sum(c[0] for c in coords) / len(coords)
            lat = sum(c[1] for c in coords) / len(coords)
        results.append(build_from_cyvl(iid, lon, lat, project_id))
    return results


if __name__ == "__main__":
    from data.loaders import load_intersections
    fc = load_intersections()
    geoms = build_all_somerville(fc["features"])
    for g in geoms:
        print(f"{g.intersection_id}: curb={len(g.curb_polygon)}pts  "
              f"encroachments={len(g.encroachments or [])}  "
              f"clearance={g.clearance_height_m}m")
