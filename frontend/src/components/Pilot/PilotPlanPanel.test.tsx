import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { PilotCandidate, PilotPlanSummary } from '../../types';
import { PilotPlanPanel } from './PilotPlanPanel';

function candidate(rank: number, overrides: Partial<PilotCandidate> = {}): PilotCandidate {
  return {
    superblock_id: `sb_${rank}`,
    rank,
    score: 70 - rank,
    trial_measures: 4,
    trial_cost_eur: 9_000,
    build_cost_eur: 60_000,
    displaced_peak_vph: 320,
    interior_injury_reduction_low_percent: 41.2,
    interior_injury_reduction_high_percent: 55.8,
    school_streets: 0,
    reclaimable_m2: 5_400,
    mean_detour_seconds: 40,
    emergency_delay_seconds: 12,
    centroid_lat: 47.52,
    centroid_lon: 19.17,
    ...overrides,
  };
}

const plan: PilotPlanSummary = {
  total_trial_cost_eur: 7_583_000,
  total_build_cost_eur: 24_833_000,
  top_trial_cost_eur: 381_500,
  mean_injury_reduction_low_percent: 25.4,
  mean_injury_reduction_high_percent: 43.8,
  candidates: [candidate(1, { school_streets: 2 }), candidate(2)],
  limitations: ['Costs are indicative central European unit rates, not quotes.'],
};

describe('PilotPlanPanel', () => {
  it('summarises costs and lets the user jump to a ranked superblock', () => {
    const onSelect = vi.fn();
    render(
      <PilotPlanPanel plan={plan} showOnMap onShowOnMapChange={() => {}} onSelect={onSelect} />,
    );

    expect(screen.getByText('Where to start')).toBeTruthy();
    expect(screen.getByText(/top 2 superblocks for €18k/)).toBeTruthy();
    expect(screen.getByText('€7.6M')).toBeTruthy();
    expect(screen.getByText('−25 to −44%')).toBeTruthy();
    expect(screen.getByText(/2 school streets/)).toBeTruthy();

    fireEvent.click(screen.getAllByRole('button', { name: /veh\/h out/ })[0]);
    expect(onSelect).toHaveBeenCalledWith(plan.candidates[0]);
  });
});
