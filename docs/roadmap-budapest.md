# Budapest roadmap

Working list for the automated superblock tool, targeted at Budapest. Sources: the "Modern City Traffic Design Ideas" and "Open map and traffic data sources" research docs (2026-10-15) and `docs/research/complete-automated-superblock-calculator-gap-analysis.md`.

Rules: free tools and open data only; every feature ships with tests; screening outputs stay outside the implementation gate.

## Done

- [x] Default policy: keeping busy residential streets inside superblocks is now the default (API, partitioner and UI). The older behaviour stays available as `minor_road_boundaries: "allow"`. On the offline Budapest graph the default plan goes from 1,206 superblocks (median 42 ha, 1,350 minor roads loaded, worst boundary increase 623%) to 2,205 (median 15.7 ha, 1 minor road, 142%).
- [x] Splits follow the street: an oversized cell is split along the whole street it is cut on, followed through its junctions and bends to the boundary roads, instead of a straight extension of one segment that cut through the blocks beside it. Every section of the street becomes a boundary road (a quiet residential street gets a modal filter on each section). Cells still over the maximum after a split are split again in the same pass. On the offline Budapest graph: 2,391 superblocks (2,131 at or under 40 ha), 260 cells over 40 ha instead of 336, worst boundary-road load increase 47% instead of 142%, 222 boundary roads over the increase threshold, runs take 76 s instead of 106 s.
- [x] Minor-boundary repair: with "Keep busy residential streets inside superblocks", any cell over the maximum size splits along a quiet residential street (never a busy rat run), and that street gets a modal filter so diverted traffic stays on main roads. Oversized-cell splits in the default mode get the same filter. On the offline Budapest graph the option now gives 2,205 superblocks instead of 1,004, median 15.7 ha instead of 59 ha, 336 cells over the maximum instead of 701, 1 minor road loaded instead of 25, and 230 boundary roads over the increase threshold instead of 1,207; runs take 106 s instead of 76 s.
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

Superblock creation comes first; impact, health and data work support it.

1. **Cells still over the maximum.** 260 of 2,391 default cells stay over 40 ha. Earlier diagnosis: most have a split that leaves one piece under the minimum, no street that cuts the cell, or no interior streets at all (hillside, rail yards, water). Next: allow an uneven split when the small piece can merge with a neighbour, and split along two streets at once.
2. **Health, next step.** Calibrate the NO2 share against OKIR stations. Resident weighting is done. The gap between people-weighted and per-superblock results came from outer districts left as single cells of 1,000-12,000 ha (no main road to split on) holding about 30% of residents; those now split along residential streets. Remaining: about 5,300 ha of motorway-only or hillside cells, and the 20-100 ha suburban cells that keep their residential streets inside.
3. **Data importers.** OpenFreeMap vector basemap.
4. **Performance.** Constraint enforcement and cell optimisation still take most of the 45 s; parallelise per cell.
