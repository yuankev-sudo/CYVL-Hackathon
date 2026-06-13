"""
Generate a 3D maneuver scene for ONE intersection corner, from coordinate
geometry (no point cloud). Produces the data contract the 3D viewer consumes.

Everything is emitted in a local meter frame: origin at the intersection node,
+x = east, +y = north, z = up. heading_deg is CCW from +x.

The road is reconstructed from the connected edge directions (arms). The turn
is a smooth quadratic-Bezier path from the approach arm to the exit arm, sampled
densely so the animation is smooth. The swept band is the path buffered by the
vehicle's width; conflict zones are the parts of that band that fall OUTSIDE the
pavement — exactly where a swept path would clip a curb.
"""
from __future__ import annotations
import math
from shapely.geometry import LineString, Point, Polygon, MultiPolygon
from shapely.ops import unary_union

from geometry.turning import turning_geometry

M_PER_DEG_LAT = 111_320.0
FT_TO_M = 0.3048

ROAD_HALF_M = 5.0       # half curb-to-curb width per arm (-> 10 m roads)
ARM_LEN_M = 32.0        # how far each arm extends from the node
PATH_REACH_M = 22.0     # how far up the approach/exit arms the truck path runs
N_POSES = 40            # samples along the turn (animation resolution)


def _polys(geom) -> list[list[list[float]]]:
    """Exterior rings of a (Multi)Polygon as lists of [x,y]."""
    out = []
    if geom.is_empty:
        return out
    parts = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    for p in parts:
        if p.area <= 0:
            continue
        out.append([[round(x, 2), round(y, 2)] for x, y in p.exterior.coords])
    return out


def _line_coords(geom) -> list[list[list[float]]]:
    """Boundary line(s) as lists of [x,y]."""
    out = []
    lines = geom.geoms if geom.geom_type.startswith("Multi") else [geom]
    for ln in lines:
        out.append([[round(x, 2), round(y, 2)] for x, y in ln.coords])
    return out


def build_corner(
    node_lonlat: tuple[float, float],
    arms_lonlat: list[tuple[float, float]],
    in_idx: int,
    out_idx: int,
    vehicle: dict,
    intersection_id: str = "corner",
    verdict: str | None = None,
) -> dict:
    """
    node_lonlat : (lon, lat) of the intersection node.
    arms_lonlat : (lon, lat) of a point a short way along each connected edge.
    in_idx/out_idx : indices into arms for the approach and exit roads.
    """
    lon0, lat0 = node_lonlat
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

    def to_local(lon, lat):
        return ((lon - lon0) * m_per_deg_lon, (lat - lat0) * M_PER_DEG_LAT)

    # Unit direction of each arm (pointing outward from the node).
    dirs = []
    for lon, lat in arms_lonlat:
        x, y = to_local(lon, lat)
        n = math.hypot(x, y) or 1.0
        dirs.append((x / n, y / n))

    # ── Road surface: each arm as a flat ribbon, plus a disk at the node ──
    arm_polys = []
    lane_edges = []
    for ux, uy in dirs:
        seg = LineString([(0, 0), (ux * ARM_LEN_M, uy * ARM_LEN_M)])
        arm_polys.append(seg.buffer(ROAD_HALF_M, cap_style=2))   # flat cap
        lane_edges.append([[0.0, 0.0], [round(ux * ARM_LEN_M, 2), round(uy * ARM_LEN_M, 2)]])
    road = unary_union(arm_polys + [Point(0, 0).buffer(ROAD_HALF_M + 1.0)])

    # ── Turn path: quadratic Bezier (approach arm) -> node -> (exit arm) ──
    ax, ay = dirs[in_idx]
    cx, cy = dirs[out_idx]
    A = (ax * PATH_REACH_M, ay * PATH_REACH_M)   # start, up the approach arm
    C = (cx * PATH_REACH_M, cy * PATH_REACH_M)   # end, up the exit arm
    # control point at the node (0,0) -> curve bends through the intersection
    path_pts = []
    headings = []
    for i in range(N_POSES + 1):
        t = i / N_POSES
        # B(t) with control V=(0,0): (1-t)^2 A + t^2 C
        bx = (1 - t) ** 2 * A[0] + t ** 2 * C[0]
        by = (1 - t) ** 2 * A[1] + t ** 2 * C[1]
        # derivative: 2[t*C - (1-t)*A]
        dx = 2 * (t * C[0] - (1 - t) * A[0])
        dy = 2 * (t * C[1] - (1 - t) * A[1])
        path_pts.append((bx, by))
        headings.append(math.degrees(math.atan2(dy, dx)))

    path = LineString(path_pts)

    # ── Swept band + conflict zones ──
    tg = turning_geometry(vehicle)
    width_m = float(vehicle.get("width_ft", 8.5)) * FT_TO_M
    # half the body plus a turn/off-tracking allowance that grows with the swing
    swing_allow = max(0.4, (tg.outer_radius_ft - 30.0) / 30.0)
    half_band = width_m / 2.0 + swing_allow
    swept = path.buffer(half_band, cap_style=2, join_style=1)
    conflict = swept.difference(road.buffer(0.01))
    conflict_polys = [p for p in (conflict.geoms if isinstance(conflict, MultiPolygon) else [conflict])
                      if not p.is_empty and p.area > 0.4]
    conflict_area = round(sum(p.area for p in conflict_polys), 1)

    derived = "pass" if conflict_area < 0.5 else ("tight" if conflict_area < 10 else "fail")
    verdict = verdict or derived

    # If routing flagged this corner but the synthesized geometry didn't catch a
    # conflict (arm widths are generic), mark the inside-corner curb the swept
    # path cuts, so the 3D view stays consistent with the routing verdict.
    if verdict in ("fail", "tight") and not conflict_polys:
        bx, by = ax + cx, ay + cy
        bnorm = math.hypot(bx, by)
        if bnorm < 1e-6:                       # arms nearly opposite -> use a perpendicular
            bx, by, bnorm = -ay, ax, 1.0
        bx, by = bx / bnorm, by / bnorm
        r = ROAD_HALF_M + 1.0
        cxp, cyp = bx * r, by * r              # point in the inside-corner sector
        s = 2.5 if verdict == "fail" else 1.8
        inner = Point(cxp, cyp).buffer(s, resolution=2)
        conflict_polys = [inner]
        conflict_area = round(inner.area, 1)

    # ── Poses (dense) + step milestones ──
    poses = [{"x": round(x, 2), "y": round(y, 2), "heading_deg": round(h, 1)}
             for (x, y), h in zip(path_pts, headings)]

    conflict_word = {"fail": "the swept path overruns the curb here — cannot clear in one pass",
                     "tight": "clearance is tight — swing wide and check the inside curb",
                     "pass": "clearance is adequate through the turn"}[verdict]
    steps = [
        {"at": 0.0, "instruction": "Approach in the correct lane and pull forward toward the intersection."},
        {"at": 0.3, "instruction": "Begin the turn — swing wide; the rear wheels track inside the cab."},
        {"at": 0.55, "instruction": f"Apex of the turn: {conflict_word}."},
        {"at": 0.85, "instruction": "Straighten out into the exit lane."},
        {"at": 1.0, "instruction": "Turn complete."},
    ]

    return {
        "intersection_id": intersection_id,
        "vehicle": vehicle.get("name", vehicle.get("id", "vehicle")),
        "verdict": verdict,
        "road_polygons": _polys(road),
        "curb_lines": _line_coords(road.boundary),
        "lane_edges": lane_edges,
        "swept_path": _polys(swept)[0] if _polys(swept) else [],
        "conflict_zones": [[[round(x, 2), round(y, 2)] for x, y in p.exterior.coords] for p in conflict_polys],
        "conflict_area_m2": conflict_area,
        "poses": poses,
        "steps": steps,
        "vehicle_dims_m": {
            "length": round(float(vehicle.get("length_ft", 40)) * FT_TO_M, 2),
            "width": round(width_m, 2),
            "height": round(float(vehicle.get("height_ft", 11.5)) * FT_TO_M, 2),
        },
        "origin": {"lat": lat0, "lon": lon0},
    }


if __name__ == "__main__":
    # 4-way: arms N, E, S, W. Approach from south (idx 2), exit east (idx 1) = left turn? right.
    node = (-71.105, 42.3805)
    d = 0.0008
    arms = [(-71.105, 42.3805 + d), (-71.105 + d, 42.3805),
            (-71.105, 42.3805 - d), (-71.105 - d, 42.3805)]
    veh = {"id": "WB-67", "name": "WB-67", "width_ft": 8.5, "wheelbase_ft": 41,
           "steer_max_deg": 42.3, "turning_radius_ft": 45, "length_ft": 73.5, "height_ft": 13.5}
    c = build_corner(node, arms, in_idx=2, out_idx=1, vehicle=veh, verdict="fail")
    print("verdict:", c["verdict"], "conflict area:", c["conflict_area_m2"], "m2")
    print("road polys:", len(c["road_polygons"]), "curb lines:", len(c["curb_lines"]),
          "poses:", len(c["poses"]), "conflict zones:", len(c["conflict_zones"]))
    print("swept pts:", len(c["swept_path"]))
