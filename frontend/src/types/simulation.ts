// Citywide capacity simulator: mirrors backend app/models/simulation.py.

export interface EntryChargeSettings {
  name?: string;
  cordon: [number, number][];
  charge: number;
  currency?: string;
  exempt_share?: number;
  residents_exempt?: boolean;
  value_of_time_per_hour?: number;
  running_cost_per_km?: number;
  price_elasticity?: number;
  elasticity_range?: [number, number] | null;
}

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
  entry_charge?: EntryChargeSettings | null;
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
  charged_vph?: number | null;
  planned_charged_vph?: number | null;
  inside_cordon?: boolean;
}

export interface ChargeScenarioResult {
  name: string;
  elasticity: number | null;
  trips: number;
  trips_not_made_share: number;
  mean_trip_minutes: number;
  mean_trip_minutes_change_percent: number;
  vehicle_km: number;
  vehicle_km_change_percent: number;
  vehicle_hours: number;
  vehicle_hours_change_percent: number;
  cordon_entries: number;
  cordon_entries_change_percent: number;
  inside_vehicle_km: number;
  inside_vehicle_km_change_percent: number;
  outside_vehicle_km: number;
  outside_vehicle_km_change_percent: number;
  ring_vehicle_km: number;
  ring_vehicle_km_change_percent: number;
  main_road_km_busier: number;
  minor_street_km_busier: number;
  main_road_km_quieter: number;
  congested_km: number;
  charged_entries: number;
  revenue_per_peak_hour: number;
  revenue_per_year: number;
}

export interface EntryChargeSummary {
  name: string;
  charge: number;
  currency: string;
  area_km2: number;
  cordon_links: number;
  zones_inside: number;
  price_elasticity: number;
  elasticity_range: [number, number] | null;
  value_of_time_per_hour: number;
  exempt_share: number;
  mean_entering_trip_cost: number;
  scenarios: Record<string, ChargeScenarioResult>;
  limitations: string[];
}

export interface CapacitySimulationResult {
  method: string;
  settings: Required<Omit<CapacitySimulationSettings, 'peak_hour_trips' | 'entry_charge'>> & {
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
  entry_charge?: EntryChargeSummary | null;
  limitations: string[];
  processing_time_seconds: number;
}

export interface SimulationProgress {
  message: string;
  percent: number;
}

export type SimulationMapView = 'change' | 'without' | 'with' | 'fee';
