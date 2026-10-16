"""Evaluate feasible alternatives and return a transparent Pareto portfolio."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

import networkx as nx

from app.models.schemas import (
    ActiveModeRequirement,
    AssignmentSettings,
    DemandModel,
    ObjectiveName,
    OperationalRequirement,
    OptimizationSettings,
    ScenarioCandidate,
    ScenarioMetric,
    ScenarioPortfolio,
    StreetModification,
    TurnRestriction,
    VehicleClass,
)
from app.services.assignment.network_assignment import NetworkAssignmentEngine
from app.services.constraint.active_mode_validator import ActiveModeValidator
from app.services.constraint.operational_validator import OperationalValidator

DEFAULT_CAPITAL_COST = {
    "modal_filter": 8_000.0,
    "one_way": 3_000.0,
    "two_way": 8_000.0,
    "full_closure": 30_000.0,
    "turn_restriction": 2_500.0,
}
DEFAULT_MAINTENANCE_COST = {
    "modal_filter": 500.0,
    "one_way": 150.0,
    "two_way": 300.0,
    "full_closure": 1_000.0,
    "turn_restriction": 100.0,
}
EMISSION_FACTORS_G_PER_KM = {
    VehicleClass.ALL_MOTOR: 180.0,
    VehicleClass.CAR: 180.0,
    VehicleClass.MOTORCYCLE: 95.0,
    VehicleClass.LIGHT_GOODS: 260.0,
    VehicleClass.HEAVY_GOODS: 780.0,
    VehicleClass.BUS: 900.0,
    VehicleClass.TAXI: 180.0,
    VehicleClass.REFUSE: 900.0,
    VehicleClass.EMERGENCY: 260.0,
    VehicleClass.PERMIT: 180.0,
}


class ScenarioOptimizer:
    """Run all hard constraints, then rank non-dominated feasible scenarios."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        restrictions: Iterable[TurnRestriction] = (),
        active_mode_graphs: dict[str, nx.MultiDiGraph] | None = None,
    ) -> None:
        self.graph = graph
        self.restrictions = list(restrictions)
        self.active_mode_graphs = active_mode_graphs or {}

    def optimize(
        self,
        *,
        demand: DemandModel,
        alternatives: Iterable[tuple[str, str, list[StreetModification]]],
        boundary_osm_ids: set[int],
        operational_requirements: Iterable[OperationalRequirement] = (),
        active_mode_requirements: Iterable[ActiveModeRequirement] = (),
        assignment_settings: AssignmentSettings | None = None,
        optimization_settings: OptimizationSettings | None = None,
    ) -> ScenarioPortfolio:
        assignment_settings = assignment_settings or AssignmentSettings()
        optimization_settings = optimization_settings or OptimizationSettings()
        operational_requirements = list(operational_requirements)
        active_mode_requirements = list(active_mode_requirements)
        candidates = [
            self._evaluate(
                demand=demand,
                name=name,
                description=description,
                modifications=modifications,
                boundary_osm_ids=boundary_osm_ids,
                operational_requirements=operational_requirements,
                active_mode_requirements=active_mode_requirements,
                assignment_settings=assignment_settings,
                optimization_settings=optimization_settings,
            )
            for name, description, modifications in alternatives
        ]
        if not candidates:
            raise ValueError("at least one scenario alternative is required")
        self._score(candidates, optimization_settings)
        pareto_ids = [item.scenario_id for item in candidates if item.pareto_rank == 1]
        recommended = min(
            (item for item in candidates if item.scenario_id in pareto_ids),
            key=lambda item: (
                item.weighted_score if item.weighted_score is not None else float("inf")
            ),
            default=None,
        )
        material = "|".join(sorted(item.scenario_id for item in candidates)).encode()
        return ScenarioPortfolio(
            portfolio_id=hashlib.sha256(material).hexdigest()[:24],
            candidates=candidates,
            pareto_scenario_ids=pareto_ids,
            objective_weights=optimization_settings.objective_weights,
            recommended_scenario_id=recommended.scenario_id if recommended else None,
            limitations=[
                "Only feasible candidates supplied to this optimizer can appear on the Pareto frontier.",
                "Screening emissions are factor-based; validated safety, noise, air-quality, and equity models remain separate evidence requirements.",
            ],
        )

    def _evaluate(
        self,
        *,
        demand: DemandModel,
        name: str,
        description: str,
        modifications: list[StreetModification],
        boundary_osm_ids: set[int],
        operational_requirements: list[OperationalRequirement],
        active_mode_requirements: list[ActiveModeRequirement],
        assignment_settings: AssignmentSettings,
        optimization_settings: OptimizationSettings,
    ) -> ScenarioCandidate:
        assignment = NetworkAssignmentEngine(self.graph, self.restrictions).compare(
            demand,
            modifications,
            boundary_osm_ids,
            assignment_settings,
        )
        operational = OperationalValidator(self.graph, self.restrictions).validate(
            operational_requirements,
            modifications,
        )
        active = ActiveModeValidator(self.active_mode_graphs).validate(
            active_mode_requirements,
            modifications,
        )
        capital = sum(
            modification.estimated_capital_cost
            if modification.estimated_capital_cost is not None
            else DEFAULT_CAPITAL_COST[modification.modification_type.value]
            for modification in modifications
        )
        maintenance = sum(
            modification.annual_maintenance_cost
            if modification.annual_maintenance_cost is not None
            else DEFAULT_MAINTENANCE_COST[modification.modification_type.value]
            for modification in modifications
        )
        baseline_delay = self._delay_hours(assignment.baseline)
        planned_delay = self._delay_hours(assignment.planned)
        baseline_emissions = self._emissions_grams(assignment.baseline)
        planned_emissions = self._emissions_grams(assignment.planned)
        metrics = [
            ScenarioMetric(
                objective=ObjectiveName.VEHICLE_DELAY,
                baseline_value=baseline_delay,
                planned_value=planned_delay,
                unit="vehicle-hours/hour",
                source="MSA BPR screening assignment",
                validated=bool(assignment.baseline and assignment.planned and assignment.validated),
            ),
            ScenarioMetric(
                objective=ObjectiveName.BOUNDARY_SATURATION,
                planned_value=assignment.maximum_boundary_volume_capacity_ratio or 0.0,
                unit="v/c",
                source="MSA BPR screening assignment",
                validated=assignment.validated,
            ),
            ScenarioMetric(
                objective=ObjectiveName.ADJACENT_SPILLOVER,
                planned_value=assignment.maximum_adjacent_road_volume_change_percent or 0.0,
                unit="percent",
                source="Baseline versus plan link assignment",
                validated=assignment.validated,
            ),
            ScenarioMetric(
                objective=ObjectiveName.BLOCKED_DEMAND,
                planned_value=assignment.planned.blocked_demand if assignment.planned else 0.0,
                unit="trips",
                source="Legal path assignment",
                validated=assignment.planned is not None,
            ),
            ScenarioMetric(
                objective=ObjectiveName.EMISSIONS,
                baseline_value=baseline_emissions,
                planned_value=planned_emissions,
                unit="grams/hour",
                source="Class factor times assigned vehicle-kilometres",
                validated=False,
            ),
            ScenarioMetric(
                objective=ObjectiveName.INTERVENTION_COUNT,
                planned_value=float(len(modifications)),
                unit="actions",
                source="Scenario definition",
                validated=True,
            ),
            ScenarioMetric(
                objective=ObjectiveName.CAPITAL_COST,
                planned_value=capital,
                unit="currency",
                source="User cost or transparent treatment default",
                validated=all(item.estimated_capital_cost is not None for item in modifications),
            ),
            ScenarioMetric(
                objective=ObjectiveName.ANNUAL_MAINTENANCE_COST,
                planned_value=maintenance,
                unit="currency/year",
                source="User cost or transparent treatment default",
                validated=all(item.annual_maintenance_cost is not None for item in modifications),
            ),
        ]
        failures: list[str] = []
        if not assignment.all_demand_assigned:
            failures.append("Some OD demand has no legal path")
        if not assignment.boundary_capacity_passed:
            failures.append("Boundary capacity threshold exceeded")
        if not assignment.spillover_passed:
            failures.append("Adjacent-road spillover threshold exceeded")
        if not (assignment.baseline and assignment.baseline.converged):
            failures.append("Baseline assignment did not converge")
        if not (assignment.planned and assignment.planned.converged):
            failures.append("Planned assignment did not converge")
        if (
            optimization_settings.require_operational_validation
            and not operational.all_hard_constraints_passed
        ):
            failures.append("Essential-service requirements are incomplete or failed")
        if (
            optimization_settings.require_active_mode_validation
            and not active.all_hard_constraints_passed
        ):
            failures.append("Walking, wheeling, or cycling requirements are incomplete or failed")
        if (
            optimization_settings.capital_budget is not None
            and capital > optimization_settings.capital_budget
        ):
            failures.append("Capital budget exceeded")
        if (
            optimization_settings.annual_maintenance_budget is not None
            and maintenance > optimization_settings.annual_maintenance_budget
        ):
            failures.append("Annual maintenance budget exceeded")
        if (
            optimization_settings.maximum_interventions is not None
            and len(modifications) > optimization_settings.maximum_interventions
        ):
            failures.append("Maximum intervention count exceeded")
        scenario_material = repr(
            (
                name,
                [
                    (item.u, item.v, item.key, item.modification_type.value)
                    for item in modifications
                ],
            )
        ).encode()
        return ScenarioCandidate(
            scenario_id=hashlib.sha256(scenario_material).hexdigest()[:24],
            name=name,
            description=description,
            modifications=modifications,
            metrics=metrics,
            hard_constraint_failures=failures,
            feasible=not failures,
            assignment_comparison=assignment,
            operational_validation=operational,
            active_mode_validation=active,
        )

    def _delay_hours(self, run) -> float:
        if run is None:
            return 0.0
        return sum(
            flow.volume * max(0.0, flow.assigned_time_seconds - flow.free_flow_time_seconds) / 3_600
            for flow in run.edge_flows
        )

    def _emissions_grams(self, run) -> float:
        if run is None:
            return 0.0
        total = 0.0
        for flow in run.edge_flows:
            if not self.graph.has_edge(flow.u, flow.v, flow.key):
                continue
            length_km = float(self.graph[flow.u][flow.v][flow.key].get("length", 0) or 0) / 1_000
            total += flow.volume * length_km * EMISSION_FACTORS_G_PER_KM[flow.vehicle_class]
        return total

    @staticmethod
    def _score(candidates: list[ScenarioCandidate], settings: OptimizationSettings) -> None:
        objectives = {
            objective for objective, weight in settings.objective_weights.items() if weight > 0
        }
        ranges = {}
        for objective in objectives:
            values = [
                metric.planned_value
                for candidate in candidates
                for metric in candidate.metrics
                if metric.objective == objective
            ]
            if values:
                ranges[objective] = (min(values), max(values))
        for candidate in candidates:
            if not candidate.feasible:
                candidate.weighted_score = None
                continue
            metric_by_objective = {metric.objective: metric for metric in candidate.metrics}
            weighted_sum = 0.0
            weight_sum = 0.0
            for objective, weight in settings.objective_weights.items():
                if weight <= 0 or objective not in metric_by_objective or objective not in ranges:
                    continue
                low, high = ranges[objective]
                value = metric_by_objective[objective].planned_value
                normalized = 0.0 if high == low else (value - low) / (high - low)
                if not metric_by_objective[objective].lower_is_better:
                    normalized = 1.0 - normalized
                weighted_sum += weight * normalized
                weight_sum += weight
            candidate.weighted_score = weighted_sum / weight_sum if weight_sum else 0.0

        remaining = [candidate for candidate in candidates if candidate.feasible]
        rank = 1
        while remaining:
            front = [
                candidate
                for candidate in remaining
                if not any(
                    ScenarioOptimizer._dominates(other, candidate, objectives)
                    for other in remaining
                    if other is not candidate
                )
            ]
            for candidate in front:
                candidate.pareto_rank = rank
                remaining.remove(candidate)
            rank += 1

    @staticmethod
    def _dominates(
        left: ScenarioCandidate,
        right: ScenarioCandidate,
        objectives: set[ObjectiveName],
    ) -> bool:
        left_metrics = {
            item.objective: item for item in left.metrics if item.objective in objectives
        }
        right_metrics = {
            item.objective: item for item in right.metrics if item.objective in objectives
        }
        shared = set(left_metrics) & set(right_metrics)
        if not shared:
            return False
        no_worse = True
        strictly_better = False
        for objective in shared:
            left_value = left_metrics[objective].planned_value
            right_value = right_metrics[objective].planned_value
            if left_metrics[objective].lower_is_better:
                no_worse &= left_value <= right_value
                strictly_better |= left_value < right_value
            else:
                no_worse &= left_value >= right_value
                strictly_better |= left_value > right_value
        return no_worse and strictly_better
