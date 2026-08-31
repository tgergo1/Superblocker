from types import SimpleNamespace

import httpx
import networkx as nx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import analysis, cache, search
from app.core.workload import guard_expensive_request
from app.main import app
from app.models.schemas import (
    AdministrativeBoundary,
    BoundingBox,
    CityPartition,
    StreetNetworkResponse,
)
from app.services.partitioning.city_partitioner import compute_plan_id


async def bypass_guard():
    yield


@pytest.fixture(autouse=True)
def isolated_app_state():
    app.dependency_overrides[guard_expensive_request] = bypass_guard
    analysis.partition_store._entries.clear()
    yield
    app.dependency_overrides.clear()
    analysis.partition_store._entries.clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def network_response() -> StreetNetworkResponse:
    return StreetNetworkResponse(
        features=[
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[0.0, 0.0], [0.001, 0.0]],
                },
                "properties": {
                    "osmid": 1,
                    "highway": "residential",
                    "lanes": 1,
                    "length_m": 100,
                    "u": 1,
                    "v": 2,
                    "key": 0,
                },
            }
        ],
        metadata={"total_edges": 1},
    )


def empty_partition() -> CityPartition:
    return CityPartition(
        superblocks=[],
        arterial_network=[],
        bbox=BoundingBox(north=0.01, south=0, east=0.01, west=0),
        total_area_hectares=0,
        coverage_percent=0,
        total_superblocks=0,
        total_modal_filters=0,
        total_one_way_conversions=0,
        total_street_cuts=0,
        total_unreachable_addresses=0,
    )


def test_health_root_and_openapi_have_declared_response_models(client):
    assert client.get("/health").json() == {"status": "healthy"}
    assert client.get("/").status_code == 200
    schema = client.get("/openapi.json").json()
    analyze_schema = schema["paths"]["/api/v1/analyze"]["post"]["responses"]["200"]
    partition_schema = schema["paths"]["/api/v1/partition"]["post"]["responses"]["200"]
    assert analyze_schema["content"]["application/json"]["schema"]["$ref"].endswith(
        "AnalysisResponse"
    )
    assert partition_schema["content"]["application/json"]["schema"]["$ref"].endswith(
        "PartitionResponse"
    )


def test_network_endpoint_validates_profile_and_returns_traffic(client, monkeypatch):
    async def fake_network(*_args, **_kwargs):
        return network_response()

    monkeypatch.setattr(analysis, "get_street_network", fake_network)
    payload = {"bbox": {"north": 0.01, "south": 0, "east": 0.01, "west": 0}}
    response = client.post("/api/v1/network", json=payload)
    assert response.status_code == 200
    assert response.json()["features"][0]["properties"]["estimated_volume"] == 60

    payload["network_type"] = "invalid"
    assert client.post("/api/v1/network", json=payload).status_code == 422


def test_analyze_regular_and_streaming_endpoints(client, monkeypatch):
    def fake_analysis(_bbox, _min_area, _max_area, _road_types, progress_queue, _cancel_event):
        progress_queue.put({"type": "progress", "stage": "network", "percent": 10, "message": "ok"})
        return {"candidates": [{"id": "candidate"}], "network_stats": {"total_nodes": 2}}

    monkeypatch.setattr(analysis, "run_analysis_sync", fake_analysis)
    payload = {"bbox": {"north": 0.01, "south": 0, "east": 0.01, "west": 0}}

    response = client.post("/api/v1/analyze", json=payload)
    assert response.status_code == 200
    assert response.json()["total_found"] == 1

    response = client.post("/api/v1/analyze/stream", json=payload)
    assert response.status_code == 200
    assert '"type": "progress"' in response.text
    assert '"type": "complete"' in response.text


def test_partition_regular_stream_and_route_with_supplied_partition(client, monkeypatch):
    partition = empty_partition()
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=0.002, y=0.002)
    graph.add_node(2, x=0.008, y=0.008)
    graph.add_edge(1, 2, key=0, osmid=1, highway="primary", length=100)

    def fake_partition(
        _bbox,
        _boundary,
        _target,
        _minimum,
        _maximum,
        _sectors,
        _enforce,
        _road_types,
        _traffic_observations,
        _access_targets,
        _access_dataset_source,
        _access_dataset_complete,
        progress_queue,
        _cancel_event,
    ):
        progress_queue.put(
            {
                "type": "progress",
                "stage": "network",
                "percent": 10,
                "message": "ok",
                "current_superblock": None,
                "total_superblocks": None,
            }
        )
        return {
            "partition": partition,
            "graph": graph,
            "network": network_response(),
            "processing_time": 0.1,
        }

    monkeypatch.setattr(analysis, "run_partition_sync", fake_partition)
    payload = {"bbox": partition.bbox.model_dump()}
    response = client.post("/api/v1/partition", json=payload)
    assert response.status_code == 200
    assert response.json()["street_network"]["features"]

    response = client.post("/api/v1/partition/stream", json=payload)
    assert '"street_network"' in response.text
    assert '"type": "complete"' in response.text

    route_payload = {
        "origin": {"lat": 0.002, "lon": 0.002},
        "destination": {"lat": 0.008, "lon": 0.008},
        "respect_superblocks": False,
        "partition": partition.model_dump(),
    }
    response = client.post("/api/v1/route", json=route_payload)
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_route_rejects_points_outside_exact_boundary(client):
    partition = empty_partition()
    partition.boundary = AdministrativeBoundary(
        type="Polygon",
        coordinates=[[[0, 0], [0.005, 0], [0, 0.005], [0, 0]]],
    )
    response = client.post(
        "/api/v1/route",
        json={
            "origin": {"lat": 0.001, "lon": 0.001},
            "destination": {"lat": 0.008, "lon": 0.008},
            "partition": partition.model_dump(),
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "administrative boundary" in response.json()["blocked_reason"]


def test_reviews_are_post_analysis_and_bound_to_plan_digest(client):
    partition = empty_partition()
    partition.plan_id = compute_plan_id(partition)
    attestations = [
        {
            "plan_id": partition.plan_id,
            "review_type": review_type,
            "reviewer": "Qualified Reviewer",
            "organization": "City Transport Office",
            "reviewed_at": "2026-08-31",
            "reference": f"review-{index}",
        }
        for index, review_type in enumerate(["transport_engineering", "site_inspection"], start=1)
    ]
    response = client.post(
        "/api/v1/partition/review",
        json={"partition": partition.model_dump(), "review_attestations": attestations},
    )

    assert response.status_code == 200
    assert response.json()["readiness"]["transport_engineering_reviewed"] is True
    assert response.json()["readiness"]["site_inspection_reviewed"] is True
    assert response.json()["readiness"]["implementation_ready"] is False

    partition.coverage_percent = 99
    response = client.post(
        "/api/v1/partition/review",
        json={"partition": partition.model_dump(), "review_attestations": attestations},
    )
    assert response.status_code == 400


def test_optimal_size_rejects_invalid_inputs_and_returns_result(client, monkeypatch):
    async def fake_graph(_bbox):
        return nx.MultiDiGraph()

    monkeypatch.setattr(analysis, "get_street_network_graph", fake_graph)
    monkeypatch.setattr(
        analysis,
        "calculate_optimal_superblock_size",
        lambda **_kwargs: SimpleNamespace(
            min_side_m=300,
            max_side_m=500,
            optimal_side_m=400,
            min_area_ha=9,
            max_area_ha=25,
            optimal_area_ha=16,
            grid_orientation_deg=0,
            rationale="test",
        ),
    )
    params = {"north": 1, "south": 0, "east": 1, "west": 0, "population_density": 100}
    assert client.get("/api/v1/optimize/size", params=params).status_code == 200
    params["population_density"] = -1
    assert client.get("/api/v1/optimize/size", params=params).status_code == 422


class MemoryCache:
    enabled = True
    default_ttl = 60
    cache_dir = "secret/path"

    def __init__(self):
        self.data = None

    def get(self, *_args):
        return self.data

    def set(self, _kind, _params, data, **_kwargs):
        self.data = data
        return True

    def invalidate(self, **_kwargs):
        return 2

    def cleanup_expired(self):
        return 1

    def get_stats(self):
        return SimpleNamespace(to_dict=lambda: {"entries_count": 0})


def test_search_is_cached_bounded_and_upstream_errors_are_generic(client, monkeypatch):
    memory_cache = MemoryCache()
    monkeypatch.setattr(search, "get_cache_service", lambda: memory_cache)

    async def fake_get(_path, params):
        assert params["q"] == "Budapest"
        assert params["polygon_geojson"] == 1
        return httpx.Response(
            200,
            json=[
                {
                    "place_id": 1,
                    "osm_type": "relation",
                    "osm_id": 2,
                    "display_name": "Budapest, Hungary",
                    "lat": "47.5",
                    "lon": "19.04",
                    "boundingbox": ["47.4", "47.6", "18.9", "19.2"],
                    "geojson": {
                        "type": "Polygon",
                        "coordinates": [[[18.9, 47.4], [19.2, 47.4], [19.1, 47.6], [18.9, 47.4]]],
                    },
                    "type": "city",
                    "importance": 0.8,
                }
            ],
            request=httpx.Request("GET", "https://example.test"),
        )

    monkeypatch.setattr(search, "nominatim_get", fake_get)
    result = client.get("/api/v1/search", params={"q": "Budapest"})
    assert result.status_code == 200
    assert result.json()["results"][0]["boundary_source"] == "nominatim"
    assert result.json()["results"][0]["boundary"]["type"] == "Polygon"
    assert client.get("/api/v1/search", params={"q": "x"}).status_code == 422
    assert client.get("/api/v1/search", params={"q": "   "}).status_code == 422
    assert client.get("/api/v1/search", params={"q": "x" * 201}).status_code == 422


def test_reverse_search_uses_backend_throttle_and_cache(client, monkeypatch):
    memory_cache = MemoryCache()
    monkeypatch.setattr(search, "get_cache_service", lambda: memory_cache)
    calls = 0

    async def fake_get(path, params):
        nonlocal calls
        calls += 1
        assert path == "/reverse"
        assert params["lat"] == 47.5
        return httpx.Response(
            200,
            json={"display_name": "Budapest, Hungary"},
            request=httpx.Request("GET", "https://example.test"),
        )

    monkeypatch.setattr(search, "nominatim_get", fake_get)
    params = {"lat": 47.5, "lon": 19.04}
    assert client.get("/api/v1/search/reverse", params=params).status_code == 200
    assert (
        client.get("/api/v1/search/reverse", params=params).json()["display_name"]
        == "Budapest, Hungary"
    )
    assert calls == 1


def test_cache_maintenance_requires_key_and_stats_hide_path(client, monkeypatch):
    memory_cache = MemoryCache()
    monkeypatch.setattr(cache, "get_cache_service", lambda: memory_cache)
    monkeypatch.setattr(cache.settings, "admin_api_key", "test-secret")

    stats = client.get("/api/v1/cache/stats")
    assert stats.status_code == 200
    assert "cache_dir" not in stats.json()
    assert client.delete("/api/v1/cache").status_code == 403
    assert (
        client.delete("/api/v1/cache", headers={"X-Admin-Key": "test-secret"}).json()["cleared"]
        == 2
    )
    assert (
        client.post("/api/v1/cache/cleanup", headers={"X-Admin-Key": "test-secret"}).json()[
            "removed"
        ]
        == 1
    )
