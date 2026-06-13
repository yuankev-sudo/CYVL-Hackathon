"""
Build a routing graph from the Cyvl Somerville centerline shapefile,
augmented with segment-to-segment pavement scores via spatial join.

Graph nodes  = OSM node IDs (u/v fields in centerline shapefile).
Edge weight  = length in metres + optional PCI roughness penalty.
Each edge carries pci_score / pci_label for display and penalty routing.

Spatial coverage: full city of Somerville, MA
  Centerline: 2 167 edges, 1 535 nodes, ~5 km × 5 km
  Pavement  :   894 scored segments (subset of the above)

Usage:
  graph = build_from_shapefiles()                     # full city
  graph = build_from_shapefiles(bbox=(-71.10, 42.38, -71.09, 42.39))  # 1 km²
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path
import heapq

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
CENTERLINE_SHP = (
    DATA_DIR
    / "CityofSomervilleMAMarketingDemo-centerline"
    / "somerville_ma_streets_final_2.shp"
)
PAVEMENT_SHP = (
    DATA_DIR
    / "CityofSomervilleMAMarketingDemo-Segment-to-Segment Pavement Scores"
    / "layer_zip.shp"
)

# Full Somerville extent [minx, miny, maxx, maxy] — ~5 km × 5 km
SOMERVILLE_BBOX = (-71.1343408, 42.3734084, -71.0752535, 42.4180395)

# PCI label → score midpoint (for display when score is missing)
PCI_LABEL_ORDER = ["Good", "Satisfactory", "Fair", "Poor", "Very Poor", "Serious", "Failed"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Node:
    id: str
    lon: float
    lat: float


@dataclass
class Edge:
    from_id: str
    to_id: str
    length_m: float
    oneway: bool = False
    pci_score: float | None = None
    pci_label: str = "Unknown"
    points: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    # adjacency: node_id -> [(neighbor_id, base_length_m)]
    adj: dict[str, list[tuple[str, float]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # rich edge metadata keyed by directed (from, to)
    edge_meta: dict[tuple[str, str], Edge] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(self, node: Node) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: Edge) -> None:
        self.adj[edge.from_id].append((edge.to_id, edge.length_m))
        self.edge_meta[(edge.from_id, edge.to_id)] = edge
        if not edge.oneway:
            self.adj[edge.to_id].append((edge.from_id, edge.length_m))
            self.edge_meta[(edge.to_id, edge.from_id)] = edge

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def intersection_nodes(self) -> list[str]:
        """Node IDs where 3+ road segments meet — true intersections."""
        return [nid for nid, nbrs in self.adj.items() if len(nbrs) >= 3]

    def nearest_node(self, lon: float, lat: float, min_degree: int = 0) -> str:
        """
        Return the node ID closest to (lon, lat).
        min_degree: skip nodes with fewer outgoing edges (use 1 to exclude
        oneway dead-ends, 2 to stick to through-roads).
        """
        best_id, best_d = None, float("inf")
        for nid, node in self.nodes.items():
            if min_degree and len(self.adj.get(nid, [])) < min_degree:
                continue
            d = _haversine_m(lon, lat, node.lon, node.lat)
            if d < best_d:
                best_d, best_id = d, nid
        return best_id  # type: ignore[return-value]

    def subgraph_bbox(
        self, minx: float, miny: float, maxx: float, maxy: float
    ) -> "Graph":
        """Return a new Graph containing only nodes/edges inside the bbox."""
        g = Graph()
        for nid, node in self.nodes.items():
            if minx <= node.lon <= maxx and miny <= node.lat <= maxy:
                g.add_node(node)
        for (a, b), edge in self.edge_meta.items():
            if a in g.nodes and b in g.nodes:
                # avoid double-adding undirected edges
                if (b, a) not in g.edge_meta:
                    g.add_edge(edge)
        return g

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def dijkstra(
        self,
        start: str,
        end: str,
        blocked: set[str] | None = None,
        pci_penalty_factor: float = 0.0,
    ) -> list[str] | None:
        """
        Shortest path (Dijkstra) from start to end.

        Args:
            blocked: node IDs to skip (e.g. FAIL intersections).
            pci_penalty_factor: metres to add per PCI point below 100.
                0  = pure distance routing (ignores pavement quality).
                1  = moderate pavement preference.
                5  = strongly prefer good pavement.
        """
        _blocked = blocked or set()
        dist: dict[str, float] = {start: 0.0}
        prev: dict[str, str | None] = {start: None}
        pq: list[tuple[float, str]] = [(0.0, start)]

        while pq:
            d, u = heapq.heappop(pq)
            if u == end:
                path: list[str] = []
                cur: str | None = u
                while cur is not None:
                    path.append(cur)
                    cur = prev[cur]
                return list(reversed(path))
            if d > dist.get(u, float("inf")):
                continue
            for v, base_w in self.adj.get(u, []):
                if v in _blocked:
                    continue
                w = base_w
                if pci_penalty_factor > 0:
                    meta = self.edge_meta.get((u, v))
                    if meta and meta.pci_score is not None:
                        w += (100.0 - meta.pci_score) * pci_penalty_factor
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        return None

    def route(
        self,
        start: str,
        end: str,
        blocked: set[str] | None = None,
        pci_penalty_factor: float = 0.0,
    ) -> dict:
        """
        Return naive route and (if blocked nodes given) a safe alternative.
        Mirrors the interface in graph.py / osm_graph.py for drop-in use.
        """
        _blocked = blocked or set()
        naive = self.dijkstra(start, end, blocked=set(), pci_penalty_factor=0.0)
        safe = self.dijkstra(start, end, blocked=_blocked, pci_penalty_factor=pci_penalty_factor)
        return {
            "naive_route": naive,
            "safe_route": safe,
            "blocked_nodes": list(_blocked),
            "rerouted": naive != safe,
        }

    def check_route_feasibility(
        self,
        node_path: list[str],
        vehicle: dict,
        obstacles: list[tuple[float, float]] | None = None,
    ) -> list[dict]:
        """
        For each intersection node (degree >= 3) along node_path, check whether
        the vehicle can make the turn using road geometry + optional LiDAR obstacles.

        obstacles: list of (lon, lat) for trees / utility poles from above-ground assets.
        Returns list of {node_id, verdict, reason, lon, lat}.
        """
        isect_set = set(self.intersection_nodes())
        _obstacles = obstacles or []
        results = []
        for i, node_id in enumerate(node_path):
            if node_id not in isect_set:
                continue
            entry_edge = exit_edge = None
            if i > 0:
                prev_id = node_path[i - 1]
                entry_edge = self.edge_meta.get((prev_id, node_id)) or self.edge_meta.get((node_id, prev_id))
            if i < len(node_path) - 1:
                next_id = node_path[i + 1]
                exit_edge = self.edge_meta.get((node_id, next_id)) or self.edge_meta.get((next_id, node_id))

            if not entry_edge or not exit_edge:
                continue

            node = self.nodes[node_id]
            nearest_obs = _nearest_obstacle_m(node.lon, node.lat, _obstacles) if _obstacles else None
            verdict, reason = _check_turn(
                entry_edge, exit_edge, node.lon, node.lat, vehicle,
                nearest_obstacle_m=nearest_obs,
            )

            results.append({
                "node_id": node_id,
                "verdict": verdict,
                "reason": reason,
                "lon": node.lon,
                "lat": node.lat,
            })
        return results


# ---------------------------------------------------------------------------
# Turn feasibility helpers
# ---------------------------------------------------------------------------


def _vec(p1: tuple[float, float], p2: tuple[float, float]) -> tuple[float, float]:
    return (p2[0] - p1[0], p2[1] - p1[1])


def _turn_deviation_deg(
    entry_pts: list[tuple[float, float]],
    exit_pts: list[tuple[float, float]],
    node_lon: float,
    node_lat: float,
) -> float:
    """
    Turn sharpness in degrees: 0=straight through, 90=right-angle, 180=U-turn.
    Uses node coordinates to correctly orient both edge polylines — avoids
    the broken float-equality comparison that caused most edges to read reversed.
    """
    # Orient entry so its LAST point is closest to the node (truck approaches node)
    entry = list(entry_pts)
    if (math.hypot(entry[-1][0] - node_lon, entry[-1][1] - node_lat) >
            math.hypot(entry[0][0]  - node_lon, entry[0][1]  - node_lat)):
        entry = entry[::-1]

    # Orient exit so its FIRST point is closest to the node (truck departs from node)
    exit_ = list(exit_pts)
    if (math.hypot(exit_[0][0]  - node_lon, exit_[0][1]  - node_lat) >
            math.hypot(exit_[-1][0] - node_lon, exit_[-1][1] - node_lat)):
        exit_ = exit_[::-1]

    if len(entry) < 2 or len(exit_) < 2:
        return 0.0

    entry_vec = (entry[-1][0] - entry[-2][0], entry[-1][1] - entry[-2][1])  # towards node
    exit_vec  = (exit_[1][0]  - exit_[0][0],  exit_[1][1]  - exit_[0][1])  # away from node

    mag1 = math.hypot(*entry_vec)
    mag2 = math.hypot(*exit_vec)
    if mag1 < 1e-12 or mag2 < 1e-12:
        return 0.0

    # Angle between approach direction and departure direction:
    # 0° = same direction (straight through), 90° = right-angle turn, 180° = U-turn
    cos_a = (entry_vec[0] * exit_vec[0] + entry_vec[1] * exit_vec[1]) / (mag1 * mag2)
    cos_a = max(-1.0, min(1.0, cos_a))
    return math.degrees(math.acos(cos_a))


def _nearest_obstacle_m(
    node_lon: float,
    node_lat: float,
    obstacles: list[tuple[float, float]],
    search_radius_m: float = 12.0,
) -> float | None:
    """
    Return distance in metres to the nearest obstacle (tree / utility pole)
    within search_radius_m of the intersection node, or None if clear.
    Uses LiDAR-derived point positions from above-ground assets.
    """
    best = None
    for olon, olat in obstacles:
        d = _haversine_m(node_lon, node_lat, olon, olat)
        if d <= search_radius_m:
            if best is None or d < best:
                best = d
    return best


def _road_width_m(edge: "Edge") -> float:
    """Estimate road width from edge metadata. One-way = 1 lane, two-way = 2 lanes."""
    LANE_WIDTH_M = 3.65
    return LANE_WIDTH_M if edge.oneway else LANE_WIDTH_M * 2


def _check_turn(
    entry_edge: "Edge",
    exit_edge: "Edge",
    node_lon: float,
    node_lat: float,
    vehicle: dict,
    nearest_obstacle_m: float | None = None,
) -> tuple[str, str | None]:
    """
    Return (verdict, reason) for a vehicle making this turn.

    Road width is derived from edge oneway flag (1 lane for one-way streets,
    2 lanes for two-way), so narrow residential one-ways correctly produce fails.
    Obstacle clearance from LiDAR trees/poles further constrains available space.
    """
    FT_TO_M = 0.3048

    deviation = _turn_deviation_deg(entry_edge.points, exit_edge.points, node_lon, node_lat)

    if deviation < 20.0:
        return "pass", None  # straight through or very gentle curve

    if deviation > 165.0:
        return "fail", f"Near-U-turn ({deviation:.0f}deg) — not feasible for large vehicles"

    r_needed_ft = vehicle["turning_radius_ft"]
    r_needed_m  = r_needed_ft * FT_TO_M

    # Total usable width = approach width + departure width
    # (truck can use its own lane + adjacent crossing lane)
    entry_w = _road_width_m(entry_edge)
    exit_w  = _road_width_m(exit_edge)
    full_width_m = entry_w + exit_w

    sin_d = math.sin(math.radians(deviation))

    if deviation <= 90.0:
        available_r_m = full_width_m / (sin_d + 1e-9)
    else:
        # Sharper than 90°: progressively less room; linearly reduce to ~0 at 165°
        factor = (165.0 - deviation) / 75.0
        available_r_m = full_width_m * factor / (sin_d + 1e-9)

    # If a tree or pole sits within the swept arc, subtract its encroachment
    if nearest_obstacle_m is not None:
        # Obstacle effectively reduces the clearance the truck has
        available_r_m = min(available_r_m, nearest_obstacle_m)

    available_r_ft = available_r_m / FT_TO_M

    if r_needed_m <= available_r_m:
        return "pass", None
    elif r_needed_m <= available_r_m * 1.15:
        obstacle_note = f" (tree/pole at {nearest_obstacle_m/FT_TO_M:.0f}ft)" if nearest_obstacle_m else ""
        return "tight", (
            f"{deviation:.0f}deg turn: needs {r_needed_ft:.0f}ft, "
            f"{available_r_ft:.0f}ft available{obstacle_note}"
        )
    else:
        obstacle_note = f" (obstacle at {nearest_obstacle_m/FT_TO_M:.0f}ft)" if nearest_obstacle_m else ""
        return "fail", (
            f"{deviation:.0f}deg turn: needs {r_needed_ft:.0f}ft, "
            f"only {available_r_ft:.0f}ft available{obstacle_note}"
        )


# ---------------------------------------------------------------------------
# Shapefile loaders
# ---------------------------------------------------------------------------


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _midpoint(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Midpoint of a polyline by arc-length."""
    if len(points) == 1:
        return points[0]
    total = 0.0
    segs: list[tuple[float, float, float]] = []  # (cumulative_dist, x, y)
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        d = math.hypot(x2 - x1, y2 - y1)
        total += d
        segs.append((total, x2, y2))

    half = total / 2.0
    prev_x, prev_y = points[0]
    prev_cum = 0.0
    for cum, x, y in segs:
        if cum >= half:
            t = (half - prev_cum) / (cum - prev_cum) if (cum - prev_cum) > 0 else 0.5
            return (prev_x + t * (x - prev_x), prev_y + t * (y - prev_y))
        prev_cum, prev_x, prev_y = cum, x, y
    return points[-1]


def _in_bbox(
    points: list[tuple[float, float]],
    bbox: tuple[float, float, float, float] | None,
) -> bool:
    if bbox is None:
        return True
    minx, miny, maxx, maxy = bbox
    return any(minx <= x <= maxx and miny <= y <= maxy for x, y in points)


def _load_pavement_scores(
    shp_path: Path,
) -> list[tuple[tuple[float, float], float, str]]:
    """
    Returns list of (midpoint_lonlat, score, label) for every pavement segment.
    """
    import shapefile  # pyshp

    out = []
    with shapefile.Reader(str(shp_path)) as sf:
        for shape, rec in zip(sf.iterShapes(), sf.iterRecords()):
            pts = shape.points
            if not pts:
                continue
            mid = _midpoint(pts)
            out.append((mid, float(rec["score"]), rec["label"]))
    return out


def _assign_pavement_scores(
    edges: list[Edge],
    pavement: list[tuple[tuple[float, float], float, str]],
    max_match_m: float = 50.0,
) -> None:
    """
    Mutate edges in-place: assign pci_score / pci_label by nearest-midpoint join.
    Only assigns if the nearest pavement midpoint is within max_match_m.
    """
    if not pavement:
        return

    # Pre-compute edge midpoints
    edge_mids: list[tuple[float, float]] = [_midpoint(e.points) for e in edges]

    # For each pavement segment, find the closest edge midpoint
    for (plon, plat), score, label in pavement:
        best_idx, best_d = -1, float("inf")
        for i, (elon, elat) in enumerate(edge_mids):
            d = _haversine_m(plon, plat, elon, elat)
            if d < best_d:
                best_d, best_idx = d, i
        if best_idx >= 0 and best_d <= max_match_m:
            e = edges[best_idx]
            # Keep the score that is closer (in case multiple pave segs map to one edge)
            if e.pci_score is None or best_d < _haversine_m(
                *_midpoint(e.points), *edge_mids[best_idx]
            ):
                e.pci_score = score
                e.pci_label = label


def build_from_shapefiles(
    centerline: Path = CENTERLINE_SHP,
    pavement: Path = PAVEMENT_SHP,
    bbox: tuple[float, float, float, float] | None = None,
    attach_pavement: bool = True,
    max_match_m: float = 50.0,
) -> Graph:
    """
    Build a Graph from the Somerville centerline shapefile.

    Args:
        centerline:     path to centerline .shp
        pavement:       path to pavement scores .shp
        bbox:           (minx, miny, maxx, maxy) to restrict edges loaded.
                        None = load entire city.
        attach_pavement: spatially join pavement scores onto edges.
        max_match_m:    max metres between segment midpoints to accept a join.
    """
    import shapefile  # pyshp

    g = Graph()
    all_edges: list[Edge] = []

    with shapefile.Reader(str(centerline)) as sf:
        for shape, rec in zip(sf.iterShapes(), sf.iterRecords()):
            pts = shape.points
            if not pts or len(pts) < 2:
                continue
            if not _in_bbox(pts, bbox):
                continue

            u_id = str(rec["u"])
            v_id = str(rec["v"])
            length_m = float(rec["length"]) if rec["length"] else _haversine_m(
                pts[0][0], pts[0][1], pts[-1][0], pts[-1][1]
            )
            oneway = bool(rec["oneway"])

            # Node positions: first point = u, last point = v
            if u_id not in g.nodes:
                g.add_node(Node(u_id, pts[0][0], pts[0][1]))
            if v_id not in g.nodes:
                g.add_node(Node(v_id, pts[-1][0], pts[-1][1]))

            edge = Edge(
                from_id=u_id,
                to_id=v_id,
                length_m=length_m,
                oneway=oneway,
                points=pts,
            )
            all_edges.append(edge)

    if attach_pavement and pavement.exists():
        pave_data = _load_pavement_scores(pavement)
        _assign_pavement_scores(all_edges, pave_data, max_match_m=max_match_m)
        scored = sum(1 for e in all_edges if e.pci_score is not None)
        logger.info("Pavement join: %d/%d edges scored", scored, len(all_edges))

    for edge in all_edges:
        g.add_edge(edge)

    logger.info(
        "Graph built: %d nodes, %d edges (bbox=%s)",
        len(g.nodes), len(all_edges), bbox,
    )
    return g


# ---------------------------------------------------------------------------
# GeoJSON export helpers
# ---------------------------------------------------------------------------


def graph_to_geojson(g: Graph) -> dict:
    """Export all edges as a GeoJSON FeatureCollection (for Leaflet display)."""
    features = []
    seen: set[tuple[str, str]] = set()
    for (a, b), edge in g.edge_meta.items():
        key = (min(a, b), max(a, b))
        if key in seen:
            continue
        seen.add(key)
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[x, y] for x, y in edge.points],
            },
            "properties": {
                "from": a,
                "to": b,
                "length_m": round(edge.length_m, 1),
                "pci_score": edge.pci_score,
                "pci_label": edge.pci_label,
                "oneway": edge.oneway,
                "color": _pci_color(edge.pci_score),
            },
        })

    # Intersection nodes
    isect_ids = set(g.intersection_nodes())
    node_features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [n.lon, n.lat]},
            "properties": {"id": n.id, "is_intersection": n.id in isect_ids},
        }
        for n in g.nodes.values()
    ]

    return {
        "type": "FeatureCollection",
        "features": features + node_features,
    }


def path_to_geojson(g: Graph, node_ids: list[str]) -> dict:
    """Convert a list of node IDs (route) to a GeoJSON LineString."""
    coords: list[list[float]] = []
    for a, b in zip(node_ids, node_ids[1:]):
        edge = g.edge_meta.get((a, b)) or g.edge_meta.get((b, a))
        if edge:
            seg = edge.points if edge.from_id == a else list(reversed(edge.points))
            if coords:
                seg = seg[1:]  # avoid duplicating shared node
            coords.extend([x, y] for x, y in seg)
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"nodes": node_ids, "hop_count": len(node_ids)},
    }


def _pci_color(score: float | None) -> str:
    if score is None:
        return "#aaaaaa"
    if score >= 85:
        return "#00B050"   # Good — green
    if score >= 70:
        return "#92D050"   # Satisfactory — light green
    if score >= 55:
        return "#FFFF00"   # Fair — yellow
    if score >= 40:
        return "#FFC000"   # Poor — orange
    if score >= 25:
        return "#FF0000"   # Very Poor — red
    return "#7030A0"       # Serious/Failed — purple


# ---------------------------------------------------------------------------
# __main__ demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json, sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # 1. Build full-city graph
    print("Loading Somerville graph from shapefiles ...")
    g = build_from_shapefiles()

    # Count unique undirected edges
    seen_edges: set[tuple[str, str]] = set()
    for (a, b) in g.edge_meta:
        seen_edges.add((min(a, b), max(a, b)))
    total = len(seen_edges)
    scored = sum(
        1 for (a, b) in seen_edges
        if g.edge_meta.get((a, b), g.edge_meta.get((b, a))).pci_score is not None
    )
    isects = g.intersection_nodes()

    print(f"  Nodes      : {len(g.nodes)}")
    print(f"  Edges      : {total}")
    print(f"  Scored     : {scored} ({100*scored//total if total else 0}%)")
    print(f"  Intersections (degree>=3): {len(isects)}")

    # 2. Demo route: from west Somerville to east Somerville
    #    Pick two nodes near known landmarks by coordinate
    start_node = g.nearest_node(-71.1200, 42.3960, min_degree=2)  # near Holland St / Broadway
    end_node   = g.nearest_node(-71.0800, 42.3820, min_degree=2)  # near McGrath Hwy / Washington

    print(f"\nStart node: {start_node}  ({g.nodes[start_node].lon:.4f}, {g.nodes[start_node].lat:.4f})")
    print(f"End   node: {end_node}   ({g.nodes[end_node].lon:.4f}, {g.nodes[end_node].lat:.4f})")

    # 3. Naive route (distance only)
    naive = g.dijkstra(start_node, end_node)
    print(f"\nNaive route : {len(naive) if naive else 'NO PATH'} hops")

    # 4. PCI-penalised route (prefer good pavement)
    pci_route = g.dijkstra(start_node, end_node, pci_penalty_factor=2.0)
    print(f"PCI route   : {len(pci_route) if pci_route else 'NO PATH'} hops")

    # 5. Demo blocked-intersection reroute
    #    Block the first real intersection along the naive route
    candidate_blocked = {n for n in (naive or []) if n in set(isects)}
    first_blocked = next(iter(candidate_blocked), None)
    if first_blocked:
        safe = g.dijkstra(start_node, end_node, blocked={first_blocked})
        print(f"\nBlocked {first_blocked[:8]}... -> safe route: {len(safe) if safe else 'NO PATH'} hops")
        print(f"Rerouted: {naive != safe}")

    # 6. Export a small bbox subgraph to GeoJSON for inspection
    sub = g.subgraph_bbox(-71.105, 42.390, -71.095, 42.398)
    geojson = graph_to_geojson(sub)
    out_path = Path(__file__).parent.parent / "data" / "somerville_graph_sample.geojson"
    with open(out_path, "w") as f:
        json.dump(geojson, f)
    print(f"\nSample GeoJSON written to {out_path}")
    print(f"  ({len([x for x in geojson['features'] if x['geometry']['type']=='LineString'])} edges, "
          f"{len([x for x in geojson['features'] if x['geometry']['type']=='Point'])} nodes)")
