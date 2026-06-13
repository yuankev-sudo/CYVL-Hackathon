"""
Local street-level imagery from the Cyvl Somerville shapefiles.

The data/ folder ships two point layers, each ~tens of thousands of captured
frames tagged with lat/lon/bearing and an image URL:

  - panoramicImagery : 360° frames, URL is an interactive 3D viewer
                       (platform.cyvl.ai/3DViewer.html?image_url=…<frame>.jpg)
  - plainImagery     : flat front-facing frames, URL is a direct .jpg

This module finds the nearest captured frame to a point (a failing turn) so the
UI can show a real, high-quality 3D photo of *why* the turn fails — with no
network/API key required. It complements data.cyvl_client.nearby_imagery, which
needs a live Cyvl key; this path always works from the bundled data.
"""
from __future__ import annotations

import math
import urllib.parse
from pathlib import Path

import shapefile  # pyshp

_DATA_DIR = Path(__file__).parent
_LAYERS = {
    "panoramic": _DATA_DIR / "CityofSomervilleMAMarketingDemo-panoramicImagery" / "layer_zip.shp",
    "plain": _DATA_DIR / "CityofSomervilleMAMarketingDemo-plainImagery" / "layer_zip.shp",
}

# Loaded lazily once per process, then reused. Each value is a list of frame
# dicts: {id, image_url, lat, lon, bearing}.
_FRAMES: dict[str, list[dict]] = {}


def _load_layer(layer: str) -> list[dict]:
    """Read one imagery shapefile into a flat list of frame records (cached)."""
    if layer in _FRAMES:
        return _FRAMES[layer]
    path = _LAYERS[layer]
    frames: list[dict] = []
    if path.exists():
        r = shapefile.Reader(str(path))
        fields = [f[0] for f in r.fields[1:]]
        for rec in r.records():
            d = dict(zip(fields, rec))
            lat, lon = d.get("lat"), d.get("lon")
            if lat is None or lon is None:
                continue
            frames.append({
                "id": d.get("id"),
                "image_url": d.get("image_url") or "",
                "lat": float(lat),
                "lon": float(lon),
                "bearing": d.get("bearing"),
            })
    _FRAMES[layer] = frames
    return frames


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _underlying_jpg(image_url: str) -> str:
    """
    Pull the raw .jpg out of a 3D-viewer URL so an <img> tag can render it.

    panoramic URLs look like:
      https://platform.cyvl.ai/3DViewer.html?image_url=https://…/frame.jpg
    plain URLs are already a direct .jpg.
    """
    if "image_url=" in image_url:
        try:
            qs = urllib.parse.urlparse(image_url).query
            inner = urllib.parse.parse_qs(qs).get("image_url", [None])[0]
            if inner:
                return inner
        except Exception:
            pass
    return image_url


def nearby_imagery(
    lat: float,
    lon: float,
    radius_m: float = 40.0,
    limit: int = 4,
    layer: str = "panoramic",
) -> list[dict]:
    """
    Nearest captured frames to (lat, lon), closest first, within radius_m.

    Returns a list of normalized records matching what the frontend expects:
      {url, thumb, viewer_url, score, lat, lon, bearing, caption}
    - url/thumb   : direct .jpg so it renders in an <img>/lightbox
    - viewer_url  : interactive 3D/360° viewer (panoramic only) for "open full"
    Always returns a list (possibly empty); never raises.
    """
    try:
        frames = _load_layer(layer)
    except Exception:
        return []

    scored: list[tuple[float, dict]] = []
    for f in frames:
        d = _haversine_m(lon, lat, f["lon"], f["lat"])
        if d <= radius_m:
            scored.append((d, f))
    scored.sort(key=lambda x: x[0])

    out: list[dict] = []
    for dist, f in scored[:limit]:
        viewer = f["image_url"]
        jpg = _underlying_jpg(viewer)
        is_3d = "3dviewer" in viewer.lower() or "image_url=" in viewer
        out.append({
            "url": jpg,
            "thumb": jpg,
            "viewer_url": viewer if is_3d else None,
            "score": round(dist, 1),
            "lat": f["lat"],
            "lon": f["lon"],
            "bearing": f["bearing"],
            "caption": f"{'360° capture' if is_3d else 'Street view'} · {dist:.0f} m from turn",
        })
    return out


if __name__ == "__main__":
    # Sanity demo: a point in central Somerville should have nearby frames.
    test_lat, test_lon = 42.3849809, -71.1003459
    for lyr in ("panoramic", "plain"):
        imgs = nearby_imagery(test_lat, test_lon, radius_m=40, layer=lyr)
        print(f"\n{lyr}: {len(_load_layer(lyr))} frames total, "
              f"{len(imgs)} within 40 m of test point")
        for im in imgs:
            print(f"  {im['score']:>5} m  3d={bool(im['viewer_url'])}  {im['url'][:80]}")
