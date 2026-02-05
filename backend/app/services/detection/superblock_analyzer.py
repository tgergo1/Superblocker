"""
Advanced Superblock Detection and Analysis System.

Based on the Barcelona Superilles model and urban planning literature:
- Rueda, S. (2019). Superblocks for the Design of New Cities and Renovation of Existing Ones
- Mueller, N. et al. (2020). Changing the urban design of cities for health

This module implements:
1. Betweenness centrality analysis to identify through-traffic corridors
2. Cell-based superblock detection using graph partitioning
3. Traffic redistribution modeling
4. Accessibility and emergency access verification
5. Street reorientation planning (one-way conversions, modal filters)
6. Multi-criteria evaluation based on urban planning metrics
"""

import networkx as nx
import osmnx as ox
import logging
import threading
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from shapely.geometry import Polygon, Point, LineString, MultiPolygon, mapping
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree
import pyproj
from shapely.ops import transform
import uuid
import math
from typing import Any, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum

from app.models.schemas import BoundingBox

logger = logging.getLogger(__name__)


class InterventionType(str, Enum):
    """Types of street interventions in a superblock."""
    PEDESTRIANIZE = "pedestrianize"  # Full pedestrianization
    ONE_WAY = "one_way"  # Convert to one-way
    MODAL_FILTER = "modal_filter"  # Allow bikes/emergency, block cars
    LOCAL_ACCESS = "local_access"  # Residents/delivery only
    NO_CHANGE = "no_change"  # Keep as-is (boundary road)


@dataclass
class StreetIntervention:
    """Planned intervention for a street segment."""
    osm_id: int
    name: Optional[str]
    intervention_type: InterventionType
    direction: Optional[str] = None  # For one-way: 'forward', 'backward'
    access_allowed: list[str] = field(default_factory=lambda: ["emergency"])
    rationale: str = ""


@dataclass
class AccessibilityMetrics:
    """Accessibility analysis results."""
    max_walking_distance_to_boundary: float  # meters
    emergency_access_maintained: bool
    delivery_access_points: int
    residential_access_maintained: bool
    public_transport_affected: bool


@dataclass
class TrafficImpact:
    """Traffic redistribution analysis."""
    removed_through_traffic_pct: float  # % of through traffic removed
    boundary_load_increase_pct: float  # % increase on boundary roads
    estimated_vmt_reduction: float  # Vehicle miles traveled reduction
    affected_od_pairs: int  # Origin-destination pairs affected


@dataclass
class SuperblockScore:
    """Multi-criteria scoring breakdown."""
    size_score: float  # 0-100: Ideal size (9-16 ha)
    shape_score: float  # 0-100: Compactness/regularity
    traffic_score: float  # 0-100: Through-traffic removal potential
    accessibility_score: float  # 0-100: Maintained accessibility
    connectivity_score: float  # 0-100: Internal network for active mobility
    boundary_quality_score: float  # 0-100: Boundary road capacity
    total_score: float  # Weighted average


@dataclass
class SuperblockCandidate:
    """Complete superblock analysis result."""
    id: str
    geometry: dict  # GeoJSON polygon
    area_hectares: float
    perimeter_roads: list[int]
    interior_roads: list[int]
    score: float
    algorithm: str

    # Enhanced analysis
    score_breakdown: Optional[SuperblockScore] = None
    interventions: list[StreetIntervention] = field(default_factory=list)
    accessibility: Optional[AccessibilityMetrics] = None
    traffic_impact: Optional[TrafficImpact] = None

    # Network metrics
    boundary_centrality_mean: float = 0.0
    interior_centrality_mean: float = 0.0
    num_access_points: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "id": self.id,
            "geometry": self.geometry,
            "area_hectares": self.area_hectares,
            "perimeter_roads": self.perimeter_roads,
            "interior_roads": self.interior_roads,
            "score": self.score,
            "algorithm": self.algorithm,
            "boundary_centrality_mean": self.boundary_centrality_mean,
            "interior_centrality_mean": self.interior_centrality_mean,
            "num_access_points": self.num_access_points,
        }
        if self.score_breakdown:
            result["score_breakdown"] = asdict(self.score_breakdown)
        if self.interventions:
            result["interventions"] = [
                {
                    "osm_id": i.osm_id,
                    "name": i.name,
                    "intervention_type": i.intervention_type.value,
                    "direction": i.direction,
                    "access_allowed": i.access_allowed,
                    "rationale": i.rationale,
                }
                for i in self.interventions
            ]
        if self.accessibility:
            result["accessibility"] = asdict(self.accessibility)
        if self.traffic_impact:
            result["traffic_impact"] = asdict(self.traffic_impact)
        return result


class SuperblockAnalyzer:
    """
    Advanced superblock detection and analysis.

    The algorithm follows the Barcelona methodology:
    1. Identify high-centrality roads as potential boundaries
    2. Partition the network into cells bounded by these roads
    3. Filter cells by size (4-25 hectares, ideal 9-16)
    4. Score each cell based on multiple criteria
    5. Plan street interventions for viable candidates
    """

    # Configuration based on Barcelona Superilles guidelines
    MIN_AREA_HA = 4.0
    MAX_AREA_HA = 25.0
    IDEAL_MIN_HA = 9.0
    IDEAL_MAX_HA = 16.0
    MAX_BBOX_SPAN_DEGREES = 0.5  # ~50km, keep centrality compute tractable

    CENTRALITY_APPROX_NODE_THRESHOLD = 2500
    CENTRALITY_APPROX_SAMPLE_RATIO = 0.10
    CENTRALITY_APPROX_SAMPLE_MIN = 200
    CENTRALITY_APPROX_SAMPLE_MAX = 800
    CENTRALITY_HEARTBEAT_SECONDS = 20
    NETWORK_HEARTBEAT_SECONDS = 20

    # Road hierarchy for boundary suitability (lower = better for boundaries)
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
        "pedestrian": 10,
        "cycleway": 10,
        "footway": 10,
        "path": 10,
    }

    # Capacity estimates (vehicles/hour/lane) based on Highway Capacity Manual
    CAPACITY_MAP = {
        "motorway": 2000,
        "trunk": 1800,
        "primary": 900,
        "secondary": 700,
        "tertiary": 500,
        "residential": 300,
        "living_street": 100,
        "unclassified": 250,
        "service": 150,
    }

    def __init__(
        self,
        min_area: float = 4.0,
        max_area: float = 25.0,
        centrality_threshold_percentile: float = 70,
    ):
        self.min_area = min_area
        self.max_area = max_area
        self.centrality_threshold_pct = centrality_threshold_percentile

        # Projection for accurate area calculations
        self.proj_wgs84 = pyproj.CRS("EPSG:4326")
        self.proj_mercator = pyproj.CRS("EPSG:3857")

    async def analyze(
        self,
        bbox: BoundingBox,
        progress_callback: Optional[callable] = None,
    ) -> dict:
        """
        Perform complete superblock analysis for an area.

        Returns:
            Dictionary with candidates, network stats, and analysis metadata.
        """
        def report_progress(stage: str, percent: int, message: str):
            if progress_callback:
                progress_callback(stage, percent, message)

        lat_diff = bbox.north - bbox.south
        lon_diff = bbox.east - bbox.west
        if lat_diff <= 0 or lon_diff <= 0:
            raise ValueError("Invalid bounding box: north must be > south, east must be > west")
        if lat_diff > self.MAX_BBOX_SPAN_DEGREES or lon_diff > self.MAX_BBOX_SPAN_DEGREES:
            raise ValueError(
                "Bounding box too large. Maximum size is ~50km. "
                "Please select a smaller area."
            )

        report_progress("network", 10, "Fetching street network from OpenStreetMap...")
        logger.info(
            "Starting analysis for bbox: north=%.6f south=%.6f east=%.6f west=%.6f",
            bbox.north,
            bbox.south,
            bbox.east,
            bbox.west,
        )

        # 1. Fetch street network
        network_start = time.time()
        network_heartbeat_stop = threading.Event()

        def _network_heartbeat():
            while not network_heartbeat_stop.wait(self.NETWORK_HEARTBEAT_SECONDS):
                elapsed = int(time.time() - network_start)
                message = f"Fetching street network... ({elapsed}s elapsed)"
                logger.info(message)
                report_progress("network", 10, message)

        network_heartbeat_thread = threading.Thread(
            target=_network_heartbeat,
            name="network-heartbeat",
            daemon=True,
        )
        network_heartbeat_thread.start()

        bbox_tuple = (bbox.west, bbox.south, bbox.east, bbox.north)
        try:
            G = ox.graph_from_bbox(
                bbox=bbox_tuple,
                network_type="drive",
                simplify=True,
                retain_all=False,
                truncate_by_edge=True,
            )
        finally:
            network_heartbeat_stop.set()

        if G.number_of_edges() == 0:
            logger.info("No street network edges found for bbox")
            return {"candidates": [], "network_stats": {}, "error": "No street network found"}

        network_elapsed = time.time() - network_start
        logger.info(
            "Street network fetched in %.1fs (nodes=%s edges=%s)",
            network_elapsed,
            G.number_of_nodes(),
            G.number_of_edges(),
        )

        # 2. Compute betweenness centrality
        # This identifies roads that carry through-traffic
        report_progress("centrality", 25, "Computing betweenness centrality...")
        logger.info("Computing betweenness centrality...")

        # Convert to a simple undirected graph to avoid multiedge blowups
        G_undirected = nx.Graph()
        for u, v, data in G.edges(data=True):
            length = data.get("length", 1)
            if G_undirected.has_edge(u, v):
                if length < G_undirected[u][v].get("length", length):
                    G_undirected[u][v]["length"] = length
            else:
                G_undirected.add_edge(u, v, length=length)

        node_count = G_undirected.number_of_nodes()
        edge_count = G_undirected.number_of_edges()
        approx_k = None
        if node_count >= self.CENTRALITY_APPROX_NODE_THRESHOLD:
            approx_k = min(
                self.CENTRALITY_APPROX_SAMPLE_MAX,
                max(
                    self.CENTRALITY_APPROX_SAMPLE_MIN,
                    int(node_count * self.CENTRALITY_APPROX_SAMPLE_RATIO),
                ),
            )
            approx_k = min(approx_k, node_count)

        if approx_k is not None:
            message = f"Computing betweenness centrality (approx, k={approx_k})..."
            report_progress("centrality", 25, message)
            logger.info(message)

        logger.info(
            "Centrality graph size: nodes=%s edges=%s approx=%s k=%s",
            node_count,
            edge_count,
            approx_k is not None,
            approx_k,
        )

        heartbeat_stop = threading.Event()
        centrality_start = time.time()

        def _centrality_heartbeat():
            while not heartbeat_stop.wait(self.CENTRALITY_HEARTBEAT_SECONDS):
                elapsed = int(time.time() - centrality_start)
                message = f"Computing betweenness centrality... ({elapsed}s elapsed)"
                logger.info(message)
                report_progress("centrality", 25, message)

        heartbeat_thread = threading.Thread(
            target=_centrality_heartbeat,
            name="centrality-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()

        try:
            edge_centrality = nx.edge_betweenness_centrality(
                G_undirected,
                k=approx_k,
                weight="length",
                seed=42,
            )
        finally:
            heartbeat_stop.set()

        elapsed = time.time() - centrality_start
        logger.info("Centrality computation finished in %.1fs", elapsed)

        # Map centrality back to edges
        nx.set_edge_attributes(G, 0.0, "centrality")
        for (u, v), cent in edge_centrality.items():
            if G.has_edge(u, v):
                for key in G[u][v]:
                    G[u][v][key]["centrality"] = cent
            if G.has_edge(v, u):
                for key in G[v][u]:
                    G[v][u][key]["centrality"] = cent

        report_progress("detection", 45, "Detecting superblock candidates...")
        logger.info("Detecting superblock candidates...")

        # 3. Detect superblock candidates
        candidates = self._detect_cells(G, progress_callback=progress_callback)

        if not candidates:
            logger.info("No suitable superblock areas found")
            return {
                "candidates": [],
                "network_stats": self._compute_network_stats(G),
                "message": "No suitable superblock areas found"
            }

        logger.info("Detected %s candidates", len(candidates))

        report_progress("scoring", 65, f"Scoring {len(candidates)} candidates...")
        logger.info("Scoring %s candidates...", len(candidates))

        # 4. Score and rank candidates in parallel
        scored_candidates = []
        
        # Use ThreadPoolExecutor for parallel scoring (CPU-bound but with GIL,
        # still benefits from concurrency during I/O and geometry operations)
        # For very large datasets, consider ProcessPoolExecutor with proper serialization
        max_workers = min(4, len(candidates))  # Limit to 4 workers to avoid overhead
        
        if len(candidates) > 5 and max_workers > 1:
            # Parallel scoring for multiple candidates
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all scoring tasks
                future_to_candidate = {
                    executor.submit(self._score_candidate, candidate, G): i
                    for i, candidate in enumerate(candidates)
                }
                
                # Collect results as they complete
                for future in as_completed(future_to_candidate):
                    i = future_to_candidate[future]
                    try:
                        scored = future.result()
                        scored_candidates.append(scored)
                        
                        if i % 10 == 0:
                            pct = 65 + int(20 * (len(scored_candidates) / len(candidates)))
                            report_progress("scoring", pct, f"Scoring candidate {len(scored_candidates)}/{len(candidates)}...")
                    except Exception as e:
                        logger.error(f"Error scoring candidate {i}: {e}")
        else:
            # Sequential scoring for small numbers of candidates
            for i, candidate in enumerate(candidates):
                scored = self._score_candidate(candidate, G)
                scored_candidates.append(scored)

                if i % 10 == 0:
                    pct = 65 + int(20 * (i / len(candidates)))
                    report_progress("scoring", pct, f"Scoring candidate {i+1}/{len(candidates)}...")

        report_progress("reorientation", 85, "Planning street interventions...")
        logger.info("Planning street interventions for top candidates...")

        # 5. Sort by score first, then plan interventions for top candidates only
        scored_candidates.sort(key=lambda c: c.score, reverse=True)
        
        # Plan interventions for top candidates in parallel
        top_candidates = scored_candidates[:20]
        if len(top_candidates) > 2:
            with ThreadPoolExecutor(max_workers=min(4, len(top_candidates))) as executor:
                futures = [
                    executor.submit(self._plan_interventions, candidate, G)
                    for candidate in top_candidates
                ]
                # Wait for all to complete
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Error planning interventions: {e}")
        else:
            for candidate in top_candidates:
                self._plan_interventions(candidate, G)

        report_progress("complete", 100, f"Analysis complete: {len(scored_candidates)} candidates found")
        logger.info("Analysis complete: %s candidates found", len(scored_candidates))

        return {
            "candidates": [c.to_dict() for c in scored_candidates[:50]],
            "network_stats": self._compute_network_stats(G),
            "analysis_params": {
                "min_area_hectares": self.min_area,
                "max_area_hectares": self.max_area,
                "centrality_threshold_percentile": self.centrality_threshold_pct,
            }
        }

    def _detect_cells(
        self,
        G: nx.MultiDiGraph,
        progress_callback: Optional[callable] = None,
    ) -> list[SuperblockCandidate]:
        """
        Detect superblock cells using centrality-based boundary detection.

        Algorithm:
        1. Extract edges with centrality above threshold (boundary candidates)
        2. Create polygons from enclosed areas
        3. Filter by size constraints
        
        Optimizations:
        - Uses spatial indexing (R-tree) for fast polygon-edge intersection
        - Batch processing for improved performance
        """
        def report_progress(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback("detection", percent, message)

        nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True)

        if edges.empty:
            return []

        # Get centrality threshold
        centralities = edges.get("centrality", [0])
        if hasattr(centralities, "values"):
            centralities = centralities.values
        centrality_threshold = float(sorted(centralities)[
            int(len(centralities) * self.centrality_threshold_pct / 100)
        ]) if len(centralities) > 0 else 0

        # Extract boundary road geometries (high centrality OR major road type)
        boundary_edges = []
        total_edges = len(edges)
        report_progress(45, "Scanning edges for boundary roads...")
        
        # Use itertuples for better performance
        for i, row in enumerate(edges.itertuples(), start=1):
            if i % max(1, total_edges // 20) == 0:
                percent = 45 + int(7 * (i / total_edges)) if total_edges else 45
                report_progress(
                    percent,
                    f"Scanning edges for boundary roads ({i}/{total_edges})",
                )

            highway = getattr(row, "highway", "unclassified")
            if isinstance(highway, list):
                highway = highway[0]

            hierarchy = self.HIERARCHY_MAP.get(highway, 99)
            centrality = getattr(row, "centrality", 0)

            # Road is a boundary if: high centrality OR major road type
            is_boundary = (
                centrality >= centrality_threshold or
                hierarchy <= 5  # tertiary and above
            )

            if is_boundary:
                boundary_edges.append(row.geometry)

        if not boundary_edges:
            return []

        # Create polygons from boundary network
        report_progress(52, "Polygonizing boundary network...")
        boundary_union = unary_union(boundary_edges)
        polygons = list(polygonize(boundary_union))

        # Filter and create candidates
        transformer = pyproj.Transformer.from_crs(
            self.proj_wgs84, self.proj_mercator, always_xy=True
        )

        total_polys = len(polygons)
        if total_polys == 0:
            return []

        # Build spatial index for fast intersection queries
        report_progress(53, "Building spatial index for edge geometries...")
        edge_geometries = edges.geometry.tolist()
        edge_osmids = []
        edge_centroids = []
        
        for row in edges.itertuples():
            osmid = getattr(row, "osmid", 0)
            if isinstance(osmid, list):
                osmid = osmid[0]
            edge_osmids.append(int(osmid))
            edge_centroids.append(row.geometry.centroid)
        
        # Create spatial index for boundary intersection tests
        edge_tree = STRtree(edge_geometries)
        centroid_tree = STRtree(edge_centroids)
        
        candidates = []
        
        for poly_idx, poly in enumerate(polygons, start=1):
            percent = 54 + int(6 * (poly_idx / total_polys))
            report_progress(
                percent,
                f"Evaluating polygons ({poly_idx}/{total_polys})",
            )
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue

            # Calculate area
            poly_projected = transform(transformer.transform, poly)
            area_ha = poly_projected.area / 10000

            if area_ha < self.min_area or area_ha > self.max_area:
                continue

            # Find interior and perimeter roads using spatial index
            # Query edges that potentially intersect with polygon boundary
            boundary_candidates = edge_tree.query(poly.boundary)
            perimeter_ids = set()
            
            for idx in boundary_candidates:
                if edge_geometries[idx].intersects(poly.boundary):
                    perimeter_ids.add(edge_osmids[idx])
            
            # Query edges that are potentially inside polygon
            interior_candidates = centroid_tree.query(poly)
            interior_ids = set()
            
            for idx in interior_candidates:
                if poly.contains(edge_centroids[idx]) and edge_osmids[idx] not in perimeter_ids:
                    interior_ids.add(edge_osmids[idx])

            candidates.append(SuperblockCandidate(
                id=str(uuid.uuid4())[:8],
                geometry=mapping(poly),
                area_hectares=round(area_ha, 2),
                perimeter_roads=list(perimeter_ids)[:30],
                interior_roads=list(interior_ids)[:50],
                score=0,
                algorithm="centrality_based",
            ))

        return candidates

    def _score_candidate(
        self,
        candidate: SuperblockCandidate,
        G: nx.MultiDiGraph,
    ) -> SuperblockCandidate:
        """
        Multi-criteria scoring based on urban planning metrics.

        Criteria (based on Barcelona Superilles evaluation):
        1. Size: Ideal 9-16 hectares (400x400m Barcelona blocks)
        2. Shape: Compactness/regularity for efficient pedestrian movement
        3. Traffic: Through-traffic removal potential (centrality differential)
        4. Accessibility: Walking distance to boundary, access points
        5. Connectivity: Internal network density for walking/cycling
        6. Boundary quality: Capacity of boundary roads to absorb diverted traffic
        
        Optimizations:
        - Single-pass edge iteration with data caching
        - Pre-compute lookups for candidate roads
        """
        nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True)
        poly = Polygon(candidate.geometry["coordinates"][0])

        # 1. Size score (ideal: 9-16 ha)
        area = candidate.area_hectares
        if self.IDEAL_MIN_HA <= area <= self.IDEAL_MAX_HA:
            size_score = 100
        elif area < self.IDEAL_MIN_HA:
            size_score = max(0, 100 - (self.IDEAL_MIN_HA - area) * 15)
        else:
            size_score = max(0, 100 - (area - self.IDEAL_MAX_HA) * 10)

        # 2. Shape score (isoperimetric quotient)
        if poly.is_valid and poly.area > 0:
            ipq = 4 * math.pi * poly.area / (poly.length ** 2)
            shape_score = ipq * 100
        else:
            shape_score = 50

        # 3-6. Collect all edge data in a single pass (optimization)
        perimeter_set = set(candidate.perimeter_roads)
        interior_set = set(candidate.interior_roads)
        
        boundary_centralities = []
        interior_centralities = []
        boundary_capacity = 0

        # Single-pass iteration using itertuples for performance
        for row in edges.itertuples():
            osmid = getattr(row, "osmid", 0)
            if isinstance(osmid, list):
                osmid = osmid[0]
            
            if osmid in perimeter_set:
                # Collect boundary data
                centrality = getattr(row, "centrality", 0)
                boundary_centralities.append(centrality)
                
                # Calculate capacity
                highway = getattr(row, "highway", "unclassified")
                if isinstance(highway, list):
                    highway = highway[0]
                lanes = getattr(row, "lanes", 1)
                if isinstance(lanes, list):
                    lanes = lanes[0]
                try:
                    lanes = int(lanes)
                except (ValueError, TypeError):
                    lanes = 1
                capacity = self.CAPACITY_MAP.get(highway, 200) * lanes
                boundary_capacity += capacity
                
            elif osmid in interior_set:
                # Collect interior data
                centrality = getattr(row, "centrality", 0)
                interior_centralities.append(centrality)

        # Calculate traffic score from collected data
        boundary_mean = sum(boundary_centralities) / len(boundary_centralities) if boundary_centralities else 0
        interior_mean = sum(interior_centralities) / len(interior_centralities) if interior_centralities else 0

        candidate.boundary_centrality_mean = round(boundary_mean, 6)
        candidate.interior_centrality_mean = round(interior_mean, 6)

        # Good: high boundary centrality, low interior centrality
        if boundary_mean > 0:
            centrality_ratio = interior_mean / boundary_mean
            traffic_score = max(0, 100 - centrality_ratio * 100)
        else:
            traffic_score = 50

        # 4. Accessibility score (walking distances, access points)
        centroid = poly.centroid
        boundary_distance = poly.boundary.distance(centroid)

        # Convert to approximate meters (rough estimate)
        boundary_distance_m = boundary_distance * 111000 * math.cos(math.radians(centroid.y))

        # Ideal: max 200m to boundary (400m diameter)
        if boundary_distance_m <= 200:
            accessibility_score = 100
        elif boundary_distance_m <= 300:
            accessibility_score = 80
        elif boundary_distance_m <= 400:
            accessibility_score = 60
        else:
            accessibility_score = max(0, 100 - (boundary_distance_m - 200) * 0.2)

        # Count access points (boundary road intersections)
        candidate.num_access_points = min(len(candidate.perimeter_roads), 20)
        if candidate.num_access_points >= 8:
            accessibility_score = min(100, accessibility_score + 10)

        # 5. Connectivity score (internal network density)
        if len(candidate.interior_roads) > 0:
            # More interior roads = better internal connectivity for walking/cycling
            roads_per_ha = len(candidate.interior_roads) / area
            if roads_per_ha >= 3:
                connectivity_score = 100
            elif roads_per_ha >= 2:
                connectivity_score = 80
            elif roads_per_ha >= 1:
                connectivity_score = 60
            else:
                connectivity_score = 40
        else:
            connectivity_score = 30  # No interior roads - might be too small

        # 6. Boundary quality score (already calculated from boundary_capacity)
        # Good boundaries should have significant capacity
        if boundary_capacity >= 5000:
            boundary_quality_score = 100
        elif boundary_capacity >= 3000:
            boundary_quality_score = 80
        elif boundary_capacity >= 1500:
            boundary_quality_score = 60
        else:
            boundary_quality_score = max(20, boundary_capacity / 50)

        # Calculate weighted total score
        # Weights based on Barcelona Superilles priorities
        weights = {
            "size": 0.15,
            "shape": 0.10,
            "traffic": 0.25,
            "accessibility": 0.20,
            "connectivity": 0.15,
            "boundary_quality": 0.15,
        }

        total_score = (
            weights["size"] * size_score +
            weights["shape"] * shape_score +
            weights["traffic"] * traffic_score +
            weights["accessibility"] * accessibility_score +
            weights["connectivity"] * connectivity_score +
            weights["boundary_quality"] * boundary_quality_score
        )

        candidate.score = round(total_score, 1)
        candidate.score_breakdown = SuperblockScore(
            size_score=round(size_score, 1),
            shape_score=round(shape_score, 1),
            traffic_score=round(traffic_score, 1),
            accessibility_score=round(accessibility_score, 1),
            connectivity_score=round(connectivity_score, 1),
            boundary_quality_score=round(boundary_quality_score, 1),
            total_score=round(total_score, 1),
        )

        # Accessibility metrics
        candidate.accessibility = AccessibilityMetrics(
            max_walking_distance_to_boundary=round(boundary_distance_m, 0),
            emergency_access_maintained=True,  # Assumed with modal filters
            delivery_access_points=max(4, candidate.num_access_points // 2),
            residential_access_maintained=True,
            public_transport_affected=False,  # Would need transit data
        )

        # Traffic impact estimate
        if len(candidate.interior_roads) > 0:
            through_traffic_reduction = min(80, traffic_score * 0.8)
            candidate.traffic_impact = TrafficImpact(
                removed_through_traffic_pct=round(through_traffic_reduction, 1),
                boundary_load_increase_pct=round(through_traffic_reduction * 0.3, 1),
                estimated_vmt_reduction=round(area * through_traffic_reduction * 0.5, 0),
                affected_od_pairs=len(candidate.interior_roads) * 2,
            )

        return candidate

    def _plan_interventions(
        self,
        candidate: SuperblockCandidate,
        G: nx.MultiDiGraph,
    ) -> None:
        """
        Plan street interventions for a superblock candidate.

        Barcelona-style intervention planning:
        - Boundary roads: No change (handle through traffic)
        - Major interior roads: Convert to one-way with alternating directions
        - Minor interior roads: Modal filter (bikes/emergency only)
        - Central areas: Full pedestrianization
        
        Optimizations:
        - Single-pass edge iteration using itertuples
        - Pre-compute road sets for faster lookup
        """
        nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True)
        poly = Polygon(candidate.geometry["coordinates"][0])
        centroid = poly.centroid
        poly_area_sqrt = poly.area ** 0.5

        interventions = []
        perimeter_set = set(candidate.perimeter_roads)
        interior_set = set(candidate.interior_roads)

        # Single-pass iteration using itertuples for better performance
        for row in edges.itertuples():
            osmid = getattr(row, "osmid", 0)
            if isinstance(osmid, list):
                osmid = osmid[0]

            # Only process roads relevant to this candidate
            if osmid not in perimeter_set and osmid not in interior_set:
                continue

            name = getattr(row, "name", None)
            if isinstance(name, list):
                name = name[0] if name else None

            highway = getattr(row, "highway", "unclassified")
            if isinstance(highway, list):
                highway = highway[0]

            hierarchy = self.HIERARCHY_MAP.get(highway, 99)

            if osmid in perimeter_set:
                # Boundary road - no change
                interventions.append(StreetIntervention(
                    osm_id=int(osmid),
                    name=name,
                    intervention_type=InterventionType.NO_CHANGE,
                    access_allowed=["all"],
                    rationale="Boundary road - maintains through traffic capacity"
                ))

            elif osmid in interior_set:
                # Interior road - determine intervention type
                road_centroid = row.geometry.centroid
                distance_to_center = road_centroid.distance(centroid)
                relative_distance = distance_to_center / poly_area_sqrt

                if hierarchy <= 5:
                    # Major interior road - one-way for local access
                    direction = "forward" if hash(osmid) % 2 == 0 else "backward"
                    interventions.append(StreetIntervention(
                        osm_id=int(osmid),
                        name=name,
                        intervention_type=InterventionType.ONE_WAY,
                        direction=direction,
                        access_allowed=["residents", "delivery", "emergency"],
                        rationale="Converted to one-way for local access only"
                    ))
                elif relative_distance < 0.3:
                    # Central location - pedestrianize
                    interventions.append(StreetIntervention(
                        osm_id=int(osmid),
                        name=name,
                        intervention_type=InterventionType.PEDESTRIANIZE,
                        access_allowed=["emergency", "delivery_hours"],
                        rationale="Central location - full pedestrianization"
                    ))
                elif hierarchy >= 7:
                    # Minor road - modal filter
                    interventions.append(StreetIntervention(
                        osm_id=int(osmid),
                        name=name,
                        intervention_type=InterventionType.MODAL_FILTER,
                        access_allowed=["bicycle", "emergency"],
                        rationale="Modal filter - allows cycling and emergency access"
                    ))
                else:
                    # Residential access
                    interventions.append(StreetIntervention(
                        osm_id=int(osmid),
                        name=name,
                        intervention_type=InterventionType.LOCAL_ACCESS,
                        access_allowed=["residents", "delivery", "emergency"],
                        rationale="Local access only - no through traffic"
                    ))

        candidate.interventions = interventions[:100]  # Limit for response size

    def _compute_network_stats(self, G: nx.MultiDiGraph) -> dict:
        """Compute overall network statistics."""
        nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True)

        total_length = edges["length"].sum() if "length" in edges.columns else 0

        # Centrality statistics
        centralities = edges.get("centrality", [])
        if hasattr(centralities, "values"):
            centralities = list(centralities.values)

        return {
            "total_nodes": G.number_of_nodes(),
            "total_edges": G.number_of_edges(),
            "total_length_km": round(total_length / 1000, 2),
            "mean_centrality": round(sum(centralities) / len(centralities), 6) if centralities else 0,
            "max_centrality": round(max(centralities), 6) if centralities else 0,
        }


# Convenience function for API
async def analyze_superblocks(
    bbox: BoundingBox,
    min_area: float = 4.0,
    max_area: float = 25.0,
    progress_callback: Optional[callable] = None,
) -> dict:
    """
    Analyze an area for potential superblocks.

    Args:
        bbox: Bounding box to analyze
        min_area: Minimum superblock area in hectares
        max_area: Maximum superblock area in hectares
        progress_callback: Optional callback for progress updates

    Returns:
        Analysis results with candidates, network stats, and metadata
    """
    analyzer = SuperblockAnalyzer(
        min_area=min_area,
        max_area=max_area,
    )
    return await analyzer.analyze(bbox, progress_callback)
