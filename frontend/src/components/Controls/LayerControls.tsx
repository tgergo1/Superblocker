import { useState } from 'react';
import type { DetailedProgress, AnalysisParameters } from '../../hooks/useSuperblocks';
import './LayerControls.css';

export interface AnalysisProgress {
  stage: 'idle' | 'network' | 'centrality' | 'detection' | 'scoring' | 'reorientation' | 'complete';
  percent: number;
  message: string;
}

// Helper function to format time in mm:ss
function formatTime(seconds: number): string {
  if (seconds <= 0) return '0:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

interface LayerControlsProps {
  colorBy: 'hierarchy' | 'traffic' | 'interventions';
  onColorByChange: (value: 'hierarchy' | 'traffic' | 'interventions') => void;
  isLoadingNetwork?: boolean;
  isLoadingSuperblocks?: boolean;
  analysisProgress?: DetailedProgress;
  onFetchNetwork?: () => void;
  onFindSuperblocks?: () => void;
  onCancelAnalysis?: () => void;
  canFetch?: boolean;
  hasNetwork?: boolean;
  superblockCount?: number;
  showSuperblocks?: boolean;
  onToggleSuperblocks?: (show: boolean) => void;
  // New props for parameters
  analysisParameters?: AnalysisParameters;
  onParametersChange?: (params: AnalysisParameters) => void;
  // Impact metrics
  impactMetrics?: {
    trafficReduction: number;
    co2ReductionKgPerHour: number;
    pedestrianArea: number;
    totalArea: number;
  };
  networkError?: Error | null;
  analysisError?: Error | null;
}

export function LayerControls({
  colorBy,
  onColorByChange,
  isLoadingNetwork,
  isLoadingSuperblocks,
  analysisProgress,
  onFetchNetwork,
  onFindSuperblocks,
  onCancelAnalysis,
  canFetch,
  hasNetwork,
  superblockCount,
  showSuperblocks,
  onToggleSuperblocks,
  analysisParameters,
  onParametersChange,
  impactMetrics,
  networkError,
  analysisError,
}: LayerControlsProps) {
  const [settingsExpanded, setSettingsExpanded] = useState(false);
  
  // Local state for slider values
  const [localMinArea, setLocalMinArea] = useState(analysisParameters?.minAreaHectares ?? 4);
  const [localMaxArea, setLocalMaxArea] = useState(analysisParameters?.maxAreaHectares ?? 25);

  // Default parameters for safe spreading
  const defaultParams = { minAreaHectares: 4, maxAreaHectares: 25 };

  const handleMinAreaChange = (value: number) => {
    setLocalMinArea(value);
    if (onParametersChange) {
      const params = analysisParameters ?? defaultParams;
      onParametersChange({
        ...params,
        minAreaHectares: value,
        maxAreaHectares: Math.max(value + 1, localMaxArea),
      });
    }
  };

  const handleMaxAreaChange = (value: number) => {
    setLocalMaxArea(value);
    if (onParametersChange) {
      const params = analysisParameters ?? defaultParams;
      onParametersChange({
        ...params,
        minAreaHectares: Math.min(localMinArea, value - 1),
        maxAreaHectares: value,
      });
    }
  };

  return (
    <div className="layer-controls">
      {/* Settings Toggle Header */}
      <button
        type="button"
        className="settings-toggle"
        onClick={() => setSettingsExpanded(!settingsExpanded)}
        aria-expanded={settingsExpanded}
        aria-controls="analysis-settings-panel"
      >
        <span>⚙️ Analysis Settings</span>
        <span className={`chevron ${settingsExpanded ? 'expanded' : ''}`}>▼</span>
      </button>

      {/* Collapsible Settings Panel */}
      {settingsExpanded && (
        <div className="settings-panel" id="analysis-settings-panel">
          {/* Superblock Size Sliders */}
          <div className="control-section">
            <div className="control-group">
              <label className="control-label" htmlFor="analysis-min-area">Minimum Area (hectares)</label>
              <div className="slider-container">
                <input
                  type="range"
                  id="analysis-min-area"
                  min="1"
                  max="20"
                  step="0.5"
                  value={localMinArea}
                  onChange={(e) => handleMinAreaChange(Number(e.target.value))}
                  className="slider"
                  disabled={isLoadingSuperblocks}
                />
                <span className="slider-value">{localMinArea} ha</span>
              </div>
              <div className="slider-hint">Barcelona-style: 4-9 ha minimum</div>
            </div>
          </div>

          <div className="control-section">
            <div className="control-group">
              <label className="control-label" htmlFor="analysis-max-area">Maximum Area (hectares)</label>
              <div className="slider-container">
                <input
                  type="range"
                  id="analysis-max-area"
                  min="5"
                  max="50"
                  step="1"
                  value={localMaxArea}
                  onChange={(e) => handleMaxAreaChange(Number(e.target.value))}
                  className="slider"
                  disabled={isLoadingSuperblocks}
                />
                <span className="slider-value">{localMaxArea} ha</span>
              </div>
              <div className="slider-hint">Larger = more candidates</div>
            </div>
          </div>
        </div>
      )}

      <div className="control-section">
        <div className="control-group">
          <label className="control-label">Color roads by</label>
          <div className="button-group" role="group" aria-label="Road color mode">
            <button
              type="button"
              className={`toggle-button ${colorBy === 'hierarchy' ? 'active' : ''}`}
              onClick={() => onColorByChange('hierarchy')}
              aria-pressed={colorBy === 'hierarchy'}
            >
              Road Type
            </button>
            <button
              type="button"
              className={`toggle-button ${colorBy === 'traffic' ? 'active' : ''}`}
              onClick={() => onColorByChange('traffic')}
              aria-pressed={colorBy === 'traffic'}
            >
              Traffic
            </button>
            <button
              type="button"
              className={`toggle-button ${colorBy === 'interventions' ? 'active' : ''}`}
              onClick={() => onColorByChange('interventions')}
              disabled={!superblockCount}
              title="Shows planned interventions for selected superblock"
              aria-pressed={colorBy === 'interventions'}
            >
              Changes
            </button>
          </div>
        </div>
      </div>

      <div className="control-section">
        <div className="control-group">
          <label className="control-label">Actions</label>
          {onFetchNetwork && (
            <button
              type="button"
              className="action-button primary"
              onClick={onFetchNetwork}
              disabled={!canFetch || isLoadingNetwork}
            >
              {isLoadingNetwork ? 'Loading...' : 'Load Streets'}
            </button>
          )}
          {onFindSuperblocks && (
            isLoadingSuperblocks ? (
              <button type="button" className="action-button cancel" onClick={onCancelAnalysis}>
                Cancel Analysis
              </button>
            ) : (
              <button
                type="button"
                className="action-button success"
                onClick={onFindSuperblocks}
                disabled={!hasNetwork}
              >
                Find Superblocks
              </button>
            )
          )}
        </div>

        {(networkError || analysisError) && (
          <div className="control-error" role="alert">
            {analysisError?.message || networkError?.message || 'The operation failed.'}
          </div>
        )}

        {/* Enhanced Progress indicator */}
        {isLoadingSuperblocks && analysisProgress && (
          <div className="progress-section enhanced">
            {/* Step indicator */}
            <div className="progress-steps">
              <span className="step-current">
                Step {analysisProgress.stageInfo.stepNumber}/{analysisProgress.stageInfo.totalSteps}
              </span>
              <span className="step-name">{analysisProgress.stageInfo.stepName}</span>
            </div>

            {/* Progress bar */}
            <div className="progress-bar">
              <div
                className="progress-fill"
                style={{ width: `${analysisProgress.percent}%` }}
              />
              <span className="progress-percent">{analysisProgress.percent}%</span>
            </div>

            {/* Time information */}
            <div className="progress-times">
              <span className="time-elapsed">
                ⏱️ {formatTime(analysisProgress.elapsedTime)}
              </span>
              <span className="time-remaining">
                ~{formatTime(analysisProgress.estimatedRemainingTime)} remaining
              </span>
            </div>

            {/* Step description */}
            <div className="progress-description">
              {analysisProgress.stageInfo.stepDescription}
            </div>
          </div>
        )}
      </div>

      {/* Superblock results */}
      {superblockCount !== undefined && superblockCount > 0 && (
        <div className="control-section">
          <div className="control-group">
            <label className="control-label">
              Superblocks ({superblockCount} found)
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={showSuperblocks}
                onChange={(e) => onToggleSuperblocks?.(e.target.checked)}
              />
              Show on map
            </label>
          </div>
        </div>
      )}

      {superblockCount === 0 && !isLoadingSuperblocks && (
        <div className="empty-result" role="status">
          No candidates matched the current area and size settings.
        </div>
      )}

      {/* Impact Metrics Panel */}
      {impactMetrics && superblockCount && superblockCount > 0 && (
        <div className="control-section impact-section">
          <div className="control-group">
            <label className="control-label">Estimated Impact</label>
            <div className="impact-metrics">
              <div className="impact-metric">
                <span className="impact-icon">🚗</span>
                <div className="impact-data">
                  <span className="impact-value">-{impactMetrics.trafficReduction}%</span>
                  <span className="impact-label">Through Traffic</span>
                </div>
              </div>
              <div className="impact-metric">
                <span className="impact-icon">🌿</span>
                <div className="impact-data">
                  <span className="impact-value">~{impactMetrics.co2ReductionKgPerHour} kg/h</span>
                  <span className="impact-label">CO₂ Avoided</span>
                </div>
              </div>
              <div className="impact-metric">
                <span className="impact-icon">🚶</span>
                <div className="impact-data">
                  <span className="impact-value">+{impactMetrics.pedestrianArea} ha</span>
                  <span className="impact-label">Recoverable Street Space</span>
                </div>
              </div>
              <div className="impact-metric">
                <span className="impact-icon">📐</span>
                <div className="impact-data">
                  <span className="impact-value">{impactMetrics.totalArea} ha</span>
                  <span className="impact-label">Total Coverage</span>
                </div>
              </div>
            </div>
            <small>Approximate values based on rerouted interior traffic and mapped street geometry.</small>
          </div>
        </div>
      )}
    </div>
  );
}
