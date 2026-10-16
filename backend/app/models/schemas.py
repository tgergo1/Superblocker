from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from shapely.geometry import shape


class RoadType(StrEnum):
    """Road classification types."""

    MOTORWAY = "motorway"
    TRUNK = "trunk"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    RESIDENTIAL = "residential"
    LIVING_STREET = "living_street"
    PEDESTRIAN = "pedestrian"
    UNCLASSIFIED = "unclassified"
    SERVICE = "service"


class NetworkType(StrEnum):
    """Network profiles supported by OSMnx."""

    DRIVE = "drive"
    WALK = "walk"
    BIKE = "bike"
    ALL = "all"
    ALL_PUBLIC = "all_public"


class TransportMode(StrEnum):
    """Travel modes represented by planning evidence and network permissions."""

    WALK = "walk"
    WHEEL = "wheel"
    BICYCLE = "bicycle"
    TRANSIT = "transit"
    MOTOR_VEHICLE = "motor_vehicle"
    FREIGHT = "freight"
    EMERGENCY = "emergency"


class VehicleClass(StrEnum):
    """Vehicle classes that can have distinct access and demand rules."""

    ALL_MOTOR = "all_motor"
    CAR = "car"
    MOTORCYCLE = "motorcycle"
    LIGHT_GOODS = "light_goods"
    HEAVY_GOODS = "heavy_goods"
    BUS = "bus"
    TAXI = "taxi"
    REFUSE = "refuse"
    EMERGENCY = "emergency"
    PERMIT = "permit"


class StudyAreaMode(StrEnum):
    """Geographic extent used for demand and spillover analysis."""

    BOUNDING_BOX = "bounding_box"
    ADMINISTRATIVE_POLYGON = "administrative_polygon"
    FUNCTIONAL_URBAN_AREA = "functional_urban_area"


class NetworkAuthorityMode(StrEnum):
    """Authority level of the street and traffic-order network."""

    OSM_BOOTSTRAP = "osm_bootstrap"
    AUTHORITATIVE_CONFLATED = "authoritative_conflated"


class TurnModelMode(StrEnum):
    """Whether legal movements are represented as edge or turn state."""

    EDGE_ONLY = "edge_only"
    TURN_EXPANDED = "turn_expanded"


class PlanStage(StrEnum):
    """Progressive, non-skippable assurance stages for a generated plan."""

    MODEL_ONLY = "model_only"
    TOPOLOGY_CANDIDATE = "topology_candidate"
    DATA_QUALIFIED = "data_qualified"
    BASELINE_CALIBRATED = "baseline_calibrated"
    IMPACT_VALIDATED = "impact_validated"
    ENGINEERING_REVIEWED = "engineering_reviewed"
    APPROVED_FOR_PILOT = "approved_for_pilot"
    APPROVED_FOR_PERMANENT_WORKS = "approved_for_permanent_works"


class ApprovalType(StrEnum):
    """Independent artifacts that can advance regulated plan stages."""

    TRANSPORT_ENGINEERING = "transport_engineering"
    SITE_INSPECTION = "site_inspection"
    ACCESSIBILITY_REVIEW = "accessibility_review"
    TRANSIT_OPERATOR_REVIEW = "transit_operator_review"
    EMERGENCY_SERVICES_REVIEW = "emergency_services_review"
    ROAD_MANAGER_APPROVAL = "road_manager_approval"
    TRANSPORT_AUTHORITY_APPROVAL = "transport_authority_approval"
    ENVIRONMENTAL_SCREENING = "environmental_screening"
    PUBLIC_CONSULTATION_RECORD = "public_consultation_record"
    PILOT_MONITORING_PLAN = "pilot_monitoring_plan"
    PILOT_EVALUATION = "pilot_evaluation"
    PERMANENT_WORKS_APPROVAL = "permanent_works_approval"


class DatasetAuthority(StrEnum):
    """Provenance class for an input dataset."""

    OPEN_COMMUNITY = "open_community"
    OPERATOR = "operator"
    ROAD_AUTHORITY = "road_authority"
    STATUTORY = "statutory"
    SURVEYED = "surveyed"
    DERIVED = "derived"


class AnalysisAlgorithm(StrEnum):
    """Implemented candidate-detection algorithms."""

    CENTRALITY_BASED = "centrality_based"


class BoundingBox(BaseModel):
    """Geographic bounding box."""

    north: float = Field(..., ge=-90, le=90)
    south: float = Field(..., ge=-90, le=90)
    east: float = Field(..., ge=-180, le=180)
    west: float = Field(..., ge=-180, le=180)

    @model_validator(mode="after")
    def validate_order(self) -> "BoundingBox":
        if self.north <= self.south:
            raise ValueError("north must be greater than south")
        if self.east <= self.west:
            raise ValueError("east must be greater than west")
        return self


class Coordinates(BaseModel):
    """Geographic coordinates."""

    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class AdministrativeBoundary(BaseModel):
    """Validated GeoJSON administrative analysis boundary."""

    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list[Any]

    @model_validator(mode="after")
    def validate_geometry(self) -> "AdministrativeBoundary":
        def coordinate_count(value: Any) -> int:
            if isinstance(value, list):
                if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
                    return 1
                return sum(coordinate_count(item) for item in value)
            return 0

        if coordinate_count(self.coordinates) > 250_000:
            raise ValueError("boundary exceeds the 250,000-coordinate safety limit")
        try:
            geometry = shape(self.model_dump())
        except (TypeError, ValueError) as exc:
            raise ValueError("boundary must be valid GeoJSON") from exc
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("boundary must be a non-empty valid Polygon or MultiPolygon")
        min_x, min_y, max_x, max_y = geometry.bounds
        if not (-180 <= min_x <= max_x <= 180 and -90 <= min_y <= max_y <= 90):
            raise ValueError("boundary coordinates must be valid WGS84 longitude/latitude")
        return self


class SearchResult(BaseModel):
    """City/place search result."""

    place_id: int
    osm_type: str
    osm_id: int
    display_name: str
    lat: float
    lon: float
    boundingbox: BoundingBox
    boundary: AdministrativeBoundary | None = None
    boundary_source: Literal["nominatim", "bounding_box_fallback"] = "bounding_box_fallback"
    type: str
    importance: float


class SearchResponse(BaseModel):
    """Response for search endpoint."""

    results: list[SearchResult]


class StreetNetworkRequest(BaseModel):
    """Request for street network data."""

    bbox: BoundingBox
    network_type: NetworkType = Field(default=NetworkType.DRIVE)


class RoadSegment(BaseModel):
    """A single road segment with properties."""

    osm_id: int
    name: str | None = None
    road_type: str
    lanes: int = 1
    oneway: bool = False
    maxspeed: int | None = None
    length_m: float
    capacity: int  # vehicles per hour
    estimated_load: float  # 0-1 load factor


class StreetNetworkResponse(BaseModel):
    """Response containing street network as GeoJSON."""

    type: str = "FeatureCollection"
    features: list[dict]
    metadata: dict


class SuperblockCandidate(BaseModel):
    """A potential superblock area."""

    id: str
    geometry: dict  # GeoJSON polygon
    area_hectares: float
    perimeter_roads: list[int]  # OSM way IDs
    interior_roads: list[int]
    score: float  # 0-100, higher is better candidate
    algorithm: str  # which detection algorithm found it


class AnalysisRequest(BaseModel):
    """Request for superblock analysis."""

    bbox: BoundingBox
    algorithms: list[AnalysisAlgorithm] = Field(
        default_factory=lambda: [AnalysisAlgorithm.CENTRALITY_BASED],
        min_length=1,
    )
    min_area_hectares: float = Field(default=4.0, ge=1.0)
    max_area_hectares: float = Field(default=25.0, le=100.0)
    boundary_road_types: list[RoadType] = Field(
        default_factory=lambda: [
            RoadType.PRIMARY,
            RoadType.SECONDARY,
            RoadType.TERTIARY,
        ],
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_area_range(self) -> "AnalysisRequest":
        if self.min_area_hectares > self.max_area_hectares:
            raise ValueError("min_area_hectares must not exceed max_area_hectares")
        return self


class AnalysisResponse(BaseModel):
    """Response for superblock analysis."""

    candidates: list[dict[str, Any]]
    total_found: int
    bbox: BoundingBox
    network_stats: dict[str, Any]
    parameters: dict[str, Any]


# =============================================================================
# Superblock Partitioning Models (New System)
# =============================================================================


class ModificationType(StrEnum):
    """Types of street modifications to enforce superblock constraints."""

    MODAL_FILTER = "modal_filter"  # Block cars, allow bikes/emergency
    ONE_WAY = "one_way"  # Convert to one-way street
    TWO_WAY = "two_way"  # Open the missing direction for local access
    TURN_RESTRICTION = "turn_restriction"  # Block specific turns
    FULL_CLOSURE = "full_closure"  # Street cut / close to all vehicle traffic


class EntryPoint(BaseModel):
    """An entry/exit point into a superblock from the arterial network."""

    node_id: int  # OSMnx node ID
    sector: int  # Which angular sector (0 to num_sectors-1)
    coordinates: Coordinates
    boundary_road_id: int  # Connected arterial road OSM ID
    access_type: str = "vehicle"  # 'vehicle', 'bicycle', 'pedestrian', 'all'


class TransitRoute(BaseModel):
    """One OSM public transport route relation and the ways it runs on."""

    relation_id: int
    mode: Literal["bus", "trolleybus", "tram", "minibus", "share_taxi"]
    ref: str | None = Field(default=None, max_length=50)
    name: str | None = Field(default=None, max_length=300)
    way_ids: list[int] = Field(default_factory=list, max_length=20_000)


class ContextFacility(BaseModel):
    """A point of interest that shapes access design."""

    osm_type: Literal["node", "way", "relation"]
    osm_id: int
    kind: Literal["fire_station", "ambulance_station", "school", "kindergarten"]
    name: str | None = Field(default=None, max_length=300)
    coordinates: Coordinates


class TransitStop(BaseModel):
    """A public transport stop or station entrance people walk to."""

    stop_id: str = Field(..., min_length=1, max_length=100)
    mode: Literal["bus", "tram", "rail", "subway", "ferry", "other"] = "other"
    name: str | None = Field(default=None, max_length=300)
    coordinates: Coordinates


class OsmContext(BaseModel):
    """Transit and facility layers loaded with the street network."""

    source: str = Field(default="OpenStreetMap contributors (ODbL)", max_length=300)
    transit_routes: list[TransitRoute] = Field(default_factory=list, max_length=10_000)
    facilities: list[ContextFacility] = Field(default_factory=list, max_length=50_000)
    transit_stops: list[TransitStop] = Field(default_factory=list, max_length=100_000)
    stop_source: str | None = Field(default=None, max_length=300)
    limitations: list[str] = Field(default_factory=list)

    def transit_way_ids(self) -> set[int]:
        return {way_id for route in self.transit_routes for way_id in route.way_ids}


class StreetModification(BaseModel):
    """A modification to a street segment to enforce superblock constraints."""

    u: int  # Source node ID
    v: int  # Target node ID
    key: int = 0  # Edge key for multigraph
    osm_id: int
    name: str | None = None
    modification_type: ModificationType
    direction: str | None = None  # For one-way: 'u_to_v' or 'v_to_u'
    filter_location: Coordinates | None = None  # For point-based interventions
    rationale: str = ""
    allowed_modes: list[TransportMode] = Field(default_factory=list)
    allowed_vehicle_classes: list[VehicleClass] = Field(default_factory=list)
    time_windows: list[str] = Field(default_factory=list)
    emergency_override_required: bool = False
    construction_treatment: str | None = Field(default=None, max_length=300)
    estimated_capital_cost: float | None = Field(default=None, ge=0)
    annual_maintenance_cost: float | None = Field(default=None, ge=0)
    turn_from_osm_id: int | None = Field(default=None, gt=0)
    turn_to_osm_id: int | None = Field(default=None, gt=0)
    via_node_id: int | None = None
    filter_type: (
        Literal["planter", "emergency_bollard", "camera_gate", "bus_gate", "school_street"] | None
    ) = None
    transit_route_refs: list[str] = Field(default_factory=list, max_length=200)


class TurnRestriction(BaseModel):
    """A legal movement rule between two directed ways at a junction."""

    restriction_id: str = Field(..., min_length=1, max_length=200)
    from_osm_id: int = Field(..., gt=0)
    via_node_id: int
    to_osm_id: int = Field(..., gt=0)
    kind: Literal["no_turn", "only_turn"]
    descriptor: str | None = Field(default=None, max_length=200)
    applies_to: list[VehicleClass] = Field(default_factory=lambda: [VehicleClass.ALL_MOTOR])
    except_classes: list[VehicleClass] = Field(default_factory=list)
    time_windows: list[str] = Field(default_factory=list)
    source: str = Field(..., min_length=1, max_length=300)
    source_reference: str | None = Field(default=None, max_length=1000)


class AreaExclusion(BaseModel):
    """A surveyed and justified area intentionally outside cell generation."""

    exclusion_id: str = Field(..., min_length=1, max_length=200)
    geometry: AdministrativeBoundary
    category: Literal[
        "motorway_corridor",
        "railway",
        "water",
        "airport",
        "industrial_site",
        "park_open_space",
        "inaccessible_terrain",
        "private_network",
        "insufficient_evidence",
        "other",
    ]
    reason: str = Field(..., min_length=10, max_length=1000)
    source: str = Field(..., min_length=1, max_length=300)
    source_reference: str | None = Field(default=None, max_length=1000)
    reviewed: bool = False


class AuthoritativeRoadSegment(BaseModel):
    """Versioned road-authority inventory segment used to enrich an OSM edge."""

    stable_id: str = Field(..., min_length=1, max_length=300)
    geometry: dict[str, Any]
    source: str = Field(..., min_length=1, max_length=300)
    rule_version: str = Field(..., min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=300)
    lanes_forward: int | None = Field(default=None, ge=0, le=32)
    lanes_backward: int | None = Field(default=None, ge=0, le=32)
    speed_limit_kph: float | None = Field(default=None, ge=0, le=250)
    width_m: float | None = Field(default=None, ge=0, le=100)
    grade_percent: float | None = Field(default=None, ge=-100, le=100)
    clearance_m: float | None = Field(default=None, ge=0, le=50)
    capacity_vph: float | None = Field(default=None, ge=0, le=100_000)
    signal_controller_id: str | None = Field(default=None, max_length=300)
    curb_rule_ids: list[str] = Field(default_factory=list)
    permitted_vehicle_classes: list[VehicleClass] = Field(
        default_factory=lambda: [VehicleClass.ALL_MOTOR]
    )
    owner: str | None = Field(default=None, max_length=300)
    road_manager: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_linestring(self) -> "AuthoritativeRoadSegment":
        try:
            geometry = shape(self.geometry)
        except (TypeError, ValueError) as exc:
            raise ValueError("authoritative road geometry must be valid GeoJSON") from exc
        if geometry.geom_type != "LineString" or geometry.is_empty or not geometry.is_valid:
            raise ValueError("authoritative road geometry must be a valid LineString")
        return self


class ConflationQualityReport(BaseModel):
    """Stable-ID match coverage and ambiguities for one network snapshot."""

    source: str
    rule_version: str
    total_graph_road_length_km: float
    matched_graph_road_length_km: float
    authoritative_network_coverage_percent: float
    lane_coverage_percent: float
    signal_coverage_percent: float
    matched_stable_ids: list[str] = Field(default_factory=list)
    unmatched_stable_ids: list[str] = Field(default_factory=list)
    unmatched_osm_ids: list[int] = Field(default_factory=list)
    ambiguous_matches: dict[str, list[str]] = Field(default_factory=dict)


class OperationalRequirement(BaseModel):
    """A measurable service route that a plan must preserve."""

    requirement_id: str = Field(..., min_length=1, max_length=200)
    service_type: Literal[
        "emergency_response",
        "transit_operation",
        "freight_delivery",
        "refuse_collection",
        "permit_access",
    ]
    vehicle_class: VehicleClass
    origin: Coordinates
    destination: Coordinates
    maximum_travel_time_minutes: float | None = Field(default=None, gt=0, le=1_440)
    maximum_detour_percent: float = Field(default=10.0, ge=0, le=1_000)
    minimum_clearance_m: float | None = Field(default=None, gt=0, le=20)
    time_window: str | None = Field(default=None, max_length=100)
    source: str = Field(..., min_length=1, max_length=300)
    source_reference: str | None = Field(default=None, max_length=1000)
    hard_constraint: bool = True


class OperationalValidationResult(BaseModel):
    """Baseline-versus-plan validation for one essential service route."""

    requirement_id: str
    service_type: str
    vehicle_class: VehicleClass
    passed: bool
    baseline_travel_time_minutes: float | None = None
    planned_travel_time_minutes: float | None = None
    detour_percent: float | None = None
    clearance_passed: bool | None = None
    blockers: list[str] = Field(default_factory=list)


class OperationalValidationSummary(BaseModel):
    """Independent result set for transit, emergency, freight, and service access."""

    validated: bool = False
    all_hard_constraints_passed: bool = False
    results: list[OperationalValidationResult] = Field(default_factory=list)
    missing_service_types: list[str] = Field(default_factory=list)


class ActiveModeRequirement(BaseModel):
    """A walking, wheeling, or cycling connection the plan must preserve."""

    requirement_id: str = Field(..., min_length=1, max_length=200)
    mode: Literal["walk", "wheel", "bicycle"]
    origin: Coordinates
    destination: Coordinates
    maximum_distance_detour_percent: float = Field(default=5.0, ge=0, le=1_000)
    minimum_clear_width_m: float | None = Field(default=None, gt=0, le=20)
    maximum_grade_percent: float | None = Field(default=None, ge=0, le=100)
    maximum_level_of_traffic_stress: int | None = Field(default=None, ge=1, le=4)
    source: str = Field(..., min_length=1, max_length=300)
    source_reference: str | None = Field(default=None, max_length=1000)
    hard_constraint: bool = True


class ActiveModeValidationResult(BaseModel):
    """Baseline-versus-plan validation for one active-mode connection."""

    requirement_id: str
    mode: str
    passed: bool
    baseline_distance_m: float | None = None
    planned_distance_m: float | None = None
    detour_percent: float | None = None
    geometry_attributes_passed: bool | None = None
    blockers: list[str] = Field(default_factory=list)


class ActiveModeValidationSummary(BaseModel):
    """Results for walking, wheeling, and cycling continuity."""

    validated: bool = False
    all_hard_constraints_passed: bool = False
    results: list[ActiveModeValidationResult] = Field(default_factory=list)
    missing_modes: list[str] = Field(default_factory=list)


class UnreachableAddress(BaseModel):
    """Legacy graph-node reachability result retained for API compatibility."""

    node_id: int
    coordinates: Coordinates
    nearest_entry_sector: int
    reason: str


class AccessTarget(BaseModel):
    """Address, parcel, building, or service access point to preserve."""

    id: str = Field(..., min_length=1, max_length=200)
    coordinates: Coordinates
    kind: Literal["address", "parcel", "building", "emergency", "delivery"]
    label: str | None = Field(default=None, max_length=300)
    source: str = Field(..., min_length=1, max_length=300)
    entrance_id: str | None = Field(default=None, max_length=200)
    permitted_vehicle_classes: list[VehicleClass] = Field(
        default_factory=lambda: [VehicleClass.ALL_MOTOR]
    )
    service_windows: list[str] = Field(default_factory=list)
    minimum_clearance_m: float | None = Field(default=None, ge=0, le=20)
    maximum_detour_minutes: float | None = Field(default=None, ge=0, le=240)
    critical: bool = False
    surveyed_at: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def specialize_default_vehicle_class(self) -> "AccessTarget":
        if self.permitted_vehicle_classes == [VehicleClass.ALL_MOTOR]:
            if self.kind == "emergency":
                self.permitted_vehicle_classes = [VehicleClass.EMERGENCY]
            elif self.kind == "delivery":
                self.permitted_vehicle_classes = [VehicleClass.LIGHT_GOODS]
        return self


class UnreachableAccessTarget(BaseModel):
    """An explicit access target that has no modeled vehicle path from an entry."""

    target_id: str
    target_kind: str
    label: str | None = None
    coordinates: Coordinates
    snapped_node_id: int | None = None
    nearest_entry_sector: int | None = None
    failed_vehicle_classes: list[VehicleClass] = Field(default_factory=list)
    reason: str


class TrafficObservation(BaseModel):
    """Time-, direction-, lane-, and class-specific transport observation."""

    osm_id: int = Field(..., gt=0)
    volume_vph: int = Field(..., ge=0, le=100_000)
    source: str = Field(..., min_length=1, max_length=300)
    observed_at: str | None = Field(default=None, max_length=100)
    observation_id: str | None = Field(default=None, max_length=200)
    interval_start: str | None = Field(default=None, max_length=100)
    interval_end: str | None = Field(default=None, max_length=100)
    direction: Literal["u_to_v", "v_to_u", "both"] = "both"
    lane: int | None = Field(default=None, ge=1, le=32)
    vehicle_class: VehicleClass = VehicleClass.ALL_MOTOR
    turn_from_osm_id: int | None = Field(default=None, gt=0)
    turn_to_osm_id: int | None = Field(default=None, gt=0)
    speed_kph: float | None = Field(default=None, ge=0, le=250)
    occupancy_percent: float | None = Field(default=None, ge=0, le=100)
    queue_length_m: float | None = Field(default=None, ge=0, le=100_000)
    confidence: float = Field(default=1.0, ge=0, le=1)
    dataset_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_turn(self) -> "TrafficObservation":
        if (self.turn_from_osm_id is None) != (self.turn_to_osm_id is None):
            raise ValueError("turn observations require both turn_from_osm_id and turn_to_osm_id")
        return self

    def identity_key(self) -> tuple[Any, ...]:
        """Return the dimensions that identify one measured value."""
        return (
            self.osm_id,
            self.interval_start or self.observed_at,
            self.interval_end,
            self.direction,
            self.lane,
            self.vehicle_class,
            self.turn_from_osm_id,
            self.turn_to_osm_id,
        )


class DemandZone(BaseModel):
    """A functional-area demand zone and its network connectors."""

    zone_id: str = Field(..., min_length=1, max_length=200)
    centroid: Coordinates
    connector_node_ids: list[int] = Field(default_factory=list, max_length=100)
    name: str | None = Field(default=None, max_length=300)
    external: bool = False


class DemandTimeSlice(BaseModel):
    """One assignment period with a defined duration."""

    time_slice_id: str = Field(..., min_length=1, max_length=100)
    start_time: str = Field(..., min_length=1, max_length=100)
    end_time: str = Field(..., min_length=1, max_length=100)
    duration_minutes: int = Field(..., ge=1, le=1_440)


class DemandMatrixEntry(BaseModel):
    """Trips between two zones for one time and vehicle class."""

    origin_zone_id: str
    destination_zone_id: str
    time_slice_id: str
    vehicle_class: VehicleClass = VehicleClass.CAR
    trips: float = Field(..., ge=0, le=100_000_000)


class DemandModel(BaseModel):
    """Versioned functional-area motor demand used for network assignment."""

    model_id: str = Field(..., min_length=1, max_length=200)
    version: str = Field(..., min_length=1, max_length=200)
    source: str = Field(..., min_length=1, max_length=300)
    source_reference: str | None = Field(default=None, max_length=1000)
    zones: list[DemandZone] = Field(..., min_length=2, max_length=100_000)
    time_slices: list[DemandTimeSlice] = Field(..., min_length=1, max_length=1_000)
    matrix: list[DemandMatrixEntry] = Field(..., min_length=1, max_length=5_000_000)

    @model_validator(mode="after")
    def validate_references(self) -> "DemandModel":
        zone_ids = [zone.zone_id for zone in self.zones]
        if len(zone_ids) != len(set(zone_ids)):
            raise ValueError("demand zone IDs must be unique")
        time_slice_ids = [time_slice.time_slice_id for time_slice in self.time_slices]
        if len(time_slice_ids) != len(set(time_slice_ids)):
            raise ValueError("demand time-slice IDs must be unique")
        zone_id_set = set(zone_ids)
        time_slice_id_set = set(time_slice_ids)
        for entry in self.matrix:
            if entry.origin_zone_id not in zone_id_set:
                raise ValueError(f"unknown origin zone {entry.origin_zone_id}")
            if entry.destination_zone_id not in zone_id_set:
                raise ValueError(f"unknown destination zone {entry.destination_zone_id}")
            if entry.time_slice_id not in time_slice_id_set:
                raise ValueError(f"unknown demand time slice {entry.time_slice_id}")
        return self


class AssignmentSettings(BaseModel):
    """Transparent convergence and impact thresholds for assignment."""

    max_iterations: int = Field(default=20, ge=1, le=500)
    convergence_gap: float = Field(default=0.01, gt=0, le=1)
    bpr_alpha: float = Field(default=0.15, ge=0, le=10)
    bpr_beta: float = Field(default=4.0, ge=1, le=20)
    passenger_car_equivalents: dict[VehicleClass, float] = Field(
        default_factory=lambda: {
            VehicleClass.CAR: 1.0,
            VehicleClass.MOTORCYCLE: 0.5,
            VehicleClass.LIGHT_GOODS: 1.2,
            VehicleClass.HEAVY_GOODS: 2.5,
            VehicleClass.BUS: 2.5,
            VehicleClass.TAXI: 1.0,
            VehicleClass.REFUSE: 2.5,
            VehicleClass.EMERGENCY: 1.0,
            VehicleClass.PERMIT: 1.0,
            VehicleClass.ALL_MOTOR: 1.0,
        }
    )
    maximum_boundary_volume_capacity_ratio: float = Field(default=0.90, gt=0, le=5)
    maximum_adjacent_road_volume_change_percent: float = Field(default=15.0, ge=0, le=1_000)
    random_seed: int = 42


class AssignedEdgeFlow(BaseModel):
    """One directed edge result for a time slice and vehicle class."""

    u: int
    v: int
    key: int
    osm_id: int
    time_slice_id: str
    vehicle_class: VehicleClass
    volume: float = Field(ge=0)
    capacity_vph: float = Field(gt=0)
    volume_capacity_ratio: float = Field(ge=0)
    free_flow_time_seconds: float = Field(ge=0)
    assigned_time_seconds: float = Field(ge=0)
    estimated_queue_length_m: float = Field(ge=0)
    is_boundary_road: bool = False


class AssignmentRun(BaseModel):
    """Converged network assignment for one network state."""

    run_id: str
    network_state: Literal["baseline", "planned"]
    converged: bool
    iterations: int
    relative_gap: float = Field(ge=0)
    total_demand: float = Field(ge=0)
    assigned_demand: float = Field(ge=0)
    blocked_demand: float = Field(ge=0)
    edge_flows: list[AssignedEdgeFlow] = Field(default_factory=list)


class AssignmentComparison(BaseModel):
    """Baseline-versus-plan demand reassignment and border impact."""

    validated: bool = False
    baseline: AssignmentRun | None = None
    planned: AssignmentRun | None = None
    maximum_boundary_volume_capacity_ratio: float | None = None
    maximum_adjacent_road_volume_change_percent: float | None = None
    boundary_capacity_passed: bool = False
    spillover_passed: bool = False
    all_demand_assigned: bool = False
    limitations: list[str] = Field(default_factory=list)


class FilterDesignSettings(BaseModel):
    """How generated filters are built and who may pass them."""

    bus_gates_on_transit_routes: bool = True
    emergency_passable_filters: bool = True


class FilterDesignSummary(BaseModel):
    """What the filter design step did with transit and emergency access."""

    transit_routes_loaded: int = Field(default=0, ge=0)
    transit_ways_loaded: int = Field(default=0, ge=0)
    facilities_loaded: int = Field(default=0, ge=0)
    bus_gates: int = Field(default=0, ge=0)
    emergency_bollards: int = Field(default=0, ge=0)
    planters: int = Field(default=0, ge=0)
    closures_converted_to_gates: int = Field(default=0, ge=0)
    one_way_changes_on_transit: int = Field(default=0, ge=0)
    protected_route_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class EmergencyAccessResult(BaseModel):
    """Worst-case response time into one superblock."""

    superblock_id: str
    nearest_station: str | None = None
    baseline_seconds: float = Field(ge=0)
    planned_seconds: float | None = Field(default=None, ge=0)
    planned_without_exemption_seconds: float | None = Field(default=None, ge=0)


class EmergencyAccessSummary(BaseModel):
    """Fire and ambulance response before and after the plan."""

    checked: bool = False
    stations_used: int = Field(default=0, ge=0)
    superblocks_checked: int = Field(default=0, ge=0)
    median_added_seconds: float = 0.0
    maximum_added_seconds: float = 0.0
    threshold_seconds: float = Field(default=60.0, ge=0)
    superblocks_over_threshold: int = Field(default=0, ge=0)
    superblocks_over_threshold_without_exemption: int = Field(default=0, ge=0)
    unreachable_superblocks: int = Field(default=0, ge=0)
    results: list[EmergencyAccessResult] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PublicSpaceSettings(BaseModel):
    """Assumptions for the reclaimed public space estimate."""

    reallocation_share: float = Field(default=0.3, ge=0, le=1)
    max_plazas_per_superblock: int = Field(default=3, ge=0, le=50)


class SuperblockPublicSpace(BaseModel):
    """Street space one superblock calms and could give back."""

    superblock_id: str
    calmed_street_km: float = Field(ge=0)
    calmed_carriageway_m2: float = Field(ge=0)
    reclaimable_m2: float = Field(ge=0)
    plaza_candidates: list[Coordinates] = Field(default_factory=list)


class PublicSpaceSummary(BaseModel):
    """Citywide calmed streets and reclaimable space."""

    settings: PublicSpaceSettings = Field(default_factory=PublicSpaceSettings)
    calmed_street_km: float = 0.0
    calmed_carriageway_m2: float = 0.0
    reclaimable_m2: float = 0.0
    plaza_candidates: int = 0
    superblocks: list[SuperblockPublicSpace] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class SchoolStreetSettings(BaseModel):
    """How school and kindergarten frontages are matched and treated."""

    max_frontage_distance_m: float = Field(default=120.0, gt=0, le=500)
    school_time_windows: list[str] = Field(
        default_factory=lambda: ["Mo-Fr 07:30-08:30", "Mo-Fr 15:30-16:30"], max_length=10
    )
    kindergarten_time_windows: list[str] = Field(
        default_factory=lambda: ["Mo-Fr 07:00-08:30", "Mo-Fr 16:00-17:30"], max_length=10
    )


class SchoolStreet(BaseModel):
    """The street in front of one school, and what the plan proposes there."""

    facility_osm_type: Literal["node", "way", "relation"]
    facility_osm_id: int
    kind: Literal["school", "kindergarten"]
    name: str | None = Field(default=None, max_length=300)
    coordinates: Coordinates
    setting: Literal["interior", "boundary", "unassigned"]
    superblock_id: str | None = None
    street_name: str | None = Field(default=None, max_length=300)
    osm_id: int | None = None
    u: int | None = None
    v: int | None = None
    key: int | None = None
    distance_m: float = Field(default=0.0, ge=0)
    street_path: list[Coordinates] = Field(default_factory=list, max_length=2000)
    on_transit_route: bool = False
    time_windows: list[str] = Field(default_factory=list, max_length=10)
    treatment: str = Field(default="", max_length=600)


class SchoolStreetSummary(BaseModel):
    """Timed school streets inside superblocks and main-road school frontages."""

    settings: SchoolStreetSettings = Field(default_factory=SchoolStreetSettings)
    facilities_loaded: int = 0
    interior_school_streets: int = 0
    boundary_frontages: int = 0
    unassigned: int = 0
    schools: list[SchoolStreet] = Field(default_factory=list, max_length=20_000)
    limitations: list[str] = Field(default_factory=list)


class PilotCandidate(BaseModel):
    """One superblock ranked for a reversible trial."""

    superblock_id: str
    rank: int = Field(default=0, ge=0)
    score: float = Field(ge=0)
    trial_measures: int = Field(ge=0)
    trial_cost_eur: float = Field(ge=0)
    build_cost_eur: float = Field(ge=0)
    displaced_peak_vph: float = Field(ge=0)
    interior_injury_reduction_low_percent: float
    interior_injury_reduction_high_percent: float
    school_streets: int = Field(default=0, ge=0)
    reclaimable_m2: float = Field(default=0.0, ge=0)
    mean_detour_seconds: float = Field(default=0.0, ge=0)
    emergency_delay_seconds: float | None = Field(default=None, ge=0)
    residents: float | None = Field(default=None, ge=0)
    centroid_lat: float | None = None
    centroid_lon: float | None = None


class PilotPlanSummary(BaseModel):
    """Where to start: superblocks ranked for a trial-then-build rollout."""

    total_trial_cost_eur: float = Field(default=0.0, ge=0)
    total_build_cost_eur: float = Field(default=0.0, ge=0)
    top_trial_cost_eur: float = Field(default=0.0, ge=0)
    mean_injury_reduction_low_percent: float = 0.0
    mean_injury_reduction_high_percent: float = 0.0
    candidates: list[PilotCandidate] = Field(default_factory=list, max_length=1000)
    limitations: list[str] = Field(default_factory=list)


class HealthSettings(BaseModel):
    """Assumptions behind the noise and NO2 screening estimate."""

    speed_noise_reduction_db_low: float = Field(default=1.0, ge=0, le=10)
    speed_noise_reduction_db_high: float = Field(default=3.0, ge=0, le=10)
    local_traffic_no2_share_low: float = Field(default=0.3, ge=0, le=1)
    local_traffic_no2_share_high: float = Field(default=0.6, ge=0, le=1)


class SuperblockHealth(BaseModel):
    """Estimated interior noise and NO2 change for one superblock (negative is better)."""

    superblock_id: str
    interior_noise_change_db_low: float
    interior_noise_change_db_high: float
    interior_no2_change_percent_low: float
    interior_no2_change_percent_high: float


class SuperblockPopulation(BaseModel):
    """Residents counted inside one superblock from a population grid."""

    superblock_id: str
    residents: float = Field(..., ge=0)
    residents_per_ha: float = Field(..., ge=0)


class PopulationSummary(BaseModel):
    """How many people the plan's superblocks serve."""

    source: str = Field(..., max_length=300)
    residents_in_area: float = Field(default=0.0, ge=0)
    residents_in_superblocks: float = Field(default=0.0, ge=0)
    share_in_superblocks: float = Field(default=0.0, ge=0, le=1)
    superblocks: list[SuperblockPopulation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class SuperblockTransitAccess(BaseModel):
    """How well one superblock's streets reach public transport on foot."""

    superblock_id: str
    covered_share: float = Field(..., ge=0, le=1)
    worst_walk_m: float | None = Field(default=None, ge=0)
    stops_nearby: int = Field(default=0, ge=0)
    nearest_stop: str | None = Field(default=None, max_length=300)


class TransitAccessSummary(BaseModel):
    """Walking access from superblock interiors to stops on and around them.

    Superblocks move cars to the boundary roads, so they work when people can
    walk to a stop there instead. Poorly served ones need a stop added or
    better crossings before a trial.
    """

    checked: bool = False
    source: str | None = Field(default=None, max_length=300)
    stops_used: int = Field(default=0, ge=0)
    walk_threshold_m: float = Field(default=400.0, gt=0)
    mean_covered_share: float = Field(default=0.0, ge=0, le=1)
    superblocks_poorly_served: int = Field(default=0, ge=0)
    residents_poorly_served: float | None = Field(default=None, ge=0)
    superblocks: list[SuperblockTransitAccess] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class HealthEstimate(BaseModel):
    """Screening estimate of noise and NO2 changes inside and around superblocks."""

    method: str = "traffic_energy_and_share_scaling"
    settings: HealthSettings = Field(default_factory=HealthSettings)
    mean_interior_noise_change_db_low: float = 0.0
    mean_interior_noise_change_db_high: float = 0.0
    mean_interior_no2_change_percent_low: float = 0.0
    mean_interior_no2_change_percent_high: float = 0.0
    boundary_roads_noticeably_louder: int = Field(default=0, ge=0)
    maximum_boundary_noise_increase_db: float = Field(default=0.0, ge=0)
    superblocks: list[SuperblockHealth] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class EvaporationScenario(BaseModel):
    """Share of displaced through traffic assumed to disappear rather than reroute."""

    name: str = Field(..., min_length=1, max_length=50)
    evaporation_rate: float = Field(..., ge=0, le=1)
    description: str = Field(default="", max_length=300)


class TrafficImpactSettings(BaseModel):
    """Assumptions for the zero-data boundary-road impact screening."""

    through_traffic_share: float = Field(default=0.5, ge=0, le=1)
    peak_hour_share_of_daily: float = Field(default=0.1, gt=0, le=1)
    max_entry_pairs_per_superblock: int = Field(default=200, ge=1, le=5_000)
    scenarios: list[EvaporationScenario] = Field(
        default_factory=lambda: [
            EvaporationScenario(
                name="pessimistic",
                evaporation_rate=0.0,
                description="Every displaced through trip reroutes onto the boundary roads.",
            ),
            EvaporationScenario(
                name="central",
                evaporation_rate=0.15,
                description="Some trips change time, mode or destination.",
            ),
            EvaporationScenario(
                name="optimistic",
                evaporation_rate=0.4,
                description="Close to the traffic evaporation seen in London LTN studies.",
            ),
        ],
        min_length=1,
        max_length=10,
    )
    central_scenario: str = "central"
    high_increase_percent: float = Field(default=10.0, ge=0, le=1_000)


class BoundaryRoadImpact(BaseModel):
    """Predicted extra peak-hour load on one boundary road (OSM way)."""

    osm_id: int
    name: str | None = None
    highway: str = "unclassified"
    baseline_peak_vph: float = Field(ge=0)
    capacity_vph: float = Field(gt=0)
    added_peak_vph: dict[str, float] = Field(default_factory=dict)
    percent_increase_central: float = Field(ge=0)
    baseline_volume_capacity_ratio: float = Field(ge=0)
    planned_volume_capacity_ratio_central: float = Field(ge=0)
    measured: bool = False
    arterial: bool = True
    superblock_ids: list[str] = Field(default_factory=list)


class SuperblockTrafficImpact(BaseModel):
    """Through traffic removed from one superblock interior."""

    superblock_id: str
    entry_pairs_checked: int = Field(ge=0)
    cut_through_pairs: int = Field(ge=0)
    unresolved_pairs: int = Field(ge=0)
    displaced_peak_vph: float = Field(ge=0)
    interior_traffic_reduction_percent: float = Field(ge=0, le=100)
    mean_detour_seconds: float = Field(ge=0)


class TrafficImpactEstimate(BaseModel):
    """Screening prediction of where displaced through traffic goes."""

    method: str = "local_entry_pair_rerouting"
    settings: TrafficImpactSettings = Field(default_factory=TrafficImpactSettings)
    traffic_basis: Literal["modeled_topology", "measured_volume", "mixed"] = "modeled_topology"
    total_displaced_peak_vph: float = Field(default=0.0, ge=0)
    added_boundary_vehicle_km_peak: dict[str, float] = Field(default_factory=dict)
    interior_vehicle_km_removed_peak: float = Field(default=0.0, ge=0)
    maximum_percent_increase_central: float = Field(default=0.0, ge=0)
    roads_over_threshold: int = Field(default=0, ge=0)
    roads_over_capacity_central: int = Field(default=0, ge=0)
    minor_roads_loaded: int = Field(default=0, ge=0)
    boundary_roads: list[BoundaryRoadImpact] = Field(default_factory=list)
    superblocks: list[SuperblockTrafficImpact] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class SimulationStatus(StrEnum):
    """Lifecycle states for an external microscopic simulation."""

    NOT_REQUESTED = "not_requested"
    TOOL_UNAVAILABLE = "tool_unavailable"
    EXPORTED = "exported"
    COMPLETED = "completed"
    FAILED = "failed"


class SimulationRun(BaseModel):
    """Traceable SUMO export or execution result."""

    simulation_id: str
    status: SimulationStatus
    engine: Literal["sumo"] = "sumo"
    engine_version: str | None = None
    network_state: Literal["baseline", "planned"]
    random_seed: int
    output_directory: str | None = None
    generated_files: list[str] = Field(default_factory=list)
    command: list[str] = Field(default_factory=list)
    return_code: int | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None
    limitations: list[str] = Field(default_factory=list)


class ObjectiveName(StrEnum):
    """Comparable objectives used to rank feasible plan alternatives."""

    VEHICLE_DELAY = "vehicle_delay"
    BOUNDARY_SATURATION = "boundary_saturation"
    ADJACENT_SPILLOVER = "adjacent_spillover"
    BLOCKED_DEMAND = "blocked_demand"
    TRANSIT_DELAY = "transit_delay"
    EMERGENCY_DELAY = "emergency_delay"
    ACTIVE_MODE_DETOUR = "active_mode_detour"
    EMISSIONS = "emissions"
    SAFETY_RISK = "safety_risk"
    EQUITY_BURDEN = "equity_burden"
    INTERVENTION_COUNT = "intervention_count"
    CAPITAL_COST = "capital_cost"
    ANNUAL_MAINTENANCE_COST = "annual_maintenance_cost"


class ScenarioMetric(BaseModel):
    """A traceable baseline-versus-plan metric used in optimization."""

    objective: ObjectiveName
    baseline_value: float | None = None
    planned_value: float
    unit: str
    lower_is_better: bool = True
    source: str
    validated: bool = False
    uncertainty_low: float | None = None
    uncertainty_high: float | None = None


class ScenarioCandidate(BaseModel):
    """One fully specified and evaluated intervention alternative."""

    scenario_id: str
    name: str
    description: str = ""
    modifications: list[StreetModification]
    metrics: list[ScenarioMetric] = Field(default_factory=list)
    hard_constraint_failures: list[str] = Field(default_factory=list)
    feasible: bool = False
    weighted_score: float | None = None
    pareto_rank: int | None = None
    assignment_comparison: AssignmentComparison = Field(default_factory=AssignmentComparison)
    operational_validation: OperationalValidationSummary = Field(
        default_factory=OperationalValidationSummary
    )
    active_mode_validation: ActiveModeValidationSummary = Field(
        default_factory=ActiveModeValidationSummary
    )


class OptimizationSettings(BaseModel):
    """Explicit hard limits and user-visible objective weights."""

    objective_weights: dict[ObjectiveName, float] = Field(
        default_factory=lambda: {
            ObjectiveName.VEHICLE_DELAY: 1.0,
            ObjectiveName.BOUNDARY_SATURATION: 3.0,
            ObjectiveName.ADJACENT_SPILLOVER: 3.0,
            ObjectiveName.BLOCKED_DEMAND: 10.0,
            ObjectiveName.INTERVENTION_COUNT: 0.5,
            ObjectiveName.CAPITAL_COST: 0.5,
            ObjectiveName.ANNUAL_MAINTENANCE_COST: 0.25,
        }
    )
    capital_budget: float | None = Field(default=None, ge=0)
    annual_maintenance_budget: float | None = Field(default=None, ge=0)
    maximum_interventions: int | None = Field(default=None, ge=0)
    require_operational_validation: bool = True
    require_active_mode_validation: bool = True


class ScenarioPortfolio(BaseModel):
    """Evaluated candidates and the non-dominated feasible alternatives."""

    portfolio_id: str
    candidates: list[ScenarioCandidate]
    pareto_scenario_ids: list[str]
    objective_weights: dict[ObjectiveName, float]
    recommended_scenario_id: str | None = None
    limitations: list[str] = Field(default_factory=list)


class BillOfQuantityItem(BaseModel):
    """Quantity and cost line in a draft engineering package."""

    item_code: str
    description: str
    quantity: float = Field(gt=0)
    unit: str
    unit_cost: float = Field(ge=0)
    total_cost: float = Field(ge=0)
    cost_basis: str


class EngineeringInstruction(BaseModel):
    """Machine-compiled draft instruction for one street intervention."""

    instruction_id: str
    osm_id: int
    street_name: str
    coordinates: Coordinates | None = None
    affected_movement: str
    treatment: str
    signs_and_markings: list[str] = Field(default_factory=list)
    allowed_modes: list[TransportMode] = Field(default_factory=list)
    allowed_vehicle_classes: list[VehicleClass] = Field(default_factory=list)
    time_windows: list[str] = Field(default_factory=list)
    emergency_operation: str | None = None
    construction_notes: list[str] = Field(default_factory=list)
    inspection_checks: list[str] = Field(default_factory=list)
    capital_cost: float
    annual_maintenance_cost: float
    field_verification_required: bool = True


class EngineeringPackage(BaseModel):
    """Draft, non-approved implementation package bound to a plan digest."""

    package_id: str
    plan_id: str
    status: Literal["draft_for_engineering_review"] = "draft_for_engineering_review"
    jurisdiction: str
    currency: str = "EUR"
    instructions: list[EngineeringInstruction]
    bill_of_quantities: list[BillOfQuantityItem]
    total_capital_cost: float
    total_annual_maintenance_cost: float
    gis_feature_collection: dict
    blockers: list[str] = Field(default_factory=list)
    disclaimer: str = "Machine-generated draft. Exact geometry, traffic orders, quantities, utilities, drainage, visibility, swept paths, accessibility, and temporary traffic management require professional field verification and approval."


class MonitoringMetricDefinition(BaseModel):
    """Pre-registered pilot metric with explicit adapt and rollback thresholds."""

    metric_id: str = Field(..., min_length=1, max_length=200)
    name: str
    unit: str
    direction: Literal["increase_is_harm", "decrease_is_harm", "absolute_change"]
    baseline_value: float
    adapt_threshold_percent: float = Field(ge=0)
    rollback_threshold_percent: float = Field(ge=0)
    minimum_samples: int = Field(default=1, ge=1)
    comparison_location_id: str | None = None
    source: str

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "MonitoringMetricDefinition":
        if self.rollback_threshold_percent < self.adapt_threshold_percent:
            raise ValueError("rollback threshold must be at least the adapt threshold")
        return self


class MonitoringObservation(BaseModel):
    """One measured post-installation monitoring value."""

    metric_id: str
    observed_at: str
    value: float
    sample_count: int = Field(default=1, ge=1)
    source_reference: str
    comparison_value: float | None = None


class MonitoringEvaluation(BaseModel):
    """Deterministic pilot action recommendation from pre-registered rules."""

    plan_id: str
    evaluated_at: str
    action: Literal["continue", "adapt", "rollback", "insufficient_data"]
    metric_changes_percent: dict[str, float] = Field(default_factory=dict)
    triggered_metric_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class DatasetManifest(BaseModel):
    """Immutable provenance and fitness metadata for one source snapshot."""

    dataset_id: str = Field(..., min_length=1, max_length=200)
    name: str = Field(..., min_length=1, max_length=300)
    category: Literal[
        "road_network",
        "traffic",
        "demand",
        "turns_signals",
        "access",
        "transit",
        "emergency",
        "freight_curb",
        "active_modes",
        "crashes_safety",
        "environment",
        "demographics_equity",
        "planning_constraints",
    ]
    source_organization: str = Field(..., min_length=1, max_length=300)
    authority: DatasetAuthority
    version: str = Field(..., min_length=1, max_length=200)
    captured_at: str = Field(..., min_length=4, max_length=100)
    checksum_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    source_url: str | None = Field(default=None, max_length=1000)
    license: str | None = Field(default=None, max_length=300)
    spatial_coverage: str | None = Field(default=None, max_length=500)
    temporal_start: str | None = Field(default=None, max_length=100)
    temporal_end: str | None = Field(default=None, max_length=100)
    stable_id_namespace: str | None = Field(default=None, max_length=300)
    update_sla_hours: int | None = Field(default=None, ge=1, le=100_000)
    complete: bool = False
    quality_score: float = Field(default=0.0, ge=0, le=1)
    limitations: list[str] = Field(default_factory=list)


class DataQualityMetrics(BaseModel):
    """Coverage measures used by the data-qualified gate."""

    eligible_road_coverage_percent: float = Field(default=0.0, ge=0, le=100)
    authoritative_network_coverage_percent: float = Field(default=0.0, ge=0, le=100)
    lane_coverage_percent: float = Field(default=0.0, ge=0, le=100)
    turn_coverage_percent: float = Field(default=0.0, ge=0, le=100)
    signal_coverage_percent: float = Field(default=0.0, ge=0, le=100)
    directional_traffic_coverage_percent: float = Field(default=0.0, ge=0, le=100)
    peak_period_coverage_percent: float = Field(default=0.0, ge=0, le=100)
    turning_movement_coverage_percent: float = Field(default=0.0, ge=0, le=100)
    access_entrance_coverage_percent: float = Field(default=0.0, ge=0, le=100)
    transit_coverage_percent: float = Field(default=0.0, ge=0, le=100)
    emergency_coverage_percent: float = Field(default=0.0, ge=0, le=100)


class CalibrationSummary(BaseModel):
    """Independent baseline calibration and validation evidence."""

    validated: bool = False
    model_version: str | None = Field(default=None, max_length=200)
    calibration_dataset_ids: list[str] = Field(default_factory=list)
    holdout_dataset_ids: list[str] = Field(default_factory=list)
    geh_under_5_percent: float = Field(default=0.0, ge=0, le=100)
    speed_mape_percent: float | None = Field(default=None, ge=0, le=1000)
    travel_time_mape_percent: float | None = Field(default=None, ge=0, le=1000)
    stochastic_runs: int = Field(default=0, ge=0, le=100_000)
    confidence_level: float = Field(default=0.95, ge=0.5, lt=1)
    limitations: list[str] = Field(default_factory=list)


class ImpactValidationSummary(BaseModel):
    """Scenario-wide operational, service, and assurance checks."""

    validated: bool = False
    scenario_id: str | None = Field(default=None, max_length=200)
    boundary_capacity_passed: bool = False
    spillover_passed: bool = False
    transit_passed: bool = False
    emergency_passed: bool = False
    freight_passed: bool = False
    accessibility_passed: bool = False
    active_modes_passed: bool = False
    safety_passed: bool = False
    environment_passed: bool = False
    equity_passed: bool = False
    uncertainty_passed: bool = False
    maximum_volume_capacity_ratio: float | None = Field(default=None, ge=0)
    maximum_queue_length_m: float | None = Field(default=None, ge=0)
    maximum_adjacent_road_volume_change_percent: float | None = None
    limitations: list[str] = Field(default_factory=list)

    def all_required_checks_passed(self) -> bool:
        return self.validated and all(
            (
                self.boundary_capacity_passed,
                self.spillover_passed,
                self.transit_passed,
                self.emergency_passed,
                self.freight_passed,
                self.accessibility_passed,
                self.active_modes_passed,
                self.safety_passed,
                self.environment_passed,
                self.equity_passed,
                self.uncertainty_passed,
            )
        )


class ApprovalArtifact(BaseModel):
    """Cryptographically verifiable approval bound to an immutable plan."""

    artifact_id: str = Field(..., min_length=8, max_length=200)
    plan_id: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    approval_type: ApprovalType
    reviewer_id: str = Field(..., min_length=2, max_length=200)
    reviewer_role: str = Field(..., min_length=2, max_length=200)
    organization: str = Field(..., min_length=2, max_length=200)
    issued_at: str = Field(..., min_length=4, max_length=100)
    expires_at: str | None = Field(default=None, max_length=100)
    reference: str = Field(..., min_length=1, max_length=1000)
    content_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    signature: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class AssuranceEvidenceType(StrEnum):
    """Signed analytical evidence that can advance model-validation gates."""

    DATA_QUALITY = "data_quality"
    BASELINE_CALIBRATION = "baseline_calibration"
    IMPACT_VALIDATION = "impact_validation"


class AssuranceEvidenceArtifact(BaseModel):
    """Trusted analytical result bound to one immutable intervention plan."""

    artifact_id: str = Field(..., min_length=8, max_length=200)
    plan_id: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    evidence_type: AssuranceEvidenceType
    payload: dict[str, Any]
    payload_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    validator_id: str = Field(..., min_length=2, max_length=200)
    validator_role: str = Field(..., min_length=2, max_length=200)
    organization: str = Field(..., min_length=2, max_length=200)
    issued_at: str = Field(..., min_length=4, max_length=100)
    model_version: str = Field(..., min_length=1, max_length=200)
    dataset_ids: list[str] = Field(..., min_length=1, max_length=1_000)
    reference: str = Field(..., min_length=1, max_length=1_000)
    signature: str = Field(..., pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_payload_shape(self) -> "AssuranceEvidenceArtifact":
        if self.evidence_type == AssuranceEvidenceType.DATA_QUALITY:
            DataQualityMetrics.model_validate(self.payload)
        elif self.evidence_type == AssuranceEvidenceType.BASELINE_CALIBRATION:
            CalibrationSummary.model_validate(self.payload)
        else:
            ImpactValidationSummary.model_validate(self.payload)
        return self


class ReviewAttestation(BaseModel):
    """Deprecated unsigned review record retained only for migration errors."""

    plan_id: str = Field(..., min_length=16, max_length=100)
    review_type: Literal["transport_engineering", "site_inspection"]
    reviewer: str = Field(..., min_length=2, max_length=200)
    organization: str = Field(..., min_length=2, max_length=200)
    reviewed_at: str = Field(..., min_length=4, max_length=100)
    reference: str = Field(..., min_length=1, max_length=500)


class AnalysisEvidence(BaseModel):
    """Machine-readable provenance for the plan's evidence inputs."""

    boundary_mode: Literal["administrative_polygon", "bounding_box_fallback"]
    traffic_mode: Literal["measured_volume", "modeled_topology"]
    traffic_observation_count: int = 0
    measured_edge_coverage_percent: float = 0.0
    traffic_sources: list[str] = Field(default_factory=list)
    access_mode: Literal["authoritative_targets", "explicit_targets", "not_supplied"]
    access_target_count: int = 0
    access_dataset_source: str | None = None
    access_dataset_complete: bool = False
    study_area_mode: StudyAreaMode = StudyAreaMode.ADMINISTRATIVE_POLYGON
    network_authority_mode: NetworkAuthorityMode = NetworkAuthorityMode.OSM_BOOTSTRAP
    turn_model_mode: TurnModelMode = TurnModelMode.EDGE_ONLY
    dataset_manifests: list[DatasetManifest] = Field(default_factory=list)
    data_quality: DataQualityMetrics = Field(default_factory=DataQualityMetrics)
    excluded_area_percent: float = Field(default=0.0, ge=0, le=100)
    baseline_calibration: CalibrationSummary | None = None
    impact_validation: ImpactValidationSummary | None = None


class ReadinessGate(BaseModel):
    """One progressive gate and the evidence still missing from it."""

    stage: PlanStage
    passed: bool
    blockers: list[str] = Field(default_factory=list)


class PlanReadiness(BaseModel):
    """Progressive assurance status; no stage can skip a failed predecessor."""

    status: PlanStage = PlanStage.MODEL_ONLY
    highest_completed_stage: PlanStage = PlanStage.MODEL_ONLY
    ready_for_engineering_review: bool = False
    approved_for_pilot: bool = False
    approved_for_permanent_works: bool = False
    implementation_ready: bool = False
    modeled_directional_validation_passed: bool
    transport_engineering_reviewed: bool
    site_inspection_reviewed: bool
    verified_approval_types: list[ApprovalType] = Field(default_factory=list)
    gates: list[ReadinessGate] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class EnforcedSuperblock(BaseModel):
    """A superblock with enforced enter-exit constraints."""

    id: str
    geometry: dict  # GeoJSON polygon
    area_hectares: float
    num_sectors: int  # Number of angular sectors (typically 4-8)

    # Boundary information
    boundary_roads: list[int]  # OSM IDs of arterial boundary roads
    entry_points: list[EntryPoint]

    # Interior network modifications
    modifications: list[StreetModification]

    # Validation results
    constraint_validated: bool  # True if no cross-sector paths exist
    all_addresses_reachable: bool | None = None
    unreachable_addresses: list[UnreachableAddress] = Field(default_factory=list)
    modeled_directional_validation_passed: bool = False
    turn_aware_validation_passed: bool = False
    vehicle_class_validation: dict[str, bool] = Field(default_factory=dict)
    access_target_count: int = 0
    all_access_targets_reachable: bool | None = None
    unreachable_access_targets: list[UnreachableAccessTarget] = Field(default_factory=list)

    # Metrics
    interior_roads_count: int
    modal_filter_count: int
    one_way_conversion_count: int
    street_cut_count: int
    two_way_conversion_count: int = 0


class CityPartition(BaseModel):
    """Complete partitioning of a city into superblocks."""

    superblocks: list[EnforcedSuperblock]
    plan_id: str = ""
    arterial_network: list[int]  # Edge OSM IDs forming the arterial grid
    bbox: BoundingBox
    boundary: AdministrativeBoundary | None = None
    approval_artifacts: list[ApprovalArtifact] = Field(default_factory=list)
    evidence_artifacts: list[AssuranceEvidenceArtifact] = Field(default_factory=list)
    turn_restrictions: list[TurnRestriction] = Field(default_factory=list)
    area_exclusions: list[AreaExclusion] = Field(default_factory=list)
    operational_validation: OperationalValidationSummary = Field(
        default_factory=OperationalValidationSummary
    )
    active_mode_validation: ActiveModeValidationSummary = Field(
        default_factory=ActiveModeValidationSummary
    )
    assignment_comparison: AssignmentComparison = Field(default_factory=AssignmentComparison)
    traffic_impact: TrafficImpactEstimate | None = None
    filter_design: FilterDesignSummary = Field(default_factory=FilterDesignSummary)
    emergency_access: EmergencyAccessSummary = Field(default_factory=EmergencyAccessSummary)
    public_space: PublicSpaceSummary | None = None
    school_streets: SchoolStreetSummary | None = None
    pilot_plan: PilotPlanSummary | None = None
    health: HealthEstimate | None = None
    transit_access: TransitAccessSummary | None = None
    population: PopulationSummary | None = None
    osm_context: OsmContext | None = None
    evidence: AnalysisEvidence = Field(
        default_factory=lambda: AnalysisEvidence(
            boundary_mode="bounding_box_fallback",
            traffic_mode="modeled_topology",
            access_mode="not_supplied",
        )
    )
    readiness: PlanReadiness = Field(
        default_factory=lambda: PlanReadiness(
            modeled_directional_validation_passed=False,
            transport_engineering_reviewed=False,
            site_inspection_reviewed=False,
            blockers=["Topology candidate gate has not passed"],
        )
    )

    # Statistics
    total_area_hectares: float
    coverage_percent: float  # % of bbox area covered by superblocks
    total_superblocks: int
    total_modal_filters: int
    total_one_way_conversions: int
    total_street_cuts: int
    total_unreachable_addresses: int
    total_unreachable_access_targets: int = 0
    total_two_way_conversions: int = 0
    eligible_road_length_km: float = 0.0
    accounted_road_length_km: float = 0.0
    eligible_road_coverage_percent: float = 0.0
    unaccounted_road_osm_ids: list[int] = Field(default_factory=list)


class PartitionRequest(BaseModel):
    """Request for city partitioning into superblocks."""

    bbox: BoundingBox
    boundary: AdministrativeBoundary | None = None
    traffic_observations: list[TrafficObservation] = Field(default_factory=list, max_length=100_000)
    access_targets: list[AccessTarget] = Field(default_factory=list, max_length=500_000)
    access_dataset_source: str | None = Field(default=None, max_length=300)
    access_dataset_complete: bool = False
    study_area_mode: StudyAreaMode = StudyAreaMode.ADMINISTRATIVE_POLYGON
    network_authority_mode: NetworkAuthorityMode = NetworkAuthorityMode.OSM_BOOTSTRAP
    turn_model_mode: TurnModelMode = TurnModelMode.EDGE_ONLY
    dataset_manifests: list[DatasetManifest] = Field(default_factory=list, max_length=1_000)
    data_quality: DataQualityMetrics = Field(default_factory=DataQualityMetrics)
    authoritative_road_segments: list[AuthoritativeRoadSegment] = Field(
        default_factory=list,
        max_length=1_000_000,
    )
    excluded_area_percent: float = Field(default=0.0, ge=0, le=100)
    baseline_calibration: CalibrationSummary | None = None
    impact_validation: ImpactValidationSummary | None = None
    turn_restrictions: list[TurnRestriction] = Field(default_factory=list, max_length=500_000)
    area_exclusions: list[AreaExclusion] = Field(default_factory=list, max_length=100_000)
    operational_requirements: list[OperationalRequirement] = Field(
        default_factory=list,
        max_length=100_000,
    )
    active_mode_requirements: list[ActiveModeRequirement] = Field(
        default_factory=list,
        max_length=100_000,
    )
    demand_model: DemandModel | None = None
    assignment_settings: AssignmentSettings = Field(default_factory=AssignmentSettings)
    traffic_impact_settings: TrafficImpactSettings = Field(default_factory=TrafficImpactSettings)
    filter_design_settings: FilterDesignSettings = Field(default_factory=FilterDesignSettings)
    public_space_settings: PublicSpaceSettings = Field(default_factory=PublicSpaceSettings)
    school_street_settings: SchoolStreetSettings = Field(default_factory=SchoolStreetSettings)
    # "avoid" keeps busy residential and living streets inside cells instead of
    # promoting them to boundary roads on centrality alone.
    minor_road_boundaries: Literal["allow", "avoid"] = "allow"
    osm_context: OsmContext | None = None
    fetch_osm_context: bool = True
    # "circulation_sectors" is Ghent's circulation plan: a few large sectors
    # bounded only by the main road grid, crossed by car only via that grid.
    planning_mode: Literal["superblocks", "circulation_sectors"] = "superblocks"
    target_size_hectares: float = Field(default=12.0, ge=4.0, le=400.0)
    min_area_hectares: float = Field(default=6.0, ge=1.0)
    max_area_hectares: float = Field(default=20.0, le=800.0)
    enforce_constraints: Literal[True] = True
    num_sectors: int = Field(default=4, ge=3, le=8)  # Angular sectors per superblock
    arterial_road_types: list[RoadType] = Field(
        default_factory=lambda: [
            RoadType.PRIMARY,
            RoadType.SECONDARY,
            RoadType.TERTIARY,
        ],
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_area_range(self) -> "PartitionRequest":
        if self.planning_mode == "superblocks" and (
            self.target_size_hectares > 50 or self.max_area_hectares > 100
        ):
            raise ValueError(
                "superblock plans allow at most 50 ha target and 100 ha maximum; "
                "use planning_mode='circulation_sectors' for larger sectors"
            )
        if self.min_area_hectares > self.max_area_hectares:
            raise ValueError("min_area_hectares must not exceed max_area_hectares")
        if not self.min_area_hectares <= self.target_size_hectares <= self.max_area_hectares:
            raise ValueError(
                "target_size_hectares must be between min_area_hectares and max_area_hectares"
            )
        if self.access_dataset_complete and not self.access_targets:
            raise ValueError("a complete access dataset must contain at least one access target")
        if self.access_dataset_complete and not (self.access_dataset_source or "").strip():
            raise ValueError("a complete access dataset must name its authoritative source")
        observation_keys = [observation.identity_key() for observation in self.traffic_observations]
        if len(observation_keys) != len(set(observation_keys)):
            raise ValueError(
                "traffic observations must be unique by way, time, direction, lane, class, and turn"
            )
        access_target_ids = [target.id for target in self.access_targets]
        if len(access_target_ids) != len(set(access_target_ids)):
            raise ValueError("access targets must have unique IDs")
        dataset_ids = [manifest.dataset_id for manifest in self.dataset_manifests]
        if len(dataset_ids) != len(set(dataset_ids)):
            raise ValueError("dataset manifests must have unique dataset IDs")
        if self.boundary is not None:
            min_x, min_y, max_x, max_y = shape(self.boundary.model_dump()).bounds
            tolerance = 1e-6
            if (
                min_x < self.bbox.west - tolerance
                or max_x > self.bbox.east + tolerance
                or min_y < self.bbox.south - tolerance
                or max_y > self.bbox.north + tolerance
            ):
                raise ValueError("boundary must be contained by the supplied bounding box")
        return self


class PartitionResponse(BaseModel):
    """Response for city partitioning."""

    partition: CityPartition
    street_network: StreetNetworkResponse
    processing_time_seconds: float


class PartitionReviewRequest(BaseModel):
    """Signed approval artifacts bound to an immutable plan ID."""

    partition: CityPartition
    approval_artifacts: list[ApprovalArtifact] = Field(..., min_length=1, max_length=50)
    review_attestations: list[ReviewAttestation] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_reviews(self) -> "PartitionReviewRequest":
        if self.review_attestations:
            raise ValueError(
                "unsigned review_attestations are no longer accepted; submit signed approval_artifacts"
            )
        if any(artifact.plan_id != self.partition.plan_id for artifact in self.approval_artifacts):
            raise ValueError("every approval artifact must reference the supplied plan ID")
        artifact_ids = [artifact.artifact_id for artifact in self.approval_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("approval artifact IDs must be unique")
        return self


class PartitionEvidenceRequest(BaseModel):
    """Signed calibration or impact artifacts for an existing plan."""

    partition: CityPartition
    evidence_artifacts: list[AssuranceEvidenceArtifact] = Field(
        ...,
        min_length=1,
        max_length=50,
    )

    @model_validator(mode="after")
    def validate_evidence(self) -> "PartitionEvidenceRequest":
        if any(artifact.plan_id != self.partition.plan_id for artifact in self.evidence_artifacts):
            raise ValueError("every evidence artifact must reference the supplied plan ID")
        artifact_ids = [artifact.artifact_id for artifact in self.evidence_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("evidence artifact IDs must be unique")
        return self


# =============================================================================
# Routing Models
# =============================================================================


class RouteRequest(BaseModel):
    """Request for superblock-aware routing."""

    origin: Coordinates
    destination: Coordinates
    respect_superblocks: bool = True  # If false, route ignores constraints
    prefer_arterials: bool = True  # Prefer arterial roads even within same superblock
    partition: CityPartition | None = Field(
        default=None,
        description="Partition to route against; avoids process-local routing state.",
    )


class RouteSegment(BaseModel):
    """A segment of a computed route."""

    coordinates: list[Coordinates]
    road_type: str
    is_arterial: bool
    superblock_id: str | None = None  # If inside a superblock
    length_m: float


class RouteResult(BaseModel):
    """Result of superblock-aware routing."""

    success: bool
    segments: list[RouteSegment] = Field(default_factory=list)
    total_distance_km: float = 0.0
    estimated_time_min: float = 0.0
    arterial_percent: float = 0.0  # % of route on arterials
    superblocks_traversed: list[str] = Field(default_factory=list)  # IDs of superblocks entered
    blocked_reason: str | None = None
    alternative_available: bool = False


# =============================================================================
# Validation Models
# =============================================================================


class ConstraintViolation(BaseModel):
    """A detected constraint violation in a superblock."""

    from_entry: EntryPoint
    to_entry: EntryPoint
    path_exists: bool
    path_edges: list[tuple[int, int]] = Field(default_factory=list)  # Edge sequence if path exists


class ValidationRequest(BaseModel):
    """Request to validate superblock constraints."""

    superblock_id: str
    test_all_pairs: bool = True  # Test all entry point pairs


class ValidationResult(BaseModel):
    """Result of superblock constraint validation."""

    superblock_id: str
    is_valid: bool
    violations: list[ConstraintViolation] = Field(default_factory=list)
    total_entry_pairs_tested: int
    reachability_percent: float  # % of interior nodes reachable from some entry


# =============================================================================
# Progress Streaming Models
# =============================================================================


class PartitionProgress(BaseModel):
    """Progress update during city partitioning."""

    stage: str  # 'network', 'arterials', 'cells', 'constraints', 'validation', 'complete'
    percent: int
    message: str
    current_superblock: int | None = None
    total_superblocks: int | None = None
