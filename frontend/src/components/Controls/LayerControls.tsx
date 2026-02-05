import './LayerControls.css';

interface LayerControlsProps {
  colorBy: 'hierarchy' | 'traffic';
  onColorByChange: (value: 'hierarchy' | 'traffic') => void;
  isLoading?: boolean;
  onFetchNetwork?: () => void;
  canFetch?: boolean;
}

export function LayerControls({
  colorBy,
  onColorByChange,
  isLoading,
  onFetchNetwork,
  canFetch,
}: LayerControlsProps) {
  return (
    <div className="layer-controls">
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

      {onFetchNetwork && (
        <div className="control-group">
          <button
            className="fetch-button"
            onClick={onFetchNetwork}
            disabled={!canFetch || isLoading}
          >
            {isLoading ? 'Loading...' : 'Load Street Network'}
          </button>
        </div>
      )}
    </div>
  );
}
