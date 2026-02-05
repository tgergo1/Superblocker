import { useState, useCallback, useEffect } from 'react';
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

  const [colorBy, setColorBy] = useState<'hierarchy' | 'traffic'>('hierarchy');
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
    analyze: findSuperblocks,
  } = useSuperblocks(bbox);

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
        />
      </main>
    </div>
  );
}

export default App;
