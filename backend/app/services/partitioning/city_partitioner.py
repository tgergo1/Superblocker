"""
City Partitioner for Superblock System.

This module partitions an entire city into non-overlapping superblocks
by identifying arterial roads and using them as boundaries.

The algorithm:
1. Extract arterial network (high-capacity roads + high-centrality roads)
2. Polygonize arterials to create enclosed cells
3. Optimize cell sizes (merge small, split large)
4. Enforce constraints for each superblock
5. Validate coverage and accessibility
"""

import networkx as nx
import osmnx as ox
import logging
import uuid
import math
from typing import Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from shapely.geometry import Polygon, Point, MultiPolygon, LineString, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree
import pyproj
from shapely.ops import transform

from app.models.schemas import (
    BoundingBox,
    Coordinates,
    EntryPoint,
    EnforcedSuperblock,
    CityPartition,
    StreetModification,
    UnreachableAddress,
    PartitionProgress,
)
from app.services.constraint.constraint_enforcer import ConstraintEnforcer

logger = logging.getLogger(__name__)


# Road types considered as arterials (boundaries)
ARTERIAL_TYPES = {"primary", "secondary", "tertiary", "primary_link", "secondary_link", "tertiary_link"}

# Road hierarchy for boundary suitability
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
class SuperblockCell:
    """Intermediate representation of a potential superblock."""
    polygon: Polygon
    area_hectares: float
    boundary_edges: list[tuple[int, int, int]]  # (u, v, key)
    interior_edges: list[tuple[int, int, int]]
    entry_nodes: list[int]


class CityPartitioner:
    """
    Partitions a city into superblocks with enforced enter-exit constraints.

    The partitioner uses arterial roads as boundaries and ensures each
    resulting superblock satisfies the core constraint: traffic entering
    from one sector can only exit from that same sector.
    """

    # Size parameters (Barcelona guidelines)
    DEFAULT_TARGET_HA = 12.0
    DEFAULT_MIN_HA = 6.0
    DEFAULT_MAX_HA = 20.0

    # Centrality threshold for adding roads to arterial network
    CENTRALITY_PERCENTILE = 75

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        bbox: BoundingBox,
        target_size_ha: float = DEFAULT_TARGET_HA,
        min_area_ha: float = DEFAULT_MIN_HA,
        max_area_ha: float = DEFAULT_MAX_HA,
        num_sectors: int = 4,
        progress_callback: Optional[Callable[[PartitionProgress], None]] = None,
    ):
        """
        Initialize the city partitioner.

        Args:
            graph: NetworkX MultiDiGraph of the street network (from OSMnx)
            bbox: Bounding box of the area to partition
            target_size_ha: Target superblock size in hectares
            min_area_ha: Minimum superblock size
            max_area_ha: Maximum superblock size
            num_sectors: Number of angular sectors per superblock
            progress_callback: Optional callback for progress updates
        """
        self.graph = graph
        self.bbox = bbox
        self.target_size_ha = target_size_ha
        self.min_area_ha = min_area_ha
        self.max_area_ha = max_area_ha
        self.num_sectors = num_sectors
        self.progress_callback = progress_callback

        # Convert graph to GeoDataFrames for spatial operations
        self.nodes_gdf = None
        self.edges_gdf = None

        # Results
        self.arterial_edges: set[tuple[int, int, int]] = set()
        self.cells: list[SuperblockCell] = []
        self.superblocks: list[EnforcedSuperblock] = []

    def partition(self) -> CityPartition:
        """
        Main method to partition the city into superblocks.

        Returns:
            CityPartition with all superblocks and statistics
        """
        self._report_progress("network", 0, "Preparing street network...")

        # Step 1: Prepare network data
        self._prepare_network()
        self._report_progress("network", 20, "Network prepared")

        # Step 2: Identify arterial network
        self._report_progress("arterials", 25, "Identifying arterial roads...")
        self._identify_arterials()
        self._report_progress("arterials", 40, f"Found {len(self.arterial_edges)} arterial edges")

        # Step 3: Create cells by polygonizing arterials
        self._report_progress("cells", 45, "Creating superblock cells...")
        self._create_cells()
        self._report_progress("cells", 55, f"Created {len(self.cells)} initial cells")

        # Step 4: Optimize cell sizes
        self._report_progress("cells", 60, "Optimizing cell sizes...")
        self._optimize_cell_sizes()
        self._report_progress("cells", 70, f"Optimized to {len(self.cells)} cells")

        # Step 5: Enforce constraints for each cell
        self._report_progress("constraints", 75, "Enforcing superblock constraints...")
        self._enforce_all_constraints()
        self._report_progress("constraints", 95, f"Created {len(self.superblocks)} superblocks")

        # Step 6: Compute statistics
        self._report_progress("complete", 100, "Partitioning complete")

        return self._build_result()

    def _report_progress(self, stage: str, percent: int, message: str):
        """Report progress through callback if available."""
        if self.progress_callback:
            self.progress_callback(
                PartitionProgress(
                    stage=stage,
                    percent=percent,
                    message=message,
                    current_superblock=len(self.superblocks) if stage == "constraints" else None,
                    total_superblocks=len(self.cells) if stage == "constraints" else None,
                )
            )

    def _prepare_network(self):
        """Convert graph to GeoDataFrames for spatial operations."""
        try:
            self.nodes_gdf, self.edges_gdf = ox.graph_to_gdfs(self.graph)
        except Exception as e:
            logger.error(f"Failed to convert graph to GeoDataFrames: {e}")
            raise

    def _identify_arterials(self):
        """
        Identify arterial roads that will form superblock boundaries.

        Uses two criteria:
        1. Road type (primary, secondary, tertiary)
        2. High betweenness centrality (top 25%)
        """
        # Criterion 1: Road type
        type_arterials = set()
        for u, v, key, data in self.graph.edges(keys=True, data=True):
            highway = data.get("highway", "")
            if isinstance(highway, list):
                highway = highway[0]
            if highway in ARTERIAL_TYPES:
                type_arterials.add((u, v, key))

        # Criterion 2: Betweenness centrality (for larger networks)
        centrality_arterials = set()
        num_nodes = self.graph.number_of_nodes()

        if num_nodes > 100:  # Only compute centrality for reasonably sized networks
            try:
                # Convert to undirected simple graph for centrality
                G_simple = nx.Graph()
                edge_mapping = {}  # simple edge -> original edges

                for u, v, key, data in self.graph.edges(keys=True, data=True):
                    simple_key = (min(u, v), max(u, v))
                    length = data.get("length", 1)

                    if simple_key not in edge_mapping:
                        edge_mapping[simple_key] = []
                        G_simple.add_edge(u, v, weight=length)
                    edge_mapping[simple_key].append((u, v, key))

                # Compute centrality (approximate for large networks)
                k = min(500, num_nodes) if num_nodes > 1000 else None
                centrality = nx.edge_betweenness_centrality(
                    G_simple, k=k, weight="weight", seed=42
                )

                # Find threshold
                values = list(centrality.values())
                if values:
                    threshold = sorted(values)[int(len(values) * self.CENTRALITY_PERCENTILE / 100)]

                    for edge, cent_value in centrality.items():
                        if cent_value >= threshold:
                            simple_key = (min(edge[0], edge[1]), max(edge[0], edge[1]))
                            if simple_key in edge_mapping:
                                centrality_arterials.update(edge_mapping[simple_key])

            except Exception as e:
                logger.warning(f"Centrality computation failed: {e}")

        # Combine both criteria
        self.arterial_edges = type_arterials.union(centrality_arterials)
        logger.info(
            f"Arterial network: {len(type_arterials)} by type, "
            f"{len(centrality_arterials)} by centrality, "
            f"{len(self.arterial_edges)} total"
        )

    def _create_cells(self):
        """
        Create superblock cells by polygonizing arterial roads.

        Uses Shapely's polygonize to find enclosed areas bounded by arterials.
        """
        # Extract arterial geometries
        arterial_lines = []
        for u, v, key in self.arterial_edges:
            if self.graph.has_edge(u, v, key):
                data = self.graph[u][v][key]
                geom = data.get("geometry")
                if geom is not None:
                    arterial_lines.append(geom)
                else:
                    # Create line from node coordinates
                    u_data = self.graph.nodes[u]
                    v_data = self.graph.nodes[v]
                    line = LineString([
                        (u_data["x"], u_data["y"]),
                        (v_data["x"], v_data["y"]),
                    ])
                    arterial_lines.append(line)

        if not arterial_lines:
            logger.warning("No arterial geometries found")
            return

        # Add bbox boundary to ensure edge cells are created
        bbox_polygon = box(
            self.bbox.west, self.bbox.south,
            self.bbox.east, self.bbox.north
        )
        arterial_lines.append(bbox_polygon.exterior)

        # Union and polygonize
        try:
            merged = unary_union(arterial_lines)
            polygons = list(polygonize(merged))
        except Exception as e:
            logger.error(f"Polygonization failed: {e}")
            return

        logger.info(f"Polygonized {len(arterial_lines)} lines into {len(polygons)} polygons")

        # Filter and create cells
        for polygon in polygons:
            if not polygon.is_valid:
                polygon = polygon.buffer(0)

            area_ha = self._calculate_area_hectares(polygon)

            # Skip very small or very large polygons
            if area_ha < 0.5:  # Less than 0.5 hectare - too small
                continue
            if area_ha > 100:  # More than 100 hectares - too large (probably exterior)
                continue

            # Find boundary and interior edges
            boundary_edges, interior_edges = self._classify_edges(polygon)

            # Find entry nodes (nodes on boundary that connect to interior)
            entry_nodes = self._find_entry_nodes(polygon, boundary_edges, interior_edges)

            self.cells.append(SuperblockCell(
                polygon=polygon,
                area_hectares=area_ha,
                boundary_edges=boundary_edges,
                interior_edges=interior_edges,
                entry_nodes=entry_nodes,
            ))

    def _calculate_area_hectares(self, polygon: Polygon) -> float:
        """Calculate polygon area in hectares using appropriate projection."""
        try:
            # Use UTM projection for accurate area calculation
            centroid = polygon.centroid
            utm_zone = int((centroid.x + 180) / 6) + 1
            hemisphere = "north" if centroid.y >= 0 else "south"

            proj_string = f"+proj=utm +zone={utm_zone} +{hemisphere} +datum=WGS84"
            project = pyproj.Transformer.from_crs(
                "EPSG:4326", proj_string, always_xy=True
            ).transform

            projected = transform(project, polygon)
            return projected.area / 10000  # m² to hectares

        except Exception as e:
            logger.warning(f"Area calculation failed: {e}")
            # Fallback: approximate using bounding box
            bounds = polygon.bounds
            width = abs(bounds[2] - bounds[0]) * 111000  # Approximate meters
            height = abs(bounds[3] - bounds[1]) * 111000
            return (width * height) / 10000

    def _classify_edges(
        self, polygon: Polygon
    ) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
        """
        Classify edges as boundary (arterial) or interior.

        Returns:
            Tuple of (boundary_edges, interior_edges)
        """
        boundary_edges = []
        interior_edges = []

        # Build spatial index for edges
        for u, v, key, data in self.graph.edges(keys=True, data=True):
            geom = data.get("geometry")
            if geom is None:
                u_data = self.graph.nodes.get(u, {})
                v_data = self.graph.nodes.get(v, {})
                if "x" not in u_data or "x" not in v_data:
                    continue
                geom = LineString([
                    (u_data["x"], u_data["y"]),
                    (v_data["x"], v_data["y"]),
                ])

            # Check if edge is inside or on boundary of polygon
            if polygon.contains(geom) or polygon.boundary.intersects(geom):
                edge_tuple = (u, v, key)

                if edge_tuple in self.arterial_edges:
                    boundary_edges.append(edge_tuple)
                elif polygon.contains(geom.centroid):
                    interior_edges.append(edge_tuple)

        return boundary_edges, interior_edges

    def _find_entry_nodes(
        self,
        polygon: Polygon,
        boundary_edges: list[tuple[int, int, int]],
        interior_edges: list[tuple[int, int, int]],
    ) -> list[int]:
        """
        Find nodes that serve as entry points into the superblock.

        Entry nodes are nodes that are part of both boundary and interior edges.
        """
        boundary_nodes = set()
        for u, v, _ in boundary_edges:
            boundary_nodes.add(u)
            boundary_nodes.add(v)

        interior_nodes = set()
        for u, v, _ in interior_edges:
            interior_nodes.add(u)
            interior_nodes.add(v)

        # Entry nodes are the intersection
        entry_nodes = boundary_nodes.intersection(interior_nodes)

        # Also include nodes that are on the polygon boundary
        boundary_buffer = polygon.boundary.buffer(0.0001)  # Small buffer for tolerance
        for node_id in interior_nodes:
            node_data = self.graph.nodes.get(node_id, {})
            if "x" in node_data and "y" in node_data:
                point = Point(node_data["x"], node_data["y"])
                if boundary_buffer.contains(point):
                    entry_nodes.add(node_id)

        return list(entry_nodes)

    def _optimize_cell_sizes(self):
        """
        Optimize cell sizes by merging small cells and splitting large ones.
        """
        iteration = 0
        max_iterations = 10

        while iteration < max_iterations:
            changed = False

            # Merge small cells
            merged = self._merge_small_cells()
            if merged:
                changed = True

            # Split large cells
            split = self._split_large_cells()
            if split:
                changed = True

            if not changed:
                break

            iteration += 1

        logger.info(f"Size optimization completed after {iteration + 1} iterations")

    def _merge_small_cells(self) -> bool:
        """Merge cells smaller than min_area_ha with neighbors."""
        merged = False
        new_cells = []
        skip_indices = set()

        # Build adjacency
        cell_adjacency = self._build_cell_adjacency()

        for i, cell in enumerate(self.cells):
            if i in skip_indices:
                continue

            if cell.area_hectares < self.min_area_ha:
                # Find best neighbor to merge with
                best_neighbor = None
                best_score = float("inf")

                for j in cell_adjacency.get(i, []):
                    if j in skip_indices:
                        continue

                    neighbor = self.cells[j]
                    combined_area = cell.area_hectares + neighbor.area_hectares

                    # Prefer merging with similar-sized neighbors
                    # that don't exceed max size
                    if combined_area <= self.max_area_ha:
                        size_diff = abs(combined_area - self.target_size_ha)
                        if size_diff < best_score:
                            best_score = size_diff
                            best_neighbor = j

                if best_neighbor is not None:
                    # Merge cells
                    merged_cell = self._merge_cells(cell, self.cells[best_neighbor])
                    new_cells.append(merged_cell)
                    skip_indices.add(i)
                    skip_indices.add(best_neighbor)
                    merged = True
                else:
                    new_cells.append(cell)
            else:
                new_cells.append(cell)

        self.cells = new_cells
        return merged

    def _split_large_cells(self) -> bool:
        """Split cells larger than max_area_ha."""
        split = False
        new_cells = []

        for cell in self.cells:
            if cell.area_hectares > self.max_area_ha:
                # Try to split
                split_cells = self._split_cell(cell)
                if len(split_cells) > 1:
                    new_cells.extend(split_cells)
                    split = True
                else:
                    new_cells.append(cell)
            else:
                new_cells.append(cell)

        self.cells = new_cells
        return split

    def _build_cell_adjacency(self) -> dict[int, list[int]]:
        """Build adjacency list for cells based on shared boundaries."""
        adjacency: dict[int, list[int]] = {i: [] for i in range(len(self.cells))}

        for i, cell_i in enumerate(self.cells):
            for j, cell_j in enumerate(self.cells):
                if i >= j:
                    continue

                # Check if cells share a boundary
                if cell_i.polygon.touches(cell_j.polygon) or \
                   cell_i.polygon.boundary.intersects(cell_j.polygon.boundary):
                    adjacency[i].append(j)
                    adjacency[j].append(i)

        return adjacency

    def _merge_cells(self, cell1: SuperblockCell, cell2: SuperblockCell) -> SuperblockCell:
        """Merge two cells into one."""
        merged_polygon = unary_union([cell1.polygon, cell2.polygon])

        if isinstance(merged_polygon, MultiPolygon):
            # Take the largest polygon if union creates multiple
            merged_polygon = max(merged_polygon.geoms, key=lambda p: p.area)

        # Recalculate boundary and interior edges
        boundary_edges, interior_edges = self._classify_edges(merged_polygon)
        entry_nodes = self._find_entry_nodes(merged_polygon, boundary_edges, interior_edges)

        return SuperblockCell(
            polygon=merged_polygon,
            area_hectares=self._calculate_area_hectares(merged_polygon),
            boundary_edges=boundary_edges,
            interior_edges=interior_edges,
            entry_nodes=entry_nodes,
        )

    def _split_cell(self, cell: SuperblockCell) -> list[SuperblockCell]:
        """
        Split a large cell into smaller ones.

        Tries to find an interior road that can serve as a new boundary.
        """
        # Find the best splitting line (an interior road that crosses the cell)
        best_split = None
        best_balance = float("inf")

        for u, v, key in cell.interior_edges:
            if not self.graph.has_edge(u, v, key):
                continue

            data = self.graph[u][v][key]
            highway = data.get("highway", "")
            if isinstance(highway, list):
                highway = highway[0]

            # Prefer higher-hierarchy roads for splitting
            hierarchy = HIERARCHY_MAP.get(highway, 6)
            if hierarchy > 5:  # Don't split on residential or lower
                continue

            geom = data.get("geometry")
            if geom is None:
                u_data = self.graph.nodes.get(u, {})
                v_data = self.graph.nodes.get(v, {})
                if "x" not in u_data or "x" not in v_data:
                    continue
                geom = LineString([
                    (u_data["x"], u_data["y"]),
                    (v_data["x"], v_data["y"]),
                ])

            # Try to split polygon with this line
            try:
                # Extend line to polygon boundary
                extended = self._extend_line_to_boundary(geom, cell.polygon)
                if extended is None:
                    continue

                # Split polygon
                result = self._split_polygon_with_line(cell.polygon, extended)
                if result is None or len(result) < 2:
                    continue

                # Check balance
                areas = [self._calculate_area_hectares(p) for p in result]
                balance = abs(areas[0] - areas[1])

                if balance < best_balance and all(a >= self.min_area_ha for a in areas):
                    best_balance = balance
                    best_split = result

            except Exception as e:
                logger.debug(f"Split attempt failed: {e}")
                continue

        if best_split is None:
            return [cell]

        # Create new cells from split polygons
        new_cells = []
        for polygon in best_split:
            if not polygon.is_valid:
                polygon = polygon.buffer(0)

            boundary_edges, interior_edges = self._classify_edges(polygon)
            entry_nodes = self._find_entry_nodes(polygon, boundary_edges, interior_edges)

            new_cells.append(SuperblockCell(
                polygon=polygon,
                area_hectares=self._calculate_area_hectares(polygon),
                boundary_edges=boundary_edges,
                interior_edges=interior_edges,
                entry_nodes=entry_nodes,
            ))

        return new_cells

    def _extend_line_to_boundary(
        self, line: LineString, polygon: Polygon
    ) -> Optional[LineString]:
        """Extend a line to intersect the polygon boundary on both ends."""
        try:
            coords = list(line.coords)
            if len(coords) < 2:
                return None

            start = Point(coords[0])
            end = Point(coords[-1])

            # Direction vectors
            dx = coords[-1][0] - coords[0][0]
            dy = coords[-1][1] - coords[0][1]
            length = math.sqrt(dx*dx + dy*dy)
            if length == 0:
                return None

            dx /= length
            dy /= length

            # Extend factor (enough to reach boundary)
            extend = polygon.bounds[2] - polygon.bounds[0]  # Width of bbox

            # Extend both ends
            new_start = (coords[0][0] - dx * extend, coords[0][1] - dy * extend)
            new_end = (coords[-1][0] + dx * extend, coords[-1][1] + dy * extend)

            extended = LineString([new_start] + list(coords) + [new_end])

            # Clip to polygon
            clipped = extended.intersection(polygon)
            if clipped.is_empty:
                return None

            if isinstance(clipped, LineString):
                return clipped

            return None

        except Exception:
            return None

    def _split_polygon_with_line(
        self, polygon: Polygon, line: LineString
    ) -> Optional[list[Polygon]]:
        """Split a polygon with a line."""
        try:
            from shapely.ops import split as shapely_split

            result = shapely_split(polygon, line)
            polygons = [g for g in result.geoms if isinstance(g, Polygon)]

            if len(polygons) >= 2:
                return polygons

            return None

        except Exception:
            return None

    def _enforce_all_constraints(self):
        """Enforce enter-exit constraints for all cells."""
        total_cells = len(self.cells)

        for i, cell in enumerate(self.cells):
            self._report_progress(
                "constraints",
                75 + int(20 * i / total_cells),
                f"Processing superblock {i + 1}/{total_cells}",
            )

            superblock = self._enforce_cell_constraints(cell, i)
            if superblock is not None:
                self.superblocks.append(superblock)

    def _enforce_cell_constraints(
        self, cell: SuperblockCell, index: int
    ) -> Optional[EnforcedSuperblock]:
        """
        Enforce constraints for a single cell and create an EnforcedSuperblock.
        """
        if len(cell.entry_nodes) < 2:
            # Not enough entry points to have cross-sector paths
            return self._create_simple_superblock(cell, index)

        # Build interior subgraph
        interior_graph = self._build_interior_subgraph(cell)

        if interior_graph.number_of_edges() == 0:
            return self._create_simple_superblock(cell, index)

        # Create constraint enforcer
        enforcer = ConstraintEnforcer(
            interior_graph=interior_graph,
            boundary_polygon=cell.polygon,
            entry_node_ids=cell.entry_nodes,
            num_sectors=self.num_sectors,
        )

        # Enforce constraints
        try:
            modifications, violations = enforcer.enforce_constraints()
        except Exception as e:
            logger.warning(f"Constraint enforcement failed for cell {index}: {e}")
            return self._create_simple_superblock(cell, index)

        # Build entry points with sector info
        entry_points = []
        if enforcer.sectors:
            for sector, nodes in enforcer.sectors.entry_points_by_sector.items():
                for node_id in nodes:
                    node_data = self.graph.nodes.get(node_id, {})
                    entry_points.append(EntryPoint(
                        node_id=node_id,
                        sector=sector,
                        coordinates=Coordinates(
                            lat=node_data.get("y", 0),
                            lon=node_data.get("x", 0),
                        ),
                        boundary_road_id=0,
                    ))

        # Check accessibility (find unreachable addresses)
        unreachable = self._find_unreachable_addresses(
            interior_graph, modifications, cell.entry_nodes, enforcer.sectors
        )

        # Create EnforcedSuperblock
        return EnforcedSuperblock(
            id=f"sb_{index}_{uuid.uuid4().hex[:8]}",
            geometry=self._polygon_to_geojson(cell.polygon),
            area_hectares=cell.area_hectares,
            num_sectors=self.num_sectors,
            boundary_roads=self._collect_boundary_osm_ids(cell.boundary_edges),
            entry_points=entry_points,
            modifications=modifications,
            constraint_validated=len(violations) == 0,
            all_addresses_reachable=len(unreachable) == 0,
            unreachable_addresses=unreachable,
            interior_roads_count=len(cell.interior_edges),
            modal_filter_count=sum(
                1 for m in modifications
                if m.modification_type.value == "modal_filter"
            ),
            one_way_conversion_count=sum(
                1 for m in modifications
                if m.modification_type.value == "one_way"
            ),
        )

    def _create_simple_superblock(
        self, cell: SuperblockCell, index: int
    ) -> EnforcedSuperblock:
        """Create a superblock without constraint enforcement (no cross-sector paths possible)."""
        entry_points = []
        for node_id in cell.entry_nodes:
            node_data = self.graph.nodes.get(node_id, {})
            entry_points.append(EntryPoint(
                node_id=node_id,
                sector=0,
                coordinates=Coordinates(
                    lat=node_data.get("y", 0),
                    lon=node_data.get("x", 0),
                ),
                boundary_road_id=0,
            ))

        return EnforcedSuperblock(
            id=f"sb_{index}_{uuid.uuid4().hex[:8]}",
            geometry=self._polygon_to_geojson(cell.polygon),
            area_hectares=cell.area_hectares,
            num_sectors=self.num_sectors,
            boundary_roads=self._collect_boundary_osm_ids(cell.boundary_edges),
            entry_points=entry_points,
            modifications=[],
            constraint_validated=True,
            all_addresses_reachable=True,
            unreachable_addresses=[],
            interior_roads_count=len(cell.interior_edges),
            modal_filter_count=0,
            one_way_conversion_count=0,
        )

    def _build_interior_subgraph(self, cell: SuperblockCell) -> nx.MultiDiGraph:
        """Build a subgraph containing only interior edges."""
        subgraph = nx.MultiDiGraph()

        for u, v, key in cell.interior_edges:
            if not self.graph.has_edge(u, v, key):
                continue

            # Add nodes
            if u not in subgraph.nodes:
                subgraph.add_node(u, **self.graph.nodes[u])
            if v not in subgraph.nodes:
                subgraph.add_node(v, **self.graph.nodes[v])

            # Add edge
            subgraph.add_edge(u, v, key=key, **self.graph[u][v][key])

        return subgraph

    def _find_unreachable_addresses(
        self,
        graph: nx.MultiDiGraph,
        modifications: list[StreetModification],
        entry_nodes: list[int],
        sectors,
    ) -> list[UnreachableAddress]:
        """
        Find interior nodes that become unreachable after modifications.
        """
        if not entry_nodes:
            return []

        # Apply modifications
        modified_graph = graph.copy()
        for mod in modifications:
            if mod.modification_type.value == "modal_filter":
                # Remove edges
                if modified_graph.has_edge(mod.u, mod.v):
                    for k in list(modified_graph[mod.u][mod.v].keys()):
                        modified_graph.remove_edge(mod.u, mod.v, k)
                if modified_graph.has_edge(mod.v, mod.u):
                    for k in list(modified_graph[mod.v][mod.u].keys()):
                        modified_graph.remove_edge(mod.v, mod.u, k)
            elif mod.modification_type.value == "one_way" and mod.direction:
                if mod.direction == "u_to_v":
                    if modified_graph.has_edge(mod.v, mod.u):
                        for k in list(modified_graph[mod.v][mod.u].keys()):
                            modified_graph.remove_edge(mod.v, mod.u, k)
                else:
                    if modified_graph.has_edge(mod.u, mod.v):
                        for k in list(modified_graph[mod.u][mod.v].keys()):
                            modified_graph.remove_edge(mod.u, mod.v, k)

        # Find all nodes reachable from any entry point
        reachable = set()
        for entry in entry_nodes:
            if entry in modified_graph.nodes:
                try:
                    reachable.update(nx.descendants(modified_graph, entry))
                    reachable.add(entry)
                except nx.NetworkXError:
                    pass

        # Find unreachable nodes
        unreachable = []
        for node in modified_graph.nodes:
            if node not in reachable and node not in entry_nodes:
                node_data = modified_graph.nodes[node]

                # Determine nearest entry sector
                nearest_sector = 0
                if sectors:
                    for sector, entries in sectors.entry_points_by_sector.items():
                        if any(e in reachable for e in entries):
                            nearest_sector = sector
                            break

                unreachable.append(UnreachableAddress(
                    node_id=node,
                    coordinates=Coordinates(
                        lat=node_data.get("y", 0),
                        lon=node_data.get("x", 0),
                    ),
                    nearest_entry_sector=nearest_sector,
                    reason="No path from any entry point after modifications",
                ))

        return unreachable

    def _polygon_to_geojson(self, polygon: Polygon) -> dict:
        """Convert Shapely Polygon to GeoJSON dict."""
        from shapely.geometry import mapping
        return mapping(polygon)

    @staticmethod
    def _normalize_osm_ids(osmid) -> list[int]:
        """Normalize OSM IDs into a flat list of positive integers."""
        if osmid is None:
            return []
        if isinstance(osmid, (list, tuple, set)):
            ids: list[int] = []
            for item in osmid:
                ids.extend(CityPartitioner._normalize_osm_ids(item))
            return ids
        try:
            value = int(osmid)
        except (TypeError, ValueError):
            return []
        return [value] if value > 0 else []

    def _collect_boundary_osm_ids(
        self, boundary_edges: list[tuple[int, int, int]]
    ) -> list[int]:
        """Collect and deduplicate boundary road OSM IDs."""
        osm_ids: list[int] = []
        for u, v, key in boundary_edges:
            if not self.graph.has_edge(u, v, key):
                continue
            edge_data = self.graph[u][v][key]
            osm_ids.extend(self._normalize_osm_ids(edge_data.get("osmid", 0)))

        seen: set[int] = set()
        deduped: list[int] = []
        for osmid in osm_ids:
            if osmid in seen:
                continue
            seen.add(osmid)
            deduped.append(osmid)

        return deduped

    def _build_result(self) -> CityPartition:
        """Build the final CityPartition result."""
        # Calculate statistics
        total_area = sum(sb.area_hectares for sb in self.superblocks)

        bbox_area = self._calculate_area_hectares(box(
            self.bbox.west, self.bbox.south,
            self.bbox.east, self.bbox.north
        ))

        coverage = (total_area / bbox_area * 100) if bbox_area > 0 else 0

        total_filters = sum(sb.modal_filter_count for sb in self.superblocks)
        total_oneway = sum(sb.one_way_conversion_count for sb in self.superblocks)
        total_unreachable = sum(len(sb.unreachable_addresses) for sb in self.superblocks)

        # Arterial road OSM IDs
        arterial_osm_ids = []
        for u, v, key in self.arterial_edges:
            if self.graph.has_edge(u, v, key):
                osmid = self.graph[u][v][key].get("osmid", 0)
                arterial_osm_ids.extend(self._normalize_osm_ids(osmid))

        return CityPartition(
            superblocks=self.superblocks,
            arterial_network=list(set(arterial_osm_ids)),
            bbox=self.bbox,
            total_area_hectares=total_area,
            coverage_percent=coverage,
            total_superblocks=len(self.superblocks),
            total_modal_filters=total_filters,
            total_one_way_conversions=total_oneway,
            total_unreachable_addresses=total_unreachable,
        )
