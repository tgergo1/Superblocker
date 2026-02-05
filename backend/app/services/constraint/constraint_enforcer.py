"""
Constraint Enforcer for Superblock Enter-Exit Rules.

This module implements the core graph theory algorithms to enforce the
superblock constraint: vehicles entering from one sector can only exit
from that same sector.

The algorithm uses minimum edge cuts to identify which edges need to be
modified (modal filters or one-way conversions) to eliminate cross-sector paths.
"""

import networkx as nx
import logging
import math
from typing import Optional
from dataclasses import dataclass, field
from shapely.geometry import Polygon, Point, LineString

from app.models.schemas import (
    EntryPoint,
    StreetModification,
    ModificationType,
    Coordinates,
    ConstraintViolation,
)

logger = logging.getLogger(__name__)


# Road hierarchy for determining modification type (lower = more important)
HIERARCHY_MAP = {
    "motorway": 1, "motorway_link": 1,
    "trunk": 2, "trunk_link": 2,
    "primary": 3, "primary_link": 3,
    "secondary": 4, "secondary_link": 4,
    "tertiary": 5, "tertiary_link": 5,
    "residential": 6,
    "living_street": 7,
    "unclassified": 8,
    "service": 9,
}


@dataclass
class SectorAssignment:
    """Entry points organized by sector."""
    num_sectors: int
    entry_points_by_sector: dict[int, list[int]]  # sector -> node IDs
    node_to_sector: dict[int, int]  # node_id -> sector
    sector_angles: list[tuple[float, float]]  # (start_angle, end_angle) per sector


@dataclass
class ModificationPlan:
    """Plan for modifying a superblock's interior to enforce constraints."""
    modal_filters: list[tuple[int, int, int]]  # (u, v, key) edges to add filters
    one_way_conversions: dict[tuple[int, int, int], str]  # edge -> direction
    cut_edges: set[tuple[int, int]]  # All edges that were cut


class ConstraintEnforcer:
    """
    Enforces the superblock enter-exit same-sector constraint.

    The core algorithm:
    1. Classify entry points into angular sectors
    2. Find all cross-sector paths (violations)
    3. Compute minimum edge cuts to eliminate violations
    4. Determine optimal modification for each cut edge
    5. Validate the result
    """

    def __init__(
        self,
        interior_graph: nx.MultiDiGraph,
        boundary_polygon: Polygon,
        entry_node_ids: list[int],
        num_sectors: int = 4,
    ):
        """
        Initialize the constraint enforcer.

        Args:
            interior_graph: NetworkX MultiDiGraph of interior roads
            boundary_polygon: Shapely Polygon of superblock boundary
            entry_node_ids: List of node IDs that connect to boundary roads
            num_sectors: Number of angular sectors (default 4 for N/E/S/W-like)
        """
        self.graph = interior_graph.copy()
        self.boundary = boundary_polygon
        self.entry_nodes = set(entry_node_ids)
        self.num_sectors = num_sectors

        # Compute centroid for sector calculations
        self.centroid = boundary_polygon.centroid

        # Sector assignment
        self.sectors: Optional[SectorAssignment] = None

    def enforce_constraints(self) -> tuple[list[StreetModification], list[ConstraintViolation]]:
        """
        Main method to enforce enter-exit same-sector constraint.

        Returns:
            Tuple of (modifications, remaining_violations)
        """
        # Step 1: Assign entry points to sectors
        self.sectors = self._assign_sectors()

        if len(self.entry_nodes) < 2:
            logger.info("Less than 2 entry points, no constraints to enforce")
            return [], []

        # Step 2: Find all cross-sector violations
        violations = self._find_violations()

        if not violations:
            logger.info("No cross-sector paths found, constraints already satisfied")
            return [], []

        logger.info(f"Found {len(violations)} cross-sector path violations")

        # Step 3: Compute minimum edge cuts
        plan = self._compute_modification_plan(violations)

        # Step 4: Generate modifications
        modifications = self._generate_modifications(plan)

        # Step 5: Apply modifications and validate
        remaining_violations = self._validate_modifications(modifications)

        return modifications, remaining_violations

    def _assign_sectors(self) -> SectorAssignment:
        """
        Assign entry points to angular sectors based on their position
        relative to the superblock centroid.
        """
        # Calculate sector boundaries (equal angular divisions)
        sector_size = 2 * math.pi / self.num_sectors
        # Start from -pi/num_sectors to center first sector on positive x-axis
        start_offset = -sector_size / 2

        sector_angles = []
        for i in range(self.num_sectors):
            start = start_offset + i * sector_size
            end = start + sector_size
            sector_angles.append((start, end))

        entry_points_by_sector: dict[int, list[int]] = {
            i: [] for i in range(self.num_sectors)
        }
        node_to_sector: dict[int, int] = {}

        cx, cy = self.centroid.x, self.centroid.y

        for node_id in self.entry_nodes:
            if node_id not in self.graph.nodes:
                continue

            node_data = self.graph.nodes[node_id]
            nx_coord = node_data.get("x", 0)
            ny_coord = node_data.get("y", 0)

            # Calculate angle from centroid
            angle = math.atan2(ny_coord - cy, nx_coord - cx)

            # Find sector
            sector = self._angle_to_sector(angle, sector_angles)
            entry_points_by_sector[sector].append(node_id)
            node_to_sector[node_id] = sector

        return SectorAssignment(
            num_sectors=self.num_sectors,
            entry_points_by_sector=entry_points_by_sector,
            node_to_sector=node_to_sector,
            sector_angles=sector_angles,
        )

    def _angle_to_sector(
        self, angle: float, sector_angles: list[tuple[float, float]]
    ) -> int:
        """Convert an angle to a sector index."""
        # Normalize angle to [-pi, pi]
        while angle > math.pi:
            angle -= 2 * math.pi
        while angle < -math.pi:
            angle += 2 * math.pi

        for i, (start, end) in enumerate(sector_angles):
            # Handle wrap-around at -pi/pi boundary
            if start < -math.pi:
                if angle >= start + 2 * math.pi or angle < end:
                    return i
            elif end > math.pi:
                if angle >= start or angle < end - 2 * math.pi:
                    return i
            elif start <= angle < end:
                return i

        # Default to sector 0 if angle doesn't match (shouldn't happen)
        return 0

    def _find_violations(self) -> list[ConstraintViolation]:
        """
        Find all cross-sector paths (violations of the constraint).

        A violation exists when there's a path from an entry point in sector A
        to an entry point in sector B where A != B.
        """
        violations = []

        # Convert to undirected for path checking (we care about connectivity)
        G_undirected = self.graph.to_undirected()

        # Check all pairs of entry points from different sectors
        sectors = self.sectors
        for sector_a in range(self.num_sectors):
            entries_a = sectors.entry_points_by_sector.get(sector_a, [])

            for sector_b in range(sector_a + 1, self.num_sectors):
                entries_b = sectors.entry_points_by_sector.get(sector_b, [])

                for entry_a in entries_a:
                    for entry_b in entries_b:
                        if entry_a == entry_b:
                            continue

                        try:
                            if nx.has_path(G_undirected, entry_a, entry_b):
                                # Path exists - this is a violation
                                path = nx.shortest_path(
                                    G_undirected, entry_a, entry_b
                                )
                                path_edges = list(zip(path[:-1], path[1:]))

                                violations.append(
                                    ConstraintViolation(
                                        from_entry=self._node_to_entry_point(
                                            entry_a, sector_a
                                        ),
                                        to_entry=self._node_to_entry_point(
                                            entry_b, sector_b
                                        ),
                                        path_exists=True,
                                        path_edges=path_edges,
                                    )
                                )
                        except nx.NetworkXError:
                            # Node not in graph
                            continue

        return violations

    def _node_to_entry_point(self, node_id: int, sector: int) -> EntryPoint:
        """Convert a node ID to an EntryPoint object."""
        node_data = self.graph.nodes.get(node_id, {})
        return EntryPoint(
            node_id=node_id,
            sector=sector,
            coordinates=Coordinates(
                lat=node_data.get("y", 0),
                lon=node_data.get("x", 0),
            ),
            boundary_road_id=0,  # Not available at this level
        )

    def _compute_modification_plan(
        self, violations: list[ConstraintViolation]
    ) -> ModificationPlan:
        """
        Compute the minimum set of edge modifications to eliminate all violations.

        Uses minimum edge cut algorithm to find edges that, when removed/modified,
        disconnect cross-sector entry point pairs.
        """
        cut_edges: set[tuple[int, int]] = set()

        # Convert to undirected simple graph for min-cut calculation
        G_simple = nx.Graph()
        for u, v, key, data in self.graph.edges(keys=True, data=True):
            if G_simple.has_edge(u, v):
                # Keep the edge with minimum capacity (most cuttable)
                existing_capacity = G_simple[u][v].get("capacity", 1)
                new_capacity = self._edge_cut_cost(data)
                if new_capacity < existing_capacity:
                    G_simple[u][v]["capacity"] = new_capacity
            else:
                G_simple.add_edge(u, v, capacity=self._edge_cut_cost(data))

        # Process violations by sector pair
        processed_pairs: set[tuple[int, int]] = set()

        for violation in violations:
            sector_a = violation.from_entry.sector
            sector_b = violation.to_entry.sector
            pair_key = (min(sector_a, sector_b), max(sector_a, sector_b))

            if pair_key in processed_pairs:
                continue

            # Get all entries from both sectors
            entries_a = self.sectors.entry_points_by_sector.get(sector_a, [])
            entries_b = self.sectors.entry_points_by_sector.get(sector_b, [])

            # Find minimum cut that disconnects all a-b pairs
            sector_cuts = self._find_sector_disconnect_cut(
                G_simple, entries_a, entries_b
            )
            cut_edges.update(sector_cuts)
            processed_pairs.add(pair_key)

        # Determine modification type for each cut edge
        modal_filters: list[tuple[int, int, int]] = []
        one_way_conversions: dict[tuple[int, int, int], str] = {}

        for u, v in cut_edges:
            # Find the actual edge in the multigraph
            if self.graph.has_edge(u, v):
                for key, data in self.graph[u][v].items():
                    mod_type, direction = self._determine_modification_type(
                        u, v, key, data
                    )
                    if mod_type == "modal_filter":
                        modal_filters.append((u, v, key))
                    elif mod_type == "one_way":
                        one_way_conversions[(u, v, key)] = direction

            # Also check reverse direction
            if self.graph.has_edge(v, u):
                for key, data in self.graph[v][u].items():
                    mod_type, direction = self._determine_modification_type(
                        v, u, key, data
                    )
                    if mod_type == "modal_filter":
                        if (v, u, key) not in modal_filters:
                            modal_filters.append((v, u, key))
                    elif mod_type == "one_way":
                        if (v, u, key) not in one_way_conversions:
                            one_way_conversions[(v, u, key)] = direction

        return ModificationPlan(
            modal_filters=modal_filters,
            one_way_conversions=one_way_conversions,
            cut_edges=cut_edges,
        )

    def _edge_cut_cost(self, edge_data: dict) -> float:
        """
        Calculate the cost of cutting an edge.

        Higher hierarchy roads have higher cut cost (prefer cutting minor roads).
        """
        highway = edge_data.get("highway", "residential")
        if isinstance(highway, list):
            highway = highway[0]
        hierarchy = HIERARCHY_MAP.get(highway, 6)

        # Invert hierarchy (1=most major becomes highest cost)
        return 10 - hierarchy + 1

    def _find_sector_disconnect_cut(
        self,
        G: nx.Graph,
        entries_a: list[int],
        entries_b: list[int],
    ) -> set[tuple[int, int]]:
        """
        Find minimum cut that disconnects all entries_a from all entries_b.

        Uses super-source and super-sink technique for multi-terminal cut.
        """
        if not entries_a or not entries_b:
            return set()

        # Filter to nodes actually in graph
        entries_a = [n for n in entries_a if n in G.nodes]
        entries_b = [n for n in entries_b if n in G.nodes]

        if not entries_a or not entries_b:
            return set()

        # Create super-source and super-sink
        super_source = "super_source"
        super_sink = "super_sink"

        G_augmented = G.copy()
        G_augmented.add_node(super_source)
        G_augmented.add_node(super_sink)

        # Connect super_source to all entries_a with infinite capacity
        for entry in entries_a:
            G_augmented.add_edge(super_source, entry, capacity=float("inf"))

        # Connect all entries_b to super_sink with infinite capacity
        for entry in entries_b:
            G_augmented.add_edge(entry, super_sink, capacity=float("inf"))

        try:
            # Find minimum cut
            cut_value, partition = nx.minimum_cut(
                G_augmented, super_source, super_sink, capacity="capacity"
            )

            reachable, non_reachable = partition

            # Extract cut edges (excluding super edges)
            cut_edges = set()
            for u in reachable:
                if u == super_source:
                    continue
                for v in G_augmented.neighbors(u):
                    if v in non_reachable and v != super_sink:
                        cut_edges.add((min(u, v), max(u, v)))

            return cut_edges

        except nx.NetworkXError as e:
            logger.warning(f"Min-cut computation failed: {e}")
            return set()

    def _determine_modification_type(
        self, u: int, v: int, key: int, edge_data: dict
    ) -> tuple[str, Optional[str]]:
        """
        Determine whether to use modal filter or one-way conversion.

        Returns:
            Tuple of (modification_type, direction)
            direction is 'u_to_v' or 'v_to_u' for one-way, None for modal filter
        """
        highway = edge_data.get("highway", "residential")
        if isinstance(highway, list):
            highway = highway[0]

        hierarchy = HIERARCHY_MAP.get(highway, 6)

        # High-capacity roads (hierarchy <= 5): prefer one-way
        # Low-capacity roads: prefer modal filter
        if hierarchy <= 5:
            # Determine optimal direction
            direction = self._compute_optimal_one_way_direction(u, v)
            return "one_way", direction
        else:
            return "modal_filter", None

    def _compute_optimal_one_way_direction(self, u: int, v: int) -> str:
        """
        Compute optimal one-way direction for an edge.

        Chooses direction that maximizes same-sector reachability
        while minimizing cross-sector connectivity.
        """
        if not self.sectors:
            return "u_to_v"

        best_direction = "u_to_v"
        best_score = float("-inf")

        for direction in ["u_to_v", "v_to_u"]:
            # Create test graph with this edge made one-way
            test_graph = self.graph.copy()

            if direction == "u_to_v":
                # Remove v->u edges
                if test_graph.has_edge(v, u):
                    edges_to_remove = list(test_graph[v][u].keys())
                    for k in edges_to_remove:
                        test_graph.remove_edge(v, u, k)
            else:
                # Remove u->v edges
                if test_graph.has_edge(u, v):
                    edges_to_remove = list(test_graph[u][v].keys())
                    for k in edges_to_remove:
                        test_graph.remove_edge(u, v, k)

            score = self._evaluate_direction_score(test_graph)

            if score > best_score:
                best_score = score
                best_direction = direction

        return best_direction

    def _evaluate_direction_score(self, graph: nx.MultiDiGraph) -> float:
        """
        Evaluate how well a graph configuration satisfies the constraints.

        Higher score = better (more same-sector connectivity, less cross-sector).
        """
        score = 0.0

        for sector in range(self.num_sectors):
            entries = self.sectors.entry_points_by_sector.get(sector, [])

            for entry in entries:
                if entry not in graph.nodes:
                    continue

                # Reward: nodes reachable from same-sector entry
                try:
                    reachable = nx.descendants(graph, entry)
                    score += len(reachable)
                except nx.NetworkXError:
                    continue

                # Penalty: cross-sector entries reachable
                for other_sector in range(self.num_sectors):
                    if other_sector == sector:
                        continue

                    other_entries = self.sectors.entry_points_by_sector.get(
                        other_sector, []
                    )
                    for other_entry in other_entries:
                        if other_entry in reachable:
                            score -= 1000  # Heavy penalty

        return score

    def _generate_modifications(
        self, plan: ModificationPlan
    ) -> list[StreetModification]:
        """Generate StreetModification objects from the modification plan."""
        modifications = []

        # Modal filters
        for u, v, key in plan.modal_filters:
            if not self.graph.has_edge(u, v, key):
                continue

            edge_data = self.graph[u][v][key]
            osmid = self._normalize_osm_id(edge_data.get("osmid", 0))
            name = self._normalize_edge_name(edge_data.get("name"))

            node_u = self.graph.nodes.get(u, {})
            filter_coords = Coordinates(
                lat=(node_u.get("y", 0) + self.graph.nodes.get(v, {}).get("y", 0)) / 2,
                lon=(node_u.get("x", 0) + self.graph.nodes.get(v, {}).get("x", 0)) / 2,
            )

            modifications.append(
                StreetModification(
                    u=u,
                    v=v,
                    key=key,
                    osm_id=osmid,
                    name=name,
                    modification_type=ModificationType.MODAL_FILTER,
                    filter_location=filter_coords,
                    rationale="Modal filter to prevent cross-sector through traffic",
                )
            )

        # One-way conversions
        for (u, v, key), direction in plan.one_way_conversions.items():
            if not self.graph.has_edge(u, v, key):
                continue

            edge_data = self.graph[u][v][key]
            osmid = self._normalize_osm_id(edge_data.get("osmid", 0))
            name = self._normalize_edge_name(edge_data.get("name"))

            modifications.append(
                StreetModification(
                    u=u,
                    v=v,
                    key=key,
                    osm_id=osmid,
                    name=name,
                    modification_type=ModificationType.ONE_WAY,
                    direction=direction,
                    rationale=f"One-way conversion ({direction}) to block cross-sector paths",
                )
            )

        return modifications

    def _validate_modifications(
        self, modifications: list[StreetModification]
    ) -> list[ConstraintViolation]:
        """
        Apply modifications and check for remaining violations.

        Returns list of violations that still exist after modifications.
        """
        # Create modified graph
        modified_graph = self.graph.copy()

        for mod in modifications:
            if mod.modification_type == ModificationType.MODAL_FILTER:
                # Remove all edges between u and v (both directions)
                if modified_graph.has_edge(mod.u, mod.v):
                    edges = list(modified_graph[mod.u][mod.v].keys())
                    for k in edges:
                        modified_graph.remove_edge(mod.u, mod.v, k)
                if modified_graph.has_edge(mod.v, mod.u):
                    edges = list(modified_graph[mod.v][mod.u].keys())
                    for k in edges:
                        modified_graph.remove_edge(mod.v, mod.u, k)

            elif mod.modification_type == ModificationType.ONE_WAY:
                # Remove edges in blocked direction
                if mod.direction == "u_to_v":
                    # Keep u->v, remove v->u
                    if modified_graph.has_edge(mod.v, mod.u):
                        edges = list(modified_graph[mod.v][mod.u].keys())
                        for k in edges:
                            modified_graph.remove_edge(mod.v, mod.u, k)
                else:
                    # Keep v->u, remove u->v
                    if modified_graph.has_edge(mod.u, mod.v):
                        edges = list(modified_graph[mod.u][mod.v].keys())
                        for k in edges:
                            modified_graph.remove_edge(mod.u, mod.v, k)

        # Re-run violation detection on modified graph
        original_graph = self.graph
        self.graph = modified_graph

        remaining_violations = self._find_violations()

        self.graph = original_graph

        if remaining_violations:
            logger.warning(
                f"{len(remaining_violations)} violations remain after modifications"
            )

        return remaining_violations

    @staticmethod
    def _normalize_osm_id(osmid) -> int:
        """Normalize OSM ID to a single positive integer."""
        if osmid is None:
            return 0
        if isinstance(osmid, (list, tuple, set)):
            for item in osmid:
                value = ConstraintEnforcer._normalize_osm_id(item)
                if value:
                    return value
            return 0
        try:
            value = int(osmid)
        except (TypeError, ValueError):
            return 0
        return value if value > 0 else 0

    @staticmethod
    def _normalize_edge_name(name) -> Optional[str]:
        """Normalize edge name to a string or None."""
        if name is None:
            return None
        if isinstance(name, str):
            return name
        if isinstance(name, (list, tuple, set)):
            parts: list[str] = []
            items = name
            if isinstance(name, set):
                items = sorted((str(item) for item in name if item))
            for item in items:
                if not item:
                    continue
                if isinstance(item, (list, tuple, set)):
                    for sub in item:
                        if sub:
                            parts.append(str(sub))
                else:
                    parts.append(str(item))
            if not parts:
                return None
            seen: set[str] = set()
            unique: list[str] = []
            for part in parts:
                if part in seen:
                    continue
                seen.add(part)
                unique.append(part)
            return " / ".join(unique)
        return str(name)

    def get_modified_graph(
        self, modifications: list[StreetModification]
    ) -> nx.MultiDiGraph:
        """
        Return a copy of the graph with modifications applied.

        Useful for routing and further analysis.
        """
        modified_graph = self.graph.copy()

        for mod in modifications:
            if mod.modification_type == ModificationType.MODAL_FILTER:
                # Mark edges as vehicle-blocked (keep for bike/pedestrian routing)
                if modified_graph.has_edge(mod.u, mod.v):
                    for k in modified_graph[mod.u][mod.v]:
                        modified_graph[mod.u][mod.v][k]["vehicle_blocked"] = True
                if modified_graph.has_edge(mod.v, mod.u):
                    for k in modified_graph[mod.v][mod.u]:
                        modified_graph[mod.v][mod.u][k]["vehicle_blocked"] = True

            elif mod.modification_type == ModificationType.ONE_WAY:
                if mod.direction == "u_to_v":
                    if modified_graph.has_edge(mod.v, mod.u):
                        edges = list(modified_graph[mod.v][mod.u].keys())
                        for k in edges:
                            modified_graph.remove_edge(mod.v, mod.u, k)
                else:
                    if modified_graph.has_edge(mod.u, mod.v):
                        edges = list(modified_graph[mod.u][mod.v].keys())
                        for k in edges:
                            modified_graph.remove_edge(mod.u, mod.v, k)

        return modified_graph
