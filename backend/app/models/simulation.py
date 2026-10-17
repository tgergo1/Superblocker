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
    max_map_links: int = Field(default=6_000, ge=0, le=200_000)
    min_map_volume_vph: float = Field(default=20.0, ge=0)
    # Junctions. Routes are found movement by movement, so OSM turn
    # restrictions apply, U-turns are only allowed at dead ends, and every
    # movement pays a fixed delay that depends on how the junction is
    # controlled: traffic signals (OSM highway=traffic_signals) or priority
    # (the lower road class gives way). The delays are typical averages for
    # urban junctions, not measured at each one.
    turn_model: bool = True
    signal_delay_straight_s: float = Field(default=15.0, ge=0, le=300)
    signal_delay_right_s: float = Field(default=12.0, ge=0, le=300)
    signal_delay_left_s: float = Field(default=25.0, ge=0, le=300)
    give_way_delay_s: float = Field(default=6.0, ge=0, le=300)
    give_way_left_extra_s: float = Field(default=6.0, ge=0, le=300)
    priority_left_delay_s: float = Field(default=3.0, ge=0, le=300)
    dead_end_u_turn_s: float = Field(default=20.0, ge=0, le=300)
    # Destination-only ("kivéve célforgalom") gates stay open to cars in the
    # plan, but a route through one costs this much extra, so through traffic
    # avoids them while trips to and from the streets behind still use them.
    # The penalty steers routes only; it is not counted in reported trip times.
    gate_penalty_s: float = Field(default=600.0, ge=0, le=7_200)
    # Share of peak car trips that start or end outside the simulated area and
    # enter or leave on its motorways, trunk and primary roads.
    external_trip_share: float = Field(default=0.15, ge=0, le=0.8)
    gateway_band_m: float = Field(default=600.0, ge=50, le=5_000)
    # How car trips between two zones respond to the plan's change in their
    # travel time: trips scale with (time with plan / time today) ** elasticity.
    # 0 keeps demand fixed (the worst case for the plan).
    demand_elasticity: float = Field(default=-0.5, ge=-3, le=0)
    elastic_iterations: int = Field(default=4, ge=1, le=20)

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
    gated_in_plan: bool = False
    is_boundary_road: bool = False
    planned_elastic_vph: float | None = Field(default=None, ge=0)
    lanes: float = Field(default=1.0, gt=0)
    length_m: float = Field(default=0.0, ge=0)
    free_speed_kph: float = Field(default=0.0, ge=0)
    baseline_speed_kph: float = Field(default=0.0, ge=0)
    planned_speed_kph: float = Field(default=0.0, ge=0)
    planned_elastic_speed_kph: float | None = Field(default=None, ge=0)


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
    # The plan at today's demand once trips respond to the change in travel
    # time (``settings.demand_elasticity``); ``None`` when demand is fixed.
    planned_elastic: SimulationLevelResult | None = None
    trips_not_made_share: float | None = Field(default=None, ge=0, le=1)
    elastic_mean_trip_minutes_change_percent: float | None = None
    elastic_vehicle_km_change_percent: float | None = None
    demand_source: str = "street length"
    external_trip_share: float = Field(default=0.0, ge=0, le=1)
    gateway_count: int = Field(default=0, ge=0)
    turn_model: bool = False
    movements: int = Field(default=0, ge=0)
    turn_restrictions_applied: int = Field(default=0, ge=0)
    signalised_junctions: int = Field(default=0, ge=0)
    destination_gates: int = Field(default=0, ge=0)
    links: list[SimulatedLink] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    processing_time_seconds: float = Field(default=0.0, ge=0)
