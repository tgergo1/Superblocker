import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { SearchResult } from '../../types';
import { SearchBox } from './SearchBox';

const place: SearchResult = {
  place_id: 1,
  osm_type: 'relation',
  osm_id: 2,
  display_name: 'Budapest, Hungary',
  lat: 47.5,
  lon: 19.04,
  boundingbox: { north: 47.6, south: 47.4, east: 19.2, west: 18.9 },
  boundary: null,
  boundary_source: 'bounding_box_fallback',
  type: 'city',
  importance: 0.8,
};

describe('SearchBox', () => {
  it('submits only after an explicit search action', async () => {
    const user = userEvent.setup();
    const onSearch = vi.fn();
    render(
      <SearchBox
        onSearch={onSearch}
        onSelect={vi.fn()}
        results={[]}
        isLoading={false}
        selectedPlace={null}
        onClear={vi.fn()}
      />,
    );

    await user.type(screen.getByRole('textbox', { name: /search for a city/i }), 'Budapest');
    expect(onSearch).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Search' }));
    expect(onSearch).toHaveBeenCalledOnce();
    expect(onSearch).toHaveBeenCalledWith('Budapest');
  });

  it('supports keyboard result selection and distinguishes errors', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const { rerender } = render(
      <SearchBox
        onSearch={vi.fn()}
        onSelect={onSelect}
        results={[place]}
        isLoading={false}
        selectedPlace={null}
        onClear={vi.fn()}
      />,
    );
    const input = screen.getByRole('textbox', { name: /search for a city/i });
    await user.type(input, 'Budapest');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith(place);

    rerender(
      <SearchBox
        onSearch={vi.fn()}
        onSelect={onSelect}
        results={[]}
        isLoading={false}
        error={new Error('upstream')}
        selectedPlace={null}
        onClear={vi.fn()}
      />,
    );
    await user.type(screen.getByRole('textbox'), 'Budapest');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    expect(screen.getByRole('alert').textContent).toContain('Place search failed');
  });
});
