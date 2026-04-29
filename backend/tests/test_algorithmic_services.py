import networkx as nx
from shapely.geometry import Polygon

from app.models.schemas import (
    ConstraintViolation,
    Coordinates,
    EntryPoint,
    EnforcedSuperblock,
    StreetNetworkResponse,
)
from app.services.constraint.accessibility_validator import AccessibilityValidator
from app.services.constraint.constraint_enforcer import ConstraintEnforcer
from app.services.detection.superblock_analyzer import SuperblockAnalyzer
from app.services.sizing.size_optimizer import SizeOptimizer
from app.services.traffic import apply_real_traffic_data, estimate_traffic


def test_estimate_traffic_normalizes_lane_values():
    network = StreetNetworkResponse(
        features=[
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]},
                "properties": {
                    "osmid": 1,
                    "highway": "primary",
                    "lanes": "2;1",
                },
            }
        ],
        metadata={},
    )

    estimated = estimate_traffic(network)
    props = estimated.features[0]["properties"]

    assert props["lanes"] == 2
    assert props["capacity"] == 1600
    assert props["estimated_volume"] == 960
    assert estimated.metadata["total_capacity"] == 1600
    assert estimated.metadata["total_estimated_volume"] == 960


def test_apply_real_traffic_data_recomputes_metadata():
    network = StreetNetworkResponse(
        features=[
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]},
                "properties": {
                    "osmid": 1,
                    "highway": "secondary",
                    "lanes": 1,
                    "capacity": 600,
                    "estimated_volume": 300,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[1, 0], [2, 0]]},
                "properties": {
                    "osmid": 2,
                    "highway": "residential",
                    "lanes": 1,
                    "capacity": 200,
                    "estimated_volume": 60,
                },
            },
        ],
        metadata={},
    )

    updated = apply_real_traffic_data(network, {1: 420})

    assert updated.features[0]["properties"]["estimated_volume"] == 420
    assert updated.features[0]["properties"]["is_real_data"] is True
    assert updated.features[1]["properties"]["is_real_data"] is False
    assert updated.metadata["total_capacity"] == 800
    assert updated.metadata["total_estimated_volume"] == 480
    assert updated.metadata["average_load"] == 0.6


def test_constraint_enforcer_assigns_wraparound_sector_consistently():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=1.0, y=0.0)
    graph.add_node(2, x=-1.0, y=0.001)
    graph.add_node(3, x=-1.0, y=-0.001)

    enforcer = ConstraintEnforcer(
        interior_graph=graph,
        boundary_polygon=Polygon([(-2, -2), (2, -2), (2, 2), (-2, 2)]),
        entry_node_ids=[1, 2, 3],
        num_sectors=4,
    )

    sectors = enforcer._assign_sectors()

    assert sectors.node_to_sector[1] == 0
    assert sectors.node_to_sector[2] == sectors.node_to_sector[3] == 2


def test_constraint_enforcer_greedy_repair_removes_remaining_violation():
    graph = nx.MultiDiGraph()
    for node_id, x in [(1, -2.0), (2, -1.0), (3, 0.0), (4, 1.0), (5, 2.0)]:
        graph.add_node(node_id, x=x, y=0.0)

    for u, v in [(1, 2), (2, 1), (2, 3), (3, 2), (3, 4), (4, 3), (4, 5), (5, 4)]:
        graph.add_edge(u, v, key=0, osmid=100 + u + v, highway="service", length=30.0)

    enforcer = ConstraintEnforcer(
        interior_graph=graph,
        boundary_polygon=Polygon([(-3, -1), (3, -1), (3, 1), (-3, 1)]),
        entry_node_ids=[1, 5],
        num_sectors=4,
    )
    enforcer.sectors = enforcer._assign_sectors()

    violation = ConstraintViolation(
        from_entry=EntryPoint(
            node_id=1,
            sector=2,
            coordinates=Coordinates(lat=0.0, lon=-2.0),
            boundary_road_id=0,
        ),
        to_entry=EntryPoint(
            node_id=5,
            sector=0,
            coordinates=Coordinates(lat=0.0, lon=2.0),
            boundary_road_id=0,
        ),
        path_exists=True,
        path_edges=[(1, 2), (2, 3), (3, 4), (4, 5)],
    )

    modifications, remaining = enforcer._repair_remaining_violations([], [violation])

    assert remaining == []
    assert any(mod.u == 2 and mod.v == 3 for mod in modifications)


def test_accessibility_validator_counts_entry_nodes_as_reachable():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=0.0, y=0.0)
    graph.add_node(2, x=1.0, y=0.0)
    graph.add_edge(1, 2, key=0, osmid=12, highway="residential", length=10.0)

    superblock = EnforcedSuperblock(
        id="sb1",
        geometry={"type": "Polygon", "coordinates": [[(0, 0), (2, 0), (2, 1), (0, 1), (0, 0)]]},
        area_hectares=1.0,
        num_sectors=4,
        boundary_roads=[],
        entry_points=[
            EntryPoint(
                node_id=1,
                sector=0,
                coordinates=Coordinates(lat=0.0, lon=0.0),
                boundary_road_id=0,
            )
        ],
        modifications=[],
        constraint_validated=True,
        all_addresses_reachable=True,
        unreachable_addresses=[],
        interior_roads_count=1,
        modal_filter_count=0,
        one_way_conversion_count=0,
        street_cut_count=0,
    )

    validator = AccessibilityValidator(graph, superblock)
    reachable = validator._find_reachable_nodes({1})

    assert 1 in reachable
    assert 2 in reachable


def test_size_optimizer_estimates_block_size_from_polygonized_blocks():
    graph = nx.MultiDiGraph()
    coords = {
        1: (0.0, 0.0),
        2: (0.0, 0.001),
        3: (0.001, 0.001),
        4: (0.001, 0.0),
    }

    for node_id, (x, y) in coords.items():
        graph.add_node(node_id, x=x, y=y)

    for u, v in [(1, 2), (2, 3), (3, 4), (4, 1)]:
        graph.add_edge(u, v, key=0, highway="residential", length=111.0)
        graph.add_edge(v, u, key=0, highway="residential", length=111.0)

    optimizer = SizeOptimizer(graph=graph)
    analysis = optimizer._analyze_grid()

    assert 90 <= analysis.average_block_size_m <= 130


def test_superblock_analyzer_estimates_graph_based_traffic_impact():
    graph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, length=100.0)
    graph.add_edge(2, 3, key=0, length=100.0)
    graph.add_edge(1, 4, key=0, length=250.0)
    graph.add_edge(4, 3, key=0, length=250.0)

    analyzer = SuperblockAnalyzer()
    impact = analyzer._estimate_traffic_impact(
        G=graph,
        interior_edges=[
            {"u": 1, "v": 2, "key": 0, "length_m": 100.0, "estimated_volume": 100},
            {"u": 2, "v": 3, "key": 0, "length_m": 100.0, "estimated_volume": 100},
        ],
        boundary_capacity=1000,
        access_nodes={1, 3},
    )

    assert impact is not None
    assert impact.removed_through_traffic_pct == 100.0
    assert impact.boundary_load_increase_pct == 10.0
    assert impact.estimated_vmt_reduction == 20.0
    assert impact.affected_od_pairs == 1
