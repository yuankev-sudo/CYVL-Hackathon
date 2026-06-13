# Feature Spec: 3D Maneuver Instructions for Challenging Intersections

## Goal
When a user clicks a flagged "challenging" intersection, open a 3D scene of
that real intersection (built from Cyvl geometry) and show a guided maneuver
for getting a large vehicle (fire truck / WB-67 / bus) through the hard turn:
the truck moving through the turn, its swept path painted on the ground with
problem zones in red, and synced step-by-step driving instructions.

This is an EXECUTABLE ACTION, not just a visualization: it tells an operator
HOW to drive the turn, which is what the judges asked for.

## Important scoping reality
The Autodesk APS Viewer is a 3D DISPLAY/INTERACTION engine — NOT a physics or
vehicle-dynamics simulator. "Simulate" here means: WE compute the truck motion
and swept path (in Python / shapely), and the Viewer PLAYS IT BACK as a visual
animation with instructions overlaid. The Viewer is the stage, not the physics.

The swept-path geometry and the truck's position/heading at each step come from
our own swept-path module. This spec covers DISPLAYING that result in the APS
Viewer. Do not try to run real Civil 3D / Vehicle Tracking analysis in the
cloud (Design Automation API) — too heavy for the time budget.

---

## BUILD IN TIERS. Lock each tier before starting the next.

### Tier 1 — MUST WORK (build this first, then stop and verify)
- Static 3D scene of ONE intersection loaded in the APS Viewer.
- The swept-path polygon (from our swept-path module) drawn as geometry on the
  ground plane.
- Any zone where the swept path crosses a curb or clips an asset highlighted
  in RED.
- A text panel beside the Viewer listing the step-by-step maneuver
  instructions.
This alone is a complete, compelling demo. Do not proceed until it renders
reliably.

### Tier 2 — IF TIME
- Animate the truck model stepping through the maneuver. Discrete snapshots
  (position 1 -> 2 -> 3 -> 4) are acceptable and much easier than smooth
  motion — they read clearly. Each snapshot syncs with its instruction step.

### Tier 3 — STRETCH
- Smooth continuous truck motion along the path.
- Camera follows the cab / cuts to an overhead view at the critical pinch point.
- Instructions narrate in sync with the animation.

---

## The KICKOFF FORK (decide in first ~90 min)
The riskiest part is getting our custom Cyvl geometry through OAuth -> OSS ->
Model Derivative -> APS Viewer in a format it accepts.

- IF the APS token -> translate -> view loop is GREEN within ~90 min:
  proceed with the APS Viewer as described here (on-brand Autodesk integration).
- IF it is still fighting us by ~hour 2:
  PIVOT — render this exact maneuver demo in plain Three.js instead (more
  forgiving), and satisfy the Autodesk requirement with the DXF/CAD export path
  instead (see docs/autodesk-integration.md). The maneuver UX is identical;
  only the rendering engine and the Autodesk "spend" location change.

Keep the maneuver logic (swept path, truck positions, instruction steps)
RENDERER-AGNOSTIC so it can drive either APS Viewer or Three.js without rewrite.

---

## APS Viewer pipeline (Tier 1 path)
1. Auth: register an APS app (Client ID + Secret). Stand up a minimal backend
   token endpoint that exchanges Client ID/Secret for a 2-legged OAuth access
   token. The Viewer fetches its token from THIS endpoint (never embed the
   secret in frontend).
2. Upload the intersection model to Autodesk Object Storage Service (OSS).
3. Call Model Derivative "Start Translation Job" to convert it to SVF2.
4. Initialize the Viewer in the frontend, load the translated model by its
   URN. The Viewer SDK JS MUST be loaded from the Autodesk-hosted URL (cannot
   self-host it).
NOTE: Model Derivative is a metered/"rated" API under the post-Dec-2025 APS
billing model. Confirm we have free-tier quota / a sponsored account before
relying on it. The Viewer SDK itself is free.

## Adding our own geometry + moving the truck (Viewer API)
- Draw the swept-path polygon and curb/lane lines as custom geometry overlaid
  on the scene (custom THREE meshes added to the Viewer's overlay scene, or
  Viewer scene-builder geometry). Color the failing segments red.
- Place/move the truck mesh by applying position + heading transforms to the
  object at each maneuver step (Tier 2/3).
- Use the Viewer camera API to move/animate the viewpoint (Tier 3).

---

## Data contract (the maneuver logic produces this; the renderer consumes it)
Keep this JSON shape so APS Viewer and Three.js are interchangeable:

```json
{
  "intersection_id": "som_highland_x_school",
  "vehicle": "fire_ladder_truck",
  "scene_model_urn": "<APS URN or local model path>",
  "curb_lines": [ [[x,y],[x,y], ...], ... ],
  "lane_edges": [ [[x,y],[x,y], ...], ... ],
  "swept_path": [[x,y],[x,y], ...],          // polygon of area the body sweeps
  "conflict_zones": [ [[x,y],[x,y], ...] ],  // sub-areas to paint RED
  "verdict": "tight",                         // pass | tight | fail
  "steps": [
    {
      "index": 1,
      "truck_pose": { "x": 0.0, "y": 0.0, "heading_deg": 90.0 },
      "instruction": "Pull forward to the far crosswalk before turning.",
      "camera": "overhead"                    // optional hint
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

The swept-path module fills swept_path, conflict_zones, verdict, and the
truck_pose at each step. The instruction text can be generated from the
geometry (e.g. detect which curb the path approaches) or templated per step.

---

## Definition of done (Tier 1)
- Click a flagged intersection -> 3D scene loads.
- Swept path visible on the ground, conflict zones red.
- Instruction steps listed beside the scene.
- Works reliably on at least 1 real Somerville intersection (2-3 is better).