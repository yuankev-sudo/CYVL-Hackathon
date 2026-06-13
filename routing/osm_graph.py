"""
OSM-based routing graph for Somerville, MA.

Queries the Overpass API for drivable ways inside a bounding box,
builds a weighted graph, and finds paths that avoid FAIL intersections.

Falls back to the hardcoded demo graph if the network request fails.
"""
from __future__ import annotations
import json
import math
import urllib.request
import urllib.parse
import logging
from dataclasses import dataclass, field
from collections import defaultdict
import heapq

from sweptpath.interface import Verdict, SweptPathResult

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Somerville bounding box: south, west, north, east
SOMERVILLE_BBOX = (42.3700, -71.1350, 42.4100, -71.0700)


@dataclass
class Node:
    id: str
    lon: float
    lat: float


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[str, list[tuple[str, float]]] = field(default_factory=lambda: defaultdict(list))

    def add_node(self, node: Node):
        self.nodes[node.id] = node

    def add_edge(self, a: str, b: str, weight: float):
        self.edges[a].append((b, weight))
        self.edges[b].append((a, weight))

    def dijkstra(self, start: str, end: str, blocked: set[str]) -> list[str] | None:
        dist = {start: 0.0}
        prev: dict[str, str | None] = {start: None}
        pq = [(0.0, start)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == end:
                path, cur = [], u
                while cur is not None:
                    path.append(cur)
                    cur = prev[cur]
                return list(reversed(path))
            if d > dist.get(u, float("inf")):
                continue
            for v, w in self.edges.get(u, []):
                if v in blocked:
                    continue
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        return None


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def build_from_osm(bbox: tuple[float, float, float, float] = SOMERVILLE_BBOX) -> Graph:
    """Fetch drivable ways from Overpass and build a routing graph."""
    s, w, n, e = bbox
    query = f"""
    [out:json][timeout:25];
    (
      way["highway"~"^(primary|secondary|tertiary|residential|unclassified|trunk|motorway)$"]
         ({s},{w},{n},{e});
    );
    out body;
    >;
    out skel qt;
    """
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        osm = json.loads(resp.read())

    # Index OSM nodes
    osm_nodes: dict[int, tuple[float, float]] = {}
    for el in osm["elements"]:
        if el["type"] == "node":
            osm_nodes[el["id"]] = (el["lon"], el["lat"])

    g = Graph()
    for el in osm["elements"]:
        if el["type"] != "way":
            continue
        nids = el["nodes"]
        for nid in nids:
            if nid not in osm_nodes:
                continue
            lon, lat = osm_nodes[nid]
            g.add_node(Node(str(nid), lon, lat))
        for a, b in zip(nids, nids[1:]):
            if a not in osm_nodes or b not in osm_nodes:
                continue
            lon1, lat1 = osm_nodes[a]
            lon2, lat2 = osm_nodes[b]
            w = _haversine_m(lon1, lat1, lon2, lat2)
            g.add_edge(str(a), str(b), w)

    logger.info("OSM graph: %d nodes, %d edge lists", len(g.nodes), len(g.edges))
    return g


def build_demo_graph() -> Graph:
    """Hardcoded Somerville demo graph — instant, no network."""
    g = Graph()
    g.add_node(Node("start",                       -71.1000, 42.3830))
    g.add_node(Node("somerville-medford-pearl",    -71.0995, 42.3825))
    g.add_node(Node("somerville-holland-broadway", -71.1215, 42.3967))
    g.add_node(Node("somerville-mcgrath-washington",-71.0946, 42.3818))
    g.add_node(Node("end",                         -71.0940, 42.3812))

    g.add_edge("start",                       "somerville-medford-pearl",     200.0)
    g.add_edge("somerville-medford-pearl",    "somerville-mcgrath-washington", 300.0)
    g.add_edge("somerville-mcgrath-washington","end",                          100.0)
    # Bypass around medford-pearl
    g.add_edge("start",                       "somerville-mcgrath-washington", 650.0)
    return g


def get_graph(use_osm: bool = False) -> Graph:
    """Return OSM graph if requested (and network available), else demo graph."""
    if use_osm:
        try:
            return build_from_osm()
        except Exception as e:
            logger.warning("OSM fetch failed (%s) — using demo graph", e)
    return build_demo_graph()


def route(
    graph: Graph,
    start: str,
    end: str,
    feasibility: list[SweptPathResult],
) -> dict:
    blocked = {r.intersection_id for r in feasibility if r.verdict == Verdict.FAIL}
    naive = graph.dijkstra(start, end, blocked=set())
    safe  = graph.dijkstra(start, end, blocked=blocked)
    return {
        "naive_route": naive,
        "safe_route":  safe,
        "blocked_intersections": list(blocked),
        "rerouted": naive != safe,
    }


if __name__ == "__main__":
    from data.loaders import load_vehicle_templates
    from geometry.extractor import load_all_from_geojson
    from sweptpath.shapely_backend import compute_swept_path

    vehicles = load_vehicle_templates()
    intersections = load_all_from_geojson()
    vehicle = vehicles["FIRE-LADDER"]
    vehicle["id"] = "FIRE-LADDER"

    feasibility = [compute_swept_path(i, vehicle) for i in intersections]
    for r in feasibility:
        print(f"  {r.intersection_id}: {r.verdict.value}  {r.reason or ''}")

    graph = build_demo_graph()
    result = route(graph, "start", "end", feasibility)
    print("\nNaive:", result["naive_route"])
    print("Safe: ", result["safe_route"])
    print("Rerouted?", result["rerouted"])
