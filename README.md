# Superblocker

Superblocker is a street-network planning prototype. It loads the complete OpenStreetMap driving graph inside a selected place's bounding box, detects an arterial boundary network, builds closed road cells, and proposes access changes that prevent modeled vehicle paths from crossing between different entry sides of each generated cell.

The output is a network-topology proposal, not an implementation-ready traffic plan. It does not replace traffic counts, turning-movement data, emergency-service review, accessibility review, legal review, or field inspection.

![Budapest analysis overview](docs/screenshots/budapest-overview.png)

## How the analysis works

1. **Select an area.** Search uses OpenStreetMap Nominatim. The selected result supplies a rectangular bounding box.
2. **Load the road graph.** The backend downloads the drivable OSM network in that box and normalizes road attributes.
3. **Find boundary roads.** Configured arterial road classes are combined with graph-centrality results to form the cross-traffic network.
4. **Build road cells.** Closed rings in the arterial network are polygonized, filtered, and merged toward the configured target area.
5. **Assign entry sides.** Each generated cell gets four cardinal entry/return sectors: east, north, west, and south.
6. **Block cross-sector paths.** The constraint solver proposes modal filters, one-way changes, street cuts, and local two-way repairs. A connectivity-preserving directional-territory fallback is used when a cheaper cut plan would remove existing access.
7. **Validate the result.** Every generated cell is checked for directed paths between different entry sectors. Local reachability is checked separately and unresolved network nodes are reported.
8. **Render and inspect.** MapLibre displays OpenStreetMap raster tiles; deck.gl displays the road graph, cell polygons, boundary roads, entry points, access signs, and test routes.

All action markers use the same symbols in the map legend, hover tooltip, selected-cell details, and implementation schedule:

| Sign | Meaning |
| --- | --- |
| Blue line | Boundary road carrying cross-traffic between cells |
| Colored dot / `E N W S` | Entry point and required return side |
| Red `X` | Modal filter; motor through-traffic blocked |
| Blue `>` | One-way conversion in the shown direction |
| Teal `<>` | Two-way local-access repair |
| Amber `!` | Turn restriction |
| Purple `=` | Full motor-traffic closure / street cut |

Point signs are hidden at city scale and appear from street-level zoom to avoid covering the road network. The implementation schedule initially renders 50 actions and can load further batches without creating thousands of DOM elements at once.

![Budapest street-level actions](docs/screenshots/budapest-street-actions.png)

## Budapest run

Observed in the local app on 2026-08-31 with the default 12 ha target, 6 ha minimum, 20 ha maximum, and four entry sectors:

| Result | Value |
| --- | ---: |
| Selected extent | Nominatim bounding box for Budapest |
| Input graph | 32,734 nodes; 84,646 directed edges |
| Rendered road network | 48,886 line features; 6,547.64 km |
| Generated superblocks | 817 |
| Generated-cell coverage | 23.4% of the rectangular bounding-box area |
| Boundary-road OSM IDs | 18,552 |
| Street actions | 2,586 |
| Modal filters | 2,508 |
| One-way changes | 8 |
| Two-way local-access repairs | 33 |
| Street cuts | 37 |
| Directional validation | 817 / 817 generated cells passed |
| Local-access review | 32 network nodes |
| Processing time | 670.4 seconds on the test machine |

The 23.4% figure is not “Budapest completed.” It is the area of generated closed road cells divided by the rectangular search bounding-box area. The rectangle includes land outside the municipal boundary, the Danube, parks, rail areas, and other places where the current arterial polygonizer may not create a closed cell. The road graph is processed across the full rectangle, but the current algorithm does not produce a wall-to-wall municipal land partition.

## Current limitations

- The search result provides a bounding box, not the exact administrative polygon.
- Arterial classification is based on OSM road class and graph centrality, not measured traffic volume.
- “Directional validation passed” means no modeled vehicle path connects different entry sectors in a generated cell. It does not prove real-world compliance.
- Network nodes are reachability proxies, not a complete address or parcel database.
- City-scale geometry processing is CPU-heavy; the observed Budapest run took about 11 minutes.
- Every proposal requires transport-engineering and on-site review before implementation.

## Interface

The right-hand planner runs one workflow: **Analyze entire city**. Results include:

- directional-validation status and generated-cell coverage;
- the boundary-road network reserved for cross-traffic;
- counts for every access-change type;
- a street-by-street action schedule with street name and coordinates;
- a warning when local network nodes lose entry access;
- display toggles for entry directions and street actions;
- a route tester that sends inter-cell travel onto boundary roads.

The basemap defaults to OpenStreetMap's no-key raster endpoint. Set `VITE_OSM_TILE_URL` to use another OSM-compatible raster tile service in a deployment.

## Architecture

### Backend

- Python 3.12 and FastAPI
- OSMnx, NetworkX, GeoPandas, Shapely, and PyProj
- server-sent events for progress
- bounded analysis worker pool and per-client request limits
- atomic file-cache writes and bounded in-memory partition cache

### Frontend

- React 19, TypeScript, and Vite
- MapLibre for OpenStreetMap raster tiles
- deck.gl for road, polygon, marker, and route layers
- same-origin `/api` proxy in development and nginx production builds

## Local development

Prerequisites: Python 3.12, Node.js 22, and npm.

Backend:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Frontend, in a second terminal:

```bash
cd frontend
npm ci
cp .env.example .env
npm run dev
```

Open `http://localhost:5173`. API documentation is at `http://localhost:8000/docs`.

## Docker Compose

```bash
docker compose up --build
```

Open `http://localhost:5173`. The frontend nginx container proxies API and SSE traffic to the non-root backend container. The backend port is intentionally not published to the host.

## Configuration

Copy [`backend/.env.example`](backend/.env.example) and [`frontend/.env.example`](frontend/.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_URL` | `/api/v1` | Backend API base URL |
| `VITE_OSM_TILE_URL` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | Raster basemap template |
| `NOMINATIM_USER_AGENT` | project identifier | Identifies geocoder requests |
| `NOMINATIM_MIN_INTERVAL_SECONDS` | `1.0` | Aggregate geocoder throttle |
| `MAX_BBOX_SPAN_DEGREES` | `0.5` | Maximum latitude/longitude span |
| `MAX_BBOX_AREA_KM2` | `2500` | Maximum approximate bbox area |
| `ANALYSIS_MAX_WORKERS` | `2` | Graph-analysis worker count |
| `ANALYSIS_MAX_CONCURRENT_REQUESTS` | `2` | Concurrent expensive requests |
| `ANALYSIS_RATE_LIMIT_PER_MINUTE` | `6` | Per-client expensive-request limit |
| `PARTITION_CACHE_MAX_ENTRIES` | `8` | In-process partition cache size |
| `PARTITION_CACHE_TTL_SECONDS` | `3600` | Partition cache lifetime |
| `ADMIN_API_KEY` | unset | Enables authenticated cache maintenance |

## API

All application endpoints are under `/api/v1`:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/search` | Nominatim place search |
| `GET` | `/search/reverse` | Reverse geocoding |
| `POST` | `/network` | Download and classify a street network |
| `POST` | `/analyze` | Legacy candidate-analysis compatibility endpoint |
| `POST` | `/analyze/stream` | Legacy candidate analysis with SSE progress |
| `POST` | `/partition` | Generate a partition and road network |
| `POST` | `/partition/stream` | Generate a partition with SSE progress |
| `POST` | `/route` | Route with an optional supplied partition |
| `GET` | `/optimize/size` | Recommend a target size |
| `GET` | `/cache/stats` | Return non-sensitive cache statistics |
| `DELETE` | `/cache` | Clear cache entries; requires `X-Admin-Key` |
| `POST` | `/cache/cleanup` | Remove expired entries; requires `X-Admin-Key` |

`GET /health` is the unauthenticated health check.

## Quality checks

```bash
cd backend
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/pytest
.venv/bin/pip-audit -r requirements.txt

cd ../frontend
npm test -- --run
npm run lint
npm run build
npm audit
```

## License

GPL-3.0. See [LICENSE](LICENSE).
