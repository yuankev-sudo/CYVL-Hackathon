"""
Nearest Cyvl street-level imagery lookup.

The panoramic layer is a shapefile of ~38k 360° photo points, each carrying a
`image_url` that wraps the raw equirectangular CloudFront image in Cyvl's
3DViewer. We unwrap it to the raw `.../images360/....jpg` so the frontend can
texture it onto a sphere.
"""
from __future__ import annotations
import math
import urllib.parse
from pathlib import Path

DATA_DIR = Path(__file__).parent
PANO_SHP = DATA_DIR / "CityofSomervilleMAMarketingDemo-panoramicImagery" / "layer_zip.shp"

_panos: list[dict] | None = None   # [{lon, lat, bearing, wrapped}]


def _load() -> list[dict]:
    global _panos
    if _panos is None:
        import shapefile
        pts = []
        with shapefile.Reader(str(PANO_SHP)) as sf:
            for rec in sf.iterRecords():
                d = rec.as_dict()
                pts.append({
                    "lon": float(d["lon"]), "lat": float(d["lat"]),
                    "bearing": float(d.get("bearing") or 0.0),
                    "wrapped": d["image_url"],
                })
        _panos = pts
    return _panos


def _unwrap(wrapped: str) -> str:
    """Pull the raw equirectangular CloudFront URL out of the 3DViewer link."""
    q = urllib.parse.urlparse(wrapped).query
    raw = urllib.parse.parse_qs(q).get("image_url", [None])[0]
    return raw or wrapped


def _haversine_m(lon1, lat1, lon2, lat2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_panorama(lat: float, lon: float) -> dict | None:
    """Return {image_url, bearing, lat, lon, dist_m} for the closest 360 photo."""
    best, best_d = None, float("inf")
    for p in _load():
        d = _haversine_m(lon, lat, p["lon"], p["lat"])
        if d < best_d:
            best_d, best = d, p
    if best is None:
        return None
    return {
        "image_url": _unwrap(best["wrapped"]),
        "bearing": best["bearing"],
        "lat": best["lat"], "lon": best["lon"],
        "dist_m": round(best_d, 1),
    }


if __name__ == "__main__":
    # Star Market corner
    print(nearest_panorama(42.3837, -71.1118))
