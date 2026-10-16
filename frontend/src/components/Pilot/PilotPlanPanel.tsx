import { useState } from 'react';
import type { PilotCandidate, PilotPlanSummary } from '../../types';
import '../Impact/TrafficImpactPanel.css';
import './PilotPlanPanel.css';

interface PilotPlanPanelProps {
  plan: PilotPlanSummary;
  showOnMap: boolean;
  onShowOnMapChange: (value: boolean) => void;
  onSelect: (candidate: PilotCandidate) => void;
}

const euro = new Intl.NumberFormat('en-GB', {
  style: 'currency',
  currency: 'EUR',
  maximumFractionDigits: 0,
});

function formatEuro(value: number): string {
  if (value >= 1_000_000) return `€${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `€${Math.round(value / 1_000)}k`;
  return euro.format(value);
}

export function PilotPlanPanel({ plan, showOnMap, onShowOnMapChange, onSelect }: PilotPlanPanelProps) {
  const [expanded, setExpanded] = useState(true);
  const top = plan.candidates.slice(0, 10);
  const topCost = top.reduce((sum, item) => sum + item.trial_cost_eur, 0);

  return (
    <section className="traffic-impact-panel pilot-plan-panel" aria-label="Pilot plan">
      <button
        type="button"
        className="impact-toggle"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span>
          <strong>Where to start</strong>
          <small>
            Trial the top {top.length} superblocks for {formatEuro(topCost)} with planters and removable bollards
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
            Show the ranking on the map
          </label>

          <dl className="impact-stats">
            <div>
              <dt>Whole plan, trial</dt>
              <dd>{formatEuro(plan.total_trial_cost_eur)}</dd>
            </div>
            <div>
              <dt>Whole plan, built</dt>
              <dd>{formatEuro(plan.total_build_cost_eur)}</dd>
            </div>
            <div>
              <dt>Interior injury risk</dt>
              <dd>
                −{plan.mean_injury_reduction_low_percent.toFixed(0)} to −{plan.mean_injury_reduction_high_percent.toFixed(0)}%
              </dd>
            </div>
            <div>
              <dt>Speed limit inside</dt>
              <dd>30 km/h</dd>
            </div>
          </dl>

          <ol className="pilot-list">
            {top.map(item => (
              <li key={item.superblock_id}>
                <button type="button" onClick={() => onSelect(item)}>
                  <span className="pilot-rank">{item.rank}</span>
                  <span className="pilot-text">
                    <strong>
                      {Math.round(item.displaced_peak_vph)} veh/h out · {formatEuro(item.trial_cost_eur)} trial
                    </strong>
                    <small>
                      Injury risk −{item.interior_injury_reduction_low_percent.toFixed(0)} to −
                      {item.interior_injury_reduction_high_percent.toFixed(0)}%
                      {item.school_streets > 0 ? ` · ${item.school_streets} school street${item.school_streets > 1 ? 's' : ''}` : ''}
                      {item.reclaimable_m2 >= 1000 ? ` · ${(item.reclaimable_m2 / 10_000).toFixed(1)} ha for people` : ''}
                    </small>
                  </span>
                </button>
              </li>
            ))}
          </ol>

          <details className="impact-method">
            <summary>How the ranking works</summary>
            <ul>
              {plan.limitations.map(text => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </section>
  );
}
