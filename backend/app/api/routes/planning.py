"""Scenario optimization, engineering, simulation, and pilot-monitoring APIs."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.registry import RegistryDep
from app.core.auth import require_admin_key
from app.core.config import get_settings
from app.core.workload import guard_expensive_request
from app.models.schemas import (
    ActiveModeRequirement,
    AssignmentSettings,
    CityPartition,
    DemandModel,
    EngineeringPackage,
    MonitoringEvaluation,
    MonitoringMetricDefinition,
    MonitoringObservation,
    OperationalRequirement,
    OptimizationSettings,
    ScenarioPortfolio,
    SimulationRun,
    StreetModification,
    VehicleClass,
)
from app.services.engineering.package_compiler import EngineeringPackageCompiler
from app.services.monitoring.rollback import evaluate_monitoring
from app.services.optimization.scenario_optimizer import ScenarioOptimizer
from app.services.osm_service import get_street_network_graph
from app.services.partitioning.city_partitioner import compute_plan_id
from app.services.simulation.sumo_adapter import SumoAdapter

router = APIRouter(prefix="/planning")
settings = get_settings()
AdminActor = Annotated[str, Depends(require_admin_key)]
ExpensivePermit = Annotated[None, Depends(guard_expensive_request)]


class ScenarioAlternativeInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1_000)
    modifications: list[StreetModification]


class ScenarioOptimizationRequest(BaseModel):
    partition: CityPartition
    demand_model: DemandModel
    alternatives: list[ScenarioAlternativeInput] = Field(default_factory=list, max_length=100)
    operational_requirements: list[OperationalRequirement] = Field(default_factory=list)
    active_mode_requirements: list[ActiveModeRequirement] = Field(default_factory=list)
    assignment_settings: AssignmentSettings = Field(default_factory=AssignmentSettings)
    optimization_settings: OptimizationSettings = Field(default_factory=OptimizationSettings)


class EngineeringPackageRequest(BaseModel):
    partition: CityPartition
    jurisdiction: str = Field(..., min_length=2, max_length=200)
    currency: str = Field(default="EUR", min_length=3, max_length=3)


class MonitoringEvaluationRequest(BaseModel):
    plan_id: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    evaluated_at: str = Field(..., min_length=4, max_length=100)
    definitions: list[MonitoringMetricDefinition] = Field(..., min_length=1, max_length=10_000)
    observations: list[MonitoringObservation] = Field(default_factory=list, max_length=1_000_000)


class SimulationRequest(BaseModel):
    partition: CityPartition
    demand_model: DemandModel
    network_state: Literal["baseline", "planned"] = "planned"
    execute: bool = False
    random_seed: int = 42
    timeout_seconds: int = Field(default=3_600, ge=30, le=86_400)


def _verified_partition(partition: CityPartition) -> None:
    if not partition.plan_id or compute_plan_id(partition) != partition.plan_id:
        raise HTTPException(
            status_code=400,
            detail="The supplied plan ID does not match the partition contents",
        )


def _all_modifications(partition: CityPartition) -> list[StreetModification]:
    return [
        modification
        for superblock in partition.superblocks
        for modification in superblock.modifications
    ]


async def _active_graphs(request: ScenarioOptimizationRequest) -> dict:
    graphs = {}
    network_types = {
        "walk" if requirement.mode in {"walk", "wheel"} else "bike"
        for requirement in request.active_mode_requirements
    }
    for network_type in sorted(network_types):
        graph = await get_street_network_graph(
            request.partition.bbox,
            network_type=network_type,
            boundary=request.partition.boundary,
        )
        if network_type == "walk":
            graphs["walk"] = graph
            graphs["wheel"] = graph
        else:
            graphs["bicycle"] = graph
    return graphs


def _automatic_alternatives(
    partition: CityPartition,
) -> list[tuple[str, str, list[StreetModification]]]:
    generated = _all_modifications(partition)
    tactical = [
        item.model_copy(
            update={
                "construction_treatment": item.construction_treatment
                or "Reversible pilot treatment with temporary signs, markings, and removable elements",
                "estimated_capital_cost": (
                    item.estimated_capital_cost * 0.65
                    if item.estimated_capital_cost is not None
                    else None
                ),
                "annual_maintenance_cost": (
                    item.annual_maintenance_cost * 1.25
                    if item.annual_maintenance_cost is not None
                    else None
                ),
            }
        )
        for item in generated
    ]
    resilient = [
        item.model_copy(
            update={
                "allowed_vehicle_classes": sorted(
                    set(item.allowed_vehicle_classes) | {VehicleClass.EMERGENCY},
                    key=lambda value: value.value,
                )
                if item.modification_type.value == "modal_filter"
                else item.allowed_vehicle_classes,
                "emergency_override_required": (
                    True
                    if item.modification_type.value in {"modal_filter", "full_closure"}
                    else item.emergency_override_required
                ),
            }
        )
        for item in generated
    ]
    return [
        ("Generated plan", "The exact topology actions returned by the partitioner.", generated),
        (
            "Reversible pilot",
            "The same legal vehicle topology compiled as reversible pilot works.",
            tactical,
        ),
        (
            "Emergency-resilient design",
            "The generated topology with explicit emergency override requirements.",
            resilient,
        ),
    ]


@router.post("/scenarios", response_model=ScenarioPortfolio)
async def optimize_scenarios(
    request: ScenarioOptimizationRequest,
    actor_id: AdminActor,
    store: RegistryDep,
    _permit: ExpensivePermit,
):
    _verified_partition(request.partition)
    graph = await get_street_network_graph(
        request.partition.bbox,
        boundary=request.partition.boundary,
    )
    active_graphs = await _active_graphs(request)
    alternatives = (
        [(item.name, item.description, item.modifications) for item in request.alternatives]
        if request.alternatives
        else _automatic_alternatives(request.partition)
    )
    portfolio = await asyncio.to_thread(
        ScenarioOptimizer(
            graph,
            request.partition.turn_restrictions,
            active_graphs,
        ).optimize,
        demand=request.demand_model,
        alternatives=alternatives,
        boundary_osm_ids=set(request.partition.arterial_network),
        operational_requirements=request.operational_requirements,
        active_mode_requirements=request.active_mode_requirements,
        assignment_settings=request.assignment_settings,
        optimization_settings=request.optimization_settings,
    )
    store.register_scenarios(portfolio, actor_id=actor_id)
    return portfolio


@router.post("/engineering-package", response_model=EngineeringPackage)
async def compile_engineering_package(
    request: EngineeringPackageRequest,
    actor_id: AdminActor,
    store: RegistryDep,
    _permit: ExpensivePermit,
):
    _verified_partition(request.partition)
    graph = await get_street_network_graph(
        request.partition.bbox,
        boundary=request.partition.boundary,
    )
    package = EngineeringPackageCompiler(graph).compile(
        request.partition.plan_id,
        _all_modifications(request.partition),
        jurisdiction=request.jurisdiction,
        currency=request.currency.upper(),
    )
    store.register_artifact(
        artifact_id=package.package_id,
        artifact_type="engineering_package",
        plan_id=request.partition.plan_id,
        payload=package.model_dump(mode="json"),
        actor_id=actor_id,
    )
    return package


@router.post("/monitoring/evaluate", response_model=MonitoringEvaluation)
async def evaluate_pilot_monitoring(
    request: MonitoringEvaluationRequest,
    actor_id: AdminActor,
    store: RegistryDep,
):
    evaluation = evaluate_monitoring(
        plan_id=request.plan_id,
        evaluated_at=request.evaluated_at,
        definitions=request.definitions,
        observations=request.observations,
    )
    artifact_id = hashlib.sha256(evaluation.model_dump_json().encode()).hexdigest()[:24]
    store.register_artifact(
        artifact_id=artifact_id,
        artifact_type="monitoring_evaluation",
        plan_id=request.plan_id,
        payload=evaluation.model_dump(mode="json"),
        actor_id=actor_id,
    )
    return evaluation


@router.post("/simulation", response_model=SimulationRun)
async def run_simulation(
    request: SimulationRequest,
    actor_id: AdminActor,
    store: RegistryDep,
    _permit: ExpensivePermit,
):
    _verified_partition(request.partition)
    graph = await get_street_network_graph(
        request.partition.bbox,
        boundary=request.partition.boundary,
    )
    modifications = (
        _all_modifications(request.partition) if request.network_state == "planned" else []
    )
    output_directory = (
        Path(settings.simulation_work_directory).resolve()
        / request.partition.plan_id
        / uuid.uuid4().hex
    )
    adapter = SumoAdapter(graph, request.partition.turn_restrictions)
    if request.execute:
        result = await asyncio.to_thread(
            adapter.run,
            request.demand_model,
            modifications,
            output_directory,
            network_state=request.network_state,
            random_seed=request.random_seed,
            timeout_seconds=request.timeout_seconds,
        )
    else:
        result = await asyncio.to_thread(
            adapter.export,
            request.demand_model,
            modifications,
            output_directory,
            network_state=request.network_state,
            random_seed=request.random_seed,
        )
    store.register_artifact(
        artifact_id=result.simulation_id + "-" + output_directory.name[:8],
        artifact_type="simulation_run",
        plan_id=request.partition.plan_id,
        payload=result.model_dump(mode="json"),
        actor_id=actor_id,
    )
    return result
