import asyncio

import networkx as nx
import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, Polygon

from app.api.routes.analysis import _sse_data
from app.models.schemas import (
    AdministrativeBoundary,
    AnalysisRequest,
    BoundingBox,
    PartitionRequest,
    StreetNetworkRequest,
    TrafficObservation,
)
from app.services.osm_service import (
    get_street_network_graph,
    graph_to_street_network,
    normalize_highway_type,
    normalize_lanes,
    normalize_maxspeed,
)
from app.services.traffic import estimate_traffic
from app.utils.geo import (
    bbox_area_hectares,
    buffer_point,
    create_bbox_polygon,
    haversine_distance,
    lines_to_polygons,
    polygon_area_hectares,
    simplify_geometry,
    validate_bbox_size,
)


def make_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node(1, x=19.0, y=47.0)
    graph.add_node(2, x=19.001, y=47.0)
    geometry = LineString([(19.0, 47.0), (19.001, 47.0)])
    properties = {
        "osmid": 10,
        "highway": ["residential", "primary"],
        "lanes": "2;1",
        "maxspeed": "30 mph",
        "length": 100.0,
        "geometry": geometry,
    }
    graph.add_edge(1, 2, key=4, **properties)
    graph.add_edge(2, 1, key=7, **properties)
    return graph


def test_schema_rejects_inverted_bounds_and_ranges():
    with pytest.raises(ValidationError):
        BoundingBox(north=0, south=1, east=1, west=0)

    bbox = BoundingBox(north=1, south=0, east=1, west=0)
    with pytest.raises(ValidationError):
        AnalysisRequest(bbox=bbox, min_area_hectares=25, max_area_hectares=4)
    with pytest.raises(ValidationError):
        PartitionRequest(
            bbox=bbox,
            target_size_hectares=50,
            min_area_hectares=6,
            max_area_hectares=20,
        )
    with pytest.raises(ValidationError):
        PartitionRequest(bbox=bbox, enforce_constraints=False)
    with pytest.raises(ValidationError):
        AdministrativeBoundary(type="Polygon", coordinates=[])
    with pytest.raises(ValidationError):
        PartitionRequest(bbox=bbox, access_dataset_complete=True)
    with pytest.raises(ValidationError, match="unique OSM way IDs"):
        PartitionRequest(
            bbox=bbox,
            traffic_observations=[
                TrafficObservation(osm_id=1, volume_vph=100, source="a"),
                TrafficObservation(osm_id=1, volume_vph=200, source="b"),
            ],
        )
    with pytest.raises(ValidationError, match="contained"):
        PartitionRequest(
            bbox=bbox,
            boundary=AdministrativeBoundary(
                type="Polygon",
                coordinates=[[[0, 0], [2, 0], [2, 1], [0, 0]]],
            ),
        )


def test_schema_rejects_unknown_algorithm_and_network_type():
    bbox = BoundingBox(north=1, south=0, east=1, west=0)
    with pytest.raises(ValidationError):
        AnalysisRequest(bbox=bbox, algorithms=["not-implemented"])
    with pytest.raises(ValidationError):
        StreetNetworkRequest(bbox=bbox, network_type="flying")


def test_bbox_size_has_physical_area_limit():
    with pytest.raises(ValueError, match="area exceeds"):
        validate_bbox_size(
            85.4,
            85.0,
            1.0,
            0.0,
            max_span_degrees=2,
            max_area_km2=100,
        )


def test_osm_tag_normalizers_handle_lists_semicolons_and_mph():
    assert normalize_highway_type(["service", "primary"]) == "primary"
    assert normalize_lanes("2;1") == 2
    assert normalize_lanes(["1", "3"]) == 3
    assert normalize_maxspeed("30 mph") == 48
    assert normalize_maxspeed("signals") is None


def test_graph_conversion_preserves_edge_keys_and_deduplicates_physical_metrics():
    bbox = BoundingBox(north=47.01, south=46.99, east=19.01, west=18.99)
    network = graph_to_street_network(make_graph(), bbox)

    assert len(network.features) == 2
    assert {feature["properties"]["key"] for feature in network.features} == {4, 7}
    assert all(feature["properties"]["highway"] == "primary" for feature in network.features)
    assert all(feature["properties"]["maxspeed"] == 48 for feature in network.features)
    assert network.metadata["total_edges"] == 1
    assert network.metadata["total_directed_edges"] == 2
    assert network.metadata["total_length_km"] == 0.1

    estimated = estimate_traffic(network)
    assert estimated.metadata["total_capacity"] == 1600
    assert estimated.metadata["total_estimated_volume"] == 960


def test_exact_boundary_uses_polygon_graph_download(monkeypatch):
    graph = make_graph()
    captured = {}

    def fake_graph_from_polygon(polygon, **kwargs):
        captured["polygon"] = polygon
        captured.update(kwargs)
        return graph

    monkeypatch.setattr("app.services.osm_service.ox.graph_from_polygon", fake_graph_from_polygon)
    boundary = AdministrativeBoundary(
        type="Polygon",
        coordinates=[[[18.99, 46.99], [19.01, 46.99], [19.0, 47.01], [18.99, 46.99]]],
    )
    bbox = BoundingBox(north=47.01, south=46.99, east=19.01, west=18.99)

    result = asyncio.run(get_street_network_graph(bbox, boundary=boundary))

    assert result is graph
    assert captured["polygon"].equals(Polygon(boundary.coordinates[0]))
    assert captured["network_type"] == "drive"


def test_graph_conversion_and_sse_sanitize_non_finite_osm_values():
    graph = make_graph()
    for _u, _v, _key, data in graph.edges(keys=True, data=True):
        data.update(
            name=float("nan"),
            highway=float("nan"),
            length=float("nan"),
            oneway=float("nan"),
        )

    bbox = BoundingBox(north=47.01, south=46.99, east=19.01, west=18.99)
    network = graph_to_street_network(graph, bbox)

    for feature in network.features:
        properties = feature["properties"]
        assert properties["name"] is None
        assert properties["highway"] == "unclassified"
        assert properties["hierarchy"] == 8
        assert properties["length_m"] == 0
        assert properties["oneway"] is False

    event = _sse_data({"name": float("nan"), "values": [float("inf"), 1.0]})
    assert "NaN" not in event
    assert "Infinity" not in event
    assert '"name": null' in event
    assert '"values": [null, 1.0]' in event


def test_geographic_helpers_return_physical_shapes_and_distances():
    assert 110_000 < haversine_distance(0, 0, 1, 0) < 112_000
    assert bbox_area_hectares(0.01, 0, 0.01, 0) > 100
    polygon = create_bbox_polygon(0.01, 0, 0.01, 0)
    assert polygon_area_hectares(polygon) > 100
    assert not buffer_point(47, 19, 100).is_empty
    assert simplify_geometry(polygon, 0.001).is_valid

    square_lines = [
        LineString([(0, 0), (1, 0)]),
        LineString([(1, 0), (1, 1)]),
        LineString([(1, 1), (0, 1)]),
        LineString([(0, 1), (0, 0)]),
    ]
    polygons = lines_to_polygons(square_lines)
    assert len(polygons) == 1
    assert polygons[0].equals(Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]))
