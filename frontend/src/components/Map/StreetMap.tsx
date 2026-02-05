import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import MapGL, { NavigationControl, ScaleControl } from 'react-map-gl/maplibre';
import type { MapRef } from 'react-map-gl/maplibre';
import { GeoJsonLayer, PolygonLayer, ScatterplotLayer, PathLayer, TextLayer } from '@deck.gl/layers';
import { MapboxOverlay } from '@deck.gl/mapbox';
import type { Feature, LineString } from 'geojson';
import type { ViewState, StreetNetworkResponse, RoadProperties, EnforcedSuperblock, CityPartition, RouteResult } from '../../types';
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

// Direction calculation helper - gets direction label based on angle from centroid
function getDirectionFromAngle(angleDeg: number): { label: string; arrow: string } {
  // Normalize angle to 0-360
  const normalizedAngle = ((angleDeg % 360) + 360) % 360;

  if (normalizedAngle >= 337.5 || normalizedAngle < 22.5) return { label: 'East', arrow: 'E' };
  if (normalizedAngle >= 22.5 && normalizedAngle < 67.5) return { label: 'NE', arrow: 'NE' };
  if (normalizedAngle >= 67.5 && normalizedAngle < 112.5) return { label: 'North', arrow: 'N' };
  if (normalizedAngle >= 112.5 && normalizedAngle < 157.5) return { label: 'NW', arrow: 'NW' };
  if (normalizedAngle >= 157.5 && normalizedAngle < 202.5) return { label: 'West', arrow: 'W' };
  if (normalizedAngle >= 202.5 && normalizedAngle < 247.5) return { label: 'SW', arrow: 'SW' };
  if (normalizedAngle >= 247.5 && normalizedAngle < 292.5) return { label: 'South', arrow: 'S' };
  return { label: 'SE', arrow: 'SE' };
}

// Calculate centroid of a polygon
function calculateCentroid(coordinates: number[][][]): [number, number] {
  const ring = coordinates[0]; // Outer ring
  if (!ring || ring.length === 0) return [0, 0];

  let sumLon = 0, sumLat = 0;
  for (const coord of ring) {
    sumLon += coord[0];
    sumLat += coord[1];
  }
  return [sumLon / ring.length, sumLat / ring.length];
}

// Superblock colors for visual distinction (pastel palette)
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

// Route colors
const ROUTE_ARTERIAL_COLOR: [number, number, number, number] = [59, 130, 246, 255]; // Blue
const ROUTE_INTERIOR_COLOR: [number, number, number, number] = [34, 197, 94, 255]; // Green

interface StreetMapProps {
  streetNetwork: StreetNetworkResponse | null;
  superblocks?: SuperblockCandidate[];
  showSuperblocks?: boolean;
  initialViewState?: ViewState;
  onViewStateChange?: (viewState: ViewState) => void;
  colorBy?: 'hierarchy' | 'traffic' | 'interventions';
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
}

export function StreetMap({
  streetNetwork,
  superblocks,
  showSuperblocks = true,
  initialViewState,
  onViewStateChange,
  colorBy = 'hierarchy',
  onSuperblockClick,
  partition,
  showPartition = false,
  showEntryPoints = true,
  showModalFilters = true,
  selectedEnforcedSuperblock,
  onEnforcedSuperblockClick,
  route,
  showRoute = true,
}: StreetMapProps) {
  const mapRef = useRef<MapRef>(null);

  // Panel collapse states
  const [legendCollapsed, setLegendCollapsed] = useState(false);
  const [infoCollapsed, setInfoCollapsed] = useState(false);

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

  // Build a map of modified streets from partition data for quick lookup
  const modifiedStreets = useMemo(() => {
    if (!showPartition || !partition) return new Map<number, { type: string; direction: string | null }>();

    const map = new Map<number, { type: string; direction: string | null }>();
    partition.superblocks.forEach(sb => {
      sb.modifications.forEach(mod => {
        map.set(mod.osm_id, {
          type: mod.modification_type,
          direction: mod.direction
        });
      });
    });
    return map;
  }, [showPartition, partition]);

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
        let osmidToCheck = props.osmid;
        if (Array.isArray(osmidToCheck)) {
          osmidToCheck = osmidToCheck[0];
        }
        const mod = modifiedStreets.get(osmidToCheck);
        if (mod) {
          if (mod.type === 'modal_filter') {
            return [239, 68, 68, 255]; // Red for modal filter
          }
          if (mod.type === 'one_way') {
            return [59, 130, 246, 255]; // Blue for one-way
          }
          if (mod.type === 'full_closure') {
            return [124, 58, 237, 255]; // Purple for closure
          }
          return [251, 191, 36, 255]; // Yellow for other modifications
        }
      }

      const hierarchy = props.hierarchy ?? 99;
      return ROAD_COLORS[hierarchy] ?? DEFAULT_ROAD_COLOR;
    },
    [colorBy, selectedSuperblock, showPartition, modifiedStreets]
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
            getLineColor: [colorBy, selectedSuperblock?.id, showPartition, partition?.total_superblocks],
          },
        })
      );

      // Add direction arrows on modified streets (one-way)
      if (showPartition && partition && modifiedStreets.size > 0) {
        // Helper to get osmid as number
        const getOsmId = (osmid: number | number[] | undefined): number | undefined => {
          if (osmid === undefined) return undefined;
          return Array.isArray(osmid) ? osmid[0] : osmid;
        };

        // Find streets that have been modified and get their geometry
        const modifiedStreetFeatures = streetNetwork.features.filter(f => {
          const osmid = getOsmId(f.properties?.osmid);
          return osmid !== undefined && modifiedStreets.get(osmid)?.type === 'one_way';
        });

        // Create arrow data at midpoints of modified streets
        const arrowData = modifiedStreetFeatures.map(f => {
          const coords = f.geometry.coordinates;
          const midIndex = Math.floor(coords.length / 2);
          const midPoint = coords[midIndex] || coords[0];

          // Calculate direction from geometry
          let angle = 0;
          if (coords.length >= 2) {
            const start = coords[Math.max(0, midIndex - 1)];
            const end = coords[Math.min(coords.length - 1, midIndex + 1)];
            angle = Math.atan2(end[1] - start[1], end[0] - start[0]) * (180 / Math.PI);
          }

          const osmid = getOsmId(f.properties?.osmid);
          const mod = osmid !== undefined ? modifiedStreets.get(osmid) : undefined;
          // Adjust angle based on direction field if available
          if (mod?.direction) {
            const dir = mod.direction.toLowerCase();
            if (dir.includes('north')) angle = 90;
            else if (dir.includes('south')) angle = -90;
            else if (dir.includes('east')) angle = 0;
            else if (dir.includes('west')) angle = 180;
          }

          return {
            position: midPoint,
            angle,
            name: f.properties?.name || 'One-way street',
          };
        });

        if (arrowData.length > 0) {
          // Direction arrows on one-way streets (using > rotated)
          result.push(
            new TextLayer({
              id: 'one-way-street-arrows',
              data: arrowData,
              pickable: false,
              getPosition: (d: typeof arrowData[0]) => d.position as [number, number],
              getText: () => '>',
              getSize: 20,
              getAngle: (d: typeof arrowData[0]) => -d.angle,
              getColor: [30, 64, 175, 255], // Darker blue for visibility
              getTextAnchor: 'middle',
              getAlignmentBaseline: 'center',
              fontFamily: 'Arial, Helvetica, sans-serif',
              fontWeight: 'bold',
              billboard: false,
              sizeMinPixels: 14,
              sizeMaxPixels: 28,
            } as any)
          );
        }
      }
    }

    // Partitioned superblocks layer (new system)
    if (showPartition && partition && partition.superblocks.length > 0) {
      result.push(
        new PolygonLayer({
          id: 'enforced-superblocks',
          data: partition.superblocks,
          pickable: true,
          stroked: true,
          filled: true,
          getPolygon: (d: EnforcedSuperblock) => d.geometry.coordinates,
          getFillColor: (d: EnforcedSuperblock) => {
            if (selectedEnforcedSuperblock?.id === d.id) {
              return [59, 130, 246, 180]; // blue when selected
            }
            // Color based on constraint validation
            if (d.constraint_validated && d.all_addresses_reachable) {
              return [34, 197, 94, 140]; // green - valid
            } else if (d.constraint_validated) {
              return [251, 191, 36, 140]; // yellow - constraint ok but some unreachable
            }
            return [239, 68, 68, 140]; // red - constraint not satisfied
          },
          getLineColor: (d: EnforcedSuperblock) => {
            if (selectedEnforcedSuperblock?.id === d.id) {
              return [37, 99, 235, 255]; // darker blue
            }
            return [0, 0, 0, 100];
          },
          getLineWidth: (d: EnforcedSuperblock) =>
            selectedEnforcedSuperblock?.id === d.id ? 3 : 1,
          onClick: (info: { object?: EnforcedSuperblock }) => {
            if (info.object) {
              onEnforcedSuperblockClick?.(info.object);
            }
          },
          updateTriggers: {
            getFillColor: [selectedEnforcedSuperblock?.id],
            getLineColor: [selectedEnforcedSuperblock?.id],
            getLineWidth: [selectedEnforcedSuperblock?.id],
          },
        } as any)
      );
    }

    // Entry points layer with direction arrows - color by superblock, direction by position
    if (showPartition && showEntryPoints && partition) {
      // Build entry point data with actual direction calculated from centroid
      const entryPointData = partition.superblocks.flatMap((sb, sbIndex) => {
        const centroid = calculateCentroid(sb.geometry.coordinates as number[][][]);

        return sb.entry_points.map(ep => {
          // Calculate angle from centroid to entry point
          const dx = ep.coordinates.lon - centroid[0];
          const dy = ep.coordinates.lat - centroid[1];
          const angleDeg = Math.atan2(dy, dx) * (180 / Math.PI);
          const direction = getDirectionFromAngle(angleDeg);

          return {
            ...ep,
            superblockId: sb.id,
            superblockIndex: sbIndex,
            centroid,
            direction: direction.label,
            arrow: direction.arrow,
          };
        });
      });

      if (entryPointData.length > 0) {
        // Lines connecting entry points to superblock centroid (for visual association)
        const connectionLines = entryPointData.map(ep => ({
          path: [
            [ep.coordinates.lon, ep.coordinates.lat],
            ep.centroid,
          ],
          superblockIndex: ep.superblockIndex,
        }));

        result.push(
          new PathLayer({
            id: 'entry-point-connections',
            data: connectionLines,
            pickable: false,
            widthScale: 1,
            widthMinPixels: 1,
            widthMaxPixels: 2,
            getPath: (d: typeof connectionLines[0]) => d.path,
            getColor: (d: typeof connectionLines[0]) => {
              const color = SUPERBLOCK_COLORS[d.superblockIndex % SUPERBLOCK_COLORS.length];
              return [color[0], color[1], color[2], 100] as [number, number, number, number];
            },
            getWidth: 1,
          } as any)
        );

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
            radiusMinPixels: 8,
            radiusMaxPixels: 16,
            getPosition: (d: typeof entryPointData[0]) => [d.coordinates.lon, d.coordinates.lat],
            getFillColor: (d: typeof entryPointData[0]) =>
              SUPERBLOCK_COLORS[d.superblockIndex % SUPERBLOCK_COLORS.length],
            getLineColor: [255, 255, 255, 255],
            getRadius: 10,
            lineWidthMinPixels: 2,
          } as any)
        );

        // Direction labels on entry points
        result.push(
          new TextLayer({
            id: 'entry-point-arrows',
            data: entryPointData,
            pickable: false,
            getPosition: (d: typeof entryPointData[0]) => [d.coordinates.lon, d.coordinates.lat],
            getText: (d: typeof entryPointData[0]) => d.arrow,
            getSize: 11,
            getColor: [255, 255, 255, 255],
            getTextAnchor: 'middle',
            getAlignmentBaseline: 'center',
            fontFamily: 'Arial, Helvetica, sans-serif',
            fontWeight: 'bold',
            sizeMinPixels: 9,
            sizeMaxPixels: 13,
          } as any)
        );
      }
    }

    // Modal filters layer with barrier visualization
    if (showPartition && showModalFilters && partition) {
      const modalFilterData = partition.superblocks.flatMap(sb =>
        sb.modifications
          .filter(m => m.modification_type === 'modal_filter' && m.filter_location)
          .map(m => ({
            ...m,
            superblockId: sb.id,
          }))
      );

      // One-way conversion data
      const oneWayData = partition.superblocks.flatMap(sb =>
        sb.modifications
          .filter(m => m.modification_type === 'one_way' && m.filter_location)
          .map(m => ({
            ...m,
            superblockId: sb.id,
          }))
      );

      if (modalFilterData.length > 0) {
        // Modal filter circles (red/orange for blocking)
        result.push(
          new ScatterplotLayer({
            id: 'modal-filters',
            data: modalFilterData,
            pickable: true,
            opacity: 0.95,
            stroked: true,
            filled: true,
            radiusScale: 1,
            radiusMinPixels: 8,
            radiusMaxPixels: 16,
            getPosition: (d: typeof modalFilterData[0]) =>
              d.filter_location ? [d.filter_location.lon, d.filter_location.lat] : [0, 0],
            getFillColor: [239, 68, 68, 255], // Red for barrier
            getLineColor: [255, 255, 255, 255],
            getRadius: 10,
            lineWidthMinPixels: 2,
          } as any)
        );

        // X symbol on modal filters
        result.push(
          new TextLayer({
            id: 'modal-filter-icons',
            data: modalFilterData,
            pickable: false,
            getPosition: (d: typeof modalFilterData[0]) =>
              d.filter_location ? [d.filter_location.lon, d.filter_location.lat] : [0, 0],
            getText: () => 'X',
            getSize: 14,
            getColor: [255, 255, 255, 255],
            getTextAnchor: 'middle',
            getAlignmentBaseline: 'center',
            fontFamily: 'Arial, Helvetica, sans-serif',
            fontWeight: 'bold',
            sizeMinPixels: 10,
            sizeMaxPixels: 16,
          } as any)
        );
      }

      // One-way conversion markers
      if (oneWayData.length > 0) {
        result.push(
          new ScatterplotLayer({
            id: 'one-way-markers',
            data: oneWayData,
            pickable: true,
            opacity: 0.95,
            stroked: true,
            filled: true,
            radiusScale: 1,
            radiusMinPixels: 7,
            radiusMaxPixels: 14,
            getPosition: (d: typeof oneWayData[0]) =>
              d.filter_location ? [d.filter_location.lon, d.filter_location.lat] : [0, 0],
            getFillColor: [59, 130, 246, 255], // Blue for one-way
            getLineColor: [255, 255, 255, 255],
            getRadius: 9,
            lineWidthMinPixels: 2,
          } as any)
        );

        // Arrow for one-way direction (using simple characters)
        result.push(
          new TextLayer({
            id: 'one-way-arrows',
            data: oneWayData,
            pickable: false,
            getPosition: (d: typeof oneWayData[0]) =>
              d.filter_location ? [d.filter_location.lon, d.filter_location.lat] : [0, 0],
            getText: (d: typeof oneWayData[0]) => {
              const dir = d.direction?.toLowerCase() || '';
              if (dir.includes('north')) return '^';
              if (dir.includes('south')) return 'v';
              if (dir.includes('east')) return '>';
              if (dir.includes('west')) return '<';
              return '>'; // Default
            },
            getSize: 16,
            getColor: [255, 255, 255, 255],
            getTextAnchor: 'middle',
            getAlignmentBaseline: 'center',
            fontFamily: 'Arial, Helvetica, sans-serif',
            fontWeight: 'bold',
            sizeMinPixels: 12,
            sizeMaxPixels: 18,
          } as any)
        );
      }
    }

    // Route layer
    if (showRoute && route && route.success && route.segments.length > 0) {
      const routePathData = route.segments.map(segment => ({
        path: segment.coordinates.map(c => [c.lon, c.lat]),
        isArterial: segment.is_arterial,
        roadType: segment.road_type,
      }));

      result.push(
        new PathLayer({
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
        } as any)
      );
    }

    return result;
  }, [streetNetwork, superblocks, showSuperblocks, colorBy, getLineColor, getLineWidth, hoveredSuperblock, selectedSuperblock, onSuperblockClick, partition, showPartition, showEntryPoints, showModalFilters, selectedEnforcedSuperblock, onEnforcedSuperblockClick, route, showRoute]);

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

      {/* Map Legend */}
      <div className="map-legend-stack">
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

        {showPartition && partition && (
          <div className="map-legend partition-legend">
            <div
              className="panel-header"
              onClick={() => setLegendCollapsed(!legendCollapsed)}
            >
              <span className="panel-header-title">Legend</span>
              <span className={`panel-toggle ${legendCollapsed ? 'collapsed' : ''}`}>▼</span>
            </div>
            <div className={`panel-content ${legendCollapsed ? 'collapsed' : ''}`}>
              <div className="legend-section-title">Superblock Status</div>
              <div className="legend-item">
                <span className="legend-color" style={{ background: 'rgba(34, 197, 94, 0.6)', width: 16, height: 16, borderRadius: 4 }} />
                <span className="legend-label">Valid</span>
              </div>
              <div className="legend-item">
                <span className="legend-color" style={{ background: 'rgba(251, 191, 36, 0.6)', width: 16, height: 16, borderRadius: 4 }} />
                <span className="legend-label">Some unreachable</span>
              </div>
              <div className="legend-item">
                <span className="legend-color" style={{ background: 'rgba(239, 68, 68, 0.6)', width: 16, height: 16, borderRadius: 4 }} />
                <span className="legend-label">Invalid</span>
              </div>
              {showEntryPoints && (
                <>
                  <div className="legend-section-title">Entry Points</div>
                  <div className="legend-item">
                    <span className="legend-marker entry-point" style={{ background: 'rgb(99, 102, 241)' }}>N</span>
                    <span className="legend-label">Direction label</span>
                  </div>
                  <div className="legend-hint">Color = superblock, Letter = direction</div>
                  <div className="legend-hint">Line connects to superblock center</div>
                </>
              )}
              {showModalFilters && (
                <>
                  <div className="legend-section-title">Modifications</div>
                  <div className="legend-item">
                    <span className="legend-marker" style={{ background: 'rgb(239, 68, 68)' }}>X</span>
                    <span className="legend-label">Modal filter</span>
                  </div>
                  <div className="legend-item">
                    <span className="legend-marker" style={{ background: 'rgb(59, 130, 246)' }}>&gt;</span>
                    <span className="legend-label">One-way street</span>
                  </div>
                  <div className="legend-item">
                    <span className="legend-color" style={{ background: 'rgb(239, 68, 68)', height: 4 }} />
                    <span className="legend-label">Filtered road</span>
                  </div>
                  <div className="legend-item">
                    <span className="legend-color" style={{ background: 'rgb(59, 130, 246)', height: 4 }} />
                    <span className="legend-label">One-way road</span>
                  </div>
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
      </div>

      {/* Stacked info panels on bottom right */}
      <div className="map-info-stack">
        <div className="info-panel">
          <div
            className="info-panel-header"
            onClick={() => setInfoCollapsed(!infoCollapsed)}
          >
            <span className="info-panel-title">Info</span>
            <span className={`panel-toggle ${infoCollapsed ? 'collapsed' : ''}`}>▼</span>
          </div>
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
                <div className="info-section-title">Partition</div>
                <div className="info-row">
                  <span>Superblocks:</span>
                  <span>{partition.total_superblocks}</span>
                </div>
                <div className="info-row">
                  <span>Coverage:</span>
                  <span>{partition.coverage_percent.toFixed(1)}%</span>
                </div>
                <div className="info-row">
                  <span>Modifications:</span>
                  <span>{partition.total_modal_filters + partition.total_one_way_conversions}</span>
                </div>
                {partition.total_unreachable_addresses > 0 && (
                  <div className="info-row warning">
                    <span>Unreachable:</span>
                    <span>{partition.total_unreachable_addresses}</span>
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
      </div>

    </div>
  );
}
