"""
Real Cyvl pavement-distress lookup (cached).

Pre-fetched from the Cyvl API (get_pavement_score_detail) for the worst corridor
segments — distress types/severity + the inspection-cell mask photo that
highlights the cracks/potholes. Lets the app back its PCI numbers with the
surveyed reality without a live API key at runtime.
"""
from __future__ import annotations
import json
import math
from pathlib import Path

_CACHE = Path(__file__).parent / "distress_cache.json"
_points: list[dict] | None = None


def _load() -> list[dict]:
    global _points
    if _points is None:
        _points = json.loads(_CACHE.read_text()).get("points", [])
    return _points


def _haversine_m(lon1, lat1, lon2, lat2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def points_near_path(route_pts: list, max_m: float = 45.0) -> list[dict]:
    """Cached distress points within max_m of any point on a route polyline,
    worst (lowest score) first, each tagged with its distance to the route."""
    out = []
    for p in _load():
        if not route_pts:
            continue
        d = min(_haversine_m(p["lon"], p["lat"], x, y) for x, y in route_pts)
        if d <= max_m:
            out.append({**p, "dist_m": round(d, 1)})
    return sorted(out, key=lambda p: p["score"])


def nearest_distress(lon: float, lat: float, max_m: float = 60.0) -> dict | None:
    """Nearest cached distress inspection to a point, within max_m, else None."""
    best, best_d = None, float("inf")
    for p in _load():
        d = _haversine_m(lon, lat, p["lon"], p["lat"])
        if d < best_d:
            best_d, best = d, p
    if best is None or best_d > max_m:
        return None
    return {**best, "dist_m": round(best_d, 1)}


if __name__ == "__main__":
    print("cached distress points:", len(_load()))
    print(nearest_distress(-71.1058, 42.3881))
