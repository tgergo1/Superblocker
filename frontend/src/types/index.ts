import type { Feature, LineString, MultiPolygon, Polygon } from 'geojson';

export interface BoundingBox {
  north: number;
  south: number;
  east: number;
  west: number;
}

export interface Coordinates {
  lat: number;
  lon: number;
}

export interface SearchResult {
  place_id: number;
  osm_type: string;
  osm_id: number;
  display_name: string;
  lat: number;
  lon: number;
  boundingbox: BoundingBox;
  boundary: Polygon | MultiPolygon | null;
  boundary_source: 'nominatim' | 'bounding_box_fallback';
  type: string;
  importance: number;
}

export interface SearchResponse {
  results: SearchResult[];
}

export interface RoadProperties {
  osmid: number | number[];
  name: string | null;
  highway: string;
  hierarchy: number;
  lanes: number;
  oneway: boolean;
  maxspeed: number | null;
  length_m: number;
  u: number;
  v: number;
  key: number;
  capacity: number;
  estimated_load: number;
  estimated_volume: number;
  traffic_intensity: number;
  is_real_data?: boolean;
}

export interface StreetNetworkMetadata {
  bbox: BoundingBox;
  total_edges: number;
  total_length_km: number;
  road_type_counts: Record<string, number>;
  network_type: string;
  total_capacity?: number;
  total_estimated_volume?: number;
  average_load?: number;
}

export interface StreetNetworkResponse {
  type: 'FeatureCollection';
  features: Feature<LineString, RoadProperties>[];
  metadata: StreetNetworkMetadata;
}

export interface SuperblockCandidate {
  id: string;
  geometry: Polygon;
  area_hectares: number;
  perimeter_roads: number[];
  interior_roads: number[];
  score: number;
  algorithm: string;
}

export interface AnalysisResponse {
  candidates: SuperblockCandidate[];
  street_network: StreetNetworkResponse;
  metadata: Record<string, unknown>;
}

export interface ViewState {
  longitude: number;
  latitude: number;
  zoom: number;
  pitch?: number;
  bearing?: number;
}

export type RoadType =
  | 'motorway'
  | 'trunk'
  | 'primary'
  | 'secondary'
  | 'tertiary'
  | 'residential'
  | 'living_street'
  | 'unclassified'
  | 'service'
  | 'pedestrian';

// =============================================================================
// City Partitioning Types (New System)
// =============================================================================

export type ModificationType = 'modal_filter' | 'one_way' | 'two_way' | 'turn_restriction' | 'full_closure';
export type VehicleClass = 'all_motor' | 'car' | 'motorcycle' | 'light_goods' | 'heavy_goods' | 'bus' | 'taxi' | 'refuse' | 'emergency' | 'permit';

export interface DemandZone {
  zone_id: string;
  centroid: Coordinates;
  connector_node_ids: number[];
  name?: string | null;
  external?: boolean;
}

export interface DemandTimeSlice {
  time_slice_id: string;
  start_time: string;
  end_time: string;
  duration_minutes: number;
}

export interface DemandMatrixEntry {
  origin_zone_id: string;
  destination_zone_id: string;
  time_slice_id: string;
  vehicle_class: VehicleClass;
  trips: number;
}

export interface DemandModel {
  model_id: string;
  version: string;
  source: string;
  source_reference?: string | null;
  zones: DemandZone[];
  time_slices: DemandTimeSlice[];
  matrix: DemandMatrixEntry[];
}

export interface EntryPoint {
  node_id: number;
  sector: number;
  coordinates: Coordinates;
  boundary_road_id: number;
  access_type: string;
}

export interface StreetModification {
  u: number;
  v: number;
  key: number;
  osm_id: number;
  name: string | null;
  modification_type: ModificationType;
  direction: string | null;
  filter_location: Coordinates | null;
  rationale: string;
  allowed_modes: string[];
  allowed_vehicle_classes: string[];
  time_windows: string[];
  emergency_override_required: boolean;
  construction_treatment: string | null;
  estimated_capital_cost: number | null;
  annual_maintenance_cost: number | null;
  turn_from_osm_id: number | null;
  turn_to_osm_id: number | null;
  via_node_id: number | null;
  filter_type?: 'planter' | 'emergency_bollard' | 'camera_gate' | 'bus_gate' | 'school_street' | null;
  transit_route_refs?: string[];
}

export interface EmergencyAccessResult {
  superblock_id: string;
  nearest_station: string | null;
  baseline_seconds: number;
  planned_seconds: number | null;
  planned_without_exemption_seconds: number | null;
}

export interface EmergencyAccessSummary {
  checked: boolean;
  stations_used: number;
  superblocks_checked: number;
  median_added_seconds: number;
  maximum_added_seconds: number;
  threshold_seconds: number;
  superblocks_over_threshold: number;
  superblocks_over_threshold_without_exemption: number;
  unreachable_superblocks: number;
  results: EmergencyAccessResult[];
  limitations: string[];
}

export interface SuperblockPublicSpace {
  superblock_id: string;
  calmed_street_km: number;
  calmed_carriageway_m2: number;
  reclaimable_m2: number;
  plaza_candidates: Coordinates[];
}

export interface SchoolStreet {
  facility_osm_type: 'node' | 'way' | 'relation';
  facility_osm_id: number;
  kind: 'school' | 'kindergarten';
  name?: string | null;
  coordinates: Coordinates;
  setting: 'interior' | 'boundary' | 'unassigned';
  superblock_id?: string | null;
  street_name?: string | null;
  osm_id?: number | null;
  distance_m: number;
  street_path: Coordinates[];
  on_transit_route: boolean;
  time_windows: string[];
  treatment: string;
}

export interface SchoolStreetSummary {
  facilities_loaded: number;
  interior_school_streets: number;
  boundary_frontages: number;
  unassigned: number;
  schools: SchoolStreet[];
  limitations: string[];
}

export interface PilotCandidate {
  superblock_id: string;
  rank: number;
  score: number;
  trial_measures: number;
  trial_cost_eur: number;
  build_cost_eur: number;
  displaced_peak_vph: number;
  interior_injury_reduction_low_percent: number;
  interior_injury_reduction_high_percent: number;
  school_streets: number;
  reclaimable_m2: number;
  mean_detour_seconds: number;
  emergency_delay_seconds?: number | null;
  residents?: number | null;
  transit_covered_share?: number | null;
  centroid_lat?: number | null;
  centroid_lon?: number | null;
}

export interface PilotPlanSummary {
  total_trial_cost_eur: number;
  total_build_cost_eur: number;
  top_trial_cost_eur: number;
  mean_injury_reduction_low_percent: number;
  mean_injury_reduction_high_percent: number;
  candidates: PilotCandidate[];
  limitations: string[];
}

export interface SuperblockHealth {
  superblock_id: string;
  interior_noise_change_db_low: number;
  interior_noise_change_db_high: number;
  interior_no2_change_percent_low: number;
  interior_no2_change_percent_high: number;
}

export interface HealthEstimate {
  method: string;
  mean_interior_noise_change_db_low: number;
  mean_interior_noise_change_db_high: number;
  mean_interior_no2_change_percent_low: number;
  mean_interior_no2_change_percent_high: number;
  residents_counted?: number | null;
  residents_noticeably_quieter?: number | null;
  resident_weighted_noise_change_db_low?: number | null;
  resident_weighted_noise_change_db_high?: number | null;
  resident_weighted_no2_change_percent_low?: number | null;
  resident_weighted_no2_change_percent_high?: number | null;
  boundary_roads_noticeably_louder: number;
  maximum_boundary_noise_increase_db: number;
  superblocks: SuperblockHealth[];
  limitations: string[];
}

export interface SuperblockTransitAccess {
  superblock_id: string;
  covered_share: number;
  worst_walk_m: number | null;
  stops_nearby: number;
  nearest_stop: string | null;
}

export interface TransitAccessSummary {
  checked: boolean;
  source: string | null;
  stops_used: number;
  walk_threshold_m: number;
  mean_covered_share: number;
  superblocks_poorly_served: number;
  residents_poorly_served?: number | null;
  superblocks: SuperblockTransitAccess[];
  limitations: string[];
}

export interface PopulationSummary {
  source: string;
  residents_in_area: number;
  residents_in_superblocks: number;
  share_in_superblocks: number;
  superblocks: { superblock_id: string; residents: number; residents_per_ha: number }[];
  limitations: string[];
}

export interface PublicSpaceSummary {
  settings: { reallocation_share: number; max_plazas_per_superblock: number };
  calmed_street_km: number;
  calmed_carriageway_m2: number;
  reclaimable_m2: number;
  plaza_candidates: number;
  superblocks: SuperblockPublicSpace[];
  limitations: string[];
}

export interface FilterDesignSummary {
  transit_routes_loaded: number;
  transit_ways_loaded: number;
  facilities_loaded: number;
  bus_gates: number;
  emergency_bollards: number;
  planters: number;
  closures_converted_to_gates: number;
  one_way_changes_on_transit: number;
  protected_route_refs: string[];
  limitations: string[];
}

export interface UnreachableAddress {
  node_id: number;
  coordinates: Coordinates;
  nearest_entry_sector: number;
  reason: string;
}

export interface AccessTarget {
  id: string;
  coordinates: Coordinates;
  kind: 'address' | 'parcel' | 'building' | 'emergency' | 'delivery';
  label: string | null;
  source: string;
  entrance_id?: string | null;
  permitted_vehicle_classes?: string[];
  service_windows?: string[];
  minimum_clearance_m?: number | null;
  maximum_detour_minutes?: number | null;
  critical?: boolean;
  surveyed_at?: string | null;
}

export interface UnreachableAccessTarget {
  target_id: string;
  target_kind: string;
  label: string | null;
  coordinates: Coordinates;
  snapped_node_id: number | null;
  nearest_entry_sector: number | null;
  failed_vehicle_classes: string[];
  reason: string;
}

export interface TrafficObservation {
  osm_id: number;
  volume_vph: number;
  source: string;
  observed_at: string | null;
  observation_id?: string | null;
  interval_start?: string | null;
  interval_end?: string | null;
  direction?: 'u_to_v' | 'v_to_u' | 'both';
  lane?: number | null;
  vehicle_class?: 'all_motor' | 'car' | 'motorcycle' | 'light_goods' | 'heavy_goods' | 'bus' | 'taxi' | 'refuse' | 'emergency' | 'permit';
  turn_from_osm_id?: number | null;
  turn_to_osm_id?: number | null;
  speed_kph?: number | null;
  occupancy_percent?: number | null;
  queue_length_m?: number | null;
  confidence?: number;
  dataset_id?: string | null;
}

export type PlanStage =
  | 'model_only'
  | 'topology_candidate'
  | 'data_qualified'
  | 'baseline_calibrated'
  | 'impact_validated'
  | 'engineering_reviewed'
  | 'approved_for_pilot'
  | 'approved_for_permanent_works';

export interface DataQualityMetrics {
  eligible_road_coverage_percent: number;
  authoritative_network_coverage_percent: number;
  lane_coverage_percent: number;
  turn_coverage_percent: number;
  signal_coverage_percent: number;
  directional_traffic_coverage_percent: number;
  peak_period_coverage_percent: number;
  turning_movement_coverage_percent: number;
  access_entrance_coverage_percent: number;
  transit_coverage_percent: number;
  emergency_coverage_percent: number;
}

export interface AnalysisEvidence {
  boundary_mode: 'administrative_polygon' | 'bounding_box_fallback';
  traffic_mode: 'measured_volume' | 'modeled_topology';
  traffic_observation_count: number;
  measured_edge_coverage_percent: number;
  traffic_sources: string[];
  access_mode: 'authoritative_targets' | 'explicit_targets' | 'not_supplied';
  access_target_count: number;
  access_dataset_source: string | null;
  access_dataset_complete: boolean;
  study_area_mode: 'bounding_box' | 'administrative_polygon' | 'functional_urban_area';
  network_authority_mode: 'osm_bootstrap' | 'authoritative_conflated';
  turn_model_mode: 'edge_only' | 'turn_expanded';
  dataset_manifests: unknown[];
  data_quality: DataQualityMetrics;
  excluded_area_percent: number;
  baseline_calibration: unknown | null;
  impact_validation: unknown | null;
}

export interface AssignedEdgeFlow {
  u: number;
  v: number;
  key: number;
  osm_id: number;
  time_slice_id: string;
  vehicle_class: VehicleClass;
  volume: number;
  capacity_vph: number;
  volume_capacity_ratio: number;
  free_flow_time_seconds: number;
  assigned_time_seconds: number;
  estimated_queue_length_m: number;
  is_boundary_road: boolean;
}

export interface AssignmentRun {
  run_id: string;
  network_state: 'baseline' | 'planned';
  converged: boolean;
  iterations: number;
  relative_gap: number;
  total_demand: number;
  assigned_demand: number;
  blocked_demand: number;
  edge_flows: AssignedEdgeFlow[];
}

export interface AssignmentComparison {
  validated: boolean;
  baseline: AssignmentRun | null;
  planned: AssignmentRun | null;
  maximum_boundary_volume_capacity_ratio: number | null;
  maximum_adjacent_road_volume_change_percent: number | null;
  boundary_capacity_passed: boolean;
  spillover_passed: boolean;
  all_demand_assigned: boolean;
  limitations: string[];
}

export interface EvaporationScenario {
  name: string;
  evaporation_rate: number;
  description: string;
}

export interface TrafficImpactSettings {
  through_traffic_share: number;
  peak_hour_share_of_daily: number;
  max_entry_pairs_per_superblock: number;
  scenarios: EvaporationScenario[];
  central_scenario: string;
  high_increase_percent: number;
}

export interface BoundaryRoadImpact {
  osm_id: number;
  name: string | null;
  highway: string;
  baseline_peak_vph: number;
  capacity_vph: number;
  added_peak_vph: Record<string, number>;
  percent_increase_central: number;
  baseline_volume_capacity_ratio: number;
  planned_volume_capacity_ratio_central: number;
  measured: boolean;
  arterial: boolean;
  superblock_ids: string[];
}

export interface SuperblockTrafficImpact {
  superblock_id: string;
  entry_pairs_checked: number;
  entry_pairs_total?: number;
  cut_through_pairs: number;
  unresolved_pairs: number;
  displaced_peak_vph: number;
  interior_traffic_reduction_percent: number;
  mean_detour_seconds: number;
}

export interface TrafficImpactEstimate {
  method: string;
  settings: TrafficImpactSettings;
  traffic_basis: 'modeled_topology' | 'measured_volume' | 'mixed';
  total_displaced_peak_vph: number;
  added_boundary_vehicle_km_peak: Record<string, number>;
  interior_vehicle_km_removed_peak: number;
  maximum_percent_increase_central: number;
  roads_over_threshold: number;
  roads_over_capacity_central: number;
  minor_roads_loaded: number;
  boundary_roads: BoundaryRoadImpact[];
  superblocks: SuperblockTrafficImpact[];
  limitations: string[];
}

export interface OperationalValidationSummary {
  validated: boolean;
  all_hard_constraints_passed: boolean;
  results: Array<{
    requirement_id: string;
    service_type: string;
    vehicle_class: VehicleClass;
    passed: boolean;
    baseline_travel_time_minutes: number | null;
    planned_travel_time_minutes: number | null;
    detour_percent: number | null;
    clearance_passed: boolean | null;
    blockers: string[];
  }>;
  missing_service_types: string[];
}

export interface ActiveModeValidationSummary {
  validated: boolean;
  all_hard_constraints_passed: boolean;
  results: Array<{
    requirement_id: string;
    mode: 'walk' | 'wheel' | 'bicycle';
    passed: boolean;
    baseline_distance_m: number | null;
    planned_distance_m: number | null;
    detour_percent: number | null;
    geometry_attributes_passed: boolean | null;
    blockers: string[];
  }>;
  missing_modes: string[];
}

export interface PlanReadiness {
  status: PlanStage;
  highest_completed_stage: PlanStage;
  ready_for_engineering_review: boolean;
  approved_for_pilot: boolean;
  approved_for_permanent_works: boolean;
  implementation_ready: boolean;
  modeled_directional_validation_passed: boolean;
  transport_engineering_reviewed: boolean;
  site_inspection_reviewed: boolean;
  verified_approval_types: string[];
  gates: Array<{ stage: PlanStage; passed: boolean; blockers: string[] }>;
  blockers: string[];
}

export type CellKind = 'superblock' | 'naturally_calm' | 'non_street_land';
export type SizeStatus = 'within' | 'under_minimum' | 'over_maximum' | 'mostly_open_land';

export interface EnforcedSuperblock {
  id: string;
  geometry: Polygon;
  area_hectares: number;
  num_sectors: number;
  cell_kind?: CellKind;
  size_status?: SizeStatus;
  street_area_hectares?: number | null;
  boundary_roads: number[];
  entry_points: EntryPoint[];
  modifications: StreetModification[];
  constraint_validated: boolean;
  all_addresses_reachable: boolean | null;
  unreachable_addresses: UnreachableAddress[];
  modeled_directional_validation_passed: boolean;
  turn_aware_validation_passed: boolean;
  vehicle_class_validation: Record<string, boolean>;
  access_target_count: number;
  all_access_targets_reachable: boolean | null;
  unreachable_access_targets: UnreachableAccessTarget[];
  interior_roads_count: number;
  modal_filter_count: number;
  one_way_conversion_count: number;
  street_cut_count: number;
  two_way_conversion_count: number;
}

export interface CityPartition {
  superblocks: EnforcedSuperblock[];
  plan_id: string;
  arterial_network: number[];
  bbox: BoundingBox;
  boundary: Polygon | MultiPolygon | null;
  approval_artifacts: unknown[];
  evidence_artifacts: unknown[];
  turn_restrictions: unknown[];
  area_exclusions: unknown[];
  operational_validation: OperationalValidationSummary;
  active_mode_validation: ActiveModeValidationSummary;
  assignment_comparison: AssignmentComparison;
  traffic_impact?: TrafficImpactEstimate | null;
  filter_design?: FilterDesignSummary;
  emergency_access?: EmergencyAccessSummary;
  public_space?: PublicSpaceSummary | null;
  school_streets?: SchoolStreetSummary | null;
  pilot_plan?: PilotPlanSummary | null;
  health?: HealthEstimate | null;
  transit_access?: TransitAccessSummary | null;
  population?: PopulationSummary | null;
  evidence: AnalysisEvidence;
  readiness: PlanReadiness;
  total_area_hectares: number;
  coverage_percent: number;
  total_superblocks: number;
  cell_kind_counts?: Partial<Record<CellKind, number>>;
  oversized_superblocks?: number;
  undersized_superblocks?: number;
  stranded_junctions?: number;
  access_changes_reopened?: number;
  crossings_sealed?: number;
  destination_gates?: number;
  total_modal_filters: number;
  total_one_way_conversions: number;
  total_street_cuts: number;
  total_unreachable_addresses: number;
  total_unreachable_access_targets: number;
  total_two_way_conversions: number;
  eligible_road_length_km: number;
  accounted_road_length_km: number;
  eligible_road_coverage_percent: number;
  unaccounted_road_osm_ids: number[];
}

export interface PartitionProgress {
  stage: 'network' | 'arterials' | 'cells' | 'constraints' | 'impact' | 'validation' | 'complete';
  percent: number;
  message: string;
  current_superblock?: number;
  total_superblocks?: number;
}

// =============================================================================
// Routing Types
// =============================================================================

export interface RouteSegment {
  coordinates: Coordinates[];
  road_type: string;
  is_arterial: boolean;
  superblock_id: string | null;
  length_m: number;
}

export interface RouteResult {
  success: boolean;
  segments: RouteSegment[];
  total_distance_km: number;
  estimated_time_min: number;
  arterial_percent: number;
  superblocks_traversed: string[];
  blocked_reason: string | null;
  alternative_available: boolean;
}

// =============================================================================
// Size Optimization Types
// =============================================================================

export interface SizeRecommendation {
  min_side_m: number;
  max_side_m: number;
  optimal_side_m: number;
  min_area_ha: number;
  max_area_ha: number;
  optimal_area_ha: number;
  grid_orientation_deg: number;
  rationale: string;
}

export interface ScenarioMetric {
  objective: string;
  baseline_value: number | null;
  planned_value: number;
  unit: string;
  lower_is_better: boolean;
  source: string;
  validated: boolean;
}

export interface ScenarioCandidate {
  scenario_id: string;
  name: string;
  description: string;
  modifications: StreetModification[];
  metrics: ScenarioMetric[];
  hard_constraint_failures: string[];
  feasible: boolean;
  weighted_score: number | null;
  pareto_rank: number | null;
  assignment_comparison: AssignmentComparison;
  operational_validation: OperationalValidationSummary;
  active_mode_validation: ActiveModeValidationSummary;
}

export interface ScenarioPortfolio {
  portfolio_id: string;
  candidates: ScenarioCandidate[];
  pareto_scenario_ids: string[];
  objective_weights: Record<string, number>;
  recommended_scenario_id: string | null;
  limitations: string[];
}

export interface EngineeringPackage {
  package_id: string;
  plan_id: string;
  status: 'draft_for_engineering_review';
  jurisdiction: string;
  currency: string;
  instructions: Array<{
    instruction_id: string;
    osm_id: number;
    street_name: string;
    coordinates: Coordinates | null;
    affected_movement: string;
    treatment: string;
    signs_and_markings: string[];
    allowed_modes: string[];
    allowed_vehicle_classes: VehicleClass[];
    time_windows: string[];
    emergency_operation: string | null;
    construction_notes: string[];
    inspection_checks: string[];
    capital_cost: number;
    annual_maintenance_cost: number;
    field_verification_required: boolean;
  }>;
  bill_of_quantities: Array<Record<string, unknown>>;
  total_capital_cost: number;
  total_annual_maintenance_cost: number;
  gis_feature_collection: GeoJSON.FeatureCollection;
  blockers: string[];
  disclaimer: string;
}

export interface SimulationRun {
  simulation_id: string;
  status: 'not_requested' | 'tool_unavailable' | 'exported' | 'completed' | 'failed';
  engine: 'sumo';
  engine_version: string | null;
  network_state: 'baseline' | 'planned';
  random_seed: number;
  output_directory: string | null;
  generated_files: string[];
  return_code: number | null;
  limitations: string[];
}

export interface PlanningEvidencePackage {
  study_area_mode?: 'bounding_box' | 'administrative_polygon' | 'functional_urban_area';
  network_authority_mode?: 'osm_bootstrap' | 'authoritative_conflated';
  dataset_manifests?: unknown[];
  data_quality?: Partial<DataQualityMetrics>;
  authoritative_road_segments?: unknown[];
  turn_restrictions?: unknown[];
  area_exclusions?: unknown[];
  operational_requirements?: unknown[];
  active_mode_requirements?: unknown[];
  demand_model?: DemandModel | null;
  assignment_settings?: Record<string, unknown>;
}
