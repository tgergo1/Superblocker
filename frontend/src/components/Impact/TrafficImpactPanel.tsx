import { useMemo, useState } from 'react';
import type { BoundaryRoadImpact, TrafficImpactEstimate } from '../../types';
import { impactPercent } from '../../utils/impact';
import './TrafficImpactPanel.css';

interface TrafficImpactPanelProps {
  impact: TrafficImpactEstimate;
  showOnMap: boolean;
  onShowOnMapChange: (value: boolean) => void;
  scenario: string;
  onScenarioChange: (value: string) => void;
}

const SCENARIO_LABELS: Record<string, string> = {
  pessimistic: 'No evaporation',
  central: 'Central',
  optimistic: 'London-like',
};

function formatCount(value: number): string {
  return Math.round(value).toLocaleString();
}

export function TrafficImpactPanel({
  impact,
  showOnMap,
  onShowOnMapChange,
  scenario,
  onScenarioChange,
}: TrafficImpactPanelProps) {
  const [expanded, setExpanded] = useState(true);
  const [showMinor, setShowMinor] = useState(false);

  const ranked = useMemo(() => {
    // OSM splits long streets into many ways; show each named street once,
    // at its most affected section.
    const byStreet = new Map<string, { road: BoundaryRoadImpact; percent: number }>();
    impact.boundary_roads
      .filter(road => showMinor || road.arterial)
      .forEach(road => {
        const percent = impactPercent(road, scenario);
        const key = road.name ? `${road.name}|${road.highway}` : `osm:${road.osm_id}`;
        const existing = byStreet.get(key);
        if (!existing || percent > existing.percent) byStreet.set(key, { road, percent });
      });
    return [...byStreet.values()].sort((a, b) => b.percent - a.percent).slice(0, 8);
  }, [impact.boundary_roads, scenario, showMinor]);

  const roadsOverThreshold = impact.boundary_roads.filter(
    road => impactPercent(road, scenario) >= impact.settings.high_increase_percent,
  ).length;
  const roadsOverCapacity = impact.boundary_roads.filter(
    road => road.baseline_peak_vph + (road.added_peak_vph[scenario] ?? 0) > road.capacity_vph,
  ).length;
  // Superblocks with no cut-through traffic count as a 0% cut, not missing data.
  const reductions = impact.superblocks
    .map(item => item.interior_traffic_reduction_percent)
    .sort((a, b) => a - b);
  const medianReduction = reductions.length ? reductions[Math.floor(reductions.length / 2)] : 0;
  const extraVehicleKm = impact.added_boundary_vehicle_km_peak[scenario] ?? 0;
  const selected = impact.settings.scenarios.find(item => item.name === scenario);

  return (
    <section className="traffic-impact-panel" aria-label="Traffic impact">
      <button
        type="button"
        className="impact-toggle"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span>
          <strong>Where does the traffic go?</strong>
          <small>
            {formatCount(impact.total_displaced_peak_vph)} veh/h leave superblock interiors at peak
          </small>
        </span>
        <span aria-hidden="true">{expanded ? '−' : '+'}</span>
      </button>

      {expanded && (
        <div className="impact-body">
          <label className="impact-map-toggle">
            <input
              type="checkbox"
              checked={showOnMap}
              onChange={event => onShowOnMapChange(event.target.checked)}
            />
            Color boundary roads by predicted increase
          </label>

          <div className="impact-scenarios" role="radiogroup" aria-label="Evaporation scenario">
            {impact.settings.scenarios.map(item => (
              <button
                key={item.name}
                type="button"
                role="radio"
                aria-checked={scenario === item.name}
                className={scenario === item.name ? 'active' : ''}
                onClick={() => onScenarioChange(item.name)}
                title={item.description}
              >
                {SCENARIO_LABELS[item.name] ?? item.name}
                <small>{Math.round(item.evaporation_rate * 100)}% vanish</small>
              </button>
            ))}
          </div>
          {selected?.description && <p className="impact-scenario-note">{selected.description}</p>}

          <dl className="impact-stats">
            <div>
              <dt>Median interior cut</dt>
              <dd>{medianReduction.toFixed(0)}%</dd>
            </div>
            <div>
              <dt>Detour driving at peak</dt>
              <dd>{formatCount(extraVehicleKm)} veh-km</dd>
            </div>
            <div>
              <dt>Roads over +{impact.settings.high_increase_percent}%</dt>
              <dd>{roadsOverThreshold}</dd>
            </div>
            <div>
              <dt>Over capacity</dt>
              <dd>{roadsOverCapacity}</dd>
            </div>
          </dl>

          <div className="impact-list-header">
            <h3>Most affected {showMinor ? 'roads' : 'main roads'}</h3>
            <label>
              <input
                type="checkbox"
                checked={showMinor}
                onChange={event => setShowMinor(event.target.checked)}
              />
              include minor streets
            </label>
          </div>
          <ol className="impact-road-list">
            {ranked.map(({ road, percent }) => (
              <li key={road.osm_id}>
                <span className="impact-road-name">
                  {road.name ?? `Road ${road.osm_id}`}
                  <small>{road.highway.replace('_', ' ')}{road.measured ? ' · counted' : ''}</small>
                </span>
                <span className={`impact-road-value ${percent >= 25 ? 'high' : percent >= 10 ? 'medium' : ''}`}>
                  +{percent.toFixed(0)}%
                </span>
              </li>
            ))}
            {ranked.length === 0 && <li className="impact-empty">No diverted traffic on these roads.</li>}
          </ol>

          {impact.minor_roads_loaded > 0 && (
            <p className="impact-warning">
              {impact.minor_roads_loaded} minor streets would carry diverted traffic. Consider merging
              those superblocks so their edges are main roads.
            </p>
          )}

          <details className="impact-method">
            <summary>
              How this is estimated ({impact.traffic_basis === 'modeled_topology' ? 'modeled volumes' : 'uses counts'})
            </summary>
            <ul>
              {impact.limitations.map(item => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </section>
  );
}
