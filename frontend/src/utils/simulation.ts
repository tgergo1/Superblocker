import type { SimulatedLink, SimulationMapView } from '../types/simulation';

type Rgba = [number, number, number, number];

export const LOAD_BANDS: { label: string; max: number; color: Rgba }[] = [
  { label: 'Under half full', max: 0.5, color: [34, 197, 94, 220] },
  { label: '50-80% full', max: 0.8, color: [234, 179, 8, 230] },
  { label: '80-100% full', max: 1.0, color: [249, 115, 22, 240] },
  { label: 'Over capacity', max: Infinity, color: [220, 38, 38, 250] },
];

export const CHANGE_BANDS: { label: string; color: Rgba }[] = [
  { label: 'Much more traffic (+200 veh/h or more)', color: [185, 28, 28, 250] },
  { label: 'More traffic', color: [248, 113, 113, 235] },
  { label: 'Little change', color: [148, 163, 184, 150] },
  { label: 'Less traffic', color: [96, 165, 250, 235] },
  { label: 'Closed to through traffic', color: [22, 163, 74, 240] },
];

/** Volume after the change: with the plan, or with the plan and the entry charge. */
function afterVolume(link: SimulatedLink, view: SimulationMapView): number {
  return view === 'fee' ? (link.planned_charged_vph ?? link.planned_vph) : link.planned_vph;
}

export function loadColor(ratio: number): Rgba {
  return (LOAD_BANDS.find(band => ratio < band.max) ?? LOAD_BANDS[LOAD_BANDS.length - 1]).color;
}

export function simulationLinkColor(link: SimulatedLink, view: SimulationMapView): Rgba {
  if (view === 'without') return loadColor(link.baseline_vc);
  if (view === 'with') return loadColor(link.planned_vc);
  if (link.closed_in_plan) return CHANGE_BANDS[4].color;
  const delta = afterVolume(link, view) - link.baseline_vph;
  const relative = delta / Math.max(link.baseline_vph, 50);
  if (delta >= 200) return CHANGE_BANDS[0].color;
  if (delta > 25 && relative > 0.1) return CHANGE_BANDS[1].color;
  if (delta < -25 && relative < -0.1) return CHANGE_BANDS[3].color;
  return CHANGE_BANDS[2].color;
}

export function simulationLinkWidth(link: SimulatedLink, view: SimulationMapView): number {
  const volume =
    view === 'without'
      ? link.baseline_vph
      : view === 'with'
        ? link.planned_vph
        : Math.max(Math.abs(afterVolume(link, view) - link.baseline_vph), link.closed_in_plan ? 150 : 0);
  return Math.min(9, 1.5 + Math.sqrt(volume) / 9);
}

export function formatPercentChange(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const rounded = Math.round(value);
  return `${rounded > 0 ? '+' : ''}${rounded}%`;
}
