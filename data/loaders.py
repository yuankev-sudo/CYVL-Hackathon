"""Loaders for point clouds, intersection GeoJSON, and vehicle templates."""
from __future__ import annotations
import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent


def load_vehicle_templates() -> dict:
    with open(DATA_DIR / "vehicle_templates.json") as f:
        return json.load(f)


def load_intersections(path: Path | None = None) -> dict:
    """Load intersection GeoJSON from disk (local fallback)."""
    p = path or DATA_DIR / "sample_intersections.geojson"
    with open(p) as f:
        return json.load(f)


def load_intersections_live(project_id: str | None = None) -> dict:
    """
    Load intersections from the Cyvl API for the Somerville project.
    Returns a GeoJSON FeatureCollection so the rest of the pipeline is unchanged.
    Falls back to local GeoJSON if CYVL_API_KEY is not set.
    """
    if not os.getenv("CYVL_API_KEY"):
        return load_intersections()

    try:
        from data.cyvl_client import (
            get_somerville_project_id,
            query_infrastructure,
            somerville_bbox,
        )

        pid = project_id or get_somerville_project_id()
        if not pid:
            raise RuntimeError("Could not resolve Somerville project_id")

        infra = query_infrastructure(pid, somerville_bbox())

        # Normalise: if the API already returned a FeatureCollection, use it
        if isinstance(infra, dict) and infra.get("type") == "FeatureCollection":
            return infra

        # Wrap a plain list into a FeatureCollection
        features = infra if isinstance(infra, list) else infra.get("features", [])
        return {"type": "FeatureCollection", "features": features}

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Cyvl live load failed (%s) — using local GeoJSON", e)
        return load_intersections()


def load_point_cloud(path: Path):
    """Load a LAS/LAZ point cloud. Returns laspy LasData."""
    try:
        import laspy
        return laspy.read(str(path))
    except ImportError:
        raise RuntimeError("laspy not installed — run: pip install laspy")


if __name__ == "__main__":
    vehicles = load_vehicle_templates()
    print("Vehicles loaded:", list(vehicles.keys()))

    fc = load_intersections()
    print("Local intersections:", len(fc["features"]))

    fc_live = load_intersections_live()
    print("Live intersections: ", len(fc_live["features"]),
          "(live)" if os.getenv("CYVL_API_KEY") else "(fallback — no CYVL_API_KEY)")
