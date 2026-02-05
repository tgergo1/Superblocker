import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import MapGL, { NavigationControl, ScaleControl } from 'react-map-gl/maplibre';
import type { MapRef } from 'react-map-gl/maplibre';
import { GeoJsonLayer, PolygonLayer } from '@deck.gl/layers';
import { MapboxOverlay } from '@deck.gl/mapbox';
import type { Feature, LineString } from 'geojson';
import type { ViewState, StreetNetworkResponse, RoadProperties } from '../../types';
import type { SuperblockCandidate } from '../../services/api';
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

// Intervention type colors for road visualization
const INTERVENTION_COLORS: Record<string, [number, number, number, number]> = {
  pedestrianize: [34, 197, 94, 255],    // green - full pedestrianization
  one_way: [59, 130, 246, 255],         // blue - one-way conversion
  modal_filter: [251, 191, 36, 255],    // amber - modal filter
  local_access: [168, 85, 247, 255],    // purple - local access only
  no_change: [156, 163, 175, 200],      // gray - no change
};

// Score-based colors for superblocks (green = good, yellow = ok, red = poor)
function getScoreColor(score: number): [number, number, number, number] {
  if (score >= 70) return [34, 197, 94, 160];   // green
  if (score >= 50) return [234, 179, 8, 160];   // yellow
  return [239, 68, 68, 160];                     // red
}

interface StreetMapProps {
  streetNetwork: StreetNetworkResponse | null;
  superblocks?: SuperblockCandidate[];
  showSuperblocks?: boolean;
  initialViewState?: ViewState;
  onViewStateChange?: (viewState: ViewState) => void;
  colorBy?: 'hierarchy' | 'traffic' | 'interventions';
  onSuperblockClick?: (superblock: SuperblockCandidate) => void;
}

export function StreetMap({
  streetNetwork,
  superblocks,
  showSuperblocks = true,
  initialViewState,
  onViewStateChange,
  colorBy = 'hierarchy',
  onSuperblockClick,
}: StreetMapProps) {
  const mapRef = useRef<MapRef>(null);
  const [viewState, setViewState] = useState<ViewState>(
    initialViewState ?? {
      longitude: 19.0402,
      latitude: 47.4979,
      zoom: 12,
      pitch: 0,
      bearing: 0,
    }
  );

  const [hoveredFeature, setHoveredFeature] = useState<Feature<LineString, RoadProperties> | null>(null);
  const [hoveredSuperblock, setHoveredSuperblock] = useState<SuperblockCandidate | null>(null);
  const [selectedSuperblock, setSelectedSuperblock] = useState<SuperblockCandidate | null>(null);

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

  const handleMove = useCallback(
    (evt: { viewState: ViewState }) => {
      setViewState(evt.viewState);
      onViewStateChange?.(evt.viewState);
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

      if (colorBy === 'interventions' && selectedSuperblock) {
        const osmid = props.osmid;
        // Check if this road is in the selected superblock's interventions
        const intervention = selectedSuperblock.interventions?.find(i => i.osm_id === osmid);
        if (intervention) {
          return INTERVENTION_COLORS[intervention.intervention_type] ?? DEFAULT_ROAD_COLOR;
        }
        // Check if it's a perimeter or interior road
        if (selectedSuperblock.perimeter_roads?.includes(osmid)) {
          return INTERVENTION_COLORS.no_change;
        }
        if (selectedSuperblock.interior_roads?.includes(osmid)) {
          return [100, 100, 100, 180]; // Roads inside but not in interventions
        }
        // Roads outside the superblock - fade them
        return [200, 200, 200, 100];
      }

      const hierarchy = props.hierarchy ?? 99;
      return ROAD_COLORS[hierarchy] ?? DEFAULT_ROAD_COLOR;
    },
    [colorBy, selectedSuperblock]
  );

  const getLineWidth = useCallback((d: Feature<LineString, RoadProperties>): number => {
    const hierarchy = d.properties?.hierarchy ?? 8;
    return ROAD_WIDTHS[hierarchy] ?? 1.5;
  }, []);

  const layers = useMemo(() => {
    const result: unknown[] = [];

    // Superblock polygons layer (render first, below roads)
    if (showSuperblocks && superblocks && superblocks.length > 0) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      result.push(
        new PolygonLayer({
          id: 'superblocks',
          data: superblocks,
          pickable: true,
          stroked: true,
          filled: true,
          getPolygon: (d: SuperblockCandidate) => d.geometry.coordinates,
          getFillColor: (d: SuperblockCandidate) => {
            if (selectedSuperblock?.id === d.id) {
              return [59, 130, 246, 180]; // blue when selected
            }
            if (hoveredSuperblock?.id === d.id) {
              const color = getScoreColor(d.score);
              return [color[0], color[1], color[2], 200] as [number, number, number, number];
            }
            return getScoreColor(d.score);
          },
          getLineColor: (d: SuperblockCandidate) => {
            if (selectedSuperblock?.id === d.id) {
              return [37, 99, 235, 255]; // darker blue
            }
            return [0, 0, 0, 100];
          },
          getLineWidth: (d: SuperblockCandidate) =>
            selectedSuperblock?.id === d.id ? 3 : 1,
          onHover: (info: { object?: SuperblockCandidate }) => {
            setHoveredSuperblock(info.object ?? null);
          },
          onClick: (info: { object?: SuperblockCandidate }) => {
            if (info.object) {
              setSelectedSuperblock(
                selectedSuperblock?.id === info.object.id ? null : info.object
              );
              onSuperblockClick?.(info.object);
            }
          },
          updateTriggers: {
            getFillColor: [hoveredSuperblock?.id, selectedSuperblock?.id],
            getLineColor: [selectedSuperblock?.id],
            getLineWidth: [selectedSuperblock?.id],
          },
        } as any)
      );
    }

    // Street network layer
    if (streetNetwork?.features) {
      result.push(
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
            getLineColor: [colorBy, selectedSuperblock?.id],
          },
        })
      );
    }

    return result;
  }, [streetNetwork, superblocks, showSuperblocks, colorBy, getLineColor, getLineWidth, hoveredSuperblock, selectedSuperblock, onSuperblockClick]);

  // Store overlay reference
  const overlayRef = useRef<MapboxOverlay | null>(null);

  // Create deck overlay for maplibre
  const onMapLoad = useCallback(() => {
    const map = mapRef.current?.getMap();
    if (!map || overlayRef.current) return;

    const overlay = new MapboxOverlay({
      layers,
    });
    overlayRef.current = overlay;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    map.addControl(overlay as any);
  }, [layers]);

  // Update deck layers when they change
  useEffect(() => {
    if (overlayRef.current) {
      overlayRef.current.setProps({ layers });
    }
  }, [layers]);

  return (
    <div className="street-map">
      <MapGL
        ref={mapRef}
        {...viewState}
        onMove={handleMove}
        mapStyle={MAP_STYLE}
        onLoad={onMapLoad}
        style={{ width: '100%', height: '100%' }}
      >
        <NavigationControl position="bottom-right" />
        <ScaleControl position="bottom-left" />
      </MapGL>

      {/* Road tooltip */}
      {hoveredFeature && !hoveredSuperblock && (
        <div className="tooltip road-tooltip">
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

      {/* Superblock tooltip */}
      {hoveredSuperblock && (
        <div className="tooltip superblock-tooltip">
          <div className="tooltip-title">
            Superblock Candidate
          </div>
          <div className="tooltip-row">
            <span>Score:</span>
            <span className={`score score-${hoveredSuperblock.score >= 70 ? 'good' : hoveredSuperblock.score >= 50 ? 'ok' : 'poor'}`}>
              {hoveredSuperblock.score}/100
            </span>
          </div>
          <div className="tooltip-row">
            <span>Area:</span>
            <span>{hoveredSuperblock.area_hectares} ha</span>
          </div>
          <div className="tooltip-row">
            <span>Interior roads:</span>
            <span>{hoveredSuperblock.interior_roads.length}</span>
          </div>
          <div className="tooltip-hint">Click to select</div>
        </div>
      )}

      {/* Selected superblock details */}
      {selectedSuperblock && (
        <div className="superblock-details">
          <div className="details-header">
            <span className="details-title">Superblock Analysis</span>
            <button
              className="close-button"
              onClick={() => setSelectedSuperblock(null)}
            >
              ×
            </button>
          </div>
          <div className="details-body">
            {/* Overall score */}
            <div className="detail-row">
              <span>Overall Score:</span>
              <span className={`score score-${selectedSuperblock.score >= 70 ? 'good' : selectedSuperblock.score >= 50 ? 'ok' : 'poor'}`}>
                {selectedSuperblock.score}/100
              </span>
            </div>
            <div className="detail-row">
              <span>Area:</span>
              <span>{selectedSuperblock.area_hectares} ha</span>
            </div>

            {/* Score breakdown */}
            {selectedSuperblock.score_breakdown && (
              <>
                <div className="details-section-title">Score Breakdown</div>
                <div className="score-breakdown">
                  <div className="score-item">
                    <span>Size</span>
                    <div className="score-bar">
                      <div className="score-fill" style={{ width: `${selectedSuperblock.score_breakdown.size_score}%` }} />
                    </div>
                    <span>{selectedSuperblock.score_breakdown.size_score}</span>
                  </div>
                  <div className="score-item">
                    <span>Shape</span>
                    <div className="score-bar">
                      <div className="score-fill" style={{ width: `${selectedSuperblock.score_breakdown.shape_score}%` }} />
                    </div>
                    <span>{selectedSuperblock.score_breakdown.shape_score}</span>
                  </div>
                  <div className="score-item">
                    <span>Traffic</span>
                    <div className="score-bar">
                      <div className="score-fill" style={{ width: `${selectedSuperblock.score_breakdown.traffic_score}%` }} />
                    </div>
                    <span>{selectedSuperblock.score_breakdown.traffic_score}</span>
                  </div>
                  <div className="score-item">
                    <span>Access</span>
                    <div className="score-bar">
                      <div className="score-fill" style={{ width: `${selectedSuperblock.score_breakdown.accessibility_score}%` }} />
                    </div>
                    <span>{selectedSuperblock.score_breakdown.accessibility_score}</span>
                  </div>
                  <div className="score-item">
                    <span>Connect</span>
                    <div className="score-bar">
                      <div className="score-fill" style={{ width: `${selectedSuperblock.score_breakdown.connectivity_score}%` }} />
                    </div>
                    <span>{selectedSuperblock.score_breakdown.connectivity_score}</span>
                  </div>
                  <div className="score-item">
                    <span>Boundary</span>
                    <div className="score-bar">
                      <div className="score-fill" style={{ width: `${selectedSuperblock.score_breakdown.boundary_quality_score}%` }} />
                    </div>
                    <span>{selectedSuperblock.score_breakdown.boundary_quality_score}</span>
                  </div>
                </div>
              </>
            )}

            {/* Traffic impact */}
            {selectedSuperblock.traffic_impact && (
              <>
                <div className="details-section-title">Traffic Impact</div>
                <div className="detail-row">
                  <span>Through-traffic removed:</span>
                  <span className="score-good">{selectedSuperblock.traffic_impact.removed_through_traffic_pct}%</span>
                </div>
                <div className="detail-row">
                  <span>Boundary load increase:</span>
                  <span>{selectedSuperblock.traffic_impact.boundary_load_increase_pct}%</span>
                </div>
              </>
            )}

            {/* Interventions summary */}
            {selectedSuperblock.interventions && selectedSuperblock.interventions.length > 0 && (
              <>
                <div className="details-section-title">Planned Interventions</div>
                <div className="interventions-summary">
                  {(() => {
                    const counts = selectedSuperblock.interventions.reduce((acc, i) => {
                      acc[i.intervention_type] = (acc[i.intervention_type] || 0) + 1;
                      return acc;
                    }, {} as Record<string, number>);
                    return (
                      <>
                        {counts.pedestrianize && (
                          <div className="intervention-badge pedestrianize">
                            {counts.pedestrianize} pedestrian
                          </div>
                        )}
                        {counts.one_way && (
                          <div className="intervention-badge one-way">
                            {counts.one_way} one-way
                          </div>
                        )}
                        {counts.modal_filter && (
                          <div className="intervention-badge modal-filter">
                            {counts.modal_filter} filtered
                          </div>
                        )}
                        {counts.local_access && (
                          <div className="intervention-badge local-access">
                            {counts.local_access} local only
                          </div>
                        )}
                      </>
                    );
                  })()}
                </div>
              </>
            )}

            {/* Network info */}
            <div className="details-section-title">Network</div>
            <div className="detail-row">
              <span>Interior roads:</span>
              <span>{selectedSuperblock.interior_roads.length}</span>
            </div>
            <div className="detail-row">
              <span>Boundary roads:</span>
              <span>{selectedSuperblock.perimeter_roads.length}</span>
            </div>
            <div className="detail-row">
              <span>Access points:</span>
              <span>{selectedSuperblock.num_access_points ?? 'N/A'}</span>
            </div>
          </div>
        </div>
      )}

      {/* Map Legend */}
      {colorBy === 'interventions' && selectedSuperblock && (
        <div className="map-legend interventions-legend">
          <div className="legend-title">Street Interventions</div>
          <div className="legend-item">
            <span className="legend-color" style={{ background: 'rgb(34, 197, 94)' }} />
            <span className="legend-label">Pedestrianize</span>
          </div>
          <div className="legend-item">
            <span className="legend-color" style={{ background: 'rgb(59, 130, 246)' }} />
            <span className="legend-label">One-way</span>
          </div>
          <div className="legend-item">
            <span className="legend-color" style={{ background: 'rgb(251, 191, 36)' }} />
            <span className="legend-label">Modal filter</span>
          </div>
          <div className="legend-item">
            <span className="legend-color" style={{ background: 'rgb(168, 85, 247)' }} />
            <span className="legend-label">Local access</span>
          </div>
          <div className="legend-item">
            <span className="legend-color" style={{ background: 'rgb(156, 163, 175)' }} />
            <span className="legend-label">No change</span>
          </div>
        </div>
      )}

      {colorBy === 'hierarchy' && (
        <div className="map-legend hierarchy-legend">
          <div className="legend-title">Road Types</div>
          <div className="legend-item">
            <span className="legend-color" style={{ background: 'rgb(139, 0, 0)' }} />
            <span className="legend-label">Motorway</span>
          </div>
          <div className="legend-item">
            <span className="legend-color" style={{ background: 'rgb(255, 69, 0)' }} />
            <span className="legend-label">Primary</span>
          </div>
          <div className="legend-item">
            <span className="legend-color" style={{ background: 'rgb(255, 140, 0)' }} />
            <span className="legend-label">Secondary</span>
          </div>
          <div className="legend-item">
            <span className="legend-color" style={{ background: 'rgb(255, 215, 0)' }} />
            <span className="legend-label">Tertiary</span>
          </div>
          <div className="legend-item">
            <span className="legend-color" style={{ background: 'rgb(144, 238, 144)' }} />
            <span className="legend-label">Residential</span>
          </div>
        </div>
      )}

      {colorBy === 'traffic' && (
        <div className="map-legend traffic-legend">
          <div className="legend-title">Traffic Intensity</div>
          <div className="legend-gradient">
            <div className="gradient-bar" />
            <div className="gradient-labels">
              <span>Low</span>
              <span>High</span>
            </div>
          </div>
        </div>
      )}

      {/* Network stats */}
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
