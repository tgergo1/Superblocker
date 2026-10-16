import hashlib

import pytest

from app.models.schemas import DatasetAuthority, DatasetManifest
from app.services.registry.store import RegistryStore
from tests.test_api_routes import empty_partition


def registry(tmp_path) -> RegistryStore:
    return RegistryStore(tmp_path / "registry.sqlite3", tmp_path / "objects")


def manifest(payload: bytes, *, complete: bool = True) -> DatasetManifest:
    return DatasetManifest(
        dataset_id="traffic-2026-09-02-v1",
        name="Traffic snapshot",
        category="traffic",
        source_organization="Test Authority",
        authority=DatasetAuthority.ROAD_AUTHORITY,
        version="1",
        captured_at="2026-09-02T00:00:00Z",
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        complete=complete,
        quality_score=1,
    )


def test_registry_verifies_immutable_dataset_content(tmp_path):
    store = registry(tmp_path)
    payload = b"verified snapshot"

    record = store.register_dataset(manifest(payload), payload, actor_id="test-admin")

    assert record["checksum_verified"]
    assert record["content_available"]
    assert store.get_dataset("traffic-2026-09-02-v1") == record
    assert store.verify_audit_chain()["valid"]


def test_registry_rejects_complete_metadata_without_snapshot_and_bad_checksum(tmp_path):
    store = registry(tmp_path)
    with pytest.raises(ValueError, match="must include immutable content"):
        store.register_dataset(manifest(b"expected"), None, actor_id="test-admin")
    with pytest.raises(ValueError, match="checksum mismatch"):
        store.register_dataset(manifest(b"expected"), b"different", actor_id="test-admin")


def test_registry_persists_plans_and_detects_audit_tampering(tmp_path):
    store = registry(tmp_path)
    partition = empty_partition()
    partition.plan_id = "a" * 64
    store.register_plan(partition, actor_id="analysis-worker")

    assert store.get_plan(partition.plan_id)["plan_id"] == partition.plan_id
    assert store.list_plans()[0]["plan_id"] == partition.plan_id
    assert store.verify_audit_chain()["valid"]

    with store._connect() as connection:
        connection.execute("UPDATE audit_log SET action = 'tampered' WHERE sequence = 1")
    assert not store.verify_audit_chain()["valid"]


def test_durable_job_lifecycle_and_cancellation(tmp_path):
    store = registry(tmp_path)
    job_id = store.create_job("partition", {"bbox": {"north": 1}}, actor_id="user")
    claimed = store.claim_next_job("partition")
    assert claimed["job_id"] == job_id
    assert claimed["attempts"] == 1

    store.update_job_progress(job_id, {"percent": 50})
    store.complete_job(job_id, {"plan_id": "a" * 64}, actor_id="worker")
    completed = store.get_job(job_id, include_result=True)
    assert completed["status"] == "completed"
    assert completed["progress"] == {"percent": 50}
    assert completed["result"]["plan_id"] == "a" * 64

    cancel_id = store.create_job("partition", {}, actor_id="user")
    assert store.request_job_cancel(cancel_id, actor_id="user")
    assert store.get_job(cancel_id)["status"] == "cancelled"
    assert store.claim_next_job("partition") is None
