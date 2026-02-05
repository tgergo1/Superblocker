from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import polygonize
import math
from typing import Any


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on Earth.

    Args:
        lat1, lon1: First point coordinates in degrees
        lat2, lon2: Second point coordinates in degrees

    Returns:
        Distance in meters
    """
    R = 6371000  # Earth's radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def bbox_area_hectares(north: float, south: float, east: float, west: float) -> float:
    """
    Calculate approximate area of a bounding box in hectares.

    Args:
        north, south, east, west: Bounding box coordinates in degrees

    Returns:
        Area in hectares
    """
    # Calculate width and height in meters
    center_lat = (north + south) / 2
    width = haversine_distance(center_lat, west, center_lat, east)
    height = haversine_distance(south, west, north, west)

    # Area in square meters, convert to hectares
    area_m2 = width * height
    return area_m2 / 10000


def polygon_area_hectares(polygon: Polygon) -> float:
    """
    Calculate area of a polygon in hectares.

    Note: For accurate results, the polygon should be in a projected CRS.
    This function assumes WGS84 and provides an approximation.

    Args:
        polygon: Shapely Polygon object

    Returns:
        Approximate area in hectares
    """
    # Get bounding box
    minx, miny, maxx, maxy = polygon.bounds

    # Calculate approximate conversion factor at the polygon's center
    center_lat = (miny + maxy) / 2

    # Degrees to meters (approximate at this latitude)
    lat_deg_to_m = 111320  # roughly constant
    lon_deg_to_m = 111320 * math.cos(math.radians(center_lat))

    # Scale factor
    scale = lat_deg_to_m * lon_deg_to_m

    # Area in square degrees * scale = square meters
    area_deg2 = polygon.area
    area_m2 = area_deg2 * scale

    return area_m2 / 10000


def simplify_geometry(geometry: Any, tolerance: float = 0.0001) -> Any:
    """
    Simplify a geometry to reduce complexity.

    Args:
        geometry: Shapely geometry object
        tolerance: Simplification tolerance in degrees

    Returns:
        Simplified geometry
    """
    return geometry.simplify(tolerance, preserve_topology=True)


def buffer_point(lat: float, lon: float, radius_m: float) -> Polygon:
    """
    Create a circular buffer around a point.

    Args:
        lat, lon: Point coordinates in degrees
        radius_m: Buffer radius in meters

    Returns:
        Polygon representing the buffer
    """
    # Approximate degrees for the buffer
    lat_deg = radius_m / 111320
    lon_deg = radius_m / (111320 * math.cos(math.radians(lat)))

    point = Point(lon, lat)
    # Create elliptical buffer approximation
    return point.buffer(max(lat_deg, lon_deg))


def create_bbox_polygon(north: float, south: float, east: float, west: float) -> Polygon:
    """
    Create a polygon from bounding box coordinates.

    Args:
        north, south, east, west: Bounding box coordinates

    Returns:
        Shapely Polygon
    """
    return box(west, south, east, north)


def lines_to_polygons(lines: list[LineString]) -> list[Polygon]:
    """
    Convert a collection of lines into polygons (enclosed areas).

    Args:
        lines: List of Shapely LineString objects

    Returns:
        List of polygons formed by the lines
    """
    return list(polygonize(lines))
