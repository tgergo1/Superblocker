import pytest
from pydantic import ValidationError

from app.models.schemas import BoundingBox, PartitionRequest
from app.services.partitioning.city_partitioner import CityPartitioner
from tests.test_boundary_impact import cell_graph

BBOX = BoundingBox(north=47.52, south=47.49, east=19.08, west=19.04)


def test_superblock_mode_keeps_neighbourhood_sizes():
    with pytest.raises(ValidationError, match="circulation_sectors"):
        PartitionRequest(
            bbox=BBOX, target_size_hectares=150, min_area_hectares=30, max_area_hectares=400
        )


def test_circulation_sector_mode_allows_district_sized_cells():
    request = PartitionRequest(
        bbox=BBOX,
        planning_mode="circulation_sectors",
        target_size_hectares=150,
        min_area_hectares=30,
        max_area_hectares=400,
    )
    assert request.planning_mode == "circulation_sectors"


def test_sector_mode_bounds_cells_by_main_roads_only():
    graph = cell_graph()
    partitioner = CityPartitioner(
        graph,
        BoundingBox(north=0.003, south=-0.001, east=0.003, west=-0.001),
        arterial_road_types={"primary"},
        use_centrality_boundaries=False,
    )
    partitioner._identify_arterials()
    highways = {graph.edges[edge]["highway"] for edge in partitioner.arterial_edges}
    assert highways == {"primary"}


def test_split_candidates_keep_main_roads_nearest_the_centre(monkeypatch):
    import networkx as nx
    from shapely.geometry import box

    from app.services.partitioning import city_partitioner as module

    graph = nx.MultiDiGraph()
    edges = []
    for index in range(10):
        x = index * 0.001
        graph.add_node(2 * index, x=x, y=0.0)
        graph.add_node(2 * index + 1, x=x, y=0.001)
        highway = "tertiary" if index != 4 else "residential"
        graph.add_edge(2 * index, 2 * index + 1, 0, highway=highway, length=111.0)
        edges.append((2 * index, 2 * index + 1, 0))
    partitioner = CityPartitioner(
        graph, BoundingBox(north=0.002, south=-0.001, east=0.01, west=-0.001)
    )
    cell = module.SuperblockCell(
        polygon=box(0.0, 0.0, 0.009, 0.001),
        area_hectares=100.0,
        boundary_edges=[],
        interior_edges=edges,
        entry_nodes=[],
    )
    monkeypatch.setattr(module, "MAX_SPLIT_CANDIDATES", 3)

    candidates = partitioner._split_candidates(cell)

    # The residential street at x=0.004 is skipped; the next nearest the
    # centre (x=0.0045) are the tertiary streets at x=0.005, 0.003 and 0.006.
    assert {u // 2 for u, _v, _key in candidates} == {3, 5, 6}


def _residential_cell(width_deg: float, use_centrality_boundaries: bool = True):
    """A cell crossed only by north-south residential streets every 0.002 degrees."""
    import networkx as nx
    from shapely.geometry import box

    from app.services.partitioning import city_partitioner as module

    graph = nx.MultiDiGraph()
    edges = []
    steps = round(width_deg / 0.002)
    for index in range(1, steps):
        x = index * 0.002
        graph.add_node(2 * index, x=x, y=0.0)
        graph.add_node(2 * index + 1, x=x, y=0.01)
        graph.add_edge(2 * index, 2 * index + 1, 0, highway="residential", length=1100.0)
        edges.append((2 * index, 2 * index + 1, 0))
    partitioner = CityPartitioner(
        graph,
        BoundingBox(north=0.011, south=-0.001, east=width_deg + 0.001, west=-0.001),
        min_area_ha=6,
        max_area_ha=20,
        use_centrality_boundaries=use_centrality_boundaries,
    )
    polygon = box(0.0, 0.0, width_deg, 0.01)
    cell = module.SuperblockCell(
        polygon=polygon,
        area_hectares=partitioner._calculate_area_hectares(polygon),
        boundary_edges=[],
        interior_edges=edges,
        entry_nodes=[],
    )
    return partitioner, cell


def test_oversized_cell_without_main_roads_splits_along_residential_streets():
    partitioner, cell = _residential_cell(0.02)  # about 245 ha, over 5 x 20 ha
    assert cell.area_hectares > 100
    pieces = partitioner._split_cell(cell)
    assert len(pieces) == 2
    assert sum(piece.area_hectares for piece in pieces) == pytest.approx(
        cell.area_hectares, rel=0.01
    )
    # The middle street gives the most even split.
    assert abs(pieces[0].area_hectares - pieces[1].area_hectares) < 1.0


def test_circulation_sectors_never_split_on_residential_streets():
    partitioner, cell = _residential_cell(0.02, use_centrality_boundaries=False)
    assert partitioner._split_cell(cell) == [cell]


def test_cell_just_over_the_maximum_keeps_its_residential_streets_inside():
    partitioner, cell = _residential_cell(0.006)  # about 73 ha, under 5 x 20 ha
    assert 20 < cell.area_hectares < 100
    assert partitioner._split_cell(cell) == [cell]


def test_split_line_through_a_concave_cell_keeps_the_piece_holding_the_road():
    from shapely.geometry import LineString, Polygon

    partitioner, _cell = _residential_cell(0.02)
    # A U shape: the horizontal road in the left arm, extended, would also cross
    # the right arm.
    u_shape = Polygon([(0, 0), (3, 0), (3, 3), (2, 3), (2, 1), (1, 1), (1, 3), (0, 3)])
    piece = partitioner._extend_line_to_boundary(LineString([(0.4, 2), (0.6, 2)]), u_shape)
    assert piece is not None
    assert piece.bounds == pytest.approx((0, 2, 1, 2))
