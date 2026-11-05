# November 2026 runs: sensitivity and improvements

Whole-Budapest runs behind Section 5.4 of `docs/paper/superblocks-budapest.tex`. Result files (without per-link maps) are in `runs-2026-11/`.

## Setup

- Code: this branch. Graph from the cached Overpass response (`backend/cache/db98d5be…json`, 34,641 junctions), boundary from the cached Nominatim search; Overpass context layers were unreachable, so bus routes come from GTFS shapes and facilities from Overture.
- Open data from `backend/scripts/fetch_open_data.py --bbox 18.925,47.350,19.335,47.613 --year 2025` with the BKK GTFS from `storage.googleapis.com/mdb-latest/hu-budapest-budapesti-kozlekedesi-kozpont-bkk-gtfs-990.zip` and OpenAQ locations 783877, 783882, 783913, 783920, 783927, 783938, 783939, 3945, 3960, 3962, 3963, 3964 (10 stations with 2025 data).
- Plan: `scripts/run_budapest.py plan` with default settings: 2,291 superblocks, all valid, 1,047 destination-only gates (950 camera gates plus gated bus gates and one-ways), 629 bus gates, 5,808 emergency bollards. About 270 s.
- Each simulation: `scripts/run_budapest.py simulate --plan plan.pkl --settings '<json>'`, about 25 minutes each with four in parallel.

| File | Settings | Notes |
| --- | --- | --- |
| tti13 | `{"max_map_links": 200000}` plus `--scores-out scores.json` | Reference run; writes the coverage scores |
| tti16 | `target_peak_travel_time_index` 1.6, breakdown 2.46 | TomTom 2025 all-day congestion |
| tti19 | index 1.9, breakdown 2.92 | TomTom 2025 morning peak |
| gates_open_full | `gate_penalty_s` 0 | Gates cost through traffic nothing (no enforcement) |
| cov0.1 … cov0.75 | `--coverage <share> --scores scores.json` | Best-ranked share of superblocks only |
| charge_cordon | Hungária cordon, 800 HUF, no elasticity range | Same scheme as the paper's Table 4, on this plan |
| charge_bridges | as above plus `charged_bridge_names: ["híd"]` | Every Danube road bridge charged per crossing |
| cov0.25_bridges | best 25% plus the bridge charge | Combined package |

## Results (trips respond, elasticity −0.5)

| Variant | Superblocks | Trip time | Capacity | Pop.-weighted NO2 (µg/m³) |
| --- | --- | --- | --- | --- |
| Today | 0 | 0 | 0 | 22.42 |
| Full plan, index 1.3 | 2,291 | +18.9% | −17% | 23.05 |
| Full plan, index 1.6 | 2,291 | +20.7% | −12% | 22.99 (today 22.35) |
| Full plan, index 1.9 | 2,291 | +24.4% | −10% | 22.96 (today 22.27) |
| Gates not enforced | 2,291 | +13.6% | −14% | 22.88 |
| Best 75% | 1,718 | +16.5% | −14% | 22.99 |
| Best 50% | 1,146 | +16.1% | −12% | 22.98 |
| Best 25% | 573 | +11.4% | −7% | 22.88 |
| Best 10% | 229 | +4.5% | −1% | 22.62 |
| Cordon charge with plan | 2,291 | +25.3% | – | 22.97 |
| Cordon and bridges charged, with plan | 2,291 | +20.9% | – | 22.61 |
| Bridge charge alone | 0 | +3.2% | – | 22.19 |
| Best 25% with bridges charged | 573 | +13.3% | – | 22.45 |

Coverage frontier (fixed demand): the best 10/25/50/75% of superblocks remove 39/78/92/92% of the interior vehicle-km the full plan removes (115,500 veh-km in the peak hour) and add 26/73/89/91% of its boundary vehicle-km. 1,366 superblocks carry no modelled through traffic.

Unenforced gates: traffic on gated links rises from about 3,800 to 14,900 veh-km in the peak hour and the interior reduction falls by 8%.

Bridge charge revenue (about 63 bn HUF a year) assumes 800 HUF per crossing, which is far above any real per-crossing toll; treat it as an upper bound.

## Caveats

- No traffic counts: demand is still the gravity model. The public BKK counter dashboard shows about 15 sites as daily charts only; hourly data for all counters needs the request in `bkk-data-requests.md`.
- The coverage score uses one assignment of the full plan, so the ranking is a screening order, not an optimum for each share. Dropping superblocks also drops the crossing seals they carried, so a few kept superblocks may regain a crossing route.
- Junctions cut off once filtered sections close: 2 in the full plan, 6–15 in the partial plans.
