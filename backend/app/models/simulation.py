"""Request and result models for the citywide capacity simulator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.schemas import CityPartition


class CapacitySimulationSettings(BaseModel):
    """Assumptions for the synthetic peak-hour demand and the assignment."""

    zone_size_m: float = Field(default=800.0, ge=150.0, le=5_000.0)
    max_zones: int = Field(default=100, ge=4, le=1_000)
    connectors_per_zone: int = Field(default=8, ge=1, le=30)
    # Peak-hour car trips at demand level 1.0. Left empty, the level is
    # calibrated so today's average trip takes ``target_peak_travel_time_index``
    # times its empty-street time, a typical European capital at peak.
    peak_hour_trips: float | None = Field(default=None, gt=0, le=10_000_000)
    target_peak_travel_time_index: float = Field(default=1.3, gt=1, le=5)
    # Gravity model: share of trips falls by exp(-decay * free-flow minutes).
    distance_decay_per_minute: float = Field(default=0.12, ge=0, le=2)
    demand_levels: list[float] = Field(
        default_factory=lambda: [0.5, 1.0, 1.5, 2.0, 3.0],
        min_length=1,
        max_length=12,
    )
    max_iterations: int = Field(default=6, ge=1, le=50)
    convergence_gap: float = Field(default=0.005, gt=0, le=1)
    bpr_alpha: float = Field(default=0.15, ge=0, le=10)
    bpr_beta: float = Field(default=4.0, ge=1, le=10)
    # The network is "full" when the average trip takes this many times as
    # long as on today's empty streets.
    breakdown_travel_time_index: float = Field(default=2.0, gt=1, le=10)
    congested_volume_capacity_ratio: float = Field(default=0.9, gt=0, le=5)
    max_map_links: int = Field(default=6_000, ge=0, le=50_000)
    min_map_volume_vph: float = Field(default=20.0, ge=0)

    @model_validator(mode="after")
    def _breakdown_above_calibration(self) -> CapacitySimulationSettings:
        if self.breakdown_travel_time_index <= self.target_peak_travel_time_index:
            raise ValueError(
                "breakdown_travel_time_index must exceed target_peak_travel_time_index"
            )
        return self

    @field_validator("demand_levels")
    @classmethod
    def _sorted_positive_levels(cls, levels: list[float]) -> list[float]:
        cleaned = sorted({round(float(level), 4) for level in levels})
        if any(level <= 0 or level > 20 for level in cleaned):
            raise ValueError("demand levels must be between 0 and 20")
        if 1.0 not in cleaned:
            cleaned.append(1.0)
            cleaned.sort()
        return cleaned


class CapacitySimulationRequest(BaseModel):
    """A plan to test; the street network is reloaded or taken from the cache."""

    partition: CityPartition
    settings: CapacitySimulationSettings = Field(default_factory=CapacitySimulationSettings)


class SimulationLevelResult(BaseModel):
    """Equilibrium network performance at one demand level."""

    demand_multiplier: float
    trips: float = Field(ge=0)
    served_trips: float = Field(ge=0)
    blocked_trips: float = Field(ge=0)
    vehicle_km: float = Field(ge=0)
    vehicle_hours: float = Field(ge=0)
    mean_trip_minutes: float = Field(ge=0)
    mean_speed_kph: float = Field(ge=0)
    travel_time_index: float = Field(ge=0)
    congested_km: float = Field(ge=0)
    over_capacity_km: float = Field(ge=0)
    relative_gap: float = Field(ge=0)
    iterations: int = Field(ge=0)


class SimulationScenarioSummary(BaseModel):
    """One network state across every tested demand level."""

    name: Literal["without_superblocks", "with_superblocks"]
    levels: list[SimulationLevelResult]
    # Demand multiplier at which the travel-time index reaches the breakdown
    # threshold; ``None`` when it is never reached in the tested range.
    capacity_multiplier: float | None = None
    capacity_trips: float | None = None
    capacity_reached: bool = False
    drivable_km: float = Field(default=0.0, ge=0)


class SimulatedLink(BaseModel):
    """Both directions of one street section at demand level 1.0."""

    coordinates: list[list[float]]
    osm_id: int
    name: str | None = None
    highway: str
    baseline_vph: float = Field(ge=0)
    planned_vph: float = Field(ge=0)
    capacity_vph: float = Field(gt=0)
    baseline_vc: float = Field(ge=0)
    planned_vc: float = Field(ge=0)
    closed_in_plan: bool = False
    is_boundary_road: bool = False


class CapacitySimulationResult(BaseModel):
    """With and without superblocks: travel times, loads and network capacity."""

    method: str = "static_user_equilibrium_frank_wolfe"
    settings: CapacitySimulationSettings
    base_peak_hour_trips: float = Field(ge=0)
    demand_calibrated: bool
    zone_count: int = Field(ge=0)
    connector_count: int = Field(ge=0)
    node_count: int = Field(ge=0)
    link_count: int = Field(ge=0)
    links_closed_by_plan: int = Field(ge=0)
    junctions_cut_off_by_plan: int = Field(default=0, ge=0)
    baseline: SimulationScenarioSummary
    planned: SimulationScenarioSummary
    # Relative change of the planned network against the baseline at level 1.0.
    capacity_change_percent: float | None = None
    mean_trip_minutes_change_percent: float = 0.0
    vehicle_km_change_percent: float = 0.0
    vehicle_hours_change_percent: float = 0.0
    links: list[SimulatedLink] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    processing_time_seconds: float = Field(default=0.0, ge=0)
