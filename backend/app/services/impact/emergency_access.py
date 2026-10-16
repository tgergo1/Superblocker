"""Check fire and ambulance response times before and after a plan.

London's 72-scheme study found no slower fire response inside low-traffic
neighbourhoods, largely because filters were passable or routes existed
around them. This check makes that claim testable for every superblock:
travel time from the nearest OSM fire or ambulance station to the worst
interior node today, with the plan (emergency vehicles use bus gates and
emergency bollards), and with the plan if no filter could be opened.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable

import networkx as nx

from app.models.schemas import (
    ContextFacility,
    EmergencyAccessResult,
    EmergencyAccessSummary,
    StreetModification,
    VehicleClass,
)
from app.services.impact.boundary_impact import SuperblockImpactInput
from app.services.network.turn_graph import TurnGraphBuilder, _travel_time_seconds

EMERGENCY_KINDS = {"fire_station", "ambulance_station"}


def _nearest_node(graph: nx.MultiDiGraph, lon: float, lat: float) -> int | None:
    best: tuple[float, int] | None = None
    scale = math.cos(math.radians(lat))
    for node, data in graph.nodes(data=True):
        if "x" not in data or "y" not in data:
            continue
        distance = ((data["x"] - lon) * scale) ** 2 + (data["y"] - lat) ** 2
        if best is None or distance < best[0]:
            best = (distance, node)
    return best[1] if best else None


def _times_from(
    graph: nx.MultiDiGraph, sources: dict[int, str]
) -> tuple[dict[int, float], dict[int, str]]:
    simple = nx.DiGraph()
    for u, v, data in graph.edges(data=True):
        seconds = _travel_time_seconds(data)
        if not simple.has_edge(u, v) or simple[u][v]["seconds"] > seconds:
            simple.add_edge(u, v, seconds=seconds)
    present = {node for node in sources if node in simple}
    if not present:
        return {}, {}
    distances, paths = nx.multi_source_dijkstra(simple, present, weight="seconds")
    origin = {node: sources[path[0]] for node, path in paths.items()}
    return distances, origin


class EmergencyAccessChecker:
    def __init__(
        self,
        graph: nx.MultiDiGraph,
        facilities: Iterable[ContextFacility],
        maximum_added_seconds: float = 60.0,
    ) -> None:
        self.graph = graph
        self.stations = [item for item in facilities if item.kind in EMERGENCY_KINDS]
        self.maximum_added_seconds = maximum_added_seconds

    def check(
        self,
        superblocks: Iterable[SuperblockImpactInput],
        modifications: list[StreetModification],
    ) -> EmergencyAccessSummary:
        items = list(superblocks)
        if not self.stations:
            return EmergencyAccessSummary(
                limitations=[
                    "No fire or ambulance stations were found in OpenStreetMap for this "
                    "area, so emergency response was not checked."
                ]
            )
        sources: dict[int, str] = {}
        for station in self.stations:
            node = _nearest_node(self.graph, station.coordinates.lon, station.coordinates.lat)
            if node is not None:
                sources.setdefault(node, station.name or station.kind.replace("_", " "))
        builder = TurnGraphBuilder(self.graph)
        baseline, origin = _times_from(self.graph, sources)
        planned, _ = _times_from(
            builder._prepare_graph(VehicleClass.EMERGENCY, modifications), sources
        )
        locked, _ = _times_from(builder._prepare_graph(VehicleClass.CAR, modifications), sources)

        results: list[EmergencyAccessResult] = []
        for item in items:
            nodes = {node for u, v, _key in item.interior_edges for node in (u, v)}
            nodes = {node for node in nodes if node in baseline}
            if not nodes:
                continue
            worst = max(nodes, key=lambda node: baseline[node])
            base = max(baseline[node] for node in nodes)
            plan = max(planned.get(node, math.inf) for node in nodes)
            lock = max(locked.get(node, math.inf) for node in nodes)
            results.append(
                EmergencyAccessResult(
                    superblock_id=item.superblock_id,
                    nearest_station=origin.get(worst),
                    baseline_seconds=round(base, 1),
                    planned_seconds=round(plan, 1) if math.isfinite(plan) else None,
                    planned_without_exemption_seconds=round(lock, 1)
                    if math.isfinite(lock)
                    else None,
                )
            )

        def added(values: list[float | None], base: list[float]) -> list[float]:
            return [
                (value - b) if value is not None else math.inf
                for value, b in zip(values, base, strict=True)
            ]

        base_times = [item.baseline_seconds for item in results]
        planned_added = added([item.planned_seconds for item in results], base_times)
        locked_added = added(
            [item.planned_without_exemption_seconds for item in results], base_times
        )
        finite = [value for value in planned_added if math.isfinite(value)]
        return EmergencyAccessSummary(
            checked=True,
            stations_used=len(sources),
            superblocks_checked=len(results),
            median_added_seconds=round(statistics.median(finite), 1) if finite else 0.0,
            maximum_added_seconds=round(max(finite), 1) if finite else 0.0,
            superblocks_over_threshold=sum(
                1 for value in planned_added if value > self.maximum_added_seconds
            ),
            superblocks_over_threshold_without_exemption=sum(
                1 for value in locked_added if value > self.maximum_added_seconds
            ),
            threshold_seconds=self.maximum_added_seconds,
            unreachable_superblocks=sum(1 for value in planned_added if not math.isfinite(value)),
            results=results,
            limitations=[
                "Travel times use free-flow road speeds from the nearest station node; "
                "real response also depends on signals, congestion and dispatch.",
            ],
        )
