import { useState } from 'react';
import type {
  CapacitySimulationResult,
  SimulationLevelResult,
  SimulationMapView,
  SimulationProgress,
} from '../../types/simulation';
import { formatPercentChange } from '../../utils/simulation';
import './CapacitySimulatorPanel.css';

interface CapacitySimulatorPanelProps {
  result: CapacitySimulationResult | null;
  isRunning: boolean;
  progress: SimulationProgress | null;
  error: string | null;
  onRun: (peakHourTrips: number | null) => void;
  onCancel: () => void;
  showOnMap: boolean;
  onShowOnMapChange: (value: boolean) => void;
  mapView: SimulationMapView;
  onMapViewChange: (value: SimulationMapView) => void;
}

const WITHOUT_COLOR = '#475569';
const WITH_COLOR = '#c2410c';

function atToday(levels: SimulationLevelResult[]): SimulationLevelResult | undefined {
  return levels.find(level => Math.abs(level.demand_multiplier - 1) < 1e-6);
}

function formatMultiplier(value: number | null, reached: boolean, maxLevel: number): string {
  if (value === null || !reached) return `over ${maxLevel.toFixed(1)}×`;
  return `${value.toFixed(2)}×`;
}

function CapacityChart({ result }: { result: CapacitySimulationResult }) {
  const width = 300;
  const height = 170;
  const pad = { left: 34, right: 10, top: 10, bottom: 30 };
  const threshold = result.settings.breakdown_travel_time_index;
  const all = [...result.baseline.levels, ...result.planned.levels];
  const maxX = Math.max(...all.map(level => level.demand_multiplier));
  const maxY = Math.min(
    Math.max(threshold * 1.6, ...all.map(level => level.travel_time_index)) * 1.05,
    threshold * 3,
  );
  const minY = 1;
  const x = (value: number) => pad.left + (value / maxX) * (width - pad.left - pad.right);
  const y = (value: number) =>
    pad.top + (1 - (Math.min(value, maxY) - minY) / (maxY - minY)) * (height - pad.top - pad.bottom);
  const path = (levels: SimulationLevelResult[]) =>
    levels
      .map((level, index) => `${index ? 'L' : 'M'}${x(level.demand_multiplier).toFixed(1)},${y(level.travel_time_index).toFixed(1)}`)
      .join(' ');
  const xTicks = Array.from({ length: Math.floor(maxX) + 1 }, (_, index) => index).filter(tick => tick > 0);
  const yTicks = [1, threshold, Math.round(maxY * 2) / 2].filter(
    (tick, index, ticks) => ticks.indexOf(tick) === index && tick <= maxY,
  );

  return (
    <svg
      className="capacity-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Average trip time against demand, with and without superblocks"
    >
      {yTicks.map(tick => (
        <g key={`y${tick}`}>
          <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} className="chart-grid" />
          <text x={pad.left - 4} y={y(tick) + 3} className="chart-label" textAnchor="end">
            {tick.toFixed(1)}×
          </text>
        </g>
      ))}
      <line
        x1={pad.left}
        x2={width - pad.right}
        y1={y(threshold)}
        y2={y(threshold)}
        className="chart-threshold"
      />
      <text x={width - pad.right} y={y(threshold) - 4} className="chart-label threshold" textAnchor="end">
        jammed
      </text>
      {xTicks.map(tick => (
        <text key={`x${tick}`} x={x(tick)} y={height - pad.bottom + 13} className="chart-label" textAnchor="middle">
          {tick}×
        </text>
      ))}
      <text x={(pad.left + width - pad.right) / 2} y={height - 4} className="chart-axis" textAnchor="middle">
        car demand (1× = today's peak hour)
      </text>
      <path d={path(result.baseline.levels)} stroke={WITHOUT_COLOR} className="chart-line" />
      <path d={path(result.planned.levels)} stroke={WITH_COLOR} className="chart-line" />
      {[result.baseline, result.planned].map(scenario => (
        <g key={scenario.name}>
          {scenario.levels.map(level => (
            <circle
              key={level.demand_multiplier}
              cx={x(level.demand_multiplier)}
              cy={y(level.travel_time_index)}
              r={2.5}
              fill={scenario.name === 'with_superblocks' ? WITH_COLOR : WITHOUT_COLOR}
            >
              <title>
                {`${scenario.name === 'with_superblocks' ? 'With' : 'Without'} superblocks at ${level.demand_multiplier}×: `}
                {`${level.mean_trip_minutes.toFixed(1)} min average trip`}
              </title>
            </circle>
          ))}
          {scenario.capacity_reached && scenario.capacity_multiplier !== null && (
            <circle
              cx={x(scenario.capacity_multiplier)}
              cy={y(threshold)}
              r={4.5}
              className="chart-capacity"
              stroke={scenario.name === 'with_superblocks' ? WITH_COLOR : WITHOUT_COLOR}
            />
          )}
        </g>
      ))}
    </svg>
  );
}

export function CapacitySimulatorPanel({
  result,
  isRunning,
  progress,
  error,
  onRun,
  onCancel,
  showOnMap,
  onShowOnMapChange,
  mapView,
  onMapViewChange,
}: CapacitySimulatorPanelProps) {
  const [expanded, setExpanded] = useState(true);
  const [tripsInput, setTripsInput] = useState('');

  const parsedTrips = Number(tripsInput.replace(/[\s,]/g, ''));
  const peakHourTrips = tripsInput.trim() && Number.isFinite(parsedTrips) && parsedTrips > 0 ? parsedTrips : null;
  const baseToday = result ? atToday(result.baseline.levels) : undefined;
  const planToday = result ? atToday(result.planned.levels) : undefined;
  const maxLevel = result ? Math.max(...result.settings.demand_levels) : 0;

  const summary = result
    ? result.capacity_change_percent !== null
      ? `Capacity ${formatPercentChange(result.capacity_change_percent)} with superblocks`
      : 'Neither network jammed in the tested range'
    : 'Simulate traffic with and without the plan';

  return (
    <section className="capacity-panel" aria-label="Traffic simulation">
      <button
        type="button"
        className="capacity-toggle"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span>
          <strong>Can the city still move?</strong>
          <small>{summary}</small>
        </span>
        <span aria-hidden="true">{expanded ? '−' : '+'}</span>
      </button>

      {expanded && (
        <div className="capacity-body">
          {!result && !isRunning && (
            <p className="capacity-intro">
              Simulates peak-hour car trips across the whole area on today&apos;s streets and on the
              planned ones, then raises demand until each network jams.
            </p>
          )}

          {isRunning ? (
            <div className="capacity-progress" role="status">
              <div className="capacity-progress-bar">
                <span style={{ width: `${progress?.percent ?? 0}%` }} />
              </div>
              <span>{progress?.message ?? 'Starting the simulation'}</span>
              <button type="button" className="capacity-secondary" onClick={onCancel}>
                Cancel
              </button>
            </div>
          ) : (
            <div className="capacity-run">
              <label>
                Peak-hour car trips
                <input
                  type="text"
                  inputMode="numeric"
                  placeholder={result ? Math.round(result.base_peak_hour_trips).toLocaleString() : 'estimate'}
                  value={tripsInput}
                  onChange={event => setTripsInput(event.target.value)}
                />
              </label>
              <button type="button" className="capacity-primary" onClick={() => onRun(peakHourTrips)}>
                {result ? 'Run again' : 'Run traffic simulation'}
              </button>
            </div>
          )}
          {error && <p className="capacity-error">{error}</p>}

          {result && baseToday && planToday && (
            <>
              <dl className="capacity-stats">
                <div>
                  <dt>Jams at</dt>
                  <dd>
                    <span style={{ color: WITHOUT_COLOR }}>
                      {formatMultiplier(result.baseline.capacity_multiplier, result.baseline.capacity_reached, maxLevel)}
                    </span>
                    {' → '}
                    <span style={{ color: WITH_COLOR }}>
                      {formatMultiplier(result.planned.capacity_multiplier, result.planned.capacity_reached, maxLevel)}
                    </span>
                  </dd>
                  <small>today&apos;s peak demand</small>
                </div>
                <div>
                  <dt>Average car trip</dt>
                  <dd>
                    {baseToday.mean_trip_minutes.toFixed(1)} → {planToday.mean_trip_minutes.toFixed(1)} min
                  </dd>
                  <small>{formatPercentChange(result.mean_trip_minutes_change_percent)} at today&apos;s peak</small>
                </div>
                <div>
                  <dt>Driving distance</dt>
                  <dd>{formatPercentChange(result.vehicle_km_change_percent)}</dd>
                  <small>{Math.round(baseToday.vehicle_km).toLocaleString()} veh-km/h today</small>
                </div>
                <div>
                  <dt>Congested roads</dt>
                  <dd>
                    {baseToday.congested_km.toFixed(1)} → {planToday.congested_km.toFixed(1)} km
                  </dd>
                  <small>over 90% full at peak</small>
                </div>
              </dl>

              <div className="capacity-chart-block">
                <div className="capacity-chart-title">Average trip time as demand grows</div>
                <CapacityChart result={result} />
                <div className="capacity-chart-legend">
                  <span><i style={{ background: WITHOUT_COLOR }} />Without superblocks</span>
                  <span><i style={{ background: WITH_COLOR }} />With superblocks</span>
                </div>
                <p className="capacity-note">
                  1× is the trip time on today&apos;s empty streets. The network counts as jammed when
                  trips take {result.settings.breakdown_travel_time_index}× as long.
                </p>
              </div>

              {result.junctions_cut_off_by_plan > 0 && (
                <p className="capacity-warning">
                  {result.junctions_cut_off_by_plan.toLocaleString()} junctions lose all car access in the
                  plan because every street section around them is filtered. Trips start and end only
                  where both networks reach.
                </p>
              )}
              {planToday.blocked_trips > 0 && (
                <p className="capacity-error">
                  {Math.round(planToday.blocked_trips).toLocaleString()} trips have no legal route in the plan.
                </p>
              )}

              <label className="capacity-map-toggle">
                <input
                  type="checkbox"
                  checked={showOnMap}
                  onChange={event => onShowOnMapChange(event.target.checked)}
                />
                Show simulated traffic on the map
              </label>
              {showOnMap && (
                <div className="capacity-views" role="radiogroup" aria-label="Map view">
                  {([
                    ['change', 'Change'],
                    ['without', 'Today'],
                    ['with', 'With plan'],
                  ] as [SimulationMapView, string][]).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={mapView === value}
                      className={mapView === value ? 'active' : ''}
                      onClick={() => onMapViewChange(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}

              <details className="capacity-method">
                <summary>How this is simulated</summary>
                <p>
                  {Math.round(result.base_peak_hour_trips).toLocaleString()} peak-hour car trips
                  {result.demand_calibrated ? ' (estimated)' : ''} between {result.zone_count} zones,
                  assigned to user equilibrium on {result.link_count.toLocaleString()} street links;
                  the plan closes {result.links_closed_by_plan.toLocaleString()} of them to through
                  traffic. Took {Math.round(result.processing_time_seconds)} s.
                </p>
                <ul>
                  {result.limitations.map(item => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </details>
            </>
          )}
        </div>
      )}
    </section>
  );
}
