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
  osmid: number;
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
