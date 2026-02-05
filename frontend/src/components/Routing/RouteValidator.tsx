import { useState } from 'react';
import type { Coordinates, RouteResult } from '../../types';
import { computeRoute, type RouteRequest } from '../../services/api';
import './RouteValidator.css';

interface RouteValidatorProps {
  onRouteComputed: (route: RouteResult | null) => void;
  isPartitionAvailable: boolean;
}

export function RouteValidator({
  onRouteComputed,
  isPartitionAvailable,
}: RouteValidatorProps) {
  const [origin, setOrigin] = useState<Coordinates | null>(null);
  const [destination, setDestination] = useState<Coordinates | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RouteResult | null>(null);
  const [respectSuperblocks, setRespectSuperblocks] = useState(true);

  // Input state for manual coordinate entry
  const [originLat, setOriginLat] = useState('');
  const [originLon, setOriginLon] = useState('');
  const [destLat, setDestLat] = useState('');
  const [destLon, setDestLon] = useState('');

  const handleSetOrigin = () => {
    const lat = parseFloat(originLat);
    const lon = parseFloat(originLon);
    if (!isNaN(lat) && !isNaN(lon)) {
      setOrigin({ lat, lon });
      setError(null);
    } else {
      setError('Invalid origin coordinates');
    }
  };

  const handleSetDestination = () => {
    const lat = parseFloat(destLat);
    const lon = parseFloat(destLon);
    if (!isNaN(lat) && !isNaN(lon)) {
      setDestination({ lat, lon });
      setError(null);
    } else {
      setError('Invalid destination coordinates');
    }
  };

  const handleComputeRoute = async () => {
    if (!origin || !destination) {
      setError('Please set both origin and destination');
      return;
    }

    if (!isPartitionAvailable) {
      setError('Please partition the city first');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const request: RouteRequest = {
        origin,
        destination,
        respect_superblocks: respectSuperblocks,
      };

      const routeResult = await computeRoute(request);
      setResult(routeResult);
      onRouteComputed(routeResult);

      if (!routeResult.success) {
        setError(routeResult.blocked_reason || 'Route computation failed');
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setError(message);
      setResult(null);
      onRouteComputed(null);
    } finally {
      setIsLoading(false);
    }
  };

  const handleClear = () => {
    setOrigin(null);
    setDestination(null);
    setOriginLat('');
    setOriginLon('');
    setDestLat('');
    setDestLon('');
    setResult(null);
    setError(null);
    onRouteComputed(null);
  };

  return (
    <div className="route-validator">
      <div className="validator-header">
        <span className="validator-title">Route Validator</span>
      </div>

      <div className="validator-body">
        {/* Origin Input */}
        <div className="coordinate-input">
          <label className="input-label">Origin</label>
          <div className="coord-row">
            <input
              type="text"
              placeholder="Lat"
              value={originLat}
              onChange={(e) => setOriginLat(e.target.value)}
              className="coord-field"
            />
            <input
              type="text"
              placeholder="Lon"
              value={originLon}
              onChange={(e) => setOriginLon(e.target.value)}
              className="coord-field"
            />
            <button
              onClick={handleSetOrigin}
              className="set-button"
              disabled={!originLat || !originLon}
            >
              Set
            </button>
          </div>
          {origin && (
            <div className="coord-set">
              {origin.lat.toFixed(5)}, {origin.lon.toFixed(5)}
            </div>
          )}
        </div>

        {/* Destination Input */}
        <div className="coordinate-input">
          <label className="input-label">Destination</label>
          <div className="coord-row">
            <input
              type="text"
              placeholder="Lat"
              value={destLat}
              onChange={(e) => setDestLat(e.target.value)}
              className="coord-field"
            />
            <input
              type="text"
              placeholder="Lon"
              value={destLon}
              onChange={(e) => setDestLon(e.target.value)}
              className="coord-field"
            />
            <button
              onClick={handleSetDestination}
              className="set-button"
              disabled={!destLat || !destLon}
            >
              Set
            </button>
          </div>
          {destination && (
            <div className="coord-set">
              {destination.lat.toFixed(5)}, {destination.lon.toFixed(5)}
            </div>
          )}
        </div>

        {/* Options */}
        <div className="route-options">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={respectSuperblocks}
              onChange={(e) => setRespectSuperblocks(e.target.checked)}
            />
            <span>Respect superblock constraints</span>
          </label>
        </div>

        {/* Action Buttons */}
        <div className="action-row">
          <button
            onClick={handleComputeRoute}
            className="compute-button"
            disabled={!origin || !destination || isLoading || !isPartitionAvailable}
          >
            {isLoading ? 'Computing...' : 'Compute Route'}
          </button>
          <button
            onClick={handleClear}
            className="clear-button"
            disabled={isLoading}
          >
            Clear
          </button>
        </div>

        {/* Error Display */}
        {error && (
          <div className="error-message">
            {error}
          </div>
        )}

        {/* Result Display */}
        {result && result.success && (
          <div className="result-display">
            <div className="result-title">Route Found</div>
            <div className="result-row">
              <span>Distance:</span>
              <span>{result.total_distance_km.toFixed(2)} km</span>
            </div>
            <div className="result-row">
              <span>Est. time:</span>
              <span>{result.estimated_time_min.toFixed(0)} min</span>
            </div>
            <div className="result-row">
              <span>Arterial roads:</span>
              <span>{result.arterial_percent.toFixed(0)}%</span>
            </div>
            {result.superblocks_traversed.length > 0 && (
              <div className="result-row">
                <span>Superblocks entered:</span>
                <span>{result.superblocks_traversed.length}</span>
              </div>
            )}
          </div>
        )}

        {result && !result.success && (
          <div className="result-display blocked">
            <div className="result-title">Route Blocked</div>
            <div className="blocked-reason">
              {result.blocked_reason || 'No route available'}
            </div>
            {result.alternative_available && (
              <div className="alternative-hint">
                Try with "Respect superblock constraints" unchecked
              </div>
            )}
          </div>
        )}

        {/* Help Text */}
        {!isPartitionAvailable && (
          <div className="help-text">
            Partition the city first to enable route validation.
          </div>
        )}
      </div>
    </div>
  );
}
