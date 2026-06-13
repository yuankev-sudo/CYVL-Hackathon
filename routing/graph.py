"""
Build a route graph from intersections and find paths that avoid FAIL turns.
Simple adjacency-based graph — nodes are intersections, edges are road segments.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from collections import defaultdict
import heapq

from sweptpath.interface import Verdict, SweptPathResult


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

    def add_edge(self, from_id: str, to_id: str, weight: float = 1.0):
        self.edges[from_id].append((to_id, weight))
        self.edges[to_id].append((from_id, weight))

    def dijkstra(self, start: str, end: str, blocked: set[str]) -> list[str] | None:
        """Return shortest node-id path from start to end, skipping blocked nodes."""
        dist = {start: 0.0}
        prev: dict[str, str | None] = {start: None}
        pq = [(0.0, start)]

        while pq:
            d, u = heapq.heappop(pq)
            if u == end:
                path = []
                while u is not None:
                    path.append(u)
                    u = prev[u]
                return list(reversed(path))
            if d > dist.get(u, float("inf")):
                continue
            for v, w in self.edges[u]:
                if v in blocked:
                    continue
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        return None


def build_demo_graph() -> Graph:
    """Hardcoded demo graph matching the three sample intersections."""
    g = Graph()
    g.add_node(Node("intersection-001", -122.4192, 37.7751))
    g.add_node(Node("intersection-002", -122.4177, 37.7748))
    g.add_node(Node("intersection-003", -122.4162, 37.7743))
    g.add_node(Node("start",            -122.4200, 37.7755))
    g.add_node(Node("end",              -122.4150, 37.7738))

    g.add_edge("start",            "intersection-001", 1.0)
    g.add_edge("intersection-001", "intersection-002", 1.0)
    g.add_edge("intersection-002", "intersection-003", 1.0)
    g.add_edge("intersection-003", "end",              1.0)
    # alternate route bypassing intersection-001
    g.add_edge("start",            "intersection-002", 1.8)
    return g


def route(
    graph: Graph,
    start: str,
    end: str,
    feasibility: list[SweptPathResult],
) -> dict:
    """Return naive route and (if needed) a rerouted alternative."""
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
        print(f"{r.intersection_id}: {r.verdict.value}  {r.reason or ''}")

    graph = build_demo_graph()
    result = route(graph, "start", "end", feasibility)
    print("\nNaive route:", result["naive_route"])
    print("Safe  route:", result["safe_route"])
    print("Rerouted?  ", result["rerouted"])
