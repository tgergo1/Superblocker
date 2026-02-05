import osmnx as ox
import geopandas as gpd
from shapely.geometry import box, mapping
import json
from typing import Any
import logging
import time

from app.models.schemas import BoundingBox, StreetNetworkResponse
from app.core.config import get_settings
from app.services.cache_service import get_cache_service

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


def get_road_hierarchy_value(highway: Any) -> int:
    """Get hierarchy value for a road type."""
    if isinstance(highway, list):
        # Take the most important (lowest) value
        return min(ROAD_HIERARCHY.get(h, 99) for h in highway)
    return ROAD_HIERARCHY.get(str(highway), 99)


def normalize_highway_type(highway: Any) -> str:
    """Normalize highway type to a single string."""
    if isinstance(highway, list):
        # Return the first/primary type
        return str(highway[0]) if highway else "unclassified"
    return str(highway) if highway else "unclassified"


async def get_street_network(
    bbox: BoundingBox,
    network_type: str = "drive",
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

    # Validate bbox size to prevent huge requests
    lat_diff = bbox.north - bbox.south
    lon_diff = bbox.east - bbox.west

    if lat_diff > 0.5 or lon_diff > 0.5:
        raise ValueError(
            "Bounding box too large. Maximum size is ~50km. "
            "Please select a smaller area."
        )

    if lat_diff <= 0 or lon_diff <= 0:
        raise ValueError("Invalid bounding box: north must be > south, east must be > west")

    # Round bbox coordinates for consistent cache keys (5 decimal places ~ 1m precision)
    cache_params = {
        "north": round(bbox.north, 5),
        "south": round(bbox.south, 5),
        "east": round(bbox.east, 5),
        "west": round(bbox.west, 5),
        "network_type": network_type,
    }

    # Check cache first
    cache_service = get_cache_service()
    cached_data = cache_service.get("network", cache_params)

    if cached_data is not None:
        logger.info(
            "Street network loaded from cache (network_type=%s)",
            network_type,
        )
        return StreetNetworkResponse(
            type=cached_data["type"],
            features=cached_data["features"],
            metadata=cached_data["metadata"],
        )

    logger.info(
        "Fetching street network from OSM (network_type=%s bbox=%s)",
        network_type,
        bbox.model_dump(),
    )

    # Fetch the network using OSMnx
    # OSMnx 2.x expects bbox as tuple: (left, bottom, right, top) = (west, south, east, north)
    bbox_tuple = (bbox.west, bbox.south, bbox.east, bbox.north)
    G = ox.graph_from_bbox(
        bbox=bbox_tuple,
        network_type=network_type,
        simplify=True,
        retain_all=False,
        truncate_by_edge=True,
    )
    logger.info(
        "Street network fetched in %.1fs (nodes=%s edges=%s)",
        time.time() - start_time,
        G.number_of_nodes(),
        G.number_of_edges(),
    )

    # Convert to GeoDataFrame (edges)
    gdf_edges = ox.graph_to_gdfs(G, nodes=False, edges=True)

    # Reset index to get u, v, key as columns
    gdf_edges = gdf_edges.reset_index()

    # Build features list
    features = []
    for idx, row in gdf_edges.iterrows():
        # Extract properties
        highway = normalize_highway_type(row.get("highway", "unclassified"))

        # Get number of lanes
        lanes = row.get("lanes", 1)
        if isinstance(lanes, list):
            lanes = int(lanes[0]) if lanes else 1
        elif lanes is not None:
            try:
                lanes = int(lanes)
            except (ValueError, TypeError):
                lanes = 1
        else:
            lanes = 1

        # Get oneway status
        oneway = row.get("oneway", False)
        if isinstance(oneway, str):
            oneway = oneway.lower() in ("yes", "true", "1")

        # Get maxspeed
        maxspeed = row.get("maxspeed")
        if isinstance(maxspeed, list):
            maxspeed = maxspeed[0] if maxspeed else None
        if maxspeed:
            try:
                # Remove units if present (e.g., "50 mph")
                maxspeed = int(str(maxspeed).split()[0])
            except (ValueError, TypeError):
                maxspeed = None

        # Get length
        length = row.get("length", 0)

        # Get name
        name = row.get("name")
        if isinstance(name, list):
            name = name[0] if name else None

        # Get OSM ID
        osmid = row.get("osmid")
        if isinstance(osmid, list):
            osmid = osmid[0] if osmid else 0

        # Create GeoJSON feature
        feature = {
            "type": "Feature",
            "geometry": mapping(row.geometry),
            "properties": {
                "osmid": osmid,
                "name": name,
                "highway": highway,
                "hierarchy": get_road_hierarchy_value(highway),
                "lanes": lanes,
                "oneway": oneway,
                "maxspeed": maxspeed,
                "length_m": round(length, 2),
                "u": row.get("u"),
                "v": row.get("v"),
            }
        }
        features.append(feature)

    # Calculate metadata
    total_length = sum(f["properties"]["length_m"] for f in features)
    road_types = {}
    for f in features:
        rt = f["properties"]["highway"]
        road_types[rt] = road_types.get(rt, 0) + 1

    metadata = {
        "bbox": {
            "north": bbox.north,
            "south": bbox.south,
            "east": bbox.east,
            "west": bbox.west,
        },
        "total_edges": len(features),
        "total_length_km": round(total_length / 1000, 2),
        "road_type_counts": road_types,
        "network_type": network_type,
    }

    response = StreetNetworkResponse(
        type="FeatureCollection",
        features=features,
        metadata=metadata,
    )

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
