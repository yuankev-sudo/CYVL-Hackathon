# ClearPath

Turn feasibility for large vehicles, powered by LiDAR geometry.

## Quick start

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload
# open http://localhost:8000
```

## Verify the shapely demo works end-to-end

```bash
python -m sweptpath.shapely_backend   # runs 4 vehicles × 3 intersections
python -m routing.graph               # runs demo route with FIRE-LADDER
```

## Environment variables (optional — Autodesk path)

```
APS_CLIENT_ID=...
APS_CLIENT_SECRET=...
```

If not set, the shapely fallback is used automatically.
