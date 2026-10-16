// Citywide capacity simulator: mirrors backend app/models/simulation.py.

export interface CapacitySimulationSettings {
  zone_size_m?: number;
  max_zones?: number;
  connectors_per_zone?: number;
  peak_hour_trips?: number | null;
  target_peak_travel_time_index?: number;
  distance_decay_per_minute?: number;
  demand_levels?: number[];
  max_iterations?: number;
  breakdown_travel_time_index?: number;
}

export interface SimulationLevelResult {
  demand_multiplier: number;
  trips: number;
  served_trips: number;
  blocked_trips: number;
  vehicle_km: number;
  vehicle_hours: number;
  mean_trip_minutes: number;
  mean_speed_kph: number;
  travel_time_index: number;
  congested_km: number;
  over_capacity_km: number;
  relative_gap: number;
  iterations: number;
}

export interface SimulationScenarioSummary {
  name: 'without_superblocks' | 'with_superblocks';
  levels: SimulationLevelResult[];
  capacity_multiplier: number | null;
  capacity_trips: number | null;
  capacity_reached: boolean;
  drivable_km: number;
}

export interface SimulatedLink {
  coordinates: [number, number][];
  osm_id: number;
  name: string | null;
  highway: string;
  baseline_vph: number;
  planned_vph: number;
  capacity_vph: number;
  baseline_vc: number;
  planned_vc: number;
  closed_in_plan: boolean;
  is_boundary_road: boolean;
}

export interface CapacitySimulationResult {
  method: string;
  settings: Required<Omit<CapacitySimulationSettings, 'peak_hour_trips'>> & {
    peak_hour_trips: number | null;
  };
  base_peak_hour_trips: number;
  demand_calibrated: boolean;
  zone_count: number;
  connector_count: number;
  node_count: number;
  link_count: number;
  links_closed_by_plan: number;
  junctions_cut_off_by_plan: number;
  baseline: SimulationScenarioSummary;
  planned: SimulationScenarioSummary;
  capacity_change_percent: number | null;
  mean_trip_minutes_change_percent: number;
  vehicle_km_change_percent: number;
  vehicle_hours_change_percent: number;
  links: SimulatedLink[];
  limitations: string[];
  processing_time_seconds: number;
}

export interface SimulationProgress {
  message: string;
  percent: number;
}

export type SimulationMapView = 'change' | 'without' | 'with';
