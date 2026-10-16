import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { BoundaryRoadImpact, TrafficImpactEstimate } from '../../types';
import { impactColor, impactPercent } from '../../utils/impact';
import { TrafficImpactPanel } from './TrafficImpactPanel';

function road(overrides: Partial<BoundaryRoadImpact>): BoundaryRoadImpact {
  return {
    osm_id: 1,
    name: 'Thököly út',
    highway: 'secondary',
    baseline_peak_vph: 700,
    capacity_vph: 1300,
    added_peak_vph: { pessimistic: 140, central: 119, optimistic: 84 },
    percent_increase_central: 17,
    baseline_volume_capacity_ratio: 0.54,
    planned_volume_capacity_ratio_central: 0.63,
    measured: false,
    arterial: true,
    superblock_ids: ['sb_1'],
    ...overrides,
  };
}

const impact: TrafficImpactEstimate = {
  method: 'local_entry_pair_rerouting',
  settings: {
    through_traffic_share: 0.5,
    peak_hour_share_of_daily: 0.1,
    max_entry_pairs_per_superblock: 200,
    scenarios: [
      { name: 'pessimistic', evaporation_rate: 0, description: 'All reroute.' },
      { name: 'central', evaporation_rate: 0.15, description: 'Some vanish.' },
      { name: 'optimistic', evaporation_rate: 0.4, description: 'London-like.' },
    ],
    central_scenario: 'central',
    high_increase_percent: 10,
  },
  traffic_basis: 'modeled_topology',
  total_displaced_peak_vph: 1234,
  added_boundary_vehicle_km_peak: { central: 50 },
  interior_vehicle_km_removed_peak: 40,
  maximum_percent_increase_central: 17,
  roads_over_threshold: 1,
  roads_over_capacity_central: 0,
  minor_roads_loaded: 1,
  boundary_roads: [
    road({}),
    road({ osm_id: 3, added_peak_vph: { pessimistic: 20, central: 10, optimistic: 5 } }),
    road({ osm_id: 2, name: 'Kis utca', highway: 'residential', arterial: false, baseline_peak_vph: 80 }),
  ],
  superblocks: [
    {
      superblock_id: 'sb_1',
      entry_pairs_checked: 12,
      cut_through_pairs: 4,
      unresolved_pairs: 0,
      displaced_peak_vph: 60,
      interior_traffic_reduction_percent: 42,
      mean_detour_seconds: 35,
    },
  ],
  limitations: ['Screening estimate.'],
};

describe('impact utilities', () => {
  it('computes the scenario increase and color band', () => {
    expect(impactPercent(road({}), 'pessimistic')).toBeCloseTo(20);
    expect(impactColor(1)[0]).toBe(34);
    expect(impactColor(80)[0]).toBe(127);
  });
});

describe('TrafficImpactPanel', () => {
  it('ranks main roads, switches scenarios and toggles the map', () => {
    const onShow = vi.fn();
    const onScenario = vi.fn();
    render(
      <TrafficImpactPanel
        impact={impact}
        showOnMap={false}
        onShowOnMapChange={onShow}
        scenario="central"
        onScenarioChange={onScenario}
      />,
    );

    expect(screen.getByText('Thököly út')).toBeTruthy();
    expect(screen.queryByText('Kis utca')).toBeNull();
    expect(screen.getByText('+17%')).toBeTruthy();
    expect(screen.getByText('42%')).toBeTruthy();
    expect(screen.getByText('50 veh-km')).toBeTruthy();
    expect(screen.getByText(/1 minor streets would carry/)).toBeTruthy();

    fireEvent.click(screen.getByLabelText('include minor streets'));
    expect(screen.getByText('Kis utca')).toBeTruthy();

    fireEvent.click(screen.getByRole('radio', { name: /No evaporation/ }));
    expect(onScenario).toHaveBeenCalledWith('pessimistic');

    fireEvent.click(screen.getByLabelText('Color boundary roads by predicted increase'));
    expect(onShow).toHaveBeenCalledWith(true);
  });
});
