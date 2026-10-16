# Budapest roadmap

Working list for the automated superblock tool, targeted at Budapest. Sources: the "Modern City Traffic Design Ideas" and "Open map and traffic data sources" research docs (2026-10-15) and `docs/research/complete-automated-superblock-calculator-gap-analysis.md`.

Rules: free tools and open data only; every feature ships with tests; screening outputs stay outside the implementation gate.

## Done

- [x] Import the local planning stack (registry, jobs, planning, evidence, SUMO adapter, optimizer, engineering compiler, approvals, workbench).
- [x] Boundary-road impact estimate with evaporation scenarios, map view and ranked road list.
- [x] Bus and tram aware filters: filters on OSM bus, trolleybus and tram routes become camera bus gates; other filters become emergency bollards.
- [x] Emergency response check from OSM fire and ambulance stations, before and after, with and without exemptions.
- [x] Reclaimed space: calmed street km, carriageway m² and candidate plazas per superblock.
- [x] Option to keep busy residential streets inside superblocks instead of making them boundary roads.
- [x] School streets: timed closures in front of schools and kindergartens inside superblocks; crossing and 30 km/h treatment for schools on main roads.
- [x] Pilot-first phasing and 30 km/h safety: trial and build costs per superblock, interior injury-risk range, and a "Where to start" ranking with map badges.
- [x] Health co-benefit screening: interior noise (dB) and NO2 (%) ranges per superblock, and main roads that get noticeably louder.
- [x] Telraam importer: hourly citizen counts become peak-hour traffic observations, matched to OSM streets.
- [x] Performance, second pass: whole-city circulation-sector runs finish in about 3 minutes instead of more than 10.
- [x] Performance, first pass: compiled sampled centrality and cached path searches take a Budapest run from about 93 s to 45 s.

## Next

1. **Minor-boundary repair.** First step done: the "Keep busy residential streets inside superblocks" option stops residential and living streets becoming boundary roads on centrality alone. On the offline Budapest graph it removes diverted traffic from all 1,311 minor roads, lowers the worst boundary-road increase from 510% to 84% and calms 3,673 km of street instead of 3,277 km, but the median cell grows from 23 ha to 42 ha. Next: split those large cells along low-traffic residential streets with filters, so cells stay near the target size.
2. **Ghent-style sector mode** for the inner districts (V, VI, VII, VIII, IX): a few large sectors around the Kiskörút and Nagykörút, crossing only via the ring.
3. **Health, next step.** Weight the noise and NO2 ranges by residents (GHS-POP) and calibrate the NO2 share against OKIR stations.
4. **Data importers.** GHS-POP residents per cell to rank candidates; BKK GTFS for stop access; Geofabrik extract loading instead of live Overpass; OpenFreeMap vector basemap.
5. **Performance.** Constraint enforcement and cell optimisation still take most of the 45 s; parallelise per cell.
