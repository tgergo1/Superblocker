import { useCallback, useRef, useState } from 'react';
import type { CityPartition, Coordinates, RouteResult, SearchResult } from '../../types';
import { computeRoute, searchPlaces, type RouteRequest } from '../../services/api';
import './RouteValidator.css';

interface RouteValidatorProps {
  onRouteComputed: (route: RouteResult | null) => void;
  partition: CityPartition;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
}

type SearchTarget = 'origin' | 'destination';

export function RouteValidator({
  onRouteComputed,
  partition,
  expanded,
  onExpandedChange,
}: RouteValidatorProps) {
  const [origin, setOrigin] = useState<Coordinates | null>(null);
  const [destination, setDestination] = useState<Coordinates | null>(null);
  const [originName, setOriginName] = useState('');
  const [destName, setDestName] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RouteResult | null>(null);
  const [inputMode, setInputMode] = useState<'address' | 'coordinates'>('address');
  const [originSearch, setOriginSearch] = useState('');
  const [destSearch, setDestSearch] = useState('');
  const [originResults, setOriginResults] = useState<SearchResult[]>([]);
  const [destResults, setDestResults] = useState<SearchResult[]>([]);
  const [searchingTarget, setSearchingTarget] = useState<SearchTarget | null>(null);
  const [originLat, setOriginLat] = useState('');
  const [originLon, setOriginLon] = useState('');
  const [destLat, setDestLat] = useState('');
  const [destLon, setDestLon] = useState('');
  const searchAbortRef = useRef<AbortController | null>(null);

  const searchAddress = useCallback(async (target: SearchTarget) => {
    const query = (target === 'origin' ? originSearch : destSearch).trim();
    if (query.length < 2) {
      setError('Enter at least two characters before searching.');
      return;
    }
    searchAbortRef.current?.abort();
    const controller = new AbortController();
    searchAbortRef.current = controller;
    setSearchingTarget(target);
    setError(null);
    try {
      const response = await searchPlaces(query, 5, controller.signal);
      if (target === 'origin') setOriginResults(response.results);
      else setDestResults(response.results);
      if (response.results.length === 0) setError('No matching places found.');
    } catch (searchError) {
      if ((searchError as Error).name !== 'CanceledError') {
        setError('Address search failed. Please try again.');
      }
    } finally {
      if (searchAbortRef.current === controller) setSearchingTarget(null);
    }
  }, [destSearch, originSearch]);

  const selectAddress = (target: SearchTarget, place: SearchResult) => {
    const coordinates = { lat: place.lat, lon: place.lon };
    const shortName = place.display_name.split(',').slice(0, 2).join(', ');
    if (target === 'origin') {
      setOrigin(coordinates);
      setOriginName(shortName);
      setOriginSearch(shortName);
      setOriginResults([]);
    } else {
      setDestination(coordinates);
      setDestName(shortName);
      setDestSearch(shortName);
      setDestResults([]);
    }
    setError(null);
  };

  const setCoordinate = (target: SearchTarget) => {
    const lat = Number(target === 'origin' ? originLat : destLat);
    const lon = Number(target === 'origin' ? originLon : destLon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      setError(`Invalid ${target} coordinates`);
      return;
    }
    if (target === 'origin') {
      setOrigin({ lat, lon });
      setOriginName(`${lat.toFixed(4)}, ${lon.toFixed(4)}`);
    } else {
      setDestination({ lat, lon });
      setDestName(`${lat.toFixed(4)}, ${lon.toFixed(4)}`);
    }
    setError(null);
  };

  const handleComputeRoute = async () => {
    if (!origin || !destination) {
      setError('Please set both origin and destination');
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const request: RouteRequest = {
        origin,
        destination,
        respect_superblocks: true,
        prefer_arterials: true,
        partition,
      };
      const routeResult = await computeRoute(request);
      setResult(routeResult);
      onRouteComputed(routeResult);
    } catch {
      setError('Route computation failed. Please try again.');
      setResult(null);
      onRouteComputed(null);
    } finally {
      setIsLoading(false);
    }
  };

  const handleClear = () => {
    searchAbortRef.current?.abort();
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
    setOriginResults([]);
    setDestResults([]);
    setResult(null);
    setError(null);
    onRouteComputed(null);
  };

  const addressInput = (
    target: SearchTarget,
    label: string,
    value: string,
    setValue: (value: string) => void,
    name: string,
    results: SearchResult[],
  ) => (
    <div className="location-input">
      <label className="input-label" htmlFor={`${target}-address`}>
        {label} {name && <span className="coord-set">({name})</span>}
      </label>
      <form className="address-input-wrapper address-search-form" onSubmit={(event) => {
        event.preventDefault();
        void searchAddress(target);
      }}>
        <input
          id={`${target}-address`}
          type="text"
          placeholder="Enter address, then search"
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            if (target === 'origin') setOriginResults([]);
            else setDestResults([]);
          }}
          className="address-field"
          autoComplete="off"
        />
        <button className="set-button" type="submit" disabled={searchingTarget !== null || value.trim().length < 2}>
          {searchingTarget === target ? '…' : 'Search'}
        </button>
      </form>
      {results.length > 0 && (
        <div className="address-results" role="listbox" aria-label={`${label} results`}>
          {results.map((place) => (
            <button
              type="button"
              key={place.place_id}
              className="address-result-item"
              onClick={() => selectAddress(target, place)}
            >
              {place.display_name}
            </button>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="route-validator">
      <button
        type="button"
        className="validator-header"
        onClick={() => onExpandedChange(!expanded)}
        aria-expanded={expanded}
        aria-controls="route-validator-body"
      >
        <span className="validator-title">Test boundary-road routing</span>
        <span className={`validator-chevron ${expanded ? 'expanded' : ''}`} aria-hidden="true">⌄</span>
      </button>
      {expanded && <div className="validator-body" id="route-validator-body">
        <p className="validator-description">
          Test any trip against the finished plan. Travel between superblocks is
          forced onto the highlighted boundary-road network.
        </p>
        <div className="input-mode-toggle" role="group" aria-label="Location input mode">
          <button type="button" className={`mode-btn ${inputMode === 'address' ? 'active' : ''}`} onClick={() => setInputMode('address')} aria-pressed={inputMode === 'address'}>Address</button>
          <button type="button" className={`mode-btn ${inputMode === 'coordinates' ? 'active' : ''}`} onClick={() => setInputMode('coordinates')} aria-pressed={inputMode === 'coordinates'}>Coordinates</button>
        </div>

        {inputMode === 'address' ? <>
          {addressInput('origin', 'Origin', originSearch, setOriginSearch, originName, originResults)}
          {addressInput('destination', 'Destination', destSearch, setDestSearch, destName, destResults)}
        </> : <>
          <div className="coordinate-input">
            <label className="input-label">Origin</label>
            <div className="coord-row">
              <input aria-label="Origin latitude" type="number" placeholder="Lat" value={originLat} onChange={(e) => setOriginLat(e.target.value)} className="coord-field" />
              <input aria-label="Origin longitude" type="number" placeholder="Lon" value={originLon} onChange={(e) => setOriginLon(e.target.value)} className="coord-field" />
              <button
                type="button"
                onClick={() => setCoordinate('origin')}
                className="set-button"
                aria-label="Set origin coordinates"
              >
                Set
              </button>
            </div>
            {origin && <div className="coord-set">{origin.lat.toFixed(5)}, {origin.lon.toFixed(5)}</div>}
          </div>
          <div className="coordinate-input">
            <label className="input-label">Destination</label>
            <div className="coord-row">
              <input aria-label="Destination latitude" type="number" placeholder="Lat" value={destLat} onChange={(e) => setDestLat(e.target.value)} className="coord-field" />
              <input aria-label="Destination longitude" type="number" placeholder="Lon" value={destLon} onChange={(e) => setDestLon(e.target.value)} className="coord-field" />
              <button
                type="button"
                onClick={() => setCoordinate('destination')}
                className="set-button"
                aria-label="Set destination coordinates"
              >
                Set
              </button>
            </div>
            {destination && <div className="coord-set">{destination.lat.toFixed(5)}, {destination.lon.toFixed(5)}</div>}
          </div>
        </>}

        <div className="action-row">
          <button type="button" onClick={() => void handleComputeRoute()} className="compute-button" disabled={!origin || !destination || isLoading}>{isLoading ? 'Testing...' : 'Test planned route'}</button>
          <button type="button" onClick={handleClear} className="clear-button" disabled={isLoading}>Clear</button>
        </div>
        {error && <div className="error-message" role="alert">{error}</div>}
        {result?.success && <div className="result-display">
          <div className="result-title">Route Found</div>
          <div className="result-row"><span>Distance:</span><span>{result.total_distance_km.toFixed(2)} km</span></div>
          <div className="result-row"><span>Est. time:</span><span>{result.estimated_time_min.toFixed(0)} min</span></div>
          <div className="result-row"><span>Arterial roads:</span><span>{result.arterial_percent.toFixed(0)}%</span></div>
          {result.superblocks_traversed.length > 0 && <div className="result-row"><span>Superblocks entered:</span><span>{result.superblocks_traversed.length}</span></div>}
        </div>}
        {result && !result.success && <div className="result-display blocked"><div className="result-title">Route Blocked</div><div className="blocked-reason">{result.blocked_reason || 'No route available'}</div></div>}
      </div>}
    </div>
  );
}
