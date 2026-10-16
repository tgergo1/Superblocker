import networkx as nx

from app.models.schemas import AuthoritativeRoadSegment, VehicleClass
from app.services.network.conflation import NetworkConflator


def test_authoritative_conflation_applies_stable_rules_and_reports_coverage():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=19.0, y=47.0)
    graph.add_node(2, x=19.001, y=47.0)
    graph.add_edge(1, 2, key=0, osmid=10, length=76, highway="residential")
    graph.add_edge(2, 1, key=0, osmid=10, length=76, highway="residential")
    inventory = [
        AuthoritativeRoadSegment(
            stable_id="authority-road-1",
            geometry={
                "type": "LineString",
                "coordinates": [[19.0, 47.0], [19.001, 47.0]],
            },
            source="Road Authority",
            rule_version="2026-09-02",
            lanes_forward=2,
            lanes_backward=1,
            speed_limit_kph=30,
            capacity_vph=1_200,
            signal_controller_id="signal-1",
            permitted_vehicle_classes=[VehicleClass.CAR, VehicleClass.EMERGENCY],
        )
    ]

    conflated, report = NetworkConflator().conflate(graph, inventory)

    assert conflated[1][2][0]["authoritative_stable_id"] == "authority-road-1"
    assert conflated[1][2][0]["traffic_rule_version"] == "2026-09-02"
    assert conflated[1][2][0]["lanes"] == 2
    assert conflated[2][1][0]["lanes"] == 1
    assert conflated[1][2][0]["access"] == "no"
    assert conflated[1][2][0]["emergency"] == "yes"
    assert report.authoritative_network_coverage_percent == 100
    assert report.lane_coverage_percent == 100
    assert report.signal_coverage_percent == 100
    assert not report.unmatched_stable_ids


def test_directional_lanes_follow_inventory_geometry_not_node_id_order():
    graph = nx.MultiDiGraph()
    graph.add_node(10, x=19.0, y=47.0)
    graph.add_node(2, x=19.001, y=47.0)
    graph.add_edge(10, 2, key=0, osmid=10, length=76)
    graph.add_edge(2, 10, key=0, osmid=10, length=76)
    inventory = [
        AuthoritativeRoadSegment(
            stable_id="directed-road",
            geometry={
                "type": "LineString",
                "coordinates": [[19.0, 47.0], [19.001, 47.0]],
            },
            source="Road Authority",
            rule_version="1",
            lanes_forward=3,
            lanes_backward=1,
        )
    ]

    conflated, _report = NetworkConflator().conflate(graph, inventory)

    assert conflated[10][2][0]["lanes"] == 3
    assert conflated[2][10][0]["lanes"] == 1
