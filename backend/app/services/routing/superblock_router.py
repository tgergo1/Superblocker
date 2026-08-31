"""
Superblock-Aware Router.

This module implements A* routing that respects superblock constraints:
- Routes primarily use arterial roads
- Entering superblocks only for origin/destination
- Respects one-way conversions and modal filters
- Ensures no through-traffic in superblock interiors
"""

import heapq
import itertools
import logging
import math
from dataclasses import dataclass, field

import networkx as nx
from shapely.geometry import Point, shape

from app.models.schemas import (
    CityPartition,
    Coordinates,
    EnforcedSuperblock,
    RouteRequest,
    RouteResult,
    RouteSegment,
)

logger = logging.getLogger(__name__)


# Speed assumptions for travel time estimation (km/h)
SPEED_ARTERIAL = 40
SPEED_INTERIOR = 20
SPEED_RESIDENTIAL = 25
METERS_PER_DEGREE_LON = 111320
METERS_PER_DEGREE_LAT = 110540


@dataclass(order=True)
class PriorityNode:
    """Node in the A* priority queue."""

    f_score: float
    node_id: int = field(compare=False)
    came_from: int | None = field(compare=False, default=None)
    came_via_edge: tuple | None = field(compare=False, default=None)
    in_superblock: str | None = field(compare=False, default=None)
    entry_sector: int | None = field(compare=False, default=None)


class SuperblockRouter:
    """
    Routes between locations while respecting superblock constraints.

    The routing algorithm:
    1. If origin and destination in same superblock: route directly within
    2. If different superblocks: origin → arterial → arterial route → destination
    3. Never allows through-traffic in superblock interiors
    """

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        partition: CityPartition,
    ):
        """
        Initialize the router.

        Args:
            graph: Full street network graph
            partition: City partition with superblocks and modifications
        """
        self.graph = graph
        self.partition = partition

        # Build auxiliary data structures
        self.arterial_set = set(partition.arterial_network)
        self.superblocks_by_id = {sb.id: sb for sb in partition.superblocks}
        self.superblock_index = self._build_superblock_index()
        self.superblock_node_ids: dict[str, set[int]] = {
            sb.id: {
                node_id
                for node_id, indexed_sb in self.superblock_index.items()
                if indexed_sb.id == sb.id
            }
            for sb in partition.superblocks
        }
        self.arterial_node_ids = self._build_arterial_node_ids()
        self.modified_graph = self._build_modified_graph()

    def _build_superblock_index(self) -> dict[int, EnforcedSuperblock]:
        """Build index mapping nodes to their containing superblock."""
        index: dict[int, EnforcedSuperblock] = {}
        polygons = [(sb, shape(sb.geometry)) for sb in self.partition.superblocks]

        for node_id, data in self.graph.nodes(data=True):
            if "x" not in data or "y" not in data:
                continue
            point = Point(data["x"], data["y"])
            for sb, polygon in polygons:
                if polygon.covers(point):
                    index[node_id] = sb
                    break

        for sb in self.partition.superblocks:
            for entry_point in sb.entry_points:
                index.setdefault(entry_point.node_id, sb)

        return index

    def _build_modified_graph(self) -> nx.MultiDiGraph:
        """Build graph with all superblock modifications applied."""
        modified = self.graph.copy()

        for u, v, _key, data in modified.edges(keys=True, data=True):
            u_sb = self.superblock_index.get(u)
            v_sb = self.superblock_index.get(v)
            if u_sb is not None and v_sb is not None and u_sb.id == v_sb.id:
                data["superblock_id"] = u_sb.id

        for sb in self.partition.superblocks:
            for mod in sb.modifications:
                if mod.modification_type.value == "modal_filter":
                    # Mark edges as vehicle-blocked
                    for k in self._matching_edge_keys(modified, mod.u, mod.v, mod.key, mod.osm_id):
                        if modified.has_edge(mod.u, mod.v, k):
                            modified[mod.u][mod.v][k]["vehicle_blocked"] = True
                            modified[mod.u][mod.v][k]["superblock_id"] = sb.id
                    for k in self._matching_edge_keys(modified, mod.v, mod.u, None, mod.osm_id):
                        if modified.has_edge(mod.v, mod.u, k):
                            modified[mod.v][mod.u][k]["vehicle_blocked"] = True
                            modified[mod.v][mod.u][k]["superblock_id"] = sb.id

                elif mod.modification_type.value == "one_way":
                    if mod.direction == "u_to_v":
                        for key in self._matching_edge_keys(
                            modified, mod.v, mod.u, None, mod.osm_id
                        ):
                            modified.remove_edge(mod.v, mod.u, key)
                    else:
                        for key in self._matching_edge_keys(
                            modified, mod.u, mod.v, mod.key, mod.osm_id
                        ):
                            modified.remove_edge(mod.u, mod.v, key)

                elif mod.modification_type.value == "full_closure":
                    for u, v, key_hint in (
                        (mod.u, mod.v, mod.key),
                        (mod.v, mod.u, None),
                    ):
                        for key in self._matching_edge_keys(modified, u, v, key_hint, mod.osm_id):
                            modified.remove_edge(u, v, key)

                elif mod.modification_type.value == "two_way":
                    if not self.graph.has_edge(mod.u, mod.v):
                        continue
                    edge_data = self.graph[mod.u][mod.v].get(mod.key)
                    if edge_data is None:
                        edge_data = next(iter(self.graph[mod.u][mod.v].values()), None)
                    if edge_data is not None and not modified.has_edge(mod.v, mod.u):
                        reverse_data = dict(edge_data)
                        reverse_data["superblock_id"] = sb.id
                        modified.add_edge(mod.v, mod.u, **reverse_data)

        return modified

    @staticmethod
    def _matching_edge_keys(
        graph: nx.MultiDiGraph,
        u: int,
        v: int,
        key_hint: int | None,
        osm_id: int,
    ) -> list[int]:
        if not graph.has_edge(u, v):
            return []
        if key_hint is not None and graph.has_edge(u, v, key_hint):
            return [key_hint]
        matching = []
        for key, data in graph[u][v].items():
            raw_osmid = data.get("osmid", 0)
            osm_ids = raw_osmid if isinstance(raw_osmid, list) else [raw_osmid]
            if osm_id in osm_ids:
                matching.append(key)
        return matching

    def _build_arterial_node_ids(self) -> set[int]:
        """Collect nodes that belong to arterial edges."""
        arterial_nodes: set[int] = set()
        for u, v, data in self.graph.edges(data=True):
            if self._is_arterial_edge(data):
                arterial_nodes.add(u)
                arterial_nodes.add(v)

        return arterial_nodes

    def route(self, request: RouteRequest) -> RouteResult:
        """
        Compute a route respecting superblock constraints.

        Args:
            request: Route request with origin and destination

        Returns:
            RouteResult with route segments or failure reason
        """
        # Find nearest nodes
        origin_node = self._find_nearest_node(request.origin)
        dest_node = self._find_nearest_node(request.destination)

        if origin_node is None:
            return RouteResult(
                success=False,
                blocked_reason="Could not find road near origin",
            )

        if dest_node is None:
            return RouteResult(
                success=False,
                blocked_reason="Could not find road near destination",
            )

        if origin_node == dest_node:
            return RouteResult(
                success=True,
                segments=[],
                total_distance_km=0,
                estimated_time_min=0,
                arterial_percent=100,
                superblocks_traversed=[],
            )

        # Find superblocks containing origin/destination
        origin_sb = self._find_containing_superblock(request.origin)
        dest_sb = self._find_containing_superblock(request.destination)

        # Choose routing strategy
        if request.respect_superblocks:
            if origin_sb is not None and dest_sb is not None and origin_sb.id == dest_sb.id:
                # Same superblock: route within
                return self._route_within_superblock(
                    origin_node, dest_node, origin_sb, request.prefer_arterials
                )
            else:
                # Different superblocks or on arterial: use arterial routing
                return self._route_via_arterials(
                    origin_node,
                    dest_node,
                    origin_sb,
                    dest_sb,
                    request.prefer_arterials,
                )
        else:
            # Ignore superblock constraints
            return self._route_direct(origin_node, dest_node)

    def _find_nearest_node(self, coords: Coordinates) -> int | None:
        """Find nearest graph node to coordinates."""
        best_node = None
        best_dist = float("inf")

        for node, data in self.graph.nodes(data=True):
            if "x" not in data or "y" not in data:
                continue

            dist = self._distance_squared_m(
                coords.lon,
                coords.lat,
                data["x"],
                data["y"],
            )

            if dist < best_dist:
                best_dist = dist
                best_node = node

        return best_node

    def _find_containing_superblock(self, coords: Coordinates) -> EnforcedSuperblock | None:
        """Find which superblock contains the given coordinates."""
        point = Point(coords.lon, coords.lat)

        for sb in self.partition.superblocks:
            from shapely.geometry import shape

            polygon = shape(sb.geometry)

            if polygon.contains(point):
                return sb

        return None

    def _route_within_superblock(
        self,
        origin_node: int,
        dest_node: int,
        superblock: EnforcedSuperblock,
        prefer_arterials: bool,
    ) -> RouteResult:
        """Route within a single superblock."""
        # Use A* on modified graph
        path = self._astar(
            origin_node,
            dest_node,
            allow_interior=True,
            restrict_to_superblock=superblock.id,
            prefer_arterials=prefer_arterials,
        )

        if path is None:
            return RouteResult(
                success=False,
                blocked_reason="No path found within superblock",
            )

        segments = self._path_to_segments(path)
        total_dist, total_time = self._calculate_metrics(segments)

        return RouteResult(
            success=True,
            segments=segments,
            total_distance_km=total_dist,
            estimated_time_min=total_time,
            arterial_percent=self._calculate_arterial_percent(segments),
            superblocks_traversed=[superblock.id],
        )

    def _route_via_arterials(
        self,
        origin_node: int,
        dest_node: int,
        origin_sb: EnforcedSuperblock | None,
        dest_sb: EnforcedSuperblock | None,
        prefer_arterials: bool,
    ) -> RouteResult:
        """
        Route via arterial network.

        Strategy:
        1. If origin in superblock: find nearest arterial exit
        2. Route on arterial network
        3. If destination in superblock: find nearest arterial entry
        """
        superblocks_traversed = []

        # Phase 1: Exit origin superblock (if applicable)
        if origin_sb is not None:
            exit_connection = self._find_arterial_connection(
                origin_node, origin_sb, reverse=False, prefer_arterials=prefer_arterials
            )
            if exit_connection is None:
                return RouteResult(
                    success=False,
                    blocked_reason="Cannot exit origin superblock to arterial",
                )

            arterial_exit, exit_path = exit_connection

            superblocks_traversed.append(origin_sb.id)
        else:
            connection = self._find_external_arterial_connection(origin_node, reverse=False)
            if connection is None:
                return RouteResult(
                    success=False,
                    blocked_reason="No path from origin to arterial network",
                )
            arterial_exit, exit_path = connection

        # Phase 2: Find arterial entry to destination superblock (if applicable)
        if dest_sb is not None:
            entry_connection = self._find_arterial_connection(
                dest_node, dest_sb, reverse=True, prefer_arterials=prefer_arterials
            )
            if entry_connection is None:
                return RouteResult(
                    success=False,
                    blocked_reason="Cannot enter destination superblock from arterial",
                )

            arterial_entry, entry_path = entry_connection
            if dest_sb.id not in superblocks_traversed:
                superblocks_traversed.append(dest_sb.id)
        else:
            connection = self._find_external_arterial_connection(dest_node, reverse=True)
            if connection is None:
                return RouteResult(
                    success=False,
                    blocked_reason="No path from arterial network to destination",
                )
            arterial_entry, entry_path = connection

        # Phase 3: Route on arterials
        if arterial_exit != arterial_entry:
            arterial_path = self._astar(
                arterial_exit,
                arterial_entry,
                allow_interior=False,  # Arterials only
                prefer_arterials=True,
            )

            if arterial_path is None:
                return RouteResult(
                    success=False,
                    blocked_reason="No arterial route between origin and destination areas",
                    alternative_available=False,
                )
        else:
            arterial_path = [arterial_exit]

        # Phase 4: Enter destination superblock (if applicable)
        # Combine paths
        full_path = exit_path[:-1] + arterial_path
        if entry_path:
            if full_path and entry_path[0] == full_path[-1]:
                full_path += entry_path[1:]
            else:
                full_path += entry_path
        full_path = self._deduplicate_path(full_path)

        segments = self._path_to_segments(full_path)
        total_dist, total_time = self._calculate_metrics(segments)

        return RouteResult(
            success=True,
            segments=segments,
            total_distance_km=total_dist,
            estimated_time_min=total_time,
            arterial_percent=self._calculate_arterial_percent(segments),
            superblocks_traversed=superblocks_traversed,
        )

    def _route_direct(self, origin_node: int, dest_node: int) -> RouteResult:
        """Route directly without superblock constraints (for comparison)."""
        path = self._astar(
            origin_node,
            dest_node,
            allow_interior=True,
            graph=self.graph,
            prefer_arterials=False,
        )

        if path is None:
            return RouteResult(
                success=False,
                blocked_reason="No path found",
            )

        segments = self._path_to_segments(path, graph=self.graph)
        total_dist, total_time = self._calculate_metrics(segments)

        return RouteResult(
            success=True,
            segments=segments,
            total_distance_km=total_dist,
            estimated_time_min=total_time,
            arterial_percent=self._calculate_arterial_percent(segments),
            superblocks_traversed=[],  # Not tracked for direct routing
        )

    def _astar(
        self,
        start: int,
        goal: int,
        allow_interior: bool = True,
        restrict_to_superblock: str | None = None,
        prefer_arterials: bool = True,
        graph: nx.MultiDiGraph | None = None,
        avoid_superblocks: bool = False,
    ) -> list[int] | None:
        """
        A* pathfinding with superblock-aware costs.

        Args:
            start: Start node ID
            goal: Goal node ID
            allow_interior: Whether to allow interior superblock roads
            restrict_to_superblock: If set, only allow edges in this superblock
        """
        routing_graph = graph or self.modified_graph
        if start not in routing_graph.nodes or goal not in routing_graph.nodes:
            return None

        goal_data = routing_graph.nodes[goal]
        goal_x = goal_data.get("x", 0)
        goal_y = goal_data.get("y", 0)

        def heuristic(node: int) -> float:
            node_data = routing_graph.nodes.get(node, {})
            return self._distance_m(
                node_data.get("x", 0),
                node_data.get("y", 0),
                goal_x,
                goal_y,
            )

        # A* implementation
        open_set = [PriorityNode(f_score=heuristic(start), node_id=start)]
        came_from: dict[int, int] = {}
        g_score: dict[int, float] = {start: 0}

        while open_set:
            current = heapq.heappop(open_set)

            if current.node_id == goal:
                # Reconstruct path
                path = [goal]
                node = goal
                while node in came_from:
                    node = came_from[node]
                    path.append(node)
                path.reverse()
                return path

            # Explore neighbors
            for _, neighbor, _key, data in routing_graph.out_edges(
                current.node_id, keys=True, data=True
            ):
                # Check if edge is traversable
                if data.get("vehicle_blocked", False):
                    continue

                if not allow_interior and not self._is_arterial_edge(data):
                    continue

                if restrict_to_superblock is not None:
                    edge_sb = data.get("superblock_id")
                    if edge_sb != restrict_to_superblock:
                        allowed_nodes = self.superblock_node_ids.get(restrict_to_superblock, set())
                        is_boundary_connector = self._is_arterial_edge(data) and (
                            current.node_id in allowed_nodes or neighbor in allowed_nodes
                        )
                        if not is_boundary_connector:
                            continue
                elif avoid_superblocks and data.get("superblock_id") is not None:
                    continue

                # Calculate cost
                length = data.get("length", 100)
                highway = data.get("highway", "residential")
                if isinstance(highway, list):
                    highway = highway[0] if highway else "residential"

                is_arterial = self._is_arterial_edge(data)
                cost_factor = 1.0 if is_arterial or not prefer_arterials else 1.5

                edge_cost = length * cost_factor

                tentative_g = g_score[current.node_id] + edge_cost

                if neighbor not in g_score or tentative_g < g_score[neighbor]:
                    came_from[neighbor] = current.node_id
                    g_score[neighbor] = tentative_g
                    f_score = tentative_g + heuristic(neighbor)
                    heapq.heappush(
                        open_set,
                        PriorityNode(
                            f_score=f_score,
                            node_id=neighbor,
                        ),
                    )

        return None

    def _is_arterial_edge(self, data: dict) -> bool:
        """Return whether an edge belongs to the partition's arterial network."""
        raw_osmid = data.get("osmid", 0)
        osm_ids = raw_osmid if isinstance(raw_osmid, list) else [raw_osmid]
        if any(osmid in self.arterial_set for osmid in osm_ids):
            return True

        highway = data.get("highway", "")
        highways = highway if isinstance(highway, list) else [highway]
        arterial_highways = {
            "motorway",
            "motorway_link",
            "trunk",
            "trunk_link",
            "primary",
            "primary_link",
            "secondary",
            "secondary_link",
            "tertiary",
            "tertiary_link",
        }
        return any(value in arterial_highways for value in highways)

    def _find_arterial_connection(
        self,
        node: int,
        superblock: EnforcedSuperblock,
        *,
        reverse: bool,
        prefer_arterials: bool,
    ) -> tuple[int, list[int]] | None:
        """Find a reachable entry point and its constrained connector path."""
        candidates = {
            entry.node_id
            for entry in superblock.entry_points
            if entry.node_id in self.arterial_node_ids
        }
        if not candidates:
            candidates = {
                entry.node_id
                for entry in superblock.entry_points
                if entry.node_id in self.modified_graph
            }

        best: tuple[int, list[int]] | None = None
        best_length = float("inf")
        for arterial_node in candidates:
            start, goal = (arterial_node, node) if reverse else (node, arterial_node)
            path = self._astar(
                start,
                goal,
                allow_interior=True,
                restrict_to_superblock=superblock.id,
                prefer_arterials=prefer_arterials,
            )
            if path is None:
                continue
            length = self._path_length(path, self.modified_graph)
            if length < best_length:
                best = (arterial_node, path)
                best_length = length
        return best

    def _find_external_arterial_connection(
        self,
        node: int,
        *,
        reverse: bool,
    ) -> tuple[int, list[int]] | None:
        """Connect a point outside cells to the arterial grid without crossing a cell."""
        if node in self.arterial_node_ids:
            return node, [node]
        node_data = self.graph.nodes.get(node, {})
        candidates = sorted(
            self.arterial_node_ids,
            key=lambda arterial_node: self._distance_squared_m(
                node_data.get("x", 0),
                node_data.get("y", 0),
                self.graph.nodes[arterial_node].get("x", 0),
                self.graph.nodes[arterial_node].get("y", 0),
            ),
        )[:50]
        best: tuple[int, list[int]] | None = None
        best_length = float("inf")
        for arterial_node in candidates:
            start, goal = (arterial_node, node) if reverse else (node, arterial_node)
            path = self._astar(
                start,
                goal,
                allow_interior=True,
                prefer_arterials=False,
                avoid_superblocks=True,
            )
            if path is None:
                continue
            length = self._path_length(path, self.modified_graph)
            if length < best_length:
                best = (arterial_node, path)
                best_length = length
        return best

    @staticmethod
    def _path_length(path: list[int], graph: nx.MultiDiGraph) -> float:
        total = 0.0
        for u, v in itertools.pairwise(path):
            if graph.has_edge(u, v):
                total += min(float(data.get("length", 0) or 0) for data in graph[u][v].values())
        return total

    def _find_nearest_arterial_from_node(self, node: int) -> int | None:
        """Find nearest node on the arterial network."""
        node_data = self.graph.nodes.get(node, {})
        if "x" not in node_data:
            return None

        nx_coord = node_data["x"]
        ny_coord = node_data["y"]

        best_node = None
        best_dist = float("inf")

        for arterial_node in self.arterial_node_ids:
            if arterial_node not in self.graph.nodes:
                continue

            arterial_data = self.graph.nodes[arterial_node]
            dist = self._distance_squared_m(
                nx_coord,
                ny_coord,
                arterial_data.get("x", 0),
                arterial_data.get("y", 0),
            )

            if dist < best_dist:
                best_dist = dist
                best_node = arterial_node

        return best_node

    @staticmethod
    def _distance_squared_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        """Approximate squared local metric distance."""
        mid_lat = math.radians((lat1 + lat2) / 2)
        dx = (lon2 - lon1) * METERS_PER_DEGREE_LON * math.cos(mid_lat)
        dy = (lat2 - lat1) * METERS_PER_DEGREE_LAT
        return dx * dx + dy * dy

    @classmethod
    def _distance_m(cls, lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        """Approximate local metric distance."""
        return math.sqrt(cls._distance_squared_m(lon1, lat1, lon2, lat2))

    def _path_to_segments(
        self,
        path: list[int],
        *,
        graph: nx.MultiDiGraph | None = None,
    ) -> list[RouteSegment]:
        """Convert node path to route segments."""
        if len(path) < 2:
            return []

        routing_graph = graph or self.modified_graph

        segments = []
        current_coords = []
        current_road_type = None
        current_is_arterial = None
        current_sb_id = None
        current_length = 0

        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]

            # Select the shortest traversable parallel edge instead of relying on
            # dictionary insertion order.
            if routing_graph.has_edge(u, v):
                edge_data = min(
                    (
                        data
                        for data in routing_graph[u][v].values()
                        if not data.get("vehicle_blocked", False)
                    ),
                    key=lambda data: float(data.get("length", 0) or 0),
                    default=None,
                )
            elif routing_graph.has_edge(v, u):
                edge_data = min(
                    routing_graph[v][u].values(),
                    key=lambda data: float(data.get("length", 0) or 0),
                )
            else:
                continue
            if edge_data is None:
                continue

            # Get coordinates
            u_data = routing_graph.nodes[u]
            v_data = routing_graph.nodes[v]

            coord_u = Coordinates(lat=u_data.get("y", 0), lon=u_data.get("x", 0))
            coord_v = Coordinates(lat=v_data.get("y", 0), lon=v_data.get("x", 0))

            highway = edge_data.get("highway", "residential")
            if isinstance(highway, list):
                highway = highway[0]

            is_arterial = highway in {
                "primary",
                "secondary",
                "tertiary",
                "primary_link",
                "secondary_link",
                "tertiary_link",
            }
            sb_id = edge_data.get("superblock_id")
            length = edge_data.get("length", 0)

            # Check if we need to start a new segment
            if (
                current_road_type != highway
                or current_is_arterial != is_arterial
                or current_sb_id != sb_id
            ):
                if current_coords:
                    segments.append(
                        RouteSegment(
                            coordinates=current_coords,
                            road_type=current_road_type or "unknown",
                            is_arterial=current_is_arterial or False,
                            superblock_id=current_sb_id,
                            length_m=current_length,
                        )
                    )

                current_coords = [coord_u]
                current_road_type = highway
                current_is_arterial = is_arterial
                current_sb_id = sb_id
                current_length = 0

            current_coords.append(coord_v)
            current_length += length

        # Add final segment
        if current_coords:
            segments.append(
                RouteSegment(
                    coordinates=current_coords,
                    road_type=current_road_type or "unknown",
                    is_arterial=current_is_arterial or False,
                    superblock_id=current_sb_id,
                    length_m=current_length,
                )
            )

        return segments

    def _calculate_metrics(self, segments: list[RouteSegment]) -> tuple[float, float]:
        """Calculate total distance and time for segments."""
        total_dist_m = sum(s.length_m for s in segments)
        total_dist_km = total_dist_m / 1000

        # Estimate time based on road type
        total_time_h = 0
        for s in segments:
            if s.is_arterial:
                speed = SPEED_ARTERIAL
            elif s.road_type == "residential":
                speed = SPEED_RESIDENTIAL
            else:
                speed = SPEED_INTERIOR

            total_time_h += (s.length_m / 1000) / speed

        total_time_min = total_time_h * 60

        return total_dist_km, total_time_min

    def _calculate_arterial_percent(self, segments: list[RouteSegment]) -> float:
        """Calculate percentage of route on arterials."""
        total_length = sum(s.length_m for s in segments)
        arterial_length = sum(s.length_m for s in segments if s.is_arterial)

        if total_length == 0:
            return 100.0

        return (arterial_length / total_length) * 100

    def _deduplicate_path(self, path: list[int]) -> list[int]:
        """Remove consecutive duplicates from path."""
        if not path:
            return path

        result = [path[0]]
        for node in path[1:]:
            if node != result[-1]:
                result.append(node)

        return result


def route_with_superblocks(
    graph: nx.MultiDiGraph,
    partition: CityPartition,
    origin: Coordinates,
    destination: Coordinates,
    respect_superblocks: bool = True,
) -> RouteResult:
    """
    Convenience function for superblock-aware routing.

    Args:
        graph: Street network graph
        partition: City partition with superblocks
        origin: Starting coordinates
        destination: Ending coordinates
        respect_superblocks: Whether to enforce superblock constraints

    Returns:
        RouteResult with the computed route
    """
    router = SuperblockRouter(graph, partition)
    return router.route(
        RouteRequest(
            origin=origin,
            destination=destination,
            respect_superblocks=respect_superblocks,
        )
    )
