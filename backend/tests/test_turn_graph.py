import networkx as nx

from app.models.schemas import (
    Coordinates,
    ModificationType,
    StreetModification,
    TurnRestriction,
    VehicleClass,
)
from app.services.network.turn_graph import TurnGraphBuilder


def junction_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for node in range(1, 6):
        graph.add_node(node, x=float(node), y=0.0)
    graph.add_edge(1, 2, key=0, osmid=10, length=100)
    graph.add_edge(2, 3, key=0, osmid=20, length=100)
    graph.add_edge(2, 4, key=0, osmid=30, length=100)
    graph.add_edge(3, 5, key=0, osmid=40, length=100)
    return graph


def test_turn_restriction_removes_only_the_prohibited_movement():
    restriction = TurnRestriction(
        restriction_id="no-turn-1",
        from_osm_id=10,
        via_node_id=2,
        to_osm_id=20,
        kind="no_turn",
        source="Road authority",
    )
    turn_graph = TurnGraphBuilder(junction_graph(), [restriction]).build()

    assert not turn_graph.graph.has_edge((1, 2, 0), (2, 3, 0))
    assert turn_graph.graph.has_edge((1, 2, 0), (2, 4, 0))


def test_only_turn_removes_all_other_exits_for_matching_vehicle_class():
    restriction = TurnRestriction(
        restriction_id="only-turn-1",
        from_osm_id=10,
        via_node_id=2,
        to_osm_id=20,
        kind="only_turn",
        applies_to=[VehicleClass.CAR],
        source="Road authority",
    )
    car_graph = TurnGraphBuilder(junction_graph(), [restriction]).build(VehicleClass.CAR)
    emergency_graph = TurnGraphBuilder(junction_graph(), [restriction]).build(
        VehicleClass.EMERGENCY
    )

    assert car_graph.graph.has_edge((1, 2, 0), (2, 3, 0))
    assert not car_graph.graph.has_edge((1, 2, 0), (2, 4, 0))
    assert emergency_graph.graph.has_edge((1, 2, 0), (2, 4, 0))


def test_modal_filter_permissions_are_vehicle_class_specific():
    modification = StreetModification(
        u=2,
        v=3,
        osm_id=20,
        modification_type=ModificationType.MODAL_FILTER,
        filter_location=Coordinates(lat=0, lon=0),
        allowed_vehicle_classes=[VehicleClass.EMERGENCY],
    )
    builder = TurnGraphBuilder(junction_graph())
    car_graph = builder.build(VehicleClass.CAR, [modification])
    emergency_graph = builder.build(VehicleClass.EMERGENCY, [modification])

    assert (2, 3, 0) not in car_graph.graph
    assert (2, 3, 0) in emergency_graph.graph


def test_sector_validation_uses_legal_turn_paths():
    turn_graph = TurnGraphBuilder(junction_graph()).build()
    violations = turn_graph.sector_violations({0: [1], 1: [3], 2: [4]})

    assert (0, 1) in violations
    assert (0, 2) in violations


def test_timed_closure_applies_only_in_its_own_window():
    closure = StreetModification(
        u=2,
        v=3,
        osm_id=20,
        modification_type=ModificationType.FULL_CLOSURE,
        time_windows=["Mo-Fr 07:30-08:30"],
    )
    builder = TurnGraphBuilder(junction_graph(), [])

    in_window = builder.build(modifications=[closure], active_time_window="Mo-Fr 07:30-08:30")
    other_window = builder.build(modifications=[closure], active_time_window="Sa 12:00-13:00")
    no_window = builder.build(modifications=[closure])

    assert (2, 3, 0) not in in_window.edge_data
    assert (2, 3, 0) in other_window.edge_data
    assert (2, 3, 0) not in no_window.edge_data


def test_one_way_exemption_keeps_buses_in_both_directions():
    graph = junction_graph()
    graph.add_edge(3, 2, key=0, osmid=20, length=100)
    one_way = StreetModification(
        u=2,
        v=3,
        osm_id=20,
        modification_type=ModificationType.ONE_WAY,
        direction="u_to_v",
        allowed_vehicle_classes=[VehicleClass.BUS, VehicleClass.EMERGENCY],
    )
    builder = TurnGraphBuilder(graph, [])

    assert (3, 2, 0) not in builder.build(VehicleClass.CAR, [one_way]).edge_data
    assert (3, 2, 0) in builder.build(VehicleClass.BUS, [one_way]).edge_data
