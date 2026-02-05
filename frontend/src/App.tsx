import { useState, useCallback, useEffect, useMemo } from 'react';
import { StreetMap } from './components/Map';
import { SearchBox } from './components/Search';
import { LayerControls } from './components/Controls';
import { PartitionControls } from './components/Controls/PartitionControls';
import { RouteValidator } from './components/Routing/RouteValidator';
import { useSearch } from './hooks/useSearch';
import { useStreetNetwork } from './hooks/useStreetNetwork';
import { useSuperblocks } from './hooks/useSuperblocks';
import { usePartition } from './hooks/usePartition';
import type { BoundingBox, ViewState, EnforcedSuperblock, RouteResult } from './types';
import './App.css';

type AnalysisMode = 'candidates' | 'partition';

function App() {
  const {
    searchResults,
    isLoading: isSearching,
    selectedPlace,
    handleSearch,
    handleSelect,
    clearSelection,
  } = useSearch();

  const [colorBy, setColorBy] = useState<'hierarchy' | 'traffic' | 'interventions'>('hierarchy');
  const [bbox, setBbox] = useState<BoundingBox | null>(null);
  const [showSuperblocks, setShowSuperblocks] = useState(true);
  const [viewState, setViewState] = useState<ViewState>({
    longitude: 19.0402,
    latitude: 47.4979,
    zoom: 12,
  });

  // Analysis mode toggle
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>('partition');

  // Partition display options
  const [showEntryPoints, setShowEntryPoints] = useState(true);
  const [showModalFilters, setShowModalFilters] = useState(true);
  const [selectedEnforcedSuperblock, setSelectedEnforcedSuperblock] = useState<EnforcedSuperblock | null>(null);

  // Route state
  const [route, setRoute] = useState<RouteResult | null>(null);
  const [showRoute, setShowRoute] = useState(true);

  const {
    data: streetNetwork,
    isLoading: isLoadingNetwork,
    refetch: fetchNetwork,
  } = useStreetNetwork(bbox, false);

  // Old candidate-based analysis
  const {
    data: superblockData,
    isLoading: isLoadingSuperblocks,
    progress: analysisProgress,
    parameters: analysisParameters,
    setParameters: setAnalysisParameters,
    analyze: findSuperblocks,
  } = useSuperblocks(bbox);

  // New partition-based system
  const {
    partition,
    isLoading: isLoadingPartition,
    progress: partitionProgress,
    parameters: partitionParameters,
    setParameters: setPartitionParameters,
    runPartition,
  } = usePartition(bbox);

  // Calculate impact metrics from superblock data (for old mode)
  const impactMetrics = useMemo(() => {
    if (!superblockData?.candidates || superblockData.candidates.length === 0) {
      return undefined;
    }

    const candidates = superblockData.candidates;
    const totalArea = candidates.reduce((sum, c) => sum + c.area_hectares, 0);
    const avgTrafficReduction = candidates.reduce((sum, c) => {
      const trafficImpact = c.traffic_impact?.removed_through_traffic_pct ?? (c.score * 0.6);
      return sum + trafficImpact;
    }, 0) / candidates.length;
    const pollutionReduction = Math.round(avgTrafficReduction * 0.8);
    const interiorRoadsCount = candidates.reduce((sum, c) => sum + (c.interior_roads?.length ?? 0), 0);
    const pedestrianAreaGain = Math.round((interiorRoadsCount * 0.05) * 10) / 10;

    return {
      trafficReduction: Math.round(avgTrafficReduction),
      pollutionReduction,
      pedestrianArea: pedestrianAreaGain,
      totalArea: Math.round(totalArea * 10) / 10,
    };
  }, [superblockData?.candidates]);

  // Update view state and bbox when a place is selected
  useEffect(() => {
    if (selectedPlace) {
      setViewState({
        longitude: selectedPlace.lon,
        latitude: selectedPlace.lat,
        zoom: 14,
      });
      setBbox(selectedPlace.boundingbox);
    }
  }, [selectedPlace]);

  const handleFetchNetwork = useCallback(() => {
    if (bbox) {
      fetchNetwork();
    }
  }, [bbox, fetchNetwork]);

  const handleFindSuperblocks = useCallback(() => {
    if (bbox && streetNetwork) {
      findSuperblocks();
    }
  }, [bbox, streetNetwork, findSuperblocks]);

  const handleClearSelection = useCallback(() => {
    clearSelection();
    setBbox(null);
    setSelectedEnforcedSuperblock(null);
    setRoute(null);
  }, [clearSelection]);

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
        <h1>Superblocker</h1>
        <span className="subtitle">Urban Superblock Planning Tool</span>

        {/* Mode Toggle */}
        <div className="mode-toggle">
          <button
            className={`mode-btn ${analysisMode === 'candidates' ? 'active' : ''}`}
            onClick={() => setAnalysisMode('candidates')}
          >
            Candidates
          </button>
          <button
            className={`mode-btn ${analysisMode === 'partition' ? 'active' : ''}`}
            onClick={() => setAnalysisMode('partition')}
          >
            Full Partition
          </button>
        </div>
      </header>

      <main className="app-main">
        <StreetMap
          streetNetwork={streetNetwork ?? null}
          superblocks={analysisMode === 'candidates' ? superblockData?.candidates : undefined}
          showSuperblocks={analysisMode === 'candidates' && showSuperblocks}
          initialViewState={viewState}
          onViewStateChange={setViewState}
          colorBy={colorBy}
          // New partition props
          partition={analysisMode === 'partition' ? partition : null}
          showPartition={analysisMode === 'partition' && !!partition}
          showEntryPoints={showEntryPoints}
          showModalFilters={showModalFilters}
          selectedEnforcedSuperblock={selectedEnforcedSuperblock}
          onEnforcedSuperblockClick={handleEnforcedSuperblockClick}
          // Route props
          route={route}
          showRoute={showRoute}
        />

        <div className="app-overlay">
          <div className="overlay-column overlay-left">
            <SearchBox
              onSearch={handleSearch}
              onSelect={handleSelect}
              results={searchResults}
              isLoading={isSearching}
              selectedPlace={selectedPlace}
              onClear={handleClearSelection}
            />

            {/* Route Validator - only show when partition is available */}
            {analysisMode === 'partition' && partition && (
              <RouteValidator
                onRouteComputed={handleRouteComputed}
                isPartitionAvailable={!!partition}
              />
            )}
          </div>

          <div className="overlay-column overlay-right">
            {/* Show different controls based on mode */}
            {analysisMode === 'candidates' ? (
              <LayerControls
                colorBy={colorBy}
                onColorByChange={setColorBy}
                isLoadingNetwork={isLoadingNetwork}
                isLoadingSuperblocks={isLoadingSuperblocks}
                analysisProgress={analysisProgress}
                onFetchNetwork={handleFetchNetwork}
                onFindSuperblocks={handleFindSuperblocks}
                canFetch={bbox !== null}
                hasNetwork={!!streetNetwork}
                superblockCount={superblockData?.candidates.length}
                showSuperblocks={showSuperblocks}
                onToggleSuperblocks={setShowSuperblocks}
                analysisParameters={analysisParameters}
                onParametersChange={setAnalysisParameters}
                impactMetrics={impactMetrics}
              />
            ) : (
              <PartitionControls
                isLoading={isLoadingPartition}
                progress={partitionProgress}
                parameters={partitionParameters}
                onParametersChange={setPartitionParameters}
                onPartition={runPartition}
                canPartition={bbox !== null}
                partition={partition}
                showEntryPoints={showEntryPoints}
                onShowEntryPointsChange={setShowEntryPoints}
                showModalFilters={showModalFilters}
                onShowModalFiltersChange={setShowModalFilters}
              />
            )}
          </div>
        </div>
      </main>

      {/* Selected Enforced Superblock Details */}
      {analysisMode === 'partition' && selectedEnforcedSuperblock && (
        <div className="enforced-superblock-details">
          <div className="details-header">
            <span className="details-title">Superblock Details</span>
            <button
              className="close-button"
              onClick={() => setSelectedEnforcedSuperblock(null)}
            >
              ×
            </button>
          </div>
          <div className="details-body">
            <div className="detail-row">
              <span>ID:</span>
              <span>{selectedEnforcedSuperblock.id.slice(0, 12)}...</span>
            </div>
            <div className="detail-row">
              <span>Area:</span>
              <span>{selectedEnforcedSuperblock.area_hectares.toFixed(1)} ha</span>
            </div>
            <div className="detail-row">
              <span>Sectors:</span>
              <span>{selectedEnforcedSuperblock.num_sectors}</span>
            </div>
            <div className="detail-row">
              <span>Entry Points:</span>
              <span>{selectedEnforcedSuperblock.entry_points.length}</span>
            </div>
            <div className="detail-row">
              <span>Constraint Valid:</span>
              <span className={selectedEnforcedSuperblock.constraint_validated ? 'status-good' : 'status-bad'}>
                {selectedEnforcedSuperblock.constraint_validated ? 'Yes' : 'No'}
              </span>
            </div>
            <div className="detail-row">
              <span>All Reachable:</span>
              <span className={selectedEnforcedSuperblock.all_addresses_reachable ? 'status-good' : 'status-warning'}>
                {selectedEnforcedSuperblock.all_addresses_reachable ? 'Yes' : 'No'}
              </span>
            </div>

            <div className="details-section-title">Modifications</div>
            <div className="detail-row">
              <span>Modal Filters:</span>
              <span>{selectedEnforcedSuperblock.modal_filter_count}</span>
            </div>
            <div className="detail-row">
              <span>One-way Conversions:</span>
              <span>{selectedEnforcedSuperblock.one_way_conversion_count}</span>
            </div>
            <div className="detail-row">
              <span>Interior Roads:</span>
              <span>{selectedEnforcedSuperblock.interior_roads_count}</span>
            </div>

            {selectedEnforcedSuperblock.unreachable_addresses.length > 0 && (
              <>
                <div className="details-section-title">Unreachable ({selectedEnforcedSuperblock.unreachable_addresses.length})</div>
                <div className="unreachable-list">
                  {selectedEnforcedSuperblock.unreachable_addresses.slice(0, 3).map((addr, i) => (
                    <div key={i} className="unreachable-item">
                      Node {addr.node_id} - Sector {addr.nearest_entry_sector}
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
