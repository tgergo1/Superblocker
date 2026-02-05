import axios from 'axios';
import type { SearchResponse, StreetNetworkResponse, BoundingBox } from '../types';

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

export async function analyzeArea(
  bbox: BoundingBox,
  algorithms: string[] = ['graph', 'barcelona'],
  options?: {
    minAreaHectares?: number;
    maxAreaHectares?: number;
    boundaryRoadTypes?: string[];
  }
) {
  const response = await api.post('/analyze', {
    bbox,
    algorithms,
    min_area_hectares: options?.minAreaHectares ?? 4,
    max_area_hectares: options?.maxAreaHectares ?? 25,
    boundary_road_types: options?.boundaryRoadTypes ?? ['primary', 'secondary', 'tertiary'],
  });
  return response.data;
}

export default api;
