import axios from 'axios';
import type {
  BoundingBox,
  AccessTarget,
  CityPartition,
  Coordinates,
  DemandModel,
  EngineeringPackage,
  PartitionProgress,
  RouteResult,
  SearchResponse,
  ScenarioPortfolio,
  SimulationRun,
  SizeRecommendation,
  StreetNetworkResponse,
  TrafficObservation,
} from '../types';
import type { MultiPolygon, Polygon } from 'geojson';

export const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
});

export async function searchPlaces(
  query: string,
  limit = 5,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const normalizedQuery = query.trim();
  if (normalizedQuery.length < 2) return { results: [] };
  const response = await api.get<SearchResponse>('/search', {
    params: { q: normalizedQuery, limit },
    signal,
  });
  return response.data;
}

export async function getStreetNetwork(
  bbox: BoundingBox,
  networkType = 'drive',
  signal?: AbortSignal,
): Promise<StreetNetworkResponse> {
  const response = await api.post<StreetNetworkResponse>(
    '/network',
    { bbox, network_type: networkType },
    { signal },
  );
  return response.data;
}

export interface ScoreBreakdown {
  size_score: number;
  shape_score: number;
  traffic_score: number;
  accessibility_score: number;
  connectivity_score: number;
  boundary_quality_score: number;
  total_score: number;
}

export interface StreetIntervention {
  osm_id: number;
  name: string | null;
  intervention_type: 'pedestrianize' | 'one_way' | 'modal_filter' | 'local_access' | 'no_change';
  direction: string | null;
  access_allowed: string[];
  rationale: string;
}

export interface AccessibilityMetrics {
  max_walking_distance_to_boundary: number;
  emergency_access_maintained: boolean;
  delivery_access_points: number;
  residential_access_maintained: boolean;
  public_transport_affected: boolean;
}

export interface TrafficImpact {
  removed_through_traffic_pct: number;
  boundary_load_increase_pct: number;
  estimated_vehicle_km_reduction: number;
  affected_od_pairs: number;
}

export interface SuperblockCandidate {
  id: string;
  geometry: GeoJSON.Polygon;
  area_hectares: number;
  perimeter_roads: number[];
  interior_roads: number[];
  score: number;
  algorithm: string;
  score_breakdown?: ScoreBreakdown;
  interventions?: StreetIntervention[];
  accessibility?: AccessibilityMetrics;
  traffic_impact?: TrafficImpact;
  boundary_centrality_mean?: number;
  interior_centrality_mean?: number;
  num_access_points?: number;
}

export interface NetworkStats {
  total_nodes: number;
  total_edges: number;
  total_length_km: number;
  mean_centrality: number;
  max_centrality: number;
}

export interface AnalyzeResponse {
  candidates: SuperblockCandidate[];
  total_found: number;
  bbox: BoundingBox;
  network_stats?: NetworkStats;
  parameters: {
    min_area_hectares: number;
    max_area_hectares: number;
    algorithms: string[];
    boundary_road_types?: string[];
  };
}

export interface AnalysisProgress {
  stage: 'idle' | 'network' | 'centrality' | 'detection' | 'scoring' | 'reorientation' | 'complete';
  percent: number;
  message: string;
}

export async function analyzeArea(
  bbox: BoundingBox,
  options?: { minAreaHectares?: number; maxAreaHectares?: number },
): Promise<AnalyzeResponse> {
  const response = await api.post<AnalyzeResponse>('/analyze', {
    bbox,
    algorithms: ['centrality_based'],
    min_area_hectares: options?.minAreaHectares ?? 4,
    max_area_hectares: options?.maxAreaHectares ?? 25,
    boundary_road_types: ['primary', 'secondary', 'tertiary'],
  });
  return response.data;
}

type SsePayload = Record<string, unknown> & { type?: string; message?: string };

async function streamPost(
  path: string,
  body: unknown,
  onEvent: (event: SsePayload) => boolean,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const errorBody = await response.json() as { detail?: string };
      if (errorBody.detail) message = errorBody.detail;
    } catch {
      // Keep the safe status-based fallback.
    }
    throw new Error(message);
  }
  if (!response.body) throw new Error('The server returned no response stream');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      for (const frame of frames) {
        const data = frame
          .split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n');
        if (!data) continue;
        let event: SsePayload;
        try {
          event = JSON.parse(data) as SsePayload;
        } catch {
          throw new Error('The server returned an invalid response. Please try again.');
        }
        if (event.type === 'error') throw new Error(event.message || 'The operation failed');
        if (onEvent(event)) return;
      }
      if (done) throw new Error('The response stream ended before completion');
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

export async function analyzeAreaWithProgress(
  bbox: BoundingBox,
  options: {
    minAreaHectares?: number;
    maxAreaHectares?: number;
    onProgress?: (progress: AnalysisProgress) => void;
    signal?: AbortSignal;
  } = {},
): Promise<AnalyzeResponse> {
  const { minAreaHectares = 4, maxAreaHectares = 25, onProgress, signal } = options;
  let result: AnalyzeResponse | undefined;
  await streamPost(
    '/analyze/stream',
    {
      bbox,
      algorithms: ['centrality_based'],
      min_area_hectares: minAreaHectares,
      max_area_hectares: maxAreaHectares,
      boundary_road_types: ['primary', 'secondary', 'tertiary'],
    },
    (event) => {
      if (event.type === 'progress') {
        onProgress?.(event as unknown as AnalysisProgress);
      } else if (event.type === 'complete') {
        result = {
          candidates: event.candidates as SuperblockCandidate[],
          total_found: event.total_found as number,
          bbox,
          network_stats: event.network_stats as unknown as NetworkStats,
          parameters: {
            min_area_hectares: minAreaHectares,
            max_area_hectares: maxAreaHectares,
            algorithms: ['centrality_based'],
            boundary_road_types: ['primary', 'secondary', 'tertiary'],
          },
        };
        return true;
      }
      return false;
    },
    signal,
  );
  if (!result) throw new Error('Analysis completed without a result');
  return result;
}

export interface PartitionRequest {
  bbox: BoundingBox;
  boundary?: Polygon | MultiPolygon | null;
  traffic_observations?: TrafficObservation[];
  access_targets?: AccessTarget[];
  access_dataset_source?: string | null;
  access_dataset_complete?: boolean;
  target_size_hectares?: number;
  min_area_hectares?: number;
  max_area_hectares?: number;
  num_sectors?: number;
  arterial_road_types?: string[];
  study_area_mode?: 'bounding_box' | 'administrative_polygon' | 'functional_urban_area';
  network_authority_mode?: 'osm_bootstrap' | 'authoritative_conflated';
  dataset_manifests?: unknown[];
  data_quality?: Record<string, unknown>;
  authoritative_road_segments?: unknown[];
  turn_restrictions?: unknown[];
  area_exclusions?: unknown[];
  operational_requirements?: unknown[];
  active_mode_requirements?: unknown[];
  demand_model?: DemandModel | null;
  assignment_settings?: Record<string, unknown>;
  minor_road_boundaries?: 'allow' | 'avoid';
  planning_mode?: 'superblocks' | 'circulation_sectors';
}

export interface PartitionResponse {
  partition: CityPartition;
  street_network: StreetNetworkResponse;
  processing_time_seconds: number;
}

function partitionBody(request: PartitionRequest): Record<string, unknown> {
  return {
    bbox: request.bbox,
    boundary: request.boundary ?? null,
    traffic_observations: request.traffic_observations ?? [],
    access_targets: request.access_targets ?? [],
    access_dataset_source: request.access_dataset_source ?? null,
    access_dataset_complete: request.access_dataset_complete ?? false,
    target_size_hectares: request.target_size_hectares ?? 12,
    min_area_hectares: request.min_area_hectares ?? 6,
    max_area_hectares: request.max_area_hectares ?? 20,
    enforce_constraints: true,
    num_sectors: request.num_sectors ?? 4,
    arterial_road_types: request.arterial_road_types ?? ['primary', 'secondary', 'tertiary'],
    study_area_mode: request.study_area_mode ?? (request.boundary ? 'administrative_polygon' : 'bounding_box'),
    network_authority_mode: request.network_authority_mode ?? 'osm_bootstrap',
    dataset_manifests: request.dataset_manifests ?? [],
    data_quality: request.data_quality ?? {},
    authoritative_road_segments: request.authoritative_road_segments ?? [],
    turn_restrictions: request.turn_restrictions ?? [],
    area_exclusions: request.area_exclusions ?? [],
    operational_requirements: request.operational_requirements ?? [],
    active_mode_requirements: request.active_mode_requirements ?? [],
    demand_model: request.demand_model ?? null,
    assignment_settings: request.assignment_settings ?? {},
    minor_road_boundaries: request.minor_road_boundaries ?? 'avoid',
    planning_mode: request.planning_mode ?? 'superblocks',
  };
}

export async function partitionCity(request: PartitionRequest): Promise<PartitionResponse> {
  const response = await api.post<PartitionResponse>('/partition', partitionBody(request));
  return response.data;
}

export async function partitionCityWithProgress(
  request: PartitionRequest,
  onProgress?: (progress: PartitionProgress) => void,
  signal?: AbortSignal,
): Promise<PartitionResponse> {
  let result: PartitionResponse | undefined;
  await streamPost(
    '/partition/stream',
    partitionBody(request),
    (event) => {
      if (event.type === 'progress') {
        onProgress?.(event as unknown as PartitionProgress);
      } else if (event.type === 'complete') {
        result = {
          partition: event.partition as unknown as CityPartition,
          street_network: event.street_network as unknown as StreetNetworkResponse,
          processing_time_seconds: event.processing_time_seconds as number,
        };
        return true;
      }
      return false;
    },
    signal,
  );
  if (!result) throw new Error('Partitioning completed without a result');
  return result;
}

export interface RouteRequest {
  origin: Coordinates;
  destination: Coordinates;
  respect_superblocks?: boolean;
  prefer_arterials?: boolean;
  partition?: CityPartition;
}

export async function computeRoute(request: RouteRequest): Promise<RouteResult> {
  const response = await api.post<RouteResult>('/route', {
    origin: request.origin,
    destination: request.destination,
    respect_superblocks: request.respect_superblocks ?? true,
    prefer_arterials: request.prefer_arterials ?? true,
    partition: request.partition,
  });
  return response.data;
}

export async function getOptimalSize(
  bbox: BoundingBox,
  populationDensity?: number,
): Promise<SizeRecommendation> {
  const response = await api.get<SizeRecommendation>('/optimize/size', {
    params: {
      north: bbox.north,
      south: bbox.south,
      east: bbox.east,
      west: bbox.west,
      population_density: populationDensity,
    },
  });
  return response.data;
}

function adminHeaders(adminKey: string): Record<string, string> {
  return { 'X-Admin-Key': adminKey };
}

export async function optimizePlanScenarios(
  partition: CityPartition,
  demandModel: DemandModel,
  adminKey: string,
  operationalRequirements: unknown[] = [],
  activeModeRequirements: unknown[] = [],
): Promise<ScenarioPortfolio> {
  const response = await api.post<ScenarioPortfolio>(
    '/planning/scenarios',
    {
      partition,
      demand_model: demandModel,
      operational_requirements: operationalRequirements,
      active_mode_requirements: activeModeRequirements,
    },
    { headers: adminHeaders(adminKey) },
  );
  return response.data;
}

export async function compileEngineeringPackage(
  partition: CityPartition,
  jurisdiction: string,
  adminKey: string,
): Promise<EngineeringPackage> {
  const response = await api.post<EngineeringPackage>(
    '/planning/engineering-package',
    { partition, jurisdiction, currency: 'EUR' },
    { headers: adminHeaders(adminKey) },
  );
  return response.data;
}

export async function exportSimulation(
  partition: CityPartition,
  demandModel: DemandModel,
  adminKey: string,
  execute = false,
): Promise<SimulationRun> {
  const response = await api.post<SimulationRun>(
    '/planning/simulation',
    {
      partition,
      demand_model: demandModel,
      network_state: 'planned',
      execute,
      random_seed: 42,
    },
    { headers: adminHeaders(adminKey) },
  );
  return response.data;
}

export default api;
