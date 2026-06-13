"""
Shapely swept-path backend — the always-working fallback.
Approximates a vehicle turn as an annular sector swept between inner and
outer turning radii, then checks overlap with the curb polygon.
"""
from __future__ import annotations
import math
import numpy as np
from shapely.geometry import Polygon, Point, MultiPolygon
from shapely.ops import unary_union

from geometry.extractor import IntersectionGeometry
from sweptpath.interface import SweptPathResult, Verdict

# ft → metres
FT_TO_M = 0.3048


def _annular_sector(
    cx: float, cy: float,
    r_inner: float, r_outer: float,
    start_deg: float, end_deg: float,
    n_pts: int = 64,
) -> Polygon:
    """Return a Shapely polygon for an annular sector (donut slice)."""
    angles = np.linspace(math.radians(start_deg), math.radians(end_deg), n_pts)
    outer = [(cx + r_outer * math.cos(a), cy + r_outer * math.sin(a)) for a in angles]
    inner = [(cx + r_inner * math.cos(a), cy + r_inner * math.sin(a)) for a in reversed(angles)]
    return Polygon(outer + inner)


def compute_swept_path(
    geometry: IntersectionGeometry,
    vehicle: dict,
    turn_angle_deg: float = 90.0,
) -> SweptPathResult:
    """
    Compute whether vehicle can complete a turn at this intersection.

    The vehicle turns around the intersection's geometric centroid.
    Inner radius  = vehicle turning_radius
    Outer radius  = turning_radius + vehicle width
    We then check if the swept annular sector fits inside the curb polygon.
    """
    curb = Polygon(geometry.curb_polygon)

    r_inner_ft = vehicle["turning_radius_ft"]
    r_outer_ft = r_inner_ft + vehicle["width_ft"]
    r_inner = r_inner_ft * FT_TO_M
    r_outer = r_outer_ft * FT_TO_M

    cx, cy = curb.centroid.x, curb.centroid.y

    swept = _annular_sector(cx, cy, r_inner, r_outer, 0, turn_angle_deg)

    # Scale curb to metres if it looks like it's in degrees (rough heuristic)
    if abs(cx) > 10 or abs(cy) > 10:
        # Coordinates are lon/lat — scale swept path to degree-equivalent
        # ~111,139 m per degree latitude
        scale = 1 / 111_139
        swept = _annular_sector(cx, cy, r_inner * scale, r_outer * scale, 0, turn_angle_deg)

    overlap = swept.difference(curb)
    overlap_area = overlap.area
    curb_area = curb.area

    swept_coords = list(swept.exterior.coords)

    if overlap_area < 1e-10:
        margin_ft = (curb.buffer(-r_outer * (1/111_139 if abs(cx) > 10 else 1)).area ** 0.5) * (1 / FT_TO_M)
        return SweptPathResult(
            intersection_id=geometry.intersection_id,
            vehicle_id=vehicle.get("id", "unknown"),
            verdict=Verdict.PASS,
            reason=None,
            swept_polygon=swept_coords,
            clearance_margin_ft=round(margin_ft, 1),
        )

    overlap_pct = overlap_area / swept.area
    if overlap_pct < 0.05:
        return SweptPathResult(
            intersection_id=geometry.intersection_id,
            vehicle_id=vehicle.get("id", "unknown"),
            verdict=Verdict.TIGHT,
            reason=f"Swept path clips curb by {overlap_pct*100:.1f}% of vehicle area",
            swept_polygon=swept_coords,
            clearance_margin_ft=-round(overlap_pct * r_outer_ft, 1),
        )

    return SweptPathResult(
        intersection_id=geometry.intersection_id,
        vehicle_id=vehicle.get("id", "unknown"),
        verdict=Verdict.FAIL,
        reason=f"Swept path exceeds curb by {overlap_pct*100:.1f}% — cannot complete turn in one pass",
        swept_polygon=swept_coords,
        clearance_margin_ft=-round(overlap_pct * r_outer_ft, 1),
    )


if __name__ == "__main__":
    from data.loaders import load_vehicle_templates
    from geometry.extractor import load_all_from_geojson

    vehicles = load_vehicle_templates()
    intersections = load_all_from_geojson()

    for intersection in intersections:
        print(f"\n=== {intersection.intersection_id} ===")
        for vid, vehicle in vehicles.items():
            vehicle["id"] = vid
            result = compute_swept_path(intersection, vehicle)
            print(f"  {vid:12s}  {result.verdict.value:5s}  {result.reason or 'OK'}")
