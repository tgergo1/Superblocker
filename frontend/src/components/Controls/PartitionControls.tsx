import { useState } from 'react';
import type { PartitionProgress, CityPartition } from '../../types';
import './PartitionControls.css';

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
  numSectors: number;
  enforceConstraints: boolean;
}

interface PartitionControlsProps {
  isLoading: boolean;
  progress?: PartitionProgress;
  parameters: PartitionParameters;
  onParametersChange: (params: PartitionParameters) => void;
  onPartition: () => void;
  canPartition: boolean;
  partition?: CityPartition | null;
  // Display options
  showEntryPoints: boolean;
  onShowEntryPointsChange: (show: boolean) => void;
  showModalFilters: boolean;
  onShowModalFiltersChange: (show: boolean) => void;
}

export function PartitionControls({
  isLoading,
  progress,
  parameters,
  onParametersChange,
  onPartition,
  canPartition,
  partition,
  showEntryPoints,
  onShowEntryPointsChange,
  showModalFilters,
  onShowModalFiltersChange,
}: PartitionControlsProps) {
  const [settingsExpanded, setSettingsExpanded] = useState(false);
  const [startTime] = useState<number | null>(null);

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

  const handleNumSectorsChange = (value: number) => {
    onParametersChange({
      ...parameters,
      numSectors: value,
    });
  };

  const handleEnforceConstraintsChange = (checked: boolean) => {
    onParametersChange({
      ...parameters,
      enforceConstraints: checked,
    });
  };

  // Calculate elapsed time if loading
  const elapsedSeconds = startTime ? Math.floor((Date.now() - startTime) / 1000) : 0;

  return (
    <div className="partition-controls">
      {/* Settings Toggle Header */}
      <button
        className="settings-toggle"
        onClick={() => setSettingsExpanded(!settingsExpanded)}
      >
        <span className="settings-icon">{settingsExpanded ? '▼' : '▶'}</span>
        <span>Partition Settings</span>
      </button>

      {/* Expandable Settings */}
      {settingsExpanded && (
        <div className="settings-panel">
          {/* Target Size */}
          <div className="param-group">
            <label className="param-label">
              <span>Target size</span>
              <span className="param-value">{parameters.targetSizeHectares} ha</span>
            </label>
            <input
              type="range"
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
            <label className="param-label">
              <span>Min area</span>
              <span className="param-value">{parameters.minAreaHectares} ha</span>
            </label>
            <input
              type="range"
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
            <label className="param-label">
              <span>Max area</span>
              <span className="param-value">{parameters.maxAreaHectares} ha</span>
            </label>
            <input
              type="range"
              min={10}
              max={40}
              step={1}
              value={parameters.maxAreaHectares}
              onChange={(e) => handleMaxAreaChange(Number(e.target.value))}
              className="param-slider"
            />
          </div>

          {/* Number of Sectors */}
          <div className="param-group">
            <label className="param-label">
              <span>Sectors</span>
              <span className="param-value">{parameters.numSectors}</span>
            </label>
            <input
              type="range"
              min={3}
              max={8}
              step={1}
              value={parameters.numSectors}
              onChange={(e) => handleNumSectorsChange(Number(e.target.value))}
              className="param-slider"
            />
            <span className="param-hint">Angular divisions per superblock</span>
          </div>

          {/* Enforce Constraints Checkbox */}
          <div className="param-group checkbox-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={parameters.enforceConstraints}
                onChange={(e) => handleEnforceConstraintsChange(e.target.checked)}
              />
              <span>Enforce enter-exit constraints</span>
            </label>
            <span className="param-hint">
              Block cross-sector through traffic
            </span>
          </div>
        </div>
      )}

      {/* Display Options */}
      {partition && (
        <div className="display-options">
          <div className="option-title">Display</div>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={showEntryPoints}
              onChange={(e) => onShowEntryPointsChange(e.target.checked)}
            />
            <span>Entry points</span>
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={showModalFilters}
              onChange={(e) => onShowModalFiltersChange(e.target.checked)}
            />
            <span>Modal filters</span>
          </label>
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
            />
          </div>
          <div className="progress-info">
            <span className="progress-message">{progress.message}</span>
            <span className="progress-time">
              {formatTime(elapsedSeconds)}
            </span>
          </div>
        </div>
      )}

      {/* Action Button */}
      <button
        className={`action-button partition-button ${isLoading ? 'loading' : ''}`}
        onClick={onPartition}
        disabled={!canPartition || isLoading}
      >
        {isLoading ? (
          <>
            <span className="spinner" />
            Partitioning...
          </>
        ) : (
          'Partition City'
        )}
      </button>

      {/* Results Summary */}
      {partition && !isLoading && (
        <div className="results-summary">
          <div className="summary-title">Results</div>
          <div className="summary-grid">
            <div className="summary-item">
              <span className="summary-value">{partition.total_superblocks}</span>
              <span className="summary-label">Superblocks</span>
            </div>
            <div className="summary-item">
              <span className="summary-value">{partition.coverage_percent.toFixed(0)}%</span>
              <span className="summary-label">Coverage</span>
            </div>
            <div className="summary-item">
              <span className="summary-value">{partition.total_modal_filters}</span>
              <span className="summary-label">Filters</span>
            </div>
            <div className="summary-item">
              <span className="summary-value">{partition.total_one_way_conversions}</span>
              <span className="summary-label">One-ways</span>
            </div>
          </div>
          {partition.total_unreachable_addresses > 0 && (
            <div className="warning-banner">
              {partition.total_unreachable_addresses} addresses flagged for review
            </div>
          )}
        </div>
      )}
    </div>
  );
}
