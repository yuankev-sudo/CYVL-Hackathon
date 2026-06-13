"""
Autodesk Platform Services (APS) swept-path backend.
Uses Vehicle Tracking / Civil 3D API to compute the authoritative swept path.
Falls back gracefully to shapely_backend if APS credentials are unavailable.
"""
from __future__ import annotations
import os
import logging

from geometry.extractor import IntersectionGeometry
from sweptpath.interface import SweptPathResult
from sweptpath import shapely_backend

logger = logging.getLogger(__name__)

APS_CLIENT_ID = os.getenv("APS_CLIENT_ID")
APS_CLIENT_SECRET = os.getenv("APS_CLIENT_SECRET")


def _aps_available() -> bool:
    return bool(APS_CLIENT_ID and APS_CLIENT_SECRET)


def _get_aps_token() -> str:
    import urllib.request, urllib.parse, json
    data = urllib.parse.urlencode({
        "client_id": APS_CLIENT_ID,
        "client_secret": APS_CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": "data:read data:write",
    }).encode()
    req = urllib.request.Request(
        "https://developer.api.autodesk.com/authentication/v2/token",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def compute_swept_path(
    geometry: IntersectionGeometry,
    vehicle: dict,
    turn_angle_deg: float | None = None,
) -> SweptPathResult:
    if not _aps_available():
        logger.debug("APS credentials not set — using shapely backend")
        return shapely_backend.compute_swept_path(geometry, vehicle, turn_angle_deg)

    try:
        token = _get_aps_token()
        # TODO: call APS Vehicle Tracking endpoint with intersection geometry + vehicle spec
        # Placeholder until APS endpoint is wired up
        raise NotImplementedError("APS Vehicle Tracking call not yet implemented")
    except Exception as exc:
        logger.warning("APS call failed (%s) — falling back to shapely backend", exc)
        return shapely_backend.compute_swept_path(geometry, vehicle, turn_angle_deg)


if __name__ == "__main__":
    print("APS available:", _aps_available())
    from data.loaders import load_vehicle_templates
    from geometry.extractor import load_all_from_geojson

    vehicles = load_vehicle_templates()
    intersections = load_all_from_geojson()
    vehicle = list(vehicles.values())[0]
    vehicle["id"] = list(vehicles.keys())[0]
    result = compute_swept_path(intersections[0], vehicle)
    print(result)
