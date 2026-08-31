import asyncio
import hashlib
import json
import logging
import math
import re
import time
from numbers import Real
from typing import Any

import networkx as nx
import osmnx as ox
from shapely.geometry import mapping, shape

from app.core.config import get_settings
from app.models.schemas import AdministrativeBoundary, BoundingBox, StreetNetworkResponse
from app.services.cache_service import get_cache_service
from app.utils.geo import validate_bbox_size

settings = get_settings()
logger = logging.getLogger(__name__)

# Configure OSMnx
ox.settings.timeout = settings.osm_timeout
ox.settings.memory = settings.osm_memory_limit
ox.settings.use_cache = True
ox.settings.log_console = settings.debug


# Road type hierarchy for classification
ROAD_HIERARCHY = {
    "motorway": 1,
    "motorway_link": 1,
    "trunk": 2,
    "trunk_link": 2,
    "primary": 3,
    "primary_link": 3,
    "secondary": 4,
    "secondary_link": 4,
    "tertiary": 5,
    "tertiary_link": 5,
    "residential": 6,
    "living_street": 7,
    "unclassified": 8,
    "service": 9,
    "pedestrian": 10,
}


def is_missing_osm_value(value: Any) -> bool:
    """Return whether an optional OSM/Pandas value is absent or non-finite."""
    return value is None or (isinstance(value, Real) and not math.isfinite(float(value)))


def normalize_optional_text(value: Any) -> str | None:
    """Convert an optional scalar/list OSM tag into valid JSON text."""
    values = value if isinstance(value, (list, tuple, set)) else [value]
    for candidate in values:
        if is_missing_osm_value(candidate):
            continue
        text = str(candidate).strip()
        if text and text.lower() not in {"nan", "none", "null"}:
            return text
    return None


def get_road_hierarchy_value(highway: Any) -> int:
    """Get hierarchy value for a road type."""
    if isinstance(highway, (list, tuple, set)):
        # Take the most important (lowest) value
        values = [str(value) for value in highway if not is_missing_osm_value(value)]
        return min((ROAD_HIERARCHY.get(value, 99) for value in values), default=99)
    if is_missing_osm_value(highway):
        return ROAD_HIERARCHY["unclassified"]
    return ROAD_HIERARCHY.get(str(highway), 99)


def normalize_highway_type(highway: Any) -> str:
    """Normalize highway type to a single string."""
    if isinstance(highway, (list, tuple, set)):
        values = [str(value) for value in highway if not is_missing_osm_value(value)]
        if not values:
            return "unclassified"
        return min(
            values,
            key=lambda value: ROAD_HIERARCHY.get(value, 99),
        )
    return str(highway) if not is_missing_osm_value(highway) and highway else "unclassified"


def normalize_lanes(value: Any) -> int:
    """Normalize common OSM lane encodings without silently dropping values."""
    values = value if isinstance(value, (list, tuple, set)) else [value]
    parsed: list[int] = []
    for item in values:
        for token in str(item or "").split(";"):
            try:
                parsed.append(int(float(token.strip())))
            except (TypeError, ValueError):
                continue
    return max(1, min(12, max(parsed, default=1)))


def normalize_maxspeed(value: Any) -> int | None:
    """Return max speed in km/h, converting explicit mph values."""
    values = value if isinstance(value, (list, tuple, set)) else [value]
    speeds: list[float] = []
    for item in values:
        text = str(item or "").strip().lower()
        match = re.search(r"\d+(?:\.\d+)?", text)
        if not match:
            continue
        speed = float(match.group())
        if "mph" in text:
            speed *= 1.609344
        speeds.append(speed)
    return round(min(speeds)) if speeds else None


def _validate_bbox(bbox: BoundingBox) -> None:
    validate_bbox_size(
        bbox.north,
        bbox.south,
        bbox.east,
        bbox.west,
        max_span_degrees=settings.max_bbox_span_degrees,
        max_area_km2=settings.max_bbox_area_km2,
    )


def graph_to_street_network(
    graph: nx.MultiDiGraph,
    bbox: BoundingBox,
    network_type: str = "drive",
    boundary: AdministrativeBoundary | None = None,
) -> StreetNetworkResponse:
    """Convert an already-fetched graph into the public GeoJSON response."""
    gdf_edges = ox.graph_to_gdfs(graph, nodes=False, edges=True).reset_index()
    features: list[dict[str, Any]] = []

    for row in gdf_edges.itertuples(index=False):
        highway_raw = getattr(row, "highway", "unclassified")
        highway = normalize_highway_type(highway_raw)
        oneway = getattr(row, "oneway", False)
        if is_missing_osm_value(oneway):
            oneway = False
        elif isinstance(oneway, str):
            oneway = oneway.lower() in ("yes", "true", "1", "-1")

        name = normalize_optional_text(getattr(row, "name", None))
        osmid = getattr(row, "osmid", 0)
        if isinstance(osmid, (list, tuple, set)):
            osmid = next((value for value in osmid if not is_missing_osm_value(value)), 0)
        if is_missing_osm_value(osmid):
            osmid = 0

        length = getattr(row, "length", 0)
        if is_missing_osm_value(length):
            length = 0

        features.append(
            {
                "type": "Feature",
                "geometry": mapping(row.geometry),
                "properties": {
                    "osmid": int(osmid or 0),
                    "name": name,
                    "highway": highway,
                    "hierarchy": get_road_hierarchy_value(highway_raw),
                    "lanes": normalize_lanes(getattr(row, "lanes", 1)),
                    "oneway": bool(oneway),
                    "maxspeed": normalize_maxspeed(getattr(row, "maxspeed", None)),
                    "length_m": round(float(length or 0), 2),
                    "u": int(row.u),
                    "v": int(row.v),
                    "key": int(getattr(row, "key", 0)),
                },
            }
        )

    # Directed OSMnx graphs usually contain a feature in each direction. Count
    # each physical segment once for length and street-space reporting.
    physical_features: dict[tuple[int, int, int], dict[str, Any]] = {}
    for feature in features:
        props = feature["properties"]
        physical_key = (
            min(props["u"], props["v"]),
            max(props["u"], props["v"]),
            props["osmid"],
        )
        physical_features.setdefault(physical_key, feature)

    total_length = sum(feature["properties"]["length_m"] for feature in physical_features.values())
    road_types: dict[str, int] = {}
    for feature in physical_features.values():
        road_type = feature["properties"]["highway"]
        road_types[road_type] = road_types.get(road_type, 0) + 1

    return StreetNetworkResponse(
        type="FeatureCollection",
        features=features,
        metadata={
            "bbox": bbox.model_dump(),
            "total_edges": len(physical_features),
            "total_directed_edges": len(features),
            "total_length_km": round(total_length / 1000, 2),
            "road_type_counts": road_types,
            "network_type": network_type,
            "analysis_boundary": boundary.model_dump() if boundary else None,
            "boundary_mode": "administrative_polygon" if boundary else "bounding_box_fallback",
        },
    )


def _boundary_cache_token(boundary: AdministrativeBoundary | None) -> str | None:
    if boundary is None:
        return None
    payload = json.dumps(boundary.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _download_graph(
    bbox: BoundingBox,
    network_type: str,
    boundary: AdministrativeBoundary | None,
) -> nx.MultiDiGraph:
    """Download a road graph clipped by an exact polygon when available."""
    common = {
        "network_type": network_type,
        "simplify": True,
        "retain_all": False,
        "truncate_by_edge": True,
    }
    if boundary is not None:
        return ox.graph_from_polygon(shape(boundary.model_dump()), **common)
    bbox_tuple = (bbox.west, bbox.south, bbox.east, bbox.north)
    return ox.graph_from_bbox(bbox=bbox_tuple, **common)


async def get_street_network(
    bbox: BoundingBox,
    network_type: str = "drive",
    boundary: AdministrativeBoundary | None = None,
) -> StreetNetworkResponse:
    """
    Fetch street network from OSM for a bounding box.

    Uses caching to avoid re-fetching the same network data.

    Args:
        bbox: Bounding box coordinates
        network_type: Type of network ('drive', 'walk', 'bike', 'all')

    Returns:
        StreetNetworkResponse with GeoJSON features
    """
    start_time = time.time()
    network_type_value = getattr(network_type, "value", network_type)

    _validate_bbox(bbox)

    # Round bbox coordinates for consistent cache keys (5 decimal places ~ 1m precision)
    cache_params = {
        "north": round(bbox.north, 5),
        "south": round(bbox.south, 5),
        "east": round(bbox.east, 5),
        "west": round(bbox.west, 5),
        "network_type": network_type_value,
        "boundary": _boundary_cache_token(boundary),
    }

    # Check cache first
    cache_service = get_cache_service()
    cached_data = cache_service.get("network", cache_params)

    if cached_data is not None:
        logger.info(
            "Street network loaded from cache (network_type=%s)",
            network_type_value,
        )
        return StreetNetworkResponse(
            type=cached_data["type"],
            features=cached_data["features"],
            metadata=cached_data["metadata"],
        )

    logger.info(
        "Fetching street network from OSM (network_type=%s bbox=%s)",
        network_type_value,
        bbox.model_dump(),
    )

    # Fetch the network using OSMnx
    # OSMnx 2.x expects bbox as tuple: (left, bottom, right, top) = (west, south, east, north)
    G = await asyncio.to_thread(_download_graph, bbox, network_type_value, boundary)
    logger.info(
        "Street network fetched in %.1fs (nodes=%s edges=%s)",
        time.time() - start_time,
        G.number_of_nodes(),
        G.number_of_edges(),
    )

    response = graph_to_street_network(G, bbox, network_type_value, boundary)

    # Cache the result
    cache_service.set(
        "network",
        cache_params,
        {
            "type": response.type,
            "features": response.features,
            "metadata": response.metadata,
        },
        ttl_seconds=settings.cache_network_ttl_seconds,
    )

    return response


async def get_street_network_graph(
    bbox: BoundingBox,
    network_type: str = "drive",
    boundary: AdministrativeBoundary | None = None,
):
    """
    Fetch street network as a NetworkX MultiDiGraph.

    This is the raw graph used for partitioning and routing algorithms.

    Args:
        bbox: Bounding box coordinates
        network_type: Type of network ('drive', 'walk', 'bike', 'all')

    Returns:
        NetworkX MultiDiGraph of the street network
    """
    _validate_bbox(bbox)
    network_type_value = getattr(network_type, "value", network_type)

    logger.info(
        "Fetching street network graph from OSM (network_type=%s bbox=%s)",
        network_type_value,
        bbox.model_dump(),
    )

    # Fetch the network using OSMnx
    G = await asyncio.to_thread(_download_graph, bbox, network_type_value, boundary)

    logger.info(
        "Street network graph fetched (nodes=%s edges=%s)",
        G.number_of_nodes(),
        G.number_of_edges(),
    )

    return G
