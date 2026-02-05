import './LayerControls.css';

export interface AnalysisProgress {
  stage: 'idle' | 'network' | 'centrality' | 'detection' | 'scoring' | 'reorientation' | 'complete';
  percent: number;
  message: string;
}

interface LayerControlsProps {
  colorBy: 'hierarchy' | 'traffic';
  onColorByChange: (value: 'hierarchy' | 'traffic') => void;
  isLoadingNetwork?: boolean;
  isLoadingSuperblocks?: boolean;
  analysisProgress?: AnalysisProgress;
  onFetchNetwork?: () => void;
  onFindSuperblocks?: () => void;
  canFetch?: boolean;
  hasNetwork?: boolean;
  superblockCount?: number;
  showSuperblocks?: boolean;
  onToggleSuperblocks?: (show: boolean) => void;
}

export function LayerControls({
  colorBy,
  onColorByChange,
  isLoadingNetwork,
  isLoadingSuperblocks,
  analysisProgress,
  onFetchNetwork,
  onFindSuperblocks,
  canFetch,
  hasNetwork,
  superblockCount,
  showSuperblocks,
  onToggleSuperblocks,
}: LayerControlsProps) {
  return (
    <div className="layer-controls">
      <div className="control-section">
        <div className="control-group">
          <label className="control-label">Color roads by</label>
          <div className="button-group">
            <button
              className={`toggle-button ${colorBy === 'hierarchy' ? 'active' : ''}`}
              onClick={() => onColorByChange('hierarchy')}
            >
              Road Type
            </button>
            <button
              className={`toggle-button ${colorBy === 'traffic' ? 'active' : ''}`}
              onClick={() => onColorByChange('traffic')}
            >
              Traffic
            </button>
          </div>
        </div>
      </div>

      <div className="control-section">
        <div className="control-group">
          <label className="control-label">Actions</label>
          {onFetchNetwork && (
            <button
              className="action-button primary"
              onClick={onFetchNetwork}
              disabled={!canFetch || isLoadingNetwork}
            >
              {isLoadingNetwork ? 'Loading...' : 'Load Streets'}
            </button>
          )}
          {onFindSuperblocks && (
            <button
              className="action-button success"
              onClick={onFindSuperblocks}
              disabled={!hasNetwork || isLoadingSuperblocks}
            >
              {isLoadingSuperblocks ? 'Analyzing...' : 'Find Superblocks'}
            </button>
          )}
        </div>

        {/* Progress indicator */}
        {isLoadingSuperblocks && analysisProgress && (
          <div className="progress-section">
            <div className="progress-bar">
              <div
                className="progress-fill"
                style={{ width: `${analysisProgress.percent}%` }}
              />
            </div>
            <div className="progress-text">{analysisProgress.message}</div>
          </div>
        )}
      </div>

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
    </div>
  );
}
