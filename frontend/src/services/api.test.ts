import { afterEach, describe, expect, it, vi } from 'vitest';
import { API_BASE_URL, partitionCityWithProgress } from './api';

describe('streaming API client', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('uses the same-origin API by default', () => {
    expect(API_BASE_URL).toBe('/api/v1');
  });

  it('keeps the real street network returned by partition streaming', async () => {
    const progress = {
      type: 'progress',
      stage: 'network',
      percent: 10,
      message: 'Loading',
    };
    const complete = {
      type: 'complete',
      partition: {
        superblocks: [],
        arterial_network: [],
        bbox: { north: 1, south: 0, east: 1, west: 0 },
        total_area_hectares: 0,
        coverage_percent: 0,
        total_superblocks: 0,
        total_modal_filters: 0,
        total_one_way_conversions: 0,
        total_street_cuts: 0,
        total_unreachable_addresses: 0,
        total_two_way_conversions: 0,
      },
      street_network: {
        type: 'FeatureCollection',
        features: [{
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] },
          properties: { osmid: 1 },
        }],
        metadata: { total_edges: 1 },
      },
      processing_time_seconds: 0.1,
    };
    const body = `data: ${JSON.stringify(progress)}\n\ndata: ${JSON.stringify(complete)}\n\n`;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })));
    const onProgress = vi.fn();

    const result = await partitionCityWithProgress(
      { bbox: { north: 1, south: 0, east: 1, west: 0 } },
      onProgress,
    );
    expect(onProgress).toHaveBeenCalledWith(expect.objectContaining({ percent: 10 }));
    expect(result.street_network.features).toHaveLength(1);
  });

  it('turns malformed stream data into a safe user-facing error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('data: {"name": NaN}\n\n', {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })));

    await expect(partitionCityWithProgress({
      bbox: { north: 1, south: 0, east: 1, west: 0 },
    })).rejects.toThrow('The server returned an invalid response. Please try again.');
  });
});
