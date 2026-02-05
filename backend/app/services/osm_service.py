import osmnx as ox
import geopandas as gpd
from shapely.geometry import box
import json
from typing import Any

from app.models.schemas import BoundingBox, StreetNetworkResponse
from app.core.config import get_settings

settings = get_settings()

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

    Args:
        bbox: Bounding box coordinates
        network_type: Type of network ('drive', 'walk', 'bike', 'all')

    Returns:
        StreetNetworkResponse with GeoJSON features
    """
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

    # Fetch the network using OSMnx
    # Note: OSMnx expects (north, south, east, west) for bbox
    G = ox.graph_from_bbox(
        north=bbox.north,
        south=bbox.south,
        east=bbox.east,
        west=bbox.west,
        network_type=network_type,
        simplify=True,
        retain_all=False,
        truncate_by_edge=True,
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
            "geometry": json.loads(row.geometry.to_json()),
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

    return StreetNetworkResponse(
        type="FeatureCollection",
        features=features,
        metadata=metadata,
    )
