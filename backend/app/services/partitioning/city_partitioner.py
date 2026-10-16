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

import hashlib
import itertools
import json
import logging
import math
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

import networkx as nx
import numpy as np
import osmnx as ox
import pyproj
import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box, shape
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

from app.models.schemas import (
    AccessTarget,
    ActiveModeRequirement,
    AdministrativeBoundary,
    AnalysisEvidence,
    ApprovalType,
    AreaExclusion,
    AssignmentComparison,
    AssignmentSettings,
    BoundingBox,
    CellKind,
    CityPartition,
    Coordinates,
    DataQualityMetrics,
    DemandModel,
    EmergencyAccessSummary,
    EnforcedSuperblock,
    EntryPoint,
    FilterDesignSettings,
    ImpactValidationSummary,
    ModificationType,
    NetworkAuthorityMode,
    OperationalRequirement,
    OsmContext,
    PartitionProgress,
    PlanReadiness,
    PlanStage,
    PublicSpaceSettings,
    ReadinessGate,
    SchoolStreetSettings,
    SizeStatus,
    StreetModification,
    StudyAreaMode,
    TrafficImpactSettings,
    TrafficObservation,
    TransportMode,
    TurnModelMode,
    TurnRestriction,
    UnreachableAccessTarget,
    VehicleClass,
)
from app.services.assignment.network_assignment import (
    NetworkAssignmentEngine,
    calibrate_assignment,
)
from app.services.constraint.active_mode_validator import ActiveModeValidator
from app.services.constraint.constraint_enforcer import KEEP_OPEN_ATTRIBUTE, ConstraintEnforcer
from app.services.constraint.operational_validator import OperationalValidator
from app.services.design.filter_design import FilterDesigner
from app.services.design.pilot_plan import PilotPlanner
from app.services.design.public_space import PublicSpaceEstimator
from app.services.design.school_streets import SchoolStreetPlanner
from app.services.impact.boundary_impact import (
    BoundaryImpactEstimator,
    SuperblockImpactInput,
)
from app.services.impact.emergency_access import EmergencyAccessChecker
from app.services.impact.health import HealthEstimator
from app.services.impact.transit_access import POORLY_SERVED_SHARE, TransitAccessChecker
from app.services.importers.population import PopulationGrid, summarize_population
from app.services.network.turn_graph import TurnGraphBuilder
from app.services.partitioning.fast_centrality import sampled_edge_betweenness

logger = logging.getLogger(__name__)


# Road types considered as arterials (boundaries)
ARTERIAL_TYPES = {
    "primary",
    "secondary",
    "tertiary",
    "primary_link",
    "secondary_link",
    "tertiary_link",
}

# Split lines tried per oversized cell, nearest the cell centre first.
MAX_SPLIT_CANDIDATES = 150
# A split follows its street through junctions up to this many edges, as long
# as the street turns by no more than this at each one.
MAX_CHAIN_EDGES = 60
MAX_CHAIN_TURN_DEGREES = 50.0
# When no split of an oversized cell leaves both pieces at the minimum size,
# one piece may be this fraction of the minimum; merging then joins it to a
# neighbour.
UNEVEN_SPLIT_MIN_FACTOR = 0.25
# How far a clipped split line is pushed past the cell boundary, in degrees
# (about 10 cm).
SPLIT_OVERSHOOT = 1e-6
# Splits tried per cell before giving up when each leaves a piece without
# entries.
MAX_SPLIT_ATTEMPTS = 3
# How much a split's choice weighs the compactness of its least compact piece
# against how evenly it divides the area (both on a 0-1 scale).
SPLIT_SHAPE_WEIGHT = 2.0
# Split lines come from main roads (tertiary and above). Outer districts such
# as Rakosmente have no main road for kilometres, which left single "cells"
# of thousands of hectares that cut no traffic at all. Cells this many times
# over the maximum size may also split along residential streets.
OVERSIZED_CELL_FACTOR = 5.0
MAIN_ROAD_SPLIT_HIERARCHY = 5
MINOR_SPLIT_HIERARCHIES = frozenset({6, 8})  # residential, unclassified
# Housing estates (lakótelepek) are often served only by living streets. A cell
# that no residential street divides may split along one as a last resort.
LIVING_STREET_SPLIT_HIERARCHIES = frozenset({7})
# A cell over the maximum where most land lies beyond this many metres of any
# street is open land with a road through it (forest, rail, an airfield), not
# an oversized superblock: splitting it would only divide fields and forest.
STREET_REACH_M = 100.0
MAX_ACCESS_REPAIR_ROUNDS = 10
THROUGH_CORRIDOR_HIGHWAYS = frozenset({"motorway", "motorway_link", "trunk", "trunk_link"})
MAX_REDESIGN_ROUNDS = 3
MAX_CROSSING_SEALS_PER_ENTRY = 8
MIN_SECTORS = 2
# Access changes a repair pass may reopen when they strand streets.
REPAIRABLE_CHANGES = frozenset(
    {ModificationType.MODAL_FILTER, ModificationType.FULL_CLOSURE, ModificationType.ONE_WAY}
)
OPEN_LAND_STREET_SHARE = 0.5

# Road hierarchy for boundary suitability
MINOR_ROAD_CLASSES = frozenset(
    {"residential", "living_street", "service", "pedestrian", "track", "road", "busway"}
)

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


def _clearance(edge_data: dict | None) -> float | None:
    """Surveyed clearance in metres, or None when it is unknown."""
    try:
        return float((edge_data or {})["clearance_m"])
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class SuperblockCell:
    """Intermediate representation of a potential superblock."""

    polygon: Polygon
    area_hectares: float
    boundary_edges: list[tuple[int, int, int]]  # (u, v, key)
    interior_edges: list[tuple[int, int, int]]
    entry_nodes: list[int]


MIN_COMPLETE_COVERAGE_PERCENT = 99.5
MIN_AUTHORITATIVE_NETWORK_COVERAGE_PERCENT = 95.0
MIN_DIRECTIONAL_TRAFFIC_COVERAGE_PERCENT = 90.0
MIN_PEAK_PERIOD_COVERAGE_PERCENT = 90.0
MIN_TURN_COVERAGE_PERCENT = 80.0
MIN_LANE_COVERAGE_PERCENT = 90.0
MIN_SIGNAL_COVERAGE_PERCENT = 90.0
MIN_ACCESS_ENTRANCE_COVERAGE_PERCENT = 99.5
MIN_TRANSIT_COVERAGE_PERCENT = 90.0
MIN_EMERGENCY_COVERAGE_PERCENT = 90.0
MIN_CALIBRATION_GEH_PASS_PERCENT = 85.0

ENGINEERING_APPROVALS = {
    ApprovalType.TRANSPORT_ENGINEERING,
    ApprovalType.SITE_INSPECTION,
    ApprovalType.ACCESSIBILITY_REVIEW,
    ApprovalType.TRANSIT_OPERATOR_REVIEW,
    ApprovalType.EMERGENCY_SERVICES_REVIEW,
}
PILOT_APPROVALS = ENGINEERING_APPROVALS | {
    ApprovalType.ROAD_MANAGER_APPROVAL,
    ApprovalType.TRANSPORT_AUTHORITY_APPROVAL,
    ApprovalType.ENVIRONMENTAL_SCREENING,
    ApprovalType.PUBLIC_CONSULTATION_RECORD,
    ApprovalType.PILOT_MONITORING_PLAN,
}
PERMANENT_APPROVALS = PILOT_APPROVALS | {
    ApprovalType.PILOT_EVALUATION,
    ApprovalType.PERMANENT_WORKS_APPROVAL,
}


def assess_plan_readiness(
    *,
    evidence: AnalysisEvidence,
    has_boundary: bool,
    modeled_directional_validation_passed: bool,
    validated_target_count: int,
    total_unreachable_targets: int,
    coverage_percent: float = 0.0,
    verified_approval_types: set[ApprovalType] | None = None,
) -> PlanReadiness:
    """Evaluate progressive gates without inferring professional approval."""
    approvals = verified_approval_types or set()
    gates: list[ReadinessGate] = []

    topology_blockers: list[str] = []
    if not has_boundary:
        topology_blockers.append("An exact study-area polygon is missing")
    if evidence.turn_model_mode != TurnModelMode.TURN_EXPANDED:
        topology_blockers.append("The network is edge-only; turn movements are not modeled")
    accounted_coverage = min(100.0, coverage_percent)
    if accounted_coverage < MIN_COMPLETE_COVERAGE_PERCENT:
        topology_blockers.append(
            f"Only {accounted_coverage:.2f}% of eligible physical road length is assigned to a "
            "generated cell, boundary corridor, or reviewed exclusion; "
            f"at least {MIN_COMPLETE_COVERAGE_PERCENT:.1f}% is required"
        )
    if not modeled_directional_validation_passed:
        topology_blockers.append(
            "Turn-aware directional validation has not passed for every generated cell"
        )
    gates.append(
        ReadinessGate(
            stage=PlanStage.TOPOLOGY_CANDIDATE,
            passed=not topology_blockers,
            blockers=topology_blockers,
        )
    )

    data_blockers: list[str] = []
    if evidence.study_area_mode != StudyAreaMode.FUNCTIONAL_URBAN_AREA:
        data_blockers.append("Demand and spillover are not modeled over a functional urban area")
    if evidence.network_authority_mode != NetworkAuthorityMode.AUTHORITATIVE_CONFLATED:
        data_blockers.append("The street network is not conflated with an authoritative register")
    if evidence.traffic_mode != "measured_volume":
        data_blockers.append("Measured time-dependent traffic observations are missing")
    quality = evidence.data_quality
    if quality.authoritative_network_coverage_percent < MIN_AUTHORITATIVE_NETWORK_COVERAGE_PERCENT:
        data_blockers.append(
            "Authoritative road-network coverage is "
            f"{quality.authoritative_network_coverage_percent:.1f}%; "
            f"{MIN_AUTHORITATIVE_NETWORK_COVERAGE_PERCENT:.0f}% is required"
        )
    if quality.directional_traffic_coverage_percent < MIN_DIRECTIONAL_TRAFFIC_COVERAGE_PERCENT:
        data_blockers.append(
            "Directional traffic coverage is "
            f"{quality.directional_traffic_coverage_percent:.1f}%; "
            f"{MIN_DIRECTIONAL_TRAFFIC_COVERAGE_PERCENT:.0f}% is required"
        )
    if quality.peak_period_coverage_percent < MIN_PEAK_PERIOD_COVERAGE_PERCENT:
        data_blockers.append(
            f"Peak-period coverage is below {MIN_PEAK_PERIOD_COVERAGE_PERCENT:.0f}%"
        )
    if quality.turning_movement_coverage_percent < MIN_TURN_COVERAGE_PERCENT:
        data_blockers.append(f"Turning-movement coverage is below {MIN_TURN_COVERAGE_PERCENT:.0f}%")
    if quality.lane_coverage_percent < MIN_LANE_COVERAGE_PERCENT:
        data_blockers.append(f"Directional lane coverage is below {MIN_LANE_COVERAGE_PERCENT:.0f}%")
    if quality.signal_coverage_percent < MIN_SIGNAL_COVERAGE_PERCENT:
        data_blockers.append(f"Signal-control coverage is below {MIN_SIGNAL_COVERAGE_PERCENT:.0f}%")
    if not evidence.access_dataset_complete:
        data_blockers.append("Complete authoritative entrance/access evidence is missing")
    elif validated_target_count != evidence.access_target_count:
        data_blockers.append("Some authoritative access targets fall outside generated cells")
    if quality.access_entrance_coverage_percent < MIN_ACCESS_ENTRANCE_COVERAGE_PERCENT:
        data_blockers.append(
            f"Surveyed entrance coverage is below {MIN_ACCESS_ENTRANCE_COVERAGE_PERCENT:.1f}%"
        )
    if quality.transit_coverage_percent < MIN_TRANSIT_COVERAGE_PERCENT:
        data_blockers.append(
            f"Transit operations coverage is below {MIN_TRANSIT_COVERAGE_PERCENT:.0f}%"
        )
    if quality.emergency_coverage_percent < MIN_EMERGENCY_COVERAGE_PERCENT:
        data_blockers.append(
            f"Emergency-service coverage is below {MIN_EMERGENCY_COVERAGE_PERCENT:.0f}%"
        )
    if total_unreachable_targets:
        data_blockers.append("One or more authoritative access targets are unreachable")
    required_categories = {
        "road_network",
        "traffic",
        "demand",
        "turns_signals",
        "access",
        "transit",
        "emergency",
        "freight_curb",
        "active_modes",
        "crashes_safety",
        "environment",
        "demographics_equity",
        "planning_constraints",
    }
    qualified_categories = {
        manifest.category
        for manifest in evidence.dataset_manifests
        if manifest.complete and manifest.quality_score >= 0.8
    }
    for category in sorted(required_categories - qualified_categories):
        data_blockers.append(f"Qualified {category.replace('_', ' ')} dataset is missing")
    gates.append(
        ReadinessGate(
            stage=PlanStage.DATA_QUALIFIED,
            passed=not topology_blockers and not data_blockers,
            blockers=data_blockers,
        )
    )

    calibration_blockers: list[str] = []
    calibration = evidence.baseline_calibration
    if calibration is None or not calibration.validated:
        calibration_blockers.append("An independently validated baseline calibration is missing")
    else:
        if not calibration.holdout_dataset_ids:
            calibration_blockers.append("Calibration has no independent holdout dataset")
        if calibration.geh_under_5_percent < MIN_CALIBRATION_GEH_PASS_PERCENT:
            calibration_blockers.append(
                f"Calibration GEH pass rate is below {MIN_CALIBRATION_GEH_PASS_PERCENT:.0f}%"
            )
        if calibration.stochastic_runs < 5:
            calibration_blockers.append("Calibration contains fewer than five stochastic runs")
    gates.append(
        ReadinessGate(
            stage=PlanStage.BASELINE_CALIBRATED,
            passed=gates[-1].passed and not calibration_blockers,
            blockers=calibration_blockers,
        )
    )

    impact_blockers: list[str] = []
    impact = evidence.impact_validation
    if impact is None or not impact.all_required_checks_passed():
        impact_blockers.append(
            "Boundary capacity, spillover, service, safety, environment, equity, and uncertainty checks have not all passed"
        )
    gates.append(
        ReadinessGate(
            stage=PlanStage.IMPACT_VALIDATED,
            passed=gates[-1].passed and not impact_blockers,
            blockers=impact_blockers,
        )
    )

    engineering_missing = ENGINEERING_APPROVALS - approvals
    engineering_blockers = [
        f"Verified {approval.value.replace('_', ' ')} artifact is missing"
        for approval in sorted(engineering_missing, key=lambda value: value.value)
    ]
    gates.append(
        ReadinessGate(
            stage=PlanStage.ENGINEERING_REVIEWED,
            passed=gates[-1].passed and not engineering_blockers,
            blockers=engineering_blockers,
        )
    )

    pilot_missing = PILOT_APPROVALS - approvals
    pilot_blockers = [
        f"Verified {approval.value.replace('_', ' ')} artifact is missing"
        for approval in sorted(pilot_missing, key=lambda value: value.value)
    ]
    gates.append(
        ReadinessGate(
            stage=PlanStage.APPROVED_FOR_PILOT,
            passed=gates[-1].passed and not pilot_blockers,
            blockers=pilot_blockers,
        )
    )

    permanent_missing = PERMANENT_APPROVALS - approvals
    permanent_blockers = [
        f"Verified {approval.value.replace('_', ' ')} artifact is missing"
        for approval in sorted(permanent_missing, key=lambda value: value.value)
    ]
    gates.append(
        ReadinessGate(
            stage=PlanStage.APPROVED_FOR_PERMANENT_WORKS,
            passed=gates[-1].passed and not permanent_blockers,
            blockers=permanent_blockers,
        )
    )

    highest = PlanStage.MODEL_ONLY
    for gate in gates:
        if not gate.passed:
            break
        highest = gate.stage
    next_gate = next((gate for gate in gates if not gate.passed), None)
    blockers = list(next_gate.blockers) if next_gate else []
    return PlanReadiness(
        status=highest,
        highest_completed_stage=highest,
        ready_for_engineering_review=next(
            gate.passed for gate in gates if gate.stage == PlanStage.IMPACT_VALIDATED
        ),
        approved_for_pilot=next(
            gate.passed for gate in gates if gate.stage == PlanStage.APPROVED_FOR_PILOT
        ),
        approved_for_permanent_works=next(
            gate.passed for gate in gates if gate.stage == PlanStage.APPROVED_FOR_PERMANENT_WORKS
        ),
        implementation_ready=next(
            gate.passed for gate in gates if gate.stage == PlanStage.APPROVED_FOR_PERMANENT_WORKS
        ),
        modeled_directional_validation_passed=modeled_directional_validation_passed,
        transport_engineering_reviewed=ApprovalType.TRANSPORT_ENGINEERING in approvals,
        site_inspection_reviewed=ApprovalType.SITE_INSPECTION in approvals,
        verified_approval_types=sorted(approvals, key=lambda value: value.value),
        gates=gates,
        blockers=blockers,
    )


def compute_plan_id(partition: CityPartition) -> str:
    """Bind interventions and input provenance while allowing signed results to attach."""
    payload = partition.model_dump(
        exclude={
            "plan_id": True,
            "readiness": True,
            "approval_artifacts": True,
            "evidence_artifacts": True,
            "evidence": {
                "data_quality": True,
                "baseline_calibration": True,
                "impact_validation": True,
            },
        }
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


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
        boundary: AdministrativeBoundary | None = None,
        target_size_ha: float = DEFAULT_TARGET_HA,
        min_area_ha: float = DEFAULT_MIN_HA,
        max_area_ha: float = DEFAULT_MAX_HA,
        num_sectors: int = 4,
        arterial_road_types: set[str] | None = None,
        enforce_constraints: bool = True,
        traffic_evidence: dict[str, object] | None = None,
        access_targets: list[AccessTarget] | None = None,
        access_dataset_source: str | None = None,
        access_dataset_complete: bool = False,
        evidence_context: AnalysisEvidence | None = None,
        turn_restrictions: list[TurnRestriction] | None = None,
        area_exclusions: list[AreaExclusion] | None = None,
        operational_requirements: list[OperationalRequirement] | None = None,
        active_mode_requirements: list[ActiveModeRequirement] | None = None,
        active_mode_graphs: dict[str, nx.MultiDiGraph] | None = None,
        traffic_observations: list[TrafficObservation] | None = None,
        demand_model: DemandModel | None = None,
        assignment_settings: AssignmentSettings | None = None,
        traffic_impact_settings: TrafficImpactSettings | None = None,
        osm_context: OsmContext | None = None,
        population: PopulationGrid | None = None,
        filter_design_settings: FilterDesignSettings | None = None,
        public_space_settings: PublicSpaceSettings | None = None,
        school_street_settings: SchoolStreetSettings | None = None,
        minor_boundary_policy: str = "avoid",
        use_centrality_boundaries: bool = True,
        progress_callback: Callable[[PartitionProgress], None] | None = None,
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
        self.boundary = boundary
        self.analysis_polygon = (
            shape(boundary.model_dump())
            if boundary is not None
            else box(bbox.west, bbox.south, bbox.east, bbox.north)
        )
        self.target_size_ha = target_size_ha
        self.min_area_ha = min_area_ha
        self.max_area_ha = max_area_ha
        self.num_sectors = num_sectors
        configured_types = arterial_road_types or {"primary", "secondary", "tertiary"}
        self.arterial_types = configured_types | {
            f"{road_type}_link" for road_type in configured_types
        }
        self.enforce_constraints = enforce_constraints
        self.traffic_evidence = traffic_evidence or {
            "traffic_mode": "modeled_topology",
            "traffic_observation_count": 0,
            "measured_edge_coverage_percent": 0.0,
            "traffic_sources": [],
        }
        self.access_targets = access_targets or []
        self._access_target_points = [
            Point(target.coordinates.lon, target.coordinates.lat) for target in self.access_targets
        ]
        self._access_target_tree = (
            STRtree(self._access_target_points) if self._access_target_points else None
        )
        self.access_dataset_source = access_dataset_source
        self.access_dataset_complete = access_dataset_complete
        self.evidence_context = evidence_context
        self.turn_restrictions = turn_restrictions or []
        self.area_exclusions = area_exclusions or []
        self.operational_requirements = operational_requirements or []
        self.active_mode_requirements = active_mode_requirements or []
        self.active_mode_graphs = active_mode_graphs or {}
        self.traffic_observations = traffic_observations or []
        self.demand_model = demand_model
        self.assignment_settings = assignment_settings or AssignmentSettings()
        self.traffic_impact_settings = traffic_impact_settings or TrafficImpactSettings()
        self._impact_inputs: list[SuperblockImpactInput] = []
        self.osm_context = osm_context
        self.population_grid = population
        self.public_space_settings = public_space_settings or PublicSpaceSettings()
        self.school_street_settings = school_street_settings or SchoolStreetSettings()
        self.minor_boundary_policy = minor_boundary_policy
        self.use_centrality_boundaries = use_centrality_boundaries
        # Residential streets busy enough to be boundaries, kept inside cells
        # by the "avoid" policy; never used as split lines either.
        self._busy_minor_streets: set[tuple[int, int]] = set()
        # Residential streets a cell was split along. They bound two cells but
        # get a modal filter, so diverted traffic stays on the main roads.
        self._quiet_split_streets: set[tuple[int, int]] = set()
        self._split_filters: dict[tuple[int, int], StreetModification] = {}
        self._split_filter_owner: dict[tuple[int, int], int] = {}
        self.filter_designer = FilterDesigner(
            graph, osm_context, filter_design_settings or FilterDesignSettings()
        )
        self.progress_callback = progress_callback

        # Convert graph to GeoDataFrames for spatial operations
        self.nodes_gdf = None
        self.edges_gdf = None
        self._edge_records: list[tuple[int, int, int, LineString]] = []
        self._edge_tree: STRtree | None = None
        self._arterial_nodes: set[int] = set()
        self._arterial_edges_by_node: dict[int, list[tuple[int, int, int]]] | None = None
        self._area_transformer = None

        # Results
        self.arterial_edges: set[tuple[int, int, int]] = set()
        self.cells: list[SuperblockCell] = []
        self.superblocks: list[EnforcedSuperblock] = []
        self._superblock_cells: dict[str, SuperblockCell] = {}
        self._main_road_nodes: set[int] = set()
        self._main_road_edges: set[tuple[int, int, int]] = set()
        self.crossings_sealed = 0
        self._live_nodes: set[int] | None = None
        self.reopened_for_access = 0
        # Interior streets reopened for access; a redesign must not cut them.
        self._keep_open: set[tuple[int, int]] = set()
        self._superblock_index: dict[str, int] = {}
        self.stranded_junctions = 0
        self.destination_gates = 0
        # Cells with no streets inside are not superblocks; they are only counted.
        self.non_street_cells = 0

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
        self._main_road_edges = set(self.arterial_edges)
        self._main_road_nodes = {node for edge in self.arterial_edges for node in edge[:2]}
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
        self._repair_access()
        for _round in range(MAX_REDESIGN_ROUNDS):
            if not self._redesign_after_repair():
                break
        # Superblocks that still bounce between a cut and the street it strands
        # get fewer, larger sectors: fewer cuts, each with more room to route.
        sectors = self.num_sectors
        try:
            while self.num_sectors > MIN_SECTORS:
                self.num_sectors -= 1
                if not self._redesign_after_repair():
                    break
        finally:
            self.num_sectors = sectors
        self._gate_unresolved()
        self._seal_crossings()
        self._gate_stranded()
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
            edge_geometries = []
            for u, v, key, data in self.graph.edges(keys=True, data=True):
                geometry = data.get("geometry")
                if geometry is None:
                    u_data = self.graph.nodes.get(u, {})
                    v_data = self.graph.nodes.get(v, {})
                    if "x" not in u_data or "x" not in v_data:
                        continue
                    geometry = LineString([(u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])])
                self._edge_records.append((u, v, key, geometry))
                edge_geometries.append(geometry)
            if edge_geometries:
                self._edge_tree = STRtree(edge_geometries)

            centroid = self.analysis_polygon.centroid
            utm_zone = int((centroid.x + 180) / 6) + 1
            hemisphere = "north" if centroid.y >= 0 else "south"
            proj_string = f"+proj=utm +zone={utm_zone} +{hemisphere} +datum=WGS84"
            self._area_transformer = pyproj.Transformer.from_crs(
                "EPSG:4326", proj_string, always_xy=True
            ).transform
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
        measured_edges = {
            (u, v, key): float(data["measured_volume_vph"])
            for u, v, key, data in self.graph.edges(keys=True, data=True)
            if data.get("measured_volume_vph") is not None
        }
        if measured_edges:
            volumes = sorted(measured_edges.values())
            threshold = volumes[int((len(volumes) - 1) * 0.60)]
            self.arterial_edges = {
                edge for edge, volume in measured_edges.items() if volume >= threshold
            }
            self._arterial_nodes = {node for u, v, _key in self.arterial_edges for node in (u, v)}
            self._arterial_edges_by_node = None
            logger.info(
                "Arterial network: %s measured-volume edges at >= %.0f vehicles/hour",
                len(self.arterial_edges),
                threshold,
            )
            return

        # No matching observations were supplied: retain a model-only topology
        # fallback, and make that fallback machine-visible in result evidence.
        self.traffic_evidence["traffic_mode"] = "modeled_topology"

        # Criterion 1: Road type
        type_arterials = set()
        for u, v, key, data in self.graph.edges(keys=True, data=True):
            highway = data.get("highway", "")
            if isinstance(highway, list):
                highway = highway[0]
            if highway in self.arterial_types:
                type_arterials.add((u, v, key))

        # Criterion 2: Betweenness centrality (for larger networks)
        centrality_arterials = set()
        num_nodes = self.graph.number_of_nodes()

        # Circulation-sector plans bound cells by the main road grid alone.
        if self.use_centrality_boundaries and num_nodes > 100:
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
                k = min(128, num_nodes) if num_nodes > 1000 else None
                centrality = sampled_edge_betweenness(G_simple, k=k, weight="weight", seed=42)

                # Find threshold
                values = list(centrality.values())
                if values:
                    threshold = sorted(values)[int(len(values) * self.CENTRALITY_PERCENTILE / 100)]

                    for edge, cent_value in centrality.items():
                        if cent_value >= threshold:
                            simple_key = (min(edge[0], edge[1]), max(edge[0], edge[1]))
                            if simple_key in edge_mapping:
                                for original in edge_mapping[simple_key]:
                                    if self._may_bound_by_centrality(original):
                                        centrality_arterials.add(original)
                                    else:
                                        self._busy_minor_streets.add(simple_key)

            except Exception as e:
                logger.warning(f"Centrality computation failed: {e}")

        # Combine both criteria
        self.arterial_edges = type_arterials.union(centrality_arterials)
        self._arterial_nodes = {node for u, v, _key in self.arterial_edges for node in (u, v)}
        self._arterial_edges_by_node = None
        logger.info(
            f"Arterial network: {len(type_arterials)} by type, "
            f"{len(centrality_arterials)} by centrality, "
            f"{len(self.arterial_edges)} total"
        )

    def _may_bound_by_centrality(self, edge: tuple[int, int, int]) -> bool:
        """Whether a busy street may become a boundary road on centrality alone.

        Residential and living streets that carry many shortest paths are the
        rat runs a superblock is meant to calm. Making them boundary roads
        routes diverted traffic onto them, so the "avoid" policy keeps them
        inside cells.
        """
        if self.minor_boundary_policy != "avoid":
            return True
        highway = self.graph.edges[edge].get("highway", "")
        if isinstance(highway, list):
            highway = highway[0]
        return highway not in MINOR_ROAD_CLASSES

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
                    line = LineString(
                        [
                            (u_data["x"], u_data["y"]),
                            (v_data["x"], v_data["y"]),
                        ]
                    )
                    arterial_lines.append(line)

        if not arterial_lines:
            logger.warning("No arterial geometries found")
            return

        # Add the exact administrative boundary to close edge cells. A bbox is
        # used only when the request explicitly lacks an administrative polygon.
        arterial_lines.append(self.analysis_polygon.boundary)

        # Union and polygonize
        try:
            merged = unary_union(arterial_lines)
            polygons = list(polygonize(merged))
        except Exception as e:
            logger.error(f"Polygonization failed: {e}")
            return

        logger.info(f"Polygonized {len(arterial_lines)} lines into {len(polygons)} polygons")

        # Filter and create cells. OSMnx retains boundary-crossing edges, so
        # polygonization can also produce small polygons just outside the
        # requested area. Clip every candidate before accepting it.
        clipped_polygons: list[Polygon] = []
        for polygon in polygons:
            clipped = polygon.intersection(self.analysis_polygon)
            if isinstance(clipped, Polygon):
                clipped_polygons.append(clipped)
            elif isinstance(clipped, MultiPolygon):
                clipped_polygons.extend(clipped.geoms)

        for polygon in clipped_polygons:
            if polygon.is_empty:
                continue
            if not polygon.is_valid:
                polygon = polygon.buffer(0)

            area_ha = self._calculate_area_hectares(polygon)

            # The administrative boundary closes the outer face, so every
            # non-empty polygon is part of the accounting surface. Size is an
            # optimization objective below, not a reason to silently drop a
            # district and leave its streets unassigned.
            if area_ha <= 0:
                continue

            # Find boundary and interior edges
            boundary_edges, interior_edges = self._classify_edges(polygon)

            # Find entry nodes (nodes on boundary that connect to interior)
            entry_nodes = self._find_entry_nodes(polygon, boundary_edges, interior_edges)

            self.cells.append(
                SuperblockCell(
                    polygon=polygon,
                    area_hectares=area_ha,
                    boundary_edges=boundary_edges,
                    interior_edges=interior_edges,
                    entry_nodes=entry_nodes,
                )
            )

    def _project(self, geometry):
        """``geometry`` in the metric projection, transformed in one array call."""
        project = self._area_transformer

        def coordinates(xy):
            x, y = project(xy[:, 0], xy[:, 1])
            return np.column_stack([x, y])

        return shapely.transform(geometry, coordinates)

    def _calculate_area_hectares(self, polygon: Polygon) -> float:
        """Calculate polygon area in hectares using appropriate projection."""
        if polygon.is_empty:
            return 0.0
        try:
            project = self._area_transformer
            if project is None:
                centroid = self.analysis_polygon.centroid
                utm_zone = int((centroid.x + 180) / 6) + 1
                hemisphere = "north" if centroid.y >= 0 else "south"
                proj_string = f"+proj=utm +zone={utm_zone} +{hemisphere} +datum=WGS84"
                project = pyproj.Transformer.from_crs(
                    "EPSG:4326", proj_string, always_xy=True
                ).transform
                self._area_transformer = project
            return self._project(polygon).area / 10000  # m² to hectares

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

        # Query only edges whose envelopes intersect this cell. The previous
        # full graph scan for every polygon dominated city-scale runtime.
        if self._edge_tree is not None:
            candidate_indices = self._edge_tree.query(polygon, predicate="intersects")
            candidates = (self._edge_records[int(index)] for index in candidate_indices)
        else:
            candidates = (
                (u, v, key, data.get("geometry"))
                for u, v, key, data in self.graph.edges(keys=True, data=True)
            )

        # Prepared geometry makes the repeated containment tests much cheaper,
        # and the ring is computed once instead of once per candidate edge.
        shapely.prepare(polygon)
        ring = polygon.boundary
        shapely.prepare(ring)

        for u, v, key, geom in candidates:
            if geom is None:
                u_data = self.graph.nodes.get(u, {})
                v_data = self.graph.nodes.get(v, {})
                if "x" not in u_data or "x" not in v_data:
                    continue
                geom = LineString([(u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])])

            # Check if edge is inside or on boundary of polygon
            if polygon.contains(geom) or ring.intersects(geom):
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

        # Floating-point polygon boundaries can miss a topologically valid
        # junction. Any interior node attached to the global arterial network is
        # also a genuine entry, even if that arterial edge was classified just
        # outside this cell's geometry.
        entry_nodes.update(interior_nodes.intersection(self._arterial_nodes))

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
        new_cells: list[SuperblockCell] = []
        remaining_indices = set(range(len(self.cells)))

        # Build adjacency
        cell_adjacency = self._build_cell_adjacency()

        for i, cell in enumerate(self.cells):
            if i not in remaining_indices or cell.area_hectares >= self.min_area_ha:
                continue

            # Find the best neighbor among cells that have not already been
            # consumed. Delaying unmerged cells until the end prevents an
            # earlier large neighbor from being emitted and then emitted again
            # as part of a later merge.
            best_neighbor = None
            best_score = float("inf")
            for j in cell_adjacency.get(i, []):
                if j == i or j not in remaining_indices:
                    continue

                neighbor = self.cells[j]
                combined_area = cell.area_hectares + neighbor.area_hectares
                if combined_area <= self.max_area_ha:
                    size_diff = abs(combined_area - self.target_size_ha)
                    if size_diff < best_score:
                        best_score = size_diff
                        best_neighbor = j

            if best_neighbor is not None:
                new_cells.append(self._merge_cells(cell, self.cells[best_neighbor]))
                remaining_indices.remove(i)
                remaining_indices.remove(best_neighbor)
                merged = True

        new_cells.extend(self.cells[index] for index in sorted(remaining_indices))

        self.cells = new_cells
        return merged

    def _split_large_cells(self) -> bool:
        """Split cells larger than max_area_ha until each piece fits or cannot split.

        Each split halves a cell, so a 1,000 ha outer-district cell needs several
        rounds; splitting the pieces again here, instead of once per round of
        the merge/split loop, keeps the round limit from leaving them oversized.
        """
        split = False
        new_cells = []
        queue = deque(self.cells)
        while queue:
            cell = queue.popleft()
            if cell.area_hectares > self.max_area_ha:
                pieces = self._split_cell(cell)
                if len(pieces) > 1:
                    queue.extendleft(reversed(pieces))
                    split = True
                    continue
            new_cells.append(cell)

        self.cells = new_cells
        return split

    def _build_cell_adjacency(self) -> dict[int, list[int]]:
        """Build adjacency list for cells based on shared boundaries."""
        adjacency: dict[int, list[int]] = {i: [] for i in range(len(self.cells))}

        polygons = [cell.polygon for cell in self.cells]
        if not polygons:
            return adjacency
        tree = STRtree(polygons)
        for i, polygon in enumerate(polygons):
            for candidate in tree.query(polygon, predicate="intersects"):
                j = int(candidate)
                if i >= j:
                    continue
                if polygon.touches(polygons[j]) or polygon.boundary.intersects(
                    polygons[j].boundary
                ):
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

        Tries to find an interior main road that can serve as a new
        boundary; a cell far over the maximum size falls back to residential
        streets.
        """
        # Circulation sectors are bounded by main roads only, by design.
        # With the "avoid" policy any cell over the maximum may split along a
        # quiet residential street; otherwise only cells far over it may.
        minor_split_factor = 1.0 if self.minor_boundary_policy == "avoid" else OVERSIZED_CELL_FACTOR
        may_use_minor = (
            self.use_centrality_boundaries
            and cell.area_hectares > minor_split_factor * self.max_area_ha
        )
        # An even split comes first. Failing that, a split may cut off a piece
        # under the minimum size, which the next merge round joins to a
        # neighbour other than its oversized sibling.
        rejected: set[tuple[int, int, int]] = set()
        for _attempt in range(MAX_SPLIT_ATTEMPTS):
            best = None
            quiet_street = False
            for min_piece_ha in (self.min_area_ha, UNEVEN_SPLIT_MIN_FACTOR * self.min_area_ha):
                best = self._best_split(
                    cell,
                    [
                        c
                        for c in self._split_candidates(cell)
                        if (min(c[:2]), max(c[:2]), c[2]) not in rejected
                    ],
                    self._split_edges(cell),
                    min_piece_ha,
                )
                for hierarchies in (MINOR_SPLIT_HIERARCHIES, LIVING_STREET_SPLIT_HIERARCHIES):
                    if best is not None or not may_use_minor:
                        break
                    best = self._best_split(
                        cell,
                        [
                            c
                            for c in self._split_candidates(cell, hierarchies)
                            if (min(c[:2]), max(c[:2]), c[2]) not in rejected
                        ],
                        # A street can change tag along its length (residential
                        # then living street); its whole length is followed.
                        self._split_edges(
                            cell, MINOR_SPLIT_HIERARCHIES | LIVING_STREET_SPLIT_HIERARCHIES
                        ),
                        min_piece_ha,
                    )
                    quiet_street = best is not None
                if best is not None:
                    break
            if best is None:
                return [cell]
            polygons, edge, chain = best
            # The street the cell was split on is now a boundary road of both
            # halves; without this it lies on both rings and belongs to neither.
            promoted_edges = set()
            promoted_nodes = set()
            for a, b, _k in chain:
                promoted_nodes |= {a, b} - self._arterial_nodes
                promoted_edges |= self._promote_to_boundary(a, b)
            pieces = self._cells_from_polygons(polygons)
            # A cut that is not a street (a straight extension past the end of
            # one) can leave a piece whose streets reach no boundary road at
            # all, only the neighbour across the cut: it has no entries and
            # can never be a valid superblock. Try the next street instead.
            if cell.entry_nodes and any(
                piece.interior_edges and not piece.entry_nodes for piece in pieces
            ):
                self.arterial_edges -= promoted_edges
                self._arterial_nodes -= promoted_nodes
                # Both directions and every section of the street, or the
                # next attempt rebuilds the same cut from another of them.
                rejected.update((min(a, b), max(a, b), k) for a, b, k in [edge, *chain])
                continue
            if quiet_street:
                # One filter halfway along stops through traffic between the
                # roads at its ends. A filter on every section would also cut
                # each junction along it off from both ends, stranding the
                # streets behind it.
                a, b, _k = self._middle_section(chain)
                self._quiet_split_streets.add((min(a, b), max(a, b)))
            return pieces
        return [cell]

    def _middle_section(self, chain: list[tuple[int, int, int]]) -> tuple[int, int, int]:
        """The section of a street that holds its halfway point by length."""
        lengths = [float(self.graph.edges[edge].get("length", 0) or 0) for edge in chain]
        half = sum(lengths) / 2
        walked = 0.0
        for edge, length in zip(chain, lengths, strict=True):
            walked += length
            if walked >= half:
                return edge
        return chain[len(chain) // 2]

    def _promote_to_boundary(self, u: int, v: int) -> set[tuple[int, int, int]]:
        """Treat every carriageway between ``u`` and ``v`` as a boundary road.

        Returns the edges that were not boundary roads before.
        """
        added = set()
        for a, b in ((u, v), (v, u)):
            for key in self.graph.get_edge_data(a, b, default={}):
                if (a, b, key) not in self.arterial_edges:
                    added.add((a, b, key))
        self.arterial_edges |= added
        self._arterial_nodes.update((u, v))
        return added

    def _split_street_filters(self, cell: SuperblockCell) -> list[StreetModification]:
        """Modal filters on the quiet residential streets this cell was split from."""
        filters = []
        for u, v, key in cell.boundary_edges:
            street = (min(u, v), max(u, v))
            if street not in self._quiet_split_streets:
                continue
            if street not in self._split_filters:
                data = self.graph[u][v][key]
                nodes = [self.graph.nodes.get(node, {}) for node in (u, v)]
                ids = self._normalize_osm_ids(data.get("osmid"))
                name = data.get("name")
                self._split_filters[street] = self.filter_designer.design(
                    [
                        StreetModification(
                            u=u,
                            v=v,
                            key=key,
                            osm_id=ids[0] if ids else 0,
                            name=str(name[0] if isinstance(name, list) else name) if name else None,
                            modification_type=ModificationType.MODAL_FILTER,
                            filter_location=Coordinates(
                                lat=sum(node.get("y", 0) for node in nodes) / 2,
                                lon=sum(node.get("x", 0) for node in nodes) / 2,
                            ),
                            allowed_modes=[
                                TransportMode.WALK,
                                TransportMode.WHEEL,
                                TransportMode.BICYCLE,
                            ],
                            construction_treatment=(
                                "Accessible pass-through modal filter; exact geometry "
                                "requires survey"
                            ),
                            rationale=(
                                "Modal filter on a residential street that separates two "
                                "superblocks, so through traffic stays on the main roads"
                            ),
                        )
                    ]
                )[0]
            if self._split_filters[street] not in filters:
                filters.append(self._split_filters[street])
        return filters

    def _claim_split_filters(self, cell: SuperblockCell, index: int) -> list[StreetModification]:
        """Split-street filters this cell owns: those no neighbour claimed first."""
        owned = []
        for item in self._split_street_filters(cell):
            street = (min(item.u, item.v), max(item.u, item.v))
            if self._split_filter_owner.setdefault(street, index) == index:
                owned.append(item)
        return owned

    def _best_split(
        self,
        cell: SuperblockCell,
        candidates: list[tuple[int, int, int]],
        streets: list[tuple[int, int, int]] | None = None,
        min_piece_ha: float | None = None,
    ) -> tuple[list[Polygon], tuple[int, int, int], list[tuple[int, int, int]]] | None:
        """The most even two-way split of ``cell``, the street it starts from
        and every edge it runs along.

        With ``streets`` (the edges a split may follow) a candidate is first
        followed along its street to the cell boundary, so the split runs
        down the real street rather than a straight extension of one segment
        that cuts through the blocks beside it.
        """
        adjacency = self._street_adjacency(streets) if streets else None
        min_piece_ha = self.min_area_ha if min_piece_ha is None else min_piece_ha
        best = None
        best_balance = float("inf")
        # Both directions of a two-way street share a key; parallel
        # carriageways between the same junctions do not.
        followed: set[tuple[int, int, int]] = set()

        for edge in candidates:
            if (min(edge[:2]), max(edge[:2]), edge[2]) in followed:
                continue
            lines: list[tuple[LineString, list[tuple[int, int, int]]]] = []
            if adjacency is not None:
                chain = self._street_chain(edge, adjacency)
                followed.update((min(u, v), max(u, v), key) for u, v, key in chain)
                if len(chain) > 1:
                    line = self._chain_line(chain)
                    if line is not None:
                        lines.append((line, chain))
            geom = self._edge_line(*edge)
            if geom is not None:
                lines.append((geom, [edge]))

            for geom, chain in lines:
                try:
                    extended = self._extend_line_to_boundary(
                        geom, cell.polygon, follow_ends=len(chain) > 1
                    )
                    if extended is None:
                        continue
                    result = self._split_polygon_with_line(cell.polygon, extended)
                    if result is None or len(result) < 2:
                        continue
                    areas = [self._calculate_area_hectares(p) for p in result]
                    # A relaxed minimum lets at most one piece fall under the
                    # real one, so the split never leaves only small pieces.
                    if not all(a >= min_piece_ha for a in areas) or (
                        sum(a < self.min_area_ha for a in areas) > 1
                    ):
                        continue
                    # Even areas, and no long thin sliver among the pieces.
                    balance = abs(areas[0] - areas[1]) / sum(areas) + SPLIT_SHAPE_WEIGHT * (
                        1 - min(self._compactness(piece) for piece in result)
                    )
                    if balance < best_balance:
                        best_balance = balance
                        best = (result, edge, chain)
                    # A split along the real street beats a straight cut.
                    break
                except Exception as e:
                    logger.debug(f"Split attempt failed: {e}")
                    continue

        return best

    @staticmethod
    def _compactness(polygon: Polygon) -> float:
        """Polsby-Popper score: 1 for a circle, about 0.3 for an 8:1 strip.

        Longitude is scaled by latitude so the shape is measured on the ground.
        """
        scale = math.cos(math.radians(polygon.centroid.y))
        ground = affinity.scale(polygon, xfact=scale, yfact=1.0, origin=(0, 0))
        return 4 * math.pi * ground.area / ground.length**2 if ground.length else 0.0

    def _edge_line(self, u: int, v: int, key: int) -> LineString | None:
        """The edge's geometry from ``u`` to ``v``."""
        data = self.graph[u][v][key]
        geom = data.get("geometry")
        if geom is not None:
            return geom
        u_data = self.graph.nodes.get(u, {})
        v_data = self.graph.nodes.get(v, {})
        if "x" not in u_data or "x" not in v_data:
            return None
        return LineString([(u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])])

    def _street_label(self, edge: tuple[int, int, int]) -> str:
        """The street's name, or its road type when it has none."""
        data = self.graph[edge[0]][edge[1]][edge[2]]
        label = data.get("name") or data.get("highway", "")
        return str(label[0] if isinstance(label, list) else label)

    @staticmethod
    def _street_adjacency(
        streets: list[tuple[int, int, int]],
    ) -> dict[int, list[tuple[int, tuple[int, int, int]]]]:
        adjacency: dict[int, list[tuple[int, tuple[int, int, int]]]] = {}
        for edge in streets:
            u, v, _key = edge
            adjacency.setdefault(u, []).append((v, edge))
            adjacency.setdefault(v, []).append((u, edge))
        return adjacency

    def _turn_degrees(self, a: int, b: int, c: int) -> float:
        """How far the path a-b-c turns at ``b``, in degrees."""
        points = [self.graph.nodes.get(node, {}) for node in (a, b, c)]
        if any("x" not in point for point in points):
            return 180.0
        # Coordinates are degrees: a degree of longitude shrinks with latitude.
        scale = math.cos(math.radians(points[1]["y"]))
        ax = (points[1]["x"] - points[0]["x"]) * scale
        ay = points[1]["y"] - points[0]["y"]
        bx = (points[2]["x"] - points[1]["x"]) * scale
        by = points[2]["y"] - points[1]["y"]
        if (ax == 0 and ay == 0) or (bx == 0 and by == 0):
            return 180.0
        return abs(math.degrees(math.atan2(ax * by - ay * bx, ax * bx + ay * by)))

    def _street_chain(
        self,
        edge: tuple[int, int, int],
        adjacency: dict[int, list[tuple[int, tuple[int, int, int]]]],
    ) -> list[tuple[int, int, int]]:
        """``edge`` and the same street on either side of it, in order.

        The street is followed through each junction along its straightest
        continuation with the same name, until it reaches a boundary road or
        turns sharply away.
        """
        label = self._street_label(edge)
        u, v, _key = edge
        seen = {u, v}

        def walk(prev: int, node: int) -> list[tuple[tuple[int, int, int], int, int]]:
            path = []
            while len(path) < MAX_CHAIN_EDGES and node not in self._arterial_nodes:
                options = [
                    (self._turn_degrees(prev, node, nbr), nbr, step)
                    for nbr, step in adjacency.get(node, [])
                    if nbr != prev and self._street_label(step) == label
                ]
                if not options:
                    break
                turn, nbr, step = min(options, key=lambda item: item[0])
                if turn > MAX_CHAIN_TURN_DEGREES or nbr in seen:
                    break
                seen.add(nbr)
                path.append((step, node, nbr))
                prev, node = node, nbr
            return path

        backward = walk(v, u)
        forward = walk(u, v)
        # Edges in travel order from the far end behind ``u`` to the far end past ``v``.
        return (
            [step for step, _a, _b in reversed(backward)]
            + [edge]
            + [step for step, _a, _b in forward]
        )

    def _chain_line(self, chain: list[tuple[int, int, int]]) -> LineString | None:
        """One line along consecutive edges, each turned to run the same way."""
        coords: list[tuple[float, float]] = []
        for index, (u, v, key) in enumerate(chain):
            geom = self._edge_line(u, v, key)
            if geom is None:
                return None
            points = list(geom.coords)
            if index == 0:
                # Orient the first edge towards the second.
                if len(chain) > 1 and u in chain[1][:2] and v not in chain[1][:2]:
                    points.reverse()
            elif coords and Point(points[-1]).distance(Point(coords[-1])) < Point(
                points[0]
            ).distance(Point(coords[-1])):
                points.reverse()
            coords.extend(points if not coords else points[1:])
        line = LineString(coords)
        return line if line.is_simple else None

    def _cells_from_polygons(self, polygons: list[Polygon]) -> list[SuperblockCell]:
        new_cells = []
        for polygon in polygons:
            if not polygon.is_valid:
                polygon = polygon.buffer(0)

            boundary_edges, interior_edges = self._classify_edges(polygon)
            entry_nodes = self._find_entry_nodes(polygon, boundary_edges, interior_edges)

            new_cells.append(
                SuperblockCell(
                    polygon=polygon,
                    area_hectares=self._calculate_area_hectares(polygon),
                    boundary_edges=boundary_edges,
                    interior_edges=interior_edges,
                    entry_nodes=entry_nodes,
                )
            )

        return new_cells

    def _split_edges(
        self, cell: SuperblockCell, hierarchies: frozenset[int] | None = None
    ) -> list[tuple[int, int, int]]:
        """Interior edges a split line may run along: main roads, or ``hierarchies``."""
        edges = []
        for u, v, key in cell.interior_edges:
            if not self.graph.has_edge(u, v, key):
                continue
            highway = self.graph[u][v][key].get("highway", "")
            if isinstance(highway, list):
                highway = highway[0]
            if hierarchies is None:
                # Main roads only by default
                if HIERARCHY_MAP.get(highway, 6) > MAIN_ROAD_SPLIT_HIERARCHY:
                    continue
            # Unknown types such as busway or road never qualify as a fallback,
            # and busy rat runs stay inside cells to be filtered, not bounded.
            elif (
                HIERARCHY_MAP.get(highway) not in hierarchies
                or (min(u, v), max(u, v)) in self._busy_minor_streets
            ):
                continue
            edges.append((u, v, key))
        return edges

    def _split_candidates(
        self, cell: SuperblockCell, hierarchies: frozenset[int] | None = None
    ) -> list[tuple[int, int, int]]:
        """Interior main-road edges to try as split lines, nearest the centre first.

        ``hierarchies`` picks other road levels instead of main roads.

        A line through the middle of a cell gives the most even split. Large
        circulation-sector cells hold thousands of candidate edges, and
        clipping each against the cell polygon dominated the run, so only the
        nearest ``MAX_SPLIT_CANDIDATES`` are tried.
        """
        candidates: list[tuple[float, tuple[int, int, int]]] = []
        centroid = cell.polygon.centroid
        for u, v, key in self._split_edges(cell, hierarchies):
            u_data = self.graph.nodes.get(u, {})
            v_data = self.graph.nodes.get(v, {})
            if "x" in u_data and "x" in v_data:
                mid_x = (u_data["x"] + v_data["x"]) / 2
                mid_y = (u_data["y"] + v_data["y"]) / 2
                distance = (mid_x - centroid.x) ** 2 + (mid_y - centroid.y) ** 2
            else:
                distance = float("inf")
            candidates.append((distance, (u, v, key)))
        if len(candidates) > MAX_SPLIT_CANDIDATES:
            candidates.sort(key=lambda item: item[0])
            candidates = candidates[:MAX_SPLIT_CANDIDATES]
        return [edge for _distance, edge in candidates]

    def _extend_line_to_boundary(
        self, line: LineString, polygon: Polygon, follow_ends: bool = False
    ) -> LineString | None:
        """Extend a line to intersect the polygon boundary on both ends.

        The line is extended along its overall direction, or with
        ``follow_ends`` (a split along a winding street) along the direction
        each end is heading.
        """
        try:
            coords = list(line.coords)
            if len(coords) < 2:
                return None

            def unit(a, b):
                dx, dy = b[0] - a[0], b[1] - a[1]
                length = math.sqrt(dx * dx + dy * dy)
                return (dx / length, dy / length) if length else None

            if follow_ends:
                start_dir = unit(coords[1], coords[0])
                end_dir = unit(coords[-2], coords[-1])
            else:
                overall = unit(coords[0], coords[-1])
                start_dir = (-overall[0], -overall[1]) if overall else None
                end_dir = overall
            if start_dir is None or end_dir is None:
                return None

            # Extend factor (enough to reach boundary)
            extend = polygon.bounds[2] - polygon.bounds[0]  # Width of bbox

            # Extend both ends
            new_start = (coords[0][0] + start_dir[0] * extend, coords[0][1] + start_dir[1] * extend)
            new_end = (coords[-1][0] + end_dir[0] * extend, coords[-1][1] + end_dir[1] * extend)

            extended = LineString([new_start, *list(coords), new_end])

            # Clip to polygon
            clipped = extended.intersection(polygon)
            if clipped.is_empty:
                return None

            if isinstance(clipped, LineString):
                return self._past_boundary(clipped)
            if isinstance(clipped, MultiLineString):
                # A concave cell or one with holes cuts the line into pieces;
                # keep the piece that holds the road itself.
                middle = line.interpolate(0.5, normalized=True)
                piece = min(clipped.geoms, key=lambda part: part.distance(middle))
                return self._past_boundary(piece) if piece.distance(middle) < 1e-9 else None

            return None

        except Exception:
            return None

    @staticmethod
    def _past_boundary(line: LineString) -> LineString:
        """``line`` with each end pushed a few centimetres further out.

        A line clipped to a polygon ends on its boundary only up to rounding,
        and a split line that stops a hair short of the boundary splits
        nothing; this kept about a third of the cells over twice the maximum
        size from ever splitting.
        """
        coords = list(line.coords)

        def beyond(end, previous):
            dx, dy = end[0] - previous[0], end[1] - previous[1]
            length = math.hypot(dx, dy)
            if not length:
                return end
            return (end[0] + dx / length * SPLIT_OVERSHOOT, end[1] + dy / length * SPLIT_OVERSHOOT)

        return LineString(
            [beyond(coords[0], coords[1]), *coords[1:-1], beyond(coords[-1], coords[-2])]
        )

    def _split_polygon_with_line(self, polygon: Polygon, line: LineString) -> list[Polygon] | None:
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
            if superblock is None and not cell.interior_edges:
                self.non_street_cells += 1
            if superblock is not None:
                superblock.cell_kind = self._cell_kind(cell)
                if cell.area_hectares > self.max_area_ha:
                    superblock.street_area_hectares = round(self._street_area_hectares(cell), 2)
                superblock.size_status = self._size_status(
                    cell.area_hectares, superblock.street_area_hectares
                )
                self.superblocks.append(superblock)
                self._superblock_cells[superblock.id] = cell
                self._superblock_index[superblock.id] = i
                # Both neighbours see the filter on a shared split street; one owns it.
                shared = [
                    item
                    for item in self._split_street_filters(cell)
                    if self._split_filter_owner.get((min(item.u, item.v), max(item.u, item.v))) != i
                ]
                self._impact_inputs.append(
                    SuperblockImpactInput(
                        superblock_id=superblock.id,
                        interior_edges=list(cell.interior_edges),
                        boundary_edges=list(cell.boundary_edges),
                        entry_sectors={
                            entry.node_id: entry.sector for entry in superblock.entry_points
                        },
                        modifications=list(superblock.modifications) + shared,
                    )
                )

    def _live_boundary_nodes(self) -> set[int]:
        """Nodes joined to a main road along boundary roads open to cars.

        Boundary roads are the main roads and the streets cells were split
        along; a split street is closed only at its filter. Computed once,
        after every split is made.
        """
        if self._live_nodes is None:
            network = nx.Graph()
            network.add_nodes_from(self._main_road_nodes)
            network.add_edges_from(
                (u, v)
                for u, v, _key in self.arterial_edges
                if (min(u, v), max(u, v)) not in self._quiet_split_streets
            )
            self._live_nodes = {
                node
                for component in nx.connected_components(network)
                if component & self._main_road_nodes
                for node in component
            }
        return self._live_nodes

    def _repair_access(self) -> None:
        """Reopen the fewest access changes that leave streets unreachable by car.

        Each superblock is designed and validated on its own, treating every
        junction on its boundary as reachable from outside. Filters in
        neighbouring superblocks, and the filter on a street two of them were
        split along, can together cut a junction off from every main road,
        stranding the streets behind it. This pass routes on the whole planned
        city and, for each stranded pocket, reopens one access change on its
        edge, the one least likely to reopen a through route: a one-way first,
        then a filter inside a superblock, and a split-street filter last.
        The superblocks it touches are validated again.
        """
        if not self.superblocks:
            return
        owner: dict[int, EnforcedSuperblock] = {}
        changes: list[StreetModification] = []
        for superblock in self.superblocks:
            for item in superblock.modifications:
                if id(item) not in owner:
                    owner[id(item)] = superblock
                    changes.append(item)
        if not changes:
            return
        planned = TurnGraphBuilder(self.graph)._prepare_graph(VehicleClass.CAR, changes)
        baseline_core = max(nx.strongly_connected_components(self.graph), key=len)
        core = max(nx.strongly_connected_components(planned), key=len)
        stranded = baseline_core - core
        if not stranded:
            return
        quiet = {id(item) for item in self._split_filters.values()}

        def priority(item: StreetModification) -> int:
            if item.modification_type == ModificationType.ONE_WAY:
                return 0
            return 1 if id(item) in quiet else 2

        def removed_edges(item: StreetModification) -> list[tuple[int, int, int, dict]]:
            if item.modification_type == ModificationType.ONE_WAY:
                pairs = [(item.v, item.u)] if item.direction == "u_to_v" else [(item.u, item.v)]
            else:
                pairs = [(item.u, item.v), (item.v, item.u)]
            return [
                (a, b, key, data)
                for a, b in pairs
                if self.graph.has_edge(a, b) and not planned.has_edge(a, b)
                for key, data in self.graph[a][b].items()
            ]

        def reaches_core(node: int, graph) -> bool:
            seen = {node}
            queue = deque([node])
            while queue:
                current = queue.popleft()
                if current in core:
                    return True
                for nxt in graph.successors(current):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
            return False

        by_node: dict[int, list[StreetModification]] = defaultdict(list)
        for item in changes:
            if item.modification_type not in REPAIRABLE_CHANGES:
                continue
            by_node[item.u].append(item)
            by_node[item.v].append(item)
        reopened: set[int] = set()
        undirected = planned.to_undirected(as_view=True)
        pockets = sorted(
            nx.connected_components(undirected.subgraph(stranded)), key=lambda p: min(p)
        )
        # A pocket behind another pocket reconnects only once the nearer one has.
        for _round in range(MAX_ACCESS_REPAIR_ROUNDS):
            remaining = [pocket for pocket in pockets if min(pocket) not in core]
            if not remaining:
                break
            before = len(reopened)
            for pocket in remaining:
                self._reopen_one(
                    pocket, core, planned, by_node, reopened, priority, removed_edges, reaches_core
                )
            if len(reopened) == before:
                break
        self.stranded_junctions = len(stranded - core)
        if not reopened:
            return
        self.reopened_for_access += len(reopened)
        for item_id in reopened:
            self._split_filters = {
                street: item for street, item in self._split_filters.items() if id(item) != item_id
            }
        self._keep_open |= {
            (min(owner_item.u, owner_item.v), max(owner_item.u, owner_item.v))
            for owner_item in changes
            if id(owner_item) in reopened and id(owner_item) not in quiet
        }
        touched = {owner[item_id].id for item_id in reopened}
        for superblock in self.superblocks:
            if superblock.id not in touched:
                continue
            superblock.modifications = [
                item for item in superblock.modifications if id(item) not in reopened
            ]
            self._revalidate(superblock)
        for impact_input in self._impact_inputs:
            impact_input.modifications = [
                item for item in impact_input.modifications if id(item) not in reopened
            ]

    @staticmethod
    def _reopen_one(
        pocket, core, planned, by_node, reopened, priority, removed_edges, reaches_core
    ):
        """Reopen the first access change on ``pocket``'s edge that reconnects it."""
        node = min(pocket)
        border = {
            id(item): item
            for member in pocket
            for item in by_node.get(member, [])
            if id(item) not in reopened and ((item.u in pocket) != (item.v in pocket))
        }
        for item in sorted(border.values(), key=priority):
            added = removed_edges(item)
            if not added:
                continue
            for a, b, key, data in added:
                planned.add_edge(a, b, key=key, **data)
            if reaches_core(node, planned) and reaches_core(node, planned.reverse(copy=False)):
                reopened.add(id(item))
                core |= pocket
                return
            for a, b, key, _data in added:
                planned.remove_edge(a, b, key)

    def _redesign_after_repair(self) -> bool:
        """Design again the superblocks a reopened street left with a through route.

        The reopened streets are kept open, so the solver looks for another
        cut; the whole plan is then checked for stranded streets once more.
        """
        if not self._keep_open:
            return False
        redesigned_any = False
        for position, superblock in enumerate(self.superblocks):
            if superblock.constraint_validated:
                continue
            cell = self._superblock_cells[superblock.id]
            if not any(
                (min(u, v), max(u, v)) in self._keep_open for u, v, _key in cell.interior_edges
            ):
                continue
            index = self._superblock_index[superblock.id]
            redesigned = self._enforce_cell_constraints(cell, index)
            if redesigned is None or not redesigned.constraint_validated:
                continue
            self._install_redesign(position, redesigned)
            redesigned_any = True
        if redesigned_any:
            self._repair_access()
        return redesigned_any

    def _install_redesign(self, position: int, redesigned: EnforcedSuperblock) -> None:
        superblock = self.superblocks[position]
        for field_name in ("cell_kind", "size_status", "street_area_hectares"):
            setattr(redesigned, field_name, getattr(superblock, field_name))
        redesigned.id = superblock.id
        self.superblocks[position] = redesigned
        for impact_input in self._impact_inputs:
            if impact_input.superblock_id == superblock.id:
                shared = [
                    item
                    for item in impact_input.modifications
                    if item in self._split_filters.values() and item not in superblock.modifications
                ]
                impact_input.modifications = list(redesigned.modifications) + shared
                impact_input.entry_sectors = {
                    entry.node_id: entry.sector for entry in redesigned.entry_points
                }

    def _gate_unresolved(self) -> None:
        """Close the last through routes with destination-only gates.

        Some superblocks keep a through route because every cut that closes it
        strands streets: the access repair reopens the cut, the solver puts it
        back. There the closing design goes in anyway, and each cut that would
        leave a street without access becomes a camera-enforced
        "except destination traffic" gate (célforgalom): residents, deliveries
        and visitors still drive in, through traffic does not.
        """
        if not self._keep_open:
            return
        changes = [item for sb in self.superblocks for item in sb.modifications]
        access = TurnGraphBuilder(self.graph)._prepare_graph(VehicleClass.PERMIT, changes)
        core = max(nx.strongly_connected_components(access), key=len)
        for position, superblock in enumerate(self.superblocks):
            if superblock.constraint_validated:
                continue
            cell = self._superblock_cells[superblock.id]
            if not any(
                (min(u, v), max(u, v)) in self._keep_open for u, v, _key in cell.interior_edges
            ):
                continue
            redesigned = self._enforce_cell_constraints(cell, self._superblock_index[superblock.id])
            if redesigned is None or not redesigned.constraint_validated:
                continue
            shared = list(self._split_filters.values())
            for item in superblock.modifications:
                if item in shared:
                    continue
                for a, b, key, data in self._closed_edges(item, access, present=False):
                    access.add_edge(a, b, key=key, **data)
            gated = []
            for item in redesigned.modifications:
                closed = self._closed_edges(item, access, present=True)
                for a, b, key, _data in closed:
                    access.remove_edge(a, b, key)
                if all(
                    not (a in core and b in core) or nx.has_path(access, a, b)
                    for a, b, _key, _data in closed
                ):
                    gated.append(item)
                    continue
                for a, b, key, data in closed:
                    access.add_edge(a, b, key=key, **data)
                gated.append(self._destination_gate(item))
                self.destination_gates += 1
            redesigned.modifications = gated
            self._install_redesign(position, redesigned)

    def _gate_stranded(self) -> None:
        """Let residents through the changes around pockets still cut off.

        The access repair reopens one change per stranded pocket; a pocket
        ringed by several filters stays cut off. Here the changes on and in it
        become destination-only gates, one at a time, until residents can
        drive in and out again; through traffic stays out.
        """
        owner: dict[int, EnforcedSuperblock] = {}
        for superblock in self.superblocks:
            for item in superblock.modifications:
                owner.setdefault(id(item), superblock)
        changes = [item for sb in self.superblocks for item in sb.modifications]
        access = TurnGraphBuilder(self.graph)._prepare_graph(VehicleClass.PERMIT, changes)
        baseline_core = max(nx.strongly_connected_components(self.graph), key=len)
        core = max(nx.strongly_connected_components(access), key=len)
        stranded = baseline_core - core
        if not stranded:
            self.stranded_junctions = 0
            return
        by_node: dict[int, list[StreetModification]] = defaultdict(list)
        for item in changes:
            if self._closed_edges(item, self.graph, present=True):
                by_node[item.u].append(item)
                by_node[item.v].append(item)
        replaced: dict[int, StreetModification] = {}
        pockets = sorted(
            nx.weakly_connected_components(access.subgraph(stranded)), key=lambda p: min(p)
        )
        anchor = next(iter(core))

        def connected(pocket: set[int]) -> bool:
            return all(
                nx.has_path(access, node, anchor) and nx.has_path(access, anchor, node)
                for node in pocket
            )

        # A pocket behind another pocket reconnects only once the nearer one has.
        for _round in range(MAX_ACCESS_REPAIR_ROUNDS):
            progress = False
            for pocket in pockets:
                node = min(pocket)
                if node in core:
                    continue
                if connected(pocket):
                    core |= pocket
                    progress = True
                    continue
                border = [
                    item
                    for item in {
                        id(item): item
                        for member in pocket
                        for item in by_node.get(member, [])
                        # One-way streets can strand part of a pocket from inside.
                        if id(item) not in replaced
                    }.values()
                ]
                opened = []
                for item in border:
                    added = self._closed_edges(item, access, present=False)
                    for a, b, key, data in added:
                        access.add_edge(a, b, key=key, **data)
                    opened.append((item, added))
                    if connected(pocket):
                        break
                else:
                    # Not enough on its own; maybe once a neighbour reconnects.
                    for _item, added in opened:
                        for a, b, key, _data in added:
                            access.remove_edge(a, b, key)
                    continue
                for item, _added in opened:
                    replaced[id(item)] = self._destination_gate(item)
                core |= pocket
                progress = True
            if not progress:
                break
        self.stranded_junctions = len(stranded - core)
        if not replaced:
            return
        self.destination_gates += len(replaced)
        for superblock in self.superblocks:
            superblock.modifications = [
                replaced.get(id(item), item) for item in superblock.modifications
            ]
        for impact_input in self._impact_inputs:
            impact_input.modifications = [
                replaced.get(id(item), item) for item in impact_input.modifications
            ]

    def _closed_edges(
        self, item: StreetModification, graph, *, present: bool
    ) -> list[tuple[int, int, int, dict]]:
        """Directed source edges ``item`` closes to residents' cars.

        Only those that are (``present``) or are not in ``graph`` are returned.
        """
        if VehicleClass.PERMIT in item.allowed_vehicle_classes:
            return []
        if item.modification_type == ModificationType.ONE_WAY:
            pairs = [(item.v, item.u)] if item.direction == "u_to_v" else [(item.u, item.v)]
        elif item.modification_type in (
            ModificationType.MODAL_FILTER,
            ModificationType.FULL_CLOSURE,
        ):
            pairs = [(item.u, item.v), (item.v, item.u)]
        else:
            return []
        return [
            (a, b, key, data)
            for a, b in pairs
            if self.graph.has_edge(a, b) and graph.has_edge(a, b) == present
            for key, data in self.graph[a][b].items()
        ]

    @staticmethod
    def _destination_gate(item: StreetModification) -> StreetModification:
        classes = list(item.allowed_vehicle_classes)
        classes += [c for c in (VehicleClass.PERMIT, VehicleClass.EMERGENCY) if c not in classes]
        update = {
            "allowed_vehicle_classes": classes,
            "rationale": (
                f"{item.rationale or ''} Destination traffic only: every full closure here "
                "would leave streets without car access."
            ).strip(),
        }
        if item.modification_type != ModificationType.ONE_WAY and item.filter_type != "bus_gate":
            update |= {
                "modification_type": ModificationType.MODAL_FILTER,
                "filter_type": "camera_gate",
                "construction_treatment": (
                    "'Kivéve célforgalom' sign with an enforcement camera: residents, "
                    "deliveries and visitors pass; through traffic is fined"
                ),
            }
        return item.model_copy(update=update)

    def _seal_crossings(self) -> None:
        """Close routes that cross from one superblock straight into the next.

        Each superblock keeps traffic between its own entries local, but where
        a street of one meets a street of its neighbour at a junction on the
        residential street between them, a car can drive in from one main
        road, cross, and leave on another main road through the neighbour.
        Neither superblock's own check sees that route. For each such route a
        filter goes on the first street of the second superblock, or failing
        that the last street of the first, provided it leaves every junction
        reachable by car.
        """
        if not self.superblocks:
            return
        changes = [item for sb in self.superblocks for item in sb.modifications]
        builder = TurnGraphBuilder(self.graph)
        planned = builder._prepare_graph(VehicleClass.CAR, changes)
        # Access is judged for residents, who may pass destination-only gates.
        access = builder._prepare_graph(VehicleClass.PERMIT, changes)
        core = max(nx.strongly_connected_components(access), key=len)
        owner: dict[tuple[int, int], EnforcedSuperblock] = {}
        for superblock in self.superblocks:
            for u, v, _key in self._superblock_cells[superblock.id].interior_edges:
                owner[(u, v)] = superblock
        main = self._main_road_nodes
        on_main = {
            (u, v)
            for u, v, _key in self._main_road_edges
            if (min(u, v), max(u, v)) not in self._quiet_split_streets
        }
        entries: dict[str, set[int]] = defaultdict(set)
        for (u, v), superblock in owner.items():
            for node in (u, v):
                if node in main:
                    entries[superblock.id].add(node)

        def minor_successors(node: int):
            for nxt in planned.successors(node):
                if (node, nxt) not in on_main:
                    yield nxt

        def still_connected(u: int, v: int) -> bool:
            removed = {
                graph: [
                    (a, b, key, data)
                    for a, b in ((u, v), (v, u))
                    if graph.has_edge(a, b)
                    for key, data in list(graph[a][b].items())
                ]
                for graph in (planned, access)
            }
            for a, b, key, _data in removed[access]:
                access.remove_edge(a, b, key)
            # The street stays strongly connected exactly when its two ends can
            # still reach each other both ways without it.
            ok = not (u in core and v in core) or (
                nx.has_path(access, u, v) and nx.has_path(access, v, u)
            )
            if not ok:
                for a, b, key, data in removed[access]:
                    access.add_edge(a, b, key=key, **data)
                return False
            for a, b, key, _data in removed[planned]:
                planned.remove_edge(a, b, key)
            return True

        def leak_from(start: int, home: str) -> list[int] | None:
            parent = {start: None}
            queue = deque([start])
            while queue:
                node = queue.popleft()
                for nxt in minor_successors(node):
                    if nxt in parent:
                        continue
                    parent[nxt] = node
                    if nxt in main:
                        other = owner.get((node, nxt))
                        if (
                            other is not None
                            and other.id != home
                            and nxt not in entries[home]
                            and nxt in entries[other.id]
                        ):
                            path = [nxt]
                            while parent[path[-1]] is not None:
                                path.append(parent[path[-1]])
                            return path[::-1]
                        continue
                    queue.append(nxt)
            return None

        sealed = 0
        for superblock in list(self.superblocks):
            for start in sorted(entries[superblock.id]):
                for _attempt in range(MAX_CROSSING_SEALS_PER_ENTRY):
                    path = leak_from(start, superblock.id)
                    if path is None:
                        break
                    steps = list(itertools.pairwise(path))
                    crossing = next(
                        i
                        for i, step in enumerate(steps)
                        if owner.get(step) is not None and owner[step].id != superblock.id
                    )
                    candidates = [steps[crossing]]
                    if crossing > 0:
                        candidates.append(steps[crossing - 1])
                    for u, v in candidates:
                        target = owner.get((u, v))
                        if target is None or not still_connected(u, v):
                            continue
                        target.modifications.append(self._crossing_filter(u, v))
                        target.modal_filter_count += 1
                        sealed += 1
                        break
                    else:
                        # Every filter here cuts a street off: let residents through.
                        u, v = candidates[0]
                        for a, b in ((u, v), (v, u)):
                            if planned.has_edge(a, b):
                                for key in list(planned[a][b]):
                                    planned.remove_edge(a, b, key)
                        target = owner[(u, v)]
                        target.modifications.append(
                            self._destination_gate(self._crossing_filter(u, v))
                        )
                        target.modal_filter_count += 1
                        sealed += 1
                        self.destination_gates += 1
        self.crossings_sealed = sealed
        if sealed:
            by_id = {sb.id: sb for sb in self.superblocks}
            for impact_input in self._impact_inputs:
                superblock = by_id.get(impact_input.superblock_id)
                if superblock is None:
                    continue
                known = {id(item) for item in impact_input.modifications}
                impact_input.modifications.extend(
                    item for item in superblock.modifications if id(item) not in known
                )

    def _crossing_filter(self, u: int, v: int) -> StreetModification:
        key, data = min(
            self.graph[u][v].items(), key=lambda item: float(item[1].get("length", 0) or 0)
        )
        nodes = [self.graph.nodes.get(node, {}) for node in (u, v)]
        ids = self._normalize_osm_ids(data.get("osmid"))
        name = data.get("name")
        return self.filter_designer.design(
            [
                StreetModification(
                    u=u,
                    v=v,
                    key=key,
                    osm_id=ids[0] if ids else 0,
                    name=str(name[0] if isinstance(name, list) else name) if name else None,
                    modification_type=ModificationType.MODAL_FILTER,
                    filter_location=Coordinates(
                        lat=sum(node.get("y", 0) for node in nodes) / 2,
                        lon=sum(node.get("x", 0) for node in nodes) / 2,
                    ),
                    allowed_modes=[TransportMode.WALK, TransportMode.WHEEL, TransportMode.BICYCLE],
                    construction_treatment=(
                        "Accessible pass-through modal filter; exact geometry requires survey"
                    ),
                    rationale=(
                        "Modal filter where this superblock meets its neighbour across a "
                        "residential street, so traffic cannot cross from one into the other"
                    ),
                )
            ]
        )[0]

    def _revalidate(self, superblock: EnforcedSuperblock) -> None:
        """Validate a superblock again after access changes were reopened."""
        cell = self._superblock_cells[superblock.id]
        sectors: dict[int, list[int]] = defaultdict(list)
        for entry in superblock.entry_points:
            sectors[entry.sector].append(entry.node_id)
        turn_valid, vehicle_class_validation = self._validate_turn_aware_constraints(
            self._build_interior_subgraph(cell), superblock.modifications, dict(sectors)
        )
        superblock.constraint_validated = turn_valid
        superblock.modeled_directional_validation_passed = turn_valid
        superblock.turn_aware_validation_passed = turn_valid
        superblock.vehicle_class_validation = vehicle_class_validation
        counts = defaultdict(int)
        for item in superblock.modifications:
            counts[item.modification_type] += 1
        superblock.modal_filter_count = counts[ModificationType.MODAL_FILTER]
        superblock.one_way_conversion_count = counts[ModificationType.ONE_WAY]
        superblock.street_cut_count = counts[ModificationType.FULL_CLOSURE]
        superblock.two_way_conversion_count = counts[ModificationType.TWO_WAY]

    def _cell_kind(self, cell: SuperblockCell) -> CellKind:
        """Read whether a cell has through routes to remove at all.

        A cell whose interior streets never join two entries is already calm:
        every street inside is a dead end off one main road, so it needs no
        filters and its size does not matter. A cell with no streets inside is
        land the street network does not reach.
        """
        if not cell.interior_edges:
            return CellKind.NON_STREET_LAND
        if not cell.entry_nodes:
            return CellKind.SUPERBLOCK
        streets = nx.Graph()
        streets.add_edges_from((u, v) for u, v, _ in cell.interior_edges)
        entries = set(cell.entry_nodes)
        for component in nx.connected_components(streets):
            if len(entries & component) > 1:
                return CellKind.SUPERBLOCK
        return CellKind.NATURALLY_CALM

    def _street_area_hectares(self, cell: SuperblockCell) -> float:
        """Land in ``cell`` within STREET_REACH_M of a street inside or around it."""
        lines = [
            self._edge_line(u, v, key) for u, v, key in [*cell.interior_edges, *cell.boundary_edges]
        ]
        lines = [line for line in lines if line is not None]
        if not lines:
            return 0.0
        self._calculate_area_hectares(cell.polygon)  # sets up the projection
        project = self._area_transformer
        if project is None:
            return cell.area_hectares
        reach = unary_union([self._project(line) for line in lines]).buffer(STREET_REACH_M)
        return reach.intersection(self._project(cell.polygon)).area / 10000

    def _size_status(
        self, area_hectares: float, street_area_hectares: float | None = None
    ) -> SizeStatus:
        if area_hectares > self.max_area_ha:
            if (
                street_area_hectares is not None
                and street_area_hectares < OPEN_LAND_STREET_SHARE * area_hectares
            ):
                return SizeStatus.MOSTLY_OPEN_LAND
            return SizeStatus.OVER_MAXIMUM
        if area_hectares < self.min_area_ha:
            return SizeStatus.UNDER_MINIMUM
        return SizeStatus.WITHIN

    def _enforce_cell_constraints(
        self, cell: SuperblockCell, index: int
    ) -> EnforcedSuperblock | None:
        """
        Enforce constraints for a single cell and create an EnforcedSuperblock.
        """
        if not cell.entry_nodes:
            # A road-bearing cell without a connection to the boundary network
            # is not a valid access design. Empty faces are accounting areas,
            # not superblocks.
            if not cell.interior_edges:
                return None
            return self._create_simple_superblock(cell, index, constraint_validated=False)

        # Build interior subgraph
        interior_graph = self._build_interior_subgraph(cell)

        if interior_graph.number_of_edges() == 0:
            return self._create_simple_superblock(cell, index)

        # Create constraint enforcer. A junction on a filtered split street
        # with no open way along the boundary roads to a main road is no real
        # entry: designing as if traffic could arrive there strands the streets
        # behind it. It is reached from the superblock's other entries instead.
        live = self._live_boundary_nodes()
        entry_nodes = [node for node in cell.entry_nodes if node in live] or cell.entry_nodes
        enforcer = ConstraintEnforcer(
            interior_graph=interior_graph,
            boundary_polygon=cell.polygon,
            entry_node_ids=entry_nodes,
            num_sectors=self.num_sectors,
        )

        # Enforce constraints, or validate the unmodified design when enforcement
        # was explicitly disabled.
        try:
            if self.enforce_constraints:
                modifications, violations = enforcer.enforce_constraints()
            else:
                enforcer.sectors = enforcer._assign_sectors()
                modifications = []
                violations = enforcer._find_violations()
        except Exception as e:
            logger.warning(f"Constraint enforcement failed for cell {index}: {e}")
            return self._create_simple_superblock(cell, index, constraint_validated=False)
        # Design each action for buses and emergency vehicles. Car topology is
        # unchanged, so the validations below still test the solver's result.
        modifications = self.filter_designer.design(modifications)

        # Build entry points with sector info
        entry_points = []
        if enforcer.sectors:
            for sector, nodes in enforcer.sectors.entry_points_by_sector.items():
                for node_id in nodes:
                    node_data = self.graph.nodes.get(node_id, {})
                    entry_points.append(
                        EntryPoint(
                            node_id=node_id,
                            sector=sector,
                            coordinates=Coordinates(
                                lat=node_data.get("y", 0),
                                lon=node_data.get("x", 0),
                            ),
                            boundary_road_id=self._boundary_road_id_for_node(cell, node_id),
                        )
                    )

        access_targets = self._access_targets_for_cell(cell.polygon)
        unreachable_targets = self._find_unreachable_access_targets(
            interior_graph,
            modifications,
            cell.entry_nodes,
            enforcer.sectors,
            access_targets,
        )
        turn_valid, vehicle_class_validation = self._validate_turn_aware_constraints(
            interior_graph,
            modifications,
            enforcer.sectors.entry_points_by_sector if enforcer.sectors else {},
        )

        # Filters on residential streets this cell was split from are added
        # after validation: they sit on boundary roads outside the interior graph.
        modifications = [*modifications, *self._claim_split_filters(cell, index)]

        # Create EnforcedSuperblock
        return EnforcedSuperblock(
            id=f"sb_{index}_{uuid.uuid4().hex[:8]}",
            geometry=self._polygon_to_geojson(cell.polygon),
            area_hectares=cell.area_hectares,
            num_sectors=self.num_sectors,
            boundary_roads=self._collect_boundary_osm_ids(cell.boundary_edges),
            entry_points=entry_points,
            modifications=modifications,
            constraint_validated=len(violations) == 0 and turn_valid,
            all_addresses_reachable=None,
            unreachable_addresses=[],
            modeled_directional_validation_passed=turn_valid,
            turn_aware_validation_passed=turn_valid,
            vehicle_class_validation=vehicle_class_validation,
            access_target_count=len(access_targets),
            all_access_targets_reachable=(
                len(unreachable_targets) == 0 if access_targets else None
            ),
            unreachable_access_targets=unreachable_targets,
            interior_roads_count=len(cell.interior_edges),
            modal_filter_count=sum(
                1 for m in modifications if m.modification_type.value == "modal_filter"
            ),
            one_way_conversion_count=sum(
                1 for m in modifications if m.modification_type.value == "one_way"
            ),
            street_cut_count=sum(
                1 for m in modifications if m.modification_type.value == "full_closure"
            ),
            two_way_conversion_count=sum(
                1 for m in modifications if m.modification_type.value == "two_way"
            ),
        )

    def _create_simple_superblock(
        self,
        cell: SuperblockCell,
        index: int,
        *,
        constraint_validated: bool = True,
    ) -> EnforcedSuperblock:
        """Create a superblock without constraint enforcement (no cross-sector paths possible)."""
        interior_graph = self._build_interior_subgraph(cell)
        sector_by_node: dict[int, int] = {}
        sectors = None
        if cell.entry_nodes:
            enforcer = ConstraintEnforcer(
                interior_graph=interior_graph,
                boundary_polygon=cell.polygon,
                entry_node_ids=cell.entry_nodes,
                num_sectors=self.num_sectors,
            )
            sectors = enforcer._assign_sectors()
            sector_by_node = sectors.node_to_sector

        entry_points = []
        for node_id in cell.entry_nodes:
            node_data = self.graph.nodes.get(node_id, {})
            entry_points.append(
                EntryPoint(
                    node_id=node_id,
                    sector=sector_by_node.get(node_id, 0),
                    coordinates=Coordinates(
                        lat=node_data.get("y", 0),
                        lon=node_data.get("x", 0),
                    ),
                    boundary_road_id=self._boundary_road_id_for_node(cell, node_id),
                )
            )

        access_targets = self._access_targets_for_cell(cell.polygon)
        unreachable_targets = self._find_unreachable_access_targets(
            interior_graph,
            [],
            cell.entry_nodes,
            sectors,
            access_targets,
        )
        turn_valid, vehicle_class_validation = self._validate_turn_aware_constraints(
            interior_graph,
            [],
            sectors.entry_points_by_sector if sectors else {},
        )
        turn_valid = turn_valid and constraint_validated
        split_filters = self._claim_split_filters(cell, index)

        return EnforcedSuperblock(
            id=f"sb_{index}_{uuid.uuid4().hex[:8]}",
            geometry=self._polygon_to_geojson(cell.polygon),
            area_hectares=cell.area_hectares,
            num_sectors=self.num_sectors,
            boundary_roads=self._collect_boundary_osm_ids(cell.boundary_edges),
            entry_points=entry_points,
            modifications=split_filters,
            constraint_validated=turn_valid,
            all_addresses_reachable=None,
            unreachable_addresses=[],
            modeled_directional_validation_passed=turn_valid,
            turn_aware_validation_passed=turn_valid,
            vehicle_class_validation=vehicle_class_validation,
            access_target_count=len(access_targets),
            all_access_targets_reachable=(
                len(unreachable_targets) == 0 if access_targets else None
            ),
            unreachable_access_targets=unreachable_targets,
            interior_roads_count=len(cell.interior_edges),
            modal_filter_count=len(split_filters),
            one_way_conversion_count=0,
            street_cut_count=0,
            two_way_conversion_count=0,
        )

    def _validate_turn_aware_constraints(
        self,
        interior_graph: nx.MultiDiGraph,
        modifications: list[StreetModification],
        entry_points_by_sector: dict[int, list[int]],
    ) -> tuple[bool, dict[str, bool]]:
        """Validate legal movements independently for important vehicle classes."""
        if not entry_points_by_sector:
            return True, {VehicleClass.CAR.value: True, VehicleClass.EMERGENCY.value: True}
        builder = TurnGraphBuilder(interior_graph, self.turn_restrictions)
        results: dict[str, bool] = {}
        for vehicle_class in (VehicleClass.CAR, VehicleClass.EMERGENCY):
            turn_graph = builder.build(vehicle_class, modifications)
            results[vehicle_class.value] = not turn_graph.sector_violations(entry_points_by_sector)
        return results[VehicleClass.CAR.value], results

    def _boundary_road_id_for_node(self, cell: SuperblockCell, node_id: int) -> int:
        """Return a connected boundary way ID for an entry point when available."""
        node_data = self.graph.nodes.get(node_id, {})
        point = Point(node_data.get("x", 0), node_data.get("y", 0))
        for u, v, key in cell.boundary_edges:
            if not self.graph.has_edge(u, v, key):
                continue
            edge_data = self.graph[u][v][key]
            geometry = edge_data.get("geometry")
            touches_entry = node_id in (u, v) or (
                geometry is not None and geometry.distance(point) <= 0.0001
            )
            if not touches_entry:
                continue
            osm_ids = self._normalize_osm_ids(edge_data.get("osmid"))
            if osm_ids:
                return osm_ids[0]

        # Use the global arterial grid as a topology fallback when geometric
        # classification put a touching edge just outside this cell.
        if self._arterial_edges_by_node is None:
            index: dict[int, list[tuple[int, int, int]]] = {}
            for edge in sorted(self.arterial_edges, key=repr):
                index.setdefault(edge[0], []).append(edge)
                index.setdefault(edge[1], []).append(edge)
            self._arterial_edges_by_node = index
        for u, v, key in self._arterial_edges_by_node.get(node_id, ()):
            if not self.graph.has_edge(u, v, key):
                continue
            osm_ids = self._normalize_osm_ids(self.graph[u][v][key].get("osmid"))
            if osm_ids:
                return osm_ids[0]
        return 0

    def _build_interior_subgraph(self, cell: SuperblockCell) -> nx.MultiDiGraph:
        """Build a subgraph containing only interior edges."""
        subgraph = nx.MultiDiGraph()

        for node_id in cell.entry_nodes:
            if node_id in self.graph:
                subgraph.add_node(node_id, **self.graph.nodes[node_id])

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
            if (min(u, v), max(u, v)) in self._keep_open:
                subgraph[u][v][key][KEEP_OPEN_ATTRIBUTE] = True

        return subgraph

    def _access_targets_for_cell(self, polygon: Polygon) -> list[AccessTarget]:
        """Return explicit address/parcel targets covered by a generated cell."""
        if self._access_target_tree is None:
            return []
        indices = self._access_target_tree.query(polygon, predicate="covers")
        return [self.access_targets[int(index)] for index in indices]

    def _find_unreachable_access_targets(
        self,
        graph: nx.MultiDiGraph,
        modifications: list[StreetModification],
        entry_nodes: list[int],
        sectors,
        access_targets: list[AccessTarget],
    ) -> list[UnreachableAccessTarget]:
        """Validate class-specific legal inbound and return paths to real entrances."""
        if not access_targets:
            return []

        unreachable: list[UnreachableAccessTarget] = []
        graph_nodes = list(graph.nodes(data=True))
        node_points = [
            Point(float(data.get("x", 0)), float(data.get("y", 0))) for _node, data in graph_nodes
        ]
        node_tree = STRtree(node_points) if node_points else None
        requested_classes = {
            VehicleClass.CAR if vehicle_class == VehicleClass.ALL_MOTOR else vehicle_class
            for target in access_targets
            for vehicle_class in target.permitted_vehicle_classes
        }
        turn_graphs = {
            vehicle_class: TurnGraphBuilder(graph, self.turn_restrictions).build(
                vehicle_class,
                modifications,
            )
            for vehicle_class in requested_classes
        }
        for target in access_targets:
            snapped_node = None
            if node_tree is not None:
                nearest_index = int(
                    node_tree.nearest(Point(target.coordinates.lon, target.coordinates.lat))
                )
                snapped_node = graph_nodes[nearest_index][0]

            failed_vehicle_classes: list[VehicleClass] = []
            constraint_reasons: list[str] = []
            for requested_class in target.permitted_vehicle_classes:
                vehicle_class = (
                    VehicleClass.CAR
                    if requested_class == VehicleClass.ALL_MOTOR
                    else requested_class
                )
                if snapped_node is not None and target.service_windows:
                    # Reachability depends on which timed measures are in
                    # force, so it is checked in each declared window.
                    reasons = self._access_constraint_failures(
                        graph, modifications, entry_nodes, snapped_node, target, vehicle_class
                    )
                    if reasons:
                        failed_vehicle_classes.append(requested_class)
                        constraint_reasons.extend(reasons)
                    continue
                turn_graph = turn_graphs[vehicle_class]
                inbound = snapped_node is not None and any(
                    entry == snapped_node or turn_graph.has_path_between_nodes(entry, snapped_node)
                    for entry in entry_nodes
                )
                outbound = snapped_node is not None and any(
                    entry == snapped_node or turn_graph.has_path_between_nodes(snapped_node, entry)
                    for entry in entry_nodes
                )
                if not inbound or not outbound:
                    failed_vehicle_classes.append(requested_class)
                    continue
                reasons = self._access_constraint_failures(
                    graph, modifications, entry_nodes, snapped_node, target, vehicle_class
                )
                if reasons:
                    failed_vehicle_classes.append(requested_class)
                    constraint_reasons.extend(reasons)
            if not failed_vehicle_classes:
                continue

            nearest_sector = None
            if sectors and entry_nodes:
                nearest_entry = min(
                    entry_nodes,
                    key=lambda node: (
                        (float(self.graph.nodes[node].get("x", 0)) - target.coordinates.lon) ** 2
                        + (float(self.graph.nodes[node].get("y", 0)) - target.coordinates.lat) ** 2
                    ),
                )
                nearest_sector = sectors.node_to_sector.get(nearest_entry)

            unreachable.append(
                UnreachableAccessTarget(
                    target_id=target.id,
                    target_kind=target.kind,
                    label=target.label,
                    coordinates=target.coordinates,
                    snapped_node_id=snapped_node,
                    nearest_entry_sector=nearest_sector,
                    failed_vehicle_classes=failed_vehicle_classes,
                    reason=(
                        "No entry point connects this cell to the boundary network"
                        if not entry_nodes
                        else "; ".join(dict.fromkeys(constraint_reasons))
                        if constraint_reasons
                        else "No legal inbound-and-return path for: "
                        + ", ".join(vehicle_class.value for vehicle_class in failed_vehicle_classes)
                    ),
                )
            )

        return unreachable

    def _access_constraint_failures(
        self,
        graph: nx.MultiDiGraph,
        modifications: list[StreetModification],
        entry_nodes: list[int],
        target_node: int,
        target: AccessTarget,
        vehicle_class: VehicleClass,
    ) -> list[str]:
        """Check a target's declared service windows, clearance and detour limit.

        Path existence alone is not access: a delivery bay needs a route during
        its service window, under its vehicle's height, within its detour
        allowance. Targets that declare none of these are skipped.
        """
        if (
            not target.service_windows
            and target.minimum_clearance_m is None
            and target.maximum_detour_minutes is None
        ):
            return []

        def best_round_trip(turn_graph) -> tuple[float, list] | None:
            inbound = [
                turn_graph.shortest_path_between_nodes(entry, target_node) for entry in entry_nodes
            ]
            outbound = [
                turn_graph.shortest_path_between_nodes(target_node, entry) for entry in entry_nodes
            ]
            inbound = [item for item in inbound if item is not None]
            outbound = [item for item in outbound if item is not None]
            if not inbound or not outbound:
                return None
            best_in = min(inbound, key=lambda item: item[0])
            best_out = min(outbound, key=lambda item: item[0])
            return best_in[0] + best_out[0], best_in[1] + best_out[1]

        builder = TurnGraphBuilder(graph, self.turn_restrictions)
        reasons: list[str] = []
        for window in target.service_windows or [None]:
            label = f" during {window}" if window else ""
            planned_graph = builder.build(vehicle_class, modifications, window)
            planned = best_round_trip(planned_graph)
            if planned is None:
                reasons.append(f"No {vehicle_class.value} inbound-and-return path{label}")
                continue
            if target.minimum_clearance_m is not None:
                # Route only over streets surveyed tall enough, so a slower
                # route under the height limit still counts.
                clear_states = {
                    state
                    for state in planned_graph.graph.nodes
                    if (clearance := _clearance(planned_graph.edge_data.get(state))) is not None
                    and clearance >= target.minimum_clearance_m
                }
                cleared = best_round_trip(planned_graph.restricted_to(clear_states))
                if cleared is None:
                    reasons.append(
                        f"No {vehicle_class.value} route with surveyed clearance of at least "
                        f"{target.minimum_clearance_m:.2f} m{label}"
                    )
                    continue
                planned = cleared
            minutes, _states = planned
            if target.maximum_detour_minutes is not None:
                baseline = best_round_trip(builder.build(vehicle_class, [], window))
                if baseline is not None and (
                    minutes - baseline[0] > target.maximum_detour_minutes + 1e-9
                ):
                    reasons.append(
                        f"Detour of {minutes - baseline[0]:.1f} min exceeds "
                        f"{target.maximum_detour_minutes:.1f} min{label}"
                    )
        return reasons

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

    def _collect_boundary_osm_ids(self, boundary_edges: list[tuple[int, int, int]]) -> list[int]:
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

    def _calculate_eligible_road_coverage(
        self,
        covered_geometry,
        excluded_geometry,
    ) -> tuple[float, float, float, list[int]]:
        """Account for every physical road as a cell road or boundary corridor."""
        arterial_physical = {(min(u, v), max(u, v), key) for u, v, key in self.arterial_edges}
        physical_edges: dict[tuple[int, int, int], tuple[float, bool, list[int]]] = {}
        for u, v, key, data in self.graph.edges(keys=True, data=True):
            physical_key = (min(int(u), int(v)), max(int(u), int(v)), int(key))
            length = max(0.0, float(data.get("length", 0) or 0))
            geometry = data.get("geometry")
            if geometry is None:
                u_data = self.graph.nodes.get(u, {})
                v_data = self.graph.nodes.get(v, {})
                if not {"x", "y"}.issubset(u_data) or not {"x", "y"}.issubset(v_data):
                    continue
                geometry = LineString([(u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])])
            midpoint = geometry.interpolate(0.5, normalized=True)
            highway = data.get("highway")
            highway = highway[0] if isinstance(highway, list) else highway
            accounted = (
                physical_key in arterial_physical
                # A motorway or trunk road is a through corridor wherever it
                # runs, even where it crosses a cell on a bridge or ends at the
                # city edge without bounding one.
                or highway in THROUGH_CORRIDOR_HIGHWAYS
                or covered_geometry.covers(midpoint)
                or excluded_geometry.covers(midpoint)
            )
            osm_ids = self._normalize_osm_ids(data.get("osmid"))
            previous = physical_edges.get(physical_key)
            if previous is None or length > previous[0]:
                physical_edges[physical_key] = (length, accounted, osm_ids)

        eligible_length_m = sum(item[0] for item in physical_edges.values())
        accounted_length_m = sum(
            length for length, accounted, _osmids in physical_edges.values() if accounted
        )
        coverage_percent = (
            accounted_length_m / eligible_length_m * 100 if eligible_length_m else 0.0
        )
        unaccounted_ids = sorted(
            {
                osmid
                for _length, accounted, osmids in physical_edges.values()
                if not accounted
                for osmid in osmids
            }
        )
        return (
            eligible_length_m / 1000,
            accounted_length_m / 1000,
            min(100.0, coverage_percent),
            unaccounted_ids,
        )

    def _build_result(self) -> CityPartition:
        """Build the final CityPartition result."""
        # Calculate covered area from the geometric union rather than summing
        # successfully represented superblocks. This keeps failed/empty faces
        # from being presented as completed plan area.
        covered_geometry = (
            unary_union([shape(item.geometry) for item in self.superblocks]).intersection(
                self.analysis_polygon
            )
            if self.superblocks
            else Polygon()
        )
        total_area = self._calculate_area_hectares(covered_geometry)
        analysis_area = self._calculate_area_hectares(self.analysis_polygon)

        coverage = min(100.0, (total_area / analysis_area * 100)) if analysis_area > 0 else 0
        reviewed_exclusion_geometries = [
            shape(exclusion.geometry.model_dump()).intersection(self.analysis_polygon)
            for exclusion in self.area_exclusions
            if exclusion.reviewed
        ]
        excluded_geometry = (
            unary_union(reviewed_exclusion_geometries)
            if reviewed_exclusion_geometries
            else Polygon()
        )
        excluded_area = self._calculate_area_hectares(excluded_geometry)
        excluded_area_percent = (
            min(100.0, excluded_area / analysis_area * 100) if analysis_area > 0 else 0.0
        )
        (
            eligible_road_length_km,
            accounted_road_length_km,
            eligible_road_coverage_percent,
            unaccounted_road_osm_ids,
        ) = self._calculate_eligible_road_coverage(covered_geometry, excluded_geometry)

        total_filters = sum(sb.modal_filter_count for sb in self.superblocks)
        total_oneway = sum(sb.one_way_conversion_count for sb in self.superblocks)
        total_twoway = sum(sb.two_way_conversion_count for sb in self.superblocks)
        total_cuts = sum(sb.street_cut_count for sb in self.superblocks)
        total_unreachable_targets = sum(
            len(sb.unreachable_access_targets) for sb in self.superblocks
        )
        all_modifications = [
            modification
            for superblock in self.superblocks
            for modification in superblock.modifications
        ]
        operational_validation = OperationalValidator(
            self.graph,
            self.turn_restrictions,
        ).validate(self.operational_requirements, all_modifications)
        active_mode_validation = ActiveModeValidator(self.active_mode_graphs).validate(
            self.active_mode_requirements,
            all_modifications,
        )

        # Arterial road OSM IDs
        arterial_osm_ids = []
        for u, v, key in self.arterial_edges:
            if self.graph.has_edge(u, v, key):
                osmid = self.graph[u][v][key].get("osmid", 0)
                arterial_osm_ids.extend(self._normalize_osm_ids(osmid))

        assignment_comparison = AssignmentComparison(
            limitations=[
                "No time-sliced OD demand model was supplied; network-wide rerouting was not run."
            ]
        )
        if self.demand_model is not None:
            assignment_comparison = NetworkAssignmentEngine(
                self.graph,
                self.turn_restrictions,
            ).compare(
                self.demand_model,
                all_modifications,
                set(arterial_osm_ids),
                self.assignment_settings,
            )

        traffic_impact = None
        if self._impact_inputs:
            self._report_progress("impact", 97, "Estimating boundary-road traffic impact...")
            try:
                traffic_impact = BoundaryImpactEstimator(
                    self.graph, self.traffic_impact_settings
                ).estimate(self._impact_inputs)
            except Exception as error:  # Screening output must not block the plan.
                logger.warning("Traffic impact estimate failed: %s", error)

        public_space = (
            PublicSpaceEstimator(self.graph, self.public_space_settings).estimate(
                self._impact_inputs
            )
            if self._impact_inputs
            else None
        )
        emergency_access = EmergencyAccessSummary(
            limitations=["No OpenStreetMap context layers were loaded."]
        )
        school_streets = None
        try:
            school_streets = SchoolStreetPlanner(
                self.graph,
                self.school_street_settings,
                self.osm_context.transit_way_ids() if self.osm_context else set(),
            ).plan(
                self.osm_context.facilities if self.osm_context else [],
                self._impact_inputs,
                self.arterial_edges,
            )
        except Exception as error:  # Screening output must not block the plan.
            logger.warning("School street planning failed: %s", error)
        if self.osm_context is not None:
            self._report_progress("impact", 98, "Checking emergency response times...")
            try:
                emergency_access = EmergencyAccessChecker(
                    self.graph, self.osm_context.facilities
                ).check(self._impact_inputs, all_modifications)
            except Exception as error:  # Screening output must not block the plan.
                logger.warning("Emergency access check failed: %s", error)

        transit_access = None
        if self.osm_context is not None and self._impact_inputs:
            try:
                transit_access = TransitAccessChecker(
                    self.graph,
                    self.osm_context.transit_stops,
                    self.osm_context.stop_source,
                ).check(self._impact_inputs)
            except Exception as error:  # Screening output must not block the plan.
                logger.warning("Transit access check failed: %s", error)

        polygons = {}
        centroids = {}
        areas = {}
        for superblock in self.superblocks:
            try:
                polygon = shape(superblock.geometry)
            except Exception as error:  # Screening output must not block the plan.
                logger.warning("Superblock %s has no usable geometry: %s", superblock.id, error)
                continue
            polygons[superblock.id] = polygon
            centre = polygon.centroid
            centroids[superblock.id] = (centre.y, centre.x)
            areas[superblock.id] = superblock.area_hectares

        population = None
        residents: dict[str, float] = {}
        if self.population_grid is not None and polygons:
            try:
                population = summarize_population(
                    self.population_grid,
                    polygons,
                    areas,
                    self.bbox,
                    shape(self.boundary.model_dump()) if self.boundary else None,
                )
                # A grid with no cells here says nothing about density, so the
                # pilot ranking keeps its street-density fallback.
                if population.residents_in_superblocks > 0:
                    residents = {
                        item.superblock_id: item.residents for item in population.superblocks
                    }
                else:
                    population.limitations.append(
                        "The population grid has no residents in these superblocks; "
                        "check that it covers this area."
                    )
            except Exception as error:  # Screening output must not block the plan.
                logger.warning("Population count failed: %s", error)
        if transit_access is not None and transit_access.checked and residents:
            transit_access.residents_poorly_served = round(
                sum(
                    residents.get(item.superblock_id, 0.0)
                    for item in transit_access.superblocks
                    if item.covered_share < POORLY_SERVED_SHARE
                )
            )

        health = None
        if traffic_impact is not None:
            try:
                health = HealthEstimator(self.graph).estimate(
                    traffic_impact, self._impact_inputs, residents
                )
            except Exception as error:  # Screening output must not block the plan.
                logger.warning("Health estimate failed: %s", error)

        pilot_plan = None
        if self._impact_inputs:
            try:
                pilot_plan = PilotPlanner().plan(
                    self._impact_inputs,
                    traffic_impact,
                    public_space,
                    school_streets,
                    emergency_access,
                    centroids,
                    areas,
                    residents,
                    transit_access,
                )
            except Exception as error:  # Screening output must not block the plan.
                logger.warning("Pilot planning failed: %s", error)

        modeled_directional_validation_passed = bool(self.superblocks) and all(
            sb.modeled_directional_validation_passed for sb in self.superblocks
        )
        validated_target_count = sum(sb.access_target_count for sb in self.superblocks)
        access_mode = (
            "authoritative_targets"
            if self.access_dataset_complete
            else "explicit_targets"
            if self.access_targets
            else "not_supplied"
        )
        evidence = AnalysisEvidence(
            boundary_mode=("administrative_polygon" if self.boundary else "bounding_box_fallback"),
            traffic_mode=str(self.traffic_evidence.get("traffic_mode", "modeled_topology")),
            traffic_observation_count=int(
                self.traffic_evidence.get("traffic_observation_count", 0)
            ),
            measured_edge_coverage_percent=float(
                self.traffic_evidence.get("measured_edge_coverage_percent", 0.0)
            ),
            traffic_sources=list(self.traffic_evidence.get("traffic_sources", [])),
            access_mode=access_mode,
            access_target_count=len(self.access_targets),
            access_dataset_source=self.access_dataset_source,
            access_dataset_complete=self.access_dataset_complete,
            study_area_mode=(
                self.evidence_context.study_area_mode
                if self.evidence_context
                else StudyAreaMode.ADMINISTRATIVE_POLYGON
                if self.boundary
                else StudyAreaMode.BOUNDING_BOX
            ),
            network_authority_mode=(
                self.evidence_context.network_authority_mode
                if self.evidence_context
                else NetworkAuthorityMode.OSM_BOOTSTRAP
            ),
            turn_model_mode=TurnModelMode.TURN_EXPANDED,
            dataset_manifests=(
                self.evidence_context.dataset_manifests if self.evidence_context else []
            ),
            data_quality=(
                self.evidence_context.data_quality
                if self.evidence_context
                else DataQualityMetrics()
            ),
            excluded_area_percent=excluded_area_percent,
            baseline_calibration=(
                self.evidence_context.baseline_calibration if self.evidence_context else None
            ),
            impact_validation=(
                self.evidence_context.impact_validation if self.evidence_context else None
            ),
        )
        if (
            self.demand_model is not None
            and assignment_comparison.baseline is not None
            and evidence.baseline_calibration is None
        ):
            evidence.baseline_calibration = calibrate_assignment(
                assignment_comparison.baseline,
                self.traffic_observations,
                model_version=f"{self.demand_model.model_id}:{self.demand_model.version}",
            )
        if evidence.impact_validation is None:
            maximum_queue = max(
                (
                    flow.estimated_queue_length_m
                    for flow in (
                        assignment_comparison.planned.edge_flows
                        if assignment_comparison.planned
                        else []
                    )
                ),
                default=None,
            )
            evidence.impact_validation = ImpactValidationSummary(
                validated=False,
                scenario_id=(
                    assignment_comparison.planned.run_id if assignment_comparison.planned else None
                ),
                boundary_capacity_passed=assignment_comparison.boundary_capacity_passed,
                spillover_passed=assignment_comparison.spillover_passed,
                transit_passed=all(
                    result.passed
                    for result in operational_validation.results
                    if result.service_type == "transit_operation"
                )
                and "transit_operation" not in operational_validation.missing_service_types,
                emergency_passed=all(
                    result.passed
                    for result in operational_validation.results
                    if result.service_type == "emergency_response"
                )
                and "emergency_response" not in operational_validation.missing_service_types,
                freight_passed=all(
                    result.passed
                    for result in operational_validation.results
                    if result.service_type in {"freight_delivery", "refuse_collection"}
                )
                and not {
                    "freight_delivery",
                    "refuse_collection",
                }.intersection(operational_validation.missing_service_types),
                accessibility_passed=(
                    self.access_dataset_complete and total_unreachable_targets == 0
                ),
                active_modes_passed=active_mode_validation.all_hard_constraints_passed,
                maximum_volume_capacity_ratio=(
                    assignment_comparison.maximum_boundary_volume_capacity_ratio
                ),
                maximum_queue_length_m=maximum_queue,
                maximum_adjacent_road_volume_change_percent=(
                    assignment_comparison.maximum_adjacent_road_volume_change_percent
                ),
                limitations=[
                    *assignment_comparison.limitations,
                    "Safety, environment, equity, and uncertainty modules have not supplied passing evidence.",
                ],
            )
        evidence.data_quality.eligible_road_coverage_percent = eligible_road_coverage_percent

        readiness = assess_plan_readiness(
            evidence=evidence,
            has_boundary=self.boundary is not None,
            modeled_directional_validation_passed=modeled_directional_validation_passed,
            validated_target_count=validated_target_count,
            total_unreachable_targets=total_unreachable_targets,
            coverage_percent=eligible_road_coverage_percent,
        )

        partition = CityPartition(
            superblocks=self.superblocks,
            arterial_network=list(set(arterial_osm_ids)),
            bbox=self.bbox,
            boundary=self.boundary,
            turn_restrictions=self.turn_restrictions,
            area_exclusions=self.area_exclusions,
            operational_validation=operational_validation,
            active_mode_validation=active_mode_validation,
            assignment_comparison=assignment_comparison,
            traffic_impact=traffic_impact,
            filter_design=self.filter_designer.finish(),
            emergency_access=emergency_access,
            public_space=public_space,
            school_streets=school_streets,
            pilot_plan=pilot_plan,
            health=health,
            transit_access=transit_access,
            population=population,
            osm_context=self.osm_context,
            evidence=evidence,
            readiness=readiness,
            total_area_hectares=total_area,
            coverage_percent=coverage,
            total_superblocks=len(self.superblocks),
            cell_kind_counts={
                kind.value: sum(1 for sb in self.superblocks if sb.cell_kind == kind)
                + (self.non_street_cells if kind == CellKind.NON_STREET_LAND else 0)
                for kind in CellKind
            },
            oversized_superblocks=sum(
                1
                for sb in self.superblocks
                if sb.cell_kind == CellKind.SUPERBLOCK and sb.size_status == SizeStatus.OVER_MAXIMUM
            ),
            stranded_junctions=self.stranded_junctions,
            access_changes_reopened=self.reopened_for_access,
            crossings_sealed=self.crossings_sealed,
            destination_gates=self.destination_gates,
            undersized_superblocks=sum(
                1
                for sb in self.superblocks
                if sb.cell_kind == CellKind.SUPERBLOCK
                and sb.size_status == SizeStatus.UNDER_MINIMUM
            ),
            total_modal_filters=total_filters,
            total_one_way_conversions=total_oneway,
            total_street_cuts=total_cuts,
            total_unreachable_addresses=0,
            total_unreachable_access_targets=total_unreachable_targets,
            total_two_way_conversions=total_twoway,
            eligible_road_length_km=eligible_road_length_km,
            accounted_road_length_km=accounted_road_length_km,
            eligible_road_coverage_percent=eligible_road_coverage_percent,
            unaccounted_road_osm_ids=unaccounted_road_osm_ids,
        )
        partition.plan_id = compute_plan_id(partition)
        return partition
