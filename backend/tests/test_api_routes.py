import hashlib
import json
from types import SimpleNamespace

import httpx
import networkx as nx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import analysis, cache, jobs, planning, registry, search
from app.core.workload import guard_expensive_request
from app.main import app
from app.models.schemas import (
    AdministrativeBoundary,
    AssuranceEvidenceArtifact,
    AssuranceEvidenceType,
    BoundingBox,
    CalibrationSummary,
    CityPartition,
    DataQualityMetrics,
    DatasetAuthority,
    DatasetManifest,
    PartitionRequest,
    StreetNetworkResponse,
)
from app.services.assurance.approvals import compute_approval_signature
from app.services.partitioning.city_partitioner import compute_plan_id
from app.services.registry.store import RegistryStore


async def bypass_guard():
    yield


@pytest.fixture(autouse=True)
def isolated_app_state():
    original_approval_secret = analysis.settings.approval_signing_secret
    app.dependency_overrides[guard_expensive_request] = bypass_guard
    analysis.partition_store._entries.clear()
    yield
    app.dependency_overrides.clear()
    analysis.partition_store._entries.clear()
    analysis.settings.approval_signing_secret = original_approval_secret


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
        _evidence_context,
        _turn_restrictions,
        _area_exclusions,
        _operational_requirements,
        _active_mode_requirements,
        _demand_model,
        _assignment_settings,
        _authoritative_road_segments,
        progress_queue,
        _cancel_event,
        _traffic_impact_settings=None,
        _osm_context=None,
        _fetch_context=False,
        _filter_design_settings=None,
        _public_space_settings=None,
        _school_street_settings=None,
        _minor_road_boundaries="allow",
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
    secret = "test-approval-secret-that-is-long-enough-123456"
    analysis.settings.approval_signing_secret = secret
    artifact = {
        "artifact_id": "engineering-review-1",
        "plan_id": partition.plan_id,
        "approval_type": "transport_engineering",
        "reviewer_id": "reviewer-42",
        "reviewer_role": "Qualified traffic engineer",
        "organization": "City Transport Office",
        "issued_at": "2026-08-31T10:00:00Z",
        "reference": "review-1",
        "content_sha256": "a" * 64,
        "signature": "0" * 64,
    }
    artifact["signature"] = compute_approval_signature(artifact, secret)
    response = client.post(
        "/api/v1/partition/review",
        json={"partition": partition.model_dump(), "approval_artifacts": [artifact]},
    )

    assert response.status_code == 200
    assert response.json()["readiness"]["transport_engineering_reviewed"] is True
    assert response.json()["readiness"]["site_inspection_reviewed"] is False
    assert response.json()["readiness"]["implementation_ready"] is False

    partition.coverage_percent = 99
    response = client.post(
        "/api/v1/partition/review",
        json={"partition": partition.model_dump(), "approval_artifacts": [artifact]},
    )
    assert response.status_code == 400


def test_unsigned_legacy_reviews_cannot_advance_a_plan(client):
    partition = empty_partition()
    partition.plan_id = compute_plan_id(partition)
    response = client.post(
        "/api/v1/partition/review",
        json={
            "partition": partition.model_dump(),
            "review_attestations": [
                {
                    "plan_id": partition.plan_id,
                    "review_type": "transport_engineering",
                    "reviewer": "Reviewer",
                    "organization": "Office",
                    "reviewed_at": "2026-09-02",
                    "reference": "unsigned",
                }
            ],
        },
    )
    assert response.status_code == 422


def test_partition_evidence_requires_signed_payload_and_registered_datasets(client):
    partition = empty_partition()
    partition.evidence.dataset_manifests = [
        DatasetManifest(
            dataset_id="traffic-v1",
            name="Traffic",
            category="traffic",
            source_organization="Authority",
            authority=DatasetAuthority.ROAD_AUTHORITY,
            version="1",
            captured_at="2026-09-02",
            checksum_sha256="b" * 64,
            complete=True,
            quality_score=1,
        )
    ]
    partition.plan_id = compute_plan_id(partition)
    payload = CalibrationSummary(
        validated=True,
        model_version="1",
        calibration_dataset_ids=["traffic-v1"],
        holdout_dataset_ids=["traffic-v1"],
        geh_under_5_percent=90,
        stochastic_runs=5,
    ).model_dump(mode="json")
    payload_sha256 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    secret = "test-signing-secret-that-is-at-least-32-characters"
    analysis.settings.approval_signing_secret = secret
    artifact_data = {
        "artifact_id": "evidence-001",
        "plan_id": partition.plan_id,
        "evidence_type": AssuranceEvidenceType.BASELINE_CALIBRATION,
        "payload": payload,
        "payload_sha256": payload_sha256,
        "validator_id": "validator-1",
        "validator_role": "transport model validator",
        "organization": "Independent Lab",
        "issued_at": "2026-09-02",
        "model_version": "1",
        "dataset_ids": ["traffic-v1"],
        "reference": "artifact://calibration/1",
        "signature": "0" * 64,
    }
    artifact_data["signature"] = compute_approval_signature(artifact_data, secret)
    artifact = AssuranceEvidenceArtifact.model_validate(artifact_data)

    response = client.post(
        "/api/v1/partition/evidence",
        json={
            "partition": partition.model_dump(mode="json"),
            "evidence_artifacts": [artifact.model_dump(mode="json")],
        },
    )

    assert response.status_code == 200
    assert response.json()["evidence"]["baseline_calibration"]["validated"]
    assert response.json()["evidence_artifacts"][0]["artifact_id"] == "evidence-001"


def test_unsigned_quality_claims_are_ignored_and_full_manifests_are_verified(monkeypatch, tmp_path):
    payload = b"traffic snapshot"
    store = RegistryStore(tmp_path / "registry.sqlite3", tmp_path / "objects")
    manifest = DatasetManifest(
        dataset_id="traffic-v1",
        name="Traffic",
        category="traffic",
        source_organization="Authority",
        authority=DatasetAuthority.ROAD_AUTHORITY,
        version="1",
        captured_at="2026-09-02",
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        complete=True,
        quality_score=1,
    )
    store.register_dataset(manifest, payload, actor_id="test")
    monkeypatch.setattr(analysis, "get_registry_store", lambda: store)
    request = PartitionRequest(
        bbox={"north": 0.01, "south": 0, "east": 0.01, "west": 0},
        dataset_manifests=[manifest],
        data_quality=DataQualityMetrics(directional_traffic_coverage_percent=100),
    )

    analysis._validate_registered_dataset_manifests(request)
    assert (
        analysis._evidence_context(request).data_quality.directional_traffic_coverage_percent == 0
    )

    request.dataset_manifests[0] = manifest.model_copy(update={"category": "demand"})
    with pytest.raises(ValueError, match="do not match registry snapshots"):
        analysis._validate_registered_dataset_manifests(request)


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


def test_registry_dataset_upload_and_audit_verification(client, monkeypatch, tmp_path):
    store = RegistryStore(tmp_path / "registry.sqlite3", tmp_path / "objects")
    app.dependency_overrides[registry.get_registry_store] = lambda: store
    monkeypatch.setattr(cache.settings, "admin_api_key", "test-secret")
    payload = b"immutable traffic snapshot"
    manifest = DatasetManifest(
        dataset_id="traffic-api-v1",
        name="Traffic API fixture",
        category="traffic",
        source_organization="Road Authority",
        authority=DatasetAuthority.ROAD_AUTHORITY,
        version="1",
        captured_at="2026-09-02",
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        complete=True,
        quality_score=1,
    )

    response = client.post(
        "/api/v1/registry/datasets",
        data={"manifest_json": manifest.model_dump_json()},
        files={"snapshot": ("traffic.csv", payload, "text/csv")},
        headers={"X-Admin-Key": "test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["checksum_verified"]
    headers = {"X-Admin-Key": "test-secret"}
    assert client.get("/api/v1/registry/datasets", headers=headers).json()["datasets"]
    snapshot = client.get("/api/v1/registry/datasets/traffic-api-v1/snapshot", headers=headers)
    assert snapshot.content == payload
    audit = client.get(
        "/api/v1/registry/audit/verify",
        headers={"X-Admin-Key": "test-secret"},
    )
    assert audit.json()["valid"]


def test_durable_job_api_queues_reads_and_cancels(client, monkeypatch, tmp_path):
    store = RegistryStore(tmp_path / "registry.sqlite3", tmp_path / "objects")
    app.dependency_overrides[registry.get_registry_store] = lambda: store
    monkeypatch.setattr(cache.settings, "admin_api_key", "test-secret")
    monkeypatch.setattr(
        jobs, "get_partition_job_runner", lambda: SimpleNamespace(wake=lambda: None)
    )
    headers = {"X-Admin-Key": "test-secret"}

    created = client.post(
        "/api/v1/jobs/partition",
        json={"bbox": {"north": 0.01, "south": 0, "east": 0.01, "west": 0}},
        headers=headers,
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["status"] == "queued"
    assert client.delete(f"/api/v1/jobs/{job_id}", headers=headers).status_code == 202
    assert client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["status"] == "cancelled"


def test_engineering_package_api_persists_draft_artifact(client, monkeypatch, tmp_path):
    store = RegistryStore(tmp_path / "registry.sqlite3", tmp_path / "objects")
    app.dependency_overrides[registry.get_registry_store] = lambda: store
    monkeypatch.setattr(cache.settings, "admin_api_key", "test-secret")
    partition = empty_partition()
    partition.plan_id = compute_plan_id(partition)
    graph = nx.MultiDiGraph()

    async def fake_graph(*_args, **_kwargs):
        return graph

    monkeypatch.setattr(planning, "get_street_network_graph", fake_graph)
    response = client.post(
        "/api/v1/planning/engineering-package",
        json={"partition": partition.model_dump(mode="json"), "jurisdiction": "Test City"},
        headers={"X-Admin-Key": "test-secret"},
    )

    assert response.status_code == 200
    package = response.json()
    assert package["status"] == "draft_for_engineering_review"
    artifact = client.get(
        f"/api/v1/registry/artifacts/{package['package_id']}",
        headers={"X-Admin-Key": "test-secret"},
    )
    assert artifact.status_code == 200
    assert artifact.json()["artifact_type"] == "engineering_package"


def test_evidence_import_api_requires_auth_and_converts_demand(client, monkeypatch):
    monkeypatch.setattr(cache.settings, "admin_api_key", "test-secret")
    headers = {"X-Admin-Key": "test-secret"}

    def files():
        return {
            "zones": (
                "zones.csv",
                "zone_id,name,lat,lon,connector_node_ids,external\n"
                "A,Inner,47,19,1;2,false\nB,Outer,47.1,19.1,3,true\n",
                "text/csv",
            ),
            "time_slices": (
                "periods.csv",
                "time_slice_id,start_time,end_time,duration_minutes\nam,08:00,09:00,60\n",
                "text/csv",
            ),
            "matrix": (
                "matrix.csv",
                "origin_zone_id,destination_zone_id,time_slice_id,vehicle_class,trips\n"
                "A,B,am,car,12\n",
                "text/csv",
            ),
        }

    data = {"model_id": "efm", "version": "1", "source": "Model authority"}

    assert (
        client.post("/api/v1/evidence/import/demand", data=data, files=files()).status_code == 403
    )
    response = client.post(
        "/api/v1/evidence/import/demand",
        data=data,
        files=files(),
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["demand_model"]["matrix"][0]["trips"] == 12


def test_evidence_import_api_rejects_invalid_road_inventory(client, monkeypatch):
    monkeypatch.setattr(cache.settings, "admin_api_key", "test-secret")
    response = client.post(
        "/api/v1/evidence/import/roads",
        data={"source": "Authority", "rule_version": "1"},
        files={"roads_geojson": ("roads.geojson", '{"type":"Point"}', "application/geo+json")},
        headers={"X-Admin-Key": "test-secret"},
    )

    assert response.status_code == 422
    assert response.json()["detail"].startswith("Invalid evidence input:")
