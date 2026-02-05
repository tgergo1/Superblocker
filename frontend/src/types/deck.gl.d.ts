declare module '@deck.gl/react' {
  import { Component, ReactNode } from 'react';

  export interface DeckGLProps {
    viewState?: {
      longitude: number;
      latitude: number;
      zoom: number;
      pitch?: number;
      bearing?: number;
    };
    initialViewState?: {
      longitude: number;
      latitude: number;
      zoom: number;
      pitch?: number;
      bearing?: number;
    };
    controller?: boolean | object;
    layers?: unknown[];
    onViewStateChange?: (params: { viewState: any }) => void;
    children?: ReactNode;
    width?: string | number;
    height?: string | number;
    style?: React.CSSProperties;
    getCursor?: () => string;
  }

  export default class DeckGL extends Component<DeckGLProps> {}
}

declare module '@deck.gl/core' {
  export interface MapViewState {
    longitude: number;
    latitude: number;
    zoom: number;
    pitch?: number;
    bearing?: number;
  }

  export interface DeckProps {
    layers?: unknown[];
  }
}

declare module '@deck.gl/mapbox' {
  export class MapboxOverlay {
    constructor(props: { layers?: unknown[]; interleaved?: boolean });
    setProps(props: { layers?: unknown[] }): void;
    onAdd(map: unknown): HTMLElement;
    onRemove(): void;
  }
}

declare module '@deck.gl/layers' {
  export class GeoJsonLayer<D = any> {
    constructor(props: {
      id: string;
      data: any;
      pickable?: boolean;
      stroked?: boolean;
      filled?: boolean;
      extruded?: boolean;
      lineWidthUnits?: 'pixels' | 'meters';
      lineWidthScale?: number;
      lineWidthMinPixels?: number;
      lineWidthMaxPixels?: number;
      getLineColor?: ((d: D) => [number, number, number, number]) | [number, number, number, number];
      getFillColor?: ((d: D) => [number, number, number, number]) | [number, number, number, number];
      getLineWidth?: ((d: D) => number) | number;
      getElevation?: ((d: D) => number) | number;
      onHover?: (info: { object?: D; x?: number; y?: number }) => void;
      onClick?: (info: { object?: D; x?: number; y?: number }) => void;
      updateTriggers?: Record<string, unknown[]>;
    });
  }

  export class PathLayer<D = any> {
    constructor(props: {
      id: string;
      data: D[];
      pickable?: boolean;
      widthUnits?: 'pixels' | 'meters';
      widthScale?: number;
      widthMinPixels?: number;
      getPath?: (d: D) => number[][];
      getColor?: ((d: D) => [number, number, number, number]) | [number, number, number, number];
      getWidth?: ((d: D) => number) | number;
      updateTriggers?: Record<string, unknown[]>;
    });
  }

  export class PolygonLayer<D = any> {
    constructor(props: {
      id: string;
      data: D[];
      pickable?: boolean;
      stroked?: boolean;
      filled?: boolean;
      extruded?: boolean;
      getPolygon?: (d: D) => number[][] | number[][][];
      getLineColor?: ((d: D) => [number, number, number, number]) | [number, number, number, number];
      getFillColor?: ((d: D) => [number, number, number, number]) | [number, number, number, number];
      getLineWidth?: ((d: D) => number) | number;
      updateTriggers?: Record<string, unknown[]>;
    });
  }

  export class ScatterplotLayer<D = any> {
    constructor(props: {
      id: string;
      data: D[];
      pickable?: boolean;
      radiusUnits?: 'pixels' | 'meters';
      radiusScale?: number;
      radiusMinPixels?: number;
      getPosition?: (d: D) => [number, number];
      getRadius?: ((d: D) => number) | number;
      getFillColor?: ((d: D) => [number, number, number, number]) | [number, number, number, number];
      updateTriggers?: Record<string, unknown[]>;
    });
  }
}
