from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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


class SearchResult(BaseModel):
    """City/place search result."""

    place_id: int
    osm_type: str
    osm_id: int
    display_name: str
    lat: float
    lon: float
    boundingbox: BoundingBox
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
    """An address that became unreachable after modifications."""

    node_id: int
    coordinates: Coordinates
    nearest_entry_sector: int
    reason: str


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
    all_addresses_reachable: bool
    unreachable_addresses: list[UnreachableAddress] = Field(default_factory=list)

    # Metrics
    interior_roads_count: int
    modal_filter_count: int
    one_way_conversion_count: int
    street_cut_count: int
    two_way_conversion_count: int = 0


class CityPartition(BaseModel):
    """Complete partitioning of a city into superblocks."""

    superblocks: list[EnforcedSuperblock]
    arterial_network: list[int]  # Edge OSM IDs forming the arterial grid
    bbox: BoundingBox

    # Statistics
    total_area_hectares: float
    coverage_percent: float  # % of bbox area covered by superblocks
    total_superblocks: int
    total_modal_filters: int
    total_one_way_conversions: int
    total_street_cuts: int
    total_unreachable_addresses: int
    total_two_way_conversions: int = 0


class PartitionRequest(BaseModel):
    """Request for city partitioning into superblocks."""

    bbox: BoundingBox
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
        return self


class PartitionResponse(BaseModel):
    """Response for city partitioning."""

    partition: CityPartition
    street_network: StreetNetworkResponse
    processing_time_seconds: float


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
