# CLAUDE.md — Project context for Claude Code

## What we're building
"ClearPath" — a turn-feasibility tool for large vehicles. Given
intersection geometry derived from LiDAR point clouds (from Cyvl),
determine whether a specific vehicle (fire truck, WB-67 semi, transit
bus) can physically complete each turn in ONE pass by computing its
swept path. Output per intersection: pass / tight / fail, the reason
for any fail, and a routed alternative that avoids failing turns.
This is also for a cyvl hackthon and they really want us to integrate autodesk.

Positioning: this is the "physical feasibility layer" that existing
truck routers (Google, Waze, Route4Me) lack — they route off reported
restriction databases; we route off measured reality from LiDAR. 
We have Lidar data for road condition, trees, clearance etc.

This is a 6-hour hackathon MVP. Optimize for a working demo, not
production polish.

## Sponsor integrations (judges care about these)
- Autodesk: swept-path / turning analysis via Vehicle Tracking (Civil
  3D) / Autodesk Platform Services. This is the engineering core, not
  decoration — it computes whether the vehicle's swept path fits the
  available pavement.
- NVIDIA: train/run the model that extracts intersection geometry
  (curb lines, lane edges, encroaching objects) from the point cloud.

## Tech stack
- Backend: Python 3.11+, FastAPI
- Geometry: shapely, numpy; point clouds via laspy/open3d
- Frontend: single-page Leaflet (or deck.gl) map + vanilla JS
- Data: GeoJSON for intersection geometry; JSON for vehicle templates

## Architecture (directories)
- /data       loaders: point cloud, intersection GeoJSON, vehicle templates
- /geometry   extract curb lines + lane edges from point cloud
- /sweptpath  TWO backends behind ONE interface:
                autodesk_backend.py (APS / Vehicle Tracking)
                shapely_backend.py  (Python geometric fallback)
- /routing    graph route that excludes "fail" intersections
- /frontend   the map UI + work-order card
- /api        FastAPI serving feasibility + routes

## CRITICAL RULE — the fallback must always work
shapely_backend.py must stay fully functional end-to-end on 2-3
hardcoded sample intersections AT ALL TIMES. The Autodesk and LiDAR-
extraction paths are upgrades layered on top. We must never be in a
state where the demo can't run. If you change shared interfaces,
verify the shapely demo still runs before committing.

## Vehicle templates
Use standard AASHTO design vehicles (real dimensions + turning radii):
WB-67 tractor-trailer, SU-30 single-unit truck, fire ladder apparatus,
40ft transit bus. Do not invent dimensions.

## The demo we're building toward
One real, tight intersection. Show a naive router sending a fire truck
into a turn its body can't clear (swept path crosses the curb / clips
an object), then our tool flags "can't make this turn in one pass" and
reroutes. That before/after frame is the whole pitch — build toward it.

## Coding conventions
- Keep functions small and testable; prefer pure functions for geometry.
- Every module runnable/inspectable on its own with a __main__ demo block.
- Print intermediate visualizations when working on geometry so we can
  eyeball correctness fast.
- Don't add dependencies without noting them in the README run steps.