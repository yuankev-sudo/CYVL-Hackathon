# Build Spec: 3D Intersection Maneuver Viewer (MVP FIRST)

## Context
We have Autodesk's `aps-simple-viewer-nodejs` sample WORKING (auth + token
endpoint + viewer render all confirmed). We are extending THIS sample into our
real app. Do NOT rebuild auth or the token flow — reuse the sample's existing
working code untouched.

## What we're building
For a flagged "challenging" corner, render a 3D view of the intersection
(reconstructed from geometry, NOT a translated point cloud), draw the vehicle's
swept path with conflict zones in red, animate a truck through the turn, and
show synced step-by-step driving instructions.

## CRITICAL: STOP AFTER THE MVP. DO NOT BUILD EVERYTHING AT ONCE.
Build ONLY the MVP described below, using ONE hardcoded sample intersection.
Then STOP and let me visually check the intersection rendering before you build
animation, instructions, multi-corner support, or real data wiring. If the
intersection looks wrong, we fix that first — everything else depends on it.

---

## KEY ARCHITECTURE DECISION: draw geometry, don't translate point clouds
Do NOT upload/translate a Cyvl point cloud through Model Derivative. Instead,
DRAW the intersection directly in the viewer's underlying THREE.js scene from
coordinate geometry:
- curb lines + lane edges -> extruded into simple 3D geometry
- road surface -> a flat polygon
- swept path -> a translucent polygon on the ground
- truck -> a box mesh sized to real vehicle dimensions

This is lighter, fully under our control, and animatable. The "3D
reconstruction of the intersection" = extruded curb/lane geometry, not a dot
cloud.

The viewer exposes a THREE.js scene; add custom meshes to its overlay scene.
If adding meshes to the APS viewer specifically fights you, the identical
THREE.js drawing code works in a plain THREE.js canvas as a fallback — but try
it in the APS viewer first since APS is working.

---

## ============ MVP SCOPE (BUILD THIS ONLY, THEN STOP) ============

Use ONE hardcoded sample intersection. Hardcode realistic geometry — a simple
4-way or T intersection is fine. No Cyvl API, no swept-path module, no
animation yet.

MVP must show:
1. A 3D scene in the viewer showing the intersection reconstructed from
   geometry:
   - curb lines extruded as low 3D edges
   - lane edges / road surface as a flat polygon
   - it should clearly read as an intersection from a 3/4 overhead camera angle
2. A swept-path polygon drawn on the ground (hardcoded sample polygon).
3. Any conflict zone (where swept path crosses a curb) drawn in RED.
4. A static truck box mesh (sized to a fire ladder truck ~ 12m long x 2.5m
   wide) placed at the start of the turn.

NO animation, NO instruction panel, NO corner picker, NO real data in the MVP.
Just: does the intersection + swept path + truck render and look correct.

### After MVP: STOP and output
- A screenshot-ready rendered scene I can look at.
- A short note on what's hardcoded and where the real data will plug in.

Wait for my go-ahead before continuing.

---

## ============ PHASE 2 (ONLY after I approve the MVP) ============

Once the intersection rendering is approved, build the rest:

5. Truck animation: move the truck box through the turn along a sequence of
   poses. Start with discrete "snap" between poses (easy), then interpolate
   for smooth motion (requestAnimationFrame lerp between poses).
6. Instruction panel: side panel showing step-by-step driving instructions,
   highlighting the current step as the truck reaches each pose.
   (e.g. "Pull forward to the far crosswalk", "Swing wide to clear the NE
   curb — rear wheels track ~4 ft inside the cab".)
7. Corner picker: a list/map of flagged corners; clicking one fetches
   /api/corner/:id and renders that corner's scene with the same code.
8. Optional: camera moves to the pinch point at the critical step.

---

## ============ PHASE 3 (real data) ============

9. Backend route GET /api/corner/:id returns the JSON contract below. Until the
   Cyvl + swept-path module is ready, keep it returning hardcoded sample
   corners so the frontend works independently.
10. Swap the stub for real data: the swept-path module pulls curb/lane/asset
    geometry from the Cyvl API (spatially filtered to the intersection), computes
    swept_path, conflict_zones, verdict, and truck poses, and emits the contract.

---

## Data contract (frontend consumes this; keep it stable across all phases)
```json
{
  "intersection_id": "som_highland_x_school",
  "vehicle": "fire_ladder_truck",
  "curb_lines": [ [[x,y],[x,y], "..."], "..." ],
  "lane_edges": [ [[x,y],[x,y], "..."], "..." ],
  "road_polygon": [[x,y],[x,y], "..."],
  "swept_path": [[x,y],[x,y], "..."],
  "conflict_zones": [ [[x,y],[x,y], "..."] ],
  "verdict": "tight",
  "steps": [
    {
      "index": 1,
      "truck_pose": { "x": 0.0, "y": 0.0, "heading_deg": 90.0 },
      "instruction": "Pull forward to the far crosswalk before turning.",
      "camera": "overhead"
    },
    {
      "index": 2,
      "truck_pose": { "x": 5.2, "y": 1.1, "heading_deg": 70.0 },
      "instruction": "Swing wide to clear the NE curb. Rear wheels track ~4 ft inside the cab.",
      "camera": "follow_cab"
    }
  ]
}
```
Coordinates are in meters in a local intersection frame (origin at intersection
center). The MVP can hardcode an instance of this and ignore `steps`,
`instruction`, and `camera` until Phase 2.

## Vehicle dimensions (use real values, don't invent)
- Fire ladder truck: ~12 m long, ~2.5 m wide
- WB-67 semi: ~22 m long, ~2.6 m wide
- 40 ft transit bus: ~12 m long, ~2.6 m wide

## Definition of done (MVP only)
Click/load -> 3D scene shows a recognizable intersection (extruded curbs + road),
a swept-path polygon on the ground with conflict zones in red, and a correctly
sized truck box at the turn entry. Renders reliably. Then STOP for review.