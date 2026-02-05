import axios from 'axios';
import type {
  SearchResponse,
  StreetNetworkResponse,
  BoundingBox,
  Coordinates,
  CityPartition,
  PartitionProgress,
  RouteResult,
  SizeRecommendation,
} from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export async function searchPlaces(query: string, limit = 5): Promise<SearchResponse> {
  const response = await api.get<SearchResponse>('/search', {
    params: { q: query, limit },
  });
  return response.data;
}

export async function getStreetNetwork(
  bbox: BoundingBox,
  networkType = 'drive'
): Promise<StreetNetworkResponse> {
  const response = await api.post<StreetNetworkResponse>('/network', {
    bbox,
    network_type: networkType,
  });
  return response.data;
}

// Enhanced types for the new analysis system
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
  estimated_vmt_reduction: number;
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
  // Enhanced analysis data
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
  };
}

export interface AnalysisProgress {
  stage: 'idle' | 'network' | 'centrality' | 'detection' | 'scoring' | 'reorientation' | 'complete';
  percent: number;
  message: string;
}

// Regular (non-streaming) analysis
export async function analyzeArea(
  bbox: BoundingBox,
  options?: {
    minAreaHectares?: number;
    maxAreaHectares?: number;
  }
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

// Streaming analysis with progress updates
export async function analyzeAreaWithProgress(
  bbox: BoundingBox,
  options: {
    minAreaHectares?: number;
    maxAreaHectares?: number;
    onProgress?: (progress: AnalysisProgress) => void;
  } = {}
): Promise<AnalyzeResponse> {
  const { minAreaHectares = 4, maxAreaHectares = 25, onProgress } = options;

  console.log('[Superblock] Starting streaming analysis...');

  return new Promise((resolve, reject) => {
    const body = JSON.stringify({
      bbox,
      algorithms: ['centrality_based'],
      min_area_hectares: minAreaHectares,
      max_area_hectares: maxAreaHectares,
      boundary_road_types: ['primary', 'secondary', 'tertiary'],
    });

    const url = `${API_BASE_URL}/analyze/stream`;
    console.log('[Superblock] Fetching:', url);

    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      },
      body,
    })
      .then(response => {
        console.log('[Superblock] Response status:', response.status);
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('No response body');
        }

        const decoder = new TextDecoder();
        let buffer = '';

        const processStream = async (): Promise<void> => {
          try {
            const { done, value } = await reader.read();

            if (done) {
              console.log('[Superblock] Stream ended');
              // If stream ends without complete message, reject
              reject(new Error('Stream ended without completion'));
              return;
            }

            const chunk = decoder.decode(value, { stream: true });
            buffer += chunk;
            console.log('[Superblock] Received chunk:', chunk.substring(0, 100));

            // Process complete SSE messages
            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
              if (line.startsWith('data: ')) {
                try {
                  const data = JSON.parse(line.slice(6));
                  console.log('[Superblock] Parsed event:', data.type, data.percent ?? '');

                  if (data.type === 'progress') {
                    onProgress?.({
                      stage: data.stage,
                      percent: data.percent,
                      message: data.message,
                    });
                  } else if (data.type === 'complete') {
                    console.log('[Superblock] Analysis complete!', data.total_found, 'candidates');
                    resolve({
                      candidates: data.candidates,
                      total_found: data.total_found,
                      bbox,
                      network_stats: data.network_stats,
                      parameters: {
                        min_area_hectares: minAreaHectares,
                        max_area_hectares: maxAreaHectares,
                        algorithms: ['centrality_based'],
                      },
                    });
                    return;
                } else if (data.type === 'error') {
                  console.error('[Superblock] Server error:', data.message);
                  reject(new Error(data.message));
                  return;
                }
              } catch (e) {
                console.error('[Superblock] Failed to parse SSE data:', e, line);
              }
            }
          }

          return processStream();
          } catch (streamError) {
            console.error('[Superblock] Stream processing error:', streamError);
            reject(streamError);
          }
        };

        processStream().catch((err) => {
          console.error('[Superblock] Stream error:', err);
          reject(err);
        });
      })
      .catch((err) => {
        console.error('[Superblock] Fetch error:', err);
        reject(err);
      });
  });
}

// =============================================================================
// City Partitioning API
// =============================================================================

export interface PartitionRequest {
  bbox: BoundingBox;
  target_size_hectares?: number;
  min_area_hectares?: number;
  max_area_hectares?: number;
  enforce_constraints?: boolean;
  num_sectors?: number;
}

export interface PartitionResponse {
  partition: CityPartition;
  street_network: StreetNetworkResponse;
  processing_time_seconds: number;
}

// Regular (non-streaming) partitioning
export async function partitionCity(request: PartitionRequest): Promise<PartitionResponse> {
  const response = await api.post<PartitionResponse>('/partition', {
    bbox: request.bbox,
    target_size_hectares: request.target_size_hectares ?? 12,
    min_area_hectares: request.min_area_hectares ?? 6,
    max_area_hectares: request.max_area_hectares ?? 20,
    enforce_constraints: request.enforce_constraints ?? true,
    num_sectors: request.num_sectors ?? 4,
  });
  return response.data;
}

// Streaming partitioning with progress updates
export async function partitionCityWithProgress(
  request: PartitionRequest,
  onProgress?: (progress: PartitionProgress) => void
): Promise<PartitionResponse> {
  console.log('[Partition] Starting streaming partition...');

  return new Promise((resolve, reject) => {
    const body = JSON.stringify({
      bbox: request.bbox,
      target_size_hectares: request.target_size_hectares ?? 12,
      min_area_hectares: request.min_area_hectares ?? 6,
      max_area_hectares: request.max_area_hectares ?? 20,
      enforce_constraints: request.enforce_constraints ?? true,
      num_sectors: request.num_sectors ?? 4,
    });

    const url = `${API_BASE_URL}/partition/stream`;
    console.log('[Partition] Fetching:', url);

    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      },
      body,
    })
      .then(response => {
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('No response body');
        }

        const decoder = new TextDecoder();
        let buffer = '';

        const processStream = async (): Promise<void> => {
          try {
            const { done, value } = await reader.read();

            if (done) {
              reject(new Error('Stream ended without completion'));
              return;
            }

            const chunk = decoder.decode(value, { stream: true });
            buffer += chunk;

            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
              if (line.startsWith('data: ')) {
                try {
                  const data = JSON.parse(line.slice(6));

                  if (data.type === 'progress') {
                    onProgress?.({
                      stage: data.stage,
                      percent: data.percent,
                      message: data.message,
                      current_superblock: data.current_superblock,
                      total_superblocks: data.total_superblocks,
                    });
                  } else if (data.type === 'complete') {
                    console.log('[Partition] Complete!', data.partition?.total_superblocks, 'superblocks');
                    resolve({
                      partition: data.partition,
                      street_network: { type: 'FeatureCollection', features: [], metadata: {} as any },
                      processing_time_seconds: data.processing_time_seconds,
                    });
                    return;
                  } else if (data.type === 'error') {
                    reject(new Error(data.message));
                    return;
                  }
                } catch (e) {
                  console.error('[Partition] Failed to parse SSE data:', e);
                }
              }
            }

            return processStream();
          } catch (streamError) {
            reject(streamError);
          }
        };

        processStream().catch(reject);
      })
      .catch(reject);
  });
}

// =============================================================================
// Routing API
// =============================================================================

export interface RouteRequest {
  origin: Coordinates;
  destination: Coordinates;
  respect_superblocks?: boolean;
  prefer_arterials?: boolean;
}

export async function computeRoute(request: RouteRequest): Promise<RouteResult> {
  const response = await api.post<RouteResult>('/route', {
    origin: request.origin,
    destination: request.destination,
    respect_superblocks: request.respect_superblocks ?? true,
    prefer_arterials: request.prefer_arterials ?? true,
  });
  return response.data;
}

// =============================================================================
// Size Optimization API
// =============================================================================

export async function getOptimalSize(
  bbox: BoundingBox,
  populationDensity?: number
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

export default api;
