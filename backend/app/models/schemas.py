from pydantic import BaseModel, Field
from typing import Optional, Any
from enum import Enum


class RoadType(str, Enum):
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


class BoundingBox(BaseModel):
    """Geographic bounding box."""
    north: float = Field(..., ge=-90, le=90)
    south: float = Field(..., ge=-90, le=90)
    east: float = Field(..., ge=-180, le=180)
    west: float = Field(..., ge=-180, le=180)


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
    network_type: str = Field(default="drive", description="OSMnx network type")


class RoadSegment(BaseModel):
    """A single road segment with properties."""
    osm_id: int
    name: Optional[str] = None
    road_type: str
    lanes: int = 1
    oneway: bool = False
    maxspeed: Optional[int] = None
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
    algorithms: list[str] = Field(default=["graph", "barcelona"])
    min_area_hectares: float = Field(default=4.0, ge=1.0)
    max_area_hectares: float = Field(default=25.0, le=100.0)
    boundary_road_types: list[RoadType] = Field(
        default=[RoadType.PRIMARY, RoadType.SECONDARY, RoadType.TERTIARY]
    )


class AnalysisResponse(BaseModel):
    """Response for superblock analysis."""
    candidates: list[SuperblockCandidate]
    street_network: StreetNetworkResponse
    metadata: dict


# =============================================================================
# Superblock Partitioning Models (New System)
# =============================================================================


class ModificationType(str, Enum):
    """Types of street modifications to enforce superblock constraints."""
    MODAL_FILTER = "modal_filter"  # Block cars, allow bikes/emergency
    ONE_WAY = "one_way"  # Convert to one-way street
    TURN_RESTRICTION = "turn_restriction"  # Block specific turns
    FULL_CLOSURE = "full_closure"  # Close to all vehicle traffic


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
    name: Optional[str] = None
    modification_type: ModificationType
    direction: Optional[str] = None  # For one-way: 'u_to_v' or 'v_to_u'
    filter_location: Optional[Coordinates] = None  # For modal filters
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
    unreachable_addresses: list[UnreachableAddress] = []

    # Metrics
    interior_roads_count: int
    modal_filter_count: int
    one_way_conversion_count: int


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
    total_unreachable_addresses: int


class PartitionRequest(BaseModel):
    """Request for city partitioning into superblocks."""
    bbox: BoundingBox
    target_size_hectares: float = Field(default=12.0, ge=4.0, le=50.0)
    min_area_hectares: float = Field(default=6.0, ge=1.0)
    max_area_hectares: float = Field(default=20.0, le=100.0)
    enforce_constraints: bool = True
    num_sectors: int = Field(default=4, ge=3, le=8)  # Angular sectors per superblock
    arterial_road_types: list[RoadType] = Field(
        default=[RoadType.PRIMARY, RoadType.SECONDARY, RoadType.TERTIARY]
    )


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


class RouteSegment(BaseModel):
    """A segment of a computed route."""
    coordinates: list[Coordinates]
    road_type: str
    is_arterial: bool
    superblock_id: Optional[str] = None  # If inside a superblock
    length_m: float


class RouteResult(BaseModel):
    """Result of superblock-aware routing."""
    success: bool
    segments: list[RouteSegment] = []
    total_distance_km: float = 0.0
    estimated_time_min: float = 0.0
    arterial_percent: float = 0.0  # % of route on arterials
    superblocks_traversed: list[str] = []  # IDs of superblocks entered
    blocked_reason: Optional[str] = None
    alternative_available: bool = False


# =============================================================================
# Validation Models
# =============================================================================


class ConstraintViolation(BaseModel):
    """A detected constraint violation in a superblock."""
    from_entry: EntryPoint
    to_entry: EntryPoint
    path_exists: bool
    path_edges: list[tuple[int, int]] = []  # Edge sequence if path exists


class ValidationRequest(BaseModel):
    """Request to validate superblock constraints."""
    superblock_id: str
    test_all_pairs: bool = True  # Test all entry point pairs


class ValidationResult(BaseModel):
    """Result of superblock constraint validation."""
    superblock_id: str
    is_valid: bool
    violations: list[ConstraintViolation] = []
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
    current_superblock: Optional[int] = None
    total_superblocks: Optional[int] = None
