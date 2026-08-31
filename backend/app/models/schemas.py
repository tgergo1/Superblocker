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


class UnreachableAccessTarget(BaseModel):
    """An explicit access target that has no modeled vehicle path from an entry."""

    target_id: str
    target_kind: str
    label: str | None = None
    coordinates: Coordinates
    snapped_node_id: int | None = None
    nearest_entry_sector: int | None = None
    reason: str


class TrafficObservation(BaseModel):
    """Measured traffic volume attached to an OSM way."""

    osm_id: int = Field(..., gt=0)
    volume_vph: int = Field(..., ge=0, le=100_000)
    source: str = Field(..., min_length=1, max_length=300)
    observed_at: str | None = Field(default=None, max_length=100)


class ReviewAttestation(BaseModel):
    """Named professional or field-review sign-off."""

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


class PlanReadiness(BaseModel):
    """Release gate separating model output from an implementation-ready plan."""

    status: Literal["model_only", "review_pending", "implementation_ready"]
    implementation_ready: bool
    modeled_directional_validation_passed: bool
    transport_engineering_reviewed: bool
    site_inspection_reviewed: bool
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
    evidence: AnalysisEvidence = Field(
        default_factory=lambda: AnalysisEvidence(
            boundary_mode="bounding_box_fallback",
            traffic_mode="modeled_topology",
            access_mode="not_supplied",
        )
    )
    readiness: PlanReadiness = Field(
        default_factory=lambda: PlanReadiness(
            status="model_only",
            implementation_ready=False,
            modeled_directional_validation_passed=False,
            transport_engineering_reviewed=False,
            site_inspection_reviewed=False,
            blockers=["Model output has not passed implementation review"],
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


class PartitionRequest(BaseModel):
    """Request for city partitioning into superblocks."""

    bbox: BoundingBox
    boundary: AdministrativeBoundary | None = None
    traffic_observations: list[TrafficObservation] = Field(default_factory=list, max_length=100_000)
    access_targets: list[AccessTarget] = Field(default_factory=list, max_length=500_000)
    access_dataset_source: str | None = Field(default=None, max_length=300)
    access_dataset_complete: bool = False
    target_size_hectares: float = Field(default=12.0, ge=4.0, le=50.0)
    min_area_hectares: float = Field(default=6.0, ge=1.0)
    max_area_hectares: float = Field(default=20.0, le=100.0)
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
        observation_ids = [observation.osm_id for observation in self.traffic_observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("traffic observations must have unique OSM way IDs")
        access_target_ids = [target.id for target in self.access_targets]
        if len(access_target_ids) != len(set(access_target_ids)):
            raise ValueError("access targets must have unique IDs")
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
    """Post-analysis professional review submission bound to an immutable plan ID."""

    partition: CityPartition
    review_attestations: list[ReviewAttestation] = Field(..., min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_reviews(self) -> "PartitionReviewRequest":
        review_types = [review.review_type for review in self.review_attestations]
        if set(review_types) != {"transport_engineering", "site_inspection"}:
            raise ValueError("both transport-engineering and site-inspection reviews are required")
        if any(review.plan_id != self.partition.plan_id for review in self.review_attestations):
            raise ValueError("every review attestation must reference the supplied plan ID")
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
