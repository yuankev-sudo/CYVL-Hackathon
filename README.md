# ClearPath

> 🏆 **2nd Place — CYVL Hackathon** | $3,000 prize

Profile-aware routing for large vehicles, off **measured pavement reality** —
not a reported-restriction database. Pick a start and end on a map of
Somerville, MA (built from Cyvl LiDAR centerline + pavement data) and route
under one of three profiles:

- 🚗 **Fastest** — shortest distance on the real road network.
- 🚑 **Smoothest** — avoids rough pavement (low PCI). For ambulances / patient comfort.
- 🚛 **Large Vehicle** — blocks intersections whose **swept path can't clear the
  turn** for your vehicle, then reroutes around them. Enter the vehicle's
  dimensions (or pick an AASHTO preset); the turning radius is derived from the
  wheelbase and drives the per-turn feasibility check.

## Screenshots

![Main Web Page](CYVL-Hack/Main%20Web%20Page.png)

![LiDAR Demo Segment](CYVL-Hack/Lidar%20Demo%20Segment.png)

![LiDAR Point Cloud](CYVL-Hack/Lidar%20Point%20Cloud%20.png)

## Quick start

```bash
pip install -r requirements.txt      # includes pyshp for the shapefile network
uvicorn api.main:app --reload
# open http://localhost:8000
```

Then: pick a profile → click the map to drop **A** (start) and **B** (end) →
*Get Directions*. For the Large Vehicle profile, set the dimensions first.

## How it fits together

```
frontend/            Leaflet map: profile pills, A/B pins, truck dimension form
api/routes.py        /network · /route/dynamic · /turning-radius · /vehicles
routing/cyvl_graph.py  road graph from the Somerville centerline + pavement shapefiles
                       (PCI per edge, Dijkstra w/ blocks + PCI penalty, turn feasibility)
geometry/turning.py    vehicle dimensions -> turning radii (the swept-path inputs)
sweptpath/             shapely (always-works) + autodesk (APS) backends behind one interface
data/                  Cyvl shapefiles, vehicle templates, MVP-safe demo scenario
```

The road network is built once from the shapefiles in `data/` (~1535 nodes,
2160 edges, 890 PCI-scored). Turn feasibility uses `geometry/turning.py` to get
the vehicle's **outer swing radius** (wheelbase → centerline radius, plus width
+ front overhang) and compares it to the corner geometry at each intersection
along the naive route.

## Standalone module demos (no server, no network)

```bash
python -m geometry.turning            # dimensions -> turning radii for each preset
python -m sweptpath.shapely_backend   # swept-path verdicts, 4 vehicles × sample corners
python -m routing.cyvl_graph          # build the real network + a demo reroute
python -m routing.graph               # MVP-safe hardcoded demo corridor (offline fallback)
```

`data/scenario.py` + `routing/graph.py` are a self-contained, hardcoded
Somerville corridor (Star Market ↔ Market Basket) kept as a guaranteed-offline
fallback; the live app routes on the full `routing/cyvl_graph.py` network.

## Environment variables (all optional)

```
CYVL_API_KEY=...        # enables the /cyvl/* live data overlays (REST passthrough)
APS_CLIENT_ID=...       # Autodesk Platform Services swept-path backend
APS_CLIENT_SECRET=...   # if unset, the shapely backend is used automatically
```

The map + routing work with **zero** credentials — the network and pavement
come from the bundled shapefiles.
