"""Load the street graph from a local OpenStreetMap extract.

City-scale runs against the public Overpass API are slow and rate-limited. A
free extract (Geofabrik's Hungary file, a BBBike Budapest cut, or any
.osm.pbf, .osm or .osm.bz2) holds the same data offline. This module reads the
extract with pyosmium, keeps the roads OSMnx's ``drive`` network would keep
inside the requested area, and builds the graph with OSMnx so it matches an
Overpass download.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

import networkx as nx
import osmium
import osmnx as ox
from shapely.geometry import Polygon

# OSMnx's "drive" filter, as of osmnx 2.1.
_DRIVE_EXCLUDED_HIGHWAYS = re.compile(
    r"abandoned|bridleway|bus_guideway|construction|corridor|cycleway|elevator|escalator|"
    r"footway|no|path|pedestrian|planned|platform|proposed|raceway|razed|rest_area|service|"
    r"services|steps|track"
)
_DRIVE_EXCLUDED_SERVICE = re.compile(
    r"alley|driveway|emergency_access|parking|parking_aisle|private"
)
_ALL_EXCLUDED_HIGHWAYS = re.compile(
    r"abandoned|construction|no|planned|platform|proposed|raceway|razed"
)
_YES = re.compile("yes")
_NO = re.compile("no")
_PRIVATE = re.compile("private")
SUPPORTED_NETWORK_TYPES = ("drive", "all")


def _matches(pattern: re.Pattern[str], value: str | None) -> bool:
    return value is not None and pattern.search(value) is not None


def _keep_drive(tags: Mapping[str, str]) -> bool:
    return (
        "highway" in tags
        and not _matches(_YES, tags.get("area"))
        and not _matches(_PRIVATE, tags.get("access"))
        and not _matches(_DRIVE_EXCLUDED_HIGHWAYS, tags.get("highway"))
        and not _matches(_NO, tags.get("motor_vehicle"))
        and not _matches(_NO, tags.get("motorcar"))
        and not _matches(_DRIVE_EXCLUDED_SERVICE, tags.get("service"))
    )


def _keep_all(tags: Mapping[str, str]) -> bool:
    return (
        "highway" in tags
        and not _matches(_YES, tags.get("area"))
        and not _matches(_PRIVATE, tags.get("access"))
        and not _matches(_ALL_EXCLUDED_HIGHWAYS, tags.get("highway"))
    )


def road_filter(network_type: str) -> Callable[[Mapping[str, str]], bool]:
    if network_type == "drive":
        return _keep_drive
    if network_type == "all":
        return _keep_all
    raise ValueError(
        f"Extract loading supports {', '.join(SUPPORTED_NETWORK_TYPES)} networks, "
        f"not {network_type!r}"
    )


class _RoadCollector(osmium.SimpleHandler):
    """First pass: roads with at least one node inside the area."""

    def __init__(self, keep, west: float, south: float, east: float, north: float):
        super().__init__()
        self.keep = keep
        self.bounds = (west, south, east, north)
        self.way_ids: set[int] = set()
        self.node_ids: set[int] = set()

    def way(self, way) -> None:
        tags = {tag.k: tag.v for tag in way.tags}
        if not self.keep(tags):
            return
        west, south, east, north = self.bounds
        inside = False
        refs: list[int] = []
        for node in way.nodes:
            refs.append(node.ref)
            location = node.location
            if location.valid() and west <= location.lon <= east and south <= location.lat <= north:
                inside = True
        if inside:
            self.way_ids.add(way.id)
            self.node_ids.update(refs)


class _RoadWriter(osmium.SimpleHandler):
    """Second pass: write the selected nodes and roads to an OSM XML file."""

    def __init__(self, writer, way_ids: set[int], node_ids: set[int]):
        super().__init__()
        self.writer = writer
        self.way_ids = way_ids
        self.node_ids = node_ids

    def node(self, node) -> None:
        if node.id in self.node_ids:
            self.writer.add_node(node)

    def way(self, way) -> None:
        if way.id in self.way_ids:
            self.writer.add_way(way)


def graph_from_extract(
    extract_path: str | Path,
    bbox: tuple[float, float, float, float],
    network_type: str = "drive",
    polygon: Polygon | None = None,
) -> nx.MultiDiGraph:
    """Build the street graph for ``bbox`` (west, south, east, north) from an extract.

    Roads crossing the edge of the area are kept whole, as with OSMnx's
    ``truncate_by_edge``. When ``polygon`` is given the graph is clipped to it.
    """
    path = Path(extract_path)
    if not path.is_file():
        raise FileNotFoundError(f"OSM extract not found: {path}")
    keep = road_filter(network_type)
    west, south, east, north = bbox

    collector = _RoadCollector(keep, west, south, east, north)
    collector.apply_file(str(path), locations=True)
    if not collector.way_ids:
        raise ValueError("The OSM extract has no roads in the requested area")

    with tempfile.TemporaryDirectory() as directory:
        clipped = os.path.join(directory, "roads.osm")
        writer = osmium.SimpleWriter(clipped)
        try:
            _RoadWriter(writer, collector.way_ids, collector.node_ids).apply_file(str(path))
        finally:
            writer.close()
        graph = ox.graph_from_xml(clipped, bidirectional=False, simplify=True, retain_all=True)

    if polygon is not None:
        graph = ox.truncate.truncate_graph_polygon(graph, polygon, truncate_by_edge=True)
    else:
        graph = ox.truncate.truncate_graph_bbox(graph, bbox, truncate_by_edge=True)
    return ox.truncate.largest_component(graph, strongly=False)
