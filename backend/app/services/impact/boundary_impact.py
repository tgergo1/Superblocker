"""Predict where through traffic goes when a superblock closes its interior.

The estimate works without an OD matrix. For each superblock it routes every
pair of entry points in different sectors twice on the local network (the
interior plus the boundary ring): once on today's streets and once with the
plan's filters, one-way changes and cuts applied. A baseline route that cuts
through the interior is a through trip; its planned route shows where that
trip goes instead. Interior volumes come from measured counts when present,
otherwise from the same road-class capacity and load model the street layer
uses. A share of the interior volume is treated as through traffic, and each
evaporation scenario removes part of the displaced trips before rerouting.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from app.models.schemas import (
    BoundaryRoadImpact,
    StreetModification,
    SuperblockTrafficImpact,
    TrafficImpactEstimate,
    TrafficImpactSettings,
    VehicleClass,
)
from app.services.assignment.network_assignment import _edge_capacity_vph
from app.services.network.turn_graph import TurnGraphBuilder, _travel_time_seconds

type DirectedEdge = tuple[int, int]


@dataclass
class SuperblockImpactInput:
    """The parts of one generated superblock the estimator needs."""

    superblock_id: str
    interior_edges: list[tuple[int, int, int]]
    boundary_edges: list[tuple[int, int, int]]
    entry_sectors: dict[int, int]
    modifications: list[StreetModification] = field(default_factory=list)


@dataclass
class _EdgeStats:
    osm_id: int
    name: str | None
    highway: str
    length_m: float
    baseline_vph: float
    capacity_vph: float
    measured: bool
    added: dict[str, float] = field(default_factory=dict)
    superblock_ids: set[str] = field(default_factory=set)


def _first(value: Any, default: Any) -> Any:
    if isinstance(value, (list, tuple, set)):
        return next(iter(value), default)
    return default if value is None else value


def _osm_id(data: Mapping[str, Any]) -> int:
    try:
        return max(0, int(_first(data.get("osmid"), 0)))
    except (TypeError, ValueError):
        return 0


def _highway(data: Mapping[str, Any]) -> str:
    return str(_first(data.get("highway"), "unclassified"))


# Typical peak-hour, per-direction flows on European urban streets by OSM class.
# They stand in for counts only; any measured volume on the edge wins.
DEFAULT_PEAK_VPH = {
    "motorway": 3_000.0,
    "trunk": 1_800.0,
    "primary": 1_000.0,
    "secondary": 700.0,
    "tertiary": 450.0,
    "unclassified": 150.0,
    "residential": 80.0,
    "living_street": 20.0,
    "service": 30.0,
}
ARTERIAL_CLASSES = frozenset({"motorway", "trunk", "primary", "secondary", "tertiary"})


def _base_class(highway: str) -> str:
    return highway.removesuffix("_link")


def edge_capacity_vph(data: Mapping[str, Any]) -> float:
    """Hourly directional screening capacity, shared with the assignment engine."""
    return _edge_capacity_vph(data)


def edge_baseline_vph(data: Mapping[str, Any]) -> tuple[float, bool]:
    """Measured peak-hour volume when present, else the class default."""
    measured = data.get("measured_volume_vph")
    if measured is not None:
        try:
            return max(0.0, float(measured)), True
        except (TypeError, ValueError):
            pass
    highway = _highway(data)
    volume = DEFAULT_PEAK_VPH.get(_base_class(highway), 60.0)
    if highway.endswith("_link"):
        volume *= 0.5
    return min(volume, 0.9 * edge_capacity_vph(data)), False


def _spread_order(nodes: list[int]) -> list[int]:
    """Nodes in an order that keeps any prefix spread along the list (0, 1/2, 1/4, 3/4...)."""

    def van_der_corput(index: int) -> float:
        value, base = 0.0, 0.5
        while index:
            value += base * (index & 1)
            index >>= 1
            base /= 2
        return value

    return [node for _key, node in sorted((van_der_corput(i), n) for i, n in enumerate(nodes))]


def sample_entry_pairs(
    entries: list[int], entry_sectors: dict[int, int], cap: int
) -> tuple[list[tuple[int, int]], int]:
    """Entry pairs in different sectors, sampled down to about ``cap``, and the full count.

    Every sampled entry is checked against all its destinations, so the cost
    stays at one shortest-path search per sampled entry. Entries are taken in
    turn from each sector, spread along it, rather than in node id order,
    which would check only one corner of a large superblock. Every sector
    keeps at least one entry even when that goes past ``cap``.
    """
    destinations = {
        a: [b for b in entries if b != a and entry_sectors[b] != entry_sectors[a]] for a in entries
    }
    total = sum(len(items) for items in destinations.values())
    if total <= cap:
        return [(a, b) for a in entries for b in destinations[a]], total
    by_sector: dict[int, list[int]] = defaultdict(list)
    for node in entries:
        by_sector[entry_sectors[node]].append(node)
    queues = [_spread_order(sorted(by_sector[sector])) for sector in sorted(by_sector)]
    chosen: list[int] = []
    count = 0
    for turn in range(max(len(queue) for queue in queues)):
        for queue in queues:
            if turn >= len(queue):
                continue
            node = queue[turn]
            size = len(destinations[node])
            if turn == 0 or count + size <= cap:
                chosen.append(node)
                count += size
    pairs = [(a, b) for a in sorted(chosen) for b in destinations[a]]
    return pairs, total


class BoundaryImpactEstimator:
    """Screen boundary-road load changes for a set of superblocks."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        settings: TrafficImpactSettings | None = None,
    ) -> None:
        self.graph = graph
        self.settings = settings or TrafficImpactSettings()

    def estimate(self, superblocks: Iterable[SuperblockImpactInput]) -> TrafficImpactEstimate:
        settings = self.settings
        scenario_names = [scenario.name for scenario in settings.scenarios]
        central = (
            settings.central_scenario
            if settings.central_scenario in scenario_names
            else scenario_names[0]
        )
        boundary_stats: dict[DirectedEdge, _EdgeStats] = {}
        superblock_results: list[SuperblockTrafficImpact] = []
        added_vkm = dict.fromkeys(scenario_names, 0.0)
        interior_vkm_removed = 0.0
        total_displaced = 0.0
        measured_edges = 0
        modeled_edges = 0

        for item in superblocks:
            result, stats, vkm, removed_vkm, measured, modeled = self._estimate_one(item)
            superblock_results.append(result)
            total_displaced += result.displaced_peak_vph
            interior_vkm_removed += removed_vkm
            measured_edges += measured
            modeled_edges += modeled
            for name in scenario_names:
                added_vkm[name] += vkm.get(name, 0.0)
            for edge, stat in stats.items():
                existing = boundary_stats.get(edge)
                if existing is None:
                    boundary_stats[edge] = stat
                    continue
                for name, value in stat.added.items():
                    existing.added[name] = existing.added.get(name, 0.0) + value
                existing.superblock_ids |= stat.superblock_ids

        roads = self._aggregate_roads(boundary_stats, scenario_names, central)
        traffic_basis = (
            "mixed"
            if measured_edges and modeled_edges
            else "measured_volume"
            if measured_edges
            else "modeled_topology"
        )
        limitations = [
            "Screening estimate: routes are local to each superblock, so trips that would "
            "leave the boundary ring entirely are kept on the nearest boundary roads.",
            f"{settings.through_traffic_share:.0%} of interior traffic on cut-through "
            "streets is assumed to be through traffic; residents' own trips stay.",
            "Evaporation scenarios bracket the London low-traffic-neighbourhood evidence "
            "(interior traffic -47%, boundary roads under +1%) without assuming it applies.",
        ]
        if traffic_basis != "measured_volume":
            limitations.append(
                "Volumes without counts come from road-class capacity and load defaults; "
                "import counts (for example Telraam) to calibrate."
            )
        if any(not road.arterial for road in roads):
            limitations.append(
                "Some superblocks are bounded by minor streets; traffic shown on them would "
                "partly use the wider arterial grid, which this local screening does not model."
            )
        unresolved = sum(item.unresolved_pairs for item in superblock_results)
        if unresolved:
            limitations.append(
                f"{unresolved} cut-through entry pairs have no legal local detour; their "
                "traffic is counted as displaced but not assigned."
            )
        sampled = [
            item for item in superblock_results if item.entry_pairs_total > item.entry_pairs_checked
        ]
        if sampled:
            limitations.append(
                f"{len(sampled)} large superblocks have more than "
                f"{settings.max_entry_pairs_per_superblock} entry pairs; a sample of entries "
                "spread over every side was checked against all their destinations."
            )
        return TrafficImpactEstimate(
            settings=settings,
            traffic_basis=traffic_basis,
            total_displaced_peak_vph=round(total_displaced, 1),
            added_boundary_vehicle_km_peak={
                name: round(value, 2) for name, value in added_vkm.items()
            },
            interior_vehicle_km_removed_peak=round(interior_vkm_removed, 2),
            maximum_percent_increase_central=max(
                (road.percent_increase_central for road in roads), default=0.0
            ),
            roads_over_threshold=sum(
                1
                for road in roads
                if road.percent_increase_central >= settings.high_increase_percent
            ),
            roads_over_capacity_central=sum(
                1 for road in roads if road.planned_volume_capacity_ratio_central > 1.0
            ),
            minor_roads_loaded=sum(
                1
                for road in roads
                if not road.arterial and road.added_peak_vph.get(central, 0.0) > 0
            ),
            boundary_roads=roads,
            superblocks=superblock_results,
            limitations=limitations,
        )

    def _local_graph(self, item: SuperblockImpactInput) -> nx.MultiDiGraph:
        local = nx.MultiDiGraph()
        for u, v, key in [*item.interior_edges, *item.boundary_edges]:
            if not self.graph.has_edge(u, v, key):
                continue
            for node in (u, v):
                if node not in local:
                    local.add_node(node, **self.graph.nodes[node])
            local.add_edge(u, v, key=key, **self.graph[u][v][key])
        return local

    @staticmethod
    def _collapse(graph: nx.MultiDiGraph) -> nx.DiGraph:
        """Keep the fastest parallel edge so paths map back to one street."""
        simple = nx.DiGraph()
        simple.add_nodes_from(graph.nodes)
        for u, v, key, data in graph.edges(keys=True, data=True):
            seconds = _travel_time_seconds(data)
            if simple.has_edge(u, v) and simple[u][v]["seconds"] <= seconds:
                continue
            simple.add_edge(u, v, seconds=seconds, key=key)
        return simple

    def _estimate_one(
        self, item: SuperblockImpactInput
    ) -> tuple[
        SuperblockTrafficImpact,
        dict[DirectedEdge, _EdgeStats],
        dict[str, float],
        float,
        int,
        int,
    ]:
        settings = self.settings
        local = self._local_graph(item)
        baseline = self._collapse(local)
        planned_multi = TurnGraphBuilder(local)._prepare_graph(
            VehicleClass.CAR, list(item.modifications)
        )
        planned = self._collapse(planned_multi)
        interior = {(u, v) for u, v, _key in item.interior_edges}
        boundary = {(u, v) for u, v, _key in item.boundary_edges} - interior

        entries = sorted(node for node in item.entry_sectors if node in baseline)
        pairs, total_pairs = sample_entry_pairs(
            entries, item.entry_sectors, settings.max_entry_pairs_per_superblock
        )
        sources = sorted({a for a, _b in pairs})
        base_paths = {
            source: nx.single_source_dijkstra(baseline, source, weight="seconds")
            for source in sources
        }
        plan_paths = {
            source: nx.single_source_dijkstra(planned, source, weight="seconds")
            if source in planned
            else ({}, {})
            for source in sources
        }

        def edge_data(graph: nx.DiGraph, u: int, v: int) -> dict[str, Any]:
            return local[u][v][graph[u][v]["key"]]

        cut_through: list[tuple[int, int, list[DirectedEdge]]] = []
        for a, b in pairs:
            _distances, paths = base_paths[a]
            if b not in paths:
                continue
            path_edges = list(zip(paths[b][:-1], paths[b][1:], strict=True))
            interior_used = [edge for edge in path_edges if edge in interior]
            if interior_used:
                cut_through.append((a, b, interior_used))

        users: dict[DirectedEdge, int] = defaultdict(int)
        for _a, _b, used in cut_through:
            for edge in used:
                users[edge] += 1
        interior_baseline = {}
        measured_count = 0
        modeled_count = 0
        for u, v in interior:
            if not baseline.has_edge(u, v):
                continue
            volume, measured = edge_baseline_vph(edge_data(baseline, u, v))
            interior_baseline[(u, v)] = volume
            if measured:
                measured_count += 1
            else:
                modeled_count += 1

        stats: dict[DirectedEdge, _EdgeStats] = {}
        removed_on_edge: dict[DirectedEdge, float] = defaultdict(float)
        added_vkm: dict[str, float] = defaultdict(float)
        removed_vkm = 0.0
        displaced = 0.0
        unresolved = 0
        detour_weighted = 0.0
        detour_volume = 0.0
        for a, b, used in cut_through:
            volume = min(
                settings.through_traffic_share * interior_baseline.get(edge, 0.0) / users[edge]
                for edge in used
            )
            if volume <= 0:
                continue
            plan_distances, plan_route = plan_paths[a]
            route_edges = (
                list(zip(plan_route[b][:-1], plan_route[b][1:], strict=True))
                if b in plan_route
                else []
            )
            if any(edge in interior for edge in route_edges):
                continue  # The plan still lets this trip cut through.
            displaced += volume
            for edge in used:
                removed_on_edge[edge] += volume
            removed_vkm += (
                volume
                * sum(float(edge_data(baseline, u, v).get("length", 0) or 0) for u, v in used)
                / 1000
            )
            if b not in plan_route:
                unresolved += 1
                continue
            detour = max(0.0, plan_distances[b] - base_paths[a][0][b])
            detour_weighted += detour * volume
            detour_volume += volume
            route_km = (
                sum(float(edge_data(planned, u, v).get("length", 0) or 0) for u, v in route_edges)
                / 1000
            )
            for scenario in settings.scenarios:
                rerouted = volume * (1 - scenario.evaporation_rate)
                added_vkm[scenario.name] += rerouted * route_km
                for edge in route_edges:
                    if edge not in boundary:
                        continue
                    stat = stats.get(edge)
                    if stat is None:
                        data = edge_data(planned, *edge)
                        baseline_volume, measured = edge_baseline_vph(data)
                        name = _first(data.get("name"), None)
                        stat = _EdgeStats(
                            osm_id=_osm_id(data),
                            name=str(name) if name else None,
                            highway=_highway(data),
                            length_m=float(data.get("length", 0) or 0),
                            baseline_vph=baseline_volume,
                            capacity_vph=edge_capacity_vph(data),
                            measured=measured,
                        )
                        stats[edge] = stat
                    stat.added[scenario.name] = stat.added.get(scenario.name, 0.0) + rerouted
                    stat.superblock_ids.add(item.superblock_id)

        interior_total = sum(interior_baseline.values())
        reduction = (
            min(100.0, sum(removed_on_edge.values()) / interior_total * 100)
            if interior_total > 0
            else 0.0
        )
        result = SuperblockTrafficImpact(
            superblock_id=item.superblock_id,
            entry_pairs_checked=len(pairs),
            entry_pairs_total=total_pairs,
            cut_through_pairs=len(cut_through),
            unresolved_pairs=unresolved,
            displaced_peak_vph=round(displaced, 1),
            interior_traffic_reduction_percent=round(reduction, 1),
            mean_detour_seconds=round(detour_weighted / detour_volume, 1) if detour_volume else 0.0,
        )
        return result, stats, dict(added_vkm), removed_vkm, measured_count, modeled_count

    @staticmethod
    def _aggregate_roads(
        boundary_stats: dict[DirectedEdge, _EdgeStats],
        scenario_names: list[str],
        central: str,
    ) -> list[BoundaryRoadImpact]:
        """Report each OSM way by its most loaded direction under the central case."""
        by_way: dict[int, BoundaryRoadImpact] = {}
        for stat in boundary_stats.values():
            if stat.osm_id <= 0:
                continue
            added_central = stat.added.get(central, 0.0)
            planned_ratio = (stat.baseline_vph + added_central) / stat.capacity_vph
            candidate = BoundaryRoadImpact(
                osm_id=stat.osm_id,
                name=stat.name,
                highway=stat.highway,
                baseline_peak_vph=round(stat.baseline_vph, 1),
                capacity_vph=stat.capacity_vph,
                added_peak_vph={
                    name: round(stat.added.get(name, 0.0), 1) for name in scenario_names
                },
                percent_increase_central=round(
                    added_central / stat.baseline_vph * 100 if stat.baseline_vph > 0 else 0.0,
                    1,
                ),
                baseline_volume_capacity_ratio=round(stat.baseline_vph / stat.capacity_vph, 3),
                planned_volume_capacity_ratio_central=round(planned_ratio, 3),
                measured=stat.measured,
                arterial=_base_class(stat.highway) in ARTERIAL_CLASSES,
                superblock_ids=sorted(stat.superblock_ids),
            )
            existing = by_way.get(stat.osm_id)
            if existing is None:
                by_way[stat.osm_id] = candidate
                continue
            merged_ids = sorted(set(existing.superblock_ids) | set(candidate.superblock_ids))
            if (
                candidate.planned_volume_capacity_ratio_central
                > existing.planned_volume_capacity_ratio_central
            ):
                existing = candidate
                by_way[stat.osm_id] = candidate
            existing.superblock_ids = merged_ids
        return sorted(
            by_way.values(),
            key=lambda road: (-road.percent_increase_central, road.osm_id),
        )
