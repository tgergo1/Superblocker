# Budapest roadmap

Working list for the automated superblock tool, targeted at Budapest. Sources: the "Modern City Traffic Design Ideas" and "Open map and traffic data sources" research docs (2026-10-15) and `docs/research/complete-automated-superblock-calculator-gap-analysis.md`.

Rules: free tools and open data only; every feature ships with tests; screening outputs stay outside the implementation gate.

## Done

- [x] Import the local planning stack (registry, jobs, planning, evidence, SUMO adapter, optimizer, engineering compiler, approvals, workbench).
- [x] Boundary-road impact estimate with evaporation scenarios, map view and ranked road list.

## Next

1. **Bus and tram aware filters.** Mark each filter as physical, camera gate or retractable bollard with the vehicle classes that pass (bus, taxi, emergency, refuse). Keep OSM bus routes (`route=bus|trolleybus|tram` relations) through cells via gates instead of cutting them.
2. **Emergency response check.** Travel time from OSM fire and ambulance stations (`amenity=fire_station|ambulance_station`) to every superblock, before and after, honoring the exemption classes.
3. **School streets.** Find schools in OSM (`amenity=school|kindergarten`) and propose timed closures on the street at each entrance.
4. **Reclaimed space.** Square metres of carriageway freed per cell (filtered length × width from lanes), plus candidate plazas at filtered intersections and tree sites.
5. **Minor-boundary repair.** Merge or re-split cells whose boundary includes residential streets that the impact screening shows taking diverted traffic.
6. **Ghent-style sector mode** for the inner districts (V, VI, VII, VIII, IX): a few large sectors around the Kiskörút and Nagykörút, crossing only via the ring.
7. **Interior speed and safety score.** Default 30 km/h inside cells, expected crash change shown as a range from the 40-city study.
8. **Pilot then permanent phasing.** Split each plan into a cheap trial stage (paint, planters, one-way flips) and a build stage.
9. **Health co-benefit estimate.** Clearly labelled NO2, noise and activity estimate per cell, outside the gate.
10. **Data importers.** Telraam counts (CC BY 4.0) as traffic observations; GHS-POP residents per cell to rank candidates; BKK GTFS for stop access; Geofabrik extract loading instead of live Overpass; OpenFreeMap vector basemap.
11. **Performance.** Constraint enforcement dominates city-scale runtime; profile and parallelise per cell.
