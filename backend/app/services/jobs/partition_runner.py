"""Restart-safe worker for durable city partition jobs."""

from __future__ import annotations

import logging
import threading
from functools import lru_cache

from app.api.routes.analysis import (
    WorkCancelled,
    _evidence_context,
    _validate_registered_dataset_manifests,
    run_partition_sync,
)
from app.api.routes.registry import get_registry_store
from app.models.schemas import PartitionRequest

logger = logging.getLogger(__name__)


class _ProgressSink:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.store = get_registry_store()

    def put_nowait(self, progress: dict) -> None:
        self.store.update_job_progress(self.job_id, progress)


class PartitionJobRunner:
    """Single worker that resumes queued jobs and persists every result."""

    def __init__(self) -> None:
        self.store = get_registry_store()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.store.recover_running_jobs()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="durable-partition-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def wake(self) -> None:
        self._wake_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            job = self.store.claim_next_job("partition")
            if job is None:
                self._wake_event.wait(timeout=1)
                self._wake_event.clear()
                continue
            self._execute(job)

    def _execute(self, job: dict) -> None:
        job_id = job["job_id"]
        cancel_event = threading.Event()
        cancellation_watcher = threading.Thread(
            target=self._watch_cancellation,
            args=(job_id, cancel_event),
            daemon=True,
        )
        cancellation_watcher.start()
        try:
            request = PartitionRequest.model_validate(job["request"])
            _validate_registered_dataset_manifests(request)
            result = run_partition_sync(
                request.bbox,
                request.boundary,
                request.target_size_hectares,
                request.min_area_hectares,
                request.max_area_hectares,
                request.num_sectors,
                request.enforce_constraints,
                {road_type.value for road_type in request.arterial_road_types},
                request.traffic_observations,
                request.access_targets,
                request.access_dataset_source,
                request.access_dataset_complete,
                _evidence_context(request),
                request.turn_restrictions,
                request.area_exclusions,
                request.operational_requirements,
                request.active_mode_requirements,
                request.demand_model,
                request.assignment_settings,
                request.authoritative_road_segments,
                _ProgressSink(job_id),
                cancel_event,
            )
            if cancel_event.is_set():
                self.store.mark_job_cancelled(job_id, actor_id="partition-worker")
                return
            partition = result["partition"]
            self.store.register_plan(partition, actor_id="partition-worker")
            self.store.complete_job(
                job_id,
                {
                    "partition": partition.model_dump(mode="json"),
                    "street_network": result["network"].model_dump(mode="json"),
                    "processing_time_seconds": result["processing_time"],
                },
                actor_id="partition-worker",
            )
        except WorkCancelled:
            self.store.mark_job_cancelled(job_id, actor_id="partition-worker")
        except Exception as exc:
            logger.exception("Durable partition job %s failed", job_id)
            self.store.fail_job(job_id, str(exc), actor_id="partition-worker")
        finally:
            cancel_event.set()
            cancellation_watcher.join(timeout=2)

    def _watch_cancellation(self, job_id: str, cancel_event: threading.Event) -> None:
        while not cancel_event.wait(timeout=0.5):
            if self._stop_event.is_set() or self.store.job_cancel_requested(job_id):
                cancel_event.set()
                return


@lru_cache
def get_partition_job_runner() -> PartitionJobRunner:
    return PartitionJobRunner()
