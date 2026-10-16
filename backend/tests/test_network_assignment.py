import networkx as nx

from app.models.schemas import (
    AssignmentSettings,
    Coordinates,
    DemandMatrixEntry,
    DemandModel,
    DemandTimeSlice,
    DemandZone,
    ModificationType,
    StreetModification,
    TrafficObservation,
    VehicleClass,
)
from app.services.assignment.network_assignment import (
    NetworkAssignmentEngine,
    calculate_geh,
    calibrate_assignment,
)


def assignment_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=0.0, y=0.0)
    graph.add_node(2, x=1.0, y=1.0)
    graph.add_node(3, x=1.0, y=-1.0)
    graph.add_node(4, x=2.0, y=0.0)
    graph.add_edge(1, 2, key=0, osmid=10, length=100, maxspeed=30, capacity=1_000)
    graph.add_edge(2, 4, key=0, osmid=20, length=100, maxspeed=30, capacity=1_000)
    graph.add_edge(1, 3, key=0, osmid=30, length=120, maxspeed=30, capacity=1_000)
    graph.add_edge(3, 4, key=0, osmid=40, length=120, maxspeed=30, capacity=1_000)
    return graph


def demand_model() -> DemandModel:
    return DemandModel(
        model_id="test-demand",
        version="1",
        source="Test fixture",
        zones=[
            DemandZone(
                zone_id="origin", centroid=Coordinates(lat=0, lon=0), connector_node_ids=[1]
            ),
            DemandZone(
                zone_id="destination", centroid=Coordinates(lat=0, lon=2), connector_node_ids=[4]
            ),
        ],
        time_slices=[
            DemandTimeSlice(
                time_slice_id="am",
                start_time="08:00",
                end_time="09:00",
                duration_minutes=60,
            )
        ],
        matrix=[
            DemandMatrixEntry(
                origin_zone_id="origin",
                destination_zone_id="destination",
                time_slice_id="am",
                vehicle_class=VehicleClass.CAR,
                trips=100,
            )
        ],
    )


def test_assignment_compares_baseline_and_modified_networks():
    modification = StreetModification(
        u=2,
        v=4,
        osm_id=20,
        modification_type=ModificationType.MODAL_FILTER,
    )
    comparison = NetworkAssignmentEngine(assignment_graph()).compare(
        demand_model(),
        [modification],
        {40},
        AssignmentSettings(max_iterations=10, maximum_adjacent_road_volume_change_percent=150),
    )

    assert comparison.baseline is not None
    assert comparison.planned is not None
    assert comparison.baseline.converged
    assert comparison.planned.converged
    assert comparison.all_demand_assigned
    assert {(flow.osm_id, flow.volume) for flow in comparison.baseline.edge_flows} == {
        (10, 100),
        (20, 100),
    }
    assert {(flow.osm_id, flow.volume) for flow in comparison.planned.edge_flows} == {
        (30, 100),
        (40, 100),
    }


def test_assignment_reports_demand_blocked_by_plan():
    modifications = [
        StreetModification(
            u=2,
            v=4,
            osm_id=20,
            modification_type=ModificationType.FULL_CLOSURE,
        ),
        StreetModification(
            u=3,
            v=4,
            osm_id=40,
            modification_type=ModificationType.FULL_CLOSURE,
        ),
    ]
    comparison = NetworkAssignmentEngine(assignment_graph()).compare(
        demand_model(), modifications, set()
    )

    assert comparison.planned is not None
    assert comparison.planned.blocked_demand == 100
    assert not comparison.all_demand_assigned
    assert not comparison.validated


def test_calibration_requires_and_reports_holdout_observations():
    run = NetworkAssignmentEngine(assignment_graph()).assign(
        demand_model(),
        [],
        set(),
        AssignmentSettings(),
        network_state="baseline",
    )
    summary = calibrate_assignment(
        run,
        [
            TrafficObservation(
                osm_id=10,
                volume_vph=100,
                source="Counter",
                interval_start="am",
                dataset_id="holdout",
            )
        ],
        model_version="test-demand:1",
        holdout_dataset_ids=["holdout"],
    )

    assert summary.validated
    assert summary.geh_under_5_percent == 100
    assert summary.holdout_dataset_ids == ["holdout"]
    assert summary.stochastic_runs == 1
    assert calculate_geh(0, 0) == 0
