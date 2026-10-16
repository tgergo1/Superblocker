import type { BoundaryRoadImpact } from '../types';

// Predicted peak-hour increase on boundary roads, from calm to overloaded.
export const IMPACT_BANDS: { max: number; color: [number, number, number, number]; label: string }[] = [
  { max: 2, color: [34, 197, 94, 235], label: 'Under 2%' },
  { max: 10, color: [234, 179, 8, 240], label: '2 to 10%' },
  { max: 25, color: [249, 115, 22, 245], label: '10 to 25%' },
  { max: 50, color: [220, 38, 38, 250], label: '25 to 50%' },
  { max: Infinity, color: [127, 29, 29, 255], label: 'Over 50%' },
];

export function impactPercent(road: BoundaryRoadImpact, scenario: string): number {
  const added = road.added_peak_vph[scenario] ?? 0;
  return road.baseline_peak_vph > 0 ? (added / road.baseline_peak_vph) * 100 : 0;
}

export function impactColor(percent: number): [number, number, number, number] {
  return (IMPACT_BANDS.find(band => percent < band.max) ?? IMPACT_BANDS[IMPACT_BANDS.length - 1]).color;
}
