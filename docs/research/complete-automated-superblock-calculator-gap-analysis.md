# What is missing from a complete automated superblock calculator

Research cut-off: 1 September 2026  
Scope: Budapest first, reusable for other cities with jurisdiction-specific data and rules.

## Verdict

Superblocker is currently a fast **street-topology proposal generator**. It can load an exact administrative polygon, build an OSM driving graph, infer boundary roads, create closed cells, propose filters and one-way changes, and test whether modeled vehicle paths cross between entry sectors.

That is a useful core, but it is not yet a complete superblock calculator. It does not answer the decisive question:

> After every affected trip reroutes, can the boundary network carry the new traffic safely and reliably—without unacceptable queues, transit delay, emergency delay, freight failure, crash risk, pollution, noise, or spillover into the next neighborhood?

The largest missing component is therefore **a calibrated, time-dependent, multimodal demand and traffic-assignment loop**, not another polygon or graph-cut heuristic.

The defensible end state is highly automated evidence ingestion, option generation, simulation, constraint checking, comparison, documentation, and monitoring. Statutory approval, professional safety certification, emergency-service acceptance, site verification, public consultation, and political trade-offs must remain accountable human decisions.

## What the repository already does

The current pipeline in [`city_partitioner.py`](../../backend/app/services/partitioning/city_partitioner.py) is:

1. fetch and normalize an OSMnx driving graph;
2. identify boundary roads from observed-volume percentile or OSM class plus approximate centrality;
3. polygonize closed boundary-road rings;
4. merge or split cells toward a target area;
5. use graph cuts and a directional fallback to block paths between entry sectors;
6. check explicit access points for a modeled inbound and return path;
7. expose a plan hash, evidence metadata, two review attestations, a map, an action list, and a route tester.

The documented Budapest run is fast enough for interactive proposal generation: 24,525 nodes, 64,026 directed edges, 726 generated cells and 2,439 proposed street actions in about 41 seconds end to end. But those cells cover only 34.7% of the administrative area, the run has no external demand/access evidence, and all 726 passing cells prove only the modeled cross-sector path condition.

## Gap matrix

| Capability | Present behavior | Missing completion criterion | Priority |
| --- | --- | --- | --- |
| Study area | Budapest administrative polygon | Functional urban area plus a simulation cordon that captures commuting, freight and diversion outside the city boundary | Blocker |
| Street model | Simplified OSM driving edges | Versioned lane, junction, turn, signal, restriction, curb and physical-barrier model with stable authoritative IDs | Blocker |
| Traffic evidence | One vehicles-per-hour value per OSM way; otherwise class-based estimates | Direction, lane, vehicle class, time slice, turning movement, speed, confidence, detector metadata and independent validation samples | Blocker |
| Demand | No origin-destination demand | Time-dependent person and goods demand by mode and vehicle class, including external/cordon trips | Blocker |
| Assignment | One route at a time | Network-wide rerouting with congestion, capacity, queues, spillback, signals, turn delay and induced/suppressed demand | Blocker |
| Boundary roads | High observed percentile or OSM class/centrality | Continuous corridor hierarchy proven to have enough directional capacity and safe intersections in every scenario | Blocker |
| City partition | Closed arterial rings; 34.7% Budapest coverage | Every eligible street and access target assigned to a cell or an explicit, justified exclusion | Blocker |
| Intervention choice | Road-class heuristic chooses filter, one-way or cut | Turn-aware, constructible, costed interventions optimized against hard constraints and multiple objectives | Critical |
| Modes and services | A single motor-vehicle graph | Walking, wheeling, cycling, transit, emergency, refuse, freight, taxi and permit/exempt vehicle classes | Critical |
| Access | Point snapped to nearest road node | Entrance/driveway/frontage, time-window, vehicle-envelope, loading, accessibility and emergency-operating-space validation | Critical |
| Safety and environment | No predictive safety or environmental case | Crash/exposure, speed, conflict, sight-line, air, noise, heat, emissions and equity comparison with uncertainty | Critical |
| Decision assurance | Two free-text review types can unlock `implementation_ready` | Authenticated roles, jurisdictional approvals, signed artifacts, consultation, pilot, monitoring and rollback workflow | Blocker |
| Platform | In-process analysis and file/memory caches | PostGIS/object storage, dataset and scenario registry, job queue, cancellation/resume, audit log, observability and reproducible runs | Important |

## The target calculation

The calculator needs a closed evidence loop:

```text
versioned source data
  -> conflated lane/turn/mode network
  -> calibrated baseline demand and assignment
  -> candidate cells and interventions
  -> network-wide reassignment and selected micro-simulation
  -> hard constraint gates and Pareto-ranked alternatives
  -> engineering/pilot package
  -> before/after monitoring, adaptation or rollback
```

### 1. Build an authoritative multimodal network

Keep OSM as a bootstrap and public basemap, but not as the legal traffic-order record. The network must represent:

- lanes, allowed movements and turn restrictions;
- signal phases, intergreens, coordination and detectors;
- direction, speed, width, grade, clearances and vehicle restrictions;
- footways, crossings, curb ramps, cycle facilities and transit boarding areas;
- parking, loading, taxi, disabled parking, bus stops and time-dependent curb rules;
- barriers, gates, removable emergency filters and failure state;
- ownership, road manager, jurisdiction and rule version.

OSM turn restrictions are relations between a `from` way, `via` node/way and `to` way; a node-edge graph that only removes a street direction cannot represent that state correctly. The production model should use a turn-expanded or lane-connection graph.

For Budapest, the most important authoritative foundation is Budapest Közút’s [KAPU/KARESZ system](https://www.budapestkozut.hu/informatikai-igazgatosag/nyilvantartas-es-adatszolgaltatas/kapu-adatpublikacio/), which the operator describes as containing the full road and traffic-order inventory. Its open [Közút Figyelő ArcGIS service](https://kapu.budapestkozut.hu/arcgis/rest/services/kozutfigyelo/kozutfigyelo_v2/MapServer) exposes useful restrictions, speed limits, cycling facilities, parking zones, signal locations, managed roads and traffic-calmed areas, but not the full engineering record.

### 2. Model demand, not just observed links

A road count says how many vehicles crossed a detector; it does not uniquely reveal where they started, where they ended or which alternate route they will choose. SUMO’s own [count-based demand documentation](https://sumo.dlr.de/docs/Demand/Routes_from_Observation_Points.html) explicitly notes this non-uniqueness and warns that some count-routing methods produce implausible routes in meshed city networks.

The traffic schema therefore needs:

- 5- or 15-minute intervals, not one undifferentiated VPH value;
- direction, lane and turning movement;
- car, light/heavy goods, bus, motorcycle, emergency and other relevant classes;
- volume, speed, occupancy, queue and travel-time observations;
- detector position, calibration, missingness, uncertainty and source licence;
- representative weekday/weekend, school, seasonal, event and disrupted conditions.

Budapest has a strong starting point. BKK’s [EFM 3.0](https://bkk.hu/rolunk/forgalmi-adatok-diagramok/egyseges-forgalmi-modell/) covers Budapest and its agglomeration, uses more than 1,000 zones, and was rebuilt around roughly 60,000 surveyed trips. It can supply metropolitan demand and strategic assignments by request. It should seed the baseline, but its macroscopic zones must be refined with junction counts, turning movements, signals, travel times and local surveys before validating a street filter.

### 3. Prove that displaced traffic fits on the borders

Every candidate plan must rerun the same baseline demand on the modified network. A pass requires, for each modeled period and uncertainty case:

- no prohibited through-trip crosses a cell interior;
- every permitted local trip can enter and return through an allowed access sector;
- boundary volume-to-capacity, queue length, spillback and intersection saturation stay within engineer-set limits;
- travel-time and reliability changes remain within approved limits by user group;
- displaced traffic does not create unacceptable impacts in adjacent cells or municipalities;
- transit, emergency and freight constraints pass separately;
- the result is stable across plausible demand, behavior and intervention-failure assumptions.

Use a two-level engine. A calibrated macroscopic or mesoscopic assignment can screen thousands of candidate plans. Run the finalists through a lane/junction simulator. [SUMO](https://sumo.dlr.de/docs/) already supports large networks, dynamic assignment, road and turn counts, GTFS, signals, public transport, pedestrians, bicycles, vehicle permissions, emergency vehicles, parking and emissions. Building a new microscopic simulator inside Superblocker would be wasted effort.

Calibration is a release gate, not an optional chart. Follow a documented process of input checking, baseline calibration, independent validation, multiple stochastic repetitions, confidence intervals, sensitivity testing and alternative comparison, as set out in the [FHWA Traffic Analysis Toolbox](https://ops.fhwa.dot.gov/trafficanalysistools/tat_vol3/Vol3_Guidelines.pdf). The methodology is useful even though it is not Hungarian law.

### 4. Make the partition complete and objective-driven

The current polygonization accepts only closed rings between 0.5 and 100 hectares. That explains the 34.7% Budapest coverage. A complete result needs one of three labels for every eligible location:

1. assigned to a generated superblock;
2. reserved as a boundary/corridor facility;
3. excluded with a machine-readable reason, such as motorway, railway, water, industrial site, airport, park, inaccessible terrain or insufficient evidence.

Cell size should be a soft outcome, not the main objective. The optimizer needs hard constraints and explicit objectives.

Hard constraints should include directional access, local destinations, boundary capacity, transit continuity, emergency response, freight/service access, walking/wheeling accessibility, cycling continuity, safety, legal restrictions, constructability and budget ceilings.

Objectives should include through-traffic reduction, total delay, reliability, emissions, noise, crash exposure, active-mode access, transit performance, equity, public-space gain, number of interventions, capital cost and maintenance cost. Because those objectives conflict, the system should return a small Pareto set with transparent weights and trade-offs—not one opaque “optimal” map.

### 5. Compile graph changes into buildable street designs

“Cut this edge” is not an implementation instruction. Each proposal needs:

- exact junction/chainage and affected movements;
- sign, marking, signal, bollard, planter, camera or kerb treatment;
- permitted classes, exemptions and time windows;
- swept paths and operating space for local service vehicles;
- visibility, drainage, utilities, parking/loading and accessibility effects;
- temporary traffic management and construction staging;
- quantity, capital cost, operating cost, maintenance owner and failure plan;
- GIS/CAD export, bill of quantities, permit package and field inspection checklist.

The app currently states that a modal filter retains emergency access, but its access validator removes the road from the only vehicle graph. That wording must be removed until a distinct emergency-vehicle class, physical gate design, key/transponder process, response-time model and service approval exist.

### 6. Protect every mode and essential service

The following need independent constraint engines, not generic reachability:

- **Public transport:** static [BKK GTFS and GTFS-Realtime](https://opendata.bkk.hu/data-sources), observed running times, passenger loads, dwell times, depot/dead-running movements, trolleybus geometry, stop accessibility and operator-approved detour/reliability limits.
- **Emergency response:** actual fire/ambulance/police vehicles, clearances, turn envelopes, working areas, stations, hydrants, dispatch/response-time data, access-control reliability and drills. Hungarian fire-access duties are in [54/2014 BM](https://njt.hu/jogszabaly/2014-54-20-0A.9); software routing cannot certify them.
- **Walking and wheeling:** continuous routes, clear width, slope/crossfall, surfaces, curb ramps, tactile guidance, crossing time, refuge, signals, lighting and obstruction surveys.
- **Cycling:** coherent, direct, low-stress routes across both interiors and busier border roads, including safe junctions and secure parking.
- **Freight and servicing:** vehicle dimensions, delivery demand, time windows, loading bays, waste collection, building/service entrances and district curb rules.

### 7. Add safety, environment, equity and causal monitoring

The [EU road-safety directive](https://eur-lex.europa.eu/eli/dir/2019/1936/oj/eng) provides a useful completeness checklist even where its mandatory scope does not cover an ordinary local street: traffic and vulnerable-user volumes, speeds, sight distance, junction geometry/control, crossings, property and emergency access, public transport, signs, markings, lighting, pavement, drainage, parking and heavy vehicles.

Each selected alternative needs a baseline and forecast for crashes/exposure, speeds, air pollution, greenhouse gases, noise, heat, walking/cycling, transit use, public-space activity, business access and distributional impacts. Budapest’s official Mobility Plan already includes objectives, a strategic environmental assessment and monitoring; the calculator should align its indicators with that framework rather than invent a separate scorecard. See the [Budapest Mobility Plan](https://bkk.hu/en/about-bkk/strategy/budapest-mobility-plan/) and the EU’s [2026 SUMP Guidelines](https://urban-mobility-observatory.transport.ec.europa.eu/sustainable-urban-mobility-plans/guidelines-developing-and-implementing-sustainable-urban-mobility-plan-0_en).

Do not assume effects from the Barcelona label. Barcelona’s [2025 public-health evaluation](https://doi.org/10.1186/s12889-025-21835-z) found heterogeneous results and used surveys, environmental measurements, walkability audits, observed activity, qualitative work and injury analysis. Every pilot needs pre-registered measures, before/after and comparison locations where feasible, seasonality/confounder handling, boundary sensors, privacy controls and explicit adapt/rollback triggers.

## Budapest data and institutional work still required

| Partner/source | Required production input |
| --- | --- |
| BKK | EFM 3.0 demand and assignments; raw multimodal counts; transit demand, running time and operational constraints; Mobility Plan indicator alignment |
| Budapest Közút | KAPU/KARESZ road and traffic-order export; raw detectors; signal controller plans; curb/loading; planned works; traffic-engineering review workflow |
| Hungarian NAP / Magyar Közút | DATEX II and TN-ITS packages, restrictions, incidents and road data with freshness/licence checks |
| ORFK/BKK | Clean geocoded injury-collision records by severity, mode, maneuver and conditions |
| Lechner / land offices | Licensed address, parcel and building geometry; entrances still need survey/enrichment |
| KSH and city geoportal | Population, disability/limitation, land use, environment, heritage and planned-development layers at privacy-safe resolution |
| Districts | Local-road ownership, parking/curb rules, permits, projects, waste and delivery operations |
| Fire, ambulance, police and operators | Vehicle envelopes, stations/depots, response/service constraints, access-control procedures and formal review |
| Field survey contractors | Turns, speeds, queues, walking/cycling desire lines, curb use, entrances, accessibility, delivery and before/after measurements |

Open data alone is insufficient. The production system needs data-sharing and licence agreements, update service levels, stable identifiers, redistribution rules and targeted field surveys.

## Replace the current release gate

`implementation_ready` is not defensible as currently implemented. The code can set it after an 80% physical-road-length traffic match, point access, directional graph checks, and the presence of two review records. That does not prove demand coverage, safe boundary capacity, accessibility, emergency response, transit operation, legal approval or constructability.

Rename it to `ready_for_engineering_review` and use progressive gates:

1. `topology_candidate` — complete/excluded geography and turn-aware directional rules pass;
2. `data_qualified` — required datasets meet spatial, temporal, mode and quality thresholds;
3. `baseline_calibrated` — baseline passes independent validation targets;
4. `impact_validated` — reassignment, boundary, service, safety and sensitivity gates pass;
5. `engineering_reviewed` — signed traffic, accessibility, transit, emergency and constructability reviews;
6. `approved_for_pilot` — road manager, authority, consultation and monitoring package complete;
7. `approved_for_permanent_works` — pilot evidence and statutory approvals complete.

No user-entered attestation should advance a regulated gate without authenticated identity, role, artifact, timestamp, applicable rule version and immutable audit history.

## Build sequence

The durations below are engineering estimates, not source claims. They assume a dedicated 10–14 person product/transport team plus BKK, Budapest Közút, district, emergency-service and legal partners.

### Phase 0 — stop overclaiming (2–4 weeks)

- rename the release state and remove the emergency-access claim;
- make incomplete cell coverage a blocker and require explicit exclusions;
- expand observations to time, direction, lane, class and turn;
- separate vehicle classes and permissions;
- persist reviews and evidence instead of accepting transient free-text presence;
- add regression tests for every claim displayed in the UI.

Exit: the app clearly labels what it proves and cannot produce a false implementation-ready state.

### Phase 1 — data and network foundation (2–4 months)

- PostGIS/object-store dataset registry with source, licence, version, checksum and lineage;
- functional-area/corridor boundary model;
- OSM plus KAPU/KARESZ conflation and stable-ID strategy;
- lane/turn/signal/curb/mode graph;
- GTFS/GTFS-Realtime, EFM, detector, crash, access and planning-data adapters;
- automated completeness and quality reports.

Exit: one reproducible Budapest baseline network snapshot with known gaps.

### Phase 2 — calibrated impact engine (3–6 months)

- EFM demand importer and local disaggregation;
- time-dependent multimodal assignment;
- count/turn/speed/travel-time calibration and independent holdout validation;
- boundary capacity, queue/spillback and adjacent-area checks;
- SUMO conversion and selected-scenario simulation;
- transit, emergency, freight and intervention-failure scenarios.

Exit: baseline and modified networks reproduce agreed observations and quantify rerouting with uncertainty.

### Phase 3 — complete optimizer and engineering compiler (4–8 months)

- wall-to-wall cell/exclusion generation;
- hard-constraint plus multi-objective/Pareto optimization;
- simulator-in-the-loop refinement of finalists;
- constructability rules, cost and maintenance model;
- scenario comparison, explanation and signed export packages.

Exit: the system returns several feasible, costed alternatives and a traceable reason for every action.

### Phase 4 — assurance and pilot operations (4–12 months)

- jurisdiction-specific approval workflows;
- consultation and affected-party mapping;
- reversible pilot design and rollback triggers;
- sensor/field monitoring, causal evaluation and adaptive updates;
- security, access control, job orchestration, observability and operational support.

Exit: one Budapest pilot is approved, installed, monitored and either adapted, removed or advanced to permanent design using recorded evidence.

Realistically, a credible Budapest pilot calculator is a **12–18 month programme** after data agreements begin. A reusable multi-city platform is likely **18–30 months** because legal rules, authoritative networks, accessibility standards, data licences and institutional workflows do not transfer automatically.

## Definition of “complete”

The calculator is complete when it can take a versioned city evidence package and automatically produce multiple constructible plans for the entire eligible network, while demonstrating with calibrated and sensitivity-tested models that:

- motor through-traffic is removed from cell interiors by class and time period;
- local access remains valid for every authoritative entrance and service need;
- displaced trips are assigned to a continuous border network that passes capacity, queue and spillover limits;
- transit, emergency, walking/wheeling, cycling, freight and curb operations pass separate constraints;
- safety, environment, equity, cost and uncertainty are visible for every alternative;
- every excluded area, input, transformation, objective weight, decision and review is reproducible;
- the output advances only through the applicable professional, public and statutory gates;
- the deployed pilot is monitored against its baseline and can be adapted or rolled back.

Until then, Superblocker should describe its output as **automatically generated topology candidates for engineering analysis**. That is already valuable, precise, and honest.
