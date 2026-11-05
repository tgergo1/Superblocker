"""Run the whole-Budapest plan and the impact model offline, for the paper.

Builds the drive graph from a cached Overpass response (the public Overpass
API is often unreachable from CI or containers), partitions the city with the
default settings, and runs the capacity simulator with any settings overrides.
Results are written as JSON so the paper's tables can be rebuilt.

    OVERPASS_CACHE=cache/db98d5be...json BOUNDARY_CACHE=cache/588cae8f...json \
    POPULATION_PATH=... JOBS_PATH=... python scripts/run_budapest.py plan --out runs/plan.pkl
    python scripts/run_budapest.py simulate --plan runs/plan.pkl --out runs/tti13.json \
        --settings '{"target_peak_travel_time_index": 1.3}'

``simulate`` options:
``--scores-out scores.json`` writes per-superblock coverage scores from the run;
``--coverage 0.5 --scores scores.json`` keeps only the best-ranked half of the
superblocks (see app/services/optimization/coverage.py). Pass
``"max_map_links": 200000`` in the settings so every link is scored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pickle
import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _patch_overpass() -> None:
    import osmnx._overpass as overpass

    path = os.environ.get("OVERPASS_CACHE")
    if not path:
        return

    def cached(*_args, **_kwargs):
        with open(path, encoding="utf-8") as handle:
            yield json.load(handle)

    overpass._download_overpass_network = cached


def _boundary():
    from app.models.schemas import AdministrativeBoundary, BoundingBox

    path = os.environ["BOUNDARY_CACHE"]
    record = json.loads(Path(path).read_text(encoding="utf-8"))["data"][0]
    box = record["boundingbox"]
    bbox = BoundingBox(north=box["north"], south=box["south"], east=box["east"], west=box["west"])
    return bbox, AdministrativeBoundary.model_validate(record["boundary"])


def plan(args) -> None:
    _patch_overpass()
    from app.api.routes.analysis import _evidence_context, run_partition_sync
    from app.models.schemas import FilterDesignSettings, PartitionRequest

    bbox, boundary = _boundary()
    filter_settings = FilterDesignSettings()
    request = PartitionRequest(bbox=bbox, boundary=boundary, filter_design_settings=filter_settings)
    started = time.time()
    result = run_partition_sync(
        bbox,
        boundary,
        request.target_size_hectares,
        request.min_area_hectares,
        request.max_area_hectares,
        request.num_sectors,
        True,
        {road_type.value for road_type in request.arterial_road_types},
        [],
        [],
        None,
        False,
        _evidence_context(request),
        [],
        [],
        [],
        [],
        None,
        request.assignment_settings,
        [],
        queue.Queue(maxsize=10),
        traffic_impact_settings=request.traffic_impact_settings,
        fetch_context=True,
        filter_design_settings=filter_settings,
        public_space_settings=request.public_space_settings,
        school_street_settings=request.school_street_settings,
    )
    partition = result["partition"]
    with open(args.out, "wb") as handle:
        pickle.dump({"partition": partition, "graph": result["graph"]}, handle)
    print(json.dumps(_plan_summary(partition, time.time() - started), indent=1))


def _plan_summary(partition, seconds: float) -> dict:
    from collections import Counter

    kinds: Counter = Counter()
    for superblock in partition.superblocks:
        for modification in superblock.modifications:
            kinds[str(getattr(modification, "modification_type", "?"))] += 1
    return {
        "superblocks": partition.total_superblocks,
        "valid": sum(1 for s in partition.superblocks if getattr(s, "is_valid", True)),
        "modifications": dict(kinds),
        "seconds": round(seconds, 1),
    }


def simulate(args) -> None:
    from app.models.simulation import CapacitySimulationSettings
    from app.services.importers.open_data import (
        load_configured_air_quality_stations,
        load_configured_jobs,
        load_configured_turn_restrictions,
    )
    from app.services.importers.population import load_configured_population
    from app.services.simulation.capacity_simulator import CapacitySimulator

    with open(args.plan, "rb") as handle:
        stored = pickle.load(handle)
    partition, graph = stored["partition"], stored["graph"]
    overrides = json.loads(args.settings or "{}")
    if overrides.get("entry_charge") and isinstance(overrides["entry_charge"].get("cordon"), str):
        ring = json.loads(Path(overrides["entry_charge"]["cordon"]).read_text(encoding="utf-8"))
        overrides["entry_charge"]["cordon"] = (
            ring.get("coordinates", ring) if isinstance(ring, dict) else ring
        )
        if overrides["entry_charge"]["cordon"] and isinstance(
            overrides["entry_charge"]["cordon"][0][0], list
        ):
            overrides["entry_charge"]["cordon"] = overrides["entry_charge"]["cordon"][0]
    settings = CapacitySimulationSettings(**overrides)
    residents = load_configured_population(partition.bbox)
    if args.coverage is not None and args.coverage < 1.0:
        from app.services.optimization.coverage import select_coverage

        scores = json.loads(Path(args.scores).read_text())
        partition = select_coverage(partition, scores, args.coverage)
    started = time.time()
    result = CapacitySimulator(
        graph,
        partition=partition,
        settings=settings,
        progress=lambda message, share: print(f"{share:5.0%} {message}", flush=True),
        residents=residents,
        jobs=load_configured_jobs(partition.bbox),
        turn_restrictions=load_configured_turn_restrictions(graph),
        stations=load_configured_air_quality_stations(),
    ).run()
    payload = result.model_dump(mode="json", exclude={"links"})
    payload["superblocks_simulated"] = len(partition.superblocks)
    payload["wall_seconds"] = round(time.time() - started, 1)
    payload["link_summary"] = _link_summary(result.links)
    Path(args.out).write_text(json.dumps(payload, indent=1))
    if args.scores_out:
        from app.services.optimization.coverage import superblock_scores

        links = [link.model_dump(mode="json") for link in result.links]
        Path(args.scores_out).write_text(json.dumps(superblock_scores(partition, links)))
    print("wrote", args.out)


def _link_summary(links) -> dict:
    """Totals the paper quotes: boundary-road load, gate use and main roads over +10%."""
    out = {
        "boundary_vkm_today": 0.0,
        "boundary_vkm_plan": 0.0,
        "boundary_vkm_plan_response": 0.0,
        "interior_vkm_today": 0.0,
        "interior_vkm_plan": 0.0,
        "gated_links": 0,
        "gated_vkm_plan": 0.0,
        "main_km_over_10pct": 0.0,
        "main_km_over_10pct_response": 0.0,
    }
    for link in links:
        km = link.length_m / 1000.0
        response = (
            link.planned_elastic_vph if link.planned_elastic_vph is not None else link.planned_vph
        )
        if link.is_boundary_road:
            out["boundary_vkm_today"] += link.baseline_vph * km
            out["boundary_vkm_plan"] += link.planned_vph * km
            out["boundary_vkm_plan_response"] += response * km
            if link.baseline_vph > 50 and link.planned_vph > 1.1 * link.baseline_vph:
                out["main_km_over_10pct"] += km
            if link.baseline_vph > 50 and response > 1.1 * link.baseline_vph:
                out["main_km_over_10pct_response"] += km
        else:
            out["interior_vkm_today"] += link.baseline_vph * km
            out["interior_vkm_plan"] += link.planned_vph * km
        if link.gated_in_plan:
            out["gated_links"] += 1
            out["gated_vkm_plan"] += link.planned_vph * km
    return {key: round(value, 1) for key, value in out.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--out", required=True)
    s = sub.add_parser("simulate")
    s.add_argument("--plan", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--settings")
    s.add_argument("--coverage", type=float)
    s.add_argument("--scores", help="superblock scores from a full run's --scores-out")
    s.add_argument("--scores-out", help="write per-superblock coverage scores here")
    args = parser.parse_args()
    {"plan": plan, "simulate": simulate}[args.command](args)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
