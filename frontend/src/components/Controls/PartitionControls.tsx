import { useMemo, useState } from 'react';
import type {
  PartitionProgress,
  CityPartition,
  ModificationType,
  StreetModification,
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
    purpose: 'Stops motor through-traffic; walking, cycling, and emergency access remain.',
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
}: PartitionControlsProps) {
  const [settingsExpanded, setSettingsExpanded] = useState(false);
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
    (superblock) => superblock.constraint_validated,
  ).length ?? 0;
  const allCrossTrafficBlocked = Boolean(
    partition && partition.total_superblocks > 0 && validatedSuperblocks === partition.total_superblocks,
  );
  const totalModifications = streetActions.length;

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
                  ? 'Directional cross-traffic paths blocked'
                  : 'Cross-traffic validation incomplete'}
              </strong>
              <small>
                {validatedSuperblocks}/{partition.total_superblocks} superblocks pass the directional path test
              </small>
            </span>
          </div>
          <div className="summary-title">City plan</div>
          <div className="summary-grid">
            <div className="summary-item">
              <span className="summary-value">{partition.total_superblocks}</span>
              <span className="summary-label">Superblocks</span>
            </div>
            <div className="summary-item">
              <span className="summary-value">{partition.coverage_percent.toFixed(0)}%</span>
              <span className="summary-label">Generated-cell coverage</span>
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
          </div>
          {partition.total_unreachable_addresses > 0 && (
            <div className="warning-banner">
              {partition.total_unreachable_addresses} network nodes require local-access review
            </div>
          )}
        </div>
      )}

      {partition && !isLoading && streetActions.length > 0 && (
        <section className="street-action-plan" aria-labelledby="street-action-plan-title">
          <div className="action-plan-header">
            <div>
              <span className="action-plan-kicker">Implementation schedule</span>
              <h3 id="street-action-plan-title">Street-by-street actions</h3>
            </div>
            <span className="action-plan-count">{streetActions.length}</span>
          </div>
          <p className="action-plan-intro">
            Each instruction uses the same sign and color as its marker on the map.
          </p>
          <ol className="street-action-list">
            {streetActions.slice(0, visibleActionCount).map(({ modification, superblockIndex, key }) => {
              const presentation = ACTION_PRESENTATION[modification.modification_type];
              return (
                <li key={key} className={`street-action-item ${modification.modification_type}`}>
                  <span className={`action-sign ${modification.modification_type}`} aria-hidden="true">
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
          {/* Target Size */}
          <div className="param-group">
            <label className="param-label" htmlFor="partition-target-size">
              <span>Target size</span>
              <span className="param-value">{parameters.targetSizeHectares} ha</span>
            </label>
            <input
              type="range"
              id="partition-target-size"
              min={6}
              max={25}
              step={1}
              value={parameters.targetSizeHectares}
              onChange={(e) => handleTargetSizeChange(Number(e.target.value))}
              className="param-slider"
            />
            <span className="param-hint">Barcelona standard: 9-16 ha</span>
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
              min={2}
              max={15}
              step={1}
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
              min={10}
              max={40}
              step={1}
              value={parameters.maxAreaHectares}
              onChange={(e) => handleMaxAreaChange(Number(e.target.value))}
              className="param-slider"
            />
          </div>

          <span className="settings-note">
            Directional sectors and cross-traffic blocking are always enforced.
          </span>
        </div>
      )}
    </div>
  );
}
