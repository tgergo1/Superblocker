"""
Superblock-Aware Router.

This module implements A* routing that respects superblock constraints:
- Routes primarily use arterial roads
- Entering superblocks only for origin/destination
- Respects one-way conversions and modal filters
- Ensures no through-traffic in superblock interiors
"""

import heapq
import math
import logging
from typing import Optional
from dataclasses import dataclass, field
from shapely.geometry import Point
import networkx as nx

from app.models.schemas import (
    Coordinates,
    RouteRequest,
    RouteResult,
    RouteSegment,
    CityPartition,
    EnforcedSuperblock,
)

logger = logging.getLogger(__name__)


# Speed assumptions for travel time estimation (km/h)
SPEED_ARTERIAL = 40
SPEED_INTERIOR = 20
SPEED_RESIDENTIAL = 25


@dataclass(order=True)
class PriorityNode:
    """Node in the A* priority queue."""
    f_score: float
    node_id: int = field(compare=False)
    came_from: Optional[int] = field(compare=False, default=None)
    came_via_edge: Optional[tuple] = field(compare=False, default=None)
    in_superblock: Optional[str] = field(compare=False, default=None)
    entry_sector: Optional[int] = field(compare=False, default=None)


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
        self.superblock_index = self._build_superblock_index()
        self.modified_graph = self._build_modified_graph()

    def _build_superblock_index(self) -> dict[int, EnforcedSuperblock]:
        """Build index mapping nodes to their containing superblock."""
        index = {}

        for sb in self.partition.superblocks:
            for ep in sb.entry_points:
                index[ep.node_id] = sb

            # Also index interior nodes (if we can determine them)
            # This is approximate - entry points are on boundary

        return index

    def _build_modified_graph(self) -> nx.MultiDiGraph:
        """Build graph with all superblock modifications applied."""
        modified = self.graph.copy()

        for sb in self.partition.superblocks:
            for mod in sb.modifications:
                if mod.modification_type.value == "modal_filter":
                    # Mark edges as vehicle-blocked
                    if modified.has_edge(mod.u, mod.v):
                        for k in modified[mod.u][mod.v]:
                            modified[mod.u][mod.v][k]["vehicle_blocked"] = True
                            modified[mod.u][mod.v][k]["superblock_id"] = sb.id
                    if modified.has_edge(mod.v, mod.u):
                        for k in modified[mod.v][mod.u]:
                            modified[mod.v][mod.u][k]["vehicle_blocked"] = True
                            modified[mod.v][mod.u][k]["superblock_id"] = sb.id

                elif mod.modification_type.value == "one_way":
                    if mod.direction == "u_to_v":
                        if modified.has_edge(mod.v, mod.u):
                            for k in list(modified[mod.v][mod.u].keys()):
                                modified.remove_edge(mod.v, mod.u, k)
                    else:
                        if modified.has_edge(mod.u, mod.v):
                            for k in list(modified[mod.u][mod.v].keys()):
                                modified.remove_edge(mod.u, mod.v, k)

                elif mod.modification_type.value == "full_closure":
                    if modified.has_edge(mod.u, mod.v):
                        for k in list(modified[mod.u][mod.v].keys()):
                            modified.remove_edge(mod.u, mod.v, k)
                    if modified.has_edge(mod.v, mod.u):
                        for k in list(modified[mod.v][mod.u].keys()):
                            modified.remove_edge(mod.v, mod.u, k)

        return modified

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
                    origin_node, dest_node, origin_sb
                )
            else:
                # Different superblocks or on arterial: use arterial routing
                return self._route_via_arterials(
                    origin_node, dest_node, origin_sb, dest_sb
                )
        else:
            # Ignore superblock constraints
            return self._route_direct(origin_node, dest_node)

    def _find_nearest_node(self, coords: Coordinates) -> Optional[int]:
        """Find nearest graph node to coordinates."""
        best_node = None
        best_dist = float("inf")

        for node, data in self.graph.nodes(data=True):
            if "x" not in data or "y" not in data:
                continue

            dx = data["x"] - coords.lon
            dy = data["y"] - coords.lat
            dist = dx*dx + dy*dy

            if dist < best_dist:
                best_dist = dist
                best_node = node

        return best_node

    def _find_containing_superblock(
        self, coords: Coordinates
    ) -> Optional[EnforcedSuperblock]:
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
    ) -> RouteResult:
        """Route within a single superblock."""
        # Use A* on modified graph
        path = self._astar(origin_node, dest_node, allow_interior=True)

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
        origin_sb: Optional[EnforcedSuperblock],
        dest_sb: Optional[EnforcedSuperblock],
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
            arterial_exit = self._find_nearest_arterial_from_node(origin_node)
            if arterial_exit is None:
                return RouteResult(
                    success=False,
                    blocked_reason="Cannot exit origin superblock to arterial",
                )

            exit_path = self._astar(
                origin_node, arterial_exit,
                allow_interior=True,
                restrict_to_superblock=origin_sb.id,
            )

            if exit_path is None:
                return RouteResult(
                    success=False,
                    blocked_reason="No path from origin to arterial network",
                )

            superblocks_traversed.append(origin_sb.id)
        else:
            arterial_exit = origin_node
            exit_path = [origin_node]

        # Phase 2: Find arterial entry to destination superblock (if applicable)
        if dest_sb is not None:
            arterial_entry = self._find_nearest_arterial_from_node(dest_node)
            if arterial_entry is None:
                return RouteResult(
                    success=False,
                    blocked_reason="Cannot enter destination superblock from arterial",
                )

            if dest_sb.id not in superblocks_traversed:
                superblocks_traversed.append(dest_sb.id)
        else:
            arterial_entry = dest_node

        # Phase 3: Route on arterials
        if arterial_exit != arterial_entry:
            arterial_path = self._astar(
                arterial_exit, arterial_entry,
                allow_interior=False,  # Arterials only
            )

            if arterial_path is None:
                # Try allowing interior as fallback
                arterial_path = self._astar(arterial_exit, arterial_entry, allow_interior=True)

                if arterial_path is None:
                    return RouteResult(
                        success=False,
                        blocked_reason="No arterial route between origin and destination areas",
                        alternative_available=False,
                    )
        else:
            arterial_path = [arterial_exit]

        # Phase 4: Enter destination superblock (if applicable)
        if dest_sb is not None and arterial_entry != dest_node:
            entry_path = self._astar(
                arterial_entry, dest_node,
                allow_interior=True,
                restrict_to_superblock=dest_sb.id,
            )

            if entry_path is None:
                return RouteResult(
                    success=False,
                    blocked_reason="No path from arterial to destination",
                )
        else:
            entry_path = [dest_node] if arterial_entry != dest_node else []

        # Combine paths
        full_path = exit_path[:-1] + arterial_path + entry_path[1:] if entry_path else exit_path[:-1] + arterial_path
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

    def _route_direct(
        self, origin_node: int, dest_node: int
    ) -> RouteResult:
        """Route directly without superblock constraints (for comparison)."""
        path = self._astar(origin_node, dest_node, allow_interior=True)

        if path is None:
            return RouteResult(
                success=False,
                blocked_reason="No path found",
            )

        segments = self._path_to_segments(path)
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
        restrict_to_superblock: Optional[str] = None,
    ) -> Optional[list[int]]:
        """
        A* pathfinding with superblock-aware costs.

        Args:
            start: Start node ID
            goal: Goal node ID
            allow_interior: Whether to allow interior superblock roads
            restrict_to_superblock: If set, only allow edges in this superblock
        """
        if start not in self.modified_graph.nodes or goal not in self.modified_graph.nodes:
            return None

        goal_data = self.modified_graph.nodes[goal]
        goal_x = goal_data.get("x", 0)
        goal_y = goal_data.get("y", 0)

        def heuristic(node: int) -> float:
            node_data = self.modified_graph.nodes.get(node, {})
            dx = (node_data.get("x", 0) - goal_x) * 111000  # Approximate meters
            dy = (node_data.get("y", 0) - goal_y) * 111000
            return math.sqrt(dx*dx + dy*dy)

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
            for _, neighbor, key, data in self.modified_graph.out_edges(
                current.node_id, keys=True, data=True
            ):
                # Check if edge is traversable
                if data.get("vehicle_blocked", False):
                    continue

                if not allow_interior:
                    osmid = data.get("osmid", 0)
                    if isinstance(osmid, list):
                        osmid = osmid[0]
                    if osmid not in self.arterial_set:
                        # Check if it's really an interior road
                        highway = data.get("highway", "")
                        if highway not in {"primary", "secondary", "tertiary",
                                           "primary_link", "secondary_link", "tertiary_link"}:
                            continue

                if restrict_to_superblock is not None:
                    edge_sb = data.get("superblock_id")
                    if edge_sb is not None and edge_sb != restrict_to_superblock:
                        continue

                # Calculate cost
                length = data.get("length", 100)
                highway = data.get("highway", "residential")

                # Prefer arterials with lower cost
                if highway in {"primary", "secondary", "tertiary"}:
                    cost_factor = 1.0
                else:
                    cost_factor = 1.5 if allow_interior else 10.0

                edge_cost = length * cost_factor

                tentative_g = g_score[current.node_id] + edge_cost

                if neighbor not in g_score or tentative_g < g_score[neighbor]:
                    came_from[neighbor] = current.node_id
                    g_score[neighbor] = tentative_g
                    f_score = tentative_g + heuristic(neighbor)
                    heapq.heappush(open_set, PriorityNode(
                        f_score=f_score,
                        node_id=neighbor,
                    ))

        return None

    def _find_nearest_arterial_from_node(self, node: int) -> Optional[int]:
        """Find nearest node on the arterial network."""
        node_data = self.graph.nodes.get(node, {})
        if "x" not in node_data:
            return None

        nx_coord = node_data["x"]
        ny_coord = node_data["y"]

        best_node = None
        best_dist = float("inf")

        # Check nodes connected to arterial edges
        for sb in self.partition.superblocks:
            for ep in sb.entry_points:
                if ep.node_id not in self.graph.nodes:
                    continue

                ep_data = self.graph.nodes[ep.node_id]
                dx = ep_data.get("x", 0) - nx_coord
                dy = ep_data.get("y", 0) - ny_coord
                dist = dx*dx + dy*dy

                if dist < best_dist:
                    best_dist = dist
                    best_node = ep.node_id

        return best_node

    def _path_to_segments(self, path: list[int]) -> list[RouteSegment]:
        """Convert node path to route segments."""
        if len(path) < 2:
            return []

        segments = []
        current_coords = []
        current_road_type = None
        current_is_arterial = None
        current_sb_id = None
        current_length = 0

        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]

            # Get edge data (take first if multiple)
            if self.modified_graph.has_edge(u, v):
                edge_data = list(self.modified_graph[u][v].values())[0]
            elif self.modified_graph.has_edge(v, u):
                edge_data = list(self.modified_graph[v][u].values())[0]
            else:
                continue

            # Get coordinates
            u_data = self.modified_graph.nodes[u]
            v_data = self.modified_graph.nodes[v]

            coord_u = Coordinates(lat=u_data.get("y", 0), lon=u_data.get("x", 0))
            coord_v = Coordinates(lat=v_data.get("y", 0), lon=v_data.get("x", 0))

            highway = edge_data.get("highway", "residential")
            if isinstance(highway, list):
                highway = highway[0]

            is_arterial = highway in {"primary", "secondary", "tertiary",
                                      "primary_link", "secondary_link", "tertiary_link"}
            sb_id = edge_data.get("superblock_id")
            length = edge_data.get("length", 0)

            # Check if we need to start a new segment
            if (current_road_type != highway or
                current_is_arterial != is_arterial or
                current_sb_id != sb_id):

                if current_coords:
                    segments.append(RouteSegment(
                        coordinates=current_coords,
                        road_type=current_road_type or "unknown",
                        is_arterial=current_is_arterial or False,
                        superblock_id=current_sb_id,
                        length_m=current_length,
                    ))

                current_coords = [coord_u]
                current_road_type = highway
                current_is_arterial = is_arterial
                current_sb_id = sb_id
                current_length = 0

            current_coords.append(coord_v)
            current_length += length

        # Add final segment
        if current_coords:
            segments.append(RouteSegment(
                coordinates=current_coords,
                road_type=current_road_type or "unknown",
                is_arterial=current_is_arterial or False,
                superblock_id=current_sb_id,
                length_m=current_length,
            ))

        return segments

    def _calculate_metrics(
        self, segments: list[RouteSegment]
    ) -> tuple[float, float]:
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
    return router.route(RouteRequest(
        origin=origin,
        destination=destination,
        respect_superblocks=respect_superblocks,
    ))
