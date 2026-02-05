import type { Feature, LineString, Polygon } from 'geojson';

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

export type ModificationType = 'modal_filter' | 'one_way' | 'turn_restriction' | 'full_closure';

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
}

export interface UnreachableAddress {
  node_id: number;
  coordinates: Coordinates;
  nearest_entry_sector: number;
  reason: string;
}

export interface EnforcedSuperblock {
  id: string;
  geometry: Polygon;
  area_hectares: number;
  num_sectors: number;
  boundary_roads: number[];
  entry_points: EntryPoint[];
  modifications: StreetModification[];
  constraint_validated: boolean;
  all_addresses_reachable: boolean;
  unreachable_addresses: UnreachableAddress[];
  interior_roads_count: number;
  modal_filter_count: number;
  one_way_conversion_count: number;
}

export interface CityPartition {
  superblocks: EnforcedSuperblock[];
  arterial_network: number[];
  bbox: BoundingBox;
  total_area_hectares: number;
  coverage_percent: number;
  total_superblocks: number;
  total_modal_filters: number;
  total_one_way_conversions: number;
  total_unreachable_addresses: number;
}

export interface PartitionProgress {
  stage: 'network' | 'arterials' | 'cells' | 'constraints' | 'validation' | 'complete';
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
