"""Operational APIs for immutable evidence, plans, and audit verification."""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app.core.auth import require_admin_key
from app.core.config import get_settings
from app.models.schemas import CityPartition, DatasetManifest, ScenarioPortfolio
from app.services.registry.store import RegistryStore

router = APIRouter(prefix="/registry")
settings = get_settings()


@lru_cache
def get_registry_store() -> RegistryStore:
    return RegistryStore(
        settings.registry_database_path,
        settings.registry_object_directory,
    )


AdminActor = Annotated[str, Depends(require_admin_key)]
RegistryDep = Annotated[RegistryStore, Depends(get_registry_store)]


@router.post("/datasets")
async def register_dataset(
    manifest_json: Annotated[str, Form()],
    actor_id: AdminActor,
    store: RegistryDep,
    snapshot: Annotated[UploadFile | None, File()] = None,
):
    """Register a versioned dataset and verify its immutable snapshot checksum."""
    try:
        manifest = DatasetManifest.model_validate_json(manifest_json)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    payload = None
    if snapshot is not None:
        payload = await snapshot.read(settings.registry_max_upload_bytes + 1)
        if len(payload) > settings.registry_max_upload_bytes:
            raise HTTPException(status_code=413, detail="Dataset snapshot exceeds the upload limit")
    try:
        return store.register_dataset(manifest, payload, actor_id=actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/datasets")
async def list_datasets(_actor_id: AdminActor, store: RegistryDep):
    return {"datasets": store.list_datasets()}


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, _actor_id: AdminActor, store: RegistryDep):
    record = store.get_dataset(dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return record


@router.get("/datasets/{dataset_id}/snapshot")
async def download_dataset_snapshot(
    dataset_id: str,
    _actor_id: AdminActor,
    store: RegistryDep,
):
    path = store.get_dataset_object_path(dataset_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Dataset snapshot not found")
    return FileResponse(
        path, media_type="application/octet-stream", filename=f"{dataset_id}.snapshot"
    )


@router.post("/plans")
async def register_plan(
    partition: CityPartition,
    actor_id: AdminActor,
    store: RegistryDep,
):
    try:
        return store.register_plan(partition, actor_id=actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/plans")
async def list_plans(_actor_id: AdminActor, store: RegistryDep):
    return {"plans": store.list_plans()}


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, _actor_id: AdminActor, store: RegistryDep):
    plan = store.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@router.post("/scenarios")
async def register_scenarios(
    portfolio: ScenarioPortfolio,
    actor_id: AdminActor,
    store: RegistryDep,
):
    try:
        return store.register_scenarios(portfolio, actor_id=actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/scenarios")
async def list_scenarios(_actor_id: AdminActor, store: RegistryDep):
    return {"scenario_portfolios": store.list_scenario_portfolios()}


@router.get("/scenarios/{portfolio_id}")
async def get_scenarios(portfolio_id: str, _actor_id: AdminActor, store: RegistryDep):
    portfolio = store.get_scenario_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Scenario portfolio not found")
    return portfolio


@router.get("/artifacts")
async def list_artifacts(
    _actor_id: AdminActor,
    store: RegistryDep,
    plan_id: str | None = None,
):
    return {"artifacts": store.list_artifacts(plan_id=plan_id)}


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, _actor_id: AdminActor, store: RegistryDep):
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@router.get("/audit/verify")
async def verify_audit(
    _actor_id: AdminActor,
    store: RegistryDep,
):
    return store.verify_audit_chain()
