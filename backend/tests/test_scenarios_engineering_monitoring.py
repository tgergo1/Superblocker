from app.models.schemas import (
    ModificationType,
    MonitoringMetricDefinition,
    MonitoringObservation,
    OptimizationSettings,
    StreetModification,
)
from app.services.engineering.package_compiler import EngineeringPackageCompiler
from app.services.monitoring.rollback import evaluate_monitoring
from app.services.optimization.scenario_optimizer import ScenarioOptimizer
from tests.test_network_assignment import assignment_graph, demand_model


def test_scenario_optimizer_enforces_hard_limits_and_returns_pareto_set():
    modification = StreetModification(
        u=2,
        v=4,
        osm_id=20,
        modification_type=ModificationType.MODAL_FILTER,
        estimated_capital_cost=8_000,
        annual_maintenance_cost=500,
    )
    portfolio = ScenarioOptimizer(assignment_graph()).optimize(
        demand=demand_model(),
        alternatives=[
            ("No changes", "Baseline comparator", []),
            ("Filter", "Reroute the northern branch", [modification]),
        ],
        boundary_osm_ids={40},
        optimization_settings=OptimizationSettings(
            capital_budget=5_000,
            require_operational_validation=False,
            require_active_mode_validation=False,
        ),
    )

    assert len(portfolio.candidates) == 2
    assert portfolio.candidates[0].feasible
    assert not portfolio.candidates[1].feasible
    assert "Capital budget exceeded" in portfolio.candidates[1].hard_constraint_failures
    assert portfolio.pareto_scenario_ids == [portfolio.candidates[0].scenario_id]
    assert portfolio.recommended_scenario_id == portfolio.candidates[0].scenario_id


def test_engineering_compiler_creates_costed_gis_package():
    modification = StreetModification(
        u=1,
        v=2,
        osm_id=10,
        name="Test Street",
        modification_type=ModificationType.MODAL_FILTER,
    )
    package = EngineeringPackageCompiler(assignment_graph()).compile(
        "a" * 64,
        [modification],
        jurisdiction="Test City",
    )

    assert package.status == "draft_for_engineering_review"
    assert package.instructions[0].street_name == "Test Street"
    assert package.instructions[0].coordinates is not None
    assert package.total_capital_cost == 8_000
    assert package.gis_feature_collection["features"][0]["geometry"]["type"] == "Point"
    assert any("schedule-of-rates" in blocker for blocker in package.blockers)


def test_monitoring_rollback_takes_precedence_over_adaptation():
    definitions = [
        MonitoringMetricDefinition(
            metric_id="emergency-time",
            name="Emergency response time",
            unit="minutes",
            direction="increase_is_harm",
            baseline_value=10,
            adapt_threshold_percent=5,
            rollback_threshold_percent=10,
            source="Dispatch system",
        )
    ]
    result = evaluate_monitoring(
        plan_id="a" * 64,
        evaluated_at="2026-09-02T12:00:00Z",
        definitions=definitions,
        observations=[
            MonitoringObservation(
                metric_id="emergency-time",
                observed_at="2026-09-02T11:00:00Z",
                value=11.5,
                sample_count=20,
                source_reference="dispatch://sample",
            )
        ],
    )

    assert result.action == "rollback"
    assert result.triggered_metric_ids == ["emergency-time"]
    assert result.metric_changes_percent["emergency-time"] == 15


def test_monitoring_fails_closed_on_missing_samples():
    result = evaluate_monitoring(
        plan_id="a" * 64,
        evaluated_at="2026-09-02T12:00:00Z",
        definitions=[
            MonitoringMetricDefinition(
                metric_id="volume",
                name="Boundary traffic",
                unit="vehicles/hour",
                direction="increase_is_harm",
                baseline_value=100,
                adapt_threshold_percent=5,
                rollback_threshold_percent=10,
                minimum_samples=10,
                source="Counter",
            )
        ],
        observations=[],
    )

    assert result.action == "insufficient_data"
