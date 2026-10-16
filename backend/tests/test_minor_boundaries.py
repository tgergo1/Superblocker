from app.models.schemas import BoundingBox, PartitionRequest
from app.services.partitioning.city_partitioner import CityPartitioner
from tests.test_boundary_impact import cell_graph

BBOX = BoundingBox(north=0.003, south=-0.001, east=0.003, west=-0.001)


def test_avoid_policy_keeps_residential_streets_off_the_centrality_boundary():
    graph = cell_graph()
    avoid = CityPartitioner(graph, BBOX, minor_boundary_policy="avoid")
    allow = CityPartitioner(graph, BBOX, minor_boundary_policy="allow")
    residential = (5, 6, 0)
    primary = (1, 5, 0)
    assert not avoid._may_bound_by_centrality(residential)
    assert avoid._may_bound_by_centrality(primary)
    assert allow._may_bound_by_centrality(residential)


def test_keeping_residential_streets_inside_is_the_default():
    assert CityPartitioner(cell_graph(), BBOX).minor_boundary_policy == "avoid"
    assert PartitionRequest(bbox=BBOX).minor_road_boundaries == "avoid"
