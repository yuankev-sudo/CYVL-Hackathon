"""
Cyvl Data API client.
Base: https://i3.cyvl.app   Auth: Bearer token (CYVL_API_KEY).

project_id (uuid) is REQUIRED by almost every data endpoint.
Spatial filter: pass EITHER bbox OR (radius_lat + radius_lng + radius_meters).
"""
from __future__ import annotations
import os
import json
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any

BASE_URL = "https://i3.cyvl.app"
CACHE_DIR = Path(__file__).parent / ".cache"


# ── Auth / transport ──────────────────────────────────────────────────────────

def _api_key() -> str:
    key = os.getenv("CYVL_API_KEY", "")
    if not key:
        raise RuntimeError("CYVL_API_KEY not set — copy .env.example to .env and fill it in")
    return key


def _get(path: str, params: dict | None = None, cache_key: str | None = None) -> Any:
    if cache_key:
        CACHE_DIR.mkdir(exist_ok=True)
        cache_file = CACHE_DIR / f"{cache_key}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text())

    url = BASE_URL + path
    if params:
        filtered = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(filtered)

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_api_key()}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if cache_key:
        (CACHE_DIR / f"{cache_key}.json").write_text(json.dumps(data))
    return data


def _post(path: str, body: dict) -> Any:
    url = BASE_URL + path
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# ── Spatial helpers ───────────────────────────────────────────────────────────

def bbox_filter(lon_min: float, lat_min: float, lon_max: float, lat_max: float) -> dict:
    """Bounding-box spatial filter params."""
    return {"bbox": f"{lon_min},{lat_min},{lon_max},{lat_max}"}


def radius_filter(lat: float, lon: float, meters: float = 50.0) -> dict:
    """Radius spatial filter params (alternative to bbox)."""
    return {"radius_lat": lat, "radius_lng": lon, "radius_meters": meters}


def intersection_bbox(lon: float, lat: float, radius_deg: float = 0.0003) -> dict:
    """~33 m square around a point (degrees at Somerville lat)."""
    return bbox_filter(lon - radius_deg, lat - radius_deg, lon + radius_deg, lat + radius_deg)


def somerville_bbox() -> dict:
    return bbox_filter(-71.1350, 42.3700, -71.0700, 42.4100)


# ── Projects ──────────────────────────────────────────────────────────────────

def list_projects(name: str | None = None) -> list[dict]:
    return _get("/api/v1/projects", {"name": name} if name else None)


def get_somerville_project_id() -> str | None:
    """Return cached project ID from env, or find it by name in the project list."""
    pid = os.getenv("CYVL_PROJECT_ID", "")
    if pid:
        return pid
    projects = list_projects()
    # list_projects may return a dict with a 'projects' key or a plain list
    items = projects if isinstance(projects, list) else projects.get("projects", [])
    for p in items:
        name = (p.get("name") or "").lower()
        if "somerville" in name:
            return str(p.get("id") or p.get("project_id"))
    return None


# ── Infrastructure ────────────────────────────────────────────────────────────

def query_infrastructure(
    project_id: str,
    spatial: dict,                    # bbox_filter(...) or radius_filter(...)
    asset_types: list[str] | None = None,
    limit: int = 500,
    cursor: str | None = None,
) -> dict:
    params = {"project_id": project_id, "limit": limit, "cursor": cursor, **spatial}
    if asset_types:
        params["asset_types"] = ",".join(asset_types)
    return _get("/api/v1/infrastructure/query", params)


# ── Assets ────────────────────────────────────────────────────────────────────

def get_assets(
    project_id: str,
    spatial: dict,
    asset_type: str | None = None,
    condition: str | None = None,
    limit: int = 500,
    cursor: str | None = None,
) -> dict:
    return _get("/api/v1/assets", {
        "project_id": project_id,
        "asset_type": asset_type,
        "condition": condition,
        "limit": limit,
        "cursor": cursor,
        **spatial,
    })


def get_asset_detail(asset_id: str, project_id: str, include: list[str] | None = None) -> dict:
    """Full asset detail. include can contain: history, distresses, imagery."""
    params: dict = {"project_id": project_id}
    if include:
        params["include"] = ",".join(include)
    return _get(f"/api/v1/assets/detail/{asset_id}", params)


def get_asset_imagery(asset_id: str, project_id: str) -> dict:
    return _get(f"/api/v1/assets/{asset_id}/imagery", {"project_id": project_id})


def get_asset_history(asset_id: str, project_id: str) -> dict:
    return _get(f"/api/v1/assets/{asset_id}/history", {"project_id": project_id})


def get_asset_types() -> dict:
    return _get("/api/v1/reference/asset-types")


# ── Markings ──────────────────────────────────────────────────────────────────

def get_markings(
    project_id: str,
    spatial: dict,
    category: str | None = None,
    line_type: str | None = None,
    color: str | None = None,
    condition: str | None = None,
    limit: int = 500,
    cursor: str | None = None,
) -> dict:
    return _get("/api/v1/markings", {
        "project_id": project_id,
        "category": category,
        "type": line_type,
        "color": color,
        "condition": condition,
        "limit": limit,
        "cursor": cursor,
        **spatial,
    })


def get_line_types() -> dict:
    return _get("/api/v1/reference/line-types")


# ── Signs ─────────────────────────────────────────────────────────────────────

def get_signs(
    project_id: str,
    spatial: dict,
    mutcd: str | None = None,
    category: str | None = None,
    condition: str | None = None,
    limit: int = 500,
    cursor: str | None = None,
) -> dict:
    return _get("/api/v1/signs", {
        "project_id": project_id,
        "mutcd": mutcd,
        "category": category,
        "condition": condition,
        "limit": limit,
        "cursor": cursor,
        **spatial,
    })


# ── Pavement ──────────────────────────────────────────────────────────────────

def get_pavement_scores(
    project_id: str,
    spatial: dict | None = None,
    score_min: float | None = None,
    score_max: float | None = None,
    limit: int = 500,
    cursor: str | None = None,
) -> dict:
    return _get("/api/v1/pavement/scores", {
        "project_id": project_id,
        "score_min": score_min,
        "score_max": score_max,
        "limit": limit,
        "cursor": cursor,
        **(spatial or {}),
    })


def get_pavement_segments(
    project_id: str,
    spatial: dict | None = None,
    limit: int = 500,
    cursor: str | None = None,
) -> dict:
    return _get("/api/v1/pavement/segments", {
        "project_id": project_id,
        "limit": limit,
        "cursor": cursor,
        **(spatial or {}),
    })


def get_pavement_distresses(
    project_id: str,
    spatial: dict | None = None,
    distress_type: str | None = None,
    severity: str | None = None,
    limit: int = 500,
) -> dict:
    return _get("/api/v1/pavement/distresses", {
        "project_id": project_id,
        "distress_type": distress_type,
        "severity": severity,
        "limit": limit,
        **(spatial or {}),
    })


def get_pci_distribution(project_id: str) -> dict:
    return _get("/api/v1/pavement/pci-distribution", {"project_id": project_id})


# ── Image semantic search ─────────────────────────────────────────────────────

def search_images(
    query: str,
    project_id: str | None = None,
    spatial: dict | None = None,
    page_size: int = 20,
    min_score: float = 0.0,
) -> dict:
    body: dict = {"query": query, "page_size": page_size, "min_score": min_score}
    if project_id:
        body["project_id"] = project_id
    if spatial and "bbox" in spatial:
        body["bbox"] = spatial["bbox"]
    elif spatial and "radius_lat" in spatial:
        body["lat"] = spatial["radius_lat"]
        body["lon"] = spatial["radius_lng"]
        body["radius_m"] = spatial["radius_meters"]
    return _post("/api/v1/embeddings/query", body)


def browse_images(
    project_id: str | None = None,
    spatial: dict | None = None,
    page_size: int = 500,
) -> dict:
    body: dict = {"page_size": page_size}
    if project_id:
        body["project_id"] = project_id
    if spatial and "bbox" in spatial:
        body["bbox"] = spatial["bbox"]
    return _post("/api/v1/embeddings/browse", body)


# ── Imagery normalization ─────────────────────────────────────────────────────
# The embeddings/imagery endpoints return slightly different shapes; we don't
# want the UI to care. These helpers flatten whatever comes back into a simple
# list of {url, thumb, score, lat, lon, caption} so a failing turn can show the
# street-level photo of *why* it fails.

_URL_KEYS = ("image_url", "imageUrl", "signed_url", "signedUrl", "url",
             "src", "href", "image", "full_url", "original_url")
_THUMB_KEYS = ("thumbnail_url", "thumbnailUrl", "thumb_url", "thumb",
               "thumbnail", "preview_url", "small_url")
_LAT_KEYS = ("lat", "latitude", "image_lat", "capture_lat", "y")
_LON_KEYS = ("lon", "lng", "longitude", "image_lon", "image_lng", "capture_lon", "x")
_SCORE_KEYS = ("score", "similarity", "distance", "relevance")
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")


def _looks_like_image_url(val: Any) -> bool:
    if not isinstance(val, str) or not val.startswith(("http://", "https://")):
        return False
    low = val.split("?", 1)[0].lower()
    return low.endswith(_IMG_EXT) or any(t in val.lower() for t in ("image", "img", "thumb", "photo", "frame"))


def _first(d: dict, keys: tuple) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _normalize_image_item(item: dict) -> dict | None:
    """Pull a usable image record out of one result dict, or None if no URL."""
    url = _first(item, _URL_KEYS)
    if not _looks_like_image_url(url):
        # Sometimes the URL is nested (e.g. item["image"]["url"]).
        for v in item.values():
            if isinstance(v, dict):
                nested = _first(v, _URL_KEYS)
                if _looks_like_image_url(nested):
                    url = nested
                    break
    if not _looks_like_image_url(url):
        return None
    thumb = _first(item, _THUMB_KEYS)
    return {
        "url": url,
        "thumb": thumb if _looks_like_image_url(thumb) else url,
        "score": _first(item, _SCORE_KEYS),
        "lat": _first(item, _LAT_KEYS),
        "lon": _first(item, _LON_KEYS),
        "caption": item.get("caption") or item.get("description") or item.get("label"),
    }


def _result_items(payload: Any) -> list[dict]:
    """Find the list of image records inside an arbitrary response envelope."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("results", "images", "items", "data", "matches", "hits"):
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        # Single image record returned bare.
        if _first(payload, _URL_KEYS):
            return [payload]
    return []


def nearby_imagery(
    project_id: str,
    lat: float,
    lon: float,
    radius_m: float = 30.0,
    query: str | None = None,
    limit: int = 6,
) -> list[dict]:
    """
    Best-effort street-level photos near a point. Tries semantic search (when a
    query is given) then falls back to a spatial browse. Always returns a list
    (possibly empty) — never raises — so the demo survives any API hiccup.
    """
    spatial = radius_filter(lat, lon, radius_m)
    out: list[dict] = []
    try:
        if query:
            payload = search_images(query, project_id=project_id, spatial=spatial, page_size=limit)
            out = [r for r in (_normalize_image_item(i) for i in _result_items(payload)) if r]
        if not out:
            payload = browse_images(project_id=project_id, spatial=intersection_bbox(lon, lat), page_size=limit)
            out = [r for r in (_normalize_image_item(i) for i in _result_items(payload)) if r]
    except Exception:  # network, auth, shape — anything. Photos are a bonus, not a blocker.
        return []
    return out[:limit]


# ── Health ────────────────────────────────────────────────────────────────────

def health() -> dict:
    return _get("/health")


if __name__ == "__main__":
    try:
        print("Health:", health())
        projects = list_projects()
        items = projects if isinstance(projects, list) else projects.get("projects", [])
        print("Projects:", [p.get("name") for p in items])
        pid = get_somerville_project_id()
        print("Somerville project ID:", pid)
        if pid:
            print("\nAsset types:")
            types = get_asset_types()
            data = types if isinstance(types, list) else types.get("data", [])
            for t in data[:10]:
                print(" ", t)
    except RuntimeError as e:
        print("Not connected:", e)
