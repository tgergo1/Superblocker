# Budapest roadmap

Working list for the automated superblock tool, targeted at Budapest. Sources: the "Modern City Traffic Design Ideas" and "Open map and traffic data sources" research docs (2026-10-15) and `docs/research/complete-automated-superblock-calculator-gap-analysis.md`.

Rules: free tools and open data only; every feature ships with tests; screening outputs stay outside the implementation gate.

## Done

- [x] Entry-pair cap: the traffic cut is measured over every entry pair (cap raised from 200 to 20,000; any sample left is spread over all sides). On the offline Budapest graph, removed through traffic rises 22% in circulation-sector mode (24,600 to 30,100 veh/h) and 4% in superblock mode, for under 3% more run time.
- [x] Transit in the pilot ranking: superblocks where under half the streets are within walking distance of a stop rank 15% lower, the "Where to start" list says so, and the map outlines them in purple.
- [x] Import the local planning stack (registry, jobs, planning, evidence, SUMO adapter, optimizer, engineering compiler, approvals, workbench).
- [x] Boundary-road impact estimate with evaporation scenarios, map view and ranked road list.
- [x] Bus and tram aware filters: filters on OSM bus, trolleybus and tram routes become camera bus gates; other filters become emergency bollards.
- [x] Emergency response check from OSM fire and ambulance stations, before and after, with and without exemptions.
- [x] Reclaimed space: calmed street km, carriageway m² and candidate plazas per superblock.
- [x] Option to keep busy residential streets inside superblocks instead of making them boundary roads.
- [x] School streets: timed closures in front of schools and kindergartens inside superblocks; crossing and 30 km/h treatment for schools on main roads.
- [x] Pilot-first phasing and 30 km/h safety: trial and build costs per superblock, interior injury-risk range, and a "Where to start" ranking with map badges.
- [x] Health co-benefit screening: interior noise (dB) and NO2 (%) ranges per superblock, and main roads that get noticeably louder.
- [x] Oversized cells: cells over five times the maximum size with no main road split along residential streets (not in circulation-sector mode), and split lines now work in concave cells. Split streets become boundary roads of both halves. On the offline Budapest graph this turns 782 superblocks into 1,206, shrinks the largest cell from 12,236 ha to 5,276 ha, and raises residents with noticeably quieter streets from about 19,000 to 49,000 (stand-in population grid); two main roads get up to 3.8 dB louder.
- [x] Health per person: with a population grid, noise and NO2 means are weighted by residents, and the plan counts residents whose streets get noticeably quieter (3 dB or more).
- [x] Residents per superblock from GHS-POP (`POPULATION_PATH`): residents and density per superblock, share of residents in superblocks, residents poorly served by transit, and a pilot ranking by residents per hectare.
- [x] Walking access to transit: share of each superblock's streets within 400 m of a bus, tram, metro or train stop, from OSM or a GTFS feed such as BKK's (`GTFS_PATH`); poorly served superblocks are flagged.
- [x] Offline street data: networks load from a local Geofabrik or BBBike extract (`OSM_EXTRACT_PATH`) instead of Overpass.
- [x] Circulation-sector mode (Ghent-style): district-sized sectors bounded by main roads only.
- [x] Telraam importer: hourly citizen counts become peak-hour traffic observations, matched to OSM streets.
- [x] Performance, second pass: whole-city circulation-sector runs finish in about 3 minutes instead of more than 10.
- [x] Performance, first pass: compiled sampled centrality and cached path searches take a Budapest run from about 93 s to 45 s.

## Next

1. **Minor-boundary repair.** First step done: the "Keep busy residential streets inside superblocks" option stops residential and living streets becoming boundary roads on centrality alone. On the offline Budapest graph it removes diverted traffic from all 1,311 minor roads, lowers the worst boundary-road increase from 510% to 84% and calms 3,673 km of street instead of 3,277 km, but the median cell grows from 23 ha to 42 ha. Next: split those large cells along low-traffic residential streets with filters, so cells stay near the target size.
2. **Health, next step.** Calibrate the NO2 share against OKIR stations. Resident weighting is done. The gap between people-weighted and per-superblock results came from outer districts left as single cells of 1,000-12,000 ha (no main road to split on) holding about 30% of residents; those now split along residential streets. Remaining: about 5,300 ha of motorway-only or hillside cells, and the 20-100 ha suburban cells that keep their residential streets inside.
3. **Data importers.** OpenFreeMap vector basemap.
4. **Performance.** Constraint enforcement and cell optimisation still take most of the 45 s; parallelise per cell.
