"""
Graph-based superblock detection algorithm.

Finds areas bounded by major roads that could be converted to superblocks.
"""
import networkx as nx
import osmnx as ox
from shapely.geometry import Polygon, MultiPolygon, mapping
from shapely.ops import polygonize, unary_union
import uuid
from typing import Any

from app.models.schemas import BoundingBox, SuperblockCandidate


# Road types that can form superblock boundaries
BOUNDARY_ROAD_TYPES = {
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
}

# Minimum hierarchy level for boundary roads (lower = more major)
MIN_BOUNDARY_HIERARCHY = 5  # tertiary and above


def detect_superblocks(
    G: nx.MultiDiGraph,
    min_area_hectares: float = 4.0,
    max_area_hectares: float = 25.0,
    boundary_hierarchy: int = 5,
) -> list[SuperblockCandidate]:
    """
    Detect potential superblock areas in a street network graph.

    Algorithm:
    1. Extract edges that are major roads (boundary candidates)
    2. Create polygons from the enclosed areas
    3. Filter by size and score by suitability

    Args:
        G: OSMnx street network graph
        min_area_hectares: Minimum superblock area
        max_area_hectares: Maximum superblock area
        boundary_hierarchy: Maximum hierarchy level for boundary roads (1-10)

    Returns:
        List of SuperblockCandidate objects
    """
    # Convert graph to GeoDataFrames
    nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True)

    if edges.empty:
        return []

    # Get the CRS for area calculations
    edges_projected = edges.to_crs(epsg=3857)  # Web Mercator for area calc

    # Extract boundary road geometries (major roads)
    boundary_edges = []
    interior_edges = []

    for idx, row in edges.iterrows():
        highway = row.get("highway", "unclassified")
        if isinstance(highway, list):
            highway = highway[0]

        hierarchy = get_hierarchy(highway)

        if hierarchy <= boundary_hierarchy:
            boundary_edges.append(row.geometry)
        else:
            interior_edges.append(row.geometry)

    if not boundary_edges:
        return []

    # Create polygons from boundary roads
    boundary_union = unary_union(boundary_edges)
    polygons = list(polygonize(boundary_union))

    if not polygons:
        return []

    # Filter and score polygons
    candidates = []

    for poly in polygons:
        if not poly.is_valid:
            poly = poly.buffer(0)

        if poly.is_empty:
            continue

        # Calculate area in hectares
        # Project to Web Mercator for accurate area calculation
        from shapely.ops import transform
        import pyproj

        project = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:3857", always_xy=True
        ).transform
        poly_projected = transform(project, poly)
        area_m2 = poly_projected.area
        area_hectares = area_m2 / 10000

        # Filter by size
        if area_hectares < min_area_hectares or area_hectares > max_area_hectares:
            continue

        # Find interior and perimeter roads
        perimeter_osmids = []
        interior_osmids = []

        for idx, row in edges.iterrows():
            if row.geometry.intersects(poly.boundary):
                osmid = row.get("osmid", idx)
                if isinstance(osmid, list):
                    osmid = osmid[0]
                perimeter_osmids.append(osmid)
            elif row.geometry.within(poly) or poly.contains(row.geometry.centroid):
                osmid = row.get("osmid", idx)
                if isinstance(osmid, list):
                    osmid = osmid[0]
                interior_osmids.append(osmid)

        # Calculate score (0-100)
        score = calculate_superblock_score(
            area_hectares=area_hectares,
            num_interior_roads=len(interior_osmids),
            num_perimeter_roads=len(perimeter_osmids),
            polygon=poly,
        )

        candidates.append(SuperblockCandidate(
            id=str(uuid.uuid4())[:8],
            geometry=mapping(poly),
            area_hectares=round(area_hectares, 2),
            perimeter_roads=perimeter_osmids[:20],  # Limit for response size
            interior_roads=interior_osmids[:50],
            score=round(score, 1),
            algorithm="graph",
        ))

    # Sort by score descending
    candidates.sort(key=lambda c: c.score, reverse=True)

    return candidates[:50]  # Return top 50 candidates


def get_hierarchy(highway: str) -> int:
    """Get hierarchy value for road type."""
    hierarchy_map = {
        "motorway": 1, "motorway_link": 1,
        "trunk": 2, "trunk_link": 2,
        "primary": 3, "primary_link": 3,
        "secondary": 4, "secondary_link": 4,
        "tertiary": 5, "tertiary_link": 5,
        "residential": 6,
        "living_street": 7,
        "unclassified": 8,
        "service": 9,
        "pedestrian": 10,
    }
    return hierarchy_map.get(highway, 99)


def calculate_superblock_score(
    area_hectares: float,
    num_interior_roads: int,
    num_perimeter_roads: int,
    polygon: Polygon,
) -> float:
    """
    Calculate a suitability score for a superblock candidate.

    Factors:
    - Ideal size (around 9-16 hectares, like Barcelona)
    - Good ratio of perimeter to interior roads
    - Compact shape (closer to square/circle)
    """
    score = 50.0  # Base score

    # Size score: ideal is 9-16 hectares (Barcelona style)
    if 9 <= area_hectares <= 16:
        score += 20
    elif 6 <= area_hectares <= 20:
        score += 10
    elif area_hectares < 4 or area_hectares > 25:
        score -= 10

    # Interior roads score: fewer is better (more pedestrianizable)
    if num_interior_roads == 0:
        score += 15
    elif num_interior_roads <= 5:
        score += 10
    elif num_interior_roads <= 10:
        score += 5
    elif num_interior_roads > 20:
        score -= 10

    # Perimeter score: need enough boundary roads
    if num_perimeter_roads >= 4:
        score += 10
    elif num_perimeter_roads < 2:
        score -= 15

    # Compactness score (isoperimetric quotient)
    # 1.0 = perfect circle, lower = more irregular
    if polygon.is_valid and polygon.area > 0:
        compactness = 4 * 3.14159 * polygon.area / (polygon.length ** 2)
        if compactness > 0.7:
            score += 10
        elif compactness > 0.5:
            score += 5
        elif compactness < 0.3:
            score -= 5

    return max(0, min(100, score))


async def analyze_area(
    bbox: BoundingBox,
    min_area: float = 4.0,
    max_area: float = 25.0,
) -> list[SuperblockCandidate]:
    """
    Analyze a bounding box for potential superblocks.

    Args:
        bbox: Area to analyze
        min_area: Minimum superblock area in hectares
        max_area: Maximum superblock area in hectares

    Returns:
        List of superblock candidates
    """
    # Fetch street network
    bbox_tuple = (bbox.west, bbox.south, bbox.east, bbox.north)

    G = ox.graph_from_bbox(
        bbox=bbox_tuple,
        network_type="drive",
        simplify=True,
        retain_all=False,
        truncate_by_edge=True,
    )

    # Detect superblocks
    candidates = detect_superblocks(
        G=G,
        min_area_hectares=min_area,
        max_area_hectares=max_area,
    )

    return candidates
