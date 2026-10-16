import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import MapGL, { AttributionControl, NavigationControl, ScaleControl } from 'react-map-gl/maplibre';
import type { MapRef } from 'react-map-gl/maplibre';
import type { IControl, StyleSpecification } from 'maplibre-gl';
import { GeoJsonLayer, PolygonLayer, ScatterplotLayer, PathLayer, TextLayer } from '@deck.gl/layers';
import { MapboxOverlay } from '@deck.gl/mapbox';
import type { LayersList } from '@deck.gl/core';
import type { Feature, Geometry, LineString, MultiPolygon, Polygon } from 'geojson';
import type { ViewState, StreetNetworkResponse, RoadProperties, EnforcedSuperblock, CityPartition, RouteResult, BoundaryRoadImpact, SchoolStreet } from '../../types';
import type { SuperblockCandidate } from '../../services/api';
import { IMPACT_BANDS, impactColor, impactPercent } from '../../utils/impact';
import 'maplibre-gl/dist/maplibre-gl.css';
import './StreetMap.css';

// Keep the style definition local so a failed remote style document cannot leave
// the application with a blank canvas. OpenStreetMap's raster tiles require no
// API key; deployments can point at an OSM-compatible tile service via the env var.
const OPENSTREETMAP_TILE_URL =
  import.meta.env.VITE_OSM_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';

const MAP_STYLE: StyleSpecification = {
  version: 8,
  name: 'Superblocker OpenStreetMap',
  sources: {
    openstreetmap: {
      type: 'raster',
      tiles: [OPENSTREETMAP_TILE_URL],
      tileSize: 256,
      attribution: '&copy; OpenStreetMap contributors',
      maxzoom: 19,
    },
  },
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#eef2f6' },
    },
    {
      id: 'openstreetmap',
      type: 'raster',
      source: 'openstreetmap',
      minzoom: 0,
      maxzoom: 22,
      paint: {
        'raster-opacity': 1,
        'raster-fade-duration': 120,
      },
    },
  ],
};

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

type PlanActionType = 'modal_filter' | 'bus_gate' | 'one_way' | 'two_way' | 'turn_restriction' | 'full_closure';

interface PlanMarkerDatum {
  position: [number, number];
  name: string;
  title: string;
  symbol: string;
  actionType: PlanActionType | 'entry_point' | 'school' | 'pilot';
  angle?: number;
  detail?: string;
}

// One visual language for modified road lines, map signs, hover labels, and the
// legend. Keep these values in sync with the plan-sign CSS classes.
const PLAN_ACTION_STYLES: Record<PlanActionType, {
  line: [number, number, number, number];
  markerFill: [number, number, number, number];
  markerLine: [number, number, number, number];
  text: [number, number, number, number];
  symbol: string;
  title: string;
}> = {
  modal_filter: {
    line: [220, 38, 38, 255],
    markerFill: [220, 38, 38, 255],
    markerLine: [255, 255, 255, 255],
    text: [255, 255, 255, 255],
    symbol: 'X',
    title: 'Modal filter',
  },
  bus_gate: {
    line: [4, 120, 87, 255],
    markerFill: [4, 120, 87, 255],
    markerLine: [255, 255, 255, 255],
    text: [255, 255, 255, 255],
    symbol: 'B',
    title: 'Bus gate',
  },
  one_way: {
    line: [37, 99, 235, 255],
    markerFill: [255, 255, 255, 245],
    markerLine: [37, 99, 235, 255],
    text: [30, 64, 175, 255],
    symbol: '>',
    title: 'One-way conversion',
  },
  two_way: {
    line: [13, 148, 136, 255],
    markerFill: [255, 255, 255, 245],
    markerLine: [13, 148, 136, 255],
    text: [15, 118, 110, 255],
    symbol: '<>',
    title: 'Two-way local access',
  },
  turn_restriction: {
    line: [217, 119, 6, 255],
    markerFill: [217, 119, 6, 255],
    markerLine: [255, 255, 255, 255],
    text: [255, 255, 255, 255],
    symbol: '!',
    title: 'Turn restriction',
  },
  full_closure: {
    line: [124, 58, 237, 255],
    markerFill: [124, 58, 237, 255],
    markerLine: [255, 255, 255, 255],
    text: [255, 255, 255, 255],
    symbol: '=',
    title: 'Street cut',
  },
};

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

// Superblock colors for visual distinction
const SUPERBLOCK_COLORS: [number, number, number, number][] = [
  [99, 102, 241, 255],   // Indigo
  [236, 72, 153, 255],   // Pink
  [34, 197, 94, 255],    // Green
  [249, 115, 22, 255],   // Orange
  [14, 165, 233, 255],   // Sky
  [168, 85, 247, 255],   // Purple
  [20, 184, 166, 255],   // Teal
  [245, 158, 11, 255],   // Amber
  [239, 68, 68, 255],    // Red
  [59, 130, 246, 255],   // Blue
];

const MUTED_ROAD_COLOR: [number, number, number, number] = [148, 163, 184, 120];
const POOR_TRANSIT_COLOR: [number, number, number, number] = [147, 51, 234, 220]; // Purple
// Matches POORLY_SERVED_SHARE in the backend transit access check.
const POORLY_SERVED_SHARE = 0.5;

// Route colors
const ROUTE_ARTERIAL_COLOR: [number, number, number, number] = [59, 130, 246, 255]; // Blue
const ROUTE_INTERIOR_COLOR: [number, number, number, number] = [34, 197, 94, 255]; // Green
const DEFAULT_VIEW_STATE: ViewState = {
  longitude: 19.0402,
  latitude: 47.4979,
  zoom: 12,
  pitch: 0,
  bearing: 0,
};

interface StreetMapProps {
  streetNetwork: StreetNetworkResponse | null;
  superblocks?: SuperblockCandidate[];
  showSuperblocks?: boolean;
  initialViewState?: ViewState;
  onViewStateChange?: (viewState: ViewState) => void;
  colorBy?: 'hierarchy' | 'traffic' | 'interventions' | 'impact';
  impactScenario?: string;
  onSuperblockClick?: (superblock: SuperblockCandidate) => void;
  // New partitioning system props
  partition?: CityPartition | null;
  showPartition?: boolean;
  showEntryPoints?: boolean;
  showModalFilters?: boolean;
  selectedEnforcedSuperblock?: EnforcedSuperblock | null;
  onEnforcedSuperblockClick?: (superblock: EnforcedSuperblock) => void;
  // Routing props
  route?: RouteResult | null;
  showRoute?: boolean;
  selectedPlaceName?: string;
  analysisBoundary?: Polygon | MultiPolygon | null;
  emptyStateMessage?: string;
  hideLegends?: boolean;
  showPilotRanking?: boolean;
}

export function StreetMap({
  streetNetwork,
  superblocks,
  showSuperblocks = true,
  initialViewState,
  onViewStateChange,
  colorBy = 'hierarchy',
  impactScenario = 'central',
  onSuperblockClick,
  partition,
  showPartition = false,
  showEntryPoints = true,
  showModalFilters = true,
  selectedEnforcedSuperblock,
  onEnforcedSuperblockClick,
  route,
  showRoute = true,
  selectedPlaceName,
  analysisBoundary,
  emptyStateMessage,
  hideLegends = false,
  showPilotRanking = false,
}: StreetMapProps) {
  const mapRef = useRef<MapRef>(null);
  const [mapReady, setMapReady] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);

  // Panel collapse states
  const [legendCollapsed, setLegendCollapsed] = useState(false);
  const [roadLegendCollapsed, setRoadLegendCollapsed] = useState(true);
  const [infoCollapsed, setInfoCollapsed] = useState(true);

  const [internalViewState, setInternalViewState] = useState<ViewState>(
    initialViewState ?? DEFAULT_VIEW_STATE
  );

  const [hoveredFeature, setHoveredFeature] = useState<Feature<LineString, RoadProperties> | null>(null);
  const [hoveredSuperblock, setHoveredSuperblock] = useState<SuperblockCandidate | null>(null);
  const [hoveredPlanMarker, setHoveredPlanMarker] = useState<PlanMarkerDatum | null>(null);
  const [selectedSuperblock, setSelectedSuperblock] = useState<SuperblockCandidate | null>(null);

  const viewState = initialViewState ?? internalViewState;
  const showDetailedPlanSigns = viewState.zoom >= 12;
  const boundaryRoadWidth = viewState.zoom < 10 ? 1.35 : viewState.zoom < 12 ? 2.25 : 4;

  const handleMove = useCallback(
    (evt: { viewState: ViewState }) => {
      if (!initialViewState) {
        setInternalViewState(evt.viewState);
      }
      onViewStateChange?.(evt.viewState);
    },
    [initialViewState, onViewStateChange]
  );

  // Build a map of modified streets from partition data for quick lookup
  const modifiedStreets = useMemo(() => {
    if (!showPartition || !showModalFilters || !partition) {
      return new Map<string, { type: string; direction: string | null }>();
    }

    const map = new Map<string, { type: string; direction: string | null }>();
    partition.superblocks.forEach(sb => {
      sb.modifications.forEach(mod => {
        const display = {
          type: mod.filter_type === 'bus_gate' ? 'bus_gate' : mod.modification_type,
          direction: mod.direction
        };
        if (mod.modification_type !== 'one_way' || mod.direction === 'u_to_v') {
          map.set(`${mod.u}:${mod.v}:key:${mod.key}`, display);
        }
        if (mod.modification_type !== 'one_way' || mod.direction === 'v_to_u') {
          map.set(`${mod.v}:${mod.u}:osm:${mod.osm_id}`, display);
        }
      });
    });
    return map;
  }, [showModalFilters, showPartition, partition]);

  // Superblocks where less than half the streets are a short walk from a stop.
  const poorTransitIds = useMemo(() => {
    const ids = new Set<string>();
    const access = partition?.transit_access;
    if (!access?.checked) return ids;
    access.superblocks.forEach(item => {
      if (item.covered_share < POORLY_SERVED_SHARE) ids.add(item.superblock_id);
    });
    return ids;
  }, [partition]);

  const impactByOsmId = useMemo(() => {
    const map = new Map<number, BoundaryRoadImpact>();
    if (colorBy !== 'impact' || !partition?.traffic_impact) return map;
    partition.traffic_impact.boundary_roads.forEach(road => map.set(road.osm_id, road));
    return map;
  }, [colorBy, partition]);

  const getRoadImpact = useCallback((props: RoadProperties | null | undefined) => {
    if (!props || impactByOsmId.size === 0) return undefined;
    const osmids = Array.isArray(props.osmid) ? props.osmid : [props.osmid];
    for (const osmid of osmids) {
      const road = impactByOsmId.get(osmid);
      if (road) return road;
    }
    return undefined;
  }, [impactByOsmId]);

  const getStreetModification = useCallback((props: RoadProperties | null | undefined) => {
    if (!props) return undefined;
    const osmids = Array.isArray(props.osmid) ? props.osmid : [props.osmid];
    const exact = modifiedStreets.get(`${props.u}:${props.v}:key:${props.key}`);
    if (exact) return exact;
    for (const osmid of osmids) {
      const byWayAndDirection = modifiedStreets.get(`${props.u}:${props.v}:osm:${osmid}`);
      if (byWayAndDirection) return byWayAndDirection;
    }
    return undefined;
  }, [modifiedStreets]);

  const getLineColor = useCallback(
    (d: Feature<Geometry, RoadProperties>): [number, number, number, number] => {
      const props = d.properties;
      if (!props) return DEFAULT_ROAD_COLOR;

      if (colorBy === 'impact') {
        const road = getRoadImpact(props);
        return road ? impactColor(impactPercent(road, impactScenario)) : MUTED_ROAD_COLOR;
      }

      if (colorBy === 'traffic') {
        const intensity = props.traffic_intensity ?? 0;
        const r = Math.min(255, Math.floor((intensity / 100) * 255));
        const g = Math.min(255, Math.floor((1 - intensity / 100) * 200));
        return [r, g, 50, 220];
      }

      if (colorBy === 'interventions' && selectedSuperblock) {
        // Handle osmid being a single number or an array
        let osmid = props.osmid;
        if (Array.isArray(osmid)) {
          osmid = osmid[0];
        }
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

      // Highlight modified streets in partition mode
      if (showPartition && modifiedStreets.size > 0) {
        // Handle osmid being a single number or an array
        const mod = getStreetModification(props);
        if (mod) {
          return PLAN_ACTION_STYLES[mod.type as PlanActionType]?.line
            ?? [217, 119, 6, 255];
        }
      }

      const hierarchy = props.hierarchy ?? 99;
      return ROAD_COLORS[hierarchy] ?? DEFAULT_ROAD_COLOR;
    },
    [colorBy, selectedSuperblock, showPartition, modifiedStreets, getStreetModification, getRoadImpact, impactScenario]
  );

  const getLineWidth = useCallback((d: Feature<Geometry, RoadProperties>): number => {
    const hierarchy = d.properties?.hierarchy ?? 8;
    const base = ROAD_WIDTHS[hierarchy] ?? 1.5;
    if (colorBy === 'impact') {
      const road = getRoadImpact(d.properties);
      if (!road) return Math.min(base, 1.5);
      const added = road.added_peak_vph[impactScenario] ?? 0;
      return base + Math.min(6, Math.sqrt(added) / 4);
    }
    return base;
  }, [colorBy, getRoadImpact, impactScenario]);

  const layers = useMemo(() => {
    const result: LayersList = [];

    if (analysisBoundary) {
      result.push(
        new PolygonLayer<Polygon | MultiPolygon>({
          id: 'administrative-analysis-boundary',
          data: [analysisBoundary],
          pickable: false,
          stroked: true,
          filled: true,
          getPolygon: geometry => geometry.coordinates,
          getFillColor: [37, 99, 235, 13],
          getLineColor: [29, 78, 216, 220],
          getLineWidth: 3,
          lineWidthMinPixels: 2,
        }),
      );
    }

    // Partition fills render below the road network so streets, labels, and
    // access changes remain readable at every zoom level.
    if (showPartition && partition && partition.superblocks.length > 0) {
      result.push(
        new PolygonLayer({
          id: 'enforced-superblocks',
          data: partition.superblocks,
          pickable: true,
          stroked: true,
          filled: true,
          // Outline widths are screen pixels so they stay visible at city zoom.
          lineWidthUnits: 'pixels',
          getPolygon: (d: EnforcedSuperblock) => d.geometry.coordinates,
          getFillColor: (d: EnforcedSuperblock) => {
            if (selectedEnforcedSuperblock?.id === d.id) {
              return [59, 130, 246, 82];
            }
            if (d.modeled_directional_validation_passed && d.all_access_targets_reachable) {
              return [34, 197, 94, 48];
            }
            if (d.modeled_directional_validation_passed) {
              return [251, 191, 36, 54];
            }
            return [239, 68, 68, 62];
          },
          getLineColor: (d: EnforcedSuperblock) =>
            selectedEnforcedSuperblock?.id === d.id
              ? [37, 99, 235, 235]
              : poorTransitIds.has(d.id)
                ? POOR_TRANSIT_COLOR
                : [51, 65, 85, 92],
          getLineWidth: (d: EnforcedSuperblock) =>
            selectedEnforcedSuperblock?.id === d.id ? 3 : poorTransitIds.has(d.id) ? 2.5 : 1,
          onClick: (info: { object?: EnforcedSuperblock }) => {
            if (info.object) onEnforcedSuperblockClick?.(info.object);
          },
          updateTriggers: {
            getFillColor: [selectedEnforcedSuperblock?.id],
            getLineColor: [selectedEnforcedSuperblock?.id, poorTransitIds],
            getLineWidth: [selectedEnforcedSuperblock?.id, poorTransitIds],
          },
        })
      );
    }

    // Superblock polygons layer (render first, below roads)
    if (showSuperblocks && superblocks && superblocks.length > 0) {
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
        })
      );

    }

    // Street network layer
    if (streetNetwork?.features) {
      result.push(
        new GeoJsonLayer<RoadProperties>({
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
            getLineColor: [colorBy, impactScenario, selectedSuperblock?.id, showPartition, partition?.total_superblocks],
            getLineWidth: [colorBy, impactScenario, partition?.total_superblocks],
          },
        })
      );

      if (showPartition && colorBy !== 'impact' && partition?.arterial_network.length) {
        const boundaryRoadIds = new Set(partition.arterial_network);
        const boundaryFeatures = streetNetwork.features.filter((feature) => {
          const osmids = Array.isArray(feature.properties.osmid)
            ? feature.properties.osmid
            : [feature.properties.osmid];
          return osmids.some((osmid) => boundaryRoadIds.has(osmid));
        });
        const boundaryData = {
          type: 'FeatureCollection' as const,
          features: boundaryFeatures,
        };

        if (boundaryFeatures.length > 0) {
          result.push(
            new GeoJsonLayer<RoadProperties>({
              id: 'boundary-road-halo',
              data: boundaryData,
              pickable: false,
              stroked: true,
              filled: false,
              lineWidthUnits: 'pixels',
              getLineColor: [255, 255, 255, 225],
              getLineWidth: boundaryRoadWidth + 3,
            }),
            new GeoJsonLayer<RoadProperties>({
              id: 'cross-traffic-boundary-roads',
              data: boundaryData,
              pickable: false,
              stroked: true,
              filled: false,
              lineWidthUnits: 'pixels',
              getLineColor: [30, 64, 175, 245],
              getLineWidth: boundaryRoadWidth,
            }),
          );
        }
      }

      // Add one clear, hoverable sign per modified road segment. The symbol,
      // line color, marker color, and legend all come from PLAN_ACTION_STYLES.
      if (showPartition && partition && modifiedStreets.size > 0 && showDetailedPlanSigns) {
        const markerIndex = new Map<string, PlanMarkerDatum>();
        streetNetwork.features.forEach(f => {
          const modification = getStreetModification(f.properties);
          if (!modification || !(modification.type in PLAN_ACTION_STYLES)) return;
          const coords = f.geometry.coordinates;
          if (coords.length === 0) return;

          const actionType = modification.type as PlanActionType;
          const style = PLAN_ACTION_STYLES[actionType];
          const midIndex = Math.floor((coords.length - 1) / 2);
          const point = coords[midIndex] as [number, number];
          const prevPoint = coords[Math.max(0, midIndex - 1)] ?? point;
          const nextPoint = coords[Math.min(coords.length - 1, midIndex + 1)] ?? point;
          const angle = actionType === 'one_way'
            ? Math.atan2(nextPoint[1] - prevPoint[1], nextPoint[0] - prevPoint[0])
              * (180 / Math.PI) + (modification.direction === 'v_to_u' ? 180 : 0)
            : 0;
          const marker: PlanMarkerDatum = {
            position: point,
            angle,
            name: f.properties?.name || `OSM road ${f.properties?.osmid ?? ''}`.trim(),
            title: style.title,
            symbol: style.symbol,
            actionType,
            detail: actionType === 'one_way'
              ? 'Follow the marked direction; return to the same boundary side.'
              : actionType === 'two_way'
                ? 'Both local directions remain available inside this territory.'
                : 'Motor through-traffic stops at this marked point.',
          };
          const markerKey = `${actionType}:${point[0].toFixed(6)}:${point[1].toFixed(6)}`;
          markerIndex.set(markerKey, marker);
        });

        (Object.keys(PLAN_ACTION_STYLES) as PlanActionType[]).forEach(actionType => {
          const markerData = Array.from(markerIndex.values()).filter(
            marker => marker.actionType === actionType,
          );
          if (markerData.length === 0) return;
          const style = PLAN_ACTION_STYLES[actionType];

          result.push(
            new ScatterplotLayer<PlanMarkerDatum>({
              id: `${actionType}-road-markers`,
              data: markerData,
              pickable: true,
              opacity: 1,
              stroked: true,
              filled: true,
              radiusScale: 1,
              radiusMinPixels: 9,
              radiusMaxPixels: 14,
              getPosition: marker => marker.position,
              getFillColor: style.markerFill,
              getLineColor: style.markerLine,
              getRadius: 11,
              lineWidthMinPixels: 2,
              onHover: (info: { object?: PlanMarkerDatum }) => {
                setHoveredPlanMarker(info.object ?? null);
              },
            }),
            new TextLayer<PlanMarkerDatum>({
              id: `${actionType}-road-labels`,
              data: markerData,
              pickable: false,
              getPosition: marker => marker.position,
              getText: marker => marker.symbol,
              getSize: actionType === 'two_way' ? 11 : 14,
              getAngle: marker => actionType === 'one_way' ? -(marker.angle ?? 0) : 0,
              getColor: style.text,
              getTextAnchor: 'middle',
              getAlignmentBaseline: 'center',
              fontFamily: 'Arial, Helvetica, sans-serif',
              fontWeight: 'bold',
              billboard: actionType !== 'one_way',
              sizeMinPixels: actionType === 'two_way' ? 9 : 11,
              sizeMaxPixels: actionType === 'two_way' ? 13 : 17,
            }),
          );
        });
      }
    }

    // Candidate plazas: junctions whose every leg is a calmed interior street.
    if (showPartition && partition?.public_space && showDetailedPlanSigns && colorBy !== 'impact') {
      const plazas = partition.public_space.superblocks.flatMap(item =>
        item.plaza_candidates.map(point => ({ position: [point.lon, point.lat] as [number, number] })),
      );
      if (plazas.length > 0) {
        result.push(
          new ScatterplotLayer<{ position: [number, number] }>({
            id: 'plaza-candidates',
            data: plazas,
            pickable: false,
            stroked: true,
            filled: true,
            radiusUnits: 'meters',
            getRadius: 14,
            radiusMinPixels: 5,
            getPosition: d => d.position,
            getFillColor: [34, 197, 94, 120],
            getLineColor: [21, 128, 61, 230],
            lineWidthMinPixels: 1.5,
          }),
        );
      }
    }

    // Schools: timed school streets inside superblocks, crossings on main roads.
    if (showPartition && partition?.school_streets && showDetailedPlanSigns && colorBy !== 'impact') {
      const schools = partition.school_streets.schools.filter(item => item.setting !== 'unassigned');
      const schoolStreets = schools.filter(item => item.setting === 'interior' && item.street_path.length > 1);
      if (schoolStreets.length > 0) {
        result.push(
          new PathLayer<SchoolStreet>({
            id: 'school-streets',
            data: schoolStreets,
            pickable: false,
            widthUnits: 'pixels',
            getWidth: 6,
            getPath: item => item.street_path.map(point => [point.lon, point.lat] as [number, number]),
            getColor: [249, 115, 22, 200],
            capRounded: true,
          }),
        );
      }
      if (schools.length > 0) {
        const markers: PlanMarkerDatum[] = schools.map(item => ({
          position: [item.coordinates.lon, item.coordinates.lat],
          name: item.name ?? (item.kind === 'kindergarten' ? 'Kindergarten' : 'School'),
          title: item.setting === 'interior' ? 'School street' : 'School on a main road',
          symbol: 'S',
          actionType: 'school',
          detail: item.treatment,
        }));
        result.push(
          new ScatterplotLayer<PlanMarkerDatum>({
            id: 'school-markers',
            data: markers,
            pickable: true,
            stroked: true,
            filled: true,
            radiusUnits: 'pixels',
            getRadius: 9,
            getPosition: d => d.position,
            getFillColor: d => d.title === 'School street' ? [249, 115, 22, 235] : [255, 255, 255, 235],
            getLineColor: [194, 65, 12, 255],
            lineWidthMinPixels: 2,
            onHover: (info: { object?: PlanMarkerDatum }) => {
              setHoveredPlanMarker(info.object ?? null);
            },
          }),
          new TextLayer<PlanMarkerDatum>({
            id: 'school-labels',
            data: markers,
            pickable: false,
            getPosition: d => d.position,
            getText: d => d.symbol,
            getSize: 12,
            getColor: d => d.title === 'School street' ? [255, 255, 255, 255] : [194, 65, 12, 255],
            getTextAnchor: 'middle',
            getAlignmentBaseline: 'center',
            fontFamily: 'Arial, Helvetica, sans-serif',
            fontWeight: 'bold',
          }),
        );
      }
    }

    // Entry points layer - simple markers at superblock entries
    if (showPartition && showEntryPoints && partition && showDetailedPlanSigns) {
      const entryPointData = partition.superblocks.flatMap((sb, sbIndex) =>
        sb.entry_points.map(ep => ({
          ...ep,
          superblockId: sb.id,
          superblockIndex: sbIndex,
        }))
      );
      const directionLabelData = partition.superblocks.flatMap((sb, sbIndex) => {
        const labels: (typeof entryPointData)[number][] = [];
        for (let sector = 0; sector < sb.num_sectors; sector += 1) {
          const entries = sb.entry_points.filter((entry) => entry.sector === sector);
          if (entries.length === 0) continue;
          const representative = entries.reduce((best, entry) => {
            if (sector === 0) return entry.coordinates.lon > best.coordinates.lon ? entry : best;
            if (sector === 1) return entry.coordinates.lat > best.coordinates.lat ? entry : best;
            if (sector === 2) return entry.coordinates.lon < best.coordinates.lon ? entry : best;
            return entry.coordinates.lat < best.coordinates.lat ? entry : best;
          });
          labels.push({
            ...representative,
            superblockId: sb.id,
            superblockIndex: sbIndex,
          });
        }
        return labels;
      });

      if (entryPointData.length > 0) {
        // Entry point circles - colored by superblock
        result.push(
          new ScatterplotLayer({
            id: 'entry-points',
            data: entryPointData,
            pickable: true,
            opacity: 0.95,
            stroked: true,
            filled: true,
            radiusScale: 1,
            radiusMinPixels: 4,
            radiusMaxPixels: 8,
            getPosition: (d: typeof entryPointData[0]) => [d.coordinates.lon, d.coordinates.lat],
            getFillColor: (d: typeof entryPointData[0]) =>
              SUPERBLOCK_COLORS[d.superblockIndex % SUPERBLOCK_COLORS.length],
            getLineColor: [255, 255, 255, 255],
            getRadius: 6,
            lineWidthMinPixels: 1.5,
            onHover: (info: { object?: (typeof entryPointData)[number] }) => {
              const entry = info.object;
              if (!entry) {
                setHoveredPlanMarker(null);
                return;
              }
              const side = partition.superblocks[entry.superblockIndex]?.num_sectors === 4
                ? ['East', 'North', 'West', 'South'][entry.sector] ?? `Sector ${entry.sector + 1}`
                : `Sector ${entry.sector + 1}`;
              setHoveredPlanMarker({
                position: [entry.coordinates.lon, entry.coordinates.lat],
                name: `Superblock ${entry.superblockIndex + 1}`,
                title: `${side} entry and return point`,
                symbol: side.charAt(0),
                actionType: 'entry_point',
                detail: 'Vehicles using this entry must return to the same boundary side.',
              });
            },
          })
        );

        // Atlas-safe cardinal label communicates the side to which a vehicle
        // must return. (The default deck.gl atlas omits Unicode arrows.)
        result.push(
          new TextLayer({
            id: 'entry-point-labels',
            data: directionLabelData,
            pickable: false,
            getPosition: (d: typeof entryPointData[0]) => [d.coordinates.lon, d.coordinates.lat],
            getText: (d: typeof entryPointData[0]) => {
              if (partition.superblocks[d.superblockIndex]?.num_sectors === 4) {
                return ['E', 'N', 'W', 'S'][d.sector] ?? '·';
              }
              return '·';
            },
            getSize: 11,
            getColor: [255, 255, 255, 255],
            getTextAnchor: 'middle',
            getAlignmentBaseline: 'center',
            fontFamily: 'Arial, Helvetica, sans-serif',
            fontWeight: 'bold',
            sizeMinPixels: 8,
            sizeMaxPixels: 10,
          })
        );
      }
    }

    // Pilot ranking: numbered badges at the best places to start, at any zoom.
    if (showPartition && showPilotRanking && partition?.pilot_plan && colorBy !== 'impact') {
      const badges: PlanMarkerDatum[] = partition.pilot_plan.candidates
        .slice(0, 10)
        .filter(item => item.centroid_lat != null && item.centroid_lon != null)
        .map(item => ({
          position: [item.centroid_lon as number, item.centroid_lat as number],
          name: `Superblock ${item.superblock_id}`,
          title: `Pilot #${item.rank}`,
          symbol: String(item.rank),
          actionType: 'pilot',
          detail:
            `${Math.round(item.displaced_peak_vph)} veh/h of through traffic removed; trial with ` +
            `${item.trial_measures} removable measures for about €${Math.round(item.trial_cost_eur / 1000)}k; ` +
            `interior injury risk −${item.interior_injury_reduction_low_percent.toFixed(0)} to ` +
            `−${item.interior_injury_reduction_high_percent.toFixed(0)}% at 30 km/h.` +
            (item.transit_covered_share != null && item.transit_covered_share < POORLY_SERVED_SHARE
              ? ` Only ${Math.round(item.transit_covered_share * 100)}% of its streets are a short walk from a stop, so add a stop or crossing first.`
              : ''),
        }));
      if (badges.length > 0) {
        result.push(
          new ScatterplotLayer<PlanMarkerDatum>({
            id: 'pilot-badges',
            data: badges,
            pickable: true,
            stroked: true,
            filled: true,
            radiusUnits: 'pixels',
            getRadius: 12,
            getPosition: d => d.position,
            getFillColor: [250, 204, 21, 245],
            getLineColor: [66, 32, 6, 255],
            lineWidthMinPixels: 2,
            onHover: (info: { object?: PlanMarkerDatum }) => {
              setHoveredPlanMarker(info.object ?? null);
            },
          }),
          new TextLayer<PlanMarkerDatum>({
            id: 'pilot-badge-labels',
            data: badges,
            pickable: false,
            getPosition: d => d.position,
            getText: d => d.symbol,
            getSize: 13,
            getColor: [66, 32, 6, 255],
            getTextAnchor: 'middle',
            getAlignmentBaseline: 'center',
            fontFamily: 'Arial, Helvetica, sans-serif',
            fontWeight: 'bold',
          }),
        );
      }
    }

    // Route layer
    if (showRoute && route && route.success && route.segments.length > 0) {
      const routePathData = route.segments.map(segment => ({
        path: segment.coordinates.map(c => [c.lon, c.lat] as [number, number]),
        isArterial: segment.is_arterial,
        roadType: segment.road_type,
      }));

      result.push(
        new PathLayer<(typeof routePathData)[number]>({
          id: 'route-path',
          data: routePathData,
          pickable: false,
          widthScale: 1,
          widthMinPixels: 4,
          widthMaxPixels: 8,
          getPath: (d: typeof routePathData[0]) => d.path,
          getColor: (d: typeof routePathData[0]) =>
            d.isArterial ? ROUTE_ARTERIAL_COLOR : ROUTE_INTERIOR_COLOR,
          getWidth: 5,
        })
      );
    }

    return result;
  }, [analysisBoundary, streetNetwork, superblocks, showSuperblocks, colorBy, impactScenario, getLineColor, getLineWidth, hoveredSuperblock, selectedSuperblock, onSuperblockClick, partition, showPartition, showEntryPoints, selectedEnforcedSuperblock, onEnforcedSuperblockClick, route, showRoute, modifiedStreets, getStreetModification, showDetailedPlanSigns, boundaryRoadWidth, showPilotRanking, poorTransitIds]);

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
    map.addControl(overlay as unknown as IControl);
  }, [layers]);

  const retryBasemap = useCallback(() => {
    setMapError(null);
    setMapReady(false);
    mapRef.current?.getMap().setStyle(MAP_STYLE);
  }, []);

  const hasMapData = Boolean(
    streetNetwork?.features.length ||
    analysisBoundary ||
    superblocks?.length ||
    partition?.superblocks.length ||
    route?.success
  );

  const hasInfo = Boolean(
    (showRoute && route?.success) ||
    (showPartition && partition) ||
    streetNetwork
  );

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
        onIdle={() => setMapReady(true)}
        onError={(event) => {
          setMapError(event.error?.message || 'The basemap could not be loaded.');
        }}
        attributionControl={false}
        style={{ width: '100%', height: '100%' }}
      >
        <NavigationControl position="bottom-right" />
        <ScaleControl position="bottom-left" />
        <AttributionControl compact position="bottom-right" />
      </MapGL>

      {!mapReady && !mapError && (
        <div className="map-status" role="status" aria-live="polite">
          <span className="map-status-spinner" />
          Loading basemap
        </div>
      )}

      {mapError && (
        <div className="map-status map-status-error" role="alert">
          <span>Basemap unavailable</span>
          <button type="button" onClick={retryBasemap}>Retry</button>
        </div>
      )}

      {mapReady && !hasMapData && (
        <div className="map-empty-state" aria-hidden="true">
          <span className="empty-state-icon">⌖</span>
          <strong>{selectedPlaceName ? 'City selected' : 'Choose a city to begin'}</strong>
          <span>
            {selectedPlaceName
              ? emptyStateMessage || 'Choose an analysis action from the controls.'
              : 'Search for a city, then run the complete automated analysis.'}
          </span>
        </div>
      )}

      {/* Plan-sign tooltip */}
      {hoveredPlanMarker && (
        <div className="tooltip plan-marker-tooltip">
          <div className="tooltip-title">{hoveredPlanMarker.title}</div>
          <div className="tooltip-row">
            <span>Street / area:</span>
            <span>{hoveredPlanMarker.name}</span>
          </div>
          <div className="tooltip-detail">{hoveredPlanMarker.detail}</div>
          <div className="tooltip-coordinate">
            {hoveredPlanMarker.position[1].toFixed(5)}° N,{' '}
            {hoveredPlanMarker.position[0].toFixed(5)}° E
          </div>
        </div>
      )}

      {/* Road tooltip */}
      {hoveredFeature && !hoveredSuperblock && !hoveredPlanMarker && (
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
            <span>{hoveredFeature.properties?.is_real_data ? 'Measured volume:' : 'Modeled volume:'}</span>
            <span>{hoveredFeature.properties?.estimated_volume} veh/h</span>
          </div>
          {(() => {
            const road = getRoadImpact(hoveredFeature.properties);
            if (!road) return null;
            const added = road.added_peak_vph[impactScenario] ?? 0;
            return (
              <>
                <div className="tooltip-row">
                  <span>Peak hour today:</span>
                  <span>{Math.round(road.baseline_peak_vph)} veh/h</span>
                </div>
                <div className="tooltip-row">
                  <span>Diverted traffic:</span>
                  <span>+{Math.round(added)} veh/h ({impactPercent(road, impactScenario).toFixed(0)}%)</span>
                </div>
                <div className="tooltip-row">
                  <span>Load after plan:</span>
                  <span>{Math.round(((road.baseline_peak_vph + added) / road.capacity_vph) * 100)}% of capacity</span>
                </div>
              </>
            );
          })()}
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

      {/* Map Legend */}
      {!hideLegends && <div className="map-legend-stack">
        {/* Selected superblock details */}
        {selectedSuperblock && (
          <div className="superblock-details">
            <div className="details-header">
              <span className="details-title">Superblock Analysis</span>
              <button
                type="button"
                className="close-button"
                onClick={() => setSelectedSuperblock(null)}
                aria-label="Close superblock analysis"
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

        {streetNetwork && colorBy === 'hierarchy' && !showPartition && (
          <div className="map-legend hierarchy-legend">
            <button
              type="button"
              className="panel-header"
              onClick={() => setRoadLegendCollapsed(!roadLegendCollapsed)}
              aria-expanded={!roadLegendCollapsed}
            >
              <span className="panel-header-title">Road Types</span>
              <span className={`panel-toggle ${roadLegendCollapsed ? 'collapsed' : ''}`}>▼</span>
            </button>
            <div className={`panel-content ${roadLegendCollapsed ? 'collapsed' : ''}`}>
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
          </div>
        )}

        {streetNetwork && colorBy === 'impact' && partition?.traffic_impact && (
          <div className="map-legend impact-legend">
            <div className="legend-title">Predicted peak-hour increase</div>
            {IMPACT_BANDS.map(band => (
              <div key={band.label} className="legend-item">
                <span className="legend-color" style={{ background: `rgb(${band.color[0]}, ${band.color[1]}, ${band.color[2]})` }} />
                <span className="legend-label">{band.label}</span>
              </div>
            ))}
            <div className="legend-note">Line width grows with diverted vehicles per hour.</div>
          </div>
        )}

        {streetNetwork && colorBy === 'traffic' && (
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

        {showPartition && partition && colorBy !== 'impact' && (
          <div className="map-legend partition-legend">
            <button
              type="button"
              className="panel-header"
              onClick={() => setLegendCollapsed(!legendCollapsed)}
              aria-expanded={!legendCollapsed}
            >
              <span className="panel-header-title">Superblock plan · map signs</span>
              <span className={`panel-toggle ${legendCollapsed ? 'collapsed' : ''}`}>▼</span>
            </button>
            <div className={`panel-content ${legendCollapsed ? 'collapsed' : ''}`}>
              <div className="legend-section-title">Traffic structure</div>
              <div className="legend-item">
                <span className="plan-sign plan-sign-line boundary-road" />
                <span className="legend-label">Cross-traffic boundary road</span>
              </div>
              <div className="legend-item">
                <span className="plan-area-status valid" />
                <span className="legend-label">Model paths blocked + supplied access reachable</span>
              </div>
              <div className="legend-item">
                <span className="plan-area-status review" />
                <span className="legend-label">Model passed; evidence/review incomplete</span>
              </div>
              <div className="legend-item">
                <span className="plan-area-status invalid" />
                <span className="legend-label">Modeled cross-sector path remains</span>
              </div>
              {poorTransitIds.size > 0 && (
                <div className="legend-item">
                  <span className="plan-area-status poor-transit" />
                  <span className="legend-label">
                    Purple outline: under half the streets within {partition.transit_access?.walk_threshold_m.toFixed(0)} m of a stop
                  </span>
                </div>
              )}
              {showEntryPoints && (
                <>
                  <div className="legend-section-title">Entry and return direction</div>
                  <div className="legend-item">
                    <span className="plan-sign entry-point">E</span>
                    <span className="legend-label">Colored dot = entry; E/N/W/S = required return side</span>
                  </div>
                </>
              )}
              {showModalFilters && (
                <>
                  <div className="legend-section-title">Street actions</div>
                  <div className="legend-item">
                    <span className="plan-sign modal-filter">X</span>
                    <span className="legend-label">Modal filter · cars blocked</span>
                  </div>
                  <div className="legend-item">
                    <span className="plan-area-status valid" />
                    <span className="legend-label">Candidate plaza · all legs calmed</span>
                  </div>
                  <div className="legend-item">
                    <span className="plan-sign bus-gate">B</span>
                    <span className="legend-label">Bus gate · buses and emergency pass</span>
                  </div>
                  <div className="legend-item">
                    <span className="plan-sign school-street">S</span>
                    <span className="legend-label">School street · closed at school run</span>
                  </div>
                  <div className="legend-item">
                    <span className="plan-sign school-frontage">S</span>
                    <span className="legend-label">School on a main road · crossing and 30 km/h</span>
                  </div>
                  <div className="legend-item">
                    <span className="plan-sign one-way">&gt;</span>
                    <span className="legend-label">One-way · sign points with traffic</span>
                  </div>
                  <div className="legend-item">
                    <span className="plan-sign two-way">&lt;&gt;</span>
                    <span className="legend-label">Two-way local access</span>
                  </div>
                  <div className="legend-item">
                    <span className="plan-sign turn-restriction">!</span>
                    <span className="legend-label">Turn restriction</span>
                  </div>
                  <div className="legend-item">
                    <span className="plan-sign street-cut">=</span>
                    <span className="legend-label">Street cut · motor traffic closed</span>
                  </div>
                  <div className="legend-hint">Every sign matches the street-by-street action schedule.</div>
                  {!showDetailedPlanSigns && (
                    <div className="legend-hint">Zoom in to street level to show point signs.</div>
                  )}
                </>
              )}
            </div>
          </div>
        )}

        {/* Route legend */}
        {showRoute && route && route.success && (
          <div className="map-legend route-legend">
            <div className="legend-title">Route</div>
            <div className="legend-item">
              <span className="legend-color" style={{ background: 'rgb(59, 130, 246)', height: 4 }} />
              <span className="legend-label">Arterial roads</span>
            </div>
            <div className="legend-item">
              <span className="legend-color" style={{ background: 'rgb(34, 197, 94)', height: 4 }} />
              <span className="legend-label">Interior roads</span>
            </div>
          </div>
        )}
      </div>}

      {/* Stacked info panels on bottom right */}
      {hasInfo && !selectedEnforcedSuperblock && !hideLegends && <div className="map-info-stack">
        <div className="info-panel">
          <button
            type="button"
            className="info-panel-header"
            onClick={() => setInfoCollapsed(!infoCollapsed)}
            aria-expanded={!infoCollapsed}
          >
            <span className="info-panel-title">Info</span>
            <span className={`panel-toggle ${infoCollapsed ? 'collapsed' : ''}`}>▼</span>
          </button>
          <div className={`info-panel-content ${infoCollapsed ? 'collapsed' : ''}`}>
            {/* Route info */}
            {showRoute && route && route.success && (
              <div className="info-section">
                <div className="info-section-title">Route</div>
                <div className="info-row">
                  <span>Distance:</span>
                  <span>{route.total_distance_km.toFixed(2)} km</span>
                </div>
                <div className="info-row">
                  <span>Time:</span>
                  <span>{route.estimated_time_min.toFixed(0)} min</span>
                </div>
                <div className="info-row">
                  <span>Arterial:</span>
                  <span>{route.arterial_percent.toFixed(0)}%</span>
                </div>
              </div>
            )}

            {/* Partition stats */}
            {showPartition && partition && (
              <div className="info-section">
                <div className="info-section-title">City plan</div>
                <div className="info-row">
                  <span>Superblocks:</span>
                  <span>{partition.total_superblocks}</span>
                </div>
                <div className="info-row">
                  <span>Eligible roads accounted:</span>
                  <span>{partition.eligible_road_coverage_percent.toFixed(1)}%</span>
                </div>
                <div className={`info-row ${partition.readiness.approved_for_pilot ? '' : 'warning'}`}>
                  <span>Assurance stage:</span>
                  <span>{partition.readiness.status.replaceAll('_', ' ')}</span>
                </div>
                <div className="info-row">
                  <span>Boundary roads:</span>
                  <span>{partition.arterial_network.length}</span>
                </div>
                <div className="info-row">
                  <span>Access changes:</span>
                  <span>
                    {partition.total_modal_filters
                      + partition.total_one_way_conversions
                      + partition.total_two_way_conversions
                      + partition.total_street_cuts}
                  </span>
                </div>
                <div className="info-row">
                  <span>Street cuts:</span>
                  <span>{partition.total_street_cuts}</span>
                </div>
                {partition.total_unreachable_access_targets > 0 && (
                  <div className="info-row warning">
                    <span>Access targets blocked:</span>
                    <span>{partition.total_unreachable_access_targets}</span>
                  </div>
                )}
              </div>
            )}

            {/* Network stats */}
            {streetNetwork && (
              <div className="info-section">
                <div className="info-section-title">Network</div>
                <div className="info-row">
                  <span>Roads:</span>
                  <span>{streetNetwork.metadata.total_edges}</span>
                </div>
                <div className="info-row">
                  <span>Length:</span>
                  <span>{streetNetwork.metadata.total_length_km} km</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>}

    </div>
  );
}
