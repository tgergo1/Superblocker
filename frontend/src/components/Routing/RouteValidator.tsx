import { useState, useCallback, useRef, useEffect } from 'react';
import type { Coordinates, RouteResult } from '../../types';
import { computeRoute, type RouteRequest } from '../../services/api';
import './RouteValidator.css';

interface AddressResult {
  place_id: number;
  display_name: string;
  lat: string;
  lon: string;
}

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
  const [originName, setOriginName] = useState<string>('');
  const [destName, setDestName] = useState<string>('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RouteResult | null>(null);
  const [respectSuperblocks, setRespectSuperblocks] = useState(true);

  // Input mode: 'address' or 'coordinates'
  const [inputMode, setInputMode] = useState<'address' | 'coordinates'>('address');

  // Address search state
  const [originSearch, setOriginSearch] = useState('');
  const [destSearch, setDestSearch] = useState('');
  const [originResults, setOriginResults] = useState<AddressResult[]>([]);
  const [destResults, setDestResults] = useState<AddressResult[]>([]);
  const [isSearchingOrigin, setIsSearchingOrigin] = useState(false);
  const [isSearchingDest, setIsSearchingDest] = useState(false);
  const [showOriginResults, setShowOriginResults] = useState(false);
  const [showDestResults, setShowDestResults] = useState(false);

  const originSearchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const destSearchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Input state for manual coordinate entry
  const [originLat, setOriginLat] = useState('');
  const [originLon, setOriginLon] = useState('');
  const [destLat, setDestLat] = useState('');
  const [destLon, setDestLon] = useState('');

  // Geocoding search
  const searchAddress = useCallback(async (query: string): Promise<AddressResult[]> => {
    if (query.length < 3) return [];

    try {
      const response = await fetch(
        `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(query)}&limit=5&addressdetails=1`,
        {
          headers: {
            'Accept': 'application/json',
          }
        }
      );
      if (!response.ok) return [];
      const data = await response.json();
      return data as AddressResult[];
    } catch {
      return [];
    }
  }, []);

  // Debounced origin search
  useEffect(() => {
    if (originSearchTimeout.current) {
      clearTimeout(originSearchTimeout.current);
    }

    if (originSearch.length >= 3 && inputMode === 'address') {
      setIsSearchingOrigin(true);
      originSearchTimeout.current = setTimeout(async () => {
        const results = await searchAddress(originSearch);
        setOriginResults(results);
        setIsSearchingOrigin(false);
        setShowOriginResults(results.length > 0);
      }, 300);
    } else {
      setOriginResults([]);
      setShowOriginResults(false);
    }

    return () => {
      if (originSearchTimeout.current) {
        clearTimeout(originSearchTimeout.current);
      }
    };
  }, [originSearch, searchAddress, inputMode]);

  // Debounced destination search
  useEffect(() => {
    if (destSearchTimeout.current) {
      clearTimeout(destSearchTimeout.current);
    }

    if (destSearch.length >= 3 && inputMode === 'address') {
      setIsSearchingDest(true);
      destSearchTimeout.current = setTimeout(async () => {
        const results = await searchAddress(destSearch);
        setDestResults(results);
        setIsSearchingDest(false);
        setShowDestResults(results.length > 0);
      }, 300);
    } else {
      setDestResults([]);
      setShowDestResults(false);
    }

    return () => {
      if (destSearchTimeout.current) {
        clearTimeout(destSearchTimeout.current);
      }
    };
  }, [destSearch, searchAddress, inputMode]);

  const handleSelectOrigin = (result: AddressResult) => {
    setOrigin({ lat: parseFloat(result.lat), lon: parseFloat(result.lon) });
    setOriginName(result.display_name.split(',').slice(0, 2).join(', '));
    setOriginSearch(result.display_name.split(',').slice(0, 2).join(', '));
    setShowOriginResults(false);
    setError(null);
  };

  const handleSelectDest = (result: AddressResult) => {
    setDestination({ lat: parseFloat(result.lat), lon: parseFloat(result.lon) });
    setDestName(result.display_name.split(',').slice(0, 2).join(', '));
    setDestSearch(result.display_name.split(',').slice(0, 2).join(', '));
    setShowDestResults(false);
    setError(null);
  };

  const handleSetOrigin = () => {
    const lat = parseFloat(originLat);
    const lon = parseFloat(originLon);
    if (!isNaN(lat) && !isNaN(lon)) {
      setOrigin({ lat, lon });
      setOriginName(`${lat.toFixed(4)}, ${lon.toFixed(4)}`);
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
      setDestName(`${lat.toFixed(4)}, ${lon.toFixed(4)}`);
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
    setOriginName('');
    setDestName('');
    setOriginLat('');
    setOriginLon('');
    setDestLat('');
    setDestLon('');
    setOriginSearch('');
    setDestSearch('');
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
        {/* Input Mode Toggle */}
        <div className="input-mode-toggle">
          <button
            className={`mode-btn ${inputMode === 'address' ? 'active' : ''}`}
            onClick={() => setInputMode('address')}
          >
            Address
          </button>
          <button
            className={`mode-btn ${inputMode === 'coordinates' ? 'active' : ''}`}
            onClick={() => setInputMode('coordinates')}
          >
            Coordinates
          </button>
        </div>

        {inputMode === 'address' ? (
          <>
            {/* Origin Address Search */}
            <div className="location-input">
              <label className="input-label">
                Origin {originName && <span className="coord-set">({originName})</span>}
              </label>
              <div className="address-input-wrapper">
                <input
                  type="text"
                  placeholder="Search address..."
                  value={originSearch}
                  onChange={(e) => setOriginSearch(e.target.value)}
                  onFocus={() => originResults.length > 0 && setShowOriginResults(true)}
                  onBlur={() => setTimeout(() => setShowOriginResults(false), 200)}
                  className="address-field"
                />
                {showOriginResults && (
                  <div className="address-results">
                    {isSearchingOrigin ? (
                      <div className="address-searching">Searching...</div>
                    ) : (
                      originResults.map((r) => (
                        <div
                          key={r.place_id}
                          className="address-result-item"
                          onClick={() => handleSelectOrigin(r)}
                        >
                          {r.display_name}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Destination Address Search */}
            <div className="location-input">
              <label className="input-label">
                Destination {destName && <span className="coord-set">({destName})</span>}
              </label>
              <div className="address-input-wrapper">
                <input
                  type="text"
                  placeholder="Search address..."
                  value={destSearch}
                  onChange={(e) => setDestSearch(e.target.value)}
                  onFocus={() => destResults.length > 0 && setShowDestResults(true)}
                  onBlur={() => setTimeout(() => setShowDestResults(false), 200)}
                  className="address-field"
                />
                {showDestResults && (
                  <div className="address-results">
                    {isSearchingDest ? (
                      <div className="address-searching">Searching...</div>
                    ) : (
                      destResults.map((r) => (
                        <div
                          key={r.place_id}
                          className="address-result-item"
                          onClick={() => handleSelectDest(r)}
                        >
                          {r.display_name}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        ) : (
          <>
            {/* Origin Coordinate Input */}
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

            {/* Destination Coordinate Input */}
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
          </>
        )}

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
