"""
Shapely swept-path backend — the always-working fallback.

The turn is modeled as an annular sector (a wedge of a donut). The vehicle's
body sweeps the band between its inner rear-wheel radius and its outer front
corner radius. The intersection offers the band between the curb-return radius
and that radius plus the receiving road width. The turn is feasible iff the
vehicle's swept band fits inside the pavement band.

    available = sector(inner = corner_radius,  outer = corner_radius + road_width)
    swept     = sector(inner = corner_radius,  outer = vehicle.outer_radius)
    spill     = swept - available          # area outside the pavement

All math is done in feet (a flat local frame); the swept polygon is then
projected to lon/lat around the intersection centroid so the map can draw it.
"""
from __future__ import annotations
import math
import numpy as np
from shapely.geometry import Polygon

from geometry.extractor import IntersectionGeometry
from geometry.turning import turning_geometry
from sweptpath.interface import SweptPathResult, Verdict

FT_PER_DEG_LAT = 364_000.0          # ~ feet per degree latitude
TIGHT_MARGIN_FT = 2.0               # margin below which a PASS becomes TIGHT


def _annular_sector_ft(r_inner, r_outer, start_deg, end_deg, n=48) -> Polygon:
    angles = np.linspace(math.radians(start_deg), math.radians(end_deg), n)
    outer = [(r_outer * math.cos(a), r_outer * math.sin(a)) for a in angles]
    inner = [(r_inner * math.cos(a), r_inner * math.sin(a)) for a in reversed(angles)]
    return Polygon(outer + inner)


def _ft_to_lonlat(coords_ft, lon0, lat0):
    """Project local feet-frame (x=east, y=north) coords to lon/lat."""
    ft_per_deg_lon = FT_PER_DEG_LAT * math.cos(math.radians(lat0))
    return [(lon0 + x / ft_per_deg_lon, lat0 + y / FT_PER_DEG_LAT) for x, y in coords_ft]


def compute_swept_path(
    geometry: IntersectionGeometry,
    vehicle: dict,
    turn_angle_deg: float | None = None,
) -> SweptPathResult:
    turn = turn_angle_deg if turn_angle_deg is not None else geometry.turn_angle_deg
    tg = turning_geometry(vehicle)

    corner = geometry.corner_radius_ft
    available_outer = geometry.available_radius_ft

    swept = _annular_sector_ft(corner, tg.outer_radius_ft, 0, turn)
    available = _annular_sector_ft(corner, available_outer, 0, turn)

    spill = swept.difference(available)
    spill_area = spill.area
    margin_ft = round(available_outer - tg.outer_radius_ft, 1)

    # Project the swept path to lon/lat for the map overlay.
    swept_lonlat = _ft_to_lonlat(list(swept.exterior.coords), geometry.lon, geometry.lat)

    if margin_ft >= TIGHT_MARGIN_FT and spill_area < 1e-6:
        verdict, reason = Verdict.PASS, None
    elif margin_ft >= 0:
        verdict = Verdict.TIGHT
        reason = (f"Only {margin_ft} ft of clearance — vehicle's {tg.outer_radius_ft} ft "
                  f"swing nearly fills the {available_outer:.0f} ft corner")
    else:
        verdict = Verdict.FAIL
        reason = (f"Swept path overruns the curb by {abs(margin_ft)} ft — "
                  f"{tg.outer_radius_ft} ft outer swing vs {available_outer:.0f} ft available; "
                  f"cannot complete this turn in one pass")

    return SweptPathResult(
        intersection_id=geometry.intersection_id,
        vehicle_id=vehicle.get("id", "unknown"),
        verdict=verdict,
        reason=reason,
        swept_polygon=swept_lonlat,
        clearance_margin_ft=margin_ft,
    )


if __name__ == "__main__":
    from data.loaders import load_vehicle_templates
    from geometry.extractor import load_all_from_geojson

    vehicles = load_vehicle_templates()
    for inter in load_all_from_geojson():
        print(f"\n=== {inter.name} (corner {inter.corner_radius_ft}ft + road "
              f"{inter.road_width_ft}ft = {inter.available_radius_ft}ft) ===")
        for vid, v in vehicles.items():
            v["id"] = vid
            r = compute_swept_path(inter, v)
            print(f"  {vid:12s} {r.verdict.value:5s} margin={r.clearance_margin_ft:>6}ft  {r.reason or 'OK'}")
