"""
Superblock Size Optimizer.

This module calculates optimal superblock dimensions based on:
- Barcelona Superilles research (Rueda 2019)
- Urban planning literature
- Local context (density, street grid type)

The goal is to find superblock sizes that:
- Are walkable (5-minute walk across)
- Support local community life
- Are large enough to be meaningful for traffic reduction
"""

import math
import logging
import numpy as np
from typing import Optional
from dataclasses import dataclass
from shapely.geometry import Polygon, LineString, Point
import networkx as nx

logger = logging.getLogger(__name__)


# Barcelona Superilles base parameters
BARCELONA_BASE_SIZE_M = 400  # ~400m x 400m blocks (3x3 city blocks)
BARCELONA_MIN_SIZE_M = 300   # Minimum ~300m
BARCELONA_MAX_SIZE_M = 500   # Maximum ~500m

# Walking speed assumptions
WALKING_SPEED_MPS = 1.4  # 1.4 m/s average walking speed
MAX_WALKING_TIME_S = 300  # 5 minutes max to cross


@dataclass
class SizeRecommendation:
    """Recommended superblock dimensions."""
    min_side_m: float
    max_side_m: float
    optimal_side_m: float
    min_area_ha: float
    max_area_ha: float
    optimal_area_ha: float
    grid_orientation_deg: float
    rationale: str


@dataclass
class GridAnalysis:
    """Analysis of street grid characteristics."""
    dominant_angle_deg: float
    grid_regularity: float  # 0-1, higher = more regular grid
    average_block_size_m: float
    street_density: float  # km of streets per km²


class SizeOptimizer:
    """
    Calculates optimal superblock size and orientation.

    Based on Barcelona methodology with adjustments for:
    - Population density
    - Street grid type (regular vs organic)
    - Climate considerations
    """

    def __init__(
        self,
        graph: Optional[nx.MultiDiGraph] = None,
        population_density: Optional[float] = None,
        latitude: Optional[float] = None,
    ):
        """
        Initialize the optimizer.

        Args:
            graph: Street network graph (for grid analysis)
            population_density: Population per km² (optional)
            latitude: Latitude for solar considerations (optional)
        """
        self.graph = graph
        self.population_density = population_density
        self.latitude = latitude

        self.grid_analysis: Optional[GridAnalysis] = None

    def calculate_optimal_size(self) -> SizeRecommendation:
        """
        Calculate optimal superblock dimensions.

        Returns:
            SizeRecommendation with all dimensions and rationale
        """
        # Analyze grid if graph is available
        if self.graph is not None:
            self.grid_analysis = self._analyze_grid()

        # Start with Barcelona base
        base_size = BARCELONA_BASE_SIZE_M
        min_size = BARCELONA_MIN_SIZE_M
        max_size = BARCELONA_MAX_SIZE_M

        rationale_parts = ["Base: Barcelona Superilles model (400m optimal)"]

        # Adjust for population density
        if self.population_density is not None:
            density_factor = self._density_adjustment_factor()
            base_size *= density_factor
            min_size *= density_factor
            max_size *= density_factor

            if density_factor < 1:
                rationale_parts.append(
                    f"Reduced by {(1-density_factor)*100:.0f}% for high density"
                )
            elif density_factor > 1:
                rationale_parts.append(
                    f"Increased by {(density_factor-1)*100:.0f}% for low density"
                )

        # Adjust for grid regularity
        if self.grid_analysis is not None:
            grid_factor = self._grid_adjustment_factor()

            # For irregular grids, allow wider range
            if self.grid_analysis.grid_regularity < 0.5:
                min_size *= 0.8
                max_size *= 1.2
                rationale_parts.append("Widened range for organic street layout")

            # Use average block size if available
            if self.grid_analysis.average_block_size_m > 0:
                # Aim for 3-4 blocks per superblock side
                block_based_size = self.grid_analysis.average_block_size_m * 3.5
                if min_size <= block_based_size <= max_size:
                    base_size = block_based_size
                    rationale_parts.append(
                        f"Aligned to ~3.5 blocks ({self.grid_analysis.average_block_size_m:.0f}m blocks)"
                    )

        # Ensure walkability constraint
        max_walkable = MAX_WALKING_TIME_S * WALKING_SPEED_MPS
        if base_size > max_walkable:
            base_size = max_walkable
            rationale_parts.append(f"Limited to 5-minute walk ({max_walkable:.0f}m)")

        if max_size > max_walkable * 1.2:
            max_size = max_walkable * 1.2

        # Calculate orientation
        orientation = self._calculate_orientation()

        if self.grid_analysis is not None:
            rationale_parts.append(
                f"Grid orientation: {orientation:.1f}° (regularity: {self.grid_analysis.grid_regularity:.2f})"
            )

        # Convert to hectares
        min_area = (min_size ** 2) / 10000
        max_area = (max_size ** 2) / 10000
        optimal_area = (base_size ** 2) / 10000

        return SizeRecommendation(
            min_side_m=min_size,
            max_side_m=max_size,
            optimal_side_m=base_size,
            min_area_ha=min_area,
            max_area_ha=max_area,
            optimal_area_ha=optimal_area,
            grid_orientation_deg=orientation,
            rationale="; ".join(rationale_parts),
        )

    def _density_adjustment_factor(self) -> float:
        """
        Calculate size adjustment based on population density.

        Higher density → smaller superblocks (more services nearby)
        Lower density → larger superblocks (maintain critical mass)
        """
        if self.population_density is None:
            return 1.0

        # Reference: Barcelona ~16,000/km²
        reference_density = 16000

        if self.population_density > reference_density * 1.5:
            # Very high density: reduce size
            return 0.8
        elif self.population_density > reference_density:
            # High density: slight reduction
            return 0.9
        elif self.population_density > reference_density * 0.5:
            # Medium density: no change
            return 1.0
        elif self.population_density > reference_density * 0.25:
            # Low density: increase size
            return 1.1
        else:
            # Very low density: larger superblocks
            return 1.2

    def _analyze_grid(self) -> GridAnalysis:
        """Analyze street grid characteristics."""
        if self.graph is None:
            return GridAnalysis(
                dominant_angle_deg=0,
                grid_regularity=0.5,
                average_block_size_m=100,
                street_density=0,
            )

        # Collect edge orientations
        angles = []
        total_length = 0

        for u, v, data in self.graph.edges(data=True):
            u_data = self.graph.nodes.get(u, {})
            v_data = self.graph.nodes.get(v, {})

            if "x" not in u_data or "x" not in v_data:
                continue

            dx = v_data["x"] - u_data["x"]
            dy = v_data["y"] - u_data["y"]

            length = data.get("length", math.sqrt(dx*dx + dy*dy))
            total_length += length

            # Calculate angle (0-180 range, treating opposite directions as same)
            angle = math.degrees(math.atan2(dy, dx))
            angle = angle % 180  # Normalize to 0-180

            angles.append((angle, length))

        if not angles:
            return GridAnalysis(
                dominant_angle_deg=0,
                grid_regularity=0.5,
                average_block_size_m=100,
                street_density=0,
            )

        # Find dominant angle using weighted histogram
        bins = np.linspace(0, 180, 37)  # 5-degree bins
        hist, _ = np.histogram(
            [a[0] for a in angles],
            bins=bins,
            weights=[a[1] for a in angles]
        )

        dominant_bin = np.argmax(hist)
        dominant_angle = (bins[dominant_bin] + bins[dominant_bin + 1]) / 2

        # Calculate grid regularity
        # High regularity = most edges aligned to dominant + perpendicular directions
        perpendicular = (dominant_angle + 90) % 180

        aligned_length = 0
        for angle, length in angles:
            # Check if aligned to dominant or perpendicular (within 15 degrees)
            diff_to_dominant = min(abs(angle - dominant_angle), 180 - abs(angle - dominant_angle))
            diff_to_perp = min(abs(angle - perpendicular), 180 - abs(angle - perpendicular))

            if diff_to_dominant < 15 or diff_to_perp < 15:
                aligned_length += length

        regularity = aligned_length / total_length if total_length > 0 else 0.5

        # Estimate average block size
        # This is rough - based on total length and number of nodes
        num_nodes = self.graph.number_of_nodes()
        if num_nodes > 0:
            # Assume roughly square blocks
            # Average block perimeter ≈ total_length / (num_blocks)
            # num_blocks ≈ num_nodes / 4
            estimated_blocks = max(1, num_nodes / 4)
            avg_perimeter = total_length / estimated_blocks
            avg_block_size = avg_perimeter / 4  # Perimeter to side
        else:
            avg_block_size = 100

        # Street density (km per km²)
        # Rough calculation - would need bbox for accurate result
        street_density = 0  # TODO: calculate if bbox available

        return GridAnalysis(
            dominant_angle_deg=dominant_angle,
            grid_regularity=regularity,
            average_block_size_m=avg_block_size,
            street_density=street_density,
        )

    def _grid_adjustment_factor(self) -> float:
        """Calculate adjustment factor based on grid regularity."""
        if self.grid_analysis is None:
            return 1.0

        # More regular grids can have tighter size constraints
        if self.grid_analysis.grid_regularity > 0.8:
            return 1.0  # No adjustment for very regular grids
        elif self.grid_analysis.grid_regularity > 0.6:
            return 1.0
        elif self.grid_analysis.grid_regularity > 0.4:
            return 1.05  # Slightly larger for medium irregularity
        else:
            return 1.1  # Larger for organic layouts

    def _calculate_orientation(self) -> float:
        """
        Calculate optimal superblock orientation.

        Prioritizes grid alignment (90%) with solar considerations (10%).
        """
        grid_angle = 0.0
        if self.grid_analysis is not None:
            grid_angle = self.grid_analysis.dominant_angle_deg

        # Solar optimal orientation (for northern hemisphere)
        # Slightly south-facing for passive solar
        solar_optimal = 0.0  # East-west alignment
        if self.latitude is not None:
            if self.latitude > 0:
                # Northern hemisphere: slight rotation for south exposure
                solar_optimal = 15  # 15 degrees from E-W
            else:
                # Southern hemisphere: opposite
                solar_optimal = -15

        # Weight: 90% grid, 10% solar
        orientation = 0.9 * grid_angle + 0.1 * solar_optimal

        return orientation

    def suggest_num_sectors(self, area_hectares: float) -> int:
        """
        Suggest number of angular sectors based on superblock size.

        Larger superblocks may benefit from more sectors for finer
        traffic control.

        Args:
            area_hectares: Superblock area

        Returns:
            Recommended number of sectors (3-8)
        """
        if area_hectares < 6:
            return 3  # Small: 3 sectors sufficient
        elif area_hectares < 12:
            return 4  # Medium: classic 4 sectors
        elif area_hectares < 20:
            return 4  # Standard: 4 sectors
        elif area_hectares < 30:
            return 6  # Large: 6 sectors for better control
        else:
            return 8  # Very large: 8 sectors


def calculate_optimal_superblock_size(
    graph: Optional[nx.MultiDiGraph] = None,
    population_density: Optional[float] = None,
    latitude: Optional[float] = None,
) -> SizeRecommendation:
    """
    Convenience function to calculate optimal superblock size.

    Args:
        graph: Street network for grid analysis
        population_density: Population per km²
        latitude: Location latitude

    Returns:
        SizeRecommendation with all dimensions
    """
    optimizer = SizeOptimizer(
        graph=graph,
        population_density=population_density,
        latitude=latitude,
    )
    return optimizer.calculate_optimal_size()
