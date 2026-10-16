import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { CapacitySimulationResult, SimulatedLink, SimulationLevelResult } from '../../types/simulation';
import { simulationLinkColor, CHANGE_BANDS, LOAD_BANDS } from '../../utils/simulation';
import { CapacitySimulatorPanel } from './CapacitySimulatorPanel';

function level(demand: number, index: number, minutes: number): SimulationLevelResult {
  return {
    demand_multiplier: demand,
    trips: 10_000 * demand,
    served_trips: 10_000 * demand,
    blocked_trips: 0,
    vehicle_km: 40_000 * demand,
    vehicle_hours: 1_500 * demand,
    mean_trip_minutes: minutes,
    mean_speed_kph: 26,
    travel_time_index: index,
    congested_km: 3 * demand,
    over_capacity_km: demand,
    relative_gap: 0.004,
    iterations: 6,
  };
}

const result: CapacitySimulationResult = {
  method: 'static_user_equilibrium_frank_wolfe',
  settings: {
    zone_size_m: 800,
    max_zones: 100,
    connectors_per_zone: 2,
    peak_hour_trips: null,
    target_peak_travel_time_index: 1.3,
    distance_decay_per_minute: 0.12,
    demand_levels: [0.5, 1, 2, 3],
    max_iterations: 6,
    breakdown_travel_time_index: 2,
  },
  base_peak_hour_trips: 10_000,
  demand_calibrated: true,
  zone_count: 80,
  connector_count: 160,
  node_count: 30_000,
  link_count: 80_000,
  links_closed_by_plan: 4_000,
  junctions_cut_off_by_plan: 120,
  baseline: {
    name: 'without_superblocks',
    levels: [level(0.5, 1.01, 10), level(1, 1.05, 10.5), level(2, 1.3, 13), level(3, 1.7, 17)],
    capacity_multiplier: 2.5,
    capacity_trips: 25_000,
    capacity_reached: true,
    drivable_km: 5_000,
  },
  planned: {
    name: 'with_superblocks',
    levels: [level(0.5, 1.03, 10.3), level(1, 1.08, 10.8), level(2, 1.4, 14), level(3, 2.0, 20)],
    capacity_multiplier: 2.2,
    capacity_trips: 22_000,
    capacity_reached: true,
    drivable_km: 4_500,
  },
  capacity_change_percent: -12,
  mean_trip_minutes_change_percent: 2.9,
  vehicle_km_change_percent: 1.5,
  vehicle_hours_change_percent: 3,
  links: [],
  limitations: ['Static peak-hour equilibrium.'],
  processing_time_seconds: 42,
};

describe('CapacitySimulatorPanel', () => {
  it('runs the simulation with an optional trip total', () => {
    const onRun = vi.fn();
    render(
      <CapacitySimulatorPanel
        result={null}
        isRunning={false}
        progress={null}
        error={null}
        onRun={onRun}
        onCancel={vi.fn()}
        showOnMap
        onShowOnMapChange={vi.fn()}
        mapView="change"
        onMapViewChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Run traffic simulation' }));
    expect(onRun).toHaveBeenLastCalledWith(null);
    fireEvent.change(screen.getByLabelText('Peak-hour car trips'), { target: { value: '12,000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run traffic simulation' }));
    expect(onRun).toHaveBeenLastCalledWith(12_000);
  });

  it('compares capacity and trip times with and without the plan', () => {
    const onMapViewChange = vi.fn();
    render(
      <CapacitySimulatorPanel
        result={result}
        isRunning={false}
        progress={null}
        error={null}
        onRun={vi.fn()}
        onCancel={vi.fn()}
        showOnMap
        onShowOnMapChange={vi.fn()}
        mapView="change"
        onMapViewChange={onMapViewChange}
      />,
    );
    expect(screen.getByText('Capacity -12% with superblocks')).toBeTruthy();
    expect(screen.getByText('2.50×')).toBeTruthy();
    expect(screen.getByText('2.20×')).toBeTruthy();
    expect(screen.getByText('10.5 → 10.8 min')).toBeTruthy();
    expect(screen.getByRole('img', { name: /Average trip time/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: 'With plan' }));
    expect(onMapViewChange).toHaveBeenCalledWith('with');
  });
});

describe('simulationLinkColor', () => {
  const link: SimulatedLink = {
    coordinates: [[19, 47.5], [19.001, 47.5]],
    osm_id: 1,
    name: 'Dob utca',
    highway: 'residential',
    baseline_vph: 300,
    planned_vph: 20,
    capacity_vph: 700,
    baseline_vc: 0.43,
    planned_vc: 0.03,
    closed_in_plan: false,
    is_boundary_road: false,
  };

  it('shows less traffic, closures and load bands', () => {
    expect(simulationLinkColor(link, 'change')).toEqual(CHANGE_BANDS[3].color);
    expect(simulationLinkColor({ ...link, closed_in_plan: true }, 'change')).toEqual(CHANGE_BANDS[4].color);
    expect(simulationLinkColor({ ...link, planned_vph: 700 }, 'change')).toEqual(CHANGE_BANDS[0].color);
    expect(simulationLinkColor(link, 'without')).toEqual(LOAD_BANDS[0].color);
    expect(simulationLinkColor({ ...link, planned_vc: 1.2 }, 'with')).toEqual(LOAD_BANDS[3].color);
  });
});
