import { useQuery } from '@tanstack/react-query';
import { getStreetNetwork } from '../services/api';
import type { BoundingBox, StreetNetworkResponse } from '../types';

export function useStreetNetwork(bbox: BoundingBox | null, enabled = true) {
  return useQuery<StreetNetworkResponse>({
    queryKey: ['streetNetwork', bbox],
    queryFn: () => {
      if (!bbox) throw new Error('No bounding box provided');
      return getStreetNetwork(bbox);
    },
    enabled: enabled && bbox !== null,
    staleTime: 10 * 60 * 1000, // 10 minutes
    gcTime: 30 * 60 * 1000, // 30 minutes (formerly cacheTime)
    retry: 2,
  });
}
