"""
Intersection geometry model + loaders.

An IntersectionGeometry describes the *measured pavement reality* at one
intersection — the things a swept-path check needs:

  - corner_radius_ft : the curb-return radius the vehicle pivots around
                       (small = tight corner). From LiDAR/Cyvl curb assets.
  - road_width_ft    : curb-to-curb width of the receiving street. Bounds how
                       far the vehicle's body can legally swing.
  - obstacles        : point objects (poles, signals, hydrants, trees) inside
                       the corner that the swept path can clip.
  - pci              : pavement condition score (0-100) for the smoothness profile.

The LiDAR / point-cloud extractor (`extract_from_point_cloud`) and the Cyvl
API builder (`geometry.from_cyvl`) both produce this same struct, so the swept
path + routing layers never care where the geometry came from.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class IntersectionGeometry:
    intersection_id: str
    lon: float
    lat: float
    corner_radius_ft: float            # curb-return radius (pivot)
    road_width_ft: float               # curb-to-curb width of receiving street
    turn_angle_deg: float = 90.0       # turn sharpness at this node
    pci: float | None = None           # pavement condition 0-100
    obstacles: list[dict] = field(default_factory=list)   # {type, lon, lat}
    name: str = ""
    source: str = "stub"               # "lidar" | "cyvl" | "stub"

    @property
    def available_radius_ft(self) -> float:
        """Widest outer radius a vehicle may use before crossing the far curb."""
        return self.corner_radius_ft + self.road_width_ft


def extract_from_point_cloud(las_data, intersection_id: str) -> IntersectionGeometry:
    """
    NVIDIA-accelerated path: classify ground/curb returns, fit curb lines, and
    measure the corner-return radius + road width. Not wired for the MVP demo.
    """
    raise NotImplementedError("Point-cloud extraction not yet implemented")


def load_from_geojson(feature: dict) -> IntersectionGeometry:
    """Parse a GeoJSON Feature (sample_intersections.geojson) into geometry."""
    props = feature["properties"]
    geom = feature.get("geometry", {})
    coords = geom.get("coordinates", [[[0, 0]]])
    ring = coords[0] if geom.get("type") == "Polygon" else [[props.get("lon", 0), props.get("lat", 0)]]
    lon = props.get("lon") or sum(c[0] for c in ring) / len(ring)
    lat = props.get("lat") or sum(c[1] for c in ring) / len(ring)
    return IntersectionGeometry(
        intersection_id=props["id"],
        lon=lon,
        lat=lat,
        corner_radius_ft=float(props.get("corner_radius_ft", props.get("curb_radius_ft", 25))),
        road_width_ft=float(props.get("road_width_ft", 24)),
        turn_angle_deg=float(props.get("turn_angle_deg", 90)),
        pci=props.get("pci"),
        obstacles=props.get("obstacles", props.get("encroachments", [])),
        name=props.get("name", props["id"]),
        source="stub",
    )


def load_all_from_geojson(path: Path | None = None) -> list[IntersectionGeometry]:
    from data.loaders import load_intersections
    fc = load_intersections(path)
    return [load_from_geojson(f) for f in fc["features"]]


if __name__ == "__main__":
    for g in load_all_from_geojson():
        print(f"{g.intersection_id:32s} corner={g.corner_radius_ft:>4}ft "
              f"road={g.road_width_ft:>4}ft available={g.available_radius_ft:>5}ft "
              f"pci={g.pci}")
