"""SQLite metadata registry with content-addressed objects and hash-chained audit."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.schemas import CityPartition, DatasetManifest, ScenarioPortfolio


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


class ContentAddressedStore:
    """Immutable local object store keyed by SHA-256."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, payload: bytes, expected_sha256: str | None = None) -> tuple[str, Path]:
        digest = hashlib.sha256(payload).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError(
                f"artifact checksum mismatch: expected {expected_sha256}, calculated {digest}"
            )
        destination = self.root / digest[:2] / digest[2:4] / digest
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{digest}.",
                dir=destination.parent,
            )
            try:
                with os.fdopen(descriptor, "wb") as temporary:
                    temporary.write(payload)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, destination)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return digest, destination

    def put_json(self, value: Any) -> tuple[str, Path]:
        return self.put_bytes(_canonical(value))

    def read_json(self, path: str) -> Any:
        candidate = self.resolve_path(path)
        return json.loads(candidate.read_text(encoding="utf-8"))

    def resolve_path(self, path: str) -> Path:
        candidate = Path(path).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("object path is outside the configured store")
        return candidate


class RegistryStore:
    """Persistent registry for reproducible inputs, outputs, jobs, and audit events."""

    def __init__(self, database_path: str | Path, object_directory: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.objects = ContentAddressedStore(object_directory)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_id TEXT PRIMARY KEY,
                    manifest_json TEXT NOT NULL,
                    object_path TEXT,
                    checksum_verified INTEGER NOT NULL,
                    registered_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    object_path TEXT NOT NULL,
                    object_sha256 TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scenarios (
                    portfolio_id TEXT PRIMARY KEY,
                    object_path TEXT NOT NULL,
                    object_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_type TEXT NOT NULL,
                    plan_id TEXT,
                    object_path TEXT NOT NULL,
                    object_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    progress_json TEXT,
                    result_path TEXT,
                    result_sha256 TEXT,
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_status_created_idx
                    ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS audit_log (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    occurred_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL UNIQUE
                );
                """
            )
            connection.execute("PRAGMA user_version = 1")

    def register_dataset(
        self,
        manifest: DatasetManifest,
        payload: bytes | None,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        if manifest.complete and payload is None:
            raise ValueError("a complete dataset snapshot must include immutable content")
        object_path = None
        verified = False
        if payload is not None:
            _digest, path = self.objects.put_bytes(payload, manifest.checksum_sha256)
            object_path = str(path)
            verified = True
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT manifest_json, object_path FROM datasets WHERE dataset_id = ?",
                (manifest.dataset_id,),
            ).fetchone()
            manifest_json = manifest.model_dump_json()
            if existing and (
                existing["manifest_json"] != manifest_json or existing["object_path"] != object_path
            ):
                raise ValueError("dataset IDs are immutable; register a new versioned dataset ID")
            connection.execute(
                """
                INSERT OR IGNORE INTO datasets
                    (dataset_id, manifest_json, object_path, checksum_verified, registered_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (manifest.dataset_id, manifest_json, object_path, int(verified), _utc_now()),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                action="dataset.registered",
                entity_type="dataset",
                entity_id=manifest.dataset_id,
                payload_sha256=manifest.checksum_sha256,
                details={"checksum_verified": verified, "complete": manifest.complete},
            )
        return self.get_dataset(manifest.dataset_id) or {}

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
        return self._dataset_row(row) if row else None

    def list_datasets(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM datasets ORDER BY registered_at DESC, dataset_id"
            ).fetchall()
        return [self._dataset_row(row) for row in rows]

    def get_dataset_object_path(self, dataset_id: str) -> Path | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT object_path FROM datasets WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
        if row is None or not row["object_path"]:
            return None
        return self.objects.resolve_path(row["object_path"])

    @staticmethod
    def _dataset_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "manifest": json.loads(row["manifest_json"]),
            "checksum_verified": bool(row["checksum_verified"]),
            "content_available": bool(row["object_path"]),
            "registered_at": row["registered_at"],
        }

    def register_plan(self, partition: CityPartition, *, actor_id: str) -> dict[str, Any]:
        if not partition.plan_id:
            raise ValueError("a persisted plan must have a stable plan ID")
        payload = partition.model_dump(mode="json")
        digest, path = self.objects.put_json(payload)
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT object_sha256 FROM plans WHERE plan_id = ?", (partition.plan_id,)
            ).fetchone()
            if existing and existing["object_sha256"] != digest:
                raise ValueError("plan ID collision with different content")
            connection.execute(
                """
                INSERT OR IGNORE INTO plans
                    (plan_id, object_path, object_sha256, stage, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    partition.plan_id,
                    str(path),
                    digest,
                    partition.readiness.status.value,
                    _utc_now(),
                ),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                action="plan.registered",
                entity_type="plan",
                entity_id=partition.plan_id,
                payload_sha256=digest,
                details={"stage": partition.readiness.status.value},
            )
        return self.get_plan_summary(partition.plan_id) or {}

    def get_plan_summary(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if row is None:
            return None
        return {
            "plan_id": row["plan_id"],
            "object_sha256": row["object_sha256"],
            "stage": row["stage"],
            "created_at": row["created_at"],
        }

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT object_path FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        return self.objects.read_json(row["object_path"]) if row else None

    def list_plans(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT plan_id, object_sha256, stage, created_at FROM plans ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def register_scenarios(
        self,
        portfolio: ScenarioPortfolio,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        digest, path = self.objects.put_json(portfolio.model_dump(mode="json"))
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT object_sha256 FROM scenarios WHERE portfolio_id = ?",
                (portfolio.portfolio_id,),
            ).fetchone()
            if existing and existing["object_sha256"] != digest:
                raise ValueError("portfolio ID collision with different content")
            connection.execute(
                """
                INSERT OR IGNORE INTO scenarios
                    (portfolio_id, object_path, object_sha256, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (portfolio.portfolio_id, str(path), digest, _utc_now()),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                action="scenario_portfolio.registered",
                entity_type="scenario_portfolio",
                entity_id=portfolio.portfolio_id,
                payload_sha256=digest,
                details={"candidate_count": len(portfolio.candidates)},
            )
        return {
            "portfolio_id": portfolio.portfolio_id,
            "object_sha256": digest,
            "created": existing is None,
        }

    def get_scenario_portfolio(self, portfolio_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT object_path FROM scenarios WHERE portfolio_id = ?", (portfolio_id,)
            ).fetchone()
        return self.objects.read_json(row["object_path"]) if row else None

    def list_scenario_portfolios(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT portfolio_id, object_sha256, created_at FROM scenarios "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def register_artifact(
        self,
        *,
        artifact_id: str,
        artifact_type: str,
        payload: dict[str, Any],
        actor_id: str,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        digest, path = self.objects.put_json(payload)
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT object_sha256 FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if existing and existing["object_sha256"] != digest:
                raise ValueError("artifact IDs are immutable")
            connection.execute(
                """
                INSERT OR IGNORE INTO artifacts
                    (artifact_id, artifact_type, plan_id, object_path, object_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, artifact_type, plan_id, str(path), digest, _utc_now()),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                action=f"artifact.{artifact_type}.registered",
                entity_type="artifact",
                entity_id=artifact_id,
                payload_sha256=digest,
                details={"plan_id": plan_id},
            )
        return {
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "plan_id": plan_id,
            "object_sha256": digest,
            "created": existing is None,
        }

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "artifact_id": row["artifact_id"],
            "artifact_type": row["artifact_type"],
            "plan_id": row["plan_id"],
            "object_sha256": row["object_sha256"],
            "created_at": row["created_at"],
            "payload": self.objects.read_json(row["object_path"]),
        }

    def list_artifacts(self, *, plan_id: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT artifact_id, artifact_type, plan_id, object_sha256, created_at FROM artifacts"
        )
        parameters: tuple[str, ...] = ()
        if plan_id is not None:
            query += " WHERE plan_id = ?"
            parameters = (plan_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def create_job(self, job_type: str, request: dict[str, Any], *, actor_id: str) -> str:
        job_id = uuid.uuid4().hex
        now = _utc_now()
        request_json = _canonical(request).decode()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs
                    (job_id, job_type, status, request_json, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, ?, ?)
                """,
                (job_id, job_type, request_json, now, now),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                action="job.queued",
                entity_type="job",
                entity_id=job_id,
                payload_sha256=hashlib.sha256(request_json.encode()).hexdigest(),
                details={"job_type": job_type},
            )
        return job_id

    def recover_running_jobs(self) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = 'queued', updated_at = ?
                WHERE status = 'running' AND cancel_requested = 0
                """,
                (_utc_now(),),
            )
            return cursor.rowcount

    def claim_next_job(self, job_type: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued' AND job_type = ? AND cancel_requested = 0
                ORDER BY created_at LIMIT 1
                """,
                (job_type,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ?
                WHERE job_id = ?
                """,
                (_utc_now(), row["job_id"]),
            )
        claimed = dict(row)
        claimed["request"] = json.loads(claimed.pop("request_json"))
        claimed["status"] = "running"
        claimed["attempts"] += 1
        return claimed

    def update_job_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET progress_json = ?, updated_at = ? WHERE job_id = ?",
                (_canonical(progress).decode(), _utc_now(), job_id),
            )

    def complete_job(self, job_id: str, result: dict[str, Any], *, actor_id: str) -> None:
        digest, path = self.objects.put_json(result)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET status = 'completed', result_path = ?, result_sha256 = ?,
                    updated_at = ? WHERE job_id = ?
                """,
                (str(path), digest, _utc_now(), job_id),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                action="job.completed",
                entity_type="job",
                entity_id=job_id,
                payload_sha256=digest,
                details={},
            )

    def fail_job(self, job_id: str, error: str, *, actor_id: str) -> None:
        safe_error = error[-4_000:]
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = 'failed', error = ?, updated_at = ? WHERE job_id = ?",
                (safe_error, _utc_now(), job_id),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                action="job.failed",
                entity_type="job",
                entity_id=job_id,
                payload_sha256=hashlib.sha256(safe_error.encode()).hexdigest(),
                details={},
            )

    def request_job_cancel(self, job_id: str, *, actor_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None or row["status"] in {"completed", "failed", "cancelled"}:
                return False
            new_status = "cancelled" if row["status"] == "queued" else row["status"]
            connection.execute(
                """
                UPDATE jobs SET cancel_requested = 1, status = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (new_status, _utc_now(), job_id),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                action="job.cancel_requested",
                entity_type="job",
                entity_id=job_id,
                payload_sha256="0" * 64,
                details={},
            )
            return True

    def job_cancel_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def mark_job_cancelled(self, job_id: str, *, actor_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = 'cancelled', updated_at = ? WHERE job_id = ?",
                (_utc_now(), job_id),
            )
            self._append_audit(
                connection,
                actor_id=actor_id,
                action="job.cancelled",
                entity_type="job",
                entity_id=job_id,
                payload_sha256="0" * 64,
                details={},
            )

    def get_job(self, job_id: str, include_result: bool = False) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["progress"] = (
            json.loads(result.pop("progress_json")) if result.get("progress_json") else None
        )
        result["cancel_requested"] = bool(result["cancel_requested"])
        result_path = result.pop("result_path")
        if include_result and result_path:
            result["result"] = self.objects.read_json(result_path)
        return result

    def verify_audit_chain(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM audit_log ORDER BY sequence").fetchall()
        previous = "0" * 64
        for row in rows:
            material = self._audit_material(
                event_id=row["event_id"],
                occurred_at=row["occurred_at"],
                actor_id=row["actor_id"],
                action=row["action"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                payload_sha256=row["payload_sha256"],
                details_json=row["details_json"],
                previous_hash=previous,
            )
            calculated = hashlib.sha256(material).hexdigest()
            if row["previous_hash"] != previous or row["entry_hash"] != calculated:
                return {
                    "valid": False,
                    "entries": len(rows),
                    "failed_sequence": row["sequence"],
                    "head_hash": previous,
                }
            previous = calculated
        return {"valid": True, "entries": len(rows), "head_hash": previous}

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload_sha256: str,
        details: dict[str, Any],
    ) -> None:
        previous_row = connection.execute(
            "SELECT entry_hash FROM audit_log ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous_row["entry_hash"] if previous_row else "0" * 64
        event_id = uuid.uuid4().hex
        occurred_at = _utc_now()
        details_json = _canonical(details).decode()
        material = self._audit_material(
            event_id=event_id,
            occurred_at=occurred_at,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_sha256=payload_sha256,
            details_json=details_json,
            previous_hash=previous_hash,
        )
        entry_hash = hashlib.sha256(material).hexdigest()
        connection.execute(
            """
            INSERT INTO audit_log
                (event_id, occurred_at, actor_id, action, entity_type, entity_id,
                 payload_sha256, details_json, previous_hash, entry_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                occurred_at,
                actor_id,
                action,
                entity_type,
                entity_id,
                payload_sha256,
                details_json,
                previous_hash,
                entry_hash,
            ),
        )

    @staticmethod
    def _audit_material(**values: str) -> bytes:
        return _canonical(values)
