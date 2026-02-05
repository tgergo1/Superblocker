import { useState, useCallback, useEffect, useMemo } from 'react';
import { StreetMap } from './components/Map';
import { SearchBox } from './components/Search';
import { LayerControls } from './components/Controls';
import { useSearch } from './hooks/useSearch';
import { useStreetNetwork } from './hooks/useStreetNetwork';
import { useSuperblocks } from './hooks/useSuperblocks';
import type { BoundingBox, ViewState } from './types';
import './App.css';

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

  const {
    data: streetNetwork,
    isLoading: isLoadingNetwork,
    refetch: fetchNetwork,
  } = useStreetNetwork(bbox, false);

  const {
    data: superblockData,
    isLoading: isLoadingSuperblocks,
    progress: analysisProgress,
    parameters: analysisParameters,
    setParameters: setAnalysisParameters,
    analyze: findSuperblocks,
  } = useSuperblocks(bbox);

  // Calculate impact metrics from superblock data
  const impactMetrics = useMemo(() => {
    if (!superblockData?.candidates || superblockData.candidates.length === 0) {
      return undefined;
    }

    const candidates = superblockData.candidates;
    
    // Calculate total area covered by superblocks
    const totalArea = candidates.reduce((sum, c) => sum + c.area_hectares, 0);
    
    // Estimate traffic reduction from average score and traffic impact
    // When actual traffic_impact data is unavailable, we estimate using score * 0.6
    // because superblock scores correlate with through-traffic removal potential,
    // and typically 60% of a high-scoring superblock's benefit comes from traffic reduction
    const avgTrafficReduction = candidates.reduce((sum, c) => {
      const trafficImpact = c.traffic_impact?.removed_through_traffic_pct ?? (c.score * 0.6);
      return sum + trafficImpact;
    }, 0) / candidates.length;
    
    // Estimate pollution reduction (roughly proportional to traffic reduction)
    const pollutionReduction = Math.round(avgTrafficReduction * 0.8);
    
    // Estimate pedestrian area gain (interior roads becoming pedestrian/shared)
    const interiorRoadsCount = candidates.reduce((sum, c) => sum + (c.interior_roads?.length ?? 0), 0);
    // Each interior road segment is assumed to average ~500m² (0.05 ha) of reclaimed pedestrian space
    // This accounts for typical urban road widths (8-10m) and segment lengths (50-60m)
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
  }, [clearSelection]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Superblocker</h1>
        <span className="subtitle">Urban Superblock Planning Tool</span>
      </header>

      <main className="app-main">
        <StreetMap
          streetNetwork={streetNetwork ?? null}
          superblocks={superblockData?.candidates}
          showSuperblocks={showSuperblocks}
          initialViewState={viewState}
          onViewStateChange={setViewState}
          colorBy={colorBy}
        />

        <SearchBox
          onSearch={handleSearch}
          onSelect={handleSelect}
          results={searchResults}
          isLoading={isSearching}
          selectedPlace={selectedPlace}
          onClear={handleClearSelection}
        />

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
      </main>
    </div>
  );
}

export default App;
