import { useMemo, useState } from 'react';
import type {
  PartitionProgress,
  CityPartition,
  AccessTarget,
  ModificationType,
  StreetModification,
  TrafficObservation,
  PlanningEvidencePackage,
} from '../../types';
import './PartitionControls.css';

const ACTION_PRESENTATION: Record<ModificationType, {
  symbol: string;
  label: string;
  purpose: string;
}> = {
  modal_filter: {
    symbol: 'X',
    label: 'Modal filter',
    purpose: 'Stops modeled motor traffic. Other modes require separate physical and operational validation.',
  },
  one_way: {
    symbol: '>',
    label: 'One-way',
    purpose: 'Returns vehicles toward their entry boundary instead of across the cell.',
  },
  two_way: {
    symbol: '<>',
    label: 'Two-way access',
    purpose: 'Preserves local entry and exit without reconnecting cross-traffic.',
  },
  turn_restriction: {
    symbol: '!',
    label: 'Turn restriction',
    purpose: 'Removes a through movement at the junction while keeping local access.',
  },
  full_closure: {
    symbol: '=',
    label: 'Street cut',
    purpose: 'Closes this point to motor traffic and separates directional territories.',
  },
};

const PLAN_STAGE_LABELS: Record<CityPartition['readiness']['status'], string> = {
  model_only: 'Model draft',
  topology_candidate: 'Topology candidate',
  data_qualified: 'Data qualified',
  baseline_calibrated: 'Baseline calibrated',
  impact_validated: 'Ready for engineering review',
  engineering_reviewed: 'Engineering reviewed',
  approved_for_pilot: 'Approved for pilot',
  approved_for_permanent_works: 'Approved for permanent works',
};

const BUS_GATE_PRESENTATION = {
  symbol: 'B',
  label: 'Bus gate',
  purpose: 'Buses, trolleybuses, trams and emergency vehicles pass; general traffic does not.',
};

function actionPresentation(modification: StreetModification) {
  if (modification.filter_type === 'bus_gate') return BUS_GATE_PRESENTATION;
  const presentation = ACTION_PRESENTATION[modification.modification_type];
  if (modification.filter_type === 'emergency_bollard') {
    return {
      ...presentation,
      purpose: 'Planters stop through traffic; a lockable bollard lets emergency vehicles through.',
    };
  }
  return presentation;
}

function actionClass(modification: StreetModification): string {
  return modification.filter_type === 'bus_gate' ? 'bus_gate' : modification.modification_type;
}

function streetName(modification: StreetModification): string {
  return modification.name?.trim() || `OSM way ${modification.osm_id}`;
}

function formatActionLocation(modification: StreetModification): string {
  if (!modification.filter_location) {
    return `the marked segment between nodes ${modification.u} and ${modification.v}`;
  }
  const { lat, lon } = modification.filter_location;
  const latitude = `${Math.abs(lat).toFixed(5)}° ${lat >= 0 ? 'N' : 'S'}`;
  const longitude = `${Math.abs(lon).toFixed(5)}° ${lon >= 0 ? 'E' : 'W'}`;
  return `${latitude}, ${longitude}`;
}

function actionInstruction(modification: StreetModification): string {
  const name = streetName(modification);
  const location = formatActionLocation(modification);

  switch (modification.modification_type) {
    case 'modal_filter':
      if (modification.filter_type === 'bus_gate') {
        const routes = modification.transit_route_refs?.length
          ? ` It keeps ${modification.transit_route_refs.slice(0, 4).join(', ')} on their route.`
          : '';
        return `Install a camera-enforced bus gate on “${name}” at ${location}.${routes}`;
      }
      return `Install a modal filter on “${name}” at ${location}.`;
    case 'one_way':
      return `Make “${name}” one-way at ${location}; follow the > direction shown on the map.`;
    case 'two_way':
      return `Open “${name}” to two-way local access at ${location}.`;
    case 'turn_restriction':
      return `Add the marked turn restriction on “${name}” at ${location}.`;
    case 'full_closure':
      return `Cut “${name}” to motor traffic at ${location}.`;
  }
}

// Helper function to format time in mm:ss
function formatTime(seconds: number): string {
  if (seconds <= 0) return '0:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export interface PartitionParameters {
  targetSizeHectares: number;
  minAreaHectares: number;
  maxAreaHectares: number;
  keepResidentialInside?: boolean;
  planningMode?: 'superblocks' | 'circulation_sectors';
}

interface PartitionControlsProps {
  isLoading: boolean;
  progress?: PartitionProgress & { elapsedTime?: number };
  parameters: PartitionParameters;
  onParametersChange: (params: PartitionParameters) => void;
  onPartition: () => void;
  onCancel: () => void;
  canPartition: boolean;
  partition?: CityPartition | null;
  // Display options
  showEntryPoints: boolean;
  onShowEntryPointsChange: (show: boolean) => void;
  showModalFilters: boolean;
  onShowModalFiltersChange: (show: boolean) => void;
  error?: Error | null;
  boundaryMode: 'administrative_polygon' | 'bounding_box_fallback';
  trafficObservations: TrafficObservation[];
  onTrafficObservationsChange: (observations: TrafficObservation[]) => void;
  accessTargets: AccessTarget[];
  onAccessTargetsChange: (targets: AccessTarget[]) => void;
  accessDatasetSource: string | null;
  onAccessDatasetSourceChange: (source: string | null) => void;
  accessDatasetComplete: boolean;
  onAccessDatasetCompleteChange: (complete: boolean) => void;
  planningEvidence: PlanningEvidencePackage;
  onPlanningEvidenceChange: (evidence: PlanningEvidencePackage) => void;
}

export function PartitionControls({
  isLoading,
  progress,
  parameters,
  onParametersChange,
  onPartition,
  onCancel,
  canPartition,
  partition,
  showEntryPoints,
  onShowEntryPointsChange,
  showModalFilters,
  onShowModalFiltersChange,
  error,
  boundaryMode,
  trafficObservations,
  onTrafficObservationsChange,
  accessTargets,
  onAccessTargetsChange,
  accessDatasetSource,
  onAccessDatasetSourceChange,
  accessDatasetComplete,
  onAccessDatasetCompleteChange,
  planningEvidence,
  onPlanningEvidenceChange,
}: PartitionControlsProps) {
  const [settingsExpanded, setSettingsExpanded] = useState(false);
  const [evidenceExpanded, setEvidenceExpanded] = useState(false);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [visibleActionCount, setVisibleActionCount] = useState(50);
  const streetActions = useMemo(
    () => partition?.superblocks.flatMap((superblock, superblockIndex) =>
      superblock.modifications.map((modification, modificationIndex) => ({
        modification,
        superblockIndex,
        key: `${superblock.id}-${modification.u}-${modification.v}-${modification.key}-${modification.modification_type}-${modificationIndex}`,
      }))) ?? [],
    [partition],
  );

  const sectorMode = parameters.planningMode === 'circulation_sectors';
  const ranges = sectorMode
    ? { target: [50, 300, 10], min: [10, 100, 5], max: [100, 600, 10] }
    : { target: [6, 25, 1], min: [2, 15, 1], max: [10, 40, 1] };

  const handlePlanningModeChange = (mode: 'superblocks' | 'circulation_sectors') => {
    onParametersChange(
      mode === 'circulation_sectors'
        ? { ...parameters, planningMode: mode, targetSizeHectares: 150, minAreaHectares: 30, maxAreaHectares: 400 }
        : { ...parameters, planningMode: mode, targetSizeHectares: 12, minAreaHectares: 6, maxAreaHectares: 20 },
    );
  };

  const handleTargetSizeChange = (value: number) => {
    onParametersChange({
      ...parameters,
      targetSizeHectares: value,
    });
  };

  const handleMinAreaChange = (value: number) => {
    onParametersChange({
      ...parameters,
      minAreaHectares: value,
      maxAreaHectares: Math.max(value + 2, parameters.maxAreaHectares),
    });
  };

  const handleMaxAreaChange = (value: number) => {
    onParametersChange({
      ...parameters,
      minAreaHectares: Math.min(parameters.minAreaHectares, value - 2),
      maxAreaHectares: value,
    });
  };

  // Calculate elapsed time if loading
  const elapsedSeconds = progress?.elapsedTime ?? 0;
  const validatedSuperblocks = partition?.superblocks.filter(
    (superblock) => superblock.modeled_directional_validation_passed,
  ).length ?? 0;
  const allCrossTrafficBlocked = Boolean(
    partition && partition.total_superblocks > 0 && validatedSuperblocks === partition.total_superblocks,
  );
  const totalModifications = streetActions.length;

  const loadTrafficFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const text = await file.text();
      let rows: Array<Record<string, unknown>>;
      if (file.name.toLowerCase().endsWith('.json')) {
        const value = JSON.parse(text) as unknown;
        rows = Array.isArray(value) ? value as Array<Record<string, unknown>> : [];
      } else {
        const lines = text.split(/\r?\n/).filter(line => line.trim());
        const headers = (lines.shift() ?? '').split(',').map(value => value.trim());
        rows = lines.map(line => Object.fromEntries(
          line.split(',').map((value, index) => [headers[index], value.trim()]),
        ));
      }
      const observations = rows.map((row, index) => {
        const osmId = Number(row.osm_id);
        const volume = Number(row.volume_vph);
        const source = String(row.source ?? '').trim();
        if (!Number.isInteger(osmId) || osmId <= 0 || !Number.isFinite(volume) || volume < 0 || !source) {
          throw new Error(`Invalid traffic row ${index + 1}`);
        }
        const vehicleClass = String(row.vehicle_class ?? 'all_motor') as TrafficObservation['vehicle_class'];
        return {
          osm_id: osmId,
          volume_vph: Math.round(volume),
          source,
          observed_at: row.observed_at ? String(row.observed_at) : null,
          observation_id: row.observation_id ? String(row.observation_id) : null,
          interval_start: row.interval_start ? String(row.interval_start) : null,
          interval_end: row.interval_end ? String(row.interval_end) : null,
          direction: (row.direction ? String(row.direction) : 'both') as TrafficObservation['direction'],
          lane: row.lane ? Number(row.lane) : null,
          vehicle_class: vehicleClass,
          turn_from_osm_id: row.turn_from_osm_id ? Number(row.turn_from_osm_id) : null,
          turn_to_osm_id: row.turn_to_osm_id ? Number(row.turn_to_osm_id) : null,
          speed_kph: row.speed_kph ? Number(row.speed_kph) : null,
          occupancy_percent: row.occupancy_percent ? Number(row.occupancy_percent) : null,
          queue_length_m: row.queue_length_m ? Number(row.queue_length_m) : null,
          confidence: row.confidence === undefined ? 1 : Number(row.confidence),
          dataset_id: row.dataset_id ? String(row.dataset_id) : null,
        } satisfies TrafficObservation;
      });
      if (!observations.length) throw new Error('The traffic file contains no observations');
      onTrafficObservationsChange(observations);
      setEvidenceError(null);
    } catch (fileError) {
      setEvidenceError(fileError instanceof Error ? fileError.message : 'Invalid traffic file');
    }
  };

  const loadPlanningEvidence = async (file: File | undefined) => {
    if (!file) return;
    try {
      const value = JSON.parse(await file.text()) as PlanningEvidencePackage;
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error('The evidence package must be a JSON object');
      }
      if (value.demand_model) {
        if (!value.demand_model.model_id || !value.demand_model.version
          || value.demand_model.zones.length < 2 || !value.demand_model.matrix.length) {
          throw new Error('The demand model is incomplete');
        }
      }
      onPlanningEvidenceChange(value);
      setEvidenceError(null);
    } catch (fileError) {
      setEvidenceError(fileError instanceof Error ? fileError.message : 'Invalid evidence package');
    }
  };

  const loadAccessFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const collection = JSON.parse(await file.text()) as {
        type?: string;
        features?: Array<{
          id?: string | number;
          geometry?: { type?: string; coordinates?: unknown };
          properties?: Record<string, unknown>;
        }>;
      };
      if (collection.type !== 'FeatureCollection' || !Array.isArray(collection.features)) {
        throw new Error('Access data must be a GeoJSON FeatureCollection');
      }
      const source = accessDatasetSource?.trim() || file.name;
      const targets = collection.features.map((feature, index) => {
        if (feature.geometry?.type !== 'Point' || !Array.isArray(feature.geometry.coordinates)) {
          throw new Error(`Access feature ${index + 1} must be a Point`);
        }
        const [lon, lat] = feature.geometry.coordinates as number[];
        const kind = String(feature.properties?.kind ?? 'address');
        if (!Number.isFinite(lon) || !Number.isFinite(lat)
          || !['address', 'parcel', 'building', 'emergency', 'delivery'].includes(kind)) {
          throw new Error(`Invalid access feature ${index + 1}`);
        }
        return {
          id: String(feature.id ?? feature.properties?.id ?? `access-${index + 1}`),
          coordinates: { lat, lon },
          kind: kind as AccessTarget['kind'],
          label: feature.properties?.label ? String(feature.properties.label) : null,
          source,
        } satisfies AccessTarget;
      });
      if (!targets.length) throw new Error('The access file contains no targets');
      onAccessTargetsChange(targets);
      if (!accessDatasetSource) onAccessDatasetSourceChange(file.name);
      setEvidenceError(null);
    } catch (fileError) {
      setEvidenceError(fileError instanceof Error ? fileError.message : 'Invalid access file');
    }
  };

  return (
    <div className={`partition-controls ${partition ? 'has-results' : ''}`}>
      <div className="planner-intro">
        <span className="planner-kicker">Automated analysis</span>
        <h2>Citywide superblock plan</h2>
        <p>
          Loads the complete selected road network, extracts closed superblock cells,
          and blocks cross-sector shortcuts inside every generated cell.
        </p>
        <div className="planner-rule">
          <span aria-hidden="true">→</span>
          Enter and return on the same side; cross-traffic stays on boundary roads.
        </div>
      </div>

      <button
        type="button"
        className="settings-toggle evidence-toggle"
        onClick={() => setEvidenceExpanded(!evidenceExpanded)}
        aria-expanded={evidenceExpanded}
        aria-controls="evidence-inputs-panel"
      >
        <span className="settings-icon">{evidenceExpanded ? '▼' : '▶'}</span>
        <span>Evidence inputs</span>
        <span className={`input-status ${boundaryMode === 'administrative_polygon' ? 'ready' : 'missing'}`}>
          {boundaryMode === 'administrative_polygon' ? 'Exact boundary' : 'BBox fallback'}
        </span>
      </button>

      {evidenceExpanded && (
        <div className="settings-panel evidence-panel" id="evidence-inputs-panel">
          <div className="evidence-row">
            <div>
              <strong>Measured traffic</strong>
              <small>CSV/JSON: osm_id, volume_vph, source, observed_at</small>
            </div>
            <label className="file-button">
              {trafficObservations.length ? `${trafficObservations.length} loaded` : 'Load counts'}
              <input
                type="file"
                accept=".csv,.json,text/csv,application/json"
                onChange={(event) => void loadTrafficFile(event.target.files?.[0])}
              />
            </label>
          </div>
          <div className="evidence-row">
            <div>
              <strong>Planning evidence package</strong>
              <small>JSON: authoritative roads, OD demand, turns, exclusions, services, modes, manifests, and quality metrics</small>
            </div>
            <label className="file-button">
              {planningEvidence.demand_model
                ? `${planningEvidence.demand_model.zones.length} zones`
                : 'Load package'}
              <input
                type="file"
                accept=".json,application/json"
                onChange={(event) => void loadPlanningEvidence(event.target.files?.[0])}
              />
            </label>
          </div>
          <div className="evidence-row">
            <div>
              <strong>Access targets</strong>
              <small>GeoJSON Point features for addresses, parcels, and services</small>
            </div>
            <label className="file-button">
              {accessTargets.length ? `${accessTargets.length} loaded` : 'Load GeoJSON'}
              <input
                type="file"
                accept=".geojson,.json,application/geo+json,application/json"
                onChange={(event) => void loadAccessFile(event.target.files?.[0])}
              />
            </label>
          </div>
          <label className="param-label" htmlFor="access-dataset-source">
            <span>Access dataset source</span>
          </label>
          <input
            id="access-dataset-source"
            className="evidence-source-input"
            value={accessDatasetSource ?? ''}
            onChange={(event) => onAccessDatasetSourceChange(event.target.value || null)}
            placeholder="Authority and dataset version"
          />
          <label className="checkbox-label evidence-complete-check">
            <input
              type="checkbox"
              checked={accessDatasetComplete}
              disabled={!accessTargets.length}
              onChange={(event) => onAccessDatasetCompleteChange(event.target.checked)}
            />
            <span>Declare this snapshot complete; a verified registry manifest is still required for the data gate</span>
          </label>
          {evidenceError && <div className="control-error" role="alert">{evidenceError}</div>}
        </div>
      )}

      {/* Progress Indicator */}
      {isLoading && progress && (
        <div className="progress-section">
          <div className="progress-header">
            <span className="progress-stage">{progress.stage}</span>
            {progress.current_superblock && progress.total_superblocks && (
              <span className="progress-count">
                {progress.current_superblock}/{progress.total_superblocks}
              </span>
            )}
          </div>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${progress.percent}%` }}
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress.percent}
              aria-label="City analysis progress"
            />
          </div>
          <div className="progress-info">
            <span className="progress-message">{progress.message}</span>
            <span className="progress-time">{formatTime(elapsedSeconds)}</span>
          </div>
        </div>
      )}

      {isLoading ? (
        <button type="button" className="action-button cancel-button" onClick={onCancel}>
          Cancel analysis
        </button>
      ) : (
        <button
          type="button"
          className="action-button partition-button"
          onClick={() => {
            setVisibleActionCount(50);
            onPartition();
          }}
          disabled={!canPartition}
        >
          {partition ? 'Re-analyze entire city' : 'Analyze entire city'}
        </button>
      )}

      {!canPartition && !isLoading && (
        <div className="action-hint">Select a city to begin the automated plan.</div>
      )}

      {error && <div className="control-error" role="alert">{error.message}</div>}

      {/* Results Summary */}
      {partition && !isLoading && (
        <div className="results-summary">
          <div className={`validation-banner ${allCrossTrafficBlocked ? 'success' : 'failure'}`}>
            <span className="validation-icon" aria-hidden="true">
              {allCrossTrafficBlocked ? '✓' : '!'}
            </span>
            <span>
              <strong>
                {allCrossTrafficBlocked
                  ? 'Modeled cross-sector paths blocked'
                  : 'Modeled path validation incomplete'}
              </strong>
              <small>
                {validatedSuperblocks}/{partition.total_superblocks} cells pass the graph path test; this is not field compliance
              </small>
            </span>
          </div>
          <div className={`readiness-banner ${partition.readiness.approved_for_pilot ? 'ready' : 'blocked'}`}>
            <strong>
              {PLAN_STAGE_LABELS[partition.readiness.status]}
            </strong>
            <small>
              {partition.readiness.approved_for_permanent_works
                ? 'All modeled, professional, pilot, and statutory gates are recorded.'
                : `${partition.readiness.blockers.length} requirement${partition.readiness.blockers.length === 1 ? '' : 's'} block the next assurance stage.`}
            </small>
            {partition.plan_id && (
              <code className="plan-id">Plan {partition.plan_id.slice(0, 16)}</code>
            )}
            {!partition.readiness.approved_for_permanent_works && (
              <ul className="readiness-blockers">
                {partition.readiness.blockers.map(blocker => <li key={blocker}>{blocker}</li>)}
              </ul>
            )}
            <ol className="readiness-gates" aria-label="Plan assurance gates">
              {partition.readiness.gates.map(gate => (
                <li key={gate.stage} className={gate.passed ? 'passed' : 'pending'}>
                  <span aria-hidden="true">{gate.passed ? '✓' : '○'}</span>
                  {PLAN_STAGE_LABELS[gate.stage]}
                </li>
              ))}
            </ol>
          </div>
          <div className="summary-title">City plan</div>
          <div className="summary-grid">
            <div className="summary-item">
              <span className="summary-value">{partition.total_superblocks}</span>
              <span className="summary-label">Superblocks</span>
            </div>
            <div className="summary-item">
              <span className="summary-value">{partition.eligible_road_coverage_percent.toFixed(0)}%</span>
              <span className="summary-label">Eligible roads accounted</span>
            </div>
            <div className="summary-item">
              <span className="summary-value">{partition.arterial_network.length}</span>
              <span className="summary-label">Boundary roads</span>
            </div>
            <div className="summary-item">
              <span className="summary-value">{totalModifications}</span>
              <span className="summary-label">Access changes</span>
            </div>
          </div>
          <div className="modification-breakdown">
            <span>{partition.total_modal_filters} filters</span>
            <span>{partition.total_one_way_conversions} one-way changes</span>
            <span>{partition.total_two_way_conversions} two-way access changes</span>
            <span>{partition.total_street_cuts} street cuts</span>
            {(partition.filter_design?.bus_gates ?? 0) > 0 && (
              <span>{partition.filter_design?.bus_gates} bus gates</span>
            )}
          </div>
          {partition.public_space && partition.public_space.calmed_street_km > 0 && (
            <div className="public-space-summary">
              {partition.public_space.calmed_street_km.toFixed(0)} km of streets calmed;{' '}
              {(partition.public_space.reclaimable_m2 / 10_000).toFixed(1)} ha could become public space,
              with {partition.public_space.plaza_candidates} candidate plazas (green dots on the map).
            </div>
          )}
          {partition.health && partition.health.superblocks.length > 0 && (
            <div className="health-summary" title={partition.health.limitations.join(' ')}>
              Inside superblocks, about {Math.abs(partition.health.mean_interior_noise_change_db_low).toFixed(0)}–
              {Math.abs(partition.health.mean_interior_noise_change_db_high).toFixed(0)} dB quieter and{' '}
              {Math.abs(partition.health.mean_interior_no2_change_percent_low).toFixed(0)}–
              {Math.abs(partition.health.mean_interior_no2_change_percent_high).toFixed(0)}% less NO₂ on average
              {partition.health.boundary_roads_noticeably_louder > 0
                ? `; ${partition.health.boundary_roads_noticeably_louder} main roads get noticeably louder (3 dB or more).`
                : '; no main road gets noticeably louder.'}{' '}
              {partition.health.residents_noticeably_quieter != null
                ? `${Math.round(partition.health.residents_noticeably_quieter).toLocaleString('en')} residents live on streets that get noticeably quieter (3 dB or more). `
                : ''}
              Screening estimate.
            </div>
          )}
          {partition.transit_access?.checked && (
            <div className="transit-summary" title={partition.transit_access.limitations.join(' ')}>
              {Math.round(partition.transit_access.mean_covered_share * 100)}% of superblock streets are
              within {partition.transit_access.walk_threshold_m.toFixed(0)} m walk of a stop
              {partition.transit_access.superblocks_poorly_served > 0
                ? `; ${partition.transit_access.superblocks_poorly_served} superblocks${
                    partition.transit_access.residents_poorly_served
                      ? ` (${Math.round(partition.transit_access.residents_poorly_served).toLocaleString('en')} residents)`
                      : ''
                  } are poorly served and need a stop or crossing first.`
                : '; every superblock is well served.'}
            </div>
          )}
          {partition.population && partition.population.residents_in_area > 0 && (
            <div className="transit-summary" title={partition.population.limitations.join(' ')}>
              {Math.round(partition.population.residents_in_superblocks).toLocaleString('en')} residents
              ({Math.round(partition.population.share_in_superblocks * 100)}% of the area) live inside
              superblocks. Source: {partition.population.source}.
            </div>
          )}
          {partition.school_streets && partition.school_streets.facilities_loaded > 0 && (
            <div className="school-summary">
              {partition.school_streets.interior_school_streets} school streets inside superblocks
              (orange S, closed at drop-off and pick-up); {partition.school_streets.boundary_frontages}{' '}
              schools on main roads need safe crossings (white S).
            </div>
          )}
          {partition.emergency_access?.checked && (
            <div className={`emergency-summary ${partition.emergency_access.superblocks_over_threshold > 0 ? 'warn' : ''}`}>
              Emergency response from {partition.emergency_access.stations_used} stations:{' '}
              {partition.emergency_access.superblocks_over_threshold === 0
                ? `no superblock slower by more than ${partition.emergency_access.threshold_seconds} s`
                : `${partition.emergency_access.superblocks_over_threshold} superblocks slower by more than ${partition.emergency_access.threshold_seconds} s`}
              {partition.emergency_access.superblocks_over_threshold_without_exemption > 0 &&
                ` (${partition.emergency_access.superblocks_over_threshold_without_exemption} if bollards stay locked)`}
              .
            </div>
          )}
          {partition.filter_design && partition.filter_design.protected_route_refs.length > 0 && (
            <div className="transit-protection">
              Keeps {partition.filter_design.protected_route_refs.length} bus, trolleybus and tram
              routes running through superblocks via bus gates.
            </div>
          )}
          {partition.total_unreachable_access_targets > 0 && (
            <div className="warning-banner">
              {partition.total_unreachable_access_targets} supplied access targets lack modeled entry-and-return access
            </div>
          )}
          <div className="evidence-summary">
            <span>
              Boundary: {partition.evidence.boundary_mode === 'administrative_polygon' ? 'administrative polygon' : 'bounding-box fallback'}
            </span>
            <span>
              Traffic: {partition.evidence.traffic_mode === 'measured_volume'
                ? `${partition.evidence.traffic_observation_count} counts · ${partition.evidence.measured_edge_coverage_percent.toFixed(1)}% road-length coverage`
                : 'topology model'}
            </span>
            <span>
              Access: {partition.evidence.access_target_count
                ? `${partition.evidence.access_target_count} supplied targets`
                : 'not supplied'}
            </span>
          </div>
        </div>
      )}

      {partition && !isLoading && streetActions.length > 0 && (
        <section className="street-action-plan" aria-labelledby="street-action-plan-title">
          <div className="action-plan-header">
            <div>
              <span className="action-plan-kicker">Proposed works schedule</span>
              <h3 id="street-action-plan-title">Street-by-street actions</h3>
            </div>
            <span className="action-plan-count">{streetActions.length}</span>
          </div>
          <p className="action-plan-intro">
            Model-generated instructions use the same sign and color as the map. They remain blocked from implementation until the readiness gate passes.
          </p>
          <ol className="street-action-list">
            {streetActions.slice(0, visibleActionCount).map(({ modification, superblockIndex, key }) => {
              const presentation = actionPresentation(modification);
              return (
                <li key={key} className={`street-action-item ${actionClass(modification)}`}>
                  <span className={`action-sign ${actionClass(modification)}`} aria-hidden="true">
                    {presentation.symbol}
                  </span>
                  <div className="street-action-copy">
                    <div className="street-action-heading">
                      <strong>{presentation.label}</strong>
                      <span>SB {superblockIndex + 1}</span>
                    </div>
                    <p>{actionInstruction(modification)}</p>
                    <small>{presentation.purpose}</small>
                  </div>
                </li>
              );
            })}
          </ol>
          {visibleActionCount < streetActions.length && (
            <button
              type="button"
              className="load-more-actions"
              onClick={() => setVisibleActionCount(count => Math.min(count + 50, streetActions.length))}
            >
              Show 50 more
              <span>{streetActions.length - visibleActionCount} remaining</span>
            </button>
          )}
        </section>
      )}

      {/* Display Options */}
      {partition && (
        <div className="display-options">
          <div className="option-title">Map display</div>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={showEntryPoints}
              onChange={(e) => onShowEntryPointsChange(e.target.checked)}
            />
            <span>Entry and return directions</span>
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={showModalFilters}
              onChange={(e) => onShowModalFiltersChange(e.target.checked)}
            />
            <span>Street access modifications</span>
          </label>
        </div>
      )}

      {/* Settings Toggle Header */}
      <button
        type="button"
        className="settings-toggle"
        onClick={() => setSettingsExpanded(!settingsExpanded)}
        aria-expanded={settingsExpanded}
        aria-controls="partition-settings-panel"
      >
        <span className="settings-icon">{settingsExpanded ? '▼' : '▶'}</span>
        <span>Advanced sizing</span>
      </button>

      {/* Expandable Settings */}
      {settingsExpanded && (
        <div className="settings-panel" id="partition-settings-panel">
          <div className="planning-mode" role="radiogroup" aria-label="Planning mode">
            <button
              type="button"
              role="radio"
              aria-checked={!sectorMode}
              className={!sectorMode ? 'active' : ''}
              onClick={() => handlePlanningModeChange('superblocks')}
            >
              Superblocks
              <small>Barcelona · 9-25 ha cells</small>
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={sectorMode}
              className={sectorMode ? 'active' : ''}
              onClick={() => handlePlanningModeChange('circulation_sectors')}
            >
              Circulation sectors
              <small>Ghent · main roads only</small>
            </button>
          </div>

          {/* Target Size */}
          <div className="param-group">
            <label className="param-label" htmlFor="partition-target-size">
              <span>Target size</span>
              <span className="param-value">{parameters.targetSizeHectares} ha</span>
            </label>
            <input
              type="range"
              id="partition-target-size"
              min={ranges.target[0]}
              max={ranges.target[1]}
              step={ranges.target[2]}
              value={parameters.targetSizeHectares}
              onChange={(e) => handleTargetSizeChange(Number(e.target.value))}
              className="param-slider"
            />
            <span className="param-hint">
              {sectorMode ? 'Ghent sectors: 100-300 ha, crossed only via the main road grid' : 'Barcelona standard: 9-16 ha'}
            </span>
          </div>

          {/* Min Area */}
          <div className="param-group">
            <label className="param-label" htmlFor="partition-min-area">
              <span>Min area</span>
              <span className="param-value">{parameters.minAreaHectares} ha</span>
            </label>
            <input
              type="range"
              id="partition-min-area"
              min={ranges.min[0]}
              max={ranges.min[1]}
              step={ranges.min[2]}
              value={parameters.minAreaHectares}
              onChange={(e) => handleMinAreaChange(Number(e.target.value))}
              className="param-slider"
            />
          </div>

          {/* Max Area */}
          <div className="param-group">
            <label className="param-label" htmlFor="partition-max-area">
              <span>Max area</span>
              <span className="param-value">{parameters.maxAreaHectares} ha</span>
            </label>
            <input
              type="range"
              id="partition-max-area"
              min={ranges.max[0]}
              max={ranges.max[1]}
              step={ranges.max[2]}
              value={parameters.maxAreaHectares}
              onChange={(e) => handleMaxAreaChange(Number(e.target.value))}
              className="param-slider"
            />
          </div>

          <label className="checkbox-label" htmlFor="partition-keep-residential">
            <input
              type="checkbox"
              id="partition-keep-residential"
              checked={parameters.keepResidentialInside ?? true}
              onChange={(e) => onParametersChange({ ...parameters, keepResidentialInside: e.target.checked })}
            />
            <span>Keep busy residential streets inside superblocks</span>
          </label>
          <span className="param-hint">
            Busy residential streets are filtered instead of becoming boundary roads; quiet ones may split large cells.
          </span>

          <span className="settings-note">
            Directional sectors and cross-traffic blocking are always enforced.
          </span>
        </div>
      )}
    </div>
  );
}
