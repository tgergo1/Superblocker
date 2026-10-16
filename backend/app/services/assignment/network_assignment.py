"""Time-sliced, multi-class network assignment on the legal turn graph.

This module is deliberately a transparent screening assignment rather than a
microscopic simulator. It uses deterministic all-or-nothing loading with method
of successive averages (MSA) and BPR volume-delay functions. Finalists can be
exported to SUMO by the simulation adapter for lane/junction-level analysis.
"""

from __future__ import annotations

import hashlib
import heapq
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import networkx as nx

from app.models.schemas import (
    AssignedEdgeFlow,
    AssignmentComparison,
    AssignmentRun,
    AssignmentSettings,
    CalibrationSummary,
    DemandModel,
    DemandZone,
    StreetModification,
    TrafficObservation,
    TurnRestriction,
    VehicleClass,
)
from app.services.network.turn_graph import TurnGraph, TurnGraphBuilder

type EdgeRef = tuple[int, int, int]
type FlowKey = tuple[str, VehicleClass, EdgeRef]


@dataclass(frozen=True)
class _Route:
    cost_seconds: float
    edges: tuple[EdgeRef, ...]


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, (list, tuple, set)):
        value = next(iter(value), None)
    try:
        parsed = float(str(value).split(";")[0])
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else default


def _highway(data: Mapping[str, Any]) -> str:
    value = data.get("highway", "residential")
    if isinstance(value, (list, tuple, set)):
        value = next(iter(value), "residential")
    return str(value)


def _edge_capacity_vph(data: Mapping[str, Any]) -> float:
    """Return a documented screening capacity for one directed edge."""
    explicit = _as_float(data.get("capacity"), 0.0)
    if explicit > 0:
        return explicit
    lanes = max(1.0, _as_float(data.get("lanes"), 1.0))
    per_lane = {
        "motorway": 2_000.0,
        "trunk": 1_800.0,
        "primary": 1_500.0,
        "secondary": 1_300.0,
        "tertiary": 1_100.0,
        "residential": 700.0,
        "living_street": 350.0,
        "service": 300.0,
    }.get(_highway(data), 600.0)
    return lanes * per_lane


def _osm_id(data: Mapping[str, Any]) -> int:
    value = data.get("osmid", 0)
    if isinstance(value, (list, tuple, set)):
        value = next(iter(value), 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _stable_run_id(
    state: str, demand: DemandModel, modifications: Iterable[StreetModification]
) -> str:
    actions = sorted(
        (
            item.u,
            item.v,
            item.key,
            item.modification_type.value,
            item.direction,
        )
        for item in modifications
    )
    material = repr((state, demand.model_id, demand.version, actions)).encode()
    return hashlib.sha256(material).hexdigest()[:24]


class NetworkAssignmentEngine:
    """Assign an OD matrix to baseline and planned legal movement graphs."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        restrictions: Iterable[TurnRestriction] = (),
    ) -> None:
        self.graph = graph
        self.restrictions = list(restrictions)

    def compare(
        self,
        demand: DemandModel,
        modifications: Iterable[StreetModification],
        boundary_osm_ids: set[int],
        settings: AssignmentSettings | None = None,
    ) -> AssignmentComparison:
        settings = settings or AssignmentSettings()
        planned_modifications = list(modifications)
        baseline = self.assign(
            demand,
            [],
            boundary_osm_ids,
            settings,
            network_state="baseline",
        )
        planned = self.assign(
            demand,
            planned_modifications,
            boundary_osm_ids,
            settings,
            network_state="planned",
        )

        maximum_boundary_ratio = max(
            (flow.volume_capacity_ratio for flow in planned.edge_flows if flow.is_boundary_road),
            default=0.0,
        )
        baseline_index = {
            (flow.u, flow.v, flow.key, flow.time_slice_id, flow.vehicle_class): flow.volume
            for flow in baseline.edge_flows
        }
        adjacent_changes: list[float] = []
        for flow in planned.edge_flows:
            if flow.is_boundary_road:
                continue
            key = (flow.u, flow.v, flow.key, flow.time_slice_id, flow.vehicle_class)
            baseline_volume = baseline_index.get(key, 0.0)
            if baseline_volume <= 1e-9:
                if flow.volume > 1e-9:
                    adjacent_changes.append(100.0)
                continue
            adjacent_changes.append(abs(flow.volume - baseline_volume) / baseline_volume * 100)
        maximum_adjacent_change = max(adjacent_changes, default=0.0)
        all_demand_assigned = baseline.blocked_demand <= 1e-6 and planned.blocked_demand <= 1e-6
        boundary_passed = maximum_boundary_ratio <= settings.maximum_boundary_volume_capacity_ratio
        spillover_passed = (
            maximum_adjacent_change <= settings.maximum_adjacent_road_volume_change_percent
        )
        limitations = [
            "Screening assignment uses BPR link delay; signals, spillback, and lane-changing require microscopic simulation.",
            "Demand is held fixed; induced, suppressed, and mode-shift responses require scenario demand models.",
        ]
        if not baseline.converged or not planned.converged:
            limitations.append(
                "At least one assignment did not meet the configured convergence gap."
            )
        if not all_demand_assigned:
            limitations.append("Some OD demand has no legal baseline or planned path.")
        validated = (
            baseline.converged
            and planned.converged
            and all_demand_assigned
            and boundary_passed
            and spillover_passed
        )
        return AssignmentComparison(
            validated=validated,
            baseline=baseline,
            planned=planned,
            maximum_boundary_volume_capacity_ratio=maximum_boundary_ratio,
            maximum_adjacent_road_volume_change_percent=maximum_adjacent_change,
            boundary_capacity_passed=boundary_passed,
            spillover_passed=spillover_passed,
            all_demand_assigned=all_demand_assigned,
            limitations=limitations,
        )

    def assign(
        self,
        demand: DemandModel,
        modifications: Iterable[StreetModification],
        boundary_osm_ids: set[int],
        settings: AssignmentSettings,
        *,
        network_state: str,
    ) -> AssignmentRun:
        modifications = list(modifications)
        zones = self._resolve_zone_connectors(demand.zones)
        durations = {
            time_slice.time_slice_id: time_slice.duration_minutes
            for time_slice in demand.time_slices
        }
        classes = sorted(
            {entry.vehicle_class for entry in demand.matrix}, key=lambda item: item.value
        )
        turn_graphs = {
            (time_slice.time_slice_id, vehicle_class): TurnGraphBuilder(
                self.graph,
                self.restrictions,
            ).build(vehicle_class, modifications, time_slice.time_slice_id)
            for time_slice in demand.time_slices
            for vehicle_class in classes
        }
        capacities: dict[EdgeRef, float] = {}
        free_flow: dict[EdgeRef, float] = {}
        osm_ids: dict[EdgeRef, int] = {}
        for turn_graph in turn_graphs.values():
            for ref, data in turn_graph.edge_data.items():
                capacities[ref] = _edge_capacity_vph(data)
                free_flow[ref] = float(turn_graph.graph.nodes[ref].get("travel_time_s", 0.0))
                osm_ids[ref] = _osm_id(data)

        flows: dict[FlowKey, float] = defaultdict(float)
        final_assigned_trips = 0.0
        final_blocked_trips = 0.0
        gap = float("inf")
        iterations = 0
        for iteration in range(1, settings.max_iterations + 1):
            iterations = iteration
            pce_flows = self._combined_pce_flows(flows, settings)
            costs = {
                (time_slice, ref): self._bpr_time(
                    free_flow.get(ref, 0.0),
                    pce_flows.get((time_slice, ref), 0.0),
                    capacities[ref],
                    settings,
                )
                for time_slice in durations
                for ref in capacities
            }
            aon: dict[FlowKey, float] = defaultdict(float)
            assigned_trips = 0.0
            blocked_trips = 0.0
            for entry in demand.matrix:
                if entry.trips <= 0:
                    continue
                if entry.origin_zone_id == entry.destination_zone_id:
                    assigned_trips += entry.trips
                    continue
                turn_graph = turn_graphs[(entry.time_slice_id, entry.vehicle_class)]
                route = self._shortest_route(
                    turn_graph,
                    zones[entry.origin_zone_id],
                    zones[entry.destination_zone_id],
                    costs,
                    entry.time_slice_id,
                )
                if route is None:
                    blocked_trips += entry.trips
                    continue
                assigned_trips += entry.trips
                volume_vph = entry.trips * 60.0 / durations[entry.time_slice_id]
                for ref in route.edges:
                    aon[(entry.time_slice_id, entry.vehicle_class, ref)] += volume_vph

            step = 1.0 / iteration
            keys = set(flows) | set(aon)
            denominator = max(sum(abs(value) for value in flows.values()), 1.0)
            raw_delta = sum(abs(aon.get(key, 0.0) - flows.get(key, 0.0)) for key in keys)
            for key in keys:
                flows[key] = flows.get(key, 0.0) + step * (aon.get(key, 0.0) - flows.get(key, 0.0))
                if flows[key] <= 1e-9:
                    flows.pop(key, None)
            gap = raw_delta / denominator
            final_assigned_trips = assigned_trips
            final_blocked_trips = blocked_trips
            if iteration > 1 and gap <= settings.convergence_gap:
                break

        pce_flows = self._combined_pce_flows(flows, settings)
        edge_results: list[AssignedEdgeFlow] = []
        for (time_slice_id, vehicle_class, ref), volume in sorted(
            flows.items(),
            key=lambda item: (item[0][0], item[0][1].value, item[0][2]),
        ):
            capacity = capacities[ref]
            combined_volume = pce_flows.get((time_slice_id, ref), 0.0)
            assigned_time = self._bpr_time(
                free_flow.get(ref, 0.0),
                combined_volume,
                capacity,
                settings,
            )
            duration_hours = durations[time_slice_id] / 60.0
            queue_length = max(0.0, combined_volume - capacity) * duration_hours * 7.0
            edge_results.append(
                AssignedEdgeFlow(
                    u=ref[0],
                    v=ref[1],
                    key=ref[2],
                    osm_id=osm_ids.get(ref, 0),
                    time_slice_id=time_slice_id,
                    vehicle_class=vehicle_class,
                    volume=volume,
                    capacity_vph=capacity,
                    volume_capacity_ratio=combined_volume / capacity,
                    free_flow_time_seconds=free_flow.get(ref, 0.0),
                    assigned_time_seconds=assigned_time,
                    estimated_queue_length_m=queue_length,
                    is_boundary_road=osm_ids.get(ref, 0) in boundary_osm_ids,
                )
            )

        total_demand = sum(entry.trips for entry in demand.matrix)
        return AssignmentRun(
            run_id=_stable_run_id(network_state, demand, modifications),
            network_state=network_state,
            converged=gap <= settings.convergence_gap,
            iterations=iterations,
            relative_gap=max(0.0, gap),
            total_demand=total_demand,
            assigned_demand=final_assigned_trips,
            blocked_demand=final_blocked_trips,
            edge_flows=edge_results,
        )

    def _resolve_zone_connectors(self, zones: Iterable[DemandZone]) -> dict[str, tuple[int, ...]]:
        graph_nodes = list(self.graph.nodes(data=True))
        if not graph_nodes:
            raise ValueError("cannot assign demand on an empty network")
        result: dict[str, tuple[int, ...]] = {}
        for zone in zones:
            connectors = tuple(node for node in zone.connector_node_ids if node in self.graph)
            if not connectors:
                nearest = min(
                    graph_nodes,
                    key=lambda item: (
                        (float(item[1].get("x", 0.0)) - zone.centroid.lon) ** 2
                        + (float(item[1].get("y", 0.0)) - zone.centroid.lat) ** 2
                    ),
                )[0]
                connectors = (int(nearest),)
            result[zone.zone_id] = tuple(int(node) for node in connectors)
        return result

    @staticmethod
    def _combined_pce_flows(
        flows: Mapping[FlowKey, float],
        settings: AssignmentSettings,
    ) -> dict[tuple[str, EdgeRef], float]:
        combined: dict[tuple[str, EdgeRef], float] = defaultdict(float)
        for (time_slice, vehicle_class, ref), volume in flows.items():
            pce = settings.passenger_car_equivalents.get(vehicle_class, 1.0)
            combined[(time_slice, ref)] += volume * pce
        return combined

    @staticmethod
    def _bpr_time(
        free_flow_seconds: float,
        volume: float,
        capacity: float,
        settings: AssignmentSettings,
    ) -> float:
        ratio = max(0.0, volume / max(capacity, 1e-9))
        return max(0.01, free_flow_seconds) * (1.0 + settings.bpr_alpha * ratio**settings.bpr_beta)

    @staticmethod
    def _shortest_route(
        turn_graph: TurnGraph,
        source_nodes: tuple[int, ...],
        target_nodes: tuple[int, ...],
        costs: Mapping[tuple[str, EdgeRef], float],
        time_slice_id: str,
    ) -> _Route | None:
        starts = set().union(*(turn_graph.outgoing_states(node) for node in source_nodes))
        targets = set().union(*(turn_graph.incoming_states(node) for node in target_nodes))
        if not starts or not targets:
            return None
        distances: dict[EdgeRef, float] = {}
        previous: dict[EdgeRef, EdgeRef] = {}
        heap: list[tuple[float, EdgeRef]] = []
        for state in starts:
            cost = costs.get((time_slice_id, state), float("inf"))
            if math.isfinite(cost) and cost < distances.get(state, float("inf")):
                distances[state] = cost
                heapq.heappush(heap, (cost, state))
        target: EdgeRef | None = None
        while heap:
            distance, state = heapq.heappop(heap)
            if distance != distances.get(state):
                continue
            if state in targets:
                target = state
                break
            for next_state in turn_graph.graph.successors(state):
                candidate = distance + costs.get((time_slice_id, next_state), float("inf"))
                if candidate < distances.get(next_state, float("inf")):
                    distances[next_state] = candidate
                    previous[next_state] = state
                    heapq.heappush(heap, (candidate, next_state))
        if target is None:
            return None
        path = [target]
        while path[-1] in previous:
            path.append(previous[path[-1]])
        path.reverse()
        return _Route(cost_seconds=distances[target], edges=tuple(path))


def calculate_geh(modeled: float, observed: float) -> float:
    """Return GEH; zero against zero is defined as a perfect match."""
    if modeled <= 0 and observed <= 0:
        return 0.0
    return math.sqrt(2.0 * (modeled - observed) ** 2 / max(modeled + observed, 1e-9))


def calibrate_assignment(
    run: AssignmentRun,
    observations: Iterable[TrafficObservation],
    *,
    model_version: str,
    holdout_dataset_ids: Iterable[str] = (),
) -> CalibrationSummary:
    """Compare a baseline assignment with measured link observations.

    The function reports metrics but only marks validation complete when an
    explicit independent holdout set exists. A single deterministic assignment
    is one run and therefore cannot satisfy the production readiness gate alone.
    """
    observations = list(observations)
    holdouts = set(holdout_dataset_ids)
    indexed: dict[tuple[int, str, VehicleClass], float] = defaultdict(float)
    for flow in run.edge_flows:
        indexed[(flow.osm_id, flow.time_slice_id, flow.vehicle_class)] += flow.volume
        indexed[(flow.osm_id, flow.time_slice_id, VehicleClass.ALL_MOTOR)] += flow.volume

    geh_values: list[float] = []
    speed_errors: list[float] = []
    used_datasets: set[str] = set()
    holdout_observation_count = 0
    for observation in observations:
        time_key = observation.interval_start or observation.observed_at
        if time_key is None:
            continue
        modeled = indexed.get((observation.osm_id, time_key, observation.vehicle_class), 0.0)
        geh_values.append(calculate_geh(modeled, observation.volume_vph))
        if observation.dataset_id:
            used_datasets.add(observation.dataset_id)
            if observation.dataset_id in holdouts:
                holdout_observation_count += 1
        if observation.speed_kph and observation.speed_kph > 0:
            candidate_times = [
                flow
                for flow in run.edge_flows
                if flow.osm_id == observation.osm_id
                and flow.time_slice_id == time_key
                and flow.assigned_time_seconds > 0
                and flow.free_flow_time_seconds > 0
            ]
            for flow in candidate_times[:1]:
                modeled_speed = observation.speed_kph * (
                    flow.free_flow_time_seconds / flow.assigned_time_seconds
                )
                speed_errors.append(
                    abs(modeled_speed - observation.speed_kph) / observation.speed_kph * 100
                )

    geh_pass = (
        sum(value < 5.0 for value in geh_values) / len(geh_values) * 100 if geh_values else 0.0
    )
    limitations: list[str] = []
    if not geh_values:
        limitations.append("No observations matched assignment time-slice identifiers.")
    if not holdouts:
        limitations.append("No independent holdout dataset was designated.")
    elif holdout_observation_count == 0:
        limitations.append("The designated holdout datasets contain no matched observations.")
    limitations.append("Only one deterministic screening assignment was evaluated.")
    return CalibrationSummary(
        validated=bool(geh_values and holdouts and holdout_observation_count),
        model_version=model_version,
        calibration_dataset_ids=sorted(used_datasets - holdouts),
        holdout_dataset_ids=sorted(used_datasets & holdouts),
        geh_under_5_percent=geh_pass,
        speed_mape_percent=(sum(speed_errors) / len(speed_errors) if speed_errors else None),
        stochastic_runs=1,
        limitations=limitations,
    )
