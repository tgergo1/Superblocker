"""Walking, wheeling, and cycling continuity and geometry validation."""

from collections.abc import Iterable
from itertools import pairwise
from typing import Any

import networkx as nx

from app.models.schemas import (
    ActiveModeRequirement,
    ActiveModeValidationResult,
    ActiveModeValidationSummary,
    ModificationType,
    StreetModification,
    TransportMode,
)

REQUIRED_MODES = {"walk", "wheel", "bicycle"}


def _number(value: Any) -> float | None:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    for item in values:
        try:
            return float(str(item).lower().replace("m", "").strip())
        except (TypeError, ValueError):
            continue
    return None


class ActiveModeValidator:
    """Compare required active-mode paths before and after plan changes."""

    def __init__(self, graphs: dict[str, nx.MultiDiGraph]) -> None:
        self.graphs = graphs

    def validate(
        self,
        requirements: Iterable[ActiveModeRequirement],
        modifications: Iterable[StreetModification],
    ) -> ActiveModeValidationSummary:
        requirements = list(requirements)
        modifications = list(modifications)
        if not requirements:
            return ActiveModeValidationSummary(
                validated=False,
                all_hard_constraints_passed=False,
                missing_modes=sorted(REQUIRED_MODES),
            )

        planned_graphs = {
            mode: self._apply_modifications(graph, mode, modifications)
            for mode, graph in self.graphs.items()
        }
        results: list[ActiveModeValidationResult] = []
        for requirement in requirements:
            baseline = self.graphs.get(requirement.mode)
            planned = planned_graphs.get(requirement.mode)
            if baseline is None or planned is None:
                results.append(
                    ActiveModeValidationResult(
                        requirement_id=requirement.requirement_id,
                        mode=requirement.mode,
                        passed=False,
                        blockers=[f"No {requirement.mode} network was supplied"],
                    )
                )
                continue
            results.append(self._validate_one(requirement, baseline, planned))

        supplied_modes = {requirement.mode for requirement in requirements}
        hard_results = [
            result
            for result, requirement in zip(results, requirements, strict=True)
            if requirement.hard_constraint
        ]
        return ActiveModeValidationSummary(
            validated=True,
            all_hard_constraints_passed=bool(hard_results)
            and all(result.passed for result in hard_results),
            results=results,
            missing_modes=sorted(REQUIRED_MODES - supplied_modes),
        )

    def _validate_one(
        self,
        requirement: ActiveModeRequirement,
        baseline: nx.MultiDiGraph,
        planned: nx.MultiDiGraph,
    ) -> ActiveModeValidationResult:
        baseline_nodes = self._snap_pair(baseline, requirement)
        planned_nodes = self._snap_pair(planned, requirement)
        blockers: list[str] = []
        if baseline_nodes is None or planned_nodes is None:
            blockers.append("Origin or destination cannot be snapped to the mode network")
            return ActiveModeValidationResult(
                requirement_id=requirement.requirement_id,
                mode=requirement.mode,
                passed=False,
                blockers=blockers,
            )
        baseline_path = self._shortest_path(baseline, *baseline_nodes)
        planned_path = self._shortest_path(planned, *planned_nodes)
        if baseline_path is None:
            blockers.append("Baseline active-mode route is unavailable")
        if planned_path is None:
            blockers.append("The proposed plan removes this active-mode route")

        baseline_distance = baseline_path[0] if baseline_path else None
        planned_distance = planned_path[0] if planned_path else None
        detour_percent = None
        geometry_passed = None
        if baseline_distance is not None and planned_distance is not None:
            detour_percent = (
                (planned_distance / baseline_distance - 1) * 100 if baseline_distance > 0 else 0.0
            )
            if detour_percent > requirement.maximum_distance_detour_percent:
                blockers.append(
                    f"Distance detour {detour_percent:.1f}% exceeds "
                    f"{requirement.maximum_distance_detour_percent:.1f}%"
                )
            geometry_passed, geometry_blockers = self._validate_geometry(
                planned,
                planned_path[1],
                requirement,
            )
            blockers.extend(geometry_blockers)

        return ActiveModeValidationResult(
            requirement_id=requirement.requirement_id,
            mode=requirement.mode,
            passed=not blockers,
            baseline_distance_m=baseline_distance,
            planned_distance_m=planned_distance,
            detour_percent=detour_percent,
            geometry_attributes_passed=geometry_passed,
            blockers=blockers,
        )

    @staticmethod
    def _snap_pair(
        graph: nx.MultiDiGraph,
        requirement: ActiveModeRequirement,
    ) -> tuple[int, int] | None:
        nodes = [
            (int(node), data)
            for node, data in graph.nodes(data=True)
            if "x" in data and "y" in data
        ]
        if not nodes:
            return None

        def nearest(lon: float, lat: float) -> int:
            return min(
                nodes,
                key=lambda item: (
                    (float(item[1]["x"]) - lon) ** 2 + (float(item[1]["y"]) - lat) ** 2
                ),
            )[0]

        return (
            nearest(requirement.origin.lon, requirement.origin.lat),
            nearest(requirement.destination.lon, requirement.destination.lat),
        )

    @staticmethod
    def _shortest_path(
        graph: nx.MultiDiGraph,
        source: int,
        target: int,
    ) -> tuple[float, list[int]] | None:
        try:
            path = nx.shortest_path(graph, source, target, weight="length")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        distance = 0.0
        for u, v in pairwise(path):
            edges = graph.get_edge_data(u, v, default={})
            distance += min(
                (max(0.0, float(data.get("length", 0) or 0)) for data in edges.values()),
                default=0.0,
            )
        return distance, path

    @staticmethod
    def _validate_geometry(
        graph: nx.MultiDiGraph,
        path: list[int],
        requirement: ActiveModeRequirement,
    ) -> tuple[bool | None, list[str]]:
        if all(
            value is None
            for value in (
                requirement.minimum_clear_width_m,
                requirement.maximum_grade_percent,
                requirement.maximum_level_of_traffic_stress,
            )
        ):
            return None, []
        blockers: list[str] = []
        for u, v in pairwise(path):
            edges = graph.get_edge_data(u, v, default={})
            data = min(
                edges.values(),
                key=lambda item: float(item.get("length", 0) or 0),
                default=None,
            )
            if data is None:
                continue
            if requirement.minimum_clear_width_m is not None:
                width = _number(data.get("clear_width_m", data.get("width")))
                if width is None:
                    blockers.append("Surveyed clear width is missing on the planned route")
                    break
                if width < requirement.minimum_clear_width_m:
                    blockers.append("Planned route is narrower than the required clear width")
                    break
            if requirement.maximum_grade_percent is not None:
                grade = _number(data.get("grade"))
                if grade is None:
                    blockers.append("Surveyed grade is missing on the planned route")
                    break
                grade_percent = abs(grade) * 100 if abs(grade) <= 1 else abs(grade)
                if grade_percent > requirement.maximum_grade_percent:
                    blockers.append("Planned route exceeds the maximum grade")
                    break
            if requirement.maximum_level_of_traffic_stress is not None:
                lts = _number(data.get("level_of_traffic_stress"))
                if lts is None:
                    blockers.append("Traffic-stress evidence is missing on the cycling route")
                    break
                if lts > requirement.maximum_level_of_traffic_stress:
                    blockers.append("Cycling route exceeds the maximum traffic-stress level")
                    break
        return not blockers, blockers

    @staticmethod
    def _apply_modifications(
        graph: nx.MultiDiGraph,
        mode: str,
        modifications: list[StreetModification],
    ) -> nx.MultiDiGraph:
        planned = graph.copy()
        transport_mode = TransportMode(mode)
        for modification in modifications:
            if modification.modification_type not in {
                ModificationType.MODAL_FILTER,
                ModificationType.FULL_CLOSURE,
            }:
                continue
            if transport_mode in modification.allowed_modes:
                continue
            removed = False
            for u, v in (
                (modification.u, modification.v),
                (modification.v, modification.u),
            ):
                if planned.has_edge(u, v):
                    for key in list(planned[u][v].keys()):
                        planned.remove_edge(u, v, key)
                        removed = True
            if removed:
                continue
            for u, v, key, data in list(planned.edges(keys=True, data=True)):
                osmids = data.get("osmid", [])
                if not isinstance(osmids, (list, tuple, set)):
                    osmids = [osmids]
                if modification.osm_id in {int(value) for value in osmids if value}:
                    planned.remove_edge(u, v, key)
        return planned
