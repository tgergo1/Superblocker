"""Citywide traffic simulator: network capacity with and without superblocks.

The simulator answers two questions: how does peak car traffic move once the
plan's filters, gates and one-way changes are in place, and how much traffic
can the street network carry before it jams, today and with the plan?

It is a macroscopic static assignment, the method city transport models use
for strategic tests:

1. Demand. The area is cut into square zones. With a population grid and a
   jobs layer, a zone produces morning-peak trips from its residents and
   attracts them to its jobs, shops and schools; without them, both follow the
   street length inside the zone. Trips are spread with a gravity model over
   free-flow travel time. A share of trips enters or leaves the area through
   gateway zones where its motorways, trunk and primary roads cross the edge.
   Each zone joins the streets at several junctions on its local streets, so
   trips start and end inside superblocks and spread over their exits.
2. Junctions. Routes are found movement by movement on a turn-expanded graph:
   OSM turn restrictions apply, U-turns are allowed only at dead ends, and each
   movement pays a fixed delay for the junction's control (signals or
   priority). Destination-only gates stay open to cars in the plan behind a
   routing penalty, so through traffic avoids them and residents still drive in.
3. Level. Demand level 1.0 is calibrated so today's average trip takes
   ``target_peak_travel_time_index`` times its empty-street time (or taken
   from ``peak_hour_trips``).
4. Assignment. Trips are assigned to user equilibrium with Frank-Wolfe and
   BPR volume-delay curves on both networks: today's, and the planned one.
5. Response. With a demand elasticity, trips between each pair of zones then
   scale with the plan's change in their travel time, and the planned network
   is assigned again until the two agree.
6. Capacity. The fixed-demand assignment is repeated at higher demand levels.
   A network is full when the average trip takes
   ``breakdown_travel_time_index`` times as long as it does on today's empty
   network; the demand level where that happens is the network's capacity.

Shortest paths run in scipy's compiled Dijkstra over batches of origins; flows
are pushed up each shortest-path tree level by level with numpy, so a whole
city of tens of thousands of junctions is tractable without SUMO.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

from app.models.schemas import (
    CityPartition,
    StreetModification,
    TurnRestriction,
    VehicleClass,
)
from app.models.simulation import (
    CapacitySimulationResult,
    CapacitySimulationSettings,
    SimulatedLink,
    SimulationLevelResult,
    SimulationScenarioSummary,
)
from app.services.assignment.network_assignment import _edge_capacity_vph
from app.services.importers.population import PopulationGrid
from app.services.network.turn_graph import (
    TurnGraphBuilder,
    _class_matches,
    _osm_ids,
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
# Roads that carry traffic into and out of the area at its edge.
GATEWAY_ROAD_TYPES = {"motorway", "trunk", "primary", "motorway_link", "trunk_link"}
# Road rank for priority at unsignalised junctions: the lower rank gives way.
ROAD_RANK = {
    "motorway": 7,
    "motorway_link": 6,
    "trunk": 6,
    "trunk_link": 5,
    "primary": 5,
    "primary_link": 4,
    "secondary": 4,
    "secondary_link": 3,
    "tertiary": 3,
    "tertiary_link": 2,
    "unclassified": 1,
    "residential": 1,
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


def _lanes(data: Mapping[str, Any]) -> float:
    try:
        return max(1.0, float(str(_first(data.get("lanes"), 1)).split(";")[0]))
    except (TypeError, ValueError):
        return 1.0


@dataclass
class _Links:
    """Directed street links shared by both network states, one per node pair."""

    tail: np.ndarray
    head: np.ndarray
    free_time: np.ndarray
    capacity: np.ndarray
    length_m: np.ndarray
    is_main: np.ndarray
    rank: np.ndarray
    activity: np.ndarray
    enabled_base: np.ndarray
    enabled_plan: np.ndarray
    # Open in the plan only to destination traffic (camera gates).
    gated: np.ndarray
    start_bearing: np.ndarray
    end_bearing: np.ndarray
    osm_ids: list[set[int]]
    data: list[dict[str, Any]]


@dataclass
class _Arcs:
    """Arcs of the routing graph.

    Routing nodes are street links (a car on a link), then one source and one
    sink per zone. An arc into a street link costs that link's travel time
    plus a fixed junction delay; ``link`` is -1 for arcs into a sink.
    """

    tail: np.ndarray
    head: np.ndarray
    link: np.ndarray
    delay: np.ndarray
    enabled_base: np.ndarray
    enabled_plan: np.ndarray
    penalty_plan: np.ndarray


@dataclass
class _Assignment:
    flows: np.ndarray  # street link flows, vehicles per hour
    arc_flows: np.ndarray
    times: np.ndarray
    served: float
    blocked: float
    gap: float
    iterations: int
    # Zone-to-zone travel minutes on the final routes (penalties excluded).
    skims: np.ndarray | None = None


@dataclass
class _Network:
    """Fixed inputs of one simulation run."""

    node_ids: list[int]
    lon: np.ndarray
    lat: np.ndarray
    links: _Links
    arcs: _Arcs
    connectors: np.ndarray
    zone_count: int
    sources: np.ndarray
    sinks: np.ndarray
    node_count: int
    # demand[i, j]: share of all trips going from zone i to zone j.
    demand: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    gateway_count: int = 0
    movements: int = 0
    restrictions_applied: int = 0
    signalised: int = 0


class CapacitySimulator:
    """Compare the plan's network with today's under rising demand."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        partition: CityPartition | None = None,
        modifications: Iterable[StreetModification] | None = None,
        settings: CapacitySimulationSettings | None = None,
        progress: ProgressCallback | None = None,
        residents: PopulationGrid | None = None,
        jobs: PopulationGrid | None = None,
        turn_restrictions: Iterable[TurnRestriction] = (),
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
        self.residents = residents if residents is not None and residents.residents.size else None
        self.jobs = jobs if jobs is not None and jobs.residents.size else None
        self.turn_restrictions = list(turn_restrictions)
        if partition is not None:
            self.turn_restrictions += list(partition.turn_restrictions)
        self._cut_off = 0
        self._external_share = 0.0
        self._land_use_dropped = 0.0

    # ------------------------------------------------------------------ public

    def run(self) -> CapacitySimulationResult:
        started = time.monotonic()
        settings = self.settings
        self._report("Building the street network", 0.0)
        network = self._build_network()
        links = network.links
        self._report("Building demand", 0.05)
        reference_seconds, base_trips, calibrated = self._build_demand(network)

        boundary_ids = set(self.partition.arterial_network) if self.partition else set()
        levels = settings.demand_levels
        summaries: dict[str, SimulationScenarioSummary] = {}
        detail: dict[str, _Assignment] = {}
        elastic = settings.demand_elasticity < 0
        total_runs = 2 * len(levels) + (settings.elastic_iterations if elastic else 0)
        done = 0
        for name, plan in (("without_superblocks", False), ("with_superblocks", True)):
            results: list[SimulationLevelResult] = []
            warm: tuple[np.ndarray, np.ndarray] | None = None
            previous_level = None
            for level in levels:
                self._report(
                    f"Simulating {'with' if plan else 'without'} superblocks at {level:g}x demand",
                    0.1 + 0.85 * done / total_runs,
                )
                start = (
                    (warm[0] * (level / previous_level), warm[1] * (level / previous_level))
                    if warm is not None and previous_level
                    else None
                )
                demand = network.demand * base_trips * level
                assignment = self._equilibrium(
                    network, plan, demand, start, skims=math.isclose(level, 1.0)
                )
                results.append(self._level_result(level, float(demand.sum()), assignment, network))
                results[-1].travel_time_index = (
                    results[-1].mean_trip_minutes * 60.0 / reference_seconds
                    if reference_seconds > 0
                    else 0.0
                )
                if math.isclose(level, 1.0):
                    detail[name] = assignment
                warm, previous_level = (assignment.flows, assignment.arc_flows), level
                done += 1
            capacity = self._capacity_multiplier(results)
            enabled = links.enabled_plan if plan else links.enabled_base
            summaries[name] = SimulationScenarioSummary(
                name=name,
                levels=results,
                capacity_multiplier=capacity[0],
                capacity_trips=(capacity[0] * base_trips if capacity[0] is not None else None),
                capacity_reached=capacity[1],
                drivable_km=float(links.length_m[enabled].sum() / 1000.0),
            )

        baseline, planned = summaries["without_superblocks"], summaries["with_superblocks"]
        base_one = next(i for i in baseline.levels if math.isclose(i.demand_multiplier, 1.0))
        plan_one = next(i for i in planned.levels if math.isclose(i.demand_multiplier, 1.0))

        elastic_result: SimulationLevelResult | None = None
        elastic_assignment: _Assignment | None = None
        if elastic:
            elastic_assignment, trips = self._elastic(
                network,
                base_trips,
                detail["without_superblocks"],
                detail["with_superblocks"],
                lambda k: self._report(
                    f"Letting trips respond to the plan (round {k})",
                    0.1 + 0.85 * (done + k) / total_runs,
                ),
            )
            elastic_result = self._level_result(1.0, trips, elastic_assignment, network)
            elastic_result.travel_time_index = (
                elastic_result.mean_trip_minutes * 60.0 / reference_seconds
                if reference_seconds > 0
                else 0.0
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
            link_count=int(links.enabled_base.sum()),
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
            planned_elastic=elastic_result,
            trips_not_made_share=(
                max(0.0, 1.0 - elastic_result.trips / base_one.trips)
                if elastic_result is not None and base_one.trips > 0
                else None
            ),
            elastic_mean_trip_minutes_change_percent=(
                _change(base_one.mean_trip_minutes, elastic_result.mean_trip_minutes)
                if elastic_result is not None
                else None
            ),
            elastic_vehicle_km_change_percent=(
                _change(base_one.vehicle_km, elastic_result.vehicle_km)
                if elastic_result is not None
                else None
            ),
            demand_source=self._demand_source(),
            external_trip_share=self._external_share,
            gateway_count=network.gateway_count,
            turn_model=settings.turn_model,
            movements=network.movements,
            turn_restrictions_applied=network.restrictions_applied,
            signalised_junctions=network.signalised,
            destination_gates=int((links.gated & links.enabled_plan).sum()),
            links=self._map_links(
                network,
                detail["without_superblocks"],
                detail["with_superblocks"],
                elastic_assignment,
                boundary_ids,
            ),
            limitations=self._limitations(calibrated, baseline, planned),
        )
        result.processing_time_seconds = time.monotonic() - started
        self._report("Simulation complete", 1.0)
        return result

    # ----------------------------------------------------------- network build

    def _build_network(self) -> _Network:
        settings = self.settings
        builder = TurnGraphBuilder(self.graph)
        base_graph = builder.road_graph(VehicleClass.CAR, [])
        plan_graph = builder.road_graph(VehicleClass.CAR, self.modifications)
        # Residents' network: destination-only gates are open to them.
        permit_graph = builder.road_graph(VehicleClass.PERMIT, self.modifications)

        node_ids = [
            int(node) for node, data in self.graph.nodes(data=True) if "x" in data and "y" in data
        ]
        if len(node_ids) < 2:
            raise ValueError("the street network has too few junctions to simulate")
        index = {node: position for position, node in enumerate(node_ids)}
        lon = np.array([float(self.graph.nodes[node]["x"]) for node in node_ids])
        lat = np.array([float(self.graph.nodes[node]["y"]) for node in node_ids])

        records: dict[tuple[int, int], dict[str, Any]] = {}
        for state, graph in (("base", base_graph), ("plan", plan_graph), ("permit", permit_graph)):
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
                        "permit": False,
                    }
                elif travel < record["time"]:
                    record["time"], record["data"] = travel, data
                record[state] = True
                # Parallel ways between the same junctions add their capacity.
                record["capacity"].setdefault(state, 0.0)
                record["capacity"][state] += _edge_capacity_vph(data)
        # A permit-only link must exist today too; anything else is a two-way
        # change already in the plan state.
        records = {
            pair: record for pair, record in records.items() if record["base"] or record["plan"]
        }
        if not records:
            raise ValueError("the street network has no drivable links")

        pairs = list(records)
        values = [records[pair] for pair in pairs]
        data_list = [record["data"] for record in values]
        highways = [_highway(data) for data in data_list]
        lengths = np.array([max(1.0, float(data.get("length", 0) or 0)) for data in data_list])
        tail = np.array([pair[0] for pair in pairs], dtype=np.int64)
        head = np.array([pair[1] for pair in pairs], dtype=np.int64)
        enabled_plan = np.array([record["plan"] for record in values])
        gated = np.array([record["permit"] and not record["plan"] for record in values])
        start_bearing, end_bearing = self._bearings(data_list, tail, head, lon, lat)
        links = _Links(
            tail=tail,
            head=head,
            free_time=np.array([record["time"] for record in values]),
            capacity=np.array(
                [
                    max(
                        record["capacity"].get("base", 0.0),
                        record["capacity"].get("plan", 0.0),
                        record["capacity"].get("permit", 0.0),
                        50.0,
                    )
                    for record in values
                ]
            ),
            length_m=lengths,
            is_main=np.array([highway in MAIN_ROAD_TYPES for highway in highways]),
            rank=np.array([ROAD_RANK.get(highway, 0) for highway in highways]),
            activity=lengths
            * np.array([ACTIVITY_WEIGHT.get(highway, 1.0) for highway in highways]),
            enabled_base=np.array([record["base"] for record in values]),
            enabled_plan=enabled_plan | gated,
            gated=gated,
            start_bearing=start_bearing,
            end_bearing=end_bearing,
            osm_ids=[_osm_ids(data.get("osmid")) for data in data_list],
            data=data_list,
        )
        street_nodes = len(node_ids)
        movements, restricted, signalised = self._movements(links, street_nodes)

        zones, connectors, gateways = self._zones(street_nodes, lon, lat, links, movements)
        zone_count = int(zones.max()) + 1
        link_count = links.tail.size
        sources = link_count + np.arange(zone_count)
        sinks = link_count + zone_count + np.arange(zone_count)

        # Arcs: movements between street links, zone sources onto the links
        # leaving a connector, links arriving at a connector into zone sinks.
        out_links: dict[int, list[int]] = defaultdict(list)
        in_links: dict[int, list[int]] = defaultdict(list)
        for link in range(link_count):
            out_links[int(links.tail[link])].append(link)
            in_links[int(links.head[link])].append(link)
        source_tail, source_head = [], []
        sink_tail, sink_head = [], []
        for zone, node in zip(zones.tolist(), connectors.tolist(), strict=True):
            for link in out_links.get(node, []):
                source_tail.append(sources[zone])
                source_head.append(link)
            for link in in_links.get(node, []):
                sink_tail.append(link)
                sink_head.append(sinks[zone])
        m_from, m_to, m_delay = movements
        arc_tail = np.concatenate([m_from, np.array(source_tail, dtype=np.int64), sink_tail])
        arc_head = np.concatenate([m_to, np.array(source_head, dtype=np.int64), sink_head])
        arc_link = np.concatenate(
            [m_to, np.array(source_head, dtype=np.int64), np.full(len(sink_tail), -1)]
        ).astype(np.int64)
        arc_delay = np.concatenate([m_delay, np.zeros(len(source_tail) + len(sink_tail))])
        from_link = np.concatenate(
            [m_from, np.full(len(source_tail), -1), np.array(sink_tail, dtype=np.int64)]
        ).astype(np.int64)

        def state(enabled: np.ndarray) -> np.ndarray:
            into = np.where(arc_link >= 0, enabled[np.maximum(arc_link, 0)], True)
            out_of = np.where(from_link >= 0, enabled[np.maximum(from_link, 0)], True)
            return into & out_of

        penalty = np.where(
            arc_link >= 0, links.gated[np.maximum(arc_link, 0)] * settings.gate_penalty_s, 0.0
        )
        # A route already on a gated link does not pay again to continue on it.
        same_gate = (from_link >= 0) & links.gated[np.maximum(from_link, 0)]
        penalty = np.where(same_gate, 0.0, penalty)
        arcs = _Arcs(
            tail=arc_tail.astype(np.int64),
            head=arc_head.astype(np.int64),
            link=arc_link,
            delay=arc_delay,
            enabled_base=state(links.enabled_base),
            enabled_plan=state(links.enabled_plan),
            penalty_plan=penalty,
        )
        return _Network(
            node_ids=node_ids,
            lon=lon,
            lat=lat,
            links=links,
            arcs=arcs,
            connectors=connectors,
            zone_count=zone_count,
            sources=sources,
            sinks=sinks,
            node_count=link_count + 2 * zone_count,
            gateway_count=gateways,
            movements=int(m_from.size),
            restrictions_applied=restricted,
            signalised=signalised,
        )

    @staticmethod
    def _bearings(
        data_list: list[dict[str, Any]],
        tail: np.ndarray,
        head: np.ndarray,
        lon: np.ndarray,
        lat: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Compass bearing at the start and at the end of each link, in degrees."""
        cos_lat = math.cos(math.radians(float(lat.mean())))

        def bearing(x0, y0, x1, y1):
            return (math.degrees(math.atan2((x1 - x0) * cos_lat, y1 - y0)) + 360.0) % 360.0

        start = np.zeros(tail.size)
        end = np.zeros(tail.size)
        for position, data in enumerate(data_list):
            geometry = data.get("geometry")
            ax, ay = float(lon[tail[position]]), float(lat[tail[position]])
            bx, by = float(lon[head[position]]), float(lat[head[position]])
            coords = (
                list(geometry.coords)
                if geometry is not None and hasattr(geometry, "coords")
                else []
            )
            if len(coords) >= 2:
                # Shapes may run either way; orient them from tail to head.
                first, _last = coords[0], coords[-1]
                if (first[0] - bx) ** 2 + (first[1] - by) ** 2 < (first[0] - ax) ** 2 + (
                    first[1] - ay
                ) ** 2:
                    coords = coords[::-1]
                start[position] = bearing(*coords[0], *coords[1])
                end[position] = bearing(*coords[-2], *coords[-1])
            else:
                start[position] = end[position] = bearing(ax, ay, bx, by)
        return start, end

    def _movements(
        self, links: _Links, node_count: int
    ) -> tuple[tuple[np.ndarray, np.ndarray, np.ndarray], int, int]:
        """Legal movements between links at every junction, with their delays."""
        settings = self.settings
        out_links: dict[int, list[int]] = defaultdict(list)
        in_links: dict[int, list[int]] = defaultdict(list)
        for link in range(links.tail.size):
            if links.enabled_base[link] or links.enabled_plan[link]:
                out_links[int(links.tail[link])].append(link)
                in_links[int(links.head[link])].append(link)

        rules: dict[tuple[int, int], list[TurnRestriction]] = defaultdict(list)
        seen: set[tuple[int, int, int, str]] = set()
        for rule in self.turn_restrictions:
            key = (rule.from_osm_id, rule.via_node_id, rule.to_osm_id, rule.kind)
            if key in seen or not _class_matches(rule, VehicleClass.CAR):
                continue
            seen.add(key)
            rules[(rule.from_osm_id, int(rule.via_node_id))].append(rule)
        node_ids = [int(node) for node, data in self.graph.nodes(data=True) if "x" in data]
        signals = self._signalised(links, node_ids, in_links, out_links)

        from_list: list[int] = []
        to_list: list[int] = []
        delay_list: list[float] = []
        restricted = 0
        for node in range(node_count):
            incoming = in_links.get(node)
            outgoing = out_links.get(node)
            if not incoming or not outgoing:
                continue
            signalised = node in signals
            top_rank = max(int(links.rank[link]) for link in incoming)
            for a in incoming:
                a_rules = []
                if rules:
                    via = node_ids[node]
                    for osm in links.osm_ids[a]:
                        a_rules.extend(rules.get((osm, via), ()))
                choices = [b for b in outgoing if links.head[b] != links.tail[a]]
                dead_end = not choices
                if dead_end:
                    choices = [b for b in outgoing]
                for b in choices:
                    if a_rules:
                        blocked = False
                        for rule in a_rules:
                            target = rule.to_osm_id in links.osm_ids[b]
                            if (rule.kind == "no_turn" and target) or (
                                rule.kind == "only_turn" and not target
                            ):
                                blocked = True
                                break
                        if blocked:
                            restricted += 1
                            continue
                    if not settings.turn_model:
                        delay = 0.0
                    elif dead_end:
                        delay = settings.dead_end_u_turn_s
                    else:
                        delay = self._turn_delay(
                            links, a, b, signalised, top_rank, len(incoming) + len(outgoing)
                        )
                    from_list.append(a)
                    to_list.append(b)
                    delay_list.append(delay)
        return (
            (
                np.array(from_list, dtype=np.int64),
                np.array(to_list, dtype=np.int64),
                np.array(delay_list),
            ),
            restricted,
            len(signals),
        )

    def _signalised(
        self,
        links: _Links,
        node_ids: list[int],
        in_links: Mapping[int, list[int]],
        out_links: Mapping[int, list[int]],
    ) -> set[int]:
        """Junctions run by traffic lights.

        OSM tags lights on the junction node or, often, on the stop lines just
        before it, which the simplified graph drops. So a junction also counts
        as signalised where a secondary or bigger road crosses another named
        tertiary or bigger road, as in most European cities.
        """
        signals: set[int] = set()
        for position, node in enumerate(node_ids):
            if "traffic_signals" in str(self.graph.nodes[node].get("highway", "")):
                signals.add(position)
                continue
            legs = in_links.get(position, []) + out_links.get(position, [])
            ends = {int(links.tail[leg]) for leg in legs} | {int(links.head[leg]) for leg in legs}
            if len(ends) < 4:
                continue  # fewer than three neighbouring junctions: not a crossing
            streets: dict[str, int] = {}
            for link in legs:
                rank = int(links.rank[link])
                if rank < 3:
                    continue
                data = links.data[link]
                key = str(_first(data.get("name")) or _osm_id(data))
                streets[key] = max(streets.get(key, 0), rank)
            ranks = sorted(streets.values(), reverse=True)
            if len(ranks) >= 2 and ranks[0] >= 4 and ranks[1] >= 3:
                signals.add(position)
        return signals

    def _turn_delay(
        self, links: _Links, a: int, b: int, signalised: bool, top_rank: int, legs: int
    ) -> float:
        settings = self.settings
        turn = (float(links.start_bearing[b]) - float(links.end_bearing[a]) + 540.0) % 360.0 - 180.0
        # Driving on the right: a turn to the left crosses oncoming traffic.
        kind = "straight" if abs(turn) < 35 else ("right" if turn > 0 else "left")
        if legs <= 2:
            return 0.0  # a bend or a change of way, not a junction
        if signalised:
            return {
                "straight": settings.signal_delay_straight_s,
                "right": settings.signal_delay_right_s,
                "left": settings.signal_delay_left_s,
            }[kind]
        rank_a = int(links.rank[a])
        if rank_a < top_rank:
            # From the minor road: give way to the main road's traffic.
            return settings.give_way_delay_s + (
                settings.give_way_left_extra_s if kind == "left" else 0.0
            )
        return settings.priority_left_delay_s if kind == "left" else 0.0

    def _zones(
        self,
        node_count: int,
        lon: np.ndarray,
        lat: np.ndarray,
        links: _Links,
        movements: tuple[np.ndarray, np.ndarray, np.ndarray],
    ) -> tuple[np.ndarray, np.ndarray, int]:
        """Grid zones over junctions every car can reach and leave in both networks.

        Trips start and end only where both networks connect (residents may
        pass gates). Junctions that only a closed filter or closure cuts off
        are counted for the report. Gateway zones sit where main roads cross
        the edge of the area.
        """
        settings = self.settings

        def core(enabled: np.ndarray) -> np.ndarray:
            matrix = csr_matrix(
                (np.ones(int(enabled.sum())), (links.tail[enabled], links.head[enabled])),
                shape=(node_count, node_count),
            )
            _count, labels = connected_components(matrix, directed=True, connection="strong")
            return labels == np.bincount(labels).argmax()

        base_core = core(links.enabled_base)
        in_core = base_core & core(links.enabled_plan)
        self._cut_off = int((base_core & ~in_core).sum())
        if in_core.sum() < 2:
            in_core = base_core

        activity = np.zeros(node_count)
        base = links.enabled_base
        np.add.at(activity, links.tail[base], links.activity[base] / 2.0)
        np.add.at(activity, links.head[base], links.activity[base] / 2.0)
        local = np.zeros(node_count, dtype=bool)
        local_links = base & ~links.is_main & (links.activity > 0)
        local[links.tail[local_links]] = True
        local[links.head[local_links]] = True

        lat0 = float(lat[in_core].mean())
        x0, y0 = float(lon[in_core].min()), float(lat[in_core].min())
        scale_x = METRES_PER_DEGREE_LON * math.cos(math.radians(lat0))

        def project(lon_values, lat_values):
            return (np.asarray(lon_values) - x0) * scale_x, (
                np.asarray(lat_values) - y0
            ) * METRES_PER_DEGREE_LAT

        x, y = project(lon, lat)
        candidates = np.nonzero(in_core)[0]

        land_use = self.residents is not None
        if land_use:
            px, py = project(self.residents.lon, self.residents.lat)
            jobs = self.jobs if self.jobs is not None else self.residents
            jx, jy = project(jobs.lon, jobs.lat)

        size = settings.zone_size_m
        for _attempt in range(30):
            node_cell = _cell_key(x[candidates], y[candidates], size)
            cells, cell_index = np.unique(node_cell, return_inverse=True)
            if land_use:
                production, attraction = self._land_use_weights(
                    cells,
                    _cell_key(px, py, size),
                    self.residents.residents,
                    _cell_key(jx, jy, size),
                    jobs.residents,
                    x[candidates],
                    y[candidates],
                    cell_index,
                    size,
                )
                weights = production + attraction
            else:
                weights = np.bincount(
                    cell_index, weights=activity[candidates], minlength=cells.size
                )
            if np.count_nonzero(weights) <= settings.max_zones:
                break
            size *= math.sqrt(np.count_nonzero(weights) / settings.max_zones) * 1.02

        connectors: list[int] = []
        zone_of: list[int] = []
        zone_production: list[float] = []
        zone_attraction: list[float] = []
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
            if land_use:
                zone_production.append(float(production[cell_number]))
                zone_attraction.append(float(attraction[cell_number]))
            else:
                zone_production.append(float(weights[cell_number]))
                zone_attraction.append(float(weights[cell_number]))
            zone += 1
        internal = zone
        if internal < 2:
            raise ValueError("the area is too small to build at least two demand zones")

        production = np.array(zone_production)
        attraction = np.array(zone_attraction)
        production /= production.sum()
        attraction /= attraction.sum()
        gateways = self._gateways(links, x, y, in_core, settings.gateway_band_m)
        share = settings.external_trip_share if gateways else 0.0
        self._external_share = share
        if gateways:
            capacity = np.array([weight for _nodes, weight in gateways])
            capacity /= capacity.sum()
            production = np.concatenate([production * (1 - share), capacity * share])
            attraction = np.concatenate([attraction * (1 - share), capacity * share])
            for nodes, _weight in gateways:
                connectors.extend(nodes)
                zone_of.extend([zone] * len(nodes))
                zone += 1
        self._production, self._attraction = production, attraction
        self._zone_weights = production
        return (
            np.array(zone_of, dtype=np.int64),
            np.array(connectors, dtype=np.int64),
            len(gateways),
        )

    def _land_use_weights(
        self,
        cells: np.ndarray,
        home_cells: np.ndarray,
        homes: np.ndarray,
        job_cells: np.ndarray,
        jobs: np.ndarray,
        node_x: np.ndarray,
        node_y: np.ndarray,
        node_cell_index: np.ndarray,
        size: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Residents and jobs per zone cell; cells without junctions pass theirs to the nearest."""
        position = {int(cell): index for index, cell in enumerate(cells.tolist())}
        centres_x = np.bincount(node_cell_index, weights=node_x, minlength=cells.size)
        centres_y = np.bincount(node_cell_index, weights=node_y, minlength=cells.size)
        counts = np.maximum(np.bincount(node_cell_index, minlength=cells.size), 1)
        tree = cKDTree(np.column_stack([centres_x / counts, centres_y / counts]))

        def gather(point_cells: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, float]:
            out = np.zeros(cells.size)
            mapped = np.array([position.get(int(cell), -1) for cell in point_cells.tolist()])
            inside = mapped >= 0
            np.add.at(out, mapped[inside], values[inside])
            dropped = 0.0
            if (~inside).any():
                column, row = _cell_position(point_cells[~inside])
                cx, cy = (column + 0.5) * size, (row + 0.5) * size
                distance, nearest = tree.query(np.column_stack([cx, cy]))
                # Homes far from any drivable junction (islands, forest) drop out.
                near = distance <= 2.0 * size
                np.add.at(out, nearest[near], values[~inside][near])
                dropped = float(values[~inside][~near].sum())
            return out, dropped

        production, dropped = gather(home_cells, homes)
        attraction, _ = gather(job_cells, jobs)
        self._land_use_dropped = dropped / max(float(homes.sum()), 1e-9)
        return production, attraction

    @staticmethod
    def _gateways(
        links: _Links, x: np.ndarray, y: np.ndarray, in_core: np.ndarray, band: float
    ) -> list[tuple[list[int], float]]:
        """Clusters of main-road junctions near the edge of the area, with their capacity."""
        nodes = np.nonzero(in_core)[0]
        if nodes.size < 4:
            return []
        xmin, xmax = x[nodes].min(), x[nodes].max()
        ymin, ymax = y[nodes].min(), y[nodes].max()
        edge_distance = np.minimum.reduce([x - xmin, xmax - x, y - ymin, ymax - y])
        gateway_highways = np.array([_highway(data) in GATEWAY_ROAD_TYPES for data in links.data])
        weight: dict[int, float] = defaultdict(float)
        for link in np.nonzero(gateway_highways & links.enabled_base)[0]:
            for node in (int(links.tail[link]), int(links.head[link])):
                if in_core[node] and edge_distance[node] <= band:
                    weight[node] += float(links.capacity[link])
        if not weight:
            return []
        clusters: dict[tuple[int, int], list[int]] = defaultdict(list)
        for node in weight:
            clusters[(int(x[node] // 1_500), int(y[node] // 1_500))].append(node)
        return [
            (sorted(members, key=lambda n: -weight[n])[:4], sum(weight[n] for n in members))
            for members in clusters.values()
        ]

    # ------------------------------------------------------------------ demand

    def _demand_source(self) -> str:
        if self.residents is None:
            return "street length"
        return f"residents ({self.residents.source})" + (
            f" and jobs ({self.jobs.source})" if self.jobs is not None else ""
        )

    def _build_demand(self, network: _Network) -> tuple[float, float, bool]:
        """Gravity demand shares, the level-1.0 trip total and the reference trip time."""
        settings = self.settings
        free = self._all_or_nothing(
            network,
            False,
            network.links.free_time,
            np.ones((network.zone_count,) * 2),
            skims=True,
        )
        minutes = free.skims

        production = self._production
        attraction = self._attraction
        with np.errstate(over="ignore", invalid="ignore"):
            pull = attraction[None, :] * np.exp(-settings.distance_decay_per_minute * minutes)
        pull[~np.isfinite(pull)] = 0.0
        np.fill_diagonal(pull, 0.0)
        totals = pull.sum(axis=1, keepdims=True)
        shares = (
            np.divide(pull, totals, out=np.zeros_like(pull), where=totals > 0) * production[:, None]
        )
        shares /= max(shares.sum(), 1e-12)
        network.demand = shares

        unit = self._all_or_nothing(network, False, network.links.free_time, shares)
        reference_seconds = unit.cost / max(unit.served, 1e-12)
        self._reference_seconds = reference_seconds
        if settings.peak_hour_trips is not None:
            return reference_seconds, float(settings.peak_hour_trips), False
        links = network.links
        enabled = links.enabled_base
        main = enabled & links.is_main
        pool = main if links.length_m[main].sum() > 0 else enabled
        ratio = unit.flows[pool] / links.capacity[pool]
        mean_load = float(np.average(ratio, weights=links.length_m[pool]))
        if mean_load <= 0:
            raise ValueError("no trips reach the main roads; the area is too small to simulate")
        trips = 0.5 / mean_load
        return reference_seconds, self._calibrate(network, reference_seconds, trips), True

    def _calibrate(self, network: _Network, reference_seconds: float, trips: float) -> float:
        """Find the trip total at which today's peak trip takes the target index.

        The excess delay (index - 1) grows roughly as a power of demand, so the
        search is a secant on log(index - 1) against log(trips), on
        equilibria warm-started from the previous guess.
        """
        target = self.settings.target_peak_travel_time_index
        iterations = self.settings.max_iterations
        points: list[tuple[float, float]] = []
        start: tuple[np.ndarray, np.ndarray] | None = None
        best = (math.inf, trips)
        for _attempt in range(6):
            if start is not None and points:
                factor = trips / points[-1][0]
                start = (start[0] * factor, start[1] * factor)
            assignment = self._equilibrium(
                network, False, network.demand * trips, start, max_iterations=iterations
            )
            start = (assignment.flows, assignment.arc_flows)
            index = self._real_cost(network, False, assignment) / max(assignment.served, 1e-9)
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

    def _elastic(
        self,
        network: _Network,
        base_trips: float,
        baseline: _Assignment,
        planned: _Assignment,
        report: Callable[[int], None],
    ) -> tuple[_Assignment, float]:
        """Planned equilibrium once zone-to-zone trips respond to their new travel times.

        Trips between zones i and j become T0 * (t_plan / t_today) ** e, with
        the times taken on the planned network's own equilibrium. The demand
        and the assignment are iterated with averaging until they agree.
        """
        settings = self.settings
        reference = network.demand * base_trips
        today = baseline.skims
        current = planned
        demand = reference.copy()
        with np.errstate(divide="ignore", invalid="ignore"):
            for round_number in range(1, settings.elastic_iterations + 1):
                report(round_number)
                ratio = np.where(
                    (today > 0) & np.isfinite(today) & np.isfinite(current.skims),
                    current.skims / today,
                    1.0,
                )
                target = reference * np.power(np.maximum(ratio, 1e-3), settings.demand_elasticity)
                target = np.where(np.isfinite(current.skims), target, 0.0)
                demand = demand + (target - demand) / (round_number + 1)
                current = self._equilibrium(
                    network, True, demand, (current.flows, current.arc_flows), skims=True
                )
        return current, float(demand.sum())

    # -------------------------------------------------------------- assignment

    def _arc_costs(self, network: _Network, plan: bool, link_times: np.ndarray) -> np.ndarray:
        arcs = network.arcs
        costs = np.where(arcs.link >= 0, link_times[np.maximum(arcs.link, 0)], 0.0) + arcs.delay
        if plan:
            costs = costs + arcs.penalty_plan
        return np.maximum(costs, 1e-3)

    @dataclass
    class _Loaded:
        flows: np.ndarray
        arc_flows: np.ndarray
        cost: float
        served: float
        blocked: float
        skims: np.ndarray | None

    def _all_or_nothing(
        self,
        network: _Network,
        plan: bool,
        link_times: np.ndarray,
        demand: np.ndarray,
        skims: bool = False,
    ) -> _Loaded:
        """Load every trip on its current shortest path."""
        arcs = network.arcs
        size = network.node_count
        enabled = arcs.enabled_plan if plan else arcs.enabled_base
        costs = self._arc_costs(network, plan, link_times)
        arc_ids = np.nonzero(enabled)[0]
        matrix = csr_matrix(
            (costs[arc_ids], (arcs.tail[arc_ids], arcs.head[arc_ids])), shape=(size, size)
        )
        keys = arcs.tail[arc_ids] * size + arcs.head[arc_ids]
        key_order = np.argsort(keys)
        sorted_keys = keys[key_order]
        penalised = None
        if plan and skims and network.arcs.penalty_plan.any():
            # Penalty paid on the way to each routing node, to report real times.
            penalised = np.zeros(size)

        arc_flows = np.zeros(arcs.tail.size)
        cost = served = blocked = 0.0
        sinks = network.sinks
        skim = np.full((network.zone_count, network.zone_count), np.inf) if skims else None
        for start in range(0, network.zone_count, ORIGIN_BATCH):
            zones = np.arange(start, min(start + ORIGIN_BATCH, network.zone_count))
            distances, predecessors = dijkstra(
                matrix, directed=True, indices=network.sources[zones], return_predecessors=True
            )
            trips = demand[zones]
            to_sinks = distances[:, sinks]
            if skim is not None:
                real = to_sinks
                if penalised is not None:
                    paid = _tree_path_sums(
                        predecessors,
                        lambda parent, child: _lookup(
                            sorted_keys,
                            key_order,
                            arc_ids,
                            size,
                            parent,
                            child,
                            network.arcs.penalty_plan,
                        ),
                    )[:, sinks]
                    real = to_sinks - paid
                skim[zones] = real / 60.0
            reach = np.isfinite(to_sinks)
            blocked += float(trips[~reach].sum())
            trips = np.where(reach, trips, 0.0)
            served += float(trips.sum())
            cost += float((trips * np.where(reach, to_sinks, 0.0)).sum())
            loads = np.zeros((zones.size, size))
            loads[:, sinks] = trips
            child, parent, carried = _tree_loads(predecessors, loads)
            if child.size == 0:
                continue
            position = np.searchsorted(sorted_keys, parent * size + child)
            arc_flows += np.bincount(
                arc_ids[key_order[position]], weights=carried, minlength=arc_flows.size
            )
        flows = self._link_flows(network, arc_flows)
        return self._Loaded(
            flows=flows,
            arc_flows=arc_flows,
            cost=cost,
            served=served,
            blocked=blocked,
            skims=skim,
        )

    @staticmethod
    def _link_flows(network: _Network, arc_flows: np.ndarray) -> np.ndarray:
        arcs = network.arcs
        into = arcs.link >= 0
        return np.bincount(
            arcs.link[into], weights=arc_flows[into], minlength=network.links.tail.size
        )

    def _bpr(self, links: _Links, flows: np.ndarray) -> np.ndarray:
        ratio = flows / links.capacity
        return links.free_time * (1.0 + self.settings.bpr_alpha * ratio**self.settings.bpr_beta)

    def _equilibrium(
        self,
        network: _Network,
        plan: bool,
        demand: np.ndarray,
        start: tuple[np.ndarray, np.ndarray] | None,
        max_iterations: int | None = None,
        skims: bool = False,
    ) -> _Assignment:
        """Frank-Wolfe user equilibrium with an exact line search."""
        settings = self.settings
        links = network.links
        arcs = network.arcs
        constant = arcs.delay + (arcs.penalty_plan if plan else 0.0)
        if start is None:
            loaded = self._all_or_nothing(network, plan, links.free_time, demand)
            flows, arc_flows = loaded.flows, loaded.arc_flows
        else:
            enabled = arcs.enabled_plan if plan else arcs.enabled_base
            arc_flows = np.where(enabled, start[1], 0.0)
            flows = self._link_flows(network, arc_flows)
        gap = float("inf")
        iterations = 0
        served = blocked = 0.0
        limit = max_iterations or settings.max_iterations
        target = None
        for iteration in range(1, limit + 1):
            iterations = iteration
            times = self._bpr(links, flows)
            target = self._all_or_nothing(network, plan, times, demand)
            served, blocked = target.served, target.blocked
            total = float((flows * times).sum() + (arc_flows * constant).sum())
            gap = max(0.0, (total - target.cost) / total) if total > 0 else 0.0
            if gap <= settings.convergence_gap:
                break
            direction = target.flows - flows
            arc_direction = target.arc_flows - arc_flows
            step = self._line_search(
                links, flows, direction, float((arc_direction * constant).sum())
            )
            flows = flows + step * direction
            arc_flows = arc_flows + step * arc_direction
        times = self._bpr(links, flows)
        skim = None
        if skims:
            skim = self._all_or_nothing(network, plan, times, demand, skims=True).skims
        return _Assignment(
            flows=flows,
            arc_flows=arc_flows,
            times=times,
            served=served,
            blocked=blocked,
            gap=gap,
            iterations=iterations,
            skims=skim,
        )

    def _line_search(
        self, links: _Links, flows: np.ndarray, direction: np.ndarray, fixed_slope: float
    ) -> float:
        low, high = 0.0, 1.0
        if float((direction * self._bpr(links, flows + direction)).sum()) + fixed_slope <= 0:
            return 1.0
        for _ in range(24):
            middle = (low + high) / 2
            slope = float((direction * self._bpr(links, flows + middle * direction)).sum())
            if slope + fixed_slope > 0:
                high = middle
            else:
                low = middle
        return (low + high) / 2

    def _real_cost(self, network: _Network, plan: bool, assignment: _Assignment) -> float:
        """Vehicle-seconds on links and at junctions; gate penalties are not real time."""
        return float(
            (assignment.flows * assignment.times).sum()
            + (assignment.arc_flows * network.arcs.delay).sum()
        )

    # ----------------------------------------------------------------- results

    def _level_result(
        self,
        level: float,
        trips: float,
        assignment: _Assignment,
        network: _Network,
    ) -> SimulationLevelResult:
        links = network.links
        flows, _times = assignment.flows, assignment.times
        vehicle_seconds = self._real_cost(network, False, assignment)
        vehicle_km = float((flows * links.length_m).sum() / 1000.0)
        vehicle_hours = vehicle_seconds / 3600.0
        ratio = flows / links.capacity
        mean_seconds = vehicle_seconds / assignment.served if assignment.served > 0 else 0.0
        return SimulationLevelResult(
            demand_multiplier=level,
            trips=trips,
            served_trips=assignment.served,
            blocked_trips=assignment.blocked,
            vehicle_km=vehicle_km,
            vehicle_hours=vehicle_hours,
            mean_trip_minutes=mean_seconds / 60.0,
            mean_speed_kph=vehicle_km / vehicle_hours if vehicle_hours > 0 else 0.0,
            travel_time_index=0.0,
            congested_km=float(
                links.length_m[ratio > self.settings.congested_volume_capacity_ratio].sum() / 1000
            ),
            over_capacity_km=float(links.length_m[ratio > 1.0].sum() / 1000),
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
        elastic: _Assignment | None,
        boundary_ids: set[int],
    ) -> list[SimulatedLink]:
        settings = self.settings
        if settings.max_map_links == 0:
            return []
        links = network.links
        grouped: dict[tuple[int, int], list[int]] = {}
        for link in range(links.tail.size):
            if not links.enabled_base[link] and not links.enabled_plan[link]:
                continue
            tail, head = int(links.tail[link]), int(links.head[link])
            grouped.setdefault((min(tail, head), max(tail, head)), []).append(link)

        def speed(members: list[int], times: np.ndarray, flows: np.ndarray) -> float:
            # Flow-weighted over directions; an unused section shows its free speed.
            weights = flows[members] + 1e-6
            seconds = float(np.average(times[members], weights=weights))
            return float(links.length_m[members[0]] / max(seconds, 1e-3) * 3.6)

        rows: list[tuple[float, SimulatedLink]] = []
        for (first, second), members in grouped.items():
            base_volume = float(baseline.flows[members].sum())
            plan_volume = float(planned.flows[members].sum())
            elastic_volume = float(elastic.flows[members].sum()) if elastic is not None else None
            closed = all(
                links.enabled_base[link] and not links.enabled_plan[link] for link in members
            )
            gated = any(links.gated[link] for link in members)
            if (
                max(base_volume, plan_volume, elastic_volume or 0.0) < settings.min_map_volume_vph
                and not closed
                and not gated
            ):
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
                        planned_elastic_vph=(
                            round(elastic_volume, 1) if elastic_volume is not None else None
                        ),
                        capacity_vph=float(capacities.max()),
                        baseline_vc=round(float((baseline.flows[members] / capacities).max()), 3),
                        planned_vc=round(float((planned.flows[members] / capacities).max()), 3),
                        closed_in_plan=closed,
                        gated_in_plan=gated,
                        is_boundary_road=_osm_id(data) in boundary_ids,
                        lanes=_lanes(data),
                        length_m=round(float(links.length_m[members[0]]), 1),
                        free_speed_kph=round(
                            float(links.length_m[members[0]] / links.free_time[members[0]] * 3.6),
                            1,
                        ),
                        baseline_speed_kph=round(speed(members, baseline.times, baseline.flows), 1),
                        planned_speed_kph=round(speed(members, planned.times, planned.flows), 1),
                        planned_elastic_speed_kph=(
                            round(speed(members, elastic.times, elastic.flows), 1)
                            if elastic is not None
                            else None
                        ),
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
        settings = self.settings
        notes = [
            "Static peak-hour equilibrium with BPR link delays and fixed junction delays: "
            "queues spilling back between junctions are not modelled (export to SUMO for that).",
        ]
        if self.residents is None:
            notes.append(
                "Trips are generated from street length per zone; supply a population grid "
                "and a jobs layer to generate them from homes and jobs."
            )
        elif self._land_use_dropped > 0.01:
            notes.append(
                f"{self._land_use_dropped:.0%} of residents live too far from a drivable "
                "junction to be given a zone and generate no car trips."
            )
        if self._external_share > 0:
            notes.append(
                f"{self._external_share:.0%} of peak trips enter or leave through the area's "
                "main-road gateways; that share is an assumption, not a count."
            )
        if settings.demand_elasticity < 0:
            notes.append(
                "Trips between zones respond to the plan's change in their travel time with "
                f"an elasticity of {settings.demand_elasticity:g}; trips not made are counted "
                "as switching mode, time or destination, which is not modelled further."
            )
        else:
            notes.append(
                "Demand is the same with and without the plan; trips that would vanish or "
                "switch to walking, cycling or transit are not subtracted, so the plan's "
                "effect on car traffic is a worst case."
            )
        if settings.turn_model:
            notes.append(
                "Junction delays are typical values by control type (signals or priority), "
                "not timed signal plans; turn restrictions come only from the supplied data."
            )
        if calibrated:
            notes.append(
                "Demand level 1.0 is calibrated so the average trip today takes "
                f"{settings.target_peak_travel_time_index:g}x as long as on empty streets; "
                "set peak_hour_trips to anchor it to counted traffic."
            )
        if self._cut_off:
            notes.append(
                f"{self._cut_off:,} junctions have no car route in and out once the plan's "
                "closures are in place; trips start and end only at junctions both networks "
                "reach."
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


CELL_OFFSET = 1 << 20


def _cell_key(xs: np.ndarray, ys: np.ndarray, size: float) -> np.ndarray:
    """One integer per grid cell of ``size`` metres."""
    column = np.floor(np.asarray(xs) / size).astype(np.int64) + CELL_OFFSET
    row = np.floor(np.asarray(ys) / size).astype(np.int64) + CELL_OFFSET
    return (column << 32) | row


def _cell_position(keys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return ((keys >> 32) - CELL_OFFSET).astype(float), ((keys & 0xFFFFFFFF) - CELL_OFFSET).astype(
        float
    )


def _change(before: float, after: float) -> float:
    return (after / before - 1.0) * 100.0 if before > 0 else 0.0


def _lookup(
    sorted_keys: np.ndarray,
    key_order: np.ndarray,
    arc_ids: np.ndarray,
    size: int,
    parent: np.ndarray,
    child: np.ndarray,
    values: np.ndarray,
) -> np.ndarray:
    position = np.searchsorted(sorted_keys, parent * size + child)
    position = np.minimum(position, sorted_keys.size - 1)
    return values[arc_ids[key_order[position]]]


def _depths(predecessors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Flattened parent pointers and depth of every node in each shortest-path tree."""
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
    return parent, depth


def _tree_path_sums(
    predecessors: np.ndarray,
    arc_value: Callable[[np.ndarray, np.ndarray], np.ndarray],
) -> np.ndarray:
    """Sum an arc value along each tree path from the origin to every node."""
    origins, size = predecessors.shape
    parent, depth = _depths(predecessors)
    total = np.zeros(origins * size)
    members = np.nonzero(parent >= 0)[0]
    if members.size == 0:
        return total.reshape(origins, size)
    by_depth = members[np.argsort(depth[members], kind="stable")]
    depths = depth[by_depth]
    for level in np.split(by_depth, np.flatnonzero(np.diff(depths)) + 1):
        values = arc_value(parent[level] % size, level % size)
        total[level] = total[parent[level]] + values
    return total.reshape(origins, size)


def _tree_loads(
    predecessors: np.ndarray, loads: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Push node demand up shortest-path trees.

    ``predecessors`` and ``loads`` are (origins, nodes). Returns, for every tree
    edge that carries traffic, its child node, parent node and the trips that
    cross it. Depths come from pointer jumping, then each depth level is added
    to its parents in one vectorised step, deepest first.
    """
    _origins, size = predecessors.shape
    parent, depth = _depths(predecessors)
    valid = parent >= 0
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
