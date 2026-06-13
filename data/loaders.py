"""Loaders for point clouds, intersection GeoJSON, and vehicle templates."""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent

def load_vehicle_templates() -> dict:
    with open(DATA_DIR / "vehicle_templates.json") as f:
        return json.load(f)

def load_intersections(path: Path | None = None) -> dict:
    p = path or DATA_DIR / "sample_intersections.geojson"
    with open(p) as f:
        return json.load(f)

def load_point_cloud(path: Path):
    """Load a LAS/LAZ point cloud. Returns open3d PointCloud or laspy LasData."""
    try:
        import laspy
        return laspy.read(str(path))
    except ImportError:
        raise RuntimeError("laspy not installed — run: pip install laspy")


if __name__ == "__main__":
    vehicles = load_vehicle_templates()
    print("Vehicles loaded:", list(vehicles.keys()))
    intersections = load_intersections()
    print("Intersections loaded:", len(intersections["features"]))
