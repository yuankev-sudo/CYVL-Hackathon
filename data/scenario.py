"""
The demo scenario: a grocery-run corridor through Somerville, MA.

Origin is Star Market (275 Beacon St); destinations are real nearby places
(Market Basket, Union Square, City Hall). The node coordinates sit on the real
street grid and the corridor is wired so there are TWO ways from Star Market to
Market Basket:

    DIRECT  : star_market -> beacon_park -> somerville_park -> som_ave_mid -> market_basket
    DETOUR  : star_market -> beacon_park -> highland_medford -> som_ave_mid -> market_basket

`somerville_park` is a deliberately tight, rough corner. It is the villain of
the demo:
  - the truck profile must avoid it (swept path overruns the curb),
  - the ambulance/smooth profile prefers to avoid it (low PCI = rough ride),
  - the fastest profile drives straight through it.

Per-node `corner_radius_ft` / `road_width_ft` stand in for LiDAR-measured curb
geometry. PCI and obstacles are baked as a fallback but get overwritten with
LIVE Cyvl data when an API key is present (see `enrich_from_cyvl`).
"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field

from geometry.extractor import IntersectionGeometry

logger = logging.getLogger(__name__)

SOMERVILLE_PROJECT_ID = "f15b854a-d203-49c7-bc25-1350dd4a1cd6"

ORIGIN = "star_market"
DESTINATIONS = ["market_basket", "union_square", "city_hall"]

# id -> node spec.  kind: origin | dest | intersection
# corner_radius_ft + road_width_ft describe the drivable corner (LiDAR/curb).
NODES: dict[str, dict] = {
    "star_market":      {"name": "Star Market (275 Beacon St)", "kind": "origin",
                         "lon": -71.1118, "lat": 42.3837, "corner_radius_ft": 40, "road_width_ft": 40, "pci": 85},
    "market_basket":    {"name": "Market Basket (400 Somerville Ave)", "kind": "dest",
                         "lon": -71.1016, "lat": 42.3807, "corner_radius_ft": 40, "road_width_ft": 40, "pci": 85},
    "union_square":     {"name": "Union Square", "kind": "dest",
                         "lon": -71.0939, "lat": 42.3770, "corner_radius_ft": 40, "road_width_ft": 40, "pci": 80},
    "city_hall":        {"name": "Somerville City Hall", "kind": "dest",
                         "lon": -71.0982, "lat": 42.3872, "corner_radius_ft": 40, "road_width_ft": 40, "pci": 80},

    "beacon_park":      {"name": "Beacon St & Park St", "kind": "intersection",
                         "lon": -71.1064, "lat": 42.3819, "corner_radius_ft": 28, "road_width_ft": 32, "pci": 82},
    "somerville_park":  {"name": "Somerville Ave & Park St (Tight Corner)", "kind": "intersection",
                         "lon": -71.1050, "lat": 42.3805, "corner_radius_ft": 12, "road_width_ft": 26, "pci": 28,
                         "obstacles": [{"type": "traffic_signal_pole", "lon": -71.10498, "lat": 42.38052}]},
    "som_ave_mid":      {"name": "Somerville Ave & Bow St", "kind": "intersection",
                         "lon": -71.1030, "lat": 42.3806, "corner_radius_ft": 28, "road_width_ft": 32, "pci": 80},
    "highland_medford": {"name": "Highland Ave & Medford St", "kind": "intersection",
                         "lon": -71.1030, "lat": 42.3845, "corner_radius_ft": 32, "road_width_ft": 34, "pci": 90},
    "washington_jct":   {"name": "Washington St & Somerville Ave", "kind": "intersection",
                         "lon": -71.0995, "lat": 42.3815, "corner_radius_ft": 34, "road_width_ft": 40, "pci": 80},
    "prospect_union":   {"name": "Prospect St & Washington St", "kind": "intersection",
                         "lon": -71.0965, "lat": 42.3788, "corner_radius_ft": 30, "road_width_ft": 36, "pci": 76},
}

# Undirected road segments. Distance is computed; pci is the worst pavement
# along the segment (governs ride comfort).
EDGES: list[tuple[str, str]] = [
    ("star_market", "beacon_park"),
    ("beacon_park", "somerville_park"),
    ("somerville_park", "som_ave_mid"),
    ("beacon_park", "highland_medford"),
    ("highland_medford", "som_ave_mid"),
    ("highland_medford", "city_hall"),
    ("som_ave_mid", "market_basket"),
    ("som_ave_mid", "washington_jct"),
    ("market_basket", "washington_jct"),
    ("washington_jct", "prospect_union"),
    ("prospect_union", "union_square"),
    ("washington_jct", "city_hall"),
]


@dataclass
class Edge:
    a: str
    b: str
    length_m: float
    pci: float


@dataclass
class Scenario:
    origin: str
    destinations: list[str]
    nodes: dict[str, dict]                          # raw node specs (for the map)
    geometry: dict[str, IntersectionGeometry]       # per-node swept-path geometry
    edges: list[Edge]
    source: str = "baked"                           # "baked" | "cyvl"


def haversine_m(lon1, lat1, lon2, lat2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _geometry_for(node_id: str, spec: dict, source: str) -> IntersectionGeometry:
    return IntersectionGeometry(
        intersection_id=node_id,
        lon=spec["lon"], lat=spec["lat"],
        corner_radius_ft=float(spec.get("corner_radius_ft", 30)),
        road_width_ft=float(spec.get("road_width_ft", 32)),
        turn_angle_deg=float(spec.get("turn_angle_deg", 90)),
        pci=spec.get("pci"),
        obstacles=spec.get("obstacles", []),
        name=spec.get("name", node_id),
        source=source,
    )


def build_scenario(live: bool = False) -> Scenario:
    """Assemble the corridor scenario. If live, overwrite PCI/obstacles from Cyvl."""
    nodes = {k: dict(v) for k, v in NODES.items()}
    source = "baked"

    if live:
        try:
            enrich_from_cyvl(nodes)
            source = "cyvl"
        except Exception as e:
            logger.warning("Cyvl enrichment failed (%s) — using baked node values", e)

    geometry = {nid: _geometry_for(nid, spec, source) for nid, spec in nodes.items()}

    edges: list[Edge] = []
    for a, b in EDGES:
        na, nb = nodes[a], nodes[b]
        length = haversine_m(na["lon"], na["lat"], nb["lon"], nb["lat"])
        pci = min(na.get("pci", 75), nb.get("pci", 75))
        edges.append(Edge(a=a, b=b, length_m=round(length, 1), pci=pci))

    return Scenario(origin=ORIGIN, destinations=DESTINATIONS,
                    nodes=nodes, geometry=geometry, edges=edges, source=source)


def enrich_from_cyvl(nodes: dict[str, dict]) -> None:
    """
    Overwrite each intersection node's PCI (length-weighted avg of nearby
    pavement scores) and obstacles (nearby poles/signals/hydrants) with live
    Cyvl data. Mutates `nodes` in place. Raises on hard failure so the caller
    can fall back to baked values.
    """
    from data.cyvl_client import get_pavement_scores, get_assets, radius_filter

    pid = SOMERVILLE_PROJECT_ID
    obstacle_types = {"TRAFFIC_SIGNAL_POLE", "UTILITY_POLE", "HYDRANT",
                      "TRAFFIC_SIGNAL", "LUMINARIES", "FLASHING_BEACONS"}

    for nid, spec in nodes.items():
        if spec.get("kind") != "intersection":
            continue
        spatial = radius_filter(spec["lat"], spec["lon"], meters=35)

        # --- PCI: length-weighted average of nearby inspection scores ---
        try:
            resp = get_pavement_scores(pid, spatial)
            feats = resp.get("features", []) if isinstance(resp, dict) else []
            num = den = 0.0
            for f in feats:
                p = f.get("properties", {})
                score = p.get("condition_score")
                length = p.get("length_ft", 1) or 1
                if score is not None:
                    num += float(score) * float(length)
                    den += float(length)
            if den:
                spec["pci"] = round(num / den, 1)
                spec["pci_source"] = "cyvl"
        except Exception as e:
            logger.debug("PCI enrich failed for %s: %s", nid, e)

        # --- Obstacles: nearby point assets that can clip a swept path ---
        try:
            resp = get_assets(pid, spatial)
            feats = resp.get("features", []) if isinstance(resp, dict) else []
            obstacles = []
            for f in feats:
                p = f.get("properties", {})
                atype = str(p.get("asset_type", "")).upper()
                if atype in obstacle_types:
                    coords = (f.get("geometry") or {}).get("coordinates", [None, None])
                    obstacles.append({"type": atype.lower(), "lon": coords[0], "lat": coords[1]})
            if obstacles:
                spec["obstacles"] = obstacles
        except Exception as e:
            logger.debug("Obstacle enrich failed for %s: %s", nid, e)


if __name__ == "__main__":
    import os
    sc = build_scenario(live=bool(os.getenv("CYVL_API_KEY")))
    print(f"Scenario source: {sc.source}")
    print(f"Origin: {sc.origin}   Destinations: {sc.destinations}\n")
    for nid, g in sc.geometry.items():
        if sc.nodes[nid]["kind"] == "intersection":
            print(f"  {nid:18s} corner={g.corner_radius_ft:>4}ft road={g.road_width_ft:>4}ft "
                  f"pci={g.pci}  obstacles={len(g.obstacles)}")
    print("\nEdges:")
    for e in sc.edges:
        print(f"  {e.a:18s} -- {e.b:18s} {e.length_m:6.0f} m  pci={e.pci}")
