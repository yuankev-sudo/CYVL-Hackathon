"""
Extract curb lines and lane edges from a LiDAR point cloud.
Falls back to reading pre-computed GeoJSON when no point cloud is provided.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import numpy as np

@dataclass
class IntersectionGeometry:
    intersection_id: str
    curb_polygon: list[tuple[float, float]]   # exterior curb as (x,y) pairs (metres or degrees)
    lane_edges: list[list[tuple[float, float]]]
    clearance_height_m: float | None = None    # None means unrestricted
    encroachments: list[dict] | None = None    # objects in the swept zone


def extract_from_point_cloud(las_data, intersection_id: str) -> IntersectionGeometry:
    """
    Segment ground / curb returns and fit a polygon.
    Requires open3d + laspy.  This is the NVIDIA-accelerated path.
    """
    # TODO: classify returns, RANSAC plane fit, alpha-shape curb outline
    raise NotImplementedError("Point-cloud extraction not yet implemented")


def load_from_geojson(feature: dict) -> IntersectionGeometry:
    """Parse a GeoJSON Feature into IntersectionGeometry (demo path)."""
    props = feature["properties"]
    coords = feature["geometry"]["coordinates"][0]
    return IntersectionGeometry(
        intersection_id=props["id"],
        curb_polygon=[(c[0], c[1]) for c in coords],
        lane_edges=[],
        clearance_height_m=props.get("clearance_height_m"),
        encroachments=props.get("encroachments", []),
    )


def load_all_from_geojson(path: Path | None = None) -> list[IntersectionGeometry]:
    from data.loaders import load_intersections
    fc = load_intersections(path)
    return [load_from_geojson(f) for f in fc["features"]]


if __name__ == "__main__":
    geometries = load_all_from_geojson()
    for g in geometries:
        print(f"{g.intersection_id}: {len(g.curb_polygon)} curb vertices")
