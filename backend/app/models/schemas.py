from pydantic import BaseModel, Field
from typing import Optional
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
