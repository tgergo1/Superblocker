import { useState, useCallback } from 'react';
import { StreetMap } from './components/Map';
import { SearchBox } from './components/Search';
import { PartitionControls } from './components/Controls/PartitionControls';
import { RouteValidator } from './components/Routing/RouteValidator';
import { useSearch } from './hooks/useSearch';
import { usePartition } from './hooks/usePartition';
import type {
  BoundingBox,
  ViewState,
  EnforcedSuperblock,
  RouteResult,
  SearchResult,
} from './types';
import './App.css';

function App() {
  const {
    searchResults,
    isLoading: isSearching,
    error: searchError,
    selectedPlace,
    handleSearch,
    handleSelect,
    clearSelection,
  } = useSearch();

  const [bbox, setBbox] = useState<BoundingBox | null>(null);
  const [viewState, setViewState] = useState<ViewState>({
    longitude: 19.0402,
    latitude: 47.4979,
    zoom: 12,
  });

  const [showEntryPoints, setShowEntryPoints] = useState(true);
  const [showModalFilters, setShowModalFilters] = useState(true);
  const [selectedEnforcedSuperblock, setSelectedEnforcedSuperblock] = useState<EnforcedSuperblock | null>(null);

  // Route state
  const [route, setRoute] = useState<RouteResult | null>(null);
  const [showRoute, setShowRoute] = useState(true);
  const [routeValidatorExpanded, setRouteValidatorExpanded] = useState(false);

  const {
    data: partitionData,
    partition,
    isLoading: isLoadingPartition,
    progress: partitionProgress,
    parameters: partitionParameters,
    setParameters: setPartitionParameters,
    runPartition,
    cancel: cancelPartition,
    error: partitionError,
    reset: resetPartition,
  } = usePartition(bbox);

  const activeStreetNetwork = partitionData?.street_network ?? null;

  const handlePlaceSelect = useCallback((place: SearchResult) => {
    resetPartition();
    handleSelect(place);
    setViewState({
      longitude: place.lon,
      latitude: place.lat,
      zoom: 14,
    });
    setBbox(place.boundingbox);
    setSelectedEnforcedSuperblock(null);
    setRoute(null);
    setRouteValidatorExpanded(false);
  }, [handleSelect, resetPartition]);

  const handleClearSelection = useCallback(() => {
    resetPartition();
    clearSelection();
    setBbox(null);
    setSelectedEnforcedSuperblock(null);
    setRoute(null);
    setRouteValidatorExpanded(false);
  }, [clearSelection, resetPartition]);

  const handleEnforcedSuperblockClick = useCallback((sb: EnforcedSuperblock) => {
    setSelectedEnforcedSuperblock(prev =>
      prev?.id === sb.id ? null : sb
    );
  }, []);

  const handleRouteComputed = useCallback((routeResult: RouteResult | null) => {
    setRoute(routeResult);
    setShowRoute(true);
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-brand">
          <span className="brand-mark" aria-hidden="true">S</span>
          <div className="brand-copy">
            <h1>Superblocker</h1>
            <span className="subtitle">Automated citywide superblock planning</span>
          </div>
        </div>
        <div className="header-purpose" aria-label="Planner purpose">
          <span className="purpose-dot" aria-hidden="true" />
          Citywide plan
        </div>
      </header>

      <main className={`app-main ${selectedEnforcedSuperblock ? 'details-open' : ''}`}>
        <StreetMap
          key={selectedPlace?.place_id ?? 'no-place'}
          streetNetwork={activeStreetNetwork}
          initialViewState={viewState}
          onViewStateChange={setViewState}
          colorBy="hierarchy"
          partition={partition}
          showPartition={!!partition}
          showEntryPoints={showEntryPoints}
          showModalFilters={showModalFilters}
          selectedEnforcedSuperblock={selectedEnforcedSuperblock}
          onEnforcedSuperblockClick={handleEnforcedSuperblockClick}
          // Route props
          route={route}
          showRoute={showRoute}
          selectedPlaceName={selectedPlace?.display_name}
          emptyStateMessage="Run the automated analysis to partition the complete road network and move cross-traffic to boundary roads."
          hideLegends={routeValidatorExpanded}
        />

        <div className="app-overlay">
          <div className="overlay-column overlay-left">
            <SearchBox
              onSearch={handleSearch}
              onSelect={handlePlaceSelect}
              results={searchResults}
              isLoading={isSearching}
              error={searchError}
              selectedPlace={selectedPlace}
              onClear={handleClearSelection}
            />

            {partition && (
              <RouteValidator
                onRouteComputed={handleRouteComputed}
                partition={partition}
                expanded={routeValidatorExpanded}
                onExpandedChange={setRouteValidatorExpanded}
              />
            )}
          </div>

          <div className="overlay-column overlay-right">
            <PartitionControls
              isLoading={isLoadingPartition}
              progress={partitionProgress}
              parameters={partitionParameters}
              onParametersChange={setPartitionParameters}
              onPartition={runPartition}
              onCancel={cancelPartition}
              canPartition={bbox !== null}
              partition={partition}
              showEntryPoints={showEntryPoints}
              onShowEntryPointsChange={setShowEntryPoints}
              showModalFilters={showModalFilters}
              onShowModalFiltersChange={setShowModalFilters}
              error={partitionError}
            />
          </div>
        </div>
      </main>

      {selectedEnforcedSuperblock && (
        <div className="enforced-superblock-details">
          <div className="details-header">
            <span className="details-title">Superblock Details</span>
            <button
              type="button"
              className="close-button"
              onClick={() => setSelectedEnforcedSuperblock(null)}
              aria-label="Close superblock details"
            >
              ×
            </button>
          </div>
          <div className="details-body">
            {/* Status indicators */}
            <div className="status-row">
              <span className={`status-badge ${selectedEnforcedSuperblock.constraint_validated ? 'valid' : 'invalid'}`}>
                {selectedEnforcedSuperblock.constraint_validated ? '✓ No cross-traffic' : '✕ Cross-traffic remains'}
              </span>
              <span className={`status-badge ${selectedEnforcedSuperblock.all_addresses_reachable ? 'reachable' : 'unreachable'}`}>
                {selectedEnforcedSuperblock.all_addresses_reachable ? '✓ All Reachable' : '⚠ Some Unreachable'}
              </span>
            </div>

            <div className="detail-row">
              <span>Area:</span>
              <span>{selectedEnforcedSuperblock.area_hectares.toFixed(1)} ha</span>
            </div>
            <div className="detail-row">
              <span>Interior Roads:</span>
              <span>{selectedEnforcedSuperblock.interior_roads_count}</span>
            </div>
            <div className="detail-row">
              <span>Street Cuts:</span>
              <span>{selectedEnforcedSuperblock.street_cut_count}</span>
            </div>

            {/* Entry Points by direction */}
            <div className="details-section-title">Entry and return directions ({selectedEnforcedSuperblock.entry_points.length})</div>
            <div className="entry-points-grid">
              {Array.from({ length: selectedEnforcedSuperblock.num_sectors }, (_, sector) => sector).map(sector => {
                const count = selectedEnforcedSuperblock.entry_points.filter(ep => ep.sector === sector).length;
                const labels = selectedEnforcedSuperblock.num_sectors === 4
                  ? ['E', 'N', 'W', 'S']
                  : Array.from({ length: selectedEnforcedSuperblock.num_sectors }, (_, index) => `S${index + 1}`);
                const arrows = selectedEnforcedSuperblock.num_sectors === 4
                  ? ['→', '↑', '←', '↓']
                  : Array.from({ length: selectedEnforcedSuperblock.num_sectors }, () => '•');
                const colors = ['#3b82f6', '#22c55e', '#ef4444', '#fbbf24'];
                return count > 0 ? (
                  <div key={sector} className="entry-point-item" style={{ borderLeftColor: colors[sector % colors.length] }}>
                    <span className="direction-arrow" style={{ color: colors[sector % colors.length] }}>{arrows[sector]}</span>
                    <span className="direction-label">{labels[sector]}</span>
                    <span className="direction-count">{count}</span>
                  </div>
                ) : null;
              })}
            </div>

            {/* Street actions */}
            <div className="details-section-title">
              Street actions ({selectedEnforcedSuperblock.modifications.length})
            </div>
            <div className="modifications-list">
              {selectedEnforcedSuperblock.modifications.slice(0, 5).map((mod, i) => (
                <div key={i} className="modification-item">
                  <span className={`mod-icon ${mod.modification_type}`}>
                    {mod.modification_type === 'modal_filter' ? 'X' :
                     mod.modification_type === 'one_way' ? '>' :
                     mod.modification_type === 'two_way' ? '<>' :
                     mod.modification_type === 'turn_restriction' ? '!' : '='}
                  </span>
                  <div className="mod-details">
                    <span className="mod-name">{mod.name || `Road ${mod.osm_id}`}</span>
                    <span className="mod-type">
                      {mod.modification_type === 'full_closure'
                        ? 'street cut'
                        : mod.modification_type === 'two_way'
                          ? 'two-way local access'
                        : mod.modification_type.replace('_', ' ')}
                      {mod.direction && (
                        <span className="mod-direction">
                          {' '}
                          → {mod.direction === 'u_to_v' ? 'u → v' : 'v → u'}
                        </span>
                      )}
                    </span>
                  </div>
                </div>
              ))}
              {selectedEnforcedSuperblock.modifications.length > 5 && (
                <div className="modifications-more">
                  +{selectedEnforcedSuperblock.modifications.length - 5} more modifications
                </div>
              )}
            </div>

            {/* Unreachable addresses warning */}
            {selectedEnforcedSuperblock.unreachable_addresses.length > 0 && (
              <>
                <div className="details-section-title warning">
                  ⚠ Unreachable ({selectedEnforcedSuperblock.unreachable_addresses.length})
                </div>
                <div className="unreachable-list">
                  {selectedEnforcedSuperblock.unreachable_addresses.slice(0, 3).map((addr, i) => (
                    <div key={i} className="unreachable-item">
                      <span className="unreachable-icon">⚠</span>
                      <span>Node {addr.node_id}</span>
                      <span className="unreachable-reason">{addr.reason}</span>
                    </div>
                  ))}
                  {selectedEnforcedSuperblock.unreachable_addresses.length > 3 && (
                    <div className="unreachable-more">
                      +{selectedEnforcedSuperblock.unreachable_addresses.length - 3} more...
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
