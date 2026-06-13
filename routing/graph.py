"""
Profile-aware routing over the demo corridor.

Three routing profiles, each a different edge-cost function over the same graph:

  fastest      — minimize distance (proxy for travel time).
  smoothest    — minimize distance penalized by pavement roughness (low PCI).
                 This is the "ambulance" profile: avoid potholes for the patient.
  largevehicle — HARD-block any intersection whose swept path overruns the curb
                 for the given vehicle, and penalize "tight" turns. This is the
                 physical-feasibility profile that ordinary routers lack.

`plan_route` returns both the naive fastest route (the "before") and the
profile route (the "after") so the frontend can show the reroute.
"""
from __future__ import annotations
import heapq
from collections import defaultdict

from data.scenario import Scenario, build_scenario
from sweptpath.interface import Verdict, SweptPathResult
from sweptpath.autodesk_backend import compute_swept_path

# smoothness: pavement below this PCI starts to hurt; weight the deficit hard.
SMOOTH_K = 3.0
SMOOTH_PCI_TARGET = 70.0
# largevehicle: extra "virtual distance" for routing through a tight corner.
TIGHT_TURN_PENALTY_M = 250.0
DEFAULT_SPEED_MPS = 8.94  # ~20 mph surface streets


def _roughness(pci: float | None) -> float:
    pci = 75.0 if pci is None else pci
    return max(0.0, (SMOOTH_PCI_TARGET - pci)) / SMOOTH_PCI_TARGET


def _feasibility(scenario: Scenario, vehicle: dict) -> dict[str, SweptPathResult]:
    """Swept-path verdict for every intersection node (origins/dests skipped)."""
    out: dict[str, SweptPathResult] = {}
    for nid, geom in scenario.geometry.items():
        if scenario.nodes[nid]["kind"] != "intersection":
            continue
        out[nid] = compute_swept_path(geom, vehicle)
    return out


def _edge_weight(profile: str, length_m: float, pci: float,
                 node_b_penalty: float) -> float:
    if profile == "smoothest":
        return length_m * (1.0 + SMOOTH_K * _roughness(pci))
    if profile == "largevehicle":
        return length_m + node_b_penalty
    return length_m  # fastest


def _dijkstra(scenario: Scenario, start: str, end: str, profile: str,
              blocked: set[str], tight: set[str]) -> list[str] | None:
    adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for e in scenario.edges:
        for a, b in ((e.a, e.b), (e.b, e.a)):
            if b in blocked:
                continue
            penalty = TIGHT_TURN_PENALTY_M if (profile == "largevehicle" and b in tight) else 0.0
            adj[a].append((b, _edge_weight(profile, e.length_m, e.pci, penalty)))

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
        for v, w in adj[u]:
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    return None


def _edge_lookup(scenario: Scenario) -> dict[tuple[str, str], object]:
    lut = {}
    for e in scenario.edges:
        lut[(e.a, e.b)] = e
        lut[(e.b, e.a)] = e
    return lut


def _metrics(scenario: Scenario, path: list[str] | None) -> dict:
    if not path:
        return {"distance_m": None, "eta_min": None, "avg_pci": None}
    lut = _edge_lookup(scenario)
    dist = num = den = 0.0
    for a, b in zip(path, path[1:]):
        e = lut[(a, b)]
        dist += e.length_m
        num += e.pci * e.length_m
        den += e.length_m
    return {
        "distance_m": round(dist),
        "eta_min": round(dist / DEFAULT_SPEED_MPS / 60.0, 1),
        "avg_pci": round(num / den, 1) if den else None,
    }


def plan_route(scenario: Scenario, start: str, end: str, profile: str,
               vehicle: dict | None = None) -> dict:
    """Plan a route under `profile`. Returns naive + chosen routes and metrics."""
    blocked: set[str] = set()
    tight: set[str] = set()
    feasibility_payload = None

    if profile == "largevehicle":
        if not vehicle:
            raise ValueError("largevehicle profile requires vehicle dimensions")
        feas = _feasibility(scenario, vehicle)
        blocked = {nid for nid, r in feas.items() if r.verdict == Verdict.FAIL}
        tight = {nid for nid, r in feas.items() if r.verdict == Verdict.TIGHT}
        feasibility_payload = {
            nid: {
                "verdict": r.verdict.value,
                "reason": r.reason,
                "clearance_margin_ft": r.clearance_margin_ft,
                "swept_polygon": r.swept_polygon,
            } for nid, r in feas.items()
        }

    # "Before": a naive fastest route that ignores feasibility/comfort.
    naive = _dijkstra(scenario, start, end, "fastest", blocked=set(), tight=set())
    # "After": the route under the requested profile (with constraints applied).
    chosen = _dijkstra(scenario, start, end, profile, blocked=blocked, tight=tight)

    blocked_on_naive = sorted(blocked.intersection(naive or []))

    return {
        "profile": profile,
        "start": start,
        "end": end,
        "naive_route": naive,
        "route": chosen,
        "rerouted": naive != chosen,
        "blocked_intersections": sorted(blocked),
        "blocked_on_naive_route": blocked_on_naive,
        "tight_intersections": sorted(tight),
        "feasibility": feasibility_payload,
        "metrics": _metrics(scenario, chosen),
        "naive_metrics": _metrics(scenario, naive),
    }


if __name__ == "__main__":
    from data.loaders import load_vehicle_templates

    sc = build_scenario(live=False)
    vehicles = load_vehicle_templates()
    wb67 = dict(vehicles["WB-67"], id="WB-67")

    for profile, veh in [("fastest", None), ("smoothest", None), ("largevehicle", wb67)]:
        r = plan_route(sc, "star_market", "market_basket", profile, veh)
        m = r["metrics"]
        print(f"\n[{profile}]  ->  {r['route']}")
        print(f"   dist={m['distance_m']}m  eta={m['eta_min']}min  avg_pci={m['avg_pci']}  "
              f"rerouted={r['rerouted']}  blocked={r['blocked_intersections']}")
