"""
Constraint Enforcer for Superblock Enter-Exit Rules.

This module implements the core graph theory algorithms to enforce the
superblock constraint: vehicles entering from one sector can only exit
from that same sector.

The algorithm uses minimum edge cuts to identify which edges need to be
modified (modal filters or one-way conversions) to eliminate cross-sector paths.
"""

import heapq
import itertools
import logging
import math
from dataclasses import dataclass

import networkx as nx
from shapely.geometry import Polygon

from app.models.schemas import (
    ConstraintViolation,
    Coordinates,
    EntryPoint,
    ModificationType,
    StreetModification,
)

logger = logging.getLogger(__name__)


# Road hierarchy for determining modification type (lower = more important)
HIERARCHY_MAP = {
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
    full_closures: list[tuple[int, int, int]]  # Street cuts / closures
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
        self.sectors: SectorAssignment | None = None

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

        # Step 2: Find all cross-sector violations
        violations = self._find_violations()

        modifications: list[StreetModification] = []
        remaining_violations: list[ConstraintViolation] = []
        if violations:
            logger.info(f"Found {len(violations)} cross-sector path violations")

            # Step 3: Compute minimum edge cuts
            plan = self._compute_modification_plan(violations)

            # Step 4: Generate modifications
            modifications = self._generate_modifications(plan)

            # Step 5: Apply modifications and validate
            remaining_violations = self._validate_modifications(modifications)

            if remaining_violations:
                modifications, remaining_violations = self._repair_remaining_violations(
                    modifications,
                    remaining_violations,
                )
        else:
            logger.info("No cross-sector paths found before modifications")

        # Minimum cuts optimize the number/cost of interventions, but a union of
        # pairwise cuts can occasionally strand streets that were reachable before
        # the plan. Fall back to directional entry territories when either the
        # cross-traffic invariant or the original access envelope is not preserved.
        if (
            remaining_violations
            or not self._preserves_entry_access(modifications)
            or not self._all_nodes_reachable(modifications)
        ):
            fallback = self._build_directional_territory_modifications()
            fallback = self._restore_local_entry_access(fallback)
            fallback_violations = self._validate_modifications(fallback)
            if (
                not fallback_violations
                and self._preserves_entry_access(fallback)
                and self._all_nodes_reachable(fallback)
            ):
                logger.info(
                    "Using connectivity-preserving territory plan (%s modifications)",
                    len(fallback),
                )
                modifications = fallback
                remaining_violations = []

        deduplicated = self._deduplicate_physical_modifications(modifications)
        if len(deduplicated) != len(modifications):
            modifications = deduplicated
            remaining_violations = self._validate_modifications(modifications)

        return modifications, remaining_violations

    @staticmethod
    def _deduplicate_physical_modifications(
        modifications: list[StreetModification],
    ) -> list[StreetModification]:
        """Return one implementable action per physical street connection."""
        seen: set[tuple[ModificationType, int, int, str | None]] = set()
        deduplicated: list[StreetModification] = []
        for modification in modifications:
            physical_u, physical_v = sorted((modification.u, modification.v))
            direction = (
                modification.direction
                if modification.modification_type
                in {ModificationType.ONE_WAY, ModificationType.TURN_RESTRICTION}
                else None
            )
            signature = (
                modification.modification_type,
                physical_u,
                physical_v,
                direction,
            )
            if signature in seen:
                continue
            seen.add(signature)
            deduplicated.append(modification)
        return deduplicated

    def _build_directional_territory_modifications(self) -> list[StreetModification]:
        """
        Build a guaranteed no-cross-traffic plan without losing existing access.

        A directed multi-source shortest-path expansion assigns every node that
        was originally reachable from an entry to that entry's cardinal sector.
        Every physical connection between different territories is then blocked.
        Shortest-path parent edges always stay inside one territory, so every
        originally reachable node remains reachable from at least one entry.
        """
        if not self.sectors:
            return []

        fixed_sectors = {
            node: sector
            for node, sector in self.sectors.node_to_sector.items()
            if node in self.graph
        }
        if not fixed_sectors:
            return []

        territory: dict[int, int] = dict(fixed_sectors)
        distances: dict[int, float] = {node: 0.0 for node in fixed_sectors}
        frontier: list[tuple[float, int, int]] = [
            (0.0, sector, node) for node, sector in fixed_sectors.items()
        ]
        heapq.heapify(frontier)

        while frontier:
            distance, sector, node = heapq.heappop(frontier)
            if distance != distances.get(node) or territory.get(node) != sector:
                continue

            for _u, neighbor, _key, edge_data in self.graph.out_edges(node, keys=True, data=True):
                fixed_sector = fixed_sectors.get(neighbor)
                if fixed_sector is not None and fixed_sector != sector:
                    continue

                length = max(0.001, float(edge_data.get("length", 1.0) or 1.0))
                candidate = distance + length
                current = distances.get(neighbor, float("inf"))
                current_sector = territory.get(neighbor, self.num_sectors)
                if candidate < current or (
                    math.isclose(candidate, current) and sector < current_sector
                ):
                    distances[neighbor] = candidate
                    territory[neighbor] = sector
                    heapq.heappush(frontier, (candidate, sector, neighbor))

        # One hard vehicle block removes all parallel edges in both directions,
        # so keep only the least disruptive representative per physical link.
        cross_territory_edges: dict[tuple[int, int], tuple[int, int, int, dict]] = {}
        for u, v, key, edge_data in self.graph.edges(keys=True, data=True):
            if u == v or u not in territory or v not in territory:
                continue
            if territory[u] == territory[v]:
                continue

            physical_edge = (min(u, v), max(u, v))
            current = cross_territory_edges.get(physical_edge)
            if current is None or self._edge_cut_cost(edge_data) < self._edge_cut_cost(current[3]):
                cross_territory_edges[physical_edge] = (u, v, key, edge_data)

        modifications: list[StreetModification] = []
        for u, v, key, edge_data in cross_territory_edges.values():
            mod_type = (
                "full_closure" if self._should_use_street_cut(u, v, edge_data) else "modal_filter"
            )
            modification = self._build_single_modification(
                u,
                v,
                key,
                edge_data,
                mod_type,
                None,
            )
            if modification is not None:
                modification.rationale = (
                    "Directional territory boundary: blocks cross-traffic while "
                    "preserving access from the assigned entry side"
                )
                modifications.append(modification)

        return modifications

    def _preserves_entry_access(self, modifications: list[StreetModification]) -> bool:
        """Return True when no node loses access from the set of entry points."""
        original_reachable = self._nodes_reachable_from_entries(self.graph)
        modified = self._apply_vehicle_modifications(modifications)
        modified_reachable = self._nodes_reachable_from_entries(modified)
        return original_reachable.issubset(modified_reachable)

    def _all_nodes_reachable(self, modifications: list[StreetModification]) -> bool:
        """Return True when every interior node can be entered from a boundary side."""
        modified = self._apply_vehicle_modifications(modifications)
        return self._nodes_reachable_from_entries(modified) == set(modified.nodes)

    def _restore_local_entry_access(
        self, modifications: list[StreetModification]
    ) -> list[StreetModification]:
        """
        Open missing local travel directions inside each separated territory.

        The territory filters are applied first. Shortest paths are then found in
        the remaining physical network, so a two-way conversion can never bridge
        a cross-sector filter. Revalidation still runs after these additions.
        """
        updated = list(modifications)
        modified = self._apply_vehicle_modifications(updated)
        sources = [entry for entry in self.entry_nodes if entry in modified]
        if not sources:
            return updated

        physical = modified.to_undirected()
        try:
            paths = nx.multi_source_dijkstra_path(physical, sources, weight="length")
        except nx.NetworkXError:
            return updated

        planned_links: set[tuple[int, int]] = set()
        unreachable = set(modified.nodes) - self._nodes_reachable_from_entries(modified)
        for target in sorted(unreachable):
            path = paths.get(target)
            if not path:
                continue
            for source, destination in itertools.pairwise(path):
                if modified.has_edge(source, destination):
                    continue
                physical_link = (min(source, destination), max(source, destination))
                if physical_link in planned_links:
                    continue
                if not self.graph.has_edge(destination, source):
                    continue

                edge_key, edge_data = min(
                    self.graph[destination][source].items(),
                    key=lambda item: float(item[1].get("length", 0) or 0),
                )
                source_node = self.graph.nodes.get(source, {})
                destination_node = self.graph.nodes.get(destination, {})
                intervention_location = Coordinates(
                    lat=(source_node.get("y", 0) + destination_node.get("y", 0)) / 2,
                    lon=(source_node.get("x", 0) + destination_node.get("x", 0)) / 2,
                )
                modification = StreetModification(
                    u=destination,
                    v=source,
                    key=edge_key,
                    osm_id=self._normalize_osm_id(edge_data.get("osmid", 0)),
                    name=self._normalize_edge_name(edge_data.get("name")),
                    modification_type=ModificationType.TWO_WAY,
                    filter_location=intervention_location,
                    rationale=(
                        "Open the missing local-access direction so this street can "
                        "be entered and exited within its directional territory"
                    ),
                )
                updated.append(modification)
                planned_links.add(physical_link)
                modified = self._apply_vehicle_modifications(updated)

        return updated

    def _nodes_reachable_from_entries(self, graph: nx.MultiDiGraph) -> set[int]:
        """Collect nodes reachable by vehicles from at least one entry."""
        reachable: set[int] = set()
        for entry in self.entry_nodes:
            if entry not in graph:
                continue
            reachable.add(entry)
            try:
                reachable.update(nx.descendants(graph, entry))
            except nx.NetworkXError:
                continue
        return reachable

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

        entry_points_by_sector: dict[int, list[int]] = {i: [] for i in range(self.num_sectors)}
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

    def _angle_to_sector(self, angle: float, sector_angles: list[tuple[float, float]]) -> int:
        """Convert an angle to a sector index."""
        if self.num_sectors < 1:
            return 0

        sector_size = 2 * math.pi / self.num_sectors
        start_offset = sector_angles[0][0] if sector_angles else -sector_size / 2
        normalized_angle = (angle - start_offset) % (2 * math.pi)
        sector = int(normalized_angle / sector_size)
        return min(self.num_sectors - 1, sector)

    def _find_violations(self) -> list[ConstraintViolation]:
        """
        Find all directed cross-sector paths (violations of the constraint).

        A violation exists when vehicle traffic can travel from an entry point
        in sector A to an entry point in sector B where A != B.
        """
        violations = []

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

                        violations.extend(
                            self._find_pair_violations(
                                entry_a,
                                sector_a,
                                entry_b,
                                sector_b,
                            )
                        )

        return violations

    def _find_pair_violations(
        self,
        entry_a: int,
        sector_a: int,
        entry_b: int,
        sector_b: int,
    ) -> list[ConstraintViolation]:
        """Check both travel directions for a pair of entry points."""
        directed_pairs = [
            (entry_a, sector_a, entry_b, sector_b),
            (entry_b, sector_b, entry_a, sector_a),
        ]
        violations: list[ConstraintViolation] = []

        for source, source_sector, target, target_sector in directed_pairs:
            try:
                if not nx.has_path(self.graph, source, target):
                    continue

                path = nx.shortest_path(self.graph, source, target)
                path_edges = list(itertools.pairwise(path))

                violations.append(
                    ConstraintViolation(
                        from_entry=self._node_to_entry_point(source, source_sector),
                        to_entry=self._node_to_entry_point(target, target_sector),
                        path_exists=True,
                        path_edges=path_edges,
                    )
                )
            except nx.NetworkXError:
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

    def _compute_modification_plan(self, violations: list[ConstraintViolation]) -> ModificationPlan:
        """
        Compute the minimum set of edge modifications to eliminate all violations.

        Uses minimum edge cut algorithm to find edges that, when removed/modified,
        disconnect cross-sector entry point pairs.
        """
        cut_edges: set[tuple[int, int]] = set()

        # Convert to undirected simple graph for min-cut calculation
        G_simple = nx.Graph()
        for u, v, _key, data in self.graph.edges(keys=True, data=True):
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
            sector_cuts = self._find_sector_disconnect_cut(G_simple, entries_a, entries_b)
            cut_edges.update(sector_cuts)
            processed_pairs.add(pair_key)

        # Determine modification type for each cut edge
        modal_filters: list[tuple[int, int, int]] = []
        one_way_conversions: dict[tuple[int, int, int], str] = {}
        full_closures: list[tuple[int, int, int]] = []

        for u, v in cut_edges:
            one_way_planned = False

            # Find the actual edge in the multigraph
            if self.graph.has_edge(u, v):
                for key, data in self.graph[u][v].items():
                    mod_type, direction = self._determine_modification_type(u, v, key, data)
                    if mod_type == "modal_filter":
                        modal_filters.append((u, v, key))
                    elif mod_type == "one_way":
                        one_way_conversions[(u, v, key)] = direction
                        one_way_planned = True
                    elif mod_type == "full_closure":
                        full_closures.append((u, v, key))

            # Also check reverse direction
            if self.graph.has_edge(v, u):
                for key, data in self.graph[v][u].items():
                    mod_type, direction = self._determine_modification_type(v, u, key, data)
                    if mod_type == "modal_filter":
                        if (v, u, key) not in modal_filters:
                            modal_filters.append((v, u, key))
                    elif mod_type == "one_way":
                        if one_way_planned:
                            continue
                        if (v, u, key) not in one_way_conversions:
                            one_way_conversions[(v, u, key)] = direction
                            one_way_planned = True
                    elif mod_type == "full_closure" and (v, u, key) not in full_closures:
                        full_closures.append((v, u, key))

        return ModificationPlan(
            modal_filters=modal_filters,
            one_way_conversions=one_way_conversions,
            full_closures=full_closures,
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
            _cut_value, partition = nx.minimum_cut(
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
    ) -> tuple[str, str | None]:
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
        # Very minor connectors: use a hard street cut
        # Other local roads: prefer modal filter
        if hierarchy <= 5:
            # Determine optimal direction
            direction = self._compute_optimal_one_way_direction(u, v)
            return "one_way", direction
        if self._should_use_street_cut(u, v, edge_data):
            return "full_closure", None
        else:
            return "modal_filter", None

    def _should_use_street_cut(self, u: int, v: int, edge_data: dict) -> bool:
        """Choose full closure for minor connector streets that best serve as cuts."""
        highway = edge_data.get("highway", "residential")
        if isinstance(highway, list):
            highway = highway[0]

        hierarchy = HIERARCHY_MAP.get(highway, 6)
        if highway in {"service", "pedestrian"}:
            return True

        length = float(edge_data.get("length", 0) or 0)
        undirected = self.graph.to_undirected(as_view=True)
        min_degree = min(undirected.degree(u), undirected.degree(v))

        return hierarchy >= 7 and length <= 80 and min_degree <= 3

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

                    other_entries = self.sectors.entry_points_by_sector.get(other_sector, [])
                    for other_entry in other_entries:
                        if other_entry in reachable:
                            score -= 1000  # Heavy penalty

        return score

    def _generate_modifications(self, plan: ModificationPlan) -> list[StreetModification]:
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

        # Street cuts / full closures
        for u, v, key in plan.full_closures:
            if not self.graph.has_edge(u, v, key):
                continue

            edge_data = self.graph[u][v][key]
            osmid = self._normalize_osm_id(edge_data.get("osmid", 0))
            name = self._normalize_edge_name(edge_data.get("name"))

            node_u = self.graph.nodes.get(u, {})
            cut_coords = Coordinates(
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
                    modification_type=ModificationType.FULL_CLOSURE,
                    filter_location=cut_coords,
                    rationale="Street cut to break cross-sector through traffic and segment the interior network",
                )
            )

        # One-way conversions
        for (u, v, key), direction in plan.one_way_conversions.items():
            if not self.graph.has_edge(u, v, key):
                continue

            edge_data = self.graph[u][v][key]
            osmid = self._normalize_osm_id(edge_data.get("osmid", 0))
            name = self._normalize_edge_name(edge_data.get("name"))
            node_u = self.graph.nodes.get(u, {})
            conversion_coords = Coordinates(
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
                    modification_type=ModificationType.ONE_WAY,
                    direction=direction,
                    filter_location=conversion_coords,
                    rationale=f"One-way conversion ({direction}) to block cross-sector paths",
                )
            )

        return modifications

    def _repair_remaining_violations(
        self,
        modifications: list[StreetModification],
        remaining_violations: list[ConstraintViolation],
    ) -> tuple[list[StreetModification], list[ConstraintViolation]]:
        """
        Greedily add additional low-cost modifications until remaining directed
        cross-sector paths are removed or no further progress is possible.
        """
        updated = list(modifications)
        max_iterations = max(1, self.graph.number_of_edges())

        for _ in range(max_iterations):
            if not remaining_violations:
                break

            extra_modifications = self._generate_greedy_repairs(
                remaining_violations,
                updated,
            )
            if not extra_modifications:
                break

            updated.extend(extra_modifications)
            remaining_violations = self._validate_modifications(updated)

        return updated, remaining_violations

    def _generate_greedy_repairs(
        self,
        violations: list[ConstraintViolation],
        existing_modifications: list[StreetModification],
    ) -> list[StreetModification]:
        """Pick cheap edges on still-violating paths and modify them."""
        existing_edges = {
            (mod.u, mod.v, mod.key, mod.modification_type, mod.direction)
            for mod in existing_modifications
        }
        repairs: list[StreetModification] = []

        for violation in violations:
            repair_edge = self._select_repair_edge(violation, existing_edges)
            if repair_edge is None:
                continue

            u, v, key = repair_edge
            edge_data = self.graph[u][v][key]
            mod_type, direction = self._determine_modification_type(u, v, key, edge_data)
            modification = self._build_single_modification(
                u,
                v,
                key,
                edge_data,
                mod_type,
                direction,
            )
            if modification is None:
                continue

            edge_signature = (
                modification.u,
                modification.v,
                modification.key,
                modification.modification_type,
                modification.direction,
            )
            if edge_signature in existing_edges:
                continue

            repairs.append(modification)
            existing_edges.add(edge_signature)

        return repairs

    def _select_repair_edge(
        self,
        violation: ConstraintViolation,
        existing_edges: set[tuple[int, int, int, ModificationType, str | None]],
    ) -> tuple[int, int, int] | None:
        """Choose the cheapest non-entry edge on a violating path."""
        best_edge: tuple[int, int, int] | None = None
        best_cost = float("inf")
        blocked_nodes = {
            violation.from_entry.node_id,
            violation.to_entry.node_id,
        }

        for u, v in violation.path_edges:
            for from_node, to_node in ((u, v), (v, u)):
                if from_node in blocked_nodes or to_node in blocked_nodes:
                    continue
                if not self.graph.has_edge(from_node, to_node):
                    continue

                for key, edge_data in self.graph[from_node][to_node].items():
                    mod_type, direction = self._determine_modification_type(
                        from_node,
                        to_node,
                        key,
                        edge_data,
                    )
                    signature = (
                        from_node,
                        to_node,
                        key,
                        ModificationType(mod_type),
                        direction,
                    )
                    if signature in existing_edges:
                        continue

                    cost = self._edge_cut_cost(edge_data)
                    if mod_type == "one_way":
                        cost += 0.5

                    if cost < best_cost:
                        best_cost = cost
                        best_edge = (from_node, to_node, key)

        return best_edge

    def _build_single_modification(
        self,
        u: int,
        v: int,
        key: int,
        edge_data: dict,
        mod_type: str,
        direction: str | None,
    ) -> StreetModification | None:
        """Create a StreetModification for a single edge."""
        osmid = self._normalize_osm_id(edge_data.get("osmid", 0))
        name = self._normalize_edge_name(edge_data.get("name"))
        node_u = self.graph.nodes.get(u, {})
        midpoint = Coordinates(
            lat=(node_u.get("y", 0) + self.graph.nodes.get(v, {}).get("y", 0)) / 2,
            lon=(node_u.get("x", 0) + self.graph.nodes.get(v, {}).get("x", 0)) / 2,
        )

        if mod_type == "modal_filter":
            return StreetModification(
                u=u,
                v=v,
                key=key,
                osm_id=osmid,
                name=name,
                modification_type=ModificationType.MODAL_FILTER,
                filter_location=midpoint,
                rationale="Additional modal filter to remove remaining cross-sector through traffic",
            )

        if mod_type == "full_closure":
            return StreetModification(
                u=u,
                v=v,
                key=key,
                osm_id=osmid,
                name=name,
                modification_type=ModificationType.FULL_CLOSURE,
                filter_location=midpoint,
                rationale="Additional street cut to eliminate remaining cross-sector routing",
            )

        if mod_type == "one_way":
            return StreetModification(
                u=u,
                v=v,
                key=key,
                osm_id=osmid,
                name=name,
                modification_type=ModificationType.ONE_WAY,
                direction=direction,
                filter_location=midpoint,
                rationale=f"Additional one-way conversion ({direction}) to eliminate remaining cross-sector routing",
            )

        return None

    def _validate_modifications(
        self, modifications: list[StreetModification]
    ) -> list[ConstraintViolation]:
        """
        Apply modifications and check for remaining violations.

        Returns list of violations that still exist after modifications.
        """
        modified_graph = self._apply_vehicle_modifications(modifications)

        # Re-run violation detection on modified graph
        original_graph = self.graph
        self.graph = modified_graph

        remaining_violations = self._find_violations()

        self.graph = original_graph

        if remaining_violations:
            logger.warning(f"{len(remaining_violations)} violations remain after modifications")

        return remaining_violations

    def _apply_vehicle_modifications(
        self, modifications: list[StreetModification]
    ) -> nx.MultiDiGraph:
        """Apply a plan to the vehicle graph used for validation and routing."""
        modified_graph = self.graph.copy()

        for mod in modifications:
            if mod.modification_type in {
                ModificationType.MODAL_FILTER,
                ModificationType.FULL_CLOSURE,
            }:
                if modified_graph.has_edge(mod.u, mod.v):
                    for key in list(modified_graph[mod.u][mod.v].keys()):
                        modified_graph.remove_edge(mod.u, mod.v, key)
                if modified_graph.has_edge(mod.v, mod.u):
                    for key in list(modified_graph[mod.v][mod.u].keys()):
                        modified_graph.remove_edge(mod.v, mod.u, key)
            elif mod.modification_type == ModificationType.ONE_WAY:
                if mod.direction == "u_to_v":
                    if modified_graph.has_edge(mod.v, mod.u):
                        for key in list(modified_graph[mod.v][mod.u].keys()):
                            modified_graph.remove_edge(mod.v, mod.u, key)
                elif modified_graph.has_edge(mod.u, mod.v):
                    for key in list(modified_graph[mod.u][mod.v].keys()):
                        modified_graph.remove_edge(mod.u, mod.v, key)
            elif mod.modification_type == ModificationType.TWO_WAY:
                if not self.graph.has_edge(mod.u, mod.v):
                    continue
                edge_data = self.graph[mod.u][mod.v].get(mod.key)
                if edge_data is None:
                    edge_data = next(iter(self.graph[mod.u][mod.v].values()), None)
                if edge_data is not None and not modified_graph.has_edge(mod.v, mod.u):
                    modified_graph.add_edge(mod.v, mod.u, **dict(edge_data))

        return modified_graph

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
    def _normalize_edge_name(name) -> str | None:
        """Normalize edge name to a string or None."""
        if name is None:
            return None
        if isinstance(name, str):
            return name
        if isinstance(name, (list, tuple, set)):
            parts: list[str] = []
            items = name
            if isinstance(name, set):
                items = sorted(str(item) for item in name if item)
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

    def get_modified_graph(self, modifications: list[StreetModification]) -> nx.MultiDiGraph:
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
            elif mod.modification_type == ModificationType.TWO_WAY:
                if not self.graph.has_edge(mod.u, mod.v):
                    continue
                edge_data = self.graph[mod.u][mod.v].get(mod.key)
                if edge_data is None:
                    edge_data = next(iter(self.graph[mod.u][mod.v].values()), None)
                if edge_data is not None and not modified_graph.has_edge(mod.v, mod.u):
                    modified_graph.add_edge(mod.v, mod.u, **dict(edge_data))
            elif mod.modification_type == ModificationType.FULL_CLOSURE:
                if modified_graph.has_edge(mod.u, mod.v):
                    edges = list(modified_graph[mod.u][mod.v].keys())
                    for k in edges:
                        modified_graph.remove_edge(mod.u, mod.v, k)
                if modified_graph.has_edge(mod.v, mod.u):
                    edges = list(modified_graph[mod.v][mod.u].keys())
                    for k in edges:
                        modified_graph.remove_edge(mod.v, mod.u, k)

        return modified_graph
