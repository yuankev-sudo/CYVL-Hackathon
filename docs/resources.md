# Cyvl Hackathon — Resources & Data Access

Context for what we can use and what we have access to. Reference this when
deciding what data to pull and which tools to lean on.

Event: June 13, 2026 · Somerville, MA · Cyvl HQ (76 School Street)
We build on **Somerville** Cyvl data (possibly Boston too), optionally paired
with real external public data.

---

## Hard rules (judges enforce these)

- **Real data only.** The build must come from real Cyvl Somerville data,
  optionally paired with real external sources. **Mock data scores lower** —
  judges mark it down. Using real info is the whole point.
- **Check for prior art first.** Before committing to a direction, search for
  existing solutions. Reframing a known solution as novel is the most common
  mistake judges penalize.
- **Sponsor tools must be genuinely useful, not bolted on.** Use one or two
  (not all three required). Integration has to serve the idea.
- **Keep the team API key private.** Never commit it to a public repo.

---

## What we have access to

### Cyvl data (Somerville)
- **LiDAR point clouds** — full Somerville cloud is ~500 GB. DO NOT download
  the whole thing. Pull sections per area from the platform to match the
  project. (Full cloud only on direct request to Cyvl staff.)
- **Digital twin** — geometry + asset attributes, layered view of geometry
  over the real world.
- **Detected assets** — signs, road markings/striping, pavement features,
  above-ground assets.
- **CV model inference** — pavement condition, signs, markings.
- **Imagery** — street-level capture across the whole city.

Important limit: **it is a snapshot, not a live feed.** No real-time updates.
Any idea depending on real-time data is out.

### Sponsor tools (use 1-2, meaningfully)
- **NVIDIA** — train our own model on Cyvl data (e.g. a CV model to detect an
  asset, score pavement, classify imagery, or anything fitting the idea).
- **Autodesk** — turn Cyvl capture data into CAD / 3D design output.
- **Ask Boston (askboston.ai)** — Cyvl infrastructure intelligence layered
  with Boston Open Data: 311 requests, Vision Zero crashes, sidewalks,
  streetlights. (Boston, not Somerville — note the geography.)

### AI tooling
- **Claude Code + Cyvl MCP** — once connected, Claude Code queries Cyvl data
  directly; give it a specific question ("pavement condition on Main St?") and
  it writes + runs the code. Specific questions beat vague ones.
- **Cyvl MCP** — for AI agent integration (Claude Desktop, Cursor, Claude Code).
- Web Claude / ChatGPT — for exploring ideas + GIS concepts, not the build.

### GIS / point cloud tools
- **QGIS** (qgis.org) — free GIS desktop; visualize the dataset, overlay
  government layers, do spatial analysis without code.
- **CloudCompare** (cloudcompare.org) — open-source 3D viewer for .las/.laz.

---

## Cyvl Data API

Base docs: https://i3.cyvl.app/docs  (OpenAPI 3.1)
Auth: team API key (private). Spatial filters supported. Exports in multiple
formats. GeoJSON Feature/FeatureCollection schemas available.

### Endpoints by category

**Infrastructure Query**
- `GET /api/v1/infrastructure/query` — spatial infrastructure query (the
  general-purpose spatial entry point)

**Projects**
- `GET /api/v1/projects` — list projects (find the Somerville project)

**Pavement** (this is Cyvl's core strength — condition data)
- `GET /api/v1/pavement/scores` — list pavement scores
- `GET /api/v1/pavement/scores/{inspect_id}` — score detail
- `GET /api/v1/pavement/segments` — pavement segments
- `GET /api/v1/pavement/distresses` — distresses (cracks etc.)
- `GET /api/v1/pavement/cells` — inspection cells
- `GET /api/v1/pavement/pci-distribution` — PCI distribution
- `GET /api/v1/pavement/distress-breakdown` — distress breakdown

**Signs**
- `GET /api/v1/signs` — list signs
- `GET /api/v1/signs/statistics` — sign statistics
- `GET /api/v1/signs/{sign_id}` — single sign

**Above-Ground Assets** (poles, hydrants, basins, trees, etc.)
- `GET /api/v1/assets` — list assets
- `GET /api/v1/assets/statistics` — asset statistics
- `GET /api/v1/assets/inventory` — asset inventory
- `GET /api/v1/assets/detail/{asset_id}` — asset detail
- `GET /api/v1/assets/{asset_id}` — single asset
- `GET /api/v1/assets/{asset_id}/imagery` — asset imagery
- `GET /api/v1/assets/{asset_id}/history` — asset history (change over time)

**Markings** (striping, lane lines, crosswalks)
- `GET /api/v1/markings` — list markings
- `GET /api/v1/markings/statistics` — marking statistics
- `GET /api/v1/markings/{marking_id}` — single marking

**Reference Data** (lookups — useful for decoding codes)
- `GET /api/v1/reference/distress-types`
- `GET /api/v1/reference/asset-types`
- `GET /api/v1/reference/sign-categories`
- `GET /api/v1/reference/mutcd-codes` — MUTCD sign codes
- `GET /api/v1/reference/line-types` — line type/subtype lookups

**Image Search** (semantic / embedding search over imagery — powerful, often overlooked)
- `POST /api/v1/embeddings/query` — search images by text
- `POST /api/v1/embeddings/query_image` — search images by image
- `GET  /api/v1/embeddings/results/{search_id}` — search results page
- `POST /api/v1/embeddings/browse` — browse images
- `GET  /api/v1/embeddings/browse/{browse_id}` — browse page
- `GET  /api/v1/embeddings/projects` — list embedded projects
- `GET  /api/v1/embeddings/collections` — list image collections

**Health**
- `GET /health`, `GET /ready`

### Useful schemas
GeoJSONFeature / GeoJSONFeatureCollection / GeoJSONGeometry (spatial output),
PavementScoreDetailResponse, PCIDistributionResponse, DistressBreakdownResponse,
AssetInventoryResponse, AssetHistoryResult, AssetImageryResult,
ImageSearchResponse, Severity (string enum), AssetType (string enum).

---

## External data we can layer on (integration is on us)
- **MassGIS** — https://www.mass.gov/massgis-data-layers (state GIS layers)
- **Somerville open data portal**
- **USGS National Map** — elevation, hydrography
- **OpenStreetMap** — road network, land use
- **Ask Boston / Boston Open Data** — 311, Vision Zero crashes, sidewalks,
  streetlights (Boston geography)

---

## Quick read: what's strong vs. missing in this dataset

**Strong (lean on these):**
- Pavement condition (scores, distresses, PCI) — the deepest data layer.
- Asset inventory with imagery AND history (history = change over time).
- Signs + markings with MUTCD/line-type reference lookups (compliance angles).
- Semantic image search over street imagery (search the physical world).
- Point cloud geometry for any 3D / clearance / spatial-measurement idea.

**Missing / constrained (design around these):**
- No real-time feed — snapshot only.
- Underground utilities (sewer/storm) are NOT in the API — would need external
  GIS or inference from surface catch-basin assets.
- External data integration is entirely on us.

---

## Platform / access notes
- Accept the email invite → you're in the Cyvl Hackathon org on cyvl.app.
- Verification code may land in spam.
- Platform: https://cyvl.app/projects?chatModal=open
- Reference build for inspiration: https://phi-cyvl.github.io/
- Downloads: pull sections per area from the platform, not the whole cloud.
- Support: Discord #questions, or on-site Cyvl engineers 9:30am-5:00pm.