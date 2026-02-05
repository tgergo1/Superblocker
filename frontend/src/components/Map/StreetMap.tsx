import { useState, useCallback, useMemo, useEffect } from 'react';
import Map, { NavigationControl, ScaleControl } from 'react-map-gl/maplibre';
import DeckGL from '@deck.gl/react';
import { GeoJsonLayer } from '@deck.gl/layers';
import type { Feature, LineString } from 'geojson';
import type { ViewState, StreetNetworkResponse, RoadProperties } from '../../types';
import 'maplibre-gl/dist/maplibre-gl.css';
import './StreetMap.css';

const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';

// Color scale for road types (hierarchy-based)
const ROAD_COLORS: Record<number, [number, number, number, number]> = {
  1: [139, 0, 0, 255],      // motorway - dark red
  2: [220, 20, 60, 255],    // trunk - crimson
  3: [255, 69, 0, 255],     // primary - orange red
  4: [255, 140, 0, 255],    // secondary - dark orange
  5: [255, 215, 0, 255],    // tertiary - gold
  6: [144, 238, 144, 255],  // residential - light green
  7: [152, 251, 152, 255],  // living_street - pale green
  8: [176, 196, 222, 255],  // unclassified - light steel blue
  9: [211, 211, 211, 255],  // service - light gray
  10: [230, 230, 250, 255], // pedestrian - lavender
};

const DEFAULT_ROAD_COLOR: [number, number, number, number] = [128, 128, 128, 200];

// Width scale for road types
const ROAD_WIDTHS: Record<number, number> = {
  1: 6,  // motorway
  2: 5,  // trunk
  3: 4,  // primary
  4: 3,  // secondary
  5: 2.5, // tertiary
  6: 2,  // residential
  7: 1.5, // living_street
  8: 1.5, // unclassified
  9: 1,  // service
  10: 1, // pedestrian
};

interface StreetMapProps {
  streetNetwork: StreetNetworkResponse | null;
  initialViewState?: ViewState;
  onViewStateChange?: (viewState: ViewState) => void;
  colorBy?: 'hierarchy' | 'traffic';
}

export function StreetMap({
  streetNetwork,
  initialViewState,
  onViewStateChange,
  colorBy = 'hierarchy',
}: StreetMapProps) {
  const [viewState, setViewState] = useState<ViewState>(
    initialViewState ?? {
      longitude: 19.0402,
      latitude: 47.4979,
      zoom: 12,
      pitch: 0,
      bearing: 0,
    }
  );

  const [hoveredFeature, setHoveredFeature] = useState<Feature<LineString, RoadProperties> | null>(
    null
  );

  // Sync with external viewState changes
  useEffect(() => {
    if (initialViewState) {
      setViewState(prev => ({
        ...prev,
        longitude: initialViewState.longitude,
        latitude: initialViewState.latitude,
        zoom: initialViewState.zoom,
      }));
    }
  }, [initialViewState?.longitude, initialViewState?.latitude, initialViewState?.zoom]);

  const handleViewStateChange = useCallback(
    ({ viewState: newViewState }: { viewState: ViewState }) => {
      setViewState(newViewState);
      onViewStateChange?.(newViewState);
    },
    [onViewStateChange]
  );

  const getLineColor = useCallback(
    (d: Feature<LineString, RoadProperties>): [number, number, number, number] => {
      const props = d.properties;
      if (!props) return DEFAULT_ROAD_COLOR;

      if (colorBy === 'traffic') {
        const intensity = props.traffic_intensity ?? 0;
        const r = Math.min(255, Math.floor((intensity / 100) * 255));
        const g = Math.min(255, Math.floor((1 - intensity / 100) * 200));
        return [r, g, 50, 220];
      }

      const hierarchy = props.hierarchy ?? 99;
      return ROAD_COLORS[hierarchy] ?? DEFAULT_ROAD_COLOR;
    },
    [colorBy]
  );

  const getLineWidth = useCallback((d: Feature<LineString, RoadProperties>): number => {
    const hierarchy = d.properties?.hierarchy ?? 8;
    return ROAD_WIDTHS[hierarchy] ?? 1.5;
  }, []);

  const layers = useMemo(() => {
    if (!streetNetwork?.features) return [];

    return [
      new GeoJsonLayer({
        id: 'street-network',
        data: streetNetwork,
        pickable: true,
        stroked: true,
        filled: false,
        lineWidthUnits: 'pixels',
        lineWidthScale: 1,
        lineWidthMinPixels: 1,
        getLineColor,
        getLineWidth,
        onHover: (info: { object?: unknown }) => {
          setHoveredFeature(info.object as Feature<LineString, RoadProperties> | null);
        },
        updateTriggers: {
          getLineColor: [colorBy],
        },
      }),
    ];
  }, [streetNetwork, getLineColor, getLineWidth, colorBy]);

  return (
    <div className="street-map">
      <DeckGL
        viewState={viewState}
        onViewStateChange={handleViewStateChange}
        controller={true}
        layers={layers}
      >
        <Map mapStyle={MAP_STYLE}>
          <NavigationControl position="bottom-right" />
          <ScaleControl position="bottom-left" />
        </Map>
      </DeckGL>

      {hoveredFeature && (
        <div className="tooltip">
          <div className="tooltip-title">
            {hoveredFeature.properties?.name ?? 'Unnamed road'}
          </div>
          <div className="tooltip-row">
            <span>Type:</span>
            <span>{hoveredFeature.properties?.highway}</span>
          </div>
          <div className="tooltip-row">
            <span>Lanes:</span>
            <span>{hoveredFeature.properties?.lanes ?? 1}</span>
          </div>
          {hoveredFeature.properties?.maxspeed && (
            <div className="tooltip-row">
              <span>Speed limit:</span>
              <span>{hoveredFeature.properties.maxspeed} km/h</span>
            </div>
          )}
          <div className="tooltip-row">
            <span>Capacity:</span>
            <span>{hoveredFeature.properties?.capacity} veh/h</span>
          </div>
          <div className="tooltip-row">
            <span>Est. volume:</span>
            <span>{hoveredFeature.properties?.estimated_volume} veh/h</span>
          </div>
        </div>
      )}

      {streetNetwork && (
        <div className="network-info">
          <div className="info-title">Network Stats</div>
          <div className="info-row">
            <span>Roads:</span>
            <span>{streetNetwork.metadata.total_edges}</span>
          </div>
          <div className="info-row">
            <span>Total length:</span>
            <span>{streetNetwork.metadata.total_length_km} km</span>
          </div>
          {streetNetwork.metadata.average_load !== undefined && (
            <div className="info-row">
              <span>Avg. load:</span>
              <span>{(streetNetwork.metadata.average_load * 100).toFixed(0)}%</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
