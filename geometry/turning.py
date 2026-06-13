"""
Vehicle dimensions  ->  turning geometry.

This is the bridge the demo cares about: take the dimensions a user types in
(or an AASHTO preset) and turn them into the radii that define the vehicle's
swept path, which we then test against the LiDAR-measured pavement at each
intersection.

Model (single-track / "bicycle" approximation):

    R_center = wheelbase / tan(steer_max)          # path of the centerline
    R_inner  = R_center - track_width / 2           # inner (rear, off-tracking) wheel
    R_outer  = R_center + width / 2 + overhang_front  # outer front corner — the
                                                      # point that swings widest

R_outer is the radius that actually clips a curb on a turn, so it is what the
swept-path check uses. For articulated trucks the true swept width is larger
than a single-unit bicycle model predicts; the AASHTO presets ship a
`steer_max_deg` calibrated so the derived radius matches the published design
turning radius, and the user can always override any field.
"""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class TurningGeometry:
    turning_radius_ft: float   # centerline turning radius (R_center)
    inner_radius_ft: float     # inner rear wheel path
    outer_radius_ft: float     # outer front corner path — widest swing
    swept_width_ft: float      # R_outer - R_inner: the lateral band the body sweeps


def turning_radius_from_dims(
    wheelbase_ft: float,
    steer_max_deg: float = 31.5,
) -> float:
    """Centerline turning radius from wheelbase + max steer angle (bicycle model)."""
    steer = math.radians(max(1.0, min(steer_max_deg, 75.0)))
    return wheelbase_ft / math.tan(steer)


def turning_geometry(vehicle: dict) -> TurningGeometry:
    """
    Build the full turning geometry for a vehicle dict.

    Honors an explicit `turning_radius_ft` if present; otherwise derives it from
    `wheelbase_ft` + `steer_max_deg`. Everything downstream uses outer_radius_ft.
    """
    width = float(vehicle.get("width_ft", 8.5))
    track = float(vehicle.get("track_width_ft", width - 0.5))
    front_oh = float(vehicle.get("overhang_front_ft", 4.0))

    if vehicle.get("turning_radius_ft"):
        r_center = float(vehicle["turning_radius_ft"])
    else:
        r_center = turning_radius_from_dims(
            float(vehicle.get("wheelbase_ft", 20.0)),
            float(vehicle.get("steer_max_deg", 31.5)),
        )

    r_inner = max(0.0, r_center - track / 2.0)
    r_outer = r_center + width / 2.0 + front_oh
    return TurningGeometry(
        turning_radius_ft=round(r_center, 1),
        inner_radius_ft=round(r_inner, 1),
        outer_radius_ft=round(r_outer, 1),
        swept_width_ft=round(r_outer - r_inner, 1),
    )


if __name__ == "__main__":
    from data.loaders import load_vehicle_templates

    for vid, v in load_vehicle_templates().items():
        derived = turning_radius_from_dims(v["wheelbase_ft"], v["steer_max_deg"])
        g = turning_geometry(v)
        print(
            f"{vid:12s} wb={v['wheelbase_ft']:>4}ft steer={v['steer_max_deg']:>4}deg  "
            f"derived R={derived:5.1f}  spec R={v['turning_radius_ft']:>4}  "
            f"outer={g.outer_radius_ft:5.1f}  swept_width={g.swept_width_ft:4.1f}ft"
        )
