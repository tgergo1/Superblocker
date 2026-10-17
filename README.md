# Superblocker

Superblocker is a street-network planning prototype. It loads the OpenStreetMap driving graph inside a selected place's administrative polygon, detects a boundary-road network, builds closed road cells, and proposes access changes that prevent modeled vehicle paths from crossing between different entry sides of each generated cell.

Every result carries machine-readable evidence provenance and a release gate. A model run cannot be labeled `implementation_ready` until it uses an exact boundary, matched measured traffic observations, a complete authoritative access-target dataset, and recorded transport-engineering and on-site reviews.

![Budapest analysis overview](docs/screenshots/budapest-overview.png)

## How the analysis works

1. **Select an area.** Search asks OpenStreetMap Nominatim for the result's GeoJSON. Polygon and multipolygon administrative boundaries are validated and propagated into the analysis. The bounding box is used for map fitting and only as a labeled fallback when no polygon exists.
2. **Load the road graph.** The backend calls OSMnx with the exact polygon and normalizes road attributes.
3. **Load evidence.** The UI accepts measured traffic counts keyed by OSM way ID and GeoJSON Point access targets from an address, parcel, building, delivery, or emergency-service dataset.
4. **Find boundary roads.** When matching observations are supplied, the measured volume distribution selects the boundary network. Without them, the road-class/centrality fallback remains available but the result is tagged `modeled_topology` and blocked from implementation.
5. **Build road cells.** Closed rings in the boundary network are polygonized, clipped to the administrative geometry, filtered, and merged toward the configured target area.
6. **Assign entry sides.** Each generated cell gets four cardinal entry/return sectors: east, north, west, and south.
7. **Block cross-sector paths.** The constraint solver proposes modal filters, one-way changes, street cuts, and local two-way repairs. A connectivity-preserving directional-territory fallback is used when a cheaper cut plan would remove existing access.
8. **Validate separate claims.** The graph test checks only cross-sector vehicle paths. Supplied access targets are independently snapped to the interior road graph and checked for both an inbound and return path. Neither result is called real-world compliance.
9. **Apply the release gate.** `implementation_ready` requires all model checks, complete evidence, a transport-engineering attestation, and an on-site inspection attestation. Otherwise the API returns the exact blockers.
10. **Render and inspect.** MapLibre displays OpenStreetMap raster tiles; deck.gl displays the road graph, cell polygons, boundary roads, entry points, access signs, and test routes.

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

Point signs are hidden at city scale and appear from street-level zoom to avoid covering the road network. The proposed-works schedule initially renders 50 actions and can load further batches without creating thousands of DOM elements at once.

![Budapest street-level actions](docs/screenshots/budapest-street-actions.png)

## Where does the traffic go?

Every plan now comes with a traffic impact screening that needs no OD matrix. For each superblock, Superblocker routes every pair of entry points on different sides twice on the local network (interior plus boundary ring): once on today's streets and once with the plan's filters, one-way changes and cuts. A baseline route that cuts through the interior is a through trip, and its planned route shows where that trip goes instead.

- Interior volumes come from imported counts when present, otherwise from per-class peak-hour defaults for European urban streets.
- Half of the interior traffic on cut-through streets is treated as through traffic by default (`traffic_impact_settings.through_traffic_share`).
- Three evaporation scenarios bracket the evidence: none (every trip reroutes), central (15%), and London-like (40%), after the London low-traffic-neighbourhood studies that saw interior traffic fall 47% with boundary roads rising under 1%.
- The result lists added peak-hour vehicles, percent increase and volume/capacity per boundary road, the interior traffic cut and mean detour per superblock, and warns when minor streets end up carrying diverted traffic.

In the app, the **Where does the traffic go?** panel switches scenarios, ranks the most affected main roads, and colors the map by predicted increase.

![Budapest traffic impact](docs/screenshots/budapest-traffic-impact.png)

## Street design layers

Each run also loads OpenStreetMap bus, trolleybus and tram routes, fire and ambulance stations, schools and kindergartens in one cached Overpass query, and uses them to shape the plan:

- **Bus gates.** A filter on a public-transport route becomes a camera-enforced bus gate that buses and emergency vehicles pass; other filters become emergency bollards.
- **Emergency response.** Response time from the nearest station to the farthest interior node of each superblock, before and after, with and without the exemptions.
- **School streets.** A school inside a superblock gets a timed closure of its street at drop-off and pick-up, with residents and emergency vehicles exempt. A school on a boundary road gets a crossing and 30 km/h instead, because closing that road would push its traffic into the superblock.
- **Reclaimed space.** Calmed street length, the carriageway area that could become public space, and junctions whose every leg is calmed as candidate plazas.

All four are screening outputs: they inform the plan but do not count toward the implementation gate.

## Where to start

Cities that built superblocks well started small: a reversible trial with planters, removable bollards and signs, then permanent works where the trial succeeded. The **Where to start** panel ranks every superblock for such a trial and puts numbered badges on the map. Clicking a row zooms to that superblock.

- Measures are split into a trial stage and a build stage, with indicative central European unit costs. Explicit engineering estimates replace them when present.
- Interior injury risk is given as a range. It combines the through traffic removed with a 30 km/h limit, using the speed power model (injury crashes scale with roughly the square of mean speed).
- The ranking weighs, by percentile:
  - interior traffic cut: 25%;
  - through traffic removed: 15%;
  - reclaimable space: 15%;
  - street density, as a proxy for where people live: 15%;
  - school streets: 15%;
  - safety: 15%.
- It is lowered for long detours and for superblocks whose emergency response gets more than a minute slower.

On the offline Budapest graph, the top ten trials cost about €160k. The whole-city trial costs €7.6M and the permanent build €24.8M.

![Budapest pilot ranking](docs/screenshots/budapest-pilot-ranking.png)

## Noise and air quality

Each plan carries a screening estimate of what it does to noise and NO2, on both sides of the change:

- **Inside superblocks**, noise falls with the through traffic removed: half the traffic is about 3 dB quieter. A 30 km/h limit takes off another 1–3 dB on streets whose limit is above 30 km/h today. NO2 falls by the traffic cut times an assumed local-traffic share of street-level NO2 (30–60%).
- **On main roads**, the diverted traffic makes them louder. Roads that get 3 dB louder or more, roughly a doubling of traffic, are counted as noticeably louder.

On the offline Budapest graph, superblock interiors get about 1–2 dB quieter on average, with 4–7% less NO2; many inner streets are already signed 30 km/h, so the speed change adds little there. No main road gets 3 dB louder (the largest increase is 2.7 dB), and by default busy residential streets stay inside superblocks with filters, while quiet ones that split large cells get a filter too, so almost no residential street takes diverted traffic (the traffic estimate lists any that do). The "Keep busy residential streets inside superblocks" option can be turned off to get the older behaviour. These are relative changes, not a noise map or dispersion model; check the NO2 share against the OKIR monitoring stations before quoting a figure.

## Budapest verification run

Observed in the local app on 2026-08-31 with the default 12 ha target, 6 ha minimum, 20 ha maximum, four entry sectors, the exact Nominatim administrative polygon, and no external traffic/access evidence:

| Result | Value |
| --- | ---: |
| Selected extent | Exact Budapest administrative polygon |
| Input graph | 24,525 nodes; 64,026 directed edges |
| Rendered road network | 37,406 physical segments; 4,743.65 km |
| Generated superblocks | 726 |
| Generated-cell coverage | 34.7% of the administrative-polygon area |
| Boundary-road OSM IDs | 15,164 |
| Street actions | 2,439 |
| Modal filters | 2,371 |
| One-way changes | 4 |
| Two-way local-access repairs | 24 |
| Street cuts | 40 |
| Modeled directional path test | 726 / 726 generated cells passed |
| Evidence status | Exact boundary; topology traffic fallback; no access dataset |
| Release status | `model_only`; four blockers returned |
| OSM graph retrieval | 25.6 seconds on the test machine |
| Partition processing | 14.6 seconds on the test machine |
| End-to-end run | About 41 seconds on the test machine |

The prior rectangular pipeline spent 670.4 seconds in partition processing on a larger 32,734-node graph. The current 14.6-second figure is the measured polygon-clipped processing time, not an estimate. Coverage is still not a wall-to-wall land-use partition: it is the union of accepted closed road cells divided by the administrative area.

## Evidence inputs

Traffic CSV must contain `osm_id,volume_vph,source,observed_at`. JSON may contain the equivalent array of objects. `volume_vph` is the measured vehicles-per-hour value for the observation period.

Measured data must match at least 80% of physical road length before it can satisfy the implementation gate. Sparse observations still drive the measured-volume arterial mode, but the returned readiness blockers state the actual coverage shortfall.

Telraam citizen counts (CC BY 4.0) can stand in for authority counts where those are missing. `POST /api/v1/evidence/import/telraam` (admin key) takes an hourly report from the Telraam API and returns traffic observations, one per segment, for its busiest hour of cars and heavy vehicles. Telraam already corrects counts for sensor downtime; hours with less than 50% uptime are dropped, and a way covered by several segments keeps the busiest. Segments are tied to OSM ways by a `segment_id,osm_id` CSV, or matched to the nearest street within 25 m from the Telraam segments GeoJSON and a bounding box. Credit Telraam wherever the results are shown.

Reference files: [`examples/traffic-observations.csv`](examples/traffic-observations.csv) and [`examples/access-targets.geojson`](examples/access-targets.geojson).

Access data must be a GeoJSON `FeatureCollection` of `Point` features. Each feature may provide `id`, `kind`, and `label`; `kind` is one of `address`, `parcel`, `building`, `emergency`, or `delivery`. The operator must name the source and explicitly attest that the uploaded set is complete before it can satisfy the authoritative-access gate.

![Budapest exact boundary and evidence inputs](docs/screenshots/budapest-evidence-inputs.png)

After generation, the API assigns a SHA-256 `plan_id` over the immutable plan contents. `POST /partition/review` accepts attestations for `transport_engineering` and `site_inspection`, and rejects reviews whose `plan_id` does not match the submitted partition. Each attestation records the reviewer, organization, review date, and reference. The presence of an attestation is auditable input provenance; the software does not invent, infer, or authenticate professional credentials.

City-scale geometry work now uses one projected-area transformer per run, a Shapely edge index for cell classification, and a spatial index for cell adjacency. Large topology-fallback runs cap approximate centrality sampling at 128 nodes; measured-volume runs skip centrality entirely.

## Interface

The right-hand planner runs one workflow: **Analyze entire city**. Results include:

- model-path status, evidence provenance, release blockers, and administrative-boundary coverage;
- the boundary-road network reserved for cross-traffic;
- counts for every access-change type;
- a street-by-street action schedule with street name and coordinates;
- a warning when a supplied address/parcel/service target loses inbound or return access;
- display toggles for entry directions and street actions;
- a route tester that sends inter-cell travel onto boundary roads.

The basemap defaults to OpenStreetMap's no-key raster endpoint. Set `VITE_OSM_TILE_URL` to use another OSM-compatible raster tile service in a deployment, or `VITE_BASEMAP_STYLE_URL` to a vector style such as OpenFreeMap's no-key `https://tiles.openfreemap.org/styles/positron`.

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
| `VITE_BASEMAP_STYLE_URL` | unset | Vector basemap style, such as OpenFreeMap's `https://tiles.openfreemap.org/styles/positron`; falls back to the raster basemap if it fails to load |
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
| `OSM_EXTRACT_PATH` | unset | Local `.osm.pbf`, `.osm` or `.osm.bz2` extract; drive networks load from it instead of Overpass |


### Offline street data

City-scale runs against the public Overpass API are slow and rate-limited. Point `OSM_EXTRACT_PATH` at a free OpenStreetMap extract and street networks load from that file instead. Good sources are Geofabrik's [Hungary extract](https://download.geofabrik.de/europe/hungary.html) or a smaller [BBBike Budapest](https://download.bbbike.org/osm/bbbike/Budapest/) cut. The loader keeps the same roads as OSMnx's `drive` filter inside the requested area, so plans match an Overpass download. Context layers (schools, stations, transit routes) still come from Overpass. With Docker Compose, mount the file into the backend container and set the variable to its path there.

### Residents per superblock

Set `POPULATION_PATH` to a population grid and every plan counts the residents inside each superblock, the share of the area's residents that live in superblocks, and how many live in superblocks poorly served by transit. The pilot ranking then uses residents per hectare instead of street density, so dense neighbourhoods come first. The free [GHS-POP](https://human-settlement.emergency.copernicus.eu/download.php?ds=pop) grid from the European Commission (CC BY 4.0, 100 m cells) covers Budapest in one tile; download the GeoTIFF in either projection. A CSV with `lon,lat,population` columns also works.

### Open data for traffic, air and noise

`backend/scripts/fetch_open_data.py` downloads the layers that an Overpass download lacks, from public sources that need no account, and writes files the settings point at: residents from Meta's 30 m HRSL grid (`POPULATION_PATH`), jobs estimated from Overture Maps places and buildings (`JOBS_PATH`), fire and ambulance stations and schools (`FACILITIES_PATH`), OpenStreetMap turn restrictions via Overture (`TURN_RESTRICTIONS_PATH`), the operator's GTFS feed (`GTFS_PATH`, whose shapes also give the streets buses, trolleybuses and trams run on) and annual NO2 at air quality stations from the OpenAQ archive (`AIR_QUALITY_STATIONS_PATH`). With them, plans keep bus routes open and check facilities even when Overpass is unreachable, and the traffic simulator generates trips from homes and jobs.

The traffic simulator (`POST /api/v1/simulation/capacity`) routes movement by movement: OSM turn restrictions apply, U-turns only at dead ends, and each turn pays a typical delay for its junction (signals by direction, give-way from the minor road). Destination-only camera gates stay open to residents, so through traffic avoids them while nobody is cut off. A share of trips enters through the area's motorway and main-road gateways. Trips respond to the plan's change in travel time with a demand elasticity (default -0.5), so the result shows both the fixed-demand worst case and the plan once some trips are not made. `app/services/impact/environment.py` turns the simulated volumes and speeds into NO2 (emissions spread near-road and city-wide, fitted to the stations' measured annual means) and peak-hour noise (CRTN), and counts residents by the levels at their homes.

### Walking access to public transport

Every plan reports how far residents walk from each superblock's streets to a bus, tram, metro or train stop, using a 400 m threshold. Superblocks where less than half of the interior street length is within reach are flagged as poorly served, since moving cars to the boundary only works when people can take transit there instead. Stops come from OpenStreetMap by default. Set `GTFS_PATH` to a GTFS feed (the zip or its `stops.txt`), such as BKK's free [Budapest feed](https://go.bkk.hu/api/static/v1/public-gtfs/budapest_gtfs.zip), to use the operator's full list of platforms and metro entrances instead.

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
| `POST` | `/partition/review` | Apply both post-analysis review attestations to an exact plan ID |
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
