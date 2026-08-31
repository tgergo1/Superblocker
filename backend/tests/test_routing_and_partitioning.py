import networkx as nx
import pytest
from shapely.geometry import LineString, Polygon

from app.models.schemas import (
    AccessTarget,
    AdministrativeBoundary,
    AnalysisEvidence,
    BoundingBox,
    CityPartition,
    Coordinates,
    EnforcedSuperblock,
    EntryPoint,
    ModificationType,
    RouteRequest,
    StreetModification,
    TrafficObservation,
)
from app.services.partitioning.city_partitioner import (
    CityPartitioner,
    SuperblockCell,
    assess_plan_readiness,
)
from app.services.routing.superblock_router import SuperblockRouter
from app.services.traffic import apply_traffic_observations_to_graph


def partition_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    coordinates = {
        1: (0.0, 0.0),
        2: (0.002, 0.0),
        3: (0.002, 0.002),
        4: (0.0, 0.002),
        5: (0.001, 0.0),
        6: (0.001, 0.001),
        7: (0.001, 0.002),
    }
    for node, (x, y) in coordinates.items():
        graph.add_node(node, x=x, y=y)
    edges = [
        (1, 2, 101, "primary"),
        (2, 3, 102, "primary"),
        (3, 4, 103, "primary"),
        (4, 1, 104, "primary"),
        (5, 6, 201, "service"),
        (6, 7, 202, "service"),
    ]
    for u, v, osmid, highway in edges:
        for source, target in ((u, v), (v, u)):
            graph.add_edge(
                source,
                target,
                key=0,
                osmid=osmid,
                highway=highway,
                length=50.0,
                geometry=LineString([coordinates[source], coordinates[target]]),
            )
    return graph


def test_partition_respects_arterial_types_and_enforcement_toggle():
    bbox = BoundingBox(north=0.002, south=0, east=0.002, west=0)
    unenforced = CityPartitioner(
        partition_graph(),
        bbox,
        target_size_ha=5,
        min_area_ha=0.5,
        max_area_ha=20,
        arterial_road_types={"primary"},
        enforce_constraints=False,
    ).partition()
    assert unenforced.total_superblocks == 1
    assert unenforced.superblocks[0].modifications == []
    assert unenforced.superblocks[0].constraint_validated is False
    assert set(unenforced.arterial_network) == {101, 102, 103, 104}
    assert any(entry.boundary_road_id > 0 for entry in unenforced.superblocks[0].entry_points)

    enforced = CityPartitioner(
        partition_graph(),
        bbox,
        target_size_ha=5,
        min_area_ha=0.5,
        max_area_ha=20,
        arterial_road_types={"primary"},
        enforce_constraints=True,
    ).partition()
    assert enforced.superblocks[0].constraint_validated is True
    assert enforced.total_street_cuts > 0
    assert len(enforced.plan_id) == 64
    assert 0 <= enforced.coverage_percent <= 100
    assert enforced.readiness.status == "model_only"
    assert enforced.readiness.implementation_ready is False
    assert "Transport-engineering review is pending" in enforced.readiness.blockers


def test_measured_volume_replaces_topology_for_arterial_selection():
    graph = partition_graph()
    evidence = apply_traffic_observations_to_graph(
        graph,
        [TrafficObservation(osm_id=201, volume_vph=1200, source="Measured counter")],
    )
    bbox = BoundingBox(north=0.002, south=0, east=0.002, west=0)
    partitioner = CityPartitioner(graph, bbox, traffic_evidence=evidence)
    partitioner._prepare_network()
    partitioner._identify_arterials()

    selected_osmids = {graph[u][v][key]["osmid"] for u, v, key in partitioner.arterial_edges}
    assert selected_osmids == {201}
    assert evidence["traffic_mode"] == "measured_volume"


def test_sparse_measured_traffic_remains_blocked_from_implementation():
    graph = partition_graph()
    evidence = apply_traffic_observations_to_graph(
        graph,
        [TrafficObservation(osm_id=101, volume_vph=1000, source="One counter")],
    )
    bbox = BoundingBox(north=0.002, south=0, east=0.002, west=0)
    result = CityPartitioner(graph, bbox, traffic_evidence=evidence).partition()

    assert result.evidence.traffic_mode == "measured_volume"
    assert any("Measured traffic coverage" in blocker for blocker in result.readiness.blockers)


def test_complete_evidence_and_both_post_analysis_reviews_pass_release_gate():
    readiness = assess_plan_readiness(
        evidence=AnalysisEvidence(
            boundary_mode="administrative_polygon",
            traffic_mode="measured_volume",
            traffic_observation_count=100,
            measured_edge_coverage_percent=90,
            access_mode="authoritative_targets",
            access_target_count=25,
            access_dataset_source="Municipal cadastre 2026",
            access_dataset_complete=True,
        ),
        has_boundary=True,
        modeled_directional_validation_passed=True,
        validated_target_count=25,
        total_unreachable_targets=0,
        review_types={"transport_engineering", "site_inspection"},
    )

    assert readiness.status == "implementation_ready"
    assert readiness.implementation_ready is True
    assert readiness.blockers == []


def test_access_validation_uses_explicit_targets_not_every_graph_node():
    graph = partition_graph()
    bbox = BoundingBox(north=0.002, south=0, east=0.002, west=0)
    target = AccessTarget(
        id="parcel-1",
        coordinates=Coordinates(lat=0.001, lon=0.001),
        kind="parcel",
        source="Municipal cadastre 2026",
    )
    partitioner = CityPartitioner(graph, bbox, access_targets=[target])
    interior = graph.subgraph([5, 6, 7]).copy()

    assert partitioner._find_unreachable_access_targets(interior, [], [5, 7], None, [target]) == []
    blocked = partitioner._find_unreachable_access_targets(interior, [], [], None, [target])
    assert [item.target_id for item in blocked] == ["parcel-1"]


def test_partition_coverage_uses_exact_administrative_polygon():
    graph = partition_graph()
    bbox = BoundingBox(north=0.002, south=0, east=0.002, west=0)
    boundary = AdministrativeBoundary(
        type="Polygon",
        coordinates=[[[0, 0], [0.002, 0], [0.002, 0.0015], [0, 0.0015], [0, 0]]],
    )
    result = CityPartitioner(
        graph,
        bbox,
        boundary=boundary,
        target_size_ha=5,
        min_area_ha=0.5,
        max_area_ha=20,
        arterial_road_types={"primary"},
    ).partition()

    assert result.boundary == boundary
    assert result.evidence.boundary_mode == "administrative_polygon"
    assert 0 <= result.coverage_percent <= 100


def test_small_cell_merge_does_not_duplicate_an_earlier_neighbor():
    bbox = BoundingBox(north=0.002, south=0, east=0.002, west=0)
    partitioner = CityPartitioner(
        nx.MultiDiGraph(),
        bbox,
        target_size_ha=3,
        min_area_ha=2,
        max_area_ha=5,
    )
    partitioner.cells = [
        SuperblockCell(
            polygon=Polygon([(0, 0), (0.001, 0), (0.001, 0.002), (0, 0.002)]),
            area_hectares=2,
            boundary_edges=[],
            interior_edges=[],
            entry_nodes=[],
        ),
        SuperblockCell(
            polygon=Polygon([(0.001, 0), (0.002, 0), (0.002, 0.002), (0.001, 0.002)]),
            area_hectares=1,
            boundary_edges=[],
            interior_edges=[],
            entry_nodes=[],
        ),
    ]

    assert partitioner._merge_small_cells() is True
    assert len(partitioner.cells) == 1
    assert partitioner.cells[0].polygon.area == pytest.approx(0.000004)


def routing_fixture(block_exit: bool = False, arterial_link: bool = True):
    graph = nx.MultiDiGraph()
    coordinates = {
        1: (-1.0, 0.0),
        2: (-1.0, 1.0),
        3: (0.0, 1.0),
        4: (1.0, 1.0),
        5: (1.0, 0.0),
    }
    for node, (x, y) in coordinates.items():
        graph.add_node(node, x=x, y=y)

    def add_both(u: int, v: int, osmid: int, highway: str, length: float):
        graph.add_edge(u, v, key=0, osmid=osmid, highway=highway, length=length)
        graph.add_edge(v, u, key=0, osmid=osmid, highway=highway, length=length)

    add_both(1, 2, 10, "residential", 100)
    add_both(2, 3, 100, "primary" if arterial_link else "residential", 100)
    add_both(3, 4, 100, "primary" if arterial_link else "residential", 100)
    add_both(4, 5, 20, "residential", 100)
    add_both(1, 5, 30, "residential", 20)

    modification = []
    if block_exit:
        modification = [
            StreetModification(
                u=1,
                v=2,
                key=0,
                osm_id=10,
                modification_type=ModificationType.FULL_CLOSURE,
                rationale="test closure",
            )
        ]

    def superblock(
        identifier: str, left: float, right: float, interior: int, entry: int, mods=None
    ):
        return EnforcedSuperblock(
            id=identifier,
            geometry={
                "type": "Polygon",
                "coordinates": [
                    [
                        (left, -0.5),
                        (right, -0.5),
                        (right, 1.1),
                        (left, 1.1),
                        (left, -0.5),
                    ]
                ],
            },
            area_hectares=1,
            num_sectors=4,
            boundary_roads=[100],
            entry_points=[
                EntryPoint(
                    node_id=entry,
                    sector=1,
                    coordinates=Coordinates(lat=coordinates[entry][1], lon=coordinates[entry][0]),
                    boundary_road_id=100,
                )
            ],
            modifications=mods or [],
            constraint_validated=True,
            all_addresses_reachable=True,
            interior_roads_count=1,
            modal_filter_count=0,
            one_way_conversion_count=0,
            street_cut_count=len(mods or []),
        )

    partition = CityPartition(
        superblocks=[
            superblock("left", -1.5, -0.5, 1, 2, modification),
            superblock("right", 0.5, 1.5, 5, 4),
        ],
        arterial_network=[100] if arterial_link else [],
        bbox=BoundingBox(north=1.5, south=-1, east=2, west=-2),
        total_area_hectares=2,
        coverage_percent=50,
        total_superblocks=2,
        total_modal_filters=0,
        total_one_way_conversions=0,
        total_street_cuts=len(modification),
        total_unreachable_addresses=0,
    )
    request = RouteRequest(
        origin=Coordinates(lat=0, lon=-1),
        destination=Coordinates(lat=0, lon=1),
    )
    return graph, partition, request


def test_unconstrained_route_uses_original_graph_even_when_exit_is_closed():
    graph, partition, request = routing_fixture(block_exit=True)
    request.respect_superblocks = False
    result = SuperblockRouter(graph, partition).route(request)
    assert result.success is True
    assert result.total_distance_km == 0.02


def test_constrained_route_is_strictly_arterial_between_cells():
    graph, partition, request = routing_fixture()
    result = SuperblockRouter(graph, partition).route(request)
    assert result.success is True
    assert result.arterial_percent > 0
    assert result.superblocks_traversed == ["left", "right"]

    graph, partition, request = routing_fixture(arterial_link=False)
    result = SuperblockRouter(graph, partition).route(request)
    assert result.success is False
    assert "arterial route" in (result.blocked_reason or "")


def test_parallel_edge_reporting_uses_shortest_edge():
    graph, partition, _request = routing_fixture()
    graph.add_edge(1, 5, key=8, osmid=31, highway="residential", length=5)
    router = SuperblockRouter(graph, partition)
    segments = router._path_to_segments([1, 5], graph=graph)
    assert segments[0].length_m == 5
