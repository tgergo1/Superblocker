# Superblocker

Superblocker is an automated citywide street-network planner. It turns a complete OpenStreetMap driving network into non-overlapping superblocks, proposes the access changes required to remove interior cross-traffic, and keeps travel between superblocks on the arterial boundary network.

![Superblocker main interface](docs/screenshots/main-interface.png)

> [!IMPORTANT]
> The output is an algorithmic planning proposal derived from OpenStreetMap topology. Every plan still requires transport-engineering, emergency-access, accessibility, and on-site validation before implementation.

## What it does

- Searches for a city or place using a throttled and cached Nominatim client.
- Downloads and analyzes the complete OpenStreetMap driving network inside the selected boundary.
- Identifies the arterial grid that becomes the cross-traffic and inter-superblock network.
- Polygonizes that grid into a complete city partition and optimizes cells toward the selected size range.
- Assigns entries to four cardinal entry/return sectors and eliminates every directed path between different sectors.
- Uses minimum-cost modal filters, street cuts, and one-way changes first; if those would lose existing access, a connectivity-preserving directional-territory plan is used instead.
- Re-validates every superblock and exposes the exact boundary roads, entry directions, access modifications, coverage, and any remaining local-access review items.
- Tests trips against the finished plan with cross-superblock travel forced onto boundary roads.
- Streams progress throughout the long-running city analysis.

![Full partition mode](docs/screenshots/full-partition-mode.png)

## Architecture

The backend is a Python 3.12 FastAPI application using OSMnx, NetworkX, GeoPandas, Shapely, and PyProj. Expensive OSM and graph work runs through a bounded worker pool with per-client request limits. File-cache writes are atomic, and the optional in-memory partition cache is bounded by size and TTL; route correctness does not depend on that cache because a partition can be included in every route request.

The frontend is React 19 and TypeScript, built by Vite. MapLibre renders an OpenStreetMap raster basemap with no API key, while deck.gl renders the street network, city partition, boundary-road network, directional entries, and access changes. Set `VITE_OSM_TILE_URL` to use another OSM-compatible tile endpoint in a deployment. The development server and production nginx container both proxy `/api` to the backend, so no public backend URL is baked into the production bundle.

## Local development

Prerequisites:

- Python 3.12
- Node.js 22 and npm

Start the backend:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Start the frontend in a second terminal:

```bash
cd frontend
npm ci
cp .env.example .env
npm run dev
```

Open `http://localhost:5173`. FastAPI's interactive API documentation is at `http://localhost:8000/docs`.

## Docker Compose

```bash
docker compose up --build
```

Open `http://localhost:5173`. The frontend nginx service proxies API and SSE traffic to the non-root backend container. Both services have health checks, and OSM/application caches use named volumes with non-root-compatible paths.

The Compose backend port is intentionally not published to the host; nginx is its trusted proxy and preserves client addresses for per-client workload limits. Use the local-development setup when you need direct access to port 8000.

To override backend settings, add an `environment` entry or `env_file` to the backend service. Set a long random `ADMIN_API_KEY` before using cache-maintenance endpoints.

## Usage

1. Enter a city or district name and submit the search.
2. Select the correct boundary.
3. Choose **Analyze entire city**. The road download, arterial detection, partitioning, access design, and validation run as one workflow.
4. Inspect the highlighted blue boundary-road network, directional entry/return signs, street-action markers and schedule, validation status, and coverage.
5. Optionally test an origin-destination pair; the planned route will respect superblocks and use boundary roads between them.

Analysis deliberately rejects very large bounding boxes. Tune `MAX_BBOX_SPAN_DEGREES` and `MAX_BBOX_AREA_KM2` only after considering OSM download size and graph-processing cost.

## API

All application endpoints are under `/api/v1`:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/search` | Forward place search |
| `GET` | `/search/reverse` | Reverse geocoding |
| `POST` | `/network` | Download and classify a street network |
| `GET` | `/network/bbox` | Query-parameter alternative to `/network` |
| `POST` | `/analyze` | Legacy candidate-analysis compatibility endpoint |
| `POST` | `/analyze/stream` | Legacy candidate analysis with SSE progress |
| `POST` | `/partition` | Generate a city-wide partition and its network |
| `POST` | `/partition/stream` | Generate a partition with SSE progress |
| `POST` | `/route` | Route with an optional supplied partition |
| `GET` | `/optimize/size` | Recommend a target size from grid properties |
| `GET` | `/cache/stats` | Return non-sensitive cache statistics |
| `DELETE` | `/cache` | Clear cache entries; requires `X-Admin-Key` |
| `POST` | `/cache/cleanup` | Remove expired entries; requires `X-Admin-Key` |

`GET /health` is the unauthenticated health check. OpenAPI schemas are available at `/openapi.json` and `/docs`.

## Configuration

Copy [`backend/.env.example`](backend/.env.example) and [`frontend/.env.example`](frontend/.env.example) for the complete defaults. Important backend settings include:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CORS_ORIGINS` | local frontend origins | Explicit allowed browser origins |
| `NOMINATIM_USER_AGENT` | project identifier | Required identifying user agent |
| `NOMINATIM_MIN_INTERVAL_SECONDS` | `1.0` | Aggregate geocoder request throttle |
| `MAX_BBOX_SPAN_DEGREES` | `0.5` | Maximum latitude/longitude span |
| `MAX_BBOX_AREA_KM2` | `2500` | Maximum approximate physical area |
| `ANALYSIS_MAX_WORKERS` | `2` | Bounded graph-analysis worker count |
| `ANALYSIS_MAX_CONCURRENT_REQUESTS` | `2` | Simultaneous expensive request limit |
| `ANALYSIS_RATE_LIMIT_PER_MINUTE` | `6` | Per-client expensive request budget |
| `PARTITION_CACHE_MAX_ENTRIES` | `8` | In-process partition optimization cache size |
| `PARTITION_CACHE_TTL_SECONDS` | `3600` | Partition optimization cache lifetime |
| `ADMIN_API_KEY` | unset | Enables authenticated cache maintenance |
| `CACHE_*_TTL_SECONDS` | varies | File-cache lifetimes by data type |

The frontend defaults to same-origin `/api/v1` and OpenStreetMap's no-key raster tiles. Set `VITE_API_URL` only when the API is intentionally hosted on a different origin. Set `VITE_OSM_TILE_URL` when a deployment uses its own OSM-compatible tile service.

## Quality checks

Backend:

```bash
cd backend
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/pytest --cov=app --cov-report=term-missing
.venv/bin/pip-audit -r requirements.txt
```

Frontend:

```bash
cd frontend
npm test
npm run lint
npm run build
npm audit
```

The CI workflow runs these checks and builds both production containers. Dependabot tracks Python, npm, and GitHub Actions dependencies.

## Project layout

```text
Superblocker/
├── backend/
│   ├── app/api/routes/       # FastAPI HTTP and SSE endpoints
│   ├── app/core/             # Settings and workload protection
│   ├── app/models/           # Validated request/response models
│   ├── app/services/         # OSM, detection, partitioning, routing, cache
│   ├── app/utils/            # Geospatial helpers
│   └── tests/                # Unit, integration, routing, and API tests
├── frontend/
│   ├── src/components/       # Search, map, controls, routing UI
│   ├── src/hooks/            # Abortable data-loading hooks
│   ├── src/services/         # HTTP and SSE client
│   └── src/types/            # Shared TypeScript domain types
├── docs/screenshots/
├── .github/workflows/ci.yml
└── docker-compose.yml
```

## License

Licensed under GPL-3.0. See [LICENSE](LICENSE).
