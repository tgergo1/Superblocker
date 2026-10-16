"""Authenticated durable job submission, status, result, and cancellation APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.registry import RegistryDep
from app.core.auth import require_admin_key
from app.models.schemas import PartitionRequest
from app.services.jobs.partition_runner import get_partition_job_runner

router = APIRouter(prefix="/jobs")
AdminActor = Annotated[str, Depends(require_admin_key)]


@router.post("/partition", status_code=202)
async def create_partition_job(
    request: PartitionRequest,
    actor_id: AdminActor,
    store: RegistryDep,
):
    job_id = store.create_job(
        "partition",
        request.model_dump(mode="json"),
        actor_id=actor_id,
    )
    get_partition_job_runner().wake()
    return {"job_id": job_id, "status": "queued"}


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    _actor_id: AdminActor,
    store: RegistryDep,
    include_result: bool = False,
):
    job = store.get_job(job_id, include_result=include_result)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/{job_id}", status_code=202)
async def cancel_job(job_id: str, actor_id: AdminActor, store: RegistryDep):
    if not store.request_job_cancel(job_id, actor_id=actor_id):
        raise HTTPException(status_code=409, detail="Job does not exist or is already terminal")
    return {"job_id": job_id, "cancel_requested": True}
