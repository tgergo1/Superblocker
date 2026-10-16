"""Baseline-versus-plan checks for essential vehicle operations."""

from collections.abc import Iterable

import networkx as nx

from app.models.schemas import (
    OperationalRequirement,
    OperationalValidationResult,
    OperationalValidationSummary,
    StreetModification,
    TurnRestriction,
)
from app.services.network.turn_graph import TurnGraph, TurnGraphBuilder

REQUIRED_SERVICE_TYPES = {
    "emergency_response",
    "transit_operation",
    "freight_delivery",
    "refuse_collection",
}


class OperationalValidator:
    """Validate essential service trips on baseline and modified networks."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        restrictions: Iterable[TurnRestriction] = (),
    ) -> None:
        self.graph = graph
        self.restrictions = list(restrictions)
        self._nodes = list(graph.nodes(data=True))

    def validate(
        self,
        requirements: Iterable[OperationalRequirement],
        modifications: Iterable[StreetModification],
    ) -> OperationalValidationSummary:
        requirements = list(requirements)
        modifications = list(modifications)
        if not requirements:
            return OperationalValidationSummary(
                validated=False,
                all_hard_constraints_passed=False,
                missing_service_types=sorted(REQUIRED_SERVICE_TYPES),
            )

        builder = TurnGraphBuilder(self.graph, self.restrictions)
        graph_cache: dict[tuple[str, str | None, bool], TurnGraph] = {}
        results: list[OperationalValidationResult] = []
        for requirement in requirements:
            cache_key = (
                requirement.vehicle_class.value,
                requirement.time_window,
                False,
            )
            baseline = graph_cache.setdefault(
                cache_key,
                builder.build(
                    requirement.vehicle_class,
                    active_time_window=requirement.time_window,
                ),
            )
            planned_key = (
                requirement.vehicle_class.value,
                requirement.time_window,
                True,
            )
            planned = graph_cache.setdefault(
                planned_key,
                builder.build(
                    requirement.vehicle_class,
                    modifications,
                    active_time_window=requirement.time_window,
                ),
            )
            results.append(self._validate_one(requirement, baseline, planned))

        supplied_types = {requirement.service_type for requirement in requirements}
        missing_service_types = sorted(REQUIRED_SERVICE_TYPES - supplied_types)
        hard_results = [
            result
            for result, requirement in zip(results, requirements, strict=True)
            if requirement.hard_constraint
        ]
        return OperationalValidationSummary(
            validated=True,
            all_hard_constraints_passed=bool(hard_results)
            and all(result.passed for result in hard_results),
            results=results,
            missing_service_types=missing_service_types,
        )

    def _validate_one(
        self,
        requirement: OperationalRequirement,
        baseline: TurnGraph,
        planned: TurnGraph,
    ) -> OperationalValidationResult:
        origin = self._nearest_node(requirement.origin.lon, requirement.origin.lat)
        destination = self._nearest_node(
            requirement.destination.lon,
            requirement.destination.lat,
        )
        blockers: list[str] = []
        if origin is None or destination is None:
            blockers.append("Origin or destination cannot be snapped to the supplied network")
            return OperationalValidationResult(
                requirement_id=requirement.requirement_id,
                service_type=requirement.service_type,
                vehicle_class=requirement.vehicle_class,
                passed=False,
                blockers=blockers,
            )

        baseline_path = baseline.shortest_path_between_nodes(origin, destination)
        planned_path = planned.shortest_path_between_nodes(origin, destination)
        if baseline_path is None:
            blockers.append("Baseline service route is unavailable")
        if planned_path is None:
            blockers.append("The proposed plan removes this service route")

        baseline_minutes = baseline_path[0] if baseline_path else None
        planned_minutes = planned_path[0] if planned_path else None
        detour_percent = None
        if baseline_minutes is not None and planned_minutes is not None:
            if baseline_minutes > 0:
                detour_percent = (planned_minutes / baseline_minutes - 1) * 100
            else:
                detour_percent = 0.0
            if detour_percent > requirement.maximum_detour_percent:
                blockers.append(
                    f"Travel-time detour {detour_percent:.1f}% exceeds "
                    f"{requirement.maximum_detour_percent:.1f}%"
                )
            if (
                requirement.maximum_travel_time_minutes is not None
                and planned_minutes > requirement.maximum_travel_time_minutes
            ):
                blockers.append(
                    f"Travel time {planned_minutes:.1f} min exceeds "
                    f"{requirement.maximum_travel_time_minutes:.1f} min"
                )

        clearance_passed = None
        if requirement.minimum_clearance_m is not None and planned_path is not None:
            clearance_passed = self._clearance_passed(
                planned,
                planned_path[1],
                requirement.minimum_clearance_m,
            )
            if clearance_passed is None:
                blockers.append("Authoritative clearance is missing on the planned route")
            elif not clearance_passed:
                blockers.append(
                    f"Planned route clearance is below {requirement.minimum_clearance_m:.2f} m"
                )

        return OperationalValidationResult(
            requirement_id=requirement.requirement_id,
            service_type=requirement.service_type,
            vehicle_class=requirement.vehicle_class,
            passed=not blockers,
            baseline_travel_time_minutes=baseline_minutes,
            planned_travel_time_minutes=planned_minutes,
            detour_percent=detour_percent,
            clearance_passed=clearance_passed,
            blockers=blockers,
        )

    def _nearest_node(self, lon: float, lat: float) -> int | None:
        candidates = [
            (
                (float(data.get("x", 0)) - lon) ** 2 + (float(data.get("y", 0)) - lat) ** 2,
                int(node),
            )
            for node, data in self._nodes
            if "x" in data and "y" in data
        ]
        return min(candidates)[1] if candidates else None

    @staticmethod
    def _clearance_passed(
        turn_graph: TurnGraph,
        path: list[tuple[int, int, int]],
        required_clearance_m: float,
    ) -> bool | None:
        clearances: list[float] = []
        for state in path:
            value = turn_graph.edge_data[state].get("clearance_m")
            if value is None:
                return None
            try:
                clearances.append(float(value))
            except (TypeError, ValueError):
                return None
        return bool(clearances) and min(clearances) >= required_clearance_m
