"""Citywide traffic simulator: network capacity with and without superblocks.

The simulator answers one question: how much car traffic can the street
network carry before it jams, today and once the plan's filters, cuts and
one-way changes are in place?

It is a macroscopic static assignment, the method city transport models use
for strategic tests, built only on the street graph:

1. Demand. The area is cut into square zones. Each zone produces and attracts
   trips in proportion to the street length inside it (a stand-in for homes
   and jobs when no land-use data is supplied). Trips are spread with a
   gravity model over free-flow travel time. Each zone joins the streets at
   several junctions on its local streets through uncongested connectors, so
   trips start and end inside superblocks and spread over their exits.
2. Level. Demand level 1.0 is calibrated so today's average trip takes
   ``target_peak_travel_time_index`` times its empty-street time (or taken
   from ``peak_hour_trips``).
3. Assignment. Trips are assigned to user equilibrium with Frank-Wolfe and
   BPR volume-delay curves on both networks: today's, and the planned one
   with the plan's street changes applied.
4. Capacity. The same is repeated at higher demand levels. A network is full
   when the average trip takes ``breakdown_travel_time_index`` times as long
   as it does on today's empty network; the demand level where that happens
   is the network's capacity.

Shortest paths run in scipy's compiled Dijkstra over batches of origins; flows
are pushed up each shortest-path tree level by level with numpy, so a whole
city of tens of thousands of junctions is tractable without SUMO.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from app.models.schemas import CityPartition, StreetModification, VehicleClass
from app.models.simulation import (
    CapacitySimulationResult,
    CapacitySimulationSettings,
    SimulatedLink,
    SimulationLevelResult,
    SimulationScenarioSummary,
)
from app.services.assignment.network_assignment import _edge_capacity_vph
from app.services.network.turn_graph import (
    TurnGraphBuilder,
    _travel_time_seconds,
    edge_allows_vehicle,
)

logger = logging.getLogger(__name__)

MAIN_ROAD_TYPES = {
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
}
# How much a metre of street generates trips: homes and shops line local and
# main streets; motorways and their ramps have no frontage.
ACTIVITY_WEIGHT = {
    "motorway": 0.0,
    "motorway_link": 0.0,
    "trunk": 0.2,
    "trunk_link": 0.0,
    "primary": 0.6,
    "primary_link": 0.0,
    "secondary": 0.7,
    "secondary_link": 0.0,
    "tertiary": 0.8,
    "tertiary_link": 0.0,
}
ORIGIN_BATCH = 48
METRES_PER_DEGREE_LAT = 110_540.0
METRES_PER_DEGREE_LON = 111_320.0

ProgressCallback = Callable[[str, float], None]


def _first(value: Any, default: Any = None) -> Any:
    if isinstance(value, (list, tuple, set)):
        return next(iter(value), default)
    return default if value is None else value


def _highway(data: Mapping[str, Any]) -> str:
    return str(_first(data.get("highway"), "residential"))


def _osm_id(data: Mapping[str, Any]) -> int:
    try:
        return max(0, int(_first(data.get("osmid"), 0)))
    except (TypeError, ValueError):
        return 0


@dataclass
class _Links:
    """Directed links shared by both network states, one per node pair."""

    tail: np.ndarray
    head: np.ndarray
    free_time: np.ndarray
    capacity: np.ndarray
    length_m: np.ndarray
    is_main: np.ndarray
    activity: np.ndarray
    enabled_base: np.ndarray
    enabled_plan: np.ndarray
    data: list[dict[str, Any]]
    # Zone connectors: uncongested links between a zone's trip ends and streets.
    virtual: np.ndarray


@dataclass
class _Assignment:
    flows: np.ndarray
    times: np.ndarray
    served: float
    blocked: float
    gap: float
    iterations: int


@dataclass
class _Network:
    """Fixed inputs of one simulation run."""

    node_ids: list[int]
    lon: np.ndarray
    lat: np.ndarray
    links: _Links
    connectors: np.ndarray
    zone_count: int
    # One source and one sink node per zone, after the street junctions.
    sources: np.ndarray
    sinks: np.ndarray
    node_count: int
    # demand[i, j]: share of all trips going from zone i to zone j.
    demand: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))


class CapacitySimulator:
    """Compare the plan's network with today's under rising demand."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        partition: CityPartition | None = None,
        modifications: Iterable[StreetModification] | None = None,
        settings: CapacitySimulationSettings | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.graph = graph
        self.partition = partition
        if modifications is None:
            modifications = [
                modification
                for superblock in (partition.superblocks if partition else [])
                for modification in superblock.modifications
            ]
        self.modifications = list(modifications)
        self.settings = settings or CapacitySimulationSettings()
        self._progress = progress

    # ------------------------------------------------------------------ public

    def run(self) -> CapacitySimulationResult:
        started = time.monotonic()
        settings = self.settings
        network = self._build_network()
        links = network.links
        self._report("Building demand", 0.05)
        reference_seconds, base_trips, calibrated = self._build_demand(network)

        boundary_ids = set(self.partition.arterial_network) if self.partition else set()
        levels = settings.demand_levels
        summaries: dict[str, SimulationScenarioSummary] = {}
        detail: dict[str, _Assignment] = {}
        total_runs = 2 * len(levels)
        done = 0
        for name, enabled in (
            ("without_superblocks", links.enabled_base),
            ("with_superblocks", links.enabled_plan),
        ):
            results: list[SimulationLevelResult] = []
            warm: np.ndarray | None = None
            previous_level = None
            for level in levels:
                self._report(
                    f"Simulating {'with' if name == 'with_superblocks' else 'without'} "
                    f"superblocks at {level:g}x demand",
                    0.1 + 0.85 * done / total_runs,
                )
                start_flows = (
                    warm * (level / previous_level) if warm is not None and previous_level else None
                )
                assignment = self._equilibrium(network, enabled, base_trips * level, start_flows)
                results.append(
                    self._level_result(
                        level, base_trips * level, assignment, links, reference_seconds
                    )
                )
                if math.isclose(level, 1.0):
                    detail[name] = assignment
                warm, previous_level = assignment.flows, level
                done += 1
            capacity = self._capacity_multiplier(results)
            summaries[name] = SimulationScenarioSummary(
                name=name,
                levels=results,
                capacity_multiplier=capacity[0],
                capacity_trips=(capacity[0] * base_trips if capacity[0] is not None else None),
                capacity_reached=capacity[1],
                drivable_km=float(links.length_m[enabled].sum() / 1000.0),
            )

        baseline, planned = summaries["without_superblocks"], summaries["with_superblocks"]
        base_one = next(
            item for item in baseline.levels if math.isclose(item.demand_multiplier, 1.0)
        )
        plan_one = next(
            item for item in planned.levels if math.isclose(item.demand_multiplier, 1.0)
        )
        capacity_change = None
        if baseline.capacity_multiplier and planned.capacity_multiplier is not None:
            capacity_change = (
                planned.capacity_multiplier / baseline.capacity_multiplier - 1.0
            ) * 100.0

        result = CapacitySimulationResult(
            settings=settings,
            base_peak_hour_trips=base_trips,
            demand_calibrated=calibrated,
            zone_count=network.zone_count,
            connector_count=int(network.connectors.size),
            node_count=len(network.node_ids),
            link_count=int((links.enabled_base & ~links.virtual).sum()),
            links_closed_by_plan=int((links.enabled_base & ~links.enabled_plan).sum()),
            junctions_cut_off_by_plan=self._cut_off,
            baseline=baseline,
            planned=planned,
            capacity_change_percent=capacity_change,
            mean_trip_minutes_change_percent=_change(
                base_one.mean_trip_minutes, plan_one.mean_trip_minutes
            ),
            vehicle_km_change_percent=_change(base_one.vehicle_km, plan_one.vehicle_km),
            vehicle_hours_change_percent=_change(base_one.vehicle_hours, plan_one.vehicle_hours),
            links=self._map_links(
                network,
                detail["without_superblocks"],
                detail["with_superblocks"],
                boundary_ids,
            ),
            limitations=self._limitations(calibrated, baseline, planned),
        )
        result.processing_time_seconds = time.monotonic() - started
        self._report("Simulation complete", 1.0)
        return result

    # ----------------------------------------------------------- network build

    def _build_network(self) -> _Network:
        builder = TurnGraphBuilder(self.graph)
        base_graph = builder.road_graph(VehicleClass.CAR, [])
        plan_graph = builder.road_graph(VehicleClass.CAR, self.modifications)

        node_ids = [
            int(node) for node, data in self.graph.nodes(data=True) if "x" in data and "y" in data
        ]
        if len(node_ids) < 2:
            raise ValueError("the street network has too few junctions to simulate")
        index = {node: position for position, node in enumerate(node_ids)}
        lon = np.array([float(self.graph.nodes[node]["x"]) for node in node_ids])
        lat = np.array([float(self.graph.nodes[node]["y"]) for node in node_ids])

        records: dict[tuple[int, int], dict[str, Any]] = {}
        for state, graph in (("base", base_graph), ("plan", plan_graph)):
            for u, v, data in graph.edges(data=True):
                if u == v or u not in index or v not in index:
                    continue
                if not edge_allows_vehicle(data, VehicleClass.CAR):
                    continue
                pair = (index[u], index[v])
                travel = max(0.5, _travel_time_seconds(data))
                record = records.get(pair)
                if record is None:
                    record = records[pair] = {
                        "time": travel,
                        "data": data,
                        "capacity": {},
                        "base": False,
                        "plan": False,
                    }
                elif travel < record["time"]:
                    record["time"], record["data"] = travel, data
                record[state] = True
                # Parallel ways between the same junctions add their capacity.
                record["capacity"].setdefault(state, 0.0)
                record["capacity"][state] += _edge_capacity_vph(data)
        if not records:
            raise ValueError("the street network has no drivable links")

        pairs = list(records)
        values = [records[pair] for pair in pairs]
        data_list = [record["data"] for record in values]
        highways = [_highway(data) for data in data_list]
        lengths = np.array([max(1.0, float(data.get("length", 0) or 0)) for data in data_list])
        links = _Links(
            tail=np.array([pair[0] for pair in pairs], dtype=np.int64),
            head=np.array([pair[1] for pair in pairs], dtype=np.int64),
            free_time=np.array([record["time"] for record in values]),
            capacity=np.array(
                [
                    max(
                        record["capacity"].get("base", 0.0),
                        record["capacity"].get("plan", 0.0),
                        50.0,
                    )
                    for record in values
                ]
            ),
            length_m=lengths,
            is_main=np.array([highway in MAIN_ROAD_TYPES for highway in highways]),
            activity=lengths
            * np.array([ACTIVITY_WEIGHT.get(highway, 1.0) for highway in highways]),
            enabled_base=np.array([record["base"] for record in values]),
            enabled_plan=np.array([record["plan"] for record in values]),
            data=data_list,
            virtual=np.zeros(len(values), dtype=bool),
        )
        zones, connectors = self._zones(len(node_ids), lon, lat, links)
        zone_count = int(zones.max()) + 1
        street_nodes = len(node_ids)
        sources = street_nodes + np.arange(zone_count)
        sinks = street_nodes + zone_count + np.arange(zone_count)
        # Trips enter the streets from their zone at any of its connectors and
        # leave at any; equilibrium spreads them as the nearest exits fill up.
        extra = 2 * connectors.size
        links.tail = np.concatenate([links.tail, sources[zones], connectors])
        links.head = np.concatenate([links.head, connectors, sinks[zones]])
        links.free_time = np.concatenate([links.free_time, np.full(extra, 0.01)])
        links.capacity = np.concatenate([links.capacity, np.full(extra, 1e9)])
        links.length_m = np.concatenate([links.length_m, np.zeros(extra)])
        links.is_main = np.concatenate([links.is_main, np.zeros(extra, dtype=bool)])
        links.activity = np.concatenate([links.activity, np.zeros(extra)])
        links.enabled_base = np.concatenate([links.enabled_base, np.ones(extra, dtype=bool)])
        links.enabled_plan = np.concatenate([links.enabled_plan, np.ones(extra, dtype=bool)])
        links.virtual = np.concatenate([links.virtual, np.ones(extra, dtype=bool)])
        links.data = links.data + [{} for _ in range(extra)]
        centre_lon = np.bincount(zones, weights=lon[connectors]) / np.bincount(zones)
        centre_lat = np.bincount(zones, weights=lat[connectors]) / np.bincount(zones)
        return _Network(
            node_ids=node_ids,
            lon=np.concatenate([lon, centre_lon, centre_lon]),
            lat=np.concatenate([lat, centre_lat, centre_lat]),
            links=links,
            connectors=connectors,
            zone_count=zone_count,
            sources=sources,
            sinks=sinks,
            node_count=street_nodes + 2 * zone_count,
        )

    def _zones(
        self,
        node_count: int,
        lon: np.ndarray,
        lat: np.ndarray,
        links: _Links,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Grid zones over junctions every car can reach and leave in both networks.

        A filter is modelled by closing its whole street section, so a junction
        reached only through filtered sections drops out of the planned network
        even though the homes there keep their access from the far end. Trips
        start and end only where both networks connect, and the junctions the
        plan cuts off are counted for the report.
        """
        settings = self.settings
        base = links.enabled_base

        def core(enabled: np.ndarray) -> np.ndarray:
            matrix = csr_matrix(
                (np.ones(int(enabled.sum())), (links.tail[enabled], links.head[enabled])),
                shape=(node_count, node_count),
            )
            _count, labels = connected_components(matrix, directed=True, connection="strong")
            return labels == np.bincount(labels).argmax()

        base_core = core(base)
        in_core = base_core & core(links.enabled_plan)
        self._cut_off = int((base_core & ~in_core).sum())
        if in_core.sum() < 2:
            in_core = base_core

        activity = np.zeros(node_count)
        np.add.at(activity, links.tail[base], links.activity[base] / 2.0)
        np.add.at(activity, links.head[base], links.activity[base] / 2.0)
        local = np.zeros(node_count, dtype=bool)
        local_links = base & ~links.is_main & (links.activity > 0)
        local[links.tail[local_links]] = True
        local[links.head[local_links]] = True

        lat0 = float(lat[in_core].mean())
        x = (lon - float(lon[in_core].min())) * METRES_PER_DEGREE_LON * math.cos(math.radians(lat0))
        y = (lat - float(lat[in_core].min())) * METRES_PER_DEGREE_LAT

        size = settings.zone_size_m
        candidates = np.nonzero(in_core)[0]
        for _attempt in range(30):
            cell = (np.floor(x[candidates] / size).astype(np.int64) << 32) + np.floor(
                y[candidates] / size
            ).astype(np.int64)
            cells, cell_index = np.unique(cell, return_inverse=True)
            weights = np.bincount(cell_index, weights=activity[candidates], minlength=cells.size)
            if np.count_nonzero(weights) <= settings.max_zones:
                break
            size *= math.sqrt(np.count_nonzero(weights) / settings.max_zones) * 1.02

        connectors: list[int] = []
        zone_of: list[int] = []
        zone = 0
        order = np.argsort(cell_index, kind="stable")
        boundaries = np.searchsorted(cell_index[order], np.arange(cells.size + 1))
        for cell_number in range(cells.size):
            if weights[cell_number] <= 0:
                continue
            members = candidates[order[boundaries[cell_number] : boundaries[cell_number + 1]]]
            preferred = members[local[members]]
            pool = preferred if preferred.size else members
            centre_x = np.average(x[members], weights=activity[members] + 1e-9)
            centre_y = np.average(y[members], weights=activity[members] + 1e-9)
            chosen = [int(pool[np.argmin((x[pool] - centre_x) ** 2 + (y[pool] - centre_y) ** 2)])]
            # Farthest-point picks spread the remaining connectors over the zone.
            while len(chosen) < min(settings.connectors_per_zone, pool.size):
                distance = np.min(
                    [(x[pool] - x[node]) ** 2 + (y[pool] - y[node]) ** 2 for node in chosen],
                    axis=0,
                )
                chosen.append(int(pool[np.argmax(distance)]))
            connectors.extend(chosen)
            zone_of.extend([zone] * len(chosen))
            zone += 1
        self._zone_weights = weights[weights > 0]
        if zone < 2:
            raise ValueError("the area is too small to build at least two demand zones")
        return np.array(zone_of, dtype=np.int64), np.array(connectors, dtype=np.int64)

    # ------------------------------------------------------------------ demand

    def _build_demand(self, network: _Network) -> tuple[float, float, bool]:
        """Gravity demand shares, the level-1.0 trip total and the reference trip time."""
        settings = self.settings
        links = network.links

        # Zone-to-zone free-flow minutes on today's network.
        matrix = self._csr(network, links.enabled_base, links.free_time)
        minutes = dijkstra(matrix, directed=True, indices=network.sources)[:, network.sinks] / 60.0

        production = self._zone_weights / self._zone_weights.sum()
        with np.errstate(over="ignore", invalid="ignore"):
            attraction = production[None, :] * np.exp(-settings.distance_decay_per_minute * minutes)
        attraction[~np.isfinite(attraction)] = 0.0
        np.fill_diagonal(attraction, 0.0)
        totals = attraction.sum(axis=1, keepdims=True)
        shares = (
            np.divide(attraction, totals, out=np.zeros_like(attraction), where=totals > 0)
            * production[:, None]
        )
        shares /= max(shares.sum(), 1e-12)

        network.demand = shares

        unit = self._all_or_nothing(network, links.enabled_base, links.free_time, 1.0)
        reference_seconds = unit.cost / max(unit.served, 1e-12)
        if settings.peak_hour_trips is not None:
            return reference_seconds, float(settings.peak_hour_trips), False
        enabled = links.enabled_base & ~links.virtual
        main = enabled & links.is_main
        pool = main if links.length_m[main].sum() > 0 else enabled
        ratio = unit.flows[pool] / links.capacity[pool]
        mean_load = float(np.average(ratio, weights=links.length_m[pool]))
        if mean_load <= 0:
            raise ValueError("no trips reach the main roads; the area is too small to simulate")
        # First guess: main roads half full on all-or-nothing routes.
        trips = 0.5 / mean_load
        return reference_seconds, self._calibrate(network, reference_seconds, trips), True

    def _calibrate(self, network: _Network, reference_seconds: float, trips: float) -> float:
        """Find the trip total at which today's peak trip takes the target index.

        The excess delay (index - 1) grows roughly as a power of demand, so the
        search is a secant on log(index - 1) against log(trips), on short
        equilibria warm-started from the previous guess.
        """
        target = self.settings.target_peak_travel_time_index
        links = network.links
        iterations = min(4, self.settings.max_iterations)
        points: list[tuple[float, float]] = []
        flows: np.ndarray | None = None
        best = (math.inf, trips)
        for _attempt in range(6):
            start = flows * (trips / points[-1][0]) if flows is not None and points else None
            assignment = self._equilibrium(
                network, links.enabled_base, trips, start, max_iterations=iterations
            )
            flows = assignment.flows
            index = float((flows * assignment.times).sum()) / max(assignment.served, 1e-9)
            index /= reference_seconds
            if abs(index - target) < best[0]:
                best = (abs(index - target), trips)
            if abs(index - target) <= 0.02:
                break
            excess = max(index - 1.0, 1e-4)
            points.append((trips, math.log(excess)))
            if len(points) >= 2:
                (t0, e0), (t1, e1) = points[-2], points[-1]
                slope = (e1 - e0) / (math.log(t1) - math.log(t0)) if t1 != t0 else 2.0
                slope = min(max(slope, 0.5), 8.0)
            else:
                slope = 2.0
            factor = math.exp((math.log(target - 1.0) - math.log(excess)) / slope)
            trips *= min(max(factor, 0.1), 10.0)
        return best[1]

    # -------------------------------------------------------------- assignment

    @staticmethod
    def _csr(network: _Network, enabled: np.ndarray, costs: np.ndarray) -> csr_matrix:
        links = network.links
        size = network.node_count
        return csr_matrix(
            (costs[enabled], (links.tail[enabled], links.head[enabled])),
            shape=(size, size),
        )

    @dataclass
    class _Loaded:
        flows: np.ndarray
        cost: float
        served: float
        blocked: float

    def _all_or_nothing(
        self,
        network: _Network,
        enabled: np.ndarray,
        costs: np.ndarray,
        trips: float,
    ) -> _Loaded:
        """Load every trip on its current shortest path."""
        links = network.links
        size = network.node_count
        matrix = self._csr(network, enabled, costs)
        link_ids = np.nonzero(enabled)[0]
        keys = links.tail[link_ids] * size + links.head[link_ids]
        key_order = np.argsort(keys)
        sorted_keys = keys[key_order]

        flows = np.zeros(links.tail.size)
        cost = served = blocked = 0.0
        sinks = network.sinks
        for start in range(0, network.zone_count, ORIGIN_BATCH):
            zones = np.arange(start, min(start + ORIGIN_BATCH, network.zone_count))
            distances, predecessors = dijkstra(
                matrix, directed=True, indices=network.sources[zones], return_predecessors=True
            )
            demand = network.demand[zones] * trips
            to_sinks = distances[:, sinks]
            reach = np.isfinite(to_sinks)
            blocked += float(demand[~reach].sum())
            demand = np.where(reach, demand, 0.0)
            served += float(demand.sum())
            cost += float((demand * np.where(reach, to_sinks, 0.0)).sum())
            loads = np.zeros((zones.size, size))
            loads[:, sinks] = demand
            child, parent, carried = _tree_loads(predecessors, loads)
            if child.size == 0:
                continue
            position = np.searchsorted(sorted_keys, parent * size + child)
            # Many origins share a link, so accumulate rather than assign.
            flows += np.bincount(
                link_ids[key_order[position]], weights=carried, minlength=flows.size
            )
        return self._Loaded(flows=flows, cost=cost, served=served, blocked=blocked)

    def _bpr(self, links: _Links, flows: np.ndarray) -> np.ndarray:
        ratio = flows / links.capacity
        return links.free_time * (1.0 + self.settings.bpr_alpha * ratio**self.settings.bpr_beta)

    def _equilibrium(
        self,
        network: _Network,
        enabled: np.ndarray,
        trips: float,
        start_flows: np.ndarray | None,
        max_iterations: int | None = None,
    ) -> _Assignment:
        """Frank-Wolfe user equilibrium with an exact line search."""
        settings = self.settings
        links = network.links
        if start_flows is None:
            loaded = self._all_or_nothing(network, enabled, links.free_time, trips)
            flows = loaded.flows
        else:
            flows = np.where(enabled, start_flows, 0.0)
        gap = float("inf")
        iterations = 0
        served = blocked = 0.0
        for iteration in range(1, (max_iterations or settings.max_iterations) + 1):
            iterations = iteration
            times = self._bpr(links, flows)
            target = self._all_or_nothing(network, enabled, times, trips)
            served, blocked = target.served, target.blocked
            total = float((flows * times).sum())
            gap = max(0.0, (total - target.cost) / total) if total > 0 else 0.0
            if gap <= settings.convergence_gap:
                break
            direction = target.flows - flows
            step = self._line_search(links, flows, direction)
            flows = flows + step * direction
        return _Assignment(
            flows=flows,
            times=self._bpr(links, flows),
            served=served,
            blocked=blocked,
            gap=gap,
            iterations=iterations,
        )

    def _line_search(self, links: _Links, flows: np.ndarray, direction: np.ndarray) -> float:
        low, high = 0.0, 1.0
        if float((direction * self._bpr(links, flows + direction)).sum()) <= 0:
            return 1.0
        for _ in range(24):
            middle = (low + high) / 2
            slope = float((direction * self._bpr(links, flows + middle * direction)).sum())
            if slope > 0:
                high = middle
            else:
                low = middle
        return (low + high) / 2

    # ----------------------------------------------------------------- results

    def _level_result(
        self,
        level: float,
        trips: float,
        assignment: _Assignment,
        links: _Links,
        reference_seconds: float,
    ) -> SimulationLevelResult:
        real = ~links.virtual
        flows, times = assignment.flows[real], assignment.times[real]
        vehicle_seconds = float((flows * times).sum())
        vehicle_km = float((flows * links.length_m[real]).sum() / 1000.0)
        vehicle_hours = vehicle_seconds / 3600.0
        served = max(assignment.served, 1e-9)
        ratio = flows / links.capacity[real]
        mean_seconds = vehicle_seconds / served if assignment.served > 0 else 0.0
        return SimulationLevelResult(
            demand_multiplier=level,
            trips=trips,
            served_trips=assignment.served,
            blocked_trips=assignment.blocked,
            vehicle_km=vehicle_km,
            vehicle_hours=vehicle_hours,
            mean_trip_minutes=mean_seconds / 60.0,
            mean_speed_kph=vehicle_km / vehicle_hours if vehicle_hours > 0 else 0.0,
            travel_time_index=mean_seconds / reference_seconds if reference_seconds > 0 else 0.0,
            congested_km=float(
                links.length_m[real][ratio > self.settings.congested_volume_capacity_ratio].sum()
                / 1000
            ),
            over_capacity_km=float(links.length_m[real][ratio > 1.0].sum() / 1000),
            relative_gap=assignment.gap if math.isfinite(assignment.gap) else 1.0,
            iterations=assignment.iterations,
        )

    def _capacity_multiplier(
        self, results: list[SimulationLevelResult]
    ) -> tuple[float | None, bool]:
        threshold = self.settings.breakdown_travel_time_index
        previous: SimulationLevelResult | None = None
        for item in results:
            if item.travel_time_index >= threshold:
                if previous is None:
                    return item.demand_multiplier, True
                span = item.travel_time_index - previous.travel_time_index
                share = (threshold - previous.travel_time_index) / span if span > 0 else 0.0
                return (
                    previous.demand_multiplier
                    + share * (item.demand_multiplier - previous.demand_multiplier),
                    True,
                )
            previous = item
        return None, False

    def _map_links(
        self,
        network: _Network,
        baseline: _Assignment,
        planned: _Assignment,
        boundary_ids: set[int],
    ) -> list[SimulatedLink]:
        settings = self.settings
        if settings.max_map_links == 0:
            return []
        links = network.links
        grouped: dict[tuple[int, int], list[int]] = {}
        for link in range(links.tail.size):
            if links.virtual[link]:
                continue
            if not links.enabled_base[link] and not links.enabled_plan[link]:
                continue
            tail, head = int(links.tail[link]), int(links.head[link])
            grouped.setdefault((min(tail, head), max(tail, head)), []).append(link)

        rows: list[tuple[float, SimulatedLink]] = []
        for (first, second), members in grouped.items():
            base_volume = float(baseline.flows[members].sum())
            plan_volume = float(planned.flows[members].sum())
            closed = all(
                links.enabled_base[link] and not links.enabled_plan[link] for link in members
            )
            if max(base_volume, plan_volume) < settings.min_map_volume_vph and not closed:
                continue
            capacities = links.capacity[members]
            data = links.data[members[0]]
            rows.append(
                (
                    max(base_volume, plan_volume, abs(plan_volume - base_volume)),
                    SimulatedLink(
                        coordinates=self._coordinates(network, data, first, second),
                        osm_id=_osm_id(data),
                        name=(str(_first(data.get("name"))) if data.get("name") else None),
                        highway=_highway(data),
                        baseline_vph=round(base_volume, 1),
                        planned_vph=round(plan_volume, 1),
                        capacity_vph=float(capacities.max()),
                        baseline_vc=round(float((baseline.flows[members] / capacities).max()), 3),
                        planned_vc=round(float((planned.flows[members] / capacities).max()), 3),
                        closed_in_plan=closed,
                        is_boundary_road=_osm_id(data) in boundary_ids,
                    ),
                )
            )
        rows.sort(key=lambda item: item[0], reverse=True)
        return [link for _score, link in rows[: settings.max_map_links]]

    @staticmethod
    def _coordinates(
        network: _Network, data: Mapping[str, Any], first: int, second: int
    ) -> list[list[float]]:
        geometry = data.get("geometry")
        if geometry is not None and hasattr(geometry, "coords"):
            return [[round(x, 6), round(y, 6)] for x, y in geometry.coords]
        return [
            [round(float(network.lon[first]), 6), round(float(network.lat[first]), 6)],
            [round(float(network.lon[second]), 6), round(float(network.lat[second]), 6)],
        ]

    def _limitations(
        self,
        calibrated: bool,
        baseline: SimulationScenarioSummary,
        planned: SimulationScenarioSummary,
    ) -> list[str]:
        notes = [
            "Static peak-hour equilibrium with BPR link delays: junction signals, queues "
            "spilling back and lane changes are not modelled (export to SUMO for that).",
            "Trips are generated from street length per zone, not from homes and jobs; "
            "through traffic entering from outside the area is not included.",
            "Demand is the same with and without the plan; trips that would vanish or "
            "switch to walking, cycling or transit are not subtracted, so the plan's "
            "effect on car traffic is a worst case.",
            "Turn restrictions are not modelled in this citywide test; the route validator "
            "and plan validation check them.",
        ]
        if calibrated:
            notes.append(
                "Demand level 1.0 is calibrated so the average trip today takes "
                f"{self.settings.target_peak_travel_time_index:g}x as long as on empty streets; "
                "set peak_hour_trips to anchor it to counted traffic."
            )
        if self._cut_off:
            notes.append(
                f"{self._cut_off:,} junctions have no car route in and out once every "
                "filtered street section is closed; trips start and end only at "
                "junctions both networks reach."
            )
        if not baseline.capacity_reached or not planned.capacity_reached:
            notes.append(
                "At least one network never reached the breakdown threshold in the tested "
                "range; its capacity is higher than the largest demand level."
            )
        if any(item.blocked_trips > 0 for item in planned.levels):
            notes.append("Some trips have no legal route in the planned network.")
        return notes

    def _report(self, message: str, share: float) -> None:
        if self._progress is not None:
            self._progress(message, share)


def _change(before: float, after: float) -> float:
    return (after / before - 1.0) * 100.0 if before > 0 else 0.0


def _tree_loads(
    predecessors: np.ndarray, loads: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Push node demand up shortest-path trees.

    ``predecessors`` and ``loads`` are (origins, nodes). Returns, for every tree
    edge that carries traffic, its child node, parent node and the trips that
    cross it. Depths come from pointer jumping, then each depth level is added
    to its parents in one vectorised step, deepest first.
    """
    origins, size = predecessors.shape
    flat_parent = predecessors.astype(np.int64).ravel()
    valid = flat_parent >= 0
    offsets = np.repeat(np.arange(origins, dtype=np.int64) * size, size)
    parent = np.where(valid, flat_parent + offsets, -1)

    depth = valid.astype(np.int64)
    jump = parent.copy()
    while True:
        active = np.nonzero(jump >= 0)[0]
        if active.size == 0:
            break
        targets = jump[active]
        # Both right-hand sides read the arrays before this round's writes.
        depth[active] += depth[targets]
        jump[active] = jump[targets]

    carried = loads.ravel().copy()
    members = np.nonzero(valid)[0]
    if members.size == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64), np.array([])
    by_depth = members[np.argsort(depth[members], kind="stable")[::-1]]
    depths = depth[by_depth]
    level_breaks = np.flatnonzero(np.diff(depths)) + 1
    for level in np.split(by_depth, level_breaks):
        values = carried[level]
        keep = values > 0
        if keep.any():
            np.add.at(carried, parent[level[keep]], values[keep])

    used = members[carried[members] > 0]
    return used % size, parent[used] % size, carried[used]
